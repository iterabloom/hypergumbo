# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py
"""An empty import specifier emits no import edge (WI-sagur).

``import x from ''``, ``require('')``, a bare ``node:`` (whose scheme is
dropped, leaving nothing) and Go's ``import ""`` name no module. The analyzers
built ``javascript::0-0:module:module`` / ``go::0-0:package:package`` for them:
a dst whose path slot is empty, outside the id grammar (the run's
``non_id_edge_endpoints`` counted each one), which finalize turned into an
``external_symbol`` the ``id_format`` validator rejects. Found by a fixture
survey of empty specifiers across twelve languages; the other ten emitted none.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints


def _import_dsts(edges: list) -> list[str]:  # type: ignore[type-arg]
    return sorted(e.dst for e in edges if e.edge_type == "imports")


class TestJavaScript:
    def test_an_empty_specifier_imports_nothing(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "a.js").write_text(
            "import x from '';\n"
            "import n from 'node:';\n"
            "const z = require('');\n"
            "import l from 'lodash';\n"
            "const f = require('node:fs');\n"
            "export function g() { return x + n + z + l + f; }\n"
        )
        result = analyze_javascript(tmp_path)
        # Reach: the two named modules are imported (the control).
        assert _import_dsts(result.edges) == [
            "javascript:fs:0-0:module:module",
            "javascript:lodash:0-0:module:module",
        ]
        assert non_id_edge_endpoints(result.symbols, result.edges, grammar_only=True) == []

    def test_typescript_too(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "j.ts").write_text(
            "import type {} from '';\nimport { a } from './b';\nexport const c = a;\n"
        )
        result = analyze_javascript(tmp_path)
        assert _import_dsts(result.edges) == ["typescript:./b:0-0:module:module"]


class TestGo:
    def test_an_empty_import_path_imports_nothing(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text(
            'package main\n\nimport ""\nimport (\n    ""\n    "fmt"\n)\n\n'
            'func main() { fmt.Println("x") }\n'
        )
        result = analyze_go(tmp_path)
        assert _import_dsts(result.edges) == ["go:fmt:0-0:package:package"]
        assert non_id_edge_endpoints(result.symbols, result.edges, grammar_only=True) == []
