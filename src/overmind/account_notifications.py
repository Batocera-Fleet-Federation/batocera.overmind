"""Email delivery for account verification, password resets, and invitations."""

from __future__ import annotations

import urllib.parse
from typing import Any


def send_verification_email(
    user: dict,
    code: str,
    token: str,
    *,
    email_client: Any,
    ttl_minutes: int,
) -> None:
    link = f"{email_client.base_url()}/api/auth/verify-email?token={urllib.parse.quote(token)}"
    body_html = email_client.render_email_template(
        "registration_verification.html",
        {"code": code, "verification_link": link, "ttl_minutes": ttl_minutes},
    )
    html_body, text_body = email_client.themed_email(
        "Verify your Overmind account",
        body_html,
        f"Your Overmind validation code is {code}.\nVerify here: {link}\nThis code expires in {ttl_minutes} minutes.",
    )
    email_client.send_email(user["email"], "Verify your Batocera Overmind account", html_body, text_body)


def send_password_reset_email(
    user: dict,
    token: str,
    *,
    email_client: Any,
    ttl_minutes: int,
) -> None:
    link = f"{email_client.base_url()}/#reset-password={urllib.parse.quote(token)}"
    body_html = email_client.render_email_template(
        "password_reset.html",
        {"reset_link": link, "ttl_minutes": ttl_minutes},
    )
    html_body, text_body = email_client.themed_email(
        "Reset your Overmind password",
        body_html,
        f"Reset your Overmind password: {link}\nThis link expires in {ttl_minutes} minutes.",
    )
    email_client.send_email(user["email"], "Reset your Batocera Overmind password", html_body, text_body)


def send_invitation_email(email: str, swarm: dict, role: str, token: str, *, email_client: Any) -> None:
    link = f"{email_client.base_url()}/#invite={urllib.parse.quote(token)}"
    body_html = email_client.render_email_template(
        "swarm_invitation.html",
        {"swarm_name": swarm.get("name"), "role": role, "invitation_link": link},
    )
    html_body, text_body = email_client.themed_email(
        "You were invited to a Batocera swarm",
        body_html,
        f"You were invited to {swarm.get('name')} as {role}.\nAccept: {link}",
    )
    email_client.send_email(email, "Batocera Overmind swarm invitation", html_body, text_body)
