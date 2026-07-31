# SPDX-License-Identifier: AGPL-3.0-or-later
"""One home for "how a language qualifies a member name onto its owner".

INV-tihim's originally-filed cause: this fact was recorded independently in
several consumers and they disagreed. ``linkers/containment`` knew
``("#", "::", ".")``; ``linkers/type_hierarchy`` hand-rolled ``#`` and ``.``
and **not** ``::``, so it returned ``owner=None`` for every Rust member and
was structurally incapable of firing for that language;
``linkers/method_call_recovery`` held a third complete copy of the tuple.

Deliberately NOT folded into ``qualified_name_axis``. That module maps a
language to the separator of its *fully-qualified module path* (``php`` →
``\\``, ``rust`` → ``::``). This one is about the separator between an
**owner and its member** in ``Symbol.name``. They coincide for Rust and
diverge for PHP, whose members are emitted dotted while its qualified names
use a backslash — so reusing the qualified-name policy here would silently
mis-split every PHP member.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.member_names import (
    MEMBER_NAME_SEPARATORS,
    find_hand_rolled_separator_literals,
    member_owner,
    member_short_name,
    split_member_name,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_splits_every_separator_the_analyzers_emit() -> None:
    assert split_member_name("User.save") == ("User", "save")
    assert split_member_name("User#save") == ("User", "save")          # ruby
    assert split_member_name("MyTrait::method") == ("MyTrait", "method")  # rust / cpp
    assert split_member_name("save") == (None, "save")


def test_rsplit_keeps_the_immediate_owner_not_the_root() -> None:
    """A dotted FQN's owner is the enclosing type, not the top package."""
    assert split_member_name("com.example.UserService.getUsers") == (
        "com.example.UserService", "getUsers",
    )
    assert split_member_name("Outer.Inner.do_thing") == ("Outer.Inner", "do_thing")


def test_separator_order_is_specificity_not_position() -> None:
    """``#`` and ``::`` win over ``.`` when a name carries both.

    Ruby emits ``Foo::Bar#baz`` for a namespaced method: the member boundary
    is the ``#``, and splitting on ``::`` or ``.`` first would hand back
    ``Foo`` or a mangled owner. This ordering is the reason the tuple is a
    shared constant rather than something each caller re-derives.
    """
    assert split_member_name("Foo::Bar#baz") == ("Foo::Bar", "baz")
    assert split_member_name("a.b::c") == ("a.b", "c")
    assert MEMBER_NAME_SEPARATORS == ("#", "::", ".")


def test_owner_and_short_name_are_consistent_with_split() -> None:
    for name in ("User.save", "User#save", "MyTrait::method", "save",
                 "com.example.S.get", "Foo::Bar#baz"):
        owner, short = split_member_name(name)
        assert member_owner(name) == owner
        assert member_short_name(name) == short


def test_short_name_of_an_unqualified_name_is_itself() -> None:
    """The bug this replaces returned the WHOLE name as the short name.

    ``type_hierarchy._get_method_short_name("MyTrait::method")`` returned
    ``"MyTrait::method"``, so the name-match against an implementor's
    ``"Square::area"`` could never succeed. An unqualified name returning
    itself is correct; a *qualified* one returning itself is the defect.
    """
    assert member_short_name("area") == "area"
    assert member_short_name("MyTrait::area") == "area"


def test_no_module_hand_rolls_the_separator_tuple() -> None:
    """Live-tree linter: the fact has one home.

    Flags any module outside ``member_names`` that enumerates two or more of
    the separators as a literal collection — the shape all three prior copies
    took. Per-site uses of a single separator (``name.rsplit(".", 1)`` for a
    known-dotted value) are not flagged; the defect is re-declaring the
    *vocabulary*, not using one member of it.
    """
    offenders = find_hand_rolled_separator_literals(REPO_ROOT)
    assert offenders == [], (
        "modules re-declare the member-name separator vocabulary; import "
        "MEMBER_NAME_SEPARATORS / split_member_name instead:\n  "
        + "\n  ".join(offenders)
    )


def test_linter_detects_a_planted_copy(tmp_path: Path) -> None:
    """Non-vacuity — the live-tree test must be able to fail."""
    pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    pkg.mkdir(parents=True)
    (pkg / "offender.py").write_text('SEPS = ("#", "::", ".")\n')
    offenders = find_hand_rolled_separator_literals(tmp_path)
    assert len(offenders) == 1
    assert "offender.py" in offenders[0]


def test_linter_ignores_single_separator_use(tmp_path: Path) -> None:
    """Using one separator is not re-declaring the vocabulary."""
    pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    pkg.mkdir(parents=True)
    (pkg / "fine.py").write_text('def f(n):\n    return n.rsplit(".", 1)[-1]\n')
    assert find_hand_rolled_separator_literals(tmp_path) == []
