# Signal-Rangliste und Signal-Modal — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Signale werden nach Bedeutung sortiert statt nach Endpunktnummer, und das Signal-Modal wird eine fluchtende Tabelle mit Endpunkt-Gruppen.

**Architecture:** Eine Cluster-Rangliste als `rank:` in `profiles/clusters.yaml` liefert den ersten Teil des Sortierschlüssels; `Store.signals` sortiert damit in Python nach, weil SQLite den Rang nicht kennt. Dadurch ändern sich Kachel, Modal *und* die Loxone-Vorlage aus einer Quelle. Die Oberfläche bekommt dazu drei neue Felder in `SignalOut` (`endpoint`, `cluster_id`, `endpoint_label`), eine Batterie-Fußzeile auf der Kachel und ein Rasterlayout im Modal.

**Tech Stack:** Python 3.12, SQLite, FastAPI/Pydantic, PyYAML, Alpine.js (kein Build-Schritt), pytest.

## Global Constraints

- **Entwurf:** `docs/superpowers/specs/2026-09-07-signal-ranking-and-modal-design.md`. Abschnittsnummern in diesem Plan verweisen darauf.
- **Sprache:** Docstrings, Kommentare und Commit-Botschaften auf **Deutsch**, dicht und begründend (warum, nicht nur was). Der GPL-Kopf jeder Quelldatei bleibt in der englischen FSF-Fassung.
- **Umlaute in Python-Kommentaren:** die bestehenden Dateien schreiben `ue`/`ae`/`oe` statt Umlauten (`ueberhaupt`, `Geraet`). Diese Schreibweise beibehalten. In Markdown und in `strings.yaml`-Werten stehen echte Umlaute.
- **Laufzeittexte gehen durch `i18n`:** jeder neue sichtbare Text bekommt ein `en`/`de`-Paar in `src/loxmatter/i18n/strings.yaml`. Kein fest verdrahteter deutscher Text in `app.js` oder `index.html`.
- **`strings.yaml`-Werte dürfen nicht in typografische Anführungszeichen gefasst sein** — `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` sperrt das.
- **Schlüssel sind unantastbar.** `d4_1_press` bleibt `d4_1_press`. Keine Aufgabe in diesem Plan ändert `signal.key`.
- **Tests laufen mit** `uv run pytest`. Einzelne Datei: `uv run pytest tests/profiles/test_table.py -v`.
- **Vor jedem Commit:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`.

## Dateistruktur

| Datei | Zuständigkeit | Aufgabe |
| --- | --- | --- |
| `src/loxmatter/profiles/clusters.yaml` | trägt zusätzlich `rank:` je Cluster | 1 |
| `src/loxmatter/profiles/table.py` | `rank_for()` und `DEFAULT_RANK` | 1 |
| `src/loxmatter/model/store.py` | `signals()` sortiert nach Rang | 2 |
| `src/loxmatter/profiles/endpoints.py` | **neu** — Gerätetyp → sprechender Endpunktname | 4 |
| `src/loxmatter/api/models.py` | `SignalOut` bekommt `endpoint`, `cluster_id`, `endpoint_label` | 4 |
| `src/loxmatter/api/devices.py` | füllt die drei Felder | 4 |
| `src/loxmatter/web/app.js` | Batteriezeile, Endpunktgruppen, Zeilen-Aufklapper, Kopfzahl | 5–9 |
| `src/loxmatter/web/index.html` | Kachel-Fußzeile, Modal-Raster, Batteriesymbol | 5–10 |
| `src/loxmatter/web/style.css` | `.device-battery`, `.signal-grid`, Umbruch unter 640 px | 5, 7, 10 |
| `src/loxmatter/i18n/strings.yaml` | alle neuen Texte | 4–9 |

`profiles/endpoints.py` steht bewusst **neben** `categories.py` statt darin: `categories.py` beantwortet „was für ein Ding ist das ganze Gerät", `endpoints.py` „wie heißt dieser eine Endpunkt darin". Eine Fernbedienung ist *ein* Schalter mit *zwei* Tasten — dieselbe Tabelle für beide Fragen wäre falsch (Entwurf 7.4).

---

### Task 1: Die Cluster-Rangliste

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py:44-100`
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `loxmatter.profiles.table.rank_for(cluster_id: int) -> int` und `loxmatter.profiles.table.DEFAULT_RANK: int` (Wert 50).

- [ ] **Step 1: Die failing tests schreiben**

An `tests/profiles/test_table.py` anhängen:

```python
def test_a_cluster_with_a_rank_reports_it():
    """Der Rang entscheidet, was auf der Kachel als Leitwert erscheint -
    er muss deshalb aus der Tabelle kommen und nicht aus einer Annahme."""
    assert table.rank_for(6) == 10  # OnOff
    assert table.rank_for(59) == 10  # Switch
    assert table.rank_for(47) == 90  # PowerSource


def test_a_cluster_without_a_rank_gets_the_default():
    """Cluster 3 (Identify) steht nicht in der Tabelle. Er darf weder vorn
    landen noch hinter der Batterie: die Vorgabe ist die Mitte, damit ein
    neuer Geraetetyp nie versehentlich mit seinem Batteriestand fuehrt und
    sein Hauptmerkmal trotzdem vor Verwaltungsangaben steht (Entwurf 4)."""
    assert table.rank_for(3) == table.DEFAULT_RANK
    assert table.DEFAULT_RANK == 50


def test_the_utility_clusters_rank_behind_everything_functional():
    """Die eine Regel, wegen der dieser Entwurf ueberhaupt entstand."""
    functional = [table.rank_for(c) for c in (6, 8, 59, 144, 145, 768, 1026, 1029)]
    assert max(functional) < table.rank_for(47)
    assert table.rank_for(47) < table.rank_for(40)


def test_every_rank_in_the_table_is_an_integer():
    """Ein `rank: "10"` aus einem Tippfehler waere in YAML eine Zeichenkette
    und wuerde beim Sortieren gegen eine Zahl werfen - erst zur Laufzeit,
    beim Oeffnen einer Geraeteansicht."""
    for cluster_id, cluster in table._table().items():
        if "rank" in cluster:
            assert isinstance(cluster["rank"], int), cluster_id
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/profiles/test_table.py -k rank -v`
Expected: FAIL mit `AttributeError: module 'loxmatter.profiles.table' has no attribute 'rank_for'`

- [ ] **Step 3: `rank:` in `clusters.yaml` eintragen**

In jeden der zehn Cluster-Blöcke eine `rank:`-Zeile direkt unter `name:` einfügen. Über dem `clusters:`-Schlüssel diesen Kommentar ergänzen:

```yaml
# `rank` ordnet die Cluster ZUEINANDER, wenn die Oberflaeche und der Export
# die Signale eines Geraets sortieren (Entwurf 2026-09-07, Abschnitt 4).
# Kleiner Rang zuerst: was ein Geraet im Haus TUT, steht bei 10-40; was es
# ueber sich selbst aussagt, bei 90+. Ein Cluster ohne `rank` bekommt 50 und
# landet damit in der Mitte - hinter dem, was nachweislich zaehlt, aber vor
# Batterie und Geraeteangaben. Diese Vorgabe ist Absicht: ein Geraetetyp,
# den noch niemand eingetragen hat, soll nie mit seinem Batteriestand
# fuehren, sein Hauptmerkmal aber auch nicht hinter Bekanntes verbannt
# bekommen.
#
# INNERHALB eines Rangs bleibt die bisherige Ordnung (Endpunkt, Cluster,
# Element, Art) - sie ordnet zwei Signale desselben Clusters, und das tut
# sie gut. Es gibt bewusst KEINEN Rang je Element: die Element-IDs sind in
# der Matter-Spezifikation bereits grob nach Wichtigkeit vergeben, eine
# zweite Rangebene waere Aufwand ohne belegten Gewinn.
```

Die Werte:

| Cluster | `rank` |
| --- | --- |
| 6 (OnOff) | 10 |
| 59 (Switch) | 10 |
| 1026 (TemperatureMeasurement) | 10 |
| 1029 (RelativeHumidityMeasurement) | 10 |
| 8 (LevelControl) | 20 |
| 768 (ColorControl) | 30 |
| 144 (ElectricalPowerMeasurement) | 40 |
| 145 (ElectricalEnergyMeasurement) | 40 |
| 47 (PowerSource) | 90 |
| 40 (BasicInformation) | 95 |

Beispiel für den ersten Block:

```yaml
clusters:
  6:
    name: onoff
    rank: 10
    attributes:
      0: {slug: onoff, unit: ""}
```

Falls Cluster 40 (BasicInformation) heute keinen eigenen Block hat, einen anlegen — er braucht weder `attributes:` noch `commands:`, nur Name und Rang:

```yaml
  40:
    name: basicinformation
    # Kein `attributes:`-Abschnitt: dieser Cluster steht hier ALLEIN wegen
    # seines Rangs. Ein leerer `attributes:`-Abschnitt haette eine zweite,
    # ungewollte Wirkung - `relevance.is_functional` liest ihn ueber
    # `known_attribute_section` und wuerde dann JEDES Attribut dieses
    # Clusters als nicht gewollt verwerfen (siehe dessen Docstring,
    # Schicht 3). Der Rang allein aendert nur die Reihenfolge.
    rank: 95
```

- [ ] **Step 4: `rank_for` in `table.py` schreiben**

Nach `_table()` (Zeile 99) einfügen:

```python
# Der Rang eines Clusters, den die Tabelle nicht fuehrt (Entwurf
# 2026-09-07, Abschnitt 4). Die Mitte, nicht das Ende: ein unbekannter
# Cluster soll nie hinter dem Batteriestand landen, aber auch nicht vor
# einem Cluster, dessen Bedeutung belegt ist.
DEFAULT_RANK = 50


def rank_for(cluster_id: int) -> int:
    """Wie wichtig dieser Cluster fuer die Anzeige ist - kleiner ist wichtiger.

    Getrennt von `lookup` und `knows_cluster`, weil diese Frage eine andere
    ist als "wie heisst das Element" oder "kennt die Tabelle den Cluster":
    ein Cluster kann in der Tabelle stehen (wegen seiner Kommandos) und
    trotzdem keinen Rang tragen. Beide Faelle - gar nicht in der Tabelle,
    und in der Tabelle ohne `rank` - ergeben hier dieselbe Antwort, weil
    sie fuer die Sortierung dasselbe bedeuten.
    """
    cluster = _table().get(cluster_id)
    if cluster is None:
        return DEFAULT_RANK
    rank = cluster.get("rank")
    return DEFAULT_RANK if rank is None else int(rank)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: PASS, alle Tests der Datei (die bestehenden dürfen nicht brechen).

- [ ] **Step 6: Prüfen, dass sonst nichts kaputtging**

Run: `uv run pytest tests/profiles tests/export -v`
Expected: PASS. `clusters.yaml` wird von `lookup`, `names_element` und `extract_commands` gelesen — eine zusätzliche Schlüssel-Zeile darf dort nichts ändern.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/profiles/table.py tests/profiles/test_table.py
git commit -m "$(cat <<'MSG'
feat(profiles): Cluster-Rangliste fuer die Signalreihenfolge

`rank:` je Cluster in clusters.yaml, gelesen ueber `rank_for()`. Noch
ohne Wirkung - Aufgabe 2 haengt die Sortierung daran.

Die Vorgabe fuer einen Cluster ohne Rang ist bewusst 50 und nicht 99: ein
Geraetetyp, den noch niemand eingetragen hat, soll nie mit seinem
Batteriestand fuehren (Rang 90), sein tatsaechliches Hauptmerkmal aber
auch nicht hinter allem Bekannten verschwinden.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 2: `Store.signals` sortiert nach Rang

**Files:**
- Modify: `src/loxmatter/model/store.py:1304-1310`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `table.rank_for` aus Task 1.
- Produces: `Store.signals(device_id)` liefert nach `(rank, endpoint, cluster_id, element_id, kind)` sortiert. Signatur und Rückgabetyp unverändert (`list[StoredSignal]`).

- [ ] **Step 1: Die failing tests schreiben**

An `tests/model/test_store.py` anhängen (die Datei hat bereits `load_snapshot` aus `conftest` und legt einen `Store` in `tmp_path` an — dem dortigen Muster folgen):

```python
def test_the_button_leads_with_a_switch_signal_not_the_battery(tmp_path):
    """Der Befund, wegen dessen dieser Entwurf entstand: PowerSource sitzt
    auf Endpunkt 0, das Nutz-Cluster auf Endpunkt 1 - nach Endpunktnummer
    sortiert gewinnt damit bei JEDEM batteriebetriebenen Geraet die
    Batterie."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].ref.cluster_id == 59
    assert functional[-1].ref.cluster_id == 47


def test_the_plug_still_leads_with_onoff(tmp_path):
    """Die beiden heute richtigen Geraete duerfen sich nicht verstellen."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].ref.cluster_id == 6
    assert functional[0].title == "onoff"


