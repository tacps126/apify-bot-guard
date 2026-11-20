// Every control in docs/CONFIGURATION.md, through the env and through code. Mirrors test_controls.py.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    BotGuard, DEFAULTS, LIMIT_FIELDS, Ledger, MemoryBackend, allowanceOf, configFromEnv, envName, makeConfig, planOf,
} from '../src/index.js';
import { needsLastMonth, weekReset } from '../src/core.js';

const FREE = { APIFY_USER_ID: 'u1', APIFY_USER_IS_PAYING: '', ACTOR_RUN_ID: 'r1' };
const OLD = '2020-01-01';
const NO_DEFAULTS = { ...Object.fromEntries(LIMIT_FIELDS.map((k) => [k, null])), dailyShare: null };
const quiet = { info() {}, warn() {} };

const only = (limits = {}) => makeConfig('a', { ...NO_DEFAULTS, ...limits });
const admit = (env, opts = {}) => BotGuard.admit('a', opts.backend ?? new MemoryBackend(), {
    env: env ?? FREE, today: '2026-11-20', accountCreated: OLD, log: quiet, ...opts,
});

test('every setting has an env var', () => {
    assert.equal(envName('actorWeekResults'), 'BOT_GUARD_ACTOR_WEEK_RESULTS');
    assert.equal(LIMIT_FIELDS.length, 15);
    const cfg = configFromEnv('a', {
        BOT_GUARD_ACTOR_WEEK_RESULTS: '70',
        BOT_GUARD_USER_DAY_USD: '0.01',
        BOT_GUARD_RUN_RESULTS: '20',
        BOT_GUARD_ACTOR_MONTH_USD: 'none',
        BOT_GUARD_POOL_MONTH_RESULTS: 'unlimited',
        BOT_GUARD_PLANS: 'free, starter',
        BOT_GUARD_EXEMPT_USERS: 'me,tester',
        BOT_GUARD_BLOCKED_USERS: 'bad',
        BOT_GUARD_MODE: ' Observe ',
        BOT_GUARD_MESSAGE: 'Limit: {delivered} ',
        BOT_GUARD_SALT: 's1',
        BOT_GUARD_TRACK_FROM: '2026-11-01',
        BOT_GUARD_AUTO_PRUNE: '0',
    });
    assert.deepEqual([cfg.actorWeekResults, cfg.userDayUsd, cfg.runResults], [70, 0.01, 20]);
    assert.equal(cfg.actorMonthUsd, null);
    assert.equal(cfg.poolMonthResults, null);
    assert.equal(cfg.actorMonthResults, DEFAULTS.actorMonthResults);
    assert.deepEqual(cfg.plans, ['FREE', 'STARTER']);
    assert.deepEqual([...cfg.exemptUsers], ['me', 'tester']);
    assert.deepEqual([...cfg.blockedUsers], ['bad']);
    assert.deepEqual([cfg.mode, cfg.message, cfg.salt, cfg.trackFrom, cfg.autoPrune], ['observe', 'Limit: {delivered} ', 's1', '2026-11-01', 0]);
    assert.deepEqual(cfg.problems, []);
});

test('code overrides env; unknown names fail', () => {
    const cfg = configFromEnv('a', { BOT_GUARD_RUN_RESULTS: '20' }, { runResults: 5, plans: ['trial'], exemptUsers: 'a,b' });
    assert.deepEqual([cfg.runResults, cfg.plans, [...cfg.exemptUsers]], [5, ['TRIAL'], ['a', 'b']]);
    assert.throws(() => configFromEnv('a', {}, { monthlyQuota: 5 }), /unknown bot-guard setting: monthlyQuota/);
});

test('bad values are reported, not fatal', async () => {
    const cfg = configFromEnv('a', { BOT_GUARD_RUN_RESULTS: 'ten', BOT_GUARD_MODE: 'strict' });
    assert.equal(cfg.runResults, null);
    assert.equal(cfg.mode, 'enforce');
    assert.equal(cfg.problems.length, 2);
    assert.equal(makeConfig('a', { runUsd: -1, userDayResults: true }).problems.length, 2);
    const warned = [];
    await admit({ ...FREE, BOT_GUARD_RUN_RESULTS: 'ten' }, { log: { info() {}, warn: (m) => warned.push(m) } });
    assert.match(warned[0], /BOT_GUARD_RUN_RESULTS='ten'/);
});

test('absolute day, week and run limits', () => {
    const user = new Ledger({ x: { a: 'a', d: '2026-11-20', n: 4 }, y: { a: 'a', d: '2026-11-15', n: 10 } });
    const cases = [
        [{ actorDayResults: 5 }, [1, 'day']],
        [{ actorWeekResults: 20 }, [6, 'week']],
        [{ userWeekResults: 30, actorWeekResults: 50 }, [16, 'week']],
        [{ runResults: 3 }, [3, 'run']],
        [{ actorMonthResults: 12 }, [0, 'month']],
        [{ actorDayResults: 50, runResults: 50 }, [46, 'day']],
    ];
    for (const [limits, want] of cases) {
        const a = allowanceOf(only(limits), user, new Ledger(), '2026-11-20');
        assert.deepEqual([a.resultsLeft, a.bound], want, JSON.stringify(limits));
    }
});

