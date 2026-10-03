# Research

Findings behind `docs/SPEC.md`. Facts are cited. A claim without a source is marked
`(unverified)`. Fetched 2026-10-03.

## 1. What python-multiplatform does today

Read from the python-multiplatform checkout (`develop` at `de9f1fad`).

| Direction | What exists | Source |
|---|---|---|
| Python awaits a Kotlin `suspend fun` | Yes. An exposed `suspend fun` returns an `asyncio.Future`. A body that never suspends returns the plain value and builds no Future. Status `implemented` on desktop and Kotlin/Native, `planned` on wasm. | SPEC U-5; `docs/design/upcall.md` §5.4 to §5.8; `AsyncUpcall.kt` |
| Cancel from Python into Kotlin | Cooperative only. Cancelling the Future reaches `ensureActive()` inside the body through a `PendingCall` context element. | `upcall.md` §5.6; `PendingCall.kt` |
| Dispatcher control | None. The coroutine starts with `startCoroutine` and a context of the `PendingCall` alone, no dispatcher and no `Job`. Work before the first suspension runs on the calling thread, inside the C frame, holding the GIL. | `upcall.md` §5.4 |
| `kotlinx.coroutines` | Not a dependency of the binder. The stdlib `Continuation` is used. | `upcall.md` §5.4; `gradle/libs.versions.toml` lists the library only for Swing tests |
| Kotlin awaits a Python coroutine | Nothing found. No suspend wrapper over a Python awaitable exists. | grep of `commonMain`, `nativeMain`, `jvmMain` |
| `Flow` | Not exposed. Generic declarations are not exposed (U-4). `suspend` function types are rejected with a KSP warning (`upcall.md` §5.3). | SPEC U-4 |
| Needs a running loop | A call that really suspends fails with `no running event loop` when none runs on the calling thread, and its resumption is dropped. | `upcall.md` §5.5 |
| Completion thread | The thread that resumes the continuation takes its own GIL scope with `withGIL`. A dispatcher that resumes an exposed call's continuation must hold the GIL while doing so. | `commonMain/README.md` |
| Free-threaded CPython | 3.14t runs the desktop suite with `-PpythonFreeThreaded=true` (236 tests, 0 failures, as recorded in ROADMAP §9). No Android or iOS prebuilts. Default is the GIL build. | SPEC T-2; `docs/design/threading-and-abi.md` |
| GIL release after init | Not enabled. Only a convention covers "every C API call holds the GIL". | SPEC C-4 |
| Thread attach | Android: a thread CPython creates is attached to ART once and detached when it dies. | SPEC C-5 |
| iOS | The sample cannot show a really-suspending call: resuming a parked continuation needs a Kotlin/Native worker the sample does not start. The library tests do it with a `pthread_create` wrapper and pass on the simulator. | ROADMAP 13.3; `upcall.md` §5.7 |

Consequence. The Python-awaits-Kotlin path exists but is thin. It has no `Job` parent, so a Kotlin
body that suspends in `delay()` does not stop on cancellation unless it calls `ensureActive()` itself
(inferred from "cooperative by construction": the context holds no `Job`, so the library's own
cancellation checks have nothing to observe). The other direction, Flow and dispatchers are missing.

## 2. Kotlin coroutines

- Structured concurrency: `coroutineScope` waits for its children, and a failure cancels the
  parent and the siblings. `launch` returns a `Job`, `async` returns a `Deferred`.
  Source: https://kotlinlang.org/docs/coroutines-basics.html
- Dispatchers: `Default` for CPU work on a shared pool; `IO` for blocking I/O on a shared pool
  (default 64 threads; available on JVM and Native, not JS or Wasm); `Main` for the UI thread
  (declared on all platforms, but it needs a UI framework artifact to work on JVM);
  `Unconfined` runs in the caller frame and resumes wherever the suspending code resumes.
  Source: https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-core/kotlinx.coroutines/-dispatchers/
- Cancellation is cooperative: a coroutine cancels at suspension points and at `ensureActive()`.
  `CancellationException` is the signal and must not be swallowed. `withTimeout` throws
  `TimeoutCancellationException`, a subclass.
  Source: https://kotlinlang.org/docs/cancellation-and-timeouts.html
- `Flow` is a cold asynchronous stream with structured collection; `collect` is a suspend function.
  Source: https://kotlinlang.org/docs/flow.html
- Design background: Roman Elizarov, "Structured concurrency":
  https://elizarov.medium.com/structured-concurrency-722d765aa952

## 3. Python asyncio (3.11 and later)

Source for this section: https://docs.python.org/3/library/asyncio-task.html

- `asyncio.TaskGroup` (3.11): holds strong references to its tasks, waits for all of them at
  `async with` exit, and raises an `ExceptionGroup` when several fail. `KeyboardInterrupt` and
  `SystemExit` are re-raised on their own.
