"""Tests fuer das Zertifikatsmaterial des HTTPS-Listeners (Entwurf 4.4/4.5).

Diese Datei prueft drei Zusagen, die der Entwurf ausdruecklich macht: dass
das Server-Zertifikat die Adressen abdeckt, unter denen der Dienst
tatsaechlich erreichbar ist; dass es sich erneuert, wenn es das nicht mehr
tut (Ablauf oder neue DHCP-Adresse), waehrend die CA das ueberlebt; und dass
ein Fehlschlag nie als Ausnahme nach draussen dringt, weil TLS den Start
nicht verhindern darf.

`_now` in `loxmatter.tls` existiert allein als Naht fuer den Ablauftest -
ohne sie muesste dieser Test 397 Tage warten oder ein Zertifikat mit
absichtlich kaputter Laufzeit erzeugen, was etwas anderes pruefte als den
echten Pfad.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from cryptography import x509

from loxmatter import tls
from loxmatter.tls import TlsUnavailableError, ensure_tls_material, prepare_tls


def _certificate(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _serial(path: Path) -> int:
    return _certificate(path).serial_number


def _san(path: Path) -> dict[str, list[str]]:
    extension = _certificate(path).extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return {
        "dns": list(extension.value.get_values_for_type(x509.DNSName)),
        "ip": [str(value) for value in extension.value.get_values_for_type(x509.IPAddress)],
    }


def test_a_fresh_directory_gets_a_ca_and_a_server_certificate(tmp_path):
    material = ensure_tls_material(tmp_path)

    assert material.ca_certificate == tmp_path / "ca.crt"
    assert material.certificate == tmp_path / "server.crt"
    assert material.private_key == tmp_path / "server.key"
    for path in (material.ca_certificate, material.certificate, material.private_key):
        assert path.exists()


def test_the_ca_is_a_ca_and_the_server_certificate_is_not(tmp_path):
    ensure_tls_material(tmp_path)

    ca_constraints = _certificate(tmp_path / "ca.crt").extensions.get_extension_for_class(
        x509.BasicConstraints
    )
    server_constraints = _certificate(tmp_path / "server.crt").extensions.get_extension_for_class(
        x509.BasicConstraints
    )
    assert ca_constraints.value.ca is True
    assert server_constraints.value.ca is False


def test_the_server_certificate_is_signed_by_the_ca(tmp_path):
    ensure_tls_material(tmp_path)

    assert _certificate(tmp_path / "server.crt").issuer == _certificate(tmp_path / "ca.crt").subject


@pytest.mark.skipif(os.getuid() == 0, reason="root umgeht Dateirechte")
def test_the_private_keys_are_readable_by_nobody_else(tmp_path):
    """Ein Schluessel, den jeder Nutzer der Maschine lesen kann, ist kein
    Schluessel - und das TLS-Verzeichnis liegt neben der Datenbank, also an
    einer Stelle, an der auch andere Dinge liegen."""
    ensure_tls_material(tmp_path)

    for name in ("ca.key", "server.key"):
        assert (tmp_path / name).stat().st_mode & 0o077 == 0


def test_the_certificate_always_covers_localhost(tmp_path):
    ensure_tls_material(tmp_path)
    names = _san(tmp_path / "server.crt")

    assert "localhost" in names["dns"]
    assert "127.0.0.1" in names["ip"]
    assert "::1" in names["ip"]


def test_the_certificate_covers_every_address_it_was_given(tmp_path, monkeypatch):
    """Die Adressliste wird hier FEST vorgegeben, statt die echte zu
    benutzen: `local_ipv4_addresses()` liefert in einer Sandbox ohne
    Netzwerk eine leere Liste, und eine Schleife darueber liefe leer durch -
    ein gruener Test, der nichts geprueft hat. Die Suite soll laut README
    ohne Netzwerkzugriff laufen, also darf ihr Ergebnis nicht davon
    abhaengen, ob gerade ein Kabel steckt."""
    monkeypatch.setattr(tls, "local_ipv4_addresses", lambda: ["192.168.178.42", "10.0.0.7"])

    material = ensure_tls_material(tmp_path)
    names = _san(tmp_path / "server.crt")

    assert material.addresses == ("192.168.178.42", "10.0.0.7")
    assert "192.168.178.42" in names["ip"]
    assert "10.0.0.7" in names["ip"]


def test_a_second_call_reuses_what_is_already_there(tmp_path):
    """Sonst bekaeme jeder Neustart ein neues Zertifikat - und jedes Handy
    eine neue Warnung, obwohl sich nichts geaendert hat."""
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") == server_serial


def test_a_new_address_renews_the_certificate_but_keeps_the_ca(tmp_path, monkeypatch):
    """Der DHCP-Fall. Wuerde die CA mitrotieren, waere das auf dem Handy
    eingerichtete Vertrauen nach jedem Router-Neustart wertlos (Entwurf 4.4)."""
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    monkeypatch.setattr(tls, "local_ipv4_addresses", lambda: ["10.9.9.9"])
    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") != server_serial
    assert "10.9.9.9" in _san(tmp_path / "server.crt")["ip"]


def test_an_expired_certificate_is_renewed_but_the_ca_survives(tmp_path, monkeypatch):
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=400)
    monkeypatch.setattr(tls, "_now", lambda: later)
    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") != server_serial


def test_the_server_certificate_does_not_outlive_apples_limit(tmp_path):
    """397 Tage, siehe Entwurf 4.4."""
    material = ensure_tls_material(tmp_path)

    assert material.not_valid_after - dt.datetime.now(dt.UTC) <= dt.timedelta(days=398)


def test_without_cryptography_the_error_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "CRYPTOGRAPHY_AVAILABLE", False)

    with pytest.raises(TlsUnavailableError, match="cryptography"):
        ensure_tls_material(tmp_path)


def test_prepare_tls_reports_a_failure_instead_of_raising(tmp_path, monkeypatch):
    """TLS darf den Start nicht verhindern (Entwurf 4.5)."""
    monkeypatch.setattr(tls, "CRYPTOGRAPHY_AVAILABLE", False)

    state = prepare_tls(tmp_path, 8443)

    assert state.material is None
    assert state.port is None
    assert state.error is not None
    assert "cryptography" in state.error


@pytest.mark.skipif(os.getuid() == 0, reason="root umgeht Dateirechte")
def test_prepare_tls_survives_an_unwritable_directory(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)

    state = prepare_tls(blocked / "tls", 8443)

    assert state.material is None
    assert state.error is not None


def test_port_zero_switches_https_off_without_an_error(tmp_path):
    state = prepare_tls(tmp_path, 0)

    assert state.material is None
    assert state.port is None
    assert state.error is None
    assert not (tmp_path / "ca.crt").exists()


def test_local_addresses_never_contain_loopback():
    """127.0.0.1 wird in `_build_server_certificate` ohnehin fest
    hinzugefuegt - stuende es zusaetzlich in dieser Liste, taeuschte der
    Vergleich in `ensure_tls_material` eine Aenderung vor, wo keine ist.

    Der Filter wird an einer FESTEN Eingabe geprueft, nicht am echten
    Netzwerk: ohne Netz ist die echte Liste leer, und eine Schleife darueber
    liefe leer durch. `_filter_addresses` ist genau deshalb eine eigene
    Funktion - sie ist der Teil von `local_ipv4_addresses`, der eine
    pruefbare Regel enthaelt."""
    found = {"127.0.0.1", "127.0.1.1", "192.168.178.42", "10.0.0.7"}

    assert tls._filter_addresses(found) == ["10.0.0.7", "192.168.178.42"]


def test_the_real_address_lookup_returns_no_loopback(tmp_path):
    """Dieselbe Regel am echten Aufruf. Er darf eine leere Liste liefern
    (kein Netz) - was er nicht darf, ist Loopback zurueckgeben."""
    for address in tls.local_ipv4_addresses():
        assert not address.startswith("127.")


def test_a_password_protected_ca_key_does_not_crash_the_startup(tmp_path):
    """Ein passwortgeschuetzter CA-Schluessel loest TypeError aus, der
    bisher nicht gefangen wurde. Das Modul muss ihn abfangen wie jeden
    anderen Fehler beim Laden des Schluessels, um die Zusage einzuloesen:
    prepare_tls darf niemals wirft (TLS darf den Start nicht verhindern).
    Stattdessen wird die CA regeneriert.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Gültiges Material erzeugen
    material = ensure_tls_material(tmp_path)
    original_ca_serial = _serial(tmp_path / "ca.crt")
    assert material.ca_certificate.exists()
    assert material.private_key.exists()

    # CA-Schlüssel durch einen passwortgeschützten ersetzen
    encrypted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encrypted_key_bytes = encrypted_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"password"),
    )
    ca_key_path = tmp_path / "ca.key"
    ca_key_path.write_bytes(encrypted_key_bytes)

    # prepare_tls darf nicht werfen - der TypeError muss abgefangen werden
    state = prepare_tls(tmp_path, 8443)

    # Das System hat sich erholt und eine neue CA erzeugt
    assert state.material is not None
    assert state.port == 8443
    assert state.error is None
    # Die CA wurde regeneriert, weil der alte Schluessel nicht geladen werden konnte
    assert _serial(tmp_path / "ca.crt") != original_ca_serial
