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

**Er läuft im aufrufenden Thread.** `logging.Handler.emit` wird dort
ausgeführt, wo die Zeile entsteht — bei diesem Projekt auch aus aiohttp und dem
chip-SDK, also aus fremden Threads. Der Handler hängt deshalb nur an den Ring
an (`collections.deque.append` ist unter CPython atomar) und weckt den
Event-Loop über `loop.call_soon_threadsafe`. Er wartet nie und kennt keine
asyncio-Primitive.

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

1. Ob die Zeilenobergrenze im Browser und die Ringgröße im Dienst verschieden
   sein sollten, ist ungeprüft. Vorschlag: gleich, bis jemand einen Grund
   nennt.
2. Der Stufenfilter wirkt clientseitig. Ein Dienst, der auf DEBUG läuft, füllt
   den Ring damit trotzdem mit Debug-Zeilen. Bis jemand DEBUG im Betrieb
   braucht: unverändert lassen.
3. Ein Herunterladen des Mitschnitts als Datei ist nicht Teil dieses Entwurfs.
