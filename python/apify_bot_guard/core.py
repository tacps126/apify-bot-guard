"""Pure allowance logic. No I/O; kept identical to the JavaScript package (docs/DESIGN.md)."""

from __future__ import annotations

import calendar
import hashlib
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone

Env = Mapping[str, str]

# Limits, in the order ties are broken. Each is a number (0 allowed) or None (no limit).
# "actor_*" count one user's runs of this scope; "user_*" count one user's runs of every scope
# sharing the backend; "run_*" cap a single run; "pool_*" count every limited user together.
WINDOWS = ("month", "week", "day")
_GRID = tuple(f"{who}_{w}_{unit}" for w in WINDOWS for who in ("actor", "user") for unit in ("results", "usd"))
LIMIT_FIELDS = (*_GRID, "run_results", "run_usd", "pool_month_results")

DEFAULTS: dict[str, float | None] = {
    "actor_month_results": 100.0,
    "actor_month_usd": 0.05,
    "user_month_results": 300.0,
    "user_month_usd": 0.25,
    "pool_month_results": 3000.0,
    "daily_share": 0.34,
    "new_account_days": 7.0,
    "new_account_share": 0.25,
    "cost_per_result": 0.0005,
    "auto_prune": 0.02,
}

NUMBER_FIELDS = (*LIMIT_FIELDS, "daily_share", "new_account_days", "new_account_share", "cost_per_result", "auto_prune")
LIST_FIELDS = ("plans", "exempt_users", "blocked_users")
TEXT_FIELDS = ("mode", "message", "salt", "track_from")
NO_LIMIT = {"none", "off", "unlimited", "no", "-"}
MODES = ("enforce", "observe")


def env_name(name: str) -> str:
    """`actor_week_results` -> `BOT_GUARD_ACTOR_WEEK_RESULTS`."""
    return f"BOT_GUARD_{name.upper()}"


@dataclass
class Config:
    """Every control the guard has. Unset limits (None) do not apply. See docs/CONFIGURATION.md."""

    scope: str
    actor_month_results: float | None = DEFAULTS["actor_month_results"]
    actor_month_usd: float | None = DEFAULTS["actor_month_usd"]
    user_month_results: float | None = DEFAULTS["user_month_results"]
    user_month_usd: float | None = DEFAULTS["user_month_usd"]
    actor_week_results: float | None = None
    actor_week_usd: float | None = None
    user_week_results: float | None = None
    user_week_usd: float | None = None
    actor_day_results: float | None = None
    actor_day_usd: float | None = None
    user_day_results: float | None = None
    user_day_usd: float | None = None
    run_results: float | None = None
    run_usd: float | None = None
    pool_month_results: float | None = DEFAULTS["pool_month_results"]
    daily_share: float | None = DEFAULTS["daily_share"]
    new_account_days: float | None = DEFAULTS["new_account_days"]
    new_account_share: float | None = DEFAULTS["new_account_share"]
    cost_per_result: float | None = DEFAULTS["cost_per_result"]
    plans: tuple[str, ...] = ("FREE",)
    exempt_users: frozenset[str] = frozenset()
    blocked_users: frozenset[str] = frozenset()
    mode: str = "enforce"
    message: str | None = None
    salt: str = ""
    track_from: str | None = None
    auto_prune: float | None = DEFAULTS["auto_prune"]
    problems: list[str] = field(default_factory=list, compare=False, repr=False)

    def __post_init__(self) -> None:
        for name in NUMBER_FIELDS:
            v = getattr(self, name)
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
            if v is not None and not ok:
                self.problems.append(f"{name}: {v!r} is not a number >= 0; using no limit")
                setattr(self, name, None)
        if self.daily_share is not None:
            self.daily_share = min(self.daily_share, 1.0)
        self.new_account_share = 1.0 if self.new_account_share is None else min(self.new_account_share, 1.0)
        self.new_account_days = self.new_account_days or 0.0
        self.cost_per_result = max(self.cost_per_result or 0.0, 1e-7)
        self.auto_prune = min(self.auto_prune or 0.0, 1.0)
        self.plans = tuple(p.strip().upper() for p in self.plans if p.strip())
        self.exempt_users = frozenset(u.strip() for u in self.exempt_users if u.strip())
        self.blocked_users = frozenset(u.strip() for u in self.blocked_users if u.strip())
        self.mode = (self.mode or "enforce").strip().lower()
        if self.mode not in MODES:
            self.problems.append(f"mode: {self.mode!r} is not enforce or observe; using enforce")
            self.mode = "enforce"

    @property
    def has_limits(self) -> bool:
        return any(getattr(self, n) is not None for n in LIMIT_FIELDS)

    @classmethod
    def from_env(cls, scope: str, env: Env | None = None, **overrides: object) -> Config | None:
        """Controls from `BOT_GUARD_*` env vars, then keyword overrides (which win).
        None when BOT_GUARD is off, 0 or false. Unreadable values are skipped and listed
        in `problems` (the guard logs them); they never stop a run."""
        env = os.environ if env is None else env
        if str(env.get("BOT_GUARD", "")).strip().lower() in ("off", "0", "false"):
            return None
        values, problems = _read_env(env)
        known = {f.name for f in fields(cls)} - {"problems", "scope"}
        for k, v in overrides.items():
            if k not in known:
                raise TypeError(f"unknown bot-guard setting: {k}")
            values[k] = _as_list(k, v)
        cfg = cls(scope=str(env.get("BOT_GUARD_SCOPE") or scope), **values)  # type: ignore[arg-type]
        cfg.problems[:0] = problems
        return cfg


