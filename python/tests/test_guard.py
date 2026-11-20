import asyncio
import sys
import types
import unittest
from typing import Any

from apify_bot_guard import ApifyKvBackend, BotGuard, Config, MemoryBackend, RedisBackend, push_data
from apify_bot_guard.backends import record_key
from tests.fakes import FakeApifyClient, FakeRedis, FakeUpstash

FREE = {"APIFY_USER_ID": "u1", "APIFY_USER_IS_PAYING": "", "ACTOR_RUN_ID": "r1"}
LATER = "2099-01-01"  # first-seen days before this predate tracking: users are not "new"


def run(c: Any) -> Any:
    return asyncio.run(c)


async def one(backend: Any, run_id: str, wanted: int, today: str, **kw: Any) -> tuple[int, BotGuard]:
    g = await BotGuard.open("a", backend, env=dict(FREE, ACTOR_RUN_ID=run_id), today=today, track_from=LATER, **kw)
    assert g is not None
    got = g.take(wanted)
    await g.close()
    return got, g


def backends() -> list[tuple[str, Any]]:
    return [
        ("memory", MemoryBackend()),
        ("apify", ApifyKvBackend(client=FakeApifyClient())),
        ("redis", RedisBackend(FakeRedis())),
        ("upstash", RedisBackend(FakeUpstash())),
    ]


class EveryBackend(unittest.TestCase):
    def test_a_month_of_runs(self):
        for name, b in backends():
            with self.subTest(name):
                got, g = run(one(b, "r1", 500, "2026-11-20"))
                self.assertEqual(got, 34)
                self.assertTrue(g.limited)
                self.assertIn("tomorrow (UTC)", g.message)
                self.assertEqual(run(one(b, "r2", 500, "2026-11-20"))[0], 0)
                self.assertEqual(run(one(b, "r3", 500, "2026-11-21"))[0], 34)
                self.assertEqual(run(one(b, "r4", 500, "2026-11-22"))[0], 32)
                got, g = run(one(b, "r5", 500, "2026-11-23"))
                self.assertEqual(got, 0)
                self.assertIn("2026-12-01", g.message)

    def test_hold_stops_parallel_runs_and_booking_replaces_it(self):
        for name, b in backends():
            with self.subTest(name):

                async def both(b: Any = b) -> tuple[BotGuard, BotGuard]:
                    g1 = await BotGuard.open("a", b, env=FREE, today="2026-11-20", track_from=LATER)
                    g2 = await BotGuard.open(
                        "a", b, env=dict(FREE, ACTOR_RUN_ID="r2"), today="2026-11-20", track_from=LATER
                    )
                    assert g1 and g2
                    return g1, g2

                g1, g2 = run(both())
                self.assertEqual((g1.allowance.results_left, g2.allowance.results_left), (34, 0))
                g1.take(10)
                run(g1.close())
                (led,) = run(b.get_ledgers([g1.user_key]))
                self.assertEqual((led.runs["r1"].n, led.runs["r1"].h), (10, False))
                # A third run now sees the 24 the first one did not use.
                g3 = run(BotGuard.open("a", b, env=dict(FREE, ACTOR_RUN_ID="r3"), today="2026-11-20", track_from=LATER))
                assert g3
                self.assertEqual(g3.allowance.results_left, 24)

    def test_first_seen_is_kept(self):
        for name, b in backends():
            with self.subTest(name):
                self.assertEqual(run(b.first_seen("x", "2026-11-20")), "2026-11-20")
                self.assertEqual(run(b.first_seen("x", "2026-11-21")), "2026-11-20")