def test_signals_of_the_same_cluster_keep_the_previous_order(tmp_path):
    """Die Rangliste ordnet nur die CLUSTER zueinander. Innerhalb eines
    Clusters bleibt Endpunkt/Element - dort ist die alte Ordnung richtig."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    switch = [s for s in store.signals(device_id) if s.ref.cluster_id == 59]
    keys = [(s.ref.endpoint, s.ref.element_id, s.ref.kind.value) for s in switch]

    assert keys == sorted(keys)


def test_the_order_is_total_and_stable(tmp_path):
    """Zwei Aufrufe muessen dieselbe Reihenfolge liefern - der Export
    schreibt sie in eine Datei, ein Flattern waere dort ein Diff ohne
    Aenderung."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    assert [s.key for s in store.signals(device_id)] == [
        s.key for s in store.signals(device_id)
    ]
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/model/test_store.py -k "button_leads or same_cluster" -v`
Expected: FAIL — `functional[0].ref.cluster_id` ist 47, nicht 59.

- [ ] **Step 3: Sortierung einbauen**

In `store.py` den Import ergänzen (die Datei importiert bereits `Exportability` und `is_exportable` aus `profiles.table`):

```python
from loxmatter.profiles.table import Exportability, is_exportable, rank_for
```

Vor der `Store`-Klasse (bei den anderen Modulfunktionen wie `_normalized_room`) einfügen:

```python
def _signal_order(signal: StoredSignal) -> tuple[int, int, int, int, str]:
    """Der Sortierschluessel der Signalliste (Entwurf 2026-09-07, Abschnitt 4).

    Der Rang steht VOR dem Endpunkt, und genau darin liegt die ganze
    Aenderung: Matter traegt PowerSource auf Endpunkt 0, das Nutz-Cluster
    aber auf Endpunkt 1 oder 2 - nach Endpunktnummer sortiert fuehrte
    deshalb jedes batteriebetriebene Geraet mit seinem Batteriestand.

    Sortiert wird in Python und nicht in SQL, weil der Rang aus
    `clusters.yaml` kommt: SQLite kennt ihn nicht, und ihn als Spalte in
    `signal` zu spiegeln hiesse, ihn bei jeder Aenderung der YAML-Datei
    nachtragen zu muessen - eine zweite Wahrheit fuer denselben Wert.

    Die vier hinteren Glieder sind der bisherige Schluessel. Er ist wegen
    der UNIQUE-Bedingung auf `signal` bereits eindeutig, damit ist auch
    dieser Schluessel total - die Reihenfolge flattert nie, was fuer den
    Export wichtig ist (er schreibt sie in eine Datei).
    """
    return (
        rank_for(signal.ref.cluster_id),
        signal.ref.endpoint,
        signal.ref.cluster_id,
        signal.ref.element_id,
        signal.ref.kind.value,
    )
```

`Store.signals` ersetzen:

```python
    def signals(self, device_id: int) -> list[StoredSignal]:
        """Alle Signale eines Geraets, nach Bedeutung sortiert.

        Das `ORDER BY` bleibt stehen, obwohl `_signal_order` es
        ueberschreibt: es haelt die Zeilenfolge schon vor dem Sortieren
        fest und macht damit einen Fehler in `_signal_order` sichtbar,
        statt ihn hinter einer zufaelligen SQLite-Reihenfolge zu
        verstecken.

        Diese Reihenfolge traegt weiter als die Oberflaeche: `to_inputs`
        in `api.export` schreibt sie unveraendert in die VIU-Vorlage
        (Entwurf 2026-09-07, Abschnitt 5). Der Projektdatei-Sync gleicht
        dagegen ueber den Schluessel ab (`projectsync.diff._plan_inputs`),
        nicht ueber die Position - eine geaenderte Reihenfolge erzeugt
        dort keine Scheinaenderungen.
        """
        rows = self._db.execute(
            "SELECT * FROM signal WHERE device_id = ?"
            " ORDER BY endpoint, cluster_id, element_id, kind",
            (device_id,),
        ).fetchall()
        return sorted((self._as_signal(r) for r in rows), key=_signal_order)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/model -v`
Expected: PASS.

- [ ] **Step 5: Die ganze Testreihe laufen lassen**

Run: `uv run pytest -q`
Expected: PASS. Schlägt hier etwas fehl, ist es ein Test, der die alte Reihenfolge festschreibt — **nicht** einfach die Erwartung umschreiben, sondern prüfen, ob der Test die Reihenfolge zufällig oder absichtlich prüft, und das Ergebnis in Task 3 festhalten.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store.py
git commit -m "$(cat <<'MSG'
feat(store): Signale nach Cluster-Rang statt nach Endpunktnummer sortieren

Der Rang steht vor dem Endpunkt, und darin liegt die ganze Aenderung:
Matter traegt PowerSource auf Endpunkt 0, das Nutz-Cluster auf Endpunkt 1
oder 2 - nach Endpunktnummer sortiert fuehrte jedes batteriebetriebene
Geraet mit seinem Batteriestand. Am Taster gemessen: `battery` (0/47/12)
stand vor allen sechzehn Switch-Signalen.

Sortiert wird in Python, nicht in SQL: der Rang kommt aus clusters.yaml,
SQLite kennt ihn nicht, und ihn als Spalte zu spiegeln hiesse, eine
zweite Wahrheit fuer denselben Wert zu pflegen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 3: Export und Projektdatei-Sync gegen die neue Ordnung absichern

Diese Aufgabe schreibt **keinen** Produktionscode. Sie hält fest, was Abschnitt 5 des Entwurfs als geprüft behauptet — damit die Behauptung nicht nur im Entwurf steht.

**Files:**
- Test: `tests/export/test_signals.py`
- Test: `tests/projectsync/test_diff.py`

**Interfaces:**
- Consumes: `Store.signals` aus Task 2, `export.signals.to_inputs`, `projectsync.diff.build_plan`.
- Produces: nichts.

- [ ] **Step 1: Den Export-Test schreiben**

An `tests/export/test_signals.py` anhängen:

```python
def test_the_template_lists_the_button_press_before_the_battery(tmp_path):
    """Die VIU-Vorlage erbt die Reihenfolge aus `Store.signals`
    (api/export.py ruft `to_inputs(store.signals(...))`). Im Loxone-Baum
    steht seit der Rangliste der Tastendruck oben und die Batterie unten -
    das ist Absicht, kein Nebeneffekt, und gehoert deshalb festgehalten."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    inputs = to_inputs(store.signals(device_id), device_id, "Taster")
    keys = [i.key for i in inputs]

    assert keys.index(f"d{device_id}_1_press") < keys.index(f"d{device_id}_0_battery")
```

- [ ] **Step 2: Den Rückwärtskompatibilitäts-Test schreiben**

An `tests/projectsync/test_diff.py` anhängen. Dem dortigen Muster für den Aufbau einer Projektdatei folgen; entscheidend ist, dass die Eingänge in der **alten** Reihenfolge (Endpunkt vor Rang, also Batterie zuerst) in der Projektdatei stehen:

```python
def test_a_project_imported_before_the_reordering_still_matches(tmp_path):
    """Eine Projektdatei, die ein Anwender VOR der Rangliste importiert hat,
    fuehrt ihre Eingaenge in einer anderen Reihenfolge. `_plan_inputs` schlaegt
    jeden Eintrag ueber `index.input_cmds.get(entry.key)` nach, nicht ueber
    seine Position - kein Eintrag darf deshalb als neu gelten und keiner
    als verwaist.

    Ohne diesen Test waere die Zusicherung aus Abschnitt 5 des Entwurfs
    eine Behauptung. Sie ist der einzige Grund, warum die Reihenfolge an
    der Quelle geaendert werden durfte statt nur in der Anzeige."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    inputs = to_inputs(store.signals(device_id), device_id, "Taster")
    # EINE andere Reihenfolge als die heutige - welche, ist gleichgueltig:
    # der Abgleich laeuft ueber den Schluessel, also darf ihn KEINE
    # Umsortierung stoeren. Alphabetisch nach Schluessel ist eine beliebige
    # Permutation und beweist damit mehr als die eine alte Ordnung.
    shuffled = sorted(inputs, key=lambda i: i.key)
    project = _project_with_inputs(shuffled)  # Helfer dieser Testdatei

    plan = build_plan(project, store)

    statuses = {e.key: e.status for e in plan.entries if e.kind == "input"}
    assert PlanStatus.NEW not in statuses.values()
    assert PlanStatus.ORPHANED not in statuses.values()
```

Falls `_project_with_inputs` in dieser Datei noch nicht existiert: den bestehenden Aufbau der Nachbartests wiederverwenden und als Helfer herausziehen — **nicht** eine zweite Kopie anlegen.

- [ ] **Step 3: Tests laufen lassen**

Run: `uv run pytest tests/export tests/projectsync -v`
Expected: PASS. Beide Tests sollten **sofort** grün sein — sie prüfen eine Eigenschaft, die Task 2 bereits hergestellt hat. Ein Fehlschlag beim Sync-Test bedeutet, dass die Zusicherung aus Abschnitt 5 nicht trägt: dann **hier anhalten** und melden, statt den Test anzupassen.

- [ ] **Step 4: Commit**

```bash
git add tests/export/test_signals.py tests/projectsync/test_diff.py
git commit -m "$(cat <<'MSG'
test(export): die Zusicherungen der Rangliste festhalten

Zwei Tests zu Abschnitt 5 des Entwurfs. Der erste haelt fest, dass die
VIU-Vorlage die neue Ordnung erbt (Tastendruck vor Batterie im
Loxone-Baum) - das ist gewollt und kein Nebeneffekt.

Der zweite ist der wichtigere: eine Projektdatei, die vor der Umstellung
importiert wurde, fuehrt ihre Eingaenge in der alten Reihenfolge. Weil
`_plan_inputs` ueber den Schluessel abgleicht und nicht ueber die
Position, gilt dort kein Eintrag als neu oder verwaist. Genau diese
Eigenschaft war der Grund, die Reihenfolge an der Quelle aendern zu
duerfen statt nur in der Anzeige - bis hierher war sie eine Behauptung.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 4: `endpoint`, `cluster_id` und `endpoint_label` in `SignalOut`

**Files:**
- Create: `src/loxmatter/profiles/endpoints.py`
- Modify: `src/loxmatter/api/models.py:29-59`
- Modify: `src/loxmatter/api/devices.py` (`_signal_out` und seine Aufrufstelle)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/profiles/test_endpoints.py` (neu), `tests/api/test_devices.py`

**Interfaces:**
- Consumes: `StoredDevice.device_types: dict[int, frozenset[int]] | None`, `StoredSignal.ref`.
- Produces:
  - `loxmatter.profiles.endpoints.ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str]`
  - `loxmatter.profiles.endpoints.endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]` — Endpunktnummer → fertiger, übersetzter Name.
  - `SignalOut` trägt zusätzlich `endpoint: int`, `cluster_id: int`, `endpoint_label: str`.

- [ ] **Step 1: Den failing test für `endpoints.py` schreiben**

Neue Datei `tests/profiles/test_endpoints.py` (GPL-Kopf wie in den Nachbardateien):

```python
from loxmatter import i18n
from loxmatter.profiles import endpoints


def test_two_button_endpoints_are_numbered():
    """Der Fall, wegen dessen es dieses Modul gibt: die Fernbedienung traegt
    denselben Geraetetyp auf zwei Endpunkten. Ohne Nummerierung stuenden im
    Modal zwei Gruppen namens "Taste", und `press` waere weiter zweimal
    dasselbe Wort ohne Auskunft, welche Taste gemeint ist."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels(
        {0: frozenset({0x0016, 0x0011}), 1: frozenset({0x000F}), 2: frozenset({0x000F})}
    )
    assert labels[1] == "Taste 1"
    assert labels[2] == "Taste 2"


def test_a_single_endpoint_of_a_type_is_not_numbered():
    """Eine Steckdose hat genau einen Nutz-Endpunkt. "Steckdose 1" waere
    eine Nummer ohne Gegenstueck."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016}), 1: frozenset({0x010A})})
    assert labels[1] == "Steckdose"


def test_a_utility_endpoint_is_called_the_device():
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016, 0x0011})})
    assert labels[0] == "Gerät"


def test_an_unmapped_type_falls_back_to_the_endpoint_number():
    """Die Tabelle ist bewusst klein und deckt nur belegte Geraetetypen ab.
    Alles andere bekommt einen Namen, der immer stimmt."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({3: frozenset({0x0302})})
    assert labels[3] == "Endpunkt 3"


def test_device_types_never_backfilled_fall_back_for_every_endpoint():
    """`device.device_types` ist `NULL`, solange `backfill_device_types`
    nicht lief (siehe `_migrate_to_v7`). Das ist kein Fehlerfall, sondern
    derselbe Ruecktritt wie bei `category_for(None)`."""
    i18n.set_language("de")
    assert endpoint_label_of(None, 1) == "Endpunkt 1"


def endpoint_label_of(device_types, endpoint):
    return endpoints.endpoint_labels(device_types).get(endpoint) or i18n.t(
        "web.signals.endpoint_plain", endpoint=endpoint
    )


def test_every_mapped_type_exists_in_the_matter_table():
    """Dieselbe Absicherung wie `test_categories.py` sie fuer
    CATEGORY_BY_DEVICE_TYPE hat: eine Nummer aus dem Gedaechtnis statt aus
    der Spezifikation faellt sonst nie auf."""
    from matter_server.client.models import device_types as matter_types

    known = {t.device_type for t in matter_types.ALL_TYPES.values()}
    for device_type in endpoints.ENDPOINT_NAME_KEY_BY_DEVICE_TYPE:
        assert device_type in known, hex(device_type)
```

**Hinweis:** Wie `ALL_TYPES` in der installierten `matter_server`-Fassung genau heißt, steht in `tests/profiles/test_categories.py::test_every_mapped_type_exists_in_the_matter_table`. Von dort abschreiben statt raten.

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/profiles/test_endpoints.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.profiles.endpoints'`

- [ ] **Step 3: Die Texte in `strings.yaml` anlegen**

```yaml
web.signals.endpoint_button:
  en: "Button"
  de: "Taste"
web.signals.endpoint_socket:
  en: "Socket"
  de: "Steckdose"
web.signals.endpoint_light:
  en: "Light"
  de: "Leuchte"
web.signals.endpoint_metering:
  en: "Metering"
  de: "Messung"
web.signals.endpoint_device:
  en: "Device"
  de: "Gerät"
web.signals.endpoint_numbered:
  en: "{name} {index}"
  de: "{name} {index}"
web.signals.endpoint_plain:
  en: "Endpoint {endpoint}"
  de: "Endpunkt {endpoint}"
```

- [ ] **Step 4: `endpoints.py` schreiben**

```python
# <GPL-Kopf wie in profiles/categories.py, unveraendert uebernehmen>

"""Der sprechende Name EINES Endpunkts (Entwurf 2026-09-07, Abschnitt 7.4).

