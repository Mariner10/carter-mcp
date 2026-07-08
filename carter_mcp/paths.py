"""Workspace-relative paths.

carter-mcp normally sits beside the CAR-TER app repo in the CAR-TER workspace;
these paths resolve the app repo's bundled assets from there. In a standalone
checkout they simply don't exist and the features that read them degrade
gracefully (empty sample list, docs fall back to the website/carterkit sources).
"""

from pathlib import Path

# carter_mcp/paths.py → carter_mcp/ → carter-mcp/ → the CAR-TER workspace root.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

# The app repo nests its sources one level down (CAR-TER/CAR-TER/…).
_APP_REPO = WORKSPACE_ROOT / "CAR-TER" / "CAR-TER"

# Sample layouts bundled with the app — read by the sample-layout tools.
SAMPLE_LAYOUTS_DIR = _APP_REPO / "SampleLayouts"

# Last-resort local copy of the control docs (see sources.py resolution order).
REPO_CONTROLDOCS = _APP_REPO / "ControlDocs"
