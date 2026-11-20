"""Every control in docs/CONFIGURATION.md, through the env and through code."""

import asyncio
import logging
import unittest
from typing import Any

from apify_bot_guard import DEFAULTS, BotGuard, Config, Entry, Ledger, MemoryBackend, allowance_of, env_name, plan_of
from apify_bot_guard.core import LIMIT_FIELDS, needs_last_month, week_reset

FREE = {"APIFY_USER_ID": "u1", "APIFY_USER_IS_PAYING": "", "ACTOR_RUN_ID": "r1"}
OLD = "2020-01-01"  # account creation date: not a new account
NO_DEFAULTS = {name: None for name in LIMIT_FIELDS} | {"daily_share": None}


def run(c: Any) -> Any:
    return asyncio.run(c)


def only(**limits: Any) -> Config:
    """A config with just the given limits (defaults switched off)."""
    return Config(scope="a", **(NO_DEFAULTS | limits))


def admit(env: dict[str, str] | None = None, backend: Any = None, **kw: Any) -> Any:
    kw.setdefault("today", "2026-11-20")
    kw.setdefault("account_created", OLD)
    return run(BotGuard.admit("a", backend or MemoryBackend(), env=FREE if env is None else env, **kw))


class EnvNames(unittest.TestCase):
    def test_every_setting_has_an_env_var(self):
        self.assertEqual(env_name("actor_week_results"), "BOT_GUARD_ACTOR_WEEK_RESULTS")
        self.assertEqual(len(LIMIT_FIELDS), 15)
        cfg = Config.from_env(
            "a",
            {
                "BOT_GUARD_ACTOR_WEEK_RESULTS": "70",
                "BOT_GUARD_USER_DAY_USD": "0.01",
                "BOT_GUARD_RUN_RESULTS": "20",
                "BOT_GUARD_ACTOR_MONTH_USD": "none",
                "BOT_GUARD_POOL_MONTH_RESULTS": "unlimited",
                "BOT_GUARD_PLANS": "free, starter",
                "BOT_GUARD_EXEMPT_USERS": "me,tester",
                "BOT_GUARD_BLOCKED_USERS": "bad",
                "BOT_GUARD_MODE": " Observe ",
                "BOT_GUARD_MESSAGE": "Limit: {delivered} ",
                "BOT_GUARD_SALT": "s1",
                "BOT_GUARD_TRACK_FROM": "2026-11-01",
                "BOT_GUARD_AUTO_PRUNE": "0",
            },
        )
        assert cfg
        self.assertEqual((cfg.actor_week_results, cfg.user_day_usd, cfg.run_results), (70, 0.01, 20))
        self.assertIsNone(cfg.actor_month_usd)
        self.assertIsNone(cfg.pool_month_results)
        self.assertEqual(cfg.actor_month_results, DEFAULTS["actor_month_results"])
        self.assertEqual(cfg.plans, ("FREE", "STARTER"))
        self.assertEqual(cfg.exempt_users, frozenset({"me", "tester"}))
        self.assertEqual(cfg.blocked_users, frozenset({"bad"}))
        self.assertEqual(
            (cfg.mode, cfg.message, cfg.salt, cfg.track_from, cfg.auto_prune),
            ("observe", "Limit: {delivered} ", "s1", "2026-11-01", 0.0),
        )
        self.assertEqual(cfg.problems, [])

    def test_code_overrides_env_and_unknown_names_fail(self):
        cfg = Config.from_env("a", {"BOT_GUARD_RUN_RESULTS": "20"}, run_results=5, plans=["trial"], exempt_users="a,b")
        assert cfg
        self.assertEqual((cfg.run_results, cfg.plans, cfg.exempt_users), (5, ("TRIAL",), frozenset({"a", "b"})))
        with self.assertRaises(TypeError):
            Config.from_env("a", {}, monthly_quota=5)

    def test_bad_values_are_reported_not_fatal(self):
        cfg = Config.from_env("a", {"BOT_GUARD_RUN_RESULTS": "ten", "BOT_GUARD_MODE": "strict"})
        assert cfg
        self.assertIsNone(cfg.run_results)
        self.assertEqual(cfg.mode, "enforce")
        self.assertEqual(len(cfg.problems), 2)
        self.assertEqual(len(Config(scope="a", run_usd=-1, user_day_results=True).problems), 2)  # type: ignore[arg-type]
        with self.assertLogs("apify_bot_guard", logging.WARNING) as logs:
            admit(dict(FREE, BOT_GUARD_RUN_RESULTS="ten"))
        self.assertIn("BOT_GUARD_RUN_RESULTS='ten'", logs.output[0])


