"""Focused tests for runtime config, email sender formatting, and UI session hooks."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind import emailer
from overmind import main as overmind_main
from overmind.postgres_store import PostgresMetadataStore
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


def test_ui_session_refreshes_while_active_and_times_out_after_30_minutes():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000;" in js
    assert "const AUTH_REFRESH_INTERVAL_MS = 2 * 60 * 1000;" in js
    assert "maybeRefreshAuthToken();" in js
    assert "You were logged out after 30 minutes of inactivity." in js


def test_ui_notifications_nav_and_polling_hooks():
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = root.joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert 'id="notification-button"' in html
    assert 'id="notification-badge"' in html
    assert 'id="notification-panel"' in html
    assert 'View All' in html
    assert 'id="notifications-tab"' in html
    assert "/api/notifications" in js
    assert "/api/notifications/dismiss" in js
    assert "99+" in js
    assert "setupNotificationMenu();" in js
    assert "closeNotificationsPanel()" in js
    assert "loadNotificationsPage" in js
    assert "Dismiss All Notifications" in html
    assert "startNotificationPolling();" in js


def test_profile_notification_preferences_match_created_event_types():
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = root.joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert 'id="notify-email-address"' not in html
    assert "registered account email" in html
    assert 'id="notify-slack"' in html
    assert 'id="notify-discord"' in html
    assert 'id="notify-slack-webhook"' in html
    assert 'id="notify-discord-webhook"' in html
    for notification_type in (
        "master_rom",
        "master_bios",
        "master_artwork",
        "drone_status",
        "drone_membership",
        "sync_triggered",
        "device_action",
    ):
        assert f'data-notify-type="{notification_type}"' in html
    for old_event_type in ("master_rom_added", "drone_offline", "drone_added"):
        assert f'data-notify-type="{old_event_type}"' not in html
    assert "selectedTypes[input.dataset.notifyType]" in js


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


def test_lambda_runtime_detection(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("OVERMIND_RUNTIME", raising=False)
    assert overmind_main.is_lambda_runtime() is False

    monkeypatch.setenv("OVERMIND_RUNTIME", "lambda")
    assert overmind_main.is_lambda_runtime() is True

    monkeypatch.setenv("OVERMIND_RUNTIME", "")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "bff-overmind-prod-low")
    assert overmind_main.is_lambda_runtime() is True


def test_postgres_store_refreshes_after_runtime_secret(monkeypatch):
    monkeypatch.delenv("OVERMIND_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OVERMIND_POSTGRES_HOST", raising=False)
    store = PostgresMetadataStore()
    assert store.url is None

    monkeypatch.setenv("OVERMIND_POSTGRES_HOST", "db.example.internal")
    monkeypatch.setenv("OVERMIND_POSTGRES_USER", "overmind")
    monkeypatch.setenv("OVERMIND_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("OVERMIND_POSTGRES_DB", "overmind")
    store.refresh_from_environment()

    assert store.url == "postgresql://overmind:secret@db.example.internal:5432/overmind"


def test_runtime_config_can_override_postgres_host_for_lambda(monkeypatch):
    monkeypatch.setenv("OVERMIND_POSTGRES_HOST", "rds-direct.example.internal")
    monkeypatch.setenv("OVERMIND_POSTGRES_HOST_OVERRIDE", "rds-proxy.example.internal")

    overmind_main.apply_runtime_config_side_effects({})

    assert os.environ["OVERMIND_POSTGRES_HOST"] == "rds-proxy.example.internal"


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
    js = (Path(__file__).resolve().parents[1] / "src" / "overmind" / "static" / "js" / "overmind.js").read_text(encoding="utf-8")

    assert "const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000" in js
    assert "function markUserActivity()" in js
    assert "resetInactivityTimer()" in js
    assert "/api/auth/refresh" in js
    assert "You were logged out after 30 minutes of inactivity." in js
