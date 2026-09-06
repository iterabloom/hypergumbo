# SPDX-License-Identifier: AGPL-3.0-or-later
"""``fs.open``'s mode is read from its FLAGS argument (WI-nolut; INV-kaduh's
javascript cell).

WI-nolut rows ``fs.open`` / ``fs.openSync`` / ``fs.promises.open`` under BOTH
``fs_read`` and ``fs_write`` -- python's ``builtins.open`` shape, one row per
direction, settled by ``io_mode``. The parity test in
``test_io_mode_discrimination.py`` refuses a mode-discriminated primitive no
analyzer can produce a mode for (INV-kaduh: python had a producer, C had none,
and every ``fopen(path, "w")`` tagged ``fs_read`` -- an EXAMINED negative for
the boundary that was true). So the javascript analyzer must stamp it.

Node's flags argument is positional 1 (``fs.open(path, flags[, mode], cb)``)
and its spellings -- ``'r'``, ``'r+'``, ``'w'``, ``'wx'``, ``'a'``, ``'a+'``,
``'as'`` -- resolve under the shared ``_WRITE_MODE_CHARS`` rule exactly as
python's do. The stamp goes through the one shared producer
(``stamp_io_mode_from_call``), which learned to read a javascript string
(tree-sitter-javascript spells the literal's text ``string_fragment``, C and
python ``string_content``) rather than getting a copy. An ABSENT flags
argument stamps nothing and resolves to ``fs_read``, which is node's
documented default; a COMPUTED one stamps nothing and resolves to ``fs_read``
too, because ignorance licenses nothing.

TypeScript reaches the same code and the same catalogue and is pinned here for
the reason cpp is pinned in the C file: inheritance is measured, not assumed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_FILENAME = {"javascript": "app.js", "typescript": "app.ts"}


def _open_edge(tmp_path: Path, language: str, source: str, callee: str = "open"):
    """The single ``open``-family call edge, found by CALLEE rather than by tag."""
    (tmp_path / _FILENAME[language]).write_text(source)
    result = analyze_javascript(tmp_path)
    tag_io_boundaries(result.edges, {language: load_catalog(language)})
    edges = [e for e in result.edges if e.edge_type == "calls" and f":{callee}:" in e.dst]
    assert len(edges) == 1, [e.dst for e in edges]
    return edges[0]


WRITER = "const fs = require('fs');\nfunction f(p) { fs.open(p, 'w', (e, fd) => fd); }\n"
APPENDER = "const fs = require('fs');\nfunction f(p) { fs.openSync(p, 'a+'); }\n"
READER = "const fs = require('fs');\nfunction f(p) { fs.open(p, 'r', (e, fd) => fd); }\n"
DEFAULT = "const fs = require('fs');\nfunction f(p) { fs.open(p, (e, fd) => fd); }\n"
COMPUTED = "const fs = require('fs');\nfunction f(p, m) { fs.open(p, m, (e, fd) => fd); }\n"
PROMISES = "const { open } = require('fs/promises');\nasync function f(p) { const h = await open(p, 'wx'); return h; }\n"
NEIGHBOUR = "const fs = require('fs');\nfunction f(p) { fs.open(p, 'w', (e, fd) => fd); fs.readFileSync(p); }\n"


@pytest.mark.parametrize("language", ["javascript", "typescript"])
class TestTheAnalyzerStampsTheMode:
    def test_write_flags_are_recorded(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, WRITER).meta or {}).get("io_mode") == "w"

    def test_append_flags_are_recorded_on_the_sync_form(self, tmp_path: Path, language: str) -> None:
        edge = _open_edge(tmp_path, language, APPENDER, callee="openSync")
        assert (edge.meta or {}).get("io_mode") == "a+"

    def test_read_flags_are_recorded(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, READER).meta or {}).get("io_mode") == "r"

    def test_absent_flags_stamp_nothing(self, tmp_path: Path, language: str) -> None:
        """``fs.open(p, cb)``: the second argument is the callback, not a
        string, so nothing is stamped and node's documented default applies."""
        assert "io_mode" not in (_open_edge(tmp_path, language, DEFAULT).meta or {})

    def test_a_computed_mode_records_absence_rather_than_guessing(self, tmp_path: Path, language: str) -> None:
        assert "io_mode" not in (_open_edge(tmp_path, language, COMPUTED).meta or {})

    def test_a_neighbouring_call_is_not_stamped(self, tmp_path: Path, language: str) -> None:
        edge = _open_edge(tmp_path, language, NEIGHBOUR, callee="readFileSync")
        assert "io_mode" not in (edge.meta or {})


@pytest.mark.parametrize("language", ["javascript", "typescript"])
class TestTheBoundaryFollowsTheMode:
    def test_write_flags_tag_fs_write(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, WRITER).meta or {}).get("io_boundary") == "fs_write"

    def test_append_flags_tag_fs_write(self, tmp_path: Path, language: str) -> None:
        edge = _open_edge(tmp_path, language, APPENDER, callee="openSync")
        assert (edge.meta or {}).get("io_boundary") == "fs_write"

    def test_read_flags_tag_fs_read(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, READER).meta or {}).get("io_boundary") == "fs_read"

    def test_absent_flags_fall_back_to_the_documented_default(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, DEFAULT).meta or {}).get("io_boundary") == "fs_read"

    def test_fs_promises_open_is_settled_the_same_way(self, tmp_path: Path, language: str) -> None:
        assert (_open_edge(tmp_path, language, PROMISES).meta or {}).get("io_boundary") == "fs_write"
