"""Tests for CLI commands to achieve 100% coverage."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import (
    cmd_init,
    cmd_run,
    cmd_slice,
    cmd_catalog,
    cmd_export_capsule,
    cmd_sketch,
    main,
    _find_git_root,
)


class FakeArgs:
    """Minimal namespace for testing command functions."""
    pass


def test_cmd_init_creates_capsule(tmp_path: Path, capsys) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.capabilities = "python,javascript"
    args.assistant = "template"
    args.llm_input = "tier1"

    result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()

    data = json.loads(capsule_path.read_text())
    assert data["capabilities"] == ["python", "javascript"]
    assert data["assistant"] == "template"
    assert data["llm_input"] == "tier1"

    out, _ = capsys.readouterr()
    assert "[hypergumbo init]" in out


def test_cmd_init_empty_capabilities(tmp_path: Path) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.capabilities = ""
    args.assistant = "template"
    args.llm_input = "tier0"

    result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    data = json.loads(capsule_path.read_text())
    assert data["capabilities"] == []


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
    from hypergumbo.ir import Symbol, Span, AnalysisRun
    from hypergumbo.analyze.js_ts import JsAnalysisResult

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

    with patch("hypergumbo.analyze.js_ts.analyze_javascript", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.js_ts import JsAnalysisResult
    from hypergumbo.analyze.php import PhpAnalysisResult

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

    with patch("hypergumbo.analyze.js_ts.analyze_javascript", return_value=mock_js_result), \
         patch("hypergumbo.analyze.php.analyze_php", return_value=mock_php_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.php import PhpAnalysisResult

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

    with patch("hypergumbo.analyze.php.analyze_php", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.c import CAnalysisResult

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

    with patch("hypergumbo.analyze.c.analyze_c", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.java import JavaAnalysisResult

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

    with patch("hypergumbo.analyze.java.analyze_java", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.elixir import ElixirAnalysisResult

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

    with patch("hypergumbo.analyze.elixir.analyze_elixir", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.rust import RustAnalysisResult

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

    with patch("hypergumbo.analyze.rust.analyze_rust", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.go import GoAnalysisResult

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

    with patch("hypergumbo.analyze.go.analyze_go", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.ruby import RubyAnalysisResult

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

    with patch("hypergumbo.analyze.ruby.analyze_ruby", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.kotlin import KotlinAnalysisResult

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

    with patch("hypergumbo.analyze.kotlin.analyze_kotlin", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.swift import SwiftAnalysisResult

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

    with patch("hypergumbo.analyze.swift.analyze_swift", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.scala import ScalaAnalysisResult

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

    with patch("hypergumbo.analyze.scala.analyze_scala", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.lua import LuaAnalysisResult

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

    with patch("hypergumbo.analyze.lua.analyze_lua", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.haskell import HaskellAnalysisResult

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

    with patch("hypergumbo.analyze.haskell.analyze_haskell", return_value=mock_result):
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
    from hypergumbo.ir import AnalysisRun
    from hypergumbo.analyze.ocaml import OCamlAnalysisResult

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

    with patch("hypergumbo.analyze.ocaml.analyze_ocaml", return_value=mock_result):
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
    # Create a simple Python file to analyze
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def hello():\n    pass\n")

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
                "stable_id": "get",  # Decorator marker
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
                "stable_id": "get",  # Decorator marker for HTTP route
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
            },
            {
                "id": "python:src/runner.py:1-5:run:function",
                "name": "run",
                "kind": "function",
                "language": "python",
                "path": "src/runner.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
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


def test_cmd_catalog_shows_all_passes(capsys) -> None:
    """Catalog shows all passes including extras by default."""
    args = FakeArgs()

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "Available Passes:" in out
    assert "python-ast-v1" in out
    assert "html-pattern-v1" in out
    assert "javascript-ts-v1" in out  # extras now shown by default
    assert "Available Packs:" in out


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


def test_cmd_export_capsule(tmp_path: Path, capsys) -> None:
    """Export capsule creates tarball."""
    # Setup capsule directory
    capsule_dir = tmp_path / ".hypergumbo"
    capsule_dir.mkdir()
    (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
    (capsule_dir / "capsule_plan.json").write_text(
        '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.shareable = True
    args.out = str(tmp_path / "capsule.tar.gz")

    result = cmd_export_capsule(args)

    assert result == 0
    assert (tmp_path / "capsule.tar.gz").exists()

    out, _ = capsys.readouterr()
    assert "[hypergumbo export-capsule]" in out
    assert "shareable" in out


def test_cmd_export_capsule_no_capsule(tmp_path: Path, capsys) -> None:
    """Export fails if no capsule exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.shareable = False
    args.out = str(tmp_path / "capsule.tar.gz")

    result = cmd_export_capsule(args)

    assert result == 1

    _, err = capsys.readouterr()
    assert "No capsule found" in err


