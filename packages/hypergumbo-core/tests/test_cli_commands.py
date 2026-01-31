"""Tests for CLI commands to achieve 100% coverage."""
import json
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import (
    cmd_run,
    cmd_slice,
    cmd_catalog,
    cmd_sketch,
    cmd_compact,
    main,
    _find_git_root,
    _print_output_summary,
    _sanitize_filename_part,
)


class FakeArgs:
    """Minimal namespace for testing command functions."""
    pass


def test_cmd_run_creates_behavior_map(tmp_path: Path) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    result = cmd_run(args)

    assert result == 0

    out_path = tmp_path / "results.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == SCHEMA_VERSION


def test_cmd_run_with_js_analyzer_available(tmp_path: Path) -> None:
    """Test run with mocked JS analyzer returning successful results."""
    from unittest.mock import patch
    from hypergumbo_core.ir import Symbol, Span, AnalysisRun
    from hypergumbo_lang_mainstream.js_ts import JsAnalysisResult

    # Create a JS file to trigger analysis
    (tmp_path / "app.js").write_text("function foo() {}")

    # Create mock result with symbols and edges
    mock_run = AnalysisRun.create(pass_id="javascript-ts-v1", version="test")
    mock_symbol = Symbol(
        id="javascript:app.js:1-1:foo:function",
        name="foo",
        kind="function",
        language="javascript",
        path=str(tmp_path / "app.js"),
        span=Span(start_line=1, end_line=1, start_col=0, end_col=17),
    )
    mock_result = JsAnalysisResult(
        symbols=[mock_symbol],
        edges=[],
        run=mock_run,
        skipped=False,
        skip_reason="",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.js_ts.analyze_javascript", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have JavaScript symbols
    js_nodes = [n for n in data["nodes"] if n["language"] == "javascript"]
    assert len(js_nodes) == 1
    assert js_nodes[0]["name"] == "foo"


def test_cmd_run_with_js_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with JS analyzer skipped (tree-sitter not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.js_ts import JsAnalysisResult
    from hypergumbo_lang_mainstream.php import PhpAnalysisResult

    # Create mock result with skipped flag for JS
    mock_js_run = AnalysisRun.create(pass_id="javascript-ts-v1", version="test")
    mock_js_result = JsAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_js_run,
        skipped=True,
        skip_reason="requires tree-sitter",
    )

    # Create mock result for PHP (not skipped, just empty)
    mock_php_run = AnalysisRun.create(pass_id="php-v1", version="test")
    mock_php_result = PhpAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_php_run,
        skipped=False,
        skip_reason=None,
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.js_ts.analyze_javascript", return_value=mock_js_result), \
         patch("hypergumbo_lang_mainstream.php.analyze_php", return_value=mock_php_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    # Check that JS is in the skipped list (there may be other skipped passes too)
    skipped_passes = [p["pass"] for p in data["limits"]["skipped_passes"]]
    assert "javascript-ts-v1" in skipped_passes


def test_cmd_run_with_php_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with PHP analyzer skipped (tree-sitter-php not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.php import PhpAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="php-v1", version="test")
    mock_result = PhpAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-php",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.php.analyze_php", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "php-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-php" in skipped[0]["reason"]


def test_cmd_run_with_c_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with C analyzer skipped (tree-sitter-c not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.c import CAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="c-v1", version="test")
    mock_result = CAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-c",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.c.analyze_c", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "c-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-c" in skipped[0]["reason"]


def test_cmd_run_with_java_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Java analyzer skipped (tree-sitter-java not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.java import JavaAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="java-v1", version="test")
    mock_result = JavaAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-java",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.java.analyze_java", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "java-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-java" in skipped[0]["reason"]


def test_cmd_run_with_elixir_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Elixir analyzer skipped (tree-sitter-elixir not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_common.elixir import ElixirAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="elixir-v1", version="test")
    mock_result = ElixirAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-elixir",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_common.elixir.analyze_elixir", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "elixir-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-elixir" in skipped[0]["reason"]


def test_cmd_run_with_rust_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Rust analyzer skipped (tree-sitter-rust not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.rust import RustAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="rust-v1", version="test")
    mock_result = RustAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-rust",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.rust.analyze_rust", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "rust-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-rust" in skipped[0]["reason"]


def test_cmd_run_with_go_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Go analyzer skipped (tree-sitter-go not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.go import GoAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="go-v1", version="test")
    mock_result = GoAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-go",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.go.analyze_go", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "go-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-go" in skipped[0]["reason"]


def test_cmd_run_with_ruby_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Ruby analyzer skipped (tree-sitter-ruby not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.ruby import RubyAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="ruby-v1", version="test")
    mock_result = RubyAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-ruby",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.ruby.analyze_ruby", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "ruby-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-ruby" in skipped[0]["reason"]


def test_cmd_run_with_kotlin_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Kotlin analyzer skipped (tree-sitter-kotlin not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.kotlin import KotlinAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="kotlin-v1", version="test")
    mock_result = KotlinAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-kotlin",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.kotlin.analyze_kotlin", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "kotlin-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-kotlin" in skipped[0]["reason"]


def test_cmd_run_with_swift_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Swift analyzer skipped (tree-sitter-swift not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.swift import SwiftAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="swift-v1", version="test")
    mock_result = SwiftAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-swift",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.swift.analyze_swift", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "swift-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-swift" in skipped[0]["reason"]


def test_cmd_run_with_scala_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Scala analyzer skipped (tree-sitter-scala not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.scala import ScalaAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="scala-v1", version="test")
    mock_result = ScalaAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-scala",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.scala.analyze_scala", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "scala-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-scala" in skipped[0]["reason"]


def test_cmd_run_with_lua_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Lua analyzer skipped (tree-sitter-lua not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_mainstream.lua import LuaAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="lua-v1", version="test")
    mock_result = LuaAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-lua",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_mainstream.lua.analyze_lua", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "lua-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-lua" in skipped[0]["reason"]


def test_cmd_run_with_haskell_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with Haskell analyzer skipped (tree-sitter-haskell not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_common.haskell import HaskellAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="haskell-v1", version="test")
    mock_result = HaskellAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-haskell",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_common.haskell.analyze_haskell", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "haskell-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-haskell" in skipped[0]["reason"]


def test_cmd_run_with_ocaml_analyzer_skipped(tmp_path: Path) -> None:
    """Test run with OCaml analyzer skipped (tree-sitter-ocaml not available)."""
    from unittest.mock import patch
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_lang_common.ocaml import OCamlAnalysisResult

    # Create mock result with skipped flag
    mock_run = AnalysisRun.create(pass_id="ocaml-v1", version="test")
    mock_result = OCamlAnalysisResult(
        symbols=[],
        edges=[],
        run=mock_run,
        skipped=True,
        skip_reason="requires tree-sitter-ocaml",
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    with patch("hypergumbo_lang_common.ocaml.analyze_ocaml", return_value=mock_result):
        result = cmd_run(args)

    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())
    # Should have recorded skipped pass in limits
    assert "skipped_passes" in data["limits"]
    skipped = [p for p in data["limits"]["skipped_passes"] if p["pass"] == "ocaml-v1"]
    assert len(skipped) == 1
    assert "tree-sitter-ocaml" in skipped[0]["reason"]


def test_cmd_run_with_jni_linker(tmp_path: Path) -> None:
    """Test that JNI linker runs when Java and C files with JNI patterns exist."""
    # Create Java file with native method
    (tmp_path / "NativeLib.java").write_text("""
public class NativeLib {
    public native void sayHello();
}
""")

    # Create C file with JNI implementation
    (tmp_path / "native.c").write_text("""
#include <jni.h>

JNIEXPORT void JNICALL Java_NativeLib_sayHello(JNIEnv *env, jobject obj) {
    // Implementation
}
""")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "output.json")

    result = cmd_run(args)

    assert result == 0

    out_path = tmp_path / "output.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())

    # Should have JNI linker run
    runs = [r["pass"] for r in data["analysis_runs"]]
    assert "jni-linker-v1" in runs

    # Should have native_bridge edge
    native_edges = [e for e in data["edges"] if e["type"] == "native_bridge"]
    assert len(native_edges) >= 1


