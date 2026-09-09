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

"""The file side of the update - design "Applying updates through the
web UI" (2026-09-08), section 7.

The bridge and the sidecar communicate exclusively through files in a
shared volume - no network, no socket, no shared library. The reason is
not frugality: the sidecar survives the bridge's restart, and because the
state lives in a file instead of in memory, the web UI can simply keep
reading after the restart. That is exactly the gap one was blind to
before this design.

This module knows no HTTP concepts and makes no network calls -
`update_check.py` handles the network, `api/update.py` the HTTP. That way
each can be tested on its own.

The exact file formats below were re-checked against `deploy/updater/
update-once.sh` (the only other participant in this protocol) rather than
taken as given, since that script has been through several review rounds
since this module was first sketched. It matches: state.json's fields are
exactly `id`, `phase`, `from`, `to`, `error`, `rolled_back`,
`rolled_back_to`, `healthy` and `updater_seen_at` (see `set_state()`
there), and the phases that count as
"still running" are exactly `queued`, `backup`, `pull`, `recreate`,
`health` and `rollback` - every other phase (`idle`, `rejected`, `done`,
`failed`) is an end state that allows a new request.

`phase` alone is not the whole story, though: the sidecar only updates
`state.json` from its two-second poll loop, so there is a real window -
between a request being written and that poll waking up - in which
`state.json` still reports whatever end state preceded it. A second
`request_update()` call inside that window would see the same "not
running" phase the first call saw, and its `os.replace` would silently
overwrite `request.json` before the sidecar ever looked at it: the first
job's id, already handed back to its caller, is then never written
anywhere else, never logged, and never marked handled - it simply
vanishes. `request_update()` closes that window itself (see
`_pending_job_id()`) by also checking whether an existing request.json
names a job that state.json has not yet caught up to and that
`handled/<job-id>` (see below) does not yet cover; if so, it is treated
as busy the same way a running phase is. The `handled/<job-id>` marker
the sidecar keeps (added to survive a `state.json` reset that would
otherwise replay a finished job, written the instant a request is
accepted - see update-once.sh's `set_state queued ""` and the marker
write right after it) is what lets that check tell "already picked up,
whatever state.json currently says" apart from "still sitting there
unread."
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Phases in which a request is still running. Everything else is an end
# state (or `idle`) and allows a new request. Copied from update-once.sh's
# own phase names - `queued` (accepted, not yet started), `backup`,
# `pull`, `recreate`, `health` (the normal forward path) and `rollback`
# (the recovery path after a failed health check). `rejected` is
# deliberately NOT in this set: a rejection is a completed judgment about
# a bad request, not work in progress, and the point of surfacing it
# instead of silently discarding it is exactly so a corrected resubmission
# is not needlessly blocked.
_RUNNING_PHASES = frozenset({"queued", "backup", "pull", "recreate", "health", "rollback"})

# The sidecar refreshes its heartbeat every two seconds (update-once.sh's
# main loop, driven by entrypoint.sh). Thirty seconds of silence is
# generous enough to absorb one slow pass on a loaded Pi, and short enough
# that nobody is shown a button for long that nothing would answer.
_MAX_SILENT_SECONDS = 30

# The sidecar writes this exact strftime shape (see update-once.sh's
# `now()`: `date -u +%Y-%m-%dT%H:%M:%SZ`) - always UTC, always with a
# literal trailing "Z", never fractional seconds. Parsed with the matching
# `strptime` format below rather than a general ISO-8601 parser, so a
# timestamp in a shape the sidecar would never actually produce is treated
# as unparseable rather than guessed at.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class UpdateBusyError(RuntimeError):
    """Raised by `request_update` when a request is already in flight."""


@dataclass(frozen=True)
class UpdateState:
    """A snapshot of the sidecar's state.json - see the module docstring
    for where each field comes from and what it means."""

    phase: str
    id: str | None
    from_version: str | None
    to_version: str | None
    error: str | None
    rolled_back: bool
    # The concrete version a rollback restored (update-once.sh's own
    # $BACK - see its comment in the rollback section there), never the
    # possibly-aliased `from_version` above. `None` whenever `rolled_back`
    # is false (no rollback ran) and, defensively, if it ever is true
    # against an older sidecar build that predates this field.
    rolled_back_to: str | None
    healthy: bool
    updater_seen_at: str | None


def _as_optional_str(value: object) -> str | None:
    """Coerce a raw JSON value to `str | None`, defensively.

    The sidecar writes `id`/`from`/`to`/`error`/`updater_seen_at` through
    jq's `--arg`, which always stringifies - so in a well-formed
    state.json these are always a JSON string or `null`. A hand-edited or
    otherwise corrupted file could carry some other JSON type under one of
    these keys (a number, a list, an object); `UpdateState` promises
    `str | None` for each of them, and Task 8 builds its HTTP response
    model directly off that promise. Letting a value of the wrong type
    through here - rather than falling back to `None` - would turn a
    typed field into a de facto `Any` the moment the file on disk drifted
    from the shape this module expects.
    """
    return value if isinstance(value, str) else None


def read_state(update_dir: Path) -> UpdateState | None:
    """The most recently written state, or `None`.

    Every failure mode below - a missing file, a truncated one, valid JSON
    that is not an object, a file this process cannot open - is folded
    into the same `None` result rather than raised as an exception: the
    web UI polls this once a second, and a single failure here would look
    there exactly like a broken update rather than like a momentary read
    hiccup. The sidecar writes atomically (temp file, then `mv`), so a
    half-written file is the exception here, not the rule - but it is one
    that does not deserve to bubble up as a 500 in Task 8.
    """
    try:
        raw = json.loads((update_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return UpdateState(
        phase=_as_optional_str(raw.get("phase")) or "idle",
        id=_as_optional_str(raw.get("id")),
        from_version=_as_optional_str(raw.get("from")),
        to_version=_as_optional_str(raw.get("to")),
        error=_as_optional_str(raw.get("error")),
        rolled_back=bool(raw.get("rolled_back")),
        rolled_back_to=_as_optional_str(raw.get("rolled_back_to")),
        # `True` is not a claim that anything is healthy - it mirrors the
        # sidecar's own default (update-once.sh initialises HEALTHY=true
        # ahead of validation and only ever sets it false once a health
        # check has actually failed). Absence of the key here means the
        # same thing it means there: nothing has said otherwise yet.
        healthy=bool(raw.get("healthy", True)),
        updater_seen_at=_as_optional_str(raw.get("updater_seen_at")),
    )


def read_log(update_dir: Path, lines: int = 40) -> list[str]:
    """The last `lines` lines of the sidecar's log.txt, or `[]`.

    Best-effort, like `read_state`: a missing or unreadable log is not a
    reason to fail a request for the update card, only a reason to show
    an empty log.
    """
    try:
        text = (update_dir / "log.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def updater_present(
    state: UpdateState | None,
    *,
    now: datetime,
    max_age_seconds: int = _MAX_SILENT_SECONDS,
) -> bool:
    """Whether anyone would actually pick up a request right now.

    Without this judgment the web UI would show, on an installation that
    has since dropped the sidecar from its Compose file (or never had one
    added), a button that does nothing and reports nothing either - the
    second failure mode named in this task's own brief, and just as bad as
    the first (hiding a working button).
    """
    if state is None or not state.updater_seen_at:
        return False
    try:
        seen = datetime.strptime(state.updater_seen_at, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        # A timestamp the sidecar would never actually produce (hand
        # edited, truncated, a different format entirely) is exactly as
        # informative as no timestamp at all - fail closed, the same
        # direction the sidecar's own validation takes throughout
        # update-once.sh (an inconclusive answer is a "no", never waved
        # through as a "sure, why not").
        return False
    # `abs`, not a one-sided "is `seen` not too far in the PAST"
    # comparison: the sidecar and the bridge run in containers on the same
    # host and share the same clock, so a heartbeat noticeably AHEAD of
    # `now` is not a sidecar reporting from the future - it is a clock
    # that jumped (an NTP correction, a container restarting with a
    # drifted RTC) or a state.json edited by hand. A one-sided check would
    # treat that as "present", permanently, regardless of how stale the
    # real heartbeat becomes afterward - exactly the false positive this
    # function exists to prevent: a button that writes into a volume
    # nobody is reading anymore. Bounding both directions by the same
    # window catches a future timestamp the same way it already catches a
    # stale one.
    return abs((now - seen).total_seconds()) <= max_age_seconds


def _pending_job_id(update_dir: Path, state: UpdateState | None) -> str | None:
    """The id of a request the sidecar has not yet caught up to, or `None`.

    `state.phase` only reflects reality once the sidecar's two-second
    poll has woken up and read `request.json`; see the module docstring
    for the overwrite this closes. A request counts as pending exactly
    when `request.json` exists, carries a readable id, that id is not the
    one `state.json` currently reports, and that id has no
    `handled/<job-id>` marker - `handled/` being the sidecar's own record
    of "already accepted," written at acceptance time regardless of
    whether the job has since finished (update-once.sh, right after
    `set_state queued ""`). Any of those three not holding means either
    the sidecar has already reflected this request in state.json, or has
    already accepted it into its pipeline (marker present) even if
    state.json was since reset - in both cases nothing would be lost by
    overwriting request.json now.

    A `request.json` that cannot be parsed, is not a JSON object, or
    carries no string id does NOT count as pending. The sidecar's own
    "request is not readable" branch treats such a file identically: it
    synthesises an id from the file's mtime and size and rejects it
    outright, never accepting it and never marking it handled. Blocking
    new requests on a file the sidecar itself will only ever reject would
    trade one lost job for a caller permanently locked out by a request
    nobody is ever going to act on - a strictly worse failure.
    """
    try:
        raw = json.loads((update_dir / "request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    if state is not None and job_id == state.id:
        return None
    # `handled/` is created by the sidecar on first run (`mkdir -p
    # "$UPDATE_DIR/handled"`), but this module must not assume it has run
    # even once yet - a fresh installation, or a bridge started before
    # the sidecar's own first pass, has no `handled/` directory at all.
    # `Path.exists()` on a missing parent simply returns `False`, which is
    # exactly the right answer here: no marker directory means no id has
    # ever been marked handled.
    if (update_dir / "handled" / job_id).exists():
        return None
    return job_id


def request_update(update_dir: Path, *, channel: str, target: str) -> str:
    """Deposit a request for the sidecar and return its `id`.

    Written atomically (temp file, then `os.replace`) for the same reason
    the sidecar itself writes state.json atomically: it reads request.json
    every two seconds, and a half-written file would be rejected as
    invalid - permanently, since a request the sidecar marks handled
    (its `handled/<job-id>` marker, written the moment a request is
    accepted) is never looked at again. `os.replace` is POSIX-atomic
    within one filesystem, which the shared update volume always is here.

    Raises `UpdateBusyError` if the sidecar's last known phase is one of
    the running phases (see `_RUNNING_PHASES`), or if `request.json`
    already names a request the sidecar has not yet caught up to (see
    `_pending_job_id()`) - in both cases, writing a second request over
    the first would simply lose the first one, since the sidecar only
    ever looks at request.json's current content, not a queue of past
    versions of it.

    What this function deliberately does NOT do is validate `target`. The
    sidecar validates it (update-once.sh, "Rule 1"/"Rule 2"/"Rule 3": a
    channel enum, a version or commit pattern, forward-only), because the
    sidecar is the security boundary for this whole protocol (design
    section 10) - it is the one process with the privileges to touch the
    Compose stack, and a boundary that trusts its caller to have already
    checked is not a boundary at all. This function is only the mailbox:
    it takes what it is given, stamps it with a fresh id and a timestamp,
    and hands it over.
    """
    state = read_state(update_dir)
    if state is not None and state.phase in _RUNNING_PHASES:
        raise UpdateBusyError(state.phase)
    pending = _pending_job_id(update_dir, state)
    if pending is not None:
        raise UpdateBusyError(pending)

    job_id = str(uuid.uuid4())
    body = {
        "id": job_id,
        "channel": channel,
        "target": target,
        "requested_at": datetime.now(UTC).strftime(_TIMESTAMP_FORMAT),
    }
    update_dir.mkdir(parents=True, exist_ok=True)
    temp = update_dir / "request.json.tmp"
    temp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temp, update_dir / "request.json")
    return job_id
