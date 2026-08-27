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


class TestAModuleCarryingBothCallKinds:
    """INV-soval: the predicate is MODULE-granular but tests a PER-KIND property.

    A catalogue entry declares its own call shape, and a MODULE can legitimately
    declare both — ``std::fs::File`` has associated functions (``open``,
    ``create``) and methods (``metadata``, ``sync_all``, the ``lock`` family).
    ``method_modules`` collects every module carrying ANY method-kind primitive
    and marks a module satisfied ONLY on a ``call_construct == "method"`` edge,
    so a mixed module can never be satisfied by a function-construct call — even
    when the catalogue matched that call exactly.

    THE CONSEQUENCE IS THAT CORRECT CATALOGUING CAUSES WITHHOLDING, which is the
    opposite of what the stdlib audit campaign is for. Measured on the shipped
    catalogue: ``std::fs::File`` is function-only today and starves nothing;
    adding ONE correct method row makes a repo that merely opens a file starve.
    The incentive that creates is to UNDER-catalogue.

    The predicate's own docstring states the intent the code does not implement:
    *"the catalogue was never given anything it could match"*. For a mixed
    module it WAS given something it could match.
    """

    @staticmethod
    def _mixed_catalog():
        from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

        return IoBoundaryCatalog(
            language="toy",
            primitives=[
                # An ASSOCIATED function: no receiver exists at the call site.
                IoPrimitive(boundary="fs_read", module="toy.File",
                            name="open", kind="function"),
                # A genuine METHOD on the value that function returns.
                IoPrimitive(boundary="fs_read", module="toy.File",
                            name="metadata", kind="method"),
            ],
        )

    @staticmethod
    def _method_only_catalog():
        from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

        return IoBoundaryCatalog(
            language="toy",
            primitives=[
                IoPrimitive(boundary="fs_read", module="toy.File",
                            name="metadata", kind="method"),
            ],
        )

    @staticmethod
    def _call(construct: str, name: str = "open"):
        return [{
            "src": "toy:src/a.toy:1-9:load:function",
            "dst": f"toy:toy.File:0-0:{name}:external_symbol",
            "type": "calls",
            "meta": {"call_construct": construct},
        }]

    def test_function_call_into_a_mixed_module_does_not_starve(self) -> None:
        """THE DEFECT. The catalogue declares ``open`` as a function and the
        analyzer produced a function-construct edge, so the catalogue was handed
        exactly what it needs. Reporting the module as structurally invisible is
        false."""
        assert method_starved_modules(
            self._call("function"), {"toy": self._mixed_catalog()},
        ) == []

    def test_method_call_into_a_mixed_module_does_not_starve(self) -> None:
        assert method_starved_modules(
            self._call("method", name="metadata"),
            {"toy": self._mixed_catalog()},
        ) == []

    def test_function_call_into_a_METHOD_ONLY_module_still_starves(self) -> None:
        """THE CONTROL, AND THE POINT OF THE WHOLE PREDICATE.

        This is the INV-nular / blind-Kotlin shape: every catalogued name needs a
        receiver, and the analyzer produced none. The fix must NOT blunt it —
        loosening this gate is the false-all-clear direction, so the signal it
        was built for has to survive intact.
        """
        assert method_starved_modules(
            self._call("function"), {"toy": self._method_only_catalog()},
        ) == ["toy.File"]

    def test_shipped_rust_modules_split_by_pr_552_no_longer_starve(self) -> None:
        """The three real instances, created by the INV-nular fix itself.

        Splitting the miskinded associated functions out of ``methods:`` fixed
        the MATCH and left the COVERAGE broken: the modules became mixed, so a
        repo calling ``TcpStream::connect`` and nothing else still starved.
        """
        catalogs = _catalogs("rust")
        for module, fn in (
            ("std::net::TcpStream", "connect"),
            ("std::net::UdpSocket", "bind"),
            ("std::process::Command", "new"),
        ):
            edges = [{
                "src": "rust:src/lib.rs:1-9:f:function",
                "dst": f"rust:{module}:0-0:{fn}:external_symbol",
                "type": "calls",
                "meta": {"call_construct": "function"},
            }]
            assert method_starved_modules(edges, catalogs) == [], (
                f"{module}::{fn} is catalogued as a function and was called as "
                f"one; the module must not be reported structurally invisible"
            )

    def test_kotlin_blind_signal_is_untouched_by_the_loosening(self) -> None:
        """The gate this fix loosens must still catch what it was built for.

        Neither the kotlin nor the java catalogue declares a single mixed-kind
        module, so the blind-language population is entirely method-only and
        this change cannot reach it. Asserted rather than assumed, because the
        fix would be unsafe if that ever stopped being true.
        """
        from collections import defaultdict

        for lang in ("kotlin", "java"):
            kinds: dict[str, set[str]] = defaultdict(set)
            for prim in load_catalog(lang).primitives:
                kinds[prim.module].add(prim.kind)
            mixed = [m for m, k in kinds.items() if {"method", "function"} <= k]
            assert mixed == [], (
                f"{lang} now has mixed-kind module(s) {mixed}; the blind-language "
                f"signal may no longer be method-only and this loosening needs "
                f"re-measuring against it"
            )
        assert method_starved_modules(_KT_BLIND_EDGES, _catalogs("kotlin")) == [
            "java.io.File", "java.net.Socket",
        ]


