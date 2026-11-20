# Design and storage format

This file is the spec both packages follow. A port to another language, or a new backend, should match
it exactly so every implementation can share one store.

## Records

All dates are UTC. `month` is `YYYY-MM`, `day` is `YYYY-MM-DD`.

| Key | Holds |
|---|---|
| `user.<month>.<hash>` | The user's free runs this month, across every scope |
| `pool.<month>.<scope>` | All free runs of one scope this month |
| `seen.<hash>` | The first day the user was seen (only when the account's creation date is unknown) |

`hash` = first 32 hex characters of `sha256("bot-guard:<salt>:<userId>")`. Salt defaults to empty.

A ledger is a map of run id to entry:

```json
{ "v": 1, "runs": { "<runId>": { "a": "my-actor", "d": "2026-11-20", "n": 34, "usd": 0.017, "h": true } } }
```

| Field | Meaning |
|---|---|
| `a` | Scope the run was booked under (empty in pool ledgers) |
| `d` | Day of the run |
| `n` | Results delivered (or, for a hold, results the run may take) |
| `usd` | Your cost, 6 decimals (0 in pool ledgers) |
| `h` | Present and `true` only for a hold |

Readers accept a bare `{ "<runId>": {...} }` map too, and skip entries they cannot read.

## Allowance

Settings and their defaults are in [CONFIGURATION.md](CONFIGURATION.md). Unset limits (null) are
skipped. With `share` = `newAccountShare` for accounts younger than `newAccountDays`, else 1, and
`used(scope, since)` = results and USD of the user's runs of `scope` (all scopes for `user*` limits) on
or after day `since`:

```
for window in month, week, day:              # since: "" (whole month), today - 6 days, today
  for who in actor, user:
    resultsLeft = <who><Window>Results * share - used.results
    usdLeft     = max(0, <who><Window>Usd * share - used.usd)    # also kept for the cost guard
    candidate   = min(resultsLeft, usdLeft / costPerResult)
  day also: ceil(actorMonthResults * share * dailyShare) - used(scope, today).results
run      = min(runResults, runUsd / costPerResult)
pool     = ceil(poolMonthResults * dayOfMonth / daysInMonth) - pool.results()
results  = max(0, floor(min(month, week, day, run, pool) + 1e-6))
bound    = the smallest; ties go to the earlier of month, week, day, run, pool
usdLeft  = min of every usd limit left, and runUsd; null when no usd limit is set
```

The week is rolling: today and the 6 days before. On days 1 to 6 of a month, when a weekly limit is
set, last month's user ledger (`user.<last month>.<hash>`) is read too and merged for the week only.
The week's reset day is the day the oldest run in the window (with results) leaves it, + 7 days.

Holds from an earlier day are dropped before computing (a run that crashed before booking must not
block anyone the next day). Holds from today count.

Account age: the creation date when known; else the first-seen day, ignored when earlier than
`trackFrom` (users who were around before you started tracking are not new).

Before any of this: users in `exemptUsers` and users whose plan is not in `plans` are not limited
(nothing is read); users in `blockedUsers` get 0 with bound `blocked` (nothing is read or written).

## Run lifecycle

1. Read both ledgers (plus last month's user ledger for a rolling week) and, if needed, first-seen.
   Any read error: no guard (fail open).
2. If anything is left, write a hold of `results` (and `usdLeft`) to both ledgers under the run id.
   Backends that merge whole records may merge into the ledgers just read instead of re-reading.
3. `take(n)` returns `min(n, remaining)`, or 0 once the caller's measured cost reaches `usdLeft`. In
   observe mode it returns `n` and only records that the run went over.
4. `close()` writes the delivered amount under the same run id, replacing the hold. Re-read before
   merging. Errors are logged, never raised. With chance `auto_prune` it then prunes ledgers older
   than last month (this month and last are kept, so holds and late bookings are never lost).

## Writing a backend

Four async methods:

| Python | JavaScript | Contract |
|---|---|---|
| `get_ledgers(keys)` | `getLedgers(keys)` | Ledgers in key order; empty for missing keys; one round trip if the store allows |
| `put_entries(puts, known)` | `putEntries(puts, known)` | Set entry `(key, runId)`; keep other runs' entries; expire after about 45 days |
| `first_seen(hash, today)` | `firstSeen(hash, today)` | Return the stored day, storing `today` if none (set-if-absent) |
| `prune(before_month)` | `prune(beforeMonth)` | Delete `user.*` and `pool.*` ledgers of months before `before_month`; return how many. Stores with expiry may return 0 |

Stores with per-field writes (Redis hashes, SQL rows keyed by `(key, run_id)`, DynamoDB items) give
exact counts. Stores that only replace whole values (key-value stores) must read, merge and write;
the hold limits the damage of a lost update to one run's share.

Add the backend to both packages, run the shared scenarios (`test_guard.py` / `guard.test.js`) against
it with a fake client, and document the round trips.
