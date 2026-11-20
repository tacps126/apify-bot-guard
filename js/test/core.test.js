import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    Ledger, accountShare, allowanceOf, configFromEnv, entry, isFreePlan, makeConfig, userHash,
} from '../src/index.js';
import { dayOfMonth, nextReset } from '../src/core.js';

// Pinned in both packages: Python and JavaScript Actors can share one store.
// python3 -c "from apify_bot_guard import user_hash; print(user_hash('u1'))"
export const HASH_U1 = 'c7e697d2100b975d4301c43545656d09';

test('plan detection', () => {
    assert.equal(isFreePlan({ APIFY_USER_IS_PAYING: '' }), true);
    assert.equal(isFreePlan({ APIFY_USER_IS_PAYING: ' 0 ' }), true);
    assert.equal(isFreePlan({ APIFY_USER_IS_PAYING: '1' }), false);
    assert.equal(isFreePlan({ APIFY_USER_PRICING_TIER: 'free', APIFY_USER_IS_PAYING: '1' }), true);
    assert.equal(isFreePlan({ APIFY_USER_PRICING_TIER: 'SCALE' }), false);
    assert.equal(isFreePlan({}), null);
});

test('config from env: off switch, values, overrides, clamps', () => {
    for (const v of ['off', '0', 'false', ' OFF ']) assert.equal(configFromEnv('a', { FREE_BUDGET: v }), null);
    const cfg = configFromEnv('a', {
        FREE_ACTOR_MONTHLY_RESULTS: '50',
        FREE_DAILY_SHARE: '5',
        FREE_USER_MONTHLY_RESULTS: '-1',
        FREE_POOL_MONTHLY_RESULTS: 'lots',
        FREE_NEW_ACCOUNT_SHARE: 'Infinity',
        FREE_COST_PER_RESULT: '0',
        FREE_BUDGET_SCOPE: 'shared',
    }, { poolResults: 10, userUsd: null });
    assert.equal(cfg.actorResults, 50);
    assert.equal(cfg.dailyShare, 1);
    assert.equal(cfg.userResults, 300);
    assert.equal(cfg.poolResults, 10);
    assert.equal(cfg.newAccountShare, 0.25);
    assert.equal(cfg.costPerResult, 1e-7);
    assert.equal(cfg.scope, 'shared');
    assert.equal(configFromEnv('a').scope.length > 0, true);
});

test('ledgers tolerate junk and drop stale holds', () => {
    assert.deepEqual(Ledger.fromRaw(null).runs, {});
    assert.deepEqual(Ledger.fromRaw({ runs: 5 }).runs, {});
    const led = Ledger.fromRaw({ runs: { r: { n: '7', usd: 'x', h: 'yes' }, bad: 3 } });
    assert.deepEqual(led.runs, { r: { a: '', d: '', n: 7, usd: 0 } });
    assert.equal(Ledger.fromRaw({ r: { n: 2 } }).runs.r.n, 2);
    assert.deepEqual(entry({ a: 's', d: 'd', n: 3, usd: 0.1234567, h: true }), { a: 's', d: 'd', n: 3, usd: 0.123457, h: true });
    assert.deepEqual(entry(), { a: '', d: '', n: 0, usd: 0 });
    const l2 = new Ledger({ old: { d: '2026-11-19', h: true }, now: { d: '2026-11-20', h: true }, done: { d: '2026-11-01' } });
    l2.dropStaleHolds('2026-11-20');
    assert.deepEqual(Object.keys(l2.runs).sort(), ['done', 'now']);
});

test('tightest limit wins', () => {
    const cfg = makeConfig('a');
    const pick = (a) => [a.resultsLeft, a.bound];
    assert.deepEqual(pick(allowanceOf(cfg, new Ledger(), new Ledger(), '2026-11-30')), [34, 'day']);
    cfg.dailyShare = 1;
    assert.equal(allowanceOf(cfg, new Ledger(), new Ledger(), '2026-11-30').bound, 'month');
    assert.equal(allowanceOf(cfg, new Ledger(), new Ledger(), '2026-11-01').bound, 'month');
    const pool = new Ledger({ x: { n: 95, d: '2026-11-01' } });
    assert.deepEqual(pick(allowanceOf(cfg, new Ledger(), pool, '2026-11-01')), [5, 'pool']);
});

test('cost, cross-Actor and floor', () => {
    assert.equal(allowanceOf(makeConfig('a', { dailyShare: 1, costPerResult: 0.001 }), new Ledger(), new Ledger(), '2026-11-30').resultsLeft, 50);
    const other = new Ledger(Object.fromEntries([0, 1, 2].map((i) => [`r${i}`, { a: `b${i}`, n: 100 }])));
    assert.equal(allowanceOf(makeConfig('a', { dailyShare: 1 }), other, new Ledger(), '2026-11-30').resultsLeft, 0);
    const user = new Ledger({ r: { a: 'a', d: '2026-11-30', n: 500, usd: 9 } });
    const a = allowanceOf(makeConfig('a'), user, new Ledger(), '2026-11-30');
    assert.deepEqual([a.resultsLeft, a.usdLeft], [0, 0]);
});

test('date helpers and account share', () => {
    assert.deepEqual(dayOfMonth('2024-02-10'), [10, 29]);
    assert.deepEqual(dayOfMonth('nonsense'), [30, 30]);
    assert.equal(nextReset('2026-12'), '2027-01-01');
    assert.equal(nextReset('2026-01'), '2026-02-01');
    const cfg = makeConfig('a');
    assert.equal(accountShare(cfg, null, '2026-11-20'), 1);
    assert.equal(accountShare(cfg, 'garbage', '2026-11-20'), 1);
    assert.equal(accountShare(cfg, '2026-11-14', '2026-11-20'), 0.25);
    assert.equal(accountShare(cfg, '2026-11-13T23:00:00Z', '2026-11-20'), 1);
});

test('user hash matches the Python package', () => {
    assert.equal(userHash('u1'), HASH_U1);
    assert.notEqual(userHash('u1', 's'), HASH_U1);
});
