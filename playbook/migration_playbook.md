# Migration Playbook — Python service standardization + 3.14 upgrade

You are migrating a single Python service repository. Follow these steps in
order. Make the **smallest change that satisfies each step**. Do **not** alter
business logic or public APIs. If you cannot complete a step, stop, leave a
clear note in the PR description under "NEEDS REVIEW", and continue to the
verification step so a human can pick it up.

## 1. Impose the standard project structure (copier template)
- Apply the org copier template `gh:acme-fin/py-service-template` with defaults
  inferred from the repo (service name = repo name, package = existing `src/`
  package).
- Reconcile, do not clobber: keep existing source, tests, and README content;
  adopt the template's `pyproject.toml`, CI config, and directory conventions.

## 2. Replace Make with just
- Translate every `Makefile` target into an equivalent `justfile` recipe
  (`install`, `test`, `lint`, `run`, `clean`), preserving behavior.
- Delete the `Makefile` once parity is confirmed.

## 3. Move dependency management to uv
- Convert `requirements.txt` / `requirements-dev.txt` / `setup.py` metadata into
  `pyproject.toml` dependencies and dependency groups.
- Generate a `uv.lock` with `uv lock`. Use `uv sync` for installs in recipes.

## 4. Upgrade Python 3.11 → 3.14
- Set `requires-python = ">=3.14"`, update `.python-version` to `3.14`, and any
  classifiers / CI matrix.
- Fix removed/deprecated stdlib usage. Known hotspots in this codebase:
  - `datetime.utcnow()` → `datetime.now(datetime.UTC)` (utcnow removed path).
  - Audit for other deprecations surfaced by `python -W error`.

## 5. Resolve dependencies for 3.14
- Run `uv lock` and upgrade pins that don't support 3.14.
- **pydantic v1 → v2** is expected: migrate `class Config` → `model_config`,
  validators, and `.dict()`/`.json()` call sites. If a transitive pin cannot be
  resolved for 3.14, do not force it — record it under NEEDS REVIEW.

## 6. Verify (gate before PR)
- Run `just test`. Iterate until the suite is green **without weakening tests**.
- Run `just lint`.
- If green: open a PR titled `chore: standardize tooling + upgrade to Python 3.14`
  with a description summarizing each step and any residual risks.
- If not green after reasonable effort: open the PR anyway, mark it `NEEDS REVIEW`,
  and list exactly what failed and why.

## Guardrails
- No business-logic changes. No public-API changes. No test weakening.
- Prefer minimal diffs. Every deviation from the playbook must be noted in the PR.
- The PR is for human review; nothing is auto-merged.
