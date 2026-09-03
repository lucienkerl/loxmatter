from loxmatter.projectsync.scan import parse_attrs, parse_root, scan_children

NESTED = (
    '<C Type="VirtualUdpIn" IName="VUI1" U="u-container" Title="Geraet">'
    '<C Type="VirtualUdpInCmd" IName="VCI1" U="u-cmd1" Check="d1_1_on:\\v" Title="An">'
    '<Co K="Q" U="u-co1"/>'
    "</C>"
    '<C Type="VirtualUdpInCmd" IName="VCI2" U="u-cmd2" Check="d1_1_off:1" Title="Aus"/>'
    "</C>"
)

ROOT_DOC = f'<?xml version="1.0" encoding="utf-8"?>\r\n<ControlList Version="275" NextObj="10">{NESTED}</ControlList>\r\n'


def test_parse_attrs_reads_and_unescapes_values():
    attrs = parse_attrs('<C Type="A" Title="a &amp; b"/>')
    assert attrs == {"Type": "A", "Title": "a & b"}


def test_scan_children_finds_one_top_level_element():
    [container] = scan_children(NESTED, 0, len(NESTED))
    assert container.type == "VirtualUdpIn"
    assert container.attrs["IName"] == "VUI1"
    assert not container.self_closing


def test_scan_children_finds_nested_container_and_leaf():
    [container] = scan_children(NESTED, 0, len(NESTED))
    assert len(container.children) == 2
    cmd1, cmd2 = container.children
    assert cmd1.type == "VirtualUdpInCmd"
    assert cmd1.attrs["Check"] == "d1_1_on:\\v"
    assert not cmd1.self_closing
    assert cmd2.self_closing
    assert cmd2.attrs["Check"] == "d1_1_off:1"


def test_element_spans_point_at_the_right_substrings():
    [container] = scan_children(NESTED, 0, len(NESTED))
    cmd1 = container.children[0]
    assert NESTED[cmd1.open_start : cmd1.open_end].startswith('<C Type="VirtualUdpInCmd"')
    assert NESTED[cmd1.inner_end : cmd1.inner_end + 4] == "</C>"
    assert NESTED[cmd1.outer_end - 4 : cmd1.outer_end] == "</C>"


def test_parse_root_finds_control_list_and_content_bounds():
    attrs, open_start, open_end, close_start = parse_root(ROOT_DOC)
    assert attrs["NextObj"] == "10"
    assert ROOT_DOC[open_start:open_end].startswith("<ControlList ")
    assert ROOT_DOC[open_end:close_start] == NESTED
    assert ROOT_DOC[close_start:].startswith("</ControlList>")


def test_parse_root_raises_on_missing_control_list():
    import pytest

    from loxmatter.projectsync.scan import ProjectFormatError

    with pytest.raises(ProjectFormatError):
        parse_root("<NotAProject/>")


def test_scan_children_handles_unescaped_gt_in_attribute_value():
    # XML erlaubt ein woertliches '>' in einem Attributwert, ohne dass es als
    # `&gt;` escaped werden muss (nur '<', '&' und das Anfuehrungszeichen
    # selbst muessen escaped werden). Ein Titel wie 'Temp > 20' ist also
    # gueltiges, unescaped XML, das eine Loxone-Projektdatei so enthalten
    # darf. Ein naives `text.index(">", open_start)` faende das '>' mitten im
    # Attributwert statt das wirkliche Tag-Ende.
    doc = (
        '<C Type="VirtualUdpIn" IName="VUI1" U="u-container" Title="Temp > 20">'
        '<C Type="VirtualUdpInCmd" IName="VCI1" U="u-cmd1" Title="An"/>'
        "</C>"
        '<C Type="VirtualUdpIn" IName="VUI2" U="u-container2" Title="Zweites">'
        "</C>"
    )
    [first, second] = scan_children(doc, 0, len(doc))

    assert first.attrs["Title"] == "Temp > 20"
    assert first.attrs["IName"] == "VUI1"
    assert not first.self_closing
    assert len(first.children) == 1
    assert first.children[0].attrs["IName"] == "VCI1"

    assert second.type == "VirtualUdpIn"
    assert second.attrs["IName"] == "VUI2"
