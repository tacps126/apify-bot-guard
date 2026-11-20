# apify-free-guard

Monthly free-plan allowances for Apify Actors, in Python and JavaScript.

Runs by users on Apify's free plan are paid from the credit Apify gives them, while your proxy, compute
and API costs are still yours. Free accounts are also what scraper farms use: many accounts, each taking
its free share. This library gives every free user a fair monthly allowance and puts a ceiling on what
all free users of an Actor can take together. Paying users are never touched.

## Limits

All in UTC months and days. Set them with env vars on the Actor, or in code.

| Limit | Env var | Default |
|---|---|---|
| Results per user per Actor per month | `FREE_ACTOR_MONTHLY_RESULTS` | 100 |
| Your cost per user per Actor per month (USD) | `FREE_ACTOR_MONTHLY_USD` | 0.05 |
| Results per user across all your Actors per month | `FREE_USER_MONTHLY_RESULTS` | 300 |
| Your cost per user across all your Actors (USD) | `FREE_USER_MONTHLY_USD` | 0.25 |
| Share of the monthly allowance usable in one day | `FREE_DAILY_SHARE` | 0.34 |
| All free users of one Actor together, per month | `FREE_POOL_MONTHLY_RESULTS` | 3000 |
| New accounts: age in days, and their share | `FREE_NEW_ACCOUNT_DAYS`, `FREE_NEW_ACCOUNT_SHARE` | 7, 0.25 |
| What one result costs you (USD) | `FREE_COST_PER_RESULT` | 0.0005 |
| Book under another name (variants sharing one allowance) | `FREE_BUDGET_SCOPE` | the scope you pass |
| Turn it off | `FREE_BUDGET=off` | |

The pool is released evenly through the month (on day 10 of 30, a third of it is open), so a farm can
never drain more than what has been released so far.

A run that starts while another is still going sees that run's share as used: each run books a hold at
start and replaces it with what it really delivered at the end. Holds from earlier days are ignored, so a
run that crashed does not block anyone.

Plan detection: `APIFY_USER_PRICING_TIER` when set, else `APIFY_USER_IS_PAYING`. That variable is `1` for
paying users and set but empty for free ones, so an empty value means free.

When storage cannot be read, the run gets no monthly limit and a warning in the log. A storage problem
never blocks a customer.

## Storage

| Backend | Needs | Good for |
|---|---|---|
| `ApifyKvBackend` | Your Apify token, saved as a secret env var on the Actor | No extra service. Up to tens of thousands of free runs a month |
| `RedisBackend` | Redis, Valkey or Upstash | Exact counts under heavy parallel load; one hash field per run, nothing overwritten |
| `MemoryBackend` | nothing | Tests and local runs |

The Actor's own run token belongs to the user and cannot write to your stores, so the Apify backend uses
your token. Store keys hold a hash of the user id, never the id itself.

Rough cost per month. Per free run the Apify backend does about 7 reads and 4 writes (each save re-reads
the record first); Redis does about 12 commands.

| Free runs / month | Apify key-value store | Upstash (pay as you go) |
|---|---|---|
| 10,000 | about $2.40 | free tier |
| 50,000 | about $12 | about $0.20 |
| 100,000 | about $24 | about $1.40 |

Storage itself is pennies: about 1 KB per user per month.

Apify prices: $0.05 per 1,000 writes, $0.005 per 1,000 reads. Upstash: 500,000 commands a month free,
then $0.20 per 100,000. Check both price pages, as they change.

## Python

```bash
pip install "apify-free-guard[apify]"        # Apify storage
pip install "apify-free-guard[apify,redis]"  # or Redis
```

```python
import os
from apify import Actor
from apify_free_guard import ApifyKvBackend, FreeGuard, push_data

async def main():
    async with Actor:
        backend = ApifyKvBackend(token=os.environ["GUARD_TOKEN"])  # secret env var
        guard = await FreeGuard.open("my-actor", backend)          # None for paying users
        try:
            for page in pages():
                if await push_data(guard, scrape(page)) == 0 and guard and guard.limited:
                    break
        finally:
            if guard:
                await guard.close()
```

With Redis: `RedisBackend(redis.asyncio.from_url(os.environ["REDIS_URL"]))`.

## JavaScript

```bash
npm install apify-free-guard
```

```js
import { Actor } from 'apify';
import { ApifyKvBackend, FreeGuard, pushData } from 'apify-free-guard';

await Actor.init();
const guard = await FreeGuard.open('my-actor', new ApifyKvBackend({ token: process.env.GUARD_TOKEN }));
try {
    for (const page of pages) {
        if ((await pushData(guard, await scrape(page))) === 0 && guard?.limited) break;
    }
} finally {
    await guard?.close();
}
await Actor.exit();
```

With Redis: `new RedisBackend(client)` for ioredis, node-redis or `@upstash/redis`.

Without `pushData`: call `guard.take(n)` before delivering `n` results; it returns how many you may
deliver. Pass your run's own cost as the second argument if you track it. `guard.message` is a ready
status line for the user.

## Tests

```bash
cd python && python3 -m unittest discover -s tests
cd js && npm test
```

Both packages run the same scenarios and produce the same user hashes, so Python and JavaScript Actors
can share one backend.

## License

MIT
