# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-jozan: top-level-source → matching-test mapper.

smart-test's reverse-slice via ``hypergumbo slice --files`` only reaches
source inside the ``packages/*/src/**`` tree. Any PR that only touches
``.agent/hooks/_shared/*.py`` or ``scripts/*`` produced an empty slice
and the per-PR CI gate was silent. This module maps those top-level
source files to their matching ``tests/test_<basename>.py`` files so
smart-test can run them alongside the slice-found tests.

The predicate is file-existence-based: a match is reported only when
the target test file actually exists on disk. The tests build an
isolated tmp tree so they do not depend on the repo's current test
layout and so new top-level tests do not silently change test
behaviour.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).parent.parent
MODULE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "top_level_test_map.py"


def _import_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("top_level_test_map", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_tree(tmp_path: Path, test_filenames: list[str]) -> Path:
    """Create a fake repo tree under tmp_path with the given test files."""
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    for name in test_filenames:
        (tmp_path / "tests" / name).write_text("# fixture\n")
    return tmp_path


# --- _candidate_test_basename ---


def test_agent_hooks_shared_py_maps_to_basename() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/awaits_bakeoff_nudge.py") == "awaits_bakeoff_nudge"


def test_agent_hooks_shared_subdir_is_skipped() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/subdir/foo.py") is None


def test_shared_non_source_extension_is_skipped() -> None:
    """Only .py/.sh in _shared/ map — a README there is not a source file."""
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/README.md") is None
    assert mod._candidate_test_basename(".agent/hooks/_shared/notes") is None


def test_scripts_hyphen_collapses_to_underscore() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/agent-supervisor") == "agent_supervisor"
    assert mod._candidate_test_basename("scripts/auto-pr") == "auto_pr"


def test_scripts_strips_py_and_sh_extension() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/foo.py") == "foo"
    assert mod._candidate_test_basename("scripts/foo.sh") == "foo"


def test_scripts_subdir_is_skipped() -> None:
    """scripts/lib/forgejo-api.sh should not auto-map — sub-scripts have
    generic basenames that would over-match unrelated tests."""
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/lib/forgejo-api.sh") is None


def test_scripts_empty_name_is_skipped() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/") is None


def test_unrelated_path_returns_none() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("packages/hypergumbo-core/src/foo.py") is None
    assert mod._candidate_test_basename("docs/README.md") is None
    assert mod._candidate_test_basename("") is None


def test_other_agent_dir_is_skipped() -> None:
    """Only .agent/hooks/_shared/ is mapped — .agent/tracker/, .agent/hooks/claude-code/,
    etc. have their own test conventions or no tests at all."""
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/claude-code/stop.sh") is None
    assert mod._candidate_test_basename(".agent/tracker/config.yaml") is None


# --- map_to_tests ---


def test_map_returns_only_existing_tests(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_awaits_bakeoff_nudge.py"])
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/awaits_bakeoff_nudge.py",
            ".agent/hooks/_shared/nonexistent.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_awaits_bakeoff_nudge.py"]


def test_map_deduplicates(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_watched_process.py"])
    # Same source file appearing twice in the diff (should not happen but
    # guard anyway).
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/watched_process.py",
            ".agent/hooks/_shared/watched_process.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_watched_process.py"]


def test_map_sorts_output(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_a.py", "test_b.py", "test_c.py"])
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/c.py",
            ".agent/hooks/_shared/a.py",
            ".agent/hooks/_shared/b.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


def test_map_skips_blank_lines(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_foo.py"])
    out = mod.map_to_tests(
        [
            "",
            "   ",
            ".agent/hooks/_shared/foo.py",
            "\n",
        ],
        tmp_path,
    )
    assert out == ["tests/test_foo.py"]


def test_map_scripts_hyphen_to_underscore(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_agent_supervisor.py"])
    out = mod.map_to_tests(["scripts/agent-supervisor"], tmp_path)
    assert out == ["tests/test_agent_supervisor.py"]


def test_map_empty_input(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, [])
    assert mod.map_to_tests([], tmp_path) == []


def test_map_skips_unmapped_non_empty_paths(tmp_path: Path) -> None:
    """Non-empty paths whose _candidate_test_basename returns None hit the
    continue branch and contribute nothing to the result — covered here so
    that branch stays exercised."""
    mod = _import_module()
    _make_tree(tmp_path, ["test_foo.py"])
    out = mod.map_to_tests(
        [
            "docs/README.md",
            "packages/hypergumbo-core/src/foo.py",
            ".agent/hooks/_shared/foo.py",  # this one DOES map
        ],
        tmp_path,
    )
    assert out == ["tests/test_foo.py"]


def test_map_no_tests_directory(tmp_path: Path) -> None:
    """When tests/ doesn't exist, nothing matches (no crash)."""
    mod = _import_module()
    out = mod.map_to_tests([".agent/hooks/_shared/foo.py"], tmp_path)
    assert out == []


# --- CLI ---


def test_cli_reads_stdin_and_prints_matches(tmp_path: Path) -> None:
    _make_tree(tmp_path, ["test_watched_process.py", "test_awaits_bakeoff_nudge.py"])
    stdin = (
        ".agent/hooks/_shared/watched_process.py\n"
        ".agent/hooks/_shared/awaits_bakeoff_nudge.py\n"
        "packages/hypergumbo-core/src/foo.py\n"
    )
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(tmp_path)],
        input=stdin,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "tests/test_awaits_bakeoff_nudge.py",
        "tests/test_watched_process.py",
    ]


def test_cli_empty_stdin_exits_zero(tmp_path: Path) -> None:
    _make_tree(tmp_path, [])
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(tmp_path)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_missing_repo_root_arg_exits_two() -> None:
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr


# --- main() direct invocation for coverage of the return statement ---


def test_main_returns_zero_on_success(tmp_path: Path, monkeypatch) -> None:
    import io
    mod = _import_module()
    _make_tree(tmp_path, ["test_watched_process.py"])
    monkeypatch.setattr("sys.stdin", io.StringIO(".agent/hooks/_shared/watched_process.py\n"))
    rc = mod.main(["top_level_test_map.py", str(tmp_path)])
    assert rc == 0


def test_main_returns_two_on_missing_arg() -> None:
    mod = _import_module()
    rc = mod.main(["top_level_test_map.py"])
    assert rc == 2


# ---------------------------------------------------------------------------
# WI-bisar: the map is separator-insensitive and prefix-based
#
# The original rule was exact (``scripts/<name>`` → ``tests/test_<name>.py``
# with hyphens collapsed), which meant a script whose tests were split across
# several files had all but one of them selected by nothing. Measured on this
# repo at the time: 27 of 73 root tests were reachable from any top-level
# source, and ``scripts/auto-pr`` — the script that merges every PR here —
# reached 0 of its 11 test files, because they are named ``test_autopr_*``
# while the collapse rule produces ``auto_pr``. A plain prefix match does NOT
# fix that (``test_autopr_x`` does not start with ``test_auto_pr``); comparing
# with separators removed is what does.
# ---------------------------------------------------------------------------


def test_map_matches_suffixed_test_files(tmp_path: Path) -> None:
    """One script, several test files — all of them are selected."""
    mod = _import_module()
    _make_tree(tmp_path, [
        "test_agent_supervisor.py",
        "test_agent_supervisor_meta_breaker.py",
    ])
    out = mod.map_to_tests(["scripts/agent-supervisor"], tmp_path)
    assert out == [
        "tests/test_agent_supervisor.py",
        "tests/test_agent_supervisor_meta_breaker.py",
    ]


def test_map_is_separator_insensitive(tmp_path: Path) -> None:
    """``scripts/auto-pr`` reaches ``test_autopr_*`` — the motivating case.

    The separator placement differs between the script name and its tests,
    so any rule that compares underscores literally misses every one of them.
    """
    mod = _import_module()
    _make_tree(tmp_path, [
        "test_auto_pr.py",
        "test_autopr_title_desc_flags.py",
        "test_autopr_tracker_id.py",
    ])
    out = mod.map_to_tests(["scripts/auto-pr"], tmp_path)
    assert out == [
        "tests/test_auto_pr.py",
        "tests/test_autopr_title_desc_flags.py",
        "tests/test_autopr_tracker_id.py",
    ]


def test_map_does_not_match_an_unrelated_name(tmp_path: Path) -> None:
    """Widening is prefix-anchored, not substring — unrelated tests stay out."""
    mod = _import_module()
    _make_tree(tmp_path, ["test_tracker_sync.py", "test_my_auto_pr_helper.py"])
    assert mod.map_to_tests(["scripts/auto-pr"], tmp_path) == []


def test_map_shared_shell_helper_maps(tmp_path: Path) -> None:
    """``_shared/*.sh`` maps just as ``_shared/*.py`` does.

    The directory holds 13 shell helpers against 8 Python ones, so requiring
    ``.py`` there left most of a covered directory unreachable — while
    ``scripts/`` already accepted ``.py``, ``.sh`` and extensionless names.
    """
    mod = _import_module()
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_touch_heartbeat.py").write_text("# fixture\n")
    out = mod.map_to_tests([".agent/hooks/_shared/touch_heartbeat.sh"], tmp_path)
    assert out == ["tests/test_touch_heartbeat.py"]


def test_map_stem_with_no_alphanumerics_matches_nothing(tmp_path: Path) -> None:
    """A stem that normalizes to empty must not match every test.

    ``scripts/-`` collapses to ``_`` and then to the empty string, and
    ``"anything".startswith("")`` is True — so without an explicit guard this
    one path would select the entire root suite.
    """
    mod = _import_module()
    _make_tree(tmp_path, ["test_foo.py", "test_bar.py"])
    assert mod.map_to_tests(["scripts/-"], tmp_path) == []


# ---------------------------------------------------------------------------
# Reachability ratchet against the REAL tree
#
# The tests above run against isolated tmp trees so they do not depend on the
# repo's layout — deliberate, and the reason this gap survived: nothing ever
# compared the mapping against the actual filenames, so the unit was correct
# while the wiring was inert. This ratchet closes that by measuring the real
# directory, and it is two-sided: a newly-added unreachable test fails, and an
# exempt test that becomes reachable also fails so the list cannot rot.
# ---------------------------------------------------------------------------

#: Root tests no name-based rule can reach, with the reason each is exempt.
#: Shrink-only — removing an entry is the goal, adding one needs justification.
KNOWN_UNREACHABLE = {
    # scripts/lib/* is deliberately unmapped (the module's own docstring
    # records the decision: sub-script basenames are generic and would
    # over-match). These test scripts/lib/pool_utils.py and forgejo-api.sh.
    "test_pool_utils.py",
    "test_forge_backend_github.py",
    "test_resolve_forge_token_github.py",
    "test_ci_status_endpoints_failover_aware.py",
    "test_hg_github_token_documented.py",
    # Cover the transcript pipeline across several sources at once, which no
    # name-based rule reaches. (Vendor hook dirs are no longer exempt: the
    # 2026-08-01 INV-lizor extension maps them by name + parity floor, which
    # is what removed test_session_start_respawn / _agent_notes and
    # test_stop_hook_state_write_discipline from this list.)
    "test_watcher_lifecycle.py",
    "test_transcript_scrub_wiring.py",
    "test_transcript_pipeline_properties.py",
    "test_training_log_parse_misses.py",
    # Named for the behaviour under test rather than the file under test, so
    # no name-based rule can reach them. test_bakeoff_resolve_workdir_prefix
    # covers bakeoff-broad AND bakeoff-deep; test_workflow_cli_invocation
    # covers check-schema-coverage; test_dead_code_prospector's name is a
    # prefix OF its source (dead-code-prospector-run.py), the inverse
    # direction. These want a declarative "covers:" marker, not a heuristic.
    "test_bakeoff_resolve_workdir_prefix.py",
    "test_workflow_cli_invocation.py",
    "test_dead_code_prospector.py",
    "test_rct_public_api_pinned.py",
    # Assert over governance/workflow artifacts, not a single source file.
    "test_codeowners_governance.py",
    "test_full_suite_coverage_teeth.py",
    # WI-hajif: a recurrence guard over the ABSENCE of the retired CI-failover
    # layer. It scans scripts/, .githooks/, .agent/hooks/ and packages/*/src for
    # failover tokens, so it maps to no single source by construction — the
    # thing it guards is a property of the tree, not a file. Consequence stated
    # plainly rather than hidden by the exemption: it therefore runs in the full
    # suite and not per-PR, so a PR that reintroduces failover machinery is
    # caught after merge rather than before it. Fixing that needs the
    # declarative "covers:" marker this list keeps asking for, not a rename.
    "test_ci_failover_retired.py",
    # Covers tests/_forge_github_harness.py -- a TEST HELPER, which is a third
    # category this mapper does not claim: it maps scripts/ and
    # .agent/hooks/_shared/, not tests/. Extending it to tests/_<name>.py
    # would be a rule for a category of exactly one (that helper is the only
    # underscore-prefixed module under tests/), so this is an exemption rather
    # than a mapping. Revisit if a second test helper acquires a test.
    "test_forge_github_harness.py",
}


