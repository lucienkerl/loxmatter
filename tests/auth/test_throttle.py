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

"""Tests for login throttling (Spec 8).

The core question: does it slow things down after enough failed
attempts, does it let the legitimate operator back through afterward,
and does it really only hit the address that got it wrong?
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
    """Otherwise the operator would lock themselves out after five typos,
    even though they have since entered the password correctly."""
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
