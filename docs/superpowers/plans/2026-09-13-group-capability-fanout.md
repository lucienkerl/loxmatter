# Mixed Light Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A light group offers every light command any member has, and each member receives from one Loxone value whatever part of it that member can carry.

**Architecture:**
- A pure adapter, `commands/adapt.py`, turns one group light command and one member's stored light rows into that member's device calls.
- `commands/translate.py` gains a shared decoder and small payload helpers so the device path and the adapter build identical payloads.
- `Store.register_group_commands` takes the union for light pairs and keeps the intersection for everything else.
- `Store.group_targets` hands every member its light rows.
- `plan_group_calls` routes light commands through the adapter.

**Tech Stack:** Python 3.12, pytest (+ pytest-asyncio auto mode), SQLite store, Alpine.js web UI, i18n via `src/loxmatter/i18n/strings.yaml`.

**Spec:** `docs/superpowers/specs/2026-09-13-group-capability-fanout-design.md`. Read Sections 3 and 4 before any task.

## Global Constraints

- Everything in the repository is English, except `de:` values in `src/loxmatter/i18n/strings.yaml` (CLAUDE.md).
- Every user-visible string goes into `strings.yaml` with `en` **and** `de`, resolved at call time with `i18n.t(...)`. German web copy uses the formal "Sie".
- Every new source file carries the 15-line GPL header, copied from an existing file such as `src/loxmatter/commands/fanout.py`.
- No plan task numbers and no TRANSITIONAL markers in `src/`.
- Store migrations are additive only. This plan needs **no** migration and **no** schema change.
- **The single-device command path must not change behaviour.** Every existing test in `tests/commands/test_translate.py` and `tests/commands/test_translate_error_messages.py` passes unmodified.
- Light command pairs, exactly: (6,0) `off`, (6,1) `on`, (6,2) `toggle`, (8,0) `level`, (8,4) `level_onoff`, (768,6) `color`, (768,7) `color_xy`, (768,10) `colortemp`.
- The colour command a colour lamp gets is the one the group command names when the member carries it (HS for `color`, XY for `color_xy`), and the other one only when it does not.
- Brightness 0 means off: `MoveToLevelWithOnOff` (8,4) level 0 if carried, else `off` for an on/off light. No colour call at 0.
- Brightness uses (8,4) when carried, (8,0) only when that is all there is, and `on`/`off` for an on/off-only light.
- A member receiving no calls is not a failure.
- Invalid values are rejected with 400 before anything is sent.
- Kelvin → CIE xy uses Kim et al. (2002). Clamp Kelvin to 1667–25000.
- Commits use Conventional Commits and end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Tests:** the full suite takes more than 10 minutes. Run it in four parts, each as a separate foreground command:
  - A1: `uv run pytest -q tests/api` (must run alone)
  - A2: `uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee`
  - B: `uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py`
  - C: `uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py`
- **Checks:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- **Fault injection for every protective test.** Break the code, purge `__pycache__`, watch the test FAIL, restore, watch it PASS. Report both outputs.
- Test module basenames must be unique: there is no `__init__.py` under `tests/`.

## File Map

| File | Change | Responsibility |
|---|---|---|
| `src/loxmatter/commands/color.py` | modify | `planckian_xy`, `kelvin_to_cie_xy`, `kelvin_to_hue_saturation` |
| `src/loxmatter/profiles/light_commands.py` | create | the eight light pairs as named constants and `LIGHT_COMMAND_PAIRS` |
| `src/loxmatter/commands/translate.py` | modify | `LoxoneColour`, `decode_loxone_colour`, `level_from_percent`, payload helpers; builders reuse them |
| `src/loxmatter/commands/adapt.py` | create | `adapt_group_command(pair, rows, value)` |
| `src/loxmatter/model/store.py` | modify | union for light pairs in `register_group_commands`; light rows in `group_targets` |
| `src/loxmatter/commands/fanout.py` | modify | `plan_group_calls(command, targets, value)` |
| `src/loxmatter/loxone/server.py`, `src/loxmatter/api/control.py` | modify | pass the group command to `plan_group_calls` |
| `src/loxmatter/i18n/strings.yaml`, `src/loxmatter/web/index.html` | modify | members hint; reworded remove note |
| `CHANGELOG.md`, `docs/superpowers/specs/2026-09-10-device-groups-design.md` | modify | copy and a pointer note |
| Tests | create/modify | `tests/commands/test_color.py`, `tests/commands/test_adapt.py`, `tests/commands/test_fanout.py`, `tests/model/test_store_groups.py`, `tests/api/test_group_control.py`, `tests/api/test_web.py` |

---

### Task 1: White as a colour point

**Files:**
- Modify: `src/loxmatter/commands/color.py` (append after `rgb_to_cie_xy`)
- Test: `tests/commands/test_color.py` (append)

**Interfaces:**
- Produces:
  - `planckian_xy(kelvin: float) -> tuple[float, float]`
  - `kelvin_to_cie_xy(kelvin: float) -> tuple[int, int]` (ZCL-encoded, same scale as `rgb_to_cie_xy`)
  - `kelvin_to_hue_saturation(kelvin: float) -> tuple[int, int]` (same scale as `rgb_to_hue_saturation`)

- [ ] **Step 1: Write the failing tests** (append to `tests/commands/test_color.py`)

```python
from loxmatter.commands.color import kelvin_to_cie_xy, kelvin_to_hue_saturation, planckian_xy


@pytest.mark.parametrize(
    ("kelvin", "x", "y"),
    [
        # Computed from Kim et al. (2002)'s published coefficients, not from
        # this code: 2700 K is the warm end of Loxone's Lumitech range,
        # 6500 K the cold end, 4000 K the branch boundary of both cubics.
        (2700, 0.4593, 0.4107),
        (4000, 0.3805, 0.3767),
        (6500, 0.3135, 0.3237),
    ],
)
def test_planckian_xy_matches_the_published_approximation(kelvin, x, y):
    got_x, got_y = planckian_xy(kelvin)
    assert got_x == pytest.approx(x, abs=0.0005)
    assert got_y == pytest.approx(y, abs=0.0005)


def test_planckian_xy_clamps_to_the_range_the_approximation_is_valid_for():
    """Outside 1667-25000 K the cubics diverge. A white a lamp cannot reach
    is approximated by the nearest one it can, like a tunable-white lamp
    clamping to its own physical limits.

    Fault to prove it: remove the clamp - 1000 K then lands far off the
    locus and the equality below fails."""
    assert planckian_xy(1000) == planckian_xy(1667)
    assert planckian_xy(30000) == planckian_xy(25000)
    assert planckian_xy(1667) == pytest.approx((0.5646, 0.4029), abs=0.0005)
    assert planckian_xy(25000) == pytest.approx((0.2525, 0.2523), abs=0.0005)


def test_kelvin_to_cie_xy_uses_the_zcl_encoding():
    assert kelvin_to_cie_xy(2700) == (30102, 26913)
    assert kelvin_to_cie_xy(6500) == (20545, 21212)


def test_kelvin_to_hue_saturation_gives_a_warm_white_and_a_near_white():
    """2700 K is an orange-ish, clearly desaturated white; 6500 K is almost
    exactly D65 and therefore nearly unsaturated. Values computed through
    xy -> sRGB (IEC 61966-2-1 inverse matrix and OETF) -> HSV."""
    assert kelvin_to_hue_saturation(2700) == (21, 165)
    hue, saturation = kelvin_to_hue_saturation(6500)
    assert saturation <= 8
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/commands/test_color.py -k "planckian or kelvin_to_cie or kelvin_to_hue"`
Expected: collection error: `ImportError: cannot import name 'kelvin_to_cie_xy'`.

