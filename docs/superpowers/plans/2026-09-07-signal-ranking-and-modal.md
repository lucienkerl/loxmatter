# Signal ranking and signal modal — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Signals are sorted by importance instead of by endpoint number, and the signal modal becomes an aligned table with endpoint groups.

**Architecture:** A cluster ranking as `rank:` in `profiles/clusters.yaml` provides the first part of the sort key; `Store.signals` re-sorts it in Python because SQLite does not know the rank. This way the tile, modal *and* Loxone template all change from one source. The UI gets three new fields in `SignalOut` (`endpoint`, `cluster_id`, `endpoint_label`), a battery footer on the tile, and a grid layout in the modal.

**Tech Stack:** Python 3.12, SQLite, FastAPI/Pydantic, PyYAML, Alpine.js (no build step), pytest.

## Global Constraints

- **Design:** `docs/superpowers/specs/2026-09-07-signal-ranking-and-modal-design.md`. Section numbers in this plan refer to it.
- **Language:** Docstrings, comments, and commit messages in **German**, concise and reasoned (why, not just what). The GPL header of each source file stays in English FSF form.
- **Umlauts in Python comments:** existing files write `ue`/`ae`/`oe` instead of umlauts (`ueberhaupt`, `Geraet`). Keep this convention. In Markdown and `strings.yaml` values, use real umlauts.
- **Runtime text goes through `i18n`:** every new visible text gets an `en`/`de` pair in `src/loxmatter/i18n/strings.yaml`. No hard-coded German text in `app.js` or `index.html`.
- **`strings.yaml` values must not be wrapped in typographic quotes** — `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` blocks this.
- **Keys are untouchable.** `d4_1_press` stays `d4_1_press`. No task in this plan changes `signal.key`.
- **Tests run with** `uv run pytest`. Single file: `uv run pytest tests/profiles/test_table.py -v`.
- **Before each commit:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`.

## File structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/loxmatter/profiles/clusters.yaml` | adds `rank:` per cluster | 1 |
| `src/loxmatter/profiles/table.py` | `rank_for()` and `DEFAULT_RANK` | 1 |
| `src/loxmatter/model/store.py` | `signals()` sorts by rank | 2 |
| `src/loxmatter/profiles/endpoints.py` | **new** — device type → descriptive endpoint name | 4 |
| `src/loxmatter/api/models.py` | `SignalOut` gets `endpoint`, `cluster_id`, `endpoint_label` | 4 |
| `src/loxmatter/api/devices.py` | fills the three fields | 4 |
| `src/loxmatter/web/app.js` | battery row, endpoint groups, row expander, header count | 5–9 |
| `src/loxmatter/web/index.html` | tile footer, modal grid, battery icon | 5–10 |
| `src/loxmatter/web/style.css` | `.device-battery`, `.signal-grid`, wrap below 640 px | 5, 7, 10 |
| `src/loxmatter/i18n/strings.yaml` | all new text | 4–9 |

`profiles/endpoints.py` stands deliberately **beside** `categories.py` rather than in it: `categories.py` answers "what kind of thing is the whole device", `endpoints.py` "what is this one endpoint in it called". A remote is *one* switch with *two* buttons — the same table for both questions would be wrong (design 7.4).

---

### Task 1: The cluster ranking

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py:44-100`
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `loxmatter.profiles.table.rank_for(cluster_id: int) -> int` and `loxmatter.profiles.table.DEFAULT_RANK: int` (value 50).

- [ ] **Step 1: Write the failing tests**

Append to `tests/profiles/test_table.py`:

```python
def test_a_cluster_with_a_rank_reports_it():
    """The rank determines what appears as the primary value on the tile -
    it must therefore come from the table and not from an assumption."""
    assert table.rank_for(6) == 10  # OnOff
    assert table.rank_for(59) == 10  # Switch
    assert table.rank_for(47) == 90  # PowerSource


def test_a_cluster_without_a_rank_gets_the_default():
    """Cluster 3 (Identify) is not in the table. It must neither
    land at the front nor behind battery: the default is the middle, so a
    new device type never accidentally leads with battery and
    its main feature still stands ahead of utility info (design 4)."""
    assert table.rank_for(3) == table.DEFAULT_RANK
    assert table.DEFAULT_RANK == 50


def test_the_utility_clusters_rank_behind_everything_functional():
    """The one rule why this design exists at all."""
    functional = [table.rank_for(c) for c in (6, 8, 59, 144, 145, 768, 1026, 1029)]
    assert max(functional) < table.rank_for(47)
    assert table.rank_for(47) < table.rank_for(40)


def test_every_rank_in_the_table_is_an_integer():
    """A typo `rank: "10"` would be a string in YAML
    and would throw against a number during sorting - only at runtime,
    when opening a device view."""
    for cluster_id, cluster in table._table().items():
        if "rank" in cluster:
            assert isinstance(cluster["rank"], int), cluster_id
```

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/profiles/test_table.py -k rank -v`
Expected: FAIL with `AttributeError: module 'loxmatter.profiles.table' has no attribute 'rank_for'`

- [ ] **Step 3: Enter `rank:` in `clusters.yaml`**

Insert a `rank:` line directly under `name:` in each of the ten cluster blocks. Add this comment above the `clusters:` key:

```yaml
# `rank` orders clusters RELATIVE TO EACH OTHER when the UI and export
# sort a device's signals (design 2026-09-07, section 4).
# Smaller rank first: what a device DOES in the house ranks at 10-40; what it
# says about itself ranks at 90+. A cluster without `rank` gets 50 and
# lands in the middle - behind what demonstrably matters, but ahead of
# battery and device info. This default is intentional: a device type
# no one has entered yet should never lead with battery, its main feature
# also should not be banished behind the known.
#
# WITHIN a rank, the previous order stays (endpoint, cluster,
# element, kind) - it orders two signals of the same cluster, and it does
# that well. There is deliberately NO rank per element: element IDs are
# already roughly assigned by importance in the Matter spec itself, a
# second rank level would be effort without proven gain.
```

The values:

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

Example for the first block:

```yaml
clusters:
  6:
    name: onoff
    rank: 10
    attributes:
      0: {slug: onoff, unit: ""}
```

If cluster 40 (BasicInformation) has no block of its own today, create one — it needs neither `attributes:` nor `commands:`, just name and rank:

```yaml
  40:
    name: basicinformation
    # No `attributes:` section: this cluster is here ONLY because of
    # its rank. An empty `attributes:` section would have a second,
    # unwanted effect - `relevance.is_functional` reads it via
    # `known_attribute_section` and would then reject EVERY attribute of this
    # cluster as unwanted (see its docstring,
    # layer 3). Rank alone only changes the order.
    rank: 95
```

- [ ] **Step 4: Write `rank_for` in `table.py`**

Insert after `_table()` (line 99):

