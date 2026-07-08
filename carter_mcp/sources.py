"""carter-mcp sources — where control definitions and authoring demos come from.

The MCP runs **locally** but is a thin tool layer over the *current* truth rather
than a vendored copy of it. Two live sources are resolved at call time:

  • Control **DEFINITIONS** — the catalog (every control's schema, fields,
    defaultSpan) and rendered doc prose — come from the **website**:
    ``carterbeaudoin.net/CAR-TER/catalog.json``, the same bundle the public docs
    site is built from. It's fetched once, cached on disk with a TTL, and
    materialized into a docs directory so the existing ``carterkit.catalog``
    parser can read it unchanged. Resolution order:
        local override → network → fresh-enough cache → bundled carterkit → repo.

  • Authoring **DEMOS** / "how to write code" — the documented example snippets
    and the codegen/builder engine — come from the installed **carterkit**, and
    we check PyPI for a newer release so the model can tell the user to upgrade a
    stale kit.

``sources_status()`` rolls all of this (plus an optional paired-device app
version) into one drift report for the ``check_sources`` MCP tool. The model is
expected to consult it — and the paired device's reported app version — to catch
the case where the website definitions, the local kit, and the phone's app have
drifted out of sync.

Everything here is best-effort and degrades gracefully: no network, no cache, and
a missing kit each fall through to the next source instead of raising.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ─── Configuration (all env-overridable) ──────────────────────────────────────

CATALOG_URL = os.environ.get(
    "CARTER_CATALOG_URL", "https://carterbeaudoin.net/CAR-TER/catalog.json")
PYPI_URL = os.environ.get(
    "CARTER_PYPI_URL", "https://pypi.org/pypi/carterkit/json")
CACHE_DIR = Path(os.environ.get(
    "CARTER_CACHE_DIR", str(Path.home() / ".cache" / "carter-mcp")))
# How long a fetched catalog / PyPI lookup is trusted before we try the network again.
CATALOG_TTL = int(os.environ.get("CARTER_CATALOG_TTL", "3600"))      # 1 hour
PYPI_TTL = int(os.environ.get("CARTER_PYPI_TTL", "21600"))           # 6 hours
# Force fully-offline operation (cache / bundled kit only — never touch the network).
OFFLINE = os.environ.get("CARTER_OFFLINE", "").lower() in ("1", "true", "yes")
# Dev escape hatch: point definitions at a local ControlDocs checkout.
LOCAL_CONTROLDOCS = os.environ.get("CARTER_LOCAL_CONTROLDOCS", "")
HTTP_TIMEOUT = int(os.environ.get("CARTER_HTTP_TIMEOUT", "12"))

_USER_AGENT = "carter-mcp/sources"

# Repo-relative last-resort copy of the docs (only meaningful in the dev workspace).
from carter_mcp.paths import REPO_CONTROLDOCS as _REPO_CONTROLDOCS

_BUNDLE_CACHE = CACHE_DIR / "catalog.json"
_META_CACHE = CACHE_DIR / "catalog.meta.json"
_DOCS_CACHE = CACHE_DIR / "controldocs"
_PYPI_CACHE = CACHE_DIR / "pypi-carterkit.json"

# In-process memo so repeated tool calls in one session don't re-read/re-hash disk.
_resolved: dict = {}


# ─── Small HTTP + disk helpers ────────────────────────────────────────────────

def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _age(path: Path) -> Optional[float]:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def fingerprint_dir(docs_dir) -> Optional[str]:
    """``sha256:…`` over the concatenated bytes of every ``*.md`` in a docs dir,
    sorted by name — the same recipe the website uses. Comparing two dirs' prints
    is the canonical "are these the same control definitions?" check."""
    d = Path(docs_dir)
    if not d.is_dir():
        return None
    h = hashlib.sha256()
    files = sorted(d.glob("*.md"))
    if not files:
        return None
    for f in files:
        try:
            h.update(f.read_bytes())
        except OSError:
            return None
    return "sha256:" + h.hexdigest()


# ─── Website catalog bundle (control definitions) ─────────────────────────────

def _bundle_is_valid(bundle) -> bool:
    return (isinstance(bundle, dict)
            and isinstance(bundle.get("docs"), dict)
            and isinstance(bundle.get("catalog"), dict)
            and bool(bundle["docs"]))


def fetch_catalog_bundle(force: bool = False) -> tuple[Optional[dict], str]:
    """Return ``(bundle, origin)`` for the website control-definition bundle.

    origin ∈ {"network", "cache", "stale-cache", "none"}. Network is skipped when
    OFFLINE, or when a cached copy is younger than CATALOG_TTL (unless ``force``).
    On any network failure we fall back to whatever cache exists (even if stale)
    so the MCP keeps working on a plane.
    """
    cached = _read_json(_BUNDLE_CACHE)
    cache_age = _age(_BUNDLE_CACHE)
    fresh = cached is not None and cache_age is not None and cache_age < CATALOG_TTL

    if not force and fresh and _bundle_is_valid(cached):
        return cached, "cache"

    if not OFFLINE:
        try:
            bundle = _http_get_json(CATALOG_URL)
            if _bundle_is_valid(bundle):
                _write_json(_BUNDLE_CACHE, bundle)
                _write_json(_META_CACHE, {
                    "url": CATALOG_URL,
                    "fetched_at": time.time(),
                    "fingerprint": (bundle.get("manifest") or {}).get("fingerprint"),
                })
                _materialize_docs(bundle)
                return bundle, "network"
        except (urllib.error.URLError, ValueError, OSError, TimeoutError):
            pass  # fall through to cache

    if _bundle_is_valid(cached):
        return cached, ("cache" if fresh else "stale-cache")
    return None, "none"


def _materialize_docs(bundle: dict) -> Optional[Path]:
    """Write the bundle's per-control markdown into the cache as a docs dir the
    ``carterkit.catalog`` parser can consume. Returns the dir (or None)."""
    docs = bundle.get("docs") or {}
    if not docs:
        return None
    _DOCS_CACHE.mkdir(parents=True, exist_ok=True)
    # Drop stale files so a removed control doesn't linger.
    for old in _DOCS_CACHE.glob("*.md"):
        if old.stem not in docs:
            try:
                old.unlink()
            except OSError:
                pass
    for node_id, entry in docs.items():
        md = entry.get("markdown")
        if isinstance(md, str):
            (_DOCS_CACHE / f"{node_id}.md").write_text(md, encoding="utf-8")
    return _DOCS_CACHE


# ─── carterkit (authoring demos / "how to write code") ────────────────────────

def carterkit_controldocs_dir() -> Optional[Path]:
    """The ControlDocs bundled inside the installed carterkit (the demos source)."""
    try:
        import carterkit
        d = carterkit.controldocs_dir()
        return d if Path(d).is_dir() else None
    except Exception:
        return None


def installed_carterkit_version() -> Optional[str]:
    try:
        import importlib.metadata as md
        return md.version("carterkit")
    except Exception:
        try:
            import carterkit
            v = getattr(carterkit, "__version__", None)
            return v if v and v != "0+unknown" else None
        except Exception:
            return None


def latest_carterkit_version(force: bool = False) -> Optional[str]:
    """Latest carterkit release on PyPI (cached, best-effort, None when offline)."""
    cached = _read_json(_PYPI_CACHE)
    age = _age(_PYPI_CACHE)
    if not force and cached and age is not None and age < PYPI_TTL:
        return cached.get("version")
    if not OFFLINE:
        try:
            data = _http_get_json(PYPI_URL)
            version = (data.get("info") or {}).get("version")
            if version:
                _write_json(_PYPI_CACHE, {"version": version, "fetched_at": time.time()})
                return version
        except (urllib.error.URLError, ValueError, OSError, TimeoutError):
            pass
    return cached.get("version") if cached else None


def _ver_tuple(v: Optional[str]):
    if not v:
        return ()
    out = []
    for part in v.split("+")[0].split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def carterkit_is_current() -> Optional[bool]:
    """True/False if installed kit is up to date with PyPI; None if unknown."""
    inst, latest = installed_carterkit_version(), latest_carterkit_version()
    if not inst or not latest:
        return None
    return _ver_tuple(inst) >= _ver_tuple(latest)


# ─── Resolved docs directories (what the rest of the MCP consumes) ────────────

def definitions_docs_dir() -> Path:
    """Docs dir for the control **catalog + doc prose** (control definitions).

    Website-first, with graceful fallback. Memoized per process.
    """
    if "definitions" in _resolved:
        return _resolved["definitions"]

    chosen: Optional[Path] = None
    if LOCAL_CONTROLDOCS and Path(LOCAL_CONTROLDOCS).is_dir():
        chosen = Path(LOCAL_CONTROLDOCS)
    else:
        bundle, origin = fetch_catalog_bundle()
        if bundle is not None:
            # Network path already materialized; ensure cache dir is populated.
            if not list(_DOCS_CACHE.glob("*.md")):
                _materialize_docs(bundle)
            if list(_DOCS_CACHE.glob("*.md")):
                chosen = _DOCS_CACHE
    if chosen is None:
        chosen = carterkit_controldocs_dir()
    if chosen is None and _REPO_CONTROLDOCS.is_dir():
        chosen = _REPO_CONTROLDOCS
    if chosen is None:
        raise RuntimeError(
            "No control definitions available: website unreachable, no cache, "
            "carterkit not installed, and no local ControlDocs checkout.")
    _resolved["definitions"] = chosen
    return chosen


def demos_docs_dir() -> Path:
    """Docs dir for **example snippets / demos** — the installed carterkit, falling
    back to the same dir definitions resolved to."""
    if "demos" in _resolved:
        return _resolved["demos"]
    chosen = carterkit_controldocs_dir() or definitions_docs_dir()
    _resolved["demos"] = chosen
    return chosen


def reset_cache_memo() -> None:
    """Forget the per-process resolution (used by check_sources --refresh)."""
    _resolved.clear()


# ─── Status / drift report ────────────────────────────────────────────────────

def website_manifest() -> Optional[dict]:
    bundle, _ = fetch_catalog_bundle()
    return (bundle or {}).get("manifest") if bundle else None


def sources_status(device_info: Optional[dict] = None, refresh: bool = False) -> dict:
    """One structured snapshot of every source + a drift verdict.

    device_info (optional): the paired device's reply, e.g.
    ``{"appVersion": "1.2.0", "build": "42", "protocolVersion": 1,
       "catalogFingerprint": "sha256:…"}`` — included so the model can compare the
    phone's app against the website definitions and the local kit.
    """
    if refresh:
        reset_cache_memo()
    bundle, origin = fetch_catalog_bundle(force=refresh)
    manifest = (bundle or {}).get("manifest") or {}

    inst = installed_carterkit_version()
    latest = latest_carterkit_version(force=refresh)
    kit_current = carterkit_is_current()

    site_fp = manifest.get("fingerprint")
    kit_fp = fingerprint_dir(carterkit_controldocs_dir() or "")

    status = {
        "website": {
            "url": CATALOG_URL,
            "reachable": origin in ("network",),
            "origin": origin,                       # network | cache | stale-cache | none
            "schemaVersion": manifest.get("schemaVersion"),
            "protocolVersion": manifest.get("protocolVersion"),
            "carterkitVersion": manifest.get("carterkitVersion"),
            "controlCount": manifest.get("controlCount"),
            "fingerprint": site_fp,
            "generated": manifest.get("generated"),
        },
        "carterkit": {
            "installed": inst,
            "latest": latest,
            "upToDate": kit_current,
            "controldocsFingerprint": kit_fp,
        },
        "device": device_info or None,
        "config": {"offline": OFFLINE, "cacheDir": str(CACHE_DIR),
                   "catalogTTL": CATALOG_TTL},
    }

    # ── Verdict / drift detection ───────────────────────────────────────────
    issues: list[str] = []
    if origin == "none":
        issues.append("Website catalog unreachable and no cache — using the "
                      "carterkit-bundled definitions (may be older than the site).")
    elif origin == "stale-cache":
        issues.append("Using a stale cached catalog (network unavailable).")
    if kit_current is False:
        issues.append(f"carterkit is out of date (installed {inst}, latest "
                      f"{latest}) — run `pip install -U carterkit` to pull the "
                      f"newest authoring demos.")
    if site_fp and kit_fp and site_fp != kit_fp:
        issues.append("Website definitions and the local carterkit's bundled docs "
                      "differ (fingerprint mismatch) — examples may reference "
                      "fields not in the published catalog, or vice versa.")
    if device_info:
        dev_proto = device_info.get("protocolVersion")
        site_proto = manifest.get("protocolVersion")
        if dev_proto is not None and site_proto is not None and dev_proto != site_proto:
            issues.append(f"Paired app speaks protocol v{dev_proto} but the catalog "
                          f"targets v{site_proto} — update the app or expect some "
                          f"controls to be ignored.")
        dev_fp = device_info.get("catalogFingerprint")
        if dev_fp and site_fp and dev_fp != site_fp:
            issues.append("The paired app's control definitions differ from the "
                          "website's — the installed app is a different version "
                          "than the published docs.")

    status["issues"] = issues
    status["aligned"] = not issues
    return status


def format_status(status: dict) -> str:
    """Human/AI-readable rendering of ``sources_status``."""
    w, k, d = status["website"], status["carterkit"], status.get("device")
    lines = ["# CAR-TER source status", ""]

    healthy = w["origin"] in ("network", "cache")
    lines.append("## Control definitions — website")
    lines.append(f"- source: {w['url']}")
    lines.append(f"- status: {w['origin']}" + (" ✅" if healthy else " ⚠️"))
    if w.get("controlCount") is not None:
        lines.append(f"- controls: {w['controlCount']} · schema v{w.get('schemaVersion')}"
                     f" · protocol v{w.get('protocolVersion')}")
    if w.get("generated"):
        lines.append(f"- generated: {w['generated']} (carterkit {w.get('carterkitVersion')})")
    if w.get("fingerprint"):
        lines.append(f"- fingerprint: {w['fingerprint'][:23]}…")

    lines.append("")
    lines.append("## Authoring demos — carterkit")
    uptodate = {True: "up to date ✅", False: "OUT OF DATE ⚠️", None: "unknown"}[k["upToDate"]]
    lines.append(f"- installed: {k['installed'] or '?'} · latest: {k['latest'] or '?'} · {uptodate}")

    if d:
        lines.append("")
        lines.append("## Paired device — app")
        lines.append(f"- app version: {d.get('appVersion', '?')}"
                     + (f" (build {d['build']})" if d.get("build") else ""))
        if d.get("protocolVersion") is not None:
            lines.append(f"- protocol: v{d['protocolVersion']}")

    lines.append("")
    if status["aligned"]:
        # Only claim the device leg when a device was actually read back.
        who = ("website, carterkit, and device agree" if d else
               "website and carterkit agree — no device paired, so the app leg "
               "wasn't checked (pair one and re-run to verify)")
        lines.append(f"✅ **Aligned** — {who}.")
    else:
        lines.append("⚠️ **Drift detected:**")
        for i in status["issues"]:
            lines.append(f"- {i}")
    return "\n".join(lines)
