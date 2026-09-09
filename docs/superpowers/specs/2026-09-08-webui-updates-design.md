# Deploying updates through the UI

Specification, 8 September 2026. Affects `deploy/testhost/docker-compose.yml`,
`Dockerfile`, `.github/workflows/ci.yml`, `scripts/update.sh`, the
System tab of the WebUI, and a new route group `/api/update/*`. Introduces
a new service to the stack (`loxmatter-updater`) and a second image built
by the same CI.

## 1. The problem

An update currently requires an SSH session. Whoever operates the bridge must
find the machine, log in, navigate to the right directory, and call
`./scripts/update.sh`. This is the point where someone automating a home
but not managing servers gives up — and then runs on an old version with
known bugs for an indefinite time.

Two things stand in the way, both more fundamental than a missing button.

**First: nobody knows what's running.** `version = "0.1.0"` has stood unchanged
since 628 commits in `pyproject.toml`, there is no single tag, no
release, and the UI displays no version anywhere. An update requires that
"before" and "after" be nameable. As long as the only answer to "what version
is this?" is a git hash in the checkout on the host, which the running process
does not know, there is no update — only a restart with unclear outcome.

**Second: the bridge cannot replace itself.** It runs
in a container without a Docker socket. The process that would call
`docker compose up -d --force-recreate` is exactly the one that call would end —
in the middle of its own work, with no way to report the result. Something
outside the container is needed to survive the restart.

There is also a third, smaller inconvenience worth addressing:
`docker compose build` takes several minutes on a Raspberry Pi
and can fail on PyPI outages or memory limits. What is a long progress bar
in the terminal would be unreasonable in the browser.

## 2. What this specification aims for

1. The UI **displays which version is running** — permanently, not only
   when something is pending.
2. It **reports when a newer version is available**, in one of two
   channels (stable, development).
3. One click, one confirmation, and the bridge **applies the update itself**
   — visible, step by step, even while it is not responding.
4. If it does not come back healthy, **it rolls back automatically**,
   without anyone having to watch.

## 3. What remains unchanged

- `scripts/update.sh` continues to exist as the console path and is switched to
  the same flow. The sidecar is an additional path, not a replacement.
- matter-server and otbr are **not** touched. Their state is tied to
  volumes — thread network keys, Fabric credentials — and a failed update there costs
  re-pairing every device. The UI only displays their running images. The structure
  anticipates a separate, individually confirmed button for each service; it will be
  built later with its own warning for otbr.
- Signal keys, rooms, export settings: untouched. An update swaps code, not data.
- The `build:` block is retained, **alongside** `image:` on the same service.
  Building from source remains possible (`docker compose build`). A profile would not have been
  feasible here: profiles apply to services, not to individual keys of a service. That it
  never accidentally builds is ensured by the caller, not the file — `update.sh` and the
  updater explicitly call `compose pull` before `up -d`, and `up` only builds
  if no image exists locally. On a host without GHCR access, that is exactly
  the desired fallback.

## 4. Version identity

Without a reliable answer to "what is running here" the whole rest does not hold.
It comes from the image, not from the checkout on the host — which
may by now be elsewhere, moved or advanced, without that ever being shipped.

The build receives four arguments and stores them as `ENV` in the image:

| Variable | Contents |
|---|---|
| `LOXMATTER_VERSION` | tag (`0.3.0`) or `dev` |
| `LOXMATTER_COMMIT` | short SHA |
| `LOXMATTER_BUILT_AT` | time of build |
| `LOXMATTER_SCHEMA_VERSION` | `_SCHEMA_VERSION` from `model/store.py` |

The last line is not incidental. It allows reading an image's schema
**via `docker inspect` without starting the container** — this is what
the pre-check in section 8 depends on. The CI reads its value from
`model/store.py` before the build and passes it as a build argument; it
is not maintained by hand. A second place that asserts the same number
would be a place that will someday assert something different.

`GET /api/version` returns these, plus the running images of
matter-server and otbr (those come via `docker inspect` through the sidecar;
the bridge itself has no socket).

## 5. What CI builds

| Trigger | Tags |
|---|---|
| Push to `main` | `:dev`, `:sha-<short>` |
| Tag `v*` | `:<version>`, `:stable` |

Multi-arch for `arm64` and `amd64` — the Pi is the standard case, not the
exception. The Compose file references

```yaml
image: ghcr.io/lucienkerl/loxmatter:${LOXMATTER_IMAGE_TAG:-stable}
```

