import asyncio
import logging
from typing import Coroutine, Any, Optional

log = logging.getLogger(__name__)

class TaskManager:
    """Small helper to track background tasks and log exceptions.

    v16.1 policy: do not call asyncio.create_task directly around the codebase.
    Use TaskManager.create(...) (or Scheduler jobs) so tasks are tracked and
    errors are visible in logs.
    """

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task] = set()

    def create(self, coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None) -> asyncio.Task:
        # Centralized place where create_task is allowed.
        task = asyncio.create_task(coro, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self._log_exception)
        return task

    def _log_exception(self, task: asyncio.Task) -> None:
        try:
            exc = task.exception()
            if exc:
                log.error(
                    "Background task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        except asyncio.CancelledError:
            pass
        except (asyncio.InvalidStateError, RuntimeError):  # validator: allow-except-exception
            # Never fail the event loop because logging failed.
            log.exception("Failed to inspect task exception")

    async def shutdown(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
