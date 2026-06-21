"""Tests for theming.py — color helpers, vibe presets, brand + free-text themes."""

import pytest

import theming


def test_parse_and_to_hex_roundtrip():
    assert theming.parse_hex("#667eea") == (0x66, 0x7E, 0xEA, 0xFF)
    assert theming.parse_hex("#fff") == (255, 255, 255, 255)
    assert theming.to_hex(102, 126, 234) == "#667EEA"


def test_with_alpha():
    assert theming.with_alpha("#FFFFFF", 0.5).upper() == "#FFFFFF80"


def test_mix_and_darken_lighten():
    assert theming.mix("#000000", "#FFFFFF", 0.5) == "#808080"
    assert theming.darken("#808080", 0.5) == "#404040"
    assert theming.lighten("#808080", 0.5) == "#C0C0C0"


def test_readable_on_contrast():
    assert theming.readable_on("#FFFFFF") == "#1C1C1E"   # dark text on light
    assert theming.readable_on("#000000") == "#FFFFFF"   # light text on dark


def test_vibe_theme_known_and_unknown():
    hud = theming.vibe_theme("hud")
    assert hud["accentColor"] == "#00FF66"
    assert hud["fontDesign"] == "monospaced"
    assert theming.vibe_theme("nope") is None


def test_brand_theme_uses_accent_and_contrast():
    t = theming.brand_theme("#FF2D55", dark=True)
    assert t["accentColor"] == "#FF2D55"
    assert t["foregroundColor"] == "#FFFFFF"   # on dark bg
    assert t["accentGradient"][0] == "#FF2D55"
    assert t["borderColor"].startswith("#FF2D55")


def test_brand_theme_normalizes_missing_hash():
    t = theming.brand_theme("00AAFF")
    assert t["accentColor"] == "#00AAFF"


def test_theme_for_keywords():
    assert theming.theme_for("make it a fighter jet HUD")["accentColor"] == "#00FF66"
    assert theming.theme_for("synthwave neon vibes")["accentColor"] == "#FF2D95"
    assert theming.theme_for("clean minimal apple style")["accentColor"] == "#007AFF"


def test_theme_for_hex_color_makes_brand():
    t = theming.theme_for("brand it around #7A04EB please")
    assert t["accentColor"] == "#7A04EB"


def test_theme_for_default():
    # no keyword, no hex -> midnight fallback
    assert theming.theme_for("something neutral")["accentColor"] == "#4CC9F0"


def test_bad_hex_raises():
    with pytest.raises(ValueError):
        theming.parse_hex("#xyz")