and **the updater rewrites this one value in `.env` when switching.**
This is also the rollback mechanism from section 8: the previous
value is a line you write back. `.env` is not version-controlled,
so a `git checkout` does not touch it — which is exactly why the
version sits there and not in the Compose file.

The same CI builds a second, tiny image
`ghcr.io/lucienkerl/loxmatter-updater` (Alpine, Docker CLI,
Compose plugin, git, curl).

**This is where tagging begins.** This is the commitment of this specification
that does not live in code: from the first tag `v0.2.0` onward, a version
is something foreign installations can rely on. This includes
`CHANGELOG.md` and a section in `docs/DEVELOPMENT.md` that states
what a release entails — otherwise the chain deteriorates at exactly the point
where it is sustained by discipline rather than code.

## 6. The sidecar

New service `loxmatter-updater` in `deploy/testhost/docker-compose.yml`:

- Image pinned by digest.
- Mounted: the Docker socket, the repo checkout, `loxmatter-store:/data`
  — the same volume where the bridge holds its database.
- **No `ports:`, no `network_mode: host`.** It sits on the
  standard Compose network: reachable outward (for `git fetch`), not from the
  LAN. This distinguishes it from all three existing services and
  is the reason it is allowed to have the socket.
- `extra_hosts: ["host.docker.internal:host-gateway"]` so it can reach `/health`
  on the host.
- Operation: loop with `sleep 2`.

Why a permanent service and not a helper started on demand:
someone has to notice the request, and the only one who could start a
container is the one who has the socket. The alternative
would have been to mount the socket in the bridge — i.e., precisely in
the service that is reachable across the entire LAN with `network_mode: host`. The
justifications in the Compose file around Fabric security
argue at length against this design; this specification follows them.

## 7. Communication via files

No network between bridge and sidecar. Three files under
`/data/update/`:

| File | Who writes | Contents |
|---|---|---|
| `request.json` | bridge | `{id, channel, target, requested_at}`, atomic via temp + rename |
| `state.json` | updater | `{id, phase, steps[], from, to, error, rolled_back, healthy, updater_seen_at}` |
| `log.txt` | updater | raw output, capped |

The updater processes an `id` exactly once. It writes `updater_seen_at`
with every loop iteration — this is how the bridge knows if one is present at all
(section 11).

That the state sits in a file in the volume and not in the bridge's memory
is the carrying decision of this specification. Only this allows
the UI to continue reading after a restart instead of running into
a connection error.

## 8. The updater flow

Before starting, it records in `state.json`: the value of `LOXMATTER_IMAGE_TAG`, the
git ref, the path of the fresh backup, the running `user_version`.

1. **`backup`** — signal database to `/data/backups/store-<stamp>.tgz`.
   Intentionally in the same volume: the volume survives the image swap, and what
   we back up against is a failed migration, not volume loss. The UI offers
   the backup for download so it can leave the device.
2. **`pull`** — `git fetch --tags`, check out the target (the
   Compose file must match the version), then
   `docker compose pull loxmatter`.
3. **`recreate`** — `docker compose up -d --no-deps loxmatter`. `--no-deps`
   as today: matter-server and otbr remain untouched, and **the
   updater does not recreate itself**, which would end it in the middle of its own
   request.
4. **`health`** — wait up to **120 s** for `/health`. Not a new value,
   but the one from `scripts/update.sh`, with its documented
   rationale from 8 September: 20 s was too tight, one run went over,
   and the script reported a service as sick that was working fine
   ten seconds later. Too short a window is the more expensive kind of false alarm.
5. **`done`** or **`rollback`**.

**Pre-check before step 1:** The updater reads
`LOXMATTER_SCHEMA_VERSION` of the target image via `docker inspect`. If the
number rises, that appears in the confirmation dialog: *"Raises the database schema from 7
to 8."* If it stays the same, nothing appears — the normal case should not
look dangerous.

### The rollback

Trigger: `/health` does not respond within 120 s.

Write `LOXMATTER_IMAGE_TAG` back to `.env`, check out the old git ref,
`up -d --no-deps loxmatter`, wait again up to 120 s.
**Exactly once.**

**The database remains untouched.** This is the substantive
decision of this section, and it rests on a verified finding,
not an assumption. `_migrate` in `model/store.py` begins with

```python
version = int(db.execute("PRAGMA user_version").fetchone()[0])
if version >= _SCHEMA_VERSION:
    return
```

