# One-Liner-Installskript — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein `install.sh` im Wurzelverzeichnis richtet den Docker-Stack aus `deploy/testhost/` mit einem einzigen Befehl ein — wahlweise mit Thread-Border-Router oder als reiner WiFi/Ethernet-Betrieb.

**Architecture:** Ein einzelnes POSIX-`sh`-Skript in sieben Phasen: prüfen (ohne zu verändern), fehlende Pakete und Docker nachinstallieren, klonen, `.env` schreiben, Stack starten, Zustand prüfen, berichten. `otbr` wird zu einem Compose-Profil, damit der Stack ohne Funkmodul läuft. Getestet wird mit einem versiegelten `PATH` aus gefälschten Binaries, die ihre Aufrufe protokollieren.

**Tech Stack:** POSIX `sh` (dash-kompatibel), Docker Compose (Profile), pytest mit `subprocess`, `shellcheck`, PyYAML (schon Abhängigkeit).

**Entwurf:** [docs/superpowers/specs/2026-09-05-install-oneliner-design.md](../specs/2026-09-05-install-oneliner-design.md)

## Global Constraints

- `install.sh` ist **reines POSIX `sh`**. Kein `[[`, keine Arrays, kein `local`, kein `set -o pipefail`, kein `source`. Prüfung: `shellcheck -s sh install.sh` muss ohne Befund durchlaufen.
- `install.sh` ist **durchgehend englisch**, auch die Kommentare. Alle anderen Dateien behalten die Projektkonvention: deutsche Kommentare, deutsche Prosa.
- Jede neue Shell-Datei trägt denselben GPL-3.0-or-later-Kopf wie `scripts/update.sh` (dort wortgleich abschreiben, nur die englische Beschreibungszeile darunter unterscheidet sich).
- `set -eu` steht ganz oben. **Niemals** eine Funktion mit `[ ... ] && befehl` enden lassen — schlägt der Test fehl, ist der Rückgabewert der Funktion ungleich 0 und `set -e` beendet das ganze Skript. Immer `if ... then ... fi`.
- Alles steht in Funktionen; die **letzte Zeile** der Datei ist `main "$@"`. Ein abgeschnittener Download definiert dann nur Funktionen und tut nichts.
- Python-Dateien halten `line-length = 100` (ruff) und müssen `uv run ruff check .` sowie `uv run ruff format --check .` bestehen. `tests/` liegt nicht unter mypy (`files = ["src", "scripts"]`), Typannotationen sind dort also freiwillig.
- Commit-Nachrichten deutsch, im Stil der bestehenden Historie (`fix(matter): …`, `docs(otbr): …`), mit `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` als letzter Zeile.
- Konstanten, die mehrfach vorkommen: Repository `https://github.com/lucienkerl/loxmatter.git`, Docker-Installer `https://get.docker.com`, Standardverzeichnis `$HOME/loxmatter`.

---

## Dateiübersicht

| Datei | Verantwortung |
|---|---|
| `install.sh` (neu) | Der gesamte Installationsablauf. Eine Datei, weil sie über `curl` als eine Datei ausgeliefert wird — eine Aufteilung wäre hier nicht möglich, sondern schädlich. |
| `tests/test_install_script.py` (neu) | Testgerüst (versiegelter `PATH`, Stub-Binaries) und alle Verhaltenstests des Skripts. |
| `tests/test_compose_profiles.py` (neu) | Prüft die Compose-Datei als Datenstruktur: Profil an `otbr`, kein `depends_on` auf `otbr`. |
| `deploy/testhost/docker-compose.yml` | `otbr` bekommt `profiles: ["thread"]`; `matter-server` verliert `depends_on`. |
| `deploy/testhost/.env.example` | Neue Variable `COMPOSE_PROFILES` mit Erklärung. |
| `deploy/testhost/README.md` | Abschnitt „WiFi/Ethernet-only". |
| `scripts/otbr-watchdog.sh` | Wächter davor: existiert kein `otbr`-Container, still beenden. |
| `.github/workflows/ci.yml` | Ein `shellcheck`-Schritt. |

Die Reihenfolge der Aufgaben ist bindend: Aufgabe 1 legt die Compose-Grundlage, auf die `install.sh` ab Aufgabe 6 schreibt.

**Keine Aufgabe für `README.md`.** Der Quickstart-Wortlaut steht fertig in
Abschnitt 10 des Entwurfs und wird von der README-Produktseiten-Session
übernommen; diese Arbeit fasst die README nicht an. Wer das hier ausführt,
sucht also nicht nach einer fehlenden Dokumentationsaufgabe.

---

### Task 1: `otbr` wird ein Compose-Profil

**Files:**
- Modify: `deploy/testhost/docker-compose.yml`
- Modify: `deploy/testhost/.env.example`
- Modify: `deploy/testhost/README.md`
- Modify: `scripts/otbr-watchdog.sh`
- Test: `tests/test_compose_profiles.py`

**Interfaces:**
- Consumes: nichts.
- Produces: Der Compose-Dienst `otbr` läuft nur, wenn das Profil `thread` aktiv ist. Aktiviert wird es über `COMPOSE_PROFILES=thread` in `deploy/testhost/.env`. Ab Aufgabe 6 schreibt `install.sh` genau diesen Schlüssel.

- [ ] **Step 1: Write the failing test**

Neue Datei `tests/test_compose_profiles.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: `test_otbr_steht_hinter_dem_thread_profil` FAILS mit `KeyError: 'profiles'`, `test_kein_dienst_ausserhalb_des_profils_haengt_an_otbr` FAILS mit `AssertionError: matter-server`.

- [ ] **Step 3: Compose-Datei ändern**

In `deploy/testhost/docker-compose.yml`, im Dienst `otbr` direkt unter `container_name: otbr` einfügen:

```yaml
    # Nur mit aktivem Profil "thread" (2026-09-05). Dieser Dienst reicht mit
    # `devices:` unten ein echtes Geraet durch - fehlt das Funkmodul, scheitert
    # `docker compose up` fuer den GESAMTEN Stack, auch fuer jemanden, der nur
    # WLAN-Matter-Geraete anbinden will. Hinter einem Profil bleibt der Rest
    # startbar; eingeschaltet wird es ueber COMPOSE_PROFILES in der .env, das
    # Compose von sich aus liest - deshalb braucht kein spaeterer Aufruf und
    # kein Skript ein `--profile` mitzuschleppen.
    profiles: ["thread"]
```

Im Dienst `matter-server` diese beiden Zeilen **löschen**:

```yaml
    depends_on:
      - otbr
```

und an ihrer Stelle den Grund festhalten:

```yaml
    # Kein `depends_on: otbr` mehr (2026-09-05): Compose bricht ab, wenn ein
    # aktiver Dienst von einem profil-deaktivierten abhaengt. Inhaltlich
    # folgenlos - `depends_on` steuert die Startreihenfolge, nicht die
    # Bereitschaft, und matter-server braucht den Border Router beim Start
    # nicht: Thread-Kommissionierung laeuft spaeter ueber das Host-Netz, in
    # dem otbr mit `network_mode: host` ohnehin steht.
```

Der Dienst `loxmatter` behält sein `depends_on: - matter-server` unverändert — `matter-server` hat kein Profil.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: 3 passed.

- [ ] **Step 5: `.env.example` ergänzen**

Ganz oben in `deploy/testhost/.env.example`, **vor** `RADIO_DEVICE`, einfügen:

```
# Betriebsart. Leer = nur WLAN- und Ethernet-Matter-Geraete; "thread" nimmt
# zusaetzlich den OpenThread Border Router (Dienst `otbr`) dazu.
#
# Compose liest diese Variable von sich aus aus der .env - deshalb steht die
# Betriebsart hier und nicht als `--profile`-Argument an jedem Aufruf. Ein
# spaeteres `docker compose up`, `scripts/update.sh` und der Watchdog treffen
# damit von allein die richtige Auswahl.
#
# Nachruesten: Funkmodul stecken, hier `thread` eintragen, RADIO_DEVICE unten
# auf den richtigen Pfad setzen, `docker compose up -d` erneut.
COMPOSE_PROFILES=thread

# Nur bei COMPOSE_PROFILES=thread noetig - ohne Profil wird der otbr-Dienst
# gar nicht erzeugt und dieser Pfad nie geoeffnet.
```

- [ ] **Step 6: Watchdog absichern**

In `scripts/otbr-watchdog.sh`, direkt nach der Zuweisung von `STAMP` (vor `thread_is_up()`), einfügen:

```bash
# Im WiFi/Ethernet-only-Betrieb (COMPOSE_PROFILES ohne "thread", siehe
# deploy/testhost/.env) gibt es diesen Dienst gar nicht. Ohne diese Bremse
# faende der Waechter nie eine Thread-Schnittstelle, versuchte alle fuenf
# Minuten einen Neustart und schriebe jedes Mal einen Fehlschlag ins Log -
# aus einem Aufpasser wuerde eine Lawine.
if ! docker ps -a --format '{{.Names}}' | grep -qx "$SERVICE"; then
  exit 0
fi
```

- [ ] **Step 7: deploy-README ergänzen**

In `deploy/testhost/README.md` einen neuen Abschnitt direkt **vor** `## Aktualisieren` einfügen:

```markdown
## WiFi/Ethernet-only (ohne Thread-Funkmodul)

Der `otbr`-Dienst steht seit dem 5. September 2026 hinter dem Compose-Profil
`thread`. Wer nur WLAN- oder Ethernet-Matter-Geräte anbinden will, lässt
`COMPOSE_PROFILES` in der `.env` leer — dann wird der Border Router gar nicht
erzeugt, und `RADIO_DEVICE`, `RADIO_BAUDRATE` und `BACKBONE_IF` bleiben
wirkungslos.

**Warum das nötig war:** `otbr` reicht mit `devices: -
${RADIO_DEVICE}:${RADIO_DEVICE}` ein echtes Gerät durch. Steckt kein Funkmodul,
scheitert `docker compose up` mit „error gathering device information" — und
zwar für den *gesamten* Stack, auch für die beiden Dienste, die das Modul nie
gebraucht hätten.

**BLE bleibt in beiden Betriebsarten nötig.** Auch ein WLAN-Matter-Gerät wird
über Bluetooth eingelernt; `BLUETOOTH_ADAPTER` und der rfkill-Abschnitt weiter
unten gelten unverändert.

**Nachrüsten:** Funkmodul stecken, in der `.env` `COMPOSE_PROFILES=thread`
setzen und `RADIO_DEVICE` auf den richtigen Pfad, dann `docker compose up -d`.
Der `start-stop-daemon`-Workaround weiter unten wird ab dann wieder gebraucht.
```

- [ ] **Step 8: Alles laufen lassen**

Run: `uv run pytest tests/test_compose_profiles.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 3 passed, `All checks passed!`, `N files already formatted`.

- [ ] **Step 9: Commit**

```bash
git add deploy/testhost/docker-compose.yml deploy/testhost/.env.example \
        deploy/testhost/README.md scripts/otbr-watchdog.sh tests/test_compose_profiles.py
git commit -m "feat(deploy): otbr hinter ein Compose-Profil legen

Ohne Funkmodul scheiterte der gesamte Stack an devices: \${RADIO_DEVICE}
am otbr-Dienst, auch fuer Installationen, die nur WLAN-Matter-Geraete
anbinden wollen. otbr laeuft jetzt nur mit aktivem Profil \"thread\", das
ueber COMPOSE_PROFILES in der .env eingeschaltet wird - Compose liest die
Variable von sich aus, deshalb braucht kein Aufruf ein --profile.

matter-server verliert dabei sein depends_on auf otbr: Compose bricht ab,
wenn ein aktiver Dienst von einem profil-deaktivierten abhaengt. Der
Watchdog beendet sich still, wenn es keinen otbr-Container gibt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Skelett von `install.sh` — Optionen, Ausgabe, Trap, Plattformprüfung

**Files:**
- Create: `install.sh`
- Create: `tests/test_install_script.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nichts.
- Produces: die Shell-Funktionen `say`, `note`, `warn`, `die`, `step`, `state_summary`, `usage`, `parse_args`, `check_platform`, `main`; die Variablen `REPO_URL`, `DOCKER_INSTALL_URL`, `DRY_RUN`, `TARGET_DIR`, `STEP`, `CHECKOUT_EXISTED`, `STACK_STARTED`. Für die Tests: die pytest-Fixture `installer`, die ein Objekt mit `returncode`, `output`, `calls`, `called(prefix)`, `home` liefert.
- Konvention für alle folgenden Aufgaben: `die` beendet mit **Exit-Code 2** (erwarteter, sauber gemeldeter Abbruch). Jeder andere Code ungleich 0 ist ein unerwarteter Fehler, den der `EXIT`-Trap mit Schritt und Zustand meldet.

- [ ] **Step 1: Testgerüst und die ersten Tests schreiben**

Neue Datei `tests/test_install_script.py`:

```python
"""Verhaltenstests fuer install.sh.

Das Skript veraendert fremde Rechner: es installiert Pakete, ruft sudo,
klont und startet Container. Geprueft wird deshalb, WELCHE Befehle es
waehlt - nicht, was sie bewirken. Dazu laeuft es gegen einen versiegelten
PATH aus zwei Verzeichnissen:

  bin/  gefaelschte Binaries (docker, git, sudo, apt-get, curl, uname, ip,
        hostname, usermod). Jedes protokolliert seinen Aufruf nach $STUB_LOG
        und endet erfolgreich. Ein Werkzeug "fehlt" schlicht dadurch, dass
        sein Stub nicht angelegt wird - deshalb darf im PATH nichts liegen,
        was es auf dem Testrechner echt gibt.
  sys/  Symlinks auf genau die echten Werkzeuge, die das Skript legitim
        braucht (sh, awk, sed, grep, ...). `id` steht bewusst NICHT dabei,
        sondern ist ein Stub - sonst haenge das Verhalten davon ab, ob die
        Testsuite gerade als root laeuft.

Die Stubs liegen zusaetzlich unveraendert in templates/. Der apt-get-Stub
kopiert von dort nach bin/, und der curl-Stub gibt fuer get.docker.com ein
Skript aus, das dasselbe fuer `docker` tut. Damit verhaelt sich ein Lauf, in
dem ein Werkzeug fehlt und nachinstalliert wird, wie auf einem echten Host:
danach ist es da.

Die Kindprozesse laufen mit start_new_session=True, also ohne
kontrollierendes Terminal. Damit schlaegt jedes Oeffnen von /dev/tty fehl
und der nicht-interaktive Zweig ist deterministisch - unabhaengig davon, ob
pytest gerade in einem Terminal oder in der CI laeuft.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install.sh"

# Echte Werkzeuge, die das Skript benutzen darf. Alles andere kommt aus
# einem Stub oder gilt als nicht installiert.
SYSTEM_TOOLS = (
    "sh", "cat", "grep", "sed", "awk", "tr", "od", "mkdir", "rm", "mv",
    "sleep", "chmod", "cp", "printf", "true", "false",
)

_UNAME = """case "${1-}" in
  -m) echo x86_64 ;;
  *) echo Linux ;;
esac
"""

_IP = 'echo "default via 10.0.1.1 dev eth0 proto dhcp src 10.0.1.56"\n'

_HOSTNAME = 'echo "10.0.1.56"\n'

# Nicht als echtes Werkzeug: sonst haengt jeder root-Test davon ab, als wer
# die Testsuite laeuft.
_ID = "echo 1000\n"

_OPENSSL = """case "${1-} ${2-}" in
  "rand -hex") echo "aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66" ;;
esac
exit 0
"""

# Holt die "installierten" Pakete aus templates/ nach bin/ - danach sind sie
# wirklich da, so wie nach einem echten apt-get.
_APT_GET = """for pkg in "$@"; do
  if [ -f "$STUB_TEMPLATES/$pkg" ]; then
    cp "$STUB_TEMPLATES/$pkg" "$STUB_BIN/$pkg"
    chmod 755 "$STUB_BIN/$pkg"
  fi
done
exit 0
"""

_SUDO = """cmd=$1
shift
exec "$cmd" "$@"
"""

_DOCKER = """if [ "${1-}" = "compose" ] && [ "${2-}" = "version" ]; then
  echo "Docker Compose version v2.30.0"
fi
exit 0
"""

# Legt beim `clone` ein Checkout an, das die ECHTEN Stack-Dateien enthaelt -
# so laufen die Tests gegen die tatsaechliche docker-compose.yml und .env.example.
_GIT = """if [ "${1-}" = "-C" ]; then shift 2; fi
case "${1-}" in
  clone)
    for a in "$@"; do target="$a"; done
    mkdir -p "$target/deploy/testhost" "$target/scripts"
    : > "$target/Dockerfile"
    cp "$LOXMATTER_REPO/deploy/testhost/docker-compose.yml" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/deploy/testhost/.env.example" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/scripts/update.sh" "$target/scripts/"
    ;;
  rev-list) echo "${FAKE_BEHIND-0}" ;;
esac
exit 0
"""

# Die get.docker.com-Ausgabe wird vom Skript in `sh` gepipet - sie legt
# deshalb den docker-Stub an, statt nur erfolgreich zu sein.
_CURL = """for a in "$@"; do last="$a"; done
case "$last" in
  *get.docker.com*)
    echo "cp '$STUB_TEMPLATES/docker' '$STUB_BIN/docker' && chmod 755 '$STUB_BIN/docker'"
    ;;
  *health*) echo '{"status":"ok"}' ;;
