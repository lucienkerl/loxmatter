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

"""Every zigpy name `zigbee/source.py` and `zigbee/configure.py` depend on,
checked against the installed library.

**This is the one test module in the Zigbee suite that imports zigpy, and
it exists precisely because the others must not.** `tests/zigbee/fakes.py`
stands in for a radio, and a fake proves nothing about the library it
imitates: the source matches failures by exception NAME and spells zigpy's
configuration keys out as string constants - both deliberately, so that
opening a radio costs no import at startup (see the source's module
docstring) - and both would go silently dead if a name here were wrong. A
fake with the same wrong name would keep every other test in this suite
green while every real device failed.

That is not a hypothetical on this branch. A sentinel table once tested for
`0x8000` while the attribute's real type made `-32768` the only value the
library can produce; the row was dead, the test stayed green, and a sensor
with no reading would have published -327.68 °C into the house. This module
is the answer to that class of mistake: it is cheap (importing
`zigpy.exceptions`, `zigpy.config` and `zigpy.zcl` costs about half a
second, and nothing here opens a port or walks the quirks registry), and it
fails loudly on the day an upgrade renames something.

No hardware is involved, and none of this says the bridge works against a
radio - only that it is talking to the library that is actually installed.
"""

from __future__ import annotations

import inspect
import re

import pytest

from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.zigbee import configure as configure_module
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.configure import (
    IAS_CIE_ADDRESS_ATTRIBUTE,
    IAS_ENROLL_REQUEST_COMMAND,
    IAS_ENROLL_RESPONSE_COMMAND,
    IAS_ENROLL_SUCCESS,
    IAS_STATUS_CHANGE_NOTIFICATION_COMMAND,
    IAS_ZONE_CLUSTER,
    IAS_ZONE_STATUS_ATTRIBUTE,
    IAS_ZONE_TYPE_ATTRIBUTE,
    IDENTIFY_CLUSTER,
    IDENTIFY_COMMAND,
    PACKET_PRIORITY_HIGH,
    POLL_CONTROL_CHECKIN_COMMAND,
    POLL_CONTROL_CLUSTER,
    REPORTING,
)
from loxmatter.zigbee.source import (
    _RADIO_MODULES,
    _STARTUP_MESSAGES,
    _UNREACHABLE_EXCEPTION_NAMES,
    ATTRIBUTE_EVENTS,
    PERMIT_MAX_SECONDS,
    ZigbeeSource,
    _ApplicationListener,
)
from loxmatter.zigbee.translate import _SENTINELS


def test_every_failure_name_the_source_catches_exists_in_zigpy() -> None:
    """The source catches by name over an exception's MRO, so a name that
    zigpy does not have catches nothing at all - and a zigpy `DeliveryError`
    would leave the boundary untranslated and reach `api/devices.py`'s
    removal route as an unhandled 500.

    Fault to prove it: misspell one name in
    `_UNREACHABLE_EXCEPTION_NAMES` (`ControllerError`, which is what the
    design calls it, is exactly the misspelling this guards against)."""
    import zigpy.exceptions

    for name in sorted(_UNREACHABLE_EXCEPTION_NAMES):
        if name == "TimeoutError":
            continue  # a builtin, not zigpy's
        assert hasattr(zigpy.exceptions, name), f"zigpy.exceptions has no {name}"
        assert issubclass(getattr(zigpy.exceptions, name), Exception)

    # And the leaves really do reach the roots, which is what lets the two
    # roots above stand for zigpy's whole failure vocabulary.
    assert issubclass(zigpy.exceptions.DeliveryError, zigpy.exceptions.ZigbeeException)
    assert issubclass(zigpy.exceptions.ControllerException, zigpy.exceptions.ZigbeeException)
    assert issubclass(zigpy.exceptions.TransientConnectionError, zigpy.exceptions.RadioException)


def test_the_network_mismatch_exception_is_spelled_the_way_the_source_expects() -> None:
    """The one startup failure matched by name rather than by type - the
    stick that already carries somebody else's network.

    Fault to prove it: rename it in `_STARTUP_MESSAGES`. The bridge then
    reports a generic radio failure and the user never learns that their
    network is the reason."""
    import zigpy.exceptions

    matches = {
        key: predicate
        for predicate, key in _STARTUP_MESSAGES
        if key == "api.errors.zigbee_network_mismatch"
    }
    assert matches, "no rule for a stick carrying another network"
    predicate = matches["api.errors.zigbee_network_mismatch"]
    # `(message, new_state, old_state)` - zigpy carries both backups on the
    # exception. The source only ever catches it, so the two states are
    # stand-ins here; `tests/zigbee/fakes.py` takes the message alone and
    # says so.
    raised = zigpy.exceptions.NetworkSettingsInconsistent("PAN ID differs", object(), object())
    assert predicate(raised)


