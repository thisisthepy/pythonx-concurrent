English | [한국어](https://github.com/thisisthepy/pythonx-concurrent/blob/main/docs/locale/README_ko.md)

# pythonx-concurrent

**Comfortable concurrency for Python apps on Kotlin Multiplatform: asyncio and Kotlin coroutines, together.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-7c4dff.svg)](https://github.com/thisisthepy/pythonx-concurrent/blob/main/LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-blue.svg)](#status)

## Why

Python apps that run on Android, iOS and desktop through
[python-multiplatform](https://github.com/thisisthepy/python-multiplatform) meet two worlds of
concurrency. Python has `asyncio`. Kotlin has coroutines, and on Android you will use them.
`pythonx-concurrent` is a pythonx library, Python language extensions in the spirit of androidx and
kotlinx. It joins the two worlds and gives Python the structure `kotlinx.coroutines` gives Kotlin.

## What it does

- **Scopes and jobs**, spelled like `asyncio.TaskGroup`, named like Kotlin: `Scope`,
  `supervisor_scope`, `CoroutineScope`, `launch`, `defer`, `Job`, `Deferred`, `with_context`,
  `with_timeout`, `with_timeout_or_none`, `non_cancellable`.
- **Dispatchers**: `Main`, `IO`, `Default`, `Unconfined` and `Loop`, plus `Dispatchers.from_kotlin`
  and `Dispatchers.limited`.
- **Both-way bridge**: Python awaits Kotlin `suspend` functions and `Flow` (planned). Kotlin awaits
  Python coroutines (planned).
- **Goroutine-style scheduling** of lightweight tasks over carrier loops with pre-first-step work
  stealing, parallel on free-threaded CPython 3.15t.

```python
from pythonx.concurrent import Scope, Dispatchers, with_context

async def load(user_id):
    async with Scope() as s:
        profile = s.defer(fetch_profile, user_id)
        data = await with_context(Dispatchers.IO, read_cache, user_id)
    return profile.result(), data
```

## Install

```bash
uv add --prerelease allow pythonx-concurrent
ppp core add "pythonx-concurrent==0.0.1a1"
tcl install pythonx-concurrent
```

## Status

Milestone 1 is implemented and tested on CPython 3.15 and free-threaded CPython 3.15t (`Scope`,
`supervisor_scope`, `CoroutineScope`, `Job`, `Deferred`, `Dispatchers`, `KotlinEventLoop`,
`with_context`, `run_blocking`, `run_blocking_call`, `with_timeout`, `with_timeout_or_none`,
`non_cancellable`, `CancelToken`). Features that need new hooks in `python-multiplatform` (`Flow`,
Kotlin-mode stepping, parent `Job` propagation) are tracked as issues there.

## License

Apache-2.0.
