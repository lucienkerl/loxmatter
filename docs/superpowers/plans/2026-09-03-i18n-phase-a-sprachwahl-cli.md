# i18n Phase A: Sprachwahl-Infrastruktur + CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared, persistent language setting (`en`/`de`, default `en`) plus a reusable translation mechanism, and translate the entire CLI (`src/loxmatter/cli.py`) to use it.

**Architecture:** A new `loxmatter.i18n` package holds a flat, dotted-key YAML string table and a `t(key, **values)` lookup function backed by a process-global "current language" variable. A new `LocaleStore` (mirroring the existing `AuthStore`/`BridgeSettingsStore` pattern) persists the setting in the generic `setting` SQLite table already used by those two classes — no schema migration needed. `cli.py` resolves the language once at module import (env var override → stored setting → `en` default) for `--help` text, and every `typer.echo`/`_fail` call resolves it fresh at call time via `t()`.

**Tech Stack:** Python 3.12, Typer/Click, PyYAML (already a dependency), sqlite3 (stdlib), pytest.

## Global Constraints

- Default language is `en`; only `en` and `de` are supported values (spec section 1, 3).
- The language setting is a single, shared value for the whole installation — not per-command, not per-browser (user decision, recorded in spec intro).
- No new dependency and no build/extraction step — hand-maintained YAML, not `gettext`/`babel` (spec section 3).
- `LOXMATTER_LANG` environment variable overrides the stored setting for one process only; never writes back to storage (spec section 4, step 1).
- An invalid `LOXMATTER_LANG` value warns on stderr and falls back to the stored/default value — never a fatal error (spec section 4).
- The Click/Typer-generated chrome (`Usage:`, `Options:`, `Arguments:`, the word "Error") stays English always — only strings this project supplies are bilingual (spec section 2).
- Source comments, docstrings, and spec documents stay German throughout this change — only CLI-user-facing text (`help=`, `typer.echo`, `_fail` messages) becomes bilingual.
- `docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md` is the approved spec this plan implements; read it for the full rationale behind any decision referenced here as "per spec".

**Deviation from the spec, noted here because it wasn't resolved during brainstorming:** spec section 4 says the module-import-time DB read for the language setting uses "dieselbe Rangfolge `--store-path` > `LOXMATTER_STORE` > Standardpfad" as `_resolve_store_path`. That's not achievable literally — `--store-path` is a per-command CLI option, not parsed yet when the module top-level code runs. Task 3 below resolves the DB path via `_resolve_store_path(None)`, i.e. `LOXMATTER_STORE` env var → default path only. A command invoked with an explicit `--store-path` pointing at a *different* database will still show `--help` text and any warning in the language stored in the *default* database, not the one `--store-path` names — this is a narrow, inherent limitation of resolving language before argument parsing, not a bug to fix later.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/loxmatter/i18n/__init__.py` | `t()`, `set_language()`, `current_language()`, `SUPPORTED_LANGUAGES`, `DEFAULT_LANGUAGE` — the entire public API of the translation mechanism. |
| `src/loxmatter/i18n/strings.yaml` | The translation table itself: one entry per string, `en`/`de` pairs. Namespaced by dotted key prefix (`cli.*` in this phase; `test.*` for the mechanism's own tests). |
| `src/loxmatter/model/locale_store.py` | `LocaleStore` — reads/writes the `language` key in the shared `setting` table. Mirrors `auth_store.py`. |
| `src/loxmatter/model/store.py` | Modified: wires `self.locale = LocaleStore(self._db)` into `Store.__init__`, next to `self.auth`/`self.settings`. |
| `src/loxmatter/cli.py` | Modified throughout: bootstrap language resolution at module top, new `set-language` command, every `help=`/`typer.echo`/`_fail` string routed through `t()`. |
| `tests/test_i18n.py` | Unit tests for `t()`/`set_language()` against the real string table plus the `test.*` fixture entries. |
| `tests/conftest.py` | Modified: adds an autouse fixture resetting the global language after every test. |
| `tests/model/test_locale_store.py` | Unit tests for `LocaleStore`, mirroring `tests/model/test_auth_store.py`. |
| `tests/test_cli_language.py` | Tests for the CLI bootstrap resolution function and the `set-language` command; one `@pytest.mark.slow` subprocess test proving `--help` text actually changes language. |
| `tests/test_cli.py`, `tests/test_export_cli.py` | Modified: assertions on literal German CLI text updated to the new English default; a few assertions added under `LOXMATTER_LANG=de`/`i18n.set_language("de")` to prove translation actually happens. |

---

### Task 1: `i18n` package — translation mechanism + string table

**Files:**
- Create: `src/loxmatter/i18n/__init__.py`
- Create: `src/loxmatter/i18n/strings.yaml`
- Create: `tests/test_i18n.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `loxmatter.i18n.SUPPORTED_LANGUAGES: frozenset[str]` (`{"en", "de"}`), `loxmatter.i18n.DEFAULT_LANGUAGE: str` (`"en"`), `loxmatter.i18n.set_language(language: str) -> None` (raises `ValueError` for an unsupported value), `loxmatter.i18n.current_language() -> str`, `loxmatter.i18n.t(key: str, **values: object) -> str` (raises `KeyError` for an unknown key).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_i18n.py`:

```python
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

"""Tests fuer den Uebersetzungsmechanismus selbst - nicht fuer einzelne
CLI-Zeichenketten (die kommen in tests/test_cli.py bzw.
tests/test_cli_language.py dazu, sobald cli.py sie tatsaechlich nutzt).

`test.*`-Schluessel in strings.yaml sind absichtlich Teil der echten Tabelle,
nicht einer separaten Testdatei: `t()` haengt an genau einer Datei, und
`test.english_only` braucht einen echten, dauerhaft fehlenden `de`-Eintrag,
um den Ruecksicherungsfall zu belegen.
"""

from __future__ import annotations

import pytest

from loxmatter import i18n


def test_default_language_is_english():
    assert i18n.current_language() == "en"
    assert i18n.DEFAULT_LANGUAGE == "en"


def test_t_returns_english_by_default():
    assert i18n.t("test.greeting", name="Ada") == "Hello, Ada!"


def test_t_returns_german_after_set_language():
    i18n.set_language("de")
    assert i18n.t("test.greeting", name="Ada") == "Hallo, Ada!"


def test_t_falls_back_to_english_when_german_is_missing():
    i18n.set_language("de")
    assert i18n.t("test.english_only") == "English only"


def test_t_raises_for_an_unknown_key():
    with pytest.raises(KeyError):
        i18n.t("test.does_not_exist")


def test_set_language_rejects_an_unsupported_value():
    with pytest.raises(ValueError):
        i18n.set_language("fr")
    # Ein fehlgeschlagener Aufruf darf die aktuelle Sprache nicht aendern.
    assert i18n.current_language() == "en"