def _real_top_level_sources() -> list[str]:
    """Every top-level source path the mapper claims to cover."""
    out = [
        f"scripts/{p.name}"
        for p in sorted((REPO_ROOT / "scripts").iterdir())
        if p.is_file()
    ]
    out += [
        f".agent/hooks/_shared/{p.name}"
        for p in sorted((REPO_ROOT / ".agent" / "hooks" / "_shared").iterdir())
        if p.is_file() and p.suffix in (".py", ".sh")
    ]
    # Vendor hook dirs are part of the claimed domain since the INV-lizor
    # extension; enumerating them keeps the two-sided ratchet honest about
    # what the map can actually reach.
    hooks_dir = REPO_ROOT / ".agent" / "hooks"
    for vendor in sorted(hooks_dir.iterdir()):
        if vendor.is_dir() and vendor.name != "_shared":
            out += [
                f".agent/hooks/{vendor.name}/{p.name}"
                for p in sorted(vendor.iterdir())
                if p.is_file() and p.suffix in (".py", ".sh")
            ]
    return out


def _real_unreachable() -> set[str]:
    mod = _import_module()
    reachable = set(mod.map_to_tests(_real_top_level_sources(), REPO_ROOT))
    every = {f"tests/{p.name}" for p in (REPO_ROOT / "tests").glob("test_*.py")}
    return {Path(t).name for t in every - reachable}


