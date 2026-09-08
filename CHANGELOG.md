# Änderungen

Dieses Projekt vergibt ab 0.2.0 Versionsnummern nach [Semantic
Versioning](https://semver.org/lang/de/). Jede veröffentlichte Version
trägt hier einen Abschnitt, und die Oberfläche zeigt seinen Text als
Änderungsnotizen an, bevor jemand ein Update einspielt — er wird also von
Leuten gelesen, die den Code nicht kennen.

## [Unveröffentlicht]

## [0.2.0] — 2026-09-08

### Neu

- Die Oberfläche zeigt im System-Tab, welche Version läuft.
- Fertige Images liegen unter `ghcr.io/lucienkerl/loxmatter` bereit
  (`arm64` und `amd64`). Ein Update lädt jetzt ein solches Image, statt
  es auf dem Raspberry Pi selbst zu bauen — der lokale Build hat bisher
  fünf bis zehn Minuten gebraucht, und ein fertiges Image herunterzuladen
  sollte das deutlich abkürzen. Gemessen ist das noch nicht: es gibt
  bislang keinen Rechner, auf dem ein Update diesen Weg gegangen ist.

### Geändert

- `scripts/update.sh` zieht das Image, statt lokal zu bauen. `--build`
  stellt das alte Verhalten wieder her.
- `install.sh` startet den Stack jetzt ebenfalls ohne `--build`: `docker
  compose up -d` zieht das veröffentlichte Image. Ein `--build` hätte das
  Ergebnis unter dem Namen `ghcr.io/lucienkerl/loxmatter:stable` getaggt,
  auch wenn lokal gebaut wurde - eine frische Installation hätte dann ein
  lokales Image getragen, das sich selbst als `dev` meldet.
- Der Stack läuft aus einem veröffentlichten Image. **Diese eine
  Umstellung braucht einmalig die Konsole:** `git pull &&
  ./scripts/update.sh` auf dem Rechner, auf dem die Brücke läuft.
