# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""``method_name:`` reads a member's own name, and a declared kind always binds.

Two defects of the matcher, found behind rows that matched nothing:

1. ``method_name:`` cut the name at its last dot. Ruby names an instance
   method ``MathTest#test_adds`` and Rust and C++ a member ``Type::member``,
   so for those languages the "method name" was the whole name and no
   anchored row could match it. The member's own name is now what
   ``member_names.split_member_name`` says it is, for every separator.

2. ``symbol_kind:`` (and ``framework_role:``, ``meta_match:``) was checked by
   two of the match types and ignored by the rest. A row that said
   ``method_name: ^render$`` and ``symbol_kind: ^method$`` matched a top-level
   function named ``render``; a row with ``base_class:`` and ``symbol_kind:``
   never looked at the kind. The three are conditions on the symbol itself,
   like ``language:`` and ``symbol_path:``: they hold, or the row does not
   match by any of its other fields.

The rows these change are tested through their analyzers, in the language
packages (a symbol built by hand can only show what its author believed the
analyzer records). The last test here reads every shipped row and refuses a
combination of fields the matcher does not conjoin.
"""

from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.framework_patterns import (
    Pattern,
    get_frameworks_dir,
    load_framework_patterns,
)
from hypergumbo_core.ir import Span, Symbol


def _sym(
    name: str, kind: str = "method", *, language: str = "python",
    meta: dict[str, Any] | None = None,
) -> Symbol:
    return Symbol(
        id=f"{language}:src/x:1-3:{name}:{kind}", name=name, kind=kind,
        language=language, path="src/x", span=Span(1, 3, 0, 0),
        meta=dict(meta or {}),
    )


class TestMethodNameReadsTheMembersOwnName:
    @pytest.mark.parametrize(("name", "own"), [
        ("MathTest.test_adds", "test_adds"),                  # python, swift, java ...
        ("MathTest#test_adds", "test_adds"),                  # ruby instance method
        ("Api::V1::MathTest#test_adds", "test_adds"),         # ruby, namespaced
        ("Api::MathTest.test_adds", "test_adds"),             # ruby class method
        ("Suite::test_adds", "test_adds"),                    # rust, cpp
        ("com.example.MathTest.test_adds", "test_adds"),
    ])
    def test_every_owner_separator(self, name: str, own: str) -> None:
        row = Pattern(concept="test_function", method_name=r"^test_")

        result = row.matches(_sym(name))

        assert result is not None
        assert result["matched_method_name"] == own

    def test_an_unqualified_name_is_its_own_name(self) -> None:
        row = Pattern(concept="test_function", method_name=r"^test_")

        assert row.matches(_sym("test_adds", "function")) is not None

    def test_the_owner_is_not_the_member(self) -> None:
        """A helper nested in ``test_outer`` is named ``test_outer.helper``:
        its own name is ``helper``."""
        row = Pattern(concept="test_function", method_name=r"^test_")

        assert row.matches(_sym("test_outer.helper", "function")) is None
        assert row.matches(_sym("TestThings#helper")) is None

    def test_it_is_matched_from_the_start_of_the_own_name(self) -> None:
        row = Pattern(concept="route_handler", method_name=r"on_get$")

        assert row.matches(_sym("Things.on_get")) is not None
        assert row.matches(_sym("Things.fallback_on_get")) is None

    def test_with_a_parent_base_class_both_must_hold(self) -> None:
        row = Pattern(
            concept="lifecycle_hook", method_name=r"^onCreate$",
            parent_base_class=r"^Activity$",
        )
        activity = {"parent_base_classes": ["Activity"]}

        assert row.matches(_sym("Main.onCreate", meta=activity)) is not None
        assert row.matches(_sym("Main.onCreate", meta={"parent_base_classes": ["Object"]})) is None
        assert row.matches(_sym("Main.onStart", meta=activity)) is None


class TestKindRoleAndMetaBindEveryMatchType:
    """A declared ``symbol_kind`` / ``framework_role`` / ``meta_match`` holds
    whichever field the row matches by."""

    def test_method_name_with_a_kind(self) -> None:
        row = Pattern(
            concept="render_method", method_name=r"^render$", symbol_kind=r"^method$",
        )

        matched = row.matches(_sym("MyElement.render", "method"))
        assert matched is not None
        assert matched["matched_method_name"] == "render"
        assert matched["matched_symbol_kind"] == "method"
        assert row.matches(_sym("render", "function")) is None
        assert row.matches(_sym("Config.render", "field")) is None

    def test_parent_base_class_with_a_kind(self) -> None:
        row = Pattern(
            concept="lifecycle_hook", parent_base_class=r"^Activity$",
            symbol_kind=r"^method$",
        )
        meta = {"parent_base_classes": ["Activity"]}

        assert row.matches(_sym("Main.onCreate", "method", meta=meta)) is not None
        assert row.matches(_sym("Main.TAG", "field", meta=meta)) is None

    def test_base_class_with_a_kind(self) -> None:
        row = Pattern(concept="connector", base_class=r"^SinkTask$", symbol_kind=r"^class$")
        meta = {"base_classes": ["SinkTask"]}

        matched = row.matches(_sym("FileSink", "class", meta=meta))
        assert matched is not None
        assert matched["matched_base_class"] == "SinkTask"
        assert matched["matched_symbol_kind"] == "class"
        assert row.matches(_sym("FileSinkLike", "interface", meta=meta)) is None

    def test_decorator_with_a_kind(self) -> None:
        row = Pattern(concept="task", decorator=r"^task$", symbol_kind=r"^function$")
        meta = {"decorators": [{"name": "task", "args": [], "kwargs": {}}]}

        assert row.matches(_sym("send", "function", meta=meta)) is not None
        assert row.matches(_sym("Sender", "class", meta=meta)) is None

    def test_annotation_with_a_kind(self) -> None:
        row = Pattern(concept="test_function", annotation=r"^Test$", symbol_kind=r"^method$")
        meta = {"annotations": [{"name": "Test", "args": [], "kwargs": {}}]}

        assert row.matches(_sym("T.adds", "method", meta=meta)) is not None
        assert row.matches(_sym("T", "class", meta=meta)) is None

    def test_framework_role_with_a_method_name(self) -> None:
        row = Pattern(concept="route", method_name=r"^index$", framework_role=r"^route$")

        assert row.matches(_sym("Pages.index", meta={"framework_role": "route"})) is not None
        assert row.matches(_sym("Pages.index", meta={"framework_role": "job"})) is None
        assert row.matches(_sym("Pages.index")) is None

    def test_meta_match_with_a_symbol_name(self) -> None:
        row = Pattern(
            concept="shader_entry", symbol_name=r"^main$",
            meta_match={"entry_point": r"^vertex$"},
        )

        matched = row.matches(_sym("main", "function", meta={"entry_point": "vertex"}))
        assert matched is not None
        assert matched["matched_meta"] == {"entry_point": "vertex"}
        assert row.matches(_sym("main", "function", meta={"entry_point": "compute"})) is None
        assert row.matches(_sym("main", "function")) is None

    def test_meta_match_with_a_base_class(self) -> None:
        row = Pattern(
            concept="model", base_class=r"^Model$", meta_match={"abstract": r"^False$"},
        )

        assert row.matches(
            _sym("User", "class", meta={"base_classes": ["Model"], "abstract": False})
        ) is not None
        assert row.matches(
            _sym("Base", "class", meta={"base_classes": ["Model"], "abstract": True})
        ) is None

    def test_what_a_match_records_is_unchanged_for_the_two_older_shapes(self) -> None:
        """``symbol_name`` + kind, and kind + ``meta_match`` alone, checked
        the kind before; their results keep the same keys."""
        by_name = Pattern(concept="main_function", symbol_name=r"^main$", symbol_kind=r"^function$")
        by_meta = Pattern(
            concept="shader_entry", symbol_kind=r"^function$",
            meta_match={"entry_point": r"^vertex$"},
        )

        assert by_name.matches(_sym("main", "function")) == {
            "concept": "main_function", "matched_symbol_name": "main",
            "matched_symbol_kind": "function",
        }
        assert by_name.matches(_sym("main", "variable")) is None
        assert by_meta.matches(_sym("vs", "function", meta={"entry_point": "vertex"})) == {
            "concept": "shader_entry", "matched_symbol_kind": "function",
            "matched_meta": {"entry_point": "vertex"},
        }


# The fields a row can match BY. A row conjoins: one of the first three alone;
# ``parameter_type``, alone or with ``symbol_name``; ``symbol_name`` alone;
# ``method_name`` and/or ``parent_base_class``; or none of them (kind, role,
# meta only). Any other pair is tried one after the other, which is an OR
# that nothing in the YAML says.
_MATCH_BY = (
    "decorator", "base_class", "annotation", "parameter_type", "symbol_name",
    "method_name", "parent_base_class",
)
_CONJOINED = {
    (), ("decorator",), ("base_class",), ("annotation",), ("parameter_type",),
    ("symbol_name",), ("parameter_type", "symbol_name"), ("method_name",),
    ("parent_base_class",), ("method_name", "parent_base_class"),
}


def test_no_shipped_row_declares_fields_the_matcher_does_not_conjoin() -> None:
    files = sorted(get_frameworks_dir().glob("*.yaml"))
    assert len(files) > 100, "the framework files were not found"
    seen: set[tuple[str, ...]] = set()
    offenders: list[str] = []
    for path in files:
        loaded = load_framework_patterns(path.stem)
        assert loaded is not None, path.name
        for index, row in enumerate(loaded.patterns):
            if row.usage is not None:
                continue
            declared = tuple(f for f in _MATCH_BY if getattr(row, f) is not None)
            seen.add(declared)
            if declared not in _CONJOINED:
                offenders.append(f"{path.name} row {index} ({row.concept}): {' + '.join(declared)}")

    assert not offenders, "\n".join(offenders)
    # Reach: the scan read rows of the shapes this file is about.
    assert ("method_name", "parent_base_class") in seen
    assert ("symbol_name",) in seen
