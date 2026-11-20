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

DAYS = ["2026-02-01", "2026-02-28", "2024-02-29", "2026-11-20", "2026-12-31"]
USED = [0, 33, 34, 99, 100, 301]
SHARES = [1.0, 0.25]
POOL = [0, 50, 2999]
COSTS = [0.0005, 0.0017]


def cases() -> list[dict[str, object]]:
    out = []
    for day, used, share, pool, cost in itertools.product(DAYS, USED, SHARES, POOL, COSTS):
        out.append(
            {
                "day": day,
                "share": share,
                "cost": cost,
                "user": {
                    "a": {"a": "s", "d": day, "n": used, "usd": used * cost},
                    "b": {"a": "t", "d": day, "n": used // 2},
                },
                "pool": {"p": {"d": day, "n": pool}},
            }
        )
    return out


def python_side(cs: list[dict[str, object]]) -> list[list[object]]:
    res = []
    for c in cs:
        cfg = Config(scope="s", cost_per_result=c["cost"])  # type: ignore[arg-type]
        a = allowance_of(cfg, Ledger.from_raw(c["user"]), Ledger.from_raw(c["pool"]), c["day"], c["share"])  # type: ignore[arg-type]
        res.append([a.results_left, round(a.usd_left, 9), a.bound])
    return res


JS = """
import { Ledger, allowanceOf, makeConfig, userHash } from './js/src/index.js';
const cs = JSON.parse(process.argv[1]);
const out = cs.map((c) => {
  const cfg = makeConfig('s', { costPerResult: c.cost });
  const a = allowanceOf(cfg, Ledger.fromRaw(c.user), Ledger.fromRaw(c.pool), c.day, c.share);
  return [a.resultsLeft, Math.round(a.usdLeft * 1e9) / 1e9, a.bound];
});
const hashes = ['u1', 'acct-9', 'ü'].map((u) => userHash(u)).concat([userHash('u1', 'salt')]);
console.log(JSON.stringify({ out, hashes }));
"""


def main() -> int:
    cs = cases()
    js = subprocess.run(
        ["node", "--input-type=module", "-e", JS, json.dumps(cs)], cwd=ROOT, capture_output=True, text=True, check=True
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
