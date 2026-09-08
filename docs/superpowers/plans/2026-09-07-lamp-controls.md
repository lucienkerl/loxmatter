# Controls for Lamps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The device tile gets controls that match a command's value type — including a colour picker — and the colour path `MoveToHueAndSaturation` (768/6) is enabled for WebUI and Loxone.

**Architecture:** `profiles/clusters.yaml` stays the single source: a new command field `control:` determines the widget, a new attribute field `functional: false` allows reading device constants without exporting them. The UI reads only `control` and compares no slugs. WebUI and Loxone continue to share `to_matter_call` as their one translator.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PyYAML, pytest (asyncio_mode=auto), Alpine.js (vendored), uv.

**Spec:** [2026-09-07-lamp-controls-design.md](../specs/2026-09-07-lamp-controls-design.md)

## Global Constraints

- **Dev prose in English.** Docstrings, comments, and commit messages in English, no umlauts in commit messages (see existing history). Code identifiers in English.
- **Every new UI string needs both `en` AND `de`** in `src/loxmatter/i18n/strings.yaml`.
- **Allowlist, not a blocklist.** Only enable what is proven against a real device — or explicitly marked "spec only".
- **`ruff` (line-length 100) and `mypy --strict`** must pass over `src` and `scripts`.
- **No second translation path.** Everything value-bearing goes through `commands.translate.to_matter_call`.
- **Check command after every task:** `uv run pytest -q && uv run ruff check . && uv run mypy`

---

## File Overview

| File | Responsibility | Task |
| --- | --- | --- |
| `tests/fixtures/nodes/ikea_*_cct.json`, `ikea_*_rgbw.json` | Evidence from the real lamps | 1 |
| `src/loxmatter/commands/color.py` | Colour-space and Loxone encoding | 2 |
| `src/loxmatter/commands/translate.py` | Desired state → Matter command | 3 |
| `src/loxmatter/profiles/clusters.yaml` | Table: slugs, units, `control`, `functional` | 3, 4, 5 |
| `src/loxmatter/profiles/table.py` | Access to the table | 4, 5 |
| `src/loxmatter/profiles/relevance.py` | What's wanted by default | 5 |
| `src/loxmatter/api/models.py` | `CommandOut` | 6 |
| `src/loxmatter/api/control.py` | Controls route, command execution | 6 |
| `src/loxmatter/loxone/server.py` | Router wiring | 6 |
| `src/loxmatter/web/{index.html,app.js,style.css}` | Tile and control modal | 7, 8 |
| `src/loxmatter/i18n/strings.yaml` | Translations | 7, 8 |

---

### Task 1: Fixtures from the real lamps — the gate

Without this task, every allowlist entry is a guess. **If the check in Step 4 finds that command 6 is missing, the plan halts here** and the spec gets updated (Spec 4.1, 10.2).

**Files:**
- Create: `tests/fixtures/nodes/ikea_kajplats_ws_lamp.json`
- Create: `tests/fixtures/nodes/ikea_kajplats_cws_lamp.json`
- Modify: `tests/profiles/test_real_device_fixtures.py`

**Interfaces:**
- Produces: two fixture files in the format `{"node_id", "available", "attributes"}`, loadable via the `load(name)` helper already there.

- [x] **Step 1: Pull both nodes — DONE on 7 September 2026**

Pulled from `ws://10.0.1.56:5580/ws` (the Pi from `deploy/testhost/README.md`). Nine nodes in total; the two lamps sit in the scratchpad under `nodes/`:

| File in the scratchpad | Node | Device |
| --- | --- | --- |
| `node_14_ikea_of_sweden_kajplats_e27_ws_g60_clear_470lm.json` | 14 | KAJPLATS E27 WS G60 clear 470lm (white tone) |
| `node_21_ikea_of_sweden_kajplats_e14_cws_globe_806lm.json` | 21 | KAJPLATS E14 CWS globe 806lm (colour) |

Findings the rest of the plan rests on:

| | WS (Node 14) | CWS (Node 21) |
| --- | --- | --- |
| ColorControl endpoint | 1 | 1 |
| AcceptedCommandList | `[7, 8, 9, 10, 71, 75, 76]` | `[0…10, 64…68, 71, 75, 76]` |
| FeatureMap | 24 = XY\|CT | 31 = HS\|EHUE\|ColorLoop\|XY\|CT |
| Command 6 (Hue/Sat) | **missing** | **present** ✓ |
| PhysMin/Max Mired | 153 / 454 | 153 / 555 |
| Kelvin derived from that | 2202–6535 K | 1801–6535 K |

**The gate is therefore open:** the CWS lamp accepts command 6, the planned enabling is proven.

- [x] **Step 2: Check for non-public content — DONE**

Both snapshots checked: no IPv4 addresses, no serial number (`0/40/15` missing), `NodeLabel` empty, `Location` = `"XX"`. `0/40/18` (UniqueID) stays in - the same call as with the checked-in `ikea_grillplats_plug.json`.

To do: copy the two files to `tests/fixtures/nodes/ikea_kajplats_ws_lamp.json` and `ikea_kajplats_cws_lamp.json` respectively, and prepend each with a `"_comment"` field naming the capture date, source (`ws://10.0.1.56:5580/ws`), and device — pattern: the existing fixtures.

- [ ] **Step 3: Write the evidence test**

Append to `tests/profiles/test_real_device_fixtures.py`:

```python
def test_rgbw_lamp_accepts_move_to_hue_and_saturation():
    """The evidence on which the enabling of (768, 6) rests (spec 4.1).

    If this test fails, the design is wrong - then the lamp
    expects MoveToColor (7, xy) and there is a missing color-space
    conversion that does not exist anywhere in the project (spec 10.2)."""
    snap = load("ikea_kajplats_cws_lamp.json")
    accepted = snap.attributes["1/768/65529"]
    assert 6 in accepted


def test_both_lamps_report_their_physical_colour_temperature_limits():
    """Without these two attributes, `range` would stay empty and the
    kelvin slider unbounded (spec 6.4)."""
    for name in ("ikea_kajplats_ws_lamp.json", "ikea_kajplats_cws_lamp.json"):
        snap = load(name)
        assert isinstance(snap.attributes["1/768/16395"], int)
        assert isinstance(snap.attributes["1/768/16396"], int)


def test_the_ws_lamp_has_no_hue_saturation_command():
    """Proves the distinction from spec 6.3: the WS lamp gets no
    color tab because it has no hue/sat command - not because the code
    knows its model.

    It does support MoveToColor (7) and thus the XY color space
    (FeatureMap 24 = XY|CT). That stays deliberately unused: an
    xy conversion does not exist in the project, and for a white-tone
    lamp it would be a control for a capability that nobody expects
    from it."""
    accepted = load("ikea_kajplats_ws_lamp.json").attributes["1/768/65529"]
    assert 6 not in accepted
    assert 10 in accepted
    assert 7 in accepted  # XY present, but not enabled


def test_the_cws_lamp_advertises_the_full_colour_feature_set():
    """FeatureMap 31 = HS|EHUE|ColorLoop|XY|CT - the basis for
    exactly this lamp getting both tabs and the WS lamp not."""
    assert load("ikea_kajplats_cws_lamp.json").attributes["1/768/65532"] == 31
```

- [ ] **Step 4: Run the test — the gate**

Run: `uv run pytest tests/profiles/test_real_device_fixtures.py -v`
Expected: all three new tests PASS.

If `test_rgbw_lamp_accepts_move_to_hue_and_saturation` fails: **stop**, report the finding, update the spec. If `test_the_cct_lamp_offers_no_colour_command` fails (the CCT lamp can do colour after all), that is not a fault of the plan — then adjust the test to the finding and continue.

- [ ] **Step 5: Retire the synthetic fixture**

`synthetic_color_light.json` currently sits in two places:

- `tests/profiles/test_relevance.py:198` (via `_snapshot`)
- `tests/profiles/test_categories.py:80` (via `load_snapshot`)

