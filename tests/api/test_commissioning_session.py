"""The commissioning session: cards, codes, the worker and the naming line
(design 2026-10-02, sections 4, 8 and 9)."""

import asyncio
import json
import logging

import pytest
from commissioning_fakes import (
    BILRESA,
    KAJPLATS_E14,
    KAJPLATS_E27,
    MANUAL_CODE,
    QR_CODE,
    QR_CODE_ON_NETWORK,
    HeldLabels,
    advert,
    build_session,
    snapshot_of,
)
from conftest import settle_until

from loxmatter import i18n
from loxmatter.commissioning.session import CodeRejected
from loxmatter.matter.client import CommissioningError
from loxmatter.radios.bluetooth_health import KernelFinding
from loxmatter.radios.bluez import BluezScanError


@pytest.fixture
async def h(tmp_path, fake_client, fake_runtime, fake_otbr):
    harness = build_session(tmp_path, fake_client, fake_runtime, fake_otbr)
    yield harness
    harness.gate.release_all()
    await harness.session.aclose()


def card_view(h, card_id):
    return next(card for card in h.session.view()["cards"] if card["id"] == card_id)


async def idle(h):
    """Until the worker has nothing left to do."""
    await settle_until(
        lambda: not any(c["state"] in ("queued", "running") for c in h.session.view()["cards"]),
        "the worker finished its queue",
    )


async def ready_card(h, address, discriminator, pid, rssi, code, name="", room=None):
    """A found card from a scan with a code attached."""
    h.scanner.adverts.append(advert(address, discriminator, pid, rssi))
    assert await h.session.scan()
    card = await h.session.add_code(code, room)
    if name:
        await h.session.update_card(card.id, name=name)
    return card


async def test_scan_makes_cards_named_from_the_dcl(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E14, -70),
        advert("AA:02", 3000, KAJPLATS_E27, -50),
    ]
    assert await h.session.scan()
    # In card order; the dialog sorts them (`sortedCards()` in app.js).
    cards = h.session.view()["cards"]
    assert [c["state"] for c in cards] == ["found", "found"]
    assert {c["product"]: c["rssi"] for c in cards} == {
        "KAJPLATS E27 WS globe 1055lm": -50,
        "KAJPLATS E14 CWS globe 806lm": -70,
    }
    assert cards[1]["detail"] == "IKEA of Sweden · LED2407G8"
    assert h.scanner.scans == [10.0]


async def test_automatic_scan_is_skipped_within_60_seconds(h):
    assert await h.session.scan(automatic=True)
    h.clock.now = 30.0
    assert not await h.session.scan(automatic=True)
    assert len(h.scanner.scans) == 1
    h.clock.now = 61.0
    assert await h.session.scan(automatic=True)
    assert len(h.scanner.scans) == 2


async def test_scan_is_refused_while_commissioning(h):
    h.gate.held = True
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, card.id)["state"] == "running", "running")
    assert h.session.busy
    assert not await h.session.scan()
    assert h.scanner.scans == []
    assert h.session.view()["scan"]["state"] == "blocked"


async def test_qr_code_attaches_to_its_card(h):
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    found = h.session.view()["cards"][0]
    card = await h.session.add_code(QR_CODE, "Küche")
    assert card.id == found["id"]
    view = card_view(h, card.id)
    assert view["state"] == "ready"
    assert view["has_code"] is True
    assert view["room"] == "Küche"
    assert len(h.session.view()["cards"]) == 1


