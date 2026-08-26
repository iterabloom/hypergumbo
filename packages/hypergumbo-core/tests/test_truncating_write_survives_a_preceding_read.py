# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vukiv end-to-end: a real write must not be deleted by a read above it.

The two fixtures below differ by ONE statement. Measured on the shipped CLI
before this fix::

    def truncate_logs(p):          def truncate_logs(p):
        fh = open(p, 'r')              open(p, 'w').close()
        fh.close()
        open(p, 'w').close()

    io-boundaries: fs_read         io-boundaries: fs_write

Same truncating write in both. Adding a READ above it deleted it from the
boundary map outright, because ``deduplicate_edges`` collapsed the two
``builtins.open`` call sites into one edge carrying the FIRST site's
``io_mode='r'``, and the mode gate then eliminated the ``fs_write`` row. A
``must_not_exist: fs_write`` claim confirms on the left-hand repo.

A CONTROL IN THE SAME RUN is the point of the pairing: "fs_write is present"
proves nothing on its own if the fixture would report it either way, and
"fs_read is present" is CORRECT here — the read really happens. What the
invariant demands is that the write is not TRADED for it.

Kept out of the analyzer package deliberately: the defect is in
``ir.deduplicate_edges`` and ``io_boundary``'s mode gate, both of which live
here, and the python analyzer is only the most convenient way to produce two
same-name call sites with different modes.
"""

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge, deduplicate_edges


def _open_edge(line: int, mode: str) -> Edge:
    return Edge.create(
        src="python:m.py:1-4:truncate_logs:function",
        dst="python:builtins:0-0:open:unresolved",
        edge_type="calls",
        line=line,
        is_resolved=False,
        origin="python",
        origin_run_id="run-truncate-logs",
        evidence_type="ast_call",
        meta={"io_mode": mode},
    )


def _boundaries(edges: list[Edge]) -> set[str]:
    """Every boundary the shipped tagger stamps on the SURVIVING edges."""
    deduped = deduplicate_edges(list(edges))
    tag_io_boundaries(deduped, {"python": load_catalog("python")})
    return {
        b for e in deduped
        if isinstance(b := (e.meta or {}).get("io_boundary"), str)
    }


def _all_boundaries(edges: list[Edge]) -> set[str]:
    """Every boundary the tagger stamps, plural key included."""
    deduped = deduplicate_edges(list(edges))
    tag_io_boundaries(deduped, {"python": load_catalog("python")})
    found: set[str] = set()
    for e in deduped:
        meta = e.meta or {}
        found |= set(meta.get("io_boundaries") or [])
        if isinstance(b := meta.get("io_boundary"), str):
            found.add(b)
    return found


def test_the_lone_truncating_write_is_the_control():
    assert _boundaries([_open_edge(2, "w")]) == {"fs_write"}


def test_a_preceding_read_does_not_delete_the_write():
    """The measured regression: 'r' first must not trade away the 'w'."""
    assert "fs_write" in _boundaries([_open_edge(2, "r"), _open_edge(4, "w")])


def test_encounter_order_does_not_decide_it_either():
    assert "fs_write" in _boundaries([_open_edge(2, "w"), _open_edge(4, "r")])


def test_a_read_only_function_still_reports_only_a_read():
    """The gate must not become 'everything is a write'."""
    assert _boundaries([_open_edge(2, "r"), _open_edge(4, "r")]) == {"fs_read"}


def test_the_read_is_not_traded_away_for_the_write():
    """Both crossings really happen, so both are reported (io_boundaries)."""
    assert _all_boundaries([_open_edge(2, "r"), _open_edge(4, "w")]) == {
        "fs_read", "fs_write",
    }


def test_a_uniform_function_gains_no_plural_key():
    """Two writing sites span nothing; the ~99% case must not grow a key."""
    deduped = deduplicate_edges([_open_edge(2, "w"), _open_edge(4, "w")])
    tag_io_boundaries(deduped, {"python": load_catalog("python")})
    assert all("io_boundaries" not in (e.meta or {}) for e in deduped)
