# Updates über die Oberfläche, Stufe 2: der Beiwagen und der Knopf

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Voraussetzung:** [Stufe 1](2026-09-08-webui-updates-stufe1.md) ist vollständig umgesetzt und die Version `0.2.0` ist als `ghcr.io/lucienkerl/loxmatter:stable` veröffentlicht. Ohne ein Image, auf das man aktualisieren kann, ist hier nichts zu testen — der Rückfall am wenigsten.

**Goal:** Ein Klick im System-Tab spielt eine neue Version ein, sichtbar Schritt für Schritt, auch während die Brücke selbst gerade nicht antwortet — und setzt sich selbsttätig zurück, wenn die neue Fassung nicht gesund hochkommt.

**Architecture:** Ein zusätzlicher Container `loxmatter-updater` hält den Docker-Socket, hat keine Ports und kein Host-Netz, und verständigt sich mit der Brücke ausschließlich über drei Dateien im gemeinsamen Volume. Weil der Zustand dort liegt und nicht im Speicher der Brücke, bleibt der Fortschritt während des Neustarts sichtbar. Die Sicherheitsgrenze liegt im Beiwagen, nicht am Login: er setzt den Image-Namen selbst zusammen, prüft das Ziel gegen ein festes Muster und lässt nur vorwärts.

**Tech Stack:** POSIX sh (busybox/Alpine), `jq`, Docker CLI + Compose-Plugin, git, curl, Python 3.12/FastAPI/Pydantic, Alpine.js, pytest.

**Grundlage:** [`docs/superpowers/specs/2026-09-08-webui-updates-design.md`](../specs/2026-09-08-webui-updates-design.md), Abschnitte 6–14.

## Global Constraints

- **Jede neue Quelldatei beginnt mit dem GPL-Kopf** in der englischen FSF-Formulierung, wortgleich zu bestehenden Dateien (Vorlage: `src/loxmatter/api/settings.py:1-15`). Für Shell-Dateien in der `#`-Kommentarform wie in `scripts/update.sh:1-16`.
- **Entwicklerprosa auf Deutsch**, dicht und begründend.
- **Jeder nutzersichtbare Text über `i18n.t()`** mit `en`- **und** `de`-Eintrag in `src/loxmatter/i18n/strings.yaml`.
- **POSIX sh, nicht bash.** Das Updater-Image ist Alpine; `/bin/sh` ist busybox, und ein `[[` stirbt dort. Jede neue Shell-Datei muss `shellcheck -s sh` bestehen.
- **Feste Namen**, überall gleich: Volume-Pfad `/data/update/`, Dateien `request.json`, `state.json`, `log.txt`, `LETZTER-FEHLSCHLAG.txt`; Dienstname `loxmatter-updater`; Registry `ghcr.io/lucienkerl/loxmatter` und `ghcr.io/lucienkerl/loxmatter-updater`.
- **Phasen** (der Wert von `state.json.phase`), abschließend: `idle`, `queued`, `backup`, `pull`, `recreate`, `health`, `rollback`, `done`, `failed`, `rejected`.
- **Der Updater interpretiert nie Text aus dem Auftrag als Befehl.** Kein `eval`, keine Variable aus `request.json` in einer Kommandoposition, kein Image-Name aus dem Auftrag.
- **Die volle Testsuite braucht rund drei Minuten.** Nicht abbrechen.
- **Vor jedem Commit:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, und für Shell-Änderungen `shellcheck -s sh <datei>`.
- **Jeder Test muss einmal probeweise scheitern.**

## File Structure

| Datei | Verantwortung |
|---|---|
| `deploy/updater/Dockerfile` (neu) | Das Beiwagen-Image: Alpine + docker-cli + compose + git + curl + jq + coreutils. |
| `deploy/updater/entrypoint.sh` (neu) | Die Schleife. Drei Zeilen: einmal arbeiten, zwei Sekunden schlafen. |
| `deploy/updater/update-once.sh` (neu) | Ein Durchlauf: Lebenszeichen, Auftrag prüfen, ausführen, Zustand schreiben. Der ganze Verstand des Beiwagens, und alles davon testbar ohne Schleife. |
| `deploy/testhost/docker-compose.yml` (ändern) | Der neue Dienst. |
| `.github/workflows/ci.yml` (ändern) | Zweiter Image-Job für den Beiwagen. |
| `src/loxmatter/update.py` (neu) | Auftrag schreiben (atomar), Zustand lesen, Anwesenheit des Beiwagens beurteilen. Kennt keine HTTP-Begriffe. |
| `src/loxmatter/update_check.py` (neu) | Fragt GitHub nach Release bzw. `main`. Kennt keine Dateien. |
| `src/loxmatter/api/update.py` (neu) | Die drei Routen. Klebt die beiden Module an HTTP, sonst nichts. |
| `src/loxmatter/model/store.py` (ändern) | Kanaleinstellung und „Prüfung erlaubt"-Schalter, wie die übrigen Einstellungen. |
| `src/loxmatter/web/index.html`, `app.js`, `i18n/strings.yaml` (ändern) | Die Karte mit vier Zuständen. |
| `tests/test_updater_script.py`, `tests/test_update_module.py`, `tests/api/test_update_api.py`, `tests/test_update_check.py` (neu) | siehe jeweilige Task. |

**Warum drei Python-Module und nicht eines:** `update.py` fasst Dateien an, `update_check.py` das Netz, `api/update.py` weder noch. Getrennt lässt sich jedes für sich testen — der Netzteil ohne Dateisystem, der Dateiteil ohne Netz, und die Routen gegen beide als Attrappe.

---

### Task 1: Das Beiwagen-Image

**Files:**
- Create: `deploy/updater/Dockerfile`, `deploy/updater/entrypoint.sh`
- Modify: `.github/workflows/ci.yml` (zweiter Job `updater-image`)
- Test: `tests/test_updater_image.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `ghcr.io/lucienkerl/loxmatter-updater:<version>` und `:stable`. Der Einstiegspunkt ruft `/opt/loxmatter/update-once.sh` (Task 2) in einer Schleife.

- [ ] **Step 1: Write the failing test**

`tests/test_updater_image.py` (GPL-Kopf, dann):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_updater_image.py -v`
Expected: FAIL — `FileNotFoundError` für `deploy/updater/Dockerfile`

- [ ] **Step 3: Write the Dockerfile**

`deploy/updater/Dockerfile`:

```dockerfile
# Der Updater-Beiwagen (Entwurf "Updates ueber die Oberflaeche einspielen",
# 2026-09-08, Abschnitt 6).
#
# Dieser Container haelt den Docker-Socket und ist damit root-gleichwertig
# auf dem Host. Abgesichert wird das durch Enge, nicht durch Rechte: keine
# Ports, kein Host-Netz (siehe Compose-Datei), ein einziges festes
# Arbeitsprogramm - und diese Basis hier gepinnt statt `latest`, damit ein
# Neubau nie eine Ueberraschung ist.
#
# Bewusst NICHT das loxmatter-Image mit anderem Einstiegspunkt: der
# Beiwagen darf nicht dasselbe Image sein wie das, das er austauscht, sonst
# ersetzt ein Update den Prozess, der es gerade ausfuehrt.
FROM alpine:3.20

# docker-cli-compose bringt das `docker compose`-Unterkommando mit - der
# Beiwagen ruft ausschliesslich Compose auf, nie ein nacktes `docker run`
# oder `docker build`. Der Grund steht seit 2026-09-03 in scripts/update.sh:
# der Dienst baut ueber seinen `build:`-Block sein eigenes Image, und ein
# daneben gebautes benutzt niemand.
#
# coreutils wegen `sort -V`: die Pruefung "nur vorwaerts" (Spec-Abschnitt
# 10, Regel 3) vergleicht semantische Versionen, und busybox' sort kann
# das nicht verlaesslich.
RUN apk add --no-cache \
      docker-cli \
      docker-cli-compose \
      git \
      curl \
      jq \
      coreutils \
      tar

COPY entrypoint.sh update-once.sh /opt/loxmatter/
RUN chmod +x /opt/loxmatter/entrypoint.sh /opt/loxmatter/update-once.sh

ENTRYPOINT ["/opt/loxmatter/entrypoint.sh"]
```

- [ ] **Step 4: Write the entrypoint**

`deploy/updater/entrypoint.sh` (GPL-Kopf in `#`-Form, dann):

```sh
#!/bin/sh
# Die Schleife des Beiwagens - Entwurf "Updates ueber die Oberflaeche
# einspielen" (2026-09-08), Abschnitt 6.
#
# Bewusst duenn: aller Verstand steckt in update-once.sh, und zwar
# vollstaendig. Nur so laesst sich ein Durchlauf im Test aufrufen, ohne
# eine Endlosschleife anzuwerfen und wieder abzuwuergen.
#
# `|| true`: ein einzelner misslungener Durchlauf darf den Beiwagen nicht
# beenden. Er ist der Einzige, der einen kaputten Zustand ueberhaupt noch
# melden kann - ein Container, der sich bei einem Fehler beendet, nimmt
# genau diese Meldung mit.
set -u

while true; do
  /opt/loxmatter/update-once.sh || true
  sleep 2
done
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_image.py -v && shellcheck -s sh deploy/updater/entrypoint.sh`
Expected: 2 passed, shellcheck still

- [ ] **Step 6: Add the CI job**

In `.github/workflows/ci.yml`, nach dem Job `image`:

```yaml

  # Der Beiwagen. Eigener Job und eigenes Image, weil er NICHT dasselbe
  # Image sein darf wie das, das er austauscht: sonst ersetzte ein Update
  # den Prozess, der es gerade ausfuehrt. Er aendert sich selten - deshalb
  # ohne :dev-Tag, nur auf Releases.
  updater-image:
    needs: test
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: deploy/updater
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/lucienkerl/loxmatter-updater:${{ github.ref_name }}
            ghcr.io/lucienkerl/loxmatter-updater:stable
```

- [ ] **Step 7: Commit**

