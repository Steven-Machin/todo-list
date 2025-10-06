from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .models import NotificationJob, NotificationMessage, Recipient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationPreferences:
    """User-configurable notification preferences."""

    frequency: str = "daily"  # daily, weekly, off
    channels: List[str] = field(default_factory=list)
    daily_hour: int = 7
    overdue_enabled: bool = True
    badge_enabled: bool = True
    summary_enabled: bool = True
    weekly_day: int = 0  # Monday by default

    def effective_channels(self) -> List[str]:
        return self.channels or ["email"]


def build_recipient(username: str, preferences: NotificationPreferences, profile: Dict[str, str]) -> Recipient:
    channels = preferences.effective_channels()
    return Recipient(
        username=username,
        email=profile.get("email"),
        discord_webhook=profile.get("discord_webhook"),
        channels=channels,
    )


def create_message(subject: str, body_lines: Iterable[str], *, category: str = "general") -> NotificationMessage:
    body_text = "\n".join(body_lines)
    return NotificationMessage(subject=subject, body_text=body_text, category=category)


def deliver_jobs(jobs: Iterable[NotificationJob], dispatcher) -> int:
    delivered = 0
    for job in jobs:
        if not job.messages:
            continue
        try:
            dispatcher(job.recipient, job.messages)
            delivered += 1
        except Exception:  # pragma: no cover - fatal logging only
            LOGGER.exception("Failed to dispatch notifications for %s", job.recipient.username)
    return delivered
