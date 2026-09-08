# Changelog

From 0.2.0 onwards this project uses [Semantic
Versioning](https://semver.org/). Every released version gets a section
here, and the web interface shows that section as the release notes before
anyone installs an update — so it is read by people who do not know the
code, at the moment they decide whether to update. Write it for them.

## [Unreleased]

## [0.2.0] — 2026-09-08

### Added

- The System tab shows which version is running.
- Prebuilt images are available at `ghcr.io/lucienkerl/loxmatter` for
  `arm64` and `amd64`. An update now downloads one of those instead of
  building it on the Raspberry Pi itself — the local build has been taking
  five to ten minutes, and downloading a finished image should cut that
  down considerably. This has not been measured yet: so far no machine has
  performed an update this way.

### Changed

- `scripts/update.sh` pulls the image instead of building locally.
  `--build` restores the old behaviour, for development and for hosts that
  cannot reach the registry.
- `install.sh` now also starts the stack without `--build`: `docker compose
  up -d` pulls the published image. A `--build` would have tagged the
  result as `ghcr.io/lucienkerl/loxmatter:stable` even though it was built
  locally — a fresh installation would then have carried a local image that
  reports itself as `dev`.
- The stack runs from a published image. **This one switchover needs the
  console once:** `git pull && ./scripts/update.sh` on the machine the
  bridge runs on.
