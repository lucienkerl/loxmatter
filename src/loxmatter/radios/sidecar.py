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
"""The bridge's half of the radios job in the updater sidecar (design
2026-09-11 "Radios in the Web UI", sections 6 and 7).

The same shape as `loxmatter.update`: the bridge writes one request file
atomically, the sidecar writes a state file atomically, and nothing else
connects them. Every read failure folds into `None`, because the settings
card polls this and a momentary read hiccup must not look like a broken
sidecar.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from loxmatter import update

TERMINAL_PHASES: Final = frozenset({"idle", "done", "failed", "rejected", "unchanged"})

SidecarStatus = Literal["ready", "missing", "outdated", "unmounted"]


class RadiosBusyError(RuntimeError):
    """An update or another radio job is running, or a request is still
    waiting to be picked up."""


@dataclass(frozen=True)
class RadioConfig:
    thread_enabled: bool
    thread_device: str | None
    bluetooth_adapter: int
    otbr_running: bool


@dataclass(frozen=True)
class ThreadRequest:
    """One half of a request. Passing `None` instead of this in
    `request_radios` is what says "leave Thread alone" - see there."""

    enabled: bool
    device: str | None


@dataclass(frozen=True)
class BluetoothRequest:
    """The Bluetooth counterpart of `ThreadRequest`."""

    adapter: int


@dataclass(frozen=True)
class RadiosState:
    id: str | None
    phase: str
    steps: tuple[str, ...]
    error: str | None
    rolled_back: bool
    healthy: bool | None
    current: RadioConfig | None
    capable: bool
    capable_reason: str | None
    seen_at: str | None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _config(raw: object) -> RadioConfig | None:
    if not isinstance(raw, dict):
        return None
    enabled, device = raw.get("thread_enabled"), raw.get("thread_device")
    adapter, running = raw.get("bluetooth_adapter"), raw.get("otbr_running")
    if not isinstance(enabled, bool) or not isinstance(running, bool):
        return None
    if isinstance(adapter, bool) or not isinstance(adapter, int):
        return None
    if device is not None and not isinstance(device, str):
        return None
    return RadioConfig(
        thread_enabled=enabled,
        thread_device=device or None,
        bluetooth_adapter=adapter,
        otbr_running=running,
    )


def read_radios_state(update_dir: Path) -> RadiosState | None:
    try:
        raw = json.loads((update_dir / "radios-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    steps = raw.get("steps")
    healthy = raw.get("healthy")
    return RadiosState(
        id=_opt_str(raw.get("id")),
        phase=_opt_str(raw.get("phase")) or "idle",
        steps=tuple(s for s in steps if isinstance(s, str)) if isinstance(steps, list) else (),
        error=_opt_str(raw.get("error")),
        rolled_back=raw.get("rolled_back") is True,
        healthy=healthy if isinstance(healthy, bool) else None,
        current=_config(raw.get("current")),
        capable=raw.get("capable") is True,
        capable_reason=_opt_str(raw.get("capable_reason")),
        seen_at=_opt_str(raw.get("seen_at")),
    )


def _seen_recently(seen_at: str | None, now: datetime) -> bool:
    """The same window and the same both-directions rule as
    `update.updater_present` - see its docstring for why a timestamp ahead
    of `now` counts as stale too."""
    if not seen_at:
        return False
    try:
        seen = datetime.strptime(seen_at, update._TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return False
    return abs((now - seen).total_seconds()) <= update._MAX_SILENT_SECONDS


def report_is_fresh(state: RadiosState | None, *, now: datetime) -> bool:
    """Whether the sidecar wrote this report within the heartbeat window -
    the window `sidecar_status` uses for "outdated"."""
    return state is not None and _seen_recently(state.seen_at, now)


def change_in_progress(update_dir: Path, state: RadiosState | None) -> bool:
    """A radios job that has not reached a terminal phase, or a request the
    sidecar has not picked up yet - the two conditions `request_radios`
    refuses a new request for. While either holds, `current` may be about
    to stop being true."""
    return (state is not None and state.phase not in TERMINAL_PHASES) or _pending(update_dir, state)


def sidecar_status(
    update_state: update.UpdateState | None,
    radios_state: RadiosState | None,
    *,
    now: datetime,
) -> SidecarStatus:
    if not update.updater_present(update_state, now=now):
        return "missing"
    if radios_state is None or not _seen_recently(radios_state.seen_at, now):
        return "outdated"
    if not radios_state.capable:
        return "unmounted"
    return "ready"


def _pending(update_dir: Path, state: RadiosState | None) -> bool:
    try:
        raw = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    request_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(request_id, str) or not request_id:
        return False
    if state is not None and state.id == request_id:
        return False
    return not (update_dir / "radios-handled" / request_id).exists()


def request_radios(
    update_dir: Path,
    *,
    thread: ThreadRequest | None,
    bluetooth: BluetoothRequest | None,
) -> str:
    """Writes one request file. A half given as `None` is written as a JSON
    `null`, which `radios-once.sh` reads as "do not touch this radio": it
    validates nothing about it, writes none of its `.env` keys, and neither
    applies nor verifies it (design section 6.3).

    Both keys are always written, `null` or not, so the sidecar's schema
    check stays the strict whitelist it has always been - only the value
    type widened. A request with both halves `null` ends `unchanged`."""
    update_state = update.read_state(update_dir)
    if update_state is not None and update_state.phase in update._RUNNING_PHASES:
        raise RadiosBusyError(update_state.phase)
    state = read_radios_state(update_dir)
    if state is not None and state.phase not in TERMINAL_PHASES:
        raise RadiosBusyError(state.phase)
    if _pending(update_dir, state):
        raise RadiosBusyError("pending")

    job_id = str(uuid.uuid4())
    body = {
        "id": job_id,
        "thread": None if thread is None else {"enabled": thread.enabled, "device": thread.device},
        "bluetooth": None if bluetooth is None else {"adapter": bluetooth.adapter},
        "requested_at": datetime.now(UTC).strftime(update._TIMESTAMP_FORMAT),
    }
    update_dir.mkdir(parents=True, exist_ok=True)
    temp = update_dir / "radios-request.json.tmp"
    temp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temp, update_dir / "radios-request.json")
    return job_id
