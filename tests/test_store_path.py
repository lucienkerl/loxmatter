"""Tests für die Herkunft des Store-Pfads (Task: Fix Important #1).

`--store-path` schlägt `LOXMATTER_STORE`, das wiederum den Standard
`~/.loxmatter/loxmatter.sqlite` schlägt (`loxmatter.cli._resolve_store_path`).
Das autouse-Fixture aus `conftest.py` sorgt schon dafür, dass kein Test die
echte Home-Datenbank berührt; hier wird die Rangfolge zusätzlich gezielt mit
eigenem `monkeypatch` erzwungen, um jeden der drei Fälle einzeln zu belegen.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from loxmatter.cli import _resolve_store_path, app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "nodes" / "ikea_grillplats_plug.json"


def _load_snapshot() -> NodeSnapshot:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _run_export(store_path: Path, out_dir: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURE),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(out_dir),
            "--store-path",
            str(store_path),
        ],
    )
    assert result.exit_code == 0, result.output


# -- Rangfolge von _resolve_store_path -----------------------------------


def test_explicit_store_path_wins_over_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOXMATTER_STORE", str(tmp_path / "env.sqlite"))
    explicit = tmp_path / "explicit.sqlite"

    assert _resolve_store_path(explicit) == explicit


def test_environment_variable_wins_over_default(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home-for-this-test"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    env_store = tmp_path / "env.sqlite"
    monkeypatch.setenv("LOXMATTER_STORE", str(env_store))

    assert _resolve_store_path(None) == env_store


def test_default_is_per_user_and_not_cwd_relative(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home-for-default"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.delenv("LOXMATTER_STORE", raising=False)

    resolved = _resolve_store_path(None)

    assert resolved == fake_home / ".loxmatter" / "loxmatter.sqlite"
    assert resolved.is_absolute()


# -- Schlüsselstabilität über die CLI --------------------------------------


def test_same_store_reused_across_exports_keeps_keys_stable(tmp_path):
    store_path = tmp_path / "shared.sqlite"

    _run_export(store_path, tmp_path / "out-1")
    _run_export(store_path, tmp_path / "out-2")

    store = Store(store_path)
    try:
        snapshot = _load_snapshot()
        device_id = store.register_device(snapshot)
        keys = sorted(s.key for s in store.signals(device_id))
    finally:
        store.close()

    # Kein zweites Gerät wurde angelegt, und die Schlüssel sind stabil —
    # das ist der eigentliche Schließungspunkt der Phase (Spec 6.2).
    text_1 = next((tmp_path / "out-1").glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    text_2 = next((tmp_path / "out-2").glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    assert text_1 == text_2
    assert keys  # es wurden ueberhaupt Signale registriert


def test_different_store_yields_different_device_id(tmp_path):
    """Zwei getrennte Datenbanken kennen sich nicht: dasselbe Geraet bekommt
    in jeder für sich eine eigene device_id vergeben — genau das Symptom,
    das eine CWD-abhaengige Store-Wahl versehentlich ausloesen wuerde
    (siehe Modul-Docstring)."""
    store_a = tmp_path / "a.sqlite"
    store_b = tmp_path / "b.sqlite"

    # store_b bekommt zuerst ein anderes Geraet, damit sein Zaehler nicht
    # zufaellig wieder bei 1 startet und das Ergebnis unabhaengig vom
    # AUTOINCREMENT-Startwert beweiskraeftig bleibt.
    other_raw = json.loads(
        (FIXTURE.parent / "ikea_bilresa_button.json").read_text(encoding="utf-8")
    )
    other_snapshot = NodeSnapshot.from_raw(other_raw["node_id"], other_raw)
    seed = Store(store_b)
    try:
        seed.register_device(other_snapshot)
    finally:
        seed.close()

    _run_export(store_a, tmp_path / "out-a")
    _run_export(store_b, tmp_path / "out-b")

    snapshot = _load_snapshot()

    a = Store(store_a)
    try:
        device_id_a = a.register_device(snapshot)
    finally:
        a.close()

    b = Store(store_b)
    try:
        device_id_b = b.register_device(snapshot)
    finally:
        b.close()

    assert device_id_a != device_id_b


def test_export_prints_which_store_was_used(tmp_path):
    store_path = tmp_path / "printed.sqlite"

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURE),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path / "out"),
            "--store-path",
            str(store_path),
        ],
    )

    assert result.exit_code == 0
    assert str(store_path) in result.stdout