```bash
git add deploy/updater/ .github/workflows/ci.yml tests/test_updater_image.py
git commit -m "feat(updater): Beiwagen-Image und Schleife

Ein eigenes, winziges Image - bewusst NICHT das loxmatter-Image mit
anderem Einstiegspunkt: der Beiwagen darf nicht dasselbe Image sein wie
das, das er austauscht, sonst ersetzt ein Update den Prozess, der es
gerade ausfuehrt.

Die Basis gepinnt statt latest. Bei einem Container, der den Docker-Socket
haelt und damit root-gleichwertig auf dem Host steht, ist ein
ueberraschender Neubau die falsche Sorte Bequemlichkeit.

Die Schleife bleibt duenn, aller Verstand liegt in update-once.sh: nur so
laesst sich ein Durchlauf testen, ohne eine Endlosschleife anzuwerfen. Ein
misslungener Durchlauf beendet den Beiwagen nicht - er ist der Einzige,
der einen kaputten Zustand noch melden kann.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Lebenszeichen und Auftragsprüfung — die Sicherheitsgrenze

**Files:**
- Create: `deploy/updater/update-once.sh`
- Test: `tests/test_updater_script.py`

**Interfaces:**
- Consumes: die Umgebungsvariablen aus der Compose-Datei (Task 5), hier bereits mit Vorgaben belegt.
- Produces: `update-once.sh` — ein Durchlauf. Schreibt `state.json` mit mindestens `{phase, updater_seen_at}`, verarbeitet eine `id` genau einmal. Task 3 und 4 erweitern dieselbe Datei.

- [ ] **Step 1: Write the failing tests**

`tests/test_updater_script.py` (GPL-Kopf, dann):

```python
"""Verhaltenstests fuer den Beiwagen.

Dasselbe Verfahren wie bei `test_install_script.py` und
`test_update_script.py`: versiegelter PATH aus gefaelschten Binaries, und
geprueft wird, WELCHE Befehle das Skript waehlt.

Die Tests um `test_ein_ziel_mit_semikolon_*` sind der Kern dieser Datei.
Sie belegen die Aussage aus Spec-Abschnitt 10 - "selbst wer die Bruecke
vollstaendig uebernimmt, kann hoechstens eine veroeffentlichte, neuere
Version installieren". Ohne sie waere das eine Behauptung. Entscheidend
ist dabei nicht nur, DASS abgelehnt wird, sondern dass im Aufrufprotokoll
KEIN EINZIGER docker-Aufruf steht: eine Ablehnung, die vorher schon etwas
getan hat, ist keine."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "update-once.sh"

SYSTEM_TOOLS = ("sh", "cat", "grep", "sed", "awk", "tr", "printf", "mkdir", "rm", "mv", "date", "sort", "head", "tail", "jq", "sleep", "seq", "ls", "cut")


@pytest.fixture
def updater(tmp_path):
    """Liefert `run(**env)` -> (result, calls, state). `calls` ist das
    Protokoll aller gefaelschten Werkzeuge, `state` der geschriebene
    Zustand als dict (oder None)."""
    bindir, sysdir = tmp_path / "bin", tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "data" / "update"
    update_dir.mkdir(parents=True)
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    (stack / ".env").write_text("LOXMATTER_IMAGE_TAG=0.2.0\n", encoding="utf-8")

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8")
        path.chmod(0o755)

    stub("docker")
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')
    stub("tar")

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(**extra_env):
        env = {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_BACKUP_DIR": str(tmp_path / "data" / "backups"),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_REPO": str(tmp_path / "repo"),
            "LOXMATTER_HEALTH_TIMEOUT": "3",
            **extra_env,
        }
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        return result, calls, state

    run.update_dir = update_dir
    run.stack = stack
    return run


def _auftrag(updater, **fields) -> None:
    body = {"id": "auftrag-1", "channel": "stable", "target": "0.3.0", "requested_at": "2026-09-08T10:00:00Z"}
    body.update(fields)
    (updater.update_dir / "request.json").write_text(json.dumps(body), encoding="utf-8")


def test_ohne_auftrag_schreibt_es_nur_ein_lebenszeichen(updater):
    _, calls, state = updater()
    assert state["phase"] == "idle"
    assert state["updater_seen_at"]
    assert "docker" not in calls


def test_das_lebenszeichen_kommt_bei_jedem_durchlauf(updater):
    _, _, erst = updater()
    _, _, dann = updater()
    assert dann["updater_seen_at"] >= erst["updater_seen_at"]


def test_ein_ziel_mit_semikolon_wird_abgelehnt(updater):
    _auftrag(updater, target="0.3.0; rm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_ein_ziel_mit_fremder_registry_wird_abgelehnt(updater):
    # Der Image-Name wird im Skript zusammengesetzt, nie uebernommen. Ein
    # Ziel, das wie ein Image aussieht, ist deshalb schlicht kein
    # gueltiges Ziel.
    _auftrag(updater, target="evil.example.com/loxmatter:latest")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_ein_unbekannter_kanal_wird_abgelehnt(updater):
    _auftrag(updater, channel="beliebig")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_eine_aeltere_version_wird_abgelehnt(updater):
    # "Nur vorwaerts", Spec-Abschnitt 10, Regel 3. .env steht auf 0.2.0.
    _auftrag(updater, target="0.1.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_dieselbe_version_wird_abgelehnt(updater):
    _auftrag(updater, target="0.2.0")
    _, _, state = updater()
    assert state["phase"] == "rejected"


def test_ein_gueltiges_ziel_wird_angenommen(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] != "rejected"
    assert "docker" in calls


def test_derselbe_auftrag_wird_nicht_zweimal_ausgefuehrt(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    _, zweite_calls, _ = updater()
    assert "compose pull" not in zweite_calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: FAIL — alle, `No such file or directory` für `update-once.sh`

- [ ] **Step 3: Write the script's skeleton, heartbeat and validation**

`deploy/updater/update-once.sh` (GPL-Kopf in `#`-Form, dann):

```sh
#!/bin/sh
# Ein Durchlauf des Beiwagens - Entwurf "Updates ueber die Oberflaeche
# einspielen" (2026-09-08), Abschnitte 6 bis 8 und 10.
#
# Aufgerufen alle zwei Sekunden von entrypoint.sh. Tut drei Dinge, in
# dieser Reihenfolge: Lebenszeichen auffrischen, einen neuen Auftrag
# pruefen, ihn ausfuehren.
#
# WAS DIESES SKRIPT NIE TUT: Text aus request.json in eine
# Kommandoposition setzen. Kein eval, kein "$TARGET" als Teil eines
# Befehls, kein Image-Name aus dem Auftrag. Der Image-Name wird unten fest
# zusammengesetzt; aus dem Auftrag kommt nur eine Zeichenkette, die vorher
# gegen ein festes Muster geprueft wurde. Das ist die Sicherheitsgrenze
# dieser Loesung - nicht das Login davor.
set -eu

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
BACKUP_DIR="${LOXMATTER_BACKUP_DIR:-/data/backups}"
STACK="${LOXMATTER_STACK:-/repo/deploy/testhost}"
REPO="${LOXMATTER_REPO:-/repo}"
SERVICE="${LOXMATTER_SERVICE:-loxmatter}"
IMAGE="${LOXMATTER_IMAGE:-ghcr.io/lucienkerl/loxmatter}"
HEALTH_URL="${LOXMATTER_HEALTH_URL:-http://host.docker.internal:8080/health}"
HEALTH_TIMEOUT="${LOXMATTER_HEALTH_TIMEOUT:-120}"

REQUEST="$UPDATE_DIR/request.json"
STATE="$UPDATE_DIR/state.json"
LOG="$UPDATE_DIR/log.txt"
FAILURE="$UPDATE_DIR/LETZTER-FEHLSCHLAG.txt"
ENV_FILE="$STACK/.env"

mkdir -p "$UPDATE_DIR" "$BACKUP_DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Atomar, immer. Die Bruecke liest diese Datei im Sekundentakt und darf nie
# eine halb geschriebene sehen - ein abgeschnittenes JSON waere fuer sie
# nicht von "kein Beiwagen da" zu unterscheiden.
write_state() {
  printf '%s\n' "$1" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

log() {
  printf '%s %s\n' "$(now)" "$*" >> "$LOG"
  tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
}

# Was LAEUFT, nicht was beim naechsten Start gezogen wuerde. Das sind zwei
# verschiedene Fragen, und Stufe 1 hat gezeigt, dass die Verwechslung teuer
# ist: seit 0.2.0 traegt jede frische Installation LOXMATTER_IMAGE_TAG=stable
# in der .env (deploy/testhost/.env.example). Der Tag ist ein WANDERNDER
# ALIAS - "stable" ist keine Version, und ein Vergleich "ist 0.3.0 neuer als
# stable" hat keine Antwort. Genau daran waere die Vorwaerts-Pruefung aus
# Spec-Abschnitt 10, Regel 3, auf jeder Standardinstallation stillschweigend
# vorbeigelaufen.
#
# Die belastbare Auskunft gibt der laufende Container selbst: sein
# LOXMATTER_VERSION ist beim Bau hineingelegt worden (Stufe 1, Abschnitt 4)
# und benennt genau die Fassung, die gerade arbeitet - unabhaengig davon,
# unter welchem Alias sie einmal gezogen wurde.
running_version() {
  version="$(docker inspect "$SERVICE" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed -n -E 's/^LOXMATTER_VERSION=(.+)$/\1/p' | head -1)"
  printf '%s' "${version:-unbekannt}"
}

# Der Tag aus der .env - gebraucht wird er nur noch fuer den Rueckfall, also
# um zu wissen, was zurueckzuschreiben ist, wenn das Update scheitert.
current_tag() {
  tag="$(sed -n -E 's/^LOXMATTER_IMAGE_TAG=(.*)$/\1/p' "$ENV_FILE" 2>/dev/null | tail -1)"
  printf '%s' "${tag:-stable}"
}

set_state() {
  # $1 Phase, $2 Fehlermeldung (darf leer sein)
  write_state "$(jq -n \
    --arg id "${JOB_ID:-}" --arg phase "$1" --arg error "${2:-}" \
    --arg from "${FROM:-}" --arg to "${TO:-}" --arg seen "$(now)" \
    --argjson rolled "${ROLLED:-false}" --argjson healthy "${HEALTHY:-true}" \
    '{id: (if $id == "" then null else $id end),
      phase: $phase,
      from: (if $from == "" then null else $from end),
      to:   (if $to == "" then null else $to end),
      error: (if $error == "" then null else $error end),
      rolled_back: $rolled,
      healthy: $healthy,
      updater_seen_at: $seen}')"
}

# ------------------------------------------------------- Lebenszeichen --
# Zuerst, vor allem anderen: die Bruecke blendet den Update-Knopf aus,
# wenn dieser Zeitstempel veraltet (siehe update.py). Ein Beiwagen, der
# erst nach getaner Arbeit ein Lebenszeichen gibt, sieht waehrend jeder
# Arbeit aus wie ein abwesender.
if [ -f "$STATE" ]; then
  write_state "$(jq --arg seen "$(now)" '.updater_seen_at = $seen' "$STATE")"
else
  JOB_ID="" FROM="" TO="" set_state idle ""
fi

[ -f "$REQUEST" ] || exit 0

JOB_ID="$(jq -r '.id // empty' "$REQUEST" 2>/dev/null || true)"
CHANNEL="$(jq -r '.channel // empty' "$REQUEST" 2>/dev/null || true)"
TARGET="$(jq -r '.target // empty' "$REQUEST" 2>/dev/null || true)"

# Genau einmal. Ohne das arbeitete der Beiwagen denselben Auftrag alle zwei
# Sekunden erneut ab - und ein Update, das sich selbst neu startet, kommt
# nie zur Ruhe.
LAST="$(jq -r '.id // empty' "$STATE" 2>/dev/null || true)"
[ -n "$JOB_ID" ] || exit 0
[ "$JOB_ID" != "$LAST" ] || exit 0

# Zwei verschiedene Dinge, bewusst getrennt gehalten:
#   RUNNING - welche Fassung arbeitet gerade (fuer die Vorwaerts-Pruefung
#             und fuer die Anzeige)
#   FROM    - was in der .env steht und beim Rueckfall zurueckzuschreiben
#             waere (kann ein Alias wie "stable" sein)
RUNNING="$(running_version)"
FROM="$(current_tag)"
TO="$TARGET"
ROLLED=false
HEALTHY=true

reject() {
  log "Auftrag $JOB_ID abgelehnt: $1"
  set_state rejected "$1"
  exit 0
}

# ------------------------------------------------------------ Pruefung --
# Regel 1: Kanal ist ein Enum, Ziel muss ein Muster erfuellen.
case "$CHANNEL" in
  stable|dev) ;;
  *) reject "unbekannter Kanal" ;;
esac

case "$CHANNEL" in
  stable)
    printf '%s' "$TARGET" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
      || reject "kein gueltiges Versionsziel" ;;
  dev)
    printf '%s' "$TARGET" | grep -Eq '^[0-9a-f]{7,40}$' \
      || reject "kein gueltiges Commit-Ziel" ;;
esac

# Regel 3: nur vorwaerts. Im stabilen Kanal nach semantischer Version;
# `sort -V` aus coreutils, busybox' sort kann das nicht verlaesslich. Der
# dev-Kanal hat keine Ordnung ueber SHAs - dort prueft Task 3 stattdessen
# die Abstammung, sobald die Refs geholt sind.
if [ "$CHANNEL" = "stable" ]; then
  # Verglichen wird gegen die LAUFENDE Version, nicht gegen den Tag in der
  # .env - siehe running_version() oben. Ein Tag kann "stable" heissen und
  # damit gar keine Version sein.
  CUR="${RUNNING#v}"
  NEW="${TARGET#v}"
  # Eine Fassung, die sich nicht zu erkennen gibt (von Hand gebautes Image,
  # LOXMATTER_VERSION leer), kann nicht Ausgangspunkt eines Vergleichs sein.
  # Ablehnen statt raten: der Nutzer sieht dann, dass er ein Image faehrt,
  # das seine Herkunft nicht nennt - eine brauchbare Auskunft.
  case "$CUR" in
    ''|unbekannt|dev) reject "die laufende Fassung nennt keine Version - Update nur ueber die Konsole" ;;
  esac
  if [ "$CUR" = "$NEW" ]; then
    reject "diese Version laeuft bereits"
  fi
  if [ "$(printf '%s\n%s\n' "$CUR" "$NEW" | sort -V | head -1)" != "$CUR" ]; then
    reject "aeltere Version - zurueck geht nur der Rueckfall"
  fi
fi

set_state queued ""
log "Auftrag $JOB_ID angenommen: $FROM -> $TO ($CHANNEL)"
```

- [ ] **Step 4: Run tests to verify the rejection tests pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: alle bis auf `test_ein_gueltiges_ziel_wird_angenommen` und `test_derselbe_auftrag_wird_nicht_zweimal_ausgefuehrt` bestehen (die brauchen Task 3 — bis dahin ruft das Skript noch kein `docker` auf). Diese beiden **erwartet FAIL** lassen und in Task 3 grün ziehen.

- [ ] **Step 5: Prove the security tests can fail**

Kommentiere probeweise die `case "$CHANNEL"`-Musterprüfung für `stable` aus, führe `uv run pytest tests/test_updater_script.py -v` aus.
Expected: `test_ein_ziel_mit_semikolon_wird_abgelehnt` und `test_ein_ziel_mit_fremder_registry_wird_abgelehnt` FAILEN. Danach zurücknehmen.

- [ ] **Step 6: shellcheck**

Run: `shellcheck -s sh deploy/updater/update-once.sh`
Expected: keine Meldungen

- [ ] **Step 7: Commit**

```bash
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): Lebenszeichen und Auftragspruefung

Die Sicherheitsgrenze dieser Loesung liegt hier, nicht am Login: Kanal ist
ein Enum, das Ziel muss ein festes Muster erfuellen, und weiter geht es
nur vorwaerts. Der Image-Name wird spaeter im Skript zusammengesetzt, nie
aus dem Auftrag uebernommen - ein Ziel, das wie ein Image aussieht, ist
deshalb schlicht kein gueltiges Ziel.

Die Tests belegen nicht nur, DASS abgelehnt wird, sondern dass im
Aufrufprotokoll kein einziger docker-Aufruf steht. Eine Ablehnung, die
vorher schon etwas getan hat, ist keine.

Das Lebenszeichen steht bewusst VOR der Auftragspruefung: die Bruecke
blendet den Knopf aus, wenn es veraltet, und ein Beiwagen, der erst nach
getaner Arbeit ein Lebenszeichen gibt, saehe waehrend jeder Arbeit wie ein
abwesender aus.

Zwei Tests bleiben vorerst rot - sie brauchen den Ablauf aus dem naechsten
Commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Der Ablauf — sichern, ziehen, neu starten, gesund werden

**Files:**
- Modify: `deploy/updater/update-once.sh` (anhängen)
- Test: `tests/test_updater_script.py` (anhängen)

**Interfaces:**
- Consumes: `JOB_ID`, `FROM`, `TO`, `CHANNEL` aus Task 2.
- Produces: die Phasenfolge `backup` → `pull` → `recreate` → `health` → `done`, und `set_tag <wert>`, das `LOXMATTER_IMAGE_TAG` in der `.env` setzt. **Task 4 benutzt `set_tag` für den Rückfall.**

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_updater_script.py`:

```python
def test_der_ablauf_haelt_seine_reihenfolge_ein(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert calls.index("tar") < calls.index("compose pull")
    assert calls.index("compose pull") < calls.index("compose up")
    assert state["phase"] == "done"


def test_der_neustart_laesst_die_nachbardienste_in_ruhe(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    up = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up
    assert "loxmatter-updater" not in up


def test_der_image_name_kommt_nicht_aus_dem_auftrag(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    assert "ghcr.io/lucienkerl/loxmatter" in calls


def test_der_tag_landet_in_der_env(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in (updater.stack / ".env").read_text(encoding="utf-8")


def test_die_env_behaelt_ihre_uebrigen_zeilen(updater):
    # Die .env traegt MINISERVER_IP, RADIO_DEVICE, LOXMATTER_API_TOKEN. Ein
    # Update, das sie ueberschreibt, nimmt die halbe Installation mit.
    env = updater.stack / ".env"
    env.write_text("MINISERVER_IP=10.0.1.9\nLOXMATTER_IMAGE_TAG=0.2.0\nRADIO_DEVICE=/dev/ttyUSB0\n", encoding="utf-8")
    _auftrag(updater, target="0.3.0")
    updater()
    text = env.read_text(encoding="utf-8")
    assert "MINISERVER_IP=10.0.1.9" in text
    assert "RADIO_DEVICE=/dev/ttyUSB0" in text
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in text
    assert "0.2.0" not in text


def test_eine_sicherung_entsteht_vor_dem_ziehen(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    tar_line = next(line for line in calls.splitlines() if line.startswith("tar"))
    assert "store-" in tar_line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: die sechs neuen FAILEN (`ValueError: substring not found` bzw. `phase == 'queued'`)

- [ ] **Step 3: Append the flow to the script**

Ans Ende von `deploy/updater/update-once.sh`:

```sh
# ------------------------------------------------------------- Ablauf --

run() {
  log "\$ $*"
  "$@" >> "$LOG" 2>&1
}

# Ersetzt GENAU die eine Zeile und laesst den Rest der .env unberuehrt.
# Dort stehen MINISERVER_IP, RADIO_DEVICE und das API-Token - ein Update,
# das die Datei neu schreibt, nimmt die halbe Installation mit.
set_tag() {
  if grep -q '^LOXMATTER_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null; then
    sed -i -E "s|^LOXMATTER_IMAGE_TAG=.*|LOXMATTER_IMAGE_TAG=$1|" "$ENV_FILE"
  else
    printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" >> "$ENV_FILE"
  fi
}

# Wartet auf den ersten gesunden Ton. Die 120 Sekunden sind kein neuer
# Wert, sondern der aus scripts/update.sh - und die Begruendung dort gilt
# unveraendert: 20 Sekunden gingen genau so lange gut, bis ein Lauf am
# 8. September knapp darueber kippte und das Skript einen Dienst als krank
# meldete, der zehn Sekunden spaeter tadellos lief. Ein zu kurzes Fenster
# ist hier die teurere Sorte Fehlalarm - es sieht aus wie ein kaputtes
# Update und verleitet zum Zuruecksetzen eines Standes, der in Ordnung ist.
wait_healthy() {
  i=0
  while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
    if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

# 1. Sichern. Vor allem anderen: die Signaldatenbank ist das Einzige, was
# ein misslungenes Update nicht wiederherstellen koennte - darin stehen die
# Signalschluessel, und die sind die Verdrahtung in der Loxone-Konfiguration.
set_state backup ""
STAMP="$(date -u +%Y-%m-%d-%H%M%S)"
if ! run tar czf "$BACKUP_DIR/store-$STAMP.tgz" -C /data loxmatter.sqlite; then
  set_state failed "Sicherung fehlgeschlagen - es wurde nichts geaendert"
  exit 0
fi
# Nie die letzten zehn wegraeumen, wie in scripts/update.sh.
ls -1t "$BACKUP_DIR"/store-*.tgz 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done

# 2. Ziel holen. Die Compose-Datei muss zur Version passen: eine neue
# Fassung kann einen neuen Dienst oder eine neue Variable brauchen.
set_state pull ""
if ! run git -C "$REPO" fetch --tags --force origin; then
  set_state failed "git fetch fehlgeschlagen"
  exit 0
fi

if [ "$CHANNEL" = "dev" ]; then
  # Die Entsprechung zu "nur vorwaerts" fuer den dev-Kanal (Spec-Abschnitt
  # 10, Regel 3): ueber SHAs gibt es keine Ordnung, wohl aber Abstammung.
  if ! git -C "$REPO" merge-base --is-ancestor HEAD "$TARGET" 2>/dev/null; then
    reject "kein Nachfahre des laufenden Standes"
  fi
  REF="$TARGET"
else
  REF="v${TARGET#v}"
fi

if ! run git -C "$REPO" checkout --detach "$REF"; then
  set_state failed "Ziel $REF im Repository nicht gefunden"
  exit 0
fi

# Der Image-Name wird HIER zusammengesetzt, aus einer festen Konstante und
# einem geprueften Ziel - er kommt nie aus dem Auftrag (Spec-Abschnitt 10,
# Regel 2). Deshalb steht $IMAGE unten auch nur im Log, nicht als Argument:
# den Namen bildet Compose aus der .env-Zeile, die set_tag geschrieben hat.
log "Ziel-Image: $IMAGE:${TARGET#v}"
set_tag "${TARGET#v}"

if ! run docker compose --project-directory "$STACK" pull "$SERVICE"; then
  set_tag "$FROM"
  set_state failed "Image nicht ladbar - der laufende Dienst bleibt unveraendert"
  exit 0
fi

# 3. Austauschen. --no-deps: matter-server und OTBR bleiben unangetastet,
# und der Beiwagen tauscht sich nicht selbst aus - das beendete ihn mitten
# im eigenen Auftrag.
set_state recreate ""
if ! run docker compose --project-directory "$STACK" up -d --no-deps --force-recreate "$SERVICE"; then
  set_state failed "Neustart fehlgeschlagen"
  exit 0
fi

# 4. Auf den ersten gesunden Ton warten.
set_state health ""
if wait_healthy; then
  set_state done ""
  log "Update auf $TO abgeschlossen"
  exit 0
fi

set_state rollback ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: alle bestehen (die Attrappe `curl` antwortet sofort gesund, also endet der Lauf bei `done`)

- [ ] **Step 5: Prove the `.env` test can fail**

Ersetze `set_tag` probeweise durch `printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" > "$ENV_FILE"` und führe die Tests aus.
Expected: `test_die_env_behaelt_ihre_uebrigen_zeilen` FAILT. Danach zurücknehmen.

- [ ] **Step 6: shellcheck, commit**

```bash
shellcheck -s sh deploy/updater/update-once.sh
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): sichern, ziehen, neu starten, gesund werden

Die Reihenfolge ist die von scripts/update.sh, samt der 120 Sekunden und
ihrer Begruendung: 20 gingen genau so lange gut, bis ein Lauf am
8. September knapp darueber kippte und einen Dienst als krank meldete, der
zehn Sekunden spaeter tadellos lief.

set_tag ersetzt genau eine Zeile der .env statt sie neu zu schreiben -
dort stehen MINISERVER_IP, RADIO_DEVICE und das API-Token, und ein Update,
das die Datei ueberbuegelt, nimmt die halbe Installation mit. Ein Test
haelt das fest.

Der dev-Kanal bekommt hier seine Entsprechung zu 'nur vorwaerts': ueber
SHAs gibt es keine Ordnung, wohl aber Abstammung - merge-base
--is-ancestor gegen den laufenden Stand.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Der Rückfall, die Klartextdatei und der Selbstaustausch

**Files:**
- Modify: `deploy/updater/update-once.sh` (anhängen)
- Test: `tests/test_updater_script.py` (anhängen)

**Interfaces:**
- Consumes: `set_tag`, `wait_healthy`, `run`, `FROM`, `TO` aus Task 3.
- Produces: Endphasen `done` und `failed` mit `rolled_back` und `healthy`; die Datei `LETZTER-FEHLSCHLAG.txt`.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_updater_script.py`:

```python
@pytest.fixture
def kranker_dienst(updater, tmp_path):
    """Dieselbe Umgebung, aber `curl` antwortet nie gesund - der Fall, fuer
    den es den Rueckfall gibt."""
    curl = tmp_path / "bin" / "curl"
    curl.write_text('#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$STUB_LOG"\nexit 7\n', encoding="utf-8")
    curl.chmod(0o755)
    return updater


def test_ein_kranker_dienst_wird_zurueckgesetzt(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    _, _, state = kranker_dienst()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True


def test_der_rueckfall_setzt_den_alten_tag_zurueck(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    kranker_dienst()
    assert "LOXMATTER_IMAGE_TAG=0.2.0" in (kranker_dienst.stack / ".env").read_text(encoding="utf-8")


def test_der_rueckfall_laeuft_genau_einmal(kranker_dienst):
    # Kein Flattern: zwei `up`-Aufrufe (Update und Rueckfall), nicht mehr.
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst()
    assert len([line for line in calls.splitlines() if "compose up" in line]) == 2


def test_der_rueckfall_ruehrt_die_datenbank_nicht_an(kranker_dienst):
    # Spec-Abschnitt 8: die alte Fassung laeuft auf dem neuen Schema
    # (`_migrate` kehrt bei version >= _SCHEMA_VERSION sofort zurueck).
    # Die Sicherung zurueckzuspielen ist der destruktivere Schritt und
    # bleibt eine ausdrueckliche Handlung in der Oberflaeche.
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst()
    # Genau auf das Entpacken pruefen, nicht auf ein beliebiges "-x": das
    # traefe sonst jeden kuenftigen Aufruf, der zufaellig ein -x-Flag traegt,
    # und der Test wuerde aus einem Grund rot, der mit seiner Aussage nichts
    # zu tun hat.
    tar_aufrufe = [line for line in calls.splitlines() if line.startswith("tar ")]
    assert tar_aufrufe, "die Sicherung selbst muss stattgefunden haben"
    for line in tar_aufrufe:
        assert " -x" not in line and "xzf" not in line, line


def test_ein_fehlschlag_hinterlaesst_eine_lesbare_datei(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    kranker_dienst()
    text = (kranker_dienst.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert "0.2.0" in text
    assert "0.3.0" in text
    assert "scripts/update.sh" in text


def test_ein_gelungenes_update_hinterlaesst_keine_fehlschlagdatei(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    assert not (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: die sechs neuen FAILEN — der Lauf endet bislang in `rollback` und tut dort nichts

- [ ] **Step 3: Append the rollback**

Ans Ende von `deploy/updater/update-once.sh` (die Zeile `set_state rollback ""` aus Task 3 bleibt stehen und wird damit fortgesetzt):

```sh
# ----------------------------------------------------------- Rueckfall --
# Ausgeloest, weil /health binnen HEALTH_TIMEOUT nicht antwortete.
#
# Was hier BEWUSST NICHT passiert: die Datenbank wird nicht
# zurueckgespielt. `_migrate` in model/store.py kehrt bei
# `version >= _SCHEMA_VERSION` sofort zurueck - die alte Fassung startet
# also auf dem neuen Schema, und da alle bisherigen Migrationen
# ALTER TABLE ADD COLUMN sind (in SQLite zwingend nullable oder mit
# Vorgabewert), schreibt sie weiter gueltige Zeilen. Der Image-Rueckfall
# allein genuegt, um das Haus wieder ans Laufen zu bringen.
#
# Die Sicherung zurueckzuspielen waere der destruktivere Schritt: er
# verwirft alles seit dem Sicherungszeitpunkt. Das tut man nicht
# selbsttaetig um zwei Uhr nachts, wenn niemand hinsieht - es steht in der
# Oberflaeche als eigener, ausdruecklich zu bestaetigender Knopf.
# WOHIN zurueckgefallen wird, ist nicht dasselbe wie WOHER das Update kam.
# Stand in der .env ein wandernder Alias ("stable", der Normalfall auf jeder
# frischen Installation seit 0.2.0), dann zeigt dieser Alias in der Registry
# INZWISCHEN AUF DIE GESCHEITERTE VERSION. Ihn zurueckzuschreiben hiesse,
# beim naechsten `compose pull` genau den Stand wiederzuholen, der gerade
# nicht gesund wurde - und niemand haette eine Erinnerung daran, dass je ein
# Rueckfall stattfand.
#
# Zurueckgeschrieben wird deshalb die konkrete Fassung, die vorher LIEF.
# Nur wenn die sich nicht ermitteln liess, bleibt der alte Eintrag die
# einzige verfuegbare Auskunft.
case "$RUNNING" in
  ''|unbekannt|dev) BACK="$FROM" ;;
  *)                BACK="${RUNNING#v}" ;;
esac
log "Update auf $TO nicht gesund nach ${HEALTH_TIMEOUT}s - Rueckfall auf $BACK"
ROLLED=true
set_state rollback ""
set_tag "$BACK"
run git -C "$REPO" checkout --detach "$GIT_BEFORE" || true
run docker compose --project-directory "$STACK" up -d --no-deps --force-recreate "$SERVICE" || true

# Genau einmal. Kein zweiter Versuch, kein Flattern: waere die Ursache
# nicht das Image (sondern etwa ein toter matter-server), brachte jeder
# weitere Durchlauf nur weitere Ausfallzeit.
if wait_healthy; then
  HEALTHY=true
else
  HEALTHY=false
fi

set_state failed "Version $TO wurde nach ${HEALTH_TIMEOUT}s nicht gesund"

# Wenn auch der Rueckfall nicht gesund wurde, ist die Oberflaeche
# wahrscheinlich gar nicht erreichbar - dann ist diese Datei die einzige
# Antwort, die jemand findet, der doch per SSH nachsieht. Sie steht auch
# im geglueckten Rueckfall da: wer wissen will, warum seine Version wieder
# die alte ist, soll es nachlesen koennen.
{
  printf 'loxmatter - letzter fehlgeschlagener Updateversuch\n\n'
  printf 'Zeitpunkt:      %s\n' "$(now)"
  printf 'Versucht:       %s -> %s (Kanal %s)\n' "$FROM" "$TO" "$CHANNEL"
  printf 'Zurueckgesetzt: ja, auf %s\n' "$FROM"
  printf 'Wieder gesund:  %s\n\n' "$([ "$HEALTHY" = true ] && echo ja || echo NEIN)"
  printf 'Sicherung der Signaldatenbank:\n  %s\n\n' "$BACKUP_DIR/store-$STAMP.tgz"
  printf 'Von Hand weiter:\n'
  printf '  cd %s && docker compose logs --tail 100 %s\n' "$STACK" "$SERVICE"
  printf '  cd %s && ./scripts/update.sh --no-pull\n' "$REPO"
  printf '  # Sicherung zurueckspielen (verwirft alles seither):\n'
  printf '  #   docker compose stop %s\n' "$SERVICE"
  printf '  #   tar xzf %s -C /var/lib/docker/volumes/.../\n\n' "$BACKUP_DIR/store-$STAMP.tgz"
  printf 'Letzte Zeilen des Protokolls:\n'
  tail -n 40 "$LOG" 2>/dev/null || true
} > "$FAILURE"
```

Und in Task 3, direkt nach `FROM="$(current_tag)"` in Task 2, ergänzen (damit `GIT_BEFORE` existiert):

```sh
GIT_BEFORE="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo HEAD)"
```

Außerdem im Erfolgszweig aus Task 3, vor `set_state done ""`:

```sh
  rm -f "$FAILURE"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: alle bestehen

- [ ] **Step 5: Prove the "genau einmal" test can fail**

Umschließe den Rückfallblock probeweise mit `for _ in 1 2; do … done` und führe die Tests aus.
Expected: `test_der_rueckfall_laeuft_genau_einmal` FAILT (`3 != 2`). Danach zurücknehmen.

- [ ] **Step 6: Append the self-replacement**

Ganz ans Ende des Erfolgszweigs, **nach** `set_state done ""`:

```sh
# Zuletzt, und nur nach einem geglueckten Update: der Beiwagen prueft, ob
# sein eigenes Image veraltet ist, und stoesst abgekoppelt seinen eigenen
# Austausch an. NACH dem Schreiben von `done`, nie vorher - sonst beendet
# er sich mitten im Schreiben des Zustands, den die Oberflaeche gerade
# liest, und ein gelungenes Update saehe aus wie ein haengendes.
#
# Abgekoppelt ueber `-d`: der Aufruf, der diesen Container ersetzt, darf
# nicht in diesem Container auf sein eigenes Ende warten.
if [ "${LOXMATTER_UPDATER_SELF_REPLACE:-1}" = "1" ]; then
  run docker compose --project-directory "$STACK" up -d --no-deps loxmatter-updater || true
fi
```

Im Test-Fixture aus Task 2 die Zeile `"LOXMATTER_UPDATER_SELF_REPLACE": "0",` zum `env`-Wörterbuch hinzufügen — sonst zählt `test_der_neustart_laesst_die_nachbardienste_in_ruhe` einen `up`-Aufruf mit, der den Beiwagen meint. Dazu ein eigener Test:

```python
def test_der_beiwagen_tauscht_sich_erst_nach_dem_erfolg_aus(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater(LOXMATTER_UPDATER_SELF_REPLACE="1")
    zeilen = calls.splitlines()
    eigen = next(i for i, line in enumerate(zeilen) if "loxmatter-updater" in line)
    fremd = next(i for i, line in enumerate(zeilen) if "compose up" in line and "loxmatter-updater" not in line)
    assert fremd < eigen


def test_nach_einem_fehlschlag_tauscht_er_sich_nicht_aus(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst(LOXMATTER_UPDATER_SELF_REPLACE="1")
    assert "loxmatter-updater" not in calls
```

- [ ] **Step 7: Run all updater tests, shellcheck, commit**

Run: `uv run pytest tests/test_updater_script.py -v && shellcheck -s sh deploy/updater/update-once.sh`
Expected: alles grün

```bash
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): Rueckfall, Klartextdatei, Selbstaustausch

Der Rueckfall tauscht das Image zurueck und laesst die Datenbank in Ruhe.
Das steht auf einem gepruefen Befund, nicht auf einer Annahme: _migrate in
model/store.py kehrt bei version >= _SCHEMA_VERSION sofort zurueck, die
alte Fassung laeuft also auf dem neuen Schema. Die Sicherung
zurueckzuspielen ist der destruktivere Schritt - er verwirft alles seit
dem Sicherungszeitpunkt - und bleibt eine ausdrueckliche Handlung in der
Oberflaeche.

Genau einmal, kein Flattern: waere die Ursache nicht das Image, sondern
etwa ein toter matter-server, brachte jeder weitere Durchlauf nur weitere
Ausfallzeit.

LETZTER-FEHLSCHLAG.txt entsteht auch bei gegluecktem Rueckfall. Wenn die
Oberflaeche nicht hochkommt, ist sie die einzige Antwort, die jemand
findet, der doch per SSH nachsieht - und wer nur wissen will, warum seine
Version wieder die alte ist, soll es nachlesen koennen.

Der Selbstaustausch steht NACH dem Schreiben von done: davor beendete er
sich mitten im Schreiben des Zustands, den die Oberflaeche gerade liest,
und ein gelungenes Update saehe aus wie ein haengendes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Der Beiwagen im Stack

**Files:**
- Modify: `deploy/testhost/docker-compose.yml`
- Test: `tests/test_compose_profiles.py` (anhängen)

**Interfaces:**
- Consumes: das Image aus Task 1, die Umgebungsnamen aus Task 2.
- Produces: den laufenden Dienst `loxmatter-updater` mit Zugriff auf `loxmatter-store`, den Checkout und den Docker-Socket.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_compose_profiles.py`:

```python
def test_der_updater_hat_kein_netz_nach_aussen_offen() -> None:
    # Die tragende Absicherung dieses Containers: er haelt den
    # Docker-Socket und ist damit root-gleichwertig auf dem Host. Anders
    # als die drei uebrigen Dienste steht er deshalb NICHT im Host-Netz und
    # veroeffentlicht keinen Port.
    updater = _stack()["services"]["loxmatter-updater"]
    assert "ports" not in updater
    assert updater.get("network_mode") != "host"


def test_nur_der_updater_hat_den_docker_socket() -> None:
    for name, service in _stack()["services"].items():
        socket = any("docker.sock" in str(v) for v in service.get("volumes", []))
        assert socket == (name == "loxmatter-updater"), name


def test_der_updater_sieht_dieselbe_datenbank_wie_die_bruecke() -> None:
    # Die Verstaendigung laeuft ueber Dateien in genau diesem Volume.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any(str(v).startswith("loxmatter-store:") for v in updater["volumes"])


def test_der_updater_erreicht_die_gesundheitsroute_des_hosts() -> None:
    # Er haengt im Compose-Standardnetz; 127.0.0.1 waere dort er selbst.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any("host-gateway" in str(h) for h in updater["extra_hosts"])


def test_der_updater_ist_gepinnt() -> None:
    image = _stack()["services"]["loxmatter-updater"]["image"]
    assert "@sha256:" in image or ":latest" not in image
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: FAIL — `KeyError: 'loxmatter-updater'`

- [ ] **Step 3: Add the service**

In `deploy/testhost/docker-compose.yml`, nach dem Dienst `loxmatter`:

```yaml
  # Der Updater-Beiwagen (Entwurf "Updates ueber die Oberflaeche
  # einspielen", 2026-09-08, Abschnitt 6). Er existiert, weil die Bruecke
  # sich nicht selbst ersetzen kann: der Prozess, der `up -d
  # --force-recreate` aufriefe, waere genau der, den dieser Aufruf beendet.
  #
  # DIESER CONTAINER IST ROOT-GLEICHWERTIG AUF DEM HOST. Der Docker-Socket
  # gibt das her, daran laesst sich nichts abschwaechen. Abgesichert wird
  # es durch Enge, nicht durch Rechte:
  #
  #   - Kein `network_mode: host` und keine `ports:`. Er haengt im
  #     Compose-Standardnetz - nach draussen erreichbar (git fetch), aus
  #     dem LAN nicht. Genau das unterscheidet ihn von den drei anderen
  #     Diensten hier und ist der Grund, warum er den Socket haben darf.
  #   - Kein Netzdienst: die Verstaendigung mit der Bruecke laeuft
  #     ausschliesslich ueber Dateien in loxmatter-store (/data/update/).
  #   - Ein einziges festes Arbeitsprogramm. Er fuehrt nie Text aus dem
  #     Auftrag aus (siehe update-once.sh).
  #
  # Wer das nicht will, streicht diesen Dienst. Die Bruecke merkt das am
  # veralteten Lebenszeichen, blendet den Knopf aus und nennt den
  # Konsolenweg - kein toter Knopf, keine Meldung, die nach Defekt klingt.
  loxmatter-updater:
    image: ghcr.io/lucienkerl/loxmatter-updater:stable
    container_name: loxmatter-updater
    restart: unless-stopped
    # Damit `curl http://host.docker.internal:8080/health` den Dienst auf
    # dem Host trifft: aus diesem Container heraus waere 127.0.0.1 er
    # selbst, nicht die Bruecke.
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      # Root-gleichwertig - siehe oben.
      - /var/run/docker.sock:/var/run/docker.sock
      # Der Checkout: die Compose-Datei muss zur Zielversion passen, eine
      # neue Fassung kann einen neuen Dienst oder eine neue Variable
      # brauchen. Schreibbar, weil `git checkout` hineinschreibt.
      - ../..:/repo
      # Dasselbe Volume wie die Bruecke: hier liegen Auftrag, Zustand,
      # Protokoll und die Sicherungen.
      - loxmatter-store:/data
    environment:
      LOXMATTER_UPDATE_DIR: /data/update
      LOXMATTER_BACKUP_DIR: /data/backups
      LOXMATTER_STACK: /repo/deploy/testhost
      LOXMATTER_REPO: /repo
      LOXMATTER_SERVICE: loxmatter
      LOXMATTER_IMAGE: ghcr.io/lucienkerl/loxmatter
      LOXMATTER_HEALTH_URL: http://host.docker.internal:8080/health
      LOXMATTER_HEALTH_TIMEOUT: "120"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: 11 passed

- [ ] **Step 5: Prove the socket test can fail**

Hänge probeweise `/var/run/docker.sock:/var/run/docker.sock` auch beim Dienst `loxmatter` ein und führe die Tests aus.
Expected: `test_nur_der_updater_hat_den_docker_socket` FAILT. Danach zurücknehmen.

- [ ] **Step 6: Bring it up on the test host and watch it breathe**

```bash
cd deploy/testhost && docker compose up -d loxmatter-updater
sleep 5
docker exec loxmatter cat /data/update/state.json
```
Expected: `{"id":null,"phase":"idle",...,"updater_seen_at":"2026-..."}` — und der Zeitstempel wandert bei jedem erneuten Aufruf weiter.

- [ ] **Step 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "feat(compose): den Updater-Beiwagen in den Stack nehmen

Er haelt den Docker-Socket und ist damit root-gleichwertig auf dem Host -
das steht unverkuerzt im Kommentar, es laesst sich nicht abschwaechen.
Abgesichert wird es durch Enge: kein Host-Netz, keine Ports, kein
Netzdienst, ein festes Arbeitsprogramm. Genau das unterscheidet ihn von
den drei anderen Diensten hier, die alle im Host-Netz stehen.

Tests halten die drei Eigenschaften fest, auf denen das ruht - und die
dritte prueft nicht nur, dass der Beiwagen den Socket hat, sondern dass
sonst NIEMAND ihn hat.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `loxmatter.update` — Auftrag schreiben, Zustand lesen

**Files:**
- Create: `src/loxmatter/update.py`
- Test: `tests/test_update_module.py`

**Interfaces:**
- Consumes: die Dateiformate aus Task 2–4.
- Produces:
  - `UpdateState` (frozen dataclass: `phase: str`, `id: str | None`, `from_version: str | None`, `to_version: str | None`, `error: str | None`, `rolled_back: bool`, `healthy: bool`, `updater_seen_at: str | None`)
  - `read_state(update_dir: Path) -> UpdateState | None`
  - `read_log(update_dir: Path, lines: int = 40) -> list[str]`
  - `updater_present(state: UpdateState | None, *, now: datetime, max_age_seconds: int = 30) -> bool`
  - `request_update(update_dir: Path, *, channel: str, target: str) -> str` (gibt die neue `id` zurück)
  - `UpdateBusyError`

- [ ] **Step 1: Write the failing tests**

`tests/test_update_module.py` (GPL-Kopf, dann):

```python
"""Tests fuer die Dateiseite des Updates - Entwurf "Updates ueber die
Oberflaeche einspielen" (2026-09-08), Abschnitt 7.

