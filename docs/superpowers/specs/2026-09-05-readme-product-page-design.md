# README als Produktseite — Entwurf

**Datum:** 2026-09-05
**Status:** abgestimmt, wartet auf Implementierungsplan

## 1. Ausgangslage

Die heutige `README.md` (404 Zeilen) ist ein Handbuch: deutsche Prosa,
technisch korrekt, chronologisch gewachsen. Sie beantwortet „wie betreibe ich
das" gut und „warum sollte mich das interessieren" gar nicht. Es gibt kein
einziges Bild der Oberfläche, obwohl die Oberfläche inzwischen das
Hauptprodukt ist.

Zwei konkrete Auslöser:

- Die Anwendung ist seit i18n-Phase B/C **englisch als Standardsprache**, die
  README ist rein deutsch — sie passt nicht mehr zu dem, was jemand nach dem
  ersten Start sieht.
- Als Vorbild dient die README von
  [`lucienkerl/mdb-esp32-cashless`](https://github.com/lucienkerl/mdb-esp32-cashless):
  zentrierter Hero, Badges, Feature-Tabelle mit Emoji, Screenshot-Galerie,
  ASCII-Architektur, Quickstart erst weiter unten, Detail-Dokumente daneben.

## 2. Abgestimmte Entscheidungen

| Frage | Entscheidung |
|---|---|
| Sprache | **Nur Englisch.** Eine Datei, keine Drift. Passt zum Englisch-Default der App und zur Vorbild-README. |
| Screenshots | **Selbst erzeugt** aus einer geseedeten Demo-Instanz (Fixture-Geräte), committet unter `docs/screenshots/`. Keine echten Daten. |
| Struktur | **Aufteilen.** README = Schaufenster; Betriebs- und Entwicklerdetails wandern nach `docs/`. |

Daraus folgt: die verschobenen Abschnitte werden beim Verschieben **übersetzt**.
Rund 300 Zeilen dichte deutsche Technik-Prosa. Code-Kommentare und die
`docs/superpowers/`-Spezifikationen bleiben unangetastet deutsch — nur die
nutzerseitige Dokumentation wird englisch.

## 3. Aufbau der neuen README

1. **Hero** — Icon, `# loxmatter`, Tagline, Ein-Satz-Pitch, Badges (GPL-3.0,
   Python 3.12+, CI), Sprungmarken-Zeile.
2. **Why loxmatter** — drei bis vier Sätze: Loxone spricht kein Matter; diese
   Brücke schließt die Lücke, selbst gehostet, ohne Cloud und ohne Handarbeit
   an XML.
3. **✨ What you can do** — zweispaltige HTML-Tabelle, sechs Zellen mit Emoji:
   Geräte einlernen · Signale gezielt auswählen · Loxone-Vorlagen erzeugen ·
   Projektdatei-Sync · Live-Diagnose · Zugangsschutz und Sprache.
4. **🖥 The web interface** — Screenshot-Galerie, zwei Bilder je Reihe, je eine
   fette Bildunterschrift plus eine Zeile Erklärung.
5. **🏗 How it works** — **Abweichung vom abgestimmten Entwurf:** statt eines
   neuen ASCII-Diagramms bleibt das **bestehende Mermaid-Diagramm** stehen, nur
   mit englischen Beschriftungen. Begründung: GitHub rendert Mermaid nativ, und
   das vorhandene Diagramm zeigt mit `otbr`, `matter-server`, Browser und allen
   Protokollen mehr, als eine ASCII-Nachzeichnung lesbar unterbringt. Darunter
   ein Absatz zum Datenfluss.
6. **🚀 Quickstart** — drei Schritte, kompakt, mit Verweis auf `docs/SETUP.md`
   für den vollständigen Weg.
7. **📚 Documentation** — Tabelle mit den vier neuen `docs/`-Dateien.
8. **🧰 Tech stack** — kleine Tabelle.
9. **🗺 Status** — der ehrliche Stand, siehe Abschnitt 6 dieses Entwurfs.
10. **🤝 Contributing** und **📄 License** — knapp, Lizenzdetails verlinkt.

## 4. Aufteilung nach `docs/`

| Alter Abschnitt | Neues Zuhause |
|---|---|
| Voraussetzungen, Erste Schritte (beide Wege), Ein Gerät ansehen | `docs/SETUP.md` |
| Dauerhaft betreiben (`run`), Zugangsschutz, Sprache, Export-Verhalten, Projektdatei-Sync-Details | `docs/OPERATIONS.md` |
| Entwickeln | `docs/DEVELOPMENT.md` |
| Fremdsoftware, Hinweise in den Quelldateien | `docs/LICENSING.md` |

Die README verlinkt alle vier. Bestehende Links auf
`docs/superpowers/specs/…` bleiben erhalten, sie wandern mit ihrem Abschnitt
mit.

## 5. Screenshots

Sieben Bilder unter `docs/screenshots/`:

| Datei | Zeigt |
|---|---|
| `dashboard.png` | Geräteliste mit Live-Werten, Kacheln je Gerät |
| `signals.png` | Signalansicht, funktional vs. Experte, Export-Haken |
| `export.png` | Export-Tab mit Vorschautabelle |
| `project-sync.png` | Projektdatei-Sync mit Diff-Plan |
| `system.png` | Live-Diagnose: Logzeilen, UDP-Mitschnitt, Kommando-Log |
| `settings.png` | Einstellungen samt Sprachumschalter |
| `commissioning.png` | Einlern-Karte oben im Geräte-Tab, mit Codefeld |

Erzeugt aus einer Demo-Instanz: ein temporärer Store, geseedet aus
`tests/fixtures/nodes/*.json` (vier Geräte), Passwort gesetzt, Bridge-IP und
Ports vorbelegt, einige Signale als exportiert markiert. Für
`project-sync.png` wird die Beispiel-Projektdatei aus
`tests/projectsync/conftest.py` durch den echten Endpunkt geschickt, damit der
Plan echte Einträge zeigt.

Das Seed-Skript wird als `scripts/demo_instance.py` **mitcommittet**: die
Oberfläche hat sich in einer Woche dreimal geändert; ohne reproduzierbaren
Weg veralten die Bilder still. Das Skript startet die App über `build_app()`
ohne Matter-Client — es braucht keine Hardware.

## 6. Harte Randbedingung: die Warnhinweise bleiben

Eine werblichere README darf die unbequemen Stellen nicht wegputzen. Diese
Aussagen müssen wortgleich in der Sache erhalten bleiben — im `Status`-Block
der README oder in der jeweiligen `docs/`-Datei, verlinkt:

- Der **Durchstich gegen einen echten Miniserver fehlt** — die erzeugten
  Vorlagen wurden nie in Loxone Config importiert.
- **Kein TLS.** Passwort und Token gehen im Klartext über das Netz.
- **Trust on first use:** zwischen Dienststart und erster Passwortvergabe kann
  jeder im Netz die Brücke übernehmen.
- `/cmd` ist ein **GET ohne Ursprungsprüfung** und damit von jeder Webseite
  auslösbar, die jemand im selben Netz öffnet.
- Beim Projektdatei-Sync sind **neue Geräte-Container experimentell** und das
  ID-Schema unverifiziert.
- `deploy/testhost/` ist **kein gehärtetes Produktions-Image**.
- Der Schema-Umzug **setzt gesetzte Export-Haken zurück**.
- Ein Sprachwechsel wirkt nur auf **neu** erzeugte Vorlagen.

Die drei erstgenannten gehören sichtbar in die README selbst, nicht nur in ein
verlinktes Dokument.

## 7. Abgrenzung

Nicht Teil dieser Arbeit:

- Keine Änderung an Anwendungscode, Verhalten oder Tests der App.
- Code-Kommentare und `docs/superpowers/`-Spezifikationen bleiben deutsch.
- Kein neues Logo, keine CI-Änderung, keine GitHub-Pages-Seite.
- **Das One-Liner-Installskript entsteht in einer eigenen Session** (so
  gewünscht). Der Quickstart beschreibt zunächst die manuelle Installation;
  wer das Skript baut, zieht Schritt 1 nach.

## 8. Risiken

| Risiko | Umgang |
|---|---|
| Screenshots veralten | Seed-Skript mitcommitten, Erzeugung im Skript dokumentieren |
| Warnhinweise gehen beim Umbau verloren | Abschnitt 6 als Prüfliste; Abnahme prüft jeden Punkt einzeln |
| Übersetzungsfehler in dichter Technik-Prosa | Beim Verschieben Abschnitt für Abschnitt, nicht frei nacherzählt |
| Tote Links nach dem Verschieben | Am Ende alle relativen Links maschinell prüfen |