def test_main_with_init(tmp_path: Path) -> None:
    result = main(["init", str(tmp_path)])
    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()


def test_main_with_run(tmp_path: Path) -> None:
    out_file = tmp_path / "output.json"
    result = main(["run", str(tmp_path), "--out", str(out_file)])
    assert result == 0
    assert out_file.exists()


def test_main_with_slice(tmp_path: Path) -> None:
    # Create a simple Python file
    (tmp_path / "main.py").write_text("def foo():\n    pass\n")
    out_file = tmp_path / "slice.json"
    result = main(["slice", str(tmp_path), "--entry", "foo", "--out", str(out_file)])
    assert result == 0
    assert out_file.exists()


def test_main_with_catalog() -> None:
    result = main(["catalog"])
    assert result == 0


def test_main_with_export_capsule(tmp_path: Path) -> None:
    """Main with export-capsule creates tarball."""
    # Setup capsule directory
    capsule_dir = tmp_path / ".hypergumbo"
    capsule_dir.mkdir()
    (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
    (capsule_dir / "capsule_plan.json").write_text(
        '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
    )

    out_path = tmp_path / "capsule.tar.gz"
    result = main(["export-capsule", str(tmp_path), "--out", str(out_path)])
    assert result == 0
    assert out_path.exists()


def test_cmd_sketch_config_extraction_modes(tmp_path: Path, capsys) -> None:
    """Test --config-extraction flag with all modes."""
    # Create a simple package.json
    (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

    # Test default (heuristic) mode
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

    # Test embedding mode (will use heuristic if sentence-transformers unavailable)
    args.config_extraction_mode = "embedding"
    result = cmd_sketch(args)
    assert result == 0

    # Test hybrid mode
    args.config_extraction_mode = "hybrid"
    result = cmd_sketch(args)
    assert result == 0


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
    """Test that cmd_run includes entrypoints in the JSON output."""
    # Create a Python file with a main function (detected as CLI entrypoint)
    (tmp_path / "main.py").write_text("""
def main():
    print("Hello")

if __name__ == "__main__":
    main()
""")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    result = cmd_run(args)
    assert result == 0

    data = json.loads((tmp_path / "results.json").read_text())

    # Verify entrypoints section exists
    assert "entrypoints" in data
    assert isinstance(data["entrypoints"], list)

    # Should have at least one entrypoint (the main function)
    assert len(data["entrypoints"]) >= 1

    # Check entrypoint structure
    ep = data["entrypoints"][0]
    assert "symbol_id" in ep
    assert "kind" in ep
    assert "confidence" in ep
    assert "label" in ep


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
                "name": "main",  # This would be detected as CLI entrypoint
                "kind": "function",
                "language": "python",
                "path": "a.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
            }
        ],
        "edges": [],
    }
    behavior_map2 = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:b.py:1-2:main_from_file2:function",
                "name": "main",  # This would be detected as CLI entrypoint
                "kind": "function",
                "language": "python",
                "path": "b.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 0},
                "origin": "python-ast-v1",
                "origin_run_id": "test",
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
