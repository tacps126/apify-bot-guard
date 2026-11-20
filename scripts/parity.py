"""Check that the Python and JavaScript packages agree: same user hashes and the
same allowance for a grid of ledgers, limits and days. Needs python3 and node."""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from apify_bot_guard import Config, Ledger, allowance_of, user_hash  # noqa: E402
from apify_bot_guard.core import previous_month, week_reset  # noqa: E402

DAYS = ["2026-02-01", "2026-02-28", "2024-02-29", "2026-11-20", "2026-12-03", "2026-12-31"]
USED = [0, 33, 34, 99, 100, 301]
SHARES = [1.0, 0.25]
POOL = [0, 50, 2999]
COSTS = [0.0005, 0.0017]
# Defaults, then each other control: weekly (with last month), absolute daily, per run, by price, off.
CONFIGS: list[dict[str, object]] = [
    {},
    {"daily_share": None, "actor_week_results": 70, "user_week_usd": 0.03},
    {"actor_day_results": 20, "user_day_usd": 0.004, "run_results": 15},
    {"run_usd": 0.004, "pool_month_results": None, "actor_month_usd": None, "user_week_results": 120},
]


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def cases() -> list[dict[str, object]]:
    out = []
    for cfg, day, used, share, pool, cost in itertools.product(CONFIGS, DAYS, USED, SHARES, POOL, COSTS):
        prev = previous_month(day[:7])
        out.append(
            {
                "cfg": {**cfg, "cost_per_result": cost},
                "day": day,
                "share": share,
                "user": {
                    "a": {"a": "s", "d": day, "n": used, "usd": used * cost},
                    "b": {"a": "t", "d": day, "n": used // 2},
                    "c": {"a": "s", "d": f"{day[:8]}01", "n": used // 3, "usd": 0.001},
                },
                "last": {"l": {"a": "s", "d": f"{prev}-28", "n": used // 4, "usd": 0.002}},
                "pool": {"p": {"d": day, "n": pool}},
            }
        )
    return out


def python_side(cs: list[dict[str, object]]) -> list[list[object]]:
    res = []
    for c in cs:
        cfg = Config(scope="s", **c["cfg"])  # type: ignore[arg-type]
        user, pool, last = (Ledger.from_raw(c[k]) for k in ("user", "pool", "last"))
        a = allowance_of(cfg, user, pool, c["day"], c["share"], last_month=last)  # type: ignore[arg-type]
        usd = None if a.usd_left is None else round(a.usd_left, 9)
        res.append([a.results_left, usd, a.bound, week_reset(user, last, a.week_start)])
    return res


JS = """
import { Ledger, allowanceOf, makeConfig, userHash } from './js/src/index.js';
import { weekReset } from './js/src/core.js';
import { readFileSync } from 'node:fs';
// Cases come on stdin: as an argument they pass Linux's per-argument size limit.
const cs = JSON.parse(readFileSync(0, 'utf8'));
const out = cs.map((c) => {
  const cfg = makeConfig('s', c.cfg);
  const [user, pool, last] = [c.user, c.pool, c.last].map((r) => Ledger.fromRaw(r));
  const a = allowanceOf(cfg, user, pool, c.day, c.share, { lastMonth: last });
  const usd = a.usdLeft === null ? null : Math.round(a.usdLeft * 1e9) / 1e9;
  return [a.resultsLeft, usd, a.bound, weekReset(user, last, a.weekStart)];
});
const hashes = ['u1', 'acct-9', 'ü'].map((u) => userHash(u)).concat([userHash('u1', 'salt')]);
console.log(JSON.stringify({ out, hashes }));
"""


def main() -> int:
    cs = cases()
    js_cases = [dict(c, cfg={camel(k): v for k, v in c["cfg"].items()}) for c in cs]  # type: ignore[union-attr]
    js = subprocess.run(
        ["node", "--input-type=module", "-e", JS],
        input=json.dumps(js_cases),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    got = json.loads(js.stdout)
    want_hashes = [user_hash(u) for u in ["u1", "acct-9", "ü"]] + [user_hash("u1", "salt")]
    bad = [i for i, (p, j) in enumerate(zip(python_side(cs), got["out"], strict=True)) if p != j]
    if got["hashes"] != want_hashes:
        print("user hashes differ:", got["hashes"], want_hashes)
        return 1
    if bad:
        for i in bad[:10]:
            print("differs:", cs[i], python_side([cs[i]])[0], got["out"][i])
        return 1
    print(f"parity ok: {len(cs)} allowance cases, {len(want_hashes)} hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
