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
