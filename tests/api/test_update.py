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

"""Tests for GET /api/update/status, GET /api/update/check and
POST /api/update/apply - WebUI updates design (2026-09-08), sections 7,
9, 10, 11, 12 and 13.

`api` builds the app with its own, throwaway `update` directory (via
`build_app`'s `update_base`) instead of the real `/data/update` mount -
tests write `state.json` into it directly with `update.protocol.write_state`
to control exactly what the route reads, the same way `test_settings_api.py`
writes straight to the store instead of going through a second route.

`GET /api/update/check` reaching GitHub itself is still not exercised
here - `_query_github` is monkeypatched wherever the cache or the route's
shape needs proving, so nothing in this file makes a real network call,
which would be flaky in CI for reasons that have nothing to do with this
route's own logic."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate

import loxmatter.api.update as update_module
from loxmatter.api.update import _BUSY_BOUND_SECONDS, _STALE_AFTER_SECONDS
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.model.update_settings_store import UpdateChannel
from loxmatter.update import runner as runner_module
from loxmatter.update.commands import (
    _BACKUP_TIMEOUT_SECONDS,
    _GIT_FETCH_TIMEOUT_SECONDS,
    _HEALTH_TIMEOUT_SECONDS,
    _IMAGE_PULL_TIMEOUT_SECONDS,
    _LOCAL_TIMEOUT_SECONDS,
    _RECREATE_TIMEOUT_SECONDS,
)
from loxmatter.update.protocol import UpdateRequest, UpdateState, read_request, write_state
from loxmatter.update.runner import HEALTH_REFRESH_POLLS, POLL_SECONDS, run_update


@pytest.fixture
async def api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, Path]]:
    store = Store(tmp_path / "t.sqlite")
    update_dir = tmp_path / "update"
    app = build_app(store, no_invoke, fake_runtime(store), update_base=update_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, update_dir
    store.close()


@pytest.fixture
async def api_without_session(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_base=tmp_path / "update")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    store.close()


def _write_state(update_dir, *, phase, updater_seen_at, **overrides) -> None:
    fields = {
        "id": "req-1",
        "phase": phase,
        "steps": ["backup"],
        "from_version": "0.2.0",
        "to_version": "0.3.0",
        "error": None,
        "rolled_back": False,
        "healthy": False,
        "updater_seen_at": updater_seen_at,
    }
    fields.update(overrides)
    write_state(update_dir, UpdateState(**fields))


async def test_status_says_no_updater_when_the_stamp_is_missing_or_stale(api):
    """Design section 11. `state.json` absent, or `updater_seen_at` older
    than a few loop intervals, both mean the same thing to an operator:
    there is nothing here that could perform an update. The interface must
    say so plainly rather than offering a button that does nothing."""
    client, _, update_dir = api

    missing = await client.get("/api/update/status")
    assert missing.status_code == 200
    assert missing.json()["updater_present"] is False

    stale_stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="idle", updater_seen_at=stale_stamp)
    stale = await client.get("/api/update/status")
    assert stale.status_code == 200
    assert stale.json()["updater_present"] is False


async def test_status_reports_the_phase_from_the_file(api):
    """Not from the bridge's memory. This route answers correctly one
    second after the bridge itself was restarted by an update - which is
    the entire reason the state is a file."""
    client, _, update_dir = api

    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(
        update_dir,
        phase="pull",
        updater_seen_at=fresh_stamp,
        steps=["backup", "pull"],
        from_version="0.2.0",
        to_version="0.3.0",
    )

    response = await client.get("/api/update/status")
    assert response.status_code == 200
    body = response.json()
    assert body["updater_present"] is True
    assert body["phase"] == "pull"
    assert body["steps"] == ["backup", "pull"]
    assert body["from_version"] == "0.2.0"
    assert body["to_version"] == "0.3.0"
    assert body["updater_seen_at"] == fresh_stamp


async def test_apply_writes_a_request_and_returns_its_id(api, monkeypatch):
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.2.0")

    response = await client.post(
        "/api/update/apply", json={"channel": "stable", "target": "v0.3.0"}
    )
    assert response.status_code == 200
    request_id = response.json()["id"]
    assert request_id

    written = read_request(update_dir)
    assert written is not None
    assert written.id == request_id
    assert written.channel == "stable"
    assert written.target == "v0.3.0"


async def test_apply_refuses_a_target_the_rules_reject(api, monkeypatch):
    """The rules live in the updater and are enforced there. Checking them
    here too is not duplication for its own sake: a request that the
    sidecar will refuse should fail while the operator is still looking at
    the screen, not silently two seconds later in a file they never open."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.5.0")

    response = await client.post(
        "/api/update/apply", json={"channel": "stable", "target": "v0.1.0"}
    )
    assert response.status_code == 400
    # Refused before anything reached disk - the sidecar never sees a
    # request the rules would have rejected too.
    assert read_request(update_dir) is None


