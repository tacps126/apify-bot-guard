/** A limit: a number (0 allowed) or null for no limit. */
export type Limit = number | null;

/** Every control the guard has. See docs/CONFIGURATION.md. */
export interface Settings {
    actorMonthResults: Limit;
    actorMonthUsd: Limit;
    userMonthResults: Limit;
    userMonthUsd: Limit;
    actorWeekResults: Limit;
    actorWeekUsd: Limit;
    userWeekResults: Limit;
    userWeekUsd: Limit;
    actorDayResults: Limit;
    actorDayUsd: Limit;
    userDayResults: Limit;
    userDayUsd: Limit;
    runResults: Limit;
    runUsd: Limit;
    poolMonthResults: Limit;
    /** Per-Actor daily cap as a share of actorMonthResults (0 to 1); null = off. */
    dailyShare: Limit;
    newAccountDays: number;
    newAccountShare: number;
    costPerResult: number;
    /** Chance (0 to 1) that close() also deletes ledgers older than last month; 0 = off. */
    autoPrune: number;
    /** Plans that are limited, upper case. Default ['FREE']. */
    plans: string[];
    exemptUsers: Set<string>;
    blockedUsers: Set<string>;
    mode: 'enforce' | 'observe';
    /** Template with {delivered}, {when}, {resets_on}, {bound}; null = built-in text. */
    message: string | null;
    salt: string;
    /** YYYY-MM-DD: first-seen days before it predate your tracking and do not count as new. */
    trackFrom: string | null;
}

/** What you may pass: any setting, lists as arrays, Sets or comma-separated text. */
export type SettingsInput = Partial<Omit<Settings, 'plans' | 'exemptUsers' | 'blockedUsers' | 'mode'>> & {
    plans?: Iterable<string> | string;
    exemptUsers?: Iterable<string> | string;
    blockedUsers?: Iterable<string> | string;
    mode?: string;
};

export interface Config extends Settings {
    scope: string;
    /** Values that were ignored, with the reason; the guard logs them. */
    problems: string[];
}

export type Env = Record<string, string | undefined>;

export interface Entry {
    a: string;
    d: string;
    n: number;
    usd: number;
    h?: true;
}

export interface Allowance {
    readonly resultsLeft: number;
    /** null when no cost limit applies. */
    readonly usdLeft: number | null;
    readonly bound: Bound;
    readonly weekStart: string;
}

export type Bound = 'month' | 'week' | 'day' | 'run' | 'pool' | 'blocked';

export declare const DEFAULTS: Readonly<Partial<Settings>>;
export declare const LIMIT_FIELDS: readonly string[];
export declare const SETTINGS: readonly (keyof Settings)[];
export declare const WINDOWS: readonly ['month', 'week', 'day'];
/** Env var for each setting, e.g. actorWeekResults: BOT_GUARD_ACTOR_WEEK_RESULTS. */
export declare const ENV_NAMES: Readonly<Record<keyof Settings, string>>;
export declare const DEFAULT_MESSAGE: string;
export declare const BLOCKED_MESSAGE: string;

export declare function envName(setting: keyof Settings): string;
export declare function makeConfig(scope: string, values?: SettingsInput): Config;
/** Settings from BOT_GUARD_* env vars, then `overrides`. null when BOT_GUARD is off. */
export declare function configFromEnv(scope: string, env?: Env, overrides?: SettingsInput): Config | null;
export declare function hasLimits(cfg: Config): boolean;
/** The Apify plan, upper case (FREE, STARTER, ..., or PAID); null when unknown. */
export declare function planOf(env?: Env): string | null;
export declare function isFreePlan(env?: Env): boolean | null;
export declare function entry(e?: Partial<Entry> & { h?: boolean }): Entry;
export declare function allowanceOf(cfg: Config, user: Ledger, pool: Ledger, today: string, share?: number, opts?: { lastMonth?: Ledger | null }): Allowance;
export declare function accountShare(cfg: Config, created: string | null | undefined, today: string): number;
export declare function userHash(userId: string, salt?: string): string;
export declare function recordKey(key: string): string;

export declare class Ledger {
    constructor(runs?: Record<string, Partial<Entry>>);
    runs: Record<string, Entry>;
    static fromRaw(raw: unknown): Ledger;
    static merged(ledgers: Ledger[]): Ledger;
    dropStaleHolds(today: string): void;
    /** [results, usd] of runs on or after `since`, for one scope or (null) all. */
    used(scope?: string | null, since?: string): [number, number];
    results(): number;
}

