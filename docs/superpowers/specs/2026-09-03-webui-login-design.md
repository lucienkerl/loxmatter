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
- **Kein TLS.** Der Dienst spricht weiterhin HTTP auf Port 8080. Siehe 13.1.
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
3. **Weder noch, und es ist mindestens ein Zugangsmittel eingerichtet**
   (Passwort gesetzt oder Token konfiguriert) → 401.
4. **Weder noch, und es ist gar nichts eingerichtet** → durch, mit
   derselben deutlichen Startwarnung wie heute. Das ist der Zustand vor der
   Ersteinrichtung; siehe 5.

Der Wächter gilt unverändert für alle fünf `/api`-Router einschließlich der
WebSocket-Route `/api/live`. Die Peer-Adresse des Aufrufers spielt in keiner
dieser Entscheidungen eine Rolle.

`cli._warn_if_missing_api_token` prüft künftig nicht mehr nur das Token,
sondern beide Zugangsmittel, und heißt entsprechend
`_warn_if_no_credentials`. Ein Dienst mit gesetztem Passwort und ohne Token
ist abgesichert und soll nicht länger warnen.

## 5. Erststart: Trust on first use

Ist im Store kein Passwort hinterlegt und auch kein Token konfiguriert,
zeigt die Oberfläche einen Einrichtungsbildschirm, auf dem das Passwort
vergeben wird — ohne weiteren Nachweis. Wer zuerst kommt, richtet ein.

**Trust on first use gilt nur für den wirklich ersten Start.** Läuft der
Dienst bereits mit konfiguriertem `LOXMATTER_API_TOKEN`, aber noch ohne
Passwort, verlangt die Einrichtung dieses Token. Sonst wäre der
Einrichtungsbildschirm ein Weg, eine bereits abgesicherte Installation zu
übernehmen: ein Unbeteiligter im Netz vergibt sich ein Passwort und hat
damit den Vollzugriff, den das Token gerade verhindern sollte. Drei
Zustände, drei Verhalten:

| Passwort | Token | `POST /auth/setup` |
| --- | --- | --- |
| nicht gesetzt | nicht konfiguriert | offen (Trust on first use) |
| nicht gesetzt | konfiguriert | verlangt `Authorization: Bearer <Token>` |
| gesetzt | beliebig | 409, dauerhaft |

**Das ist eine bewusst getroffene Abwägung, kein Versehen.** Sie wurde am
3. September 2026 gegen drei Alternativen entschieden: Einrichtungscode im
Startlog, Zeitfenster von 15 Minuten nach dem Start, und Erstpasswort per
CLI. Ausschlaggebend war, dass die Einrichtung ohne Blick in ein Log und
ohne Shell auf dem Host möglich sein soll.

Der Preis, der damit gekauft wird: zwischen dem ersten Start und der
Passwortvergabe kann jeder, der den Dienst im Netz erreicht, ihn
übernehmen — und der rechtmäßige Betreiber erfährt davon erst dadurch, dass
sein eigenes Passwort nicht angenommen wird. Wer die Brücke aufsetzt und
erst Tage später weiterkonfiguriert, lässt dieses Fenster tagelang offen.

Drei Milderungen, die innerhalb dieser Entscheidung liegen und deshalb
umgesetzt werden:

- Solange kein Passwort gesetzt ist, schreibt der Dienst bei **jedem** Start
  eine deutliche Warnzeile ins Log — nicht nur beim ersten.
- Der Einrichtungsbildschirm benennt den Zustand offen: diese Brücke ist
  gerade für jeden im Netz übernehmbar, die Einrichtung sollte jetzt
  abgeschlossen werden.
- Sobald ein Passwort gesetzt ist, ist der Einrichtungsweg **dauerhaft**
  geschlossen (`POST /auth/setup` antwortet ab dann mit 409, siehe 8).

## 6. Speicherung

Der Store (`model/store.py`) hat bereits ein versioniertes Schema mit
Migrationen (`_SCHEMA_VERSION`, `_migrate`) und liegt im persistenten Volume
unter `LOXMATTER_STORE`. Neue Migration `_migrate_to_v3`, `_SCHEMA_VERSION`
auf 3:

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
denselben Weg gehen soll (13.2).

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
  solange 13.1 offen ist.

Laufzeit 30 Tage, bei jedem erfolgreichen Zugriff gleitend verlängert.

## 8. Neue Routen

Alle vier hängen **außerhalb** des Wächters, neben `/health` — sie müssen
unangemeldet erreichbar sein.

