"""The pure rules of firmware updates (design 2026-09-30, sections 5 and 9.3).

The spec-version values are the ones measured on the test Pi on
September 30, 2026 - see the design, section 9.3."""

import pytest

from loxmatter.firmware import states
from loxmatter.firmware.states import derive_state, format_spec_version, job_state_for


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0x01030000, "1.3"),  # IKEA BILRESA, ALPSTUGA, MYGGSPRAY, TIMMERFLOTTE, KLIPPBOK
        (0x01040000, "1.4"),  # IKEA GRILLPLATS, MYGGBETT, KAJPLATS
        (0x01040100, "1.4.1"),  # Tasmota 15.6.0
        (None, states.SPEC_VERSION_BEFORE_1_3),  # Tasmota 13.3.0: no 0/40/21
    ],
)
def test_format_spec_version_matches_the_measured_devices(raw, expected):
    assert format_spec_version(raw, "matter") == expected


def test_format_spec_version_ignores_the_reserved_low_byte():
    assert format_spec_version(0x010400FF, "matter") == "1.4"


def test_format_spec_version_is_none_for_a_zigbee_device():
    assert format_spec_version(None, "zigbee") is None
    assert format_spec_version(0x01040000, "zigbee") is None


@pytest.mark.parametrize(
    ("update_state", "expected"),
    [
        (None, None),
        (0, None),  # Unknown
        (1, None),  # Idle
        (2, states.TRANSFERRING),  # Querying
        (3, states.TRANSFERRING),  # DelayedOnQuery
        (4, states.TRANSFERRING),  # Downloading
        (5, states.APPLYING),  # Applying
        (6, states.APPLYING),  # DelayedOnApply
        (7, states.APPLYING),  # RollingBack
        (8, states.TRANSFERRING),  # DelayedOnUserConsent
    ],
)
def test_job_state_for_maps_every_update_state(update_state, expected):
    assert job_state_for(update_state) == expected


def _derive(**overrides):
    values = {
        "has_requestor": True,
        "installed": 16842752,  # KAJPLATS 1.1.0
        "checked_at": "2026-09-30T06:00:00+00:00",
        "check_error": None,
        "offer_version": None,
        "job_state": None,
    }
    values.update(overrides)
    return derive_state(**values)


def test_a_device_without_the_requestor_cluster_has_no_source():
    assert _derive(has_requestor=False, checked_at=None) == states.NO_SOURCE


def test_a_device_never_checked_is_unchecked():
    assert _derive(checked_at=None) == states.UNCHECKED


def test_a_check_without_offer_found_nothing():
    assert _derive() == states.NONE_FOUND


def test_a_newer_offer_is_available():
    assert _derive(offer_version=16908288) == states.AVAILABLE  # 1.2.0


def test_an_offer_the_device_already_reached_is_not_available():
    assert _derive(offer_version=16842752) == states.NONE_FOUND


def test_an_offer_for_a_device_with_unknown_version_is_available():
    assert _derive(installed=None, offer_version=16908288) == states.AVAILABLE


def test_a_failed_check_wins_over_the_kept_offer():
    assert _derive(check_error="no internet", offer_version=16908288) == states.CHECK_FAILED


@pytest.mark.parametrize("job", [states.TRANSFERRING, states.APPLYING, states.STALLED])
def test_a_running_job_wins_over_everything(job):
    assert _derive(has_requestor=False, check_error="x", job_state=job) == job


@pytest.mark.parametrize("job", [states.FAILED, states.INTERRUPTED])
def test_an_ended_job_is_shown_until_the_next_check(job):
    assert _derive(offer_version=16908288, job_state=job) == job
