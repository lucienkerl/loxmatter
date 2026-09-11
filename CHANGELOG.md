# Changes

This project uses [Semantic
Versioning](https://semver.org/) starting with 0.2.0. Each published version
has a section here, and the UI shows its text as
change notes before someone installs an update — it will be read by
people who don't know the code.

## [Unreleased]

### Added

- **Groups.** Several devices of the same kind — usually lamps — can now be
  switched together from a single block in Loxone. The bridge takes the one
  command and passes it on to each member itself, so the Miniserver no longer
  needs a virtual output per lamp and no longer fires them one after another.
- A group takes only one kind of device, and offers only what *all* its
  members understand: put a colour lamp and a white-only lamp together and the
  group can do brightness and on/off, but not colour. If one member does not
  answer, the others are still switched and the log names the one that stayed
  dark — "five of six" is the useful answer when something is wrong.
- Groups sit as tiles beside the devices, carry their own room, and get their
  own template in the export and their own container in the project sync, just
  like a device. They only send: a group reports no state back, because six
  lamps have six brightnesses and any single number for them would be invented.
- These are the bridge's own groups, not Matter's. No Matter server this bridge
  can use offers group messaging, so nothing is created on the devices
  themselves.

## [0.3.10] — 2026-09-10

### Fixed

- While an update on the **Development** channel is building, the card
  now says so. It used to show the step for downloading, because the
  build had no state of its own — so anyone watching waited for a
  download that was not happening and wondered why their connection was
  so slow. The step reads "Building the image" on that channel and
  "Loading the image" on **Stable**, which is what each one does.

## [0.3.9] — 2026-09-10

### Changed

- On the **Development** channel the bridge is now built on the device
  instead of being downloaded. The code is already there — a development
  update is a checkout away — so waiting for a build to finish elsewhere
  was the whole cost of following that channel. Measured on a Raspberry
  Pi 4: about twenty seconds when only the source changed, about thirty
  when the dependencies did. Faster than the download it replaces.
- The **Stable** channel is unchanged. It downloads published versions,
  as it always has, and that is what an installation nobody is working
  on should be following.

## [0.3.8] — 2026-09-10

### Fixed

- Switching to the **Development** channel is no longer a one-way trip.
  Going back to **Stable** was refused every time, with a message about
  the running version not stating a version, and the only way back was
  the console. It works from the interface now, and the card says
  beforehand what the move means: the release you land on is the newest
  published one, it may be older than the development build you are
  running, and the newer changes reach a release only later.

## [0.3.7] — 2026-09-10

### Added

- The **update channel** can be chosen again in the interface, under
  Version & updates. *Stable* follows published releases, the way this
  bridge has always updated. *Development* follows every change as it
  lands — new things arrive sooner, and so do their rough edges. The
  control has existed since 0.3.0 and was hidden because choosing
  *Development* could not actually install anything; it can now.

### Fixed

- Choosing the development channel and pressing update no longer fails
  every time. Three separate reasons it could not work are gone: the
  update it offered did not name a version the bridge could install, the
  image it would have downloaded was never published under that name,
  and the check for "is this actually newer" compared against the wrong
  thing — the copy of the source on the device rather than the version
  running. That last one also stopped the check being fooled after an
  update from the console.
- The green **"Now running: …"** notice no longer stays on the System
  tab forever. It reports the update you just watched and is gone after
  a page reload. A *failed* update still says so after a reload — that is
  something still waiting to be dealt with, not news.

## [0.3.6] — 2026-09-10

### Fixed

- **An update through the web interface could leave the bridge unable to
  reach your Miniserver — and report success.** The bridge was restarted
  without the settings file that holds the Miniserver's address and the
  API token, so it came back configured with neither. Its health check
  answers regardless of whether it can reach anything, so the update
  reported itself finished and the tile turned green while the house
  went quiet. Nothing on disk was lost: only the running container was
  rebuilt without those values. If this has happened to you,
  `docker inspect loxmatter --format '{{json .Config.Cmd}}'` shows an
  empty entry after `--miniserver`, and recreating the container from
  the console restores it.

  Two things follow from the same cause, and are fixed with it: the
  version an update wrote down was never actually deployed — the
  `stable` image was reinstalled every time, whichever version you
  chose — and a rollback brought the same failed image back while
  reporting that the previous version was running again.

- **Updating from the console failed after any update run from the web
  interface**, with a message about local changes when there were none.
  The web updater leaves the checkout pinned to an exact version, and
  the console script had no way back from that. It now recovers on its
  own, whatever left it that way.

- **The interface could claim the updater had crashed while it was
  working normally**, and advise restarting it — which, during the step
  it usually appeared in, would have interrupted a healthy update. The
  updater now keeps reporting for duty during its longer steps.

- **A failed update whose rollback also failed was reported as if the
  rollback had worked.** That is the one outcome that needs a person, and
  it now says so plainly and names the file that explains what to do.

- **An update begun on a full disk could empty the settings file and
  report success.** The write is now checked before the old file is
  replaced.

- **The file written when an update is interrupted printed paths that do
  not exist on your machine**, so the commands in it failed when pasted.
  It now prints the paths as you see them.

### Changed

- The updater writes its "still working" signal five times less often
  during long steps, which matters on an SD card, and reacts faster when
  a step finishes.

## [0.3.5] — 2026-09-10

### Fixed

- **The "updater is out of date" warning could fire on a release that
  never touched the updater at all.** It compared version numbers, and
  every release stamps a version number into the updater image whether
  or not that image actually changed — so the warning could tell you to
  refresh something that was already current. It now compares the actual
  image running against what is currently published, and only speaks up
  when they genuinely differ.
- **The command shown to refresh the updater named a directory that may
  not exist.** It printed a path taken from the documentation
  (`~/loxmatter/...`), not from your own checkout — so on any checkout
  not named exactly that, the command failed at the first step. It now
  asks the updater for its own real location and prints that instead;
  if it can't be determined, the message says so rather than guessing.

## [0.3.4] — 2026-09-09

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
