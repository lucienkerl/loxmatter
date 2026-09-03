# Login statt Token-Box: passwortgeschützter Zugang zur Oberfläche

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), insbesondere
dessen Abschnitte 8 (WebUI) und 9 (Absicherung), und löst die in Phase 5,
Task 8 eingeführte Token-Eingabe ab.

## 1. Das Problem

Die Oberfläche verlangt beim ersten Aufruf ein API-Token, das der Betreiber
zuvor selbst erzeugt (`openssl rand -hex 32`), in `.env` einträgt und dann
im Browser noch einmal von Hand einträgt. Der Grund dafür ist strukturell,
nicht kosmetisch: die WebUI ist eine statische Seite ohne eigene Anmeldung.
`/` und `/static/*` hängen in `loxone/server.py` bewusst ohne
`dependencies=api_guard`; geschützt ist ausschließlich `/api/*`. Es gibt
keinen Login, keine Sitzung, kein Konto — **das Token ist der einzige
Ausweis, den dieser Dienst kennt**.

Daraus folgt, dass der Server das Token nicht "einfach automatisch
übergeben" kann. Läge es in der ausgelieferten `index.html` oder hinter
einem Bootstrap-Endpunkt, bekäme es jeder, der `http://<Host>:8080/`
öffnet — und damit auch `GET /api/diagnostics/fabric-backup`, also die
unersetzlichen Zugangsdaten der Matter-Fabric (Hauptdokument 4.1). Das
Token wäre dann keines mehr.

Der Ausweg ist nicht, das Token bequemer zu verteilen, sondern ihm für den
Browser einen echten Ausweis zur Seite zu stellen: eine Anmeldung mit
Passwort, wie man sie von jedem anderen selbst gehosteten Dienst kennt.

## 2. Was dieser Entwurf nicht antastet

- **`/cmd` und `/resync` bleiben ohne jede Absicherung erreichbar.** Der
  Miniserver ruft virtuelle Ausgänge ohne Header und ohne Cookie auf; jede
  Prüfung dort schaltet die Loxone-Integration ab. Unverändert gegenüber
  Phase 5, Task 8.
- **Das Bearer-Token bleibt.** Es ist künftig nicht mehr der Weg des
  Browsers, sondern der von Skripten und `curl`. `LOXMATTER_API_TOKEN`,
  `--api-token`, `normalize_api_token` und der WebSocket-Subprotokoll-Weg
  `bearer, <Token>` bleiben serverseitig vollständig erhalten.
- **Kein TLS.** Der Dienst spricht weiterhin HTTP auf Port 8080. Siehe 14.1.
- **Kein Benutzername.** Ein Dienst, ein Betreiber, ein Passwort. Ein
  Namensfeld wäre eine Eingabe ohne Entscheidung dahinter.

## 3. Verworfene Alternativen

**Token in die Seite einbetten oder über einen Bootstrap-Endpunkt
ausliefern — verworfen.** Siehe 1: `/` ist unauthentifiziert, also wäre das
Token es auch.

**Einmal-Link `http://host:8080/?token=<Token>`, den die Seite in den
`localStorage` übernimmt und aus der URL entfernt — verworfen.** Funktioniert
und kostet wenig Code, aber das Token steht dabei in der Browser-History und
möglicherweise im Zugriffslog; und es bleibt bei einem Geheimnis, das der
Betreiber selbst erzeugen und transportieren muss. Ein Login löst dasselbe
Problem, ohne dass jemals ein Geheimnis durch eine URL läuft.

**Vertrauenswürdige Netze (`--trusted-net 192.168.1.0/24`), aus denen
`/api` ohne Nachweis erreichbar ist — verworfen.** War zwischenzeitlich
abgestimmt und wurde zugunsten des Logins zurückgezogen. Der Grund gegen die
Netzausnahme: sie macht jedes Gerät im selben Netz zum Administrator,
einschließlich Fernseher, Saugroboter und Besuchsgeräten im WLAN. Ein Login
ist der stärkere Ausweis — und weil er stärker ist, darf er auch mehr
freigeben (siehe 11).

