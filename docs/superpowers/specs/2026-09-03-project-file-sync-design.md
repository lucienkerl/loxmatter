# Project file sync: automatically create and update virtual inputs/outputs

Design, September 3, 2026. Supplements
[the main document](2026-09-01-matter-loxone-bridge-design.md), specifically
its section 3.2 (Loxone import) and section 6.1 of the template spec
referenced there (attribute schema of the template files, implemented in
`export/documents.py`).

## 1. The problem

Today `loxmatter` exports two template files per device (`VirtualInUdp`/
`VirtualOut`, see `export/documents.py`), which a user imports manually
into Loxone Config — per device, freshly each time. If a device's signal
set changes (new firmware, a newly enabled signal in the signal
selection), the user has to redo that by hand and figure out for
themselves what changed, without destroying existing wiring to function
blocks.

Goal of this design: `loxmatter` accepts a real Loxone Config project
file, matches it against the stored devices/signals, and returns a
patched version in which existing virtual inputs/outputs are updated (not
replaced) and missing ones are newly created — without the user having to
gather individual templates by hand anymore.

## 2. Non-goals

- **No live connection to the Miniserver for this feature.** The
  Miniserver's runtime API (`/dev/sps/io/...`, `LoxAPP3.json`) only
  delivers a simplified, purely read-only view of existing IOs — not the
  full project structure with `ControlList`, `NextObj`, etc. The project
  file comes exclusively from Loxone Config itself (export, or "Load
  program from Miniserver"), the user uploads it in the WebUI.
- **No automatic wiring to function blocks.** Stays manual work as
  recorded in section 3.2 of the main document — the gain would be small,
  the risk high.
- **No automatic deletion.** Orphaned objects (signal no longer in the
  export, device removed) are reported, not touched. Deleting is riskier
  than creating and was not requested.
- **No reimplementation of the proprietary Miniserver upload protocol.**
  The user still opens the patched file themselves in Loxone Config and
  saves it from there to the Miniserver — that is the part only Config
  itself can do safely (section 3.2 of the main document).

## 3. Decisions

### 3.1 Input path: file upload in the WebUI, no live connection

Originally, "automatically connect to the Miniserver" was on the table.
But the Miniserver API cannot create IOs and also does not deliver the
full project structure — only Loxone Config itself has both.
Reverse-engineering Config's proprietary project protocol was already
rejected in the main document as "disqualifying for a tool that runs in
other people's houses," and it remains so here.

Chosen: the user uploads the `.Loxone` project file in the WebUI, the
tool returns a patched version for download. One manual step remains —
export the file from Config, reopen the patched version, and save it to
the Miniserver — but the effort *per device and signal* is gone, and that
is the actual pain point from section 1.

### 3.2 Writing: text surgery instead of rebuilding the XML

`export/xml.py` deliberately builds template files without an XML
library, because a serializer could reorder attributes or write the
declaration differently, without that being verifiable here. The same
argument applies to the project file with greater weight: 3 MB,
predominantly block types this project doesn't know and shouldn't touch.

Therefore: **reading** via a standard XML parser (the real reference file
parses without complaint with `xml.etree.ElementTree` — the duplicate
attributes feared in the main document did not occur anywhere, so if they
exist anywhere, they concern block types other than the ones relevant
here). **Writing** exclusively as targeted text replacement at exactly the
byte ranges that change: a changed attribute is replaced within its
existing `<C .../>` tag, a new object is inserted as fully rendered XML
text immediately before the closing tag of its container. Everything else
in the document stays byte-identical — that is the property a round trip
through a generic serializer could not guarantee.

### 3.3 Matching via the existing signal key

Every signal already carries, in the project file, the key `loxmatter`
itself assigns: in the `Check` attribute for inputs
(`Check="d3_1_onoff:\v"`, cf. `render_virtual_in_udp`) and in the `CmdOn`
path for outputs (`CmdOn="/cmd/d3_1_onoff/1"`, cf. `_command_path` in
`export/outputs.py`). Per `model.store`, these keys are already globally
unique across all devices.

The matching therefore searches the entire document for
`VirtualUdpInCmd`/`VirtualOutCmd` elements whose `Check`/`CmdOn` begins
with a known key — independent of the title (which the user may have
renamed in Config) and independent of which container the signal
actually hangs under. Matching via the device container (title) would be
more fragile and is not necessary.

**Correction for outputs: `CmdOn` alone is not enough** (user report
2026-09-05, "after export and re-import a new onoff field"). The combined
on/off output from `export.outputs.to_outputs` sends the same path on a
rising edge as the single `on` command (`/cmd/d1_1_on/1`) and differs from
it only by its `CmdOff`. Read from `CmdOn` alone, both got the same key,
and one overwrote the other in the index — the combined command was
nowhere to be found under its real key (`"d1_1_on + d1_1_off"`, as
`to_outputs` assigns it) and was recreated on **every** run. The key of an
existing output is therefore read from `CmdOn` AND `CmdOff` together
(`keys.key_from_output_cmd`). Side effect of the same collision: the
`possible_duplicate` protection did not catch this, because the displaced
element was no longer in the index at all.

### 3.4 Risk levels: update is the default case, creation is opt-in

The real reference file parses cleanly, but the `U` ID scheme for new
objects is proprietary and unverified (section 6). Updating an existing
object only changes known attribute values within a structure Config has
already accepted — low risk. Creating something new (a new container or
a new Cmd object with a self-generated ID), by contrast, carries a real,
unconfirmed risk: if Config rejects the file on opening or silently
discards parts of it, the user has, in the worst case, a damaged project.

Therefore: the diff plan **always shows both** (informationally), but the
file offered for download contains newly created **device containers**
only if the user explicitly checks that in the WebUI ("Also create new
device containers — experimental, not yet validated against Loxone
Config"). Without the checkbox, the file contains updates to existing
objects plus new signals *within* already existing device containers —
both lower risk, because no new container is created, only new leaves in
a structure Config has already accepted.

This is not a global switch and not a config file — once the user has
successfully imported a file with a freshly created container, the
checkbox is simply routine for them from then on.

### 3.5 File structure & Miniserver assignment (corrected after a real-world test)

**The original assumption in this section (and in 3.3) was wrong.** In
the first test against a real, grown project file (not just the small
reference file from the brainstorming phase), it turned out:
`VirtualInCaption`/`VirtualOutCaption` do **not** sit directly under
`<ControlList>`. The real structure is:

```
<ControlList>
  <C Type="Document">                    -- exactly ONE child of ControlList
    <C Type="LoxLIVE" IntAddr="…">       -- one block PER configured Miniserver
      <C Type="VirtualInCaption"> … </C>
      <C Type="VirtualOutCaption"> … </C>
    </C>
    <C Type="LoxLIVE" IntAddr="…"> … </C>  -- optional: additional Miniservers
  </C>
</ControlList>
```

The original, flat search algorithm parsed such files without error, but
found **not a single** existing virtual input/output — every already
existing device therefore falsely appeared as `new_device`, instead of
`unchanged`/`updated`. This exact bug was reported by the user on his real
file and was the trigger for this correction.

**Consequence: Miniserver assignment is a step of its own, BEFORE the
actual matching.** A project file can contain multiple `LoxLIVE` blocks
(multiple Miniservers configured in Loxone Config within one project) —
the matching must only search within ONE of them, otherwise a signal
could wrongly land in the wrong Miniserver's area. Resolution
(`index._resolve_target_loxlive`):

- No `LoxLIVE` block found → error (no place for virtual inputs/outputs).
- Exactly one block → is chosen automatically, regardless of whether an
  IP was supplied.
- An IP was supplied → must correspond exactly to one `LoxLIVE.IntAddr`
  (the same value as with `loxmatter run --miniserver <IP>`), otherwise
  an error with a listing of the found Miniservers — even if there is
  only one block: a non-matching, but explicitly specified IP points more
  toward the wrong file than toward a reason to ignore it.
- Multiple blocks, no IP supplied → error, an IP is mandatory.

The matching from section 3.3 ("searches the entire document") is
henceforth refined to "searches within the resolved `LoxLIVE` block" — no
longer across the entire `<ControlList>`. Newly created captions
(section 6, creation path) accordingly attach to the end of this
`LoxLIVE` block, not to the end of `<ControlList>`.

## 4. Architecture & data flow

```
User uploads .Loxone file in the WebUI
        │
        ▼
Parsing (read-only, ElementTree) + matching against stored devices/signals
        │
        ▼
Diff plan: new (signal) / new (device) / updated / unchanged / orphaned
        │
        ▼
WebUI shows the plan for confirmation — nothing has been written yet
        │
        ▼
User sets the experimental checkbox if desired, confirms
        │
        ▼
Patched file (text surgery on the original byte stream) for download
```

One server request is enough: `POST /api/export/project-sync` delivers
the diff plan and both file variants (with/without new device containers)
in one response. The "Confirm" step is purely client-side — no second
round trip, no server-side intermediate state.

The patched file is always a **new** file; the uploaded original is never
overwritten anywhere. A failed patch attempt is thus without consequence —
the user simply uploads again.

## 5. Diff plan: data model and cases

Per known signal (from `model.store`, filtered on `exported`), one of
four states:

| State | Condition | Effect in the patched file |
|---|---|---|
| `unchanged` | matching object found, all relevant attributes agree | none |
| `updated` | matching object found, at least one attribute differs | only the differing attributes are replaced within the existing tag; `U` and all `Co`/`In` children (wiring) remain untouched |
| `new_signal` | no matching object, but the device container already exists | new `VirtualUdpInCmd`/`VirtualOutCmd` is appended to the end of the existing container |
| `new_device` | no matching object, and no container exists yet at all for this device | new `VirtualUdpIn`/`VirtualOut` container is created under `VirtualInCaption`/`VirtualOutCaption`, with the first Cmd child |

In addition, independent of the table above: **`orphaned`** — a
`VirtualUdpInCmd`/`VirtualOutCmd` in the file carries a key that no
longer corresponds to any currently known, exported signal (device
removed, signal deselected). Is reported, not modified (section 2).

**`possible_duplicate`** (addendum after a real-world test, 2026-09-05):
no object found with the desired key, BUT an existing command in the same
device container already carries exactly the desired title. Points more
toward a damaged/stale `Check`/`CmdOn` on a single existing object than
toward a genuinely new signal — observed on a combined output command
"onoff" whose `CmdOn` was missing a character due to an old export bug
(`/cmd/d1_1_o/1` instead of `/cmd/d1_1_onoff/1`). Without this check, the
sync would have created a second "onoff" command in the same container
instead of recognizing the damaged one. Like `orphaned`/`conflict`, it is
never created automatically — the user has to check/repair the existing
command themselves in Loxone Config.

`updated` additionally carries the concrete attribute diff (old value →
new value per attribute), so the user sees in the plan *what* is
changing, not just *that* it is.

If all known signals are `unchanged` and there are no `orphaned` ones,
the plan says so explicitly ("Everything up to date, no changes needed")
instead of showing an empty list.

## 6. ID assignment for new objects

Every new object (container as well as Cmd) needs a new `U` ID that is
unique within the file. The format is proprietary
(`<hex>-<hex>-<hex>-<hex-suffix>`) and undocumented. Generation: a
timestamp-based prefix, combined with the installation suffix (the last
16 hex digits), taken from any existing object *from the same uploaded
file*, so that new IDs belong to the same project family. Uniqueness is
checked against all `U` values found in the file, not only against the
newly created ones.

The root node `ControlList` carries counters (`NextObj`, `NextConst`,
...) that Config increments when it creates objects itself. Their exact
meaning is unverified; to be on the safe side, `NextObj` is conservatively
raised above the highest numeric portion occurring in the file, if new
objects were created.

**This is the unverified part of this design.** Whether Config opens a
file created this way without complaint is something nobody knows until
it has been tested against a real installation at least once. Hence
section 3.4: creating new device containers is opt-in, not the default,
until a successful test import. Creating new Cmd objects *within* an
existing container carries the same ID risk, but a significantly smaller
structural risk (no new container, no new parent structure) and therefore
stays the default.

**Missing `V` attribute (found in the real-world test, 2026-09-05).** The
first real test showed: newly created device containers did appear in
Loxone Config, but their command children stayed empty. Cause: EVERY `<C>`
object in the real reference file carries a `V` attribute (checked on all
3710 occurring objects, without exception — practically always `"178"`,
only the `Document` root object carries the full Config version number).
The original attribute list for newly created containers/Cmds/captions
simply didn't have this attribute on the radar. Fixed: all five
`new_*_open_tag` functions (`projectsync/schema.py`) now write `V="178"`.
The same check also uncovered that a newly created caption
(`VirtualInCaption`/`VirtualOutCaption`, section 8: special case of full
creation) wrongly carried an `IName` that real captions do not have, and
a fixed `Title` (`"Virtuelle Eingänge"`/`"Virtuelle Ausgänge"`) was
missing — also corrected.

**The unit belongs in `<Display>`, not on `<C>`** (user report 2026-09-05,
"the unit is no longer there for the virtual inputs"). The *template
file* carries the unit as an attribute (`virtual_in_udp_cmd_attributes`),
a *project file* does not: there, not a single `<C>` object carries a
`Unit` attribute (again checked on all 3710), the unit sits exclusively
in the `<Display>` child — as a complete format string including the unit
text, accompanied by `Type="2"` for an analog value
(`<Display Type="2" Unit="&lt;v.3&gt; kW" StateOnly="true"/>`, as on all
86 analog inputs of the reference file). The template attribute list
adopted for creating new objects therefore wrote `Unit` to a place Loxone
Config never reads, while the `<Display>` got a fixed `Unit="<v.1>"` with
no unit. Fixed: `new_input_cmd_open_tag` filters `Unit` out,
`new_cmd_children_xml` writes the format string and `Type` into
`<Display>`. `Unit` is thus also no longer a managed update attribute
(`MANAGED_INPUT_CMD_ATTRS`) — otherwise every analog input would appear
as "updated" again on every run. The `<Display>` of an **existing** object
stays untouched, like any other structure the user may have set up
themselves.

## 7. API & WebUI

**Endpoint:** `POST /api/export/project-sync`, multipart with the
uploaded `.Loxone` file, plus query parameters `bridge_ip`/`port`/
`listen` (as with `/api/export/download`) and optionally `miniserver_ip`
(section 3.5) — needed only if the file configures more than one
Miniserver. Response: structured diff plan (section 5) plus two file
variants (`patched_conservative`, `patched_with_new_devices`) — OR, with
multiple Miniservers and no `miniserver_ip` chosen (addendum after the
review, 2026-09-05), `needs_miniserver_selection=True` plus
`available_miniservers` (title + `IntAddr` of every found Miniserver)
instead of a plan. Both are a normal 200 response, not an error — only
the "none configured at all" case remains a genuine 400 (section 8).

**WebUI:** primary entry in the export area (per user request,
originally planned under "System"). File upload, then either the plan
directly, or — with multiple Miniservers in the file — a selection field
with the found Miniservers (user request: select instead of typing the
IP by hand, replaces the earlier text field). The file stays in the
browser's memory for this (no repeated file dialog needed); the selection
triggers the same upload a second time, this time with `miniserver_ip`
set. After that, the plan as a list — grouped by device (inputs/outputs
per device, analogous to the later Loxone structure), one row per signal
with a status badge (unchanged/updated/new/orphaned/possible duplicate)
and, for `updated`, the attribute diff. The experimental checkbox
switches which of the two delivered file variants the download button
offers. The download button is active only after the upload (= the plan
has been seen).

## 8. Error handling

- File is not a valid Loxone project file (no `ControlList` root, etc.)
  → clear error message, no crash, no file offered.
- `VirtualInCaption`/`VirtualOutCaption` is missing entirely (the project
  never had a virtual input/output) → treated as a special case of
  creation, also behind the experimental checkbox.
- A found object with a matching key looks structurally unexpected (e.g.
  wrong object type for the key) → marked as `conflict`, is skipped and
  reported explicitly instead of silently overwritten or adopted.
- No object found with a matching key, but an existing command in the
  same container already carries the same title (section 5,
  `possible_duplicate`) → is skipped instead of creating a silent
  duplicate.
- No changes needed → the plan says so explicitly (section 5), no empty
  or confusing state.

## 9. Tests

A synthetic, deliberately small fixture project file, hand-built
following the schema observed in sections 3–6 — analogous to the existing
`tests/fixtures/loxone/*.xml`. **Not** the real file supplied by the
user — that stays outside the repo because of personal data (addresses,
device titles).

At minimum, the following should be covered:

- Update of an existing `VirtualUdpInCmd`/`VirtualOutCmd`: only the
  differing attributes change, `U` and `Co`/`In` children are preserved
  exactly.
- Creation of a new Cmd in an existing container.
- Creation of a complete container (only when the experimental path is
  tested).
- An orphaned signal is reported, not modified.
- Byte identity of all file parts that aren't part of the plan (diff of
  the file before/after the patch, minus the planned change locations,
  must be empty).
- ID uniqueness of newly generated `U` values against all existing ones.
- The "no changes needed" case delivers the explicit message, not an
  empty list.

## 10. Open risks

- **ID scheme unverified** (section 6) — the central remaining risk
  point of this design. Mitigated by the experimental checkbox
  (section 3.4), not resolved.
- **`NextObj`/`NextConst` semantics unverified** — the conservative raise
  (section 6) is an assumption, not proven behavior.
- **Format deviations between Config versions** possible — checked so
  far on two real files (the original reference file and the file on
  which the structure correction from section 3.5 was found). Other
  versions could still deviate.
- **Done (2026-09-04):** the question left open in an earlier version of
  this section about the actual nesting was the actual first real bug —
  fixed and documented in section 3.5.

The first real test remains: open an automatically patched file in
Loxone Config and check for errors before trusting it — especially for
the experimental path (new device containers). The ID scheme itself
(section 6) has so far only been checked against the `U` values found in
the file, not confirmed by a successful import into Loxone Config.