def test_cmd_slice_creates_slice(tmp_path: Path, capsys) -> None:
    """Test that slice command produces a valid slice file."""
    # Create a behavior map file
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-2:hello:function",
                "name": "hello",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 10},
            }
        ],
        "edges": [],
    }
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "hello"
    args.out = str(tmp_path / "slice.json")
    args.input = None
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0

    out_path = tmp_path / "slice.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["view"] == "slice"
    assert "feature" in data
    assert data["feature"]["name"] == "hello"

    out, _ = capsys.readouterr()
    assert "[hypergumbo slice]" in out


def test_cmd_slice_with_input_file(tmp_path: Path) -> None:
    """Test slice command reading from existing behavior map."""
    # Create a behavior map file
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 10},
            }
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "foo"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())
    assert len(data["feature"]["node_ids"]) == 1


def test_cmd_slice_input_not_found(tmp_path: Path) -> None:
    """Test slice command with missing input file."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "foo"
    args.out = str(tmp_path / "slice.json")
    args.input = str(tmp_path / "nonexistent.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 1  # Error exit code


def test_cmd_slice_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Test slice command auto-runs analysis when no cached results exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "foo"
    args.out = str(tmp_path / "slice.json")
    args.input = None  # No --input, and no hypergumbo.results.json
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    # Auto-runs analysis and succeeds (even if slice is empty due to no matching entry)
    assert result == 0

    _, err = capsys.readouterr()
    # Should indicate analysis was auto-run
    assert "No cached results found, running analysis" in err


def test_cmd_slice_reads_existing_results(tmp_path: Path, capsys) -> None:
    """Test slice command reads from existing hypergumbo.results.json."""
    # Create a behavior map file at the default location
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-2:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 10},
            }
        ],
        "edges": [],
    }
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "bar"
    args.out = str(tmp_path / "slice.json")
    args.input = None  # Should auto-detect existing results
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())
    assert len(data["feature"]["node_ids"]) == 1


def test_cmd_slice_with_limits_hit(tmp_path: Path, capsys) -> None:
    """Test slice command prints limits hit."""
    # Create a chain that will hit hop limit
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:a.py:1-2:a:function",
                "name": "a",
                "kind": "function",
                "language": "python",
                "path": "a.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 5},
            },
            {
                "id": "python:b.py:1-2:b:function",
                "name": "b",
                "kind": "function",
                "language": "python",
                "path": "b.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 5},
            },
            {
                "id": "python:c.py:1-2:c:function",
                "name": "c",
                "kind": "function",
                "language": "python",
                "path": "c.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 5},
            },
        ],
        "edges": [
            {
                "id": "edge:1",
                "src": "python:a.py:1-2:a:function",
                "dst": "python:b.py:1-2:b:function",
                "type": "calls",
                "confidence": 0.9,
                "meta": {"evidence_type": "ast_call_direct"},
            },
            {
                "id": "edge:2",
                "src": "python:b.py:1-2:b:function",
                "dst": "python:c.py:1-2:c:function",
                "type": "calls",
                "confidence": 0.9,
                "meta": {"evidence_type": "ast_call_direct"},
            },
        ],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "a"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 1  # Only allow 1 hop to trigger limit
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "limits hit: hop_limit" in out


def test_edge_from_dict_defaults(tmp_path: Path) -> None:
    """Test _edge_from_dict uses defaults for missing fields."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {},  # Empty span to test defaults
            }
        ],
        "edges": [
            {
                "id": "edge:1",
                "src": "python:src/main.py:1-2:foo:function",
                "dst": "python:src/main.py:1-2:foo:function",
                "type": "calls",
                # No line, no confidence, no meta - should use defaults
            },
        ],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "foo"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0


