// Compile-only check that the published types describe real usage (`npm run typecheck`).
import { ApifyKvBackend, type Backend, type Bound, BotGuard, MemoryBackend, RedisBackend, configFromEnv, makeConfig, pushData } from '../src/index.js';

export async function usage(): Promise<number> {
    const backends: Backend[] = [new MemoryBackend(), new ApifyKvBackend({ token: 't' }), new RedisBackend({}, { flavor: 'ioredis' })];
    const guard = await BotGuard.open('my-actor', backends[0], { userId: 'u', free: true, config: makeConfig('x', { dailyShare: 1 }) });
    const n: number = guard ? guard.take(10, 0.01) : 0;
    const bound: Bound | undefined = guard?.allowance.bound;
    void bound;
    const d = await BotGuard.admit('my-actor', backends[0], {
        userId: 'u', plan: 'trial', plans: ['trial'], actorWeekResults: 50, runUsd: null, exemptUsers: 'me', mode: 'observe', autoPrune: 0,
    });
    const cfg = configFromEnv('my-actor', {}, { blockedUsers: new Set(['x']), message: 'Used {delivered}' });
    const problems: string[] = cfg ? cfg.problems : [];
    void problems;
    const enforced: boolean = d.enforced;
    void enforced;
    const ok: boolean = d.allowed && (d.remaining ?? Infinity) > 0;
    void ok;
    await guard?.close();
    return n + (await pushData(guard, [{ a: 1 }], { pushData: async () => undefined, setStatusMessage: async () => undefined }));
}
