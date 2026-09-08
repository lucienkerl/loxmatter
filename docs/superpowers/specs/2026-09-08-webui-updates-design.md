# Updates über die Oberfläche einspielen

Entwurf, 8. September 2026. Betrifft `deploy/testhost/docker-compose.yml`,
`Dockerfile`, `.github/workflows/ci.yml`, `scripts/update.sh`, den
System-Tab der WebUI und eine neue Routengruppe `/api/update/*`. Bringt
einen neuen Dienst in den Stack (`loxmatter-updater`) und ein zweites, von
derselben CI gebautes Image.

## 1. Das Problem

Ein Update kostet heute eine SSH-Sitzung. Wer die Brücke betreibt, muss
den Rechner finden, sich anmelden, ins richtige Verzeichnis wechseln und
`./scripts/update.sh` aufrufen. Das ist für jemanden, der ein Haus
automatisiert und keine Server verwaltet, die Stelle, an der er aufhört —
und er betreibt danach auf unbestimmte Zeit eine Fassung mit Fehlern, die
längst behoben sind.

Zwei Dinge stehen dem im Weg, und beide sind grundsätzlicher als eine
fehlende Schaltfläche.

**Erstens weiß niemand, was läuft.** `version = "0.1.0"` steht seit 628
Commits unverändert in `pyproject.toml`, es gibt kein einziges Tag, kein
Release, und die Oberfläche zeigt nirgends eine Version an. Ein Update
setzt voraus, dass „vorher" und „nachher" benennbar sind. Solange die
einzige Antwort auf „welcher Stand ist das?" ein git-Hash im Checkout auf
dem Host ist, den der laufende Prozess nicht kennt, gibt es kein Update —
nur ein Neustarten mit unklarem Ausgang.

**Zweitens kann sich die Brücke nicht selbst ersetzen.** Sie läuft in
einem Container, ohne Docker-Socket. Der Prozess, der
`docker compose up -d --force-recreate` aufriefe, wäre genau der, den
dieser Aufruf beendet — mitten im eigenen Arbeitsschritt, ohne die
Möglichkeit, das Ergebnis zu melden. Es braucht etwas außerhalb des
Containers, das den Neustart überlebt.

Dazu kommt eine dritte, kleinere Unbequemlichkeit, die sich mit erledigen
lässt: `docker compose build` baut auf einem Raspberry Pi mehrere Minuten
und kann an einem PyPI-Ausfall oder am Speicher scheitern. Was im Terminal
ein langer Balken ist, wäre im Browser eine Zumutung.

## 2. Was dieser Entwurf will

1. Die Oberfläche **benennt, welche Version läuft** — dauerhaft, nicht nur
   wenn etwas ansteht.
2. Sie **meldet, wenn eine neuere bereitsteht**, in einem von zwei
   Kanälen (Stabil, Entwicklung).
3. Ein Klick, eine Bestätigung, und die Brücke **spielt das Update selbst
   ein** — sichtbar, Schritt für Schritt, auch während sie selbst gerade
   nicht antwortet.
4. Kommt sie nicht gesund zurück, **setzt sie sich selbsttätig zurück**,
   ohne dass jemand davor sitzt.

## 3. Was unverändert bleibt

- `scripts/update.sh` bleibt als Weg über die Konsole bestehen und wird auf
  denselben Ablauf umgestellt. Der Beiwagen ist ein zusätzlicher Weg, kein
  Ersatz.
- matter-server und otbr werden **nicht** angefasst. Ihr Zustand hängt an
  Volumes — Thread-Netzschlüssel, Fabric-Credentials —, und ein
  misslungenes Update dort kostet das Neupaaren jedes Geräts. Die
  Oberfläche zeigt ihre laufenden Images nur an. Die Struktur sieht je
  Dienst einen eigenen, einzeln bestätigten Knopf vor; gebaut wird er
  später, mit eigener Warnung für otbr.
- Signalschlüssel, Räume, Exporteinstellungen: unberührt. Ein Update
  tauscht Code, keine Daten.
- Der `build:`-Block bleibt erhalten, hinter einem Compose-Profil. Aus der
  Quelle bauen bleibt möglich.

## 4. Versionsidentität

Ohne eine belastbare Antwort auf „was läuft hier gerade" trägt der ganze
Rest nicht. Sie kommt aus dem Image, nicht aus dem Checkout auf dem Host —
der kann inzwischen woanders stehen, umgezogen oder weitergewandert sein,
ohne dass das je ausgeliefert wurde.

