# One Output for the Loxone Lighting Controller - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every light endpoint and every light group gets one output, `lumitech`, that takes whatever a Loxone lighting controller's colour actuator emits (RGB or Lumitech) and gives the light what it can carry; the single light commands move into an expert area and are no longer exported by default.

**Architecture:** The output is a stored command row with the reserved pseudo pair `(-1, 0)`, appended by `extract_commands` for light endpoints, so keys, storage, group union, export and project sync work unchanged. Dispatch goes through the existing group adapter (`commands/adapt.py`) over the endpoint's own light rows. A nullable `exported` column on `command` and `group_command` records only explicit user choices; `NULL` follows a default rule the store resolves on read.

**Tech Stack:** Python 3.13, FastAPI, SQLite (`sqlite3`), pytest, Alpine.js (vendored, no build step), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-lumitech-output-design.md`

## Global Constraints

- Everything in the repository is English - code, comments, docstrings, test names, commit messages (`CLAUDE.md`). German only in `de:` values of `src/loxmatter/i18n/strings.yaml`.
- Every string a user can see goes through `strings.yaml` with an `en` and a `de` value, resolved with `i18n.t(...)`. The one exception is the output title "Lumitech / RGB", identical in both languages (spec decision 5).
- Commit messages: Conventional Commits, English, say what changed and why; end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Store migrations are additive only: never drop or rewrite a column (the updater rolls back without restoring the database).
- Pseudo pair: `LUMITECH = (-1, 0)`, slug `lumitech`, keys `d{id}_{endpoint}_lumitech` and `g{id}_lumitech`, export title `Lumitech / RGB`.
- Schema version goes from 12 to 13.
- Runtime (`/cmd/{key}/{value}`, `POST /api/commands/{key}`) never looks at `exported`.
- **Tests:** never start a test run in the background, never use Monitor, never end a turn waiting for a notification - every command in the foreground, report only when everything is done. The full suite does not fit one shell time limit; run it in these parts, each in the foreground:
  1a. `uv run pytest -q tests/api`
  1b. `uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee`
  2. `uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py`
  3. `uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_image.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py`
  (`tests/api` and `tests/projectsync` must not share one pytest call - their `conftest.py` files collide.)
- Before each commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- Every protective test gets a fault-injection check: break the code it protects, watch the test fail, restore. Say in the report which fault was injected.
- Work in a worktree with absolute paths; the main checkout is shared with other sessions.

## File Map

| File | Change |
|---|---|
| `src/loxmatter/profiles/light_commands.py` | `LUMITECH`, `LUMITECH_SLUG`, `is_expert_light_command` |
| `src/loxmatter/profiles/categories.py` | `is_light_endpoint` |
| `src/loxmatter/export/commands.py` | append the `lumitech` row per light endpoint |
| `src/loxmatter/commands/translate.py` | `parse_kelvin` rejects a Lumitech value |
| `src/loxmatter/commands/adapt.py` | `LUMITECH` decoding, XY-first rule, `adapt_device_command` |
| `src/loxmatter/loxone/server.py`, `src/loxmatter/api/control.py` | device routes call `adapt_device_command`; device controls skip `LUMITECH` |
| `src/loxmatter/api/groups.py` | group controls skip `LUMITECH`; `GET /api/groups/{id}/outputs` |
| `src/loxmatter/model/store.py` | schema v13, `exported` column, resolved `exported`/`functional`, setters |
| `src/loxmatter/export/outputs.py` | filter on `exported`, `output_title` |
| `src/loxmatter/api/export.py` | counts exported commands, withheld count includes commands |
| `src/loxmatter/api/outputs.py` (new) | `GET /api/devices/{id}/outputs`, `PATCH /api/commands/{key}` |
| `src/loxmatter/api/models.py` | `OutputOut`, `OutputPatch`, `ExportDeviceOut`/`ExportGroupOut` field |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | "Outputs" part in the signal dialog and the group dialog |
| `src/loxmatter/i18n/strings.yaml` | new strings |
| `CHANGELOG.md`, `docs/superpowers/specs/2026-09-13-group-capability-fanout-design.md` | docs |

---

### Task 1: The pseudo pair and the `lumitech` row

**Files:**
- Modify: `src/loxmatter/profiles/light_commands.py`
- Modify: `src/loxmatter/profiles/categories.py`
- Modify: `src/loxmatter/export/commands.py`
- Test: `tests/export/test_commands.py`, `tests/profiles/test_light_commands.py` (new)

**Interfaces:**
- Produces: `LUMITECH: Final = (-1, 0)`, `LUMITECH_SLUG: Final = "lumitech"`, `LIGHT_COMMAND_PAIRS` now includes `LUMITECH`; `is_expert_light_command(pair: tuple[int, int], endpoint_has_lumitech: bool) -> bool`; `categories.is_light_endpoint(device_types: frozenset[int]) -> bool`; `extract_commands` returns one extra `DeviceCommand(endpoint, -1, 0, "lumitech", True)` per light endpoint.

- [ ] **Step 1: Write the failing tests**

Append to `tests/export/test_commands.py`:

```python
def _lumitech_rows(name: str) -> list[tuple[int, str]]:
    return [
        (c.endpoint, c.slug)
        for c in extract_commands(load(name))
        if (c.cluster_id, c.command_id) == (-1, 0)
    ]


@pytest.mark.parametrize("name", ["ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"])
def test_a_light_endpoint_gets_one_lumitech_output(name):
    """Design 2026-09-24, 3.1: one output per light endpoint.

    Fault to prove it: drop the append in `extract_commands` - this fails."""
    assert _lumitech_rows(name) == [(1, "lumitech")]
    lumitech = next(c for c in extract_commands(load(name)) if c.slug == "lumitech")
    assert lumitech.takes_value is True


def test_a_plug_gets_no_lumitech_output():
    """A plug carries on/off like a light but is no light (0x010A).

    Fault to prove it: append the row for every endpoint with OnOff - this fails."""
    assert _lumitech_rows("ikea_grillplats_plug.json") == []


def test_a_device_without_light_commands_gets_no_lumitech_output():
    assert _lumitech_rows("ikea_bilresa_button.json") == []


def test_the_lumitech_row_is_also_there_in_raw_mode():
    assert [
        c.slug for c in extract_commands(load("ikea_kajplats_ws_lamp.json"), raw=True)
        if c.slug == "lumitech"
    ] == ["lumitech"]
```

Check that `pytest` is imported at the top of `tests/export/test_commands.py`; add `import pytest` if it is not.

Create `tests/profiles/test_light_commands.py`:

```python
# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The default export rule for light commands (design 2026-09-24, 4.2)."""

from __future__ import annotations

import pytest

from loxmatter.profiles.categories import is_light_endpoint
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    LIGHT_COMMAND_PAIRS,
    LUMITECH,
    OFF,
    ON,
    TOGGLE,
    is_expert_light_command,
)

_SINGLE = [OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE]


def test_lumitech_is_a_light_pair_no_real_command_can_have():
    assert LUMITECH in LIGHT_COMMAND_PAIRS
    assert LUMITECH[0] < 0


@pytest.mark.parametrize("pair", _SINGLE)
def test_a_single_light_command_beside_lumitech_is_expert(pair):
    """Fault to prove it: return False unconditionally - this fails."""
    assert is_expert_light_command(pair, endpoint_has_lumitech=True)


@pytest.mark.parametrize("pair", _SINGLE)
def test_a_single_light_command_without_lumitech_stays_functional(pair):
    """An endpoint without the output (a plug) keeps every command it has."""
    assert not is_expert_light_command(pair, endpoint_has_lumitech=False)


def test_lumitech_itself_is_never_expert():
    assert not is_expert_light_command(LUMITECH, endpoint_has_lumitech=True)


def test_a_non_light_pair_is_never_expert():
    assert not is_expert_light_command((258, 0), endpoint_has_lumitech=True)


@pytest.mark.parametrize("device_type", [0x0100, 0x0101, 0x010C, 0x010D, 0x010F, 0x0110])
def test_light_device_types_make_a_light_endpoint(device_type):
    assert is_light_endpoint(frozenset({device_type}))


