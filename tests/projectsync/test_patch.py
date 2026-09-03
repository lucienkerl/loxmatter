from loxmatter.export.signals import SignalKind
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


def _patch(index, device, signals, *, include_new_devices, commands=()):
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


def test_untouched_regions_stay_byte_identical(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals, include_new_devices=False)
    # Das verwaiste Signal wird nur gemeldet, nie veraendert (Entwurf
    # Abschnitt 2).
    assert 'Title="Verwaist"' in patched
    assert 'Check="d9_9_verwaist:\\v"' in patched


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
# dieses Dokument nur fuer den Fehlerpfad in `_new_device_edit` gebraucht wird.
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_new_device_without_virtual_in_caption_raises_clear_error():
    """`include_new_devices=True` fuer ein Geraet, das einen komplett neuen
    Eingangs-Container braucht, in einem Projekt ohne jeden bestehenden
    `VirtualInCaption`-Abschnitt: `_new_device_edit` darf hier nicht mit einem
    nackten `AssertionError` abstuerzen, sondern muss einen aussagekraeftigen
    `MissingCaptionError` werfen (Finding 2 aus dem Review)."""
    import pytest

    from loxmatter.projectsync.patch import MissingCaptionError

    index = build_index(NO_VIRTUAL_IN_CAPTION_PROJECT)
    assert index.virtual_in_caption is None

    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    plan = build_plan(index, [device], {device.id: signals}, {device.id: []})

    with pytest.raises(MissingCaptionError, match="VirtualInCaption"):
        apply_plan(
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