class Guard(unittest.TestCase):
    def test_no_guard_for_paying_unknown_off_or_anonymous(self):
        b = MemoryBackend()
        self.assertIsNone(run(BotGuard.open("a", b, env=dict(FREE, APIFY_USER_IS_PAYING="1"))))
        self.assertIsNone(run(BotGuard.open("a", b, env={"APIFY_USER_ID": "u1"})))
        self.assertIsNone(run(BotGuard.open("a", b, env=dict(FREE, FREE_BUDGET="off"))))
        self.assertIsNone(run(BotGuard.open("a", b, env=dict(FREE, APIFY_USER_ID=""))))
        self.assertEqual(b.data, {})

    def test_generic_identity_off_apify(self):
        b = MemoryBackend()
        g = run(BotGuard.open("api", b, env={}, user_id="acct-9", free=True, today="2026-11-20", track_from=LATER))
        assert g
        self.assertEqual(g.allowance.results_left, 34)
        self.assertTrue(g.run_id.startswith("local-"))
        self.assertIsNone(run(BotGuard.open("api", b, env={}, user_id="acct-9", free=False)))
        self.assertIsNotNone(run(BotGuard.open("api", b, env={}, user_id="acct-9", free=False, force=True)))

    def test_new_accounts(self):
        g = run(BotGuard.open("a", MemoryBackend(), env=FREE, today="2026-11-20", account_created="2026-11-18"))
        assert g
        self.assertEqual(g.allowance.results_left, 9)  # ceil(25 * 0.34)
        # Without a creation date, first seen today counts as new.
        g = run(BotGuard.open("a", MemoryBackend(), env=FREE, today="2026-11-20"))
        assert g
        self.assertEqual(g.allowance.results_left, 9)

    def test_explicit_config_and_cost_guard(self):
        cfg = Config(scope="x", daily_share=1.0)
        g = run(BotGuard.open("ignored", MemoryBackend(), env=FREE, config=cfg, today="2026-11-30", track_from=LATER))
        assert g
        self.assertEqual(g.pool_key, "pool.2026-11.x")
        self.assertEqual(g.take(10, cost_so_far=0.01), 10)
        self.assertEqual(g.take(10, cost_so_far=1.0), 0)
        self.assertEqual(g.take(-3), 0)
        self.assertEqual(g.remaining, 90)

    def test_storage_failures_fail_open(self):
        c = FakeApifyClient()
        c.fail = True
        self.assertIsNone(run(BotGuard.open("a", ApifyKvBackend(client=c), env=FREE)))

        class WriteFails(MemoryBackend):
            async def put_entries(self, puts: Any, known: Any = None) -> None:
                raise RuntimeError("read only")

        g = run(BotGuard.open("a", WriteFails(), env=FREE, today="2026-11-20", track_from=LATER))
        assert g
        g.take(1)
        run(g.close())
        run(g.close())  # second close is a no-op

    def test_context_manager_books(self):
        b = MemoryBackend()

        async def go() -> None:
            g = await BotGuard.open("a", b, env=FREE, today="2026-11-20", track_from=LATER)
            assert g
            async with g:
                g.take(5)

        run(go())
        (led,) = run(b.get_ledgers([next(k for k in b.data if k.startswith("user."))]))
        self.assertEqual(led.runs["r1"].n, 5)


class RoundTrips(unittest.TestCase):
    def test_redis_pipelines_each_batch(self):
        r = FakeRedis()
        run(one(RedisBackend(r), "r1", 5, "2026-11-20", account_created="2020-01-01"))
        self.assertEqual(r.s.round_trips, 3)  # read, hold, book

    def test_apify_hold_reuses_the_read(self):
        c = FakeApifyClient()
        run(one(ApifyKvBackend(client=c), "r1", 5, "2026-11-20", account_created="2020-01-01"))
        # store lookup 1 + reads 2 + hold writes 2 + book (re-read 2 + write 2)
        self.assertEqual(c.calls, 9)

    def test_record_keys_are_valid(self):
        self.assertEqual(record_key("user.2026-11.ab/cd é"), "user.2026-11.ab_cd__")
        self.assertEqual(len(record_key("x" * 300)), 256)


class PushData(unittest.TestCase):
    def test_push_data_cuts_and_sets_status(self):
        pushed: list[Any] = []
        status: list[str] = []

        class Actor:
            @staticmethod
            async def push_data(items: Any) -> None:
                pushed.extend(items)

            @staticmethod
            async def set_status_message(m: str) -> None:
                status.append(m)

        sys.modules["apify"] = types.SimpleNamespace(Actor=Actor)  # type: ignore[assignment]
        try:
            g = run(BotGuard.open("a", MemoryBackend(), env=FREE, today="2026-11-20", track_from=LATER))
            self.assertEqual(run(push_data(g, [{"i": i} for i in range(30)])), 30)
            self.assertEqual(run(push_data(g, ({"i": i} for i in range(30)))), 4)
            self.assertEqual(run(push_data(g, {"i": 99})), 0)
            self.assertEqual(run(push_data(None, {"i": 1})), 1)
            self.assertEqual(len(pushed), 35)
            self.assertEqual(len(status), 2)
        finally:
            del sys.modules["apify"]


if __name__ == "__main__":
    unittest.main()
