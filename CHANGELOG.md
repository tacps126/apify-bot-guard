# Changelog

All notable changes to both packages. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

## 0.1.0

First release, Python and JavaScript.

- Monthly free-plan allowances per user per Actor and per user across Actors, by results and by cost.
- Daily share of the monthly allowance.
- Per-Actor pool for all free users, released evenly through the month.
- Smaller share for new accounts (creation date, or first seen).
- Holds at run start so parallel runs cannot each take the whole allowance.
- Backends: Apify key-value store, Redis / Valkey / Upstash (pipelined), in-memory.
- Generic identity: works outside Apify with your own user id, plan and run id.
- `push_data` / `pushData` helpers for the Apify SDK.
