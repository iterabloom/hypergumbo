# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Kotlin supertype written ``Outer.Nested`` keeps its nested name.

``class OkHttpClient : Call.Factory, WebSocket.Factory`` recorded the bases
``Call`` and ``WebSocket`` (the first identifier of each type), so the client
"implemented" the interface ``WebSocket`` and, once Kotlin had a hierarchy
walker, ``client.close()`` was bound to ``WebSocket.close`` (measured on
okhttp: 4 of 26 newly resolved calls).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

_FILES = {
    "okhttp3/Call.kt": "package okhttp3\n\ninterface Call {\n    fun cancel()\n    fun interface Factory {\n        fun newCall(): Call\n    }\n}\n",
    "okhttp3/WebSocket.kt": "package okhttp3\n\ninterface WebSocket {\n    fun close(): Boolean\n    fun interface Factory {\n        fun newWebSocket(): WebSocket\n    }\n}\n",
    "okhttp3/OkHttpClient.kt": "package okhttp3\n\nopen class OkHttpClient : Call.Factory, WebSocket.Factory {\n    override fun newCall(): Call = TODO()\n    override fun newWebSocket(): WebSocket = TODO()\n}\n",
    "okhttp3/Use.kt": "package okhttp3\n\nfun use(client: OkHttpClient) {\n    client.close()\n}\n",
}


def _write(root: Path) -> Path:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_the_bases_are_recorded_as_written(tmp_path: Path) -> None:
    result = analyze_kotlin(_write(tmp_path))
    client = next(s for s in result.symbols if s.name == "OkHttpClient")
    assert (client.meta or {}).get("base_classes") == ["Call.Factory", "WebSocket.Factory"]


def test_the_client_implements_the_nested_interfaces_and_has_no_close(tmp_path: Path) -> None:
    repo = _write(tmp_path / "repo")
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    client = next(n for n in data["nodes"] if n["name"] == "OkHttpClient")
    implemented = sorted(
        nodes[e["dst"]].get("qualified_name") or ""
        for e in data["edges"]
        if e["src"] == client["id"] and e["type"] in ("extends", "implements") and e["dst"] in nodes
    )
    assert implemented == ["okhttp3.Call.Factory", "okhttp3.WebSocket.Factory"], implemented
    use = next(n for n in data["nodes"] if n["name"] == "use")
    bound = [
        nodes[e["dst"]]["name"] for e in data["edges"]
        if e["src"] == use["id"] and e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
    ]
    assert "WebSocket.close" not in bound, bound
