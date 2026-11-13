# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``_manifest_union`` in ``scripts/lib/forgejo-api.sh``.

Background
----------
``auto-pr`` regenerates ``.ci/affected-tests.txt`` during the push so a STALE
manifest — one missing a test the new diff affects — cannot reach CI.
Overwriting the committed manifest with the regenerated one would destroy
authored intent: the slicer has no data-file -> test edge (INV-bigaz), so a
change to ``io_primitives/*.yaml`` — or any other pure-data input — slices to
roughly ONE test file, and a manifest the author deliberately widened to the
tests that exercise the changed data would be silently narrowed back during
the push.

THE OVERWRITE WOULD BE INVISIBLE FROM THE AUTHOR'S SIDE: the manifest is
extended and committed correctly, then replaced afterwards; only CI's
``N selected files`` log line shows the narrowing.

``_ops_union_restore_file`` — the function directly below this one in the
same library — protects tracker ``.ops`` restores from the same overwrite by
making the restore a UNION, and ``_manifest_union`` applies that remedy to the
manifest (history: WI-buhov).

Invariant under test
--------------------
The merged manifest contains every test path present in EITHER the committed
manifest OR the freshly sliced one (so the regen may still ADD), and never
fewer than the committed one lists (so it can never DROP) — except for paths
that no longer exist on disk, which are dropped so a deleted test cannot be
resurrected into CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"

#: The manifest is SECTIONED. CI reads everything after
#: ``# === SELECTED_TESTS ===`` as the test list, and the region between
#: ``# === CHANGED_SOURCE_FILES ===`` and that marker as the changed-source
#: list. Tests here use the real shape, because matching entries by a
#: ``packages/`` prefix instead of by section selects NOTHING from a manifest
#: of root-level ``tests/...`` entries, which is the common shape.
_HEADER = (
    "# Test selection manifest\n"
    "# Mode: targeted\n"
    "#\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
)


def _run_lib(cwd: Path, snippet: str) -> subprocess.CompletedProcess:
    """Run SNIPPET against the real library under the REAL CALLER'S SHELL OPTIONS.

    ``scripts/auto-pr`` runs ``set -Eeuo pipefail`` before sourcing this
    library, so every function here executes with ``errexit`` and
    ``pipefail`` live. A harness that sources the library into a plain
    ``bash -c`` hides any non-zero pipeline status: a trailing
    ``| grep -v '^#' | grep -v '^$'`` exits 1 on a manifest that selects no
    tests, which passes with ``errexit`` off and kills auto-pr mid-push —
    no PR, no gate, no log — with it on (history: INV-zamoh).

    A test that does not reproduce the caller's shell options is not testing
    the caller. Options are set BEFORE the source, as auto-pr does it.
    """
    return subprocess.run(
        ["bash", "-c",
         f"set -euo pipefail; source '{FORGEJO_LIB}' >/dev/null 2>&1; "
         + snippet],
        cwd=cwd, env={"PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=10,
    )


def _run_union(cwd: Path, committed: Path, regen: Path, out: Path):
    return _run_lib(cwd, f"_manifest_union '{committed}' '{regen}' '{out}'")


def _run_selected(cwd: Path, manifest: Path) -> subprocess.CompletedProcess:
    return _run_lib(cwd, f"_manifest_selected_tests '{manifest}'")


def _mk(cwd: Path, *rel: str) -> None:
    """Create the test files on disk so the existence filter keeps them."""
    for r in rel:
        p = cwd / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")


def _body(path: Path) -> list[str]:
    """Read the manifest the way CI does: the SELECTED_TESTS section."""
    lines = path.read_text().splitlines()
    if "# === SELECTED_TESTS ===" in lines:
        lines = lines[lines.index("# === SELECTED_TESTS ===") + 1:]
    return [l for l in lines if l and not l.startswith("#")]


def _run_count(cwd: Path, manifest: Path) -> str:
    r = _run_lib(cwd, f"_manifest_test_count '{manifest}'")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _declared_count(path: Path) -> str:
    """The count the manifest DECLARES in its header, as CI's operator reads it."""
    for line in path.read_text().splitlines():
        if line.startswith("# Selected tests:"):
            return line.split(":", 1)[1].strip()
    return "<absent>"


def test_the_count_sees_root_level_tests_paths(tmp_path: Path) -> None:
    """The count must see root-level ``tests/...`` entries. A path-prefix
    count (``grep -c '^packages/'``) is 0 for them — the common shape — so
    the "kept N entries" line that makes a silent narrowing VISIBLE would
    never print. A diagnostic that cannot fire is worse than none: it reads
    as "nothing was preserved"."""
    m = tmp_path / "m.txt"
    m.write_text(_HEADER + "tests/test_a.py\ntests/test_b.py\n"
                 "packages/p/tests/test_c.py\n")
    assert _run_count(tmp_path, m) == "3"


def test_the_count_is_zero_for_an_empty_selection(tmp_path: Path) -> None:
    m = tmp_path / "m.txt"
    m.write_text(_HEADER)
    assert _run_count(tmp_path, m) == "0"


def test_root_level_tests_paths_survive(tmp_path: Path) -> None:
    """Entries are selected by SECTION, never by path prefix. Selecting with
    ``grep '^packages/'`` writes an EMPTY test list for a manifest of
    root-level ``tests/...`` paths, and CI then runs nothing while reporting
    success."""
    _mk(tmp_path, "tests/test_root.py", "packages/p/tests/test_pkg.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_root.py\n"
                         "packages/p/tests/test_pkg.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_root.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _body(out) == ["packages/p/tests/test_pkg.py", "tests/test_root.py"]


def test_the_changed_source_files_section_is_preserved(tmp_path: Path) -> None:
    """CI parses CHANGED_SOURCE_FILES separately; rebuilding the file from
    scratch would silently drop it."""
    _mk(tmp_path, "tests/test_a.py")
    header = ("# Test selection manifest\n# Mode: targeted\n"
              "# === CHANGED_SOURCE_FILES ===\n"
              "packages/p/src/p/mod.py\n"
              "# === SELECTED_TESTS ===\n")
    committed = tmp_path / "committed.txt"
    committed.write_text(header + "tests/test_a.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(header + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    text = out.read_text()
    assert "# === CHANGED_SOURCE_FILES ===" in text
    assert "packages/p/src/p/mod.py" in text
    assert _body(out) == ["tests/test_a.py"]


def test_the_slicer_may_not_narrow_a_hand_extended_manifest(tmp_path: Path) -> None:
    """The core property: committed lists 3; the slicer sees 1; all 3 survive."""
    _mk(tmp_path, "tests/test_a.py", "packages/b/tests/test_b.py",
        "tests/test_c.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_a.py\n"
                         "packages/b/tests/test_b.py\n"
                         "tests/test_c.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    result = _run_union(tmp_path, committed, regen, out)
    assert result.returncode == 0, result.stderr
    # sort -u orders the union; "packages/..." sorts before "tests/...".
    assert _body(out) == ["packages/b/tests/test_b.py",
                          "tests/test_a.py",
                          "tests/test_c.py"]


def test_the_slicer_may_still_add_a_newly_affected_test(tmp_path: Path) -> None:
    """The regen's REAL job — catching a stale manifest — still works."""
    _mk(tmp_path, "tests/test_a.py", "tests/test_new.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_a.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n"
                     "tests/test_new.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _body(out) == ["tests/test_a.py",
                          "tests/test_new.py"]


def test_a_deleted_test_is_not_resurrected(tmp_path: Path) -> None:
    """Union must not drag a path back that no longer exists on disk."""
    _mk(tmp_path, "tests/test_a.py")   # test_gone.py NOT created
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_a.py\n"
                         "tests/test_gone.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _body(out) == ["tests/test_a.py"]


def test_a_filter_that_removed_everything_is_not_believed(tmp_path: Path) -> None:
    """FAIL-SAFE. The existence check is cwd-relative, so it assumes auto-pr
    runs from the repo root. If that assumption ever broke, every path would
    fail -f and the manifest would empty — CI would then run
    nothing and report success, which is the silent-loss failure this whole
    function exists to prevent, reintroduced one level down. A filter that
    removed absolutely everything is not a believable answer."""
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_a.py\n"
                         "packages/b/tests/test_b.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"
    # NOTE: no _mk() call — none of these paths exist on disk.

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _body(out) == ["packages/b/tests/test_b.py",
                          "tests/test_a.py"], (
        "an empty result must fall back to the unfiltered union"
    )


def test_the_targeted_mode_header_is_preserved(tmp_path: Path) -> None:
    """The pre-commit hook rejects any manifest whose mode is not
    ``targeted`` (full-suite included), so the header from the freshly
    generated (targeted) file must survive the merge."""
    _mk(tmp_path, "tests/test_a.py")
    committed = tmp_path / "committed.txt"
    committed.write_text("# Mode: full-suite\ntests/test_a.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    text = out.read_text()
    assert "# Mode: targeted" in text
    assert "# Mode: full-suite" not in text


def test_identical_inputs_are_idempotent(tmp_path: Path) -> None:
    _mk(tmp_path, "tests/test_a.py")
    content = _HEADER + "tests/test_a.py\n"
    committed = tmp_path / "committed.txt"
    committed.write_text(content)
    regen = tmp_path / "regen.txt"
    regen.write_text(content)
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _body(out) == ["tests/test_a.py"]


def test_missing_committed_file_falls_back_to_the_regen(tmp_path: Path) -> None:
    """A brand-new manifest has no committed predecessor; that is not an error."""
    _mk(tmp_path, "tests/test_a.py")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    result = _run_union(tmp_path, tmp_path / "nope.txt", regen, out)
    assert result.returncode == 0, result.stderr
    assert _body(out) == ["tests/test_a.py"]


# ----------------------------------------------------------------------
# A manifest that selects NO tests (history: INV-zamoh)
#
# `_manifest_union` calls `_manifest_selected_tests` twice inside a pipeline
# group. A selection pipeline ending in `| grep -v '^#' | grep -v '^$'` exits
# 1 on a manifest with zero selected tests (both greps match nothing), and
# under auto-pr's `set -euo pipefail` that status kills the shell before the
# function's own `return 0` can mask it.
#
# WHICH POPULATION: any docs-only or config-only change, where the slicer
# correctly reports "no test-relevant files changed". That is precisely the
# population the union protects, so an unguarded empty selection turns the
# guard against silent narrowing into a killed run.
# ----------------------------------------------------------------------

#: A real zero-selection manifest, as `smart-test --manifest` writes one for a
#: docs-only diff: a full header, both section markers, and NOTHING after the
#: SELECTED_TESTS marker.
_ZERO_MANIFEST = (
    "# Test selection manifest\n"
    "# Mode: targeted\n"
    "# Changed source files: 0\n"
    "# Selected tests: 0\n"
    "#\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
)


def _header_declaring(n: int) -> str:
    return (
        "# Test selection manifest\n"
        "# Mode: targeted\n"
        f"# Selected tests: {n}\n"
        "#\n"
        "# === CHANGED_SOURCE_FILES ===\n"
        "# === SELECTED_TESTS ===\n"
    )


def test_an_empty_selection_is_a_success_not_an_error(tmp_path: Path) -> None:
    """(a) Zero selected tests is a legitimate answer for a docs-only diff,
    so the function must SUCCEED with empty output: every caller runs under
    `set -euo pipefail`, where an exit 1 kills the shell."""
    m = tmp_path / "zero.txt"
    m.write_text(_ZERO_MANIFEST)

    result = _run_selected(tmp_path, m)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_an_empty_selection_is_a_success_in_the_markerless_branch(
    tmp_path: Path,
) -> None:
    """The same pipeline appears in the `else` branch, reached by a manifest
    with no section markers (the pre-section format). Both arms are pinned,
    because a fix applied to one arm of an if/else and not the other leaves
    half a defect behind."""
    m = tmp_path / "old.txt"
    m.write_text("# Test selection manifest\n# Mode: targeted\n")

    result = _run_selected(tmp_path, m)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_docs_only_regen_does_not_kill_the_union(tmp_path: Path) -> None:
    """(b) End to end: three authored tests in the committed manifest, a
    zero-selection regen from a docs-only diff. A non-zero exit here kills
    auto-pr mid-push — no PR, no `.git/PR_PENDING` gate, no branch on the
    remote. The union must complete and preserve every committed path."""
    _mk(tmp_path, "tests/test_adr_readme_index_sync.py",
        "tests/test_adr_supersession_symmetry.py",
        "tests/test_top_level_test_map.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_adr_readme_index_sync.py\n"
                         "tests/test_adr_supersession_symmetry.py\n"
                         "tests/test_top_level_test_map.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_ZERO_MANIFEST)
    out = tmp_path / "out.txt"

    result = _run_union(tmp_path, committed, regen, out)
    assert result.returncode == 0, result.stderr
    assert _body(out) == ["tests/test_adr_readme_index_sync.py",
                          "tests/test_adr_supersession_symmetry.py",
                          "tests/test_top_level_test_map.py"]


def test_both_sides_empty_is_still_a_success(tmp_path: Path) -> None:
    """Nothing committed, nothing sliced. An empty manifest is the correct
    answer for a docs-only change to a branch that never had one."""
    committed = tmp_path / "committed.txt"
    committed.write_text(_ZERO_MANIFEST)
    regen = tmp_path / "regen.txt"
    regen.write_text(_ZERO_MANIFEST)
    out = tmp_path / "out.txt"

    result = _run_union(tmp_path, committed, regen, out)
    assert result.returncode == 0, result.stderr
    assert _body(out) == []
    assert "# === SELECTED_TESTS ===" in out.read_text()


# ----------------------------------------------------------------------
# The header must not contradict the body (history: INV-zamoh)
#
# The union copies the header VERBATIM from the regen so the "# Mode:" line
# and the CHANGED_SOURCE_FILES section survive. That also copies the regen's
# own "# Selected tests:" count, which describes a body the union replaced;
# left alone, a docs-only union would say 0 over a body of three.
#
# No consumer reads this count: ci.yml recomputes it from the SELECTED_TESTS
# section it is about to run, and the pre-commit hook gates only on the
# "# Mode:" line. So an uncorrected count is a manifest that LIES rather than
# one that breaks, and the tests below pin it as a lie, not a failure. It is
# corrected anyway: "CI runs nothing while reporting success" is the failure
# `_manifest_union`'s own FAIL-SAFE comment says it exists to prevent, and a
# header that reads 0 is that failure pre-staged for whichever consumer
# trusts it first.
# ----------------------------------------------------------------------


def test_the_declared_count_is_restated_from_the_union(tmp_path: Path) -> None:
    """(c) The docs-only shape again, read through the header. The regen
    declares 0; the union writes three paths; the header must say 3."""
    _mk(tmp_path, "tests/test_a.py", "tests/test_b.py", "tests/test_c.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_header_declaring(3) + "tests/test_a.py\n"
                         "tests/test_b.py\ntests/test_c.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_ZERO_MANIFEST)
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _declared_count(out) == "3"
    assert _declared_count(out) == str(len(_body(out)))


def test_the_declared_count_matches_a_widened_union(tmp_path: Path) -> None:
    """The union ADDS as well as preserves: 1 committed + 1 newly sliced = 2,
    and neither input's declared count is 2."""
    _mk(tmp_path, "tests/test_a.py", "tests/test_new.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_header_declaring(1) + "tests/test_a.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_header_declaring(1) + "tests/test_new.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _declared_count(out) == "2"
    assert _body(out) == ["tests/test_a.py", "tests/test_new.py"]


def test_the_declared_count_follows_a_dropped_deleted_test(tmp_path: Path) -> None:
    """The existence filter runs AFTER the union, so a count taken before it
    would over-report. Committed declares 2; one of its tests is gone."""
    _mk(tmp_path, "tests/test_a.py")   # test_gone.py NOT created
    committed = tmp_path / "committed.txt"
    committed.write_text(_header_declaring(2) + "tests/test_a.py\n"
                         "tests/test_gone.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_header_declaring(1) + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _declared_count(out) == "1"
    assert _body(out) == ["tests/test_a.py"]


def test_a_header_without_a_count_line_does_not_gain_one(tmp_path: Path) -> None:
    """The header is the REGEN'S, preserved verbatim; restating a count is a
    correction, not a licence to author header lines the generator did not
    write. `_HEADER` has no count line and the output must not either."""
    _mk(tmp_path, "tests/test_a.py")
    committed = tmp_path / "committed.txt"
    committed.write_text(_HEADER + "tests/test_a.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_HEADER + "tests/test_a.py\n")
    out = tmp_path / "out.txt"

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _declared_count(out) == "<absent>"
    assert _body(out) == ["tests/test_a.py"]


def test_the_declared_count_survives_the_fail_safe_fallback(tmp_path: Path) -> None:
    """When the existence filter removes EVERYTHING the union falls back to
    the unfiltered body — and the count must describe what was actually
    written, not what the filter would have left."""
    committed = tmp_path / "committed.txt"
    committed.write_text(_header_declaring(0) + "tests/test_a.py\n"
                         "packages/b/tests/test_b.py\n")
    regen = tmp_path / "regen.txt"
    regen.write_text(_ZERO_MANIFEST)
    out = tmp_path / "out.txt"
    # NOTE: no _mk() call — none of these paths exist on disk.

    assert _run_union(tmp_path, committed, regen, out).returncode == 0
    assert _declared_count(out) == "2"
    assert _declared_count(out) == str(len(_body(out)))
