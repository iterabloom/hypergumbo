import json
import subprocess
import sys
from pathlib import Path


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
    assert data["schema_version"] == "0.1.0"
    assert data["view"] == "behavior_map"