**Passwort aus einer Umgebungsvariable (`LOXMATTER_PASSWORD` oder
`LOXMATTER_PASSWORD_HASH`) — verworfen.** Ziel ist eine headless aufsetzbare
Installation, die vollständig über die Oberfläche konfiguriert wird. Ein
Passwort, das vor dem ersten Start in einer Datei stehen muss, ist das
Gegenteil davon.

## 4. Das Zugangsmodell

`build_api_guard` in `loxone/server.py` entscheidet künftig über zwei
Nachweise statt über einen. Reihenfolge je Anfrage:

1. **Gültiges Sitzungs-Cookie** → durch.
2. **Gültiges Bearer-Token** (Header oder WebSocket-Subprotokoll, beides wie
   bisher) → durch.
3. **Weder noch** → 401.

**Punkt 3 kennt keine Ausnahme mehr, und das ist die eigentliche Härtung
dieses Entwurfs.** Heute läuft ein Dienst ohne konfiguriertes Token mit
vollständig offenen `/api`-Routen und lediglich einer Warnung im Log — wer
die Warnung überliest, betreibt eine offene Brücke, ohne es zu merken.
Künftig gibt es diesen Zustand nicht: solange kein Passwort gesetzt ist,
antwortet **jede** `/api`-Route mit 401, und die Oberfläche kann nichts
anderes als den Einrichtungsbildschirm zeigen. Die Passwortvergabe ist
damit Voraussetzung des Betriebs und nicht mehr eine Empfehlung, die man
ignorieren kann.

Das gilt ausdrücklich auch für **bestehende Installationen nach dem
Update**: eine Brücke, die bisher ohne Token lief, liefert nach dem Update
keine Gerätedaten mehr aus, bis ein Passwort vergeben ist. Ein
konfiguriertes `LOXMATTER_API_TOKEN` kommt über Punkt 2 unverändert durch —
Skripte und Automatisierungen brechen durch das Update also nicht ab,
auch nicht in der Zeit vor der Passwortvergabe.

Der Wächter gilt unverändert für alle fünf `/api`-Router einschließlich der
WebSocket-Route `/api/live`. Die Peer-Adresse des Aufrufers spielt in keiner
dieser Entscheidungen eine Rolle.

`cli._warn_if_missing_api_token` warnt künftig, solange **kein Passwort
gesetzt** ist, und heißt entsprechend `_warn_if_no_password`. Ein
konfiguriertes Token bringt die Warnung nicht mehr zum Schweigen: es ist
der Weg für Skripte, kein Ersatz für die Ersteinrichtung.

## 5. Erststart: Trust on first use

Ist im Store kein Passwort hinterlegt, zeigt die Oberfläche einen
Einrichtungsbildschirm, auf dem das Passwort vergeben wird — ohne weiteren
Nachweis. Wer zuerst kommt, richtet ein.

**Das gilt für jede Installation ohne Passwort, auch für eine bestehende
nach dem Update.** Es gibt keinen Sonderfall für einen bereits
konfigurierten `LOXMATTER_API_TOKEN`: ein Bildschirm, ein Ablauf, dieselben
Regeln. Zwei Zustände, zwei Verhalten:

| Passwort | `POST /auth/setup` |
| --- | --- |
| nicht gesetzt | offen (Trust on first use) |
| gesetzt | 409, dauerhaft |

**Das ist eine bewusst getroffene Abwägung, kein Versehen.** Für die
Neuinstallation wurde sie am 3. September 2026 gegen drei Alternativen
entschieden: Einrichtungscode im Startlog, Zeitfenster von 15 Minuten nach
dem Start, und Erstpasswort per CLI. Ausschlaggebend war, dass die
Einrichtung ohne Blick in ein Log und ohne Shell auf dem Host möglich sein
soll. Für das Bestandssystem wurde am selben Tag gegen die Variante
entschieden, dort einmalig das vorhandene Token abzufragen — zugunsten
eines einzigen Ablaufs ohne Sonderfall in Code und Dokumentation.

Der Preis, der damit gekauft wird, ist in beiden Fällen derselbe und muss
klar benannt sein: **zwischen dem Start ohne Passwort und der
Passwortvergabe kann jeder, der den Dienst erreicht, ihn übernehmen.** Der
rechtmäßige Betreiber erfährt davon erst dadurch, dass sein eigenes
Passwort nicht angenommen wird. Wer die Brücke aufsetzt und erst Tage
später weiterkonfiguriert, lässt dieses Fenster tagelang offen.

