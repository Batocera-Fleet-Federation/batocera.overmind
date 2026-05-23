"""Focused tests for runtime config, email sender formatting, and UI session hooks."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind import emailer
from overmind.runtime_secrets import RuntimeSecretRefresher


class FakeSecretsClient:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error

    def get_secret_value(self, SecretId):
        if self.error:
            raise self.error
        return self.responses.pop(0)


def test_email_sender_header_without_display_name(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")
    monkeypatch.delenv("EMAIL_FROM_DISPLAY_NAME", raising=False)

    assert emailer.sender_header() == "noreply@example.com"


def test_email_sender_header_with_display_name(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("EMAIL_FROM_DISPLAY_NAME", "Batocera Overmind")

    assert emailer.sender_header() == "Batocera Overmind <noreply@example.com>"


def test_smtp_from_header_uses_display_name(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, username, password):
            return None

        def send_message(self, message):
            sent["from"] = message["From"]

    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("EMAIL_FROM_DISPLAY_NAME", "Batocera Overmind")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "noreply@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)

    assert emailer.send_email("user@example.com", "Subject", "<p>Hello</p>", "Hello") is True
    assert sent["from"] == "Batocera Overmind <noreply@example.com>"


def test_env_only_config_is_unchanged_when_secret_missing(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "from-env")
    refresher = RuntimeSecretRefresher(client=FakeSecretsClient(error=RuntimeError("not found")))

    assert refresher.refresh_once() is False
    assert refresher._last_good_values == {}
    assert os.environ["SMTP_PASSWORD"] == "from-env"


def test_secret_overrides_environment(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "from-env")
    client = FakeSecretsClient([
        {"VersionId": "v1", "SecretString": json.dumps({"SMTP_PASSWORD": "from-secret", "EMAIL_FROM": "noreply@example.com"})}
    ])
    refresher = RuntimeSecretRefresher(client=client)

    assert refresher.refresh_once() is True
    assert os.environ["SMTP_PASSWORD"] == "from-secret"
    assert os.environ["EMAIL_FROM"] == "noreply@example.com"


def test_empty_secret_fallback_keeps_existing_env(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "from-env")
    client = FakeSecretsClient([{"VersionId": "v1", "SecretString": ""}])
    refresher = RuntimeSecretRefresher(client=client)

    assert refresher.refresh_once() is True
    assert os.environ["SMTP_PASSWORD"] == "from-env"


def test_secret_update_detection_and_refresh(monkeypatch):
    client = FakeSecretsClient([
        {"VersionId": "v1", "SecretString": json.dumps({"SMTP_PASSWORD": "first"})},
        {"VersionId": "v1", "SecretString": json.dumps({"SMTP_PASSWORD": "first"})},
        {"VersionId": "v2", "SecretString": json.dumps({"SMTP_PASSWORD": "second"})},
    ])
    refresher = RuntimeSecretRefresher(client=client)

    assert refresher.refresh_once() is True
    assert os.environ["SMTP_PASSWORD"] == "first"
    assert refresher.refresh_once() is False
    assert refresher.refresh_once() is True
    assert os.environ["SMTP_PASSWORD"] == "second"


def test_secret_refresh_failure_keeps_last_known_good(monkeypatch):
    good_client = FakeSecretsClient([{"VersionId": "v1", "SecretString": json.dumps({"SMTP_PASSWORD": "good"})}])
    refresher = RuntimeSecretRefresher(client=good_client)
    assert refresher.refresh_once() is True

    refresher.client = FakeSecretsClient(error=RuntimeError("boom"))
    assert refresher.refresh_once() is False
    assert refresher._last_good_values == {"SMTP_PASSWORD": "good"}
    assert os.environ["SMTP_PASSWORD"] == "good"


def test_overmind_ui_contains_inactivity_timeout_hooks():
    html = (Path(__file__).resolve().parents[1] / "src" / "overmind" / "templates" / "index.html").read_text(encoding="utf-8")

    assert "const INACTIVITY_TIMEOUT_MS = 5 * 60 * 1000" in html
    assert "function markUserActivity()" in html
    assert "resetInactivityTimer()" in html
    assert "/api/auth/refresh" in html
    assert "You were logged out after 5 minutes of inactivity." in html
