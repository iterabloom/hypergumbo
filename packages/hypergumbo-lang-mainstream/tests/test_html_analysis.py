# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for HTML script tag detection."""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have a node for the HTML file
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1

    # Should have an edge from HTML to the script
    script_edges = [e for e in data["edges"] if e["type"] == "references" and (e.get("meta") or {}).get("ref_construct") == "script_src"]
    assert len(script_edges) == 1
    assert "index.html" in script_edges[0]["src"]
    assert "app.js" in script_edges[0]["dst"]
    # INV-vavat / ADR-0023: <script src> is a `references` relationship with
    # the ref_construct in meta — NOT the old endpoint-shape edge_type "script_src".
    assert script_edges[0]["type"] == "references"
    assert script_edges[0]["meta"]["ref_construct"] == "script_src"


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have three script_src edges
    script_edges = [e for e in data["edges"] if e["type"] == "references" and (e.get("meta") or {}).get("ref_construct") == "script_src"]
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still have the HTML file node
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1

    # But no script_src edges (inline scripts don't reference external files)
    script_edges = [e for e in data["edges"] if e["type"] == "references" and (e.get("meta") or {}).get("ref_construct") == "script_src"]
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    script_edges = [e for e in data["edges"] if e["type"] == "references" and (e.get("meta") or {}).get("ref_construct") == "script_src"]
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still process the valid file
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1
    assert "valid.html" in html_nodes[0]["path"]

    # Should have the edge from valid file
    script_edges = [e for e in data["edges"] if e["type"] == "references" and (e.get("meta") or {}).get("ref_construct") == "script_src"]
    assert len(script_edges) == 1


# ─── INV-tajap PR 2: html_entry detection ─────────────────────────────────
#
# Pre-fix: HTML files were parsed and got file-kind Symbols, but no concept
# rode on the file Symbol — so entrypoint detection ignored every index.html
# in the repo. SPA roots (the page that bootstraps the JS bundle) looked like
# inert content. This sub-fix stamps a ``html_entry`` concept on the file
# Symbol when the filename is index.html (the convention-based SPA root),
# and entrypoints.py turns that into a HTML_ENTRY entrypoint.


def test_inv_tajap_index_html_emits_html_entry_concept(tmp_path: Path) -> None:
    """The file Symbol for index.html carries an html_entry concept."""
    from hypergumbo_lang_mainstream.html import analyze_html

    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><script src='main.js'></script></body></html>\n"
    )
    result = analyze_html(tmp_path)

    file_syms = [s for s in result.symbols if s.kind == "file"]
    assert file_syms, "HTML analyzer must emit a file Symbol for index.html"
    file_sym = file_syms[0]
    concepts = (file_sym.meta or {}).get("concepts", [])
    assert any(c.get("concept") == "html_entry" for c in concepts), (
        f"expected html_entry concept on index.html file Symbol; "
        f"got concepts={concepts!r}"
    )


def test_inv_tajap_non_index_html_does_not_emit_html_entry(tmp_path: Path) -> None:
    """A regular ``page.html`` is NOT marked as html_entry — only the SPA root."""
    from hypergumbo_lang_mainstream.html import analyze_html

    (tmp_path / "page.html").write_text("<html><body>Hi</body></html>\n")
    result = analyze_html(tmp_path)

    file_syms = [s for s in result.symbols if s.kind == "file"]
    assert file_syms
    concepts = (file_syms[0].meta or {}).get("concepts", [])
    assert not any(c.get("concept") == "html_entry" for c in concepts), (
        f"non-index HTML must not carry html_entry; got concepts={concepts!r}"
    )


def test_inv_tajap_index_html_case_insensitive(tmp_path: Path) -> None:
    """``INDEX.HTML`` / ``Index.html`` still trigger html_entry."""
    from hypergumbo_lang_mainstream.html import analyze_html

    (tmp_path / "Index.html").write_text("<html></html>\n")
    result = analyze_html(tmp_path)

    file_syms = [s for s in result.symbols if s.kind == "file"]
    assert file_syms
    concepts = (file_syms[0].meta or {}).get("concepts", [])
    assert any(c.get("concept") == "html_entry" for c in concepts)


def test_inv_tajap_index_html_in_subdirectory_still_emits_concept(
    tmp_path: Path,
) -> None:
    """SPA roots in subdirectories (e.g. ``packages/frontend/index.html``)
    also count — the convention is the filename, not the root location."""
    from hypergumbo_lang_mainstream.html import analyze_html

    sub = tmp_path / "packages" / "frontend"
    sub.mkdir(parents=True)
    (sub / "index.html").write_text("<html></html>\n")
    result = analyze_html(tmp_path)

    file_syms = [s for s in result.symbols if s.kind == "file"]
    assert file_syms, "expected the subdirectory index.html to be analyzed"
    concepts = (file_syms[0].meta or {}).get("concepts", [])
    assert any(c.get("concept") == "html_entry" for c in concepts)
