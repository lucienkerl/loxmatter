# Die Bruecke ueberlebt einen Neustart von matter-server

Entwurf, 8. September 2026. Gibt loxmatter zurueck, was es heute nicht hat:
es bemerkt den Verlust der Verbindung zu matter-server, baut sie wieder auf,
und sagt die Wahrheit darueber, solange sie fehlt.

Anlass ist ein Betriebsausfall vom selben Abend, zweimal in einer Stunde
reproduziert. Der ganze Hergang steht in Abschnitt 1.

## 1. Was passiert ist

Gemeldet wurde: „Dual Button und Fensterkontakt senden keine Daten mehr."
Gefunden wurden **zwei voneinander unabhaengige Fehler**, von denen der
zweite den ersten verdeckt hat.

### 1.1 Der harmlose Teil

Der Fensterkontakt war nie defekt. Er meldet `BooleanState` (Cluster 69) nur
bei **Aenderung**, und kein Signal traegt das Resend-Flag. Seit dem letzten
Neustart der Bruecke hatte niemand das Fenster bewegt — also kam nichts. Ein
Gerät, das gerade still ist, sah in der Oberflaeche exakt so aus wie eines,
von dem seit Tagen nichts kommt. Genau das behebt Abschnitt 5.

### 1.2 Das tote Abo

Fuer den BILRESA-Taster (Node 4) hielt matter-server seit dem 3. September
19:32 ein Abo mit Berichtsintervall [0, 900] fuer lebendig — und protokollierte
seither **keine einzige Zeile**. Kein Timeout, keine Erneuerung. Auch nicht,
als ein Neustart des Border Routers die Abos von fuenf anderen Geraeten riss:
bei Node 4 war nichts mehr da, was haette reissen koennen.

Das Geraet selbst war die ganze Zeit erreichbar. Ping ueber IPv6: 6,8 s,
erfolgreich. Live-Lesen von `0/47/12`: 7,5 s, Antwort `200` Halbprozent, also
volle Batterie. Es antwortete auf jede Frage und stellte von sich aus keine.

