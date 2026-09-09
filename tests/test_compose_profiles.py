"""Die Compose-Datei muss ohne Thread-Funkmodul brauchbar bleiben.

`otbr` reicht mit `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}` ein Geraet
durch. Fehlt es, scheitert `docker compose up` ("error gathering device
information") - auch bei jemandem, der ausschliesslich WLAN-Matter-Geraete
anbinden will. Diese Tests halten fest, dass `otbr` deshalb hinter einem
Profil steht und niemand ausserhalb dieses Profils davon abhaengt.
"""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "testhost" / "docker-compose.yml"


def _stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_otbr_steht_hinter_dem_thread_profil() -> None:
    assert _stack()["services"]["otbr"]["profiles"] == ["thread"]


def test_kein_dienst_ausserhalb_des_profils_haengt_an_otbr() -> None:
    # Compose bricht ab, wenn ein aktiver Dienst von einem profil-
    # deaktivierten abhaengt. matter-server darf otbr also nicht mehr
    # in depends_on fuehren.
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "otbr" not in service.get("depends_on", []), name


def test_nur_otbr_braucht_das_funkmodul() -> None:
    # Alles, was RADIO_DEVICE beruehrt, muss im Profil liegen - sonst
    # scheitert der WiFi-Betrieb doch wieder an einem fehlenden Geraet.
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


def test_the_updater_has_no_network_open_to_the_outside() -> None:
    # The load-bearing safeguard for this container: it holds the Docker
    # socket and is thereby root-equivalent on the host. Unlike the three
    # other services, it therefore does NOT sit on the host network and
    # publishes no port.
    updater = _stack()["services"]["loxmatter-updater"]
    assert "ports" not in updater
    assert updater.get("network_mode") != "host"


def test_only_the_updater_has_the_docker_socket() -> None:
    for name, service in _stack()["services"].items():
        socket = any("docker.sock" in str(v) for v in service.get("volumes", []))
        assert socket == (name == "loxmatter-updater"), name


def test_the_updater_sees_the_same_database_as_the_bridge() -> None:
    # Communication runs through files in exactly this volume.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any(str(v).startswith("loxmatter-store:") for v in updater["volumes"])


def test_the_updater_reaches_the_host_health_route() -> None:
    # It sits on Compose's default network; 127.0.0.1 there would be itself.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any("host-gateway" in str(h) for h in updater["extra_hosts"])


def test_the_updater_is_pinned() -> None:
    image = _stack()["services"]["loxmatter-updater"]["image"]
    assert "@sha256:" in image or ":latest" not in image
