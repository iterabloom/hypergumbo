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


# ---------------------------------------------------------------------------
# WI-majov: a ``<script src>`` naming an in-repo JS/TS file must land on that
# file's real node, not on a phantom ``external_symbol`` placeholder. Relative
# and root-absolute srcs both resolve against the HTML file's directory (a
# leading "/" takes that directory as the web root, the Vite convention); the
# language comes from the resolved file, the way the JS/TS analyzer tags it.


def _script_edges(data: dict) -> list[dict]:
    return [
        e for e in data["edges"]
        if e["type"] == "references"
        and (e.get("meta") or {}).get("ref_construct") == "script_src"
    ]


def _run(tmp_path: Path) -> dict:
    out_path = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False
    )
    return json.loads(out_path.read_text())


def test_wi_majov_script_src_resolves_onto_first_party_file_nodes(
    tmp_path: Path,
) -> None:
    """Production path: root-absolute ``/src/main.ts`` and relative
    ``./src/util.js`` both land on the existing file nodes; a CDN URL keeps
    its external placeholder."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        'import { u } from "./util.js";\nconsole.log(u());\n'
    )
    (tmp_path / "src" / "util.js").write_text(
        "export function u() { return 1; }\n"
    )
    (tmp_path / "index.html").write_text(
        "<!doctype html>\n<html><body>\n"
        '<script type="module" src="/src/main.ts"></script>\n'
        '<script src="./src/util.js"></script>\n'
        '<script src="https://cdn.example.com/lib.js"></script>\n'
        "</body></html>\n"
    )

    data = _run(tmp_path)
    nodes = {n["id"]: n for n in data["nodes"]}
    edges = _script_edges(data)
    assert len(edges) == 3, edges  # reach: all three tags were seen
    by_dst = {e["dst"]: e for e in edges}

    for dst, lang, path in (
        ("typescript:src/main.ts:1-1:file:file", "typescript", "src/main.ts"),
        ("javascript:src/util.js:1-1:file:file", "javascript", "src/util.js"),
    ):
        assert dst in by_dst, f"{dst} not an edge dst; got {sorted(by_dst)}"
        assert by_dst[dst]["is_resolved"] is True
        node = nodes[dst]
        assert node["kind"] == "file"
        assert node["language"] == lang
        assert node["path"] == path

    cdn = [d for d in by_dst if "cdn.example.com" in d]
    assert len(cdn) == 1
    assert by_dst[cdn[0]]["is_resolved"] is False
    assert nodes[cdn[0]]["kind"] == "external_symbol"

    # No html script placeholder (``...:0-0:ref:...``) survives for either
    # in-repo script. (The JS/TS analyzer's own ``import "./util.js"``
    # placeholder is a different producer and out of scope here.)
    phantoms = [
        i for i, n in nodes.items()
        if n["kind"] == "external_symbol" and ":0-0:ref:" in i
        and ("main.ts" in i or "util.js" in i)
    ]
    assert phantoms == []


def test_wi_majov_relative_src_from_subdirectory_with_query(
    tmp_path: Path,
) -> None:
    """A src is relative to the HTML file's own directory (``..`` allowed
    while it stays inside the repo); a cache-busting query is not part of
    the path."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.js").write_text("function a() {}\n")
    # Not ``site/``: discovery excludes it (mkdocs build output).
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "page.html").write_text(
        '<html><script src="../lib/a.js?v=3#x"></script></html>\n'
    )

    data = _run(tmp_path)
    edges = _script_edges(data)
    assert [e["dst"] for e in edges] == ["javascript:lib/a.js:1-1:file:file"]
    assert edges[0]["is_resolved"] is True