Steht neben `categories.py`, nicht darin, und das ist die ganze
Begruendung dieses Moduls: `category_for` beantwortet "was fuer ein Ding
ist das GERAET" - Leuchte, Steckdose, Schalter -, diese Datei "wie heisst
dieser eine Endpunkt DARIN". Eine Fernbedienung ist EIN Schalter mit ZWEI
Tasten; `CATEGORY_BY_DEVICE_TYPE` auf ihre Endpunkte angewandt ergaebe
"Schalter 1" und "Schalter 2", also zweimal denselben falschen Begriff.

Die Tabelle unten ist bewusst KLEIN. Sie fuehrt die Geraetetypen, die an
den eingecheckten Abbildern in tests/fixtures/nodes/ tatsaechlich
vorkommen, und sonst nichts - derselbe Anspruch wie bei
`UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`: ein neuer Eintrag
braucht eine konkrete Belegung, nicht die Annahme, die Tabelle sei von
sich aus vollstaendig. Alles Uebrige faellt auf "Endpunkt N" zurueck, und
das ist ein Name, der immer stimmt.
"""

from __future__ import annotations

from collections.abc import Mapping

from loxmatter import i18n
from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES

# Geraetetyp -> Uebersetzungsschluessel. Die Nummern stammen aus
# `matter_server.client.models.device_types` wie in `categories.py`; die
# Kommentare nennen den dortigen Klassennamen.
ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str] = {
    0x000F: "web.signals.endpoint_button",  # GenericSwitch (IKEA BILRESA, Ep 1+2)
    0x010A: "web.signals.endpoint_socket",  # OnOffPlugInUnit (IKEA GRILLPLATS, Ep 1)
    0x010D: "web.signals.endpoint_light",  # ExtendedColorLight (synthetic_color_light, Ep 1)
    0x0510: "web.signals.endpoint_metering",  # ElectricalSensor (GRILLPLATS, Ep 2)
}

# Ein Endpunkt, der nur Verwaltung traegt, heisst schlicht "Geraet" - dort
# sitzt der Batteriestand, und "Endpunkt 0" waere fuer den Bedienenden eine
# Zahl ohne Bedeutung. PowerSource zaehlt hier mit, weil er allein noch
# keinen Nutz-Endpunkt macht (dieselbe Ueberlegung wie
# `_IGNORED_DEVICE_TYPES` in categories.py).
_DEVICE_ENDPOINT_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}


def _name_key(declared: frozenset[int]) -> str | None:
    """Der Schluessel fuer diesen Endpunkt, oder `None` fuer den Ruecktritt.

    Ein Nutz-Typ schlaegt den Verwaltungs-Typ: Endpunkt 0 der Fernbedienung
    deklariert Root Node UND Power Source UND OTA Requestor - er heisst
    "Geraet". Traegt ein Endpunkt dagegen beides, Verwaltung und einen
    benannten Nutz-Typ, gewinnt der Nutz-Typ, weil er mehr aussagt.
    """
    for device_type in sorted(declared):
        key = ENDPOINT_NAME_KEY_BY_DEVICE_TYPE.get(device_type)
        if key is not None:
            return key
    if declared & _DEVICE_ENDPOINT_TYPES:
        return "web.signals.endpoint_device"
    return None


def endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]:
    """Endpunktnummer -> fertiger, uebersetzter Name.

    Nummeriert wird nur, wo es etwas zu unterscheiden gibt: zwei
    Taster-Endpunkte ergeben "Taste 1" und "Taste 2", ein einzelner
    Steckdosen-Endpunkt bleibt "Steckdose" - eine "1" ohne "2" ist eine
    Nummer ohne Gegenstueck.

    `None` (Geraetetypen noch nicht nachgetragen, siehe
    `Store.backfill_device_types`) ergibt eine leere Zuordnung; der
    Aufrufer faellt dann fuer jeden Endpunkt auf `endpoint_plain` zurueck -
    dieselbe wortlose Behandlung, die `category_for(None)` mit `OTHER`
    bekommt.
    """
    if not device_types:
        return {}

    keys: dict[int, str] = {}
    for endpoint, declared in device_types.items():
        key = _name_key(declared)
        if key is not None:
            keys[endpoint] = key

    counts: dict[str, int] = {}
    for key in keys.values():
        counts[key] = counts.get(key, 0) + 1

    labels: dict[int, str] = {}
    seen: dict[str, int] = {}
    for endpoint in sorted(keys):
        key = keys[endpoint]
        name = i18n.t(key)
        if counts[key] == 1:
            labels[endpoint] = name
            continue
        seen[key] = seen.get(key, 0) + 1
        labels[endpoint] = i18n.t("web.signals.endpoint_numbered", name=name, index=seen[key])
    return labels
```

- [ ] **Step 5: Test laufen lassen**

Run: `uv run pytest tests/profiles/test_endpoints.py -v`
Expected: PASS.

- [ ] **Step 6: Den failing test für `SignalOut` schreiben**

An `tests/api/test_devices.py` anhängen (dem dortigen `api`-Fixture-Muster folgen; nötigenfalls ein zweites Fixture mit dem Taster statt der Steckdose anlegen):

```python
async def test_a_signal_carries_its_endpoint_cluster_and_endpoint_label(button_api):
    """Die Oberflaeche gruppiert nach Endpunkt und erkennt den Batteriestand
    an seinem Cluster. Beides aus `path` ("1/59/2") in JavaScript
    herauszuparsen hiesse, die Zerlegung ein zweites Mal zu pflegen -
    deshalb liefert die API die Zahlen fertig."""
    client, store, device_id = button_api

    response = await client.get(f"/api/devices/{device_id}/signals")

    signals = response.json()
    battery = next(s for s in signals if s["key"].endswith("_0_battery"))
    assert battery["endpoint"] == 0
    assert battery["cluster_id"] == 47
    assert battery["endpoint_label"] == "Device"

    press = next(s for s in signals if s["key"].endswith("_1_press"))
    assert press["endpoint"] == 1
    assert press["endpoint_label"] == "Button 1"
```

- [ ] **Step 7: Test laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_devices.py -k endpoint_label -v`
Expected: FAIL mit `KeyError: 'endpoint'`

- [ ] **Step 8: `SignalOut` erweitern**

In `api/models.py` nach `kind: str` einfügen:

```python
    # endpoint/cluster_id (Entwurf 2026-09-07, Abschnitt 7.4): `path` traegt
    # dieselben Zahlen als "1/59/2", aber als Text. Die Oberflaeche
    # gruppiert nach Endpunkt und erkennt den Batteriestand an Cluster 47 -
    # beides aus `path` zu parsen hiesse, `matter.paths` ein zweites Mal in
    # JavaScript zu pflegen. `endpoint_label` ist der sprechende Name
    # desselben Endpunkts ("Taste 1"), uebersetzt aus `profiles.endpoints`;
    # ohne nachgetragene Geraetetypen steht dort "Endpunkt 1".
    endpoint: int
    cluster_id: int
    endpoint_label: str
```

- [ ] **Step 9: `api/devices.py` füllen**

Import ergänzen:

```python
from loxmatter.profiles.endpoints import endpoint_labels
```

`_signal_out` bekommt die Zuordnung als Parameter (statt sie je Signal neu zu berechnen — bei 173 Signalen wäre das 173-mal dieselbe Rechnung):

```python
def _signal_out(
    signal: StoredSignal,
    value: float | bool | str | None,
    labels: dict[int, str],
) -> SignalOut:
    ...
    return SignalOut(
        ...
        endpoint=signal.ref.endpoint,
        cluster_id=signal.ref.cluster_id,
        endpoint_label=labels.get(
            signal.ref.endpoint,
            i18n.t("web.signals.endpoint_plain", endpoint=signal.ref.endpoint),
        ),
        ...
    )
```

An der Aufrufstelle einmal je Gerät:

```python
    labels = endpoint_labels(device.device_types)
    return [_signal_out(s, values.get(s.key), labels) for s in store.signals(device.id)]
```

Die vorhandene Signatur und Aufrufstelle vor dem Umbau lesen — sie kann von der obigen Skizze abweichen; die Änderung ist in jedem Fall: `labels` einmal je Gerät bilden und durchreichen.

- [ ] **Step 10: Tests laufen lassen**

Run: `uv run pytest tests/api tests/profiles -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/profiles/endpoints.py src/loxmatter/api/models.py src/loxmatter/api/devices.py src/loxmatter/i18n/strings.yaml tests/profiles/test_endpoints.py tests/api/test_devices.py
git commit -m "$(cat <<'MSG'
feat(api): Endpunkt, Cluster und sprechender Endpunktname je Signal

`profiles/endpoints.py` beantwortet "wie heisst dieser eine Endpunkt",
was `categories.py` NICHT beantwortet: dort geht es um das ganze Geraet.
Eine Fernbedienung ist EIN Schalter mit ZWEI Tasten - die Kategorie auf
ihre Endpunkte angewandt ergaebe zweimal "Schalter".

Nummeriert wird nur, wo es etwas zu unterscheiden gibt: zwei
Taster-Endpunkte werden "Taste 1"/"Taste 2", ein einzelner
Steckdosen-Endpunkt bleibt "Steckdose". Nicht eingetragene Typen und ein
noch nicht nachgetragenes `device.device_types` fallen auf "Endpunkt N"
zurueck - ein Name, der immer stimmt.

`endpoint`/`cluster_id` in SignalOut, weil `path` dieselben Zahlen nur
als Text traegt: sie in JavaScript herauszuparsen hiesse, `matter.paths`
ein zweites Mal zu pflegen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 5: Die Kachel — Batteriezeile und richtiger Zähler

**Files:**
- Modify: `src/loxmatter/web/app.js:1064-1079`
- Modify: `src/loxmatter/web/index.html` (Symbolblock um Zeile 170, Kachel um Zeile 665-690)
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.cluster_id` und `signal.functional` aus Task 4.
- Produces: `batterySignalFor(deviceId)`, `previewSignalsFor(deviceId)` in `app.js`; `leadSignalFor`/`restSignalsFor`/`remainingSignalCount` behalten Namen und Signatur.

- [ ] **Step 1: Die failing tests schreiben**

An `tests/api/test_web.py` anhängen:

```python
@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_battery_is_never_the_lead_and_never_counted_twice():
    """Die drei Zusicherungen der Batteriezeile an EINEM Aufbau, weil sie
    zusammengehoeren: der Batteriestand fuehrt nicht, er steht nicht in der
    Vorschau, und er zaehlt nicht als "weiteres".

    Der Aufbau ist der Taster: 17 funktionale Signale in der Reihenfolge,
    in der die Cluster-Rangliste sie liefert - sechzehn Switch-Signale,
    zuletzt die Batterie. Sechs Vorschauzeilen plus eine Fusszeile lassen
    zehn uebrig. Nennt die Kachel elf, ist die Batterie doppelt gezaehlt -
    genau der Fehler, den der Canvas-Entwurf hatte.

    Als node-Lauf statt als Zeichenketten-Suche in `app.js`: eine Suche
    belegt nur, DASS eine Zeile ausgeliefert wird. Am 2026-09-05 haben drei
    solche Tests einen Critical durchgelassen, weil sie exakt die
    Zeichenketten prueften, die den Fehler erzeugten."""
    values = _app_state(
        """
        const signals = [];
        for (let i = 0; i < 16; i++) {
          signals.push({
            key: "d1_1_s" + i, title: "s" + i,
            endpoint: 1, cluster_id: 59, functional: true,
          });
        }
        signals.push({
          key: "d1_0_battery", title: "battery",
          endpoint: 0, cluster_id: 47, functional: true,
        });
        state.signalsByDevice = { 1: signals };
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1).key,
          battery: state.batterySignalFor(1).key,
          preview: state.firstSignalsFor(1).map((s) => s.key),
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["lead"] == "d1_1_s0"
    assert values["battery"] == "d1_0_battery"
    assert "d1_0_battery" not in values["preview"]
    assert len(values["preview"]) == 6
    assert values["remaining"] == 10


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_mains_powered_device_has_no_battery_row():
    """Ohne PowerSource-Signal darf die Kachel keine Fusszeile zeigen - und
    der Zaehler muss sich genauso verhalten wie vor dieser Aenderung."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_1_onoff", title: "onoff", endpoint: 1, cluster_id: 6, functional: true },
          { key: "d1_2_power", title: "power", endpoint: 2, cluster_id: 144, functional: true },
        ] };
        console.log(JSON.stringify({
          battery: state.batterySignalFor(1),
          lead: state.leadSignalFor(1).key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["battery"] is None
    assert values["lead"] == "d1_1_onoff"
    assert values["remaining"] == 0


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_whose_only_functional_signal_is_the_battery_has_no_lead():
    """Der Randfall, an dem der Hinweis "keine funktionalen Signale" falsch
    waere: es GIBT eines, es steht nur in der Fusszeile."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_battery", title: "battery", endpoint: 0, cluster_id: 47, functional: true },
        ] };
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1),
          battery: state.batterySignalFor(1).key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["lead"] is None
    assert values["battery"] == "d1_0_battery"
    assert values["remaining"] == 0


