# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from pathlib import Path
from unittest.mock import patch

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerResult
from hypergumbo_core.schema import SCHEMA_VERSION


def test_run_behavior_map_writes_behavior_map_json(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    out_path = tmp_path / "hypergumbo.results.json"

    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    assert out_path.is_file()

    data = json.loads(out_path.read_text())

    assert data["schema_version"] == SCHEMA_VERSION
    assert data["view"] == "behavior_map"
    assert data["confidence_model"] == "hypergumbo-evidence-v2"
    assert data["analysis_incomplete"] is False
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)


def test_run_behavior_map_stamps_repo_fingerprint_on_every_run(tmp_path):
    """INV-tofur: every AnalysisRun in the output must carry a non-null
    repo_fingerprint matching the declared top-level scheme. Acceptance
    criterion from the tracker: self-analysis dogfood must show
    `analysis_runs[*].repo_fingerprint` non-null on every run."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # One source file ensures at least the python-ast analyzer runs and
    # contributes an AnalysisRun to the output.
    (repo_root / "a.py").write_text("def f(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path,
        include_sketch_precomputed=False,
    )
    data = json.loads(out_path.read_text())

    runs = data["analysis_runs"]
    assert runs, "expected at least one AnalysisRun in output"
    for run in runs:
        fp = run.get("repo_fingerprint")
        assert isinstance(fp, str) and len(fp) == 64, (
            f"AnalysisRun pass={run.get('pass')!r} has invalid "
            f"repo_fingerprint={fp!r}"
        )
    # All runs must share the same fingerprint — they analyzed the same
    # snapshot, so any divergence means a producer leaked its own value
    # before the orchestrator stamp, or the stamp skipped some runs.
    fps = {run["repo_fingerprint"] for run in runs}
    assert len(fps) == 1, f"runs produced divergent fingerprints: {fps}"

    assert data["repo_fingerprint_scheme"] == "hypergumbo-repofp-v1"


def test_sketch_precomputed_omits_vocabulary(tmp_path):
    """INV-padoz: the sketch_precomputed cache must not carry a ``vocabulary``
    field.

    It had no consumer anywhere (``compact`` strips ``sketch_precomputed``
    entirely; no reader in any package's ``src``) and, per the Wave-4
    typed-SketchPrecomputed direction, was resolved by DELETION rather than
    lemmatization. The retained cache fields stay populated — the deletion is
    surgical, not a removal of the whole precompute payload.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("def helper(): return 1\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path,
        include_sketch_precomputed=True,
    )
    data = json.loads(out_path.read_text())
    sp = data["sketch_precomputed"]

    assert "vocabulary" not in sp, "deleted sketch_precomputed.vocabulary reappeared"
    # Retained cache fields remain (surgical deletion).
    assert "config_info" in sp
    assert "readme_description" in sp
    assert "additional_file_centrality_scores" in sp


def test_run_behavior_map_no_symbol_has_absolute_path_in_name(tmp_path):
    """INV-vaguj: no Symbol may have an absolute filesystem path in its ``name``.

    The orchestrator's ``synthesize_file_symbols_for_dangling_edges``
    historically stamped whatever path a dangling endpoint id carried into
    ``Symbol.name``. When analyzers leaked absolute paths in their
    edge endpoint ids (most did), the resulting file-kind Symbols ended up
    with ``name="/home/.../foo.py"`` while ``path`` was normalised to
    relative. INV-dihif's user-visible explain leak is a downstream
    consequence.

    Property: after running the full pipeline, no Symbol has a ``name``
    that ``Path(name).is_absolute()`` reports as True.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # A few files in nested dirs so that imports/cross-file edges generate
    # dangling file-id endpoints — that's the codepath that triggers the
    # synthesiser.
    (repo_root / "src").mkdir()
    (repo_root / "src" / "a.py").write_text("from . import b\n\ndef f(): return b.g()\n")
    (repo_root / "src" / "b.py").write_text("def g(): return 1\n")
    (repo_root / "src" / "__init__.py").write_text("")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path,
        include_sketch_precomputed=False,
    )
    data = json.loads(out_path.read_text())

    offenders = [
        n for n in data["nodes"]
        if isinstance(n.get("name"), str) and Path(n["name"]).is_absolute()
    ]
    assert not offenders, (
        f"INV-vaguj violation: {len(offenders)} Symbol(s) have absolute paths "
        f"in their name field. Sample: {offenders[0]}"
    )


def test_run_behavior_map_classifies_supply_chain_tiers(tmp_path):
    """Nodes should have supply_chain tier classification based on path."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create files in different tier locations
    # Tier 1: src/ directory (first-party)
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    # Tier 3: node_modules/ (external dep) - but this is excluded by default
    # So we test with a file in root (defaults to first-party)
    (repo_root / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Find the nodes and check supply_chain field
    nodes = data["nodes"]
    assert len(nodes) >= 2, "Expected at least 2 nodes"

    for node in nodes:
        assert "supply_chain" in node, f"Node missing supply_chain: {node['id']}"
        sc = node["supply_chain"]
        assert "tier" in sc
        assert "tier_name" in sc
        assert "reason" in sc
        assert isinstance(sc["tier"], int)
        assert sc["tier"] in [1, 2, 3, 4]

    # Check specific file classifications
    src_nodes = [n for n in nodes if "src/app.py" in n["path"]]
    assert len(src_nodes) >= 1
    assert src_nodes[0]["supply_chain"]["tier"] == 1
    assert src_nodes[0]["supply_chain"]["tier_name"] == "first_party"
    assert "src/" in src_nodes[0]["supply_chain"]["reason"]


def test_run_behavior_map_includes_supply_chain_summary(tmp_path):
    """Output should include supply_chain_summary with counts per tier."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create some source files
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")
    (repo_root / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have supply_chain_summary
    assert "supply_chain_summary" in data
    summary = data["supply_chain_summary"]

    # Should have entries for each tier
    assert "first_party" in summary
    assert "internal_dep" in summary
    assert "external_dep" in summary
    assert "derived_skipped" in summary

    # First party should have counts
    fp = summary["first_party"]
    assert "files" in fp
    assert "symbols" in fp
    assert isinstance(fp["files"], int)
    assert isinstance(fp["symbols"], int)

    # derived_skipped should have paths list
    assert "paths" in summary["derived_skipped"]
    assert isinstance(summary["derived_skipped"]["paths"], list)


def test_find_derived_skipped_enumerates_derived_dirs_and_prunes_deps(tmp_path):
    """WI-jafoz: ``_find_derived_skipped`` walks the tree and records files
    under derived dirs (dist/__pycache__, top-level and nested) while pruning
    dependency dirs (so a dep's *own* build output is never misattributed)."""
    from hypergumbo_core.cli import _find_derived_skipped

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    # Top-level derived dir, with a nested subdir.
    (repo / "dist" / "assets").mkdir(parents=True)
    (repo / "dist" / "bundle.js").write_text("//\n")
    (repo / "dist" / "assets" / "chunk.js").write_text("//\n")
    # Top-level and nested __pycache__ (matches both ^__pycache__/ and /__pycache__/).
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.pyc").write_text("\n")
    (repo / "pkg" / "__pycache__").mkdir(parents=True)
    (repo / "pkg" / "__pycache__" / "x.pyc").write_text("\n")
    # A dependency dir with its OWN dist/ inside — must be pruned, not recorded.
    (repo / "node_modules" / "dep" / "dist").mkdir(parents=True)
    (repo / "node_modules" / "dep" / "dist" / "dep.js").write_text("//\n")
    (repo / "node_modules" / "dep" / "index.js").write_text("//\n")

    result = _find_derived_skipped(repo)

    # Derived files (top-level + nested) are recorded.
    assert "dist/bundle.js" in result
    assert "dist/assets/chunk.js" in result
    assert "__pycache__/mod.pyc" in result
    assert "pkg/__pycache__/x.pyc" in result
    # First-party source is not derived.
    assert "src/a.py" not in result
    # node_modules is pruned: neither its files nor its own dist/ are recorded.
    assert not any(p.startswith("node_modules/") for p in result)
    # Output is deterministic (sorted).
    assert result == sorted(result)


def test_run_behavior_map_derived_skipped_accounts_for_excluded_dirs(tmp_path):
    """WI-jafoz behavioral evidence: a repo whose only build artifacts live in a
    discovery-excluded derived dir previously reported ``derived_skipped`` as
    ``{files: 0, paths: []}`` (a silent lie). It must now enumerate them."""
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "app.py").write_text("def main(): pass\n")
    # A derived dir discovery gitignore-excludes before the tier-4 classifier.
    (repo_root / "build").mkdir()
    (repo_root / "build" / "artifact.o").write_text("\n")
    (repo_root / "build" / "artifact.js").write_text("//\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False
    )
    derived_skipped = json.loads(out_path.read_text())["supply_chain_summary"][
        "derived_skipped"
    ]

    # No longer a silent {files: 0, paths: []}: the build/ artifacts are counted.
    assert derived_skipped["files"] >= 2
    assert "build/artifact.o" in derived_skipped["paths"]
    assert "build/artifact.js" in derived_skipped["paths"]


def test_make_ecosystem_classifier_stdlib_third_party_and_unknown():
    """ADR-0041 §3: the ecosystem classifier maps stdlib/third_party from the
    single-source io_boundary catalog, and returns None for languages with no
    enumerated stdlib."""
    from hypergumbo_core.cli import _make_ecosystem_classifier

    classify = _make_ecosystem_classifier()
    # Python has an enumerated stdlib catalog (python.yaml stdlib_modules).
    assert classify("python", "os") == "stdlib"
    assert classify("python", "json") == "stdlib"
    assert classify("python", "requests") == "third_party"
    # A language with no catalog / no enumerated stdlib → None (cannot tell).
    assert classify("no_such_language_xyz", "whatever") is None


def test_run_behavior_map_stamps_ecosystem_on_boundary_nodes(tmp_path):
    """ADR-0041 §3 end-to-end: stdlib imports stamp ecosystem=stdlib, declared
    third-party imports stamp ecosystem=third_party, and supply_chain_summary
    sub-buckets tier-3 externals by ecosystem."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2"]\n'
    )
    (repo_root / "main.py").write_text(
        "import os\n"
        "import requests\n"
        "\n"
        "def run():\n"
        "    os.getcwd()\n"
        "    requests.get('x')\n"
    )
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)
    data = json.loads(out_path.read_text())

    def eco_for(modtoken):
        nodes = [
            n for n in data["nodes"]
            if n.get("kind") == "external_symbol" and modtoken in (n.get("id") or "")
        ]
        return {(n.get("meta") or {}).get("ecosystem") for n in nodes}, len(nodes)

    os_eco, os_n = eco_for(":os:")
    req_eco, req_n = eco_for(":requests:")
    assert os_n >= 1 and os_eco == {"stdlib"}, f"os should be stdlib; got {os_eco}"
    assert req_n >= 1 and req_eco == {"third_party"}, f"requests should be third_party; got {req_eco}"

    # supply_chain_summary tier-3 ecosystem sub-bucket present and counted.
    eco_summary = data["supply_chain_summary"]["external_dep"]["ecosystem"]
    assert eco_summary.get("stdlib", 0) >= 1
    assert eco_summary.get("third_party", 0) >= 1


def test_run_behavior_map_compact_mode(tmp_path):
    """Compact mode produces coverage-based output with summaries."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create source files so we have symbols to work with
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main(): helper()\n")
    (src_dir / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "compact.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        compact=True,
        coverage=0.8,
        budgets="none",  # Disable tiers for this test
        include_sketch_precomputed=False,
    )

    data = json.loads(out_path.read_text())

    # Should have compact view and nodes_summary
    assert data["view"] == "compact"
    assert "nodes_summary" in data

    summary = data["nodes_summary"]
    assert "included" in summary
    assert "omitted" in summary

    # Included summary should have count and coverage
    assert "count" in summary["included"]
    assert "coverage" in summary["included"]

    # Omitted summary should have semantic flavor
    assert "count" in summary["omitted"]
    assert "top_words" in summary["omitted"]
    assert "top_paths" in summary["omitted"]
    assert "kinds" in summary["omitted"]


def test_run_behavior_map_default_tiered_output(tmp_path):
    """Default run generates tiered output files (4k, 16k, 64k)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create source files
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    # Main file should exist
    assert out_path.is_file()

    # Default budget files should be generated
    budget_4k = tmp_path / "hypergumbo.results.4k.json"
    budget_16k = tmp_path / "hypergumbo.results.16k.json"
    budget_64k = tmp_path / "hypergumbo.results.64k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"
    assert budget_64k.is_file(), "64k budget file should be generated"

    # Check budget file structure
    data_4k = json.loads(budget_4k.read_text())
    assert data_4k["view"] == "tiered"
    assert data_4k["tier_tokens"] == 4000
    assert "nodes_summary" in data_4k

    data_16k = json.loads(budget_16k.read_text())
    assert data_16k["view"] == "tiered"
    assert data_16k["tier_tokens"] == 16000

    data_64k = json.loads(budget_64k.read_text())
    assert data_64k["view"] == "tiered"
    assert data_64k["tier_tokens"] == 64000


def test_run_behavior_map_custom_budgets(tmp_path):
    """Custom budget specification generates specified budget files."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="2k,8k",  # Custom budgets
        include_sketch_precomputed=False,
    )

    # Custom budget files should be generated
    budget_2k = tmp_path / "output.2k.json"
    budget_8k = tmp_path / "output.8k.json"

    assert budget_2k.is_file(), "2k budget file should be generated"
    assert budget_8k.is_file(), "8k budget file should be generated"

    # Default budgets should NOT be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert not budget_4k.exists(), "4k budget file should NOT be generated"
    assert not budget_16k.exists(), "16k budget file should NOT be generated"
    assert not budget_64k.exists(), "64k budget file should NOT be generated"

    # Check custom budget structure
    data_2k = json.loads(budget_2k.read_text())
    assert data_2k["view"] == "tiered"
    assert data_2k["tier_tokens"] == 2000


def test_run_behavior_map_budgets_none(tmp_path):
    """budgets='none' disables budget file generation."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="none",  # Disable budget output
        include_sketch_precomputed=False,
    )

    # Main file should exist
    assert out_path.is_file()

    # No budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert not budget_4k.exists(), "4k budget file should NOT be generated when budgets=none"
    assert not budget_16k.exists(), "16k budget file should NOT be generated when budgets=none"
    assert not budget_64k.exists(), "64k budget file should NOT be generated when budgets=none"


def test_run_behavior_map_budgets_default_keyword(tmp_path):
    """budgets='default' generates standard budget files."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="default",  # Explicit default
        include_sketch_precomputed=False,
    )

    # Default budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"
    assert budget_64k.is_file(), "64k budget file should be generated"


