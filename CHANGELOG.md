# Changes

This project uses [Semantic
Versioning](https://semver.org/) starting with 0.2.0. Each published version
has a section here, and the UI shows its text as
change notes before someone installs an update — it will be read by
people who don't know the code.

## [Unreleased]

### Fixed

- **The updater's version could go missing, or go stale, on the System
  tab.** The card that says when the updater has fallen behind the bridge
  only updated when an update actually ran — so a freshly installed
  updater could sit there for minutes with no version shown at all, and an
  updater later replaced by a newer one could keep reporting the old
  version long after it was gone. The updater now reports its own version
  on every check-in, not only when it does work, so the card always
  reflects what is actually running.

## [0.3.3] — 2026-09-09

### Changed

- **The updater no longer tries to update itself.** After installing an
  update for the bridge, it used to also try refreshing its own container
  — and that was measured to fail: a container cannot correctly replace
  itself while it is the very thing running the command that would do it.
  On a real test this left the installation with no updater running at
  all, and a second, half-finished container next to it that only the
  console could clean up — the worst outcome for a feature whose whole
  point is to avoid the console. The updater now leaves itself alone and
  only ever installs updates for the bridge, exactly as reliably as
  before.

### Added

- **System → Version now says when the updater itself is out of date.**
  Since the updater no longer refreshes itself (see above), it can quietly
  fall behind the bridge it serves. The System tab now compares the two
  and, only when they actually disagree, shows the one command that brings
  the updater back in step. An installation whose updater predates this
  check simply sees nothing extra — no false alarm over a fact it cannot
  yet report.

## [0.3.2] — 2026-09-09

### Fixed

- **A successful update no longer reports itself as failed.** After an
  update finished, the updater replaced its own container — and being shut
  down for that replacement looked, from the inside, exactly like being
  interrupted. It then overwrote the finished result with a failure, so the
  System tab announced "Update failed" beside a bridge that had updated
  perfectly well. A finished update now stays finished, whatever happens to
  the updater afterwards. This showed up on the first update whose release
  also rebuilt the updater — which is most of them.

## [0.3.1] — 2026-09-09

### Fixed

- **A rollback now names the version it restored.** When an update does not
  come up healthy, the bridge returns to the version that was running before
  — and said so on screen by naming the update channel it came from, usually
  "stable", rather than the version itself. The channel is not a version, and
  this is the one message that has to be exact. The plain-text report the
  updater leaves behind was already correct; the screen now agrees with it.

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
