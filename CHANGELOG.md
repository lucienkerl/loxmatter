# Changes

This project uses [Semantic
Versioning](https://semver.org/) starting with 0.2.0. Each published version
has a section here, and the UI shows its text as
change notes before someone installs an update — it will be read by
people who don't know the code.

## [Unreleased]

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