def test_cmd_slice_list_entries(tmp_path: Path, capsys) -> None:
    """Test --list-entries shows detected entrypoints."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/user"}]},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = True
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "Detected" in out
    assert "entrypoint" in out


def test_cmd_slice_list_entries_none(tmp_path: Path, capsys) -> None:
    """Test --list-entries when no entrypoints detected."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = True
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "No entrypoints detected" in out


def test_cmd_slice_list_entries_exclude_tests(tmp_path: Path, capsys) -> None:
    """Test --list-entries with --exclude-tests filters out test entrypoints."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/test_main.py:1-5:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]},
            },
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/user"}]},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    # First test WITHOUT --exclude-tests: should show all entrypoints
    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = True
    args.reverse = False
    args.language = None
    args.max_tier = None

    result = cmd_slice(args)
    assert result == 0
    out, _ = capsys.readouterr()
    assert "test_main" in out or "tests/test_main" in out

    # Now test WITH --exclude-tests: should NOT show test entrypoints
    args.exclude_tests = True

    result = cmd_slice(args)
    assert result == 0
    out, _ = capsys.readouterr()
    assert "test_main" not in out
    assert "tests/test_main" not in out
    assert "excluding tests" in out
    # Should still show non-test entrypoint
    assert "get_user" in out or "api.py" in out


def test_cmd_slice_list_entries_max_tier(tmp_path: Path, capsys) -> None:
    """Test --list-entries with --max-tier filters out high-tier entrypoints."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "supply_chain_tier": 1,
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/user"}]},
            },
            {
                # Vendor entrypoint with explicit route concept to ensure detection
                "id": "c:deps/hiredis/examples/api.c:1-5:vendor_api:function",
                "name": "vendor_api",
                "kind": "function",
                "language": "c",
                "path": "deps/hiredis/examples/api.c",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "supply_chain_tier": 3,
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/vendor"}]},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    # Test with --max-tier 1: should NOT show tier 3 entrypoints
    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = True
    args.reverse = False
    args.language = None
    args.max_tier = 1

    result = cmd_slice(args)
    assert result == 0
    out, _ = capsys.readouterr()
    # Should show tier 1 entrypoint
    assert "get_user" in out or "api.py" in out
    # Should NOT show tier 3 (vendor) entrypoint
    assert "hiredis" not in out
    assert "max-tier 1" in out


def test_cmd_slice_list_entries_all_filtered_out(tmp_path: Path, capsys) -> None:
    """Test --list-entries when all entrypoints are filtered out."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                # This entrypoint is in a test file
                "id": "python:tests/test_main.py:1-5:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "supply_chain_tier": 1,
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]},
            },
            {
                # This entrypoint is in vendor code (tier 3) with explicit route concept
                "id": "c:deps/vendor/api.c:1-5:vendor_handler:function",
                "name": "vendor_handler",
                "kind": "function",
                "language": "c",
                "path": "deps/vendor/api.c",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "supply_chain_tier": 3,
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/vendor"}]},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    # Test with both --exclude-tests and --max-tier 1: should filter out ALL entrypoints
    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = True
    args.list_entries = True
    args.reverse = False
    args.language = None
    args.max_tier = 1

    result = cmd_slice(args)
    assert result == 0
    out, _ = capsys.readouterr()
    # Should show "No entrypoints detected" with filter messages
    assert "No entrypoints detected" in out
    assert "--exclude-tests active" in out
    assert "--max-tier 1 active" in out


def test_cmd_slice_auto_entry(tmp_path: Path, capsys) -> None:
    """Test --entry auto uses detected entrypoints."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "route", "method": "GET", "path": "/user"}]},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "Auto-detected entry" in out
    # Check slice was created
    assert (tmp_path / "slice.json").exists()


def test_cmd_slice_auto_entry_no_entrypoints(tmp_path: Path, capsys) -> None:
    """Test --entry auto fails when no entrypoints detected."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 1  # Error exit code
    _, err = capsys.readouterr()
    assert "No entrypoints detected" in err


def test_cmd_slice_auto_entry_prefers_connected(tmp_path: Path, capsys) -> None:
    """Test --entry auto prefers well-connected entries over isolated ones.

    When multiple entries have similar confidence, the one with more
    outgoing edges produces a richer slice and should be preferred.
    """
    # Create two potential entries (both match cli_main pattern)
    # Entry 1: main() with 5 outgoing edges (well-connected)
    # Entry 2: run() with 0 outgoing edges (isolated)
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/app.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "command", "framework": "click"}]},
            },
            {
                "id": "python:src/runner.py:1-5:run:function",
                "name": "run",
                "kind": "function",
                "language": "python",
                "path": "src/runner.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "meta": {"concepts": [{"concept": "command", "framework": "click"}]},
            },
            {
                "id": "python:src/utils.py:1-5:helper1:function",
                "name": "helper1",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:6-10:helper2:function",
                "name": "helper2",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # main calls helper1, helper2, and itself (well-connected)
            {
                "id": "edge1",
                "src": "python:src/app.py:1-10:main:function",
                "dst": "python:src/utils.py:1-5:helper1:function",
                "type": "calls",
                "confidence": 0.95,
            },
            {
                "id": "edge2",
                "src": "python:src/app.py:1-10:main:function",
                "dst": "python:src/utils.py:6-10:helper2:function",
                "type": "calls",
                "confidence": 0.95,
            },
            # run has NO outgoing edges (isolated)
        ],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "auto"
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None

    result = cmd_slice(args)

    assert result == 0
    out, _ = capsys.readouterr()
    # main should be selected because it has more outgoing edges
    assert "main" in out
    assert "connectivity" in out  # Should mention connectivity
    assert "2 outgoing edges" in out  # Should report edge count


def test_cmd_slice_reverse(tmp_path: Path, capsys) -> None:
    """Test --reverse flag finds callers instead of callees."""
    # Create a behavior map where caller -> callee
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:caller:function",
                "name": "caller",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-5:callee:function",
                "name": "callee",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            {
                "id": "edge:caller->callee",
                "src": "python:src/main.py:1-5:caller:function",
                "dst": "python:src/utils.py:1-5:callee:function",
                "type": "calls",
                "confidence": 0.85,
            },
        ],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.entry = "callee"  # Start from callee
    args.out = str(tmp_path / "slice.json")
    args.input = str(input_file)
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = True  # Reverse slice
    args.language = None

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())
    # Reverse slice from callee should find caller
    assert "python:src/main.py:1-5:caller:function" in data["feature"]["node_ids"]
    assert "python:src/utils.py:1-5:callee:function" in data["feature"]["node_ids"]
    assert data["feature"]["query"]["reverse"] is True

    out, _ = capsys.readouterr()
    assert "reverse slice" in out


def test_cmd_slice_inline_embeds_full_objects(tmp_path: Path, capsys) -> None:
    """Test slice --inline embeds full node/edge objects instead of just IDs."""
    # Create behavior map with nodes and edges
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:caller:function",
                "name": "caller",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
            {
                "id": "python:src/utils.py:1-5:callee:function",
                "name": "callee",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
        ],
        "edges": [
            {
                "id": "edge:caller->callee",
                "src": "python:src/main.py:1-5:caller:function",
                "dst": "python:src/utils.py:1-5:callee:function",
                "type": "calls",
                "confidence": 0.85,
                "origin": "python-ast-v1",
                "origin_run_id": "test",
                "meta": {},
            },
        ],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(results_file)
    args.entry = "caller"
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.reverse = False
    args.language = None
    args.list_entries = False
    args.max_tier = None
    args.inline = True  # Enable inline mode

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())

    # With --inline, should have full nodes and edges arrays
    assert "nodes" in data["feature"]
    assert "edges" in data["feature"]

    # Nodes should be full objects, not just IDs
    assert len(data["feature"]["nodes"]) == 2
    node_names = {n["name"] for n in data["feature"]["nodes"]}
    assert "caller" in node_names
    assert "callee" in node_names

    # Edges should be full objects
    assert len(data["feature"]["edges"]) == 1
    assert data["feature"]["edges"][0]["type"] == "calls"

    # Should still have node_ids/edge_ids for reference
    assert "node_ids" in data["feature"]
    assert "edge_ids" in data["feature"]


def test_cmd_slice_without_inline_has_ids_only(tmp_path: Path) -> None:
    """Test slice without --inline only has IDs, not full objects."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(results_file)
    args.entry = "foo"
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.reverse = False
    args.language = None
    args.list_entries = False
    args.max_tier = None
    args.inline = False  # Disable inline mode (default)

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())

    # Without --inline, should NOT have full nodes/edges arrays
    assert "nodes" not in data["feature"]
    assert "edges" not in data["feature"]

    # Should have IDs
    assert "node_ids" in data["feature"]
    assert "edge_ids" in data["feature"]


