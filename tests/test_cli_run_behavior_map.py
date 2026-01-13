import json
import subprocess
import sys
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION


def test_cli_run_creates_behavior_map(tmp_path: Path) -> None:
    # Project root is the repo root (two levels up from this test file)
    project_root = Path(__file__).resolve().parents[1]

    out_path = tmp_path / "hypergumbo.results.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "hypergumbo", "run",
            str(project_root), "--out", str(out_path),
            "--max-files", "5",  # Limit files per analyzer for faster test
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    # Help debug if the CLI exits non-zero
    assert result.returncode == 0, f"stderr was:\n{result.stderr}"

    assert out_path.exists(), "hypergumbo.results.json was not created"

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["view"] == "behavior_map"


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
    from hypergumbo.cli import run_behavior_map

    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    # Run with budgets disabled (only main output)
    out_path = tmp_path / "results.json"
    generated = run_behavior_map(tmp_path, out_path, budgets="none")

    assert len(generated) == 1
    assert generated[0] == out_path
    assert out_path.exists()


def test_run_behavior_map_returns_budget_files(tmp_path: Path) -> None:
    """Test that run_behavior_map returns budget files when generated."""
    from hypergumbo.cli import run_behavior_map

    # Create a simple Python file
    (tmp_path / "test.py").write_text("def hello(): pass\n")

    # Run with custom budgets
    out_path = tmp_path / "results.json"
    generated = run_behavior_map(tmp_path, out_path, budgets="4k,16k")

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
    # Check that artifact summary is printed
    assert "[hypergumbo run] Generated 1 artifact(s):" in result.stdout
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
    # Check artifact summary includes budget files
    assert "[hypergumbo run] Generated 3 artifact(s):" in result.stdout
    assert "results.4k.json" in result.stdout
    assert "results.16k.json" in result.stdout
    assert str(out_path) in result.stdout
