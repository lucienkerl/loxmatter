# Internationalisierung, Phase A: Sprachwahl-Infrastruktur + CLI

Entwurf, 3. September 2026. Erster Teil einer dreiteiligen Internationalisierung
(Wunsch: Grundsprache Englisch, umschaltbar auf Deutsch — bisher war jeder
Nutzertext im Projekt ausschließlich Deutsch). Diese Phase baut die
Sprachwahl-Infrastruktur und übersetzt die CLI vollständig; Phase B (API +
WebUI, wegen geteilter Fehlertexte zusammen) und Phase C (Texte in den
generierten Loxone-Vorlagen) folgen als eigene Entwürfe und Umsetzungen und
nutzen die hier gebaute Infrastruktur unverändert weiter.

Dieser Entwurf ändert **nicht** die Sprache des Projekts selbst — Quelltext-
Kommentare, Docstrings und die Entwurfsdokumente unter `docs/superpowers/specs/`
bleiben Deutsch, wie in [[user-lucien-loxmatter]] festgehalten. Es geht
ausschließlich um Text, den ein Betreiber oder Nutzer der Bridge zu Gesicht
bekommt.

## 1. Ziel

Eine einzige, dauerhafte Spracheinstellung pro Installation (nicht pro
CLI-Aufruf, nicht pro Browser) mit den Werten `en` (Vorgabe) und `de`, die
später sowohl die CLI-Ausgabe als auch die WebUI steuert. In dieser Phase:
die Einstellung selbst, ihr Speicherort, ihre Auflösung, und ein
wiederverwendbarer Übersetzungsmechanismus — erprobt an der kompletten CLI
(`src/loxmatter/cli.py`, aktuell rund 60 deutsche Zeichenketten: 25
`help=`-Texte, 15 `typer.echo`-Meldungen, 20 `_fail`-Fehlermeldungen).

## 2. Nicht-Ziele dieser Phase

- **WebUI** (`src/loxmatter/web/`) und **API-Fehlermeldungen**
  (`HTTPException(..., detail=...)` in `src/loxmatter/api/*.py`) — folgen in
  Phase B. Beide hängen zusammen: `app.js` zeigt `detail`-Texte direkt als
  Meldung an (siehe `readErrorDetail` in `app.js`), eine Trennung der beiden
  Oberflächen ergäbe keinen sauberen Schnitt.
- **Text in exportierten Loxone-Vorlagen** (`export/signals.py`,
  `export/xml.py` — `title`/`comment`-Felder, die in Loxone Config selbst
  sichtbar werden) — folgt in Phase C. Eigene Fragen dort: in welcher Sprache
  ein Anwender seine Loxone-Config typischerweise führt, ob ein rückwirkender
  Sprachwechsel bestehende Vorlagen berühren darf.
- **Das von Typer/Click selbst erzeugte Gerüst** einer `--help`-Ausgabe
  (`Usage:`, `Options:`, `Arguments:`, das Wort „Error“ vor einer
  `_fail`-Meldung) bleibt dauerhaft Englisch — das ist eine Grenze des
  verwendeten Frameworks (Click liefert dafür keine praktikable
  Übersetzungsschnittstelle), keine offene Aufgabe für eine spätere Phase.
- Kein automatisierter Übersetzungs-Workflow (keine Extraktion aus dem
  Quelltext, kein Übersetzungsdienst) — bei rund 60 Zeichenketten in dieser
  Phase wird von Hand übersetzt und gepflegt.

## 3. Übersetzungsmechanismus

Neues Paket `src/loxmatter/i18n/`:

```
i18n/
  __init__.py    t(), resolve_language(), SUPPORTED_LANGUAGES
  strings.yaml   Uebersetzungstabelle
```

`strings.yaml` ist eine flache Tabelle mit punktierten Namensräumen, ein
Eintrag pro Zeichenkette:

```yaml
cli.inspect.help_node:
  en: "Node ID on the running matter-server"
  de: "Node-ID am laufenden matter-server"
cli.export.fail_matter_unreachable:
  en: "matter-server at {url} unreachable — is the service running?"
  de: "matter-server unter {url} nicht erreichbar — läuft der Dienst?"
```

Der Namensraum `cli.*` deckt diese Phase ab; Phase B ergänzt `api.*` und
`web.*` in derselben Datei, ohne dass diese Phase etwas umbauen muss —
genau deshalb eine flache, punktierte Tabelle statt einer Datenstruktur, die
an die CLI gebunden ist.

**Begründung für YAML statt `gettext`:** `gettext` ist Industriestandard,
verlangt aber `.po`/`.mo`-Kataloge und ein Extraktions-/Kompilierwerkzeug
(typischerweise `babel`) — zusätzliche Maschinerie für zwei von Hand
gepflegte Sprachen. PyYAML ist bereits eine Abhängigkeit
(`pyproject.toml`), und `profiles/clusters.yaml` etabliert bereits das
Muster „Fachdaten in YAML, kein Python". Kein neues Paket, kein Build-Schritt.

`t()`:

```python
def t(key: str, **values: object) -> str:
    entry = _STRINGS[key]                    # KeyError = Programmierfehler, soll auffallen
    template = entry.get(_current_language(), entry["en"])
    return template.format(**values)
```

