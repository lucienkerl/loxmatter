from loxmatter.export.signals import SignalKind
from loxmatter.export.xml import BOM
from loxmatter.model.store import SignalRef, StoredCommand, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import build_plan
from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.patch import apply_plan


def _signal(key: str, device_id: int, title: str = "Ein/Aus", unit: str = "") -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title=title,
        unit=unit,
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
        resend=False,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
    )


def _command(key: str, slug: str, device_id: int, command_id: int) -> StoredCommand:
    return StoredCommand(
        key=key,
        slug=slug,
        node_id=device_id,
        endpoint=1,
        cluster_id=6,
        command_id=command_id,
        takes_value=False,
        device_id=device_id,
    )


def _patch_bytes(index, device, signals, *, include_new_devices, commands=()):
    commands = list(commands)
    plan = build_plan(index, [device], {device.id: signals}, {device.id: commands})
    return apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: commands},
        include_new_devices=include_new_devices,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )


def _patch(index, device, signals, *, include_new_devices, commands=()):
    return _patch_bytes(
        index, device, signals, include_new_devices=include_new_devices, commands=commands
    ).decode("utf-8-sig")


def test_updated_attribute_is_replaced_in_place(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert 'Title="Ein/Aus"' in patched
    assert 'Title="Alter Titel"' not in patched
    # Die U-ID des aktualisierten Objekts bleibt exakt erhalten - Verdrahtung
    # (Co) darf ein Update nie anfassen.
    assert '"1000-0002-0000-aaaaaaaaaaaaaaaa"' in patched
    assert '<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>' in patched


def test_orphaned_object_is_left_untouched(sample_project):
    """Ein verwaistes Signal wird nur gemeldet, nie veraendert (Entwurf
    Abschnitt 2). Das ist eine Inhalts-, keine Byte-Identitaets-Zusage - die
    prueft `test_unchanged_plan_leaves_the_file_byte_identical` unten."""
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert 'Title="Verwaist"' in patched
    assert 'Check="d9_9_verwaist:\\v"' in patched


def _unchanged_signals() -> list[StoredSignal]:
    """Signale, die exakt dem entsprechen, was in `sample_project` steht - der
    Plan enthaelt damit weder `updated` noch `new_signal`/`new_device` (siehe
    `tests/projectsync/test_diff.py`,
    `test_has_changes_is_false_when_everything_matches`)."""
    return [_signal("d1_1_onoff", 1, title="Alter Titel")]


def test_unchanged_plan_leaves_the_file_byte_identical(sample_project):
    """Die Kernzusage des ganzen Entwurfs (Abschnitt 3.2/9): was nicht im Plan
    steht, wird nicht angefasst. Ein Plan ohne jede geplante Aenderung muss
    darum EXAKT dieselben Bytes zurueckliefern - einzige erlaubte Abweichung
    ist ein vorangestelltes BOM, wenn das Original keines hatte (siehe
    `patch`-Moduldocstring).

    Bewusst ein voller Byte-Vergleich statt einiger Teilstring-Proben: nur so
    faellt auch eine Aenderung auf, an die beim Schreiben des Tests niemand
    gedacht hat."""
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    plan = build_plan(index, [device], {1: _unchanged_signals()}, {1: []})
    assert plan.has_changes is False

    patched = _patch_bytes(index, device, _unchanged_signals(), include_new_devices=True)
    assert patched == ("﻿" + sample_project).encode("utf-8")
    assert patched.decode("utf-8").lstrip("﻿") == sample_project.lstrip("﻿")


def test_existing_bom_is_preserved_and_not_duplicated(sample_project):
    """Gegenstueck zum Test oben: hatte das Original schon ein BOM, kommt
    genau EINES zurueck, nicht zwei. Loxone Config schreibt seine Projektdatei
    mit BOM, dieser Fall ist also der Normalfall - der BOM-lose oben der
    Sonderfall (siehe `patch`-Moduldocstring)."""
    with_bom = BOM + sample_project
    index = build_index(with_bom)
    device = _device(1, "Altes Geraet")
    patched = _patch_bytes(index, device, _unchanged_signals(), include_new_devices=True)

    assert patched == with_bom.encode("utf-8")
    assert patched.decode("utf-8").count(BOM) == 1


def test_created_u_ids_are_unique_across_the_whole_file(sample_project):
    """ID-Eindeutigkeit neu erzeugter `U`-Werte gegen ALLE vorhandenen
    (Entwurf Abschnitt 6/9) - ueber ein Szenario, das beide Neuanlage-Wege
    gleichzeitig geht: ein neues Signal in einem bestehenden Container
    (Geraet 1) und ein komplett neues Geraet mit mehreren Signalen und
    Kommandos (Geraet 2). Jedes davon erzeugt neben dem Objekt selbst noch
    `Co`-Verdrahtungsstummel mit eigenen IDs."""
    import re

    index = build_index(sample_project)
    devices = [_device(1, "Altes Geraet"), _device(2, "Neues Geraet")]
    signals = {
        1: [_signal("d1_1_onoff", 1, title="Alter Titel"), _signal("d1_1_temp", 1)],
        2: [_signal("d2_1_onoff", 2), _signal("d2_1_temp", 2)],
    }
    commands = {1: [], 2: [_command("d2_1_on", "on", 2, 1), _command("d2_1_off", "off", 2, 0)]}
    plan = build_plan(index, devices, signals, commands)
    # Vor dem Patchen festhalten: `new_unique_id` traegt jede erzeugte ID
    # sofort in `index.all_u_values` nach.
    u_count_before = len(index.all_u_values)
    patched = apply_plan(
        index,
        plan,
        devices,
        signals,
        commands,
        include_new_devices=True,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    ).decode("utf-8-sig")

    all_u = re.findall(r'\bU="([^"]*)"', patched)
    assert len(all_u) > u_count_before  # es wurden wirklich welche erzeugt
    assert len(set(all_u)) == len(all_u)


def test_new_signal_is_appended_inside_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [
        _signal("d1_1_onoff", 1, title="Alter Titel"),
        _signal("d1_1_temp", 1, title="Temperatur"),
    ]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert 'Check="d1_1_temp:\\v"' in patched
    # Eingefuegt in denselben Container wie das bestehende d1_1_onoff, nicht
    # irgendwo im Dokument und nicht als neuer Geraete-Container. Ueber
    # build_index statt Byte-Offset-Arithmetik geprueft: ein naiver
    # patched.index("</C>", container_start) faende das schliessende Tag des
    # ERSTEN Kindes (VCI1), nicht das des Containers selbst - derselbe
    # Fehler, den Task 3 im Scanner schon einmal beheben musste.
    patched_index = build_index(patched)
    assert "d1_1_temp" in patched_index.input_containers
    assert (
        patched_index.input_containers["d1_1_temp"].attrs["U"]
        == index.input_containers["d1_1_onoff"].attrs["U"]
    )


def test_new_device_is_absent_without_the_flag(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert "d2_1_onoff" not in patched


def test_new_device_is_created_with_the_flag(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    assert 'Check="d2_1_onoff:\\v"' in patched
    assert 'Title="Matter — Neues Geraet"' in patched
    assert 'Address="10.0.0.5"' in patched


def test_new_device_with_several_signals_gets_exactly_one_container(sample_project):
    """Ein komplett neues Geraet mit MEHREREN neuen Signalen darf genau EINEN
    `VirtualUdpIn`-Container bekommen, der alle Kommandos als Kinder traegt -
    nicht einen eigenen Container je Signal.

    Der Fall ist nicht exotisch, sondern der Normalfall: `export.signals.
    to_inputs` erzeugt je Geraet immer zusaetzlich das Online-Signal, jedes
    reale neue Geraet hat also mindestens zwei `NEW_DEVICE`-Eintraege. Ein
    Container je Eintrag ergaebe mehrere gleichnamige Geraete mit identischer
    Adresse/Port - eine strukturell falsche Projektdatei."""
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2), _signal("d2_1_temp", 2, title="Temperatur", unit="°C")]
    commands = [_command("d2_1_on", "on", 2, 1), _command("d2_1_off", "off", 2, 0)]
    patched = _patch(index, device, signals, include_new_devices=True, commands=commands)

    # Je genau ein neuer Container - zusaetzlich zu dem je einen, den die
    # Beispieldatei fuer Geraet 1 schon mitbringt.
    assert patched.count('Type="VirtualUdpIn"') == 2
    assert patched.count('Type="VirtualOut"') == 2

    patched_index = build_index(patched)
    new_input_keys = {key for key in patched_index.input_containers if key.startswith("d2_")}
    assert new_input_keys == {"d2_1_onoff", "d2_1_temp", "d2_online"}
    # Alle drei haengen im SELBEN Container (gleiche U-ID).
    assert len({patched_index.input_containers[key].attrs["U"] for key in new_input_keys}) == 1

    new_output_keys = {key for key in patched_index.output_containers if key.startswith("d2_")}
    assert new_output_keys == {"d2_1_on", "d2_1_off"}
    assert len({patched_index.output_containers[key].attrs["U"] for key in new_output_keys}) == 1


def test_next_obj_is_raised_when_new_objects_were_created(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    next_obj = int(patched.split('NextObj="', 1)[1].split('"', 1)[0])
    assert next_obj > 100  # Ausgangswert in der Beispieldatei


def test_output_is_valid_xml(sample_project):
    import xml.etree.ElementTree as ET

    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    ET.fromstring(patched)  # wirft bei ungueltigem XML


def test_missing_attribute_is_inserted_into_existing_tag(sample_project):
    """`d1_1_onoff` hat im Beispieldokument gar kein `Unit`-Attribut auf dem
    `<C>`-Tag selbst (nur das `Display`-Kindelement traegt eines). Ein Signal
    mit einer echten physikalischen Einheit (anders als der Standard-`_signal`
    mit `unit=""`) macht `Unit` zu einem gewuenschten, aber im bestehenden Tag
    fehlenden Attribut - das deckt den `if span is None`-Einfuege-Zweig in
    `_update_edits` ab, den bislang kein Test beruehrt hat (alle anderen
    Updates aendern nur ein bereits vorhandenes `Title`)."""
    import xml.etree.ElementTree as ET

    index = build_index(sample_project)
    # Vorher: kein `Unit=` auf dem VCI1-Tag selbst.
    assert "Unit" not in index.input_cmds["d1_1_onoff"].attrs

    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Alter Titel", unit="°C")]
    patched = _patch(index, device, signals, include_new_devices=False)

    # Neu eingefuegt, mit dem richtigen (escapten) Wert, in genau das Tag,
    # dem es vorher fehlte - nicht irgendwo sonst im Dokument.
    assert 'Unit="&lt;v.1&gt; °C"' in patched
    patched_index = build_index(patched)
    assert patched_index.input_cmds["d1_1_onoff"].attrs["Unit"] == "<v.1> °C"
    # Die uebrigen, unveraenderten Attribute des Tags bleiben erhalten - das
    # Einfuegen haengt nur vor dem schliessenden '>' an, statt das Tag zu
    # ersetzen.
    assert 'Check="d1_1_onoff:\\v"' in patched
    assert 'Title="Alter Titel"' in patched

    ET.fromstring(patched)  # wirft bei ungueltigem XML


# Synthetische Projektdatei, die absichtlich GAR KEINEN `VirtualInCaption`-
# Abschnitt enthaelt - anders als `sample_project` aus conftest.py, das immer
# beide Abschnitte hat. Ein reales Projekt, in dem noch nie ein virtueller
# Eingang angelegt wurde, sieht so aus. Bewusst nicht in conftest.py, weil
# dieses Dokument nur fuer den Automatisch-anlegen-Pfad in `_new_device_edit`
# gebraucht wird.
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


# Ein Projekt, in dem VOR dem echten `Title` ein Attribut steht, dessen Name
# auf einen verwalteten Attributnamen ENDET (`XTitle`). Solche Namen sind in
# einer echten Projektdatei nicht ausgeschlossen - dieses Projekt kennt laengst
# nicht alle Bausteintypen, die Loxone Config schreibt.
DECOY_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' XTitle="Bitte nicht anfassen" Title="Alter Titel" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_update_does_not_rewrite_an_attribute_that_only_ends_in_the_name():
    """`_attr_span` suchte `Title="..."` ohne Wortgrenze links - `re.search`
    lieferte damit den ersten Treffer irgendwo im Tag, also auch die zweite
    Haelfte eines laengeren Attributnamens wie `XTitle`. Das Update schrieb
    dann still in das FALSCHE Attribut und liess das echte unberuehrt: genau
    der Bruch der Zusage, nie Bytes anzufassen, die dieses Projekt nicht
    versteht (Entwurf Abschnitt 3.2)."""
    index = build_index(DECOY_ATTR_PROJECT)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Neuer Titel")]
    patched = _patch(index, device, signals, include_new_devices=False)

    assert 'XTitle="Bitte nicht anfassen"' in patched
    patched_index = build_index(patched)
    cmd = patched_index.input_cmds["d1_1_onoff"]
    assert cmd.attrs["XTitle"] == "Bitte nicht anfassen"
    assert cmd.attrs["Title"] == "Neuer Titel"


# Wie `sample_project`, aber OHNE ein einziges `U`-Attribut irgendwo im
# Dokument - der Fall, den Entwurf Abschnitt 10 als offenes Risiko nennt
# (Datei ganz ohne `U`-Attribute, oder eine Config-Version mit abweichendem
# ID-Format). `d1_1_onoff` existiert schon (bleibt `unchanged`), `d1_1_temp`
# fehlt noch - das erzwingt eine neue ID ueber `_new_signal_edit` ->
# `new_unique_id` -> `_installation_suffix`, ohne dass ein komplett neues
# Geraet (und damit `MissingCaptionError`) noetig waere.
NO_U_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1">\r\n'
    '\t\t<C Type="VirtualUdpIn" IName="VUI1" Title="Matter — Altes Geraet" WF="16384"'
    ' Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" Title="Alter Titel" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_new_signal_without_any_existing_u_id_raises_project_format_error():
    """Finding N1 aus dem Re-Review: `_installation_suffix` warf bislang einen
    nackten `ValueError`, wenn keine bestehende `U`-ID im erwarteten
    4-Hex-Gruppen-Format zu finden war - unbehandelt am Upload-Endpunkt eine
    HTTP 500 statt der ueblichen verstaendlichen 400 (Entwurf Abschnitt 8).
    Erwartet ist ein `ProjectFormatError`, wie bei jedem anderen erkannten
    Formatfehler dieser Datei auch."""
    import pytest

    from loxmatter.projectsync.scan import ProjectFormatError

    index = build_index(NO_U_ATTR_PROJECT)
    assert index.all_u_values == set()

    device = _device(1, "Altes Geraet")
    signals = [
        _signal("d1_1_onoff", 1, title="Alter Titel"),
        _signal("d1_1_temp", 1, title="Temperatur"),
    ]
    plan = build_plan(index, [device], {1: signals}, {1: []})

    with pytest.raises(ProjectFormatError, match="Installations-Suffix"):
        apply_plan(
            index,
            plan,
            [device],
            {1: signals},
            {1: []},
            include_new_devices=False,
            bridge_ip="10.0.0.5",
            port=7000,
            listen=8080,
        )


def test_next_obj_edit_is_skipped_when_next_obj_is_not_numeric(sample_project):
    """Finding N1 aus dem Re-Review: `_next_obj_edit` rief bislang
    ungeschuetzt `int(index.root_attrs["NextObj"])` auf - ein nicht-dezimaler
    Wert warf einen nackten `ValueError`, unbehandelt eine HTTP 500. `NextObj`
    ist laut Entwurf Abschnitt 6/10 ohnehin nur eine unverifizierte,
    konservative Bestleistung, kein belegtes Verhalten - ein kaputter Wert
    darf darum nicht den ganzen (sonst gueltigen) Patch scheitern lassen,
    sondern nur diese eine Attribut-Aenderung ueberspringen."""
    bad_project = sample_project.replace('NextObj="100"', 'NextObj="not-a-number"')
    index = build_index(bad_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)

    # Das kaputte Attribut bleibt unangetastet ...
    assert 'NextObj="not-a-number"' in patched
    # ... aber die eigentliche Neuanlage hat trotzdem stattgefunden (der
    # `created_count > 0`-Zweig in `_next_obj_edit` wurde also wirklich
    # erreicht, nicht nur der fruehe `created_count == 0`-Ausstieg).
    assert 'Check="d2_1_onoff:\\v"' in patched


def test_new_device_without_virtual_in_caption_creates_the_caption_too():
    """`include_new_devices=True` fuer ein Geraet, das einen komplett neuen
    Eingangs-Container braucht, in einem Projekt ohne jeden bestehenden
    `VirtualInCaption`-Abschnitt: `_new_device_edit` legt diesen Abschnitt
    inzwischen selbst mit an (Entwurf Abschnitt 8, Nutzerwunsch nach dem
    Review) - der Nutzer soll nicht von Hand einmal manuell etwas in Loxone
    Config anlegen muessen, nur um den experimentellen Pfad ueberhaupt testen
    zu koennen. Das Geraet-Kommando steckt danach INNERHALB der neu
    angelegten Caption, nicht daneben."""
    import xml.etree.ElementTree as ET

    index = build_index(NO_VIRTUAL_IN_CAPTION_PROJECT)
    assert index.virtual_in_caption is None

    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    plan = build_plan(index, [device], {device.id: signals}, {device.id: []})

    patched = apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: []},
        include_new_devices=True,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )
    patched_text = patched.decode("utf-8-sig")

    assert 'Type="VirtualInCaption"' in patched_text
    ET.fromstring(patched_text)  # wirft bei ungueltigem XML

    patched_index = build_index(patched_text)
    assert patched_index.virtual_in_caption is not None
    cmd = patched_index.input_cmds["d2_1_onoff"]
    container = patched_index.input_containers["d2_1_onoff"]
    # Das Kommando steckt in einem Container, der wiederum unter der neu
    # angelegten Caption haengt - nicht als loses Geschwister-Objekt daneben.
    assert container in patched_index.virtual_in_caption.children
    assert cmd in container.children