def test_the_configuration_keys_are_zigpys_own_and_the_config_validates() -> None:
    """The source spells zigpy's configuration keys out as constants, so
    that building a config costs no zigpy import. A wrong key would not
    raise - zigpy's schema fills in its own default and the setting would
    simply not apply, which for the OTA switch means a bridge that updates
    the user's lamps from the internet after all.

    Fault to prove it: change `CONF_OTA_ENABLED` to `"ota_enabled"`. The
    config below then validates with OTA back ON."""
    import zigpy.application
    import zigpy.config

    for name in (
        "CONF_DEVICE",
        "CONF_DEVICE_PATH",
        "CONF_DEVICE_BAUDRATE",
        "CONF_DEVICE_FLOW_CONTROL",
        "CONF_DATABASE",
        "CONF_NWK",
        "CONF_NWK_CHANNELS",
        "CONF_NWK_VALIDATE_SETTINGS",
        "CONF_OTA",
        "CONF_OTA_ENABLED",
        "CONF_TOPO_SCAN_ENABLED",
    ):
        assert getattr(source_module, name) == getattr(zigpy.config, name), name

    source = ZigbeeSource(
        path="/dev/ttyUSB0",
        fingerprint=Fingerprint(
            name="SONOFF ZBDongle-E V2",
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
        ),
        database="/data/zigbee.sqlite",
        thread_channel=15,
    )
    raw = {key: value for key, value in source._config().items() if not key.startswith("_")}
    validated = zigpy.application.ControllerApplication.SCHEMA(raw)

    assert validated[zigpy.config.CONF_OTA][zigpy.config.CONF_OTA_ENABLED] is False
    assert validated[zigpy.config.CONF_NWK_VALIDATE_SETTINGS] is True
    assert validated[zigpy.config.CONF_TOPO_SCAN_ENABLED] is True
    assert validated[zigpy.config.CONF_DATABASE] == "/data/zigbee.sqlite"
    # zigpy turns the list into its own `Channels` bit flag, which is why
    # the comparison goes through `from_channel_list` rather than through
    # the list the source handed over.
    import zigpy.types

    channels = validated[zigpy.config.CONF_NWK][zigpy.config.CONF_NWK_CHANNELS]
    assert channels == zigpy.types.Channels.from_channel_list([11, 20, 25])
    assert validated[zigpy.config.CONF_DEVICE][zigpy.config.CONF_DEVICE_FLOW_CONTROL] == "software"


def test_the_application_calls_the_source_makes_have_the_arguments_it_passes() -> None:
    """`new(start_radio=False, device_resolver=...)`, `startup(auto_form=)`,
    `shutdown(db=)`, `permit(time_s=, node=)` and `remove(ieee)`.

    Fault to prove it: pass `auto_form` to `new()` instead of to
    `startup()`. The radio then forms a network while the source still
    thinks it only read the database."""
    import zigpy.application

    application = zigpy.application.ControllerApplication
    new = inspect.signature(application.new).parameters
    assert {"config", "start_radio", "device_resolver"} <= set(new)
    assert new["start_radio"].default is True, "start_radio must be passed explicitly"
    assert "auto_form" in inspect.signature(application.startup).parameters
    assert "db" in inspect.signature(application.shutdown).parameters
    permit = inspect.signature(application.permit).parameters
    assert {"time_s", "node"} <= set(permit)
    assert "ieee" in inspect.signature(application.remove).parameters
    for name in ("add_listener", "remove_listener", "listener_event", "connection_lost"):
        assert hasattr(application, name), name

    # And the source really does split the two calls that way: the database
    # is loaded with the radio shut, and forming is asked for only when the
    # radio is opened.
    # The body, not the docstring, which names both calls.
    built = inspect.getsource(source_module._default_application).split('"""')[-1]
    assert "start_radio=False" in built
    assert "auto_form" not in built
    assert "auto_form=True" in inspect.getsource(ZigbeeSource.connect)


def test_the_four_attribute_events_are_the_ones_a_cluster_emits() -> None:
    """The source registers these four names on every cluster. A fifth name,
    or a renamed one, registers a listener that is never called - and a
    Zigbee device then reports nothing while the bridge looks healthy.

    Fault to prove it: add `"attribute_changed"` to `ATTRIBUTE_EVENTS`."""
    import zigpy.zcl

    emitted = {
        zigpy.zcl.AttributeReportedEvent.event_type,
        zigpy.zcl.AttributeReadEvent.event_type,
        zigpy.zcl.AttributeUpdatedEvent.event_type,
        zigpy.zcl.AttributeWrittenEvent.event_type,
    }
    assert set(ATTRIBUTE_EVENTS) == emitted
    assert len(ATTRIBUTE_EVENTS) == 4
    # And the registration call itself, which returns its own unsubscribe.
    assert callable(zigpy.zcl.Cluster.on_event)


