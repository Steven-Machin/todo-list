"""Celery application factory for background notification tasks."""
from __future__ import annotations

import os
from celery import Celery
from celery.schedules import crontab

DEFAULT_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
DEFAULT_BACKEND_URL = os.getenv("CELERY_RESULT_BACKEND", DEFAULT_BROKER_URL)


def create_celery_app() -> Celery:
    """Create and configure the Celery app for the project."""
    celery_app = Celery(
        "todo_notifications",
        broker=DEFAULT_BROKER_URL,
        backend=DEFAULT_BACKEND_URL,
        include=["notifications.tasks"],
    )

    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone=os.getenv("CELERY_TIMEZONE", "UTC"),
        enable_utc=True,
        beat_schedule={
            "send-daily-summaries": {
                "task": "notifications.tasks.schedule_daily_summaries",
                "schedule": crontab(hour=int(os.getenv("NOTIFY_SUMMARY_HOUR", "7")), minute=0),
            },
            "scan-overdue-reminders": {
                "task": "notifications.tasks.schedule_overdue_alerts",
                "schedule": crontab(minute="*/30"),
            },
        },
    )

    return celery_app


celery_app = create_celery_app()
