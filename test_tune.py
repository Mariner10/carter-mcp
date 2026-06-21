"""Tests for tune.py — auto-tuning gauges from observed samples."""

import tune


def test_suggest_range_bounds_observed_values():
    lo, hi = tune.suggest_range([40, 45, 50, 55, 60])
    assert 0 <= lo <= 40     # padded floor, never above the min
    assert hi >= 60          # padded above the max


def test_suggest_range_zeros_base_near_zero():
    # Values close to zero should anchor the floor at 0.
    lo, hi = tune.suggest_range([0.1, 0.3, 0.8])
    assert lo == 0
    assert hi >= 0.8


def test_suggest_range_handles_constant():
    lo, hi = tune.suggest_range([50, 50, 50])
    assert hi > lo


def test_segments_higher_is_worse_orders_green_to_red():
    segs = tune.suggest_segments([10, 20, 30, 40, 90], higher_is_worse=True)
    assert [s["color"] for s in segs] == [tune.GREEN, tune.AMBER, tune.RED]
    limits = [s["limit"] for s in segs]
    assert limits == sorted(limits)  # strictly increasing


def test_segments_higher_is_better_orders_red_to_green():
    segs = tune.suggest_segments([10, 20, 30, 40, 90], higher_is_worse=False)
    assert [s["color"] for s in segs] == [tune.RED, tune.AMBER, tune.GREEN]


def test_infer_unit():
    assert tune.infer_unit("cpu_temp") == "°C"
    assert tune.infer_unit("battery_pct") == "%"
    assert tune.infer_unit("latency_ms") == "ms"
    assert tune.infer_unit("random_thing") is None


def test_higher_is_worse_classification():
    assert tune.higher_is_worse("cpu_temp") is True
    assert tune.higher_is_worse("battery_level") is False


def test_tune_gauge_patch():
    patch = tune.tune_gauge({"id": "cpu_temp", "type": "gauge"},
                            [40, 50, 60, 70, 95], field_name="cpu_temp")
    assert "min" in patch and "max" in patch
    assert patch["min"] <= 40 and patch["max"] >= 95
    assert patch["segments"][0]["color"] == tune.GREEN  # higher is worse
    assert patch["segments"][-1]["limit"] == patch["max"]