def test_the_device_surface_the_source_reads_is_public_and_shaped_as_expected() -> None:
    """`Cluster._attr_cache` is an `AttributeCache` in zigpy 2.2.0, not a
    dict, and offers no way to enumerate what it holds - so the source reads
    `Cluster.attributes` (every declared attribute) and asks
    `Cluster.get(attribute_id)` for each. A fake offering a dict there would
    have hidden an `AttributeError` that every real device would hit.

    Fault to prove it: iterate `cluster._attr_cache.items()` in
    `_endpoint_facts`."""
    import zigpy.device
    import zigpy.endpoint
    import zigpy.zcl
    import zigpy.zcl.clusters.lighting

    assert not hasattr(zigpy.zcl.AttributeCache, "items")
    assert callable(zigpy.zcl.Cluster.get)
    colour = zigpy.zcl.clusters.lighting.Color
    assert isinstance(colour.attributes, dict)
    # ColorCapabilities: the attribute the colour picker of every Zigbee
    # lamp hangs off, and the one zigpy's interview does not read.
    assert 0x400A in colour.attributes
    assert colour.attributes[0x400A].name == "color_capabilities"
    assert hasattr(zigpy.device.Device, "non_zdo_endpoints")
    assert "in_clusters" in inspect.getsource(zigpy.endpoint.Endpoint.__init__)

    # And the source stays on that public surface.
    facts = inspect.getsource(source_module.ZigbeeSource._endpoint_facts)
    assert "_attr_cache" not in facts
    assert "cluster.get(" in facts


def test_cluster_get_raises_on_a_duplicate_attribute_id_and_not_on_its_definition() -> None:
    """The Critical this module exists to keep fixed.

    `Cluster.get(key)` calls `self.find_attribute(key)` OUTSIDE its own
    `try`, and `find_attribute` raises `KeyError("Multiple definitions exist
    for attribute ID ...")` for any id a cluster class declares twice - once
    as a standard attribute, once as a manufacturer-specific one.
    `Cluster.attributes` is keyed by id, so it shows one of the two and the
    duplicate is invisible there; `_attributes_by_id` keeps both, and that is
    what the ambiguity check reads. A lookup by DEFINITION short-circuits:
    `find_attribute` returns the first candidate at once for a non-integer
    key.

    Why it mattered: `_endpoint_facts` feeds `snapshots()` and `cli._run`
    calls `attach()` unguarded, so one Ubisys dimmer in the network stopped
    the bridge from starting - before uvicorn, with no HTTP surface left to
    say why.

    Fault to prove it: iterate `for attribute_id in cluster.attributes` and
    call `cluster.get(attribute_id)` in `_endpoint_facts`, as the code did.

    Real classes, not a constructed one: `UbisysLevelControl` is shipped by
    the installed zha-quirks and is the case the bug report will name."""
    from zhaquirks.ubisys.dimmer_d1 import UbisysLevelControl

    with pytest.raises(KeyError, match="Multiple definitions exist"):
        UbisysLevelControl.find_attribute(0x0000)

    definition = UbisysLevelControl.attributes[0x0000]
    assert UbisysLevelControl.find_attribute(definition) is definition
    # And the survivor really is the manufacturer-specific one here, which
    # is the residual `_endpoint_facts` documents: a Ubisys D1 exports its
    # minimum-on level where `current_level` belongs.
    assert definition.name == "minimum_on_level"
    assert definition.is_manufacturer_specific is True

    # `Cluster.get` itself, not only `find_attribute` - the call the source
    # actually makes, on an instance. The endpoint is only ever stored by
    # `Cluster.__init__`, so `None` is enough to build one here.
    cluster = UbisysLevelControl(None)
    with pytest.raises(KeyError, match="Multiple definitions exist"):
        cluster.get(0x0000)
    assert cluster.get(definition) is None  # nothing cached: a default, not a raise

    # And the source stays on the definition.
    facts = inspect.getsource(source_module.ZigbeeSource._endpoint_facts)
    assert "cluster.attributes.items()" in facts
    assert "cluster.get(definition)" in facts


def test_how_many_shipped_quirks_carry_a_duplicate_attribute_id() -> None:
    """The blast radius, counted rather than guessed - and a canary for the
    day an upgrade makes it larger or the check unnecessary.

    Walking the registry is the only honest way to answer "how common is
    this": the 28 pairs below are spread over five quirk modules, not the
    one the first report named. Asserting a floor rather than an exact
    number, so a zha-quirks release that adds a Ubisys device does not turn
    this into a failing test for no reason; a release that drops to zero
    would be news and is asserted against as well.

    `zhaquirks.setup()` IS called here - it is the only way the registry
    holds every quirk module - which is what makes this the slowest test in
    the file (2-3 s). It is the one place in the suite that pays it."""
    import zhaquirks
    from zigpy.zcl import Cluster

    zhaquirks.setup()

    duplicates: list[tuple[str, str, int]] = []
    seen: set[type] = set()
    pending = list(Cluster.__subclasses__())
    while pending:
        cluster_class = pending.pop()
        if cluster_class in seen:
            continue
        seen.add(cluster_class)
        pending.extend(cluster_class.__subclasses__())
        for attribute_id in cluster_class.attributes:
            try:
                cluster_class.find_attribute(attribute_id)
            except KeyError:
                duplicates.append((cluster_class.__module__, cluster_class.__name__, attribute_id))

    assert duplicates, (
        "no shipped quirk declares a duplicate attribute id any more - if that is "
        "real and not a broken walk, `_endpoint_facts` can be simplified"
    )
    assert len(duplicates) >= 28, len(duplicates)
    assert ("zhaquirks.ubisys.dimmer_d1", "UbisysLevelControl", 0x0000) in duplicates
    # Five modules, not one: the first report said "all under ubisys".
    assert {"zhaquirks.philips", "zhaquirks.innr.innr_sp120_plug"} <= {
        module for module, _, _ in duplicates
    }


