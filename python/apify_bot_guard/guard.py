"""The run-time guard: load the allowance at start, cut deliveries, book at end."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .backends import Backend
from .core import (
    Allowance,
    Config,
    Entry,
    Env,
    account_share,
    allowance_of,
    is_free_plan,
    next_day,
    next_reset,
    previous_month,
    user_hash,
    utc_today,
)

log = logging.getLogger("apify_bot_guard")


@dataclass(frozen=True)
class Decision:
    """Whether a run may go ahead, and on what terms.

    `allowed`: deliver results (False: nothing left; end the run with `message`).
    `unlimited`: no limit applies (paying user, guard off, storage unreadable).
    `remaining`: results this run may deliver (None when unlimited).
    """

    allowed: bool
    unlimited: bool
    remaining: int | None = None
    bound: str | None = None
    message: str | None = None
    resets_on: str | None = None
    guard: BotGuard | None = None


class BotGuard:
    """One run's free-plan allowance. Create it with `await BotGuard.open(...)`."""

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
        logger: logging.Logger = log,
    ) -> None:
        self.cfg = cfg
        self.allowance = allowance
        self.delivered = 0
        self.limited = False
        self.user_key = user_key
        self.pool_key = pool_key
        self.run_id = run_id
        self.today = today
        self._backend = backend
        self._log = logger
        self._booked = False
        self.auto_prune = 0.0

    @classmethod
    async def admit(cls, scope: str, backend: Backend, **kwargs: Any) -> Decision:
        """`open()` as one yes/no answer. Keep `decision.guard` (when set): call
        `take()` while delivering and `close()` at the end."""
        guard = await cls.open(scope, backend, **kwargs)
        return guard.decision if guard else Decision(allowed=True, unlimited=True)

    @property
    def decision(self) -> Decision:
        left = self.remaining
        return Decision(
            allowed=left > 0,
            unlimited=False,
            remaining=left,
            bound=self.allowance.bound,
            message=None if left > 0 else self.message,
            resets_on=self.resets_on,
            guard=self,
        )

    @property
    def resets_on(self) -> str:
        """The UTC day more becomes available."""
        return next_reset(self.today[:7]) if self.allowance.bound == "month" else next_day(self.today)

    @classmethod
    async def open(
        cls,
        scope: str,
        backend: Backend,
        *,
        user_id: str | None = None,
        free: bool | None = None,
        run_id: str | None = None,
        env: Env | None = None,
        config: Config | None = None,
        account_created: str | None = None,
        today: str | None = None,
        force: bool = False,
        salt: str = "",
        track_from: str | None = None,
        auto_prune: float = 0.02,
        logger: logging.Logger = log,
    ) -> BotGuard | None:
        """Load this run's allowance.

        Returns None (no limit) for paying users, unknown plans, a missing user
        id, BOT_GUARD=off, and when the backend cannot be read: a storage
        problem never blocks a customer.

        On Apify, `user_id`, `free` and `run_id` come from the run's env vars.
        Elsewhere, pass them yourself.

        `account_created`: the account's creation date, if you can read it.
        Otherwise the day the user was first seen on any Actor sharing this
        backend stands in. `track_from`: first-seen days before this predate
        your tracking, so those users do not count as new.
        """
        env = os.environ if env is None else env
        is_free = free if free is not None else is_free_plan(env)
        if not (force or is_free):
            return None
        cfg = config or Config.from_env(scope, env)
        user = (user_id or str(env.get("APIFY_USER_ID", ""))).strip()
        if cfg is None or not user:
            return None
        today = today or utc_today()
        month = today[:7]
        h = user_hash(user, salt)
        user_key = f"user.{month}.{h}"
        pool_key = f"pool.{month}.{cfg.scope}"
        run = run_id or env.get("ACTOR_RUN_ID") or env.get("APIFY_ACTOR_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}"

        async def no_seen() -> str | None:
            return None

        try:
            (u, p), seen = await asyncio.gather(
                backend.get_ledgers([user_key, pool_key]),
                no_seen() if account_created else backend.first_seen(h, today),
            )
        except Exception as e:  # noqa: BLE001 - fail open on any storage error
            logger.warning("bot-guard: allowance unreadable, no monthly limit this run: %s", e)
            return None
        u.drop_stale_holds(today)
        p.drop_stale_holds(today)
        if seen and track_from and seen < track_from:
            seen = None
        share = account_share(cfg, account_created or seen, today)
        allowance = allowance_of(cfg, u, p, today, share)
        guard = cls(
            cfg, backend, allowance, user_key=user_key, pool_key=pool_key, run_id=str(run), today=today, logger=logger
        )
        guard.auto_prune = auto_prune
        logger.info(
            "bot-guard: %s results left (bound: %s, new account: %s)",
            allowance.results_left,
            allowance.bound,
            share < 1,
        )
        if not allowance.exhausted:
            try:
                await guard._save(
                    allowance.results_left, allowance.usd_left, hold=True, known={user_key: u, pool_key: p}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("bot-guard: hold not saved: %s", e)
        return guard

    def take(self, wanted: int, cost_so_far: float | None = None) -> int:
        """How many of `wanted` more results may be delivered now; counts them
        as delivered. `cost_so_far`: the run's own cost in USD, if you track it."""
        left = max(0, self.allowance.results_left - self.delivered)
        n = max(0, min(wanted, left))
        if cost_so_far is not None and cost_so_far >= self.allowance.usd_left:
            n = 0
        if n < wanted:
            self.limited = True
        self.delivered += n
        return n

    @property
    def remaining(self) -> int:
        return max(0, self.allowance.results_left - self.delivered)

    @property
    def message(self) -> str:
        """A status line for the user when the run was cut or had nothing left."""
        if self.allowance.bound == "day":
            when = "tomorrow (UTC)"
        elif self.allowance.bound == "pool":
            when = "tomorrow (UTC), when more of this month's free share opens"
        else:
            when = f"on {next_reset(self.today[:7])} (UTC)"
        return (
            f"Free plan limit reached: {self.delivered} results this run. "
            f"More are available {when}, or right away on a paid plan."
        )

    async def close(self, usd: float | None = None) -> None:
        """Book what the run delivered, replacing the hold. Safe to call twice."""
        if self._booked:
            return
        self._booked = True
        cost = usd if usd is not None else self.delivered * self.cfg.cost_per_result
        try:
            await self._save(self.delivered, cost, hold=False)
        except Exception as e:  # noqa: BLE001
            self._log.warning("bot-guard: run not booked: %s", e)
        if self.auto_prune > 0 and random.random() < self.auto_prune:
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

    async def _save(self, n: int, usd: float, *, hold: bool, known: dict[str, Any] | None = None) -> None:
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
