import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FreeGuard, Ledger, MemoryBackend, RedisBackend, allowanceOf, configFromEnv, isFreePlan, userHash } from '../src/index.js';

const FREE = { APIFY_USER_ID: 'u1', APIFY_USER_IS_PAYING: '', ACTOR_RUN_ID: 'r1' };
const quiet = { info() {}, warn() {} };
const open = (b, env, o = {}) => FreeGuard.open('a', b, { env, log: quiet, trackFrom: '2099-01-01', ...o });

test('empty paying flag means free', () => {
    assert.equal(isFreePlan({ APIFY_USER_IS_PAYING: '' }), true);
    assert.equal(isFreePlan({ APIFY_USER_IS_PAYING: '1' }), false);
    assert.equal(isFreePlan({ APIFY_USER_PRICING_TIER: 'FREE', APIFY_USER_IS_PAYING: '1' }), true);
    assert.equal(isFreePlan({}), null);
    assert.equal(configFromEnv('a', { FREE_BUDGET: 'off' }), null);
});

test('tightest limit wins', () => {
    const cfg = configFromEnv('a', {});
    assert.deepEqual(pick(allowanceOf(cfg, new Ledger(), new Ledger(), '2026-11-30')), [34, 'day']);
    cfg.dailyShare = 1;
    const pool = new Ledger({ x: { n: 95, d: '2026-11-01' } });
    assert.deepEqual(pick(allowanceOf(cfg, new Ledger(), pool, '2026-11-01')), [5, 'pool']);
    cfg.costPerResult = 0.001;
    assert.equal(allowanceOf(cfg, new Ledger(), new Ledger(), '2026-11-30').resultsLeft, 50);
});

test('a month of runs', async () => {
    const b = new MemoryBackend();
    const one = async (runId, wanted, today) => {
        const g = await open(b, { ...FREE, ACTOR_RUN_ID: runId }, { today });
        const got = g.take(wanted);
        await g.close();
        return [got, g];
    };
    let [got, g] = await one('r1', 500, '2026-11-20');
    assert.equal(got, 34);
    assert.ok(g.limited && g.message.includes('tomorrow'));
    assert.equal((await one('r2', 500, '2026-11-20'))[0], 0);
    assert.equal((await one('r3', 500, '2026-11-21'))[0], 34);
    assert.equal((await one('r4', 500, '2026-11-22'))[0], 32);
    [got, g] = await one('r5', 500, '2026-11-23');
    assert.equal(got, 0);
    assert.ok(g.message.includes('2026-12-01'));
});

test('paying users, new accounts, holds, broken storage', async () => {
    assert.equal(await open(new MemoryBackend(), { ...FREE, APIFY_USER_IS_PAYING: '1' }), null);
    const g = await open(new MemoryBackend(), FREE, { today: '2026-11-20', accountCreated: '2026-11-18T10:00:00Z' });
    assert.equal(g.allowance.resultsLeft, 9);
    const b = new MemoryBackend();
    const g1 = await open(b, FREE, { today: '2026-11-20' });
    const g2 = await open(b, { ...FREE, ACTOR_RUN_ID: 'r2' }, { today: '2026-11-20' });
    assert.equal(g1.allowance.resultsLeft, 34);
    assert.equal(g2.allowance.resultsLeft, 0);
    const broken = Object.assign(new MemoryBackend(), { getLedger: async () => { throw new Error('down'); } });
    assert.equal(await open(broken, FREE), null);
});

test('same user hash as the Python package', () => {
    assert.equal(userHash('u1'), 'b563b03be6a7a0ad597e7ac60b180aea');
});

for (const flavor of ['ioredis', 'node-redis', 'upstash']) {
    test(`redis backend (${flavor})`, async () => {
        const h = new Map(); const kv = new Map();
        const hset = flavor === 'upstash' ? async (k, obj) => { h.set(k, { ...(h.get(k) || {}), ...obj }); } : async (k, f, v) => { h.set(k, { ...(h.get(k) || {}), [f]: v }); };
        const r = {
            [flavor === 'node-redis' ? 'hSet' : 'hset']: hset,
            [flavor === 'node-redis' ? 'hGetAll' : 'hgetall']: async (k) => h.get(k) || {},
            expire: async () => 1,
            set: async (k, v, ...a) => { const nx = JSON.stringify(a).toLowerCase().includes('nx'); if (!(nx && kv.has(k))) kv.set(k, v); },
            get: async (k) => kv.get(k) ?? null,
        };
        if (flavor === 'ioredis') r.status = 'ready';
        const b = new RedisBackend(r);
        assert.equal(b.flavor, flavor);
        const g = await open(b, FREE, { today: '2026-11-20' });
        g.take(10);
        await g.close();
        const led = await b.getLedger(g.userKey);
        assert.equal(led.runs.r1.n, 10);
        assert.equal(led.runs.r1.h, undefined);
        assert.equal(await b.firstSeen('x', '2026-11-20'), '2026-11-20');
        assert.equal(await b.firstSeen('x', '2026-11-21'), '2026-11-20');
    });
}

function pick(a) { return [a.resultsLeft, a.bound]; }
