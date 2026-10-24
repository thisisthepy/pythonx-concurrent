"""Dispatchers, carrier event loops, work stealing, with_context and run_blocking.

Covers SPEC S2.4, S3.1–S3.7, S4.1–S4.4, S5.5, S7.1–S7.5, S9.1, S9.2.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import importlib
import inspect
import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Coroutine
from typing import TypeVar, cast

from pythonx.concurrent._cancellation import CancelToken
from pythonx.concurrent._kotlin_loop import KotlinEventLoop

T = TypeVar("T")


def _default_carrier_count() -> int:
    """SPEC S7.2: os.cpu_count() on free-threaded 3.15t, 1 on a GIL build, or PYTHONX_CONCURRENT_CARRIERS."""
    override = os.environ.get("PYTHONX_CONCURRENT_CARRIERS")
    if override is not None:
        try:
            parsed = int(override)
            if parsed >= 1:
                return parsed
        except ValueError:
            pass
    gil_enabled = bool(sys._is_gil_enabled())
    if gil_enabled:
        return 1
    return max(1, os.cpu_count() or 1)


class _PendingItem:
    """A coroutine task queued before its first step so idle carriers can steal it (SPEC S7.3)."""

    def __init__(
        self,
        coro_factory: Callable[[], Coroutine[object, object, object]],
        ctx: contextvars.Context,
        name: str | None,
        on_task_created: Callable[[asyncio.Task[object], asyncio.AbstractEventLoop], None],
    ) -> None:
        self.coro_factory: Callable[[], Coroutine[object, object, object]] = coro_factory
        self.ctx: contextvars.Context = ctx
        self.name: str | None = name
        self.on_task_created: Callable[[asyncio.Task[object], asyncio.AbstractEventLoop], None] = on_task_created
        self.claimed: bool = False
        self.cancelled_before_start: bool = False
        self.cancel_msg: str | None = None


class _CarrierThread:
    """One daemon thread owning a standard selector-backed asyncio event loop (SPEC S4.2)."""

    def __init__(self, pool: CarrierDispatcher, index: int, name: str) -> None:
        self.pool: CarrierDispatcher = pool
        self.index: int = index
        self.name: str = name
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self.thread_id: int = -1
        self.active_tasks: int = 0
        self.pending: deque[_PendingItem] = deque()
        self._started: bool = False
        self._ready: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread = t
        t.start()
        self._ready.wait()

    def _run(self) -> None:
        self.thread_id = threading.get_ident()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass


class Dispatcher:
    """Base class for Python coroutine dispatchers (SPEC S3)."""

    def __init__(self, name: str) -> None:
        self.name: str = name

    @property
    def carrier_count(self) -> int:
        return 1

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return None

    def is_main_dispatcher(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"Dispatcher({self.name!r})"


class LoopDispatcher(Dispatcher):
    """Runs tasks on the caller's already-running asyncio event loop (SPEC S3.1, S4.3)."""

    def __init__(self) -> None:
        super().__init__("Dispatchers.Loop")


class UnconfinedDispatcher(Dispatcher):
    """Starts execution in the caller frame until the first suspension point (SPEC S3.1, S3.2)."""

    def __init__(self) -> None:
        super().__init__("Dispatchers.Unconfined")


