# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jomik: a Java call on a typed receiver whose method is nowhere on the
type's in-repo chain is a USE of the type, not a call to it.

``inherited_calls`` Site 2 resolves ``var.method()`` by the receiver's inferred
type: the method on the type, then on its in-repo ancestors. When neither has
it -- ``err.getMessage()`` on an exception subclass, ``repo.save(e)`` on a Spring
Data repository -- the callee is an external base's method. Java used to emit a
``calls`` edge to the TYPE there. It never names the callee, and as a ``calls``
edge it became a class hint for ``method_call_recovery``, which then bound any
other unresolved member call in the same caller that the type happens to
declare: jedis's ``RedisClusterClient.builder()...build()`` went to the
unrelated ``redis.clients.jedis.Builder.build`` 338 times. The edge is now
``uses`` (the caller depends on the type; DEEP bakeoff asked for that
controller -> repository dependency on spring-petclinic), and the call stays
unresolved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map

_FILES = {
    "src/app/MyErr.java": (
        "package app;\n\n"
        "public class MyErr extends RuntimeException {\n"
        "    public String describe() { return \"err\"; }\n"
        "}\n"
    ),
    "src/app/Caller.java": (
        "package app;\n\n"
        "public class Caller {\n"
        "    public String report(MyErr err) {\n"
        "        String m = err.getMessage();\n"
        "        return Factory.make().describe() + m;\n"
        "    }\n\n"
        "    public String direct(MyErr err) {\n"
        "        return err.describe();\n"
        "    }\n"
        "}\n"
    ),
    "src/app/OwnerRepository.java": (
        "package app;\n\n"
        "public interface OwnerRepository {\n"
        "    Owner findByLastName(String lastName);\n"
        "}\n"
    ),
    "src/app/OwnerController.java": (
        "package app;\n\n"
        "public class OwnerController {\n"
        "    private OwnerRepository owners;\n\n"
        "    public void processCreation(Owner owner) {\n"
        "        this.owners.save(owner);\n"
        "    }\n"
        "}\n"
    ),
}


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    repo = tmp_path_factory.mktemp("javause")
    for name, text in _FILES.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    out = repo / "map.json"
    with pytest.MonkeyPatch.context() as mp:
        # A shared cache keyed by file content could answer for another run.
        mp.setenv("XDG_CACHE_HOME", str(repo / "cache"))
        run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _edges_from(graph: dict[str, Any], caller: str) -> list[tuple[str, str, str]]:
    """``(edge type, dst name, dst kind)`` for the fixture caller's edges."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    src = next(n["id"] for n in graph["nodes"] if n["name"] == caller)
    return [
        (e["type"], nodes[e["dst"]]["name"], nodes[e["dst"]]["kind"])
        for e in graph["edges"]
        if e["src"] == src and e["dst"] in nodes and e.get("is_resolved")
    ]


def test_an_external_base_method_is_a_use_of_the_type(graph: dict[str, Any]) -> None:
    edges = _edges_from(graph, "Caller.report")
    assert ("uses", "MyErr", "class") in edges
    assert ("calls", "MyErr", "class") not in edges


def test_the_type_is_no_class_hint_for_another_call(graph: dict[str, Any]) -> None:
    """``Factory.make().describe()`` is not ``err``'s: it stays unresolved."""
    assert ("calls", "MyErr.describe", "method") not in _edges_from(graph, "Caller.report")


def test_a_method_declared_on_the_type_still_resolves(graph: dict[str, Any]) -> None:
    """Control: Site 2 step 1 is untouched."""
    assert ("calls", "MyErr.describe", "method") in _edges_from(graph, "Caller.direct")


def test_a_spring_data_repository_call_keeps_the_dependency(
    graph: dict[str, Any],
) -> None:
    """``save`` is JpaRepository's; the controller still depends on the repo."""
    edges = _edges_from(graph, "OwnerController.processCreation")
    assert ("uses", "OwnerRepository", "interface") in edges
    assert ("calls", "OwnerRepository", "interface") not in edges
