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
        kind="long",
        discriminator=3840,
        vendor_id=0xFFF1,
        product_id=0x8000,
        ble=True,
        on_network=False,
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
    assert (payload.kind, payload.discriminator, payload.vendor_id, payload.product_id) == (
        "short",
        0,
        0,
        0,
    )


@pytest.mark.parametrize("code", ["34970112333"])
def test_a_typo_is_named(code):
    with pytest.raises(TypoError):
        decode(code)


@pytest.mark.parametrize("code", ["", "abc", "1234", "40000000007", "MT:", "MT:!!!"])
def test_unreadable_codes_raise(code):
    with pytest.raises(UnreadableCodeError):
        decode(code)


def test_non_ascii_digits_raise_unreadable():
    with pytest.raises(UnreadableCodeError):
        decode("3497011233²")


def test_arabic_indic_digits_raise_unreadable():
    with pytest.raises(UnreadableCodeError):
        decode("3497011233٢")


@pytest.mark.parametrize("code", ["3497\t011\n2332"])
def test_whitespace_including_tab_and_newline_is_stripped(code):
    assert decode(code) == SetupPayload(
        kind="short", discriminator=15, vendor_id=None, product_id=None, ble=None, on_network=None
    )


def test_the_error_text_never_contains_the_code():
    with pytest.raises(UnreadableCodeError) as raised:
        decode("MT:SECRET99")
    assert "SECRET" not in str(raised.value)


def test_error_text_never_quotes_non_ascii_digits():
    with pytest.raises(UnreadableCodeError) as raised:
        decode("3497011233²")
    assert "²" not in str(raised.value)


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
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    browser = json.loads(result.stdout)
    for code, theirs in zip(codes, browser, strict=True):
        ours = decode(code)
        assert {"kind": ours.kind, "discriminator": ours.discriminator} == theirs