async def test_apply_refusal_detail_follows_the_selected_language(api, monkeypatch):
    """Design section 12 names this route's error messages among the ones
    needing en/de pairs, and CLAUDE.md says an `HTTPException` detail the
    interface displays belongs in `strings.yaml` - `rules.py` authors this
    English prose itself (unlike `api/devices.py`'s `str(exc)` sites, which
    forward a third-party layer's own message), so it must not reach the
    operator untranslated. Before the fix, `raise HTTPException(...,
    detail=exc.reason)` passed the English reason straight through
    regardless of the selected language."""
    client, store, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.5.0")

    async def apply() -> str:
        response = await client.post(
            "/api/update/apply", json={"channel": "stable", "target": "v0.1.0"}
        )
        assert response.status_code == 400
        return str(response.json()["detail"])

    english = await apply()
    store.locale.set_language("de")
    german = await apply()

    assert english != german
    assert "not newer than" in english
    assert "nicht neuer als" in german
    # Refused in both languages - the operator sees a translated reason,
    # never an untranslated fallback.
    assert read_request(update_dir) is None


async def test_apply_refuses_a_second_request_while_one_is_running(api, monkeypatch):
    """Design section 13: "rejection of a second request while one is
    running." Reproduced by the finding this closes: `state.json` at
    `phase="pull"` with a fresh stamp, a second `POST /apply` used to come
    back 200 and overwrite `request.json` - and because `run_once`
    (`update/__main__.py`) returns the OLD id once the in-flight
    `run_update` finishes, the very next pass then saw a DIFFERENT id in
    `request.json` and ran a second full update immediately, back to back
    with the first."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.2.0")
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="pull", updater_seen_at=fresh_stamp)

    response = await client.post(
        "/api/update/apply", json={"channel": "stable", "target": "v0.3.0"}
    )

    assert response.status_code == 409
    # `request.json` untouched - a rejected second request must not
    # overwrite whatever the first one is still running against.
    assert read_request(update_dir) is None


async def test_apply_accepts_a_new_request_once_the_previous_one_finished(api, monkeypatch):
    """A terminal phase (`"done"`/`"failed"`/`"idle"`) is not "an update is
    running", however fresh its stamp - the operator must be able to start
    the very next update the moment the last one finishes, not wait out
    some cooldown."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.2.0")
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="done", updater_seen_at=fresh_stamp, healthy=True)

    response = await client.post(
        "/api/update/apply", json={"channel": "stable", "target": "v0.3.0"}
    )

    assert response.status_code == 200
    assert read_request(update_dir) is not None


async def test_apply_accepts_a_request_when_state_json_is_missing_or_corrupt(api, monkeypatch):
    """ "No usable state" must not read as "an update is running" -
    `read_state` returns `None` for a missing file and for a corrupt one
    alike, and treating that as busy would wedge the apply button
    permanently on one bad byte."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.2.0")

    missing = await client.post("/api/update/apply", json={"channel": "stable", "target": "v0.3.0"})
    assert missing.status_code == 200

    update_dir.mkdir(parents=True, exist_ok=True)
    (update_dir / "state.json").write_text("{not valid json", encoding="utf-8")
    corrupt = await client.post("/api/update/apply", json={"channel": "stable", "target": "v0.4.0"})
    assert corrupt.status_code == 200


async def test_apply_accepts_a_request_once_a_busy_looking_phase_has_wedged(api, monkeypatch):
    """A phase string alone must not be permanent evidence of a running
    update - the same requirement `_updater_present` has to satisfy. A
    `phase="pull"` stamp far older than `_BUSY_BOUND_SECONDS["pull"]` is a
    dead sidecar, not a running one, and the operator must be able to
    start over."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.2.0")
    wedged_stamp = (
        datetime.now(UTC) - timedelta(seconds=_BUSY_BOUND_SECONDS["pull"] + 3600)
    ).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="pull", updater_seen_at=wedged_stamp)

    response = await client.post(
        "/api/update/apply", json={"channel": "stable", "target": "v0.3.0"}
    )
    assert response.status_code == 200


