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

"""Tests fuer `BridgeSettingsStore` - den Teil des Stores, der die
Verbindungsdaten zur Bruecke (IP, Ports) verwaltet, analog zu `AuthStore`.

Siehe docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4."""

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
        saved = store.settings.save(bridge_ip="192.168.1.20", udp_port=7001, listen_port=8081)
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
        store.settings.save(bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080)
        store.settings.save(bridge_ip="10.0.0.2", udp_port=7002, listen_port=8082)
        settings = store.settings.get()
        assert settings.bridge_ip == "10.0.0.2"
        assert settings.udp_port == 7002
        assert settings.listen_port == 8082
    finally:
        store.close()


def test_settings_survive_a_reopened_connection(tmp_path):
    """Serverseitig statt localStorage (Entwurf Abschnitt 4): der Punkt ist
    genau, dass es einen Prozessneustart uebersteht."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.settings.save(bridge_ip="192.168.1.20", udp_port=7000, listen_port=8080)
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.settings.get().bridge_ip == "192.168.1.20"
    finally:
        reopened.close()
