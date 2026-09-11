from loxmatter.export.signals import SignalKind
from loxmatter.export.xml import BOM
from loxmatter.model.store import SignalRef, StoredCommand, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import build_plan
from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.patch import apply_plan


def _signal(key: str, device_id: int, title: str = "Ein/Aus", unit: str = "") -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title=title,
        unit=unit,
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
        resend=False,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
        room=None,
        device_types=None,
    )


def _command(key: str, slug: str, device_id: int, command_id: int) -> StoredCommand:
    return StoredCommand(
        key=key,
        slug=slug,
        node_id=device_id,
        endpoint=1,
        cluster_id=6,
        command_id=command_id,
        takes_value=False,
        device_id=device_id,
    )


def _patch_bytes(index, device, signals, *, commands=()):
    commands = list(commands)
    plan = build_plan(index, [device], {device.id: signals}, {device.id: commands})
    return apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: commands},
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )


def _patch(index, device, signals, *, commands=()):
    return _patch_bytes(index, device, signals, commands=commands).decode("utf-8-sig")


def test_updated_attribute_is_replaced_in_place(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals)
    assert 'Title="Ein/Aus"' in patched
    assert 'Title="Alter Titel"' not in patched
    # The u-id of the updated object stays exactly preserved - wiring
    # (Co) must never be touched by an update.
    assert '"1000-0002-0000-aaaaaaaaaaaaaaaa"' in patched
    assert '<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>' in patched


def test_orphaned_object_is_left_untouched(sample_project):
    """An orphaned signal is only reported, never changed (draft
    section 2). That is a content guarantee, not a byte-identity one - that
    is checked by `test_unchanged_plan_leaves_the_file_byte_identical`
    below."""
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals)
    assert 'Title="Verwaist"' in patched
    assert 'Check="d9_9_verwaist:\\v"' in patched


# Like `sample_project`, but the existing output command carries a
# corrupted `CmdOn` (missing "n": `/cmd/d1_1_o/1` instead of
# `/cmd/d1_1_onoff/1`) with a correct title "onoff" - the user report
# "onoff shows up twice" (see `test_diff.py`,
# `test_title_collision_with_mismatched_key_is_possible_duplicate`, for the
# same fixture at the level of `diff.build_plan`).
CORRUPTED_ONOFF_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="http://10.0.0.9:8080"'
    ' CloseAfterSend="true" CmdSep=";">\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="onoff" Nio="1" WF="16400" CmdOn="/cmd/d1_1_o/1" CmdOnMethod="1" Tx="false">\r\n'
    '\t\t\t\t\t\t<Co K="I" U="1000-000d-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_possible_duplicate_is_not_patched_in():
    """`apply_plan` treats `POSSIBLE_DUPLICATE` like `ORPHANED`/`CONFLICT`:
    only UPDATED/NEW_SIGNAL/NEW_DEVICE produce an edit (see the `if`/`elif`
    chain there) - without this check, the corrupted existing "onoff"
    command would have gotten a second, genuine "onoff" in the same
    container."""
    index = build_index(CORRUPTED_ONOFF_PROJECT)
    device = _device(1, "Altes Geraet")
    commands = [
        _command("d1_1_on", "on", 1, 1),
        _command("d1_1_off", "off", 1, 0),
    ]
    patched = _patch(index, device, [], commands=commands)
    # The corrupted existing command stays exactly as it was ...
    assert 'CmdOn="/cmd/d1_1_o/1"' in patched
    # ... and NO second "onoff" gets added - only exactly ONE
    # `Title="onoff"` in the whole document.
    assert patched.count('Title="onoff"') == 1


def _unchanged_signals() -> list[StoredSignal]:
    """Signals that exactly match what is in `sample_project` - so the plan
    contains neither `updated` nor `new_signal`/`new_device` (see
    `tests/projectsync/test_diff.py`,
    `test_has_changes_is_false_when_everything_matches`)."""
    return [_signal("d1_1_onoff", 1, title="Alter Titel")]


