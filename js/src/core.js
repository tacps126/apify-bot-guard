// Pure allowance logic. No I/O; kept identical to the Python package.
import { createHash } from 'node:crypto';

export const DEFAULTS = Object.freeze({
    actorResults: 100,
    actorUsd: 0.05,
    userResults: 300,
    userUsd: 0.25,
    dailyShare: 0.34,
    poolResults: 3000,
    newAccountDays: 7,
    newAccountShare: 0.25,
    costPerResult: 0.0005,
});

export const ENV_NAMES = Object.freeze({
    actorResults: 'FREE_ACTOR_MONTHLY_RESULTS',
    actorUsd: 'FREE_ACTOR_MONTHLY_USD',
    userResults: 'FREE_USER_MONTHLY_RESULTS',
    userUsd: 'FREE_USER_MONTHLY_USD',
    dailyShare: 'FREE_DAILY_SHARE',
    poolResults: 'FREE_POOL_MONTHLY_RESULTS',
    newAccountDays: 'FREE_NEW_ACCOUNT_DAYS',
    newAccountShare: 'FREE_NEW_ACCOUNT_SHARE',
    costPerResult: 'FREE_COST_PER_RESULT',
});

/** Limits with defaults filled in and values clamped. */
export function makeConfig(scope, values = {}) {
    const cfg = { ...DEFAULTS, ...values, scope };
    cfg.dailyShare = Math.min(cfg.dailyShare, 1);
    cfg.newAccountShare = Math.min(cfg.newAccountShare, 1);
    cfg.costPerResult = Math.max(cfg.costPerResult, 1e-7);
    return cfg;
}

/** Limits from env vars (see ENV_NAMES), then `overrides`. `null` when FREE_BUDGET is off, 0 or false. */
export function configFromEnv(scope, env = process.env, overrides = {}) {
    if (['off', '0', 'false'].includes(String(env.FREE_BUDGET ?? '').trim().toLowerCase())) return null;
    const values = {};
    for (const [k, name] of Object.entries(ENV_NAMES)) {
        const raw = String(env[name] ?? '').trim();
        const v = raw === '' ? NaN : Number(raw);
        if (Number.isFinite(v) && v >= 0) values[k] = v;
    }
    for (const [k, v] of Object.entries(overrides)) if (v != null) values[k] = Number(v);
    return makeConfig(env.FREE_BUDGET_SCOPE || scope, values);
}

/**
 * true on Apify's free plan, false when paying, null when unknown.
 * APIFY_USER_PRICING_TIER wins when set. APIFY_USER_IS_PAYING is "1" for
 * paying users and set but EMPTY for free ones, so empty means free.
 */
export function isFreePlan(env = process.env) {
    const tier = String(env.APIFY_USER_PRICING_TIER ?? '').trim();
    if (tier) return tier.toUpperCase() === 'FREE';
    if (Object.hasOwn(env, 'APIFY_USER_IS_PAYING')) return String(env.APIFY_USER_IS_PAYING).trim() !== '1';
    return null;
}

const num = (x) => {
    const v = Number(x);
    return Number.isFinite(v) ? v : 0;
};

/** One run booked against an allowance, as stored. */
export function entry({ a = '', d = '', n = 0, usd = 0, h = false } = {}) {
    const e = { a: String(a), d: String(d), n: Math.trunc(num(n)), usd: Math.round(num(usd) * 1e6) / 1e6 };
    if (h === true) e.h = true;
    return e;
}

/** Runs in one month: one user's runs, or all free runs of one scope. */
export class Ledger {
    constructor(runs = {}) {
        this.runs = {};
        for (const [k, v] of Object.entries(runs)) if (v && typeof v === 'object') this.runs[k] = entry(v);
    }

    /** From a stored record: {runs: {...}} or a bare {runId: entry}. */
    static fromRaw(raw) {
        if (!raw || typeof raw !== 'object') return new Ledger();
        const runs = 'runs' in raw ? raw.runs : raw;
        return runs && typeof runs === 'object' ? new Ledger(runs) : new Ledger();
    }

    dropStaleHolds(today) {
        for (const [k, e] of Object.entries(this.runs)) if (e.h && e.d !== today) delete this.runs[k];
    }

    #sum(keep, of) {
        let s = 0;
        for (const e of Object.values(this.runs)) if (keep(e)) s += of(e);
        return s;
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
    return [Number(m[3]), new Date(Date.UTC(Number(m[1]), Number(m[2]), 0)).getUTCDate()];
}

/** What a run may still take. The tightest limit wins; ties keep "month". */
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
    // A hair of slack so 0.05 / 0.0005 is 100, not 99.
    return Object.freeze({ resultsLeft: Math.max(0, Math.floor(best[0] + 1e-6)), usdLeft, bound: best[1] });
}

const dayMs = (iso) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);

/** Share of the per-user limits for an account created on `created`. */
export function accountShare(cfg, created, today) {
    if (!created) return 1;
    const c = Date.parse(created.length === 10 ? `${created}T00:00:00Z` : created);
    const t = dayMs(today);
    if (Number.isNaN(c) || Number.isNaN(t)) return 1;
    const age = (t - dayMs(new Date(c).toISOString())) / 86_400_000;
    return age < cfg.newAccountDays ? cfg.newAccountShare : 1;
}

/** Store keys never hold raw user ids. Same value in the Python package. */
export function userHash(userId, salt = '') {
    return createHash('sha256').update(`bot-guard:${salt}:${userId}`).digest('hex').slice(0, 32);
}

export const utcToday = () => new Date().toISOString().slice(0, 10);

export function nextReset(month) {
    let [y, m] = month.split('-').map(Number);
    if (m >= 12) {
        y += 1;
        m = 1;
    } else m += 1;
    return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-01`;
}
