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

### Task 3: Enable command 6 — table and translator together

The existing consistency test via `known_command_pairs()` requires both to change together. That's why this is a single task, not a pair.

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/commands/translate.py`
- Test: `tests/commands/test_translate.py`

**Interfaces:**
- Consumes: `loxone_rgb_to_rgb` from Task 2.
- Produces: the pair `(768, 6)` with slug `color`, `takes_value: true`. Payload `{"hue": int, "saturation": int, "transitionTime": 0}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/commands/test_translate.py` (reuse the helper there for building a `StoredCommand` — it is at the start of the file):

```python
def test_a_packed_loxone_colour_becomes_hue_and_saturation():
    """Pure red: hue 0, full saturation (254). The path is
    Loxone number -> RGB -> Hue/Sat, so that WebUI and Loxone use the same
    translator (design 2026-09-07, section 6.5)."""
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
    """A channel above 100% returns 400, not a made-up
    colour on the device."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "999999999")


def test_colour_rejects_text():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "red")
```

`cmd(cluster, command, takes_value=False)` already sits at the top of the file (line 30) and builds the `StoredCommand` — the same helper `(768, 10)` is already checked with there.

- [ ] **Step 2: Run the test, check the failure**

Run: `uv run pytest tests/commands/test_translate.py -v`
Expected: FAIL — `UnsupportedValueError` for a *valid* red, because `(768, 6)` is not yet in any builder.

- [ ] **Step 3: Add the table entry**

In `src/loxmatter/profiles/clusters.yaml`, cluster 768, under `commands:` next to the existing `10:` entry:

```yaml
      # MoveToHueAndSaturation - enabled on September 7, 2026
      # (design 2026-09-07). The earlier block was based on a
      # mix-up: `translate.py` justified it with "Loxone RGB not
      # backed", while `commands/color.py` backs RGB with an official
      # source and leaves only Lumitech open. The value is the
      # packed Loxone colour number; backed against the checked-in
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

(Read the existing import line and add `marked_non_functional`, don't replace.)

Change the end of the function — currently:

```python
    if known_attribute_section(ref.cluster_id):
        return names_element(ref)
```

becomes:

```python
    if known_attribute_section(ref.cluster_id):
        return names_element(ref) and not marked_non_functional(ref)
```

And add this to layer 3 of the docstring:

```
   ... there only the named elements - minus those the
   table explicitly marks with `functional: false` (device constants
   like min/max ranges, see `marked_non_functional`).
```

- [ ] **Step 6: Run tests, check success**

Run: `uv run pytest tests/profiles/ -v`
Expected: all PASS.

**Warning:** `tests/profiles/test_real_device_fixtures.py` counts signals from the plug and the switch. The new attributes live in cluster 768, which neither has — the numbers must not change. If they do, that's a real finding, not an expected value to adjust.

- [ ] **Step 7: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/profiles/
git add tests/profiles/test_relevance.py tests/profiles/test_table.py
git commit -m "feat(profiles): Geraetekonstanten lesbar, aber nicht vorausgewaehlt"
```

---

### Task 6: `CommandOut.control` and `range`

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/control.py`
- Modify: `src/loxmatter/loxone/server.py:499`
- Test: `tests/api/test_control.py`

**Interfaces:**
- Consumes: `command_control` (Task 4), the attributes from Task 5.
- Produces: `CommandOut` with `control: str` and `range: ControlRange | None`; `ControlRange` has `min: int` and `max: int` in **Kelvin**. `build_control_router(store, invoke, values)` — third parameter is new.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_control.py`. The existing `api` fixture loads the plug; for these tests create your own fixture following the same pattern, but with the RGBW lamp:

```python
@pytest.fixture
async def api_lamp(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int, object]]:
    """Like `api`, but with the checked-in RGBW lamp - the only
    template that carries both colour and colour temperature commands."""
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
    """Mired -> Kelvin is a reciprocal: the smaller mired gives the
    LARGER Kelvin, so min and max swap (design 2026-09-07,
    section 5.5)."""
    client, store, device_id, runtime = api_lamp
    keys = {
        signal.ref.element_id: signal.key
        for signal in store.signals(device_id)
        if signal.ref.cluster_id == 768 and signal.ref.element_id in (16395, 16396)
    }
    # The actual values from the checked-in CWS lamp.
    runtime.seed(keys[16395], 153)  # 153 Mired = 6535 K
    runtime.seed(keys[16396], 555)  # 555 Mired = 1801 K

    response = await client.get(f"/api/devices/{device_id}/controls")
    colortemp = next(c for c in response.json()["commands"] if c["slug"] == "colortemp")
    assert colortemp["range"] == {"min": 1801, "max": 6535}


async def test_without_the_limits_there_is_no_range(api_lamp):
    """No range is better than a made-up one - the UI then falls
    back to the number field (design 2026-09-07, section 9.2)."""
    client, _store, device_id, _runtime = api_lamp  # nothing seeded
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

- [ ] **Step 2: Run tests, check failure**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: FAIL with `KeyError: 'control'`

- [ ] **Step 3: Extend the models**

In `src/loxmatter/api/models.py`, before `CommandOut`:

```python
class ControlRange(BaseModel):
    """Limits of a slider, in the unit the UI displays.

    Today only for colour temperature, in Kelvin. The conversion from mired
    happens on the server, not in JavaScript: it is a reciprocal where
    min and max swap - a trap you don't want to set twice (design 2026-09-07,
    section 5.5)."""

    model_config = ConfigDict(frozen=True)

    min: int
    max: int
```

Extend `CommandOut` with two fields (leave existing fields and the docstring as they are, only add):

```python
    control: str
    range: ControlRange | None = None
```

And append to the `CommandOut` docstring:

```
    `control` says WHICH control widget should be built (`none`,
    `percent`, `kelvin`, `hue_sat`, `unknown`) - see
    `profiles.table.command_control`. `takes_value` remains alongside
    it because it answers something different: whether the EXPORT creates
    an analogue or digital output.
```

`ControlsOut` stays unchanged.

- [ ] **Step 4: Extend the route**

In `src/loxmatter/api/control.py`:

Add imports:

```python
from typing import Protocol

from loxmatter.api.models import CommandOut, ControlRange, ControlsOut, ValueIn
from loxmatter.profiles.table import command_control, command_slug
```

After the `Invoker` line:

```python
class ValueReader(Protocol):
    """What this route needs from `runtime` - read-only.

    Deliberately narrower than `api.devices.RuntimeValues`: the control route
    sets nothing online, and a protocol that demands more than it uses
    forces every test into a bigger double than the case needs.
    `loxone.runtime.Runtime` satisfies both.
    """

    def last_values_for(self, device_id: int) -> dict[str, float | bool]: ...


# ColorTempPhysicalMinMireds / ColorTempPhysicalMaxMireds, verified
# against the installed SDK (chip.clusters.Objects.ColorControl.Attributes).
_CLUSTER_COLOR = 768
_ATTR_CT_PHYS_MIN_MIREDS = 16395
_ATTR_CT_PHYS_MAX_MIREDS = 16396
```

Change the signature:

```python
def build_control_router(store: Store, invoke: Invoker, values: ValueReader) -> APIRouter:
```

Insert a helper before the `controls` route:

```python
    def _kelvin_range(device_id: int, endpoint: int) -> ControlRange | None:
        """The colour temperature range of the lamp, in Kelvin - or None.

        Kelvin = 1e6 / Mired is a reciprocal: the SMALLER mired gives the
        LARGER Kelvin, so min and max swap during conversion.

        None instead of a fallback range if the lamp doesn't report the limits:
        a slider that ends at 6500 K even though the device stops at
        4000 K lets you set a value it silently clips - exactly the silent
        failure this view should uncover (Spec 8.1).
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

