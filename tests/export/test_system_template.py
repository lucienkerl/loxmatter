from loxmatter.export.documents import render_system_templates


def text(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_input_template_carries_the_heartbeat():
    viu, _ = render_system_templates("192.168.1.50", 7000)
    assert 'Check="bridge_alive:\\v"' in text(viu)
    assert 'Analog="false"' in text(viu)


def test_output_template_carries_resync():
    _, vo = render_system_templates("192.168.1.50", 7000)
    assert 'CmdOn="/resync"' in text(vo)


def test_both_templates_have_the_info_element_first():
    for raw in render_system_templates("192.168.1.50", 7000):
        assert text(raw).split(">", 2)[2].lstrip().startswith("<Info ")


def test_both_are_utf8_with_bom_and_crlf():
    for raw in render_system_templates("192.168.1.50", 7000):
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\n" not in raw.replace(b"\r\n", b"")


def test_system_templates_carry_no_device_prefix():
    """Sie gehoeren zu keinem Geraet - ein d<id>_ waere falsch."""
    viu, vo = render_system_templates("192.168.1.50", 7000)
    assert "d1_" not in text(viu)
    assert "d1_" not in text(vo)
