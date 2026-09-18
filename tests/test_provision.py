"""Tests for the provision-device leg (spec 2026-08-23): the pure protocol shapes,
the credential file write (0600 / no-overwrite / key-change warning), and — the one
that matters most — result redaction: no credential secret may ever appear in a
tool result.
"""

import json
import os
import stat

import pytest

from carter_mcp import protocol
from carter_mcp.tools.device import write_credential_file


CREDENTIAL = {
    "url": "wss://connect.carterbeaudoin.net",
    "channel": "lights",
    "token": "SECRET-DEVICE-TOKEN",
    "role": "hub",
    "refresh": "SECRET-REFRESH",
    "did": "dv_abc123",
    "validator": "https://validator.example/prod",
    "k": "SECRETKEYBASE64==",
}


# ─── request/reply shapes ────────────────────────────────────────────────────

def test_build_provision_request_defaults():
    assert protocol.build_provision_request("lights") == {
        "channel": "lights", "role": "hub", "reuse_room_key": True}


def test_build_provision_request_explicit():
    assert protocol.build_provision_request("lab", role="viewer", reuse_room_key=False) == {
        "channel": "lab", "role": "viewer", "reuse_room_key": False}


def test_build_provision_result_request():
    assert protocol.build_provision_result_request("req-1") == {"request_id": "req-1"}


def test_extract_provision_pending():
    assert protocol.extract_provision_pending(
        {"ok": True, "status": "pending", "request_id": "r"}) == "r"
    assert protocol.extract_provision_pending({"ok": True, "status": "done"}) is None
    assert protocol.extract_provision_pending({"error": "busy"}) is None
    assert protocol.extract_provision_pending(None) is None


def test_provision_error_messages():
    assert "denied" in protocol.provision_error_message({"error": "denied"})
    assert "approval sheet" in protocol.provision_error_message({"error": "busy"})
    assert "Connect+" in protocol.provision_error_message({"error": "no-connect-session"})
    out = protocol.provision_error_message({"error": "mint-failed", "reason": "HTTP 403"})
    assert "HTTP 403" in out
    # Not errors:
    assert protocol.provision_error_message({"ok": True, "status": "pending"}) is None
    assert protocol.provision_error_message(None) is None


# ─── key-change warning ──────────────────────────────────────────────────────

def test_key_change_warning_fires_on_same_channel_new_key():
    old = json.dumps({"channel": "lights", "k": "OLDKEY"})
    warning = protocol.provision_key_change_warning(old, CREDENTIAL)
    assert warning and "ROOM KEY CHANGED" in warning and "lights" in warning


def test_key_change_warning_quiet_when_key_unchanged():
    old = json.dumps({"channel": "lights", "k": CREDENTIAL["k"]})
    assert protocol.provision_key_change_warning(old, CREDENTIAL) is None


def test_key_change_warning_quiet_across_channels_and_garbage():
    other = json.dumps({"channel": "garage", "k": "OLDKEY"})
    assert protocol.provision_key_change_warning(other, CREDENTIAL) is None
    assert protocol.provision_key_change_warning("not json", CREDENTIAL) is None
    assert protocol.provision_key_change_warning(json.dumps({"channel": "lights"}),
                                                 CREDENTIAL) is None


# ─── redaction ───────────────────────────────────────────────────────────────

def test_summary_contains_only_nonsecret_fields():
    out = protocol.format_provision_summary(
        CREDENTIAL, "/tmp/device.json", "reused-existing", expires_at=1756000000)
    # The allowed metadata is present…
    assert "dv_abc123" in out
    assert "lights" in out and "hub" in out
    assert "connect.carterbeaudoin.net" in out
    assert "/tmp/device.json" in out
    assert "reused" in out
    assert "1756000000" in out
    # …and no secret substring is.
    for field in protocol.PROVISION_SECRET_FIELDS:
        assert CREDENTIAL[field] not in out, f"secret field '{field}' leaked"
    assert "SECRET" not in out


def test_summary_key_verdicts_and_warning():
    fresh = protocol.format_provision_summary(CREDENTIAL, "/x", "fresh-channel-had-none")
    assert "fresh" in fresh
    forced = protocol.format_provision_summary(CREDENTIAL, "/x", "fresh-forced",
                                               warning="⚠️ ROOM KEY CHANGED …")
    assert "reuse_room_key=False" in forced
    assert "ROOM KEY CHANGED" in forced


# ─── file write ──────────────────────────────────────────────────────────────

def test_write_creates_parents_verbatim_0600(tmp_path):
    out = tmp_path / "deep" / "nested" / "device.json"
    warning, err = write_credential_file(str(out), CREDENTIAL)
    assert warning is None and err is None
    assert out.exists()
    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    # Verbatim: the file parses back to exactly the credential the device sent.
    assert json.loads(out.read_text()) == CREDENTIAL


def test_write_refuses_overwrite_by_default(tmp_path):
    out = tmp_path / "device.json"
    out.write_text("{}")
    warning, err = write_credential_file(str(out), CREDENTIAL)
    assert err and "Refusing to overwrite" in err
    assert out.read_text() == "{}", "the existing file must be untouched"


def test_overwrite_warns_on_key_change_and_tightens_mode(tmp_path):
    out = tmp_path / "device.json"
    out.write_text(json.dumps({"channel": "lights", "k": "OLDKEY"}))
    os.chmod(out, 0o644)  # a pre-existing loose file must end up 0600

    warning, err = write_credential_file(str(out), CREDENTIAL, overwrite=True)
    assert err is None
    assert warning and "ROOM KEY CHANGED" in warning
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert json.loads(out.read_text()) == CREDENTIAL


def test_overwrite_same_key_no_warning(tmp_path):
    out = tmp_path / "device.json"
    out.write_text(json.dumps({"channel": "lights", "k": CREDENTIAL["k"]}))
    warning, err = write_credential_file(str(out), CREDENTIAL, overwrite=True)
    assert warning is None and err is None
