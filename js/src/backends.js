// Where ledgers live. A backend stores, per key, a map of run id -> entry.
// Every backend works in batches, so a run costs a fixed, small number of
// round trips: one batch read at start, one batch write for the hold, one at
// the end.
import { Ledger } from './core.js';

export const LEDGER_TTL_SECONDS = 45 * 24 * 3600; // a month plus slack
export const SEEN_TTL_SECONDS = 400 * 24 * 3600;

/** In-process store for tests and local runs. */
export class MemoryBackend {
    constructor() {
        this.data = new Map();
    }

    async getLedgers(keys) {
        return keys.map((k) => Ledger.fromRaw(this.data.get(k)));
    }

    async putEntries(puts) {
        for (const [key, runId, e] of puts) {
            if (!this.data.has(key)) this.data.set(key, {});
            this.data.get(key)[runId] = e;
        }
    }

    async firstSeen(userKey, today) {
        const k = `seen.${userKey}`;
        if (!this.data.has(k)) this.data.set(k, today);
        return this.data.get(k);
    }
}

/** Apify record keys allow a-zA-Z0-9!-_.'() and at most 256 characters. */
export const recordKey = (k) => k.replace(/[^a-zA-Z0-9!\-_.'()]/g, '_').slice(0, 256);

/**
 * Ledgers as JSON records in a key-value store on YOUR Apify account.
 *
 * The run's own token belongs to the user and cannot write to your stores, so
 * pass your token (an Actor secret env var). Writes are read-merge-write: two
 * runs saving in the same instant can lose one entry. The hold booked at run
 * start keeps parallel runs from each taking the whole allowance, so the worst
 * case is a small overshoot. Use RedisBackend for exact counts.
 */
export class ApifyKvBackend {
    /** @param {{ token?: string, store?: string, baseUrl?: string, client?: object }} opts */
    constructor({ token, store = 'bot-guard', baseUrl, client } = {}) {
        this.opts = { token, baseUrl };
        this.client = client ?? null;
        this.storeName = store;
        this.store = null;
    }

    #kv() {
        // One lookup even when the first reads run in parallel.
        this.store ??= (async () => {
            if (!this.client) {
                const { ApifyClient } = await import('apify-client');
                this.client = new ApifyClient({ token: this.opts.token, ...(this.opts.baseUrl ? { baseUrl: this.opts.baseUrl } : {}) });
            }
            const { id } = await this.client.keyValueStores().getOrCreate(this.storeName);
            return this.client.keyValueStore(id);
        })().catch((e) => {
            this.store = null;
            throw e;
        });
        return this.store;
    }

    async #get(k) {
        const rec = await (await this.#kv()).getRecord(recordKey(k));
        return rec ? rec.value : null;
    }

    async getLedgers(keys) {
        return (await Promise.all(keys.map((k) => this.#get(k)))).map((raw) => Ledger.fromRaw(raw));
    }

    async putEntries(puts, known) {
        const byKey = new Map();
        for (const [key, runId, e] of puts) {
            if (!byKey.has(key)) byKey.set(key, {});
            byKey.get(key)[runId] = e;
        }
        const kv = await this.#kv();
        await Promise.all([...byKey].map(async ([key, mine]) => {
            const base = known?.[key] ?? Ledger.fromRaw(await this.#get(key));
            await kv.setRecord({ key: recordKey(key), value: { v: 1, runs: { ...base.runs, ...mine } } });
        }));
    }

    async firstSeen(userKey, today) {
        const k = `seen.${userKey}`;
        const got = await this.#get(k);
        if (got?.first) return String(got.first);
        await (await this.#kv()).setRecord({ key: recordKey(k), value: { first: today } });
        return today;
    }
}

/**
 * Redis, Valkey or Upstash. One hash field per run: concurrent runs never
 * overwrite each other, so counts are exact. Each batch is one pipelined
 * round trip.
 *
 * `client`: an ioredis, node-redis (v4+) or @upstash/redis instance. `flavor`
 * is detected (node-redis has `hSet`, ioredis has `status`, else Upstash);
 * pass it to be explicit.
 */
export class RedisBackend {
    constructor(client, { prefix = 'bot-guard:', flavor } = {}) {
        this.r = client;
        this.prefix = prefix;
        this.flavor = flavor ?? (typeof client.hSet === 'function' ? 'node-redis' : 'status' in client ? 'ioredis' : 'upstash');
    }

    #pipe() {
        return this.flavor === 'node-redis' ? this.r.multi() : this.r.pipeline();
    }

    async #exec(p) {
        if (this.flavor === 'node-redis') return p.execAsPipeline();
        const out = await p.exec();
        // ioredis answers [[err, value], ...]
        return this.flavor === 'ioredis' ? out.map(([err, v]) => {
            if (err) throw err;
            return v;
        }) : out;
    }

    async getLedgers(keys) {
        const p = this.#pipe();
        for (const k of keys) (this.flavor === 'node-redis' ? p.hGetAll(this.prefix + k) : p.hgetall(this.prefix + k));
        const raws = await this.#exec(p);
        return raws.map((raw) => {
            const runs = {};
            for (const [f, v] of Object.entries(raw || {})) {
                try {
                    runs[f] = typeof v === 'string' ? JSON.parse(v) : v;
                } catch { /* skip unreadable entries */ }
            }
            return new Ledger(runs);
        });
    }

    async putEntries(puts) {
        const p = this.#pipe();
        for (const [key, runId, e] of puts) {
            const k = this.prefix + key;
            const v = JSON.stringify(e);
            if (this.flavor === 'node-redis') p.hSet(k, runId, v);
            else if (this.flavor === 'ioredis') p.hset(k, runId, v);
            else p.hset(k, { [runId]: v });
            p.expire(k, LEDGER_TTL_SECONDS);
        }
        await this.#exec(p);
    }

    async firstSeen(userKey, today) {
        const k = `${this.prefix}seen.${userKey}`;
        if (this.flavor === 'node-redis') await this.r.set(k, today, { NX: true, EX: SEEN_TTL_SECONDS });
        else if (this.flavor === 'ioredis') await this.r.set(k, today, 'EX', SEEN_TTL_SECONDS, 'NX');
        else await this.r.set(k, today, { nx: true, ex: SEEN_TTL_SECONDS });
        return this.r.get(k);
    }
}