In the `controls` route, replace the list building:

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

- [ ] **Step 5: Wire the router**

In `src/loxmatter/loxone/server.py` line 499:

```python
    app.include_router(build_control_router(store, invoke, runtime), dependencies=api_guard)
```

The comment above it stays.

- [ ] **Step 6: Run tests, check success**

Run: `uv run pytest tests/api/ -v`
Expected: all PASS. For 250 Mired → 4000 K and 454 Mired → 2202 K (clipped in each case, as `kelvin_to_mireds` does in the opposite direction).

- [ ] **Step 7: Update the module docstring of `control.py`**

The paragraph "Open point, deliberately not solved here" mentions that the interface `build_control_router(store, invoke)` also "takes no second caller". The signature now has three parameters — but the statement about **writing attributes** remains correct because `values` only reads. Refine the sentence instead of removing it:

```
... and this module's interface (`build_control_router(store,
invoke, values)`) also takes no writing caller; `invoke` is typed
exclusively for commands, `values` only reads (see `ValueReader`), and an
attribute-write access is neither.
```

- [ ] **Step 8: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/api/models.py src/loxmatter/api/control.py src/loxmatter/loxone/server.py tests/api/test_control.py
git commit -m "feat(api): Bedienelement-Typ und Kelvin-Bereich ausliefern"
```

---

### Task 7: Tile button and control modal with sliders

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `CommandOut.control` and `.range` from Task 6.
- Produces: Alpine state `controlModalDevice`, methods `openControlModal(device)`, `closeControlModal()`, `controlsByKind(deviceId, kind)`, `sendControl(device, command, value)`. Task 8 builds on this.

- [ ] **Step 1: Add translations**

In `src/loxmatter/i18n/strings.yaml`, alongside the other `web.devices.*` keys:

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

- [ ] **Step 2: State and methods in `app.js`**

Alongside `signalsModalDevice` (around line 488):

```javascript
    // Like `signalsModalDevice`: just the ID, not the object - see the
    // comment there and `controlModalDeviceObject()`.
    controlModalDevice: null,
