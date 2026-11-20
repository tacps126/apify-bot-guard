# Contributing

Thanks for helping. Bug reports, new backends, ports to other languages and doc fixes are all welcome.

## Ground rules

- The Python and JavaScript packages implement one spec, [docs/DESIGN.md](docs/DESIGN.md). A change to
  the rules or the storage format lands in both packages and the spec in the same pull request.
- Every change comes with tests. Coverage gates: Python 95% lines + branches, JavaScript 95% lines.
- No new runtime dependencies. Clients for a store are optional extras (Python) or optional peer
  dependencies (JavaScript), imported only when that backend is used.
- Fail open: a storage error must never stop a customer's run. Log and continue.
- Never store raw user ids.

## Setup

```bash
# Python
cd python
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
coverage run -m unittest discover -s tests -t . && coverage report

# JavaScript
cd js
npm ci
npm run check   # eslint, tsc on the types, tests with coverage gates
```

## Pull requests

1. Open an issue first for anything bigger than a fix, so we agree on the approach.
2. Keep the change focused; one topic per pull request.
3. Update `CHANGELOG.md` under "Unreleased".
4. CI must be green (lint, types, tests on every supported Python and Node version).

## Releases

Maintainers bump the version in `python/pyproject.toml`, `python/apify_bot_guard/__init__.py` and
`js/package.json` together, move "Unreleased" in the changelog under the new version, tag `vX.Y.Z`,
and publish both packages.

## Conduct

Be kind and assume good intent. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
