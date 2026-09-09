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

"""Builds, from the byte-span tree (`projectsync.scan`), an index
searchable by `loxmatter` keys: which virtual inputs/outputs already
exist, and in which device container they sit (design section 3.3/5).

**Correction after a real-world test (2026-09-04):** the original
assumption - that `VirtualInCaption`/`VirtualOutCaption` sit directly
under `<ControlList>` - was wrong. Checked against a real project file
grown over years: `<ControlList>` has exactly ONE child, `<C
Type="Document">`, and EVERY Miniserver configured within it gets its own
`<C Type="LoxLIVE">` block (with its own `IntAddr`, `Serial`, etc.) -
`VirtualInCaption`/`VirtualOutCaption` are children of THIS `LoxLIVE`
block, not of `ControlList` or `Document`. A file can have several
`LoxLIVE` blocks (several Miniservers in one project) - `build_index` must
therefore first select the right one before it even looks for virtual
inputs/outputs. The original, flat structure parsed through without an
error message, but simply found NOTHING - every existing device wrongly
showed up as `new_device` (see `docs/superpowers/specs/
2026-09-03-project-file-sync-design.md`, the section on Miniserver
assignment, for the full derivation)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.projectsync.keys import key_from_check, key_from_output_cmd
from loxmatter.projectsync.scan import Element, ProjectFormatError, parse_root, scan_children

__all__ = [
    "AmbiguousMiniserverError",
    "MiniserverCandidate",
    "ProjectFormatError",
    "ProjectIndex",
    "build_index",
]

_U_ATTR = re.compile(r'\bU="([^"]*)"')
_INAME_ATTR = re.compile(r'\bIName="([^"]*)"')


@dataclass(frozen=True)
class MiniserverCandidate:
    """A Miniserver found in the project file (`LoxLIVE` block), as carried
    by `AmbiguousMiniserverError.candidates` - enough to populate a
    selection field in the WebUI (user request: select instead of typing
    the IP by hand), without passing the whole `Element` tree through."""

    title: str
    int_addr: str


class AmbiguousMiniserverError(ProjectFormatError):
    """The project file is valid, but which `LoxLIVE` block (= which
    configured Miniserver) is meant cannot be determined unambiguously -
    either there is none at all, or there are several and no (or a
    non-matching) `miniserver_ip` was supplied. Without an unambiguous
    assignment, the comparison could otherwise land in the wrong
    Miniserver section of a multi-Miniserver file. A subclass of
    `ProjectFormatError` so that the same error handling applies at the
    upload endpoint (a clear 400 response instead of 500) - the file
    itself is not faulty here, only the request is incomplete.

    `candidates` carries the Miniservers actually found, if there are any
    (empty only in the "none configured at all" case, where there is
    nothing to choose from) - `api.project_sync` uses this to offer a
    selection field instead of a plain error message (user request after
    the review)."""

    def __init__(self, message: str, candidates: Sequence[MiniserverCandidate] = ()) -> None:
        super().__init__(message)
        self.candidates: list[MiniserverCandidate] = list(candidates)


@dataclass
class ProjectIndex:
    text: str
    root_attrs: dict[str, str]
    root_open_end: int
    root_close_start: int
    # The selected `LoxLIVE` block (= Miniserver) this run compares
    # against - newly created captions (see `patch._new_device_edit`)
    # attach at its `inner_end`, no longer at `root_close_start`.
    target_loxlive: Element
    virtual_in_caption: Element | None
    virtual_out_caption: Element | None
    input_cmds: dict[str, Element]
    output_cmds: dict[str, Element]
    input_containers: dict[str, Element]
    output_containers: dict[str, Element]
    all_u_values: set[str]
    all_inames: set[str]


def _find_all_loxlive(elements: list[Element]) -> list[Element]:
    """Finds all `LoxLIVE` blocks anywhere in the tree, regardless of
    nesting depth - in the reference file they sit under `Document`, not
    directly under `<ControlList>`. Recursive rather than assuming a fixed
    depth: that depth is itself not a documented, reliable format
    property."""
    found: list[Element] = []
    for element in elements:
        if element.type == "LoxLIVE":
            found.append(element)
        found.extend(_find_all_loxlive(element.children))
    return found


def _describe(loxlives: list[Element]) -> str:
    return ", ".join(
        f"„{ll.attrs.get('Title', '?')}“ "
        f"({ll.attrs.get('IntAddr', i18n.t('projectsync.no_ip_known'))})"
        for ll in loxlives
    )


def _candidates(loxlives: list[Element]) -> list[MiniserverCandidate]:
    """Builds `AmbiguousMiniserverError.candidates` from the found
    `LoxLIVE` blocks - only the ones that also carry an `IntAddr`: without
    one, there is nothing that `miniserver_ip` could accept on the next
    attempt, so such a block would just be a dead entry in the selection."""
    return [
        MiniserverCandidate(title=ll.attrs.get("Title", "?"), int_addr=ll.attrs["IntAddr"])
        for ll in loxlives
        if ll.attrs.get("IntAddr")
    ]


def _resolve_target_loxlive(loxlives: list[Element], miniserver_ip: str | None) -> Element:
    """Selects the ONE `LoxLIVE` block this run compares against.

    Exactly one block in the file: that is the one - regardless of
    whether `miniserver_ip` is set, PROVIDED it is not set. If it is set,
    it must still match (see below): an explicitly supplied, non-matching
    IP points more towards the wrong file than towards a reason to ignore
    it.

    Several blocks: `miniserver_ip` is mandatory and must correspond
    exactly to one `LoxLIVE.IntAddr` (the same internal address that
    `loxmatter run --miniserver <IP>` also receives) - otherwise the
    comparison could land in the wrong Miniserver half of the file, and
    that is exactly what this feature must never do."""
    if not loxlives:
        raise AmbiguousMiniserverError(i18n.t("projectsync.no_miniserver_configured"))
    if miniserver_ip:
        matches = [ll for ll in loxlives if ll.attrs.get("IntAddr") == miniserver_ip]
        if not matches:
            raise AmbiguousMiniserverError(
                i18n.t(
                    "projectsync.miniserver_ip_not_found",
                    ip=repr(miniserver_ip),
                    candidates=_describe(loxlives),
                ),
                _candidates(loxlives),
            )
        return matches[0]
    if len(loxlives) > 1:
        raise AmbiguousMiniserverError(
            i18n.t("projectsync.multiple_miniservers", candidates=_describe(loxlives)),
            _candidates(loxlives),
        )
    return loxlives[0]


def build_index(text: str, miniserver_ip: str | None = None) -> ProjectIndex:
    root_attrs, _root_open_start, root_open_end, root_close_start = parse_root(text)
    top_level = scan_children(text, root_open_end, root_close_start)

    loxlives = _find_all_loxlive(top_level)
    target_loxlive = _resolve_target_loxlive(loxlives, miniserver_ip)
    if target_loxlive.self_closing or target_loxlive.inner_end is None:
        raise AmbiguousMiniserverError(
            i18n.t(
                "projectsync.miniserver_without_configuration",
                title=target_loxlive.attrs.get("Title", "?"),
            )
        )

    virtual_in_caption = next(
        (e for e in target_loxlive.children if e.type == "VirtualInCaption"), None
    )
    virtual_out_caption = next(
        (e for e in target_loxlive.children if e.type == "VirtualOutCaption"), None
    )

    input_cmds: dict[str, Element] = {}
    input_containers: dict[str, Element] = {}
    if virtual_in_caption is not None:
        for container in virtual_in_caption.children:
            if container.type != "VirtualUdpIn":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualUdpInCmd":
                    continue
                key = key_from_check(cmd.attrs.get("Check", ""))
                if key is not None:
                    input_cmds[key] = cmd
                    input_containers[key] = container

    output_cmds: dict[str, Element] = {}
    output_containers: dict[str, Element] = {}
    if virtual_out_caption is not None:
        for container in virtual_out_caption.children:
            if container.type != "VirtualOut":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualOutCmd":
                    continue
                # Via `CmdOn` AND `CmdOff`: the combined on/off output
                # carries the same `CmdOn` as the individual `on` command
                # and would otherwise collide with it - see
                # `key_from_output_cmd`.
                key = key_from_output_cmd(cmd.attrs)
                if key is not None:
                    output_cmds[key] = cmd
                    output_containers[key] = container

    return ProjectIndex(
        text=text,
        root_attrs=root_attrs,
        root_open_end=root_open_end,
        root_close_start=root_close_start,
        target_loxlive=target_loxlive,
        virtual_in_caption=virtual_in_caption,
        virtual_out_caption=virtual_out_caption,
        input_cmds=input_cmds,
        output_cmds=output_cmds,
        input_containers=input_containers,
        output_containers=output_containers,
        # Over the entire raw text, not only over <C> elements: <Co>
        # wiring stubs also carry U-IDs that a newly generated ID must not
        # collide with (design section 6).
        all_u_values=set(_U_ATTR.findall(text)),
        all_inames=set(_INAME_ATTR.findall(text)),
    )
