# The Version Names Its Commit Date - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** System → Version names the commit *and* the date of that commit, so a
`dev` build can be told from the next one without a lookup.

**Architecture:** CI computes the commit's own date next to the values it
already computes, and passes it into the image as a fourth build argument.
`BuildInfo` and `GET /api/version` carry it through, and the version card in the
web UI shows a dated commit line when the running build knows a date.

**Tech Stack:** GitHub Actions, Docker build arguments, Python 3.12 (FastAPI,
pydantic), Alpine.js, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-version-commit-date-design.md`

**Worktree:** `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/version-commit-date`
(branch `claude/version-commit-date`). Every path below is relative to it.
Subagents: `cd` there first and use absolute paths; the main checkout is a
different branch.

## Global Constraints

- Everything in the repository is English; user-facing text goes through
  `src/loxmatter/i18n/strings.yaml` with an `en` and a `de` value, resolved with
  `i18n.t(...)` / `t(...)` at call time. `de:` values are German by design.
- The build argument is named `LOXMATTER_COMMIT_DATE`.
- Its value has the same shape as `built_at`: UTC, `%Y-%m-%dT%H:%M:%SZ`.
- An empty environment variable counts as missing (`_clean` in
  `src/loxmatter/version.py`) - Docker Compose interpolates an absent variable
  to an empty string.
- An image without the new value behaves exactly as today: the page shows the
  undated commit line, and nothing else changes.
- The update check is not touched: it keeps comparing commits, not dates.
- Commit messages: Conventional Commits, English, ending with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Tests: run only the files a task names, in the foreground - never
  `run_in_background`, never `Monitor`. The whole suite takes ~11 minutes and
  does not fit one Bash call. Before every commit run `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`,
  `uv run python scripts/check_language.py`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `.github/workflows/ci.yml` (modify) | compute `commit_date`, pass it as a build argument |
| `Dockerfile` (modify) | declare `ARG`/`ENV LOXMATTER_COMMIT_DATE` |
| `tests/test_build_arguments.py` (modify) | CI and Dockerfile agree; the date is read in the shape the UI expects |
| `src/loxmatter/version.py` (modify) | `BuildInfo.commit_date` |
| `src/loxmatter/api/version.py` (modify) | `VersionOut.commit_date` |
| `tests/api/test_version_api.py` (modify) | the route reports it, and stays green without it |
| `src/loxmatter/web/index.html`, `src/loxmatter/i18n/strings.yaml` (modify) | the dated commit line |
| `tests/api/test_web.py` (modify) | the markup binds both variants |
| `CHANGELOG.md` (modify) | the change note |

---

### Task 1: The image carries the commit's date

**Files:**
- Modify: `.github/workflows/ci.yml` (the `meta` step's output block, and the
  `docker/build-push-action` step's `build-args` of the `image` job)
- Modify: `Dockerfile` (the `ARG`/`ENV` block, around lines 79-87)
- Test: `tests/test_build_arguments.py`

**Interfaces:**
- Produces: build argument and environment variable `LOXMATTER_COMMIT_DATE`,
  value shaped `2026-09-21T18:42:11Z`; workflow output `steps.meta.outputs.commit_date`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_build_arguments.py`:

```python
# The commit's own date, in the same UTC shape as `built_at` so the two read
# alike in the version card. `git show -s` with an explicit format rather than
# the local default: a runner in another timezone would otherwise publish a
# different spelling of the same instant.
COMMIT_DATE_COMMAND = (
    "TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd HEAD"
)


def test_the_ci_reads_the_commit_date_in_the_same_shape_as_the_build_time() -> None:
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert f'commit_date=$({COMMIT_DATE_COMMAND})' in workflow_source
    assert 'built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)' in workflow_source


def test_every_build_argument_of_the_image_is_also_exported_as_an_environment_variable() -> None:
    """An `ARG` the image does not turn into an `ENV` reaches the build and
    nothing else: the running bridge would report the value as missing, which
    is exactly what a fourth argument is easy to forget."""
    source = DOCKERFILE.read_text(encoding="utf-8")
    declared = set(re.findall(r"^ARG\s+([A-Z_][A-Z0-9_]*)", source, re.MULTILINE))
    exported = set(re.findall(r"^\s*(?:ENV\s+)?([A-Z_][A-Z0-9_]*)=\$\{\1\}", source, re.MULTILINE))
    assert declared <= exported
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_build_arguments.py -v`
Expected: the two new tests FAIL (`commit_date=$(...)` not in the workflow; the
`ARG`/`ENV` sets already agree today, so the second test may pass before the
change - if it does, say so in the report and keep it as the guard it is).

- [ ] **Step 3: Compute the date in CI**

In `.github/workflows/ci.yml`, in the `meta` step's output block, add the line
after `built_at` (keep the existing three unchanged):

```sh
            echo "commit_date=$(TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd HEAD)"
```

- [ ] **Step 4: Pass it to the build**

In the same job's `docker/build-push-action` step, extend `build-args`:

```yaml
            LOXMATTER_COMMIT_DATE=${{ steps.meta.outputs.commit_date }}
```

