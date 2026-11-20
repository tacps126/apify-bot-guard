"""Pure allowance logic: no I/O, identical in the Python and JavaScript packages."""

from __future__ import annotations

import calendar
import hashlib
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Mapping, Optional

DEFAULTS = {
    "actor_results": 100.0,  # per user per Actor per month
    "actor_usd": 0.05,  # our cost per user per Actor per month
    "user_results": 300.0,  # per user across all Actors sharing the backend
    "user_usd": 0.25,
    "daily_share": 0.34,  # share of the monthly allowance usable in one day
    "pool_results": 3000.0,  # all free users of one Actor together, per month
    "new_account_days": 7.0,
    "new_account_share": 0.25,
    "cost_per_result": 0.0005,  # USD, what one result costs you
}

ENV_NAMES = {
    "actor_results": "FREE_ACTOR_MONTHLY_RESULTS",
    "actor_usd": "FREE_ACTOR_MONTHLY_USD",
    "user_results": "FREE_USER_MONTHLY_RESULTS",
    "user_usd": "FREE_USER_MONTHLY_USD",
    "daily_share": "FREE_DAILY_SHARE",
    "pool_results": "FREE_POOL_MONTHLY_RESULTS",
    "new_account_days": "FREE_NEW_ACCOUNT_DAYS",
    "new_account_share": "FREE_NEW_ACCOUNT_SHARE",
    "cost_per_result": "FREE_COST_PER_RESULT",
}


@dataclass
class Config:
    scope: str
    actor_results: float = DEFAULTS["actor_results"]
    actor_usd: float = DEFAULTS["actor_usd"]
    user_results: float = DEFAULTS["user_results"]
    user_usd: float = DEFAULTS["user_usd"]
    daily_share: float = DEFAULTS["daily_share"]
    pool_results: float = DEFAULTS["pool_results"]
    new_account_days: float = DEFAULTS["new_account_days"]
    new_account_share: float = DEFAULTS["new_account_share"]
    cost_per_result: float = DEFAULTS["cost_per_result"]

    @classmethod
    def from_env(cls, scope: str, env: Optional[Mapping[str, str]] = None, **overrides) -> Optional["Config"]:
        """Limits from env vars (see ENV_NAMES), then keyword overrides.
        None when FREE_BUDGET=off."""
        env = os.environ if env is None else env
        if str(env.get("FREE_BUDGET", "")).strip().lower() in ("off", "0", "false"):
            return None
        values = {}
        for k, name in ENV_NAMES.items():
            try:
                v = float(str(env.get(name, "")).strip())
                if math.isfinite(v) and v >= 0:
                    values[k] = v
            except ValueError:
                pass
        values.update({k: float(v) for k, v in overrides.items() if v is not None})
        cfg = cls(scope=str(env.get("FREE_BUDGET_SCOPE") or scope), **values)
        cfg.daily_share = min(cfg.daily_share, 1.0)
        cfg.new_account_share = min(cfg.new_account_share, 1.0)
        cfg.cost_per_result = max(cfg.cost_per_result, 1e-7)
        return cfg


def is_free_plan(env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    """True on Apify's free plan, False when paying, None when unknown.

    APIFY_USER_PRICING_TIER (e.g. FREE) wins when set. APIFY_USER_IS_PAYING is
    "1" for paying users and set but EMPTY for free ones, so an empty value
    means free, not unknown.
    """
    env = os.environ if env is None else env
    tier = str(env.get("APIFY_USER_PRICING_TIER", "")).strip()
    if tier:
        return tier.upper() == "FREE"
    if "APIFY_USER_IS_PAYING" in env:
        return str(env["APIFY_USER_IS_PAYING"]).strip() != "1"
    return None


@dataclass
class Entry:
    """One run booked against an allowance."""

    a: str = ""  # Actor scope ("" in pool ledgers)
    d: str = ""  # day, YYYY-MM-DD
    n: int = 0  # results
    usd: float = 0.0  # our cost
    h: bool = False  # hold for a run still going

    def to_json(self) -> dict:
        out = {"a": self.a, "d": self.d, "n": self.n, "usd": round(self.usd, 6)}
        if self.h:
            out["h"] = True
        return out

    @classmethod
    def from_json(cls, v: Mapping) -> "Entry":
        return cls(
            a=str(v.get("a", "")),
            d=str(v.get("d", "")),
            n=int(v.get("n", 0) or 0),
            usd=float(v.get("usd", 0) or 0),
            h=bool(v.get("h", False)),
        )


@dataclass
class Ledger:
    runs: dict = field(default_factory=dict)  # run id -> Entry

    def drop_stale_holds(self, today: str) -> None:
        self.runs = {k: e for k, e in self.runs.items() if not e.h or e.d == today}

    def _sum(self, keep: Callable[[Entry], bool], of: Callable[[Entry], float]) -> float:
        return sum(of(e) for e in self.runs.values() if keep(e))

    def results_on(self, scope: str) -> float:
        return self._sum(lambda e: e.a == scope, lambda e: e.n)

    def results_on_day(self, scope: str, day: str) -> float:
        return self._sum(lambda e: e.a == scope and e.d == day, lambda e: e.n)

    def results(self) -> float:
        return self._sum(lambda e: True, lambda e: e.n)

    def spent_on(self, scope: str) -> float:
        return self._sum(lambda e: e.a == scope, lambda e: e.usd)

    def spent(self) -> float:
        return self._sum(lambda e: True, lambda e: e.usd)


@dataclass
class Allowance:
    results_left: int
    usd_left: float
    bound: str  # "month" | "day" | "pool"

    @property
    def exhausted(self) -> bool:
        return self.results_left == 0


def day_of_month(today: str) -> tuple:
    try:
        d = date.fromisoformat(today)
    except ValueError:
        return 30, 30
    return d.day, calendar.monthrange(d.year, d.month)[1]


def allowance_of(cfg: Config, user: Ledger, pool: Ledger, today: str, share: float = 1.0) -> Allowance:
    s = cfg.scope
    usd_left = max(
        0.0,
        min(cfg.actor_usd * share - user.spent_on(s), cfg.user_usd * share - user.spent()),
    )
    month = min(
        cfg.actor_results * share - user.results_on(s),
        cfg.user_results * share - user.results(),
        usd_left / cfg.cost_per_result,
    )
    day = math.ceil(cfg.actor_results * share * cfg.daily_share) - user.results_on_day(s, today)
    d, days = day_of_month(today)
    pool_left = math.ceil(cfg.pool_results * d / days) - pool.results()
    best = (month, "month")
    for c in ((day, "day"), (pool_left, "pool")):
        if c[0] < best[0]:
            best = c
    return Allowance(
        results_left=max(0, math.floor(best[0] + 1e-6)),
        usd_left=usd_left,
        bound=best[1],
    )


def account_share(cfg: Config, created: Optional[str], today: str) -> float:
    if not created:
        return 1.0
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
        t = date.fromisoformat(today)
    except ValueError:
        return 1.0
    return cfg.new_account_share if (t - c).days < cfg.new_account_days else 1.0


def user_hash(user_id: str, salt: str = "") -> str:
    """Store keys never hold raw user ids."""
    return hashlib.sha256(f"free-guard:{salt}:{user_id}".encode()).hexdigest()[:32]


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def next_reset(month: str) -> str:
    y, m = (int(x) for x in month.split("-")[:2])
    y, m = (y + 1, 1) if m >= 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-01"
