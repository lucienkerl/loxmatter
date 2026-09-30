"""The daily-check switch (design 2026-09-30, section 6.2)."""

from loxmatter.model.store import Store


def test_the_daily_check_is_on_when_nothing_is_stored(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    assert store.firmware_settings.get_daily_check_enabled() is True


def test_the_daily_check_can_be_switched_off_and_on(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    store.firmware_settings.set_daily_check_enabled(False)
    store.close()
    store = Store(path)
    assert store.firmware_settings.get_daily_check_enabled() is False
    store.firmware_settings.set_daily_check_enabled(True)
    assert store.firmware_settings.get_daily_check_enabled() is True