Switch both to `ikea_kajplats_cws_lamp.json`. Adjust the expected values
**to match the real device, not the other way around** — a real lamp has
more attributes than the synthetic snapshot, so the numbers are expected
to change.

The docstring in `test_relevance.py:195` explicitly points out that
the fixture is synthetic ("see comment there"). That sentence becomes
moot and must be rewritten along with it — otherwise the test claims
the opposite of what it does.

Then delete `tests/fixtures/nodes/synthetic_color_light.json`. First
check that it really no longer appears anywhere:

```bash
grep -rn "synthetic_color_light" . --exclude-dir=.git
```

Expected: no hits except in the design and in this plan.

- [ ] **Step 6: Commit**

```bash
uv run pytest -q
git add tests/fixtures/nodes/ tests/profiles/
git commit -m "test(fixtures): echte IKEA-Leuchten statt des synthetischen Abbilds"
```

---

### Task 2: `loxone_rgb_to_rgb` — unpacking the Loxone colour number

**Files:**
- Modify: `src/loxmatter/commands/color.py`
- Test: `tests/commands/test_color.py`

**Interfaces:**
- Produces: `loxone_rgb_to_rgb(value: float) -> tuple[int, int, int]` — three channels, each 0–255. Raises `ValueError` on invalid input. Task 3 builds on this.

- [ ] **Step 1: Write the failing test**

Append to `tests/commands/test_color.py`:

```python
from loxmatter.commands.color import loxone_rgb_to_rgb


@pytest.mark.parametrize(
    ("packed", "rgb"),
    [
        # The example from the Loxone knowledge base, quoted in the module
        # docstring: 20040060 = 60% red, 40% green, 20% blue.
        (20040060, (153, 102, 51)),
        (0, (0, 0, 0)),
        (100100100, (255, 255, 255)),
        (100, (255, 0, 0)),
        (100000, (0, 255, 0)),
        (100000000, (0, 0, 255)),
    ],
)
def test_the_packed_loxone_number_splits_into_three_channels(packed, rgb):
    assert loxone_rgb_to_rgb(packed) == rgb


@pytest.mark.parametrize("packed", [-1, 101, 101000, 101000000, 999999999])
def test_a_channel_above_100_percent_is_rejected(packed):
    """A clear error is better than a made-up color on the real device -
    the same stance as `kelvin_to_mireds` at 0 Kelvin."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(packed)


def test_a_fractional_number_is_rejected():
    """The Loxone encoding is integer; 20040060.5 would be a sign
    that a completely different number is arriving here."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(20040060.5)
```

- [ ] **Step 2: Run the test, check the failure**

Run: `uv run pytest tests/commands/test_color.py -v`
Expected: FAIL with `ImportError: cannot import name 'loxone_rgb_to_rgb'`

- [ ] **Step 3: Implement**

Append to `src/loxmatter/commands/color.py`:

```python
def loxone_rgb_to_rgb(value: float) -> tuple[int, int, int]:
    """Unpacks the Loxone color number into three channels of 0-255 each.

    The encoding is backed by an official source above in the module
    docstring: `AQa = red% + green% * 1000 + blue% * 1_000_000`. It carries
    only whole percent per channel - so the color is already quantized on
    leaving Loxone, and this function cannot recover that
    (design 2026-09-07, section 9.1).

    Accepted as an integer rather than rounded: a fractional number does
    not occur in this encoding, and silently rounding it would mean
    waving through a completely different number - say, an already
    unpacked channel - as a valid color.
    """
    if value != int(value):
        raise ValueError(f"Loxone-Farbzahl muss ganzzahlig sein, war {value}")
    packed = int(value)
    if packed < 0:
        raise ValueError(f"Loxone-Farbzahl darf nicht negativ sein, war {packed}")

    percents = (packed % 1000, packed // 1000 % 1000, packed // 1_000_000)
    for channel, percent in zip(("rot", "gruen", "blau"), percents, strict=True):
        if percent > 100:
            raise ValueError(
                f"Kanal {channel} liegt bei {percent} %, erlaubt sind 0-100 "
                f"(Loxone-Farbzahl {packed})"
            )
    red, green, blue = (round(percent * 255 / 100) for percent in percents)
    return red, green, blue
```

Explicitly unpacked rather than returned as `tuple(...)`: a generator `tuple` has no fixed length for mypy and would need a `type: ignore`, which this plan does not want to afford (`mypy --strict` is one of the global constraints).

- [ ] **Step 4: Run the test, check success**

Run: `uv run pytest tests/commands/test_color.py -v`
Expected: all PASS.

For `(20040060, (153, 102, 51))`: 60% of 255 = 153, 40% = 102, 20% = 51.

- [ ] **Step 5: Fix the incorrect module docstring in `color.py`**

The header of `color.py` currently warns: "ATTENTION - this part is NOT validated against hardware. No Matter lamp was available at build time". **Do not remove** this paragraph yet — it only goes away in Task 9, after checking against the real lamp. Instead, add a sentence to the Lumitech section, directly after the paragraph "Lumitech ... NOT backed":

```
This reservation never applied to RGB - `translate.py` mistakenly applied
it to the RGB encoding too, up until 7 September 2026, and therefore
blocked command 6 (see design 2026-09-07, section 1).
```

- [ ] **Step 6: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/commands/color.py tests/commands/test_color.py
git commit -m "feat(color): Loxone-Farbzahl entpacken"
```

---

### Task 3: Kommando 6 freischalten — Tabelle und Übersetzer zusammen

Der bestehende Konsistenztest über `known_command_pairs()` erzwingt, dass beide gemeinsam wandern. Deshalb eine Task, kein Paar.

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/commands/translate.py`
- Test: `tests/commands/test_translate.py`

**Interfaces:**
- Consumes: `loxone_rgb_to_rgb` aus Task 2.
- Produces: das Paar `(768, 6)` mit Slug `color`, `takes_value: true`. Nutzlast `{"hue": int, "saturation": int, "transitionTime": 0}`.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

An `tests/commands/test_translate.py` anhängen (die dortige Hilfe zum Bauen eines `StoredCommand` wiederverwenden — sie steht am Dateianfang):

```python
def test_a_packed_loxone_colour_becomes_hue_and_saturation():
    """Reines Rot: Farbton 0, volle Saettigung (254). Der Weg ist
    Loxone-Zahl -> RGB -> Hue/Sat, damit WebUI und Loxone denselben
    Uebersetzer benutzen (Entwurf 2026-09-07, Abschnitt 6.5)."""
    command = cmd(768, 6, takes_value=True)
    call = to_matter_call(command, "100")
    assert call.cluster_id == 768
    assert call.command_id == 6
    assert call.payload["hue"] == 0
    assert call.payload["saturation"] == 254
    assert call.payload["transitionTime"] == 0


def test_white_has_no_saturation():
    command = cmd(768, 6, takes_value=True)
    call = to_matter_call(command, "100100100")
    assert call.payload["saturation"] == 0


def test_an_impossible_colour_number_is_rejected():
    """Ein Kanal ueber 100 % kommt als 400 zurueck, nicht als erfundene
    Farbe am Geraet."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "999999999")


def test_colour_rejects_text():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "rot")
```

`cmd(cluster, command, takes_value=False)` already sits at the top of the file (line 30) and builds the `StoredCommand` — the same helper `(768, 10)` is already checked with there.

- [ ] **Step 2: Run the test, check the failure**

Run: `uv run pytest tests/commands/test_translate.py -v`
Expected: FAIL — `UnsupportedValueError` for a *valid* red, because `(768, 6)` is not yet in any builder.

- [ ] **Step 3: Add the table entry**

In `src/loxmatter/profiles/clusters.yaml`, cluster 768, under `commands:` next to the existing `10:` entry:

