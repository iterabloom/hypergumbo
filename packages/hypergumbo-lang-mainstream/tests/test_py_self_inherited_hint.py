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

from hypergumbo_lang_mainstream.py import extract_nodes


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
