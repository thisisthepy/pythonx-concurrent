"""Structured concurrency: Scope, supervisor_scope, Job, Deferred, timeouts, cancellation and errors.

Covers SPEC S2.1, S2.2, S2.3, S2.5, S2.6, S2.7, S2.8, S5.4, S5.6, S5.7, S8.1, S8.4, S10.6.
"""

from __future__ import annotations

import asyncio
import contextvars
import unittest
import warnings

import pythonx.concurrent as pc


class ScopeAndJobTests(unittest.IsolatedAsyncioTestCase):
    """SPEC S2.1, S2.2, S2.3, S2.5, S2.6, S2.7, S2.8, S5.4, S5.6, S5.7, S8.1, S8.4, S10.6."""

    async def test_scope_waits_for_all_launched_and_deferred_children(self) -> None:
        order: list[str] = []

        async def worker(label: str, delay: float) -> str:
            await asyncio.sleep(delay)
            order.append(label)
            return label.upper()

        async with pc.Scope() as s:
            j = s.launch(worker, "a", 0.02, name="job-a")
            d = s.defer(worker, "b", 0.01, name="def-b")
            self.assertTrue(j.is_active())
            self.assertFalse(j.done())
            val = await d
            self.assertEqual("B", val)
            self.assertEqual("B", d.result())
            self.assertEqual("B", await d.await_())

        self.assertTrue(j.done())
        self.assertFalse(j.is_active())
        self.assertFalse(j.cancelled())
        self.assertIsNone(j.exception())
        self.assertEqual(["b", "a"], order)

    async def test_job_and_deferred_cannot_be_instantiated_outside_a_scope(self) -> None:
        """SPEC S2.2, S2.7: no unstructured spawn."""
        with self.assertRaises(TypeError):
            pc.Job()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            pc.Deferred()  # type: ignore[call-arg]
        self.assertFalse(hasattr(pc, "GlobalScope"))
        self.assertFalse(hasattr(pc, "launch"))

    async def test_launch_and_defer_reject_non_coroutine_function(self) -> None:
        def plain() -> int:
            return 1

        async with pc.Scope() as s:
            with self.assertRaises(TypeError):
                s.launch(plain)  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                s.defer(plain)  # type: ignore[arg-type]

    async def test_deferred_result_and_exception_before_done_raise_invalid_state(self) -> None:
        gate = asyncio.Event()

        async def slow() -> int:
            await gate.wait()
            return 42

        async with pc.Scope() as s:
            d = s.defer(slow)
            with self.assertRaises(asyncio.InvalidStateError):
                d.result()
            with self.assertRaises(asyncio.InvalidStateError):
                d.exception()
            gate.set()
        self.assertEqual(42, d.result())

    async def test_child_failure_cancels_siblings_and_raises_exception_group_of_one(self) -> None:
        """SPEC S2.1, S8.1, S8.4: single failure still raises ExceptionGroup so except* works."""
        sibling_cancelled = False

        async def boom() -> None:
            await asyncio.sleep(0.01)
            raise ValueError("bad value")

        async def sibling() -> None:
            nonlocal sibling_cancelled
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise

        caught_values: list[str] = []
        try:
            async with pc.Scope() as s:
                s.launch(sibling)
                s.launch(boom)
        except* ValueError as eg:
            caught_values.extend(str(e) for e in eg.exceptions)

        self.assertTrue(sibling_cancelled)
        self.assertEqual(["bad value"], caught_values)

    async def test_multiple_failures_preserve_first_failure_order_in_exception_group(self) -> None:
        """SPEC S8.1: first failure is the group's first member, and the rest follow."""
        async def first_fail() -> None:
            await asyncio.sleep(0.01)
            raise ValueError("first")

        async def second_fail() -> None:
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                raise RuntimeError("second on cancel") from None

        with self.assertRaises(ExceptionGroup) as ctx:
            async with pc.Scope() as s:
                s.launch(first_fail)
                s.launch(second_fail)

        exc_types = [type(e) for e in ctx.exception.exceptions]
        self.assertEqual([ValueError, RuntimeError], exc_types)

    async def test_supervisor_scope_keeps_failures_on_jobs_without_cancelling_siblings(self) -> None:
        """SPEC S2.5, S8.4: supervisor_scope does not cancel siblings and logs no unretrieved warning."""
        async def failing() -> int:
            await asyncio.sleep(0.01)
            raise KeyError("boom")

        async def healthy() -> int:
            await asyncio.sleep(0.02)
            return 99

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            async with pc.supervisor_scope() as s:
                bad_job = s.launch(failing)
                bad_def = s.defer(failing)
                good_def = s.defer(healthy)

        self.assertEqual(99, good_def.result())
        self.assertIsInstance(bad_job.exception(), KeyError)
        self.assertIsInstance(bad_def.exception(), KeyError)
        with self.assertRaises(KeyError):
            bad_def.result()
        unretrieved = [w for w in caught_warnings if "never retrieved" in str(w.message)]
        self.assertEqual([], unretrieved)

    async def test_contextvars_propagate_to_children_without_leaking_back(self) -> None:
        """SPEC S2.8: child inherits a copy of parent's contextvars.Context."""
        var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="init")
        var.set("parent")

        async def mutate_and_read() -> str:
            before = var.get()
            var.set("child")
            await asyncio.sleep(0)
            return f"{before}->{var.get()}"

        async with pc.Scope() as s:
            d = s.defer(mutate_and_read)

        self.assertEqual("parent->child", d.result())
        self.assertEqual("parent", var.get())

    async def test_no_uncancel_inside_scope_owned_task_raises_runtime_error(self) -> None:
        """SPEC S5.4, S10.6: swallowing CancelledError after cancel request fails with RuntimeError."""
        started = asyncio.Event()

        async def swallow_cancel() -> str:
            started.set()
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                return "swallowed"
            return "normal"

        with self.assertRaises(ExceptionGroup) as ctx:
            async with pc.Scope() as s:
                j = s.launch(swallow_cancel, name="bad-swallower")
                await started.wait()
                j.cancel()

        self.assertEqual(1, len(ctx.exception.exceptions))
        self.assertIsInstance(ctx.exception.exceptions[0], RuntimeError)
        self.assertIn("bad-swallower", str(ctx.exception.exceptions[0]))

    async def test_non_cancellable_shields_cleanup_in_finally_and_restores_cancellation(self) -> None:
        """SPEC S5.6: async with non_cancellable() completes cleanup even after task cancellation."""
        started = asyncio.Event()
        cleaned_up = False

        async def worker() -> None:
            nonlocal cleaned_up
            started.set()
            try:
                await asyncio.sleep(10.0)
            finally:
                async with pc.non_cancellable():
                    await asyncio.sleep(0.01)
                    cleaned_up = True

        async with pc.Scope() as s:
            j = s.launch(worker)
            await started.wait()
            j.cancel()
            await j.join()

        self.assertTrue(cleaned_up)
        self.assertTrue(j.cancelled())

    async def test_non_cancellable_defers_cancel_arriving_mid_block_until_exit(self) -> None:
        """SPEC S5.6: a cancel arriving during non_cancellable() does not interrupt the block, then raises on exit."""
        in_block = asyncio.Event()
        release_block = asyncio.Event()
        completed_inner = False

        async def worker() -> None:
            nonlocal completed_inner
            async with pc.non_cancellable():
                in_block.set()
                await release_block.wait()
                completed_inner = True
            await asyncio.sleep(10.0)

        async with pc.Scope() as s:
            j = s.launch(worker)
            await in_block.wait()
            j.cancel()
            await asyncio.sleep(0.01)
            release_block.set()
            await j.join()

        self.assertTrue(completed_inner)
        self.assertTrue(j.cancelled())

    async def test_scope_cancel_is_idempotent_and_cancels_children(self) -> None:
        """SPEC S5.7: scope.cancel() cancels every child and the scope, idempotently."""
        started = asyncio.Event()

        async def sleeper() -> None:
            started.set()
            await asyncio.sleep(10.0)

        async with pc.Scope() as s:
            j = s.launch(sleeper)
            await started.wait()
            s.cancel()
            s.cancel()

        self.assertTrue(j.cancelled())

    async def test_with_timeout_and_with_timeout_or_none(self) -> None:
        """SPEC S2.6: with_timeout raises TimeoutError; with_timeout_or_none yields a flag."""
        with self.assertRaises(TimeoutError):
            async with pc.with_timeout(0.01):
                await asyncio.sleep(10.0)

        async with pc.with_timeout_or_none(0.01) as timed_out:
            await asyncio.sleep(10.0)
        self.assertTrue(timed_out.expired)
        self.assertFalse(bool(timed_out))

        async with pc.with_timeout_or_none(1.0) as ok:
            await asyncio.sleep(0.005)
        self.assertFalse(ok.expired)
        self.assertTrue(bool(ok))


if __name__ == "__main__":
    unittest.main()
