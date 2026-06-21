"""Tests for chat.py — outgoing message build + incoming parse."""

import chat


def test_build_chat_message_shape():
    msg = chat.build_chat_message("hello", sender_name="Claude", channel="home")
    assert msg["text"] == "hello"
    assert msg["sender"] == {"name": "Claude", "role": "assistant"}
    assert msg["channel"] == "home"
    assert "id" in msg and "timestamp" in msg


def test_build_chat_message_reply():
    msg = chat.build_chat_message("re", reply_to="abc")
    assert msg["replyToId"] == "abc"


def test_parse_incoming_roundtrip():
    out = chat.parse_incoming(chat.build_chat_message("hi", sender_name="Phone"))
    assert out == {"text": "hi", "sender": "Phone"}


def test_parse_incoming_garbage():
    assert chat.parse_incoming("nope") is None
    assert chat.parse_incoming({"no_text": 1}) is None