async def test_the_tile_has_a_battery_row_with_its_own_symbol(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'id="i-battery"' in page
    assert "device-battery" in page
    assert 'x-show="batterySignalFor(device.id)"' in page


async def test_the_no_functional_signals_hint_accounts_for_the_battery(api):
    """Ein Geraet, dessen einziges funktionales Signal die Batterie ist, hat
    keinen Leitwert - aber der Satz "keine funktionalen Signale" waere dort
    falsch, denn die Fusszeile zeigt eines."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    hint = page[page.index("no_functional_signals") - 400 : page.index("no_functional_signals")]
    assert "!batterySignalFor(device.id)" in hint
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k battery -v`
Expected: FAIL — `previewSignalsFor` steht nirgends.

- [ ] **Step 3: Den Text anlegen**

In `strings.yaml`:

```yaml
web.devices.battery_label:
  en: "Battery"
  de: "Batterie"
```

- [ ] **Step 4: `app.js` umbauen**

`functionalSignalsFor` bleibt unverändert. Direkt darunter einfügen und `firstSignalsFor`/`remainingSignalCount` ersetzen:

```js
    // Der Cluster, an dem die Kachel den Batteriestand erkennt. Die Zahl
    // steht hier statt einer Titel-Pruefung: der Titel ist vom Nutzer frei
    // umbenennbar ("Akku", "Saft"), der Cluster nicht.
    POWER_SOURCE_CLUSTER: 47,

    // Der Batteriestand des Geraets, oder null. Er bekommt seit der
    // Cluster-Rangliste (Entwurf 2026-09-07, Abschnitt 6) eine eigene
    // Fusszeile: mit Rang 90 steht er hinter allen sechzehn anderen
    // funktionalen Signalen des Tasters und fiele damit aus den sechs
    // Vorschauzeilen heraus - er waere auf der Kachel gar nicht mehr zu
    // sehen. Das ist der Preis der Rangliste, und dies ist die Gegenbuchung.
    batterySignalFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      if (!signals) {
        return null;
      }
      return (
        signals.find(
          (signal) => signal.functional && signal.cluster_id === this.POWER_SOURCE_CLUSTER,
        ) || null
      );
    },

    // Die funktionalen Signale OHNE den Batteriestand - die Menge, aus der
    // sich Leitwert, Vorschauzeilen und der "+ N weitere"-Zaehler bilden.
    //
    // Dass alle drei aus DERSELBEN Menge kommen, ist der ganze Trick: der
    // Leitwert kann damit nie die Batterie sein (sie ist gar nicht drin),
    // und der Zaehler kann sie nie doppelt zaehlen (sie fehlt in beiden
    // Summanden). Eine Sonderregel an drei Stellen waere dieselbe Aussage
    // dreimal - und beim ersten Entwurf ist genau eine davon vergessen
    // worden.
    previewSignalsFor(deviceId) {
      const battery = this.batterySignalFor(deviceId);
      const functional = this.functionalSignalsFor(deviceId);
      return battery ? functional.filter((signal) => signal.key !== battery.key) : functional;
    },

    firstSignalsFor(deviceId) {
      return this.previewSignalsFor(deviceId).slice(0, this.FUNCTIONAL_PREVIEW_LIMIT);
    },

    remainingSignalCount(deviceId) {
      return Math.max(
        0,
        this.previewSignalsFor(deviceId).length - this.FUNCTIONAL_PREVIEW_LIMIT,
      );
    },
```

- [ ] **Step 5: Das Symbol in `index.html` anlegen**

Zum Symbolblock (neben `i-kebab`, um Zeile 170):

```html
      <symbol id="i-battery" viewBox="0 0 24 24">
        <rect x="2" y="8" width="16" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.9"/>
        <path d="M21 11v3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>
        <path d="M5 12.5h4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>
      </symbol>
```

- [ ] **Step 6: Die Fußzeile in die Kachel setzen**

Unmittelbar **nach** dem schließenden `</div>` von `.value-rows` und **vor** `<p class="hint" x-show="!signalsByDevice[device.id]" …>`:

```html
                  <!-- Der Batteriestand steht UNTER den Vorschauzeilen und
                       unter dem "+ N weitere"-Link, nicht zwischen ihnen
                       (Entwurf 2026-09-07, Abschnitt 6). Er ist kein
                       Nutzsignal wie ein Tastendruck, sondern eine Angabe
                       ueber das Geraet selbst - und mit Rang 90 waere er
                       aus den sechs Vorschauzeilen gefallen. Eigene Zeile,
                       eigenes Symbol, gestrichelte Trennung: immer
                       sichtbar, nie Leitwert.

                       `x-show` statt `x-if`: ein netzbetriebenes Geraet
                       hat kein PowerSource-Signal und darf nicht um eine
                       leere Zeile hoeher werden - `.device-battery` traegt
                       deshalb kein eigenes Aussenmass, das stehen bliebe. -->
                  <div class="device-battery" x-show="batterySignalFor(device.id)" x-cloak>
                    <svg class="icon" aria-hidden="true"><use href="#i-battery"></use></svg>
                    <span class="value-key" x-text="t('web.devices.battery_label')"></span>
                    <span
                      class="value"
                      :class="{ 'value-fresh': signalIsFresh(batterySignalFor(device.id)) }"
                      :title="signalAgeTitle(batterySignalFor(device.id))"
                      x-text="formatValue(liveValueOf(batterySignalFor(device.id))) + (batterySignalFor(device.id)?.unit ? ' ' + batterySignalFor(device.id).unit : '')"
                    ></span>
                  </div>
```

- [ ] **Step 7: Die Hinweisbedingung nachziehen**

Am `<p class="hint" … x-text="t('web.devices.no_functional_signals')">` die Bedingung ergänzen:

```html
                  <p
                    class="hint"
                    x-show="signalsByDevice[device.id] && !leadSignalFor(device.id) && !batterySignalFor(device.id)"
                    x-text="t('web.devices.no_functional_signals')"
                  ></p>
```

Und den bestehenden Kommentar darüber um einen Satz ergänzen:

```
                       Seit der Batteriezeile (Entwurf 2026-09-07) reicht
                       "kein Leitwert" als Bedingung nicht mehr: ein Geraet,
                       dessen einziges funktionales Signal die Batterie ist,
                       hat keinen Leitwert - aber "keine funktionalen
                       Signale" waere dort falsch, die Fusszeile darunter
                       zeigt ja eines.
```

- [ ] **Step 8: `style.css` ergänzen**

Nach `.value-rows .value` einfügen:

```css
/* Der Batteriestand als eigene Zeile am Fuss der Vorschau (Entwurf
   2026-09-07, Abschnitt 6). Gestrichelt abgesetzt wie `.device-controls`
   im Modal - dieselbe Geste fuer dasselbe: "gehoert dazu, ist aber eine
   andere Art von Angabe".

   `margin-top` steht auf dem Element, nicht als `margin-bottom` der
   Vorschau: ein netzbetriebenes Geraet blendet diese Zeile per `x-show`
   aus, und ein Aussenmass am Nachbarn bliebe dann als Luecke stehen. */
.device-battery {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  margin-top: 0.45rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--border);
}

.device-battery .icon {
  width: 0.85rem;
  height: 0.85rem;
  color: var(--warn);
  align-self: center;
  flex: 0 0 auto;
}

.device-battery .value-key {
  flex: 1 1 auto;
}

/* Rechtsbuendig wie in `.value-rows`, damit die Zahl mit den Werten
   darueber fluchtet - die Zeile ist ein Flex-Container und erbt deren
   Rasterausrichtung nicht. */
.device-battery .value {
  flex: 0 0 auto;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--warn);
}
```

- [ ] **Step 9: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 10: Die Bindung im Harness belegen, nicht nur die Funktion**

Der node-Test aus Step 1 belegt, dass `batterySignalFor` das richtige Signal
liefert. Er sagt **nichts** darüber, ob die Zeile im Browser erscheint: `x-show`
auf einem Element, dessen Ausdruck nie greift, verpufft stillschweigend. Genau
diese Lücke hat am 2026-09-05 einen Critical durchgelassen.

**Wie der Harness gebaut wird** (bewährt am 2026-09-06, spart Anmeldung und
Runden): eine `harness.html` im Scratchpad, die den fraglichen Markup-Block
per Python **aus `index.html` herausschneidet** — nicht abtippen, sonst prüft
man eine Kopie. Daneben `style.css` und `vendor/alpine.min.js` kopieren und
ein Mini-`app()` stellen, das nur die Felder trägt, die der Block anfasst.
Davor `python3 -m http.server`. Zwei Fallen: `file://` lädt der eingebettete
Browser als statischen Schnappschuss, Alpine läuft dort **gar nicht** — es
muss über http gehen; und ein Tab, der einmal eine lokale Datei gezeigt hat,
bleibt darauf festgenagelt, also einen neuen Tab für die http-URL öffnen.

Im Harness dann **gemessene** Werte lesen, nicht Augenschein:

```js
const row = document.querySelector(".device-battery");
JSON.stringify({
  vorhanden: !!row,
  sichtbar: row && getComputedStyle(row).display !== "none",
  text: row && row.textContent.replace(/\s+/g, " ").trim(),
  leitwert: document.querySelector(".lead-label").textContent,
  rest: document.querySelector(".value-rows a.value-key").textContent,
})
```

Erwartet: `sichtbar` true, `text` enthält „Batterie" und „12.4", `leitwert`
ist `press`, `rest` nennt **10** weitere.

Danach denselben Harness mit einem Gerät **ohne** PowerSource-Signal laden:
`vorhanden` true (das Element steht im DOM), `sichtbar` false — und die
Kachelhöhe darf sich gegenüber dem Stand vor dieser Aufgabe nicht ändern
(`document.querySelector(".device-card").getBoundingClientRect().height`
vorher/nachher vergleichen).

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Batteriestand als eigene Kachelzeile statt als Leitwert

Die Gegenbuchung zur Cluster-Rangliste: mit Rang 90 steht der
Batteriestand hinter allen sechzehn anderen funktionalen Signalen des
Tasters und fiele damit aus den sechs Vorschauzeilen - er waere auf der
Kachel gar nicht mehr zu sehen. Er bekommt deshalb eine eigene, immer
sichtbare Fusszeile mit eigenem Symbol.

Leitwert, Vorschauzeilen und der "+ N weitere"-Zaehler bilden sich alle
drei aus `previewSignalsFor`, das die Batterie herausnimmt. Dadurch kann
der Leitwert sie nie sein und der Zaehler sie nie doppelt zaehlen - eine
Sonderregel an drei Stellen waere dieselbe Aussage dreimal, und beim
Entwurf ist genau eine davon schon einmal vergessen worden ("+ 11
weitere" auf einer Kachel, die sieben von 17 Signalen zeigt).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 6: Das Modal — Endpunkt-Gruppen

**Files:**
- Modify: `src/loxmatter/web/app.js:1446-1451`
- Modify: `src/loxmatter/web/index.html` (die `<summary>` der Signalgruppe)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.endpoint`, `signal.endpoint_label` aus Task 4.
- Produces: `signalGroupsFor(deviceId)` liefert Gruppen der Form `{key, title, subtitle, collapsible, signals}` — `subtitle` ist neu, die vier übrigen Felder behalten Bedeutung und Typ.

**Abweichung vom Entwurf, bewusst:** Abschnitt 7.4 zeichnet die Untertitel als „Endpunkt 1 · Switch (59)". Das trägt nicht — Endpunkt 2 der Steckdose führt die Cluster 144 **und** 145, ein einzelner Clustername wäre dort falsch. Der Untertitel ist deshalb nur „Endpunkt N". Der Cluster steht ab Task 8 je Zeile im Aufklapper, wo er hingehört.

- [ ] **Step 1: Die failing tests schreiben**

```python
@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_groups_follow_the_ranking_not_the_endpoint_number():
    """Der Grund fuer die Gruppen: `press` steht zweimal in der Liste -
    1/59/1 und 2/59/1, also zwei verschiedene Tasten derselben
    Fernbedienung. Ohne Gruppe ist das zweimal dasselbe Wort ohne Auskunft,
    welche gemeint ist.

    Und die Reihenfolge: "Geraet" (Endpunkt 0, nur die Batterie) steht
    ZULETZT, obwohl es die kleinste Endpunktnummer traegt - die Gruppen
    uebernehmen die Reihenfolge des ersten Auftretens in der bereits
    gerangten Liste, sie sortieren nicht selbst. Genau das kann eine
    Zeichenketten-Suche in `app.js` nicht belegen.

    `t()` liefert ohne geladene Uebersetzungstabelle den Schluessel selbst
    zurueck (siehe `t` in app.js) - der Titel der Experte-Gruppe ist hier
    deshalb der Schluessel, und das genuegt fuer die Zusicherung."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_1_press", title: "press", endpoint: 1, cluster_id: 59,
            functional: true, endpoint_label: "Taste 1" },
          { key: "d1_2_press", title: "press", endpoint: 2, cluster_id: 59,
            functional: true, endpoint_label: "Taste 2" },
          { key: "d1_0_battery", title: "battery", endpoint: 0, cluster_id: 47,
            functional: true, endpoint_label: "Gerät" },
          { key: "d1_0_vendor", title: "VendorName", endpoint: 0, cluster_id: 40,
            functional: false, endpoint_label: "Gerät" },
        ] };
        console.log(JSON.stringify(
          state.signalGroupsFor(1).map((g) => ({
            key: g.key, title: g.title, collapsible: g.collapsible,
            signals: g.signals.map((s) => s.key),
          }))
        ));
        """
    )

    assert [g["key"] for g in values] == ["ep1", "ep2", "ep0", "expert"]
    assert [g["title"] for g in values[:3]] == ["Taste 1", "Taste 2", "Gerät"]
    assert values[0]["signals"] == ["d1_1_press"]
    assert values[1]["signals"] == ["d1_2_press"]
    # 156 Signale ueber alle Endpunkte zu gliedern erzeugte nur mehr
    # Ueberschriften - der Experte-Block bleibt EINE zugeklappte Gruppe.
    assert values[3]["signals"] == ["d1_0_vendor"]
    assert values[3]["collapsible"] is True
    assert [g["collapsible"] for g in values[:3]] == [False, False, False]


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_without_functional_signals_yields_only_the_expert_group():
    """Der Zustand, fuer den der Hinweis `none_functional` jetzt AUSSERHALB
    der Gruppenschleife steht: eine Endpunktgruppe ist nie leer, es gibt
    dann schlicht keine."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_vendor", title: "VendorName", endpoint: 0, cluster_id: 40,
            functional: false, endpoint_label: "Gerät" },
        ] };
        console.log(JSON.stringify(state.signalGroupsFor(1).map((g) => g.key)));
        """
    )

    assert values == ["expert"]


async def test_the_group_header_shows_the_endpoint_as_a_subtitle(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'x-text="group.subtitle"' in page
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k "groups_signals or subtitle" -v`
Expected: FAIL.

- [ ] **Step 3: Den Text anlegen**

```yaml
web.signals.group_endpoint_subtitle:
  en: "Endpoint {endpoint}"
  de: "Endpunkt {endpoint}"
```

- [ ] **Step 4: `signalGroupsFor` ersetzen**

```js
    // Die Gruppen des Signal-Modals: je Endpunkt eine, danach der
    // Experte-Block (Entwurf 2026-09-07, Abschnitt 7.4).
    //
    // Die Reihenfolge der Endpunktgruppen folgt der Cluster-Rangliste, ohne
    // dass hier sortiert wuerde: `functionalSignalsFor` kommt bereits
    // sortiert an, und diese Schleife uebernimmt die Reihenfolge des ERSTEN
    // Auftretens jedes Endpunkts. Am Taster steht "Geraet" (nur Batterie)
    // deshalb zuletzt, obwohl es Endpunkt 0 ist.
    //
    // `group.key` bleibt stabil ueber Neuzeichnungen ("ep1", "expert") -
    // das ist Voraussetzung fuer das `x-init="$el.open = !group.collapsible"`
    // im Markup: waere der Schluessel unstabil, baute Alpine den Knoten neu
    // auf und klappte eine geoeffnete Gruppe wortlos wieder zu.
    signalGroupsFor(deviceId) {
      const groups = [];
      const byEndpoint = new Map();
      for (const signal of this.functionalSignalsFor(deviceId)) {
        let group = byEndpoint.get(signal.endpoint);
        if (!group) {
          group = {
            key: "ep" + signal.endpoint,
            title: signal.endpoint_label,
            subtitle: t("web.signals.group_endpoint_subtitle", { endpoint: signal.endpoint }),
            collapsible: false,
            signals: [],
          };
          byEndpoint.set(signal.endpoint, group);
          groups.push(group);
        }
        group.signals.push(signal);
      }
      // Bleibt EINE Gruppe: 156 Signale ueber alle Endpunkte zu gliedern
      // erzeugte nur mehr Ueberschriften, keine Uebersicht.
      groups.push({
        key: "expert",
        title: t("web.signals.group_expert"),
        subtitle: "",
        collapsible: true,
        signals: this.expertSignalsFor(deviceId),
      });
      return groups;
    },
```

**Achtung: zwei bestehende Tests brechen dadurch** — sie schreiben fest,
dass `signalGroupsFor` die Gruppe „Funktional" führt, und genau die
verschwindet:

- `tests/api/test_web.py:402` (`assert 't("web.signals.group_functional")' in script`)
- `tests/api/test_web.py:1852` (`assert 'title: t("web.signals.group_functional")' in body`)

Beide sind aus Aufgabe 12 der i18n-Umstellung und belegen dort etwas
Richtiges: dass die Gruppentitel übersetzt sind statt fest verdrahtet. Diese
Zusicherung bleibt gültig und muss erhalten bleiben — sie zielt nur auf einen
Titel, den es nicht mehr gibt. Also **umschreiben, nicht löschen**: beide
Tests prüfen künftig `t("web.signals.group_expert")` und den
Endpunkt-Untertitel `t("web.signals.group_endpoint_subtitle", ...)`, und
behalten ihre Sperre gegen feste Literale (`'"Funktional"' not in body`
entfällt, `'"Experte"' not in body` bleibt).

Der Schlüssel `web.signals.group_functional` in `strings.yaml` wird danach von
niemandem mehr gelesen und **wird mitentfernt** — ein toter
Übersetzungsschlüssel ist Ballast, den beim nächsten Mal jemand für eine
Fundstelle hält.

**Achtung:** die bisherige Gruppe „Funktional" verschwindet damit. Der Hinweis `web.signals.none_functional` hing an `!group.collapsible && group.signals.length === 0` — eine Endpunktgruppe ist nie leer, sie entsteht ja aus ihren Signalen. Der Hinweis muss deshalb **außerhalb** der Gruppenschleife stehen, siehe Step 5.

- [ ] **Step 5: Das Markup nachziehen**

In der `<summary>` den Untertitel ergänzen:

```html
                  <summary>
                    <span x-text="group.title"></span>
                    <span class="muted" x-show="group.subtitle" x-text="group.subtitle"></span>
                    <span class="muted" x-text="'(' + group.signals.length + ')'"></span>
                    <svg class="icon chevron" aria-hidden="true"><use href="#i-chevron"></use></svg>
                  </summary>
```

Den `none_functional`-Hinweis aus der Gruppe herausnehmen und **vor** die `<template x-for="group …">` setzen:

```html
              <!-- Ausserhalb der Gruppenschleife, seit die Gruppen nach
                   Endpunkt entstehen (Entwurf 2026-09-07, Abschnitt 7.4):
                   eine Endpunktgruppe ist nie leer, sie entsteht ja aus
                   ihren Signalen. "Keine funktionalen Signale" heisst
                   jetzt "es gibt gar keine Endpunktgruppe". -->
              <p
                class="hint"
                x-show="functionalSignalsFor(signalsModalDevice).length === 0"
                x-text="t('web.signals.none_functional')"
              ></p>
```

- [ ] **Step 6: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 7: Im Browser prüfen**

Modal von „Hallway button" öffnen: drei Gruppen — „Taste 1 (8)", „Taste 2 (8)", „Gerät (1)" in dieser Reihenfolge, darunter „Experte (156)" zugeklappt. `press` steht einmal unter Taste 1 und einmal unter Taste 2.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Signal-Modal nach Endpunkt gruppieren

`press` stand zweimal in der Liste - 1/59/1 und 2/59/1, also zwei
verschiedene Tasten derselben Fernbedienung, gleich beschriftet und ohne
Auskunft, welche gemeint ist. Jetzt steht es einmal unter "Taste 1" und
einmal unter "Taste 2".

Die Reihenfolge der Gruppen folgt der Cluster-Rangliste, ohne dass hier
sortiert wuerde: `functionalSignalsFor` kommt sortiert an, und die
Schleife uebernimmt die Reihenfolge des ersten Auftretens. Am Taster
steht "Geraet" deshalb zuletzt, obwohl es Endpunkt 0 ist.

Der Untertitel nennt nur den Endpunkt, nicht den Cluster (abweichend vom
Entwurf 7.4): Endpunkt 2 der Steckdose fuehrt die Cluster 144 UND 145,
ein einzelner Clustername waere dort falsch.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 7: Das Modal — Tabellenraster mit Spaltenköpfen, beide Ja/Nein-Spalten als Häkchen

**Files:**
- Modify: `src/loxmatter/web/index.html` (Signalzeile im Modal)
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: die Gruppen aus Task 6.
- Produces: CSS-Klasse `.signal-grid` mit `grid-template-columns: 58px minmax(0, 1fr) 150px 70px 76px 28px`, verwendet von Kopfzeile und jeder Datenzeile.

- [ ] **Step 1: Die failing tests schreiben**

```python
async def test_the_signal_rows_and_the_header_share_one_grid(api):
    """Was heute fehlt und weshalb nichts fluchtet: die Zeile ist ein
    `flex-wrap`-Container ohne Spaltenmasse. Bei `multipress_ongoing`
    rutschte "periodisch erneut senden" allein in die naechste Zeile."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    assert page.count("signal-grid") >= 2
    assert "grid-template-columns: 58px minmax(0, 1fr) 150px 70px 76px 28px" in css


