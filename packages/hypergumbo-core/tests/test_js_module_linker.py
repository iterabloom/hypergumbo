"""Tests for JS/TS module resolution linker.

The JS module linker resolves unresolved import edges created by the JS/TS
analyzer. Import edges have dst format like 'javascript:./utils:0-0:module:module'
(raw import path embedded in synthetic ID). The linker resolves relative paths
to actual files, creates module_file symbols, and creates module_exports edges
from module_file to the functions/methods/classes defined in the target file.

This enables cross-file graph traversal through imports:
  file_A --imports_module--> module_file_B --module_exports--> functionInB
"""

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.js_module import (
    _extract_import_path,
    _extract_path_from_id,
    _probe_file,
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
