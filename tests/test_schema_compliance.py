"""Tests for schema compliance with the hypergumbo spec."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


def test_output_has_analysis_runs(tmp_path: Path) -> None:
    """Output should include analysis_runs with provenance info."""
    py_file = tmp_path / "app.py"
    py_file.write_text("def main():\n    pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should have analysis_runs array with at least one run
    assert "analysis_runs" in data
    assert len(data["analysis_runs"]) >= 1

    run = data["analysis_runs"][0]
    # Required fields per spec
    assert "execution_id" in run
    assert "pass" in run
    assert "version" in run
    assert "files_analyzed" in run
    assert "started_at" in run
    assert "duration_ms" in run


def test_nodes_have_origin_fields(tmp_path: Path) -> None:
    """Nodes should have origin provenance fields."""
    py_file = tmp_path / "app.py"
    py_file.write_text("def main():\n    pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert len(data["nodes"]) == 1
    node = data["nodes"][0]

    # Origin fields per spec
    assert "origin" in node
    assert "origin_run_id" in node
    # stable_id and shape_id must be present (can be null)
    assert "stable_id" in node
    assert "shape_id" in node


def test_nodes_have_span_with_columns(tmp_path: Path) -> None:
    """Nodes should have span object with line and column info."""
    py_file = tmp_path / "app.py"
    py_file.write_text("def main():\n    pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    node = data["nodes"][0]
    assert "span" in node
    span = node["span"]
    assert "start_line" in span
    assert "end_line" in span
    assert "start_col" in span
    assert "end_col" in span


def test_edges_have_required_fields(tmp_path: Path) -> None:
    """Edges should have id, confidence, and origin fields."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert len(data["edges"]) == 1
    edge = data["edges"][0]

    # Required edge fields per spec
    assert "id" in edge
    assert "confidence" in edge
    assert "origin" in edge
    assert "origin_run_id" in edge
    # src/dst instead of source/target per spec
    assert "src" in edge
    assert "dst" in edge
    assert "type" in edge  # spec uses "type" not "kind"


def test_edges_have_meta_with_evidence(tmp_path: Path) -> None:
    """Edges should have meta object with evidence info."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    edge = data["edges"][0]
    assert "meta" in edge
    meta = edge["meta"]
    assert "evidence_type" in meta
