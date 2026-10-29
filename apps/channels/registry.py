"""Adapter registry — one lookup, no imports at call sites."""
from __future__ import annotations

from apps.channels.adapters.base import ChannelAdapter
from apps.channels.adapters.telegram import TelegramAdapter

_ADAPTERS: dict[str, ChannelAdapter] = {
    "TELEGRAM": TelegramAdapter(),
}


class UnsupportedChannelType(Exception):
    pass


def get_adapter(channel_type: str) -> ChannelAdapter:
    adapter = _ADAPTERS.get((channel_type or "").upper())
    if adapter is None:
        raise UnsupportedChannelType(channel_type)
    return adapter
