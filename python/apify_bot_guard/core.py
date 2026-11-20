"""Pure allowance logic. No I/O; kept identical to the JavaScript package."""

from __future__ import annotations

import calendar
import hashlib
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

DEFAULTS: dict[str, float] = {
    "actor_results": 100.0,
    "actor_usd": 0.05,
    "user_results": 300.0,
    "user_usd": 0.25,
    "daily_share": 0.34,
    "pool_results": 3000.0,
    "new_account_days": 7.0,
    "new_account_share": 0.25,
    "cost_per_result": 0.0005,
}

ENV_NAMES: dict[str, str] = {
    "actor_results": "BOT_GUARD_ACTOR_MONTHLY_RESULTS",
    "actor_usd": "BOT_GUARD_ACTOR_MONTHLY_USD",
    "user_results": "BOT_GUARD_USER_MONTHLY_RESULTS",
    "user_usd": "BOT_GUARD_USER_MONTHLY_USD",
    "daily_share": "BOT_GUARD_DAILY_SHARE",
    "pool_results": "BOT_GUARD_POOL_MONTHLY_RESULTS",
    "new_account_days": "BOT_GUARD_NEW_ACCOUNT_DAYS",
    "new_account_share": "BOT_GUARD_NEW_ACCOUNT_SHARE",
    "cost_per_result": "BOT_GUARD_COST_PER_RESULT",
}

Env = Mapping[str, str]


@dataclass
class Config:
    """Limits. `scope` is the name per-Actor limits and the pool are booked under."""

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

    def __post_init__(self) -> None:
        self.daily_share = min(self.daily_share, 1.0)
        self.new_account_share = min(self.new_account_share, 1.0)
        self.cost_per_result = max(self.cost_per_result, 1e-7)

    @classmethod
    def from_env(cls, scope: str, env: Env | None = None, **overrides: float) -> Config | None:
        """Limits from env vars (see ENV_NAMES), then keyword overrides.
        None when BOT_GUARD is off, 0 or false."""
        env = os.environ if env is None else env
        if str(env.get("BOT_GUARD", "")).strip().lower() in ("off", "0", "false"):
            return None
        values: dict[str, float] = {}
        for k, name in ENV_NAMES.items():
            try:
                v = float(str(env.get(name, "")).strip())
            except ValueError:
                continue
            if math.isfinite(v) and v >= 0:
                values[k] = v
        values.update({k: float(v) for k, v in overrides.items() if v is not None})
        return cls(scope=str(env.get("BOT_GUARD_SCOPE") or scope), **values)


def is_free_plan(env: Env | None = None) -> bool | None:
    """True on Apify's free plan, False when paying, None when unknown.

    APIFY_USER_PRICING_TIER (FREE, STARTER, ...) wins when set.
    APIFY_USER_IS_PAYING is "1" for paying users and set but EMPTY for free
    ones, so an empty value means free, not unknown.
    """
    env = os.environ if env is None else env
    tier = str(env.get("APIFY_USER_PRICING_TIER", "")).strip()
    if tier:
        return tier.upper() == "FREE"
    if "APIFY_USER_IS_PAYING" in env:
        return str(env["APIFY_USER_IS_PAYING"]).strip() != "1"
    return None


def _num(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


@dataclass
class Entry:
    """One run booked against an allowance."""

    a: str = ""  # scope ("" in pool ledgers)
    d: str = ""  # day, YYYY-MM-DD
    n: int = 0  # results
    usd: float = 0.0  # your cost
    h: bool = False  # hold for a run still going

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"a": self.a, "d": self.d, "n": self.n, "usd": round(self.usd, 6)}
        if self.h:
            out["h"] = True
        return out

    @classmethod
    def from_json(cls, v: Mapping[str, object]) -> Entry:
        return cls(
            a=str(v.get("a", "")),
            d=str(v.get("d", "")),
            n=int(_num(v.get("n"))),
            usd=_num(v.get("usd")),
            h=v.get("h") is True,
        )


@dataclass
class Ledger:
    """Runs in one month: one user's runs, or all free runs of one scope."""

    runs: dict[str, Entry] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> Ledger:
        """From a stored record: {"runs": {...}} or a bare {run id: entry}."""
        if not isinstance(raw, Mapping):
            return cls()
        runs = raw.get("runs", raw)
        if not isinstance(runs, Mapping):
            return cls()
        return cls({str(k): Entry.from_json(v) for k, v in runs.items() if isinstance(v, Mapping)})

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


@dataclass(frozen=True)
class Allowance:
    results_left: int
    usd_left: float
    bound: str  # "month" | "day" | "pool"

    @property
    def exhausted(self) -> bool:
        return self.results_left == 0


def day_of_month(today: str) -> tuple[int, int]:
    try:
        d = date.fromisoformat(today)
    except ValueError:
        return 30, 30
    return d.day, calendar.monthrange(d.year, d.month)[1]


def allowance_of(cfg: Config, user: Ledger, pool: Ledger, today: str, share: float = 1.0) -> Allowance:
    """What a run may still take. The tightest limit wins; ties keep "month"."""
    s = cfg.scope
    usd_left = max(0.0, min(cfg.actor_usd * share - user.spent_on(s), cfg.user_usd * share - user.spent()))
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
    # A hair of slack so 0.05 / 0.0005 is 100, not 99.
    return Allowance(max(0, math.floor(best[0] + 1e-6)), usd_left, best[1])


def account_share(cfg: Config, created: str | None, today: str) -> float:
    """Share of the per-user limits for an account created on `created`."""
    if not created:
        return 1.0
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
        t = date.fromisoformat(today)
    except ValueError:
        return 1.0
    return cfg.new_account_share if (t - c).days < cfg.new_account_days else 1.0


def user_hash(user_id: str, salt: str = "") -> str:
    """Store keys never hold raw user ids. Same value in the JavaScript package."""
    return hashlib.sha256(f"bot-guard:{salt}:{user_id}".encode()).hexdigest()[:32]


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def next_reset(month: str) -> str:
    y, m = (int(x) for x in month.split("-")[:2])
    y, m = (y + 1, 1) if m >= 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-01"


def next_day(today: str) -> str:
    return date.fromordinal(date.fromisoformat(today).toordinal() + 1).isoformat()


def previous_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-")[:2])
    y, m = (y - 1, 12) if m <= 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


_LEDGER_KEY = re.compile(r"^(?:user|pool)\.(\d{4}-\d{2})\.")


def ledger_month(key: str) -> str | None:
    """`2026-11` for a ledger key like `user.2026-11.<hash>`; None for other keys."""
    m = _LEDGER_KEY.match(key)
    return m.group(1) if m else None
