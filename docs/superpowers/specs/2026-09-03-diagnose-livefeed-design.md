# Live-Feed für Logs, UDP-Mitschnitt und Kommandos

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), Abschnitte 8.3
(WebSocket aus derselben Subscription) und 10.5 (Diagnose).

## 1. Das Problem

Die Ansicht „System" holt Mitschnitt, Kommando-Log und Systemcheck **einmal**
beim Öffnen. Wer eine Störung sucht, drückt also fortwährend neu laden — und
sieht dabei nie, was gerade passiert, sondern nur, was beim letzten Klick
schon vorbei war.

Für Logzeilen gibt es überhaupt keine Erfassung. Sie gehen nach `stderr` und
damit nach `docker logs`. Wer sie sehen will, braucht eine Shell auf dem Host —
und genau in dem Moment, in dem man sie braucht, sitzt man vor dem Browser.

## 2. Drei Ströme, jeder an seiner ehrlichen Quelle

### 2.1 UDP-Mitschnitt: aus dem Sender, nicht aus der Laufzeit

`UdpSender._record_sent` schreibt **nach** dem `sendto` mit. Das ist die
einzige Stelle, die weiß, was tatsächlich auf der Leitung war.

Der bequemere Weg wäre die vorhandene Beobachterkette der Laufzeit
(`Runtime._notify_observers`), die schon die Werte-Oberfläche speist. Sie ist
für diesen Zweck aber **falsch**: sie benachrichtigt bewusst nicht beim
Full-Resend und nicht beim Absenken eines Impulses (beides in `runtime.py`
begründet). Ein Feed darauf hieße „Mitschnitt" und zeigte etwas anderes als
den Verkehr — und zwar ausgerechnet in dem Fall, für den man ihn aufmacht:
„ging überhaupt etwas raus?"

`UdpSender` bekommt deshalb eine Beobachterkette nach demselben Muster wie
`Runtime`: Aufruf erst nach dem Senden, Fehler eines Beobachters werden
verschluckt, damit ein Diagnosewerkzeug nie den Pfad anhält, den es beobachtet.

### 2.2 Kommando-Log: vorhanden, wird nur nicht geschoben

Die `/cmd`-Aufrufe aus Loxone liegen bereits als `RingBuffer` in
`loxone/server.py`. Sie kommen als dritte Nachrichtenart mit — dieselbe
Mechanik, und in der Ansicht stehen sie ohnehin nebeneinander.

### 2.3 Logzeilen: neu

Ein `logging.Handler`, der in einen Ring schreibt und Beobachter benachrichtigt.
Er hängt am Logger `loxmatter` (nicht am Root-Logger: die Zeilen fremder
Bibliotheken gehören nicht in eine Bedienoberfläche), Stufe INFO.

Drei Eigenschaften, die nicht offensichtlich sind und den Entwurf bestimmen:

**Er protokolliert nie selbst.** Ein Handler, der beim Verarbeiten einer Zeile
eine Zeile erzeugt, ruft sich endlos auf. Das gilt auch für seine Beobachter:
ein Fehler dort wird verschluckt und **nicht geloggt**. Das ist die eine Stelle
im Projekt, an der ein verschluckter Fehler nicht durch einen Logeintrag
ausgeglichen werden darf.

Die Absicherung dafür ist eine Wiedereintrittssperre um die
Beobachterschleife, thread-lokal geführt (`LogBufferHandler`,
`diagnostics/logbuffer.py`, umgesetzt). **Sie deckt bewusst nicht
`self.format(record)` selbst ab** — das ist eine Lücke, keine Behauptung des
Gegenteils: ein Log-Argument, dessen `__str__`/`__repr__` seinerseits über
denselben Logger protokolliert, rekursiert durch `format()` ungebremst bis
`RecursionError`, ohne dass die Sperre je zum Zug käme. Kein heutiger
Aufrufer im Projekt tut das (alle `%`-Argumente sind einfache Werte); wer die
Sperre auf `format()` ausdehnt, muss den Eintrag trotzdem an den Ring
anhängen, bevor er abbricht — die Zeile soll nicht verlorengehen, nur weil
sie den Fehler ausgelöst hat.

