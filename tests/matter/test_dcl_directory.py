"""Human-readable product names (design 2026-10-02, section 7). Fixtures are
the DCL's answers of October 2, 2026."""

from datetime import UTC, datetime, timedelta

import pytest

from loxmatter import i18n
from loxmatter.matter.dcl import DclDirectory, DclModel, is_test_vendor
from loxmatter.model.store import Store

VENDOR_4476 = {"vendorInfo": {"vendorID": 4476, "vendorName": "IKEA of Sweden"}}
MODEL_36865 = {
    "model": {
        "vid": 4476,
        "pid": 36865,
        "deviceTypeId": 268,
        "productName": "KAJPLATS E27 WS globe 1055lm",
        "partNumber": "LED2407G8",
        "commissioningModeInitialStepsHint": 1,
        "commissioningModeInitialStepsInstruction": "",
    }
}
MODEL_12288 = {
    "model": {
        "vid": 4476,
        "pid": 12288,
        "deviceTypeId": 263,
        "productName": "MYGGSPRAY wrlss mtn sensor",
        "partNumber": "E2494",
        "commissioningModeInitialStepsHint": 1,
        "commissioningModeInitialStepsInstruction": "",
    }
}


class Fetcher:
    def __init__(self, answers):
        self.answers = answers
        self.urls = []
        self.fail = False

    async def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("no route to host")
        return self.answers.get(url)


BASE = "https://on.dcl.csa-iot.org"


def _directory(tmp_path, answers, now):
    fetch = Fetcher(answers)
    return DclDirectory(Store(tmp_path / "t.sqlite").dcl, fetch, now=lambda: now[0]), fetch


async def test_names_from_the_dcl(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, _ = _directory(
        tmp_path,
        {
            f"{BASE}/dcl/vendorinfo/vendors/4476": VENDOR_4476,
            f"{BASE}/dcl/model/models/4476/36865": MODEL_36865,
        },
        now,
    )
    label = await directory.product_label(4476, 36865)
    assert label.product == "KAJPLATS E27 WS globe 1055lm"
    assert label.detail == "IKEA of Sweden · LED2407G8"
    assert label.pairing_hint == i18n.t("web.commissioning.hint_power_cycle")


async def test_a_second_lookup_does_not_ask_again(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(
        tmp_path, {f"{BASE}/dcl/model/models/4476/12288": MODEL_12288}, now
    )
    first = await directory.model(4476, 12288)
    second = await directory.model(4476, 12288)
    assert (
        first
        == second
        == DclModel(4476, 12288, "MYGGSPRAY wrlss mtn sensor", "E2494", 263, 1, None)
    )
    assert len(fetch.urls) == 1


async def test_not_in_the_dcl_is_asked_again_after_seven_days(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    assert await directory.model(4476, 1) is None
    now[0] += timedelta(days=6)
    assert await directory.model(4476, 1) is None
    assert len(fetch.urls) == 1
    now[0] += timedelta(days=2)
    await directory.model(4476, 1)
    assert len(fetch.urls) == 2


async def test_offline_falls_back_to_numbers_and_is_not_cached(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    fetch.fail = True
    label = await directory.product_label(0x117C, 0x9001)
    assert label.product == i18n.t(
        "web.commissioning.unknown_product", vendor="0x117C", product="0x9001"
    )
    fetch.fail = False
    fetch.answers[f"{BASE}/dcl/model/models/4476/36865"] = MODEL_36865
    assert (await directory.model(4476, 36865)).name == "KAJPLATS E27 WS globe 1055lm"


@pytest.mark.parametrize("vid", [0xFFF1, 0xFFF2, 0xFFF3, 0xFFF4])
async def test_test_vendors_are_never_asked(tmp_path, vid):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    label = await directory.product_label(vid, 0x8000)
    assert is_test_vendor(vid)
    assert label.product == i18n.t("web.commissioning.test_device", vendor=f"0x{vid:04X}")
    assert fetch.urls == []


async def test_without_ids_the_label_says_so(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    label = await directory.product_label(None, None)
    assert label.product == i18n.t("web.commissioning.numeric_code_device")
    assert fetch.urls == []
