# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``_manifest_union`` in ``scripts/lib/forgejo-api.sh``.

Background
----------
``auto-pr`` regenerates ``.ci/affected-tests.txt`` during the push so a STALE
manifest — one missing a test the new diff affects — cannot reach CI. It did
that by running ``smart-test --manifest`` and, if the result differed from the
committed file, ``git add``-ing it into an amended commit.

That is an unconditional overwrite, and it destroys authored intent. The
slicer has no data-file -> test edge (INV-bigaz), so a change to
``io_primitives/*.yaml`` — or any other pure-data input — slices to roughly
ONE test file. An author who deliberately widens the manifest to the twenty
tests that actually exercise the changed data watches auto-pr silently narrow
it back during the push.

THE OVERWRITE IS INVISIBLE FROM THE AUTHOR'S SIDE, which is why it survived:
the manifest is extended, committed correctly, and replaced afterwards. It was
found by reading a CI log that said ``1 selected files`` for a commit whose
own manifest listed twenty. Re-checked across three merged catalogue PRs:
every one shipped a 1-file manifest while its notes recorded a working
19-file extension.

THIS IS THE SAME BUG AS WI-buhov, ONE DIRECTORY OVER. ``auto-pr`` used to
restore backed-up ``.ops`` files over the working tree unconditionally and
silently ate concurrent tracker writes; ``_ops_union_restore_file`` — the
function directly above this one in the same library — fixed it by making the
restore a UNION. The manifest path had the identical shape and no such guard.

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
#: list. Tests here use the real shape, because an earlier cut of this helper
#: matched entries by the ``packages/`` prefix and would have emitted an EMPTY
#: manifest for the very manifest that motivated it — 95 root-level
#: ``tests/...`` entries, none of which start with ``packages/``.
_HEADER = (
    "# Test selection manifest\n"
    "# Mode: targeted\n"
    "#\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
)


def _run_union(cwd: Path, committed: Path, regen: Path, out: Path):
    return subprocess.run(
        ["bash", "-c",
         f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
         f"_manifest_union '{committed}' '{regen}' '{out}'"],
        cwd=cwd, env={"PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=10,
    )


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
    r = subprocess.run(
        ["bash", "-c",
         f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
         f"_manifest_test_count '{manifest}'"],
        cwd=cwd, env={"PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_count_sees_root_level_tests_paths(tmp_path: Path) -> None:
    """The REPORTING half of the same prefix bug. auto-pr's first cut counted
    with ``grep -c '^packages/'``, which is 0 for a manifest of root-level
    ``tests/...`` entries — the common shape — so the "kept N entries" line
    that makes a silent narrowing VISIBLE never printed. A diagnostic that
    cannot fire is worse than none: it reads as "nothing was preserved"."""
    m = tmp_path / "m.txt"
    m.write_text(_HEADER + "tests/test_a.py\ntests/test_b.py\n"
                 "packages/p/tests/test_c.py\n")
    assert _run_count(tmp_path, m) == "3"


def test_the_count_is_zero_for_an_empty_selection(tmp_path: Path) -> None:
    m = tmp_path / "m.txt"
    m.write_text(_HEADER)
    assert _run_count(tmp_path, m) == "0"


def test_root_level_tests_paths_survive(tmp_path: Path) -> None:
    """THE BUG THIS HELPER ITSELF SHIPPED WITH, caught before merge. An
    earlier cut selected entries with ``grep '^packages/'``. The manifest
    that motivated the whole fix holds 95 entries, every one of them a
    root-level ``tests/...`` path — so that cut would have written an EMPTY
    test list and CI would have run nothing while reporting success. Entries
    are selected by SECTION, never by path prefix."""
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
    """THE REGRESSION. Committed lists 3; the slicer sees 1; all 3 survive."""
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
    """FAIL-SAFE. The existence check is cwd-relative. If the caller's
    long-standing "auto-pr runs from the repo root" assumption ever broke,
    every path would fail -f and the manifest would empty — CI would then run
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
    """The pre-commit hook rejects a full-suite manifest, so the header from
    the freshly generated (targeted) file must survive the merge."""
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
