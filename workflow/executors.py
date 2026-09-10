"""Logical executor registration without runtime-state concerns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ExecutorNotFoundError(LookupError):
    """Raised when no executor is registered for a logical executor ID."""


class ExecutorRegistry:
    """Resolve logical executor IDs to callables."""

    def __init__(self) -> None:
        self._executors: dict[str, Callable[..., Any]] = {}

    def register(self, executor_id: str, executor: Callable[..., Any]) -> None:
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("executor_id must not be empty")
        if not callable(executor):
            raise ValueError("executor must be callable")
        if executor_id in self._executors:
            raise ValueError(f"duplicate executor: {executor_id}")
        self._executors[executor_id] = executor

    def resolve(self, executor_id: str) -> Callable[..., Any]:
        try:
            return self._executors[executor_id]
        except KeyError as error:
            raise ExecutorNotFoundError(f"unknown executor: {executor_id}") from error

    def execute(self, executor_id: str, *args: Any, **kwargs: Any) -> Any:
        return self.resolve(executor_id)(*args, **kwargs)
