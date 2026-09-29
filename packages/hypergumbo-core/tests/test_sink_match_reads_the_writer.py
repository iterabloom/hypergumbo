# SPDX-License-Identifier: AGPL-3.0-or-later
"""Both propagators match a gated SINK by the call site's stamp (INV-totar).

WI-suhug gave a writer's sink rows ``requires_target_kind`` and taught
``_match_propagation_entry`` to resolve a stamp in the WRITE direction, and
io-boundaries selects the row by the stamp. The propagators never passed the
stamp for sinks, only for sources, so a stamped writer always matched its
abstention fallback: one call site, two answers.

MEASURED ON THE SHIPPED CLI before this change, a Go repo whose one statement is
``io.WriteString(os.Stderr, os.Getenv("API_KEY"))``:

* ``io-boundaries``: ``logging`` (the site is stamped ``std_stream``);
* ``verify-claims`` ``host_secret -> logging``: ``confirmed``, 0 flows, a false
  all-clear; ``host_secret -> host_fs``: ``violated``, 1 flow.

The matcher's own tests (``test_target_kind_fallback_entries.py``) pass the
stamp in by hand, so they stayed green through the whole defect. These drive
the PROPAGATORS, with the edges copied from a real survey of that file and the
entries derived from the shipped go catalogue.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from hypergumbo_core import taint as T
from hypergumbo_core.cfg import DdgEdge

_CAT_DIR = Path(T.__file__).parent / "io_primitives"
_CALLER = "go:f.go:8-11:F:function"


@pytest.fixture(scope="module")
def go_catalogue():
    sources, sinks, ambiguous = T._derive_auto_imports_from_io_primitives(_CAT_DIR)
    return sources["go"], sinks["go"], ambiguous.get("go", frozenset())


def _edges(writer: str, target_kind: Optional[str]) -> list[dict]:
    """``s := os.Getenv("API_KEY")`` then ``<writer>(<stream>, s)``.

    Shaped as ``hypergumbo survey`` emits them. Two lines, not the repro's one
    nested call, so the DDG arm has a def-use edge to walk.
    """
    module, name = writer.split(".")
    write_meta: dict = {
        "call_construct": "function", "evidence_lang": "go",
        "evidence_type": "ast_call",
    }
    if target_kind is not None:
        write_meta["io_target_kind"] = target_kind
    return [
        {"src": _CALLER, "dst": f"go:{module}:0-0:{name}:external_symbol",
         "type": "calls", "is_resolved": False, "line": 10, "meta": write_meta},
        {"src": _CALLER, "dst": "go:os:0-0:Getenv:external_symbol",
         "type": "calls", "is_resolved": False, "line": 9,
         "meta": {"call_construct": "function", "evidence_lang": "go",
                  "evidence_type": "ast_call"}},
    ]


def _zones(arm: str, go_catalogue, writer: str, target_kind: Optional[str]) -> set[str]:
    sources, sinks, ambiguous = go_catalogue
    edges = _edges(writer, target_kind)
    if arm == "structural":
        found = T.propagate_taint_structural(
            edges, sources, sinks, [], ambiguous_names=ambiguous, language="go",
        )
    else:
        # A real def-use edge: with none, the DDG arm returns ``[]`` before it
        # reaches a sink, and every assertion below would be vacuous.
        found = T.propagate_taint_ddg(
            [DdgEdge(variable="s", def_block="b0", def_line=9,
                     use_block="b0", use_line=10, symbol_id=_CALLER)],
            edges, sources, sinks, [], ddg_symbols={_CALLER},
            ambiguous_names=ambiguous, language="go",
        )
    return {f.sink_zone for f in found if f.taint_label == "host_secret"}


_ARMS = ["structural", "ddg"]


@pytest.mark.parametrize("arm", _ARMS)
@pytest.mark.parametrize("writer", ["io.WriteString", "fmt.Fprintln"])
@pytest.mark.parametrize("kind,zone", [
    ("std_stream", "logging"),
    ("host_path", "host_fs"),
    ("pipe", "ipc"),
    ("net_stream", "network"),
])
def test_the_stamp_decides_the_zone(go_catalogue, arm, writer, kind, zone):
    """The item's repro is ``io.WriteString`` + ``std_stream``: host_fs before."""
    assert _zones(arm, go_catalogue, writer, kind) == {zone}


@pytest.mark.parametrize("arm", _ARMS)
@pytest.mark.parametrize("writer,fallback", [
    ("io.WriteString", "host_fs"),
    ("fmt.Fprintln", "logging"),
])
def test_an_unstamped_writer_keeps_its_fallback(go_catalogue, arm, writer, fallback):
    """THE CONTROL: no stamp is an abstention, and it must not lose the sink."""
    assert _zones(arm, go_catalogue, writer, None) == {fallback}


@pytest.mark.parametrize("arm", _ARMS)
def test_an_in_memory_writer_is_no_sink(go_catalogue, arm):
    """``&buf`` crosses nothing: refused by the carry gate, not by the match."""
    assert _zones(arm, go_catalogue, "io.WriteString", "in_memory") == set()