def test_the_stale_after_threshold_leaves_headroom_over_the_health_wait_refresh_interval():
    """Renamed from "...worst_case_refresh_interval..." - that name
    claimed to check the general property (the worst case across an
    entire run) while actually modelling only the health-check wait, the
    ONE stamp-free window that was already fixed. A reader seeing it green
    believed the whole defect class (a hand-typed threshold silently
    drifting out of agreement with the constants that determine the real
    worst case) could not recur - it recurred anyway, in three windows far
    larger than this one, which is exactly why this test's own name now
    says only what it actually covers. `test_every_busy_phase_has_a_bound_
    that_covers_its_own_actual_worst_case` below is the general property.

    Two constants that live in different modules with nothing else tying
    them together: `runner.HEALTH_REFRESH_POLLS`/`runner.POLL_SECONDS`
    (how often `_wait_for_health` republishes `state.json` during a health
    wait) and this module's own `_STALE_AFTER_SECONDS` (how old that stamp
    may get before `_updater_present`'s fast path calls the updater gone).
    The worst case per poll is `POLL_SECONDS` (the health check's own
    subprocess call, bounded by `commands.py`'s `_HEALTH_TIMEOUT_SECONDS`
    backstop - see there) plus the `sleep(POLL_SECONDS)` `_wait_for_health`
    always pays regardless of how long the poll took. This is not four
    separate numbers asserted to look right in isolation - every one of
    them is imported from the module that actually defines it, so a
    change to any of the three that erodes the real margin fails here,
    not silently."""
    worst_case_refresh_interval_seconds = HEALTH_REFRESH_POLLS * (
        POLL_SECONDS + _HEALTH_TIMEOUT_SECONDS
    )
    minimum_headroom_seconds = 10.0
    assert _STALE_AFTER_SECONDS >= worst_case_refresh_interval_seconds + minimum_headroom_seconds


def test_every_busy_phase_has_a_bound_that_covers_its_own_actual_worst_case():
    """The general property `_the_stale_after_threshold_...` above was
    supposed to be, and was not: this covers every stamp-free window a
    real run can produce, not only the health-check wait. Four windows,
    reproduced from a full read of `runner.run_update` and `commands.py`'s
    own timeouts - each computed here independently from the raw
    constants, not by importing `_BUSY_BOUND_SECONDS` and checking it
    against itself, so a change to any underlying timeout that erodes the
    real margin fails here even if `_BUSY_BOUND_SECONDS`'s own formula
    "kept up" with it by construction:

    - `"backup"`, published once the backup step itself is done: `fetch()`
      + `checkout()` + `pull()` run before the next publish (`"pull"`).
      Against a flat 30s threshold this was negative margin by a factor of
      twenty - a fetch, a checkout and a full image pull comfortably
      exceed half a minute even on a fast connection, and the constants
      say this can legitimately run for over twenty minutes.
    - `"pull"`, before the next publish (`"recreate"`): `write_image_tag()`
      is plain file I/O with nothing to time out on, so only `recreate()`
      is bounded here.
    - `"rollback"`, before its own confirmation health wait starts
      polling: `checkout()` + `recreate()`, then one health-wait refresh
      cycle before the first republish.
    - The health-wait windows (`"recreate"` into the main wait, and
      `"health"` itself) are the ones the fast path (`_STALE_AFTER_SECONDS`)
      already covers with headroom - included here too so this test states
      the full picture in one place rather than trusting a coincidence.
    """
    health_wait_worst_case = HEALTH_REFRESH_POLLS * (POLL_SECONDS + _HEALTH_TIMEOUT_SECONDS)
    worst_case_seconds: dict[str, float] = {
        "backup": _GIT_FETCH_TIMEOUT_SECONDS + _LOCAL_TIMEOUT_SECONDS + _IMAGE_PULL_TIMEOUT_SECONDS,
        "pull": _RECREATE_TIMEOUT_SECONDS,
        "recreate": health_wait_worst_case,
        "health": health_wait_worst_case,
        "rollback": _LOCAL_TIMEOUT_SECONDS + _RECREATE_TIMEOUT_SECONDS + health_wait_worst_case,
    }
    assert set(worst_case_seconds) == set(_BUSY_BOUND_SECONDS)
    for phase, worst_case in worst_case_seconds.items():
        assert _BUSY_BOUND_SECONDS[phase] >= worst_case, phase