def test_run_behavior_map_budgets_invalid_spec_skipped(tmp_path):
    """Invalid budget specs are silently skipped, valid ones still work."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    # Mix valid and invalid budget specs
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="4k,invalid_budget,16k",  # Invalid spec in the middle
        include_sketch_precomputed=False,
    )

    # Main file should exist
    assert out_path.is_file()

    # Valid budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"

    # Invalid budget file should NOT exist
    budget_invalid = tmp_path / "output.invalid_budget.json"
    assert not budget_invalid.exists(), "Invalid budget file should NOT be generated"


def test_run_behavior_map_normalizes_linker_absolute_paths(tmp_path):
    """Linker-produced symbols with absolute paths are normalized to relative."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    abs_path = str(repo_root / "linker_generated.py")
    linker_sym = Symbol(
        id="linker::generated",
        name="generated",
        kind="function",
        language="python",
        path=abs_path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
    )
    fake_result = LinkerResult(symbols=[linker_sym], edges=[])

    real_run_all_linkers = None

    def patched_run_all_linkers(ctx, limits=None):
        results = real_run_all_linkers(ctx, limits=limits)
        results.append(("fake_linker", fake_result))
        return results

    import hypergumbo_core.cli as cli_mod

    real_run_all_linkers = cli_mod.run_all_linkers
    with patch.object(cli_mod, "run_all_linkers", side_effect=patched_run_all_linkers):
        out_path = tmp_path / "output.json"
        run_behavior_map(
            repo_root=repo_root,
            out_path=out_path,
            budgets="none",
            include_sketch_precomputed=False,
        )

    data = json.loads(out_path.read_text())
    linker_nodes = [n for n in data["nodes"] if n["id"] == "linker::generated"]
    assert len(linker_nodes) == 1
    assert linker_nodes[0]["path"] == "linker_generated.py"