/** (key, run id, entry) */
export type Put = [string, string, Entry];

/** Implement this to keep ledgers anywhere else (SQL, DynamoDB, ...). */
export interface Backend {
    getLedgers(keys: string[]): Promise<Ledger[]>;
    /** `known`: ledgers just read, for backends that must merge instead of setting one field. */
    putEntries(puts: Put[], known?: Record<string, Ledger>): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
    /** Delete ledgers of months before `beforeMonth` (YYYY-MM); resolve to how many. */
    prune(beforeMonth: string): Promise<number>;
}

export declare class MemoryBackend implements Backend {
    data: Map<string, unknown>;
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[]): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
    prune(beforeMonth: string): Promise<number>;
}

export declare class ApifyKvBackend implements Backend {
    constructor(opts?: { token?: string; store?: string; baseUrl?: string; client?: unknown });
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[], known?: Record<string, Ledger>): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
    prune(beforeMonth: string): Promise<number>;
}

export declare class RedisBackend implements Backend {
    constructor(client: unknown, opts?: { prefix?: string; flavor?: 'ioredis' | 'node-redis' | 'upstash' });
    readonly flavor: 'ioredis' | 'node-redis' | 'upstash';
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[]): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
    prune(beforeMonth: string): Promise<number>;
}

/** Options for open() and admit(). Any setting can be given here too, e.g. { actorWeekResults: 50 }. */
export interface OpenOptions extends SettingsInput {
    /** Defaults to process.env. */
    env?: Env;
    /** Off Apify: the user's id. Defaults to APIFY_USER_ID. */
    userId?: string;
    /** Off Apify: your plan name for this user, matched against `plans`. Defaults to the Apify env vars. */
    plan?: string;
    /** Shorthand for plan 'FREE' (true) or 'PAID' (false). */
    free?: boolean;
    /** Defaults to ACTOR_RUN_ID, else a random local id. */
    runId?: string;
    /** A ready config; otherwise BOT_GUARD_* env vars. Settings in these options go on top. */
    config?: Config;
    /** Settings on top of the config or env vars (same as passing them directly). */
    settings?: SettingsInput;
    /** Account creation date if you can read it; otherwise first-seen stands in. */
    accountCreated?: string;
    /** YYYY-MM-DD; defaults to today in UTC. */
    today?: string;
    /** Limit this user whatever the plan (testing). */
    force?: boolean;
    log?: { info?(m: string): void; warn?(m: string): void };
}

export interface Decision {
    /** Deliver results. false: nothing left; end the run with `message`. */
    allowed: boolean;
    /** No limit applies (plan not limited, exempt user, guard off, no limits, storage unreadable). */
    unlimited: boolean;
    /** Results this run may deliver; null when unlimited or not enforced. */
    remaining: number | null;
    /** false in observe mode: limits are measured and logged, never applied. */
    enforced: boolean;
    bound?: Bound;
    message?: string | null;
    /** UTC day (YYYY-MM-DD) more becomes available; null when blocked. */
    resetsOn?: string | null;
    /** Keep it: take() while delivering, close() at the end. */
    guard: BotGuard | null;
}

export declare class BotGuard {
    static open(scope: string, backend: Backend, opts?: OpenOptions): Promise<BotGuard | null>;
    /** open() as one yes/no answer. */
    static admit(scope: string, backend: Backend, opts?: OpenOptions): Promise<Decision>;
    readonly decision: Decision;
    readonly resetsOn: string | null;
    readonly enforced: boolean;
    /** The run went past its allowance (in either mode). */
    readonly over: boolean;
    /** Delete ledgers older than last month. Never throws. */
    prune(): Promise<number>;
    readonly cfg: Config;
    readonly allowance: Allowance;
    readonly delivered: number;
    readonly limited: boolean;
    readonly remaining: number;
    readonly message: string;
    readonly userKey: string;
    readonly poolKey: string;
    readonly runId: string;
    /** How many of `wanted` more results may be delivered now; counts them as delivered. */
    take(wanted: number, costSoFar?: number): number;
    /** Book what the run delivered, replacing the hold. Safe to call twice. */
    close(usd?: number): Promise<void>;
}

export declare function pushData(
    guard: BotGuard | null,
    items: unknown[] | Record<string, unknown>,
    actor?: { pushData(items: unknown[]): Promise<unknown>; setStatusMessage(m: string): Promise<unknown> },
): Promise<number>;