- [ ] **Step 3: Implement** (append to `src/loxmatter/commands/color.py`, after `rgb_to_cie_xy`)

```python
# The Planckian locus - the colours of an ideal black body, which is what a
# "2700 K" white means - as Kim, Kim, Lee, Kim & Kim (2002), "Design of
# advanced color temperature control system for HDTV applications",
# Journal of the Korean Physical Society 41(6), approximate it: cubic
# polynomials in 1/T for x, and in x for y, valid from 1667 K to 25000 K.
# Used for a colour lamp that has no ColorTemperature command but is sent a
# white (design 2026-09-13, section 3.3).
_PLANCKIAN_MIN_KELVIN = 1667.0
_PLANCKIAN_MAX_KELVIN = 25000.0

# IEC 61966-2-1's own XYZ -> linear sRGB matrix, the inverse of
# `_SRGB_TO_XYZ` above.
_XYZ_TO_SRGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


def planckian_xy(kelvin: float) -> tuple[float, float]:
    """CIE 1931 (x, y) of a black body at `kelvin`, clamped to the range the
    approximation holds for."""
    t = min(_PLANCKIAN_MAX_KELVIN, max(_PLANCKIAN_MIN_KELVIN, kelvin))
    t2 = t * t
    t3 = t2 * t
    if t <= 4000:
        x = -0.2661239e9 / t3 - 0.2343589e6 / t2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t3 + 2.1070379e6 / t2 + 0.2226347e3 / t + 0.240390
    x2 = x * x
    x3 = x2 * x
    if t <= 2222:
        y = -1.1063814 * x3 - 1.34811020 * x2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x3 - 1.37418593 * x2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x3 - 5.87338670 * x2 + 3.75112997 * x - 0.37001483
    return x, y


def kelvin_to_cie_xy(kelvin: float) -> tuple[int, int]:
    """A white temperature as ZCL CurrentX/CurrentY, for a lamp that takes
    MoveToColor but not MoveToColorTemperature."""
    x, y = planckian_xy(kelvin)
    return _to_cie_component(x), _to_cie_component(y)


def _compress_gamma(linear: float) -> float:
    if linear <= 0.0031308:
        return 12.92 * linear
    return 1.055 * math.pow(linear, 1 / 2.4) - 0.055


def kelvin_to_hue_saturation(kelvin: float) -> tuple[int, int]:
    """A white temperature as Matter hue/saturation, for a lamp that only
    takes MoveToHueAndSaturation.

    xy -> XYZ at Y = 1 -> linear sRGB, negatives clipped (the locus leaves
    the sRGB gamut at its warm end), normalised to the brightest channel so
    only the chromaticity is kept - brightness travels separately, through
    LevelControl - then gamma-encoded and handed to `rgb_to_hue_saturation`.
    """
    x, y = planckian_xy(kelvin)
    xyz = (x / y, 1.0, (1.0 - x - y) / y)
    linear = [
        max(0.0, row[0] * xyz[0] + row[1] * xyz[1] + row[2] * xyz[2]) for row in _XYZ_TO_SRGB
    ]
    peak = max(linear)
    red, green, blue = (round(255 * _compress_gamma(channel / peak)) for channel in linear)
    return rgb_to_hue_saturation(red, green, blue)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -q tests/commands/test_color.py`
Expected: all pass.

- [ ] **Step 5: Fault-inject the clamp test**

Change `t = min(...)` to `t = kelvin`, purge `__pycache__`, and run the clamp test. Expected: FAIL. Restore, then run again. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/commands/color.py tests/commands/test_color.py
git commit -m "feat(commands): convert a white temperature into a colour point

A colour lamp without a colour-temperature command can still show a white
when a group sends one: the Kelvin value becomes a point on the Planckian
locus, as XY or as hue and saturation.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: One decoder and the per-member adapter

**Files:**
- Create: `src/loxmatter/profiles/light_commands.py`
- Modify: `src/loxmatter/commands/translate.py`
- Create: `src/loxmatter/commands/adapt.py`
- Test: create `tests/commands/test_adapt.py`; append to `tests/commands/test_translate.py`

**Interfaces:**
- Consumes: `kelvin_to_cie_xy`, `kelvin_to_hue_saturation` from Task 1.
- Produces:
  - `loxmatter.profiles.light_commands`: `OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE: tuple[int, int]` and `LIGHT_COMMAND_PAIRS: frozenset[tuple[int, int]]`
  - `loxmatter.commands.translate`:
    - `LoxoneColour(kelvin: int | None, rgb: tuple[int, int, int] | None, brightness_percent: float)`
    - `decode_loxone_colour(value: str) -> LoxoneColour`
    - `parse_number(value: str) -> float`
    - `level_from_percent(percent: float) -> int`
    - `hue_saturation_payload(hue: int, saturation: int) -> dict[str, object]`
    - `xy_payload(x: int, y: int) -> dict[str, object]`
    - `colour_temperature_payload(kelvin: float) -> dict[str, object]`
  - `loxmatter.commands.adapt`: `adapt_group_command(pair: tuple[int, int], rows: Sequence[StoredCommand], value: str) -> list[DeviceCall]`

- [ ] **Step 1: Create `src/loxmatter/profiles/light_commands.py`** (GPL header first)

```python
"""The commands a light group adapts per member (design 2026-09-13).

Pairs of (cluster, command). Its own module in `profiles` rather than in
`commands`, because `model.store` needs it and `commands` imports the
store - a constant here keeps that import one-way.
"""

from __future__ import annotations

from typing import Final

OFF: Final = (6, 0)
ON: Final = (6, 1)
TOGGLE: Final = (6, 2)
LEVEL: Final = (8, 0)
LEVEL_ONOFF: Final = (8, 4)
COLOUR_HS: Final = (768, 6)
COLOUR_XY: Final = (768, 7)
COLOUR_TEMPERATURE: Final = (768, 10)

LIGHT_COMMAND_PAIRS: Final = frozenset(
    {OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE}
)
```