esac
exit 0
"""

DEFAULT_STUBS = {
    "uname": _UNAME,
    "ip": _IP,
    "hostname": _HOSTNAME,
    "sudo": _SUDO,
    "docker": _DOCKER,
    "git": _GIT,
    "curl": _CURL,
    "openssl": _OPENSSL,
    "id": _ID,
    "apt-get": _APT_GET,
    "usermod": "exit 0\n",
}


class Result:
    def __init__(self, proc, home, log):
        self.returncode = proc.returncode
        self.output = proc.stdout + proc.stderr
        self.home = home
        self._log = log

    @property
    def calls(self):
        if not self._log.exists():
            return []
        return [line for line in self._log.read_text().splitlines() if line]

    def called(self, prefix):
        return any(call.startswith(prefix) for call in self.calls)

    @property
    def env_file(self):
        return self.home / "loxmatter" / "deploy" / "testhost" / ".env"


@pytest.fixture
def installer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    log = tmp_path / "stub.log"

    for tool in SYSTEM_TOOLS:
        real = shutil.which(tool, path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real is not None:
            (sysdir / tool).symlink_to(real)

    def run(*args, env=None, omit=(), stubs=None):
        active = dict(DEFAULT_STUBS)
        active.update(stubs or {})
        for name in omit:
            active.pop(name, None)
        for stale in list(bindir.iterdir()):
            stale.unlink()
        for stale in list(templates.iterdir()):
            stale.unlink()

        def write(directory, name, body):
            path = directory / name
            path.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"{name} $*\" >> \"$STUB_LOG\"\n"
                f"{body}"
            )
            path.chmod(0o755)

        # templates/ kennt alles, bin/ nur das, was auf diesem Host "da" ist.
        for name, body in dict(DEFAULT_STUBS, **(stubs or {})).items():
            write(templates, name, body)
        for name, body in active.items():
            write(bindir, name, body)

        full_env = {
            "PATH": f"{bindir}:{sysdir}",
            "HOME": str(home),
            "STUB_LOG": str(log),
            "STUB_BIN": str(bindir),
            "STUB_TEMPLATES": str(templates),
            "LOXMATTER_REPO": str(REPO_ROOT),
            "LOXMATTER_DIR": str(home / "loxmatter"),
        }
        full_env.update(env or {})
        proc = subprocess.run(
            ["/bin/sh", str(INSTALLER), *args],
            env=full_env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=120,
        )
        return Result(proc, home, log)

    return run


def test_hilfe_endet_erfolgreich(installer):
    result = installer("--help")
    assert result.returncode == 0
    assert "--dry-run" in result.output


def test_unbekanntes_argument_bricht_ab(installer):
    result = installer("--nope")
    assert result.returncode == 2
    assert "Unknown argument" in result.output


def test_macos_wird_abgewiesen(installer):
    result = installer(stubs={"uname": 'echo Darwin\n'})
    assert result.returncode == 2
    assert "needs Linux" in result.output
    assert not (result.home / "loxmatter").exists()


def test_fremde_architektur_wird_abgewiesen(installer):
    riscv = 'case "${1-}" in\n  -m) echo riscv64 ;;\n  *) echo Linux ;;\nesac\n'
    result = installer(stubs={"uname": riscv})
    assert result.returncode == 2
    assert "riscv64" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: alle vier FAIL, weil `install.sh` nicht existiert (`/bin/sh: can't open …/install.sh`, Exit-Code 127).

- [ ] **Step 3: `install.sh` anlegen**

Neue Datei `install.sh` (ausführbar). Der GPL-Kopf wird aus `scripts/update.sh` wortgleich übernommen:

```sh
#!/bin/sh
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


# One-command installer for the loxmatter Docker stack.
#
#   curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
#
# Clones the repository, writes deploy/testhost/.env, starts the containers
# and then reports what still needs a human - it never silently repairs the
# host. Design: docs/superpowers/specs/2026-09-05-install-oneliner-design.md
#
# POSIX sh on purpose, not bash: the one-liner above ends in `| sh`, and
# /bin/sh is dash on Raspberry Pi OS - a bash script piped into sh dies at
# the first `[[`. Everything lives in a function and `main` runs on the very
# last line, so a download that is cut short defines functions and does
# nothing at all.
set -eu

REPO_URL="https://github.com/lucienkerl/loxmatter.git"
DOCKER_INSTALL_URL="https://get.docker.com"

DRY_RUN=0
TARGET_DIR=""
STEP="starting up"
CHECKOUT_EXISTED=0
STACK_STARTED=0

# ---------------------------------------------------------------- output --

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  %s\033[0m\n' "$*"; }

# Records what is being attempted, so the EXIT trap can say where it stopped.
step() { STEP="$1"; }

# An expected, fully explained stop. Exit code 2 tells the trap not to add
# its own "Failed while" noise on top.
die() {
  printf '\n\033[31mAborted: %s\033[0m\n' "$*" >&2
  exit 2
}

state_summary() {
  if [ "$STACK_STARTED" -eq 1 ]; then
    printf 'The stack in %s was started; `docker compose ps` there shows it.\n' \
      "$TARGET_DIR/deploy/testhost"
  elif [ -d "$TARGET_DIR" ]; then
    printf 'The checkout at %s exists; nothing was started.\n' "$TARGET_DIR"
  else
    printf 'Nothing was created; %s does not exist.\n' "$TARGET_DIR"
  fi
}

on_exit() {
  code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne 2 ]; then
    printf '\n\033[31mFailed while: %s\033[0m\n' "$STEP" >&2
    state_summary >&2
  fi
}

# ----------------------------------------------------------------- usage --

usage() {
  cat <<'EOF'
loxmatter installer - sets up the Docker stack in deploy/testhost.

Usage:
  curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
  curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh -s -- --dry-run
  sh install.sh [--dir PATH] [--dry-run]

Options:
  --dir PATH   where to clone the repository (default: $HOME/loxmatter)
  --dry-run    print every step without changing anything
  --help       show this text

These environment variables skip the matching question:
  LOXMATTER_DIR       where to clone
  LOXMATTER_MODE      thread | wifi
  MINISERVER_IP       address of the Loxone Miniserver
  RADIO_DEVICE        Thread radio, e.g. /dev/ttyUSB0 (thread mode only)
  RADIO_BAUDRATE      Thread radio baud rate (thread mode only)
  BACKBONE_IF         network interface for the border router (thread mode only)
  BLUETOOTH_ADAPTER   Bluetooth adapter id, e.g. 0
  LOXMATTER_API_TOKEN token for scripts and curl; generated when unset
EOF
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --dir)
        if [ $# -lt 2 ]; then die "--dir needs a path"; fi
        TARGET_DIR="$2"
        shift
        ;;
      --dir=*) TARGET_DIR="${1#--dir=}" ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument: $1 (allowed: --dir, --dry-run, --help)" ;;
    esac
    shift
  done
  if [ -z "$TARGET_DIR" ]; then
    TARGET_DIR="${LOXMATTER_DIR:-$HOME/loxmatter}"
  fi
}

# ------------------------------------------------------------- phase one --

check_platform() {
  step "checking the operating system"
  install_os="$(uname -s)"
  if [ "$install_os" != "Linux" ]; then
    die "This installer sets up the Docker stack, which needs Linux (found: $install_os).
On macOS, use the development path instead:
  git clone $REPO_URL && cd loxmatter && uv sync"
  fi
  install_arch="$(uname -m)"
  case "$install_arch" in
    aarch64|arm64|x86_64|amd64) : ;;
    *) die "Unsupported architecture: $install_arch (supported: aarch64, arm64, x86_64, amd64)" ;;
  esac
  note "Linux on $install_arch"
}

# ------------------------------------------------------------------ main --

main() {
  trap on_exit EXIT
  parse_args "$@"
  say "loxmatter installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "Dry run: every step is printed, nothing is changed."
  fi
  check_platform
}

main "$@"
```

- [ ] **Step 4: Ausführbar machen und Tests laufen lassen**

```bash
chmod +x install.sh
uv run pytest tests/test_install_script.py -v
```
Expected: 4 passed.

- [ ] **Step 5: shellcheck laufen lassen**

Run: `shellcheck -s sh install.sh`
Expected: keine Ausgabe, Exit-Code 0. Kommt ein Befund, wird er behoben — keine `# shellcheck disable`-Zeile ohne Begründung im Kommentar daneben.

- [ ] **Step 6: CI-Schritt ergänzen**

In `.github/workflows/ci.yml`, direkt nach `- uses: actions/checkout@v4`:

```yaml
      # install.sh wird per `curl | sh` auf fremden Rechnern ausgefuehrt -
      # ein Quoting-Fehler darin ist teurer als in jedem anderen Skript hier.
      - run: shellcheck -s sh install.sh
```

- [ ] **Step 7: Commit**

```bash
git add install.sh tests/test_install_script.py .github/workflows/ci.yml
git commit -m "feat(install): Skelett des One-Liner-Installskripts

install.sh mit Optionsauswertung, Ausgabe-Helfern, EXIT-Trap und der
Pruefung von Betriebssystem und Architektur. Reines POSIX sh, weil der
One-Liner auf \`| sh\` endet und /bin/sh auf Raspberry Pi OS dash ist;
alles steht in Funktionen und main laeuft in der letzten Zeile, damit ein
abgebrochener Download nichts ausfuehrt.

Dazu das Testgeruest: ein versiegelter PATH aus gefaelschten Binaries, die
ihre Aufrufe protokollieren. Ein Werkzeug \"fehlt\" dadurch, dass sein Stub
nicht angelegt wird.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Phase 1 — Werkzeuge, Rechte, Betriebsart, Konfigurationsquelle

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: aus Aufgabe 2 `say`, `note`, `warn`, `die`, `step`, `DRY_RUN`, `TARGET_DIR`.
- Produces: die Funktionen `have`, `check_tty`, `ask`, `check_privileges`, `collect_missing`, `check_can_install`, `detect_radio_device`, `decide_mode`, `valid_ipv4`, `env_file_value`, `check_config_source`; die Variablen `HAVE_TTY` (0/1), `SUDO` (`""` oder `sudo`), `MISSING_PACKAGES` (leer oder durch Leerzeichen getrennte Paketnamen), `NEED_DOCKER` (0/1), `MODE` (`thread` oder `wifi`), `DETECTED_RADIO` (Pfad oder leer).

**Abweichung vom Entwurf, bewusst:** Die Betriebsartfrage steht hier in Phase 1, nicht in Phase 4. Ihre Antwort entscheidet darüber, ob Phase 1 abbrechen muss (Thread ohne Funkmodul und ohne Terminal), und eine Frage verändert nichts am Host — sie darf also vor die Mutationsgrenze.

- [ ] **Step 1: Fixture um Standardwerte ergänzen**

In `tests/test_install_script.py`, in `full_env`, zwei Zeilen ergänzen — ohne sie bricht ab jetzt jeder Test schon an der fehlenden Miniserver-Adresse ab:

```python
            "LOXMATTER_DIR": str(home / "loxmatter"),
            # Ohne Terminal muss die Adresse aus der Umgebung kommen. Tests,
            # die genau diesen Abbruch pruefen, setzen sie auf "".
            "MINISERVER_IP": "10.0.1.99",
```

- [ ] **Step 2: Write the failing tests**

Ans Ende von `tests/test_install_script.py` anfügen:

```python
def test_ohne_sudo_und_ohne_root_bricht_es_vor_dem_klonen_ab(installer):
    result = installer(omit=("docker", "sudo"))
    assert result.returncode == 2
    assert "docker" in result.output
    assert not result.called("git clone")


def test_alle_fehlenden_werkzeuge_werden_auf_einmal_genannt(installer):
    result = installer(omit=("git", "curl", "openssl", "docker", "sudo"))
    assert result.returncode == 2
    for tool in ("git", "curl", "openssl", "docker"):
        assert tool in result.output


def test_ohne_apt_get_nennt_es_die_pakete_und_bricht_ab(installer):
    result = installer(omit=("git", "apt-get"))
    assert result.returncode == 2
    assert "apt-get" in result.output
    assert "git" in result.output
    assert not result.called("git clone")


def test_root_wird_gewarnt_aber_nicht_gestoppt(installer):
    result = installer(env={"FAKE_UID": "0"})
    assert result.returncode == 0
    assert "Running as root" in result.output


def test_ohne_funkmodul_faellt_es_auf_wifi(installer):
    result = installer()
    assert result.returncode == 0
    assert "Operating mode: wifi" in result.output


def test_thread_ohne_geraet_und_ohne_terminal_bricht_ab(installer):
    result = installer(env={"LOXMATTER_MODE": "thread"})
    assert result.returncode == 2
    assert "no radio" in result.output


def test_thread_mit_geraet_aus_der_umgebung(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "Operating mode: thread" in result.output


def test_unbekannte_betriebsart_bricht_ab(installer):
    result = installer(env={"LOXMATTER_MODE": "zigbee"})
    assert result.returncode == 2
    assert "thread" in result.output


def test_ungueltige_miniserver_ip_bricht_vor_dem_klonen_ab(installer):
    result = installer(env={"MINISERVER_IP": "nicht.eine.ip"})
    assert result.returncode == 2
    assert "not a valid IPv4" in result.output
    assert not result.called("git clone")


def test_ohne_miniserver_ip_und_ohne_terminal_bricht_es_ab(installer):
    result = installer(env={"MINISERVER_IP": ""})
    assert result.returncode == 2
    assert "MINISERVER_IP" in result.output
    assert not result.called("git clone")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "sudo or werkzeuge or apt_get or root or wifi or thread or betriebsart or miniserver"`
Expected: alle zehn FAIL — die Prüfungen gibt es noch nicht, das Skript endet nach `check_platform` mit 0.

- [ ] **Step 4: Zusätzliche Werkzeuge in `SYSTEM_TOOLS` aufnehmen**

`install.sh` benutzt ab hier `env`, `tail` und `head`. In `tests/test_install_script.py`:

```python
SYSTEM_TOOLS = (
    "sh", "cat", "grep", "sed", "awk", "tr", "od", "mkdir", "rm", "mv",
    "sleep", "chmod", "cp", "printf", "true", "false", "env", "tail", "head",
)
```

Und den `id`-Stub so ersetzen, dass er auch nach dem Namen gefragt werden kann:

```python
# Nicht als echtes Werkzeug: sonst haengt jeder root-Test davon ab, als wer
# die Testsuite laeuft. FAKE_UID=0 macht daraus einen root-Lauf.
_ID = """case "${1-}" in
  -un|-nu|-n) echo "tester" ;;
  *) echo "${FAKE_UID-1000}" ;;
