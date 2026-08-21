# SPDX-License-Identifier: AGPL-3.0-or-later
"""One registry-backed answer to "is this an inheritance edge?" (INV-nosoz).

The defect
----------
``edge_types.INHERITANCE_EDGE_TYPES`` exists and two consumers use it.
Measured across the tree, seven *other* sites answered the same question
their own way and disagreed:

===========================================  ==========================================
site                                         vocabulary
===========================================  ==========================================
``edge_types.INHERITANCE_EDGE_TYPES``        ``{extends, inherits, implements}``
``cli.py`` (dead-code ancestor walk)         hand-rolled complete copy
``cli.py`` (test-exclusion preservation)     ``("extends", "implements")``
``linkers/_transitive_bases.py``             ``("extends", "implements")``
``linkers/inherited_calls.py``               ``("extends", "implements", "includes")``
``linkers/type_hierarchy.py``                inline ``== "extends"`` / ``== "implements"``
===========================================  ==========================================

ADR-0023 §3 already says consumers should resolve the family from the
registry instead of keeping a list. Six of eight sites did not.

The measured consequence, on real repositories
-----------------------------------------------
* **openzeppelin-contracts** — 516 ``inherits`` edges, 545 ``overrides``
  edges, and **0** Solidity ``dispatches_to``. ``type_hierarchy`` branches
  on two string literals and never consults the registry constant that
  does contain ``inherits``.
* **postal** (Rails) — 41 ``includes`` edges, of which 39 are Ruby mixins,
  and **0** of them reach ``type_hierarchy``.

Why ``includes`` is NOT in ``INHERITANCE_EDGE_TYPES``
-----------------------------------------------------
The tracker item posed the open question as "is ``includes`` a member of
the inheritance family?" and framed it as the Ruby mixin edge type. It is
not one relationship. ``includes`` is a **registered two-meaning value** —
its own registry entry reads "File or class includes / sources / mixes-in
another unit's ...". Nine producers emit it and eight mean *file
inclusion*: latex, make, meson, puppet, requirements, rst, scss, twig.
Exactly one, ``linkers/inheritance.py``, means the Ruby mixin.

Measured in postal: 39 ``class -> module`` mixin edges from the
inheritance linker, and 2 ``file -> external_symbol`` edges from SCSS
``@include`` of a Sass mixin. Putting the bare string into
``INHERITANCE_EDGE_TYPES`` would have enrolled a stylesheet ``@include``,
a Makefile ``include`` and a LaTeX ``\\include`` as class is-a edges in
slice expansion, ranking centrality and the dead-code ancestor walk —
the mirror-image of the hand-rolled copies, which the item itself warned
against.

So the family is resolved by a **predicate over the edge**, not by a set
of type strings alone. The discriminator already exists and is
registry-backed: ``evidence_type="ast_includes"``, whose spec reads "Edge
inferred from a runtime mixin declaration (Ruby ``include``/``extend``,
etc.)" and which exactly one emit site produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.edge_types import (
    CONCRETE_INHERITANCE_EDGE_TYPES,
    INHERITANCE_EDGE_TYPES,
    INTERFACE_SATISFACTION_EDGE_TYPES,
    MIXIN_INCLUDE_EVIDENCE_TYPES,
    find_partial_inheritance_family_literals,
    inheritance_edge_fields,
    is_inheritance_edge,
    is_inheritance_edge_record,
)
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.type_hierarchy import (
    build_inheritance_maps,
    link_type_hierarchy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# The registry constant and the predicate.
# ---------------------------------------------------------------------------

class TestFamilyMembership:
    def test_the_three_unambiguous_spellings_are_in_the_set(self) -> None:
        assert INHERITANCE_EDGE_TYPES == {"extends", "inherits", "implements"}

    def test_includes_is_deliberately_absent_from_the_set(self) -> None:
        """``includes`` means file-inclusion for 8 of its 9 producers.

        This is a load-bearing exclusion, not an omission. If a future
        change adds it, slice / ranking / dead-code start treating a
        Makefile ``include`` as a class is-a edge.
        """
        assert "includes" not in INHERITANCE_EDGE_TYPES

    def test_overrides_stays_out(self) -> None:
        """``overrides`` is method -> method, not type -> type."""
        assert "overrides" not in INHERITANCE_EDGE_TYPES

    def test_the_dispatch_split_partitions_the_family_exactly(self) -> None:
        """Every family member lands in exactly one dispatch role."""
        assert (
            CONCRETE_INHERITANCE_EDGE_TYPES | INTERFACE_SATISFACTION_EDGE_TYPES
            == INHERITANCE_EDGE_TYPES
        )
        assert not (
            CONCRETE_INHERITANCE_EDGE_TYPES & INTERFACE_SATISFACTION_EDGE_TYPES
        )

    @pytest.mark.parametrize("edge_type", sorted(INHERITANCE_EDGE_TYPES))
    def test_every_unambiguous_member_is_an_inheritance_edge(
        self, edge_type: str
    ) -> None:
        assert is_inheritance_edge(edge_type) is True

    def test_ruby_mixin_include_is_an_inheritance_edge(self) -> None:
        """The Ruby-mixin half of the overloaded ``includes`` value."""
        assert is_inheritance_edge("includes", evidence_type="ast_includes") is True

    def test_scss_include_is_not_an_inheritance_edge(self) -> None:
        """THE DISCRIMINATING CONTROL.

        Same ``edge_type``, different evidence. If this ever returns True
        the predicate has stopped discriminating and has silently widened
        to file-inclusion — which is the failure this whole design avoids.
        """
        assert is_inheritance_edge("includes", evidence_type="include") is False

    def test_bare_includes_with_no_evidence_is_not_inheritance(self) -> None:
        """Absence of evidence is not evidence of a mixin.

        Eight of nine producers mean file-inclusion, so the conservative
        reading of an unstamped ``includes`` is "not inheritance".
        """
        assert is_inheritance_edge("includes") is False
        assert is_inheritance_edge("includes", evidence_type=None) is False

    def test_unrelated_edge_type_is_not_inheritance(self) -> None:
        assert is_inheritance_edge("calls") is False
        assert is_inheritance_edge("contains", evidence_type="ast_includes") is False

    def test_mixin_evidence_vocabulary_is_registry_backed(self) -> None:
        """The discriminator must name a registered evidence type."""
        from hypergumbo_core.evidence_types import all_evidence_type_names

        assert MIXIN_INCLUDE_EVIDENCE_TYPES
        assert MIXIN_INCLUDE_EVIDENCE_TYPES <= all_evidence_type_names()


# ---------------------------------------------------------------------------
# Fixtures for the linker-level tests.
# ---------------------------------------------------------------------------

def _sym(sid: str, name: str, kind: str, lang: str) -> Symbol:
    return Symbol(
        id=sid,
        name=name,
        kind=kind,
        language=lang,
        path=f"/app/{name}.x",
        span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
        origin=lang,
        origin_run_id="test",
    )


def _edge(src: str, dst: str, edge_type: str, evidence_type: str) -> Edge:
    return Edge.create(
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=1,
        origin="test",
        origin_run_id="test-run",
        evidence_type=evidence_type,
    )


class TestSolidityInheritsGetsDispatch:
    """openzeppelin-contracts: 516 ``inherits`` edges, 0 dispatches.

    Scope warning, stated because the corpus contradicts the obvious reading
    of these tests: fixing the edge-type vocabulary does **not**, on its own,
    give Solidity dispatch on a real repository. Re-measured on
    openzeppelin-contracts after this change, the delta is **zero**.

    The reason is a second, independent cause on a different axis. Solidity
    emits contract members as ``kind="function"`` — 2,025 members of
    type-like containers, none of them ``kind="method"`` — and
    ``link_type_hierarchy`` indexes candidates with a literal
    ``if sym.kind != "method": continue``. So the ``inherits`` edge now
    reaches the dispatch map and finds no members waiting there. That cause
    is filed separately; it is a ``Symbol.kind`` producer gap, not an
    ``Edge.edge_type`` vocabulary gap, and it closes on its own evidence.

    What these tests therefore pin is exactly what was fixed: the edge
    reaches the map. ``test_solidity_members_are_functions_not_methods``
    below pins the part that is still broken, so this file cannot be read as
    claiming Solidity works.
    """

    def test_solidity_inherits_reaches_the_dispatch_map(self) -> None:
        base = _sym("solidity:/a.sol:1-10:Shape:contract", "Shape", "contract",
                    "solidity")
        base_m = _sym("solidity:/a.sol:2-4:Shape.area:method", "Shape.area",
                      "method", "solidity")
        child = _sym("solidity:/b.sol:1-10:Square:contract", "Square", "contract",
                     "solidity")
        child_m = _sym("solidity:/b.sol:2-4:Square.area:method", "Square.area",
                       "method", "solidity")
        edges = [
            _edge(child.id, base.id, "inherits", "ast_inherits"),
            _edge(base.id, base_m.id, "contains", "ast_contains"),
            _edge(child.id, child_m.id, "contains", "ast_contains"),
        ]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=[base, base_m, child, child_m],
            edges=edges,
        )
        result = link_type_hierarchy(ctx)
        dispatches = [
            e for e in result.edges
            if e.edge_type == "dispatches_to" and e.src == base_m.id
        ]
        assert dispatches, (
            "Solidity `contract Square is Shape` produced no dispatch. This is "
            "the openzeppelin-contracts case: 516 inherits edges, 0 dispatches."
        )
        assert dispatches[0].dst == child_m.id

    def test_solidity_members_are_functions_not_methods(self) -> None:
        """The REMAINING Solidity gap, pinned so it cannot be forgotten.

        Identical fixture to the test above except the members carry the kind
        Solidity actually emits. It produces no dispatch, and that is the
        honest current state — not a regression introduced here.
        """
        base = _sym("solidity:/a.sol:1-10:Shape:contract", "Shape", "contract",
                    "solidity")
        base_m = _sym("solidity:/a.sol:2-4:Shape.area:function", "Shape.area",
                      "function", "solidity")
        child = _sym("solidity:/b.sol:1-10:Square:contract", "Square",
                     "contract", "solidity")
        child_m = _sym("solidity:/b.sol:2-4:Square.area:function",
                       "Square.area", "function", "solidity")
        edges = [
            _edge(child.id, base.id, "inherits", "ast_inherits"),
            _edge(base.id, base_m.id, "contains", "ast_contains"),
            _edge(child.id, child_m.id, "contains", "ast_contains"),
        ]
        result = link_type_hierarchy(
            LinkerContext(
                repo_root="/app",
                symbols=[base, base_m, child, child_m],
                edges=edges,
            )
        )
        assert not [e for e in result.edges if e.edge_type == "dispatches_to"], (
            "Solidity function-kind members now dispatch. If that is "
            "deliberate, delete this test and re-measure openzeppelin-"
            "contracts (it was 0 dispatches across 516 inherits edges)."
        )


class TestRubyMixinGetsDispatch:
    """postal: 39 Ruby mixin edges, 0 dispatches."""

    def test_ruby_include_produces_dispatches_to(self) -> None:
        mod = _sym("ruby:/m.rb:1-10:Greet:module", "Greet", "module", "ruby")
        mod_m = _sym("ruby:/m.rb:2-4:Greet.hello:method", "Greet.hello", "method",
                     "ruby")
        cls = _sym("ruby:/c.rb:1-10:Person:class", "Person", "class", "ruby")
        cls_m = _sym("ruby:/c.rb:2-4:Person.hello:method", "Person.hello",
                     "method", "ruby")
        edges = [
            _edge(cls.id, mod.id, "includes", "ast_includes"),
            _edge(mod.id, mod_m.id, "contains", "ast_contains"),
            _edge(cls.id, cls_m.id, "contains", "ast_contains"),
        ]
        ctx = LinkerContext(
            repo_root="/app", symbols=[mod, mod_m, cls, cls_m], edges=edges,
        )
        result = link_type_hierarchy(ctx)
        dispatches = [
            e for e in result.edges
            if e.edge_type == "dispatches_to" and e.src == mod_m.id
        ]
        assert dispatches, (
            "Ruby `include Greet` produced no dispatch — the mixin half of the "
            "overloaded `includes` value is still invisible to type_hierarchy."
        )
        assert dispatches[0].dst == cls_m.id

    def test_scss_include_produces_no_dispatch(self) -> None:
        """POSITIVE CONTROL on the widening: file-inclusion must NOT dispatch.

        Identical edge_type to the mixin above, different evidence. If this
        starts producing dispatches, the predicate has widened to the eight
        file-inclusion producers and the fix has become the bug it replaced.
        """
        sheet = _sym("scss:/a.scss:1-10:a.scss:file", "a.scss", "file", "scss")
        mixin = _sym("scss:/b.scss:1-10:clearfix:external_symbol", "clearfix",
                     "external_symbol", "scss")
        target = _sym("scss:/b.scss:2-4:clearfix.rule:method", "clearfix.rule",
                      "method", "scss")
        other = _sym("scss:/a.scss:2-4:a.rule:method", "a.rule", "method", "scss")
        edges = [
            _edge(sheet.id, mixin.id, "includes", "include"),
            _edge(mixin.id, target.id, "contains", "ast_contains"),
            _edge(sheet.id, other.id, "contains", "ast_contains"),
        ]
        ctx = LinkerContext(
            repo_root="/app", symbols=[sheet, mixin, target, other], edges=edges,
        )
        result = link_type_hierarchy(ctx)
        assert not [e for e in result.edges if e.edge_type == "dispatches_to"], (
            "An SCSS @include produced a dispatches_to edge — the predicate "
            "widened to file-inclusion."
        )


class TestInheritanceMapsDoNotClobber:
    """``dict.update()`` replaced a child list instead of merging it.

    ``build_inheritance_maps`` returns two maps that ``link_type_hierarchy``
    immediately unions with two ``.update()`` calls. ``dict.update`` REPLACES
    the value for a shared key, so a type that is both extended and
    implemented lost one set of children entirely.

    Honest scope: this is latent-by-construction. Three repositories were
    measured (openzeppelin-contracts, postal, sherpa-onnx) and all three
    emit **zero** ``implements`` edges, so no overlapping key was observed
    live. It is fixed here because widening the family raises the odds of a
    shared key, not because a repro was found.
    """

    def test_a_type_both_extended_and_implemented_keeps_both_children(
        self,
    ) -> None:
        iface = _sym("java:/I.java:1-9:Shape:interface", "Shape", "interface",
                     "java")
        iface_m = _sym("java:/I.java:2-3:Shape.area:method", "Shape.area",
                       "method", "java")
        sub_iface = _sym("java:/J.java:1-9:Solid:interface", "Solid",
                         "interface", "java")
        sub_iface_m = _sym("java:/J.java:2-3:Solid.area:method", "Solid.area",
                           "method", "java")
        impl = _sym("java:/C.java:1-9:Box:class", "Box", "class", "java")
        impl_m = _sym("java:/C.java:2-3:Box.area:method", "Box.area", "method",
                      "java")
        symbols = [iface, iface_m, sub_iface, sub_iface_m, impl, impl_m]
        edges = [
            _edge(sub_iface.id, iface.id, "extends", "ast_extends"),
            _edge(impl.id, iface.id, "implements", "ast_implements"),
            _edge(iface.id, iface_m.id, "contains", "ast_contains"),
            _edge(sub_iface.id, sub_iface_m.id, "contains", "ast_contains"),
            _edge(impl.id, impl_m.id, "contains", "ast_contains"),
        ]
        result = link_type_hierarchy(
            LinkerContext(repo_root="/app", symbols=symbols, edges=edges)
        )
        dsts = {
            e.dst for e in result.edges
            if e.edge_type == "dispatches_to" and e.src == iface_m.id
        }
        assert sub_iface_m.id in dsts, "extends-child lost to dict.update()"
        assert impl_m.id in dsts, "implements-child lost to dict.update()"

    def test_build_inheritance_maps_still_reports_the_two_roles(self) -> None:
        """The split is real — it decides whether the language gate applies."""
        parent, iface = build_inheritance_maps([], [])
        assert parent == {}
        assert iface == {}


# ---------------------------------------------------------------------------
# The guard that keeps the vocabulary from re-forking.
# ---------------------------------------------------------------------------

class TestNoHandRolledInheritanceVocabularies:
    def test_live_tree_has_no_partial_inheritance_literals(self) -> None:
        offenders = find_partial_inheritance_family_literals(REPO_ROOT)
        assert offenders == [], (
            "A consumer enumerates part of the inheritance family by hand. "
            "That is how Solidity lost dispatch for 516 edges. Resolve it "
            "from the registry instead:\n  " + "\n  ".join(offenders)
        )

    def test_the_guard_actually_fires(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL — a guard that cannot be shown to fire is
        indistinguishable from one that matches nothing."""
        pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "m"
        pkg.mkdir(parents=True)
        (pkg / "offender.py").write_text(
            "STRUCTURAL = ('extends', 'implements')\n", encoding="utf-8"
        )
        offenders = find_partial_inheritance_family_literals(tmp_path)
        assert len(offenders) == 1, offenders
        assert "offender.py" in offenders[0]
        assert "inherits" in offenders[0]

    def test_a_complete_literal_is_not_flagged(self, tmp_path: Path) -> None:
        """Enumerate-all-or-none: the resolver is preferred, a complete
        literal is not the bug."""
        pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "m"
        pkg.mkdir(parents=True)
        (pkg / "ok.py").write_text(
            "S = ('extends', 'implements', 'inherits')\n", encoding="utf-8"
        )
        assert find_partial_inheritance_family_literals(tmp_path) == []


