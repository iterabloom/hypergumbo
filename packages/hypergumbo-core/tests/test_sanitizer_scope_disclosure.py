# SPDX-License-Identifier: AGPL-3.0-or-later
"""The published sanitizer scope — INV-karud clause (b)'s limits as data.

Clause (b) asks that a sanitizer on a route actually neutralize the flow. It has
been measured and it HOLDS — but only for the flows it can speak about, and that
population is much smaller than the source/sink catalogue's. Every built-in
sanitizer is cryptographic, so a repository-wide "0 sanitized flows" is
ambiguous exactly the way L58 describes: it may mean nothing was protected, or
that nothing *could* be, because the claims' taint labels and the catalogue's
input labels are disjoint. A nine-repo cohort produced zero for the second
reason. That is why the scope ships as data rather than as prose in an ADR.

Two claims here carry an executable re-evaluation trigger (R16): the taint
categories are DERIVED from the catalogue rather than listed, so extending it
moves the disclosure automatically; and ``same_function_honoured_by`` is a
declared constant whose test fails the moment the structural pass gains the
ability the constant says it lacks.
"""
from __future__ import annotations

from hypergumbo_core.dataflow_scope import (
    SAME_FUNCTION_SANITIZATION_HONOURED_BY,
    compute_dataflow_scope,
    compute_sanitizer_scope,
    dataflow_scope_dict,
    render_dataflow_scope_text,
)
from hypergumbo_core.taint import load_builtin_taint_catalog

_LANGS = ("python", "go", "java", "rust", "typescript", "javascript")


class TestSanitizerScopeIsDerived:
    """Counted from the catalogue production loads, never from a list here."""

    def test_scope_matches_the_loaded_catalogue(self) -> None:
        """The instrument is production's own accessor (L53).

        Re-deriving these numbers with a predicate of my own is the failure
        this project has counted four times in one session; the test asserts
        agreement with ``sanitizers_for_language`` rather than with a literal.
        """
        catalog = load_builtin_taint_catalog()
        scope = compute_sanitizer_scope(catalog, _LANGS)
        expected = sum(
            len(catalog.sanitizers_for_language(lang)) for lang in _LANGS
        )
        assert scope.total == expected
        assert scope.total > 0, "non-vacuity: the catalogue must not be empty"

    def test_only_languages_with_entries_are_listed(self) -> None:
        """JavaScript has taint sinks and no sanitizers; it must not appear."""
        catalog = load_builtin_taint_catalog()
        scope = compute_sanitizer_scope(catalog, _LANGS)
        assert "javascript" not in scope.languages
        assert "python" in scope.languages
        for lang in scope.languages:
            assert catalog.sanitizers_for_language(lang)

    def test_categories_and_labels_come_from_the_entries(self) -> None:
        """The vocabulary limit, stated as the catalogue states it.

        Asserts the RULE — every reported category is a real entry's
        ``input -> output`` pair and every entry's pair is reported — rather
        than the two string literals the catalogue happens to hold today. A
        literal assertion would have to be edited by whoever extends the
        catalogue, which is precisely when nobody re-reads the disclosure.
        """
        catalog = load_builtin_taint_catalog()
        scope = compute_sanitizer_scope(catalog, _LANGS)
        entries = [
            san for lang in _LANGS
            for san in catalog.sanitizers_for_language(lang)
        ]
        assert set(scope.taint_categories) == {
            f"{s.input_taint} -> {s.output_taint}" for s in entries
        }
        assert set(scope.sanitizable_labels) == {s.input_taint for s in entries}

    def test_empty_language_set_yields_an_empty_scope(self) -> None:
        scope = compute_sanitizer_scope(load_builtin_taint_catalog(), [])
        assert scope.total == 0
        assert scope.languages == ()
        assert scope.taint_categories == ()
        assert scope.sanitizable_labels == ()


class TestSameFunctionConstant:
    """``same_function_honoured_by`` is a claim with a test on it (R16)."""

    def test_structural_pass_is_not_listed(self) -> None:
        """The declared limit.

        ``propagate_taint_structural`` decides reachability on the call graph,
        where two calls sharing a caller have no order between them — so the
        graph cannot distinguish encrypt-then-write from write-then-encrypt.
        If that pass ever gains statement ordering, this test fails and the
        constant has to be revisited rather than quietly outliving its truth.
        """
        assert "structural" not in SAME_FUNCTION_SANITIZATION_HONOURED_BY
        assert "ddg" in SAME_FUNCTION_SANITIZATION_HONOURED_BY

    def test_constant_reaches_the_json_surface(self) -> None:
        block = dataflow_scope_dict(
            [], {}, compute_sanitizer_scope(load_builtin_taint_catalog(), _LANGS),
        )
        scope = block["sanitizer_scope"]
        assert scope["same_function_honoured_by"] == list(
            SAME_FUNCTION_SANITIZATION_HONOURED_BY
        )


class TestBothSurfacesCarryIt:
    """A disclosure that exists only under ``--json`` is half shipped.

    WI-bifob's exclusion bucket reached the dataclass and never the text
    renderer, so a text reader of a violated claim never learned flows had been
    set aside. Not worth repeating.
    """

    def test_json_block_is_present_even_with_no_scope_supplied(self) -> None:
        """Envelope shape is stable; absence never has to be interpreted."""
        block = dataflow_scope_dict([], {})
        assert "sanitizer_scope" in block
        assert block["sanitizer_scope"]["total"] == 0
        assert block["sanitizer_scope"]["sanitizable_labels"] == []

    def test_text_renderer_states_both_limits(self) -> None:
        catalog = load_builtin_taint_catalog()
        rows = compute_dataflow_scope(catalog, ["python"])
        lines = render_dataflow_scope_text(
            rows, {"structural": 3}, compute_sanitizer_scope(catalog, _LANGS),
        )
        body = "\n".join(lines)
        assert "Sanitizers:" in body
        assert "plaintext -> ciphertext" in body
        # The vocabulary limit: a zero must be readable as "not expressible".
        assert "not expressible" in body
        # The same-function limit, naming the pass that cannot honour it.
        assert "SAME function" in body
        assert "UNSANITIZED" in body

    def test_text_renderer_stays_silent_with_no_rows(self) -> None:
        """No analyzed language → nothing to disclose, not an empty header."""
        assert render_dataflow_scope_text([], {}) == []
