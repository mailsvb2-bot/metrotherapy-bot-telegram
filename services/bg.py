from __future__ import annotations

from core.task_manager import TaskManager

_tm: TaskManager | None = None

def tm() -> TaskManager:
    global _tm
    if _tm is None:
        _tm = TaskManager()
    return _tm
