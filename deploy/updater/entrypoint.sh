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
# `|| true`: a single failed run must not terminate the sidecar. It is the
# only one still able to report a broken state at all - a container that
# exits on error takes exactly that report down with it.
set -u

while true; do
  /opt/loxmatter/update-once.sh || true
  sleep 2
done
