"""The compose file must stay usable without a Thread radio module.

`otbr` passes through a device with `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}`.
If it's missing, `docker compose up` fails ("error gathering device
information") - even for someone who only wants to connect WiFi Matter
devices. These tests pin down that `otbr` therefore sits behind a
profile and nobody outside that profile depends on it.
"""

from pathlib import Path, PurePosixPath

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "testhost" / "docker-compose.yml"


def _stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_otbr_is_behind_the_thread_profile() -> None:
    assert _stack()["services"]["otbr"]["profiles"] == ["thread"]


def test_no_service_outside_the_profile_depends_on_otbr() -> None:
    # Compose aborts if an active service depends on one that's disabled by a
    # profile. matter-server must therefore no longer list otbr in
    # depends_on.
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "otbr" not in service.get("depends_on", []), name


def test_only_otbr_needs_the_radio_module() -> None:
    # Anything that touches RADIO_DEVICE must sit in the profile - otherwise
    # WiFi-only operation fails again on a missing device.
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "devices" not in service, name


def test_the_bridge_runs_from_a_published_image() -> None:
    # Before 0.2.0, Compose built the image on the Pi - five to ten
    # minutes, with PyPI and memory as sources of failure in the middle of an update.
    image = _stack()["services"]["loxmatter"]["image"]
    assert image.startswith("ghcr.io/lucienkerl/loxmatter:")
    assert "${LOXMATTER_IMAGE_TAG:-stable}" in image


def test_the_build_path_remains_alongside() -> None:
    # `image:` and `build:` on the same service: `compose pull` pulls,
    # `compose build` builds, and `up` builds only if there is no local image.
    # On a host without GHCR access, that is the fallback path. A
    # profile would not work here - profiles apply to services, not
    # to individual keys of a service.
    assert _stack()["services"]["loxmatter"]["build"]["context"] == "../.."


def test_the_running_version_is_at_exactly_one_place() -> None:
    # Stage 2 relies on this: the fallback writes ONE line back to .env.
    # If the tag appears at a second place, only the
    # one is rolled back and the other is not.
    source = COMPOSE.read_text(encoding="utf-8")
    assert source.count("LOXMATTER_IMAGE_TAG") == 1


def test_the_updater_has_no_network_open_to_the_outside() -> None:
    # The load-bearing safeguard for this container: it holds the Docker
    # socket and is thereby root-equivalent on the host. Unlike the three
    # other services, it therefore does NOT sit on the host network and
    # publishes no port.
    updater = _stack()["services"]["loxmatter-updater"]
    assert "ports" not in updater
    assert updater.get("network_mode") != "host"


def test_only_the_updater_has_the_docker_socket() -> None:
    for name, service in _stack()["services"].items():
        socket = any("docker.sock" in str(v) for v in service.get("volumes", []))
        assert socket == (name == "loxmatter-updater"), name


def test_the_updater_sees_the_same_database_as_the_bridge() -> None:
    # Communication runs through files in exactly this volume.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any(str(v).startswith("loxmatter-store:") for v in updater["volumes"])


def test_the_updater_reaches_the_host_health_route() -> None:
    # It sits on Compose's default network; 127.0.0.1 there would be itself.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any("host-gateway" in str(h) for h in updater["extra_hosts"])


def test_the_updater_does_not_use_latest() -> None:
    # Checks that the sidecar image does not use the moving :latest tag.
    # The tag configured is :stable, which is also a moving alias (see
    # running_version()'s comment in update-once.sh on why "stable is not
    # a version"), but unlike :latest, :stable is rare enough that
    # accidentally pulling a different image on startup is acceptable:
    # the sidecar changes rarely and is not versioned through the bridge's
    # forward-only logic. The bridge itself (loxmatter) is pinned by
    # semantic version through the update mechanism; this sidekick does not
    # need that same strictness.
    image = _stack()["services"]["loxmatter-updater"]["image"]
    assert "@sha256:" in image or ":latest" not in image


def test_only_the_bridge_and_the_updater_see_the_host_dev_tree_read_only() -> None:
    """Design 2026-09-11 "Radios in the Web UI", section 5: names under
    /dev/serial/by-id only, never device access. Fault to prove it: mount
    `/dev:/host/dev` without `:ro` on one of them."""
    for name, service in _stack()["services"].items():
        mounts = [str(v) for v in service.get("volumes", []) if str(v).startswith("/dev:")]
        if name in ("loxmatter", "loxmatter-updater"):
            assert mounts == ["/dev:/host/dev:ro"], name
        else:
            assert mounts == [], name


def test_the_bridge_may_open_serial_devices_without_naming_one():
    """Design 2026-09-12 section 8.1. A `devices:` entry cannot be used
    here: it fails the WHOLE stack at `docker compose up` when the node is
    absent (which is why otbr sits behind a profile), and it is copied into
    the container at create time, so hotplug is invisible. The cgroup rule
    grants the access instead, and the existing read-only /dev bind supplies
    the names.

    188 = USB serial converters (ttyUSB*), 166 = ACM USB modems (ttyACM*),
    both verified against the kernel's admin-guide/devices.txt (research
    F.8). A GPIO-UART hat would be 204:64 and is deliberately not granted.

    Fault to prove it: delete one of the two rules."""
    rules = _stack()["services"]["loxmatter"]["device_cgroup_rules"]
    assert "c 188:* rmw" in rules
    assert "c 166:* rmw" in rules


