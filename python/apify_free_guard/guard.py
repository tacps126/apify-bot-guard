"""The run-time guard: load the allowance at start, cut deliveries, book at end."""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

from .backends import Backend
from .core import (
    Allowance,
    Config,
    Entry,
    Ledger,
    account_share,
    allowance_of,
    is_free_plan,
    next_reset,
    user_hash,
    utc_today,
)

log = logging.getLogger("apify_free_guard")


class FreeGuard:
    """One run's free-plan allowance.

    Use `await FreeGuard.open(...)`; it returns None for paying users, for
    runs off the platform, when FREE_BUDGET=off, and when the backend cannot
    be read (a storage problem never blocks a customer).
    """

    def __init__(
        self,
        cfg: Config,
        backend: Backend,
        allowance: Allowance,
        user_key: str,
        pool_key: str,
        run_id: str,
        today: str,
    ) -> None:
        self.cfg = cfg
        self.allowance = allowance
        self.delivered = 0
        self.limited = False
        self._backend = backend
        self._user_key = user_key
        self._pool_key = pool_key
        self._run_id = run_id
        self._today = today
        self._booked = False

    @classmethod
    async def open(
        cls,
        scope: str,
        backend: Backend,
        *,
        env: Optional[Mapping[str, str]] = None,
        config: Optional[Config] = None,
        account_created: Optional[str] = None,
        today: Optional[str] = None,
        force: bool = False,
        salt: str = "",
        track_from: Optional[str] = None,
    ) -> Optional["FreeGuard"]:
        """`scope`: the name limits are booked under (usually the Actor name;
        give variants one name to share an allowance). `account_created`: the
        account's creation date if you can read it; otherwise the day the
        user was first seen on any Actor sharing this backend stands in.
        `track_from`: first-seen days before this are when you started
        tracking, not when the user arrived, so they do not count as new.
        """
        env = os.environ if env is None else env
        if not (force or is_free_plan(env)):
            return None
        cfg = config or Config.from_env(scope, env)
        user = str(env.get("APIFY_USER_ID", "")).strip()
        if cfg is None or not user:
            return None
        today = today or utc_today()
        month = today[:7]
        h = user_hash(user, salt)
        user_key = f"user.{month}.{h}"
        pool_key = f"pool.{month}.{cfg.scope}"
        run_id = str(env.get("ACTOR_RUN_ID") or env.get("APIFY_ACTOR_RUN_ID") or "local")
        try:
            u = await backend.get_ledger(user_key)
            p = await backend.get_ledger(pool_key)
            seen = None if account_created else await backend.first_seen(h, today)
        except Exception as e:  # noqa: BLE001 - fail open on any storage error
            log.warning("free allowance unreadable, no monthly limit this run: %s", e)
            return None
        u.drop_stale_holds(today)
        p.drop_stale_holds(today)
        if seen and track_from and seen < track_from:
            seen = None
        share = account_share(cfg, account_created or seen, today)
        allowance = allowance_of(cfg, u, p, today, share)
        guard = cls(cfg, backend, allowance, user_key, pool_key, run_id, today)
        log.info(
            "free plan: %s results left (bound: %s, new account: %s)",
            allowance.results_left,
            allowance.bound,
            share < 1.0,
        )
        if not allowance.exhausted:
            await guard._save(allowance.results_left, allowance.usd_left, hold=True)
        return guard

    def take(self, wanted: int, cost_so_far: Optional[float] = None) -> int:
        """How many of `wanted` more results may be delivered now; counts
        them as delivered. `cost_so_far`: the run's own cost in USD, if you
        track it (a second guard next to cost_per_result)."""
        left = max(0, self.allowance.results_left - self.delivered)
        n = min(wanted, left)
        if cost_so_far is not None and cost_so_far >= self.allowance.usd_left:
            n = 0
        if n < wanted:
            self.limited = True
        self.delivered += n
        return n

    @property
    def message(self) -> str:
        """A status line for the user when the run was cut or had nothing left."""
        if self.allowance.bound == "day":
            when = "tomorrow (UTC)"
        elif self.allowance.bound == "pool":
            when = "tomorrow (UTC), when more of this month's free share opens"
        else:
            when = f"on {next_reset(self._today[:7])} (UTC)"
        return (
            f"Free plan limit reached: {self.delivered} results this run. "
            f"More are available {when}, or right away on a paid plan."
        )

    async def close(self, usd: Optional[float] = None) -> None:
        """Book what the run delivered (replaces the hold). Call once, at the end."""
        if self._booked:
            return
        self._booked = True
        cost = usd if usd is not None else self.delivered * self.cfg.cost_per_result
        try:
            await self._save(self.delivered, cost, hold=False)
        except Exception as e:  # noqa: BLE001
            log.warning("free allowance not booked: %s", e)

    async def _save(self, n: int, usd: float, hold: bool) -> None:
        mine = Entry(a=self.cfg.scope, d=self._today, n=int(n), usd=usd, h=hold)
        await self._backend.put_entry(self._user_key, self._run_id, mine)
        pool = Entry(a="", d=self._today, n=int(n), usd=0.0, h=hold)
        await self._backend.put_entry(self._pool_key, self._run_id, pool)

    async def __aenter__(self) -> "FreeGuard":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


async def push_data(guard: Optional[FreeGuard], items) -> int:
    """Push to the default dataset through the Apify SDK, cut to the allowance.
    Returns how many were pushed."""
    from apify import Actor  # optional dependency

    items = list(items) if not isinstance(items, dict) else [items]
    n = guard.take(len(items)) if guard else len(items)
    if n:
        await Actor.push_data(items[:n])
    if guard and guard.limited:
        await Actor.set_status_message(guard.message)
    return n
