# SPDX-License-Identifier: AGPL-3.0-or-later
"""The static half of ADR-0034's id-construction discipline (WI-vodin).

WHAT THESE TESTS PIN, and why the population is narrow. The filed shape was
``Symbol(id=f"...")`` and the tree contains exactly ONE of those. 231 of the
470 ``id=`` arguments are a bare local, and 20 of the f-strings hide behind
one, so a check that stops at the constructor sees ``id=sym_id`` and learns
nothing. These tests pin the three-hop resolution that makes the rule reach the
population it is about, and the grammar it applies once it gets there.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.check_id_construction import (
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
        # The commonest live defect: `line: int` interpolated where the
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
        # 244 id-shaped f-strings exist in the tree and only 40 reach a
        # constructor. Pricing the rule on the wrong unit is what makes a
        # linter unusable.
        root = _write(tmp_path, '''
            def f(a, b):
                return f"site:{a}:{b}:note"
        ''')
        assert find_id_construction_drift(root) == []

    def test_an_edge_id_answers_to_its_own_grammar_not_the_node_one(
        self, tmp_path: Path,
    ) -> None:
        # WI-vodin asked for Edge.id to be checked against "the same canonical
        # shape it enforces on Symbol.id". Measured on a live survey, every one
        # of apollo-server's 18,283 edge ids is `edge:sha256:<16hex>`, so that
        # rule would have flagged all of them. Counterparts in ROLE, not SHAPE.
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


class TestTheLiveTree:
    def test_live_tree_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        drift = find_id_construction_drift(repo_root)
        assert drift == [], (
            f"{len(drift)} id-construction violation(s):\n  "
            + "\n  ".join(drift)
        )
