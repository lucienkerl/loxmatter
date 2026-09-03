# Einlernen per QR-Code — und das HTTPS, das dafür nötig ist

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), insbesondere
dessen Abschnitte 7.1 (Einlernen), 8 (WebUI) und 9 (Fehlerbehandlung), sowie
den [Zugangsschutz-Entwurf](2026-09-03-webui-login-design.md), dessen
Token-Modell hier an einer Stelle sichtbar bricht (Abschnitt 6).

## 1. Das Problem

Ein Matter-Pairing-Code ist elf Ziffern oder ein 21-stelliger `MT:`-String.
Beides steht in Kleinstschrift auf dem Gerät oder seiner Verpackung, und
beides tippt man vor einer Steckdose in der Fußleiste kniend in ein
Handy-Formular ab. Daneben ist derselbe Code als QR aufgedruckt. Ihn zu
lesen, statt ihn abzuschreiben, ist die ganze Anforderung.

Sie zerfällt an einer Browser-Regel: `navigator.mediaDevices.getUserMedia`
ist an einen *secure context* gebunden. Als solcher gelten `localhost`,
`127.0.0.1` und `[::1]` — private Adressbereiche wie `192.168.x.x`
ausdrücklich **nicht**, in keinem der drei großen Browser. Die Brücke liefert
ihre Oberfläche heute über reines HTTP an einer LAN-Adresse aus (kein TLS
irgendwo in `deploy/`). Vom Handy aus ist die Kamera damit gesperrt — also
genau in dem Fall, für den das Feature gedacht ist.

Deshalb ist dieser Entwurf zwei Dinge in einem: **TLS im Dienst** und **der
QR-Scan in der Oberfläche**. Das erste trägt für sich allein — heute geht das
API-Token im Klartext über das LAN, was der Sicherheitsabschnitt der README
bislang nicht erwähnt.

## 2. Was dieser Entwurf nicht antastet

`/cmd` und `/resync` bleiben unverändert auf HTTP. Der Miniserver ruft sie
als virtuelle Ausgänge auf und wird ein selbstsigniertes Zertifikat nicht
akzeptieren; das Hauptdokument führt diese Offenheit bereits als bewusste
Grenze. Ein Zwangs-Redirect von HTTP auf HTTPS ist aus demselben Grund
ausgeschlossen — er würde die Loxone-Strecke am ersten Tag stilllegen.

Der HTTP-Zugang bleibt vollständig funktionsfähig: dieselbe Oberfläche,
dieselben `/api`-Routen, derselbe Token-Wächter. HTTPS ist eine **zweite
Tür**, keine Ablösung.

Das Token-Modell aus dem Zugangsschutz-Entwurf bleibt unverändert:
`Authorization: Bearer`, `localStorage`, nie in einer URL, nie als
Query-Parameter. Abschnitt 6 beschreibt die eine Folge, die daraus für den
Origin-Wechsel entsteht — er ändert die Regel nicht, er zahlt ihren Preis.

Unverändert bleibt auch die bestehende Einlern-Route `POST
/api/devices/commission` samt `CommissionRequest`. Der QR-Scan erzeugt nur
einen String, den dieselbe Route entgegennimmt wie ein abgetippter. Es gibt
keine zweite Einlern-Strecke.

## 3. Der Ablauf aus Sicht des Nutzers

Im Kasten „Neues Gerät einlernen" (Ansicht 1) kommt ein Knopf **„QR-Code
scannen"** dazu.

Welchen der beiden Wege er öffnet, entscheidet **nicht** das Schema in der
Adresszeile, sondern `window.isSecureContext` — sonst behauptete die
Oberfläche auf einem `http://localhost`-Aufruf fälschlich, die Kamera sei
gesperrt, obwohl sie dort erlaubt ist.

