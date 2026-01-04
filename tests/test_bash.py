"""Tests for Bash/shell script analyzer."""
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestBashHelpers:
    """Tests for Bash analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from hypergumbo.analyze.bash import _find_child_by_type

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "identifier")
        assert result is None


class TestFindBashFiles:
    """Tests for Bash file discovery."""

    def test_finds_sh_files(self, tmp_path: Path) -> None:
        """Finds .sh files."""
        from hypergumbo.analyze.bash import find_bash_files

        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hello")
        (tmp_path / "utils.bash").write_text("#!/bin/bash\nfunction test() { :; }")
        (tmp_path / "other.txt").write_text("not a script")

        files = list(find_bash_files(tmp_path))

        assert len(files) == 2
        extensions = {f.suffix for f in files}
        assert ".sh" in extensions
        assert ".bash" in extensions

    def test_finds_files_without_extension_with_shebang(self, tmp_path: Path) -> None:
        """Finds executable files with shell shebang but no extension."""
        from hypergumbo.analyze.bash import find_bash_files

        # File with shebang but no extension
        script_file = tmp_path / "run-script"
        script_file.write_text("#!/bin/bash\necho hello")

        files = list(find_bash_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "run-script"


class TestBashTreeSitterAvailability:
    """Tests for tree-sitter-bash availability checking."""

    def test_is_bash_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-bash is available."""
        from hypergumbo.analyze.bash import is_bash_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_bash_tree_sitter_available() is True

    def test_is_bash_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.bash import is_bash_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_bash_tree_sitter_available() is False

    def test_is_bash_tree_sitter_available_no_bash(self) -> None:
        """Returns False when tree-sitter is available but bash grammar is not."""
        from hypergumbo.analyze.bash import is_bash_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_bash_tree_sitter_available() is False


