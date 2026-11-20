// Pure allowance logic. No I/O; kept identical to the Python package (docs/DESIGN.md).
import { createHash } from 'node:crypto';

const snake = (name) => name.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

// Limits, in the order ties are broken. Each is a number (0 allowed) or null (no limit).
// "actor*" count one user's runs of this scope; "user*" count one user's runs of every scope
// sharing the backend; "run*" cap a single run; "pool*" count every limited user together.
export const WINDOWS = Object.freeze(['month', 'week', 'day']);
const GRID = WINDOWS.flatMap((w) => ['actor', 'user'].flatMap((who) => ['Results', 'Usd'].map((u) => `${who}${w[0].toUpperCase()}${w.slice(1)}${u}`)));
export const LIMIT_FIELDS = Object.freeze([...GRID, 'runResults', 'runUsd', 'poolMonthResults']);

export const DEFAULTS = Object.freeze({
    actorMonthResults: 100,
    actorMonthUsd: 0.05,
    userMonthResults: 300,
    userMonthUsd: 0.25,
    poolMonthResults: 3000,
    dailyShare: 0.34,
    newAccountDays: 7,
    newAccountShare: 0.25,
    costPerResult: 0.0005,
    autoPrune: 0.02,
});

const NUMBER_FIELDS = [...LIMIT_FIELDS, 'dailyShare', 'newAccountDays', 'newAccountShare', 'costPerResult', 'autoPrune'];
const LIST_FIELDS = ['plans', 'exemptUsers', 'blockedUsers'];
const TEXT_FIELDS = ['mode', 'message', 'salt', 'trackFrom'];
const NO_LIMIT = new Set(['none', 'off', 'unlimited', 'no', '-']);
const MODES = ['enforce', 'observe'];
export const SETTINGS = Object.freeze([...NUMBER_FIELDS, ...LIST_FIELDS, ...TEXT_FIELDS]);

/** `actorWeekResults` -> `BOT_GUARD_ACTOR_WEEK_RESULTS`. */
export const envName = (name) => `BOT_GUARD_${snake(name).toUpperCase()}`;

/** Env var names for every setting, keyed by setting. */
export const ENV_NAMES = Object.freeze(Object.fromEntries(SETTINGS.map((k) => [k, envName(k)])));

function asList(name, v) {
    if (v == null || !LIST_FIELDS.includes(name)) return v;
    const items = (typeof v === 'string' ? v.split(',') : [...v]).map((x) => String(x).trim()).filter(Boolean);
    return name === 'plans' ? items.map((p) => p.toUpperCase()) : new Set(items);
}

/** Every control the guard has, defaults filled in and values checked. Unset limits (null) do not apply. */
export function makeConfig(scope, values = {}) {
    for (const k of Object.keys(values)) {
        if (!SETTINGS.includes(k)) throw new TypeError(`unknown bot-guard setting: ${k}`);
    }
    const cfg = {
        scope,
        ...Object.fromEntries(LIMIT_FIELDS.map((k) => [k, null])),
        ...DEFAULTS,
        plans: ['FREE'],
        exemptUsers: new Set(),
        blockedUsers: new Set(),
        mode: 'enforce',
        message: null,
        salt: '',
        trackFrom: null,
        problems: [],
    };
    for (const [k, v] of Object.entries(values)) cfg[k] = asList(k, v);
    for (const k of NUMBER_FIELDS) {
        const v = cfg[k];
        if (v != null && !(typeof v === 'number' && Number.isFinite(v) && v >= 0)) {
            cfg.problems.push(`${snake(k)}: ${JSON.stringify(v)} is not a number >= 0; using no limit`);
            cfg[k] = null;
        }
    }
    if (cfg.dailyShare != null) cfg.dailyShare = Math.min(cfg.dailyShare, 1);
    cfg.newAccountShare = cfg.newAccountShare == null ? 1 : Math.min(cfg.newAccountShare, 1);
    cfg.newAccountDays = cfg.newAccountDays || 0;
    cfg.costPerResult = Math.max(cfg.costPerResult || 0, 1e-7);
    cfg.autoPrune = Math.min(cfg.autoPrune || 0, 1);
    cfg.mode = String(cfg.mode || 'enforce').trim().toLowerCase();
    if (!MODES.includes(cfg.mode)) {
        cfg.problems.push(`mode: '${cfg.mode}' is not enforce or observe; using enforce`);
        cfg.mode = 'enforce';
    }
    return cfg;
}

