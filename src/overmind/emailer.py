"""Outbound email helpers for Overmind."""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Optional

logger = logging.getLogger("overmind.email")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "emails"


def base_url() -> str:
    return (os.getenv("BASE_URL") or os.getenv("OAUTH_REDIRECT_BASE_URL") or "http://localhost:8000").rstrip("/")


def provider() -> str:
    """Determine the email provider."""
    explicit = os.getenv("EMAIL_PROVIDER", "").strip().lower()
    if explicit:
        return "smtp" if explicit == "purelymail" else explicit
    if os.getenv("SMTP_HOST"):
        return "smtp"
    environment = (os.getenv("OVERMIND_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "").lower()
    if environment in {"prod", "production"} and os.getenv("AWS_REGION"):
        return "ses"
    return "console"


def themed_email(title: str, body_html: str, body_text: str) -> tuple[str, str]:
    html = render_email_template("base.html", {"title": title, "body_html": body_html}, html_keys={"body_html"})
    return html, body_text


def render_email_template(name: str, context: dict, html_keys: Optional[set[str]] = None) -> str:
    """Render a small HTML email template without adding another template dependency."""
    html_keys = html_keys or set()
    safe_context = {
        key: str(value) if key in html_keys else escape(str(value))
        for key, value in context.items()
    }
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8").format_map(safe_context)


def send_email(to_email: str, subject: str, html_body: str, text_body: str, from_email: Optional[str] = None) -> bool:
    selected = provider()
    if selected == "disabled":
        logger.info("Email disabled; skipped message to %s subject=%s", to_email, subject)
        return False
    if selected == "console":
        logger.info("Console email to=%s subject=%s\n%s", to_email, subject, text_body)
        return True
    if selected == "smtp":
        return _send_smtp_email(to_email, subject, html_body, text_body, from_email=from_email)
    if selected == "ses":
        return _send_ses_email(to_email, subject, html_body, text_body, from_email=from_email)
    logger.warning("Unknown EMAIL_PROVIDER=%s; email not sent", selected)
    return False


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _sender(from_email: Optional[str] = None) -> Optional[str]:
    return from_email or os.getenv("EMAIL_FROM") or os.getenv("SMTP_FROM") or os.getenv("AWS_SES_FROM_ADDRESS") or os.getenv("SES_FROM_EMAIL")


def _send_smtp_email(to_email: str, subject: str, html_body: str, text_body: str, from_email: Optional[str] = None) -> bool:
    sender = _sender(from_email)
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = _env_bool("SMTP_USE_SSL", _env_bool("SMTP_SSL", port == 465))
    use_starttls = _env_bool("SMTP_STARTTLS", not use_ssl)

    if use_ssl and use_starttls:
        logger.warning("SMTP_USE_SSL and SMTP_STARTTLS are both enabled; disabling STARTTLS because SMTP SSL is enabled")
        use_starttls = False

    missing = [
        name for name, value in {
            "EMAIL_FROM": sender,
            "SMTP_HOST": host,
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        logger.error("SMTP email not sent to %s: missing environment variable(s): %s", to_email, ", ".join(missing))
        return False

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as client:
                client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as client:
                if use_starttls:
                    client.starttls(context=ssl.create_default_context())
                client.login(username, password)
                client.send_message(message)
        logger.info(
            "SMTP email sent to %s subject=%s from=%s host=%s port=%s ssl=%s starttls=%s",
            to_email,
            subject,
            sender,
            host,
            port,
            use_ssl,
            use_starttls,
        )
        return True
    except Exception as error:
        logger.error(
            "SMTP email send failed to %s subject=%s from=%s host=%s port=%s ssl=%s starttls=%s error=%s: %s",
            to_email,
            subject,
            sender,
            host,
            port,
            use_ssl,
            use_starttls,
            error.__class__.__name__,
            str(error),
        )
        return False


def _send_ses_email(to_email: str, subject: str, html_body: str, text_body: str, from_email: Optional[str] = None) -> bool:
    sender = _sender(from_email)
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    if not sender:
        logger.error("SES email not sent to %s: EMAIL_FROM or AWS_SES_FROM_ADDRESS env var is required.", to_email)
        return False
    if not region:
        logger.error("SES email not sent to %s: AWS_REGION env var is required for AWS SES.", to_email)
        return False

    try:
        import boto3  # type: ignore

        client = boto3.client("ses", region_name=region)
        client.send_email(
            Source=sender,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("SES email sent to %s subject=%s from=%s region=%s", to_email, subject, sender, region)
        return True
    except Exception as error:
        logger.error(
            "SES email send failed to %s subject=%s from=%s region=%s error=%s: %s",
            to_email,
            subject,
            sender,
            region,
            error.__class__.__name__,
            str(error),
        )
        return False
