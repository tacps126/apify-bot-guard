import asyncio
import unittest

from apify_free_guard import Config, FreeGuard, Ledger, MemoryBackend, RedisBackend, allowance_of, is_free_plan
from apify_free_guard.core import Entry

FREE = {"APIFY_USER_ID": "u1", "APIFY_USER_IS_PAYING": "", "ACTOR_RUN_ID": "r1"}


def run(c):
    return asyncio.run(c)


class Plan(unittest.TestCase):
    def test_empty_paying_flag_means_free(self):
        self.assertTrue(is_free_plan({"APIFY_USER_IS_PAYING": ""}))
        self.assertFalse(is_free_plan({"APIFY_USER_IS_PAYING": "1"}))
        self.assertTrue(is_free_plan({"APIFY_USER_PRICING_TIER": "FREE", "APIFY_USER_IS_PAYING": "1"}))
        self.assertFalse(is_free_plan({"APIFY_USER_PRICING_TIER": "SCALE"}))
        self.assertIsNone(is_free_plan({}))

    def test_off_switch_and_env_limits(self):
        self.assertIsNone(Config.from_env("a", {"FREE_BUDGET": "off"}))
        cfg = Config.from_env("a", {"FREE_ACTOR_MONTHLY_RESULTS": "50", "FREE_DAILY_SHARE": "5"})
        self.assertEqual(cfg.actor_results, 50)
        self.assertEqual(cfg.daily_share, 1.0)


class Allowances(unittest.TestCase):
    def test_tightest_limit_wins(self):
        cfg = Config(scope="a")
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-11-30")
        self.assertEqual((a.results_left, a.bound), (34, "day"))
        cfg.daily_share = 1.0
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-11-30")
        self.assertEqual((a.results_left, a.bound), (100, "month"))
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-11-01")
        self.assertEqual((a.results_left, a.bound), (100, "month"))  # pool 3000 / 30 ties, month kept
        pool = Ledger(runs={"x": Entry(n=95, d="2026-11-01")})
        a = allowance_of(cfg, Ledger(), pool, "2026-11-01")
        self.assertEqual((a.results_left, a.bound), (5, "pool"))

    def test_cost_limit(self):
        cfg = Config(scope="a", daily_share=1.0, cost_per_result=0.001)
        a = allowance_of(cfg, Ledger(), Ledger(), "2026-11-30")
        self.assertEqual(a.results_left, 50)


class Guard(unittest.TestCase):
    def test_paying_users_get_no_guard(self):
        b = MemoryBackend()
        env = dict(FREE, APIFY_USER_IS_PAYING="1")
        self.assertIsNone(run(FreeGuard.open("a", b, env=env)))
        self.assertEqual(b.data, {})

    def test_month_of_runs(self):
        b = MemoryBackend()

        async def one(run_id, wanted, today):
            env = dict(FREE, ACTOR_RUN_ID=run_id)
            g = await FreeGuard.open("a", b, env=env, today=today, track_from="2099-01-01")
            got = g.take(wanted)
            await g.close()
            return got, g

        got, g = run(one("r1", 500, "2026-11-20"))
        self.assertEqual(got, 34)  # one day's share
        self.assertTrue(g.limited)
        self.assertIn("tomorrow", g.message)
        self.assertEqual(run(one("r2", 500, "2026-11-20"))[0], 0)
        self.assertEqual(run(one("r3", 500, "2026-11-21"))[0], 34)
        self.assertEqual(run(one("r4", 500, "2026-11-22"))[0], 32)  # 100 a month
        got, g = run(one("r5", 500, "2026-11-23"))
        self.assertEqual(got, 0)
        self.assertIn("2026-12-01", g.message)

    def test_new_accounts_get_a_quarter(self):
        b = MemoryBackend()
        g = run(FreeGuard.open("a", b, env=FREE, today="2026-11-20", account_created="2026-11-18T10:00:00Z"))
        self.assertEqual(g.allowance.results_left, 9)  # ceil(25 * 0.34)

    def test_hold_stops_parallel_runs(self):
        b = MemoryBackend()

        async def both():
            g1 = await FreeGuard.open("a", b, env=FREE, today="2026-11-20", track_from="2099-01-01")
            g2 = await FreeGuard.open("a", b, env=dict(FREE, ACTOR_RUN_ID="r2"), today="2026-11-20", track_from="2099-01-01")
            return g1, g2

        g1, g2 = run(both())
        self.assertEqual(g1.allowance.results_left, 34)
        self.assertEqual(g2.allowance.results_left, 0)

    def test_storage_failure_fails_open(self):
        class Broken(MemoryBackend):
            async def get_ledger(self, key):
                raise RuntimeError("down")

        self.assertIsNone(run(FreeGuard.open("a", Broken(), env=FREE)))


class FakeRedis:
    def __init__(self):
        self.h, self.kv = {}, {}

    async def hgetall(self, k):
        return {f.encode(): v.encode() for f, v in self.h.get(k, {}).items()}

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def expire(self, k, s):
        return True

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v

    async def get(self, k):
        v = self.kv.get(k)
        return v.encode() if v else None


class Redis(unittest.TestCase):
    def test_redis_backend_round_trip(self):
        r = FakeRedis()
        b = RedisBackend(r)
        g = run(FreeGuard.open("a", b, env=FREE, today="2026-11-20", track_from="2099-01-01"))
        g.take(10)
        run(g.close())
        led = run(b.get_ledger(g._user_key))
        self.assertEqual(led.runs["r1"].n, 10)
        self.assertFalse(led.runs["r1"].h)
        self.assertEqual(run(b.first_seen("x", "2026-11-20")), "2026-11-20")
        self.assertEqual(run(b.first_seen("x", "2026-11-21")), "2026-11-20")


if __name__ == "__main__":
    unittest.main()