def test_run_behavior_map_emits_partial_json_when_analyzer_crashes(tmp_path):
    """§17 fail-open end-to-end (WI-madal L3): a crashing registered analyzer
    does not abort the run — valid partial JSON is still written to disk, the
    healthy analyzers' nodes survive, and the crash is recorded pass-level.

    This drives the full production ``run_behavior_map`` path (not just the
    orchestrator function), exercising serialization of the ``crashed:`` entry
    — the behavioral closure-evidence bar set by the sibling L1/L2 fix.
    """
    from hypergumbo_core.analyze.registry import _ANALYZER_REGISTRY, register_analyzer

    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")

    @register_analyzer("crash-e2e", priority=999)
    def _crash(root, **kwargs):
        raise RuntimeError("e2e analyzer boom")

    try:
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path,
            out_path=out_path,
            budgets="none",
            include_sketch_precomputed=False,
        )

        data = json.loads(out_path.read_text())  # (a) valid JSON on disk
        assert len(data["nodes"]) >= 1  # (b) healthy analyzer output survived
        crashed = [
            s for s in data["limits"]["skipped_passes"] if s["pass"] == "crash-e2e"
        ]
        assert len(crashed) == 1  # (c) crash recorded pass-level
        assert crashed[0]["reason"].startswith("crashed: RuntimeError")
        assert "e2e analyzer boom" in crashed[0]["reason"]
        # (d) top-level honesty signal reflects the crash (not clobbered).
        assert "crashed" in data["limits"]["partial_results_reason"]
    finally:
        _ANALYZER_REGISTRY.pop("crash-e2e", None)




