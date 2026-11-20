# apify-bot-guard

[![CI](https://github.com/tacps126/apify-bot-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/tacps126/apify-bot-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Fair free-plan allowances for Apify Actors, in Python and JavaScript, with no runtime dependencies.

Runs by users on Apify's free plan are paid from the credit Apify gives them, while your proxy, compute
and API costs are still yours. Free accounts are also what scraper farms use: many accounts, each
taking its free share. `apify-bot-guard` gives every free user a fair monthly allowance and puts a
ceiling on what all free users of an Actor can take together. Paying users are never limited.

It is not tied to Apify: pass the user id, plan and run id yourself and it guards any free tier.

- [How it works](#how-it-works)
- [One answer: allowed or not](#one-answer-allowed-or-not)
- [Install](#install)
- [Python](#python)
- [JavaScript](#javascript)
- [Storage backends](#storage-backends)
- [Limits](#limits)
- [Cost](#cost)
- [Examples](examples)
- [Design and storage format](docs/DESIGN.md)
- [Maintaining and releasing](docs/MAINTAINING.md)
- [Contributing](CONTRIBUTING.md)

## How it works

At the start of a run the guard reads two small records: the user's runs this month and the Actor's
pool for all free users. It works out what the run may deliver, books that amount as a hold (so a
second run started meanwhile sees it as used), and lets you deliver up to it. At the end the run
books what it really delivered, which replaces the hold.

| Limit | What it stops |
|---|---|
| Per user per Actor per month (results and your cost) | One account taking more than its share |
| Per user across all your Actors per month | One account spreading over many Actors |
| Daily share of the monthly allowance | Emptying a month in one burst |
| Pool per Actor for all free users, released evenly through the month | Farms of many accounts: they can never take more than what has been released so far |
| Smaller share for new accounts | Fresh farm accounts |

A run that finds nothing left still succeeds, with a status message saying when more is available.
When storage cannot be read, the run gets no monthly limit and a warning: a storage problem never
blocks a customer.

## One answer: allowed or not

`admit()` gives you a decision to act on:

```python
decision = await BotGuard.admit("my-actor", backend)          # or user_id=..., free=... off Apify
if not decision.allowed:
    await Actor.set_status_message(decision.message)          # "Free plan limit reached ... more on 2026-12-01"
    return                                                    # succeed, never fail
guard = decision.guard                                        # None when unlimited (paying users)
```

| Field | Meaning |
|---|---|
| `allowed` | `true`: deliver. `false`: nothing left this period; end with `message` |
| `unlimited` | No limit applies (paying user, guard off, or storage unreadable) |
| `remaining` | Results this run may deliver (`None` / `null` when unlimited) |
| `bound` | The limit that applies: `month`, `day` or `pool` |
| `message`, `resets_on` / `resetsOn` | What to tell the user, and the UTC day more becomes available |
| `guard` | Keep it while delivering: `take(n)` before each batch, `close()` at the end |

Everything else happens inside: plan detection, holds against parallel runs, booking, the monthly
reset, and cleanup of old records. Full runnable examples, including a free tier on your own HTTP API,
are in [examples/](examples).

## Install

```bash
pip install "apify-bot-guard[apify]"   # Python, with the Apify SDK and client
npm install apify-bot-guard            # JavaScript (apify / apify-client are optional peers)
```

Python 3.10+, Node.js 20+.

## Python

```python
import os
from apify import Actor
from apify_bot_guard import ApifyKvBackend, BotGuard, push_data

async def main() -> None:
    async with Actor:
        backend = ApifyKvBackend(token=os.environ["GUARD_TOKEN"])  # your token, a secret env var
        guard = await BotGuard.open("my-actor", backend)           # None for paying users
        try:
            async for page in pages():
                await push_data(guard, scrape(page))               # cut to the allowance
                if guard and guard.remaining == 0:
                    break
        finally:
            if guard:
                await guard.close()                                # books what was delivered
```

With Redis or Upstash:

```python
import redis.asyncio as redis
from apify_bot_guard import RedisBackend

backend = RedisBackend(redis.from_url(os.environ["REDIS_URL"]))
```

Without the Apify SDK, call `guard.take(n)` before delivering `n` results; it returns how many you
may deliver. `guard.message` is a ready status line for the user.

## JavaScript

```js
import { Actor } from 'apify';
import { ApifyKvBackend, BotGuard, pushData } from 'apify-bot-guard';

await Actor.init();
const guard = await BotGuard.open('my-actor', new ApifyKvBackend({ token: process.env.GUARD_TOKEN }));
try {
    for await (const page of pages()) {
        await pushData(guard, await scrape(page));
        if (guard && guard.remaining === 0) break;
    }
} finally {
    await guard?.close();
}
await Actor.exit();
```

With Redis: `new RedisBackend(client)` for ioredis, node-redis (v4+) or `@upstash/redis`. Types are
included.

### Outside Apify

```js
const guard = await BotGuard.open('search-api', backend, {
    userId: account.id,
    free: account.plan === 'free',
    runId: request.id,
    accountCreated: account.createdAt,
});
```

## Storage backends

| Backend | Needs | Round trips per run | Good for |
|---|---|---|---|
| `ApifyKvBackend` | Your Apify token as a secret env var | 3 batches (parallel calls) | No extra service |
| `RedisBackend` | Redis, Valkey or Upstash | 3 pipelined batches | Exact counts under heavy parallel load |
| `MemoryBackend` | nothing | 0 | Tests, local runs |

The run's own token belongs to the user and cannot write to your stores, so the Apify backend uses
your token. Store keys hold a salted hash of the user id, never the id itself, and Python and
JavaScript produce the same hash, so Actors in both languages can share one store.

Old records clean themselves up. Redis ledgers expire after about 45 days. Key-value stores do not
expire records, so about 1 in 50 runs (`auto_prune` / `autoPrune`, default 0.02) deletes ledgers older
than last month on close. Set it to 0 and run [examples/cleanup](examples/cleanup) on a schedule if you
prefer.

Need another store (Postgres, DynamoDB, Cloudflare KV)? A backend is four methods; see
[docs/DESIGN.md](docs/DESIGN.md#writing-a-backend) and send a pull request.

## Limits

Set with env vars on the Actor, or pass a config in code.

| Limit | Env var | Default |
|---|---|---|
| Results per user per Actor per month | `BOT_GUARD_ACTOR_MONTHLY_RESULTS` | 100 |
| Your cost per user per Actor per month (USD) | `BOT_GUARD_ACTOR_MONTHLY_USD` | 0.05 |
| Results per user across all your Actors per month | `BOT_GUARD_USER_MONTHLY_RESULTS` | 300 |
| Your cost per user across all your Actors per month (USD) | `BOT_GUARD_USER_MONTHLY_USD` | 0.25 |
| Share of the monthly allowance usable in one day | `BOT_GUARD_DAILY_SHARE` | 0.34 |
| All free users of one Actor together, per month | `BOT_GUARD_POOL_MONTHLY_RESULTS` | 3000 |
| New accounts: age in days, and their share of the limits | `BOT_GUARD_NEW_ACCOUNT_DAYS`, `BOT_GUARD_NEW_ACCOUNT_SHARE` | 7, 0.25 |
| What one result costs you (USD) | `BOT_GUARD_COST_PER_RESULT` | 0.0005 |
| Book under another name (variants sharing one allowance) | `BOT_GUARD_SCOPE` | the scope you pass |
| Turn it off | `BOT_GUARD=off` | |

Plan detection on Apify: `APIFY_USER_PRICING_TIER` when set, else `APIFY_USER_IS_PAYING`. That variable
is `1` for paying users and set but empty for free ones, so an empty value means free.

## Cost

Per free run the Apify backend reads 4 to 5 small JSON records and writes 4; Redis runs about 12
commands in 3 pipelined round trips.

| Free runs / month | Apify key-value store | Upstash (pay as you go) |
|---|---|---|
| 10,000 | about $2.30 | free tier |
| 50,000 | about $11 | about $0.20 |
| 100,000 | about $23 | about $1.40 |

Storage itself is pennies: about 1 KB per user per month. Prices as of late 2026 (Apify: $0.05 per
1,000 writes, $0.005 per 1,000 reads; Upstash: 500,000 commands a month free, then $0.20 per 100,000).
Check both price pages, as they change.

## Development

```bash
cd python && pip install -e ".[dev]" && ruff check . && mypy && coverage run -m unittest discover -s tests -t . && coverage report
cd js && npm ci && npm run check
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Both packages run the same scenarios; a change to the rules
lands in both, with tests, in one pull request.

## License

[MIT](LICENSE)
