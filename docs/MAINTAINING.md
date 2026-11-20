# Maintaining

## One-time repository setup

Run once after creating the repository on GitHub (needs the GitHub CLI, `gh auth login`):

```bash
# Protect main: pull requests only, squash merges, CI green, one code-owner review,
# no force pushes or deletion. Admins may merge their own pull requests (bypass
# "pull_request" mode), but nobody pushes straight to main.
gh api -X POST repos/tacps126/apify-bot-guard/rulesets --input .github/rulesets/main.json

# Squash merges only, branches deleted after merge.
gh repo edit tacps126/apify-bot-guard --enable-squash-merge --enable-merge-commit=false \
  --enable-rebase-merge=false --delete-branch-on-merge

# Private vulnerability reporting (used by SECURITY.md).
gh api -X PUT repos/tacps126/apify-bot-guard/private-vulnerability-reporting
```

To change the protection later, edit `.github/rulesets/main.json` and update the ruleset:
`gh api repos/tacps126/apify-bot-guard/rulesets` lists it with its id, then
`gh api -X PUT repos/tacps126/apify-bot-guard/rulesets/<id> --input .github/rulesets/main.json`.

## Releasing

1. Open a pull request that bumps the version in all three places (`python/pyproject.toml`,
   `python/apify_bot_guard/__init__.py`, `js/package.json`) and moves the "Unreleased" notes in
   `CHANGELOG.md` under `## X.Y.Z`. CI fails if the three versions differ or the section is missing.
2. Merge it. The Release workflow sees a version with no tag, builds both packages, tags `vX.Y.Z`
   and creates a GitHub Release with the changelog section and the built files attached.
3. Versions with a suffix (`0.2.0-rc.1`) become pre-releases.

## Publishing to PyPI and npm (off until enabled)

The Publish workflow runs when a GitHub Release is published, but only when the repository variable
`PUBLISH_ENABLED` is `true`. To turn it on:

1. **PyPI, trusted publishing (no token stored):** on pypi.org, add a pending publisher for project
   `apify-bot-guard`: owner `tacps126`, repository `apify-bot-guard`, workflow `publish.yml`,
   environment `pypi`.
2. **npm:** create an automation token on npmjs.com, add it as the repository secret `NPM_TOKEN`.
3. In GitHub, Settings, Environments: create `pypi` and `npm`. Optionally add yourself as a required
   reviewer on each, so every publish waits for your click.
4. Settings, Secrets and variables, Actions, Variables: add `PUBLISH_ENABLED` = `true`.

To publish a release made before publishing was on: Actions, Publish, Run workflow, tag `vX.Y.Z`.

## Housekeeping

- Dependabot opens grouped pull requests (actions weekly, npm and pip monthly).
- Keep Python and JavaScript in step: `python3 scripts/parity.py` must pass.
