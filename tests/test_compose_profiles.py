"""The compose file must stay usable without a Thread radio module.

`otbr` passes through a device with `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}`.
If it's missing, `docker compose up` fails ("error gathering device
information") - even for someone who only wants to connect WiFi Matter
devices. These tests pin down that `otbr` therefore sits behind a
profile and nobody outside that profile depends on it.
"""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "testhost" / "docker-compose.yml"


def _stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_otbr_is_behind_the_thread_profile() -> None:
    assert _stack()["services"]["otbr"]["profiles"] == ["thread"]


def test_no_service_outside_the_profile_depends_on_otbr() -> None:
    # Compose aborts if an active service depends on one that's disabled by a
    # profile. matter-server must therefore no longer list otbr in
    # depends_on.
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "otbr" not in service.get("depends_on", []), name


def test_only_otbr_needs_the_radio_module() -> None:
    # Anything that touches RADIO_DEVICE must sit in the profile - otherwise
    # WiFi-only operation fails again on a missing device.
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "devices" not in service, name


def test_the_bridge_runs_from_a_published_image() -> None:
    # Before 0.2.0, Compose built the image on the Pi - five to ten
    # minutes, with PyPI and memory as sources of failure in the middle of an update.
    image = _stack()["services"]["loxmatter"]["image"]
    assert image.startswith("ghcr.io/lucienkerl/loxmatter:")
    assert "${LOXMATTER_IMAGE_TAG:-stable}" in image


def test_the_build_path_remains_alongside() -> None:
    # `image:` and `build:` on the same service: `compose pull` pulls,
    # `compose build` builds, and `up` builds only if there is no local image.
    # On a host without GHCR access, that is the fallback path. A
    # profile would not work here - profiles apply to services, not
    # to individual keys of a service.
    assert _stack()["services"]["loxmatter"]["build"]["context"] == "../.."


def test_the_running_version_is_at_exactly_one_place() -> None:
    # Stage 2 relies on this: the fallback writes ONE line back to .env.
    # If the tag appears at a second place, only the
    # one is rolled back and the other is not.
    source = COMPOSE.read_text(encoding="utf-8")
    assert source.count("LOXMATTER_IMAGE_TAG") == 1