def test_every_listener_method_is_an_event_zigpy_actually_emits() -> None:
    """zigpy dispatches to a listener BY METHOD NAME through
    `ListenableMixin.listener_event`, and that method swallows every
    listener exception. So a renamed or misspelled event here is the most
    silently dead name in the source: no error, no log at warning level,
    just a bridge that stops noticing something.

    `hasattr(ControllerApplication, "connection_lost")` - which is all the
    module checked before - proves nothing about dispatch: it finds
    `ControllerApplication`'s OWN method, not the listener event of the same
    name, and there is no such method for `device_reinterviewed` at all.
    This reads the names out of zigpy's source instead.

    Two faults prove it, both observed: rename
    `_ApplicationListener.device_removed` to `device_remove` (the listener
    then goes dead and a removed device stays in the pairing tab forever),
    and delete `device_reinterviewed` (the gap this test was written for -
    it would have caught that one for free)."""
    import zigpy.application

    emitted = set(re.findall(r'listener_event\("(\w+)"', inspect.getsource(zigpy.application)))
    assert emitted >= {
        "connection_lost",
        "device_initialized",
        "device_joined",
        "device_left",
        "device_reinterviewed",
        "device_removed",
        "raw_device_initialized",
    }, sorted(emitted)

    listened = {
        name
        for name in vars(_ApplicationListener)
        if not name.startswith("_") and callable(vars(_ApplicationListener)[name])
    }
    assert listened == {
        "connection_lost",
        "device_joined",
        "raw_device_initialized",
        "device_initialized",
        "device_reinterviewed",
        "device_removed",
    }
    # Every one of them is a name zigpy really emits. This is the assertion
    # that catches a typo; the set above only catches a deletion.
    assert listened <= emitted, sorted(listened - emitted)

    # `device_left` is emitted and deliberately not listened to: zigpy
    # announces a leave before it knows whether the device is gone for good,
    # and `device_removed` is the one that follows `app.remove()`.
    assert "device_left" in emitted - listened


def test_the_join_window_bound_is_the_one_zigpy_asserts() -> None:
    """`ControllerApplication.permit` opens with `assert 0 <= time_s <= 254`.
    A source that allowed more would turn a user's request into an
    `AssertionError` from inside the library - which, under `python -O`, is
    no check at all and an out-of-range broadcast instead.

    Fault to prove it: set `PERMIT_MAX_SECONDS` to 255."""
    import zigpy.application

    body = inspect.getsource(zigpy.application.ControllerApplication.permit)
    assert f"<= time_s <= {PERMIT_MAX_SECONDS}" in body, body.splitlines()[:4]


def _real_zigpy_device(ieee: str = "00:12:4b:00:1c:a1:b2:c3"):
    """A genuine `zigpy.device.Device` with one endpoint and two real
    clusters, built without a radio.

    `Device.__init__` and `Endpoint.add_input_cluster` reach back into the
    application for exactly two things - `register_callback_listener` and
    `_dblistener` - so the stub below is the whole of what a device needs to
    exist. Everything else on the object is the library's own."""
    import zigpy.device
    import zigpy.endpoint
    import zigpy.types
    import zigpy.zdo.types

    class _MinimalApplication:
        _dblistener = None

        def register_callback_listener(self, *args: object, **kwargs: object) -> int:
            return 0

    device = zigpy.device.Device(_MinimalApplication(), zigpy.types.EUI64.convert(ieee), 0x1234)
    endpoint = zigpy.endpoint.Endpoint(device, 1)
    device.endpoints[1] = endpoint
    endpoint.profile_id = 0x0104
    endpoint.device_type = 0x0100
    endpoint.add_input_cluster(0x0006)  # OnOff
    endpoint.add_input_cluster(0x0402)  # TemperatureMeasurement
    device.node_desc = zigpy.zdo.types.NodeDescriptor(
        logical_type=zigpy.zdo.types.LogicalType.Router, mac_capability_flags=0x8E
    )
    return device