class TestAnalyzeBashFallback:
    """Tests for fallback behavior when tree-sitter-bash unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-bash unavailable."""
        from hypergumbo.analyze.bash import analyze_bash

        (tmp_path / "test.sh").write_text("#!/bin/bash\necho hello")

        with patch("hypergumbo.analyze.bash.is_bash_tree_sitter_available", return_value=False):
            result = analyze_bash(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-bash" in result.skip_reason


class TestBashFunctionExtraction:
    """Tests for extracting Bash functions."""

    def test_extracts_function_keyword_style(self, tmp_path: Path) -> None:
        """Extracts function declarations with 'function' keyword."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "utils.sh"
        bash_file.write_text("""#!/bin/bash

function greet() {
    echo "Hello, $1!"
}

function helper() {
    echo "helping"
}
""")

        result = analyze_bash(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "greet" in func_names
        assert "helper" in func_names

    def test_extracts_posix_style_function(self, tmp_path: Path) -> None:
        """Extracts POSIX-style function definitions (name())."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "script.sh"
        bash_file.write_text("""#!/bin/bash

say_hello() {
    echo "hello"
}

do_work() {
    echo "working"
}
""")

        result = analyze_bash(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "say_hello" in func_names
        assert "do_work" in func_names


class TestBashVariableExtraction:
    """Tests for extracting Bash variables."""

    def test_extracts_exported_variables(self, tmp_path: Path) -> None:
        """Extracts exported variable declarations."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "config.sh"
        bash_file.write_text("""#!/bin/bash

export MY_CONFIG="value"
export PATH="/usr/bin:$PATH"
""")

        result = analyze_bash(tmp_path)


        exports = [s for s in result.symbols if s.kind == "export"]
        export_names = [s.name for s in exports]
        assert "MY_CONFIG" in export_names
        assert "PATH" in export_names


class TestBashSourceEdges:
    """Tests for extracting source/import statements."""

    def test_extracts_source_statements(self, tmp_path: Path) -> None:
        """Extracts source statements as import edges."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "main.sh"
        bash_file.write_text("""#!/bin/bash

source utils.sh
source lib/common.sh
""")

        result = analyze_bash(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "sources"]
        assert len(import_edges) >= 2

        sourced = [e.dst for e in import_edges]
        assert any("utils.sh" in dst for dst in sourced)
        assert any("common.sh" in dst for dst in sourced)

    def test_extracts_dot_source_statements(self, tmp_path: Path) -> None:
        """Extracts dot (.) source statements as import edges."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "main.sh"
        bash_file.write_text("""#!/bin/bash

. /etc/profile
. ./local.sh
""")

        result = analyze_bash(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "sources"]
        assert len(import_edges) >= 2


class TestBashCallEdges:
    """Tests for extracting function call edges."""

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "main.sh"
        bash_file.write_text("""#!/bin/bash

function helper() {
    echo "helping"
}

function main() {
    helper
}
""")

        result = analyze_bash(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_extracts_cross_file_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions in different files."""
        from hypergumbo.analyze.bash import analyze_bash

        helper_file = tmp_path / "helper.sh"
        helper_file.write_text("""#!/bin/bash

function do_work() {
    echo "working"
}
""")

        main_file = tmp_path / "main.sh"
        main_file.write_text("""#!/bin/bash

source helper.sh

function run() {
    do_work
}
""")

        result = analyze_bash(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # Check for cross-file call edge with lower confidence
        cross_file_edges = [e for e in call_edges if e.confidence == 0.80]
        assert len(cross_file_edges) >= 1


class TestBashSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "test.sh"
        bash_file.write_text("""function test() {
    echo "test"
}
""")

        result = analyze_bash(tmp_path)


        test_func = next((s for s in result.symbols if s.name == "test"), None)
        assert test_func is not None
        assert test_func.span.start_line == 1
        assert test_func.language == "bash"
        assert test_func.origin == "bash-v1"


class TestBashEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "test.sh"
        bash_file.write_text("""#!/bin/bash

source utils.sh
""")

        result = analyze_bash(tmp_path)


        source_edges = [e for e in result.edges if e.edge_type == "sources"]
        for edge in source_edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestBashEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty Bash files gracefully."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "empty.sh"
        bash_file.write_text("")

        result = analyze_bash(tmp_path)


        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "comments.sh"
        bash_file.write_text("""#!/bin/bash
# This is a comment
# Another comment
""")

        result = analyze_bash(tmp_path)


        assert result.run is not None


class TestBashParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load Bash parser."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "test.sh"
        bash_file.write_text("#!/bin/bash\necho hello")

        with patch("hypergumbo.analyze.bash.is_bash_tree_sitter_available", return_value=True):
            with patch("tree_sitter_bash.language", side_effect=Exception("Parser error")):
                result = analyze_bash(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason


class TestBashAliasExtraction:
    """Tests for extracting Bash aliases."""

    def test_extracts_aliases(self, tmp_path: Path) -> None:
        """Extracts alias declarations."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "aliases.sh"
        bash_file.write_text("""#!/bin/bash

alias ll='ls -la'
alias gs='git status'
""")

        result = analyze_bash(tmp_path)


        aliases = [s for s in result.symbols if s.kind == "alias"]
        alias_names = [s.name for s in aliases]
        assert "ll" in alias_names
        assert "gs" in alias_names


class TestBashInternalHelpers:
    """Tests for internal helper functions."""

    def test_is_bash_shebang_no_shebang(self) -> None:
        """Returns False when line doesn't start with #!."""
        from hypergumbo.analyze.bash import _is_bash_shebang

        assert _is_bash_shebang("not a shebang") is False
        assert _is_bash_shebang("echo hello") is False

    def test_extract_alias_word_format(self, tmp_path: Path) -> None:
        """Extracts alias from word format (alias name=value without quotes)."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "alias.sh"
        bash_file.write_text("""#!/bin/bash
alias myalias=value
""")

        result = analyze_bash(tmp_path)


        aliases = [s for s in result.symbols if s.kind == "alias"]
        assert any(s.name == "myalias" for s in aliases)


class TestBashShebangHandling:
    """Tests for shebang handling in files."""

    def test_ignores_non_bash_shebang(self, tmp_path: Path) -> None:
        """Ignores files with non-bash shebang."""
        from hypergumbo.analyze.bash import find_bash_files

        # Python script without extension
        py_file = tmp_path / "run-python"
        py_file.write_text("#!/usr/bin/env python3\nprint('hello')")

        # Bash script without extension
        bash_file = tmp_path / "run-bash"
        bash_file.write_text("#!/bin/bash\necho hello")

        files = list(find_bash_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "run-bash"

    def test_handles_various_bash_shebangs(self, tmp_path: Path) -> None:
        """Handles various bash shebang formats."""
        from hypergumbo.analyze.bash import find_bash_files

        # Various shebang formats
        (tmp_path / "bash1").write_text("#!/bin/bash\necho 1")
        (tmp_path / "bash2").write_text("#!/usr/bin/bash\necho 2")
        (tmp_path / "bash3").write_text("#!/usr/bin/env bash\necho 3")
        (tmp_path / "sh1").write_text("#!/bin/sh\necho 4")

        files = list(find_bash_files(tmp_path))

        assert len(files) == 4


class TestBashSignatureExtraction:
    """Tests for Bash function signature extraction."""

    def test_function_signature_is_empty_parens(self, tmp_path: Path) -> None:
        """Bash functions always have () signature (no formal parameters)."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "funcs.sh"
        bash_file.write_text("""#!/bin/bash

function greet() {
    echo "Hello, $1!"
}
""")
        result = analyze_bash(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_posix_function_signature(self, tmp_path: Path) -> None:
        """POSIX-style functions also have () signature."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "funcs.sh"
        bash_file.write_text("""#!/bin/bash

say_hello() {
    echo "hello"
}
""")
        result = analyze_bash(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "say_hello"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_multiple_functions_all_have_signatures(self, tmp_path: Path) -> None:
        """All extracted functions have signatures."""
        from hypergumbo.analyze.bash import analyze_bash

        bash_file = tmp_path / "utils.sh"
        bash_file.write_text("""#!/bin/bash

function one() { echo 1; }
function two() { echo 2; }
three() { echo 3; }
""")
        result = analyze_bash(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 3
        for func in funcs:
            assert func.signature == "()"
