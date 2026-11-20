// A free tier on your own HTTP API, outside Apify. Each request is a "run":
// free accounts get the usual monthly, daily and pool limits, keyed by your own
// account ids. Needs REDIS_URL. Try:
//   curl -H 'x-account: acct-1' -H 'x-plan: free' 'localhost:3000/search?q=shoes&limit=50'
import express from 'express';
import Redis from 'ioredis';
import { BotGuard, RedisBackend } from 'apify-bot-guard';

const backend = new RedisBackend(new Redis(process.env.REDIS_URL));
const app = express();

/** Stand-in for your data source. */
async function search(q, limit) {
    return Array.from({ length: limit }, (_, i) => ({ q, rank: i + 1 }));
}

app.get('/search', async (req, res) => {
    const decision = await BotGuard.admit('search-api', backend, {
        userId: req.get('x-account'), // your own account id
        free: req.get('x-plan') === 'free', // your own plan check
        runId: req.get('x-request-id'), // optional; a random id otherwise
        log: { warn: console.warn }, // keep info logs quiet per request
    });
    if (!decision.allowed) {
        return res.status(429).json({ error: decision.message, resetsOn: decision.resetsOn });
    }
    const { guard } = decision;
    const wanted = Math.min(Number(req.query.limit) || 10, 100);
    const n = guard ? guard.take(wanted) : wanted;
    const items = await search(String(req.query.q ?? ''), n);
    await guard?.close();
    return res.json({ items, remainingThisPeriod: guard?.remaining ?? null });
});

app.listen(3000, () => console.log('listening on :3000'));
