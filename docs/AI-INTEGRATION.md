# Adding apify-bot-guard with an AI assistant

This page is written for AI coding assistants (Claude, ChatGPT, Copilot, Cursor and others) and for
people who use them. It holds everything needed to add the guard to a project correctly, in one file.
Point your assistant at it, or paste one of the prompts at the end.

## What the assistant must know first

- The guard decides, at run start, how many results a run may deliver. It never fails a run: when
  nothing is left the run ends successfully with a status message.
- Paying users are never limited. On Apify the plan is read from the run's environment; elsewhere you
  pass `plan` (or `free`).
- One decision per run: `admit()`. Then `take(n)` before delivering `n` results (or the `push_data` /
  `pushData` helper), and `close()` at the end, also on errors (`finally`).
- Storage: on Apify use `ApifyKvBackend` with the developer's own Apify token stored as a secret env
  var (the run's token belongs to the user and cannot write to the developer's store). Redis, Valkey
  or Upstash work through `RedisBackend`. `MemoryBackend` is for tests only.
- Settings come from `BOT_GUARD_*` env vars or code arguments; code wins. Python uses `snake_case`,
  JavaScript `camelCase`. Full list: [CONFIGURATION.md](CONFIGURATION.md).

## Step 1: find out about the project

Ask or detect before writing code:

| Question | Where to look | Why it matters |
|---|---|---|
| Python or JavaScript/TypeScript? | `requirements.txt`, `pyproject.toml`, `package.json` | Which package |
| Runs on Apify? | `.actor/actor.json`, imports of `apify` | Identity is automatic on Apify |
| Where are results delivered? | `Actor.push_data`, `Actor.pushData`, `dataset.push`, an HTTP response | Where to put `take()` |
| Several Actors that should share one allowance? | Repo layout | Same `scope` (or `BOT_GUARD_SCOPE`) |
| Redis already in the stack? | env vars like `REDIS_URL` | Exact counts under heavy parallel load |
| What should free users get? | Ask the developer | Limits; defaults are 100 results per Actor per month |

## Step 2: install

```bash
pip install "apify-bot-guard[apify]"    # Python; add [redis] for RedisBackend
npm install apify-bot-guard             # JavaScript; apify and apify-client are optional peers,
                                        # a Redis client (ioredis, redis, @upstash/redis) is yours
```

## Step 3: wire it in

### Python Actor on Apify

```python
import os
from apify import Actor
from apify_bot_guard import ApifyKvBackend, BotGuard, push_data

async def main() -> None:
    async with Actor:
        decision = await BotGuard.admit("my-actor", ApifyKvBackend(token=os.environ.get("GUARD_TOKEN")))
        if not decision.allowed:
            await Actor.set_status_message(decision.message)
            return                                    # succeed: nothing left this period
        guard = decision.guard                        # None when the user is not limited
        try:
            for batch in produce_results():           # the project's own loop
                await push_data(guard, batch)         # pushes at most what is allowed
                if guard and guard.remaining == 0:
                    break                             # stop paying for work nobody receives
        finally:
            if guard:
                await guard.close()                   # books what was really delivered
```

### JavaScript Actor on Apify

```js
import { Actor } from 'apify';
import { ApifyKvBackend, BotGuard, pushData } from 'apify-bot-guard';

await Actor.init();
const decision = await BotGuard.admit('my-actor', new ApifyKvBackend({ token: process.env.GUARD_TOKEN }));
if (!decision.allowed) {
    await Actor.setStatusMessage(decision.message);
    await Actor.exit();
} else {
    const { guard } = decision;
    try {
        for await (const batch of produceResults()) {
            await pushData(guard, batch);
            if (guard && guard.remaining === 0) break;
        }
    } finally {
        await guard?.close();
    }
    await Actor.exit();
}
```

### Any other service (API, job queue, SaaS free tier)

```js
const decision = await BotGuard.admit('search-api', backend, {
    userId: account.id,            // required; no id means no limit
    plan: account.plan,            // required; limited when it is in `plans`
    plans: ['free', 'trial'],
    runId: request.id,             // optional; random when missing
    accountCreated: account.createdAt, // optional; enables the smaller share for new accounts
    userDayResults: 200,           // any setting can be passed here
});
if (!decision.allowed) return res.status(429).json({ error: decision.message, resetsOn: decision.resetsOn });
const n = decision.guard ? decision.guard.take(results.length) : results.length;
res.json(results.slice(0, n));
await decision.guard?.close();
```