```python
# The rank of a cluster the table does not carry (design
# 2026-09-07, section 4). The middle, not the end: an unknown
# cluster must never land behind battery, but also not ahead of
# a cluster whose importance is proven.
DEFAULT_RANK = 50


def rank_for(cluster_id: int) -> int:
    """How important this cluster is for display - smaller is more important.

    Separate from `lookup` and `knows_cluster`, because this question is different
    from "what is the element called" or "does the table know this cluster":
    a cluster can be in the table (because of its commands) and
    still not carry a rank. Both cases - not in the table at all,
    and in the table without `rank` - give the same answer here, because
    they mean the same thing for sorting.
    """
    cluster = _table().get(cluster_id)
    if cluster is None:
        return DEFAULT_RANK
    rank = cluster.get("rank")
    return DEFAULT_RANK if rank is None else int(rank)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: PASS, all tests in the file (existing ones must not break).

- [ ] **Step 6: Verify nothing else broke**

Run: `uv run pytest tests/profiles tests/export -v`
Expected: PASS. `clusters.yaml` is read by `lookup`, `names_element` and `extract_commands` — an additional key line must not change anything there.

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

### Task 2: `Store.signals` sorts by rank

**Files:**
- Modify: `src/loxmatter/model/store.py:1304-1310`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `table.rank_for` from Task 1.
- Produces: `Store.signals(device_id)` delivers sorted by `(rank, endpoint, cluster_id, element_id, kind)`. Signature and return type unchanged (`list[StoredSignal]`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/model/test_store.py` (the file already has `load_snapshot` from `conftest` and creates a `Store` in `tmp_path` — follow the pattern there):

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

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/model/test_store.py -k "button_leads or same_cluster" -v`
Expected: FAIL — `functional[0].ref.cluster_id` is 47, not 59.

- [ ] **Step 3: Sortierung einbauen**

Add the import in `store.py` (the file already imports `Exportability` and `is_exportable` from `profiles.table`):

```python
from loxmatter.profiles.table import Exportability, is_exportable, rank_for
```

Insert before the `Store` class (with the other module functions like `_normalized_room`):

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
Expected: PASS. If something fails here, it is a test that encodes the old order — **do not** simply rewrite the expectation, but check if the test verifies the order randomly or intentionally, and record the result in Task 3.

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

### Task 3: Secure export and project file sync against the new ordering

This task writes **no** production code. It records what section 5 of the design claims as verified — so the claim does not stand only in the design.

**Files:**
- Test: `tests/export/test_signals.py`
- Test: `tests/projectsync/test_diff.py`

**Interfaces:**
- Consumes: `Store.signals` from Task 2, `export.signals.to_inputs`, `projectsync.diff.build_plan`.
- Produces: nichts.

- [ ] **Step 1: Write the export test**

Append to `tests/export/test_signals.py`:

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

- [ ] **Step 2: Write the backward compatibility test**

Append to `tests/projectsync/test_diff.py`. Follow the pattern there for building a project file; the key thing is that the inputs are in the **old** order (endpoint before rank, battery first) in the project file:

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

If `_project_with_inputs` does not yet exist in this file: reuse the existing structure from neighboring tests and extract it as a helper — **do not** create a second copy.

- [ ] **Step 3: Tests laufen lassen**

Run: `uv run pytest tests/export tests/projectsync -v`
Expected: PASS. Both tests should **immediately** turn green — they verify a property Task 2 already established. A failure in the sync test means the assurance from section 5 does not hold: then **stop here** and report, instead of adjusting the test.

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

### Task 4: `endpoint`, `cluster_id`, and `endpoint_label` in `SignalOut`

**Files:**
- Create: `src/loxmatter/profiles/endpoints.py`
- Modify: `src/loxmatter/api/models.py:29-59`
- Modify: `src/loxmatter/api/devices.py` (`_signal_out` and its call site)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/profiles/test_endpoints.py` (neu), `tests/api/test_devices.py`

**Interfaces:**
- Consumes: `StoredDevice.device_types: dict[int, frozenset[int]] | None`, `StoredSignal.ref`.
- Produces:
  - `loxmatter.profiles.endpoints.ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str]`
  - `loxmatter.profiles.endpoints.endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]` — endpoint number → finished, translated name.
  - `SignalOut` additionally carries `endpoint: int`, `cluster_id: int`, `endpoint_label: str`.

- [ ] **Step 1: Write the failing test for `endpoints.py`**

New file `tests/profiles/test_endpoints.py` (GPL header as in neighboring files):

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

**Note:** How `ALL_TYPES` in the installed `matter_server` version is called exactly is documented in `tests/profiles/test_categories.py::test_every_mapped_type_exists_in_the_matter_table`. Copy from there rather than guessing.

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/profiles/test_endpoints.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.profiles.endpoints'`

- [ ] **Step 3: Create the text in `strings.yaml`**

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

- [ ] **Step 4: Write `endpoints.py`**

```python
# <GPL header as in profiles/categories.py, copied unchanged>

"""The human-readable name of ONE endpoint (design 2026-09-07, section 7.4).

Sits beside `categories.py`, not inside it, and that is the entire reason
for this module: `category_for` answers "what kind of thing is this DEVICE"
(light, socket, switch), this file answers "what is this single ENDPOINT
called in it". A remote control is ONE switch with TWO buttons;
`CATEGORY_BY_DEVICE_TYPE` applied to its endpoints would yield "switch 1"
and "switch 2", the same wrong term twice.

The table below is deliberately SMALL. It lists the device types that
actually appear in the checked-in fixtures in tests/fixtures/nodes/, and
nothing else — same standard as `UTILITY_ENDPOINT_KEEP_CLUSTERS` in
`relevance.py`: a new entry needs a concrete device that uses it, not the
assumption that the table is complete by itself. Everything else falls back
to "Endpoint N", and that is a name that always works.
"""

from __future__ import annotations

from collections.abc import Mapping

from loxmatter import i18n
from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES

# Device type → translation key. The numbers come from
# `matter_server.client.models.device_types` as in `categories.py`; the
# comments name the class there.
ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str] = {
    0x000F: "web.signals.endpoint_button",  # GenericSwitch (IKEA BILRESA, Ep 1+2)
    0x010A: "web.signals.endpoint_socket",  # OnOffPlugInUnit (IKEA GRILLPLATS, Ep 1)
    0x010D: "web.signals.endpoint_light",  # ExtendedColorLight (synthetic_color_light, Ep 1)
    0x0510: "web.signals.endpoint_metering",  # ElectricalSensor (GRILLPLATS, Ep 2)
}

# An endpoint that carries only housekeeping is simply called "device" — the
# battery level sits there, and "endpoint 0" would be a number without meaning
# for the user. PowerSource counts here because alone it does not yet make a
# functional endpoint (same reasoning as `_IGNORED_DEVICE_TYPES` in
# categories.py).
_DEVICE_ENDPOINT_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}


def _name_key(declared: frozenset[int]) -> str | None:
    """The key for this endpoint, or `None` for the fallback.

    A functional type beats the housekeeping type: endpoint 0 of the remote
    control declares Root Node AND Power Source AND OTA Requestor — it is
    called "device". If an endpoint carries both housekeeping and a named
    functional type, the functional type wins because it says more.
    """
    for device_type in sorted(declared):
        key = ENDPOINT_NAME_KEY_BY_DEVICE_TYPE.get(device_type)
        if key is not None:
            return key
    if declared & _DEVICE_ENDPOINT_TYPES:
        return "web.signals.endpoint_device"
    return None


def endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]:
    """Endpoint number → finished, translated name.

    Numbering happens only where there is something to distinguish: two
    button endpoints yield "button 1" and "button 2", a single socket endpoint
    stays "socket" — a "1" without a "2" is a number without a counterpart.

    `None` (device types not yet backfilled, see `Store.backfill_device_types`)
    yields an empty mapping; the caller then falls back to `endpoint_plain` for
    each endpoint — the same silent handling that `category_for(None)` gets with
    `OTHER`.
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

- [ ] **Step 5: Run test**

Run: `uv run pytest tests/profiles/test_endpoints.py -v`
Expected: PASS.

- [ ] **Step 6: Write the failing test for `SignalOut`**

Append to `tests/api/test_devices.py` (follow the `api` fixture pattern there; if needed create a second fixture with the button instead of the plug):

```python
async def test_a_signal_carries_its_endpoint_cluster_and_endpoint_label(button_api):
    """The UI groups by endpoint and recognizes the battery level by its
    cluster. Parsing both from `path` ("1/59/2") in JavaScript would mean
    maintaining the decomposition twice — so the API delivers the numbers ready."""
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

