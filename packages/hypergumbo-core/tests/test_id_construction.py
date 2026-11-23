# SPDX-License-Identifier: AGPL-3.0-or-later
"""The static half of ADR-0034's id-construction discipline (WI-vodin).

WHAT THESE TESTS PIN. ``Symbol(id=f"...")`` written at the constructor is
the rarest spelling of a hand-built id: an ``id=`` argument is far more often
a bare local or a helper call with the f-string behind it, so a check that
stops at the constructor sees ``id=sym_id`` and learns nothing. These tests
pin the three-hop resolution that makes the rule reach the ids it is about,
and the grammar it applies once it gets there.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.check_id_construction import (
    UNJUDGED_REASONS,
    edge_template_problem,
    find_id_construction_drift,
    id_template_problem,
)


class TestTheGrammarOnATemplate:
    """ADR-0036's five-slot grammar, checked on a template rather than a value."""

    @pytest.mark.parametrize("template", [
        "{}:{}:{}-{}:{}:{}",           # every slot interpolated
        "python:{}:1-1:file:file",     # every slot literal but the path
        "{}:{}:0-0:{}:external_symbol",
        "wasm:{}:0-0:module:wasm_module",
        "rust:{}:0-0:{}:event_emitter",
    ])
    def test_conformant_templates_pass(self, template: str) -> None:
        assert id_template_problem(template) is None

    def test_a_single_line_in_the_span_slot_is_not_a_span(self) -> None:
        # The typical defect: `line: int` interpolated where the
        # grammar wants `start-end`.
        problem = id_template_problem("handlebars:{}:{}:{}:{}")
        assert problem is not None
        assert "malformed_span_segment" in problem

    def test_a_colon_where_a_hyphen_belongs_shifts_every_slot(self) -> None:
        problem = id_template_problem("{}:{}:{}:0:{}:crypto_producer")
        assert problem is not None
        assert "malformed_span_segment" in problem

    def test_four_slots_is_not_five(self) -> None:
        problem = id_template_problem("{}:{}:{}:annotated_publisher")
        assert problem is not None
        assert "wrong_field_count" in problem

    def test_a_hyphen_in_the_language_slot_is_not_canonical(self) -> None:
        problem = id_template_problem("swift-objc:{}:{}:{}:{}")
        assert problem is not None
        assert "non_canonical_language_prefix" in problem

    def test_an_uppercase_kind_is_not_canonical(self) -> None:
        problem = id_template_problem("python:{}:1-1:{}:Function")
        assert problem is not None
        assert "non_canonical_kind_suffix" in problem

    def test_an_empty_name_slot_is_refused(self) -> None:
        # `lang:path:1-1::kind` parses into five fields with an EMPTY name —
        # grammatical by arity and useless to every consumer.
        assert id_template_problem("python:{}:1-1::function") == "empty_name_slot"

    def test_an_interpolated_kind_cannot_be_judged_statically(self) -> None:
        # A placeholder in the kind slot is not a violation: the check refuses
        # to invent a verdict it has no evidence for.
        assert id_template_problem("python:{}:1-1:{}:{}") is None


def _write(tmp_path: Path, body: str) -> Path:
    pkg = tmp_path / "packages" / "p" / "src" / "m"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


class TestTheEdgeGrammar:
    """`Edge.id` is a content digest, and that is a different rule."""

    def test_the_digest_shape_passes(self) -> None:
        assert edge_template_problem("edge:sha256:{}") is None

    def test_a_literal_digest_passes(self) -> None:
        assert edge_template_problem("edge:sha256:0123456789abcdef") is None

    def test_a_short_digest_is_refused(self) -> None:
        assert edge_template_problem("edge:sha256:0123") is not None

    def test_the_node_grammar_is_refused_for_an_edge(self) -> None:
        problem = edge_template_problem("python:{}:1-1:{}:calls")
        assert problem is not None
        assert "non_canonical_edge_id" in problem


