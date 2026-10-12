from foreman.executors.base import Executor, ExecutorInvocation
from foreman.executors.opencode import OpenCodeExecutor
from foreman.executors.shell import ShellExecutor

EXECUTORS: dict[str, Executor] = {e.kind: e for e in (OpenCodeExecutor(), ShellExecutor())}


def get_executor(kind: str) -> Executor:
    try:
        return EXECUTORS[kind]
    except KeyError:
        raise ValueError(f"unknown executor {kind!r}; known: {sorted(EXECUTORS)}") from None


__all__ = ["EXECUTORS", "Executor", "ExecutorInvocation", "get_executor"]
