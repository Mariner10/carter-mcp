"""Tests for meshgraph.py — roster → GraphData and the mesh layout."""

from carter_mcp import meshgraph


def test_roster_to_graph_hub_and_clients():
    clients = [
        {"id": "a1", "name": "iPhone", "role": "viewer"},
        {"id": "b2", "name": "server", "role": "server"},
    ]
    g = meshgraph.roster_to_graph(clients)
    ids = {n["id"] for n in g["nodes"]}
    assert "__hub__" in ids and "a1" in ids and "b2" in ids
    # every client edged to the hub
    assert all(e["source"] == "__hub__" for e in g["edges"])
    assert len(g["edges"]) == 2
    viewer = next(n for n in g["nodes"] if n["id"] == "a1")
    assert viewer["color"] == meshgraph.ROLE_COLORS["viewer"]


def test_roster_unknown_role_gets_default_color():
    g = meshgraph.roster_to_graph([{"id": "x", "name": "n", "role": "weird"}])
    node = next(n for n in g["nodes"] if n["id"] == "x")
    assert node["color"] == "#9AA0A6"


def test_roster_empty_is_just_hub():
    g = meshgraph.roster_to_graph([])
    assert len(g["nodes"]) == 1 and g["edges"] == []


def test_build_mesh_layout_has_graph_bound_to_mesh():
    layout = meshgraph.build_mesh_layout()
    graph = layout["tabs"][0]["children"][0]
    assert graph["type"] == "graph"
    sync = graph["sync"][0]
    assert sync["event"] == meshgraph.MESH_EVENT
    assert sync["valuePath"] == meshgraph.MESH_VALUE_PATH