An **old** binary on a **newer** database does not refuse service —
it returns immediately and starts normally. And because
all migrations so far are `ALTER TABLE ADD COLUMN`, which SQLite only
allows nullable or with a default value, the old version continues to write
valid rows. The image rollback alone is enough to get the house running again.

Restoring the backup would be the more destructive step: it discards
everything since the backup time. You do not do that automatically at 2 a.m.
when nobody is watching. Instead it stands in the UI
as a separate, explicitly confirmed button, with clear notice
of what is lost.

The remaining case deserves to be named rather than hidden: a migration that
**rewrites** existing data instead of only adding columns — as
`_migrate_to_v3` did with exportability — leaves rewritten values under
old logic after rollback. That is exactly what the button is for, and the
notice appears only if the schema actually rose.

**If even the rollback does not come back healthy**, the updater stops.
No second try, no flutter: `failed`, `rolled_back: true`,
`healthy: false`. Then the UI probably is not reachable either — so it also writes
`/data/update/LETZTER-FEHLSCHLAG.txt` in plain text: status, last
log lines, backup path, the three commands that help by hand. Someone checking
via SSH then finds an answer instead of a puzzle.

**Finally, after `done`:** the updater checks whether its own pinned
digest is outdated, and if so triggers its own replacement in a detached manner.
After writing `done`, never before — otherwise it ends itself in the middle of writing the
state that the UI is currently reading.

## 9. The UI

**A "Version & updates" tile at the top of the System tab**, before
the checks. No separate tab, no banner across other tabs. The
version sits there even when nothing is pending.

Discarded were: a fifth "Updates" tab (the navigation paid
permanently for something used ten times a year) and a global
banner (intrusive for a state that may persist for days).

Four states, all in the same tile:

1. **Confirmation** — what happens, what stays untouched, the schema jump if any,
   and the honest message: *roughly one minute without the
   bridge, during which the Miniserver receives no values.* Afterward the
   bridge sends everything again anyway.
2. **Running** — four named steps (backed up, loaded, restarted,
   healthy) instead of a progress bar, plus the tail of the
   log.
3. **Bridge away** — same tile, fed from `state.json`, with
   running seconds counting up against 120 s. **No error banner.** The
   existing connection banner (`index.html`, `!socketConnected &&
   socketEverConnected`) gets different text for the duration instead of
   looking like a fault.
4. **Result** — success with the new version and duration, or failure with
   the finding that the old version is running again, the
   last log lines and two buttons (full log, download backup).

**Everything stays usable during updates.** Nothing is locked, no
modal covers the UI. If someone wants to commission a device
during this time, they run into an error — but the
connection banner from state 3 explains it. A minute of paternalism would be the
higher price.

**Channels** — a setting in the SQLite database like everything else,
default `stable`:

- **Stable** compares against the latest GitHub release; displays "Version
  0.3.0 available" plus release text as change notes.
- **Development** compares the running SHA against `main`; displays "14
  commits behind" plus the subject lines in between. When switching,
  a clear warning that untested intermediate versions land here.

**The check** queries the GitHub API once daily and when opening the
System tab, result cached. Can be switched off — it is an outbound
connection, and whoever runs a bridge in their home should be able to
forbid it. Without internet, a quiet note ("last checked on …"), not a red
state. A device without internet access is not an error here.

## 10. Access and security boundary

`GET /api/update/status`, `GET /api/update/check` and
`POST /api/update/apply` sit behind the existing `api_guard` —
session or token, like everything under `/api`.

**No re-entry of password.** The same session already downloads the
Fabric backup today, i.e., the irreplaceable credentials of
the entire Matter network. An update to a published version is
by comparison the smaller price; a second prompt would claim
security that does not exist elsewhere.

**The real boundary is in the updater, not in login.** Three rules,
all enforced in the sidecar:

1. `channel` is an enum. `target` must match `^v?\d+\.\d+\.\d+$` or
   `^[0-9a-f]{7,40}$`. Anything else: request rejected, nothing
   executed.
2. The **image name is not taken from the request**, but is
   hard-coded in the script: `ghcr.io/lucienkerl/loxmatter:` plus the
   checked target. The git ref is verified against the refs fetched from `origin`,
   and `origin` points to the project repository.
3. **Only forward.** In the *Stable* channel, a request cannot name a version
   below the running one (comparison by semantic version).
   In the *Development* channel there is no ordering over SHAs, so
   the corresponding check applies there: the target must be a **descendant** of
   the running commit, verified via `git merge-base --is-ancestor
   <running> <target>`. Going backward is solely the updater's own
   business, from its own bookkeeping — **without any exception via this
   route.**

