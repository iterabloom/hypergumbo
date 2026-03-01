"""Tests for JS/TS module resolution linker.

The JS module linker resolves unresolved import edges created by the JS/TS
analyzer. Import edges have dst format like 'javascript:./utils:0-0:module:module'
(raw import path embedded in synthetic ID). The linker resolves relative paths
to actual files, creates module_file symbols, and creates module_exports edges
from module_file to the functions/methods/classes defined in the target file.

This enables cross-file graph traversal through imports:
  file_A --imports_module--> module_file_B --module_exports--> functionInB

Path alias resolution (tsconfig.json paths, Vite resolve.alias) expands
non-relative imports like '@/utils' or 'dashboard/Header' to actual file paths
before falling back to npm_package creation.
"""

import json
from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.js_module import (
    _build_tsconfig_alias_index,
    _extract_import_path,
    _extract_path_from_id,
    _get_aliases_for_file,
    _load_tsconfig_aliases,
    _load_vite_aliases,
    _parse_tsconfig_paths,
    _probe_file,
    _resolve_alias,
    _strip_jsonc_comments,
    link_js_modules,
)
from hypergumbo_core.linkers.registry import LinkerContext


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Create a minimal repo structure with JS files."""
    # src/app.js imports ./utils
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.js").write_text("import { helper } from './utils';")
    (src / "utils.js").write_text("export function helper() {}")
    (src / "utils.ts").write_text("")  # should prefer .js first
    (src / "config.json").write_text("{}")

    # src/components/Modal.vue
    comp = src / "components"
    comp.mkdir()
    (comp / "Modal.vue").write_text("<template><div></div></template>")

    # src/lib/ directory with index.js
    lib = src / "lib"
    lib.mkdir()
    (lib / "index.js").write_text("export default {}")

    # src/helpers/ directory with index.ts (no .js index)
    helpers = src / "helpers"
    helpers.mkdir()
    (helpers / "index.ts").write_text("export default {}")

    # src/styles/ directory with no index file
    styles = src / "styles"
    styles.mkdir()
    (styles / "main.css").write_text("")

    return tmp_path


class TestExtractPathFromId:
    """Tests for parsing file path from symbol ID."""

    def test_absolute_path(self) -> None:
        sid = "javascript:/home/user/repo/app.js:1-1:app.js:file"
        assert _extract_path_from_id(sid) == "/home/user/repo/app.js"

    def test_relative_path(self) -> None:
        sid = "javascript:src/app.js:5-10:app.js:function"
        assert _extract_path_from_id(sid) == "src/app.js"

    def test_invalid_format(self) -> None:
        assert _extract_path_from_id("not-a-valid-id") is None

    def test_deep_nested_path(self) -> None:
        sid = "typescript:/repo/src/components/deep/Modal.vue:25-30:handleClick:method"
        assert _extract_path_from_id(sid) == "/repo/src/components/deep/Modal.vue"


class TestExtractImportPath:
    """Tests for parsing import path from unresolved dst ID."""

    def test_relative_path(self) -> None:
        dst = "javascript:./utils:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "./utils")

    def test_relative_parent(self) -> None:
        dst = "javascript:../shared/helpers:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "../shared/helpers")

    def test_bare_module(self) -> None:
        dst = "javascript:lodash:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "lodash")

    def test_scoped_module(self) -> None:
        dst = "javascript:@vue/test-utils:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "@vue/test-utils")

    def test_nested_path(self) -> None:
        dst = "javascript:shared/helpers/format:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "shared/helpers/format")

    def test_typescript_import(self) -> None:
        dst = "typescript:./types:0-0:module:module"
        assert _extract_import_path(dst) == ("typescript", "./types")

    def test_not_module_format(self) -> None:
        """Non-module dst returns None."""
        dst = "javascript:/path/to/file.js:1-10:myFunc:function"
        assert _extract_import_path(dst) is None

    def test_dynamic_import(self) -> None:
        """Dynamic imports (require with variable) still parsed as import path."""
        dst = "javascript:<dynamic:configPath>:0-0:module:module"
        assert _extract_import_path(dst) == ("javascript", "<dynamic:configPath>")

    def test_no_colon_in_prefix(self) -> None:
        """Dst with no colon before module suffix returns None."""
        dst = "nocolon:0-0:module:module"
        assert _extract_import_path(dst) is None

    def test_empty_import_path(self) -> None:
        """Dst with empty import path returns None."""
        dst = "javascript::0-0:module:module"
        assert _extract_import_path(dst) is None


class TestProbeFile:
    """Tests for file extension probing."""

    def test_exact_match_js(self, repo_root: Path) -> None:
        """./utils resolves to utils.js when it exists."""
        result = _probe_file(repo_root / "src" / "utils")
        assert result is not None
        assert result.name == "utils.js"

    def test_exact_match_with_extension(self, repo_root: Path) -> None:
        """./utils.js resolves directly."""
        result = _probe_file(repo_root / "src" / "utils.js")
        assert result is not None
        assert result.name == "utils.js"

    def test_vue_extension(self, repo_root: Path) -> None:
        """./components/Modal resolves to Modal.vue."""
        result = _probe_file(repo_root / "src" / "components" / "Modal")
        assert result is not None
        assert result.name == "Modal.vue"

    def test_directory_index_js(self, repo_root: Path) -> None:
        """./lib resolves to lib/index.js."""
        result = _probe_file(repo_root / "src" / "lib")
        assert result is not None
        assert str(result).endswith("lib/index.js")

    def test_directory_index_ts(self, repo_root: Path) -> None:
        """./helpers resolves to helpers/index.ts."""
        result = _probe_file(repo_root / "src" / "helpers")
        assert result is not None
        assert str(result).endswith("helpers/index.ts")

    def test_directory_no_index(self, repo_root: Path) -> None:
        """Directory without index file returns None."""
        result = _probe_file(repo_root / "src" / "styles")
        assert result is None

    def test_nonexistent(self, repo_root: Path) -> None:
        """Completely nonexistent path returns None."""
        result = _probe_file(repo_root / "src" / "doesnotexist")
        assert result is None

    def test_json_extension(self, repo_root: Path) -> None:
        """./config.json resolves directly."""
        result = _probe_file(repo_root / "src" / "config.json")
        assert result is not None
        assert result.name == "config.json"


class TestLinkJsModules:
    """Tests for the main linker function."""

    def _make_file_symbol(
        self,
        path: str,
        lang: str = "javascript",
    ) -> Symbol:
        """Helper to create a file symbol."""
        name = Path(path).name
        return Symbol(
            id=f"{lang}:{path}:1-1:{name}:file",
            name=name,
            kind="file",
            language=lang,
            path=path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def _make_import_edge(
        self,
        src_id: str,
        module_path: str,
        lang: str = "javascript",
        line: int = 1,
    ) -> Edge:
        """Helper to create an unresolved import edge."""
        dst_id = f"{lang}:{module_path}:0-0:module:module"
        return Edge.create(
            src=src_id,
            dst=dst_id,
            edge_type="imports",
            line=line,
            origin="js-ts-v1",
            origin_run_id="test-run",
            evidence_type="import_static",
            confidence=0.95,
        )

    def _make_function_symbol(
        self,
        path: str,
        name: str,
        kind: str = "function",
        lang: str = "javascript",
        start_line: int = 5,
        end_line: int = 10,
    ) -> Symbol:
        """Helper to create a function/method symbol."""
        return Symbol(
            id=f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}",
            name=name,
            kind=kind,
            language=lang,
            path=path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=0,
                end_col=0,
            ),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def test_resolves_relative_import(self, repo_root: Path) -> None:
        """./utils import from src/app.js resolves to src/utils.js."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "./utils")

        # Function defined in utils.js
        utils_path = str(repo_root / "src" / "utils.js")
        helper_fn = self._make_function_symbol(utils_path, "helper")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym, helper_fn],
            edges=[import_edge],
        )

        # Should create: module_file symbol + imports_module edge + module_exports edge
        assert len(result.symbols) >= 1
        module_file = result.symbols[0]
        assert module_file.kind == "module_file"
        assert "utils.js" in module_file.path

        # Should have imports_module edge (file -> module_file)
        imports_module_edges = [
            e for e in result.edges if e.edge_type == "imports_module"
        ]
        assert len(imports_module_edges) == 1
        assert imports_module_edges[0].src == file_sym.id
        assert imports_module_edges[0].dst == module_file.id

        # Should have module_exports edge (module_file -> helper function)
        exports_edges = [e for e in result.edges if e.edge_type == "module_exports"]
        assert len(exports_edges) == 1
        assert exports_edges[0].src == module_file.id
        assert exports_edges[0].dst == helper_fn.id

    def test_resolves_directory_import(self, repo_root: Path) -> None:
        """./lib import resolves to lib/index.js."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "./lib")

        index_path = str(repo_root / "src" / "lib" / "index.js")
        default_fn = self._make_function_symbol(index_path, "default")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym, default_fn],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert "lib/index.js" in module_files[0].path

    def test_resolves_parent_relative(self, repo_root: Path) -> None:
        """../utils import from deep path resolves correctly."""
        # File at src/components/Modal.js importing ../utils
        comp_path = str(repo_root / "src" / "components" / "Modal.js")
        (repo_root / "src" / "components" / "Modal.js").write_text("")
        file_sym = self._make_file_symbol(comp_path)
        import_edge = self._make_import_edge(file_sym.id, "../utils")

        utils_path = str(repo_root / "src" / "utils.js")
        helper_fn = self._make_function_symbol(utils_path, "helper")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym, helper_fn],
            edges=[import_edge],
        )

        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1

    def test_bare_module_creates_npm_package(self, repo_root: Path) -> None:
        """Bare module 'lodash' creates npm_package symbol."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "lodash")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        npm_packages = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm_packages) == 1
        assert npm_packages[0].name == "lodash"
        assert npm_packages[0].supply_chain_tier == 3
        assert npm_packages[0].supply_chain_reason == "npm_package (third-party dependency)"

        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1
        assert imports_module[0].dst == npm_packages[0].id

    def test_scoped_npm_package(self, repo_root: Path) -> None:
        """Scoped @vue/test-utils creates npm_package symbol."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "@vue/test-utils")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        npm_packages = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm_packages) == 1
        assert npm_packages[0].name == "@vue/test-utils"
        assert npm_packages[0].supply_chain_tier == 3

    def test_unresolvable_relative_ignored(self, repo_root: Path) -> None:
        """Relative import to nonexistent file produces no results."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "./nonexistent")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_non_import_edges_ignored(self, repo_root: Path) -> None:
        """Non-import edges are not processed."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        call_edge = Edge.create(
            src=file_sym.id,
            dst="javascript:someFunc:5-10:someFunc:function",
            edge_type="calls",
            line=5,
            origin="js-ts-v1",
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[call_edge],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_dynamic_import_skipped(self, repo_root: Path) -> None:
        """Dynamic require(<variable>) imports are skipped."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(
            file_sym.id, "<dynamic:configPath>"
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_multiple_imports_same_target(self, repo_root: Path) -> None:
        """Multiple files importing same module share one module_file symbol."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym_a = self._make_file_symbol(app_path)

        other_path = str(repo_root / "src" / "other.js")
        (repo_root / "src" / "other.js").write_text("")
        file_sym_b = self._make_file_symbol(other_path)

        import_a = self._make_import_edge(file_sym_a.id, "./utils")
        import_b = self._make_import_edge(file_sym_b.id, "./utils")

        utils_path = str(repo_root / "src" / "utils.js")
        helper_fn = self._make_function_symbol(utils_path, "helper")

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym_a, file_sym_b, helper_fn],
            edges=[import_a, import_b],
        )

        # Only one module_file symbol for utils.js
        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1

        # Two imports_module edges (one from each importing file)
        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 2

        # Only one module_exports edge (helper -> module_file, not duplicated)
        exports_edges = [e for e in result.edges if e.edge_type == "module_exports"]
        assert len(exports_edges) == 1

    def test_multiple_functions_in_target(self, repo_root: Path) -> None:
        """Target file with multiple functions creates multiple exports edges."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "./utils")

        utils_path = str(repo_root / "src" / "utils.js")
        fn1 = self._make_function_symbol(utils_path, "helper", start_line=1, end_line=5)
        fn2 = self._make_function_symbol(
            utils_path, "format", start_line=7, end_line=12
        )
        cls = self._make_function_symbol(
            utils_path, "Formatter", kind="class", start_line=14, end_line=30
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym, fn1, fn2, cls],
            edges=[import_edge],
        )

        exports_edges = [e for e in result.edges if e.edge_type == "module_exports"]
        assert len(exports_edges) == 3  # helper, format, Formatter
        export_dsts = {e.dst for e in exports_edges}
        assert fn1.id in export_dsts
        assert fn2.id in export_dsts
        assert cls.id in export_dsts

    def test_src_without_path_skipped(self, repo_root: Path) -> None:
        """Import edge with completely unparseable src is skipped."""
        import_edge = self._make_import_edge(
            "badformat", "./utils"
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[],
            edges=[import_edge],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_src_path_from_id_fallback(self, repo_root: Path) -> None:
        """Import edge src not in symbols list but path parseable from ID."""
        # File symbol not in the symbols list (simulates real-world behavior
        # where file-level symbols aren't in the output nodes)
        app_path = str(repo_root / "src" / "app.js")
        src_id = f"javascript:{app_path}:1-1:app.js:file"
        import_edge = self._make_import_edge(src_id, "./utils")

        utils_path = str(repo_root / "src" / "utils.js")
        helper_fn = self._make_function_symbol(utils_path, "helper")

        # Note: file_sym is NOT in the symbols list
        result = link_js_modules(
            repo_root=repo_root,
            symbols=[helper_fn],  # Only the target function, not the file symbol
            edges=[import_edge],
        )

        # Should still resolve via ID parsing fallback
        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1

    def test_vue_file_import(self, repo_root: Path) -> None:
        """Import of .vue file resolves correctly."""
        app_path = str(repo_root / "src" / "app.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(
            file_sym.id, "./components/Modal"
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert module_files[0].path.endswith("Modal.vue")

    def test_typescript_import(self, repo_root: Path) -> None:
        """TypeScript import edges are also resolved."""
        # Create a .ts file
        (repo_root / "src" / "types.ts").write_text("export type Foo = string;")
        app_path = str(repo_root / "src" / "app.ts")
        (repo_root / "src" / "app.ts").write_text("")
        file_sym = self._make_file_symbol(app_path, lang="typescript")
        import_edge = self._make_import_edge(
            file_sym.id, "./types", lang="typescript"
        )

        result = link_js_modules(
            repo_root=repo_root,
            symbols=[file_sym],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert module_files[0].path.endswith("types.ts")


class TestLinkerRegistryIntegration:
    """Tests for registry integration."""

    def test_linker_entry_point(self, tmp_path: Path) -> None:
        """Linker entry point works via LinkerContext."""
        from hypergumbo_core.linkers.js_module import link_js_module

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.js").write_text("")
        app_path = str(tmp_path / "src" / "app.js")

        file_sym = Symbol(
            id=f"javascript:{app_path}:1-1:app.js:file",
            name="app.js",
            kind="file",
            language="javascript",
            path=app_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        import_edge = Edge.create(
            src=file_sym.id,
            dst="javascript:./utils:0-0:module:module",
            edge_type="imports",
            line=1,
            origin="js-ts-v1",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[file_sym],
            edges=[import_edge],
        )

        result = link_js_module(ctx)
        assert result.run is not None
        # Should resolve the import
        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1

    def test_activation_requires_js_or_ts(self) -> None:
        """Linker activation requires javascript or typescript language."""
        from hypergumbo_core.linkers.js_module import link_js_module
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("js-modules")
        assert linker is not None
        activation = linker.activation

        # Should run when javascript is detected
        assert activation.should_run(set(), {"javascript"})
        assert activation.should_run(set(), {"typescript"})
        assert activation.should_run(set(), {"javascript", "typescript"})

        # Should NOT run for non-JS/TS repos
        assert not activation.should_run(set(), {"python", "ruby"})

    def test_count_js_import_edges(self, tmp_path: Path) -> None:
        """_count_js_import_edges counts only import edges with module format."""
        from hypergumbo_core.linkers.js_module import _count_js_import_edges

        import_edge = Edge.create(
            src="javascript:file.js:1-1:file.js:file",
            dst="javascript:./utils:0-0:module:module",
            edge_type="imports",
            line=1,
            origin="test",
        )
        call_edge = Edge.create(
            src="javascript:file.js:1-1:file.js:file",
            dst="javascript:file.js:5-10:foo:function",
            edge_type="calls",
            line=5,
            origin="test",
        )
        import_non_module = Edge.create(
            src="javascript:file.js:1-1:file.js:file",
            dst="javascript:file.js:5-10:foo:function",
            edge_type="imports",
            line=1,
            origin="test",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            edges=[import_edge, call_edge, import_non_module],
        )
        assert _count_js_import_edges(ctx) == 1


class TestNpmHelpers:
    """Tests for npm package helper functions."""

    def test_scoped_package_single_part(self) -> None:
        """Scoped package with no slash after scope returns full string."""
        from hypergumbo_core.linkers.js_module import _get_npm_package_name

        assert _get_npm_package_name("@types") == "@types"

    def test_scoped_package_with_subpath(self) -> None:
        """Scoped package with subpath returns scope/name only."""
        from hypergumbo_core.linkers.js_module import _get_npm_package_name

        assert _get_npm_package_name("@babel/core/lib/foo") == "@babel/core"

    def test_is_npm_package_relative(self) -> None:
        """Relative paths are not npm packages."""
        from hypergumbo_core.linkers.js_module import _is_npm_package

        assert not _is_npm_package("./utils")
        assert not _is_npm_package("../shared/helpers")

    def test_is_npm_package_dynamic(self) -> None:
        """Dynamic imports are not npm packages."""
        from hypergumbo_core.linkers.js_module import _is_npm_package

        assert not _is_npm_package("<dynamic:configPath>")

    def test_is_npm_package_bare(self) -> None:
        """Bare module names are npm packages."""
        from hypergumbo_core.linkers.js_module import _is_npm_package

        assert _is_npm_package("lodash")
        assert _is_npm_package("@vue/test-utils")


class TestEdgeCases:
    """Tests for edge cases in the linker."""

    def test_import_edge_non_module_dst(self, tmp_path: Path) -> None:
        """Import edge with non-module dst format is skipped."""
        file_sym = Symbol(
            id="javascript:/repo/file.js:1-1:file.js:file",
            name="file.js",
            kind="file",
            language="javascript",
            path=str(tmp_path / "file.js"),
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )
        # Import edge with dst that doesn't match module:module format
        edge = Edge.create(
            src=file_sym.id,
            dst="javascript:/repo/other.js:1-10:Other:class",
            edge_type="imports",
            line=1,
            origin="js-ts-v1",
        )

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym],
            edges=[edge],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_resolve_outside_repo(self, tmp_path: Path) -> None:
        """Import resolving to path outside repo_root uses absolute path."""
        # Create a nested repo structure where resolve goes above repo_root
        inner = tmp_path / "inner"
        inner.mkdir()
        (tmp_path / "outside.js").write_text("")

        app_path = str(inner / "app.js")
        (inner / "app.js").write_text("")

        file_sym = Symbol(
            id=f"javascript:{app_path}:1-1:app.js:file",
            name="app.js",
            kind="file",
            language="javascript",
            path=app_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )
        # Import ../outside.js goes above inner/ (the repo_root)
        edge = Edge.create(
            src=file_sym.id,
            dst="javascript:../outside:0-0:module:module",
            edge_type="imports",
            line=1,
            origin="js-ts-v1",
        )

        result = link_js_modules(
            repo_root=inner,
            symbols=[file_sym],
            edges=[edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        # Path should be absolute since it's outside repo_root
        assert module_files[0].path.startswith("/")

    def test_no_import_edges_early_return(self, tmp_path: Path) -> None:
        """No import edges returns empty result with run metadata."""
        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[],
            edges=[],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0
        assert result.run is not None


class TestStripJsoncComments:
    """Tests for JSONC comment stripping (tsconfig.json uses JSONC)."""

    def test_single_line_comment(self) -> None:
        text = '{\n  "key": "value" // comment\n}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_multiline_comment(self) -> None:
        text = '{\n  /* block comment */\n  "key": "value"\n}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_url_in_string_preserved(self) -> None:
        """Double slashes inside strings are NOT treated as comments."""
        text = '{"url": "https://example.com"}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"url": "https://example.com"}

    def test_no_comments(self) -> None:
        text = '{"key": "value"}'
        assert _strip_jsonc_comments(text) == text

    def test_trailing_comma_removed(self) -> None:
        """Trailing commas (common in JSONC) are removed."""
        text = '{"a": 1, "b": 2,}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_escaped_chars_in_string(self) -> None:
        """Escaped characters inside strings are preserved."""
        text = r'{"key": "value with \"quotes\""}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"key": 'value with "quotes"'}


class TestLoadTsconfigAliases:
    """Tests for tsconfig.json path alias loading."""

    def test_basic_wildcard_paths(self, tmp_path: Path) -> None:
        """Loads @/* -> ./src/* from tsconfig.json."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        prefix, target = aliases[0]
        assert prefix == "@/"
        assert target == tmp_path / "src"

    def test_multiple_paths(self, tmp_path: Path) -> None:
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {
                    "@/*": ["./src/*"],
                    "@components/*": ["./src/components/*"],
                },
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src" / "components").mkdir(parents=True)

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 2
        prefixes = {a[0] for a in aliases}
        assert "@/" in prefixes
        assert "@components/" in prefixes

    def test_no_tsconfig(self, tmp_path: Path) -> None:
        aliases = _load_tsconfig_aliases(tmp_path)
        assert aliases == []

    def test_no_paths_key(self, tmp_path: Path) -> None:
        tsconfig = {"compilerOptions": {"target": "es6"}}
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        aliases = _load_tsconfig_aliases(tmp_path)
        assert aliases == []

    def test_jsconfig_fallback(self, tmp_path: Path) -> None:
        """Falls back to jsconfig.json when tsconfig.json doesn't exist."""
        jsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        (tmp_path / "jsconfig.json").write_text(json.dumps(jsconfig))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1

    def test_extends_chain(self, tmp_path: Path) -> None:
        """Follows extends to load paths from parent config."""
        base = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        child = {"extends": "./tsconfig.base.json"}
        (tmp_path / "tsconfig.base.json").write_text(json.dumps(base))
        (tmp_path / "tsconfig.json").write_text(json.dumps(child))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "@/"

    def test_jsonc_comments_handled(self, tmp_path: Path) -> None:
        text = """{
  // TypeScript config
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"] // main alias
    }
  }
}"""
        (tmp_path / "tsconfig.json").write_text(text)
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1

    def test_baseurl_subdir(self, tmp_path: Path) -> None:
        """baseUrl pointing to a subdirectory offsets path targets."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": "./src",
                "paths": {"@/*": ["./*"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        _, target = aliases[0]
        assert target == tmp_path / "src"

    def test_non_wildcard_exact_path(self, tmp_path: Path) -> None:
        """Non-wildcard paths (exact module aliases) use exact prefix."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"shared": ["./packages/shared/src/index"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        prefix, _ = aliases[0]
        # No trailing slash for non-wildcard exact alias
        assert prefix == "shared"

    def test_malformed_json(self, tmp_path: Path) -> None:
        (tmp_path / "tsconfig.json").write_text("{invalid json")
        aliases = _load_tsconfig_aliases(tmp_path)
        assert aliases == []

    def test_no_compiler_options(self, tmp_path: Path) -> None:
        (tmp_path / "tsconfig.json").write_text("{}")
        aliases = _load_tsconfig_aliases(tmp_path)
        assert aliases == []

    def test_extends_depth_limit(self, tmp_path: Path) -> None:
        """Circular or deep extends chains don't cause infinite loops."""
        a = {"extends": "./b.json"}
        b = {"extends": "./a.json", "compilerOptions": {"paths": {"@/*": ["./src/*"]}, "baseUrl": "."}}
        (tmp_path / "a.json").write_text(json.dumps(a))
        (tmp_path / "b.json").write_text(json.dumps(b))
        (tmp_path / "tsconfig.json").write_text(json.dumps({"extends": "./a.json"}))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert isinstance(aliases, list)

    def test_extends_without_json_suffix(self, tmp_path: Path) -> None:
        """Extends path without .json extension gets .json appended."""
        base = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"~/*": ["./lib/*"]},
            }
        }
        child = {"extends": "./base"}  # no .json suffix
        (tmp_path / "base.json").write_text(json.dumps(base))
        (tmp_path / "tsconfig.json").write_text(json.dumps(child))
        (tmp_path / "lib").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "~/"

    def test_empty_targets_array(self, tmp_path: Path) -> None:
        """Path with empty targets array is skipped."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {
                    "@/*": [],  # empty targets
                    "~/*": ["./src/*"],
                },
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()

        aliases = _load_tsconfig_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "~/"


class TestLoadViteAliases:
    """Tests for vite.config.* alias loading."""

    def test_resolve_style(self, tmp_path: Path) -> None:
        """Parses resolve('./path') style aliases from Vite config."""
        config = """import { resolve } from 'path';
export default defineConfig({
  resolve: {
    alias: {
      dashboard: resolve('./app/javascript/dashboard'),
      shared: resolve('./app/javascript/shared'),
    },
  },
});
"""
        (tmp_path / "vite.config.js").write_text(config)

        aliases = _load_vite_aliases(tmp_path)
        assert len(aliases) == 2
        names = {a[0] for a in aliases}
        assert "dashboard" in names
        assert "shared" in names

    def test_path_resolve_style(self, tmp_path: Path) -> None:
        """Parses path.resolve(__dirname, 'path') style."""
        config = """
const path = require('path');
module.exports = {
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
};
"""
        (tmp_path / "vite.config.js").write_text(config)

        aliases = _load_vite_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "@"
        assert aliases[0][1] == tmp_path / "src"

    def test_no_vite_config(self, tmp_path: Path) -> None:
        aliases = _load_vite_aliases(tmp_path)
        assert aliases == []

    def test_vite_config_ts(self, tmp_path: Path) -> None:
        """Finds vite.config.ts (TypeScript variant)."""
        config = """
export default defineConfig({
  resolve: {
    alias: {
      utils: resolve('./src/utils'),
    },
  },
});
"""
        (tmp_path / "vite.config.ts").write_text(config)

        aliases = _load_vite_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "utils"

    def test_quoted_keys(self, tmp_path: Path) -> None:
        config = """export default {
  resolve: {
    alias: {
      'dashboard': resolve('./app/dashboard'),
    },
  },
};
"""
        (tmp_path / "vite.config.js").write_text(config)

        aliases = _load_vite_aliases(tmp_path)
        assert len(aliases) == 1
        assert aliases[0][0] == "dashboard"

    def test_no_alias_section(self, tmp_path: Path) -> None:
        """Vite config without resolve.alias returns empty."""
        config = "export default defineConfig({});\n"
        (tmp_path / "vite.config.js").write_text(config)

        aliases = _load_vite_aliases(tmp_path)
        assert aliases == []

    def test_unreadable_config(self, tmp_path: Path) -> None:
        """Returns empty list when vite config can't be read."""
        from unittest.mock import patch

        (tmp_path / "vite.config.js").write_text("content")

        with patch("pathlib.Path.read_text", side_effect=OSError("perm denied")):
            aliases = _load_vite_aliases(tmp_path)
        assert aliases == []


class TestResolveAlias:
    """Tests for alias path expansion."""

    def test_wildcard_match(self, tmp_path: Path) -> None:
        """@/components/Button resolves via @/ wildcard alias."""
        (tmp_path / "src" / "components").mkdir(parents=True)
        (tmp_path / "src" / "components" / "Button.tsx").write_text("")

        aliases = [("@/", tmp_path / "src")]
        result = _resolve_alias("@/components/Button", aliases)
        assert result is not None
        assert result.name == "Button.tsx"

    def test_exact_match_directory(self, tmp_path: Path) -> None:
        """Exact alias 'dashboard' resolves to directory index."""
        (tmp_path / "app" / "dashboard").mkdir(parents=True)
        (tmp_path / "app" / "dashboard" / "index.js").write_text("")

        aliases = [("dashboard", tmp_path / "app" / "dashboard")]
        result = _resolve_alias("dashboard", aliases)
        assert result is not None
        assert result.name == "index.js"

    def test_exact_with_subpath(self, tmp_path: Path) -> None:
        """'dashboard/Header' resolves via exact alias + subpath."""
        (tmp_path / "app" / "dashboard").mkdir(parents=True)
        (tmp_path / "app" / "dashboard" / "Header.vue").write_text("")

        aliases = [("dashboard", tmp_path / "app" / "dashboard")]
        result = _resolve_alias("dashboard/Header", aliases)
        assert result is not None
        assert result.name == "Header.vue"

    def test_no_match(self, tmp_path: Path) -> None:
        aliases = [("@/", tmp_path / "src")]
        result = _resolve_alias("lodash", aliases)
        assert result is None

    def test_alias_file_not_found(self, tmp_path: Path) -> None:
        """Matched alias but no file on disk returns None."""
        (tmp_path / "src").mkdir()
        aliases = [("@/", tmp_path / "src")]
        result = _resolve_alias("@/nonexistent", aliases)
        assert result is None

    def test_empty_aliases(self) -> None:
        result = _resolve_alias("@/foo", [])
        assert result is None

    def test_longest_prefix_wins(self, tmp_path: Path) -> None:
        """More specific alias takes priority (sorted by length)."""
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "Button.tsx").write_text("specific")
        (tmp_path / "src" / "components").mkdir(parents=True)
        (tmp_path / "src" / "components" / "Button.tsx").write_text("general")

        # Longer prefix first → @components/ matches before @/
        aliases = [
            ("@components/", tmp_path / "components"),
            ("@/", tmp_path / "src"),
        ]
        result = _resolve_alias("@components/Button", aliases)
        assert result is not None
        # Should resolve to the specific components dir, not src/components
        assert str(result) == str(tmp_path / "components" / "Button.tsx")


class TestAliasIntegration:
    """Tests for alias resolution integrated with link_js_modules."""

    def _make_file_symbol(
        self, path: str, lang: str = "javascript"
    ) -> Symbol:
        name = Path(path).name
        return Symbol(
            id=f"{lang}:{path}:1-1:{name}:file",
            name=name,
            kind="file",
            language=lang,
            path=path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def _make_import_edge(
        self, src_id: str, module_path: str, lang: str = "javascript"
    ) -> Edge:
        return Edge.create(
            src=src_id,
            dst=f"{lang}:{module_path}:0-0:module:module",
            edge_type="imports",
            line=1,
            origin="js-ts-v1",
            origin_run_id="test-run",
            evidence_type="import_static",
            confidence=0.95,
        )

    def _make_function_symbol(
        self, path: str, name: str, lang: str = "javascript"
    ) -> Symbol:
        return Symbol(
            id=f"{lang}:{path}:5-10:{name}:function",
            name=name,
            kind="function",
            language=lang,
            path=path,
            span=Span(start_line=5, end_line=10, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def test_tsconfig_alias_resolves(self, tmp_path: Path) -> None:
        """Import '@/utils' resolves via tsconfig @/* alias."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("")
        (tmp_path / "src" / "utils.ts").write_text("")

        app_path = str(tmp_path / "src" / "app.ts")
        utils_path = str(tmp_path / "src" / "utils.ts")

        file_sym = self._make_file_symbol(app_path, "typescript")
        import_edge = self._make_import_edge(file_sym.id, "@/utils", "typescript")
        helper_fn = self._make_function_symbol(utils_path, "helper", "typescript")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym, helper_fn],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert "utils.ts" in module_files[0].path

        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1

        exports_edges = [e for e in result.edges if e.edge_type == "module_exports"]
        assert len(exports_edges) == 1

    def test_vite_alias_resolves(self, tmp_path: Path) -> None:
        """Import 'dashboard/Header' resolves via Vite alias."""
        config = """export default defineConfig({
  resolve: {
    alias: {
      dashboard: resolve('./app/javascript/dashboard'),
    },
  },
});
"""
        (tmp_path / "vite.config.js").write_text(config)
        js_dir = tmp_path / "app" / "javascript"
        (js_dir / "dashboard").mkdir(parents=True)
        (js_dir / "dashboard" / "Header.vue").write_text("")
        (js_dir / "src").mkdir(parents=True)
        (js_dir / "src" / "App.js").write_text("")

        app_path = str(js_dir / "src" / "App.js")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "dashboard/Header")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert "Header.vue" in module_files[0].path

        # Should NOT create npm_package for 'dashboard'
        npm_packages = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm_packages) == 0

    def test_unresolved_alias_falls_to_npm(self, tmp_path: Path) -> None:
        """Import with no matching alias still creates npm_package."""
        app_path = str(tmp_path / "app.js")
        (tmp_path / "app.js").write_text("")
        file_sym = self._make_file_symbol(app_path)
        import_edge = self._make_import_edge(file_sym.id, "lodash")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym],
            edges=[import_edge],
        )

        npm_packages = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm_packages) == 1
        assert npm_packages[0].name == "lodash"

    def test_alias_evidence_type(self, tmp_path: Path) -> None:
        """Alias-resolved imports use 'alias_resolution' evidence type."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("")
        (tmp_path / "src" / "utils.ts").write_text("")

        app_path = str(tmp_path / "src" / "app.ts")
        file_sym = self._make_file_symbol(app_path, "typescript")
        import_edge = self._make_import_edge(file_sym.id, "@/utils", "typescript")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym],
            edges=[import_edge],
        )

        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1
        assert imports_module[0].evidence_type == "alias_resolution"


class TestParseTsconfigPaths:
    """Tests for _parse_tsconfig_paths (config file → aliases)."""

    def test_basic_config(self, tmp_path: Path) -> None:
        """Parses a simple tsconfig.json with paths."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        config = tmp_path / "tsconfig.json"
        config.write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()

        aliases = _parse_tsconfig_paths(config)
        assert len(aliases) == 1
        assert aliases[0][0] == "@/"
        assert aliases[0][1] == tmp_path / "src"

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Nonexistent config returns empty list."""
        aliases = _parse_tsconfig_paths(tmp_path / "nope.json")
        assert aliases == []


class TestBuildTsconfigAliasIndex:
    """Tests for recursive tsconfig discovery in monorepos."""

    def test_root_tsconfig(self, tmp_path: Path) -> None:
        """Root tsconfig.json is included in index."""
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }
        (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_path / "src").mkdir()

        index = _build_tsconfig_alias_index(tmp_path)
        assert str(tmp_path) in index
        assert len(index[str(tmp_path)]) == 1

    def test_subdir_tsconfig(self, tmp_path: Path) -> None:
        """tsconfig.json in subdirectory is discovered."""
        frontend = tmp_path / "packages" / "frontend"
        frontend.mkdir(parents=True)
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./*"]},
            }
        }
        (frontend / "tsconfig.json").write_text(json.dumps(tsconfig))

        index = _build_tsconfig_alias_index(tmp_path)
        assert str(frontend) in index
        aliases = index[str(frontend)]
        assert len(aliases) == 1
        assert aliases[0][0] == "@/"
        assert aliases[0][1] == frontend

    def test_multiple_subdirs(self, tmp_path: Path) -> None:
        """Multiple subdirectory tsconfigs are all discovered."""
        for pkg in ("frontend", "backend"):
            pkg_dir = tmp_path / "packages" / pkg
            pkg_dir.mkdir(parents=True)
            tsconfig = {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@/*": ["./*"]},
                }
            }
            (pkg_dir / "tsconfig.json").write_text(json.dumps(tsconfig))

        index = _build_tsconfig_alias_index(tmp_path)
        assert len(index) == 2
        assert str(tmp_path / "packages" / "frontend") in index
        assert str(tmp_path / "packages" / "backend") in index

    def test_node_modules_excluded(self, tmp_path: Path) -> None:
        """tsconfig.json inside node_modules is ignored."""
        nm = tmp_path / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        tsconfig = {
            "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}},
        }
        (nm / "tsconfig.json").write_text(json.dumps(tsconfig))

        index = _build_tsconfig_alias_index(tmp_path)
        assert str(nm) not in index
        assert len(index) == 0

    def test_no_paths_skipped(self, tmp_path: Path) -> None:
        """tsconfig without paths is not included in index."""
        (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {}}))

        index = _build_tsconfig_alias_index(tmp_path)
        assert len(index) == 0

    def test_jsconfig_discovered(self, tmp_path: Path) -> None:
        """jsconfig.json in subdirectory is also found."""
        frontend = tmp_path / "packages" / "frontend"
        frontend.mkdir(parents=True)
        (frontend / "jsconfig.json").write_text(json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["./*"]}},
        }))

        index = _build_tsconfig_alias_index(tmp_path)
        assert str(frontend) in index

    def test_duplicate_configs_first_wins(self, tmp_path: Path) -> None:
        """When both tsconfig.json and jsconfig.json exist, tsconfig wins."""
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}},
        }))
        (tmp_path / "jsconfig.json").write_text(json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["./lib/*"]}},
        }))
        (tmp_path / "src").mkdir()

        index = _build_tsconfig_alias_index(tmp_path)
        assert str(tmp_path) in index
        # tsconfig.json found first, so @/ alias should be present
        prefixes = {a[0] for a in index[str(tmp_path)]}
        assert "@/" in prefixes


class TestGetAliasesForFile:
    """Tests for finding nearest ancestor tsconfig aliases."""

    def test_exact_dir_match(self, tmp_path: Path) -> None:
        """Source file in indexed directory gets those aliases."""
        frontend = tmp_path / "packages" / "frontend"
        expected = [("@/", frontend)]
        index = {str(frontend): expected}

        result = _get_aliases_for_file(
            str(frontend / "index.ts"), index, tmp_path
        )
        assert result == expected

    def test_ancestor_dir_match(self, tmp_path: Path) -> None:
        """Source file nested under indexed dir gets ancestor aliases."""
        frontend = tmp_path / "packages" / "frontend"
        expected = [("@/", frontend)]
        index = {str(frontend): expected}

        result = _get_aliases_for_file(
            str(frontend / "pages" / "features" / "index.tsx"),
            index,
            tmp_path,
        )
        assert result == expected

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        """Source file with no ancestor tsconfig gets empty list."""
        index: dict[str, list[tuple[str, Path]]] = {}

        result = _get_aliases_for_file(
            str(tmp_path / "src" / "app.ts"), index, tmp_path
        )
        assert result == []

    def test_nearest_wins(self, tmp_path: Path) -> None:
        """Most specific (nearest) tsconfig takes priority."""
        root_aliases = [("~/*", tmp_path)]
        child_aliases = [("@/", tmp_path / "packages" / "fe")]
        index = {
            str(tmp_path): root_aliases,
            str(tmp_path / "packages" / "fe"): child_aliases,
        }

        result = _get_aliases_for_file(
            str(tmp_path / "packages" / "fe" / "src" / "app.ts"),
            index,
            tmp_path,
        )
        assert result == child_aliases


class TestMonorepoIntegration:
    """Integration tests for monorepo alias resolution in link_js_modules."""

    def _make_file_symbol(
        self, path: str, lang: str = "typescript"
    ) -> Symbol:
        name = Path(path).name
        return Symbol(
            id=f"{lang}:{path}:1-1:{name}:file",
            name=name,
            kind="file",
            language=lang,
            path=path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def _make_import_edge(
        self, src_id: str, module_path: str, lang: str = "typescript"
    ) -> Edge:
        return Edge.create(
            src=src_id,
            dst=f"{lang}:{module_path}:0-0:module:module",
            edge_type="imports",
            line=1,
            origin="js-ts-v1",
            origin_run_id="test-run",
            evidence_type="import_static",
            confidence=0.95,
        )

    def _make_function_symbol(
        self, path: str, name: str, lang: str = "typescript"
    ) -> Symbol:
        return Symbol(
            id=f"{lang}:{path}:5-10:{name}:function",
            name=name,
            kind="function",
            language=lang,
            path=path,
            span=Span(start_line=5, end_line=10, start_col=0, end_col=0),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

    def test_monorepo_subdirectory_tsconfig(self, tmp_path: Path) -> None:
        """Import @/components/Button resolves via packages/frontend tsconfig."""
        # Setup monorepo structure
        frontend = tmp_path / "packages" / "frontend"
        (frontend / "components").mkdir(parents=True)
        (frontend / "components" / "Button.tsx").write_text("")
        (frontend / "pages").mkdir()
        (frontend / "pages" / "index.tsx").write_text("")

        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./*"]},
            }
        }
        (frontend / "tsconfig.json").write_text(json.dumps(tsconfig))

        src_path = str(frontend / "pages" / "index.tsx")
        btn_path = str(frontend / "components" / "Button.tsx")

        file_sym = self._make_file_symbol(src_path)
        import_edge = self._make_import_edge(file_sym.id, "@/components/Button")
        btn_fn = self._make_function_symbol(btn_path, "Button")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[file_sym, btn_fn],
            edges=[import_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 1
        assert "Button.tsx" in module_files[0].path

        # Should NOT be treated as npm_package
        npm = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm) == 0

        imports_module = [e for e in result.edges if e.edge_type == "imports_module"]
        assert len(imports_module) == 1
        assert imports_module[0].evidence_type == "alias_resolution"

        exports = [e for e in result.edges if e.edge_type == "module_exports"]
        assert len(exports) == 1

    def test_monorepo_two_packages_different_aliases(self, tmp_path: Path) -> None:
        """Two packages with different tsconfigs resolve independently."""
        # frontend: @/* -> ./*
        frontend = tmp_path / "packages" / "frontend"
        (frontend / "src").mkdir(parents=True)
        (frontend / "src" / "utils.ts").write_text("")
        (frontend / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}},
        }))

        # backend: ~/* -> ./src/*
        backend = tmp_path / "packages" / "backend"
        (backend / "src").mkdir(parents=True)
        (backend / "src" / "db.ts").write_text("")
        (backend / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["./src/*"]}},
        }))

        # Frontend import: @/src/utils
        fe_path = str(frontend / "app.ts")
        (frontend / "app.ts").write_text("")
        fe_sym = self._make_file_symbol(fe_path)
        fe_edge = self._make_import_edge(fe_sym.id, "@/src/utils")

        # Backend import: ~/db
        be_path = str(backend / "index.ts")
        (backend / "index.ts").write_text("")
        be_sym = self._make_file_symbol(be_path)
        be_edge = self._make_import_edge(be_sym.id, "~/db")

        result = link_js_modules(
            repo_root=tmp_path,
            symbols=[fe_sym, be_sym],
            edges=[fe_edge, be_edge],
        )

        module_files = [s for s in result.symbols if s.kind == "module_file"]
        assert len(module_files) == 2
        paths = {m.path for m in module_files}
        assert any("utils.ts" in p for p in paths)
        assert any("db.ts" in p for p in paths)

        npm = [s for s in result.symbols if s.kind == "npm_package"]
        assert len(npm) == 0
