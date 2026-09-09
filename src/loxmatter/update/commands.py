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

"""`Commands` (see `runner.py`), for real: docker, git, curl, tar and one
line of `.env`.

Nothing here is interesting for what it invokes - it is interesting for
*what* it invokes and *how* it edits `.env`, and both are testable without
a daemon by injecting the subprocess runner (`run=`). A test that shells
out to a real `docker` would not run in CI, so the committed tests
(`tests/update/test_commands.py`) never do; only the throwaway exercise
recorded in the task report calls a real daemon.

`repo_dir`, `stack_dir`, `env_path`, `health_url` and `base` are
constructor arguments because they differ between a test's `tmp_path` and
the real mount points inside the sidecar container - `base` is where
`log()` writes `log.txt`, the same directory `run_update` passes to
`write_state` (see `__main__.py`), so the two cannot silently drift apart
when `LOXMATTER_UPDATE_BASE` moves one of them. `/data/backups` is not
injected: this container always has `/data` mounted at that fixed path
(the same volume the bridge itself holds its database in - see
`deploy/testhost/docker-compose.yml`), independently of where the request/
state/log directory lives, and no committed test exercises `backup()`'s
path for that reason.

`repo_dir` and `stack_dir` are two different directories, not one path
under two names. `git` (`fetch`, `checkout`, `merge-base --is-ancestor`,
`rev-parse HEAD`) needs the repository work tree - `repo_dir`. `docker
compose` (`up`, `ps`) needs the directory holding `docker-compose.yml` -
`stack_dir`, `deploy/testhost` two levels below the repository root.
`write_image_tag`/`read_image_tag` rewrite `.env`, which lives in
`stack_dir` too, hence `env_path` defaulting there in `__main__.py`. A
single `compose_dir` for both used to happen to work, because `git`
walks up from a subdirectory to find `.git` - but it worked by accident,
and a caller that later mounted only `deploy/testhost` (a reasonable-
looking simplification) would have broken every git command with no
obvious cause. `scripts/update.sh`'s own `REPO`/`STACK` split is the
model this mirrors."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from loxmatter.timestamps import now_iso
from loxmatter.update.protocol import log_path
from loxmatter.update.runner import POLL_SECONDS

#: `Commands.pull`, `Commands.write_image_tag` and this module's own
#: `read_image_tag` all key off this one variable name in `.env` - see
#: `deploy/testhost/.env.example`, which documents why it lives there
#: rather than in the Compose file (a `git checkout` during an update must
#: not touch it).
_TAG_VAR = "LOXMATTER_IMAGE_TAG"
_TAG_PREFIX = f"{_TAG_VAR}="

#: Compose's own default (`${LOXMATTER_IMAGE_TAG:-stable}`) - the reader
#: here has to agree with it. Disagreeing would mean a rollback writes
#: back a tag that was never actually running, on an installation whose
#: `.env` never set the variable at all.
_DEFAULT_TAG = "stable"

#: Fixed inside the container - see the module docstring.
_DATA_DIR = Path("/data")
_BACKUP_DIR = _DATA_DIR / "backups"
_STORE_FILE = "loxmatter.sqlite"

#: `scripts/update.sh`'s own rule (`ls -1t ... | tail -n +11 | xargs rm`),
#: matched here because `backup()` writes into the volume it protects - on
#: a Pi that volume is the whole disk, and an update that never deletes an
#: old backup would eventually fail by filling it.
_BACKUPS_TO_KEEP = 10

#: `log.txt` is capped rather than left to grow forever - the SD card in a
#: Raspberry Pi is small, and a full one is exactly the kind of ordinary
#: failure an update has to survive (see `runner._log`). 256 KiB is enough
#: for several updates' worth of lines without needing rotation.
_LOG_CAP_BYTES = 256 * 1024

#: Every subprocess call below carries one of these, chosen per command
#: rather than as one number for all of them (see `_run_subprocess`) - an
#: unattended loop that lets ANY of them hang forever gets stuck in
#: whatever phase it was in, with no rollback and no further log line,
#: until a person notices and restarts the container.
#:
#: `git fetch` and `docker pull` are the only two commands here that move
#: data over the operator's own internet connection rather than talking
#: to a local daemon or repository - on the slow rural link this ships
#: to, both legitimately take a while. They do NOT share one budget,
#: though (a single `_NETWORK_TIMEOUT_SECONDS` used to cover both): the
#: two move very different amounts of data, and a number generous enough
#: for the image killed a fetch that was never going to be the slow part,
#: while a number sized for the fetch killed image pulls on exactly the
#: connection this feature ships to. See the two constants below.

#: The checkout this container mounts is this project's own source
#: repository - modest in size and object count even after many
#: releases, and a `fetch` only has to bring down refs and objects since
#: the last one, not the whole history again. 60s is generous for that
#: even on the same slow link `_IMAGE_PULL_TIMEOUT_SECONDS` names below;
#: a connection that goes fully dead (the half-open-socket case this
#: whole fix exists for) still ends inside a minute instead of sitting in
#: `phase="backup"` forever, rather than borrowing an image-sized budget
#: that would let a genuinely dead fetch masquerade as a slow one for
#: five times as long.
_GIT_FETCH_TIMEOUT_SECONDS = 60.0

#: The bridge image is `python:3.12-slim` plus a full `uv sync` -
#: compressed, realistically 100-150 MB. At the slow end of the range
#: this ships to (1 Mbit/s rural upload-constrained connections, per the
#: install notes), 150 MB is 1200 Mbit, which is exactly 1200s at 1
#: Mbit/s - the pessimistic end of "four to twenty minutes" this module
#: has always quoted for a real pull, not a new estimate. The old shared
#: `_NETWORK_TIMEOUT_SECONDS` (300s) killed a pull that would have
#: finished on precisely the connection that number's own comment
#: invoked - a failed update, and a rollback, on a link that was merely
#: slow, not dead. 1200s covers that worst case with no slack cut, while
#: still bounding a connection that goes fully dead to twenty minutes
#: instead of sitting in `phase="backup"` forever - see `api/update.py`'s
#: `_updater_present` for how the interface stays honest about "an update
#: is still running" for a window this long.
_IMAGE_PULL_TIMEOUT_SECONDS = 1200.0

#: Everything else that shells out here - `git checkout`, `git
#: rev-parse`, `git merge-base --is-ancestor`, `docker compose ps` and
#: `docker inspect` - talks only to the local checkout or the local
#: Docker socket, never the network, so none of them has a legitimate
#: reason to take more than a second or two in practice. 30s is headroom
#: for a Raspberry Pi under I/O pressure (a concurrent backup, an SD card
#: having a slow moment), not a number chosen to accommodate real work -
#: a command that is still running after 30s of purely local work is
#: hung, not slow.
_LOCAL_TIMEOUT_SECONDS = 30.0

#: `docker compose up -d --no-deps loxmatter` (`recreate`). The image it
#: starts was already pulled by `pull` earlier in the same run (`compose
#: up` resolves it from `.env`, already rewritten - see `recreate`'s own
#: docstring), so this is container stop/start and network
#: reconfiguration, not an image fetch - no reason to share
#: `_IMAGE_PULL_TIMEOUT_SECONDS`'s twenty minutes. Longer than
#: `_LOCAL_TIMEOUT_SECONDS` because Compose can be slow to tear down a
#: container that is not exiting cleanly, but still well short of
#: `runner.DEADLINE_SECONDS` (120s), the health wait that follows it.
_RECREATE_TIMEOUT_SECONDS = 60.0

#: `tar czf` of the sqlite store in `backup()`. Bounded by disk I/O and
#: how large the store has grown, not by anything off the Pi - generous
#: compared to `_LOCAL_TIMEOUT_SECONDS` for that reason, but nowhere near
#: `_IMAGE_PULL_TIMEOUT_SECONDS`: a backup reads and writes the same
#: local volume, it does not wait on a remote host.
_BACKUP_TIMEOUT_SECONDS = 120.0

#: `curl`'s own bound on one health-check attempt (see `healthy()`) -
#: half of `runner.POLL_SECONDS`. `_wait_for_health` calls
#: `sleep(POLL_SECONDS)` after every poll regardless of how long
#: `healthy()` took, so a poll that used its whole budget still leaves as
#: much time again before the next one starts - "fits `POLL_SECONDS`"
#: without letting one hung connection attempt eat the interval it is
#: supposed to occupy.
_HEALTH_CURL_MAX_TIME_SECONDS = POLL_SECONDS / 2

#: Backstop for the health check's own `_run_subprocess` call, in case
#: `curl` somehow does not honour `--max-time` above (a killed process, a
#: platform quirk) - not the primary bound, `_HEALTH_CURL_MAX_TIME_SECONDS`
#: is. Capped at `POLL_SECONDS` itself so that even in that pathological
#: case, one poll's subprocess call cannot outlast the interval
#: `_wait_for_health`'s own `waited += POLL_SECONDS` arithmetic assumes
#: it stays within.
_HEALTH_TIMEOUT_SECONDS = POLL_SECONDS

#: `_cap_log` trims down to this many bytes, not all the way up to
#: `_LOG_CAP_BYTES` - trimming to the cap itself would leave the file
#: right back at the trigger threshold, so the very next line appended
#: crosses it again and the whole file gets rewritten once more. A
#: caller that logs the same persisting failure on many passes (the
#: Docker socket staying unreachable, say) would then pay that rewrite
#: on every single call once the file is near the cap. Leaving headroom
#: means many lines append for free before a rewrite is needed again.
_LOG_TRIM_BYTES = _LOG_CAP_BYTES * 3 // 4

#: What `run` is called with, and what it returns - stdout, on success.
#: `check=True` in the real implementation below means a non-zero exit
#: raises rather than returning an error code; every caller in this module
#: either lets that exception propagate (the happy-path steps, exactly
#: like `run_update` expects - see its module docstring) or catches it
#: itself where "did this fail" IS the answer (`healthy`, `is_ancestor`).
#: `timeout` is a required keyword for the same reason `check=True` is not
#: optional: `subprocess.TimeoutExpired` is a `SubprocessError`, itself an
#: `Exception`, so it propagates and is caught exactly like any other
#: failure above - no caller needs to know the difference between "the
#: command failed" and "the command never finished".
Runner = Callable[..., str]


def _clean_tag_value(value: str) -> str:
    """`.env` is hand-edited more than any other file this module
    touches. A value wrapped in quotes (`LOXMATTER_IMAGE_TAG="0.3.0"` -
    valid shell, and a style some editors add on their own) must resolve
    to the same tag `pull`/`checkout` accept unquoted - otherwise a
    rollback writes the surrounding characters straight back into
    `.env`, and the tag that is meant to describe what is running never
    again resolves to a published image.

    CRLF line endings do not reach here: the only caller splits `.env`
    with `str.splitlines()`, which already treats `\\r\\n` as one line
    boundary, so `value` never carries a trailing `\\r` for this
    function to strip."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _run_subprocess(argv: list[str], *, cwd: Path | None = None, timeout: float) -> str:
    # `argv` is built from fixed strings and caller-supplied refs/tags/
    # images that `rules.py` has already validated (or, for `checkout`, a
    # value read back out of `.env`/`git` itself) - never raw request
    # data.
    #
    # `timeout` has no default: every call site below picks one of the
    # constants above rather than falling back to a value that fits no
    # command in particular. Without it, a stalled `docker pull` (a
    # half-open connection, the ordinary way a network hangs rather than
    # fails outright) leaves `subprocess.run` blocked forever - and with
    # it, this sidecar wedged in whatever phase it was in, publishing
    # nothing further, with no rollback and no human watching to notice.
    result = subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


