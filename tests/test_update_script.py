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

"""Verhaltenstests fuer scripts/update.sh.

Dasselbe Verfahren wie in `test_install_script.py`: das Skript laeuft
gegen einen versiegelten PATH aus gefaelschten Binaries, und geprueft
wird, WELCHE Befehle es waehlt - nicht, was sie bewirken. Ein echtes
`docker compose pull` waere weder in der CI noch auf einem
Entwicklungsrechner zumutbar.

Der wichtigste Test unten ist `test_ohne_build_wird_nie_gebaut`: das
Skript baute vor 0.2.0 immer, und der ganze Sinn dieser Aenderung ist,
dass ein Update auf einem Pi keine fuenf bis zehn Minuten mehr dauert.
Ein zurueckgerutschtes `compose build` faellt sonst niemandem auf - es
funktioniert ja, nur langsam."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update.sh"

SYSTEM_TOOLS = (
    "bash",
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "printf",
    "mkdir",
    "rm",
    "sleep",
    "date",
    "ls",
    "xargs",
    "tail",
    "seq",
    "hostname",
    "dirname",
)


@pytest.fixture
def sealed(tmp_path):
    """Ein PATH aus zwei Verzeichnissen: gefaelschte Werkzeuge und die
    echten, die das Skript legitim braucht. Jeder Stub protokolliert seinen
    Aufruf nach $STUB_LOG und endet erfolgreich - `curl` gibt zusaetzlich
    eine Gesundheitsantwort aus, damit die Warteschleife sofort
    weiterlaeuft statt 120 Sekunden zu warten."""
    bindir = tmp_path / "bin"
    sysdir = tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8"
        )
        path.chmod(0o755)

    # "docker run ... tar czf X ..." (die Datenbanksicherung) muss die
    # Zieldatei wirklich anlegen: das anschliessende `ls store-*.tgz` im
    # Skript laeuft unter `pipefail`, und ohne Treffer scheitert `ls` mit
    # Exitstatus 1 - das Skript braeche ab, bevor es je zum Ziehen oder
    # Bauen kaeme. Ein Stub, der nur protokolliert, waere hier zu duenn.
    # Der Zielpfad steckt im Container hinter `/backup/...` - der Stub
    # sucht das `-v HOSTDIR:/backup` und schreibt dorthin zurueck.
    stub(
        "docker",
        'if [ "$1" = "run" ]; then\n'
        '  hostdir=""\n'
        '  for a in "$@"; do\n'
        '    case "$a" in *:/backup) hostdir="${a%:/backup}" ;; esac\n'
        "  done\n"
        '  prev=""\n'
        '  for a in "$@"; do\n'
        '    if [ "$prev" = "czf" ]; then\n'
        '      case "$a" in /backup/*) a="$hostdir/${a#/backup/}" ;; esac\n'
        '      : > "$a"\n'
        "    fi\n"
        '    prev="$a"\n'
        "  done\n"
        "fi\n",
    )
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(
            ["which", tool], capture_output=True, text=True, check=False
        ).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(*args: str):
        result = subprocess.run(
            [str(SCRIPT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{sysdir}",
                "STUB_LOG": str(log),
                "HOME": str(tmp_path),
            },
            check=False,
        )
        return result, log.read_text(encoding="utf-8") if log.exists() else ""

    run.bindir = bindir
    return run


def test_es_zieht_das_image_statt_es_zu_bauen(sealed):
    _, calls = sealed("--no-pull")
    assert "compose pull loxmatter" in calls


def test_ohne_build_wird_nie_gebaut(sealed):
    _, calls = sealed("--no-pull")
    assert "compose build" not in calls


def test_mit_build_wird_gebaut_und_nicht_gezogen(sealed):
    _, calls = sealed("--no-pull", "--build")
    assert "compose build loxmatter" in calls
    assert "compose pull loxmatter" not in calls


def test_das_ziehen_kommt_vor_dem_neustart(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("compose pull") < calls.index("compose up")


def test_der_neustart_laesst_die_nachbardienste_in_ruhe(sealed):
    # --no-deps: OTBRs Thread-Zustand haengt an einem Volume, und ein
    # Neustart des Thread-Netzes gehoert nicht zu einem Update.
    _, calls = sealed("--no-pull")
    up_line = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up_line


def test_die_datenbank_wird_vor_allem_anderen_gesichert(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("volume inspect") < calls.index("compose pull")


def test_no_cache_ohne_build_wird_abgewiesen(sealed):
    result, _ = sealed("--no-pull", "--no-cache")
    assert result.returncode != 0
    assert "--build" in result.stderr


# ------------------------------------------------ Boundary-crossing fix --
# Critical 4 from a whole-branch review: the documented migration path
# for an existing installation ("git pull && ./scripts/update.sh",
# README.md/CHANGELOG.md) only ever touched $SERVICE (loxmatter) - it
# never brought up the stack, so loxmatter-updater was never created on
# an installation that predates it. See
# .superpowers/sdd/final-fix-boundary-report.md for the full write-up.


def test_der_updater_sidecar_wird_erstellt(sealed):
    _, calls = sealed("--no-pull")
    assert "compose up -d --no-deps loxmatter-updater" in calls


def test_das_erstellen_des_sidecars_laesst_die_nachbardienste_in_ruhe(sealed):
    # --no-deps here too: matter-server and OTBR must not restart just
    # because the sidecar is being brought up for the first time - same
    # reasoning as the bridge's own restart (see the block comment at the
    # top of this file), and loxmatter-updater itself carries no
    # `depends_on` in the compose file, so this is defensive rather than
    # load-bearing today, but it must stay true regardless of that.
    _, calls = sealed("--no-pull")
    sidecar_line = next(
        line for line in calls.splitlines() if "compose up" in line and "loxmatter-updater" in line
    )
    assert "--no-deps" in sidecar_line


def test_ein_fehlschlag_beim_sidecar_bricht_das_skript_nicht_ab(sealed):
    # Best-effort, deliberately: by the time this step runs, the bridge
    # itself has already been updated and confirmed healthy. A registry
    # hiccup fetching the SIDECAR's own image must not turn an otherwise-
    # successful bridge update into a reported failure.
    docker_path = sealed.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *loxmatter-updater*) exit 1 ;;\n"
        "  run*)\n"
        '    hostdir=""\n'
        '    for a in "$@"; do\n'
        '      case "$a" in *:/backup) hostdir="${a%:/backup}" ;; esac\n'
        "    done\n"
        '    prev=""\n'
        '    for a in "$@"; do\n'
        '      if [ "$prev" = "czf" ]; then\n'
        '        case "$a" in /backup/*) a="$hostdir/${a#/backup/}" ;; esac\n'
        '        : > "$a"\n'
        "      fi\n"
        '      prev="$a"\n'
        "    done\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    result, calls = sealed("--no-pull")
    assert result.returncode == 0, result.stderr
    assert "compose up -d --no-deps loxmatter-updater" in calls
    assert "Could not start loxmatter-updater" in result.stdout