@pytest.mark.parametrize("device_type", [0x010A, 0x010B, 0x000F, 0x0510])
def test_other_device_types_do_not(device_type):
    assert not is_light_endpoint(frozenset({device_type}))
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest -q tests/export/test_commands.py tests/profiles/test_light_commands.py`
Expected: FAIL - `ImportError: cannot import name 'LUMITECH'` / `'is_light_endpoint'`, and the new `test_commands.py` tests fail on an empty list.

- [ ] **Step 3: Implement**

In `src/loxmatter/profiles/light_commands.py`, after `COLOUR_TEMPERATURE`, and replacing `LIGHT_COMMAND_PAIRS`:

```python
# The lighting controller output (design 2026-09-24): not a Matter command
# but one Loxone value - an RGB colour or a Lumitech white, each with its
# brightness - that `commands/adapt.py` turns into whatever the light
# carries. A negative cluster id exists in neither Matter nor Zigbee, so the
# pair can never coincide with a real command.
LUMITECH: Final = (-1, 0)
LUMITECH_SLUG: Final = "lumitech"

LIGHT_COMMAND_PAIRS: Final = frozenset(
    {OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE, LUMITECH}
)


def is_expert_light_command(pair: tuple[int, int], endpoint_has_lumitech: bool) -> bool:
    """Whether a command belongs in the expert area rather than the export
    by default (design 2026-09-24, 4.2): a single light command beside a
    `lumitech` output, which already carries all of it. Whether the endpoint
    is a light is read from the rows themselves - it has a `lumitech` row -
    not from a second source."""
    return endpoint_has_lumitech and pair in LIGHT_COMMAND_PAIRS and pair != LUMITECH
```

In `src/loxmatter/profiles/categories.py`, after `category_for`:

```python
def is_light_endpoint(device_types: frozenset[int]) -> bool:
    """Whether one endpoint's device types make it a light - the same table
    `category_for` reads, not a second list (design 2026-09-24, 3.1). A
    dimmable plug (0x010B) is a socket and stays out."""
    return any(CATEGORY_BY_DEVICE_TYPE.get(t) is Category.LIGHT for t in device_types)
```

In `src/loxmatter/export/commands.py`, add imports:

```python
from loxmatter.profiles.categories import is_light_endpoint
from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS, LUMITECH, LUMITECH_SLUG
from loxmatter.profiles.relevance import device_types_by_endpoint
```

and replace `return sorted(commands)` at the end of `extract_commands` with:

```python
    # One lighting controller output per light endpoint (design 2026-09-24,
    # 4.1): appended with its slug, never derived from the
    # AcceptedCommandList, so raw mode cannot turn it into `c-1_cmd0`. Only
    # where the endpoint carries a light command at all - an output that
    # could send nothing would be a promise the light cannot keep.
    device_types = device_types_by_endpoint(snapshot)
    light_endpoints = {
        command.endpoint
        for command in commands
        if (command.cluster_id, command.command_id) in LIGHT_COMMAND_PAIRS
        and is_light_endpoint(device_types.get(command.endpoint, frozenset()))
    }
    for endpoint in light_endpoints:
        commands.append(
            DeviceCommand(
                endpoint=endpoint,
                cluster_id=LUMITECH[0],
                command_id=LUMITECH[1],
                slug=LUMITECH_SLUG,
                takes_value=True,
            )
        )

    return sorted(commands)
```

Extend the module docstring of `export/commands.py` by one paragraph saying the one row that does not come from the AcceptedCommandList is `lumitech`, and why.

If importing `profiles.categories` from `export.commands` produces an import cycle, run `uv run python -c "import loxmatter.export.commands"` to see it and report it rather than working around it with a local import.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/export/test_commands.py tests/profiles/test_light_commands.py`
Expected: PASS.

- [ ] **Step 5: Run the neighbouring suites and sort every failure**

Run: `uv run pytest -q tests/export tests/model tests/commands tests/profiles`
Expected: some existing tests that count a lamp's commands now see one more. For each failure, decide: the count or list is correct with the new row → update the expectation and say so in the test's docstring or an adjacent comment ("+1: the lumitech output, design 2026-09-24"); anything else → it is a bug, fix the code. List every updated test in the report.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/profiles/light_commands.py src/loxmatter/profiles/categories.py src/loxmatter/export/commands.py tests/
git commit -m "feat(export): give every light endpoint a lumitech output

A Loxone lighting controller emits RGB or Lumitech on one output, but a
light's outputs were named after single Matter commands, and a tunable-white
lamp had none that understands Lumitech. The row uses the reserved pair
(-1, 0), so keys, storage and the group union work unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `colortemp` rejects a Lumitech value

**Files:**
- Modify: `src/loxmatter/commands/translate.py` (`parse_kelvin`, around line 138)
- Modify: `src/loxmatter/i18n/strings.yaml` (after `api.errors.kelvin_not_positive`)
- Test: `tests/commands/test_translate_error_messages.py`, `tests/commands/test_adapt.py`

**Interfaces:**
- Produces: `parse_kelvin(value)` raises `UnsupportedValueError` with key `api.errors.lumitech_on_colortemp` for an integer value in the Lumitech range. Both the device path (`_payload_color_temperature`) and the group path (`adapt_group_command`) already call `parse_kelvin`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/commands/test_translate_error_messages.py`:

```python
def test_a_lumitech_value_on_colortemp_is_rejected_not_sent_as_zero_mired():
    """Design 2026-09-24, 3.3: before, 201002700 became 0 mired.

    Fault to prove it: remove the `is_lumitech` check - no error is raised."""
    with pytest.raises(UnsupportedValueError, match="Lumitech value '201002700'"):
        parse_kelvin("201002700")


def test_the_lumitech_on_colortemp_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Lumitech-Wert '201002700'"):
        parse_kelvin("201002700")


def test_a_plain_kelvin_value_still_passes():
    assert parse_kelvin("2700") == 2700
```

Append to `tests/commands/test_adapt.py`:

```python
def test_a_lumitech_value_on_the_group_colortemp_raises_for_every_member():
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(COLOUR_TEMPERATURE, WW, WHITE_2700_30)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/commands/test_translate_error_messages.py tests/commands/test_adapt.py`
Expected: the three rejection tests FAIL with "DID NOT RAISE".

- [ ] **Step 3: Implement**

In `strings.yaml`, after the `api.errors.kelvin_not_positive` entry:

```yaml
# `colortemp` takes plain Kelvin; the Lumitech number belongs on the
# `lumitech` output (design 2026-09-24, 3.3).
api.errors.lumitech_on_colortemp:
  en: "Lumitech value {value!r} does not belong on the colour temperature output: it takes plain Kelvin. Use the lumitech output."
  de: "Lumitech-Wert {value!r} gehört nicht auf den Farbtemperatur-Ausgang: der nimmt reine Kelvin. Den lumitech-Ausgang verwenden."
```

In `translate.py`, `parse_kelvin`, after `kelvin = _as_number(value)`:

```python
    # A Lumitech number is not a Kelvin value: `kelvin_to_mireds` turned
    # 201002700 into 0 mired and sent it (design 2026-09-24, 3.3).
    if kelvin == int(kelvin) and is_lumitech(int(kelvin)):
        raise UnsupportedValueError(i18n.t("api.errors.lumitech_on_colortemp", value=value))
