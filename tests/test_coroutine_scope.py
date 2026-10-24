"""App-level CoroutineScope(dispatcher) tests (SPEC S2.9, S8.1, S8.4).

Covers launching from a thread with no running loop, cancel(), join(), failure propagation,
sys.unraisablehook reporting for unjoined failures, and supervisor=True.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from typing import Any

import pythonx.concurrent as pc


class CoroutineScopeTests(unittest.IsolatedAsyncioTestCase):
    """SPEC S2.9."""

    async def test_launch_from_thread_with_no_running_loop_and_join(self) -> None:
        scope = pc.CoroutineScope(pc.Dispatchers.Default)
        results: list[int] = []
        done_event = threading.Event()

        async def job_fn(v: int) -> int:
            await asyncio.sleep(0.01)
            results.append(v)
            done_event.set()
            return v * 10

        holders: list[pc.Deferred[int]] = []

        def non_async_caller() -> None:
            # No event loop running on this thread (like a UI button click callback).
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            d = scope.defer(job_fn, 7)
            holders.append(d)

        t = threading.Thread(target=non_async_caller)
        t.start()
        t.join(timeout=2.0)

        self.assertTrue(done_event.wait(timeout=2.0))
        await scope.join()
        self.assertEqual([7], results)
        self.assertEqual(70, await holders[0])
        scope.cancel()

    async def test_cancel_is_idempotent_and_refuses_subsequent_launch(self) -> None:
        scope = pc.CoroutineScope(pc.Dispatchers.Default)
        started = threading.Event()

        async def hang() -> None:
            started.set()
            await asyncio.sleep(10.0)

        job = scope.launch(hang)
        self.assertTrue(started.wait(timeout=2.0))
        scope.cancel()
        scope.cancel()
        await job.join()
        self.assertTrue(job.cancelled())

        async def after() -> None:
            pass

        with self.assertRaises(RuntimeError) as ctx:
            scope.launch(after)
        self.assertIn("CoroutineScope", str(ctx.exception))

    async def test_failed_child_cancels_siblings_raises_on_join_and_reports_unraisable_once(self) -> None:
        """SPEC S2.9, S8.1, S8.4: failure cancels siblings, reports once via sys.unraisablehook, and join() raises ExceptionGroup."""
        unraisables: list[Any] = []
        orig_hook = sys.unraisablehook

        def custom_hook(args: Any) -> None:
            unraisables.append(args)

        sibling_cancelled = threading.Event()
        sys.unraisablehook = custom_hook
        try:
            scope = pc.CoroutineScope(pc.Dispatchers.Default)

            async def sibling() -> None:
                try:
                    await asyncio.sleep(10.0)
                except asyncio.CancelledError:
                    sibling_cancelled.set()
                    raise

            async def failing() -> None:
                await asyncio.sleep(0.01)
                raise ValueError("app-scope-boom")

            scope.launch(sibling)
            scope.launch(failing)

            self.assertTrue(sibling_cancelled.wait(timeout=2.0))
            with self.assertRaises(ExceptionGroup) as ctx:
                await scope.join()

            self.assertEqual(1, len(ctx.exception.exceptions))
            self.assertIsInstance(ctx.exception.exceptions[0], ValueError)
            matching = [u for u in unraisables if isinstance(u.exc_value, ValueError)]
            self.assertEqual(1, len(matching))
        finally:
            sys.unraisablehook = orig_hook

    async def test_supervisor_coroutine_scope_keeps_siblings_alive(self) -> None:
        unraisables: list[Any] = []
        orig_hook = sys.unraisablehook
        sys.unraisablehook = lambda args: unraisables.append(args)
        try:
            scope = pc.CoroutineScope(pc.Dispatchers.Default, supervisor=True)

            async def bad() -> None:
                await asyncio.sleep(0.005)
                raise RuntimeError("isolated")

            async def good() -> str:
                await asyncio.sleep(0.02)
                return "survived"

            bad_job = scope.launch(bad)
            good_def = scope.defer(good)
            await scope.join()
            self.assertEqual("survived", good_def.result())
            self.assertIsInstance(bad_job.exception(), RuntimeError)
            scope.cancel()
        finally:
            sys.unraisablehook = orig_hook


if __name__ == "__main__":
    unittest.main()
