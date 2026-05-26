"""TLS certificate bootstrap and HTTPS server launch support."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple


CertificateLoader = Callable[[], Tuple[Optional[Path], Optional[Path]]]


def tls_file_pair_usable(key_file: Path, cert_file: Path) -> bool:
    if not key_file.exists() or not cert_file.exists():
        return False
    checks = [
        ["openssl", "x509", "-in", str(cert_file), "-noout"],
        ["openssl", "pkey", "-in", str(key_file), "-noout"],
    ]
    try:
        for cmd in checks:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def ensure_self_signed_cert() -> Tuple[Optional[Path], Optional[Path]]:
    """Create a self-signed TLS certificate unless the configured pair is usable."""
    cert_dir = Path(os.getenv("TLS_SELF_SIGNED_DIR", "./local-data/certs")).expanduser()
    cert_dir.mkdir(parents=True, exist_ok=True)

    key_file = cert_dir / "server.key"
    cert_file = cert_dir / "server.crt"
    if tls_file_pair_usable(key_file, cert_file):
        return key_file, cert_file

    cmd = [
        "openssl",
        "req",
        "-x509",
        "-nodes",
        "-days",
        "3650",
        "-newkey",
        "rsa:2048",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-subj",
        "/CN=localhost",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return key_file, cert_file
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Unable to create self-signed certificate: {exc}")
        return None, None


def run_https_app(app, *, certificate_loader: CertificateLoader = ensure_self_signed_cert) -> None:
    """Run Overmind with HTTPS, creating a self-signed cert when needed."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Run Batocera Overmind")
    parser.add_argument("--host", default=os.getenv("OVERMIND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OVERMIND_PORT", "8443")))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("OVERMIND_RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    parser.add_argument("--ssl-keyfile", default=None)
    parser.add_argument("--ssl-certfile", default=None)
    args = parser.parse_args()

    ssl_keyfile = args.ssl_keyfile
    ssl_certfile = args.ssl_certfile

    if not ssl_keyfile and not ssl_certfile:
        generated_keyfile, generated_certfile = certificate_loader()
        ssl_keyfile = str(generated_keyfile) if generated_keyfile else None
        ssl_certfile = str(generated_certfile) if generated_certfile else None
        if ssl_keyfile and ssl_certfile:
            print(f"Using self-signed TLS certificate: {ssl_certfile}")
    elif not ssl_keyfile or not ssl_certfile:
        raise RuntimeError("Both --ssl-keyfile and --ssl-certfile must be specified together.")
    elif not tls_file_pair_usable(Path(ssl_keyfile), Path(ssl_certfile)):
        print(f"Configured TLS certificate/key are unusable; manufacturing self-signed certificate instead: {ssl_certfile}")
        generated_keyfile, generated_certfile = certificate_loader()
        ssl_keyfile = str(generated_keyfile) if generated_keyfile else None
        ssl_certfile = str(generated_certfile) if generated_certfile else None

    if not ssl_keyfile or not ssl_certfile:
        raise RuntimeError("HTTPS is required, but no TLS certificate/key could be loaded or generated.")

    uvicorn.run(
        "overmind.main:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