async def test_both_boolean_columns_are_checkboxes(api):
    """Das Bedienelement folgt dem BEHAELTER, nicht der Bedeutung: in einer
    Tabelle Haekchen, weil sie in einer Spalte fluchten und leise bleiben -
    eine Spalte aus 17 Schiebeschaltern waere eine deutlich lautere Textur,
    und Lautstaerke ist genau das Problem dieses Modals."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    modal = page[page.index('class="signals-modal"') :]
    for handler in ("toggleExported(signal)", "toggleResend(signal)"):
        # Das Bedienelement, das den Handler traegt: vom Handler
        # rueckwaerts bis zum oeffnenden Tag. So prueft der Test das
        # tatsaechliche Element und nicht irgendein `type="checkbox"`
        # anderswo im Modal.
        end = modal.index(handler)
        element = modal[modal.rindex("<", 0, end) : end]
        assert 'type="checkbox"' in element, handler


async def test_the_boolean_columns_keep_a_label_for_assistive_technology(api):
    """Die Beschriftung steht als Spaltenkopf einmal statt siebzehnmal neben
    einem Kaestchen - ein Screenreader liest aber die Zeile, nicht die
    Tabelle. Beide Kaestchen brauchen deshalb weiterhin ihren eigenen Namen."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.export_checkbox" in page
    assert "web.signals.resend_checkbox" in page
    assert ":aria-label=\"t('web.signals.export_checkbox')\"" in page


async def test_the_resend_column_is_explained_once_above_the_table(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.resend_explanation" in page
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k "grid or boolean or resend_column" -v`
Expected: FAIL.

- [ ] **Step 3: Die Texte anlegen**

```yaml
web.signals.col_export:
  en: "Export"
  de: "Export"
web.signals.col_signal:
  en: "Signal"
  de: "Signal"
web.signals.col_input:
  en: "Loxone input"
  de: "Loxone-Eingang"
web.signals.col_value:
  en: "Value"
  de: "Wert"
web.signals.col_resend:
  en: "Periodic"
  de: "Periodisch"
web.signals.resend_explanation:
  en: "Periodic means the value is sent again regularly, even when it has not changed."
  de: "Periodisch heißt: der Wert wird regelmäßig erneut gesendet, auch wenn er sich nicht ändert."
```

- [ ] **Step 4: Die Kopfzeile ins Markup setzen**

Direkt **vor** die `<template x-for="group …">`, nach dem `none_functional`-Hinweis:

```html
              <!-- Der Spaltenkopf klebt beim Scrollen oben: ohne ihn
                   verlieren die beiden Haekchenspalten ihre Bedeutung,
                   sobald er bei 173 Signalen aus dem Bild ist. Er traegt
                   DIESELBE `grid-template-columns` wie jede Datenzeile -
                   das ist der ganze Grund, warum die Tabelle fluchtet, und
                   der Test `test_the_signal_rows_and_the_header_share_one_
                   grid` sperrt genau diese Gleichheit. -->
              <div class="signal-grid signal-grid-head">
                <span x-text="t('web.signals.col_export')"></span>
                <span x-text="t('web.signals.col_signal')"></span>
                <span x-text="t('web.signals.col_input')"></span>
                <span class="col-right" x-text="t('web.signals.col_value')"></span>
                <span class="col-center" x-text="t('web.signals.col_resend')"></span>
                <span></span>
              </div>
```

Und die Erklärung darüber, neben den bestehenden `key_hint`:

```html
          <p class="hint">
            <span x-text="t('web.signals.key_hint')"></span>
            <span x-text="t('web.signals.resend_explanation')"></span>
          </p>
```

- [ ] **Step 5: Die Signalzeile umbauen**

Die bisherige `<div class="device-controls"><div class="row">…</div>…</div>` ersetzen durch:

```html
                  <template x-for="signal in group.signals" :key="signal.key">
                    <div class="signal-grid signal-row-cells">
                      <label class="col-center">
                        <input
                          type="checkbox"
                          :checked="signal.exported"
                          :disabled="!signal.exportable"
                          :aria-label="t('web.signals.export_checkbox')"
                          :title="t('web.signals.export_checkbox')"
                          @change="toggleExported(signal)"
                        />
                      </label>
                      <input
                        type="text"
                        class="signal-title"
                        :value="signal.title"
                        @input="titleDrafts[signal.key] = $event.target.value"
                        @change="saveTitle(signal)"
                      />
                      <span class="key" :title="t('web.signals.key_tooltip')" x-text="signal.key"></span>
                      <span
                        class="value col-right"
                        :class="{ 'value-fresh': signalIsFresh(signal) }"
                        :title="signalAgeTitle(signal)"
                        x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                      ></span>
                      <!-- Dasselbe Bedienelement wie in der Export-Spalte,
                           und das ist Absicht: beides ist ein Ja/Nein je
                           Signal in derselben Zeile. Der erste Entwurf hatte
                           hier einen Schiebeschalter - zwei Bedienelemente
                           fuer denselben Fall, genau die Inkonsistenz, die
                           dieses Modal schwer lesbar macht. Ein Schalter
                           gehoert in einen Detailbereich mit erklaerendem
                           Satz daneben, nicht in eine Tabellenspalte. -->
                      <label class="col-center">
                        <input
                          type="checkbox"
                          :checked="signal.resend"
                          :disabled="!signal.exportable"
                          :aria-label="t('web.signals.resend_checkbox')"
                          :title="t('web.signals.resend_checkbox')"
                          @change="toggleResend(signal)"
                        />
                      </label>
                      <span class="badge warn" x-show="!signal.exportable" x-text="signal.reason"></span>
                    </div>
                  </template>
```

Die letzte Zelle trägt vorläufig die `reason`-Pille; Task 8 setzt dort den Aufklapper hin und verlegt `reason` in dessen Inhalt.

- [ ] **Step 6: `style.css` ergänzen**

```css
/* Ein Raster fuer Kopfzeile UND Datenzeile (Entwurf 2026-09-07,
   Abschnitt 7.2). Die beiden MUESSEN dieselbe Vorlage tragen - genau das
   fehlte bisher, und deshalb fluchtete nichts: die alte `.row` war ein
   `flex-wrap`-Container ohne Spaltenmasse, bei `multipress_ongoing` rutschte
   die zweite Beschriftung allein in die naechste Zeile.

   Die 150 px der Schluessel-Spalte sind an `d4_1_multipress_ongoing`
   gemessen, dem laengsten Schluessel der Testvorlage. */
.signal-grid {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) 150px 70px 76px 28px;
  gap: 0.5rem;
  align-items: center;
  padding: 0.3rem 0;
}

