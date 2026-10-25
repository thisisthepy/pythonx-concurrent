"""Structured concurrency scopes, CoroutineScope, Job and Deferred.

Covers SPEC S2.1, S2.2, S2.3, S2.5, S2.7, S2.8, S2.9, S3.5, S4.5, S5.4, S5.7, S8.1, S8.4.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import sys
import threading
from collections.abc import Callable, Coroutine, Generator
from types import SimpleNamespace, TracebackType
from typing import Generic, TypeVar, cast

from pythonx.concurrent._dispatchers import (
    CarrierDispatcher,
    Dispatcher,
    Dispatchers,
    IODispatcher,
    KotlinDispatcherAdapter,
    LimitedDispatcher,
    LoopDispatcher,
    MainDispatcher,
    UnconfinedDispatcher,
    _PendingItem,
)

T = TypeVar("T")

_PRIVATE_JOB_TOKEN: object = object()


class Job:
    """Handle to a coroutine launched inside a `Scope` or `CoroutineScope` (SPEC S2.2, S4.5, S5.4).

    Cannot be instantiated directly by user code; only `scope.launch` and `scope.defer` create jobs.
    """

    def __init__(self, _token: object = None, *, name: str | None = None) -> None:
        if _token is not _PRIVATE_JOB_TOKEN:
            raise TypeError("Job cannot be instantiated outside a Scope or CoroutineScope (SPEC S2.2, S2.7)")
        self._name: str | None = name
        self._lock: threading.Lock = threading.Lock()
        self._done: bool = False
        self._cancelled: bool = False
        self._cancel_requested: bool = False
        self._cancel_msg: str | None = None
        self._result: object = None
        self._exception: BaseException | None = None
        self._task: asyncio.Task[object] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_item: _PendingItem | None = None
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []
        self._done_callbacks: list[Callable[[Job], None]] = []

    @property
    def name(self) -> str | None:
        return self._name

    def _bind_pending(self, item: _PendingItem) -> None:
        with self._lock:
            self._pending_item = item
            if self._cancel_requested:
                item.cancelled_before_start = True
                item.cancel_msg = self._cancel_msg

    def _bind_task(self, task: asyncio.Task[object], loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._task = task
            self._loop = loop
            if self._cancel_requested and not task.done():
                loop.call_soon_threadsafe(task.cancel, self._cancel_msg)

    def _complete(
        self,
        *,
        cancelled: bool,
        result: object = None,
        exception: BaseException | None = None,
    ) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            self._cancelled = cancelled
            self._result = result
            self._exception = exception
            waiters = list(self._waiters)
            self._waiters.clear()
            callbacks = list(self._done_callbacks)
            self._done_callbacks.clear()

        for waiter_loop, fut in waiters:
            waiter_loop.call_soon_threadsafe(_resolve_waiter_future, fut)
        for cb in callbacks:
            cb(self)

    def _add_done_callback(self, callback: Callable[[Job], None]) -> None:
        with self._lock:
            if self._done:
                already_done = True
            else:
                self._done_callbacks.append(callback)
                already_done = False
        if already_done:
            callback(self)

    def cancel(self, msg: str | None = None) -> bool:
        """Request cancellation of this job (idempotent)."""
        with self._lock:
            if self._done:
                return False
            self._cancel_requested = True
            self._cancel_msg = msg
            pending = self._pending_item
            if pending is not None:
                pending.cancelled_before_start = True
                pending.cancel_msg = msg
            task = self._task
            loop = self._loop

        if task is not None and loop is not None:
            running: asyncio.AbstractEventLoop | None = asyncio._get_running_loop()
            if running is loop:
                task.cancel(msg)
            else:
                loop.call_soon_threadsafe(task.cancel, msg)
        return True

    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def done(self) -> bool:
        with self._lock:
            return self._done

    def is_active(self) -> bool:
        with self._lock:
            return not self._done

    def exception(self) -> BaseException | None:
        with self._lock:
            if not self._done:
                raise asyncio.InvalidStateError("Job is not done yet")
            if self._cancelled:
                raise asyncio.CancelledError()
            return self._exception

    async def join(self) -> None:
        """Wait until the job completes (returning normally even if the job was cancelled)."""
        caller_loop = asyncio.get_running_loop()
        with self._lock:
            if self._done:
                return
            fut = cast("asyncio.Future[None]", caller_loop.create_future())
            self._waiters.append((caller_loop, fut))
        try:
            await fut
        except asyncio.CancelledError:
            with self._lock:
                try:
                    self._waiters.remove((caller_loop, fut))
                except ValueError:
                    pass
            raise


class Deferred(Job, Generic[T]):
    """A `Job` carrying a result value, created by `scope.defer` (SPEC S2.3, S4.5)."""

    def __init__(self, _token: object = None, *, name: str | None = None) -> None:
        if _token is not _PRIVATE_JOB_TOKEN:
            raise TypeError("Deferred cannot be instantiated outside a Scope or CoroutineScope (SPEC S2.3, S2.7)")
        super().__init__(_token, name=name)

    def result(self) -> T:
        with self._lock:
            if not self._done:
                raise asyncio.InvalidStateError("Deferred is not done yet")
            if self._cancelled:
                raise asyncio.CancelledError()
            if self._exception is not None:
                raise self._exception
            return cast(T, self._result)

    async def await_(self) -> T:
        await self.join()
        return self.result()

    def __await__(self) -> Generator[object, None, T]:
        coro = cast("Coroutine[object, object, T]", self.await_())
        return coro.__await__()


def _resolve_waiter_future(fut: asyncio.Future[None]) -> None:
    if not fut.done():
        fut.set_result(None)


def _unwrap_limited(
    dispatcher: Dispatcher,
) -> tuple[LimitedDispatcher | None, Dispatcher]:
    limited_outer: LimitedDispatcher | None = None
    current: Dispatcher = dispatcher
    while isinstance(current, LimitedDispatcher):
        limited_outer = current
        current = current.base
    if isinstance(current, MainDispatcher):
        current = current.resolve_delegate()
    elif isinstance(current, IODispatcher):
        current = current.carrier_pool
    return limited_outer, current


def _start_job_execution(
    job: Job,
    dispatcher: Dispatcher,
    fn: Callable[..., Coroutine[object, object, object]],
    args: tuple[object, ...],
    ctx: contextvars.Context,
    caller_loop: asyncio.AbstractEventLoop | None,
) -> None:
    """Schedule `fn(*args)` on `dispatcher` with S5.4 no-uncancel enforcement and S2.8 context propagation."""
    limited_outer, actual_dispatcher = _unwrap_limited(dispatcher)

    async def _wrapped_coro() -> object:
        current_task = cast("asyncio.Task[object] | None", asyncio.current_task())
        if limited_outer is not None:
            await limited_outer.acquire()
        try:
            res = await fn(*args)
        finally:
            if limited_outer is not None:
                limited_outer.release()
        if job._cancel_requested or (current_task is not None and current_task.cancelling() > 0):
            fn_name = cast("str | None", getattr(fn, "__name__", None))
            label = job.name or fn_name or "<anonymous>"
            raise RuntimeError(
                f"Task {label!r} swallowed CancelledError and finished normally after cancellation (SPEC S5.4)"
            )
        return res

    def _attach_completion(task: asyncio.Task[object], loop: asyncio.AbstractEventLoop) -> None:
        job._bind_task(task, loop)

        def _on_done(done_task: asyncio.Task[object]) -> None:
            if done_task.cancelled():
                job._complete(cancelled=True)
                return
            exc = done_task.exception()
            if exc is not None:
                job._complete(cancelled=False, exception=exc)
                return
            if job._cancel_requested or done_task.cancelling() > 0:
                fn_name = cast("str | None", getattr(fn, "__name__", None))
                label = job.name or fn_name or "<anonymous>"
                err = RuntimeError(
                    f"Task {label!r} swallowed CancelledError and finished normally after cancellation (SPEC S5.4)"
                )
                job._complete(cancelled=False, exception=err)
                return
            res = cast(object, done_task.result())
            job._complete(cancelled=False, result=res)

        task.add_done_callback(_on_done)

    if isinstance(actual_dispatcher, CarrierDispatcher):
        item = actual_dispatcher.submit_coroutine(_wrapped_coro, ctx, job.name, _attach_completion)
        job._bind_pending(item)
        return

    if isinstance(actual_dispatcher, KotlinDispatcherAdapter):
        target_loop = actual_dispatcher.loop

        def _spawn_on_kt() -> None:
            coro_kt = cast("Coroutine[object, object, object]", ctx.run(_wrapped_coro))
            t = target_loop.create_task(coro_kt, name=job.name, context=ctx)
            _attach_completion(t, target_loop)

        target_loop.call_soon_threadsafe(_spawn_on_kt)
        return

    if isinstance(actual_dispatcher, (LoopDispatcher, UnconfinedDispatcher)):
        if caller_loop is None:
            raise RuntimeError(
                f"{actual_dispatcher.name} requires a running event loop on the calling thread"
            )
        t = cast(
            "asyncio.Task[object]",
            caller_loop.create_task(ctx.run(_wrapped_coro), name=job.name, context=ctx),
        )
        _attach_completion(t, caller_loop)
        return

    custom_loop = actual_dispatcher.loop
    if custom_loop is not None:
        def _spawn_custom() -> None:
            t_custom = cast(
                "asyncio.Task[object]",
                custom_loop.create_task(ctx.run(_wrapped_coro), name=job.name, context=ctx),
            )
            _attach_completion(t_custom, custom_loop)

        custom_loop.call_soon_threadsafe(_spawn_custom)
        return

    if caller_loop is not None:
        t_caller = cast(
            "asyncio.Task[object]",
            caller_loop.create_task(ctx.run(_wrapped_coro), name=job.name, context=ctx),
        )
        _attach_completion(t_caller, caller_loop)
        return

    item = Dispatchers.Default.submit_coroutine(_wrapped_coro, ctx, job.name, _attach_completion)
    job._bind_pending(item)


class Scope:
    """Structured concurrency scope entered with `async with Scope(...) as s:` (SPEC S2.1–S2.5, S5.7, S8.1, S8.4)."""

    def __init__(
        self,
        *,
        dispatcher: Dispatcher | None = None,
        supervisor: bool = False,
    ) -> None:
        self._default_dispatcher: Dispatcher = dispatcher if dispatcher is not None else Dispatchers.Loop
        self._supervisor: bool = supervisor
        self._entered: bool = False
        self._exiting: bool = False
        self._cancelled: bool = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_task: asyncio.Task[object] | None = None
        self._lock: threading.Lock = threading.Lock()
        self._jobs: list[Job] = []
        self._failures: list[BaseException] = []

    async def __aenter__(self) -> Scope:
        if self._entered:
            raise RuntimeError("Scope cannot be entered more than once")
        self._entered = True
        self._owner_loop = asyncio.get_running_loop()
        self._owner_task = cast("asyncio.Task[object] | None", asyncio.current_task())
        return self

    def _resolve_child_dispatcher(self, override: Dispatcher | None) -> Dispatcher:
        if override is not None:
            return override
        return self._default_dispatcher

    def _on_child_completed(self, job: Job) -> None:
        if job.cancelled():
            return
        exc = job._exception
        if exc is None:
            return
        if self._supervisor:
            return
        with self._lock:
            self._failures.append(exc)
            siblings = [j for j in self._jobs if j is not job and not j.done()]
        for sib in siblings:
            sib.cancel()
        owner_loop = self._owner_loop
        owner_task = self._owner_task
        if owner_loop is not None and owner_task is not None and not self._exiting:
            owner_loop.call_soon_threadsafe(owner_task.cancel)

    def launch(
        self,
        fn: Callable[..., Coroutine[object, object, object]],
        *args: object,
        dispatcher: Dispatcher | None = None,
        name: str | None = None,
    ) -> Job:
        """Spawn `fn(*args)` in this scope and return its `Job` (SPEC S2.2)."""
        if not self._entered or self._exiting:
            raise RuntimeError("Scope is not active")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Scope.launch requires a coroutine function, got {fn!r}")
        fn_name = cast("str | None", getattr(fn, "__name__", None))
        job = Job(_PRIVATE_JOB_TOKEN, name=name if name is not None else fn_name)
        with self._lock:
            self._jobs.append(job)
            is_cancelled = self._cancelled
        job._add_done_callback(self._on_child_completed)
        if is_cancelled:
            job.cancel()
        target_disp = self._resolve_child_dispatcher(dispatcher)
        ctx = contextvars.copy_context()
        caller_loop = asyncio._get_running_loop()
        arg_tuple: tuple[object, ...] = tuple(args)
        _start_job_execution(job, target_disp, fn, arg_tuple, ctx, caller_loop)
        return job

    def defer(
        self,
        fn: Callable[..., Coroutine[object, object, T]],
        *args: object,
        dispatcher: Dispatcher | None = None,
        name: str | None = None,
    ) -> Deferred[T]:
        """Spawn `fn(*args)` in this scope and return a `Deferred[T]` (SPEC S2.3)."""
        if not self._entered or self._exiting:
            raise RuntimeError("Scope is not active")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Scope.defer requires a coroutine function, got {fn!r}")
        fn_name = cast("str | None", getattr(fn, "__name__", None))
        deferred: Deferred[T] = Deferred(_PRIVATE_JOB_TOKEN, name=name if name is not None else fn_name)
        with self._lock:
            self._jobs.append(deferred)
            is_cancelled = self._cancelled
        deferred._add_done_callback(self._on_child_completed)
        if is_cancelled:
            deferred.cancel()
        target_disp = self._resolve_child_dispatcher(dispatcher)
        ctx = contextvars.copy_context()
        caller_loop = asyncio._get_running_loop()
        arg_tuple: tuple[object, ...] = tuple(args)
        _start_job_execution(deferred, target_disp, fn, arg_tuple, ctx, caller_loop)
        return deferred

    def cancel(self) -> None:
        """Cancel every child and mark the scope cancelled. Idempotent (SPEC S5.7)."""
        with self._lock:
            self._cancelled = True
            jobs = list(self._jobs)
        for j in jobs:
            j.cancel()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self._exiting = True
        owner_task = self._owner_task

        body_cancelled_by_child = False
        if exc_val is not None:
            if isinstance(exc_val, asyncio.CancelledError):
                with self._lock:
                    has_child_failure = len(self._failures) > 0
                if has_child_failure and owner_task is not None and owner_task.cancelling() > 0:
                    owner_task.uncancel()
                    body_cancelled_by_child = True
                for j in list(self._jobs):
                    j.cancel()
            else:
                with self._lock:
                    self._failures.append(exc_val)
                if not self._supervisor:
                    for j in list(self._jobs):
                        j.cancel()

        saved_cancels = 0
        if owner_task is not None:
            while owner_task.cancelling() > 0:
                owner_task.uncancel()
                saved_cancels += 1
        try:
            while True:
                with self._lock:
                    pending = [j for j in self._jobs if not j.done()]
                if not pending:
                    break
                for j in pending:
                    await j.join()
        finally:
            if owner_task is not None:
                for _ in range(saved_cancels):
                    owner_task.cancel()

        with self._lock:
            failures = list(self._failures)

        if failures and not self._supervisor:
            normal_excs: list[Exception] = []
            base_excs: list[BaseException] = []
            for f in failures:
                if isinstance(f, Exception):
                    normal_excs.append(f)
                else:
                    base_excs.append(f)
            if base_excs:
                raise BaseExceptionGroup("unhandled errors in a Scope", failures)
            raise ExceptionGroup("unhandled errors in a Scope", normal_excs)

        if body_cancelled_by_child:
            return True
        return False


def supervisor_scope(*, dispatcher: Dispatcher | None = None) -> Scope:
    """Create a `Scope(supervisor=True)` that does not cancel siblings when a child fails (SPEC S2.5)."""
    return Scope(dispatcher=dispatcher, supervisor=True)


def _select_app_dispatcher(dispatcher: Dispatcher | None) -> Dispatcher:
    if dispatcher is None or isinstance(dispatcher, LoopDispatcher):
        return Dispatchers.Default
    return dispatcher


class CoroutineScope:
    """Long-lived app-level scope bound to a `Dispatcher` (SPEC S2.9).

    Lives until `cancel()` is called. `launch` and `defer` may be called from any thread, including
    threads with no running event loop (such as a Compose UI click callback).
    """

    def __init__(
        self,
        dispatcher: Dispatcher | None = None,
        *,
        supervisor: bool = False,
    ) -> None:
        self._dispatcher: Dispatcher = _select_app_dispatcher(dispatcher)
        self._supervisor: bool = supervisor
        self._lock: threading.Lock = threading.Lock()
        self._closed: bool = False
        self._jobs: list[Job] = []
        self._failures: list[BaseException] = []

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dispatcher

    def _on_child_completed(self, job: Job) -> None:
        if job.cancelled():
            return
        exc = job._exception
        if exc is None:
            return
        siblings: list[Job] = []
        with self._lock:
            if not self._supervisor:
                self._failures.append(exc)
                self._closed = True
                siblings = [j for j in self._jobs if j is not job and not j.done()]

        for sib in siblings:
            sib.cancel()

        hook = cast("Callable[[object], object] | None", getattr(sys, "unraisablehook", None))
        if hook is not None:
            args = SimpleNamespace(
                exc_type=type(exc),
                exc_value=exc,
                exc_traceback=exc.__traceback__,
                err_msg="Unhandled exception in CoroutineScope child job",
                object=job,
            )
            try:
                hook(args)
            except Exception:
                pass

    def launch(
        self,
        fn: Callable[..., Coroutine[object, object, object]],
        *args: object,
        dispatcher: Dispatcher | None = None,
        name: str | None = None,
    ) -> Job:
        """Spawn `fn(*args)` on the scope's dispatcher from any thread and return its `Job` (SPEC S2.9)."""
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"CoroutineScope.launch requires a coroutine function, got {fn!r}")
        fn_name = cast("str | None", getattr(fn, "__name__", None))
        with self._lock:
            if self._closed:
                raise RuntimeError(f"CoroutineScope({self._dispatcher.name}) is closed and cannot launch new jobs")
            job = Job(_PRIVATE_JOB_TOKEN, name=name if name is not None else fn_name)
            self._jobs.append(job)

        job._add_done_callback(self._on_child_completed)
        target_disp = _select_app_dispatcher(dispatcher if dispatcher is not None else self._dispatcher)
        ctx = contextvars.copy_context()
        caller_loop = asyncio._get_running_loop()
        arg_tuple: tuple[object, ...] = tuple(args)
        _start_job_execution(job, target_disp, fn, arg_tuple, ctx, caller_loop)
        return job

    def defer(
        self,
        fn: Callable[..., Coroutine[object, object, T]],
        *args: object,
        dispatcher: Dispatcher | None = None,
        name: str | None = None,
    ) -> Deferred[T]:
        """Spawn `fn(*args)` on the scope's dispatcher from any thread and return a `Deferred[T]` (SPEC S2.9)."""
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"CoroutineScope.defer requires a coroutine function, got {fn!r}")
        fn_name = cast("str | None", getattr(fn, "__name__", None))
        with self._lock:
            if self._closed:
                raise RuntimeError(f"CoroutineScope({self._dispatcher.name}) is closed and cannot launch new jobs")
            deferred: Deferred[T] = Deferred(_PRIVATE_JOB_TOKEN, name=name if name is not None else fn_name)
            self._jobs.append(deferred)

        deferred._add_done_callback(self._on_child_completed)
        target_disp = _select_app_dispatcher(dispatcher if dispatcher is not None else self._dispatcher)
        ctx = contextvars.copy_context()
        caller_loop = asyncio._get_running_loop()
        arg_tuple: tuple[object, ...] = tuple(args)
        _start_job_execution(deferred, target_disp, fn, arg_tuple, ctx, caller_loop)
        return deferred

    def cancel(self) -> None:
        """Cancel every child and close the scope. Idempotent (SPEC S2.9, S5.7)."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs)
        for j in jobs:
            j.cancel()

    async def join(self) -> None:
        """Wait for all children launched so far and raise `ExceptionGroup` if any failed (SPEC S2.9, S8.1)."""
        while True:
            with self._lock:
                snapshot = list(self._jobs)
                pending = [j for j in snapshot if not j.done()]
            if not pending:
                break
            for j in pending:
                await j.join()

        with self._lock:
            failures = list(self._failures)

        if failures and not self._supervisor:
            normal_excs: list[Exception] = [f for f in failures if isinstance(f, Exception)]
            if len(normal_excs) == len(failures):
                raise ExceptionGroup("unhandled errors in CoroutineScope", normal_excs)
            raise BaseExceptionGroup("unhandled errors in CoroutineScope", failures)
