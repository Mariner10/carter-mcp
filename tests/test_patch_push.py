"""K12 (carter-n4x.11): push_buffer sends a small patch-layout against the last
device-acknowledged layout, with exactly one full apply-layout as the fallback.

The socket is faked: every routed verb answers from a per-verb script, and each
call is recorded so tests can assert what went over the wire (and how often).
"""

from __future__ import annotations

import asyncio
import copy

import pytest

# carterkit>=0.14.0 ships carterkit.patch; an older venv skips rather than errors.
patch = pytest.importorskip("carterkit.patch")
from carterkit.buffer import LayoutBuffer

from carter_mcp import mesh, protocol, state
from carter_mcp.tools import buffer as buffer_tools
from carter_mcp.tools import device as device_tools

PATCH_INFO = {"appVersion": "1.3", "protocolVersion": 2, "features": ["verb.patch-layout"]}
OLD_INFO = {"appVersion": "1.2.6", "protocolVersion": 2, "features": ["control.gauge"]}


class FakeDeviceSocket:
    """Routed replies by verb: a value, a list (popped per call) or a callable."""

    is_running = True

    def __init__(self, replies):
        self.replies = replies
        self.calls = []      # (verb, payload)
        self.sent = []       # broadcast frames

    async def request(self, event, payload=None, timeout=None):
        if event == "get_nodes":
            return {"clients": [{"id": "dev-1", "name": "phone"}]}
        verb = payload["type"]
        self.calls.append((verb, payload["payload"]))
        reply = self.replies.get(verb)
        if isinstance(reply, list):
            reply = reply.pop(0)
        if callable(reply):
            reply = reply(payload["payload"])
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)

    async def send(self, event, payload=None):
        self.sent.append((event, payload))

    async def stop(self):
        self.is_running = False

    def verbs(self):
        return [v for v, _ in self.calls]


def base_layout():
    return {"name": "K12", "tabs": [
        {"title": "Main", "icon": "house", "grid": {"columns": 4, "rows": 8}, "children": [
            {"id": "temp", "type": "gauge", "position": [0, 0], "span": [2, 2], "tint": "#fff"},
            {"id": "note", "type": "label", "text": "hi", "position": [2, 0]},
        ]},
    ]}


@pytest.fixture
def dev(monkeypatch):
    sock = FakeDeviceSocket({
        "get-device-info": PATCH_INFO,
        "apply-layout": {"ok": True, "hash": "sha256:dev-a", "rendered": {"tabs": []}},
        "patch-layout": lambda p: {"ok": True, "applied": len(p["ops"]),
                                   "reload": "hot", "hash": "sha256:dev-b"},
    })
    monkeypatch.setattr(state, "socket", sock)
    monkeypatch.setattr(state, "socket_task", None)
    monkeypatch.setattr(state, "peer_list", [{"id": "dev-1"}])
    monkeypatch.setattr(state, "work_buffer", LayoutBuffer.from_layout(base_layout()))
    mesh.clear_acked(support=True)
    yield sock
    mesh.clear_acked(support=True)


def push(full=False):
    return asyncio.run(device_tools.push_buffer(full=full))


def edit_tint(color="#f00"):
    state.work_buffer.layout["tabs"][0]["children"][0]["tint"] = color


# ─── first push / acked state ────────────────────────────────────────────────

def test_first_push_is_full_and_records_device_hash(dev):
    out = push()
    assert dev.verbs() == ["apply-layout"]
    assert "Full push" in out and "full reload" in out and "no acknowledged base" in out
    assert state.acked_hash == "sha256:dev-a"          # device hash preferred
    assert state.acked_layout == base_layout()
    assert state.acked_layout is not state.work_buffer.layout   # a deep copy
    assert state.acked_device_id == "dev-1"


def test_ack_without_device_hash_uses_canonical_hash(dev):
    dev.replies["apply-layout"] = {"ok": True, "rendered": {"tabs": []}}
    push()
    assert state.acked_hash == patch.layout_hash(base_layout())