class Windows(unittest.TestCase):
    def test_absolute_day_week_and_run_limits(self):
        user = Ledger({"x": Entry(a="a", d="2026-11-20", n=4), "y": Entry(a="a", d="2026-11-15", n=10)})
        cases = [
            (only(actor_day_results=5), (1, "day")),
            (only(actor_week_results=20), (6, "week")),
            (only(user_week_results=30, actor_week_results=50), (16, "week")),
            (only(run_results=3), (3, "run")),
            (only(actor_month_results=12), (0, "month")),
            (only(actor_day_results=50, run_results=50), (46, "day")),
        ]
        for cfg, want in cases:
            a = allowance_of(cfg, user, Ledger(), "2026-11-20")
            self.assertEqual((a.results_left, a.bound), want, cfg)

    def test_price_limits_per_window(self):
        user = Ledger({"x": Entry(a="a", d="2026-11-20", n=10, usd=0.004)})
        a = allowance_of(only(actor_day_usd=0.005, cost_per_result=0.0001), user, Ledger(), "2026-11-20")
        self.assertEqual((a.results_left, a.bound), (10, "day"))
        self.assertAlmostEqual(a.usd_left or 0, 0.001)
        a = allowance_of(only(user_week_usd=0.01, run_usd=0.002, cost_per_result=0.0001), user, Ledger(), "2026-11-20")
        self.assertEqual((a.results_left, a.bound), (20, "run"))
        self.assertAlmostEqual(a.usd_left or 0, 0.002)
        self.assertIsNone(allowance_of(only(run_results=5), user, Ledger(), "2026-11-20").usd_left)

    def test_week_reaches_into_last_month(self):
        cfg = only(actor_week_results=50)
        self.assertTrue(needs_last_month(cfg, "2026-12-03"))
        self.assertFalse(needs_last_month(cfg, "2026-12-07"))
        self.assertFalse(needs_last_month(only(actor_month_results=5), "2026-12-01"))
        last = Ledger({"o": Entry(a="a", d="2026-11-28", n=30), "older": Entry(a="a", d="2026-11-20", n=99)})
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-12-03", last_month=last)
        self.assertEqual((a.results_left, a.week_start), (20, "2026-11-27"))
        self.assertEqual(week_reset(Ledger(), last, a.week_start), "2026-12-05")
        self.assertEqual(week_reset(Ledger(), None, a.week_start), "2026-12-04")

    def test_guard_reads_last_month_for_the_week(self):
        b = MemoryBackend()
        d = admit(backend=b, today="2026-11-28", actor_week_results=50, actor_month_results=None, daily_share=None)
        d.guard.take(30)
        run(d.guard.close())
        d2 = admit(
            dict(FREE, ACTOR_RUN_ID="r2"),
            b,
            today="2026-12-02",
            actor_week_results=50,
            actor_month_results=None,
            daily_share=None,
        )
        self.assertEqual((d2.remaining, d2.bound, d2.resets_on), (20, "week", "2026-12-05"))
        d2.guard.take(25)
        self.assertTrue(d2.guard.limited)
        self.assertIn("on 2026-12-05 (UTC)", d2.guard.message)


class Policy(unittest.TestCase):
    def test_plans(self):
        self.assertEqual(plan_of({"APIFY_USER_PRICING_TIER": "starter"}), "STARTER")
        self.assertEqual(plan_of({"APIFY_USER_IS_PAYING": "1"}), "PAID")
        starter = dict(FREE, APIFY_USER_PRICING_TIER="STARTER")
        self.assertTrue(admit(starter).unlimited)
        self.assertFalse(admit(starter, plans=["FREE", "STARTER"]).unlimited)
        # Off Apify: your own plan names.
        self.assertFalse(admit({}, user_id="acct", plan="trial", plans=("trial",)).unlimited)
        self.assertTrue(admit({}, user_id="acct", plan="pro", plans=("trial",)).unlimited)
        self.assertTrue(admit({}, user_id="acct").unlimited)  # plan unknown

    def test_exempt_and_blocked_users(self):
        b = MemoryBackend()
        self.assertTrue(admit(backend=b, exempt_users=["u1"]).unlimited)
        d = admit(backend=b, blocked_users=["u1"])
        self.assertEqual((d.allowed, d.remaining, d.bound, d.resets_on), (False, 0, "blocked", None))
        self.assertIn("not available for this account", d.message)
        run(d.guard.close())
        self.assertEqual(b.data, {})  # nothing read or written for either

    def test_observe_mode_measures_but_never_cuts(self):
        b = MemoryBackend()
        d = admit(backend=b, mode="observe", run_results=5)
        self.assertEqual((d.allowed, d.enforced, d.remaining, d.bound), (True, False, None, "run"))
        self.assertEqual(d.guard.take(8), 8)
        self.assertTrue(d.guard.over)
        self.assertFalse(d.guard.limited)
        run(d.guard.close())
        led = run(b.get_ledgers([next(k for k in b.data if k.startswith("user."))]))[0]
        self.assertEqual(led.runs["r1"].n, 8)
        blocked = admit(mode="observe", blocked_users=["u1"])
        self.assertEqual((blocked.allowed, blocked.enforced), (True, False))

    def test_no_limits_means_no_guard(self):
        self.assertTrue(admit(config=only()).unlimited)

    def test_custom_message_and_run_bound(self):
        d = admit(run_results=0, message="Used {delivered}; bound {bound}; more {when} ({resets_on}).")
        self.assertEqual(d.message, "Used 0; bound run; more in a new run (2026-11-20).")

    def test_settings_on_top_of_a_config(self):
        d = admit(config=only(run_results=5), run_results=2)
        self.assertEqual(d.remaining, 2)

    def test_salt_changes_the_key(self):
        a = admit().guard.user_key
        b = admit(salt="pepper").guard.user_key
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
