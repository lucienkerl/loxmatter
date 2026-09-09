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
10 and 11.

`api` builds the app with its own, throwaway `update` directory (via
`build_app`'s `update_base`) instead of the real `/data/update` mount -
tests write `state.json` into it directly with `update.protocol.write_state`
to control exactly what the route reads, the same way `test_settings_api.py`
writes straight to the store instead of going through a second route.

`GET /api/update/check` reaches GitHub and is deliberately not exercised
here beyond the guard test: it has no dedicated test in this task's brief,
and a test that actually hit the network would be flaky in CI for reasons
that have nothing to do with this route's own logic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.api.update import _BUSY_BOUND_SECONDS, _STALE_AFTER_SECONDS
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.update.commands import (
    _GIT_FETCH_TIMEOUT_SECONDS,
    _HEALTH_TIMEOUT_SECONDS,
    _IMAGE_PULL_TIMEOUT_SECONDS,
    _LOCAL_TIMEOUT_SECONDS,
    _RECREATE_TIMEOUT_SECONDS,
)
from loxmatter.update.protocol import UpdateState, read_request, write_state
from loxmatter.update.runner import HEALTH_REFRESH_POLLS, POLL_SECONDS


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