def test_rejected_apply_clears_base(dev):
    push()
    dev.replies["apply-layout"] = {"ok": False, "error": "decode failed"}
    push(full=True)
    assert state.acked_layout is None and state.acked_hash is None


# ─── the patch path ──────────────────────────────────────────────────────────

def test_second_push_sends_patch_with_base_and_ops(dev):
    push()
    edit_tint()
    out = push()
    assert dev.verbs() == ["apply-layout", "get-device-info", "patch-layout"]
    payload = dev.calls[-1][1]
    assert payload["base"] == "sha256:dev-a"
    assert payload["ops"] == patch.diff(base_layout(), state.work_buffer.layout)
    assert payload["ops"] and all(op["op"] == "update" for op in payload["ops"])
    assert out.startswith("Patched 1 op · hot reload (values kept)")
    assert state.acked_hash == "sha256:dev-b"
    assert state.acked_layout == state.work_buffer.layout


def test_full_reload_reply_is_surfaced(dev):
    push()
    dev.replies["patch-layout"] = {"ok": True, "applied": 1, "reload": "full", "hash": "sha256:c"}
    edit_tint()
    out = push()
    assert out.startswith("Patched 1 op · full reload")
    assert dev.verbs().count("apply-layout") == 1


def test_patch_without_reply_hash_records_canonical_hash(dev):
    push()
    dev.replies["patch-layout"] = {"ok": True, "applied": 1, "reload": "hot"}
    edit_tint()
    push()
    assert state.acked_hash == patch.layout_hash(state.work_buffer.layout)


def test_device_capability_cached_per_pairing(dev):
    push()
    for c in ("#a00", "#b00", "#c00"):
        edit_tint(c)
        push()
    assert dev.verbs().count("get-device-info") == 1
    assert dev.verbs().count("patch-layout") == 3


def test_no_change_still_sends_empty_patch_to_verify_base(dev):
    push()
    out = push()
    assert dev.calls[-1] == ("patch-layout", {"base": "sha256:dev-a", "ops": []})
    assert out.startswith("Patched 0 ops · hot reload")


def test_multi_op_patch_reports_count(dev):
    push()
    kids = state.work_buffer.layout["tabs"][0]["children"]
    kids[0]["tint"] = "#0f0"
    kids[1]["text"] = "bye"
    state.work_buffer.layout["accentColor"] = "#123456"
    out = push()
    ops = dev.calls[-1][1]["ops"]
    assert len(ops) == 3 and {o["op"] for o in ops} == {"layout", "update"}
    assert out.startswith("Patched 3 ops · hot reload")


# ─── fallbacks: exactly one full push, and say why ───────────────────────────

def _primed(dev):
    push()
    edit_tint()
    dev.calls.clear()


def test_stale_base_falls_back_to_exactly_one_full_push(dev):
    _primed(dev)
    dev.replies["patch-layout"] = {"ok": False, "error": "stale-base", "hash": "sha256:zzz"}
    dev.replies["apply-layout"] = {"ok": True, "hash": "sha256:dev-c", "rendered": {"tabs": []}}
    out = push()
    assert dev.verbs() == ["get-device-info", "patch-layout", "apply-layout"]
    assert out.startswith("Full push · full reload (stale-base")
    assert state.acked_hash == "sha256:dev-c"


def test_no_reply_falls_back_to_full(dev):
    _primed(dev)
    dev.replies["patch-layout"] = None
    out = push()
    assert dev.verbs() == ["get-device-info", "patch-layout", "apply-layout"]
    assert "did not answer patch-layout" in out and "full reload" in out


def test_relay_error_falls_back_to_full(dev):
    _primed(dev)
    dev.replies["patch-layout"] = {"error": "target not found"}
    out = push()
    assert dev.verbs().count("apply-layout") == 1
    assert "patch-layout error" in out and "target not found" in out


def test_socket_exception_falls_back_to_full(dev):
    _primed(dev)
    dev.replies["patch-layout"] = ConnectionError("socket closed")
    out = push()
    assert dev.verbs().count("apply-layout") == 1
    assert "relay error on patch-layout" in out