- [ ] **Step 5: Declare it in the Dockerfile**

```dockerfile
ARG LOXMATTER_COMMIT_DATE=""
```

directly after `ARG LOXMATTER_COMMIT=""`, and in the `ENV` block:

```dockerfile
ENV LOXMATTER_VERSION=${LOXMATTER_VERSION} \
    LOXMATTER_COMMIT=${LOXMATTER_COMMIT} \
    LOXMATTER_COMMIT_DATE=${LOXMATTER_COMMIT_DATE} \
    LOXMATTER_BUILT_AT=${LOXMATTER_BUILT_AT} \
    LOXMATTER_SCHEMA_VERSION=${LOXMATTER_SCHEMA_VERSION}
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_build_arguments.py -v`
Expected: all PASS, including the pre-existing
`test_the_ci_passes_exactly_the_arguments_the_dockerfile_knows`, which now
compares five names on both sides.

- [ ] **Step 7: Check the command by hand**

Run: `TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd HEAD`
Expected: a single line like `2026-09-23T05:34:50Z`. Paste it into the report.

- [ ] **Step 8: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add .github/workflows/ci.yml Dockerfile tests/test_build_arguments.py
git commit -m "build: carry the commit's date into the image

On the dev channel the version tag moves with every merge, so the commit is
the only thing that names the state - and a bare hash does not say how old it
is. CI knows the date; the image now carries it like the three values beside
it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The bridge reports it

**Files:**
- Modify: `src/loxmatter/version.py` (`BuildInfo`, `build_info()`)
- Modify: `src/loxmatter/api/version.py` (`VersionOut`, `get_version`)
- Test: `tests/api/test_version_api.py`, `tests/test_cli.py` (only if it asserts the version payload - grep first)

**Interfaces:**
- Consumes (Task 1): environment variable `LOXMATTER_COMMIT_DATE`.
- Produces: `BuildInfo.commit_date: str | None`; `GET /api/version` field
  `commit_date` (string or `null`), next to `version`, `commit`, `built_at`,
  `schema_version`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_version_api.py`, extend the existing
`test_die_route_nennt_die_vier_angaben` (keep its name; renaming it is not part
of this change) so the payload carries the new field:

```python
    monkeypatch.setenv("LOXMATTER_COMMIT_DATE", "2026-09-08T09:58:12Z")
```

and in its expected dict, between `commit` and `built_at`:

```python
        "commit_date": "2026-09-08T09:58:12Z",
```

Extend `test_it_responds_even_in_development_checkout`'s tuple of deleted names
with `"LOXMATTER_COMMIT_DATE"` and add one assertion to it:

```python
    assert response.json()["commit_date"] is None
```

and append:

```python
async def test_an_empty_commit_date_reads_as_missing(api, monkeypatch):
    """Docker Compose interpolates a variable missing from `.env` to an empty
    string - the trap `_clean` exists for. Fault to prove it: read the variable
    with `os.environ.get` instead of `_clean`."""
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_COMMIT_DATE", "")
    response = await api.get("/api/version")
    assert response.json()["commit_date"] is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: FAIL - the response has no `commit_date` key.

- [ ] **Step 3: Implement**

`src/loxmatter/version.py`:

```python
@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    commit_date: str | None
    built_at: str | None
    schema_version: int
```

and in `build_info()`, between `commit` and `built_at`:

```python
        commit_date=_clean("LOXMATTER_COMMIT_DATE"),
```

Add two sentences to the module docstring's list of values, after the sentence
about `commit`/`built_at`: the commit date is the date of the commit the image
was built from, in UTC, and it is not the build time - a rebuild of the same
state moves `built_at` and leaves `commit_date` where it was.

`src/loxmatter/api/version.py`:

```python
class VersionOut(BaseModel):
    version: str
    commit: str | None
    commit_date: str | None
    built_at: str | None
    schema_version: int
```

and in `get_version()`:

```python
            commit_date=info.commit_date,
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: all PASS.

Then: `grep -rn "built_at" tests src/loxmatter --include=*.py | grep -v test_version_api`
and run any other test file that builds a `BuildInfo` or asserts the payload
(the dataclass gained a field, so a positional construction elsewhere would
break). Run those files and report which ones you ran.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/version.py src/loxmatter/api/version.py tests
git commit -m "feat(api): report the commit's date with the running version

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The version card shows the dated commit

**Files:**
- Modify: `src/loxmatter/web/index.html` (the version card, the commit `<span>` around line 2181)
- Modify: `src/loxmatter/i18n/strings.yaml` (after `web.system.version_commit`)
- Modify: `CHANGELOG.md` (`## [Unreleased]`, `### Added`)
- Test: `tests/api/test_web.py`, `tests/api/test_version_api.py`

