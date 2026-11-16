# SPDX-License-Identifier: AGPL-3.0-or-later
"""Value labels are labels only: no digest moves when a value is relabelled.

ADR-0035 §7 gave every identity value its scheme's label (``hgstableid15:``,
``hgscipid1:``, ``hgshapeid4:``, ``hgrepofp3:``) in place of ``sha256:``,
keeping each digest. A stable id is also hash INPUT: a containing scope's id,
a file anchor, the id an occurrence or route re-mint extends. Folded with its
new label, every nested id's digest would move, so a folded id enters the
input as ``sha256:<hex>``, its text through scheme v14
(``stable_id_hash_input``). These tests pin that, at each formula that folds
an id, by recomputing the v14 formula by hand; and they pin that the
occurrence re-mint of a SCIP-arm id keeps the SCIP label.
"""
from __future__ import annotations

import hashlib

from hypergumbo_core.analyze.base import (
    _short_sha256,
    assemble_stable_id,
    make_file_stable_id,
    make_typed_stable_id,
    mint_scip_stable_id,
    split_within_file_stable_id_collisions,
    stable_id_hash_input,
    widen_route_stable_ids,
)
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.schema import SCIP_STABLE_ID_PREFIX, STABLE_ID_PREFIX


def _hex(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _v14(payload: str) -> str:
    """The digest a v14 formula produced, written with the v15 label."""
    return STABLE_ID_PREFIX + _hex(payload)


def _sym(stable_id: str, line: int, *, name: str = "f", kind: str = "function",
         meta: dict | None = None) -> Symbol:
    return Symbol(
        id=f"rust:src/lib.rs:{line}-{line}:{name}:{kind}", name=name, kind=kind,
        language="rust", path="src/lib.rs", span=Span(line, line, 0, 1),
        stable_id=stable_id, meta=meta,
    )


def test_a_folded_stable_id_enters_the_hash_as_its_v14_text() -> None:
    anchor = make_file_stable_id("python", "a.py")
    old_anchor = "sha256:" + anchor[len(STABLE_ID_PREFIX):]
    assert stable_id_hash_input(anchor) == old_anchor
    scip = mint_scip_stable_id("scip:a.rs:m")
    assert stable_id_hash_input(scip) == "sha256:" + scip[len(SCIP_STABLE_ID_PREFIX):]
    # A value that is not a labelled stable id passes through unchanged.
    assert stable_id_hash_input("") == ""
    assert stable_id_hash_input("not:an:id") == "not:an:id"


def test_assemble_stable_id_digest_is_the_v14_digest() -> None:
    anchor = make_file_stable_id("python", "a.py")
    old_anchor = "sha256:" + anchor[len(STABLE_ID_PREFIX):]
    got = assemble_stable_id("function", 1, "", "", anchor, "f", "f")
    assert got == _v14(f"function:1:::{old_anchor}:f:f:0")


def test_typed_stable_id_digest_is_the_v14_digest_for_both_containing_slots() -> None:
    anchor = make_file_stable_id("java", "A.java")
    old_anchor = "sha256:" + anchor[len(STABLE_ID_PREFIX):]
    expected = _v14(f"method:(int)void:public::{old_anchor}:m:A.m")
    assert make_typed_stable_id(
        "method", "(int)void", "public", anchor, name="m", qualified_name="A.m",
    ) == expected
    assert make_typed_stable_id(
        "method", "(int)void", "public", name="m", qualified_name="A.m",
        file_stable_id=anchor,
    ) == expected


def test_occurrence_re_mint_hashes_the_v14_text_and_keeps_the_label() -> None:
    ts_id = _short_sha256("x")
    scip_id = mint_scip_stable_id("scip:src/lib.rs:m")
    ts = [_sym(ts_id, 1), _sym(ts_id, 2)]
    scip = [_sym(scip_id, 3, name="g"), _sym(scip_id, 4, name="g")]
    assert split_within_file_stable_id_collisions(ts + scip) == 2
    old_ts = "sha256:" + ts_id[len(STABLE_ID_PREFIX):]
    old_scip = "sha256:" + scip_id[len(SCIP_STABLE_ID_PREFIX):]
    assert ts[0].stable_id == ts_id
    assert ts[1].stable_id == STABLE_ID_PREFIX + _hex(f"{old_ts}:occ:1")
    assert scip[0].stable_id == scip_id
    assert scip[1].stable_id == SCIP_STABLE_ID_PREFIX + _hex(f"{old_scip}:occ:1")


def test_route_widening_hashes_the_v14_text() -> None:
    route_id = _short_sha256("route:GET:/")
    route = Symbol(
        id="javascript:app.js:3-3:GET /:route", name="GET /", kind="route",
        language=None, discovery_language="javascript", path="app.js",
        span=Span(3, 3, 0, 1), stable_id=route_id,
        meta={"framework_role": "route"},
    )
    assert widen_route_stable_ids([route]) == 1
    old_route = "sha256:" + route_id[len(STABLE_ID_PREFIX):]
    assert route.stable_id == _v14(f"route_site:javascript:app.js:{old_route}")
