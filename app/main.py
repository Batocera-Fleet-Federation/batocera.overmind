from datetime import datetime, timezone
from pathlib import Path
import sys

if __package__ in (None, ""):
    # Allow running as: python3 app/main.py
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    from overmind.main import app, ensure_self_signed_cert  # type: ignore
else:
    from overmind.main import app, ensure_self_signed_cert


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


class _TimestampedStream:
    """Wraps a stream to prefix each written line with an ISO-8601 timestamp."""
    def __init__(self, original_stream):
        self._original_stream = original_stream
        self._partial = ""

    def write(self, data: str) -> int:
        if not isinstance(data, str):
            data = str(data)
        self._partial += data
        lines = self._partial.split("\n")
        complete = lines[:-1]
        self._partial = lines[-1]
        for line in complete:
            ts = _ts()
            self._original_stream.write(f"[{ts}] {line}\n")
        return len(data)

    def flush(self) -> None:
        self._original_stream.flush()

    def isatty(self) -> bool:
        return self._original_stream.isatty()


def _configure_timestamped_logging() -> None:
    sys.stdout = _TimestampedStream(sys.stdout)
    sys.stderr = _TimestampedStream(sys.stderr)


def main() -> None:
    import uvicorn

    _configure_timestamped_logging()

    print(f"Starting Batocera Overmind")
    print(f"Logging timestamped output to stdout/stderr")

    key_file, cert_file = ensure_self_signed_cert()
    kwargs = {
        "host": "0.0.0.0",
        "port": 8000,
        "log_config": {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "format": "[%(asctime)s] %(levelprefix)s %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                    "use_colors": None,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "format": "[%(asctime)s] %(levelprefix)s %(client_addr)s - \"%(request_line)s\" %(status_code)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"level": "INFO", "handlers": ["default"]},
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        },
    }
    if key_file and cert_file:
        kwargs["ssl_keyfile"] = str(key_file)
        kwargs["ssl_certfile"] = str(cert_file)

    print(f"API Documentation: http://localhost:{kwargs.get('port', 8000)}/docs")
    print(f"UI: http://localhost:{kwargs.get('port', 8000)}/")
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
