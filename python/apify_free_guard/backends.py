"""Where ledgers live. A backend stores, per key, a map of run id -> entry.

- MemoryBackend: tests and local runs.
- ApifyKvBackend: a key-value store on YOUR Apify account (the developer's),
  reached with your token from an Actor secret env var. No extra service.
- RedisBackend: any redis-py compatible async client (Redis, Valkey,
  Upstash). One hash field per run, so concurrent runs never overwrite each
  other.
"""

from __future__ import annotations

import json
from typing import Optional, Protocol

from .core import Entry, Ledger

LEDGER_TTL_SECONDS = 45 * 24 * 3600  # a month plus slack
SEEN_TTL_SECONDS = 400 * 24 * 3600


class Backend(Protocol):
    async def get_ledger(self, key: str) -> Ledger: ...

    async def put_entry(self, key: str, run_id: str, entry: Entry) -> None: ...

    async def first_seen(self, user_key: str, today: str) -> Optional[str]:
        """The stored first-seen day, recording `today` if none."""
        ...


def _ledger(raw: Optional[dict]) -> Ledger:
    runs = (raw or {}).get("runs", raw or {})
    return Ledger(runs={str(k): Entry.from_json(v) for k, v in runs.items() if isinstance(v, dict)})


class MemoryBackend:
    def __init__(self) -> None:
        self.data: dict = {}

    async def get_ledger(self, key: str) -> Ledger:
        return _ledger(self.data.get(key))

    async def put_entry(self, key: str, run_id: str, entry: Entry) -> None:
        self.data.setdefault(key, {})[run_id] = entry.to_json()

    async def first_seen(self, user_key: str, today: str) -> Optional[str]:
        return self.data.setdefault(f"seen.{user_key}", today)


class ApifyKvBackend:
    """Ledgers as JSON records in one of your own key-value stores.

    Writes are read-merge-write: two runs saving in the same instant can lose
    one entry. Holds booked at run start keep parallel runs from each taking
    the whole allowance, so the worst case is a small overshoot. Use
    RedisBackend if you need exact counts under heavy parallel load.
    """

    def __init__(self, token: str, store: str = "free-guard", api_url: Optional[str] = None) -> None:
        from apify_client import ApifyClientAsync  # optional dependency

        kwargs = {"token": token}
        if api_url:
            kwargs["api_url"] = api_url
        self._client = ApifyClientAsync(**kwargs)
        self._store_name = store
        self._store_id: Optional[str] = None

    async def _store(self):
        if self._store_id is None:
            got = await self._client.key_value_stores().get_or_create(name=self._store_name)
            self._store_id = got["id"]
        return self._client.key_value_store(self._store_id)

    @staticmethod
    def _key(key: str) -> str:
        # Record keys allow a-zA-Z0-9!-_.'() and at most 256 characters.
        return "".join(c if c.isalnum() or c in "!-_.'()" else "_" for c in key)[:256]

    async def _get(self, key: str):
        rec = await (await self._store()).get_record(self._key(key))
        return rec["value"] if rec else None

    async def get_ledger(self, key: str) -> Ledger:
        return _ledger(await self._get(key))

    async def put_entry(self, key: str, run_id: str, entry: Entry) -> None:
        current = await self._get(key) or {}
        runs = dict(current.get("runs", {}))
        runs[run_id] = entry.to_json()
        await (await self._store()).set_record(self._key(key), {"v": 1, "runs": runs})

    async def first_seen(self, user_key: str, today: str) -> Optional[str]:
        key = f"seen.{user_key}"
        got = await self._get(key)
        if isinstance(got, dict) and got.get("first"):
            return str(got["first"])
        await (await self._store()).set_record(self._key(key), {"first": today})
        return today


class RedisBackend:
    """`client`: redis.asyncio.Redis, or upstash_redis.asyncio.Redis."""

    def __init__(self, client, prefix: str = "free-guard:") -> None:
        self._r = client
        self._prefix = prefix

    async def get_ledger(self, key: str) -> Ledger:
        raw = await self._r.hgetall(self._prefix + key) or {}
        runs = {}
        for k, v in raw.items():
            k = k.decode() if isinstance(k, bytes) else k
            v = v.decode() if isinstance(v, bytes) else v
            try:
                runs[k] = json.loads(v)
            except (TypeError, ValueError):
                continue
        return _ledger({"runs": runs})

    async def put_entry(self, key: str, run_id: str, entry: Entry) -> None:
        k = self._prefix + key
        await self._r.hset(k, run_id, json.dumps(entry.to_json()))
        await self._r.expire(k, LEDGER_TTL_SECONDS)

    async def first_seen(self, user_key: str, today: str) -> Optional[str]:
        k = f"{self._prefix}seen.{user_key}"
        await self._r.set(k, today, nx=True, ex=SEEN_TTL_SECONDS)
        got = await self._r.get(k)
        return got.decode() if isinstance(got, bytes) else got
