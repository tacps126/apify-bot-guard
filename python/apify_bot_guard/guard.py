"""The run-time guard: load the allowance at start, cut deliveries, book at end."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from .backends import Backend
from .core import (
    Allowance,
    Config,
    Entry,
    Env,
    Ledger,
    account_share,
    allowance_of,
    needs_last_month,
    next_day,
    next_reset,
    plan_of,
    previous_month,
    user_hash,
    utc_today,
    week_reset,
)

log = logging.getLogger("apify_bot_guard")

WHEN = {
    "day": "tomorrow (UTC)",
    "pool": "tomorrow (UTC), when more of this month's free share opens",
    "run": "in a new run",
}
DEFAULT_MESSAGE = (
    "Free plan limit reached: {delivered} results this run. More are available {when}, or right away on a paid plan."
)
BLOCKED_MESSAGE = "Free use of this Actor is not available for this account. It works on a paid plan."


@dataclass(frozen=True)
class Decision:
    """Whether a run may go ahead, and on what terms.

    `allowed`: deliver results (False: nothing left; end the run with `message`).
    `unlimited`: no limit applies (plan not limited, exempt user, guard off, storage unreadable).
    `remaining`: results this run may deliver (None when unlimited or not enforced).
    `enforced`: False in observe mode: limits are measured and logged, never applied.
    """

    allowed: bool
    unlimited: bool
    remaining: int | None = None
    bound: str | None = None
    message: str | None = None
    resets_on: str | None = None
    enforced: bool = True
    guard: BotGuard | None = None


def _limited_user(
    cfg: Config, env: Env, *, user_id: str | None, plan: str | None, free: bool | None, force: bool
) -> str | None:
    """The user id when this run is limited; None when it is not (unknown, exempt, plan not limited)."""
    user = (user_id or str(env.get("APIFY_USER_ID", ""))).strip()
    if not user or user in cfg.exempt_users:
        return None
    if plan is None and free is not None:
        plan = "FREE" if free else "PAID"
    plan = (plan or plan_of(env) or "").strip().upper()
    return user if force or plan in cfg.plans else None


class BotGuard:
    """One run's allowance. Create it with `await BotGuard.admit(...)` or `open(...)`."""

    def __init__(
        self,
        cfg: Config,
        backend: Backend,
        allowance: Allowance,
        *,
        user_key: str,
        pool_key: str,
        run_id: str,
        today: str,
        resets_on: str | None = None,
        logger: logging.Logger = log,
    ) -> None:
        self.cfg = cfg
        self.allowance = allowance
        self.delivered = 0
        self.limited = False  # a take() was cut (enforce mode)
        self.over = False  # the run went past its allowance (either mode)
        self.user_key = user_key
        self.pool_key = pool_key
        self.run_id = run_id
        self.today = today
        self.enforced = cfg.mode == "enforce"
        self._resets_on = resets_on
        self._backend = backend
        self._log = logger
        self._booked = allowance.bound == "blocked"

    @classmethod
    async def admit(cls, scope: str, backend: Backend, **kwargs: Any) -> Decision:
        """`open()` as one answer. Keep `decision.guard` (when set): call `take()` while
        delivering and `close()` at the end."""
        guard = await cls.open(scope, backend, **kwargs)
        return guard.decision if guard else Decision(allowed=True, unlimited=True)

    @property
    def decision(self) -> Decision:
        left = self.remaining
        return Decision(
            allowed=left > 0 or not self.enforced,
            unlimited=False,
            remaining=left if self.enforced else None,
            bound=self.allowance.bound,
            message=None if left > 0 else self.message,
            resets_on=self.resets_on,
            enforced=self.enforced,
            guard=self,
        )

    @property
    def resets_on(self) -> str | None:
        """The UTC day more becomes available (None when blocked)."""
        if self._resets_on is not None or self.allowance.bound == "blocked":
            return self._resets_on
        if self.allowance.bound == "month":
            return next_reset(self.today[:7])
        if self.allowance.bound == "run":
            return self.today
        return next_day(self.today)

    @classmethod
    async def open(
        cls,
        scope: str,
        backend: Backend,
        *,
        user_id: str | None = None,
        plan: str | None = None,
        free: bool | None = None,
        run_id: str | None = None,
        env: Env | None = None,
        config: Config | None = None,
        account_created: str | None = None,
        today: str | None = None,
        force: bool = False,
        logger: logging.Logger = log,
        **settings: Any,
    ) -> BotGuard | None:
        """Load this run's allowance. Returns None (no limit) when the user's plan is not in
        `plans`, the plan or user id is unknown, the user is exempt, BOT_GUARD=off, no limit is
        set, or the backend cannot be read: a storage problem never blocks a customer.

        On Apify, `user_id`, the plan and `run_id` come from the run's env vars. Elsewhere,
        pass them yourself: `plan` is your own plan name (matched against `plans`); `free`
        is a shorthand for plan FREE (True) or PAID (False).

        Settings: `config`, else `BOT_GUARD_*` env vars with `**settings` on top (any field
        of `Config`, e.g. `actor_week_results=50`). See docs/CONFIGURATION.md.

        `account_created`: the account's creation date, if you can read it. Otherwise the
        day the user was first seen on any Actor sharing this backend stands in.
        """
        env = os.environ if env is None else env
        cfg = replace(config, **settings) if config and settings else config or Config.from_env(scope, env, **settings)
        if cfg is None:
            return None
        for problem in cfg.problems:
            logger.warning("bot-guard: %s", problem)
        user = _limited_user(cfg, env, user_id=user_id, plan=plan, free=free, force=force)
        if user is None:
            return None
        today = today or utc_today()
        month = today[:7]
        h = user_hash(user, cfg.salt)
        user_key = f"user.{month}.{h}"
        pool_key = f"pool.{month}.{cfg.scope}"
        run = run_id or env.get("ACTOR_RUN_ID") or env.get("APIFY_ACTOR_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}"
        common: dict[str, Any] = {"user_key": user_key, "pool_key": pool_key, "run_id": str(run), "today": today}
        if user in cfg.blocked_users:
            logger.info("bot-guard: user is blocked")
            return cls(cfg, backend, Allowance(0, 0.0, "blocked"), logger=logger, **common)
        if not cfg.has_limits:
            return None
        keys = [user_key, pool_key]
        if needs_last_month(cfg, today):
            keys.append(f"user.{previous_month(month)}.{h}")

        async def no_seen() -> str | None:
            return None

        try:
            ledgers, seen = await asyncio.gather(
                backend.get_ledgers(keys),
                no_seen() if account_created else backend.first_seen(h, today),
            )
        except Exception as e:  # noqa: BLE001 - fail open on any storage error
            logger.warning("bot-guard: allowance unreadable, no limit this run: %s", e)
            return None
        u, p = ledgers[0], ledgers[1]
        last = ledgers[2] if len(ledgers) > 2 else None
        for led in filter(None, (u, p, last)):
            led.drop_stale_holds(today)
        if seen and cfg.track_from and seen < cfg.track_from:
            seen = None
        share = account_share(cfg, account_created or seen, today)
        allowance = allowance_of(cfg, u, p, today, share, last_month=last)
        resets = week_reset(u, last, allowance.week_start) if allowance.bound == "week" else None
        guard = cls(cfg, backend, allowance, resets_on=resets, logger=logger, **common)
        logger.info(
            "bot-guard: %s results left (bound: %s, new account: %s, mode: %s)",
            allowance.results_left,
            allowance.bound,
            share < 1,
            cfg.mode,
        )
        if not allowance.exhausted:
            usd = allowance.usd_left
            if usd is None:
                usd = allowance.results_left * float(cfg.cost_per_result or 0)
            try:
                await guard._save(allowance.results_left, usd, hold=True, known={user_key: u, pool_key: p})
            except Exception as e:  # noqa: BLE001
                logger.warning("bot-guard: hold not saved: %s", e)
        return guard

    def take(self, wanted: int, cost_so_far: float | None = None) -> int:
        """How many of `wanted` more results may be delivered now; counts them as delivered.
        `cost_so_far`: the run's own cost in USD, if you track it. In observe mode returns
        `wanted` and only logs when the run goes past its allowance."""
        left = max(0, self.allowance.results_left - self.delivered)
        n = max(0, min(wanted, left))
        usd_left = self.allowance.usd_left
        if cost_so_far is not None and usd_left is not None and cost_so_far >= usd_left:
            n = 0
        if n < wanted:
            if not self.over:
                self._log.info(
                    "bot-guard: allowance used up (bound: %s, enforced: %s)",
                    self.allowance.bound,
                    self.enforced,
                )
            self.over = True
            if self.enforced:
                self.limited = True
            else:
                n = wanted
        self.delivered += n
        return n

    @property
    def remaining(self) -> int:
        return max(0, self.allowance.results_left - self.delivered)

    @property
    def message(self) -> str:
        """A status line for the user when the run was cut or had nothing left."""
        b = self.allowance.bound
        when = WHEN.get(b) or f"on {self.resets_on} (UTC)"
        template = self.cfg.message or (BLOCKED_MESSAGE if b == "blocked" else DEFAULT_MESSAGE)
        values = {"delivered": str(self.delivered), "when": when, "resets_on": self.resets_on or "", "bound": b}
        for k, v in values.items():
            template = template.replace("{" + k + "}", v)
        return template

    async def close(self, usd: float | None = None) -> None:
        """Book what the run delivered, replacing the hold. Safe to call twice."""
        if self._booked:
            return
        self._booked = True
        cost = usd if usd is not None else self.delivered * float(self.cfg.cost_per_result or 0)
        try:
            await self._save(self.delivered, cost, hold=False)
        except Exception as e:  # noqa: BLE001
            self._log.warning("bot-guard: run not booked: %s", e)
        chance = float(self.cfg.auto_prune or 0)
        if chance > 0 and random.random() < chance:
            await self.prune()

    async def prune(self) -> int:
        """Delete ledgers older than last month. Never raises."""
        try:
            n = await self._backend.prune(previous_month(self.today[:7]))
        except Exception as e:  # noqa: BLE001
            self._log.warning("bot-guard: prune failed: %s", e)
            return 0
        if n:
            self._log.info("bot-guard: pruned %s old ledgers", n)
        return n

    async def _save(self, n: int, usd: float, *, hold: bool, known: dict[str, Ledger] | None = None) -> None:
        mine = Entry(a=self.cfg.scope, d=self.today, n=int(n), usd=usd, h=hold)
        pool = Entry(a="", d=self.today, n=int(n), usd=0.0, h=hold)
        await self._backend.put_entries([(self.user_key, self.run_id, mine), (self.pool_key, self.run_id, pool)], known)

    async def __aenter__(self) -> BotGuard:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


async def push_data(guard: BotGuard | None, items: Iterable[Any] | dict[str, Any]) -> int:
    """Push to the run's default dataset through the Apify SDK, cut to the
    allowance; sets the status message when it cuts. Returns how many were pushed."""
    from apify import Actor  # optional dependency

    batch = [items] if isinstance(items, dict) else list(items)
    n = guard.take(len(batch)) if guard else len(batch)
    if n:
        await Actor.push_data(batch[:n])
    if guard and guard.limited:
        await Actor.set_status_message(guard.message)
    return n
