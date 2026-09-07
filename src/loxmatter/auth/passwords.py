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

"""Password hashing with `hashlib.scrypt` (spec 6).

**Why scrypt and not Argon2 or bcrypt:** both would need a new runtime
dependency (`argon2-cffi` or `passlib` respectively) for exactly one hash
in this project. scrypt is memory-hard, in the standard library, and
sufficient for this purpose. The dependency list in `pyproject.toml` stays
unchanged as a result.

**Why the parameters live in the stored value** (`scrypt$n$r$p$salt$hash`):
if the cost factors are raised later, already-stored hashes must remain
checkable - otherwise an update would lock the operator out of their own
bridge. `verify_password` therefore reads the parameters from the value
itself, not from this module's constants.

scrypt's memory requirement is 128 * n * r, so 16 MiB here. That is below
the threshold that `hashlib.scrypt` allows through without a `maxmem` set
(32 MiB) - which is why no `maxmem` argument appears there.
"""

from __future__ import annotations

import hashlib
import secrets

# Not a value pulled out of a security vacuum, but the usual interactive
# working point for scrypt: about 16 MiB of memory and a fraction of a
# second per check. Set any higher, every login would become noticeably
# sluggish on a Raspberry Pi.
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_SCHEME = "scrypt"

# Shorter, and a password would no longer be saved by a throttle of 30
# seconds per five attempts (see `throttle`). Mandating longer tends, in
# practice, to end up as a note stuck to the screen.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Computes the value to store - with a fresh salt on every call."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Checks `password` against a stored value.

    Simply returns `False` for any unreadable, foreign, or corrupted
    `stored` value instead of raising: the value comes from a file on the
    operator's disk, and a typo in it should produce a 401, not a 500
    with a traceback in the log."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return False
    _, n, r, p, salt_hex, key_hex = parts
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        # Unreadable hex characters, nonsensical parameters (n not a power
        # of two, dklen 0) - all the same case: this value is not a hash.
        return False
    return secrets.compare_digest(key, expected)