class CarrierDispatcher(Dispatcher):
    """Multi-carrier event-loop pool with least-loaded placement and pre-first-step work stealing (SPEC S4.2, S7.1–S7.3)."""

    def __init__(self, name: str, *, carriers: int | None = None) -> None:
        super().__init__(name)
        count = carriers if carriers is not None else _default_carrier_count()
        if count < 1:
            raise ValueError(f"carriers must be >= 1, got {count}")
        self._carrier_count: int = count
        self._lock: threading.Lock = threading.Lock()
        self._closed: bool = False
        self._carriers: list[_CarrierThread] = [
            _CarrierThread(self, i, f"{name}-carrier-{i}") for i in range(count)
        ]

    @property
    def carrier_count(self) -> int:
        return self._carrier_count

    def _select_least_loaded_locked(self) -> _CarrierThread:
        best = self._carriers[0]
        best_load = best.active_tasks + len(best.pending)
        for c in self._carriers[1:]:
            load = c.active_tasks + len(c.pending)
            if load < best_load:
                best = c
                best_load = load
        return best

    def submit_coroutine(
        self,
        coro_factory: Callable[[], Coroutine[object, object, object]],
        ctx: contextvars.Context,
        name: str | None,
        on_task_created: Callable[[asyncio.Task[object], asyncio.AbstractEventLoop], None],
    ) -> _PendingItem:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"Dispatcher {self.name!r} is closed")
            target = self._select_least_loaded_locked()
            target.ensure_started()
            item = _PendingItem(coro_factory, ctx, name, on_task_created)
            target.pending.append(item)
            other_carriers = [c for c in self._carriers if c is not target]
            for c in other_carriers:
                if c.active_tasks == 0 and len(c.pending) == 0:
                    c.ensure_started()

        target.loop.call_soon_threadsafe(self._drain_on_carrier, target)
        for c in other_carriers:
            if c._started:
                c.loop.call_soon_threadsafe(self._steal_or_drain_on_carrier, c)
        return item

    def _claim_next_for_carrier(self, carrier: _CarrierThread, *, allow_steal: bool) -> _PendingItem | None:
        with self._lock:
            while carrier.pending:
                item = carrier.pending.popleft()
                if not item.claimed:
                    item.claimed = True
                    carrier.active_tasks += 1
                    return item
            if allow_steal:
                for donor in self._carriers:
                    if donor is carrier:
                        continue
                    while donor.pending:
                        item = donor.pending.popleft()
                        if not item.claimed:
                            item.claimed = True
                            carrier.active_tasks += 1
                            return item
        return None

    def _drain_on_carrier(self, carrier: _CarrierThread) -> None:
        while True:
            item = self._claim_next_for_carrier(carrier, allow_steal=False)
            if item is None:
                break
            self._start_claimed_item(carrier, item)

    def _steal_or_drain_on_carrier(self, carrier: _CarrierThread) -> None:
        while True:
            item = self._claim_next_for_carrier(carrier, allow_steal=True)
            if item is None:
                break
            self._start_claimed_item(carrier, item)

    def _start_claimed_item(self, carrier: _CarrierThread, item: _PendingItem) -> None:
        loop = carrier.loop
        coro = item.ctx.run(item.coro_factory)
        task = cast("asyncio.Task[object]", loop.create_task(coro, name=item.name, context=item.ctx))
        if item.cancelled_before_start:
            task.cancel(item.cancel_msg)

        def _on_done(_t: asyncio.Task[object]) -> None:
            with self._lock:
                carrier.active_tasks = max(0, carrier.active_tasks - 1)
            self._steal_or_drain_on_carrier(carrier)

        task.add_done_callback(_on_done)
        item.on_task_created(task, loop)

    async def _probe_pre_first_step_steal(
        self,
        blocker_started: threading.Event,
        release_blocker: threading.Event,
    ) -> int | None:
        """Diagnostic probe used by S7.3 tests to verify pre-first-step work stealing."""
        if self._carrier_count < 2:
            return None
        c0 = self._carriers[0]
        c1 = self._carriers[1]
        c0.ensure_started()
        c1.ensure_started()

        caller_loop = asyncio.get_running_loop()
        stolen_future = cast("asyncio.Future[int]", caller_loop.create_future())

        def _block_c0() -> None:
            blocker_started.set()
            release_blocker.wait(timeout=2.0)

        async def _victim() -> int:
            return threading.get_ident()

        def _on_victim_created(task: asyncio.Task[object], _loop: asyncio.AbstractEventLoop) -> None:
            def _done(t: asyncio.Task[object]) -> None:
                if not stolen_future.done():
                    res = cast(int, t.result())
                    caller_loop.call_soon_threadsafe(stolen_future.set_result, res)

            task.add_done_callback(_done)

        c0.loop.call_soon_threadsafe(_block_c0)
        while not blocker_started.is_set():
            await asyncio.sleep(0.001)

        with self._lock:
            item = _PendingItem(
                lambda: _victim(),
                contextvars.copy_context(),
                "steal-victim",
                _on_victim_created,
            )
            c0.pending.append(item)

        c1.loop.call_soon_threadsafe(self._steal_or_drain_on_carrier, c1)
        try:
            victim_tid = await asyncio.wait_for(stolen_future, timeout=2.0)
        finally:
            release_blocker.set()
        return victim_tid if victim_tid == c1.thread_id else None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            carriers = list(self._carriers)
        for c in carriers:
            if c._started:
                c.loop.call_soon_threadsafe(c.loop.stop)
        for c in carriers:
            t = c._thread
            if t is not None and t.is_alive() and threading.get_ident() != c.thread_id:
                t.join(timeout=2.0)


