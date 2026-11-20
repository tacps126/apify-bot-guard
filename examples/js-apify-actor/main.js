// A JavaScript Actor guarded by apify-bot-guard.
// Run on Apify with GUARD_TOKEN set (see ../README.md). Locally the guard is
// off, because there is no free-plan user to limit.
import { Actor } from 'apify';
import { ApifyKvBackend, BotGuard, pushData } from 'apify-bot-guard';

/** Stand-in for your scraper: 25 results per page. */
async function scrape(page) {
    return Array.from({ length: 25 }, (_, item) => ({ page, item }));
}

await Actor.init();

const backend = new ApifyKvBackend({ token: process.env.GUARD_TOKEN });
const decision = await BotGuard.admit('my-actor', backend);

if (!decision.allowed) {
    // Nothing left this period: succeed with a clear message, never fail.
    await Actor.exit(decision.message ?? 'Free plan limit reached.');
} else {
    const { guard } = decision; // null for paying users: no limit
    try {
        for (let page = 1; page < 100; page++) {
            const pushed = await pushData(guard, await scrape(page));
            if (guard?.remaining === 0 || pushed === 0) break;
        }
    } finally {
        await guard?.close(); // books what was delivered; tidies old records now and then
    }
    await Actor.exit();
}
