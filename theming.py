"""Generative theming — turn a "vibe" or a brand color into a ThemeConfig.

Emits dicts using the real ThemeConfig keys (Models/ThemeConfig.swift): pageBackground,
accentColor, accentGradient, foregroundColor, secondaryColor, surfacePrimary,
borderColor, cornerRadius, fontDesign, blurEnabled, etc. Pure logic.
"""

from __future__ import annotations

import re
from typing import Optional

# ─── Color helpers ───────────────────────────────────────────────────────────


def parse_hex(s: str) -> tuple[int, int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        s += "FF"
    if len(s) != 8:
        raise ValueError(f"bad hex color: {s!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore


def to_hex(r: int, g: int, b: int, a: int = 255) -> str:
    clamp = lambda v: max(0, min(255, int(round(v))))
    body = f"#{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"
    return body if a >= 255 else body + f"{clamp(a):02X}"


def with_alpha(hex_color: str, alpha: float) -> str:
    r, g, b, _ = parse_hex(hex_color)
    return to_hex(r, g, b, int(round(alpha * 255)))


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba, _ = parse_hex(a)
    rb, gb, bb, _ = parse_hex(b)
    return to_hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def darken(hex_color: str, amount: float = 0.2) -> str:
    return mix(hex_color, "#000000", amount)


def lighten(hex_color: str, amount: float = 0.2) -> str:
    return mix(hex_color, "#FFFFFF", amount)


def relative_luminance(hex_color: str) -> float:
    r, g, b, _ = parse_hex(hex_color)

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def readable_on(bg: str) -> str:
    """Black or white text, whichever reads better on the background."""
    return "#1C1C1E" if relative_luminance(bg) > 0.4 else "#FFFFFF"


# ─── Curated vibes ───────────────────────────────────────────────────────────

VIBES: dict[str, dict] = {
    "hud": {
        "pageBackground": "#000000", "accentColor": "#00FF66",
        "foregroundColor": "#C8FFD4", "secondaryColor": "#00CC55",
        "surfacePrimary": "#00FF6611", "borderColor": "#00FF6633",
        "cornerRadius": 4, "borderWidth": 1, "fontDesign": "monospaced",
        "blurEnabled": False,
    },
    "synthwave": {
        "pageBackground": "#1A0B2E", "accentColor": "#FF2D95",
        "accentGradient": ["#FF2D95", "#7A04EB"],
        "foregroundColor": "#FFE9FF", "secondaryColor": "#B16CFF",
        "surfacePrimary": "#FF2D9514", "borderColor": "#FF2D9533",
        "cornerRadius": 14, "blurEnabled": True,
    },
    "apple-clean": {
        "pageBackground": "#F2F2F7", "accentColor": "#007AFF",
        "foregroundColor": "#1C1C1E", "secondaryColor": "#8E8E93",
        "surfacePrimary": "#FFFFFFF2", "surfaceSecondary": "#FFFFFF",
        "borderColor": "#D1D1D6", "cornerRadius": 12, "blurEnabled": True,
        "fontDesign": "rounded",
    },
    "terminal": {
        "pageBackground": "#0B0B0B", "accentColor": "#33FF33",
        "foregroundColor": "#33FF33", "secondaryColor": "#1FAF1F",
        "surfacePrimary": "#11FF1108", "borderColor": "#1FAF1F55",
        "cornerRadius": 2, "fontDesign": "monospaced", "blurEnabled": False,
    },
    "midnight": {
        "pageBackground": "#0D1B2A", "accentColor": "#4CC9F0",
        "foregroundColor": "#E0E1DD", "secondaryColor": "#778DA9",
        "surfacePrimary": "#1B263B", "borderColor": "#415A7755",
        "cornerRadius": 16, "blurEnabled": True,
    },
    "sunset": {
        "pageBackgroundGradient": ["#FF512F", "#DD2476"], "accentColor": "#FFD166",
        "foregroundColor": "#FFFFFF", "secondaryColor": "#FFE3B3",
        "surfacePrimary": "#FFFFFF1A", "borderColor": "#FFFFFF33",
        "cornerRadius": 18, "blurEnabled": True,
    },
}

# free-text keyword -> vibe name
_VIBE_KEYWORDS = [
    (re.compile(r"hud|cockpit|jet|fighter|military|aviation|aircraft", re.I), "hud"),
    (re.compile(r"synthwave|neon|retro|vaporwave|80s|cyberpunk", re.I), "synthwave"),
    (re.compile(r"apple|clean|minimal|ios|light|bright|simple", re.I), "apple-clean"),
    (re.compile(r"terminal|hacker|matrix|crt|console|green screen", re.I), "terminal"),
    (re.compile(r"midnight|ocean|blue|calm|night|deep", re.I), "midnight"),
    (re.compile(r"sunset|sunrise|warm|dusk|orange|fire", re.I), "sunset"),
]

_HEX_RE = re.compile(r"#?[0-9a-fA-F]{6}\b")


def vibe_theme(name: str) -> Optional[dict]:
    v = VIBES.get(name.strip().lower())
    return dict(v) if v else None


def brand_theme(accent: str, dark: bool = True) -> dict:
    """A theme built around a brand accent color."""
    accent = accent if accent.startswith("#") else "#" + accent
    parse_hex(accent)  # validate
    bg = "#0E0E12" if dark else "#F7F7FA"
    fg = readable_on(bg)
    return {
        "pageBackground": bg,
        "accentColor": accent,
        "accentGradient": [accent, darken(accent, 0.25)],
        "foregroundColor": fg,
        "secondaryColor": with_alpha(fg, 0.6),
        "surfacePrimary": with_alpha(fg, 0.06),
        "borderColor": with_alpha(accent, 0.3),
        "cornerRadius": 14,
        "blurEnabled": True,
    }


def theme_for(description: str) -> dict:
    """Map a free-text description (or a hex color) to a theme. Falls back to a brand
    theme if the text contains a hex color, else 'midnight'."""
    for pattern, name in _VIBE_KEYWORDS:
        if pattern.search(description):
            return vibe_theme(name)  # type: ignore
    m = _HEX_RE.search(description)
    if m:
        return brand_theme(m.group(0))
    return vibe_theme("midnight")  # type: ignore
