"""A Python Actor guarded by apify-bot-guard.

Run on Apify with GUARD_TOKEN set (see ../README.md). Locally the guard is off,
because there is no free-plan user to limit.
"""

from __future__ import annotations

import asyncio
import os

from apify import Actor

from apify_bot_guard import ApifyKvBackend, BotGuard, push_data


async def scrape(page: int) -> list[dict[str, object]]:
    """Stand-in for your scraper: 25 results per page."""
    return [{"page": page, "item": i} for i in range(25)]


async def main() -> None:
    async with Actor:
        backend = ApifyKvBackend(token=os.environ.get("GUARD_TOKEN"))
        decision = await BotGuard.admit("my-actor", backend)

        if not decision.allowed:
            # Nothing left this period: succeed with a clear message, never fail.
            await Actor.set_status_message(decision.message or "Free plan limit reached.")
            return

        guard = decision.guard  # None for paying users: no limit
        try:
            for page in range(1, 100):
                pushed = await push_data(guard, await scrape(page))
                if guard and guard.remaining == 0:
                    break
                if pushed == 0:
                    break
        finally:
            if guard:
                await guard.close()  # books what was delivered; tidies old records now and then


if __name__ == "__main__":
    asyncio.run(main())
