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

"""scripts/home-assistant-handover.sh against stand-ins for docker, curl,
sudo and chown.

What counts is the ORDER of what it does to someone's running smart home:
read everything first and refuse before the first change, stop Home
Assistant's containers only once the Thread network is safely read, put
the network into loxmatter's border router before the bridge starts, and
give back exactly what it took. The fake Home Assistant is the Pi of the
first real migration (7 November 2026)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "home-assistant-handover.sh"
DATASET = "0e080000000000010000000300001835060004001fffe0"

_DOCKER = """echo "docker $*" >> "$STUB_LOG"
case "$1" in
  info) exit 0 ;;
  ps)
    printf 'homeassistant\\tghcr.io/home-assistant/home-assistant:stable\\n'
    printf 'matter-server\\tghcr.io/home-assistant-libs/python-matter-server:stable\\n'
    [ -n "${NO_HA_OTBR-}" ] || printf 'homeassistant-otbr-1\\topenthread/border-router:latest\\n'
    ;;
  inspect)
    case "$3" in
      *Mounts*) echo "$FAKE_MATTER_DATA" ;;
      *Devices*) printf '/dev/serial/by-id/usb-SONOFF_MG24-if00-port0\\n/dev/net/tun\\n' ;;
      *Env*) printf 'OT_RCP_DEVICE=spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800\\nOT_REST_LISTEN_PORT=8082\\n' ;;
      *RestartPolicy*) echo unless-stopped ;;
    esac
    ;;
esac
exit 0
"""

_CURL = """echo "curl $*" >> "$STUB_LOG"
for a in "$@"; do url="$a"; done
case "$*" in
  *8082/node/dataset/active*) printf '%s' "${HA_DATASET-}"; [ -n "${HA_DATASET-}" ] || exit 22 ;;
  *"-X PUT"*) [ -z "${PUT_FAILS-}" ] || exit 22 ;;
