# Live-Werte ohne Neustart: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Gerät, das nach dem Start der Brücke eingelernt wird — oder das nachträglich neue Attributpfade meldet —, bekommt seine Attribut-Abonnements und Startwerte ohne Neustart der Brücke.

**Architecture:** Eine neue Methode `BridgeMatterClient.follow_node(node_id)` zieht Abonnements nach: sie diffed die Pfade eines Node gegen die bereits abonnierten (Node, Pfad)-Paare, legt für die fehlenden je ein Abonnement an und reicht dem Handler das Abbild. Angestoßen wird sie von der Einlern-Route (nach `register_device`) und aus der Dispatch-Schleife bei `NODE_ADDED`/`NODE_UPDATED`. `Runtime` erfüllt den neuen Handler-Aufruf mit `register_signals` → `invalidate_index` → Werte säen.

**Tech Stack:** Python 3.12, asyncio, python-matter-server 8.1.2, FastAPI, pytest (`asyncio_mode = "auto"`), Alpine.js für die Oberfläche.

**Spec:** [2026-09-04-live-werte-neuer-geraete-design.md](../specs/2026-09-04-live-werte-neuer-geraete-design.md)

## Global Constraints

- Kommentare, Docstrings und Oberflächentexte auf Deutsch, im Stil der umgebenden Dateien: begründen, warum etwas so ist, nicht wiederholen, was der Code sagt.
- Quelltext-Dateien enthalten keine Umlaute in Bezeichnern oder Kommentaren, wo die Umgebung sie meidet (`ue`/`ae`/`oe`); Oberflächentexte in `index.html`/`app.js` dagegen sehr wohl.
- `uv run ruff format` auf jede berührte Datei; `uv run ruff check` darf keine **neuen** Funde bringen (4 `SIM118`-Funde in `tests/loxone/test_runtime.py` bestehen bereits und bleiben unangetastet).
- `uv run mypy` (strict, `files = ["src", "scripts"]`) muss sauber bleiben.
- Zeilenlänge 100 (`[tool.ruff] line-length = 100`).
- TDD: kein Produktionscode ohne zuvor fehlschlagenden Test.
- Jede Task endet mit einem eigenen Commit.

---

### Task 1: `Runtime.on_node_snapshot` — Signalzeilen, Cache, Werte

Der Empfänger des Nachziehens. Unabhängig von den Abonnements testbar, deshalb zuerst.

**Files:**
- Modify: `src/loxmatter/matter/client.py` (Protokoll `RuntimeEventHandler`, ab Zeile 134)
- Modify: `src/loxmatter/loxone/runtime.py` (neue Methode nach `seed_from_snapshot`)
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store.register_signals(device_id, snapshot)`, `Runtime.invalidate_index(device_id)`, `Runtime._cache_attribute(device_id, path, raw)`, `Runtime._cache_online(device_id, online)` — alle vorhanden.
- Produces: `RuntimeEventHandler.on_node_snapshot(device_id: int, snapshot: NodeSnapshot) -> None` (async) und `Runtime.on_node_snapshot` mit derselben Signatur. Task 2 ruft sie auf.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

An `tests/loxone/test_runtime.py` anhängen:

```python
async def test_on_node_snapshot_registers_a_new_signal_and_lets_it_through(
    environment, monkeypatch
):
    """Der Kern des Nachziehens: ein Pfad, den der Store beim Einlernen noch
    nicht kannte, muss danach eine Signalzeile haben UND durch den
    Signal-Cache der Laufzeit kommen. Genau hier faengt der Test den
    vergessenen `invalidate_index`-Aufruf - ohne ihn legt `register_signals`
    die Zeile zwar an, aber `_signal_for` bleibt bei seinem einmal geladenen
    Stand und jedes Update auf den neuen Pfad laeuft fuer den Rest des
    Prozesses ins Leere."""
    runtime, sender, _, device_id, _ = environment
    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # Erst indizieren lassen, wie im Betrieb: die Laufzeit hat das Geraet
    # schon einmal gesehen, bevor der neue Pfad auftaucht.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]


async def test_on_node_snapshot_seeds_the_values_without_sending(environment):
    """Dieselbe Begruendung wie bei `seed_from_snapshot`: der Cache fuellt
    sich, gesendet wird nichts. Ein frisch angelegtes Signal hat in Loxone
    ohnehin noch keinen virtuellen Eingang - der entsteht erst mit dem
    Export der Vorlage."""
    runtime, sender, _, device_id, _ = environment

    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    assert runtime._last_values[f"d{device_id}_online"] is True
    assert len(runtime._last_values) == 111
    assert sender.sent == []