def test_cmd_slice_flat_output(tmp_path: Path, capsys) -> None:
    """Test slice --flat outputs nodes/edges at top level (no wrapper)."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/main.py:1-5:caller:function",
                "name": "caller",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
            {
                "id": "python:src/utils.py:1-5:callee:function",
                "name": "callee",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
        ],
        "edges": [
            {
                "id": "calls:caller->callee",
                "type": "calls",
                "src": "python:src/main.py:1-5:caller:function",
                "dst": "python:src/utils.py:1-5:callee:function",
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(results_file)
    args.entry = "caller"
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.max_tier = None
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.list_entries = False
    args.reverse = False
    args.language = None
    args.inline = False  # --flat implies inline, so this should be ignored
    args.flat = True

    result = cmd_slice(args)

    assert result == 0

    data = json.loads((tmp_path / "slice.json").read_text())

    # --flat outputs a simple structure with just nodes and edges
    assert "nodes" in data
    assert "edges" in data

    # Should NOT have the wrapper structure
    assert "view" not in data
    assert "feature" not in data
    assert "schema_version" not in data

    # Nodes should be full objects
    assert len(data["nodes"]) == 2
    node_names = {n["name"] for n in data["nodes"]}
    assert "caller" in node_names
    assert "callee" in node_names

    # Edges should be full objects
    assert len(data["edges"]) == 1
    assert data["edges"][0]["type"] == "calls"

    out, _ = capsys.readouterr()
    assert "[hypergumbo slice]" in out


def test_cmd_slice_ambiguous_entry_error(tmp_path: Path, capsys) -> None:
    """Test slice command handles ambiguous entry with helpful error message."""
    # Create behavior map with same symbol name in different files/languages
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/app.py:1-5:ping:function",
                "name": "ping",
                "kind": "function",
                "language": "python",
                "path": "src/app.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            },
            {
                "id": "typescript:web/client.ts:1-5:ping:function",
                "name": "ping",
                "kind": "function",
                "language": "typescript",
                "path": "web/client.ts",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "origin": "typescript-ast-v1",
                "origin_run_id": "test",
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(results_file)
    args.entry = "ping"  # Ambiguous - matches both
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.reverse = False
    args.language = None
    args.list_entries = False
    args.max_tier = None

    result = cmd_slice(args)

    # Should fail with error
    assert result == 1

    # Error message should include helpful info
    _, err = capsys.readouterr()
    assert "Ambiguous entry" in err
    assert "ping" in err
    assert "src/app.py" in err
    assert "web/client.ts" in err
    assert "Use a full node ID" in err


def test_cmd_catalog_shows_all_passes(capsys, tmp_path, monkeypatch) -> None:
    """Catalog shows all passes including extras by default."""
    # Run from empty temp dir to avoid scanning full repo
    monkeypatch.chdir(tmp_path)

    args = FakeArgs()

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "Available Passes:" in out
    assert "python-ast-v1" in out
    assert "html-pattern-v1" in out
    assert "javascript-ts-v1" in out  # extras now shown by default
    # v1.1.x: Show framework patterns instead of deprecated packs
    assert "Available Framework Patterns (v1.1.x):" in out
    assert "--frameworks" in out
    assert "Packs are deprecated" in out


def test_cmd_catalog_shows_suggestions(capsys, tmp_path, monkeypatch) -> None:
    """Catalog shows suggested passes based on current directory."""
    # Create Python file in temp directory
    (tmp_path / "main.py").write_text("print('hello')")

    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    args = FakeArgs()

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "Suggested for current repo:" in out
    assert "python-ast-v1" in out


def test_cmd_catalog_skips_large_directory(capsys, tmp_path, monkeypatch) -> None:
    """Catalog skips language detection for large directories."""
    # Create many files to trigger large directory detection
    for i in range(250):  # More than 200 entries
        (tmp_path / f"file{i}.txt").write_text("content")

    # Change to temp directory
    monkeypatch.chdir(tmp_path)
    args = FakeArgs()

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "Large directory detected" in out
    assert "skipping language suggestions" in out
    # Should still show passes and frameworks
    assert "Available Passes:" in out
    assert "Available Framework Patterns" in out


def test_cmd_catalog_prints_output_summary(capsys, tmp_path, monkeypatch) -> None:
    """Catalog prints output summary to stdout."""
    # Create a minimal directory
    (tmp_path / "main.py").write_text("def main(): pass\n")
    monkeypatch.chdir(tmp_path)

    args = FakeArgs()
    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo catalog] Generated 0 artifact(s)" in out
    assert "Output: stdout" in out


def test_main_with_run(tmp_path: Path) -> None:
    out_file = tmp_path / "output.json"
    result = main(["run", str(tmp_path), "--out", str(out_file)])
    assert result == 0
    assert out_file.exists()


def test_main_with_slice(tmp_path: Path) -> None:
    # Create a behavior map file
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 10},
            }
        ],
        "edges": [],
    }
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    out_file = tmp_path / "slice.json"
    result = main(["slice", str(tmp_path), "--entry", "foo", "--out", str(out_file)])
    assert result == 0
    assert out_file.exists()


def test_main_with_catalog(tmp_path, monkeypatch) -> None:
    # Run from empty temp dir to avoid scanning full repo
    monkeypatch.chdir(tmp_path)

    result = main(["catalog"])
    assert result == 0


def test_cmd_sketch_config_extraction_modes(tmp_path: Path, capsys) -> None:
    """Test --config-extraction flag parses correctly.

    Note: Embedding mode behavior is thoroughly tested in test_sketch.py.
    This test validates CLI argument parsing with fast heuristic mode.
    """
    # Create a simple package.json
    (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

    # Test heuristic mode (fast, validates CLI plumbing)
    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 1000
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"

    result = cmd_sketch(args)
    assert result == 0
    out, _ = capsys.readouterr()
    assert "test" in out  # Should include package name


def test_main_sketch_config_extraction_flag(tmp_path: Path) -> None:
    """Test sketch command with --config-extraction flag via main()."""
    (tmp_path / "package.json").write_text('{"name": "cli-test", "version": "2.0.0"}')

    # Test with explicit heuristic mode
    result = main(["sketch", str(tmp_path), "--config-extraction", "heuristic"])
    assert result == 0


def test_cmd_sketch_nonexistent_path(capsys) -> None:
    """Test cmd_sketch with nonexistent path returns error."""
    args = FakeArgs()
    args.path = "/nonexistent/path/that/does/not/exist"
    args.tokens = None
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"

    result = cmd_sketch(args)
    assert result == 1
    _, err = capsys.readouterr()
    assert "does not exist" in err


def test_cmd_sketch_warns_about_git_root(tmp_path: Path, capsys) -> None:
    """Test cmd_sketch warns when analyzing a subdirectory of a git repo."""
    # Create a git repo structure
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    # Create a subdirectory with some code
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(src_dir)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800

    result = cmd_sketch(args)
    assert result == 0
    _, err = capsys.readouterr()
    assert "NOTE: Your repo root appears to be at" in err
    assert str(tmp_path) in err
    assert "You may want to run" in err
    # Verify flags are preserved in suggested command
    assert "-t 100" in err


def test_cmd_sketch_git_warning_with_exclude_tests(tmp_path: Path, capsys) -> None:
    """Test git root warning includes -x flag when exclude_tests is True."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(src_dir)
    args.tokens = 100
    args.exclude_tests = True  # This should be included in suggested command
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800

    result = cmd_sketch(args)
    assert result == 0
    _, err = capsys.readouterr()
    assert "-x" in err
    assert "-t 100" in err


