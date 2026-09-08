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


def test_die_bruecke_laeuft_aus_einem_veroeffentlichten_image() -> None:
    # Vor 0.2.0 baute Compose das Image auf dem Pi - fuenf bis zehn
    # Minuten, mit PyPI und Speicher als Fehlerquellen mitten im Update.
    image = _stack()["services"]["loxmatter"]["image"]
    assert image.startswith("ghcr.io/lucienkerl/loxmatter:")
    assert "${LOXMATTER_IMAGE_TAG:-stable}" in image


def test_der_bauweg_bleibt_daneben_bestehen() -> None:
    # `image:` und `build:` am selben Dienst: `compose pull` zieht,
    # `compose build` baut, und `up` baut nur, wenn lokal kein Image liegt.
    # Auf einem Host ohne GHCR-Zugang ist das die Rueckfallebene. Ein
    # Profil waere hier nicht moeglich - Profile gelten fuer Dienste, nicht
    # fuer einzelne Schluessel eines Dienstes.
    assert _stack()["services"]["loxmatter"]["build"]["context"] == "../.."


def test_die_laufende_version_steht_an_genau_einer_stelle() -> None:
    # Stufe 2 setzt darauf auf: der Rueckfall schreibt EINE Zeile in .env
    # zurueck. Taucht der Tag an einer zweiten Stelle auf, faellt nur die
    # eine zurueck und die andere nicht.
    source = COMPOSE.read_text(encoding="utf-8")
    assert source.count("LOXMATTER_IMAGE_TAG") == 1