```yaml
      # MoveToHueAndSaturation - unlocked on September 7, 2026
      # (design 2026-09-07). The earlier block was based on a
      # mix-up: `translate.py` justified it with "Loxone RGB not
      # backed", while `commands/color.py` backs RGB with an official
      # source and leaves only Lumitech open. The value is the
      # packed Loxone color number; backed against the checked-in
      # RGBW light (tests/fixtures/nodes/ikea_kajplats_cws_lamp.json, 1/768/65529
      # contains 6).
      6: {slug: color, takes_value: true}
```

`control:` gets added in Task 4 — deliberately not yet here, so this task does exactly one thing.

- [ ] **Step 4: Add the payload builder**

In `src/loxmatter/commands/translate.py`:

Extend the import:

```python
from loxmatter.commands.color import kelvin_to_mireds, loxone_rgb_to_rgb, rgb_to_hue_saturation
```

Constant next to the existing ones:

```python
_COMMAND_HUE_SATURATION = 6
```

Builder next to `_payload_color_temperature`:

```python
def _payload_hue_saturation(value: str) -> dict[str, object]:
    """Packed Loxone color number -> Matter hue/saturation.

    Two conversions in a row, both backed by a source in
    `commands/color.py`: unpacking the Loxone encoding and converting the
    result to HSV. `loxone_rgb_to_rgb` raises `ValueError` for an
    impossible number - here that becomes `UnsupportedValueError`, so the
    caller answers with 400 rather than 500, as for any other unsuitable
    value.
    """
    try:
        red, green, blue = loxone_rgb_to_rgb(_as_number(value))
    except ValueError as exc:
        raise UnsupportedValueError(str(exc)) from exc
    hue, saturation = rgb_to_hue_saturation(red, green, blue)
    return {"hue": hue, "saturation": saturation, "transitionTime": 0}
```

Entry in `_PAYLOAD_BUILDERS`:

```python
    (_CLUSTER_COLOR, _COMMAND_HUE_SATURATION): _payload_hue_saturation,
```

- [ ] **Step 5: Fix the incorrect module docstring in `translate.py`**

The current paragraph claims command 6 is "deliberately not served, because the Loxone-side RGB number is not reliably documented (see `color.py`)". That is factually wrong and was the sole reason for the block. Replace it with:

```
Cluster 768 command 6 (Hue/Saturation) has been served since 7 September
2026: the Loxone-side RGB encoding is backed by an official source in
`color.py` (Knowledge Base, "RGB Lighting Controller"). Until then the
justification here was that it was unbacked - that confused RGB with
**Lumitech**, the combined brightness-and-kelvin output, which remains
without a solid source (see `color.py` and design 2026-09-07, section
10.1). MoveToHue (0), MoveToSaturation (3), MoveToColor (7, xy), and
Enhanced (67) remain unserved - the control surface sets hue and
saturation in one command, anything beyond that would be unbacked
surface.
```

Carry the enumeration below it along too, which names command 6 as an example of "known cluster, unknown command ID": `test_known_cluster_with_unknown_command_raises` checks command 6 there. **Switch this test to command 7**, or it fails from now on — while the statement ("a known cluster does not protect against an unknown command") is preserved.

- [ ] **Step 6: Run all tests**

Run: `uv run pytest -q`
Expected: PASS, in particular the consistency test between `known_command_pairs()` and `_PAYLOAD_BUILDERS`.

- [ ] **Step 7: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/commands/translate.py tests/commands/test_translate.py
git commit -m "feat(commands): MoveToHueAndSaturation freischalten"
```

---

### Task 4: The table field `control:`

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py`
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Produces: `command_control(cluster_id: int, command_id: int) -> str` — returns `"none"`, `"percent"`, `"kelvin"`, `"hue_sat"`, or `"unknown"`. Task 6 builds on this.

- [ ] **Step 1: Write the failing test**

Append to `tests/profiles/test_table.py`:

```python
from loxmatter.profiles.table import command_control


@pytest.mark.parametrize(
    ("cluster_id", "command_id", "control"),
    [
        (6, 0, "none"),
        (6, 1, "none"),
        (6, 2, "none"),
        (8, 0, "percent"),
        (8, 4, "percent"),
        (768, 10, "kelvin"),
        (768, 6, "hue_sat"),
    ],
)
def test_every_known_command_names_its_widget(cluster_id, command_id, control):
    assert command_control(cluster_id, command_id) == control


def test_a_command_outside_the_table_is_unknown():
    assert command_control(768, 7) == "unknown"


def test_every_table_command_carries_a_control():
    """An entry without `control` would appear in the UI as a bare
    number field, without anyone having decided that (design
    2026-09-07, section 5.5). This test makes the oversight visible,
    instead of letting it slide."""
    for cluster_id, command_id in known_command_pairs():
        assert command_control(cluster_id, command_id) != "unknown"
```

`known_command_pairs` is already imported in the file; otherwise add it.

- [ ] **Step 2: Run the test, check the failure**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: FAIL with `ImportError: cannot import name 'command_control'`

- [ ] **Step 3: Add `control:` to every command entry**

In `src/loxmatter/profiles/clusters.yaml`:

```yaml
  6:
    commands:
      0: {slug: "off", takes_value: false, control: none}
      1: {slug: "on", takes_value: false, control: none}
      2: {slug: toggle, takes_value: false, control: none}
  8:
    commands:
      0: {slug: level, takes_value: true, control: percent}
      4: {slug: level_onoff, takes_value: true, control: percent}
  768:
    commands:
      6: {slug: color, takes_value: true, control: hue_sat}
      10: {slug: colortemp, takes_value: true, control: kelvin}
```

**Add only the `control:` key** — slugs, `takes_value`, and the surrounding comments stay word for word as they are. The slugs above are the ones actually present; proofread against the file before editing. Careful: `off` and `on` sit there in quotes, because YAML would otherwise read them as booleans — that must stay that way.

- [ ] **Step 4: Implement `command_control`**

In `src/loxmatter/profiles/table.py`, next to `command_takes_value`:

```python
def command_control(cluster_id: int, command_id: int) -> str:
    """Which control the UI should build for this command.

    `none` (button), `percent`, `kelvin`, `hue_sat` - or `unknown` for
    an entry that no one has yet given a `control`.

    `unknown` is deliberately its own value and not a default guessed
    from `takes_value`: a slider asserts a value range, and
    no one here knows it. The UI falls back to the
    plain number field for `unknown` (design 2026-09-07, section 5.5).

    The return value is deliberately a `str` and not an enum: it travels
    unchanged through the API into the JavaScript, where only the
    wording matters anyway - an enum would have to be resolved again at
    the boundary and would need two changes instead of one for every new
    widget.
    """
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    if not entry:
        return "unknown"
    control = entry.get("control")
    return str(control) if control else "unknown"
```

- [ ] **Step 5: Run the test, check success**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: all PASS, including `test_every_table_command_carries_a_control`.

- [ ] **Step 6: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/profiles/table.py tests/profiles/test_table.py
git commit -m "feat(profiles): Bedienelement-Typ je Kommando in der Tabelle"
```

---

### Task 5: `functional: false` and the CT limits

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py`
- Modify: `src/loxmatter/profiles/relevance.py`
- Test: `tests/profiles/test_relevance.py`, `tests/profiles/test_table.py`

**Interfaces:**
- Produces: `marked_non_functional(ref: SignalRef) -> bool` in `table.py`; `is_functional` honours it. The attributes `1/768/16395` and `1/768/16396` are known, carry the unit `mired`, are `exportable`, but not `functional`.

- [ ] **Step 1: Write the failing test**

Append to `tests/profiles/test_relevance.py` (take over the usual way of building a `SignalRef` and `device_types` there from the existing tests):