Dieses Modul fasst nur Dateien an, nie das Netz und nie HTTP. Deshalb
laeuft hier alles gegen ein tmp_path-Verzeichnis, ohne Attrappen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from loxmatter.update import (
    UpdateBusyError,
    read_log,
    read_state,
    request_update,
    updater_present,
)

JETZT = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _state(tmp_path, **fields):
    body = {"id": "a", "phase": "idle", "updater_seen_at": JETZT.strftime("%Y-%m-%dT%H:%M:%SZ")}
    body.update(fields)
    (tmp_path / "state.json").write_text(json.dumps(body), encoding="utf-8")


def test_ohne_zustandsdatei_gibt_es_keinen_zustand(tmp_path):
    assert read_state(tmp_path) is None


def test_eine_halb_geschriebene_datei_gilt_als_kein_zustand(tmp_path):
    # Der Beiwagen schreibt atomar (temp + rename), aber ein abgeschnittenes
    # JSON darf hier trotzdem keinen 500er ausloesen: die Oberflaeche fragt
    # diese Route im Sekundentakt, und ein einzelner Fehlschlag saehe dort
    # aus wie ein kaputtes Update.
    (tmp_path / "state.json").write_text('{"phase": "pu', encoding="utf-8")
    assert read_state(tmp_path) is None