| Route | Verhalten |
| --- | --- |
| `GET /auth-info` | `{"password_set": bool, "token_configured": bool, "authenticated": bool}`. Sagt der Oberfläche, ob sie Einrichtung, Login oder die App zeigt — und ob die Einrichtung zusätzlich das Token verlangt (5). Kein Geheimnis: alle drei Werte sind auch daran ablesbar, wie `/api/devices` antwortet. |
| `POST /auth/setup` | Nimmt das neue Passwort entgegen, **nur solange keines gesetzt ist**, und nur unter den Bedingungen der Tabelle in 5. Ist bereits eines gesetzt: 409, ohne Ausnahme. Legt bei Erfolg sofort eine Sitzung an, damit der Betreiber nicht direkt danach noch einmal tippt. |
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

- **Einrichtung** — Passwort zweimal eingeben, mit dem Hinweis aus 5. Ist
  laut `/auth-info` ein Token konfiguriert, kommt ein drittes Feld für das
  Token dazu (5), mit dem Hinweis, wo es herkommt (`LOXMATTER_API_TOKEN`).
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

Diese Sperre bleibt, ihre Bedingung wird nur ehrlicher benannt: der
Parameter `api_token_configured` von `build_diagnostics_router` heißt künftig
`credentials_configured` und ist wahr, wenn **ein Passwort gesetzt ODER ein
Token konfiguriert** ist. Nach dem Login ist der Download damit frei — ohne
Token, ohne Zusatzschritt. Das folgt aus 3: ein Login ist der stärkere
Ausweis, und eine Ausnahme, die den Betreiber nach erfolgreicher Anmeldung
noch einmal nach einem zweiten Geheimnis fragt, schützt nichts, das nicht
schon geschützt wäre.

Der Router bekommt weiterhin **nur einen Wahrheitswert**, nie das Passwort,
den Hash oder das Token: was er nicht kennt, kann er nicht versehentlich in
eine Antwort oder ins Log schreiben.

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
- Migration: eine Datenbank auf Schemaversion 2 wird auf 3 gehoben, ohne
  Bestandszeilen in `device`/`signal`/`command` anzutasten.

`tests/api/test_security.py` wächst um:

- Sitzungs-Cookie kommt durch jede der fünf `/api`-Router-Gruppen.
- Bearer-Token kommt unverändert weiter durch.
- Weder noch → 401, solange ein Zugangsmittel eingerichtet ist.
- Gar nichts eingerichtet → alles offen (Zustand vor der Einrichtung).
- `POST /auth/setup` nach gesetztem Passwort → 409.
- `POST /auth/setup` ohne Passwort, aber mit konfiguriertem Token: ohne
  Token → 401, mit gültigem Token → Passwort gesetzt. Das ist der Test, der
  die Lücke aus 5 offenhielte, wenn er fehlte.
- Die umbenannte Startwarnung `cli._warn_if_no_credentials`: warnt ohne
  jedes Zugangsmittel, schweigt bei gesetztem Passwort **und** bei
  konfiguriertem Token.
- Logout macht das Cookie sofort wertlos (derselbe Wert danach → 401).
- `/api/live` verbindet mit Cookie und ohne Subprotokoll.
- `/cmd` und `/resync` bleiben in **jedem** dieser Zustände offen.
- `GET /api/diagnostics/fabric-backup` nach Login ohne Token → 200.

## 13. Offene Punkte

**13.1 Kein TLS.** Beim Login geht das Passwort im Klartext über das Netz.
Das ist keine Verschlechterung — der `Authorization`-Header tut das heute
schon —, aber ein Passwort wird von Menschen wiederverwendet, ein
dienstspezifisches Token nicht. Die Dokumentation muss deshalb ausdrücklich
zu einem Passwort raten, das nirgends sonst benutzt wird. TLS (Zertifikat,
Reverse-Proxy, `Secure`-Flag am Cookie) ist ein eigener Entwurf.

**13.2 Restliche Konfiguration in der Oberfläche.** Miniserver-Adresse,
matter-server-Adresse, Ports und Datenverzeichnis kommen weiterhin aus
`docker-compose.yml` und den CLI-Optionen. Sie in die Oberfläche zu holen —
mitsamt der Frage, welche Werte im laufenden Betrieb änderbar sind und
welche einen Neustart brauchen — ist der eigentliche Weg zur headless
aufgesetzten Installation und bekommt eine eigene Spec. Dieser Entwurf legt
mit `setting` und dem Einrichtungsbildschirm die Mechanik dafür an.

**13.3 Mehrere Betreiber.** Ein Passwort, kein Benutzername, keine Rollen.
Sollte das je gebraucht werden, trägt das Schema es (`setting` wird zu einer
`user`-Tabelle), aber es ist heute kein Ziel.
