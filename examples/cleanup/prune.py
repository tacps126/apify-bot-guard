"""Delete ledgers older than last month from an Apify key-value store.

Not needed by default: each guarded run tidies the store now and then
(`auto_prune`, 2% of runs). Run this on a monthly schedule instead if you set
auto_prune=0, for example as a scheduled Actor or a cron job:

    GUARD_TOKEN=... python prune.py

Redis needs nothing: its ledgers expire on their own.
"""

from __future__ import annotations

import asyncio
import os

from apify_bot_guard import ApifyKvBackend
from apify_bot_guard.core import previous_month, utc_today


async def main() -> None:
    backend = ApifyKvBackend(token=os.environ["GUARD_TOKEN"], store=os.environ.get("GUARD_STORE", "bot-guard"))
    removed = await backend.prune(previous_month(utc_today()[:7]))
    print(f"removed {removed} old ledgers")


if __name__ == "__main__":
    asyncio.run(main())