def test_der_zustand_wird_vollstaendig_gelesen(tmp_path):
    _state(tmp_path, phase="failed", from_version="0.2.0", to_version="0.3.0",
           error="nicht gesund", rolled_back=True, healthy=True)
    state = read_state(tmp_path)
    assert state.phase == "failed"
    assert state.from_version == "0.2.0"
    assert state.to_version == "0.3.0"
    assert state.error == "nicht gesund"
    assert state.rolled_back is True


def test_ein_frisches_lebenszeichen_heisst_beiwagen_da(tmp_path):
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=5)) is True


def test_ein_altes_lebenszeichen_heisst_kein_beiwagen(tmp_path):
    # Er meldet sich alle zwei Sekunden. Eine halbe Minute Stille heisst,
    # dass niemand den Auftrag abholen wuerde - dann darf die Oberflaeche
    # keinen Knopf zeigen, der nichts tut.
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=90)) is False


def test_ohne_zustand_gibt_es_keinen_beiwagen(tmp_path):
    assert updater_present(None, now=JETZT) is False


def test_ein_auftrag_wird_geschrieben_und_bekommt_eine_id(tmp_path):
    _state(tmp_path)
    job = request_update(tmp_path, channel="stable", target="0.3.0")
    body = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    assert body["id"] == job
    assert body["channel"] == "stable"
    assert body["target"] == "0.3.0"
    assert body["requested_at"]


