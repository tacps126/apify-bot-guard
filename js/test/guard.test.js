import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
    ApifyKvBackend, BotGuard, MemoryBackend, RedisBackend, makeConfig, pushData, recordKey,
} from '../src/index.js';
import { FakeApifyClient, fakeRedis } from './fakes.js';

const FREE = { APIFY_USER_ID: 'u1', APIFY_USER_IS_PAYING: '', ACTOR_RUN_ID: 'r1' };
const LATER = '2099-01-01'; // first-seen days before this predate tracking: users are not "new"
const quiet = { info() {}, warn() {} };
const open = (b, env = FREE, o = {}) => BotGuard.open('a', b, { env, log: quiet, trackFrom: LATER, ...o });

async function one(b, runId, wanted, today, o = {}) {
    const g = await open(b, { ...FREE, ACTOR_RUN_ID: runId }, { today, ...o });
    const got = g.take(wanted);
    await g.close();
    return [got, g];
}

const backends = () => [
    ['memory', new MemoryBackend()],
    ['apify', new ApifyKvBackend({ client: new FakeApifyClient() })],
    ['ioredis', new RedisBackend(fakeRedis('ioredis'))],
    ['node-redis', new RedisBackend(fakeRedis('node-redis'))],
    ['upstash', new RedisBackend(fakeRedis('upstash'))],
];

for (const [name, make] of backends().map(([n], i) => [n, () => backends()[i][1]])) {
    describe(`${name} backend`, () => {
        test('a month of runs', async () => {
            const b = make();
            let [got, g] = await one(b, 'r1', 500, '2026-11-20');
            assert.equal(got, 34);
            assert.ok(g.limited && g.message.includes('tomorrow (UTC)'));
            assert.equal((await one(b, 'r2', 500, '2026-11-20'))[0], 0);
            assert.equal((await one(b, 'r3', 500, '2026-11-21'))[0], 34);
            assert.equal((await one(b, 'r4', 500, '2026-11-22'))[0], 32);
            [got, g] = await one(b, 'r5', 500, '2026-11-23');
            assert.equal(got, 0);
            assert.ok(g.message.includes('2026-12-01'));
        });

        test('hold stops parallel runs; booking replaces it', async () => {
            const b = make();
            const g1 = await open(b, FREE, { today: '2026-11-20' });
            const g2 = await open(b, { ...FREE, ACTOR_RUN_ID: 'r2' }, { today: '2026-11-20' });
            assert.deepEqual([g1.allowance.resultsLeft, g2.allowance.resultsLeft], [34, 0]);
            g1.take(10);
            await g1.close();
            const [led] = await b.getLedgers([g1.userKey]);
            assert.deepEqual(led.runs.r1, { a: 'a', d: '2026-11-20', n: 10, usd: 0.005 });
            const g3 = await open(b, { ...FREE, ACTOR_RUN_ID: 'r3' }, { today: '2026-11-20' });
            assert.equal(g3.allowance.resultsLeft, 24);
        });

        test('first seen is kept', async () => {
            const b = make();
            assert.equal(await b.firstSeen('x', '2026-11-20'), '2026-11-20');
            assert.equal(await b.firstSeen('x', '2026-11-21'), '2026-11-20');
        });
    });
}

test('no guard for paying, unknown, off or anonymous users', async () => {
    const b = new MemoryBackend();
    assert.equal(await open(b, { ...FREE, APIFY_USER_IS_PAYING: '1' }), null);
    assert.equal(await open(b, { APIFY_USER_ID: 'u1' }), null);
    assert.equal(await open(b, { ...FREE, FREE_BUDGET: 'off' }), null);
    assert.equal(await open(b, { ...FREE, APIFY_USER_ID: '' }), null);
    assert.equal(b.data.size, 0);
});

