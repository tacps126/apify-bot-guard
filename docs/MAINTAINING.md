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

The Publish workflow publishes a GitHub Release to both registries with trusted publishing: each job
proves it is this repository's `publish.yml` through GitHub's OIDC token and the registry gives it a
one-time upload credential. No PyPI or npm token is stored anywhere, and npm versions get provenance.
It runs only when the repository variable `PUBLISH_ENABLED` is `true`, and skips versions already
published.

1. **PyPI** (a new project can be trusted before its first upload):
   - Create an account on pypi.org and turn on two-factor authentication (required).
   - Account settings, Publishing, Add a new pending publisher, GitHub: PyPI project name
     `apify-bot-guard`, owner `tacps126`, repository `apify-bot-guard`, workflow `publish.yml`,
     environment `pypi`. The first run of the workflow creates the project.
2. **npm, first version by hand** (npm can only trust a workflow for a package that already exists).
   From a clean checkout of the release tag, signed in with `npm login` (two-factor code when asked):

   ```bash
   git switch --detach v0.1.0
   cd js && npm ci && npm publish --access public
   git switch -
   ```

3. **npm, trust the workflow** on npmjs.com: the package, Settings, Trusted publishing, GitHub Actions:
   organization or user `tacps126`, repository `apify-bot-guard`, workflow file `publish.yml`, environment
   `npm`. Save, then under Publishing access choose "Require two-factor authentication and disallow
   tokens", so only this workflow (and you, with a code) can publish. The trusted publisher must
   publish once within 2 days of being added, or it expires: do step 5 soon after.
4. **GitHub:** Settings, Environments: create `pypi` and `npm` (optionally add yourself as a required
   reviewer on each, so every publish waits for your click). Settings, Secrets and variables,
   Actions, Variables: add `PUBLISH_ENABLED` = `true`. No secrets are needed.
5. **Publish:** merge a pull request that bumps the version (Releasing, above) within 2 days of step
   3. The Release workflow tags it and starts Publish, which uploads to PyPI and npm. To publish an
   existing tag again: Actions, Publish, Run workflow, tag `vX.Y.Z` (versions already up are
   skipped).

## Housekeeping

- Dependabot opens grouped pull requests (actions weekly, npm and pip monthly).
- Keep Python and JavaScript in step: `python3 scripts/parity.py` must pass.
