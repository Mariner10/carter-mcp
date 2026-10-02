"""Derive (carter-bkm.5): validate_buffer accepts a derive layout, and check_sources
names `derive` when the paired app is older than the layout features.

Run with the carterkit checkout on the path (the venv carries the released kit):
    PYTHONPATH=<carterkit checkout> .venv/bin/python -m pytest -q
"""
import carterkit
import pytest
from carterkit.buffer import LayoutBuffer

from carter_mcp import content, sources, state
from carter_mcp.tools import buffer as buffer_tools

pytestmark = pytest.mark.skipif(not hasattr(carterkit, "derive"),
                                reason="needs a carterkit with derive (set PYTHONPATH to the checkout)")

DERIVE_LAYOUT = {
    "name": "Power", "version": 1,
    "derive": {"watts": {"mul": [{"control": "volts"}, {"control": "amps"}]},
               "kw": {"round": {"of": {"div": [{"derive": "watts"}, 1000]}, "places": 2}}},
    "tabs": [{"title": "Main", "icon": "bolt", "grid": {"columns": 2, "rows": 2}, "children": [
        {"type": "label", "id": "volts", "position": [0, 0],
         "sync": [{"event": "broadcast", "filter": {"msg_type": "plug"}, "valuePath": "v"}]},
        {"type": "label", "id": "amps", "position": [0, 1],
         "sync": [{"event": "broadcast", "filter": {"msg_type": "plug"}, "valuePath": "a"}]},
        {"type": "gauge", "id": "w", "position": [1, 0], "min": 0, "max": 1800,
         "sync": [{"method": "derive", "from": "watts"}]},
        {"type": "label", "id": "kwl", "position": [1, 1],
         "sync": [{"method": "derive", "from": "kw"}]},
    ]}],
}


@pytest.fixture
def bundled_catalog(monkeypatch):
    # Offline: lint against the kit's bundled catalog, not the website.
    monkeypatch.setattr(content, "build_catalog",
                        lambda include_theme=False: carterkit.controls(include_theme=include_theme))
    saved = state.work_buffer
    yield
    state.work_buffer = saved


def test_validate_buffer_accepts_a_derive_layout(bundled_catalog):
    state.work_buffer = LayoutBuffer.from_layout(DERIVE_LAYOUT)
    out = buffer_tools.validate_buffer()
    assert "valuePath" not in out
    assert "derive" not in out, out          # no unknown-key / bad-sync / ref finding


def test_validate_buffer_flags_a_bad_derive(bundled_catalog):
    bad = dict(DERIVE_LAYOUT, derive={"watts": {"frobnicate": [{"control": "volts"}]}})
    state.work_buffer = LayoutBuffer.from_layout(bad)
    out = buffer_tools.validate_buffer()
    assert "frobnicate" in out and "kw" in out    # unknown op + a sync from an undeclared id


def _status(monkeypatch, device_info):
    monkeypatch.setattr(sources, "fetch_catalog_bundle",
                        lambda force=False: ({"manifest": {"protocolVersion": 2,
                                                           "fingerprint": "sha256:site"}}, "cache"))
    monkeypatch.setattr(sources, "installed_carterkit_version", lambda: "0.15.0")
    monkeypatch.setattr(sources, "latest_carterkit_version", lambda force=False: "0.15.0")
    monkeypatch.setattr(sources, "carterkit_is_current", lambda: True)
    monkeypatch.setattr(sources, "fingerprint_dir", lambda d: "sha256:site")
    return sources.sources_status(device_info=device_info)


def test_check_sources_names_derive_for_an_older_app(monkeypatch):
    status = _status(monkeypatch, {"appVersion": "1.2.6", "protocolVersion": 2})
    assert not status["aligned"]
    text = sources.format_status(status)
    assert "`derive`" in text and "1.3" in text


def test_check_sources_drift_text_mentions_derive(monkeypatch):
    status = _status(monkeypatch, {"appVersion": "1.3.0", "protocolVersion": 1,
                                   "catalogFingerprint": "sha256:other"})
    issues = status["issues"]
    assert len(issues) == 2 and all("derive" in i for i in issues)


def test_check_sources_quiet_for_a_current_app(monkeypatch):
    status = _status(monkeypatch, {"appVersion": "1.3", "protocolVersion": 2,
                                   "catalogFingerprint": "sha256:site"})
    assert status["aligned"], status["issues"]
