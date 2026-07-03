"""Adapter registry. The graph asks for the adapter by name (env EXECUTOR_ADAPTER);
tests inject instances to count calls; crash-resume rebuilds it from persisted state.
"""
from .. import config
from .base import ExecutorAdapter, ExecutorResult
from .dryrun_adapter import AlwaysFailAdapter, DryRunAdapter, FlakyAdapter

__all__ = [
    "ExecutorAdapter", "ExecutorResult",
    "DryRunAdapter", "FlakyAdapter", "AlwaysFailAdapter",
    "get_adapter", "set_adapter", "reset_adapters",
]

_REGISTRY: dict[str, ExecutorAdapter] = {}


def _build(name: str) -> ExecutorAdapter:
    if name == "dryrun":
        return DryRunAdapter()
    if name == "flaky":
        return FlakyAdapter()
    if name == "alwaysfail":
        return AlwaysFailAdapter()
    if name == "pi":
        from .pi_adapter import PiAdapter  # deferred: needs no import cost on dryrun path
        return PiAdapter()
    raise ValueError(f"unknown executor adapter: {name}")


def get_adapter(name: str | None = None) -> ExecutorAdapter:
    name = name or config.executor_adapter_name()
    if name not in _REGISTRY:
        _REGISTRY[name] = _build(name)
    return _REGISTRY[name]


def set_adapter(name: str, adapter: ExecutorAdapter) -> None:
    _REGISTRY[name] = adapter


def reset_adapters() -> None:
    _REGISTRY.clear()