async def test_on_node_snapshot_keeps_the_key_and_the_export_flag(environment):
    """`register_signals` ist ausdruecklich fuer erneute Aufrufe gebaut
    (siehe dortiger Docstring). Wuerde das Nachziehen Schluessel neu vergeben
    oder `exported` zuruecksetzen, zerstoerte jeder Wiederaufruf die
    Verdrahtung in der Loxone-Konfiguration."""
    runtime, _, store, device_id, _ = environment
    before = store.signals(device_id)[0]
    store.set_exported(before.key, not before.exported)
    expected = not before.exported

    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    after = store.signal_by_key(before.key)
    assert after is not None
    assert after.exported is expected


async def test_on_node_snapshot_seeds_an_unavailable_node_as_offline(environment):
    runtime, _, _, device_id, _ = environment
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    raw["available"] = False

    await runtime.on_node_snapshot(device_id, NodeSnapshot.from_raw(raw["node_id"], raw))

    assert runtime._last_values[f"d{device_id}_online"] is False
```

`_plug_snapshot`, `SignalRef`, `SignalKind`, `extract_signals`, `FIXTURES`, `json` und `NodeSnapshot` sind in dieser Datei bereits importiert bzw. definiert (siehe `test_invalidate_index_lets_a_newly_registered_signal_through` und `test_seed_from_snapshot_populates_cache_without_sending`). Nichts neu importieren.

- [ ] **Step 2: Den Test laufen lassen und den Fehlschlag sehen**

Run: `uv run pytest tests/loxone/test_runtime.py -q -k on_node_snapshot`
Expected: 4 FAILED mit `AttributeError: 'Runtime' object has no attribute 'on_node_snapshot'`

- [ ] **Step 3: Das Protokoll erweitern**

In `src/loxmatter/matter/client.py`, in `class RuntimeEventHandler(Protocol)`, nach `async def set_online(...)` einfügen:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None: ...
```

Und den Docstring der Klasse um einen Absatz ergaenzen:

```python
class RuntimeEventHandler(Protocol):
    """Was `subscribe()` von seinem Aufrufer braucht — `Runtime`
    (loxone/runtime.py) erfüllt das bereits unverändert, `_run()` kann sie
    also direkt als `handler` übergeben, ohne einen Adapter zu schreiben.

    `on_node_snapshot` kam mit dem Nachziehen der Abonnements dazu
    (`follow_node`): der Client sieht ein Gerät mit Pfaden, für die es noch
    keine Signalzeile gibt, und kann selbst nichts damit anfangen — er kennt
    den `Store` nicht und soll ihn nicht kennen. Der Handler dagegen hat
    ihn."""
```

- [ ] **Step 4: `Runtime.on_node_snapshot` schreiben**

In `src/loxmatter/loxone/runtime.py` direkt nach `seed_from_snapshot` einfügen:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        """Zieht ein Geraet nach, dessen Attributpfade sich geaendert haben -
        gerufen aus `BridgeMatterClient.follow_node`.

        Drei Schritte, deren Reihenfolge nicht beliebig ist:

        1. `register_signals` legt die Zeilen fuer neue Pfade an. Die Methode
           ist ausdruecklich fuer erneute Aufrufe gebaut (siehe dortiger
           Docstring): Schluessel und Titel bleiben, `exported` bleibt bei
           bekannten Signalen unangetastet, `unit`/`exportability`/
           `functional` werden nachgezogen.
        2. `invalidate_index` verwirft den Signal-Cache dieses Geraets.
           **Ohne diesen Schritt waere der ganze Vorgang wirkungslos**:
           `_signal_for` liest die Signale eines Geraets genau einmal und
           merkt sich das in `_indexed`; ein eben angelegtes Signal existierte
           dann in der Datenbank, aber jedes Update dazu liefe fuer den Rest
           des Prozesses ins Leere - ohne Fehler, nur mit einem
           `debug`-Eintrag. Der Docstring von `invalidate_index` verlangt
           diesen Aufruf seit Phase 4; dies ist sein erster Aufrufer.
        3. Werte saeen, ueber denselben `_cache_attribute`-Weg wie
           `seed_from_snapshot` - und aus demselben Grund: ein Stecker ohne
           Last meldet nie eine sich aendernde Spannung, sein Wert entstuende
           also sonst nie.

        Sendet selbst nichts, genau wie `seed_from_snapshot` (siehe dort).
        Zusaetzlicher Grund hier: ein frisch angelegtes Signal hat in Loxone
        noch gar keinen virtuellen Eingang - der entsteht erst, wenn die
        Vorlage exportiert und importiert wurde.
        """
        self._store.register_signals(device_id, snapshot)
        self.invalidate_index(device_id)
        self._cache_online(device_id, snapshot.available)
        for path, raw in snapshot.attributes.items():
            self._cache_attribute(device_id, path, raw)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/loxone/test_runtime.py -q`
Expected: alle PASS

Run: `uv run pytest -q`
Expected: alle PASS

- [ ] **Step 6: Formatieren, pruefen, committen**

```bash
uv run ruff format src/loxmatter/loxone/runtime.py src/loxmatter/matter/client.py tests/loxone/test_runtime.py
uv run mypy
git add src/loxmatter/loxone/runtime.py src/loxmatter/matter/client.py tests/loxone/test_runtime.py
git commit -m "feat(runtime): on_node_snapshot zieht Signalzeilen, Cache und Werte nach"
```

---

### Task 2: `BridgeMatterClient.follow_node` — Abonnements nachziehen

**Files:**
- Modify: `src/loxmatter/matter/client.py` (`__init__`, `disconnect`, `subscribe`, neue Methoden)
- Test: `tests/matter/test_client.py` (dort stehen die vorhandenen `subscribe()`-Tests samt `FakeNode`, `FakeUpstream`, `FakeHandler`, `make_connected_pair` und `_settle`)

**Interfaces:**
- Consumes: `RuntimeEventHandler.on_node_snapshot` aus Task 1.
- Produces: `BridgeMatterClient.follow_node(node_id: int) -> None` (async). Task 3 und Task 4 rufen sie auf.

- [ ] **Step 1: Die Attrappen erweitern und die fehlschlagenden Tests schreiben**

Alles in `tests/matter/test_client.py`.

Zuerst `FakeUpstream` um eine Methode ergaenzen, direkt nach `get_nodes`:

```python
    def add_node(self, node: FakeNode) -> None:
        """Ein Geraet, das erst nach start_listening() dazukommt - beim
        echten MatterClient fuellt das NODE_ADDED-Ereignis den Node-Cache
        entsprechend. Genau der Fall, den `follow_node` abdeckt."""
        self._nodes.append(node)
