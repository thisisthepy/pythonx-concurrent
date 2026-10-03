# Specification

What `pythonx-concurrent` will do: the behavioural contract. Every item stays inside
`docs/INTENT.md`. Anything that INTENT does not cover is under "Outside intent: needs a decision".

Each item carries a status:

| Status | Meaning |
|---|---|
| `implemented` | Behaviour exists in this repository and a test that was read for this document passes. |
| `partial` | Some of it exists, or its only evidence is outside this repository. |
| `planned` | Required by INTENT; nothing delivers it yet. |

Every entry below is `planned`. Issue markers read `Issue: to be opened`; the coordinator opens them.
A line starting `needs binder:` is a request to python-multiplatform. It becomes an issue there and
is collected in section 12.

A behaviour change starts here, then becomes a failing test, then code (`AGENTS.md` §5).

Examples install with uv, once the package is published:

```bash
uv add --prerelease allow pythonx-concurrent
ppp core add "pythonx-concurrent==0.1.0a1"
tcl install pythonx-concurrent
```

## 0. Baseline

Nothing is built. The state this spec starts from is in `docs/research.md` §1: Python can await a
Kotlin `suspend` function (python-multiplatform SPEC U-5); nothing else in this spec exists.

---

## 1. Distribution

### S1.1 Package and import name: `planned`

Distribution `pythonx-concurrent`, import package `pythonx.concurrent`, pure Python, Python 3.11 or
later (for `TaskGroup`, `ExceptionGroup`, `asyncio.timeout`). Reason: those three are the base the
structured API stands on. Issue: to be opened.

### S1.2 No Kotlin renaming, real package on disk: `planned`

`pythonx/concurrent/` is ordinary source. It imports Kotlin modules under their Kotlin names. It does
not synthesise modules and does not set `__path__ = []`. Reason: the `pythonx` package is shared with
`pythonx-compose` and must stay importable from disk. Issue: to be opened.

---

## 2. API shape

### Recommendation

**A scope object used with `async with`, in the style of `asyncio.TaskGroup`, carrying Kotlin's
vocabulary (`launch`, `defer`, `Job`, `Deferred`), plus a function `with_context(...)` to switch
dispatcher.** Not the `launch`/`async` free functions of Kotlin.

Reasons:

1. **Python has no implicit scope.** Kotlin's `launch` is a method on the `CoroutineScope` receiver of
   the enclosing lambda. A Python function has no such receiver. A module-level `launch()` would need
   a hidden `ContextVar`, and a forgotten scope would silently spawn an unstructured task. trio calls
   this the problem with the `go` statement (`docs/research.md` §4).
2. **Python users already know it.** `asyncio.TaskGroup`, trio nurseries and anyio task groups share
   this shape. The exit of `async with` is the join point, so structure is visible in the indentation.
3. **It maps to Kotlin one to one.** `async with Scope() as s` is `coroutineScope { }`. `s.launch` is
   `launch`. `s.defer` is `async`. `Job` and `Deferred` keep their names and methods.
4. **`with_context` fits as a function.** Kotlin's `withContext(Dispatchers.IO) { }` runs a block in
   another context and returns its value. A Python block cannot change threads in the middle of a
   function, so `with_context` takes a function: `await with_context(Dispatchers.IO, fn, *args)`.

```python
from pythonx.concurrent import Scope, Dispatchers, with_context

async def load(user_id):
    async with Scope() as s:                      # coroutineScope { }
        profile = s.defer(fetch_profile, user_id)  # async { }  -> Deferred
        s.launch(log_visit, user_id)               # launch { } -> Job
        data = await with_context(Dispatchers.IO, read_cache, user_id)
    return profile.result(), data                  # scope exit joined both
```

`async` is a Python keyword, so Kotlin's `async` is spelled `defer`. Open question 2 in INTENT.

### S2.1 `Scope` is an async context manager: `planned`

`async with Scope(...) as s:` creates a scope. Leaving the block waits for every child. A child
failure cancels the other children and the body, then raises after all have finished.
`Scope` is built on `asyncio.TaskGroup`'s rules and is usable in any running loop. Issue: to be opened.

### S2.2 `launch` returns a `Job`: `planned`

`s.launch(fn, *args, dispatcher=None, name=None) -> Job`. `fn` is a coroutine function. `Job` offers
`cancel(msg=None)`, `cancelled()`, `done()`, `join()` (awaitable), `is_active()`, `exception()`.
A `Job` cannot be created outside a scope. Reason: no unstructured spawn. Issue: to be opened.