- [ ] **Step 7: Run test, verify failure**

Run: `uv run pytest tests/api/test_devices.py -k endpoint_label -v`
Expected: FAIL mit `KeyError: 'endpoint'`

- [ ] **Step 8: Extend `SignalOut`**

Insert in `api/models.py` after `kind: str`:

```python
    # endpoint/cluster_id (design 2026-09-07, section 7.4): `path` carries
    # the same numbers as "1/59/2", but as text. The UI groups by endpoint and
    # recognizes the battery level at cluster 47 — parsing both from `path`
    # would mean maintaining `matter.paths` twice in JavaScript. `endpoint_label`
    # is the human-readable name of that endpoint ("button 1"), translated from
    # `profiles.endpoints`; without backfilled device types it reads "endpoint 1".
    endpoint: int
    cluster_id: int
    endpoint_label: str
```

- [ ] **Step 9: Fill `api/devices.py`**

Add the import:

```python
from loxmatter.profiles.endpoints import endpoint_labels
```

`_signal_out` gets the mapping as a parameter (instead of recalculating it per signal — with 173 signals that would be the same calculation 173 times):

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

At the call site once per device:

```python
    labels = endpoint_labels(device.device_types)
    return [_signal_out(s, values.get(s.key), labels) for s in store.signals(device.id)]
```

Read the existing signature and call site before refactoring — it may differ from the sketch above; in any case, the change is: form `labels` once per device and pass it through.

- [ ] **Step 10: Run tests**

Run: `uv run pytest tests/api tests/profiles -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/profiles/endpoints.py src/loxmatter/api/models.py src/loxmatter/api/devices.py src/loxmatter/i18n/strings.yaml tests/profiles/test_endpoints.py tests/api/test_devices.py
git commit -m "$(cat <<'MSG'
feat(api): Endpoint, cluster, and human-readable endpoint name per signal

`profiles/endpoints.py` answers "what is this one endpoint called", which
`categories.py` does NOT answer: there it is about the whole device.
A remote control is ONE switch with TWO buttons — applying the category to
its endpoints would yield "switch" twice.

Numbering happens only where there is something to distinguish: two
button endpoints become "button 1"/"button 2", a single socket endpoint
stays "socket". Unregistered types and an not-yet-backfilled `device.device_types`
fall back to "endpoint N" — a name that always works.

`endpoint`/`cluster_id` in SignalOut because `path` carries the same
numbers only as text: parsing them in JavaScript would mean maintaining
`matter.paths` twice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 5: The tile — battery row and correct counter

**Files:**
- Modify: `src/loxmatter/web/app.js:1064-1079`
- Modify: `src/loxmatter/web/index.html` (Symbolblock um Zeile 170, Kachel um Zeile 665-690)
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.cluster_id` and `signal.functional` from Task 4.
- Produces: `batterySignalFor(deviceId)`, `previewSignalsFor(deviceId)` in `app.js`; `leadSignalFor`/`restSignalsFor`/`remainingSignalCount` keep names and signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_web.py`:

```python
@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_the_battery_is_never_the_lead_and_never_counted_twice():
    """The three guarantees of the battery row in ONE setup, because they
    belong together: the battery level does not lead, it does not appear in the
    preview, and it is not counted as "remaining".

    The setup is the button: 17 functional signals in the order that the
    cluster ranking delivers them — sixteen switch signals, battery last. Six
    preview rows plus one footer row leaves ten over. If the tile names eleven,
    the battery is counted twice — exactly the bug that the canvas design had.

    As a node run rather than a string search in `app.js`: a search only
    proves that a row is delivered. On 2026-09-05, three such tests let a
    Critical through because they checked exactly the strings that caused
    the bug."""
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


@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_a_mains_powered_device_has_no_battery_row():
    """Without a PowerSource signal, the tile must not show a footer row — and
    the counter must behave the same as before this change."""
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


@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_a_device_whose_only_functional_signal_is_the_battery_has_no_lead():
    """The edge case where the message "no functional signals" would be wrong:
    there IS one, it is just in the footer row."""
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

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/api/test_web.py -k battery -v`
Expected: FAIL — `previewSignalsFor` does not exist.

- [ ] **Step 3: Create the text**

In `strings.yaml`:

```yaml
web.devices.battery_label:
  en: "Battery"
  de: "Batterie"
```

- [ ] **Step 4: Refactor `app.js`**

`functionalSignalsFor` stays unchanged. Insert directly below it and replace `firstSignalsFor`/`remainingSignalCount`:

```js
    // The cluster by which the tile recognizes the battery level. The number
    // is here instead of a title check: the title is freely renameable by the
    // user ("battery", "power"), the cluster is not.
    POWER_SOURCE_CLUSTER: 47,

    // The battery level of the device, or null. It gets its own footer row
    // from the cluster ranking (design 2026-09-07, section 6): with rank 90
    // it stands behind all sixteen other functional signals of the button and
    // would fall out of the six preview rows — it would no longer be visible
    // on the tile at all. That is the price of the ranking, and this is the
    // offsetting entry.
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

    // The functional signals WITHOUT the battery level — the set from which
    // the lead signal, preview rows, and the "+ N more" counter are formed.
    //
    // That all three come from the SAME set is the whole trick: the lead
    // signal can never be the battery (it is not in there at all), and the
    // counter can never count it twice (it is missing from both summands).
    // A special rule in three places would be the same statement three times —
    // and in the first design, exactly one of them was forgotten.
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

- [ ] **Step 5: Create the icon in `index.html`**

To the symbol block (next to `i-kebab`, around line 170):

```html
      <symbol id="i-battery" viewBox="0 0 24 24">
        <rect x="2" y="8" width="16" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.9"/>
        <path d="M21 11v3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>
        <path d="M5 12.5h4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>
      </symbol>
```

- [ ] **Step 6: Put the footer in the tile**

Directly **after** the closing `</div>` of `.value-rows` and **before** `<p class="hint" x-show="!signalsByDevice[device.id]" …>`:

```html
                  <!-- The battery level stands BELOW the preview rows and
                       below the "+ N more" link, not between them (design
                       2026-09-07, section 6). It is not a functional signal
                       like a button press, but a statement about the device
                       itself — and with rank 90 it would have fallen out of
                       the six preview rows. Own row, own symbol, dashed
                       separator: always visible, never a lead signal.

                       `x-show` instead of `x-if`: a mains-powered device has no
                       PowerSource signal and must not grow by one empty row —
                       `.device-battery` therefore carries no own spacing that
                       would remain. -->
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

- [ ] **Step 7: Update the hint condition**

Add the condition to `<p class="hint" … x-text="t('web.devices.no_functional_signals')">`:

```html
                  <p
                    class="hint"
                    x-show="signalsByDevice[device.id] && !leadSignalFor(device.id) && !batterySignalFor(device.id)"
                    x-text="t('web.devices.no_functional_signals')"
                  ></p>
```

And add one sentence to the existing comment above it:

```
                       Since the battery row (design 2026-09-07), "no lead
                       signal" is no longer sufficient as a condition: a device
                       whose only functional signal is the battery has no lead
                       signal — but "no functional signals" would be wrong there,
                       because the footer row below shows one.
