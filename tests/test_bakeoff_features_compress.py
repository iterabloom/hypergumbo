"""Tests for bakeoff-features compress subcommand and hg.json.gz transparent reading."""

import argparse
import gzip
import json
import os
import time
from pathlib import Path

# Import the bakeoff-features script as a module
import importlib
import importlib.machinery
import importlib.util


def _load_bakeoff_features():
    """Import scripts/bakeoff-features as a module despite no .py extension."""
    script_path = str(
        Path(__file__).resolve().parent.parent / "scripts" / "bakeoff-features"
    )
    loader = importlib.machinery.SourceFileLoader("bakeoff_features", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_features", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bf = _load_bakeoff_features()


# ---------------------------------------------------------------------------
# Helper: create a minimal bakeoff repo output directory
# ---------------------------------------------------------------------------

def _make_repo_output(out_dir: Path, repo_name: str, *, hg_data: dict | None = None,
                      derived_age_hours: float = 0.0, include_derived: bool = True,
                      already_compressed: bool = False) -> Path:
    """Create a fake repo output directory with hg.json and optional derived artifacts."""
    repo_out = out_dir / repo_name
    repo_out.mkdir(parents=True, exist_ok=True)

    hg_data = hg_data or {"nodes": [], "edges": []}

    if already_compressed:
        gz_path = repo_out / "hg.json.gz"
        with gzip.open(gz_path, "wt") as f:
            json.dump(hg_data, f)
    else:
        hg_path = repo_out / "hg.json"
        with open(hg_path, "w") as f:
            json.dump(hg_data, f)

    if include_derived:
        for artifact in ("entries.txt", "symbols.txt", "routes.txt"):
            art_path = repo_out / artifact
            art_path.write_text("some data\n")
            if derived_age_hours > 0:
                # Set mtime to N hours ago
                old_time = time.time() - (derived_age_hours * 3600)
                os.utime(art_path, (old_time, old_time))

    return repo_out


# ---------------------------------------------------------------------------
# Tests: _find_hg_json / _open_hg_json
# ---------------------------------------------------------------------------

class TestFindHgJson:
    """Test transparent hg.json / hg.json.gz discovery."""

    def test_finds_plain_hg_json(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a")
        result = bf._find_hg_json(str(repo_out))
        assert result is not None
        assert result.endswith("hg.json")
        assert not result.endswith(".gz")

    def test_finds_compressed_hg_json(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-b", already_compressed=True)
        result = bf._find_hg_json(str(repo_out))
        assert result is not None
        assert result.endswith("hg.json.gz")

    def test_prefers_plain_over_compressed(self, tmp_path: Path) -> None:
        """If both hg.json and hg.json.gz exist, prefer plain."""
        repo_out = tmp_path / "repo-c"
        repo_out.mkdir()
        (repo_out / "hg.json").write_text('{"nodes":[],"edges":[]}')
        with gzip.open(repo_out / "hg.json.gz", "wt") as f:
            json.dump({"nodes": [], "edges": []}, f)
        result = bf._find_hg_json(str(repo_out))
        assert result.endswith("hg.json")
        assert not result.endswith(".gz")

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        repo_out = tmp_path / "repo-d"
        repo_out.mkdir()
        result = bf._find_hg_json(str(repo_out))
        assert result is None


class TestOpenHgJson:
    """Test transparent reading of hg.json or hg.json.gz."""

    def test_reads_plain_json(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "a"}], "edges": []}
        repo_out = _make_repo_output(tmp_path, "repo-a", hg_data=data)
        result = bf._open_hg_json(str(repo_out / "hg.json"))
        assert result == data

    def test_reads_compressed_json(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "b"}], "edges": []}
        repo_out = _make_repo_output(tmp_path, "repo-b", hg_data=data,
                                     already_compressed=True)
        result = bf._open_hg_json(str(repo_out / "hg.json.gz"))
        assert result == data


# ---------------------------------------------------------------------------
# Tests: _should_compress_repo
# ---------------------------------------------------------------------------