**Er läuft im aufrufenden Thread.** `logging.Handler.emit` wird dort
ausgeführt, wo die Zeile entsteht — bei diesem Projekt auch aus aiohttp und dem
chip-SDK, also aus fremden Threads. Der Handler selbst hängt deshalb nur an
den Ring an (`collections.deque.append` ist unter CPython atomar) und ruft
seine Beobachter synchron im selben Thread auf; er wartet nie und kennt
selbst keine asyncio-Primitive (siehe `diagnostics/logbuffer.py`,
`LogBufferHandler.add_observer`: ein Beobachter darf laut Vertrag ebenfalls
nicht blockieren). **Das Wecken des Event-Loops ist deshalb Sache des
jeweiligen Beobachters, nicht des Handlers** — anders, als eine frühere
Fassung dieses Entwurfs hier behauptete. Für den Diagnose-Feed ist dieser
Beobachter `on_log` in `api/diagnostics_live.py`: er hält den laufenden Loop
fest, bevor er sich anmeldet, und reiht über
`loop.call_soon_threadsafe(queue.put, ...)` ein statt `queue.put(...)` direkt
aufzurufen — die erste Umsetzung tat das nicht, und eine Logzeile aus einem
echten fremden Thread wäre unter Umständen nie angekommen, weil ein
bereits schlafender Event-Loop nur über `call_soon_threadsafe` (nicht über
gewöhnlichen Queue-Zugriff) zuverlässig aus einem fremden Thread geweckt
wird.

**Er verschiebt keine Geheimnisse.** Der Feed zeigt, was ohnehin in
`docker logs` steht, und liegt hinter demselben Token-Schutz wie alle
`/api`-Routen. Die Fabric-Sicherung protokolliert bewusst gar nichts (siehe
`api/diagnostics.py`); das bleibt so.

### 2.4 Größen

Alle drei Ringe fassen 500 Einträge, wie die bestehenden. Bei zwei Geräten
deckt das rund eine Viertelstunde ab: alle 300 s läuft ein Full-Resend mit rund
140 Datagrammen, dazu alle 30 s der Heartbeat.

## 3. Transport

Ein zweiter WebSocket, `GET /api/diagnostics/live`. Er liegt unter `/api` und
erbt damit den Token-Schutz samt Subprotokoll-Weg ohne eine Zeile Zusatzcode
(Abschnitt 9.1 des Hauptdokuments).

**Getrennt vom Wertekanal `/api/live`, nicht angehängt.** Die beiden haben
verschiedene Lebensdauern und verschiedene Mengen: der Wertekanal läuft,
solange die Oberfläche offen ist, der Diagnosekanal nur, solange jemand
hinsieht. Wäre alles ein Kanal, bekäme jeder vergessene Browsertab auf der
Ansicht „Geräte" wochenlang jede Logzeile. Ausserdem trüge `/api/live` dann
dreierlei statt einem.

**Die gemeinsame Maschinerie wandert in ein eigenes Modul.** Begrenzte
Warteschlange mit Drop-Oldest, Trennungserkennung über `receive_text`, sauberes
Abräumen beider Teil-Tasks — das steckt heute in `api/live.py` und wird von
beiden Routen gebraucht. Zweimal gehalten hieße, jede künftige Korrektur
zweimal zu machen; die erste war schon nötig (die unbegrenzte Warteschlange,
Review-Fix Phase 5).

**Nachrichtenformat.** Jede Nachricht trägt ihre Art und einen Zeitstempel:

```json
{"kind": "datagram", "at": "…", "key": "d1_2_power", "value": "0"}
{"kind": "command",  "at": "…", "path": "/cmd/d1_1_on/1", "status": 200}
{"kind": "log",      "at": "…", "level": "WARNING", "logger": "…", "message": "…"}
```

Die tatsächlichen Feldnamen übernimmt die Umsetzung aus den bestehenden
Ring-Einträgen (`DatagramLogEntry`, `CommandLogEntry`), damit nicht dieselbe
Angabe zweimal verschieden heisst.

**Beim Verbinden zuerst eine Momentaufnahme**, dann live. Damit ist die Ansicht
sofort gefüllt und es entsteht keine Lücke zwischen „einmal abrufen" und „ab
jetzt zuhören". Die vorhandenen GET-Routen bleiben unverändert für Skripte
und `curl`.

## 4. Oberfläche

Die Ansicht „System" behält ihre Bereiche; sie füllen sich laufend statt
einmalig. Dazu vier Bedienelemente:

| | Vorgabe | warum |
|---|---|---|
| **Anhalten** | läuft | ohne das lässt sich nichts lesen, was scrollt |
| **Heartbeat und Resend ausblenden** | an | sonst spülen alle 300 s rund 140 Zeilen alles weg |
| **Stufenfilter Logs** | ab INFO | INFO ist die Stufe, auf der dieses Projekt die Ereignisse protokolliert, die man bei einer Störung sucht |
| **Zeilenobergrenze im Browser** | fest | ein tagelang offener Tab soll keinen Speicher füllen |

Der Filter „ausblenden" wirkt nur auf die **Anzeige**, nicht auf den Ring: wer
ihn ausschaltet, sieht die vorhandenen Zeilen sofort, ohne auf neue zu warten.

