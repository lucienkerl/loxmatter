# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID
from loxmatter.profiles import table
from loxmatter.profiles.table import ADMINISTRATIVE_CLUSTERS, command_slug

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_administrative_clusters_are_named():
    """Diese Cluster duerfen nie als Loxone-Ausgang erscheinen."""
    for cluster in (31, 41, 42, 48, 49, 50, 51, 56, 60, 62, 63):
        assert cluster in ADMINISTRATIVE_CLUSTERS


def test_known_command_has_a_slug():
    assert command_slug(6, 0) == "off"
    assert command_slug(6, 1) == "on"
    assert command_slug(6, 2) == "toggle"


def test_unknown_command_has_none():
    assert command_slug(6, 99) is None
    assert command_slug(64999, 0) is None


def test_plug_yields_only_the_onoff_commands():
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert {(c.cluster_id, c.command_id) for c in commands} == {(6, 0), (6, 1), (6, 2)}
    assert all(c.endpoint == 1 for c in commands)


def test_button_yields_no_commands():
    """Ein Taster ist ein Eingabegeraet."""
    assert extract_commands(load("ikea_bilresa_button.json")) == []


def test_administrative_commands_never_appear():
    """Sanity-Check an der echten Steckdosen-Fixture - kein Beweis fuer die Sperre.

    Im Normalmodus liefert `command_slug()` fuer jeden Verwaltungscluster ohnehin
    `None`, weil keiner einen `commands`-Eintrag in `clusters.yaml` hat. Dieser
    Test wuerde also auch dann noch gruen sein, wenn die ADMINISTRATIVE_CLUSTERS-
    Sperre in `extract_commands()` komplett entfernt wuerde. Der tatsaechliche
    Beweis fuer die Sperre steht in
    `test_raw_mode_adds_unknown_clusters_but_not_administrative_ones` (Rohmodus)
    und in `test_gate_blocks_administrative_cluster_even_with_table_entry` unten,
    die die Sperre unabhaengig von Fixture-Daten pinnt.
    """
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in commands)


def test_raw_mode_adds_unknown_clusters_but_not_administrative_ones():
    """Der Rohmodus erweitert die Erlaubnisliste - er hebt die Sicherheitsregel nicht auf."""
    plug = load("ikea_grillplats_plug.json")
    roh = extract_commands(plug, raw=True)
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in roh)
    assert len(roh) > len(extract_commands(plug))
    assert any(c.cluster_id == 4 for c in roh)  # Groups, unbekannt aber harmlos


def test_raw_mode_names_unknown_commands_generically():
    roh = extract_commands(load("ikea_grillplats_plug.json"), raw=True)
    unbekannt = next(c for c in roh if c.cluster_id == 4)
    assert unbekannt.slug.startswith("c4_cmd")


def test_gate_blocks_administrative_cluster_even_with_table_entry(monkeypatch):
    """Pinnt die ADMINISTRATIVE_CLUSTERS-Sperre selbst, unabhaengig von Fixture-Daten.

    Cluster 62 (OperationalCredentials) bekommt fuer diesen Test einen echten
    `commands`-Eintrag in der Profiltabelle - im Normalmodus wuerde `command_slug()`
    das Kommando also finden und `extract_commands()` wuerde es ausgeben, waere die
    ADMINISTRATIVE_CLUSTERS-Pruefung in `extract_commands()` nicht mehr da. Faellt
    dieser Test, wurde die Sperre entfernt oder umgangen - unabhaengig davon, ob
    `clusters.yaml` fuer Verwaltungscluster zufaellig leer bleibt.
    """
    assert 62 in ADMINISTRATIVE_CLUSTERS
    patched = dict(table._table())
    patched[62] = {"commands": {10: {"slug": "remove_fabric", "takes_value": False}}}
    monkeypatch.setattr(table, "_table", lambda: patched)

    path = f"1/62/{ACCEPTED_COMMAND_LIST_ID}"
    snapshot = NodeSnapshot(
        node_id=999,
        vendor_name="test",
        product_name="test",
        unique_id="test",
        attributes={path: [10]},
    )

    assert extract_commands(snapshot) == []
    assert extract_commands(snapshot, raw=True) == []