```

`FakeHandler` um den neuen Handler-Aufruf ergaenzen — im `__init__`:

```python
        self.snapshot_calls: list[tuple[int, NodeSnapshot]] = []
```

und als Methode:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        self.snapshot_calls.append((device_id, snapshot))
```

Dafuer `from loxmatter.matter.models import NodeSnapshot` ergaenzen, falls die Datei ihn noch nicht importiert.

`_settle()` von drei auf sechs Durchlaeufe heben, mit ergaenztem Docstring:

```python
async def _settle() -> None:
    """Lässt den Dispatch-Task von subscribe() der Queue hinterherlaufen —
    put_nowait() aus einem synchronen Callback und dessen Verarbeitung im
    Hintergrund-Task liegen sonst in verschiedenen Event-Loop-Durchläufen.

    Sechs Durchläufe statt drei, seit ein NODE_ADDED/NODE_UPDATED zwei
    Einträge erzeugt (Erreichbarkeit und Nachziehen) und das Nachziehen
    selbst noch einmal auf den Handler wartet."""
    for _ in range(6):
        await asyncio.sleep(0)
```

Und eine Lesehilfe bei den uebrigen Modul-Funktionen:

```python
def _attribute_subscriptions(upstream: FakeUpstream) -> list[str]:
    """Die Schluessel der aktiven Attribut-Abonnements, je einer pro
    (Node, Pfad), in der Form `attribute_updated/<node>/<pfad>`.

    Liest `_subscribers` der Attrappe absichtlich direkt: sie bildet damit
    exakt das Schluessel-Matching von `MatterClient._signal_event()` nach,
    und genau dieses Registrierungsschema soll hier geprueft werden."""
    prefix = f"{EventType.ATTRIBUTE_UPDATED.value}/"
    return sorted(
        key
        for key, callbacks in upstream._subscribers.items()
        if key.startswith(prefix) and callbacks
    )
```

Dann die Tests anhaengen:

