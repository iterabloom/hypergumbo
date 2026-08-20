# SPDX-License-Identifier: AGPL-3.0-or-later
"""A language whose catalogued I/O is method-shaped and that emits no method calls.

WHAT THIS CLOSES. ``compute_boundary_coverage`` asked one question per language —
*did it produce ANY call edge?* — and one edge was enough to look covered. Measured
on a real Kotlin fixture that reads a socket and writes the bytes to disk: 93
catalogued sinks, ``dataflow_capable: False``, zero findings, and a ``confirmed``
verdict at rc 0. That is the worst failure this tool has: it claims support, looks
at nothing, and reports silence as safety.

THE PREDICATE, AND WHY IT IS THIS ONE. Four candidates were measured over six
fixtures with ``scripts/measure-blind-language-signal.py`` before any code changed:

    fixture     A:any  B:non-first-party  C/D:method-construct  F:starved-module
    kt_blind    covered   covered            BLIND                BLIND
    kt_clean    covered   covered            BLIND                covered
    py_io       covered   covered            covered              covered
    py_clean    covered   covered            covered              covered
    py_pure     covered   covered            BLIND                covered
    go_io       covered   covered            covered              covered

* **A** is today's predicate and fails outright.
* **B** ("count only non-first-party dsts") does not discriminate: Kotlin's
  intra-repo dst is ``kotlin:App.kt:9-12:helper:function``, whose path slot is
  RELATIVE, and :func:`is_first_party_callable_dst` requires an ABSOLUTE path slot.
  That is the disclosed relative-path gap surfacing in a second consumer.
* **C/D** catch ``kt_blind`` but also downgrade ``py_pure`` — a pure-computation repo
  making no external calls at all. Conflating "the analyzer cannot see this
  language's I/O" with "this repo calls nothing external" is the blanket-downgrade
  failure mode that made ``confirmed`` unreachable in this gate's first version.
* **F** is the one implemented: for each external module a repo actually CALLS that
  the catalogue covers with METHOD-kind primitives, did any method-construct call
  edge land in it? ``kt_blind`` calls ``java.io.File`` exactly once — the
  CONSTRUCTOR — while ``writeText`` produces no edge at all, which is WI-nasuf
  verbatim. ``py_pure`` calls no catalogued module, so it raises no expectation.

CALL EDGES ONLY, NOT IMPORTS. ``import pathlib`` performs no I/O, so an unused import
must not starve. This mirrors the reasoning already recorded above
``_IO_CALL_EDGE_TYPES`` for a different question, and it is load-bearing here: it is
what keeps "imported but never called" out of the blind set. The Kotlin case is
caught anyway because its evidence is a CALL edge (the constructor), not the import.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import (
    compute_boundary_coverage,
    method_starved_modules,
)

#: The Kotlin map below is not invented. It is what ``hypergumbo survey`` really
#: emits for a file that constructs ``java.io.File`` and calls ``.writeText`` on it:
#: the constructor call is emitted, the method call is not.
_KT_BLIND_EDGES = [
    {"src": "kotlin:src/App.kt:1-1:file:file",
     "dst": "kotlin:java.io.File:0-0:package:external_symbol", "type": "imports"},
    {"src": "kotlin:src/App.kt:1-1:file:file",
     "dst": "kotlin:java.net.Socket:0-0:package:external_symbol", "type": "imports"},
    {"src": "kotlin:src/App.kt:5-9:Handler.pump:method",
     "dst": "kotlin:java.net.Socket:0-0:Socket:external_symbol", "type": "calls"},
    {"src": "kotlin:src/App.kt:5-9:Handler.pump:method",
     "dst": "kotlin:java.io.File:0-0:File:external_symbol", "type": "calls"},
    {"src": "kotlin:src/App.kt:12-14:main:function",
     "dst": "kotlin:src/App.kt:4-10:Handler:class", "type": "calls",
     "meta": {"call_construct": "function"}},
]

#: Real ``survey`` output for ``asyncio.start_server`` / ``os.mkdir`` — both carry
#: ``call_construct: method``, which is what a healthy language looks like here.
_PY_HEALTHY_EDGES = [
    {"src": "python:src/writer.py:5-6:persist:function",
     "dst": "python:os:0-0:mkdir:external_symbol", "type": "calls",
     "meta": {"call_construct": "method"}},
    {"src": "python:src/writer.py:9-12:serve:function",
     "dst": "python:asyncio:0-0:start_server:external_symbol", "type": "calls",
     "meta": {"call_construct": "method"}},
]

#: A pure-computation repo: one first-party call, nothing external.
_PY_PURE_EDGES = [
    {"src": "python:src/pure.py:5-9:total:function",
     "dst": "python:src/pure.py:1-2:add:function", "type": "calls"},
]


def _catalogs(*languages: str) -> dict:
    return {lang: load_catalog(lang) for lang in languages}


class TestTheStarvationPredicate:
    """``method_starved_modules`` is the single answer, consumed by the gate."""

    def test_kotlin_constructor_without_method_call_starves_the_module(self) -> None:
        """BOTH ends of the flow are starved, which is the point.

        ``java.io.File`` is the sink side (``writeText``) and ``java.net.Socket``
        the source side (``getInputStream().readBytes()``). Kotlin emits the
        CONSTRUCTOR call for each and no method call for either, so the catalogue
        was never handed anything it could match at either end.
        """
        starved = method_starved_modules(_KT_BLIND_EDGES, _catalogs("kotlin"))
        assert starved == ["java.io.File", "java.net.Socket"]

    def test_a_healthy_language_starves_nothing(self) -> None:
        assert method_starved_modules(_PY_HEALTHY_EDGES, _catalogs("python")) == []

    def test_a_repo_with_no_external_calls_starves_nothing(self) -> None:
        """THE OVER-DOWNGRADE GUARD, and the reason candidates C/D were rejected.

        py_pure genuinely calls nothing external. Reporting it blind would say the
        analyzer cannot see Python I/O, which is false and is the failure mode that
        made ``confirmed`` unreachable when this gate was first built.
        """
        assert method_starved_modules(_PY_PURE_EDGES, _catalogs("python")) == []

    def test_an_unused_import_does_not_starve(self) -> None:
        """``import java.io.File`` with no call is not evidence of blindness."""
        edges = [{"src": "kotlin:src/App.kt:1-1:file:file",
                  "dst": "kotlin:java.io.File:0-0:package:external_symbol",
                  "type": "imports"}]
        assert method_starved_modules(edges, _catalogs("kotlin")) == []

    def test_a_method_construct_call_satisfies_the_module(self) -> None:
        """The permitting case, asserted directly rather than inferred.

        Satisfying ``java.io.File`` alone must clear ``java.io.File`` alone —
        starvation is per-module, so the still-blind ``java.net.Socket`` has to
        survive. A predicate that collapsed to one per-language verdict would
        pass this file's other tests and fail here.
        """
        edges = list(_KT_BLIND_EDGES) + [
            {"src": "kotlin:src/App.kt:5-9:Handler.pump:method",
             "dst": "kotlin:java.io.File:0-0:writeText:external_symbol",
             "type": "calls", "meta": {"call_construct": "method"}},
        ]
        assert method_starved_modules(edges, _catalogs("kotlin")) == [
            "java.net.Socket",
        ]

    def test_a_language_with_no_catalogue_starves_nothing(self) -> None:
        """No catalogue means no expectation; that case is the sibling gate's."""
        edges = [{"src": "brainfuck:a.bf:1-1:x:function",
                  "dst": "brainfuck:whatever:0-0:y:external_symbol", "type": "calls"}]
        assert method_starved_modules(edges, _catalogs("kotlin")) == []