Der Build bekommt vier Argumente mit und legt sie als `ENV` ins Image:

| Variable | Inhalt |
|---|---|
| `LOXMATTER_VERSION` | Tag (`0.3.0`) oder `dev` |
| `LOXMATTER_COMMIT` | kurzer SHA |
| `LOXMATTER_BUILT_AT` | Zeitpunkt des Baus |
| `LOXMATTER_SCHEMA_VERSION` | `_SCHEMA_VERSION` aus `model/store.py` |

Die letzte Zeile ist kein Beiwerk. Sie erlaubt, das Schema eines Images
**per `docker inspect` zu lesen, ohne den Container zu starten** — davon
hängt die Vorabprüfung in Abschnitt 8 ab. Ihren Wert liest die CI vor dem
Build aus `model/store.py` und reicht ihn als Build-Argument durch; er
wird nicht von Hand gepflegt. Eine zweite Stelle, die dieselbe Zahl
behauptet, wäre eine Stelle, die irgendwann etwas anderes behauptet.

`GET /api/version` gibt das aus, dazu die laufenden Images von
matter-server und otbr (die kommen aus `docker inspect` über den Beiwagen;
die Brücke selbst hat keinen Socket).

## 5. Was CI baut

| Auslöser | Tags |
|---|---|
| Push auf `main` | `:dev`, `:sha-<kurz>` |
| Tag `v*` | `:<version>`, `:stable` |

Multi-arch für `arm64` und `amd64` — der Pi ist der Normalfall, nicht die
Ausnahme. Die Compose-Datei referenziert

```yaml
image: ghcr.io/lucienkerl/loxmatter:${LOXMATTER_IMAGE_TAG:-stable}
```

und **der Updater schreibt beim Wechsel diesen einen Wert in `.env` um.**
Das ist zugleich der Rückfallmechanismus aus Abschnitt 8: der vorherige
Wert ist eine Zeile, die man zurückschreibt. `.env` ist nicht versioniert,
ein `git checkout` fasst sie also nicht an — genau deshalb steht die
Version dort und nicht in der Compose-Datei.

Dieselbe CI baut ein zweites, winziges Image
`ghcr.io/lucienkerl/loxmatter-updater` (Alpine, Docker-CLI,
Compose-Plugin, git, curl).

**Damit beginnt das Taggen.** Das ist die Verpflichtung dieses Entwurfs,
die nicht im Code steht: ab dem ersten Tag `v0.2.0` ist eine Version
etwas, worauf sich fremde Installationen verlassen. Dazu gehören
`CHANGELOG.md` und ein Abschnitt in `docs/DEVELOPMENT.md`, der festhält,
was ein Release ausmacht — sonst verwittert die Kette genau an der Stelle,
an der sie von Disziplin statt von Code getragen wird.

## 6. Der Beiwagen

Neuer Dienst `loxmatter-updater` in `deploy/testhost/docker-compose.yml`:

- Image per Digest gepinnt.
- Eingehängt: der Docker-Socket, der Repo-Checkout, `loxmatter-store:/data`
  — dasselbe Volume, in dem die Brücke ihre Datenbank hält.
- **Keine `ports:`, kein `network_mode: host`.** Er hängt im
  Compose-Standardnetz: nach draußen erreichbar (für `git fetch`), aus dem
  LAN nicht. Das unterscheidet ihn von allen drei bestehenden Diensten und
  ist der Grund, warum er den Socket haben darf.
- `extra_hosts: ["host.docker.internal:host-gateway"]`, damit er `/health`
  auf dem Host erreicht.
- Arbeitsweise: Schleife mit `sleep 2`.

Warum ein dauerhafter Dienst und nicht ein bei Bedarf gestarteter Helfer:
irgendjemand muss den Auftrag bemerken, und der Einzige, der einen
Container starten könnte, ist der, der den Socket hat. Die Alternative
wäre gewesen, den Socket in die Brücke zu hängen — also ausgerechnet in
den Dienst, der mit `network_mode: host` im ganzen LAN erreichbar ist. Die
Begründungen in der Compose-Datei rund um die Fabric-Sicherung
argumentieren ausführlich gegen diese Bauart; dieser Entwurf folgt ihnen.

## 7. Verständigung über Dateien

Kein Netzwerk zwischen Brücke und Beiwagen. Drei Dateien unter
`/data/update/`:

| Datei | Wer schreibt | Inhalt |
|---|---|---|
| `request.json` | Brücke | `{id, channel, target, requested_at}`, atomar über temp + rename |
| `state.json` | Updater | `{id, phase, steps[], from, to, error, rolled_back, healthy, updater_seen_at}` |
| `log.txt` | Updater | Rohausgabe, gedeckelt |

Der Updater verarbeitet eine `id` genau einmal. `updater_seen_at` schreibt
er bei jedem Schleifendurchlauf — daran erkennt die Brücke, ob überhaupt
einer da ist (Abschnitt 11).

Dass der Zustand in einer Datei im Volume liegt und nicht im Speicher der
Brücke, ist die tragende Entscheidung dieses Entwurfs. Nur deshalb kann
die Oberfläche nach dem Neustart weiterlesen, statt in einen
Verbindungsfehler zu laufen.

## 8. Der Ablauf im Updater

Vorher notiert er in `state.json`: den Wert von `LOXMATTER_IMAGE_TAG`, den
git-Ref, den Pfad der frischen Sicherung, das laufende `user_version`.

1. **`backup`** — Signaldatenbank nach `/data/backups/store-<stamp>.tgz`.
   Bewusst im selben Volume: das Volume überlebt den Image-Tausch, und der
   Fall, gegen den gesichert wird, ist eine misslungene Migration, kein
   Volume-Verlust. Die Oberfläche bietet die Sicherung zum Herunterladen
   an, damit sie das Gerät verlassen kann.
2. **`pull`** — `git fetch --tags`, auf das Ziel auschecken (die
   Compose-Datei muss zur Version passen), dann
   `docker compose pull loxmatter`.
3. **`recreate`** — `docker compose up -d --no-deps loxmatter`. `--no-deps`
   wie heute: matter-server und otbr bleiben unangetastet, und **der
   Updater rekreiert sich nicht selbst**, was ihn mitten im eigenen
   Auftrag beenden würde.
4. **`health`** — bis zu **120 s** auf `/health` warten. Kein neuer Wert,
   sondern der aus `scripts/update.sh`, samt der dort dokumentierten
   Begründung vom 8. September: 20 s waren zu knapp, ein Lauf kippte
   darüber, und das Skript meldete einen Dienst als krank, der zehn
   Sekunden später tadellos lief. Ein zu kurzes Zeitfenster ist hier die
   teurere Sorte Fehlalarm.
5. **`done`** oder **`rollback`**.

**Vorabprüfung vor Schritt 1:** Der Updater liest
`LOXMATTER_SCHEMA_VERSION` des Zielimages per `docker inspect`. Steigt die
Zahl, steht das im Bestätigungsdialog: *„Hebt das Datenbankschema von 7
auf 8."* Bleibt sie gleich, steht dort nichts — der Normalfall soll nicht
nach Gefahr aussehen.

### Der Rückfall

Auslöser: `/health` antwortet binnen 120 s nicht.

`LOXMATTER_IMAGE_TAG` in `.env` zurückschreiben, den alten git-Ref
auschecken, `up -d --no-deps loxmatter`, erneut bis zu 120 s warten.
**Genau einmal.**

**Die Datenbank bleibt unangetastet.** Das ist die inhaltliche
Entscheidung dieses Abschnitts, und sie steht auf einem geprüften Befund,
nicht auf einer Annahme. `_migrate` in `model/store.py` beginnt mit

```python
version = int(db.execute("PRAGMA user_version").fetchone()[0])
if version >= _SCHEMA_VERSION:
    return
```

Eine **alte** Binärdatei auf einer **neueren** Datenbank verweigert also
nicht den Dienst — sie kehrt sofort zurück und startet normal. Und weil
alle bisherigen Migrationen `ALTER TABLE ADD COLUMN` sind, was SQLite nur
nullable oder mit Vorgabewert erlaubt, schreibt die alte Version weiterhin
gültige Zeilen. Der Image-Rückfall allein genügt damit, um das Haus wieder
ans Laufen zu bringen.

Die Sicherung zurückzuspielen wäre der destruktivere Schritt: er verwirft
alles seit dem Sicherungszeitpunkt. Das tut man nicht selbsttätig um zwei
Uhr nachts, wenn niemand hinsieht. Sie steht stattdessen in der Oberfläche
als eigener, ausdrücklich zu bestätigender Knopf bereit, mit klarer Ansage,
was dabei verloren geht.

