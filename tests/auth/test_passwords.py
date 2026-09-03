# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Tests fuer das Passwort-Hashing (Spec 6).

Die Kernfrage: passt das richtige Passwort, faellt jedes andere durch, und
verkraftet `verify_password` einen kaputten oder fremden Hash, ohne zu
werfen? Der letzte Punkt ist kein Randfall: der Wert kommt aus einer Datei,
die ein Betreiber von Hand bearbeitet haben kann.
"""

from __future__ import annotations

import hashlib

from loxmatter.auth.passwords import hash_password, verify_password


def test_the_right_password_verifies():
    stored = hash_password("richtig-und-lang-genug")
    assert verify_password("richtig-und-lang-genug", stored) is True


def test_a_wrong_password_does_not_verify():
    stored = hash_password("richtig-und-lang-genug")
    assert verify_password("falsch-und-lang-genug", stored) is False


def test_the_same_password_hashes_differently_every_time():
    """Sonst waere das Salz keins - zwei Installationen mit demselben
    Passwort haetten denselben Hash."""
    assert hash_password("gleiches-passwort") != hash_password("gleiches-passwort")


def test_the_stored_form_names_its_scheme_and_parameters():
    stored = hash_password("egal-hauptsache-lang")
    scheme, n, r, p, salt, key = stored.split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**14, 8, 1)
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(key)) == 32


def test_a_hash_with_other_parameters_still_verifies():
    """Der Grund, warum die Parameter im Wert stehen: ein spaeterer Wechsel
    der Kostenfaktoren darf alte Hashes nicht entwerten.

    Der Vergleichswert wird hier mit ANDEREN Kostenfaktoren (n = 1024) selbst
    gerechnet, nicht mit denen des Moduls - sonst pruefte der Test nur, dass
    eine Konstante mit sich selbst uebereinstimmt."""
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    key = hashlib.scrypt(b"geheim-und-lang", salt=salt, n=1024, r=8, p=1, dklen=32)
    stored = f"scrypt$1024$8$1${salt.hex()}${key.hex()}"
    assert verify_password("geheim-und-lang", stored) is True


def test_a_broken_stored_value_never_raises():
    for kaputt in ["", "keinDollar", "scrypt$1$2", "argon2$1$2$3$4$5", "scrypt$a$b$c$d$e"]:
        assert verify_password("irgendwas", kaputt) is False
