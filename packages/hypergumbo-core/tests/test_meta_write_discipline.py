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
    filter_meta_key,
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


# -- INV-hazov (a): the ALIAS shape ------------------------------------------
#
# `_mutation_key` matched only `<expr>.meta["k"] = ...`. A local name defeated
# it entirely, and one live site used exactly that:
#
#     meta = symbol.meta          # an ALIAS — the same dict object
#     meta["concepts"] = kept     # invisible to rule 1
#
# in `framework_patterns.strip_test_file_only_concepts` — the same module that
# produced this invariant's own seventh instance. Measured over the tree when
# the residual was audited: 1 alias, 13 `dict(x.meta)` COPIES. The copies are
# the control showing the predicate is not merely matching every local dict.


def test_checker_flags_an_alias_mediated_assignment(tmp_path):
    """The filed gap: a bare name hid a direct write to a merge_union key."""
    offenders = _drift(tmp_path, """
        def f(symbol):
            meta = symbol.meta
            meta["concepts"] = []
    """)
    assert any("'concepts'" in o for o in offenders), offenders


def test_a_dict_copy_is_not_an_alias(tmp_path):
    """THE CONTROL, and the reason this rule is usable rather than noise.

    ``m = dict(x.meta)`` builds a FRESH dict; mutating it destroys nothing
    until it is assigned back, and assigning it back is already rule 3's
    business. Thirteen sites in the tree do this and none is a violation.
    """
    offenders = _drift(tmp_path, """
        def f(symbol):
            meta = dict(symbol.meta)
            meta["concepts"] = []
    """)
    assert offenders == []


def test_an_alias_does_not_leak_across_scopes(tmp_path):
    """PER SCOPE, for the reason rule 3 already learned on ``go.py``.

    An alias bound in one function must not make an unrelated subscript in
    another function look like a meta write — the module-wide version of this
    scan reported 5 where the truth was 1.
    """
    offenders = _drift(tmp_path, """
        def binder(symbol):
            meta = symbol.meta
            return meta

        def unrelated():
            meta = {}
            meta["concepts"] = []
    """)
    assert offenders == []


# -- INV-hazov (b): the REMOVAL case -----------------------------------------
#
# The vocabulary had add / refine / replace and NO REMOVE, so the one
# legitimate remover in the tree could not be routed through the chokepoint:
# `concepts` is merge_union, and write_meta_key would have UNIONED the
# stripped entries straight back in.
#
# The answer is a second VERB, not a new discipline. A write_discipline says
# how several WRITERS COMBINE, and `concepts` genuinely is merge_union for its
# writers. A curator is not a competing writer — it runs after them and narrows
# their agreed result.


def test_filter_narrows_a_merge_union_key_and_reports_the_count():
    meta: dict[str, object] = {"concepts": [{"concept": "a"}, {"concept": "b"}]}
    removed = filter_meta_key(
        meta, "concepts", lambda c: c.get("concept") != "a",
    )
    assert removed == 1
    assert meta["concepts"] == [{"concept": "b"}]


def test_filter_leaves_the_slot_untouched_when_nothing_is_removed():
    original = [{"concept": "a"}]
    meta: dict[str, object] = {"concepts": original}
    assert filter_meta_key(meta, "concepts", lambda c: True) == 0
    assert meta["concepts"] is original, (
        "a no-op filter must not rebind the slot — rebinding is a write, and "
        "a write is what every instance of this class turned out to be"
    )


def test_filter_on_an_absent_or_non_list_slot_is_a_no_op():
    assert filter_meta_key({}, "concepts", lambda c: False) == 0
    assert filter_meta_key({"concepts": "not-a-list"}, "concepts",
                           lambda c: False) == 0


def test_filter_refuses_an_unregistered_key():
    with pytest.raises(ValueError, match="not registered"):
        filter_meta_key({"nope": []}, "nope", lambda c: True)


def test_filter_refuses_a_single_valued_key():
    """Narrowing is meaningful only where several writers contribute.

    A single_writer / producer_primary key holds ONE authoritative value, and
    silently dropping it is precisely the erasure this class is about — so the
    refusal is loud rather than a quiet no-op.
    """
    single = next(
        s for s in META_KEYS
        if s.write_discipline in (DISCIPLINE_SINGLE_WRITER,
                                  DISCIPLINE_PRODUCER_PRIMARY)
    )
    with pytest.raises(ValueError, match="may be narrowed"):
        filter_meta_key({single.name: ["x"]}, single.name, lambda c: False)


def test_the_live_remover_routes_through_the_chokepoint():
    """END TO END on the real function, not a fixture.

    ``strip_test_file_only_concepts`` is the site that motivated (b). Its
    behaviour must be unchanged — it still strips, and still returns the
    count — while no longer assigning through an alias.
    """
    offenders = find_discipline_drift(REPO_ROOT)
    assert not [o for o in offenders if "framework_patterns.py" in o], (
        "the live remover must satisfy rule 1 now that it can be seen:\n"
        + "\n".join(offenders)
    )
    # And it still does its job — behaviour unchanged, which is the half a
    # linter cannot check.
    from hypergumbo_core.framework_patterns import strip_test_file_only_concepts
    from hypergumbo_core.ir import Symbol

    sym = Symbol(
        id="python:t/test_x.py:1-2:MyService:class",
        name="MyService", kind="class", path="t/test_x.py",
        language="python", span=(1, 2),
    )
    sym.is_test_file = True
    sym.meta = {"concepts": [{"concept": "keep_me"}]}
    assert strip_test_file_only_concepts([sym]) == 0
    assert sym.meta["concepts"] == [{"concept": "keep_me"}]
