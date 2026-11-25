# SPDX-License-Identifier: AGPL-3.0-or-later
"""``ScopedVarTypes``: a variable's type closes with the function that binds it (INV-zabud).

The nodes here are stand-ins with a byte span and a parent, which is all the
class reads; the analyzers' own tests run it over real syntax trees.
"""
from __future__ import annotations

from dataclasses import dataclass

from hypergumbo_core.analyze.var_scope import ScopedVarTypes


@dataclass
class Node:
    start_byte: int
    end_byte: int
    parent: "Node | None" = None


FILE = Node(0, 100)
FIRST = Node(10, 30, FILE)   # a function
INSIDE_FIRST = Node(15, 20, FIRST)
SECOND = Node(40, 60, FILE)  # the next function
INSIDE_SECOND = Node(45, 50, SECOND)
AFTER = Node(70, 80, FILE)


def test_a_local_is_gone_when_its_function_ends() -> None:
    types = ScopedVarTypes()
    types.open_scope(FIRST)
    types["db"] = "Other"
    types.close_scopes_before(INSIDE_FIRST)
    assert types["db"] == "Other"  # still inside: nothing closed
    types.close_scopes_before(SECOND)
    assert "db" not in types


def test_a_binding_outside_every_scope_is_seen_by_every_function() -> None:
    types = ScopedVarTypes()
    types["repo"] = "Repository"  # a field
    types.open_scope(FIRST)
    assert types["repo"] == "Repository"
    types.close_scopes_before(SECOND)
    assert types["repo"] == "Repository"


def test_a_local_that_shadows_a_field_gives_the_field_back() -> None:
    types = ScopedVarTypes()
    types["db"] = "Field"
    types.open_scope(FIRST)
    types["db"] = "Local"
    types["db"] = "Narrowed"  # a second write keeps the FIRST replaced value
    assert types["db"] == "Narrowed"
    types.close_scopes_before(SECOND)
    assert types["db"] == "Field"


def test_a_nested_function_sees_the_outer_locals_and_closes_alone() -> None:
    types = ScopedVarTypes()
    types.open_scope(FIRST)
    types["outer"] = "A"
    types.open_scope(INSIDE_FIRST)
    types["inner"] = "B"
    assert types["outer"] == "A"
    types.close_scopes_before(Node(22, 25, FIRST))  # past the nested one only
    assert "inner" not in types and types["outer"] == "A"
    types.close_scopes_before(AFTER)
    assert not types


def test_bind_outer_survives_the_scope_it_was_written_in() -> None:
    types = ScopedVarTypes()
    types.open_scope(FIRST)
    types.bind_outer("svc", "Service")  # a parameter property
    types.close_scopes_before(SECOND)
    assert types["svc"] == "Service"


def test_bound_here_is_about_the_innermost_scope() -> None:
    types = ScopedVarTypes()
    types["db"] = "Field"
    assert types.bound_here("db")
    types.open_scope(FIRST)
    assert not types.bound_here("db")  # visible, but not bound by this function
    types["db"] = "Local"
    assert types.bound_here("db")


def test_scope_end_names_the_open_scope() -> None:
    types = ScopedVarTypes()
    assert types.scope_end is None
    types.open_scope(FIRST)
    assert types.scope_end == 30
    types.close_scopes_before(SECOND)
    assert types.scope_end is None


def test_at_answers_per_function_after_the_walk() -> None:
    types = ScopedVarTypes(remember=True)
    types["mod"] = "Module"
    types.open_scope(FIRST)
    types["db"] = "Other"
    types.close_scopes_before(SECOND)
    types.open_scope(SECOND)
    types["db"] = "Conn"
    # No further node: ``at`` closes what is still open.
    assert types.at(INSIDE_FIRST) == {"mod": "Module", "db": "Other"}
    assert types.at(INSIDE_SECOND) == {"mod": "Module", "db": "Conn"}
    assert types.at(AFTER) == {"mod": "Module"}


def test_at_without_remember_has_only_the_file_bindings() -> None:
    types = ScopedVarTypes()
    types.open_scope(FIRST)
    types["db"] = "Other"
    assert types.at(INSIDE_FIRST) == {}


def test_two_scopes_with_one_span_keep_the_inner_bindings() -> None:
    types = ScopedVarTypes(remember=True)
    types.open_scope(FIRST)
    types.open_scope(FIRST)
    types["x"] = "T"
    assert types.at(INSIDE_FIRST) == {"x": "T"}
