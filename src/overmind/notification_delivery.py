"""Outbound delivery for Overmind swarm notifications."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from typing import Any, Optional

from overmind import emailer

logger = logging.getLogger("overmind.notifications")

NOTIFICATION_EVENT_TYPES = {
    "master_rom_added": "New ROM added to master list",
    "master_rom_removed": "ROM removed from master list",
    "master_bios_added": "New BIOS added to master list",
    "master_bios_removed": "BIOS removed from master list",
    "master_artwork_added": "New artwork added to master list",
    "master_artwork_removed": "Artwork removed from master list",
    "drone_online": "Drone came online",
    "drone_offline": "Drone went offline",
    "drone_added": "Drone added to swarm",
    "drone_removed": "Drone removed from swarm",
    "sync_triggered": "Sync triggered",
    "device_action_completed": "Remote action completed",
    "device_action_failed": "Remote action failed",
}

NOTIFICATION_TYPE_GROUPS = {
    "master_rom": ("master_rom_added", "master_rom_removed"),
    "master_bios": ("master_bios_added", "master_bios_removed"),
    "master_artwork": ("master_artwork_added", "master_artwork_removed"),
    "drone_status": ("drone_online", "drone_offline"),
    "drone_membership": ("drone_added", "drone_removed"),
    "sync_triggered": ("sync_triggered",),
    "device_action": ("device_action_completed", "device_action_failed"),
}

EVENT_TYPE_TO_SETTING = {
    event_type: setting
    for setting, event_types in NOTIFICATION_TYPE_GROUPS.items()
    for event_type in event_types
}

DEFAULT_NOTIFICATION_TYPES = {setting: True for setting in NOTIFICATION_TYPE_GROUPS}
REALTIME_EVENT_TYPES = {"drone_online", "drone_offline"}

_TEMPLATE_BY_EVENT_TYPE = {
    "master_rom_added": "notification_master_asset_added.html",
    "master_bios_added": "notification_master_asset_added.html",
    "master_artwork_added": "notification_master_asset_added.html",
    "master_rom_removed": "notification_master_asset_removed.html",
    "master_bios_removed": "notification_master_asset_removed.html",
    "master_artwork_removed": "notification_master_asset_removed.html",
    "drone_online": "notification_drone_status.html",
    "drone_offline": "notification_drone_status.html",
    "drone_added": "notification_drone_membership.html",
    "drone_removed": "notification_drone_membership.html",
    "sync_triggered": "notification_sync_triggered.html",
    "device_action_completed": "notification_device_action.html",
    "device_action_failed": "notification_device_action.html",
}


def _refresh(data_store: Any) -> None:
    refresh = getattr(data_store, "refresh_persistent_state", None)
    if callable(refresh):
        refresh()


def deliver_notification(data_store: Any, notification: dict, *, email_client: Any = emailer) -> None:
    """Deliver a stored swarm notification to configured member channels."""
    _refresh(data_store)
    swarm_id = notification.get("swarm_id")
    swarm = data_store.swarms.get(swarm_id) or {}
    actor = data_store.get_user(str(notification.get("actor_user_id") or "")) if notification.get("actor_user_id") else None
    for user_id in data_store.swarm_memberships.get(swarm_id, {}):
        user = data_store.get_user(user_id)
        if not user:
            continue
        for channel, sender in (
            ("email", lambda: send_email_notification(user, notification, swarm, actor, email_client=email_client)),
            ("slack", lambda: send_slack_notification(user, notification, swarm, actor)),
            ("discord", lambda: send_discord_notification(user, notification, swarm, actor)),
        ):
            if not should_notify_user(user, str(notification.get("event_type") or ""), channel):
                continue
            try:
                sender()
            except Exception as error:
                logger.error(
                    "Notification delivery failed channel=%s user_id=%s notification_id=%s error=%s: %s",
                    channel,
                    user_id,
                    notification.get("id"),
                    error.__class__.__name__,
                    str(error),
                )


def deliver_pending_notifications(data_store: Any, *, email_client: Any = emailer, limit: int = 0) -> int:
    """Deliver one digest per enabled user/channel for all queued events."""
    _refresh(data_store)
    # In stateless runtimes the in-memory store is empty; load pending
    # notifications (and recipients) straight from the database.
    hydrate = getattr(data_store, "hydrate_pending_notifications_from_store", None)
    if callable(hydrate):
        hydrate()
    limit = max(0, int(limit or 0))
    pending = [
        (notification, data_store.swarms.get(swarm_id) or {})
        for swarm_id, notifications in list(data_store.notifications.items())
        for notification in notifications
        if notification.get("delivery_pending") is True and not notification.get("delivery_completed_at")
    ]
    pending.sort(key=lambda item: str(item[0].get("created_at") or ""))
    claim_pending = getattr(data_store, "claim_pending_notifications_for_delivery", None)
    if callable(claim_pending):
        claimed_ids = claim_pending([str(item[0].get("id") or "") for item in pending], limit=limit)
        if claimed_ids is not None:
            pending = [item for item in pending if item[0].get("id") in claimed_ids]
        elif limit:
            pending = pending[:limit]
    elif limit:
        pending = pending[:limit]
    if not pending:
        logger.info("Notification digest delivery: no pending notifications")
        return 0

    sent_count = 0
    for user_id, user in list(data_store.users.items()):
        items = [
            (notification, swarm)
            for notification, swarm in pending
            if user_id in data_store.swarm_memberships.get(notification.get("swarm_id"), {})
        ]
        if not items:
            continue
        for channel, sender in (
            ("email", lambda selected: send_email_digest(user, selected, email_client=email_client)),
            ("slack", lambda selected: send_slack_digest(user, selected)),
            ("discord", lambda selected: send_discord_digest(user, selected)),
        ):
            selected = [
                item for item in items
                if should_notify_user(user, str(item[0].get("event_type") or ""), channel)
            ]
            if not selected:
                continue
            try:
                if sender(selected):
                    sent_count += 1
            except Exception as error:
                logger.error(
                    "Notification digest delivery failed channel=%s user_id=%s error=%s: %s",
                    channel,
                    user_id,
                    error.__class__.__name__,
                    str(error),
                )

    completed_at = datetime.utcnow()
    for notification, _ in pending:
        notification["delivery_pending"] = False
        notification.setdefault("delivery_completed_at", completed_at)
    for notifications in data_store.notifications.values():
        del notifications[:-500]
    persist = getattr(data_store, "_persist_state", None)
    if callable(persist):
        persist()
    logger.info("Notification digest delivery: pending=%d digests_sent=%d", len(pending), sent_count)
    return sent_count


def should_notify_user(user: dict, event_type: str, channel: str) -> bool:
    settings = user.get("notification_settings") if isinstance(user.get("notification_settings"), dict) else {}
    channel_key = f"notify_{channel}"
    if not settings.get(channel_key):
        return False
    if channel in {"slack", "discord"} and not str(settings.get(f"{channel}_webhook") or "").strip():
        return False
    return notification_type_enabled(settings, event_type)


def should_email_user(user: dict, event_type: str) -> bool:
    return should_notify_user(user, event_type, "email")


def notification_type_enabled(settings: dict, event_type: str) -> bool:
    types = settings.get("types") if isinstance(settings.get("types"), dict) else {}
    setting = EVENT_TYPE_TO_SETTING.get(event_type, event_type)
    if setting in types:
        return bool(types.get(setting))
    if event_type in types:
        return bool(types.get(event_type))
    return bool(DEFAULT_NOTIFICATION_TYPES.get(setting, False))


def send_email_notification(
    user: dict,
    notification: dict,
    swarm: Optional[dict],
    actor: Optional[dict],
    *,
    email_client: Any,
) -> bool:
    event_type = str(notification.get("event_type") or "")
    template_name = _TEMPLATE_BY_EVENT_TYPE.get(event_type)
    to_email = str(user.get("email") or "").strip()
    if not template_name or not to_email:
        return False

    context = _notification_context(notification, swarm, actor)
    body_html = email_client.render_email_template(template_name, context, html_keys={"devices_html", "targets_html", "sources_html"})
    html_body, text_body = email_client.themed_email(
        str(notification.get("title") or "Swarm notification"),
        body_html,
        _notification_text(notification, context),
    )
    return email_client.send_email(to_email, f"Batocera Overmind: {notification.get('title') or 'Swarm notification'}", html_body, text_body)


def send_slack_notification(user: dict, notification: dict, swarm: Optional[dict], actor: Optional[dict]) -> bool:
    webhook = str((user.get("notification_settings") or {}).get("slack_webhook") or "").strip()
    if not webhook:
        return False
    context = _notification_context(notification, swarm, actor)
    payload = {
        "text": _webhook_text(notification, context),
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": str(notification.get("title") or "Swarm notification")[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": _webhook_text(notification, context)}},
        ],
    }
    return post_webhook(webhook, payload)


def send_discord_notification(user: dict, notification: dict, swarm: Optional[dict], actor: Optional[dict]) -> bool:
    webhook = str((user.get("notification_settings") or {}).get("discord_webhook") or "").strip()
    if not webhook:
        return False
    context = _notification_context(notification, swarm, actor)
    payload = {
        "username": "Batocera Overmind",
        "embeds": [
            {
                "title": str(notification.get("title") or "Swarm notification")[:256],
                "description": str(notification.get("message") or ""),
                "color": 5025535,
                "fields": [
                    {"name": "Swarm", "value": str(context["swarm_name"])[:1024], "inline": True},
                    {"name": "Event", "value": str(context["event_label"])[:1024], "inline": True},
                    {"name": "When", "value": str(context["created_at"])[:1024], "inline": False},
                ],
            }
        ],
    }
    return post_webhook(webhook, payload)


def send_email_digest(user: dict, items: list[tuple[dict, dict]], *, email_client: Any) -> bool:
    to_email = str(user.get("email") or "").strip()
    if not to_email:
        return False
    count = len(items)
    heading = f"{count} swarm update{'s' if count != 1 else ''}"
    body_rows = "".join(
        f"<li><strong>{escape(str(notification.get('title') or 'Swarm notification'))}</strong>"
        f" - {escape(_digest_detail(notification))}<br>"
        f"<small>{escape(str((swarm or {}).get('name') or 'your swarm'))} | "
        f"{escape(_format_datetime(notification.get('created_at')))}</small></li>"
        for notification, swarm in items
    )
    body_html = f"<p>Updates recorded during the last notification interval:</p><ul>{body_rows}</ul>"
    text_body = "\n".join(
        f"- {notification.get('title') or 'Swarm notification'}: {_digest_detail(notification)} "
        f"[{(swarm or {}).get('name') or 'your swarm'}; {_format_datetime(notification.get('created_at'))}]"
        for notification, swarm in items
    )
    html_body, text_body = email_client.themed_email(heading, body_html, text_body)
    return email_client.send_email(to_email, f"Batocera Overmind: {heading}", html_body, text_body)


def send_slack_digest(user: dict, items: list[tuple[dict, dict]]) -> bool:
    webhook = str((user.get("notification_settings") or {}).get("slack_webhook") or "").strip()
    if not webhook:
        return False
    heading = f"{len(items)} Batocera Overmind update{'s' if len(items) != 1 else ''}"
    lines = "\n".join(_digest_webhook_line(notification, swarm) for notification, swarm in items)[:2900]
    return post_webhook(webhook, {
        "text": f"{heading}\n{lines}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": heading[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": lines}},
        ],
    })


def send_discord_digest(user: dict, items: list[tuple[dict, dict]]) -> bool:
    webhook = str((user.get("notification_settings") or {}).get("discord_webhook") or "").strip()
    if not webhook:
        return False
    heading = f"{len(items)} Batocera Overmind update{'s' if len(items) != 1 else ''}"
    lines = "\n".join(_digest_discord_line(notification, swarm) for notification, swarm in items)[:3900]
    return post_webhook(webhook, {
        "username": "Batocera Overmind",
        "embeds": [{"title": heading[:256], "description": lines, "color": 5025535}],
    })


def post_webhook(webhook_url: str, payload: dict) -> bool:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "batocera-overmind"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= int(response.status) < 300
    except urllib.error.HTTPError as error:
        logger.error("Notification webhook failed status=%s reason=%s", error.code, error.reason)
        return False
    except urllib.error.URLError as error:
        logger.error("Notification webhook failed reason=%s", error.reason)
        return False


def _notification_context(notification: dict, swarm: Optional[dict], actor: Optional[dict]) -> dict:
    details = notification.get("details") if isinstance(notification.get("details"), dict) else {}
    asset = details.get("asset") if isinstance(details.get("asset"), dict) else {}
    device = details.get("device") if isinstance(details.get("device"), dict) else {}
    devices = details.get("devices") if isinstance(details.get("devices"), list) else []
    targets = details.get("targets") if isinstance(details.get("targets"), list) else []
    sources = details.get("sources") if isinstance(details.get("sources"), list) else []

    return {
        "title": notification.get("title") or "Swarm notification",
        "message": notification.get("message") or "",
        "swarm_name": (swarm or {}).get("name") or "your swarm",
        "event_label": NOTIFICATION_EVENT_TYPES.get(str(notification.get("event_type") or ""), "Swarm notification"),
        "created_at": _format_datetime(notification.get("created_at")),
        "asset_name": asset.get("path") or asset.get("name") or "the item",
        "asset_kind": _asset_kind(str(notification.get("event_type") or "")),
        "device_name": device.get("device_name") or device.get("device_id") or _first_device_name(devices) or "a Drone",
        "status": details.get("status") or "",
        "sync_type": details.get("sync_type") or "Sync",
        "nature": details.get("nature") or notification.get("message") or "",
        "actor_name": (actor or {}).get("full_name") or (actor or {}).get("email") or "An Overlord",
        "devices_html": _device_list_html(devices),
        "targets_html": _device_list_html(targets),
        "sources_html": _device_list_html(sources),
    }


def _notification_text(notification: dict, context: dict) -> str:
    lines = [
        str(notification.get("title") or "Swarm notification"),
        str(notification.get("message") or ""),
        f"Swarm: {context['swarm_name']}",
        f"When: {context['created_at']}",
    ]
    return "\n".join(line for line in lines if line)


def _webhook_text(notification: dict, context: dict) -> str:
    return "\n".join([
        f"*{notification.get('title') or 'Swarm notification'}*",
        str(notification.get("message") or ""),
        f"Swarm: {context['swarm_name']}",
        f"Event: {context['event_label']}",
        f"When: {context['created_at']}",
    ])


def _digest_detail(notification: dict) -> str:
    details = notification.get("details") if isinstance(notification.get("details"), dict) else {}
    nature = str(details.get("nature") or "").strip()
    message = str(notification.get("message") or "").strip()
    if nature and nature not in message:
        return f"{message} {nature}".strip()
    return message


def _digest_webhook_line(notification: dict, swarm: dict) -> str:
    return (
        f"*{notification.get('title') or 'Swarm notification'}*: {_digest_detail(notification)} "
        f"_({(swarm or {}).get('name') or 'your swarm'}, {_format_datetime(notification.get('created_at'))})_"
    )


def _digest_discord_line(notification: dict, swarm: dict) -> str:
    return (
        f"**{notification.get('title') or 'Swarm notification'}**: {_digest_detail(notification)} "
        f"({(swarm or {}).get('name') or 'your swarm'}, {_format_datetime(notification.get('created_at'))})"
    )


def _format_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value or "")


def _asset_kind(event_type: str) -> str:
    if "bios" in event_type:
        return "BIOS"
    if "artwork" in event_type:
        return "artwork"
    return "ROM"


def _first_device_name(devices: list) -> str:
    if not devices:
        return ""
    first = devices[0] if isinstance(devices[0], dict) else {}
    return str(first.get("device_name") or first.get("device_id") or "")


def _device_list_html(devices: list) -> str:
    if not devices:
        return "<span>None listed</span>"
    rows = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        label = device.get("device_name") or device.get("device_id")
        if label:
            rows.append(f"<li>{escape(str(label))}</li>")
    return "<ul>" + "".join(rows or ["<li>None listed</li>"]) + "</ul>"
