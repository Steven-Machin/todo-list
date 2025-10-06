from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass(slots=True)
class Recipient:
    """Represents a user or destination for notifications."""

    username: str
    email: Optional[str] = None
    discord_webhook: Optional[str] = None
    channels: Sequence[str] = field(default_factory=list)


@dataclass(slots=True)
class NotificationMessage:
    """Structured payload passed to concrete notification senders."""

    subject: str
    body_text: str
    body_html: Optional[str] = None
    category: str = "general"
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class NotificationJob:
    """Batch of messages to deliver for a given recipient."""

    recipient: Recipient
    messages: List[NotificationMessage] = field(default_factory=list)

    def add(self, message: NotificationMessage) -> None:
        self.messages.append(message)