```python
# --- follow_node() ----------------------------------------------------


async def test_follow_node_subscribes_a_node_that_did_not_exist_at_subscribe_time():
    """Der gemeldete Fall: ein Geraet, das erst nach `subscribe()` eingelernt
    wurde, hatte kein einziges Attribut-Abonnement - seine Signale standen
    bis zum naechsten Neustart der Bruecke auf "-"."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda node_id: {12: 5, 8: 9}.get(node_id), handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow_node(8)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=8, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(9, "1/6/0", False)]


async def test_follow_node_does_not_subscribe_the_same_path_twice():
    """Ein zweites Abonnement fuer denselben Pfad wuerde jeden Wert doppelt
    zustellen - `on_attribute` liefe zweimal, und bei einem Ereignissignal
    zaehlte der Zaehler doppelt hoch."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(12)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(5, "1/6/0", False)]


async def test_follow_node_only_subscribes_the_paths_that_are_new():
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    await bridge.subscribe(lambda _node_id: 5, FakeHandler())

    node.node_data.attributes["1/8/0"] = 254
    await bridge.follow_node(12)

    assert _attribute_subscriptions(upstream) == [
        "attribute_updated/12/1/6/0",
        "attribute_updated/12/1/8/0",
    ]


async def test_follow_node_without_new_paths_leaves_the_handler_alone():
    """Der Regelfall im Betrieb: `NODE_UPDATED` feuert auch bei einem Wechsel
    der Erreichbarkeit und nach jeder Re-Subscription. Fuer ein Geraet ohne
    neue Pfade ist der Diff leer, und der Vorgang endet vor dem Handler -
    sonst schriebe jede dieser Meldungen ueber hundert UPDATE-Anweisungen in
    die Datenbank."""
    bridge, _upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(12)

    assert handler.snapshot_calls == []


async def test_follow_node_hands_the_snapshot_to_the_handler():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda node_id: {8: 42}.get(node_id), handler)

    upstream.add_node(FakeNode(8, {"0/40/1": "IKEA of Sweden", "1/6/0": True}))
    await bridge.follow_node(8)

    assert [(device_id, snap.node_id) for device_id, snap in handler.snapshot_calls] == [(42, 8)]
    assert handler.snapshot_calls[0][1].vendor_name == "IKEA of Sweden"


async def test_follow_node_subscribes_even_when_the_store_does_not_know_the_node():
    """Die Abonnements entstehen trotzdem - nur der Handler bleibt aussen
    vor, weil es kein Geraet gibt, dem die Werte gehoerten. Sobald die
    Einlern-Route das Geraet registriert und erneut nachzieht, greift der
    Handler-Zweig."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: None, handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow_node(8)

    assert "attribute_updated/8/1/6/0" in _attribute_subscriptions(upstream)
    assert handler.snapshot_calls == []


async def test_follow_node_before_subscribe_does_nothing():
    """Kein Werfen: die Einlern-Route ruft `follow_node` bedingungslos auf,
    und ein Aufbau ohne Subscription soll daran nicht scheitern."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()

    await bridge.follow_node(12)

    assert _attribute_subscriptions(upstream) == []


async def test_follow_node_for_an_unknown_node_does_nothing():
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(999)

    assert handler.snapshot_calls == []
```

- [ ] **Step 2: Den Test laufen lassen und den Fehlschlag sehen**

Run: `uv run pytest tests/matter/ -q -k follow_node`
Expected: FAILED mit `AttributeError: 'BridgeMatterClient' object has no attribute 'follow_node'`

- [ ] **Step 3: Die Buchfuehrung anlegen**

In `BridgeMatterClient.__init__`, nach `self._thread_dataset_set = False`:

```python
        # subscribe()/follow_node()-Zustand. Die Menge der bereits angelegten
        # Attribut-Abonnements ist die einzige Quelle dafuer, was "neu" heisst
        # - ein zweites Abonnement fuer denselben (Node, Pfad) wuerde jeden
        # Wert doppelt zustellen. Queue, Handler und die device_id-Aufloesung
        # bleiben nach subscribe() erreichbar, weil follow_node sie braucht.
        self._subscribed_paths: set[tuple[int, str]] = set()
        self._queue: asyncio.Queue[_QueueItem] | None = None
        self._handler: RuntimeEventHandler | None = None
        self._resolve_device_id: Callable[[int], int | None] | None = None
```

In `disconnect()`, bei den uebrigen Rueckstellungen (direkt nach `self._unsubscribers = []`):

```python
        self._subscribed_paths = set()
        self._queue = None
        self._handler = None
        self._resolve_device_id = None
```

- [ ] **Step 4: Das Anlegen der Abonnements herausziehen**

In `client.py` eine neue private Methode ergaenzen, oberhalb von `subscribe`:

```python
    def _subscribe_attribute_paths(
        self,
        upstream: Any,
        queue: asyncio.Queue[_QueueItem],
        node_id: int,
        paths: Iterable[str],
    ) -> int:
        """Legt je ein Attribut-Abonnement pro noch nicht abonniertem
        (Node, Pfad)-Paar an und liefert deren Anzahl.

        Eine Stelle fuer beide Aufrufer (`subscribe` und `follow_node`): zwei
        Stellen, die dasselbe Registrierungsschema nachbilden, driften ueber
        kurz oder lang auseinander - und das faellt hier nicht auf, weil ein
        fehlendes Abonnement kein Fehler ist, sondern Stille.

        `queue` kommt als Parameter statt aus `self._queue`, damit `subscribe`
        sie uebergeben kann, bevor sie im Feld steht - und damit hier keine
        Nicht-`None`-Pruefung noetig ist, deren Einengung ueber die Closure
        unten ohnehin nicht traegt.
        """
        # Lazy importiert wie ueberall in dieser Datei: Tests mit einem
        # Fake-Upstream sollen matter_server nie laden muessen.
        from matter_server.common.models import EventType

        added = 0
        for path in paths:
            if (node_id, path) in self._subscribed_paths:
                continue

            # default-Argumente binden node_id/path pro Schleifendurchlauf,
            # statt den Namen aus dem umschliessenden Scope zu spaet
            # auszuwerten (klassische Closure-Falle in einer Schleife).
            def on_attribute_event(
                _event: Any, data: Any, node_id: int = node_id, path: str = path
            ) -> None:
                queue.put_nowait(_AttributeUpdate(node_id, path, data))

            self._unsubscribers.append(
                upstream.subscribe_events(
                    on_attribute_event,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=node_id,
                    attr_path_filter=path,
                )
            )
            self._subscribed_paths.add((node_id, path))
            added += 1
        return added
```

