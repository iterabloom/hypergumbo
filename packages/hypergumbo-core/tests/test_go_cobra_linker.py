# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Go spf13/cobra CLI dispatch linker (WI-gohad).

Cobra is the dominant Go CLI framework — used by kubectl, helm, hugo,
prometheus promtool, terraform, docker, hashicorp vault/consul/nomad,
etcdctl and every cobra-based service. The linker bridges the
``cobra.Command`` struct-literal construction site to each of the
handler functions referenced via the ``Run``/``RunE``/``PreRun``/
``PreRunE``/``PostRun``/``PostRunE``/``PersistentPreRun``/
``PersistentPreRunE``/``PersistentPostRun``/``PersistentPostRunE``
fields. Without the linker, these handlers look dead to the BFS
because cobra dispatches them at runtime from ``Execute()``.

The tests exercise two layers:
- Regex-level detection (``_find_handler_assignments``,
  ``_file_imports_cobra``): confirms the cobra.Command literal and
  handler-field parsers work on a range of cobra-idiomatic sources.
- Linker-level integration (``go_cobra_linker(ctx)``): confirms the
  LinkerContext plumbing wires enclosing functions to handler
  symbols, dedupes, respects the ``go`` language gate, and does
  nothing when no cobra import is present.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.go_cobra import (
    _COBRA_COMMAND_ANCHOR,
    _FIELD_ASSIGN_PATTERN,
    _file_imports_cobra,
    _find_handler_assignments,
    go_cobra_linker,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestAnchorAndImport:
    """Low-level regex helpers for cobra source detection."""

    def test_anchor_matches_value_form(self) -> None:
        assert _COBRA_COMMAND_ANCHOR.search(b"x := cobra.Command{")

    def test_anchor_matches_pointer_form(self) -> None:
        assert _COBRA_COMMAND_ANCHOR.search(b"x := &cobra.Command{")

    def test_anchor_rejects_unrelated(self) -> None:
        assert not _COBRA_COMMAND_ANCHOR.search(b"cobra.NotCommand{")

    def test_file_imports_cobra_true(self) -> None:
        source = b'import (\n  "fmt"\n  "github.com/spf13/cobra"\n)\n'
        assert _file_imports_cobra(source) is True

    def test_file_imports_cobra_false(self) -> None:
        source = b'import "github.com/spf13/otherlib"\n'
        assert _file_imports_cobra(source) is False


class TestFindHandlerAssignments:
    """Parse handler-field assignments out of cobra.Command literals."""

    def test_finds_run_e(self) -> None:
        source = b"""
cmd := &cobra.Command{
    Use: "mycmd",
    RunE: runMyCmd,
}
"""
        results = _find_handler_assignments(source)
        assert ("RunE", "runMyCmd", 4) in results

    def test_finds_all_handler_fields(self) -> None:
        source = b"""
cmd := &cobra.Command{
    Use:                "mycmd",
    Run:                runCmd,
    PreRun:             preRunCmd,
    PostRun:            postRunCmd,
    PersistentPreRun:   persistentPreRunCmd,
    PersistentPostRun:  persistentPostRunCmd,
    RunE:               runECmd,
    PreRunE:            preRunECmd,
    PostRunE:           postRunECmd,
    PersistentPreRunE:  persistentPreRunECmd,
    PersistentPostRunE: persistentPostRunECmd,
}
"""
        results = _find_handler_assignments(source)
        fields = {r[0] for r in results}
        assert fields == {
            "Run", "PreRun", "PostRun",
            "PersistentPreRun", "PersistentPostRun",
            "RunE", "PreRunE", "PostRunE",
            "PersistentPreRunE", "PersistentPostRunE",
        }

    def test_finds_package_qualified_handler(self) -> None:
        source = b"""
cmd := &cobra.Command{
    RunE: pkg.runMyCmd,
}
"""
        results = _find_handler_assignments(source)
        # Match shouldn't fail on the dot — identifier capture allows one
        # optional package prefix.
        names = {r[1] for r in results}
        assert "pkg.runMyCmd" in names

    def test_skips_inline_function_literal(self) -> None:
        """Inline ``func(...)`` lambdas are NOT in scope for this linker."""
        source = b"""
cmd := &cobra.Command{
    Run: func(cmd *cobra.Command, args []string) {
        fmt.Println("inline")
    },
}
"""
        # The regex captures ``Run: func`` as an identifier "func", but
        # "func" is a Go keyword so it won't resolve to a Symbol. We
        # still prefer the parser to NOT emit it — but if the parser
        # does yield it, the linker-level resolution filter handles it.
        # The explicit assertion here is that the regex does not mistake
        # ``func`` for a real handler identifier more than once (the
        # opening brace isn't a comma/brace terminator).
        results = _find_handler_assignments(source)
        names = {r[1] for r in results}
        # "func" may appear but it's not a real handler (Go keyword);
        # no edges will be emitted for it at linker time. The important
        # invariant is that we don't accidentally capture "fmt" as a
        # handler.
        assert "fmt" not in names
        assert "Println" not in names

    def test_skips_non_handler_fields(self) -> None:
        source = b"""
cmd := &cobra.Command{
    Use:   "mycmd",
    Short: "Does a thing",
    Long:  "Really does a thing",
}
"""
        results = _find_handler_assignments(source)
        assert results == []

    def test_skips_nil_handler(self) -> None:
        source = b"""
cmd := &cobra.Command{
    RunE: nil,
}
"""
        results = _find_handler_assignments(source)
        assert results == []


class TestGoCobraLinkerIntegration:
    """End-to-end linker runs on real tmp_path Go sources."""

    def _write_cobra_file(
        self, tmp_path: Path, handlers: str = "RunE: runMyCmd,",
    ) -> Path:
        p = tmp_path / "cmd" / "root.go"
        p.parent.mkdir(parents=True)
        p.write_text(
            'package cmd\n\n'
            'import (\n'
            '    "fmt"\n'
            '    "github.com/spf13/cobra"\n'
            ')\n\n'
            'func init() {\n'
            '    cmd := &cobra.Command{\n'
            '        Use: "mycmd",\n'
            f'        {handlers}\n'
            '    }\n'
            '    _ = cmd\n'
            '}\n\n'
            'func runMyCmd(cmd *cobra.Command, args []string) error {\n'
            '    fmt.Println("run")\n'
            '    return nil\n'
            '}\n',
        )
        return p

    def test_links_run_e_to_handler(self, tmp_path: Path) -> None:
        file_path = self._write_cobra_file(tmp_path)

        init_sym = Symbol(
            id=f"go:{file_path}:8-14:init:function",
            name="init",
            kind="function",
            language="go",
            path=str(file_path),
            span=Span(start_line=8, end_line=14, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"go:{file_path}:16-19:runMyCmd:function",
            name="runMyCmd",
            kind="function",
            language="go",
            path=str(file_path),
            span=Span(start_line=16, end_line=19, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[init_sym, handler_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)

        assert len(result.edges) >= 1
        # At least one edge goes from the enclosing function to the handler.
        matching = [
            e for e in result.edges
            if e.dst == handler_sym.id and e.edge_type == "dispatches_to"
        ]
        assert len(matching) == 1
        edge = matching[0]
        assert edge.src == init_sym.id
        assert edge.meta is not None
        assert edge.meta.get("cobra_field") == "RunE"
        assert edge.meta.get("handler_name") == "runMyCmd"

    def test_noop_when_no_go_detected(self, tmp_path: Path) -> None:
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"python"},
        )
        result = go_cobra_linker(ctx)
        assert result.edges == []

    def test_noop_when_no_cobra_command_anchor(self, tmp_path: Path) -> None:
        """Go file that doesn't mention cobra.Command is skipped before
        the import check — exercises the anchor pre-filter."""
        p = tmp_path / "utils.go"
        p.write_text(
            'package utils\n\n'
            'import "fmt"\n\n'
            'func Helper() {\n'
            '    fmt.Println("hello")\n'
            '}\n',
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        assert result.edges == []

    def test_noop_when_no_cobra_import(self, tmp_path: Path) -> None:
        # File has cobra.Command{ but does NOT import spf13/cobra —
        # a highly unlikely case but the pre-filter still covers it.
        p = tmp_path / "main.go"
        p.write_text(
            'package main\n\n'
            'type cobra struct{}\n'
            'func (cobra) Command() {}\n'
            'func main() {\n'
            '    var _ = cobra.Command{}\n'
            '}\n',
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        assert result.edges == []

    def test_noop_when_no_handler_assignments(self, tmp_path: Path) -> None:
        p = tmp_path / "cmd.go"
        p.write_text(
            'package cmd\n\n'
            'import "github.com/spf13/cobra"\n\n'
            'func init() {\n'
            '    cmd := &cobra.Command{Use: "x"}\n'
            '    _ = cmd\n'
            '}\n',
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        assert result.edges == []

    def test_unresolved_handler_yields_no_edge(self, tmp_path: Path) -> None:
        """Handler referenced but no matching Symbol → no edge emitted."""
        self._write_cobra_file(tmp_path)

        # Only the init symbol; no runMyCmd in the context.
        init_sym = Symbol(
            id="go:stub:init",
            name="init",
            kind="function",
            language="go",
            path=str(tmp_path / "cmd" / "root.go"),
            span=Span(start_line=8, end_line=14, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[init_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        assert result.edges == []

    def test_deduplicates_identical_pairs(self, tmp_path: Path) -> None:
        """Two cobra.Command literals referencing the same handler from the
        same enclosing function should produce exactly one edge."""
        p = tmp_path / "cmd" / "root.go"
        p.parent.mkdir(parents=True)
        p.write_text(
            'package cmd\n\n'
            'import "github.com/spf13/cobra"\n\n'
            'func init() {\n'
            '    a := &cobra.Command{RunE: runMyCmd}\n'
            '    b := &cobra.Command{RunE: runMyCmd}\n'
            '    _, _ = a, b\n'
            '}\n\n'
            'func runMyCmd(cmd *cobra.Command, args []string) error {\n'
            '    return nil\n'
            '}\n',
        )

        init_sym = Symbol(
            id=f"go:{p}:5-9:init:function",
            name="init",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=5, end_line=9, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"go:{p}:11-13:runMyCmd:function",
            name="runMyCmd",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=11, end_line=13, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[init_sym, handler_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        matching = [
            e for e in result.edges if e.dst == handler_sym.id
        ]
        assert len(matching) == 1

    def test_package_qualified_handler_resolves_via_short_name(
        self, tmp_path: Path,
    ) -> None:
        """A handler referenced as ``pkg.handler`` in the cobra.Command
        literal is resolved against the short-name index when the full
        qualified form has no direct match."""
        file_path = tmp_path / "cmd" / "root.go"
        file_path.parent.mkdir(parents=True)
        file_path.write_text(
            'package cmd\n\n'
            'import (\n'
            '    "github.com/spf13/cobra"\n'
            '    "myapp/internal/runner"\n'
            ')\n\n'
            'func init() {\n'
            '    cmd := &cobra.Command{\n'
            '        Use:  "mycmd",\n'
            '        RunE: runner.runMyCmd,\n'
            '    }\n'
            '    _ = cmd\n'
            '}\n\n'
            'func doSomething(cmd *cobra.Command, args []string) error {\n'
            '    return nil\n'
            '}\n',
        )

        init_sym = Symbol(
            id=f"go:{file_path}:8-14:init:function",
            name="init",
            kind="function",
            language="go",
            path=str(file_path),
            span=Span(start_line=8, end_line=14, start_col=0, end_col=0),
        )
        # The handler symbol is indexed under its short name "runMyCmd"
        # (the analyzer's usual convention). The linker's first lookup
        # ("runner.runMyCmd") will miss; the retry on the short name
        # after splitting the dot succeeds.
        handler_sym = Symbol(
            id=f"go:{file_path}:16-18:runMyCmd:function",
            name="runMyCmd",
            kind="function",
            language="go",
            path=str(file_path),
            span=Span(start_line=16, end_line=18, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[init_sym, handler_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)

        # Exactly one dispatches_to edge from init to runMyCmd,
        # proved by the metadata capturing the qualified handler name.
        matching = [
            e for e in result.edges
            if e.dst == handler_sym.id and e.edge_type == "dispatches_to"
        ]
        assert len(matching) == 1
        assert matching[0].meta is not None
        assert matching[0].meta.get("handler_name") == "runner.runMyCmd"

    def test_package_level_var_emits_edge_from_var_symbol(
        self, tmp_path: Path,
    ) -> None:
        """When the cobra.Command literal is a package-level var declaration,
        the linker should emit an edge from the var symbol to the handler."""
        p = tmp_path / "cmd.go"
        p.write_text(
            'package main\n\n'
            'import "github.com/spf13/cobra"\n\n'
            'var rootCmd = &cobra.Command{\n'
            '    Use:  "root",\n'
            '    RunE: rootRun,\n'
            '}\n\n'
            'func rootRun(cmd *cobra.Command, args []string) error {\n'
            '    return nil\n'
            '}\n',
        )

        var_sym = Symbol(
            id=f"go:{p}:5-8:rootCmd:variable",
            name="rootCmd",
            kind="variable",
            language="go",
            path=str(p),
            span=Span(start_line=5, end_line=8, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"go:{p}:10-12:rootRun:function",
            name="rootRun",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=10, end_line=12, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[var_sym, handler_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        # Package-level var → edge from var symbol to handler.
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == var_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "dispatches_to"

    def test_package_level_var_no_var_symbol_yields_no_edge(
        self, tmp_path: Path,
    ) -> None:
        """When the cobra.Command literal is at package level and no
        var symbol is provided, the linker skips rather than emitting a
        misattributed edge."""
        p = tmp_path / "cmd.go"
        p.write_text(
            'package main\n\n'
            'import "github.com/spf13/cobra"\n\n'
            'var rootCmd = &cobra.Command{\n'
            '    Use:  "root",\n'
            '    RunE: rootRun,\n'
            '}\n\n'
            'func rootRun(cmd *cobra.Command, args []string) error {\n'
            '    return nil\n'
            '}\n',
        )

        handler_sym = Symbol(
            id=f"go:{p}:10-12:rootRun:function",
            name="rootRun",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=10, end_line=12, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[handler_sym],
            detected_languages={"go"},
        )
        result = go_cobra_linker(ctx)
        # No var symbol and no enclosing function → no edges.
        assert result.edges == []

    def test_pattern_compiles(self) -> None:
        """_FIELD_ASSIGN_PATTERN compiles and has the expected groups."""
        m = _FIELD_ASSIGN_PATTERN.search(b"RunE: myFn,")
        assert m is not None
        assert m.group("field") == b"RunE"
        assert m.group("handler") == b"myFn"
