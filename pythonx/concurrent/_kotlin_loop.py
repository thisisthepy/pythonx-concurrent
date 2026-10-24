"""Event loop driven by a Kotlin CoroutineDispatcher (SPEC S3.3, S4.1)."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Protocol, cast

_logger: logging.Logger = logging.getLogger("pythonx.concurrent")


class _KotlinDispatcherLike(Protocol):
    def dispatch(self, context: object, block: Callable[[], None]) -> None: ...


class KotlinEventLoop(asyncio.AbstractEventLoop):
    """An asyncio event loop whose scheduling belongs to a Kotlin dispatcher (such as Dispatchers.Main).

    `call_soon`, `call_soon_threadsafe`, `call_later` and `call_at` post callbacks to the underlying
    Kotlin dispatcher. Because the UI thread already owns a message loop, this loop does not own a
    `selectors` selector: `add_reader`, `add_writer` and subprocess APIs raise `NotImplementedError`
    (SPEC S4.1). Steps that exceed `slow_step_ms` emit a warning on the `pythonx.concurrent` logger
    (SPEC S3.3).
    """

    def __init__(self, kotlin_dispatcher: object, *, slow_step_ms: float = 100.0) -> None:
        self._kotlin_dispatcher: object = kotlin_dispatcher
        self._slow_step_ms: float = slow_step_ms
        self._closed: bool = False
        self._debug: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._timers: dict[asyncio.TimerHandle, threading.Timer] = {}
        self._exception_handler: Callable[[asyncio.AbstractEventLoop, dict[str, object]], object] | None = None

    def _post(self, callback: Callable[[], None]) -> None:
        disp = self._kotlin_dispatcher
        if hasattr(disp, "dispatch"):
            cast(_KotlinDispatcherLike, disp).dispatch(None, callback)
        elif callable(disp):
            cast("Callable[[Callable[[], None]], object]", disp)(callback)
        else:
            raise TypeError(
                f"Kotlin dispatcher {disp!r} must provide a callable .dispatch(context, block) or be callable"
            )

    def _run_handle(self, handle: asyncio.Handle) -> None:
        if handle.cancelled():
            return
        prev_loop = asyncio._get_running_loop()
        asyncio._set_running_loop(self)
        start = time.perf_counter()
        try:
            handle._run()
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            asyncio._set_running_loop(prev_loop)
            if self._slow_step_ms > 0.0 and elapsed_ms >= self._slow_step_ms:
                _logger.warning(
                    "Step on Main / KotlinEventLoop took %.1f ms (threshold %.1f ms); avoid blocking the UI thread",
                    elapsed_ms,
                    self._slow_step_ms,
                )

    def get_debug(self) -> bool:
        return self._debug

    def set_debug(self, enabled: bool) -> None:
        self._debug = bool(enabled)

    def time(self) -> float:
        return time.monotonic()

    def is_running(self) -> bool:
        return not self._closed

    def is_closed(self) -> bool:
        return self._closed

    def stop(self) -> None:
        pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _timer_handle_cancelled(self, handle: asyncio.TimerHandle) -> None:
        with self._lock:
            timer = self._timers.pop(handle, None)
        if timer is not None:
            timer.cancel()

    def call_soon(
        self,
        callback: Callable[..., object],
        *args: object,
        context: contextvars.Context | None = None,
    ) -> asyncio.Handle:
        if self._closed:
            raise RuntimeError("Event loop is closed")
        arg_list: list[object] = list(args)
        handle = asyncio.Handle(callback, arg_list, self, context)
        self._post(lambda: self._run_handle(handle))
        return handle

    def call_soon_threadsafe(
        self,
        callback: Callable[..., object],
        *args: object,
        context: contextvars.Context | None = None,
    ) -> asyncio.Handle:
        return self.call_soon(callback, *args, context=context)

    def call_later(
        self,
        delay: float,
        callback: Callable[..., object],
        *args: object,
        context: contextvars.Context | None = None,
    ) -> asyncio.TimerHandle:
        when = self.time() + max(0.0, delay)
        return self.call_at(when, callback, *args, context=context)

    def call_at(
        self,
        when: float,
        callback: Callable[..., object],
        *args: object,
        context: contextvars.Context | None = None,
    ) -> asyncio.TimerHandle:
        if self._closed:
            raise RuntimeError("Event loop is closed")
        arg_list: list[object] = list(args)
        timer_handle = asyncio.TimerHandle(when, callback, arg_list, self, context)
        delay = max(0.0, when - self.time())
        if delay <= 0.0:
            self._post(lambda: self._run_handle(timer_handle))
            return timer_handle

        def _fire() -> None:
            with self._lock:
                self._timers.pop(timer_handle, None)
                if self._closed or timer_handle.cancelled():
                    return
            self._post(lambda: self._run_handle(timer_handle))

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        with self._lock:
            self._timers[timer_handle] = timer
        timer.start()
        return timer_handle

    def create_future(self) -> asyncio.Future[object]:
        return asyncio.Future(loop=self)

    def create_task(  # type: ignore[override]
        self,
        coro: Coroutine[object, object, object],
        *,
        name: str | None = None,
        context: contextvars.Context | None = None,
    ) -> asyncio.Task[object]:
        if self._closed:
            raise RuntimeError("Event loop is closed")
        if context is None:
            return cast("asyncio.Task[object]", asyncio.Task(coro, loop=self, name=name))
        return cast("asyncio.Task[object]", asyncio.Task(coro, loop=self, name=name, context=context))

    def call_exception_handler(self, context: dict[str, object]) -> None:
        if self._exception_handler is not None:
            self._exception_handler(self, context)
            return
        self.default_exception_handler(context)

    def set_exception_handler(
        self,
        handler: Callable[[asyncio.AbstractEventLoop, dict[str, object]], object] | None,
    ) -> None:
        self._exception_handler = handler

    def get_exception_handler(
        self,
    ) -> Callable[[asyncio.AbstractEventLoop, dict[str, object]], object] | None:
        return self._exception_handler

    def default_exception_handler(self, context: dict[str, object]) -> None:
        msg = context.get("message", "Unhandled exception in KotlinEventLoop")
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            _logger.error("%s", msg, exc_info=exc)
        else:
            _logger.error("%s", msg)

    def add_reader(self, fd: object, callback: Callable[..., object], *args: object) -> None:
        raise NotImplementedError("KotlinEventLoop does not own a selector; perform network I/O on Dispatchers.IO")

    def remove_reader(self, fd: object) -> bool:
        raise NotImplementedError("KotlinEventLoop does not own a selector; perform network I/O on Dispatchers.IO")

    def add_writer(self, fd: object, callback: Callable[..., object], *args: object) -> None:
        raise NotImplementedError("KotlinEventLoop does not own a selector; perform network I/O on Dispatchers.IO")

    def remove_writer(self, fd: object) -> bool:
        raise NotImplementedError("KotlinEventLoop does not own a selector; perform network I/O on Dispatchers.IO")

    async def subprocess_exec(  # type: ignore[override]
        self,
        protocol_factory: object,
        program: object,
        *args: object,
        **kwargs: object,
    ) -> tuple[asyncio.SubprocessTransport, asyncio.SubprocessProtocol]:
        raise NotImplementedError("KotlinEventLoop does not support subprocesses; use Dispatchers.IO")

    async def subprocess_shell(  # type: ignore[override]
        self,
        protocol_factory: object,
        cmd: object,
        **kwargs: object,
    ) -> tuple[asyncio.SubprocessTransport, asyncio.SubprocessProtocol]:
        raise NotImplementedError("KotlinEventLoop does not support subprocesses; use Dispatchers.IO")