Der Restfall gehört benannt statt versteckt: eine Migration, die
Bestandsdaten **umschreibt** statt nur Spalten anzuhängen — wie
`_migrate_to_v3` es mit der Exportierbarkeit getan hat — hinterlässt nach
dem Rückfall umgeschriebene Werte unter alter Logik. Genau dafür ist der
Knopf da, und der Hinweis darauf erscheint nur, wenn das Schema
tatsächlich gestiegen ist.

**Wird auch der Rückfall nicht gesund**, hört der Updater auf. Kein
zweiter Versuch, kein Flattern: `failed`, `rolled_back: true`,
`healthy: false`. Dann ist wahrscheinlich auch die Oberfläche nicht
erreichbar — deshalb schreibt er zusätzlich
`/data/update/LETZTER-FEHLSCHLAG.txt` im Klartext: Stand, letzte
Logzeilen, Pfad der Sicherung, die drei Befehle, die von Hand
weiterhelfen. Wer dann doch per SSH nachsieht, findet eine Antwort statt
eines Rätsels.

**Zuletzt, nach `done`:** der Updater prüft, ob sein eigener gepinnter
Digest veraltet ist, und stößt gegebenenfalls abgekoppelt seinen eigenen
Austausch an. Nach dem Schreiben von `done`, nie vorher — sonst beendet er
sich mitten im Schreiben des Zustands, den die Oberfläche gerade liest.

## 9. Die Oberfläche

**Eine Karte „Version & Updates" ganz oben im System-Tab**, vor den
Prüfungen. Kein eigener Reiter, kein Banner über den anderen Tabs. Die
Version steht dort auch dann, wenn nichts ansteht.

Verworfen wurden: ein fünfter Reiter „Updates" (die Navigation zahlte
dauerhaft für etwas, das man zehnmal im Jahr braucht) und ein globales
Banner (aufdringlich für einen Zustand, der Tage bestehen darf).

Vier Zustände, alle in derselben Karte:

1. **Bestätigung** — was passiert, was unberührt bleibt, gegebenenfalls
   der Schemasprung, und die ehrliche Ansage: *rund eine Minute ohne
   Brücke, in der der Miniserver keine Werte erhält.* Danach sendet die
   Brücke ohnehin alles erneut.
2. **Läuft** — vier benannte Schritte (gesichert, geladen, neu gestartet,
   Gesundheit) statt eines wandernden Balkens, dazu der Schwanz des
   Protokolls.
3. **Brücke weg** — dieselbe Karte, weitergespeist aus `state.json`, mit
   laufender Sekundenzahl gegen die 120 s. **Kein Fehlerbanner.** Das
   bestehende Verbindungsbanner (`index.html`, `!socketConnected &&
   socketEverConnected`) bekommt für die Dauer einen anderen Text, statt
   nach Störung auszusehen.
4. **Ergebnis** — Erfolg mit neuer Version und Dauer, oder Fehlschlag mit
   der Feststellung, dass die alte Version bereits wieder läuft, den
   letzten Logzeilen und den beiden Knöpfen (ganzes Protokoll, Sicherung
   herunterladen).

**Während des Updates bleibt alles bedienbar.** Nichts wird gesperrt, kein
Modal legt sich über die Oberfläche. Wer währenddessen ein Gerät einlernen
will, läuft in einen Fehler — den erklärt aber das
Verbindungsbanner aus Zustand 3. Eine Minute Bevormundung wäre der höhere
Preis.

**Kanäle** — eine Einstellung, in der SQLite-Datenbank wie alles andere,
Standard `stable`:

- **Stabil** vergleicht mit dem letzten GitHub-Release; Anzeige „Version
  0.3.0 verfügbar" plus Release-Text als Änderungsnotizen.
- **Entwicklung** vergleicht den laufenden SHA mit `main`; Anzeige „14
  Commits hinterher" plus die Betreffzeilen dazwischen. Beim Umschalten
  eine klare Warnung, dass hier ungetestete Zwischenstände landen.

**Die Prüfung** fragt die GitHub-API, einmal täglich und beim Öffnen des
System-Tabs, Ergebnis zwischengespeichert. Abschaltbar — es ist ein
Verbindungsaufbau nach außen, und wer eine Brücke im Haus betreibt, darf
das verbieten dürfen. Ohne Internet ein ruhiger Hinweis („zuletzt geprüft
am …"), kein roter Zustand. Ein Gerät ohne Internetzugang ist hier kein
Fehler.

## 10. Zugriff und Sicherheitsgrenze

`GET /api/update/status`, `GET /api/update/check` und
`POST /api/update/apply` liegen hinter dem bestehenden `api_guard` —
Sitzung oder Token, wie alles unter `/api`.

**Keine erneute Passwortabfrage.** Dieselbe Sitzung lädt heute schon die
Fabric-Sicherung herunter, also die unersetzlichen Anmeldedaten des
gesamten Matter-Netzes. Ein Update auf eine veröffentlichte Version ist
demgegenüber der kleinere Preis; eine zweite Abfrage würde Sicherheit
behaupten, die woanders längst nicht besteht.

**Die eigentliche Grenze liegt im Updater, nicht im Login.** Drei Regeln,
alle im Beiwagen durchgesetzt:

1. `channel` ist ein Enum. `target` muss `^v?\d+\.\d+\.\d+$` oder
   `^[0-9a-f]{7,40}$` erfüllen. Alles andere: Auftrag abgelehnt, nichts
   ausgeführt.
2. Der **Image-Name wird nicht aus dem Auftrag genommen**, sondern im
   Skript fest zusammengesetzt: `ghcr.io/lucienkerl/loxmatter:` plus das
   geprüfte Ziel. Der git-Ref wird gegen die von `origin` geholten Refs
   verifiziert, und `origin` zeigt auf das Projekt-Repository.
3. **Nur vorwärts.** Im Kanal *Stabil* darf ein Auftrag keine Version
   unterhalb der laufenden nennen (Vergleich nach semantischer Version).
   Im Kanal *Entwicklung* gibt es keine Ordnung über SHAs, deshalb dort
   die entsprechende Prüfung: das Ziel muss ein **Nachfahre** des
   laufenden Commits sein, belegt über `git merge-base --is-ancestor
   <laufend> <ziel>`. Rückwärts geht ausschließlich der Updater selbst,
   aus seiner eigenen Buchführung — **ohne jede Ausnahme über diese
   Route.**

Der Wechsel von *Entwicklung* zurück auf *Stabil* stuft deshalb **nicht**
herab. Er stellt nur um, worauf verglichen wird; die Oberfläche sagt dann
klar: *„Sie laufen auf einem Entwicklungsstand, der neuer ist als 0.3.0.
Das nächste stabile Update kommt mit 0.4.0."* Wer wirklich zurück will,
geht über die Konsole. Eine Ausnahme in Regel 3 wäre der Punkt gewesen, an
dem die Aussage darüber — „höchstens eine neuere veröffentlichte Version"
— nicht mehr gilt, und sie wäre für einen seltenen Komfortfall gefallen.

Zusammen: selbst wer die Brücke vollständig übernimmt, kann darüber
höchstens **eine veröffentlichte, neuere loxmatter-Version installieren**.
Kein fremdes Image, kein beliebiger Befehl.

**Was der Docker-Socket trotzdem bedeutet**, gehört unverkürzt ins README
und in den Compose-Kommentar: dieser Container ist root-gleichwertig auf
dem Host. Abgesichert wird das durch Enge, nicht durch Rechte — keine
Ports, kein Host-Netz, Digest gepinnt, ein einziges festes
Arbeitsprogramm.

**Der Beiwagen ist entfernbar.** Wer ihn aus der Compose-Datei streicht,
verliert nur den Knopf.

## 11. Wenn kein Beiwagen da ist

Bestandsinstallationen und alle, die ihn entfernt haben: die Brücke
erkennt das an einem veralteten `updater_seen_at`, blendet den Knopf aus
und nennt stattdessen den Konsolenweg. Kein toter Knopf, keine Meldung,
die nach Defekt klingt.

**Der erste Sprung braucht einmal die Konsole.** Der Beiwagen und der
Wechsel von `build:` auf `image:` müssen einmal über den alten Weg
ankommen — `git pull && ./scripts/update.sh`. Ab dann trägt es sich
selbst. Das gehört in die Release-Notiz der ersten Version, die es
mitbringt, sonst sucht jemand vergeblich einen Knopf.

## 12. Texte

Die WebUI ist seit i18n-Phase B übersetzt. Jeder neue nutzersichtbare Text
geht über `i18n.t()` mit `en`- und `de`-Paar in
`src/loxmatter/i18n/strings.yaml`, nicht als fest verdrahtetes Deutsch.
Betroffen sind die Karte, die vier Zustände, der Bestätigungsdialog, die
Kanalumschaltung und die Fehlermeldungen der drei Routen.

## 13. Prüfen

**Der Updater ist ein Shell-Skript, und dafür gibt es hier ein erprobtes
Verfahren.** `tests/test_install_script.py` lässt `install.sh` gegen einen
versiegelten PATH aus gefälschten Binärdateien laufen und prüft, *welche
Befehle es wählt*, nicht was sie bewirken. Derselbe Aufbau, gefälschte
`docker`, `git`, `curl`:

- Der glückliche Pfad ruft `compose pull` **vor** `up -d`, und `up -d`
  immer mit `--no-deps`.
- Bleibt `/health` stumm, folgt die Rückfallsequenz — und genau einmal.
- `target: "v1.0.0; rm -rf /"` wird abgelehnt, **ohne dass ein einziger
  docker-Aufruf im Protokoll steht.** Das ist der Test, der die Aussage
  aus Abschnitt 10 zu mehr macht als einer Behauptung.
- Ein Ziel unterhalb der laufenden Version wird abgelehnt.
- Nach `done` folgt der Selbstaustausch, nach `failed` nicht.

**Python:** atomares Schreiben von `request.json` (eine halb geschriebene
Datei darf der Updater nie sehen), Statuslesen ohne vorhandene Datei,
Erkennung des fehlenden Beiwagens über veraltetes `updater_seen_at`,
Ablehnung eines zweiten Auftrags, solange einer läuft.

**Compose:** `tests/test_compose_profiles.py` bekommt Nachbarn — der neue
Dienst hat keine `ports:` und kein `network_mode: host`.

**Oberfläche:** ein Browsertest belegt nur, dass Dateien ausgeliefert
werden. Die vier Zustände werden gegen erfundene `state.json`-Inhalte in
einem Wegwerf-Harness gefahren, in dem die Alpine-Bindungen tatsächlich
laufen. Dazu ein neues `update.png` aus `scripts/capture_screenshots.py`
fürs README, das bisher über das Aktualisieren kein Wort verliert.

Für jeden dieser Tests gilt: **einmal probeweise den Fehler herstellen,
den er fangen soll.** Ein Test, der eine Struktur nur benennt statt sie zu
prüfen, ist in diesem Projekt schon vorgekommen.

## 14. Was nicht dazugehört

- **Kein selbsttätiges Einspielen.** Eine Brücke, die sich nachts
  unbeaufsichtigt ersetzt, ist in einem Haus keine Bequemlichkeit, sondern
  ein Risiko. Melden ja, klicken nein.
- **Kein Update von matter-server und otbr** — nur angezeigt (Abschnitt 3).
- **Keine freie Versionsliste, kein gezieltes Herabstufen.** Ein
  Schema-Downgrade über mehrere Stufen ist nicht geprüft und muss es für
  diesen Entwurf auch nicht sein.
- **Keine Update-Historie in der Oberfläche.** `log.txt` und die
  Sicherungen reichen; eine gepflegte Chronik wäre Datenhaltung für einen
  Zweck, den noch niemand hat.

## 15. Reihenfolge der Umsetzung

Der Entwurf ist als ein Vorhaben geschrieben, zerfällt aber in zwei
Stufen, und die Reihenfolge ist nicht beliebig: **die zweite kann ohne die
erste nichts tun.**

**Stufe 1 — Identität und Auslieferung.** Build-Argumente und `ENV` im
Image, `GET /api/version`, die Version in der Oberfläche, CI baut
multi-arch nach GHCR, Compose wechselt auf `image:` mit `build:` hinter
einem Profil, `scripts/update.sh` auf `pull` umgestellt, `CHANGELOG.md`,
und schließlich der erste echte Tag `v0.2.0`. Danach zeigt die Oberfläche
an, was läuft, und der Konsolenweg ist schon deutlich schneller — für sich
genommen bereits ein Gewinn, auch wenn Stufe 2 nie käme.

**Stufe 2 — der Knopf.** Beiwagen-Image, der neue Compose-Dienst, die
Auftragsdateien, `/api/update/*`, die Karte mit ihren vier Zuständen, der
Rückfall.

Es gibt nichts zu aktualisieren, solange es keine veröffentlichte Version
gibt, auf die man aktualisieren könnte. Wer mit Stufe 2 beginnt, baut
einen Knopf, den er nicht auslösen kann, und testet den Rückfall gegen ein
Image, das es nicht gibt.
