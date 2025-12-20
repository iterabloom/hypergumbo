import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


def test_run_behavior_map_writes_behavior_map_json(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    out_path = tmp_path / "hypergumbo.results.json"

    run_behavior_map(repo_root=repo_root, out_path=out_path)

    assert out_path.is_file()

    data = json.loads(out_path.read_text())

    assert data["schema_version"] == "0.1.0"
    assert data["view"] == "behavior_map"
    assert data["confidence_model"] == "hypergumbo-evidence-v1"
    assert data["analysis_incomplete"] is False
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)