# ---------------------------------------------------------------------------
# WI-kojob: --gzip and --no-sketch-fan-out flags
# ---------------------------------------------------------------------------


def test_run_behavior_map_gzip_output_writes_gzipped_json(tmp_path):
    """WI-kojob: gzip_output=True writes a gzipped JSON to the given path.

    `gunzip + json.loads` round-trips: the inner payload is the same
    behavior-map dict that would have been written uncompressed.
    """
    import gzip

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json.gz"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="none",
        gzip_output=True,
        include_sketch_precomputed=False,
    )

    assert out_path.is_file()
    # The file is valid gzip.
    with gzip.open(out_path, "rt") as f:
        data = json.load(f)
    assert "nodes" in data
    assert "edges" in data
    assert data.get("schema_version") == SCHEMA_VERSION


def test_run_behavior_map_gzip_output_also_gzips_budget_tiers(tmp_path):
    """WI-kojob: when gzip_output=True, budget-tier files are also gzipped.

    Default budgets emit `<stem>.{4k,16k,64k}.json`; with gzip they become
    `<stem>.{4k,16k,64k}.json.gz`, each independently valid gzip.
    """
    import gzip

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json.gz"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="default",
        gzip_output=True,
        include_sketch_precomputed=False,
    )

    for spec in ("4k", "16k", "64k"):
        budget = tmp_path / f"output.{spec}.json.gz"
        assert budget.is_file(), f"{spec} budget file missing"
        with gzip.open(budget, "rt") as f:
            tier = json.load(f)
        assert "schema_version" in tier