```python
def test_the_physical_colour_temperature_limits_are_known_but_not_wanted():
    """Two immutable device constants. Known well enough to read
    (the kelvin slider needs them), not interesting enough to be
    preselected as a virtual Loxone input (design
    2026-09-07, section 5.2)."""
    device_types = {1: frozenset({269})}
    for element_id in (16395, 16396):
        ref = SignalRef(endpoint=1, cluster_id=768, element_id=element_id,
                        kind=SignalKind.ATTRIBUTE)
        assert not is_functional(ref, device_types)


def test_the_ordinary_colour_attributes_stay_functional():
    """The converse check: `functional: false` must not spill over onto the
    whole cluster."""
    device_types = {1: frozenset({269})}
    for element_id in (0, 1, 7, 8):
        ref = SignalRef(endpoint=1, cluster_id=768, element_id=element_id,
                        kind=SignalKind.ATTRIBUTE)
        assert is_functional(ref, device_types)
```

Append to `tests/profiles/test_table.py`:

```python
def test_the_colour_temperature_limits_remain_exportable():
    """Not preselected does not mean locked: in the expert block,
    it must still be possible to select them by hand."""
    ref = SignalRef(endpoint=1, cluster_id=768, element_id=16395,
                    kind=SignalKind.ATTRIBUTE)
    profile = lookup(ref, 250)
    assert profile.unit == "mired"
    assert is_exportable(profile.exportability)
    assert not profile.slug.startswith("c768_a")
```

- [ ] **Step 2: Run the tests, check the failure**

Run: `uv run pytest tests/profiles/ -v`
Expected: FAIL — the limits are not yet in the table, `lookup` invents the generic slug `c768_a16395`, and `is_functional` still returns `True` for them.

- [ ] **Step 3: Add the attributes**

In `src/loxmatter/profiles/clusters.yaml`, cluster 768, under `attributes:` after the `8:` entry:

```yaml
      # ColorTempPhysicalMinMireds (16395) and ColorTempPhysicalMaxMireds
      # (16396): what color temperature the light can do at all. IDs
      # checked against the installed SDK (chip.clusters.Objects.
      # ColorControl.Attributes), values checked against both checked-in
      # lights.
      #
      # `functional: false`, because they are immutable device constants:
      # the UI needs them to limit the Kelvin slider
      # (design 2026-09-07, section 6.2), but as a virtual
      # Loxone input they would be two numbers that never change. In
      # the expert block they remain manually selectable.
      #
      # Like 7 above, they stay in mired - Kelvin = 1e6 / mired is
      # a reciprocal that `scale` cannot express. The API converts
      # them for the UI (see api/control.py).
      16395: {slug: colortemp_phys_min_mireds, unit: mired, functional: false}
      16396: {slug: colortemp_phys_max_mireds, unit: mired, functional: false}
```

- [ ] **Step 4: Implement `marked_non_functional`**

In `src/loxmatter/profiles/table.py`, directly after `names_element`:

```python
def marked_non_functional(ref: SignalRef) -> bool:
    """Whether the table explicitly marks this element as not preselected
    (`functional: false`).

    The counterpart to `names_element`: being named normally means
    being wanted (see `profiles.relevance.is_functional`,
    layer 3). For device constants - min/max ranges, resolutions -
    that is not true: they must be readable without bloating the
    standard export.

    Deliberately a general field rather than a special case for cluster 768:
    every further cluster with capacity figures hits the same problem.
    The alternative - reaching for the values past the table directly from
    the snapshot - would create a second place where attribute knowledge
    lives (design 2026-09-07, section 5.2).
    """
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return False
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    entry = (cluster.get(section) or {}).get(ref.element_id)
    return bool(entry) and entry.get("functional") is False
```

- [ ] **Step 5: Hook `is_functional` up to it**

In `src/loxmatter/profiles/relevance.py`, extend the import:

```python
from loxmatter.profiles.table import known_attribute_section, marked_non_functional, names_element
```

(Die vorhandene Importzeile lesen und `marked_non_functional` ergänzen, nicht ersetzen.)

Den Schluss der Funktion ändern — heute:

```python
    if known_attribute_section(ref.cluster_id):
        return names_element(ref)
```

wird zu:

```python
    if known_attribute_section(ref.cluster_id):
        return names_element(ref) and not marked_non_functional(ref)
```

Und in Schicht 3 des Docstrings den Zusatz ergänzen:

```
   ... dort nur die dort benannten Elemente - abzueglich derer, die die
   Tabelle ausdruecklich mit `functional: false` fuehrt (Geraetekonstanten
   wie Min/Max-Bereiche, siehe `marked_non_functional`).
```

- [ ] **Step 6: Tests laufen lassen, Erfolg prüfen**

Run: `uv run pytest tests/profiles/ -v`
Expected: alle PASS.

**Achtung:** `tests/profiles/test_real_device_fixtures.py` zählt Signale des Steckers und des Tasters. Die neuen Attribute liegen in Cluster 768, den beide nicht haben — die Zahlen dürfen sich also nicht ändern. Ändern sie sich doch, ist das ein echter Befund und kein anzupassender Erwartungswert.

- [ ] **Step 7: Prüfen und committen**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/profiles/
git add tests/profiles/test_relevance.py tests/profiles/test_table.py
git commit -m "feat(profiles): Geraetekonstanten lesbar, aber nicht vorausgewaehlt"
```

---

### Task 6: `CommandOut.control` und `range`

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/control.py`
- Modify: `src/loxmatter/loxone/server.py:499`
- Test: `tests/api/test_control.py`

**Interfaces:**
- Consumes: `command_control` (Task 4), die Attribute aus Task 5.
- Produces: `CommandOut` mit `control: str` und `range: ControlRange | None`; `ControlRange` hat `min: int` und `max: int` in **Kelvin**. `build_control_router(store, invoke, values)` — dritter Parameter neu.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

An `tests/api/test_control.py` anhängen. Die vorhandene `api`-Fixture lädt den Stecker; für diese Tests eine eigene Fixture nach demselben Muster, aber mit der RGBW-Leuchte:

```python
@pytest.fixture
async def api_lamp(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int, object]]:
    """Wie `api`, aber mit der eingecheckten RGBW-Leuchte - der einzigen
    Vorlage, die Farb- und Farbtemperatur-Kommandos zugleich traegt."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    runtime = fake_runtime(store)

    app = build_app(store, no_invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id, runtime
    store.close()


async def test_every_control_names_its_widget(api_lamp):
    client, _store, device_id, _runtime = api_lamp
    response = await client.get(f"/api/devices/{device_id}/controls")
    assert response.status_code == 200
    by_slug = {c["slug"]: c["control"] for c in response.json()["commands"]}
    assert by_slug["on"] == "none"
    assert by_slug["colortemp"] == "kelvin"
    assert by_slug["color"] == "hue_sat"


async def test_the_kelvin_range_comes_from_the_device_in_kelvin(api_lamp):
    """Mired -> Kelvin ist ein Kehrwert: das kleinere Mired ergibt das
    GROESSERE Kelvin, Min und Max tauschen also (Entwurf 2026-09-07,
    Abschnitt 5.5)."""
    client, store, device_id, runtime = api_lamp
    keys = {
        signal.ref.element_id: signal.key
        for signal in store.signals(device_id)
        if signal.ref.cluster_id == 768 and signal.ref.element_id in (16395, 16396)
    }
    # Die echten Werte der eingecheckten CWS-Leuchte.
    runtime.seed(keys[16395], 153)  # 153 Mired = 6535 K
    runtime.seed(keys[16396], 555)  # 555 Mired = 1801 K

    response = await client.get(f"/api/devices/{device_id}/controls")
    colortemp = next(c for c in response.json()["commands"] if c["slug"] == "colortemp")
    assert colortemp["range"] == {"min": 1801, "max": 6535}


async def test_without_the_limits_there_is_no_range(api_lamp):
    """Kein Bereich ist besser als ein erfundener - die Oberflaeche faellt
    dann auf das Zahlenfeld zurueck (Entwurf 2026-09-07, Abschnitt 9.2)."""
    client, _store, device_id, _runtime = api_lamp  # nichts geseedet
    response = await client.get(f"/api/devices/{device_id}/controls")
    colortemp = next(c for c in response.json()["commands"] if c["slug"] == "colortemp")
    assert colortemp["range"] is None


async def test_commands_without_a_range_carry_none(api_lamp):
    client, _store, device_id, _runtime = api_lamp
    response = await client.get(f"/api/devices/{device_id}/controls")
    for command in response.json()["commands"]:
        if command["slug"] != "colortemp":
            assert command["range"] is None
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: FAIL mit `KeyError: 'control'`

- [ ] **Step 3: Die Modelle erweitern**

In `src/loxmatter/api/models.py`, vor `CommandOut`:

```python
class ControlRange(BaseModel):
    """Grenzen eines Reglers, in der Einheit, die die Oberflaeche anzeigt.

    Heute nur fuer die Farbtemperatur, in Kelvin. Die Umrechnung aus Mired
    passiert im Server und nicht im JavaScript: sie ist ein Kehrwert, bei
    dem Min und Max tauschen - eine Falle, die man nicht zweimal aufstellen
    will (Entwurf 2026-09-07, Abschnitt 5.5)."""

    model_config = ConfigDict(frozen=True)

    min: int
    max: int