- `CancelledError` derives from `BaseException`, so `except Exception` does not catch it. Code that
  suppresses it must call `Task.uncancel()`. `Task.cancelling()` counts pending cancel requests.
- `asyncio.timeout()` (3.11) raises the built-in `TimeoutError`, can be rescheduled, and reports
  `expired()`.
- `asyncio.to_thread()` runs blocking code in a thread; `run_coroutine_threadsafe()` submits a
  coroutine to a loop from another thread.
- Eager task execution (3.12): a task may run to its first `await` at creation. This matches the
  "does not suspend, no Future" fast path of the binder.
- 3.14 adds `python -m asyncio ps` and `pstree` and `capture_call_graph()` for introspection.
  Source: https://docs.python.org/3/whatsnew/3.14.html

A task and its loop are not thread-safe. Moving a running task to another thread is not supported.
This limits any goroutine-style design (section 7).

## 4. Prior art in Python

- **trio** introduced the nursery and the idea that spawning a task without a scope is the new
  `goto`. Source: https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/
  and https://trio.readthedocs.io/en/stable/reference-core.html
- **anyio** gives trio-style task groups and cancel scopes over asyncio and trio. Its docs note that
  its `TaskGroup` differs from `asyncio.TaskGroup` in cancellation semantics, and that it raises an
  `ExceptionGroup`. Source: https://anyio.readthedocs.io/en/stable/tasks.html
- `asyncio.TaskGroup` itself came from this line of work.

Takeaway: scopes are the accepted Python shape. `pythonx-concurrent` adds what none of them has:
dispatchers, a `Job` handle with Kotlin semantics, and the Kotlin bridge.

## 5. Free-threaded CPython

- 3.13 introduced the experimental free-threaded build. 3.14 made it officially supported but
  optional (PEP 779). Single-thread overhead is about 5 to 10 percent in 3.14.
  Sources: https://docs.python.org/3/whatsnew/3.14.html and https://peps.python.org/pep-0779/
- Caveats: a C extension not marked free-threading compatible re-enables the GIL; iterators are not
  thread-safe; `frame.f_locals` from another thread is unsafe; memory use is higher.
  Source: https://docs.python.org/3/howto/free-threading-python.html (the page describes the build
  as "experimental"; the What's New page for the same release says "officially supported". Both are
  quoted as fetched. PEP 779 is the decision text.)
- Python 3.14 provides binary releases for Android (What's New). iOS support is PEP 730 (not
  re-checked here).
- In this ecosystem, only desktop has free-threaded prebuilts (SPEC T-2).
- Whether `asyncio` in 3.14t is thread-safe across several loops, each on its own thread, is
  `(unverified)` here. The spec's tests must prove it before the scheduler depends on it.

## 6. Virtual threads

- Java 21 virtual threads: instances of `Thread` scheduled by the JVM onto carrier threads in a
  `ForkJoinPool`. They suit blocking I/O at high concurrency. "Not faster threads."
  Source: https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html and
  https://openjdk.org/jeps/444 (JEP 444, not re-fetched).
- **Pinning.** A virtual thread cannot unmount while a native method or foreign function is on its
  stack. `synchronized` pinning was removed in Java 24; native frames still pin.
  Source: https://openjdk.org/jeps/491
- **Consequence for Python.** Every Python call from Kotlin goes through an FFM downcall on desktop.
  A virtual thread inside that call is pinned to its carrier for as long as the Python code runs,
  and a Python callback that blocks holds the carrier. A JVM virtual thread therefore does not turn
  blocking-style Python into cheap threads. See the next point for what it can do.
- **Stepping escapes pinning.** Pinning lasts only while a native frame is on the stack. A Python
  coroutine driven one `send()` at a time from Kotlin holds a native frame only during the step; while
  it waits on a Kotlin `suspend` call the virtual thread holds none and unmounts normally. So Python
  coroutines can live on virtual threads; blocking-style Python cannot.
- **Thread state versus `ThreadLocal`.** `PyGILState_Ensure` stores the thread state per OS thread.
  python-multiplatform's `withGIL` keeps its nesting depth in a Java `ThreadLocal`
  (`jvmMain/.../GILScope.jvm.kt`), which is per virtual thread. If a virtual thread unmounts inside
  `withGIL`, the two disagree: the carrier still holds the GIL and the thread state, and the virtual
  thread may release them later from another carrier. Read from the source, not observed in a run.
- **Desktop JDK version.** python-multiplatform requires desktop FFI not to depend on one JDK version
  (AGENTS.md §16). Virtual threads need 21 or later, so they can only be an optional optimisation.