def test_run_behavior_map_no_sketch_fan_out_skips_budget_files(tmp_path):
    """WI-kojob: no_sketch_fan_out=True suppresses budget-tier emission.

    Equivalent to `budgets='none'` but expressed as a dedicated flag —
    matches the CLI ergonomic the UAT campaign requested.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        no_sketch_fan_out=True,
        include_sketch_precomputed=False,
    )

    # Main file present.
    assert out_path.is_file()
    # No budget files.
    for spec in ("4k", "16k", "64k"):
        budget = tmp_path / f"output.{spec}.json"
        assert not budget.exists(), (
            f"{spec} budget should be suppressed by no_sketch_fan_out=True"
        )


def test_run_behavior_map_no_sketch_fan_out_overrides_default_budgets(tmp_path):
    """WI-kojob: no_sketch_fan_out wins over budgets=default.

    A user who passes both flags wants no sketch fan-out; the named flag
    is the more specific signal.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="default",
        no_sketch_fan_out=True,
        include_sketch_precomputed=False,
    )

    for spec in ("4k", "16k", "64k"):
        budget = tmp_path / f"output.{spec}.json"
        assert not budget.exists()


def test_run_behavior_map_gzip_and_no_sketch_fan_out_combine(tmp_path):
    """WI-kojob: --gzip + --no-sketch-fan-out together produce one gzipped file."""
    import gzip

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json.gz"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        gzip_output=True,
        no_sketch_fan_out=True,
        include_sketch_precomputed=False,
    )

    assert out_path.is_file()
    # No budget files (gzipped or not).
    for spec in ("4k", "16k", "64k"):
        for suffix in (".json", ".json.gz"):
            budget = tmp_path / f"output.{spec}{suffix}"
            assert not budget.exists()
    # Main file is gzipped.
    with gzip.open(out_path, "rt") as f:
        data = json.load(f)
    assert "nodes" in data


