// The run-time guard: load the allowance at start, cut deliveries, book at end.
import { randomUUID } from 'node:crypto';

import {
    accountShare, allowanceOf, configFromEnv, entry, isFreePlan, nextDay, nextReset, previousMonth, userHash, utcToday,
} from './core.js';

export class BotGuard {
    constructor({ cfg, backend, allowance, userKey, poolKey, runId, today, log }) {
        this.cfg = cfg;
        this.allowance = allowance;
        this.delivered = 0;
        this.limited = false;
        this.userKey = userKey;
        this.poolKey = poolKey;
        this.runId = runId;
        this.today = today;
        this.backend = backend;
        this.log = log;
        this.booked = false;
        this.autoPrune = 0;
    }

    /**
     * open() as one yes/no answer. Keep `decision.guard` (when set): call
     * take() while delivering and close() at the end.
     */
    static async admit(scope, backend, opts = {}) {
        const guard = await BotGuard.open(scope, backend, opts);
        return guard ? guard.decision : { allowed: true, unlimited: true, remaining: null, guard: null };
    }

    get decision() {
        const left = this.remaining;
        return {
            allowed: left > 0,
            unlimited: false,
            remaining: left,
            bound: this.allowance.bound,
            message: left > 0 ? null : this.message,
            resetsOn: this.resetsOn,
            guard: this,
        };
    }

    /** The UTC day more becomes available. */
    get resetsOn() {
        return this.allowance.bound === 'month' ? nextReset(this.today.slice(0, 7)) : nextDay(this.today);
    }

    /**
     * Load this run's allowance. Resolves to null (no limit) for paying users,
     * unknown plans, a missing user id, BOT_GUARD=off, and when the backend
     * cannot be read: a storage problem never blocks a customer.
     *
     * On Apify, userId, free and runId come from the run's env vars. Elsewhere,
     * pass them yourself. See index.d.ts for every option.
     */
    static async open(scope, backend, opts = {}) {
        const env = opts.env ?? process.env;
        const log = opts.log ?? console;
        const free = opts.free ?? isFreePlan(env);
        if (!(opts.force || free)) return null;
        const cfg = opts.config ?? configFromEnv(scope, env);
        const user = String(opts.userId ?? env.APIFY_USER_ID ?? '').trim();
        if (!cfg || !user) return null;
        const today = opts.today ?? utcToday();
        const h = userHash(user, opts.salt ?? '');
        const userKey = `user.${today.slice(0, 7)}.${h}`;
        const poolKey = `pool.${today.slice(0, 7)}.${cfg.scope}`;
        const runId = String(opts.runId || env.ACTOR_RUN_ID || env.APIFY_ACTOR_RUN_ID || `local-${randomUUID().slice(0, 12)}`);
        let u;
        let p;
        let seen;
        try {
            [[u, p], seen] = await Promise.all([
                backend.getLedgers([userKey, poolKey]),
                opts.accountCreated ? null : backend.firstSeen(h, today),
            ]);
        } catch (e) {
            log.warn?.(`bot-guard: allowance unreadable, no monthly limit this run: ${e?.message ?? e}`);
            return null;
        }
        u.dropStaleHolds(today);
        p.dropStaleHolds(today);
        if (seen && opts.trackFrom && seen < opts.trackFrom) seen = null;
        const share = accountShare(cfg, opts.accountCreated ?? seen, today);
        const allowance = allowanceOf(cfg, u, p, today, share);
        const guard = new BotGuard({ cfg, backend, allowance, userKey, poolKey, runId, today, log });
        guard.autoPrune = opts.autoPrune ?? 0.02;
        log.info?.(`bot-guard: ${allowance.resultsLeft} results left (bound: ${allowance.bound}, new account: ${share < 1})`);
        if (allowance.resultsLeft > 0) {
            try {
                await guard.#save(allowance.resultsLeft, allowance.usdLeft, true, { [userKey]: u, [poolKey]: p });
            } catch (e) {
                log.warn?.(`bot-guard: hold not saved: ${e?.message ?? e}`);
            }
        }
        return guard;
    }

    /** How many of `wanted` more results may be delivered now; counts them as delivered. */
    take(wanted, costSoFar) {
        let n = Math.max(0, Math.min(wanted, this.remaining));
        if (costSoFar != null && costSoFar >= this.allowance.usdLeft) n = 0;
        if (n < wanted) this.limited = true;
        this.delivered += n;
        return n;
    }

    get remaining() {
        return Math.max(0, this.allowance.resultsLeft - this.delivered);
    }

    /** A status line for the user when the run was cut or had nothing left. */
    get message() {
        let when = `on ${nextReset(this.today.slice(0, 7))} (UTC)`;
        if (this.allowance.bound === 'day') when = 'tomorrow (UTC)';
        if (this.allowance.bound === 'pool') when = "tomorrow (UTC), when more of this month's free share opens";
        return `Free plan limit reached: ${this.delivered} results this run. More are available ${when}, or right away on a paid plan.`;
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
        if (this.autoPrune > 0 && Math.random() < this.autoPrune) await this.prune();
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