```

In the methods block, alongside the signal-modal methods:

```javascript
    controlModalDeviceObject() {
      return this.devices.find((device) => device.id === this.controlModalDevice) || null;
    },

    /**
     * Opens the control modal. The `$nextTick` is required, not style -
     * same reasoning as `openSignalsModal`: `showModal()` sets
     * initial focus to the first focusable element IN the dialog, and
     * that only exists after Alpine builds the `x-if` content.
     */
    openControlModal(device) {
      this.deviceActionError = null;
      this.controlModalDevice = device.id;
      this.controlDrafts = this.readStartValues(device.id);
      this.$nextTick(() => this.$refs.controlModal.showModal());
    },

    /** Closes via `close()` so the `close` handler in index.html
     * remains the one place that resets `controlModalDevice` -
     * same rule as `closeSignalsModal`. */
    closeControlModal() {
      this.$refs.controlModal.close();
    },

    /** All commands of a device with exactly this control widget type. */
    controlsByKind(deviceId, kind) {
      return this.commandsFor(deviceId).filter((command) => command.control === kind);
    },

    /** Whether this device can do anything with a value at all - only then
     * does the tile get a "Control" button. */
    hasAdjustableControls(deviceId) {
      return this.commandsFor(deviceId).some((command) => command.control !== "none");
    },
```

- [ ] **Step 3: Read start values**

Also in `app.js`:

```javascript
    // The paths under which a device's signals carry start values.
    // `signalsByDevice` holds ALL signals, not just the
    // exported ones (see api/devices.py, `get_signals`), and their values
    // are already scaled (loxone/values.py, `to_loxone_value`) - level
    // and saturation as percent, hue as degrees. Only colour temperature stands
    // in mired, because Kelvin is a reciprocal that `scale` cannot handle.
    // Searched by PATH, not by slug in the key: if a key collides within a device,
    // `Store._assign_key` appends the element ID (`d1_1_hue_0`), and a comparison
    // on `_hue` would then miss it. `SignalOut.path` is "endpoint/cluster/element",
    // so it's exact.
    signalValueByPath(deviceId, clusterId, elementId) {
      const signals = this.signalsByDevice[deviceId] || [];
      const signal = signals.find((entry) => entry.path.endsWith(`/${clusterId}/${elementId}`));
      return signal ? this.liveValueOf(signal) : undefined;
    },

    /**
     * Read once on opening, then NOT updated (design
     * 2026-09-07, section 2). Without these start values, every slider
     * would stand at a made-up position, and the first drag would move the lamp
     * somewhere - then the click would prove nothing about the state it
     * just changed.
     *
     * `undefined` stays `undefined` and is not replaced by null:
     * the UI shows the hint "Start value unknown" instead,
     * rather than pretending to know what it doesn't.
     */
    readStartValues(deviceId) {
      // Cluster 8 attribute 0 = CurrentLevel; Cluster 768: 0 = CurrentHue,
      // 1 = CurrentSaturation, 7 = ColorTemperatureMireds, 8 = ColorMode.
      // All verified against the installed SDK (see design, section 4).
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

And create the draft storage alongside `commandValueDrafts`:

```javascript
    controlDrafts: {},
```

- [ ] **Step 4: Send**

```javascript
    /**
     * Sends a slider value. Called on RELEASE (`change`), not
     * during dragging: one drag = one radio packet. Thread is slow,
     * and when a click should prove something, the mapping between
     * input and reaction must stay unambiguous (design 2026-09-07,
     * section 6.5).
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

- [ ] **Step 5: Rebuild the tile**

In `src/loxmatter/web/index.html`, in the `device-commands` block: restrict the `<template x-for>` loop to render only valueless commands, and add the button. The branch for `command.takes_value` with the number field **is removed from here** — it goes into the modal:

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

The three following hint lines (`hiddenRawCommandsFor`, `controlsLoading`, `no_known_commands`) remain **unchanged**.

- [ ] **Step 6: Create the modal**

Beside the existing signal modal in `index.html`, add a second `<dialog>` following the same pattern (`x-ref="controlModal"`, `@close` sets `controlModalDevice = null`, `@click` with `isBackdropEvent`). Content:

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

The `hue_sat` block follows in Task 8.

- [ ] **Step 7: Styling**

In `style.css` alongside the existing modal rules:

```css
/* Control modal: one row per slider, label above the slider instead of
   beside - on narrow windows the slider stays too short to aim at. */
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

- [ ] **Step 8: Add delivery test**

Append to `tests/api/test_web.py` (following the pattern there):

```python
async def test_the_control_modal_is_delivered(client):
    """Proves ONLY delivery. This test cannot tell whether the Alpine expressions in it
    actually bind - the browser run-through in Task 9 tests that."""
    response = await client.get("/app.js")
    assert "openControlModal" in response.text
    assert "readStartValues" in response.text
```

- [ ] **Step 9: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): Bedien-Modal mit Reglern statt nackter Zahlenfelder"
```

---

### Task 8: Colour field and mode tabs

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`

