from loxmatter.export.xml import render_document


def test_document_starts_with_utf8_bom():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert out.startswith(b"\xef\xbb\xbf")


def test_document_uses_crlf_line_endings():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert b"\r\n" in out
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_declaration_comes_first():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    text = out.decode("utf-8-sig")
    assert text.splitlines()[0] == '<?xml version="1.0" encoding="utf-8"?>'


def test_loxone_value_placeholder_is_escaped():
    """Ein unescaptes <v> macht die Datei fuer Loxone Config unlesbar."""
    out = render_document(
        "VirtualOut",
        [("Title", "T")],
        [("VirtualOutCmd", [("CmdOn", "/cmd/d1_1_level/<v>")])],
    )
    text = out.decode("utf-8-sig")
    assert "&lt;v&gt;" in text
    assert "/<v>" not in text


def test_backslash_v_in_check_is_left_alone():
    """\\v ist Loxones Wertplatzhalter in der Befehlserkennung, kein XML."""
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Check", "d1_1_temp:\\v")])],
    )
    assert "d1_1_temp:\\v" in out.decode("utf-8-sig")


def test_quotes_and_ampersands_are_escaped():
    out = render_document("VirtualInUdp", [("Title", 'Klaus & "Otto"')], [])
    text = out.decode("utf-8-sig")
    assert "&amp;" in text
    assert "&quot;" in text or "&#34;" in text


def test_children_are_rendered_as_self_closing_elements():
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Title", "A")]), ("VirtualInUdpCmd", [("Title", "B")])],
    )
    text = out.decode("utf-8-sig")
    assert text.count("<VirtualInUdpCmd ") == 2
    assert text.count("/>") == 2
    assert text.rstrip().endswith("</VirtualInUdp>")
