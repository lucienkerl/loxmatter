#!/bin/sh
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
#
# The watchdog job of the updater sidecar - design "Thread setup without
# handwork" (2026-09-14), section 4.2. One pass: run
# scripts/otbr-watchdog.sh from the checkout under /repo. The script runs
# from there, not from a copy baked into this image, so a later fix to
# the watchdog arrives with the ordinary bridge update, without an
# updater refresh - and nobody needs a crontab line on the host any more,
# entrypoint.sh calls this every minute on its own (see its own comment).

set -u

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
SCRIPT="${LOXMATTER_WATCHDOG_SCRIPT:-/repo/scripts/otbr-watchdog.sh}"
LOG="$UPDATE_DIR/otbr-watchdog.log"

# The updater may meet an older checkout - a bridge rollback to 0.4.0, say.
# That version of the script still reads /proc/net/if_inet6 to see whether
# Thread is up, which a container outside the host's network namespace
# cannot see, and would restart otbr every minute believing Thread is
# always down. The marker line is how a container-ready script identifies
# itself; without it, doing nothing is the safe choice, not running it and
# hoping. OTBR_WATCHDOG_LOCK is left exactly as the environment gave it:
# unset, so the script below locks itself, the same file the /repo bind
# mount also gives a host cron job - the shared inode is the whole point
# (spec section 4.5).
[ -f "$SCRIPT" ] || exit 0
grep -q '^# loxmatter-watchdog: container-ready' "$SCRIPT" || exit 0

mkdir -p "$UPDATE_DIR" 2>/dev/null || true

# Keep the log from growing without bound, newest lines kept. A failure
# here - disk full, an unwritable directory - must never stop the watchdog
# from running: it having run is what matters, not whether its log got
# trimmed on this particular pass.
trimmed="$UPDATE_DIR/otbr-watchdog.log.trimmed"
if [ -f "$LOG" ] && tail -n 2000 "$LOG" >"$trimmed" 2>/dev/null; then
  mv "$trimmed" "$LOG" 2>/dev/null || true
fi

# Trimmed BEFORE the run, so the run can be the last thing and replace this
# shell: entrypoint.sh's `timeout` signals only its direct child, and a
# worst-case watchdog run (every docker call at its limit, twelve waits for
# the network) is longer than the worker's limit. Run as a grandchild, bash
# would outlive the kill, keep the lock and go on restarting otbr unseen.
exec bash "$SCRIPT" >>"$LOG" 2>&1
