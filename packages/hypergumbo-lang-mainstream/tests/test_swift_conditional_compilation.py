# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ``#if`` block inside a Swift type body must not cost the type its symbol
(INV-bisok).

tree-sitter-swift 0.0.1 could not parse a conditional-compilation block inside
a class body; error recovery then re-parented the class header, so Alamofire's
``open class Session: @unchecked Sendable`` was never minted as a class, its
methods surfaced as top-level FUNCTIONS, and every ``session.x`` receiver in
the repository was treated as an external type. The pin moved to 0.7.3, where
the same shape parses cleanly; this test is what fails under the old grammar.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.swift import analyze_swift


def test_class_with_conditional_block_keeps_its_symbol_and_methods(tmp_path: Path) -> None:
    (tmp_path / "Session.swift").write_text(
        "import Foundation\n"
        "open class Session: @unchecked Sendable {\n"
        "    public let rootQueue: DispatchQueue = .main\n"
        "    #if canImport(Darwin) && !canImport(FoundationNetworking)\n"
        "    open func webSocketRequest() {}\n"
        "    #endif\n"
        "    func perform(u: URL) {\n"
        "        rootQueue.async {}\n"
        "    }\n"
        "}\n"
    )
    result = analyze_swift(tmp_path)
    by_name = {(s.name, s.kind) for s in result.symbols}
    assert ("Session", "class") in by_name, sorted(by_name)
    assert ("Session.perform", "method") in by_name, sorted(by_name)
    assert ("Session.webSocketRequest", "method") in by_name, sorted(by_name)
    assert ("perform", "function") not in by_name
    asyncs = [e for e in result.edges if e.edge_type == "calls" and e.dst.endswith(":async:unresolved")]
    assert asyncs and asyncs[0].dst == "swift:DispatchQueue:0-0:async:unresolved"
