# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/clojure.py
"""A Babashka ``.bb`` script is analysed as the Clojure it is.

The Clojure analyzer read ``.clj`` / ``.cljs`` / ``.cljc`` only, and the
BitBake analyzer took every ``.bb``: metabase's ``bin/*.bb`` scripts had no
symbols at all. ``discovery.classify_dot_bb_file`` now gives a ``.bb`` file
to one of the two.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_lang_common.clojure import analyze_clojure, find_clojure_files


def test_a_babashka_script_is_a_clojure_file(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "mage.bb").write_text(
        ";; -*- mode: clojure -*-\n(ns mage)\n(defn run [] (helper))\n(defn helper [] 1)\n"
    )
    # Control: a BitBake recipe is not read, and .clj still is.
    (tmp_path / "foo_1.0.bb").write_text('SUMMARY = "foo"\ninherit autotools\n')
    (tmp_path / "core.clj").write_text("(ns core)\n(defn x [] 1)\n")
    (tmp_path / "deps.edn").write_text("{:deps {}}\n")  # data, still skipped
    found = {p.relative_to(tmp_path).as_posix() for p in find_clojure_files(tmp_path)}
    assert found == {"bin/mage.bb", "core.clj"}

    result = analyze_clojure(tmp_path)
    by_name = {s.name: s for s in result.symbols if s.path == "bin/mage.bb"}
    assert {"run", "helper"} <= set(by_name)
    assert make_file_id("clojure", "bin/mage.bb") in {s.id for s in result.symbols}
    assert any(
        e.src == by_name["run"].id and e.dst == by_name["helper"].id
        for e in result.edges if e.edge_type == "calls"
    )
    assert not any(s.path == "foo_1.0.bb" for s in result.symbols)


def test_a_babashka_only_tree_reaches_the_clojure_pass(tmp_path: Path) -> None:
    """Production path: the file-presence pre-filter counts clojure files
    with the analyzer's own finder (registered ``find_files``, INV-hokig, as
    the Wolfram ``.m`` precedent), so a tree whose only Clojure is ``.bb``
    scripts is not skipped as having no clojure files."""
    from hypergumbo_core.analyze.all_analyzers import run_all_analyzers
    from hypergumbo_core.profile import detect_profile

    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "athena.bb").write_text("#!/usr/bin/env bb\n(ns athena)\n(defn go [] 1)\n")
    # Control: the recipe stays BitBake's.
    (tmp_path / "foo_1.0.bb").write_text('SUMMARY = "foo"\n')
    profile = detect_profile(tmp_path, count_loc=True).to_dict()
    assert profile["languages"].get("clojure", {}).get("files") == 1, profile["languages"]

    _runs, symbols, _edges, _ucs, limits, _cap, _dep = run_all_analyzers(tmp_path, profile=profile)
    assert "clojure" not in {entry["pass"] for entry in limits.skipped_passes}
    by_lang = {(s.language, s.path) for s in symbols if s.kind != "file"}
    assert ("clojure", "bin/athena.bb") in by_lang
    assert ("bitbake", "foo_1.0.bb") in by_lang
