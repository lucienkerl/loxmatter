# Development

[Back to the README](../README.md)

## Running the test suite

```bash
uv sync
uv run pytest
```

The test suite runs without hardware and without network access.

## Commit messages

English, Conventional Commits — see [CLAUDE.md](../CLAUDE.md) for the full
language rules. Subjects before September 2026 are German and stay that way.

## Checks that CI runs

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python scripts/check_language.py
```

## Releasing a version

Starting from 0.2.0, external installations rely on version numbers: the
UI compares the running version with the latest release, and
the updater deploys exactly what was published here. This
chain is not sustained by code, but by discipline — that's why it stands
here.

1. `CHANGELOG.md`: rewrite the `[Unreleased]` section to the new number
   with date. **Write for people who don't know the code** — this text appears in the confirmation dialog before update.
2. Choose the number: `PATCH` for bug fixes, `MINOR` for new
   features, `MAJOR` for anything that requires existing installations
   to update by hand.
3. **If `_SCHEMA_VERSION` in `model/store.py` rises, that belongs in the
   release notes.** A schema jump is the only case where rolling back
   to the previous version is not consequence-free (see [docs/superpowers/specs/2026-09-08-webui-updates-design.md, section 8](superpowers/specs/2026-09-08-webui-updates-design.md)).
4. Bump `version` in `pyproject.toml` to the same number. At
   runtime, this reads nothing — CI derives the image version from the
   Git ref, not from this file —, so a forgotten
   step here never surfaces automatically: an image tagged `0.3.0`
   could be built without complaint from a package that still carries `version =
   "0.2.0"`.
5. Commit, then `git tag -a v0.3.0 -m "0.3.0"` and `git push --tags`.
6. CI builds `:0.3.0` and `:stable` from it. **Only when both are in the
   registry** is the version released — before that, the
   UI shows it, but a deployment would go nowhere.
7. Create a GitHub release whose text is the changelog section. The
   UI reads exactly this text.
