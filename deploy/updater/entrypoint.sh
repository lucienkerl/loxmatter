#!/bin/sh
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
# The sidecar's loop - design "Applying updates through the web UI"
# (2026-09-08), section 6.
#
# Deliberately thin: all the logic lives in update-once.sh, entirely. Only
# that way can a single run be invoked in a test, without spinning up an
# endless loop and having to kill it again.
#
# A single failed pass must not terminate the sidecar. It is the only
# thing still able to report a broken state at all - a container that
# exits on error takes exactly that report down with it. That is why the
# loop below never treats a non-zero worker exit (timeout included) as a
# reason to stop.
set -u

# This script is PID 1 inside the container. PID 1 gets none of the
# default signal dispositions a normal process gets - without an explicit
# trap, `docker stop` (SIGTERM) does nothing at all until the runtime's
# grace period runs out and SIGKILL lands. Worse, the update-once.sh child
# this shell is synchronously blocked on never sees the signal either: it
# is killed together with the shell, possibly mid `docker compose up -d
# --force-recreate` of the bridge itself. Forwarding the signal to the
# child on receipt gives it a chance to reach a safe point instead of
# being cut off arbitrarily.
#
# Reaching the child requires its PID, which `wait` only makes available
# for a job started in the background - hence `worker & child_pid=$!;
# wait "$child_pid"` below instead of a plain synchronous call.
child_pid=""
terminated=0

forward_signal() {
  terminated=1
  if [ -n "$child_pid" ]; then
    kill -TERM "$child_pid" 2>/dev/null
  fi
}
trap forward_signal TERM INT

# The worker path and the loop-body command are both overridable so this
# script is exercisable against a stub in tests, without writing into
# /opt/loxmatter. Production leaves both at their defaults.
WORKER="${WORKER:-/opt/loxmatter/update-once.sh}"

# update-once.sh's own longest legitimate wait is up to 120s for the
# bridge's health endpoint (spec section 6), and it may hit that wait
# *twice* in a single run: once after bringing the update up, once more
# after a rollback if the first health check fails. Add a database
# backup, an image pull over a slow uplink, and two container
# recreations (`docker compose up -d --force-recreate`, twice), and a
# generous multiple of the 240s of known waiting alone is warranted. 600s
# (10 minutes) leaves real headroom above that: a limit that can fire
# during a legitimate slow update would be worse than no limit at all -
# it would kill the update mid-way, not just report a hang. What this
# guards against is a hang with no time bound whatsoever, which would
# block the loop forever and defeat the one job this sidecar has: still
# being there, and still reporting, when the bridge is not.
WORKER_TIMEOUT_SECONDS="${WORKER_TIMEOUT_SECONDS:-600}"

while [ "$terminated" -eq 0 ]; do
  timeout "$WORKER_TIMEOUT_SECONDS" "$WORKER" &
  child_pid=$!

  # The two lines above are not one atomic step. `child_pid` was reset to
  # "" at the end of the previous pass (below), and stays "" for however
  # long it takes the shell to get from starting the background job to
  # storing its PID. A SIGTERM landing in exactly that gap runs
  # forward_signal with child_pid still "" - its `[ -n "$child_pid" ]`
  # guard is false, so the trap only records terminated=1 and forwards
  # nothing. The while loop below then sees terminated=1 and exits
  # promptly, so `docker stop` is satisfied and PID 1 is gone - but the
  # worker it just forked is still running, never signaled, parented to
  # nothing. That is the orphaned-mid-update outcome this whole trap
  # exists to prevent. This line re-checks the flag now that child_pid is
  # finally known, and forwards late if a signal already arrived in that
  # gap: a signal arriving any earlier only sets terminated (child_pid
  # was ""), a signal arriving any later runs the trap with child_pid
  # already set (so the trap forwards it directly). Either way the
  # worker gets its TERM. It looks redundant next to a trap that sends
  # the same signal - it is not; it is the only thing that closes the
  # gap between backgrounding the worker and knowing its PID.
  [ "$terminated" -eq 1 ] && kill -TERM "$child_pid" 2>/dev/null

  # `wait` returns as soon as a trapped signal arrives (POSIX 2.9.3.1),
  # which can be before the child has actually exited - the trap above
  # only just sent it TERM. Keep waiting on the same PID until it is
  # truly gone, so `status` reflects the worker's real exit rather than
  # the interruption, and so the child is fully reaped before this shell
  # considers exiting.
  wait "$child_pid"
  status=$?
  while [ "$terminated" -eq 1 ] && kill -0 "$child_pid" 2>/dev/null; do
    wait "$child_pid"
    status=$?
  done
  child_pid=""

  if [ "$status" -eq 124 ]; then
    # `timeout` uses 124 for its own timeout exit, distinct from any exit
    # status update-once.sh itself could produce - report the wait limit
    # having fired, since it means an update run is stuck on the same
    # host for longer than any legitimate operation should take.
    echo "entrypoint: update-once.sh exceeded ${WORKER_TIMEOUT_SECONDS}s and was killed" >&2
  fi

  [ "$terminated" -eq 1 ] && break

  # LOOP_ONCE lets a test run exactly one pass and return, instead of
  # looping forever and needing to be killed. It only changes how many
  # times the `while` condition above is re-checked - every pass still
  # goes through the same timeout-wrapped worker call and the same status
  # handling production does - so it cannot open a gap between what a
  # test observes and what the container actually runs.
  if [ "${LOOP_ONCE:-0}" = "1" ]; then
    break
  fi

  sleep 2
done
