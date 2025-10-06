from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List

from app import (
    get_badge_catalog,
    get_next_badge_progress,
    get_user_badges,
    get_user_completion_stats,
    load_prefs,
    load_tasks,
    load_users,
    parse_date,
    task_visible_to,
)
from notifications.config import DEFAULT_NOTIFICATION_PREFS

from .models import NotificationJob
from .service import NotificationPreferences, build_recipient, create_message

LOGGER = logging.getLogger(__name__)


def load_notification_preferences() -> Dict[str, NotificationPreferences]:
    prefs_raw = load_prefs()
    preferences: Dict[str, NotificationPreferences] = {}
    for username, data in prefs_raw.items():
        key = str(username).lower()
        notif = data.get("notifications", {}) if isinstance(data, dict) else {}
        merged = {**DEFAULT_NOTIFICATION_PREFS, **notif}
        channels = merged.get("channels")
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        elif not channels:
            channels = list(DEFAULT_NOTIFICATION_PREFS["channels"])
        prefs = NotificationPreferences(
            frequency=str(merged.get("frequency", "daily")).lower(),
            channels=list(channels),
            daily_hour=int(merged.get("daily_hour", 7)),
            overdue_enabled=bool(merged.get("overdue_enabled", True)),
            badge_enabled=bool(merged.get("badge_enabled", True)),
            summary_enabled=bool(merged.get("summary_enabled", True)),
            weekly_day=int(merged.get("weekly_day", 0)),
        )
        preferences[key] = prefs
    return preferences


def _visible_open_tasks_by_user(tasks: Iterable[Dict], users: List[Dict]) -> Dict[str, List[Dict]]:
    results: Dict[str, List[Dict]] = defaultdict(list)
    for user in users:
        username = user.get("username")
        if not username:
            continue
        normalized = username.lower()
        for task in tasks:
            if task.get("done"):
                continue
            if task_visible_to(task, normalized, users):
                results[normalized].append(task)
    return results


def _format_task_line(task: Dict, due_date: date) -> str:
    label = task.get("text") or "Task"
    priority = task.get("priority") or "Medium"
    return f"- {label} (due {due_date.strftime('%b %d')}, {priority} priority)"


def collect_overdue_jobs(today: date, preferences: Dict[str, NotificationPreferences]) -> List[NotificationJob]:
    users = load_users()
    tasks = load_tasks()
    visible = _visible_open_tasks_by_user(tasks, users)

    jobs: List[NotificationJob] = []
    for user in users:
        uname = (user.get("username") or "").lower()
        if not uname:
            continue
        prefs = preferences.get(uname) or NotificationPreferences()
        if prefs.frequency == "off" or not prefs.overdue_enabled:
            continue

        overdue_lines: List[str] = []
        for task in visible.get(uname, []):
            due = parse_date(task.get("due_date") or task.get("due"))
            if due and due < today:
                overdue_lines.append(_format_task_line(task, due))

        if not overdue_lines:
            continue

        subject = "Overdue tasks reminder"
        body_lines = [
            "You have overdue tasks that need attention:",
            "",
            *overdue_lines,
        ]
        job = NotificationJob(
            recipient=build_recipient(uname, prefs, user),
            messages=[create_message(subject, body_lines, category="overdue")],
        )
        jobs.append(job)
    return jobs


def _should_send_summary(prefs: NotificationPreferences, target_date: date) -> bool:
    if prefs.frequency == "off" or not prefs.summary_enabled:
        return False
    if prefs.frequency == "daily":
        return True
    if prefs.frequency == "weekly":
        return target_date.weekday() == prefs.weekly_day
    return False


def collect_daily_summary_jobs(target_date: date, preferences: Dict[str, NotificationPreferences]) -> List[NotificationJob]:
    users = load_users()
    tasks = load_tasks()
    visible = _visible_open_tasks_by_user(tasks, users)

    jobs: List[NotificationJob] = []
    for user in users:
        uname = (user.get("username") or "").lower()
        if not uname:
            continue
        prefs = preferences.get(uname) or NotificationPreferences()
        if not _should_send_summary(prefs, target_date):
            continue

        due_today_lines: List[str] = []
        for task in visible.get(uname, []):
            due = parse_date(task.get("due_date") or task.get("due"))
            if due and due == target_date:
                due_today_lines.append(_format_task_line(task, due))

        if not due_today_lines:
            continue

        subject = "Today's task summary"
        body_lines = [
            "Here's what's due today:",
            "",
            *due_today_lines,
        ]
        job = NotificationJob(
            recipient=build_recipient(uname, prefs, user),
            messages=[create_message(subject, body_lines, category="daily")],
        )
        jobs.append(job)
    return jobs


def collect_badge_progress_jobs(preferences: Dict[str, NotificationPreferences]) -> List[NotificationJob]:
    users = load_users()
    tasks = load_tasks()
    badge_catalog = get_badge_catalog()

    jobs: List[NotificationJob] = []
    for user in users:
        uname = (user.get("username") or "").lower()
        if not uname:
            continue
        prefs = preferences.get(uname) or NotificationPreferences()
        if prefs.frequency == "off" or not prefs.badge_enabled:
            continue

        stats = get_user_completion_stats(uname, tasks)
        earned = get_user_badges(uname)
        earned_slugs = {badge.get("slug") for badge in earned}
        progress = get_next_badge_progress(stats, earned_slugs, badge_catalog)
        if not progress or not progress.get("remaining"):
            continue

        remaining = int(progress["remaining"])
        if remaining <= 0:
            continue
        badge = progress.get("badge", {})
        subject = f"Badge progress: {badge.get('name', 'New badge')}"
        body_lines = [
            f"You're {remaining} task{'s' if remaining != 1 else ''} away from the {badge.get('name', 'next')} badge!",
        ]
        job = NotificationJob(
            recipient=build_recipient(uname, prefs, user),
            messages=[create_message(subject, body_lines, category="badge")],
        )
        jobs.append(job)
    return jobs


__all__ = [
    "load_notification_preferences",
    "collect_overdue_jobs",
    "collect_daily_summary_jobs",
    "collect_badge_progress_jobs",
]
