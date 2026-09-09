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

`compose_dir`, `env_path`, `health_url` and `base` are constructor
arguments because they differ between a test's `tmp_path` and the real
mount point inside the sidecar container - `base` is where `log()` writes
`log.txt`, the same directory `run_update` passes to `write_state` (see
`__main__.py`), so the two cannot silently drift apart when
`LOXMATTER_UPDATE_BASE` moves one of them. `/data/backups` is not
injected: this container always has `/data` mounted at that fixed path
(the same volume the bridge itself holds its database in - see
`deploy/testhost/docker-compose.yml`), independently of where the request/
state/log directory lives, and no committed test exercises `backup()`'s
path for that reason.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from loxmatter.timestamps import now_iso
from loxmatter.update.protocol import log_path

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

#: What `run` is called with, and what it returns - stdout, on success.
#: `check=True` in the real implementation below means a non-zero exit
#: raises rather than returning an error code; every caller in this module
#: either lets that exception propagate (the happy-path steps, exactly
#: like `run_update` expects - see its module docstring) or catches it
#: itself where "did this fail" IS the answer (`healthy`, `is_ancestor`).
Runner = Callable[..., str]


def _clean_tag_value(value: str) -> str:
    """`.env` is hand-edited more than any other file this module
    touches. A value saved with CRLF line endings, or wrapped in quotes
    (`LOXMATTER_IMAGE_TAG="0.3.0"` - valid shell, and a style some
    editors add on their own), must resolve to the same tag `pull`/
    `checkout` accept unquoted - otherwise a rollback writes the
    surrounding characters straight back into `.env`, and the tag that
    is meant to describe what is running never again resolves to a
    published image."""
    value = value.rstrip("\r")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _run_subprocess(argv: list[str], *, cwd: Path | None = None) -> str:
    # `argv` is built from fixed strings and caller-supplied refs/tags/
    # images that `rules.py` has already validated (or, for `checkout`, a
    # value read back out of `.env`/`git` itself) - never raw request
    # data.
    result = subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class DockerCommands:
    """`Commands`, plus `is_ancestor` - the one member `run_update` takes
    as its own injected argument rather than through the Protocol (see
    `runner.py`), because the sidecar's loop needs a real implementation
    of it to hand over."""

    def __init__(
        self,
        compose_dir: Path,
        env_path: Path,
        health_url: str,
        base: Path,
        *,
        run: Runner = _run_subprocess,
    ) -> None:
        self._compose_dir = compose_dir
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
        self._run(["tar", "czf", str(path), "-C", str(_DATA_DIR), _STORE_FILE])
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
        self._run(["git", "fetch", "--tags", "--prune", "origin"], cwd=self._compose_dir)

    def checkout(self, ref: str) -> None:
        self._run(["git", "checkout", "--detach", ref], cwd=self._compose_dir)

    def pull(self, image: str) -> None:
        # `docker pull <image>`, not `docker compose pull loxmatter`: the
        # latter would resolve the image through `.env`'s
        # `LOXMATTER_IMAGE_TAG`, which at this point in `run_update` still
        # holds the OLD value - `write_image_tag` runs only after `pull`
        # (see `runner.run_update`). `image` is `image_reference(target)`,
        # the full, already-resolved registry reference; using it directly
        # is the only way `pull` fetches the version this update is
        # actually going to.
        self._run(["docker", "pull", image])

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
            cwd=self._compose_dir,
        )

    def healthy(self) -> bool:
        # A connection failure has to read as `False` here, not as an
        # exception a caller three modules away has to know to catch
        # (`run_update` does, as a safety net - see its docstring - but
        # the meaning belongs at the point that actually calls `/health`).
        try:
            self._run(["curl", "-fsS", self._health_url])
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return False
        return True

    def is_ancestor(self, older: str, newer: str) -> bool:
        try:
            self._run(
                ["git", "merge-base", "--is-ancestor", older, newer],
                cwd=self._compose_dir,
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
        return self._run(["git", "rev-parse", "HEAD"], cwd=self._compose_dir).strip()

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
            handle.seek(-_LOG_CAP_BYTES, 2)
            tail = handle.read()
        # The seek almost certainly landed inside a line - drop the
        # partial fragment before it so the cap boundary is a line
        # boundary, not a torn one.
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1 :]
        path.write_bytes(tail)