- **Android.** The `java.lang.Thread` reference page for Android lists no virtual thread API
  (https://developer.android.com/reference/java/lang/Thread). That is absence from one page, not a
  statement from Google. Treat Android virtual threads as unavailable. `(unverified)` beyond that page.
- **Kotlin/Native.** No virtual threads. Kotlin/Native threads are OS threads over a shared heap with
  a tracing GC. Source: https://kotlinlang.org/docs/native-memory-manager.html
- **wasm.** No threads in the supported setup (python-multiplatform SPEC U-5 says `planned` on wasm
  because there are no threads).

## 7. Goroutines and M:N scheduling

- Go's scheduler matches **G** (goroutine), **M** (OS thread) and **P** (processor: scheduler
  state for running Go code, `GOMAXPROCS` of them). An M that enters a system call hands back its P.
  Source: https://go.dev/src/runtime/HACKING
- Goroutines are stackful and the runtime can move a runnable goroutine to another M, because the
  runtime controls the stack. Work stealing between Ps relies on that.
- **What carries over to Python.** An `asyncio` task is stackless. It is bound to the loop that
  created it, and loops are not thread-safe. So the available mapping is:

  | Go | `pythonx-concurrent` |
  |---|---|
  | G | a task (a Python coroutine) |
  | M | a carrier thread |
  | P | the carrier's event loop and run queue |
  | `go f()` | `scope.launch(f)` |
  | work stealing | **only for tasks that have not started.** After the first step a task stays on its loop. |
  | blocking syscall hands off P | `run_blocking()` moves the call to a worker thread so the loop keeps running |

  This is M:N for tasks that cooperate (they `await`). It is not preemptive. A task that computes
  without awaiting holds its loop. On a GIL build, extra carrier threads add no CPU parallelism.

## 8. Feasibility per platform

| Capability | Desktop JVM | Android (ART) | iOS (Kotlin/Native) | wasm |
|---|---|---|---|---|
| Python awaits Kotlin `suspend` | works today (U-5) | works today, device-tested in the sample | works in library tests; sample needs a worker | planned, no threads |
| Kotlin awaits Python coroutine | feasible, needs binder work | feasible, needs binder work | feasible, needs binder work | not feasible without threads, single-loop design only |
| Dispatchers.Main for Python tasks | feasible (needs a UI artifact such as Swing/Compose) | feasible (Android main looper) | feasible (main queue) `(unverified, to be tested)` | the one thread |
| Dispatchers.IO / Default for Python tasks | feasible | feasible | feasible (K/N has both) | absent |
| Several asyncio loops on carrier threads | feasible | feasible | feasible | no |
| Real CPU parallelism of Python code | only on 3.14t (opt-in) | no free-threaded prebuilt | no free-threaded prebuilt | no |
| JVM virtual threads | JDK 21+; Python coroutines stepped from Kotlin, never blocking-style Python (SPEC S7.12) | not available | not applicable | not applicable |
| Goroutine-style tasks over carriers | feasible; parallel on 3.14t, concurrent only on the GIL build | feasible, concurrent only | feasible, concurrent only | no |
| Stackful virtual threads (blocking style) | `(unverified)` via greenlet | `(unverified)` | `(unverified)` | no |
| Free-threaded safety work | needed | needed if a prebuilt appears | needed if a prebuilt appears | none |

## 9. What is hard or impossible

1. **JVM virtual threads cannot host blocking-style Python** (pinning in a foreign call). They can host
   Python coroutines stepped from Kotlin (§6, "Stepping escapes pinning"). Android and Kotlin/Native
   have none, so there the same stepping runs on ordinary Kotlin dispatcher threads.
2. **Preemption is impossible.** `asyncio` is cooperative. CPU-bound tasks need a carrier thread of
   their own, which helps only on a free-threaded build.
3. **Moving a started task between threads is impossible** with `asyncio` tasks. Stealing is
   limited to unstarted tasks.
4. **No parallelism on Android and iOS today**, because no free-threaded prebuilt exists there
   (SPEC T-2). Threads still help with blocking calls, because blocking calls release the GIL.
5. **UI-thread safety.** Python on the main thread holds the GIL. A long Python step on a GIL build
   blocks other Python threads, and blocking the main thread janks the UI.
6. **Kotlin `Flow` is generic.** The binder exposes no generic declarations today (U-4). A typed
   bridge needs binder support or a hand-written Kotlin helper.
7. **Cancellation is not symmetric.** Kotlin's cancelled `Job` cannot be revived. Python's
   `uncancel()` can. The spec forbids `uncancel` inside a scope-owned task (SPEC S5.4).
8. **iOS completion threads.** Resuming a parked continuation needs a Kotlin/Native thread setup the
   sample lacks (ROADMAP 13.3). The library tests show the library itself can do it.
9. **Free-threaded `asyncio` across loops** is not yet verified for this stack.