def test_run_update_actually_measured_stays_within_every_busy_bound(monkeypatch):
    """The general property, executed rather than transcribed - the gap
    `test_every_busy_phase_has_a_bound_that_covers_its_own_actual_worst_case`
    above does not close, despite its own docstring calling it that:
    that test bites against a change to `_BUSY_BOUND_SECONDS`'s formula,
    computed independently from the same raw constants - but not against
    a change to the STEP SEQUENCE those formulas assume. Inserting an
    extra `commands.fetch()` call into `runner.run_update` between
    `write_image_tag()` and `recreate()` left every test in this suite,
    that one included, green: the true `pull` window becomes
    `_RECREATE_TIMEOUT_SECONDS + _GIT_FETCH_TIMEOUT_SECONDS` (120s)
    against a 75s bound, and nothing that only sums constants by hand
    - "reproduced from a full read of `runner.run_update`", a human
    read, not an executed one - can ever notice a step moving.

    This drives `run_update` for real against a fake `Commands` whose
    every method advances a monotonic fake clock by that method's actual
    `commands.py` timeout before returning (the same worst case a real,
    not-yet-timed-out subprocess call can legitimately cost), and a fake
    `sleep` that does the same for `_wait_for_health`'s own
    `sleep(POLL_SECONDS)`. `runner.write_state` is monkeypatched to
    record `(phase, clock_reading)` at every `publish()` call - the
    sequence `run_update` actually produces, not one restated here. The
    gap between two consecutive records is exactly how long `state.json`
    can legitimately sit at the earlier phase's stamp with nothing
    refreshing it - the same question `_BUSY_BOUND_SECONDS` exists to
    answer - so comparing it against that dict reads `runner.py` by
    running it.

    Two runs share one clock-driven `Commands`: a healthy one, whose
    health wait is held false across two refresh cycles before
    succeeding (covering `backup`, `pull`, `recreate` and `health` - the
    last two exercised across an actual republish, not just a single
    poll), and one that never turns healthy (covering `rollback`, whose
    own confirmation wait is exercised the same way). Together every key
    in `_BUSY_BOUND_SECONDS` is observed at least once."""
    clock = {"now": 0.0}
    records: list[tuple[str, float]] = []

    def fake_write_state(_base: object, state: UpdateState) -> None:
        records.append((state.phase, clock["now"]))

    monkeypatch.setattr(runner_module, "write_state", fake_write_state)

    class ClockCommands:
        """Every method advances the shared `clock` by the real timeout
        `commands.py` gives that operation, mirroring the worst case a
        real subprocess call can legitimately cost without actually
        timing out. `write_image_tag`/`read_image_tag` cost nothing, like
        their real counterparts in `commands.DockerCommands` - plain file
        I/O, no subprocess to bound."""

        def __init__(self, *, healthy_after: int) -> None:
            self._healthy_after = healthy_after
            self._health_asked = 0

        def backup(self) -> str:
            clock["now"] += _BACKUP_TIMEOUT_SECONDS
            return "/data/backups/store-x.tgz"

        def fetch(self) -> None:
            clock["now"] += _GIT_FETCH_TIMEOUT_SECONDS

        def checkout(self, ref: str) -> None:
            clock["now"] += _LOCAL_TIMEOUT_SECONDS

        def pull(self, image: str) -> None:
            clock["now"] += _IMAGE_PULL_TIMEOUT_SECONDS

        def recreate(self) -> None:
            clock["now"] += _RECREATE_TIMEOUT_SECONDS

        def healthy(self) -> bool:
            self._health_asked += 1
            clock["now"] += _HEALTH_TIMEOUT_SECONDS
            return self._health_asked > self._healthy_after

        def write_image_tag(self, tag: str) -> None:
            pass

        def read_image_tag(self) -> str:
            return "stable"

        def read_git_ref(self) -> str:
            clock["now"] += _LOCAL_TIMEOUT_SECONDS
            return "v0.2.0"

        def log(self, line: str) -> None:
            pass

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    def request() -> UpdateRequest:
        return UpdateRequest(
            id="clock-1",
            channel="stable",
            target="v0.3.0",
            requested_at="2026-09-09T10:00:00+00:00",
        )

    def deltas_by_phase(run_records: list[tuple[str, float]]) -> dict[str, float]:
        """The max gap this one run observed between a publish of each
        phase and whatever was published right after it - `max` because a
        phase republished across several refreshes (`"health"`, twice
        below) is measured once per gap, and only the largest of those
        gaps is the one `_BUSY_BOUND_SECONDS` has to cover."""
        out: dict[str, float] = {}
        for (phase, start), (_next_phase, end) in itertools.pairwise(run_records):
            out[phase] = max(out.get(phase, 0.0), end - start)
        return out

    # Run 1: healthy only after two full refresh cycles - exercises
    # `backup`, `pull`, `recreate` and `health` (the latter two across a
    # real republish, both at `HEALTH_REFRESH_POLLS` and again at
    # `2 * HEALTH_REFRESH_POLLS`).
    state = run_update(
        request(),
        ClockCommands(healthy_after=2 * HEALTH_REFRESH_POLLS),
        base=Path("/unused"),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=lambda _a, _b: True,
        sleep=fake_sleep,
    )
    assert state.phase == "done"
    observed = deltas_by_phase(records)

    # Run 2: never healthy, deadline reached quickly - exercises
    # `rollback`, whose own confirmation wait republishes the same way.
    clock["now"] = 0.0
    records.clear()
    state = run_update(
        request(),
        ClockCommands(healthy_after=10_000),
        base=Path("/unused"),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=lambda _a, _b: True,
        sleep=fake_sleep,
        deadline_seconds=2 * HEALTH_REFRESH_POLLS * POLL_SECONDS,
    )
    assert state.phase == "failed"
    assert state.rolled_back is True
    for phase, delta in deltas_by_phase(records).items():
        observed[phase] = max(observed.get(phase, 0.0), delta)

    assert set(observed) <= set(_BUSY_BOUND_SECONDS)
    for phase, worst_case in observed.items():
        assert worst_case <= _BUSY_BOUND_SECONDS[phase], phase
    # Every phase this dict makes a promise about was actually measured -
    # a promise never checked is not a promise kept.
    assert set(_BUSY_BOUND_SECONDS) <= set(observed)