**„Wer den Dienst erreicht" ist dabei weiter zu lesen als „wer im selben
Netz steht"** (Nachtrag vom 3. September 2026, aus dem Abschlussreview).
Eine fremde Webseite, deren Name nach kurzer TTL auf die LAN-Adresse der
Brücke umschwenkt — DNS-Rebinding —, ist für den Browser des Betreibers
derselbe Ursprung. Damit entfallen sowohl der CORS-Preflight, der einen
fremden `Content-Type: application/json` sonst blockiert, als auch die
Wirkung von `SameSite=Strict`: `POST /auth/setup` ist aus dem Internet
erreichbar, sobald der Betreiber irgendeine Seite öffnet, während seine
Brücke noch ohne Passwort läuft. Nach der Passwortvergabe bleibt die
Wirkung eines solchen Angriffs auf `/cmd` und `/resync` beschränkt, die
ohnehin bewusst offen sind.

Ein Test auf `Sec-Fetch-Site` (nur `same-origin` und `none` zulassen) auf
den beiden `/auth`-Routen würde genau diese Grenze herstellen und wäre
wenige Zeilen groß. Er wurde am 3. September 2026 **bewusst nicht**
umgesetzt: der Betreiber richtet die Brücke unmittelbar nach dem Ausrollen
ein, das Fenster ist damit Minuten lang, und eine weitere Prüfung auf dem
einzigen Weg hinein ist eine weitere Stelle, an der man sich aussperren
kann. Wer die Brücke länger unkonfiguriert stehen lässt, sollte das anders
entscheiden.

Beim Bestandssystem wiegt das schwerer als bei der Neuinstallation, und
auch das gehört hierher: die Anlage war bereits abgesichert, der Betreiber
hat keinen Anlass, nach einem Update mit einem Übernahmefenster zu rechnen,
und er bemerkt das Update unter Umständen erst Tage später. Wer diese
Version ausrollt, sollte sich unmittelbar danach anmelden. Das gehört
**in den Release-Hinweis und in die README**, nicht nur in diese Spec.

Was das Fenster begrenzt, und was innerhalb dieser Entscheidung liegt:

- Solange kein Passwort gesetzt ist, sind alle `/api`-Routen gesperrt
  (Abschnitt 4). Wer den Dienst in diesem Zustand erreicht, aber die
  Einrichtung *nicht* abschließt, sieht keine Gerätedaten, keine
  Diagnose und keine Fabric-Sicherung. Angreifbar ist allein die
  Übernahme selbst, nicht der Bestand.
- Solange kein Passwort gesetzt ist, schreibt der Dienst bei **jedem** Start
  eine deutliche Warnzeile ins Log — nicht nur beim ersten.
- Der Einrichtungsbildschirm benennt den Zustand offen: diese Brücke ist
  gerade für jeden im Netz übernehmbar, die Einrichtung sollte jetzt
  abgeschlossen werden.
- Sobald ein Passwort gesetzt ist, ist der Einrichtungsweg **dauerhaft**
  geschlossen (`POST /auth/setup` antwortet ab dann mit 409, siehe 8).
- Ein bereits konfiguriertes Token bleibt nach der Einrichtung gültig; die
  Passwortvergabe entwertet es nicht.

## 6. Speicherung

Der Store (`model/store.py`) hat bereits ein versioniertes Schema mit
Migrationen (`_SCHEMA_VERSION`, `_migrate`) und liegt im persistenten Volume
unter `LOXMATTER_STORE`. `_SCHEMA_VERSION` steht nach Phase 6 auf 3; neu
kommt `_migrate_to_v4` hinzu, `_SCHEMA_VERSION` geht auf 4:

```sql
CREATE TABLE IF NOT EXISTS setting (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
  id         TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
```

`setting` trägt zunächst genau einen Schlüssel, `password_hash`. Die Tabelle
ist trotzdem generisch angelegt, weil die restliche Konfiguration später
denselben Weg gehen soll (14.2).