test('generic identity off Apify', async () => {
    const b = new MemoryBackend();
    const g = await BotGuard.open('api', b, { env: {}, userId: 'acct-9', free: true, today: '2026-11-20', trackFrom: LATER, log: quiet });
    assert.equal(g.allowance.resultsLeft, 34);
    assert.ok(g.runId.startsWith('local-'));
    assert.equal(await BotGuard.open('api', b, { env: {}, userId: 'acct-9', free: false, log: quiet }), null);
    assert.notEqual(await BotGuard.open('api', b, { env: {}, userId: 'acct-9', free: false, force: true, log: quiet }), null);
});

test('new accounts get a quarter', async () => {
    let g = await open(new MemoryBackend(), FREE, { today: '2026-11-20', accountCreated: '2026-11-18', trackFrom: undefined });
    assert.equal(g.allowance.resultsLeft, 9);
    g = await open(new MemoryBackend(), FREE, { today: '2026-11-20', trackFrom: undefined });
    assert.equal(g.allowance.resultsLeft, 9);
});

test('explicit config, cost guard, remaining', async () => {
    const g = await open(new MemoryBackend(), FREE, { config: makeConfig('x', { dailyShare: 1 }), today: '2026-11-30' });
    assert.equal(g.poolKey, 'pool.2026-11.x');
    assert.equal(g.take(10, 0.01), 10);
    assert.equal(g.take(10, 1), 0);
    assert.equal(g.take(-3), 0);
    assert.equal(g.remaining, 90);
});

test('storage failures fail open', async () => {
    const c = new FakeApifyClient();
    c.fail = true;
    assert.equal(await open(new ApifyKvBackend({ client: c })), null);
    const b = Object.assign(new MemoryBackend(), { putEntries: async () => { throw new Error('read only'); } });
    const g = await open(b, FREE, { today: '2026-11-20' });
    g.take(1);
    await g.close();
    await g.close();
    const warned = [];
    const g2 = await BotGuard.open('a', b, { env: FREE, log: { warn: (m) => warned.push(m) }, today: '2026-11-20', trackFrom: LATER });
    await g2.close();
    assert.equal(warned.length, 2);
});

test('round trips: redis pipelines each batch, apify hold reuses the read', async () => {
    for (const flavor of ['ioredis', 'node-redis', 'upstash']) {
        const r = fakeRedis(flavor);
        await one(new RedisBackend(r), 'r1', 5, '2026-11-20', { accountCreated: '2020-01-01' });
        assert.equal(r.s.roundTrips, 3, flavor);
    }
    const c = new FakeApifyClient();
    await one(new ApifyKvBackend({ client: c }), 'r1', 5, '2026-11-20', { accountCreated: '2020-01-01' });
    assert.equal(c.calls, 9); // store lookup + 2 reads + 2 hold writes + book (2 re-reads + 2 writes)
});

test('redis flavor can be forced; junk fields skipped', async () => {
    const r = fakeRedis('upstash');
    r.s.h.set('bot-guard:k', { good: '{"n":2}', bad: '{nope' });
    const b = new RedisBackend(r, { flavor: 'upstash' });
    const [led] = await b.getLedgers(['k']);
    assert.deepEqual(Object.keys(led.runs), ['good']);
});

test('record keys are valid', () => {
    assert.equal(recordKey('user.2026-11.ab/cd é'), 'user.2026-11.ab_cd__');
    assert.equal(recordKey('x'.repeat(300)).length, 256);
});

test('pushData cuts and sets the status message', async () => {
    const pushed = [];
    const status = [];
    const Actor = { pushData: async (i) => pushed.push(...i), setStatusMessage: async (m) => status.push(m) };
    const g = await open(new MemoryBackend(), FREE, { today: '2026-11-20' });
    assert.equal(await pushData(g, Array.from({ length: 30 }, (_, i) => ({ i })), Actor), 30);
    assert.equal(await pushData(g, Array.from({ length: 30 }, (_, i) => ({ i })), Actor), 4);
    assert.equal(await pushData(g, { i: 99 }, Actor), 0);
    assert.equal(await pushData(null, { i: 1 }, Actor), 1);
    assert.equal(pushed.length, 35);
    assert.equal(status.length, 2);
});
