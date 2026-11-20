// In-memory stand-ins for the Apify client and the three Redis clients, counting round trips.

export class FakeApifyClient {
    constructor() {
        this.calls = 0;
        this.fail = false;
        this.records = new Map();
    }

    keyValueStores() {
        return { getOrCreate: async (name) => { await null; this.calls += 1; return { id: `id-${name}` }; } };
    }

    keyValueStore() {
        return {
            getRecord: async (key) => {
                this.calls += 1;
                if (this.fail) throw new Error('store down');
                return this.records.has(key) ? { key, value: structuredClone(this.records.get(key)) } : undefined;
            },
            setRecord: async ({ key, value }) => {
                this.calls += 1;
                this.records.set(key, structuredClone(value));
            },
        };
    }
}

function store() {
    return { h: new Map(), kv: new Map(), roundTrips: 0 };
}

const hgetall = (s, k) => ({ ...(s.h.get(k) || {}) });
const hset = (s, k, f, v) => { s.h.set(k, { ...(s.h.get(k) || {}), [f]: v }); return 1; };
const setNx = (s, k, v, nx) => { if (!(nx && s.kv.has(k))) s.kv.set(k, v); };

export function fakeRedis(flavor) {
    const s = store();
    const queue = () => {
        const ops = [];
        const p = {
            hgetall: (k) => { ops.push(() => hgetall(s, k)); return p; },
            hGetAll: (k) => { ops.push(() => hgetall(s, k)); return p; },
            hset: (k, f, v) => {
                ops.push(() => (typeof f === 'object' ? Object.entries(f).forEach(([ff, vv]) => hset(s, k, ff, vv)) : hset(s, k, f, v)));
                return p;
            },
            hSet: (k, f, v) => { ops.push(() => hset(s, k, f, v)); return p; },
            expire: () => { ops.push(() => 1); return p; },
            run: () => { s.roundTrips += 1; return ops.map((op) => op()); },
        };
        return p;
    };
    const r = {
        s,
        get: async (k) => { s.roundTrips += 1; return s.kv.get(k) ?? null; },
    };
    if (flavor === 'node-redis') {
        r.hSet = () => { throw new Error('use the pipeline'); };
        r.multi = () => { const p = queue(); p.execAsPipeline = async () => p.run(); return p; };
        r.set = async (k, v, o) => { s.roundTrips += 1; setNx(s, k, v, o?.NX); };
    } else if (flavor === 'ioredis') {
        r.status = 'ready';
        r.pipeline = () => { const p = queue(); p.exec = async () => p.run().map((v) => [null, v]); return p; };
        r.set = async (k, v, ...a) => { s.roundTrips += 1; setNx(s, k, v, a.includes('NX')); };
    } else {
        r.pipeline = () => { const p = queue(); p.exec = async () => p.run(); return p; };
        r.set = async (k, v, o) => { s.roundTrips += 1; setNx(s, k, v, o?.nx); };
    }
    return r;
}
