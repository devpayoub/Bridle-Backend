"""Update parsing: what reaches the model and what is rejected before it."""
from __future__ import annotations

import time

import pytest

from apps.channels.adapters.base import IgnoredUpdate
from apps.channels.telegram.updates import parse_update

CHAT_ID = 4242
USER_ID = 99


def make_update(**message_overrides):
    message = {
        "message_id": 7,
        "date": int(time.time()),
        "from": {"id": USER_ID, "is_bot": False},
        "chat": {"id": CHAT_ID, "type": "private"},
        "text": "What time is check-in?",
    }
    message.update(message_overrides)
    return {"update_id": 1001, "message": message}


def test_private_text_message_is_parsed():
    inbound = parse_update(make_update())
    assert inbound.text == "What time is check-in?"
    assert inbound.external_thread_id == str(CHAT_ID)
    assert inbound.external_user_id == str(USER_ID)
    assert inbound.external_message_id == "7"


def test_text_is_trimmed():
    assert parse_update(make_update(text="  hello  ")).text == "hello"


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 1},
        {"update_id": 1, "edited_message": {"text": "changed"}},
        {"update_id": 1, "callback_query": {"id": "1"}},
    ],
)
def test_non_message_updates_are_ignored(payload):
    with pytest.raises(IgnoredUpdate):
        parse_update(payload)


def test_bot_sender_is_ignored():
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(**{"from": {"id": 1, "is_bot": True}}))
    assert excinfo.value.reason == "bot_sender"


def test_group_chat_is_ignored():
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(chat={"id": -100, "type": "group"}))
    assert excinfo.value.reason == "group_chat"


@pytest.mark.parametrize(
    "field,value",
    [
        ("photo", [{"file_id": "x"}]),
        ("document", {"file_id": "x"}),
        ("voice", {"file_id": "x"}),
        ("sticker", {"file_id": "x"}),
        ("location", {"latitude": 1, "longitude": 2}),
        ("contact", {"phone_number": "1"}),
    ],
)
def test_unsupported_content_is_rejected_with_a_reply(field, value):
    payload = make_update(text=None, **{field: value})
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(payload)
    assert excinfo.value.reason == "unsupported_content"
    assert excinfo.value.user_message


def test_forwarded_message_is_rejected(settings):
    settings.CHANNELS_TELEGRAM_ALLOW_FORWARDS = False
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(forward_origin={"type": "user"}))
    assert excinfo.value.reason == "forwarded_message"


def test_forwarded_message_can_be_allowed(settings):
    settings.CHANNELS_TELEGRAM_ALLOW_FORWARDS = True
    assert parse_update(make_update(forward_origin={"type": "user"})).text


def test_stale_update_is_rejected(settings):
    settings.CHANNELS_MAX_UPDATE_AGE_SECONDS = 60
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(date=int(time.time()) - 7200))
    assert excinfo.value.reason == "stale_update"


def test_oversized_message_is_rejected(settings):
    settings.CHAT_MAX_MESSAGE_CHARS = 50
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(text="x" * 200))
    assert excinfo.value.reason == "message_too_long"
    assert excinfo.value.user_message


def test_empty_text_without_media_is_ignored():
    with pytest.raises(IgnoredUpdate) as excinfo:
        parse_update(make_update(text="   "))
    assert excinfo.value.reason == "empty_message"
