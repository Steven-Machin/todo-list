from __future__ import annotations

import json
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional

import requests

from .models import NotificationMessage, Recipient

LOGGER = logging.getLogger(__name__)


def _smtp_connection():
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "1") not in {"0", "false", "False"}

    if not host:
        return None

    server = smtplib.SMTP(host, port, timeout=10)
    try:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
    except Exception:
        server.quit()
        raise
    return server


def send_email(recipient: Recipient, message: NotificationMessage) -> bool:
    """Send the notification via SMTP email."""
    if not recipient.email:
        LOGGER.info("Skipping email notification for %s: no address", recipient.username)
        return False

    sender = os.getenv("NOTIFY_FROM_EMAIL") or os.getenv("SMTP_DEFAULT_SENDER")
    if not sender:
        LOGGER.warning("Skipping email notification: NOTIFY_FROM_EMAIL not configured")
        return False

    email = EmailMessage()
    email["Subject"] = message.subject
    email["From"] = sender
    email["To"] = recipient.email
    email.set_content(message.body_text)
    if message.body_html:
        email.add_alternative(message.body_html, subtype="html")

    try:
        server = _smtp_connection()
        if server is None:
            LOGGER.warning("SMTP_HOST not configured; email suppressed")
            return False
        with server:
            server.send_message(email)
        LOGGER.info("Sent email notification '%s' to %s", message.subject, recipient.email)
        return True
    except Exception as exc:  # pragma: no cover - network dependant
        LOGGER.exception("Failed to send email notification: %s", exc)
        return False


def send_discord(recipient: Recipient, message: NotificationMessage) -> bool:
    """Send the notification to a Discord webhook."""
    webhook_url = recipient.discord_webhook or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        LOGGER.info("Skipping discord notification for %s: no webhook", recipient.username)
        return False

    payload = {
        "username": os.getenv("DISCORD_BOT_NAME", "To-Do Bot"),
        "content": f"**{message.subject}**\n{message.body_text}",
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=5)
        if resp.status_code >= 400:
            LOGGER.error("Discord webhook responded with %s: %s", resp.status_code, resp.text[:120])
            return False
        LOGGER.info("Sent discord notification '%s' to %s", message.subject, webhook_url)
        return True
    except Exception as exc:  # pragma: no cover - network dependant
        LOGGER.exception("Failed to send discord notification: %s", exc)
        return False


def dispatch(recipient: Recipient, messages: Iterable[NotificationMessage]) -> None:
    """Send messages using all configured channels for the recipient."""
    channels = set(recipient.channels) if recipient.channels else {"email", "discord"}
    for msg in messages:
        delivered = False
        if "email" in channels:
            delivered |= send_email(recipient, msg)
        if "discord" in channels:
            delivered |= send_discord(recipient, msg)
        if not delivered:
            LOGGER.info("Notification '%s' was not delivered to %s", msg.subject, recipient.username)