def test_protocol_v2_app_gets_full_push_and_no_patch(dev):
    dev.replies["get-device-info"] = OLD_INFO
    _primed(dev)
    out = push()
    assert "patch-layout" not in dev.verbs()
    assert dev.verbs() == ["get-device-info", "apply-layout"]
    assert "older app" in out and "update CAR-TER to 1.3 for patch pushes" in out


def test_unanswered_device_info_gets_full_push(dev):
    dev.replies["get-device-info"] = None
    _primed(dev)
    out = push()
    assert "patch-layout" not in dev.verbs()
    assert "did not report its capabilities" in out


def test_protocol_v3_device_patches(dev):
    dev.replies["get-device-info"] = {"protocolVersion": 3}
    _primed(dev)
    assert push().startswith("Patched")


def test_inexpressible_diff_gets_full_push_and_no_patch(dev):
    _primed(dev)
    state.work_buffer.layout["tabs"].append({"title": "Two", "children": []})
    out = push()
    assert "patch-layout" not in dev.verbs()
    assert dev.verbs() == ["get-device-info", "apply-layout"]
    assert "not expressible" in out


def test_full_true_forces_full_push(dev):
    _primed(dev)
    out = push(full=True)
    assert dev.verbs() == ["apply-layout"]
    assert out.startswith("Full push · full reload (full=True)")


def test_broadcast_when_no_device_and_base_cleared(dev, monkeypatch):
    push()

    async def no_device():
        return None
    monkeypatch.setattr(mesh, "get_device_id", no_device)
    out = push()
    assert "no single device paired" in out and dev.sent
    assert state.acked_layout is None


# ─── clearing the base ───────────────────────────────────────────────────────

def test_reconnect_clears_base(dev):
    push()
    assert state.acked_hash
    asyncio.run(mesh.cleanup_socket())
    assert state.acked_layout is None and state.acked_hash is None
    assert state.patch_support is None and state.patch_support_device is None


def test_reconnected_session_first_push_is_full(dev, monkeypatch):
    push()
    asyncio.run(mesh.cleanup_socket())
    monkeypatch.setattr(state, "socket", dev)
    dev.is_running = True
    monkeypatch.setattr(state, "peer_list", [{"id": "dev-1"}])
    dev.calls.clear()
    edit_tint()
    out = push()
    assert dev.verbs() == ["apply-layout"] and "no acknowledged base" in out


def test_device_change_clears_base(dev, monkeypatch):
    push()
    monkeypatch.setattr(state, "peer_list", [{"id": "dev-2"}])
    dev.calls.clear()
    edit_tint()
    out = push()
    assert "patch-layout" not in dev.verbs()
    assert "no acknowledged base" in out
    assert state.acked_device_id == "dev-2"


def test_begin_edit_from_device_clears_base(dev):
    push()
    dev.replies["get-current-layout"] = {"layout": base_layout()}
    asyncio.run(buffer_tools.begin_edit(from_device=True))
    assert state.acked_layout is None and state.acked_hash is None


# ─── builder accepts the differ's vocabulary ─────────────────────────────────

def test_builder_accepts_kit_ops_and_empty_when_allowed():
    ops = [{"op": "setTab", "tab": 0, "key": "title", "value": "Deux"},
           {"op": "add", "parent": 1, "control": {"id": "d", "type": "button"}, "index": 0},
           {"op": "move", "id": "c", "parent": "g", "index": 1}]
    assert protocol.build_patch_layout_request("sha256:a", ops)["ops"] == ops
    assert protocol.build_patch_layout_request("sha256:a", [], allow_empty=True) == {
        "base": "sha256:a", "ops": []}
    with pytest.raises(ValueError):
        protocol.build_patch_layout_request("sha256:a", [])
    with pytest.raises(ValueError):
        protocol.build_patch_layout_request("sha256:a", [{"op": "setTab", "tab": 0}])