@pytest.mark.parametrize("phase", ["backup", "pull", "recreate", "health", "rollback"])
async def test_status_says_updater_present_for_a_busy_phase_within_its_bound(api, phase):
    """The defect this fixes, reproduced directly: a stamp aged past
    `_STALE_AFTER_SECONDS` alone used to read as "no updater installed"
    regardless of `phase` - including for `"backup"`, which `state.json`
    sits at for the entire fetch+checkout+pull window, legitimately many
    minutes on the slow link this feature ships to. `phase` itself is
    evidence the updater wrote this file recently enough to still be
    running that step, up to the phase's own derived bound."""
    client, _, update_dir = api
    age = _BUSY_BOUND_SECONDS[phase] - 5.0
    stamp = (datetime.now(UTC) - timedelta(seconds=age)).isoformat(timespec="microseconds")
    _write_state(update_dir, phase=phase, updater_seen_at=stamp)

    response = await client.get("/api/update/status")
    assert response.json()["updater_present"] is True


@pytest.mark.parametrize("phase", ["backup", "pull", "recreate", "health", "rollback"])
async def test_status_says_no_updater_once_a_busy_phase_ages_past_its_own_bound(api, phase):
    """A phase string must not make presence permanent: a genuinely wedged
    updater (the container died mid-step, say) still has to read as absent
    once its own phase's derived bound has passed, the same as any other
    stale stamp."""
    client, _, update_dir = api
    age = _BUSY_BOUND_SECONDS[phase] + 3600.0
    stamp = (datetime.now(UTC) - timedelta(seconds=age)).isoformat(timespec="microseconds")
    _write_state(update_dir, phase=phase, updater_seen_at=stamp)

    response = await client.get("/api/update/status")
    assert response.json()["updater_present"] is False


async def test_status_says_no_updater_for_a_terminal_phase_past_the_fast_path_alone(api):
    """A terminal phase (`"done"`/`"failed"`/`"idle"`) gets no busy-bound
    widening - nothing is running that could justify one - so it must
    still go stale at the ordinary, short threshold, not linger at a
    phase-specific bound meant for phases that mean something is actually
    in progress."""
    client, _, update_dir = api
    age = _STALE_AFTER_SECONDS + 5.0
    stamp = (datetime.now(UTC) - timedelta(seconds=age)).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="done", updater_seen_at=stamp, healthy=True)

    response = await client.get("/api/update/status")
    assert response.json()["updater_present"] is False


