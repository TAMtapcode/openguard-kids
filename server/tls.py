"""Tự tạo và quản lý chứng chỉ TLS của máy chủ (người dùng không phải biết gì về chứng chỉ)."""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def local_ips() -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # không gửi gói nào, chỉ để hệ điều hành chọn card mạng đang dùng
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4][0] not in ips and not info[4][0].startswith("127."):
                ips.append(info[4][0])
    except OSError:
        pass
    return ips


def make_cert(hosts, ips, out: Path, days=3650) -> str:
    out.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0] if hosts else "ogk-server")])
    san = [x509.DNSName(h) for h in hosts] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (out / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (out / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    try:
        (out / "server.key").chmod(0o600)
    except OSError:
        pass
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def fingerprint_of_pem(path: Path) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(path).read_text())).hexdigest()


def ensure_cert(cert_dir: Path) -> tuple[Path, Path, str]:
    """Chỉ tạo lần đầu; giữ nguyên về sau để mã ghim của các máy con không đổi."""
    cert, key = cert_dir / "server.pem", cert_dir / "server.key"
    if not (cert.exists() and key.exists()):
        make_cert([socket.gethostname(), "localhost"], ["127.0.0.1"] + local_ips(), cert_dir)
    return cert, key, fingerprint_of_pem(cert)
