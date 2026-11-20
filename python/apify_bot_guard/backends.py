"""Where ledgers live. A backend stores, per key, a map of run id -> entry.

Every backend works in batches, so a run costs a fixed, small number of round
trips: one batch read at start, one batch write for the hold, one batch write
at the end.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any, Protocol

from .core import Entry, Ledger

LEDGER_TTL_SECONDS = 45 * 24 * 3600  # a month plus slack
SEEN_TTL_SECONDS = 400 * 24 * 3600

Put = tuple[str, str, Entry]  # (key, run id, entry)


class Backend(Protocol):
    async def get_ledgers(self, keys: Sequence[str]) -> list[Ledger]:
        """Ledgers for `keys`, in order; empty ledgers for missing keys."""
        ...

    async def put_entries(self, puts: Sequence[Put], known: dict[str, Ledger] | None = None) -> None:
        """Set one entry per (key, run id). `known`: ledgers just read, which a
        backend without per-entry writes may merge into instead of re-reading."""
        ...

    async def first_seen(self, user_key: str, today: str) -> str | None:
        """The stored first-seen day for a user, recording `today` if none."""
        ...


class MemoryBackend:
    """In-process store for tests and local runs."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get_ledgers(self, keys: Sequence[str]) -> list[Ledger]:
        return [Ledger.from_raw(self.data.get(k)) for k in keys]

    async def put_entries(self, puts: Sequence[Put], known: dict[str, Ledger] | None = None) -> None:
        for key, run_id, entry in puts:
            self.data.setdefault(key, {})[run_id] = entry.to_json()

    async def first_seen(self, user_key: str, today: str) -> str | None:
        return str(self.data.setdefault(f"seen.{user_key}", today))


def record_key(key: str) -> str:
    """Apify record keys allow a-zA-Z0-9!-_.'() and at most 256 characters."""
    return "".join(c if c.isascii() and (c.isalnum() or c in "!-_.'()") else "_" for c in key)[:256]


class ApifyKvBackend:
    """Ledgers as JSON records in a key-value store on YOUR Apify account.

    The run's own token belongs to the user and cannot write to your stores,
    so pass your token (an Actor secret env var). Writes are read-merge-write:
    two runs saving in the same instant can lose one entry. The hold booked at
    run start keeps parallel runs from each taking the whole allowance, so the
    worst case is a small overshoot. Use RedisBackend for exact counts.

    `client`: an ApifyClientAsync; built from `token` when omitted.
    """

    def __init__(
        self,
        token: str | None = None,
        store: str = "bot-guard",
        *,
        client: Any = None,
        api_url: str | None = None,
    ) -> None:
        if client is None:  # pragma: no cover - needs the optional dependency
            from apify_client import ApifyClientAsync

            client = ApifyClientAsync(token=token, **({"api_url": api_url} if api_url else {}))
        self._client = client
        self._store_name = store
        self._store: Any = None
        self._lock = asyncio.Lock()

    async def _kv(self) -> Any:
        # One lookup even when the first reads run in parallel.
        async with self._lock:
            if self._store is None:
                got = await self._client.key_value_stores().get_or_create(name=self._store_name)
                self._store = self._client.key_value_store(got["id"])
        return self._store

    async def _get(self, key: str) -> Any:
        rec = await (await self._kv()).get_record(record_key(key))
        return rec["value"] if rec else None

    async def get_ledgers(self, keys: Sequence[str]) -> list[Ledger]:
        raws = await asyncio.gather(*(self._get(k) for k in keys))
        return [Ledger.from_raw(r) for r in raws]

    async def put_entries(self, puts: Sequence[Put], known: dict[str, Ledger] | None = None) -> None:
        by_key: dict[str, list[tuple[str, Entry]]] = {}
        for key, run_id, entry in puts:
            by_key.setdefault(key, []).append((run_id, entry))
        kv = await self._kv()

        async def save(key: str, items: list[tuple[str, Entry]]) -> None:
            base = known.get(key) if known else None
            ledger = base if base is not None else Ledger.from_raw(await self._get(key))
            runs = {k: e.to_json() for k, e in ledger.runs.items()}
            runs.update({run_id: e.to_json() for run_id, e in items})
            await kv.set_record(record_key(key), {"v": 1, "runs": runs})

        await asyncio.gather(*(save(k, v) for k, v in by_key.items()))

    async def first_seen(self, user_key: str, today: str) -> str | None:
        key = f"seen.{user_key}"
        got = await self._get(key)
        if isinstance(got, dict) and got.get("first"):
            return str(got["first"])
        await (await self._kv()).set_record(record_key(key), {"first": today})
        return today


def _text(v: Any) -> Any:
    return v.decode() if isinstance(v, bytes) else v


class RedisBackend:
    """Redis, Valkey or Upstash. One hash field per run: concurrent runs never
    overwrite each other, so counts are exact.

    `client`: redis.asyncio.Redis (pipelined: one round trip per batch), or any
    client with async hgetall/hset/expire/set/get such as
    upstash_redis.asyncio.Redis (calls made concurrently). `pipelined`
    overrides the detection.
    """

    def __init__(self, client: Any, prefix: str = "bot-guard:", *, pipelined: bool | None = None) -> None:
        self._r = client
        self._prefix = prefix
        self._pipelined = _supports_pipeline(client) if pipelined is None else pipelined

    async def get_ledgers(self, keys: Sequence[str]) -> list[Ledger]:
        full = [self._prefix + k for k in keys]
        if self._pipelined:
            pipe = self._r.pipeline(transaction=False)
            for k in full:
                pipe.hgetall(k)
            raws = await pipe.execute()
        else:
            raws = await asyncio.gather(*(self._r.hgetall(k) for k in full))
        out = []
        for raw in raws:
            runs: dict[str, Any] = {}
            for f, v in (raw or {}).items():
                try:
                    runs[_text(f)] = json.loads(_text(v))
                except (TypeError, ValueError):
                    continue
            out.append(Ledger.from_raw({"runs": runs}))
        return out

    async def put_entries(self, puts: Sequence[Put], known: dict[str, Ledger] | None = None) -> None:
        if self._pipelined:
            pipe = self._r.pipeline(transaction=False)
            for key, run_id, entry in puts:
                pipe.hset(self._prefix + key, run_id, json.dumps(entry.to_json()))
                pipe.expire(self._prefix + key, LEDGER_TTL_SECONDS)
            await pipe.execute()
            return

        async def one(key: str, run_id: str, entry: Entry) -> None:
            await self._r.hset(self._prefix + key, run_id, json.dumps(entry.to_json()))
            await self._r.expire(self._prefix + key, LEDGER_TTL_SECONDS)

        await asyncio.gather(*(one(*p) for p in puts))

    async def first_seen(self, user_key: str, today: str) -> str | None:
        k = f"{self._prefix}seen.{user_key}"
        await self._r.set(k, today, nx=True, ex=SEEN_TTL_SECONDS)
        got = _text(await self._r.get(k))
        return str(got) if got else None


def _supports_pipeline(client: Any) -> bool:
    """redis-py style: pipeline(transaction=False) with an async execute()."""
    make = getattr(client, "pipeline", None)
    if not callable(make):
        return False
    try:
        return callable(getattr(make(transaction=False), "execute", None))
    except TypeError:
        return False
