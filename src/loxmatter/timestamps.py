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

"""A single timestamp helper for the whole codebase.

`model.store`, `loxone.sender` and `loxone.server` each used to carry their
own, word-for-word copy of this function - each with its own comment
arguing why a shared dependency would supposedly cost more coupling than it
saved. For a single line of code that is not needed anywhere else, that was
already a thin justification with two copies; with THREE (review fix minor,
2026-09-02) any future change to the timestamp format (e.g. to the
precision) would have had to stay in sync across three places.
`model.store.Store._now` remains here as a thin, one-line bridge to
`now_iso` - not out of fear of coupling, but because `self._now()` is
already wired in at many places in that class, and a plain rename of every
caller would have offered no benefit over the bridge."""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """ISO 8601 timestamp in UTC, with microseconds.

    Fixed at `timespec="microseconds"` so that two timestamps taken in
    quick succession (e.g. an export, then immediately a rename) stay
    reliably comparable as text in the same order as chronologically -
    without this, `datetime.isoformat()` would drop the fractional seconds
    whenever a timestamp happened to land on an exact second, which could
    produce two timestamps of different lengths."""
    return datetime.now(UTC).isoformat(timespec="microseconds")