esac
"""
```

- [ ] **Step 5: Phase 1 in `install.sh` implementieren**

Neue Variablen zu den bestehenden oben ergänzen:

```sh
HAVE_TTY=0
SUDO=""
DOCKER_SUDO=0
INSTALLED_DOCKER=0
MISSING_PACKAGES=""
NEED_DOCKER=0
MODE=""
DETECTED_RADIO=""
STACK_DIR=""
```

Nach `check_platform` einfügen:

```sh
have() { command -v "$1" >/dev/null 2>&1; }

# stdin is the pipe when this runs as `curl ... | sh`, so every question has
# to go to the controlling terminal instead. Opening it in a subshell is the
# portable way to find out whether there is one at all - `test -r /dev/tty`
# can succeed on a device node that then refuses to open.
check_tty() {
  if ( exec </dev/tty ) 2>/dev/null; then
    HAVE_TTY=1
  else
    HAVE_TTY=0
    note "No terminal available; every value has to come from the environment."
  fi
}

# Asks on the terminal and echoes the answer. Without a terminal, or in a dry
# run, it echoes the default and asks nothing.
ask() {
  ask_prompt="$1"
  ask_default="$2"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$ask_default"
    return 0
  fi
  if [ -n "$ask_default" ]; then
    printf '%s [%s]: ' "$ask_prompt" "$ask_default" >/dev/tty
  else
    printf '%s: ' "$ask_prompt" >/dev/tty
  fi
  if ! read -r ask_answer </dev/tty; then
    ask_answer=""
  fi
  if [ -z "$ask_answer" ]; then
    ask_answer="$ask_default"
  fi
  printf '%s' "$ask_answer"
}

check_privileges() {
  step "checking privileges"
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    warn "Running as root. The checkout and ~/loxmatter-backups will belong to"
    warn "root, and scripts/update.sh will need root from then on."
  elif have sudo; then
    SUDO="sudo"
  else
    SUDO=""
  fi
}

# Collects everything that is missing instead of stopping at the first gap -
# being told about git, then about curl, then about Docker on three separate
# runs is the opposite of a one-liner.
collect_missing() {
  step "checking which tools are present"
  MISSING_PACKAGES=""
  for tool in git curl openssl; do
    if ! have "$tool"; then
      MISSING_PACKAGES="$MISSING_PACKAGES $tool"
    fi
  done
  MISSING_PACKAGES="${MISSING_PACKAGES# }"
  NEED_DOCKER=0
  if ! have docker; then
    NEED_DOCKER=1
  elif ! docker compose version >/dev/null 2>&1; then
    die "docker is installed but the compose plugin is not.
On Debian and Ubuntu: apt-get install docker-compose-plugin
Then run this again."
  fi
}

check_can_install() {
  step "checking whether missing tools can be installed"
  if [ -z "$MISSING_PACKAGES" ] && [ "$NEED_DOCKER" -eq 0 ]; then
    return 0
  fi
  wanted="$MISSING_PACKAGES"
  if [ "$NEED_DOCKER" -eq 1 ]; then
    wanted="$wanted docker"
  fi
  wanted="${wanted# }"
  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
    die "Missing: $wanted
Installing these needs root, but this is not root and sudo is not available.
Install them yourself, then run this again."
  fi
  if ! have apt-get; then
    die "Missing: $wanted
This installer only knows apt-get (Debian, Ubuntu, Raspberry Pi OS).
Install them with your package manager, then run this again."
  fi
  note "Will install: $wanted"
}

detect_radio_device() {
  for candidate in /dev/ttyUSB* /dev/ttyACM*; do
    if [ -e "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
}

decide_mode() {
  step "deciding the operating mode"
  DETECTED_RADIO="$(detect_radio_device)"
  if [ -n "${LOXMATTER_MODE:-}" ]; then
    MODE="$LOXMATTER_MODE"
  else
    if [ -n "$DETECTED_RADIO" ]; then
      note "Found a possible Thread radio at $DETECTED_RADIO."
      mode_default="thread"
    else
      note "No Thread radio found at /dev/ttyUSB* or /dev/ttyACM*."
      mode_default="wifi"
    fi
    MODE="$(ask "Operating mode - 'thread' for Thread and WiFi, 'wifi' for WiFi and Ethernet only" "$mode_default")"
  fi
  case "$MODE" in
    thread|wifi) : ;;
    *) die "Operating mode must be 'thread' or 'wifi' (got: $MODE)" ;;
  esac
  note "Operating mode: $MODE"
}

valid_ipv4() {
  case "$1" in
    ""|*[!0-9.]*) return 1 ;;
  esac
  ipv4_saved_ifs="$IFS"
  IFS=.
  # Deliberate word splitting on the dots.
  # shellcheck disable=SC2086
  set -- $1
  IFS="$ipv4_saved_ifs"
  if [ $# -ne 4 ]; then
    return 1
  fi
  for ipv4_octet in "$@"; do
    case "$ipv4_octet" in
      ""|*[!0-9]*) return 1 ;;
    esac
    if [ "$ipv4_octet" -gt 255 ]; then
      return 1
    fi
  done
  return 0
}

# Reads a key out of an existing .env, so a second run does not ask again for
# something that is already configured.
env_file_value() {
  if [ -f "$TARGET_DIR/deploy/testhost/.env" ]; then
    awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' \
      "$TARGET_DIR/deploy/testhost/.env"
  fi
}

# Anything that has no default and cannot be asked for has to stop the run
# HERE - before a single file is written.
check_config_source() {
  step "checking that the configuration can be obtained"
  if [ -z "${MINISERVER_IP:-}" ] && [ -z "$(env_file_value MINISERVER_IP)" ] &&
     [ "$HAVE_TTY" -eq 0 ]; then
    die "MINISERVER_IP is not set and there is no terminal to ask on.
Pass it in instead:
  curl -fsSL $RAW_URL | MINISERVER_IP=10.0.1.99 sh"
  fi
  # A malformed address has to stop the run here too - noticing it after the
  # clone would be exactly the "aborted halfway" this phase exists to prevent.
  if [ -n "${MINISERVER_IP:-}" ] && ! valid_ipv4 "$MINISERVER_IP"; then
    die "MINISERVER_IP is not a valid IPv4 address: '$MINISERVER_IP'"
  fi
  if [ "$MODE" = "thread" ] && [ -z "${RADIO_DEVICE:-}" ] &&
     [ -z "$(env_file_value RADIO_DEVICE)" ] && [ -z "$DETECTED_RADIO" ] &&
     [ "$HAVE_TTY" -eq 0 ]; then
    die "Thread mode was requested, but no radio was found at /dev/ttyUSB* or
/dev/ttyACM* and there is no terminal to ask on. Either plug the radio in,
pass RADIO_DEVICE=/dev/ttyUSB0, or use LOXMATTER_MODE=wifi."
  fi
}
```

Oben zu den Konstanten ergänzen (wird in mehreren Meldungen gebraucht):

```sh
RAW_URL="https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh"
```

`main` erweitern:

```sh
main() {
  trap on_exit EXIT
  parse_args "$@"
  say "loxmatter installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "Dry run: every step is printed, nothing is changed."
  fi
  check_platform
  check_tty
  check_privileges
  collect_missing
  check_can_install
  decide_mode
  check_config_source
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 14 passed.

- [ ] **Step 7: shellcheck**

Run: `shellcheck -s sh install.sh`
Expected: keine Ausgabe.

- [ ] **Step 8: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 1 - pruefen, bevor irgendetwas veraendert wird

Sammelt fehlende Werkzeuge vollstaendig ein, statt beim ersten Treffer
abzubrechen, klaert Rechte und Paketverwaltung, bestimmt die Betriebsart
und stellt sicher, dass jeder Wert ohne Vorgabe auch beschaffbar ist. Erst
danach darf spaeter etwas geschrieben werden.

Die Betriebsartfrage steht bewusst hier und nicht in Phase 4: ihre Antwort
entscheidet ueber einen Abbruch, und eine Frage veraendert nichts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Phase 2 — fehlende Pakete und Docker nachinstallieren

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `MISSING_PACKAGES`, `NEED_DOCKER`, `SUDO`, `DRY_RUN`, `DOCKER_INSTALL_URL`.
- Produces: `run_root`, `install_packages`, `install_docker`, `dk`. Nach `install_docker` ist `DOCKER_SUDO` 1, wenn Docker in diesem Lauf installiert wurde; `dk` ist ab dann der einzige erlaubte Weg, Docker aufzurufen — kein direkter `docker`-Aufruf mehr irgendwo im Skript.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_install_script.py`:

```python
def test_nur_die_fehlenden_pakete_werden_installiert(installer):
    result = installer(omit=("git", "curl"))
    assert result.returncode == 0
    assert result.called("apt-get install -y git curl")
    assert not result.called("apt-get install -y git curl openssl")


def test_docker_kommt_nach_den_basispaketen(installer):
    # get.docker.com braucht selbst curl - die Reihenfolge ist keine Kosmetik.
    result = installer(omit=("git", "curl", "docker"))
    assert result.returncode == 0
    apt = next(i for i, c in enumerate(result.calls) if c.startswith("apt-get install"))
    docker_install = next(
        i for i, c in enumerate(result.calls) if "get.docker.com" in c
    )
    assert apt < docker_install


def test_nach_eigener_docker_installation_laeuft_alles_ueber_sudo(installer):
    result = installer(omit=("docker",))
    assert result.returncode == 0
    assert result.called("usermod -aG docker")
    assert result.called("sudo docker")
    assert "log out and back in" in result.output


def test_vorhandenes_docker_wird_nicht_neu_installiert(installer):
    result = installer()
    assert result.returncode == 0
    assert not any("get.docker.com" in call for call in result.calls)
    assert not result.called("sudo docker")


def test_dry_run_veraendert_nichts(installer):
    result = installer("--dry-run", omit=("git", "docker"))
    assert result.returncode == 0
    assert not result.called("apt-get")
    assert not result.called("sudo")
    assert not any("get.docker.com" in call for call in result.calls)
    assert "would run" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "pakete or docker or dry_run"`
Expected: FAIL — `apt-get` wird nie aufgerufen, `usermod` nie, `would run` steht nicht in der Ausgabe.

- [ ] **Step 3: Phase 2 implementieren**

In `install.sh` nach `check_config_source` einfügen:

```sh
# ------------------------------------------------------------- phase two --

# Runs a command as root, or prints it in a dry run. Everything that needs
# root goes through here, so a dry run cannot slip past by accident.
run_root() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: $*"
    return 0
  fi
  if [ -n "$SUDO" ]; then
    sudo "$@"
  else
    "$@"
  fi
}

install_packages() {
  if [ -z "$MISSING_PACKAGES" ]; then
    return 0
  fi
  step "installing $MISSING_PACKAGES"
  say "Installing missing tools: $MISSING_PACKAGES"
  note "This uses apt-get and needs root."
  run_root apt-get update ||
    die "apt-get update failed. Is this machine online?"
  # Deliberate word splitting: one package per argument.
  # shellcheck disable=SC2086
  run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $MISSING_PACKAGES ||
    die "Installing $MISSING_PACKAGES failed. Nothing else was changed."
}

install_docker() {
  if [ "$NEED_DOCKER" -eq 0 ]; then
    return 0
  fi
  step "installing Docker"
  say "Docker is not installed"
  note "Installing it from $DOCKER_INSTALL_URL."
  note "This needs root and adds Docker's package repository to this machine."
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: curl -fsSL $DOCKER_INSTALL_URL | sh"
    return 0
  fi
  if [ -n "$SUDO" ]; then
    curl -fsSL "$DOCKER_INSTALL_URL" | sudo sh ||
      die "The Docker installer failed. Nothing else was changed."
  else
    curl -fsSL "$DOCKER_INSTALL_URL" | sh ||
      die "The Docker installer failed. Nothing else was changed."
  fi
  INSTALLED_DOCKER=1
  if [ -n "$SUDO" ]; then
    docker_user="$(id -un)"
    run_root usermod -aG docker "$docker_user" ||
      warn "Could not add $docker_user to the 'docker' group."
    # The new group only takes effect after a new login session, so this run
    # cannot use plain `docker` - it would fail with a permission error right
    # after reporting success.
    DOCKER_SUDO=1
    warn "You are not in the 'docker' group in this session yet."
    note "This run continues with 'sudo docker'; log out and back in afterwards."
  fi
}

# The only way this script calls Docker.
dk() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: docker $*"
    return 0
  fi
  if [ "$DOCKER_SUDO" -eq 1 ]; then
    sudo docker "$@"
  else
    docker "$@"
  fi
}
```

`main` erweitern — nach `check_config_source`:

```sh
  install_packages
  install_docker
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 19 passed.

- [ ] **Step 5: shellcheck**

Run: `shellcheck -s sh install.sh`
Expected: keine Ausgabe. Die einzige `disable`-Zeile ist die für SC2086 am `apt-get install`, mit der Begründung darüber.

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 2 - fehlende Pakete und Docker nachziehen

Erst apt-get fuer git/curl/openssl, dann get.docker.com - in dieser
Reihenfolge, weil der Docker-Installer selbst curl braucht. Angekuendigt,
aber nicht erfragt: das Skript nennt Schritt und Quelle und laeuft weiter.

Hat es Docker selbst installiert, benutzt es fuer den Rest dieses Laufs
sudo docker: die neue Gruppenmitgliedschaft greift erst nach einer
Neuanmeldung, ein blankes `docker` scheiterte sonst unmittelbar nach der
Erfolgsmeldung.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Phase 3 — Klon oder vorhandenes Checkout

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `TARGET_DIR`, `REPO_URL`, `DRY_RUN`.
- Produces: `ensure_checkout`; setzt `STACK_DIR="$TARGET_DIR/deploy/testhost"` und `CHECKOUT_EXISTED` (0/1). Alle folgenden Aufgaben lesen und schreiben ausschließlich über `STACK_DIR`.

- [ ] **Step 1: Write the failing tests**

```python
def test_klont_nach_target_dir(installer):
    result = installer()
    assert result.returncode == 0
    assert result.called("git clone --branch main https://github.com/lucienkerl/loxmatter.git")
    assert (result.home / "loxmatter" / "deploy" / "testhost").is_dir()


def test_zweiter_lauf_klont_nicht_erneut(installer):
    first = installer()
    assert first.returncode == 0
    second = installer()
    assert second.returncode == 0
    assert not second.called("git clone")
    assert "existing checkout" in second.output


def test_fremdes_verzeichnis_wird_abgewiesen(installer, tmp_path):
    fremd = tmp_path / "home" / "loxmatter"
    fremd.mkdir(parents=True)
    (fremd / "irgendwas.txt").write_text("nicht loxmatter")
    result = installer()
    assert result.returncode == 2
    assert "does not look like a loxmatter checkout" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "klont or verzeichnis"`
Expected: FAIL — `git clone` wird nie aufgerufen.

- [ ] **Step 3: Phase 3 implementieren**

In `install.sh` nach `dk` einfügen:

```sh
# ----------------------------------------------------------- phase three --

ensure_checkout() {
  step "getting the repository"
  STACK_DIR="$TARGET_DIR/deploy/testhost"
  if [ -d "$TARGET_DIR" ]; then
    CHECKOUT_EXISTED=1
    say "Using the existing checkout"
    note "$TARGET_DIR"
    if [ ! -f "$TARGET_DIR/Dockerfile" ] || [ ! -f "$STACK_DIR/docker-compose.yml" ]; then
      die "$TARGET_DIR exists but does not look like a loxmatter checkout
(no Dockerfile, or no deploy/testhost/docker-compose.yml).
Move it aside, or pass --dir with a different path."
    fi
    return 0
  fi
  say "Cloning the repository"
  note "$REPO_URL -> $TARGET_DIR"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: git clone --branch main $REPO_URL $TARGET_DIR"
    return 0
  fi
  git clone --branch main "$REPO_URL" "$TARGET_DIR" ||
    die "git clone failed. Is this machine online, and is $TARGET_DIR writable?"
}
```

`main` erweitern — nach `install_docker`:

```sh
  ensure_checkout
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 3 - klonen oder das vorhandene Checkout nehmen

Ein vorhandenes Verzeichnis wird weder neu geklont noch gezogen, sondern
nur darauf geprueft, dass es wirklich ein loxmatter-Checkout ist -
aktualisiert wird spaeter und nur nach Zustimmung, damit die Sicherung aus
scripts/update.sh nicht umgangen wird.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Phase 4 — `.env` schreiben

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `STACK_DIR`, `MODE`, `DETECTED_RADIO`, `HAVE_TTY`, `ask`, `env_file_value`, `dk`.
- Produces: `env_set`, `env_file_has`, `ensure_env_value`, `ask_miniserver`, `detect_backbone_if`, `detect_bt_adapter`, `gen_token`, `configure_mode`, `configure`; setzt `ENV_FILE` und `ENV_IS_NEW`. Nach `configure` steht `MODE` endgültig fest — eine vorhandene `.env` kann ihn überschreiben.

**Die Regel „bestehende Werte nie überschreiben" gilt nur für eine `.env`, die es schon gab.** Eine frisch aus `.env.example` kopierte Datei enthält Beispielwerte (`COMPOSE_PROFILES=thread`, `RADIO_DEVICE=/dev/ttyUSB0`) — würden die als Nutzerentscheidung gelten, fragte das Skript nie etwas. `ENV_IS_NEW` trennt beides.

- [ ] **Step 1: Write the failing tests**

```python
def _env(result):
    return dict(
        line.split("=", 1)
        for line in result.env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


def test_wifi_lauf_schaltet_das_thread_profil_ab(installer):
    result = installer()
    assert result.returncode == 0
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_thread_lauf_setzt_profil_geraet_und_interface(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == "/dev/ttyUSB0"
    assert values["BACKBONE_IF"] == "eth0"


def test_miniserver_ip_kommt_aus_der_umgebung(installer):
    result = installer(env={"MINISERVER_IP": "10.0.1.77"})
    assert _env(result)["MINISERVER_IP"] == "10.0.1.77"


def test_token_wird_erzeugt(installer):
    result = installer()
    token = _env(result)["LOXMATTER_API_TOKEN"]
    assert len(token) == 64
    assert set(token) <= set("0123456789abcdef")


def test_token_faellt_ohne_openssl_auf_urandom_zurueck(installer):
    result = installer(omit=("openssl",))
    assert result.returncode == 0
    token = _env(result)["LOXMATTER_API_TOKEN"]
    assert len(token) == 64
    assert set(token) <= set("0123456789abcdef")


def test_zweiter_lauf_laesst_die_env_unberuehrt(installer):
    first = installer()
    before = first.env_file.read_bytes()
    second = installer()
    assert second.returncode == 0
    assert second.env_file.read_bytes() == before


def test_nur_der_fehlende_schluessel_wird_ergaenzt(installer):
    first = installer()
    text = first.env_file.read_text().replace(
        f"LOXMATTER_API_TOKEN={_env(first)['LOXMATTER_API_TOKEN']}",
        "LOXMATTER_API_TOKEN=",
    )
    first.env_file.write_text(text)
    second = installer(env={"MINISERVER_IP": "10.0.1.55"})
    values = _env(second)
    assert len(values["LOXMATTER_API_TOKEN"]) == 64
    # Die vorhandene Adresse bleibt, obwohl die Umgebung eine andere nennt.
    assert values["MINISERVER_IP"] == "10.0.1.99"


def test_ein_schluessel_wird_ersetzt_nicht_angehaengt(installer):
    result = installer()
    lines = [
        line for line in result.env_file.read_text().splitlines()
        if line.startswith("COMPOSE_PROFILES=")
    ]
    assert len(lines) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "wifi_lauf or thread_lauf or miniserver_ip_kommt or token or zweiter_lauf or schluessel"`
Expected: FAIL mit `FileNotFoundError` auf `.env` — die Datei wird noch nicht angelegt.

- [ ] **Step 3: Phase 4 implementieren**

Oben zu den Variablen ergänzen:

```sh
ENV_FILE=""
ENV_IS_NEW=0
PORT=8080
```

Nach `ensure_checkout` einfügen:

```sh
# ------------------------------------------------------------ phase four --

env_file_has() { grep -q "^$1=" "$ENV_FILE" 2>/dev/null; }

# Replaces the line instead of appending a second definition. Compose would
# honour the last one either way, but whoever edits the file later would then
# be changing the wrong line - the reasoning is spelled out in
# deploy/testhost/README.md.
env_set() {
  env_key="$1"
  env_value="$2"
  if env_file_has "$env_key"; then
    awk -F= -v key="$env_key" -v value="$env_value" '
      $1 == key && !seen { print key "=" value; seen = 1; next }
      { print }
    ' "$ENV_FILE" > "$ENV_FILE.new"
    mv "$ENV_FILE.new" "$ENV_FILE"
  else
    printf '%s=%s\n' "$env_key" "$env_value" >> "$ENV_FILE"
  fi
}

ensure_env_value() {
  value_key="$1"
  value_prompt="$2"
  value_default="$3"
  value_required="$4"
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    value_kept="$(env_file_value "$value_key")"
    if [ -n "$value_kept" ]; then
      note "$value_key=$value_kept (kept)"
      return 0
    fi
  fi
  # An environment variable of the same name skips the question entirely.
  value_override=""
  eval "value_override=\${$value_key:-}"
  if [ -n "$value_override" ]; then
    value_new="$value_override"
  else
    value_new="$(ask "$value_prompt" "$value_default")"
  fi
  if [ -z "$value_new" ] && [ "$value_required" -eq 1 ]; then
    die "$value_key needs a value and none could be obtained."
  fi
  env_set "$value_key" "$value_new"
  note "$value_key=$value_new"
}

ask_miniserver() {
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    ms_kept="$(env_file_value MINISERVER_IP)"
    if [ -n "$ms_kept" ]; then
      note "MINISERVER_IP=$ms_kept (kept)"
      return 0
    fi
  fi
  ms_value="${MINISERVER_IP:-}"
  while [ -z "$ms_value" ] || ! valid_ipv4 "$ms_value"; do
    if [ "$HAVE_TTY" -eq 0 ]; then
      die "MINISERVER_IP is not a valid IPv4 address: '$ms_value'"
    fi
    ms_value="$(ask "IPv4 address of the Loxone Miniserver" "")"
  done
  env_set MINISERVER_IP "$ms_value"
  note "MINISERVER_IP=$ms_value"
}

detect_backbone_if() {
  ip route show default 2>/dev/null |
    awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }'
}

detect_bt_adapter() {
  for candidate in /sys/class/bluetooth/hci*; do
    if [ -e "$candidate" ]; then
      printf '%s' "${candidate##*/hci}"
      return 0
    fi
  done
  printf '0'
}

# The .env.example explains why the token has to be plain [0-9a-f]: it travels
# in an HTTP header and in a WebSocket subprotocol. /dev/urandom produces the
# same shape, so a missing openssl cannot sink an otherwise healthy install.
gen_token() {
  if have openssl; then
    token_value="$(openssl rand -hex 32 2>/dev/null || true)"
    if [ -n "$token_value" ]; then
      printf '%s' "$token_value"
      return 0
    fi
  fi
  od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
}

# An installation that predates the profiles has no COMPOSE_PROFILES line but
# does have an otbr container. Writing an empty value there would silently
# take its border router away on the next `compose up`.
configure_mode() {
  if [ "$ENV_IS_NEW" -eq 0 ] && env_file_has COMPOSE_PROFILES; then
    if [ -n "$(env_file_value COMPOSE_PROFILES)" ]; then
      MODE="thread"
    else
      MODE="wifi"
    fi
    note "COMPOSE_PROFILES kept, mode: $MODE"
    return 0
  fi
  if [ "$ENV_IS_NEW" -eq 0 ] && dk ps -a --format '{{.Names}}' 2>/dev/null | grep -qx otbr; then
    env_set COMPOSE_PROFILES thread
    MODE="thread"
    note "COMPOSE_PROFILES=thread (this installation already runs otbr)"
    return 0
  fi
  if [ "$MODE" = "thread" ]; then
    env_set COMPOSE_PROFILES thread
    note "COMPOSE_PROFILES=thread"
  else
    env_set COMPOSE_PROFILES ""
    note "COMPOSE_PROFILES= (WiFi and Ethernet only, no Thread border router)"
  fi
}

configure() {
  step "writing the configuration"
  ENV_FILE="$STACK_DIR/.env"
  say "Configuration"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would write $ENV_FILE for mode '$MODE'"
    return 0
  fi
  ENV_IS_NEW=0
  if [ ! -f "$ENV_FILE" ]; then
    cp "$STACK_DIR/.env.example" "$ENV_FILE" || die "Could not create $ENV_FILE"
    ENV_IS_NEW=1
  fi
  configure_mode
  if [ "$MODE" = "thread" ]; then
    ensure_env_value RADIO_DEVICE "Thread radio device" "$DETECTED_RADIO" 1
    ensure_env_value RADIO_BAUDRATE "Thread radio baud rate" "460800" 1
    ensure_env_value BACKBONE_IF "Network interface for the border router" \
      "$(detect_backbone_if)" 1
  fi
  ensure_env_value BLUETOOTH_ADAPTER "Bluetooth adapter id for BLE commissioning" \
    "$(detect_bt_adapter)" 0
  ask_miniserver
  if [ "$ENV_IS_NEW" -eq 1 ] || [ -z "$(env_file_value LOXMATTER_API_TOKEN)" ]; then
    env_set LOXMATTER_API_TOKEN "${LOXMATTER_API_TOKEN:-$(gen_token)}"
    note "LOXMATTER_API_TOKEN generated"
  else
    note "LOXMATTER_API_TOKEN kept"
  fi
}
```

`main` erweitern — nach `ensure_checkout`:

```sh
  configure
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 30 passed.

- [ ] **Step 5: shellcheck und Formatierung**

Run: `shellcheck -s sh install.sh && uv run ruff check . && uv run ruff format --check .`
Expected: keine Ausgabe von shellcheck, `All checks passed!`, `N files already formatted`.

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 4 - .env erkennen, fragen, schreiben

Erkannte Vorgaben sind Vorschlaege in einer Rueckfrage, keine stille
Festlegung; gesetzte Umgebungsvariablen ueberspringen die jeweilige Frage.
Werte werden zeilenweise ersetzt statt angehaengt - eine zweite Definition
waere zwar wirksam, aber wer die Datei spaeter bearbeitet, aendert dann die
falsche Zeile.

\"Bestehende Werte nie ueberschreiben\" gilt nur fuer eine .env, die es
schon gab: eine frisch aus .env.example kopierte enthaelt Beispielwerte,
die als Nutzerentscheidung gelesen jede Rueckfrage verschluckt haetten.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Phase 5 und 6 — starten, prüfen, Befunde sammeln

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `STACK_DIR`, `MODE`, `dk`, `die`.
- Produces: `add_finding`, `stack_port`, `start_stack`, `check_health`, `check_containers`, `check_rfkill`, `check_thread`, `run_checks`; setzt `STACK_STARTED=1`, `PORT` und sammelt `FINDINGS`.

- [ ] **Step 1: Docker-Stub um `compose ps` erweitern**

In `tests/test_install_script.py` den Docker-Stub ersetzen:

```python
_DOCKER = """if [ "${1-}" = "compose" ] && [ "${2-}" = "version" ]; then
  echo "Docker Compose version v2.30.0"
