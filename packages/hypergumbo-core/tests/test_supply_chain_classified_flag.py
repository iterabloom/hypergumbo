# SPDX-License-Identifier: AGPL-3.0-or-later
""""Unclassified" is an explicit flag, not tier 1 with an empty reason (WI-lidig).

The supply-chain annotator used to classify every node whose tier was the
default 1 AND whose reason was empty. That made the emptiness of
``supply_chain_reason`` -- declared free text that no consumer branches on --
the branch, and a producer that deliberately stamped tier 1 without a reason
could not be told from one that stamped nothing (the F78.A3 sentinel watch-item,
unexercised on the substrate but undecidable by construction).
``Symbol.supply_chain_classified`` now answers the question directly.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.cli import _classify_symbols
from hypergumbo_core.ir import Span, Symbol


def _sym(**kw) -> Symbol:
    kw.setdefault("id", "python:src/app.py:1-2:f:function")
    kw.setdefault("name", "f")
    kw.setdefault("kind", "function")
    kw.setdefault("language", "python")
    kw.setdefault("path", "src/app.py")
    kw.setdefault("span", Span(1, 2, 0, 0))
    return Symbol(**kw)


def test_a_default_symbol_is_unclassified() -> None:
    assert _sym().supply_chain_classified is False


def test_a_constructor_given_a_tier_or_reason_has_classified_it() -> None:
    assert _sym(supply_chain_tier=3).supply_chain_classified is True
    assert _sym(supply_chain_reason="crypto producer").supply_chain_classified is True


def test_a_deserialized_symbol_is_classified() -> None:
    restored = Symbol.from_dict(_sym().to_dict())
    assert restored.supply_chain_classified is True


def test_the_flag_is_not_serialized() -> None:
    d = _sym().to_dict()
    assert "supply_chain_classified" not in d
    assert "supply_chain_classified" not in d["supply_chain"]


def test_the_annotator_classifies_only_unclassified_nodes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def f(): pass\n")
    fresh = _sym()
    deliberate = _sym(id="python:src/app.py:3-4:g:function", name="g")
    # A deliberate tier-1 stamp with no reason: the old guard re-classified it.
    deliberate.supply_chain_classified = True
    stand_in = _sym(id="x:syn:0-0:s:function", name="s", protocol_origin="crypto_flow")

    _classify_symbols([fresh, deliberate, stand_in], tmp_path, set())

    assert fresh.supply_chain_classified is True
    assert fresh.supply_chain_reason  # the path classifier gave a reason
    assert deliberate.supply_chain_reason == ""  # left as its producer set it
    assert stand_in.supply_chain_classified is False  # synthetic: skipped
    assert stand_in.supply_chain_reason == ""
