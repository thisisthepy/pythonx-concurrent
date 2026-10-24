"""Dispatchers, carrier loops, KotlinEventLoop, with_context, run_blocking and cross-loop awaiting.

Covers SPEC S2.4, S3.1–S3.7, S4.1–S4.5, S5.5, S7.1–S7.5, S9.1, S9.2, S10.6.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import sys
import threading
import time
import unittest
from collections.abc import Callable

import pythonx.concurrent as pc


class FakeKotlinDispatcher:
    """Fake Kotlin CoroutineDispatcher with `.dispatch(context, runnable)` on a dedicated worker thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self.thread_id: int = -1
        self.dispatch_count = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fake-kotlin-main", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        self.thread_id = threading.get_ident()
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    def dispatch(self, context: object, block: Callable[[], None]) -> None:
        self.dispatch_count += 1
        self._loop.call_soon_threadsafe(block)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)


class DispatchersAndLoopsTests(unittest.IsolatedAsyncioTestCase):
    """SPEC S2.4, S3.1–S3.7, S4.1–S4.5, S5.5, S7.1–S7.5."""

    async def test_dispatchers_loop_runs_on_current_thread_and_loop(self) -> None:
        caller_thread = threading.get_ident()
        caller_loop = asyncio.get_running_loop()

        async def check() -> tuple[int, asyncio.AbstractEventLoop]:
            return threading.get_ident(), asyncio.get_running_loop()

        tid, loop = await pc.with_context(pc.Dispatchers.Loop, check)
        self.assertEqual(caller_thread, tid)
        self.assertIs(caller_loop, loop)

    async def test_dispatchers_default_carrier_count_matches_gil_and_env_override(self) -> None:
        """SPEC S3.2, S7.2: 1 carrier on GIL build, os.cpu_count() on 3.15t, env override."""
        expected_default = 1 if sys._is_gil_enabled() else (os.cpu_count() or 1)
        self.assertEqual(expected_default, pc.Dispatchers.Default.carrier_count)

        custom = pc.Dispatchers.create_carrier_dispatcher("custom-pool", carriers=3)
        try:
            self.assertEqual(3, custom.carrier_count)
        finally:
            custom.close()

    async def test_dispatchers_default_runs_on_carrier_and_preserves_contextvars(self) -> None:
        """SPEC S2.8, S4.2, S4.5: carrier thread runs task, preserves ContextVar, awaits across loops."""
        main_tid = threading.get_ident()
        var: contextvars.ContextVar[str] = contextvars.ContextVar("carrier_var", default="none")
        var.set("from-main")

        async def on_default(x: int) -> tuple[int, str, int]:
            await asyncio.sleep(0.005)
            return threading.get_ident(), var.get(), x * 2

        async with pc.Scope() as s:
            d = s.defer(on_default, 21, dispatcher=pc.Dispatchers.Default)
            tid, seen_var, val = await d

        self.assertNotEqual(main_tid, tid)
        self.assertEqual("from-main", seen_var)
        self.assertEqual(42, val)

    async def test_work_stealing_before_first_step_and_pinning_after_first_step(self) -> None:
        """SPEC S7.3: an idle carrier steals a queued task before its first step; a started task never moves."""
        pool = pc.Dispatchers.create_carrier_dispatcher("steal-pool", carriers=2)
        try:
            blocker_started = threading.Event()
            release_blocker = threading.Event()

            async def block_carrier() -> int:
                blocker_started.set()
                release_blocker.wait(timeout=2.0)
                return threading.get_ident()

            async def pinned_across_awaits() -> tuple[int, int]:
                t1 = threading.get_ident()
                await asyncio.sleep(0.01)
                t2 = threading.get_ident()
                return t1, t2

            # Enqueue a task on carrier 0 that blocks its event loop, plus a second task queued on carrier 0
            # before carrier 0 can step it, and verify carrier 1 steals the second task.
            stolen_tid = await pool._probe_pre_first_step_steal(blocker_started, release_blocker)
            self.assertIsNotNone(stolen_tid)

            async with pc.Scope(dispatcher=pool) as s:
                jobs = [s.defer(pinned_across_awaits) for _ in range(6)]
            for job in jobs:
                t1, t2 = job.result()
                self.assertEqual(t1, t2)
        finally:
            pool.close()

    async def test_dispatchers_io_runs_blocking_function_and_supports_run_blocking_call(self) -> None:
        """SPEC S2.4, S3.6, S7.5: blocking_fn runs on an IO thread without blocking the caller loop."""
        main_tid = threading.get_ident()

        def blocking_add(a: int, b: int) -> tuple[int, int]:
            time.sleep(0.01)
            return threading.get_ident(), a + b

        tid1, sum1 = await pc.with_context(pc.Dispatchers.IO, blocking_add, 10, 20)
        tid2, sum2 = await pc.run_blocking_call(blocking_add, 3, 4)
        self.assertNotEqual(main_tid, tid1)
        self.assertNotEqual(main_tid, tid2)
        self.assertEqual(30, sum1)
        self.assertEqual(7, sum2)

    async def test_cancelling_blocking_io_call_stops_wait_and_sets_cancel_token(self) -> None:
        """SPEC S5.5: cancelling with_context(Dispatchers.IO, blocking_fn) stops the wait and sets CancelToken."""
        entered = threading.Event()
        observed_cancelled = threading.Event()

        def cancellable_blocking(cancel_token: pc.CancelToken) -> str:
            entered.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if cancel_token.cancelled():
                    observed_cancelled.set()
                    return "stopped"
                time.sleep(0.005)
            return "timed-out"

        async def caller() -> str:
            return await pc.with_context(pc.Dispatchers.IO, cancellable_blocking)

        async with pc.Scope() as s:
            j = s.launch(caller)
            while not entered.is_set():
                await asyncio.sleep(0.002)
            j.cancel()
            await j.join()

        self.assertTrue(j.cancelled())
        self.assertTrue(observed_cancelled.wait(timeout=1.0))

    async def test_kotlin_event_loop_and_dispatchers_from_kotlin(self) -> None:
        """SPEC S3.3, S3.7, S4.1: KotlinEventLoop posts to Kotlin dispatcher, warns on slow step, refuses selectors."""
        fake_kt = FakeKotlinDispatcher()
        try:
            kt_disp = pc.Dispatchers.from_kotlin(fake_kt, slow_step_ms=20.0)

            async def ui_work() -> int:
                await asyncio.sleep(0.01)
                return threading.get_ident()

            tid = await pc.with_context(kt_disp, ui_work)
            self.assertEqual(fake_kt.thread_id, tid)
            self.assertGreater(fake_kt.dispatch_count, 0)

            # Slow step warning (SPEC S3.3)
            async def slow_ui_step() -> None:
                time.sleep(0.035)

            with self.assertLogs("pythonx.concurrent", level=logging.WARNING) as cm:
                await pc.with_context(kt_disp, slow_ui_step)
            self.assertTrue(any("Main" in msg or "ms" in msg for msg in cm.output))

            # Selector and subprocess APIs raise NotImplementedError (SPEC S4.1)
            loop = kt_disp.loop
            assert isinstance(loop, pc.KotlinEventLoop)
            with self.assertRaises(NotImplementedError):
                loop.add_reader(0, lambda: None)
            with self.assertRaises(NotImplementedError):
                loop.add_writer(0, lambda: None)
            with self.assertRaises(NotImplementedError):
                await loop.subprocess_shell(lambda: asyncio.SubprocessProtocol(), "true")

            # run_blocking on Main raises RuntimeError (SPEC S4.4)
            async def try_run_blocking_on_main() -> None:
                async def inner() -> int:
                    return 1

                pc.run_blocking(inner)

            with self.assertRaises(RuntimeError):
                await pc.with_context(kt_disp, try_run_blocking_on_main)
        finally:
            fake_kt.close()

    async def test_dispatchers_limited_caps_parallelism(self) -> None:
        """SPEC S3.7: Dispatchers.limited(d, n) caps concurrency to n."""
        with self.assertRaises(ValueError):
            pc.Dispatchers.limited(pc.Dispatchers.Default, 0)

        limited = pc.Dispatchers.limited(pc.Dispatchers.Default, 2)
        active = 0
        peak = 0
        lock = threading.Lock()

        async def work() -> None:
            nonlocal active, peak
            with lock:
                active += 1
                if active > peak:
                    peak = active
            await asyncio.sleep(0.02)
            with lock:
                active -= 1

        async with pc.Scope(dispatcher=limited) as s:
            for _ in range(6):
                s.launch(work)

        self.assertLessEqual(peak, 2)
        self.assertGreaterEqual(peak, 1)

    def test_run_blocking_from_sync_code(self) -> None:
        """SPEC S4.4: run_blocking runs a coroutine to completion on a carrier."""
        async def compute(x: int) -> int:
            await asyncio.sleep(0.005)
            return x + 1

        self.assertEqual(10, pc.run_blocking(compute, 9))


if __name__ == "__main__":
    unittest.main()
