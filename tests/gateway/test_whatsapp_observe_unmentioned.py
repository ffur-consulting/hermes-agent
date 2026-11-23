"""WhatsApp observe mode: unaddressed group chatter is stored as context, never dispatched.

Covers:
- the gate still returns None (no dispatch) for an unaddressed group message
- the skipped message lands in the group session transcript tagged ``[name|id]`` with ``observed``
- DMs, non-allowlisted groups and unset/narrow ``observe_chats`` observe nothing
"""

import asyncio
import re
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig

GROUP = "120363413124512952@g.us"


class _FakeSession:
    def __init__(self):
        self.session_id = "agent:main:whatsapp:group:" + GROUP

    def get_or_create_session(self, source, **_kwargs):
        self.source = source
        return self


class _FakeStore:
    def __init__(self):
        self.entries = []
        self.session = _FakeSession()

    def get_or_create_session(self, source, **_kwargs):
        self.source = source
        return self.session

    def append_to_transcript(self, session_id, entry):
        self.entries.append((session_id, entry))


def _adapter(extra, *, group_allow=GROUP, store=None):
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = WhatsAppAdapter(PlatformConfig(enabled=True, extra=extra))
    adapter._group_policy = "allowlist"
    adapter._group_allow_from = {group_allow}
    adapter._session_store = store or _FakeStore()
    return adapter


def _group_data(body="halo semua", **overrides):
    data = {
        "isGroup": True,
        "chatId": GROUP,
        "chatName": "Whizzy Agent Core",
        "senderId": "628111222333@lid",
        "senderName": "Tommy",
        "body": body,
        "messageId": "MSG-1",
    }
    data.update(overrides)
    return data


class TestObserveGate:
    def test_observed_message_never_dispatches(self):
        adapter = _adapter({"observe_unmentioned_group_messages": True})
        # require_mention on with a jarvis wake word: "halo semua" is unaddressed
        adapter._mention_patterns = [re.compile(r"(?i)^\s*jarvis\b")]

        assert asyncio.run(adapter._build_message_event(_group_data())) is None
        assert len(adapter._session_store.entries) == 1

    def test_addressed_message_dispatches_and_is_not_observed(self):
        adapter = _adapter({"observe_unmentioned_group_messages": True})
        adapter._mention_patterns = [re.compile(r"(?i)^\s*jarvis\b")]

        event = asyncio.run(adapter._build_message_event(_group_data(body="jarvis tolong cek")))
        assert event is not None
        assert adapter._session_store.entries == []


class TestObserveScope:
    def test_off_by_default(self):
        adapter = _adapter({})
        adapter._mention_patterns = [re.compile(r"(?i)^\s*jarvis\b")]

        asyncio.run(adapter._build_message_event(_group_data()))
        assert adapter._session_store.entries == []

    def test_dm_is_never_observed(self):
        adapter = _adapter({"observe_unmentioned_group_messages": True})

        adapter._observe_unmentioned_group_message(_group_data(isGroup=False, chatId="628111222333@s.whatsapp.net"))
        assert adapter._session_store.entries == []

    def test_group_outside_allowlist_is_never_observed(self):
        adapter = _adapter({"observe_unmentioned_group_messages": True})

        adapter._observe_unmentioned_group_message(_group_data(chatId="120363999999999999@g.us"))
        assert adapter._session_store.entries == []

    def test_observe_chats_narrows_scope(self):
        adapter = _adapter({
            "observe_unmentioned_group_messages": True,
            "observe_chats": "120363999999999999@g.us",
        })

        adapter._observe_unmentioned_group_message(_group_data())
        assert adapter._session_store.entries == []


class TestObservedEntry:
    def test_entry_is_attributed_to_sender_on_group_session(self):
        store = _FakeStore()
        adapter = _adapter({"observe_unmentioned_group_messages": True}, store=store)

        adapter._observe_unmentioned_group_message(_group_data())

        session_id, entry = store.entries[0]
        assert session_id == "agent:main:whatsapp:group:" + GROUP
        assert entry["role"] == "user"
        assert entry["content"] == "[Tommy|628111222333] halo semua"
        assert entry["observed"] is True
        assert entry["message_id"] == "MSG-1"
        # Shared session: the observed entry must not key on the sender.
        assert store.session.source.user_id is None
        assert store.session.source.chat_id == GROUP

    def test_empty_body_is_skipped(self):
        store = _FakeStore()
        adapter = _adapter({"observe_unmentioned_group_messages": True}, store=store)

        adapter._observe_unmentioned_group_message(_group_data(body="   "))
        assert store.entries == []

    def test_missing_session_store_is_survivable(self):
        adapter = _adapter({"observe_unmentioned_group_messages": True})
        adapter._session_store = None

        adapter._observe_unmentioned_group_message(_group_data())  # must not raise


class TestConfigBridge:
    def test_yaml_key_bridges_to_env(self, monkeypatch):
        from plugins.platforms.whatsapp.adapter import _apply_yaml_config

        monkeypatch.delenv("WHATSAPP_OBSERVE_UNMENTIONED_GROUP_MESSAGES", raising=False)
        seeded = _apply_yaml_config({}, {"observe_unmentioned_group_messages": True})

        assert seeded["observe_unmentioned_group_messages"] is True