```

Extend `parse_kelvin`'s docstring by one sentence about the Lumitech check.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/commands tests/test_i18n.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/commands/translate.py src/loxmatter/i18n/strings.yaml tests/commands/
git commit -m "fix(commands): colortemp rejects a Lumitech value instead of sending 0 mired

parse_kelvin read 201002700 as Kelvin and kelvin_to_mireds truncated it to
0 mired, a colour temperature no lamp has. It now answers 400 and names the
output that takes the value.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The adapter serves `lumitech`, for a group member and a single light

**Files:**
- Modify: `src/loxmatter/commands/adapt.py`
- Test: `tests/commands/test_adapt.py`

**Interfaces:**
- Consumes: `LUMITECH` (Task 1).
- Produces: `adapt_group_command(LUMITECH, rows, value)` decodes like the colour pairs and prefers XY; `adapt_device_command(command: StoredCommand, device_rows: Sequence[StoredCommand], value: str) -> list[DeviceCall]`, exported in `__all__`.

- [ ] **Step 1: Write the failing tests**

In `tests/commands/test_adapt.py`, import `LUMITECH` and `adapt_device_command`, add `LUMITECH: "lumitech"` to `_SLUGS`, and append:

```python
def _with_lumitech(member):
    return [*member, *rows(member[0].address, LUMITECH)]


# Design 2026-09-24, 3.1: one test per row of the table.
def test_lumitech_rgb_on_the_colour_lamp_sends_xy_then_brightness():
    """XY before hue/saturation when both are carried.

    Fault to prove it: leave `_colour`'s XY rule at `named == COLOUR_XY` -
    this gets (768, 6)."""
    got = shape(adapt_group_command(LUMITECH, _with_lumitech(CWS), BLUE_60))
    x, y = rgb_to_cie_xy(0, 0, 153)
    assert got == [
        (768, 7, {"colorX": x, "colorY": y, "transitionTime": 0, **EIF}),
        (8, 4, {"level": 152, "transitionTime": 0}),
    ]


def test_lumitech_white_on_the_colour_lamp_sends_the_temperature_then_brightness():
    assert shape(adapt_group_command(LUMITECH, _with_lumitech(CWS), WHITE_2700_30)) == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF}),
        (8, 4, {"level": 76, "transitionTime": 0}),
    ]


def test_lumitech_white_on_a_colour_lamp_without_temperature_is_a_colour_point():
    x, y = kelvin_to_cie_xy(2700)
    assert shape(adapt_group_command(LUMITECH, _with_lumitech(XY_ONLY), WHITE_2700_30))[0] == (
        768,
        7,
        {"colorX": x, "colorY": y, "transitionTime": 0, **EIF},
    )


def test_lumitech_on_the_tunable_white_lamp():
    member = _with_lumitech(WS)
    assert shape(adapt_group_command(LUMITECH, member, BLUE_60)) == [
        (8, 4, {"level": 152, "transitionTime": 0})
    ]
    assert shape(adapt_group_command(LUMITECH, member, WHITE_2700_30)) == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF}),
        (8, 4, {"level": 76, "transitionTime": 0}),
    ]


@pytest.mark.parametrize("value", [BLUE_60, WHITE_2700_30])
def test_lumitech_on_the_dim_only_lamp_is_brightness_only(value):
    level = 152 if value == BLUE_60 else 76
    assert shape(adapt_group_command(LUMITECH, _with_lumitech(WW), value)) == [
        (8, 4, {"level": level, "transitionTime": 0})
    ]


def test_lumitech_on_an_on_off_light_switches_it():
    member = _with_lumitech(ONOFF)
    assert shape(adapt_group_command(LUMITECH, member, BLUE_60)) == [(6, 1, {})]
    assert shape(adapt_group_command(LUMITECH, member, "0")) == [(6, 0, {})]


def test_lumitech_brightness_zero_is_a_single_off():
    assert shape(adapt_group_command(LUMITECH, _with_lumitech(CWS), "200002700")) == [
        (8, 4, {"level": 0, "transitionTime": 0})
    ]


def test_lumitech_rejects_a_value_that_means_nothing():
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(LUMITECH, _with_lumitech(CWS), "101")


def test_a_single_light_gets_the_same_calls_as_a_group_member():
    """Design 2026-09-24, 3.2: byte-identical.

    Fault to prove it: have `adapt_device_command` pass `COLOUR_HS` instead
    of `LUMITECH` - the colour call differs."""
    member = _with_lumitech(CWS)
    lumitech = next(r for r in member if (r.cluster_id, r.command_id) == LUMITECH)
    for value in (BLUE_60, WHITE_2700_30, "0"):
        assert adapt_device_command(lumitech, member, value) == adapt_group_command(
            LUMITECH, member, value
        )


def test_a_single_light_uses_only_its_own_endpoint():
    """A device with lights on two endpoints: `d9_2_lumitech` drives
    endpoint 2 only.

    Fault to prove it: drop the endpoint filter - calls for endpoint 1 appear."""
    device = [
        *rows("40", OFF, ON, LEVEL_ONOFF, LUMITECH, endpoint=1),
        *rows("40", OFF, ON, LEVEL_ONOFF, LUMITECH, endpoint=2),
    ]
    second = next(r for r in device if r.endpoint == 2 and r.slug == "lumitech")
    calls = adapt_device_command(second, device, WHITE_2700_30)
    assert {c.endpoint for c in calls} == {2}


def test_any_other_command_goes_through_to_device_calls_unchanged():
    level = next(r for r in CWS if r.slug == "level_onoff")
    assert shape(adapt_device_command(level, CWS, "50")) == [
        (8, 4, {"level": 127, "transitionTime": 0})
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/commands/test_adapt.py`
Expected: FAIL - `ImportError: cannot import name 'adapt_device_command'`.

- [ ] **Step 3: Implement**

In `src/loxmatter/commands/adapt.py`:

1. Import `LIGHT_COMMAND_PAIRS` and `LUMITECH` from `loxmatter.profiles.light_commands`.
2. `__all__ = ["adapt_device_command", "adapt_group_command"]`.
3. In `_colour`, replace the XY line:

```python
    # XY when the command names it, and for `lumitech`, which names neither
    # colour command: XY is mandatory for a Matter Extended Color Light and
    # the only colour ZHA sends (design 2026-09-24, 3.1).
    if COLOUR_XY in here and (named in (COLOUR_XY, LUMITECH) or COLOUR_HS not in here):
```

4. In `adapt_group_command`, the decode line:

```python
    decoded = (
        decode_loxone_colour(value) if pair in (COLOUR_HS, COLOUR_XY, LUMITECH) else None
    )
```

5. Append:

```python
def adapt_device_command(
    command: StoredCommand, device_rows: Sequence[StoredCommand], value: str
) -> list[DeviceCall]:
    """The calls one stored device command produces (design 2026-09-24, 4.1).

    `lumitech` is no Matter command: it goes through `adapt_group_command`
    over the light rows of its own endpoint, the same rules a group member
    follows, so the two paths build byte-identical calls. Every other
    command is `to_device_calls` unchanged. Here rather than in
    `translate.py`, because this module imports that one."""
    if (command.cluster_id, command.command_id) != LUMITECH:
        return to_device_calls(command, value)
    rows = [
        row
        for row in device_rows
        if row.endpoint == command.endpoint
        and (row.cluster_id, row.command_id) in LIGHT_COMMAND_PAIRS
    ]
    return adapt_group_command(LUMITECH, rows, value)
```

Update the module docstring's second paragraph: the device path now shares this module for `lumitech`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/commands`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/commands/adapt.py tests/commands/test_adapt.py
git commit -m "feat(commands): translate a lumitech value into what the light carries

The group adapter already gives each member the part of a lighting
controller value it can carry. adapt_device_command reuses it for a single
light's lumitech output, so both paths build identical calls; XY goes first
because lumitech names no colour command.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The routes send `lumitech`; the controls leave it out

**Files:**
- Modify: `src/loxmatter/loxone/server.py` (`/cmd/{key}/{value}`, around line 685)
- Modify: `src/loxmatter/api/control.py` (`POST /api/commands/{key}` around line 364; `controls` around line 290)
- Modify: `src/loxmatter/api/groups.py` (`group_controls`, around line 205)
- Test: `tests/api/test_control.py`

**Interfaces:**
- Consumes: `adapt_device_command` (Task 3), `LUMITECH` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_control.py` a fixture of its own - the file's `api` fixture registers only the plug - and the tests. `/cmd/...` is served by the same `build_app`, so the Loxone route is tested from here too:

```python
@pytest.fixture
async def lamp_api(
    tmp_path, invocations, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int, int]]:
    """The KAJPLATS CWS and WS lamps with a recording invoker (design
    2026-09-24): the two light shapes the lumitech output must serve."""
    store = Store(tmp_path / "t.sqlite")
    ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        ids.append(device_id)

    async def invoke(call: DeviceCall) -> None:
        invocations.append(call)

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, ids[0], ids[1]
    store.close()