class IODispatcher(Dispatcher):
    """Dispatcher for blocking I/O and on-demand IO carrier loops (SPEC S3.2, S3.6, S5.5)."""

    def __init__(self, *, max_workers: int = 64) -> None:
        super().__init__("Dispatchers.IO")
        self._executor: concurrent.futures.ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="pythonx-io",
        )
        self._carrier_pool: CarrierDispatcher = CarrierDispatcher(
            "Dispatchers.IO.Async", carriers=max(2, _default_carrier_count())
        )

    @property
    def carrier_pool(self) -> CarrierDispatcher:
        return self._carrier_pool

    async def run_blocking_fn(
        self,
        fn: Callable[..., T],
        *args: object,
        ctx: contextvars.Context,
    ) -> T:
        caller_loop = asyncio.get_running_loop()
        token = CancelToken()
        call_args: list[object] = list(args)
        if _accepts_cancel_token(fn, len(call_args)):
            call_args.append(token)

        def _invoke() -> T:
            return ctx.run(fn, *call_args)

        cf_fut: concurrent.futures.Future[T] = self._executor.submit(_invoke)
        aio_fut = cast("asyncio.Future[T]", asyncio.wrap_future(cf_fut, loop=caller_loop))
        try:
            return await asyncio.shield(aio_fut)
        except asyncio.CancelledError:
            token._cancel()
            raise

    def close(self) -> None:
        self._carrier_pool.close()
        self._executor.shutdown(wait=False, cancel_futures=True)


def _accepts_cancel_token(fn: Callable[..., object], provided_positional: int) -> bool:
    """Return True if `fn` has an unfilled parameter named `cancel_token` or annotated with `CancelToken`."""
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return False
    params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(params) <= provided_positional:
        return False
    candidate = params[provided_positional]
    if candidate.name == "cancel_token":
        return True
    ann = cast(object, candidate.annotation)
    if ann is CancelToken or (isinstance(ann, str) and ann.endswith("CancelToken")):
        return True
    return False


class KotlinDispatcherAdapter(Dispatcher):
    """Wraps a Kotlin CoroutineDispatcher with a KotlinEventLoop (SPEC S3.3, S3.7, S4.1)."""

    def __init__(
        self,
        kotlin_dispatcher: object,
        *,
        name: str = "Dispatchers.Kotlin",
        slow_step_ms: float = 100.0,
        is_main: bool = False,
    ) -> None:
        super().__init__(name)
        self._kotlin_dispatcher: object = kotlin_dispatcher
        self._loop: KotlinEventLoop = KotlinEventLoop(kotlin_dispatcher, slow_step_ms=slow_step_ms)
        self._is_main: bool = is_main

    @property
    def loop(self) -> KotlinEventLoop:
        return self._loop

    def is_main_dispatcher(self) -> bool:
        return self._is_main

    def close(self) -> None:
        self._loop.close()


