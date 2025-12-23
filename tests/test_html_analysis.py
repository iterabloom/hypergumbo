"""Tests for HTML script tag detection."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


def test_detects_script_src_tag(tmp_path: Path) -> None:
    """Should detect external script references via <script src='...'> tags."""
    html_file = tmp_path / "index.html"
    html_file.write_text(
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '  <script src="app.js"></script>\n'
        '</head>\n'
        '<body></body>\n'
        '</html>\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should have a node for the HTML file
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1

    # Should have an edge from HTML to the script
    script_edges = [e for e in data["edges"] if e["type"] == "script_src"]
    assert len(script_edges) == 1
    assert "index.html" in script_edges[0]["src"]
    assert "app.js" in script_edges[0]["dst"]


def test_detects_multiple_script_tags(tmp_path: Path) -> None:
    """Should detect multiple script tags in one HTML file."""
    html_file = tmp_path / "page.html"
    html_file.write_text(
        '<html>\n'
        '<head>\n'
        '  <script src="vendor.js"></script>\n'
        '  <script src="app.js"></script>\n'
        '</head>\n'
        '<body>\n'
        '  <script src="analytics.js"></script>\n'
        '</body>\n'
        '</html>\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should have three script_src edges
    script_edges = [e for e in data["edges"] if e["type"] == "script_src"]
    assert len(script_edges) == 3


def test_ignores_inline_scripts_for_edges(tmp_path: Path) -> None:
    """Inline scripts without src should not create script_src edges."""
    html_file = tmp_path / "inline.html"
    html_file.write_text(
        '<html>\n'
        '<body>\n'
        '  <script>\n'
        '    console.log("inline");\n'
        '  </script>\n'
        '</body>\n'
        '</html>\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should still have the HTML file node
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1

    # But no script_src edges (inline scripts don't reference external files)
    script_edges = [e for e in data["edges"] if e["type"] == "script_src"]
    assert len(script_edges) == 0


def test_handles_both_quote_styles(tmp_path: Path) -> None:
    """Should handle both single and double quotes in src attributes."""
    html_file = tmp_path / "quotes.html"
    html_file.write_text(
        '<html>\n'
        '<head>\n'
        '  <script src="double.js"></script>\n'
        "  <script src='single.js'></script>\n"
        '</head>\n'
        '</html>\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    script_edges = [e for e in data["edges"] if e["type"] == "script_src"]
    assert len(script_edges) == 2

    srcs = {e["dst"] for e in script_edges}
    assert any("double.js" in s for s in srcs)
    assert any("single.js" in s for s in srcs)


def test_skips_unreadable_html_files(tmp_path: Path) -> None:
    """Should gracefully skip HTML files that cannot be read."""
    # Create a valid HTML file
    valid_file = tmp_path / "valid.html"
    valid_file.write_text('<html><script src="app.js"></script></html>')

    # Create a broken symlink to a non-existent HTML file
    broken_link = tmp_path / "broken.html"
    broken_link.symlink_to(tmp_path / "nonexistent.html")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should still process the valid file
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1
    assert "valid.html" in html_nodes[0]["path"]

    # Should have the edge from valid file
    script_edges = [e for e in data["edges"] if e["type"] == "script_src"]
    assert len(script_edges) == 1