def test_zwei_auftraege_bekommen_verschiedene_ids(tmp_path):
    _state(tmp_path)
    erste = request_update(tmp_path, channel="stable", target="0.3.0")
    _state(tmp_path, id=erste, phase="done")
    zweite = request_update(tmp_path, channel="stable", target="0.4.0")
    assert erste != zweite


def test_waehrend_eines_laufenden_updates_wird_kein_zweiter_angenommen(tmp_path):
    _state(tmp_path, id="laeuft", phase="pull")
    with pytest.raises(UpdateBusyError):
        request_update(tmp_path, channel="stable", target="0.3.0")


def test_nach_einem_abgeschlossenen_update_geht_ein_neuer(tmp_path):
    _state(tmp_path, id="alt", phase="done")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_der_auftrag_wird_atomar_geschrieben(tmp_path, monkeypatch):
    """Der Beiwagen liest alle zwei Sekunden. Saehe er die Datei halb
    geschrieben, lehnte er einen gueltigen Auftrag als ungueltig ab - und
    weil er jede id nur einmal anfasst, kaeme dieser Auftrag nie wieder."""
    gesehen = []
    echtes_replace = __import__("os").replace

    def spion(src, dst):
        gesehen.append((str(src), str(dst)))
        echtes_replace(src, dst)

    monkeypatch.setattr("loxmatter.update.os.replace", spion)
    _state(tmp_path)
    request_update(tmp_path, channel="stable", target="0.3.0")
    assert gesehen, "request.json muss ueber os.replace an seinen Platz kommen"


def test_das_protokoll_liefert_die_letzten_zeilen(tmp_path):
    (tmp_path / "log.txt").write_text("\n".join(f"zeile {i}" for i in range(100)), encoding="utf-8")
    assert read_log(tmp_path, lines=5) == [f"zeile {i}" for i in range(95, 100)]


def test_ohne_protokoll_ist_die_liste_leer(tmp_path):
    assert read_log(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_module.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.update'`

- [ ] **Step 3: Write the module**

`src/loxmatter/update.py` (GPL-Kopf, dann):

```python
"""Die Dateiseite des Updates - Entwurf "Updates ueber die Oberflaeche
einspielen" (2026-09-08), Abschnitt 7.

Bruecke und Beiwagen verstaendigen sich ausschliesslich ueber Dateien im
gemeinsamen Volume - kein Netzwerk, kein Socket, keine gemeinsame
Bibliothek. Der Grund ist nicht Sparsamkeit: der Beiwagen ueberlebt den
Neustart der Bruecke, und weil der Zustand in einer Datei liegt statt im
Speicher, kann die Oberflaeche nach dem Neustart einfach weiterlesen. Genau
das ist die Luecke, in der man vor diesem Entwurf blind war.

Dieses Modul kennt keine HTTP-Begriffe und macht keine Netzzugriffe -
`update_check.py` macht das Netz, `api/update.py` das HTTP. So laesst sich
jedes fuer sich pruefen."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Phasen, in denen ein Auftrag noch laeuft. Alles andere ist ein Endzustand
# (oder `idle`) und laesst einen neuen Auftrag zu.
_LAUFENDE_PHASEN = frozenset({"queued", "backup", "pull", "recreate", "health", "rollback"})

# Der Beiwagen meldet sich alle zwei Sekunden (entrypoint.sh). Dreissig
# Sekunden Stille sind grosszuegig genug fuer einen belasteten Pi und kurz
# genug, dass niemand lange einen Knopf sieht, den keiner abholen wuerde.
_MAX_STILLE_SEKUNDEN = 30


class UpdateBusyError(RuntimeError):
    """Es laeuft bereits ein Auftrag."""


@dataclass(frozen=True)
class UpdateState:
    phase: str
    id: str | None
    from_version: str | None
    to_version: str | None
    error: str | None
    rolled_back: bool
    healthy: bool
    updater_seen_at: str | None


def read_state(update_dir: Path) -> UpdateState | None:
    """Der zuletzt geschriebene Zustand, oder `None`.

    Ein unlesbarer Inhalt gilt bewusst wie ein fehlender, statt eine
    Ausnahme zu werfen: die Oberflaeche fragt diese Angabe im Sekundentakt
    ab, und ein einzelner Fehlschlag saehe dort aus wie ein kaputtes
    Update. Der Beiwagen schreibt atomar (temp + rename), ein halber Inhalt
    ist also ohnehin die Ausnahme - aber eine, die keinen Alarm verdient.
    """
    try:
        raw = json.loads((update_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return UpdateState(
        phase=str(raw.get("phase") or "idle"),
        id=raw.get("id"),
        from_version=raw.get("from"),
        to_version=raw.get("to"),
        error=raw.get("error"),
        rolled_back=bool(raw.get("rolled_back")),
        healthy=bool(raw.get("healthy", True)),
        updater_seen_at=raw.get("updater_seen_at"),
    )


def read_log(update_dir: Path, lines: int = 40) -> list[str]:
    try:
        text = (update_dir / "log.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def updater_present(
    state: UpdateState | None,
    *,
    now: datetime,
    max_age_seconds: int = _MAX_STILLE_SEKUNDEN,
) -> bool:
    """Ob ueberhaupt jemand da ist, der einen Auftrag abholen wuerde.

    Ohne diese Beurteilung zeigte die Oberflaeche einer Bestandsinstallation
    - und jedem, der den Beiwagen aus der Compose-Datei gestrichen hat -
    einen Knopf, der nichts tut und auch nichts meldet.
    """
    if state is None or not state.updater_seen_at:
        return False
    try:
        seen = datetime.strptime(state.updater_seen_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - seen).total_seconds() <= max_age_seconds


def request_update(update_dir: Path, *, channel: str, target: str) -> str:
    """Legt einen Auftrag ab und liefert seine `id`.

    Atomar ueber `os.replace`: der Beiwagen liest alle zwei Sekunden, und
    saehe er die Datei halb geschrieben, lehnte er einen gueltigen Auftrag
    als ungueltig ab - endgueltig, denn er fasst jede `id` nur einmal an.

    Geprueft wird hier NICHT, ob `target` gueltig ist. Das tut der Beiwagen,
    und zwar bewusst dort: er ist die Sicherheitsgrenze (Spec-Abschnitt 10),
    und eine Grenze, die sich darauf verlaesst, dass der Aufrufer schon
    geprueft hat, ist keine. Diese Funktion ist nur der Briefkasten.
    """
    state = read_state(update_dir)
    if state is not None and state.phase in _LAUFENDE_PHASEN:
        raise UpdateBusyError(state.phase)

    job_id = str(uuid.uuid4())
    body = {
        "id": job_id,
        "channel": channel,
        "target": target,
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    update_dir.mkdir(parents=True, exist_ok=True)
    temp = update_dir / "request.json.tmp"
    temp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temp, update_dir / "request.json")
    return job_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_module.py -v`
Expected: 13 passed

- [ ] **Step 5: Prove the atomicity test can fail**

Ersetze `os.replace(temp, ...)` probeweise durch `(update_dir / "request.json").write_text(json.dumps(body), encoding="utf-8")` und führe die Tests aus.
Expected: `test_der_auftrag_wird_atomar_geschrieben` FAILT. Danach zurücknehmen.

- [ ] **Step 6: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/update.py tests/test_update_module.py
git commit -m "feat(update): Auftrag schreiben, Zustand lesen, Beiwagen erkennen

Die Verstaendigung laeuft ueber Dateien, nicht ueber ein Netz. Das ist
nicht Sparsamkeit: der Beiwagen ueberlebt den Neustart der Bruecke, und
weil der Zustand in einer Datei liegt statt im Speicher, kann die
Oberflaeche nach dem Neustart weiterlesen - genau die Luecke, in der man
vorher blind war.

Ein unlesbarer Zustand gilt wie ein fehlender statt eine Ausnahme zu
werfen: die Oberflaeche fragt im Sekundentakt, und ein einzelner
Fehlschlag saehe dort aus wie ein kaputtes Update.

request_update prueft das Ziel BEWUSST NICHT. Das tut der Beiwagen - er
ist die Sicherheitsgrenze, und eine Grenze, die sich darauf verlaesst,
dass der Aufrufer schon geprueft hat, ist keine.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `loxmatter.update_check` — was es Neues gibt

**Files:**
- Create: `src/loxmatter/update_check.py`
- Modify: `src/loxmatter/model/store.py` (zwei Einstellungen, siehe Step 3)
- Test: `tests/test_update_check.py`

**Interfaces:**
- Consumes: nichts aus Task 6.
- Produces:
  - `Available` (frozen dataclass: `channel: str`, `target: str | None`, `title: str | None`, `notes: str | None`, `behind: int | None`, `checked_at: str`, `error: str | None`)
  - `async check(channel: str, *, current_version: str, current_commit: str | None, fetch: Fetch) -> Available`
  - `Fetch = Callable[[str], Awaitable[dict | list]]` — die Netzschicht wird hereingereicht, damit der Test ohne Netz auskommt.
  - Store: `store.update_settings.get_channel() -> str`, `.set_channel(str)`, `.get_check_enabled() -> bool`, `.set_check_enabled(bool)`

- [ ] **Step 1: Write the failing tests**

`tests/test_update_check.py` (GPL-Kopf, dann):

```python
"""Tests fuer die Abfrage bei GitHub - Entwurf "Updates ueber die
Oberflaeche einspielen" (2026-09-08), Abschnitt 9.

Die Netzschicht wird als `fetch` hereingereicht: so laeuft jeder Test ohne
Netz, und der Fall "kein Internet" ist ein Testfall statt eines
Zufallsereignisses in der CI. Ein Geraet ohne Internetzugang ist hier
ausdruecklich KEIN Fehler - das haelt der letzte Test fest."""

from __future__ import annotations

import pytest

from loxmatter.update_check import check


async def test_der_stabile_kanal_meldet_ein_neueres_release():
    async def fetch(url):
        assert "releases/latest" in url
        return {"tag_name": "v0.3.0", "name": "0.3.0", "body": "Behebt den Neustart-Haenger."}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target == "0.3.0"
    assert result.notes == "Behebt den Neustart-Haenger."
    assert result.error is None


async def test_auf_dem_neuesten_stand_gibt_es_kein_ziel():
    async def fetch(url):
        return {"tag_name": "v0.2.0", "name": "0.2.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_ein_aelteres_release_gilt_nicht_als_update():
    # Wer auf einem Entwicklungsstand laeuft, der neuer ist als das letzte
    # Release, soll nicht zum Herabstufen eingeladen werden - "nur
    # vorwaerts" faengt das im Beiwagen zwar ab, aber ein Knopf, der
    # zuverlaessig abgelehnt wird, ist ein kaputter Knopf.
    async def fetch(url):
        return {"tag_name": "v0.1.0", "name": "0.1.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_der_entwicklungskanal_zaehlt_die_commits():
    async def fetch(url):
        assert "compare" in url
        return {"ahead_by": 14, "commits": [{"commit": {"message": "fix: eins\n\nmehr"}}, {"commit": {"message": "feat: zwei"}}]}

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.behind == 14
    assert "fix: eins" in result.notes
    assert "mehr" not in result.notes, "nur die Betreffzeile, nicht der ganze Rumpf"


async def test_der_entwicklungskanal_ohne_bekannten_commit_meldet_nichts():
    async def fetch(url):
        raise AssertionError("ohne Commit darf gar nicht gefragt werden")

    result = await check("dev", current_version="dev", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_ohne_internet_ist_das_kein_fehlerzustand():
    async def fetch(url):
        raise OSError("Name or service not known")

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error
    assert result.checked_at


async def test_ein_unbekannter_kanal_wird_abgewiesen():
    async def fetch(url):
        raise AssertionError("darf nicht gefragt werden")

    with pytest.raises(ValueError):
        await check("beliebig", current_version="0.2.0", current_commit=None, fetch=fetch)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_check.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add the two settings to the store**

In `src/loxmatter/model/store.py`, neben den übrigen Einstellungszugängen (dem Muster von `store.resend_settings` folgend, das bereits in derselben `setting`-Tabelle aus Schema 5 liegt):

```python
class UpdateSettings:
    """Kanal und Prüfschalter - Entwurf "Updates ueber die Oberflaeche
    einspielen" (2026-09-08), Abschnitt 9.

    Keine neue Tabelle und damit keine neue Schema-Version: beide Werte
    liegen in `setting`, die es seit Schema 5 gibt. Eine Migration hier
    waere teuer erkauft - ein Schemasprung ist der einzige Fall, in dem ein
    Rueckfall auf die vorherige Version nicht folgenlos ist, und
    ausgerechnet die Update-Funktion sollte ihn nicht ohne Not ausloesen.
    """

    _CHANNEL = "update.channel"
    _CHECK = "update.check_enabled"

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_channel(self) -> str:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (self._CHANNEL,)).fetchone()
        return row[0] if row else "stable"

    def set_channel(self, channel: str) -> None:
        if channel not in ("stable", "dev"):
            raise ValueError(channel)
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._CHANNEL, channel),
        )
        self._db.commit()

    def get_check_enabled(self) -> bool:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (self._CHECK,)).fetchone()
        # Vorgabe an: wer eine Bruecke im Haus betreibt, soll erfahren, dass
        # eine Fassung mit bekannten Fehlern laeuft. Abschaltbar bleibt es
        # trotzdem - es ist ein Verbindungsaufbau nach draussen.
        return row[0] != "0" if row else True

    def set_check_enabled(self, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._CHECK, "1" if enabled else "0"),
        )
        self._db.commit()