Geheilt hat es erst ein Neustart von matter-server. **`interview_node` reicht
nicht** — die Funktion liest die Geraetedaten neu, baut das Abo aber nicht
neu auf (belegt: nach dem Aufruf fehlt die Zeile „Setting up attributes and
events subscription" im Log).

Dieser Fehler liegt in matter-server, nicht in loxmatter. Er ist hier
festgehalten, weil er die Fehlersuche gepraegt hat, und weil er die
Begruendung fuer Abschnitt 5 liefert. **Er ist nicht Gegenstand dieses
Entwurfs.**

### 1.3 Der eigentliche Fehler: die Bruecke ueberlebt den Neustart nicht

Als matter-server neu startete, um 1.2 zu heilen, verstummte loxmatter
vollstaendig — und meldete es niemandem:

| Von aussen sichtbar | Tatsaechlich |
| --- | --- |
| WebUI antwortet | — |
| `bridge_alive` pulst weiter im 30-Sekunden-Takt | kein Geraetewert kommt mehr an |
| `GET /api/diagnostics/system` meldet `matter-server -> True \| Connected.` | jedes `/cmd/...` antwortet mit **502 Bad Gateway** |

Im loxmatter-Log steht dazu **keine einzige Zeile**. Kein Reconnect-Versuch,
keine Warnung. Einziger Ausweg war ein Neustart des Containers.

Dasselbe war Stunden zuvor schon einmal passiert und hatte den urspruenglich
gemeldeten Ausfall verursacht; der Neustart der Bruecke, mit dem der Betreiber
es zu beheben versuchte, hat es voruebergehend behoben.

## 2. Warum es passiert

Zwei Stellen, beide klein, beide eindeutig.

**`connected` fragt das Falsche.** Die Eigenschaft ist
`self._upstream is not None` (`matter/client.py`). Das Feld wird einmal in
`connect()` gesetzt und ausschliesslich von `disconnect()` geleert. Stirbt
das Websocket, bleibt es stehen. Die Pruefung beantwortet „hat jemand
`connect()` aufgerufen?" und nicht „steht die Verbindung?". Ihr eigener
Docstring sagt das bereits — er nennt die Bedingung als bewusst identisch zu
`_require_upstream`. Als Grundlage eines Gesundheitschecks ist sie damit
wertlos.

**Niemand schaut auf den Listener.** `_start_listener` gibt den Task von
`upstream.start_listening()` zurueck, `connect()` legt ihn in
`self._listener_task` — und danach sieht ihn nie wieder jemand an. Kein
`add_done_callback`, keine Ueberwachung. Endet er mit einer Ausnahme, wird
sie nie abgeholt. Das Signal, das den Abriss meldet, existiert bereits; es
wird nur weggeworfen.

## 3. Der Client merkt den Abriss

Zwei Aenderungen an `BridgeMatterClient`.

**`connected` prueft kuenftig beides:**

```python
return (
    self._upstream is not None
    and self._listener_task is not None
    and not self._listener_task.done()
)
```

Damit wird `_check_matter_server` (`api/diagnostics.py`) ohne eigene
Aenderung ehrlich. Der Diagnosepunkt aus 1.3 faellt hier als Nebenprodukt ab
— ein Zeichen dafuer, dass die Ursache richtig getroffen ist.

**Neu: `await client.wait_for_link_loss()`** — wartet auf den Listener-Task
und kehrt zurueck, sobald er endet, gleich ob mit Ausnahme oder regulaer. Die
Ausnahme wird dabei abgeholt und protokolliert, nicht weitergeworfen: der
Aufrufer will wissen, *dass* die Verbindung weg ist, und soll nicht selbst
zwischen Abbruchgruenden unterscheiden muessen.

Kein Polling, kein Timer. Der Task ist das Signal.

## 4. Die Wiederanbindung

Neues Modul `src/loxmatter/matter/supervisor.py` mit zwei Funktionen. Es
liegt bewusst nicht im Client: der Wiederaufbau braucht `Store` und
`Runtime`, und ein Client, der die halbe Anwendung kennt, waere die
schlechtere Grenze.

**`attach(client, store, runtime) -> None`** — die Sequenz, die heute
wortgleich in `cli.serve()` steht: `subscribe` → `snapshots` →
`seed_from_snapshot` → `backfill_device_types` → `backfill_commands` →
`resend_all`. Eine Stelle, zwei Aufrufer: der Start und jeder Wiederaufbau.

Dass `resend_all()` dabei mitlaeuft, ist der eigentliche Gewinn. Nach einem
Wiederaufbau bekommt Loxone den **vollstaendigen** Zustand zurueck, statt auf
den Werten sitzenzubleiben, die beim Abriss zufaellig die letzten waren. Das
entspricht der bestehenden Zusicherung „ein Neustart der Bruecke wirkt wie
`/resync`" (Spec 6.4) — sie gilt kuenftig auch fuer eine Wiederverbindung.

**`supervise(client, store, runtime) -> None`** — die Schleife:

1. `await client.wait_for_link_loss()`
2. Warnung protokollieren (die Zeile, die heute Abend gefehlt hat)
3. `await client.connect()`, bei Fehlschlag Backoff und erneut
4. `await attach(client, store, runtime)`
5. von vorn

Backoff: 1 s, verdoppelnd, gedeckelt bei 60 s, zuruecksetzen nach jedem
Erfolg. **Ohne Aufgabegrenze.** Eine Bruecke, die nach zehn Versuchen
aufgibt, ist schlechter als eine, die alle sechzig Sekunden weiter anklopft —
matter-server kann beliebig lange weg sein, und niemand steht daneben.

`cli.serve()` ruft `attach()` einmal beim Start und startet `supervise()` als
Hintergrund-Task. **`runtime.start()` bleibt draussen** und laeuft weiterhin
genau einmal: es startet die Heartbeat- und Resend-Schleifen, und die sollen
den Ausfall ueberdauern, nicht mit ihm neu beginnen.

## 5. Der Heartbeat verstummt

`Runtime.__init__` bekommt einen weiteren Nur-Schluesselwort-Parameter:

```python
link_ok: Callable[[], bool] = lambda: True
```

`_heartbeat_loop` ueberspringt das Senden von `HEARTBEAT_KEY`
(`"bridge_alive"`), solange `link_ok()` `False` liefert. `cli.serve()`
uebergibt **`lambda: client.connected`**, nicht `client.connected`:
`connected` ist eine Property, der zweite Ausdruck waere also ein einmal
ausgewerteter Bool. Der Heartbeat hinge dann fuer immer am Zustand des
Startmoments — und weil der beim Start `True` ist, wuerde er nie verstummen
und dieser ganze Abschnitt waere wirkungslos, ohne dass ein Test es merkt,
der `link_ok` selbst uebergibt. Test 3 in Abschnitt 7 prueft deshalb die
Weitergabe aus `serve()` mit, nicht nur das Verhalten von `Runtime`.

> **Berichtigung (Schluss-Review, 9. September 2026).** Der letzte Satz oben
> beschreibt einen Test, den es nicht gibt und nicht geben wird — und das ist
> Absicht, nicht Versäumnis. Der Umsetzungsplan begründet in seinem Abschnitt
> [„Abweichung vom Entwurf, bewusst"](../plans/2026-09-08-matter-server-reconnect.md#abweichung-vom-entwurf-bewusst),
> warum die Annotation `link_ok: Callable[[], bool]` die **stärkere**
> Absicherung ist: ein `bool` an dieser Stelle ist damit ein Typfehler, den
> `mypy --strict` in CI abweist — der Fehler kann gar nicht erst committet
> werden, während ein Test ihn erst nach dem Commit fände. Probeweise
> eingesetzt (`link_ok=client.connected` statt `link_ok=lambda:
> client.connected`) meldet mypy wörtlich: `src/loxmatter/cli.py:591: error:
> Argument "link_ok" to "Runtime" has incompatible type "bool"; expected
> "Callable[[], bool]"  [arg-type]`. Was dieser Abschnitt inhaltlich will —
> dass ein einmal ausgewerteter Wert den Puls nicht anhalten kann —, deckt
> Test 3 auf der Verhaltensseite ab; siehe die Berichtigung dort. Der falsche
> Satz bleibt stehen: dieses Projekt schreibt seine Belege nicht rückwirkend
> um.

Damit merkt **Loxone selbst**, dass etwas nicht stimmt: der Watchdog am
`bridge_alive`-Eingang schlaegt an, ohne dass jemand eine Oberflaeche
oeffnet. Genau das fehlte heute Abend.

**Die bestehende Zusicherung bleibt unangetastet.** Der Kommentar in
`_heartbeat_loop` haelt fest, dass ein **Sendefehler** die Schleife nicht zum
Schweigen bringen darf, sonst friert der Loxone-Watchdog auf dem letzten Wert
ein. Das gilt weiter und wird nicht angefasst. „Das Senden scheitert" und
„es gibt keine Matter-Verbindung" sind zwei verschiedene Zustaende mit zwei
verschiedenen richtigen Antworten: beim ersten weiterpulsen, damit der Fehler
sichtbar wird, beim zweiten schweigen, damit er sichtbar wird. Der Unterschied
ist, wovon der Puls eine Aussage macht.

Der Vorgabewert `lambda: True` haelt jeden bestehenden Test unveraendert
lauffaehig.

## 6. „Zuletzt gehoert"

`Runtime` fuehrt `_last_heard: dict[int, str]` (Geraete-ID auf
ISO-8601-Zeitstempel), gesetzt in `on_attribute`, `on_event` und
`on_node_snapshot` — also ueberall dort, wo tatsaechlich etwas von einem
Geraet eintrifft. Gelesen ueber eine Methode `last_heard_for(device_id) ->
str | None` neben dem vorhandenen `last_values_for`.

`DeviceOut` (`api/models.py`) bekommt ein Feld `last_heard: str | None`,
gefuellt in `api/devices.py` dort, wo heute schon `online` aus
`runtime.last_values_for(device.id)` gelesen wird.

**Nur im Speicher, nicht in der Datenbank.** Dieselbe Begruendung, die im
Docstring von `StoredDevice` bereits fuer `online` steht: Erreichbarkeit ist
Laufzeitzustand. Ein Zeitstempel, der einen Neustart ueberlebt, behauptet
nach dem Start etwas, das niemand geprueft hat — schlechter als gar keiner.
`None` heisst dann ehrlich „seit dem Start dieser Bruecke nichts gehoert".

Das ist der Unterschied, den 1.1 sichtbar gemacht hat: ein Geraet, das gerade
still ist, von einem, von dem seit fuenf Tagen nichts kam. Beide zeigen heute
`online: true`, und `online` bleibt auch kuenftig, was es ist — die Anzeige
bekommt daneben die zweite Zahl, die die Frage erst beantwortbar macht.

## 7. Tests

Vier Tests, alle ohne Netz, alle gegen die vorhandenen Attrappen in
`tests/matter/test_client.py` bzw. `tests/loxone/`:

1. **Abriss wird bemerkt** — eine `FakeUpstream`, deren `start_listening`
   nach dem Setzen von `init_ready` wirft. Danach ist `connected` `False`,
   und `wait_for_link_loss()` kehrt zurueck, statt zu haengen.
2. **Wiederaufbau mit Backoff** — eine Client-Attrappe, deren `connect()`
   zweimal wirft und beim dritten Mal traegt. `supervise` ruft danach
   `attach` auf; die Wartezeiten werden ueber eine injizierte
   `sleep`-Funktion gemessen, nicht real abgewartet.
3. **Heartbeat schweigt** — `Runtime` mit `link_ok=lambda: False`: der Sender
   bekommt keinen Aufruf mit `HEARTBEAT_KEY`. Gegenprobe mit
   `link_ok=lambda: True`: er bekommt ihn. Dazu ein zweiter Test, der die
   **Weitergabe** prueft: ein `link_ok`, das beim ersten Aufruf `True` und
   danach `False` liefert, muss den Puls tatsaechlich anhalten — das faellt
   durch, wenn `serve()` eine einmal ausgewertete Property statt eines
   Aufrufs uebergibt (siehe Abschnitt 5).
4. **„Zuletzt gehoert"** — nach `on_attribute` liefert `last_heard_for` einen
   Zeitstempel, vorher `None`.

> **Berichtigung zu Test 3 (Schluss-Review, 9. September 2026).** Der „zweite
> Test, der die **Weitergabe** prueft", wurde nicht geschrieben — bewusst,
> siehe die Berichtigung in Abschnitt 5 und den Abschnitt
> [„Abweichung vom Entwurf, bewusst"](../plans/2026-09-08-matter-server-reconnect.md#abweichung-vom-entwurf-bewusst)
> im Umsetzungsplan. Die Weitergabe aus `cli.serve()` sichert stattdessen die
> Annotation `Callable[[], bool]` unter `mypy --strict` ab, und zwar schärfer,
> als ein Test es könnte. Was der Entwurf inhaltlich verlangt, deckt
> `test_the_heartbeat_falls_silent_when_the_link_drops` in
> `tests/loxone/test_runtime.py` auf der Verhaltensseite ab: ein `link_ok`,
> das erst `True` und dann `False` liefert, muss den Puls tatsächlich
> anhalten — ein einmal ausgewerteter Wert fiele dort durch. Die geforderte
> Beißprobe zu Test 3 („die `link_ok`-Prüfung entfernen muss den Test rot
> machen") gilt unverändert und wurde durchgeführt. Der ursprüngliche
> Wortlaut oben bleibt stehen und wird nicht getilgt.

Zu Test 1 und 3 gehoert jeweils die Probe, dass der Test wirklich beisst:
`connected` haendisch auf die alte Bedingung zuruecksetzen bzw. die
`link_ok`-Pruefung entfernen muss den Test rot machen. Ein Test, der eine
Struktur nur benennt, statt sie zu pruefen, faellt beim naechsten Umbau nicht
auf.

## 8. Nicht Teil dieses Entwurfs

- **Das tote Abo aus 1.2.** Der Fehler liegt in matter-server. Ob
  `matterjs-server` ihn nicht hat, ist ungeprueft und waere ein eigener
  Befund.
- **Der Umzug auf matterjs-server.** Er erzwingt genau den Neustart, den
  dieser Entwurf ueberlebbar macht, und gehoert deshalb danach — siehe
  `2026-09-08-matterjs-server-umstieg-design.md`.
- **Eine Anzeige in der WebUI fuer den Ausfall.** Der Diagnosepunkt wird mit
  Abschnitt 3 ehrlich; ob die Geraeteliste zusaetzlich ein Banner braucht,
  ist eine Gestaltungsfrage und keine Voraussetzung dafuer, den Ausfall
  ueberhaupt zu bemerken.
- **Resend-Flags fuer die Ereignissignale aus 1.1.** Ob ein Fensterkontakt
  seinen Zustand periodisch wiederholen soll, ist eine Betriebsentscheidung
  ueber die vorhandene Einstellung, keine Codeaenderung.
