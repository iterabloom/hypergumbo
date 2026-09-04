# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift file the grammar cannot parse is retried through a byte-length-preserving
rewrite of the spellings tree-sitter-swift 0.7.3 lags on (INV-bisok, the Swift-Testing half).

INV-bisok was filed against Alamofire's `#if`-inside-a-class-body (closed by the 0.7.3
grammar bump) and its residual half was recorded as the `@Suite` / `@Test` macro
attributes, on the evidence that 225 of VernissageServer's 263 error-parsing files are
Swift Testing files. Bisecting the constructs REFUTES that: `@Suite`, `@Test`, `@Test`
with arguments and `#expect` all parse clean under 0.7.3. What error recovery was
choking on is a backtick RAW IDENTIFIER containing spaces (Swift 6.1's spelling for a
test name) and `try` inside an `if` / `guard` / `while` condition.

The rewrite is guarded twice over: it runs only on a file that ALREADY fails to parse,
and its result is kept only when it strictly reduces ERROR nodes. So a parseable file is
byte-identical through the hook, which is what stops a backtick or a `try` inside a
string literal from being rewritten.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.swift import (
    _swift_rewrite_unparseable,
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


def _symbols(root: Path, files: dict[str, str]):
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_swift(root)


class TestTheRewriteItself:
    def test_it_preserves_byte_length(self) -> None:
        """Every span the analyzer reports must still point at the real file."""
        for src in (
            SWIFT_TESTING,
            "func f() async throws {\n    if try await s.check(x) { return }\n}\n",
            "func f() async throws {\n    if let y: [D] = try? await c.get(k) { return }\n}\n",
        ):
            raw = src.encode()
            assert len(_swift_rewrite_unparseable(raw)) == len(raw)

    def test_a_backtick_identifier_keeps_its_backticks_and_loses_its_spaces(self) -> None:
        out = _swift_rewrite_unparseable(b"func `a b c`() {}\n")
        assert out == b"func `a_b_c`() {}\n"

    def test_try_is_blanked_only_inside_a_condition(self) -> None:
        out = _swift_rewrite_unparseable(
            b"if try await a() {\n}\nlet x = try await b()\n"
        )
        assert out == b"if     await a() {\n}\nlet x = try await b()\n"

    def test_a_file_with_neither_spelling_is_returned_unchanged(self) -> None:
        src = b"func go() {\n    let s = `default`\n}\n"
        assert _swift_rewrite_unparseable(src) == src


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

    def test_a_parseable_file_is_not_rewritten(self, tmp_path: Path) -> None:
        """A backtick-with-spaces inside a STRING must survive, because the file
        it sits in parses and so never reaches the rewrite."""
        res = _symbols(tmp_path / "str", {"S.swift": (
            "import Foundation\n"
            "func go() {\n"
            "    let banner = \"use `a b c` here\"\n"
            "    print(banner)\n"
            "}\n"
        )})
        assert any(s.name == "go" for s in res.symbols)
        assert (tmp_path / "str" / "S.swift").read_text().count("`a b c`") == 1

    def test_an_erroring_file_with_neither_spelling_is_left_alone(
        self, tmp_path: Path,
    ) -> None:
        """The `rewritten == source` early return: a parse failure the rewrite has
        no answer for keeps the original bytes and the original tree."""
        res = _symbols(tmp_path / "other", {"O.swift": "struct S {\n  ],\n"})
        assert any(s.name == "S" and s.kind == "struct" for s in res.symbols)

    def test_a_rewrite_that_does_not_help_is_discarded(self, tmp_path: Path) -> None:
        """The strictly-fewer-errors guard, and the reason it exists: a backtick
        identifier inside a STRING must not be rewritten just because the file
        happens to fail to parse for an unrelated reason. Here the rewrite DOES
        change the bytes (the string contains a backticked phrase with a space) and
        does NOT reduce the ERROR count, so the original bytes are what the analysis
        runs on."""
        res = _symbols(tmp_path / "nohelp", {"N.swift": (
            "struct S {\n"
            "  ],\n"
            "}\n"
            "let banner = \"use `a b` here\"\n"
        )})
        assert any(s.name == "S" for s in res.symbols)
