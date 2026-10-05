# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift raw identifier containing spaces has one canonical, underscored name
whatever grammar release parses it (INV-bisok, the Swift-Testing half).

INV-bisok found that tree-sitter-swift 0.7.3 could not parse a backtick RAW
IDENTIFIER containing spaces (Swift 6.1's spelling for a test name) nor ``try``
inside an ``if`` / ``guard`` / ``while`` condition, and retried such files through a
byte-length-preserving rewrite. 0.7.4 parses both natively, so the retry is gone,
but the NAME the rewrite produced (each space inside the backticks an underscore)
is kept: without it a symbol's name and id would change with the installed grammar.
The canonicalization touches identifier leaves only, never string contents.
"""
from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_swift

from hypergumbo_lang_mainstream.swift import (
    _swift_canonical_raw_identifiers,
    analyze_swift,
)

SWIFT_TESTING = (
    "@testable import App\n"
    "import Testing\n"
    "\n"
    "extension ControllersTests {\n"
    "    @Suite(\"Trending (GET /trending)\", .serialized, .tags(.trending))\n"
    "    struct TrendingActionTests {\n"
    "        var application: Application!\n"
    "\n"
    "        @Test\n"
    "        func `hashtags are returned for an anonymous user`() async throws {\n"
    "            let fm = FileManager.default\n"
    "            fm.removeItem(atPath: \"/tmp/x\")\n"
    "        }\n"
    "    }\n"
    "}\n"
)


def _parse(src: bytes) -> tree_sitter.Tree:
    return tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_swift.language())
    ).parse(src)


def _symbols(root: Path, files: dict[str, str]):
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_swift(root)


class TestTheCanonicalSpelling:
    def test_it_preserves_byte_length(self) -> None:
        """Every span the analyzer reports must still point at the real file."""
        raw = SWIFT_TESTING.encode()
        out = _swift_canonical_raw_identifiers(_parse(raw), raw)
        assert out is not None and len(out) == len(raw)

    def test_a_raw_identifier_keeps_its_backticks_and_loses_its_spaces(self) -> None:
        src = b"func `a b c`() {}\n"
        assert _swift_canonical_raw_identifiers(_parse(src), src) == (
            b"func `a_b_c`() {}\n"
        )

    def test_a_string_containing_backticked_words_is_untouched(self) -> None:
        """Only identifier leaves are rewritten; a string's text is another node."""
        src = b'func `a b`() { let s = "use `x y` here" }\n'
        assert _swift_canonical_raw_identifiers(_parse(src), src) == (
            b'func `a_b`() { let s = "use `x y` here" }\n'
        )

    def test_nothing_to_rewrite_returns_none(self) -> None:
        """A space-free raw identifier (``default``) is already canonical."""
        src = b"func go() {\n    let s = `default`\n}\n"
        assert _swift_canonical_raw_identifiers(_parse(src), src) is None


class TestThroughTheAnalyzer:
    def test_a_swift_testing_file_yields_its_struct_and_method(
        self, tmp_path: Path,
    ) -> None:
        res = _symbols(tmp_path / "st", {"T.swift": SWIFT_TESTING})
        kinds = {s.name: s.kind for s in res.symbols}
        assert "TrendingActionTests" in kinds, sorted(kinds)
        assert kinds["TrendingActionTests"] == "struct"
        methods = [n for n, k in kinds.items() if k == "method"]
        assert any("hashtags_are_returned" in m for m in methods), methods
        assert not any(" " in m for m in methods), methods

    def test_the_receiver_inside_it_is_typed(self, tmp_path: Path) -> None:
        """The point of parsing it at all: its calls reach the catalogue."""
        res = _symbols(tmp_path / "recv", {"T.swift": SWIFT_TESTING})
        hits = [
            e for e in res.edges
            if e.edge_type == "calls" and e.dst.endswith(":removeItem:unresolved")
        ]
        assert len(hits) == 1, [e.dst for e in res.edges if "removeItem" in e.dst]
        assert hits[0].dst == "swift:FileManager:0-0:removeItem:unresolved"

    def test_a_try_condition_file_parses(self, tmp_path: Path) -> None:
        res = _symbols(tmp_path / "trycond", {"C.swift": (
            "import Foundation\n"
            "class Controller {\n"
            "    func handle(svc: Service) async throws {\n"
            "        if try await svc.blocked(host) {\n"
            "            let fm = FileManager.default\n"
            "            fm.removeItem(atPath: \"/tmp/x\")\n"
            "        }\n"
            "    }\n"
            "}\n"
            "class Service {\n"
            "    func blocked(_ h: String) async throws -> Bool { return false }\n"
            "}\n"
        )})
        kinds = {s.name: s.kind for s in res.symbols}
        assert kinds.get("Controller.handle") == "method", sorted(kinds)
        assert any(
            e.dst == "swift:FileManager:0-0:removeItem:unresolved"
            for e in res.edges
        ), [e.dst for e in res.edges if "removeItem" in e.dst]

    def test_a_backticked_phrase_in_a_string_survives(self, tmp_path: Path) -> None:
        res = _symbols(tmp_path / "str", {"S.swift": (
            "import Foundation\n"
            "func go() {\n"
            "    let banner = \"use `a b c` here\"\n"
            "    print(banner)\n"
            "}\n"
        )})
        assert any(s.name == "go" for s in res.symbols)