def test_no_new_unreachable_top_level_tests() -> None:
    """A new root test must be reachable from some top-level source."""
    newly = _real_unreachable() - KNOWN_UNREACHABLE
    assert not newly, (
        "these root tests are selected by no top-level source, so a change to "
        "the code they cover will not run them in per-PR CI: "
        f"{sorted(newly)}. Either name the test after the file it covers, or "
        "add it to KNOWN_UNREACHABLE with a reason."
    )


def test_known_unreachable_list_has_not_rotted() -> None:
    """An exempt test that became reachable must leave the list."""
    stale = KNOWN_UNREACHABLE - _real_unreachable()
    assert not stale, (
        f"these are now reachable — remove them from KNOWN_UNREACHABLE: "
        f"{sorted(stale)}"
    )


def test_the_motivating_case_is_actually_fixed() -> None:
    """scripts/auto-pr reaches its whole test family, not just one file.

    Pinned against the real tree because the tmp-tree tests cannot catch a
    regression in the repo's own naming.
    """
    mod = _import_module()
    hits = mod.map_to_tests(["scripts/auto-pr"], REPO_ROOT)
    assert len(hits) >= 11, hits
    assert "tests/test_auto_pr.py" in hits
    assert "tests/test_autopr_tracker_id.py" in hits


