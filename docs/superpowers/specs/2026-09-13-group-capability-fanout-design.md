# Mixed Light Groups: Every Member Follows What It Can

Design, 13 September 2026. Changes how a software group of lights decides
which commands it offers and what each member receives, so that a group of
a colour lamp, a tunable-white lamp and a dim-only lamp can be driven from
one Loxone colour output.

It amends the device groups design of 10 September 2026
(`2026-09-10-device-groups-design.md`): its invariant 2 ("the command list
is the intersection") and Section 4.3 are replaced for light commands by
Section 3 below. Everything else in that design stands, including the
single category per group, the fan-out's ordering rules, and the absence of
any group state.

## 1. What Happened, and Why the Intersection Is the Wrong Rule for Lights

On 13 September 2026 the maintainer added a Zigbee TRADFRI WW lamp
(dim-only) to "Lampengruppe 1", whose other two members are Matter KAJPLATS
E14 CWS colour lamps. The group is driven from a Loxone lighting controller
with a colour actuator on `g1_color`.

The intersection rule then did exactly what the 10 September design says
it does: the WW lamp has no ColorControl, so `g1_color`, `g1_color_xy` and
`g1_colortemp` dropped out of the group, and the Loxone output answered
404. Because Loxone's colour actuator carries brightness inside the colour
value (`commands/translate.py`, `to_device_calls`), brightness changes from
Loxone stopped reaching *any* member, the dim-only lamp included. The lamp
did not react to brightness or colour.

The intersection was chosen so that a group never claims a capability no
member has. For lights that reasoning is too strict: the value Loxone sends
contains parts every light can use (on/off, brightness) and parts only some
can use (colour, white temperature). Dropping the whole command because one
part does not apply to one member throws away the parts that do. What the
maintainer asked for is the opposite: **every member takes over whatever it
can.**

## 2. Decisions Taken With the Maintainer

1. The group is driven from a **Loxone lighting controller colour actuator**
   (`g1_color` / `g1_color_xy`). One value carries either an RGB colour or a
   Lumitech white, each with its brightness. The separate outputs
   (`g1_level`, `g1_colortemp`, `g1_on`, ...) must keep working as well.
2. A **colour** value on a tunable-white lamp sets **brightness only**; the
   lamp keeps its white temperature. No white temperature is derived from a
   colour.
3. A **white** (Lumitech) value on a colour lamp **without** colour
   temperature support is **reproduced as a colour point** for that white,
   together with the brightness.
4. Groups stay **lights only**: one category per group, as before. An
   on/off-only light (category light) follows with on/off; plugs and
   switches stay in their own groups.

## 3. The Rule

### 3.1 What a Group Offers

A group's command list is computed from its members as follows:

- **Light commands** - the pairs in the table below - are offered when **at
  least one** member carries the pair. A pair no member carries is not
  offered, even if it could be adapted to what members do carry: a group of
  dim-only lamps gets no colour output.
- **Every other pair** (Identify, a vendor command, anything outside the
  table) is still offered only when **all** members carry it - the
  10 September intersection, unchanged. There is no meaningful adaptation
  for those, and "one member understood it" would be a claim about the
  group nobody could rely on.

| Pair | Slug | Light command |
|---|---|---|
| (6, 0) | `off` | yes |
| (6, 1) | `on` | yes |
| (6, 2) | `toggle` | yes |
| (8, 0) | `level` | yes |
| (8, 4) | `level_onoff` | yes |
| (768, 6) | `color` | yes |
| (768, 7) | `color_xy` | yes |
| (768, 10) | `colortemp` | yes |

A key keeps the form `g{group_id}_{slug}` and therefore stays stable across
membership changes, as today. A light command now drops out only when **no**
member can do anything with it; adding a dim-only lamp to a colour group no
longer removes `g1_color`.

The capabilities of a member are read from its **stored** commands, as the
intersection already does - never from a live query. The stored commands
are already gated by `profiles/capabilities.py` (ColorCapabilities and the
device type), so "carries (768, 10)" really means "can do colour
temperature", and an offline member does not change the group.

### 3.2 What Each Member Receives

The group's value is decoded **once**, as today, and then each member
receives the calls for the parts it can carry. A member's capability is the
set of light pairs among its stored commands:

- **colour XY** - carries (768, 7)
- **colour HS** - carries (768, 6)
- **white** - carries (768, 10)
- **dim** - carries (8, 4) or (8, 0)
- **switch** - carries (6, 0) and (6, 1)

| Loxone sends | colour lamp (XY or HS) | tunable-white lamp | dim-only lamp | on/off-only light |
|---|---|---|---|---|
| **colour** value (RGB + brightness) on `color` / `color_xy` | colour + brightness | brightness | brightness | on; off at brightness 0 |
| **white** value (Lumitech: Kelvin + brightness) on `color` / `color_xy` | white temperature if it has **white**, else the Kelvin as a colour point; + brightness | white temperature + brightness | brightness | on; off at brightness 0 |
| `level` / `level_onoff` | same command | same command | same command | on; off at 0 |
| `colortemp` (Kelvin) | white temperature if it has **white**, else colour point | white temperature | nothing | nothing |
| `on` / `off` / `toggle` | same command | same command | same command | same command |

Rules that apply across the table:

- **Which colour command a colour lamp gets:** the one the group command
  names when the member carries it - HS for `color`, XY for `color_xy` -
  and the other one only when it does not. A group of lamps that all carry
  both therefore sends exactly what it sent before this design.

- **Order within a member stays colour first, brightness second**, with
  `ExecuteIfOff` on the colour payload - the ordering recorded in
  `to_device_calls` and in Section 3.1 of the groups design. The adapted
  path reuses the existing payload builders; it does not build a second set.
- **Brightness 0 means off** for every member, the rule `to_device_calls`
  already applies: a single `MoveToLevelWithOnOff` with level 0, or `off`
  for an on/off-only light. No colour is sent at 0.
- **Brightness goes via `MoveToLevelWithOnOff` (8, 4)** when the member
  carries it, and via `MoveToLevel` (8, 0) only when that is all it has -
  the same preference as the device path, because Loxone means off with 0.
- **A member that receives nothing is not a failure.** `colortemp` sent to
  a dim-only lamp produces no call for it; it does not appear in the
  failure report and does not turn the response into an error.
- **A member that carries the pair on several endpoints** receives its calls
  on each of them, in endpoint order - unchanged from the groups design.
- **An invalid value** (a malformed Lumitech number, an out-of-range colour
  channel) is still rejected with 400 **before anything is sent**, because
  it is decoded once for the whole group.

### 3.3 White as a Colour Point

A colour lamp without white-temperature support receives a Lumitech white
or a `colortemp` value as a CIE 1931 chromaticity on the Planckian locus:

- **XY lamps:** Kelvin → (x, y) with the Kim et al. (2002) cubic
  approximation of the Planckian locus, valid 1667 K - 25000 K. Loxone's
  Lumitech range (2700 K - 6500 K) lies well inside it. The result is
  scaled to the Matter/ZCL 0-65279 range the existing `rgb_to_cie_xy`
  already uses.
- **HS-only lamps:** Kelvin → (x, y) as above → linear sRGB → gamma-encoded
  RGB, clipped and normalised to the largest channel → the existing
  `rgb_to_hue_saturation`. The brightness is not derived from this RGB;
  it comes from the Lumitech value.
- Kelvin outside 1667 K - 25000 K is clamped to that range, not rejected:
  the white temperature a lamp cannot reach is approximated by the nearest
  one it can, the same way a tunable-white lamp clamps to its own
  `ColorTempPhysicalMin/Max`.

The function lives in `commands/color.py` next to `rgb_to_cie_xy` and
`kelvin_to_mireds`, with its reference points pinned in tests (Section 6).

## 4. Where It Lives

- **`commands/adapt.py`** (new): a pure function
  `adapt_group_value(slug, value, member_pairs) -> list[call spec]`
  deciding, per member, which existing payload builder to use and with
  which command id. It knows nothing about HTTP, the store or sources,
  like `commands/fanout.py`.
- **`commands/translate.py`**: the payload builders stay where they are.
  The decoding of a Loxone colour value into "colour or white, plus
  brightness" is factored out of `_payload_hue_saturation` /
  `_payload_color_xy` so the adapter and the device path share one decoder.
  **The single-device path's behaviour does not change.**
- **`commands/fanout.py`**: `plan_group_calls` asks the adapter per member
  instead of translating the member's own row of the same pair. The
  concurrency and ordering rules in that module stay as they are.
- **`model/store.py`**:
  - `register_group_commands` computes the union for light pairs and the
    intersection for all others (Section 3.1). The rollback guard and the
    "stamp `updated_at` only when something changed" rule stay.
  - `group_targets` returns **every** member with its light-relevant stored
    commands, not only members carrying the exact pair. The docstring's
    "by construction there should be none" no longer holds and goes.
- **No schema change and no migration.** `group_command` rows keep their
  shape. The union is written on the next recompute, and
  `register_commands`' existing trigger already recomputes every group at
  startup (groups design 4.3), so "Lampengruppe 1" regains its colour
  commands on the first start of the new version.

## 5. What the User Sees

- **Group dialog:** nothing in the web UI states the intersection today. A
  hint is added under the members heading, shown while the group's category
  is light, in `en` and `de`: each lamp takes over what it supports - colour
  lamps the colour, the others brightness and on/off.
- **`web.devices.remove_confirm_groups_note`:** its sentence about a command
  dropping out of the "intersection" is reworded: a light command now drops
  out only when no remaining member can use it; other commands as before.
- **Export:** the group's template gains the outputs the union adds, with
  the keys it already had where they existed. A group whose command list
  changed reads "changed since the last export", as today.
- **CHANGELOG `[Unreleased]`:** the Groups entry (still unreleased) is
  rewritten from "offers only what *all* its members understand" to the new
  rule, in that section's voice.
- **Groups design:** a dated note at the top of
  `2026-09-10-device-groups-design.md` points here for invariant 2 and
  Section 4.3; its body is not rewritten.

## 6. Testing

- **Adapter table, per cell of Section 3.2.** The adapter takes a member's
  set of light pairs, so its tests use the pair sets of real devices rather
  than invented ones:
  - colour XY+HS+white: the stored commands of the checked-in Matter
    fixture `ikea_kajplats_cws_lamp.json`;
  - tunable white: `ikea_kajplats_ws_lamp.json`;
  - dim-only: the Zigbee TRADFRI bulb E27 WW. There is no fixture for it;
    its stored command rows, read from the maintainer's Pi on 13 September
    2026 (endpoint 1: (6, 0), (6, 1), (6, 2), (8, 0), (8, 4)), are written
    out in the test and named as captured;
  - on/off-only: no such light exists in the setup. Its pair set
    {(6, 0), (6, 1), (6, 2)} is written out in the test and named as
    constructed.

  Each cell asserts the exact calls and their order. Brightness 0 yields a
  single off call per member. `colortemp` on a dim-only member yields no
  call and no failure.
- **White as a colour point:** Kim et al. reference points pinned
  computed from the published coefficients (2700 K → (0.4593, 0.4107),
  4000 K → (0.3805, 0.3767), 6500 K → (0.3135, 0.3237), each within
  0.0005), plus clamping at both ends (1667 K → (0.5646, 0.4029),
  25000 K → (0.2525, 0.2523)).
- **Store:**
  - a colour group plus a dim-only member keeps `g{id}_color`,
    `g{id}_color_xy` and `g{id}_colortemp` with unchanged keys;
  - a non-light pair only one member carries is not offered;
  - removing the last colour member drops the colour commands;
  - `updated_at` moves only when the list changed.
- **Fan-out integration:** a Lumitech value through `/cmd/g{id}_color/{v}`
  on the three-lamp group produces white temperature on the CWS lamps and
  brightness on the WW lamp, in one fan-out, with per-member ordering intact.
- **The device path is unchanged:** the existing `to_device_calls` tests
  pass untouched. One added test pins that a single device's colour value
  still yields exactly the calls it did before the decoder was factored out.
- **Fault injection** for every protective test, as on the branches before
  it.
- **Hardware:** on the maintainer's Pi, "Lampengruppe 1" with both CWS lamps
  and the TRADFRI WW. Blue at 60 % from the Loxone colour actuator should
  turn the CWS lamps blue at 60 % and set the WW lamp to 60 %. 2700 K at
  30 % should set all three to 30 %, the CWS lamps warm white. Read back
  through the running bridge's `signals` route, never through a fresh
  `snapshots()` call.

## 7. Explicitly Not Built

- **Mixed categories** (a plug following a light group). Decision 4.
- **A white temperature derived from a colour** for tunable-white lamps.
  Decision 2.
- **The same colour-point fallback for a single device.** A single colour
  lamp without white-temperature support that receives a Lumitech value on
  its own `d{id}_color` output still gets a colour-temperature command it
  may reject, as today. It is a one-line reuse of Section 3.3 once this
  lands, and worth doing, but it changes the device path, which this design
  deliberately leaves untouched. Recorded as an open point.
- **Group state or aggregated feedback.** Unchanged from the groups design.

## 8. Open Points

1. **The single-device colour-point fallback** of Section 7.
2. **How close the colour point looks to a real white** on the KAJPLATS
   CWS depends on its LEDs. The CWS lamps carry white-temperature support
   and therefore never take this path; it is exercised only by a colour
   lamp without it, which the test setup does not have. It stays unverified
   on hardware until such a lamp is available.
