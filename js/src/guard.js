// The run-time guard: load the allowance at start, cut deliveries, book at end.
import {
    Ledger, accountShare, allowanceOf, configFromEnv, entry, isFreePlan, nextReset, userHash, utcToday,
} from './core.js';

export class FreeGuard {
    constructor({ cfg, backend, allowance, userKey, poolKey, runId, today, log }) {
        Object.assign(this, { cfg, allowance, delivered: 0, limited: false });
        this.backend = backend;
        this.userKey = userKey;
        this.poolKey = poolKey;
        this.runId = runId;
        this.today = today;
        this.log = log;
        this.booked = false;
    }

    /**
     * Returns null for paying users, runs off the platform, FREE_BUDGET=off,
     * and when the backend cannot be read (a storage problem never blocks a
     * customer).
     *
     * @param {string} scope name the limits are booked under (usually the Actor name)
     * @param {object} backend MemoryBackend | ApifyKvBackend | RedisBackend
     * @param {object} [opts]
     * @param {object} [opts.env] defaults to process.env
     * @param {object} [opts.config] limits; defaults to env vars
     * @param {string} [opts.accountCreated] account creation date if you can read it;
     *   otherwise the day the user was first seen on any Actor sharing this backend stands in
     * @param {string} [opts.trackFrom] first-seen days before this predate your tracking
     *   and do not count as new
     * @param {boolean} [opts.force] apply to paying users too (testing)
     */
    static async open(scope, backend, opts = {}) {
        const env = opts.env ?? process.env;
        const log = opts.log ?? console;
        if (!(opts.force || isFreePlan(env))) return null;
        const cfg = opts.config ?? configFromEnv(scope, env);
        const user = String(env.APIFY_USER_ID ?? '').trim();
        if (!cfg || !user) return null;
        const today = opts.today ?? utcToday();
        const month = today.slice(0, 7);
        const h = userHash(user, opts.salt ?? '');
        const userKey = `user.${month}.${h}`;
        const poolKey = `pool.${month}.${cfg.scope}`;
        const runId = String(env.ACTOR_RUN_ID || env.APIFY_ACTOR_RUN_ID || 'local');
        let u; let p; let seen = null;
        try {
            [u, p] = await Promise.all([backend.getLedger(userKey), backend.getLedger(poolKey)]);
            if (!opts.accountCreated) seen = await backend.firstSeen(h, today);
        } catch (e) {
            log.warn?.(`free allowance unreadable, no monthly limit this run: ${e?.message ?? e}`);
            return null;
        }
        u.dropStaleHolds(today);
        p.dropStaleHolds(today);
        if (seen && opts.trackFrom && seen < opts.trackFrom) seen = null;
        const share = accountShare(cfg, opts.accountCreated ?? seen, today);
        const allowance = allowanceOf(cfg, u, p, today, share);
        const guard = new FreeGuard({ cfg, backend, allowance, userKey, poolKey, runId, today, log });
        log.info?.(`free plan: ${allowance.resultsLeft} results left (bound: ${allowance.bound}, new account: ${share < 1})`);
        if (allowance.resultsLeft > 0) await guard.#save(allowance.resultsLeft, allowance.usdLeft, true);
        return guard;
    }

    /** How many of `wanted` more results may be delivered now; counts them as delivered. */
    take(wanted, costSoFar) {
        const left = Math.max(0, this.allowance.resultsLeft - this.delivered);
        let n = Math.min(wanted, left);
        if (costSoFar != null && costSoFar >= this.allowance.usdLeft) n = 0;
        if (n < wanted) this.limited = true;
        this.delivered += n;
        return n;
    }

    get message() {
        const when = this.allowance.bound === 'day'
            ? 'tomorrow (UTC)'
            : this.allowance.bound === 'pool'
                ? "tomorrow (UTC), when more of this month's free share opens"
                : `on ${nextReset(this.today.slice(0, 7))} (UTC)`;
        return `Free plan limit reached: ${this.delivered} results this run. More are available ${when}, or right away on a paid plan.`;
    }

    /** Book what the run delivered (replaces the hold). Call once, at the end. */
    async close(usd) {
        if (this.booked) return;
        this.booked = true;
        const cost = usd ?? this.delivered * this.cfg.costPerResult;
        try {
            await this.#save(this.delivered, cost, false);
        } catch (e) {
            this.log.warn?.(`free allowance not booked: ${e?.message ?? e}`);
        }
    }

    async #save(n, usd, hold) {
        await this.backend.putEntry(this.userKey, this.runId, entry({ a: this.cfg.scope, d: this.today, n, usd, h: hold }));
        await this.backend.putEntry(this.poolKey, this.runId, entry({ a: '', d: this.today, n, usd: 0, h: hold }));
    }
}

/** Push through the Apify SDK, cut to the allowance. Returns how many were pushed. */
export async function pushData(guard, items) {
    const { Actor } = await import('apify');
    const list = Array.isArray(items) ? items : [items];
    const n = guard ? guard.take(list.length) : list.length;
    if (n) await Actor.pushData(list.slice(0, n));
    if (guard?.limited) await Actor.setStatusMessage(guard.message);
    return n;
}

export { Ledger };