/** True when at least one limit is set. */
export const hasLimits = (cfg) => LIMIT_FIELDS.some((k) => cfg[k] != null);

/**
 * Controls from `BOT_GUARD_*` env vars, then `overrides` (which win). `null` when BOT_GUARD is
 * off, 0 or false. Unreadable values are skipped and listed in `problems` (the guard logs them).
 */
export function configFromEnv(scope, env = process.env, overrides = {}) {
    if (['off', '0', 'false'].includes(String(env.BOT_GUARD ?? '').trim().toLowerCase())) return null;
    const values = {};
    const problems = [];
    for (const k of NUMBER_FIELDS) {
        const raw = String(env[envName(k)] ?? '').trim();
        if (!raw) continue;
        if (NO_LIMIT.has(raw.toLowerCase())) {
            values[k] = null;
            continue;
        }
        const v = Number(raw);
        if (Number.isFinite(v) && v >= 0) values[k] = v;
        else problems.push(`${envName(k)}='${raw}' is not a number >= 0; ignored`);
    }
    for (const k of LIST_FIELDS) {
        const raw = String(env[envName(k)] ?? '').trim();
        if (raw) values[k] = raw;
    }
    for (const k of TEXT_FIELDS) {
        const raw = String(env[envName(k)] ?? '');
        if (raw.trim()) values[k] = k === 'message' ? raw : raw.trim();
    }
    Object.assign(values, overrides);
    const cfg = makeConfig(env.BOT_GUARD_SCOPE || scope, values);
    cfg.problems.unshift(...problems);
    return cfg;
}

/**
 * The user's plan on Apify, upper case: the pricing tier (FREE, STARTER, ...), else FREE or
 * PAID from APIFY_USER_IS_PAYING ("1" for paying users, set but EMPTY for free ones). null when unknown.
 */
export function planOf(env = process.env) {
    const tier = String(env.APIFY_USER_PRICING_TIER ?? '').trim();
    if (tier) return tier.toUpperCase();
    if (Object.hasOwn(env, 'APIFY_USER_IS_PAYING')) return String(env.APIFY_USER_IS_PAYING).trim() === '1' ? 'PAID' : 'FREE';
    return null;
}

/** true on Apify's free plan, false on any other plan, null when unknown. */
export function isFreePlan(env = process.env) {
    const plan = planOf(env);
    return plan === null ? null : plan === 'FREE';
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

/** Runs in one month: one user's runs, or all limited runs of one scope. */
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

    static merged(ledgers) {
        const out = new Ledger();
        for (const l of ledgers) Object.assign(out.runs, l.runs);
        return out;
    }

    dropStaleHolds(today) {
        for (const [k, e] of Object.entries(this.runs)) if (e.h && e.d !== today) delete this.runs[k];
    }

    /** [results, usd] of runs on or after day `since`, for one scope or (null) all. */
    used(scope = null, since = '') {
        let n = 0;
        let usd = 0;
        for (const e of Object.values(this.runs)) {
            if ((scope === null || e.a === scope) && e.d >= since) {
                n += e.n;
                usd += e.usd;
            }
        }
        return [n, usd];
    }

    results() { return this.used()[0]; }
}

export function dayOfMonth(today) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(today);
    if (!m) return [30, 30];
    return [Number(m[3]), new Date(Date.UTC(Number(m[1]), Number(m[2]), 0)).getUTCDate()];
}

const dayMs = (iso) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
export const shiftDay = (day, days) => new Date(dayMs(day) + days * 86_400_000).toISOString().slice(0, 10);

/** A rolling week that starts last month needs last month's user ledger too. */
export function needsLastMonth(cfg, today) {
    const weekly = ['actorWeekResults', 'actorWeekUsd', 'userWeekResults', 'userWeekUsd'].some((k) => cfg[k] != null);
    return weekly && dayOfMonth(today)[0] < 7;
}

