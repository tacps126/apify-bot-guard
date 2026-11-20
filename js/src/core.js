// Pure allowance logic: no I/O, identical to the Python package.
import { createHash } from 'node:crypto';

export const DEFAULTS = {
    actorResults: 100, // per user per Actor per month
    actorUsd: 0.05, // your cost per user per Actor per month
    userResults: 300, // per user across all Actors sharing the backend
    userUsd: 0.25,
    dailyShare: 0.34, // share of the monthly allowance usable in one day
    poolResults: 3000, // all free users of one Actor together, per month
    newAccountDays: 7,
    newAccountShare: 0.25,
    costPerResult: 0.0005, // USD, what one result costs you
};

export const ENV_NAMES = {
    actorResults: 'FREE_ACTOR_MONTHLY_RESULTS',
    actorUsd: 'FREE_ACTOR_MONTHLY_USD',
    userResults: 'FREE_USER_MONTHLY_RESULTS',
    userUsd: 'FREE_USER_MONTHLY_USD',
    dailyShare: 'FREE_DAILY_SHARE',
    poolResults: 'FREE_POOL_MONTHLY_RESULTS',
    newAccountDays: 'FREE_NEW_ACCOUNT_DAYS',
    newAccountShare: 'FREE_NEW_ACCOUNT_SHARE',
    costPerResult: 'FREE_COST_PER_RESULT',
};

/** Limits from env vars, then `overrides`. `null` when FREE_BUDGET=off. */
export function configFromEnv(scope, env = process.env, overrides = {}) {
    if (['off', '0', 'false'].includes(String(env.FREE_BUDGET ?? '').trim().toLowerCase())) return null;
    const cfg = { scope: env.FREE_BUDGET_SCOPE || scope, ...DEFAULTS };
    for (const [k, name] of Object.entries(ENV_NAMES)) {
        const raw = String(env[name] ?? '').trim();
        const v = raw === '' ? NaN : Number(raw);
        if (Number.isFinite(v) && v >= 0) cfg[k] = v;
    }
    for (const [k, v] of Object.entries(overrides)) if (v != null) cfg[k] = Number(v);
    cfg.dailyShare = Math.min(cfg.dailyShare, 1);
    cfg.newAccountShare = Math.min(cfg.newAccountShare, 1);
    cfg.costPerResult = Math.max(cfg.costPerResult, 1e-7);
    return cfg;
}

/**
 * true on Apify's free plan, false when paying, null when unknown.
 * APIFY_USER_PRICING_TIER wins when set. APIFY_USER_IS_PAYING is "1" for
 * paying users and set but EMPTY for free ones, so empty means free.
 */
export function isFreePlan(env = process.env) {
    const tier = String(env.APIFY_USER_PRICING_TIER ?? '').trim();
    if (tier) return tier.toUpperCase() === 'FREE';
    if ('APIFY_USER_IS_PAYING' in env) return String(env.APIFY_USER_IS_PAYING).trim() !== '1';
    return null;
}

export function entry({ a = '', d = '', n = 0, usd = 0, h = false } = {}) {
    const e = { a: String(a), d: String(d), n: Math.trunc(Number(n) || 0), usd: Math.round((Number(usd) || 0) * 1e6) / 1e6 };
    if (h) e.h = true;
    return e;
}

export class Ledger {
    constructor(runs = {}) {
        this.runs = {};
        for (const [k, v] of Object.entries(runs)) if (v && typeof v === 'object') this.runs[k] = entry(v);
    }

    dropStaleHolds(today) {
        for (const [k, e] of Object.entries(this.runs)) if (e.h && e.d !== today) delete this.runs[k];
    }

    #sum(keep, of) {
        return Object.values(this.runs).filter(keep).reduce((s, e) => s + of(e), 0);
    }

    resultsOn(scope) { return this.#sum((e) => e.a === scope, (e) => e.n); }
    resultsOnDay(scope, day) { return this.#sum((e) => e.a === scope && e.d === day, (e) => e.n); }
    results() { return this.#sum(() => true, (e) => e.n); }
    spentOn(scope) { return this.#sum((e) => e.a === scope, (e) => e.usd); }
    spent() { return this.#sum(() => true, (e) => e.usd); }
}

export function dayOfMonth(today) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(today);
    if (!m) return [30, 30];
    const days = new Date(Date.UTC(Number(m[1]), Number(m[2]), 0)).getUTCDate();
    return [Number(m[3]), days];
}

/** What a run may still take: { resultsLeft, usdLeft, bound: 'month'|'day'|'pool' }. */
export function allowanceOf(cfg, user, pool, today, share = 1) {
    const s = cfg.scope;
    const usdLeft = Math.max(0, Math.min(cfg.actorUsd * share - user.spentOn(s), cfg.userUsd * share - user.spent()));
    const month = Math.min(
        cfg.actorResults * share - user.resultsOn(s),
        cfg.userResults * share - user.results(),
        usdLeft / cfg.costPerResult,
    );
    const day = Math.ceil(cfg.actorResults * share * cfg.dailyShare) - user.resultsOnDay(s, today);
    const [d, days] = dayOfMonth(today);
    const poolLeft = Math.ceil((cfg.poolResults * d) / days) - pool.results();
    let best = [month, 'month'];
    for (const c of [[day, 'day'], [poolLeft, 'pool']]) if (c[0] < best[0]) best = c;
    return { resultsLeft: Math.max(0, Math.floor(best[0] + 1e-6)), usdLeft, bound: best[1] };
}

export function accountShare(cfg, created, today) {
    if (!created) return 1;
    const c = Date.parse(created.length === 10 ? `${created}T00:00:00Z` : created);
    const t = Date.parse(`${today}T00:00:00Z`);
    if (Number.isNaN(c) || Number.isNaN(t)) return 1;
    const cDay = Date.parse(`${new Date(c).toISOString().slice(0, 10)}T00:00:00Z`);
    return (t - cDay) / 86_400_000 < cfg.newAccountDays ? cfg.newAccountShare : 1;
}

/** Store keys never hold raw user ids. Same value as the Python package. */
export function userHash(userId, salt = '') {
    return createHash('sha256').update(`free-guard:${salt}:${userId}`).digest('hex').slice(0, 32);
}

export const utcToday = () => new Date().toISOString().slice(0, 10);

export function nextReset(month) {
    let [y, m] = month.split('-').map(Number);
    if (m >= 12) { y += 1; m = 1; } else m += 1;
    return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-01`;
}
