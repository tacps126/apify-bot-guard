"""A free tier for any Python job runner, outside Apify.

Each job is a "run" for one of your accounts. Needs REDIS_URL (Redis, Valkey) or
UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN.

    python job.py acct-1 free 120
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from apify_bot_guard import BotGuard, RedisBackend


def redis_client() -> Any:
    if os.environ.get("UPSTASH_REDIS_REST_URL"):
        from upstash_redis.asyncio import Redis as Upstash

        return Upstash.from_env()
    import redis.asyncio as redis

    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))


async def run_job(account: str, plan: str, wanted: int) -> None:
    backend = RedisBackend(redis_client())
    # Limits in code here; BOT_GUARD_* env vars work too (code wins). See docs/CONFIGURATION.md.
    decision = await BotGuard.admit(
        "export-job",
        backend,
        user_id=account,  # required outside Apify
        plan=plan,  # your plan name; limited when it is in `plans`
        plans=["free", "trial"],
        actor_month_results=500,
        actor_week_results=200,
        run_results=100,
        pool_month_results=20_000,
    )

    if not decision.allowed:
        print(f"blocked: {decision.message} (more on {decision.resets_on})")
        return
    guard = decision.guard
    n = guard.take(wanted) if guard else wanted
    print(f"exporting {n} of {wanted} rows" + ("" if decision.unlimited else f", {guard.remaining} left this run"))
    if guard:
        await guard.close()


if __name__ == "__main__":
    acct, plan, rows = sys.argv[1], sys.argv[2], int(sys.argv[3])
    asyncio.run(run_job(acct, plan, rows))