**Passwort-Hash: `hashlib.scrypt` aus der Standardbibliothek**, abgelegt als
`scrypt$<n>$<r>$<p>$<salt-hex>$<hash-hex>` mit n = 2^14, r = 8, p = 1,
16 Byte Salt, 32 Byte Ausgabe. Der Speicherbedarf von 128·n·r = 16 MiB liegt
unter der Vorgabe von `hashlib.scrypt`, `maxmem` muss also nicht angefasst
werden. Gewählt gegenüber Argon2 (`argon2-cffi`) und bcrypt (`passlib`),
weil beide eine neue Laufzeitabhängigkeit für genau einen Hash bedeuteten;
scrypt ist für diesen Zweck ausreichend und schon da. Das Format trägt seine
Parameter selbst, damit ein späterer Wechsel der Kostenfaktoren alte Hashes
weiter prüfen kann.

Sitzungen liegen in der Datenbank und nicht im Speicher, weil der Dienst mit
`restart: unless-stopped` läuft: ein Neustart darf nicht jedes Mal
ausloggen. Abgelaufene Zeilen werden bei jedem Zugriff auf die Tabelle
mitgelöscht — kein Hintergrundjob für eine Tabelle mit einer Handvoll
Zeilen.

## 7. Die Sitzung

Cookie `loxmatter_session`, Wert aus `secrets.token_hex(32)` — 32 Byte
Zufall, hexadezimal.
Attribute:

- **`HttpOnly`** — kein Skript im Ursprung kommt an den Wert. Damit entfällt
  die XSS-Abwägung, die der `localStorage`-Kommentar in `web/app.js` heute
  führen muss.
- **`SameSite=Strict`** — zugleich der CSRF-Schutz: eine fremde Seite kann
  keine zustandsändernde Anfrage in einer angemeldeten Sitzung auslösen, weil
  der Browser das Cookie bei fremdem Ursprung gar nicht erst mitschickt. Ein
  eigenes CSRF-Token wird deshalb nicht eingeführt. `Strict` statt `Lax`, weil
  es keinen Anwendungsfall gibt, in dem man aus einer fremden Seite heraus in
  diese Oberfläche verlinkt.
- **`Path=/`**, damit auch der WebSocket-Handshake auf `/api/live` das Cookie
  trägt.
- **Ausdrücklich ohne `Secure`.** Der Dienst spricht HTTP; mit `Secure` würde
  der Browser das Cookie verwerfen und niemand käme hinein. Diese Zeile ist
  bewusst gesetzt und darf nicht "der Sicherheit halber" nachgezogen werden,
  solange 14.1 offen ist.

Laufzeit 30 Tage, bei jedem erfolgreichen Zugriff gleitend verlängert.

## 8. Neue Routen

Alle vier hängen **außerhalb** des Wächters, neben `/health` — sie müssen
unangemeldet erreichbar sein.

| Route | Verhalten |
| --- | --- |
| `GET /auth-info` | `{"password_set": bool, "authenticated": bool}`. Sagt der Oberfläche, ob sie Einrichtung, Login oder die App zeigt. Kein Geheimnis: `password_set` ist auch daran ablesbar, wie `POST /auth/login` antwortet (409 vor der Ersteinrichtung, 401 danach), `authenticated` daran, ob `/api/devices` Daten liefert. **Nicht** mehr an `/api/devices` allein — seit Abschnitt 4 antwortet die Route in beiden Zuständen mit 401 (korrigiert am 3. September 2026). Praktische Folge: ein Netzscan findet über `GET /auth-info` nicht eingerichtete Brücken, ohne Spur in einem Fehlerzähler zu hinterlassen. |
| `POST /auth/setup` | Nimmt das neue Passwort entgegen, **solange keines gesetzt ist** — ohne weiteren Nachweis, auch wenn ein Token konfiguriert ist (5). Ist bereits eines gesetzt: 409, ohne Ausnahme. Legt bei Erfolg sofort eine Sitzung an, damit der Betreiber nicht direkt danach noch einmal tippt. |
| `POST /auth/login` | Prüft das Passwort, legt bei Erfolg eine Sitzung an. |
| `POST /auth/logout` | Löscht die Sitzungszeile **serverseitig** und räumt das Cookie ab. Ein Logout, der nur das Cookie löscht, lässt eine gestohlene Kennung weiterleben. |