`Iterable` dazu importieren: `from collections.abc import Callable, Iterable`.

Dann in `subscribe()` die Attribut-Schleife ersetzen. Aus:

```python
        unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]

        # Attribut-Updates: siehe Modul-Docstring, warum das nur pro bekanntem
        # (Node, Pfad)-Paar geht. default-Argumente binden node_id/path pro
        # Schleifendurchlauf, statt den Namen aus dem umschließenden Scope zu
        # spät auszuwerten (klassische Closure-Falle in einer Schleife).
        for node in upstream.get_nodes():
            for path in node.node_data.attributes:

                def on_attribute_event(
                    _event: Any, data: Any, node_id: int = node.node_id, path: str = path
                ) -> None:
                    queue.put_nowait(_AttributeUpdate(node_id, path, data))

                unsubscribers.append(
                    upstream.subscribe_events(
                        on_attribute_event,
                        event_filter=EventType.ATTRIBUTE_UPDATED,
                        node_filter=node.node_id,
                        attr_path_filter=path,
                    )
                )

        self._unsubscribers = unsubscribers
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(queue, resolve_device_id, handler)
        )
```

wird:

```python
        self._unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]
        self._subscribed_paths = set()
        self._queue = queue
        self._handler = handler
        self._resolve_device_id = resolve_device_id

        # Attribut-Updates: siehe Modul-Docstring, warum das nur pro bekanntem
        # (Node, Pfad)-Paar geht. Was nach diesem Aufruf dazukommt, holt
        # `follow_node` nach.
        for node in upstream.get_nodes():
            self._subscribe_attribute_paths(
                upstream, queue, node.node_id, node.node_data.attributes
            )

        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(queue, resolve_device_id, handler)
        )
```

- [ ] **Step 5: `follow_node` schreiben**

Direkt nach `subscribe()` einfuegen:

```python
    async def follow_node(self, node_id: int) -> None:
        """Zieht die Attribut-Abonnements eines Node nach.

        Zwei Aufrufer, ein Vorgang: die Einlern-Route (`api/devices.py`) nach
        dem Registrieren eines neuen Geraets, und `_dispatch_loop` bei
        `NODE_ADDED`/`NODE_UPDATED` fuer ein Geraet, das nachtraeglich neue
        Pfade meldet.

        **Warum die Route nicht einfach auf das Ereignis warten kann:**
        matter-server meldet `NODE_ADDED` noch WAEHREND `commission_with_code`
        laeuft (`device_controller._setup_node` signalisiert es vor der
        Rueckkehr des Aufrufs). Zu diesem Zeitpunkt kennt der Store den Node
        noch nicht, `resolve_device_id` liefert `None`, und fuer ein ruhig im
        Netz stehendes Geraet folgt keine zweite Meldung. Am 2026-09-04 am
        laufenden Stack aufgezeichnet: Node 8 war um 11:15:33 fertig
        eingelernt, samt "Subscription succeeded" - alles davon vor der
        Rueckkehr an die Route.

        Der leere Diff ist der Regelfall und kostet nichts: `NODE_UPDATED`
        feuert auch bei jedem Wechsel der Erreichbarkeit und nach jeder
        Re-Subscription, und fuer ein Geraet ohne neue Pfade endet der
        Vorgang hier, bevor der Handler (und damit der Store) ueberhaupt
        angefasst wird.

        Vor `subscribe()` aufgerufen tut die Methode nichts, statt zu werfen:
        die Einlern-Route ruft sie bedingungslos, und ein Aufbau ohne
        Subscription soll daran nicht scheitern.
        """
        queue = self._queue
        handler = self._handler
        resolve_device_id = self._resolve_device_id
        if queue is None or handler is None or resolve_device_id is None:
            logger.debug(
                "follow_node(%s) ohne vorheriges subscribe() - nichts nachzuziehen", node_id
            )
            return

        upstream = self._require_upstream()
        node = next((n for n in upstream.get_nodes() if n.node_id == node_id), None)
        if node is None:
            logger.info(
                "Node %s ist matter-server nicht bekannt - keine Abonnements nachgezogen", node_id
            )
            return

        attributes = node.node_data.attributes
        if self._subscribe_attribute_paths(upstream, queue, node_id, attributes) == 0:
            return

        device_id = resolve_device_id(node_id)
        if device_id is None:
            # Die Abonnements bleiben bestehen: sobald die Einlern-Route das
            # Geraet registriert und erneut nachzieht, greift der Zweig unten.
            logger.debug("Node %s ist keinem Geraet zugeordnet - nur abonniert", node_id)
            return

        await handler.on_node_snapshot(
            device_id,
            NodeSnapshot.from_raw(
                node_id, {"attributes": attributes, "available": node.available}
            ),
        )
```