**Ohne secure context** (der Regelfall: LAN-Adresse über HTTP) öffnet der
Knopf keinen Scanner, sondern eine Erklärung: die Kamera braucht eine
verschlüsselte Verbindung, der Browser wird gleich vor dem Zertifikat
warnen, und das ist hier erwartet und kein Angriff. Darunter ein Knopf „Zur
sicheren Verbindung wechseln", der auf `https://<derselbe
Host>:<https-port>/` umleitet. Die Erklärung nennt auch, was danach anders
ist (Abschnitt 6: das Token ist weg und muss einmal neu eingetragen werden) —
eine Überraschung an dieser Stelle sähe aus wie ein Defekt.

**Mit secure context** (HTTPS, oder `localhost`) öffnet er einen Sucher:
Kamerabild aus der rückwärtigen Kamera (`facingMode: "environment"`),
Dauerscan auf einem verborgenen `<canvas>`.
Bei Erkennung stoppt der Sucher, der Code steht **sichtbar** im vorhandenen
Pairing-Feld, und das Einlernen startet sofort.

Zwei Sicherungen, die keinen Klick kosten:

- Der Dauerscan löst **genau einmal** aus und schaltet Kamera und Schleife
  dabei ab. Ohne das würde ein zweites Erkennen desselben Codes eine zweite
  Einlernanfrage starten, während die erste noch läuft.
- Er löst nur bei etwas aus, das wie ein Matter-Code aussieht: `MT:`-Präfix
  oder genau elf Ziffern. Sonst startete ein zufällig ins Bild geratener
  WLAN-, Paket- oder Plakat-QR einen sinnlosen Einlernversuch — mit einer
  Fehlermeldung, die nach einem Gerätedefekt klänge, statt nach „falscher
  QR-Code".

Darunter bleibt **immer** — auch über HTTP, auch ohne jedes Zertifikat — ein
Bildweg sichtbar: ein Dateifeld mit `accept="image/*" capture="environment"`
(auf dem Handy öffnet das die Kamera-App, auf dem Rechner einen Dateiwähler),
zusätzlich Drag & Drop und Einfügen aus der Zwischenablage per Strg+V. Alle
drei landen im selben Dekodier-Pfad wie der Sucher. Der `paste`-Event liefert
seine Dateien über `event.clipboardData.files`, was **keinen** secure context
verlangt — anders als `navigator.clipboard.read()`, das hier deshalb nicht
verwendet wird.

Dieser Bildweg ist die Rückfalllinie. Wenn das Zertifikat auf einem Gerät
zickt (Abschnitt 5 ist darüber ehrlich unsicher), bleibt das Feature
benutzbar, statt zu verschwinden.

**Nach einem Fehlschlag bleibt der Code im Feld stehen.** Der wahrscheinlichste
Fehlschlag ist ein Thread-Gerät ohne Datensatz: `commission_with_code`
scheitert dann mit „Required network information not provided". Der Nutzer
trägt den Datensatz nach und drückt „Einlernen" — kein zweiter Scan.

### 3.1 Warum der Code nicht aus dem QR-Payload gelesen wird

Ein Matter-QR-Payload trägt mehr als den Setup-Code: Hersteller- und
Produkt-ID, Discriminator, ein Discovery-Bitfeld. Die Versuchung, daraus
„Thread-Gerät erkannt, bitte Datensatz eintragen" abzuleiten, wird hier
**nicht** bedient: das Discovery-Bitfeld beschreibt die Wege zur
Inbetriebnahme (BLE, On-Network, …), nicht das spätere Betriebsfunknetz. Ein
Thread-Gerät und ein WLAN-Gerät sind daran nicht sicher zu unterscheiden. Ein
Hinweis, der in der Hälfte der Fälle falsch ist, ist schlechter als keiner.

Der dekodierte String wird deshalb unverändert an `POST
/api/devices/commission` weitergereicht — dieselbe Zeichenkette, die auch
ein abtippender Nutzer erzeugt hätte.

## 4. TLS im Dienst

### 4.1 Zwei Listener auf einer App

`_run` in [`cli.py`](../../../src/loxmatter/cli.py) startet heute genau einen
`uvicorn.Server`. Daraus werden zwei, auf **derselben** `build_app`-Instanz:

| Listener | Port | Was darüber läuft |
|---|---|---|
| HTTP | `--listen` (8080) | alles wie bisher: `/cmd`, `/resync`, `/health`, `/`, `/static`, `/api/*`, `/ca.crt` |
| HTTPS | `--https-port` (8443) | dieselbe App; praktisch genutzt für `/`, `/static`, `/api/*` |

Eine App, zwei Bindungen — kein zweiter Router, kein zweiter Zustand. `/cmd`
ist über HTTPS ebenfalls erreichbar und dort schlicht ungenutzt; es zu
sperren hieße, dieselbe App an zwei Stellen unterschiedlich zusammenzubauen,
für einen Gewinn von null.

`uvicorn.Config` nimmt `ssl_certfile`/`ssl_keyfile` direkt entgegen (belegt
gegen die installierte uvicorn 0.52.4,
`.venv/lib/python3.12/site-packages/uvicorn/config.py:234`). Ein eigener
`SSLContext` ist nicht nötig.

### 4.2 Der Signal-Fallstrick, belegt gegen die installierte uvicorn

`Server.serve()` betritt `capture_signals()`, und das setzt seine Handler mit
`signal.signal(sig, self.handle_exit)` für `SIGINT`/`SIGTERM`
(`.venv/lib/python3.12/site-packages/uvicorn/server.py:328`, Zeile
`original_handlers = {sig: signal.signal(sig, self.handle_exit) …}`).

Zwei Server im selben Prozess bedeuten damit: der zweite **überschreibt** den
Handler des ersten. Bei Strg-C setzt nur der zweite sein `should_exit`; der
erste bemerkt nichts und läuft weiter. Ein `asyncio.gather` über beide
`serve()`-Aufrufe hinge dann auf unbestimmte Zeit — ein Dienst, der sich
nicht mehr beenden lässt, mit einer Ursache, die man in keinem Log sieht.

Das ist kein vermuteter, sondern ein am installierten Quelltext abgelesener
Fallstrick, und er wird so aufgelöst: auf **beiden**
`Server`-Instanzen wird `capture_signals` vor dem Start durch einen
`contextmanager` ersetzt, der nichts tut als `yield` — instanzweise gesetzt,
nicht auf der Klasse, damit ein anderer Aufrufer von `uvicorn` im selben
Prozess (heute keiner, morgen vielleicht ein Test) davon unberührt bleibt.
Damit fasst uvicorn die Signale gar nicht erst an, und es bleibt bei dem
Abbruchweg, den der Docstring von `_run` bereits beschreibt: der
SIGINT-Handler, den `asyncio.run` seit Python 3.11 selbst installiert, bricht
den `_run`-Task ab. Der Abbruch erreicht beide `serve()`-Tasks als
`CancelledError`; `_run` setzt in seinem Abbruchzweig auf beiden Servern
`should_exit = True` und wartet kurz auf ihr geordnetes Ende, bevor der
vorhandene `finally`-Block Laufzeit, Sender und Client schließt.

Ein Test hält das fest: Dienst starten, Abbruch auslösen, beide Server sind
beendet. Ohne diesen Test ist die Neutralisierung eine Zeile, die bei einem
uvicorn-Update stillschweigend wirkungslos werden kann.

### 4.3 Neue Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| `--https-port` | `8443` | `0` schaltet HTTPS vollständig ab |
| `--tls-dir` | `<store-Verzeichnis>/tls` | wo Zertifikate und Schlüssel liegen |

HTTPS ist **standardmäßig an**. Ein Feature, das erst nach dem Lesen einer
Option funktioniert, funktioniert für niemanden.

### 4.4 Was erzeugt wird

Beim Start wird erzeugt, was fehlt. Erzeugt wird ein **Paar**, nicht ein
einzelnes Zertifikat:

- **Eine lokale CA:** `CN=loxmatter local CA`, zehn Jahre, `basicConstraints:
  CA:TRUE, pathlen:0`, `keyUsage: keyCertSign, cRLSign`. Sie existiert allein
  dafür, dass ein Handy **einmal** etwas Dauerhaftes als vertrauenswürdig
  einrichten kann — würde man das Server-Zertifikat selbst installieren,
  wäre die Installation nach jedem Adresswechsel hinfällig.
- **Ein Server-Zertifikat**, von dieser CA signiert. `CN=loxmatter`, SAN über
  `DNS:localhost`, `IP:127.0.0.1`, `IP:[::1]` und **alle** beim Start
  gefundenen lokalen IPv4-Adressen. Laufzeit **397 Tage**. Apples
  398-Tage-Grenze ist für Zertifikate belegt, die an eine im System
  mitgelieferte Wurzel anschließen; ob sie auch für eine selbst installierte
  Wurzel greift, ist nicht belegt. Die Frist einzuhalten kostet hier nichts
  und nimmt die Frage aus dem Weg — der Preis wäre eine jährliche
  Neuerzeugung, die ohnehin automatisch läuft (siehe unten).

Vier Dateien in `--tls-dir`: `ca.crt`, `ca.key`, `server.crt`, `server.key`.
Beide Schlüssel mit Dateirechten `0600`.

Neu erzeugt wird das **Server**-Zertifikat, wenn es fehlt, abgelaufen ist,
oder eine aktuelle lokale IPv4-Adresse nicht in seinem SAN steht — der
DHCP-Fall, in dem die Brücke nach einem Router-Neustart unter einer anderen
Adresse hängt und das alte Zertifikat für sie nicht mehr gilt. Die **CA**
wird dabei ausdrücklich behalten: würde sie mitrotieren, wäre das auf dem
Handy installierte Vertrauen bei jedem Adresswechsel wertlos, und niemand
verstünde, warum.

### 4.5 TLS darf den Start nicht verhindern

`cryptography` ist neu (weder direkt noch transitiv installiert, gegen die
aktuelle Umgebung geprüft) und kommt als Abhängigkeit dazu. Ein `openssl`-
Aufruf als Alternative wurde verworfen: das `python:3.12-slim`-Basisimage
garantiert das Binary nicht, und der Dockerfile sagt über sich selbst, dass
er nie gebaut wurde — dort auf ein ungeprüft vorhandenes Programm zu setzen,
wäre ein Fehler, der erst in der Installation auffiele.

Fehlt `cryptography` trotzdem, ist `--tls-dir` nicht beschreibbar, oder
scheitert die Erzeugung aus einem anderen Grund, dann **startet der Dienst
trotzdem** — nur mit dem HTTP-Listener und einer deutlichen Warnung im Log,
im selben Ton wie die vorhandene Warnung bei fehlendem API-Token. TLS ist
eine Zugabe; es darf die Loxone-Strecke nicht zum Stehen bringen. In diesem
Zustand meldet `/api/diagnostics/tls` (Abschnitt 5) den Grund, und der
QR-Knopf zeigt statt „Zur sicheren Verbindung wechseln" den Bildweg als
einzigen Weg.

## 5. Die CA aufs Handy bekommen

**`GET /ca.crt`** liefert das CA-Zertifikat mit `Content-Type:
application/x-x509-ca-cert` — der Typ, an dem iOS es als installierbares
Profil erkennt.

Diese Route liegt bewusst **außerhalb** von `/api` und trägt **kein** Token.
Beides ist notwendig, nicht bequem:

- Jeder der fünf `/api`-Router hängt am Token-Wächter
  ([`server.py:363`](../../../src/loxmatter/loxone/server.py) ff.). Eine
  token-freie Route unterhalb von `/api` wäre eine Ausnahme in einer
  Invariante, die bisher ausnahmslos gilt — und die nächste Person, die einen
  Router hinzufügt, müsste sie kennen. `/ca.crt` steht deshalb neben
  `/health`, wo Token-Freiheit die Regel ist.
- Sie muss über **HTTP** erreichbar sein. Man lädt sie ja gerade, *bevor* man
  dem HTTPS-Zugang vertraut; über HTTPS abrufbar zu sein, hülfe erst, wenn
  man sie schon nicht mehr bräuchte.

**Das ist eine benennbare Schwäche, und sie kommt so in die README.** Wer
diese CA installiert, vertraut ihr für *jede* Adresse, nicht nur für diese
Brücke. Der öffentliche Teil kommt dabei über eine unverschlüsselte
Verbindung: wer im selben LAN zwischenfunken kann, kann eine eigene CA
unterschieben und ist damit dauerhaft in der Lage, sich für beliebige Seiten
auszugeben. Das ist ein echter Zugewinn an Angriffsfläche gegenüber heute,
und er wird hier eingegangen, weil die Alternative — ein echtes Zertifikat —
eine Domain und Internetzugang verlangt, die diese Brücke laut README
ausdrücklich nicht voraussetzen darf. Wer diesen Tausch nicht will, setzt
`--https-port 0` und benutzt den Bildweg aus Abschnitt 3.

**`GET /api/diagnostics/tls`** (geschützt, im vorhandenen Diagnose-Router)
liefert den Zustand: ob TLS läuft, der Port, die Adressen im SAN, das
Ablaufdatum, und im Fehlerfall den Grund aus Abschnitt 4.5. Anders als der
Download braucht dieser Zustand kein Vertrauen im Voraus — er wird gelesen,
wenn man bereits in der Oberfläche ist.

In Ansicht 4 („System") kommt ein Abschnitt dazu, der diesen Zustand zeigt,
den Download anbietet und die Einrichtung in je drei Zeilen beschreibt:

- **iOS:** Profil laden, unter *Einstellungen → Allgemein → VPN und
  Geräteverwaltung* installieren — und dann, als eigener Schritt, unter
  *Einstellungen → Allgemein → Info → Zertifikatsvertrauen* einschalten.
  Dieser zweite Schritt wird regelmäßig übersehen, und ohne ihn bleibt das
  Zertifikat wirkungslos.
- **Android:** unter *Einstellungen → Sicherheit → Verschlüsselung und
  Anmeldedaten → Zertifikat installieren → CA-Zertifikat*.

## 6. Der Origin-Wechsel und das Token

`localStorage` ist origin-gebunden. `http://host:8080` und
`https://host:8443` sind verschiedene Origins — nach dem Wechsel ist das
Token weg, und jeder `/api`-Aufruf antwortet mit 401.

Es mitzunehmen ist keine Option. In den Query-String darf es nicht (Server-
und Proxy-Logs, Browser-History — die Begründung steht im
Zugangsschutz-Entwurf). Als URL-Fragment erreichte es zwar keinen Server-Log,
stünde aber weiterhin in der Browser-History und in jedem geteilten Link;
das ist derselbe Fehler in leiser.

Also wird es **einmal neu eingetragen**, und die Oberfläche sagt das vorher:
die Erklärseite aus Abschnitt 3 nennt es als erwarteten Schritt, nicht als
Störung. Nach dem Wechsel greift der vorhandene 401-Weg, der das Token-Feld
von selbst aufklappt — es ist keine neue Mechanik nötig, nur ein Satz an der
richtigen Stelle.

Danach ist der HTTPS-Origin der bessere: dort geht das Token verschlüsselt
über das Netz, auf HTTP tut es das bis heute nicht. Die Oberfläche empfiehlt
deshalb, auf HTTPS zu bleiben, statt zurückzuwechseln.

## 7. Die Dekodierung

Im Browser, mit einer vendorten Kopie von **jsQR 1.4.0** (MIT) neben
`alpine.min.js` — dasselbe Muster, aus demselben Grund: kein Netzverweis, kein
Build-Schritt, funktionsfähig in einer Installation ohne Internet.

Das Bild — aus dem Sucher, der Kamera-App, der Zwischenablage oder einer
Datei — wird auf ein verborgenes `<canvas>` gezeichnet, dessen `ImageData`
geht an jsQR, heraus kommt der String. Das Bild verlässt den Browser nie.
Über eine unverschlüsselte HTTP-Verbindung ist das kein Nebenaspekt: ein
hochgeladenes Foto läge im Klartext auf dem Kabel.

**Größe, offen benannt:** jsQR wird nur unminifiziert ausgeliefert, 251 KB
(`dist/jsQR.js`, jsdelivr, SHA-256 wird beim Vendoren im Kopf von
`index.html` festgehalten, wie bei Alpine). Alpine daneben ist 54 KB. Das
vervierfacht das mitgelieferte JavaScript. Hingenommen, weil es lokal
ausgeliefert wird und einmalig lädt; ein Nachladen erst bei Bedarf wäre eine
zweite Ladestrecke samt Fehlerbehandlung für einen Gewinn, den man auf einem
LAN nicht misst.

**Verworfen: serverseitige Dekodierung.** Eine Route, die das Bild
entgegennimmt und mit `pyzbar` dekodiert, kostete eine native
Systembibliothek (`libzbar0`) in einem Dockerfile, das nie gebaut wurde, und
schöbe das Foto unverschlüsselt über das LAN. Der einzige Gewinn wäre etwas
robustere Erkennung bei schlechten Aufnahmen — bei einem gedruckten QR-Code
aus 15 cm Entfernung kein Unterschied, den jemand bemerkt.

**Verworfen: `BarcodeDetector` als Schnellpfad.** Die Browser-eigene API gibt
es in Chrome, nicht in Safari. Sie spart nichts (jsQR muss als Rückfall
ohnehin mit) und erzeugt einen zweiten Code-Pfad, der auf der Hälfte der
Geräte nie läuft und deshalb dort auch nie auffällt, wenn er kaputt ist.

## 8. Fehlerbehandlung

| Lage | Verhalten |
|---|---|
| Kein Code im Bild | „Kein QR-Code erkannt" am Bildweg; der Sucher scannt weiter |
| Code erkannt, aber kein Matter-Code | „Das ist kein Matter-Pairing-Code" — kein Einlernversuch (Abschnitt 3) |
| Kamerafreigabe verweigert | Sucher schließt, Hinweis auf den Bildweg darunter |
| Keine Kamera vorhanden | Sucher-Knopf erscheint gar nicht erst; Bildweg bleibt |
| Kein secure context | Erklärung + Wechsel-Knopf statt Sucher (Abschnitt 3) |
| TLS nicht verfügbar (4.5) | Kein Wechsel-Knopf; Bildweg als einziger Weg, mit dem Grund aus `/api/diagnostics/tls` |
| Einlernen scheitert | Vorhandene Fehleranzeige; der Code bleibt im Feld stehen |

Alle Kamerafehler landen in derselben Anzeige wie die vorhandenen
Einlernmeldungen (`commissionMessage`). Kein zweiter Meldungskanal.

## 9. Prüfung

**Automatisiert.** Zertifikatserzeugung: SAN enthält die lokalen Adressen;
Neuerzeugung bei abgelaufenem Zertifikat und bei einer Adresse, die nicht im
SAN steht; die CA überlebt diese Neuerzeugung; Dateirechte `0600` auf beiden
Schlüsseln. Routen: `/ca.crt` ohne Token erreichbar, richtiger `Content-Type`,
`503` bei abgeschaltetem TLS; `/api/diagnostics/tls` **mit** Token geschützt
(im Stil von `tests/api/test_security.py`). Start: zwei Server, Abbruch
beendet beide (Abschnitt 4.2). Ausfall: fehlendes `cryptography` und ein
nicht beschreibbares `--tls-dir` führen zu einem laufenden Dienst ohne
HTTPS, nicht zu einem Startfehler. Markup: der Scan-Knopf, das Dateifeld und
die vendorte jsQR-Datei sind vorhanden, und `index.html` enthält weiterhin
keinen Netzverweis — im Stil der bestehenden Prüfungen in
[`tests/api/test_web.py`](../../../tests/api/test_web.py), inklusive
`_without_comments`.

**Nicht automatisiert — von Hand abzunehmen.** Diese Punkte sind
Abnahmekriterien des Entwurfs, keine Nebensache:

1. Vom **Android**-Handy: `http://<ip>:8080` öffnen, „QR-Code scannen",
   wechseln, Warnung durchklicken, Token neu eintragen, Sucher öffnet,
   echter Gerätecode wird erkannt und eingelernt.
2. Vom **iPhone**: derselbe Weg. **Hier sitzt die Unsicherheit dieses
   Entwurfs.** Ob Safari die Kamera auf einer Seite mit durchgeklicktem, aber
   nicht vertrautem Zertifikat freigibt, ist nicht belegt; es gibt Berichte,
   dass es sie trotz akzeptierter Ausnahme verweigert. Falls das eintritt:
   CA über `/ca.crt` laden, installieren, unter Zertifikatsvertrauen
   einschalten (Abschnitt 5) und den Weg wiederholen. Erst wenn **auch das**
   scheitert, ist die Live-Kamera auf iOS nicht erreichbar — dann bleibt der
   Bildweg, der dafür ausdrücklich vorgesehen ist, und der Entwurf verliert
   eine Bequemlichkeit, nicht das Feature.
3. Der Bildweg auf beiden Geräten **über HTTP**, ohne jedes Zertifikat.
4. Ein Thread-Gerät: Scan schlägt fehl, Code bleibt stehen, Datensatz
   nachtragen, „Einlernen" gelingt.

## 10. Offene Punkte

- Der Verzicht auf Payload-Auswertung (3.1) bedeutet, dass ein Thread-Gerät
  weiterhin erst am Fehlschlag erkennbar ist. Sollte sich zeigen, dass das im
  Alltag stört, wäre der ehrliche Weg nicht das Discovery-Bitfeld, sondern
  ein gemerkter Thread-Datensatz, der einfach immer mitgeschickt wird.
- Ob `python-matter-server` und der QR-Scan zusammen auf einem Handy-Browser
  über eine langsame WLAN-Strecke flüssig genug sind, ist ungeprüft — der
  Sucher rechnet pro Bild ein volles `ImageData` durch jsQR. Falls es hakt,
  ist die Abtastrate der erste Stellhebel, nicht die Bibliothek.
- Der Dockerfile bleibt ungebaut und ungeprüft, wie bisher. `cryptography`
  bringt Wheels für die üblichen Plattformen mit; belegt ist das für dieses
  Image erst an einem echten Build-Log.
