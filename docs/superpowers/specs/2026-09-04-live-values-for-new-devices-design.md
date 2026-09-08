# Live-Werte ohne Neustart: Abonnements nachziehen

Entwurf, 4. September 2026. Hebt die bekannte Grenze auf, die der
Modul-Docstring von
[`matter/client.py`](../../../src/loxmatter/matter/client.py) seit Phase 4
als offenen Punkt führt, und ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md).

## 1. Das Problem

`BridgeMatterClient.subscribe()`
([client.py:486](../../../src/loxmatter/matter/client.py)) läuft genau
einmal, beim Start der Brücke, und meldet für Attribute je ein Abonnement
pro **bei Aufruf bekanntem** (Node, Pfad)-Paar an. Der Grund dafür ist keine
Nachlässigkeit, sondern eine Eigenschaft von `python-matter-server`: bei
`EventType.ATTRIBUTE_UPDATED` ist `data` einzig der neue Wert — ohne
`node_id`, ohne Pfad. Eine einzelne Wildcard-Subscription könnte ein
Attribut-Update deshalb keinem Gerät zuordnen; erst `node_filter` und
`attr_path_filter` legen fest, wofür ein Callback steht, und der Callback
schließt beides als Closure ein.

Daraus folgen zwei Lücken, beide im Modul-Docstring bereits benannt:

1. **Ein nach diesem Aufruf eingelerntes Gerät** hat kein einziges
   Attribut-Abonnement. Es erscheint in der Oberfläche, aber jedes Signal
   steht auf „-", bis die Brücke neu startet.
2. **Ein bekanntes Gerät, das nachträglich neue Attributpfade meldet** —
   etwa nach einem Firmware-Update, das einen Cluster freischaltet — bleibt
   für diese neuen Pfade genauso stumm.

Der erste Fall wurde am 2026-09-04 gemeldet, unmittelbar nachdem das
Einlernen selbst wieder funktionierte. Die Oberfläche hat ihn bis dahin
nicht behoben, sondern nur beschrieben: sie zeigte nach jedem Einlernen den
Satz „Live-Werte erscheinen erst nach einem Neustart der Brücke".

## 2. Warum das Ereignis allein nicht reicht

Naheliegend wäre, auf `NODE_ADDED` zu reagieren. Das geht nicht, und der
Grund ist derselbe, der schon den Online-Status verschluckt hat:
matter-server meldet `NODE_ADDED` **während** `commission_with_code` noch
läuft (`device_controller._setup_node`, `signal_event` vor der Rückkehr des
Aufrufs). Zu diesem Zeitpunkt hat `store.register_device` dem Node noch
keine `device_id` gegeben, `_dispatch_loop`
([client.py:562](../../../src/loxmatter/matter/client.py)) verwirft die
Meldung folgerichtig, und eine zweite kommt für ein ruhig im Netz stehendes
Gerät nicht.

Aufgezeichnet am laufenden Stack, Node 8:

```
11:15:29  Matter commissioning of Node ID 8 successful.
11:15:31  <Node:8> Setting up attributes and events subscription.
11:15:33  Subscription succeeded with report interval [1, 60]
11:15:33  Commissioning of Node ID 8 completed.
```

Alles davon liegt vor der Rückkehr des Aufrufs an die Einlern-Route. Die
Route muss das Nachziehen deshalb **selbst** anstoßen.