```

- [ ] **Step 8: Add to `style.css`**

Insert after `.value-rows .value`:

```css
/* The battery level as its own row at the foot of the preview (design
   2026-09-07, section 6). Set apart with dashing like `.device-controls`
   in the modal — same gesture for the same thing: "belongs here, but is
   a different kind of statement".

   `margin-top` sits on the element, not as `margin-bottom` of the preview:
   a mains-powered device hides this row via `x-show`, and spacing on the
   neighbor would remain as a gap. */
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

/* Right-aligned like in `.value-rows`, so the number aligns with the values
   above — the row is a flex container and does not inherit its grid alignment. */
.device-battery .value {
  flex: 0 0 auto;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--warn);
}
```

- [ ] **Step 9: Run tests**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 10: Test the binding in the harness, not just the function**

The node test from Step 1 proves that `batterySignalFor` delivers the right signal.
It says **nothing** about whether the row appears in the browser: `x-show` on
an element whose expression never matches disappears silently. That is exactly the
gap that let a Critical through on 2026-09-05.

**How the harness is built** (proven 2026-09-06, saves login and iterations):
a `harness.html` in the scratchpad that **extracts the markup block from
`index.html` via Python** — do not type it in, or you test a copy. Alongside
it copy `style.css` and `vendor/alpine.min.js`, and put a mini-`app()` that
carries only the fields the block touches. Before that `python3 -m http.server`.
Two traps: `file://` loads the embedded browser as a static snapshot, Alpine
does **not run there at all** — it must go over http; and a tab that once showed
a local file stays locked to it, so open a new tab for the http URL.

In the harness then read **measured** values, not eyeballs:

```js
const row = document.querySelector(".device-battery");
JSON.stringify({
  exists: !!row,
  visible: row && getComputedStyle(row).display !== "none",
  text: row && row.textContent.replace(/\s+/g, " ").trim(),
  leadSignal: document.querySelector(".lead-label").textContent,
  rest: document.querySelector(".value-rows a.value-key").textContent,
})
```

Expected: `visible` true, `text` contains "Battery" and "12.4", `leadSignal`
is `press`, `rest` names **10** more.

Then load the same harness with a device **without** a PowerSource signal:
`exists` true (the element is in the DOM), `visible` false — and the
tile height must not change compared to the state before this task
(compare `document.querySelector(".device-card").getBoundingClientRect().height`
before/after).

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Battery level as its own tile row instead of a lead signal

The offsetting entry to the cluster ranking: with rank 90, the battery level
stands behind all sixteen other functional signals of the button and would fall
out of the six preview rows — it would no longer be visible on the tile at all.
So it gets its own, always-visible footer row with its own symbol.

Lead signal, preview rows, and the "+ N more" counter all form from
`previewSignalsFor`, which removes the battery. This means the lead signal can
never be the battery and the counter can never count it twice — a special rule
in three places would be the same statement three times, and in the design,
exactly one of them was already forgotten once ("+ 11 more" on a tile showing
seven of 17 signals).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 6: The modal — endpoint groups

**Files:**
- Modify: `src/loxmatter/web/app.js:1446-1451`
- Modify: `src/loxmatter/web/index.html` (the `<summary>` of the signal group)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.endpoint`, `signal.endpoint_label` from Task 4.
- Produces: `signalGroupsFor(deviceId)` yields groups of the form `{key, title, subtitle, collapsible, signals}` — `subtitle` is new, the four other fields keep their meaning and type.

**Deviation from design, intentional:** Section 7.4 draws the subtitles as "Endpoint 1 · Switch (59)". That does not work — endpoint 2 of the socket carries clusters 144 **and** 145, a single cluster name would be wrong there. The subtitle is therefore only "Endpoint N". The cluster appears per row in the expander from Task 8 on, where it belongs.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_the_groups_follow_the_ranking_not_the_endpoint_number():
    """The reason for groups: `press` appears twice in the list — 1/59/1 and
    2/59/1, two different buttons of the same remote control. Without grouping,
    that is the same word twice with no indication which is meant.

    And the order: "device" (endpoint 0, battery only) stands LAST, even
    though it carries the smallest endpoint number — the groups take the order
    of first appearance in the already-ranked list, they do not sort themselves.
    Exactly what a string search in `app.js` cannot prove.

    `t()` returns the key itself when the translation table is not loaded (see
    `t` in app.js) — so the title of the expert group is the key here, and
    that is sufficient for the guarantee."""
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


@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_a_device_without_functional_signals_yields_only_the_expert_group():
    """The state for which the message `none_functional` now stands OUTSIDE
    the group loop: an endpoint group is never empty, there simply are none then."""
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

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/api/test_web.py -k "groups_signals or subtitle" -v`
Expected: FAIL.

- [ ] **Step 3: Create the text**

```yaml
web.signals.group_endpoint_subtitle:
  en: "Endpoint {endpoint}"
  de: "Endpunkt {endpoint}"
```

- [ ] **Step 4: `signalGroupsFor` ersetzen**

```js
    // The groups of the signals modal: one per endpoint, then the expert
    // block (design 2026-09-07, section 7.4).
    //
    // The order of endpoint groups follows the cluster ranking, without
    // sorting here: `functionalSignalsFor` arrives already sorted, and this
    // loop takes the order of FIRST appearance of each endpoint. On the button,
    // "device" (battery only) stands last, even though it is endpoint 0.
    //
    // `group.key` stays stable across redraws ("ep1", "expert") — this is
    // required for `x-init="$el.open = !group.collapsible"` in the markup:
    // if the key were unstable, Alpine would rebuild the node and silently
    // close an open group.
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
      // Remains ONE group: breaking down 156 signals over all endpoints
      // would create only headers, no overview.
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

**Caution: two existing tests break because of this** — they assert that
`signalGroupsFor` yields the "functional" group, and that is exactly what
disappears:

- `tests/api/test_web.py:402` (`assert 't("web.signals.group_functional")' in script`)
- `tests/api/test_web.py:1852` (`assert 'title: t("web.signals.group_functional")' in body`)

Both are from task 12 of the i18n switchover and prove something correct there:
that group titles are translated instead of hardcoded. This guarantee remains
valid and must be kept — it just aims at a title that no longer exists. So
**rewrite, do not delete**: both tests hereafter check `t("web.signals.group_expert")`
and the endpoint subtitle `t("web.signals.group_endpoint_subtitle", ...)`, and
keep their guards against hardcoded strings (`'"Functional"' not in body` goes
away, `'"Expert"' not in body` stays).

The key `web.signals.group_functional` in `strings.yaml` is thereafter read by
nobody and **is removed along with it** — a dead translation key is ballast
that someone will take for a hit next time.

**Caution:** the previous "functional" group disappears. The message
`web.signals.none_functional` was tied to `!group.collapsible && group.signals.length === 0`
— an endpoint group is never empty, it is formed from its signals. The message
therefore must stand **outside** the group loop, see Step 5.

- [ ] **Step 5: Update the markup**

Add the subtitle in the `<summary>`:

```html
                  <summary>
                    <span x-text="group.title"></span>
                    <span class="muted" x-show="group.subtitle" x-text="group.subtitle"></span>
                    <span class="muted" x-text="'(' + group.signals.length + ')'"></span>
                    <svg class="icon chevron" aria-hidden="true"><use href="#i-chevron"></use></svg>
                  </summary>
