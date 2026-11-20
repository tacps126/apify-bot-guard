# apify-bot-guard (JavaScript)

Fair free-plan allowances for Apify Actors, with a per-Actor pool against farms of free accounts.
Types included.

```bash
npm install apify-bot-guard
```

```js
import { ApifyKvBackend, BotGuard, pushData } from 'apify-bot-guard';

const guard = await BotGuard.open('my-actor', new ApifyKvBackend({ token: process.env.GUARD_TOKEN }));
await pushData(guard, items);
await guard?.close();
```

Full documentation: https://github.com/tacps126/apify-bot-guard
