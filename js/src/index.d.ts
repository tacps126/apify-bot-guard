export interface Config {
    scope: string;
    actorResults: number;
    actorUsd: number;
    userResults: number;
    userUsd: number;
    dailyShare: number;
    poolResults: number;
    newAccountDays: number;
    newAccountShare: number;
    costPerResult: number;
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
    readonly usdLeft: number;
    readonly bound: 'month' | 'day' | 'pool';
}

export declare const DEFAULTS: Readonly<Omit<Config, 'scope'>>;
export declare const ENV_NAMES: Readonly<Record<keyof Omit<Config, 'scope'>, string>>;

export declare function makeConfig(scope: string, values?: Partial<Omit<Config, 'scope'>>): Config;
export declare function configFromEnv(scope: string, env?: Env, overrides?: Partial<Omit<Config, 'scope'>>): Config | null;
export declare function isFreePlan(env?: Env): boolean | null;
export declare function entry(e?: Partial<Entry> & { h?: boolean }): Entry;
export declare function allowanceOf(cfg: Config, user: Ledger, pool: Ledger, today: string, share?: number): Allowance;
export declare function accountShare(cfg: Config, created: string | null | undefined, today: string): number;
export declare function userHash(userId: string, salt?: string): string;
export declare function recordKey(key: string): string;

export declare class Ledger {
    constructor(runs?: Record<string, Partial<Entry>>);
    runs: Record<string, Entry>;
    static fromRaw(raw: unknown): Ledger;
    dropStaleHolds(today: string): void;
    resultsOn(scope: string): number;
    resultsOnDay(scope: string, day: string): number;
    results(): number;
    spentOn(scope: string): number;
    spent(): number;
}

/** (key, run id, entry) */
export type Put = [string, string, Entry];

/** Implement this to keep ledgers anywhere else (SQL, DynamoDB, ...). */
export interface Backend {
    getLedgers(keys: string[]): Promise<Ledger[]>;
    /** `known`: ledgers just read, for backends that must merge instead of setting one field. */
    putEntries(puts: Put[], known?: Record<string, Ledger>): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
}

export declare class MemoryBackend implements Backend {
    data: Map<string, unknown>;
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[]): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
}

export declare class ApifyKvBackend implements Backend {
    constructor(opts?: { token?: string; store?: string; baseUrl?: string; client?: unknown });
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[], known?: Record<string, Ledger>): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
}

export declare class RedisBackend implements Backend {
    constructor(client: unknown, opts?: { prefix?: string; flavor?: 'ioredis' | 'node-redis' | 'upstash' });
    readonly flavor: 'ioredis' | 'node-redis' | 'upstash';
    getLedgers(keys: string[]): Promise<Ledger[]>;
    putEntries(puts: Put[]): Promise<void>;
    firstSeen(userKey: string, today: string): Promise<string | null>;
}

export interface OpenOptions {
    /** Defaults to process.env. */
    env?: Env;
    /** Off Apify: the user's id. Defaults to APIFY_USER_ID. */
    userId?: string;
    /** Off Apify: whether the user is on a free plan. Defaults to the Apify env vars. */
    free?: boolean;
    /** Defaults to ACTOR_RUN_ID, else a random local id. */
    runId?: string;
    /** Limits; defaults to env vars. */
    config?: Config;
    /** Account creation date if you can read it; otherwise first-seen stands in. */
    accountCreated?: string;
    /** First-seen days before this predate your tracking and do not count as new. */
    trackFrom?: string;
    /** YYYY-MM-DD; defaults to today in UTC. */
    today?: string;
    /** Apply to paying users too (testing). */
    force?: boolean;
    /** Mixed into the user hash. */
    salt?: string;
    log?: { info?(m: string): void; warn?(m: string): void };
}

export declare class BotGuard {
    static open(scope: string, backend: Backend, opts?: OpenOptions): Promise<BotGuard | null>;
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
