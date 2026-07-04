# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hiziz PR-2 (Site 1): py.py stamps an ``enclosing_class`` hint on the
final unresolved method-call edge for a ``self.method()`` call that Case 2a
could not resolve in-file (a cross-file INHERITED method, or an absent one).

The hint lets the ``inherited_calls`` linker's Site-1 resolver walk the method
up the enclosing class's C3 MRO (the Python walker landed in PR-1) and mint a
resolved ``calls`` edge to the ancestor's method — closing the deadness-FP
where a production method inherited from a cross-file base looked unreachable.

Invariants locked here (producer side):

* **Taint-safety by construction** — the analyzer edge STAYS ``is_resolved=False``
  with an unchanged ``:unresolved`` dst; only a ``meta`` key is added. The linker
  is the sole minter of the resolved edge.
* **Site-1, not Site-2** — the ``self`` receiver is *lexical* (the enclosing
  class), so it is stamped as ``enclosing_class`` ONLY, never
  ``receiver_type_hint``. ``_try_resolve`` checks ``receiver_type_hint`` before
  ``enclosing_class``, so a double-stamp would mis-route to Site-2 (FM4).
* **Crash guard + precision gate** — the branch fires only for
  ``caller_symbol.kind == "method"`` (guarantees a dotted ``qualified_name`` so
  ``split(".")[-2]`` cannot ``IndexError``) AND ``"self" in _caller_locals``
  (excludes a ``@staticmethod`` that references ``self`` — broken code whose
  receiver is undefined at runtime).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_lang_mainstream.py import (
    _receiver_type_id_trustworthy,
    extract_nodes,
)


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _unresolved_method_edges(res, attr_name: str):
    """Unresolved method-call edges whose dst is the given attr short name."""
    dst = f"python:external:0-0:{attr_name}:unresolved"
    return [e for e in res.edges if e.edge_type == "calls" and e.dst == dst]


