"""Discord通知。"""

from house_search.notify.discord import DiscordSender, build_sender
from house_search.notify.format import (
    COLORS,
    TYPE_LABELS,
    DigestEntry,
    NotifiableProperty,
    build_digest_message,
    build_error_message,
    build_property_embed,
    build_property_message,
)

__all__ = [
    "COLORS",
    "TYPE_LABELS",
    "DigestEntry",
    "DiscordSender",
    "NotifiableProperty",
    "build_digest_message",
    "build_error_message",
    "build_property_embed",
    "build_property_message",
    "build_sender",
]
