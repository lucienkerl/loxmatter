"""Checking for firmware updates (design 2026-09-30, section 6.1)."""

import asyncio
from dataclasses import replace

from firmware_fakes import (
    BILRESA_OFFER,
    KAJPLATS_OFFER,
    FakeFirmwareSource,
    idle_facts,
    register,
)

from loxmatter import i18n
from loxmatter.firmware.check import FirmwareChecker
from loxmatter.model.store import Store


def _setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    button_id, button = register(store, "ikea_bilresa_button.json")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    checker = FirmwareChecker(store, lambda: source, now=lambda: "2026-09-30T06:00:00+00:00")
    return store, source, checker, (lamp_id, lamp), (button_id, button)


async def test_check_all_stores_every_answer(tmp_path):
    store, source, checker, (lamp_id, lamp), (button_id, button) = _setup(tmp_path)
    source.offers[lamp] = KAJPLATS_OFFER
    source.offers[button] = None

    await checker.check_all()

    assert store.firmware_status.get(lamp_id).offer == KAJPLATS_OFFER
    assert store.firmware_status.get(button_id).offer is None
    assert store.firmware_status.get(button_id).checked_at == "2026-09-30T06:00:00+00:00"
    assert checker.progress.running is False
    assert (checker.progress.checked, checker.progress.total) == (2, 2)


async def test_a_device_without_requestor_is_not_asked(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    source.facts[lamp] = replace(idle_facts(16842752, "1.1.0"), has_requestor=False)
    await checker.check_all()
    assert lamp not in source.checked
    assert store.firmware_status.get(lamp_id) is None


async def test_an_offline_device_is_skipped_and_keeps_its_state(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "yesterday")
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    await checker.check_all()
    assert lamp not in source.checked
    assert store.firmware_status.get(lamp_id).checked_at == "yesterday"


async def test_an_offer_the_device_already_has_is_not_stored(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    source.facts[lamp] = idle_facts(16908288, "1.2.0")
    source.offers[lamp] = KAJPLATS_OFFER
    await checker.check_all()
    assert store.firmware_status.get(lamp_id).offer is None


async def test_a_failed_check_keeps_the_previous_offer(tmp_path):
    store, source, checker, (lamp_id, _), _ = _setup(tmp_path)
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "yesterday")
    source.fail_check = RuntimeError("DCL unreachable")
    await checker.check_all()
    status = store.firmware_status.get(lamp_id)
    assert status.offer == KAJPLATS_OFFER
    assert status.check_error == "DCL unreachable"


async def test_a_hanging_device_times_out(tmp_path):
    store, source, _, (lamp_id, _), _ = _setup(tmp_path)
    source.hang_check = True
    checker = FirmwareChecker(store, lambda: source, now=lambda: "t", per_device_timeout=0.01)
    await checker.check_all()
    assert store.firmware_status.get(lamp_id).check_error == i18n.t("api.firmware.check_timeout")


async def test_a_second_start_joins_the_running_check(tmp_path):
    _, source, checker, _, _ = _setup(tmp_path)
    source.hang_check = True
    first = checker.start_all()
    second = checker.start_all()
    assert first.running and second.running
    await asyncio.sleep(0)
    assert len(source.checked) == 1  # only one run is asking
    checker.cancel()


async def test_check_one_asks_only_that_device(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    source.offers[lamp] = KAJPLATS_OFFER
    await checker.check_one(lamp_id)
    assert source.checked == [lamp]
    assert store.firmware_status.get(lamp_id).offer == KAJPLATS_OFFER


async def test_nothing_happens_without_a_supported_source(tmp_path):
    store, source, checker, _, _ = _setup(tmp_path)
    source.supported = False
    await checker.check_all()
    assert source.checked == []
    assert store.firmware_status.all() == {}


async def test_the_bilresa_offer_is_stored_with_its_window(tmp_path):
    store, source, checker, _, (button_id, button) = _setup(tmp_path)
    source.offers[button] = BILRESA_OFFER
    await checker.check_all()
    assert store.firmware_status.get(button_id).offer.min_applicable == 17301509