class TestBothEdgeShapes:
    """The predicate must read an ``Edge`` object AND a serialized dict.

    Linkers get ``Edge`` objects; the CLI read views get dicts. The two
    shapes diverge in two ways that have each cost a debugging session — a
    serialized edge names its type ``"type"`` rather than ``"edge_type"``,
    and its ``evidence_type`` is nested under ``meta`` rather than sitting
    at the top level. A predicate that handles only one shape is silently
    wrong in half the tree.
    """

    def test_serialized_edge_uses_type_and_nests_evidence_in_meta(self) -> None:
        """The shape a real survey.json actually carries."""
        edge = {
            "type": "includes",
            "meta": {"evidence_lang": "ruby", "evidence_type": "ast_includes"},
        }
        assert inheritance_edge_fields(edge) == ("includes", "ast_includes")
        assert is_inheritance_edge_record(edge) is True

    def test_serialized_scss_include_is_still_rejected(self) -> None:
        edge = {"type": "includes", "meta": {"evidence_type": "include"}}
        assert is_inheritance_edge_record(edge) is False

    def test_top_level_evidence_type_wins_when_present(self) -> None:
        edge = {"edge_type": "includes", "evidence_type": "ast_includes"}
        assert inheritance_edge_fields(edge) == ("includes", "ast_includes")

    def test_dict_without_meta_or_evidence(self) -> None:
        assert inheritance_edge_fields({"type": "extends"}) == ("extends", None)
        assert is_inheritance_edge_record({"type": "extends"}) is True

    def test_dict_with_non_mapping_meta(self) -> None:
        """A malformed ``meta`` must not raise; it just carries no evidence."""
        edge = {"type": "includes", "meta": ["not", "a", "mapping"]}
        assert inheritance_edge_fields(edge) == ("includes", None)
        assert is_inheritance_edge_record(edge) is False

    def test_edge_object_shape(self) -> None:
        e = _edge("a", "b", "includes", "ast_includes")
        assert inheritance_edge_fields(e) == ("includes", "ast_includes")
        assert is_inheritance_edge_record(e) is True

    def test_object_without_the_fields(self) -> None:
        assert inheritance_edge_fields(object()) == ("", None)


