# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/clojure.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py
"""A Babashka script's guarded top-level code runs, and so does what it calls.

Two gaps left every function of metabase's ``bin/*.bb`` scripts reported
dead. The Clojure analyzer emitted a call only from inside a ``defn``, so the
call a script's top-level form makes (``(-main)``, ``(deploy version)``) had
no edge at all, although INV-foluz anchors a call outside every function on
the file node. And nothing marked the script as run: Babashka's
``(when (= *file* (System/getProperty "babashka.file")) ...)`` is the
counterpart of Python's ``if __name__ == "__main__":``, which the existing
``main_guard`` entry kind (a program start, ADR-0063) names. The file node now
carries the ``main_guard`` concept (framework ``babashka``).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_lang_common.clojure import analyze_clojure

_SCRIPT = """\
#!/usr/bin/env bb
(ns athena)
(defn helper [] 1)
(defn deploy [v] (helper))
(defn unused [] 2)
(when (= *file* (System/getProperty "babashka.file"))
  (deploy "1.0"))
"""
_LIBRARY = """\
(ns lib)
(defn build [] 1)
(defn scratch [] 1)
(defn checked [] 1)
(def registry (build))
(comment (scratch))
(deftest t (checked))
"""


def _run(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "athena.bb").write_text(_SCRIPT)
    (tmp_path / "lib.clj").write_text(_LIBRARY)
    return analyze_clojure(tmp_path)


def _by_name(result, path: str) -> dict:
    return {s.name: s for s in result.symbols if s.path == path}


def test_a_top_level_call_is_anchored_on_the_file(tmp_path: Path) -> None:
    result = _run(tmp_path)
    script = _by_name(result, "bin/athena.bb")
    calls = {(e.src, e.dst) for e in result.edges if e.edge_type == "calls"}
    file_id = make_file_id("clojure", "bin/athena.bb")
    assert (script["deploy"].id, script["helper"].id) in calls  # control: in a defn
    assert (file_id, script["deploy"].id) in calls
    # A def's value form runs at load too (a library, no guard).
    lib = _by_name(result, "lib.clj")
    assert (make_file_id("clojure", "lib.clj"), lib["build"].id) in calls
    # Control: a rich comment never runs, a test body runs when the runner
    # calls it; neither is load-time code.
    assert not {dst for src, dst in calls if dst in (lib["scratch"].id, lib["checked"].id)}


def test_the_babashka_guard_makes_the_script_a_main_guard_entry(tmp_path: Path) -> None:
    result = _run(tmp_path)
    files = {s.path: s for s in result.symbols if s.kind == "file"}
    concepts = (files["bin/athena.bb"].meta or {}).get("concepts")
    assert concepts == [{"concept": "main_guard", "framework": "babashka"}]
    assert not (files["lib.clj"].meta or {}).get("concepts")  # control: no guard

    entries = detect_entrypoints(result.symbols, result.edges)
    guards = [ep for ep in entries if ep.kind == EntrypointKind.MAIN_GUARD]
    assert [ep.symbol_id for ep in guards] == [files["bin/athena.bb"].id]
    assert "Babashka" in guards[0].label


def test_an_unqualified_call_reaches_its_own_namespaces_var_first(tmp_path: Path) -> None:
    """Two scripts each define ``-main`` and call it: each call ends on its
    own file's ``-main`` (the run-wide resolver alone picked one of them)."""
    for name in ("one", "two"):
        (tmp_path / f"{name}.bb").write_text(
            f"(ns {name})\n(defn -main [] 1)\n"
            '(when (= *file* (System/getProperty "babashka.file"))\n  (-main))\n'
        )
    result = analyze_clojure(tmp_path)
    mains = {s.path: s.id for s in result.symbols if s.name == "-main"}
    assert len(mains) == 2  # reach
    calls = {(e.src, e.dst) for e in result.edges if e.edge_type == "calls"}
    for path, main_id in mains.items():
        assert (make_file_id("clojure", path), main_id) in calls


def test_a_threading_step_is_a_call(tmp_path: Path) -> None:
    """``(-> z f (g 1))``: ``f`` is called though it has no parentheses;
    ``cond->`` steps are every second form; a test is not a step."""
    (tmp_path / "t.clj").write_text(
        "(ns t)\n(defn f [x] x)\n(defn g [x y] x)\n(defn pred [x] true)\n(defn h [x] x)\n"
        "(defn run [z]\n  (-> z f (g 1))\n  (cond-> z pred h))\n"
    )
    result = analyze_clojure(tmp_path)
    by = {s.name: s.id for s in result.symbols}
    calls = {e.dst for e in result.edges if e.edge_type == "calls" and e.src == by["run"]}
    assert {by["f"], by["g"], by["h"]} <= calls
    assert by["pred"] not in calls  # control: a cond-> test is a value here
