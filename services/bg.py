from __future__ import annotations

from core.task_manager import TaskManager

_tm: TaskManager | None = None


def bind_task_manager(task_manager: TaskManager) -> TaskManager:
    """Bind the process-wide canonical TaskManager.

    Runtime owners such as DB writer and scheduler import services.bg.tm(). The
    app boot path must bind the same instance before those owners start, otherwise
    background tasks are split across two lifecycle managers and shutdown becomes
    partial.
    """
    global _tm
    _tm = task_manager
    return _tm


def tm() -> TaskManager:
    global _tm
    if _tm is None:
        _tm = TaskManager()
    return _tm
