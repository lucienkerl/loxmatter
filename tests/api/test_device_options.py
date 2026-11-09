"""A device's configuration values end to end (design 2026-11-08): from
the snapshot a Zigbee source builds, through the store, to the web UI's
dialog, `POST /api/commands/{key}` and Loxone's `/cmd`.

The device is the HOBEIAN ZG-204Z as `zigbee/translate.py` sees it, with
the two options loxmatter's own quirk describes - built from facts, so this
file needs neither zigpy nor zhaquirks (`tests/zigbee/test_options.py`
covers the quirk itself)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, settle_until

from loxmatter.device_config import ConfigOption, choice, option_command_id
from loxmatter.export.commands import extract_commands
from loxmatter.export.outputs import to_outputs
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import DeviceCall
from loxmatter.zigbee.translate import DeviceFacts, EndpointFacts, build_snapshot

IEEE = "a4:c1:38:a6:0e:d0:31:74"
IAS_ZONE = 0x0500

KEEP_TIME = ConfigOption(
    endpoint=1,
    cluster_id=IAS_ZONE,
    attribute_id=0xF001,
    name="keep_time",
    kind="enum",
    fallback_title="Keep time",
    translation_key="keep_time",
    choices=(choice(0, "_30_seconds"), choice(1, "_60_seconds"), choice(2, "_120_seconds")),
)
SENSITIVITY = ConfigOption(
    endpoint=1,
    cluster_id=IAS_ZONE,
    attribute_id=0x0013,
    name="current_zone_sensitivity_level",
    kind="enum",
    fallback_title="Sensitivity",
    translation_key="sensitivity",
    choices=(choice(0, "Low"), choice(1, "Medium"), choice(2, "High")),
)


def _snapshot(keep_time_raw: int | None = 1):
    attributes: dict[tuple[int, int], object] = {
        (IAS_ZONE, 0x0001): 13,
        (IAS_ZONE, 0x0002): 0,
        (0x0001, 0x0021): 200,
    }
    if keep_time_raw is not None:
        attributes[(IAS_ZONE, 0xF001)] = keep_time_raw
    return build_snapshot(
        DeviceFacts(
            ieee=IEEE,
            manufacturer="HOBEIAN",
            model="ZG-204Z",
            is_mains_powered=False,
            available=True,
            quirk_applied=True,
            endpoints=(
                EndpointFacts(
                    endpoint=1,
                    profile_id=260,
                    device_type=0x0402,
                    in_cluster_ids=frozenset({0, 1, 3, IAS_ZONE, 0xEF00}),
                    attributes=attributes,
                ),
            ),
            config_options=(KEEP_TIME, SENSITIVITY),
        )
    )


def test_the_snapshot_reports_the_keep_time_in_seconds():
    """On IAS Zone, whose attributes otherwise fold into one occupancy."""
    snapshot = _snapshot(keep_time_raw=1)
    assert snapshot.attributes["1/1280/61441"] == 60.0
    assert "1/1280/19" not in snapshot.attributes  # never reported: left out
    assert snapshot.config_options == (KEEP_TIME, SENSITIVITY)


@pytest.fixture
def registered(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    snapshot = _snapshot()
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    yield store, device_id
    store.close()


def test_each_option_is_an_output_that_is_offered_but_not_preselected(registered):
    store, device_id = registered
    commands = {c.slug: c for c in store.commands(device_id)}

    keep_time = commands["set_keep_time"]
    assert keep_time.command_id == option_command_id(0xF001)
    assert keep_time.option == KEEP_TIME
    assert keep_time.takes_value
    assert not keep_time.exported
    assert not keep_time.functional
    assert to_outputs(store.commands(device_id)) == []

    store.set_command_exported(keep_time.key, True)
    (output,) = to_outputs(store.commands(device_id))
    assert output.path == f"/cmd/{keep_time.key}/<v>"
    assert output.analog


def test_the_reading_is_named_by_the_option_and_not_preselected(registered):
    store, device_id = registered
    signal = next(s for s in store.signals(device_id) if s.ref.element_id == 0xF001)
    assert signal.key == f"d{device_id}_1_keep_time"
    assert not signal.exported
    assert not signal.functional


def test_a_changed_option_reaches_the_stored_row(registered):
    """A zha-quirks update that adds a choice must reach a device paired
    before it, as a `clusters.yaml` fix reaches a stored command."""
    store, device_id = registered
    wider = ConfigOption(
        **{**KEEP_TIME.__dict__, "choices": (*KEEP_TIME.choices, choice(3, "_240_seconds"))}
    )
    snapshot = _snapshot()
    snapshot = type(snapshot)(**{**snapshot.__dict__, "config_options": (wider, SENSITIVITY)})
    store.register_commands(device_id, extract_commands(snapshot))
    row = next(c for c in store.commands(device_id) if c.slug == "set_keep_time")
    assert row.option == wider


def test_an_option_never_becomes_a_group_command(registered):
    store, device_id = registered
    second = _snapshot()
    second = type(second)(
        **{**second.__dict__, "address": "a4:c1:38:66:5a:f5:49:41", "unique_id": "x"}
    )
    other = store.register_device(second)
    store.register_commands(other, extract_commands(second))
    group = store.create_group("Motion", [device_id, other])
    assert all(c.command_id >= 0 for c in store.group_commands(group.id))


# ------------------------------------------------------------------ API --


@pytest.fixture
async def api(
    registered, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int, list[DeviceCall], object]]:
    store, device_id = registered
    calls: list[DeviceCall] = []

    async def invoke(call: DeviceCall) -> None:
        calls.append(call)

    runtime = fake_runtime(store)
    app = build_app(store, invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id, calls, runtime


async def test_the_dialog_lists_both_options_with_names_and_choices(api):
    client, _store, device_id, _calls, runtime = api
    runtime.seed(f"d{device_id}_1_keep_time", 60.0)

    response = await client.get(f"/api/devices/{device_id}/options")

    assert response.status_code == 200
    options = {o["name"]: o for o in response.json()["options"]}
    keep_time = options["keep_time"]
    assert keep_time["title"] == "Keep time"
    assert [c["value"] for c in keep_time["choices"]] == [30, 60, 120]
    assert [c["label"] for c in keep_time["choices"]] == ["30 s", "60 s", "120 s"]
    assert keep_time["value"] == 60
    assert keep_time["pending"] is None
    assert keep_time["exported"] is False
    sensitivity = options["current_zone_sensitivity_level"]
    assert sensitivity["title"] == "Sensitivity"
    assert [c["label"] for c in sensitivity["choices"]] == ["Low", "Medium", "High"]
    assert sensitivity["value"] is None


async def test_the_tile_knows_the_device_has_a_configuration(api):
    client, _store, device_id, _calls, _runtime = api
    response = await client.get("/api/devices")
    device = next(d for d in response.json() if d["id"] == device_id)
    assert device["config_options"] == 2


async def test_setting_a_value_sends_the_raw_value_as_a_write(api):
    client, store, device_id, calls, _runtime = api
    key = next(c.key for c in store.commands(device_id) if c.slug == "set_keep_time")

    response = await client.post(f"/api/commands/{key}", json={"value": "120"})

    assert response.status_code == 200
    (call,) = calls
    assert (call.address, call.endpoint, call.cluster_id) == (IEEE, 1, IAS_ZONE)
    assert call.command_id == option_command_id(0xF001)
    assert call.payload == {"value": 2}


async def test_a_value_the_option_cannot_take_is_refused_before_anything_is_sent(api):
    client, store, device_id, calls, _runtime = api
    key = next(c.key for c in store.commands(device_id) if c.slug == "set_keep_time")

    response = await client.post(f"/api/commands/{key}", json={"value": "45"})

    assert response.status_code == 400
    assert "30, 60, 120" in response.json()["detail"]
    assert calls == []


async def test_loxone_sets_the_keep_time_in_seconds(api):
    client, store, device_id, calls, _runtime = api
    key = next(c.key for c in store.commands(device_id) if c.slug == "set_keep_time")

    response = await client.get(f"/cmd/{key}/30")

    assert response.status_code == 200
    await settle_until(lambda: calls, "the queued write")
    assert calls[0].payload == {"value": 0}
    assert (await client.get(f"/cmd/{key}/31")).status_code == 400


async def test_the_control_dialog_does_not_offer_an_option(api):
    client, _store, device_id, _calls, _runtime = api
    response = await client.get(f"/api/devices/{device_id}/controls")
    body = response.json()
    assert body["commands"] == []
    assert body["hidden_raw_commands"] == 0


async def test_a_german_user_reads_the_names_in_german(api):
    """The quirk's `translation_key` looks up `config.option.*`; a choice
    without a number reads through `config.choice.*`."""
    from loxmatter import i18n

    client, store, device_id, _calls, _runtime = api
    store.locale.set_language("de")
    try:
        response = await client.get(f"/api/devices/{device_id}/options")
    finally:
        store.locale.set_language("en")
        i18n.set_language("en")
    options = {o["name"]: o for o in response.json()["options"]}
    assert options["keep_time"]["title"] == "Haltezeit"
    sensitivity = options["current_zone_sensitivity_level"]
    assert sensitivity["title"] == "Empfindlichkeit"
    assert [c["label"] for c in sensitivity["choices"]] == ["Niedrig", "Mittel", "Hoch"]
