# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-higap: Edge.origin and Edge.origin_run_id are non-empty by construction.

The hard-raise enforcement in ``Edge.__post_init__`` is the primary mechanism
keeping these fields populated — every Edge construction goes through
``__post_init__``, including ``Edge.from_dict()``. The unit tests below
verify that contract directly.

Three invariants:

1. ``Edge.__post_init__`` rejects empty origin or origin_run_id.
2. ``Edge.from_dict()`` swaps in ``LEGACY_DESERIALIZED_SENTINEL`` when
   loading legacy on-disk JSON whose origin / origin_run_id is empty,
   so cache reads still succeed under the new enforcement.
3. ``LEGACY_DESERIALIZED_SENTINEL`` may only appear in the IR module
   that defines it (and the consumer-side ``cli.py`` rebuild path).
   A producer that stamps the sentinel directly would let it slip
   into fresh behavior maps. This is enforced statically by a
   source-tree scan rather than by running an end-to-end
   self-analysis (which would cost ~90 s on every CI run for the
   same signal).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.ir import LEGACY_DESERIALIZED_SENTINEL


class TestEdgePostInitEnforcement:
    """Direct unit tests for the Edge.__post_init__ hard-raise."""

    def test_empty_origin_raises(self) -> None:
        from hypergumbo_core.ir import Edge

        with pytest.raises(ValueError, match=r"Edge\.origin must be non-empty"):
            Edge(
                id="e1", src="s", dst="d", edge_type="calls", line=1,
                origin="", origin_run_id="run-1",
            )

    def test_empty_origin_run_id_raises(self) -> None:
        from hypergumbo_core.ir import Edge

        with pytest.raises(ValueError, match=r"Edge\.origin_run_id must be non-empty"):
            Edge(
                id="e1", src="s", dst="d", edge_type="calls", line=1,
                origin="test-pass", origin_run_id="",
            )

    def test_both_set_passes(self) -> None:
        from hypergumbo_core.ir import Edge

        e = Edge(
            id="e1", src="s", dst="d", edge_type="calls", line=1,
            origin="test-pass", origin_run_id="run-1",
        )
        assert e.origin == "test-pass"
        assert e.origin_run_id == "run-1"

    def test_from_dict_injects_sentinel_for_empty(self) -> None:
        """Edge.from_dict survives empty origin / origin_run_id in legacy JSON."""
        from hypergumbo_core.ir import Edge

        legacy = {
            "id": "e1",
            "src": "s",
            "dst": "d",
            "type": "calls",
            "line": 1,
            "origin": "",
            "origin_run_id": "",
        }
        e = Edge.from_dict(legacy)
        assert e.origin == LEGACY_DESERIALIZED_SENTINEL
        assert e.origin_run_id == LEGACY_DESERIALIZED_SENTINEL

    def test_from_dict_preserves_populated_fields(self) -> None:
        from hypergumbo_core.ir import Edge

        d = {
            "id": "e1",
            "src": "s",
            "dst": "d",
            "type": "calls",
            "line": 1,
            "origin": "real-pass-v1",
            "origin_run_id": "run-42",
        }
        e = Edge.from_dict(d)
        assert e.origin == "real-pass-v1"
        assert e.origin_run_id == "run-42"


class TestSentinelOnlyInIrModule:
    """LEGACY_DESERIALIZED_SENTINEL must not leak into producer code paths.

    A static source-tree scan replaces the end-to-end self-analysis: it
    runs in milliseconds, never depends on the ``hypergumbo`` console
    script being installed, and pinpoints the violating file directly.
    """

    # Files where the literal string is allowed to appear. The sentinel
    # is defined in ir.py and may legitimately appear in additional
    # consumer-side deserialization paths that load older behavior maps
    # (e.g. cli.py's ``cmd_dead_code_maybe`` rebuilds Edges from a saved
    # behavior map and uses the same sentinel pattern as Edge.from_dict).
    _ALLOWLIST_BASENAMES: ClassVar[set[str]] = {
        "ir.py",
        "cli.py",  # consumer-side dead-code maybe path
        "test_edge_provenance_invariant.py",
    }

    def test_no_producer_module_references_sentinel(self) -> None:
        import hypergumbo_core

        pkg_root = Path(hypergumbo_core.__file__).parent
        offending: list[Path] = []
        for py_file in pkg_root.rglob("*.py"):
            if py_file.name in self._ALLOWLIST_BASENAMES:
                continue
            text = py_file.read_text()
            if "LEGACY_DESERIALIZED_SENTINEL" in text:
                offending.append(py_file)

        assert offending == [], (
            "WI-higap regression: LEGACY_DESERIALIZED_SENTINEL must only "
            "be referenced from ir.py (where it's defined). Producers that "
            "stamp it directly would leak it into fresh behavior maps. "
            f"Found in: {offending}"
        )