class TestTheThreeHops:
    """Direct, one local, one same-module function — and no further."""

    def test_hop_one_the_argument_itself(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                return Symbol(id=f"lang:{path}:{line}:{name}:function")
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "malformed_span_segment" in drift[0]

    def test_hop_two_through_a_local(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                sym_id = f"lang:{path}:{line}:{name}:function"
                return Symbol(id=sym_id)
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1

    def test_hop_three_through_a_same_module_helper(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def _make_symbol_id(path, line, name):
                return f"lang:{path}:{line}:{name}:function"

            def f(path, line, name):
                return Symbol(id=_make_symbol_id(path, line, name))
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "mod.py" in drift[0]

    def test_hop_three_stops_at_a_helper_the_module_does_not_define(
        self, tmp_path: Path,
    ) -> None:
        """An imported helper has no body in this module to walk, so the third
        hop has nothing to follow and the call itself is all that is reached.
        Pinned here because the live tree covers it only incidentally, and a
        linker rewrite could remove that without anyone noticing."""
        root = _write(tmp_path, '''
            from elsewhere import build_id

            def f(path):
                return Symbol(id=build_id(path))
        ''')
        assert find_id_construction_drift(root) == []

    def test_the_canonical_factory_is_never_flagged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                return Symbol(id=make_symbol_id("lang", path, line, line, name, "function"))
        ''')
        assert find_id_construction_drift(root) == []

    def test_a_conformant_hand_built_id_is_allowed(self, tmp_path: Path) -> None:
        # The rule is the GRAMMAR, not a ban on f-strings. A synthetic linker
        # stand-in has no source span and legitimately writes 0-0 by hand.
        root = _write(tmp_path, '''
            def f(path, name):
                return Symbol(id=f"rust:{path}:0-0:{name}:event_emitter")
        ''')
        assert find_id_construction_drift(root) == []

    def test_a_concatenated_id_is_refused_because_it_cannot_be_checked(
        self, tmp_path: Path,
    ) -> None:
        # A '+' chain has no template to apply the grammar to, so the check
        # cannot clear it and does not pretend to.
        root = _write(tmp_path, '''
            def f(path, name):
                return Symbol(id="lang:" + path + ":1-1:" + name + ":function")
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "concatenated_id" in drift[0]

    def test_an_unrelated_f_string_is_not_in_the_population(self, tmp_path: Path) -> None:
        # Most id-shaped f-strings in the tree never reach a constructor.
        # Pricing the rule on the wrong unit is what makes a linter
        # unusable.
        root = _write(tmp_path, '''
            def f(a, b):
                return f"site:{a}:{b}:note"
        ''')
        assert find_id_construction_drift(root) == []

    def test_an_edge_id_answers_to_its_own_grammar_not_the_node_one(
        self, tmp_path: Path,
    ) -> None:
        # An edge id is `edge:sha256:<16hex>`, a content digest, so the node
        # grammar would flag every edge. Counterparts in ROLE, not SHAPE.
        root = _write(tmp_path, '''
            def f(digest):
                return Edge(id=f"edge:sha256:{digest}")
        ''')
        assert find_id_construction_drift(root) == []

    def test_an_edge_id_in_the_node_grammar_is_the_violation(
        self, tmp_path: Path,
    ) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                return Edge(id=f"lang:{path}:{line}-{line}:{name}:calls")
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "non_canonical_edge_id" in drift[0]


class TestOneClassifier:
    """The lint asks the public classifiers, with the id as a ``{}`` template.

    ``id_template_problem`` / ``edge_template_problem`` are what every test
    above exercises; a private twin in the lint would let what
    ``scripts/check-id-construction`` runs drift from what the tests pin
    (WI-mifup)."""

    def test_the_lint_asks_the_public_classifiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import hypergumbo_core.check_id_construction as mod

        seen: list[str] = []
        monkeypatch.setattr(mod, "id_template_problem", lambda t: seen.append(t) or "node-verdict")
        monkeypatch.setattr(mod, "edge_template_problem", lambda t: seen.append(t) or "edge-verdict")
        root = _write(tmp_path, '''
            def f(path, digest):
                Symbol(id=f"lang:{path}:1-2:n:function")
                return Edge(id=f"edge:sha256:{digest}")
        ''')
        drift = find_id_construction_drift(root)
        assert seen == ["lang:{}:1-2:n:function", "edge:sha256:{}"]
        assert [d.split(" — ", 1)[1] for d in drift] == ["node-verdict", "edge-verdict"]


class TestTheKindSlotAgainstTheSymbolsKind:
    """A grammatical id can still name the wrong kind (ADR-0036 Ruling 2).

    Two producers called the factory correctly and handed it a ROLE word
    (``export_entry``, ``test_case``) as ``kind``, then built the Symbol with
    its real kind; two more wrote the word into a helper. The grammar check
    passed all four. So where the id's kind word and ``Symbol(kind=...)`` are
    both readable in one function, they are compared."""

    def test_a_role_word_handed_to_the_factory_is_a_mismatch(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, start, end, name):
                return Symbol(
                    id=make_symbol_id("json", path, start, end, name, "export_entry"),
                    kind="export",
                )
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "kind_slot_mismatch" in drift
        assert "'export_entry'" in drift and "'export'" in drift

    def test_through_a_local_and_a_keyword_argument(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, start, end, name):
                sym_id = make_symbol_id("json", path, start, end, name, kind="export_entry")
                return Symbol(id=sym_id, kind="export")
        ''')
        assert len(find_id_construction_drift(root)) == 1

    def test_through_the_pair_factory_and_a_tuple_assignment(self, tmp_path: Path) -> None:
        """robot's shape: the id and the stable id are minted together."""
        root = _write(tmp_path, '''
            def f(path, name, span):
                symbol_id, stable_id = make_doc_symbol_ids(
                    "robot", path, "test_case", name, span.start_line, span.end_line,
                )
                return Symbol(id=symbol_id, stable_id=stable_id, kind="test")
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "'test_case'" in drift and "'test'" in drift

    def test_through_a_module_helper_whose_kind_is_not_the_last_parameter(
        self, tmp_path: Path,
    ) -> None:
        """js_ts's shape: ``_make_symbol_id(path, start, end, name, kind, lang)``."""
        root = _write(tmp_path, '''
            def _make_symbol_id(path, start, end, name, kind, lang):
                return make_symbol_id(lang, path, start, end, name, kind)

            def f(path, span, name, lang):
                return Symbol(
                    id=_make_symbol_id(path, span.start_line, span.end_line, name, "route", lang),
                    kind="function",
                )
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "'route'" in drift and "'function'" in drift

    def test_a_helper_is_read_at_its_return_not_by_its_parameter_names(
        self, tmp_path: Path,
    ) -> None:
        """The websocket linker's shape, which the first form of this rule
        reported: the helper takes a ``kind`` it appends to the NAME slot, and
        writes the Symbol's kind in the kind slot itself."""
        root = _write(tmp_path, '''
            def _make_symbol_id(path, line, event, kind, language="python"):
                return make_symbol_id(language, path, line, line, f"{event}-{kind}", "function")

            def f(ep):
                return Symbol(
                    id=_make_symbol_id(ep.path, ep.line, ep.event, "endpoint", ep.language),
                    kind="function",
                )
        ''')
        assert find_id_construction_drift(root) == []

    def test_a_literal_a_helper_writes_is_compared(self, tmp_path: Path) -> None:
        """...and the same helper under a Symbol of another kind is a mismatch,
        whatever its parameters are called."""
        root = _write(tmp_path, '''
            def _stand_in_id(path, line, name, role):
                return make_symbol_id("x", path, line, line, f"{name}-{role}", "function")

            def f(ep):
                return Symbol(id=_stand_in_id(ep.path, ep.line, ep.name, "caller"), kind="call_site")
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "'function'" in drift and "'call_site'" in drift

    def test_a_helper_parameter_under_any_name_and_as_a_keyword(self, tmp_path: Path) -> None:
        """The parameter a helper writes into the kind slot is followed back to
        the caller's argument by position or by keyword, in a factory call and
        in a template alike."""
        root = _write(tmp_path, '''
            def _by_factory(path, line, name, word):
                return make_symbol_id("x", path, line, line, name, word)

            def _by_template(path, line, name, kind):
                return f"x:{path}:{line}-{line}:{name}:{kind}"

            def f(p):
                a = Symbol(id=_by_factory(p.path, p.line, p.name, "role_a"), kind="function")
                b = Symbol(id=_by_factory(p.path, p.line, p.name, word="role_b"), kind="function")
                c = Symbol(id=_by_template(p.path, p.line, p.name, "role_c"), kind="function")
                d = Symbol(id=_by_template(p.path, p.line, p.name, "function"), kind="function")
                return a, b, c, d
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 3, drift
        assert all(f"'role_{letter}'" in line for letter, line in zip("abc", drift, strict=True))

    def test_a_helper_that_computes_its_word_is_not_judged(self, tmp_path: Path) -> None:
        """grpc's and swift_objc's shape: the word comes from a table or a
        local. No evidence, no verdict; a run of the producer is what checks it."""
        root = _write(tmp_path, '''
            def _from_table(path, line, name, role):
                return make_symbol_id("x", path, line, line, name, _FOLDS[role])

            def _from_local(pattern):
                role, kind = _FOLDS[pattern.type]
                return make_symbol_id("x", pattern.path, 1, 1, pattern.name, kind)

            def _from_expression(path, name, kind):
                return f"x:{path}:0-0:{name}:{kind.lower()}"

            def f(p):
                a = Symbol(id=_from_table(p.path, p.line, p.name, "bridge"), kind="function")
                b = Symbol(id=_from_local(p), kind="function")
                c = Symbol(id=_from_expression(p.path, p.name, "Role"), kind="function")
                return a, b, c
        ''')
        assert find_id_construction_drift(root) == []

    def test_an_ungrammatical_id_is_reported_once_as_that(self, tmp_path: Path) -> None:
        """A template with a bare line where the span belongs, or a kind slot
        that is half a word, has no kind to compare: the grammar finding
        stands alone, at the constructor and behind a helper."""
        root = _write(tmp_path, '''
            def _half_a_word(path, name, kind):
                return f"x:{path}:0-0:{name}:{kind}_site"

            def f(path, line, name):
                a = Symbol(id=f"x:{path}:{line}:{name}:role", kind="function")
                b = Symbol(id=_half_a_word(path, name, "call"), kind="function")
                return a, b
        ''')
        helper, constructor = find_id_construction_drift(root)
        assert "non_canonical_kind_suffix" in helper
        assert "malformed_span_segment" in constructor

    def test_a_literal_kind_slot_in_a_hand_built_template(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, name):
                return Symbol(id=f"bitbake:{path}:0-0:{name}:python_task", kind="task")
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "'python_task'" in drift and "'task'" in drift

    def test_agreeing_words_pass(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, start, end, name, kind):
                a = Symbol(id=make_symbol_id("json", path, start, end, name, "export"), kind="export")
                b = Symbol(id=make_symbol_id("json", path, start, end, name, kind), kind=kind)
                c = Symbol(id=f"lang:{path}:0-0:{name}:module", kind="module")
                return a, b, c
        ''')
        assert find_id_construction_drift(root) == []

    def test_a_side_that_cannot_be_read_is_not_judged(self, tmp_path: Path) -> None:
        """A name on one side and a literal on the other, two different names,
        an id from a helper the module does not define: no evidence, no
        verdict, as for an interpolated slot of the grammar check."""
        root = _write(tmp_path, '''
            def f(path, start, end, name, kind, other, ready_id, parts):
                a = Symbol(id=make_symbol_id("json", path, start, end, name, kind), kind="export")
                b = Symbol(id=make_symbol_id("json", path, start, end, name, kind), kind=other)
                c = Symbol(id=ready_id, kind="export")
                d = Symbol(id=imported_helper(path, name, "role"), kind="export")
                e = Symbol(id=make_symbol_id(*parts), kind="export")
                g = Symbol(id=make_symbol_id("json", path), kind="export")
                return a, b, c, d, e, g
        ''')
        assert find_id_construction_drift(root) == []

    def test_one_branch_of_several_is_enough(self, tmp_path: Path) -> None:
        """A local assigned twice: each value the id can hold is compared."""
        root = _write(tmp_path, '''
            def f(path, start, end, name, python):
                sym_id = make_symbol_id("bitbake", path, start, end, name, "task")
                if python:
                    sym_id = make_symbol_id("bitbake", path, start, end, name, "python_task")
                return Symbol(id=sym_id, kind="task")
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "'python_task'" in drift

    def test_the_factory_table_matches_the_factories(self) -> None:
        """The position of ``kind`` is read from this table, so it must be the
        real one."""
        import inspect

        from hypergumbo_core.analyze import base
        from hypergumbo_core.check_id_construction import _KIND_ARGUMENT_OF_FACTORY

        for name, index in _KIND_ARGUMENT_OF_FACTORY.items():
            params = list(inspect.signature(getattr(base, name)).parameters)
            assert params[index] == "kind", (name, params)

    def test_the_declared_holes_are_live_and_are_the_only_ones(self) -> None:
        """A mismatch whose word feeds a stable id needs a scheme bump to
        correct, and is declared until then. Declared means: reported the
        moment the declaration is removed, and an entry nothing matches is
        refused. So with no declarations the live tree reports exactly the
        declared holes, one finding each."""
        from hypergumbo_core.check_id_construction import KNOWN_KIND_SLOT_HOLES

        repo_root = Path(__file__).resolve().parents[3]
        undeclared = find_id_construction_drift(repo_root, known_kind_slot_holes={})
        assert len(undeclared) == len(KNOWN_KIND_SLOT_HOLES), undeclared
        for rel, id_kind, symbol_kind in KNOWN_KIND_SLOT_HOLES:
            assert (repo_root / rel).is_file(), f"declared hole names a missing file: {rel}"
            assert any(
                drift.startswith(f"{rel}:") and f"{id_kind!r}" in drift
                and f"{symbol_kind!r}" in drift
                for drift in undeclared
            ), (rel, id_kind, symbol_kind, undeclared)

    def test_a_declared_hole_silences_its_own_mismatch_only(self, tmp_path: Path) -> None:
        """The declaration mechanism, on a tree of its own: the live table may
        be empty, and a future hole must still be declarable."""
        root = _write(tmp_path, '''
            def f(path, start, end, name):
                return Symbol(id=make_symbol_id("x", path, start, end, name, "test_case"), kind="test")

            def g(path, start, end, name):
                return Symbol(id=make_symbol_id("x", path, start, end, name, "function_call"), kind="call_site")
        ''')
        hole = {("packages/p/src/m/mod.py", "test_case", "test"): "awaits a bump"}
        (drift,) = find_id_construction_drift(root, known_kind_slot_holes=hole)
        assert "'function_call'" in drift and "'call_site'" in drift
        assert len(find_id_construction_drift(root, known_kind_slot_holes={})) == 2

    def test_a_declaration_for_a_file_this_tree_lacks_says_nothing(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, start, end, name):
                return Symbol(id=make_symbol_id("x", path, start, end, name, "test"), kind="test")
        ''')
        elsewhere = {("packages/q/src/q/gone.py", "test_case", "test"): "another tree"}
        assert find_id_construction_drift(root, known_kind_slot_holes=elsewhere) == []

    def test_a_declared_hole_that_no_longer_exists_is_reported(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, start, end, name):
                return Symbol(id=make_symbol_id("x", path, start, end, name, "test"), kind="test")
        ''')
        stale = {("packages/p/src/m/mod.py", "test_case", "test"): "fixed long ago"}
        (drift,) = find_id_construction_drift(root, known_kind_slot_holes=stale)
        assert "stale_kind_slot_hole" in drift and "mod.py" in drift


class TestTheNodeIdWrapping:
    """``NodeId(...)`` and ``read_node_id(...)`` hand their argument through
    unchanged, so the template inside is judged at every hop. Without this,
    wrapping a hand-built id to satisfy the type checker would also take it
    out of this check."""

    @pytest.mark.parametrize("wrapper", ["NodeId", "read_node_id", "ir.NodeId"])
    def test_hop_one_sees_through_the_wrapper(self, tmp_path: Path, wrapper: str) -> None:
        root = _write(tmp_path, f'''
            def f(path, line, name):
                return Symbol(id={wrapper}(f"lang:{{path}}:{{line}}:{{name}}:function"))
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "malformed_span_segment" in drift

    def test_hop_two_sees_through_the_wrapper(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                sym_id = NodeId(f"lang:{path}:{line}:{name}:function")
                return Symbol(id=sym_id)
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "malformed_span_segment" in drift

    def test_hop_three_sees_through_the_wrapper(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def _make_symbol_id(path, line, name):
                return NodeId(f"lang:{path}:{line}:{name}:function")

            def f(path, line, name):
                return Symbol(id=_make_symbol_id(path, line, name))
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "malformed_span_segment" in drift

    def test_the_kind_slot_is_read_through_the_wrapper(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def _make_symbol_id(path, line, name):
                return NodeId(f"lang:{path}:{line}-{line}:{name}:task")

            def f(path, line, name):
                return Symbol(id=_make_symbol_id(path, line, name), kind="function")
        ''')
        (drift,) = find_id_construction_drift(root)
        assert "kind_slot_mismatch" in drift

    def test_a_wrapped_conformant_template_passes(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path, line, name):
                return Symbol(id=NodeId(f"lang:{path}:{line}-{line}:{name}:function"))
        ''')
        assert find_id_construction_drift(root) == []

    def test_any_other_call_is_not_seen_through(self, tmp_path: Path) -> None:
        """Only the two wrappers: an unknown function may change the text."""
        root = _write(tmp_path, '''
            def f(path, line, name):
                return Symbol(id=str(f"lang:{path}:{line}:{name}:function"))
        ''')
        assert find_id_construction_drift(root) == []


class TestTheEdgeEndpoints:
    """``src=`` / ``dst=`` of an edge answer to the NODE grammar (WI-tatop).

    An endpoint is a node's id, so a hand-built one is judged by ADR-0036's
    five-slot grammar through the same three hops as ``Symbol(id=...)``. About
    40 endpoints in 23 producers were spelled ``f"{rel_path}:file"``,
    ``f"meson:subdir:{d}"``, ``f"r:external:{pkg}::{f}"``: none parses, and
    finalize folded every edge of one spelling into one ``<unknown>`` node.
    """

    @pytest.mark.parametrize("call", [
        'Edge.create(src=f"{path}:file", dst=d, edge_type="imports", line=1)',
        'Edge.create(src=s, dst=f"meson:subdir:{name}", edge_type="includes", line=1)',
        'Edge(id=i, src=s, dst=f"r:external:{pkg}::{name}", edge_type="calls")',
        'Edge.create(f"file:{path}", d, "imports", 1)',
        'Edge.create(s, f"twig:template:{name}", "includes", 1)',
    ])
    def test_a_hand_built_endpoint_is_judged(self, tmp_path: Path, call: str) -> None:
        root = _write(tmp_path, f'''
            def f(path, name, pkg, s, d, i):
                return {call}
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "edge_endpoint" in drift[0]

    def test_through_a_local(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(s, name, found):
                dst_id = found
                if dst_id is None:
                    dst_id = f"racket:unresolved:{name}"
                return Edge.create(src=s, dst=dst_id, edge_type="calls", line=1)
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "wrong_field_count" in drift[0]

    def test_through_a_same_module_helper(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def _dst(name):
                return f"verilog:external:{name}:module"

            def f(s, name):
                return Edge.create(src=s, dst=_dst(name), edge_type="calls", line=1)
        ''')
        assert len(find_id_construction_drift(root)) == 1

    @pytest.mark.parametrize("expr", [
        'sym.id if sym is not None else f"pony:{path}"',
        'found or f"robot:{path}"',
        'found or (a and f"x:{path}")',
    ])
    def test_each_branch_of_a_choice_is_judged(self, tmp_path: Path, expr: str) -> None:
        root = _write(tmp_path, f'''
            def f(sym, found, a, path):
                src = {expr}
                return Edge.create(src=src, dst=found, edge_type="calls", line=1)
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "edge_endpoint (src)" in drift[0]

    def test_a_bare_literal_endpoint_is_judged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(s):
                return Edge.create(src=s, dst="amsmath", edge_type="imports", line=1)
        ''')
        assert len(find_id_construction_drift(root)) == 1

    def test_a_concatenated_endpoint_is_refused(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(s, name):
                return Edge.create(src=s, dst="x:" + name, edge_type="calls", line=1)
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "concatenated" in drift[0]

    @pytest.mark.parametrize("endpoint", [
        'make_file_id("latex", path)',
        'external_target("nix", name)[0]',
        'f"{lang}:{path}:0-0:{name}:unresolved"',
        'NodeId(f"go:{path}:0-0:{name}:unresolved")',
        'sym.id',
    ])
    def test_a_minted_or_conformant_endpoint_passes(
        self, tmp_path: Path, endpoint: str,
    ) -> None:
        root = _write(tmp_path, f'''
            def f(lang, path, name, sym):
                return Edge.create(src={endpoint}, dst={endpoint}, edge_type="calls", line=1)
        ''')
        assert find_id_construction_drift(root) == []

    def test_an_empty_initializer_is_not_an_endpoint(self, tmp_path: Path) -> None:
        """``ref = ""`` before a loop that sets it is a sentinel the producer
        tests before building the edge, not a value the edge can carry."""
        root = _write(tmp_path, '''
            def f(path, nodes):
                ref = ""
                for n in nodes:
                    ref = make_file_id("latex", n)
                if ref:
                    return Edge.create(src=ref, dst=ref, edge_type="imports", line=1)
        ''')
        assert find_id_construction_drift(root) == []

    def test_positions_after_a_starred_argument_are_not_guessed(
        self, tmp_path: Path,
    ) -> None:
        root = _write(tmp_path, '''
            def f(parts, name):
                return Edge.create(*parts, dst=f"nix:external:{name}", line=1)
        ''')
        drift = find_id_construction_drift(root)
        assert len(drift) == 1
        assert "edge_endpoint (dst)" in drift[0]

    def test_another_classes_create_is_not_an_edge(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(path):
                return AnalysisRun.create(src=f"{path}:file", dst="x")
        ''')
        assert find_id_construction_drift(root) == []


def _write_modules(tmp_path: Path, modules: dict[str, str]) -> Path:
    """Several modules of one package ``m``, by file name."""
    pkg = tmp_path / "packages" / "p" / "src" / "m"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name, body in modules.items():  # an "__init__.py" entry replaces the empty one
        (pkg / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def survey_id_construction(root: Path) -> tuple[list[str], list[str]]:
    """``(violations, unjudged)`` of :func:`find_id_construction_drift`."""
    unjudged: list[str] = []
    violations = find_id_construction_drift(root, unjudged=unjudged)
    return violations, unjudged


def _reasons(unjudged: list[str]) -> list[str]:
    return [u.split("not judged: ", 1)[1].split(" ", 1)[0] for u in unjudged]


class TestAnEndpointThroughAParameter:
    """An endpoint that arrives through a parameter is followed to the call
    sites of its function, one call level, in its module and through an
    import in another (WI-jozih). Before, the hop stopped at the parameter's
    name: ``make_unresolved_edge``'s ``src_id`` was never read."""

    def test_a_hand_built_argument_in_the_same_module_is_judged(
        self, tmp_path: Path,
    ) -> None:
        root = _write(tmp_path, '''
            def edge_to(src_id, name):
                return Edge.create(src=src_id, dst=make_file_id("c", name), line=1)

            def caller(path, name):
                return edge_to(f"{path}:file", name)
        ''')
        violations, unjudged = survey_id_construction(root)
        assert len(violations) == 1
        assert "mod.py:6 — edge_endpoint (src)" in violations[0]
        assert "reaches the edge at" in violations[0]
        assert unjudged == []

    def test_a_hand_built_argument_in_another_module_is_judged(
        self, tmp_path: Path,
    ) -> None:
        root = _write_modules(tmp_path, {
            "edges.py": '''
                def edge_to(src_id, name, *, line=1):
                    return Edge.create(src=src_id, dst=make_file_id("c", name), line=line)
            ''',
            "a.py": '''
                from .edges import edge_to

                def caller(path, name):
                    return edge_to(f"meson:subdir:{path}", name)
            ''',
            "b.py": '''
                from m import edges

                def caller(sym, name):
                    return edges.edge_to(sym.id, name)
            ''',
        })
        violations, unjudged = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/a.py:5"]
        assert unjudged == []

    def test_a_minted_argument_passes(self, tmp_path: Path) -> None:
        """Control: the same function called with a symbol's id and a factory."""
        root = _write(tmp_path, '''
            def edge_to(src_id, name):
                return Edge.create(src=src_id, dst=make_file_id("c", name), line=1)

            def caller(sym, path, name):
                edge_to(sym.id, name)
                return edge_to(make_file_id("c", path), name)
        ''')
        assert survey_id_construction(root) == ([], [])

    def test_a_method_binds_past_self(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            class A:
                def edge_to(self, src_id):
                    return Edge.create(src=src_id, dst=make_file_id("c", "x"), line=1)

                def caller(self, path):
                    return self.edge_to(f"{path}:file")
        ''')
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/mod.py:7"]

    def test_a_default_is_read_when_the_call_omits_it(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def edge_to(name, src_id="file:x"):
                return Edge.create(src=src_id, dst=make_file_id("c", name), line=1)

            def caller(name):
                return edge_to(name)
        ''')
        violations, _ = survey_id_construction(root)
        assert len(violations) == 1
        assert "mod.py:2" in violations[0]

    def test_a_parameter_passed_on_is_reported_unjudged(self, tmp_path: Path) -> None:
        """One call level: a call site that passes its own parameter is not
        followed further, and is REPORTED, never passed silently."""
        root = _write(tmp_path, '''
            def edge_to(src_id):
                return Edge.create(src=src_id, dst=make_file_id("c", "x"), line=1)

            def middle(given):
                return edge_to(given)
        ''')
        violations, unjudged = survey_id_construction(root)
        assert violations == []
        assert _reasons(unjudged) == ["parameter_beyond_one_call"]
        assert unjudged[0].startswith("packages/p/src/m/mod.py:3 — edge_endpoint (src)")

    def test_a_parameter_nothing_calls_is_reported_unjudged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def edge_to(src_id):
                return Edge.create(src=src_id, dst=make_file_id("c", "x"), line=1)
        ''')
        assert _reasons(survey_id_construction(root)[1]) == ["parameter_without_call_site"]

    def test_an_argument_in_star_args_is_reported_unjudged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def edge_to(src_id):
                return Edge.create(src=src_id, dst=make_file_id("c", "x"), line=1)

            def caller(parts, options):
                edge_to(*parts)
                return edge_to(**options)
        ''')
        assert _reasons(survey_id_construction(root)[1]) == ["argument_not_bound"]


class TestAnEndpointBuiltByAHelper:
    """An endpoint returned by a helper of another module is read at that
    helper's ``return``, one module away (WI-jozih)."""

    def test_a_helper_of_another_module_is_read(self, tmp_path: Path) -> None:
        root = _write_modules(tmp_path, {
            "ids.py": '''
                def stand_in(name):
                    dst = f"verilog:external:{name}:module"
                    return dst
            ''',
            "a.py": '''
                from .ids import stand_in

                def f(sym, name):
                    return Edge.create(src=sym.id, dst=stand_in(name), line=1)
            ''',
        })
        violations, unjudged = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/ids.py:3"]
        assert unjudged == []

    def test_a_helper_argument_comes_back_from_the_caller(self, tmp_path: Path) -> None:
        """A helper returning its own parameter returns what its caller passed."""
        root = _write_modules(tmp_path, {
            "ids.py": '''
                def same(value):
                    return value
            ''',
            "a.py": '''
                from .ids import same

                def f(sym, path):
                    return Edge.create(src=sym.id, dst=same(f"{path}:file"), line=1)
            ''',
        })
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/a.py:5"]

    def test_a_tuple_part_is_followed(self, tmp_path: Path) -> None:
        root = _write_modules(tmp_path, {
            "ids.py": '''
                def pair(name):
                    return f"x:{name}", None
            ''',
            "a.py": '''
                from .ids import pair

                def f(sym, name):
                    dst, _ref = pair(name)
                    return Edge.create(src=sym.id, dst=dst, line=1)
            ''',
        })
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/ids.py:3"]

    def test_a_helper_two_modules_away_is_reported_unjudged(self, tmp_path: Path) -> None:
        root = _write_modules(tmp_path, {
            "ids.py": '''
                def inner(name):
                    return f"x:{name}"
            ''',
            "mid.py": '''
                from .ids import inner

                def outer(name):
                    return inner(name)
            ''',
            "a.py": '''
                from .mid import outer

                def f(sym, name):
                    return Edge.create(src=sym.id, dst=outer(name), line=1)
            ''',
        })
        violations, unjudged = survey_id_construction(root)
        assert violations == []
        assert _reasons(unjudged) == ["helper_beyond_one_level"]

    @pytest.mark.parametrize(("endpoint", "reason"), [
        ("lookup(name)", "helper_not_located"),
        ("registry.get(name)", "computed"),
        ("registry[name]", "computed"),
        ("emission.action_id", "computed"),
    ])
    def test_a_value_it_cannot_read_is_reported_unjudged(
        self, tmp_path: Path, endpoint: str, reason: str,
    ) -> None:
        root = _write(tmp_path, f'''
            REGISTRY = {{}}

            def f(sym, name, emission):
                registry = REGISTRY
                return Edge.create(src=sym.id, dst={endpoint}, line=1)

            def g(sym, name, emission):
                return f(sym.id, name, emission)
        ''')
        violations, unjudged = survey_id_construction(root)
        assert violations == []
        assert reason in _reasons(unjudged)

    def test_a_loop_variable_is_reported_unjudged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def f(sym, ids):
                for dst in ids:
                    yield Edge.create(src=sym.id, dst=dst, line=1)

            def g(sym):
                return f(sym, [])
        ''')
        assert _reasons(survey_id_construction(root)[1]) == ["computed"]

    def test_a_module_constant_is_judged(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            ROOT = "root:file"

            def f(sym):
                return Edge.create(src=sym.id, dst=ROOT, line=1)
        ''')
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/mod.py:2"]

    def test_a_re_exported_helper_is_read(self, tmp_path: Path) -> None:
        root = _write_modules(tmp_path, {
            "ids.py": '''
                def stand_in(name):
                    return f"x:{name}"
            ''',
            "__init__.py": "from .ids import stand_in\n",
            "a.py": '''
                from m import stand_in

                def f(sym, name):
                    return Edge.create(src=sym.id, dst=stand_in(name), line=1)
            ''',
        })
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/ids.py:3"]

    def test_a_re_export_of_no_function_is_not_located(self, tmp_path: Path) -> None:
        root = _write_modules(tmp_path, {
            "ids.py": "STAND_IN = None\n",
            "__init__.py": "from .ids import STAND_IN\n",
            "a.py": '''
                from m import STAND_IN

                def f(sym, name):
                    return Edge.create(src=sym.id, dst=STAND_IN(name), line=1)
            ''',
        })
        assert _reasons(survey_id_construction(root)[1]) == ["helper_not_located"]

    @pytest.mark.parametrize(("body", "reason"), [
        # A helper imported from outside the tree.
        ('''
            from os.path import join

            def f(sym, name):
                return Edge.create(src=sym.id, dst=join(name), line=1)
        ''', "helper_not_located"),
        # A helper that returns no value.
        ('''
            def nothing(name):
                print(name)

            def f(sym, name):
                return Edge.create(src=sym.id, dst=nothing(name), line=1)
        ''', "computed"),
        # A helper returning a parameter its call does not pass.
        ('''
            def pick(value, other, *rest):
                return other

            def f(sym, name, parts):
                return Edge.create(src=sym.id, dst=pick(*parts), line=1)
        ''', "argument_not_bound"),
        # A tuple part the return does not spell element by element.
        ('''
            def pair(parts):
                return (*parts, None)

            def f(sym, parts):
                dst, _ref = pair(parts)
                return Edge.create(src=sym.id, dst=dst, line=1)
        ''', "computed"),
        # A tuple part of a tuple part.
        ('''
            def nested():
                return (("a:b", None), None)

            def f(sym):
                pair, _ = nested()
                dst, _ref = pair
                return Edge.create(src=sym.id, dst=dst, line=1)
        ''', "computed"),
        # A name nothing in the module binds.
        ('''
            def f(sym):
                return Edge.create(src=sym.id, dst=UNDEFINED, line=1)
        ''', "computed"),
        # A ``with`` target.
        ('''
            def f(sym, opener):
                with opener() as dst:
                    return Edge.create(src=sym.id, dst=dst, line=1)

            def g(sym):
                return f(sym, None)
        ''', "computed"),
        # A keyword-only parameter with no default, omitted by its call.
        ('''
            def edge_to(*, src_id):
                return Edge.create(src=src_id, dst=make_file_id("c", "x"), line=1)

            def caller():
                return edge_to()
        ''', "argument_not_bound"),
    ])
    def test_what_it_cannot_follow_is_reported(
        self, tmp_path: Path, body: str, reason: str,
    ) -> None:
        violations, unjudged = survey_id_construction(_write(tmp_path, body))
        assert violations == []
        assert reason in _reasons(unjudged)

    def test_a_keyword_only_default_is_read(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def edge_to(name, *, src_id="file:x"):
                return Edge.create(src=src_id, dst=make_file_id("c", name), line=1)

            def caller(name):
                return edge_to(name)
        ''')
        violations, _ = survey_id_construction(root)
        assert [v.split(" — ")[0] for v in violations] == ["packages/p/src/m/mod.py:2"]

    def test_a_recursive_helper_is_read_once(self, tmp_path: Path) -> None:
        root = _write(tmp_path, '''
            def dst_of(name, depth):
                if depth:
                    return dst_of(name, depth - 1)
                return make_file_id("c", name)

            def f(sym, name):
                return Edge.create(src=sym.id, dst=dst_of(name, 3), line=1)
        ''')
        assert survey_id_construction(root) == ([], [])


class TestTheLiveTree:
    def test_live_tree_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        drift = find_id_construction_drift(repo_root)
        assert drift == [], (
            f"{len(drift)} id-construction violation(s):\n  "
            + "\n  ".join(drift)
        )

    def test_every_unjudged_endpoint_names_a_declared_reason(self) -> None:
        """What the check cannot follow is listed with a reason, never dropped:
        the runtime count ``AnalysisRun.non_id_edge_endpoints`` guards it."""
        repo_root = Path(__file__).resolve().parents[3]
        _violations, unjudged = survey_id_construction(repo_root)
        assert unjudged, "the tree has endpoints the check cannot follow"
        assert set(_reasons(unjudged)) <= set(UNJUDGED_REASONS)
