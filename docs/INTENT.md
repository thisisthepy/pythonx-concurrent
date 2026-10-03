# Intent

Why `pythonx-concurrent` exists, what it is for, and what it deliberately is not.
`docs/SPEC.md` may not go beyond this file. When the two disagree, this file wins; when this file
and the maintainer's own words disagree, the maintainer's words win.

## Sources

1. **The maintainer's statement**, quoted below in the original Korean.
2. The python-multiplatform record: `docs/INTENT.md`, `docs/SPEC.md` (U-5, T-1, T-2, C-4) and
   `docs/design/upcall.md` §5. These say what the binder does today.
3. External facts, with URLs, in `docs/research.md`.

Anything that is an inference rather than a statement is marked:

> Inferred: confirm with the maintainer.

## 1. What this project is for

The maintainer's words:

> "만약 코루틴 관련해서 사용하는게 조금 파이썬 쪽에서 불편할거라고 예상이 되면 kotlinx.coroutines 처럼
> 하나 만들어도 될거같은데 ... 파이썬 코루틴도 그렇고, 이게 안드로이드같은데서는 코틀린 코루틴을 아마
> 써야 할거고, 파이썬에서 버추얼 스레드를 제공하는걸 해도 좋을거 같고, goroutine처럼 버추얼 쓰레드 위에
> 여러개 코루틴을 배치할 수 있도록 하는거도 지원하면 좋을거같고."

In English: if using coroutines turns out to be awkward from Python, build something like
`kotlinx.coroutines`. Python coroutines matter, and on Android Kotlin coroutines will probably have
to be used. Providing virtual threads from Python would be good. So would placing several
coroutines on top of virtual threads, as goroutines do.

`pythonx-concurrent` is the answer to that. It is a pythonx library, in the spirit of androidx and
kotlinx, where pythonx means Python language extensions. It makes concurrency comfortable in a
Python app that runs on Kotlin Multiplatform (Android, iOS, desktop). It has four jobs:

1. **Bridge** Python `asyncio` and Kotlin coroutines in both directions, so Python awaits Kotlin and
   Kotlin awaits Python.
2. **Structure** concurrent work the way `kotlinx.coroutines` does: scopes, jobs, dispatchers,
   cancellation and timeouts, written in Python.
3. **Place** work on the right thread (UI thread, I/O, CPU) without the user touching Kotlin threads.
4. **Schedule** many lightweight tasks over a few threads, goroutine style, where the platform allows
   it (free-threaded CPython).

## 2. User stories

- As an Android app author writing Python, I call a Kotlin `suspend` function and a Kotlin `Flow`
  from my Python code with `await` and `async for`, and I do not learn Kotlin threading.
- As a Compose-in-Python author (pythonx-compose), I start work from a screen, and it stops when the
  screen leaves. Cancelling the scope cancels the Python task and the Kotlin coroutine under it.
- As a desktop author, I fan out 10,000 I/O-bound tasks and CPU-bound tasks, and the CPU ones use
  every core on free-threaded CPython.
- As a Kotlin author who embeds Python, I `await` a Python coroutine from a Kotlin `suspend` function
  and cancel it with my `Job`.
- As a Python developer who knows `asyncio.TaskGroup` or trio, I recognise the API at once, and an
  exception in one task cancels its siblings the way I expect.
- As a maintainer, I can run the same test suite on desktop, Android and iOS and see which behaviours
  each platform really supports.

## 3. What it is, structurally

- **A pip package** named `pythonx-concurrent`, import package `pythonx.concurrent`. Pure Python at
  first. Inferred: confirm with the maintainer.
- **Real Python code on disk**, like `pythonx-compose`. It imports Kotlin modules under their own
  Kotlin names. It never asks the binder to rename anything.
- **Built on `asyncio`**, not beside it. A `pythonx.concurrent` task is an ordinary awaitable. The
  standard library keeps working inside it.
- **Kotlin vocabulary, Python spelling.** `launch`, `Job`, `Deferred`, `Dispatchers`, `with_context`,
  `with_timeout`, `supervisor_scope`; arguments in `snake_case`.

## 4. How it relates to the other repositories

| Repository | Relationship |
|---|---|
| `python-multiplatform` (the binder) | Provides the interpreter, the downcall and upcall layers, and today's `await` of Kotlin `suspend` functions (SPEC U-5). `pythonx-concurrent` sits on top. Every missing binder feature is listed in SPEC as `needs binder:` and becomes an issue there. |
| `pythonx-compose` | A sibling. Compose screens need scopes tied to composition and a UI-thread dispatcher. `pythonx-concurrent` supplies them. The reverse dependency does not exist. |
| `kotlinx.coroutines` | The model and, on Kotlin's side, the runtime. This repository does not reimplement it. It reaches it through the binder. |
| `asyncio` | The Python-side model and the base of the event loop. |

## 5. What it is not

- **Not a reimplementation of `kotlinx.coroutines`.** Kotlin code keeps using the real one.
- **Not a replacement for `asyncio`.** It adds structure, dispatch and bridging on top.
- **Not sub-interpreters.** Parallelism comes from free-threaded CPython (python-multiplatform
  AGENTS.md §12.8). Per-interpreter GIL was rejected there.
- **Not a Kotlin renamer.** No `androidx`-to-`pythonx` style mapping of Kotlin names lives here or in
  the binder.
- **Not a promise of identical behaviour on every platform.** Where the platform lacks a thing
  (threads on wasm, free-threaded builds on mobile), the feature is absent and says so. It is not
  stubbed.
- **Not blocking-style Python on JVM virtual threads.** A virtual thread is pinned while Python
  runs, so a plain blocking Python function gains nothing from one (`docs/research.md` §6). Python
  coroutines do run on virtual threads and on any Kotlin thread, one step at a time, with Kotlin
  scheduling them (SPEC §7, Kotlin mode). The maintainer asked for that (2026-10-04: "버추얼
  쓰레드까지 붙이거나 필요한 경우 kotlin 스레드 위에서 돌리는 형태").
- **Not a network, file or process library.** It schedules and bridges. I/O stays with `asyncio` and
  Kotlin.

## 6. Open questions

Everything beyond the maintainer's words is here until confirmed.

1. **Kotlin helper module.** Bridging `Flow`, dispatchers and `Job` needs a small amount of Kotlin
   that is not generic. Where does it live? A new Kotlin module in this repository, a module in
   python-multiplatform, or generated by the binder? A new top-level folder needs the maintainer's
   approval (AGENTS.md §2).
2. **Package name for the scope object.** The spec uses `Scope`, `launch` and `defer`. Kotlin's
   `async` is a Python keyword, so a different spelling is needed. Confirm the spelling.
3. **Minimum Python.** The spec assumes 3.11 or later (`TaskGroup`, `ExceptionGroup`). Android and iOS
   builds ship 3.13 or 3.14, so this should hold.
4. **Stackful virtual threads.** Writing blocking-style code on a lightweight thread needs stack
   switching (for example `greenlet`). Whether to support it is undecided and not evaluated on
   mobile.
5. **Reactive streams beyond `Flow`.** `StateFlow` and `SharedFlow` as Python types are in scope only
   if Compose state needs them. Confirm.
6. **Structured concurrency across processes or machines.** Out of scope unless asked.