# ---------------------------------------------------------------------------
# Vendor hook dirs (INV-lizor re-scope, 2026-08-01). The mapper now claims
# .agent/hooks/<vendor>/<name>.(sh|py): every vendor hook is a thin wrapper
# sourcing _shared logic (the AGENTS.md Vendor Parity table), so any change
# there must at minimum select the cross-vendor parity tests — the "floor" —
# plus whatever the name-based rule reaches. Before this, a change confined
# to a vendor dir selected NOTHING and ci.yml skipped pytest.
# ---------------------------------------------------------------------------


def test_vendor_hook_selects_the_parity_floor() -> None:
    """Any direct vendor-hook file reaches the parity tests, even when its
    own name matches no test (post-tool-use-transcript has none)."""
    mod = _import_module()
    root = Path(__file__).parent.parent  # real repo: floor files must exist
    hits = mod.map_to_tests(
        [".agent/hooks/claude-code/post-tool-use-transcript.sh"], root
    )
    assert "tests/test_touch_heartbeat.py" in hits
    assert "tests/test_session_start_respawn.py" in hits


def test_vendor_hook_name_match_unions_with_the_floor(tmp_path: Path) -> None:
    """session-start.sh reaches its name-matched tests AND the floor."""
    mod = _import_module()
    root = _make_tree(
        tmp_path,
        [
            "test_session_start_respawn.py",
            "test_session_start_agent_notes.py",
            "test_touch_heartbeat.py",
            "test_unrelated.py",
        ],
    )
    hits = mod.map_to_tests([".agent/hooks/codex-cli/session-start.sh"], root)
    assert "tests/test_session_start_respawn.py" in hits
    assert "tests/test_session_start_agent_notes.py" in hits
    assert "tests/test_touch_heartbeat.py" in hits
    assert "tests/test_unrelated.py" not in hits


