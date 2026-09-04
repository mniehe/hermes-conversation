# AGENTS.md

Guidance for coding agents working in this repository.

## Releases

- **Never bump the version or cut a release without explicit approval.** That
  means no edits to the version in `manifest.json`, `pyproject.toml` or
  `uv.lock`, no `chore: release` commit, and no tag. Commit the work, then
  propose a version (patch or minor) and wait for a yes.
- A release is the manifest version, a `chore: release X.Y.Z` commit, and an
  annotated tag `vX.Y.Z` on that commit. HACS reads the tag, so the manifest
  version must equal the tag.

## Git

- Conventional commits, one subject line: `type: subject` (`feat`, `fix`,
  `docs`, `chore`, `test`). Imperative mood, no trailing period.
- Never post to GitHub (comments, issues, PR text, dismissals) without showing
  the exact wording and getting approval first.

## Checks before finishing

All of these must pass; CI runs the same set.

```sh
nix develop --command uv run pytest -q --cov=custom_components --cov-report=json:coverage.json
nix develop --command uv run python scripts/check_guard_coverage.py
nix develop --command uv run mypy custom_components
nix develop --command ruff check .
nix develop --command ruff format .
```

`custom_components/hermes_conversation/llm.py` (the tool guard) and
`custom_components/hermes_conversation/policy.py` (the user-group policy) are
the capability boundary and must stay at 100% coverage; a missing branch there
is a vulnerability, not a test gap.

## Working rules

- Never read `.env` files, here or on any host.
- `strings.json` and `translations/en.json` must stay in step. Edit them with
  `jq -a` so unicode escapes survive.
- Home Assistant's source for the pinned version is in `.venv`; check what it
  actually does there before relying on documentation or memory.
- Keep the README's option table, prompt variables and default prompt in sync
  with `const.py` and `config_flow.py`.