async def test_status_says_no_updater_for_a_future_dated_stamp(api):
    """A negative age satisfies `<= threshold` for any threshold, however
    far in the future the stamp is - a clock that jumped backwards (an
    unsynced RTC on first boot, an NTP correction) must not keep a dead
    sidecar reading as present forever just because nothing could ever
    age the stamp past a bound it is already "younger" than."""
    client, _, update_dir = api
    future_stamp = (datetime.now(UTC) + timedelta(days=3650)).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="idle", updater_seen_at=future_stamp)

    response = await client.get("/api/update/status")
    assert response.json()["updater_present"] is False


async def test_status_localises_a_rejection_the_sidecar_recorded(api):
    """The `dev` ancestry rule is enforced only by the sidecar - a request
    this route's own pre-check let through still gets refused a loop
    interval later, inside `run_update`, and `state.json` is the only
    channel that refusal reaches the operator through (see `api.update`'s
    module docstring and `runner.run_update`'s `except RequestRejected`
    branch). Before this fix, `error` was a verbatim copy of `state.json`'s
    English `error` regardless of the selected language - a German
    operator reading an English sentence inside a German frame, exactly
    what CLAUDE.md forbids."""
    client, store, update_dir = api
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(
        update_dir,
        phase="failed",
        updater_seen_at=fresh_stamp,
        error="target 'deadbee' does not descend from the running commit 'abc1234'",
        error_i18n_key="api.update.rejected.target_not_descendant",
        error_i18n_values={"target": "deadbee", "running_commit": "abc1234"},
    )

    english = await client.get("/api/update/status")
    store.locale.set_language("de")
    german = await client.get("/api/update/status")

    assert english.json()["error"] != german.json()["error"]
    assert "does not descend" in english.json()["error"]
    assert "stammt nicht" in german.json()["error"]


async def test_status_falls_back_to_english_when_the_recorded_key_does_not_resolve(api):
    """`i18n.t()` raises `KeyError` for a key it does not know - a
    `state.json` written by a sidecar image whose `strings.yaml` has moved
    ahead of (or behind) this bridge's own, say. That must not turn a
    routine `GET /update/status` into a 500 over a rejection message that
    would have displayed just fine in English; it must fall back to the
    raw `error` instead, same as a failure with no key at all."""
    client, _, update_dir = api
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(
        update_dir,
        phase="failed",
        updater_seen_at=fresh_stamp,
        error="target 'deadbee' does not descend from the running commit 'abc1234'",
        error_i18n_key="api.update.rejected.this_key_does_not_exist",
        error_i18n_values={"target": "deadbee", "running_commit": "abc1234"},
    )

    response = await client.get("/api/update/status")
    assert response.status_code == 200
    assert response.json()["error"] == (
        "target 'deadbee' does not descend from the running commit 'abc1234'"
    )


async def test_status_leaves_a_keyless_error_in_english(api):
    """Most failures `run_update` can report - a step that raised, a
    health check that timed out, a rollback that itself failed - are
    free-form exception text with nothing in `strings.yaml` to resolve
    against, by design (see `UpdateState`'s own docstring). `error` must
    reach the interface unchanged for those, in either language."""
    client, store, update_dir = api
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(
        update_dir,
        phase="failed",
        updater_seen_at=fresh_stamp,
        error="recreate failed: RuntimeError('recreate failed')",
    )

    store.locale.set_language("de")
    response = await client.get("/api/update/status")
    assert response.json()["error"] == "recreate failed: RuntimeError('recreate failed')"


async def test_status_running_version_comes_from_build_info_not_state_to_version(api, monkeypatch):
    """The module's own docstring: "The running version comes from
    build_info(), not from state.json." `_status_out` reading
    `state.to_version or info.version` instead - the updater's own
    bookkeeping about what it last aimed for, not what is actually
    running, and the exact security defect fixed earlier on this branch -
    would leave every other test in this file green, because none of them
    ever gives `to_version` and `LOXMATTER_VERSION` different values. This
    one deliberately does."""
    client, _, update_dir = api
    monkeypatch.setenv("LOXMATTER_VERSION", "0.4.0")
    fresh_stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_state(update_dir, phase="done", updater_seen_at=fresh_stamp, to_version="0.9.9")

    response = await client.get("/api/update/status")
    assert response.json()["running_version"] == "0.4.0"