async def test_manual_code_with_several_matches_makes_its_own_card(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:02", 3841, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    card = await h.session.add_code(MANUAL_CODE, None)
    assert card.product == i18n.t("web.commissioning.numeric_code_device")
    assert card.note == i18n.t("web.commissioning.note_several", count=2)
    assert card.state == "ready"
    assert sorted(card.candidates) == ["AA:01", "AA:02"]
    others = [c for c in h.session.view()["cards"] if c["id"] != card.id]
    assert [(c["state"], c["has_code"]) for c in others] == [("found", False), ("found", False)]


async def test_no_match_is_not_nearby_with_the_hint(h):
    card = await h.session.add_code(QR_CODE, None)
    assert card.state == "not_nearby"
    assert card.pairing_hint
    assert card.pairing_hint in card.note
    assert card.note == i18n.t("web.commissioning.note_not_nearby", hint=card.pairing_hint)


async def test_on_network_only_code_is_ready_without_a_match(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    assert card.state == "ready"
    assert card.note == i18n.t("web.commissioning.note_wifi")
    assert card_view(h, card.id)["on_network"] is True


async def test_duplicate_and_unreadable_codes_are_rejected(h):
    await h.session.add_code(QR_CODE, None)
    with pytest.raises(CodeRejected) as duplicate:
        await h.session.add_code(f"  {QR_CODE} ", None)
    assert duplicate.value.detail == i18n.t("api.commissioning.fail_duplicate")
    assert duplicate.value.status == 409
    with pytest.raises(CodeRejected) as unreadable:
        await h.session.add_code("hello", None)
    assert unreadable.value.detail == i18n.t("api.commissioning.fail_unreadable_code")
    assert unreadable.value.status == 422
    with pytest.raises(CodeRejected) as typo:
        await h.session.add_code("34970112333", None)
    assert typo.value.detail == i18n.t("api.commissioning.fail_typo")
    assert typo.value.status == 422


async def test_the_code_never_appears_in_the_view(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:02", 3841, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    await h.session.add_code(QR_CODE, None)
    await h.session.add_code(MANUAL_CODE, None)
    text = json.dumps(h.session.view())
    assert "Y.K9042C00KA0648G00" not in text
    assert MANUAL_CODE not in text


async def test_worker_commissions_in_order_and_names_with_preset_name(h):
    first = await h.session.add_code(QR_CODE_ON_NETWORK, "Esszimmer")
    await h.session.update_card(first.id, name="Esstisch")
    h.scanner.adverts = [advert("AA:02", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    second = await h.session.add_code(QR_CODE, None)
    await h.session.start()
    await idle(h)
    assert h.client.commissioned == [QR_CODE_ON_NETWORK, QR_CODE]
    one = card_view(h, first.id)
    assert one["state"] == "done"
    device = h.store.device(one["device_id"])
    assert device.label == "Esstisch"
    assert device.room == "Esszimmer"
    two = card_view(h, second.id)
    assert two["state"] == "naming"
    # The check blink of the named device, then the front of the naming
    # line, which always takes the blink over (design 4.4).
    assert h.identify.starts() == [
        ("start", one["device_id"], False, 3),
        ("start", two["device_id"], True, 30),
    ]


async def test_unnamed_devices_queue_for_naming_and_only_the_first_blinks(h):
    a = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.adverts = [advert("AA:02", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    b = await h.session.add_code(QR_CODE, None)
    await h.session.start()
    await idle(h)
    view = h.session.view()
    assert [card_view(h, a.id)["state"], card_view(h, b.id)["state"]] == ["naming", "naming"]
    assert view["naming"] == [a.id, b.id]
    device_a = card_view(h, a.id)["device_id"]
    device_b = card_view(h, b.id)["device_id"]
    assert h.identify.starts() == [("start", device_a, True, 30)]
    assert view["blinking_card"] == a.id
    await h.session.update_card(a.id, name="Flurlampe")
    done = await h.session.confirm_name(a.id)
    assert done.state == "done"
    assert h.store.device(device_a).label == "Flurlampe"
    await settle_until(lambda: h.identify.blinking == device_b, "b blinks")
    assert h.identify.starts()[-1] == ("start", device_b, True, 30)
    assert h.session.view()["naming"] == [b.id]


async def test_confirm_without_a_name_is_refused_and_skip_keeps_the_default_label(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    device_id = card_view(h, card.id)["device_id"]
    label = h.store.device(device_id).label
    with pytest.raises(CodeRejected) as missing:
        await h.session.confirm_name(card.id)
    assert missing.value.detail == i18n.t("api.commissioning.fail_name_missing")
    assert missing.value.status == 422
    skipped = await h.session.skip_name(card.id)
    assert skipped.state == "done"
    assert h.store.device(device_id).label == label
    assert h.session.view()["naming"] == []
    await settle_until(lambda: h.identify.blinking is None, "the blink stops")


async def test_a_name_that_is_a_pairing_code_is_refused(h):
    """A scanner that types into a name field instead of the code field
    leaves a pairing code there; the session takes no code as a name, in
    `update_card` or in `confirm_name`, so it never reaches the store as a
    device's label. A typed-out manual code counts, with or without its
    dashes and spaces; a name that only contains digits does not.

    Fault to prove it: check only `confirm_name`, or only QR payloads."""
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    device_id = card_view(h, card.id)["device_id"]
    label = h.store.device(device_id).label
    spaced = f"{MANUAL_CODE[:4]}-{MANUAL_CODE[4:7]} {MANUAL_CODE[7:]}"
    for name in (QR_CODE, MANUAL_CODE, spaced, f"  {QR_CODE.lower()}  "):
        with pytest.raises(CodeRejected) as refused:
            await h.session.update_card(card.id, name=name)
        assert refused.value.detail == i18n.t("api.commissioning.fail_name_is_code")
        assert refused.value.status == 422
        assert card_view(h, card.id)["name"] != name
    # A code that reached the card some other way is refused on confirm too.
    card.name = MANUAL_CODE
    with pytest.raises(CodeRejected) as on_confirm:
        await h.session.confirm_name(card.id)
    assert on_confirm.value.detail == i18n.t("api.commissioning.fail_name_is_code")
    assert card_view(h, card.id)["state"] == "naming"
    assert h.store.device(device_id).label == label
    await h.session.update_card(card.id, name="Lamp 2")
    assert (await h.session.confirm_name(card.id)).state == "done"
    assert h.store.device(device_id).label == "Lamp 2"


async def test_a_done_card_takes_no_pairing_code_as_its_name_either(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    await h.session.skip_name(card.id)
    device_id = card_view(h, card.id)["device_id"]
    label = h.store.device(device_id).label
    with pytest.raises(CodeRejected):
        await h.session.update_card(card.id, name=MANUAL_CODE)
    assert h.store.device(device_id).label == label


async def test_scanning_while_running_queues_at_the_end(h):
    h.gate.held = True
    h.scanner.adverts = [advert("AA:02", 3841, KAJPLATS_E14, -60)]
    await h.session.scan()
    first = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, first.id)["state"] == "running", "running")
    second = await h.session.add_code(MANUAL_CODE, None)
    assert second.state == "queued"
    assert card_view(h, second.id)["queue_position"] == 1
    assert card_view(h, first.id)["queue_position"] is None


async def test_a_failure_moves_on_to_the_next_card(h):
    h.gate.failures = [CommissioningError("Commission with code failed for node 7."), None]
    first = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.adverts = [advert("AA:02", 3841, KAJPLATS_E14, -60)]
    await h.session.scan()
    second = await h.session.add_code(MANUAL_CODE, None)
    assert second.state == "ready"
    await h.session.start()
    await idle(h)
    one = card_view(h, first.id)
    assert one["state"] == "failed"
    assert one["note"]
    assert one["has_code"] is True
    assert card_view(h, second.id)["state"] == "naming"
    assert h.gate.calls == 2


async def test_a_card_whose_device_stopped_advertising_is_skipped(h):
    card = await ready_card(h, "AA:01", 3840, KAJPLATS_E27, -60, QR_CODE)
    h.scanner.adverts = []
    assert await h.session.scan()
    await h.session.start()
    await idle(h)
    view = card_view(h, card.id)
    assert view["state"] == "not_nearby"
    assert h.gate.calls == 0
    forced = await h.session.force(card.id)
    assert forced.state in ("queued", "running")
    await idle(h)
    assert card_view(h, card.id)["state"] == "naming"
    assert h.client.commissioned == [QR_CODE]


async def test_early_warning_after_20_seconds_without_a_matching_sample(h):
    h.gate.held = True
    card = await ready_card(h, "AA:01", 3840, KAJPLATS_E27, -60, QR_CODE)
    await h.session.start()
    await settle_until(lambda: card_view(h, card.id)["phase"] == "searching", "searching")
    h.scanner.adverts = []
    assert card_view(h, card.id)["note"] is None
    h.clock.now += 21.0
    await settle_until(
        lambda: card_view(h, card.id)["note"] == i18n.t("web.commissioning.note_no_signal_yet"),
        "the early warning",
    )


async def test_no_early_warning_while_a_matching_device_advertises(h):
    h.gate.held = True
    card = await ready_card(h, "AA:01", 3840, KAJPLATS_E27, -60, QR_CODE)
    # matter-server's own scan finds the device while it commissions.
    h.reader.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -55)]
    await h.session.start()
    await settle_until(lambda: card_view(h, card.id)["phase"] == "searching", "searching")
    h.clock.now += 21.0
    reads = h.reader.reads
    # The monitor must actually have looked again after the jump - a test
    # that only waited would also pass with a monitor that never ticks.
    await settle_until(lambda: h.reader.reads >= reads + 3, "the monitor read BlueZ again")
    assert card_view(h, card.id)["phase"] == "searching"
    assert card_view(h, card.id)["note"] is None


async def test_the_worker_waits_while_matter_server_is_away(h):
    h.client.connected = False
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    for _ in range(20):
        await settle_until(lambda: True, "a few turns")
    assert card_view(h, card.id)["state"] == "queued"
    assert h.session.view()["matter_connected"] is False
    assert h.session.has_work
    h.client.connected = True
    await idle(h)
    assert card_view(h, card.id)["state"] == "naming"


async def test_name_change_after_commissioning_is_written_to_the_device(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.update_card(card.id, name="Esstisch")
    await h.session.start()
    await idle(h)
    device_id = card_view(h, card.id)["device_id"]
    await h.session.update_card(card.id, name="Flur", room="Diele")
    device = h.store.device(device_id)
    assert device.label == "Flur"
    assert device.room == "Diele"


async def test_numeric_code_card_takes_over_its_device_after_commissioning(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:02", 3841, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    card = await h.session.add_code(MANUAL_CODE, None)
    h.gate.snapshots = [snapshot_of(200, 4476, KAJPLATS_E27)]
    await h.session.start()
    await idle(h)
    view = card_view(h, card.id)
    assert view["product"] == "KAJPLATS E27 WS globe 1055lm"
    assert view["detail"] == "IKEA of Sweden · LED2407G8"
    assert view["note"] is None
    remaining = [c for c in h.session.view()["cards"] if c["id"] != card.id]
    assert [c["product"] for c in remaining] == ["KAJPLATS E14 CWS globe 806lm"]


async def test_clear_keeps_the_running_card(h):
    h.gate.held = True
    # 3841: the manual code matches it, the QR code (3840) does not.
    h.scanner.adverts = [advert("AA:05", 3841, BILRESA, -40)]
    await h.session.scan()
    first = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, first.id)["state"] == "running", "running")
    queued = await h.session.add_code(MANUAL_CODE, None)
    assert card_view(h, queued.id)["state"] == "queued"
    h.session.clear()
    assert [c["id"] for c in h.session.view()["cards"]] == [first.id]
    h.gate.held = False
    h.gate.release_all()
    await idle(h)
    assert h.gate.calls == 1
    assert h.client.commissioned == [QR_CODE_ON_NETWORK]


async def test_bluetooth_warning_after_a_wedge_line(h):
    assert h.session.view()["bluetooth_warning"] is False
    h.kernel.findings = [KernelFinding("stuck", h.kernel.usec + 5)]
    await h.session.scan()
    assert h.session.view()["bluetooth_warning"] is True


async def test_an_old_kernel_line_does_not_warn(h):
    h.kernel.findings = [KernelFinding("stuck", h.kernel.usec - 5)]
    await h.session.scan()
    assert h.session.view()["bluetooth_warning"] is False


async def test_a_failed_scan_warns_and_reports_false(h):
    h.scanner.fail_with = BluezScanError("org.bluez.Error.NotReady")
    assert not await h.session.scan()
    assert h.session.view()["bluetooth_warning"] is True
    assert h.session.view()["scan"]["state"] == "idle"


async def test_unknown_and_running_cards_are_refused(h):
    with pytest.raises(CodeRejected) as unknown:
        h.session.remove(99)
    assert unknown.value.status == 404
    assert unknown.value.detail == i18n.t("api.commissioning.fail_unknown_card")
    h.gate.held = True
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, card.id)["state"] == "running", "running")
    with pytest.raises(CodeRejected) as running:
        h.session.remove(card.id)
    assert running.value.status == 409
    assert running.value.detail == i18n.t("api.commissioning.fail_card_running")


async def test_a_rescan_attaches_a_not_nearby_code_to_its_device(h):
    card = await h.session.add_code(QR_CODE, "Bad")
    assert card.state == "not_nearby"
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    cards = h.session.view()["cards"]
    assert len(cards) == 1
    assert cards[0]["state"] == "ready"
    assert cards[0]["has_code"] is True
    assert cards[0]["room"] == "Bad"
    assert cards[0]["product"] == "KAJPLATS E27 WS globe 1055lm"


async def test_aclose_cancels_a_running_worker_and_closes_identify(h):
    h.gate.held = True
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, card.id)["state"] == "running", "running")
    await h.session.aclose()
    assert h.identify.closed
    assert not h.session.busy


async def test_a_code_added_while_the_worker_drains_is_still_commissioned(h):
    # The DCL lookup for a new card is awaited; a worker that finishes its
    # last card meanwhile must not leave the new card queued with nobody
    # to run it. Both adverts fit the manual code, neither the QR code.
    h.scanner.adverts = [
        advert("AA:01", 3841, KAJPLATS_E27, -60),
        advert("AA:02", 3842, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    labels = HeldLabels(h.session)
    h.gate.held = True
    first = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await settle_until(lambda: card_view(h, first.id)["state"] == "running", "running")
    labels.arm()
    adding = asyncio.ensure_future(h.session.add_code(MANUAL_CODE, None))
    await settle_until(lambda: labels.waiting, "the lookup waits")
    h.gate.held = False
    h.gate.release_all()
    await settle_until(lambda: h.session._worker.done(), "the worker ended")
    labels.release.set()
    second = await adding
    await idle(h)
    assert card_view(h, second.id)["state"] == "naming"
    assert h.client.commissioned == [QR_CODE_ON_NETWORK, MANUAL_CODE]


async def test_an_unexpected_failure_keeps_the_code_out_of_log_and_note(h, caplog):
    caplog.set_level(logging.DEBUG)
    h.gate.failures = [RuntimeError(f"matter-server choked on {QR_CODE}")]
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    view = card_view(h, card.id)
    assert view["state"] == "failed"
    assert "Y.K9042C00KA0648G00" not in (view["note"] or "")
    assert caplog.records, "the failure is logged"
    for record in caplog.records:
        assert "Y.K9042C00KA0648G00" not in record.getMessage()
        assert record.exc_info is None
        assert record.exc_text is None


async def test_an_unexpected_failure_fails_the_card_and_moves_on(h):
    h.gate.failures = [RuntimeError("boom"), None]
    first = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.adverts = [advert("AA:02", 3841, KAJPLATS_E14, -60)]
    await h.session.scan()
    second = await h.session.add_code(MANUAL_CODE, None)
    await h.session.start()
    await idle(h)
    one = card_view(h, first.id)
    assert one["state"] == "failed"
    assert one["note"] == i18n.t("api.errors.commissioning_failed", exc="RuntimeError")
    assert one["has_code"] is True
    assert card_view(h, second.id)["state"] == "naming"
    assert h.gate.calls == 2


async def test_removing_the_head_while_a_scan_holds_the_radio_keeps_the_next_card(h):
    gone = await ready_card(h, "AA:01", 3840, KAJPLATS_E27, -60, QR_CODE)
    nxt = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.hold = asyncio.Event()
    scan = asyncio.ensure_future(h.session.scan())
    await settle_until(lambda: len(h.scanner.scans) == 2, "the second scan runs")
    await h.session.start()
    await settle_until(lambda: h.session._radio._waiters, "the worker waits for the radio")
    h.session.remove(gone.id)
    hold, h.scanner.hold = h.scanner.hold, None
    hold.set()
    assert await scan
    await idle(h)
    assert card_view(h, nxt.id)["state"] == "naming"
    assert h.client.commissioned == [QR_CODE_ON_NETWORK]


async def test_the_front_of_the_naming_line_takes_over_a_running_blink(h):
    await h.identify.start(999, renew=False)
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    device_id = card_view(h, card.id)["device_id"]
    assert h.identify.starts()[-1] == ("start", device_id, True, 30)
    assert h.session.view()["blinking_card"] == card.id


async def test_a_preset_name_check_blink_never_interrupts(h):
    await h.identify.start(999, renew=False)
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.update_card(card.id, name="Esstisch")
    await h.session.start()
    await idle(h)
    assert card_view(h, card.id)["state"] == "done"
    assert h.identify.blinking == 999
    assert h.identify.starts() == [("start", 999, False, 30)]


async def test_removing_the_naming_front_hands_the_blink_on(h):
    a = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.adverts = [advert("AA:02", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    b = await h.session.add_code(QR_CODE, None)
    await h.session.start()
    await idle(h)
    device_b = card_view(h, b.id)["device_id"]
    assert h.session.view()["blinking_card"] == a.id
    h.session.remove(a.id)
    await settle_until(lambda: h.identify.blinking == device_b, "b blinks")
    assert h.identify.starts()[-1] == ("start", device_b, True, 30)
    assert h.session.view()["naming"] == [b.id]
    assert h.session.view()["blinking_card"] == b.id


async def test_the_scan_is_blocked_during_follow_up_work(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:02", 3841, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    card = await h.session.add_code(MANUAL_CODE, None)
    labels = HeldLabels(h.session)
    labels.arm()
    h.gate.snapshots = [snapshot_of(200, 4476, KAJPLATS_E27)]
    await h.session.start()
    # `_adopt` looks the product up after the device is commissioned,
    # while the worker still holds the radio.
    await settle_until(lambda: labels.waiting, "the follow-up lookup waits")
    assert card_view(h, card.id)["state"] == "running"
    assert h.session.busy
    assert h.session.view()["scan"]["state"] == "blocked"
    assert not await h.session.scan()
    labels.release.set()
    await idle(h)
    assert h.session.view()["scan"]["state"] == "idle"
    assert not h.session.busy


async def test_a_rescan_matches_only_adverts_seen_now(h):
    card = await h.session.add_code(MANUAL_CODE, None)
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:02", 3841, KAJPLATS_E14, -65),
    ]
    await h.session.scan()
    # Two devices fit the short discriminator: the code stays on its own.
    assert card_view(h, card.id)["state"] == "not_nearby"
    # AA:02 has gone quiet: its card goes, and what advertises now is one
    # device.
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    cards = h.session.view()["cards"]
    assert len(cards) == 1
    assert cards[0]["has_code"] is True
    assert cards[0]["product"] == "KAJPLATS E27 WS globe 1055lm"
    assert cards[0]["state"] == "ready"


async def test_a_rescan_queues_a_revived_card_while_the_worker_runs(h):
    skipped = await ready_card(h, "AA:01", 3840, KAJPLATS_E27, -60, QR_CODE)
    h.scanner.adverts = []
    assert await h.session.scan()
    await h.session.start()
    await idle(h)
    assert card_view(h, skipped.id)["state"] == "not_nearby"
    # The worker waits for matter-server with a card in its queue.
    h.client.connected = False
    waiting = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    assert await h.session.scan()
    assert card_view(h, skipped.id)["state"] == "queued"
    h.client.connected = True
    await idle(h)
    assert card_view(h, waiting.id)["state"] == "naming"
    assert card_view(h, skipped.id)["state"] == "naming"
    assert h.client.commissioned == [QR_CODE_ON_NETWORK, QR_CODE]


# A dataset in the shape `ot-ctl dataset active -x` prints; its tail is
# what the assertions look for, so it must not occur anywhere else.
THREAD_DATASET = "0e080000000000010000" + "4a0300000f" + "35060004001fffe0" + "c0de" * 8


async def test_a_manual_thread_dataset_reaches_every_commissioning(h):
    h.session.set_thread_dataset(THREAD_DATASET)
    await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.adverts = [advert("AA:02", 3840, KAJPLATS_E27, -60)]
    await h.session.scan()
    await h.session.add_code(QR_CODE, None)
    await h.session.start()
    await idle(h)
    assert h.client.datasets == [THREAD_DATASET, THREAD_DATASET]
    order = [step for step in h.client.order if step in ("dataset", "commission")]
    assert order == ["dataset", "commission", "dataset", "commission"]


async def test_removing_the_manual_thread_dataset_falls_back_to_the_border_router(h):
    h.session.set_thread_dataset(THREAD_DATASET)
    h.session.set_thread_dataset(None)
    await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    assert THREAD_DATASET not in h.client.datasets
    assert h.session.view()["thread_dataset_set"] is False


async def test_the_thread_dataset_never_appears_in_the_view_or_the_log(h, caplog):
    caplog.set_level(logging.DEBUG)
    assert h.session.view()["thread_dataset_set"] is False
    h.session.set_thread_dataset(THREAD_DATASET)
    view = h.session.view()
    assert view["thread_dataset_set"] is True
    assert "c0dec0de" not in json.dumps(view)
    with pytest.raises(CodeRejected) as rejected:
        h.session.set_thread_dataset(THREAD_DATASET + "c")
    assert rejected.value.status == 422
    assert rejected.value.detail == i18n.t("api.devices.fail_manual_thread_dataset")
    # A rejected dataset leaves the one set before in place.
    assert h.session.view()["thread_dataset_set"] is True
    await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    for record in caplog.records:
        assert "c0dec0de" not in record.getMessage()


# --- final review: the scan result, overlapping scans, codes, naming ---


async def test_cards_come_from_the_scan_itself_not_from_a_read_after_it(h):
    """BlueZ clears every RSSI when the bridge's scan stops (measured 29
    September 2026), and the reader drops a device without one: a read
    after the scan finds nothing. The session takes what the scan read."""
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    h.reader.adverts = []
    assert await h.session.scan()
    assert [c["product"] for c in h.session.view()["cards"]] == ["KAJPLATS E27 WS globe 1055lm"]
    card = await h.session.add_code(QR_CODE, None)
    assert card.state == "ready"
    await h.session.start()
    await idle(h)
    # The worker checked the latest scan, not BlueZ after it.
    assert card_view(h, card.id)["state"] == "naming"
    assert h.client.commissioned == [QR_CODE]


async def test_a_found_card_not_seen_again_goes_and_a_card_with_a_code_stays(h):
    h.scanner.adverts = [
        advert("AA:01", 3840, KAJPLATS_E27, -60),
        advert("AA:03", 2000, BILRESA, -70),
    ]
    assert await h.session.scan()
    ready = await h.session.add_code(QR_CODE, None)
    h.scanner.adverts = []
    assert await h.session.scan()
    cards = h.session.view()["cards"]
    assert [c["id"] for c in cards] == [ready.id]
    assert cards[0]["state"] == "ready"


async def test_a_code_matches_only_a_device_of_the_latest_scan(h):
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    assert await h.session.scan()
    h.scanner.adverts = []
    assert await h.session.scan()
    card = await h.session.add_code(QR_CODE, None)
    assert card.state == "not_nearby"


async def test_overlapping_scans_make_one_card_per_device(h):
    h.scanner.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    labels = HeldLabels(h.session)
    labels.arm()
    first = asyncio.ensure_future(h.session.scan())
    await settle_until(lambda: labels.waiting, "the first scan's lookup waits")
    # The radio is free again: a second scan runs and ends meanwhile.
    second = asyncio.ensure_future(h.session.scan())
    await settle_until(lambda: len(h.scanner.scans) == 2, "the second scan ran")
    labels.release.set()
    assert await first
    assert await second
    assert len(h.session.view()["cards"]) == 1


async def test_the_worker_looks_at_matter_server_again_after_taking_the_radio(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    h.scanner.hold = asyncio.Event()
    scan = asyncio.ensure_future(h.session.scan())
    await settle_until(lambda: len(h.scanner.scans) == 1, "the scan runs")
    await h.session.start()
    await settle_until(lambda: h.session._radio._waiters, "the worker waits for the radio")
    h.client.connected = False
    hold, h.scanner.hold = h.scanner.hold, None
    hold.set()
    assert await scan
    for _ in range(20):
        await settle_until(lambda: True, "a few turns")
    assert card_view(h, card.id)["state"] == "queued"
    assert h.gate.calls == 0
    h.client.connected = True
    await idle(h)
    assert card_view(h, card.id)["state"] == "naming"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("3497-011-2332", MANUAL_CODE),
        ("3497 011 2332", MANUAL_CODE),
        (QR_CODE.lower(), QR_CODE),
    ],
)
async def test_one_code_in_two_spellings_is_a_duplicate(h, first, second):
    await h.session.add_code(first, None)
    with pytest.raises(CodeRejected) as duplicate:
        await h.session.add_code(second, None)
    assert duplicate.value.status == 409


async def test_the_worker_hands_on_the_normalized_code(h):
    await h.session.add_code("mt:-24j0afn00ka0648g00", None)
    await h.session.start()
    await idle(h)
    assert h.client.commissioned == [QR_CODE_ON_NETWORK]


async def test_naming_a_device_deleted_meanwhile_ends_the_turn_with_404(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    h.store.forget_device(card_view(h, card.id)["device_id"])
    await h.session.update_card(card.id, name="Flurlampe")
    with pytest.raises(CodeRejected) as gone:
        await h.session.confirm_name(card.id)
    assert gone.value.status == 404
    assert gone.value.detail == i18n.t("api.commissioning.fail_device_gone")
    assert card_view(h, card.id)["state"] == "done"
    assert h.session.view()["naming"] == []


async def test_renaming_a_done_card_whose_device_was_deleted_is_404(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    await h.session.skip_name(card.id)
    h.store.forget_device(card_view(h, card.id)["device_id"])
    with pytest.raises(CodeRejected) as gone:
        await h.session.update_card(card.id, name="Flurlampe")
    assert gone.value.status == 404


async def test_identify_on_the_naming_front_is_renewed(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    device_id = card_view(h, card.id)["device_id"]
    await h.session.identify_card(card.id, on=False)
    await h.session.identify_card(card.id, on=True)
    assert h.identify.starts()[-1] == ("start", device_id, True, 30)


async def test_confirming_a_name_does_not_wait_for_the_device(h):
    card = await h.session.add_code(QR_CODE_ON_NETWORK, None)
    await h.session.start()
    await idle(h)
    answer = asyncio.Event()
    stopped = []

    async def slow_stop():
        await answer.wait()
        stopped.append(True)
        h.identify.blinking = None

    h.identify.stop = slow_stop  # type: ignore[method-assign]
    await h.session.update_card(card.id, name="Flurlampe")
    # Bounded: a confirm that waited for the device would hang here.
    done = await asyncio.wait_for(h.session.confirm_name(card.id), 5)
    assert done.state == "done"
    assert stopped == []
    answer.set()
    await settle_until(lambda: stopped == [True], "the blink stops in the background")