def test_the_snapshot_is_built_from_a_real_zigpy_device_without_a_radio() -> None:
    """`_facts` and `_endpoint_facts` read eight names off zigpy objects,
    every one of them `Any` to mypy - the radio libraries ship no `py.typed` -
    so nothing but this test stands between a renamed attribute and an
    `AttributeError` that appears on hardware and nowhere else.

    Asserted by RUNNING the real methods against a real `zigpy.device.Device`
    rather than by checking that names exist: `hasattr` passes on a property
    that raises, and a source-text search passes on a docstring. This fails
    the way production would, with the same `AttributeError`.

    Fault to prove it: rename any of `ieee`, `manufacturer`, `model`,
    `node_desc.is_mains_powered`, `non_zdo_endpoints`, `endpoint_id`,
    `profile_id`, `device_type` or `in_clusters` in the source. The
    fake-driven suite stays green, because `fakes.py` gets renamed with it.

    `is_mains_powered` is the one worth naming twice: `_facts` reads a
    missing node descriptor as "battery powered", so a rename would quietly
    give every mains device a sleeping device's six hours of patience before
    Task 8 declares it dead."""
    device = _real_zigpy_device()
    source = ZigbeeSource(
        path="/dev/ttyUSB0",
        fingerprint=Fingerprint(
            name="SONOFF ZBDongle-E V2",
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
        ),
        database="/data/zigbee.sqlite",
    )

    facts = source._facts(device)

    assert facts.ieee == "00:12:4b:00:1c:a1:b2:c3"
    assert facts.manufacturer == ""  # `device.manufacturer` is None before the interview
    assert facts.model == ""
    assert facts.is_mains_powered is True
    assert facts.quirk_applied is False
    # Endpoint 0 is the ZDO and carries no ZCL clusters; `non_zdo_endpoints`
    # is what keeps it out.
    assert sorted(device.endpoints) == [0, 1]
    assert [endpoint.endpoint for endpoint in facts.endpoints] == [1]
    only = facts.endpoints[0]
    assert only.profile_id == 0x0104
    assert only.device_type == 0x0100
    assert only.in_cluster_ids == frozenset({0x0006, 0x0402})
    # Nothing has reported yet, so every declared attribute reads as `None`
    # and none of them becomes a path. The point is that getting there took
    # no exception - `_endpoint_facts` walked two real clusters' real
    # `attributes` dicts and called the real `Cluster.get` on each.
    assert only.attributes == {}


def test_the_application_devices_mapping_is_keyed_the_way_the_source_reads_it() -> None:
    """`_devices()` iterates `app.devices.values()` and `_device_or_none`
    compares `str(device.ieee)` against the store's text form, deliberately
    rather than building an `EUI64` on the command path.

    That only works if `EUI64.__str__` produces the colon-separated lower-case
    form the store writes. It does - asserted here rather than assumed,
    because a change would make every command say "unknown Zigbee device"
    while the catalogue looked complete.

    Fault to prove it: compare `device.ieee` to the address without `str()`."""
    import zigpy.application
    import zigpy.types

    assert str(zigpy.types.EUI64.convert("00:12:4b:00:1c:a1:b2:c3")) == ("00:12:4b:00:1c:a1:b2:c3")
    devices = inspect.signature(zigpy.application.ControllerApplication.get_device).parameters
    assert "ieee" in devices
    assert "devices" in inspect.getsource(zigpy.application.ControllerApplication.__init__)
    assert "app.devices.values()" in inspect.getsource(source_module.ZigbeeSource._devices)


@pytest.mark.parametrize(
    ("cluster_id", "attribute_id", "type_name", "sentinel"),
    [
        (0x0402, 0x0000, "int16s", -0x8000),
        (0x0201, 0x0000, "int16s", -0x8000),
        (0x0201, 0x0001, "int16s", -0x8000),
        (0x0403, 0x0000, "int16s", -0x8000),
        (0x0404, 0x0000, "uint16_t", 0xFFFF),
        (0x0408, 0x0000, "uint16_t", 0xFFFF),
        (0x0405, 0x0000, "uint16_t", 0xFFFF),
        (0x0400, 0x0000, "uint16_t", 0xFFFF),
        (0x0001, 0x0021, "uint8_t", 0xFF),
        (0x0001, 0x0020, "uint8_t", 0xFF),
    ],
)
def test_every_sentinel_is_a_value_its_attributes_declared_type_can_hold(
    cluster_id: int, attribute_id: int, type_name: str, sentinel: int
) -> None:
    """The trap that has now been walked into twice on this branch: a
    sentinel written as the ZCL's BIT PATTERN for an attribute whose declared
    type is signed. `int16s(0x8000)` raises, so such a row can never match
    any value zigpy produces - the row is dead, the test is green, and a
    sensor with no reading publishes -327.68 °C into the house.

    Reading the type off the installed library, per row, is what turns "we
    checked" into something that stays checked. `Thermostat.local_temperature`
    is the row this test was written for: same type, same sentinel, same
    cluster and attribute number in Matter - and a TRV is a device this
    project targets.

    Fault to prove it: write `0x8000` for any of the `int16s` rows."""
    from zigpy.zcl import Cluster

    cluster_class = Cluster._registry[cluster_id]
    definition = cluster_class.attributes[attribute_id]
    assert definition.type.__name__ == type_name, definition
    # The sentinel must round-trip through the declared type unchanged. A
    # bit pattern for a signed type does not: it raises here.
    assert definition.type(sentinel) == sentinel
    assert _SENTINELS[(cluster_id, attribute_id)] == sentinel


