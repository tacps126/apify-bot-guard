# Configuration

Everything the guard can do, how to set it, and how it behaves. Python and JavaScript accept the same
settings; Python uses `snake_case`, JavaScript `camelCase`, and every setting also has an env var.

- [Where settings come from](#where-settings-come-from)
- [Who is limited](#who-is-limited)
- [Limits](#limits)
- [How the limits combine](#how-the-limits-combine)
- [New accounts](#new-accounts)
- [Policy settings](#policy-settings)
- [Identity: what each run needs](#identity-what-each-run-needs)
- [Backend options](#backend-options)
- [What the guard does when something is wrong](#what-the-guard-does-when-something-is-wrong)
- [Recipes](#recipes)

## Where settings come from

From lowest to highest priority:

1. **Defaults** (below).
2. **Env vars** `BOT_GUARD_*`, for example set in `.actor/actor.json` under `environmentVariables`, or
   in the Actor's settings in Apify Console. On Apify, env vars belong to an Actor version, so rebuild
   after changing them.
3. **Code**: settings passed to `admit()` / `open()`, or to `Config.from_env()` / `configFromEnv()`.

```python
# Python: any setting as a keyword argument
decision = await BotGuard.admit("my-actor", backend, actor_week_results=50, run_results=20)
```

```js
// JavaScript: any setting in the options object (or under `settings`)
const decision = await BotGuard.admit('my-actor', backend, { actorWeekResults: 50, runResults: 20 });
```

A ready-made config replaces the env vars entirely: `config=Config(scope="my-actor", ...)` in Python,
`config: makeConfig('my-actor', {...})` in JavaScript. Settings passed next to `config` still go on top.

`BOT_GUARD=off` (or `0`, `false`) turns the guard off: every run is unlimited.

### Values

- **Limits** take a number `>= 0`. `0` means nothing is allowed in that window.
- To switch a limit **off**, use `None` / `null` in code, or `none`, `off`, `unlimited`, `no` or `-`
  in an env var. An env var that is unset or empty keeps the default.
- **Lists** (plans, user ids) take a list in code, or comma-separated text in an env var:
  `BOT_GUARD_EXEMPT_USERS=abc123,def456`.
- A value that cannot be read (`ten`, `-5`) is ignored, listed in `config.problems`, and logged as a
  warning when the run starts. A misconfiguration never fails a run.
- A setting name that does not exist, passed in code, raises an error right away (`TypeError`), so a
  typo cannot go unnoticed. Unknown `BOT_GUARD_*` env vars are ignored.

## Who is limited

A run is limited only when all of these hold:

| Check | Default | Setting |
|---|---|---|
| The guard is on | on | `BOT_GUARD=off` turns it off |
| The user id is known | `APIFY_USER_ID` on Apify | `user_id` / `userId` elsewhere |
| The user is not exempt | nobody is | `exempt_users` / `exemptUsers` |
| The user's plan is a limited plan | `FREE` | `plans` |
| At least one limit is set | yes | [Limits](#limits) |

On Apify the plan is read from `APIFY_USER_PRICING_TIER` (`FREE`, `STARTER`, `SCALE`, ...) when set,
else from `APIFY_USER_IS_PAYING`, which is `1` for paying users and set but empty for free ones (so an
empty value means `FREE`, anything else `PAID`). If neither is present the plan is unknown and the run
is not limited.

Outside Apify, pass the plan yourself: `plan="trial"` with `plans=["trial"]`, or the shorthand
`free=True` (plan `FREE`) / `free=False` (plan `PAID`). Plan names are compared case-insensitively.

`force=True` / `force: true` limits a run whatever its plan, for testing.

## Limits

Every limit counts **results** (items you deliver) or **USD** (what the run costs you). Results and
cost limits can be mixed freely; the tightest one wins.

| Setting (Python) | JavaScript | Env var | Default | Counts |
|---|---|---|---|---|
| `actor_month_results` | `actorMonthResults` | `BOT_GUARD_ACTOR_MONTH_RESULTS` | 100 | One user, this Actor, this calendar month |
| `actor_month_usd` | `actorMonthUsd` | `BOT_GUARD_ACTOR_MONTH_USD` | 0.05 | |
| `actor_week_results` | `actorWeekResults` | `BOT_GUARD_ACTOR_WEEK_RESULTS` | off | One user, this Actor, the last 7 days |
| `actor_week_usd` | `actorWeekUsd` | `BOT_GUARD_ACTOR_WEEK_USD` | off | |
| `actor_day_results` | `actorDayResults` | `BOT_GUARD_ACTOR_DAY_RESULTS` | off | One user, this Actor, today (UTC) |
| `actor_day_usd` | `actorDayUsd` | `BOT_GUARD_ACTOR_DAY_USD` | off | |
| `user_month_results` | `userMonthResults` | `BOT_GUARD_USER_MONTH_RESULTS` | 300 | One user, every Actor sharing the store, this month |
| `user_month_usd` | `userMonthUsd` | `BOT_GUARD_USER_MONTH_USD` | 0.25 | |
| `user_week_results` | `userWeekResults` | `BOT_GUARD_USER_WEEK_RESULTS` | off | One user, every Actor, the last 7 days |
| `user_week_usd` | `userWeekUsd` | `BOT_GUARD_USER_WEEK_USD` | off | |
| `user_day_results` | `userDayResults` | `BOT_GUARD_USER_DAY_RESULTS` | off | One user, every Actor, today |
| `user_day_usd` | `userDayUsd` | `BOT_GUARD_USER_DAY_USD` | off | |
| `run_results` | `runResults` | `BOT_GUARD_RUN_RESULTS` | off | One run |
| `run_usd` | `runUsd` | `BOT_GUARD_RUN_USD` | off | |
| `pool_month_results` | `poolMonthResults` | `BOT_GUARD_POOL_MONTH_RESULTS` | 3000 | All limited users of this Actor together, this month |
| `daily_share` | `dailyShare` | `BOT_GUARD_DAILY_SHARE` | 0.34 | Daily cap per user and Actor, as a share of `actor_month_results` |

Windows, all in UTC:

- **month**: the calendar month. Everything starts again on the 1st.
- **week**: a rolling 7 days, today and the 6 days before. It looks into last month when needed, so
  there is no reset on the 1st. Room comes back as old runs leave the window.
- **day**: the calendar day.
- **run**: this run only. Another run gets a fresh run limit (the other limits still apply).

**"This Actor"** means the scope: the name you pass to `admit()` / `open()`, or `BOT_GUARD_SCOPE` when
set. Give several Actors the same scope to make them share one allowance and one pool.

**"Every Actor"** means every scope that uses the same store: same Apify key-value store, or same Redis
with the same key prefix.

**The pool** is released evenly through the month: on day 10 of a 30-day month, a third of
`pool_month_results` is available to all limited users together, minus what they already took. A farm
of many accounts can never take more than what has been released so far, however many accounts it
has. The pool is counted per scope.

**Cost** (`*_usd`) is what you pass to `close(usd)` when the run ends, or else
`results * cost_per_result`. Mid-run, `take(n, cost_so_far)` stops delivery once the run's own cost
reaches what the cost limits leave. Set `cost_per_result` (`BOT_GUARD_COST_PER_RESULT`, default 0.0005)
to your real average cost per result: it turns cost limits into result counts at the start of a run.

## How the limits combine

At the start of a run the guard works out, for every limit that is set, how many results are left,
and takes the smallest. That number is `remaining`; the limit it came from is `bound`.

| `bound` | Meaning | `resets_on` / `resetsOn` | Default message says more is available |
|---|---|---|---|
| `month` | A monthly limit | The 1st of next month | on that day |
| `week` | A weekly limit | The day the oldest run in the window leaves it | on that day |
| `day` | A daily limit or `daily_share` | Tomorrow | tomorrow (UTC) |
| `run` | `run_results` or `run_usd` | Today | in a new run |
| `pool` | The pool | Tomorrow (more is released each day) | tomorrow (UTC) |
| `blocked` | The user is in `blocked_users` | none | (blocked message) |

When two limits leave the same number, the earlier one in the table is reported.

The amount is booked at once as a **hold**, so a second run started meanwhile sees it as used and
parallel runs cannot each take the whole allowance. At the end, `close()` books what the run really
delivered and cost, replacing the hold. A hold left by a run that crashed before `close()` stops
counting the next day.

## New accounts

Fresh accounts are what farms are made of, so they get a smaller share for their first days.

| Setting | JavaScript | Env var | Default |
|---|---|---|---|
| `new_account_days` | `newAccountDays` | `BOT_GUARD_NEW_ACCOUNT_DAYS` | 7 |
| `new_account_share` | `newAccountShare` | `BOT_GUARD_NEW_ACCOUNT_SHARE` | 0.25 |
| `track_from` | `trackFrom` | `BOT_GUARD_TRACK_FROM` | none |

The share scales every `actor_*` and `user_*` limit (and `daily_share`), not the run limits or the
pool. Set `new_account_share` to 1 to treat new accounts like any other.

Account age comes from `account_created` / `accountCreated` when you pass it (any ISO date). Otherwise
the day the user was first seen by any Actor sharing the store stands in. When you start using the
guard, every existing user would look new; set `track_from` to that day (`YYYY-MM-DD`) and users first
seen before it are not treated as new.

## Policy settings

| Setting | JavaScript | Env var | Default | What it does |
|---|---|---|---|---|
| `plans` | `plans` | `BOT_GUARD_PLANS` | `FREE` | Plans that are limited, e.g. `FREE,STARTER` |
| `exempt_users` | `exemptUsers` | `BOT_GUARD_EXEMPT_USERS` | none | User ids never limited: you, your testers, partners |
| `blocked_users` | `blockedUsers` | `BOT_GUARD_BLOCKED_USERS` | none | User ids refused at once (on a limited plan) |
| `mode` | `mode` | `BOT_GUARD_MODE` | `enforce` | `observe`: measure, book and log, never cut |
| `message` | `message` | `BOT_GUARD_MESSAGE` | built in | Your own status text (below) |
| `salt` | `salt` | `BOT_GUARD_SALT` | empty | Mixed into the stored user hash |
| `auto_prune` | `autoPrune` | `BOT_GUARD_AUTO_PRUNE` | 0.02 | Chance per run that `close()` deletes old ledgers; 0 = off |
| | | `BOT_GUARD_SCOPE` | the scope passed | Book under another scope name |
| | | `BOT_GUARD` | on | `off` turns the guard off |

**Observe mode** is the safe way to start: the guard computes and books everything as usual, logs when
a run goes past its allowance (`guard.over` becomes true), but `take()` always returns what you asked
for and `decision.allowed` is always true. Look at the logs for a week, tune the limits, then switch
`BOT_GUARD_MODE` to `enforce`.

**Blocked users** are checked after the plan: a blocked user who pays is not blocked. Nothing is read or
written for them.

**Messages** may use `{delivered}`, `{when}` (for example `tomorrow (UTC)`), `{resets_on}` and
`{bound}`:

```
BOT_GUARD_MESSAGE=Free limit reached after {delivered} results. More {when}, or upgrade for unlimited use.
```

Built-in texts:

- `Free plan limit reached: {delivered} results this run. More are available {when}, or right away on a paid plan.`
- Blocked: `Free use of this Actor is not available for this account. It works on a paid plan.`

**Salt**: user ids are stored as `sha256("bot-guard:<salt>:<userId>")`, first 32 hex characters. A salt
makes the stored hashes useless to anyone who knows a user id. Changing it starts every user afresh, so
pick it once. All Actors sharing a store must use the same salt.

## Identity: what each run needs

| Field | Python | JavaScript | On Apify | Elsewhere |
|---|---|---|---|---|
| Scope | first argument | first argument | required | required |
| Backend | second argument | second argument | required | required |
| User id | `user_id` | `userId` | `APIFY_USER_ID` | **required** (no id: not limited) |
| Plan | `plan` or `free` | `plan` or `free` | `APIFY_USER_PRICING_TIER` / `APIFY_USER_IS_PAYING` | **required** (unknown: not limited) |
| Run id | `run_id` | `runId` | `ACTOR_RUN_ID` | optional; a random id otherwise |
| Account creation date | `account_created` | `accountCreated` | optional | optional |
| Today (UTC, `YYYY-MM-DD`) | `today` | `today` | optional (tests) | optional (tests) |
| Env | `env` | `env` | `os.environ` / `process.env` | optional |
| Logger | `logger` | `log` | module logger / `console` | optional |

The run id matters: holds and bookings are keyed by it, so two calls with the same run id are the same
run. Use a request id or job id outside Apify.

## Backend options

| Backend | Options |
|---|---|
| `ApifyKvBackend` | `token` (your token, never the user's), `store` (name of the key-value store, default `bot-guard`, created on first use), `client` (your own `ApifyClientAsync` / `ApifyClient`), `api_url` / `baseUrl` |
| `RedisBackend` | the client (redis-py asyncio, `upstash_redis.asyncio`, ioredis, node-redis v4+, `@upstash/redis`), `prefix` (default `bot-guard:`); Python `pipelined`, JavaScript `flavor` to force detection |
| `MemoryBackend` | none (one process only; tests and local runs) |

Two different stores (or prefixes) never see each other's users: use that to keep separate products
apart.

## What the guard does when something is wrong

| Situation | Result |
|---|---|
| Store cannot be read | No limit this run, warning logged |
| Hold or booking cannot be written | Run continues, warning logged |
| Setting value unreadable | Ignored, warning logged |
| Setting name unknown (in code) | Error at once |
| Plan or user id unknown | Not limited |
| Run crashes before `close()` | Its hold stops counting the next day |
| Account creation date unreadable | Treated as an established account (full share) |

## Recipes

**Generous monthly, steady daily** (default style):

```
BOT_GUARD_ACTOR_MONTH_RESULTS=300
BOT_GUARD_DAILY_SHARE=0.2
```

**Weekly quota instead of monthly**:

```
BOT_GUARD_ACTOR_MONTH_RESULTS=none
BOT_GUARD_ACTOR_WEEK_RESULTS=100
```

**Budget first**: cap what free users may cost you, whatever the result count:

```
BOT_GUARD_ACTOR_MONTH_RESULTS=none
BOT_GUARD_USER_MONTH_RESULTS=none
BOT_GUARD_ACTOR_MONTH_USD=0.10
BOT_GUARD_USER_DAY_USD=0.02
BOT_GUARD_COST_PER_RESULT=0.0008
```

**Small runs only**: any number of runs, each short:

```
BOT_GUARD_RUN_RESULTS=25
```

**Limit the cheapest paid plan too**:

```
BOT_GUARD_PLANS=FREE,STARTER
```

**Try it without affecting anyone**:

```
BOT_GUARD_MODE=observe
```

**Several Actors, one allowance**: set the same `BOT_GUARD_SCOPE` on each, and the same store.