class TestShouldCompressRepo:
    """Test the eligibility logic for compressing a repo's hg.json."""

    def test_eligible_when_derived_old_enough(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a", derived_age_hours=9.0)
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is True

    def test_ineligible_when_derived_too_recent(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a", derived_age_hours=2.0)
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is False

    def test_ineligible_when_no_derived_artifacts(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a", include_derived=False)
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is False

    def test_ineligible_when_already_compressed(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a", already_compressed=True,
                                     derived_age_hours=24.0)
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is False

    def test_ineligible_when_no_hg_json(self, tmp_path: Path) -> None:
        repo_out = tmp_path / "repo-x"
        repo_out.mkdir()
        (repo_out / "entries.txt").write_text("data")
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is False

    def test_uses_oldest_derived_artifact(self, tmp_path: Path) -> None:
        """Should use the newest derived artifact's age (all must be old enough)."""
        repo_out = _make_repo_output(tmp_path, "repo-a", derived_age_hours=24.0)
        # Make one artifact very recent
        recent = repo_out / "routes.txt"
        recent.write_text("new data")
        # routes.txt now has current mtime → too recent
        assert bf._should_compress_repo(str(repo_out), min_age_hours=8.0) is False


# ---------------------------------------------------------------------------
# Tests: _compress_hg_json
# ---------------------------------------------------------------------------

class TestCompressHgJson:
    """Test the actual compression of hg.json → hg.json.gz."""

    def test_compresses_and_removes_original(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "n1"}], "edges": [{"src": "n1", "dst": "n2"}]}
        repo_out = _make_repo_output(tmp_path, "repo-a", hg_data=data,
                                     derived_age_hours=24.0)
        hg_path = repo_out / "hg.json"
        result = bf._compress_hg_json(str(hg_path))
        assert result is True
        assert not hg_path.exists(), "Original hg.json should be removed"
        gz_path = repo_out / "hg.json.gz"
        assert gz_path.exists(), "hg.json.gz should exist"

        # Verify contents are readable and correct
        with gzip.open(gz_path, "rt") as f:
            loaded = json.load(f)
        assert loaded == data

    def test_returns_false_if_already_compressed(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-a", already_compressed=True)
        hg_path = repo_out / "hg.json"
        assert not hg_path.exists()
        result = bf._compress_hg_json(str(hg_path))
        assert result is False


# ---------------------------------------------------------------------------
# Tests: cmd_compress integration
# ---------------------------------------------------------------------------

class TestCmdCompress:
    """Integration tests for the compress subcommand."""

    def _make_session(self, tmp_path: Path, repos: dict) -> Path:
        """Create a minimal bakeoff session structure.

        repos: {repo_name: {"derived_age_hours": float, ...}}
        """
        workdir = tmp_path / "session"
        workdir.mkdir()
        state = {
            "pool_path": str(tmp_path / "pool"),
            "cohorts": [
                {"id": "cohort-001", "repos": list(repos.keys()), "iteration": 1}
            ],
            "reflect_statuses": {},
        }
        (workdir / "state.json").write_text(json.dumps(state))

        # Create output structure: out/cohort-001/iter-1/<repo>/
        out_base = workdir / "out"
        cohort_out = out_base / "cohort-001" / "iter-1"
        cohort_out.mkdir(parents=True)

        for repo_name, opts in repos.items():
            _make_repo_output(
                cohort_out, repo_name,
                derived_age_hours=opts.get("derived_age_hours", 0),
                include_derived=opts.get("include_derived", True),
                already_compressed=opts.get("already_compressed", False),
            )

        return workdir

    def test_compresses_eligible_repos(self, tmp_path: Path) -> None:
        workdir = self._make_session(tmp_path, {
            "old-repo": {"derived_age_hours": 24.0},
            "new-repo": {"derived_age_hours": 1.0},
        })

        args = argparse.Namespace(workdir=str(workdir), dry_run=False)
        rc = bf.cmd_compress(args)
        assert rc == 0

        old_repo_out = workdir / "out" / "cohort-001" / "iter-1" / "old-repo"
        new_repo_out = workdir / "out" / "cohort-001" / "iter-1" / "new-repo"

        assert (old_repo_out / "hg.json.gz").exists()
        assert not (old_repo_out / "hg.json").exists()
        assert (new_repo_out / "hg.json").exists()
        assert not (new_repo_out / "hg.json.gz").exists()

    def test_dry_run_does_not_compress(self, tmp_path: Path) -> None:
        workdir = self._make_session(tmp_path, {
            "old-repo": {"derived_age_hours": 24.0},
        })

        args = argparse.Namespace(workdir=str(workdir), dry_run=True)
        rc = bf.cmd_compress(args)
        assert rc == 0

        old_repo_out = workdir / "out" / "cohort-001" / "iter-1" / "old-repo"
        assert (old_repo_out / "hg.json").exists(), "Dry run should not compress"
        assert not (old_repo_out / "hg.json.gz").exists()

    def test_no_eligible_repos(self, tmp_path: Path) -> None:
        workdir = self._make_session(tmp_path, {
            "fresh-repo": {"derived_age_hours": 0.5},
        })

        args = argparse.Namespace(workdir=str(workdir), dry_run=False)
        rc = bf.cmd_compress(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# Tests: hg.json.gz reading in diagnose / pick_reverse_slice_seeds
# ---------------------------------------------------------------------------

class TestCompressedReadIntegration:
    """Verify that diagnose and seed-picking work with compressed hg.json."""

    def test_pick_reverse_slice_seeds_reads_gz(self, tmp_path: Path) -> None:
        """pick_reverse_slice_seeds should transparently read hg.json.gz."""
        data = {
            "nodes": [
                {"id": "n1", "name": "main", "kind": "function"},
                {"id": "n2", "name": "helper", "kind": "function"},
            ],
            "edges": [
                {"src": "n1", "dst": "n2", "edge_type": "calls"},
            ],
        }
        repo_out = _make_repo_output(tmp_path, "repo-a", hg_data=data,
                                     already_compressed=True)
        gz_path = str(repo_out / "hg.json.gz")
        seeds = bf.pick_reverse_slice_seeds(gz_path, count=3)
        # Should return results (or empty list) without error
        assert isinstance(seeds, list)

    def test_pick_reverse_slice_seeds_lang_diversity(self, tmp_path: Path) -> None:
        """Language-diversity guarantee ensures underrepresented major languages get a seed.

        Without diversity logic, all 3 seeds would be Python (higher scores due to
        higher out-degree).  With diversity, TypeScript gets at least one seed since
        it has >= 10% of callable nodes.
        """
        # Python nodes with high in-degree and out-degree (would dominate without diversity)
        py_nodes = [
            {"id": f"py{i}", "name": f"py_func_{i}", "kind": "function", "language": "python"}
            for i in range(10)
        ]
        # TypeScript nodes with moderate in-degree and out-degree
        ts_nodes = [
            {"id": f"ts{i}", "name": f"ts_handler_{i}", "kind": "function", "language": "typescript"}
            for i in range(5)
        ]
        # Edges: py0 has highest in-degree (called by many), py1 second, etc.
        edges = []
        # High out-degree for Python (each calls 5 others)
        for i in range(5):
            for j in range(5):
                if i != j:
                    edges.append({"src": f"py{i}", "dst": f"py{j}", "type": "calls"})
        # Moderate for TypeScript (each calls 4 others)
        for i in range(5):
            for j in range(4):
                if i != j:
                    edges.append({"src": f"ts{i}", "dst": f"ts{j}", "type": "calls"})
        # Extra incoming edges to py0, py1, py2 to boost their in-degree
        for i in range(5, 10):
            for j in range(3):
                edges.append({"src": f"py{i}", "dst": f"py{j}", "type": "calls"})

        data = {"nodes": py_nodes + ts_nodes, "edges": edges}
        repo_out = _make_repo_output(tmp_path, "polyglot-repo", hg_data=data)
        hg_path = str(repo_out / "hg.json")

        seeds = bf.pick_reverse_slice_seeds(hg_path, count=3)
        assert len(seeds) == 3

        # At least one seed should be TypeScript (ts* prefix)
        ts_seeds = [s for s in seeds if s.startswith("ts")]
        assert len(ts_seeds) >= 1, (
            f"Expected at least 1 TypeScript seed for language diversity, "
            f"but got: {seeds}"
        )

    def test_pick_reverse_slice_seeds_replaces_overrepresented(self, tmp_path: Path) -> None:
        """Diversity replacement only replaces seeds from over-represented languages.

        With 3 major languages (Python, TypeScript, Clojure), count=3 should
        yield one seed per language.  The replacement should take a Python seed
        (which has 2) rather than removing the only JS or Clojure seed.
        """
        py_nodes = [
            {"id": f"py{i}", "name": f"py_func_{i}", "kind": "function", "language": "python"}
            for i in range(10)
        ]
        ts_nodes = [
            {"id": f"ts{i}", "name": f"ts_handler_{i}", "kind": "function", "language": "typescript"}
            for i in range(5)
        ]
        clj_nodes = [
            {"id": f"clj{i}", "name": f"clj_fn_{i}", "kind": "function", "language": "clojure"}
            for i in range(5)
        ]
        edges = []
        # Python: high connectivity (each of 5 calls 4 others = out-degree 4)
        for i in range(5):
            for j in range(5):
                if i != j:
                    edges.append({"src": f"py{i}", "dst": f"py{j}", "type": "calls"})
        # Extra incoming to py0-py2 to boost in-degree
        for i in range(5, 10):
            for j in range(3):
                edges.append({"src": f"py{i}", "dst": f"py{j}", "type": "calls"})
        # TypeScript: moderate (each of 5 calls 4 others)
        for i in range(5):
            for j in range(5):
                if i != j:
                    edges.append({"src": f"ts{i}", "dst": f"ts{j}", "type": "calls"})
        # Clojure: moderate (each of 5 calls 4 others)
        for i in range(5):
            for j in range(5):
                if i != j:
                    edges.append({"src": f"clj{i}", "dst": f"clj{j}", "type": "calls"})

        data = {"nodes": py_nodes + ts_nodes + clj_nodes, "edges": edges}
        repo_out = _make_repo_output(tmp_path, "trilingual-repo", hg_data=data)
        hg_path = str(repo_out / "hg.json")

        seeds = bf.pick_reverse_slice_seeds(hg_path, count=3)
        assert len(seeds) == 3

        # Each major language should have exactly one seed
        seed_langs = set()
        for s in seeds:
            if s.startswith("py"):
                seed_langs.add("python")
            elif s.startswith("ts"):
                seed_langs.add("typescript")
            elif s.startswith("clj"):
                seed_langs.add("clojure")
        assert seed_langs == {"python", "typescript", "clojure"}, (
            f"Expected one seed per major language, but got: {seeds}"
        )

    def test_has_hg_json_detects_compressed(self, tmp_path: Path) -> None:
        """_has_hg_json should return True for repos with only hg.json.gz."""
        repo_out = _make_repo_output(tmp_path, "repo-a", already_compressed=True)
        assert bf._has_hg_json(str(repo_out)) is True

    def test_has_hg_json_detects_plain(self, tmp_path: Path) -> None:
        repo_out = _make_repo_output(tmp_path, "repo-b")
        assert bf._has_hg_json(str(repo_out)) is True

    def test_has_hg_json_returns_false_when_missing(self, tmp_path: Path) -> None:
        repo_out = tmp_path / "repo-c"
        repo_out.mkdir()
        assert bf._has_hg_json(str(repo_out)) is False


class TestTestPathRegex:
    """Tests for _TEST_PATH_RE test file detection.

    Regression: DEEP bakeoff assessments showed rslice seed selection picking
    test functions because paths like 'test/helpers/governance.js' (no leading
    slash) and 'testonly/mock_evm.rs' slipped through the regex.
    """

    def test_filters_test_dir_path_initial(self) -> None:
        """Path-initial test/ (no leading /) is filtered."""
        assert bf._TEST_PATH_RE.search("test/helpers/governance.js")

    def test_filters_test_dir_with_slash(self) -> None:
        """Standard /test/ directory is filtered."""
        assert bf._TEST_PATH_RE.search("src/test/helpers.js")

    def test_filters_tests_dir(self) -> None:
        """tests/ directory is filtered."""
        assert bf._TEST_PATH_RE.search("tests/unit/test_foo.py")

    def test_filters_spec_dir(self) -> None:
        """spec/ directory is filtered."""
        assert bf._TEST_PATH_RE.search("spec/models/user_spec.rb")

    def test_filters_testonly_dir(self) -> None:
        """Rust testonly/ directory is filtered."""
        assert bf._TEST_PATH_RE.search(
            "core/lib/multivm/src/versions/testonly/mock_evm.rs"
        )

    def test_filters_testonly_rs(self) -> None:
        """Rust testonly.rs file is filtered."""
        assert bf._TEST_PATH_RE.search("src/testonly.rs")

    def test_filters_dot_test_js(self) -> None:
        """JS .test.js files are filtered."""
        assert bf._TEST_PATH_RE.search("src/module.test.js")

    def test_filters_dot_spec_ts(self) -> None:
        """TS .spec.ts files are filtered."""
        assert bf._TEST_PATH_RE.search("src/module.spec.ts")

    def test_filters_go_test_file(self) -> None:
        """Go *_test.go files are filtered."""
        assert bf._TEST_PATH_RE.search("pkg/handler_test.go")

    def test_filters_test_prefix(self) -> None:
        """test_ prefixed files are filtered."""
        assert bf._TEST_PATH_RE.search("src/test_utils.py")

    def test_filters_tests_rs(self) -> None:
        """Rust tests.rs module is filtered."""
        assert bf._TEST_PATH_RE.search("src/tests.rs")

    def test_keeps_source_files(self) -> None:
        """Source files are not filtered."""
        assert not bf._TEST_PATH_RE.search("src/events.js")
        assert not bf._TEST_PATH_RE.search("src/controller.py")
        assert not bf._TEST_PATH_RE.search("crates/core/app/src/main.rs")

    def test_keeps_non_test_docker_file(self) -> None:
        """Files with test-like names but not in test paths are kept."""
        assert not bf._TEST_PATH_RE.search(
            "crates/recursion/gnark-ffi/src/ffi/docker.rs"
        )

    def test_keeps_attestation(self) -> None:
        """Files containing 'test' as substring in non-test context are kept."""
        assert not bf._TEST_PATH_RE.search("src/attestation.js")


class TestQualityThresholds:
    """Tests for QUALITY_THRESHOLDS values.

    WI-hulak: 9 of 21 WARN repos were false positives from thresholds
    set too tight for formal methods repos (pure first-party, small
    focused, highly-connected). Human approved raising:
    - tier1_pct good_max: 95 → 98 (pure first-party repos)
    - slice_coverage_pct good_max: 10 → 20 (small focused repos)
    - avg_slice_nodes good_max: 500 → 2000 (highly-connected repos)
    """

    def test_tier1_pct_good_max_accommodates_pure_first_party(self) -> None:
        """tier1_pct good_max >= 98 to avoid flagging pure first-party repos."""
        assert bf.QUALITY_THRESHOLDS["tier1_pct"]["good_max"] >= 98

    def test_slice_coverage_good_max_accommodates_small_repos(self) -> None:
        """slice_coverage_pct good_max >= 20 to avoid flagging small repos."""
        assert bf.QUALITY_THRESHOLDS["slice_coverage_pct"]["good_max"] >= 20

    def test_avg_slice_nodes_good_max_accommodates_connected_repos(self) -> None:
        """avg_slice_nodes good_max >= 2000 for highly-connected repos."""
        assert bf.QUALITY_THRESHOLDS["avg_slice_nodes"]["good_max"] >= 2000
