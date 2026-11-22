# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py
"""A convention ``main`` is matched by the language's own rule for a program start.

``frameworks/main-functions.yaml`` matched a function by name, kind and
language. For these languages that is not the language's rule:

- Go: ``func main`` starts a program only in ``package main``. A cobra
  callback ``func main(cmd, args)`` in a library package, or a vendored tool
  renamed into one, is an ordinary function. The Go analyzer writes the
  package into ``Symbol.qualified_name`` (``main.main``), and a pattern can
  now read that field (``qualified_name:``).
- Erlang: the row named ``main`` and ``start``, but the analyzer names a
  function ``name/arity``, so the row matched nothing at all. An escript's
  entry is ``main/1``; an OTP ``start/2`` is an application callback, a
  different concept.

Each row is exercised through the shipped YAML (``enrich_symbols`` with no
framework detected loads the convention files).
"""

from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import (
    MAIN_FUNCTION_CONFIDENCE,
    MAIN_FUNCTION_UNMEASURED_CONFIDENCE,
    main_function_confidence,
)
from hypergumbo_core.framework_patterns import (
    FrameworkPatternDef,
    Pattern,
    clear_pattern_cache,
    enrich_symbols,
)
from hypergumbo_core.ir import Span, Symbol


def _fn(
    language: str, name: str, qualified_name: str | None, *,
    path: str = "src/x", kind: str = "function",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-3:{name}:{kind}", name=name, kind=kind,
        language=language, path=path, span=Span(1, 3, 0, 0),
        qualified_name=qualified_name, meta={},
    )


def _is_main(symbol: Symbol) -> bool:
    clear_pattern_cache()
    enrich_symbols([symbol], set())
    return any(
        c.get("concept") == "main_function"
        for c in (symbol.meta or {}).get("concepts", [])
    )


class TestQualifiedNameField:
    """``qualified_name:`` is a filter like ``language:`` and ``symbol_path:``:
    it must hold for the row to match by any of its other fields."""

    @staticmethod
    def _row(**fields: str) -> Pattern:
        return Pattern(concept="main_function", symbol_name="^main$", **fields)

    def test_matches_the_symbols_qualified_name(self) -> None:
        row = self._row(qualified_name=r"^main\.main$")

        assert row.matches(_fn("go", "main", "main.main")) is not None
        assert row.matches(_fn("go", "main", "lib.main")) is None

    def test_a_symbol_without_a_qualified_name_does_not_match(self) -> None:
        """No recorded name is not a name that happens to fit."""
        row = self._row(qualified_name=r"main")

        assert row.matches(_fn("go", "main", None)) is None
        assert row.matches(_fn("go", "main", "")) is None

    def test_it_is_a_search_like_symbol_path(self) -> None:
        row = self._row(qualified_name=r"\.main$")

        assert row.matches(_fn("scala", "main", "pkg.App.main")) is not None

    def test_a_row_without_it_is_unchanged(self) -> None:
        assert self._row().matches(_fn("go", "main", None)) is not None

    def test_it_filters_every_other_match_type(self) -> None:
        row = Pattern(
            concept="controller", base_class="^Base$", qualified_name=r"^app\.",
        )
        ours = _fn("python", "C", "app.C", kind="class")
        theirs = _fn("python", "C", "vendor.C", kind="class")
        for s in (ours, theirs):
            s.meta = {"base_classes": ["Base"]}

        assert row.matches(ours) is not None
        assert row.matches(theirs) is None

    def test_the_yaml_key_is_read(self) -> None:
        loaded = FrameworkPatternDef.from_dict({
            "id": "t", "language": "go",
            "patterns": [{
                "concept": "main_function", "symbol_name": "^main$",
                "qualified_name": r"^main\.main$",
            }],
        })

        assert loaded.patterns[0].qualified_name == r"^main\.main$"
        assert loaded.patterns[0].matches(_fn("go", "main", "lib.main")) is None


class TestGoRow:
    def test_main_of_package_main(self) -> None:
        assert _is_main(_fn("go", "main", "main.main", path="cmd/tool/main.go"))

    @pytest.mark.parametrize("qualified", ["agnhost.main", "gci.main", "main", None])
    def test_main_of_another_package_is_not_a_program_start(
        self, qualified: str | None,
    ) -> None:
        assert not _is_main(_fn("go", "main", qualified, path="pkg/x/x.go"))

    def test_a_method_named_main_is_not(self) -> None:
        assert not _is_main(
            _fn("go", "main", "main.T.main", path="cmd/tool/main.go", kind="method")
        )


class TestErlangRow:
    def test_main_of_arity_one_is_the_escript_entry(self) -> None:
        assert _is_main(_fn("erlang", "main/1", None, path="script/app_deps.erl"))

    @pytest.mark.parametrize("name", ["main/0", "main/2", "start/2", "start/0", "main"])
    def test_other_names_and_arities_are_not(self, name: str) -> None:
        assert not _is_main(_fn("erlang", name, None, path="src/app.erl"))


class TestConfidence:
    def test_erlang_is_not_a_measured_language(self) -> None:
        """Seven ``main/1`` definitions in the census repositories are not a
        measurement: the value is the unmeasured one, not a number of its own."""
        assert "erlang" not in MAIN_FUNCTION_CONFIDENCE
        assert main_function_confidence("erlang") == MAIN_FUNCTION_UNMEASURED_CONFIDENCE
