# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the build target linker.

The build target linker connects manifest-declared build targets
(e.g., Cargo [[bin]] entries, npm bin entries) to the main()
function in their target files. Without this, forward slices from
Cargo binary entrypoints are dead ends — the defines_target edge
points to a file path that doesn't resolve to a function node.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Symbol, Span, make_pass_id
from hypergumbo_core.linkers.build_target import link_build_targets
from hypergumbo_core.linkers.registry import LinkerContext


def _make_symbol(
    lang: str, path: str, start: int, end: int, name: str, kind: str,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=f"{lang}:{path}:{start}-{end}:{name}:{kind}",
        name=name,
        kind=kind,
        path=path,
        span=Span(start_line=start, start_col=0, end_line=end, end_col=0),
        language=lang,
        origin=make_pass_id("test"),
    )


def _make_edge(
    src: str, dst: str, edge_type: str, line: int = 1,
    meta: dict | None = None,
) -> Edge:
    """Create a test edge."""
    return Edge.create(
        src=src, dst=dst, edge_type=edge_type, line=line,
        confidence=1.0, origin=make_pass_id("test"),
        meta=meta,

        origin_run_id="test",
    )


class TestBuildTargetLinker:
    """Tests for build target → main() linking."""

    def test_cargo_binary_to_rust_main(self) -> None:
        """Cargo [[bin]] with defines_target to src/main.rs gets edge to main()."""
        binary_sym = _make_symbol("toml", "Cargo.toml", 11, 15, "myapp", "binary")
        main_sym = _make_symbol("rust", "src/main.rs", 1, 10, "main", "function")
        defines_edge = _make_edge(binary_sym.id, "src/main.rs", "defines_target")

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary_sym, main_sym],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == binary_sym.id
        assert edge.dst == main_sym.id
        assert edge.edge_type == "calls"
        assert edge.confidence >= 0.90

    def test_no_defines_target_edges(self) -> None:
        """No defines_target edges → no new edges."""
        main_sym = _make_symbol("rust", "src/main.rs", 1, 10, "main", "function")
        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[main_sym],
            edges=[],
        )
        result = link_build_targets(ctx)
        assert len(result.edges) == 0

    def test_skips_non_defines_target_edges(self) -> None:
        """Non-defines_target edges are ignored."""
        main_sym = _make_symbol("rust", "src/main.rs", 1, 10, "main", "function")
        other_sym = _make_symbol("rust", "src/lib.rs", 1, 5, "helper", "function")
        calls_edge = _make_edge(main_sym.id, other_sym.id, "calls")

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[main_sym, other_sym],
            edges=[calls_edge],
        )
        result = link_build_targets(ctx)
        assert len(result.edges) == 0

    def test_defines_target_no_main_in_file(self) -> None:
        """defines_target to a file without main() → no new edge."""
        binary_sym = _make_symbol("toml", "Cargo.toml", 11, 15, "mylib", "binary")
        other_fn = _make_symbol("rust", "src/lib.rs", 1, 10, "run", "function")
        defines_edge = _make_edge(binary_sym.id, "src/lib.rs", "defines_target")

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary_sym, other_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)
        assert len(result.edges) == 0

    def test_multiple_binaries(self) -> None:
        """Multiple Cargo binaries each get linked to their own main()."""
        bin1 = _make_symbol("toml", "Cargo.toml", 5, 8, "cli", "binary")
        bin2 = _make_symbol("toml", "Cargo.toml", 10, 13, "server", "binary")
        main1 = _make_symbol("rust", "src/bin/cli.rs", 1, 5, "main", "function")
        main2 = _make_symbol("rust", "src/bin/server.rs", 1, 5, "main", "function")

        edges = [
            _make_edge(bin1.id, "src/bin/cli.rs", "defines_target"),
            _make_edge(bin2.id, "src/bin/server.rs", "defines_target"),
        ]

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[bin1, bin2, main1, main2],
            edges=edges,
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 2
        dsts = {e.dst for e in result.edges}
        assert main1.id in dsts
        assert main2.id in dsts

    def test_npm_bin_to_js_main(self) -> None:
        """npm bin entry with defines_target gets linked to its main function."""
        bin_sym = _make_symbol("json", "package.json", 5, 5, "mycli", "bin")
        # The JS file has a main-like function at top level
        main_fn = _make_symbol(
            "javascript", "src/index.js", 1, 50, "main", "function",
        )
        defines_edge = _make_edge(bin_sym.id, "src/index.js", "defines_target")

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[bin_sym, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == main_fn.id

    def test_prefers_main_over_other_functions(self) -> None:
        """When file has main() and other functions, links to main()."""
        binary = _make_symbol("toml", "Cargo.toml", 5, 8, "app", "binary")
        helper = _make_symbol("rust", "src/main.rs", 1, 5, "helper", "function")
        main_fn = _make_symbol("rust", "src/main.rs", 7, 20, "main", "function")

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary, helper, main_fn],
            edges=[_make_edge(binary.id, "src/main.rs", "defines_target")],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == main_fn.id

    def test_no_duplicate_edges(self) -> None:
        """Same binary-to-main pair doesn't produce duplicate edges."""
        binary = _make_symbol("toml", "Cargo.toml", 5, 8, "app", "binary")
        main_fn = _make_symbol("rust", "src/main.rs", 1, 10, "main", "function")

        # Two defines_target edges to the same file (shouldn't happen but be safe)
        edges = [
            _make_edge(binary.id, "src/main.rs", "defines_target"),
            _make_edge(binary.id, "src/main.rs", "defines_target"),
        ]

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary, main_fn],
            edges=edges,
        )
        result = link_build_targets(ctx)
        assert len(result.edges) == 1

    def test_python_script_target_function(self) -> None:
        """Python script with target_function meta links to named function."""
        script = _make_symbol("toml", "pyproject.toml", 5, 5, "my-cli", "script")
        # The entry point is mypackage.cli:run_app (not main)
        run_app = _make_symbol(
            "python", "mypackage/cli.py", 10, 30, "run_app", "function",
        )
        main_fn = _make_symbol(
            "python", "mypackage/cli.py", 1, 5, "main", "function",
        )

        defines_edge = _make_edge(
            script.id, "mypackage/cli.py", "defines_target",
            meta={"target_function": "run_app"},
        )

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[script, run_app, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        # Should link to run_app (specified in meta), NOT main
        assert len(result.edges) == 1
        assert result.edges[0].dst == run_app.id

    def test_monorepo_cargo_binary_resolves_relative_path(self) -> None:
        """Cargo [[bin]] in a subdirectory resolves target path relative to manifest.

        In monorepos, the Cargo.toml is in a subdirectory (e.g., crates/myapp/)
        and the defines_target dst is relative to that directory (e.g., ./bin/main.rs).
        The linker must resolve this to the repo-root-relative path
        (crates/myapp/bin/main.rs) to find the main() symbol.
        """
        binary = _make_symbol(
            "toml", "crates/myapp/Cargo.toml", 10, 14, "myapp", "binary",
        )
        main_fn = _make_symbol(
            "rust", "crates/myapp/src/main.rs", 1, 10, "main", "function",
        )
        # dst is relative to manifest directory: src/main.rs
        defines_edge = _make_edge(
            binary.id, "src/main.rs", "defines_target",
        )

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == main_fn.id
        assert result.edges[0].edge_type == "calls"

    def test_monorepo_cargo_binary_dot_slash_prefix(self) -> None:
        """Cargo [[bin]] with ./ prefix in target path resolves correctly."""
        binary = _make_symbol(
            "toml", "crates/rolldown_testing/Cargo.toml", 46, 51,
            "run-fixture", "binary",
        )
        main_fn = _make_symbol(
            "rust", "crates/rolldown_testing/bin/run_fixture.rs", 1, 10,
            "main", "function",
        )
        # dst has ./ prefix as seen in rolldown's Cargo.toml
        defines_edge = _make_edge(
            binary.id, "./bin/run_fixture.rs", "defines_target",
        )

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[binary, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == main_fn.id

    def test_monorepo_npm_bin_resolves_relative_path(self) -> None:
        """npm bin in a subdirectory resolves target path relative to package.json."""
        bin_sym = _make_symbol(
            "json", "packages/rolldown/package.json", 21, 21,
            "rolldown", "bin",
        )
        main_fn = _make_symbol(
            "javascript", "packages/rolldown/bin/cli.mjs", 1, 20,
            "main", "function",
        )
        defines_edge = _make_edge(
            bin_sym.id, "bin/cli.mjs", "defines_target",
        )

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[bin_sym, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == main_fn.id

    def test_resolve_target_path_src_not_found(self) -> None:
        """When src node is not in symbol index, path is returned as-is."""
        from hypergumbo_core.linkers.build_target import _resolve_target_path

        result = _resolve_target_path("src/main.rs", "nonexistent:id", {})
        assert result == "src/main.rs"

    def test_resolve_target_path_manifest_at_root(self) -> None:
        """When manifest is at repo root, path is returned as-is."""
        from hypergumbo_core.linkers.build_target import _resolve_target_path

        # Create a mock symbol with path at root level (no directory)
        root_sym = _make_symbol("toml", "Cargo.toml", 5, 8, "app", "binary")
        result = _resolve_target_path(
            "src/main.rs", root_sym.id, {root_sym.id: root_sym},
        )
        assert result == "src/main.rs"

    def test_a_named_function_that_is_not_there_is_not_some_other_main(self) -> None:
        """``mypackage.cli:nonexistent`` names its function; linking the script
        to ``cli.main`` would assert the script runs ``main()``, which it does
        not (WI-lipal). The usual cause is a re-export (``cli.py`` imports
        ``run`` from elsewhere): the target is that ``run``, not this file's
        ``main``. No edge, rather than a wrong one."""
        script = _make_symbol("toml", "pyproject.toml", 5, 5, "my-cli", "script")
        main_fn = _make_symbol(
            "python", "mypackage/cli.py", 1, 10, "main", "function",
        )

        defines_edge = _make_edge(
            script.id, "mypackage/cli.py", "defines_target",
            meta={"target_function": "nonexistent"},
        )

        ctx = LinkerContext(
            repo_root=Path("/fake"),
            symbols=[script, main_fn],
            edges=[defines_edge],
        )
        result = link_build_targets(ctx)

        assert result.edges == []


def _python_script(manifest: str, entry: str) -> tuple[Symbol, Edge]:
    """A pyproject console script and its defines_target edge, as toml_config
    emits them: a dotted placeholder dst, the file path and function in meta."""
    module, func = entry.split(":")
    script = _make_symbol("toml", manifest, 5, 5, "my-cli", "file")
    edge = _make_edge(
        script.id, f"python:{module}:0-0:{func}:unresolved", "defines_target",
        meta={"target_path": module.replace(".", "/") + ".py", "target_function": func},
    )
    return script, edge


def _link(symbols: list[Symbol], edges: list[Edge]) -> list[str]:
    ctx = LinkerContext(repo_root=Path("/fake"), symbols=symbols, edges=edges)
    return [e.dst for e in link_build_targets(ctx).edges]


class TestPythonTargetsBeyondTheManifestDirectory:
    """A console script's module is found where Python would import it from,
    not only beside the manifest (WI-lipal). The module path is the target's
    identity; the file is wherever exactly one in-tree file has that path."""

    def test_a_src_layout_member(self) -> None:
        script, edge = _python_script("packages/tool/pyproject.toml", "tool.cli:main")
        main = _make_symbol("python", "packages/tool/src/tool/cli.py", 1, 9, "main", "function")
        assert _link([script, main], [edge]) == [main.id]

    def test_a_script_naming_another_package_s_module(self) -> None:
        """The meta-package's ``hypergumbo = "hypergumbo_core.cli:main"``."""
        script, edge = _python_script("packages/meta/pyproject.toml", "core.cli:main")
        main = _make_symbol("python", "packages/core/src/core/cli.py", 1, 9, "main", "function")
        assert _link([script, main], [edge]) == [main.id]

    def test_a_package_module(self) -> None:
        """``pkg:main`` is ``pkg/__init__.py``'s main."""
        script, edge = _python_script("pyproject.toml", "pkg:main")
        main = _make_symbol("python", "src/pkg/__init__.py", 1, 9, "main", "function")
        assert _link([script, main], [edge]) == [main.id]

    def test_two_in_tree_files_with_the_module_s_path_name_nothing(self) -> None:
        script, edge = _python_script("packages/meta/pyproject.toml", "core.cli:main")
        one = _make_symbol("python", "packages/a/src/core/cli.py", 1, 9, "main", "function")
        two = _make_symbol("python", "tests/fixtures/core/cli.py", 1, 9, "main", "function")
        assert _link([script, one, two], [edge]) == []

    def test_the_manifest_s_own_tree_wins_over_a_same_path_elsewhere(self) -> None:
        script, edge = _python_script("packages/tool/pyproject.toml", "tool.cli:main")
        own = _make_symbol("python", "packages/tool/src/tool/cli.py", 1, 9, "main", "function")
        other = _make_symbol("python", "vendor/x/tool/cli.py", 1, 9, "main", "function")
        assert _link([script, own, other], [edge]) == [own.id]

    def test_a_path_suffix_must_be_whole_segments(self) -> None:
        """``mytool/cli.py`` is not module ``tool.cli``."""
        script, edge = _python_script("packages/meta/pyproject.toml", "tool.cli:main")
        main = _make_symbol("python", "packages/x/src/mytool/cli.py", 1, 9, "main", "function")
        assert _link([script, main], [edge]) == []

    def test_a_non_python_target_takes_no_module_route(self) -> None:
        """A Cargo bin path is relative to its manifest only."""
        binary = _make_symbol("toml", "crates/app/Cargo.toml", 1, 1, "app", "binary")
        main = _make_symbol("rust", "crates/other/src/main.rs", 1, 9, "main", "function")
        edge = _make_edge(binary.id, "src/main.rs", "defines_target")
        assert _link([binary, main], [edge]) == []
