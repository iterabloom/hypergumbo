# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §12 / WI-romuh: the lint that refuses an incumbent-fed cross-backend test.

The rule: a test of cross-backend behaviour may not derive the alternative
arm's input from the incumbent arm's output. The parity test that did (fed
``rust.py``'s own spans back into the SCIP helper) passed for months over a
feature measuring 0 of 52 in production (INV-dolud). A grep cannot see a
call-site rule, so the check is an AST taint walk per test function: names
bound from a call to an incumbent producer (or to a same-module helper that
calls one) are tainted, taint follows assignment, unpacking, ``for`` targets
and comprehension targets, and a call to a cross-backend consumer whose
arguments mention a tainted name — or contain the producer call outright —
is an offence. A test may say it feeds on purpose with
``@pytest.mark.incumbent_fed("<reason>")``: an extraction contract that
proves a helper agrees with the incumbent claims nothing about production
input, and the reason is read by people.

These tests build synthetic test modules under ``tmp_path`` so the boundary
is pinned rather than rediscovered, then run the lint over the live tree.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from hypergumbo_core.check_recorded_producer_input import (
    CROSS_BACKEND_CONSUMERS,
    find_incumbent_fed_tests,
    incumbent_producer_names,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module(tmp_path: Path, body: str, name: str = "test_synthetic.py") -> Path:
    tests = tmp_path / "packages" / "hypergumbo-x" / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    path = tests / name
    path.write_text(textwrap.dedent(body))
    return path


def _lint(tmp_path: Path) -> list[str]:
    return find_incumbent_fed_tests(
        tmp_path, producers=frozenset({"analyze_rust"}),
    )


class TestTheVocabularyIsDeclared:
    def test_the_incumbent_producers_come_from_the_registry(self) -> None:
        """The incumbent of every language with two anchored producers is the
        tree-sitter backend's entry function — derived, not listed."""
        assert "analyze_rust" in incumbent_producer_names()

    def test_the_consumers_name_the_scip_shim_and_the_parity_helpers(self) -> None:
        assert {
            "compute_rust_stable_id_from_source",
            "reassign_rust_stable_ids",
            "translate_scip_to_hg",
            "scip_index_to_symbols",
        } <= CROSS_BACKEND_CONSUMERS


class TestOffences:
    def test_a_producer_result_passed_straight_to_a_consumer(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_direct(tmp_path):
                out = reassign_rust_stable_ids(analyze_rust(tmp_path).symbols, reader)
        """)
        found = _lint(tmp_path)
        assert len(found) == 1
        assert "test_direct" in found[0]
        assert "analyze_rust" in found[0] and "reassign_rust_stable_ids" in found[0]

    def test_taint_follows_assignment_and_a_for_loop(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_loop(tmp_path):
                baseline = analyze_rust(tmp_path)
                for (start, end), expected in baseline.items():
                    got = compute_rust_stable_id_from_source(src, start, end)
                    assert got == expected
        """)
        found = _lint(tmp_path)
        assert len(found) == 1 and "test_loop" in found[0]

    def test_taint_follows_a_comprehension_target(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_comp(tmp_path):
                spans = [s.span for s in analyze_rust(tmp_path).symbols]
                ids = {sp: compute_rust_stable_id_from_source(src, *sp) for sp in spans}
        """)
        found = _lint(tmp_path)
        assert len(found) == 1 and "test_comp" in found[0]

    def test_taint_follows_a_same_module_helper_that_calls_the_producer(self, tmp_path: Path) -> None:
        """``_collect_rust_py_stable_ids`` is exactly this shape."""
        _module(tmp_path, """
            def _collect(tmp_path):
                return {s.span: s.stable_id for s in analyze_rust(tmp_path).symbols}

            def test_helper(tmp_path):
                baseline = _collect(tmp_path)
                sym = make(span=next(iter(baseline)))
                reassign_rust_stable_ids([sym], reader)
        """)
        found = _lint(tmp_path)
        assert len(found) == 1 and "test_helper" in found[0]

    def test_run_analyzer_with_the_incumbents_name_is_a_source(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_by_name(tmp_path):
                result = run_analyzer("rust", tmp_path)
                translate_scip_to_hg(result, reader)
        """)
        found = find_incumbent_fed_tests(tmp_path, producers=frozenset({"rust"}))
        assert len(found) == 1 and 'run_analyzer("rust")' in found[0]

    def test_taint_follows_augmented_assignment(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_aug(tmp_path):
                spans = []
                spans += analyze_rust(tmp_path).symbols
                reassign_rust_stable_ids(spans, reader)
        """)
        assert len(_lint(tmp_path)) == 1

    def test_taint_follows_a_walrus_binding(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_walrus(tmp_path):
                if (base := analyze_rust(tmp_path)):
                    translate_scip_to_hg(base, reader)
        """)
        assert len(_lint(tmp_path)) == 1

    def test_taint_follows_a_with_binding(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_with(tmp_path):
                with analyze_rust(tmp_path) as base:
                    translate_scip_to_hg(base, reader)
        """)
        assert len(_lint(tmp_path)) == 1

    def test_a_method_on_a_test_class_is_a_test(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            class TestParity:
                def test_method(self, tmp_path):
                    base = analyze_rust(tmp_path)
                    translate_scip_to_hg(base, reader)
        """)
        assert len(_lint(tmp_path)) == 1


class TestAllowed:
    def test_recorded_input_with_the_incumbent_only_as_expectation(self, tmp_path: Path) -> None:
        """Contract 2 of the parity test: the consumer is fed RECORDED ranges;
        the incumbent's output is compared against, never fed."""
        _module(tmp_path, """
            from recorded_rust_analyzer_1_94_0 import callable_item_to_token_lines

            def test_recorded(tmp_path):
                baseline = analyze_rust(tmp_path)
                assert set(baseline) == set(callable_item_to_token_lines())
                for item, token in callable_item_to_token_lines().items():
                    got = compute_rust_stable_id_from_source(src, *token)
                    assert got == baseline[item] or got is None
        """)
        assert _lint(tmp_path) == []

    def test_the_incumbent_fed_marker_allows_an_extraction_contract(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            import pytest

            @pytest.mark.incumbent_fed("extraction contract: proves the helper agrees with rust.py's own spans")
            def test_marked(tmp_path):
                baseline = analyze_rust(tmp_path)
                for (start, end), expected in baseline.items():
                    assert compute_rust_stable_id_from_source(src, start, end) == expected
        """)
        assert _lint(tmp_path) == []

    def test_a_marker_without_a_reason_does_not_allow(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            import pytest

            @pytest.mark.incumbent_fed
            def test_bare(tmp_path):
                reassign_rust_stable_ids(analyze_rust(tmp_path).symbols, reader)
        """)
        found = _lint(tmp_path)
        assert len(found) == 1 and "reason" in found[0]

    def test_a_non_test_function_is_not_reported(self, tmp_path: Path) -> None:
        """Helpers are propagation sources, not offences; the offence is the
        TEST that feeds. A helper nobody calls from a test reports nothing."""
        _module(tmp_path, """
            def _fixture_builder(tmp_path):
                return reassign_rust_stable_ids(analyze_rust(tmp_path).symbols, reader)
        """)
        assert _lint(tmp_path) == []

    def test_a_consumer_fed_untainted_input_beside_a_producer_call(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_side_by_side(tmp_path):
                live = analyze_rust(tmp_path)
                recorded = translate_scip_to_hg(aardvark_dns_index_bytes(), reader)
                assert len(live.symbols) < len(recorded[0])
        """)
        assert _lint(tmp_path) == []

    def test_only_test_modules_are_read(self, tmp_path: Path) -> None:
        _module(tmp_path, """
            def test_direct(tmp_path):
                reassign_rust_stable_ids(analyze_rust(tmp_path).symbols, reader)
        """, name="helpers_recorded.py")
        assert _lint(tmp_path) == []


class TestLiveTree:
    def test_live_tree_passes(self) -> None:
        """Every incumbent-fed test in the tree is either recorded-input or
        carries the marker with a reason. This is the CI gate."""
        offenders = find_incumbent_fed_tests(REPO_ROOT)
        assert offenders == [], "\n".join(offenders)