Switching from *Development* back to *Stable* therefore does **not**
downgrade. It only changes what we compare against; the UI then
says clearly: *"You are running a development version newer than 0.3.0.
The next stable update comes with 0.4.0."* Whoever wants to go back
uses the console. An exception to rule 3 would have been the point at which the
statement — "at most a published, newer version" — no longer held, and
it would have fallen for a rare convenience case.

Together: even whoever takes over the bridge completely can only
**install a published, newer loxmatter version** via this.
No foreign image, no arbitrary command.

**What the Docker socket means nonetheless** belongs unabridged in the README
and the Compose comment: this container is root-equivalent on
the host. It is secured by tightness, not by permissions — no
ports, no host network, digest pinned, one single fixed
work program.

**The sidecar is removable.** Whoever deletes it from the Compose file
only loses the button.

## 11. When there is no sidecar

Existing installations and all who removed it: the bridge
recognizes this by a stale `updater_seen_at`, hides the button,
and mentions the console path instead. No dead button, no message
that sounds like a defect.

**The first jump needs the console once.** The sidecar and
switching from `build:` to `image:` must come over the old path once — `git pull && ./scripts/update.sh`. After that it sustains itself.
This belongs in the release notes of the first version that brings it,
or someone will look for a button in vain.

## 12. Text

The WebUI has been translated since i18n phase B. Every new user-visible text
goes through `i18n.t()` with `en` and `de` pairs in
`src/loxmatter/i18n/strings.yaml`, not as hard-coded German.
Affected are the tile, the four states, the confirmation dialog, the
channel toggle and the error messages of the three routes.

## 13. Testing

**The updater is a shell script, and there is a proven procedure for that.**
`tests/test_install_script.py` runs `install.sh` against a
sealed PATH of fake binaries and checks *which
commands it chooses*, not what they do. Same setup, fake
`docker`, `git`, `curl`:

- The happy path calls `compose pull` **before** `up -d`, and `up -d`
  always with `--no-deps`.
- If `/health` stays silent, the rollback sequence follows — exactly once.
- `target: "v1.0.0; rm -rf /"` is rejected, **without a single
  docker call in the log.** This is the test that makes the claim
  from section 10 more than just an assertion.
- A target below the running version is rejected.
- After `done` comes self-replacement, not after `failed`.

**Python:** atomic writing of `request.json` (the updater must never
see a half-written file), status reading without an existing file,
detection of missing sidecar via stale `updater_seen_at`,
rejection of a second request while one is running.

**Compose:** `tests/test_compose_profiles.py` gains neighbors — the new
service has no `ports:` and no `network_mode: host`.

**UI:** a browser test only proves that files are delivered.
The four states are run against invented `state.json` contents in
a throwaway harness where Alpine bindings actually run. Plus
a new `update.png` from `scripts/capture_screenshots.py`
for the README, which so far says nothing about updating.

For each of these tests: **once deliberately create the error
it should catch.** A test that only names a structure instead of checking it
has happened in this project before.

## 14. What does not belong

- **No automatic deployment.** A bridge that replaces itself unattended at night
  is not a convenience in a home, but a risk. Report yes, click no.
- **No update of matter-server and otbr** — only display (section 3).
- **No free version list, no deliberate downgrade.** A
  schema downgrade over multiple steps is not tested and does not need to be for
  this specification.
- **No update history in the UI.** `log.txt` and the
  backups suffice; a maintained chronicle would be data management for a
  purpose nobody has yet.

## 15. Implementation order

The specification is written as one effort but splits into two
stages, and the order is not arbitrary: **the second can do nothing without the
first.**

**Stage 1 — identity and delivery.** Build arguments and `ENV` in the
image, `GET /api/version`, the version in the UI, CI builds
multi-arch to GHCR, Compose switches to `image:` with `build:` behind
a profile, `scripts/update.sh` switched to `pull`, `CHANGELOG.md`,
and finally the first real tag `v0.2.0`. After that the UI
displays what is running, and the console path is already significantly faster — a
gain by itself even if stage 2 never comes.

**Stage 2 — the button.** Sidecar image, the new Compose service, the
request files, `/api/update/*`, the tile with its four states, the
rollback.

There is nothing to update as long as there is no published version
to update to. Whoever starts with stage 2 builds a button they cannot trigger,
and tests the rollback against an image that does not exist.