```

Take the `none_functional` hint out of the group and set it **before** the
`<template x-for="group …">`:

```html
              <!-- Outside the group loop, since groups are formed by endpoint
                   (design 2026-09-07, section 7.4): an endpoint group is never
                   empty, it is formed from its signals. "No functional signals"
                   now means "there is no endpoint group at all". -->
              <p
                class="hint"
                x-show="functionalSignalsFor(signalsModalDevice).length === 0"
                x-text="t('web.signals.none_functional')"
              ></p>
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 7: Check in browser**

Open modal of "Hallway button": three groups — "button 1 (8)", "button 2 (8)",
"device (1)" in that order, below it "expert (156)" collapsed. `press` appears
once under button 1 and once under button 2.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Group signals modal by endpoint

`press` appeared twice in the list — 1/59/1 and 2/59/1, two different buttons
of the same remote control, labeled the same with no indication which is meant.
Now it appears once under "button 1" and once under "button 2".

The order of groups follows the cluster ranking, without sorting here:
`functionalSignalsFor` arrives sorted, and the loop takes the order of first
appearance. On the button, "device" stands last, even though it is endpoint 0.

The subtitle names only the endpoint, not the cluster (deviation from design 7.4):
endpoint 2 of the socket carries clusters 144 AND 145, a single cluster name
would be wrong there.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 7: The modal — table grid with column headers, both yes/no columns as checkboxes

**Files:**
- Modify: `src/loxmatter/web/index.html` (signal row in modal)
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: the groups from Task 6.
- Produces: CSS class `.signal-grid` with `grid-template-columns: 58px minmax(0, 1fr) 150px 70px 76px 28px`, used by header and each data row.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_signal_rows_and_the_header_share_one_grid(api):
    """What is missing today and why nothing aligns: the row is a
    `flex-wrap` container without column widths. At `multipress_ongoing`,
    "periodic resend" alone wrapped to the next row."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    assert page.count("signal-grid") >= 2
    assert "grid-template-columns: 58px minmax(0, 1fr) 150px 70px 76px 28px" in css


async def test_both_boolean_columns_are_checkboxes(api):
    """The control follows the CONTAINER, not the meaning: in a table,
    checkboxes, because they align in a column and stay quiet — a column
    of 17 toggles would be a much louder texture, and loudness is exactly
    the problem of this modal."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    modal = page[page.index('class="signals-modal"') :]
    for handler in ("toggleExported(signal)", "toggleResend(signal)"):
        # The control that carries the handler: backwards from the handler
        # to the opening tag. So the test checks the actual element and not
        # some `type="checkbox"` elsewhere in the modal.
        end = modal.index(handler)
        element = modal[modal.rindex("<", 0, end) : end]
        assert 'type="checkbox"' in element, handler


async def test_the_boolean_columns_keep_a_label_for_assistive_technology(api):
    """The label stands as a column header once instead of seventeen times
    next to a box — a screen reader reads the row, not the table. Both boxes
    therefore still need their own name."""
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

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/api/test_web.py -k "grid or boolean or resend_column" -v`
Expected: FAIL.

- [ ] **Step 3: Create the text**

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

- [ ] **Step 4: Put the header in the markup**

Directly **before** the `<template x-for="group …">`, after the `none_functional` hint:

```html
              <!-- The column header sticks at the top when scrolling: without
                   it, the two checkbox columns lose their meaning once it
                   disappears with 173 signals. It carries the SAME
                   `grid-template-columns` as each data row — this is the whole
                   reason the table aligns, and the test `test_the_signal_rows_
                   and_the_header_share_one_grid` guards exactly this equality. -->
              <div class="signal-grid signal-grid-head">
                <span x-text="t('web.signals.col_export')"></span>
                <span x-text="t('web.signals.col_signal')"></span>
                <span x-text="t('web.signals.col_input')"></span>
                <span class="col-right" x-text="t('web.signals.col_value')"></span>
                <span class="col-center" x-text="t('web.signals.col_resend')"></span>
                <span></span>
              </div>
```

And the explanation above it, beside the existing `key_hint`:

```html
          <p class="hint">
            <span x-text="t('web.signals.key_hint')"></span>
            <span x-text="t('web.signals.resend_explanation')"></span>
          </p>
```

- [ ] **Step 5: Rebuild the signal row**

Replace the current `<div class="device-controls"><div class="row">…</div>…</div>` with:

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
                      <!-- Same control as in the export column, and that is
                           intentional: both are a yes/no per signal in the same
                           row. The first design had a toggle here — two controls
                           for the same thing, exactly the inconsistency that makes
                           this modal hard to read. A toggle belongs in a detail
                           area with an explanatory sentence beside it, not in a
                           table column. -->
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

The last cell temporarily carries the `reason` badge; Task 8 puts the expander
there and moves `reason` into its content.

- [ ] **Step 6: Add to `style.css`**

```css
/* A grid for header AND data row (design 2026-09-07, section 7.2). Both MUST
   carry the same template — that is exactly what was missing, and why nothing
   aligned: the old `.row` was a `flex-wrap` container without column widths,
   at `multipress_ongoing` the second label alone wrapped to the next row.

   The 150 px of the key column is measured from `d4_1_multipress_ongoing`,
   the longest key in the test fixture. */
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

/* The generic rule `input[type="text"] { min-width: 12rem }` above otherwise
   beats the grid column and breaks it — same trap as with `.device-head .device-name`,
   reason there. */
.signal-grid .signal-title {
  width: 100%;
  min-width: 0;
}
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

- [ ] **Step 8: Measure alignment, do not look**

"Alignment" is the guarantee of this task, and with the eye it cannot be proven
with two rows — the old wrapping at `multipress_ongoing` only became noticeable
in the long list.

**How the harness is built** (proven 2026-09-06, saves login and iterations):
a `harness.html` in the scratchpad that **extracts the markup block from
`index.html` via Python** — do not type it in, or you test a copy. Alongside
it copy `style.css` and `vendor/alpine.min.js`, and put a mini-`app()` that
carries only the fields the block touches. Before that `python3 -m http.server`.
Two traps: `file://` loads the embedded browser as a static snapshot, Alpine
does **not run there at all** — it must go over http; and a tab that once showed
a local file stays locked to it, so open a new tab for the http URL.

In the harness fill the modal with **all 17** functional signals and measure:

```js
const rows = [...document.querySelectorAll(".signal-row-cells")];
const head = document.querySelector(".signal-grid-head");
const leftEdges = (el) => [...el.children].map((c) => Math.round(c.getBoundingClientRect().left));
const expected = leftEdges(head);
JSON.stringify({
  rows: rows.length,
  misaligned: rows.filter((r) => String(leftEdges(r)) !== String(expected)).length,
  heights: [...new Set(rows.map((r) => Math.round(r.getBoundingClientRect().height)))],
})
```

Expected: `misaligned` **0** — each data row shares the column edges of the
header. `heights` must contain **one** value: multiple heights mean at least
one row wraps, exactly the old state.

Plus the guard against horizontal overflow:
`document.querySelector(".signals-modal").scrollWidth <= clientWidth`.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Signals modal as aligned table with column headers

The old row was a `flex-wrap` container without column widths: at
`multipress_ongoing`, "periodic resend" alone wrapped to the next row, and
`1/59/2` stood without comment. Header and data row now carry the SAME
`grid-template-columns` — this is the whole reason the table aligns, and a
test guards the equality.

Both yes/no columns are checkboxes. The rule behind it: the control follows
the CONTAINER, not the meaning. In a table, checkboxes — they align in a
column and stay quiet; a column of 17 toggles would be a much louder texture,
and loudness is exactly the problem of this modal. A toggle belongs in a detail
area with an explanatory sentence beside it.

