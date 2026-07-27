# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gubar (WI-noham D2): py.py resolves a ``@property`` attribute READ
(``obj.prop``) to the in-repo getter.

``obj.prop`` is an ``ast.Attribute`` in Load context, NOT an ``ast.Call``, so
``_process_call`` never sees it and the getter (``Symbol.end_line``,
``ValidationResult.ok``) looks dead. D2 adds a producer inside
``process_code_block`` that — gated on the receiver being a ``var_types``-typed
instance whose target attribute is a ``@property`` getter — emits an unresolved
``calls`` edge carrying ``receiver_type_hint``, exactly mirroring the WI-noham
Part A method-call producer. The (INV-nilud-validated) ``inherited_calls``
linker then mints the resolved edge via strict Site-2 Step-1.

Invariants this file locks:

* **Property reads emit a call edge** — a getter invocation IS a call, so the
  read produces a ``calls`` edge (keeping the ``(method,python)-calls->(method,
  python)`` single runtime_coherence partition — never ``references``/``reads``).
* **Taint-safety by construction** — the analyzer edge STAYS
  ``is_resolved=False`` with ``call_construct='method'`` (the gate_named_entry
  suppression) and an unchanged ``:unresolved`` dst; the linker is the sole
  minter of the resolved edge.
* **Precision gate** — only a genuine ``@property`` getter on a ``var_types``-
  typed instance emits. A plain data field, a non-property method read, an
  untyped receiver, a bare-CLASS receiver (``ClassName.prop`` is a descriptor
  access, not a getter call), and a Store-context write all emit NOTHING.
* **No double-emit** — the attribute that is a call's callee (``obj.method()``)
  is handled by the calls pipeline; D2 skips it (``_collect_call_func_attr_ids``).
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import (
    _collect_call_func_attr_ids,
    _resolve_property_getter,
    extract_nodes,
)


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _prop_read_edges(res, attr_name: str):
    """Unresolved ``calls`` edges whose dst is the given attr short name."""
    dst = f"python:external:0-0:{attr_name}:unresolved"
    return [e for e in res.edges if e.edge_type == "calls" and e.dst == dst]


class TestPropertyReadEmission:
    def test_typed_param_property_read_emits_hint(self, tmp_path: Path) -> None:
        # ``c: C`` (param annotation -> var_types); ``c.val`` reads the
        # @property getter. D2 emits an unresolved calls edge carrying the hint.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f(c: C):\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        edges = _prop_read_edges(res, "val")
        assert len(edges) == 1
        e = edges[0]
        assert e.is_resolved is False
        assert (e.meta or {}).get("receiver_type_hint") == "C"
        assert (e.meta or {}).get("call_construct") == "method"
        assert e.evidence_type == "ast_call"

    def test_plain_method_read_not_called_emits_no_edge(
        self, tmp_path: Path
    ) -> None:
        # ``c.regular`` (a bound-method reference, NOT a @property) must emit
        # nothing — reading a plain method is not a getter invocation.
        src = (
            "class C:\n"
            "    def regular(self):\n"
            "        return 1\n"
            "def f(c: C):\n"
            "    return c.regular\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "regular") == []

    def test_nonexistent_attribute_read_emits_no_edge(
        self, tmp_path: Path
    ) -> None:
        # ``c.missing`` — no such member on C -> getter resolution misses -> no
        # spurious external calls edge for a plain attribute access.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f(c: C):\n"
            "    return c.missing\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "missing") == []

    def test_untyped_receiver_property_read_emits_no_edge(
        self, tmp_path: Path
    ) -> None:
        # ``c`` has no inferable type (not in var_types) -> no edge.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f(c):\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "val") == []

    def test_property_write_store_context_emits_no_edge(
        self, tmp_path: Path
    ) -> None:
        # ``c.val = 5`` is a Store-context write (a setter call), not a read.
        # D2 only handles Load-context property reads.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f(c: C):\n"
            "    c.val = 5\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "val") == []

    def test_method_call_callee_not_double_emitted(self, tmp_path: Path) -> None:
        # ``c.method()`` on a typed receiver where C defines no such method:
        # the WI-noham Part A method producer emits exactly ONE unresolved
        # calls+hint edge. D2 must skip the callee Attribute (it is a call
        # func) and NOT add a second edge.
        src = (
            "class C:\n"
            "    pass\n"
            "def f(c: C):\n"
            "    return c.method()\n"
        )
        res = _analyze(tmp_path, src)
        edges = _prop_read_edges(res, "method")
        assert len(edges) == 1, "the call callee was double-emitted as a read"


class TestPropertyReadTypeInference:
    def test_constructor_typed_var_property_read(self, tmp_path: Path) -> None:
        # ``c = C()`` -> var_types via the constructor assignment (the
        # ``symbol = Symbol(...)`` flagship shape).
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def f():\n"
            "    c = C()\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        edges = _prop_read_edges(res, "val")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "C"

    def test_return_annotation_typed_var_property_read(
        self, tmp_path: Path
    ) -> None:
        # ``c = make()`` where ``make() -> C`` -> var_types via return
        # annotation (the ``val_result = validate_all(...)`` flagship shape).
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "def make() -> C:\n"
            "    return C()\n"
            "def f():\n"
            "    c = make()\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        edges = _prop_read_edges(res, "val")
        assert len(edges) == 1
        assert (edges[0].meta or {}).get("receiver_type_hint") == "C"

    def test_module_scope_property_read_emits_nothing(
        self, tmp_path: Path
    ) -> None:
        # A module-level ``c = C(); x = c.val`` runs process_code_block with the
        # <module> pseudo-node (kind='file'). A file-kind src emitting a `calls`
        # edge would create a NEW runtime_coherence offender in the
        # (file, python, external_symbol, python) partition (which already holds
        # `imports`), breaking the ADR-0023 §3 ratchet — so the branch is gated
        # to function/method callers and module-scope reads emit nothing.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return 1\n"
            "c = C()\n"
            "x = c.val\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "val") == []


class TestPropertyReadKnownGaps:
    """Documented scope limits — behaviors D2 deliberately does NOT cover (safe
    misses, never wrong edges). Locked so a future change to any is intentional.
    """

    def test_read_write_property_getter_not_resolved(
        self, tmp_path: Path
    ) -> None:
        # A read-WRITE property (getter + @x.setter) yields no edge: the setter
        # shares the qualified name ``C.val`` and, being defined later, wins the
        # last-write in global_symbols; its recorded decorator is the dotted
        # ``val.setter`` (not ``property``), so _resolve_property_getter returns
        # None. Read-ONLY properties (the flagship shape) resolve; read-write is
        # a deferred follow-up. Safe (emits nothing), documented here.
        src = (
            "class C:\n"
            "    @property\n"
            "    def val(self):\n"
            "        return self._v\n"
            "    @val.setter\n"
            "    def val(self, x):\n"
            "        self._v = x\n"
            "def f(c: C):\n"
            "    return c.val\n"
        )
        res = _analyze(tmp_path, src)
        assert _prop_read_edges(res, "val") == []


class TestResolvePropertyGetterUnit:
    """Unit coverage of the ``_resolve_property_getter`` gate."""

    @staticmethod
    def _sym(name: str, kind: str, path: str = "m.py", decorators=None) -> Symbol:
        meta = {"decorators": decorators} if decorators is not None else None
        return Symbol(
            id=f"python:{path}:1-2:{name}:{kind}",
            name=name,
            kind=kind,
            language="python",
            path=path,
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            meta=meta,
        )

    def test_property_getter_via_short_name(self) -> None:
        # extract_nodes keys methods by SHORT name; the getter's .name is the
        # qualified 'C.val', matching this class.
        cls = self._sym("C", "class")
        getter = self._sym("C.val", "method", decorators=[{"name": "property"}])
        assert (
            _resolve_property_getter(cls, "val", {"val": getter}, None) is getter
        )

    def test_property_getter_via_path_name_index(self) -> None:
        cls = self._sym("C", "class", path="pkg/m.py")
        getter = self._sym(
            "C.val", "method", path="pkg/m.py", decorators=[{"name": "property"}]
        )
        idx = {("pkg/m.py", "C.val"): getter}
        assert _resolve_property_getter(cls, "val", {}, idx) is getter

    def test_short_name_hit_on_wrong_class_returns_none(self) -> None:
        # A same-short-name getter belonging to a DIFFERENT class (D.val) must
        # not be credited to C — the qualified-name guard rejects it.
        cls = self._sym("C", "class")
        other = self._sym("D.val", "method", decorators=[{"name": "property"}])
        assert _resolve_property_getter(cls, "val", {"val": other}, None) is None

    def test_plain_method_not_property_returns_none(self) -> None:
        cls = self._sym("C", "class")
        getter = self._sym("C.val", "method", decorators=[{"name": "staticmethod"}])
        assert _resolve_property_getter(cls, "val", {"val": getter}, None) is None

    def test_missing_getter_returns_none(self) -> None:
        cls = self._sym("C", "class")
        assert _resolve_property_getter(cls, "val", {}, None) is None

    def test_non_method_kind_returns_none(self) -> None:
        cls = self._sym("C", "class")
        field = self._sym("C.val", "field", decorators=[{"name": "property"}])
        assert _resolve_property_getter(cls, "val", {"val": field}, None) is None

    def test_meta_none_returns_none(self) -> None:
        cls = self._sym("C", "class")
        getter = self._sym("C.val", "method", decorators=None)
        assert _resolve_property_getter(cls, "val", {"val": getter}, None) is None

    def test_non_dict_decorator_entry_skipped(self) -> None:
        cls = self._sym("C", "class")
        getter = self._sym("C.val", "method", decorators=["property"])
        assert _resolve_property_getter(cls, "val", {"val": getter}, None) is None


class TestCollectCallFuncAttrIds:
    def test_collects_call_func_attribute_ids(self) -> None:
        import ast

        tree = ast.parse("obj.method()\nx = obj.prop\n")
        ids = _collect_call_func_attr_ids([tree])
        call = tree.body[0].value  # ast.Call
        read = tree.body[1].value  # ast.Attribute (obj.prop, a read)
        assert id(call.func) in ids
        assert id(read) not in ids


class TestPropertyReadResolutionEndToEnd:
    """Full-pipeline: the hint py.py emits for a property read is resolved by
    the inherited_calls linker into a real in-repo ``calls`` edge to the getter.
    """

    def test_property_read_resolves_to_getter_via_linker(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "models.py").write_text(
            "class Box:\n"
            "    @property\n"
            "    def size(self):\n"
            "        return 1\n"
        )
        (tmp_path / "app.py").write_text(
            "from models import Box\n"
            "def consume(b: Box):\n"
            "    return b.size\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "Box.size" and n.get("kind") == "method"
        )
        consume = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "consume" and n.get("kind") == "function"
        )
        resolved = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == consume and e["dst"] == target
        ]
        assert resolved, (
            "b.size (@property read) did not resolve to Box.size via the "
            "receiver_type_hint -> inherited_calls linker chain"
        )
        assert "inherited-calls-linker" in (resolved[0].get("origin") or [])
        assert resolved[0]["is_resolved"] is True

    def test_property_read_linker_edges_pure_resolved(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "models.py").write_text(
            "class Box:\n"
            "    @property\n"
            "    def size(self):\n"
            "        return 1\n"
        )
        (tmp_path / "app.py").write_text(
            "from models import Box\n"
            "def consume(b: Box):\n"
            "    return b.size\n"
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
            if "inherited-calls-linker" in (e.get("origin") or [])
        ]
        assert linker_edges, "no inherited-calls edge minted (test is vacuous)"
        for e in linker_edges:
            assert e["is_resolved"] is True
            assert not str(e["dst"]).endswith(":unresolved")
            assert e["dst"] in node_ids