class DockerCommands:
    """`Commands`, plus `is_ancestor` and `read_running_identity` - two
    members the loop (`__main__.py`) needs a real implementation of but
    `run_update` itself does not ask `Commands` for, because both are
    resolved before `run_update` is even called rather than while it
    runs (see `runner.py`'s `Commands` Protocol, and `LoopCommands` in
    `__main__.py`)."""

    def __init__(
        self,
        repo_dir: Path,
        stack_dir: Path,
        env_path: Path,
        health_url: str,
        base: Path,
        *,
        run: Runner = _run_subprocess,
    ) -> None:
        self._repo_dir = repo_dir
        self._stack_dir = stack_dir
        self._env_path = env_path
        self._health_url = health_url
        self._base = base
        self._run = run

    def backup(self) -> str:
        # Only from `now_iso()` (see CLAUDE.md) - the colons and the `+`
        # of its offset are stripped because they are awkward in a
        # filename, not because a different clock is used.
        stamp = now_iso().replace("+00:00", "").replace(":", "-")
        path = _BACKUP_DIR / f"store-{stamp}.tgz"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            ["tar", "czf", str(path), "-C", str(_DATA_DIR), _STORE_FILE],
            timeout=_BACKUP_TIMEOUT_SECONDS,
        )
        self._prune_backups()
        return str(path)

    def _prune_backups(self) -> None:
        """Delete everything but the `_BACKUPS_TO_KEEP` most recent
        backups - see the constant's own comment for why this exists at
        all. Sorted by mtime, not by the timestamp in the filename: both
        agree in the normal case, but mtime is what `ls -1t` in
        `scripts/update.sh` actually goes by, and matching that proven
        behaviour matters more here than matching the stamp."""
        backups = sorted(
            _BACKUP_DIR.glob("store-*.tgz"), key=lambda entry: entry.stat().st_mtime, reverse=True
        )
        for stale in backups[_BACKUPS_TO_KEEP:]:
            stale.unlink(missing_ok=True)

    def fetch(self) -> None:
        self._run(
            ["git", "fetch", "--tags", "--prune", "origin"],
            cwd=self._repo_dir,
            timeout=_GIT_FETCH_TIMEOUT_SECONDS,
        )

    def checkout(self, ref: str) -> None:
        self._run(
            ["git", "checkout", "--detach", ref],
            cwd=self._repo_dir,
            timeout=_LOCAL_TIMEOUT_SECONDS,
        )

    def pull(self, image: str) -> None:
        # `docker pull <image>`, not `docker compose pull loxmatter`: the
        # latter would resolve the image through `.env`'s
        # `LOXMATTER_IMAGE_TAG`, which at this point in `run_update` still
        # holds the OLD value - `write_image_tag` runs only after `pull`
        # (see `runner.run_update`). `image` is `image_reference(target)`,
        # the full, already-resolved registry reference; using it directly
        # is the only way `pull` fetches the version this update is
        # actually going to.
        self._run(["docker", "pull", image], timeout=_IMAGE_PULL_TIMEOUT_SECONDS)

    def recreate(self) -> None:
        # `--no-deps` for two reasons (addendum 16.2): the updater must
        # never recreate itself mid-request, and matter-server must not be
        # recreated at all - since 8 September that service points at
        # matterjs-server, whose first start migrates the Fabric directory
        # one way.
        #
        # No `--force-recreate`, unlike `scripts/update.sh`. Defensible
        # rather than an oversight: `write_image_tag` always changes the
        # image reference in `.env` before every call to `recreate` (see
        # `runner.run_update` and its `roll_back`), so Compose already
        # sees a config diff and recreates without needing to be forced.
        self._run(
            ["docker", "compose", "up", "-d", "--no-deps", "loxmatter"],
            cwd=self._stack_dir,
            timeout=_RECREATE_TIMEOUT_SECONDS,
        )

    def read_running_identity(self) -> tuple[str, str]:
        """The build identity baked into the *running* `loxmatter`
        container's environment - `LOXMATTER_VERSION`/`LOXMATTER_COMMIT`
        from `Dockerfile`'s `ENV` - read live through the Docker socket
        this sidecar holds.

        Design section 4: "It comes from the image, not from the checkout
        on the host - which may by now be elsewhere, moved or advanced,
        without that ever being shipped." `LOXMATTER_IMAGE_TAG=stable` is
        a moving alias: CI repoints it on every release, so the running
        container can advance while a checkout on disk stays exactly
        where it was. Reading `pyproject.toml`'s bare version or `git
        rev-parse HEAD` from that checkout would then report a number
        nobody is running anymore, and `rules.check_request` would wave a
        downgrade through as "forward" because it never sees the real
        one.

        Raises if the container cannot be found or `docker inspect`
        fails - deliberately: the loop (`__main__.run_once`) already has
        a path for "cannot determine what is currently running", and a
        version identity that cannot be established must land there,
        never in a permissive guess.
        """
        container_id = self._run(
            ["docker", "compose", "ps", "-q", "loxmatter"],
            cwd=self._stack_dir,
            timeout=_LOCAL_TIMEOUT_SECONDS,
        ).strip()
        if not container_id:
            raise RuntimeError("the loxmatter container is not running")
        raw = self._run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", container_id],
            timeout=_LOCAL_TIMEOUT_SECONDS,
        )
        entries = json.loads(raw)
        env = dict(entry.split("=", 1) for entry in entries if "=" in entry)
        # A hand-built image (no build arguments passed) still carries
        # these keys with their `Dockerfile` defaults ("dev", "") - that
        # is the normal, honest case for a development build, not a
        # failure, and `rules.check_request` already treats an
        # unparsable running version/commit as "cannot prove forward" and
        # refuses safely. Falling back the same way for a foreign or very
        # old image that lacks the keys entirely keeps that one behaviour
        # in one place instead of two.
        version = env.get("LOXMATTER_VERSION") or "dev"
        commit = env.get("LOXMATTER_COMMIT") or ""
        return version, commit

    def healthy(self) -> bool:
        # A connection failure has to read as `False` here, not as an
        # exception a caller three modules away has to know to catch
        # (`run_update` does, as a safety net - see its docstring - but
        # the meaning belongs at the point that actually calls `/health`).
        try:
            self._run(
                [
                    "curl",
                    "-fsS",
                    "--max-time",
                    str(_HEALTH_CURL_MAX_TIME_SECONDS),
                    self._health_url,
                ],
                timeout=_HEALTH_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return False
        return True

    def is_ancestor(self, older: str, newer: str) -> bool:
        try:
            self._run(
                ["git", "merge-base", "--is-ancestor", older, newer],
                cwd=self._repo_dir,
                timeout=_LOCAL_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - a non-zero exit means "no"
            return False
        return True

    def write_image_tag(self, tag: str) -> None:
        # Line by line, in place - never a rewrite from scratch. This file
        # also holds the Miniserver address and the API token, and a
        # second definition of the same variable would technically work
        # (Compose takes the last) but would mislead whoever edits the
        # file by hand afterwards into changing the wrong line.
        #
        # `mkdir` first, like `backup()` and `log()` - without it, a
        # missing parent directory turns the `FileNotFoundError` this
        # method already handles on READ into an unhandled one on WRITE,
        # for a case this method exists specifically to cover.
        self._env_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = self._env_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        lines = text.splitlines(keepends=True)
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith(_TAG_PREFIX):
                lines[index] = f"{_TAG_PREFIX}{tag}\n"
                replaced = True
                break
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = f"{lines[-1]}\n"
            lines.append(f"{_TAG_PREFIX}{tag}\n")
        self._env_path.write_text("".join(lines), encoding="utf-8")

    def read_image_tag(self) -> str:
        try:
            text = self._env_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _DEFAULT_TAG
        for line in text.splitlines():
            if line.startswith(_TAG_PREFIX):
                return _clean_tag_value(line[len(_TAG_PREFIX) :])
        return _DEFAULT_TAG

    def read_git_ref(self) -> str:
        return self._run(
            ["git", "rev-parse", "HEAD"], cwd=self._repo_dir, timeout=_LOCAL_TIMEOUT_SECONDS
        ).strip()

    def log(self, line: str) -> None:
        path = log_path(self._base)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {line}\n")
        self._cap_log(path)

    def _cap_log(self, path: Path) -> None:
        """Keep only the tail once the file grows past the cap - reading
        the whole thing into memory every call would be fine at 256 KiB,
        but only reading the last N bytes off disk is just as simple and
        does not grow with how big the file was allowed to get before
        this ran."""
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size <= _LOG_CAP_BYTES:
            return
        with path.open("rb") as handle:
            handle.seek(-_LOG_TRIM_BYTES, 2)
            tail = handle.read()
        # The seek almost certainly landed inside a line - drop the
        # partial fragment before it so the cap boundary is a line
        # boundary, not a torn one.
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1 :]
        path.write_bytes(tail)
