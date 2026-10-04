# SPDX-License-Identifier: AGPL-3.0-or-later
"""The untyped-receiver caveat says which of its sites are known name collisions.

WI-jiful. On hypergumbo's own tree 63 of the 119 sites the caveat raised against
each ``host_fs`` claim were ``.replace()`` -- overwhelmingly ``str.replace``, the
literal INV-maluk collision -- and the sentence gave a reader no way to tell
them from the 19 ``mkdir`` / 12 ``write_text`` sites that are the actionable
part. The catalogue already holds the answer: ``ambiguous_names`` is the
short-name collision list the ROW CHOICE consults (``gate_named_entry``, and
taint through the same set, WI-razol). The disclosure did not.

WHAT CHANGES IS THE ORDER AND ONE CLAUSE, NOT THE SITES. A colliding name is
still disclosed -- ``pathlib.Path.replace`` is a real ``host_fs`` sink and an
untyped receiver could be one -- so ``entries`` is unchanged and nothing is
suppressed. The colliding sites are listed last, named in their own clause, and
carried in ``known_collisions`` so a merge can re-render the same sentence.
``mkdir`` and ``write_text`` are not in any ``ambiguous_names`` and are never
marked: the set is read, not grown.
"""

from __future__ import annotations

from hypergumbo_core.io_boundary import BoundaryMap, IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import (
    CAVEAT_UNTYPED_RECEIVER,
    Claim,
    _merge_caveat,
    _untyped_receiver_caveat,
    compute_boundary_coverage,
    untyped_receiver_known_collisions,
    verify_claim,
)


def _catalog() -> IoBoundaryCatalog:
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="fs_write", module="pathlib.Path",
                        name="replace", kind="method"),
            IoPrimitive(boundary="fs_write", module="pathlib.Path",
                        name="mkdir", kind="method"),
        ],
        stdlib_modules=frozenset({"pathlib"}),
        module_completeness={"pathlib": "2026-08-12"},
        ambiguous_names=frozenset({"replace"}),
    )


def _untyped(name: str, line: int) -> dict:
    return {
        "src": "python:svc.py:1-40:f:function",
        "dst": f"python:external:0-0:{name}:external_symbol",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method", "evidence_type": "ast_call",
                 "evidence_lang": "python"},
    }


def _edges() -> list[dict]:
    return [_untyped("replace", 3), _untyped("mkdir", 7)]


class TestTheCollisionSetIsTheRowChoiceSet:
    def test_a_site_whose_name_is_ambiguous_is_a_known_collision(self) -> None:
        got = untyped_receiver_known_collisions(_edges(), {"python": _catalog()})
        assert got == frozenset({"svc.py:3 replace()"})

    def test_a_language_with_no_ambiguous_names_marks_nothing(self) -> None:
        cat = _catalog()
        bare = IoBoundaryCatalog(
            language="python", primitives=list(cat.primitives),
            stdlib_modules=cat.stdlib_modules,
            module_completeness=cat.module_completeness,
        )
        assert untyped_receiver_known_collisions(
            _edges(), {"python": bare},
        ) == frozenset()


class TestTheSentence:
    def test_a_small_list_puts_the_collision_last_and_names_it(self) -> None:
        cav = _untyped_receiver_caveat(
            "fs_write", ["svc.py:3 replace()", "svc.py:7 mkdir()"],
            collisions=frozenset({"svc.py:3 replace()"}),
        )
        detail = cav["detail"]
        assert detail.index("svc.py:7 mkdir()") < detail.index("svc.py:3 replace()")
        assert "1 of them call a name the catalogue lists as a known" in detail
        assert cav["known_collisions"] == ["svc.py:3 replace()"]
        assert cav["entries"] == ["svc.py:3 replace()", "svc.py:7 mkdir()"]

    def test_a_large_list_sorts_colliding_names_after_the_others(self) -> None:
        """The hypergumbo shape: one colliding name dominates the site count."""
        sites = sorted(
            [f"a.py:{i} replace()" for i in range(6)]
            + ["b.py:1 write_text()", "b.py:2 mkdir()"],
        )
        cav = _untyped_receiver_caveat(
            "fs_write", sites,
            collisions=frozenset(s for s in sites if s.endswith("replace()")),
        )
        detail = cav["detail"]
        assert "3 distinct method(s): mkdir(), write_text(), replace()" in detail
        assert "6 of them call a name the catalogue lists" in detail
        assert "(replace())" in detail

    def test_no_collision_renders_the_sentence_unchanged(self) -> None:
        plain = _untyped_receiver_caveat("fs_write", ["svc.py:7 mkdir()"])
        marked = _untyped_receiver_caveat(
            "fs_write", ["svc.py:7 mkdir()"], collisions=frozenset(),
        )
        assert plain == marked
        assert "known_collisions" not in plain
        assert "collision" not in plain["detail"]


class TestItSurvivesAMerge:
    def test_the_merged_caveat_keeps_the_union_of_collisions(self) -> None:
        first = _untyped_receiver_caveat(
            "fs_write", ["svc.py:3 replace()"],
            collisions=frozenset({"svc.py:3 replace()"}),
        )
        second = _untyped_receiver_caveat("fs_write", ["svc.py:7 mkdir()"])
        merged = _merge_caveat([first], second)
        assert len(merged) == 1
        assert merged[0]["known_collisions"] == ["svc.py:3 replace()"]
        assert "1 of them call a name" in merged[0]["detail"]


def test_the_verdict_carries_the_marked_caveat() -> None:
    """Through ``compute_boundary_coverage`` and ``verify_claim``, the units the
    CLI calls, so the coverage field is wired and not merely defined."""
    coverage = compute_boundary_coverage(
        _edges(), {"python"}, {"python": _catalog()},
    )
    verdict = verify_claim(
        Claim(id="C", text="t", constraint_boundary="fs_write",
              constraint_must_not_exist=True),
        BoundaryMap(), coverage,
    )
    cav = next(c for c in verdict.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER)
    assert cav["known_collisions"] == ["svc.py:3 replace()"]
    assert cav["detail"].index("mkdir()") < cav["detail"].index("replace()")