def _as_list(name: str, v: object) -> object:
    if name not in LIST_FIELDS or v is None or isinstance(v, (tuple, frozenset)):
        return v
    items = [str(x) for x in v] if isinstance(v, Iterable) and not isinstance(v, str) else str(v).split(",")
    return tuple(items) if name == "plans" else frozenset(items)


def _read_env(env: Env) -> tuple[dict[str, object], list[str]]:
    values: dict[str, object] = {}
    problems: list[str] = []
    for name in NUMBER_FIELDS:
        raw = str(env.get(env_name(name), "")).strip()
        if not raw:
            continue
        if raw.lower() in NO_LIMIT:
            values[name] = None
            continue
        try:
            v = float(raw)
        except ValueError:
            v = math.nan
        if math.isfinite(v) and v >= 0:
            values[name] = v
        else:
            problems.append(f"{env_name(name)}={raw!r} is not a number >= 0; ignored")
    for name in LIST_FIELDS:
        raw = str(env.get(env_name(name), "")).strip()
        if raw:
            values[name] = _as_list(name, raw)
    for name in TEXT_FIELDS:
        raw = str(env.get(env_name(name), ""))
        if raw.strip():
            values[name] = raw if name == "message" else raw.strip()
    return values, problems


def plan_of(env: Env | None = None) -> str | None:
    """The user's plan on Apify, upper case: the pricing tier (FREE, STARTER, ...), else
    FREE or PAID from APIFY_USER_IS_PAYING ("1" for paying users, set but EMPTY for free
    ones). None when unknown."""
    env = os.environ if env is None else env
    tier = str(env.get("APIFY_USER_PRICING_TIER", "")).strip()
    if tier:
        return tier.upper()
    if "APIFY_USER_IS_PAYING" in env:
        return "PAID" if str(env["APIFY_USER_IS_PAYING"]).strip() == "1" else "FREE"
    return None


def is_free_plan(env: Env | None = None) -> bool | None:
    """True on Apify's free plan, False on any other plan, None when unknown."""
    plan = plan_of(env)
    return None if plan is None else plan == "FREE"


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
    """Runs in one month: one user's runs, or all limited runs of one scope."""

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

    @classmethod
    def merged(cls, ledgers: Iterable[Ledger]) -> Ledger:
        out = cls()
        for led in ledgers:
            out.runs.update(led.runs)
        return out

    def drop_stale_holds(self, today: str) -> None:
        self.runs = {k: e for k, e in self.runs.items() if not e.h or e.d == today}

    def _sum(self, keep: Callable[[Entry], bool], of: Callable[[Entry], float]) -> float:
        return sum(of(e) for e in self.runs.values() if keep(e))

    def used(self, scope: str | None = None, since: str = "") -> tuple[float, float]:
        """(results, usd) of runs on or after day `since`, for one scope or (None) all."""

        def keep(e: Entry) -> bool:
            return (scope is None or e.a == scope) and e.d >= since

        return self._sum(keep, lambda e: e.n), self._sum(keep, lambda e: e.usd)

    def results(self) -> float:
        return self.used()[0]


