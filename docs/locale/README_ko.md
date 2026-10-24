[English](https://github.com/thisisthepy/pythonx-concurrent/blob/main/README.md) | 한국어

# pythonx-concurrent

**Kotlin Multiplatform 위의 Python 앱을 위한 편한 동시성: asyncio와 Kotlin 코루틴을 함께 씁니다.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-7c4dff.svg)](https://github.com/thisisthepy/pythonx-concurrent/blob/main/LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-blue.svg)](#상태)

## 왜 필요한가

[python-multiplatform](https://github.com/thisisthepy/python-multiplatform)으로 Android, iOS, 데스크톱에서
실행되는 Python 앱은 동시성의 두 세계를 만납니다. Python에는 `asyncio`가 있습니다. Kotlin에는 코루틴이
있고, Android에서는 코루틴을 쓰게 됩니다. `pythonx-concurrent`는 pythonx 라이브러리입니다. androidx와
kotlinx의 정신을 따르는 Python 언어 확장입니다. 두 세계를 이어 주고, `kotlinx.coroutines`가 Kotlin에
주는 구조를 Python에도 줍니다.

## 제공하는 기능

- **스코프와 잡**: `asyncio.TaskGroup`처럼 쓰고 Kotlin처럼 이름을 붙입니다. `Scope`,
  `supervisor_scope`, `CoroutineScope`, `launch`, `defer`, `Job`, `Deferred`, `with_context`,
  `with_timeout`, `with_timeout_or_none`, `non_cancellable`이 있습니다.
- **디스패처**: `Main`, `IO`, `Default`, `Unconfined`, `Loop`와 `Dispatchers.from_kotlin`,
  `Dispatchers.limited`를 제공합니다.
- **양방향 브리지**: Python이 Kotlin `suspend` 함수와 `Flow`(계획됨)를 기다립니다. Kotlin이 Python
  코루틴을 기다립니다(계획됨).
- **고루틴 방식 스케줄링**: 첫 스텝 이전 작업 훔치기(work stealing)를 지원하는 캐리어 루프 위에
  가벼운 태스크 여러 개를 올립니다. 프리스레딩 CPython 3.15t에서는 실제로 병렬로 실행됩니다.

```python
from pythonx.concurrent import Scope, Dispatchers, with_context

async def load(user_id):
    async with Scope() as s:
        profile = s.defer(fetch_profile, user_id)
        data = await with_context(Dispatchers.IO, read_cache, user_id)
    return profile.result(), data
```

## 설치

```bash
uv add --prerelease allow pythonx-concurrent
ppp core add "pythonx-concurrent==0.0.1a1"
tcl install pythonx-concurrent
```

## 상태

마일스톤 1이 구현되어 CPython 3.15 및 프리스레딩 CPython 3.15t에서 검증되었습니다(`Scope`,
`supervisor_scope`, `CoroutineScope`, `Job`, `Deferred`, `Dispatchers`, `KotlinEventLoop`,
`with_context`, `run_blocking`, `run_blocking_call`, `with_timeout`, `with_timeout_or_none`,
`non_cancellable`, `CancelToken`). `python-multiplatform`의 새 훅이 필요한 기능(`Flow`, Kotlin 모드
스테핑, 부모 `Job` 전파)은 그쪽 이슈로 추적합니다.

## 라이선스

Apache-2.0입니다.