def test_synthetic_producers_emit_resolvable_provenance(tmp_path):
    """synthetic:F1 behavioral closure: the two synthetic-node producers
    (orchestrator file-symbol synthesis + boundary external_symbol synthesis)
    emit a real AnalysisRun and stamp resolvable provenance on their nodes.

    Previously these nodes shipped ``origin=[]`` / ``origin_run_id=''`` — a
    third sentinel state that broke the node->AnalysisRun referential-integrity
    JOIN (WI-dizir 492 file nodes, WI-sijut 1645 external_symbol origin=[],
    WI-mosil 2236 origin_run_id=''). After the fix every synthetic node carries
    a non-empty ``origin`` (a synthesis-mechanism pass-id) and an
    ``origin_run_id`` that resolves to a real ``AnalysisRun.execution_id``.

    The fixture deliberately triggers BOTH producers: ``import os`` (stdlib,
    unresolved) yields boundary external_symbol nodes, and app.py's own
    module-level import edges carry a ``make_file_id``-shape src that the
    orchestrator synthesizes into a ``kind='file'`` Symbol (any import in
    app.py fires this — the synthesized node is
    ``python:app.py:1-1:file:file``, derived from app.py's import-edge src,
    not from the import target).
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text(
        "import os\n"
        "from helpers import greet\n"
        "\n"
        "def main():\n"
        "    return greet(os.getcwd())\n"
    )
    (repo_root / "helpers.py").write_text(
        "def greet(x):\n"
        "    return f'hi {x}'\n"
    )

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path,
        include_sketch_precomputed=False,
    )
    data = json.loads(out_path.read_text())

    exec_ids = {r["execution_id"] for r in data["analysis_runs"]}
    passes = {r.get("pass") for r in data["analysis_runs"]}
    run_pass = {r["execution_id"]: r.get("pass") for r in data["analysis_runs"]}
    SYNTH = {
        "orchestrator_file_symbol_synthesis",
        "boundary_external_symbol_synthesis",
    }

    # Boundary external_symbol nodes now carry the boundary synthesis mechanism
    # (was origin=[] — zero provenance).
    externals = [n for n in data["nodes"] if n["kind"] == "external_symbol"]
    assert externals, "fixture produced no external_symbol boundary nodes"
    for n in externals:
        assert n["origin"] == ["boundary_external_symbol_synthesis"], n["id"]

    # Every synthetic node's provenance resolves to a real AnalysisRun, and to
    # ITS OWN producer's run (guards against a cross-wired execution_id).
    synthetic_nodes = [
        n for n in data["nodes"] if set(n.get("origin") or []) & SYNTH
    ]
    assert synthetic_nodes, "fixture produced no synthetic nodes to validate"
    for n in synthetic_nodes:
        assert n["origin_run_id"], (
            f"synthetic node {n['id']} has empty origin_run_id"
        )
        assert n["origin_run_id"] in exec_ids, (
            f"synthetic node {n['id']} origin_run_id {n['origin_run_id']!r} "
            "does not resolve to any AnalysisRun.execution_id"
        )
        assert run_pass[n["origin_run_id"]] == n["origin"][0], (
            f"synthetic node {n['id']} carries origin {n['origin']!r} but its "
            f"origin_run_id points at a {run_pass[n['origin_run_id']]!r} run "
            "(cross-wired producer)"
        )

    # Partition guard: BOTH producers actually fired (so the loop above can
    # never go single-producer-vacuous), and each emitted its AnalysisRun.
    origins_seen = {tuple(n.get("origin") or []) for n in synthetic_nodes}
    assert ("orchestrator_file_symbol_synthesis",) in origins_seen
    assert ("boundary_external_symbol_synthesis",) in origins_seen
    assert "orchestrator_file_symbol_synthesis" in passes
    assert "boundary_external_symbol_synthesis" in passes


def test_synthesis_runs_absent_when_no_synthetic_nodes(tmp_path):
    """synthetic:F1 conditional-append (FALSE branch): a synthesis AnalysisRun
    is recorded ONLY when its producer actually mints nodes. A trivial repo with
    no dangling/external edges leaves BOTH synthesis passes absent from
    analysis_runs (no empty-pass records) — the stated rationale for the
    ``if produced:`` guards at both orchestrator sites."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # A module-level assignment: one analyzer runs, but no imports (no
    # make_file_id-shape dangling edge) and no external calls (no boundary).
    (repo_root / "m.py").write_text("VALUE = 1\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path,
        include_sketch_precomputed=False,
    )
    data = json.loads(out_path.read_text())
    passes = {r.get("pass") for r in data["analysis_runs"]}
    assert "orchestrator_file_symbol_synthesis" not in passes
    assert "boundary_external_symbol_synthesis" not in passes