fi
if [ "${1-}" = "compose" ] && [ "${2-}" = "ps" ]; then
  for service in ${FAKE_SERVICES-otbr matter-server loxmatter}; do
    echo "$service"
  done
fi
exit 0
"""
```

- [ ] **Step 2: Write the failing tests**

```python
def test_stack_wird_gebaut_und_gestartet(installer):
    result = installer()
    assert result.returncode == 0
    assert result.called("docker compose up -d --build")
    assert (result.home / "loxmatter" / "deploy" / "testhost" / "data").is_dir()


def test_gesundheitspruefung_laeuft(installer):
    result = installer()
    assert any("/health" in call for call in result.calls)
    assert "answers" in result.output


def test_fehlender_dienst_wird_zum_befund(installer):
    result = installer(env={"FAKE_SERVICES": "loxmatter"})
    assert result.returncode == 0
    assert "matter-server" in result.output
    assert "not running" in result.output


def test_thread_lauf_ohne_wpan_meldet_den_workaround(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "start-stop-daemon" in result.output
    assert "otbr-agent" in result.output


def test_wifi_lauf_erwaehnt_thread_gar_nicht_als_problem(installer):
    result = installer()
    assert result.returncode == 0
    assert "start-stop-daemon" not in result.output
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "stack_wird or gesundheit or dienst or wpan or erwaehnt"`
Expected: FAIL — `docker compose up` wird nie aufgerufen.

- [ ] **Step 4: Phase 5 und 6 implementieren**

Oben ergänzen:

```sh
FINDINGS=""
```

Nach `configure` einfügen:

```sh
# ------------------------------------------------------------ phase five --

start_stack() {
  step "starting the stack"
  say "Starting the containers"
  if [ "$MODE" = "thread" ]; then
    note "otbr, matter-server, loxmatter"
  else
    note "matter-server, loxmatter - no Thread border router in this mode"
  fi
  note "The first build takes several minutes on a Raspberry Pi. It is not stuck."
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: docker compose up -d --build in $STACK_DIR"
    return 0
  fi
  mkdir -p "$STACK_DIR/data"
  # No --profile here on purpose: COMPOSE_PROFILES lives in .env, which Compose
  # reads by itself. Every later call in this directory - by hand, or from
  # scripts/update.sh - then picks the same services without a flag to remember.
  ( cd "$STACK_DIR" && dk compose up -d --build ) ||
    die "docker compose up failed. The checkout and .env are in place; fix the
cause and run this again. The logs are in:
  cd $STACK_DIR && docker compose logs"
  STACK_STARTED=1
}

# ------------------------------------------------------------- phase six --

# Findings are reported, never repaired: unblocking rfkill needs a privileged
# container, and the OTBR workaround is kernel specific and would kill working
# processes on hosts that do not need it.
add_finding() {
  FINDINGS="$FINDINGS
$1
"
}

stack_port() {
  port_value="$(grep -A1 -- '--listen' "$STACK_DIR/docker-compose.yml" |
    tail -1 | tr -dc '0-9')"
  if [ -z "$port_value" ]; then
    port_value=8080
  fi
  printf '%s' "$port_value"
}

check_health() {
  step "waiting for the bridge to answer"
  PORT="$(stack_port)"
  health_url="http://127.0.0.1:$PORT/health"
  health_ok=0
  health_tries=0
  while [ "$health_tries" -lt 20 ]; do
    if curl -fsS -m 3 "$health_url" >/dev/null 2>&1; then
      health_ok=1
      break
    fi
    health_tries=$((health_tries + 1))
    sleep 1
  done
  if [ "$health_ok" -ne 1 ]; then
    printf '\n\033[31m%s does not answer. Last lines from the log:\033[0m\n' "$health_url"
    dk logs --tail 30 loxmatter 2>&1 || true
    die "The containers are up but the bridge does not report healthy."
  fi
  note "$health_url answers"
}

check_containers() {
  step "checking the containers"
  expected="matter-server loxmatter"
  if [ "$MODE" = "thread" ]; then
    expected="otbr $expected"
  fi
  running="$( ( cd "$STACK_DIR" && dk compose ps --services ) 2>/dev/null || true)"
  for service in $expected; do
    if ! printf '%s\n' "$running" | grep -qx "$service"; then
      add_finding "Service '$service' is not running. Look at:
  cd $STACK_DIR && docker compose logs $service"
    fi
  done
}

check_rfkill() {
  step "checking the Bluetooth adapter"
  for entry in /sys/class/rfkill/rfkill*; do
    if [ ! -r "$entry/type" ]; then
      continue
    fi
    if [ "$(cat "$entry/type")" != "bluetooth" ]; then
      continue
    fi
    if [ "$(cat "$entry/soft" 2>/dev/null || echo 0)" = "1" ]; then
      rfkill_name="${entry##*/}"
      add_finding "Bluetooth is rfkill soft-blocked ($rfkill_name), so commissioning
over BLE will fail. This needs root, which is why it is not done here:
  docker run --rm --privileged -v /sys:/sys alpine \\
    sh -c 'echo 0 > /sys/class/rfkill/$rfkill_name/soft'
It only has to be done once; the unblock survives reboots."
    fi
    return 0
  done
}

check_thread() {
  if [ "$MODE" != "thread" ]; then
    return 0
  fi
  step "checking the Thread network"
  # The same test scripts/otbr-watchdog.sh and the "System" view use: scope 00
  # means routed, wpan* is OTBR's Thread interface.
  if awk '$4 == "00" && $6 ~ /^wpan/ { found = 1 } END { exit !found }' \
      /proc/net/if_inet6 2>/dev/null; then
    note "Thread interface is up"
    return 0
  fi
  add_finding "No Thread interface (wpan*) yet. On a Raspberry Pi kernel the OTBR
image's start-stop-daemon never finishes and otbr-agent is never exec'd, so
this has to be re-applied after every 'docker compose up':
  docker exec otbr sh -c 'rm -f /var/run/otbr-agent.pid /var/run/otbr-web.pid'
  docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B $(env_file_value BACKBONE_IF) -d7 \\
    --rest-listen-address 127.0.0.1 \\
    'spinel+hdlc+uart://$(env_file_value RADIO_DEVICE)?uart-baudrate=$(env_file_value RADIO_BAUDRATE)'
  docker exec otbr ot-ctl ifconfig up
  docker exec otbr ot-ctl thread start
The state goes from 'detached' to 'leader' after about 15 seconds. See
deploy/testhost/README.md, 'start-stop-daemon haengt auf dem Pi-Kernel'."
}

run_checks() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would check /health, the containers, Bluetooth and Thread"
    return 0
  fi
  say "Checking"
  check_health
  check_containers
  check_rfkill
  check_thread
}
```

`main` erweitern — nach `configure`:

```sh
  start_stack
  run_checks
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 35 passed.

**Hinweis für die Abnahme:** `check_rfkill` lässt sich hier nicht gezielt auslösen — auf macOS fehlt `/sys` ganz, auf CI-Runnern ist `/sys/class/rfkill` üblicherweise leer. Getestet ist nur, dass die Funktion beide Fälle übersteht. Der Befund selbst zeigt sich erst auf einem echten Pi.

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 5 und 6 - starten, pruefen, Befunde nennen

Kein --profile am compose-Aufruf: die Betriebsart steht als
COMPOSE_PROFILES in der .env, die Compose von sich aus liest - damit gilt
sie auch fuer jeden spaeteren Aufruf von Hand und fuer update.sh.

Die Pruefungen veraendern nichts. rfkill-Unblock und der
start-stop-daemon-Workaround werden ausgegeben, nicht ausgefuehrt: der eine
braucht faktisch root, der andere ist kernelspezifisch und wuerde auf
gesunden Hosts laufende Prozesse killen. Stilles Gelingen bei totem
Thread-Netz waere das schlechteste Ergebnis.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Phase 7 — Schlussbericht und Update-Angebot

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `FINDINGS`, `PORT`, `MODE`, `TARGET_DIR`, `CHECKOUT_EXISTED`, `INSTALLED_DOCKER`, `HAVE_TTY`, `ask`.
- Produces: `offer_update`, `report`. Damit ist `install.sh` vollständig.

- [ ] **Step 1: Write the failing tests**

```python
def test_bericht_nennt_die_weboberflaeche_und_das_passwort(installer):
    result = installer()
    assert result.returncode == 0
    assert "http://10.0.1.56:8080/" in result.output
    assert "set a password" in result.output


def test_thread_bericht_schlaegt_den_watchdog_vor(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert "otbr-watchdog.sh" in result.output
    assert str(result.home / "loxmatter") in result.output


def test_wifi_bericht_schlaegt_keinen_watchdog_vor(installer):
    result = installer()
    assert "otbr-watchdog.sh" not in result.output
    assert "COMPOSE_PROFILES=thread" in result.output  # so ruestet man nach


def test_zweiter_lauf_bietet_das_update_an_ohne_es_zu_tun(installer):
    first = installer()
    assert first.returncode == 0
    second = installer(env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    assert "3 new commits" in second.output
    assert "Apply them with" in second.output
    assert "update.sh" in second.output


def test_erster_lauf_prueft_nicht_auf_updates(installer):
    result = installer(env={"FAKE_BEHIND": "3"})
    assert "new commits" not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_script.py -v -k "bericht or update"`
Expected: FAIL — es gibt keinen Schlussbericht.

- [ ] **Step 3: Phase 7 implementieren**

Nach `run_checks` einfügen:

```sh
# ----------------------------------------------------------- phase seven --

# Only offered, never done on the way past: scripts/update.sh backs up the
# signal database first, and those keys are the wiring in the Loxone
# configuration. An installer that updates in passing would skip that backup.
offer_update() {
  if [ "$CHECKOUT_EXISTED" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  step "checking for updates"
  if ! git -C "$TARGET_DIR" fetch --quiet origin main 2>/dev/null; then
    note "Could not reach GitHub; skipping the update check."
    return 0
  fi
  behind="$(git -C "$TARGET_DIR" rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)"
  if [ "${behind:-0}" -le 0 ]; then
    return 0
  fi
  say "$behind new commits are available"
  if [ "$HAVE_TTY" -eq 0 ]; then
    note "Apply them with: $TARGET_DIR/scripts/update.sh"
    return 0
  fi
  update_answer="$(ask "Update now? It backs up the signal database first [y/N]" "N")"
  case "$update_answer" in
    y|Y|yes|Yes) "$TARGET_DIR/scripts/update.sh" ;;
    *) note "Left as it is. Run $TARGET_DIR/scripts/update.sh when you want it." ;;
  esac
}

report() {
  step "writing the summary"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "Dry run finished. Nothing was changed."
    return 0
  fi
  say "Done."
  lan_ip="$(hostname -I 2>/dev/null | awk '{ print $1 }' || true)"
  if [ -z "$lan_ip" ]; then
    lan_ip="<this host>"
  fi
  printf '  Web interface: http://%s:%s/\n' "$lan_ip" "$PORT"
  printf '  Open it and set a password. Until you do, no /api route answers -\n'
  printf '  there is no open state.\n'
  if [ "$MODE" = "thread" ]; then
    printf '\n  Keep an eye on the Thread radio - add this to `crontab -e`:\n'
    printf '    */5 * * * * %s/scripts/otbr-watchdog.sh >> %s/otbr-watchdog.log 2>&1\n' \
      "$TARGET_DIR" "$HOME"
  else
    printf '\n  Running WiFi and Ethernet only. To add Thread later: plug the radio in,\n'
    printf '  set COMPOSE_PROFILES=thread and RADIO_DEVICE in\n'
    printf '  %s/.env, then `docker compose up -d` there.\n' "$STACK_DIR"
  fi
  printf '\n  To update later: %s/scripts/update.sh\n' "$TARGET_DIR"
  if [ "$INSTALLED_DOCKER" -eq 1 ]; then
    printf '\n'
    warn "Docker was installed during this run. Log out and back in once, so"
    warn "that 'docker' works without sudo - scripts/update.sh needs that."
  fi
  if [ -n "$FINDINGS" ]; then
    say "Still needs a human"
    printf '%s\n' "$FINDINGS"
  fi
}
```

`main` vervollständigen — `offer_update` steht direkt nach `ensure_checkout`, damit eine Zustimmung noch vor dem Bauen wirkt:

```sh
main() {
  trap on_exit EXIT
  parse_args "$@"
  say "loxmatter installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "Dry run: every step is printed, nothing is changed."
  fi
  check_platform
  check_tty
  check_privileges
  collect_missing
  check_can_install
  decide_mode
  check_config_source
  install_packages
  install_docker
  ensure_checkout
  offer_update
  configure
  start_stack
  run_checks
  report
}

main "$@"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_script.py -v`
Expected: 40 passed.

- [ ] **Step 5: Vollständiger Durchlauf aller Prüfungen**

```bash
shellcheck -s sh install.sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
```
Expected: shellcheck ohne Ausgabe, `All checks passed!`, `N files already formatted`, `Success: no issues found`, alle Tests grün.

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): Phase 7 - Schlussbericht und Update-Angebot

Nennt die Adresse der Oberflaeche mit der LAN-IP des Hosts, die
Passwortvergabe als naechsten Schritt und - je nach Betriebsart - entweder
den Watchdog-Cron mit dem tatsaechlichen Installationspfad oder die zwei
Zeilen, mit denen sich Thread nachruesten laesst. Offene Befunde stehen
gesammelt am Ende, damit sie nicht zwischen den Build-Zeilen verschwinden.

Das Update wird nur angeboten, nie im Vorbeigehen erledigt: update.sh
sichert vorher die Signaldatenbank, und diese Schluessel sind die
Verdrahtung in der Loxone-Konfiguration.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Abnahme auf einem echten Host

Die Stub-Tests prüfen die Auswahl der Befehle, nicht ihre Wirkung. Vor dem Merge auf einem Raspberry Pi durchlaufen — beide Betriebsarten, jeweils mit einem frischen Verzeichnis:

1. `curl -fsSL $RAW_URL | sh -s -- --dry-run --dir ~/probe` — meldet Schritte, ändert nichts.
2. WiFi-Modus, Funkmodul gezogen: `curl -fsSL $RAW_URL | LOXMATTER_MODE=wifi MINISERVER_IP=… sh --dir ~/probe-wifi`. Erwartung: zwei Container, `/health` antwortet, kein Thread-Befund, WebUI erreichbar.
3. Thread-Modus mit gestecktem Modul, gegen ein frisches Verzeichnis. Erwartung: drei Container und — auf dem Pi-Kernel — der `start-stop-daemon`-Befund im Schlussbericht.
4. Denselben Befehl ein zweites Mal: kein neuer Klon, `.env` unverändert, Update-Angebot nur, wenn `main` weiter ist.
5. Der bestehende Produktivstack: `git pull` und `docker compose up -d` — der Dienst `otbr` muss weiterlaufen, weil seine `.env` `COMPOSE_PROFILES=thread` trägt (bzw. `install.sh` sie beim zweiten Lauf ergänzt hat).

Schritt 5 ist der wichtigste: er ist der einzige, der zeigt, dass die Profil-Umstellung eine laufende Installation nicht beschädigt.
