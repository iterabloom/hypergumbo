# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py
"""A bash edge endpoint is a node id even when a name holds a colon.

The program-launch edge spelled its dst by hand,
``bash:{cmd}:0-0:{cmd}:unresolved``. A colon in the NAME slot (an Erlang
call ``file:close`` written as a command in a script) shifts the
right-anchored slots: the endpoint is no id (ADR-0036 Ruling 1), and
``AnalysisRun.non_id_edge_endpoints`` counted it (ejabberd: 10). It is now
minted by ``make_symbol_id``, which folds the name slot's ``:`` to ``.``; the
path slot keeps its colon, as the grammar allows, and ``dst_ref`` keeps the
name as written.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints

SCRIPT = """#!/bin/bash
helper() {
  ls -l
  file:close x
}
source ./lib/a:b.sh
. ./lib/plain.sh
"""


def _analyze(tmp_path: Path):  # type: ignore[no-untyped-def]
    from hypergumbo_lang_mainstream.bash import analyze_bash

    (tmp_path / "run.sh").write_text(SCRIPT)
    return analyze_bash(tmp_path)


def test_a_command_with_a_colon_is_a_node_id(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    calls = sorted(e.dst for e in result.edges if e.edge_type == "calls")
    # Reach and control: both launches are edges; ``ls`` is spelled as before.
    assert calls == [
        "bash:file:close:0-0:file.close:unresolved",
        "bash:ls:0-0:ls:unresolved",
    ]
    launch = next(e for e in result.edges if e.dst.startswith("bash:file:close"))
    assert launch.dst_ref is not None and launch.dst_ref.name == "file:close"


def test_no_endpoint_is_outside_the_grammar(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert non_id_edge_endpoints(result.symbols, calls, grammar_only=True) == []