What "periodic" means stands once above the table instead of seventeen times
next to a box. The two translation keys remain as `aria-label`/`title` — a
screen reader reads the row, not the table.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 8: The modal — origin and raw write in row expander

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signal.endpoint`, `signal.cluster_id`, `signal.path`, `signal.reason`.
- Produces: `expandedSignalKey` (Alpine field, `null` or a key), `toggleSignalDetails(signal)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_only_one_signal_detail_is_open_at_a_time(api):
    """Unlike the tile menu and signal groups, this state lives in Alpine,
    not in the DOM: there is exactly ONE value for the whole modal, no open/close
    per element."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    assert "expandedSignalKey: null," in script
    body = script[script.index("toggleSignalDetails(signal)") :][:400]
    assert "this.expandedSignalKey = " in body


async def test_the_raw_write_field_is_no_longer_a_row_of_its_own(api):
    """Before, the raw write field took up a full row for every attribute —
    a tool for experimenting with the same weight as everything else."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'x-show="signal.kind === \'attribute\'"' not in page
    assert 'x-show="expandedSignalKey === signal.key"' in page


async def test_the_detail_spells_out_the_path(api):
    """The path `1/59/1` finally gets a place where there is enough room to
    spell it out, instead of putting it as a riddle next to the name."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.origin" in page
```

- [ ] **Step 2: Run tests, verify failure**

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

- [ ] **Step 4: Add to `app.js`**

To the Alpine fields (beside `titleDrafts`, `rawWriteDrafts`):

```js
    // Which signal row has its expander open, or null.
    //
    // Unlike the tile menu and signal groups, this state lives in Alpine, not
    // in the DOM, and the difference has a reason: there is an open/close PER
    // ELEMENT, here exactly ONE value for the whole modal. At most one area is
    // open — with 173 rows, multiple open expanders would be the wall again,
    // which this redesign removes.
    expandedSignalKey: null,
```

Among the signal helpers:

```js
    toggleSignalDetails(signal) {
      this.expandedSignalKey = this.expandedSignalKey === signal.key ? null : signal.key;
    },

    // The origin in plain text. `signal.path` carries the same information as
    // "1/59/1", but that is a riddle, as long as nobody says what each number
    // means — in the expander there is finally room to spell it out.
    signalOriginText(signal) {
      return t("web.signals.origin", {
        endpoint: signal.endpoint,
        cluster: signal.cluster_id,
        element: signal.path.split("/")[2],
      });
    },
```

- [ ] **Step 5: Rebuild the markup**

Replace the last grid cell of the signal row (Task 7, Step 5):

```html
                      <button
                        class="signal-more"
                        :aria-expanded="expandedSignalKey === signal.key"
                        :aria-label="t('web.signals.row_details')"
                        :title="t('web.signals.row_details')"
                        @click="toggleSignalDetails(signal)"
                      ><svg class="icon" aria-hidden="true"><use href="#i-kebab"></use></svg></button>
```

And **behind** the `.signal-grid` row, still within the same `x-for` root element
— for this, wrap row and expander in a container:

```html
                  <template x-for="signal in group.signals" :key="signal.key">
                    <div class="signal-row-wrap" :class="{ 'is-expanded': expandedSignalKey === signal.key }">
                      <div class="signal-grid signal-row-cells">
                        <!-- … the six cells from Task 7 … -->
                      </div>
                      <!-- The expander hangs directly BELOW its row, not as a
                           second `.row` beside it: it belongs to this one signal,
                           and that should be visible. -->
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

The raw write field makes sense only for attributes — so the `.row` gets
`x-show="signal.kind === 'attribute'"` **inside** the expander (the origin
appears also for an event).

- [ ] **Step 6: Add to `style.css`**

```css
.signal-row-wrap.is-expanded {
  background: var(--bg);
}

/* Indented to the name column (58 px checkboxes + 0.5 rem gap), so the
   expander is visibly part of ITS row, not the table. */
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

- [ ] **Step 8: Check in browser**

Click the kebab of an attribute row: the area opens directly below that row,
names "Endpoint 1 · Cluster 59 · Element 1", and carries the raw write field.
Clicking a second kebab closes the first. For an event row, the raw write field
is absent, but the origin is still there.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Origin and raw write in row expander per signal

The raw write field took a full row for every attribute — a tool for
experimenting with the same weight as everything else. It now hangs directly
below its row, behind the kebab.

This also solves the second problem: `1/59/1` stood without comment next to
the name, and in the expander there is finally room to spell it out as
"Endpoint 1 · Cluster 59 · Element 1".

At most one area is open, and the state lives in Alpine, not the DOM — unlike
the tile menu and signal groups, there is exactly ONE value for the whole
modal. With 173 rows, multiple open expanders would be the wall again, which
this redesign removes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 9: The modal — the number in the head

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `signalsByDevice`, `toggleExported`.
- Produces: `exportedSignalCount(deviceId)`, `deselectAllSignals(deviceId)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_modal_leads_with_the_number_the_user_came_for(api):
    """You open this modal to see and change what goes to Loxone. That number
    stood nowhere before."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.export_summary" in page
    assert "exportedSignalCount(signalsModalDevice)" in page


@pytest.mark.skipif(NODE is None, reason="node is needed for this test")
def test_deselect_all_empties_the_selection_instead_of_inverting_it():
    """A `toggleExported` over ALL signals would have inverted the selection —
    but the button says "deselect all", not "invert". So a second click must do
    nothing.

    `toggleExported` is replaced here because it calls the route: the test checks
    the selection rule of this loop, not the write path."""
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
        // Async IIFE, because `node -e` runs as CommonJS and does not allow
        // `await` at the top level — `deselectAllSignals` is async.
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

    # "c" is `exported`, but does not fit any Loxone input — it does not count,
    # just as `to_inputs` skips it server-side.
    assert values["before"] == 1
    assert values["total"] == 3
    assert values["after"] == 0
    # "b" was already off and must not have been touched.
    assert "b" not in values["firstRun"]
    assert values["secondRunTouched"] == 0
```

- [ ] **Step 2: Run tests, verify failure**

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

- [ ] **Step 4: Add to `app.js`**

```js
    // How many signals of this device actually go to Loxone as an input.
    // `exported` alone is not enough: a signal whose value does not fit any
    // Loxone input (`exportable === false`) does not produce one — the same
    // distinction that `to_inputs` makes server-side.
    exportedSignalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((s) => s.exported && s.exportable).length : 0;
    },

    signalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.length : 0;
    },

    // Only what is ON is turned OFF. A `toggleExported` over all signals would
    // invert the selection instead of emptying it — but the button says
    // "deselect all", not "invert".
    async deselectAllSignals(deviceId) {
      const signals = this.signalsByDevice[deviceId] || [];
      for (const signal of signals) {
        if (signal.exported) {
          await this.toggleExported(signal);
        }
      }
    },
```

**Read `toggleExported` before writing:** if there is optimistic toggling of the
local object before the `PATCH`, the loop above is correct. If `toggleExported`
relies on `$event.target.checked`, `deselectAllSignals` must call the route
directly instead — then rebuild `toggleExported` so it takes the target state
as a parameter, instead of making a second copy of the write path.

- [ ] **Step 5: Add the markup**

After the `<div class="signals-modal-head">`, before the `key_hint` paragraph:

```html
          <!-- The one number for which you open this modal. It stood nowhere
               before — you had to count 17 checkboxes. -->
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

- [ ] **Step 6: Add to `style.css`**

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

- [ ] **Step 7: Run tests and check in browser**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS.