```

Und im `Store.__init__`, bei den übrigen Zugängen: `self.update_settings = UpdateSettings(self._db)`

- [ ] **Step 4: Write the check module**

`src/loxmatter/update_check.py` (GPL-Kopf, dann):

```python
"""Was es Neues gibt - Entwurf "Updates ueber die Oberflaeche einspielen"
(2026-09-08), Abschnitt 9.

Die Netzschicht kommt als `fetch` herein, statt hier fest verdrahtet zu
sein. Das ist kein Selbstzweck: so laeuft jeder Test ohne Netz, und der
Fall "kein Internet" wird ein Testfall statt eines Zufallsereignisses in
der CI.

Ein Geraet ohne Internetzugang ist hier ausdruecklich KEIN Fehler. Diese
Bruecke steht in einem Haus, nicht in einem Rechenzentrum; wer sie ohne
Weg nach draussen betreibt, hat das so gewollt. Deshalb traegt `Available`
ein `error`-Feld, statt eine Ausnahme zu werfen - die Oberflaeche zeigt
daraufhin einen ruhigen Hinweis, kein rotes Banner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

Fetch = Callable[[str], Awaitable[Any]]

_RELEASE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/releases/latest"
_COMPARE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/compare/{base}...main"


@dataclass(frozen=True)
class Available:
    channel: str
    target: str | None
    title: str | None
    notes: str | None
    behind: int | None
    checked_at: str
    error: str | None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.lstrip("v").split("."))
    except ValueError:
        return None


async def check(
    channel: str,
    *,
    current_version: str,
    current_commit: str | None,
    fetch: Fetch,
) -> Available:
    if channel not in ("stable", "dev"):
        raise ValueError(channel)

    def leer(error: str | None = None) -> Available:
        return Available(channel, None, None, None, None, _now(), error)

    if channel == "dev" and not current_commit:
        # Ein Image ohne LOXMATTER_COMMIT ist ein von Hand gebautes. Der
        # Vergleich haette keinen Ausgangspunkt, und geraten wird hier
        # nicht.
        return leer("Diese Fassung nennt keinen Commit - der Entwicklungskanal kann nicht vergleichen.")

    try:
        if channel == "stable":
            body = await fetch(_RELEASE_URL)
            tag = str(body.get("tag_name", "")).lstrip("v")
            neu, alt = _as_tuple(tag), _as_tuple(current_version)
            if not tag or neu is None:
                return leer("Die Antwort von GitHub nennt keine Version.")
            # Wer auf einem Entwicklungsstand laeuft, der neuer ist als das
            # letzte Release, bekommt hier bewusst kein Ziel: "nur
            # vorwaerts" faengt das im Beiwagen zwar ab, aber ein Knopf,
            # der zuverlaessig abgelehnt wird, ist ein kaputter Knopf.
            if alt is not None and neu <= alt:
                return leer()
            return Available(
                channel, tag, str(body.get("name") or tag), str(body.get("body") or ""), None, _now(), None
            )

        body = await fetch(_COMPARE_URL.format(base=current_commit))
        ahead = int(body.get("ahead_by", 0))
        if ahead <= 0:
            return leer()
        # Nur die Betreffzeilen: der Rumpf einer Commit-Nachricht ist in
        # diesem Projekt oft ein halber Aufsatz, und die Karte soll eine
        # Uebersicht geben, keine Lektuere.
        betreffe = [str(c["commit"]["message"]).splitlines()[0] for c in body.get("commits", [])]
        return Available(channel, "main", None, "\n".join(betreffe), ahead, _now(), None)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return leer(str(exc))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_check.py -v`
Expected: 7 passed

- [ ] **Step 6: Prove the downgrade test can fail**

Entferne probeweise die Bedingung `if alt is not None and neu <= alt:` und führe die Tests aus.
Expected: `test_ein_aelteres_release_gilt_nicht_als_update` und `test_auf_dem_neuesten_stand_gibt_es_kein_ziel` FAILEN. Danach zurücknehmen.

- [ ] **Step 7: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/update_check.py src/loxmatter/model/store.py tests/test_update_check.py
git commit -m "feat(update): Abfrage bei GitHub, Kanal und Pruefschalter

Die Netzschicht kommt als fetch herein statt fest verdrahtet zu sein: so
laeuft jeder Test ohne Netz, und 'kein Internet' wird ein Testfall statt
eines Zufallsereignisses in der CI.

Ein Geraet ohne Internetzugang ist ausdruecklich KEIN Fehler - diese
Bruecke steht in einem Haus, nicht in einem Rechenzentrum. Available
traegt deshalb ein error-Feld, statt zu werfen; die Oberflaeche zeigt
daraufhin einen ruhigen Hinweis.

Ein aelteres Release gilt nicht als Update. 'Nur vorwaerts' faengt das im
Beiwagen zwar ab, aber ein Knopf, der zuverlaessig abgelehnt wird, ist ein
kaputter Knopf.

Kanal und Pruefschalter liegen in der setting-Tabelle aus Schema 5, ohne
neue Schema-Version: ein Schemasprung ist der einzige Fall, in dem ein
Rueckfall nicht folgenlos ist, und ausgerechnet die Update-Funktion sollte
ihn nicht ohne Not ausloesen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Die drei Routen

**Files:**
- Create: `src/loxmatter/api/update.py`
- Modify: `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_update_api.py`

**Interfaces:**
- Consumes: `loxmatter.update` (Task 6), `loxmatter.update_check` (Task 7), `loxmatter.version.build_info` (Stufe 1).
- Produces:
  - `GET /api/update/status` → `{state: {...} | null, updater_present: bool, log: [str], channel: str, check_enabled: bool}`
  - `GET /api/update/check` → `{channel, target, title, notes, behind, checked_at, error}`
  - `POST /api/update/apply` `{target}` → `{id}`; 409 wenn schon einer läuft, 503 wenn kein Beiwagen da ist
  - `PATCH /api/update/settings` `{channel?, check_enabled?}` → wie `status`
  - `build_update_router(store, update_dir) -> APIRouter`

- [ ] **Step 1: Write the failing tests**

`tests/api/test_update_api.py` (GPL-Kopf, dann):

```python
"""Tests fuer /api/update/*.

Die Routen kleben `loxmatter.update` und `loxmatter.update_check` an HTTP
und tun sonst nichts - entsprechend pruefen diese Tests genau die Klebe-
stellen: welcher Statuscode aus welchem Modulzustand folgt."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


def _lebenszeichen(update_dir, phase="idle", **fields):
    body = {
        "id": None,
        "phase": phase,
        "updater_seen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body.update(fields)
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=update_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, update_dir
    store.close()


async def test_ohne_beiwagen_meldet_der_status_das_ehrlich(api):
    client, _ = api
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is False
    assert body["state"] is None


async def test_mit_beiwagen_meldet_der_status_ihn(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is True
    assert body["state"]["phase"] == "idle"


async def test_ohne_beiwagen_wird_kein_auftrag_angenommen(api):
    """503 und nicht 200: sonst schriebe die Bruecke einen Auftrag in ein
    Volume, das niemand liest, und die Oberflaeche zeigte einen Fortschritt,
    der nie beginnt."""
    client, _ = api
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 503


async def test_mit_beiwagen_wird_ein_auftrag_angenommen(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 200
    assert response.json()["id"]
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["target"] == "0.3.0"


async def test_der_kanal_aus_der_einstellung_landet_im_auftrag(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    await client.patch("/api/update/settings", json={"channel": "dev"})
    await client.post("/api/update/apply", json={"target": "a3f91c2"})
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["channel"] == "dev"


async def test_waehrend_eines_laufenden_updates_gibt_es_409(api):
    client, update_dir = api
    _lebenszeichen(update_dir, phase="pull", id="laeuft")
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 409


async def test_ein_unbekannter_kanal_wird_abgewiesen(api):
    client, _ = api
    assert (await client.patch("/api/update/settings", json={"channel": "beliebig"})).status_code == 422


async def test_ohne_sitzung_kein_zugriff(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/update/status")).status_code == 401
        assert (await client.post("/api/update/apply", json={"target": "0.3.0"})).status_code == 401
    store.close()


async def test_die_pruefung_laesst_sich_abschalten(api):
    client, _ = api
    await client.patch("/api/update/settings", json={"check_enabled": False})
    body = (await client.get("/api/update/check")).json()
    assert body["target"] is None
    assert body["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: FAIL — `TypeError: build_app() got an unexpected keyword argument 'update_dir'`

- [ ] **Step 3: Write the router**

`src/loxmatter/api/update.py` (GPL-Kopf, dann):

```python
"""Die Update-Routen - Entwurf "Updates ueber die Oberflaeche einspielen"
(2026-09-08), Abschnitte 9 und 10.

Dieses Modul klebt `loxmatter.update` (Dateien) und
`loxmatter.update_check` (Netz) an HTTP und tut sonst nichts. Insbesondere
prueft es das Ziel NICHT - das ist Sache des Beiwagens (Spec-Abschnitt 10),
und eine Grenze, die sich auf eine Vorpruefung eine Schicht hoeher
verlaesst, ist keine.

Alle Routen liegen hinter demselben `api_guard` wie jede andere
`/api`-Route. Bewusst KEINE erneute Passwortabfrage: dieselbe Sitzung
laedt heute schon die Fabric-Sicherung herunter, also die unersetzlichen
Anmeldedaten des gesamten Matter-Netzes. Ein Update auf eine
veroeffentlichte Version ist demgegenueber der kleinere Preis; eine zweite
Abfrage wuerde Sicherheit behaupten, die woanders laengst nicht besteht."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import update as update_files
from loxmatter import update_check
from loxmatter.model.store import Store
from loxmatter.version import build_info


class ApplyIn(BaseModel):
    target: str


class ApplyOut(BaseModel):
    id: str


class UpdateSettingsIn(BaseModel):
    channel: str | None = None
    check_enabled: bool | None = None


async def _fetch(url: str) -> Any:
    """Die einzige Stelle, die tatsaechlich nach draussen greift.

    Kurzer Zeitrahmen: die Oberflaeche wartet auf diese Antwort, und ein
    haengender GitHub-Aufruf duerfte den System-Tab nicht blockieren.
    """
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as response:
            response.raise_for_status()
            return await response.json()


def build_update_router(store: Store, update_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    def _status() -> dict[str, Any]:
        state = update_files.read_state(update_dir)
        return {
            "state": None if state is None else {
                "phase": state.phase,
                "id": state.id,
                "from": state.from_version,
                "to": state.to_version,
                "error": state.error,
                "rolled_back": state.rolled_back,
                "healthy": state.healthy,
            },
            "updater_present": update_files.updater_present(state, now=datetime.now(timezone.utc)),
            "log": update_files.read_log(update_dir),
            "channel": store.update_settings.get_channel(),
            "check_enabled": store.update_settings.get_check_enabled(),
        }

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return _status()

    @router.patch("/settings")
    async def settings(patch: UpdateSettingsIn) -> dict[str, Any]:
        if patch.channel is not None:
            try:
                store.update_settings.set_channel(patch.channel)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if patch.check_enabled is not None:
            store.update_settings.set_check_enabled(patch.check_enabled)
        return _status()

    @router.get("/check")
    async def check() -> dict[str, Any]:
        info = build_info()
        if not store.update_settings.get_check_enabled():
            # Kein Fehler, sondern eine Einstellung - und trotzdem im
            # `error`-Feld, damit die Oberflaeche einen Satz zu zeigen hat
            # statt eines leeren Kastens.
            return {
                "channel": store.update_settings.get_channel(),
                "target": None, "title": None, "notes": None, "behind": None,
                "checked_at": None,
                "error": "Die Suche nach Updates ist abgeschaltet.",
            }
        result = await update_check.check(
            store.update_settings.get_channel(),
            current_version=info.version,
            current_commit=info.commit,
            fetch=_fetch,
        )
        return {
            "channel": result.channel, "target": result.target, "title": result.title,
            "notes": result.notes, "behind": result.behind,
            "checked_at": result.checked_at, "error": result.error,
        }

    @router.post("/apply")
    async def apply(body: ApplyIn) -> ApplyOut:
        state = update_files.read_state(update_dir)
        if not update_files.updater_present(state, now=datetime.now(timezone.utc)):
            # 503 und nicht 200: sonst schriebe die Bruecke einen Auftrag in
            # ein Volume, das niemand liest, und die Oberflaeche zeigte
            # einen Fortschritt, der nie beginnt.
            raise HTTPException(status_code=503, detail="Kein Updater vorhanden.")
        try:
            job_id = update_files.request_update(
                update_dir,
                channel=store.update_settings.get_channel(),
                target=body.target,
            )
        except update_files.UpdateBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApplyOut(id=job_id)

    return router
```

- [ ] **Step 4: Wire it into `build_app`**

In `src/loxmatter/loxone/server.py`: `build_app` bekommt einen neuen Parameter mit Vorgabe, damit alle bestehenden Aufrufer unverändert bleiben:

```python
def build_app(
    store: Store,
    invoke: Invoke,
    runtime: BuildAppRuntime,
    *,
    # Vorgabe passt zum eingehaengten Volume (siehe Compose-Datei). Als
    # Parameter, damit die Tests gegen ein tmp_path-Verzeichnis laufen -
    # ohne das brauchte jeder Test dieser Routen einen Patch auf einen
    # Modulkonstanten, und zwei Tests nebeneinander stoerten sich.
    update_dir: Path = Path("/data/update"),
    ...
```

und bei den übrigen Routern:

```python
    app.include_router(build_update_router(store, update_dir), dependencies=api_guard)
```

Dazu in `cli.py`, beim `run`-Kommando, `update_dir` aus `LOXMATTER_UPDATE_DIR` (Vorgabe `/data/update`) durchreichen.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: 9 passed

- [ ] **Step 6: Prove the 503 test can fail**

Entferne probeweise die `updater_present`-Prüfung in `apply` und führe die Tests aus.
Expected: `test_ohne_beiwagen_wird_kein_auftrag_angenommen` FAILT (`200 != 503`). Danach zurücknehmen.

- [ ] **Step 7: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/api/update.py src/loxmatter/loxone/server.py src/loxmatter/cli.py tests/api/test_update_api.py
git commit -m "feat(api): /api/update/status, /check, /apply, /settings

Die Routen kleben die Datei- und die Netzseite an HTTP und tun sonst
nichts. Insbesondere pruefen sie das Ziel NICHT - das ist Sache des
Beiwagens, und eine Grenze, die sich auf eine Vorpruefung eine Schicht
hoeher verlaesst, ist keine.

Ohne Beiwagen antwortet /apply mit 503 statt 200: sonst schriebe die
Bruecke einen Auftrag in ein Volume, das niemand liest, und die
Oberflaeche zeigte einen Fortschritt, der nie beginnt.

Keine erneute Passwortabfrage. Dieselbe Sitzung laedt heute schon die
Fabric-Sicherung herunter, also die unersetzlichen Anmeldedaten des
gesamten Matter-Netzes; eine zweite Abfrage wuerde Sicherheit behaupten,
die woanders laengst nicht besteht.

update_dir kommt als Parameter in build_app, damit die Tests gegen ein
tmp_path-Verzeichnis laufen statt sich ueber eine Modulkonstante zu
stoeren.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Die Karte mit ihren vier Zuständen

**Files:**
- Modify: `src/loxmatter/web/index.html` (die Karte aus Stufe 1, Task 4, wird erweitert)
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_update_api.py` (ein Textschlüssel-Test)

**Interfaces:**
- Consumes: die vier Routen aus Task 8.
- Produces: die fertige Oberfläche.

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/api/test_update_api.py`:

```python
def test_die_oberflaeche_kennt_alle_texte_der_update_karte():
    from loxmatter import i18n

    for key in (
        "web.system.update_available",
        "web.system.update_up_to_date",
        "web.system.update_apply",
        "web.system.update_cancel",
        "web.system.update_confirm_title",
        "web.system.update_confirm_downtime",
        "web.system.update_confirm_schema",
        "web.system.update_step_backup",
        "web.system.update_step_pull",
        "web.system.update_step_recreate",
        "web.system.update_step_health",
        "web.system.update_restarting",
        "web.system.update_restarting_hint",
        "web.system.update_done",
        "web.system.update_failed",
        "web.system.update_rolled_back",
        "web.system.update_no_updater",
        "web.system.update_channel_stable",
        "web.system.update_channel_dev",
        "web.system.update_channel_dev_warning",
        "web.system.update_check_disabled",
        "web.system.update_behind",
    ):
        assert i18n.raw_template(key), key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: FAIL — `KeyError: 'web.system.update_available'`

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, hinter den `web.system.version_*`-Schlüsseln aus Stufe 1:

```yaml
web.system.update_available:
  en: "Version {version} available"
  de: "Version {version} verfügbar"
web.system.update_up_to_date:
  en: "Up to date. Last checked {checked_at}."
  de: "Auf dem neuesten Stand. Zuletzt geprüft {checked_at}."
web.system.update_apply:
  en: "Install update"
  de: "Update einspielen"
web.system.update_cancel:
  en: "Cancel"
  de: "Abbrechen"
web.system.update_confirm_title:
  en: "Install version {version}?"
  de: "Update auf {version} einspielen?"
web.system.update_confirm_downtime:
  en: "About a minute without the bridge. The Miniserver receives no values during that time; afterwards the bridge sends everything again. Signal keys, rooms and the Loxone wiring stay untouched, and the database is backed up first."
  de: "Rund eine Minute ohne Brücke. Der Miniserver erhält in dieser Zeit keine Werte; danach sendet die Brücke alles erneut. Signalschlüssel, Räume und die Loxone-Verdrahtung bleiben unverändert, und die Datenbank wird vorher gesichert."
web.system.update_confirm_schema:
  en: "Raises the database schema from {from_version} to {to_version}. Going back to the previous version stays possible, but the new fields will not be maintained by it."
  de: "Hebt das Datenbankschema von {from_version} auf {to_version}. Ein Rückfall auf die vorherige Version bleibt möglich, die neuen Felder werden von ihr aber nicht gepflegt."
web.system.update_step_backup:
  en: "Backing up the database"
  de: "Datenbank wird gesichert"
web.system.update_step_pull:
  en: "Loading the image"
  de: "Image wird geladen"
web.system.update_step_recreate:
  en: "Restarting the service"
  de: "Dienst wird neu gestartet"
web.system.update_step_health:
  en: "Waiting for the bridge to report healthy"
  de: "Warten, bis die Brücke sich gesund meldet"
web.system.update_restarting:
  en: "The bridge is restarting"
  de: "Die Brücke startet neu"
web.system.update_restarting_hint:
  en: "This is part of the update. The page reconnects by itself as soon as the service answers. With many devices the first start takes longer: the bridge fetches every node before it goes on the network."
  de: "Das gehört dazu. Diese Seite verbindet sich von selbst wieder, sobald der Dienst antwortet. Bei vielen Geräten dauert der erste Start länger: die Brücke holt jeden Knoten ab, bevor sie ans Netz geht."
web.system.update_done:
  en: "Now running: {version}"
  de: "Läuft jetzt: {version}"
web.system.update_failed:
  en: "Update failed"
  de: "Update fehlgeschlagen"
web.system.update_rolled_back:
  en: "Version {version} did not report healthy. It was rolled back on its own; {from_version} is running again. The database was not touched."
  de: "Version {version} meldete sich nicht gesund. Es wurde selbsttätig zurückgesetzt, {from_version} läuft wieder. Die Datenbank wurde nicht angefasst."
web.system.update_no_updater:
  en: "This installation has no updater. Update from the console: git pull && ./scripts/update.sh"
  de: "Diese Installation hat keinen Updater. Aktualisieren über die Konsole: git pull && ./scripts/update.sh"
web.system.update_channel_stable:
  en: "Stable"
  de: "Stabil"
web.system.update_channel_dev:
  en: "Development"
  de: "Entwicklung"
web.system.update_channel_dev_warning:
  en: "The development channel installs whatever is currently on main, including untested intermediate states."
  de: "Der Entwicklungskanal spielt ein, was gerade auf main liegt — auch ungetestete Zwischenstände."
web.system.update_check_disabled:
  en: "The search for updates is switched off."
  de: "Die Suche nach Updates ist abgeschaltet."
web.system.update_behind:
  en: "{behind} commits behind main"
  de: "{behind} Commits hinter main"
```

- [ ] **Step 4: Add the state and the polling in `app.js`**

Neben `versionInfo` aus Stufe 1:

```javascript
    // Der Update-Zustand, wie ihn der Beiwagen in state.json schreibt.
    updateStatus: null,
    updateAvailable: null,
    updateConfirming: false,
    updateError: null,
    // Der Zeitgeber laeuft NUR, solange ein Auftrag laeuft. Ein dauerhaftes
    // Pollen waere fuer eine Angabe, die sich zehnmal im Jahr aendert, eine
    // Anfrage pro Sekunde auf einem Pi - und der Rest der Oberflaeche haengt
    // ohnehin am Live-Kanal.
    updateTimer: null,
```

Methoden:

```javascript
    /** Die vier laufenden Phasen. Ausserhalb davon ruht der Zeitgeber. */
    updateRunning() {
      const phase = this.updateStatus?.state?.phase;
      return ["queued", "backup", "pull", "recreate", "health", "rollback"].includes(phase);
    },

    async loadUpdateStatus() {
      try {
        this.updateStatus = await this.request("GET", "/api/update/status");
        this.updateError = null;
      } catch (error) {
        // Waehrend des Neustarts ist die Bruecke weg - das ist der
        // Normalfall dieses Ablaufs und kein Fehler. Der zuletzt bekannte
        // Zustand bleibt stehen, und der Zeitgeber versucht es weiter.
        if (!this.updateRunning()) {
          this.updateError = error.message;
        }
      }
      if (this.updateRunning() && !this.updateTimer) {
        this.updateTimer = setInterval(() => this.loadUpdateStatus(), 2000);
      }
      if (!this.updateRunning() && this.updateTimer) {
        clearInterval(this.updateTimer);
        this.updateTimer = null;
        // Nach dem Ende einmal die Version neu holen: die Karte oben soll
        // die neue Nummer zeigen, nicht die, mit der die Seite geladen wurde.
        this.versionInfo = await this.request("GET", "/api/version");
      }
    },

    async loadUpdateCheck() {
      try {
        this.updateAvailable = await this.request("GET", "/api/update/check");
      } catch (error) {
        this.updateAvailable = { target: null, error: error.message };
      }
    },

    async applyUpdate() {
      this.updateConfirming = false;
      this.updateError = null;
      try {
        await this.request("POST", "/api/update/apply", { target: this.updateAvailable.target });
        await this.loadUpdateStatus();
      } catch (error) {
        this.updateError = error.message;
      }
    },

    async setUpdateChannel(channel) {
      this.updateStatus = await this.request("PATCH", "/api/update/settings", { channel });
      await this.loadUpdateCheck();
    },
