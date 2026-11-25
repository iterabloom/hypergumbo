# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which c.py binds a ``calls`` edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). c.py looks every name up in one
``NameResolver`` over the repository's functions, so each route carries the
lookup's own value: a direct call (``ast_call_direct``), a function passed as
an argument (``function_pointer_arg``) and a call through a pointer variable
the file initialises (``function_pointer``, the held function's lookup). Each
test finds the edge first (it must exist) and pins the value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.c import analyze_c

_FILES = {
    "src/a.c": (
        "void far_fn(void);\n"
        "void call_far(void) { far_fn(); }\n"
        "void run(void (*cb)(void)) {}\n"
        "void call_cb(void) { run(far_fn); }\n"
        "static void handler(void) {}\n"
        "void (*hook)(void) = handler;\n"
        "void call_ptr(void) { hook(); }\n"
    ),
    "src/b.c": "void far_fn(void) {}\n",
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo: Path = tmp_path_factory.mktemp("c_mark") / "proj"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return analyze_c(repo)


@pytest.mark.parametrize(("caller", "callee", "evidence"), [
    ("call_far", "far_fn", "ast_call_direct"),
    ("call_cb", "far_fn", "function_pointer_arg"),
    ("call_ptr", "handler", "function_pointer"),
])
def test_route_carries_the_lookups_value(analysis, caller, callee, evidence) -> None:
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type == "calls" and e.is_resolved and e.evidence_type == evidence
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
    ]
    assert len(found) == 1, (caller, callee, evidence)
    # An exact key of the repository-wide table: nothing scoped the name.
    assert found[0].meta["resolved_by"] == "name"