Für den zweiten Fall dagegen ist das Ereignis der richtige Auslöser.
`NODE_UPDATED` feuert nicht pro Attributwert, sondern (geprüft gegen die
installierte Fassung) an fünf Stellen: nach einem erneuten Interview
(„existing node, signal node updated event"), beim Interview eines
Test-Node, nach einer geglückten Re-Subscription, nach einer geglückten
Subscription, und bei einem Wechsel der Erreichbarkeit. Neue Pfade werden
genau beim erneuten Interview sichtbar — und genau dann feuert es.

## 3. Die Lösung: ein Vorgang, zwei Auslöser

Eine neue Methode `BridgeMatterClient.follow_node(node_id)` erledigt das
Nachziehen vollständig:

```
neue_pfade = Pfade des Node − bereits abonnierte Pfade dieses Node
wenn leer: fertig                      ← der Regelfall, kostet nichts
für jeden neuen Pfad: Abonnement anlegen (dieselbe Queue wie subscribe())
device_id = resolve_device_id(node_id); ist sie None: fertig
handler.on_node_snapshot(device_id, snapshot)
```

Angestoßen wird sie von zwei Stellen:

- **der Einlern-Route**, nach `register_device`/`register_signals` — der
  Fall aus Abschnitt 2, den kein Ereignis abdecken kann;
- **der Dispatch-Schleife**, bei `NODE_ADDED` und `NODE_UPDATED` — der Fall
  der neuen Pfade.

Beide laufen durch denselben Code. Zwei Mechanismen, die fast dasselbe tun,
driften auseinander; einer, der von zwei Stellen gerufen wird, nicht.

**Der leere Diff ist der Kern der Kostenrechnung.** Die häufigen
`NODE_UPDATED` — Erreichbarkeit, Re-Subscription — betreffen Geräte, deren
Pfade sich nicht geändert haben. Für sie ist die Mengendifferenz leer und
der Vorgang endet vor jedem Store-Zugriff. Nur ein Gerät mit wirklich neuen
Pfaden löst Arbeit aus, und nur für diese Pfade.

## 4. Wer was weiß

Die Aufteilung folgt der bestehenden Schichtung: `subscribe()` bekommt heute
schon nur einen `resolve_device_id`-Aufruf und einen Handler, keinen Store.
Das bleibt so.

| Baustein | Zuständig für | Kennt nicht |
|---|---|---|
| `BridgeMatterClient` | Abonnements, Buchführung darüber, Weiterreichen des Abbilds | `Store`, `Runtime` |
| `Runtime` (als `RuntimeEventHandler`) | Signalzeilen, Signal-Cache, Werte säen | Abonnements |
| Einlern-Route | Anstoß nach dem Registrieren | beides im Detail |

**Client.** Neu ein `set[tuple[int, str]]` über die angelegten
Attribut-Abonnements, gefüllt in `subscribe()` und ergänzt in
`follow_node()`. Ohne diese Buchführung ließe sich „neu" nicht von „schon
vorhanden" unterscheiden, und ein zweites Abonnement für denselben Pfad
würde jeden Wert doppelt zustellen. Der Node selbst kommt aus dem
Upstream-Cache (`get_nodes()`), nicht aus einem zweiten Netzwerkaufruf.

**Handler-Protokoll.** `RuntimeEventHandler`
([client.py:134](../../../src/loxmatter/matter/client.py)) bekommt
`async def on_node_snapshot(device_id: int, snapshot: NodeSnapshot) -> None`.

**Runtime** erfüllt das mit drei Schritten, in dieser Reihenfolge:

1. `store.register_signals(device_id, snapshot)`
   ([store.py:938](../../../src/loxmatter/model/store.py)) — ausdrücklich
   für erneute Aufrufe gebaut: Schlüssel und Titel bleiben, `exported`
   bleibt bei bekannten Signalen unangetastet, `unit`/`exportability`/
   `functional` werden nachgezogen, neue Signale entstehen. Genau der Fall,
   für den der dortige Docstring das Firmware-Update nennt.
2. `invalidate_index(device_id)`
   ([runtime.py:183](../../../src/loxmatter/loxone/runtime.py)) — **ohne
   diesen Schritt bliebe der ganze Vorgang wirkungslos.** `_signal_for`
   ([runtime.py:166](../../../src/loxmatter/loxone/runtime.py)) lädt die
   Signale eines Geräts einmal und merkt sich das in `_indexed`; ein neu
   angelegtes Signal existierte dann in der Datenbank, aber jedes Update
   dazu liefe für den Rest des Prozesses ins Leere. Der Docstring von
   `invalidate_index` verlangt den Aufruf bereits, es gab bisher nur keinen
   Aufrufer.
3. Werte säen, über denselben Weg wie `seed_from_snapshot`
   ([runtime.py:230](../../../src/loxmatter/loxone/runtime.py)) —
   `_cache_attribute` pro Attribut, plus `_cache_online`.

## 5. Warum das Säen nichts sendet

`seed_from_snapshot` sendet beim Start bewusst nichts; der eine
`resend_all()`-Aufruf danach verschickt alles zusammen. `on_node_snapshot`
hält es genauso, aus einem zweiten Grund: ein frisch angelegtes Signal hat
in Loxone noch gar keinen virtuellen Eingang. Der entsteht erst, wenn die
Vorlage exportiert und in Loxone Config importiert wurde. Bis dahin ginge
jeder Versand ins Leere.

Der Nutzen des Säens liegt woanders und ist trotzdem der eigentliche Punkt
dieses Entwurfs: `_last_values` ist die Quelle für `last_values_for` — die
Oberfläche zeigt die Werte damit **sofort** statt Striche. Und die Werte
stehen für den nächsten `/resync` bereit, statt erst bei der nächsten
Änderung zu entstehen; ein Stecker ohne Last meldet nie eine sich ändernde
Spannung (die Lücke, die `seed_from_snapshot` am 2026-09-02 überhaupt
entstehen ließ).

`d<id>_online` bleibt davon unberührt: die Einlern-Route setzt es weiterhin
über `set_online` ([devices.py:373](../../../src/loxmatter/api/devices.py)),
und das sendet — ein Erreichbarkeits-Wechsel ist eine Nachricht an Loxone,
kein Startwert.

## 6. Was sich in der Oberfläche ändert

Der Satz „Live-Werte erscheinen erst nach einem Neustart der Brücke – bis
dahin zeigt das Gerät zwar ‚online', aber jedes Signal ‚-' (bekannte Grenze,
Spec 12.3)" entfällt aus der Erfolgsmeldung des Einlernens. Er beschrieb
eine Grenze, die es dann nicht mehr gibt; stehen zu lassen wäre schlimmer
als ihn nie gehabt zu haben.

Die bekannte Grenze im Modul-Docstring von `matter/client.py` wird nicht
gestrichen, sondern durch die Beschreibung dessen ersetzt, was jetzt
passiert — samt der Begründung aus Abschnitt 2, warum die Einlern-Route
trotz vorhandener Ereignisse selbst anstoßen muss. Das ist die Stelle, an
der die nächste Person danach sucht.

## 7. Ausdrücklich außerhalb des Zuschnitts

- **Geräte, die anderswo eingelernt wurden** (matter-server-Dashboard, Home
  Assistant). Sie stehen nicht im Store der Brücke; `resolve_device_id`
  liefert `None`, `follow_node` endet dort. Abonnements für sie anzulegen
  hätte keinen Empfänger.
- **Ein periodischer Abgleich** als Sicherheitsnetz gegen verpasste
  Ereignisse. Kann später dazukommen, wenn sich zeigt, dass Ereignisse
  ausbleiben — heute gibt es dafür keinen Beleg.
- **Signale, die verschwinden.** Ein Firmware-Update, das einen Cluster
  entfernt, hinterlässt eine verwaiste Signalzeile. Das ist heute schon so
  und ändert sich hier nicht.
- **Event-Abonnements** (`NODE_EVENT`) und Erreichbarkeit laufen weiterhin
  über je eine Wildcard-Subscription und brauchen kein Nachziehen — ihre
  `data` trägt die `node_id` selbst.

## 8. Prüfung

Neue Tests, jeder zuerst fehlschlagend:

**`tests/matter/`** — `follow_node` abonniert die Pfade eines Node, der bei
`subscribe()` noch nicht existierte, und ein danach eintreffendes
Attribut-Update erreicht den Handler. Ein zweiter Aufruf für denselben Node
legt **kein** zweites Abonnement an (sonst käme jeder Wert doppelt). Ein
Node mit neuen Pfaden bekommt Abonnements nur für die neuen. Ein Node ohne
neue Pfade löst keinen `on_node_snapshot`-Aufruf aus. `follow_node` vor
`subscribe()` tut nichts, statt zu werfen. Ein Node, den der Store nicht
kennt, wird abonniert, aber ohne Handler-Aufruf.

**`tests/loxone/`** — `on_node_snapshot` legt die Signalzeile eines neuen
Pfades an, verwirft den Signal-Cache und säet den Wert; ein Update auf den
neuen Pfad landet danach tatsächlich im Cache (der Test, der den vergessenen
`invalidate_index`-Aufruf fängt). Bestehende Signale behalten Schlüssel und
`exported`. Gesendet wird beim Säen nichts.

**`tests/api/`** — nach dem Einlernen ruft die Route `follow_node` mit der
Node-ID des neuen Geräts auf, und `GET /api/devices/{id}/signals` liefert
Werte statt `null`.

## 9. Offene Punkte

Keine. Die Annahmen über `NODE_UPDATED` (Abschnitt 2) und über die
Wiederaufruf-Sicherheit von `register_signals` (Abschnitt 4) sind gegen die
installierten Fassungen geprüft, nicht vermutet.