def _pairs(invocations: list[DeviceCall]) -> list[tuple[int, int, int]]:
    return [(c.endpoint, c.cluster_id, c.command_id) for c in invocations]


async def test_lumitech_on_a_single_tunable_white_lamp_sends_temperature_then_brightness(
    lamp_api, invocations
):
    """Design 2026-09-24, 3.1, and hardware check 1 in section 5.

    Fault to prove it: call `to_device_calls` in the route instead of
    `adapt_device_command` - the route answers 400."""
    client, _store, _cws, ws = lamp_api
    response = await client.post(f"/api/commands/d{ws}_1_lumitech", json={"value": "200302700"})
    assert response.status_code == 200
    assert _pairs(invocations) == [(1, 768, 10), (1, 8, 4)]
    assert invocations[0].payload["colorTemperatureMireds"] == 370


async def test_lumitech_through_the_loxone_route(lamp_api, invocations):
    """The same for `/cmd/{key}/{value}` - the route Loxone actually calls.

    Fault to prove it: switch only `api/control.py` to the adapter - this fails."""
    client, _store, _cws, ws = lamp_api
    response = await client.get(f"/cmd/d{ws}_1_lumitech/200302700")
    assert response.status_code == 200
    assert _pairs(invocations) == [(1, 768, 10), (1, 8, 4)]


async def test_the_controls_leave_the_lumitech_output_out(lamp_api):
    """It is no Matter command: no slider, and not "+1 more".

    Fault to prove it: remove the skip - `hidden_raw_commands` becomes 1."""
    client, _store, cws, _ws = lamp_api
    body = (await client.get(f"/api/devices/{cws}/controls")).json()
    assert "lumitech" not in [c["slug"] for c in body["commands"]]
    assert body["hidden_raw_commands"] == 0


async def test_the_group_lumitech_output_reaches_both_lamps_and_has_no_control(
    lamp_api, invocations
):
    client, _store, cws, ws = lamp_api
    group = (
        await client.post("/api/groups", json={"label": "G", "member_ids": [cws, ws]})
    ).json()
    controls = (await client.get(f"/api/groups/{group['id']}/controls")).json()
    assert "lumitech" not in [c["slug"] for c in controls["commands"]]
    assert controls["hidden_raw_commands"] == 0

    response = await client.post(
        f"/api/commands/g{group['id']}_lumitech", json={"value": "200302700"}
    )
    assert response.status_code == 200
    by_device: dict[str, list[tuple[int, int]]] = {}
    for call in invocations:
        by_device.setdefault(call.address, []).append((call.cluster_id, call.command_id))
    assert sorted(by_device.values()) == [[(768, 10), (8, 4)], [(768, 10), (8, 4)]]
```

If the group-creation route in this suite needs a `room` field, add `"room": None` as `tests/api/test_groups.py` does.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/api/test_control.py`
Expected: FAIL - 400 from the send route (`to_device_calls` has no builder for `(-1, 0)`), `hidden_raw_commands == 1`.

- [ ] **Step 3: Implement**

`loxone/server.py` and `api/control.py`: import `adapt_device_command` from `loxmatter.commands.adapt` and replace

```python
            calls = to_device_calls(stored, value)
```

with

```python
            # `adapt_device_command`, not `to_device_calls`: the `lumitech`
            # output needs its endpoint's other light rows (design
            # 2026-09-24, 4.1); for every other command it is the same call.
            calls = adapt_device_command(stored, store.commands(stored.device_id), value)
```

(`body.value` instead of `value` in `api/control.py`). Remove the now unused `to_device_calls` import where ruff reports it.

In `api/control.py`, `controls`, as the first statement inside `for command in stored:`:

```python
            # The lighting controller output is no Matter command: no
            # widget, and not "unnamed" either (design 2026-09-24, 4.1).
            if (command.cluster_id, command.command_id) == LUMITECH:
                continue
```

Same in `api/groups.py`, `group_controls`, inside its `for command in stored:` loop. Import `LUMITECH` in both.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/api` then `uv run pytest -q tests/loxone tests/commands`
Expected: PASS, apart from tests that assert a lamp's exact command list; sort those as in Task 1, Step 5.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/server.py src/loxmatter/api/control.py src/loxmatter/api/groups.py tests/
git commit -m "feat(api): send the lumitech output and keep it out of the controls

Both device routes now go through adapt_device_command so a lumitech value
reaches the light; the tile controls skip the pseudo command, which has no
widget and is not an unnamed raw command either.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Store - the `exported` column and the resolved flags

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_migration.py`, `tests/model/test_store_commands.py`, `tests/model/test_store_groups.py`

**Interfaces:**
- Consumes: `is_expert_light_command`, `LUMITECH` (Task 1).
- Produces:
  - `StoredCommand.exported: bool = True`, `StoredCommand.functional: bool = True` (new last fields, with defaults so hand-built rows in tests keep working)
  - `StoredGroupCommand.exported: bool = True`, `StoredGroupCommand.functional: bool = True`
  - `Store.set_command_exported(key: str, exported: bool) -> None` - device command; stamps `device.updated_at`
  - `Store.set_group_command_exported(key: str, exported: bool) -> None` - stamps `device_group.updated_at`
  - `_SCHEMA_VERSION = 13`

- [ ] **Step 1: Write the failing tests**

In `tests/model/test_store_migration.py`: replace every `== 12` user-version assertion with `== 13` (they assert "the chain ends at the latest version"), then append:

```python
def test_migration_to_v13_adds_a_nullable_exported_column_to_both_command_tables(tmp_path):
    """Design 2026-09-24, 4.2: every existing row starts at NULL and so
    follows the new default rule at once.

    Fault to prove it: give the column `DEFAULT 1` - the NULL check fails."""
    path = tmp_path / "v12.sqlite"
    store = Store(path)
    snap = load("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snap)
    store.register_commands(device_id, extract_commands(snap))
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript(
        "CREATE TABLE command_old AS SELECT id, device_id, node_id, endpoint, cluster_id,"
        " command_id, key, slug, takes_value FROM command;"
        " DROP TABLE command;"
        " ALTER TABLE command_old RENAME TO command;"
        " PRAGMA user_version = 12;"
    )
    db.commit()
    db.close()
    assert "exported" not in _columns(path, "command")

    store = Store(path)
    try:
        assert user_version(path) == 13
        assert "exported" in _columns(path, "command")
        assert "exported" in _columns(path, "group_command")
        raw = sqlite3.connect(str(path))
        assert {r[0] for r in raw.execute("SELECT exported FROM command")} == {None}
        raw.close()
    finally:
        store.close()
```

(`load` and `extract_commands` are used elsewhere in this file; add the imports if missing. If the table rebuild conflicts with the `UNIQUE` constraints the test relies on elsewhere, build the v12 table with the exact `CREATE TABLE command` of `_SCHEMA` minus the new column instead.)

In `tests/model/test_store_commands.py`, append:

```python
def _flags(store, device_id):
    return {c.slug: (c.exported, c.functional) for c in store.commands(device_id)}


def test_a_light_exports_only_its_lumitech_output_by_default(store):
    """Design 2026-09-24, decision 2.

    Fault to prove it: resolve `exported` as `True` for NULL - this fails."""
    device_id, _, _ = registered(store, "ikea_kajplats_cws_lamp.json")
    flags = _flags(store, device_id)
    assert flags.pop("lumitech") == (True, True)
    assert set(flags.values()) == {(False, False)}


def test_a_plug_keeps_every_command_exported(store):
    device_id, _, _ = registered(store, "ikea_grillplats_plug.json")
    assert set(_flags(store, device_id).values()) == {(True, True)}


def test_an_explicit_choice_wins_over_the_rule_and_survives_reregistration(store):
    device_id, snap, _ = registered(store, "ikea_kajplats_cws_lamp.json")
    store.set_command_exported(f"d{device_id}_1_color", True)
    store.set_command_exported(f"d{device_id}_1_lumitech", False)
    store.register_commands(device_id, extract_commands(snap))
    flags = _flags(store, device_id)
    assert flags["color"] == (True, False)
    assert flags["lumitech"] == (False, True)


def test_resolve_command_carries_the_same_flags(store):
    device_id, _, _ = registered(store, "ikea_kajplats_cws_lamp.json")
    assert store.resolve_command(f"d{device_id}_1_color").exported is False
    assert store.resolve_command(f"d{device_id}_1_lumitech").exported is True


def test_setting_the_flag_marks_the_device_changed_since_export(store):
    device_id, _, _ = registered(store, "ikea_kajplats_cws_lamp.json")
    store.mark_exported(device_id)
    store.set_command_exported(f"d{device_id}_1_color", True)
    device = store.device(device_id)
    assert device.updated_at > device.exported_at
```

(If `mark_exported`/`updated_at` are compared differently elsewhere in the suite, use `changed_since_export` from `loxmatter.model.store` the way the other tests do.)

In `tests/model/test_store_groups.py`, append. The file's `lamps` fixture registers no commands, so this fixture does:

```python
@pytest.fixture
def lamps_with_commands(store):
    ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        ids.append(device_id)
    return ids


def test_a_light_group_offers_lumitech_and_withholds_the_single_commands(
    store, lamps_with_commands
):
    group = store.create_group("Living room", lamps_with_commands)
    flags = {c.slug: (c.exported, c.functional) for c in store.group_commands(group.id)}
    assert flags.pop("lumitech") == (True, True)
    assert set(flags.values()) == {(False, False)}


def test_a_group_choice_survives_a_membership_recompute(store, lamps_with_commands):
    """`register_group_commands` updates surviving rows in place.

    Fault to prove it: make it DELETE and re-INSERT every row - this fails."""
    group = store.create_group("Living room", lamps_with_commands)
    store.set_group_command_exported(f"g{group.id}_color", True)
    store.set_group_members(group.id, lamps_with_commands)
    assert {c.slug: c.exported for c in store.group_commands(group.id)}["color"] is True
```

Import `extract_commands` from `loxmatter.export.commands` if the file does not. If `create_group` needs a room argument, pass it as the file's other tests do (`test_a_new_group_takes_its_category_from_its_first_member` shows the call).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/model`
Expected: FAIL - user version 12, missing column, `AttributeError: 'StoredCommand' object has no attribute 'exported'` / `'Store' object has no attribute 'set_command_exported'`.

- [ ] **Step 3: Implement**

In `src/loxmatter/model/store.py`:

1. `_SCHEMA`: add `    exported    INTEGER,` after `takes_value INTEGER NOT NULL,` in both `CREATE TABLE IF NOT EXISTS command` and `CREATE TABLE IF NOT EXISTS group_command` (the fresh-database schema at the top, not the historic copy inside `_migrate_to_v8`).
2. `_SCHEMA_VERSION = 13`.
3. After `_migrate_to_v12`:

```python
def _migrate_to_v13(db: sqlite3.Connection) -> None:
    """Adds `command.exported` and `group_command.exported` (design
    2026-09-24, 4.2), both nullable and without a default.

    `NULL` means "follows the default rule" - `Store` resolves it when it
    reads a row (`_resolved_flags`); `0`/`1` is a choice made in the web
    UI. Every existing row therefore follows the new rule on the first
    start, which is the maintainer's decision 3, without this migration
    writing a single value. Additive: a rolled-back image never reads the
    column."""
    _add_column_if_missing(db, "command", "exported", "INTEGER")
    _add_column_if_missing(db, "group_command", "exported", "INTEGER")
```

and `13: _migrate_to_v13,` in `_MIGRATIONS`.
4. `StoredCommand` and `StoredGroupCommand`: append

```python
    # Design 2026-09-24, 4.2: resolved by the store on read - an explicit
    # choice from the web UI, else the default rule
    # (`profiles.light_commands.is_expert_light_command`). `functional` is
    # the rule alone and never the user's; the web UI sorts by it.
    exported: bool = True
    functional: bool = True
```

5. `_COMMAND_SELECT` becomes:

```python
    _COMMAND_SELECT = (
        "SELECT command.*, device.technology AS technology, device.address AS address,"
        " EXISTS (SELECT 1 FROM command AS sibling"
        "  WHERE sibling.device_id = command.device_id"
        "  AND sibling.endpoint = command.endpoint"
        f"  AND sibling.cluster_id = {LUMITECH[0]} AND sibling.command_id = {LUMITECH[1]})"
        " AS endpoint_has_lumitech"
        " FROM command JOIN device ON device.id = command.device_id"
    )
```

Keep the existing comment above it and add a line on the `EXISTS`.
6. A module-level helper beside the dataclasses:

```python
def _resolved_flags(
    pair: tuple[int, int], explicit: object, has_lumitech: bool
) -> tuple[bool, bool]:
    """`(exported, functional)` for one command row (design 2026-09-24,
    4.2): `functional` is the default rule, `exported` the user's choice
    where there is one and the rule otherwise."""
    functional = not is_expert_light_command(pair, has_lumitech)
    exported = functional if explicit is None else bool(explicit)
    return exported, functional
```

7. `_as_command`: compute `exported, functional = _resolved_flags((int(row["cluster_id"]), int(row["command_id"])), row["exported"], bool(row["endpoint_has_lumitech"]))` and pass both.
8. `group_commands` and `resolve_group_command`: select with the group's own `EXISTS`:

```python
    _GROUP_COMMAND_SELECT = (
        "SELECT group_command.*,"
        " EXISTS (SELECT 1 FROM group_command AS sibling"
        "  WHERE sibling.group_id = group_command.group_id"
        f"  AND sibling.cluster_id = {LUMITECH[0]} AND sibling.command_id = {LUMITECH[1]})"
        " AS endpoint_has_lumitech"
        " FROM group_command"
    )
```

use it in both methods (`WHERE group_command.group_id = ? ORDER BY ...` / `WHERE group_command.key = ?`), and resolve the flags in `_as_group_command` the same way. Check every other `SELECT * FROM group_command` in the file with `grep -n "FROM group_command" src/loxmatter/model/store.py`; the ones feeding `_as_group_command` must use the new select.
9. Setters, next to `set_exported`:

```python
    def set_command_exported(self, key: str, exported: bool) -> None:
        """Records an explicit export choice for a device command (`PATCH
        /api/commands/{key}`, design 2026-09-24, 4.5). Stamps the owning
        device's `updated_at`: the choice changes its next template. No
        existence check, like `set_exported` - the route checks."""
        self._db.execute(
            "UPDATE device SET updated_at = ?"
            " WHERE id = (SELECT device_id FROM command WHERE key = ?)",
            (self._now(), key),
        )
        self._db.execute("UPDATE command SET exported = ? WHERE key = ?", (int(exported), key))
        self._db.commit()

    def set_group_command_exported(self, key: str, exported: bool) -> None:
        """The group counterpart of `set_command_exported`."""
        self._db.execute(
            "UPDATE device_group SET updated_at = ?"
            " WHERE id = (SELECT group_id FROM group_command WHERE key = ?)",
            (self._now(), key),
        )
        self._db.execute(
            "UPDATE group_command SET exported = ? WHERE key = ?", (int(exported), key)
        )
        self._db.commit()
```