```

`CommandOut` um zwei Felder erweitern (die bestehenden Felder und den Docstring stehen lassen, nur ergänzen):

```python
    control: str
    range: ControlRange | None = None
```

Und dem `CommandOut`-Docstring anhängen:

```
    `control` sagt, WELCHES Bedienelement gebaut werden soll (`none`,
    `percent`, `kelvin`, `hue_sat`, `unknown`) - siehe
    `profiles.table.command_control`. `takes_value` bleibt daneben
    bestehen, weil es etwas anderes beantwortet: ob der EXPORT einen
    analogen oder digitalen Ausgang erzeugt.
```

`ControlsOut` bleibt unverändert.

- [ ] **Step 4: Die Route erweitern**

In `src/loxmatter/api/control.py`:

Importe ergänzen:

```python
from typing import Protocol

from loxmatter.api.models import CommandOut, ControlRange, ControlsOut, ValueIn
from loxmatter.profiles.table import command_control, command_slug
```

Nach der `Invoker`-Zeile:

```python
class ValueReader(Protocol):
    """Was diese Route von `runtime` braucht - nur Lesen.

    Bewusst enger als `api.devices.RuntimeValues`: die Bedienroute setzt
    nichts online, und ein Protokoll, das mehr verlangt als es benutzt,
    zwingt jedem Test ein groesseres Double auf, als der Fall braucht.
    `loxone.runtime.Runtime` erfuellt beide.
    """

    def last_values_for(self, device_id: int) -> dict[str, float | bool]: ...


# ColorTempPhysicalMinMireds / ColorTempPhysicalMaxMireds, gegen das
# installierte SDK belegt (chip.clusters.Objects.ColorControl.Attributes).
_CLUSTER_COLOR = 768
_ATTR_CT_PHYS_MIN_MIREDS = 16395
_ATTR_CT_PHYS_MAX_MIREDS = 16396
```

Signatur ändern:

```python
def build_control_router(store: Store, invoke: Invoker, values: ValueReader) -> APIRouter:
```

Vor der `controls`-Route eine Hilfe einfügen:

```python
    def _kelvin_range(device_id: int, endpoint: int) -> ControlRange | None:
        """Der Farbtemperaturbereich der Leuchte, in Kelvin - oder None.

        Kelvin = 1e6 / Mired ist ein Kehrwert: das KLEINERE Mired ergibt
        das GROESSERE Kelvin, Min und Max tauschen also beim Umrechnen.

        None statt eines Ersatzbereichs, wenn die Leuchte die Grenzen nicht
        meldet: ein Regler, der bei 6500 K endet, obwohl das Geraet bei
        4000 K aufhoert, laesst einen Wert einstellen, den es still
        beschneidet - genau der stille Fehlschlag, den diese Ansicht
        aufdecken soll (Spec 8.1).
        """
        wanted = (_ATTR_CT_PHYS_MIN_MIREDS, _ATTR_CT_PHYS_MAX_MIREDS)
        keys = {
            signal.ref.element_id: signal.key
            for signal in store.signals(device_id)
            if signal.ref.endpoint == endpoint
            and signal.ref.cluster_id == _CLUSTER_COLOR
            and signal.ref.element_id in wanted
        }
        current = values.last_values_for(device_id)
        mireds: list[float] = []
        for element_id in wanted:
            key = keys.get(element_id)
            value = current.get(key) if key is not None else None
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                return None
            mireds.append(float(value))

        kelvins = sorted(int(1_000_000 / mired) for mired in mireds)
        return ControlRange(min=kelvins[0], max=kelvins[1])
```

In der `controls`-Route die Listenbildung ersetzen:

```python
        stored = store.commands(device_id)
        named = []
        for command in stored:
            if command_slug(command.cluster_id, command.command_id) is None:
                continue
            control = command_control(command.cluster_id, command.command_id)
            named.append(
                CommandOut(
                    key=command.key,
                    slug=command.slug,
                    takes_value=command.takes_value,
                    control=control,
                    range=_kelvin_range(device_id, command.endpoint)
                    if control == "kelvin"
                    else None,
                )
            )
        return ControlsOut(commands=named, hidden_raw_commands=len(stored) - len(named))
```

- [ ] **Step 5: Den Router verdrahten**

In `src/loxmatter/loxone/server.py` Zeile 499:

```python
    app.include_router(build_control_router(store, invoke, runtime), dependencies=api_guard)
```

Der Kommentar darüber bleibt.

- [ ] **Step 6: Tests laufen lassen, Erfolg prüfen**

Run: `uv run pytest tests/api/ -v`
Expected: alle PASS. Bei 250 Mired → 4000 K und 454 Mired → 2202 K (jeweils abgeschnitten, wie `kelvin_to_mireds` es in der Gegenrichtung tut).

- [ ] **Step 7: Den Moduldocstring von `control.py` nachziehen**

Der Absatz „Offener Punkt, hier bewusst nicht geloest" nennt als Grund unter anderem, die Schnittstelle `build_control_router(store, invoke)` nehme „dafuer auch keinen zweiten Aufrufer entgegen". Die Signatur hat jetzt drei Parameter — die Aussage über das **Schreiben von Attributen** bleibt aber richtig, denn `values` liest nur. Den Satz entsprechend präzisieren, statt ihn zu streichen:

```
... und die Schnittstelle dieses Moduls (`build_control_router(store,
invoke, values)`) nimmt dafuer auch keinen schreibenden Aufrufer entgegen;
`invoke` ist ausschliesslich fuer Kommandos typisiert, `values` liest nur
(siehe `ValueReader`), und ein Attribut-Schreibzugriff ist keins von beidem.
```

- [ ] **Step 8: Prüfen und committen**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/api/models.py src/loxmatter/api/control.py src/loxmatter/loxone/server.py tests/api/test_control.py
git commit -m "feat(api): Bedienelement-Typ und Kelvin-Bereich ausliefern"
```

---

### Task 7: Kachel-Knopf und Bedien-Modal mit Reglern

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `CommandOut.control` und `.range` aus Task 6.
- Produces: Alpine-Zustand `controlModalDevice`, Methoden `openControlModal(device)`, `closeControlModal()`, `controlsByKind(deviceId, kind)`, `sendControl(device, command, value)`. Task 8 baut darauf.

- [ ] **Step 1: Übersetzungen ergänzen**

In `src/loxmatter/i18n/strings.yaml`, bei den übrigen `web.devices.*`-Schlüsseln:

```yaml
web.devices.control_button:
  en: "Control"
  de: "Steuern"
web.devices.control_modal_title:
  en: "Control {label}"
  de: "{label} steuern"
web.devices.control_brightness:
  en: "Brightness"
  de: "Helligkeit"
web.devices.control_colortemp:
  en: "Colour temperature"
  de: "Farbtemperatur"
web.devices.control_start_value_unknown:
  en: "Start value unknown — the device has not reported this value."
  de: "Startwert unbekannt — das Gerät hat diesen Wert nicht gemeldet."
web.devices.control_no_range:
  en: "This lamp does not report its colour temperature range, so there is no slider."
  de: "Diese Leuchte meldet ihren Farbtemperaturbereich nicht, deshalb gibt es keinen Regler."
```

