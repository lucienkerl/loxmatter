"""The one value scale of a configuration value (design 2026-11-08,
section 2): what the web UI shows, Loxone sends and the input reports."""

from __future__ import annotations

import pytest

from loxmatter.device_config import (
    ConfigOption,
    OptionValueError,
    attribute_of_command,
    choice,
    from_raw,
    is_option_command,
    option_command_id,
    to_raw,
)
from loxmatter.profiles.light_commands import LUMITECH

KEEP_TIME = ConfigOption(
    endpoint=1,
    cluster_id=0x0500,
    attribute_id=0xF001,
    name="keep_time",
    kind="enum",
    fallback_title="Keep time",
    choices=(choice(0, "_30_seconds"), choice(1, "_60_seconds"), choice(2, "_120_seconds")),
)
SENSITIVITY = ConfigOption(
    endpoint=1,
    cluster_id=0x0500,
    attribute_id=0x0013,
    name="current_zone_sensitivity_level",
    kind="enum",
    fallback_title="Sensitivity",
    choices=(choice(0, "Low"), choice(1, "Medium"), choice(2, "High")),
)
DISTANCE = ConfigOption(
    endpoint=1,
    cluster_id=0xEF00,
    attribute_id=0xEF04,
    name="static_detection_distance",
    kind="number",
    fallback_title="Static detection distance",
    unit="m",
    minimum=0,
    maximum=10,
    step=1,
    multiplier=0.01,
)
LED = ConfigOption(
    endpoint=1,
    cluster_id=0xEF00,
    attribute_id=0xEF6B,
    name="find_switch",
    kind="switch",
    fallback_title="LED indicator",
)


def test_an_enum_whose_choices_name_seconds_is_set_by_the_seconds():
    """Loxone sends 60 for a minute, not the enum's position 1."""
    assert KEEP_TIME.by_number
    assert to_raw(KEEP_TIME, "60") == 1
    assert from_raw(KEEP_TIME, 2) == 120.0
    with pytest.raises(OptionValueError):
        to_raw(KEEP_TIME, "1")


def test_an_enum_without_numbers_is_set_by_its_raw_value():
    assert not SENSITIVITY.by_number
    assert to_raw(SENSITIVITY, "2") == 2
    assert from_raw(SENSITIVITY, 1) == 1.0
    with pytest.raises(OptionValueError):
        to_raw(SENSITIVITY, "3")


def test_a_number_is_shown_times_its_multiplier_and_checked_against_its_range():
    assert from_raw(DISTANCE, 250) == pytest.approx(2.5)
    assert to_raw(DISTANCE, "2.5") == 250
    with pytest.raises(OptionValueError):
        to_raw(DISTANCE, "11")
    with pytest.raises(OptionValueError):
        to_raw(DISTANCE, "abc")
    with pytest.raises(OptionValueError):
        to_raw(DISTANCE, "nan")


def test_a_switch_takes_zero_and_one_only():
    assert to_raw(LED, "1") == 1
    assert to_raw(LED, "0") == 0
    assert from_raw(LED, True) == 1.0
    with pytest.raises(OptionValueError):
        to_raw(LED, "2")


def test_a_member_name_spelling_two_numbers_names_none():
    assert choice(0, "_2_5_m").number is None
    assert choice(0, "PIR_and_radar").number is None
    assert choice(0, "_10_seconds").number == 10.0


def test_the_option_survives_its_json_row():
    for option in (KEEP_TIME, SENSITIVITY, DISTANCE, LED):
        assert ConfigOption.from_json(option.to_json()) == option


def test_an_option_command_is_told_apart_from_every_real_command_and_lumitech():
    command_id = option_command_id(0xF001)
    assert command_id < 0
    assert attribute_of_command(command_id) == 0xF001
    assert option_command_id(0) != 0
    assert is_option_command(0x0500, command_id)
    assert not is_option_command(*LUMITECH)
    assert not is_option_command(6, 1)
