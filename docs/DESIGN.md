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

With `share` = `newAccountShare` for accounts younger than `newAccountDays`, else 1:

```
usdLeft  = max(0, min(actorUsd*share - user.spentOn(scope), userUsd*share - user.spent()))
month    = min(actorResults*share - user.resultsOn(scope), userResults*share - user.results(), usdLeft / costPerResult)
day      = ceil(actorResults*share*dailyShare) - user.resultsOnDay(scope, today)
poolLeft = ceil(poolResults * dayOfMonth / daysInMonth) - pool.results()
results  = max(0, floor(min(month, day, poolLeft) + 1e-6))
bound    = whichever of month, day, pool is smallest; ties keep month, then day
```

Holds from an earlier day are dropped before computing (a run that crashed before booking must not
block anyone the next day). Holds from today count.

Account age: the creation date when known; else the first-seen day, ignored when earlier than
`trackFrom` (users who were around before you started tracking are not new).

## Run lifecycle

1. Read both ledgers and, if needed, first-seen. Any read error: no guard (fail open).
2. If anything is left, write a hold of `results` (and `usdLeft`) to both ledgers under the run id.
   Backends that merge whole records may merge into the ledgers just read instead of re-reading.
3. `take(n)` returns `min(n, remaining)`, or 0 once the caller's measured cost reaches `usdLeft`.
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
