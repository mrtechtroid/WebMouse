"""Optional HTTPS with an auto-generated self-signed certificate."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import os
import socket
import ssl
from pathlib import Path

from .config import Config, ConfigError, user_config_dir
from .net import lan_addresses

log = logging.getLogger("webmouse.tls")


def _san_names() -> tuple[list[str], list[str]]:
    ips = {"127.0.0.1", *lan_addresses()}
    dns = {"localhost"}
    try:
        dns.add(socket.gethostname())
    except OSError:
        pass
    return sorted(ips), sorted(dns)


def _generate(cert_path: Path, key_path: Path) -> None:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise ConfigError(
            "HTTPS needs the 'cryptography' package: pip install \"webmouse[tls]\""
        ) from None

    ips, dns = _san_names()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webmouse")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("generated self-signed certificate %s", cert_path)


def _still_valid(cert_path: Path) -> bool:
    """Reuse the stored certificate only if it is not expiring and covers our IPs."""
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        left = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else \
            cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        if left - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(days=30):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        return set(_san_names()[0]) <= have
    except Exception:  # noqa: BLE001
        return False


def make_context(cfg: Config) -> ssl.SSLContext:
    if cfg.tls.cert_file and cfg.tls.key_file:
        cert_path, key_path = Path(cfg.tls.cert_file), Path(cfg.tls.key_file)
        if not (cert_path.is_file() and key_path.is_file()):
            raise ConfigError("[tls] cert_file / key_file not found")
    else:
        folder = user_config_dir() / "tls"
        cert_path, key_path = folder / "cert.pem", folder / "key.pem"
        if not (cert_path.is_file() and key_path.is_file() and _still_valid(cert_path)):
            _generate(cert_path, key_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx
