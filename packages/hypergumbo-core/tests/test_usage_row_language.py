# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""A framework file's usage rows match calls written in that file's language.

A ``usage:`` row matches a ``UsageContext`` by the name called (and
optionally its position). Nothing compared the language of the framework
file with the language of the code the call is written in, so any loaded row
matched any language's call of that name: with R emitting usage contexts, an
R function calling its own ``createServer()`` gained a Node ``route`` concept,
an ``HTTP ALL /`` entry and a route node from ``node-http.yaml``, which is
loaded for every repository. Short verbs are the common collision (R's base
``get`` and ``match`` against a router's ``get`` / ``match`` rows).

The rule: a usage row of a file whose ``language:`` names languages matches a
context only when the context's file is of one of them (the host languages
``reference_candidates`` reads off the file; JavaScript and TypeScript are
one set). A file declared ``multi``, and a context whose file says nothing
about its language, are not restricted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hypergumbo_core.framework_patterns import (
    FrameworkPatternDef,
    Pattern,
    UsagePatternSpec,
    clear_pattern_cache,
    enrich_symbols,
    match_usage_patterns,
)
from hypergumbo_core.ir import Span, Symbol, UsageContext


def _caller(language: str, path: str) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-9:serve:function", name="serve", kind="function",
        language=language, path=path, span=Span(1, 9, 0, 0), meta={},
    )


def _call(name: str, caller: Symbol, path: str | None = None) -> UsageContext:
    return UsageContext.create(
        kind="call", context_name=name, position="caller",
        path=path if path is not None else caller.path,
        span=Span(3, 3, 0, 20), symbol_ref=caller.id, metadata={},
    )


def _concepts(symbol: Symbol) -> set[tuple[str, str]]:
    return {
        (c["concept"], c.get("framework", ""))
        for c in (symbol.meta or {}).get("concepts", [])
    }


class TestShippedRows:
    """``node-http.yaml`` (JavaScript) is loaded for every repository."""

    def _enriched(self, language: str, path: str) -> set[tuple[str, str]]:
        clear_pattern_cache()
        caller = _caller(language, path)
        enrich_symbols([caller], set(), usage_contexts=[_call("http.createServer", caller)])
        return _concepts(caller)

    @pytest.mark.parametrize(
        ("language", "path"),
        [("javascript", "src/server.js"), ("typescript", "src/server.ts")],
    )
    def test_a_node_call_is_a_node_route(self, language: str, path: str) -> None:
        assert ("route", "node-http") in self._enriched(language, path)

    @pytest.mark.parametrize(
        ("language", "path"),
        [("r", "R/server.R"), ("clojure", "src/server.clj"), ("python", "server.py")],
    )
    def test_the_same_name_called_in_another_language_is_not(
        self, language: str, path: str,
    ) -> None:
        assert ("route", "node-http") not in self._enriched(language, path)


def _def(language: Any) -> FrameworkPatternDef:
    return FrameworkPatternDef(
        id="t", language=language,
        patterns=[Pattern(
            concept="application",
            usage=UsagePatternSpec(kind="^call$", name="^boot$"),
        )],
    )


class TestTheRule:
    @staticmethod
    def _matched(language: Any, host: frozenset[str] | None) -> bool:
        caller = _caller("r", "R/app.R")
        return bool(match_usage_patterns(_call("boot", caller), [_def(language)], host))

    def test_the_files_language_must_be_the_contexts(self) -> None:
        assert self._matched("r", frozenset({"r"}))
        assert not self._matched("ruby", frozenset({"r"}))

    def test_a_list_of_languages_admits_each(self) -> None:
        assert self._matched(["java", "kotlin"], frozenset({"kotlin"}))
        assert not self._matched(["java", "kotlin"], frozenset({"scala"}))

    def test_javascript_and_typescript_are_one_set(self) -> None:
        assert self._matched("javascript", frozenset({"typescript", "javascript"}))
        assert self._matched("typescript", frozenset({"vue", "javascript", "typescript"}))

    @pytest.mark.parametrize("language", ["multi", "unknown", "", None, []])
    def test_a_file_that_names_no_language_is_not_restricted(self, language: Any) -> None:
        assert self._matched(language, frozenset({"r"}))

    def test_a_context_of_unknown_language_is_not_restricted(self) -> None:
        assert self._matched("ruby", None)

    def test_without_a_host_language_argument_nothing_is_restricted(self) -> None:
        caller = _caller("r", "R/app.R")
        assert match_usage_patterns(_call("boot", caller), [_def("ruby")])


def test_the_contexts_file_decides_not_the_stamped_symbols(tmp_path: Any) -> None:
    """The language is the one of the file the call is written in: an R call
    whose context names a symbol of another language is still an R call."""
    ruby_symbol = _caller("ruby", "lib/app.rb")
    r_call = _call("boot", ruby_symbol, path="R/app.R")
    with patch(
        "hypergumbo_core.framework_patterns.load_framework_patterns",
        return_value=_def("ruby"),
    ):
        enrich_symbols([ruby_symbol], {"t"}, usage_contexts=[r_call])

    assert ("application", "t") not in _concepts(ruby_symbol)