```

In `loadSystem()`, nach dem `versionInfo`-Aufruf aus Stufe 1:

```javascript
        await this.loadUpdateStatus();
        await this.loadUpdateCheck();
```

- [ ] **Step 5: Extend the card in `index.html`**

Innerhalb der Karte aus Stufe 1, nach dem `<template x-if="versionInfo">`-Block:

```html
          <!-- Kein Beiwagen: kein toter Knopf, sondern der Konsolenweg. -->
          <p class="hint" x-show="updateStatus && !updateStatus.updater_present && !updateRunning()" x-cloak
             x-text="t('web.system.update_no_updater')"></p>

          <!-- 1: Ein Update steht bereit. -->
          <template x-if="updateStatus?.updater_present && !updateRunning() && updateAvailable?.target">
            <div>
              <p class="banner warn"
                 x-text="updateStatus.channel === 'dev'
                   ? t('web.system.update_behind', { behind: updateAvailable.behind })
                   : t('web.system.update_available', { version: updateAvailable.target })"></p>
              <pre class="notes" x-show="updateAvailable.notes" x-cloak x-text="updateAvailable.notes"></pre>
              <button @click="updateConfirming = true" x-text="t('web.system.update_apply')"></button>
            </div>
          </template>

          <p class="hint" x-show="!updateRunning() && updateAvailable && !updateAvailable.target && !updateAvailable.error"
             x-cloak x-text="t('web.system.update_up_to_date', { checked_at: updateAvailable.checked_at })"></p>
          <p class="hint" x-show="!updateRunning() && updateAvailable?.error" x-cloak
             x-text="updateAvailable.error"></p>

          <!-- 2: Bestätigung. -->
          <template x-if="updateConfirming">
            <div class="confirm">
              <h3 x-text="t('web.system.update_confirm_title', { version: updateAvailable.target })"></h3>
              <p class="banner warn" x-text="t('web.system.update_confirm_downtime')"></p>
              <div class="row">
                <button @click="updateConfirming = false" x-text="t('web.system.update_cancel')"></button>
                <button @click="applyUpdate()" x-text="t('web.system.update_apply')"></button>
              </div>
            </div>
          </template>

          <!-- 3: Läuft — inklusive des Moments, in dem die Brücke weg ist. -->
          <template x-if="updateRunning()">
            <div>
              <h3 x-show="updateStatus.state.phase !== 'health'" x-cloak
                  x-text="t('web.system.update_available', { version: updateStatus.state.to })"></h3>
              <h3 x-show="updateStatus.state.phase === 'health'" x-cloak
                  x-text="t('web.system.update_restarting')"></h3>
              <ul class="steps">
                <li :class="{ done: ['pull','recreate','health'].includes(updateStatus.state.phase), now: updateStatus.state.phase === 'backup' }"
                    x-text="t('web.system.update_step_backup')"></li>
                <li :class="{ done: ['recreate','health'].includes(updateStatus.state.phase), now: updateStatus.state.phase === 'pull' }"
                    x-text="t('web.system.update_step_pull')"></li>
                <li :class="{ done: updateStatus.state.phase === 'health', now: updateStatus.state.phase === 'recreate' }"
                    x-text="t('web.system.update_step_recreate')"></li>
                <li :class="{ now: updateStatus.state.phase === 'health' }"
                    x-text="t('web.system.update_step_health')"></li>
              </ul>
              <p class="hint" x-show="updateStatus.state.phase === 'health'" x-cloak
                 x-text="t('web.system.update_restarting_hint')"></p>
            </div>
          </template>

          <!-- 4: Ergebnis. -->
          <p class="banner ok" x-show="updateStatus?.state?.phase === 'done'" x-cloak
             x-text="t('web.system.update_done', { version: updateStatus.state.to })"></p>
          <template x-if="updateStatus?.state?.phase === 'failed'">
            <div>
              <p class="banner danger" x-text="t('web.system.update_failed')"></p>
              <p x-show="updateStatus.state.rolled_back" x-cloak
                 x-text="t('web.system.update_rolled_back', { version: updateStatus.state.to, from_version: updateStatus.state.from })"></p>
              <pre class="notes" x-text="updateStatus.log.slice(-8).join('\n')"></pre>
              <button @click="downloadFabricBackup()" x-text="t('web.system.backup_download')"></button>
            </div>
          </template>
