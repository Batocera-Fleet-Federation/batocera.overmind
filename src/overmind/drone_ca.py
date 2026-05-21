"""Small local CA for signing approved Drone mTLS certificates."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


CA_DIR = Path(os.getenv("OVERMIND_CA_DIR", "./local-data/ca")).expanduser()
CA_KEY_FILE = CA_DIR / "overmind-ca.key"
CA_CERT_FILE = CA_DIR / "overmind-ca.crt"


def _load_or_create_ca():
    CA_DIR.mkdir(parents=True, exist_ok=True)
    if CA_KEY_FILE.exists() and CA_CERT_FILE.exists():
        key = serialization.load_pem_private_key(CA_KEY_FILE.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(CA_CERT_FILE.read_bytes())
        return key, cert

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Batocera Overmind Drone CA"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False, data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    CA_KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    CA_CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(CA_KEY_FILE, 0o600)
    os.chmod(CA_CERT_FILE, 0o644)
    return key, cert


def sign_drone_csr(csr_pem: str, device_id: str, days: int = 365) -> dict:
    ca_key, ca_cert = _load_or_create_ca()
    csr = x509.load_pem_x509_csr(csr_pem.encode("utf-8"))
    subject = csr.subject or x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"batocera-drone-{device_id}")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=max(1, int(days))))
    )
    for extension in csr.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    cert = builder.sign(ca_key, hashes.SHA256())
    return {
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"),
        "ca_certificate_pem": ca_cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"),
        "serial_number": str(cert.serial_number),
    }
