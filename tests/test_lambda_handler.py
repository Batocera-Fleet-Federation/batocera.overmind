import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

fake_mangum = types.ModuleType("mangum")


class FakeMangum:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, event, context):
        return {"statusCode": 200, "body": ""}


fake_mangum.Mangum = FakeMangum
sys.modules.setdefault("mangum", fake_mangum)

from overmind import lambda_handler


def test_scheduled_handler_returns_skipped_when_runtime_startup_fails(monkeypatch):
    def fail_startup(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(lambda_handler, "initialize_runtime", fail_startup)

    result = lambda_handler.scheduled_handler({"job": "device-status"}, None)

    assert result == {
        "job": "device-status",
        "status": "skipped",
        "error_type": "RuntimeError",
        "detail": "database unavailable",
    }


def test_scheduled_handler_runs_requested_job(monkeypatch):
    monkeypatch.setattr(lambda_handler, "initialize_runtime", lambda **kwargs: None)
    monkeypatch.setattr(lambda_handler, "run_scheduled_job", lambda job: {"job": job, "status": "ok"})

    result = lambda_handler.scheduled_handler({"detail": {"job": "notification-delivery"}}, None)

    assert result == {"job": "notification-delivery", "status": "ok"}


def test_handler_skips_runtime_startup_for_public_health(monkeypatch):
    called = {"startup": False}

    def fail_if_called(**kwargs):
        called["startup"] = True
        raise AssertionError("startup should be skipped")

    monkeypatch.setattr(lambda_handler, "initialize_runtime", fail_if_called)
    monkeypatch.setattr(lambda_handler, "_adapter", lambda event, context: {"statusCode": 200})

    result = lambda_handler.handler({"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}}, None)

    assert result == {"statusCode": 200}
    assert called["startup"] is False


def test_handler_initializes_runtime_for_api_routes(monkeypatch):
    called = {"startup": False}

    def initialize(**kwargs):
        called["startup"] = True

    monkeypatch.setattr(lambda_handler, "initialize_runtime", initialize)
    monkeypatch.setattr(lambda_handler, "_adapter", lambda event, context: {"statusCode": 200})

    result = lambda_handler.handler({"rawPath": "/api/devices", "requestContext": {"http": {"method": "GET"}}}, None)

    assert result == {"statusCode": 200}
    assert called["startup"] is True
