# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reader-side call-ownership attribution for Python (WI-jafat, T0 keystone).

Before this fix, py.py resolved a call's enclosing-symbol (the ``src`` endpoint of a
``calls``/``instantiates``/``references`` edge) for **methods** through the flat,
bare-/short-name-keyed, last-write-wins ``symbol_by_name`` dict (the fallback at the
``_extract_edges`` caller loop). Two methods sharing a short name (``to_dict``,
``__init__``, …) in the same file clobber each other; the survivor owns ALL such peers'
calls and the overwritten sibling's calls are misattributed out-of-span. 1194 combined
out-of-span edges on the live self-analysis tree (967 calls + 170 instantiates +
57 references).

The fix mirrors the INV-mofav node-id mechanism already used for plain functions:
methods are registered in the collision-immune ``func_symbol_by_node_id`` (keyed by
``id(ast_node)``), so caller resolution hits the correct method by node identity and
never reaches the bare-name fallback (CHANGE A). A paired guard keeps methods out of the
enclosing-function ``inner_scope`` so registering them does not newly shadow a function's
own nested helpers at *callee* resolution (CHANGE B).

These are property tests (assert the invariant — every call attributed to a symbol whose
span contains the call line — not golden output). The identity-neutrality of the change
is pinned separately in ``test_identity_neutrality_call_resolution.py``.

Test map:
* (a) same-named methods each own their own calls — the WI-jafat repro (RED pre-fix).
* (c) same-named nested functions resolve per-parent — INV-mofav regression guard.
* (d) ``self.method()`` still resolves — CHANGE A retains the bare ``symbol_by_name``
  write that Case-2a depends on.
* (e) a method inside a class inside a function does NOT leak into the enclosing
  function's scope — the CHANGE-A-without-CHANGE-B regression guard (exercises the
  ``kind == "method"`` guard branch).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.py import extract_nodes


def _analyze(tmp_path: Path, src: str, filename: str = "models.py"):
    """Run intra-file analysis; return (result, span-by-id, (name,kind)-by-id)."""
    f = tmp_path / filename
    f.write_text(src)
    res = extract_nodes(f)
    spans = {s.id: (s.span.start_line, s.span.end_line) for s in res.symbols}
    names = {s.id: (s.name, s.kind) for s in res.symbols}
    return res, spans, names


WIJAFAT_FIXTURE = (
    "class Alpha:\n"
    "    def to_dict(self):\n"
    "        return helper_a()\n"
    "\n"
    "class Beta:\n"
    "    def to_dict(self):\n"
    "        return helper_b()\n"
    "\n"
    "def helper_a():\n"
    "    return 1\n"
    "\n"
    "def helper_b():\n"
    "    return 2\n"
)


class TestSameNamedMethodsOwnTheirCalls:
    """(a) The WI-jafat repro: each ``to_dict`` owns its own call, in-span."""

    def test_each_method_calls_resolve_in_span(self, tmp_path: Path) -> None:
        res, spans, names = _analyze(tmp_path, WIJAFAT_FIXTURE)
        calls = [e for e in res.edges if e.edge_type == "calls"]
        offenders = []
        for e in calls:
            lo, hi = spans[e.src]
            if not (lo <= e.line <= hi):
                offenders.append((names.get(e.src), e.line, (lo, hi), names.get(e.dst)))
        assert not offenders, f"out-of-span calls (src misattributed): {offenders}"

    def test_overwritten_sibling_regains_its_call(self, tmp_path: Path) -> None:
        res, _spans, names = _analyze(tmp_path, WIJAFAT_FIXTURE)
        calls = [e for e in res.edges if e.edge_type == "calls"]
        alpha = next(sid for sid, (n, _k) in names.items() if n == "Alpha.to_dict")
        assert any(e.src == alpha for e in calls), (
            "Alpha.to_dict has no outgoing call — swallowed by the bare-name "
            "survivor (Beta.to_dict)"
        )


class TestNestedFunctionsResolvePerParent:
    """(c) INV-mofav regression guard: same-named nested helpers stay per-parent."""

    def test_same_named_nested_helpers_distinct(self, tmp_path: Path) -> None:
        src = (
            "def parent_one():\n"
            "    def shared():\n"
            "        return 1\n"
            "    return shared()\n"
            "\n"
            "def parent_two():\n"
            "    def shared():\n"
            "        return 2\n"
            "    return shared()\n"
        )
        res, spans, names = _analyze(tmp_path, src)
        shared_calls = [
            e for e in res.edges
            if e.edge_type == "calls" and names.get(e.dst, ("", ""))[0].endswith("shared")
        ]
        assert len(shared_calls) == 2, f"expected 2 shared() calls, got {len(shared_calls)}"
        assert len({e.dst for e in shared_calls}) == 2, "both calls resolved to one helper"
        for e in shared_calls:
            slo, shi = spans[e.src]
            dlo, dhi = spans[e.dst]
            assert slo <= dlo and dhi <= shi, (
                "a parent's shared() call resolved to the other parent's nested helper"
            )


class TestSelfMethodStillResolves:
    """(d) CHANGE A retains the bare symbol_by_name write Case-2a depends on."""

    def test_self_method_call_resolves(self, tmp_path: Path) -> None:
        src = (
            "class Service:\n"
            "    def run(self):\n"
            "        return self.helper()\n"
            "    def helper(self):\n"
            "        return 1\n"
        )
        res, _spans, names = _analyze(tmp_path, src)
        calls = [e for e in res.edges if e.edge_type == "calls"]
        run_id = next(sid for sid, (n, _k) in names.items() if n == "Service.run")
        helper_id = next(sid for sid, (n, _k) in names.items() if n == "Service.helper")
        assert any(e.src == run_id and e.dst == helper_id for e in calls), (
            "self.helper() no longer resolves Service.run -> Service.helper"
        )


class TestMethodDoesNotLeakIntoEnclosingScope:
    """(e) CHANGE B: registering methods must not shadow a function's own nested
    helper at callee resolution (the CHANGE-A-without-CHANGE-B regression)."""

    def test_method_short_name_does_not_shadow_nested_function(
        self, tmp_path: Path
    ) -> None:
        src = (
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    class Inner:\n"
            "        def helper(self):\n"
            "            return 2\n"
            "    return helper()\n"
        )
        res, _spans, names = _analyze(tmp_path, src)
        outer_id = next(sid for sid, (n, _k) in names.items() if n == "outer")
        helper_calls = [
            e for e in res.edges
            if e.edge_type == "calls"
            and e.src == outer_id
            and names.get(e.dst, ("", ""))[0].endswith("helper")
        ]
        assert len(helper_calls) == 1, (
            f"expected one helper() call from outer, got {helper_calls}"
        )
        _dst_name, dst_kind = names[helper_calls[0].dst]
        assert dst_kind == "function", (
            f"outer's helper() leaked to a {dst_kind}; the method shadowed the "
            "nested function (CHANGE B guard missing)"
        )