class TestGuardExemptions:
    """Each exemption is exercised, so none of them is a silent hole."""

    def _scan(self, tmp_path: Path, body: str) -> list[str]:
        pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "m"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "mod.py").write_text(body, encoding="utf-8")
        return find_partial_inheritance_family_literals(tmp_path)

    def test_language_guarded_expression_is_exempt(self, tmp_path: Path) -> None:
        """A per-language predicate is allowed to be partial."""
        assert self._scan(
            tmp_path,
            "def f(s):\n"
            "    return s.language == 'java' and s.t in ('extends', 'implements')\n",
        ) == []

    def test_not_the_family_marker_is_exempt(self, tmp_path: Path) -> None:
        """A set naming what the enclosing function EMITS, not the family.

        ``inheritance.py`` dedups its own emissions against
        ``("extends", "implements")`` because those are the only types it
        emits. That is correctly scoped, and it reads as a partial family
        literal, so it carries an inline marker a reviewer can see rather
        than an allow-list entry in a file nobody opens.
        """
        assert self._scan(
            tmp_path,
            "KEYS = ('extends', 'implements')  # not-the-family: our own emissions\n",
        ) == []

    def test_marker_does_not_exempt_a_different_line(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL on the marker — it is line-scoped, not file-scoped."""
        offenders = self._scan(
            tmp_path,
            "OK = ('extends', 'implements')  # not-the-family\n"
            "BAD = ('extends', 'implements')\n",
        )
        assert len(offenders) == 1, offenders
        assert ":2:" in offenders[0]

    def test_declared_languages_that_cannot_emit_the_missing_type(
        self, tmp_path: Path
    ) -> None:
        """A Java-only linker omitting ``inherits`` is correct, not a defect."""
        assert self._scan(
            tmp_path,
            "@register_linker('x', depends_on=[['java']])\n"
            "def f(ctx):\n"
            "    return ('extends', 'implements')\n",
        ) == []

    def test_declared_languages_that_CAN_emit_it_are_not_exempt(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL on the depends_on exemption."""
        offenders = self._scan(
            tmp_path,
            "@register_linker('x', depends_on=[['java', 'solidity']])\n"
            "def f(ctx):\n"
            "    return ('extends', 'implements')\n",
        )
        assert len(offenders) == 1, offenders
        assert "inherits" in offenders[0]

    def test_equality_chain_exempt_when_language_cannot_emit(
        self, tmp_path: Path
    ) -> None:
        assert self._scan(
            tmp_path,
            "@register_linker('x', depends_on=[['java']])\n"
            "def f(e):\n"
            "    if e.edge_type == 'extends':\n"
            "        return 1\n"
            "    elif e.edge_type == 'implements':\n"
            "        return 2\n"
            "    return 0\n",
        ) == []

    def test_equality_chain_flagged_when_language_can_emit(
        self, tmp_path: Path
    ) -> None:
        """The shape that caused the defect: an ``==`` chain, not a literal set."""
        offenders = self._scan(
            tmp_path,
            "@register_linker('x', depends_on=[['solidity']])\n"
            "def build_maps(e):\n"
            "    if e.edge_type == 'extends':\n"
            "        return 1\n"
            "    elif e.edge_type == 'implements':\n"
            "        return 2\n"
            "    return 0\n",
        )
        assert len(offenders) == 1, offenders
        assert "build_maps() branches on" in offenders[0]
        assert "inherits" in offenders[0]

    def test_single_family_mention_is_not_an_enumeration(
        self, tmp_path: Path
    ) -> None:
        """A lookup-table row mentions the family without enumerating it.

        ``scip/edges.py`` maps ``("is_implementation", "implements")`` —
        SCIP's field name to ours. Flagging that would train readers to
        ignore the linter.
        """
        assert self._scan(
            tmp_path,
            "ROWS = [('is_implementation', 'implements'),\n"
            "        ('is_reference', 'references')]\n",
        ) == []

    def test_a_complete_equality_chain_is_not_flagged(
        self, tmp_path: Path
    ) -> None:
        assert self._scan(
            tmp_path,
            "@register_linker('x', depends_on=[['solidity']])\n"
            "def f(e):\n"
            "    if e.edge_type == 'extends':\n"
            "        return 1\n"
            "    elif e.edge_type == 'inherits':\n"
            "        return 2\n"
            "    elif e.edge_type == 'implements':\n"
            "        return 3\n"
            "    return 0\n",
        ) == []