def test_node_bearing_path_gets_file_anchor_wi_dagif(tmp_path):
    """WI-dagif (file-anchor:F1, node-bearing slice): a file with content nodes
    but no imports/calls (hence no make_file_id edge) still gets a kind="file"
    anchor at the orchestrator chokepoint, and the containment linker's
    span-based pass roots its top-level members at it (rootful contains tree)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # Class-only Java file: class + fields, NO imports/calls -> no make_file_id
    # edge endpoint -> the dangling-edge synthesizer alone would not anchor it.
    (repo_root / "Data.java").write_text(
        "public class Data {\n    int x;\n    String name;\n}\n"
    )
    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)
    data = json.loads(out_path.read_text())
    nodes = data["nodes"]

    # The node-bearing path now carries exactly one file anchor.
    anchors = [n for n in nodes if n.get("kind") == "file" and n["path"] == "Data.java"]
    assert len(anchors) == 1, f"expected one file anchor for Data.java, got {anchors}"
    anchor_id = anchors[0]["id"]

    # Closure (scoped to the real source path): the class node's path has a
    # file anchor — i.e. the contains tree is no longer rootless.
    cls = next(n for n in nodes if n.get("kind") == "class" and n["path"] == "Data.java")
    contains = {(e["src"], e["dst"]) for e in data["edges"] if e["type"] == "contains"}
    assert (anchor_id, cls["id"]) in contains, (
        "file anchor does not contain its top-level class — the containment "
        "linker's span-based pass should root members at the synthesized anchor"
    )


def test_additional_file_candidate_anchored_and_subset_invariant_f1_f4(tmp_path):
    """file-anchor:F1+F4: every Additional-File candidate (config/doc) gets a
    kind="file" anchor even with NO content nodes (F1), and every
    additional_file_centrality_scores key is a real file-anchor node path —
    the WI-rajod subset invariant (F4)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # `helper` is called twice -> in_degree 2 -> qualifies for mention centrality;
    # README mentions it, so the Additional-Files surface scores non-empty.
    (repo_root / "app.py").write_text(
        "def helper():\n    return 1\n\n"
        "def a():\n    return helper()\n\n"
        "def b():\n    return helper()\n"
    )
    (repo_root / "README.md").write_text(
        "# App\n\nThe `helper` function is the core utility.\n"
    )
    (repo_root / "config.yaml").write_text("name: app\nversion: 1\n")
    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(
        repo_root=repo_root, out_path=out_path, include_sketch_precomputed=True
    )
    data = json.loads(out_path.read_text())
    nodes = data["nodes"]

    file_paths = {n["path"] for n in nodes if n.get("kind") == "file"}
    # F1: config/doc candidates are anchored even though they carry no content nodes.
    assert "README.md" in file_paths
    assert "config.yaml" in file_paths

    # F4 + WI-rajod subset invariant: the producer ran (non-empty surface) and
    # every centrality key is a real file-anchor node path.
    scores = data["sketch_precomputed"]["additional_file_centrality_scores"]
    assert "README.md" in scores, (
        f"expected README.md in the additional-file centrality surface, got {scores}"
    )
    assert set(scores) <= file_paths, (
        f"centrality keys not subset of file-anchor node paths: "
        f"{set(scores) - file_paths}"
    )
