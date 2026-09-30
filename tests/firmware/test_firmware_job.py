"""Installing one firmware update (design 2026-09-30, section 7)."""

import asyncio

import pytest
from firmware_fakes import KAJPLATS_OFFER, FakeFirmwareSource, idle_facts, register

from loxmatter.firmware import states
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareJobs,
    FirmwareUnsupportedError,
    JobTiming,
    OfferChangedError,
)
from loxmatter.model.store import Store


class Clock:
    """A clock the fake sleep advances; `script` runs source changes at
    given times, like a device reporting its state."""

    def __init__(self) -> None:
        self.t = 0.0
        self.script: list[tuple[float, object]] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        while self.script and self.script[0][0] <= self.t:
            _, action = self.script.pop(0)
            action()  # type: ignore[operator]
        await asyncio.sleep(0)


def _setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "t0")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    clock = Clock()
    jobs = FirmwareJobs(
        store, lambda: source, clock=clock, sleep=clock.sleep, now=lambda: "now", timing=JobTiming()
    )
    return store, source, clock, jobs, lamp_id, lamp


async def test_a_successful_update_ends_on_the_new_version(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [
        (2, lambda: source.set_state(lamp, 2)),
        (4, lambda: source.set_state(lamp, 4, 10)),
        (60, lambda: source.set_state(lamp, 4, 60)),
        (600, lambda: source.set_state(lamp, 5)),
        (660, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()

    assert source.started == [(lamp, 16908288)]
    status = store.firmware_status.get(lamp_id)
    assert status.job_state is None and status.offer is None
    assert store.device(lamp_id).firmware == "1.2.0"
    assert source.followed == [lamp]
    assert jobs.running_device_id is None


async def test_progress_is_written_while_transferring(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    seen = []
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 43)),
        (4, lambda: seen.append(store.firmware_status.get(lamp_id))),
        (6, lambda: source.set_state(lamp, 5)),
        (8, lambda: seen.append(store.firmware_status.get(lamp_id))),
        (10, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert (seen[0].job_state, seen[0].job_progress) == (states.TRANSFERRING, 43)
    assert (seen[1].job_state, seen[1].job_progress) == (states.APPLYING, None)


async def test_back_to_idle_without_new_version_fails_after_two_minutes(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 10)),
        (10, lambda: source.set_state(lamp, 1)),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    status = store.firmware_status.get(lamp_id)
    assert status.job_state == states.FAILED
    assert 130 <= clock.t <= 140
    assert status.offer == KAJPLATS_OFFER  # still offered, can be retried


async def test_no_change_for_fifteen_minutes_is_stalled_and_recovers(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    seen = []
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 10)),
        (910, lambda: seen.append(store.firmware_status.get(lamp_id).job_state)),
        (912, lambda: source.set_state(lamp, 4, 11)),
        (916, lambda: seen.append(store.firmware_status.get(lamp_id).job_state)),
        (920, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert seen == [states.STALLED, states.TRANSFERRING]


async def test_a_quiet_device_is_read_after_thirty_seconds(tmp_path):
    _, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 10)),
        (70, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert source.refreshed and source.refreshed[0] == lamp


async def test_the_job_gives_up_after_three_hours(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    ticker = [(t, lambda t=t: source.set_state(lamp, 4, t // 600)) for t in range(2, 11000, 600)]
    clock.script = ticker
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert store.firmware_status.get(lamp_id).job_state == states.FAILED
    assert 10800 <= clock.t <= 10810


async def test_losing_matter_server_interrupts_the_job(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 10)),
        (6, lambda: setattr(source, "connected", False)),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert store.firmware_status.get(lamp_id).job_state == states.INTERRUPTED


async def test_update_node_raising_fails_the_job(tmp_path):
    store, source, _, jobs, lamp_id, _ = _setup(tmp_path)
    source.fail_start = RuntimeError("node refused")
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    status = store.firmware_status.get(lamp_id)
    assert status.job_state == states.FAILED and status.job_error == "node refused"


async def test_only_one_install_runs_at_a_time(tmp_path):
    store, _, _, jobs, lamp_id, _ = _setup(tmp_path)
    button_id, _ = register(store, "ikea_bilresa_button.json")
    jobs.start(lamp_id, 16908288)
    with pytest.raises(FirmwareBusyError) as raised:
        jobs.start(button_id, 1)
    assert raised.value.device_id == lamp_id
    await jobs.stop()


async def test_a_version_other_than_the_offer_is_refused(tmp_path):
    _, _, _, jobs, lamp_id, _ = _setup(tmp_path)
    with pytest.raises(OfferChangedError):
        jobs.start(lamp_id, 16908289)


async def test_an_offline_device_is_refused(tmp_path):
    _, source, _, jobs, lamp_id, lamp = _setup(tmp_path)
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    with pytest.raises(DeviceOfflineError):
        jobs.start(lamp_id, 16908288)


async def test_an_unsupported_server_is_refused(tmp_path):
    _, source, _, jobs, lamp_id, _ = _setup(tmp_path)
    source.supported = False
    with pytest.raises(FirmwareUnsupportedError):
        jobs.start(lamp_id, 16908288)


async def test_resume_picks_up_a_transfer_after_a_loxmatter_restart(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    store.firmware_status.start_job(lamp_id, "before restart")
    source.set_state(lamp, 4, 50)
    clock.script = [(10, lambda: source.finish(lamp, 16908288, "1.2.0"))]
    assert jobs.resume_all() == 1
    assert jobs.running_device_id == lamp_id
    await jobs.wait()
    assert source.started == []  # no second update_node
    assert store.device(lamp_id).firmware == "1.2.0"


async def test_resume_marks_a_job_whose_device_went_idle_as_interrupted(tmp_path):
    store, _, _, jobs, lamp_id, _ = _setup(tmp_path)
    store.firmware_status.start_job(lamp_id, "before restart")
    assert jobs.resume_all() == 0
    assert store.firmware_status.get(lamp_id).job_state == states.INTERRUPTED


class _SlowSource(FakeFirmwareSource):
    """`start_update` and `follow` that yield to the loop, so the start can
    end - or not - while the job is already past its last look at it."""

    def __init__(self) -> None:
        super().__init__()
        self.start_hangs = False

    async def start_update(self, address: str, software_version: int) -> None:
        self.started.append((address, software_version))
        if self.start_hangs:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        if self.fail_start is not None:
            raise self.fail_start

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        for _ in range(3):
            await asyncio.sleep(0)
        self.followed.append(address)


def _slow_setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "t0")
    source = _SlowSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    clock = Clock()
    jobs = FirmwareJobs(store, lambda: source, clock=clock, sleep=clock.sleep, now=lambda: "now")
    return store, source, clock, jobs, lamp_id, lamp


def _loop_errors() -> list[dict]:
    errors: list[dict] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: errors.append(context))
    return errors


def _other_tasks() -> list[asyncio.Task]:
    current = asyncio.current_task()
    return [task for task in asyncio.all_tasks() if task is not current and not task.done()]


async def test_a_start_failing_after_the_job_ended_is_still_retrieved(tmp_path):
    import gc

    errors = _loop_errors()
    store, source, _, jobs, lamp_id, lamp = _slow_setup(tmp_path)
    source.fail_start = RuntimeError("late refusal")
    # The device already reports the new version: the job ends on success
    # while the start is still running, and the start fails afterwards.
    source.finish(lamp, 16908288, "1.2.0")
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    gc.collect()
    await asyncio.sleep(0)
    assert store.firmware_status.get(lamp_id).job_state is None
    assert errors == []
    assert _other_tasks() == []


async def test_stopping_a_job_whose_start_still_runs_leaves_no_task_behind(tmp_path):
    errors = _loop_errors()
    _, source, _, jobs, lamp_id, _ = _slow_setup(tmp_path)
    source.start_hangs = True
    jobs.start(lamp_id, 16908288)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await jobs.stop()
    assert _other_tasks() == []
    assert errors == []


async def test_an_interrupted_job_whose_start_still_runs_leaves_no_task_behind(tmp_path):
    errors = _loop_errors()
    store, source, clock, jobs, lamp_id, _ = _slow_setup(tmp_path)
    source.start_hangs = True
    clock.script = [(4, lambda: setattr(source, "connected", False))]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert store.firmware_status.get(lamp_id).job_state == states.INTERRUPTED
    assert _other_tasks() == []
    assert errors == []