def test_supported_languages_are_exactly_en_and_de():
    assert i18n.SUPPORTED_LANGUAGES == frozenset({"en", "de"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.i18n'`

- [ ] **Step 3: Create the string table**

Create `src/loxmatter/i18n/strings.yaml`:

```yaml
# loxmatter - Uebersetzungstabelle.
#
# Flache, punktierte Schluessel statt verschachtelter YAML-Struktur: neue
# Namensraeume (api.*, web.* in spaeteren Phasen) kommen dazu, ohne
# bestehende Eintraege umzubauen. Jeder Eintrag traegt mindestens "en" -
# t() faellt bei fehlendem "de" automatisch auf "en" zurueck (siehe
# i18n/__init__.py), nie umgekehrt.
#
# test.* gehoert zu tests/test_i18n.py - dort absichtlich mit einem
# Eintrag OHNE "de", um den Ruecksicherungsfall an echten Daten zu
# belegen, statt eine zweite, nur fuer Tests geladene Datei zu pflegen.

test.greeting:
  en: "Hello, {name}!"
  de: "Hallo, {name}!"
test.english_only:
  en: "English only"

cli.app.help:
  en: "Matter → Loxone bridge"
  de: "Matter → Loxone Bridge"

cli.common.help_matter_url:
  en: "Address of matter-server"
  de: "Adresse von matter-server"
cli.common.help_node:
  en: "Node ID on the running matter-server"
  de: "Node-ID am laufenden matter-server"
cli.common.help_udp_port:
  en: "UDP port the Miniserver listens on"
  de: "UDP-Port, auf dem der Miniserver lauscht"
cli.common.help_store_path_short:
  en: "Database holding the signal keys. See --store-path under `export`."
  de: "Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
cli.common.echo_database_path:
  en: "Database: {path}"
  de: "Datenbank: {path}"
cli.common.fail_fixture_invalid_json:
  en: "Fixture {path} does not contain valid JSON: {exc}"
  de: "Fixture {path} enthält kein gültiges JSON: {exc}"
cli.common.fail_fixture_missing_node_id:
  en: "Fixture {path} has no 'node_id' field."
  de: "Fixture {path} hat kein Feld 'node_id'."
cli.common.error_need_node_or_fixture:
  en: "specify either --node or --fixture"
  de: "entweder --node oder --fixture angeben"
cli.common.fail_matter_unreachable:
  en: "matter-server at {url} unreachable — is the service running?"
  de: "matter-server unter {url} nicht erreichbar — läuft der Dienst?"
cli.common.fail_matter_not_ready:
  en: "matter-server at {url} connected, but did not report readiness: {exc}"
  de: "matter-server unter {url} hat sich verbunden, aber keine Bereitschaft gemeldet: {exc}"
cli.common.fail_node_unknown:
  en: "Node {node} is not known to matter-server ({url}) — commissioned?"
  de: "Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?"
cli.common.fail_target_dir:
  en: "Target directory {dir} could not be created: {exc}. Is the path writable?"
  de: "Zielverzeichnis {dir} konnte nicht angelegt werden: {exc}. Ist der Pfad beschreibbar?"
cli.common.fail_store_dir:
  en: "Directory {dir} could not be created: {exc}. Is the path writable?"
  de: "Verzeichnis {dir} konnte nicht angelegt werden: {exc}. Ist der Pfad beschreibbar?"
cli.common.fail_store_open:
  en: "Database {path} could not be opened: {exc}"
  de: "Datenbank {path} konnte nicht geöffnet werden: {exc}"

cli.inspect.help:
  en: "Lists all attributes and events of a device."
  de: "Listet alle Attribute und Events eines Geräts auf."
cli.inspect.help_fixture:
  en: "Use a saved snapshot instead of matter-server"
  de: "Statt matter-server ein gespeichertes Abbild"

cli.export.help:
  en: "Generates the Loxone templates for a device."
  de: "Erzeugt die Loxone-Vorlagen für ein Gerät."
cli.export.help_fixture:
  en: "Saved snapshot instead of a running matter-server"
  de: "Gespeichertes Abbild statt eines laufenden matter-server"
cli.export.help_bridge_ip:
  en: "IP of this bridge, as seen from the Miniserver"
  de: "IP dieser Bridge, aus Sicht des Miniservers"
cli.export.help_listen:
  en: "HTTP port in the generated command URL (VO template). Must match the --listen used when `loxmatter run` is started later — otherwise output commands go nowhere, without the Miniserver reporting it."
  de: "HTTP-Port in der erzeugten Kommando-URL (VO-Vorlage). Muss mit dem --listen übereinstimmen, mit dem `loxmatter run` später gestartet wird — sonst laufen die Ausgangsbefehle ins Leere, ohne dass der Miniserver das meldet."
cli.export.help_out:
  en: "Target directory for the templates"
  de: "Zielverzeichnis für die Vorlagen"
cli.export.help_store_path:
  en: "Database holding the signal keys. Default: ~/.loxmatter/loxmatter.sqlite — deliberately independent of the working directory. The keys in it are the wiring in Loxone; a relative path would miss the database when called from a different directory, assign the device a new device_id, and thereby silently destroy every existing wiring. Alternative: the LOXMATTER_STORE environment variable, e.g. for a mounted volume in a container."
  de: "Datenbank mit den Signalschlüsseln. Standard: ~/.loxmatter/loxmatter.sqlite — bewusst unabhängig vom Arbeitsverzeichnis. Die Schlüssel darin sind die Verdrahtung in Loxone; ein relativer Pfad würde bei einem Aufruf aus einem anderen Verzeichnis die Datenbank verfehlen, dem Gerät eine neue device_id zuweisen und damit jede bestehende Verdrahtung stillschweigend zerstören. Alternative über die Umgebungsvariable LOXMATTER_STORE, etwa für ein eingehängtes Volume im Container."
cli.export.help_raw_commands:
  en: "Also export commands from unknown clusters. Management clusters always stay locked."
  de: "Auch Kommandos unbekannter Cluster exportieren. Verwaltungscluster bleiben in jedem Fall gesperrt."
cli.export.help_system:
  en: "Also generates the device-independent templates (bridge_alive, /resync). Import once."
  de: "Erzeugt zusätzlich die geräteunabhängigen Vorlagen (bridge_alive, /resync). Einmalig zu importieren."
cli.export.echo_system_templates:
  en: "VIU_Matter_System.xml, VO_Matter_System.xml: heartbeat and /resync"
  de: "VIU_Matter_System.xml, VO_Matter_System.xml: Heartbeat und /resync"
cli.export.echo_viu_summary:
  en: "{filename}: {count} inputs"
  de: "{filename}: {count} Eingänge"
cli.export.echo_vo_summary:
  en: "{filename}: {count} output commands"
  de: "{filename}: {count} Ausgangsbefehle"
cli.export.echo_vo_skipped:
  en: "{filename}: skipped (no output commands, empty template)"
  de: "{filename}: übersprungen (keine Ausgangsbefehle, leere Vorlage)"
cli.export.echo_skipped_signals:
  en: "{count} signals not exportable (lists, structs, text, null values)"
  de: "{count} Signale nicht exportierbar (Listen, Strukturen, Texte, Nullwerte)"
cli.export.echo_hidden_signals:
  en: "{count} signals held back as expert (WebUI, \"Signals\" view, \"Expert\" section – enable them individually there)"
  de: "{count} Signale als Experte zurückgehalten (Weboberfläche, Ansicht „Signale“, Block „Experte“ – dort einzeln freischaltbar)"
cli.export.fail_write_first_file:
  en: "{path} could not be written: {exc}. No file has been created yet."
  de: "{path} konnte nicht geschrieben werden: {exc}. Es wurde noch keine Datei angelegt."
cli.export.fail_write_second_file:
  en: "{path} could not be written: {exc}. {written} has already been written, {missing} is missing."
  de: "{path} konnte nicht geschrieben werden: {exc}. Geschrieben wurde bereits {written}, es fehlt {missing}."

cli.run.help:
  en: "Connects Matter and Loxone permanently: values out, commands in."
  de: "Verbindet Matter und Loxone dauerhaft: Werte raus, Kommandos rein."
cli.run.help_miniserver:
  en: "IP of the Miniserver"
  de: "IP des Miniservers"
cli.run.help_listen:
  en: "Port for the HTTP commands coming from Loxone"
  de: "Port für die HTTP-Kommandos aus Loxone"
cli.run.help_host:
  en: "Address the HTTP service binds to. Default 0.0.0.0, because the Miniserver must be able to reach the service — see --api-token for the corresponding protection of the `/api` routes (Spec 9, Task 8)."
  de: "Adresse, an die der HTTP-Dienst bindet. Standard 0.0.0.0, weil der Miniserver den Dienst erreichen muss — siehe --api-token für die dazugehörige Absicherung der `/api`-Routen (Spec 9, Task 8)."
cli.run.help_api_token:
  en: "Protects the WebUI's `/api` routes (commissioning, removal, fabric backup) with `Authorization: Bearer <Token>` — an alternative to the signed-in session, not required in addition to it (Spec 9, Task 8; Spec 11). `/cmd` and `/resync` always stay open — the Miniserver cannot send a header. Only use characters allowed in an HTTP header — no spaces, no comma, ASCII; `openssl rand -hex 32` satisfies that. Alternative via the LOXMATTER_API_TOKEN environment variable."
  de: "Schützt die `/api`-Routen der WebUI (Einlernen, Entfernen, Fabric-Sicherung) mit `Authorization: Bearer <Token>` — alternativ zur angemeldeten Sitzung, nicht zusätzlich zu ihr erforderlich (Spec 9, Task 8; Spec 11). `/cmd` und `/resync` bleiben immer offen — der Miniserver kann keinen Header mitschicken. Nur Zeichen verwenden, die in einem HTTP-Header stehen dürfen — keine Leerzeichen, kein Komma, ASCII; `openssl rand -hex 32` erfüllt das. Alternative über die Umgebungsvariable LOXMATTER_API_TOKEN."
cli.run.help_matter_data_dir:
  en: "matter-server data directory (storage-path), mounted read-only into this service — basis for `GET /api/diagnostics/fabric-backup` (Spec 4.1, Task 6, Phase 5). Without it, the route responds with 503 instead of a backup. See deploy/testhost/docker-compose.yml for the corresponding volume mount."
  de: "matter-server-Datenverzeichnis (storage-path), read-only in diesen Dienst eingehängt — Grundlage für `GET /api/diagnostics/fabric-backup` (Spec 4.1, Task 6, Phase 5). Ohne Angabe antwortet die Route mit 503 statt einer Sicherung. Siehe deploy/testhost/docker-compose.yml für die dazugehörige Volume-Einhängung."
cli.run.warn_no_password:
  en: "No password has been set for this bridge yet. Until it is, nobody can sign in through the interface — and anyone who can reach the port can complete the initial setup and take over the bridge. Open the interface now and set a password."
  de: "Für diese Brücke ist noch kein Passwort vergeben. Bis das geschehen ist, lässt sich niemand über die Oberfläche anmelden — und jeder, der den Port erreicht, kann die Ersteinrichtung abschließen und die Brücke damit übernehmen. Öffne die Oberfläche jetzt und vergib ein Passwort."

cli.set_password.help:
  en: "Resets the interface password — the emergency exit for when it has been forgotten."
  de: "Setzt das Passwort der Oberfläche neu — der Notausgang für den Fall, dass es vergessen wurde."
cli.set_password.fail_db_not_found:
  en: "Database {path} was not found. `set-password` RESETS a password, so it deliberately does not create a new database — that would create an empty, unrelated database and report success while the actual bridge stayed locked. Check the path, pass it via --store-path, or — in the reference deployment — run the command inside the running container: `docker compose exec loxmatter loxmatter set-password`. On a fresh install from source, the database may simply not exist yet — it is created on the first `loxmatter run`."
  de: "Datenbank {path} wurde nicht gefunden. `set-password` setzt ein Passwort ZURÜCK und legt deshalb absichtlich keine neue Datenbank an — das würde eine leere Fremddatenbank erzeugen und Erfolg melden, während die eigentliche Brücke gesperrt bliebe. Prüfe den Pfad, gib ihn über --store-path an, oder führe den Befehl — im Referenz-Deployment — im laufenden Container aus: `docker compose exec loxmatter loxmatter set-password`. Bei einer frischen Installation aus dem Quellcode kann die Datenbank auch schlicht noch fehlen — sie entsteht erst beim ersten `loxmatter run`."
cli.set_password.prompt:
  en: "New password"
  de: "Neues Passwort"
cli.set_password.fail_too_short:
  en: "The password must be at least {min_length} characters long."
  de: "Das Passwort muss mindestens {min_length} Zeichen haben."
cli.set_password.echo_success:
  en: "Password set. All open sessions have been signed out."
  de: "Passwort gesetzt. Alle offenen Sitzungen wurden abgemeldet."

cli.set_language.help:
  en: "Sets the shared language setting (CLI and web interface)."
  de: "Setzt die gemeinsame Spracheinstellung (CLI und Weboberfläche)."
cli.set_language.help_language:
  en: "Target language: en or de."
  de: "Zielsprache: en oder de."
cli.set_language.fail_unsupported:
  en: "Unsupported language '{language}' — expected: {supported}."
  de: "Nicht unterstützte Sprache '{language}' — erwartet: {supported}."
cli.set_language.fail_db_not_found:
  en: "Database {path} was not found. `set-language` deliberately does not create a new database — run `loxmatter run` first, or pass the correct path via --store-path."
  de: "Datenbank {path} wurde nicht gefunden. `set-language` legt absichtlich keine neue Datenbank an — führe zuerst `loxmatter run` aus oder gib den richtigen Pfad über --store-path an."
cli.set_language.echo_success:
  en: "Language set to '{language}'."
  de: "Sprache auf '{language}' gesetzt."

cli.fake_miniserver.help:
  en: "Replaces the Miniserver: logs every datagram."
  de: "Ersetzt den Miniserver: schreibt jedes Datagramm mit."
cli.fake_miniserver.help_port:
  en: "UDP port to listen on"
  de: "UDP-Port, auf dem gelauscht wird"
cli.fake_miniserver.help_template:
  en: "Generated VIU_ template: reports at the end which signals never fired"
  de: "Erzeugte VIU_-Vorlage: nennt am Ende die Signale, die nie feuerten"
cli.fake_miniserver.fail_template_not_found:
  en: "Template {path} was not found."
  de: "Vorlage {path} wurde nicht gefunden."
cli.fake_miniserver.echo_listening:
  en: "fake-miniserver listening on UDP port {port} — Ctrl+C to stop"
  de: "fake-miniserver lauscht auf UDP-Port {port} — Strg-C zum Beenden"
cli.fake_miniserver.malformed:
  en: "BROKEN (no colon)"
  de: "KAPUTT (kein Doppelpunkt)"
cli.fake_miniserver.report_no_check_signals:
  en: "{template} contains no check signals — nothing to check."
  de: "{template} enthält keine Check-Signale — nichts zu prüfen."
cli.fake_miniserver.report_all_seen:
  en: "All {count} signals from {template} were seen at least once."
  de: "Alle {count} Signale aus {template} wurden mindestens einmal gesehen."
cli.fake_miniserver.report_silent_header:
  en: "{count} signals from {template} never seen:"
  de: "{count} Signale aus {template} nie gesehen:"
```

- [ ] **Step 4: Implement the mechanism**

Create `src/loxmatter/i18n/__init__.py`:

```python
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

"""Uebersetzungsmechanismus: eine flache YAML-Tabelle (`strings.yaml`) plus
eine prozessweite "aktuelle Sprache" - siehe
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md,
Abschnitt 3.

Eine einzige, gemeinsame Spracheinstellung fuer die ganze Installation
(nicht pro Anfrage, nicht pro Thread) - deshalb ein Modul-globaler Zustand
statt eines Objekts, das jeder Aufrufer selbst herumreichen muesste. Wer die
Sprache aendert (CLI-Bootstrap in cli.py, spaeter die WebUI in Phase B) ruft
`set_language()` genau einmal auf; jeder folgende `t()`-Aufruf im selben
Prozess sieht die neue Sprache sofort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "de"})
DEFAULT_LANGUAGE = "en"

_STRINGS_PATH = Path(__file__).with_name("strings.yaml")


def _load_strings() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(_STRINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{_STRINGS_PATH} muss eine Zuordnung auf oberster Ebene sein"
    return raw


# Einmal beim Import geladen, nicht bei jedem t()-Aufruf - strings.yaml
# aendert sich nie zur Laufzeit, nur zwischen Releases.
_STRINGS: dict[str, dict[str, str]] = _load_strings()

_current_language: str = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    """Setzt die prozessweite aktuelle Sprache.

    Wirft `ValueError` fuer alles ausser den Werten in `SUPPORTED_LANGUAGES`
    - und aendert die aktuelle Sprache in dem Fall NICHT (kein Teil-Erfolg)."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"nicht unterstuetzte Sprache {language!r}, erwartet eine von "
            f"{sorted(SUPPORTED_LANGUAGES)}"
        )
    global _current_language
    _current_language = language


def current_language() -> str:
    return _current_language


def t(key: str, **values: Any) -> str:
    """Liefert den uebersetzten Text zu `key` in der aktuellen Sprache,
    mit `values` in die Platzhalter eingesetzt (`str.format`).

    Fehlt `key` selbst in der Tabelle, ist das ein Programmierfehler -
    `KeyError` faellt durch, statt ihn zu verschlucken. Fehlt nur die
    Uebersetzung der aktuellen Sprache (z. B. noch kein "de" fuer einen
    neuen Eintrag), liefert diese Funktion die englische Fassung - nie
    einen Absturz wegen einer fehlenden Uebersetzung, siehe
    `test.english_only` in strings.yaml."""
    entry = _STRINGS[key]
    template = entry.get(_current_language, entry["en"])
    return template.format(**values)
```

- [ ] **Step 5: Add the language-reset fixture to conftest.py**

Edit `tests/conftest.py` — add the import and a second autouse fixture after `isolate_loxmatter_store`:

```python
from collections.abc import Iterator

from loxmatter import i18n
```

(add these two imports below the existing `from pathlib import Path` and `import pytest` lines)

```python
@pytest.fixture(autouse=True)
def reset_language() -> Iterator[None]:
    """Setzt die globale Spracheinstellung nach jedem Test zurueck.

    `loxmatter.i18n.set_language` haelt die aktuelle Sprache in einer
    prozessweiten Variable (eine gemeinsame Einstellung fuer die ganze
    Installation, siehe i18n/__init__.py) - ein Test, der `set_language("de")`
    aufruft und nicht zuruecksetzt, wuerde jeden nachfolgenden Test in
    derselben Session mit deutscher statt englischer Ausgabe konfrontieren."""
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
```

(append this fixture at the end of the file, after `isolate_loxmatter_store`)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: 7 passed

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests that passed before this task still pass (this task adds no CLI-visible behavior yet)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/i18n/ tests/test_i18n.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(i18n): Uebersetzungsmechanismus und Sprachtabelle

t()/set_language() plus die vollstaendige CLI-Uebersetzungstabelle
(cli.*) als YAML-Woerterbuch - noch nicht von cli.py verdrahtet, das
folgt in den naechsten Aufgaben. Teil von Phase A, siehe
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `LocaleStore` — persisted language setting

**Files:**
- Create: `src/loxmatter/model/locale_store.py`
- Modify: `src/loxmatter/model/store.py:703-717` (the `Store` class body — imports and `__init__`)
- Create: `tests/model/test_locale_store.py`

**Interfaces:**
- Consumes: `loxmatter.i18n.SUPPORTED_LANGUAGES`, `loxmatter.i18n.DEFAULT_LANGUAGE` (Task 1).
- Produces: `loxmatter.model.locale_store.LocaleStore(db: sqlite3.Connection)` with `.get_language() -> str` (never raises; returns `DEFAULT_LANGUAGE` if unset or the stored value is somehow invalid) and `.set_language(language: str) -> None` (raises `ValueError` for an unsupported value); `Store.locale: LocaleStore`, wired in `Store.__init__` next to `Store.auth`/`Store.settings`.

- [ ] **Step 1: Write the failing tests**

Create `tests/model/test_locale_store.py`:

```python
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

"""Tests fuer `LocaleStore` - die gemeinsame Spracheinstellung, gehalten in
derselben `setting`-Tabelle wie `AuthStore.password_hash` (siehe dortiges
test_auth_store.py fuer das gleiche Muster)."""

from __future__ import annotations

import pytest

from loxmatter.model.store import Store


def test_language_defaults_to_english_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_set_language_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.locale.set_language("de")
        assert store.locale.get_language() == "de"
    finally:
        store.close()


def test_set_language_can_be_changed_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.locale.set_language("de")
        store.locale.set_language("en")
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_set_language_rejects_an_unsupported_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            store.locale.set_language("fr")
        # Kein Teil-Erfolg: der Vorgabewert gilt weiterhin.
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_language_survives_reopening_the_same_database(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.locale.get_language() == "de"
    finally:
        reopened.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/model/test_locale_store.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'locale'`

- [ ] **Step 3: Implement `LocaleStore`**

Create `src/loxmatter/model/locale_store.py`:

```python
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

"""Die gemeinsame Spracheinstellung dieser Installation - EINE Einstellung
fuer CLI und (ab Phase B) WebUI, kein Feld pro Nutzer oder Browser. Siehe
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md,
Abschnitt 4.

Eigenes Modul und eigene Klasse, analog zu `auth_store.py` und
`settings_store.py`: die `setting`-Tabelle ist generisch angelegt, genau
damit weitere Konfiguration wie diese hier denselben Weg gehen kann. Diese
Klasse ist eine weitere Sicht auf dieselbe Tabelle und dieselbe Verbindung,
kein zweiter Verbindungsaufbau."""

from __future__ import annotations

import sqlite3

from loxmatter.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

_LANGUAGE_KEY = "language"


class LocaleStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `AuthStore`, nur fuer den Schluessel `"language"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_language(self) -> str:
        """Der gespeicherte Wert - `DEFAULT_LANGUAGE`, solange nichts
        gespeichert ist oder der gespeicherte Wert (z. B. nach einer
        kuenftigen Ruecknahme einer Sprache aus `SUPPORTED_LANGUAGES`)
        nicht mehr unterstuetzt wird. Wirft nie."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_LANGUAGE_KEY,)
        ).fetchone()
        if row is None:
            return DEFAULT_LANGUAGE
        value = str(row["value"])
        return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    def set_language(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"nicht unterstuetzte Sprache {language!r}, erwartet eine von "
                f"{sorted(SUPPORTED_LANGUAGES)}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LANGUAGE_KEY, language),
        )
        self._db.commit()
```

- [ ] **Step 4: Wire `LocaleStore` into `Store`**

Edit `src/loxmatter/model/store.py`. Add the import next to the existing `AuthStore`/`BridgeSettingsStore` imports (around line 51-52):

```python
from loxmatter.model.auth_store import AuthStore
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.settings_store import BridgeSettingsStore
```

Then in `Store.__init__` (around line 704-716), add the wiring next to `self.auth`/`self.settings`:

```python
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        _migrate(self._db)
        # Sicht auf dieselbe Verbindung, kein zweiter Verbindungsaufbau -
        # siehe Moduldocstring von `auth_store.py`.
        self.auth = AuthStore(self._db)
        # Sicht auf dieselbe Verbindung - siehe `settings_store.py`.
        self.settings = BridgeSettingsStore(
            self._db, default_udp_port=DEFAULT_UDP_PORT, default_listen_port=DEFAULT_LISTEN_PORT
        )
        # Sicht auf dieselbe Verbindung - siehe `locale_store.py`.
        self.locale = LocaleStore(self._db)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/model/test_locale_store.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass, no change in count of pre-existing failures

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/model/locale_store.py src/loxmatter/model/store.py tests/model/test_locale_store.py
git commit -m "$(cat <<'EOF'
feat(model): LocaleStore fuer die gemeinsame Spracheinstellung

Neuer Schluessel "language" in der bestehenden setting-Tabelle - keine
Schema-Migration noetig. Store.locale ist die dritte Sicht auf dieselbe
Verbindung, neben Store.auth und Store.settings.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: CLI bootstrap language resolution + `set-language` command

**Files:**
- Modify: `src/loxmatter/cli.py` (module top, around the existing imports and `_resolve_store_path`; new command after `set_password`)
- Create: `tests/test_cli_language.py`

**Interfaces:**
- Consumes: `loxmatter.i18n.{SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE, set_language, t}` (Task 1), `loxmatter.model.locale_store.LocaleStore` (Task 2), the existing `_resolve_store_path(explicit: Path | None) -> Path` and `_fail(message: str) -> NoReturn` in `cli.py`.
- Produces: `loxmatter.cli._resolve_cli_language(store_path: Path, env: Mapping[str, str]) -> str` (pure function, no side effects beyond an stderr warning on an invalid override), called once at module import to call `i18n.set_language(...)`; a new `loxmatter set-language <en|de>` command.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_language.py`:

```python
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

"""Tests fuer die Sprachaufloesung der CLI (`cli._resolve_cli_language`) und
das `set-language`-Kommando.

`_resolve_cli_language` wird als reine Funktion getestet, nicht ueber einen
Modul-Reload von `cli.py` - das Modul wird pro Testsession genau einmal
importiert (siehe `tests/test_cli.py`s `from loxmatter.cli import app`),
seine Modul-Top-Level-Aufloesung laesst sich deshalb nicht pro Test mit
unterschiedlichen Umgebungsvariablen wiederholen. Der EINE Test, der die
tatsaechliche `--help`-Ausgabe in einer anderen Sprache als der beim
Session-Start aufgeloesten belegt, startet dafuer bewusst einen echten
Unterprozess (siehe `test_help_text_is_german_when_loxmatter_lang_is_set`,
markiert `slow` wie `tests/api/test_live_smoke.py`)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from loxmatter import i18n
from loxmatter.cli import _resolve_cli_language, app
from loxmatter.model.store import Store


def test_env_override_wins_even_with_a_stored_setting(tmp_path):
    store_path = tmp_path / "t.sqlite"
    store = Store(store_path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()
    assert _resolve_cli_language(store_path, {"LOXMATTER_LANG": "en"}) == "en"


def test_env_override_is_case_insensitive(tmp_path):
    store_path = tmp_path / "missing.sqlite"
    assert _resolve_cli_language(store_path, {"LOXMATTER_LANG": "DE"}) == "de"


def test_invalid_env_override_warns_and_falls_back(tmp_path, capsys):
    store_path = tmp_path / "missing.sqlite"
    result = _resolve_cli_language(store_path, {"LOXMATTER_LANG": "fr"})
    assert result == i18n.DEFAULT_LANGUAGE
    assert "LOXMATTER_LANG" in capsys.readouterr().err


def test_falls_back_to_default_when_no_database_exists(tmp_path):
    store_path = tmp_path / "missing.sqlite"
    assert _resolve_cli_language(store_path, {}) == i18n.DEFAULT_LANGUAGE


def test_reads_the_stored_setting_when_no_override_is_given(tmp_path):
    store_path = tmp_path / "t.sqlite"
    store = Store(store_path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()
    assert _resolve_cli_language(store_path, {}) == "de"


def test_falls_back_to_default_when_the_database_file_is_not_a_database(tmp_path):
    store_path = tmp_path / "not-a-database.sqlite"
    store_path.write_text("not a sqlite file")
    assert _resolve_cli_language(store_path, {}) == i18n.DEFAULT_LANGUAGE


def test_set_language_command_persists_the_choice(tmp_path):
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()  # set-language legt absichtlich keine neue Datenbank an
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code == 0, result.stdout

    store = Store(store_path)
    try:
        assert store.locale.get_language() == "de"
    finally:
        store.close()


def test_set_language_command_rejects_a_missing_database(tmp_path):
    store_path = tmp_path / "does-not-exist.sqlite"
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code != 0


def test_set_language_command_rejects_an_unsupported_language(tmp_path):
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()
    result = CliRunner().invoke(app, ["set-language", "fr", "--store-path", str(store_path)])
    assert result.exit_code != 0


@pytest.mark.slow
def test_help_text_is_german_when_loxmatter_lang_is_set(tmp_path):
    """Der einzige Beleg dafuer, dass `--help`-Text tatsaechlich die
    Modul-Import-Zeit-Aufloesung durchlaeuft - `_resolve_cli_language`
    (oben) prueft nur die Aufloesungsfunktion fuer sich, nicht ihre
    Verdrahtung in `cli.py`s Modul-Top-Level."""
    env = dict(os.environ)
    env["LOXMATTER_LANG"] = "de"
    env["LOXMATTER_STORE"] = str(tmp_path / "unused.sqlite")
    result = subprocess.run(
        [sys.executable, "-c", "from loxmatter.cli import app; app()", "inspect", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Statt matter-server ein gespeichertes Abbild" in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_language.py -v -m "not slow"`
Expected: FAIL with `ImportError: cannot import name '_resolve_cli_language' from 'loxmatter.cli'`

- [ ] **Step 3: Add the bootstrap resolution and the `set-language` command**

Edit `src/loxmatter/cli.py`. Add these imports next to the existing `loxmatter.*` imports (around line 34-59):

```python
from loxmatter import i18n
from loxmatter.model.locale_store import LocaleStore
```

Add `Mapping` to the existing `from collections.abc import ...` import if not already there — check the current import line first; `cli.py` currently has no `collections.abc` import, so add a new line near the top-level imports:

```python
from collections.abc import Mapping
```

**Ordering matters here and is easy to get wrong: fix it exactly as described.** Today, in file order: `logger = logging.getLogger(__name__)`, then `app = typer.Typer(help="Matter → Loxone Bridge")`, then `@app.callback()\ndef main(): ...`, then `render_report`/`_fail`/`_ensure_out_dir`/`_load_fixture`/`_build_client`/`_load_snapshot`, then `_resolve_store_path`, then the `@app.command()` functions (`inspect`, `export`, ...). A Python module executes top-level statements in file order — `app = typer.Typer(help=i18n.t("cli.app.help"))` calls `i18n.t(...)` at the moment *that line* runs, using whatever language is current *then*, not whatever gets resolved afterwards. So the bootstrap call `i18n.set_language(_resolve_cli_language(...))` must run **before** the `app = typer.Typer(...)` line, not after it — placing it where `_resolve_store_path` currently sits (near the bottom of the helper functions, just before `inspect`) would leave `app`'s own help text permanently stuck on `DEFAULT_LANGUAGE`, even though every per-command `help=` (defined later in the file, after that point) would correctly pick up the resolved language. To get this right:

1. **Move** the entire existing `_resolve_store_path` function (currently defined just before `@app.command()\ndef inspect(...)`) up to immediately after the module's `logger = logging.getLogger(__name__)` line — i.e., *before* `app = typer.Typer(...)` is created. Nothing in `_resolve_store_path`'s body depends on anything defined later in the file (only `os.environ`, `Path.home()`), so this move is safe. Delete it from its old location — it must exist exactly once.
2. Immediately after the relocated `_resolve_store_path`, add the new `_resolve_cli_language` function and the bootstrap call shown below — both must still come before `app = typer.Typer(...)`, which item 3 below moves to right after them:

```python
def _resolve_cli_language(store_path: Path, env: Mapping[str, str]) -> str:
    """Bestimmt die Sprache fuer GENAU diesen Prozess, aufgerufen einmal
    beim Modulimport (siehe unten, vor `app = typer.Typer(...)`) - siehe
    Spec-Abschnitt 4.

    Rangfolge: `LOXMATTER_LANG` (dieser Aufruf, ohne die gespeicherte
    Einstellung zu aendern) > gespeicherte Einstellung > `DEFAULT_LANGUAGE`.
    Ein ungueltiger `LOXMATTER_LANG`-Wert warnt auf stderr und faellt auf
    die naechste Stufe zurueck - diese eine Warnung bleibt zwangslaeufig
    Englisch, die Sprache steht an dieser Stelle noch nicht fest.

    `store_path` ist NICHT `--store-path` (das ist zu diesem Zeitpunkt noch
    nicht geparst, siehe den Abschnitt "Deviation from the spec" im
    Implementierungsplan dieser Aufgabe) - der Aufrufer uebergibt
    `_resolve_store_path(None)`, also `LOXMATTER_STORE` oder den
    Standardpfad.

    Oeffnet die Datenbank nur lesend und nur fuer diese eine Abfrage - NICHT
    ueber `Store(...)`, das bei jedem Aufruf `CREATE TABLE IF NOT EXISTS`
    und Migrationen ausfuehrt und damit einen Schreibzugriff braucht, den
    ein blosses `--help` nie voraussetzen darf."""
    override = env.get("LOXMATTER_LANG")
    if override:
        candidate = override.strip().lower()
        if candidate in i18n.SUPPORTED_LANGUAGES:
            return candidate
        typer.echo(
            f"Warning: LOXMATTER_LANG={override!r} is not supported "
            f"(expected one of: {', '.join(sorted(i18n.SUPPORTED_LANGUAGES))}) - "
            "falling back to the stored or default language.",
            err=True,
        )
    if store_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        except (sqlite3.Error, OSError):
            return i18n.DEFAULT_LANGUAGE
        try:
            conn.row_factory = sqlite3.Row
            return LocaleStore(conn).get_language()
        except sqlite3.Error:
            return i18n.DEFAULT_LANGUAGE
        finally:
            conn.close()
    return i18n.DEFAULT_LANGUAGE


# Einmal beim Modulimport aufgeloest, vor jeder Kommandodefinition unten -
# `help=`-Texte sind Typer-Konstruktionsargumente und damit an dieser Stelle
# eingefroren (siehe Spec-Abschnitt 5). `typer.echo`/`_fail`-Aufrufe IN den
# Kommandos lesen `i18n.current_language()` dagegen bei jedem Aufruf frisch
# ueber `t()` - fuer sie ist dieser eine Bootstrap-Aufruf kein Einfrieren,
# nur der Startwert.
i18n.set_language(_resolve_cli_language(_resolve_store_path(None), os.environ))
```

3. **Move** `app = typer.Typer(help="Matter → Loxone Bridge")` and the `@app.callback()\ndef main(): ...` block right after it (unchanged, `main` stays exactly as it is — only `app`'s own line changes) down to immediately after the bootstrap call from step 2, replacing the `app = typer.Typer(...)` line with:

```python
app = typer.Typer(help=i18n.t("cli.app.help"))
```

After this step, the file's top-level order (down to the end of `main()`) is: imports/logger → `_resolve_store_path` → `_resolve_cli_language` → the bootstrap `i18n.set_language(...)` call → `app = typer.Typer(help=i18n.t("cli.app.help"))` → `@app.callback()\ndef main(): ...` → then `render_report`, `_fail`, `_ensure_out_dir`, `_load_fixture`, `_build_client`, `_load_snapshot` unchanged, then the `@app.command()` functions as before (with `_resolve_store_path` no longer duplicated at its old spot before `inspect`).

Add the `set-language` command after `set_password` (after its closing `typer.echo(...)` call, before `@app.command(name="fake-miniserver")`):

```python
@app.command(name="set-language")
def set_language_cmd(
    language: str = typer.Argument(..., help=i18n.t("cli.set_language.help_language")),
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.common.help_store_path_short")
    ),
) -> None:
    """Setzt die gemeinsame Spracheinstellung (CLI und, ab Phase B, WebUI).

    Verlangt wie `set_password` eine VORHANDENE Datenbank und aus demselben
    Grund: eine neue, leere Fremddatenbank auf dem Host anzulegen waere bei
    einer containerisierten Installation (`LOXMATTER_STORE` nur innerhalb des
    Containers erreichbar) ein stiller Fehlschlag mit gemeldetem Erfolg."""
    if language not in i18n.SUPPORTED_LANGUAGES:
        _fail(
            i18n.t(
                "cli.set_language.fail_unsupported",
                language=language,
                supported=", ".join(sorted(i18n.SUPPORTED_LANGUAGES)),
            )
        )
    resolved_store_path = _resolve_store_path(store_path)
    if not resolved_store_path.is_file():
        _fail(i18n.t("cli.set_language.fail_db_not_found", path=resolved_store_path))
    store = Store(resolved_store_path)
    try:
        store.locale.set_language(language)
    finally:
        store.close()
    typer.echo(i18n.t("cli.set_language.echo_success", language=language))
```

Add the `help=` attribute to `set_language_cmd`'s own command registration by changing the decorator to:

```python
@app.command(name="set-language", help=i18n.t("cli.set_language.help"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_language.py -v -m "not slow"`
Expected: 9 passed

Run: `uv run pytest tests/test_cli_language.py -v -m slow`
Expected: 1 passed

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass (this task does not yet change the German text of any existing `help=`/`echo`/`_fail` call — those are migrated in Tasks 4 and 5 — so no existing assertion should break yet)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/cli.py tests/test_cli_language.py
git commit -m "$(cat <<'EOF'
feat(cli): Sprachaufloesung beim Modulstart + set-language-Kommando

_resolve_cli_language() setzt i18n.set_language() einmal pro Prozess,
noch bevor irgendein Kommando existiert - LOXMATTER_LANG schlaegt die
gespeicherte Einstellung, die wiederum den Standard "en" schlaegt. Neues
Kommando `loxmatter set-language <en|de>` als einziger Weg, die
gespeicherte Einstellung zu setzen, bis Phase B der WebUI eine eigene
Umschaltflaeche gibt.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Migrate `inspect` and `export` to `t()`

**Files:**
- Modify: `src/loxmatter/cli.py` (the `inspect` command and everything `export` touches: `_ensure_out_dir`, `_load_fixture`, `_load_snapshot`, `export`)
- Modify: `tests/test_cli.py`
- Modify: `tests/test_export_cli.py`

**Interfaces:**
- Consumes: `i18n.t`, all `cli.inspect.*`/`cli.export.*`/`cli.common.*` keys from Task 1's `strings.yaml`.
- Produces: no new public interface — `inspect`/`export`'s CLI-visible output text changes from German-only to English-by-default, German when `i18n.current_language() == "de"`.

- [ ] **Step 1: Update existing assertions to the new English default**

In `tests/test_cli.py`, find `test_cli_reads_a_fixture_without_network` (already shown above) — it only asserts on `"TRADFRI bulb"`, which is device data, not CLI text, so it needs no change. Search the file for assertions on any of these German substrings and replace them with their English counterpart from `strings.yaml` (exact key in comment for traceability):

```python
# "nichts zu prüfen" -> cli.fake_miniserver.report_no_check_signals (unaffected by this task, listed here for completeness of the search — leave until Task 5)
```

Search `tests/test_cli.py` for `_fail`/echo assertions tied to `inspect`/`export`/`_load_fixture`/`_load_snapshot`/`_ensure_out_dir` German text (e.g. any occurrence of `"entweder --node oder --fixture"`, `"nicht erreichbar"`, `"nicht bekannt"`, `"kein gültiges JSON"`, `"kein Feld 'node_id'"`, `"konnte nicht angelegt werden"`) and replace each matched string with its English counterpart from the table above, e.g.:

```python
# before
assert "entweder --node oder --fixture angeben" in result.stdout
# after
assert "specify either --node or --fixture" in result.stdout
```

Apply the same substitution pattern in `tests/test_export_cli.py` for `"übersprungen"` → `"skipped"` (from `cli.export.echo_vo_skipped`) and any other literal German substring asserted there against `export` output (search for `Eingänge`, `Ausgangsbefehle`, `Signale nicht exportierbar`, `Signale als Experte zurückgehalten`, `Zielverzeichnis`, `Datenbank:`).

For each substitution, add one companion test right after the original asserting the German text still appears when the language is set to `de`, e.g.:

```python
def test_export_reports_skipped_output_commands_in_german():
    from loxmatter import i18n

    i18n.set_language("de")
    result = CliRunner().invoke(app, [...])  # same invocation as the English-language test above
    assert "übersprungen" in result.stdout
```

(exact invocation args copied from the test immediately above it in each file — do not guess; open the file and copy the real `CliRunner().invoke(...)` call for each test being duplicated)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_export_cli.py -v`
Expected: FAIL — the German-language companion tests fail because the source strings haven't moved to `t()` yet (the CLI still only ever prints German), and any English-language assertions you just wrote fail because the CLI still prints German.

- [ ] **Step 3: Migrate `inspect`**

Edit `src/loxmatter/cli.py`. Change the `inspect` command:

```python
@app.command(help=i18n.t("cli.inspect.help"))
def inspect(
    node: int | None = typer.Option(None, help=i18n.t("cli.common.help_node")),
    fixture: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.inspect.help_fixture")
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
) -> None:
    snapshot = _load_snapshot(fixture, node, url)
    typer.echo(render_report(snapshot))
```

(the original multi-line docstring `"""Listet alle Attribute und Events eines Geräts auf."""` is removed — its content now lives in `cli.inspect.help` via the `help=` decorator argument)

- [ ] **Step 4: Migrate the shared helpers `_load_fixture` and `_load_snapshot`**

```python
def _load_fixture(path: Path) -> NodeSnapshot:
    """Lädt eine Fixture-Datei; meldet kaputten Inhalt als CLI-Fehler statt
    mit einem rohen KeyError/JSONDecodeError abzubrechen."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(i18n.t("cli.common.fail_fixture_invalid_json", path=path, exc=exc))
    try:
        node_id = raw["node_id"]
    except (KeyError, TypeError):
        _fail(i18n.t("cli.common.fail_fixture_missing_node_id", path=path))
    return NodeSnapshot.from_raw(node_id, raw)
```

```python
def _load_snapshot(fixture: Path | None, node: int | None, url: str) -> NodeSnapshot:
    """Lädt ein Node-Abbild aus einer Datei oder von einem laufenden matter-server.

    Gemeinsam von `inspect` und `export` genutzt, damit die Fehlermeldungen
    dieses Pfads nur an einer Stelle stehen, statt in zwei Kommandos
    auseinanderzudriften.
    """
    if fixture is not None:
        return _load_fixture(fixture)
    if node is None:
        raise typer.BadParameter(i18n.t("cli.common.error_need_node_or_fixture"))

    async def run() -> NodeSnapshot:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(i18n.t("cli.common.fail_matter_unreachable", url=url))
        except MatterUnavailableError as exc:
            _fail(i18n.t("cli.common.fail_matter_not_ready", url=url, exc=exc))
        try:
            return await client.snapshot(node)
        except MatterUnavailableError:
            _fail(i18n.t("cli.common.fail_node_unknown", node=node, url=url))
        finally:
            await client.disconnect()

    return asyncio.run(run())
```

- [ ] **Step 5: Migrate `_ensure_out_dir`**

```python
def _ensure_out_dir(out: Path) -> None:
    """Legt das Zielverzeichnis an; meldet einen Fehlschlag als CLI-Fehler
    statt eines Tracebacks."""
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_target_dir", dir=out, exc=exc))
```

- [ ] **Step 6: Migrate `export`**

Replace every `typer.Option(..., help="...")` in the `export` signature:

```python
@app.command(help=i18n.t("cli.export.help"))
def export(
    fixture: Path | None = typer.Option(None, help=i18n.t("cli.export.help_fixture")),  # noqa: B008
    node: int | None = typer.Option(None, help=i18n.t("cli.common.help_node")),
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
    bridge_ip: str = typer.Option(..., help=i18n.t("cli.export.help_bridge_ip")),
    port: int = typer.Option(7000, help=i18n.t("cli.common.help_udp_port")),
    listen: int = typer.Option(8080, help=i18n.t("cli.export.help_listen")),
    out: Path = typer.Option(Path("."), help=i18n.t("cli.export.help_out")),  # noqa: B008
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.export.help_store_path")
    ),
    raw_commands: bool = typer.Option(
        False, "--raw-commands", help=i18n.t("cli.export.help_raw_commands")
    ),
    system: bool = typer.Option(False, "--system", help=i18n.t("cli.export.help_system")),
) -> None:
```

(remove the original docstring below the signature — its content now lives in `cli.export.help`; keep the function body's internal comments unchanged, only replace the string arguments below)

Within the function body, replace each occurrence:

```python
        typer.echo("VIU_Matter_System.xml, VO_Matter_System.xml: Heartbeat und /resync")
```
→
```python
        typer.echo(i18n.t("cli.export.echo_system_templates"))
```

```python
        try:
            viu_sys_path.write_bytes(viu_sys)
        except OSError as exc:
            _fail(
                f"{viu_sys_path} konnte nicht geschrieben werden: {exc}. "
                "Es wurde noch keine Datei angelegt."
            )
```
→
```python
        try:
            viu_sys_path.write_bytes(viu_sys)
        except OSError as exc:
            _fail(i18n.t("cli.export.fail_write_first_file", path=viu_sys_path, exc=exc))
```

```python
        try:
            vo_sys_path.write_bytes(vo_sys)
        except OSError as exc:
            _fail(
                f"{vo_sys_path} konnte nicht geschrieben werden: {exc}. "
                f"Geschrieben wurde bereits {viu_sys_path.name}, es fehlt {vo_sys_path.name}."
            )
```
→
```python
        try:
            vo_sys_path.write_bytes(vo_sys)
        except OSError as exc:
            _fail(
                i18n.t(
                    "cli.export.fail_write_second_file",
                    path=vo_sys_path,
                    exc=exc,
                    written=viu_sys_path.name,
                    missing=vo_sys_path.name,
                )
            )
```

```python
    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(f"Datenbank: {resolved_store_path.resolve()}")
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Verzeichnis {resolved_store_path.parent} konnte nicht angelegt werden: {exc}. "
            "Ist der Pfad beschreibbar?"
        )
```
→
```python
    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(i18n.t("cli.common.echo_database_path", path=resolved_store_path.resolve()))
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_store_dir", dir=resolved_store_path.parent, exc=exc))
```

```python
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"Datenbank {resolved_store_path} konnte nicht geöffnet werden: {exc}")
```
→
```python
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(i18n.t("cli.common.fail_store_open", path=resolved_store_path, exc=exc))
```

```python
    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(f"{viu} konnte nicht geschrieben werden: {exc}. Es wurde noch keine Datei angelegt.")
```
→
```python
    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(i18n.t("cli.export.fail_write_first_file", path=viu, exc=exc))
```

```python
    if commands:
        try:
            vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:{listen}", commands))
        except OSError as exc:
            _fail(
                f"{vo} konnte nicht geschrieben werden: {exc}. "
                f"Geschrieben wurde bereits {viu}, es fehlt {vo.name}."
            )
```
→
```python
    if commands:
        try:
            vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:{listen}", commands))
        except OSError as exc:
            _fail(
                i18n.t(
                    "cli.export.fail_write_second_file",
                    path=vo,
                    exc=exc,
                    written=viu,
                    missing=vo.name,
                )
            )
```

```python
    typer.echo(f"{viu.name}: {len(inputs)} Eingänge")
    if commands:
        typer.echo(f"{vo.name}: {len(commands)} Ausgangsbefehle")
    else:
        typer.echo(f"{vo.name}: übersprungen (keine Ausgangsbefehle, leere Vorlage)")
    typer.echo(f"{skipped} Signale nicht exportierbar (Listen, Strukturen, Texte, Nullwerte)")
    typer.echo(
        f"{hidden_count} Signale als Experte zurückgehalten "
        "(Weboberfläche, Ansicht „Signale“, Block „Experte“ – dort einzeln freischaltbar)"
    )
```
→
```python
    typer.echo(i18n.t("cli.export.echo_viu_summary", filename=viu.name, count=len(inputs)))
    if commands:
        typer.echo(i18n.t("cli.export.echo_vo_summary", filename=vo.name, count=len(commands)))
    else:
        typer.echo(i18n.t("cli.export.echo_vo_skipped", filename=vo.name))
    typer.echo(i18n.t("cli.export.echo_skipped_signals", count=skipped))
    typer.echo(i18n.t("cli.export.echo_hidden_signals", count=hidden_count))
```

Also update `_ensure_out_dir`'s two call sites and the `run() -> NodeSnapshot` inner function inside `_load_snapshot` — those were already handled in Step 4 above; no further change needed here.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_export_cli.py tests/test_i18n.py tests/test_cli_language.py -v -m "not slow"`
Expected: all pass

- [ ] **Step 8: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`
Expected: all pass. Then run the slow suite once: `uv run pytest -q -m slow` — expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/cli.py tests/test_cli.py tests/test_export_cli.py
git commit -m "$(cat <<'EOF'
refactor(cli): inspect und export ueber t() uebersetzt

help=, typer.echo und _fail-Meldungen von inspect, export und den
gemeinsam genutzten Ladehelfern (_load_fixture, _load_snapshot,
_ensure_out_dir) laufen jetzt ueber i18n.t() - Standardausgabe ist ab
jetzt Englisch, deutsch nur nach `set-language de` bzw. LOXMATTER_LANG=de.
Bestehende Tests auf englischen Text angepasst, deutsche Gegenstuecke
ergaenzt.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Migrate `run`, `set_password`, and `fake-miniserver` to `t()`

**Files:**
- Modify: `src/loxmatter/cli.py` (`_warn_if_no_password`, `run`, `set_password`, `fake_miniserver_cmd`, `_silent_keys_report`, `_fake_miniserver`)
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `i18n.t`, the `cli.run.*`/`cli.set_password.*`/`cli.fake_miniserver.*`/`cli.common.*` keys from Task 1's `strings.yaml`.
- Produces: no new public interface — completes the CLI-wide migration started in Task 4. After this task, every `help=`/`typer.echo`/`_fail`/`logger.warning` user-facing string in `cli.py` is bilingual.

- [ ] **Step 1: Update existing assertions to the new English default**

Search `tests/test_cli.py` for assertions on German substrings from `run`, `set_password`, and `fake-miniserver` output (e.g. `"kein Passwort vergeben"`, `"Passwort gesetzt"`, `"Datenbank"`, `"wurde nicht gefunden"`, `"nichts zu prüfen"`, `"nie gesehen"`, `"KAPUTT"`, `"lauscht auf UDP-Port"`) and replace each with its English counterpart, mirroring Task 4's Step 1 pattern — one substitution per assertion, one German-language companion test added per substitution (`i18n.set_language("de")` before invoking, same `CliRunner().invoke(...)` args as the test above it).

Pay particular attention to `test_run_installs_the_log_buffer_before_the_password_warning` (referenced in `cli.py`'s own docstrings for `run`/`_run`) — it very likely asserts on the literal German warning text from `_warn_if_no_password`; update it to the English text (`"No password has been set for this bridge yet."`) and add a German-language companion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -m "not slow"`
Expected: FAIL — same shape of failures as Task 4 Step 2, now for `run`/`set_password`/`fake-miniserver` assertions.

- [ ] **Step 3: Migrate `_warn_if_no_password`**

```python
def _warn_if_no_password(store: Store) -> None:
    """[... keep the existing German docstring unchanged ...]"""
    if store.auth.password_hash() is not None:
        return
    logger.warning(i18n.t("cli.run.warn_no_password"))
```

- [ ] **Step 4: Migrate `run`**

```python
@app.command(help=i18n.t("cli.run.help"))
def run(
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
    miniserver: str = typer.Option(..., help=i18n.t("cli.run.help_miniserver")),
    port: int = typer.Option(7000, help=i18n.t("cli.common.help_udp_port")),
    listen: int = typer.Option(8080, help=i18n.t("cli.run.help_listen")),
    host: str = typer.Option("0.0.0.0", help=i18n.t("cli.run.help_host")),
    api_token: str | None = typer.Option(
        None, "--api-token", envvar="LOXMATTER_API_TOKEN", help=i18n.t("cli.run.help_api_token")
    ),
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.common.help_store_path_short")
    ),
    matter_data_dir: Path | None = typer.Option(  # noqa: B008
        None, "--matter-data-dir", help=i18n.t("cli.run.help_matter_data_dir")
    ),
) -> None:
```

(remove the original docstring below the signature — its content now lives in `cli.run.help`; keep the function body's internal comments unchanged)

Within the body, replace:

```python
    typer.echo(f"Datenbank: {resolved_store_path.resolve()}")
```
→
```python
    typer.echo(i18n.t("cli.common.echo_database_path", path=resolved_store_path.resolve()))
```

```python
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Verzeichnis {resolved_store_path.parent} konnte nicht angelegt werden: {exc}. "
            "Ist der Pfad beschreibbar?"
        )
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"Datenbank {resolved_store_path} konnte nicht geöffnet werden: {exc}")
```
→
```python
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_store_dir", dir=resolved_store_path.parent, exc=exc))
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(i18n.t("cli.common.fail_store_open", path=resolved_store_path, exc=exc))
```

The two `except CannotConnect`/`except MatterUnavailableError` blocks inside `_run`'s `try: await client.connect()` (lines ~621-628) mirror `_load_snapshot`'s — replace identically:

```python
        try:
            await client.connect()
        except CannotConnect:
            _fail(i18n.t("cli.common.fail_matter_unreachable", url=url))
        except MatterUnavailableError as exc:
            _fail(i18n.t("cli.common.fail_matter_not_ready", url=url, exc=exc))
```

- [ ] **Step 5: Migrate `set_password`**

```python
@app.command(help=i18n.t("cli.set_password.help"))
def set_password(
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.common.help_store_path_short")
    ),
) -> None:
```

(remove the original docstring — content now in `cli.set_password.help`; keep body comments)

```python
    resolved_store_path = _resolve_store_path(store_path)
    if not resolved_store_path.is_file():
        _fail(i18n.t("cli.set_password.fail_db_not_found", path=resolved_store_path))
    password = typer.prompt(
        i18n.t("cli.set_password.prompt"), hide_input=True, confirmation_prompt=True
    )
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(i18n.t("cli.set_password.fail_too_short", min_length=MIN_PASSWORD_LENGTH))
    store = Store(resolved_store_path)
    try:
        store.auth.reset_password(hash_password(password))
    finally:
        store.close()
    # Bewusst ohne das Passwort in der Ausgabe - auch nicht verkuerzt.
    typer.echo(i18n.t("cli.set_password.echo_success"))
```

- [ ] **Step 6: Migrate `fake_miniserver_cmd`, `_silent_keys_report`, `_fake_miniserver`**

```python
@app.command(name="fake-miniserver", help=i18n.t("cli.fake_miniserver.help"))
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help=i18n.t("cli.fake_miniserver.help_port")),
    template: Path | None = typer.Option(  # noqa: B008
        None, help=i18n.t("cli.fake_miniserver.help_template")
    ),
) -> None:
    """[... keep the existing German docstring unchanged ...]"""
    if template is not None and not template.is_file():
        _fail(i18n.t("cli.fake_miniserver.fail_template_not_found", path=template))
    asyncio.run(_fake_miniserver(port, template))
```

```python
def _silent_keys_report(template_name: str, announced: set[str], silent: list[str]) -> str:
    """[... keep the existing German docstring unchanged ...]"""
    if not announced:
        return i18n.t("cli.fake_miniserver.report_no_check_signals", template=template_name)
    if not silent:
        return i18n.t(
            "cli.fake_miniserver.report_all_seen", count=len(announced), template=template_name
        )
    lines = [
        i18n.t(
            "cli.fake_miniserver.report_silent_header", count=len(silent), template=template_name
        )
    ]
    lines += [f"  {key}" for key in silent]
    return "\n".join(lines)
```

```python
async def _fake_miniserver(port: int, template: Path | None) -> None:
    # datetime.now() ohne tz ist hier Absicht: das ist die Ortszeit fuer einen
    # Menschen, der dem Terminal beim Draufschauen zusieht - keine
    # gespeicherte oder verglichene Zeit.
    def announce(key: str, value: str) -> None:
        typer.echo(f"{datetime.now():%H:%M:%S} {key} = {value}")  # noqa: DTZ005

    def announce_malformed(data: bytes) -> None:
        typer.echo(
            f"{datetime.now():%H:%M:%S} {i18n.t('cli.fake_miniserver.malformed')} ({data!r})",  # noqa: DTZ005
            err=True,
        )

    fake = FakeMiniserver(port=port, on_received=announce, on_malformed=announce_malformed)
    await fake.start()
    typer.echo(i18n.t("cli.fake_miniserver.echo_listening", port=fake.port))
    try:
        await asyncio.Event().wait()  # blockiert, bis Strg-C den Task abbricht
    finally:
        await fake.stop()
        if template is not None:
            announced = fake.announced_keys(template)
            silent = fake.silent_keys(template)
            typer.echo(f"\n{_silent_keys_report(template.name, announced, silent)}")
```

(note the wording change for `announce_malformed`: the original was `f"... KAPUTT (kein Doppelpunkt): {data!r}"` — a colon then the repr; the migrated version reads `f"... {t(...)} ({data!r})"` — parentheses instead of a colon, since `cli.fake_miniserver.malformed` is now a standalone phrase, not one ending in `:`. Update any test asserting on the literal `"KAPUTT (kein Doppelpunkt): "` substring to match the new `"BROKEN (no colon) ("` / `"KAPUTT (kein Doppelpunkt) ("` shape — search `tests/` for `KAPUTT` to find it.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v -m "not slow"`
Expected: all pass

- [ ] **Step 8: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`
Expected: all pass. Then: `uv run pytest -q -m slow` — expected: all pass.

Run: `uv run ruff check src/loxmatter/cli.py src/loxmatter/i18n/ src/loxmatter/model/locale_store.py`
Expected: no findings

Run: `uv run mypy src/loxmatter/cli.py src/loxmatter/i18n/ src/loxmatter/model/locale_store.py`
Expected: no findings (the project runs `mypy --strict`, per `pyproject.toml`; `i18n.t`'s `**values: Any` return and every `_fail`/`typer.echo` call site should type-check cleanly since `t()` returns `str` — the exact type `_fail`/`typer.echo` already expect)

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
refactor(cli): run, set-password und fake-miniserver ueber t() uebersetzt

Schliesst die CLI-weite Migration aus Task 4 ab - jede help=/echo/_fail/
logger.warning-Zeichenkette in cli.py laeuft jetzt ueber i18n.t().
Bestehende Tests auf englischen Text angepasst, deutsche Gegenstuecke
ergaenzt.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-Plan Verification

After Task 5, manually verify the two supported languages end-to-end once (not part of the automated suite, a final sanity check before considering Phase A done):

```bash
uv run loxmatter --help
uv run loxmatter set-language de --store-path /tmp/lang-check.sqlite   # expected: exits non-zero, "was not found" (no db yet)
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json --help
LOXMATTER_LANG=de uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json --help
```

The first `--help` and the third command should print English; the fourth should print German (`Statt matter-server ein gespeichertes Abbild` for `--fixture`).