Fehlt die deutsche Übersetzung eines vorhandenen Schlüssels, liefert `t()`
automatisch die englische — nie ein Absturz wegen einer fehlenden
Übersetzung, nur wegen eines fehlenden *Schlüssels* (Tippfehler beim
Aufrufer), was ein Test abfangen soll, kein Nutzer je zu Gesicht bekommt.
`.format(**values)` deckt jede heutige Interpolation ab (Pfade, URLs,
Node-IDs, Exception-Text) — keiner der ~60 Aufrufe braucht mehr als
benannte Platzhalter.

## 4. Speicherort und Auflösung der Spracheinstellung

Kein neues Tabellenschema: die generische `setting`-Tabelle
(`model/store.py`, bereits Grundlage von `AuthStore` und
`BridgeSettingsStore`) bekommt einen weiteren Schlüssel, `"language"`. Neue
Klasse `model/locale_store.py::LocaleStore`, nach demselben Muster:

```python
class LocaleStore:
    def get_language(self) -> str: ...        # "en", falls nichts gespeichert
    def set_language(self, language: str) -> None: ...
```

**Auflösung** (jeder CLI-Aufruf ist ein neuer Prozess, die Sprache wird
einmal beim Modulstart von `cli.py` bestimmt — auch für `--help`):

1. `LOXMATTER_LANG` (Umgebungsvariable, wie `LOXMATTER_STORE` und
   `LOXMATTER_API_TOKEN`): ein gültiger Wert (`en`/`de`, Groß-/Kleinschreibung
   ignoriert) gilt für diesen einen Prozess und schlägt alles Folgende.
   Ändert die gespeicherte Einstellung nicht.
2. Sonst: die in der Datenbank gespeicherte Einstellung, falls die
   Datenbankdatei existiert und lesbar ist — Pfadauflösung identisch zu
   `_resolve_store_path` (dieselbe Rangfolge `--store-path` >
   `LOXMATTER_STORE` > Standardpfad).
3. Sonst: `en`.

Eine ungültige `LOXMATTER_LANG`-Umgebungsvariable (weder `en` noch `de`,
Groß-/Kleinschreibung ignoriert) erzeugt eine Warnung auf stderr und fällt
auf Schritt 2/3 zurück — kein Abbruch, denn ein sinnvoller Standard existiert
immer.

Schritt 2 öffnet dafür beim Modulimport von `cli.py` kurz die Datenbank
(read-only genügt) und liest genau einen Schlüssel. Fehlt die Datei, ist sie
kaputt, oder fehlt die Berechtigung — jeder dieser Fälle fällt still auf `en`
zurück, exakt wie andere Stellen in `cli.py` bereits eine fehlende oder
unlesbare Datenbank behandeln. `--help` funktioniert dadurch unverändert
ohne jede Vorbereitung, auch bei einer frischen Installation ganz ohne
Datenbank.

## 5. CLI-Integration

Alle ~60 Zeichenketten in `cli.py` wandern hinter `t("cli.<command>.<zweck>")`.
Ein neuer Befehl setzt die Einstellung:

```bash
loxmatter set-language en
loxmatter set-language de
```

Kein Passwort-artiger „Notausgang" wie `set-password` (kein Geheimnis, keine
Bestätigungseingabe) — bis Phase B der WebUI eine eigene Umschaltfläche
gibt, ist dieser Befehl der einzige Weg, die gespeicherte Einstellung zu
ändern. Er verlangt wie `set-password` eine **vorhandene** Datenbank (`_fail`,
falls nicht) — dieselbe Begründung: eine neue, leere Fremddatenbank auf dem
Host anzulegen wäre bei einer containerisierten Installation (`LOXMATTER_STORE`
nur innerhalb des Containers erreichbar) ein stiller Fehlschlag mit
gemeldetem Erfolg.

## 6. Tests

Rund zehn bestehende Assertions auf wörtlichen deutschen CLI-Text
(`tests/test_cli.py`, `tests/test_export_cli.py`) werden auf den neuen
englischen Standardtext angepasst — Englisch ist ab dieser Phase die
Standardausgabe, diese Tests prüfen unverändert *dass* eine bestimmte
Meldung erscheint, nur in der neuen Standardsprache.

Neu: `tests/test_i18n.py` prüft den Mechanismus selbst, unabhängig von der
CLI — Interpolation, Rückfall auf Englisch bei fehlender Übersetzung,
`LOXMATTER_LANG`-Override, Verhalten bei ungültigem Wert. Ergänzend ein
kleiner Satz CLI-Tests, die gezielt mit `LOXMATTER_LANG=de` laufen und
mindestens je eine `help=`-, `echo`-, und `_fail`-Meldung auf deutschen Text
prüfen — als Beleg, dass die Übersetzung tatsächlich ankommt, nicht als
Vollabdeckung aller ~60 Zeichenketten in beiden Sprachen.

## 7. Ausblick

Phase B (API + WebUI) und Phase C (Loxone-Vorlagentexte) sind eigene,
spätere Entwürfe. Beide setzen ausschließlich auf das hier gebaute
`i18n`-Paket und die `LocaleStore`-Auflösung auf — für die WebUI kommt hinzu,
wie ein langlebiger Serverprozess eine zur Laufzeit geänderte Einstellung
ausliefert (anders als die CLI, die pro Aufruf neu startet), das ist
Gegenstand von Phase B, nicht dieser Infrastruktur.
