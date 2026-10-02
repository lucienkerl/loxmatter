"""The shared commissioning sequence (design 2026-10-02, section 8.3)."""

import pytest

from loxmatter.commissioning.run import CommissionFailed, commission
from loxmatter.matter.client import CommissioningError
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.model.store import Store


async def test_commission_registers_the_device_with_its_room(
    tmp_path, fake_client, fake_runtime, fake_otbr
):
    store = Store(tmp_path / "t.sqlite")
    fake_client.store = store
    result = await commission(
        code="34970112332",
        room="Kitchen",
        discriminator=None,
        client=fake_client,
        store=store,
        runtime=fake_runtime(store),
        tracker=CommissioningTracker(),
        fetch_dataset=fake_otbr,
    )
    assert store.device(result.device_id).room == "Kitchen"


async def test_a_refusal_becomes_commission_failed(tmp_path, fake_client, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    fake_client.fail_commission_with = CommissioningError("No commissionable device was discovered")
    with pytest.raises(CommissionFailed) as raised:
        await commission(
            code="34970112332",
            room=None,
            discriminator=None,
            client=fake_client,
            store=store,
            runtime=fake_runtime(store),
            tracker=CommissioningTracker(),
            fetch_dataset=fake_otbr,
        )
    assert raised.value.status == 422
    assert raised.value.reason == "not_found"