.signal-grid > * {
  min-width: 0;
}

.signal-grid-head {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--bg);
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  padding: 0.35rem 0;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.signal-row-cells {
  border-bottom: 1px solid var(--border);
}

.signal-row-cells:last-child {
  border-bottom: none;
}

.signal-grid .col-right {
  text-align: right;
}

.signal-grid .col-center {
  display: flex;
  justify-content: center;
  margin: 0;
}

/* Die generische Regel `input[type="text"] { min-width: 12rem }` weiter oben
   gewinnt sonst gegen die Rasterspalte und sprengt sie - dieselbe Falle wie
   bei `.device-head .device-name`, Begruendung dort. */
.signal-grid .signal-title {
  width: 100%;
  min-width: 0;
}
```

- [ ] **Step 7: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 8: Das Fluchten messen, nicht ansehen**

„Fluchtet" ist die Zusicherung dieser Aufgabe, und mit dem Auge ist sie an
zwei Zeilen nicht zu belegen — bei `multipress_ongoing` ist der alte Umbruch
ja auch erst in der langen Liste aufgefallen.

**Wie der Harness gebaut wird** (bewährt am 2026-09-06, spart Anmeldung und
Runden): eine `harness.html` im Scratchpad, die den fraglichen Markup-Block
per Python **aus `index.html` herausschneidet** — nicht abtippen, sonst prüft
man eine Kopie. Daneben `style.css` und `vendor/alpine.min.js` kopieren und
ein Mini-`app()` stellen, das nur die Felder trägt, die der Block anfasst.
Davor `python3 -m http.server`. Zwei Fallen: `file://` lädt der eingebettete
Browser als statischen Schnappschuss, Alpine läuft dort **gar nicht** — es
muss über http gehen; und ein Tab, der einmal eine lokale Datei gezeigt hat,
bleibt darauf festgenagelt, also einen neuen Tab für die http-URL öffnen.

Im Harness das Modal mit **allen 17** funktionalen Signalen füllen und messen:

```js
const rows = [...document.querySelectorAll(".signal-row-cells")];
const head = document.querySelector(".signal-grid-head");
const linkeKanten = (el) => [...el.children].map((c) => Math.round(c.getBoundingClientRect().left));
const erwartet = linkeKanten(head);
JSON.stringify({
  zeilen: rows.length,
  abweichende: rows.filter((r) => String(linkeKanten(r)) !== String(erwartet)).length,
  hoehen: [...new Set(rows.map((r) => Math.round(r.getBoundingClientRect().height)))],
})
```

Erwartet: `abweichende` **0** — jede Datenzeile teilt die Spaltenkanten des
Kopfes. `hoehen` muss **einen** Wert enthalten: mehrere Höhen heißen, dass
mindestens eine Zeile umbricht, also genau der alte Zustand.

Dazu die Sperre gegen den waagerechten Überlauf:
`document.querySelector(".signals-modal").scrollWidth <= clientWidth`.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Signal-Modal als fluchtende Tabelle mit Spaltenkoepfen

Die alte Zeile war ein `flex-wrap`-Container ohne Spaltenmasse: bei
`multipress_ongoing` rutschte "periodisch erneut senden" allein in die
naechste Zeile, und `1/59/2` stand unkommentiert da. Kopfzeile und
Datenzeile tragen jetzt DIESELBE `grid-template-columns` - das ist der
ganze Grund, warum die Tabelle fluchtet, und ein Test sperrt die
Gleichheit.

Beide Ja/Nein-Spalten sind Haekchen. Die Regel dahinter: das
Bedienelement folgt dem BEHAELTER, nicht der Bedeutung. In einer Tabelle
Haekchen - sie fluchten in einer Spalte und bleiben leise; eine Spalte
aus 17 Schiebeschaltern waere eine deutlich lautere Textur, und
Lautstaerke ist genau das Problem dieses Modals. Ein Schalter gehoert in
einen Detailbereich mit erklaerendem Satz daneben.

Was "periodisch" bedeutet, steht einmal ueber der Tabelle statt
siebzehnmal neben einem Kaestchen. Die beiden Uebersetzungsschluessel
bleiben als `aria-label`/`title` erhalten - ein Screenreader liest die
Zeile, nicht die Tabelle.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 8: Das Modal — Herkunft und Rohwert im Zeilen-Aufklapper

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.endpoint`, `signal.cluster_id`, `signal.path`, `signal.reason`.
- Produces: `expandedSignalKey` (Alpine-Feld, `null` oder ein Schlüssel), `toggleSignalDetails(signal)`.

- [ ] **Step 1: Die failing tests schreiben**

```python
async def test_only_one_signal_detail_is_open_at_a_time(api):
    """Anders als beim Kachel-Menue und den Signalgruppen lebt dieser
    Zustand in Alpine, nicht im DOM: es gibt genau EINEN Wert fuer das ganze
    Modal, kein Auf/Zu je Element."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    assert "expandedSignalKey: null," in script
    body = script[script.index("toggleSignalDetails(signal)") :][:400]
    assert "this.expandedSignalKey = " in body


async def test_the_raw_write_field_is_no_longer_a_row_of_its_own(api):
    """Frueher beanspruchte das Rohwert-Feld bei JEDEM Attribut eine volle
    Zeile - ein Werkzeug zum Ausprobieren mit demselben Gewicht wie alles
    andere."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'x-show="signal.kind === \'attribute\'"' not in page
    assert 'x-show="expandedSignalKey === signal.key"' in page


async def test_the_detail_spells_out_the_path(api):
    """Der Pfad `1/59/1` bekommt endlich einen Ort, an dem genug Platz ist,
    ihn auszuschreiben, statt ihn als Raetsel neben den Namen zu stellen."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.origin" in page
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k "detail or raw_write or spells_out" -v`
Expected: FAIL.

- [ ] **Step 3: Die Texte anlegen**

```yaml
web.signals.origin:
  en: "Endpoint {endpoint} · Cluster {cluster} · Element {element}"
  de: "Endpunkt {endpoint} · Cluster {cluster} · Element {element}"
web.signals.row_details:
  en: "Origin and raw write"
  de: "Herkunft und Rohwert"
```

- [ ] **Step 4: `app.js` ergänzen**

Zu den Alpine-Feldern (neben `titleDrafts`, `rawWriteDrafts`):

```js
    // Welche Signalzeile ihren Aufklapper offen hat, oder null.
    //
    // Anders als beim Kachel-Menue und den Signalgruppen lebt dieser
    // Zustand in Alpine statt im DOM, und der Unterschied hat einen Grund:
    // dort gibt es ein Auf/Zu JE ELEMENT, hier genau EINEN Wert fuer das
    // ganze Modal. Hoechstens ein Bereich ist offen - bei 173 Zeilen waeren
    // mehrere offene Aufklapper wieder die Wand, die dieser Umbau abschafft.
    expandedSignalKey: null,
```

Bei den Signal-Helfern:

```js
    toggleSignalDetails(signal) {
      this.expandedSignalKey = this.expandedSignalKey === signal.key ? null : signal.key;
    },

    // Die Herkunft im Klartext. `signal.path` traegt dieselbe Auskunft als
    // "1/59/1", aber das ist ein Raetsel, solange niemand sagt, welche Zahl
    // was bedeutet - im Aufklapper ist endlich Platz, es auszuschreiben.
    signalOriginText(signal) {
      return t("web.signals.origin", {
        endpoint: signal.endpoint,
        cluster: signal.cluster_id,
        element: signal.path.split("/")[2],
      });
    },
```

- [ ] **Step 5: Das Markup umbauen**

Die letzte Rasterzelle der Signalzeile (Task 7, Step 5) ersetzen:

```html
                      <button
                        class="signal-more"
                        :aria-expanded="expandedSignalKey === signal.key"
                        :aria-label="t('web.signals.row_details')"
                        :title="t('web.signals.row_details')"
                        @click="toggleSignalDetails(signal)"
                      ><svg class="icon" aria-hidden="true"><use href="#i-kebab"></use></svg></button>
```

Und **hinter** die `.signal-grid`-Zeile, noch innerhalb desselben `x-for`-Wurzelelements — dafür Zeile und Aufklapper in eine Hülle nehmen:

```html
                  <template x-for="signal in group.signals" :key="signal.key">
                    <div class="signal-row-wrap" :class="{ 'is-expanded': expandedSignalKey === signal.key }">
                      <div class="signal-grid signal-row-cells">
                        <!-- … die sechs Zellen aus Task 7 … -->
                      </div>
                      <!-- Der Aufklapper haengt UNTER genau seiner Zeile,
                           nicht als zweite `.row` daneben: er gehoert zu
                           diesem einen Signal, und das soll man sehen. -->
                      <div class="signal-detail" x-show="expandedSignalKey === signal.key" x-cloak>
                        <p class="hint" x-text="signalOriginText(signal)"></p>
                        <p class="badge warn" x-show="!signal.exportable" x-text="signal.reason"></p>
                        <div class="row">
                          <input
                            type="text"
                            :placeholder="t('web.signals.raw_write_placeholder')"
                            @input="rawWriteDrafts[signal.key] = $event.target.value"
                          />
                          <button
                            @click="writeRaw(signal)"
                            :disabled="rawWriteBusyKey === signal.key"
                            x-text="t('web.signals.raw_write_submit')"
                          ></button>
                          <span
                            x-show="rawWriteMessages[signal.key]"
                            :class="rawWriteMessageClass(signal)"
                            x-text="rawWriteMessages[signal.key] ? rawWriteMessages[signal.key].text : ''"
                          ></span>
                        </div>
                      </div>
                    </div>
                  </template>
```

Das Rohwert-Feld ist nur für Attribute sinnvoll — die `.row` bekommt deshalb `x-show="signal.kind === 'attribute'"` **innerhalb** des Aufklappers (die Herkunft steht auch bei einem Ereignis).

- [ ] **Step 6: `style.css` ergänzen**

```css
.signal-row-wrap.is-expanded {
  background: var(--bg);
}

/* Eingerueckt bis zur Namensspalte (58 px Haekchen + 0.5 rem Abstand), damit
   der Aufklapper sichtbar zu SEINER Zeile gehoert und nicht zur Tabelle. */
.signal-detail {
  padding: 0.2rem 0 0.7rem calc(58px + 0.5rem);
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.signal-detail .hint {
  margin: 0;
}

.signal-more {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 0.1rem;
  color: var(--text-muted);
  cursor: pointer;
}

.signal-more[aria-expanded="true"] {
  color: var(--accent);
}
```

- [ ] **Step 7: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 8: Im Browser prüfen**

Auf das Kebab einer Attributzeile klicken: der Bereich öffnet sich unter genau dieser Zeile, nennt „Endpunkt 1 · Cluster 59 · Element 1" und trägt das Rohwert-Feld. Ein Klick auf ein zweites Kebab schließt das erste. Bei einer Ereigniszeile fehlt das Rohwert-Feld, die Herkunft steht trotzdem da.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Herkunft und Rohwert in einen Aufklapper je Signalzeile

Das Rohwert-Feld beanspruchte bei JEDEM Attribut eine volle Zeile - ein
Werkzeug zum Ausprobieren mit demselben Gewicht wie alles andere. Es
haengt jetzt unter genau seiner Zeile, hinter dem Kebab.

Das erledigt zugleich das zweite Problem: `1/59/1` stand unkommentiert
neben dem Namen, und im Aufklapper ist endlich Platz, es als "Endpunkt 1
- Cluster 59 - Element 1" auszuschreiben.

Hoechstens ein Bereich ist offen, und der Zustand lebt in Alpine statt
im DOM - anders als beim Kachel-Menue und den Signalgruppen gibt es hier
genau EINEN Wert fuer das ganze Modal. Bei 173 Zeilen waeren mehrere
offene Aufklapper wieder die Wand, die dieser Umbau abschafft.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 9: Das Modal — die Zahl im Kopf

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signalsByDevice`, `toggleExported`.
- Produces: `exportedSignalCount(deviceId)`, `deselectAllSignals(deviceId)`.

- [ ] **Step 1: Die failing tests schreiben**

```python
async def test_the_modal_leads_with_the_number_the_user_came_for(api):
    """Man oeffnet dieses Modal, um zu sehen und zu aendern, was nach Loxone
    geht. Diese Zahl stand bisher nirgends."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.export_summary" in page
    assert "exportedSignalCount(signalsModalDevice)" in page


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_deselect_all_empties_the_selection_instead_of_inverting_it():
    """Ein `toggleExported` ueber ALLE Signale haette die Auswahl invertiert -
    der Knopf heisst aber "alle abwaehlen", nicht "umkehren". Ein zweiter
    Klick muss deshalb nichts mehr tun.

    `toggleExported` wird hier ersetzt, weil es die Route ruft: geprueft
    wird die Auswahl-Regel dieser Schleife, nicht der Schreibweg."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "a", exported: true, exportable: true },
          { key: "b", exported: false, exportable: true },
          { key: "c", exported: true, exportable: false },
        ] };
        const touched = [];
        state.toggleExported = (signal) => {
          touched.push(signal.key);
          signal.exported = !signal.exported;
        };
        const before = state.exportedSignalCount(1);
        // Async-IIFE, weil `node -e` als CommonJS laeuft und dort kein
        // `await` auf oberster Ebene erlaubt ist - `deselectAllSignals`
        // ist async.
        (async () => {
          await state.deselectAllSignals(1);
          const firstRun = touched.slice();
          await state.deselectAllSignals(1);
          console.log(JSON.stringify({
            before,
            after: state.exportedSignalCount(1),
            total: state.signalCount(1),
            firstRun,
            secondRunTouched: touched.length - firstRun.length,
          }));
        })();
        """
    )

    # "c" ist zwar `exported`, passt aber auf keinen Loxone-Eingang - es
    # zaehlt nicht mit, genauso wie `to_inputs` es server-seitig auslaesst.
    assert values["before"] == 1
    assert values["total"] == 3
    assert values["after"] == 0
    # "b" war bereits aus und darf nicht angefasst worden sein.
    assert "b" not in values["firstRun"]
    assert values["secondRunTouched"] == 0
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k "number_the_user or deselect_all" -v`
Expected: FAIL.

- [ ] **Step 3: Die Texte anlegen**

```yaml
web.signals.export_summary:
  en: "{exported} of {total} signals go to Loxone as an input"
  de: "{exported} von {total} Signalen gehen als Eingang nach Loxone"