- [ ] **Step 6: Tests laufen lassen**

Run: `uv run pytest tests/matter/ -q`
Expected: alle PASS

Run: `uv run pytest -q`
Expected: alle PASS

- [ ] **Step 7: Formatieren, pruefen, committen**

```bash
uv run ruff format src/loxmatter/matter/client.py tests/matter/
uv run mypy
git add src/loxmatter/matter/client.py tests/matter/
git commit -m "feat(matter): follow_node zieht Attribut-Abonnements eines Node nach"
```

---

### Task 3: Die Dispatch-Schleife zieht bei `NODE_ADDED`/`NODE_UPDATED` nach

**Files:**
- Modify: `src/loxmatter/matter/client.py` (neuer Queue-Typ, `on_node_or_availability_event`, `_dispatch_loop`)
- Test: dieselbe Testdatei wie Task 2

**Interfaces:**
- Consumes: `BridgeMatterClient.follow_node` aus Task 2.
- Produces: nichts fuer spaetere Tasks.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

An `tests/matter/test_client.py` anhaengen:

```python
async def test_a_node_update_with_new_paths_is_followed_automatically():
    """Der zweite Fall der bekannten Grenze: ein laengst eingelerntes Geraet
    meldet nach einem Firmware-Update einen Pfad, den es beim Start noch
    nicht gab. `NODE_UPDATED` feuert bei matter-server genau dann, wenn ein
    Geraet neu interviewt wurde - der richtige Ausloeser."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    node.node_data.attributes["1/8/0"] = 254
    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert "attribute_updated/12/1/8/0" in _attribute_subscriptions(upstream)
    assert [device_id for device_id, _ in handler.snapshot_calls] == [5]


async def test_an_availability_update_without_new_paths_touches_no_handler():
    """Die haeufigste Ursache fuer `NODE_UPDATED` ueberhaupt. Sie darf keinen
    Store-Zugriff ausloesen - und muss die Erreichbarkeit trotzdem wie bisher
    zustellen."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert handler.snapshot_calls == []
    assert handler.availability_calls == [(5, True)]
```

- [ ] **Step 2: Den Test laufen lassen und den Fehlschlag sehen**

Run: `uv run pytest tests/matter/ -q -k "node_update"`
Expected: FAILED — `(8, "1/8/0") not in upstream.attribute_filters`

- [ ] **Step 3: Den Queue-Typ ergaenzen**

Bei den anderen `@dataclass(frozen=True)`-Definitionen in `client.py`:

```python
@dataclass(frozen=True)
class _FollowNode:
    """Anstoss zum Nachziehen der Abonnements eines Node.

    Laeuft ueber dieselbe Queue wie die Wert-Aktualisierungen, statt direkt
    aus dem synchronen Ereignis-Rueckruf heraus: `follow_node` ist eine
    Coroutine, und der Rueckruf kann keine erwarten (siehe
    `on_node_or_availability_event`).
    """

    node_id: int
```

Und die Union erweitern:

```python
_QueueItem = _AttributeUpdate | _EventUpdate | _AvailabilityUpdate | _FollowNode
```

- [ ] **Step 4: Das Ereignis einreihen**

In `subscribe()`, in `on_node_or_availability_event`, den `NODE_ADDED`/`NODE_UPDATED`-Zweig erweitern:

```python
            elif event in (EventType.NODE_ADDED, EventType.NODE_UPDATED):
                queue.put_nowait(_AvailabilityUpdate(data.node_id, data.available))
                # Zusaetzlich zum Erreichbarkeits-Update, nicht statt seiner:
                # beide Meldungen tragen dieselbe Ursache, aber der eine Weg
                # setzt `d<id>_online`, der andere zieht Abonnements nach.
                queue.put_nowait(_FollowNode(data.node_id))
```

- [ ] **Step 5: Die Dispatch-Schleife erweitern**

In `_dispatch_loop`, den Rumpf des `try` so beginnen lassen:

```python
            try:
                if isinstance(item, _FollowNode):
                    # VOR der device_id-Aufloesung: `follow_node` legt
                    # Abonnements auch fuer einen Node an, den der Store
                    # (noch) nicht kennt, und entscheidet selbst, ob der
                    # Handler etwas zu sehen bekommt.
                    await self.follow_node(item.node_id)
                    continue
                device_id = resolve_device_id(item.node_id)
```

