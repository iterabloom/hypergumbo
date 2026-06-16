# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bokab (v7): rust.py file-anchoring — cross-file uniqueness + location independence.

The typed-tier template for the file-identity fold. Before v7, two ``.rs`` files each
declaring ``pub fn foo`` produced the same stable_id (containing_stable_id empty, scope
carried only in qualified_name). After folding ``make_file_stable_id("rust",
normalize_path(rel_path))`` into the typed-tier containing slot, the two foos hash
distinctly. The anchor uses the REPO-RELATIVE path, so the id is location-independent
(analyzing the same file under different absolute roots yields the same stable_id) — the
property that guards against the absolute-path-fold regression.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.rust import analyze_rust


def _fn_ids_by_qualified_name(result) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in result.symbols:
        if s.stable_id and s.kind in {"function", "method"}:
            out.setdefault(s.qualified_name or s.name, []).append(s.stable_id)
    return out


def test_same_name_top_level_fn_distinct_across_files(tmp_path: Path) -> None:
    (tmp_path / "a.rs").write_text("pub fn foo(x: i32) -> i32 { x }\n")
    (tmp_path / "b.rs").write_text("pub fn foo(x: i32) -> i32 { x }\n")
    foos = [s for s in analyze_rust(tmp_path).symbols if s.name == "foo" and s.stable_id]
    assert len(foos) == 2, f"expected one foo per file, got {len(foos)}"
    assert foos[0].stable_id != foos[1].stable_id, (
        "cross-file collision: same-name top-level fns in different files share a stable_id"
    )


def test_rust_stable_id_is_location_independent(tmp_path: Path) -> None:
    """Same repo-relative path under different absolute roots → identical stable_id
    (the fold must NOT bake an absolute path)."""
    root1 = tmp_path / "loc1" / "proj"
    root2 = tmp_path / "loc2" / "proj"
    for root in (root1, root2):
        root.mkdir(parents=True)
        (root / "m.rs").write_text("pub fn bar(n: u8) -> u8 { n }\n")
    ids1 = _fn_ids_by_qualified_name(analyze_rust(root1))
    ids2 = _fn_ids_by_qualified_name(analyze_rust(root2))
    assert ids1.get("bar") and ids1["bar"] == ids2.get("bar"), (
        f"location-dependent stable_id: {ids1.get('bar')} != {ids2.get('bar')}"
    )
