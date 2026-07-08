"""Chat agent surface — let the MCP/LLM speak in a layout's chat control.

A channel chat control listens on the `chat_message` event for
{id, text, sender:{name,role}, timestamp, channel}. These helpers build outgoing
messages and parse incoming ones; the socket send/listen lives in server.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional


def build_chat_message(text: str, sender_name: str = "Claude",
                       sender_role: str = "assistant", channel: str = "",
                       reply_to: Optional[str] = None) -> dict:
    """A channel chat_message frame the CAR-TER chat control will render."""
    msg: dict = {
        "id": str(uuid.uuid4()),
        "text": text,
        "sender": {"name": sender_name, "role": sender_role},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if channel:
        msg["channel"] = channel
    if reply_to:
        msg["replyToId"] = reply_to
    return msg


def parse_incoming(payload) -> Optional[dict]:
    """Extract {text, sender} from an incoming chat_message payload, or None."""
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if text is None:
        return None
    sender = payload.get("sender")
    name = sender.get("name") if isinstance(sender, dict) else (sender or "?")
    return {"text": text, "sender": name or "?"}
