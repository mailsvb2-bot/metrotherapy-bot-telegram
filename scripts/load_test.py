import logging

import argparse, asyncio, time, statistics, random
from collections import defaultdict

logger = logging.getLogger(__name__)
async def simulate_user(uid: int, results: dict):
    t0 = time.perf_counter()
    await asyncio.sleep(random.uniform(0.05, 0.2))  # /start
    t1 = time.perf_counter()
    await asyncio.sleep(random.uniform(0.05, 0.2))  # mood
    t2 = time.perf_counter()
    await asyncio.sleep(random.uniform(0.05, 0.2))  # audio
    t3 = time.perf_counter()
    results["start_to_mood"].append((t1 - t0) * 1000)
    results["mood_to_audio"].append((t2 - t1) * 1000)
    results["audio_to_post"].append((t3 - t2) * 1000)

async def run(users: int, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    results = defaultdict(list)

    async def wrapped(uid):
        async with sem:
            await simulate_user(uid, results)

    await asyncio.gather(*(wrapped(i) for i in range(users)))

    for k, v in results.items():
        logger.info(k, "avg:", statistics.mean(v), "p95:", statistics.quantiles(v, n=20)[18], "p99:", statistics.quantiles(v, n=100)[98])

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=100)
    args = ap.parse_args()
    asyncio.run(run(args.users, args.concurrency))