**Sperre gegen Durchprobieren.** Ein Passwort ist ratbar, ein 256-Bit-Token
nicht — ohne Bremse wäre der neue Weg schwächer als der, den er ablöst. Nach
fünf Fehlversuchen aus derselben Peer-Adresse wird `POST /auth/login` für
diese Adresse auf einen Versuch je 30 Sekunden gedrosselt; ein erfolgreicher
Login setzt den Zähler zurück. Zähler im Speicher, nicht in der Datenbank: es ist
flüchtiger Zustand, der keinen Schreibzugriff je Fehlversuch rechtfertigt,
und ein Neustart löscht ihn zwar — nur kann ein Angreifer keinen auslösen. Der Passwortvergleich läuft konstantzeitig
(`secrets.compare_digest` über die Hashes).

Alle drei Antwortkörper der `POST`-Routen enthalten **niemals** das
Passwort, den Hash oder Teile davon, und die Routen loggen den Klartext
unter keinen Umständen.

## 9. Notausgang

Neuer CLI-Befehl `loxmatter set-password`, der das Passwort verdeckt abfragt
und den Hash direkt in den Store schreibt — mit `--store-path` wie die
übrigen Befehle. Ohne ihn wäre eine headless aufgesetzte Installation mit
vergessenem Passwort endgültig verloren; der Betreiber hat auf dem Host
ohnehin Zugriff auf die Datenbankdatei, der Befehl macht daraus nur einen
benutzbaren Weg. Er löscht dabei alle bestehenden Sitzungen: wer das
Passwort zurücksetzt, will nicht, dass eine alte Sitzung weiterläuft.

## 10. Oberfläche

`web/index.html` bekommt zwei Bildschirme vor die eigentliche App, gesteuert
durch `GET /auth-info` beim Start:

- **Einrichtung** — Passwort zweimal eingeben, mit dem Hinweis aus 5. Kein
  weiteres Feld, unabhängig davon, ob ein Token konfiguriert ist.
