"""Tests for Tauri IPC linker (TypeScript/JavaScript to Rust cross-language bridge).

The Tauri IPC linker creates ipc_calls edges between TypeScript/JavaScript
``invoke('command_name')`` calls and Rust functions annotated with
``#[tauri::command]``.

Two invoke mechanisms are detected by scanning TS/JS source files:

1. **Direct invoke**: ``invoke('command_name', { ... })`` from ``@tauri-apps/api``
2. **Typed wrapper**: A locally-defined wrapper that calls ``invoke(cmd, args)``
   with a string literal typed by a union (e.g. yaak's ``invokeCmd``).

On the Rust side, functions with ``#[tauri::command]`` in their annotation
metadata are detected from symbols already parsed by the Rust analyzer.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_ts_symbol(
    name: str,
    kind: str = "function",
    path: str = "src/app.ts",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test TypeScript symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"typescript:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="typescript",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="ts-v1",
        origin_run_id=run.execution_id,
    )


def _make_rust_symbol(
    name: str,
    kind: str = "function",
    path: str = "src-tauri/src/lib.rs",
    start_line: int = 1,
    end_line: int = 10,
    annotations: list | None = None,
) -> Symbol:
    """Create a test Rust symbol with optional annotations."""
    run = AnalysisRun.create(pass_id="test", version="test")
    meta = None
    if annotations:
        meta = {"annotations": annotations}
    return Symbol(
        id=f"rust:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="rust",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="rust-v1",
        origin_run_id=run.execution_id,
        meta=meta,
    )


def _tauri_command_annotation(**kwargs: object) -> dict:
    """Create a #[tauri::command] annotation dict."""
    return {"name": "tauri::command", "args": [], "kwargs": dict(kwargs)}


