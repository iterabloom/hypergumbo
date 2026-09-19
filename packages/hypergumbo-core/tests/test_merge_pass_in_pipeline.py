# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §3 / WI-kokiz: the merge pass fires inside ``run_behavior_map``.

LIVE.md rule 7: an instrument with no proven wire-up is the same organic
criterion in a costume. The unit tests prove the pass; this proves the
PIPELINE calls it where the ADR says — after relativization, before the
first Phase-C consumer — by registering a second Python producer beside the
real one for the duration of one run. The fake ``pyscip`` emits, for a
three-symbol file, two records the real analyzer also emits (``f`` and
``Alias``, as token spans — the latter with a CONTESTED kind, ``type_alias``
against the incumbent's ``variable``) and one it never does (the module
namespace), then the artifact is read back: ``f`` and ``Alias`` are ONE node
each carrying both producers, ``Alias`` keeps the incumbent's kind, the
namespace survives as ``pyscip``-only, and a ``producer-merge`` run is
recorded. The real Python
analyzer's own anchor (declared for exactly this partner shape, WI-nanom)
is what makes the pairing possible; without it the pass would REFUSE the
undeclared incumbent by name, which the last test pins.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    UndeclaredProducerError,
    as_emitted,
    ensure_discovered,
    register_analyzer,
)
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import PASS_VERSION, AnalysisRun, Span, Symbol
from hypergumbo_core.schema import SCHEMA_VERSION

SOURCE = "def f():\n    return 1\n\n\nAlias = int\n"


def _pyscip(repo_root: Path) -> AnalysisResult:
    run = AnalysisRun.create(pass_id="pyscip", version=PASS_VERSION)  # nosec B106
    symbols = [
        Symbol(
            id="python:a.py:1-1:f:function", name="f", kind="function", language="python",
            path="a.py", span=Span(1, 1, 4, 5), origin="pyscip", origin_run_id=run.execution_id,
            stable_id="sha256:pyscip-f", meta={"scip_symbol": "scip-python pypi a 0.1 a/f()."},
        ),
        Symbol(
            id="python:a.py:5-5:Alias:type_alias", name="Alias", kind="type_alias",
            language="python", path="a.py", span=Span(5, 5, 0, 5), origin="pyscip",
            origin_run_id=run.execution_id, stable_id="sha256:pyscip-alias",
            meta={"scip_symbol": "scip-python pypi a 0.1 a/Alias#"},
        ),
        # The module namespace: a record the incumbent never emits (file
        # anchors are synthesised later, as kind ``file`` named ``a.py``).
        Symbol(
            id="python:a.py:1-5:a:namespace", name="a", kind="namespace",
            language="python", path="a.py", span=Span(1, 5, 0, 0), origin="pyscip",
            origin_run_id=run.execution_id, stable_id="sha256:pyscip-a",
            meta={"scip_symbol": "scip-python pypi a 0.1 a/"},
        ),
    ]
    return AnalysisResult(symbols=symbols, edges=[], run=run)


@pytest.fixture
def second_python_producer():
    ensure_discovered()
    assert "pyscip" not in _registry_mod._ANALYZER_REGISTRY
    register_analyzer(
        "pyscip", priority=45, languages=["python"], backend="scip",
        merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN),
    )(_pyscip)
    yield
    _registry_mod._ANALYZER_REGISTRY.pop("pyscip", None)


def _run(tmp_path: Path) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(SOURCE)
    out = tmp_path / "results.json"
    run_behavior_map(
        repo_root=repo, out_path=out, budgets="none",
        include_sketch_precomputed=False, enable_handler_slices=False,
    )
    return json.loads(out.read_text())


def test_the_pipeline_folds_the_shared_declaration(tmp_path: Path, second_python_producer) -> None:
    data = _run(tmp_path)
    f_nodes = [n for n in data["nodes"] if n["name"] == "f" and n["path"] == "a.py"]
    assert len(f_nodes) == 1, [(n["origin"], n["id"]) for n in f_nodes]
    [f] = f_nodes
    assert f["origin"] == ["python", "pyscip"]
    assert f["kind"] == "function"
    assert f["span"]["start_line"] == 1 and f["span"]["end_line"] == 2  # the item, not the token
    assert (f.get("meta") or {}).get("scip_symbol") == "scip-python pypi a 0.1 a/f()."
    alias = [n for n in data["nodes"] if n["name"] == "Alias"]
    assert len(alias) == 1 and alias[0]["origin"] == ["python", "pyscip"]
    assert alias[0]["kind"] == "variable"  # incumbent-first on the contested kind
    namespace = [n for n in data["nodes"] if n["name"] == "a" and n["kind"] == "namespace"]
    assert len(namespace) == 1 and namespace[0]["origin"] == ["pyscip"]
    merge_runs = [r for r in data["analysis_runs"] if r["pass"] == "producer-merge"]
    assert len(merge_runs) == 1
    assert f["origin_run_id"] == merge_runs[0]["execution_id"]
    # WI-binis: the provenance slot is in the artifact, on merged nodes only.
    assert f["attribution"]["kind"] == ["python", "pyscip"]
    assert "kind" not in f.get("alternatives", {})
    assert alias[0]["attribution"]["kind"] == ["python"]
    assert alias[0]["alternatives"]["kind"] == [{"value": "type_alias", "origin": ["pyscip"]}]
    assert "attribution" not in namespace[0] and "alternatives" not in namespace[0]
    assert all("attribution" not in n for n in data["nodes"] if n["origin"] == ["python"])
    assert data["schema_version"] == SCHEMA_VERSION


def test_without_a_second_producer_no_merge_run_is_recorded(tmp_path: Path) -> None:
    data = _run(tmp_path)
    assert [r for r in data["analysis_runs"] if r["pass"] == "producer-merge"] == []
    assert [n["origin"] for n in data["nodes"] if n["name"] == "f"] == [["python"]]


def test_an_undeclared_incumbent_makes_the_pipeline_refuse_by_name(tmp_path: Path, second_python_producer) -> None:
    """The refusal is the pipeline's, not a fallthrough: strip the incumbent's
    declaration and the run stops naming it."""
    from hypergumbo_core.discovery import get_file_index

    python = _registry_mod._ANALYZER_REGISTRY["python"]
    saved = python.merge
    python.merge = None
    try:
        with pytest.raises(UndeclaredProducerError, match="'python'"):
            _run(tmp_path)
    finally:
        python.merge = saved
    # The refusal must not leave this run's file index behind as process
    # state: the next run_all_analyzers in the process would read THIS
    # repository's files against ITS root (CI on #1078 failed exactly so).
    assert get_file_index() is None
