# apify-bot-guard (Python)

Fair free-plan allowances for Apify Actors, with a per-Actor pool against farms of free accounts.

```bash
pip install "apify-bot-guard[apify]"
```

```python
from apify_bot_guard import ApifyKvBackend, BotGuard, push_data

guard = await BotGuard.open("my-actor", ApifyKvBackend(token=os.environ["GUARD_TOKEN"]))
await push_data(guard, items)
if guard:
    await guard.close()
```

Full documentation: https://github.com/tacps126/apify-bot-guard
