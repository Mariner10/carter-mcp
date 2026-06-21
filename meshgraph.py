"""Live mesh graph — render the MeshSocket roster as a force-directed GraphData.

"Show me my mesh": the MCP already knows the live roster (get_nodes), so it can build
nodes/edges and push them to a CARGraph control to visualize the network in real time.
Pure builders; the socket roster fetch + emit live in server.
"""

from __future__ import annotations

ROLE_COLORS = {
    "server": "#FF9500",
    "editor": "#667eea",
    "viewer": "#34C759",
    "controller": "#4CC9F0",
    "monitor": "#B16CFF",
}
HUB_COLOR = "#FFFFFF"

# The event + valuePath the generated graph control listens on.
MESH_EVENT = "broadcast"
MESH_VALUE_PATH = "meshGraph"


def roster_to_graph(clients: list[dict], hub_label: str = "relay") -> dict:
    """Build {nodes, edges} from a roster: a central hub plus one node per client,
    colored by role, each edged to the hub."""
    nodes = [{"id": "__hub__", "label": hub_label, "color": HUB_COLOR, "group": "hub"}]
    edges = []
    for c in clients:
        cid = str(c.get("id") or c.get("name") or "node")
        role = (c.get("role") or "").lower()
        nodes.append({
            "id": cid,
            "label": c.get("name", cid),
            "color": ROLE_COLORS.get(role, "#9AA0A6"),
            "group": role or "peer",
        })
        edges.append({"source": "__hub__", "target": cid})
    return {"nodes": nodes, "edges": edges}


def build_mesh_layout(name: str = "Mesh") -> dict:
    """A single-tab layout with a full-bleed graph control bound to the mesh data."""
    return {
        "name": name,
        "version": 1,
        "accentColor": "#4CC9F0",
        "tabs": [{
            "title": "Mesh",
            "icon": "antenna.radiowaves.left.and.right",
            "grid": {"columns": 4, "rows": 8},
            "children": [{
                "type": "graph",
                "id": "mesh-graph",
                "position": [0, 0],
                "span": [8, 4],
                "graphConfig": {
                    "interactive": True, "draggable": True,
                    "showLabels": True, "showParticles": True,
                    "glowEnabled": True, "edgeCurved": True,
                },
                "sync": [{
                    "method": "meshsocket", "type": "listen",
                    "event": MESH_EVENT, "valuePath": MESH_VALUE_PATH,
                }],
            }],
        }],
    }
