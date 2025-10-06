from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List

from celery import shared_task

from .channels import dispatch
from .collectors import (
    collect_badge_progress_jobs,
    collect_daily_summary_jobs,
    collect_overdue_jobs,
    load_notification_preferences,
)
from .models import NotificationJob
from .service import deliver_jobs

LOGGER = logging.getLogger(__name__)


def _merge_jobs(primary: List[NotificationJob], extra: List[NotificationJob]) -> List[NotificationJob]:
    job_map: Dict[str, NotificationJob] = {job.recipient.username: job for job in primary}
    for job in extra:
        key = job.recipient.username
        if key in job_map:
            job_map[key].messages.extend(job.messages)
        else:
            job_map[key] = job
    return list(job_map.values())


@shared_task(name="notifications.tasks.schedule_overdue_alerts")
def schedule_overdue_alerts() -> str:
    today = date.today()
    prefs = load_notification_preferences()
    jobs = collect_overdue_jobs(today, prefs)
    delivered = deliver_jobs(jobs, dispatch)
    LOGGER.info("Sent %d overdue notifications", delivered)
    return str(delivered)


@shared_task(name="notifications.tasks.schedule_daily_summaries")
def schedule_daily_summaries() -> str:
    today = date.today()
    prefs = load_notification_preferences()
    summary_jobs = collect_daily_summary_jobs(today, prefs)
    badge_jobs = collect_badge_progress_jobs(prefs)
    jobs = _merge_jobs(summary_jobs, badge_jobs)
    delivered = deliver_jobs(jobs, dispatch)
    LOGGER.info("Dispatched %d daily summary notifications", delivered)
    return str(delivered)


@shared_task(name="notifications.tasks.deliver_jobs")
def deliver_jobs_task(payload: List[Dict]) -> str:
    # Payload-based dispatch left for future extensibility
    LOGGER.info("deliver_jobs_task invoked with %d payload items", len(payload))
    return str(len(payload))