async def test_the_radio_library_is_imported_off_the_event_loop() -> None:
    """`import bellows.zigbee.application` costs 0.44 s on an M1 - measured,
    on the machine this was written on - so roughly 2-3 s on a Pi 4, and it
    sits on the reconnect path: the first time a radio is configured from the
    web UI, the loop would stop answering `/health` for that long.

    The same reason `quirks.py` gives for the registry warm-up, and the same
    remedy: the default executor.

    Asserted by watching WHICH THREAD the import runs on, not by searching
    the source for `run_in_executor` - a docstring satisfies a text search,
    and this file has already been bitten by exactly that (the F36 note in
    the Task 7 report).

    Fault to prove it: call `_radio_module(...)` directly again."""
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []

    class _StubApplication:
        @classmethod
        async def new(cls, config, *, start_radio, device_resolver):
            return ("built", config, start_radio, device_resolver)

    class _StubModule:
        ControllerApplication = _StubApplication

    def fake_radio_module(radio_type: str) -> object:
        seen.append(threading.get_ident())
        return _StubModule

    real = source_module._radio_module
    source_module._radio_module = fake_radio_module  # type: ignore[assignment]
    try:
        built = await source_module._default_application(
            {"_radio_type": "ezsp", "database_path": "/tmp/x"}
        )
    finally:
        source_module._radio_module = real  # type: ignore[assignment]

    assert seen, "the radio module was never imported"
    assert seen[0] != loop_thread, "the radio library was imported ON the event loop"
    # And it really did build the application from the config, minus this
    # module's own private key.
    assert built[0] == "built"
    assert built[1] == {"database_path": "/tmp/x"}
    assert built[2] is False
    # The loop is still the loop afterwards.
    assert asyncio.get_running_loop() is not None


def test_the_command_schemas_carry_the_field_names_the_source_renames_to() -> None:
    """`rename_payload` is handed the field names of the command it is
    about to send, read straight off `server_commands[id].schema.fields`.
    Two of them do not follow the mechanical camelCase rule, and
    `move_to_level_with_on_off` declares no options fields at all.

    Fault to prove it: expect `color_temperature_mireds`. Colour temperature
    then fails on every lamp."""
    import zigpy.zcl.clusters.general
    import zigpy.zcl.clusters.lighting

    def field_names(cluster: object, command_id: int) -> list[str]:
        command = cluster.server_commands[command_id]  # type: ignore[attr-defined]
        return [field.name for field in command.schema.fields]

    colour = zigpy.zcl.clusters.lighting.Color
    assert field_names(colour, 10)[0] == "color_temp_mireds"
    assert field_names(colour, 7)[:2] == ["color_x", "color_y"]
    assert field_names(colour, 6)[:2] == ["hue", "saturation"]
    level = zigpy.zcl.clusters.general.LevelControl
    assert field_names(level, 4) == ["level", "transition_time"]
    assert field_names(zigpy.zcl.clusters.general.OnOff, 1) == []


def test_a_zcl_status_of_zero_is_success() -> None:
    """What the source compares a command's answer against before calling
    it delivered.

    Fault to prove it: compare against 1."""
    import zigpy.zcl.foundation

    assert int(zigpy.zcl.foundation.Status.SUCCESS) == source_module._ZCL_SUCCESS


@pytest.mark.parametrize("radio_type", ["ezsp", "znp", "deconz"])
def test_every_fingerprinted_radio_type_has_an_application_to_open_it(radio_type) -> None:
    """`radios/fingerprints.py` can answer with three radio types, and each
    must map to a library that is actually installed - none of them is a
    direct dependency of loxmatter, they all arrive through `zha`.

    Fault to prove it: point one row of `_RADIO_MODULES` at a module that
    does not exist. A user with that stick then gets an `ImportError`
    instead of a radio, and only on their hardware."""
    assert radio_type in _RADIO_MODULES
    module = source_module._radio_module(radio_type)
    assert hasattr(module, "ControllerApplication")


def test_the_quirk_resolver_the_source_uses_exists_and_marks_what_it_applied() -> None:
    """The design names `DEVICE_REGISTRY.resolve`. In the installed
    zha-quirks that object is the LEGACY v1 registry and has no `resolve` at
    all; the unified one is `ZHA_DEVICE_REGISTRY`, and it is what marks a
    transformed device with `_quirk_registry_entry` - the attribute both the
    snapshot's `quirk_applied` and the pairing tab's hint read.

    Fault to prove it: use `DEVICE_REGISTRY.resolve`, as the design says.
    Every device then loads without its quirk.

    (Importing zhaquirks costs about 0.4 s here. That is the import alone -
    `zhaquirks.setup()`, the 2-3 s one, is not called.)"""
    import zhaquirks
    from zha.quirks import QUIRK_REGISTRY_ENTRY_ATTR

    assert QUIRK_REGISTRY_ENTRY_ATTR == "_quirk_registry_entry"
    assert callable(zhaquirks.ZHA_DEVICE_REGISTRY.resolve)
    assert not hasattr(zhaquirks.DEVICE_REGISTRY, "resolve")
    # The body, not the docstring, which names both registries.
    built = inspect.getsource(source_module._default_application).split('"""')[-1]
    assert "ZHA_DEVICE_REGISTRY.resolve" in built
    assert "_quirk_registry_entry" in inspect.getsource(source_module.ZigbeeSource._facts), (
        "the snapshot must read the attribute the resolver actually sets"
    )