def test_vendor_floor_is_existence_checked(tmp_path: Path) -> None:
    """The floor never invents paths: a tree without the parity tests gets
    only what exists."""
    mod = _import_module()
    root = _make_tree(tmp_path, ["test_touch_heartbeat.py"])
    hits = mod.map_to_tests([".agent/hooks/gemini-cli/before-model-transcript.sh"], root)
    assert hits == ["tests/test_touch_heartbeat.py"]


def test_vendor_subdir_depth_is_skipped(tmp_path: Path) -> None:
    """Only direct <vendor>/<file> entries map; deeper paths fall through to
    smart-test's root-suite fallback."""
    mod = _import_module()
    root = _make_tree(tmp_path, ["test_touch_heartbeat.py"])
    assert mod.map_to_tests([".agent/hooks/claude-code/sub/x.sh"], root) == []


def test_shared_dir_is_not_treated_as_a_vendor() -> None:
    """_shared/ keeps its own (name-only, no-floor) rule."""
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/foo.sh") == "foo"


def test_githooks_stays_unmapped_by_decision() -> None:
    """.githooks/** is deliberately NOT name-mapped (recorded in the module
    docstring): its hooks have no name-shaped tests, and smart-test's
    root-suite fallback for unmapped top-level sources covers it. This test
    pins the decision so a silent mapping change is visible."""
    mod = _import_module()
    root = Path(__file__).parent.parent
    assert mod.map_to_tests([".githooks/reference-transaction"], root) == []
