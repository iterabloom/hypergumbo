# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-hazov: a multi-writer meta slot must not silently erase earlier writers.

Six instances of last-writer-wins on a shared meta slot shipped between
2026-04 and 2026-08, each fixed at its own call site with a bespoke remedy
(INV-forim, the BUG-04/05 family, INV-zumin, INV-pojib, INV-virat, and the
``verify_claims`` caveat list). A seventh was found live while filing the
class item — ``framework_patterns`` overwriting producer-set ``concepts``.

The registry that should have caught them declares *which* keys may exist
and *what* vocabulary their values draw from. It was blind to *how many
writers* reach a key, so a correctly-spelled assignment of a registered key
— which is exactly what INV-virat was — passed every existing check.

These tests pin the three parts of the mechanism: the declaration is
mandatory for any key a second writer can reach, the chokepoint enforces the
declared discipline at runtime, and the static check refuses a direct
assignment that bypasses the chokepoint.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.axis_meta_keys import (
    DISCIPLINE_MERGE_UNION,
    DISCIPLINE_PRODUCER_PRIMARY,
    DISCIPLINE_SINGLE_WRITER,
    DISCIPLINE_UNAUDITED,
    META_KEYS,
    WRITE_DISCIPLINES,
    all_meta_key_names,
    find_meta_key,
    write_meta_key,
)
from hypergumbo_core.meta_write_discipline import (
    collision_capable_keys,
    find_discipline_drift,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-meta-write-discipline"


# -- the declaration is mandatory ------------------------------------


def test_every_spec_declares_a_known_write_discipline():
    for spec in META_KEYS:
        assert spec.write_discipline in WRITE_DISCIPLINES, (
            f"{spec.name} declares {spec.write_discipline!r}"
        )


def test_unaudited_is_the_default_so_no_key_falsely_asserts_safety():
    """An omitted declaration must not read as 'only one writer'.

    Declaring ``single_writer`` wrongly buys a FALSE assurance, which is
    worse than no assurance at all — the INV-faput lesson (a mislabelled
    catalogue row buys a ``confirmed``). The default therefore declines to
    claim anything.
    """
    from hypergumbo_core.axis_meta_keys import MetaKeySpec

    spec = MetaKeySpec("probe_key", "edge_meta", "probe")
    assert spec.write_discipline == DISCIPLINE_UNAUDITED


def test_the_four_instance_keys_are_registered():
    """io_boundaries / io_mode / concepts were written but never declared."""
    names = all_meta_key_names()
    for key in ("io_boundary", "io_primitive", "io_boundaries", "io_mode",
                "concepts"):
        assert key in names, f"{key} is written in src but not registered"


def test_a_non_single_writer_declaration_carries_a_note():
    """A merge/producer-primary claim must say who the writers are."""
    for spec in META_KEYS:
        if spec.write_discipline in (
            DISCIPLINE_MERGE_UNION, DISCIPLINE_PRODUCER_PRIMARY,
        ):
            assert spec.discipline_note.strip(), (
                f"{spec.name} declares {spec.write_discipline} with no note"
            )


# -- the chokepoint enforces the declaration -------------------------


def test_merge_union_keeps_both_writers_values():
    meta = {}
    write_meta_key(meta, "io_boundaries", ["command_launch"])
    write_meta_key(meta, "io_boundaries", ["fs_write"])
    assert meta["io_boundaries"] == ["command_launch", "fs_write"]


def test_merge_union_does_not_duplicate_on_an_identical_rewrite():
    """The INV-virat inverse: assign->append stops erasure and invites
    duplication. A doubled entry is not cosmetic — ``caveats`` and
    ``io_boundaries`` are surfaces a consumer COUNTS."""
    meta = {}
    write_meta_key(meta, "io_boundaries", ["fs_write"])
    write_meta_key(meta, "io_boundaries", ["fs_write"])
    assert meta["io_boundaries"] == ["fs_write"]


def test_merge_union_accepts_a_scalar_and_still_unions():
    meta = {}
    write_meta_key(meta, "io_boundaries", "command_launch")
    write_meta_key(meta, "io_boundaries", "fs_write")
    assert meta["io_boundaries"] == ["command_launch", "fs_write"]


def test_producer_primary_refuses_to_displace_an_existing_stamp():
    """INV-virat verbatim: a catalogue row must not erase the analyzer's
    ``command_launch`` opacity stamp."""
    meta = {"io_boundary": "command_launch"}
    write_meta_key(meta, "io_boundary", "fs_write")
    assert meta["io_boundary"] == "command_launch"


def test_producer_primary_writes_when_the_slot_is_empty():
    meta = {}
    write_meta_key(meta, "io_boundary", "fs_write")
    assert meta["io_boundary"] == "fs_write"


def test_single_writer_raises_when_a_second_writer_conflicts():
    """The declaration says this cannot happen; a violation must be loud
    rather than silently resolved in either direction."""
    meta = {"io_mode": "r"}
    with pytest.raises(ValueError, match="single_writer"):
        write_meta_key(meta, "io_mode", "w")


def test_single_writer_tolerates_an_identical_rewrite():
    meta = {"io_mode": "r"}
    write_meta_key(meta, "io_mode", "r")
    assert meta["io_mode"] == "r"


def test_unaudited_assigns_without_claiming_anything():
    meta = {"detection_pattern": "first"}
    write_meta_key(meta, "detection_pattern", "second")
    assert meta["detection_pattern"] == "second"


def test_an_unregistered_key_is_refused_by_the_chokepoint():
    meta = {}
    with pytest.raises(ValueError, match="not registered"):
        write_meta_key(meta, "definitely_not_a_registered_key", 1)


# -- the static check refuses a bypass -------------------------------


def _drift(tmp_path, source: str):
    pkg = tmp_path / "packages" / "fake" / "src" / "fake"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text(textwrap.dedent(source))
    return find_discipline_drift(tmp_path)


def test_checker_flags_a_direct_assignment_to_a_merge_union_key(tmp_path):
    offenders = _drift(tmp_path, """
        def tag(edge):
            edge.meta["io_boundaries"] = ["fs_write"]
    """)
    assert any("io_boundaries" in o for o in offenders), offenders


def test_checker_flags_a_direct_assignment_to_a_producer_primary_key(tmp_path):
    offenders = _drift(tmp_path, """
        def tag(edge):
            edge.meta["io_boundary"] = "fs_write"
    """)
    assert any("io_boundary" in o for o in offenders), offenders


def test_checker_accepts_the_chokepoint(tmp_path):
    offenders = _drift(tmp_path, """
        from hypergumbo_core.axis_meta_keys import write_meta_key

        def tag(edge):
            write_meta_key(edge.meta, "io_boundary", "fs_write")
    """)
    assert offenders == [], offenders


def test_checker_ignores_a_single_writer_key(tmp_path):
    offenders = _drift(tmp_path, """
        def tag(edge):
            edge.meta["io_mode"] = "w"
    """)
    assert offenders == [], offenders


def test_checker_flags_a_collision_capable_key_left_unaudited(tmp_path):
    """Fail CLOSED: a key reachable by both a constructor and a post-hoc
    mutation, with no declaration, is the exact shape of all seven
    instances. It must not pass merely because nobody declared it."""
    offenders = _drift(tmp_path, """
        from hypergumbo_core.ir import Edge

        def make():
            return Edge.create(meta={"detection_pattern": "x"})

        def later(edge):
            edge.meta["detection_pattern"] = "y"
    """)
    assert any("detection_pattern" in o for o in offenders), offenders


def test_checker_flags_a_whole_dict_meta_assignment_that_destroys_kwargs(
    tmp_path,
):
    """INV-forim's shape. It is closed on the live tree only because none of
    the nine surviving sites happen to pass a meta-bearing kwarg today —
    safety by coincidence, which this rule converts into safety by
    construction."""
    offenders = _drift(tmp_path, """
        from hypergumbo_core.ir import Edge

        def emit():
            edge = Edge.create(src="a", dst="b", access_mode="write")
            edge.meta = {"channel": "c"}
            return edge
    """)
    assert any("access_mode" in o or "meta" in o for o in offenders), offenders


def test_checker_allows_whole_dict_assignment_with_no_meta_bearing_kwarg(
    tmp_path,
):
    """The nine live sites: nothing is in meta yet, so nothing is destroyed."""
    offenders = _drift(tmp_path, """
        from hypergumbo_core.ir import Edge

        def emit():
            edge = Edge.create(src="a", dst="b")
            edge.meta = {"channel": "c"}
            return edge
    """)
    assert offenders == [], offenders


# -- the live tree ---------------------------------------------------


def test_live_tree_has_no_discipline_drift():
    offenders = find_discipline_drift(REPO_ROOT)
    assert offenders == [], "\n".join(offenders)


def test_live_tree_collision_capable_set_is_declared():
    """Every key a second writer can actually reach carries a declaration.

    This is the measurement that found the class: only four keys tree-wide
    are written BOTH at construction and by post-hoc mutation, and two of
    them are the io keys that produced INV-virat and INV-zumin — the
    discriminator independently rediscovers both known defects, which is
    what makes it a control rather than a guess.
    """
    capable = collision_capable_keys(REPO_ROOT)
    assert capable, "instrument found nothing — suspect the instrument"
    for key in capable:
        spec = find_meta_key(key)
        assert spec is not None, f"{key} is collision-capable but unregistered"
        assert spec.write_discipline != DISCIPLINE_UNAUDITED, (
            f"{key} is reachable by two writers and declares nothing"
        )


def test_script_runs_clean_on_the_live_tree():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_union_dedups_a_list_valued_entry():
    """A contribution can itself be a list — ``io_boundaries`` receives
    ``sorted(extra)`` as one value in some call shapes. The canonical form
    has to reach inside it, or an identical re-stamp appends a duplicate."""
    meta = {}
    write_meta_key(meta, "io_boundaries", [["fs_write", "subprocess"]])
    write_meta_key(meta, "io_boundaries", [["fs_write", "subprocess"]])
    assert meta["io_boundaries"] == [["fs_write", "subprocess"]]


def test_checker_flags_a_collision_capable_key_that_is_unregistered(tmp_path):
    """Rule 2's other arm: not merely undeclared, but absent from the
    registry entirely — the state ``concepts`` and ``fields`` were in."""
    offenders = _drift(tmp_path, """
        from hypergumbo_core.ir import Edge

        def make():
            return Edge.create(meta={"totally_unregistered_key": "x"})

        def later(edge):
            edge.meta["totally_unregistered_key"] = "y"
    """)
    assert any("not registered" in o for o in offenders), offenders


def test_unaudited_count_is_reported_as_visible_debt():
    """Landing the mechanism does not audit all the keys, and the number
    says so on every clean run rather than being implied by silence."""
    from hypergumbo_core.meta_write_discipline import unaudited_key_count

    count = unaudited_key_count(REPO_ROOT)
    declared = sum(
        1 for spec in META_KEYS
        if spec.write_discipline != DISCIPLINE_UNAUDITED
    )
    assert count == len(META_KEYS) - declared
    assert count > 0, "if this ever hits zero, say so in the changelog"
