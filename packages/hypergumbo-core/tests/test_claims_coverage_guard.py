# SPDX-License-Identifier: AGPL-3.0-or-later
"""The per-PR self-claims coverage guard (INV-jojog).

THE TWO ASSERTIONS THAT MATTER are the end-to-end ones: a shipped-tree call
into a module the catalogue cannot classify makes the guard report
"not qualifiable" NAMING that module, and declaring the module turns it back.
That is the whole contract -- the four cron regressions it exists to catch were
each one unclassifiable name withholding all eighteen verdicts. The rest pins
the plumbing each regression's post-mortem would otherwise re-discover: the
file list is the packaging-declared roots and nothing else (``spec_validator``
is not a test), the ids the analyzer hands over are repo-relative, and the
catalogue is the cron's catalogue with the claims file's overlays on top.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.claims_coverage_guard import (
    GuardReport,
    analyzer_edges,
    claim_catalogs,
    coverage_of,
    run_guard,
    shipped_python_files,
)
from hypergumbo_core.verify_claims import BoundaryCoverage, shipped_artifact_roots

PROD = "packages/p/src/p/mod.py"


def _shipped_repo(tmp_path: Path, body: str) -> Path:
    """A src-layout package the way ``shipped_artifact_roots`` reads one, plus
    a test file OUTSIDE the artifact that calls something no catalogue knows."""
    pkg = tmp_path / "packages" / "p"
    (pkg / "src" / "p" / "__pycache__").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\n', encoding="utf-8",
    )
    (pkg / "src" / "p" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "src" / "p" / "mod.py").write_text(body, encoding="utf-8")
    (pkg / "src" / "p" / "spec_validator.py").write_text(
        "def validate():\n    return 1\n", encoding="utf-8",
    )
    (pkg / "src" / "p" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00")
    (pkg / "tests").mkdir()
    (pkg / "tests" / "test_mod.py").write_text(
        "import leakymod\nleakymod.go()\n", encoding="utf-8",
    )
    return tmp_path


def _claims(tmp_path: Path, overlay_modules: list[str] = ()) -> Path:
    """A claims file naming one overlay, which declares ``overlay_modules``."""
    overlay = tmp_path / "overlay.yaml"
    entries = "".join(
        f'  - module: {m}\n    completeness: complete\n'
        f'    retrieved: "2026-10-17"\n    notes: test\n'
        for m in overlay_modules
    )
    overlay.write_text(
        "language: python\nstatus: overlay\n"
        + (f"module_completeness:\n{entries}" if entries else ""),
        encoding="utf-8",
    )
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "analysis_scope: shipped_artifact\nextra_catalogs:\n  io_primitives:\n"
        "    - overlay.yaml\nclaims: []\n",
        encoding="utf-8",
    )
    return claims


def _call(src_name: str, dst: str) -> dict:
    return {
        "src": f"python:{PROD}:1-3:{src_name}:function",
        "dst": dst,
        "type": "calls",
        "meta": {"call_construct": "method", "evidence_type": "ast_call"},
    }


class TestTheFileList:
    def test_shipped_roots_only_and_spec_validator_is_not_a_test(
        self, tmp_path: Path,
    ) -> None:
        root = _shipped_repo(tmp_path, "x = 1\n")
        files = shipped_python_files(root, shipped_artifact_roots(root))
        rel = {str(f.relative_to(root)) for f in files}
        assert rel == {
            "packages/p/src/p/__init__.py",
            PROD,
            "packages/p/src/p/spec_validator.py",
        }

    def test_the_live_tree_keeps_spec_validator(self) -> None:
        """The trap the first draft fell into: ``is_test_file`` calls this
        shipped module a test, its callers then read as calls into an untraced
        third-party module, and the guard fails on a tree the cron passes."""
        root = Path(__file__).resolve().parents[3]
        files = shipped_python_files(root, shipped_artifact_roots(root))
        rel = {str(f.relative_to(root)) for f in files}
        assert "packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py" in rel
        assert "packages/hypergumbo-core/src/hypergumbo_core/claims_coverage_guard.py" in rel
        assert not any("/tests/" in r for r in rel)


class TestAnalyzerEdges:
    def test_ids_are_repo_relative_and_name_the_class_qualified_slot(
        self, tmp_path: Path,
    ) -> None:
        """``datetime.date.today()`` must arrive as the cron sees it: module
        slot ``datetime.date``, not ``datetime`` -- INV-fugus and INV-jojog
        were both calls through exactly that slot."""
        root = _shipped_repo(
            tmp_path, "import datetime\n\ndef f():\n    return datetime.date.today()\n",
        )
        edges = analyzer_edges(root, shipped_python_files(root, shipped_artifact_roots(root)))
        srcs = {e["src"] for e in edges}
        assert any(s.startswith(f"python:{PROD}:") for s in srcs), sorted(srcs)[:5]
        assert not any(str(root) in e["src"] or str(root) in e["dst"] for e in edges)
        # The terminal slot is the analyzer's business (``external_symbol`` on
        # the self-tree, ``unresolved`` in a bare fixture); the gate adjudicates
        # both. What this pins is the MODULE SLOT and the name.
        assert any(
            e["dst"].startswith("python:datetime.date:0-0:today:") for e in edges
        ), sorted({e["dst"] for e in edges})

    def test_only_the_given_files_are_analysed(self, tmp_path: Path) -> None:
        root = _shipped_repo(tmp_path, "x = 1\n")
        edges = analyzer_edges(root, shipped_python_files(root, shipped_artifact_roots(root)))
        assert not any("tests/test_mod.py" in e["src"] for e in edges)
        assert not any("leakymod" in e["dst"] for e in edges)


class TestTheDecisionIsTheCronsOwn:
    def test_an_unclassifiable_module_withholds_and_is_named(self, tmp_path: Path) -> None:
        catalogs = claim_catalogs(_claims(tmp_path))
        coverage = coverage_of(
            [_call("f", "python:fakemod.Thing:0-0:run:external_symbol")], catalogs,
        )
        assert not coverage.complete
        assert not coverage.qualifying_only
        assert "fakemod.Thing" in coverage.reason

    def test_a_declared_module_is_an_examined_negative(self, tmp_path: Path) -> None:
        catalogs = claim_catalogs(_claims(tmp_path, ["fakemod.Thing"]))
        coverage = coverage_of(
            [_call("f", "python:fakemod.Thing:0-0:run:external_symbol")], catalogs,
        )
        assert coverage.complete

    def test_the_claims_overlay_sits_on_the_shipped_catalogue(self, tmp_path: Path) -> None:
        """Both layers at once: the shipped stdlib grant (``json``) and the
        claims file's own overlay grant (``fakemod.Thing``)."""
        catalog = claim_catalogs(_claims(tmp_path, ["fakemod.Thing"]))["python"]
        assert catalog.module_io_is_enumerated("json")
        assert catalog.module_io_is_enumerated("fakemod.Thing")
        assert not catalog.module_io_is_enumerated("fakemod.Other")