test('price limits per window', () => {
    const user = new Ledger({ x: { a: 'a', d: '2026-11-20', n: 10, usd: 0.004 } });
    let a = allowanceOf(only({ actorDayUsd: 0.005, costPerResult: 0.0001 }), user, new Ledger(), '2026-11-20');
    assert.deepEqual([a.resultsLeft, a.bound], [10, 'day']);
    assert.ok(Math.abs(a.usdLeft - 0.001) < 1e-9);
    a = allowanceOf(only({ userWeekUsd: 0.01, runUsd: 0.002, costPerResult: 0.0001 }), user, new Ledger(), '2026-11-20');
    assert.deepEqual([a.resultsLeft, a.bound], [20, 'run']);
    assert.ok(Math.abs(a.usdLeft - 0.002) < 1e-9);
    assert.equal(allowanceOf(only({ runResults: 5 }), user, new Ledger(), '2026-11-20').usdLeft, null);
});

test('the week reaches into last month', () => {
    const cfg = only({ actorWeekResults: 50 });
    assert.equal(needsLastMonth(cfg, '2026-12-03'), true);
    assert.equal(needsLastMonth(cfg, '2026-12-07'), false);
    assert.equal(needsLastMonth(only({ actorMonthResults: 5 }), '2026-12-01'), false);
    const last = new Ledger({ o: { a: 'a', d: '2026-11-28', n: 30 }, older: { a: 'a', d: '2026-11-20', n: 99 } });
    const a = allowanceOf(cfg, new Ledger(), new Ledger(), '2026-12-03', 1, { lastMonth: last });
    assert.deepEqual([a.resultsLeft, a.weekStart], [20, '2026-11-27']);
    assert.equal(weekReset(new Ledger(), last, a.weekStart), '2026-12-05');
    assert.equal(weekReset(new Ledger(), null, a.weekStart), '2026-12-04');
});

test('the guard reads last month for the week', async () => {
    const backend = new MemoryBackend();
    const weekly = { actorWeekResults: 50, actorMonthResults: null, dailyShare: null };
    const d = await admit(FREE, { backend, today: '2026-11-28', ...weekly });
    d.guard.take(30);
    await d.guard.close();
    const d2 = await admit({ ...FREE, ACTOR_RUN_ID: 'r2' }, { backend, today: '2026-12-02', ...weekly });
    assert.deepEqual([d2.remaining, d2.bound, d2.resetsOn], [20, 'week', '2026-12-05']);
    d2.guard.take(25);
    assert.equal(d2.guard.limited, true);
    assert.match(d2.guard.message, /on 2026-12-05 \(UTC\)/);
});

test('plans', async () => {
    assert.equal(planOf({ APIFY_USER_PRICING_TIER: 'starter' }), 'STARTER');
    assert.equal(planOf({ APIFY_USER_IS_PAYING: '1' }), 'PAID');
    const starter = { ...FREE, APIFY_USER_PRICING_TIER: 'STARTER' };
    assert.equal((await admit(starter)).unlimited, true);
    assert.equal((await admit(starter, { plans: ['FREE', 'STARTER'] })).unlimited, false);
    assert.equal((await admit({}, { userId: 'acct', plan: 'trial', plans: ['trial'] })).unlimited, false);
    assert.equal((await admit({}, { userId: 'acct', plan: 'pro', plans: ['trial'] })).unlimited, true);
    assert.equal((await admit({}, { userId: 'acct' })).unlimited, true);
});

test('exempt and blocked users', async () => {
    const backend = new MemoryBackend();
    assert.equal((await admit(FREE, { backend, exemptUsers: ['u1'] })).unlimited, true);
    const d = await admit(FREE, { backend, blockedUsers: ['u1'] });
    assert.deepEqual([d.allowed, d.remaining, d.bound, d.resetsOn], [false, 0, 'blocked', null]);
    assert.match(d.message, /not available for this account/);
    await d.guard.close();
    assert.equal(backend.data.size, 0);
});

test('observe mode measures but never cuts', async () => {
    const backend = new MemoryBackend();
    const d = await admit(FREE, { backend, mode: 'observe', runResults: 5 });
    assert.deepEqual([d.allowed, d.enforced, d.remaining, d.bound], [true, false, null, 'run']);
    assert.equal(d.guard.take(8), 8);
    assert.equal(d.guard.over, true);
    assert.equal(d.guard.limited, false);
    await d.guard.close();
    const [led] = await backend.getLedgers([[...backend.data.keys()].find((k) => k.startsWith('user.'))]);
    assert.equal(led.runs.r1.n, 8);
    const blocked = await admit(FREE, { mode: 'observe', blockedUsers: ['u1'] });
    assert.deepEqual([blocked.allowed, blocked.enforced], [true, false]);
});

test('no limits means no guard; custom message; settings on top of a config; salt', async () => {
    assert.equal((await admit(FREE, { config: only() })).unlimited, true);
    const m = await admit(FREE, { runResults: 0, message: 'Used {delivered}; bound {bound}; more {when} ({resets_on}).' });
    assert.equal(m.message, 'Used 0; bound run; more in a new run (2026-11-20).');
    assert.equal((await admit(FREE, { config: only({ runResults: 5 }), settings: { runResults: 2 } })).remaining, 2);
    const plain = (await admit()).guard.userKey;
    const salted = (await admit(FREE, { salt: 'pepper' })).guard.userKey;
    assert.notEqual(plain, salted);
});