@dataclass(frozen=True)
class Allowance:
    results_left: int
    usd_left: float | None  # None: no cost limit applies
    bound: str  # "month" | "week" | "day" | "run" | "pool" | "blocked"
    week_start: str = ""  # first day of the rolling week (for the reset day)

    @property
    def exhausted(self) -> bool:
        return self.results_left == 0


def day_of_month(today: str) -> tuple[int, int]:
    try:
        d = date.fromisoformat(today)
    except ValueError:
        return 30, 30
    return d.day, calendar.monthrange(d.year, d.month)[1]


def shift_day(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def needs_last_month(cfg: Config, today: str) -> bool:
    """A rolling week that starts last month needs last month's user ledger too."""
    weekly = any(getattr(cfg, f"{who}_week_{u}") is not None for who in ("actor", "user") for u in ("results", "usd"))
    return weekly and day_of_month(today)[0] < 7


def allowance_of(
    cfg: Config, user: Ledger, pool: Ledger, today: str, share: float = 1.0, *, last_month: Ledger | None = None
) -> Allowance:
    """What a run may still take. The tightest limit wins; ties go to the earlier of
    month, week, day, run, pool. `share` scales the actor and user limits (new accounts).
    `last_month`: last month's user ledger, for a rolling week that starts there."""
    s = cfg.scope
    cost = float(cfg.cost_per_result or 1e-7)
    week_start = shift_day(today, -6)
    starts = {"month": "", "week": week_start, "day": today}
    ledgers = {"month": user, "week": Ledger.merged([last_month or Ledger(), user]), "day": user}
    usd_lefts: list[float] = []
    candidates: list[tuple[float, str]] = []
    for w in WINDOWS:
        best = math.inf
        for who, scope in (("actor", s), ("user", None)):
            used_n, used_usd = ledgers[w].used(scope, starts[w])
            lim_n = getattr(cfg, f"{who}_{w}_results")
            lim_usd = getattr(cfg, f"{who}_{w}_usd")
            if lim_n is not None:
                best = min(best, lim_n * share - used_n)
            if lim_usd is not None:
                left = max(0.0, lim_usd * share - used_usd)
                usd_lefts.append(left)
                best = min(best, left / cost)
        if w == "day" and cfg.daily_share is not None and cfg.actor_month_results is not None:
            best = min(best, math.ceil(cfg.actor_month_results * share * cfg.daily_share) - user.used(s, today)[0])
        candidates.append((best, w))
    run = math.inf
    if cfg.run_results is not None:
        run = cfg.run_results
    if cfg.run_usd is not None:
        usd_lefts.append(cfg.run_usd)
        run = min(run, cfg.run_usd / cost)
    candidates.append((run, "run"))
    pool_left = math.inf
    if cfg.pool_month_results is not None:
        d, days = day_of_month(today)
        pool_left = math.ceil(cfg.pool_month_results * d / days) - pool.results()
    candidates.append((pool_left, "pool"))
    best_n, bound = candidates[0]
    for c in candidates[1:]:
        if c[0] < best_n:
            best_n, bound = c
    usd_left = max(0.0, min(usd_lefts)) if usd_lefts else None
    # A hair of slack so 0.05 / 0.0005 is 100, not 99.
    return Allowance(max(0, math.floor(best_n + 1e-6)), usd_left, bound, week_start)


def week_reset(user: Ledger, last_month: Ledger | None, week_start: str) -> str:
    """The day the oldest run in the rolling week leaves it."""
    days = [e.d for e in Ledger.merged([last_month or Ledger(), user]).runs.values() if e.d >= week_start and e.n > 0]
    return shift_day(min(days), 7) if days else shift_day(week_start, 7)


def account_share(cfg: Config, created: str | None, today: str) -> float:
    """Share of the actor and user limits for an account created on `created`."""
    if not created:
        return 1.0
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
        t = date.fromisoformat(today)
    except ValueError:
        return 1.0
    young = (t - c).days < float(cfg.new_account_days or 0)
    return float(cfg.new_account_share if cfg.new_account_share is not None else 1.0) if young else 1.0


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
    return shift_day(today, 1)


def previous_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-")[:2])
    y, m = (y - 1, 12) if m <= 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


_LEDGER_KEY = re.compile(r"^(?:user|pool)\.(\d{4}-\d{2})\.")


def ledger_month(key: str) -> str | None:
    """`2026-11` for a ledger key like `user.2026-11.<hash>`; None for other keys."""
    m = _LEDGER_KEY.match(key)
    return m.group(1) if m else None
