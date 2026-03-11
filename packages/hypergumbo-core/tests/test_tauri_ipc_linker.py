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
