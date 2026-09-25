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

"""Tests for `BridgeSettingsStore` - the part of the store that manages the
connection data to the bridge (IP, ports), analogous to `AuthStore`.

See docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4."""

from __future__ import annotations

from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store


def test_a_fresh_store_has_no_bridge_ip_but_default_ports(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        settings = store.settings.get()
        assert settings.bridge_ip is None
        assert settings.udp_port == DEFAULT_UDP_PORT
        assert settings.listen_port == DEFAULT_LISTEN_PORT
        assert settings.saved_at is None
    finally:
        store.close()


def test_save_persists_all_three_values(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        saved = store.settings.save(
            bridge_ip="192.168.1.20", udp_port=7001, listen_port=8081, miniserver_ip=None
        )
        assert saved.bridge_ip == "192.168.1.20"
        assert saved.udp_port == 7001
        assert saved.listen_port == 8081
        assert saved.saved_at is not None

        reloaded = store.settings.get()
        assert reloaded.bridge_ip == "192.168.1.20"
        assert reloaded.udp_port == 7001
        assert reloaded.listen_port == 8081
        assert reloaded.saved_at == saved.saved_at
    finally:
        store.close()


def test_save_overwrites_a_previous_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip=None
        )
        store.settings.save(
            bridge_ip="10.0.0.2", udp_port=7002, listen_port=8082, miniserver_ip=None
        )
        settings = store.settings.get()
        assert settings.bridge_ip == "10.0.0.2"
        assert settings.udp_port == 7002
        assert settings.listen_port == 8082
    finally:
        store.close()


def test_settings_survive_a_reopened_connection(tmp_path):
    """Server-side instead of localStorage (draft section 4): the whole
    point is that it survives a process restart."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.settings.save(
            bridge_ip="192.168.1.20", udp_port=7000, listen_port=8080, miniserver_ip=None
        )
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.settings.get().bridge_ip == "192.168.1.20"
    finally:
        reopened.close()


def test_a_fresh_store_has_no_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.settings.get().miniserver_ip is None
    finally:
        store.close()


def test_save_persists_the_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        saved = store.settings.save(
            bridge_ip="192.168.1.20", udp_port=7000, listen_port=8080, miniserver_ip="192.168.1.77"
        )
        assert saved.miniserver_ip == "192.168.1.77"
        assert store.settings.get().miniserver_ip == "192.168.1.77"
    finally:
        store.close()


def test_saving_none_removes_a_stored_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip="10.0.0.9"
        )
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip=None
        )
        assert store.settings.get().miniserver_ip is None
    finally:
        store.close()


def test_seed_writes_ip_and_port_into_an_empty_store_and_leaves_saved_at_alone(tmp_path):
    """Seeding is not a save in the interface: `saved_at` must keep saying
    "not saved yet"."""
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.settings.seed_miniserver("10.0.1.99", 7005) is True
        settings = store.settings.get()
        assert settings.miniserver_ip == "10.0.1.99"
        assert settings.udp_port == 7005
        assert settings.saved_at is None
    finally:
        store.close()


def test_seed_does_not_overwrite_a_stored_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip="10.0.0.9"
        )
        assert store.settings.seed_miniserver("10.0.1.99", 7000) is False
        assert store.settings.get().miniserver_ip == "10.0.0.9"
    finally:
        store.close()


def test_seed_keeps_a_udp_port_saved_in_the_interface(tmp_path):
    """Spec section 3: a port saved in the card is what the Loxone project's
    templates say. `--port` must not overwrite it, or the bridge keeps
    sending past the virtual input."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7001, listen_port=8080, miniserver_ip=None
        )
        assert store.settings.seed_miniserver("10.0.1.99", 7000) is True
        settings = store.settings.get()
        assert settings.miniserver_ip == "10.0.1.99"
        assert settings.udp_port == 7001
    finally:
        store.close()