def test_unchanged_plan_leaves_the_file_byte_identical(sample_project):
    """The core guarantee of the whole design (section 3.2/9): what is not
    in the plan is not touched. A plan without any planned change must
    therefore return EXACTLY the same bytes - the only permitted deviation
    is a leading BOM if the original had none (see the `patch` module
    docstring).

    Deliberately a full byte comparison instead of a few substring checks:
    only that way does a change nobody thought of while writing the test
    also get noticed."""
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    plan = build_plan(index, [device], {1: _unchanged_signals()}, {1: []})
    assert plan.has_changes is False

    patched = _patch_bytes(index, device, _unchanged_signals())
    assert patched == ("﻿" + sample_project).encode("utf-8")
    assert patched.decode("utf-8").lstrip("﻿") == sample_project.lstrip("﻿")


def test_existing_bom_is_preserved_and_not_duplicated(sample_project):
    """Counterpart to the test above: if the original already had a BOM,
    exactly ONE comes back, not two. Loxone Config writes its project file
    with a BOM, so this case is the normal one - the BOM-less one above is
    the special case (see the `patch` module docstring)."""
    with_bom = BOM + sample_project
    index = build_index(with_bom)
    device = _device(1, "Altes Geraet")
    patched = _patch_bytes(index, device, _unchanged_signals())

    assert patched == with_bom.encode("utf-8")
    assert patched.decode("utf-8").count(BOM) == 1


def test_created_u_ids_are_unique_across_the_whole_file(sample_project):
    """Id uniqueness of newly generated `U` values against ALL existing
    ones (draft section 6/9) - via a scenario that exercises both creation
    paths at once: a new signal in an existing container (device 1) and a
    completely new device with several signals and commands (device 2).
    Each of these generates, besides the object itself, `Co` wiring stubs
    with their own ids."""
    import re

    index = build_index(sample_project)
    devices = [_device(1, "Altes Geraet"), _device(2, "Neues Geraet")]
    signals = {
        1: [_signal("d1_1_onoff", 1, title="Alter Titel"), _signal("d1_1_temp", 1)],
        2: [_signal("d2_1_onoff", 2), _signal("d2_1_temp", 2)],
    }
    commands = {1: [], 2: [_command("d2_1_on", "on", 2, 1), _command("d2_1_off", "off", 2, 0)]}
    plan = build_plan(index, devices, signals, commands)
    # Record before patching: `new_unique_id` immediately adds every
    # generated id to `index.all_u_values`.
    u_count_before = len(index.all_u_values)
    patched = apply_plan(
        index,
        plan,
        devices,
        signals,
        commands,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    ).decode("utf-8-sig")

    all_u = re.findall(r'\bU="([^"]*)"', patched)
    assert len(all_u) > u_count_before  # some were really created
    assert len(set(all_u)) == len(all_u)


def test_new_signal_is_appended_inside_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [
        _signal("d1_1_onoff", 1, title="Alter Titel"),
        _signal("d1_1_temp", 1, title="Temperatur"),
    ]
    patched = _patch(index, device, signals)
    assert 'Check="d1_1_temp:\\v"' in patched
    # Inserted into the same container as the existing d1_1_onoff, not
    # somewhere in the document and not as a new device container. Checked
    # via build_index instead of byte-offset arithmetic: a naive
    # `patched.index("</C>", container_start)` would find the closing tag
    # of the FIRST child (VCI1), not that of the container itself - the
    # same bug task 3 already had to fix once in the scanner.
    patched_index = build_index(patched)
    assert "d1_1_temp" in patched_index.input_containers
    assert (
        patched_index.input_containers["d1_1_temp"].attrs["U"]
        == index.input_containers["d1_1_onoff"].attrs["U"]
    )


def test_new_device_gets_its_own_container(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals)
    assert 'Check="d2_1_onoff:\\v"' in patched
    assert 'Title="Matter — Neues Geraet"' in patched
    assert 'Address="10.0.0.5"' in patched


