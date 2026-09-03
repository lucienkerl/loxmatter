"""Zertifikatsmaterial fuer den HTTPS-Listener (Entwurf Abschnitt 4.4/4.5).

Dieses Modul kennt weder FastAPI noch uvicorn. Es beantwortet genau zwei
Fragen: unter welchen Adressen ist dieser Dienst erreichbar, und wo liegen
Zertifikat und Schluessel, die dafuer gelten. Wer sie benutzt, steht
woanders (`cli._run` baut daraus die zweite `uvicorn.Config`,
`loxone.server.build_app` liefert die CA aus).

**Warum ein Paar aus CA und Server-Zertifikat, nicht ein einzelnes
selbstsigniertes.** Ein Handy soll einmal etwas Dauerhaftes als
vertrauenswuerdig einrichten koennen. Waere das installierte Objekt das
Server-Zertifikat selbst, waere die Installation nach jedem Adresswechsel
hinfaellig - und Adresswechsel sind bei DHCP der Normalfall, nicht die
Ausnahme. Deshalb wird nur das SERVER-Zertifikat erneuert, wenn eine
Adresse dazukommt oder die Laufzeit endet; die CA ueberlebt das
ausdruecklich (siehe `ensure_tls_material` und
`test_a_new_address_renews_the_certificate_but_keeps_the_ca`).

**Warum RSA-2048 und nicht EC.** EC-Schluessel waeren schneller erzeugt.
Der Unterschied wird einmal beim Start bezahlt und ist dort belanglos,
waehrend ein Geraet, das mit dem Schluesseltyp nicht zurechtkommt, das
gesamte Feature verliert. Bei einer Wahl zwischen Millisekunden und
Kompatibilitaet gewinnt die Kompatibilitaet.

**Warum der Import von `cryptography` einen Schalter hat.** Der Dienst muss
auch dann starten, wenn das Paket fehlt (Entwurf 4.5) - dann eben ohne
HTTPS. Ein `ImportError` auf Modulebene wuerde stattdessen `cli.py` mit
herunterreissen, das dieses Modul unbedingt importiert.

**Zum bedingten Import und mypy.** Der `try/except ImportError` unten macht
`x509`, `hashes`, `serialization`, `rsa` und `NameOID`/`ExtendedKeyUsageOID`
fuer mypy zu Namen, die im `except`-Zweig gar nicht existieren - jede
spaetere Verwendung waere ein "possibly undefined name". Der Import bewusst
unbedingt zu machen, wuerde genau die Anforderung brechen, die dieser
Schalter erfuellen soll (Start auch ohne installiertes Paket). Stattdessen
importiert ein `if TYPE_CHECKING:`-Block dieselben Namen unbedingt: mypy
sieht sie als vorhanden (fuer die reine Typpruefung, ohne das Paket zur
Laufzeit zu brauchen), waehrend der echte Import zur Laufzeit weiter im
`try` steht und bei fehlendem Paket sauber auf `CRYPTOGRAPHY_AVAILABLE =
False` faellt. Kleinerer Eingriff als ein `# type: ignore` an jeder
einzelnen Verwendungsstelle.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Siehe Moduldocstring: nur fuer mypy, damit die Namen unten als
    # bekannt gelten. Zur Laufzeit zaehlt allein der Import im try-Block.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - haengt an der Installation, nicht am Code
    CRYPTOGRAPHY_AVAILABLE = False

CA_FILE = "ca.crt"
CA_KEY_FILE = "ca.key"
SERVER_FILE = "server.crt"
SERVER_KEY_FILE = "server.key"

CA_VALID_DAYS = 3650
# 397 Tage: Apples 398-Tage-Grenze ist fuer Zertifikate belegt, die an eine im
# System mitgelieferte Wurzel anschliessen; ob sie auch fuer eine selbst
# installierte Wurzel greift, ist nicht belegt. Sie einzuhalten kostet nichts
# und nimmt die Frage aus dem Weg (Entwurf 4.4).
SERVER_VALID_DAYS = 397
# Wie lange vor dem Ablauf bereits erneuert wird - sonst laeuft ein
# Zertifikat mitten im Betrieb ab, statt bei einem Neustart.
RENEW_BEFORE_DAYS = 30

CA_COMMON_NAME = "loxmatter local CA"
SERVER_COMMON_NAME = "loxmatter"

# Eine Adresse aus dem Dokumentationsbereich (RFC 5737). An sie wird nichts
# gesendet - ein UDP-Socket "verbindet" sich ohne jedes Paket, und das
# Betriebssystem verraet dabei, ueber welche lokale Adresse es das taete.
_ROUTE_PROBE_ADDRESS = "192.0.2.1"


class TlsUnavailableError(RuntimeError):
    """HTTPS laesst sich in dieser Installation nicht einrichten.

    Traegt immer einen Grund, der einer Person weiterhilft - er landet
    woertlich im Log und in `GET /api/diagnostics/tls`."""


@dataclass(frozen=True)
class TlsMaterial:
    """Wo das gueltige Material liegt, und wofuer es gilt."""

    ca_certificate: Path
    certificate: Path
    private_key: Path
    addresses: tuple[str, ...]
    not_valid_after: dt.datetime


@dataclass(frozen=True)
class TlsState:
    """Was `cli` und die Oberflaeche ueber HTTPS wissen muessen.

    Alle drei Felder zusammen sind eindeutig: `material is None and error is
    None` heisst "abgeschaltet" (`--https-port 0`), `material is None and
    error is not None` heisst "gewollt, aber nicht moeglich" - zwei Faelle,
    die die Oberflaeche unterschiedlich erklaeren muss."""

    material: TlsMaterial | None
    port: int | None
    error: str | None


def _now() -> dt.datetime:
    """Eigene Funktion statt eines direkten Aufrufs: der Ablauftest in
    `tests/test_tls.py` haengt sich hier ein, statt 397 Tage zu warten."""
    return dt.datetime.now(dt.UTC)


def local_ipv4_addresses() -> list[str]:
    """Die IPv4-Adressen, unter denen dieser Rechner erreichbar sein duerfte.

    Zwei Quellen, vereinigt, weil keine allein traegt: der Routen-Test unten
    liefert zuverlaessig die Adresse, ueber die der Verkehr tatsaechlich
    laeuft, aber nur diese eine; `getaddrinfo` ueber den eigenen Rechnernamen
    findet weitere Schnittstellen, liefert auf manchen Debian-Systemen aber
    nur `127.0.1.1`. Loopback fliegt in beiden Faellen raus - `127.0.0.1`
    und `::1` kommen in `_build_server_certificate` ohnehin fest dazu, und
    stuenden sie zusaetzlich hier, taeuschte der Vergleich in
    `ensure_tls_material` eine Aenderung vor, wo keine ist.

    Bewusst ohne `netifaces`/`psutil`: eine weitere Abhaengigkeit fuer eine
    Liste, die im Fehlerfall lediglich eine Adresse weniger enthaelt - der
    Nutzer bekommt dann eine Zertifikatswarnung mehr, keinen Ausfall.
    """
    found: set[str] = set()

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((_ROUTE_PROBE_ADDRESS, 1))
        found.add(probe.getsockname()[0])
    except OSError:
        # Kein Netz, keine Route - dann bleibt es bei localhost.
        pass
    finally:
        probe.close()

    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            # `str(...)`: der Sockaddr-Eintrag ist laut typeshed eine
            # Vereinigung zweier Tupelformen, deren erstes Element in
            # beiden Faellen bereits eine `str` ist - mypy sieht darin aber
            # "str | int" und lehnt die Zuweisung an ein `set[str]` ab.
            found.add(str(entry[4][0]))
    except (OSError, socket.gaierror):
        pass

    return _filter_addresses(found)


def _filter_addresses(found: set[str]) -> list[str]:
    """Der pruefbare Teil von `local_ipv4_addresses`: Loopback raus, sortiert.

    Eigene Funktion, damit die Regel an einer festen Eingabe pruefbar ist -
    die echte Ermittlung liefert in einer Sandbox ohne Netz eine leere
    Menge, und ein Test darueber liefe leer durch."""
    return sorted(address for address in found if not address.startswith("127."))


def _write_private(path: Path, data: bytes) -> None:
    """Schreibt Schluesselmaterial, das nur der eigene Nutzer lesen kann.

    `os.open` mit Modus statt `write_bytes` plus `chmod`: sonst existierte
    die Datei fuer die Dauer eines Schreibvorgangs mit den Rechten, die die
    umask hergibt. Der `chmod` danach ist trotzdem noetig, weil `os.open`
    den Modus bei einer BEREITS vorhandenen Datei ignoriert."""
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
    path.chmod(0o600)


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_bytes(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _build_ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    now = _now()
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Fuenf Minuten Vorlauf: eine leicht nachgehende Uhr auf dem Handy
        # wuerde ein Zertifikat sonst als "noch nicht gueltig" ablehnen.
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


def _build_server_certificate(
    ca_certificate: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    key: rsa.RSAPrivateKey,
    addresses: list[str],
) -> x509.Certificate:
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    names.extend(x509.IPAddress(ipaddress.ip_address(address)) for address in addresses)
    now = _now()
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_COMMON_NAME)]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=SERVER_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _load_certificate(path: Path) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        # Eine kaputte oder halb geschriebene Datei ist kein Grund
        # aufzugeben - sie wird gleich ueberschrieben.
        return None


def _ensure_ca(tls_dir: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    certificate_path = tls_dir / CA_FILE
    key_path = tls_dir / CA_KEY_FILE
    certificate = _load_certificate(certificate_path)
    if certificate is not None and key_path.exists():
        try:
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError):
            # OSError: Datei nicht lesbar. ValueError: syntaktisch ungueltig.
            # TypeError: Schluessel ist passwortgeschuetzt ("Password was not given
            # but private key is encrypted"). Alle drei Faelle bedeuten, dass wir
            # den Schluessel nicht laden koennen, und eine neue CA muss erzeugt werden.
            key = None
        if key is not None and certificate.not_valid_after_utc > _now():
            return certificate, key  # type: ignore[return-value]

    key = _new_key()
    certificate = _build_ca(key)
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    _write_private(key_path, _key_bytes(key))
    logger.info("Lokale CA erzeugt: %s", certificate_path)
    return certificate, key


def _server_is_current(path: Path, ca_certificate: x509.Certificate, addresses: list[str]) -> bool:
    certificate = _load_certificate(path)
    if certificate is None:
        return False
    if certificate.issuer != ca_certificate.subject:
        return False
    if certificate.not_valid_after_utc <= _now() + dt.timedelta(days=RENEW_BEFORE_DAYS):
        return False
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return False
    covered = {str(value) for value in extension.value.get_values_for_type(x509.IPAddress)}
    return set(addresses) <= covered


def ensure_tls_material(tls_dir: Path) -> TlsMaterial:
    """Sorgt dafuer, dass in `tls_dir` gueltiges Material liegt, und sagt wo.

    Erneuert wird nur, was erneuert werden muss: die CA ueberlebt einen
    Adresswechsel und einen Ablauf des Server-Zertifikats (siehe
    Moduldocstring). Wirft `TlsUnavailableError`, wenn das nicht geht -
    `prepare_tls` faengt das ab, damit der Dienst trotzdem startet."""
    if not CRYPTOGRAPHY_AVAILABLE:
        raise TlsUnavailableError(
            "das Paket cryptography ist nicht installiert - ohne es laesst sich "
            "kein Zertifikat erzeugen"
        )
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TlsUnavailableError(
            f"das Verzeichnis {tls_dir} konnte nicht angelegt werden: {exc}"
        ) from exc

    addresses = local_ipv4_addresses()
    try:
        ca_certificate, ca_key = _ensure_ca(tls_dir)
        certificate_path = tls_dir / SERVER_FILE
        key_path = tls_dir / SERVER_KEY_FILE
        # Explizit annotiert: sonst leitet mypy den Typ aus dem ersten
        # Zweig als "Certificate" (nicht optional) ab und meldet die
        # Zuweisung im else-Zweig, die ueber `_load_certificate` faellt,
        # als Fehler - obwohl der `assert` direkt danach dasselbe belegt.
        certificate: x509.Certificate
        if not _server_is_current(certificate_path, ca_certificate, addresses):
            key = _new_key()
            certificate = _build_server_certificate(ca_certificate, ca_key, key, addresses)
            certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            _write_private(key_path, _key_bytes(key))
            logger.info("Server-Zertifikat erzeugt fuer %s", ", ".join(["localhost", *addresses]))
        else:
            loaded = _load_certificate(certificate_path)
            assert loaded is not None  # `_server_is_current` hat es eben gelesen
            certificate = loaded
    except OSError as exc:
        raise TlsUnavailableError(
            f"das Zertifikat in {tls_dir} konnte nicht geschrieben werden: {exc}"
        ) from exc

    return TlsMaterial(
        ca_certificate=tls_dir / CA_FILE,
        certificate=certificate_path,
        private_key=key_path,
        addresses=tuple(addresses),
        not_valid_after=certificate.not_valid_after_utc,
    )


def prepare_tls(tls_dir: Path, port: int) -> TlsState:
    """Der eine Einstiegspunkt fuer `cli`. Wirft nie.

    TLS ist eine Zugabe; es darf die Loxone-Strecke nicht zum Stehen
    bringen (Entwurf 4.5). Jeder Fehler wird zu einer Warnung im Log und zu
    einem `TlsState` ohne Material - der Dienst laeuft dann wie bisher nur
    ueber HTTP, und `GET /api/diagnostics/tls` nennt den Grund."""
    if port == 0:
        return TlsState(material=None, port=None, error=None)
    try:
        material = ensure_tls_material(tls_dir)
    except TlsUnavailableError as exc:
        logger.warning(
            "HTTPS nicht verfuegbar: %s. Der Dienst laeuft nur ueber HTTP - "
            "der QR-Scan mit der Kamera bleibt dadurch gesperrt, der Weg "
            "ueber ein Foto funktioniert weiterhin.",
            exc,
        )
        return TlsState(material=None, port=None, error=str(exc))
    return TlsState(material=material, port=port, error=None)
