# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the callable-signature axis (ADR-0058).

Three groups, and they check different things on purpose:

* REGISTRY INVARIANTS — the notions are well-formed and the conformance
  verdict is derived from the section rather than stored twice.
* CITATION LIVENESS — every ``file:line`` this module cites still says what
  it claimed. A rotted citation is worse than none: it sends the next reader
  to a line that now means something else (ADR-0051 section 5).
* THE CLOSED PARSER SET — the A+no half of the owner ruling. The nine
  consumers that parse the value are GRANDFATHERED and enumerated; a tenth
  is a decision, and the gate makes it one by failing until the table is
  edited.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.signature_axis import (
    AXIS_DECLARATION_SURFACE,
    AXIS_FOREIGN_FACT,
    AXIS_PENDING,
    FACT_HOMES,
    LEGACY_VALUE_PARSERS,
    SIGNATURE_AXIOM,
    SIGNATURE_NOTIONS,
    VALID_AXES,
    all_signature_notions,
    find_signature_notion,
    find_undeclared_value_parsers,
    home_for_fact,
    is_axiom_conformant,
    notions_on_axis,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestRegistryInvariants:
    def test_axiom_is_one_sentence_and_names_the_two_halves(self) -> None:
        assert "DISPLAY" in SIGNATURE_AXIOM
        assert "declared home" in SIGNATURE_AXIOM

    def test_notion_names_are_unique(self) -> None:
        names = [n.name for n in SIGNATURE_NOTIONS]
        assert len(names) == len(set(names))

    def test_every_notion_sits_on_a_valid_axis(self) -> None:
        for notion in SIGNATURE_NOTIONS:
            assert notion.axis in VALID_AXES, notion.name

    def test_every_notion_has_a_description(self) -> None:
        for notion in SIGNATURE_NOTIONS:
            assert notion.description.strip(), notion.name

    def test_all_signature_notions_matches_the_tuple(self) -> None:
        assert all_signature_notions() == frozenset(
            n.name for n in SIGNATURE_NOTIONS
        )

    def test_notions_on_axis_partitions_the_registry(self) -> None:
        seen = [n for axis in VALID_AXES for n in notions_on_axis(axis)]
        assert sorted(n.name for n in seen) == sorted(
            n.name for n in SIGNATURE_NOTIONS
        )

    def test_find_signature_notion_round_trips(self) -> None:
        for notion in SIGNATURE_NOTIONS:
            assert find_signature_notion(notion.name) is notion

    def test_find_signature_notion_is_none_for_an_undeclared_name(self) -> None:
        assert find_signature_notion("no_such_notion") is None


class TestConformanceIsDerived:
    def test_the_declaration_surface_is_conformant(self) -> None:
        assert is_axiom_conformant("callable_surface")

    def test_a_foreign_fact_is_not_conformant(self) -> None:
        assert not is_axiom_conformant("value_type")

    def test_an_undeclared_name_is_not_conformant(self) -> None:
        # The false-all-clear direction. A notion nobody argued cannot have
        # been argued to satisfy the axiom.
        assert not is_axiom_conformant("no_such_notion")

    def test_pending_is_not_conformant(self) -> None:
        # "Not yet argued" is not "argued and accepted".
        for notion in notions_on_axis(AXIS_PENDING):
            assert not is_axiom_conformant(notion.name)

    def test_conformance_agrees_with_the_section_for_every_notion(self) -> None:
        for notion in SIGNATURE_NOTIONS:
            assert is_axiom_conformant(notion.name) == (
                notion.axis == AXIS_DECLARATION_SURFACE
            )


class TestFactHomes:
    def test_every_fact_names_a_home_that_exists(self) -> None:
        for fact, home in FACT_HOMES.items():
            target = REPO_ROOT / home.path
            assert target.exists(), f"{fact}: {home.path} is gone"

    def test_every_home_citation_still_says_what_it_claimed(self) -> None:
        for fact, home in FACT_HOMES.items():
            lines = (REPO_ROOT / home.path).read_text(
                encoding="utf-8"
            ).splitlines()
            assert home.anchor in lines[home.line - 1], (
                f"{fact}: {home.path}:{home.line} no longer contains "
                f"{home.anchor!r} — it says {lines[home.line - 1].strip()!r}"
            )

    def test_home_for_fact_round_trips(self) -> None:
        for fact in FACT_HOMES:
            assert home_for_fact(fact) is FACT_HOMES[fact]

    def test_home_for_an_unknown_fact_is_none(self) -> None:
        assert home_for_fact("no_such_fact") is None

    def test_the_three_facts_consumers_actually_parse_have_homes(self) -> None:
        # Return type, arity and a field's declared type are exactly the
        # three the nine legacy parsers go after.
        assert {"return_type", "parameter_arity", "value_type"} <= set(
            FACT_HOMES
        )


class TestLegacyParsersAreClosedAndLive:
    def test_the_set_is_the_current_migration_backlog(self) -> None:
        """This number may only go DOWN.

        It was NINE when ADR-0058 declared the axis. ``py.py`` left it under
        WI-ribak. A RISE means a new parser slipped in -- which the live-tree
        gate below should have caught first, so a rise here means the gate
        was edited to admit it, and that is the review.
        """
        assert len(LEGACY_VALUE_PARSERS) == 8

    def test_every_parser_cites_a_file_that_exists(self) -> None:
        for site in LEGACY_VALUE_PARSERS:
            assert (REPO_ROOT / site.path).exists(), site.path

    def test_every_parser_citation_still_says_what_it_claimed(self) -> None:
        for site in LEGACY_VALUE_PARSERS:
            lines = (REPO_ROOT / site.path).read_text(
                encoding="utf-8"
            ).splitlines()
            assert site.anchor in lines[site.line - 1], (
                f"{site.path}:{site.line} no longer contains "
                f"{site.anchor!r} — it says {lines[site.line - 1].strip()!r}"
            )

    def test_every_parser_declares_which_fact_it_goes_after(self) -> None:
        for site in LEGACY_VALUE_PARSERS:
            assert site.fact in FACT_HOMES, site.path


class TestTheGateCanFail:
    """A control that cannot fail is not a control."""

    def test_a_synthetic_undeclared_parse_site_is_caught(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "rogue.py").write_text(
            "def f(sym):\n"
            "    return sym.signature.split('->')[-1]\n",
            encoding="utf-8",
        )
        found = find_undeclared_value_parsers(tmp_path)
        assert [f"{p}:{ln}" for p, ln, _ in found] == [
            "packages/p/src/m/rogue.py:2"
        ]

    def test_a_display_read_is_not_a_parse(self, tmp_path: Path) -> None:
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "show.py").write_text(
            "def f(symbols):\n"
            "    return {s.id: s.signature for s in symbols}\n",
            encoding="utf-8",
        )
        assert find_undeclared_value_parsers(tmp_path) == []

    def test_a_write_is_not_a_parse(self, tmp_path: Path) -> None:
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "w.py").write_text(
            "def f(sym, text):\n"
            "    sym.signature = text.strip()\n",
            encoding="utf-8",
        )
        assert find_undeclared_value_parsers(tmp_path) == []

    def test_tests_are_out_of_scope(self, tmp_path: Path) -> None:
        t = tmp_path / "packages" / "p" / "tests"
        t.mkdir(parents=True)
        (t / "test_x.py").write_text(
            "def test_f(sym):\n"
            "    assert sym.signature.split('->')\n",
            encoding="utf-8",
        )
        assert find_undeclared_value_parsers(tmp_path) == []

    def test_a_kwarg_carry_into_the_same_slot_is_not_a_parse(
        self, tmp_path: Path
    ) -> None:
        # The SCIP round-trip shape: the value is carried into an
        # identically-named slot, never read.
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "copy.py").write_text(
            "def f(sym):\n"
            "    return Symbol(name=sym.name, signature=sym.signature)\n",
            encoding="utf-8",
        )
        assert find_undeclared_value_parsers(tmp_path) == []

    def test_a_differently_named_kwarg_IS_a_parse(
        self, tmp_path: Path
    ) -> None:
        # The difference arm for the carve-out above: only the identically
        # named slot is exempt, so the exemption cannot be a general hole.
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "k.py").write_text(
            "def f(sym):\n"
            "    return _extract(text=sym.signature)\n",
            encoding="utf-8",
        )
        assert [ln for _, ln, _ in find_undeclared_value_parsers(tmp_path)] == [2]

    def test_the_getattr_indirection_is_caught(self, tmp_path: Path) -> None:
        # jackson_dispatch's shape. A plain attribute walk cannot see it;
        # INV-lafid is the filed instance of that blind spot.
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "g.py").write_text(
            "def f(sym):\n"
            '    return getattr(sym, "signature", None)\n',
            encoding="utf-8",
        )
        assert [ln for _, ln, _ in find_undeclared_value_parsers(tmp_path)] == [2]

    def test_a_method_call_on_the_value_is_a_parse(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "s.py").write_text(
            "def f(sym):\n"
            '    return (sym.signature or "").strip()\n',
            encoding="utf-8",
        )
        assert [ln for _, ln, _ in find_undeclared_value_parsers(tmp_path)] == [2]

    def test_a_declared_site_is_not_reported(self, tmp_path: Path) -> None:
        # Proves the declaration is what silences it, not the shape.
        site = LEGACY_VALUE_PARSERS[0]
        target = tmp_path / site.path
        target.parent.mkdir(parents=True)
        body = "\n" * (site.line - 1) + "x = _extract(sym.signature)\n"
        target.write_text(body, encoding="utf-8")
        assert find_undeclared_value_parsers(tmp_path) == []

    def test_a_file_that_cannot_be_parsed_is_skipped_not_fatal(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "packages" / "p" / "src" / "m"
        src.mkdir(parents=True)
        (src / "broken.py").write_text("def f(:\n", encoding="utf-8")
        assert find_undeclared_value_parsers(tmp_path) == []


class TestLiveTree:
    def test_no_undeclared_value_parser_exists(self) -> None:
        """The A+no gate, on the real tree.

        The nine are declared in ``LEGACY_VALUE_PARSERS``. A tenth fails
        here, which is the point: adding one is a decision, and this is
        where the decision gets made rather than noticed later.
        """
        undeclared = find_undeclared_value_parsers(REPO_ROOT)
        assert undeclared == [], (
            "undeclared Symbol.signature value-parse site(s):\n"
            + "\n".join(f"  {p}:{ln}  {src}" for p, ln, src in undeclared)
            + "\n\nADR-0058: the facts inside a signature have declared "
              "homes (see FACT_HOMES). Read the home, or argue the "
              "exception onto LEGACY_VALUE_PARSERS."
        )
