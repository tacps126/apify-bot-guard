# Examples

| Folder | What it shows |
|---|---|
| [python-apify-actor](python-apify-actor) | A Python Actor: one yes/no check at start, cut deliveries, clean exit; `.actor/actor.json` sets limits as env vars |
| [js-apify-actor](js-apify-actor) | The same Actor in JavaScript |
| [js-express-api](js-express-api) | Outside Apify: a free tier on your own HTTP API, with Redis |
| [python-generic](python-generic) | Outside Apify in Python: any job runner, with Redis or Upstash |
| [cleanup](cleanup) | A scheduled cleanup run (only needed if you turn auto-prune off) |

## Setup on Apify (both languages)

1. Create an API token in Apify Console (Settings, API and Integrations). A token scoped to one
   key-value store is enough; the guard creates the store `bot-guard` on first use.
2. Store it as a secret on your Actor, so users never see it:

   ```bash
   apify secrets add guardToken <your-token>
   ```

3. Reference it in `.actor/actor.json`:

   ```json
   { "environmentVariables": { "GUARD_TOKEN": "@guardToken" } }
   ```

4. Optional: set limits and other settings as env vars in the same block, for example
   `"BOT_GUARD_ACTOR_MONTH_RESULTS": "200"` or `"BOT_GUARD_MODE": "observe"`. Every setting is listed in
   [docs/CONFIGURATION.md](../docs/CONFIGURATION.md).
5. Say in your README and input schema what free users get, as Apify asks of every Actor that limits
   free users.

## The one answer you need

`admit()` returns a decision:

| Field | Meaning |
|---|---|
| `allowed` | `true`: go ahead and deliver. `false`: nothing left; end the run with `message` |
| `unlimited` | `true`: no limit applies (paying user, guard off, or storage unreadable) |
| `remaining` | Results this run may deliver (`null` / `None` when unlimited) |
| `message`, `resets_on` / `resetsOn` | What to tell the user, and the UTC day more becomes available |
| `guard` | Keep it while the run delivers: `take(n)` before each batch, `close()` at the end |

Everything else (plan detection, holds against parallel runs, booking, monthly reset, cleanup of old
records) happens inside.
