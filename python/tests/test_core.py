import unittest

from apify_bot_guard import Config, Entry, Ledger, allowance_of, is_free_plan, user_hash
from apify_bot_guard.core import account_share, day_of_month, ledger_month, next_day, next_reset, previous_month

# Pinned in both packages: Python and JavaScript Actors can share one store.
HASH_U1 = "c7e697d2100b975d4301c43545656d09"


class Plan(unittest.TestCase):
    def test_plan_detection(self):
        self.assertTrue(is_free_plan({"APIFY_USER_IS_PAYING": ""}))
        self.assertTrue(is_free_plan({"APIFY_USER_IS_PAYING": " 0 "}))
        self.assertFalse(is_free_plan({"APIFY_USER_IS_PAYING": "1"}))
        self.assertTrue(is_free_plan({"APIFY_USER_PRICING_TIER": "free", "APIFY_USER_IS_PAYING": "1"}))
        self.assertFalse(is_free_plan({"APIFY_USER_PRICING_TIER": "SCALE"}))
        self.assertIsNone(is_free_plan({}))


class Configs(unittest.TestCase):
    def test_off_switch(self):
        for v in ("off", "0", "false", " OFF "):
            self.assertIsNone(Config.from_env("a", {"BOT_GUARD": v}))

    def test_env_values_overrides_and_clamps(self):
        cfg = Config.from_env(
            "a",
            {
                "BOT_GUARD_ACTOR_MONTHLY_RESULTS": "50",
                "BOT_GUARD_DAILY_SHARE": "5",
                "BOT_GUARD_USER_MONTHLY_RESULTS": "-1",
                "BOT_GUARD_POOL_MONTHLY_RESULTS": "lots",
                "BOT_GUARD_NEW_ACCOUNT_SHARE": "inf",
                "BOT_GUARD_COST_PER_RESULT": "0",
                "BOT_GUARD_SCOPE": "shared",
            },
            pool_results=10,
        )
        assert cfg is not None
        self.assertEqual(cfg.actor_results, 50)
        self.assertEqual(cfg.daily_share, 1.0)
        self.assertEqual(cfg.user_results, 300)
        self.assertEqual(cfg.pool_results, 10)
        self.assertEqual(cfg.new_account_share, 0.25)
        self.assertEqual(cfg.cost_per_result, 1e-7)
        self.assertEqual(cfg.scope, "shared")


class Ledgers(unittest.TestCase):
    def test_from_raw_tolerates_junk(self):
        self.assertEqual(Ledger.from_raw(None).runs, {})
        self.assertEqual(Ledger.from_raw({"runs": 5}).runs, {})
        led = Ledger.from_raw({"runs": {"r": {"n": "7", "usd": "x", "h": "yes"}, "bad": 3}})
        self.assertEqual(led.runs["r"], Entry(n=7))
        self.assertEqual(Ledger.from_raw({"r": {"n": 2}}).runs["r"].n, 2)

    def test_entry_json_round_trip(self):
        e = Entry(a="s", d="2026-11-20", n=3, usd=0.1234567, h=True)
        self.assertEqual(e.to_json(), {"a": "s", "d": "2026-11-20", "n": 3, "usd": 0.123457, "h": True})
        self.assertNotIn("h", Entry().to_json())
        self.assertEqual(Entry.from_json(e.to_json()).n, 3)

    def test_stale_holds_dropped(self):
        led = Ledger(
            {"old": Entry(d="2026-11-19", h=True), "now": Entry(d="2026-11-20", h=True), "done": Entry(d="2026-11-01")}
        )
        led.drop_stale_holds("2026-11-20")
        self.assertEqual(sorted(led.runs), ["done", "now"])


class Allowances(unittest.TestCase):
    def test_tightest_limit_wins(self):
        cfg = Config(scope="a")
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-11-30")
        self.assertEqual((a.results_left, a.bound), (34, "day"))
        cfg.daily_share = 1.0
        self.assertEqual(allowance_of(cfg, Ledger(), Ledger(), "2026-11-30").bound, "month")
        # Day 1 of 30 releases 100 of the 3000 pool: ties keep "month".
        self.assertEqual(allowance_of(cfg, Ledger(), Ledger(), "2026-11-01").bound, "month")
        pool = Ledger({"x": Entry(n=95, d="2026-11-01")})
        a = allowance_of(cfg, Ledger(), pool, "2026-11-01")
        self.assertEqual((a.results_left, a.bound), (5, "pool"))

    def test_cost_and_cross_actor_limits(self):
        cfg = Config(scope="a", daily_share=1.0, cost_per_result=0.001)
        self.assertEqual(allowance_of(cfg, Ledger(), Ledger(), "2026-11-30").results_left, 50)
        other = Ledger({f"r{i}": Entry(a=f"b{i}", n=100) for i in range(3)})
        a = allowance_of(Config(scope="a", daily_share=1.0), other, Ledger(), "2026-11-30")
        self.assertTrue(a.exhausted)

    def test_never_negative(self):
        user = Ledger({"r": Entry(a="a", d="2026-11-30", n=500, usd=9)})
        a = allowance_of(Config(scope="a"), user, Ledger(), "2026-11-30")
        self.assertEqual((a.results_left, a.usd_left), (0, 0.0))


class Dates(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(day_of_month("2024-02-10"), (10, 29))
        self.assertEqual(day_of_month("nonsense"), (30, 30))
        self.assertEqual(next_reset("2026-12"), "2027-01-01")
        self.assertEqual(next_reset("2026-01"), "2026-02-01")
        cfg = Config(scope="a")
        self.assertEqual(account_share(cfg, None, "2026-11-20"), 1.0)
        self.assertEqual(account_share(cfg, "garbage", "2026-11-20"), 1.0)
        self.assertEqual(account_share(cfg, "2026-11-14", "2026-11-20"), 0.25)
        self.assertEqual(account_share(cfg, "2026-11-13T23:00:00Z", "2026-11-20"), 1.0)

    def test_month_keys(self):
        self.assertEqual(previous_month("2026-01"), "2025-12")
        self.assertEqual(previous_month("2026-11"), "2026-10")
        self.assertEqual(next_day("2026-12-31"), "2027-01-01")
        self.assertEqual(ledger_month("user.2026-11.abc"), "2026-11")
        self.assertEqual(ledger_month("pool.2026-11.my.actor"), "2026-11")
        self.assertIsNone(ledger_month("seen.abc"))
        self.assertIsNone(ledger_month("user.later.abc"))

    def test_hash_is_stable_and_salted(self):
        self.assertEqual(user_hash("u1"), HASH_U1)
        self.assertNotEqual(user_hash("u1", "s"), HASH_U1)


if __name__ == "__main__":
    unittest.main()