10. Import `LUMITECH` and `is_expert_light_command` from `loxmatter.profiles.light_commands` (the module already imports `LIGHT_COMMAND_PAIRS` from there).
11. In `register_group_commands`'s docstring, add one sentence: a surviving row is updated in place, so its `exported` choice survives a recompute (design 2026-09-24, 4.2).

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/model` then part 1b of the suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/
git commit -m "feat(store): record which outputs are exported, defaulting a light to lumitech

A nullable exported column on command and group_command holds only choices
made in the web UI; NULL follows a rule the store resolves on read, so every
light commissioned before this version exports only its lumitech output
from the first start without the migration rewriting a row.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Export and project sync take only exported outputs

**Files:**
- Modify: `src/loxmatter/export/outputs.py`
- Modify: `src/loxmatter/api/export.py` (`_device_preview` around line 153, `_group_preview` around line 230)
- Modify: `src/loxmatter/api/models.py` (`ExportDeviceOut`, `ExportGroupOut`)
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.export.expert_withheld_explanation`)
- Modify: `src/loxmatter/web/index.html` (the group row of the export table, if it gets a withheld cell)
- Test: `tests/export/test_outputs.py`, `tests/export/test_group_outputs.py`, `tests/projectsync/test_diff.py`, `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `StoredCommand.exported`, `StoredGroupCommand.exported` (Task 5).
- Produces: `to_outputs`/`to_group_outputs` skip unexported rows; `output_title(slug: str) -> str` returns `"Lumitech / RGB"` for `lumitech`, the slug otherwise; `LUMITECH_TITLE = "Lumitech / RGB"`; `ExportDeviceOut.hidden_count` counts withheld signals **and** commands; `ExportGroupOut.hidden_count: int` (new).

- [ ] **Step 1: Write the failing tests**

In `tests/export/test_outputs.py` (read how it builds `StoredCommand`s first and reuse that helper):

```python
def test_an_unexported_command_is_not_an_output():
    """Design 2026-09-24, 4.4.

    Fault to prove it: remove the filter - `d1_1_color` appears."""
    commands = [
        _command("d1_1_lumitech", "lumitech", takes_value=True),
        replace(_command("d1_1_color", "color", takes_value=True), exported=False),
    ]
    assert [o.key for o in to_outputs(commands)] == ["d1_1_lumitech"]


def test_the_lumitech_output_is_titled_for_what_it_takes():
    [output] = to_outputs([_command("d1_1_lumitech", "lumitech", takes_value=True)])
    assert output.title == "Lumitech / RGB"
    assert output.analog is True


def test_on_alone_is_a_plain_output_when_off_is_withheld():
    commands = [
        _command("d1_1_on", "on", takes_value=False),
        replace(_command("d1_1_off", "off", takes_value=False), exported=False),
    ]
    outputs = to_outputs(commands)
    assert [(o.key, o.off_path) for o in outputs] == [("d1_1_on", "")]
```

(`from dataclasses import replace`. If the file has no `_command` helper, write one that builds a `StoredCommand` with `technology="matter"`, `address="1"`, `endpoint=1`, `cluster_id=6`, `command_id=0`, `device_id=1`.)

Mirror the first test in `tests/export/test_group_outputs.py` with `StoredGroupCommand`.

In `tests/projectsync/test_diff.py`, using the file's own helpers for a project index and stored rows: a project that already contains `d1_1_color` and a store where that row is unexported and `d1_1_lumitech` exported yields a plan with `d1_1_color` as `PlanStatus.ORPHANED` and `d1_1_lumitech` as new; assert both statuses by key.

In `tests/api/test_export_api.py`, with a registered CWS lamp: `GET /api/export/preview` reports `commands == 1` for it, and the downloaded `VO_*.xml` contains `d{id}_1_lumitech` and not `d{id}_1_color`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/export tests/projectsync` then `uv run pytest -q tests/api/test_export_api.py`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

In `export/outputs.py`:

```python
from loxmatter.profiles.light_commands import LUMITECH_SLUG

# The one output whose title is not its slug: "lumitech" names a format a
# Loxone user may not connect with the lighting controller, while "Lumitech
# / RGB" names both actuator types it takes (design 2026-09-24, decision 5).
# The same in both languages, so no i18n entry.
LUMITECH_TITLE = "Lumitech / RGB"


def output_title(slug: str) -> str:
    return LUMITECH_TITLE if slug == LUMITECH_SLUG else slug
```

Add `exported` to the `OutputCommand` protocol:

```python
    @property
    def exported(self) -> bool: ...
```

At the top of `_to_outputs`, before `by_group`:

```python
    # Only what the user exports (design 2026-09-24, 4.4) - filtered here,
    # once, because every export path (template, project sync, CLI) comes
    # through this function; the on/off pairing below then sees the
    # exported rows only.
    commands = [command for command in commands if command.exported]
```

and in the single-output `LoxoneCommand(...)` use `title=output_title(command.slug)`. Add one paragraph to `_to_outputs`'s docstring about the filter.

In `api/models.py`, `ExportGroupOut`: add `hidden_count: int = 0` with a comment. In `api/export.py`:

```python
    # hidden_count: signals AND commands the expert area withholds from
    # this export (design 2026-09-24, 4.5).
    hidden_count = sum(1 for s in signals if not s.functional) + sum(
        1 for c in commands if not c.functional
    )
```

and `commands=len(to_outputs(commands))` is wrong (a paired on/off adds an entry) - count rows instead: `commands=sum(1 for c in commands if c.exported)`. Same for `_group_preview` with `hidden_count=sum(1 for c in commands if not c.functional)`.

In `strings.yaml`, rewrite `web.export.expert_withheld_explanation` in both `en` and `de` to say that the column counts inputs and outputs the expert area holds back, keeping the existing sentence structure. If `index.html`'s group table has no withheld column, add a `<td x-text="group.hidden_count"></td>` in the same position as the device table's and a matching `<th>`.

- [ ] **Step 4: Run the suites and sort failures**

Run: 1b, then `uv run pytest -q tests/api`, then part 3.
Expected: the new tests PASS. Existing tests that expect `color`, `level`, `on`/`off` outputs for a lamp in a template, a preview count, a CLI export (`tests/test_export_cli.py`, `tests/test_cli.py`) or a sync plan now fail: update each to the new default with a comment naming design 2026-09-24, decision 2 - or, where the test is about the pairing or a single command's output shape, make it explicit by calling `store.set_command_exported(key, True)` first, so the test keeps testing what it was written for. List every updated test in the report.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/export/outputs.py src/loxmatter/api/export.py src/loxmatter/api/models.py src/loxmatter/i18n/strings.yaml src/loxmatter/web/index.html tests/
git commit -m "feat(export): export only selected outputs and title lumitech for its actuators

The template, the project sync and the CLI all go through to_outputs, so
one filter there decides which outputs exist; the sync then reports a
light's old single-command outputs as orphaned and lumitech as new.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: API - list outputs and set their export flag

**Files:**
- Create: `src/loxmatter/api/outputs.py`
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/loxone/server.py` (register the router next to `build_groups_router`)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_outputs_api.py` (new)

**Interfaces:**
- Consumes: `Store.set_command_exported`, `Store.set_group_command_exported`, `StoredCommand.exported/.functional` (Task 5), `output_title` (Task 6).
- Produces:
  - `OutputOut(key: str, slug: str, title: str, exported: bool, functional: bool)`
  - `OutputPatch(exported: bool)`
  - `GET /api/devices/{device_id}/outputs -> list[OutputOut]` (404 for an unknown or removed device)
  - `GET /api/groups/{group_id}/outputs -> list[OutputOut]` (404 for an unknown group)
  - `PATCH /api/commands/{key}` body `OutputPatch` -> `OutputOut`; device key first, then group key; 404 with `api.errors.unknown_command` for neither, 404 with `api.errors.command_belongs_to_removed_device` for a removed device's command.
  - `build_outputs_router(store: Store) -> APIRouter`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_outputs_api.py` with the GPL header (copy it from any test file), an `api` fixture copied from `tests/api/test_groups.py` (CWS lamp, WS lamp, plug, authenticated client), and:

```python
async def test_a_lamps_outputs_list_lumitech_first_as_functional(api):
    client, _store, lamps, _plug = api
    body = (await client.get(f"/api/devices/{lamps[0]}/outputs")).json()
    by_slug = {o["slug"]: o for o in body}
    assert by_slug["lumitech"] == {
        "key": f"d{lamps[0]}_1_lumitech",
        "slug": "lumitech",
        "title": "Lumitech / RGB",
        "exported": True,
        "functional": True,
    }
    assert {(o["exported"], o["functional"]) for s, o in by_slug.items() if s != "lumitech"} == {
        (False, False)
    }