class TestSelfMethodEnclosingClassHint:
    def test_self_inherited_crossfile_stamps_enclosing_class(
        self, tmp_path: Path
    ) -> None:
        # self.helper() where helper is not defined in-file (a cross-file
        # inherited method, or absent) falls to the terminal else. The producer
        # stamps the DIRECT enclosing class short name so Site-1 can walk it.
        src = (
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False
        assert e.dst.endswith(":helper:unresolved")
        assert (e.meta or {}).get("enclosing_class") == "Sub"
        assert (e.meta or {}).get("call_construct") == "method"
        assert "receiver_type_hint" not in (e.meta or {})

    def test_self_nested_class_enclosing_is_direct_class(
        self, tmp_path: Path
    ) -> None:
        # A method on a NESTED class gets the DIRECT (inner) class as its
        # enclosing_class — proving split(".")[-2] picks Inner, not Outer.
        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("enclosing_class") == "Inner"

    def test_annotated_self_routes_to_site2_not_enclosing_class(
        self, tmp_path: Path
    ) -> None:
        # An EXPLICIT `def run(self: Base)` annotation puts var_types["self"]=Base.
        # The self-branch's `receiver_name not in var_types` gate defers to the
        # var_types elif → receiver_type_hint="Base" (Site 2), NOT enclosing_class
        # (Site 1). The annotation is a deliberate static-type declaration; Site-2
        # on the annotated type resolves both base and mixin/host annotations,
        # whereas Site-1 on the enclosing class would walk the wrong MRO.
        src = (
            "class Base:\n"
            "    pass\n"
            "class Sub(Base):\n"
            "    def run(self: Base):\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "Base"
        assert "enclosing_class" not in (edges[0].meta or {})


    def test_self_undefined_method_still_stamps(self, tmp_path: Path) -> None:
        # The producer cannot know a method is undefined; it stamps
        # enclosing_class regardless. The linker's Site-1 walk finds nothing
        # and drops it (harmless over-stamp).
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        return self.nonexistent_helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "nonexistent_helper")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("enclosing_class") == "C"


class TestSelfMethodHintNegativeCases:
    """The crash-guard and precision-gate branches. Line-only coverage cannot
    enforce their protective value (FM6), so these negatives are mandatory —
    do NOT delete them."""

    def test_self_call_from_non_method_caller_emits_no_hint(
        self, tmp_path: Path
    ) -> None:
        # A module-level function with a param literally named `self`. caller
        # kind == "function" (dotless qualified_name) — the kind guard prevents
        # both an IndexError and a nonsensical enclosing_class.
        src = (
            "def f(self):\n"
            "    return self.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert len(edges) == 1
        assert "enclosing_class" not in (edges[0].meta or {})

    def test_staticmethod_no_self_param_emits_no_hint(
        self, tmp_path: Path
    ) -> None:
        # A @staticmethod has no `self` param, so `self` is not in the caller's
        # locals: the `"self" in _caller_locals` gate suppresses the hint (the
        # receiver is undefined at runtime; resolving it would resolve broken
        # code).
        src = (
            "class C:\n"
            "    @staticmethod\n"
            "    def foo():\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        assert "enclosing_class" not in (edges[0].meta or {})

    def test_staticmethod_with_self_param_emits_no_hint(
        self, tmp_path: Path
    ) -> None:
        # The review-caught anti-pattern: a @staticmethod that DECLARES a param
        # named `self` passes the `"self" in _caller_locals` gate (self IS a
        # param), but its `self` is an arbitrary argument — an under-determined
        # receiver. The `"staticmethod" not in _caller_decos` gate is the SOLE
        # suppressor here (isolates that gate). Without it, `self.bar()` would
        # mint a confidently-wrong 0.90 edge to the enclosing class's MRO.
        src = (
            "class C:\n"
            "    @staticmethod\n"
            "    def foo(self):\n"
            "        return self.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "bar")
        assert len(edges) == 1
        assert "enclosing_class" not in (edges[0].meta or {})

    def test_classmethod_referencing_self_emits_no_hint(
        self, tmp_path: Path
    ) -> None:
        # A @classmethod's param is `cls`, so a `self.x()` in its body references
        # an undefined `self` (not in locals). The `"self" in _caller_locals` gate
        # is the SOLE suppressor here (it is not a @staticmethod, so the deco gate
        # does not fire) — isolating that gate.
        src = (
            "class C:\n"
            "    @classmethod\n"
            "    def make(cls):\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        assert "enclosing_class" not in (edges[0].meta or {})

    def test_self_param_nested_function_kind_guard_suppresses(
        self, tmp_path: Path
    ) -> None:
        # Isolates the `kind == "method"` crash guard: a nested function that
        # declares its OWN `self` param has a DOTTED qualified_name ("run.inner")
        # AND `self` in its locals — so ONLY the kind guard (inner is kind
        # "function") can suppress it. Without the kind guard,
        # "run.inner".split(".")[-2] would wrongly yield the METHOD name "run" as
        # a class. The function-attributed edge must carry no enclosing_class.
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        def inner(self):\n"
            "            return self.helper()\n"
            "        return inner(self)\n"
        )
        res = _analyze(tmp_path, src)
        sym_kind = {s.id: s.kind for s in res.symbols}
        edges = _unresolved_method_edges(res, "helper")
        fn_edges = [e for e in edges if sym_kind[e.src] == "function"]
        assert fn_edges, "expected a function-attributed helper edge"
        assert all("enclosing_class" not in (e.meta or {}) for e in fn_edges)

    def test_self_in_nested_function_function_edge_gets_no_hint(
        self, tmp_path: Path
    ) -> None:
        # self.helper() inside a nested function of a method is emitted twice
        # (pre-existing double-walk): once attributed to the enclosing METHOD
        # (kind "method" — the closure-captured self IS a C instance, correctly
        # hinted enclosing_class="C") and once to the nested FUNCTION itself
        # (kind "function", qualified_name "run.inner"). The function-attributed
        # edge must carry NO enclosing_class. Here `inner` has no `self` param, so
        # `self` is a free var (not in inner's locals) and the `"self" in
        # _caller_locals` PRECISION gate is what suppresses the function edge (the
        # kind-guard-isolating variant is
        # test_self_param_nested_function_kind_guard_suppresses).
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        def inner():\n"
            "            return self.helper()\n"
            "        return inner()\n"
        )
        res = _analyze(tmp_path, src)
        sym_kind = {s.id: s.kind for s in res.symbols}
        edges = _unresolved_method_edges(res, "helper")
        fn_edges = [e for e in edges if sym_kind[e.src] == "function"]
        assert fn_edges, "expected a function-attributed helper edge"
        assert all("enclosing_class" not in (e.meta or {}) for e in fn_edges)


class TestSelfMethodCase2aBoundary:
    """FM2: the feature fires only when Case 2a's file-global short-name lookup
    misses. A same-file method of that short name is intercepted earlier and
    resolves directly — so it never reaches the terminal else and gets no
    enclosing_class hint. Documents the cross-file-only boundary."""

    def test_self_sameclass_method_resolved_no_hint(self, tmp_path: Path) -> None:
        # helper is on the SAME class → Case 2a resolves it directly → a
        # resolved edge, no unresolved edge, no enclosing_class.
        src = (
            "class C:\n"
            "    def helper(self):\n"
            "        return 1\n"
            "    def run(self):\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        # No unresolved helper edge at all — Case 2a intercepted.
        assert _unresolved_method_edges(res, "helper") == []
        # And a resolved calls edge to C.helper exists.
        helper_id = next(
            s.id for s in res.symbols if s.name == "C.helper" and s.kind == "method"
        )
        resolved = [
            e for e in res.edges
            if e.edge_type == "calls" and e.is_resolved and e.dst == helper_id
        ]
        assert resolved

    def test_samefile_namesake_shadows_case2a_no_hint(
        self, tmp_path: Path
    ) -> None:
        # A module-level `def save()` shadows the short name in the file-global
        # symbol table, so self.save() in Sub resolves to it via Case 2a
        # (pre-existing last-write-wins) — never reaching the else. PR-2
        # neither fixes nor regresses this same-file namesake behavior.
        src = (
            "def save():\n"
            "    return 0\n"
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.save()\n"
        )
        res = _analyze(tmp_path, src)
        assert _unresolved_method_edges(res, "save") == []


class TestSelfInheritedEndToEnd:
    """Full pipeline (producer emit → inheritance-linker extends recovery →
    inherited_calls Site-1 C3 walk): a cross-file INHERITED ``self.method()``
    resolves to the ancestor's method. This is the deadness-FP fix — a
    production method inherited from a cross-file base is no longer flagged
    unreachable."""

    def test_self_inherited_crossfile_resolves(self, tmp_path: Path) -> None:
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "base.py").write_text(
            "class Base:\n"
            "    def helper(self):\n"
            "        return 1\n"
        )
        (tmp_path / "child.py").write_text(
            "from base import Base\n"
            "class Sub(Base):\n"
            "    def run(self):\n"
            "        return self.helper()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Base.helper" and n.get("kind") == "method"
        )
        run = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Sub.run" and n.get("kind") == "method"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == run and e["dst"] == target
        ]
        assert calls, (
            "self.helper() did not resolve to the cross-file inherited "
            "Base.helper via enclosing_class -> Site-1 C3 walk"
        )
        assert calls[0]["is_resolved"] is True
        assert "inherited-calls" in (calls[0].get("origin") or [])

    def test_annotated_self_resolves_via_site2_not_site1(
        self, tmp_path: Path
    ) -> None:
        # Review regression lock: an explicit `self: Widget` annotation (a class
        # NOT in the enclosing Handler's MRO) must resolve to Widget.render via
        # Site-2 on the annotation — NOT be preempted by a Site-1 walk of
        # Handler's MRO (which contains no `render`, losing the edge / risking a
        # wrong-namesake bind). Handler has no base and no `render`, so a Site-1
        # (enclosing_class) route would mint nothing.
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "widget.py").write_text(
            "class Widget:\n"
            "    def render(self):\n"
            "        return 1\n"
        )
        (tmp_path / "handler.py").write_text(
            "from widget import Widget\n"
            "class Handler:\n"
            "    def run(self: Widget):\n"
            "        return self.render()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Widget.render" and n.get("kind") == "method"
        )
        run = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Handler.run" and n.get("kind") == "method"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == run and e["dst"] == target
        ]
        assert calls, (
            "annotated self: Widget did not resolve self.render() to "
            "Widget.render via the receiver_type_hint -> Site-2 chain"
        )
        assert calls[0]["is_resolved"] is True

    def test_self_inherited_field_method_resolves_crossfile(
        self, tmp_path: Path
    ) -> None:
        # Site-3 end-to-end: `log` is declared on a cross-file PARENT Base
        # (self.log = log, log: Logger); the child Sub's self.log.info()
        # resolves to Logger.info via the parent-field walk. This is the
        # dependency-injection deadness-FP fix.
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "base.py").write_text(
            "class Logger:\n"
            "    def info(self):\n"
            "        return 1\n"
            "class Base:\n"
            "    def __init__(self, log: Logger):\n"
            "        self.log = log\n"
        )
        (tmp_path / "child.py").write_text(
            "from base import Base, Logger\n"
            "class Sub(Base):\n"
            "    def run(self):\n"
            "        return self.log.info()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Logger.info" and n.get("kind") == "method"
        )
        run = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Sub.run" and n.get("kind") == "method"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == run and e["dst"] == target
        ]
        assert calls, (
            "self.log.info() did not resolve to the cross-file inherited-field "
            "Logger.info via the Site-3 parent-field walk"
        )
        assert calls[0]["is_resolved"] is True
        assert (calls[0].get("meta") or {}).get(
            "evidence_type"
        ) == "ast_call_inherited_field"

    def test_own_field_external_type_stays_unresolved(
        self, tmp_path: Path
    ) -> None:
        # Shadow-safety lock: an OWN field whose type is not an in-tree class
        # (self.svc = Svc(), Svc undefined) has no parent field of that name, so
        # the Site-3 walk finds nothing -> NO ast_call_inherited_field edge (no
        # confidently-wrong resolution). NOTE: evidence_type is nested UNDER
        # meta in the serialized edge (Edge.to_dict), so the filter must read
        # (e.get("meta") or {}).get("evidence_type") — a top-level lookup is
        # always None and would make this lock vacuous (review finding).
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "m.py").write_text(
            "class Base:\n"
            "    def __init__(self):\n"
            "        self.svc = Svc()\n"
            "    def run(self):\n"
            "        return self.svc.process()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        inherited_field_edges = [
            e for e in data["edges"]
            if (e.get("meta") or {}).get("evidence_type")
            == "ast_call_inherited_field"
        ]
        assert inherited_field_edges == []

    def test_own_field_shadowing_parent_field_no_wrong_edge(
        self, tmp_path: Path
    ) -> None:
        # Review regression lock (own-field under-exclusion): Child re-declares
        # an inherited field name via an UNTYPED factory assignment
        # (self.handle = make_conn()), so class_field_types (typed-only) misses
        # it — but class_own_field_names captures the NAME. The Site-3 gate's
        # `field_name not in own_field_names` excludes it, so self.handle.send()
        # does NOT resolve to the PARENT's `handle: Logger` (a different type) —
        # no confidently-wrong ast_call_inherited_field edge to Logger.send.
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "lib.py").write_text(
            "class Logger:\n"
            "    def send(self):\n"
            "        return 1\n"
            "class DBConn:\n"
            "    def send(self):\n"
            "        return 2\n"
            "def make_conn():\n"
            "    return DBConn()\n"
        )
        (tmp_path / "app.py").write_text(
            "from lib import Logger, make_conn\n"
            "class Base:\n"
            "    def __init__(self, logger: Logger):\n"
            "        self.handle = logger\n"
            "class Child(Base):\n"
            "    def __init__(self):\n"
            "        self.handle = make_conn()\n"
            "    def run(self):\n"
            "        return self.handle.send()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        logger_send = next(
            (n["id"] for n in data["nodes"]
             if n.get("name") == "Logger.send" and n.get("kind") == "method"),
            None,
        )
        child_run = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Child.run" and n.get("kind") == "method"
        )
        wrong = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == child_run
            and e["dst"] == logger_send
        ]
        assert wrong == [], (
            "Child.run's own re-declared self.handle wrongly resolved to the "
            "parent field type Logger.send (own-field exclusion failed)"
        )


