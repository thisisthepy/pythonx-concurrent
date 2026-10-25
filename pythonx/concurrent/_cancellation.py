"""Cancellation tokens, timeouts and NonCancellable shielding (SPEC S2.6, S5.5, S5.6)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Protocol, cast


class _TaskWithCancel(Protocol):
    def cancelling(self) -> int: ...
    def uncancel(self) -> int: ...
    def cancel(self, msg: str | None = None) -> bool: ...


class CancelToken:
    """Thread-safe cooperative cancellation token passed to blocking functions on Dispatchers.IO (SPEC S5.5)."""

    def __init__(self) -> None:
        self._event: threading.Event = threading.Event()

    def _cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        """Return True if the awaiting task has been cancelled."""
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until cancelled or until `timeout` seconds elapse."""
        return self._event.wait(timeout=timeout)


class TimeoutFlag:
    """Yielded by `with_timeout_or_none(seconds)`: truthy while active, falsy (`expired == True`) after timeout."""

    def __init__(self) -> None:
        self.expired: bool = False

    def __bool__(self) -> bool:
        return not self.expired


@asynccontextmanager
async def with_timeout(seconds: float | None) -> AsyncIterator[asyncio.Timeout]:
    """Wrap `asyncio.timeout(seconds)` and raise built-in `TimeoutError` on expiry (SPEC S2.6)."""
    async with asyncio.timeout(seconds) as cm:
        yield cm


@asynccontextmanager
async def with_timeout_or_none(seconds: float | None) -> AsyncIterator[TimeoutFlag]:
    """Run the block with a timeout and mark the yielded `TimeoutFlag` expired instead of raising (SPEC S2.6)."""
    flag = TimeoutFlag()
    try:
        async with asyncio.timeout(seconds):
            yield flag
    except TimeoutError:
        flag.expired = True


@asynccontextmanager
async def non_cancellable() -> AsyncIterator[None]:
    """Shield a cleanup block from cancellation and restore any pending cancellation on exit (SPEC S5.6).

    Works both when the current task is already cancelling upon entering a `finally:` block and when a
    cancellation arrives while awaiting inside the `async with non_cancellable():` body.
    """
    task = cast("_TaskWithCancel | None", asyncio.current_task())
    if task is None:
        yield
        return

    saved_cancels = 0
    while task.cancelling() > 0:
        task.uncancel()
        saved_cancels += 1

    orig_cancel: Callable[[str | None], bool] = task.cancel

    def _deferred_cancel(msg: str | None = None) -> bool:
        nonlocal saved_cancels
        saved_cancels += 1
        return True

    object.__setattr__(task, "cancel", _deferred_cancel)
    try:
        yield
    finally:
        object.__setattr__(task, "cancel", orig_cancel)
        for _ in range(saved_cancels):
            orig_cancel(None)
        if saved_cancels > 0:
            await asyncio.sleep(0)
            raise asyncio.CancelledError()