### S2.3 `defer` returns a `Deferred`: `planned`

`s.defer(fn, *args, dispatcher=None, name=None) -> Deferred`. `Deferred` is a `Job` plus `await d`,
`d.result()` (raises if the job failed or was cancelled) and `await d.await_()`. Issue: to be opened.

### S2.4 `with_context` switches context for one call: `planned`

`await with_context(dispatcher, fn, *args)` runs `fn` on `dispatcher` and returns its result. If the
dispatcher is the current one it calls `fn` directly. Exceptions and cancellation pass through
unchanged. `fn` may be a coroutine function or, for `Dispatchers.IO`, a plain blocking function
(S3.6). Issue: to be opened.

### S2.5 `supervisor_scope` and `Scope(supervisor=True)`: `planned`

A supervisor scope does not cancel siblings when one child fails. Failures are kept on the `Job`. It
is the Kotlin `supervisorScope`. `asyncio.TaskGroup` has no equivalent. Issue: to be opened.

### S2.6 `with_timeout` and `with_timeout_or_none`: `planned`

`async with with_timeout(seconds):` wraps `asyncio.timeout`. On expiry it raises the built-in
`TimeoutError`. `with_timeout_or_none` yields a flag instead of raising, as Kotlin's
`withTimeoutOrNull`. Issue: to be opened.

### S2.7 Structured-only guarantee: `planned`

No public function creates a task that outlives its scope. A global scope (`GlobalScope`) is not
offered. A user who needs a long-lived scope creates one at app level and closes it explicitly.
Reason: leaked tasks were the problem structured concurrency removes. Issue: to be opened.

### S2.8 Context variables: `planned`

A child inherits a copy of the parent's `contextvars.Context`. `with_context` keeps it. On the
free-threaded build, `sys.flags.thread_inherit_context` defaults to `True`; the spec does not rely on
it, because a carrier thread (S7) starts tasks through `Context.run` with the parent's copy.
Issue: to be opened.

---

## 3. Dispatchers

A dispatcher decides which thread, and so which event loop, runs a Python coroutine or a plain
function. Dispatchers are Python objects. Each wraps a Kotlin `CoroutineDispatcher` where one exists.

### S3.1 Names and meaning: `planned`

`Dispatchers.Main`, `Dispatchers.IO`, `Dispatchers.Default`, `Dispatchers.Unconfined`, and
`Dispatchers.Loop` (the loop already running, the one `asyncio.run` made). Meaning follows
`kotlinx.coroutines` (`docs/research.md` §2), except as S3.3 to S3.6 say. Issue: to be opened.

### S3.2 Mapping per platform: `planned`

| Dispatcher | Desktop JVM | Android | iOS | wasm |
|---|---|---|---|---|
| `Main` | the UI toolkit thread (Swing/AWT event thread under Compose Desktop) | the main looper | the main dispatch queue | the one thread |
| `IO` | Kotlin `Dispatchers.IO` threads, each running an asyncio loop on demand (S3.6) | same | same | absent: raises `NotImplementedError` |
| `Default` | one carrier loop per core on 3.14t, one carrier on a GIL build (S7.2) | same | same | the one thread |
| `Unconfined` | resumes on whatever thread resumed it | same | same | the one thread |
| `Loop` | the current loop | same | same | the one loop |

Reason `Default` is one carrier on a GIL build: extra threads add no CPU parallelism there, and they
cost memory and context switches. Issue: to be opened.

### S3.3 `Main` runs Python on the UI thread: `planned`

A coroutine launched on `Main` runs inside an event loop whose scheduling is driven by the Kotlin main
dispatcher (S4.1). Python code on `Main` must not block. The library cannot enforce this; it can log a
warning when one step takes longer than a threshold (default 100 ms, configurable). Reason: the GIL
and the UI share the same thread there, so a blocking step freezes the screen. Issue: to be opened.

### S3.4 `Default` is for CPU work: `planned`

Tasks that compute without awaiting belong here. On a GIL build they do not run in parallel, and the
documentation says so beside the dispatcher. Issue: to be opened.

### S3.5 Selecting a dispatcher: `planned`

`dispatcher=` on `launch`/`defer` and `with_context` select it. A child inherits its parent's
dispatcher unless it names one, as in Kotlin. Issue: to be opened.

### S3.6 `IO` accepts plain blocking functions: `planned`