def _class_meta(res, class_name: str) -> dict:
    """meta dict of the named class symbol (empty dict if meta is None)."""
    sym = next(
        s for s in res.symbols if s.name == class_name and s.kind == "class"
    )
    return sym.meta or {}


class TestClassFieldsMetaAttach:
    """WI-hiziz PR-3 part (a): each class symbol carries
    meta["fields"] = {field_name: type_short_name} (from the __init__
    field-type scan), mirroring java.py — the single source of truth the
    inherited_calls Site-3 resolver's parent-walk reads."""

    def test_meta_fields_typed_param_field(self, tmp_path: Path) -> None:
        # self.db = db where db: Database (typed param) -> {"db": "Database"}.
        src = (
            "class Database:\n"
            "    pass\n"
            "class Repo:\n"
            "    def __init__(self, db: Database):\n"
            "        self.db = db\n"
        )
        res = _analyze(tmp_path, src)
        assert _class_meta(res, "Repo").get("fields") == {"db": "Database"}

    def test_meta_fields_constructor_field(self, tmp_path: Path) -> None:
        # self.log = Logger() (constructor) -> {"log": "Logger"}.
        src = (
            "class Logger:\n"
            "    pass\n"
            "class Svc:\n"
            "    def __init__(self):\n"
            "        self.log = Logger()\n"
        )
        res = _analyze(tmp_path, src)
        assert _class_meta(res, "Svc").get("fields") == {"log": "Logger"}

    def test_meta_fields_coexists_with_base_classes(self, tmp_path: Path) -> None:
        # A class WITH a base (meta already has base_classes) still gets fields
        # added without clobbering base_classes.
        src = (
            "class Foo:\n"
            "    pass\n"
            "class Base:\n"
            "    pass\n"
            "class Sub(Base):\n"
            "    def __init__(self, x: Foo):\n"
            "        self.x = x\n"
        )
        res = _analyze(tmp_path, src)
        meta = _class_meta(res, "Sub")
        assert meta.get("fields") == {"x": "Foo"}
        assert "base_classes" in meta

    def test_class_without_typed_field_has_no_fields_key(
        self, tmp_path: Path
    ) -> None:
        # No typed __init__ field -> no "fields" key (class_field_types empty).
        src = (
            "class C:\n"
            "    def __init__(self):\n"
            "        self.count = 0\n"
        )
        res = _analyze(tmp_path, src)
        assert "fields" not in _class_meta(res, "C")

    def test_meta_fields_multiple_fields(self, tmp_path: Path) -> None:
        src = (
            "class A:\n"
            "    pass\n"
            "class B:\n"
            "    pass\n"
            "class C:\n"
            "    def __init__(self, a: A, b: B):\n"
            "        self.a = a\n"
            "        self.b = b\n"
        )
        res = _analyze(tmp_path, src)
        assert _class_meta(res, "C").get("fields") == {"a": "A", "b": "B"}


