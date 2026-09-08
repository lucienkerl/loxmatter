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

"""Die CI und das Dockerfile muessen sich ueber dieselben vier Argumente
einig sein.

Dies ist bewusst KEIN Test, der nur die Existenz von vier Zeilen im
Dockerfile behauptet - so einer waere wahr, sobald jemand die Namen
tippt, und bliebe wahr, wenn die CI danach andere durchreicht. Geprueft
wird der Abgleich zwischen beiden Dateien, also genau der Fehler, der
sonst erst am Image auffaellt: ein `--build-arg`, das das Dockerfile nicht
kennt, wird von Docker STILLSCHWEIGEND verworfen (nur eine Warnung), und
das Image traegt dann eine leere Version.

Der dritte Test deckt die dritte Quelle ab: die CI liest die
Schema-Version mit einem grep aus store.py. Aendert sich dort die
Schreibweise der Zeile, liefert der grep leer - und der Test faellt hier,
nicht erst beim naechsten Update auf einem fremden Pi."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from loxmatter.model.store import schema_version

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# Dieselbe Schreibweise, die der grep-Aufruf in der CI benutzt. Beide
# stehen bewusst nebeneinander: der Test ist nur dann etwas wert, wenn er
# dasselbe Muster prueft, das die CI wirklich anwendet.
SCHEMA_PATTERN = r"^_SCHEMA_VERSION = ([0-9]+)$"


def _build_push_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["image"]["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action"):
            return step
    raise AssertionError("Kein docker/build-push-action-Schritt im Job 'image'")


def test_die_ci_reicht_genau_die_argumente_durch_die_das_dockerfile_kennt() -> None:
    declared = set(
        re.findall(
            r"^ARG\s+([A-Z_][A-Z0-9_]*)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
        )
    )
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step()["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_beide_architekturen_werden_gebaut() -> None:
    # Der Pi ist der Normalfall dieses Projekts, nicht die Ausnahme. Faellt
    # arm64 weg, bemerkt das niemand, bis ein Nutzer "no matching manifest"
    # liest.
    platforms = _build_push_step()["with"]["platforms"]
    assert "linux/arm64" in platforms
    assert "linux/amd64" in platforms


def test_der_grep_der_ci_findet_die_schema_version() -> None:
    store_source = (ROOT / "src" / "loxmatter" / "model" / "store.py").read_text(encoding="utf-8")
    found = re.findall(SCHEMA_PATTERN, store_source, re.MULTILINE)
    assert len(found) == 1, "genau eine Zeile muss passen, sonst greift der grep daneben"
    assert int(found[0]) == schema_version()


def test_die_ci_benutzt_genau_dieses_muster() -> None:
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert SCHEMA_PATTERN.strip("^$") in workflow_source