In browser: "12 of 17 signals go to Loxone as an input". Deselect one checkbox
→ the number falls to 11. "Deselect all" → 0, and a second click leaves it at 0
(no inversion).

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Export count in the head of the signals modal

You open this modal to see and change what goes to Loxone — and that exact
number stood nowhere before. You had to count 17 checkboxes.

Counted is `exported && exportable`, not `exported` alone: a signal whose value
does not fit any Loxone input does not produce one — the same distinction that
`to_inputs` makes server-side.

"Deselect all" only turns OFF what is ON. A `toggleExported` over all signals
would invert the selection, but the button does not say "invert".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 10: Wrap under 640 px

**Files:**
- Modify: `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `.signal-grid` from Task 7.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

```python
async def test_the_signal_table_stacks_on_a_narrow_screen(api):
    """Six columns do not fit under about 640 px. Without this wrap, the table
    frays there again — exactly the state that the whole redesign eliminated,
    just on a phone."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    assert "@media (max-width: 640px)" in css
    narrow = css[css.index("@media (max-width: 640px)") :][:900]
    assert ".signal-grid-head" in narrow
    assert "display: none" in narrow
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/api/test_web.py -k narrow -v`
Expected: FAIL.

- [ ] **Step 3: Write the rules**

```css
/* Six columns do not fit here (design 2026-09-07, section 10). The row becomes
   a stacked card, and the column header disappears — it labels nothing over a
   stacked card, it would just stand as a row of five words with no connection.

   So the checkboxes keep their meaning without the column header, they get
   their label back here: `.col-center` becomes left-aligned and the `title`
   attribute of the checkbox appears as text beside it. That is the same
   information as in the column header, just at a different place — no second
   translation key. */
@media (max-width: 640px) {
  .signal-grid-head {
    display: none;
  }

  .signal-grid {
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.3rem 0.5rem;
    padding: 0.5rem 0;
  }

  /* Name and key get the full width, the three short cells share the row
     below. */
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

- [ ] **Step 5: Measure at 380 px**

Same harness as Task 7, but set the window to **380 px** (`resize_window` with
`preset: "mobile"`), reload the page — the media query only reliably takes
effect after reload — and read:

```js
const modal = document.querySelector(".signals-modal");
const rows = [...document.querySelectorAll(".signal-row-cells")];
JSON.stringify({
  overflow: modal.scrollWidth - modal.clientWidth,
  headerVisible: getComputedStyle(document.querySelector(".signal-grid-head")).display,
  checkboxes: document.querySelectorAll('.signal-row-cells input[type="checkbox"]').length,
  smallestHitArea: Math.min(
    ...[...document.querySelectorAll('.signal-row-cells input[type="checkbox"]')]
      .map((c) => Math.round(c.getBoundingClientRect().width)),
  ),
})
```

Expected: `overflow` **0**, `headerVisible` `"none"`, `checkboxes` equal
`2 × row count` (both yes/no columns remain usable, they do not disappear with
the header), `smallestHitArea` > 0.

At the end, reset `resize_window` to `preset: "desktop"` — a set size sticks
to the tab otherwise.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'MSG'
feat(web): Signals table as stacked cards under 640 px

Six columns do not fit there — without this wrap, the table frays on a phone
again, exactly the state that the redesign eliminated.

The column header disappears with it: it labels nothing over a stacked card.
The checkboxes keep their meaning via the `title` that they carry anyway for
assistive technology — no second translation key for the same information.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 12: Rank per element — the button must lead with `press`

**Added on September 8, 2026**, after Task 11 revealed the finding in the
finished image. The design (section 4, "Why no rank per element") explicitly
left this level open: it "can be added when a concrete device requires it".
That is exactly what happened.

**The finding.** The tile of the button does not lead with `press`, but with
`positions`:

```
 0. ep1 cl59 el0 attribute  positions   <== lead signal
 1. ep1 cl59 el1 attribute  position
 2. ep1 cl59 el1 event      press
```

`positions` is Matter's `NumberOfPositions` — the static statement that this
button has two positions. A configuration value that never changes. As a lead
signal that is **worse than the battery level**, which this entire plan aimed
to eliminate: at least the battery fell.

**Why nobody noticed.** The test from Task 2 reads

```python
assert functional[0].ref.cluster_id == 59
```

That is equally true for `positions` as for `press`. It checks the cluster,
while the design and canvas consistently show `press` — a gap between intent
and guarantee. This task closes it.

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml` (only cluster 59)
- Modify: `src/loxmatter/profiles/table.py`
- Modify: `src/loxmatter/model/store.py` (`_signal_order`)
- Test: `tests/profiles/test_table.py`, `tests/model/test_store.py`

**Interfaces:**
- Consumes: `table.rank_for`, `table.DEFAULT_RANK` (Task 1); `_signal_order` (Task 2).
- Produces: `table.element_rank_for(ref: SignalRef) -> int`; `_signal_order` yields a SIX-PART tuple `(cluster_rank, endpoint, cluster_id, element_rank, element_id, kind)`.

- [ ] **Step 1: Write the failing tests**

An `tests/profiles/test_table.py`:

```python
def test_an_element_can_carry_its_own_rank():
    """The button was the concrete case that made this level necessary: within
    cluster 59, the button press must come before the static statement
    `NumberOfPositions`, or the tile leads with a number that never changes."""
    press = SignalRef(1, 59, 1, SignalKind.EVENT)
    positions = SignalRef(1, 59, 0, SignalKind.ATTRIBUTE)

    assert table.element_rank_for(press) < table.element_rank_for(positions)


def test_an_element_without_a_rank_gets_the_default():
    """The same rule as at the cluster level, for the same reason: the middle,
    so an unregistered element neither shoots to the front nor falls to the back."""
    longpress = SignalRef(1, 59, 2, SignalKind.EVENT)
    assert table.element_rank_for(longpress) == table.DEFAULT_RANK


def test_an_element_of_an_unknown_cluster_gets_the_default():
    """Cluster 3 (Identify) is not in the table — there is neither a section
    nor an element where a rank could stand."""
    assert table.element_rank_for(SignalRef(1, 3, 0, SignalKind.ATTRIBUTE)) == table.DEFAULT_RANK
```

To `tests/model/test_store.py` — **and the existing, too-weak test is replaced
by it**, not augmented:

```python
def test_the_button_leads_with_the_button_press(tmp_path):
    """Replaces `test_the_button_leads_with_a_switch_signal_not_the_battery`,
    which only checks `cluster_id == 59`. That was too weak: `positions`
    (NumberOfPositions, element 0) carries the same cluster and sorted before —
    the tile led with the static statement that this button has two positions.
    The test said yes anyway.

    This version names the signal. A test that only checks the cluster lets
    exactly the bug through that the whole redesign aims to prevent."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].title == "press"
    assert functional[0].ref.kind is SignalKind.EVENT
    assert functional[-1].ref.cluster_id == 47


def test_the_static_position_count_sorts_behind_every_button_event(tmp_path):
    """`positions` never changes — it belongs at the end of the button group,
    not at its beginning."""
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

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/profiles/test_table.py -k element_rank tests/model/test_store.py -k "leads_with_the_button or static_position" -v`
Expected: FAIL — `element_rank_for` does not exist, and the lead signal is `positions`.

- [ ] **Step 3: Add ranks to `clusters.yaml`**

**Only cluster 59.** No other cluster gets element ranks, as long as no device
requires them — same restraint as with `UTILITY_ENDPOINT_KEEP_CLUSTERS` in
`relevance.py`.

```yaml
  59:
    name: switch
    rank: 10
    attributes:
      # `rank` per element orders WITHIN this cluster (addition 2026-09-08).
      # Cluster 59 is the only one that needs it so far, and the reason is
      # NumberOfPositions: the statement of how many positions this button has
      # never changes. It stood at element ID 0 ahead of every button press and
      # became the lead signal of the tile — a constant as the most important
      # feature of a button.
      0: {slug: positions, unit: "", rank: 90}
      1: {slug: position, unit: ""}
    events:
      # The button press is what you commission a button for.
      1: {slug: press, rank: 10}
      2: {slug: longpress}
      3: {slug: shortrelease}
      4: {slug: longrelease}
      5: {slug: multipress_ongoing}
      6: {slug: multipress}