**Interfaces:**
- Consumes (Task 2): `versionInfo.commit_date` from `GET /api/version`.
- Produces: i18n key `web.system.version_commit_dated` with `{commit}` and
  `{date}`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_web.py`, inside
`test_the_system_view_shows_the_running_version`, after the existing
`version_commit` assertion:

```python
    # With a commit date the same span says when that commit was made; without
    # one it stays the undated sentence, which is what an image built before
    # this field existed reports (design 2026-09-23, section 6).
    assert (
        "t('web.system.version_commit_dated', { commit: formatCommit(versionInfo.commit), "
        "date: formatTimestamp(versionInfo.commit_date) })" in page
    )
    assert "versionInfo.commit_date" in page
```

In `tests/api/test_version_api.py`, add the new key to the tuple in
`test_the_ui_knows_all_texts_of_the_version_card`:

```python
        "web.system.version_commit_dated",
```

and append there:

```python
async def test_the_dated_commit_text_exists_in_both_languages():
    """The generic check above resolves through the English fallback, so a
    missing `de` would pass it. Fault to prove it: delete the `de:` line."""
    from loxmatter import i18n

    entry = i18n._STRINGS["web.system.version_commit_dated"]
    assert entry.get("en") and entry.get("de") and entry["en"] != entry["de"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/api/test_web.py -v -k version` then
`uv run pytest tests/api/test_version_api.py -v`
Expected: FAIL - the key does not exist and the markup does not name it.

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, directly after `web.system.version_commit`:

```yaml
web.system.version_commit_dated:
  en: "Commit {commit} of {date}"
  de: "Commit {commit} vom {date}"
```

- [ ] **Step 4: Change the markup**

In `src/loxmatter/web/index.html`, replace the commit `<span>` of the version
card with:

```html
                <!-- The commit line says when that commit was made as soon as
                     the build knows it (design 2026-09-23). An image built
                     before this field existed, or by hand on a host, reports
                     no date and keeps the undated sentence. The build time in
                     the span below stays: it answers a different question,
                     namely when this image was produced. -->
                <span x-show="versionInfo.commit" x-cloak
                      x-text="versionInfo.commit_date
                              ? t('web.system.version_commit_dated', { commit: formatCommit(versionInfo.commit), date: formatTimestamp(versionInfo.commit_date) })
                              : t('web.system.version_commit', { commit: formatCommit(versionInfo.commit) })"></span>
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/api/test_web.py -q -k version`, then
`uv run pytest tests/api/test_version_api.py -q`, then
`uv run pytest tests/test_i18n.py -q`
Expected: all PASS.

- [ ] **Step 6: See it in a throwaway harness**

The tests above prove only that markup and strings are delivered. Check the
binding: serve `src/loxmatter/web/` with a stub `GET /api/version` answering
once with `commit_date` set and once with it `null`, open it in a browser, and
confirm the line reads "Commit a3f91c2 of …" and then "Commit a3f91c2". Delete
the harness afterwards; it is not committed. (The controller may do this step
instead - ask before skipping it.)

- [ ] **Step 7: CHANGELOG**

Under `## [Unreleased]`, `### Added`, in the file's bold-lead style:

```markdown
- **The version card names the commit's date.** On the `dev` channel the
  version alone cannot say which state is installed; the commit line now reads
  "Commit a3f91c2 of 21/09/2026, 18:42" as soon as the running image knows it.
```

- [ ] **Step 8: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests CHANGELOG.md
git commit -m "feat(web): say when the running commit was made

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Verify against a built image

**Files:** none (verification only)

- [ ] **Step 1: Build with the arguments CI passes**

```bash
docker build --build-arg LOXMATTER_VERSION=dev --build-arg LOXMATTER_COMMIT="$(git rev-parse --short HEAD)" --build-arg LOXMATTER_COMMIT_DATE="$(TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd HEAD)" --build-arg LOXMATTER_BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" --build-arg LOXMATTER_SCHEMA_VERSION="$(sed -n -E 's/^_SCHEMA_VERSION = ([0-9]+)$/\1/p' src/loxmatter/model/store.py)" -t loxmatter-commit-date-check .
```

If Docker is not available on the machine, say so in the report and instead run
the same check with the environment variables set directly:
`LOXMATTER_COMMIT_DATE=2026-09-21T18:42:11Z uv run python -c "from loxmatter.version import build_info; print(build_info())"`.

- [ ] **Step 2: Read the value back out of the image**

```bash
docker run --rm --entrypoint python loxmatter-commit-date-check -c "from loxmatter.version import build_info; print(build_info())"
```

Expected: `BuildInfo(version='dev', commit='…', commit_date='2026-…Z', built_at='2026-…Z', schema_version=…)`.

- [ ] **Step 3: Clean up and report**

```bash
docker image rm loxmatter-commit-date-check
```

Record both outputs in the report. No commit.

---

## Self-review

- Spec 3 → Task 1; 4 → Task 2; 5 → Task 3; 6 → Task 3's undated branch and
  Task 2's empty-value test; 7 → each task's tests; 8 (not part of this) → no
  task touches the update check, GitHub links, or a host checkout.
- Names used across tasks: `LOXMATTER_COMMIT_DATE`, `steps.meta.outputs.commit_date`,
  `BuildInfo.commit_date`, `VersionOut.commit_date`, `versionInfo.commit_date`,
  `web.system.version_commit_dated` - consistent throughout.