# ------------------------------------------------------ configure-on-join --
# Everything below belongs to `zigbee/configure.py`, which spells zigpy's
# numbers out as constants for the same reason `source.py` does - no zigpy
# import at module import time - and is therefore exposed to exactly the
# same class of silently wrong name.


def test_the_ias_zone_numbers_are_the_ones_the_installed_library_declares() -> None:
    """The task's make-or-break names. Every one of them is a bare integer
    in `configure.py`, and a wrong one fails INVISIBLY: an enrolment written
    to the wrong attribute is a write the device answers with a status
    nobody reads, and a sensor that is not enrolled sends no alarms at all
    while pairing, configuring and going green.

    Fault to prove it: set `IAS_CIE_ADDRESS_ATTRIBUTE` to 0x0011 (`zone_id`,
    the neighbouring attribute). Nothing in the fake-driven suite notices,
    because the fake would be given the same number."""
    from zigpy.zcl.clusters.security import IasZone

    assert IasZone.cluster_id == IAS_ZONE_CLUSTER
    assert IasZone.attributes[IAS_ZONE_TYPE_ATTRIBUTE].name == "zone_type"
    assert IasZone.attributes[IAS_ZONE_STATUS_ATTRIBUTE].name == "zone_status"
    assert IasZone.attributes[IAS_CIE_ADDRESS_ATTRIBUTE].name == "cie_addr"

    # The answer, a SERVER command, with the field names the routine passes
    # by keyword.
    enroll_response = IasZone.server_commands[IAS_ENROLL_RESPONSE_COMMAND]
    assert enroll_response.name == "enroll_response"
    assert [field.name for field in enroll_response.schema.fields] == [
        "enroll_response_code",
        "zone_id",
    ]
    assert int(IasZone.EnrollResponse.Success) == IAS_ENROLL_SUCCESS

    # The two CLIENT commands: an alarm and a request to be enrolled. These
    # are the ids that decide whether a sensor ever triggers.
    notification = IasZone.client_commands[IAS_STATUS_CHANGE_NOTIFICATION_COMMAND]
    assert notification.name == "status_change_notification"
    # `args[0]` is only `zone_status` because it is the FIRST field.
    assert next(field.name for field in notification.schema.fields) == "zone_status"
    assert IasZone.client_commands[IAS_ENROLL_REQUEST_COMMAND].name == "enroll"
    assert IAS_STATUS_CHANGE_NOTIFICATION_COMMAND != IAS_ENROLL_REQUEST_COMMAND


def test_a_client_command_is_dispatched_as_cluster_command_and_not_as_an_attribute_event() -> None:
    """THE design trap this task exists to avoid: an IAS alarm never appears
    among the four attribute events `source.py` subscribes to. It arrives
    through `ListenableMixin`, by the method name `cluster_command`, on a
    listener registered with `Cluster.add_listener` - a completely separate
    mechanism from the `EventBase.on_event` one.

    A bridge that only subscribes to reports sees a sensor that works
    perfectly and never triggers.

    Fault to prove it: rename `_IasZoneListener.cluster_command`. zigpy
    swallows the miss without a word."""
    import zigpy.zcl

    emitted = set(
        re.findall(
            r'listener_event\(\s*"(\w+)"', inspect.getsource(zigpy.zcl.Cluster.handle_message)
        )
    )
    assert "cluster_command" in emitted
    assert not [name for name in ATTRIBUTE_EVENTS if name in emitted]
    # The signature the handler is called with: `(tsn, command_id, args)`.
    handler = inspect.signature(configure_module._IasZoneListener.cluster_command).parameters
    assert list(handler) == ["self", "tsn", "command_id", "args"]
    body = inspect.getsource(zigpy.zcl.Cluster.handle_message)
    assert 'listener_event("cluster_command", hdr.tsn, hdr.command_id, args)' in body
    # And the listener really is registered the ListenableMixin way.
    assert "add_listener" in inspect.getsource(configure_module._install_ias_handlers)


def test_the_wake_up_events_are_names_zigpy_really_emits() -> None:
    """A deferred cluster is retried when the device is next heard from, and
    "heard from" is two zigpy events: `device_last_seen_updated`, which
    EVERY incoming packet fires, and PollControl's `checkin`.

    Fault to prove it: rename `_WakeUpWatcher.device_last_seen_updated` to
    `device_last_seen`. The sensor's battery reporting is then never
    configured, and nothing anywhere says so."""
    import zigpy.device
    from zigpy.zcl.clusters.general import PollControl

    emitted = set(re.findall(r'listener_event\(\s*"(\w+)"', inspect.getsource(zigpy.device)))
    assert "device_last_seen_updated" in emitted
    listened = {
        name
        for name in vars(configure_module._WakeUpWatcher)
        if not name.startswith("_") and callable(vars(configure_module._WakeUpWatcher)[name])
    }
    assert listened == {"device_last_seen_updated", "cluster_command"}
    assert "device_last_seen_updated" in emitted

    assert PollControl.cluster_id == POLL_CONTROL_CLUSTER
    assert PollControl.client_commands[POLL_CONTROL_CHECKIN_COMMAND].name == "checkin"


