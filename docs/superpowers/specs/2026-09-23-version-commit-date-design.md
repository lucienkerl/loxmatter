# The version says which commit it is, and from when

Date: 23 September 2026. Follows "Updates through the web UI" (2026-09-08),
whose section 4 gave the running bridge its identity.

## 1. What is missing

System → Version answers "Running: dev — Commit `a3f91c2` — built
2026-09-22 22:19". On the `dev` channel that is one number short of useful.
`dev` is a moving tag: every merge into `main` publishes a new image under it,
so the version alone says nothing about which state is installed. The commit is
there, but a seven-character hash answers "which state" only for someone who
looks it up, and it answers "how old is this" not at all.

The build time is the closest thing on offer today, and it is the wrong
measure: it is when the image was produced, which moves when the same state is
rebuilt, and it says nothing about when the code was written.

## 2. Goal

Wherever the running build knows its commit, the page names the commit **and
the date of that commit** — on `dev`, where it is the only way to tell one
build from the next, and on a release just as well, where it answers "is
yesterday's fix in this?" without a lookup.

The build time stays next to it: it answers a different question, namely when
this image was produced.

## 3. Where the date comes from

The CI workflow's `meta` step already computes the version, the commit and the
build time (`.github/workflows/ci.yml`). It gains the commit's own date, in the
same shape as `built_at` and in UTC, so the two read alike:

```sh
echo "commit_date=$(TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd HEAD)"
```

It travels into the image as a fourth build argument,
`LOXMATTER_COMMIT_DATE`, declared in the `Dockerfile` as an `ARG` with an
empty default and set as `ENV` beside the other three.

**Why a build argument and not a lookup at run time.** The value belongs to the
image, the way the version and the commit already do (`src/loxmatter/version.py`
states the reason at length: the checkout on the host may have moved on, or
moved away, without any of that being deployed — the image *is* what runs). A
build argument is also readable without internet, and it cannot later claim
something else than what was built.

## 4. Through the layers

- **`src/loxmatter/version.py`** — `BuildInfo` gains `commit_date: str | None`,
  read through the existing `_clean()`, which treats an empty environment
  variable as missing. That is not a formality: Docker Compose interpolates a
  variable missing from `.env` to an empty string, the trap that once caught
  `LOXMATTER_API_TOKEN`.
- **`src/loxmatter/api/version.py`** — `VersionOut` gains `commit_date` and
  `GET /api/version` reports it.
- **`Dockerfile`** — the new `ARG`/`ENV` pair.

Nothing else reads the field. The update check keeps comparing commits, not
dates.

## 5. What the page shows

In the hint line under "Running: {version}", the commit span becomes dated when
a date is known:

| Known | Text (en) |
|-------|-----------|
| commit and date | "Commit a3f91c2 of 21/09/2026, 18:42" |
| commit only | "Commit a3f91c2" (today's text, unchanged) |
| neither | nothing; the existing "built from a working copy" hint applies |

The date is rendered with the page's existing `formatTimestamp`, in the
viewer's local time, like every other timestamp in the UI. The "built …" span
next to it is unchanged.

A new key `web.system.version_commit_dated` carries the dated wording in `en`
and `de`; `web.system.version_commit` stays for the undated case.

## 6. What older installations see

An image built before this change carries no `LOXMATTER_COMMIT_DATE`, so
`commit_date` is `null` and the page reads exactly as it does today. An image
built by hand on a host (`docker compose build`) carries neither commit nor
date, and keeps the "built from a working copy" hint. Nothing here changes the
update path: the field appears with the first image CI builds after this
change.

## 7. Tests

- `tests/test_build_arguments.py` already asserts that the CI's `build-args`
  and the `Dockerfile`'s `ARG`s are the *same set*, so a name added on one side
  only fails there. Added to it: the `meta` step defines `commit_date`, and it
  uses `git show -s` with an explicit format rather than the local default —
  the same shape of check the file already makes for the schema-version grep.
- `tests/api/test_version_api.py`: `commit_date` is reported when the
  environment names it, and `null` when it is missing or empty.
- `tests/api/test_web.py`: the served markup uses the dated key when
  `versionInfo.commit_date` is set and the undated one otherwise, and both keys
  exist in `en` and `de`.

## 8. Not part of this

- A link to the commit on GitHub.
- Commit and date for the update being *offered* (only the installed build is
  named).
- Reading the commit from a checkout on the host at run time.