async def test_patch_selects_a_single_command_for_export(api):
    """Fault to prove it: have the route ignore `exported` - the flag stays False."""
    client, store, lamps, _plug = api
    key = f"d{lamps[0]}_1_color"
    response = await client.patch(f"/api/commands/{key}", json={"exported": True})
    assert response.status_code == 200
    assert response.json()["exported"] is True
    assert store.resolve_command(key).exported is True


async def test_patch_reaches_a_group_command_too(api):
    client, store, lamps, _plug = api
    group = (await client.post("/api/groups", json={"label": "G", "member_ids": lamps})).json()
    key = f"g{group['id']}_lumitech"
    response = await client.patch(f"/api/commands/{key}", json={"exported": False})
    assert response.status_code == 200
    outputs = (await client.get(f"/api/groups/{group['id']}/outputs")).json()
    assert {o["key"]: o["exported"] for o in outputs}[key] is False


async def test_patch_on_an_unknown_key_is_a_404(api):
    client, *_ = api
    assert (await client.patch("/api/commands/d999_1_on", json={"exported": True})).status_code == 404


async def test_patch_on_a_removed_devices_command_is_a_404(api):
    client, _store, lamps, _plug = api
    await client.delete(f"/api/devices/{lamps[0]}")
    response = await client.patch(f"/api/commands/d{lamps[0]}_1_color", json={"exported": True})
    assert response.status_code == 404


async def test_the_outputs_of_an_unknown_device_are_a_404(api):
    client, *_ = api
    assert (await client.get("/api/devices/999/outputs")).status_code == 404
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/api/test_outputs_api.py`
Expected: FAIL - 404/405 on every route.

- [ ] **Step 3: Implement**

`api/models.py`, after `ControlsOut`:

```python
class OutputOut(BaseModel):
    """One virtual output as the web UI's "Outputs" part shows it (design
    2026-09-24, 4.5). `functional` places it - open at the top or in the
    expert block - and comes from the store's rule unchanged; `exported` is
    what the next template carries."""

    model_config = ConfigDict(frozen=True)

    key: str
    slug: str
    title: str
    exported: bool
    functional: bool


class OutputPatch(BaseModel):
    """Only the export flag can change. The key is the wiring in Loxone
    (Spec 6.2) - like `SignalPatch`, this model has no field for it."""

    model_config = ConfigDict(frozen=True)

    exported: bool
```

`api/outputs.py`:

```python
# (GPL header, copied from another module)

"""Which outputs a device or group exports (design 2026-09-24, 4.5).

Its own router rather than a part of `api/control.py`: that one drives
devices, this one edits what the next export carries - the same split
`api/devices.py`'s `PATCH /api/signals/{key}` makes for inputs. `PATCH
/api/commands/{key}` shares its path with `POST /api/commands/{key}` on
purpose: the key namespace is one (`d` vs `g` prefix), and a device key is
tried first, the way the send route does it.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import OutputOut, OutputPatch
from loxmatter.export.outputs import output_title
from loxmatter.model.store import (
    Store,
    StoredCommand,
    StoredGroupCommand,
    UnknownCommandError,
    UnknownDeviceError,
    UnknownGroupError,
)

__all__ = ["build_outputs_router"]


def _out(command: StoredCommand | StoredGroupCommand) -> OutputOut:
    return OutputOut(
        key=command.key,
        slug=command.slug,
        title=output_title(command.slug),
        exported=command.exported,
        functional=command.functional,
    )


def _ordered(commands: Sequence[StoredCommand | StoredGroupCommand]) -> list[OutputOut]:
    """Functional first, in the store's order within each part."""
    return [_out(c) for c in commands if c.functional] + [
        _out(c) for c in commands if not c.functional
    ]


def build_outputs_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/devices/{device_id}/outputs")
    async def device_outputs(device_id: int) -> list[OutputOut]:
        try:
            store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ordered(store.commands(device_id))

    @router.get("/groups/{group_id}/outputs")
    async def group_outputs(group_id: int) -> list[OutputOut]:
        try:
            store.group(group_id)
        except UnknownGroupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ordered(store.group_commands(group_id))

    @router.patch("/commands/{key}")
    async def patch_output(key: str, patch: OutputPatch) -> OutputOut:
        try:
            stored = store.resolve_command(key)
        except UnknownCommandError:
            try:
                store.resolve_group_command(key)
            except UnknownCommandError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            store.set_group_command_exported(key, patch.exported)
            return _out(store.resolve_group_command(key))
        try:
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.command_belongs_to_removed_device",
                    command_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
        store.set_command_exported(key, patch.exported)
        return _out(store.resolve_command(key))

    return router
```

`UnknownCommandError`, `UnknownDeviceError` and `UnknownGroupError` all subclass `KeyError` in `model/store.py`; `resolve_command` and `resolve_group_command` raise `UnknownCommandError`, `store.group` raises `UnknownGroupError`.

Register in `loxone/server.py`, next to `build_groups_router`:

```python
    app.include_router(build_outputs_router(store), dependencies=api_guard)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/api`
Expected: PASS. Also check `tests/api/test_security.py` still passes - it likely enumerates guarded routes; add the three new ones there if it lists routes explicitly.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/outputs.py src/loxmatter/api/models.py src/loxmatter/loxone/server.py tests/api/
git commit -m "feat(api): list a device's or group's outputs and choose which to export

GET .../outputs returns each output with its default placement and export
flag; PATCH /api/commands/{key} records the choice for a device or group
key, the namespace POST already shares.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Web UI - the "Outputs" part in the signal dialog and the group dialog

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html` (signal modal before its closing `</dialog>` around line 3402; group dialog before the error banner around line 3876)
- Modify: `src/loxmatter/web/style.css` (next to `.signal-grid`, around line 3056)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `GET /api/devices/{id}/outputs`, `GET /api/groups/{id}/outputs`, `PATCH /api/commands/{key}` (Task 7).
- Produces (app.js state and methods): `outputsBySubject: {}` keyed `"d<id>"` / `"g<id>"`; `outputsError: null`; `async loadOutputs(subject)`; `outputGroupsFor(subject)` returning `[{key: "functional"|"expert", title, collapsible, outputs}]`; `async toggleOutputExported(output)`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_web.py`, following how it fetches the served files:

```python
async def test_the_signal_dialog_ships_an_outputs_part(client):
    html = (await client.get("/")).text
    assert "outputGroupsFor(outputSubject('d', signalsModalDevice))" in html
    js = (await client.get("/static/app.js")).text
    assert "async toggleOutputExported(output)" in js
    assert '`/api/commands/${output.key}`' in js


async def test_the_group_dialog_ships_an_outputs_part_for_an_existing_group(client):
    html = (await client.get("/")).text
    assert "outputGroupsFor(outputSubject('g', groupDraft.id))" in html
```

(Adapt the fixture name to the file's.) These tests prove shipment only; Step 5 proves behaviour.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/api/test_web.py`
Expected: FAIL.

- [ ] **Step 3: Implement**

`strings.yaml` - new keys, `en` and `de`:

```yaml
# The "Outputs" part of the signal dialog and the group dialog (design
# 2026-09-24, 4.5).
web.outputs.heading:
  en: "Outputs"
  de: "Ausgänge"
web.outputs.col_output:
  en: "Output"
  de: "Ausgang"
web.outputs.group_expert:
  en: "Single commands (expert)"
  de: "Einzelbefehle (Experte)"
web.outputs.explanation:
  en: "Connect the lighting controller's Lumitech or RGB actuator to Lumitech / RGB. The single commands stay available here for special cases."
  de: "Den Lumitech- oder RGB-Aktor des Lichtbausteins an Lumitech / RGB anschließen. Die Einzelbefehle bleiben hier für Sonderfälle verfügbar."
web.outputs.load_error:
  en: "Could not load the outputs: {message}"
  de: "Ausgänge konnten nicht geladen werden: {message}"
web.outputs.export_flag_error:
  en: "Could not change the export selection: {message}"
  de: "Export-Auswahl konnte nicht geändert werden: {message}"
```

