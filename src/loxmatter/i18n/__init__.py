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

"""Translation mechanism: a flat YAML table (`strings.yaml`) plus a
process-wide "current language" - see
docs/superpowers/specs/2026-09-03-i18n-phase-a-language-selection-cli-design.md,
section 3.

A single, shared language setting for the whole installation (not per
request, not per thread) - hence module-global state instead of an object
every caller would have to pass around itself. Whoever changes the
language (CLI bootstrap in cli.py, later the WebUI in phase B) calls
`set_language()` exactly once; every subsequent `t()` call in the same
process sees the new language immediately."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "de"})
DEFAULT_LANGUAGE = "en"

_STRINGS_PATH = Path(__file__).with_name("strings.yaml")


def _load_strings() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(_STRINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{_STRINGS_PATH} must be a mapping at the top level"
    return raw


# Loaded once at import time, not on every t() call - strings.yaml never
# changes at runtime, only between releases.
_STRINGS: dict[str, dict[str, str]] = _load_strings()

_current_language: str = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    """Sets the process-wide current language.

    Raises `ValueError` for anything other than the values in
    `SUPPORTED_LANGUAGES` - and does NOT change the current language in
    that case (no partial success)."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"unsupported language {language!r}, expected one of {sorted(SUPPORTED_LANGUAGES)}"
        )
    global _current_language
    _current_language = language


def current_language() -> str:
    return _current_language


def t(key: str, **values: Any) -> str:
    """Returns the translated text for `key` in the current language, with
    `values` substituted into the placeholders (`str.format`).

    If `key` itself is missing from the table, that is a programming
    error - `KeyError` propagates instead of being swallowed. If only the
    current language's translation is missing (e.g. no "de" yet for a new
    entry), this function returns the English version - never a crash due
    to a missing translation, see `test.english_only` in strings.yaml."""
    entry = _STRINGS[key]
    template = entry.get(_current_language, entry["en"])
    return template.format(**values)


def raw_template(key: str) -> str:
    """Like t(), but WITHOUT .format() - returns the unresolved template
    (with {placeholders} still unfilled) in the current language, falling
    back to English just like t().

    For api/language.py's GET /api/i18n: the browser fills in placeholders
    itself, at runtime, with values the server cannot know (e.g.
    error.message, device.label). t() itself would immediately crash here
    with a KeyError as soon as any web.* key carries a placeholder at all
    - exactly the bug this function avoids."""
    entry = _STRINGS[key]
    return entry.get(_current_language, entry["en"])


def strings_with_prefix(prefix: str) -> list[str]:
    """All keys that start with `prefix` - for `api/language.py`'s
    `GET /api/i18n`, which delivers only the `web.*` namespace to the
    client, not the whole table (CLI help texts, API error messages etc.
    are none of the browser's business)."""
    return [key for key in _STRINGS if key.startswith(prefix)]