**Woran „Heartbeat und Resend" erkannt wird, entschied erst die Umsetzung —
dieser Entwurf ließ es offen.** Der erste Anlauf maß die Ankunftsrate im
Browser (ein `DATAGRAM_BURST_GAP_MS`-Fenster) und blendete alles aus, was zu
schnell aufeinanderfolgte — und traf damit auch einen echten Tastendruck:
`Runtime.on_event` sendet Impuls und Zähler binnen Mikrosekunden
hintereinander, genau das Muster, das die Heuristik für Rauschen hielt. Die
tatsächlich umgesetzte Fassung fragt stattdessen eine Tatsache statt einer
Vermutung ab: `UdpSender.send` reicht sein `force`-Argument unverändert als
Feld `forced` bis in `DatagramLogEntry` und von dort in die Live-Nachricht
durch (`api/diagnostics_live.py`). `force=True` setzen im ganzen Projekt
genau zwei Aufrufer, `Runtime.resend_all()` (Full-Resend) und der Heartbeat
— `False` heißt dagegen immer eine echte Wertänderung, wie schnell sie auch
kommt. `hideNoise` in `app.js` filtert seither auf `!entry.forced`, nicht
mehr auf die Ankunftsrate.

Der Kanal geht auf beim Wechsel auf „System" und beim Verlassen wieder zu.
**Genau eine Verbindung** — die Lehre aus Phase 5, wo ein zusätzliches
`x-init="init()"` pro Tab dauerhaft zwei offene Kanäle erzeugte, weil Alpine 3
`init()` schon von sich aus aufruft.

## 5. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Ein Beobachter wirft | verschluckt; Log- und Sendepfad laufen weiter |
| Fehler im Log-Beobachter | verschluckt und **nicht geloggt** — das wäre die Endlosschleife |
| Browser liest nicht mehr | Warteschlange gedeckelt, ältester Eintrag fällt heraus |
| Verbindung bricht ab | Wiederverbindung mit wachsender Wartezeit, nur solange die Ansicht offen ist |
| Logzeile aus fremdem Thread | landet im Ring, weckt den Loop, blockiert den Aufrufer nie |
| Kein laufender Event-Loop | der Handler schreibt trotzdem in den Ring; nur die Benachrichtigung entfällt |

## 6. Prüfung

Alle Tests laufen ohne Hardware und ohne Netzzugriff.

- **Gegen die echte ASGI-Anwendung**, nicht gegen eine Nachbildung — nach dem
  Muster der WebSocket-Tests aus Phase 5. Der Grund steht dort: ein
  In-Process-Test war grün, während `/api/live` in **jeder** echten
  Installation 404 lieferte, weil uvicorn gar keine WebSocket-Implementierung
  installiert hatte. Ein Rauchtest mit rohem RFC-6455-Handshake gehört dazu.
- Eine Logzeile aus einem **fremden Thread** kommt an.
- Ein **werfender Beobachter** hält weder das Logging noch den UDP-Sender an.
- Der Handler erzeugt **keine** neue Logzeile, auch nicht im Fehlerfall
  (Prüfung auf Rekursionsfreiheit).
- Der Mitschnitt enthält, was der Sender geschickt hat — **einschliesslich**
  Impulsende und Full-Resend, die die Laufzeit-Beobachter auslassen. Das ist
  der Test, der Abschnitt 2.1 bewacht.
- Ohne Token antwortet die Route mit 401, wie jede `/api`-Route.
- Die Momentaufnahme beim Verbinden enthält vorhandene Einträge, danach kommen
  neue an.

## 7. Offene Punkte

1. **Entschieden: gleich.** `DIAGNOSTICS_LINE_LIMIT` in `app.js` und alle drei
   Ringgrößen im Dienst (`DATAGRAM_LOG_SIZE` in `loxone/sender.py`,
   `COMMAND_LOG_SIZE` in `loxone/server.py`, `LOG_BUFFER_SIZE` in
   `diagnostics/logbuffer.py`) stehen auf 500 — wie vorgeschlagen, ohne dass
   ein Grund für eine Abweichung genannt wurde.
2. **Teilweise entschärft, nicht geschlossen.** Der Stufenfilter wirkt
   weiterhin clientseitig — daran hat sich nichts geändert. Was sich
   geändert hat: `install_log_buffer()` setzt beim Start nicht nur die Stufe
   des Handlers, sondern auch die des Loggers `loxmatter` selbst (siehe
   Abschnitt 2.3-Ergänzung oben) — ohne diese Zeile hätte die Vorgabe „ab
   INFO" gar nichts erfasst, weil in diesem Projekt sonst niemand die
   Loggerstufe setzt. Solange niemand `install_log_buffer(level=logging.
   DEBUG)` aufruft — wofür `loxmatter run` heute keinen Schalter anbietet —,
   kann DEBUG den Ring gar nicht erreichen; der ursprüngliche Punkt bleibt
   aber unverändert gültig, sobald das einmal möglich wird: der Ring speichert
   dann ungefiltert, das Stufenfilter der Oberfläche wirkt weiter nur auf die
   Anzeige.
3. Ein Herunterladen des Mitschnitts als Datei ist weiterhin nicht Teil
   dieses Entwurfs.