def test_only_the_bridge_may_open_serial_devices():
    """The rule is coarse - it reaches EVERY USB-serial adapter on the host,
    the Thread stick included (research F.9). That is acceptable for the one
    service that needs to open a Zigbee coordinator and for no other, and it
    is much narrower than `privileged: true`. Exclusion of the Thread stick
    itself is enforced in loxmatter, by resolved major:minor (section 3.2).

    Fault to prove it: add the same rules to `matter-server`."""
    for name, service in _stack()["services"].items():
        has_rules = "device_cgroup_rules" in service
        assert has_rules == (name == "loxmatter"), name


def test_otbr_asks_the_kernel_to_keep_its_stick_to_itself():
    """OpenThread takes flock + TIOCEXCL only when the radio URL carries
    `uart-exclusive` (research A.3); the compose file passed no lock at all.

    This is a SECOND layer, not the guarantee: TIOCEXCL is bypassed by a
    holder of CAP_SYS_ADMIN, which privileged otbr has. The real guarantee
    is that loxmatter never offers or accepts the Thread stick (section 3.2,
    Task 11).

    `RADIO_URL` is an ENVIRONMENT variable of the otbr service, not part of
    its `command:` - the image's "test" entrypoint reads it from the
    environment, and `command:` carries only `--backbone-interface
    ${BACKBONE_IF}`. Asserting against `command` would pass for the wrong
    reason today (the parameter is absent from it either way) and would keep
    passing after somebody deleted the lock.

    Fault to prove it: drop the parameter from RADIO_URL."""
    otbr = _stack()["services"]["otbr"]
    assert "uart-exclusive" in str(otbr["environment"]["RADIO_URL"])


# --- Where zigpy's database lands ----------------------------------------------
#
# Every compose file under deploy/, not only the one `_stack()` reads: a second
# deployment shape added later must not be able to put the database back onto
# a read-only mount without this noticing.
COMPOSE_FILES = sorted(
    path
    for path in (Path(__file__).resolve().parent.parent / "deploy").rglob("*.y*ml")
    if "compose" in path.name
)


def _command_option(command: list[object], option: str) -> str | None:
    items = [str(item) for item in command]
    if option in items and items.index(option) + 1 < len(items):
        return items[items.index(option) + 1]
    return None


def _environment(service: dict) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        return dict(str(item).split("=", 1) for item in raw if "=" in str(item))
    return {str(key): str(value) for key, value in raw.items()}


def _mounts(service: dict) -> list[tuple[str, bool]]:
    """`(target, read_only)` for every volume, in both compose syntaxes."""
    mounts = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            mounts.append((str(volume["target"]), volume.get("read_only") is True))
            continue
        parts = str(volume).split(":")
        options = parts[2].split(",") if len(parts) > 2 else []
        mounts.append((parts[1] if len(parts) > 1 else parts[0], "ro" in options))
    return mounts


def test_the_zigbee_database_lands_on_a_writable_mount_in_every_compose_file() -> None:
    """zigpy creates `zigbee.sqlite` on the first Zigbee Apply, and cannot
    if its directory is read-only. The first build derived the directory
    from `--matter-data-dir`, which the testhost compose file mounts
    `:ro` - so the very first Apply on the Pi would have failed.

    The path is computed with the bridge's own rule
    (`zigbee_database_beside`), from the store path each bridge service is
    configured with, and then looked up among that service's mounts. A
    directory on no mount at all fails too: it would be inside the
    container's own layer and gone after the next update.

    Fault to prove it, either half: mount `loxmatter-store:/data:ro`, or make
    `zigbee_database_beside` answer `/matter-data/zigbee.sqlite` again."""
    from loxmatter.zigbee.runtime import zigbee_database_beside

    assert COMPOSE_FILES, "no compose file found under deploy/"
    checked = 0
    for compose in COMPOSE_FILES:
        stack = yaml.safe_load(compose.read_text(encoding="utf-8"))
        for name, service in (stack.get("services") or {}).items():
            command = service.get("command") or []
            if not command or str(command[0]) != "run":
                continue  # not a `loxmatter run` bridge
            store = _command_option(command, "--store-path") or _environment(service).get(
                "LOXMATTER_STORE"
            )
            assert store, f"{compose.name}:{name} leaves the store on the home default"
            directory = PurePosixPath(zigbee_database_beside(Path(store)).as_posix()).parent
            covering = [
                (target, read_only)
                for target, read_only in _mounts(service)
                if directory == PurePosixPath(target) or PurePosixPath(target) in directory.parents
            ]
            assert covering, f"{compose.name}:{name}: {directory} is on no mount"
            target, read_only = max(covering, key=lambda mount: len(mount[0]))
            assert not read_only, f"{compose.name}:{name}: {directory} is under {target}:ro"
            checked += 1
    assert checked, "no bridge service found in any compose file"
