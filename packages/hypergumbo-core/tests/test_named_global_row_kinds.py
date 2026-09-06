# SPDX-License-Identifier: AGPL-3.0-or-later
"""A named global's rows are function-kind, so the coverage gate and the
classifier agree about the same call (WI-komun).

THE DEFECT. javascript.yaml declared ``console``, ``localStorage``,
``sessionStorage``, ``navigator``, (in duplicate) ``Deno`` and one ``fs`` row
(``createWriteStream``) under ``methods:``. The analyzer resolves a member call on a NAMED GLOBAL with the
module slot filled and no ``call_construct`` -- ``console.log(x)`` is
``javascript:console:0-0:log:unresolved``, the same shape as
``fs.readFileSync`` -- and ``classify_call`` matched every one of them. But
``method_starved_modules`` asks, per declared KIND, whether the analyzer handed
the catalogue a shape it could match: a method-kind row wants a
method-construct edge, none ever arrives for a named global, so the module was
"starved" and every clean verdict over a repository that logs was withheld
("method-shaped (console) but no method call edge": express NO-FSWRITE /
NO-SUBPROC, ioredis NO-SUBPROC, winston NO-NET / NO-SUBPROC in the WI-nolut
A/B) -- on calls the classifier had already adjudicated.

WHICH SIDE WAS WRONG. ``methods:`` means "called on an instance whose type
must be known"; a named global is resolved by NAME, which is the
function-shaped path. So the rows' KIND was the misdeclaration (INV-nular's
class, in the kind slot), and the fix is the rows, not a looser gate: loosening
``method_starved_modules`` for module-slotted calls would loosen it for every
language, while the class receivers (WebSocket, XMLHttpRequest, net.Socket,
dgram.Socket, EventSource, BroadcastChannel) keep their method rows and keep
starving when no method edge arrives -- :class:`TestAClassReceiverStillStarves`
is the control.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_core.verify_claims import method_starved_modules

NAMED_GLOBAL_ROWS = [
    ("console", "log"), ("console", "error"), ("console", "table"),
    ("localStorage", "setItem"), ("localStorage", "getItem"), ("localStorage", "clear"),
    ("sessionStorage", "setItem"), ("sessionStorage", "getItem"),
    ("navigator", "sendBeacon"),
    ("Deno", "readFile"), ("Deno", "stat"),
    ("fs", "createWriteStream"),
]

CLASS_RECEIVER_ROWS = [
    ("WebSocket", "send"), ("XMLHttpRequest", "open"), ("net.Socket", "write"),
    ("dgram.Socket", "send"), ("EventSource", "addEventListener"), ("BroadcastChannel", "postMessage"),
]


def _kinds(module: str) -> set[str]:
    catalog = load_catalog("javascript")
    return {p.kind for p in catalog.primitives if p.module == module}


class TestNamedGlobalRowsAreFunctionKind:
    @pytest.mark.parametrize("module,name", NAMED_GLOBAL_ROWS)
    def test_the_row_is_function_kind(self, module: str, name: str) -> None:
        catalog = load_catalog("javascript")
        kinds = {p.kind for p in catalog.primitives if p.module == module and p.name == name}
        assert kinds == {"function"}, (module, name, kinds)

    @pytest.mark.parametrize("module", sorted({m for m, _ in NAMED_GLOBAL_ROWS}))
    def test_the_module_declares_no_method_row_at_all(self, module: str) -> None:
        """One surviving method row would put the module back in the
        method-keyed set the gate starves."""
        assert "method" not in _kinds(module), module

    @pytest.mark.parametrize("module,name", CLASS_RECEIVER_ROWS)
    def test_a_class_receiver_keeps_its_method_rows(self, module: str, name: str) -> None:
        """CONTROL: these are instances whose type must be known; the
        analyzer emits a method construct for them (INV-misup)."""
        catalog = load_catalog("javascript")
        kinds = {p.kind for p in catalog.primitives if p.module == module and p.name == name}
        assert kinds == {"method"}, (module, name, kinds)


class TestTheClassifierStillMatchesTheSameCalls:
    @pytest.mark.parametrize("module,name", NAMED_GLOBAL_ROWS)
    def test_a_named_global_call_classifies(self, module: str, name: str) -> None:
        prim = classify_call({"javascript": load_catalog("javascript")},
                             f"javascript:{module}:0-0:{name}:unresolved", {})
        assert prim is not None and prim.module == module and prim.name == name


def _edges(*dsts: str, construct_on_first: str | None = None) -> list[dict]:
    """Call edges from one javascript function; the FIRST carries a construct
    so the language counts as having construct evidence (the gate skips
    languages that never stamp one)."""
    out = []
    for i, dst in enumerate(dsts):
        meta = {"call_construct": construct_on_first} if (i == 0 and construct_on_first) else {}
        out.append({"src": "javascript:src/app.js:1-9:main:function", "dst": dst, "type": "calls", "meta": meta})
    return out


class TestTheGateNoLongerStarvesANamedGlobal:
    def test_a_repo_that_only_logs_is_not_starved(self) -> None:
        """THE POINT. express / winston / ioredis: a console.log with no
        construct, beside one method-construct call elsewhere."""
        edges = _edges("javascript:external:0-0:write:unresolved",
                       "javascript:console:0-0:log:unresolved",
                       "javascript:localStorage:0-0:setItem:unresolved",
                       "javascript:navigator:0-0:sendBeacon:unresolved",
                       construct_on_first="method")
        assert method_starved_modules(edges, {"javascript": load_catalog("javascript")}) == []


class TestAClassReceiverStillStarves:
    def test_a_websocket_call_without_a_method_edge_still_starves(self) -> None:
        """CONTROL, the falsifiability half: a class receiver whose method
        rows were reached without a method construct is exactly what the gate
        exists to report."""
        edges = _edges("javascript:external:0-0:write:unresolved",
                       "javascript:WebSocket:0-0:send:unresolved",
                       construct_on_first="method")
        assert "WebSocket" in method_starved_modules(edges, {"javascript": load_catalog("javascript")})
