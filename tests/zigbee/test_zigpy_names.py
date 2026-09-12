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

"""Every zigpy name `zigbee/source.py` depends on, checked against the
installed library.

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

import pytest

from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.source import (
    _RADIO_MODULES,
    _STARTUP_MESSAGES,
    _UNREACHABLE_EXCEPTION_NAMES,
    ATTRIBUTE_EVENTS,
    ZigbeeSource,
)


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
