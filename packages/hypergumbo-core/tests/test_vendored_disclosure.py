# SPDX-License-Identifier: AGPL-3.0-or-later
"""Committed vendored code is disclosed (WI-pasan, ADR-0004 point 3a).

Tier-3 code is analysed but left out of the project's summaries; the run
says so on one line, naming the vendored directories, in the same three
places as the installed-dependency disclosure (stderr, the sketch Overview,
``supply_chain_summary.vendored_analysed``).
"""

from __future__ import annotations

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.supply_chain import (
    format_vendored_disclosure,
    vendored_analysed_summary,
    vendored_root,
)


def _file(path: str, lang: str, tier: int = 3, kind: str = "file") -> Symbol:
    return Symbol(
        id=f"{lang}:{path}:1-1:{path}:{kind}", name=path, kind=kind,
        language=lang, path=path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        origin=lang, supply_chain_tier=tier,
    )


def test_vendored_root_is_the_directory_the_pattern_matched() -> None:
    assert vendored_root("pkg/src/x/vendor/tree-sitter-lean/src/parser.c") == "pkg/src/x/vendor"
    assert vendored_root("node_modules/left-pad/index.js") == "node_modules"
    assert vendored_root("third_party/zlib/inflate.c") == "third_party"
    assert vendored_root("src/app.py") is None


def test_summary_groups_tier3_files_by_vendored_root() -> None:
    symbols = [
        _file("a/vendor/g1/parser.c", "c"),
        _file("a/vendor/g1/tree_sitter/parser.h", "c"),
        _file("a/vendor/g1/tree_sitter/parser.h", "cpp"),  # same file, 2 analyzers
        _file("a/vendor/g2/scanner.cc", "cpp"),
        _file("third_party/z/z.c", "c"),
        _file("a/vendor/g1/parser.c", "c", kind="function"),  # not a file node
        _file("src/app.py", "python", tier=1),
        # Tier 3 by installed-dependency tracing, not a committed vendor tree.
        _file(".venv/lib/python3.12/site-packages/x/y.py", "python"),
    ]
    assert vendored_analysed_summary(symbols) == {
        "dirs": 2,
        "entries": [
            {"path": "a/vendor", "languages": ["c", "cpp"], "files": 3},
            {"path": "third_party", "languages": ["c"], "files": 1},
        ],
    }


def test_summary_is_empty_without_vendored_code() -> None:
    assert vendored_analysed_summary([_file("src/app.py", "python", tier=1)]) == {
        "dirs": 0, "entries": [],
    }


def test_entries_are_capped_at_ten() -> None:
    symbols = [_file(f"m{i:02d}/vendor/x.c", "c") for i in range(12)]
    summary = vendored_analysed_summary(symbols)
    assert summary["dirs"] == 12
    assert len(summary["entries"]) == 10


def test_disclosure_line() -> None:
    assert format_vendored_disclosure(None) is None
    assert format_vendored_disclosure({"dirs": 0, "entries": []}) is None
    line = format_vendored_disclosure({
        "dirs": 1,
        "entries": [{"path": "pkg/vendor", "languages": ["c", "cpp"], "files": 7}],
    })
    assert line == (
        "Vendored third-party code analysed as tier 3, not counted in the "
        "language breakdown: pkg/vendor/ (c, cpp; 7 files). "
        "To skip: --exclude <dir>."
    )


def test_disclosure_names_three_directories_then_counts() -> None:
    entries = [
        {"path": f"m{i}/vendor", "languages": ["c"], "files": 1} for i in range(5)
    ]
    line = str(format_vendored_disclosure({"dirs": 5, "entries": entries}))
    assert "m2/vendor/ (c; 1 file), 2 more directories." in line
    assert "m3/vendor" not in line
    line = str(format_vendored_disclosure({"dirs": 4, "entries": entries[:4]}))
    assert "1 more directory." in line


def test_supply_chain_summary_carries_it() -> None:
    from hypergumbo_core.cli import _compute_supply_chain_summary

    summary = _compute_supply_chain_summary(
        [_file("vendor/x/x.c", "c"), _file("src/a.py", "python", tier=1)], [],
    )
    assert summary["vendored_analysed"] == {
        "dirs": 1, "entries": [{"path": "vendor", "languages": ["c"], "files": 1}],
    }
