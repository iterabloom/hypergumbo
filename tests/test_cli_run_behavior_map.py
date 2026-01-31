import json
import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core.schema import SCHEMA_VERSION


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers is installed."""
    try:
        import sentence_transformers
        del sentence_transformers
        return True
    except ImportError:
        return False


def test_cli_run_creates_behavior_map(tmp_path: Path) -> None:
    """Test that CLI run command creates a valid behavior map."""
    # Create a small demo project instead of analyzing the full hypergumbo repo
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main():\n    print('hello')\n")
    (src_dir / "utils.py").write_text("def helper(x: int) -> str:\n    return str(x)\n")
    (tmp_path / "README.md").write_text("# Demo Project\n\nA small test project.\n")

    out_path = tmp_path / "hypergumbo.results.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "hypergumbo", "run",
            str(tmp_path), "--out", str(out_path),
        ],
        capture_output=True,
        text=True,
    )

    # Help debug if the CLI exits non-zero
    assert result.returncode == 0, f"stderr was:\n{result.stderr}"

    assert out_path.exists(), "hypergumbo.results.json was not created"

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["view"] == "behavior_map"
    # Verify basic structure
    assert "profile" in data
    assert "python" in data["profile"]["languages"]


def test_cli_run_with_max_files(tmp_path: Path) -> None:
    """Test that --max-files option limits files analyzed per language."""
    # Create a mini project with multiple Python files
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for i in range(5):
        (src_dir / f"file{i}.py").write_text(f"def func{i}(): pass\n")

    out_path = tmp_path / "results.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "hypergumbo", "run",
            str(tmp_path),
            "--out", str(out_path),
            "--max-files", "2",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"stderr was:\n{result.stderr}"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    # With max-files=2, we should have at most 2 files analyzed per analyzer
    # Check that limits were recorded
    assert "limits" in data
    limits = data["limits"]
    assert limits.get("max_files_per_analyzer") == 2


def test_run_behavior_map_returns_generated_files(tmp_path: Path) -> None:
    """Test that run_behavior_map returns list of generated file paths."""
    from hypergumbo_core.cli import run_behavior_map

    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    # Run with budgets disabled (only main output)
    out_path = tmp_path / "results.json"
    generated = run_behavior_map(tmp_path, out_path, budgets="none", include_sketch_precomputed=False)

    assert len(generated) == 1
    assert generated[0] == out_path
    assert out_path.exists()


def test_run_behavior_map_returns_budget_files(tmp_path: Path) -> None:
    """Test that run_behavior_map returns budget files when generated."""
    from hypergumbo_core.cli import run_behavior_map

    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    # Run with custom budgets
    out_path = tmp_path / "results.json"
    generated = run_behavior_map(tmp_path, out_path, budgets="4k,16k", include_sketch_precomputed=False)

    # Should have 3 files: 2 budget files + main output
    assert len(generated) == 3
    assert out_path in generated
    # Check budget files were generated
    budget_4k = tmp_path / "results.4k.json"
    budget_16k = tmp_path / "results.16k.json"
    assert budget_4k in generated
    assert budget_16k in generated
    assert budget_4k.exists()
    assert budget_16k.exists()


def test_cli_run_prints_artifact_summary(tmp_path: Path) -> None:
    """Test that cli run command prints artifact summary."""
    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    out_path = tmp_path / "results.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "hypergumbo", "run",
            str(tmp_path),
            "--out", str(out_path),
            "--budgets", "none",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Check that artifact summary is printed (format: "Generated N")
    assert "[hypergumbo run] Generated 1" in result.stdout
    assert str(out_path) in result.stdout


def test_cli_run_prints_budget_files_in_summary(tmp_path: Path) -> None:
    """Test that cli run prints budget files in artifact summary."""
    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    out_path = tmp_path / "results.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "hypergumbo", "run",
            str(tmp_path),
            "--out", str(out_path),
            "--budgets", "4k,16k",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Check artifact summary includes budget files (format: "Generated N")
    assert "[hypergumbo run] Generated 3" in result.stdout
    assert "results.4k.json" in result.stdout
    assert "results.16k.json" in result.stdout
    assert str(out_path) in result.stdout


@pytest.mark.skipif(
    not _has_sentence_transformers(),
    reason="sentence-transformers not installed"
)
def test_run_behavior_map_stores_sketch_precomputed(tmp_path: Path) -> None:
    """Test that run_behavior_map stores sketch_precomputed data.

    This test requires sentence-transformers because it tests the HYBRID
    config extraction mode which uses embeddings.
    """
    from hypergumbo_core.cli import run_behavior_map

    # Create a mini project with config file and README
    (tmp_path / "main.py").write_text("def hello(): pass\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "testproj"\n')
    (tmp_path / "README.md").write_text("# Test\n\nThis is a test project.\n")

    out_path = tmp_path / "results.json"
    run_behavior_map(tmp_path, out_path, budgets="none")

    data = json.loads(out_path.read_text())

    # Check that sketch_precomputed is stored
    assert "sketch_precomputed" in data
    precomputed = data["sketch_precomputed"]

    # Check config_info (should have project name)
    assert "config_info" in precomputed
    assert "testproj" in precomputed["config_info"]

    # Check vocabulary (should be a list)
    assert "vocabulary" in precomputed
    assert isinstance(precomputed["vocabulary"], list)

    # Check readme_description (should have extracted text)
    assert "readme_description" in precomputed
    # May be None or a string (embedding vs heuristic fallback)
    assert precomputed["readme_description"] is None or isinstance(
        precomputed["readme_description"], str
    )