- [ ] **Step 2: Write the pinning test for the device path** (append to `tests/commands/test_translate.py`; reuse that file's existing helper for building a `StoredCommand` if it has one, otherwise build one inline as below)

```python
from loxmatter.commands.translate import to_device_calls
from loxmatter.model.store import StoredCommand


def _row(cluster_id: int, command_id: int, slug: str) -> StoredCommand:
    return StoredCommand(
        key=f"d1_1_{slug}",
        slug=slug,
        technology="matter",
        address="21",
        endpoint=1,
        cluster_id=cluster_id,
        command_id=command_id,
        takes_value=True,
        device_id=1,
    )


@pytest.mark.parametrize(
    ("cluster_id", "command_id", "slug", "value", "expected"),
    [
        (768, 6, "color", "36060036", [
            (768, 6, {"hue": 85, "saturation": 101, "transitionTime": 0,
                      "optionsMask": 1, "optionsOverride": 1}),
            (8, 4, {"level": 91, "transitionTime": 0}),
        ]),
        (768, 7, "color_xy", "36060036", None),
        (768, 6, "color", "202702700", [
            (768, 10, {"colorTemperatureMireds": 370, "optionsMask": 1, "optionsOverride": 1}),
            (8, 4, {"level": 69, "transitionTime": 0}),
        ]),
        (768, 6, "color", "0", [(8, 4, {"level": 0, "transitionTime": 0})]),
    ],
)
def test_a_single_device_s_colour_calls_are_unchanged_by_the_shared_decoder(
    cluster_id, command_id, slug, value, expected
):
    """Pins what the device path sent BEFORE the decoder was factored out,
    so the refactor for groups cannot move it. Run this test on the
    unmodified code first and paste its real output into `expected` if any
    literal here disagrees - the literals must describe the code as it was,
    not as this plan guessed. `None` means: record the unmodified output and
    pin it the same way."""
    calls = to_device_calls(_row(cluster_id, command_id, slug), value)
    got = [(c.cluster_id, c.command_id, c.payload) for c in calls]
    if expected is not None:
        assert got == expected
```

- [ ] **Step 3: Run it on the unmodified code**

Run: `uv run pytest -q tests/commands/test_translate.py -k shared_decoder`

Replace every literal that disagrees, and both `None` cases, with the real output. Then remove the `if expected is not None` guard so every case asserts. It must PASS on the unmodified code before Step 4.

- [ ] **Step 4: Refactor `src/loxmatter/commands/translate.py`**

Add these below `_level`:

```python
def parse_number(value: str) -> float:
    """`_as_number` for callers outside this module (commands/adapt.py)."""
    return _as_number(value)


def level_from_percent(percent: float) -> int:
    """A brightness percentage as a LevelControl level, the rule `_level`
    applies to a value string."""
    return max(0, min(LEVEL_MAX, round(percent * LEVEL_MAX / 100)))


@dataclass(frozen=True)
class LoxoneColour:
    """One decoded value of the Loxone lighting controller's colour output:
    either a colour (`rgb`) or a Lumitech white (`kelvin`), always with its
    brightness. Exactly one of `kelvin` and `rgb` is set."""

    kelvin: int | None
    rgb: tuple[int, int, int] | None
    brightness_percent: float


def decode_loxone_colour(value: str) -> LoxoneColour:
    """Decodes the colour output's number once, for the device path and the
    group adapter alike. Raises `UnsupportedValueError` with the same
    translated messages the two colour builders raised before."""
    number = _as_number(value)
    if number == int(number) and is_lumitech(int(number)):
        try:
            kelvin = lumitech_to_kelvin(int(number))
        except ValueError as exc:
            raise UnsupportedValueError(
                i18n.t("api.errors.lumitech_malformed", value=value)
            ) from exc
        return LoxoneColour(
            kelvin=kelvin, rgb=None, brightness_percent=lumitech_to_brightness(int(number))
        )
    try:
        red, green, blue = loxone_rgb_to_rgb(number)
    except LoxoneColourError as exc:
        raise UnsupportedValueError(_translate_loxone_colour_error(exc)) from exc
    return LoxoneColour(
        kelvin=None,
        rgb=(red, green, blue),
        brightness_percent=rgb_to_brightness(red, green, blue),
    )


def hue_saturation_payload(hue: int, saturation: int) -> dict[str, object]:
    return {"hue": hue, "saturation": saturation, "transitionTime": 0, **_OPTIONS_EXECUTE_IF_OFF}


def xy_payload(x: int, y: int) -> dict[str, object]:
    return {"colorX": x, "colorY": y, "transitionTime": 0, **_OPTIONS_EXECUTE_IF_OFF}


def colour_temperature_payload(kelvin: float) -> dict[str, object]:
    return {"colorTemperatureMireds": kelvin_to_mireds(kelvin), **_OPTIONS_EXECUTE_IF_OFF}
```

`decode_loxone_colour` references `_translate_loxone_colour_error`, which is defined further down. Move the whole block to just after `_translate_loxone_colour_error`, **not** below `_level`. Only `parse_number` and `level_from_percent` stay below `_level`.

Then replace the **bodies** of the three existing builders. Keep their docstrings unchanged.

```python
def _payload_color_temperature(value: str) -> _Built:
    return _Built(colour_temperature_payload(_as_number(value)))


def _white(colour: LoxoneColour) -> _Built:
    assert colour.kelvin is not None
    return _Built(
        colour_temperature_payload(colour.kelvin),
        command_id=_COMMAND_COLOR_TEMPERATURE,
        brightness_percent=colour.brightness_percent,
    )


def _payload_hue_saturation(value: str) -> _Built:
    # (docstring unchanged)
    colour = decode_loxone_colour(value)
    if colour.kelvin is not None:
        return _white(colour)
    assert colour.rgb is not None
    hue, saturation = rgb_to_hue_saturation(*colour.rgb)
    return _Built(
        hue_saturation_payload(hue, saturation), brightness_percent=colour.brightness_percent
    )


def _payload_color_xy(value: str) -> _Built:
    # (docstring unchanged)
    colour = decode_loxone_colour(value)
    if colour.kelvin is not None:
        return _white(colour)
    assert colour.rgb is not None
    colour_x, colour_y = rgb_to_cie_xy(*colour.rgb)
    return _Built(xy_payload(colour_x, colour_y), brightness_percent=colour.brightness_percent)
```

`_payload_color_temperature` must stay defined before `colour_temperature_payload` is *called*. Python resolves the name at call time, so order in the file is free.

- [ ] **Step 5: Run the device-path tests**

Run: `uv run pytest -q tests/commands/test_translate.py tests/commands/test_translate_error_messages.py tests/commands/test_color.py`
Expected: all pass, including every pre-existing test, unmodified.

- [ ] **Step 6: Write the adapter tests** — create `tests/commands/test_adapt.py` (GPL header first)

```python
"""Per-member adaptation of a light group command - design 2026-09-13, 3.2."""

from __future__ import annotations

import pytest

from loxmatter.commands.adapt import adapt_group_command
from loxmatter.commands.color import kelvin_to_cie_xy, kelvin_to_hue_saturation, rgb_to_cie_xy
from loxmatter.commands.translate import UnsupportedValueError
from loxmatter.model.store import StoredCommand
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    OFF,
    ON,
    TOGGLE,
)

_SLUGS = {
    OFF: "off", ON: "on", TOGGLE: "toggle", LEVEL: "level", LEVEL_ONOFF: "level_onoff",
    COLOUR_HS: "color", COLOUR_XY: "color_xy", COLOUR_TEMPERATURE: "colortemp",
}


def rows(address: str, *pairs: tuple[int, int], endpoint: int = 1) -> list[StoredCommand]:
    return [
        StoredCommand(
            key=f"d9_{endpoint}_{_SLUGS[pair]}",
            slug=_SLUGS[pair],
            technology="matter",
            address=address,
            endpoint=endpoint,
            cluster_id=pair[0],
            command_id=pair[1],
            takes_value=pair not in (OFF, ON, TOGGLE),
            device_id=9,
        )
        for pair in pairs
    ]


# The stored light rows of real devices, as the store holds them.
# KAJPLATS E14 CWS and E27 WS: tests/fixtures/nodes/ikea_kajplats_{cws,ws}_lamp.json.
CWS = rows("21", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE)
WS = rows("14", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_TEMPERATURE)
# TRADFRI bulb E27 WW (Zigbee), read from the maintainer's Pi on 13 September
# 2026 - captured, there is no fixture for it.
WW = rows("14:b4:57:ff:fe:7f:f5:da", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF)
# No on/off-only light exists in the setup: this pair set is constructed.
ONOFF = rows("30", OFF, ON, TOGGLE)
# A colour lamp without colour temperature, XY only and HS only: constructed.
XY_ONLY = rows("31", OFF, ON, LEVEL_ONOFF, COLOUR_XY)
HS_ONLY = rows("32", OFF, ON, LEVEL_ONOFF, COLOUR_HS)

BLUE_60 = "60000000"  # Loxone BBBGGGRRR: blue 60 %, green 0, red 0
WHITE_2700_30 = "200302700"  # Lumitech: marker 20, brightness 030, 2700 K
EIF = {"optionsMask": 1, "optionsOverride": 1}


def shape(calls):
    return [(c.cluster_id, c.command_id, c.payload) for c in calls]


def test_colour_on_the_colour_lamp_sends_the_named_colour_command_then_brightness():
    got = shape(adapt_group_command(COLOUR_HS, CWS, BLUE_60))
    assert [(cluster, command) for cluster, command, _ in got] == [(768, 6), (8, 4)]
    assert got[1][2] == {"level": 152, "transitionTime": 0}


def test_color_xy_on_the_colour_lamp_sends_xy():
    got = shape(adapt_group_command(COLOUR_XY, CWS, BLUE_60))
    x, y = rgb_to_cie_xy(0, 0, 153)
    assert got[0] == (768, 7, {"colorX": x, "colorY": y, "transitionTime": 0, **EIF})


def test_colour_falls_back_to_the_other_colour_command_when_the_named_one_is_missing():
    assert [c.command_id for c in adapt_group_command(COLOUR_HS, XY_ONLY, BLUE_60)] == [7, 4]
    assert [c.command_id for c in adapt_group_command(COLOUR_XY, HS_ONLY, BLUE_60)] == [6, 4]


@pytest.mark.parametrize("member", [WS, WW], ids=["tunable white", "dim only"])
def test_colour_on_a_lamp_without_colour_sets_brightness_only(member):
    """Decision 2: a colour never changes a white lamp's temperature.

    Fault to prove it: send the colour temperature derived from nothing -
    or drop the brightness fallback - and this fails."""
    assert shape(adapt_group_command(COLOUR_HS, member, BLUE_60)) == [
        (8, 4, {"level": 152, "transitionTime": 0})
    ]


def test_colour_on_an_on_off_light_switches_it_on():
    assert shape(adapt_group_command(COLOUR_HS, ONOFF, BLUE_60)) == [(6, 1, {})]


@pytest.mark.parametrize("member", [CWS, WS, WW, ONOFF, XY_ONLY])
def test_brightness_zero_is_a_single_off_and_no_colour(member):
    got = shape(adapt_group_command(COLOUR_HS, member, "0"))
    assert len(got) == 1
    assert got[0][:2] in {(8, 4), (6, 0)}
    if got[0][:2] == (8, 4):
        assert got[0][2]["level"] == 0


def test_white_on_a_lamp_with_colour_temperature_sends_the_temperature():
    got = shape(adapt_group_command(COLOUR_HS, WS, WHITE_2700_30))
    assert got == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF}),
        (8, 4, {"level": 76, "transitionTime": 0}),
    ]
    assert shape(adapt_group_command(COLOUR_HS, CWS, WHITE_2700_30))[0][:2] == (768, 10)


def test_white_on_a_colour_lamp_without_temperature_is_reproduced_as_a_colour_point():
    """Decision 3.

    Fault to prove it: return no colour call when the member has no
    colour-temperature command - both assertions fail."""
    x, y = kelvin_to_cie_xy(2700)
    assert shape(adapt_group_command(COLOUR_XY, XY_ONLY, WHITE_2700_30))[0] == (
        768, 7, {"colorX": x, "colorY": y, "transitionTime": 0, **EIF},
    )
    hue, saturation = kelvin_to_hue_saturation(2700)
    assert shape(adapt_group_command(COLOUR_HS, HS_ONLY, WHITE_2700_30))[0] == (
        768, 6, {"hue": hue, "saturation": saturation, "transitionTime": 0, **EIF},
    )


def test_white_on_a_dim_only_lamp_sets_brightness_only():
    assert shape(adapt_group_command(COLOUR_HS, WW, WHITE_2700_30)) == [
        (8, 4, {"level": 76, "transitionTime": 0})
    ]


def test_level_on_an_on_off_light_switches_it():
    assert shape(adapt_group_command(LEVEL_ONOFF, ONOFF, "40")) == [(6, 1, {})]
    assert shape(adapt_group_command(LEVEL_ONOFF, ONOFF, "0")) == [(6, 0, {})]


def test_level_on_a_dimmable_member_sends_the_same_command_it_names():
    assert shape(adapt_group_command(LEVEL, WW, "40")) == [(8, 0, {"level": 102, "transitionTime": 0})]


def test_colortemp_follows_the_member():
    assert shape(adapt_group_command(COLOUR_TEMPERATURE, WS, "2700")) == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF})
    ]
    assert shape(adapt_group_command(COLOUR_TEMPERATURE, XY_ONLY, "2700"))[0][:2] == (768, 7)


@pytest.mark.parametrize("member", [WW, ONOFF])
def test_colortemp_on_a_member_without_colour_is_nothing_not_an_error(member):
    assert adapt_group_command(COLOUR_TEMPERATURE, member, "2700") == []


def test_on_off_and_toggle_pass_through():
    assert shape(adapt_group_command(TOGGLE, WW, "1")) == [(6, 2, {})]


def test_a_member_with_the_light_on_two_endpoints_gets_calls_on_both_in_order():
    two = rows("40", ON, OFF, LEVEL_ONOFF, endpoint=1) + rows("40", ON, OFF, LEVEL_ONOFF, endpoint=2)
    calls = adapt_group_command(COLOUR_HS, two, BLUE_60)
    assert [(c.endpoint, c.command_id) for c in calls] == [(1, 4), (2, 4)]


def test_an_invalid_colour_value_raises_before_any_call_is_built():
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(COLOUR_HS, WW, "banana")
```

Before relying on them, verify the level numbers used in these tests against `level_from_percent`:
- 60 % → 152
- 30 % → 76
- 40 % → 102

Also verify that `"60000000"` decodes to rgb `(0, 0, 153)` with brightness 60 %. Check this with `uv run python -c "from loxmatter.commands.color import loxone_rgb_to_rgb; print(loxone_rgb_to_rgb(60000000))"`.

If a literal differs, correct the **test literal** to the real conversion, and say so in the report. The conversions are existing, measured code; the literals here were computed by hand.

- [ ] **Step 7: Run to verify the adapter tests fail**

Run: `uv run pytest -q tests/commands/test_adapt.py`
Expected: `ModuleNotFoundError: No module named 'loxmatter.commands.adapt'`.

- [ ] **Step 8: Implement `src/loxmatter/commands/adapt.py`** (GPL header first)

```python
"""What one member of a light group receives for one group command.

Design 2026-09-13, section 3.2. The group's value is decoded once; each
member gets the calls for the parts of it the member can carry - colour for
colour lamps, white temperature for tunable-white lamps, brightness for every
dimmable lamp, and on/off for the rest. A member that can carry none of it
gets no calls, which is not a failure.

Pure: no store, no HTTP, no sources. `commands/fanout.py` calls it per
member, and the device path (`translate.to_device_calls`) is untouched - the
shared parts are `decode_loxone_colour` and the payload helpers, so both
paths build byte-identical payloads for the same case.
"""

from __future__ import annotations

from collections.abc import Sequence

from loxmatter.commands.color import (
    kelvin_to_cie_xy,
    kelvin_to_hue_saturation,
    rgb_to_cie_xy,
    rgb_to_hue_saturation,
)
from loxmatter.commands.translate import (
    LoxoneColour,
    colour_temperature_payload,
    decode_loxone_colour,
    hue_saturation_payload,
    level_from_percent,
    parse_number,
    to_device_calls,
    xy_payload,
)
from loxmatter.model.store import StoredCommand
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    OFF,
    ON,
)
from loxmatter.sources import DeviceCall

__all__ = ["adapt_group_command"]

Pair = tuple[int, int]


def _call(sample: StoredCommand, pair: Pair, payload: dict[str, object]) -> DeviceCall:
    return DeviceCall(
        technology=sample.technology,
        address=sample.address,
        endpoint=sample.endpoint,
        cluster_id=pair[0],
        command_id=pair[1],
        payload=payload,
    )


def _brightness(here: dict[Pair, StoredCommand], sample: StoredCommand, percent: float) -> list[DeviceCall]:
    """Brightness the way this endpoint can take it: (8, 4) switches off at
    0 and on above it, so it is preferred; (8, 0) only when it is all there
    is; `on`/`off` for a light that cannot dim."""
    level = level_from_percent(percent)
    if LEVEL_ONOFF in here:
        return [_call(sample, LEVEL_ONOFF, {"level": level, "transitionTime": 0})]
    if LEVEL in here:
        return [_call(sample, LEVEL, {"level": level, "transitionTime": 0})]
    if ON in here and OFF in here:
        return [_call(sample, ON if level > 0 else OFF, {})]
    return []


def _colour_order(named: Pair) -> tuple[Pair, Pair]:
    """The group command's own colour command first, the other one second."""
    return (named, COLOUR_XY if named == COLOUR_HS else COLOUR_HS)


def _colour_point(here: dict[Pair, StoredCommand], sample: StoredCommand, named: Pair, kelvin: float) -> list[DeviceCall]:
    """A white temperature: the temperature command if carried, else the
    white reproduced as a colour point (design 3.3)."""
    if COLOUR_TEMPERATURE in here:
        return [_call(sample, COLOUR_TEMPERATURE, colour_temperature_payload(kelvin))]
    for pair in _colour_order(named):
        if pair in here and pair == COLOUR_XY:
            return [_call(sample, COLOUR_XY, xy_payload(*kelvin_to_cie_xy(kelvin)))]
        if pair in here and pair == COLOUR_HS:
            return [_call(sample, COLOUR_HS, hue_saturation_payload(*kelvin_to_hue_saturation(kelvin)))]
    return []


def _colour(here: dict[Pair, StoredCommand], sample: StoredCommand, named: Pair, colour: LoxoneColour) -> list[DeviceCall]:
    if colour.kelvin is not None:
        return _colour_point(here, sample, named, colour.kelvin)
    assert colour.rgb is not None
    for pair in _colour_order(named):
        if pair in here and pair == COLOUR_XY:
            return [_call(sample, COLOUR_XY, xy_payload(*rgb_to_cie_xy(*colour.rgb)))]
        if pair in here and pair == COLOUR_HS:
            return [_call(sample, COLOUR_HS, hue_saturation_payload(*rgb_to_hue_saturation(*colour.rgb)))]
    return []


def _endpoint_calls(pair: Pair, here: dict[Pair, StoredCommand], value: str, decoded: LoxoneColour | None) -> list[DeviceCall]:
    sample = next(iter(here.values()))
    if decoded is not None:
        if level_from_percent(decoded.brightness_percent) == 0:
            return _brightness(here, sample, 0)
        return _colour(here, sample, pair, decoded) + _brightness(here, sample, decoded.brightness_percent)
    if pair == COLOUR_TEMPERATURE:
        if COLOUR_TEMPERATURE in here:
            return to_device_calls(here[COLOUR_TEMPERATURE], value)
        return _colour_point(here, sample, COLOUR_XY, parse_number(value))
    if pair in here:
        return to_device_calls(here[pair], value)
    if pair in (LEVEL, LEVEL_ONOFF):
        return _brightness(here, sample, parse_number(value))
    return []


def adapt_group_command(pair: Pair, rows: Sequence[StoredCommand], value: str) -> list[DeviceCall]:
    """The calls one member receives for the light group command `pair`.

    `rows` are the member's stored LIGHT commands on every endpoint
    (`Store.group_targets`). Endpoints are handled in ascending order and
    each gets its own calls, colour before brightness - the order
    `to_device_calls` documents. Raises `UnsupportedValueError` for a value
    that cannot mean anything, before any call is built."""
    decoded = decode_loxone_colour(value) if pair in (COLOUR_HS, COLOUR_XY) else None
    if pair in (LEVEL, LEVEL_ONOFF, COLOUR_TEMPERATURE):
        parse_number(value)
    calls: list[DeviceCall] = []
    for endpoint in sorted({row.endpoint for row in rows}):
        here = {(row.cluster_id, row.command_id): row for row in rows if row.endpoint == endpoint}
        calls.extend(_endpoint_calls(pair, here, value, decoded))
    return calls
```

- [ ] **Step 9: Run the adapter tests and the checks**

Run: `uv run pytest -q tests/commands` and then `uv run ruff check . && uv run ruff format --check . && uv run mypy`

Expected: all pass. Let `ruff format` reflow long lines.

- [ ] **Step 10: Fault-inject**

Inject each of these faults separately. For each one, purge `__pycache__`, run the named test and watch it FAIL, then restore and watch it PASS.

1. In `_endpoint_calls`, replace the brightness-0 early return with the normal path. Named test: `test_brightness_zero_is_a_single_off_and_no_colour`.
2. In `_colour_point`, return `[]` when there is no colour-temperature command. Named test: `test_white_on_a_colour_lamp_without_temperature_is_reproduced_as_a_colour_point`.
3. In `_colour_order`, always return `(COLOUR_XY, COLOUR_HS)`. Named test: `test_colour_on_the_colour_lamp_sends_the_named_colour_command_then_brightness`.
4. In `_brightness`, remove the on/off branch. Named test: `test_colour_on_an_on_off_light_switches_it_on`.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/profiles/light_commands.py src/loxmatter/commands/translate.py src/loxmatter/commands/adapt.py tests/commands/test_adapt.py tests/commands/test_translate.py
git commit -m "feat(commands): adapt a light group command to each member

One Loxone colour value becomes colour for colour lamps, white temperature
for tunable-white lamps and brightness for the rest. The colour output's
decoder is shared with the single-device path, whose calls are pinned
unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The store offers the union, and the fan-out uses the adapter

**Files:**
- Modify: `src/loxmatter/model/store.py` (`register_group_commands`, `group_targets`, their docstrings)
- Modify: `src/loxmatter/commands/fanout.py` (`plan_group_calls`)
- Modify: `src/loxmatter/loxone/server.py:703`, `src/loxmatter/api/control.py:399`
- Test: `tests/model/test_store_groups.py`, `tests/commands/test_fanout.py`, `tests/api/test_group_control.py`

**Interfaces:**
- Consumes: `LIGHT_COMMAND_PAIRS` (Task 2), `adapt_group_command` (Task 2).
- Produces: `plan_group_calls(command: StoredGroupCommand, targets: Sequence[GroupTarget], value: str) -> list[MemberPlan]`. The first parameter is new; every caller passes the resolved group command.

- [ ] **Step 1: Rewrite the two store tests that encode the intersection for lights**

In `tests/model/test_store_groups.py`, replace `test_the_group_offers_only_what_every_member_accepts` and `test_a_command_that_leaves_the_intersection_stops_resolving` with:

```python
def test_a_light_group_offers_every_light_command_any_member_has(store, lamps_with_commands):
    """Design 2026-09-13, 3.1: the CWS lamp carries colour, the WS lamp does
    not - the group of both still offers colour, and the WS lamp takes the
    brightness out of it (commands/adapt.py).

    Fault to prove it: take the intersection for light pairs again - `color`
    disappears from `both`."""
    colour_only = store.create_group("Colour", [lamps_with_commands[0]])
    both = store.create_group("Both", lamps_with_commands)
    assert "color" in _slugs(store, colour_only.id)
    assert {"color", "color_xy", "colortemp", "level_onoff", "on", "off", "toggle"} <= set(
        _slugs(store, both.id)
    )


def test_adding_a_member_without_colour_keeps_the_colour_key(store, lamps_with_commands):
    group = store.create_group("Colour", [lamps_with_commands[0]])
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "color")
    rowid = _rowid_for(store, key)
    store.set_group_members(group.id, lamps_with_commands)
    assert store.resolve_group_command(key).slug == "color"
    assert _rowid_for(store, key) == rowid


def test_a_light_command_leaves_only_when_no_member_has_it(store, lamps_with_commands):
    group = store.create_group("Both", lamps_with_commands)
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "color")
    store.set_group_members(group.id, [lamps_with_commands[1]])
    with pytest.raises(UnknownCommandError):
        store.resolve_group_command(key)
```

Then check every other test in that file for intersection assumptions:
- `test_forgetting_a_member_recomputes_the_intersection`
- `test_forgetting_a_member_that_shrinks_the_intersection_advances_updated_at`
- `test_a_startup_backfill_that_reaches_every_member_extends_the_group`
- `test_targets_carry_one_entry_per_member_with_that_member_s_own_rows`
- `test_a_member_carrying_the_pair_on_two_endpoints_gets_both`

Run each against the new behaviour. Rewrite a test only where its **light** expectation encodes the intersection. Keep its intent by switching it to a non-light pair, or by removing the last member carrying a light pair. Name every changed test in the report, with the reason.

- [ ] **Step 2: Add the non-light intersection test**

```python
def test_a_non_light_command_is_still_offered_only_when_every_member_has_it(store, lamps_with_commands):
    """Identify (3, 0) or any pair outside `LIGHT_COMMAND_PAIRS` keeps the
    10 September intersection.

    Fault to prove it: take the union for every pair - this fails."""
    cws, ws = lamps_with_commands
    extra = next(c for c in store.commands(cws) if (c.cluster_id, c.command_id) not in LIGHT_COMMAND_PAIRS)
    group = store.create_group("Both", lamps_with_commands)
    assert extra.slug in _slugs(store, store.create_group("One", [cws]).id)
    has_it_too = any(
        (c.cluster_id, c.command_id) == (extra.cluster_id, extra.command_id) for c in store.commands(ws)
    )
    assert (extra.slug in _slugs(store, group.id)) is has_it_too
```

Add `from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS` to the imports.

If the CWS fixture carries no non-light command, the `next(...)` raises `StopIteration`. In that case build the case explicitly: register an extra command row on the CWS device with `store.register_commands` and a `DeviceCommand` for Identify. Look at how `extract_commands` builds `DeviceCommand` and reuse that constructor. Do not skip the test.

- [ ] **Step 3: Targets test**

```python
def test_light_targets_carry_every_light_row_of_every_member(store, lamps_with_commands):
    group = store.create_group("Both", lamps_with_commands)
    colour = next(c for c in store.group_commands(group.id) if c.slug == "color")
    targets = store.group_targets(colour)
    assert [t.device_id for t in targets] == list(lamps_with_commands)
    ws_pairs = {(c.cluster_id, c.command_id) for c in targets[1].commands}
    assert (8, 4) in ws_pairs and (768, 6) not in ws_pairs
    assert all((c.cluster_id, c.command_id) in LIGHT_COMMAND_PAIRS for t in targets for c in t.commands)
```

- [ ] **Step 4: Run to verify the new store tests fail**

Run: `uv run pytest -q tests/model/test_store_groups.py`
Expected: the new tests FAIL; the unaffected ones pass.

- [ ] **Step 5: Implement in `src/loxmatter/model/store.py`**

Add `from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS` to the imports.

In `register_group_commands`, replace the block that computes `shared` with:

```python
        shared: dict[tuple[int, int], StoredCommand] = {}
        if by_member:
            common = set(by_member[0])
            for other in by_member[1:]:
                common &= set(other)
            shared = {pair: by_member[0][pair] for pair in common}
            # Light commands: offered when ANY member carries them (design
            # 2026-09-13, 3.1); `commands/adapt.py` gives each member the part
            # it can carry. Every other pair keeps the intersection above.
            for member in by_member:
                for pair, sample in member.items():
                    if pair in LIGHT_COMMAND_PAIRS and pair not in shared:
                        shared[pair] = sample
```

Update the docstring. Wherever it says a command "drops out of the intersection", say that a light command drops out only when no member carries it and any other command when not every member does. Cite design 2026-09-13, 3.1.

In `group_targets`, change the row filter and the docstring:

```python
        light = (command.cluster_id, command.command_id) in LIGHT_COMMAND_PAIRS
        targets: list[GroupTarget] = []
        for device in self.group_members(command.group_id):
            rows = tuple(
                stored
                for stored in self.commands(device.id)
                if (
                    (stored.cluster_id, stored.command_id) in LIGHT_COMMAND_PAIRS
                    if light
                    else stored.cluster_id == command.cluster_id
                    and stored.command_id == command.command_id
                )
            )
            if not rows:
                continue
            targets.append(
                GroupTarget(device_id=device.id, device_label=device.label, commands=rows)
            )
```

The docstring now states:
- For a light command, a member's rows are all of its light rows on every endpoint, and the adapter picks from them.
- For any other command, they are the rows of that exact pair.
- A member with no such rows is skipped.

Keep everything after the loop unchanged.

- [ ] **Step 6: Change `plan_group_calls` in `src/loxmatter/commands/fanout.py`**

```python
def plan_group_calls(
    command: StoredGroupCommand, targets: Sequence[GroupTarget], value: str
) -> list[MemberPlan]:
    """Translates the group's value once per member.

    A light command (design 2026-09-13) goes through
    `commands.adapt.adapt_group_command`, which gives each member the part of
    the value it can carry - possibly nothing, which is not a failure: such a
    member gets an empty plan and `dispatch_group` sends it nothing. Any
    other command is translated per stored row, as before.

    Raises `UnsupportedValueError` before anything is sent: the value is
    decoded for the first member, and an invalid value fails there.
    """
    pair = (command.cluster_id, command.command_id)
    plans: list[MemberPlan] = []
    for target in targets:
        calls: list[DeviceCall] = []
        if pair in LIGHT_COMMAND_PAIRS:
            calls.extend(adapt_group_command(pair, target.commands, value))
        else:
            for stored in target.commands:
                calls.extend(to_device_calls(stored, value))
        plans.append(
            MemberPlan(
                device_id=target.device_id,
                device_label=target.device_label,
                calls=tuple(calls),
            )
        )
    return plans
```

Add the imports `from loxmatter.commands.adapt import adapt_group_command`, `from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS`, and `StoredGroupCommand` from `loxmatter.model.store`.

Confirm that `dispatch_group` handles an empty `calls` tuple without error and without counting the member as failed. `_run_member` loops over zero calls; if that ever raised, it would be a bug to fix here.

At both call sites, `src/loxmatter/loxone/server.py` (`plan_group_calls(targets, value)`) and `src/loxmatter/api/control.py` (`plan_group_calls(targets, body.value)`), pass `group_command` as the new first argument.

- [ ] **Step 7: Update `tests/commands/test_fanout.py`**

Every existing call becomes `plan_group_calls(<group command>, targets, value)`.

Add a helper that builds a `StoredGroupCommand` for the pair the test's targets use:

```python
def group_command(cluster_id: int, command_id: int, slug: str, takes_value: bool) -> StoredGroupCommand:
    return StoredGroupCommand(
        key=f"g1_{slug}", slug=slug, group_id=1,
        cluster_id=cluster_id, command_id=command_id, takes_value=takes_value,
    )
```

Pass `group_command(6, 1, "on", False)` where `on_target` is used, and `group_command(768, 6, "color", True)` where `colour_target` is used.

Then add:

```python
async def test_a_member_that_gets_nothing_is_not_a_failure():
    ww_like = GroupTarget(
        device_id=2, device_label="WW",
        commands=(command(2, 14, 1, 6, 1, "on", False), command(2, 14, 1, 6, 0, "off", False)),
    )
    plans = plan_group_calls(group_command(768, 10, "colortemp", True), [ww_like], "2700")
    assert plans[0].calls == ()
    outcome = await dispatch_group(plans, lambda call: asyncio.sleep(0))
    assert outcome.failed == []
```

Use the exact `invoke` signature the file's other `dispatch_group` tests use. If they pass an async function, define one.

- [ ] **Step 8: The integration test** (append to `tests/api/test_group_control.py`)

The fixture's group is the CWS lamp plus the WS lamp, so after this change it offers `color`.

```python
async def test_one_colour_value_gives_colour_to_the_colour_lamp_and_brightness_to_the_white_one(
    api, invocations
):
    """Design 2026-09-13, 3.2, end to end through `/cmd`: blue at 60 % turns
    the CWS lamp blue at 60 % and sets only the brightness of the WS lamp.

    Fault to prove it: route light commands through `to_device_calls` again
    in `plan_group_calls` - the WS lamp then receives nothing, because its
    `color` row does not exist."""
    client, store, group_id = api
    cws, ws = store.group_members(group_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")
    response = await client.get(f"/cmd/{key}/60000000")
    assert response.status_code == 200
    by_address: dict[str, list[tuple[int, int]]] = {}
    for call in invocations:
        by_address.setdefault(call.address, []).append((call.cluster_id, call.command_id))
    assert by_address[cws.address] == [(768, 6), (8, 4)]
    assert by_address[ws.address] == [(8, 4)]


async def test_a_lumitech_white_gives_both_lamps_their_white_temperature(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")
    response = await client.get(f"/cmd/{key}/200302700")
    assert response.status_code == 200
    per_address: dict[str, list[tuple[int, int]]] = {}
    for call in invocations:
        per_address.setdefault(call.address, []).append((call.cluster_id, call.command_id))
    assert all(calls == [(768, 10), (8, 4)] for calls in per_address.values())
    assert len(per_address) == 2
```

Check that both fixture lamps really are in `group_members` order `(cws, ws)`. If the order differs, look the members up by label.

- [ ] **Step 9: Run**

Run these, each in the foreground: `uv run pytest -q tests/model tests/commands`, then part A1 `uv run pytest -q tests/api`. Also run `uv run pytest -q tests/export tests/projectsync`, because group exports and project sync read `group_commands`. Where a test there encodes the intersection for a light group, rewrite it the same way as Step 1 and name it in the report.

Expected: all pass.

- [ ] **Step 10: Fault-inject**

Inject each fault separately, purge `__pycache__`, see the named test FAIL, then restore.

1. Remove the light-union loop in `register_group_commands`. Named test: `test_a_light_group_offers_every_light_command_any_member_has`.
2. Apply the union to every pair. Named test: `test_a_non_light_command_is_still_offered_only_when_every_member_has_it`.
3. In `group_targets`, use the exact-pair filter for light commands too. Named test: `test_light_targets_carry_every_light_row_of_every_member`.
4. In `plan_group_calls`, always use `to_device_calls`. Named test: the integration test.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/commands/fanout.py src/loxmatter/loxone/server.py src/loxmatter/api/control.py tests
git commit -m "feat(groups): offer every light command any member has

A dim-only lamp added to a colour group no longer removes the group's colour
output: light commands are offered when one member carries them, and each
member receives the part of the value it can carry. Other commands keep the
intersection.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Copy, change notes, full verification

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml`, `src/loxmatter/web/index.html`
- Modify: `CHANGELOG.md`, `docs/superpowers/specs/2026-09-10-device-groups-design.md`
- Test: `tests/api/test_web.py`

- [ ] **Step 1: Add the hint string** to `strings.yaml`, after `web.groups.needs_member`:

```yaml
# Under the members heading while the group's category is light (design
# 2026-09-13, section 5): a light group no longer offers only what every
# member understands, and a user mixing a colour lamp with a white one
# should not have to find that out from Loxone.
web.groups.light_members_hint:
  en: "Each lamp takes over what it supports: colour lamps the colour, the others brightness and on/off."
  de: "Jede Lampe übernimmt, was sie kann: Farblampen die Farbe, die anderen Helligkeit und An/Aus."
```

- [ ] **Step 2: Reword `web.devices.remove_confirm_groups_note`.**

  Why the wording changes: removing a member can only *add* a non-light
  command to an intersection, never remove one. So a removal can only take
  away a **light** command, and only when this device was the last member
  carrying it.

  Replace the two values with the text below, and update the comment above
  the key: the "intersection" sentence becomes "the last member carrying a
  light command, design 2026-09-13, 3.1".

```yaml
  en: |
    It also belongs to: {groups}. If it was the last member there with a light function such as colour or white temperature, that output of the group answers 404 in Loxone from then on.
  de: |
    Es gehört außerdem zu: {groups}. War es dort das letzte Mitglied mit einer Lichtfunktion wie Farbe oder Weißton, antwortet der zugehörige virtuelle Ausgang der Gruppe in Loxone von da an mit 404.
```

  The test fixtures in `tests/api/test_web.py` that stub this key with
  `"it also belongs to: {groups}"` stay as they are.

- [ ] **Step 3: Show the hint** in `src/loxmatter/web/index.html`, inside `.group-members-status` next to the existing `needs_member` hint:

```html
            <span
              class="hint"
              x-show="groupDraftCategory() === 'light'"
              x-text="t('web.groups.light_members_hint')"
            ></span>
```

Before choosing the literal, check what `groupDraftCategory()` returns for a light group in `app.js`. If the category string is not `"light"`, use the real value.

- [ ] **Step 4: Test the hint binding** (append to `tests/api/test_web.py`, following the file's served-markup evaluation pattern with `_app_state`)

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_light_members_hint_shows_only_for_a_light_group(api):
    """Fault to prove it: `x-show="true"` - the plug case fails."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    at = markup.index("t('web.groups.light_members_hint')")
    tag = markup[markup.rindex("<span", 0, at) : markup.index(">", at)]
    match = re.search(r'x-show="([^"]*)"', tag)
    assert match, tag
    evaluator = f"with (state) {{ return Boolean({match.group(1)}); }}"
    values = _app_state(
        setup="const shown = new Function('state', " + json.dumps(evaluator) + ");\n"
        "const out = {};\n"
        "for (const category of ['light', 'plug', null]) {\n"
        "  state.groupDraftCategory = () => category;\n"
        "  out[String(category)] = shown(state);\n"
        "}\n"
        "console.log(JSON.stringify(out));"
    )
    assert values == {"light": True, "plug": False, "null": False}
```

Adjust the category literals to the real values `groupDraftCategory()` produces, per Step 3.

- [ ] **Step 5: CHANGELOG.** In `## [Unreleased]`, replace the Groups bullet that begins "A group takes only one kind of device, and offers only what *all* its members understand" with:

```markdown
- A group takes only one kind of device. A group of lights offers everything
  any of its lamps can do, and each lamp takes over what it supports: set
  blue at 60 % and the colour lamps turn blue while a warm-white lamp simply
  dims to 60 %; set a warm white and a colour lamp without its own white
  setting shows the nearest colour it can. If one member does not answer,
  the others are still switched and the log names the one that stayed
  dark — "five of six" is the useful answer when something is wrong.
```

- [ ] **Step 6: Groups design pointer.** Directly under the title of `docs/superpowers/specs/2026-09-10-device-groups-design.md`, insert:

```markdown
> **Amended 13 September 2026:** for light commands, invariant 2 and
> Section 4.3 ("the command list is the intersection") are replaced by
> `2026-09-13-group-capability-fanout-design.md`, Section 3. The text below
> is the original design and is not rewritten.
```

- [ ] **Step 7: Full verification.** Run the four suite parts (A1, A2, B, C) and the four checks from Global Constraints, each in the foreground. Expected: everything green. Report the counts.

- [ ] **Step 8: Fault-inject the hint test** (Step 4's named fault), then restore it.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/index.html tests/api/test_web.py CHANGELOG.md docs/superpowers/specs/2026-09-10-device-groups-design.md
git commit -m "docs(groups): say that every lamp in a group takes over what it can

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