class TestAnUnstampedAssociatedFunctionCall:
    """INV-soval's SECOND route, and the shape that forced it to exist.

    The construct test above cannot see the case the whole fix was measured on.
    ``call_construct`` IS NOT STAMPED ON AN ASSOCIATED-FUNCTION CALL TO AN
    EXTERNAL TYPE: across three real Rust repos every edge into
    ``std::fs::File`` / ``std::process::Command`` / ``std::path::Path`` carries
    ``call_construct: None``, while the same repos stamp it on thousands of
    other edges (ripgrep: 917 ``method`` and 12 ``function`` among 1,156
    external call edges). So the language clears the abstention check on its
    OTHER edges and then arrives here with nothing to test.

    THE CLASS ABOVE STAMPS EVERY EDGE, so it never executes this route at all —
    the coverage gate is what surfaced that, not the assertions. What follows
    exercises the name route AND BOUNDS IT: loosening a withholding gate is the
    false-all-clear direction, so each satisfying case is paired with a case
    that must still starve.
    """

    #: One stamped edge, into a module nobody catalogues, purely so the language
    #: clears the ``languages_with_construct_evidence`` abstention. This mirrors
    #: reality — Rust stamps thousands of edges and merely misses these.
    _EVIDENCE: ClassVar[dict] = {
        "src": "rust:src/lib.rs:1-9:f:function",
        "dst": "rust:std::collections::HashMap:0-0:insert:external_symbol",
        "type": "calls",
        "meta": {"call_construct": "method"},
    }

    @staticmethod
    def _unstamped(module: str, name: str) -> dict:
        """An external call edge with NO ``call_construct`` at all.

        Written with no ``meta`` key whatsoever rather than ``{"meta": {}}``,
        because that is what the analyzer really emits for this shape.
        """
        return {
            "src": "rust:src/lib.rs:1-9:f:function",
            "dst": f"rust:{module}:0-0:{name}:external_symbol",
            "type": "calls",
        }

    def _starved(self, module: str, name: str) -> list:
        return method_starved_modules(
            [self._EVIDENCE, self._unstamped(module, name)], _catalogs("rust"),
        )

    def test_unstamped_call_to_a_catalogued_associated_function_does_not_starve(
        self,
    ) -> None:
        """THE ROUTE. ``Command::new`` is catalogued as a function-kind
        primitive and the repo called it by that exact name. The catalogue can
        match it, so naming the module structurally invisible is false — even
        though not one method-construct edge landed in it.
        """
        assert self._starved("std::process::Command", "new") == []

    def test_the_name_route_covers_every_mixed_module_pr_552_created(self) -> None:
        """All four, not just the one convenient example."""
        for module, fn in (
            ("std::net::TcpStream", "connect"),
            ("std::net::UdpSocket", "bind"),
            ("std::net::TcpListener", "bind"),
            ("std::process::Command", "new"),
        ):
            assert self._starved(module, fn) == [], (
                f"{module}::{fn} is a catalogued associated function called by "
                f"name on an unstamped edge; it must not be reported invisible"
            )

    def test_an_unstamped_call_to_an_UNCATALOGUED_name_still_starves(self) -> None:
        """THE BOUND. ``Command::arg`` is not in the catalogue in any kind, so
        an unstamped edge into it is exactly what starvation means: the module
        is called, the catalogue covers it with methods, and nothing arrived
        that the catalogue could match. The loosening must not reach here.
        """
        assert self._starved("std::process::Command", "arg") == [
            "std::process::Command"
        ]

    def test_a_METHOD_name_on_an_unstamped_edge_still_starves(self) -> None:
        """THE ASYMMETRY, ASSERTED RATHER THAN ASSUMED.

        ``spawn`` IS catalogued on this module — as a METHOD. The name index is
        built from function-kind rows ONLY, so it cannot rescue this edge, and
        that is deliberate: an unstamped edge naming a method is precisely the
        blind-analyzer shape (a receiver existed and the analyzer could not see
        it). Were the index built from all kinds, this case would go silently
        clean.
        """
        assert self._starved("std::process::Command", "spawn") == [
            "std::process::Command"
        ]

    def test_the_real_Path_defect_is_NOT_papered_over_by_this_fix(self) -> None:
        """``std::path::Path`` starves in 3 of 3 real Rust repos and MUST STILL.

        It is method-only in the shipped catalogue, so it has no function-kind
        names for the second route to match — ``Path::new`` performs no I/O and
        is correctly absent. Its starvation is a genuine, separate defect
        (INV-linub: 92.7% of Rust method edges carry slot ``external``, so the
        receiver type is unresolved). INV-soval must not make it disappear by
        accident, because that would convert a filed L3 defect into silence.
        """
        assert self._starved("std::path::Path", "new") == ["std::path::Path"]
        assert self._starved("std::path::Path", "metadata") == ["std::path::Path"]

    def test_an_unstamped_edge_alone_cannot_wake_an_abstaining_language(self) -> None:
        """The abstention outranks both routes. With no stamped edge anywhere,
        the language carries no construct evidence and is skipped entirely — so
        this fix cannot start reporting JavaScript or TypeScript repos.
        """
        assert method_starved_modules(
            [self._unstamped("std::process::Command", "arg")], _catalogs("rust"),
        ) == []
