# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/profile.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""Apple's app frameworks are detected by their import, the only evidence there is.

``swiftui.yaml`` (the SwiftUI ``App``, the UIKit and AppKit app delegates,
the view controllers) loaded only when a ``Package.swift`` declared a
dependency named ``swiftui``. No app does: SwiftUI, UIKit and AppKit ship
with the platform SDK. And ``import SwiftUI`` did not load it either, because
a one-word import promotes a framework only for an allowlist of web
frameworks. On the examples of swift-composable-architecture (100 files
importing SwiftUI, 8 ``App`` conformers, 3 app delegates) no framework was
detected and no app was an entry.

The same file held the ``ParsableCommand`` rows, which belong to
swift-argument-parser, a package dependency (``import ArgumentParser``):
swift-argument-parser's own 235 commands were not commands. And
``XCTestCase``, which is a test convention of the language and is only ever
imported by tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.profile import (
    LANGUAGE_FRAMEWORKS,
    RepoProfile,
    _BARE_EXACT_PROMOTE_ROUTE_FRAMEWORKS,
    _IMPORT_IS_THE_EVIDENCE_FRAMEWORKS,
    _detect_swift_frameworks,
    refine_frameworks,
)


def _file(path: str, language: str = "swift") -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-1:file:file", name=path, kind="file",
        language=language, path=path, span=Span(1, 1, 0, 0),
    )


def _import(src: Symbol, module: str, language: str = "swift") -> Edge:
    dst = f"{language}:{module}:0-0:module:module"
    return Edge(
        id=f"e-{src.id}-{dst}", src=src.id, dst=dst, edge_type="imports",
        line=1, origin="test", origin_run_id="test",
    )


def _refined(*imports: tuple[str, str, str]) -> RepoProfile:
    """``refine_frameworks`` over ``(importing path, module, language)`` rows,
    starting from a profile no manifest contributed to."""
    files = {path: _file(path, language) for path, _module, language in imports}
    edges = [_import(files[path], module, language) for path, module, language in imports]
    return refine_frameworks(
        RepoProfile(frameworks=[], framework_mode="auto"), edges, list(files.values()),
    )


class TestImportPromotion:
    @pytest.mark.parametrize("module", ["SwiftUI", "UIKit", "AppKit"])
    def test_a_platform_framework_import_loads_the_app_rows(self, module: str) -> None:
        result = _refined(("App/MyApp.swift", module, "swift"))

        assert "swiftui" in result.frameworks

    def test_the_argument_parser_import_loads_its_rows(self) -> None:
        result = _refined(("Sources/tool/Tool.swift", "ArgumentParser", "swift"))

        assert "swift-argument-parser" in result.frameworks
        assert "swiftui" not in result.frameworks

    def test_an_import_in_test_code_only_does_not(self) -> None:
        result = _refined(("Tests/AppTests/AppTests.swift", "SwiftUI", "swift"))

        assert "swiftui" not in result.frameworks

    def test_the_same_module_name_in_another_language_does_not(self) -> None:
        result = _refined(("src/app.py", "uikit", "python"))

        assert "swiftui" not in result.frameworks

    def test_an_unrelated_swift_import_does_not(self) -> None:
        result = _refined(("Sources/x/X.swift", "Foundation", "swift"))

        assert not {"swiftui", "swift-argument-parser"} & set(result.frameworks)

    def test_the_set_is_small_named_and_apart_from_the_route_allowlist(self) -> None:
        """Each member is a detection key, so the entry is not a no-op, and
        none is a route framework riding on the other list's reasoning."""
        all_keys = {fw for d in LANGUAGE_FRAMEWORKS.values() for fw in d}

        assert _IMPORT_IS_THE_EVIDENCE_FRAMEWORKS == {"swiftui", "swift-argument-parser"}
        assert _IMPORT_IS_THE_EVIDENCE_FRAMEWORKS <= all_keys
        assert not _IMPORT_IS_THE_EVIDENCE_FRAMEWORKS & _BARE_EXACT_PROMOTE_ROUTE_FRAMEWORKS


def test_a_package_manifest_declares_argument_parser(tmp_path: Path) -> None:
    (tmp_path / "Package.swift").write_text(
        'import PackageDescription\n'
        'let package = Package(name: "tool", dependencies: [\n'
        '  .package(url: "https://github.com/apple/swift-argument-parser", from: "1.3.0"),\n'
        '])\n'
    )

    assert "swift-argument-parser" in _detect_swift_frameworks(tmp_path)


def _swift(name: str, kind: str, *bases: str, path: str = "Sources/App/X.swift") -> Symbol:
    meta: dict[str, Any] = {"base_classes": list(bases)}
    return Symbol(
        id=f"swift:{path}:1-9:{name}:{kind}", name=name, kind=kind,
        language="swift", path=path, span=Span(1, 9, 0, 0), meta=meta,
    )


def _concepts(symbol: Symbol, *frameworks: str) -> set[str]:
    clear_pattern_cache()
    enrich_symbols([symbol], set(frameworks))
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


class TestRows:
    @pytest.mark.parametrize(
        "base", ["App", "UIApplicationDelegate", "NSApplicationDelegate"],
    )
    def test_an_app_type_is_a_program_start_where_the_app_rows_load(
        self, base: str,
    ) -> None:
        assert "entrypoint" in _concepts(_swift("MyApp", "struct", base), "swiftui")
        assert "entrypoint" not in _concepts(_swift("MyApp", "struct", base))

    @pytest.mark.parametrize("base", ["ParsableCommand", "AsyncParsableCommand"])
    def test_a_command_is_argument_parsers_not_swiftuis(self, base: str) -> None:
        assert "command" in _concepts(
            _swift("Greet", "struct", base), "swift-argument-parser",
        )
        assert "command" not in _concepts(_swift("Greet", "struct", base), "swiftui")
        assert "command" not in _concepts(_swift("Greet", "struct", base))

    def test_an_xctest_case_is_a_test_class_with_no_framework_detected(self) -> None:
        case = _swift("AppTests", "class", "XCTestCase", path="Tests/AppTests/AppTests.swift")

        assert "test_class" in _concepts(case)

    def test_the_xctest_row_is_swifts(self) -> None:
        other = Symbol(
            id="objc:Tests/T.m:1-9:T:class", name="T", kind="class", language="objc",
            path="Tests/T.m", span=Span(1, 9, 0, 0), meta={"base_classes": ["XCTestCase"]},
        )

        assert "test_class" not in _concepts(other)
