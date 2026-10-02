# Commissioning queue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Commission devices" dialog that lists Matter devices in pairing mode as cards, attaches scanned codes to them, commissions in the background one after another, and lets each finished device blink while the operator names it — as designed in `docs/superpowers/specs/2026-10-02-commissioning-queue-design.md`.

**Architecture:** Pure helpers (`matter/setup_payload.py`, `matter/dcl.py` with a store cache) feed an in-memory `CommissioningSession` (`commissioning/session.py`) whose single worker calls the commissioning function moved out of `POST /api/devices/commission` (`commissioning/run.py`). A shared lock keeps the new BlueZ scan (`radios/bluez.py` `BluezScanner`) and commissioning apart. `commissioning/identify.py` makes exactly one device blink, through a new `identify()` on the Matter and Zigbee sources. A new router `api/commissioning.py` serves the dialog, which polls it.

**Tech Stack:** Python 3.12, FastAPI, SQLite, dbus-fast, aiohttp, matter-python-client, zigpy, pytest + pytest-asyncio + httpx2, Alpine.js WebUI.

## Global Constraints

- Everything in the repository is English; user-visible text goes through `i18n.t(...)` with an `en` and a `de` value in `src/loxmatter/i18n/strings.yaml`; German values use the formal "Sie".
- Commit messages: Conventional Commits, English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Store migrations are additive only: no column or table is ever dropped. This plan adds schema 17 (main is at 16).
- **A pairing code is never logged, never written to disk, and never part of an API response.**
- Scan: `10` s, filter transport `le` and UUID `0000fff6-0000-1000-8000-00805f9b34fb`; never while a device is commissioned (one shared `asyncio.Lock`); the dialog's automatic scan is skipped if one ran in the last `60` s.
- Identify: only one device blinks at a time; a device in the naming line gets `30` s renewed every `25` s; a start from a tile or card runs `30` s without renewal; a name entered beforehand gives a `3` s check blink only if nothing else blinks; stop is `IdentifyTime` `0`.
- Early warning: `20` s in phase `searching` without a matching BlueZ sample.
- DCL: base URL `https://on.dcl.csa-iot.org`, `5` s timeout; found entries kept for good, "not in the DCL" kept `7` days, network failures not cached; vendor ids `0xFFF1`–`0xFFF4` never queried.
- Dialog polls `GET /api/commissioning` every `1` s while open; the devices page every `5` s while the session has work and the dialog is closed.
- New test files must have names no other test module has. Tests run in the foreground. Before merge: `uv run pytest --collect-only -q`, then `uv run pytest -q --ignore-glob="tests/test_*.py"` and `uv run pytest -q tests/test_*.py`.
- Checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- New source files start with the GPL header block of `src/loxmatter/sources/__init__.py` (lines 1–15).

## File Structure

| File | Responsibility |
| --- | --- |
| `src/loxmatter/matter/setup_payload.py` (new) | Decode `MT:` and manual codes; no I/O |
| `src/loxmatter/matter/dcl.py` (new) | DCL HTTP client + `DclDirectory` (cache-first lookups, display names, pairing hint) |
| `src/loxmatter/model/dcl_store.py` (new) | `dcl_vendor`/`dcl_model` rows |
| `src/loxmatter/model/store.py` | Schema 17, `Store.dcl` |
| `src/loxmatter/radios/bluez.py` | `BluezScanner.scan()` |
| `src/loxmatter/commissioning/__init__.py` (new) | Package docstring |
| `src/loxmatter/commissioning/run.py` (new) | `commission(...)`: the route's commissioning sequence, shared |
| `src/loxmatter/commissioning/identify.py` (new) | `IdentifyCoordinator` |
| `src/loxmatter/commissioning/session.py` (new) | Cards, matching, worker, naming line |
| `src/loxmatter/sources/__init__.py` | `IdentifyUnsupportedError`, `IdentifySource` protocol |
| `src/loxmatter/matter/client.py` | `identify()`, `supports_identify()` |
| `src/loxmatter/zigbee/source.py` | `identify()`, `supports_identify()` |
| `src/loxmatter/api/devices.py` | Route uses `run.commission`; `POST /devices/{id}/identify`; `DeviceOut.identify` |
| `src/loxmatter/api/commissioning.py` (new) | `/api/commissioning*` routes |
| `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py` | Wiring |
| `src/loxmatter/web/{app.js,index.html,style.css}` | Dialog, cards, naming, identify |
| `CHANGELOG.md` | Unreleased entry |

---

### Task 1: Decode pairing codes in the bridge

**Files:**
- Create: `src/loxmatter/matter/setup_payload.py`
- Test: `tests/matter/test_setup_payload_decoding.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) SetupPayload(kind: Literal["long", "short"], discriminator: int, vendor_id: int | None, product_id: int | None, ble: bool | None, on_network: bool | None)`; `decode(code: str) -> SetupPayload` raising `UnreadableCodeError` (subclass of `ValueError`, message is a fixed English text with no part of the code); `TypoError(UnreadableCodeError)` for a failed Verhoeff check; `discriminator_for(payload) -> Discriminator` (the existing `matter.commissioning_progress.Discriminator`).

- [ ] **Step 1: Write the failing tests**

```python
"""The pairing code decoded in the bridge (design 2026-10-02, section 5).

The vectors are the Matter SDK's default test device (VID 0xFFF1, PID
0x8000, discriminator 3840, passcode 20202021), the same ones
tests/api/test_web.py runs through the browser decoder."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from loxmatter.matter.commissioning_progress import Discriminator
from loxmatter.matter.setup_payload import (
    SetupPayload,
    TypoError,
    UnreadableCodeError,
    decode,
    discriminator_for,
)

APP_JS = Path(__file__).parents[2] / "src" / "loxmatter" / "web" / "app.js"
NODE = shutil.which("node")


def test_qr_code_carries_everything():
    assert decode("MT:Y.K9042C00KA0648G00") == SetupPayload(
        kind="long", discriminator=3840, vendor_id=0xFFF1, product_id=0x8000, ble=True, on_network=False
    )


def test_qr_code_is_case_insensitive_and_trimmed():
    assert decode("  mt:y.k9042c00ka0648g00 ") == decode("MT:Y.K9042C00KA0648G00")


def test_on_network_only_qr_code():
    payload = decode("MT:-24J0AFN00KA0648G00")
    assert (payload.ble, payload.on_network, payload.product_id) == (False, True, 0x8001)


@pytest.mark.parametrize("code", ["34970112332", "3497-011-2332", "3497 011 2332"])
def test_manual_code_gives_the_short_discriminator(code):
    assert decode(code) == SetupPayload(
        kind="short", discriminator=15, vendor_id=None, product_id=None, ble=None, on_network=None
    )


def test_21_digit_code_decodes():
    payload = decode("400000000000000000003")
    assert (payload.kind, payload.discriminator) == ("short", 0)


@pytest.mark.parametrize("code", ["34970112333"])
def test_a_typo_is_named(code):
    with pytest.raises(TypoError):
        decode(code)


@pytest.mark.parametrize("code", ["", "abc", "1234", "40000000007", "MT:", "MT:!!!"])
def test_unreadable_codes_raise(code):
    with pytest.raises(UnreadableCodeError):
        decode(code)


def test_the_error_text_never_contains_the_code():
    with pytest.raises(UnreadableCodeError) as raised:
        decode("MT:SECRET99")
    assert "SECRET" not in str(raised.value)


def test_the_payload_has_no_passcode():
    assert "20202021" not in repr(decode("MT:Y.K9042C00KA0648G00"))


def test_discriminator_for_matches_the_tracker_type():
    assert discriminator_for(decode("MT:Y.K9042C00KA0648G00")) == Discriminator(3840, "long")
    assert discriminator_for(decode("34970112332")) == Discriminator(15, "short")


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_python_and_browser_agree():
    codes = ["34970112332", "3497-011-2332", "MT:Y.K9042C00KA0648G00", "400000000000000000003"]
    script = f"""
      const fs = require("node:fs");
      const src = fs.readFileSync({str(APP_JS)!r}, "utf8");
      const decodePairingCode = new Function(src + "\\nreturn decodePairingCode;")();
      console.log(JSON.stringify({json.dumps(codes)}.map((c) => decodePairingCode(c))));
    """
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    browser = json.loads(result.stdout)
    for code, theirs in zip(codes, browser, strict=True):
        ours = decode(code)
        assert {"kind": ours.kind, "discriminator": ours.discriminator} == theirs
```