class MainDispatcher(Dispatcher):
    """UI main-thread dispatcher backed by Kotlin Dispatchers.Main when available (SPEC S3.1, S3.3)."""

    def __init__(self) -> None:
        super().__init__("Dispatchers.Main")
        self._lock: threading.Lock = threading.Lock()
        self._adapter: KotlinDispatcherAdapter | None = None
        self._fallback_carrier: CarrierDispatcher | None = None

    def is_main_dispatcher(self) -> bool:
        return True

    def configure(self, kotlin_main_dispatcher: object, *, slow_step_ms: float = 100.0) -> None:
        """Bind `Dispatchers.Main` to an explicit Kotlin main dispatcher or poster."""
        with self._lock:
            if self._adapter is not None:
                self._adapter.close()
            self._adapter = KotlinDispatcherAdapter(
                kotlin_main_dispatcher,
                name="Dispatchers.Main",
                slow_step_ms=slow_step_ms,
                is_main=True,
            )

    def resolve_delegate(self) -> Dispatcher:
        with self._lock:
            if self._adapter is not None:
                return self._adapter
            try:
                kc = importlib.import_module("kotlinx.coroutines")
                kt_disp_ns = cast(object, getattr(kc, "Dispatchers", None))
                kt_main = cast(object, getattr(kt_disp_ns, "Main", None)) if kt_disp_ns is not None else None
                if kt_main is not None:
                    self._adapter = KotlinDispatcherAdapter(
                        kt_main,
                        name="Dispatchers.Main",
                        slow_step_ms=100.0,
                        is_main=True,
                    )
                    return self._adapter
            except Exception:
                pass
            if self._fallback_carrier is None:
                self._fallback_carrier = CarrierDispatcher("Dispatchers.Main.Fallback", carriers=1)
            return self._fallback_carrier

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        delegate = self.resolve_delegate()
        return delegate.loop


class LimitedDispatcher(Dispatcher):
    """Caps the number of tasks running concurrently on an underlying dispatcher (SPEC S3.7)."""

    def __init__(self, base: Dispatcher, parallelism: int) -> None:
        if parallelism < 1:
            raise ValueError(f"parallelism must be >= 1, got {parallelism}")
        super().__init__(f"{base.name}.limited({parallelism})")
        self.base: Dispatcher = base
        self.parallelism: int = parallelism
        self._lock: threading.Lock = threading.Lock()
        self._active: int = 0
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._active < self.parallelism:
                self._active += 1
                return
            fut = cast("asyncio.Future[None]", loop.create_future())
            self._waiters.append((loop, fut))
        try:
            await fut
        except asyncio.CancelledError:
            with self._lock:
                try:
                    self._waiters.remove((loop, fut))
                except ValueError:
                    self._release_locked()
            raise

    def release(self) -> None:
        with self._lock:
            self._release_locked()

    def _release_locked(self) -> None:
        while self._waiters:
            waiter_loop, fut = self._waiters.popleft()
            if not fut.cancelled():
                waiter_loop.call_soon_threadsafe(_safe_set_empty_result, fut)
                return
        self._active = max(0, self._active - 1)


def _safe_set_empty_result(fut: asyncio.Future[None]) -> None:
    if not fut.done():
        fut.set_result(None)