class TestEndToEnd:
    def test_a_clean_shipped_tree_is_qualifiable(self, tmp_path: Path) -> None:
        root = _shipped_repo(
            tmp_path,
            "import json\nimport datetime\n\n"
            "def f():\n    return json.dumps(datetime.date.today().isoformat())\n",
        )
        report = run_guard(root, _claims(tmp_path))
        assert report.qualifiable, report.coverage.reason
        assert report.files == 3
        assert report.edges > 0
        assert report.seconds >= 0

    def test_one_unclassifiable_call_withholds_and_names_the_module(
        self, tmp_path: Path,
    ) -> None:
        """INV-jojog's statement, run: a shipped-code call into a module no
        catalogue classifies must fail BEFORE it reaches dev."""
        root = _shipped_repo(
            tmp_path, "import fakemod\n\ndef f():\n    return fakemod.Thing().run()\n",
        )
        report = run_guard(root, _claims(tmp_path))
        assert not report.qualifiable
        assert "fakemod" in report.coverage.reason

    def test_declaring_the_module_turns_the_same_tree_green(self, tmp_path: Path) -> None:
        root = _shipped_repo(
            tmp_path, "import fakemod\n\ndef f():\n    return fakemod.Thing().run()\n",
        )
        report = run_guard(root, _claims(tmp_path, ["fakemod", "fakemod.Thing"]))
        assert report.qualifiable, report.coverage.reason


class TestTheReport:
    def test_qualifiable_is_complete_or_qualified(self) -> None:
        def _report(**kw: object) -> GuardReport:
            return GuardReport(files=1, edges=1, seconds=0.0,
                               coverage=BoundaryCoverage(**kw))  # type: ignore[arg-type]

        assert _report(complete=True).qualifiable
        assert _report(complete=False, qualifying_only=True).qualifiable
        assert not _report(complete=False, qualifying_only=False, reason="x").qualifiable
