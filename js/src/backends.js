// Where ledgers live. A backend stores, per key, a map of run id -> entry.
//
// - MemoryBackend: tests and local runs.
// - ApifyKvBackend: a key-value store on YOUR Apify account, reached with
//   your token from an Actor secret env var. No extra service.
// - RedisBackend: ioredis, node-redis v4+ or @upstash/redis. One hash field
//   per run, so concurrent runs never overwrite each other.
import { Ledger } from './core.js';

export const LEDGER_TTL_SECONDS = 45 * 24 * 3600;
export const SEEN_TTL_SECONDS = 400 * 24 * 3600;

const ledger = (raw) => new Ledger((raw && (raw.runs ?? raw)) || {});

export class MemoryBackend {
    constructor() { this.data = new Map(); }

    async getLedger(key) { return ledger({ runs: this.data.get(key) }); }

    async putEntry(key, runId, e) {
        if (!this.data.has(key)) this.data.set(key, {});
        this.data.get(key)[runId] = e;
    }

    async firstSeen(userKey, today) {
        const k = `seen.${userKey}`;
        if (!this.data.has(k)) this.data.set(k, today);
        return this.data.get(k);
    }
}

/**
 * Ledgers as JSON records in one of your own key-value stores.
 * Writes are read-merge-write: two runs saving in the same instant can lose
 * one entry. Holds booked at run start keep parallel runs from each taking
 * the whole allowance, so the worst case is a small overshoot. Use
 * RedisBackend for exact counts under heavy parallel load.
 */
export class ApifyKvBackend {
    /** @param {{ token: string, store?: string, baseUrl?: string }} opts */
    constructor({ token, store = 'free-guard', baseUrl } = {}) {
        this.opts = { token, baseUrl };
        this.storeName = store;
        this.store = null;
    }

    async #kv() {
        if (!this.store) {
            const { ApifyClient } = await import('apify-client');
            const client = new ApifyClient({ token: this.opts.token, ...(this.opts.baseUrl ? { baseUrl: this.opts.baseUrl } : {}) });
            const { id } = await client.keyValueStores().getOrCreate(this.storeName);
            this.store = client.keyValueStore(id);
        }
        return this.store;
    }

    static key(k) { return k.replace(/[^a-zA-Z0-9!\-_.'()]/g, '_').slice(0, 256); }

    async #get(k) {
        const rec = await (await this.#kv()).getRecord(ApifyKvBackend.key(k));
        return rec ? rec.value : null;
    }

    async getLedger(key) { return ledger(await this.#get(key)); }

    async putEntry(key, runId, e) {
        const current = (await this.#get(key)) || {};
        const runs = { ...(current.runs || {}), [runId]: e };
        await (await this.#kv()).setRecord({ key: ApifyKvBackend.key(key), value: { v: 1, runs } });
    }

    async firstSeen(userKey, today) {
        const k = `seen.${userKey}`;
        const got = await this.#get(k);
        if (got && got.first) return String(got.first);
        await (await this.#kv()).setRecord({ key: ApifyKvBackend.key(k), value: { first: today } });
        return today;
    }
}

/**
 * `client`: an ioredis, node-redis (v4+) or @upstash/redis instance.
 * `flavor` is detected (node-redis has `hSet`, ioredis has `status`, else
 * Upstash); pass it to be explicit.
 */
export class RedisBackend {
    constructor(client, { prefix = 'free-guard:', flavor } = {}) {
        this.r = client;
        this.prefix = prefix;
        this.flavor = flavor ?? (typeof client.hSet === 'function' ? 'node-redis' : 'status' in client ? 'ioredis' : 'upstash');
    }

    async getLedger(key) {
        const k = this.prefix + key;
        const raw = (this.flavor === 'node-redis' ? await this.r.hGetAll(k) : await this.r.hgetall(k)) || {};
        const runs = {};
        for (const [f, v] of Object.entries(raw)) {
            try { runs[f] = typeof v === 'string' ? JSON.parse(v) : v; } catch { /* skip */ }
        }
        return ledger({ runs });
    }

    async putEntry(key, runId, e) {
        const k = this.prefix + key;
        const v = JSON.stringify(e);
        if (this.flavor === 'node-redis') await this.r.hSet(k, runId, v);
        else if (this.flavor === 'ioredis') await this.r.hset(k, runId, v);
        else await this.r.hset(k, { [runId]: v });
        await this.r.expire(k, LEDGER_TTL_SECONDS);
    }

    async firstSeen(userKey, today) {
        const k = `${this.prefix}seen.${userKey}`;
        if (this.flavor === 'node-redis') await this.r.set(k, today, { NX: true, EX: SEEN_TTL_SECONDS });
        else if (this.flavor === 'ioredis') await this.r.set(k, today, 'EX', SEEN_TTL_SECONDS, 'NX');
        else await this.r.set(k, today, { nx: true, ex: SEEN_TTL_SECONDS });
        return this.r.get(k);
    }
}