def test_new_device_with_several_signals_gets_exactly_one_container(sample_project):
    """A completely new device with SEVERAL new signals may get exactly ONE
    `VirtualUdpIn` container that carries all commands as children - not
    its own container per signal.

    This case is not exotic, it is the normal case: `export.signals.
    to_inputs` always additionally generates the online signal for every
    device, so every real new device has at least two `NEW_DEVICE` entries.
    One container per entry would result in several identically named
    devices with an identical address/port - a structurally invalid
    project file."""
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2), _signal("d2_1_temp", 2, title="Temperatur", unit="°C")]
    commands = [_command("d2_1_on", "on", 2, 1), _command("d2_1_off", "off", 2, 0)]
    patched = _patch(index, device, signals, commands=commands)

    # Exactly one new container each - in addition to the one each the
    # sample file already brings for device 1.
    assert patched.count('Type="VirtualUdpIn"') == 2
    assert patched.count('Type="VirtualOut"') == 2

    patched_index = build_index(patched)
    new_input_keys = {key for key in patched_index.input_containers if key.startswith("d2_")}
    assert new_input_keys == {"d2_1_onoff", "d2_1_temp", "d2_online"}
    # All three hang off the SAME container (same u-id).
    assert len({patched_index.input_containers[key].attrs["U"] for key in new_input_keys}) == 1

    new_output_keys = {key for key in patched_index.output_containers if key.startswith("d2_")}
    # The combined on/off command also stands under its own double key in
    # the index - before the fix to `key_from_output_cmd`, it dropped out
    # here because it shares its `CmdOn` with the single `on` command and
    # one overwrote the other.
    assert new_output_keys == {"d2_1_on", "d2_1_off", "d2_1_on + d2_1_off"}
    assert len({patched_index.output_containers[key].attrs["U"] for key in new_output_keys}) == 1


def test_next_obj_is_raised_when_new_objects_were_created(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals)
    next_obj = int(patched.split('NextObj="', 1)[1].split('"', 1)[0])
    assert next_obj > 100  # starting value in the sample file


def test_output_is_valid_xml(sample_project):
    import xml.etree.ElementTree as ET

    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals)
    ET.fromstring(patched)  # raises on invalid XML


def test_missing_attribute_is_inserted_into_existing_tag(sample_project):
    """The existing output command `d1_1_on` has no `Analog` attribute at
    all in the sample document, but `desired_output_cmd_attrs` requires
    one - this covers the `if span is None` insertion branch in
    `_update_edits`, which no other test touches (all other updates only
    change an already-present `Title`).

    This test used to run via `Unit` on an INPUT. That no longer works:
    `Unit` is no longer a managed attribute of the `<C>` tag, because a
    project file does not carry the unit there at all (see
    `MANAGED_INPUT_CMD_ATTRS`)."""
    import xml.etree.ElementTree as ET

    index = build_index(sample_project)
    # Before: no `Analog=` on the VQC1 tag itself.
    assert "Analog" not in index.output_cmds["d1_1_on"].attrs

    device = _device(1, "Altes Geraet")
    commands = [_command("d1_1_on", "on", 1, 1)]
    patched = _patch(index, device, [], commands=commands)

    # Newly inserted into exactly the tag it was previously missing from -
    # not somewhere else in the document.
    patched_index = build_index(patched)
    assert patched_index.output_cmds["d1_1_on"].attrs["Analog"] == "true"
    # The tag's other, unchanged attributes stay preserved - the insertion
    # only appends before the closing '>', instead of replacing the tag.
    assert 'CmdOn="/cmd/d1_1_on/1"' in patched
    assert 'Title="on"' in patched

    ET.fromstring(patched)  # raises on invalid XML


# Synthetic project file that deliberately contains NO `VirtualInCaption`
# section at all - unlike `sample_project` from conftest.py, which always
# has both sections. A real project in which no virtual input has ever been
# created looks like this. Deliberately not in conftest.py, because this
# document is only needed for the auto-create path in `_new_device_edit`.
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