If `new Function(src + "return decodePairingCode;")()` fails because `app.js` touches browser globals at load time, copy the loading pattern of `_app_state` in `tests/api/test_web.py` (it evaluates `app.js` the same way and exports `decodePairingCode`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/matter/test_setup_payload_decoding.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'loxmatter.matter.setup_payload'`.

- [ ] **Step 3: Implement**

```python
"""The pairing code, decoded in the bridge (design 2026-10-02, section 5).

The browser has decoded it since the commissioning-feedback design
(`decodePairingCode` in app.js); the queue lives in the bridge, so the
bridge needs its own decoder. Pure functions, no I/O. The passcode is
read only as far as the format requires and never leaves this module:
`SetupPayload` has no field for it, and no error text quotes the code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from loxmatter.matter.commissioning_progress import Discriminator

_BASE38: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-."
# Discovery capabilities (Matter core spec 5.1.3.1): bit 1 BLE, bit 2 on IP network.
_CAP_BLE: Final = 1 << 1
_CAP_ON_NETWORK: Final = 1 << 2

_VERHOEFF_D: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


class UnreadableCodeError(ValueError):
    """Not a Matter pairing code. The message never quotes the input."""


class TypoError(UnreadableCodeError):
    """A manual code whose check digit does not match."""


@dataclass(frozen=True)
class SetupPayload:
    kind: Literal["long", "short"]
    discriminator: int
    vendor_id: int | None
    product_id: int | None
    ble: bool | None
    on_network: bool | None


def _base38_bytes(text: str) -> list[int]:
    out: list[int] = []
    for start in range(0, len(text), 5):
        chunk = text[start : start + 5]
        count = {5: 3, 4: 2, 2: 1}.get(len(chunk))
        if count is None:
            raise UnreadableCodeError("not a Matter QR payload")
        value = 0
        for char in reversed(chunk):
            digit = _BASE38.find(char)
            if digit < 0:
                raise UnreadableCodeError("not a Matter QR payload")
            value = value * 38 + digit
        for _ in range(count):
            out.append(value & 0xFF)
            value >>= 8
    return out


def _bits(data: list[int], start: int, length: int) -> int:
    value = 0
    for index in range(length):
        bit = start + index
        if (data[bit >> 3] >> (bit & 7)) & 1:
            value |= 1 << index
    return value


def _verhoeff_valid(digits: str) -> bool:
    check = 0
    for position, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[position % 8][int(char)]]
    return check == 0


def decode(code: str) -> SetupPayload:
    text = code.strip()
    if text[:3].upper() == "MT:":
        data = _base38_bytes(text[3:].upper())
        if len(data) < 11:
            raise UnreadableCodeError("not a Matter QR payload")
        capabilities = _bits(data, 37, 8)
        return SetupPayload(
            kind="long",
            discriminator=_bits(data, 45, 12),
            vendor_id=_bits(data, 3, 16),
            product_id=_bits(data, 19, 16),
            ble=bool(capabilities & _CAP_BLE),
            on_network=bool(capabilities & _CAP_ON_NETWORK),
        )
    digits = "".join(char for char in text if char not in " -")
    if not digits.isdigit() or len(digits) not in (11, 21):
        raise UnreadableCodeError("not a Matter pairing code")
    if not _verhoeff_valid(digits):
        raise TypoError("check digit does not match")
    chunk1 = int(digits[0])
    chunk2 = int(digits[1:6])
    long_form = bool((chunk1 >> 2) & 1)
    if long_form != (len(digits) == 21):
        raise UnreadableCodeError("not a Matter pairing code")
    vendor_id = int(digits[10:15]) if long_form else None
    product_id = int(digits[15:20]) if long_form else None
    return SetupPayload(
        kind="short",
        discriminator=((chunk1 & 0x3) << 2) | (chunk2 >> 14),
        vendor_id=vendor_id,
        product_id=product_id,
        ble=None,
        on_network=None,
    )


def discriminator_for(payload: SetupPayload) -> Discriminator:
    return Discriminator(payload.discriminator, payload.kind)
```

The browser's Verhoeff table may already exist under `VERHOEFF_D`/`VERHOEFF_P` in app.js; compare the Python tables to it and keep them identical.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run pytest tests/matter/test_setup_payload_decoding.py -q -W error
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/matter/setup_payload.py tests/matter/test_setup_payload_decoding.py
git commit -m "feat(matter): decode pairing codes in the bridge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Product names from the DCL, cached in the store (schema 17)

**Files:**
- Create: `src/loxmatter/matter/dcl.py`, `src/loxmatter/model/dcl_store.py`
- Modify: `src/loxmatter/model/store.py` (schema comment, `_SCHEMA`, `_migrate_to_v17`, `_MIGRATIONS`, `Store.__init__`), every test that pins `user_version == 16` / `schema_version() == 16`
- Test: `tests/matter/test_dcl_directory.py`, `tests/model/test_dcl_cache_store.py`

**Interfaces:**
- Produces:
  - `DclVendor(vendor_id: int, name: str)`, `DclModel(vendor_id: int, product_id: int, name: str, part_number: str | None, device_type: int | None, initial_steps_hint: int, initial_steps_instruction: str | None)`
  - `DclStore`: `vendor(vid) -> CachedVendor | None`, `model(vid, pid) -> CachedModel | None`, `put_vendor(vid, DclVendor | None, fetched_at: str)`, `put_model(vid, pid, DclModel | None, fetched_at: str)`; `CachedVendor(entry: DclVendor | None, fetched_at: str)`, `CachedModel(entry: DclModel | None, fetched_at: str)` (`entry None` = "not in the DCL")
  - `Fetch = Callable[[str], Awaitable[dict[str, Any] | None]]` (None = HTTP 404); `fetch_json(url) -> dict | None` (aiohttp, 5 s, raises on network failure)
  - `DclDirectory(store: DclStore, fetch: Fetch = fetch_json, *, now: Callable[[], datetime])`: `async vendor(vid) -> DclVendor | None`, `async model(vid, pid) -> DclModel | None`, `async product_label(vid: int | None, pid: int | None) -> ProductLabel`
  - `ProductLabel(product: str, detail: str, pairing_hint: str)` — all already translated via `i18n.t`
  - `is_test_vendor(vid) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/model/test_dcl_cache_store.py`:

```python
"""DCL answers kept in the store (design 2026-10-02, section 7)."""

import sqlite3

from loxmatter.matter.dcl import DclModel, DclVendor
from loxmatter.model.store import Store, schema_version


def test_schema_is_17():
    assert schema_version() == 17


def test_vendor_and_model_round_trip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.dcl.put_vendor(4476, DclVendor(4476, "IKEA of Sweden"), "2026-10-02T10:00:00+00:00")
    model = DclModel(4476, 36865, "KAJPLATS E27 WS globe 1055lm", "LED2407G8", 268, 1, None)
    store.dcl.put_model(4476, 36865, model, "2026-10-02T10:00:00+00:00")
    assert store.dcl.vendor(4476).entry == DclVendor(4476, "IKEA of Sweden")
    assert store.dcl.model(4476, 36865).entry == model


def test_not_in_the_dcl_is_remembered(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.dcl.put_model(4476, 1, None, "2026-10-02T10:00:00+00:00")
    cached = store.dcl.model(4476, 1)
    assert cached is not None and cached.entry is None
    assert store.dcl.model(4476, 2) is None


def test_a_v16_database_gains_the_tables(tmp_path):
    path = tmp_path / "v16.sqlite"
    Store(path).close()
    db = sqlite3.connect(str(path))
    db.executescript("DROP TABLE dcl_vendor; DROP TABLE dcl_model; PRAGMA user_version = 16;")
    db.close()
    store = Store(path)
    store.dcl.put_vendor(4476, DclVendor(4476, "IKEA of Sweden"), "t")
    assert store.dcl.vendor(4476) is not None
```

`tests/matter/test_dcl_directory.py`:

```python
"""Human-readable product names (design 2026-10-02, section 7). Fixtures are
the DCL's answers of October 2, 2026."""

from datetime import UTC, datetime, timedelta

import pytest

from loxmatter import i18n
from loxmatter.matter.dcl import DclDirectory, DclModel, is_test_vendor
from loxmatter.model.store import Store

VENDOR_4476 = {"vendorInfo": {"vendorID": 4476, "vendorName": "IKEA of Sweden"}}
MODEL_36865 = {"model": {"vid": 4476, "pid": 36865, "deviceTypeId": 268,
    "productName": "KAJPLATS E27 WS globe 1055lm", "partNumber": "LED2407G8",
    "commissioningModeInitialStepsHint": 1, "commissioningModeInitialStepsInstruction": ""}}
MODEL_12288 = {"model": {"vid": 4476, "pid": 12288, "deviceTypeId": 263,
    "productName": "MYGGSPRAY wrlss mtn sensor", "partNumber": "E2494",
    "commissioningModeInitialStepsHint": 1, "commissioningModeInitialStepsInstruction": ""}}


class Fetcher:
    def __init__(self, answers):
        self.answers = answers
        self.urls = []
        self.fail = False

    async def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("no route to host")
        return self.answers.get(url)


BASE = "https://on.dcl.csa-iot.org"


def _directory(tmp_path, answers, now):
    fetch = Fetcher(answers)
    return DclDirectory(Store(tmp_path / "t.sqlite").dcl, fetch, now=lambda: now[0]), fetch


async def test_names_from_the_dcl(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, _ = _directory(tmp_path, {
        f"{BASE}/dcl/vendorinfo/vendors/4476": VENDOR_4476,
        f"{BASE}/dcl/model/models/4476/36865": MODEL_36865,
    }, now)
    label = await directory.product_label(4476, 36865)
    assert label.product == "KAJPLATS E27 WS globe 1055lm"
    assert label.detail == "IKEA of Sweden · LED2407G8"
    assert label.pairing_hint == i18n.t("web.commissioning.hint_power_cycle")


async def test_a_second_lookup_does_not_ask_again(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {f"{BASE}/dcl/model/models/4476/12288": MODEL_12288}, now)
    first = await directory.model(4476, 12288)
    second = await directory.model(4476, 12288)
    assert first == second == DclModel(4476, 12288, "MYGGSPRAY wrlss mtn sensor", "E2494", 263, 1, None)
    assert len(fetch.urls) == 1


async def test_not_in_the_dcl_is_asked_again_after_seven_days(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    assert await directory.model(4476, 1) is None
    now[0] += timedelta(days=6)
    assert await directory.model(4476, 1) is None
    assert len(fetch.urls) == 1
    now[0] += timedelta(days=2)
    await directory.model(4476, 1)
    assert len(fetch.urls) == 2


async def test_offline_falls_back_to_numbers_and_is_not_cached(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    fetch.fail = True
    label = await directory.product_label(0x117C, 0x9001)
    assert label.product == i18n.t("web.commissioning.unknown_product", vendor="0x117C", product="0x9001")
    fetch.fail = False
    fetch.answers[f"{BASE}/dcl/model/models/4476/36865"] = MODEL_36865
    assert (await directory.model(4476, 36865)).name == "KAJPLATS E27 WS globe 1055lm"


@pytest.mark.parametrize("vid", [0xFFF1, 0xFFF2, 0xFFF3, 0xFFF4])
async def test_test_vendors_are_never_asked(tmp_path, vid):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    label = await directory.product_label(vid, 0x8000)
    assert is_test_vendor(vid)
    assert label.product == i18n.t("web.commissioning.test_device", vendor=f"0x{vid:04X}")
    assert fetch.urls == []


async def test_without_ids_the_label_says_so(tmp_path):
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    directory, fetch = _directory(tmp_path, {}, now)
    label = await directory.product_label(None, None)
    assert label.product == i18n.t("web.commissioning.numeric_code_device")
    assert fetch.urls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/matter/test_dcl_directory.py tests/model/test_dcl_cache_store.py -q`
Expected: FAIL (`ModuleNotFoundError: loxmatter.matter.dcl`).

- [ ] **Step 3: Strings**

Add to `strings.yaml` (new `web.commissioning.*` group near the other `web.devices.commission*` keys):

```yaml
web.commissioning.hint_power_cycle:
  en: "Switch the power off and on again to put it into pairing mode."
  de: "Schalten Sie den Strom aus und wieder ein, um den Anlernmodus zu starten."
web.commissioning.hint_manual:
  en: "See the manufacturer's instructions for pairing mode."
  de: "Wie der Anlernmodus startet, steht in der Anleitung des Herstellers."
web.commissioning.unknown_product:
  en: "Vendor {vendor} · Product {product}"
  de: "Hersteller {vendor} · Produkt {product}"
web.commissioning.test_device:
  en: "Test device (vendor {vendor})"
  de: "Testgerät (Hersteller {vendor})"
web.commissioning.numeric_code_device:
  en: "Device with numeric code"
  de: "Gerät mit Zahlencode"
web.commissioning.product_unknown_until_done:
  en: "Product known after commissioning"
  de: "Produkt erst nach dem Einlernen bekannt"
```

- [ ] **Step 4: Implement `model/dcl_store.py`**

```python
"""DCL answers cached in the store (design 2026-10-02, section 7)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.matter.dcl_types import DclModel, DclVendor


@dataclass(frozen=True)
class CachedVendor:
    entry: DclVendor | None
    fetched_at: str


@dataclass(frozen=True)
class CachedModel:
    entry: DclModel | None
    fetched_at: str


class DclStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def vendor(self, vendor_id: int) -> CachedVendor | None:
        row = self._db.execute("SELECT * FROM dcl_vendor WHERE vendor_id = ?", (vendor_id,)).fetchone()
        if row is None:
            return None
        entry = None if row["missing"] else DclVendor(vendor_id, str(row["vendor_name"]))
        return CachedVendor(entry, str(row["fetched_at"]))

    def model(self, vendor_id: int, product_id: int) -> CachedModel | None:
        row = self._db.execute(
            "SELECT * FROM dcl_model WHERE vendor_id = ? AND product_id = ?", (vendor_id, product_id)
        ).fetchone()
        if row is None:
            return None
        entry = None
        if not row["missing"]:
            entry = DclModel(
                vendor_id=vendor_id,
                product_id=product_id,
                name=str(row["product_name"]),
                part_number=row["part_number"],
                device_type=row["device_type"],
                initial_steps_hint=int(row["initial_steps_hint"] or 0),
                initial_steps_instruction=row["initial_steps_instruction"],
            )
        return CachedModel(entry, str(row["fetched_at"]))

    def put_vendor(self, vendor_id: int, entry: DclVendor | None, fetched_at: str) -> None:
        self._db.execute(
            "INSERT INTO dcl_vendor (vendor_id, vendor_name, missing, fetched_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(vendor_id) DO UPDATE SET vendor_name = excluded.vendor_name,"
            " missing = excluded.missing, fetched_at = excluded.fetched_at",
            (vendor_id, None if entry is None else entry.name, 1 if entry is None else 0, fetched_at),
        )
        self._db.commit()

    def put_model(self, vendor_id: int, product_id: int, entry: DclModel | None, fetched_at: str) -> None:
        self._db.execute(
            "INSERT INTO dcl_model (vendor_id, product_id, product_name, part_number, device_type,"
            " initial_steps_hint, initial_steps_instruction, missing, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(vendor_id, product_id) DO UPDATE SET product_name = excluded.product_name,"
            " part_number = excluded.part_number, device_type = excluded.device_type,"
            " initial_steps_hint = excluded.initial_steps_hint,"
            " initial_steps_instruction = excluded.initial_steps_instruction,"
            " missing = excluded.missing, fetched_at = excluded.fetched_at",
            (
                vendor_id,
                product_id,
                None if entry is None else entry.name,
                None if entry is None else entry.part_number,
                None if entry is None else entry.device_type,
                None if entry is None else entry.initial_steps_hint,
                None if entry is None else entry.initial_steps_instruction,
                1 if entry is None else 0,
                fetched_at,
            ),
        )
        self._db.commit()
```

To avoid an import cycle (`model` must not import an HTTP client), put the two dataclasses in `src/loxmatter/matter/dcl_types.py`:

```python
"""What the DCL says about a vendor and a product (design 2026-10-02, 7)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DclVendor:
    vendor_id: int
    name: str


@dataclass(frozen=True)
class DclModel:
    vendor_id: int
    product_id: int
    name: str
    part_number: str | None
    device_type: int | None
    initial_steps_hint: int
    initial_steps_instruction: str | None
```

and re-export them from `matter/dcl.py` (`from loxmatter.matter.dcl_types import DclModel, DclVendor` plus `__all__`).

- [ ] **Step 5: Schema 17 in `store.py`**

Comment block above `_SCHEMA_VERSION`:

```python
# Version 17 (commissioning queue, design 2026-10-02, section 7) adds the
# tables `dcl_vendor` and `dcl_model`, a cache of the CSA DCL's product
# names. New tables only: a rolled-back image never names them.
_SCHEMA_VERSION = 17
```

Append to `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS dcl_vendor (
    vendor_id   INTEGER PRIMARY KEY,
    vendor_name TEXT,
    missing     INTEGER NOT NULL DEFAULT 0,
    fetched_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dcl_model (
    vendor_id                 INTEGER NOT NULL,
    product_id                INTEGER NOT NULL,
    product_name              TEXT,
    part_number               TEXT,
    device_type               INTEGER,
    initial_steps_hint        INTEGER,
    initial_steps_instruction TEXT,
    missing                   INTEGER NOT NULL DEFAULT 0,
    fetched_at                TEXT NOT NULL,
    PRIMARY KEY (vendor_id, product_id)
);
```

```python
def _migrate_to_v17(db: sqlite3.Connection) -> None:
    """Adds `dcl_vendor` and `dcl_model` (design 2026-10-02, section 7).
    Both come from `_SCHEMA`'s `CREATE TABLE IF NOT EXISTS`, which runs
    before `_migrate`; nothing else to do."""
```

Add `17: _migrate_to_v17,` to `_MIGRATIONS`; in `Store.__init__`: `self.dcl = DclStore(self._db)` (import `from loxmatter.model.dcl_store import DclStore`). Check (as the firmware plan did) that `_SCHEMA` runs before `_migrate`; if not, put the two `CREATE TABLE IF NOT EXISTS` statements into `_migrate_to_v17`.

Then: `grep -rn "== 16" tests/` and move every assertion that pins the schema number to 17.

- [ ] **Step 6: Implement `matter/dcl.py`**

```python
"""Human-readable vendor and product names from the CSA DCL (design
2026-10-02, section 7).

Cache first: a found entry is kept for good, "not in the DCL" for seven
days, a network failure not at all. Test vendor ids are never asked."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from loxmatter import i18n
from loxmatter.matter.dcl_types import DclModel, DclVendor
from loxmatter.model.dcl_store import DclStore

__all__ = ["DclDirectory", "DclModel", "DclVendor", "ProductLabel", "fetch_json", "is_test_vendor"]

logger = logging.getLogger(__name__)

DCL_BASE: Final = "https://on.dcl.csa-iot.org"
_TIMEOUT_SECONDS: Final = 5.0
_RETRY_MISSING_AFTER: Final = timedelta(days=7)
_HINT_POWER_CYCLE: Final = 1 << 0

Fetch = Callable[[str], Awaitable["dict[str, Any] | None"]]


def is_test_vendor(vendor_id: int) -> bool:
    return 0xFFF1 <= vendor_id <= 0xFFF4


async def fetch_json(url: str) -> dict[str, Any] | None:
    """One GET; `None` for 404, raises on anything that is not an answer."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as response:
        if response.status == 404:
            return None
        response.raise_for_status()
        data = await response.json()
        return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class ProductLabel:
    product: str
    detail: str
    pairing_hint: str


class DclDirectory:
    def __init__(
        self,
        store: DclStore,
        fetch: Fetch = fetch_json,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._now = now

    def _fresh_missing(self, fetched_at: str) -> bool:
        try:
            moment = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        return self._now() - moment < _RETRY_MISSING_AFTER

    async def vendor(self, vendor_id: int) -> DclVendor | None:
        if is_test_vendor(vendor_id):
            return None
        cached = self._store.vendor(vendor_id)
        if cached is not None and (cached.entry is not None or self._fresh_missing(cached.fetched_at)):
            return cached.entry
        try:
            data = await self._fetch(f"{DCL_BASE}/dcl/vendorinfo/vendors/{vendor_id}")
        except Exception as exc:  # noqa: BLE001 - offline is a normal state here
            logger.info("DCL vendor %s not reachable: %s", vendor_id, exc)
            return None
        info = (data or {}).get("vendorInfo") or {}
        name = info.get("vendorName")
        entry = DclVendor(vendor_id, str(name)) if isinstance(name, str) and name.strip() else None
        self._store.put_vendor(vendor_id, entry, self._now().isoformat())
        return entry

    async def model(self, vendor_id: int, product_id: int) -> DclModel | None:
        if is_test_vendor(vendor_id):
            return None
        cached = self._store.model(vendor_id, product_id)
        if cached is not None and (cached.entry is not None or self._fresh_missing(cached.fetched_at)):
            return cached.entry
        try:
            data = await self._fetch(f"{DCL_BASE}/dcl/model/models/{vendor_id}/{product_id}")
        except Exception as exc:  # noqa: BLE001 - offline is a normal state here
            logger.info("DCL model %s/%s not reachable: %s", vendor_id, product_id, exc)
            return None
        model = (data or {}).get("model") or {}
        name = model.get("productName")
        entry = None
        if isinstance(name, str) and name.strip():
            instruction = model.get("commissioningModeInitialStepsInstruction")
            entry = DclModel(
                vendor_id=vendor_id,
                product_id=product_id,
                name=name.strip(),
                part_number=(model.get("partNumber") or None),
                device_type=model.get("deviceTypeId") if isinstance(model.get("deviceTypeId"), int) else None,
                initial_steps_hint=int(model.get("commissioningModeInitialStepsHint") or 0),
                initial_steps_instruction=instruction.strip() if isinstance(instruction, str) and instruction.strip() else None,
            )
        self._store.put_model(vendor_id, product_id, entry, self._now().isoformat())
        return entry

    async def product_label(self, vendor_id: int | None, product_id: int | None) -> ProductLabel:
        if vendor_id is None or product_id is None:
            return ProductLabel(
                product=i18n.t("web.commissioning.numeric_code_device"),
                detail=i18n.t("web.commissioning.product_unknown_until_done"),
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        if is_test_vendor(vendor_id):
            return ProductLabel(
                product=i18n.t("web.commissioning.test_device", vendor=f"0x{vendor_id:04X}"),
                detail="",
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        vendor = await self.vendor(vendor_id)
        model = await self.model(vendor_id, product_id)
        if model is None:
            return ProductLabel(
                product=i18n.t(
                    "web.commissioning.unknown_product",
                    vendor=f"0x{vendor_id:04X}",
                    product=f"0x{product_id:04X}",
                ),
                detail=vendor.name if vendor is not None else "",
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        detail = " · ".join(part for part in (vendor.name if vendor else None, model.part_number) if part)
        if model.initial_steps_instruction:
            hint = model.initial_steps_instruction
        elif model.initial_steps_hint & _HINT_POWER_CYCLE:
            hint = i18n.t("web.commissioning.hint_power_cycle")
        else:
            hint = i18n.t("web.commissioning.hint_manual")
        return ProductLabel(product=model.name, detail=detail, pairing_hint=hint)
```

Check whether `aiohttp` is the HTTP client the project already depends on (it is used by matter-python-client); if the project uses a different client elsewhere for outbound calls (`api/update.py`'s fetcher), use that one instead.

- [ ] **Step 7: Run, lint, commit**

```bash
uv run pytest tests/matter/test_dcl_directory.py tests/model -q -W error
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/matter/dcl.py src/loxmatter/matter/dcl_types.py src/loxmatter/model tests/matter/test_dcl_directory.py tests/model src/loxmatter/i18n/strings.yaml
git commit -m "feat(matter): name products from the CSA DCL and cache the answers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The bridge's own Bluetooth scan

**Files:**
- Modify: `src/loxmatter/radios/bluez.py` (module docstring: the "never scans" sentence is revised by design 2026-10-02, section 2; add `BluezScanner`)
- Test: `tests/radios/test_bluez_scan_runs.py`

**Interfaces:**
- Produces: `MethodCaller = Callable[[str, str, str, str, list[Any]], Awaitable[None]]` (path, interface, member, signature, body; raises `BluezScanError` on an error reply); `BluezScanner(call: MethodCaller | None = None, *, sleep=asyncio.sleep)` with `async scan(adapter_path: str = "/org/bluez/hci0", seconds: float = 10.0) -> None`; `class BluezScanError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests**

```python
"""The bridge's own scan (design 2026-10-02, section 6.1). The call order
is the one measured on the test Pi on October 2, 2026."""

import pytest

from loxmatter.radios.bluez import MATTER_SERVICE_UUID, BluezScanError, BluezScanner


class Calls:
    def __init__(self, fail_on=None):
        self.made = []
        self.fail_on = fail_on

    async def __call__(self, path, interface, member, signature, body):
        self.made.append((path, interface, member, signature, body))
        if member == self.fail_on:
            raise BluezScanError(f"{member} refused")


async def test_scan_filters_starts_waits_and_stops():
    calls = Calls()
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    await BluezScanner(calls, sleep=sleep).scan()
    members = [made[2] for made in calls.made]
    assert members == ["SetDiscoveryFilter", "StartDiscovery", "StopDiscovery"]
    filter_body = calls.made[0][4][0]
    assert filter_body["Transport"].value == "le"
    assert filter_body["UUIDs"].value == [MATTER_SERVICE_UUID]
    assert slept == [10.0]
    assert all(made[0] == "/org/bluez/hci0" and made[1] == "org.bluez.Adapter1" for made in calls.made)


async def test_stop_runs_even_when_the_wait_is_cancelled():
    import asyncio

    calls = Calls()

    async def sleep(seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BluezScanner(calls, sleep=sleep).scan()
    assert calls.made[-1][2] == "StopDiscovery"


async def test_a_refused_start_raises_and_does_not_stop():
    calls = Calls(fail_on="StartDiscovery")
    with pytest.raises(BluezScanError):
        await BluezScanner(calls, sleep=lambda s: None).scan()
    assert [made[2] for made in calls.made] == ["SetDiscoveryFilter", "StartDiscovery"]
```

(`sleep=lambda s: None` is never awaited because the start fails first; if mypy or the code awaits it, use an `async def` no-op.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/radios/test_bluez_scan_runs.py -q`
Expected: FAIL (`ImportError: cannot import name 'BluezScanner'`).

- [ ] **Step 3: Implement** (append to `radios/bluez.py`; also rewrite the module docstring sentence "it never starts a scan itself" to say the reader never scans, and that `BluezScanner` does, under the lock of design 2026-10-02, section 6.2)

```python
MethodCaller = Callable[[str, str, str, str, list[Any]], Awaitable[None]]


class BluezScanError(RuntimeError):
    """BlueZ answered a scan call with an error."""


async def _call_bluez(path: str, interface: str, member: str, signature: str, body: list[Any]) -> None:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body,
            )
        )
    finally:
        bus.disconnect()
    if reply is None or reply.message_type == MessageType.ERROR:
        raise BluezScanError(f"BlueZ refused {member}: {getattr(reply, 'error_name', None)}")


class BluezScanner:
    """A short scan of our own (design 2026-10-02, section 6.1).

    Measured on the test Pi on 2 October 2026: the container (uid 0) may
    call all three methods over the read-only /run/dbus mount. The caller
    holds the scan/commissioning lock; this class does not know it."""

    def __init__(
        self,
        call: MethodCaller | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._call = call or _call_bluez
        self._sleep = sleep

    async def scan(self, adapter_path: str = "/org/bluez/hci0", seconds: float = 10.0) -> None:
        from dbus_fast import Variant

        await self._call(
            adapter_path,
            _ADAPTER,
            "SetDiscoveryFilter",
            "a{sv}",
            [{"Transport": Variant("s", "le"), "UUIDs": Variant("as", [MATTER_SERVICE_UUID])}],
        )
        await self._call(adapter_path, _ADAPTER, "StartDiscovery", "", [])
        try:
            await self._sleep(seconds)
        finally:
            try:
                await self._call(adapter_path, _ADAPTER, "StopDiscovery", "", [])
            except BluezScanError as exc:
                logger.warning("Stopping the Bluetooth scan failed: %s", exc)
```

Add `import asyncio` at the top of `bluez.py`. Note that a `StopDiscovery` failure while a `CancelledError` propagates must not replace it: the inner `try/except BluezScanError` keeps that.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run pytest tests/radios -q -W error
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/radios/bluez.py tests/radios/test_bluez_scan_runs.py
git commit -m "feat(radios): scan for Matter devices in pairing mode for ten seconds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Identify — one device at a time, Matter and Zigbee

**Files:**
- Modify: `src/loxmatter/sources/__init__.py` (`IdentifyUnsupportedError`, `IdentifySource` protocol)
- Modify: `src/loxmatter/matter/client.py` (`identify`, `supports_identify`)
- Modify: `src/loxmatter/zigbee/source.py` (`identify`, `supports_identify`)
- Create: `src/loxmatter/commissioning/__init__.py`, `src/loxmatter/commissioning/identify.py`
- Modify: `src/loxmatter/api/devices.py` (`POST /devices/{id}/identify`; `DeviceOut.identify`), `src/loxmatter/api/models.py`
- Test: `tests/commissioning/test_identify_coordinator.py`, `tests/matter/test_client_identify.py`, `tests/zigbee/test_source_identify.py`, extend `tests/api/test_devices.py`

**Interfaces:**
- Produces:
  - `class IdentifyUnsupportedError(RuntimeError)` in `loxmatter.sources`
  - `IdentifySource` protocol: `supports_identify(address: str) -> bool`, `async identify(address: str, seconds: int) -> None`
  - `IdentifyCoordinator(source_for: Callable[[str], IdentifySource | None], device_address: Callable[[int], tuple[str, str]], *, sleep=asyncio.sleep, clock=time.monotonic)`; `async start(device_id: int, *, renew: bool, seconds: int = 30) -> None`; `async stop() -> None`; `blinking: int | None` (property); `async aclose() -> None`. Constants `IDENTIFY_SECONDS = 30`, `RENEW_EVERY = 25`.
  - `device_address(device_id)` returns `(technology, address)` from the store.

- [ ] **Step 1: Write the failing tests**

`tests/commissioning/test_identify_coordinator.py`:

```python
"""Only one device blinks (design 2026-10-02, section 9)."""

import asyncio

import pytest

from loxmatter.commissioning.identify import IdentifyCoordinator
from loxmatter.sources import IdentifyUnsupportedError


class Source:
    def __init__(self):
        self.calls = []

    def supports_identify(self, address):
        return address != "no-identify"

    async def identify(self, address, seconds):
        if address == "no-identify":
            raise IdentifyUnsupportedError("no Identify cluster")
        self.calls.append((address, seconds))


def _coordinator(source, sleep=None):
    addresses = {1: ("matter", "11"), 2: ("matter", "12"), 3: ("matter", "no-identify")}
    kwargs = {"sleep": sleep} if sleep else {}
    return IdentifyCoordinator(lambda tech: source, lambda device_id: addresses[device_id], **kwargs)


async def test_start_blinks_thirty_seconds():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    assert source.calls == [("11", 30)]
    assert coordinator.blinking == 1
    await coordinator.aclose()


async def test_starting_another_stops_the_first():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.start(2, renew=False)
    assert source.calls == [("11", 30), ("11", 0), ("12", 30)]
    assert coordinator.blinking == 2
    await coordinator.aclose()


async def test_stop_sends_zero():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.stop()
    assert source.calls[-1] == ("11", 0)
    assert coordinator.blinking is None


async def test_renewal_every_25_seconds_until_stopped():
    source = Source()
    ticks = asyncio.Queue()

    async def sleep(seconds):
        assert seconds == 25
        await ticks.get()

    coordinator = _coordinator(source, sleep=sleep)
    await coordinator.start(1, renew=True)
    for _ in range(2):
        ticks.put_nowait(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert source.calls == [("11", 30), ("11", 30), ("11", 30)]
    await coordinator.stop()
    assert source.calls[-1] == ("11", 0)


async def test_without_renewal_the_blink_ends_by_itself():
    source = Source()

    async def sleep(seconds):
        assert seconds == 30

    coordinator = _coordinator(source, sleep=sleep)
    await coordinator.start(1, renew=False)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert coordinator.blinking is None
    assert source.calls == [("11", 30)]


async def test_an_unsupported_device_raises_and_blinks_nothing():
    source = Source()
    coordinator = _coordinator(source)
    with pytest.raises(IdentifyUnsupportedError):
        await coordinator.start(3, renew=False)
    assert coordinator.blinking is None
```

`tests/matter/test_client_identify.py` — build a `BridgeMatterClient` with a fake upstream exactly like `tests/matter/test_client_firmware.py` does (copy its `FirmwareNode`/`FirmwareUpstream`/`_connected` helpers into this file under new names `IdentifyNode`/`IdentifyUpstream`; record `send_device_command(node_id, endpoint_id, command)`):

```python
async def test_identify_goes_to_every_endpoint_with_the_cluster():
    upstream = IdentifyUpstream([IdentifyNode(21, {"0/40/1": "IKEA", "1/3/0": 0, "1/6/0": True, "2/3/0": 0})])
    bridge = await _connected(upstream)
    try:
        assert bridge.supports_identify("21") is True
        await bridge.identify("21", 30)
    finally:
        await bridge.disconnect()
    sent = [(node, endpoint, type(command).__name__, command.identifyTime) for node, endpoint, command in upstream.sent]
    assert sent == [(21, 1, "Identify", 30), (21, 2, "Identify", 30)]


async def test_a_device_without_identify_is_unsupported():
    upstream = IdentifyUpstream([IdentifyNode(23, {"0/40/1": "Tasmota", "1/6/0": True})])
    bridge = await _connected(upstream)
    try:
        assert bridge.supports_identify("23") is False
        with pytest.raises(IdentifyUnsupportedError):
            await bridge.identify("23", 30)
    finally:
        await bridge.disconnect()
```

`tests/zigbee/test_source_identify.py` — look at how `tests/zigbee/` builds a `ZigbeeSource` with a fake application and fake zigpy device (`device.endpoints`, `endpoint.in_clusters`, `cluster.command`); add a fake Identify cluster (id 3) on endpoint 1 that records `command(0, identify_time=…)`:

```python
async def test_zigbee_identify_sends_identify_time(...):
    ...
    await source.identify(address, 30)
    assert cluster.commands == [(0, {"identify_time": 30})]


async def test_zigbee_without_identify_is_unsupported(...):
    ...
    assert source.supports_identify(address) is False
    with pytest.raises(IdentifyUnsupportedError):
        await source.identify(address, 30)
```

(Fill the `...` with the fixture the neighbouring Zigbee source tests use; if none builds a device with clusters, report NEEDS_CONTEXT naming the closest fixture you found.)

API test, in `tests/api/test_devices.py`: a `FakeMatterClient` gains `supports_identify`/`identify` recording calls; add

```python
async def test_identify_route_starts_and_stops(api):
    client, _, device_id, fake = api
    assert (await client.post(f"/api/devices/{device_id}/identify", json={"on": True})).status_code == 204
    assert (await client.post(f"/api/devices/{device_id}/identify", json={"on": False})).status_code == 204
    assert fake.identified == [("3", 30), ("3", 0)]


async def test_device_out_says_whether_it_can_identify(api):
    client, _, device_id, _ = api
    devices = (await client.get("/api/devices")).json()
    assert next(d for d in devices if d["id"] == device_id)["identify"] is True
```

(Address `"3"` is the GRILLPLATS fixture's node id; adjust to the `api` fixture's device if different.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/commissioning tests/matter/test_client_identify.py tests/zigbee/test_source_identify.py -q`
Expected: FAIL (`ModuleNotFoundError: loxmatter.commissioning`).

- [ ] **Step 3: `sources/__init__.py`**

```python
class IdentifyUnsupportedError(RuntimeError):
    """The device has no Identify cluster (design 2026-10-02, section 9.1)."""


@runtime_checkable
class IdentifySource(Protocol):
    def supports_identify(self, address: str) -> bool: ...

    async def identify(self, address: str, seconds: int) -> None: ...
```

(add `runtime_checkable` to the `typing` import if missing).

- [ ] **Step 4: `matter/client.py`**

```python
    def _identify_endpoints(self, address: str) -> list[int]:
        node_id = int(address)
        for node in self._require_upstream().get_nodes():
            if node.node_id == node_id:
                endpoints = set()
                for path in node.node_data.attributes:
                    parts = path.split("/")
                    if len(parts) == 3 and parts[1] == "3":
                        endpoints.add(int(parts[0]))
                return sorted(endpoints)
        return []

    def supports_identify(self, address: str) -> bool:
        try:
            return bool(self._identify_endpoints(address))
        except MatterUnavailableError:
            return False

    async def identify(self, address: str, seconds: int) -> None:
        """Identify (cluster 0x0003, command 0) on every endpoint that has
        the cluster (design 2026-10-02, section 9.1). `seconds = 0` stops."""
        endpoints = self._identify_endpoints(address)
        if not endpoints:
            raise IdentifyUnsupportedError(i18n.t("api.commissioning.fail_no_identify"))
        for endpoint in endpoints:
            await self.send(
                DeviceCall(
                    technology="matter",
                    address=address,
                    endpoint=endpoint,
                    cluster_id=0x0003,
                    command_id=0x00,
                    payload={"identifyTime": seconds},
                )
            )
```

- [ ] **Step 5: `zigbee/source.py`**

```python
    def supports_identify(self, address: str) -> bool:
        device = self._device_or_none(address)
        if device is None:
            return False
        return any(IDENTIFY_CLUSTER in endpoint.in_clusters for endpoint in device.non_zdo_endpoints)

    async def identify(self, address: str, seconds: int) -> None:
        device = self._require_device(address)
        for endpoint in device.non_zdo_endpoints:
            cluster = endpoint.in_clusters.get(IDENTIFY_CLUSTER)
            if cluster is None:
                continue
            with _as_device_error():
                await cluster.command(IDENTIFY_COMMAND, identify_time=seconds)
            return
        raise IdentifyUnsupportedError(i18n.t("api.commissioning.fail_no_identify"))
```

Import `IDENTIFY_CLUSTER`, `IDENTIFY_COMMAND` from `loxmatter.zigbee.configure` (they are module constants there) and `IdentifyUnsupportedError` from `loxmatter.sources`. If `non_zdo_endpoints` is not what the source's own device type exposes, use the same expression `_identify_blink` uses.

- [ ] **Step 6: `commissioning/identify.py`**

```python
"""Only one device blinks at a time (design 2026-10-02, section 9.2)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from loxmatter.sources import IdentifySource

logger = logging.getLogger(__name__)

IDENTIFY_SECONDS: Final = 30
RENEW_EVERY: Final = 25


class IdentifyCoordinator:
    def __init__(
        self,
        source_for: Callable[[str], IdentifySource | None],
        device_address: Callable[[int], tuple[str, str]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source_for = source_for
        self._device_address = device_address
        self._sleep = sleep
        self._blinking: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def blinking(self) -> int | None:
        return self._blinking

    async def start(self, device_id: int, *, renew: bool, seconds: int = IDENTIFY_SECONDS) -> None:
        async with self._lock:
            await self._stop_locked()
            source, address = self._resolve(device_id)
            await source.identify(address, seconds)
            self._blinking = device_id
            self._task = asyncio.ensure_future(self._keep(device_id, source, address, renew, seconds))

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def aclose(self) -> None:
        await self.stop()

    def _resolve(self, device_id: int) -> tuple[IdentifySource, str]:
        technology, address = self._device_address(device_id)
        source = self._source_for(technology)
        if source is None:
            from loxmatter.sources import IdentifyUnsupportedError

            raise IdentifyUnsupportedError(technology)
        return source, address

    async def _stop_locked(self) -> None:
        task, device_id = self._task, self._blinking
        self._task, self._blinking = None, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if device_id is not None:
            try:
                source, address = self._resolve(device_id)
                await source.identify(address, 0)
            except Exception as exc:  # noqa: BLE001 - the device stops by itself within 30 s
                logger.info("Stopping identify on device %s failed: %s", device_id, exc)

    async def _keep(
        self, device_id: int, source: IdentifySource, address: str, renew: bool, seconds: int
    ) -> None:
        if not renew:
            await self._sleep(seconds)
            if self._blinking == device_id:
                self._blinking = None
                self._task = None
            return
        while True:
            await self._sleep(RENEW_EVERY)
            try:
                await source.identify(address, seconds)
            except Exception as exc:  # noqa: BLE001 - keep trying; the blink lapses by itself
                logger.info("Renewing identify on device %s failed: %s", device_id, exc)
```

`commissioning/__init__.py`:

```python
"""Commissioning many devices: the session, its worker, identify
(design 2026-10-02)."""
```

- [ ] **Step 7: Route and `DeviceOut.identify`**

Strings:

```yaml
api.commissioning.fail_no_identify:
  en: "This device cannot identify itself."
  de: "Dieses Gerät kann sich nicht durch Blinken melden."
```

`api/models.py`: `DeviceOut` gains `identify: bool = False` and a new model `IdentifyRequest(BaseModel): on: bool`.

`api/devices.py`: `build_device_router(..., identify: IdentifyCoordinator | None = None)`. In `_device_out`, compute `identify` through the device's source: pass a `supports_identify: Callable[[StoredDevice], bool]` into `_device_out` built from `sources` (`isinstance(source, IdentifySource) and source.supports_identify(device.address)`, false on any exception). Route:

```python
    @router.post("/devices/{device_id}/identify", status_code=204)
    async def identify_device(device_id: int, request: IdentifyRequest) -> None:
        _require_device(device_id)
        if coordinator is None:
            raise HTTPException(status_code=503, detail=i18n.t("api.devices.fail_no_matter_client"))
        try:
            if request.on:
                await coordinator.start(device_id, renew=False)
            elif coordinator.blinking == device_id:
                await coordinator.stop()
        except IdentifyUnsupportedError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.commissioning.fail_no_identify")) from exc
        except (MatterUnavailableError, DeviceUnreachableError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
```

where `coordinator = identify or IdentifyCoordinator(lambda tech: _identify_source(sources, tech), lambda device_id: (store.device(device_id).technology, store.device(device_id).address))` and `_identify_source` returns the source from `sources` if it is an `IdentifySource`, else `None`. (`FakeMatterClient` in `tests/api/conftest.py` gains `supports_identify` returning `True` and `identify` appending to `self.identified`.)

- [ ] **Step 8: Run, lint, commit**

```bash
uv run pytest tests/commissioning tests/matter/test_client_identify.py tests/zigbee tests/api/test_devices.py -q -W error
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter tests
git commit -m "feat(identify): let one device blink at a time, Matter and Zigbee

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Move the commissioning sequence out of the route

**Files:**
- Create: `src/loxmatter/commissioning/run.py`
- Modify: `src/loxmatter/api/devices.py` (route body → `run.commission`)
- Test: `tests/commissioning/test_commission_run.py`; every existing test of `POST /api/devices/commission` must pass unchanged

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) CommissionResult(device_id: int, snapshot: NodeSnapshot)`
  - `class CommissionFailed(Exception)` with `reason: str` (a tracker `Reason`), `detail: str` (translated), `status: int` (422 or 502)
  - `async commission(*, code: str, room: str | None, discriminator: Discriminator | None, client: BridgeMatterClient, store: Store, runtime: RuntimeValues, tracker: CommissioningTracker, fetch_dataset: ThreadDatasetSource, manual_dataset: str | None = None) -> CommissionResult`

This is a pure move: the route's body from "Why this even exists" (Thread dataset) through `follow` becomes `commission(...)`; the route calls it and maps `CommissionFailed` to `HTTPException(status_code=exc.status, detail=exc.detail)`. Keep every comment of the moved code with it. Keep `_commissioning_detail`/`_reason_detail` where they are if `run.py` can import them without a cycle, otherwise move them into `run.py` and import them back in `devices.py`.

- [ ] **Step 1: Run the existing commissioning API tests to record the baseline**

Run: `uv run pytest tests/api/test_devices.py tests/api -q -k commission -p no:cacheprovider`
Expected: all pass. Note the count.

- [ ] **Step 2: Write the new unit test**

```python
"""The shared commissioning sequence (design 2026-10-02, section 8.3)."""

import pytest

from loxmatter.commissioning.run import CommissionFailed, commission
from loxmatter.matter.client import CommissioningError
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.model.store import Store


async def test_commission_registers_the_device_with_its_room(tmp_path, fake_client, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    fake_client.store = store
    result = await commission(
        code="34970112332", room="Küche", discriminator=None, client=fake_client, store=store,
        runtime=fake_runtime(store), tracker=CommissioningTracker(), fetch_dataset=fake_otbr,
    )
    assert store.device(result.device_id).room == "Küche"


async def test_a_refusal_becomes_commission_failed(tmp_path, fake_client, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    fake_client.fail_commission_with = CommissioningError("No commissionable device was discovered")
    with pytest.raises(CommissionFailed) as raised:
        await commission(
            code="34970112332", room=None, discriminator=None, client=fake_client, store=store,
            runtime=fake_runtime(store), tracker=CommissioningTracker(), fetch_dataset=fake_otbr,
        )
    assert raised.value.status == 422
    assert raised.value.reason == "not_found"
```

The fixtures `fake_client`, `fake_runtime`, `fake_otbr` live in `tests/api/conftest.py` and are not visible from `tests/commissioning/`. Either put this file under `tests/api/` as `tests/api/test_commission_run.py`, or add a `tests/commissioning/conftest.py` that imports them — prefer placing the file under `tests/api/`. The room is `"Küche"` because the store normalises rooms as given; it is test data, not prose.

- [ ] **Step 3: Move the code** as described above.

- [ ] **Step 4: Run baseline tests + new test**

Run: `uv run pytest tests/api -q -p no:cacheprovider`
Expected: same count as Step 1 plus the new tests, all green.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter tests
git commit -m "refactor(commissioning): move the commissioning sequence out of the route

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The commissioning session and its worker

**Files:**
- Create: `src/loxmatter/commissioning/session.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_commissioning_session.py` (under `tests/api/` to reuse `fake_client`, `fake_runtime`, `fake_otbr`)

**Interfaces:**
- Consumes: Task 1 `decode`, `discriminator_for`, `SetupPayload`, `UnreadableCodeError`, `TypoError`; Task 2 `DclDirectory.product_label`; Task 3 `BluezScanner`; existing `BluezReader.snapshot() -> BluezSnapshot | None` (`adverts: list[MatterAdvert]` with `address, rssi, discriminator, vendor_id, product_id`); Task 4 `IdentifyCoordinator`; Task 5 `commission`, `CommissionFailed`, `CommissionResult`; `Store.rename_device(device_id, label)`, `Store.set_room(device_id, room)`; `KernelLog.findings_async()`, `KernelLog.now_usec()`, `counts_since(findings, since_usec)` from `radios/bluetooth_health.py`.
- Produces:
  - `CardState = Literal["found", "ready", "queued", "running", "naming", "done", "not_nearby", "failed"]`
  - `@dataclass Card(id: int, state: CardState, product: str, detail: str, pairing_hint: str, rssi: int | None, advert_address: str | None, payload: SetupPayload | None, name: str, room: str | None, phase: str | None, note: str | None, device_id: int | None, candidates: list[str])` — the code is held in a separate private dict `{card_id: code}`, never on `Card`
  - `class CodeRejected(Exception)` with `detail: str` (translated)
  - `CommissioningSession(*, store, client_for: Callable[[], BridgeMatterClient | None], runtime, tracker, fetch_dataset, reader: BluezReader, scanner: BluezScanner, kernel: KernelLog | None, dcl: DclDirectory, identify: IdentifyCoordinator, clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep)`
  - Methods: `async scan(*, automatic: bool = False) -> bool` (False = skipped or refused), `async add_code(code: str, room: str | None) -> Card`, `async update_card(card_id: int, *, name: str | None, room: str | None | Unset) -> Card`, `async start() -> None`, `async confirm_name(card_id) -> Card`, `async skip_name(card_id) -> Card`, `async force(card_id) -> Card`, `remove(card_id) -> None`, `clear() -> None`, `async identify_card(card_id: int, on: bool) -> None`, `view() -> dict` (the JSON of `GET /api/commissioning`), `has_work: bool`, `async aclose() -> None`, `busy: bool` (the lock is held by the worker).
  - Constants: `SCAN_SECONDS = 10.0`, `AUTO_SCAN_MIN_INTERVAL = 60.0`, `EARLY_WARNING_SECONDS = 20.0`, `CHECK_BLINK_SECONDS = 3`.
  - `view()` shape:

```json
{
  "scan": {"state": "idle|scanning|blocked", "last_scan_age": 12.3},
  "bluetooth_warning": false,
  "matter_connected": true,
  "naming": [5, 7],
  "blinking_card": 5,
  "cards": [{"id": 5, "state": "naming", "product": "KAJPLATS E27 WS globe 1055lm",
             "detail": "IKEA of Sweden · LED2407G8", "pairing_hint": "…", "rssi": -58,
             "has_code": true, "name": "", "room": "Küche", "phase": null, "note": null,
             "device_id": 41, "queue_position": null, "on_network": false, "can_identify": true}]
}
```

- [ ] **Step 1: Strings**

```yaml
api.commissioning.fail_unreadable_code:
  en: "This is not a Matter pairing code."
  de: "Das ist kein Matter-Code."
api.commissioning.fail_typo:
  en: "The check digit does not match. Please check the code for a typo."
  de: "Die Prüfziffer stimmt nicht. Bitte prüfen Sie den Code auf einen Tippfehler."
api.commissioning.fail_duplicate:
  en: "This code has already been scanned."
  de: "Dieser Code wurde bereits gescannt."
api.commissioning.fail_busy:
  en: "Scanning is paused while a device is commissioned."
  de: "Gesucht wird nicht, solange ein Gerät eingelernt wird."
api.commissioning.fail_name_missing:
  en: "Enter a name, or continue without one."
  de: "Tragen Sie einen Namen ein oder fahren Sie ohne Namen fort."
api.commissioning.fail_unknown_card:
  en: "This card no longer exists."
  de: "Diese Karte gibt es nicht mehr."
api.commissioning.fail_card_running:
  en: "This device is being commissioned right now."
  de: "Dieses Gerät wird gerade eingelernt."
web.commissioning.note_several:
  en: "Matches {count} devices nearby. Which one it is shows after commissioning."
  de: "Passt zu {count} Geräten in der Nähe. Welches es ist, zeigt sich nach dem Einlernen."
web.commissioning.note_wifi:
  en: "Wi-Fi device: searched on the network."
  de: "WLAN-Gerät: wird im Netz gesucht."
web.commissioning.note_not_nearby:
  en: "Not found nearby. {hint}"
  de: "Nicht in der Nähe gefunden. {hint}"
web.commissioning.note_no_signal_yet:
  en: "No matching device is advertising yet."
  de: "Noch sendet kein passendes Gerät."
```

- [ ] **Step 2: Write the failing tests**

Use a `FakeScanner` (records scans, optional `asyncio.Event` to hold a scan open), a `FakeReader` whose `snapshot()` returns a `BluezSnapshot` built from a mutable list of `MatterAdvert`, a `FakeIdentify` with the coordinator's interface (records `start(device_id, renew)`/`stop()`, keeps `blinking`), a `DclDirectory` over the test store with a `Fetcher` (as in Task 2) that answers KAJPLATS E27 / E14 / BILRESA, an injected `clock` (a list-backed float), and `fake_client` from `tests/api/conftest.py` with a hook to make `commission_with_code` wait on an `asyncio.Event` per call. Adverts are the measured shape:

```python
def advert(address, discriminator, pid, rssi):
    return MatterAdvert(address=address, name=None, rssi=rssi, discriminator=discriminator,
                        vendor_id=4476, product_id=pid, connected=False, adapter="/org/bluez/hci0")
```

Tests (each a function; write them all):

1. `test_scan_makes_cards_named_from_the_dcl` — two adverts → after `scan()`, two `found` cards with product names and RSSI, sorted by RSSI.
2. `test_automatic_scan_is_skipped_within_60_seconds` — `scan(automatic=True)` twice at clock 0 and 30 → one scanner call; at 61 → two.
3. `test_scan_is_refused_while_commissioning` — worker holds a running card → `scan()` returns False, scanner not called.
4. `test_qr_code_attaches_to_its_card` — advert discriminator 3840 and code `"MT:Y.K9042C00KA0648G00"` → that card `ready`, `has_code`, room = given room.
5. `test_manual_code_with_several_matches_makes_its_own_card` — adverts 3840 and 3841 (both short 15) + `"34970112332"` → new card, product `web.commissioning.numeric_code_device`, note `note_several` count 2, both found cards untouched.
6. `test_no_match_is_not_nearby_with_the_hint` — no adverts + QR code → `not_nearby`, note contains the pairing hint.
7. `test_on_network_only_code_is_ready_without_a_match` — `"MT:-24J0AFN00KA0648G00"` → `ready`, note `note_wifi`.
8. `test_duplicate_and_unreadable_codes_are_rejected` — `CodeRejected` with the translated details; a typo gives `fail_typo`.
9. `test_the_code_never_appears_in_the_view` — after `add_code`, `json.dumps(session.view())` does not contain `"Y.K9042C00KA0648G00"` nor `"34970112332"`.
10. `test_worker_commissions_in_order_and_names_with_preset_name` — two ready cards, first has name "Esstisch" → after the worker finishes, card 1 `done`, device label "Esstisch", room set; identify got `start(device_id, renew=False, seconds=3)` once (nothing else blinking).
11. `test_unnamed_devices_queue_for_naming_and_only_the_first_blinks` — two cards without names → after both finish: states `naming`, `naming`; `view()["naming"] == [a, b]`; identify `start(a, renew=True)` only; `confirm_name(a)` (with name set via `update_card`) → a `done`, identify `start(b, renew=True)`.
12. `test_confirm_without_a_name_is_refused_and_skip_keeps_the_default_label` — `confirm_name` with empty name raises `CodeRejected(fail_name_missing)`; `skip_name` → `done`, label unchanged.
13. `test_scanning_while_running_queues_at_the_end` — while card 1 runs (held by an event), `add_code` for card 2 → `queued`, position 1.
14. `test_a_failure_moves_on_to_the_next_card` — first `commission_with_code` raises `CommissioningError` → card `failed` with a note; second card `done`.
15. `test_a_card_whose_device_stopped_advertising_is_skipped` — ready card, then its advert disappears from the reader before start → `not_nearby`, `commission_with_code` not called; `force()` → queued and commissioned without the check.
16. `test_early_warning_after_20_seconds_without_a_matching_sample` — while running in phase `searching` and no matching advert, advance clock by 21 s and let the worker's monitor tick → card note `note_no_signal_yet`.
17. `test_the_worker_waits_while_matter_server_is_away` — `fake_client.connected = False` → `start()` leaves cards `queued`, `view()["matter_connected"] is False`; set True → worker proceeds (the worker polls `client.connected` every second via the injected `sleep`).
18. `test_name_change_after_commissioning_is_written_to_the_device` — `update_card(done_card, name="Flur")` → `store.device(id).label == "Flur"`.
19. `test_numeric_code_card_takes_over_its_device_after_commissioning` — case 5 then commission: the snapshot returned by `fake_client` carries vendor 4476 / product 36865 and the card adopts the DCL name; the `found` card whose advert address matches the device that was commissioned is removed. (The tracker records `matched_address` — use `tracker` state if available, otherwise match by vendor/product and remove the first `found` card with that product, and say so in the report.)
20. `test_clear_keeps_the_running_card` — `clear()` during a run leaves only the running card.
21. `test_bluetooth_warning_after_a_wedge_line` — a fake `KernelLog` returning a `stuck` finding newer than the scan start → `view()["bluetooth_warning"] is True`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/api/test_commissioning_session.py -q`
Expected: FAIL (`ModuleNotFoundError: loxmatter.commissioning.session`).

- [ ] **Step 4: Implement `commissioning/session.py`**

Write the class against the interface above. Rules the code must follow, each tied to a test:

- One `asyncio.Lock` (`self._radio`) shared by `scan()` and the worker; the worker holds it for exactly one `commission(...)` call. `scan()` uses `self._radio.locked()` to refuse (test 3) and never waits for the worker.
- `scan()`: unless refused/skipped, records `self._scan_started = clock()` and `kernel.now_usec()`, runs `scanner.scan(seconds=SCAN_SECONDS)`, then `reader.snapshot()`; for each advert creates a `found` card keyed by `advert.address` if none exists (label via `dcl.product_label(vid, pid)`), updates RSSI for existing ones; then reads `kernel.findings_async()` and sets `bluetooth_warning` when `counts_since(findings, start_usec)` has a non-zero `stuck` or `transport` count (test 21). A `BluezScanError` sets `bluetooth_warning` too and returns False.
- `add_code(code, room)`: `decode()`; on `TypoError` → `CodeRejected(i18n.t("api.commissioning.fail_typo"))`, on `UnreadableCodeError` → `fail_unreadable_code`; duplicate code (exact string after `strip()`) → `fail_duplicate`. Matching per design 8.2 using `discriminator_for(payload).matches(card_advert_discriminator)` against `found` cards (keep each card's advert discriminator privately). One match → attach; several → new card with `candidates` = their advert addresses; none and `payload.on_network and not payload.ble` → `ready` with `note_wifi`; none → `not_nearby` with `note_not_nearby` + hint. Label for a code-only card: `dcl.product_label(payload.vendor_id, payload.product_id)`. State is `queued` (appended to the queue) when the worker is running, else `ready`.
- `start()`: moves every `ready` card to `queued` in card order and starts the worker task if it is not running.
- Worker loop: `while queue`: wait (sleep 1 s, re-check) while `client_for()` is `None` or not `connected`; take the next card; if it has an advert and was not forced, take a fresh `reader.snapshot()` and skip to `not_nearby` when its address is gone (test 15); then `async with self._radio:` set `running`, start a monitor task that every 2 s reads `tracker.phase_for(token)`/the reader to set `phase` and, after `EARLY_WARNING_SECONDS` in `searching` without a matching advert, the note (test 16); call `commission(...)` with `room=card.room`, `discriminator=discriminator_for(payload)` when the payload is known; stop the monitor in `finally`.
- After success: `card.device_id`; if `card.name.strip()`: `store.rename_device(device_id, name)`, state `done`, and if `identify.blinking is None`: `identify.start(device_id, renew=False, seconds=CHECK_BLINK_SECONDS)`. Else state `naming`, append to the naming line; if it is first and nothing blinks: `identify.start(device_id, renew=True)`. A numeric-code card adopts product/detail from `dcl.product_label(snapshot vendor, product)` (the snapshot's `0/40/2`/`0/40/4` attributes) and removes the matching `found` card (test 19).
- `confirm_name`/`skip_name`: remove from the naming line, write the name (confirm) or keep the label (skip), state `done`, stop identify if this card's device blinks, then start the next card in the line with `renew=True`.
- `update_card`: sets name/room; for a commissioned card writes `rename_device`/`set_room` at once (test 18).
- `identify_card(card_id, on)`: only for cards with a `device_id`; `on` → `identify.start(device_id, renew=False)`, off → `identify.stop()` if it blinks.
- Identify failures (`IdentifyUnsupportedError`, device errors) are logged and never fail the worker; `can_identify` in the view comes from the source's `supports_identify` (pass a `supports_identify: Callable[[int], bool]` into the session alongside the coordinator, or derive it from the client — pick one and keep it).
- Failures: `CommissionFailed` → `failed`, `note = exc.detail`; any other exception → `failed` with a generic logged message and the worker continues.
- The code dict entry is deleted when a card reaches `done` or is removed.
- `aclose()`: cancel the worker and monitor, `identify.aclose()`.

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest tests/api/test_commissioning_session.py -q -W error -p no:cacheprovider
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/commissioning/session.py src/loxmatter/i18n/strings.yaml tests/api/test_commissioning_session.py
git commit -m "feat(commissioning): queue devices nearby and name each one while it blinks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The API and the wiring

**Files:**
- Create: `src/loxmatter/api/commissioning.py`
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/loxone/server.py` (`build_app(..., commissioning_session=None, identify=None)`), `src/loxmatter/cli.py`
- Test: `tests/api/test_commissioning_routes.py`

**Interfaces:**
- Consumes: Task 6 `CommissioningSession` and its methods; Task 4 `IdentifyCoordinator`.
- Produces: routes of design section 10 exactly:

| Route | Status | Body |
| --- | --- | --- |
| `GET /api/commissioning` | 200 | `session.view()` |
| `POST /api/commissioning/scan` `{"automatic": bool}` (body optional) | 202; 409 `fail_busy` | `session.view()` |
| `POST /api/commissioning/codes` `{"code": str, "room": str | null}` | 201; 422 unreadable/typo; 409 duplicate | the card (view form) |
| `PATCH /api/commissioning/cards/{id}` `{"name"?, "room"?}` | 200; 404 | card |
| `POST /api/commissioning/start` | 202 | view |
| `POST /api/commissioning/cards/{id}/confirm-name` | 200; 422 `fail_name_missing` | card |
| `POST /api/commissioning/cards/{id}/skip-name` | 200 | card |
| `POST /api/commissioning/cards/{id}/force` | 202 | card |
| `POST /api/commissioning/cards/{id}/identify` `{"on": bool}` | 204; 409 no identify | — |
| `DELETE /api/commissioning/cards/{id}` | 204; 409 `fail_card_running` | — |
| `POST /api/commissioning/clear` | 200 | view |

Unknown card id → 404 `fail_unknown_card`. All routers with `dependencies=api_guard`.

- [ ] **Step 1: Write the failing tests** — build the app like `tests/api/test_firmware_api.py` does, passing a `CommissioningSession` made with the fakes of Task 6 (import the fakes from `tests/api/test_commissioning_session.py`? No: move the fakes into `tests/api/commissioning_fakes.py` in this task and import them in both files). Tests:
  1. GET returns the view; 401 without login (GET and `POST /codes`).
  2. `POST /codes` with a QR code after a scan → 201, card `ready`; the raw response text does not contain the code.
  3. `POST /codes` typo → 422 with `i18n.t("api.commissioning.fail_typo")`; duplicate → 409.
  4. `POST /scan` while the worker runs → 409 `fail_busy`.
  5. `PATCH` name/room → reflected in the card.
  6. `POST /start` → 202; after the fake commission finishes, the card is `naming`.
  7. `confirm-name` without name → 422; with name → 200 `done`.
  8. `DELETE` a running card → 409; unknown card → 404.
  9. `POST /cards/{id}/identify` on → 204 and the fake identify recorded it.

- [ ] **Step 2: Implement** `api/commissioning.py` as a thin layer: each route calls the session method and maps `CodeRejected` → 422 or 409 by which detail key it carries (give `CodeRejected` a `status: int` attribute in Task 6's code if it is not there: 422 for unreadable/typo/name, 409 for duplicate/busy/running, 404 for unknown card), `IdentifyUnsupportedError` → 409.

- [ ] **Step 3: Wiring**

`build_app`: new keyword params `commissioning_session: CommissioningSession | None = None`, `identify: IdentifyCoordinator | None = None`. Build the coordinator once (as in Task 4) if not given, pass it to `build_device_router(..., identify=...)`; build a default session only when `client is not None`, with `BluezReader()`, `BluezScanner()`, `kernel_log`, `DclDirectory(store.dcl)`; include the router when a session exists. `cli._run`: build `IdentifyCoordinator` and `CommissioningSession` next to `commissioning_tracker = CommissioningTracker(...)` (cli.py ~line 917) using the same tracker, pass both to `build_app`, and in the shutdown `finally` call `await session.aclose()` with the file's cancellation pattern (`if not task.cancelled(): raise` shape — read the neighbouring cleanup blocks).

- [ ] **Step 4: Run, lint, commit**

```bash
uv run pytest tests/api/test_commissioning_routes.py tests/api/test_commissioning_session.py tests/test_cli.py -q -W error -p no:cacheprovider
uv run pytest tests/api -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter tests
git commit -m "feat(api): serve the commissioning session to the dialog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: WebUI — the dialog, cards and codes

**Files:**
- Modify: `src/loxmatter/web/index.html` (commission card ~line 376 → button + `<dialog x-ref="commissionDialog">` holding the Matter|Zigbee tabs), `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py` (delivery + node binding tests); update old tests of the single-code Matter tab

**Interfaces:**
- Consumes: Task 7 routes and the `view()` JSON.
- Produces (app.js): state `commissioning` (last view), `commissioningTimer`, `commissionDialogOpen`, `commissionCode`, `commissionDefaultRoom`, `commissioningError`; methods `openCommissionDialog()`, `closeCommissionDialog()`, `loadCommissioning()`, `scheduleCommissioningPoll()`, `addCommissionCode()`, `rescanCommissioning()`, `startCommissioning()`, `patchCard(card, fields)`, `removeCard(card)`, `forceCard(card)`, `clearCommissioning()`, `cardChip(card)`, `sortedCards()`.

Layout of the Matter tab (prototype variant B, design 4.2):

```html
<div class="commission-matter" x-show="commissionTabShown() === 'matter'">
  <div class="commission-controls">
    <label>
      <span class="label" x-text="t('web.commissioning.code_label')"></span>
      <input type="text" id="commission-code" x-ref="commissionCode" autocomplete="off"
             :placeholder="t('web.devices.code_placeholder')"
             x-model="commissionCode" @keydown.enter.prevent="addCommissionCode()" />
    </label>
    <label>
      <span class="label" x-text="t('web.commissioning.default_room')"></span>
      <select id="commission-default-room" x-model="commissionDefaultRoom">
        <option value="" x-text="t('web.commissioning.no_room')"></option>
        <template x-for="room in roomChips()" :key="room"><option :value="room" x-text="room"></option></template>
      </select>
    </label>
    <button type="button" @click="rescanCommissioning()"
            :disabled="commissioning?.scan.state !== 'idle'" x-text="t('web.commissioning.scan_again')"></button>
  </div>
  <p class="hint" x-text="scanLine()"></p>
  <p class="banner warn" x-show="commissioning?.bluetooth_warning" x-cloak x-text="t('web.devices.commission_bluetooth_warning')"></p>
  <p class="banner danger" x-show="commissioningError" x-cloak x-text="commissioningError"></p>
  <div class="commission-cards">
    <template x-for="card in sortedCards()" :key="card.id">
      <div class="commission-card" :class="'is-' + card.state">
        <!-- product, detail · RSSI, chip, Code ✓, progress, note, name + room fields, actions -->
      </div>
    </template>
  </div>
  <div class="commission-foot">
    <button type="button" class="primary" @click="startCommissioning()"
            :disabled="!readyCount()" x-text="readyCount() ? t('web.commissioning.start_n', { count: readyCount() }) : t('web.commissioning.start')"></button>
    <button type="button" @click="clearCommissioning()" x-text="t('web.commissioning.clear')"></button>
    <span class="hint" x-text="footLine()"></span>
  </div>
</div>
```

Check `roomChips()` (app.js ~2272) returns plain room names; if it returns objects, map to their name field. Reuse the existing Bluetooth warning key if one exists (`grep -n bluetooth web.devices strings.yaml`), otherwise add `web.commissioning.bluetooth_warning`.

Card body (inside the `x-for`):

```html
<div class="commission-card-top">
  <div class="type-icon"><svg class="icon" aria-hidden="true"><use href="#i-light"></use></svg></div>
  <div class="grow">
    <div class="commission-card-name" x-text="card.product"></div>
    <div class="hint" x-text="[card.detail, card.rssi != null ? t('web.commissioning.signal', { rssi: card.rssi }) : ''].filter(Boolean).join(' · ')"></div>
  </div>
</div>
<div class="row">
  <span class="status-pill" :class="cardChipClass(card)">
    <span class="spinner" x-show="card.state === 'running'"></span>
    <span x-text="cardChip(card)"></span>
  </span>
  <span class="grow"></span>
  <span class="hint" x-show="card.has_code" x-text="t('web.commissioning.code_ok')"></span>
</div>
<p class="hint" x-show="card.note" x-text="card.note"></p>
<template x-if="card.has_code || card.on_network">
  <div class="commission-card-fields">
    <input type="text" :aria-label="t('web.commissioning.name')" :placeholder="t('web.commissioning.name')"
           :value="card.name" :data-card-name="card.id"
           @change="patchCard(card, { name: $event.target.value })"
           @keydown.enter.prevent="card.state === 'naming' ? confirmName(card, $event.target.value) : patchCard(card, { name: $event.target.value })" />
    <select :aria-label="t('web.commissioning.room')" @change="patchCard(card, { room: $event.target.value || null })">
      <option value="" :selected="!card.room" x-text="t('web.commissioning.no_room')"></option>
      <template x-for="room in roomChips()" :key="room"><option :value="room" :selected="card.room === room" x-text="room"></option></template>
    </select>
  </div>
</template>
<div class="commission-card-actions">
  <button type="button" x-show="['not_nearby','failed'].includes(card.state)" @click="rescanCommissioning()" x-text="t('web.commissioning.scan_again')"></button>
  <button type="button" x-show="['not_nearby','failed'].includes(card.state)" @click="forceCard(card)" x-text="t('web.commissioning.try_anyway')"></button>
  <button type="button" x-show="['ready','not_nearby','failed'].includes(card.state)" @click="removeCard(card)" x-text="t('web.commissioning.remove')"></button>
</div>
```

(Task 9 adds the naming and identify parts to this card.) Use the closest existing icon symbol (`#i-light` or whatever the device tiles use for a light; check the `<symbol id=…>` list near index.html line 140).

app.js methods (complete):

```javascript
    openCommissionDialog() {
      this.commissionDialogOpen = true;
      this.$nextTick(() => {
        this.$refs.commissionDialog.showModal();
        this.$refs.commissionCode?.focus();
      });
      this.loadCommissioning().then(() => {
        if (this.commissionTabShown() === "matter") {
          this.request("POST", "/api/commissioning/scan", { automatic: true }).catch(() => {});
        }
      });
    },

    closeCommissionDialog() {
      this.$refs.commissionDialog.close();
    },

    async loadCommissioning() {
      try {
        this.commissioning = await this.request("GET", "/api/commissioning");
      } catch {
        // Keep the last view; the next poll tries again.
      }
      this.scheduleCommissioningPoll();
    },

    scheduleCommissioningPoll() {
      clearTimeout(this.commissioningTimer);
      this.commissioningTimer = null;
      if (!this.authenticated) return;
      const work = this.commissioningHasWork();
      if (this.commissionDialogOpen) {
        this.commissioningTimer = setTimeout(() => this.loadCommissioning(), 1000);
      } else if (work) {
        this.commissioningTimer = setTimeout(() => this.loadCommissioning(), 5000);
      }
    },

    commissioningHasWork() {
      return (this.commissioning?.cards ?? []).some((card) => ["queued", "running", "naming"].includes(card.state));
    },

    async addCommissionCode() {
      const code = this.commissionCode.trim();
      if (!code) return;
      this.commissioningError = null;
      try {
        await this.request("POST", "/api/commissioning/codes", { code, room: this.commissionDefaultRoom || null });
        this.commissionCode = "";
      } catch (error) {
        this.commissioningError = error.message;
      }
      this.$refs.commissionCode?.focus();
      await this.loadCommissioning();
    },

    async rescanCommissioning() {
      this.commissioningError = null;
      try {
        await this.request("POST", "/api/commissioning/scan", { automatic: false });
      } catch (error) {
        this.commissioningError = error.message;
      }
      await this.loadCommissioning();
    },

    async startCommissioning() {
      await this.request("POST", "/api/commissioning/start").catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    async patchCard(card, fields) {
      try {
        await this.request("PATCH", `/api/commissioning/cards/${card.id}`, fields);
      } catch (error) {
        this.commissioningError = error.message;
      }
      await this.loadCommissioning();
    },

    async removeCard(card) {
      await this.request("DELETE", `/api/commissioning/cards/${card.id}`).catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    async forceCard(card) {
      await this.request("POST", `/api/commissioning/cards/${card.id}/force`).catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    async clearCommissioning() {
      await this.request("POST", "/api/commissioning/clear").catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    readyCount() {
      return (this.commissioning?.cards ?? []).filter((card) => card.state === "ready").length;
    },

    sortedCards() {
      const order = { naming: 0, running: 1, queued: 2, ready: 3, found: 4, not_nearby: 5, failed: 5, done: 6 };
      const naming = this.commissioning?.naming ?? [];
      return [...(this.commissioning?.cards ?? [])].sort(
        (a, b) =>
          order[a.state] - order[b.state] ||
          naming.indexOf(a.id) - naming.indexOf(b.id) ||
          (b.rssi ?? -999) - (a.rssi ?? -999),
      );
    },

    cardChip(card) {
      if (card.state === "running") return card.phase ? t("web.commissioning.phase_" + card.phase) : t("web.commissioning.state_running");
      if (card.state === "queued") return t("web.commissioning.state_queued", { position: card.queue_position });
      return t("web.commissioning.state_" + card.state);
    },

    cardChipClass(card) {
      return { found: "off", ready: "ok", queued: "off", running: "update", naming: "blink", done: "ok", not_nearby: "warn", failed: "warn" }[card.state];
    },

    scanLine() {
      const scan = this.commissioning?.scan;
      if (!scan) return "";
      if (scan.state === "scanning") return t("web.commissioning.scan_running");
      if (scan.state === "blocked") return t("web.commissioning.scan_blocked");
      const found = (this.commissioning.cards ?? []).filter((card) => card.state === "found").length;
      return t("web.commissioning.scan_found", { count: found });
    },

    footLine() {
      const cards = this.commissioning?.cards ?? [];
      const working = cards.filter((card) => ["queued", "running"].includes(card.state)).length;
      const naming = (this.commissioning?.naming ?? []).length;
      const done = cards.filter((card) => card.state === "done").length;
      return [
        working ? t("web.commissioning.foot_working", { count: working }) : "",
        naming ? t("web.commissioning.foot_naming", { count: naming }) : "",
        done ? t("web.commissioning.foot_done", { count: done }) : "",
      ].filter(Boolean).join(" · ");
    },
```

The phase keys: check which phase names the session exposes (`searching`, `found`, `connected`, `joined` from the tracker) and add one `web.commissioning.phase_<name>` per phase. `confirmName` is added in Task 9; until then the Enter handler may call `patchCard` only — Task 9 replaces it.

The `<dialog>` uses the expert modal's pattern: `@close="commissionDialogOpen = false; scheduleCommissioningPoll()"`, backdrop handling with `isBackdropEvent`. The devices page keeps a card with heading `web.devices.commission_heading` (new text `"Commission devices"` / `"Geräte einlernen"`) and a primary button `@click="openCommissionDialog()"`. The Zigbee tab content moves into the dialog unchanged.

Strings (en/de):

```yaml
web.commissioning.code_label:   {en: "Scan or type a code", de: "Code scannen oder eintippen"}
web.commissioning.default_room: {en: "Room for new cards", de: "Raum für neue Karten"}
web.commissioning.no_room:      {en: "No room", de: "Kein Raum"}
web.commissioning.scan_again:   {en: "Scan again", de: "Erneut suchen"}
web.commissioning.scan_running: {en: "Scanning over Bluetooth (10 s) …", de: "Suche per Bluetooth (10 s) …"}
web.commissioning.scan_blocked: {en: "Scanning is paused while a device is commissioned.", de: "Gesucht wird nicht, solange ein Gerät eingelernt wird."}
web.commissioning.scan_found:   {en: "{count} devices in pairing mode nearby.", de: "{count} Geräte im Anlernmodus in der Nähe."}
web.commissioning.signal:       {en: "Signal {rssi} dBm", de: "Signal {rssi} dBm"}
web.commissioning.code_ok:      {en: "Code ✓", de: "Code ✓"}
web.commissioning.name:         {en: "Name", de: "Name"}
web.commissioning.room:         {en: "Room", de: "Raum"}
web.commissioning.try_anyway:   {en: "Try anyway", de: "Trotzdem versuchen"}
web.commissioning.remove:       {en: "Remove", de: "Entfernen"}
web.commissioning.clear:        {en: "Clear list", de: "Liste leeren"}
web.commissioning.start:        {en: "Commission ready devices", de: "Bereite Geräte einlernen"}
web.commissioning.start_n:      {en: "Commission ready devices ({count})", de: "Bereite Geräte einlernen ({count})"}
web.commissioning.state_found:      {en: "Code missing", de: "Code fehlt"}
web.commissioning.state_ready:      {en: "Ready", de: "Bereit"}
web.commissioning.state_queued:     {en: "Queued #{position}", de: "In Warteschlange #{position}"}
web.commissioning.state_running:    {en: "Commissioning …", de: "Wird eingelernt …"}
web.commissioning.state_naming:     {en: "Waiting for a name", de: "Wartet auf Namen"}
web.commissioning.state_done:       {en: "Commissioned", de: "Eingelernt"}
web.commissioning.state_not_nearby: {en: "Not nearby", de: "Nicht in der Nähe"}
web.commissioning.state_failed:     {en: "Failed", de: "Fehlgeschlagen"}
web.commissioning.foot_working: {en: "{count} in progress", de: "{count} in Arbeit"}
web.commissioning.foot_naming:  {en: "{count} waiting for a name", de: "{count} warten auf Namen"}
web.commissioning.foot_done:    {en: "{count} commissioned", de: "{count} eingelernt"}
```

Write them in the file's real multi-line format (`key:` / `  en:` / `  de:`), not the inline form above.

- [ ] **Step 1: Tests first** in `tests/api/test_web.py`:
  - delivery: the dialog (`x-ref="commissionDialog"`), the code field id `commission-code`, `openCommissionDialog()` on the devices page, `sortedCards()` in an `x-for`, `t('web.commissioning.default_room')`;
  - node bindings (use `_app_state`): `sortedCards()` orders naming → running → queued → ready → found → not_nearby → done and by RSSI; `cardChip` for each state; `readyCount`; `scanLine` for the three scan states; `commissioningHasWork`;
  - update the existing tests of the single-code Matter tab: every test that asserts markup still present in the new dialog (code field placeholder, code help) keeps passing; a test asserting behaviour the new tab no longer has (e.g. `commissionDevice()` posting to `/api/devices/commission` from the Matter tab) is removed — list each removed or changed test with the reason in the report.
- [ ] **Step 2: Run red, implement, run green:** `uv run pytest tests/api/test_web.py tests/test_i18n.py -q -W error -p no:cacheprovider`
- [ ] **Step 3: Lint, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): commission devices from cards of what is nearby

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: WebUI — naming line, identify, background line, tile menu

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`; browser harness run (Step 4)

**Interfaces:**
- Consumes: Task 7 routes, Task 8 state and methods; `DeviceOut.identify` (Task 4).
- Produces: `confirmName(card, name)`, `skipName(card)`, `toggleCardIdentify(card)`, `toggleDeviceIdentify(device)`, `deviceBlinking` (device id or null, from `POST /api/devices/{id}/identify` responses and a 30 s local timer), `isNamingFront(card)`.

- [ ] **Step 1: Tests first** (`tests/api/test_web.py`): delivery of the naming prompt (`t('web.commissioning.naming_prompt')`), the identify buttons in the card and in the tile kebab menu (`toggleDeviceIdentify(device)` inside the menu, shown only with `device.identify`), the background line on the devices page (`commissioningHasWork() && !commissionDialogOpen`); node binding tests for `isNamingFront`, and that after a poll in which a new card became the front of the naming line, the focus target is that card's name field (`$nextTick` focuses `[data-card-name="<id>"]` — test the method that picks the id, `namingFocusTarget(previous, current)`).

- [ ] **Step 2: Implement**

Card additions (inside the Task 8 card):

```html
<p class="commission-naming-prompt" x-show="isNamingFront(card)" x-text="t('web.commissioning.naming_prompt')"></p>
<div class="commission-card-actions">
  <button type="button" class="primary" x-show="card.state === 'naming'"
          @click="confirmName(card, $root.querySelector('[data-card-name=\'' + card.id + '\']')?.value ?? card.name)"
          x-text="t('web.commissioning.confirm_name')"></button>
  <button type="button" x-show="card.state === 'naming'" @click="skipName(card)" x-text="t('web.commissioning.skip_name')"></button>
  <button type="button" x-show="card.can_identify && ['naming','done'].includes(card.state)"
          :class="{ 'is-on': commissioning?.blinking_card === card.id }"
          @click="toggleCardIdentify(card)"
          x-text="commissioning?.blinking_card === card.id ? t('web.commissioning.identify_stop') : t('web.commissioning.identify')"></button>
</div>
```

The card's icon gets `:class="{ 'is-blinking': commissioning?.blinking_card === card.id }"`; CSS animates it with the copper → yellow pulse of the prototype and respects `prefers-reduced-motion`.

app.js:

```javascript
    isNamingFront(card) {
      return card.state === "naming" && (this.commissioning?.naming ?? [])[0] === card.id;
    },

    namingFocusTarget(previous, current) {
      const before = previous?.naming?.[0] ?? null;
      const now = current?.naming?.[0] ?? null;
      return now !== null && now !== before ? now : null;
    },

    async confirmName(card, name) {
      try {
        await this.request("PATCH", `/api/commissioning/cards/${card.id}`, { name });
        await this.request("POST", `/api/commissioning/cards/${card.id}/confirm-name`);
      } catch (error) {
        this.commissioningError = error.message;
      }
      await this.loadCommissioning();
    },

    async skipName(card) {
      await this.request("POST", `/api/commissioning/cards/${card.id}/skip-name`).catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    async toggleCardIdentify(card) {
      const on = this.commissioning?.blinking_card !== card.id;
      await this.request("POST", `/api/commissioning/cards/${card.id}/identify`, { on }).catch((error) => { this.commissioningError = error.message; });
      await this.loadCommissioning();
    },

    async toggleDeviceIdentify(device) {
      const on = this.deviceBlinking !== device.id;
      try {
        await this.request("POST", `/api/devices/${device.id}/identify`, { on });
        this.deviceBlinking = on ? device.id : null;
        clearTimeout(this.deviceBlinkTimer);
        if (on) this.deviceBlinkTimer = setTimeout(() => { if (this.deviceBlinking === device.id) this.deviceBlinking = null; }, 30000);
      } catch (error) {
        this.deviceActionError = error.message;
      }
    },
```

In `loadCommissioning()` (Task 8), keep the previous view, and after assigning the new one: `const target = this.namingFocusTarget(previous, this.commissioning); if (target !== null && this.commissionDialogOpen) this.$nextTick(() => document.querySelector(`[data-card-name="${target}"]`)?.focus());`.

Devices page, above the tiles:

```html
<div class="commission-background" x-show="commissioningHasWork() && !commissionDialogOpen" x-cloak>
  <span class="spinner" aria-hidden="true"></span>
  <span class="grow" x-text="t('web.commissioning.background', { working: (commissioning?.cards ?? []).filter((c) => ['queued','running'].includes(c.state)).length, naming: (commissioning?.naming ?? []).length })"></span>
  <button type="button" @click="openCommissionDialog()" x-text="t('web.commissioning.open_dialog')"></button>
</div>
```

`startApp`: call `this.loadCommissioning()` once after login so a session that runs in the background shows its line.

Tile kebab menu, before "Export":

```html
<button class="tile-menu-item" x-show="device.identify"
        @click="closeTileMenu($el); toggleDeviceIdentify(device)"
        x-text="deviceBlinking === device.id ? t('web.commissioning.identify_stop') : t('web.commissioning.identify')"></button>
```

Strings:

```yaml
web.commissioning.naming_prompt: en "This device is blinking. Which one is it? Enter a name and press Enter." / de "Dieses Gerät blinkt. Welches ist es? Tragen Sie einen Namen ein und drücken Sie Enter."
web.commissioning.confirm_name:  en "Use name" / de "Name übernehmen"
web.commissioning.skip_name:     en "Continue without a name" / de "Ohne Namen weiter"
web.commissioning.identify:      en "Identify" / de "Identifizieren"
web.commissioning.identify_stop: en "Stop identifying" / de "Identifizieren stoppen"
web.commissioning.background:    en "Commissioning in the background: {working} in progress, {naming} waiting for a name." / de "Einlernen läuft im Hintergrund: {working} in Arbeit, {naming} warten auf Namen."
web.commissioning.open_dialog:   en "Open dialog" / de "Dialog öffnen"
```

(Write them in the file's multi-line format.)

- [ ] **Step 3: Run tests, lint**

```bash
uv run pytest tests/api/test_web.py tests/test_i18n.py -q -W error -p no:cacheprovider
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
```

- [ ] **Step 4: Browser harness** (the controller may run this instead): a throwaway script in the session scratchpad (not the repository) that builds `build_app` with a `CommissioningSession` over the Task 6 fakes, a `FakeReader` holding three adverts (KAJPLATS E14 3840, KAJPLATS E27 1059, BILRESA 2000), a fake client whose commissioning takes ~5 s, and serves on `127.0.0.1:8098` with password `harness-pass`. Walk the prototype's steps: open → cards appear → scan two QR codes and one numeric code → start → first device blinks with focus in its name field → Enter → next blinks → close dialog → background line → reopen → tile kebab "Identify". Check German and 375 px width. Fix what it shows; delete the harness.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): name each new device while it blinks, and identify any device

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Changelog and the full run

- [ ] **Step 1:** Under `## [Unreleased]` in `CHANGELOG.md`, `### Added`:

```markdown
- **Commission many devices in one go.** "Commission devices" lists every
  Matter device in pairing mode nearby, with its product name. Scan the codes
  one after another, in any order; each one finds its device. The bridge
  commissions them in the background while you keep scanning, and each new
  device blinks so you can name it before the next one is done. Name and room
  can be set before, during and after.
- **Identify any device.** A device's menu has "Identify": the lamp blinks
  for up to 30 seconds, and only one device blinks at a time.
```

and in `### Before you update` (create it if Unreleased has none; append otherwise): `- **The database schema rises from 16 to 17.** Nothing needs doing by hand.`

- [ ] **Step 2:** The full run:

```bash
uv run pytest --collect-only -q | tail -1
uv run pytest -q --ignore-glob="tests/test_*.py" -p no:cacheprovider
uv run pytest -q tests/test_*.py -p no:cacheprovider
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

Each pytest call in the foreground with a timeout of up to 600000 ms. The two halves' counts must add up to the collected count.

- [ ] **Step 3:** Commit `docs(changelog): announce the commissioning queue and identify`.

---

### Task 11: On the test Pi `pi@10.0.1.56` (design section 13) — with Lucien

Points 1 (scan permission) passed on October 2, 2026. Remaining, each recorded in design section 13:

- [ ] Build the branch image on the Pi, restart only loxmatter (`docker compose up -d --no-deps loxmatter`).
- [ ] Scan and commissioning alternating three times; `journalctl -k` shows no Bluetooth wedge line; `hciconfig` 0 errors.
- [ ] DCL reachable from the container (a card shows the KAJPLATS name).
- [ ] Identify on KAJPLATS (Matter) and on a Zigbee lamp; stop works; only one blinks.
- [ ] With Lucien's go-ahead: three factory-reset devices through the queue; time per device and the operator's waiting time.
