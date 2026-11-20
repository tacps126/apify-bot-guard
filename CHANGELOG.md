# Changelog

All notable changes to both packages. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

## 0.1.1

- Published to PyPI and npm from GitHub Actions with trusted publishing (no stored tokens), npm
  versions with provenance.
- CI: the Python/JavaScript parity check runs on Linux (cases sent on stdin).
- Dependencies: dev tooling updated.

## 0.1.0

First release, Python and JavaScript.

- Limits per user per Actor and per user across Actors, by month, rolling week and day, in results
  and in your cost; per-run limits; a daily share of the monthly allowance. Any mix; the tightest wins.
- Per-Actor pool for all limited users, released evenly through the month.
- Smaller share for new accounts (creation date, or first seen; `track_from` for existing users).
- Choose the limited plans (`plans`, default FREE), exempt or block user ids, observe mode (measure
  and log, never cut), your own message text, salt, auto-prune chance.
- Every setting as a `BOT_GUARD_*` env var or in code (code wins); unreadable values are logged and
  ignored, unknown names in code raise. Reference: docs/CONFIGURATION.md.
- Holds at run start so parallel runs cannot each take the whole allowance.
- Backends: Apify key-value store, Redis / Valkey / Upstash (pipelined), in-memory.
- Generic identity: works outside Apify with your own user id, plan names and run id.
- `admit()`: one decision (allowed, remaining, bound, message, resets on, enforced) to act on.
- Old ledgers cleaned up: Redis by expiry, key-value stores by `prune()` on 2% of runs.
- `push_data` / `pushData` helpers for the Apify SDK.
- AI-ready docs: docs/AI-INTEGRATION.md (one page for assistants, with prompts), llms.txt, AGENTS.md.
- Examples: Python and JavaScript Actors, a free tier on an HTTP API, a generic job runner, cleanup.
