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
        [sys.executable, "-m", "hypergumbo", "run", str(project_root), "--out", str(out_path)],
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