esac
exit 0
"""


@pytest.fixture
def handover(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / ".env").write_text(
        "COMPOSE_PROFILES=thread\nRADIO_DEVICE=/dev/serial/by-id/usb-SONOFF_MG24-if00-port0\nOTBR_REST_PORT=8084\n"
    )
    ha_data = tmp_path / "matter_data"
    ha_data.mkdir()
    (ha_data / "chip.json").write_text("{}")
    (ha_data / "7523513569487925123.json").write_text('{"nodes": {}}')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    stubs = {
        "docker": _DOCKER,
        "curl": _CURL,
        "sudo": 'exec "$@"\n',
        "chown": 'echo "chown $*" >> "$STUB_LOG"\n',
        "sleep": "exit 0\n",
    }
    for name, body in stubs.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def run(command, **env):
        if log.exists():
            log.unlink()
        proc = subprocess.run(
            [str(SCRIPT), command],
            env={
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "STUB_LOG": str(log),
                "LOXMATTER_STACK_DIR": str(stack),
                "FAKE_MATTER_DATA": str(ha_data),
                "HA_DATASET": DATASET,
                "HANDOVER_READY_SECONDS": "2",
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return proc, calls

    run.stack = stack
    run.ha_data = ha_data
    return run


def _index(calls, prefix):
    return next(i for i, call in enumerate(calls) if call.startswith(prefix))


def test_take_reads_the_network_before_it_stops_anything_and_starts_the_bridge_last(handover):
    proc, calls = handover("take")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    read = _index(
        calls,
        "curl -sf --max-time 5 -H Accept: text/plain http://127.0.0.1:8082/node/dataset/active",
    )
    stop_matter = _index(calls, "docker stop matter-server")
    stop_otbr = _index(calls, "docker stop homeassistant-otbr-1")
    start_otbr = _index(calls, "docker compose up -d otbr")
    put_dataset = _index(calls, "curl -sf --max-time 10 -X PUT -H Content-Type: text/plain")
    enable = _index(calls, "curl -sf --max-time 10 -X PUT -H Content-Type: application/json")
    start_all = calls.index("docker compose up -d")
    assert read < stop_matter < stop_otbr < start_otbr < put_dataset < enable < start_all
    assert f"--data {DATASET} http://127.0.0.1:8084/node/dataset/active" in calls[put_dataset]
    # Kept from starting at boot, and out of the way of loxmatter's own
    # `matter-server`.
    assert "docker update --restart=no matter-server" in calls
    assert "docker rename matter-server matter-server-homeassistant" in calls
    assert "chown -R 1000:1000 " + str(handover.stack / "data") in calls
    assert (handover.stack / "data" / "7523513569487925123.json").exists()
    assert (handover.ha_data / "7523513569487925123.json").exists()
    # The network key is printed nowhere.
    assert DATASET not in proc.stdout + proc.stderr


def test_an_unreadable_thread_network_changes_nothing(handover):
    proc, calls = handover("take", HA_DATASET="")
    assert proc.returncode != 0
    assert "Nothing was changed" in proc.stderr
    assert not any(
        call.startswith(("docker stop", "docker update", "docker rename")) for call in calls
    )
    assert not (handover.stack / ".homeassistant-handover").exists()


def test_existing_matter_data_of_loxmatters_own_changes_nothing(handover):
    (handover.stack / "data").mkdir()
    (handover.stack / "data" / "chip.json").write_text("{}")
    proc, calls = handover("take")
    assert proc.returncode != 0
    assert "already has Matter data" in proc.stderr
    assert not any(call.startswith("docker stop") for call in calls)


def test_the_wrong_stick_in_env_changes_nothing(handover):
    env_file = handover.stack / ".env"
    env_file.write_text(env_file.read_text().replace("usb-SONOFF_MG24", "usb-other"))
    proc, calls = handover("take")
    assert proc.returncode != 0
    assert "RADIO_DEVICE" in proc.stderr
    assert not any(call.startswith("docker stop") for call in calls)


def test_a_refused_network_points_at_give_back(handover):
    proc, _ = handover("take", PUT_FAILS="1")
    assert proc.returncode != 0
    assert "give-back" in proc.stderr


def test_without_a_border_router_only_matter_is_taken(handover):
    proc, calls = handover("take", NO_HA_OTBR="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not any("dataset" in call for call in calls)
    assert "docker compose up -d otbr" not in calls
    assert "docker compose up -d" in calls


def test_take_twice_is_refused(handover):
    first, _ = handover("take")
    assert first.returncode == 0
    second, calls = handover("take")
    assert second.returncode != 0
    assert "Already taken over" in second.stderr
    assert not any(call.startswith("docker stop") for call in calls)


def test_give_back_restores_names_restart_policies_and_containers(handover):
    first, _ = handover("take")
    assert first.returncode == 0
    proc, calls = handover("give-back")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stop_ours = _index(calls, "docker compose stop loxmatter matter-server otbr")
    rename = calls.index("docker rename matter-server-homeassistant matter-server")
    start = calls.index("docker start matter-server")
    assert stop_ours < rename < start
    assert "docker update --restart=unless-stopped matter-server" in calls
    assert "docker update --restart=unless-stopped homeassistant-otbr-1" in calls
    assert "docker start homeassistant-otbr-1" in calls
    assert not (handover.stack / ".homeassistant-handover").exists()


def test_give_back_without_a_take_over_is_refused(handover):
    proc, calls = handover("give-back")
    assert proc.returncode != 0
    assert "Nothing to give back" in proc.stderr
    assert not any(call.startswith("docker start") for call in calls)


def test_check_only_reads(handover):
    proc, calls = handover("check")
    assert proc.returncode == 0
    assert "container matter-server" in proc.stdout
    assert "usb-SONOFF_MG24-if00-port0 at 460800 baud, REST port 8082" in proc.stdout
    changes = ("docker stop", "docker start", "docker update", "docker rename", "docker compose")
    assert not any(call.startswith(changes) for call in calls)
    assert not any("-X PUT" in call for call in calls)


def test_loxmatters_border_router_may_take_over_home_assistants_rest_port(handover):
    # The installer picks Home Assistant's port so its OTBR integration keeps
    # a border router: theirs stops before ours starts.
    env_file = handover.stack / ".env"
    env_file.write_text(env_file.read_text().replace("OTBR_REST_PORT=8084", "OTBR_REST_PORT=8082"))
    proc, calls = handover("take")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stop_otbr = _index(calls, "docker stop homeassistant-otbr-1")
    put_dataset = _index(calls, "curl -sf --max-time 10 -X PUT -H Content-Type: text/plain")
    assert stop_otbr < put_dataset
    assert "http://127.0.0.1:8082/node/dataset/active" in calls[put_dataset]
    # Paths are printed resolved, without scripts/../
    assert "/../" not in proc.stdout
