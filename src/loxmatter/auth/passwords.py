"""Passwort-Hashing mit `hashlib.scrypt` (Spec 6).

**Warum scrypt und nicht Argon2 oder bcrypt:** beide brauchten eine neue
Laufzeitabhaengigkeit (`argon2-cffi` bzw. `passlib`) fuer genau einen Hash in
diesem Projekt. scrypt ist speicherhart, in der Standardbibliothek und fuer
diesen Zweck ausreichend. Die Abhaengigkeitsliste in `pyproject.toml` bleibt
dadurch unveraendert.

**Warum die Parameter im gespeicherten Wert stehen** (`scrypt$n$r$p$salt$hash`):
werden die Kostenfaktoren spaeter angehoben, muessen bereits abgelegte Hashes
weiter pruefbar bleiben - sonst sperrt ein Update den Betreiber aus seiner
eigenen Bruecke aus. `verify_password` liest deshalb die Parameter aus dem
Wert und nicht aus den Konstanten dieses Moduls.

Der Speicherbedarf von scrypt ist 128 * n * r, hier also 16 MiB. Das liegt
unter der Vorgabe, die `hashlib.scrypt` ohne gesetztes `maxmem` durchlaesst
(32 MiB) - deshalb steht dort kein `maxmem`-Argument.
"""

from __future__ import annotations

import hashlib
import secrets

# Kein Wert aus einem Sicherheitsvakuum, sondern der uebliche interaktive
# Arbeitspunkt fuer scrypt: rund 16 MiB Speicher und ein Bruchteil einer
# Sekunde je Pruefung. Hoeher gesetzt wuerde jeder Login auf einem
# Raspberry Pi spuerbar traege.
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_SCHEME = "scrypt"

# Kuerzer waere ein Passwort, das eine Drosselung von 30 Sekunden je fuenf
# Versuchen nicht mehr rettet (siehe `throttle`). Laenger vorzuschreiben
# fuehrt erfahrungsgemaess zu einem Zettel am Bildschirm.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Rechnet den abzulegenden Wert - mit frischem Salz bei jedem Aufruf."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Prueft `password` gegen einen abgelegten Wert.

    Gibt bei jedem unlesbaren, fremden oder verstuemmelten `stored` schlicht
    `False` zurueck, statt zu werfen: der Wert kommt aus einer Datei auf der
    Platte des Betreibers, und ein Tippfehler darin soll einen 401 ergeben,
    keinen 500 mit Traceback im Log."""
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
        # Unleserliche Hex-Zeichen, unsinnige Parameter (n keine Zweierpotenz,
        # dklen 0) - alles derselbe Fall: dieser Wert ist kein Hash.
        return False
    return secrets.compare_digest(key, expected)
