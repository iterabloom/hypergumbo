# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
"""A symbol's docstring is a line that documents it, through real analyzers.

The rule is tested per comment syntax in hypergumbo-core
(``test_doc_comment_summary.py``). Here four analyzers run on source in which
the comment above a declaration opens with decoration, a bare block opener
or the shebang: the shapes a sketch printed as ``------`` and ``!/bin/bash``.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.bash import analyze_bash
from hypergumbo_lang_mainstream.c import analyze_c
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

BASH = """#!/bin/bash
first_fn() {
  echo first
}

# ------------------------------------------------------------------
# json_field DOTPATH
#   Safe field extraction from JSON on stdin.
# ------------------------------------------------------------------
json_field() {
  echo "$1"
}

# ==================
plain_rule() {
  echo rule
}
"""

GO = """package demo

/*
decoder reads frames off the wire.
*/
type decoder struct{}

// ----------------------------------------------------------------------------
// parser turns frames into messages.
// ----------------------------------------------------------------------------
type parser struct{}
"""

JS = """/*
 * Starts the daemon.
 */
function start() { return 1; }

/**********************************
 * Stops the daemon.
 **********************************/
function stop() { return 2; }
"""

C = """/*
 * ---------------------------------
 * Adds two numbers.
 * ---------------------------------
 */
int add(int a, int b) { return a + b; }
"""


def _docstrings(result: object) -> dict[str, str | None]:
    return {s.name: s.docstring for s in result.symbols}  # type: ignore[attr-defined]


def test_bash(tmp_path: Path) -> None:
    (tmp_path / "tool.sh").write_text(BASH)
    docs = _docstrings(analyze_bash(tmp_path))
    assert docs["json_field"] == "Safe field extraction from JSON on stdin."
    # The shebang is all there is above the first function, and a rule all
    # there is above the last: neither is a summary.
    assert docs["first_fn"] is None
    assert docs["plain_rule"] is None


def test_go(tmp_path: Path) -> None:
    (tmp_path / "demo.go").write_text(GO)
    docs = _docstrings(analyze_go(tmp_path))
    assert docs["decoder"] == "decoder reads frames off the wire."
    assert docs["parser"] == "parser turns frames into messages."


def test_javascript(tmp_path: Path) -> None:
    (tmp_path / "daemon.js").write_text(JS)
    docs = _docstrings(analyze_javascript(tmp_path))
    assert docs["start"] == "Starts the daemon."
    assert docs["stop"] == "Stops the daemon."


def test_c(tmp_path: Path) -> None:
    (tmp_path / "add.c").write_text(C)
    docs = _docstrings(analyze_c(tmp_path))
    assert docs["add"] == "Adds two numbers."
