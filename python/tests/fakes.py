"""In-memory stand-ins for the Apify client, redis-py and Upstash, counting round trips."""

from __future__ import annotations

import asyncio
import copy
from typing import Any


class FakeKv:
    def __init__(self, owner: FakeApifyClient) -> None:
        self.owner = owner
        self.records: dict[str, Any] = {}

    async def get_record(self, key: str) -> dict[str, Any] | None:
        self.owner.calls += 1
        if self.owner.fail:
            raise RuntimeError("store down")
        return {"key": key, "value": copy.deepcopy(self.records[key])} if key in self.records else None

    async def set_record(self, key: str, value: Any) -> None:
        self.owner.calls += 1
        self.records[key] = copy.deepcopy(value)

    async def list_keys(self, limit: int = 1000, exclusive_start_key: str | None = None) -> dict[str, Any]:
        self.owner.calls += 1
        keys = sorted(self.records)
        if exclusive_start_key is not None:
            keys = [k for k in keys if k > exclusive_start_key]
        page = keys[:limit]
        more = len(keys) > limit
        return {
            "items": [{"key": k} for k in page],
            "isTruncated": more,
            "nextExclusiveStartKey": page[-1] if more else None,
        }

    async def delete_record(self, key: str) -> None:
        self.owner.calls += 1
        self.records.pop(key, None)


class FakeStores:
    def __init__(self, owner: FakeApifyClient) -> None:
        self.owner = owner

    async def get_or_create(self, name: str) -> dict[str, str]:
        await asyncio.sleep(0)  # yield, like a real request, so races show
        self.owner.calls += 1
        return {"id": f"id-{name}"}


class FakeApifyClient:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.kv = FakeKv(self)

    def key_value_stores(self) -> FakeStores:
        return FakeStores(self)

    def key_value_store(self, store_id: str) -> FakeKv:
        return self.kv


class _Store:
    def __init__(self) -> None:
        self.h: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.round_trips = 0

    def hgetall(self, k: str) -> dict[bytes, bytes]:
        return {f.encode(): v.encode() for f, v in self.h.get(k, {}).items()}

    def hset(self, k: str, f: str, v: str) -> int:
        self.h.setdefault(k, {})[f] = v
        return 1

    def set(self, k: str, v: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def get(self, k: str) -> bytes | None:
        v = self.kv.get(k)
        return v.encode() if v else None


class _Pipe:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.ops: list[tuple[str, tuple[Any, ...]]] = []

    def hgetall(self, *a: Any) -> _Pipe:
        self.ops.append(("hgetall", a))
        return self

    def hset(self, *a: Any) -> _Pipe:
        self.ops.append(("hset", a))
        return self

    def expire(self, *a: Any) -> _Pipe:
        self.ops.append(("expire", a))
        return self

    async def execute(self) -> list[Any]:
        self.store.round_trips += 1
        out = []
        for op, a in self.ops:
            out.append(True if op == "expire" else getattr(self.store, op)(*a))
        return out


class FakeRedis:
    """redis.asyncio.Redis shape: pipeline(transaction=False).execute()."""

    def __init__(self) -> None:
        self.s = _Store()

    def pipeline(self, transaction: bool = True) -> _Pipe:
        return _Pipe(self.s)

    async def set(self, *a: Any, **k: Any) -> Any:
        self.s.round_trips += 1
        return self.s.set(*a, **k)

    async def get(self, k: str) -> Any:
        self.s.round_trips += 1
        return self.s.get(k)


class FakeUpstash:
    """upstash_redis.asyncio.Redis shape: plain async commands, str values."""

    def __init__(self) -> None:
        self.s = _Store()

    async def hgetall(self, k: str) -> dict[str, str]:
        self.s.round_trips += 1
        return dict(self.s.h.get(k, {}))

    async def hset(self, k: str, f: str, v: str) -> int:
        self.s.round_trips += 1
        return self.s.hset(k, f, v)

    async def expire(self, k: str, s: int) -> bool:
        self.s.round_trips += 1
        return True

    async def set(self, *a: Any, **k: Any) -> Any:
        self.s.round_trips += 1
        return self.s.set(*a, **k)

    async def get(self, k: str) -> str | None:
        self.s.round_trips += 1
        return self.s.kv.get(k)