def test_cmd_sketch_no_warning_at_git_root(tmp_path: Path, capsys) -> None:
    """Test cmd_sketch does not warn when already at git root."""
    # Create a git repo structure
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800

    result = cmd_sketch(args)
    assert result == 0
    _, err = capsys.readouterr()
    assert "NOTE: Your repo root" not in err


def test_find_git_root_finds_repo(tmp_path: Path) -> None:
    """Test _find_git_root finds the git root directory."""
    # Create nested structure: tmp/.git and tmp/a/b/c
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)

    result = _find_git_root(nested)
    assert result == tmp_path


def test_find_git_root_returns_none_outside_repo(tmp_path: Path) -> None:
    """Test _find_git_root returns None when not in a git repo."""
    # No .git directory
    subdir = tmp_path / "some" / "dir"
    subdir.mkdir(parents=True)

    result = _find_git_root(subdir)
    assert result is None


def test_find_git_root_at_root_itself(tmp_path: Path) -> None:
    """Test _find_git_root when starting at the git root."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    result = _find_git_root(tmp_path)
    assert result == tmp_path


def test_cmd_run_includes_entrypoints(tmp_path: Path) -> None:
    """Test that cmd_run includes entrypoints field in the JSON output."""
    # Create a simple Python file
    (tmp_path / "app.py").write_text("""
