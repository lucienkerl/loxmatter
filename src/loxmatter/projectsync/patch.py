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

"""Wendet einen `SyncPlan` als gezielte Textersetzung auf den Original-
Byte-Strom an (Entwurf Abschnitt 3.2) - nie ueber einen XML-Serialisierer.

Jede Aenderung ist ein `_Edit(start, end, replacement)`: `end == start`
bedeutet reines Einfuegen. Alle Edits werden gesammelt, nach `start`
ABSTEIGEND sortiert und von hinten nach vorn angewendet - so bleiben
vorherige Positionen gueltig, ohne Versatz nachrechnen zu muessen.

`apply_plan` ruft `to_inputs`/`to_outputs` selbst auf, genau wie
`diff.build_plan` - dieselbe Quelle fuer beide, damit Plan und Patch niemals
auseinanderlaufen koennen. Der Grund, das nicht ueber den `PlanEntry`
hindurchzureichen: der traegt nur, was die Oberflaeche zeigen muss
(Titel/Schluessel/Status), nicht `unit_format`/`check_suffix`/`off_path`, die
ein neu angelegtes Objekt zusaetzlich braucht."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.outputs import to_outputs
from loxmatter.export.signals import LoxoneInput, to_inputs
from loxmatter.export.xml import escape_attr_value
from loxmatter.model.store import StoredCommand, StoredDevice, StoredSignal
from loxmatter.projectsync.diff import PlanEntry, PlanStatus, SyncPlan
from loxmatter.projectsync.ids import new_iname, new_unique_id
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.schema import (
    find_any_iodata_attrs,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)

__all__ = ["MissingCaptionError", "apply_plan"]


class MissingCaptionError(ValueError):
    """Die Projektdatei hat (noch) keinen `VirtualInCaption`- bzw.
    `VirtualOutCaption`-Abschnitt, in den ein komplett neues Geraet
    eingefuegt werden koennte. Anders als `ProjectFormatError`: die Datei ist
    dabei nicht fehlerhaft, ihr fehlt nur ein optionaler Abschnitt, den genau
    diese Operation braucht - das automatische Anlegen dieses Abschnitts ist
    laut Entwurf ein spaeterer Ausbauschritt, hier (noch) nicht unterstuetzt."""


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    replacement: str


def _attr_span(text: str, tag_start: int, tag_end: int, name: str) -> tuple[int, int] | None:
    """Byte-Bereich von `name="wert"` innerhalb eines Start-Tags, oder `None`,
    wenn das Attribut dort nicht vorkommt.

    Der Rueckblick `(?<![A-Za-z0-9_])` ist kein Detail: ohne ihn faende
    `re.search` fuer `Title` auch die zweite Haelfte eines laengeren
    Attributnamens (`XTitle="..."`) - und weil `search` den ERSTEN Treffer im
    Tag liefert, wuerde ein solches Attribut still ueberschrieben statt des
    eigentlich gemeinten. Ein Attributname beginnt immer nach Leerraum oder
    direkt nach `<C`, nie mitten in einem Bezeichner."""
    pattern = re.compile(rf'(?<![A-Za-z0-9_]){re.escape(name)}="(?:[^"&]|&[^;]+;)*"')
    match = pattern.search(text, tag_start, tag_end)
    return None if match is None else (match.start(), match.end())


def _update_edits(index: ProjectIndex, entry: PlanEntry) -> list[_Edit]:
    element = (index.input_cmds if entry.kind == "input" else index.output_cmds)[entry.key]
    edits: list[_Edit] = []
    for name, (_, new_value) in entry.changes.items():
        span = _attr_span(index.text, element.open_start, element.open_end, name)
        replacement = f'{name}="{escape_attr_value(new_value)}"'
        if span is None:
            # Attribut fehlt im bestehenden Tag ganz (z. B. `Unit` bei einem
            # digitalen Eingang) - vor dem schliessenden '>' einfuegen.
            insert_at = element.open_end - (2 if element.self_closing else 1)
            edits.append(_Edit(insert_at, insert_at, f" {replacement}"))
        else:
            edits.append(_Edit(span[0], span[1], replacement))
    return edits


def _new_signal_edit(
    index: ProjectIndex,
    entry: PlanEntry,
    entries_by_key: Mapping[str, LoxoneInput] | Mapping[str, LoxoneCommand],
) -> _Edit:
    is_input = entry.kind == "input"
    container = index.input_containers if is_input else index.output_containers
    prefix = f"d{entry.device_id}_"
    matching_container = next(
        (element for key, element in container.items() if key.startswith(prefix)), None
    )
    assert matching_container is not None and matching_container.inner_end is not None

    iname_prefix = "VCI" if is_input else "VQC"
    iname = new_iname(iname_prefix, index.all_inames)
    u = new_unique_id(index.all_u_values)
    iodata = sibling_iodata_attrs(index.text, next(iter(matching_container.children)))

    obj = entries_by_key[entry.key]
    open_tag = (
        new_input_cmd_open_tag(cast(LoxoneInput, obj), iname, u)
        if is_input
        else new_output_cmd_open_tag(cast(LoxoneCommand, obj), iname, u)
    )
    children_xml = new_cmd_children_xml(
        kind="input" if is_input else "output", existing_u=index.all_u_values, iodata_attrs=iodata
    )
    full_xml = f"{open_tag}{children_xml}</C>"
    pos = matching_container.inner_end
    return _Edit(pos, pos, full_xml)


def _new_device_edit(
    index: ProjectIndex,
    entries: Sequence[PlanEntry],
    entries_by_key: Mapping[str, LoxoneInput] | Mapping[str, LoxoneCommand],
    bridge_ip: str,
    port: int,
    listen: int,
) -> _Edit:
    """EIN neuer Geraete-Container fuer ALLE `NEW_DEVICE`-Eintraege eines
    Geraets derselben Art (`entries` ist die Gruppe zu einem `(kind,
    device_id)`).

    Bewusst eine Gruppe statt eines einzelnen Eintrags: `export.signals.
    to_inputs` erzeugt je Geraet immer zusaetzlich ein Online-Signal, ein
    real neues Geraet hat also praktisch nie nur einen Eintrag. Ein Container
    je Eintrag ergaebe mehrere gleichnamige `VirtualUdpIn`-Geraete mit
    identischer Adresse und Port, jedes mit genau einem Kommando darin -
    strukturell falsch, nicht nur unschoen."""
    first = entries[0]
    is_input = first.kind == "input"
    caption = index.virtual_in_caption if is_input else index.virtual_out_caption
    if caption is None or caption.inner_end is None:
        section = "VirtualInCaption" if is_input else "VirtualOutCaption"
        raise MissingCaptionError(
            f"Die Projektdatei hat keinen `{section}`-Abschnitt - ein komplett "
            f"neuer Geraete-Container fuer '{first.device_label}' kann darum nicht "
            "automatisch eingefuegt werden. Bitte zuerst manuell einen virtuellen "
            f"{'Eingang' if is_input else 'Ausgang'} in der Loxone Config anlegen."
        )

    container_iname_prefix = "VUI" if is_input else "VQ"
    container_iname = new_iname(container_iname_prefix, index.all_inames)
    container_u = new_unique_id(index.all_u_values)
    if is_input:
        container_open = new_input_container_open_tag(
            first.device_label, bridge_ip, port, container_iname, container_u
        )
    else:
        container_open = new_output_container_open_tag(
            first.device_label, f"http://{bridge_ip}:{listen}", container_iname, container_u
        )

    cmd_iname_prefix = "VCI" if is_input else "VQC"
    iodata = find_any_iodata_attrs(index.text, caption)
    cmds: list[str] = []
    for entry in entries:
        cmd_iname = new_iname(cmd_iname_prefix, index.all_inames)
        cmd_u = new_unique_id(index.all_u_values)
        obj = entries_by_key[entry.key]
        cmd_open = (
            new_input_cmd_open_tag(cast(LoxoneInput, obj), cmd_iname, cmd_u)
            if is_input
            else new_output_cmd_open_tag(cast(LoxoneCommand, obj), cmd_iname, cmd_u)
        )
        children_xml = new_cmd_children_xml(
            kind="input" if is_input else "output",
            existing_u=index.all_u_values,
            iodata_attrs=iodata,
        )
        cmds.append(f"{cmd_open}{children_xml}</C>")

    full_xml = f"{container_open}{''.join(cmds)}</C>"
    pos = caption.inner_end
    return _Edit(pos, pos, full_xml)


def _next_obj_edit(index: ProjectIndex, created_count: int) -> _Edit | None:
    if created_count == 0 or "NextObj" not in index.root_attrs:
        return None
    span = _attr_span(index.text, 0, index.root_open_end, "NextObj")
    if span is None:
        return None
    new_value = str(int(index.root_attrs["NextObj"]) + created_count)
    return _Edit(span[0], span[1], f'NextObj="{new_value}"')


def _apply_edits(text: str, edits: list[_Edit]) -> str:
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def apply_plan(
    index: ProjectIndex,
    plan: SyncPlan,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
    *,
    include_new_devices: bool,
    bridge_ip: str,
    port: int,
    listen: int,
) -> bytes:
    """Baut die gepatchte Datei fuer eine der beiden Download-Varianten
    (Entwurf Abschnitt 3.4/7): `include_new_devices=False` liefert nur
    Updates und neue Signale in bereits bestehenden Geraete-Containern,
    `True` zusaetzlich komplett neue Geraete-Container."""
    desired_inputs: dict[str, LoxoneInput] = {}
    desired_outputs: dict[str, LoxoneCommand] = {}
    for device in devices:
        for input_item in to_inputs(signals_by_device.get(device.id, []), device.id, device.label):
            desired_inputs[input_item.key] = input_item
        for output_item in to_outputs(commands_by_device.get(device.id, [])):
            desired_outputs[output_item.key] = output_item

    edits: list[_Edit] = []
    created_count = 0
    # (kind, device_id) -> alle NEW_DEVICE-Eintraege dieses Geraets, damit ein
    # Geraet genau EINEN neuen Container bekommt statt einen je Signal (siehe
    # `_new_device_edit`). `dict` haelt die Reihenfolge des Plans fest, die
    # erzeugte Datei ist damit reproduzierbar.
    new_device_groups: dict[tuple[str, int], list[PlanEntry]] = {}
    for entry in plan.entries:
        if entry.status is PlanStatus.UPDATED:
            edits += _update_edits(index, entry)
        elif entry.status is PlanStatus.NEW_SIGNAL:
            source = desired_inputs if entry.kind == "input" else desired_outputs
            edits.append(_new_signal_edit(index, entry, source))
            created_count += 1
        elif entry.status is PlanStatus.NEW_DEVICE and include_new_devices:
            new_device_groups.setdefault((entry.kind, entry.device_id), []).append(entry)

    for (kind, _device_id), group in new_device_groups.items():
        source = desired_inputs if kind == "input" else desired_outputs
        edits.append(_new_device_edit(index, group, source, bridge_ip, port, listen))
        # Der Container selbst plus ein Cmd je Eintrag sind alles neue
        # <C>-Objekte.
        created_count += 1 + len(group)

    next_obj_edit = _next_obj_edit(index, created_count)
    if next_obj_edit is not None:
        edits.append(next_obj_edit)

    patched_text = _apply_edits(index.text, edits)
    if not patched_text.startswith("﻿"):
        patched_text = "﻿" + patched_text
    return patched_text.encode("utf-8")
