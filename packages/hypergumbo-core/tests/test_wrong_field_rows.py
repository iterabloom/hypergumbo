# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""Framework rows are keyed on the field the analyzer writes.

Fifteen rows were keyed on ``decorator:`` for names their language's analyzer
records somewhere else, so they matched nothing: a C# ``[Fact]`` method or a
Rust ``#[test]`` function was not a test function, and its file's only entry
was a library export. Only the Python, JS/TS, Java, Kotlin, Scala and Groovy
analyzers write ``meta.decorators``.

Each test hands the SHIPPED rows a symbol shaped as the analyzer emits it
(the shapes were read off a run, 2026-11-22):

- Rust and C# write ``meta.annotations`` (``{"name": "test", ...}``,
  ``{"name": "Xunit.Fact", ...}``);
- WGSL writes ``meta.entry_point`` (``"vertex"``);
- PHP writes an ``implements`` name into ``meta.base_classes``; and PHP names
  a method ``Class.method``, so a ``^test`` row over the whole name never
  matched a PHPUnit ``testX`` method either.

Four of the fifteen cannot be fixed by the key, because the analyzer records
nothing a row could read: PHP ``@test`` / ``#[Test]``, F# ``[<Fact>]``, D
``@("test")``, and Plug's ``conn``. They are pinned below as not matching,
so that the day an analyzer starts recording them this file says so.
"""

from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Span, Symbol


def _sym(
    language: str, name: str, kind: str = "function", *,
    path: str = "src/x", meta: dict[str, Any] | None = None,
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-3:{name}:{kind}", name=name, kind=kind,
        language=language, path=path, span=Span(1, 3, 0, 0),
        meta=dict(meta or {}),
    )


def _annotated(language: str, name: str, *annotations: str, kind: str = "function") -> Symbol:
    return _sym(language, name, kind, meta={
        "annotations": [{"name": a, "args": [], "kwargs": {}} for a in annotations],
    })


def _concepts(symbol: Symbol, *frameworks: str) -> set[str]:
    clear_pattern_cache()
    enrich_symbols([symbol], set(frameworks))
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


class TestRust:
    @pytest.mark.parametrize("attribute", ["test", "tokio::test", "async_std::test"])
    def test_a_test_attribute_makes_a_test_function(self, attribute: str) -> None:
        assert "test_function" in _concepts(_annotated("rust", "adds", attribute, "cfg"))

    def test_a_bench_attribute_makes_a_benchmark_function(self) -> None:
        assert "benchmark_function" in _concepts(_annotated("rust", "bench_add", "bench"))

    @pytest.mark.parametrize("attribute", ["cfg", "inline", "test_case", "contest"])
    def test_another_attribute_does_not(self, attribute: str) -> None:
        got = _concepts(_annotated("rust", "helper", attribute))
        assert "test_function" not in got
        assert "benchmark_function" not in got


class TestCSharp:
    @pytest.mark.parametrize(
        "attribute", ["Fact", "Theory", "Test", "TestMethod", "Xunit.Fact"],
    )
    def test_a_test_attribute_makes_a_test_function(self, attribute: str) -> None:
        method = _annotated("csharp", "MathTests.Adds", attribute, kind="method")
        assert "test_function" in _concepts(method)

    @pytest.mark.parametrize("attribute", ["InlineData", "Obsolete", "TestCategory"])
    def test_another_attribute_does_not(self, attribute: str) -> None:
        method = _annotated("csharp", "MathTests.Helper", attribute, kind="method")
        assert "test_function" not in _concepts(method)


class TestWgsl:
    @pytest.mark.parametrize("stage", ["vertex", "fragment", "compute"])
    def test_a_stage_attribute_makes_a_shader_entrypoint(self, stage: str) -> None:
        fn = _sym("wgsl", "vs_main", meta={"entry_point": stage})
        assert "shader_entrypoint" in _concepts(fn)

    def test_a_function_with_no_stage_is_not_one(self) -> None:
        assert "shader_entrypoint" not in _concepts(_sym("wgsl", "helper"))

    def test_the_key_is_wgsls(self) -> None:
        other = _sym("glsl", "main", meta={"entry_point": "vertex"})
        assert "shader_entrypoint" not in _concepts(other)


class TestLaravelShouldQueue:
    """Jobs and queued listeners both implement ``ShouldQueue``; Laravel tells
    them apart by where they live (``app/Jobs``, ``app/Listeners``)."""

    @staticmethod
    def _queued(path: str) -> Symbol:
        return _sym("php", "X", "class", path=path, meta={"base_classes": ["ShouldQueue"]})

    def test_a_queued_job_is_a_task(self) -> None:
        got = _concepts(self._queued("app/Jobs/SendMail.php"), "laravel")
        assert "task" in got
        assert "event_handler" not in got

    def test_a_queued_listener_is_an_event_handler(self) -> None:
        got = _concepts(self._queued("app/Listeners/Notify.php"), "laravel")
        assert "event_handler" in got
        assert "task" not in got

    def test_a_queued_class_elsewhere_is_neither(self) -> None:
        got = _concepts(self._queued("app/Mail/Welcome.php"), "laravel")
        assert not got & {"task", "event_handler"}

    def test_a_class_in_jobs_that_is_not_queued_is_not_a_task(self) -> None:
        plain = _sym("php", "Helper", "class", path="app/Jobs/Helper.php")
        assert "task" not in _concepts(plain, "laravel")


class TestPhpUnitMethodNames:
    def test_a_test_prefixed_method_is_a_test_function(self) -> None:
        method = _sym("php", "MathTest.testMultiplies", "method", path="tests/MathTest.php")
        assert "test_function" in _concepts(method)

    def test_a_production_method_named_test_something_is_not(self) -> None:
        """koel: ``DropboxStorage.testSetup`` checks a connection. PHPUnit
        collects ``*Test.php`` files, and the row asks for one."""
        method = _sym(
            "php", "DropboxStorage.testSetup", "method",
            path="app/Services/SongStorages/DropboxStorage.php",
        )
        assert "test_function" not in _concepts(method)

    @pytest.mark.parametrize("name", ["MathTest.helper", "TestCase.setUp", "testing.run", "MathTest.attest"])
    def test_other_methods_are_not(self, name: str) -> None:
        method = _sym("php", name, "method", path="tests/MathTest.php")
        assert "test_function" not in _concepts(method)


class TestRowsTheKeyCannotFix:
    """What the analyzers emit today for these constructs carries nothing a
    row can read; the symbols below are those shapes."""

    @pytest.mark.parametrize(("language", "name", "kind"), [
        ("php", "MathTest.it_adds", "method"),        # /** @test */ or #[Test]
        ("fsharp", "addsMany", "function"),           # [<Fact>]
        ("d", "checkAdd", "function"),                # @("test")
    ])
    def test_an_attribute_the_analyzer_does_not_record_is_not_detected(
        self, language: str, name: str, kind: str,
    ) -> None:
        assert "test_function" not in _concepts(_sym(language, name, kind))