**Interfaces:**
- Consumes: everything from Task 7.
- Produces: `hasColourTabs(deviceId)`, `controlTab`, `hueSatToLoxone(hue, saturation)`, `pickColour(event, device, command)`.

- [ ] **Step 1: Add translations**

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

- [ ] **Step 2: The conversion in JavaScript**

In `app.js`:

```javascript
    /**
     * Hue (degrees) and saturation (percent) to the packed Loxone number
     * that `POST /api/commands/{key}` expects.
     *
     * Why the detour through Loxone encoding instead of sending Hue/Sat directly:
     * WebUI and Loxone use the same translator (`commands/translate.py`, Spec 4.2).
     * A click here goes through exactly the path Loxone later takes - if it works
     * here, the Loxone path is proven. The price is quantization to whole
     * percent per channel (design 2026-09-07, section 9.1).
     *
     * Brightness does NOT go in this number - it goes through
     * LevelControl. That's why the value part here is fixed at 1.
     */
    hueSatToLoxone(hue, saturation) {
      // Textbook HSV to RGB with fixed v = 1: brightness does NOT
      // go in this number, it goes through LevelControl.
      const h = (((hue % 360) + 360) % 360) / 60;
      const s = Math.max(0, Math.min(100, saturation)) / 100;
      const c = s;                                   // Chroma at v = 1
      const x = c * (1 - Math.abs((h % 2) - 1));
      const m = 1 - c;                               // White component
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

**This function needs its own verification step** — see step 3. It is the most error-prone place in the whole plan, because an error here looks like a device error, not a calculation error (the same warning `color.py` writes about itself).

- [ ] **Step 3: Verify the conversion in the browser**

A throwaway harness instead of a unit test, because the function lives in the browser (rule from earlier rounds: a delivery test only proves delivery).

Create a file in the scratchpad that includes `hueSatToLoxone` and tests these cases, and open it in the browser:

| Input | Expected packed number | Why |
| --- | --- | --- |
| `(0, 100)` | `100` | pure red: r=100%, g=0, b=0 |
| `(120, 100)` | `100000` | pure green |
| `(240, 100)` | `100000000` | pure blue |
| `(0, 0)` | `100100100` | no saturation = white |

If a value differs, the formula is wrong — **don't adjust the expectation**, correct the formula until all four match. The three primary colours and white are exactly the cases where a swapped mapping stands out (same reasoning as in the docstring of `rgb_to_hue_saturation`).

- [ ] **Step 4: Tabs and field**

In `app.js`:

```javascript
    // The active tab of the control modal. Set on opening from the device's
    // `colormode` signal (0 = Hue/Sat, 2 = Mired,
    // verified against the SDK) - the modal doesn't guess the mode, it
    // reads it.
    controlTab: "white",

    /** Tabs only if the device can do BOTH. A CCT lamp
     * gets no tab bar this way - without a single query on
     * device type or model (design 2026-09-07, section 6.3). */
    hasColourTabs(deviceId) {
      return (
        this.controlsByKind(deviceId, "kelvin").length > 0 &&
        this.controlsByKind(deviceId, "hue_sat").length > 0
      );
    },

    /** Converts a click on the colour field to hue and saturation
     * and sends it. The field is horizontally the hue (0-360°),
     * vertically the saturation (top 100%, bottom 0%). */
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