class TestSelfFieldMethodHint:
    """WI-hiziz PR-3 part (b): self.field.method() that Case 2f could not
    resolve (an INHERITED field) gets inherited_field_receiver + enclosing_class
    hints (Site 3). The linker walks the enclosing class's parents for the
    field's type and resolves the method there."""

    def test_self_field_inherited_stamps_hints(self, tmp_path: Path) -> None:
        # self.log.info() where `log` is NOT an own field of Sub (inherited /
        # unknown) -> Site-3 hints on the unresolved edge.
        src = (
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.log.info()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "info")
        assert len(edges) == 1
        m = edges[0].meta or {}
        assert edges[0].is_resolved is False
        assert m.get("inherited_field_receiver") == "log"
        assert m.get("enclosing_class") == "Sub"
        assert m.get("call_construct") == "method"

    def test_self_field_nested_class_enclosing_is_direct_class(
        self, tmp_path: Path
    ) -> None:
        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            return self.f.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("enclosing_class") == "Inner"
        assert (edges[0].meta or {}).get("inherited_field_receiver") == "f"

    def test_self_field_own_field_method_missing_no_hint(
        self, tmp_path: Path
    ) -> None:
        # `dep` is an OWN typed field (in var_types); the Site-3 gate excludes
        # it (field_name in var_types) even when its method misses -> no
        # inherited_field_receiver hint (Case 2f owns own fields).
        src = (
            "class Dep:\n"
            "    pass\n"
            "class C:\n"
            "    def __init__(self, dep: Dep):\n"
            "        self.dep = dep\n"
            "    def run(self):\n"
            "        return self.dep.nonexistent()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "nonexistent")
        assert all(
            "inherited_field_receiver" not in (e.meta or {}) for e in edges
        )

    def test_self_field_untyped_own_field_no_hint(self, tmp_path: Path) -> None:
        # `conn` is an UNTYPED own field (self.conn = make_conn(), a factory
        # call — not captured in class_field_types, so NOT in var_types). It IS
        # captured by NAME in class_own_field_names, so the Site-3 gate's
        # `field_name not in own_field_names` conjunct excludes it -> no hint.
        # (Isolates that conjunct: without it, `conn` not in var_types would let
        # the hint emit.)
        src = (
            "class C:\n"
            "    def __init__(self):\n"
            "        self.conn = make_conn()\n"
            "    def run(self):\n"
            "        return self.conn.query()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "query")
        assert all(
            "inherited_field_receiver" not in (e.meta or {}) for e in edges
        )

    def test_self_field_non_method_caller_no_hint(self, tmp_path: Path) -> None:
        # Module-level function with a `self` param -> kind "function" -> no hint.
        src = (
            "def f(self):\n"
            "    return self.x.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_self_field_classmethod_no_hint(self, tmp_path: Path) -> None:
        # @classmethod: self is free/undefined (param is cls) -> no hint.
        src = (
            "class C:\n"
            "    @classmethod\n"
            "    def make(cls):\n"
            "        return self.x.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_self_field_staticmethod_with_self_param_no_hint(
        self, tmp_path: Path
    ) -> None:
        # @staticmethod declaring a `self` param -> under-determined receiver.
        src = (
            "class C:\n"
            "    @staticmethod\n"
            "    def foo(self):\n"
            "        return self.x.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_self_field_annotated_self_no_hint(self, tmp_path: Path) -> None:
        # def run(self: Base) puts self in var_types; enclosing_class is LEXICAL
        # and may differ from Base -> gate on `"self" not in var_types` excludes.
        src = (
            "class Base:\n"
            "    pass\n"
            "class C(Base):\n"
            "    def run(self: Base):\n"
            "        return self.x.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_self_field_non_self_receiver_no_hint(self, tmp_path: Path) -> None:
        # other.x.g() (receiver root is `other`, not `self`) -> not Site-3.
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        return other.x.g()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "g")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_deeply_nested_self_attr_chain_no_hint(self, tmp_path: Path) -> None:
        # self.a.b.method(): func.value.value is an Attribute (self.a), NOT a
        # Name "self" -> the Site-3 elif does not match (guards the isinstance
        # Name check). Excluded (a 3+-deep chain is out of Site-3 scope).
        src = (
            "class C:\n"
            "    def run(self):\n"
            "        return self.a.b.method()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "method")
        assert all("inherited_field_receiver" not in (e.meta or {}) for e in edges)

    def test_self_field_own_resolved_no_unresolved_edge(
        self, tmp_path: Path
    ) -> None:
        # Own field with an in-file type whose method exists -> Case 2f resolves
        # it directly; the Site-3 elif never steals a Case-2f hit.
        src = (
            "class Dep:\n"
            "    def process(self):\n"
            "        return 1\n"
            "class C:\n"
            "    def __init__(self, dep: Dep):\n"
            "        self.dep = dep\n"
            "    def run(self):\n"
            "        return self.dep.process()\n"
        )
        res = _analyze(tmp_path, src)
        # No unresolved `process` edge — Case 2f resolved it.
        assert _unresolved_method_edges(res, "process") == []


def _class_id(res, class_name: str, *, prefer: str = "first") -> str:
    """Concrete Symbol id of a named class. ``prefer`` selects among same-name
    classes: 'first' = smallest start_line (top-level / earliest), 'last' =
    largest (a nested / redefined namesake)."""
    syms = [s for s in res.symbols if s.kind == "class" and s.name == class_name]
    assert syms, f"no class named {class_name}"
    chosen = min(syms, key=lambda s: s.span.start_line) if prefer == "first" \
        else max(syms, key=lambda s: s.span.start_line)
    return chosen.id


class TestWiSupatProducerIds:
    """WI-supat (D3) producer: py.py threads a CONCRETE class id alongside the
    Site name hints. The enclosing-class id comes from an AUTHORITATIVE
    method->class map (immune to the bare-name last-write-wins clobber); the
    receiver-type id is stamped only when the type's short name is UNIQUE within
    the file. Every stamped edge stays is_resolved=False (taint-safe)."""

    # ---- Site-1 enclosing_class_id ----

    def test_self_inherited_stamps_enclosing_class_id(self, tmp_path: Path) -> None:
        src = (
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.helper()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "helper")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False  # taint-safe: only meta changed
        assert (e.meta or {}).get("enclosing_class") == "Sub"
        assert (e.meta or {}).get("enclosing_class_id") == _class_id(res, "Sub")

    def test_nested_class_enclosing_id_is_direct_class(self, tmp_path: Path) -> None:
        # THE REFUTATION LOCK: a top-level `Inner` + a nested `Outer.Inner`. A
        # method of the TOP-LEVEL Inner must stamp the TOP-LEVEL Inner's id — a
        # bare-name local_symbols.get('Inner') would return the BFS-last NESTED
        # Inner (the clobber), so this pins the authoritative method->class map.
        src = (
            "class Inner:\n"
            "    def run(self):\n"
            "        return self.top_helper()\n"
            "class Outer:\n"
            "    class Inner:\n"
            "        def deep(self):\n"
            "            return self.nested_helper()\n"
        )
        res = _analyze(tmp_path, src)
        top_edges = _unresolved_method_edges(res, "top_helper")
        assert len(top_edges) == 1
        top_id = _class_id(res, "Inner", prefer="first")
        nested_id = _class_id(res, "Inner", prefer="last")
        assert top_id != nested_id
        assert (top_edges[0].meta or {}).get("enclosing_class_id") == top_id
        # and the nested Inner's method stamps the NESTED id
        nested_edges = _unresolved_method_edges(res, "nested_helper")
        assert len(nested_edges) == 1
        assert (nested_edges[0].meta or {}).get("enclosing_class_id") == nested_id

    def test_sibling_nested_enclosing_id(self, tmp_path: Path) -> None:
        # Two sibling nested `Shared` classes (A.Shared, B.Shared). A.Shared's
        # method must stamp A.Shared's id, not the BFS-last B.Shared.
        src = (
            "class A:\n"
            "    class Shared:\n"
            "        def run(self):\n"
            "            return self.helper_a()\n"
            "class B:\n"
            "    class Shared:\n"
            "        def run(self):\n"
            "            return self.helper_b()\n"
        )
        res = _analyze(tmp_path, src)
        a_edges = _unresolved_method_edges(res, "helper_a")
        assert len(a_edges) == 1
        assert (a_edges[0].meta or {}).get("enclosing_class_id") == \
            _class_id(res, "Shared", prefer="first")

    # ---- Site-2 receiver_type_id (3 producer branches) ----

    def test_property_read_stamps_receiver_type_id(self, tmp_path: Path) -> None:
        # @property READ on a typed receiver (WI-gubar producer path).
        src = (
            "class Cfg:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f(c: Cfg):\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "val")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False
        assert (e.meta or {}).get("receiver_type_hint") == "Cfg"
        assert (e.meta or {}).get("receiver_type_id") == _class_id(res, "Cfg")

    def test_var_typed_receiver_stamps_receiver_type_id(self, tmp_path: Path) -> None:
        # A var_types-typed receiver whose method is unresolvable in-file (else).
        src = (
            "class Cfg:\n"
            "    pass\n"
            "def f(c: Cfg):\n"
            "    return c.compute()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "compute")
        assert len(edges) == 1
        e = edges[0]
        assert (e.meta or {}).get("receiver_type_hint") == "Cfg"
        assert (e.meta or {}).get("receiver_type_id") == _class_id(res, "Cfg")

    def test_bare_local_class_receiver_stamps_receiver_type_id(
        self, tmp_path: Path
    ) -> None:
        # Bare local CLASS receiver: Foo.bar() (static/classmethod py.py cannot
        # resolve directly).
        src = (
            "class Foo:\n"
            "    pass\n"
            "def f():\n"
            "    return Foo.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "bar")
        assert len(edges) == 1
        e = edges[0]
        assert (e.meta or {}).get("receiver_type_hint") == "Foo"
        assert (e.meta or {}).get("receiver_type_id") == _class_id(res, "Foo")

    def test_receiver_type_id_omitted_on_samename_collision(
        self, tmp_path: Path
    ) -> None:
        # Two same-short-name `Foo` classes in one file (top-level + nested) make
        # the bare-name inference clobber-prone → the uniqueness gate OMITS the
        # receiver id (falls back to name+guard), while still stamping the name.
        src = (
            "class Foo:\n"
            "    pass\n"
            "class Outer:\n"
            "    class Foo:\n"
            "        pass\n"
            "def f(c: Foo):\n"
            "    return c.compute()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "compute")
        assert len(edges) == 1
        e = edges[0]
        assert (e.meta or {}).get("receiver_type_hint") == "Foo"
        assert "receiver_type_id" not in (e.meta or {})

    # ---- Site-3 enclosing_class_id ----

    def test_self_field_inherited_stamps_enclosing_class_id(
        self, tmp_path: Path
    ) -> None:
        src = (
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.log.info()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "info")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False
        assert (e.meta or {}).get("inherited_field_receiver") == "log"
        assert (e.meta or {}).get("enclosing_class") == "Sub"
        assert (e.meta or {}).get("enclosing_class_id") == _class_id(res, "Sub")


def _mk_cls(name: str, path: str = "/a.py") -> Symbol:
    return Symbol(
        id=f"python:{path}:1-2:{name}:class", name=name, kind="class",
        language="python", path=path,
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


class TestReceiverTypeIdTrustworthy:
    """WI-supat (D3) review blocker: the receiver-type id is trustworthy only
    when its short name is file-unique AND (when the resolved type is the in-file
    class) not shadowed by a same-name import."""

    def test_samename_collision_untrusted(self) -> None:
        foo = _mk_cls("Foo")
        assert _receiver_type_id_trustworthy(
            foo, {"Foo": 2}, {}, {}, {"Foo": foo}
        ) is False

    def test_local_class_import_shadowed_untrusted(self) -> None:
        # local `class Foo` (recv_sym IS local_symbols["Foo"]) + `from b import
        # Foo` (name in imports) -> the import rebinds Foo at runtime -> untrusted.
        foo = _mk_cls("Foo")
        assert _receiver_type_id_trustworthy(
            foo, {"Foo": 1}, {"Foo": ("b", "Foo")}, {}, {"Foo": foo}
        ) is False

    def test_local_class_not_shadowed_trusted(self) -> None:
        foo = _mk_cls("Foo")
        assert _receiver_type_id_trustworthy(
            foo, {"Foo": 1}, {}, {}, {"Foo": foo}
        ) is True

    def test_imported_type_not_local_stays_trusted(self) -> None:
        # PRECISION: a correctly cross-file-resolved imported type (recv_sym is
        # NOT the local symbol of that name) keeps its id even though the name is
        # in imports -- preserving the cross-file collision-recovery.
        imported_foo = _mk_cls("Foo", path="/b.py")
        local_other = _mk_cls("Foo", path="/a.py")
        assert _receiver_type_id_trustworthy(
            imported_foo, {"Foo": 1}, {"Foo": ("b", "Foo")}, {},
            {"Foo": local_other},
        ) is True


class TestWiSupatReceiverIdProducerOmissions:
    """WI-supat (D3) review should_fix: the uniqueness-gate OMIT branch is
    behaviorally locked for all three receiver_type_id producers (not just
    var_types)."""

    def test_property_read_id_omitted_on_collision(self, tmp_path: Path) -> None:
        # A @property receiver type with a same-name nested twin -> count>1 -> the
        # id is omitted (hint still stamped).
        src = (
            "class Cfg:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "class Outer:\n"
            "    class Cfg:\n"
            "        pass\n"
            "def f(c: Cfg):\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "val")
        assert len(edges) == 1
        e = edges[0]
        assert (e.meta or {}).get("receiver_type_hint") == "Cfg"
        assert "receiver_type_id" not in (e.meta or {})

    def test_bare_local_class_id_omitted_on_collision(
        self, tmp_path: Path
    ) -> None:
        # Bare local `Foo.bar()` with two same-name Foo classes in one file ->
        # count>1 -> id omitted.
        src = (
            "class Foo:\n"
            "    pass\n"
            "class Outer:\n"
            "    class Foo:\n"
            "        pass\n"
            "def f():\n"
            "    return Foo.bar()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _unresolved_method_edges(res, "bar")
        assert len(edges) == 1
        e = edges[0]
        assert (e.meta or {}).get("receiver_type_hint") == "Foo"
        assert "receiver_type_id" not in (e.meta or {})


class TestWiSupatImportShadowBlocker:
    """WI-supat (D3) review BLOCKER repro: a local class shadowed by a same-name
    in-tree import must NOT mint a confidently-wrong resolved edge through the
    (wrong) local class's MRO."""

    def test_import_shadowed_receiver_no_wrong_edge(
        self, tmp_path: Path
    ) -> None:
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "base.py").write_text(
            "class LocalBase:\n"
            "    def compute(self):\n"
            "        return 1\n"
        )
        (tmp_path / "b.py").write_text(
            "class Foo:\n"
            "    def compute(self):\n"
            "        return 2\n"
        )
        # a.py defines a LOCAL `class Foo(LocalBase)` then shadows the name with
        # `from b import Foo`. The runtime receiver of `c: Foo` is b.Foo, but the
        # local-first annotation resolution picks a.Foo. WI-supat must NOT stamp
        # a.Foo's id (import-shadowed) -- else the linker binds LocalBase.compute.
        (tmp_path / "a.py").write_text(
            "from base import LocalBase\n"
            "class Foo(LocalBase):\n"
            "    pass\n"
            "from b import Foo\n"
            "def f(c: Foo):\n"
            "    return c.compute()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        localbase_compute = next(
            (n["id"] for n in data["nodes"]
             if n.get("name") == "LocalBase.compute"
             and n.get("kind") == "method"),
            None,
        )
        f_id = next(
            (n["id"] for n in data["nodes"]
             if n.get("name") == "f" and n.get("kind") == "function"),
            None,
        )
        assert f_id is not None and localbase_compute is not None
        wrong = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == f_id
            and e["dst"] == localbase_compute
        ]
        assert wrong == [], (
            "import-shadowed receiver_type_id minted a confidently-wrong "
            "resolved edge f -> LocalBase.compute (should bias to unresolved)"
        )


class TestWiSupatFieldTypeIdsProducer:
    """WI-supat (D3) PR-B producer: each class symbol carries a parallel
    meta['field_type_ids'] = {field: type_id} alongside meta['fields'], so the
    inherited_calls Site-3 resolver can disambiguate a same-short-name field
    TYPE. Per-field gated by the same trustworthiness check as receiver_type_id
    (file-unique type name AND not import-shadowed)."""

    def test_field_type_ids_typed_param(self, tmp_path: Path) -> None:
        # self.db = db where db: Database -> field_type_ids['db'] == Database.id.
        src = (
            "class Database:\n"
            "    pass\n"
            "class Repo:\n"
            "    def __init__(self, db: Database):\n"
            "        self.db = db\n"
        )
        res = _analyze(tmp_path, src)
        meta = _class_meta(res, "Repo")
        assert meta.get("fields") == {"db": "Database"}
        assert meta.get("field_type_ids") == {"db": _class_id(res, "Database")}

    def test_field_type_ids_constructor_field(self, tmp_path: Path) -> None:
        # self.log = Logger() -> field_type_ids['log'] == Logger.id.
        src = (
            "class Logger:\n"
            "    pass\n"
            "class Svc:\n"
            "    def __init__(self):\n"
            "        self.log = Logger()\n"
        )
        res = _analyze(tmp_path, src)
        meta = _class_meta(res, "Svc")
        assert meta.get("field_type_ids") == {"log": _class_id(res, "Logger")}

    def test_untyped_field_has_no_field_type_ids(self, tmp_path: Path) -> None:
        # A class whose only field is untyped (no resolvable type) gets neither
        # meta['fields'] nor meta['field_type_ids'].
        src = (
            "class Repo:\n"
            "    def __init__(self, x):\n"
            "        self.x = x\n"
        )
        res = _analyze(tmp_path, src)
        meta = _class_meta(res, "Repo")
        assert "field_type_ids" not in meta

    def test_field_type_ids_omitted_on_samename_type_collision(
        self, tmp_path: Path
    ) -> None:
        # Two same-short-name Database classes -> the field's type id is
        # untrustworthy (bare-name inference could hit the wrong twin) -> the
        # entry is omitted (fields still present).
        src = (
            "class Database:\n"
            "    pass\n"
            "class Outer:\n"
            "    class Database:\n"
            "        pass\n"
            "class Repo:\n"
            "    def __init__(self, db: Database):\n"
            "        self.db = db\n"
        )
        res = _analyze(tmp_path, src)
        meta = _class_meta(res, "Repo")
        assert meta.get("fields") == {"db": "Database"}
        assert "field_type_ids" not in meta


class TestWiSupatFieldTypeImportShadow:
    """WI-supat (D3) PR-B review should_fix: a FIELD type that is a local class
    shadowed by a same-name in-tree import must be OMITTED from
    meta['field_type_ids'] at the producer, so the linker never treats the
    (wrong, shadowed) local class as the authoritative field type.

    Runs via the FULL pipeline (run_behavior_map) DELIBERATELY: the single-file
    extract_nodes path passes imports={}, so the import-shadow arm of
    _receiver_type_id_trustworthy never fires there. The assertion is at the
    PRODUCER (the parent class's meta), not the downstream edge — non-vacuous
    (bypassing the trustworthiness gate stamps field_type_ids['x'] with the
    shadowed local class id; the gate omits it)."""

    def test_field_type_import_shadow_omits_field_type_id(
        self, tmp_path: Path
    ) -> None:
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "b.py").write_text(
            "class Foo:\n"
            "    def compute(self):\n"
            "        return 2\n"
        )
        # a.py: a LOCAL `class Foo` shadowed by `from b import Foo`, used as the
        # type of Base's injected field `x`. The local-first annotation
        # resolution picks a.Foo, but the runtime binding is b.Foo -> the
        # producer must OMIT field_type_ids['x'] (import-shadowed) so the linker
        # never binds the field to the wrong (shadowed) local class.
        (tmp_path / "a.py").write_text(
            "class Foo:\n"
            "    def compute(self):\n"
            "        return 1\n"
            "from b import Foo\n"
            "class Base:\n"
            "    def __init__(self, x: Foo):\n"
            "        self.x = x\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        base = next(
            n for n in data["nodes"]
            if n.get("name") == "Base" and n.get("kind") == "class"
        )
        meta = base.get("meta") or {}
        # The field name is still recorded (fields), but its concrete type id is
        # omitted because the type name is import-shadowed.
        assert (meta.get("fields") or {}).get("x") == "Foo"
        assert "x" not in (meta.get("field_type_ids") or {})


class TestWiSupatEndToEndRecall:
    """WI-supat (D3) e2e RECALL: the concrete-id collision-recovery actually
    FIRES through the full pipeline. These lock the fix for the finalize
    relativization gap — before it, the producer-stamped ids kept their
    absolute-path form while the linker's class_symbols index was relativized, so
    every concrete id silently fell back to the name path and the recovery never
    fired (the negative e2e tests passed regardless via that same fallback, which
    is why this class exists: a POSITIVE recall assertion end-to-end)."""

    def test_field_type_id_recall_fires_e2e(self, tmp_path: Path) -> None:
        # PR-B: a Base field typed to log1.Logger; a same-name log2.Logger makes
        # the field-type NAME collide -> without field_type_ids the linker biases
        # to unresolved. With it, Sub.run's self.log.info() resolves to
        # log1.Logger.info.
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "log1.py").write_text(
            "class Logger:\n    def info(self):\n        return 1\n"
        )
        (tmp_path / "log2.py").write_text("class Logger:\n    pass\n")
        (tmp_path / "app.py").write_text(
            "from log1 import Logger\n"
            "class Base:\n"
            "    def __init__(self, log: Logger):\n"
            "        self.log = log\n"
            "class Sub(Base):\n"
            "    def run(self):\n"
            "        return self.log.info()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        sub_run = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Sub.run" and n.get("kind") == "method"
        )
        log1_info = {
            n["id"] for n in data["nodes"]
            if n.get("name") == "Logger.info"
            and (n.get("path") or "").endswith("log1.py")
        }
        resolved = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == sub_run
            and e["dst"] in log1_info and e.get("is_resolved")
        ]
        assert len(resolved) == 1, (
            "field_type_ids collision-recovery did not fire e2e "
            "(Sub.run -> log1.Logger.info unresolved)"
        )
        assert (resolved[0].get("meta") or {}).get("evidence_type") == (
            "ast_call_inherited_field"
        )

    def test_enclosing_class_id_recall_fires_e2e(self, tmp_path: Path) -> None:
        # PR-A (activated by the same finalize fix): two same-name Worker classes;
        # w1.Worker inherits greet from a cross-file BaseA. Without a concrete
        # enclosing_class_id the "Worker" name collision biases self.greet() to
        # unresolved; with it, w1.Worker.run resolves to BaseA.greet.
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "basea.py").write_text(
            "class BaseA:\n    def greet(self):\n        return 'a'\n"
        )
        (tmp_path / "w1.py").write_text(
            "from basea import BaseA\n"
            "class Worker(BaseA):\n"
            "    def run(self):\n"
            "        return self.greet()\n"
        )
        (tmp_path / "w2.py").write_text(
            "class Worker:\n    def run(self):\n        return 0\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        w1_run = {
            n["id"] for n in data["nodes"]
            if n.get("name") == "Worker.run"
            and (n.get("path") or "").endswith("w1.py")
        }
        basea_greet = {
            n["id"] for n in data["nodes"]
            if n.get("name") == "BaseA.greet" and n.get("kind") == "method"
        }
        resolved = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] in w1_run
            and e["dst"] in basea_greet and e.get("is_resolved")
        ]
        assert len(resolved) == 1, (
            "enclosing_class_id collision-recovery did not fire e2e "
            "(w1.Worker.run -> BaseA.greet unresolved)"
        )
        assert (resolved[0].get("meta") or {}).get("evidence_type") == (
            "ast_call_inherited"
        )