```

The existing keys of each element (`slug`, `unit`, …) stay unchanged — only
`rank` is added, and only to two of them.

- [ ] **Step 4: `element_rank_for` in `table.py` schreiben**

Neben `rank_for`:

```python
def element_rank_for(ref: SignalRef) -> int:
    """How important this element is WITHIN its cluster.

    Second level beside `rank_for`, and it was added later rather than from
    the start (design 2026-09-07, section 4: "can be added when a concrete
    device requires it"). The device that required it is the IKEA button:
    `NumberOfPositions` (element 0) carries the same cluster as the button press
    and sorted ahead with the smaller element ID — the tile led with a constant.

    The rule is the same as at the cluster level and for the same reason, the
    middle: an unregistered element should neither shoot to the front nor fall
    to the back. The vast majority of elements therefore carry no rank at all,
    and the element ID continues to order them — as it correctly did for every
    cluster except 59 up to here.
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

- [ ] **Step 5: Extend `_signal_order`**

In `store.py`, add `element_rank_for` to the import and make the key six parts
— **the element rank stands behind `cluster_id` and before `element_id`**,
because it orders within a cluster:

```python
def _signal_order(signal: StoredSignal) -> tuple[int, int, int, int, int, str]:
    """The sort key of the signal list (design 2026-09-07, section 4, with
    element rank as an addition from 2026-09-08).

    Two rank levels, and their position in the tuple is the whole statement:
    the CLUSTER rank stands up front and orders clusters relative to each other
    (that is why PowerSource falls behind all functional signals); the ELEMENT
    rank stands behind `cluster_id` and orders only within the same cluster
    (that is why `positions` falls behind every button press, without the button
    group as a whole changing its place).

    Sorting happens in Python, not SQL, because both ranks come from
    `clusters.yaml`: SQLite does not know them, and mirroring them as columns
    in `signal` would mean backfilling them every time the YAML file changes —
    a second source of truth for the same value.

    The later parts are the previous key. Because of the UNIQUE constraint on
    `signal`, it is already unique, so this key is also total — the order never
    flutters, which matters for export (it writes it to a file).
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

- [ ] **Step 7: The whole test suite**

Run: `uv run pytest -q`
Expected: PASS. If a test fails that asserts the old order, the same rule from
Task 2 applies: **integrate, do not delete** — if it holds the order by chance,
it can be updated; if it holds it intentionally, it is a conflict and will be
reported.

Especially check: `tests/export/test_signals.py::test_the_template_lists_the_button_press_before_the_battery`
and the project file sync test from Task 3 — both depend on this order and
should stay green.

- [ ] **Step 8: Prove that the new test catches the old bug**

Temporarily remove `rank: 90` from `positions`, run `uv run pytest tests/model/test_store.py -k leads_with_the_button -v`, show the failure, then restore the rank. Without this proof, the test is just a claim.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/profiles/table.py src/loxmatter/model/store.py tests/profiles/test_table.py tests/model/test_store.py
git commit -m "$(cat <<'MSG'
fix(profiles): Rank per element — button leads with `press`, not `positions`

The cluster ranking displaced the battery level from the tile, but did not put
the right thing in its place: the button afterward led with `positions` — Matter's
NumberOfPositions, the static statement that this button has two positions.
A value that never changes, as the most important feature of a button; that was
worse than the battery level, which this redesign aimed to eliminate, because
at least the battery fell.

It was only noticed in the finished screenshot. The test from Task 2 let it
through because it checks `functional[0].ref.cluster_id == 59` — equally true
for `positions` as for `press`. It now names the signal; a test that only checks
the cluster lets exactly the bug through that the redesign aims to prevent.

The design explicitly left this level open ("can be added when a concrete device
requires it"). The IKEA button requires it. It is registered only for cluster 59
and only at two elements — the same restraint as UTILITY_ENDPOINT_KEEP_CLUSTERS:
a new entry needs a device that uses it, not the assumption that the table is
complete by itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 11: Update screenshots

**Files:**
- Modify: `docs/screenshots/dashboard.png`, `docs/screenshots/signals.png`

**Interfaces:**
- Consumes: everything before.
- Produces: nothing.

- [ ] **Step 1: Run the whole test suite**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Capture screenshots again**

Run: `uv run python scripts/capture_screenshots.py`

The file states in its header comment what it needs (browser, port). If it fails,
read its comment rather than guessing.

- [ ] **Step 3: Look at the new images**

`docs/screenshots/dashboard.png` must show: "Hallway button" leads with `press`,
below it the battery row, "+ 10 more".
`docs/screenshots/signals.png` must show: column headers, three endpoint groups,
aligned columns, both yes/no columns as checkboxes.

If an image still shows the old state, the script captured a cached state — kill
the process, delete the `~/.loxmatter` copy of the dev service (the script puts
it in a temp directory, see its head) and run again.

- [ ] **Step 4: Commit**

```bash
git add docs/screenshots
git commit -m "$(cat <<'MSG'
docs(screenshots): Update to signal ranking

The device view now shows `press` as the button's lead signal instead of the
battery level, and the signals modal shows its endpoint groups with aligned
columns.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

## Plan self-check

**Design coverage:**

| Design section | Task |
| --- | --- |
| 1 The finding | 2 (test), 5 (tile) |
| 2 The modal | 6–10 |
| 3 Decisions | all |
| 4 The ranking | 1 |
| 5 Sorted at the source | 2, 3 |
| 6 The tile | 5 |
| 7.1–7.2 Layout, columns | 7 |
| 7.3 Both checkboxes | 7 |
| 7.4 Endpoint groups | 4, 6 |
| 7.5 Raw write field | 8 |
| 7.6 The head | 9 |
| 8 What stays unchanged | no task touches `relevance.py`, `categories.py`, `exported`, `exportability`, or `FUNCTIONAL_PREVIEW_LIMIT` |
| 9 Tests | spread across tasks, each design point has one |
| 10.1 Wrap under 640 px | 10 |
| 10.2 Ranks as first implementation | in the comment of `clusters.yaml`, Task 1 Step 3 |

**Known design deviation:** the group subtitle names only the endpoint, not additionally the cluster (Task 6, with reasoning). The cluster appears per row in the expander from Task 8 on.

**Name consistency:** `previewSignalsFor` (Task 5) is used by `firstSignalsFor` and `remainingSignalCount` of the same task. `batterySignalFor` (Task 5) is used in Task 5 markup. `endpoint_labels` (Task 4) is called only in `api/devices.py` of the same task. `signal.cluster_id`/`signal.endpoint`/`signal.endpoint_label` (Task 4) are read in Tasks 5, 6, and 8 — Task 4 comes before.

**Two places where the implementing developer must read before writing** (noted in the plan): the signature of `_signal_out` and its call site (Task 4, Step 9) and the behavior of `toggleExported` (Task 9, Step 4). Both are in the codebase and may differ from the sketches.