In `openControlModal`, set the tab from the device — add to the `controlDrafts` line:

```javascript
      this.controlTab = this.controlDrafts.colormode === 0 ? "colour" : "white";
```

- [ ] **Step 5: The markup**

In the modal, before the `kelvin` block, the tab bar:

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

Wrap the `kelvin` block from Task 7 in an `x-show` — visible when there are no tabs or the white tab is active:

```html
            <div x-show="!hasColourTabs(controlModalDevice) || controlTab === 'white'">
              <!-- the kelvin block from Task 7, unchanged -->
            </div>
```

And the colour block:

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

`@pointerup` instead of `@click`: this is the release (section 6.5), and it covers mouse and touch in one event.

- [ ] **Step 6: The colour gradient in the style**

In `style.css`:

```css
/* Colour field: horizontally the hue, vertically the saturation. Two
   overlaid gradients instead of an image - this keeps the
   field scalable and needs no file to be delivered. */
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

- [ ] **Step 7: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Farbflaeche und Modus-Reiter im Bedien-Modal"
```

---

### Task 9: Run through the browser and with the real lamp

The prior tasks prove the files are delivered. Whether the Alpine bindings work and whether the colours are correct, only a real run-through can show.

**Files:**
- Modify: `src/loxmatter/commands/color.py` (only the warning paragraph)
- Modify: `README.md`, possibly `docs/screenshots/`

- [ ] **Step 1: Start the application and open both lamps**

```bash
uv run loxmatter run --bridge-ip <ip>
```

In the browser: device tile for both lamps, click "Control".

- [ ] **Step 2: Check these points one by one**

| Check | Expectation |
| --- | --- |
| CCT lamp | Kelvin slider, **no** tab bar, no colour field |
| RGBW lamp | Tab bar White/Colour, both usable |
| Kelvin slider | Ends match the lamp's limits, not 2000–6500 |
| Start values | Sliders stand where the lamp currently is — no jump on first drag |
| Active tab | matches the mode the lamp is currently in |
| Colour field | Click on red/green/blue makes the lamp red/green/blue |
| Release | one drag creates **one** command, not many (browser network window) |
| Offline | Turn device off → all controls locked |
| Both languages | switch, no empty labels |

If the colour noticeably differs, the error is in `hueSatToLoxone` (Task 8, step 3) or in `loxone_rgb_to_rgb` (Task 2) — **not** on the lamp. That is exactly the case `color.py` warns about.

- [ ] **Step 3: Remove the hardware warning in `color.py`**

Only now, and only if step 2 ran completely. The paragraph "WARNING - this part is NOT validated against hardware..." is replaced with:

```
Verified on 7 September 2026 against two IKEA lamps (CCT and RGBW),
checked in as tests/fixtures/nodes/ikea_kajplats_ws_lamp.json and
ikea_kajplats_cws_lamp.json. Until then, the warning stood here that this part was never
run against hardware - no Matter lamp was available at build time.
The conversion remains the most error-prone in the project: an error here
looks like a device error, not a calculation error.
```

**If step 2 stays incomplete, the warning stays.** It is true as long as it is not disproven.

- [ ] **Step 4: Update README**

Add to the controls section that colour and colour temperature can be set from the UI. If screenshots are there, add one of the modal — following the pattern of existing images in `docs/screenshots/`.

- [ ] **Step 5: Check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add -A
git commit -m "docs: colour conversion verified against real lamps"
```

---

## Order and dependencies

```
Task 1 (Gate)
   └── Task 2 ── Task 3 ─┐
       Task 4 ───────────┼── Task 6 ── Task 7 ── Task 8 ── Task 9
       Task 5 ───────────┘
```

Tasks 4 and 5 do not depend on each other and can run in parallel; both touch `clusters.yaml` and `table.py`, so commit them sequentially.
