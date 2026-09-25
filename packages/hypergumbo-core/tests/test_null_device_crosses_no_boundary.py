# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nular: writing to the bit bucket is not a filesystem write.

MEASURED BEFORE THE FIX, shipped CLI, `{boundary: fs_write, must_not_exist:
true}` against a script whose only redirect is `echo "$API_KEY" > /dev/null`::

    $ hypergumbo verify-claims /tmp/nul2 --claims fswrite-claim.yaml
      ✗ [no-host-fs-write] Verdict: violated
        1 fs_write chain(s) found, but claim requires none.
    rc 1

Nothing is written to any filesystem. The kernel discards the bytes, and no
observation anywhere in the system differs because the redirect ran — which is
what makes this VACUOUS rather than merely imprecise, and why INV-nular says a
finding can be true value-flow and worthless as a claim.

WHY THE DISCRIMINATOR CANNOT LIVE IN THE CATALOGUE. The row is `redirect.>`,
one primitive; whether it crosses a filesystem boundary depends on the TARGET
at the call site, which no YAML row can see. That is the same shape `io_mode`
already has — `open(p)` and `open(p, 'w')` are one row and two boundaries — so
the discriminator is stamped by the analyzer and read by ``classify_call``,
where BOTH the boundary tagger and the coverage gate inherit it.

DIRECTION, and it is the one that needs defending because suppression is
normally the wrong answer here. Refusing to classify is safe ONLY because the
claim being refused is false, not merely unproven: `/dev/null` is not a partial
or unknown write, it is a write with no observable effect. Contrast the
unresolved case (`> "$OUT"`), which stays classified precisely because "wrote
somewhere I cannot name" is a real write. And the refusal requires EVERY
collapsed call site to discard (INV-vukiv's ``io_target_kind_values``): one
real target among them and the edge is classified as before, because silencing
a real write on the strength of a different call site is the false-negative
trade this subsystem refuses.
"""

from hypergumbo_core.io_boundary import (
    call_site_target_kinds,
    classify_call,
    load_catalog,
    tag_io_boundaries,
    target_kinds_cross_no_boundary,
)
from hypergumbo_core.ir import Edge, deduplicate_edges


def _redirect(line: int, target_kind: str | None) -> Edge:
    meta = {"io_primitive": "redirect.>", "io_mode": "w"}
    if target_kind is not None:
        meta["io_target_kind"] = target_kind
    return Edge.create(
        src="bash:s.sh:1-9:f:function",
        dst="bash:redirect:0-0:>:unresolved",
        edge_type="calls",
        line=line,
        is_resolved=False,
        origin="bash",
        origin_run_id="run-inv-nular",
        evidence_type="ast_call",
        meta=meta,
    )


def _boundaries(edges: list[Edge]) -> set[str]:
    deduped = deduplicate_edges(list(edges))
    tag_io_boundaries(deduped, {"bash": load_catalog("bash")})
    return {
        b for e in deduped
        if isinstance(b := (e.meta or {}).get("io_boundary"), str)
    }


# ---------------------------------------------------------------------------
# Reading the kinds off an edge
# ---------------------------------------------------------------------------


def test_a_single_site_edge_reports_its_one_kind():
    assert call_site_target_kinds({"io_target_kind": "null_device"}) == (
        "null_device",
    )


def test_a_collapsed_edge_reports_every_site_kind():
    assert call_site_target_kinds(
        {"io_target_kind_values": ["host_path", "null_device"]},
    ) == ("host_path", "null_device")


def test_an_edge_with_no_kind_reports_nothing():
    assert call_site_target_kinds({}) == ()
    assert call_site_target_kinds(None) == ()


def test_a_non_string_kind_is_ignored_not_crashed():
    assert call_site_target_kinds({"io_target_kind_values": ["host_path", 7]}) \
        == ("host_path",)


def test_a_non_list_values_key_falls_back_to_the_singular():
    assert call_site_target_kinds(
        {"io_target_kind_values": "x", "io_target_kind": "null_device"},
    ) == ("null_device",)


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_an_edge_with_no_target_kind_is_classified_as_before():
    """Every non-bash edge in every map ever written takes this path."""
    assert target_kinds_cross_no_boundary(()) is False


def test_a_discarding_target_crosses_nothing():
    assert target_kinds_cross_no_boundary(("null_device",)) is True


def test_a_real_path_crosses_the_boundary():
    assert target_kinds_cross_no_boundary(("host_path",)) is False


def test_an_unresolvable_target_still_counts_as_a_write():
    """'Wrote somewhere I cannot name' is a write, not a discard."""
    assert target_kinds_cross_no_boundary(("unresolved",)) is False


def test_one_real_target_among_discards_is_enough_to_classify():
    """A collapsed edge must not silence a real write for a sibling site."""
    assert target_kinds_cross_no_boundary(
        ("null_device", "host_path"),
    ) is False


# ---------------------------------------------------------------------------
# End to end through the tagger
# ---------------------------------------------------------------------------


def test_the_real_write_is_the_control():
    assert _boundaries([_redirect(2, "host_path")]) == {"fs_write"}


def test_a_write_to_the_null_device_is_not_a_filesystem_write():
    assert _boundaries([_redirect(2, "null_device")]) == set()


def test_a_collapsed_edge_with_one_real_target_still_reports_the_write():
    """The INV-vukiv interaction, and the direction that must not invert."""
    assert _boundaries([
        _redirect(2, "null_device"), _redirect(4, "host_path"),
    ]) == {"fs_write"}


def test_an_edge_predating_the_key_is_classified_exactly_as_before():
    assert _boundaries([_redirect(2, None)]) == {"fs_write"}


# ---------------------------------------------------------------------------
# EXAMINED is not the same question as CROSSES
# ---------------------------------------------------------------------------


def test_a_discarding_call_is_still_EXAMINED_by_the_catalogue():
    """The regression guard for the wrong first cut at this fix.

    Refusing the match in ``classify_call_in_catalog`` — where the coverage
    gate also reads it — answered BOTH questions with "no", and the gate then
    reported "calls into 1 module(s) that the I/O catalog could not classify
    (redirect)". Measured on the shipped CLI, that turned the false ``violated``
    (rc 1) into an ``inconclusive`` (rc 2): a wrong finding traded for a
    withheld verdict, which is the INV-tabaf family, not a fix.

    We know EXACTLY what ``> /dev/null`` is. That is why we can say it crosses
    nothing, and it is the opposite of "never examined".
    """
    edge = _redirect(2, "null_device")
    assert classify_call(
        {"bash": load_catalog("bash")}, edge.dst, edge.meta,
    ) is not None


def test_but_it_is_not_counted_as_an_io_edge():
    deduped = deduplicate_edges([_redirect(2, "null_device")])
    assert tag_io_boundaries(deduped, {"bash": load_catalog("bash")}) == 0


def test_the_real_write_is_counted():
    deduped = deduplicate_edges([_redirect(2, "host_path")])
    assert tag_io_boundaries(deduped, {"bash": load_catalog("bash")}) == 1