# A project in which an attribute whose name ENDS in a managed attribute
# name (`XTitle`) sits BEFORE the real `Title`. Such names are not ruled out
# in a real project file - this project by no means knows all the block
# types Loxone Config writes.
DECOY_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' XTitle="Bitte nicht anfassen" Title="Alter Titel" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t\t\t<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_update_does_not_rewrite_an_attribute_that_only_ends_in_the_name():
    """`_attr_span` searched for `Title="..."` without a word boundary on
    the left - `re.search` therefore returned the first match anywhere in
    the tag, including the second half of a longer attribute name like
    `XTitle`. The update then silently wrote into the WRONG attribute and
    left the real one untouched: exactly the breach of the promise to never
    touch bytes this project does not understand (draft section 3.2)."""
    index = build_index(DECOY_ATTR_PROJECT)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Neuer Titel")]
    patched = _patch(index, device, signals)

    assert 'XTitle="Bitte nicht anfassen"' in patched
    patched_index = build_index(patched)
    cmd = patched_index.input_cmds["d1_1_onoff"]
    assert cmd.attrs["XTitle"] == "Bitte nicht anfassen"
    assert cmd.attrs["Title"] == "Neuer Titel"


# Like `sample_project`, but WITHOUT a single `U` attribute anywhere in
# the document - the case draft section 10 names as an open risk (a file
# entirely without `U` attributes, or a Config version with a different id
# format). `d1_1_onoff` already exists (stays `unchanged`), `d1_1_temp` is
# still missing - that forces a new id via `_new_signal_edit` ->
# `new_unique_id` -> `_installation_suffix`, without needing a completely
# new device.
NO_U_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document">\r\n'
    '\t\t<C Type="LoxLIVE" Title="Testserver" IntAddr="10.0.0.10">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" Title="Matter — Altes Geraet" WF="16384"'
    ' Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" Title="Alter Titel" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_new_signal_without_any_existing_u_id_raises_project_format_error():
    """Finding N1 from the re-review: `_installation_suffix` used to raise a
    bare `ValueError` when no existing `U` id in the expected 4-hex-group
    format could be found - unhandled at the upload endpoint, an HTTP 500
    instead of the usual understandable 400 (draft section 8). Expected is
    a `ProjectFormatError`, as for any other recognized format error in
    this file too."""
    import pytest

    from loxmatter.projectsync.scan import ProjectFormatError

    index = build_index(NO_U_ATTR_PROJECT)
    assert index.all_u_values == set()

    device = _device(1, "Altes Geraet")
    signals = [
        _signal("d1_1_onoff", 1, title="Alter Titel"),
        _signal("d1_1_temp", 1, title="Temperatur"),
    ]
    plan = build_plan(index, [device], {1: signals}, {1: []})

    with pytest.raises(ProjectFormatError, match="Installations-Suffix"):
        apply_plan(
            index,
            plan,
            [device],
            {1: signals},
            {1: []},
            bridge_ip="10.0.0.5",
            port=7000,
            listen=8080,
        )


def test_next_obj_edit_is_skipped_when_next_obj_is_not_numeric(sample_project):
    """Finding N1 from the re-review: `_next_obj_edit` used to call
    `int(index.root_attrs["NextObj"])` unprotected - a non-decimal value
    raised a bare `ValueError`, unhandled an HTTP 500. According to draft
    section 6/10, `NextObj` is anyway only an unverified, conservative
    best effort, not documented behavior - so a broken value must not fail
    the whole (otherwise valid) patch, but only skip this one attribute
    change."""
    bad_project = sample_project.replace('NextObj="100"', 'NextObj="not-a-number"')
    index = build_index(bad_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals)

    # The broken attribute stays untouched ...
    assert 'NextObj="not-a-number"' in patched
    # ... but the actual creation still took place (so the
    # `created_count > 0` branch in `_next_obj_edit` really was reached,
    # not just the early `created_count == 0` exit).
    assert 'Check="d2_1_onoff:\\v"' in patched