def helper():
    return "Hello"
""")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    result = cmd_run(args)
    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())

    # Verify entrypoints section exists in output structure
    assert "entrypoints" in data
    assert isinstance(data["entrypoints"], list)


def test_cmd_slice_smart_json_detection(tmp_path: Path, capsys) -> None:
    """Test that slice auto-detects JSON files as input."""
    # Create a behavior map file
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:main.py:1-2:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
                "meta": {"concepts": [{"concept": "command", "framework": "click"}]},
            }
        ],
        "edges": [],
    }
    json_file = tmp_path / "results.json"
    json_file.write_text(json.dumps(behavior_map))

    # Call slice with just the JSON file path (no --input flag)
    args = FakeArgs()
    args.path = str(json_file)  # JSON file as path, not --input
    args.input = None
    args.entry = "auto"
    args.list_entries = True
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.reverse = False
    args.max_tier = None
    args.language = None
    args.inline = False

    result = cmd_slice(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Should detect the main function as an entrypoint
    assert "main" in out or "entrypoint" in out.lower()


def test_cmd_slice_smart_json_detection_does_not_override_explicit_input(
    tmp_path: Path, capsys
) -> None:
    """Test that --input flag takes precedence over smart detection."""
    # Create two behavior map files with different "main" functions
    behavior_map1 = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:a.py:1-2:main_from_file1:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "a.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
                "meta": {"concepts": [{"concept": "command", "framework": "click"}]},
            }
        ],
        "edges": [],
    }
    behavior_map2 = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:b.py:1-2:main_from_file2:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "b.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
                "meta": {"concepts": [{"concept": "command", "framework": "click"}]},
            }
        ],
        "edges": [],
    }

    json_file1 = tmp_path / "results1.json"
    json_file1.write_text(json.dumps(behavior_map1))
    json_file2 = tmp_path / "results2.json"
    json_file2.write_text(json.dumps(behavior_map2))

    # Call slice with JSON file as path but also explicit --input
    args = FakeArgs()
    args.path = str(json_file1)  # This would be auto-detected
    args.input = str(json_file2)  # But explicit --input should win
    args.entry = "auto"
    args.list_entries = True
    args.out = str(tmp_path / "slice.json")
    args.max_hops = 3
    args.max_files = 20
    args.min_confidence = 0.0
    args.exclude_tests = False
    args.reverse = False
    args.max_tier = None
    args.language = None
    args.inline = False

    result = cmd_slice(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Should use json_file2 (explicit --input), so b.py should appear (not a.py)
    assert "b.py" in out
    assert "a.py" not in out


def test_cmd_sketch_readme_debug_with_readme(tmp_path: Path, capsys) -> None:
    """Test --readme-debug flag shows extraction debug info."""
    # Create a README with a project description
    (tmp_path / "README.md").write_text("""# Test Project