Python takes the same as keyword arguments: `user_id=`, `plan=`, `plans=`, `run_id=`,
`account_created=`, `user_day_results=`.

Without the Apify SDK helpers, call `n = guard.take(len(items))` and deliver `items[:n]`.

## Step 4: settings

Put them where the developer manages config. On Apify that is `environmentVariables` in
`.actor/actor.json` (the Actor must be rebuilt after a change) or the Actor's environment settings in
Apify Console:

```json
{
  "environmentVariables": {
    "GUARD_TOKEN": "@guardToken",
    "BOT_GUARD_ACTOR_MONTH_RESULTS": "200",
    "BOT_GUARD_ACTOR_WEEK_RESULTS": "80",
    "BOT_GUARD_RUN_RESULTS": "50",
    "BOT_GUARD_MODE": "observe"
  }
}
```

`@guardToken` is an Apify secret: `apify secrets add guardToken <token>`. Never write a token into
code or into `actor.json` as plain text.

Common choices:

| The developer wants | Setting |
|---|---|
| Try it without cutting anyone | `BOT_GUARD_MODE=observe`, read the logs, then remove it |
| A weekly instead of monthly allowance | `BOT_GUARD_ACTOR_WEEK_RESULTS=50`, `BOT_GUARD_ACTOR_MONTH_RESULTS=none` |
| A cap on what one free run costs them | `BOT_GUARD_RUN_USD=0.02` with `BOT_GUARD_COST_PER_RESULT` set to their real cost |
| Limit the cheapest paid plan too | `BOT_GUARD_PLANS=FREE,STARTER` |
| Never limit themselves or a tester | `BOT_GUARD_EXEMPT_USERS=<their user id>` |
| Several Actors share one allowance | the same scope in `admit()`, or `BOT_GUARD_SCOPE=family-name` |
| Turn it off quickly | `BOT_GUARD=off` |

Every setting, its default and its exact behaviour: [CONFIGURATION.md](CONFIGURATION.md).

## Step 5: check the work

- [ ] `close()` runs on every path, including exceptions (`finally`).
- [ ] Results are cut with `take()` or `push_data` / `pushData` before they are delivered, not after.
- [ ] The run stops working once `remaining` is 0, so free users do not cost money for nothing.
- [ ] A run with nothing left ends successfully and sets `decision.message` as its status.
- [ ] The storage token is a secret env var, not the run's own `APIFY_TOKEN`.
- [ ] Tests use `MemoryBackend` and pass `env`, `today` and `account_created` / `accountCreated` to
      make results repeatable. Example: limited user `{"APIFY_USER_ID": "u1", "APIFY_USER_IS_PAYING": ""}`.
- [ ] Paying users: `decision.unlimited` is true and `decision.guard` is `None` / `null`.

## Mistakes to avoid

- Failing the run (raising, non-zero exit) when the allowance is used up. Users see failed runs and
  the Store shows a failure rate. End successfully with the message instead.
- Calling `take()` after pushing: the results are already delivered.
- Opening a new guard per batch. Open once per run.
- Reading the plan yourself on Apify. The guard does it (`APIFY_USER_PRICING_TIER`, then
  `APIFY_USER_IS_PAYING`).
- Unknown setting names in code raise `TypeError`: check spelling against CONFIGURATION.md.
- Storage outages never block users: the run continues without a limit and logs a warning. Do not
  wrap the guard in code that turns that warning into an error.

## Prompts to paste

Add the guard:

```text
Add apify-bot-guard to this project. Read
https://github.com/tacps126/apify-bot-guard/blob/main/docs/AI-INTEGRATION.md first and follow it.
Free users get <N> results per month per Actor; paying users are not limited. Store the ledgers with
<ApifyKvBackend and my token in the secret GUARD_TOKEN | RedisBackend using REDIS_URL>. Start in
observe mode. Add tests with MemoryBackend. Show me the settings you chose.
```

Tune the limits:

```text
Using https://github.com/tacps126/apify-bot-guard/blob/main/docs/CONFIGURATION.md, change our
apify-bot-guard settings so that <goal, e.g. free users get 50 results a week and no run costs us
more than $0.02>. Change only env vars or admit() arguments, and explain each one.
```
