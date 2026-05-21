"""Outbound email helpers for Overmind."""

import logging
import os
from html import escape
from pathlib import Path
from typing import Optional

logger = logging.getLogger("overmind.email")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "emails"


def base_url() -> str:
    return (os.getenv("BASE_URL") or os.getenv("OAUTH_REDIRECT_BASE_URL") or "http://localhost:8000").rstrip("/")


def provider() -> str:
    return (os.getenv("EMAIL_PROVIDER") or "console").strip().lower()


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
    if selected != "ses":
        logger.warning("Unknown EMAIL_PROVIDER=%s; email not sent", selected)
        return False

    sender = from_email or os.getenv("SES_FROM_EMAIL")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not sender or not region:
        logger.error("SES email not sent; SES_FROM_EMAIL and AWS_REGION are required")
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
        logger.info("SES email sent to %s subject=%s", to_email, subject)
        return True
    except Exception as error:
        logger.error("SES email send failed to %s subject=%s error=%s", to_email, subject, error.__class__.__name__)
        return False