`await with_context(Dispatchers.IO, blocking_fn, *args)` runs a normal function on an `IO` thread
and returns the value. It is the Kotlin-flavoured `asyncio.to_thread`. A blocking call releases the
GIL in CPython, so this is parallel even on a GIL build. Cancellation of the awaiting task cancels the
wait, not the thread (a thread cannot be killed; S5.5). Issue: to be opened.

### S3.7 `Dispatchers` is extensible: `planned`

`Dispatchers.from_kotlin(dispatcher)` wraps any Kotlin `CoroutineDispatcher`. `Dispatchers.limited(d, n)`
mirrors `limitedParallelism`. needs binder: a Kotlin `CoroutineDispatcher` must be passable from
Python as an opaque object (S12, item 4). Issue: to be opened.

---

## 4. asyncio interop

### S4.1 An event loop driven by Kotlin: `planned`

`KotlinEventLoop` implements `asyncio.AbstractEventLoop` for the threads whose scheduling belongs to a
Kotlin dispatcher (`Main`). `call_soon`, `call_soon_threadsafe` and `call_at/later` post to the
Kotlin dispatcher (timers use Kotlin's `delay`). The loop does not own a `selectors` selector.
`add_reader`, `add_writer` and subprocess APIs raise `NotImplementedError` on it. Reason: the UI thread
already has a message loop, and a second blocking loop on it is impossible. Network I/O belongs on
`IO` loops (S4.2). needs binder: Python callable invokable from a Kotlin posted `Runnable`
(S12, item 5). Issue: to be opened.

### S4.2 Carrier loops: `planned`

A carrier is a thread running a standard `asyncio` loop (`new_event_loop().run_forever()`), with
selector support. `IO`, `Default` and S7 use carriers. A carrier starts lazily and stops when its
dispatcher closes. On Android a carrier thread is a thread CPython created, so ART attach and detach
follow python-multiplatform SPEC C-5. Issue: to be opened.

### S4.3 Using the host loop: `planned`

If a loop is already running when `Scope` is entered, `Dispatchers.Loop` is that loop and nothing is
created. `Scope` works under plain `asyncio.run` with no Kotlin present, so tests run on a normal
interpreter. Issue: to be opened.

### S4.4 Running a coroutine from non-async code: `planned`

`run_blocking(fn, *args)` runs a coroutine to completion on a carrier and blocks the caller, as
`runBlocking`. It raises `RuntimeError` on `Main`, because that would deadlock the UI loop. Issue:
to be opened.

### S4.5 Awaiting across loops: `planned`

A task on loop A can `await` a `Job` or `Deferred` that runs on loop B. The result crosses with
`call_soon_threadsafe`. Direct `await` of a bare `asyncio.Future` of another loop is an error, as
asyncio defines it. Issue: to be opened.

---

## 5. Cancellation

### S5.1 Python cancels Kotlin: `planned`

Cancelling a `Job` that is awaiting a Kotlin `suspend` call cancels the Kotlin `Job` of that call. A
Kotlin body suspended in `delay()` or any `kotlinx.coroutines` suspension point stops at once.
Today the binder only gives cooperative `ensureActive()` stops (`docs/research.md` §1). needs binder:
parent `Job` in the context of an exposed `suspend` call (S12, item 1). Issue: to be opened.

### S5.2 Kotlin cancels Python: `planned`

Cancelling the Kotlin `Job` that awaits a Python coroutine (S6.4) calls `Task.cancel()` on the
Python side. The Python coroutine sees `CancelledError` at its next `await`. needs binder: the
suspend wrapper itself (S12, item 2). Issue: to be opened.

### S5.3 Cancellation exception mapping: `planned`

| Direction | Mapping |
|---|---|
| Kotlin `CancellationException` into Python | `asyncio.CancelledError` |
| `asyncio.CancelledError` into Kotlin | `CancellationException` |
| Kotlin `TimeoutCancellationException` into Python | built-in `TimeoutError` |
| Python `TimeoutError` from `with_timeout` into Kotlin | `TimeoutCancellationException` |

`CancelledError` stays a `BaseException`, as asyncio defines. The mapping reason: each side's
cancellation must be recognised by the other as a signal, not a failure. needs binder: exception
type mapping hook in the upcall entry (S12, item 6). Issue: to be opened.

### S5.4 No `uncancel` inside a scope-owned task: `planned`

A `Job` that swallows `CancelledError` and continues is a bug, as in Kotlin. `Scope` detects a task
that finishes normally after `cancelling() > 0` and raises `RuntimeError` naming it. Reason: a
Kotlin `Job` cannot be revived, so the two models must agree. Issue: to be opened.

### S5.5 Cancelling a blocking function: `planned`

Cancelling `with_context(Dispatchers.IO, blocking_fn)` stops the wait and marks the job cancelled. The
thread runs `blocking_fn` to its end, because a thread cannot be interrupted safely. If `blocking_fn`
accepts a `CancelToken` argument, it receives one and can poll `token.cancelled()`. Issue: to be opened.

### S5.6 `NonCancellable`: `planned`

`async with non_cancellable():` shields a cleanup block from cancellation, as Kotlin's
`withContext(NonCancellable)`. It wraps `asyncio.shield` and keeps the pending cancel to re-raise on exit.
Issue: to be opened.

### S5.7 Cancellation of the scope: `planned`

`scope.cancel()` cancels every child and then the scope. It is idempotent. A scope tied to a lifecycle
(a Compose screen) calls it when the lifecycle ends. Issue: to be opened.

---

## 6. Kotlin interop

### S6.1 Python awaits a Kotlin `suspend` function: `partial`

Exists in python-multiplatform (U-5, `implemented` on desktop and Kotlin/Native). `pythonx-concurrent`
adds: dispatcher choice (the call is made through `with_context`), and a parent `Job` (S5.1). Today
the call needs a running loop on the calling thread. This spec makes the call legal from any
dispatcher. Status stays `partial` for this repository because nothing here is built yet. Issue: to be
opened.

### S6.2 Awaiting on a chosen Kotlin dispatcher: `planned`

`await with_context(Dispatchers.from_kotlin(d), kotlin_fn, *args)` resumes the Kotlin body on `d`
and the Python caller on its own loop. needs binder: start the call's coroutine with a given
dispatcher (S12, item 1). Issue: to be opened.

### S6.3 `Flow` in Python: `planned`

`async for x in flow:` collects a Kotlin `Flow`. Breaking out of the loop or cancelling the task
cancels the collection. Back-pressure: the collector asks for one item at a time (a rendezvous
channel), so a slow Python consumer slows the producer. `StateFlow` also offers `.value`.
needs binder: `Flow` is generic, and generic declarations are not exposed (S12, item 3). Issue: to
be opened.

### S6.4 Kotlin awaits a Python coroutine: `planned`

A Kotlin suspend function `PyAwaitable.await(): T` takes a Python awaitable, submits it to a Python
loop (`run_coroutine_threadsafe`), suspends, and resumes when the Python task finishes. The result
is converted by the binder's marshalling. needs binder: the suspend wrapper, a thread-safe submit,
and a completion callback that is a Kotlin lambda (S12, items 2 and 5). Issue: to be opened.

### S6.5 Python exposes a coroutine to Kotlin as `suspend`: `planned`

A Python `async def` can be passed where Kotlin expects a `suspend () -> T`. This is S6.4 packaged as a
function type. The binder rejects `suspend` function types today (`docs/research.md` §1). needs
binder: suspend function type support (S12, item 7). Issue: to be opened.

### S6.6 Fast path preserved: `planned`

A Kotlin call that never suspends returns its value with no Future (binder behaviour). A Python
coroutine that finishes before its first suspension on an eager task completes without a Kotlin
suspension. Reason: cost. Both ends measure it (S10.4). Issue: to be opened.

---

## 7. Virtual threads and M:N scheduling

The word "virtual thread" here means a lightweight task scheduled over a few carrier threads. It is
not a JVM virtual thread, which cannot run Python code usefully (`docs/research.md` §6).

### S7.1 Goroutine-style launch: `planned`

`scope.launch(fn)` on a multi-carrier dispatcher places the task on one carrier. Many tasks per
carrier is the normal case. Placement happens at launch, to the least-loaded carrier. Issue: to be
opened.

### S7.2 Carrier count: `planned`

`Dispatchers.Default` has `os.cpu_count()` carriers when the interpreter is free-threaded
(`sys._is_gil_enabled()` is false) and one carrier otherwise. `PYTHONX_CONCURRENT_CARRIERS` overrides it.
Reason: S3.2. Issue: to be opened.

### S7.3 Work stealing only before the first step: `planned`

An idle carrier may take a task that has not started from a busy carrier's queue. A started task
never moves. Reason: asyncio tasks are bound to their loop (`docs/research.md` §7). Issue: to be
opened.

### S7.4 Cooperative, not preemptive: `planned`

A task that computes without `await` holds its carrier. The documentation says so. `await
sleep(0)` is the yield point. Issue: to be opened.

### S7.5 Blocking calls leave the carrier: `planned`

`run_blocking_call(fn)` inside a task moves `fn` to an `IO` thread so the carrier keeps scheduling
other tasks, as Go hands off a P. It is `with_context(Dispatchers.IO, fn)` with a shorter name.
Issue: to be opened.

### S7.6 Free-threaded build is the parallel path: `planned`

Parallel execution of Python code needs CPython 3.14t (python-multiplatform SPEC T-2, desktop,
opt-in). On GIL builds the API behaves identically but concurrent, not parallel. needs binder: a
free-threaded prebuilt for Android and iOS before parallelism exists there (S12, item 8). Issue:
to be opened.

### S7.7 Per-platform availability: `planned`

| Platform | Carrier threads | Parallel Python | Stackful virtual threads |
|---|---|---|---|
| Desktop | yes | 3.14t only | not evaluated |
| Android | yes | no | not evaluated |
| iOS | yes | no | not evaluated |
| wasm | no | no | no |

On wasm, `Default` and `IO` raise `NotImplementedError` and `Main` is the single loop. Issue: to be
opened.

### S7.8 JVM virtual threads are optional, Kotlin side only: `planned`

On desktop with JDK 21 or later, a Kotlin helper may back `Dispatchers.IO`'s blocking work with a
virtual-thread executor. It never wraps Python frames. It is off by default and gated by the JDK
version at run time. Reason: the desktop JDK is not fixed (python-multiplatform AGENTS.md §16). Issue:
to be opened.

---

## 8. Errors

### S8.1 Multiple failures raise `ExceptionGroup`: `planned`

When several children of a scope fail, the scope raises an `ExceptionGroup` (as `asyncio.TaskGroup`).
Kotlin's "first exception plus suppressed" is mapped: the first failure is the group's first member,
and the rest follow. A scope with exactly one failure raises an `ExceptionGroup` of one, so `except*`
always works. Reason: one rule is easier to teach than two. Issue: to be opened.

### S8.2 Kotlin exceptions in Python keep their cause: `planned`

A Kotlin exception that reaches a Python scope is raised as the binder's exception type, with the
original Kotlin throwable reachable as `__cause__` when the binder provides it. needs binder: expose
the Kotlin throwable on the Python exception (S12, item 6). Issue: to be opened.

### S8.3 Python exceptions in Kotlin: `planned`

A Python exception that fails a Python coroutine awaited from Kotlin (S6.4) is thrown as the binder's
`PyException`, with the Python traceback in its message. Issue: to be opened.

### S8.4 Exceptions in `launch` jobs are never lost: `planned`

A failed `Job` that nobody joined still fails its scope at exit. There is no "exception was never
retrieved" log. Issue: to be opened.

---

## 9. Thread safety and the GIL

### S9.1 No reliance on the GIL for atomicity: `planned`

State shared between carriers (the job table, queues, counters) is guarded by explicit locks or by
single-owner loops. Compound operations are never assumed atomic. Reason: free-threaded CPython
removes that assumption (`docs/research.md` §5). Issue: to be opened.

### S9.2 Cross-thread calls go through `call_soon_threadsafe`: `planned`

Nothing touches a loop, task or future from a foreign thread except through
`call_soon_threadsafe` or `run_coroutine_threadsafe`. Issue: to be opened.

### S9.3 Pure Python, no GIL re-enable: `planned`

The package ships no C extension, so importing it does not re-enable the GIL on a free-threaded
build. Issue: to be opened.

### S9.4 GIL held when resuming Kotlin continuations: `planned`

Any code that resumes a parked Kotlin continuation holds the GIL, as python-multiplatform requires
(`commonMain/README.md`). Kotlin-side helpers use the binder's `withGIL`. Issue: to be opened.

### S9.5 Releasing the GIL while Kotlin runs: `planned`

A Python call that waits for Kotlin must not hold the GIL across the wait. needs binder: GIL release
after initialisation is not enabled today (SPEC C-4) (S12, item 9). Issue: to be opened.

### S9.6 Foreign threads: `planned`

A Kotlin thread that completes a Python future attaches to CPython once per thread (binder SPEC C-5
on Android; `withGIL` elsewhere). On iOS the completing thread is a Kotlin/Native thread, so the
helper must run on a worker thread the runtime knows. Issue: to be opened.

---

## 10. Testing strategy

### S10.1 Pure-Python layer first: `planned`

Scope, Job, Deferred, cancellation, timeouts and the carrier scheduler are tested on a plain
interpreter with a fake Kotlin side (`FakeDispatcher`, `FakeSuspend`). No binder is needed. Run on
CPython 3.11 to 3.14 and on 3.14t:

```bash
uv run --python 3.14 --with pytest pytest tests -q
uv run --python 3.14t --with pytest pytest tests -q
```

### S10.2 Red first, with a cause: `planned`

Each entry here gets a test that fails before the code exists. A test says in its name whether it
expects "not implemented yet" (red) or guards a regression, so a failure can be classified
(`AGENTS.md` §5, python-multiplatform §14).

### S10.3 Cancellation and race tests: `planned`

Stress tests cancel at every `await` point of a small scope (a "cancel at step N" sweep). Free-threaded
tests run with `PYTHON_GIL=0` and many carriers. A virtual-time loop makes timeout tests
deterministic.

### S10.4 Measurement tests: `planned`

Record, do not assert: cost of a Python-to-Kotlin await round trip, a Kotlin-to-Python await, a task
launch, a cross-carrier hop, and `Flow` per-item cost. Run alone and unfiltered after checking `uptime`
(`AGENTS.md` §8). Numbers carry their environment.

### S10.5 Binder integration tests: `planned`

After the pure layer: desktop JVM against python-multiplatform fixtures, one module per invocation.
Then Android and iOS emulators, one at a time. Each platform reports which entries pass, in the
table of S7.7 and S3.2. A skip is not a pass.

### S10.6 Disable-and-fail check: `planned`

For each public path, a test disables it and confirms something fails (`AGENTS.md` §8). Issue: to be
opened.

### S10.7 GraalVM native image: `planned`

Anything the Kotlin helper exposes must work in a native image (python-multiplatform §13). No
reflection in the helper. Issue: to be opened.

---

## 11. Outside intent: needs a decision

These appeared during research and INTENT does not cover them.

1. A Kotlin helper module and where it lives (INTENT open question 1).
2. Stackful virtual threads via `greenlet`.
3. `StateFlow` and `SharedFlow` as first-class Python types beyond collection.
4. A structured `Channel` / `Select` type (Go's channels).
5. Integration with `anyio`, so anyio code runs on these dispatchers.

## 12. Needs binder: list for python-multiplatform issues

1. **Parent `Job` and dispatcher for exposed `suspend` calls.** The call's coroutine context today is
   only the `PendingCall`. Request: let the caller supply a `CoroutineContext` (a `Job` and a
   dispatcher) without adding `kotlinx.coroutines` to the binder's own dependencies. Serves S5.1,
   S6.1, S6.2.
2. **A `suspend` wrapper over a Python awaitable** (`await()` in Kotlin), with submit to a loop from any
   thread and cancellation to `Task.cancel()`. Serves S5.2, S6.4.
3. **`Flow` exposure.** Generic declarations are not exposed (SPEC U-4). Request: a path for
   `Flow<T>` and `StateFlow<T>` to be collected from Python. Serves S6.3.
4. **Opaque Kotlin objects as arguments**, so a `CoroutineDispatcher` or `Job` can be passed from
   Python to Kotlin. Serves S3.7.
5. **A Python callable as a Kotlin function or `Runnable`**, invokable from any Kotlin thread with
   the GIL taken by the binder. Serves S4.1, S6.4.
6. **Exception mapping**: Kotlin `CancellationException` to `CancelledError` and back, and the Kotlin
   throwable kept as `__cause__`. Serves S5.3, S8.2.
7. **`suspend` function types** in exposed signatures (rejected today with a KSP warning). Serves
   S6.5.
8. **Free-threaded CPython prebuilts for Android and iOS.** Only desktop has them (SPEC T-2). Serves
   S7.6.
9. **GIL release after initialisation** (SPEC C-4 says not enabled), and tests of GIL behaviour beyond
   "does not crash". Serves S9.5.
10. **Running asyncio when the calling thread has no loop**: the binder's slow path fails with
    `no running event loop` and drops the resumption. Request: a supported way to name the loop the
    result goes to. Serves S6.1.
11. **iOS completion thread**: documented, supported way to resume a continuation from a Kotlin/Native
    thread (ROADMAP 13.3). Serves S9.6.
12. **wasm threads** are out of scope here; noted only because S7.7 depends on them.
