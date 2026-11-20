// The run-time guard: load the allowance at start, cut deliveries, book at end.
import { randomUUID } from 'node:crypto';

import {
    accountShare, allowanceOf, configFromEnv, entry, hasLimits, makeConfig, needsLastMonth, nextDay, nextReset, planOf,
    previousMonth, SETTINGS, userHash, utcToday, weekReset,
} from './core.js';

const WHEN = {
    day: 'tomorrow (UTC)',
    pool: "tomorrow (UTC), when more of this month's free share opens",
    run: 'in a new run',
};
export const DEFAULT_MESSAGE = 'Free plan limit reached: {delivered} results this run. More are available {when}, or right away on a paid plan.';
export const BLOCKED_MESSAGE = 'Free use of this Actor is not available for this account. It works on a paid plan.';
const UNLIMITED = Object.freeze({ allowed: true, unlimited: true, remaining: null, enforced: true, guard: null });

/** The user id when this run is limited; null when it is not (unknown, exempt, plan not limited). */
function limitedUser(cfg, env, opts) {
    const user = String(opts.userId ?? env.APIFY_USER_ID ?? '').trim();
    if (!user || cfg.exemptUsers.has(user)) return null;
    let plan = opts.plan;
    if (plan == null && opts.free != null) plan = opts.free ? 'FREE' : 'PAID';
    plan = String(plan ?? planOf(env) ?? '').trim().toUpperCase();
    return opts.force || cfg.plans.includes(plan) ? user : null;
}

export class BotGuard {
    constructor({ cfg, backend, allowance, userKey, poolKey, runId, today, resetsOn = null, log }) {
        this.cfg = cfg;
        this.allowance = allowance;
        this.delivered = 0;
        this.limited = false; // a take() was cut (enforce mode)
        this.over = false; // the run went past its allowance (either mode)
        this.userKey = userKey;
        this.poolKey = poolKey;
        this.runId = runId;
        this.today = today;
        this.enforced = cfg.mode === 'enforce';
        this.resetsOnDay = resetsOn;
        this.backend = backend;
        this.log = log;
        this.booked = allowance.bound === 'blocked';
    }

    /** open() as one answer. Keep `decision.guard` (when set): take() while delivering, close() at the end. */
    static async admit(scope, backend, opts = {}) {
        const guard = await BotGuard.open(scope, backend, opts);
        return guard ? guard.decision : { ...UNLIMITED };
    }

    get decision() {
        const left = this.remaining;
        return {
            allowed: left > 0 || !this.enforced,
            unlimited: false,
            remaining: this.enforced ? left : null,
            bound: this.allowance.bound,
            message: left > 0 ? null : this.message,
            resetsOn: this.resetsOn,
            enforced: this.enforced,
            guard: this,
        };
    }

    /** The UTC day more becomes available (null when blocked). */
    get resetsOn() {
        const b = this.allowance.bound;
        if (this.resetsOnDay != null || b === 'blocked') return this.resetsOnDay;
        if (b === 'month') return nextReset(this.today.slice(0, 7));
        if (b === 'run') return this.today;
        return nextDay(this.today);
    }

    /**
     * Load this run's allowance. Resolves to null (no limit) when the user's plan is not in
     * `plans`, the plan or user id is unknown, the user is exempt, BOT_GUARD=off, no limit is
     * set, or the backend cannot be read: a storage problem never blocks a customer.
     *
     * Settings: `opts.config`, else BOT_GUARD_* env vars; any setting given in `opts` (or
     * `opts.settings`) goes on top, e.g. `{ actorWeekResults: 50 }`.
     * See docs/CONFIGURATION.md and index.d.ts for every option.
     */
    static async open(scope, backend, opts = {}) {
        const env = opts.env ?? process.env;
        const log = opts.log ?? console;
        const settings = { ...Object.fromEntries(Object.entries(opts).filter(([k]) => SETTINGS.includes(k))), ...opts.settings };
        let cfg;
        if (opts.config) {
            const { scope: s, problems, ...base } = opts.config;
            cfg = Object.keys(settings).length ? makeConfig(s, { ...base, ...settings }) : opts.config;
        } else cfg = configFromEnv(scope, env, settings);
        if (!cfg) return null;
        for (const p of cfg.problems ?? []) log.warn?.(`bot-guard: ${p}`);
        const user = limitedUser(cfg, env, opts);
        if (user === null) return null;
        const today = opts.today ?? utcToday();
        const month = today.slice(0, 7);
        const h = userHash(user, cfg.salt);
        const common = {
            cfg,
            backend,
            log,
            today,
            userKey: `user.${month}.${h}`,
            poolKey: `pool.${month}.${cfg.scope}`,
            runId: String(opts.runId || env.ACTOR_RUN_ID || env.APIFY_ACTOR_RUN_ID || `local-${randomUUID().slice(0, 12)}`),
        };
        if (cfg.blockedUsers.has(user)) {
            log.info?.('bot-guard: user is blocked');
            return new BotGuard({ ...common, allowance: Object.freeze({ resultsLeft: 0, usdLeft: 0, bound: 'blocked', weekStart: '' }) });
        }
        if (!hasLimits(cfg)) return null;
        const keys = [common.userKey, common.poolKey];
        if (needsLastMonth(cfg, today)) keys.push(`user.${previousMonth(month)}.${h}`);
        let ledgers;
        let seen;
        try {
            [ledgers, seen] = await Promise.all([
                backend.getLedgers(keys),
                opts.accountCreated ? null : backend.firstSeen(h, today),
            ]);
        } catch (e) {
            log.warn?.(`bot-guard: allowance unreadable, no limit this run: ${e?.message ?? e}`);
            return null;
        }
        const [u, p, last = null] = ledgers;
        for (const l of [u, p, last]) l?.dropStaleHolds(today);
        if (seen && cfg.trackFrom && seen < cfg.trackFrom) seen = null;
        const share = accountShare(cfg, opts.accountCreated ?? seen, today);
        const allowance = allowanceOf(cfg, u, p, today, share, { lastMonth: last });
        const resetsOn = allowance.bound === 'week' ? weekReset(u, last, allowance.weekStart) : null;
        const guard = new BotGuard({ ...common, allowance, resetsOn });
        log.info?.(`bot-guard: ${allowance.resultsLeft} results left (bound: ${allowance.bound}, new account: ${share < 1}, mode: ${cfg.mode})`);
        if (allowance.resultsLeft > 0) {
            const usd = allowance.usdLeft ?? allowance.resultsLeft * cfg.costPerResult;
            try {
                await guard.#save(allowance.resultsLeft, usd, true, { [common.userKey]: u, [common.poolKey]: p });
            } catch (e) {
                log.warn?.(`bot-guard: hold not saved: ${e?.message ?? e}`);
            }
        }
        return guard;
    }

