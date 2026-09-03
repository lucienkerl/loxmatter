"""Tests fuer die Login-Drosselung (Spec 8).

Die Kernfrage: bremst sie nach genug Fehlversuchen, laesst sie den
rechtmaessigen Betreiber danach wieder durch, und trifft sie wirklich nur
die Adresse, die daneben lag?
"""

from __future__ import annotations

from loxmatter.auth.throttle import (
    FAILURES_BEFORE_THROTTLING,
    THROTTLE_SECONDS,
    LoginThrottle,
)


def test_the_first_attempt_is_never_throttled():
    throttle = LoginThrottle()
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0


def test_throttling_starts_after_the_configured_number_of_failures():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING - 1):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0

    throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=0.0) > 0


def test_the_block_expires():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=THROTTLE_SECONDS + 1) == 0


def test_a_success_clears_the_counter():
    """Sonst sperrte sich der Betreiber nach fuenf Vertippern selbst aus,
    obwohl er das Passwort inzwischen richtig eingegeben hat."""
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    throttle.record_success("10.0.0.1")
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0


def test_one_address_does_not_block_another():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.2", now=0.0) == 0


def test_retry_after_counts_down():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    early = throttle.retry_after("10.0.0.1", now=1.0)
    late = throttle.retry_after("10.0.0.1", now=THROTTLE_SECONDS - 1.0)
    assert early > late > 0
