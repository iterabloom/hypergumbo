# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0035 §3 identity reconciliation: LOGICAL dedup + SITE occurrence-indexing.

The stable_id v6 atomic bump (PR #245) made ids unique-within-scope but left the
``occurrence_index`` slot at 0, so distinct symbols still collided WITHIN a file.
This module pins the two post-linker passes (``analyze/base.py``) that close that
gap — the producer half of INV-tazaj — per ADR-0035 §3's two-axis rule:

* **SITE** kinds (repeated call sites, shell ``export``s, markdown links, manifest
  entries, throwaway vars) → :func:`split_within_file_stable_id_collisions`
  occurrence-indexes within-file ``(path, stable_id)`` collisions so each distinct
  site gets a distinct id (the 1st keeps the original; the 2nd+ get a ``:occ:<n>``
  re-hash). Stays scheme v6 per ADR-0035 §6 (the slot was reserved for this).
* **LOGICAL** kinds (message-queue/event topics, graphql resolver fields) →
  :func:`dedup_logical_synthetic_identities` collapses each duplicate group to one
  hub node and rewires every edge endpoint that pointed at a dropped node to the
  survivor, so a topic's full publisher/subscriber connectivity is preserved.

Both run post-linker (after the enclosure post-pass wires per-site ``uses`` edges)
and before ``finalize`` (R1: set membership final on finalize entry); dedup runs
before split so LOGICAL families present no within-file collision to the split.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from hypergumbo_core.analyze.base import (
    dedup_logical_synthetic_identities,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge, Span, Symbol

_CANONICAL = re.compile(r"^sha256:[0-9a-f]{16}$")


def _mq(line: int, name: str = "kafka:publish:topic", sid: str = "sha256:aaaa000000000000") -> Symbol:
    """A LOGICAL message-queue Class-B stand-in (one per reference site)."""
    return Symbol(
        id=f"javascript:app.js:{line}-{line}:{name}:function",
        name=name,
        kind="function",
        language=None,
        path="app.js",
        span=Span(start_line=line, end_line=line, start_col=0, end_col=10),
        stable_id=sid,
        protocol_origin="message_queue",
    )


def _export(line: int, name: str = "FOO", sid: str = "sha256:cccc000000000000", path: str = "s.sh") -> Symbol:
    """A SITE shell-export symbol."""
    return Symbol(
        id=f"bash:{path}:{line}-{line}:{name}:export",
        name=name,
        kind="export",
        language="bash",
        path=path,
        span=Span(start_line=line, end_line=line, start_col=0, end_col=3),
        stable_id=sid,
    )


# ----------------------------------------------------------------------
# LOGICAL dedup
# ----------------------------------------------------------------------


def test_dedup_collapses_logical_duplicates_to_one_hub() -> None:
    """N references to one topic collapse to one survivor node."""
    s1, s2, s3 = _mq(5), _mq(10), _mq(15)
    symbols = [s2, s1, s3]  # deliberately out of span order
    edges: list[Edge] = []
    dropped = dedup_logical_synthetic_identities(symbols, edges)
    assert dropped == 2
    assert len(symbols) == 1
    # Survivor is the smallest-span occurrence (line 5), independent of input order.
    assert symbols[0].id == s1.id


def test_dedup_none_span_duplicate_sorts_as_position_zero() -> None:
    """WI-hafap: a span=None duplicate sorts as position (0, 0, 0, 0) in
    ``_occurrence_sort_key`` — identical to the degenerate zero Span that
    ``Symbol.from_dict`` used to fabricate — so it wins survivor selection
    over a line-10 sibling instead of crashing inside ``sorted()``."""
    s_none = _mq(5)
    s_none.span = None
    s_real = _mq(10)
    symbols = [s_real, s_none]  # out of span order: survivor choice is span-driven
    dropped = dedup_logical_synthetic_identities(symbols, [])
    assert dropped == 1
    assert symbols[0].id == s_none.id


def test_dedup_preserves_all_publisher_connectivity() -> None:
    """Every distinct publisher's ``uses`` edge is rewired onto the one hub
    (the §3 reason dedup must run AFTER the enclosure pass, not at-mint)."""
    s1, s2, s3 = _mq(5), _mq(10), _mq(15)
    f1 = "javascript:app.js:4-6:foo:function"
    f2 = "javascript:app.js:9-11:bar:function"
    f3 = "javascript:app.js:14-16:baz:function"
    edges = [
        Edge.create(src=f1, dst=s1.id, edge_type="uses", line=5, origin=["enclosure-linker"], origin_run_id="run-1"),
        Edge.create(src=f2, dst=s2.id, edge_type="uses", line=10, origin=["enclosure-linker"], origin_run_id="run-1"),
        Edge.create(src=f3, dst=s3.id, edge_type="uses", line=15, origin=["enclosure-linker"], origin_run_id="run-1"),
    ]
    symbols = [s1, s2, s3]
    dedup_logical_synthetic_identities(symbols, edges)
    survivor_id = symbols[0].id
    # All three publishers now point at the one hub; none dangle.
    assert {e.dst for e in edges} == {survivor_id}
    assert {e.src for e in edges} == {f1, f2, f3}
    # The two REWRITTEN edges had their line-insensitive key invalidated for
    # recompute by the caller's deduplicate_edges; the survivor's own edge
    # (f1 -> s1, whose dst was never remapped) keeps its computed key.
    by_src = {e.src: e for e in edges}
    assert by_src[f1].edge_key is not None
    assert by_src[f2].edge_key is None and by_src[f3].edge_key is None


def test_dedup_rewrites_derived_from() -> None:
    """``derived_from`` provenance entries are remapped onto the survivor too."""
    s1, s2 = _mq(5), _mq(10)
    e = Edge.create(
        src="javascript:app.js:4-6:foo:function",
        dst=s2.id,
        edge_type="uses",
        line=10,
        origin=["enclosure-linker"],
        origin_run_id="run-1",
        derived_from=[s2.id, "other"],
    )
    dedup_logical_synthetic_identities([s1, s2], [e])
    assert e.derived_from == [s1.id, "other"]


def test_dedup_rewrites_edge_src_for_dropped_node() -> None:
    """A pub->sub style edge whose SRC is a dropped duplicate (both endpoints are
    LOGICAL stand-ins) is rewired to the survivor — the src-side of the remap."""
    s1, s2 = _mq(5), _mq(10)  # same topic; s1 (line 5) survives, s2 is dropped
    sub = "javascript:app.js:20-20:kafka:subscribe:topic:function"
    e = Edge.create(
        src=s2.id, dst=sub, edge_type="event_publishes", line=10,
        origin=["message_queue"], origin_run_id="run-1",
    )
    dedup_logical_synthetic_identities([s1, s2], [e])
    assert e.src == s1.id  # dropped s2's edge src rewired onto the survivor
    assert e.edge_key is None


def test_dedup_leaves_site_origin_call_sites_alone() -> None:
    """SITE-axis stand-ins (http/sql call_sites) are NOT in the LOGICAL set, so
    dedup leaves them for the occurrence-indexing pass."""
    def http(line: int) -> Symbol:
        return Symbol(
            id=f"python:a.py:{line}-{line}:GET /x:call_site",
            name="GET /x", kind="call_site", language=None, path="a.py",
            span=Span(start_line=line, end_line=line, start_col=0, end_col=5),
            stable_id="sha256:bbbb000000000000", protocol_origin="http",
        )
    symbols = [http(5), http(9)]
    assert dedup_logical_synthetic_identities(symbols, []) == 0
    assert len(symbols) == 2


def test_dedup_excludes_graphql_to_protect_client_call_sites() -> None:
    """graphql is excluded from the LOGICAL allowlist: its `graphql` protocol_origin
    is shared by resolver fields (LOGICAL) AND graphql.py client call-sites (SITE,
    raw operation_name id). A coarse origin-only dedup would silently collapse
    distinct client call sites, so graphql flows to the SITE occurrence pass instead.
    """
    def gql(line: int) -> Symbol:
        return Symbol(
            id=f"javascript:app.js:{line}-{line}:GetUser:function",
            name="GetUser", kind="function", language=None, path="app.js",
            span=Span(start_line=line, end_line=line, start_col=0, end_col=8),
            stable_id="GetUser", protocol_origin="graphql",
        )
    symbols = [gql(5), gql(10)]
    assert dedup_logical_synthetic_identities(symbols, []) == 0
    assert len(symbols) == 2  # both client call sites preserved (not collapsed)


def test_dedup_noop_when_no_duplicates() -> None:
    """Distinct topics are untouched (different stable_ids)."""
    a = _mq(5, sid="sha256:1111000000000000")
    b = _mq(9, sid="sha256:2222000000000000")
    symbols = [a, b]
    assert dedup_logical_synthetic_identities(symbols, []) == 0
    assert len(symbols) == 2


# ----------------------------------------------------------------------
# SITE occurrence-indexing
# ----------------------------------------------------------------------


def test_split_occurrence_indexes_within_file_collisions() -> None:
    """N within-file duplicates → N distinct ids (1st original, rest re-hashed)."""
    s1, s2, s3 = _export(1), _export(2), _export(3)
    symbols = [s3, s1, s2]  # out of order
    reminted = split_within_file_stable_id_collisions(symbols)
    assert reminted == 2
    ids = [s.stable_id for s in symbols]
    assert len(set(ids)) == 3
    # The smallest-span occurrence keeps the original id; all stay canonical.
    assert s1.stable_id == "sha256:cccc000000000000"
    assert s2.stable_id != s1.stable_id and s3.stable_id != s1.stable_id
    assert all(_CANONICAL.match(i) for i in ids)


def test_split_is_per_file_not_cross_file() -> None:
    """The same id in DIFFERENT files is NOT a within-file collision (that's the
    corpus umbrella's domain); the per-file pass leaves it alone."""
    a = _export(1, path="a.sh", sid="sha256:dddd000000000000")
    b = _export(1, path="b.sh", sid="sha256:dddd000000000000")
    assert split_within_file_stable_id_collisions([a, b]) == 0
    assert a.stable_id == b.stable_id


def test_split_skips_pathless_and_null_stable_id() -> None:
    """Pathless and null-stable_id symbols are out of the in-a-file guarantee."""
    p1 = Symbol(id="x", name="n", kind="function", language="py", path="",
                span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
                stable_id="sha256:ffff000000000000")
    p2 = Symbol(id="y", name="n", kind="function", language="py", path="",
                span=Span(start_line=2, end_line=2, start_col=0, end_col=1),
                stable_id="sha256:ffff000000000000")
    nul = Symbol(id="z", name="n", kind="function", language="py", path="a.py",
                 span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
                 stable_id=None)
    assert split_within_file_stable_id_collisions([p1, p2, nul]) == 0


def test_split_is_order_deterministic() -> None:
    """Same fixture, reversed input order → identical resulting id per symbol
    (ADR-0043 §6 byte-determinism: the sort key is total)."""
    fwd = [_export(1), _export(2), _export(3)]
    split_within_file_stable_id_collisions(fwd)
    fwd_ids = {s.span.start_line: s.stable_id for s in fwd}
    rev = [_export(3), _export(2), _export(1)]
    split_within_file_stable_id_collisions(rev)
    rev_ids = {s.span.start_line: s.stable_id for s in rev}
    assert fwd_ids == rev_ids


# ----------------------------------------------------------------------
# Production-path closure evidence
# ----------------------------------------------------------------------


def test_within_file_site_collision_resolved_end_to_end(tmp_path: Path) -> None:
    """A file with repeated same-name throwaway assignments analyzes to DISTINCT
    stable_ids and ZERO per-file uniqueness errors (the INV-tazaj producer half,
    exercised through the real run_behavior_map pipeline)."""
    (tmp_path / "m.py").write_text("_ = 1\n_ = 2\n_ = 3\n")
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())

    # The per-file uniqueness HARD check (ADR-0035 §5) reports zero errors.
    report = data.get("validation_report", {})
    per_file_errors = [
        v for v in report.get("violations", [])
        if v.get("validator_class") == "cross_field"
        and v.get("field_name") == "Symbol.stable_id"
        and v.get("severity") == "error"
    ]
    assert per_file_errors == [], f"per-file collisions remain: {per_file_errors}"

    # Non-vacuous: every non-null stable_id in the file is unique.
    sids = [n["stable_id"] for n in data["nodes"] if n.get("stable_id")]
    assert len(sids) == len(set(sids)), "duplicate stable_ids survived the pipeline"