def test_the_device_calls_configure_on_join_makes_exist_and_are_shaped_as_it_assumes() -> None:
    """`fast_poll_mode` is an ASYNC CONTEXT MANAGER, not a coroutine - which
    is the only reason it can bracket the whole routine - and
    `request_priority` is one too. `apply_custom_configuration` exists ONLY
    on a quirked device, which is why the routine tests for it rather than
    calling it blind.

    Fault to prove it: `await device.fast_poll_mode()` instead of
    `async with`. Every configuration pass then raises before it binds
    anything."""
    import zigpy.application
    import zigpy.device
    import zigpy.state
    import zigpy.types

    assert inspect.isasyncgenfunction(zigpy.device.Device.fast_poll_mode.__wrapped__)
    assert inspect.isasyncgenfunction(
        zigpy.application.ControllerApplication.request_priority.__wrapped__
    )
    assert isinstance(zigpy.device.Device.skip_configuration, property)
    # The base device has no quirk hook at all.
    assert not hasattr(zigpy.device.Device, "apply_custom_configuration")
    from zhaquirks.device import CustomZigpyDevice

    assert hasattr(CustomZigpyDevice, "apply_custom_configuration")

    assert int(zigpy.types.PacketPriority.HIGH) == PACKET_PRIORITY_HIGH
    # The coordinator's own address, which the CIE write hands the sensor.
    assert "ieee" in zigpy.state.NodeInfo.__dataclass_fields__
    assert "node_info" in zigpy.state.State.__dataclass_fields__


def test_reporting_is_configured_the_way_zigpy_2_2_0_takes_it() -> None:
    """`configure_reporting_multiple` is keyed by the attribute DEFINITION
    and takes a `ReportingConfig`, and it answers with a status PER
    ATTRIBUTE. All three matter: a bare id would raise for the 28 quirks
    that declare an id twice, a tuple would raise outright, and a single
    overall status would lose the lamp that accepts on/off and refuses
    colour temperature - the case the polling fallback exists for.

    Fault to prove it: pass `{attribute_id: (min, max, change)}`."""
    import zigpy.zcl
    from zigpy.zcl.helpers import ReportingConfig

    parameters = inspect.signature(zigpy.zcl.Cluster.configure_reporting_multiple).parameters
    assert "config" in parameters
    assert list(ReportingConfig.__dataclass_fields__) == [
        "min_interval",
        "max_interval",
        "reportable_change",
    ]
    body = inspect.getsource(zigpy.zcl.Cluster.configure_reporting_multiple)
    assert "attr_def.id" in body, "the key really is the definition"
    assert callable(zigpy.zcl.Cluster.bind)
    assert callable(zigpy.zcl.Cluster.update_attribute)
    # `allow_cache` is a real parameter and defaults to False, which is what
    # the final read relies on.
    read = inspect.signature(zigpy.zcl.Cluster.read_attributes).parameters
    assert read["allow_cache"].default is False


@pytest.mark.parametrize(("cluster_id", "attribute_id"), sorted(REPORTING))
def test_every_reporting_row_names_an_attribute_that_exists(cluster_id, attribute_id) -> None:
    """A row for an attribute the cluster does not declare would be dropped
    silently by `_wanted_reporting` - the device would simply never report
    that value, and nothing would say why.

    Fault to prove it: add `(0x0006, 0x0001)` to `REPORTING`. OnOff has no
    attribute 0x0001."""
    from zigpy.zcl import Cluster

    cluster_class = Cluster._registry[cluster_id]
    assert attribute_id in cluster_class.attributes, (
        f"{cluster_class.__name__} declares no attribute {attribute_id:#06x}"
    )


def test_the_static_reads_and_the_identify_blink_name_real_attributes() -> None:
    """`zone_type` decides the Matter target cluster and the polarity, the
    three Color attributes are what the colour picker and the temperature
    slider hang off, and `power_source` is Basic's own answer.

    Fault to prove it: read 0x400D for `color_temp_physical_max`."""
    from zigpy.zcl import Cluster
    from zigpy.zcl.clusters.general import Identify

    expected = {
        0x0000: {0x0007: "power_source"},
        0x0300: {
            0x400A: "color_capabilities",
            0x400B: "color_temp_physical_min",
            0x400C: "color_temp_physical_max",
        },
        0x0500: {0x0001: "zone_type"},
    }
    for cluster_id, attribute_ids in configure_module.STATIC_READS.items():
        cluster_class = Cluster._registry[cluster_id]
        for attribute_id in attribute_ids:
            assert cluster_class.attributes[attribute_id].name == expected[cluster_id][attribute_id]

    assert Identify.cluster_id == IDENTIFY_CLUSTER
    identify = Identify.server_commands[IDENTIFY_COMMAND]
    assert identify.name == "identify"
    assert [field.name for field in identify.schema.fields] == ["identify_time"]
