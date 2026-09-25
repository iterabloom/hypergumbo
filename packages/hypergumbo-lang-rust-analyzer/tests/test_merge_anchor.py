# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §10 / WI-hohuh: the two Rust anchors map one declaration to one key.

The registry test the row prescribes: the incumbent (tree-sitter ``rust``)
and the alternative (``rust_analyzer``) must map the two records they emit
for ONE declaration to the SAME merge key, and the declared span roles must
let the pass tell two same-named declarations apart. Per §12 the SCIP side
is RECORDED producer output (:mod:`recorded_rust_analyzer_1_94_0`); the
tree-sitter side is the incumbent run live on the same source — which is
the one arm CI can run.

The recorded sample has two callables named ``greet`` (the trait
declaration and the impl). A key alone cannot separate them; the token-in-
item containment the declared span roles express can, and that is what the
last test pins.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    RegisteredAnalyzer,
    ensure_discovered,
    get_analyzer,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.rust import analyze_rust

from recorded_rust_analyzer_1_94_0 import (
    DEFINITIONS,
    PRODUCER_VERSION,
    SAMPLE_SOURCE,
    RecordedDefinition,
    lines_of,
)


def _arms() -> tuple[RegisteredAnalyzer, RegisteredAnalyzer]:
    ensure_discovered()
    incumbent, scip = get_analyzer("rust"), get_analyzer("rust_analyzer")
    assert incumbent is not None and scip is not None
    return incumbent, scip


def _anchor(analyzer: RegisteredAnalyzer) -> MergeAnchor:
    assert isinstance(analyzer.merge, MergeAnchor), analyzer.name
    return analyzer.merge


@pytest.fixture(scope="module")
def tree_sitter_symbols(tmp_path_factory: pytest.TempPathFactory) -> list[Symbol]:
    root = tmp_path_factory.mktemp("crate")
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text(SAMPLE_SOURCE)
    symbols = [s for s in analyze_rust(root).symbols if s.path.endswith("src/lib.rs")]
    assert symbols, "the incumbent emitted nothing for the sample"
    return symbols


def _recorded_declarations() -> list[RecordedDefinition]:
    # The crate namespace is a file-level record with no tree-sitter twin.
    return [d for d in DEFINITIONS if d["kind"] != "NAMESPACE"]


class TestTheRecordedProducerShape:
    def test_the_record_names_its_producer(self) -> None:
        assert PRODUCER_VERSION.startswith("rust-analyzer 1.94.0")

    def test_every_callable_range_is_the_identifier_token(self) -> None:
        """Why the SCIP arm declares ``span_role=token`` (INV-lodum): on the
        recorded producer, every METHOD definition's ``range`` is single-line
        even when the item spans several."""
        for definition in DEFINITIONS:
            if definition["kind"] == "METHOD":
                assert len(definition["range"]) == 3, definition["name"]

    def test_the_scip_arm_declares_the_token_role_and_the_incumbent_the_item_role(self) -> None:
        incumbent, scip = _arms()
        assert _anchor(incumbent).span_role == SPAN_ROLE_ITEM
        assert _anchor(scip).span_role == SPAN_ROLE_TOKEN


class TestOneDeclarationOneKey:
    def test_each_recorded_definition_keys_to_exactly_one_tree_sitter_record(
        self, tree_sitter_symbols: list[Symbol],
    ) -> None:
        """For every recorded SCIP definition there is EXACTLY ONE tree-sitter
        symbol with the same declared key whose item span contains the
        SCIP token line. Zero would mean the anchors do not agree; two would
        mean the roles cannot separate same-named declarations."""
        incumbent, scip = _arms()
        incumbent_key, scip_key = _anchor(incumbent).name_key, _anchor(scip).name_key
        for definition in _recorded_declarations():
            token_line, _ = lines_of(definition["range"])
            key = scip_key(definition["name"])
            twins = [
                s for s in tree_sitter_symbols
                if incumbent_key(s.name) == key
                and s.span.start_line <= token_line <= s.span.end_line
            ]
            assert len(twins) == 1, (definition["symbol"], [t.name for t in twins])

    def test_the_two_greets_are_separated_by_containment_not_by_key(
        self, tree_sitter_symbols: list[Symbol],
    ) -> None:
        incumbent, scip = _arms()
        incumbent_key, scip_key = _anchor(incumbent).name_key, _anchor(scip).name_key
        greets = [d for d in DEFINITIONS if d["name"] == "greet"]
        assert len(greets) == 2
        same_key = [s for s in tree_sitter_symbols if incumbent_key(s.name) == scip_key("greet")]
        assert len(same_key) == 2  # the key alone is ambiguous ...
        containing = {
            lines_of(d["range"])[0]: [
                s.name for s in same_key
                if s.span.start_line <= lines_of(d["range"])[0] <= s.span.end_line
            ]
            for d in greets
        }
        assert all(len(v) == 1 for v in containing.values()), containing  # ... containment is not

    def test_the_recorded_enclosing_range_is_the_incumbents_item_span(
        self, tree_sitter_symbols: list[Symbol],
    ) -> None:
        """What WI-kokiz's item-span fold will compare: once the SCIP record is
        spanned from ``enclosing_range`` its extent equals the tree-sitter
        item's, line for line, on every callable of the recorded sample."""
        incumbent, scip = _arms()
        incumbent_key, scip_key = _anchor(incumbent).name_key, _anchor(scip).name_key
        for definition in _recorded_declarations():
            if definition["kind"] != "METHOD":
                continue
            token_line, _ = lines_of(definition["range"])
            twin = next(
                s for s in tree_sitter_symbols
                if incumbent_key(s.name) == scip_key(definition["name"])
                and s.span.start_line <= token_line <= s.span.end_line
            )
            assert lines_of(definition["enclosing_range"]) == (
                twin.span.start_line, twin.span.end_line,
            ), definition["symbol"]
