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

"""The "last saved" stamp of a project file, which a patched file should
carry forward to the moment loxmatter changed it.

Loxone Config writes it as two attributes of `<C Type="Document">`:

* `Date="2026-09-23 23:13:53"` - the wall-clock time of the computer that
  saved the file, without any zone.
* `DateS="559430033"` - the same moment as seconds since 2009-01-01
  00:00 UTC, the epoch the Miniserver counts from.

Neither is documented. Both readings were checked against 150 real
project files (2016 to 2026): `DateS` read that way reproduces `Date`
exactly, shifted by the saving computer's UTC offset (+1 h in winter,
+2 h in summer for a German installation), and `Date` lies within
seconds to minutes of each file's mtime. `CDate` and `BDate`, which
follow `Date`, are left alone: `BDate` was empty in every file, and
`CDate` is sometimes earlier and sometimes later than `Date`, so it is
not a second "last changed" field."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

__all__ = ["LOXONE_EPOCH", "recorded_utc_offset", "saved_date_attrs"]

LOXONE_EPOCH = datetime(2009, 1, 1, tzinfo=UTC)

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# The widest UTC offsets in use are -12 h and +14 h. A difference beyond
# that means the two attributes do not describe the same moment (a file
# edited by hand, or one attribute left over from a copy), not a zone.
_MAX_OFFSET = timedelta(hours=14)


def recorded_utc_offset(document_attrs: Mapping[str, str]) -> timedelta | None:
    """The UTC offset of the computer that last saved the file, read as the
    difference between `Date` and `DateS`, rounded to whole minutes -
    or `None` if either attribute is missing or unreadable."""
    try:
        # Read as if it were UTC, so that the difference below is the
        # offset: `Date` itself carries no zone.
        wall_clock = datetime.strptime(document_attrs["Date"], _DATE_FORMAT).replace(tzinfo=UTC)
        seconds = int(document_attrs["DateS"])
    except (KeyError, ValueError):
        return None
    offset = wall_clock - (LOXONE_EPOCH + timedelta(seconds=seconds))
    minutes = round(offset.total_seconds() / 60)
    offset = timedelta(minutes=minutes)
    return offset if abs(offset) <= _MAX_OFFSET else None


def saved_date_attrs(saved_at: datetime) -> dict[str, str]:
    """`Date` and `DateS` for a file saved at `saved_at`, which must carry
    the zone the user reads `Date` in: `Date` is written in that zone,
    `DateS` in UTC, as Loxone Config does."""
    if saved_at.tzinfo is None:
        raise ValueError("saved_at needs a time zone: Date is wall-clock time, DateS is UTC")
    seconds = int((saved_at - LOXONE_EPOCH).total_seconds())
    return {"Date": saved_at.strftime(_DATE_FORMAT), "DateS": str(seconds)}
