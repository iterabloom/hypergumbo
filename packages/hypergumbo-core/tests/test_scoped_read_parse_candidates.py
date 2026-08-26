# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-hukuf: the coverage gate counts REFERENCES, not PARSE CANDIDATES.

``analyze/base.py``'s ``emit_module_attribute_refs(scoped_path=True)`` walks a
scoped path LEFT-RECURSIVELY and emits one ``module_attr_ref`` edge per nesting
depth, because the module/attribute split is syntactically ambiguous and the
emitter cannot consult the catalogue. ``std::env::consts::OS`` is module
``std::env`` with attribute ``consts::OS`` OR module ``std::env::consts`` with
attribute ``OS``, so it proposes every split and lets the catalogue pick::

    rust:std::env::consts:0-0:OS:external_symbol       -> no match
    rust:std::env:0-0:consts:external_symbol           -> MATCH host_info_read
    rust:std:0-0:env:external_symbol                   -> no match

That is right FOR MATCHING and a category error for COVERAGE ACCOUNTING. The two
unmatched rows are not two modules the analysis called into and could not
classify; they are two alternative PARSES of one reference it DID classify. At
most one proposal per read can ever match, so every genuine N-deep scoped read
was guaranteed to contribute N-1 entries naming modules nothing ever called into.

MEASURED ON THE SHIPPED CLI, a zero-dependency crate whose only scoped read is
``let os = std::env::consts::OS;``::

    before:  Verdict: inconclusive  rc 2
             "calls into 2 module(s) that the I/O catalog could not classify
              (std, std::env::consts)"
    after:   Verdict: confirmed_with_caveats  rc 3

``std::env`` was correctly absent in both — it matched. The other two were its
own collateral.

THE PREFIX TEST IS WHAT KEEPS THIS FROM OVER-SUPPRESSING, and it is why the rule
is not simply keyed on ``(src, line)``. Two DISTINCT scoped reads can share a
line — ``f(std::env::consts::OS, std::fs::File::open)`` — and a line-keyed rule
would let one matching vouch for the other, silently removing a real
uncatalogued module. The proposals of ONE read are not merely co-located, they
are NESTED.
"""

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import (
    _same_reference,
    _uncatalogued_external_modules,
)

_SRC = "rust:src/main.rs:1-4:main:function"


def _read(dst: str, line: int = 2) -> dict:
    return {"src": _SRC, "dst": dst, "type": "module_attr_ref", "line": line}


#: The three proposals the emitter makes for ``std::env::consts::OS``.
_ENV_CONSTS = [
    _read("rust:std::env::consts:0-0:OS:external_symbol"),
    _read("rust:std::env:0-0:consts:external_symbol"),
    _read("rust:std:0-0:env:external_symbol"),
]


def _unknown(edges: list[dict]) -> list[str]:
    return _uncatalogued_external_modules(edges, {"rust": load_catalog("rust")})


# ---------------------------------------------------------------------------
# The prefix predicate
# ---------------------------------------------------------------------------


def test_a_shallower_split_is_the_same_reference():
    assert _same_reference("std::env", "std::env::consts") is True


def test_an_identical_module_is_the_same_reference():
    assert _same_reference("std::env", "std::env") is True


def test_a_sibling_module_is_a_different_reference():
    """``std::fs`` and ``std::env`` share ``std`` and neither contains the other."""
    assert _same_reference("std::fs", "std::env") is False


def test_a_component_prefix_is_not_a_string_prefix():
    """``std::envy`` starts with ``std::env`` as TEXT and is a different module."""
    assert _same_reference("std::envy", "std::env::consts") is False


def test_separators_are_folded_before_comparing():
    """The id slot spells ``::`` in rust and ``.`` in python; the catalogue varies."""
    assert _same_reference("std.env", "std::env::consts") is True


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_the_collateral_proposals_are_no_longer_reported():
    assert _unknown(_ENV_CONSTS) == []


def test_a_read_that_matches_NOTHING_is_still_reported_in_full():
    """THE NON-VACUITY FLOOR: the rule must not silence a genuinely unknown read."""
    unknown = _unknown([
        _read("rust:acme::deep::inner:0-0:CONST:external_symbol"),
        _read("rust:acme::deep:0-0:inner:external_symbol"),
        _read("rust:acme:0-0:deep:external_symbol"),
    ])
    assert unknown == ["acme", "acme::deep", "acme::deep::inner"]


def test_a_distinct_read_on_the_same_line_is_not_vouched_for():
    """The over-suppression this predicate exists to prevent."""
    unknown = _unknown(_ENV_CONSTS + [
        _read("rust:acme::widget:0-0:Thing:external_symbol"),
        _read("rust:acme:0-0:widget:external_symbol"),
    ])
    assert unknown == ["acme", "acme::widget"]


def test_a_read_on_a_different_line_is_never_vouched_for():
    unknown = _unknown(_ENV_CONSTS + [
        _read("rust:std::env::consts:0-0:ARCH:external_symbol", line=9),
    ])
    assert unknown == ["std::env::consts"]


def test_a_call_edge_is_untouched_by_this_rule():
    """Scoped-path multi-proposal emission is a ``module_attr_ref`` shape only."""
    call = {"src": _SRC, "dst": "rust:acme::widget:0-0:make:external_symbol",
            "type": "calls", "line": 2, "is_resolved": False}
    assert _unknown(_ENV_CONSTS + [call]) == ["acme::widget"]


def test_an_edge_with_no_line_is_not_grouped():
    """``meta`` is deserialized; a missing line must not group unrelated reads."""
    lineless = {"src": _SRC, "dst": "rust:acme:0-0:widget:external_symbol",
                "type": "module_attr_ref"}
    assert "acme" in _unknown(_ENV_CONSTS + [lineless])