class TestCataloguesWithNothingMethodShaped:
    """A function-only catalogue can never starve, and must not be consulted as if
    it could. Kotlin's ``kotlin.io.ConsoleKt`` is the real instance of this shape:
    it is catalogued ``functions: [println, print]`` precisely because the receiver
    is compiler-synthesised and absent at AST level, so demanding a method-construct
    edge for it would suppress every ``println(x)``.
    """

    def test_a_function_only_catalogue_starves_nothing(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

        catalog = IoBoundaryCatalog(
            language="toy",
            primitives=[
                IoPrimitive(boundary="logging", module="toy.out",
                            name="emit", kind="function"),
            ],
        )
        edges = [
            {"src": "toy:a.toy:1-2:f:function",
             "dst": "toy:toy.out:0-0:emit:external_symbol", "type": "calls"},
            {"src": "toy:a.toy:1-2:f:function",
             "dst": "toy:a.toy:9-9:g:function", "type": "calls",
             "meta": {"call_construct": "function"}},
        ]
        assert method_starved_modules(edges, {"toy": catalog}) == []


class TestMalformedEdgesAreSkipped:
    """A src with no language slot cannot be attributed, so it is skipped rather
    than being charged to a language chosen by string accident."""

    def test_src_without_a_colon_is_ignored(self) -> None:
        edges = [
            {"src": "not-a-symbol-id",
             "dst": "kotlin:java.io.File:0-0:File:external_symbol", "type": "calls"},
            {"src": "kotlin:a.kt:1-2:f:function",
             "dst": "kotlin:a.kt:9-9:g:function", "type": "calls",
             "meta": {"call_construct": "function"}},
        ]
        assert method_starved_modules(edges, _catalogs("kotlin")) == []


class TestAnalyzersThatNeverStampCallConstruct:
    """THE FALSE POSITIVE THAT NEARLY SHIPPED, and the reason for the abstention.

    ``call_construct`` is not populated by every analyzer. Measured on two real
    repos (alertmanager, apollo-server): Go stamps it on 7,741 call edges, 6,012 of
    them ``method``; **JavaScript and TypeScript stamp it on ZERO of 2,995**. So for
    a JS repo "no method call landed in ``fs``" is absence of evidence, not evidence
    of blindness — and the first version of this predicate reported ``fs``,
    ``console`` and ``process`` starved on BOTH real repos, which would have
    downgraded every JavaScript repo in existence.

    ``None`` (could not check) is not ``empty`` (checked, found none). A language
    that never stamps the field is abstained on, which preserves today's behaviour
    rather than inventing a refusal from a field the analyzer does not write.
    """

    #: Real apollo-server shape: JS/TS call edges carry no ``call_construct`` at all.
    _JS_EDGES: ClassVar[list[dict[str, object]]] = [
        {"src": "javascript:src/app.js:1-3:main:function",
         "dst": "javascript:fs:0-0:readFileSync:external_symbol", "type": "calls"},
        {"src": "javascript:src/app.js:1-3:main:function",
         "dst": "javascript:console:0-0:log:external_symbol", "type": "calls"},
    ]

    def test_javascript_is_abstained_on_not_reported_blind(self) -> None:
        starved = method_starved_modules(self._JS_EDGES, _catalogs("javascript"))
        assert starved == [], (
            "JS stamps call_construct on zero edges, so this check has no signal "
            "for it and must not manufacture one"
        )

    def test_the_abstention_is_per_language_not_global(self) -> None:
        """A JS blind spot must not suppress the Kotlin catch in the same map.

        Without this, one un-stamped language anywhere in a polyglot repo would
        silently disable the gate for every other language in it.
        """
        starved = method_starved_modules(
            self._JS_EDGES + _KT_BLIND_EDGES, _catalogs("javascript", "kotlin"),
        )
        assert starved == ["java.io.File", "java.net.Socket"]

    def test_a_stamping_language_is_still_checked(self) -> None:
        """The permitting case for the abstention: evidence present, check runs."""
        edges = [
            {"src": "kotlin:a.kt:1-2:f:function",
             "dst": "kotlin:java.io.File:0-0:File:external_symbol", "type": "calls"},
            {"src": "kotlin:a.kt:1-2:f:function",
             "dst": "kotlin:src/a.kt:9-9:g:function", "type": "calls",
             "meta": {"call_construct": "function"}},
        ]
        assert method_starved_modules(edges, _catalogs("kotlin")) == ["java.io.File"]


class TestTheCoverageGateConsumesIt:
    """One predicate, one consumer — the boundary and taint gates share it."""

    def test_starved_module_makes_coverage_incomplete(self) -> None:
        coverage = compute_boundary_coverage(
            _KT_BLIND_EDGES, {"kotlin"}, _catalogs("kotlin"),
        )
        assert coverage.complete is False
        assert "java.io.File" in coverage.reason

    def test_healthy_language_keeps_complete_coverage(self) -> None:
        """REGRESSION GUARD. A coverage gate can only make verdicts worse, so the
        clean case has to be pinned in the same module as the catch."""
        coverage = compute_boundary_coverage(
            _PY_HEALTHY_EDGES, {"python"}, _catalogs("python"),
        )
        assert coverage.complete is True, coverage.reason

    @pytest.mark.parametrize("edges", [_PY_PURE_EDGES, _PY_HEALTHY_EDGES])
    def test_python_is_never_reported_blind_by_this_check(self, edges) -> None:
        assert method_starved_modules(edges, _catalogs("python")) == []