- [ ] **Step 2: Zustand und Methoden in `app.js`**

Neben `signalsModalDevice` (etwa Zeile 488):

```javascript
    // Wie `signalsModalDevice`: nur die ID, nicht das Objekt - siehe den
    // Kommentar dort und `controlModalDeviceObject()`.
    controlModalDevice: null,
```

Im Methodenblock, neben den Signal-Modal-Methoden:

```javascript
    controlModalDeviceObject() {
      return this.devices.find((device) => device.id === this.controlModalDevice) || null;
    },

    /**
     * Oeffnet das Bedien-Modal. Das `$nextTick` ist Pflicht, kein Stil -
     * dieselbe Begruendung wie bei `openSignalsModal`: `showModal()` setzt
     * den Anfangsfokus auf das erste fokussierbare Element IM Dialog, und
     * das entsteht erst, nachdem Alpine den `x-if`-Inhalt aufgebaut hat.
     */
    openControlModal(device) {
      this.deviceActionError = null;
      this.controlModalDevice = device.id;
      this.controlDrafts = this.readStartValues(device.id);
      this.$nextTick(() => this.$refs.controlModal.showModal());
    },

    /** Schliesst ueber `close()`, damit der `close`-Handler in index.html
     * die eine Stelle bleibt, die `controlModalDevice` zuruecksetzt -
     * dieselbe Regel wie bei `closeSignalsModal`. */
    closeControlModal() {
      this.$refs.controlModal.close();
    },

    /** Alle Kommandos eines Geraets mit genau diesem Bedienelement-Typ. */
    controlsByKind(deviceId, kind) {
      return this.commandsFor(deviceId).filter((command) => command.control === kind);
    },

    /** Ob dieses Geraet ueberhaupt etwas Wertbehaftetes kann - nur dann
     * bekommt die Kachel den "Steuern"-Knopf. */
    hasAdjustableControls(deviceId) {
      return this.commandsFor(deviceId).some((command) => command.control !== "none");
    },
```

- [ ] **Step 3: Startwerte lesen**

Ebenfalls in `app.js`:

```javascript
    // Die Slugs, unter denen die Signale eines Geraets die Startwerte
    // tragen. `signalsByDevice` haelt ALLE Signale, nicht nur die
    // exportierten (siehe api/devices.py, `get_signals`), und ihre Werte
    // sind bereits skaliert (loxone/values.py, `to_loxone_value`) - level
    // und saturation in Prozent, hue in Grad. Nur die Farbtemperatur steht
    // in Mired, weil Kelvin ein Kehrwert ist, den `scale` nicht kann.
    // Ueber den PFAD gesucht, nicht ueber den Slug im Schluessel: kollidiert
    // ein Schluessel innerhalb eines Geraets, haengt `Store._assign_key` die
    // Element-ID an (`d1_1_hue_0`), und ein Vergleich auf `_hue` ginge dann
    // ins Leere. `SignalOut.path` ist "endpunkt/cluster/element" und damit
    // exakt.
    signalValueByPath(deviceId, clusterId, elementId) {
      const signals = this.signalsByDevice[deviceId] || [];
      const signal = signals.find((entry) => entry.path.endsWith(`/${clusterId}/${elementId}`));
      return signal ? this.liveValueOf(signal) : undefined;
    },

    /**
     * Einmalig beim Oeffnen gelesen, danach NICHT nachgefuehrt (Entwurf
     * 2026-09-07, Abschnitt 2). Ohne diese Startwerte stuende jeder Regler
     * auf einer erfundenen Position, und der erste Schubs risse die Leuchte
     * irgendwohin - der Klick bewiese dann nichts ueber den Zustand, den er
     * gerade veraendert hat.
     *
     * `undefined` bleibt `undefined` und wird nicht durch eine Null
     * ersetzt: die Oberflaeche zeigt dafuer den Hinweis "Startwert
     * unbekannt", statt eine Kenntnis vorzutaeuschen, die nicht besteht.
     */
    readStartValues(deviceId) {
      // Cluster 8 Attribut 0 = CurrentLevel; Cluster 768: 0 = CurrentHue,
      // 1 = CurrentSaturation, 7 = ColorTemperatureMireds, 8 = ColorMode.
      // Alle gegen das installierte SDK belegt (siehe Entwurf, Abschnitt 4).
      const mireds = this.signalValueByPath(deviceId, 768, 7);
      return {
        percent: this.signalValueByPath(deviceId, 8, 0),
        kelvin: mireds > 0 ? Math.round(1000000 / mireds) : undefined,
        hue: this.signalValueByPath(deviceId, 768, 0),
        saturation: this.signalValueByPath(deviceId, 768, 1),
        colormode: this.signalValueByPath(deviceId, 768, 8),
      };
    },
```

Und den Entwurfsspeicher neben `commandValueDrafts` anlegen:

```javascript
    controlDrafts: {},
```

- [ ] **Step 4: Senden**

```javascript
    /**
     * Schickt einen Reglerwert. Aufgerufen beim LOSLASSEN (`change`), nicht
     * waehrend des Ziehens: ein Zug = ein Funkpaket. Thread ist langsam,
     * und wenn ein Klick etwas beweisen soll, muss die Zuordnung zwischen
     * Eingabe und Reaktion eindeutig bleiben (Entwurf 2026-09-07,
     * Abschnitt 6.5).
     */
    async sendControl(device, command, value) {
      this.commandBusyKey = command.key;
      try {
        await this.request("POST", `/api/commands/${command.key}`, { value: String(value) });
        this.showToast(t("web.devices.command_sent", { slug: command.slug, label: device.label }));
      } catch (error) {
        this.showToast(
          t("web.devices.command_failed", { slug: command.slug, message: error.message }),
          true,
        );
      } finally {
        this.commandBusyKey = null;
      }
    },
```

- [ ] **Step 5: Die Kachel umbauen**

In `src/loxmatter/web/index.html`, im Block `device-commands`: die `<template x-for>`-Schleife so einschränken, dass sie nur noch wertlose Kommandos rendert, und den Knopf ergänzen. Der Zweig für `command.takes_value` mit dem Zahlenfeld **entfällt hier** — er zieht ins Modal:

```html
                  <div class="device-commands">
                    <template x-for="command in controlsByKind(device.id, 'none')" :key="command.key">
                      <button
                        @click="executeCommand(device, command)"
                        :disabled="commandBusyKey === command.key || !isOnline(device)"
                        x-text="command.slug"
                      ></button>
                    </template>
                    <button
                      x-show="hasAdjustableControls(device.id)"
                      @click="openControlModal(device)"
                      :disabled="!isOnline(device)"
                      x-text="t('web.devices.control_button')"
                    ></button>
```

Die drei folgenden Hinweiszeilen (`hiddenRawCommandsFor`, `controlsLoading`, `no_known_commands`) bleiben **unverändert** stehen.

- [ ] **Step 6: Das Modal anlegen**

Neben das bestehende Signal-Modal in `index.html` ein zweites `<dialog>` setzen, nach demselben Muster (`x-ref="controlModal"`, `@close` setzt `controlModalDevice = null`, `@click` mit `isBackdropEvent`). Inhalt:

```html
        <template x-if="controlModalDeviceObject()">
          <div>
            <h3 x-text="t('web.devices.control_modal_title', { label: controlModalDeviceObject().label })"></h3>

            <template x-for="command in controlsByKind(controlModalDevice, 'none')" :key="command.key">
              <button
                @click="executeCommand(controlModalDeviceObject(), command)"
                :disabled="commandBusyKey === command.key || !isOnline(controlModalDeviceObject())"
                x-text="command.slug"
              ></button>
            </template>

            <template x-for="command in controlsByKind(controlModalDevice, 'percent')" :key="command.key">
              <div class="control-row">
                <label x-text="t('web.devices.control_brightness')"></label>
                <input
                  type="range" min="0" max="100" step="1"
                  :value="controlDrafts.percent ?? 50"
                  :disabled="commandBusyKey === command.key || !isOnline(controlModalDeviceObject())"
                  @change="sendControl(controlModalDeviceObject(), command, $event.target.value)"
                />
                <p class="hint" x-show="controlDrafts.percent === undefined"
                   x-text="t('web.devices.control_start_value_unknown')"></p>
              </div>
            </template>

            <template x-for="command in controlsByKind(controlModalDevice, 'kelvin')" :key="command.key">
              <div class="control-row">
                <label x-text="t('web.devices.control_colortemp')"></label>
                <input
                  x-show="command.range"
                  type="range" :min="command.range?.min" :max="command.range?.max" step="10"
                  :value="controlDrafts.kelvin ?? command.range?.min"
                  :disabled="commandBusyKey === command.key || !isOnline(controlModalDeviceObject())"
                  @change="sendControl(controlModalDeviceObject(), command, $event.target.value)"
                />
                <p class="hint" x-show="!command.range" x-text="t('web.devices.control_no_range')"></p>
                <span x-show="!command.range" class="row">
                  <input type="number" :placeholder="t('web.devices.value_placeholder')"
                         @input="commandValueDrafts[command.key] = $event.target.value" />
                  <button @click="executeCommand(controlModalDeviceObject(), command)"
                          :disabled="commandBusyKey === command.key || !isOnline(controlModalDeviceObject())"
                          x-text="t('web.devices.send')"></button>
                </span>
              </div>
            </template>

            <template x-for="command in controlsByKind(controlModalDevice, 'unknown')" :key="command.key">
              <span class="row">
                <span x-text="command.slug"></span>
                <input type="number" :placeholder="t('web.devices.value_placeholder')"
                       @input="commandValueDrafts[command.key] = $event.target.value" />
                <button @click="executeCommand(controlModalDeviceObject(), command)"
                        :disabled="commandBusyKey === command.key || !isOnline(controlModalDeviceObject())"
                        x-text="t('web.devices.send')"></button>
              </span>
            </template>
          </div>
        </template>
```

Der `hue_sat`-Block folgt in Task 8.

- [ ] **Step 7: Stil**

In `style.css` neben den vorhandenen Modal-Regeln:

```css
/* Bedien-Modal: eine Zeile je Regler, Beschriftung ueber dem Regler statt
   daneben - auf schmalen Fenstern bleibt der Regler sonst zu kurz zum
   Zielen. */
.control-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.75rem;
}

.control-row input[type="range"] {
  width: 100%;
}
```

- [ ] **Step 8: Auslieferungstest ergänzen**

An `tests/api/test_web.py` anhängen (dem dortigen Muster folgend):

```python
async def test_the_control_modal_is_delivered(client):
    """Belegt NUR die Auslieferung. Ob die Alpine-Ausdruecke darin
    tatsaechlich binden, kann dieser Test nicht sagen - das prueft der
    Browser-Durchgang in Task 9."""
    response = await client.get("/app.js")
    assert "openControlModal" in response.text
    assert "readStartValues" in response.text
```

- [ ] **Step 9: Prüfen und committen**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): Bedien-Modal mit Reglern statt nackter Zahlenfelder"
```

---

### Task 8: Farbfläche und Modus-Tabs

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`

**Interfaces:**
- Consumes: alles aus Task 7.
- Produces: `hasColourTabs(deviceId)`, `controlTab`, `hueSatToLoxone(hue, saturation)`, `pickColour(event, device, command)`.

- [ ] **Step 1: Übersetzungen ergänzen**

```yaml
web.devices.control_tab_white:
  en: "White"
  de: "Weiß"
web.devices.control_tab_colour:
  en: "Colour"
  de: "Farbe"
web.devices.control_colour_hint:
  en: "Horizontal: hue. Vertical: saturation. Release to send."
  de: "Waagerecht: Farbton. Senkrecht: Sättigung. Beim Loslassen wird gesendet."
```

- [ ] **Step 2: Die Umrechnung im JavaScript**

In `app.js`:

```javascript
    /**
     * Farbton (Grad) und Saettigung (Prozent) in die gepackte Loxone-Zahl,
     * die `POST /api/commands/{key}` erwartet.
     *
     * Warum der Umweg ueber die Loxone-Codierung, statt Hue/Sat direkt zu
     * schicken: WebUI und Loxone benutzen denselben Uebersetzer
     * (`commands/translate.py`, Spec 4.2). Ein Klick hier durchlaeuft damit
     * genau den Weg, den Loxone spaeter nimmt - klappt es hier, ist der
     * Loxone-Pfad bewiesen. Der Preis ist die Quantisierung auf volle
     * Prozent je Kanal (Entwurf 2026-09-07, Abschnitt 9.1).
     *
     * Die Helligkeit steckt NICHT in dieser Zahl - sie laeuft ueber
     * LevelControl. Deshalb ist der Value-Anteil hier fest 1.
     */
    hueSatToLoxone(hue, saturation) {
      // Lehrbuch-HSV nach RGB mit fest v = 1: die Helligkeit steckt NICHT
      // in dieser Zahl, sie laeuft ueber LevelControl.
      const h = (((hue % 360) + 360) % 360) / 60;
      const s = Math.max(0, Math.min(100, saturation)) / 100;
      const c = s;                                   // Chroma bei v = 1
      const x = c * (1 - Math.abs((h % 2) - 1));
      const m = 1 - c;                               // Weissanteil
      const sectors = [
        [c, x, 0], [x, c, 0], [0, c, x],
        [0, x, c], [x, 0, c], [c, 0, x],
      ];
      const [r, g, b] = sectors[Math.floor(h) % 6].map((channel) =>
        Math.round((channel + m) * 100),
      );
      return r + g * 1000 + b * 1000000;
    },
```

**Diese Funktion braucht einen eigenen Prüfschritt** — siehe Schritt 3. Sie ist die fehleranfälligste Stelle des ganzen Plans, weil ein Fehler hier nach einem Gerätefehler aussieht, nicht nach einem Rechenfehler (dieselbe Warnung, die `color.py` über sich selbst schreibt).

- [ ] **Step 3: Die Umrechnung im Browser gegenprüfen**

Ein Wegwerf-Harness statt eines Unit-Tests, weil die Funktion im Browser lebt (Regel aus früheren Runden: ein Auslieferungstest belegt nur die Auslieferung).

Eine Datei im Scratchpad anlegen, die `hueSatToLoxone` einbindet und diese Fälle prüft, und sie im Browser öffnen:

| Eingabe | Erwartete gepackte Zahl | Warum |
| --- | --- | --- |
| `(0, 100)` | `100` | reines Rot: r=100 %, g=0, b=0 |
| `(120, 100)` | `100000` | reines Grün |
| `(240, 100)` | `100000000` | reines Blau |
| `(0, 0)` | `100100100` | keine Sättigung = Weiß |

Weicht ein Wert ab, ist die Formel falsch — **nicht** die Erwartung anpassen, sondern die Formel korrigieren, bis alle vier stimmen. Die drei Grundfarben und Weiß sind genau die Fälle, an denen eine vertauschte Zuordnung auffällt (dieselbe Begründung wie im Docstring von `rgb_to_hue_saturation`).

- [ ] **Step 4: Tabs und Fläche**

In `app.js`:

```javascript
    // Der aktive Reiter des Bedien-Modals. Wird beim Oeffnen aus dem
    // `colormode`-Signal des Geraets gesetzt (0 = Hue/Sat, 2 = Mired,
    // gegen das SDK belegt) - das Modal raet den Modus also nicht, es
    // liest ihn.
    controlTab: "white",

    /** Tabs nur, wenn das Geraet BEIDE Wege kann. Eine CCT-Leuchte
     * bekommt dadurch keine Tableiste - ohne eine einzige Abfrage auf
     * Geraetetyp oder Modell (Entwurf 2026-09-07, Abschnitt 6.3). */
    hasColourTabs(deviceId) {
      return (
        this.controlsByKind(deviceId, "kelvin").length > 0 &&
        this.controlsByKind(deviceId, "hue_sat").length > 0
      );
    },

    /** Wandelt einen Klick auf die Farbflaeche in Farbton und Saettigung
     * und schickt ihn. Die Flaeche ist waagerecht der Farbton (0-360°),
     * senkrecht die Saettigung (oben 100 %, unten 0 %). */
    pickColour(event, device, command) {
      const rect = event.currentTarget.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const hue = x * 360;
      const saturation = (1 - y) * 100;
      this.controlDrafts = { ...this.controlDrafts, hue, saturation };
      return this.sendControl(device, command, this.hueSatToLoxone(hue, saturation));
    },
```

In `openControlModal` den Reiter aus dem Gerät setzen — die Zeile mit `controlDrafts` ergänzen um:

```javascript
      this.controlTab = this.controlDrafts.colormode === 0 ? "colour" : "white";
```

- [ ] **Step 5: Das Markup**

Im Modal, vor dem `kelvin`-Block, die Tableiste:

```html
            <div class="control-tabs" x-show="hasColourTabs(controlModalDevice)">
              <button :class="{ 'is-active': controlTab === 'white' }"
                      @click="controlTab = 'white'"
                      x-text="t('web.devices.control_tab_white')"></button>
              <button :class="{ 'is-active': controlTab === 'colour' }"
                      @click="controlTab = 'colour'"
                      x-text="t('web.devices.control_tab_colour')"></button>
            </div>
```

Den `kelvin`-Block aus Task 7 in ein `x-show` hüllen — sichtbar, wenn es keine Tabs gibt oder der Weiß-Reiter aktiv ist:

```html
            <div x-show="!hasColourTabs(controlModalDevice) || controlTab === 'white'">
              <!-- der kelvin-Block aus Task 7, unveraendert -->
            </div>
```

Und der Farbblock:

```html
            <div x-show="!hasColourTabs(controlModalDevice) || controlTab === 'colour'">
              <template x-for="command in controlsByKind(controlModalDevice, 'hue_sat')" :key="command.key">
                <div class="control-row">
                  <div
                    class="colour-field"
                    :class="{ 'is-busy': commandBusyKey === command.key || !isOnline(controlModalDeviceObject()) }"
                    @pointerup="pickColour($event, controlModalDeviceObject(), command)"
                  ></div>
                  <p class="hint" x-text="t('web.devices.control_colour_hint')"></p>
                  <p class="hint" x-show="controlDrafts.hue === undefined"
                     x-text="t('web.devices.control_start_value_unknown')"></p>
                </div>
              </template>
            </div>
```

`@pointerup` statt `@click`: das ist das Loslassen (Abschnitt 6.5), und es deckt Maus und Finger in einem Ereignis ab.

- [ ] **Step 6: Der Farbverlauf im Stil**

In `style.css`:

```css
/* Farbflaeche: waagerecht der Farbton, senkrecht die Saettigung. Zwei
   uebereinanderliegende Verlaeufe statt eines Bildes - so bleibt die
   Flaeche skalierbar und kommt ohne eine Datei aus, die ausgeliefert
   werden muesste. */
.colour-field {
  height: 140px;
  border-radius: 8px;
  cursor: crosshair;
  touch-action: none;
  background:
    linear-gradient(to top, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0)),
    linear-gradient(
      90deg,
      #f00 0%, #ff0 17%, #0f0 33%, #0ff 50%, #00f 67%, #f0f 83%, #f00 100%
    );
}

.colour-field.is-busy {
  opacity: 0.5;
  pointer-events: none;
}

.control-tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0.75rem;
}

.control-tabs .is-active {
  font-weight: 600;
}
```

- [ ] **Step 7: Prüfen und committen**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Farbflaeche und Modus-Reiter im Bedien-Modal"
```

---

### Task 9: Durchgang im Browser und an der echten Leuchte

Die vorangegangenen Tasks belegen, dass die Dateien ausgeliefert werden. Ob die Alpine-Bindungen greifen und ob die Farben stimmen, kann nur ein echter Durchgang zeigen.

**Files:**
- Modify: `src/loxmatter/commands/color.py` (nur der Warnabsatz)
- Modify: `README.md`, ggf. `docs/screenshots/`

- [ ] **Step 1: Die Anwendung starten und beide Leuchten öffnen**

```bash
uv run loxmatter run --bridge-ip <ip>
```

Im Browser: Gerätekachel beider Leuchten, „Steuern" klicken.

- [ ] **Step 2: Diese Punkte einzeln prüfen**

| Prüfung | Erwartung |
| --- | --- |
| CCT-Leuchte | Kelvin-Regler, **keine** Tableiste, keine Farbfläche |
| RGBW-Leuchte | Tableiste Weiß/Farbe, beides bedienbar |
| Kelvin-Regler | Enden entsprechen den Grenzen der Leuchte, nicht 2000–6500 |
| Startwerte | Regler stehen dort, wo die Leuchte gerade steht — kein Sprung beim ersten Zug |
| Aktiver Reiter | entspricht dem Modus, in dem die Leuchte gerade ist |
| Farbfläche | Klick auf Rot/Grün/Blau macht die Leuchte rot/grün/blau |
| Loslassen | ein Zug erzeugt **ein** Kommando, nicht viele (Browser-Netzwerkfenster) |
| Offline | Gerät ausschalten → alle Bedienelemente gesperrt |
| Beide Sprachen | umschalten, keine leeren Beschriftungen |

Weicht die Farbe sichtbar ab, ist der Fehler in `hueSatToLoxone` (Task 8, Schritt 3) oder in `loxone_rgb_to_rgb` (Task 2) — **nicht** an der Leuchte. Das ist genau der Fall, vor dem `color.py` warnt.

- [ ] **Step 3: Die Hardware-Warnung in `color.py` entfernen**

Erst jetzt, und nur wenn Schritt 2 vollständig durchgelaufen ist. Der Absatz „ACHTUNG - dieser Teil ist NICHT an Hardware validiert..." wird ersetzt durch:

```
Gegengeprueft am 7. September 2026 an zwei IKEA-Leuchten (CCT und RGBW),
eingecheckt als tests/fixtures/nodes/ikea_kajplats_ws_lamp.json und
ikea_kajplats_cws_lamp.json. Bis dahin stand hier die Warnung, dieser Teil sei nie
an Hardware gelaufen - beim Bau stand keine Matter-Leuchte zur Verfuegung.
Die Umrechnung bleibt die fehleranfaelligste im Projekt: ein Fehler hier
sieht nach einem Geraetefehler aus, nicht nach einem Rechenfehler.
```

**Bleibt Schritt 2 unvollständig, bleibt die Warnung stehen.** Sie ist wahr, solange sie nicht widerlegt ist.

- [ ] **Step 4: README nachziehen**

Im Abschnitt zu den Bedienelementen ergänzen, dass Farbe und Farbtemperatur aus der Oberfläche gesetzt werden können. Wenn dort Screenshots stehen, einen des Modals ergänzen — dem Muster der vorhandenen Bilder in `docs/screenshots/` folgend.

- [ ] **Step 5: Prüfen und committen**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add -A
git commit -m "docs: Farbumrechnung an echten Leuchten gegengeprueft"
```

---

## Reihenfolge und Abhängigkeiten

```
Task 1 (Tor)
   └── Task 2 ── Task 3 ─┐
       Task 4 ───────────┼── Task 6 ── Task 7 ── Task 8 ── Task 9
       Task 5 ───────────┘
```

Tasks 4 und 5 hängen nicht voneinander ab und können parallel laufen; beide berühren `clusters.yaml` und `table.py`, also nacheinander committen.