`app.js` - state next to `signalsModalDevice: null`:

```js
    // The "Outputs" part (design 2026-09-24, 4.5), keyed "d<id>" / "g<id>"
    // because a device and a group counter both start at 1.
    outputsBySubject: {},
    outputsError: null,
```

methods next to `toggleExported`:

```js
    outputSubject(kind, id) {
      return id === null || id === undefined ? null : kind + id;
    },

    async loadOutputs(subject) {
      if (!subject) {
        return;
      }
      this.outputsError = null;
      const path = subject.startsWith("g")
        ? `/api/groups/${subject.slice(1)}/outputs`
        : `/api/devices/${subject.slice(1)}/outputs`;
      try {
        this.outputsBySubject[subject] = await this.request("GET", path);
      } catch (error) {
        this.outputsError = t("web.outputs.load_error", { message: error.message });
      }
    },

    // Two groups, placed by the server's `functional` - no copy of the
    // rule here. Stable keys for the same `x-init` reason as
    // `signalGroupsFor`.
    outputGroupsFor(subject) {
      const outputs = (subject && this.outputsBySubject[subject]) || [];
      return [
        { key: "functional", title: t("web.outputs.heading"), collapsible: false,
          outputs: outputs.filter((o) => o.functional) },
        { key: "expert", title: t("web.outputs.group_expert"), collapsible: true,
          outputs: outputs.filter((o) => !o.functional) },
      ].filter((group) => group.outputs.length > 0);
    },

    async toggleOutputExported(output) {
      try {
        const updated = await this.request("PATCH", `/api/commands/${output.key}`, {
          exported: !output.exported,
        });
        Object.assign(output, updated);
      } catch (error) {
        this.outputsError = t("web.outputs.export_flag_error", { message: error.message });
      }
    },
```

Load them where the dialogs open: at the end of `openSignalsModal(device)` add `this.loadOutputs(this.outputSubject("d", device.id));`; in `openGroupMembers(group)` add `this.loadOutputs(this.outputSubject("g", group.id));`. Run `uv run ruff format --check .` - it does not touch JS; keep the file's own formatting by hand (two-space indent, trailing commas as the file does).

`index.html` - one block, used twice. In the signal modal, directly after the `</template>` that closes `x-for="group in signalGroupsFor(signalsModalDevice)"`, and in the group dialog inside `<template x-if="groupDraft.id !== null">` before the error banner, with `SUBJECT` replaced by `outputSubject('d', signalsModalDevice)` and `outputSubject('g', groupDraft.id)` respectively:

```html
              <!-- Outputs (design 2026-09-24, 4.5): the lighting controller
                   output open at the top, the single commands in the
                   collapsed expert block - the same `<details>` pattern and
                   the same `x-init` rule as the signal groups above. -->
              <section class="outputs">
                <h3 x-text="t('web.outputs.heading')"></h3>
                <p class="hint" x-text="t('web.outputs.explanation')"></p>
                <p class="banner danger" x-show="outputsError" x-cloak x-text="outputsError"></p>
                <template x-for="group in outputGroupsFor(SUBJECT)" :key="group.key">
                  <details class="signal-group" x-init="$el.open = !group.collapsible">
                    <summary>
                      <span x-text="group.title"></span>
                      <span class="muted" x-text="'(' + group.outputs.length + ')'"></span>
                      <svg class="icon chevron" aria-hidden="true"><use href="#i-chevron"></use></svg>
                    </summary>
                    <template x-for="output in group.outputs" :key="output.key">
                      <div class="output-grid">
                        <label class="col-center">
                          <input
                            type="checkbox"
                            :checked="output.exported"
                            :aria-label="t('web.signals.export_checkbox')"
                            :title="t('web.signals.export_checkbox')"
                            @change="toggleOutputExported(output)"
                          />
                        </label>
                        <span x-text="output.title"></span>
                        <span class="key" :title="t('web.signals.key_tooltip')" x-text="output.key"></span>
                      </div>
                    </template>
                  </details>
                </template>
              </section>
```

`style.css`, after the `.signal-grid` rules:

```css
/* The "Outputs" rows (design 2026-09-24, 4.5): checkbox, title, key - the
   export column lines up with the signal grid's first column above. */
.output-grid {
  display: grid;
  grid-template-columns: 2.5rem minmax(0, 1fr) minmax(0, 1.5fr);
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0;
}
.output-grid .key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

Match the first column's width to `.signal-grid`'s first column (read its `grid-template-columns`) instead of `2.5rem` if it differs.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest -q tests/api/test_web.py tests/test_i18n.py`
Expected: PASS.

- [ ] **Step 5: Prove the bindings in a browser**

The tests above prove shipment only. Build a throwaway harness in the scratchpad: a small `http.server` that serves `src/loxmatter/web/` with the vendored `vendor/alpine.min.js` and answers `/api/*` with made-up JSON (`/auth-info` and `/i18n` have no `/api` prefix; `/controls` returns `{commands, hidden_raw_commands}`), or run `scripts/dev_web_server.py --demo` under Playwright (`uv run --with playwright python <script>`), logging in with `page.fill` + `page.click('button:has-text("Log in")')`. Check, reading real DOM values:
1. Opening a lamp's signal dialog shows "Outputs" with "Lumitech / RGB" checked, and a collapsed "Single commands (expert)" block with unchecked rows.
2. Ticking a single command sends `PATCH /api/commands/<key>` with `{"exported": true}` and the box stays ticked.
3. A plug shows its commands in the open part, all checked, and no expert block.
4. The group dialog for an existing group shows the same part; the create dialog (no id) shows none.
5. At 375 px width no row overflows horizontally.

Use Playwright rather than the embedded browser for anything involving `<dialog>` close events. Report each check as passed, failed, or skipped with the reason.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): choose a device's or group's exported outputs in its dialog

The signal dialog and the group dialog gain an Outputs part: the lumitech
output open at the top, the single commands in a collapsed expert block,
each with the export checkbox the inputs already have.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Documentation and the whole-branch check

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-09-13-group-capability-fanout-design.md` (a dated note at the top)

- [ ] **Step 1: CHANGELOG**

Under `[Unreleased]`, in the sections the file already uses (read the existing block first; keep one `Added`, one `Changed`, one `Fixed` section), in its voice:
- Added: every light and light group has a Lumitech / RGB output for the Loxone lighting controller's Lumitech or RGB actuator; it drives colour, white temperature and brightness as far as the light supports them.
- Added: the signal dialog and the group dialog choose which outputs are exported.
- Changed: a light exports only its Lumitech / RGB output by default, also for lights added before this version; the single commands move to the expert area. Existing Loxone wiring keeps working; the project sync lists the old outputs as orphaned.
- Fixed: the colour temperature output rejected nothing and sent 0 mired for a Lumitech value; it now answers with an error.

- [ ] **Step 2: Group design note**

At the top of `2026-09-13-group-capability-fanout-design.md`, below the title paragraph:

```markdown
> **Note, 24 September 2026:** the open point in Section 7 ("the same
> colour-point fallback for a single device") is served by the `lumitech`
> output of `2026-09-24-lumitech-output-design.md`. The body below is not
> rewritten.
```

- [ ] **Step 3: Whole-branch check**

Run, each in the foreground: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`, then the four suite parts from Global Constraints. The sum of the four test counts must equal `uv run pytest --collect-only -q | tail -1`.
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-09-13-group-capability-fanout-design.md
git commit -m "docs(changelog): the lumitech output and the output export selection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hardware check (maintainer's test Pi, after merge approval)**

Not part of an implementer's task: done with the maintainer on `pi@10.0.1.56`, reading back through the running bridge's `signals` route, never a fresh `snapshots()` call - the five checks of the spec's Section 5.