async def test_check_is_served_from_cache_on_repeated_calls(api, monkeypatch):
    """Design section 9: cached, not re-run on every call. GitHub allows
    60 unauthenticated requests per hour per IP, and the interface calls
    this route on every channel toggle and every System-tab open - without
    a cache, a few dozen of those in one sitting exhaust the budget and
    the tile is stuck showing "could not check" for the rest of the
    hour."""
    client, _, _update_dir = api
    calls = 0

    async def fake_query(_channel, _info):
        nonlocal calls
        calls += 1
        return "v9.9.9", True

    monkeypatch.setattr(update_module, "_query_github", fake_query)

    first = await client.get("/api/update/check")
    second = await client.get("/api/update/check")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert calls == 1


async def test_check_queries_again_once_the_cache_expires(api, monkeypatch):
    """Bite check for the test above: proves the cache actually consults
    `_CHECK_CACHE_SECONDS` rather than caching forever regardless of it -
    a cache that ignored the lifetime entirely would also pass the
    "one call for two requests" test above."""
    client, _, _update_dir = api
    calls = 0

    async def fake_query(_channel, _info):
        nonlocal calls
        calls += 1
        return "v9.9.9", True

    monkeypatch.setattr(update_module, "_query_github", fake_query)
    monkeypatch.setattr(update_module, "_CHECK_CACHE_SECONDS", 0.0)

    await client.get("/api/update/check")
    await client.get("/api/update/check")

    assert calls == 2


async def test_check_cache_is_keyed_by_channel(api, monkeypatch):
    """Every existing cache test above calls this route twice without ever
    changing the selected channel, so a cache keyed by a single constant
    instead of `channel` - `check_cache = {}` used as a one-slot cache
    rather than `dict[UpdateChannel, ...]` - would leave every one of them
    green too. The interface re-checks on every channel toggle (design
    section 9); a cache that served `stable`'s cached answer to a `dev`
    request would show the wrong `available`/`latest` for the full fifteen
    minutes of `_CHECK_CACHE_SECONDS`, to an operator who just switched the
    toggle specifically to see the `dev` answer."""
    client, store, _update_dir = api
    calls: list[UpdateChannel] = []

    async def fake_query(channel, _info):
        calls.append(channel)
        if channel is UpdateChannel.DEV:
            return "deadbee", True
        return "v9.9.9", False

    monkeypatch.setattr(update_module, "_query_github", fake_query)

    store.update_settings.set_channel(UpdateChannel.STABLE)
    stable = await client.get("/api/update/check")
    store.update_settings.set_channel(UpdateChannel.DEV)
    dev = await client.get("/api/update/check")

    assert calls == [UpdateChannel.STABLE, UpdateChannel.DEV]
    assert stable.json()["channel"] != dev.json()["channel"]
    assert stable.json()["available"] != dev.json()["available"]
    assert stable.json()["latest"] != dev.json()["latest"]

    # Also proves the cache still WORKS per channel, not merely that it is
    # not shared - a second call on `dev` must not ask GitHub again.
    store.update_settings.set_channel(UpdateChannel.DEV)
    await client.get("/api/update/check")
    assert calls == [UpdateChannel.STABLE, UpdateChannel.DEV]


async def test_check_caches_an_unavailable_result_too(api, monkeypatch):
    """A firewalled installation must not retry GitHub on every single
    call either - that is the exact flood this cache exists to stop, and
    caching only successes would leave it wide open on every installation
    that has no internet access at all."""
    client, _, _update_dir = api
    calls = 0

    async def failing_query(_channel, _info):
        nonlocal calls
        calls += 1
        raise update_module._CheckUnavailable("network unreachable")

    monkeypatch.setattr(update_module, "_query_github", failing_query)

    first = await client.get("/api/update/check")
    second = await client.get("/api/update/check")

    assert first.json()["error"] is not None
    assert first.json() == second.json()
    assert calls == 1


async def test_every_update_route_is_behind_the_api_guard(api_without_session):
    """Session or token, like everything under /api. No second password
    prompt - the same session already downloads the Fabric backup, and a
    prompt here would claim a boundary that does not exist elsewhere."""
    client = api_without_session

    assert (await client.get("/api/update/status")).status_code == 401
    assert (await client.get("/api/update/check")).status_code == 401
    assert (
        await client.post("/api/update/apply", json={"channel": "stable", "target": "v9.9.9"})
    ).status_code == 401
