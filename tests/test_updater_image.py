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
"""Das Beiwagen-Image bringt genau die Werkzeuge mit, die das Skript
benutzt.

Der Fehler, gegen den das schuetzt, ist unangenehm leise: fehlt `jq` im
Image, laeuft der Beiwagen an, schreibt nie einen brauchbaren Zustand, und
die Oberflaeche zeigt einen Knopf, der nichts tut. Ein Abgleich zwischen
den `apk add`-Zeilen und den im Skript aufgerufenen Befehlen faellt hier,
bevor jemand ihn auf einem Pi entdeckt."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy" / "updater" / "Dockerfile"

# Was das Skript aus Task 2/3/4 aufruft und was Alpine NICHT von sich aus
# mitbringt. `sh`, `mv`, `printf` stehen bewusst nicht dabei - die sind
# busybox-eigen und koennen nicht fehlen.
BENOETIGT = ("docker-cli", "docker-cli-compose", "git", "curl", "jq", "coreutils", "tar")


def test_das_image_bringt_jedes_benutzte_werkzeug_mit() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for paket in BENOETIGT:
        assert re.search(rf"\b{re.escape(paket)}\b", source), paket


def test_die_basis_ist_gepinnt() -> None:
    # Ein `FROM alpine:latest` machte aus jedem Neubau des Beiwagens eine
    # Ueberraschung - ausgerechnet bei dem Container, der root-gleichwertig
    # auf dem Host steht.
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM alpine:3\.\d+", source, re.MULTILINE)
    assert "alpine:latest" not in source
