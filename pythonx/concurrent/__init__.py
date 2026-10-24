"""pythonx-concurrent: structured concurrency for Python on Kotlin Multiplatform.

Provides `Scope`, `supervisor_scope`, `CoroutineScope`, `Job`, `Deferred`, `Dispatchers`,
`KotlinEventLoop`, `with_context`, `run_blocking`, `run_blocking_call`, `with_timeout`,
`with_timeout_or_none`, `non_cancellable`, `CancelToken` and `TimeoutFlag`.
"""

from __future__ import annotations

from pythonx.concurrent._cancellation import (
    CancelToken,
    TimeoutFlag,
    non_cancellable,
    with_timeout,
    with_timeout_or_none,
)
from pythonx.concurrent._dispatchers import (
    CarrierDispatcher,
    Dispatcher,
    Dispatchers,
    IODispatcher,
    KotlinDispatcherAdapter,
    LimitedDispatcher,
    LoopDispatcher,
    MainDispatcher,
    UnconfinedDispatcher,
    run_blocking,
    run_blocking_call,
    with_context,
)
from pythonx.concurrent._kotlin_loop import KotlinEventLoop
from pythonx.concurrent._scope import (
    CoroutineScope,
    Deferred,
    Job,
    Scope,
    supervisor_scope,
)

__version__ = "0.0.1a1"

__all__ = [
    "CancelToken",
    "CarrierDispatcher",
    "CoroutineScope",
    "Deferred",
    "Dispatcher",
    "Dispatchers",
    "IODispatcher",
    "Job",
    "KotlinDispatcherAdapter",
    "KotlinEventLoop",
    "LimitedDispatcher",
    "LoopDispatcher",
    "MainDispatcher",
    "Scope",
    "TimeoutFlag",
    "UnconfinedDispatcher",
    "__version__",
    "non_cancellable",
    "run_blocking",
    "run_blocking_call",
    "supervisor_scope",
    "with_context",
    "with_timeout",
    "with_timeout_or_none",
]
