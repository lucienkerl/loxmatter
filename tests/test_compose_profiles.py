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