Der Rest der Schleife bleibt unveraendert.

- [ ] **Step 6: Tests laufen lassen**

Run: `uv run pytest tests/matter/ -q`
Expected: alle PASS

Run: `uv run pytest -q`
Expected: alle PASS

- [ ] **Step 7: Formatieren, pruefen, committen**

```bash
uv run ruff format src/loxmatter/matter/client.py tests/matter/
uv run mypy
git add src/loxmatter/matter/client.py tests/matter/
git commit -m "feat(matter): NODE_ADDED/NODE_UPDATED ziehen neue Attributpfade nach"
```

---

### Task 4: Einlern-Route, Oberflaeche und die bekannte Grenze

**Files:**
- Modify: `src/loxmatter/api/devices.py` (`commission_device`, ab Zeile 295)
- Modify: `src/loxmatter/matter/client.py` (Modul-Docstring, Abschnitt „Bekannte Grenze")
- Modify: `src/loxmatter/web/app.js` (Erfolgsmeldung in `commissionDevice`)
- Test: `tests/api/test_devices.py`, `tests/api/conftest.py`

**Interfaces:**
- Consumes: `BridgeMatterClient.follow_node` aus Task 2.
- Produces: nichts.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

In `tests/api/conftest.py`, `FakeMatterClient.__init__` um eine Liste ergaenzen:

```python
        # Die Node-IDs, fuer die die Route das Nachziehen der Abonnements
        # angestossen hat (`BridgeMatterClient.follow_node`).
        self.followed: list[int] = []
```

und die Methode dazu, direkt nach `set_thread_dataset`:

```python
    async def follow_node(self, node_id: int) -> None:
        self.followed.append(node_id)
        self.order.append("follow")
```

In `tests/api/test_devices.py` anhaengen:

```python
async def test_commissioning_follows_the_new_node(api):
    """Ohne diesen Aufruf haette das frisch eingelernte Geraet kein einziges
    Attribut-Abonnement: `subscribe()` lief einmal beim Start der Bruecke,
    und das `NODE_ADDED` zu diesem Geraet kam nachweislich schon, bevor der
    Store ihm eine device_id geben konnte."""
    client, store, _, fake_client = api

    new_device = (await client.post("/api/devices/commission", json={"code": "MT:X"})).json()

    assert fake_client.followed == [store.device(new_device["id"]).node_id]


async def test_the_new_node_is_followed_only_after_it_is_registered(api):
    """Die Reihenfolge ist der ganze Grund fuer diesen Aufruf: wuerde die
    Route frueher nachziehen, liefe `resolve_device_id` erneut ins Leere -
    genau das Wettrennen, das `NODE_ADDED` schon verloren hat."""
    client, _, _, fake_client = api

    await client.post("/api/devices/commission", json={"code": "MT:X"})

    assert fake_client.order == ["commission", "follow"]
```

Der Entwurf (Abschnitt 8) nennt fuer diese Ebene zusaetzlich „`GET
/api/devices/{id}/signals` liefert Werte statt `null`". Das ist hier
bewusst NICHT nachgebaut: `FakeMatterClient` und `FakeRuntime` sind zwei
voneinander unabhaengige Attrappen, und der echte Weg vom Client zur
Runtime laeuft ueber `follow_node` → `handler.on_node_snapshot`. Ein Test,
der diesen Weg in den Attrappen nachbildet, pruefte die Attrappen, nicht
den Code. Die beiden Haelften sind stattdessen dort abgedeckt, wo sie
wirklich laufen: das Saeen der Werte in Task 1
(`test_on_node_snapshot_seeds_the_values_without_sending`), der Aufruf des
Handlers in Task 2 (`test_follow_node_hands_the_snapshot_to_the_handler`).
Die Verbindung der beiden prueft der Abschnitt „Pruefung am laufenden
Stack" am Ende dieses Plans.

- [ ] **Step 2: Den Test laufen lassen und den Fehlschlag sehen**

Run: `uv run pytest tests/api/test_devices.py -q -k follow`
Expected: 2 FAILED — `assert [] == [<node_id>]`

- [ ] **Step 3: Die Route nachziehen lassen**

In `src/loxmatter/api/devices.py`, in `commission_device`, direkt nach `await runtime.set_online(device_id, snapshot.available)`:

```python
        # Erst jetzt, nach `register_device`: `follow_node` loest die Node-ID
        # ueber den Store auf, und vorher gaebe es dort nichts aufzuloesen -
        # dasselbe Wettrennen, das `NODE_ADDED` bereits verloren hat (siehe
        # den Kommentar oben und den Docstring von `follow_node`). Legt die
        # Attribut-Abonnements fuer dieses Geraet an und saeet seine Werte,
        # damit die Signale sofort Zahlen zeigen statt Striche - frueher
        # brauchte es dafuer einen Neustart der Bruecke.
        await active_client.follow_node(snapshot.node_id)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/api/ -q`
Expected: alle PASS

- [ ] **Step 5: Die Erfolgsmeldung in der Oberflaeche berichtigen**

In `src/loxmatter/web/app.js`, in `commissionDevice`, den Kommentarblock und die Zuweisung ersetzen. Aus:

```javascript
        // Der Satz zur Subscription ist kein Schmuck (Review-Fix Fix 3,
        // 2026-09-03, siehe Spec 12.3): `BridgeMatterClient.subscribe()`
        // laeuft genau einmal beim Start der Bruecke und meldet nur die
        // damals bekannten (Node, Pfad)-Paare an. Ein gerade eingelerntes
        // Geraet geht ueber das NODE_ADDED-Ereignis sofort auf "online"
        // und erscheint gruen - bekommt aber bis zum naechsten Neustart
        // keinen einzigen Attributwert. Ohne diesen Hinweis sieht der
        // Anwender ein gruenes Geraet, dessen Signale alle auf "-" stehen,
        // und sucht den Fehler bei sich.
        this.commissionMessage =
          `${device.label} wurde eingelernt. Live-Werte erscheinen erst nach einem ` +
          "Neustart der Brücke – bis dahin zeigt das Gerät zwar „online“, aber jedes " +
          "Signal „-“ (bekannte Grenze, Spec 12.3). Der Export der Vorlagen " +
          "funktioniert davon unabhängig schon jetzt.";
```

wird:

```javascript
        // Der frühere Satz "Live-Werte erst nach einem Neustart der Brücke"
        // ist entfallen, weil die Grenze entfallen ist: die Einlern-Route
        // ruft `follow_node` auf, das die Attribut-Abonnements dieses Geräts
        // anlegt und seine Werte säet (Entwurf vom 2026-09-04). Einen Hinweis
        // braucht es hier trotzdem, nur einen anderen: dass die Werte im
        // Miniserver erst nach dem Export und dem Import in Loxone Config
        // ankommen, denn bis dahin gibt es dort keinen virtuellen Eingang.
        this.commissionMessage =
          `${device.label} wurde eingelernt und liefert ab sofort Live-Werte – ohne ` +
          "Neustart der Brücke. Im Miniserver kommen sie an, sobald Sie die Vorlagen " +
          "exportiert und in Loxone Config importiert haben.";
```

- [ ] **Step 6: Die bekannte Grenze im Modul-Docstring ersetzen**

In `src/loxmatter/matter/client.py`, im Modul-Docstring, den Absatz „Bekannte Grenze: …" ersetzen durch:

```
Was nach `subscribe()` dazukommt, holt `follow_node()` nach — ein Geraet,
das erst danach eingelernt wird, ebenso wie ein bekanntes Geraet, das
nachtraeglich neue Attributpfade meldet. Angestossen wird es aus der
Dispatch-Schleife bei `NODE_ADDED`/`NODE_UPDATED` und zusaetzlich von der
Einlern-Route. Das „zusaetzlich" ist nicht Guertel-und-Hosentraeger: das
`NODE_ADDED` eines gerade eingelernten Geraets kommt nachweislich, BEVOR
`commission_with_code` zurueckkehrt und der Store dem Node eine device_id
geben kann — die Meldung wird deshalb verworfen, und eine zweite folgt
fuer ein ruhig im Netz stehendes Geraet nicht. Siehe
docs/superpowers/specs/2026-09-04-live-werte-neuer-geraete-design.md.
```

- [ ] **Step 7: Alles laufen lassen und committen**

```bash
uv run pytest -q
uv run ruff format src/loxmatter/api/devices.py src/loxmatter/matter/client.py tests/api/
uv run ruff check src tests scripts
uv run mypy
git add -A
git commit -m "feat(api): Einlern-Route zieht die Abonnements des neuen Geraets nach"
```

Expected: `uv run pytest -q` meldet alle PASS; `uv run ruff check` meldet weiterhin genau die 4 vorbestehenden `SIM118`-Funde in `tests/loxone/test_runtime.py` und keine neuen; `uv run mypy` meldet `Success`.

---

## Abschluss: Prüfung am laufenden Stack

Nach Task 4, bevor die Arbeit als fertig gilt — die Testsuite kann keinen
echten `NODE_ADDED`-Zeitpunkt nachstellen:

- [ ] Auf dem Testhost ausrollen und ein Gerät einlernen.
- [ ] In der Oberfläche prüfen: das Gerät steht auf online **und** seine
      Signale zeigen Zahlen statt Striche, ohne Neustart der Brücke.
- [ ] `docker logs loxmatter` auf `Aktualisierung fuer unbekannte Node`
      durchsehen — ein solcher Eintrag zum neuen Node ist erwartbar (das
      verlorene `NODE_ADDED`), ein anhaltender Strom davon nicht.
