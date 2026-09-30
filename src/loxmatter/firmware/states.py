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

"""The pure rules of firmware updates (design 2026-09-30, sections 5 and 9.3).

No I/O here: everything the overview shows is decided by these functions,
so the table in section 5 is tested row by row without a server."""

from __future__ import annotations

from typing import Final

AVAILABLE: Final = "available"
NONE_FOUND: Final = "none_found"
NO_SOURCE: Final = "no_source"
UNCHECKED: Final = "unchecked"
CHECK_FAILED: Final = "check_failed"
TRANSFERRING: Final = "transferring"
APPLYING: Final = "applying"
STALLED: Final = "stalled"
FAILED: Final = "failed"
INTERRUPTED: Final = "interrupted"

# A job in one of these is still running; the others have ended.
ACTIVE_JOB_STATES: Final = frozenset({TRANSFERRING, APPLYING, STALLED})
ENDED_JOB_STATES: Final = frozenset({FAILED, INTERRUPTED})

# Matter's UpdateStateEnum (OTA Software Update Requestor, 0/42/2):
# 0 Unknown, 1 Idle, 2 Querying, 3 DelayedOnQuery, 4 Downloading,
# 5 Applying, 6 DelayedOnApply, 7 RollingBack, 8 DelayedOnUserConsent.
_TRANSFERRING_STATES: Final = frozenset({2, 3, 4, 8})
_APPLYING_STATES: Final = frozenset({5, 6, 7})

# Shown for a Matter device that carries no SpecificationVersion: the
# attribute exists since Matter 1.3, so the device implements 1.2 or older.
# The WebUI translates this token; the exact older version is not guessed.
SPEC_VERSION_BEFORE_1_3: Final = "<1.3"


def job_state_for(update_state: int | None) -> str | None:
    """The job state an `UpdateState` stands for, `None` for Idle/Unknown."""
    if update_state in _TRANSFERRING_STATES:
        return TRANSFERRING
    if update_state in _APPLYING_STATES:
        return APPLYING
    return None


def derive_state(
    *,
    has_requestor: bool,
    installed: int | None,
    checked_at: str | None,
    check_error: str | None,
    offer_version: int | None,
    job_state: str | None,
) -> str:
    """One device's state in the update overview (design section 5).

    A running job wins over everything, an ended one is shown until the
    next check clears it. `none_found` never claims "up to date": the DCL
    cannot tell a current device from one whose manufacturer publishes
    nothing (design section 4)."""
    if job_state in ACTIVE_JOB_STATES or job_state in ENDED_JOB_STATES:
        return job_state
    if not has_requestor:
        return NO_SOURCE
    if checked_at is None:
        return UNCHECKED
    if check_error is not None:
        return CHECK_FAILED
    if offer_version is not None and (installed is None or offer_version > installed):
        return AVAILABLE
    return NONE_FOUND


def format_spec_version(raw: int | None, technology: str) -> str | None:
    """`SpecificationVersion` as a person reads it (design section 9.3).

    Encoded as 0xMMmmPP00 - major, minor, patch, and a reserved low byte.
    `1.4` when the patch is 0, else `1.4.1`. `None` for a non-Matter
    device, `SPEC_VERSION_BEFORE_1_3` for a Matter device without it."""
    if technology != "matter":
        return None
    if raw is None:
        return SPEC_VERSION_BEFORE_1_3
    major = (raw >> 24) & 0xFF
    minor = (raw >> 16) & 0xFF
    patch = (raw >> 8) & 0xFF
    return f"{major}.{minor}" if patch == 0 else f"{major}.{minor}.{patch}"