    /**
     * How many of `wanted` more results may be delivered now; counts them as delivered.
     * In observe mode returns `wanted` and only logs when the run goes past its allowance.
     */
    take(wanted, costSoFar) {
        let n = Math.max(0, Math.min(wanted, this.remaining));
        const usdLeft = this.allowance.usdLeft;
        if (costSoFar != null && usdLeft != null && costSoFar >= usdLeft) n = 0;
        if (n < wanted) {
            if (!this.over) this.log.info?.(`bot-guard: allowance used up (bound: ${this.allowance.bound}, enforced: ${this.enforced})`);
            this.over = true;
            if (this.enforced) this.limited = true;
            else n = wanted;
        }
        this.delivered += n;
        return n;
    }

    get remaining() {
        return Math.max(0, this.allowance.resultsLeft - this.delivered);
    }

    /** A status line for the user when the run was cut or had nothing left. */
    get message() {
        const b = this.allowance.bound;
        const when = WHEN[b] ?? `on ${this.resetsOn} (UTC)`;
        const values = { delivered: String(this.delivered), when, resets_on: this.resetsOn ?? '', bound: b };
        let text = this.cfg.message || (b === 'blocked' ? BLOCKED_MESSAGE : DEFAULT_MESSAGE);
        for (const [k, v] of Object.entries(values)) text = text.replaceAll(`{${k}}`, v);
        return text;
    }

    /** Book what the run delivered, replacing the hold. Safe to call twice. */
    async close(usd) {
        if (this.booked) return;
        this.booked = true;
        try {
            await this.#save(this.delivered, usd ?? this.delivered * this.cfg.costPerResult, false);
        } catch (e) {
            this.log.warn?.(`bot-guard: run not booked: ${e?.message ?? e}`);
        }
        if (this.cfg.autoPrune > 0 && Math.random() < this.cfg.autoPrune) await this.prune();
    }

    /** Delete ledgers older than last month. Never throws. */
    async prune() {
        try {
            const n = await this.backend.prune(previousMonth(this.today.slice(0, 7)));
            if (n) this.log.info?.(`bot-guard: pruned ${n} old ledgers`);
            return n;
        } catch (e) {
            this.log.warn?.(`bot-guard: prune failed: ${e?.message ?? e}`);
            return 0;
        }
    }

    async #save(n, usd, hold, known) {
        await this.backend.putEntries([
            [this.userKey, this.runId, entry({ a: this.cfg.scope, d: this.today, n, usd, h: hold })],
            [this.poolKey, this.runId, entry({ a: '', d: this.today, n, usd: 0, h: hold })],
        ], known);
    }
}

/**
 * Push to the run's default dataset through the Apify SDK, cut to the
 * allowance; sets the status message when it cuts. Resolves to how many were pushed.
 * `actor`: the SDK's Actor (imported from 'apify' when omitted).
 */
export async function pushData(guard, items, actor) {
    const Actor = actor ?? (await import('apify')).Actor;
    const list = Array.isArray(items) ? items : [items];
    const n = guard ? guard.take(list.length) : list.length;
    if (n) await Actor.pushData(list.slice(0, n));
    if (guard?.limited) await Actor.setStatusMessage(guard.message);
    return n;
}