class _DispatchersNamespace:
    """Standard `Dispatchers` namespace: `Default`, `IO`, `Main`, `Unconfined`, `Loop`, `from_kotlin`, `limited`."""

    def __init__(self) -> None:
        self.Loop: LoopDispatcher = LoopDispatcher()
        self.Unconfined: UnconfinedDispatcher = UnconfinedDispatcher()
        self.Default: CarrierDispatcher = CarrierDispatcher("Dispatchers.Default")
        self.IO: IODispatcher = IODispatcher()
        self.Main: MainDispatcher = MainDispatcher()

    def from_kotlin(
        self,
        kotlin_dispatcher: object,
        *,
        name: str = "Dispatchers.Kotlin",
        slow_step_ms: float = 100.0,
    ) -> KotlinDispatcherAdapter:
        """Wrap any Kotlin `CoroutineDispatcher` (SPEC S3.7)."""
        return KotlinDispatcherAdapter(kotlin_dispatcher, name=name, slow_step_ms=slow_step_ms)

    def limited(self, dispatcher: Dispatcher, parallelism: int) -> LimitedDispatcher:
        """Return a view of `dispatcher` capped at `parallelism` concurrent tasks (SPEC S3.7)."""
        return LimitedDispatcher(dispatcher, parallelism)

    def create_carrier_dispatcher(self, name: str, *, carriers: int | None = None) -> CarrierDispatcher:
        """Create a dedicated carrier loop pool."""
        return CarrierDispatcher(name, carriers=carriers)


Dispatchers: _DispatchersNamespace = _DispatchersNamespace()


async def _await_across_loops(
    target_loop: asyncio.AbstractEventLoop,
    coro_factory: Callable[[], Coroutine[object, object, T]],
    ctx: contextvars.Context,
) -> T:
    caller_loop = asyncio.get_running_loop()
    if caller_loop is target_loop:
        coro = ctx.run(coro_factory)
        return await coro

    bridge = cast("asyncio.Future[T]", caller_loop.create_future())
    task_holder: list[asyncio.Task[T]] = []

    def _start_on_target() -> None:
        if bridge.cancelled():
            return
        coro = ctx.run(coro_factory)
        t = cast("asyncio.Task[T]", target_loop.create_task(coro, context=ctx))
        task_holder.append(t)

        def _done(done_task: asyncio.Task[T]) -> None:
            if done_task.cancelled():
                caller_loop.call_soon_threadsafe(_cancel_if_pending, bridge)
            else:
                exc = done_task.exception()
                if exc is not None:
                    caller_loop.call_soon_threadsafe(_set_exc_if_pending, bridge, exc)
                else:
                    res = cast(T, done_task.result())
                    caller_loop.call_soon_threadsafe(_set_res_if_pending, bridge, res)

        t.add_done_callback(_done)

    target_loop.call_soon_threadsafe(_start_on_target)
    try:
        return await bridge
    except asyncio.CancelledError:
        def _cancel_target() -> None:
            for t in task_holder:
                t.cancel()

        target_loop.call_soon_threadsafe(_cancel_target)
        raise


def _cancel_if_pending(fut: asyncio.Future[T]) -> None:
    if not fut.done():
        fut.cancel()


def _set_exc_if_pending(fut: asyncio.Future[T], exc: BaseException) -> None:
    if not fut.done():
        fut.set_exception(exc)


def _set_res_if_pending(fut: asyncio.Future[T], res: T) -> None:
    if not fut.done():
        fut.set_result(res)


