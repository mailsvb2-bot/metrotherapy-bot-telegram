
import asyncio
_queues = {}
def queue(user_id: int) -> asyncio.Semaphore:
    sem = _queues.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(1)
        _queues[user_id] = sem
    return sem