/**
 * What a run may still take. The tightest limit wins; ties go to the earlier of month, week,
 * day, run, pool. `share` scales the actor and user limits (new accounts). `lastMonth`: last
 * month's user ledger, for a rolling week that starts there.
 */
export function allowanceOf(cfg, user, pool, today, share = 1, { lastMonth = null } = {}) {
    const s = cfg.scope;
    const cost = cfg.costPerResult || 1e-7;
    const weekStart = shiftDay(today, -6);
    const starts = { month: '', week: weekStart, day: today };
    const ledgers = { month: user, week: Ledger.merged([lastMonth ?? new Ledger(), user]), day: user };
    const usdLefts = [];
    const candidates = [];
    for (const w of WINDOWS) {
        let best = Infinity;
        const W = w[0].toUpperCase() + w.slice(1);
        for (const [who, scope] of [['actor', s], ['user', null]]) {
            const [usedN, usedUsd] = ledgers[w].used(scope, starts[w]);
            const limN = cfg[`${who}${W}Results`];
            const limUsd = cfg[`${who}${W}Usd`];
            if (limN != null) best = Math.min(best, limN * share - usedN);
            if (limUsd != null) {
                const left = Math.max(0, limUsd * share - usedUsd);
                usdLefts.push(left);
                best = Math.min(best, left / cost);
            }
        }
        if (w === 'day' && cfg.dailyShare != null && cfg.actorMonthResults != null) {
            best = Math.min(best, Math.ceil(cfg.actorMonthResults * share * cfg.dailyShare) - user.used(s, today)[0]);
        }
        candidates.push([best, w]);
    }
    let run = Infinity;
    if (cfg.runResults != null) run = cfg.runResults;
    if (cfg.runUsd != null) {
        usdLefts.push(cfg.runUsd);
        run = Math.min(run, cfg.runUsd / cost);
    }
    candidates.push([run, 'run']);
    let poolLeft = Infinity;
    if (cfg.poolMonthResults != null) {
        const [d, days] = dayOfMonth(today);
        poolLeft = Math.ceil((cfg.poolMonthResults * d) / days) - pool.results();
    }
    candidates.push([poolLeft, 'pool']);
    let [bestN, bound] = candidates[0];
    for (const c of candidates.slice(1)) if (c[0] < bestN) [bestN, bound] = c;
    const usdLeft = usdLefts.length ? Math.max(0, Math.min(...usdLefts)) : null;
    // A hair of slack so 0.05 / 0.0005 is 100, not 99.
    return Object.freeze({ resultsLeft: Math.max(0, Math.floor(bestN + 1e-6)), usdLeft, bound, weekStart });
}

/** The day the oldest run in the rolling week leaves it. */
export function weekReset(user, lastMonth, weekStart) {
    const days = Object.values(Ledger.merged([lastMonth ?? new Ledger(), user]).runs)
        .filter((e) => e.d >= weekStart && e.n > 0)
        .map((e) => e.d)
        .sort();
    return shiftDay(days[0] ?? weekStart, 7);
}

/** Share of the actor and user limits for an account created on `created`. */
export function accountShare(cfg, created, today) {
    if (!created) return 1;
    const c = Date.parse(created.length === 10 ? `${created}T00:00:00Z` : created);
    const t = dayMs(today);
    if (Number.isNaN(c) || Number.isNaN(t)) return 1;
    const age = (t - dayMs(new Date(c).toISOString())) / 86_400_000;
    return age < (cfg.newAccountDays || 0) ? (cfg.newAccountShare ?? 1) : 1;
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

export const nextDay = (today) => shiftDay(today, 1);

export function previousMonth(month) {
    let [y, m] = month.split('-').map(Number);
    if (m <= 1) {
        y -= 1;
        m = 12;
    } else m -= 1;
    return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}`;
}

/** `2026-11` for a ledger key like `user.2026-11.<hash>`; null for other keys. */
export function ledgerMonth(key) {
    return /^(?:user|pool)\.(\d{4}-\d{2})\./.exec(key)?.[1] ?? null;
}