async def with_context(
    dispatcher: Dispatcher,
    fn: Callable[..., object],
    *args: object,
) -> object:
    """Run `fn(*args)` on `dispatcher` and return its result (SPEC S2.4, S3.5, S3.6, S2.8)."""
    ctx = contextvars.copy_context()

    if isinstance(dispatcher, LimitedDispatcher):
        await dispatcher.acquire()
        try:
            return await with_context(dispatcher.base, fn, *args)
        finally:
            dispatcher.release()

    if isinstance(dispatcher, MainDispatcher):
        dispatcher = dispatcher.resolve_delegate()

    if not inspect.iscoroutinefunction(fn):
        if isinstance(dispatcher, IODispatcher):
            return await dispatcher.run_blocking_fn(fn, *args, ctx=ctx)
        raise TypeError(
            f"with_context on {dispatcher.name} requires an async coroutine function (plain blocking functions require Dispatchers.IO)"
        )

    coro_fn = cast("Callable[..., Coroutine[object, object, object]]", fn)

    if isinstance(dispatcher, (LoopDispatcher, UnconfinedDispatcher)):
        return await ctx.run(coro_fn, *args)

    if isinstance(dispatcher, IODispatcher):
        dispatcher = dispatcher.carrier_pool

    if isinstance(dispatcher, KotlinDispatcherAdapter):
        return await _await_across_loops(dispatcher.loop, lambda: coro_fn(*args), ctx)

    if isinstance(dispatcher, CarrierDispatcher):
        caller_loop = asyncio.get_running_loop()
        bridge = cast("asyncio.Future[object]", caller_loop.create_future())
        task_ref: list[tuple[asyncio.Task[object], asyncio.AbstractEventLoop]] = []

        def _on_created(task: asyncio.Task[object], carrier_loop: asyncio.AbstractEventLoop) -> None:
            task_ref.append((task, carrier_loop))

            def _done(done_task: asyncio.Task[object]) -> None:
                if done_task.cancelled():
                    caller_loop.call_soon_threadsafe(_cancel_if_pending, bridge)
                else:
                    exc = done_task.exception()
                    if exc is not None:
                        caller_loop.call_soon_threadsafe(_set_exc_if_pending, bridge, exc)
                    else:
                        res = cast(object, done_task.result())
                        caller_loop.call_soon_threadsafe(_set_res_if_pending, bridge, res)

            task.add_done_callback(_done)

        fn_name = cast("str | None", getattr(fn, "__name__", None))
        pending = dispatcher.submit_coroutine(lambda: coro_fn(*args), ctx, fn_name, _on_created)
        try:
            return await bridge
        except asyncio.CancelledError:
            pending.cancelled_before_start = True
            for t, c_loop in task_ref:
                c_loop.call_soon_threadsafe(t.cancel)
            raise

    return await ctx.run(coro_fn, *args)


async def run_blocking_call(fn: Callable[..., T], *args: object) -> T:
    """Run a plain blocking function on `Dispatchers.IO` without holding the carrier (SPEC S7.5)."""
    res = await with_context(Dispatchers.IO, fn, *args)
    return cast(T, res)


def run_blocking(
    fn: Callable[..., Coroutine[object, object, T]],
    *args: object,
    dispatcher: Dispatcher | None = None,
) -> T:
    """Run a coroutine function to completion on a carrier and block the caller (SPEC S4.4).

    Raises `RuntimeError` when called on `Dispatchers.Main` or inside a `KotlinEventLoop`, because
    blocking the UI thread would deadlock the message loop.
    """
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"run_blocking requires a coroutine function, got {fn!r}")

    running_loop = asyncio._get_running_loop()
    if isinstance(running_loop, KotlinEventLoop):
        raise RuntimeError("run_blocking cannot be called on Dispatchers.Main / KotlinEventLoop (would deadlock UI)")
    if dispatcher is not None and dispatcher.is_main_dispatcher():
        raise RuntimeError("run_blocking cannot target Dispatchers.Main")

    target = dispatcher if dispatcher is not None else Dispatchers.Default
    ctx = contextvars.copy_context()
    done_event = threading.Event()
    result_box: list[T] = []
    exc_box: list[BaseException] = []

    async def _runner() -> object:
        return await with_context(target, fn, *args)

    def _on_created(task: asyncio.Task[object], _loop: asyncio.AbstractEventLoop) -> None:
        def _done(done_task: asyncio.Task[object]) -> None:
            if done_task.cancelled():
                exc_box.append(asyncio.CancelledError())
            else:
                exc = done_task.exception()
                if exc is not None:
                    exc_box.append(exc)
                else:
                    res = cast(T, done_task.result())
                    result_box.append(res)
            done_event.set()

        task.add_done_callback(_done)

    fn_name = cast("str | None", getattr(fn, "__name__", None))
    Dispatchers.Default.submit_coroutine(_runner, ctx, fn_name, _on_created)
    done_event.wait()
    if exc_box:
        raise exc_box[0]
    return result_box[0]
