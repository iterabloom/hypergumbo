# SPDX-License-Identifier: AGPL-3.0-or-later
"""``Edge.meta["resolved_by"]``: one declared mark for what scoped a call target's choice (audit 0030).

What is pinned here: the vocabulary is closed, ``Edge.create`` is the one
writer and refuses what the axiom excludes, a shared-resolver lookup maps to
the mark in one place, a bare call to the caller's own type is ``own_type``,
and after finalization a call-family edge carries the mark if and only if it
is resolved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import resolved_by
from hypergumbo_core.analyze.base import (
    defer_bare_method_call,
    resolved_by_for_bare_call,
)
from hypergumbo_core.axis_meta_keys import (
    call_family_edge_types,
    find_meta_key,
    per_call_site_keys,
)
from hypergumbo_core.finalize import FinalizeContext, _finalize_edge_resolution
from hypergumbo_core.ir import Edge, Span, Symbol, deduplicate_edges
from hypergumbo_core.limits import Limits
from hypergumbo_core.pass_metadata import PassMetadataLookup
from hypergumbo_core.symbol_resolution import (
    MATCH_AMBIGUOUS,
    MATCH_CALLER_FILE,
    MATCH_EXACT,
    MATCH_PATH_HINT,
    MATCH_SOFT_HINT_FALLBACK,
    MATCH_SUFFIX,
    MATCH_SUFFIX_AMBIGUOUS,
    LookupResult,
    NameResolver,
)

SRC = "go:a.go:1-2:caller:function"
DST = "go:a.go:5-9:callee:function"
EXTERNAL = "go:external:0-0:WriteFile:unresolved"


def _edge(dst: str = DST, edge_type: str = "calls", line: int = 1, **kwargs: object) -> Edge:
    return Edge.create(
        src=SRC, dst=dst, edge_type=edge_type, line=line,
        origin="test", origin_run_id="r", **kwargs,  # type: ignore[arg-type]
    )


def _symbol(sym_id: str, kind: str) -> Symbol:
    return Symbol(
        id=sym_id, name=sym_id.split(":")[-2], kind=kind,
        path="" if kind == "external_symbol" else "a.go", language="go",
        span=None if kind == "external_symbol" else Span(5, 9, 0, 0),
    )


def _finalize(tmp_path: Path, symbols: list[Symbol], edges: list[Edge]) -> None:
    _finalize_edge_resolution(FinalizeContext(
        symbols=symbols, edges=edges, usage_contexts=[], analysis_runs=[],
        behavior_map={}, limits=Limits(), repo_root=tmp_path,
        pass_metadata=PassMetadataLookup({}),
    ))


# --- the vocabulary ---------------------------------------------------------


def test_every_value_states_its_evidence() -> None:
    for value, statement in resolved_by.RESOLVED_BY_VALUES.items():
        assert value == value.lower() and " " not in value
        assert len(statement) > 20, value


def test_the_constants_are_the_declared_values() -> None:
    constants = {
        getattr(resolved_by, name) for name in dir(resolved_by)
        if name.isupper() and name not in ("RESOLVED_BY_KEY", "RESOLVED_BY_VALUES")
    }
    assert constants == set(resolved_by.RESOLVED_BY_VALUES)


def test_the_vocabulary_holds_no_grade_and_no_candidate_count() -> None:
    """The axiom's exclusions: a value naming quality or ambiguity is another field's fact."""
    for value in set(resolved_by.RESOLVED_BY_VALUES):
        for off_axis in ("ambiguous", "exact", "fallback", "pick", "guess", "unique", "typed_"):
            assert off_axis not in value, value


def test_require_declared() -> None:
    assert resolved_by.require_declared("import") == "import"
    with pytest.raises(ValueError, match="not declared"):
        resolved_by.require_declared("typed_receiver")


def test_the_key_is_registered_per_call_site_on_the_call_family() -> None:
    spec = find_meta_key(resolved_by.RESOLVED_BY_KEY)
    assert spec is not None
    assert spec.applicable_edge_types == call_family_edge_types()
    assert resolved_by.RESOLVED_BY_KEY in per_call_site_keys()


# --- Edge.create is the one writer -------------------------------------------


def test_create_stores_the_mark_in_meta_beside_the_producers_keys() -> None:
    meta = {"call_construct": "method"}
    edge = _edge(resolved_by=resolved_by.RECEIVER_TYPE, meta=meta)
    assert edge.meta == {"call_construct": "method", "resolved_by": "receiver_type"}
    assert meta == {"call_construct": "method"}, "the producer's dict is not mutated"


def test_create_accepts_the_mark_on_a_constructor_edge() -> None:
    edge = _edge(edge_type="instantiates", resolved_by=resolved_by.IMPORT)
    assert edge.meta == {"resolved_by": "import"}


def test_create_without_the_mark_writes_nothing() -> None:
    assert _edge().meta is None


@pytest.mark.parametrize("kwargs", [
    {"resolved_by": "typed_receiver"},
    {"resolved_by": resolved_by.NAME, "is_resolved": False},
    {"resolved_by": resolved_by.NAME, "edge_type": "imports"},
    {"resolved_by": resolved_by.NAME, "edge_type": "references"},
])
def test_create_refuses_what_the_axiom_excludes(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _edge(**kwargs)


# --- a shared-resolver lookup maps to the mark in one place -------------------


@pytest.mark.parametrize(("match_type", "expected"), [
    (MATCH_PATH_HINT, resolved_by.IMPORT),
    (MATCH_CALLER_FILE, resolved_by.SAME_FILE),
    (MATCH_EXACT, resolved_by.NAME),
    (MATCH_SUFFIX, resolved_by.NAME),
    (MATCH_SOFT_HINT_FALLBACK, resolved_by.NAME),
    (MATCH_AMBIGUOUS, resolved_by.NAME),
    (MATCH_SUFFIX_AMBIGUOUS, resolved_by.NAME),
])
def test_each_match_type_has_one_mark(match_type: str, expected: str) -> None:
    assert LookupResult(None, match_type=match_type).resolved_by == expected


def test_a_real_lookup_by_name_is_marked_name() -> None:
    target = _symbol(DST, "function")
    found = NameResolver({"callee": target}).lookup("callee")
    assert found.symbol is target
    assert found.resolved_by == resolved_by.NAME


# --- the bare-call rule -------------------------------------------------------


def test_a_bare_call_to_the_callers_own_type_is_own_type() -> None:
    assert resolved_by_for_bare_call(
        "method", "Store.save", resolved_by.NAME, "Store",
    ) == resolved_by.OWN_TYPE
    assert resolved_by_for_bare_call(
        "method", "Store::save", resolved_by.SAME_FILE, "Store", separator="::",
    ) == resolved_by.OWN_TYPE


@pytest.mark.parametrize(("kind", "name", "enclosing"), [
    ("method", "Other.save", "Store"),   # another type's method
    ("method", "save", "Store"),         # no owner in the name
    ("method", "Store.save", None),      # the caller is not inside a type
    ("function", "Store.save", "Store"), # not a method
])
def test_any_other_bare_call_keeps_the_lookups_own_value(
    kind: str, name: str, enclosing: str | None,
) -> None:
    assert resolved_by_for_bare_call(kind, name, resolved_by.IMPORT, enclosing) == resolved_by.IMPORT


def test_own_type_is_exactly_the_case_the_deferral_binds_on() -> None:
    """One owner test serves both helpers: what is ``own_type`` is never deferred."""
    for name, enclosing in (("Store.save", "Store"), ("Other.save", "Store"), ("save", None)):
        own = resolved_by_for_bare_call("method", name, resolved_by.NAME, enclosing) == resolved_by.OWN_TYPE
        deferred = defer_bare_method_call("method", name, "suffix", enclosing)
        assert own != deferred, (name, enclosing)


# --- after finalization: marked if and only if resolved ------------------------


def test_finalize_keeps_the_mark_on_an_in_repository_target(tmp_path: Path) -> None:
    edge = _edge(resolved_by=resolved_by.SAME_FILE)
    _finalize(tmp_path, [_symbol(DST, "function")], [edge])
    assert edge.is_resolved is True
    assert edge.meta == {"resolved_by": "same_file"}


def test_finalize_removes_the_mark_from_an_edge_that_leaves_the_repository(tmp_path: Path) -> None:
    shared = {"call_construct": "method"}
    edge = _edge(dst=EXTERNAL, resolved_by=resolved_by.KEY_MATCH, meta=shared)
    only_mark = _edge(dst=EXTERNAL, line=2, resolved_by=resolved_by.NAME)
    _finalize(tmp_path, [_symbol(EXTERNAL, "external_symbol")], [edge, only_mark])
    assert edge.is_resolved is False
    assert edge.meta == {"call_construct": "method"}
    assert only_mark.meta is None


def test_finalize_removes_a_collapsed_edges_values_too(tmp_path: Path) -> None:
    first = _edge(dst=EXTERNAL, line=1, resolved_by=resolved_by.IMPORT)
    second = _edge(dst=EXTERNAL, line=2, resolved_by=resolved_by.NAME)
    (kept,) = deduplicate_edges([first, second])
    assert kept.meta is not None and "resolved_by_values" in kept.meta
    _finalize(tmp_path, [], [kept])
    assert "resolved_by" not in (kept.meta or {})
    assert "resolved_by_values" not in (kept.meta or {})


def test_finalize_leaves_an_unmarked_unresolved_edge_alone(tmp_path: Path) -> None:
    meta = {"callee_name": "WriteFile"}
    edge = _edge(dst=EXTERNAL, is_resolved=False, meta=meta)
    _finalize(tmp_path, [], [edge])
    assert edge.meta is meta


# --- two call sites, one edge -------------------------------------------------


def test_sites_that_agree_keep_the_singular_mark() -> None:
    (kept,) = deduplicate_edges([
        _edge(line=1, resolved_by=resolved_by.IMPORT),
        _edge(line=2, resolved_by=resolved_by.IMPORT),
    ])
    assert kept.meta is not None and kept.meta["resolved_by"] == "import"


def test_sites_that_disagree_list_both_and_drop_the_singular() -> None:
    (kept,) = deduplicate_edges([
        _edge(line=1, resolved_by=resolved_by.RECEIVER_TYPE),
        _edge(line=2, resolved_by=resolved_by.NAME),
    ])
    assert kept.meta is not None
    assert "resolved_by" not in kept.meta
    assert kept.meta["resolved_by_values"] == ["name", "receiver_type"]
