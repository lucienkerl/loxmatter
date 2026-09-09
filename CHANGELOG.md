# Changes

This project uses [Semantic
Versioning](https://semver.org/) starting with 0.2.0. Each published version
has a section here, and the UI shows its text as
change notes before someone installs an update — it will be read by
people who don't know the code.

## [Unreleased]

## [0.3.0] — 2026-09-09

### Added

- **System → Version can now install updates by itself.** It shows what
  is running, tells you when a newer published version is available, and
  a button installs it after you confirm — a backup of the signal
  database is taken first. The bridge is unreachable for about a minute
  while it restarts; the page says so and reconnects on its own once it
  answers again. If the new version does not report itself healthy within
  two minutes, it is rolled back automatically and the version you had
  keeps running — this happens once per attempt, not repeatedly, and the
  database is left as it was, not rolled back with it.
- This needs the new `loxmatter-updater` service from the compose file.
  An installation from before this release does not have it yet and
  needs the console once to bring it in: `git pull && ./scripts/update.sh`.
  Afterwards, updates work from the browser like any newer installation.
  Without that service — or after removing it — the System tab still
  names the running version and points back to the console path.
- **Device cards show when a device was last heard from**, as a coarse age
  that never counts seconds. A device that has never reported says so.

### Changed

- The bridge now talks to matter.js (`matterjs-server`) through
  `matter-python-client`, replacing `python-matter-server`. **This has not
  been run on real hardware yet.** It does not reach an existing
  installation by itself: every update path recreates only the bridge and
  the updater, never matter-server, so the change sits in your checkout
  until you deliberately bring the whole stack up with
  `docker compose up -d`. Read `deploy/testhost/docker-compose.yml` before
  you do — the new container runs unprivileged and needs a one-time
  ownership change on its data directory.

### Fixed

- **The bridge notices when its link to matter-server drops and rebuilds
  it.** Until now a matter-server restart left the bridge quietly inert
  while diagnostics still reported a healthy connection. The heartbeat to
  the Miniserver now falls silent while there is no Matter link, instead
  of pretending everything is fine.

## [0.2.0] — 2026-09-08

### Added

- The UI shows which version is running in the System tab.
- Ready-made images are available at `ghcr.io/lucienkerl/loxmatter`
  (`arm64` and `amd64`). An update now downloads such an image instead of
  building it on the Raspberry Pi itself — the local build took
  five to ten minutes, and downloading a ready-made image
  should speed that up significantly. This hasn't been measured yet: there is
  no device on which an update has taken this path yet.

### Changed

- `scripts/update.sh` pulls the image instead of building locally. `--build`
  restores the old behavior.
- `install.sh` now also starts the stack without `--build`: `docker
  compose up -d` pulls the published image. A `--build` would have
  tagged the result with the name `ghcr.io/lucienkerl/loxmatter:stable`,
  even if built locally - a fresh installation would then have had a
  local image that reported itself as `dev`.
- The stack runs from a published image. **This one
  change needs the console once:** `git pull &&
  ./scripts/update.sh` on the device running the bridge.