- **Login** — ein Feld, ein Knopf, verständliche Fehlermeldung bei falschem
  Passwort und bei aktiver Sperre ("zu viele Versuche, in X Sekunden wieder
  möglich").

**Die Token-Box entfällt ersatzlos.** Sie war der Anlass dieses Entwurfs.
Damit fällt in `web/app.js` weg: `TOKEN_STORAGE_KEY`, `readStoredToken`,
`authHeaders`, das gesamte `localStorage`-Verhalten samt seiner
XSS-Abwägung, `saveToken`/`clearToken`/`startTokenEdit`/`cancelTokenEdit`
und der Subprotokoll-Aufbau `new WebSocket(url, ["bearer", token])`. `fetch`
läuft künftig mit `credentials: "same-origin"`, der WebSocket bekommt das
Cookie beim Handshake von selbst. Serverseitig bleibt der
Subprotokoll-Weg für Skripte erhalten (siehe 2).

`UnauthorizedError` bleibt als eigene Fehlerklasse, ändert aber die Wirkung:
ein 401 aus einer laufenden Sitzung (abgelaufen, oder anderswo abgemeldet)
wirft die Oberfläche zurück auf den Login-Bildschirm, statt eine
Fehlermeldung anzuzeigen, die auf ein Feld verweist, das es nicht mehr gibt.

## 11. Auswirkung auf die Fabric-Sicherung

`GET /api/diagnostics/fabric-backup` verweigert heute die Auslieferung,
solange kein Token konfiguriert ist (403, Review-Fix Fix 3 vom
2026-09-03) — die Route soll nicht ungeschützt im LAN stehen, weil hinter
ihr die Übernahme der Fabric hängt.

**Der Zustand, gegen den diese Sperre gerichtet war, kann nicht mehr
eintreten.** Sie verteidigte den Fall „Dienst läuft ohne jedes Zugangsmittel,
also sind alle `/api`-Routen offen" — genau diesen Fall schafft Abschnitt 4
ab: ohne Passwort ist jede `/api`-Route gesperrt, und wer durchkommt, hat
ein Cookie oder ein Token vorgezeigt. Der Parameter `api_token_configured`
und der 403-Zweig entfallen deshalb ersatzlos.

Das ist bewusst eine Entfernung und kein Übersehen. Ein unerreichbarer
Zweig, dessen ausführlicher Docstring eine Lage beschreibt, die es nicht
mehr gibt, führt den nächsten Leser in die Irre — er liest dort eine
Bedingung, auf die er sich verlässt, und die nichts mehr prüft. Was den
Schutz künftig trägt, ist der Wächter selbst, und dass er auf **jedem** der
fünf Router hängt, prüft `tests/api/test_security.py` bereits Router für
Router einzeln statt über den gemeinsamen Präfix.

Nach dem Login ist der Download damit frei — ohne Token, ohne Zusatzschritt.
Eine Ausnahme, die den Betreiber nach erfolgreicher Anmeldung noch einmal
nach einem zweiten Geheimnis fragt, schützt nichts, das nicht schon
geschützt wäre.

**Der Ausweis vor dieser Route ist dabei schwächer geworden, und das gehört
hierher** (Nachtrag vom 3. September 2026, aus dem Abschlussreview). Ein
früherer Absatz nannte den Login den „stärkeren Ausweis" — das stimmt für
seine *Verfügbarkeit* (es gibt jetzt immer einen), nicht für seine *Stärke*.
Vorher stand vor `GET /api/diagnostics/fabric-backup` ein Geheimnis mit 256
Bit Entropie, das nicht zu raten war. Jetzt steht dort ein Passwort von
mindestens acht Zeichen, im Klartext über HTTP übertragen (14.1), gebremst
durch eine Drosselung von rund zwei Versuchen je Minute — also etwa 2.900
Versuche am Tag gegen den einzigen unwiderruflichen Zustand dieser
Installation (Hauptdokument 4.1). Ein Wörterbuchpasswort fällt darunter in
Tagen.

Die Entfernung des 403-Zweigs bleibt trotzdem richtig: der Zustand, gegen
den er stand, existiert nicht mehr. Und die Mindestlänge bleibt am
3. September 2026 bewusst bei acht Zeichen — die Alternative (zwölf) wurde
geprüft und verworfen. Was daraus folgt, gehört in die Dokumentation und
nicht in eine Konstante: **das Passwort dieser Brücke sollte zufällig sein,
nicht merkbar.** Acht zufällige Zeichen tragen die obige Rechnung, acht
gewählte nicht.

Unverändert bleibt, dass `build_diagnostics_router` **weder Passwort noch
Hash noch Token** zu sehen bekommt: was er nicht kennt, kann er nicht
versehentlich in eine Antwort oder ins Log schreiben.

## 12. Prüfung

Neue Datei `tests/api/test_auth.py`:

- Hash und Prüfung: richtiges Passwort passt, falsches nicht, zwei gleiche
  Passwörter ergeben durch das Salt verschiedene Hashes, ein Hash mit
  fremden Parametern im Präfix wird weiterhin korrekt geprüft.
- Sitzungen: anlegen, prüfen, gleitend verlängern, Ablauf, Löschen beim
  Logout, Aufräumen abgelaufener Zeilen.
- Sperre: fünf Fehlversuche, dann Drosselung; erfolgreicher Login setzt
  zurück; eine zweite Peer-Adresse ist von der Sperre der ersten nicht
  betroffen.
- Migration: eine Datenbank auf Schemaversion 3 wird auf 4 gehoben, ohne
  Bestandszeilen in `device`/`signal`/`command` anzutasten.

`tests/api/test_security.py` wächst um:

- Sitzungs-Cookie kommt durch jede der fünf `/api`-Router-Gruppen.
- Bearer-Token kommt unverändert weiter durch.
- Weder noch → 401, **ausnahmslos**. Insbesondere der Zustand „kein
  Passwort, kein Token": jede der fünf Router-Gruppen antwortet mit 401,
  nicht mit Daten. Das ist der Test, der die Verschärfung aus Abschnitt 4
  festhält — bisher war genau dieser Zustand offen.
- Kein Passwort, aber konfiguriertes Token: `/api` bleibt mit Token
  erreichbar. Das ist der Bestandsfall unmittelbar nach dem Update; er
  belegt, dass Skripte das Update überstehen.
- `POST /auth/setup` ohne gesetztes Passwort → Passwort gesetzt, auch bei
  konfiguriertem Token und ohne diesen mitzuschicken (5).
- `POST /auth/setup` nach gesetztem Passwort → 409, auch mit gültigem Token.
- Ein vor der Einrichtung konfiguriertes Token bleibt nach der
  Passwortvergabe gültig.
- Die umbenannte Startwarnung `cli._warn_if_no_password`: warnt ohne
  gesetztes Passwort — auch bei konfiguriertem Token —, schweigt mit.
- Logout macht das Cookie sofort wertlos (derselbe Wert danach → 401).
- `/api/live` verbindet mit Cookie und ohne Subprotokoll.
- `/cmd` und `/resync` bleiben in **jedem** dieser Zustände offen.
- `GET /api/diagnostics/fabric-backup` nach Login ohne Token → 200.

## 13. Dokumentation

Diese Änderung ist für bestehende Installationen ein Bruch — eine Brücke,
die bisher ohne Token lief, liefert nach dem Update nichts mehr aus, bis ein
Passwort vergeben ist (4). Das darf niemand erst am schweigenden Dienst
bemerken. Nachzuziehen sind:

- **Release-Hinweis:** was passiert, was zu tun ist (Oberfläche öffnen,
  Passwort vergeben), und der Hinweis aus 5, das unmittelbar nach dem
  Ausrollen zu tun und nicht auf später zu verschieben.
- **README:** Abschnitt zur Absicherung neu — Login statt Token-Eingabe,
  Passwortvergabe beim ersten Aufruf, `loxmatter set-password` als
  Notausgang, und der Rat aus 14.1 zu einem Passwort, das nirgends sonst
  benutzt wird.
- **`deploy/testhost/.env.example`:** `LOXMATTER_API_TOKEN` verliert seine
  Rolle als Zugang zur Oberfläche und behält nur die für Skripte. Der lange
  Kommentar, der heute zur Eingabe in der Oberfläche anleitet, wird falsch
  und muss ersetzt werden.
- **`deploy/testhost/docker-compose.yml`:** derselbe Kommentar an
  `LOXMATTER_API_TOKEN` und an der Volume-Zeile für `/matter-data` — dort
  steht heute, dass Einhängung und Token zusammengehören. Künftig trägt das
  Passwort diese Rolle.
- **Moduldocstrings** von `loxone/server.py` und `api/diagnostics.py`: beide
  beschreiben das Token als einzigen Ausweis. Beide erklären ausführlich
  Zustände, die es nach 4 und 11 nicht mehr gibt.

## 14. Offene Punkte

**14.1 Kein TLS.** Beim Login geht das Passwort im Klartext über das Netz.
Das ist keine Verschlechterung — der `Authorization`-Header tut das heute
schon —, aber ein Passwort wird von Menschen wiederverwendet, ein
dienstspezifisches Token nicht. Die Dokumentation muss deshalb ausdrücklich
zu einem Passwort raten, das nirgends sonst benutzt wird. TLS (Zertifikat,
Reverse-Proxy, `Secure`-Flag am Cookie) ist ein eigener Entwurf.

**14.2 Restliche Konfiguration in der Oberfläche.** Miniserver-Adresse,
matter-server-Adresse, Ports und Datenverzeichnis kommen weiterhin aus
`docker-compose.yml` und den CLI-Optionen. Sie in die Oberfläche zu holen —
mitsamt der Frage, welche Werte im laufenden Betrieb änderbar sind und
welche einen Neustart brauchen — ist der eigentliche Weg zur headless
aufgesetzten Installation und bekommt eine eigene Spec. Dieser Entwurf legt
mit `setting` und dem Einrichtungsbildschirm die Mechanik dafür an.

**14.3 Mehrere Betreiber.** Ein Passwort, kein Benutzername, keine Rollen.
Sollte das je gebraucht werden, trägt das Schema es (`setting` wird zu einer
`user`-Tabelle), aber es ist heute kein Ziel.
