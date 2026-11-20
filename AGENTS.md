# AGENTS.md

Guide for AI coding agents working on this repository. For adding the guard to another project, see
[docs/AI-INTEGRATION.md](docs/AI-INTEGRATION.md) instead.

## Layout

| Path | What |
|---|---|
| `python/apify_bot_guard/` | Python package: `core.py` (config, ledgers, allowance), `guard.py` (BotGuard, Decision), `backends.py` |
| `js/src/` | JavaScript package, same structure in camelCase; `index.d.ts` holds the public types |
| `docs/DESIGN.md` | The spec both packages implement: allowance formula, storage format |
| `docs/CONFIGURATION.md` | User reference for every setting |
| `scripts/parity.py` | Runs the same allowance cases through Python and JavaScript and compares |
| `scripts/release_info.py` | Checks that versions and changelog agree |
| `examples/` | Runnable integrations; keep them working when the API changes |

## Commands

```bash
cd python && ruff check . ../scripts ../examples && ruff format --check . ../scripts ../examples && mypy
cd python && coverage run -m unittest discover -s tests -t . && coverage report   # gate 95%
cd js && npm ci && npm run check                                                 # eslint, tsc, tests + coverage
python scripts/parity.py                                                         # must print "parity ok"
python scripts/release_info.py check
```

Run all of them before you say a change is done.

## Rules

- One spec, two packages. A change to limits, windows, keys or storage format lands in Python, in
  JavaScript, in `docs/DESIGN.md`, in `scripts/parity.py` cases and in tests, in one change.
- A new setting also goes into `LIMIT_FIELDS` / `SETTINGS`, the env var table in
  `docs/CONFIGURATION.md`, the README settings table, `index.d.ts` and `tests/test_controls.py` /
  `test/controls.test.js`.
- No runtime dependencies. Store clients are optional extras (Python) or optional peers (JavaScript),
  imported only inside the backend that needs them.
- Fail open: a storage error never stops a run; log a warning and continue unlimited.
- Never store or log a raw user id; keys hold a salted hash.
- Plain ASCII in docs and messages (no curly quotes, em dashes or ellipsis characters).
- Update `CHANGELOG.md` under "Unreleased".