This is a test project for validating README extraction.
It demonstrates embedding-based description extraction.
""")
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = True

    result = cmd_sketch(args)
    assert result == 0

    _, err = capsys.readouterr()
    # Should show debug output
    assert "README Extraction Debug" in err
    # Should show k-scores or similar debug info
    assert "k-scores" in err or "Elapsed" in err or "Final k" in err


def test_cmd_sketch_readme_debug_no_readme(tmp_path: Path, capsys) -> None:
    """Test --readme-debug flag when no README exists."""
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = True

    result = cmd_sketch(args)
    assert result == 0

    _, err = capsys.readouterr()
    # Should show message about no README
    assert "No README found" in err


def test_print_output_summary_with_cached_artifacts(tmp_path: Path, capsys) -> None:
    """Test that output summary shows cached vs generated artifacts."""
    import io

    # Create some test files
    cached_file = tmp_path / "cached.json"
    cached_file.write_text("{}")
    new_file = tmp_path / "new.json"
    new_file.write_text("{}")

    # Test with both cached and generated artifacts
    output = io.StringIO()
    _print_output_summary(
        "test",
        artifacts=[cached_file, new_file],
        cached_artifacts={cached_file},
        file=output,
    )

    result = output.getvalue()
    # Should show both generated and cached counts
    assert "[hypergumbo test]" in result
    assert "Generated 1" in result
    assert "Using 1 cached" in result
    # Should show [cached] prefix for cached file
    assert "[cached]" in result
    assert "cached.json" in result
    assert "new.json" in result


def test_print_output_summary_all_cached(tmp_path: Path) -> None:
    """Test output summary when all artifacts are cached."""
    import io

    cached_file = tmp_path / "cached.json"
    cached_file.write_text("{}")

    output = io.StringIO()
    _print_output_summary(
        "test",
        artifacts=[cached_file],
        cached_artifacts={cached_file},
        file=output,
    )

    result = output.getvalue()
    # Should show only cached count (no "Generated")
    assert "Using 1 cached" in result
    assert "[cached]" in result


def test_cmd_sketch_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Test that sketch prints output summary to stdout."""
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = False

    result = cmd_sketch(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Should show output summary message (format: "Generated N" or "Using N cached")
    assert "[hypergumbo sketch]" in out
    assert "Generated" in out
    assert "Output: stdout" in out
    # Should show path to cached results
    assert "hypergumbo.results.json" in out


def test_cmd_sketch_input_file_not_found(tmp_path: Path, capsys) -> None:
    """Test that sketch returns error when --input file doesn't exist."""
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = False
    args.input = str(tmp_path / "nonexistent.json")

    result = cmd_sketch(args)
    assert result == 1

    _, err = capsys.readouterr()
    assert "Error: Input file not found" in err


def test_cmd_sketch_input_uses_cached_results(tmp_path: Path, capsys) -> None:
    """Test that sketch uses cached results from --input file."""
    (tmp_path / "main.py").write_text("def main(): pass\n")

    # Create a cached results file
    cached_results = {
        "profile": {
            "languages": {"python": {"files": 5, "loc": 500}},
            "frameworks": ["flask"],
            "framework_mode": "auto",
        },
        "nodes": [
            {
                "id": "python:src/api.py:10-20:cached_function:function",
                "name": "cached_function",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 10, "end_line": 20, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "supply_chain": {"tier": 1, "reason": "first_party"},
            }
        ],
        "edges": [],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(cached_results))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 2000
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = False
    args.input = str(results_file)

    result = cmd_sketch(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Cached symbol should appear in output
    assert "cached_function" in out
    # Cached framework should appear
    assert "flask" in out.lower() or "Flask" in out


def test_cmd_sketch_input_staleness_warning(tmp_path: Path, capsys) -> None:
    """Test that sketch warns when --input file is stale."""
    import time

    # Create results file first
    cached_results = {
        "profile": {
            "languages": {"python": {"files": 1, "loc": 10}},
            "frameworks": [],
            "framework_mode": "auto",
        },
        "nodes": [],
        "edges": [],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(cached_results))

    # Wait briefly then create a source file (newer than results)
    time.sleep(0.1)
    (tmp_path / "main.py").write_text("def main(): pass\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = False
    args.input = str(results_file)

    result = cmd_sketch(args)
    assert result == 0

    _, err = capsys.readouterr()
    # Should warn about stale results
    assert "may be stale" in err
    assert "Run 'hypergumbo run' to regenerate" in err


def test_cmd_sketch_input_no_staleness_warning_when_fresh(tmp_path: Path, capsys) -> None:
    """Test that sketch does not warn when --input file is fresh."""
    import time

    # Create a source file first
    (tmp_path / "main.py").write_text("def main(): pass\n")

    # Wait briefly then create results file (newer than source)
    time.sleep(0.1)
    cached_results = {
        "profile": {
            "languages": {"python": {"files": 1, "loc": 10}},
            "frameworks": [],
            "framework_mode": "auto",
        },
        "nodes": [],
        "edges": [],
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(cached_results))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.tokens = 100
    args.exclude_tests = False
    args.first_party_priority = True
    args.extra_excludes = []
    args.config_extraction_mode = "heuristic"
    args.verbose = False
    args.max_config_files = 15
    args.fleximax_lines = 100
    args.max_chunk_chars = 800
    args.language_proportional = False
    args.progress = False
    args.readme_debug = False
    args.input = str(results_file)

    result = cmd_sketch(args)
    assert result == 0

    _, err = capsys.readouterr()
    # Should NOT warn about stale results
    assert "stale" not in err


def test_sanitize_filename_part_simple() -> None:
    """Test sanitize filename part with normal input."""
    assert _sanitize_filename_part("hello") == "hello"
    assert _sanitize_filename_part("my_func") == "my_func"
    assert _sanitize_filename_part("test-name") == "test-name"


def test_sanitize_filename_part_special_chars() -> None:
    """Test sanitize filename part replaces special characters."""
    assert _sanitize_filename_part("hello:world") == "hello_world"
    assert _sanitize_filename_part("a/b/c") == "a_b_c"
    assert _sanitize_filename_part("foo::bar::baz") == "foo_bar_baz"


def test_sanitize_filename_part_collapse_underscores() -> None:
    """Test sanitize filename part collapses multiple underscores."""
    assert _sanitize_filename_part("a___b") == "a_b"
    assert _sanitize_filename_part("x::y::z") == "x_y_z"


def test_sanitize_filename_part_strip_underscores() -> None:
    """Test sanitize filename part strips leading/trailing underscores."""
    assert _sanitize_filename_part("_hello_") == "hello"
    assert _sanitize_filename_part("::foo::") == "foo"


def test_sanitize_filename_part_truncates() -> None:
    """Test sanitize filename part truncates long names."""
    long_name = "a" * 100
    result = _sanitize_filename_part(long_name, max_len=50)
    assert len(result) == 50


def test_sanitize_filename_part_empty_becomes_unnamed() -> None:
    """Test sanitize filename part handles empty/all-special input."""
    assert _sanitize_filename_part("") == "unnamed"
    assert _sanitize_filename_part(":::") == "unnamed"


def test_cmd_slice_default_output_includes_entry_name(tmp_path: Path, capsys) -> None:
    """Test that slice default output filename includes sanitized entry name."""
    import os
    # Change to tmp_path so output goes there
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Create a behavior map file
        behavior_map = {
            "schema_version": "0.1.0",
            "nodes": [
                {
                    "id": "python:src/main.py:1-2:my_func:function",
                    "name": "my_func",
                    "kind": "function",
                    "language": "python",
                    "path": "src/main.py",
                    "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 10},
                }
            ],
            "edges": [],
        }
        (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

        args = FakeArgs()
        args.path = str(tmp_path)
        args.entry = "my_func"
        args.out = "slice.json"  # Default value
        args.input = None
        args.max_hops = 3
        args.max_files = 20
        args.min_confidence = 0.0
        args.exclude_tests = False
        args.list_entries = False
        args.reverse = False
        args.language = None

        result = cmd_slice(args)

        assert result == 0

        # Output should be slice.my_func.json, not slice.json
        assert (tmp_path / "slice.my_func.json").exists()
        assert not (tmp_path / "slice.json").exists()

        out, _ = capsys.readouterr()
        assert "slice.my_func.json" in out
    finally:
        os.chdir(original_cwd)


def test_cmd_compact_converts_behavior_map(tmp_path: Path) -> None:
    """Test that cmd_compact converts a behavior map to compact form."""
    # Create a test behavior map with some symbols
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-01-25T00:00:00Z",
        "nodes": [
            {
                "id": f"python:main.py:1-10:func{i}:function",
                "name": f"func{i}",
                "kind": "function",
                "language": "python",
                "path": "main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 0},
            }
            for i in range(20)
        ],
        "edges": [
            {
                "id": f"edge:python:main.py:1-10:func{i}:function->python:main.py:1-10:func{i+1}:function",
                "src": f"python:main.py:1-10:func{i}:function",
                "dst": f"python:main.py:1-10:func{i+1}:function",
                "edge_type": "calls",
                "line": 5,
                "confidence": 0.9,
            }
            for i in range(19)
        ],
        "entrypoints": [],
        "analysis_runs": [],
    }

    input_path = tmp_path / "hg.json"
    input_path.write_text(json.dumps(behavior_map))

    output_path = tmp_path / "hg.compact.json"

    args = FakeArgs()
    args.input = str(input_path)
    args.out = str(output_path)
    args.max_symbols = 10
    args.min_symbols = 5
    args.coverage = 0.8
    args.no_connectivity = False

    result = cmd_compact(args)

    assert result == 0
    assert output_path.exists()

    compact_map = json.loads(output_path.read_text())
    assert compact_map["view"] == "compact"
    # Should have limited number of nodes
    assert len(compact_map["nodes"]) <= 10
    # Should have nodes_summary with omitted info
    assert "nodes_summary" in compact_map


def test_cmd_compact_to_stdout(tmp_path: Path, capsys) -> None:
    """Test that cmd_compact prints to stdout when no --out specified."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-01-25T00:00:00Z",
        "nodes": [
            {
                "id": "python:main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 0},
            }
        ],
        "edges": [],
        "entrypoints": [],
        "analysis_runs": [],
    }

    input_path = tmp_path / "hg.json"
    input_path.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.input = str(input_path)
    args.out = None
    args.max_symbols = 100
    args.min_symbols = 10
    args.coverage = 0.8
    args.no_connectivity = False

    result = cmd_compact(args)

    assert result == 0
    out, _ = capsys.readouterr()
    compact_map = json.loads(out)
    assert compact_map["view"] == "compact"


def test_cmd_compact_file_not_found(tmp_path: Path) -> None:
    """Test that cmd_compact returns error for non-existent file."""
    args = FakeArgs()
    args.input = str(tmp_path / "nonexistent.json")
    args.out = None
    args.max_symbols = 100
    args.min_symbols = 10
    args.coverage = 0.8
    args.no_connectivity = False

    result = cmd_compact(args)

    assert result == 1
