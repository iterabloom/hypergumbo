# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-noham Part A: py.py stamps a ``receiver_type_hint`` on its final
unresolved method-call edge whenever the receiver's type is GENUINELY
inferred (a ``var_types``-tracked param/local, or a bare local class name for
a ``Class.staticmethod()``/``classmethod()`` call). The hint lets the
``inherited_calls`` linker's strict INV-fahub Site-2 mode resolve the method on
the concrete type.

Two invariants this file locks:

* **Taint-safety by construction** — the analyzer edge STAYS ``is_resolved=False``
  with an unchanged ``:unresolved`` dst; the linker is the sole minter of the
  resolved edge. The hint only ADDS a ``meta`` key.
* **INV-fahub bias-to-unresolved** — an untyped / duck receiver gets NO hint, so
  it cannot be bound to an arbitrary same-named internal def downstream.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _method_call_edges(res, attr_name: str):
    """Unresolved method-call edges whose dst is the given attr short name."""
    dst = f"python:external:0-0:{attr_name}:unresolved"
    return [e for e in res.edges if e.edge_type == "calls" and e.dst == dst]


class TestReceiverTypeHintEmission:
    def test_typed_receiver_unresolved_method_gets_hint(self, tmp_path: Path) -> None:
        # x is typed to in-repo Foo (param annotation -> var_types), but Foo
        # defines no ``helper``, so Case 2c's direct lookup misses and the call
        # falls to the final unresolved emit. The hint carries Foo's name.
        src = (
            "class Foo:\n"
            "    pass\n"
            "def f(x: Foo):\n"
            "    return x.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "helper")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False
        assert (e.meta or {}).get("receiver_type_hint") == "Foo"

    def test_local_class_static_call_gets_hint(self, tmp_path: Path) -> None:
        # Foo.bar() — a bare LOCAL class name as receiver. py.py has no direct
        # case for it, so it reaches the final else; the hint carries "Foo".
        src = (
            "class Foo:\n"
            "    @staticmethod\n"
            "    def bar():\n"
            "        return 1\n"
            "def use():\n"
            "    return Foo.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "bar")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "Foo"
        assert edges[0].is_resolved is False

    def test_untyped_receiver_gets_no_hint(self, tmp_path: Path) -> None:
        # x has no inferable type — INV-fahub demands NO hint (bias to
        # unresolved) so it cannot bind to an arbitrary same-named def.
        src = (
            "def f(x):\n"
            "    return x.method()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "method")
        assert len(edges) == 1
        assert "receiver_type_hint" not in (edges[0].meta or {})

    def test_typed_receiver_direct_method_gets_hint(self, tmp_path: Path) -> None:
        # Even when ``helper`` IS directly on the inferred type, single-file
        # ``extract_nodes`` cannot resolve it (methods are keyed by short name
        # in local_symbols; sym_by_path_name is unpopulated), so the call
        # reaches the final else and gets the hint. The full pipeline then
        # resolves it via the linker (see
        # TestNohamRecallEndToEnd.test_typed_receiver_instance_method_resolves).
        src = (
            "class Foo:\n"
            "    def helper(self):\n"
            "        return 1\n"
            "def f(x: Foo):\n"
            "    return x.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "helper")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "Foo"


class TestReceiverHintScopeGuard:
    """INV-fahub: a bare-name receiver that is a function-LOCAL binding (param
    or assignment) shadowing a module-level class is under-determined — its real
    runtime type is unknown — so it must get NO hint, even though the file-global
    ``local_symbols`` still resolves the name to the class. Only a receiver that
    is genuinely a reference to the class (not locally bound) may be hinted.
    """

    def test_local_var_shadowing_class_gets_no_hint(self, tmp_path: Path) -> None:
        # `Response = parse(raw)` rebinds Response to an unknown; Response.get()
        # is <that object>.get(), NOT the class method. No hint.
        src = (
            "class Response:\n"
            "    def get(self, key):\n"
            "        return key\n"
            "def handle(raw):\n"
            "    Response = parse(raw)\n"
            "    return Response.get('x')\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "get")
        assert len(edges) == 1
        assert "receiver_type_hint" not in (edges[0].meta or {}), (
            "a local var shadowing a class was hinted to the class (INV-fahub)"
        )

    def test_unannotated_param_shadowing_class_gets_no_hint(
        self, tmp_path: Path
    ) -> None:
        # Parameter named Config (no annotation) is the arg, not the class.
        src = (
            "class Config:\n"
            "    @staticmethod\n"
            "    def get(k):\n"
            "        return k\n"
            "def handler(Config):\n"
            "    return Config.get('x')\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "get")
        assert len(edges) == 1
        assert "receiver_type_hint" not in (edges[0].meta or {})

    def test_genuine_class_reference_still_hinted(self, tmp_path: Path) -> None:
        # Foo is referenced (not locally bound) in use() — the guard must NOT
        # suppress the legitimate Class.staticmethod() recall.
        src = (
            "class Foo:\n"
            "    @staticmethod\n"
            "    def bar():\n"
            "        return 1\n"
            "def use():\n"
            "    return Foo.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "bar")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "Foo"

    def test_self_method_gets_enclosing_class_not_receiver_hint(
        self, tmp_path: Path
    ) -> None:
        # WI-hiziz PR-2: self.method() that falls through to the else gets an
        # ``enclosing_class`` hint (Site 1), NOT a ``receiver_type_hint`` — a
        # ``self`` receiver is LEXICAL (the enclosing class), so it dispatches
        # to the Site-1 MRO walker, not Site-2. (Full producer coverage of the
        # self-inherited path lives in ``test_py_self_inherited_hint.py``.)
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        return self.nonexistent_helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "nonexistent_helper")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("enclosing_class") == "C"
        assert "receiver_type_hint" not in (edges[0].meta or {})

    def test_module_level_var_shadowing_class_gets_no_hint(
        self, tmp_path: Path
    ) -> None:
        # The module-scope twin of the local-var shadow: a module-level rebinding
        # `Response = parse(raw)` shadows the class, so `Response.get()` at module
        # scope must get no hint (the module frame's local_names carry Response).
        src = (
            "class Response:\n"
            "    def get(self, key):\n"
            "        return key\n"
            "Response = parse(raw)\n"
            "result = Response.get('x')\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "get")
        assert len(edges) == 1
        assert "receiver_type_hint" not in (edges[0].meta or {})

    def test_module_level_genuine_class_reference_still_hinted(
        self, tmp_path: Path
    ) -> None:
        # A module-scope `Foo.bar()` where Foo is only ever the class (not
        # rebound) still gets the hint — the module guard must not over-suppress.
        src = (
            "class Foo:\n"
            "    @staticmethod\n"
            "    def bar():\n"
            "        return 1\n"
            "result = Foo.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "bar")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "Foo"

    def test_cls_method_gets_no_hint(self, tmp_path: Path) -> None:
        # cls.foo() inside a classmethod — cls is a local binding, not a class
        # name; no hint. The WI-hiziz PR-2 self-branch keys on receiver == "self"
        # only, so cls gets NEITHER receiver_type_hint NOR enclosing_class
        # (classmethod-inheritance recall is a deferred follow-up gap).
        src = (
            "class C:\n"
            "    @classmethod\n"
            "    def make(cls):\n"
            "        return cls.build()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _method_call_edges(res, "build")
        assert len(edges) == 1
        assert "receiver_type_hint" not in (edges[0].meta or {})
        assert "enclosing_class" not in (edges[0].meta or {})


class TestNohamRecallEndToEnd:
    """Full-pipeline: the hint py.py emits is resolved by the inherited_calls
    linker into a real in-repo ``calls`` edge (the deadness-FP fix)."""

    def test_local_staticmethod_resolves_via_linker(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text(
            "class Foo:\n"
            "    @staticmethod\n"
            "    def bar():\n"
            "        return 1\n"
            "def use():\n"
            "    return Foo.bar()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Foo.bar" and n.get("kind") == "method"
        )
        use = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "use" and n.get("kind") == "function"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == use
        ]
        resolved = [e for e in calls if e["dst"] == target]
        assert resolved, (
            "Foo.bar() did not resolve to the in-repo staticmethod via the "
            "receiver_type_hint -> inherited_calls linker chain"
        )
        assert "inherited-calls" in (resolved[0].get("origin") or [])
        assert resolved[0]["is_resolved"] is True

    def test_typed_receiver_instance_method_resolves(self, tmp_path: Path) -> None:
        # ``def consume(x: Foo): x.helper()`` with an in-repo Foo.helper — the
        # typed-receiver-DIRECT cross-file residual. Resolves end-to-end (via
        # Case 2c sym_by_path_name OR the hint->linker chain).
        (tmp_path / "models.py").write_text(
            "class Foo:\n"
            "    def helper(self):\n"
            "        return 1\n"
        )
        (tmp_path / "app.py").write_text(
            "from models import Foo\n"
            "def consume(x: Foo):\n"
            "    return x.helper()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Foo.helper" and n.get("kind") == "method"
        )
        consume = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "consume" and n.get("kind") == "function"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == consume
        ]
        assert any(e["dst"] == target for e in calls), (
            "x.helper() on an in-repo-typed receiver did not resolve to "
            "Foo.helper"
        )

    def test_inherited_calls_edges_are_pure_resolved(self, tmp_path: Path) -> None:
        # Linker-purity invariant: every edge minted by the inherited-calls
        # pass is is_resolved and points at a real in-repo symbol id (never an
        # :unresolved / :external_symbol dst). Guards the taint contract.
        (tmp_path / "m.py").write_text(
            "class Foo:\n"
            "    @staticmethod\n"
            "    def bar():\n"
            "        return 1\n"
            "def use():\n"
            "    return Foo.bar()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        linker_edges = [
            e for e in data["edges"]
            if "inherited-calls" in (e.get("origin") or [])
        ]
        assert linker_edges, "no inherited-calls edge minted (test is vacuous)"
        for e in linker_edges:
            assert e["is_resolved"] is True
            assert not str(e["dst"]).endswith(":unresolved")
            assert not str(e["dst"]).endswith(":external_symbol")
            assert e["dst"] in node_ids