def test_new_device_without_virtual_in_caption_creates_the_caption_too():
    """A device that needs a completely new input container, in a project
    without any existing `VirtualInCaption` section: `_new_device_edit`
    creates this section itself too (draft section 8, user request after
    the review) - the user should not have to create something in Loxone
    Config by hand first. The device command then sits INSIDE the newly
    created caption, not next to it."""
    import xml.etree.ElementTree as ET

    index = build_index(NO_VIRTUAL_IN_CAPTION_PROJECT)
    assert index.virtual_in_caption is None

    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    plan = build_plan(index, [device], {device.id: signals}, {device.id: []})

    patched = apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: []},
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )
    patched_text = patched.decode("utf-8-sig")

    assert 'Type="VirtualInCaption"' in patched_text
    ET.fromstring(patched_text)  # raises on invalid XML

    patched_index = build_index(patched_text)
    assert patched_index.virtual_in_caption is not None
    cmd = patched_index.input_cmds["d2_1_onoff"]
    container = patched_index.input_containers["d2_1_onoff"]
    # The command sits in a container that in turn hangs off the newly
    # created caption - not as a loose sibling object next to it.
    assert container in patched_index.virtual_in_caption.children
    assert cmd in container.children


def test_new_analog_input_carries_its_unit_into_the_file(sample_project):
    """User report: "the unit is no longer there for the virtual inputs".
    Checked end to end: a newly created analog input must carry its unit
    in the finished file - specifically in the `<Display>` child, the only
    place a real project file carries it."""
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [
        StoredSignal(
            key="d2_2_voltage",
            ref=SignalRef(endpoint=2, cluster_id=144, element_id=4, kind=SignalKind.ATTRIBUTE),
            title="voltage",
            unit="V",
            exportability=Exportability.ANALOG,
            device_id=2,
            exported=True,
            functional=True,
            resend=False,
        )
    ]
    patched = _patch(index, device, signals)

    cmd_start = patched.index('Check="d2_2_voltage')
    cmd_end = patched.index("</C>", cmd_start)
    cmd_xml = patched[patched.rindex("<C ", 0, cmd_start) : cmd_end]
    assert '<Display Type="2" Unit="&lt;v.1&gt; V" StateOnly="true"/>' in cmd_xml


def test_patched_file_is_stable_when_synced_again(sample_project):
    """User report: a file generated by the sync uploaded again, and it
    wanted to create something all over again ("a new onoff field"). A
    file that was just written MUST be completely `unchanged` on the
    second pass - anything else means the diff does not recognize its own
    output and accumulates duplicates on every run."""
    from loxmatter.projectsync.diff import PlanStatus

    device = _device(2, "Neues Geraet")
    signals = [
        _signal("d2_1_onoff", 2),
        # Analog WITH a unit: additionally covers that the unit ends up in
        # `<Display>` and not as a `Unit` attribute on the `<C>` tag, which
        # would otherwise keep coming back as "updated" forever on the
        # second run.
        StoredSignal(
            key="d2_2_voltage",
            ref=SignalRef(endpoint=2, cluster_id=144, element_id=4, kind=SignalKind.ATTRIBUTE),
            title="voltage",
            unit="V",
            exportability=Exportability.ANALOG,
            device_id=2,
            exported=True,
            functional=True,
            resend=False,
        ),
    ]
    commands = [_command("d2_1_on", "on", 2, 1), _command("d2_1_off", "off", 2, 0)]

    first = _patch(build_index(sample_project), device, signals, commands=commands)
    # The combined on/off output was created exactly once ...
    assert first.count('Title="onoff"') == 1

    # ... and the second run over the same file plans nothing more.
    second_index = build_index(first)
    second_plan = build_plan(second_index, [device], {device.id: signals}, {device.id: commands})
    unstable = [
        (e.kind, e.key, e.title, e.status)
        for e in second_plan.entries
        if e.device_id == device.id and e.status is not PlanStatus.UNCHANGED
    ]
    assert unstable == []
    assert second_plan.has_changes is False
