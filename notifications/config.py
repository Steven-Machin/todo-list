"""Shared configuration defaults for the notification system."""
from __future__ import annotations

DEFAULT_NOTIFICATION_PREFS = {
    "frequency": "daily",
    "channels": ["email"],
    "daily_hour": 7,
    "overdue_enabled": True,
    "badge_enabled": True,
    "summary_enabled": True,
    "weekly_day": 0,
}

VALID_CHANNELS = {"email", "discord"}
VALID_FREQUENCIES = {"daily", "weekly", "off"}
WEEKDAY_OPTIONS = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]
