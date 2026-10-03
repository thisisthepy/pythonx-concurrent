[English](https://github.com/thisisthepy/pythonx-concurrent/blob/main/README.md) | 한국어

# pythonx-concurrent

**Kotlin Multiplatform 위의 Python 앱을 위한 편한 동시성: asyncio와 Kotlin 코루틴을 함께 씁니다.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-7c4dff.svg)](https://github.com/thisisthepy/pythonx-concurrent/blob/main/LICENSE)
[![Status](https://img.shields.io/badge/status-spec%20stage-lightgrey.svg)](#상태)

## 왜 필요한가

[python-multiplatform](https://github.com/thisisthepy/python-multiplatform)으로 Android, iOS, 데스크톱에서
실행되는 Python 앱은 동시성의 두 세계를 만납니다. Python에는 `asyncio`가 있습니다. Kotlin에는 코루틴이
있고, Android에서는 코루틴을 쓰게 됩니다. `pythonx-concurrent`는 pythonx 라이브러리입니다. androidx와
kotlinx의 정신을 따르는 Python 언어 확장입니다. 두 세계를 이어 주고, `kotlinx.coroutines`가 Kotlin에
주는 구조를 Python에도 줍니다.

## 하려는 일

- **스코프와 잡**: `asyncio.TaskGroup`처럼 쓰고 Kotlin처럼 이름을 붙입니다. `launch`, `Job`,
  `with_context`, `with_timeout`이 있습니다.
- **디스패처**: `Main`, `IO`, `Default`를 각 플랫폼의 스레드에 대응시킵니다.
- **양방향 브리지**: Python이 Kotlin `suspend` 함수와 `Flow`를 기다립니다. Kotlin이 Python 코루틴을
  기다립니다. 취소는 양쪽으로 전달됩니다.
- **고루틴 방식 스케줄링**: 가벼운 태스크 여러 개를 소수의 스레드 위에 올립니다. 프리스레딩 CPython이
  있는 곳에서는 실제로 병렬로 실행됩니다.

```python
from pythonx.concurrent import Scope, Dispatchers, with_context

async def load(user_id):
    async with Scope() as s:
        profile = s.defer(fetch_profile, user_id)
        data = await with_context(Dispatchers.IO, read_cache, user_id)
    return profile.result(), data
```

<sub>이 예제는 계획된 표기입니다. 아직 구현된 것은 없습니다.</sub>

## 설치

아직 배포되지 않았습니다. 배포되면 다음과 같이 설치합니다.

```bash
uv add --prerelease allow pythonx-concurrent
```

## 상태

명세 단계입니다. 코드는 없습니다. 의도, 동작 계약, 조사 결과가 작성되어 있고 모든 동작은 `planned`
상태입니다. 일부 기능은 python-multiplatform의 작업을 기다리며, 각각 그쪽 이슈로 추적합니다.

## 라이선스

Apache-2.0입니다.
