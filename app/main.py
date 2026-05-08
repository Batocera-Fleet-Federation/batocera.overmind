from pathlib import Path
import sys

if __package__ in (None, ""):
    # Allow running as: python3 app/main.py
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    from overmind.main import app, ensure_self_signed_cert  # type: ignore
else:
    from overmind.main import app, ensure_self_signed_cert


def main() -> None:
    import uvicorn

    key_file, cert_file = ensure_self_signed_cert()
    kwargs = {"host": "0.0.0.0", "port": 8000}
    if key_file and cert_file:
        kwargs["ssl_keyfile"] = str(key_file)
        kwargs["ssl_certfile"] = str(cert_file)
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