web.signals.deselect_all:
  en: "Deselect all"
  de: "Alle abwählen"
```

- [ ] **Step 4: `app.js` ergänzen**

```js
    // Wie viele Signale dieses Geraets tatsaechlich als Eingang nach Loxone
    // gehen. `exported` allein reicht nicht: ein Signal, dessen Wert auf
    // keinen Loxone-Eingang passt (`exportable === false`), erzeugt keinen -
    // dieselbe Unterscheidung, die `to_inputs` server-seitig macht.
    exportedSignalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((s) => s.exported && s.exportable).length : 0;
    },

    signalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.length : 0;
    },

    // Nur was AN ist, wird ausgeschaltet. Ein `toggleExported` ueber alle
    // Signale wuerde die Auswahl invertieren statt sie zu leeren - der
    // Knopf heisst aber "alle abwaehlen", nicht "umkehren".
    async deselectAllSignals(deviceId) {
      const signals = this.signalsByDevice[deviceId] || [];
      for (const signal of signals) {
        if (signal.exported) {
          await this.toggleExported(signal);
        }
      }
    },
```

**Vor dem Schreiben `toggleExported` lesen:** liegt dort ein optimistisches Umschalten des lokalen Objekts vor dem `PATCH`, ist die Schleife oben richtig. Verlässt sich `toggleExported` dagegen auf `$event.target.checked`, muss `deselectAllSignals` stattdessen die Route direkt aufrufen — dann `toggleExported` so umbauen, dass es den Zielzustand als Parameter nimmt, statt eine zweite Kopie des Schreibwegs anzulegen.

- [ ] **Step 5: Das Markup ergänzen**

Nach der `<div class="signals-modal-head">`, vor dem `key_hint`-Absatz:

```html
          <!-- Die eine Zahl, wegen der man dieses Modal oeffnet. Sie stand
               bisher nirgends - man musste 17 Haekchen zaehlen. -->
          <div class="signals-summary">
            <span
              x-text="t('web.signals.export_summary', { exported: exportedSignalCount(signalsModalDevice), total: signalCount(signalsModalDevice) })"
            ></span>
            <span style="flex: 1 1 auto"></span>
            <button
              @click="deselectAllSignals(signalsModalDevice)"
              x-text="t('web.signals.deselect_all')"
            ></button>
          </div>
```

- [ ] **Step 6: `style.css` ergänzen**

```css
.signals-summary {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  padding: 0.55rem 0.75rem;
  margin-bottom: 0.6rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
}
```

- [ ] **Step 7: Tests laufen lassen und im Browser prüfen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

Im Browser: „12 von 17 Signalen gehen als Eingang nach Loxone". Ein Häkchen abwählen → die Zahl fällt auf 11. „Alle abwählen" → 0, und ein zweiter Klick darauf lässt sie bei 0 (kein Umkehren).

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): die Exportzahl in den Kopf des Signal-Modals

Man oeffnet dieses Modal, um zu sehen und zu aendern, was nach Loxone
geht - und genau diese Zahl stand bisher nirgends. Man musste 17
Haekchen zaehlen.

Gezaehlt wird `exported && exportable`, nicht `exported` allein: ein
Signal, dessen Wert auf keinen Loxone-Eingang passt, erzeugt keinen -
dieselbe Unterscheidung, die `to_inputs` server-seitig macht.

"Alle abwaehlen" schaltet nur aus, was an ist. Ein `toggleExported` ueber
alle Signale wuerde die Auswahl invertieren, und der Knopf heisst nicht
"umkehren".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 10: Der Umbruch unter 640 px

**Files:**
- Modify: `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `.signal-grid` aus Task 7.
- Produces: nichts.

- [ ] **Step 1: Den failing test schreiben**

```python
async def test_the_signal_table_stacks_on_a_narrow_screen(api):
    """Sechs Spalten passen unter etwa 640 px nicht. Ohne diesen Umbruch
    franst die Tabelle dort wieder aus - also genau der Zustand, den der
    ganze Umbau beseitigt hat, nur auf einem Telefon."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    assert "@media (max-width: 640px)" in css
    narrow = css[css.index("@media (max-width: 640px)") :][:900]
    assert ".signal-grid-head" in narrow
    assert "display: none" in narrow
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_web.py -k narrow -v`
Expected: FAIL.

- [ ] **Step 3: Die Regeln schreiben**

```css
/* Sechs Spalten passen hier nicht (Entwurf 2026-09-07, Abschnitt 10). Die
   Zeile wird zur gestapelten Karte, und der Spaltenkopf verschwindet -
   ueber einer gestapelten Karte beschriftet er nichts mehr, er stuende nur
   als Reihe von fuenf Woertern ohne Bezug da.

   Damit die Haekchen ohne Spaltenkopf ihre Bedeutung behalten, bekommen
   sie hier ihre Beschriftung zurueck: `.col-center` wird linksbuendig und
   das `title`-Attribut des Kaestchens erscheint als Text daneben. Das ist
   dieselbe Auskunft wie im Spaltenkopf, nur am anderen Ort - kein zweiter
   Uebersetzungsschluessel. */
@media (max-width: 640px) {
  .signal-grid-head {
    display: none;
  }

  .signal-grid {
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.3rem 0.5rem;
    padding: 0.5rem 0;
  }

  /* Name und Schluessel bekommen die volle Breite, die drei kurzen Zellen
     teilen sich die Zeile darunter. */
  .signal-grid .signal-title,
  .signal-grid .key {
    grid-column: 1 / -1;
  }

  .signal-grid .col-right {
    text-align: left;
  }

  .signal-grid .col-center {
    justify-content: flex-start;
  }

  .signal-detail {
    padding-left: 0;
  }
}
```

- [ ] **Step 4: Test laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 5: Bei 380 px messen**

Denselben Harness wie in Aufgabe 7, aber das Fenster auf **380 px** stellen
(`resize_window` mit `preset: "mobile"`), Seite neu laden — die Medienabfrage
greift erst nach dem Neuladen zuverlässig — und lesen:

```js
const modal = document.querySelector(".signals-modal");
const rows = [...document.querySelectorAll(".signal-row-cells")];
JSON.stringify({
  ueberlauf: modal.scrollWidth - modal.clientWidth,
  kopfSichtbar: getComputedStyle(document.querySelector(".signal-grid-head")).display,
  kaestchen: document.querySelectorAll('.signal-row-cells input[type="checkbox"]').length,
  kleinsteTrefferflaeche: Math.min(
    ...[...document.querySelectorAll('.signal-row-cells input[type="checkbox"]')]
      .map((c) => Math.round(c.getBoundingClientRect().width)),
  ),
})
```

Erwartet: `ueberlauf` **0**, `kopfSichtbar` `"none"`, `kaestchen` gleich
`2 × Zeilenzahl` (beide Ja/Nein-Spalten bleiben bedienbar, sie verschwinden
nicht mit dem Kopf), `kleinsteTrefferflaeche` > 0.

Zum Schluss `resize_window` auf `preset: "desktop"` zurücksetzen — eine
gesetzte Größe bleibt sonst am Tab kleben.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Signaltabelle unter 640 px als gestapelte Karten

Sechs Spalten passen dort nicht - ohne diesen Umbruch franst die Tabelle
auf einem Telefon wieder aus, also genau der Zustand, den der Umbau
beseitigt hat.

Der Spaltenkopf verschwindet mit: ueber einer gestapelten Karte
beschriftet er nichts mehr. Die Haekchen behalten ihre Bedeutung ueber
das `title`, das sie ohnehin fuer Hilfstechnik tragen - kein zweiter
Uebersetzungsschluessel fuer dieselbe Auskunft.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 12: Rang je Element — der Taster muss mit `press` fuehren

**Nachgetragen am 8. September 2026**, nachdem Aufgabe 11 den Befund am fertigen
Bild aufgedeckt hat. Der Entwurf (Abschnitt 4, „Warum kein Rang je Element")
hat diese Ebene ausdruecklich offengelassen: sie „kann nachgetragen werden,
wenn ein konkretes Geraet sie verlangt". Genau das ist eingetreten.

**Der Befund.** Die Kachel des Tasters fuehrt nicht mit `press`, sondern mit
`positions`:

```
 0. ep1 cl59 el0 attribute  positions   <== Leitwert
 1. ep1 cl59 el1 attribute  position
 2. ep1 cl59 el1 event      press
```

`positions` ist Matters `NumberOfPositions` — die statische Angabe, dass diese
Taste zwei Stellungen hat. Ein Konfigurationswert, der sich nie aendert. Als
Leitwert ist das **schlechter als der Batteriestand**, den dieser ganze Plan
beseitigen wollte: der sank wenigstens.

**Warum es niemand bemerkt hat.** Der Test aus Aufgabe 2 lautet

```python
assert functional[0].ref.cluster_id == 59
```

Das ist fuer `positions` genauso wahr wie fuer `press`. Er prueft den Cluster,
waehrend Entwurf und Canvas durchweg `press` zeigen — eine Luecke zwischen
Absicht und Zusicherung. Sie wird in dieser Aufgabe mitgeschlossen.

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml` (nur Cluster 59)
- Modify: `src/loxmatter/profiles/table.py`
- Modify: `src/loxmatter/model/store.py` (`_signal_order`)
- Test: `tests/profiles/test_table.py`, `tests/model/test_store.py`

**Interfaces:**
- Consumes: `table.rank_for`, `table.DEFAULT_RANK` (Aufgabe 1); `_signal_order` (Aufgabe 2).
- Produces: `table.element_rank_for(ref: SignalRef) -> int`; `_signal_order` liefert ein SECHSSTELLIGES Tupel `(cluster_rank, endpoint, cluster_id, element_rank, element_id, kind)`.

- [ ] **Step 1: Die failing tests schreiben**

An `tests/profiles/test_table.py`:

```python
def test_an_element_can_carry_its_own_rank():
    """Der Taster war der konkrete Fall, der diese Ebene noetig gemacht hat:
    innerhalb von Cluster 59 muss der Tastendruck vor die statische Angabe
    `NumberOfPositions`, sonst fuehrt die Kachel mit einer Zahl, die sich nie
    aendert."""
    press = SignalRef(1, 59, 1, SignalKind.EVENT)
    positions = SignalRef(1, 59, 0, SignalKind.ATTRIBUTE)

    assert table.element_rank_for(press) < table.element_rank_for(positions)


def test_an_element_without_a_rank_gets_the_default():
    """Dieselbe Vorgabe wie auf Clusterebene, und aus demselben Grund: die
    Mitte, damit ein nicht eingetragenes Element weder nach vorn noch ganz
    nach hinten faellt."""
    longpress = SignalRef(1, 59, 2, SignalKind.EVENT)
    assert table.element_rank_for(longpress) == table.DEFAULT_RANK


def test_an_element_of_an_unknown_cluster_gets_the_default():
    """Cluster 3 (Identify) steht nicht in der Tabelle - es gibt dort weder
    einen Abschnitt noch ein Element, in dem ein Rang stehen koennte."""
    assert table.element_rank_for(SignalRef(1, 3, 0, SignalKind.ATTRIBUTE)) == table.DEFAULT_RANK
```

An `tests/model/test_store.py` — **und der bestehende, zu schwache Test wird dabei ersetzt**, nicht ergaenzt:

```python
def test_the_button_leads_with_the_button_press(tmp_path):
    """Ersetzt `test_the_button_leads_with_a_switch_signal_not_the_battery`,
    der nur `cluster_id == 59` prueft. Das war zu schwach: `positions`
    (NumberOfPositions, Element 0) traegt denselben Cluster und sortierte
    davor - die Kachel fuehrte damit mit der statischen Angabe, dass diese
    Taste zwei Stellungen hat. Der Test sagte trotzdem ja.

    Diese Fassung nennt das Signal beim Namen. Ein Test, der nur den Cluster
    prueft, laesst genau den Fehler durch, den zu verhindern der Zweck des
    ganzen Umbaus war."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].title == "press"
    assert functional[0].ref.kind is SignalKind.EVENT
    assert functional[-1].ref.cluster_id == 47


def test_the_static_position_count_sorts_behind_every_button_event(tmp_path):
    """`positions` aendert sich nie - es gehoert ans Ende der Tastengruppe,
    nicht an ihren Anfang."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    endpoint1 = [
        s for s in store.signals(device_id) if s.functional and s.ref.endpoint == 1
    ]
    titles = [s.title for s in endpoint1]

    assert titles[0] == "press"
    assert titles[-1] == "positions"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/profiles/test_table.py -k element_rank tests/model/test_store.py -k "leads_with_the_button or static_position" -v`
Expected: FAIL — `element_rank_for` gibt es nicht, und der Leitwert ist `positions`.

- [ ] **Step 3: Die Ränge in `clusters.yaml` eintragen**

**Nur Cluster 59.** Kein anderer Cluster bekommt Elementränge, solange kein Gerät sie verlangt — dieselbe Zurückhaltung wie bei `UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`.

```yaml
  59:
    name: switch
    rank: 10
    attributes:
      # `rank` je Element ordnet INNERHALB dieses Clusters (Nachtrag
      # 2026-09-08). Cluster 59 ist der bislang einzige, der ihn braucht,
      # und der Grund ist NumberOfPositions: die Angabe, wie viele
      # Stellungen diese Taste hat, aendert sich nie. Sie stand mit
      # Element-ID 0 vor jedem Tastendruck und wurde damit zum Leitwert
      # der Kachel - eine Konstante als wichtigstes Merkmal eines Tasters.
      0: {slug: positions, unit: "", rank: 90}
      1: {slug: position, unit: ""}
    events:
      # Der Tastendruck ist, wofuer man einen Taster einlernt.
      1: {slug: press, rank: 10}
      2: {slug: longpress}
      3: {slug: shortrelease}
      4: {slug: longrelease}
      5: {slug: multipress_ongoing}
      6: {slug: multipress}
```

Die vorhandenen Schlüssel jedes Elements (`slug`, `unit`, …) bleiben unverändert — es kommt nur `rank` dazu, und nur bei zweien.

- [ ] **Step 4: `element_rank_for` in `table.py` schreiben**

Neben `rank_for`:

```python
def element_rank_for(ref: SignalRef) -> int:
    """Wie wichtig dieses Element INNERHALB seines Clusters ist.

    Zweite Ebene neben `rank_for`, und sie ist nachgetragen worden statt von
    Anfang an dazusein (Entwurf 2026-09-07, Abschnitt 4: "kann nachgetragen
    werden, wenn ein konkretes Geraet sie verlangt"). Das Geraet, das sie
    verlangt hat, ist der IKEA-Taster: `NumberOfPositions` (Element 0) traegt
    denselben Cluster wie der Tastendruck und sortierte mit der kleineren
    Element-ID davor - die Kachel fuehrte damit mit einer Konstanten.

    Die Vorgabe ist dieselbe wie auf Clusterebene und aus demselben Grund die
    Mitte: ein nicht eingetragenes Element soll weder nach vorn noch ganz
    nach hinten fallen. Die grosse Mehrheit der Elemente traegt deshalb gar
    keinen Rang, und die Element-ID ordnet sie weiterhin - so, wie es bis
    hierher fuer jeden Cluster ausser 59 richtig war.
    """
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return DEFAULT_RANK
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    element = (cluster.get(section) or {}).get(ref.element_id)
    if not isinstance(element, dict):
        return DEFAULT_RANK
    rank = element.get("rank")
    return DEFAULT_RANK if rank is None else int(rank)
```

- [ ] **Step 5: `_signal_order` erweitern**

In `store.py` den Import um `element_rank_for` ergänzen und den Schlüssel sechsstellig machen — **der Elementrang steht hinter `cluster_id` und vor `element_id`**, denn er ordnet innerhalb eines Clusters:

```python
def _signal_order(signal: StoredSignal) -> tuple[int, int, int, int, int, str]:
    """Der Sortierschluessel der Signalliste (Entwurf 2026-09-07, Abschnitt 4,
    mit dem Elementrang als Nachtrag vom 2026-09-08).

    Zwei Rangebenen, und ihre Stellung im Tupel ist die ganze Aussage: der
    CLUSTER-Rang steht ganz vorn und ordnet die Cluster zueinander (deshalb
    faellt PowerSource hinter alles Funktionale); der ELEMENT-Rang steht
    hinter `cluster_id` und ordnet nur innerhalb desselben Clusters (deshalb
    faellt `positions` hinter jeden Tastendruck, ohne dass die Tastengruppe
    als Ganzes ihren Platz aendert).

    Sortiert wird in Python und nicht in SQL, weil beide Raenge aus
    `clusters.yaml` kommen: SQLite kennt sie nicht, und sie als Spalten in
    `signal` zu spiegeln hiesse, sie bei jeder Aenderung der YAML-Datei
    nachtragen zu muessen - eine zweite Wahrheit fuer denselben Wert.

    Die hinteren Glieder sind der bisherige Schluessel. Er ist wegen der
    UNIQUE-Bedingung auf `signal` bereits eindeutig, damit ist auch dieser
    Schluessel total - die Reihenfolge flattert nie, was fuer den Export
    wichtig ist (er schreibt sie in eine Datei).
    """
    return (
        rank_for(signal.ref.cluster_id),
        signal.ref.endpoint,
        signal.ref.cluster_id,
        element_rank_for(signal.ref),
        signal.ref.element_id,
        signal.ref.kind.value,
    )
```

- [ ] **Step 6: Tests laufen lassen**

Run: `uv run pytest tests/profiles tests/model -v`
Expected: PASS.

- [ ] **Step 7: Die ganze Testreihe**

Run: `uv run pytest -q`
Expected: PASS. Schlägt ein Test fehl, der die alte Reihenfolge festhält, gilt dieselbe Regel wie in Aufgabe 2: **einordnen, nicht wegschreiben** — hält er sie zufällig fest, darf er nachgezogen werden; hält er sie absichtlich fest, ist es ein Konflikt und wird gemeldet.

Besonders zu prüfen: `tests/export/test_signals.py::test_the_template_lists_the_button_press_before_the_battery` und der Projektdatei-Sync-Test aus Aufgabe 3 — beide hängen an dieser Reihenfolge und sollen weiterhin grün sein.

- [ ] **Step 8: Belegen, dass der neue Test den alten Fehler fängt**

Nimm `rank: 90` bei `positions` probeweise heraus, lass `uv run pytest tests/model/test_store.py -k leads_with_the_button -v` laufen, zeig den Fehlschlag, und trag den Rang danach wieder ein. Ohne diesen Nachweis ist der Test nur eine Behauptung.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/profiles/table.py src/loxmatter/model/store.py tests/profiles/test_table.py tests/model/test_store.py
git commit -m "$(cat <<'MSG'
fix(profiles): Rang je Element - der Taster fuehrt mit `press`, nicht mit `positions`

Die Cluster-Rangliste hat den Batteriestand von der Kachel verdraengt, aber
nicht das Richtige an seine Stelle gesetzt: der Taster fuehrte danach mit
`positions` - Matters NumberOfPositions, die statische Angabe, dass diese
Taste zwei Stellungen hat. Ein Wert, der sich nie aendert, als wichtigstes
Merkmal eines Tasters; damit war es schlechter als der Batteriestand, den
dieser Umbau beseitigen wollte, denn der sank wenigstens.

Aufgefallen ist es erst am fertigen Screenshot. Der Test aus Aufgabe 2 hatte
es durchgelassen, weil er `functional[0].ref.cluster_id == 59` prueft - fuer
`positions` genauso wahr wie fuer `press`. Er nennt das Signal jetzt beim
Namen; ein Test, der nur den Cluster prueft, laesst genau den Fehler durch,
den zu verhindern der Zweck des Umbaus war.

Der Entwurf hatte diese Ebene ausdruecklich offengelassen ("kann nachgetragen
werden, wenn ein konkretes Geraet sie verlangt"). Der IKEA-Taster verlangt
sie. Eingetragen ist sie nur fuer Cluster 59 und dort nur an zwei Elementen -
dieselbe Zurueckhaltung wie bei UTILITY_ENDPOINT_KEEP_CLUSTERS: ein neuer
Eintrag braucht ein Geraet, das ihn belegt, nicht die Annahme, die Tabelle
sei von sich aus vollstaendig.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 11: Screenshots nachziehen

**Files:**
- Modify: `docs/screenshots/dashboard.png`, `docs/screenshots/signals.png`

**Interfaces:**
- Consumes: alles Vorherige.
- Produces: nichts.

- [ ] **Step 1: Die ganze Testreihe laufen lassen**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`
Expected: alles grün.

- [ ] **Step 2: Screenshots neu aufnehmen**

Run: `uv run python scripts/capture_screenshots.py`

Die Datei nennt in ihrem Kopfkommentar, was sie braucht (Browser, Port). Falls sie fehlschlägt, ihren Kommentar lesen statt raten.

- [ ] **Step 3: Die neuen Bilder ansehen**

`docs/screenshots/dashboard.png` muss zeigen: „Hallway button" führt mit `press`, darunter die Batteriezeile, „+ 10 weitere".
`docs/screenshots/signals.png` muss zeigen: Spaltenköpfe, drei Endpunktgruppen, fluchtende Spalten, beide Ja/Nein-Spalten als Häkchen.

Zeigt ein Bild noch den alten Stand, hat das Skript einen zwischengespeicherten Zustand aufgenommen — Prozess beenden, `~/.loxmatter`-Kopie des Entwicklungsdienstes löschen (das Skript legt sie in einem temporären Verzeichnis an, siehe seinen Kopf) und erneut laufen lassen.

- [ ] **Step 4: Commit**

```bash
git add docs/screenshots
git commit -m "$(cat <<'MSG'
docs(screenshots): Bilder auf die Signal-Rangliste nachziehen

Die Geraeteansicht zeigt jetzt `press` als Leitwert des Tasters statt des
Batteriestands, und das Signal-Modal seine Endpunktgruppen mit
fluchtenden Spalten.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

## Selbstprüfung des Plans

**Abdeckung des Entwurfs:**

| Entwurfsabschnitt | Aufgabe |
| --- | --- |
| 1 Der Befund | 2 (Test), 5 (Kachel) |
| 2 Das Modal | 6–10 |
| 3 Entscheidungen | alle |
| 4 Die Rangliste | 1 |
| 5 Sortiert an der Quelle | 2, 3 |
| 6 Die Kachel | 5 |
| 7.1–7.2 Aufbau, Spalten | 7 |
| 7.3 Beide Häkchen | 7 |
| 7.4 Endpunkt-Gruppen | 4, 6 |
| 7.5 Rohwert-Feld | 8 |
| 7.6 Der Kopf | 9 |
| 8 Was unverändert bleibt | keine Aufgabe fasst `relevance.py`, `categories.py`, `exported`, `exportability` oder `FUNCTIONAL_PREVIEW_LIMIT` an |
| 9 Tests | über die Aufgaben verteilt, jeder Punkt des Entwurfs hat einen |
| 10.1 Umbruch unter 640 px | 10 |
| 10.2 Ränge als erste Belegung | im Kommentar von `clusters.yaml`, Aufgabe 1 Step 3 |

**Bekannte Abweichung vom Entwurf:** der Gruppen-Untertitel nennt nur den Endpunkt, nicht zusätzlich den Cluster (Task 6, mit Begründung). Der Cluster steht ab Task 8 je Zeile im Aufklapper.

**Namenskonsistenz:** `previewSignalsFor` (Task 5) wird von `firstSignalsFor` und `remainingSignalCount` derselben Aufgabe benutzt. `batterySignalFor` (Task 5) wird in Task 5 im Markup verwendet. `endpoint_labels` (Task 4) wird nur in `api/devices.py` derselben Aufgabe aufgerufen. `signal.cluster_id`/`signal.endpoint`/`signal.endpoint_label` (Task 4) werden in den Tasks 5, 6 und 8 gelesen — Task 4 steht davor.

**Zwei Stellen, an denen der ausführende Entwickler vor dem Schreiben lesen muss** (im Plan jeweils vermerkt): die Signatur von `_signal_out` und seiner Aufrufstelle (Task 4, Step 9) und das Verhalten von `toggleExported` (Task 9, Step 4). Beide sind im Bestand und können von den Skizzen abweichen.
