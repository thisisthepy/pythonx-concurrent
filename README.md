English | [한국어](https://github.com/thisisthepy/pythonx-concurrent/blob/main/docs/locale/README_ko.md)

# pythonx-concurrent

**Comfortable concurrency for Python apps on Kotlin Multiplatform: asyncio and Kotlin coroutines, together.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-7c4dff.svg)](https://github.com/thisisthepy/pythonx-concurrent/blob/main/LICENSE)
[![Status](https://img.shields.io/badge/status-spec%20stage-lightgrey.svg)](#status)

## Why

Python apps that run on Android, iOS and desktop through
[python-multiplatform](https://github.com/thisisthepy/python-multiplatform) meet two worlds of
concurrency. Python has `asyncio`. Kotlin has coroutines, and on Android you will use them.
`pythonx-concurrent` is a pythonx library, Python language extensions in the spirit of androidx and
kotlinx. It joins the two worlds and gives Python the structure `kotlinx.coroutines` gives Kotlin.

## What it will do

- **Scopes and jobs**, spelled like `asyncio.TaskGroup`, named like Kotlin: `launch`, `Job`,
  `with_context`, `with_timeout`.
- **Dispatchers**: `Main`, `IO` and `Default`, mapped to each platform's threads.
- **Both-way bridge**: Python awaits Kotlin `suspend` functions and `Flow`. Kotlin awaits Python
  coroutines. Cancellation flows both ways.
- **Goroutine-style scheduling** of many lightweight tasks over a few threads, parallel on
  free-threaded CPython where that exists.

```python
from pythonx.concurrent import Scope, Dispatchers, with_context

async def load(user_id):
    async with Scope() as s:
        profile = s.defer(fetch_profile, user_id)
        data = await with_context(Dispatchers.IO, read_cache, user_id)
    return profile.result(), data
```

<sub>This example shows the planned spelling. Nothing is built yet.</sub>

## Install

Not published yet. When it is:

```bash
uv add --prerelease allow pythonx-concurrent
```

## Status

Spec stage. There is no code. The intent, the behaviour contract and the research are written, and
every behaviour is `planned`. Several features wait on python-multiplatform; each is tracked as an
issue there.

## License

Apache-2.0.