```

- [ ] **Step 6: Change the disconnect banner's text during an update**

Am bestehenden Verbindungsbanner (`index.html:271`) die Bedingung um `&& !updateRunning()` erweitern und daneben eine zweite Zeile stellen:

```html
    <div class="banner warn" x-show="!socketConnected && updateRunning()" x-cloak
         x-text="t('web.system.update_restarting')"></div>
```

Ohne das sähe der geplante Neustart aus wie eine Störung — der Zustand, den dieser ganze Entwurf sichtbar machen soll.

- [ ] **Step 7: Check the Alpine bindings in a throwaway harness**

Ein Browsertest belegt nur die Auslieferung. Fahre die vier Zustände gegen erfundene Zustandsdateien:

```bash
mkdir -p /tmp/lox-update
export LOXMATTER_UPDATE_DIR=/tmp/lox-update
uv run python scripts/dev_web_server.py
```

Dann nacheinander, jeweils den System-Tab neu laden:

```bash
# kein Beiwagen
rm -f /tmp/lox-update/state.json
# bereit
printf '{"id":null,"phase":"idle","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
# läuft, Brücke weg
printf '{"id":"a","phase":"health","from":"0.2.0","to":"0.3.0","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
# fehlgeschlagen, zurückgesetzt
printf '{"id":"a","phase":"failed","from":"0.2.0","to":"0.3.0","rolled_back":true,"healthy":true,"error":"nicht gesund","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
```

Prüfe je Zustand: die richtige Darstellung erscheint, die Browserkonsole zeigt **keine** Alpine-Fehler, und im Zustand „läuft" sind die ersten drei Schritte abgehakt und der vierte hervorgehoben.

- [ ] **Step 8: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml tests/api/test_update_api.py
git commit -m "feat(web): die Update-Karte mit ihren vier Zustaenden

Der dritte Zustand ist der Grund fuer den ganzen Beiwagen: waehrend die
Bruecke neu startet, ist sie selbst nicht befragbar - der Fortschritt
kommt aus state.json im gemeinsamen Volume. Das Verbindungsbanner
bekommt fuer diese Zeit einen anderen Text, sonst saehe der geplante
Neustart aus wie eine Stoerung.

Der Zeitgeber laeuft nur waehrend eines Auftrags. Eine Anfrage pro
Sekunde fuer eine Angabe, die sich zehnmal im Jahr aendert, waere auf
einem Pi verschwendet.

Waehrend des Updates wird nichts gesperrt: eine Minute Bevormundung waere
der hoehere Preis, und wer mitten im Neustart etwas versucht, bekommt die
Erklaerung im Banner.

Ohne Beiwagen steht dort der Konsolenweg statt eines toten Knopfes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Der erste echte Durchlauf, das README und 0.3.0

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/OPERATIONS.md`, `scripts/capture_screenshots.py`, `pyproject.toml`
- Create: `docs/screenshots/update.png`

- [ ] **Step 1: Run a real update on the test host**

Voraussetzung: der Beiwagen läuft (Task 5), und es gibt eine neuere veröffentlichte Version als die laufende. Notfalls `v0.2.1` mit einer belanglosen Änderung taggen.

Im Browser, System-Tab: Knopf drücken, bestätigen, **hinsehen**. Erwartet:

- Die vier Schritte laufen sichtbar durch.
- Beim Neustart verschwindet die Verbindung, und es steht „Die Brücke startet neu" da — **kein rotes Störungsbanner**.
- Danach steht oben die neue Versionsnummer.
- `docker exec loxmatter ls /data/backups/` zeigt eine frische Sicherung.

- [ ] **Step 2: Provoke a real rollback**

Der wichtigste Prüfpunkt des ganzen Vorhabens, und er lässt sich nicht simulieren:

```bash
# Ein Image, das absichtlich nicht gesund wird
cd deploy/testhost
docker tag ghcr.io/lucienkerl/loxmatter:0.2.0 ghcr.io/lucienkerl/loxmatter:0.2.99
```

Dann in der Compose-Datei probeweise `LOXMATTER_HEALTH_TIMEOUT: "20"` setzen, den Beiwagen neu starten, in der Oberfläche auf `0.2.99` aktualisieren, und **den Netzstecker von matter-server ziehen** (`docker stop matter-server`), damit die Brücke nicht gesund wird.

Erwartet:

- Nach 20 s wechselt die Karte auf „Update fehlgeschlagen" mit „0.2.0 läuft wieder".
- `docker exec loxmatter cat /data/update/LETZTER-FEHLSCHLAG.txt` nennt beide Versionen, den Sicherungspfad und die drei Befehle.
- In der `.env` steht wieder `LOXMATTER_IMAGE_TAG=0.2.0`.
- **Genau zwei** `compose up`-Zeilen im Protokoll.

Danach `docker start matter-server`, den Timeout zurücksetzen, den Testtag löschen.

- [ ] **Step 3: Screenshot**

In `scripts/capture_screenshots.py`, im System-Ablauf: `shoot(page, "update", "card:Version")`

Run: `uv run python scripts/capture_screenshots.py`
Expected: `docs/screenshots/update.png` zeigt die Karte mit einem bereitstehenden Update. **`system.png` dabei verwerfen** — es ist nicht reproduzierbar.

- [ ] **Step 4: README and OPERATIONS**

Im `README.md`, den Abschnitt „Updating" aus Stufe 1 ergänzen:

```markdown
Since 0.3.0 you can do this from the browser instead: **System → Version**
shows what is running, tells you when a new version is available, and
installs it at the press of a button — with a backup taken first and an
automatic rollback if the new version does not come up healthy.

This needs the `loxmatter-updater` service from the compose file. If your
installation predates 0.3.0, bring it in once from the console:

```bash
cd ~/loxmatter && git pull && ./scripts/update.sh
```

That container holds the Docker socket and is therefore root-equivalent on
the host. It has no ports and no host network, it talks to the bridge only
through files in a shared volume, and it never runs text from a request as
a command — it can install a published loxmatter version and nothing else.
If you would rather not have it, delete the service; the bridge notices and
points you back to the console.
```

In `docs/OPERATIONS.md` einen Abschnitt „Wenn ein Update schiefgeht" mit dem Pfad von `LETZTER-FEHLSCHLAG.txt`, dem Sicherungsverzeichnis und dem Hinweis, dass die Datenbank bewusst nicht mit zurückgesetzt wird.

- [ ] **Step 5: Release 0.3.0**

`pyproject.toml` auf `0.3.0`, `CHANGELOG.md` ergänzen, dann nach der Regel aus `docs/DEVELOPMENT.md`:

```bash
git add -A && git commit -m "release: 0.3.0

Updates lassen sich ueber die Oberflaeche einspielen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git tag -a v0.3.0 -m "0.3.0" && git push && git push --tags
gh release create v0.3.0 --title "0.3.0" --notes-file -
```

Der Release-Text ist der Changelog-Abschnitt — **er ist der erste, den die Oberfläche jemandem im Bestätigungsdialog zeigt.** Entsprechend schreiben.

- [ ] **Step 6: Verify the loop closes**

Nach dem Release auf dem Test-Pi (der noch auf `0.2.x` steht): der System-Tab meldet von selbst „Version 0.3.0 verfügbar", und ein Klick spielt sie ein.

Damit ist der Weg über SSH für ein gewöhnliches Update abgeschafft — das Ziel dieses Entwurfs.

---

## Selbstprüfung dieses Plans

**Spec-Abdeckung** — jeder Abschnitt der Spec hat eine Task: 4 → Stufe 1/1–2; 5 → Stufe 1/5; 6 → Tasks 1, 5; 7 → Tasks 2, 6; 8 → Tasks 3, 4; 9 → Tasks 7, 9; 10 → Tasks 2, 5, 8; 11 → Tasks 6, 8, 9 (Erkennung), 10 (README); 12 → Task 9; 13 → jede Task; 14 → nichts zu bauen; 15 → die Aufteilung in zwei Pläne.

**Namensabgleich:** `update_dir` heißt in allen Signaturen gleich; `UpdateState.from_version`/`to_version` (Python, weil `from` ein Schlüsselwort ist) entsprechen `from`/`to` im JSON — die Umsetzung steht in `read_state` und in `_status()`; die Phasennamen sind in Task 2 abschließend aufgezählt und werden in Task 6 (`_LAUFENDE_PHASEN`) und Task 9 (`updateRunning()`) identisch benutzt.

**Erledigt:** `test_der_rueckfall_ruehrt_die_datenbank_nicht_an` prüfte ursprünglich mit `"-x" not in calls` auch auf ein entpackendes `tar` — grob genug, um an einem beliebigen anderen `-x`-Flag anzuschlagen. Der Test sieht sich jetzt nur die `tar`-Aufrufe an.

## Nachträge aus Stufe 1 (8. September 2026, nach dem Release 0.2.0)

Drei Dinge wurden erst sichtbar, als Stufe 1 tatsächlich lief:

1. **Die laufende Version kommt aus dem laufenden Container, nicht aus der `.env`.** Seit 0.2.0 trägt jede frische Installation `LOXMATTER_IMAGE_TAG=stable`, und „stable" ist keine Version. Der Vergleich „ist 0.3.0 neuer als stable" hat keine Antwort, und die Vorwärts-Prüfung aus Spec-Abschnitt 10, Regel 3, wäre auf jeder Standardinstallation stillschweigend wirkungslos geblieben. `running_version()` liest stattdessen `LOXMATTER_VERSION` aus dem laufenden Container — die Angabe, die Stufe 1 genau dafür ins Image gelegt hat.
2. **Der Rückfall pinnt eine konkrete Version.** Stand in der `.env` ein Alias, zeigt dieser in der Registry inzwischen auf die gescheiterte Fassung; ihn zurückzuschreiben hieße, sie beim nächsten `compose pull` wiederzuholen — ohne dass irgendetwas sich erinnert, dass je ein Rückfall stattfand.
3. **Eine Fassung ohne Versionsangabe wird abgelehnt**, statt geraten. Wer ein von Hand gebautes Image fährt, bekommt eine ehrliche Auskunft und den Konsolenweg.