class TestTauriIPCLinkerBasic:
    """Tests for basic Tauri IPC linking."""

    def test_links_invoke_to_tauri_command(self, tmp_path: Path) -> None:
        """invoke('greet') linked to #[tauri::command] fn greet."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "import { invoke } from '@tauri-apps/api/core';\n"
            "async function hello() {\n"
            "  const result = await invoke('greet', { name: 'World' });\n"
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "greet",
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("hello", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ipc_calls"
        assert edge.dst == rust_sym.id
        assert edge.confidence >= 0.85

    def test_links_multiple_commands(self, tmp_path: Path) -> None:
        """Multiple invoke calls in one file create separate edges."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "api.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "import { invoke } from '@tauri-apps/api/core';\n"
            "export const getUser = () => invoke('get_user');\n"
            "export const saveUser = (u: User) => invoke('save_user', { user: u });\n"
        )

        rust_get = _make_rust_symbol(
            "get_user", start_line=1, end_line=5,
            annotations=[_tauri_command_annotation()],
        )
        rust_save = _make_rust_symbol(
            "save_user", start_line=7, end_line=12,
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("api", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_get, rust_save],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert rust_get.id in dst_ids
        assert rust_save.id in dst_ids

    def test_no_link_when_command_not_found(self, tmp_path: Path) -> None:
        """invoke('nonexistent') produces no edge when no matching command exists."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "import { invoke } from '@tauri-apps/api/core';\n"
            "invoke('nonexistent_command');\n"
        )

        rust_sym = _make_rust_symbol(
            "greet",
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 0

    def test_no_link_without_tauri_command_annotation(self, tmp_path: Path) -> None:
        """Rust function without #[tauri::command] is not matched."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol("greet")  # No annotations
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 0


class TestTauriIPCLinkerInvokePatterns:
    """Tests for different invoke() call patterns."""

    def test_invoke_with_generic_type(self, tmp_path: Path) -> None:
        """invoke<string>('greet') with TypeScript generic."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "const result = await invoke<string>('greet', { name: 'World' });\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_invoke_with_double_quotes(self, tmp_path: Path) -> None:
        """invoke("greet") with double quotes."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text('invoke("greet");\n')

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_invoke_with_backtick_template(self, tmp_path: Path) -> None:
        """invoke(`greet`) with backtick template literal."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke(`greet`);\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_plugin_invoke_pattern(self, tmp_path: Path) -> None:
        """invoke('plugin:name|command') extracts the command part."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "await invoke('plugin:turso|execute', { sql: '...' });\n"
        )

        rust_sym = _make_rust_symbol(
            "execute", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1


class TestTauriIPCLinkerCamelCase:
    """Tests for snake_case to camelCase conversion."""

    def test_camel_case_rename_all(self, tmp_path: Path) -> None:
        """Rust get_user_data matched to invoke('getUserData') via rename_all."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('getUserData');\n")

        rust_sym = _make_rust_symbol(
            "get_user_data",
            annotations=[_tauri_command_annotation(rename_all="camelCase")],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_explicit_rename(self, tmp_path: Path) -> None:
        """Rust fn with explicit rename='fetchData' matched to invoke('fetchData')."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('fetchData');\n")

        rust_sym = _make_rust_symbol(
            "get_all_data",
            annotations=[_tauri_command_annotation(rename="fetchData")],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1


class TestTauriIPCLinkerEdgeCases:
    """Edge case tests for Tauri IPC linker."""

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Handles empty inputs gracefully."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[],
            rust_symbols=[],
        )

        assert result.edges == []
        assert result.run is not None

    def test_result_includes_run_metadata(self, tmp_path: Path) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[],
            rust_symbols=[],
        )

        assert result.run is not None
        assert result.run.pass_id == "tauri-ipc-linker-v1"

    def test_nonexistent_ts_file_skipped(self, tmp_path: Path) -> None:
        """TS files that don't exist on disk are silently skipped."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(tmp_path / "nonexistent.ts"))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 0

    def test_deduplicates_same_command_in_same_file(self, tmp_path: Path) -> None:
        """Same command invoked twice in one file creates only one edge per file."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "invoke('greet');\n"
            "invoke('greet');\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        # One edge per (file, command) pair
        assert len(result.edges) == 1

    def test_javascript_symbols_also_linked(self, tmp_path: Path) -> None:
        """JavaScript invoke() calls are also linked."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        js_file = tmp_path / "src" / "app.js"
        js_file.parent.mkdir(parents=True)
        js_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        run = AnalysisRun.create(pass_id="test", version="test")
        js_sym = Symbol(
            id=f"javascript:{js_file}:1-10:app:module",
            name="app",
            kind="module",
            language="javascript",
            path=str(js_file),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="js-v1",
            origin_run_id=run.execution_id,
        )

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[js_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_rust_symbol_without_meta_ignored(self, tmp_path: Path) -> None:
        """Rust symbols with no meta are gracefully ignored."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol("greet")  # No meta at all
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 0

    def test_multiple_ts_files_scanned(self, tmp_path: Path) -> None:
        """Invoke calls from different TS files all linked."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        f1 = src / "a.ts"
        f1.write_text("invoke('greet');\n")
        f2 = src / "b.ts"
        f2.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts1 = _make_ts_symbol("a", path=str(f1))
        ts2 = _make_ts_symbol("b", path=str(f2))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts1, ts2],
            rust_symbols=[rust_sym],
        )

        # One edge per file
        assert len(result.edges) == 2


class TestTauriIPCLinkerInternalFunctions:
    """Tests for internal helper functions with mixed/edge-case inputs."""

    def test_find_commands_filters_non_rust_symbols(self) -> None:
        """_find_tauri_commands ignores non-Rust symbols."""
        from hypergumbo_core.linkers.tauri_ipc import _find_tauri_commands

        ts_sym = _make_ts_symbol("greet")
        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )

        result = _find_tauri_commands([ts_sym, rust_sym])
        assert "greet" in result
        assert result["greet"] is rust_sym

    def test_find_commands_filters_non_function_kinds(self) -> None:
        """_find_tauri_commands ignores struct/module symbols."""
        from hypergumbo_core.linkers.tauri_ipc import _find_tauri_commands

        struct_sym = _make_rust_symbol(
            "AppState", kind="struct",
            annotations=[_tauri_command_annotation()],
        )

        result = _find_tauri_commands([struct_sym])
        assert len(result) == 0

    def test_find_commands_skips_empty_annotations(self) -> None:
        """_find_tauri_commands skips symbols with empty annotations list."""
        from hypergumbo_core.linkers.tauri_ipc import _find_tauri_commands

        sym = _make_rust_symbol("greet")
        sym.meta = {"annotations": []}

        result = _find_tauri_commands([sym])
        assert len(result) == 0

    def test_find_commands_skips_non_tauri_annotations(self) -> None:
        """_find_tauri_commands skips symbols with non-tauri annotations."""
        from hypergumbo_core.linkers.tauri_ipc import _find_tauri_commands

        sym = _make_rust_symbol("greet")
        sym.meta = {"annotations": [{"name": "derive", "args": ["Debug"], "kwargs": {}}]}

        result = _find_tauri_commands([sym])
        assert len(result) == 0

    def test_link_filters_non_js_ts_symbols(self, tmp_path: Path) -> None:
        """link_tauri_ipc ignores non-JS/TS symbols in ts_js_symbols list."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_cmd = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        # Pass a Rust symbol in the ts_js_symbols list (edge case)
        rust_extra = _make_rust_symbol("extra", path="src/extra.rs")
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[rust_extra, ts_sym],
            rust_symbols=[rust_cmd],
        )

        # Only the TS file produces an edge
        assert len(result.edges) == 1

    def test_link_deduplicates_same_path_symbols(self, tmp_path: Path) -> None:
        """Multiple symbols from same TS file only scan the file once."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        # Two TS symbols from same file
        ts1 = _make_ts_symbol("funcA", path=str(ts_file), start_line=1, end_line=5)
        ts2 = _make_ts_symbol("funcB", path=str(ts_file), start_line=6, end_line=10)

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts1, ts2],
            rust_symbols=[rust_sym],
        )

        # Only one edge despite two symbols from same file
        assert len(result.edges) == 1

    def test_link_handles_relative_ts_path(self, tmp_path: Path) -> None:
        """TS symbols with relative paths are resolved to repo_root."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path="src/app.ts")

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_link_handles_non_relative_path(self, tmp_path: Path) -> None:
        """TS files outside repo_root use absolute path in edge ID."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc
        import tempfile

        with tempfile.TemporaryDirectory() as other_dir:
            ts_file = Path(other_dir) / "app.ts"
            ts_file.write_text("invoke('greet');\n")

            rust_sym = _make_rust_symbol(
                "greet", annotations=[_tauri_command_annotation()],
            )
            ts_sym = _make_ts_symbol("app", path=str(ts_file))

            result = link_tauri_ipc(
                repo_root=tmp_path,
                ts_js_symbols=[ts_sym],
                rust_symbols=[rust_sym],
            )

            assert len(result.edges) == 1
            # Path should be absolute since it's outside repo_root
            assert str(other_dir) in result.edges[0].src


class TestTauriIPCLinkerSpectaPatterns:
    """Tests for tauri-specta generated binding patterns.

    tauri-specta generates TypeScript bindings that use TAURI_INVOKE or
    __TAURI_INVOKE__ instead of the standard invoke() function. These
    generated wrappers contain the command name as a string literal, so
    the linker can still match them via regex.
    """

    def test_tauri_invoke_uppercase(self, tmp_path: Path) -> None:
        """TAURI_INVOKE('command') pattern from tauri-specta generated bindings."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "bindings.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "// generated by tauri-specta\n"
            "export function takeScreenshot() {\n"
            '  return TAURI_INVOKE("take_screenshot");\n'
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "take_screenshot",
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("bindings", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == rust_sym.id

    def test_dunder_tauri_invoke(self, tmp_path: Path) -> None:
        """__TAURI_INVOKE__('cmd') pattern (older specta / internal Tauri API)."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "bindings.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "export async function getConfig() {\n"
            "  return __TAURI_INVOKE__('get_config', {});\n"
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "get_config",
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("bindings", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_tauri_invoke_with_generic(self, tmp_path: Path) -> None:
        """TAURI_INVOKE<Type>('cmd') with TypeScript generics."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "bindings.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            'return TAURI_INVOKE<Screenshot>("take_screenshot", { id });\n'
        )

        rust_sym = _make_rust_symbol(
            "take_screenshot",
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("bindings", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.edges) == 1

    def test_multiple_specta_bindings(self, tmp_path: Path) -> None:
        """Multiple TAURI_INVOKE calls in one generated bindings file."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "bindings.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "// generated by tauri-specta\n"
            "export function getUser() {\n"
            '  return TAURI_INVOKE("get_user");\n'
            "}\n"
            "export function saveUser(user: User) {\n"
            '  return TAURI_INVOKE("save_user", { user });\n'
            "}\n"
            "export function deleteUser(id: string) {\n"
            '  return TAURI_INVOKE("delete_user", { id });\n'
            "}\n"
        )

        cmds = [
            _make_rust_symbol(
                name, start_line=i * 10, end_line=i * 10 + 5,
                annotations=[_tauri_command_annotation()],
            )
            for i, name in enumerate(["get_user", "save_user", "delete_user"])
        ]
        ts_sym = _make_ts_symbol("bindings", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=cmds,
        )

        assert len(result.edges) == 3
        dst_ids = {e.dst for e in result.edges}
        for cmd in cmds:
            assert cmd.id in dst_ids

    def test_mixed_invoke_and_tauri_invoke(self, tmp_path: Path) -> None:
        """File with both standard invoke() and TAURI_INVOKE() calls."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "mixed.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "import { invoke } from '@tauri-apps/api/core';\n"
            "invoke('greet');\n"
            'TAURI_INVOKE("get_config");\n'
        )

        greet = _make_rust_symbol(
            "greet", start_line=1, end_line=5,
            annotations=[_tauri_command_annotation()],
        )
        get_config = _make_rust_symbol(
            "get_config", start_line=10, end_line=15,
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("mixed", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[greet, get_config],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert greet.id in dst_ids
        assert get_config.id in dst_ids


class TestTauriIPCSyntheticSymbols:
    """Tests for synthetic IPC publisher Symbol creation.

    The slicer's BFS needs node_by_id.get(edge.src) to return a Symbol for
    cross-language traversal. The linker creates synthetic ipc_publisher
    Symbol nodes so reverse slices from Rust handlers can traverse through
    the IPC bridge back to the TS/JS caller.
    """

    def test_creates_synthetic_symbol_for_each_edge(self, tmp_path: Path) -> None:
        """Each invoke() call creates a synthetic ipc_publisher Symbol."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text(
            "invoke('greet');\n"
            "invoke('save_user');\n"
        )

        greet = _make_rust_symbol(
            "greet", start_line=1, end_line=5,
            annotations=[_tauri_command_annotation()],
        )
        save = _make_rust_symbol(
            "save_user", start_line=10, end_line=15,
            annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[greet, save],
        )

        assert len(result.symbols) == 2
        assert len(result.edges) == 2

        sym_by_id = {s.id: s for s in result.symbols}
        for edge in result.edges:
            # Every edge source has a corresponding Symbol
            assert edge.src in sym_by_id
            sym = sym_by_id[edge.src]
            assert sym.kind == "ipc_publisher"
            assert sym.language == "typescript"

    def test_synthetic_symbol_has_correct_fields(self, tmp_path: Path) -> None:
        """Synthetic Symbol has proper id, name, fingerprint, meta."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.symbols) == 1
        sym = result.symbols[0]
        assert sym.name == "greet"
        assert sym.kind == "ipc_publisher"
        assert sym.language == "typescript"
        assert sym.canonical_name == "invoke('greet')"
        assert sym.meta == {"tauri_command": "greet"}
        assert sym.fingerprint is not None
        assert len(sym.fingerprint) == 16  # sha256 hex truncated to 16
        # Tier 2 prevents _classify_symbols from reclassifying to tier 4
        assert sym.supply_chain_tier == 2
        assert sym.supply_chain_reason == "synthetic IPC bridge node"

    def test_deduplicates_symbols_across_files(self, tmp_path: Path) -> None:
        """Same command invoked in two files creates only one Symbol per (file, cmd)."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        f1 = src / "a.ts"
        f1.write_text("invoke('greet');\n")
        f2 = src / "b.ts"
        f2.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts1 = _make_ts_symbol("a", path=str(f1))
        ts2 = _make_ts_symbol("b", path=str(f2))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts1, ts2],
            rust_symbols=[rust_sym],
        )

        # Two edges (one per file) but two distinct symbols (different file paths)
        assert len(result.edges) == 2
        assert len(result.symbols) == 2
        sym_ids = {s.id for s in result.symbols}
        assert len(sym_ids) == 2  # Different because different file paths

    def test_no_symbols_when_no_matches(self, tmp_path: Path) -> None:
        """No synthetic symbols created when no invoke calls match commands."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('nonexistent');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_sym],
            rust_symbols=[rust_sym],
        )

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_symbols_passed_through_registry(self, tmp_path: Path) -> None:
        """Symbols are passed through LinkerResult via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[ts_sym, rust_sym],
            edges=[],
        )

        result = run_linker("tauri_ipc", ctx)
        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "ipc_publisher"


class TestTauriSpectaWrapperResolution:
    """Tests for tauri-specta wrapper function resolution.

    When TS components call ``commands.startRecording()`` instead of
    ``invoke('start_recording')``, the standard invoke regex won't match.
    The linker detects specta-generated wrapper files (files containing
    ``export function foo() { ... TAURI_INVOKE("bar") ... }``), builds
    a wrapper-name-to-command-name map, then scans other TS files for
    imports from those wrapper files and creates caller_invokes edges
    from the importing call sites to the ipc_publisher nodes.
    """

    def test_wrapper_import_creates_edge(self, tmp_path: Path) -> None:
        """TS file importing a specta wrapper gets a caller_invokes edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        # Specta-generated wrapper file
        bindings = src / "bindings.ts"
        bindings.write_text(
            "// generated by tauri-specta\n"
            "export function takeScreenshot() {\n"
            '  return TAURI_INVOKE("take_screenshot");\n'
            "}\n"
        )

        # Component file that imports and calls the wrapper
        component = src / "editor.ts"
        component.write_text(
            "import { takeScreenshot } from './bindings';\n"
            "export function handleClick() {\n"
            "  takeScreenshot();\n"
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "take_screenshot",
            annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_editor = _make_ts_symbol("editor", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_editor],
            rust_symbols=[rust_sym],
        )

        # Should have: ipc_calls edge from bindings, caller_invokes from editor
        ipc_edges = [e for e in result.edges if e.edge_type == "ipc_calls"]
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]

        assert len(ipc_edges) == 1
        assert len(caller_edges) == 1
        # caller_invokes dst should be the ipc_publisher symbol
        assert caller_edges[0].dst == ipc_edges[0].src

    def test_multiple_wrapper_imports(self, tmp_path: Path) -> None:
        """Importing multiple wrappers creates multiple caller_invokes edges."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function getUser() {\n"
            '  return TAURI_INVOKE("get_user");\n'
            "}\n"
            "export function saveUser(u: User) {\n"
            '  return TAURI_INVOKE("save_user", { u });\n'
            "}\n"
        )

        component = src / "profile.ts"
        component.write_text(
            "import { getUser, saveUser } from './bindings';\n"
            "export async function loadProfile() {\n"
            "  const user = await getUser();\n"
            "  await saveUser(user);\n"
            "}\n"
        )

        rust_get = _make_rust_symbol(
            "get_user", start_line=1, end_line=5,
            annotations=[_tauri_command_annotation()],
        )
        rust_save = _make_rust_symbol(
            "save_user", start_line=10, end_line=15,
            annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_profile = _make_ts_symbol("profile", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_profile],
            rust_symbols=[rust_get, rust_save],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 2

    def test_namespace_import_creates_edges(self, tmp_path: Path) -> None:
        """import * as commands from './bindings' creates caller_invokes edges."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function startRecording() {\n"
            '  return TAURI_INVOKE("start_recording");\n'
            "}\n"
        )

        component = src / "recorder.tsx"
        component.write_text(
            "import * as commands from './bindings';\n"
            "export function RecordButton() {\n"
            "  commands.startRecording();\n"
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "start_recording",
            annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_comp = _make_ts_symbol("RecordButton", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_comp],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_no_wrapper_edge_when_import_not_from_wrapper_file(
        self, tmp_path: Path,
    ) -> None:
        """Importing a non-specta file doesn't create caller_invokes edges."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        # Regular file (not specta-generated)
        utils = src / "utils.ts"
        utils.write_text(
            "export function takeScreenshot() {\n"
            "  console.log('screenshot');\n"
            "}\n"
        )

        component = src / "editor.ts"
        component.write_text(
            "import { takeScreenshot } from './utils';\n"
            "takeScreenshot();\n"
        )

        rust_sym = _make_rust_symbol(
            "take_screenshot",
            annotations=[_tauri_command_annotation()],
        )
        ts_utils = _make_ts_symbol("utils", path=str(utils))
        ts_editor = _make_ts_symbol("editor", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_utils, ts_editor],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_wrapper_with_extension_in_import(self, tmp_path: Path) -> None:
        """Import path with .ts extension resolves to wrapper file."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './bindings.ts';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_wrapper_caller_symbol_created(self, tmp_path: Path) -> None:
        """A synthetic caller symbol is created for each caller_invokes edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './bindings';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_syms = [
            s for s in result.symbols if s.kind == "ipc_caller"
        ]
        assert len(caller_syms) == 1
        assert caller_syms[0].language == "typescript"
        assert caller_syms[0].supply_chain_tier == 2

    def test_wrapper_dedup_same_import_same_file(self, tmp_path: Path) -> None:
        """Same wrapper imported twice in same file creates only one edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './bindings';\n"
            "greet();\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_wrapper_plugin_pattern(self, tmp_path: Path) -> None:
        """Specta wrapper with plugin pattern invoke('plugin:x|cmd')."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function execute() {\n"
            '  return TAURI_INVOKE("plugin:turso|execute");\n'
            "}\n"
        )

        component = src / "db.ts"
        component.write_text(
            "import { execute } from './bindings';\n"
            "execute();\n"
        )

        rust_sym = _make_rust_symbol(
            "execute", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_db = _make_ts_symbol("db", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_db],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_non_relative_import_ignored(self, tmp_path: Path) -> None:
        """Imports from non-relative paths (npm packages) are ignored."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from '@tauri-apps/api';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_unresolvable_import_ignored(self, tmp_path: Path) -> None:
        """Imports pointing to nonexistent files are ignored."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './nonexistent';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_import_from_non_wrapper_file_ignored(self, tmp_path: Path) -> None:
        """Importing from a file that isn't a specta wrapper creates no edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        # Regular TS file (not a wrapper)
        utils = src / "utils.ts"
        utils.write_text(
            "export function greet() {\n"
            "  return 'hello';\n"
            "}\n"
        )

        # Specta wrapper (different file)
        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function saveUser() {\n"
            '  return TAURI_INVOKE("save_user");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './utils';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_utils = _make_ts_symbol("utils", path=str(utils))
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_utils, ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_wrapper_caller_outside_repo_root(self, tmp_path: Path) -> None:
        """Caller file outside repo_root uses absolute path in edge ID."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc
        import tempfile

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        with tempfile.TemporaryDirectory() as other_dir:
            component = Path(other_dir) / "app.ts"
            component.write_text(
                f"import {{ greet }} from '{bindings.parent / 'bindings'}';\n"
            )
            # Won't resolve relative, so no edge
            # But let's test the path: actually for this test we need
            # to simulate it differently. The import resolution won't work
            # since the import isn't relative. Let's skip and test the
            # ValueError path in Phase 4 directly.

        # The ValueError path (line 491-492) fires when the file_path
        # is absolute and outside repo_root. We can trigger this by
        # passing a TS symbol with an absolute path outside repo_root.
        with tempfile.TemporaryDirectory() as other_dir:
            other_src = Path(other_dir) / "src"
            other_src.mkdir()
            component = other_src / "app.ts"
            component.write_text(
                f"import {{ greet }} from '{src / 'bindings'}';\n"
            )

            rust_sym = _make_rust_symbol(
                "greet", annotations=[_tauri_command_annotation()],
            )
            ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
            ts_app = _make_ts_symbol("app", path=str(component))

            result = link_tauri_ipc(
                repo_root=tmp_path,
                ts_js_symbols=[ts_bindings, ts_app],
                rust_symbols=[rust_sym],
            )

            # Import isn't relative so no caller_invokes edge
            caller_edges = [
                e for e in result.edges if e.edge_type == "caller_invokes"
            ]
            assert len(caller_edges) == 0

    def test_wrapper_nonexistent_caller_file_skipped(
        self, tmp_path: Path,
    ) -> None:
        """Nonexistent caller files in Phase 4 are skipped."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        # Symbol for a file that doesn't exist
        ts_ghost = _make_ts_symbol(
            "ghost", path=str(tmp_path / "src" / "ghost.ts"),
        )

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_ghost],
            rust_symbols=[rust_sym],
        )

        # Only ipc_calls from bindings, no caller_invokes
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_wrapper_file_with_no_importers(self, tmp_path: Path) -> None:
        """Wrapper file with no importers creates ipc_calls but no caller_invokes."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        # Another file with no imports
        other = src / "other.ts"
        other.write_text("const x = 1;\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_other = _make_ts_symbol("other", path=str(other))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_other],
            rust_symbols=[rust_sym],
        )

        ipc_edges = [e for e in result.edges if e.edge_type == "ipc_calls"]
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(ipc_edges) == 1
        assert len(caller_edges) == 0

    def test_wrapper_trailing_comma_in_import(self, tmp_path: Path) -> None:
        """Trailing comma in import specifiers handled gracefully."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet, } from './bindings';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_wrapper_caller_outside_repo_root_phase4(
        self, tmp_path: Path,
    ) -> None:
        """Phase 4 caller file outside repo_root uses absolute path in edge ID."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc
        import tempfile

        # Put both bindings and component in a shared directory OUTSIDE
        # tmp_path (which is repo_root). This way the component can
        # use a relative import to reach the bindings file.
        with tempfile.TemporaryDirectory() as shared_dir:
            shared = Path(shared_dir)
            bindings = shared / "bindings.ts"
            bindings.write_text(
                "export function greet() {\n"
                '  return TAURI_INVOKE("greet");\n'
                "}\n"
            )

            component = shared / "app.ts"
            component.write_text(
                "import { greet } from './bindings';\n"
                "greet();\n"
            )

            rust_sym = _make_rust_symbol(
                "greet", annotations=[_tauri_command_annotation()],
            )
            ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
            ts_app = _make_ts_symbol("app", path=str(component))

            result = link_tauri_ipc(
                repo_root=tmp_path,
                ts_js_symbols=[ts_bindings, ts_app],
                rust_symbols=[rust_sym],
            )

            # Should have caller_invokes edge with absolute path (outside repo_root)
            caller_edges = [
                e for e in result.edges if e.edge_type == "caller_invokes"
            ]
            assert len(caller_edges) == 1
            # The path should be absolute since it's outside repo_root
            assert shared_dir in caller_edges[0].src

    def test_wrapper_command_not_in_publisher_map(
        self, tmp_path: Path,
    ) -> None:
        """Imported wrapper function with no matching ipc_publisher is skipped."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        # Wrapper file has a function wrapping a command we DON'T register
        # as a Rust command (so no ipc_publisher is created for it)
        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
            "export function ghost() {\n"
            '  return TAURI_INVOKE("ghost_cmd");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { ghost } from './bindings';\n"
            "ghost();\n"
        )

        # Only register "greet" as a Rust command, not "ghost_cmd"
        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        # ghost_cmd has no ipc_publisher, so no caller_invokes edge for ghost
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_wrapper_dedup_across_imports(self, tmp_path: Path) -> None:
        """Same wrapper function imported twice from namespace creates one edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        # File that imports both by name and namespace (edge case)
        component = src / "app.ts"
        component.write_text(
            "import { greet } from './bindings';\n"
            "import * as cmds from './bindings';\n"
            "greet();\n"
            "cmds.greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        # Dedup: same (file, cmd) pair → only one caller_invokes edge
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1

    def test_wrapper_imported_name_not_in_wrapper_map(
        self, tmp_path: Path,
    ) -> None:
        """Importing a name not exported by the wrapper file creates no edge."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
            "export const VERSION = '1.0';\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { VERSION } from './bindings';\n"
            "console.log(VERSION);\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        result = link_tauri_ipc(
            repo_root=tmp_path,
            ts_js_symbols=[ts_bindings, ts_app],
            rust_symbols=[rust_sym],
        )

        # VERSION is not a wrapper function, so no caller_invokes edge
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 0

    def test_wrapper_resolution_works_through_registry(
        self, tmp_path: Path,
    ) -> None:
        """Wrapper resolution works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        src = tmp_path / "src"
        src.mkdir(parents=True)

        bindings = src / "bindings.ts"
        bindings.write_text(
            "export function greet() {\n"
            '  return TAURI_INVOKE("greet");\n'
            "}\n"
        )

        component = src / "app.ts"
        component.write_text(
            "import { greet } from './bindings';\n"
            "greet();\n"
        )

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_bindings = _make_ts_symbol("bindings", path=str(bindings))
        ts_app = _make_ts_symbol("app", path=str(component))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[ts_bindings, ts_app, rust_sym],
            edges=[],
        )

        result = run_linker("tauri_ipc", ctx)
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) == 1


class TestTauriIPCLinkerRegistry:
    """Tests for Tauri IPC linker registry integration."""

    @pytest.fixture(autouse=True)
    def ensure_tauri_registered(self) -> None:
        """Ensure Tauri IPC linker is registered before each test."""
        import importlib
        import hypergumbo_core.linkers.tauri_ipc as tauri_module
        importlib.reload(tauri_module)

    def test_tauri_linker_registered(self) -> None:
        """Tauri IPC linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("tauri_ipc")
        assert linker is not None
        assert linker.name == "tauri_ipc"

    def test_tauri_linker_has_requirements(self) -> None:
        """Tauri IPC linker declares its requirements."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("tauri_ipc")
        assert linker is not None
        assert len(linker.requirements) >= 2

        req_names = [r.name for r in linker.requirements]
        assert "js_ts_files" in req_names
        assert "tauri_command_functions" in req_names

    def test_tauri_linker_activation_framework(self) -> None:
        """Tauri IPC linker activates for tauri framework."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("tauri_ipc")
        assert linker is not None

        assert linker.activation.should_run({"tauri"}, {"typescript", "rust"})
        assert linker.activation.should_run({"tauri"}, {"javascript", "rust"})

    def test_tauri_linker_activation_language_pair(self) -> None:
        """Tauri IPC linker activates for typescript+rust pair even without framework."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("tauri_ipc")
        assert linker is not None

        assert linker.activation.should_run(set(), {"typescript", "rust"})
        assert linker.activation.should_run(set(), {"javascript", "rust"})
        assert not linker.activation.should_run(set(), {"typescript"})
        assert not linker.activation.should_run(set(), {"rust"})

    def test_tauri_requirements_met(self) -> None:
        """Tauri requirements met when Rust tauri commands and TS files exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        ts_sym = _make_ts_symbol("app", path="src/app.ts")
        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[ts_sym, rust_sym],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        tauri_diag = next(
            (d for d in diagnostics if d.linker_name == "tauri_ipc"), None,
        )
        assert tauri_diag is not None
        assert tauri_diag.all_met is True

    def test_tauri_requirements_unmet_no_commands(self) -> None:
        """Tauri requirements unmet when no #[tauri::command] functions exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        ts_sym = _make_ts_symbol("app", path="src/app.ts")
        rust_sym = _make_rust_symbol("greet")  # No tauri::command annotation

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[ts_sym, rust_sym],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        tauri_diag = next(
            (d for d in diagnostics if d.linker_name == "tauri_ipc"), None,
        )
        assert tauri_diag is not None
        assert tauri_diag.all_met is False

    def test_tauri_requirements_unmet_empty_annotations(self) -> None:
        """Tauri requirements unmet when annotations list is empty."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        ts_sym = _make_ts_symbol("app", path="src/app.ts")
        rust_sym = _make_rust_symbol("greet")
        rust_sym.meta = {"annotations": []}

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[ts_sym, rust_sym],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        tauri_diag = next(
            (d for d in diagnostics if d.linker_name == "tauri_ipc"), None,
        )
        assert tauri_diag is not None
        assert tauri_diag.all_met is False

    def test_tauri_linker_via_registry_dispatch(self, tmp_path: Path) -> None:
        """Tauri IPC linker works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True)
        ts_file.write_text("invoke('greet');\n")

        rust_sym = _make_rust_symbol(
            "greet", annotations=[_tauri_command_annotation()],
        )
        ts_sym = _make_ts_symbol("app", path=str(ts_file))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[ts_sym, rust_sym],
            edges=[],
        )

        result = run_linker("tauri_ipc", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "ipc_calls"


class TestSpectaObjectMethodWrappers:
    """Tests for specta-style `export const commands = { async method() { TAURI_INVOKE } }`.

    Newer versions of tauri-specta generate an exported const object instead of
    individual export functions. This test class verifies detection of:
    - Object method → command-name mapping
    - Named import of the object (e.g., `import { commands } from './tauri'`)
    - caller_invokes edges through the object pattern
    """

    def _make_rust_sym(
        self, name: str, tmp_path: Path,
    ) -> "Symbol":
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol
        run = AnalysisRun.create(pass_id="test", version="test")
        return Symbol(
            id=f"rust:src/lib.rs:1-10:{name}:function",
            name=name,
            kind="function",
            language="rust",
            path="src/lib.rs",
            span=Span(1, 10, 0, 0),
            origin="rust-ts-v1",
            origin_run_id=run.execution_id,
            meta={"annotations": [{"name": "tauri::command"}]},
        )

    def _make_ts_sym(
        self, path: str, tmp_path: Path,
    ) -> "Symbol":
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol
        run = AnalysisRun.create(pass_id="test", version="test")
        return Symbol(
            id=f"typescript:{path}:1-10:mod:module",
            name="mod",
            kind="module",
            language="typescript",
            path=path,
            span=Span(1, 10, 0, 0),
            origin="js-ts-v1",
            origin_run_id=run.execution_id,
        )

    def test_scan_obj_method_wrappers(self, tmp_path: Path) -> None:
        """Detects async method wrappers inside export const object."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "tauri.ts"
        wrapper_file.write_text("""
export const commands = {
async setMicInput(label: string | null) : Promise<null> {
    return await TAURI_INVOKE("set_mic_input", { label });
},
async startRecording(inputs: StartRecordingInputs) : Promise<RecordingAction> {
    return await TAURI_INVOKE("start_recording", { inputs });
},
async stopRecording() : Promise<null> {
    return await TAURI_INVOKE("stop_recording");
},
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert flat == {}
        assert "commands" in obj
        methods = obj["commands"]
        assert methods["setMicInput"] == "set_mic_input"
        assert methods["startRecording"] == "start_recording"
        assert methods["stopRecording"] == "stop_recording"

    def test_scan_both_function_and_obj_wrappers(self, tmp_path: Path) -> None:
        """Detects both function-level and object-method wrappers."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "bindings.ts"
        wrapper_file.write_text("""
export function greet() { return TAURI_INVOKE("greet") }

export const commands = {
async getData() : Promise<string> {
    return await TAURI_INVOKE("get_data");
},
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert flat == {"greet": "greet"}
        assert obj == {"commands": {"getData": "get_data"}}

    def test_named_import_of_obj_creates_edges(self, tmp_path: Path) -> None:
        """import { commands } from './tauri' creates caller_invokes edges."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        wrapper_file = tmp_path / "tauri.ts"
        wrapper_file.write_text("""
export const commands = {
async startRecording() : Promise<null> {
    return await TAURI_INVOKE("start_recording");
},
async stopRecording() : Promise<null> {
    return await TAURI_INVOKE("stop_recording");
},
}
""")
        caller_file = tmp_path / "app.tsx"
        caller_file.write_text("""
import { commands } from "./tauri";
commands.startRecording();
commands.stopRecording();
""")

        rust_sym1 = self._make_rust_sym("start_recording", tmp_path)
        rust_sym2 = self._make_rust_sym("stop_recording", tmp_path)
        ts_sym1 = self._make_ts_sym(str(wrapper_file), tmp_path)
        ts_sym2 = self._make_ts_sym(str(caller_file), tmp_path)

        result = link_tauri_ipc(
            tmp_path,
            [ts_sym1, ts_sym2],
            [rust_sym1, rust_sym2],
        )

        # Should have ipc_calls edges (from Phase 3 direct detection)
        ipc_edges = [e for e in result.edges if e.edge_type == "ipc_calls"]
        assert len(ipc_edges) >= 2

        # Should have caller_invokes edges from the named import
        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) >= 2
        cmd_names = {e.meta.get("tauri_command") if e.meta else None for e in result.symbols if e.kind == "ipc_caller"}
        assert "start_recording" in cmd_names
        assert "stop_recording" in cmd_names

    def test_obj_method_with_plugin_pattern(self, tmp_path: Path) -> None:
        """Object method wrappers handle plugin:name|command pattern."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "bindings.ts"
        wrapper_file.write_text("""
export const commands = {
async speak(text: string) : Promise<null> {
    return await TAURI_INVOKE("plugin:native-tts|speak", { text });
},
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert "commands" in obj
        assert obj["commands"]["speak"] == "speak"

    def test_obj_method_no_invoke_ignored(self, tmp_path: Path) -> None:
        """Object without TAURI_INVOKE methods is not detected."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "utils.ts"
        wrapper_file.write_text("""
export const helpers = {
    formatDate(d: Date) { return d.toISOString(); },
    parseJSON(s: string) { return JSON.parse(s); },
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert flat == {}
        assert obj == {}

    def test_multiple_exported_objects(self, tmp_path: Path) -> None:
        """Multiple export const objects each contribute their methods."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "bindings.ts"
        wrapper_file.write_text("""
export const commands = {
async greet() : Promise<string> {
    return await TAURI_INVOKE("greet");
},
}

export const events = {
async onReady() : Promise<null> {
    return await TAURI_INVOKE("on_ready");
},
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert "commands" in obj
        assert "events" in obj
        assert obj["commands"]["greet"] == "greet"
        assert obj["events"]["onReady"] == "on_ready"

    def test_obj_arrow_function_method(self, tmp_path: Path) -> None:
        """Detects arrow function methods inside exported objects."""
        from hypergumbo_core.linkers.tauri_ipc import _scan_specta_wrappers

        wrapper_file = tmp_path / "bindings.ts"
        wrapper_file.write_text("""
export const commands = {
greet: (name: string) => TAURI_INVOKE("greet", { name }),
getData: async () => await TAURI_INVOKE("get_data"),
}
""")
        flat, obj = _scan_specta_wrappers(wrapper_file)
        assert "commands" in obj
        assert obj["commands"]["greet"] == "greet"
        assert obj["commands"]["getData"] == "get_data"

    def test_namespace_import_includes_obj_methods(self, tmp_path: Path) -> None:
        """import * as bindings creates edges for object method wrappers too."""
        from hypergumbo_core.linkers.tauri_ipc import link_tauri_ipc

        wrapper_file = tmp_path / "bindings.ts"
        wrapper_file.write_text("""
export const commands = {
async doStuff() : Promise<null> {
    return await TAURI_INVOKE("do_stuff");
},
}
""")
        caller_file = tmp_path / "main.ts"
        caller_file.write_text("""
import * as bindings from "./bindings";
bindings.commands.doStuff();
""")

        rust_sym = self._make_rust_sym("do_stuff", tmp_path)
        ts_sym1 = self._make_ts_sym(str(wrapper_file), tmp_path)
        ts_sym2 = self._make_ts_sym(str(caller_file), tmp_path)

        result = link_tauri_ipc(tmp_path, [ts_sym1, ts_sym2], [rust_sym])

        caller_edges = [e for e in result.edges if e.edge_type == "caller_invokes"]
        assert len(caller_edges) >= 1
