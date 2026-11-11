# SPDX-License-Identifier: AGPL-3.0-or-later
"""A language whose catalogued I/O is method-shaped and that emits no method calls.

WHAT THIS PINS. "Did the language produce ANY call edge?" is not a coverage test:
one edge is enough to look covered. A Kotlin repo that reads a socket and writes the
bytes to disk, under an analyzer that emits only the constructor calls, would get
zero findings and a ``confirmed`` verdict at rc 0. That is the worst failure this
tool has: it claims support, looks at nothing, and reports silence as safety.

THE PREDICATE, AND WHY IT IS THIS ONE. For each external module a repo actually
CALLS that the catalogue covers with METHOD-kind primitives, did any method-construct
call edge land in it? ``kt_blind`` calls ``java.io.File`` exactly once — the
CONSTRUCTOR — while ``writeText`` produces no edge at all (the WI-nasuf shape).
``py_pure`` calls no catalogued module, so it raises no expectation. The simpler
predicates (compared over six fixtures with
``scripts/measure-blind-language-signal.py``) fail:

* "did the language emit ANY call edge" passes ``kt_blind``.
* "did the language emit any method-construct edge" catches ``kt_blind`` but also
  downgrades ``py_pure`` — a pure-computation repo making no external calls at all.
  Conflating "the analyzer cannot see this language's I/O" with "this repo calls
  nothing external" is a blanket downgrade that makes ``confirmed`` unreachable.

CALL EDGES ONLY, NOT IMPORTS. ``import pathlib`` performs no I/O, so an unused import
must not starve. The same reasoning is recorded above
``_STARVATION_CALL_EDGE_TYPES`` in :mod:`hypergumbo_core.verify_claims`, and it is
load-bearing here: it is what keeps "imported but never called" out of the blind
set. The Kotlin case is
caught anyway because its evidence is a CALL edge (the constructor), not the import.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_core.verify_claims import (
    compute_boundary_coverage,
    method_starved_modules,
)

#: The Kotlin map below is what a method-blind analyzer emits for a file that
#: constructs ``java.io.File`` and calls ``.writeText`` on it: the constructor call
#: is emitted, the method call is not (the WI-nasuf shape).
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
        """THE OVER-DOWNGRADE GUARD, and the reason the predicate is per called
        module rather than per language.

        py_pure genuinely calls nothing external. Reporting it blind would say the
        analyzer cannot see Python I/O, which is false, and a gate that did so
        would make ``confirmed`` unreachable.
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
    """THE ABSTENTION, and the false positive it prevents.

    ``call_construct`` is not populated by every analyzer. For a language whose
    call edges carry none, "no method call landed in ``fs``" is absence of
    evidence, not evidence of blindness — and without the abstention ``fs``,
    ``console`` and ``process`` would read starved on every repo in that
    language.

    ``None`` (could not check) is not ``empty`` (checked, found none). A language
    that stamps the field on no edge in the run is abstained on, rather than
    inventing a refusal from a field the analyzer does not write.
    """

    #: JS call edges carrying no ``call_construct`` at all -- an unstamped language.
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
    ``method_modules`` collects every module carrying ANY method-kind primitive,
    so satisfaction must be tested per KIND: a function-construct call into a
    mixed module is one the catalogue can match exactly.

    A predicate keyed on ``method`` alone would make CORRECT CATALOGUING CAUSE
    WITHHOLDING: adding one correct method row to a function-only module would
    make a repo that merely opens a file starve, and the incentive that creates
    is to UNDER-catalogue. The question is whether the catalogue was handed
    anything it could match, and for a mixed module a function-construct call
    IS something it can match.
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
        """THE MIXED CASE. The catalogue declares ``open`` as a function and the
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
        receiver, and the analyzer produced none. The per-kind test must NOT
        blunt it — loosening this gate is the false-all-clear direction, so the
        signal it was built for has to survive intact.
        """
        assert method_starved_modules(
            self._call("function"), {"toy": self._method_only_catalog()},
        ) == ["toy.File"]

    def test_shipped_rust_modules_split_by_pr_552_no_longer_starve(self) -> None:
        """Real mixed modules in the shipped Rust catalogue.

        Their associated functions are keyed as functions and their methods as
        methods (INV-nular, PR #552), so a repo calling ``TcpStream::connect``
        and nothing else must not starve.
        """
        catalogs = _catalogs("rust")
        # ``std::net::UdpSocket::bind`` is deliberately absent: net_recv is an
        # auto-derived taint SOURCE and binding a socket receives nothing, so
        # ``bind`` is not catalogued (INV-nular). It belongs to the population
        # ``test_an_unstamped_call_to_an_UNCATALOGUED_name_still_starves``
        # governs, where starvation is the correct answer. Pinned below.
        for module, fn in (
            ("std::net::TcpStream", "connect"),
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
        """The per-kind loosening must leave the blind-language catch intact.

        Neither the kotlin nor the java catalogue declares a single mixed-kind
        module, so the blind-language population is entirely method-only and
        the per-kind test cannot reach it. Asserted rather than assumed, because
        the loosening would be unsafe if that ever stopped being true.
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
    """The SECOND satisfaction route: a called name matching a function-kind row
    (INV-soval).

    The construct test above cannot see this case. ``call_construct`` IS NOT
    STAMPED ON AN ASSOCIATED-FUNCTION CALL TO AN EXTERNAL TYPE: in real Rust
    repos every edge into ``std::fs::File`` / ``std::process::Command`` /
    ``std::path::Path`` carries ``call_construct: None``, while the same repos
    stamp it on many other edges. So the language clears the abstention check
    on its OTHER edges and then arrives here with no construct to test.

    THE CLASS ABOVE STAMPS EVERY EDGE, so it never executes this route. What
    follows exercises the name route AND BOUNDS IT: loosening a withholding
    gate is the false-all-clear direction, so each satisfying case is paired
    with a case that must still starve.
    """

    #: One stamped edge, into a module nobody catalogues, purely so the language
    #: clears the ``languages_with_construct_evidence`` abstention. This mirrors
    #: reality — Rust stamps many edges and merely misses these.
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
        """The mixed modules that HAVE a catalogued associated function.

        ``UdpSocket::bind`` and ``TcpListener::bind`` are not catalogued, because
        they would be false ``net_recv`` sources (binding receives nothing;
        INV-nular), so the honest answer for them is starvation — asserted
        directly in
        :meth:`test_binding_a_socket_no_longer_vouches_for_its_receive_surface`.
        """
        for module, fn in (
            ("std::net::TcpStream", "connect"),
            ("std::process::Command", "new"),
        ):
            assert self._starved(module, fn) == [], (
                f"{module}::{fn} is a catalogued associated function called by "
                f"name on an unstamped edge; it must not be reported invisible"
            )

    def test_binding_a_socket_no_longer_vouches_for_its_receive_surface(
        self,
    ) -> None:
        """A ``bind`` call must not vouch for the module's receive surface
        (INV-nular; the INV-zubuh shape one level down).

        Were ``bind`` a catalogued function-kind row, a repo that called only
        ``UdpSocket::bind`` would satisfy the module, and the analysis would
        report that it had LOOKED at ``std::net::UdpSocket``. Matching ``bind``
        says nothing whatever about whether ``recv`` / ``recv_from`` were
        examined — one row would vouch for the rest of the module's surface,
        which is exactly what INV-zubuh forbids.

        So the module starves, and that is the honest answer: the repo called
        into ``UdpSocket``, the catalogue covers it with methods, and no method
        edge arrived. The direction is safe — starvation WITHHOLDS a verdict
        rather than granting one, so this trades a false all-clear for an
        explicit "not examined".
        """
        for module in ("std::net::UdpSocket", "std::net::TcpListener"):
            assert self._starved(module, "bind") == [module], (
                f"{module}::bind is uncatalogued after INV-nular, so a bind-only "
                f"repo must be told the module's receive surface was not examined"
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
        """``std::path::Path`` starves in real Rust repos and MUST STILL.

        It is method-only in the shipped catalogue, so it has no function-kind
        names for the second route to match — ``Path::new`` performs no I/O and
        is correctly absent. Its starvation is a genuine, separate defect
        (INV-linub: most Rust method edges carry slot ``external``, so the
        receiver type is unresolved). The name route must not make it disappear
        by accident, because that would convert a filed defect into silence.
        """
        assert self._starved("std::path::Path", "new") == ["std::path::Path"]
        assert self._starved("std::path::Path", "metadata") == ["std::path::Path"]

    def test_an_unstamped_edge_alone_cannot_wake_an_abstaining_language(self) -> None:
        """The abstention outranks both routes. With no stamped edge anywhere,
        the language carries no construct evidence and is skipped entirely — so
        the name route cannot start reporting a language that stamps nothing.
        """
        assert method_starved_modules(
            [self._unstamped("std::process::Command", "arg")], _catalogs("rust"),
        ) == []


class TestAClassmethodCalledThroughAModuleAlias:
    """A classmethod reached through a module alias must not starve (INV-fugus).

    ``import datetime as _dt; _dt.date.today()`` is a MODULE-ROOTED dotted
    chain, and the python analyzer emits it with no ``call_construct`` at all.
    Keyed as a method, ``datetime.date.today`` would give the module no
    function-kind name, neither route could satisfy it, and a call the
    catalogue CLASSIFIES (host_info_read) would be reported "structurally
    invisible" — a blocker that drops every claim from
    ``confirmed_with_caveats`` to ``inconclusive``.

    The answer is the row's kind (a classmethod takes no receiver, as with
    ``Path.cwd`` -- INV-nular), NOT a looser gate: an unstamped edge naming a
    METHOD row still starves, pinned in the class above.
    """

    SOURCE = "import datetime as _dt\n\n\ndef stamp():\n    return _dt.date.today()\n"

    def _edges(self, tmp_path) -> list[dict]:
        from hypergumbo_lang_mainstream.py import analyze_python

        (tmp_path / "m.py").write_text(self.SOURCE, encoding="utf-8")
        return [e.to_dict() for e in analyze_python(tmp_path).edges]

    def test_the_analyzer_really_emits_it_unstamped(self, tmp_path) -> None:
        """REACH. Without this, the assertion below could pass on any edge."""
        today = [
            e for e in self._edges(tmp_path)
            if e["dst"] == "python:datetime.date:0-0:today:unresolved"
        ]
        assert len(today) == 1
        assert "call_construct" not in (today[0].get("meta") or {})

    def test_it_does_not_starve_the_module(self, tmp_path) -> None:
        # The language must carry construct evidence or it is abstained on and
        # the assertion is vacuous; one stamped edge is what every real python
        # run has.
        edges = self._edges(tmp_path) + _PY_HEALTHY_EDGES
        assert method_starved_modules(edges, _catalogs("python")) == []

    def test_the_coverage_gate_stays_complete(self, tmp_path) -> None:
        edges = self._edges(tmp_path) + _PY_HEALTHY_EDGES
        coverage = compute_boundary_coverage(
            edges, {"python"}, _catalogs("python"),
        )
        assert coverage.complete is True, coverage.reason


class TestAMethodOnAModuleLevelObject:
    """The same shape on an OBJECT owner (INV-dihun).

    ``ctypes.cdll`` is a module-level ``LibraryLoader`` instance, so
    ``ctypes.cdll.LoadLibrary(p)`` is a module-rooted chain the analyzer emits
    with no ``call_construct`` -- the ``_dt.date.today()`` shape above. Keyed
    ``methods:``, the call would classify fs_read while the gate reported
    ``ctypes.cdll`` starved. The answer is again the row's kind (INV-zikab:
    ``module.name(...)`` is a valid call as written, so it is a function).
    """

    SOURCE = "import ctypes\n\n\ndef load(p):\n    return ctypes.cdll.LoadLibrary(p)\n"

    def _edges(self, tmp_path) -> list[dict]:
        from hypergumbo_lang_mainstream.py import analyze_python

        (tmp_path / "m.py").write_text(self.SOURCE, encoding="utf-8")
        return [e.to_dict() for e in analyze_python(tmp_path).edges]

    def _load_edge(self, tmp_path) -> dict:
        hits = [
            e for e in self._edges(tmp_path)
            if e["dst"].startswith("python:ctypes.cdll:0-0:LoadLibrary:")
        ]
        assert len(hits) == 1
        return hits[0]

    def test_the_analyzer_really_emits_it_unstamped(self, tmp_path) -> None:
        """REACH."""
        assert "call_construct" not in (self._load_edge(tmp_path).get("meta") or {})

    def test_it_classifies(self, tmp_path) -> None:
        edge = self._load_edge(tmp_path)
        prim = classify_call(_catalogs("python"), edge["dst"], edge.get("meta"))
        assert prim is not None and prim.boundary == "fs_read"

    def test_it_does_not_starve_the_module(self, tmp_path) -> None:
        edges = self._edges(tmp_path) + _PY_HEALTHY_EDGES
        assert method_starved_modules(edges, _catalogs("python")) == []


class TestAHandlerRegisteredByAssignment:
    """The construct is translated to a row kind, not compared to it (WI-dapap).

    ``ws.onmessage = h`` is emitted (WI-dosuh) as a call edge into the METHOD
    row ``WebSocket.onmessage`` with ``call_construct="assignment"``, and the
    classifier matches it (``net_recv``). ``call_construct`` and the row kind
    are two different axes: testing the construct STRING for membership in the
    module's KIND set would never match ``"assignment"`` against ``{"method"}``,
    and ``WebSocket`` would be reported structurally invisible in the same run
    that classified a call into it, dropping every generic claim on this
    fixture to ``inconclusive`` (WI-zohuk).

    ``sink.write("x")`` is an untyped method call. It is there so the language
    carries construct evidence WITHOUT the assignment edge, which keeps the
    abstention out of the picture: the assertion below is about route 1, not
    about whether JS is checked at all.
    """

    SOURCE = (
        "function start(u, sink) {\n"
        "  const ws = new WebSocket(u);\n"
        "  ws.onmessage = function (ev) { console.log(ev.data); };\n"
        "  sink.write(\"x\");\n"
        "}\n"
    )

    def _edges(self, tmp_path) -> list[dict]:
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(self.SOURCE, encoding="utf-8")
        return [e.to_dict() for e in analyze_javascript(tmp_path).edges]

    def _assignment_edge(self, edges: list[dict]) -> dict:
        hits = [e for e in edges if e["dst"].startswith("javascript:WebSocket:0-0:onmessage:")]
        assert len(hits) == 1
        return hits[0]

    def test_the_analyzer_really_emits_the_assignment_edge(self, tmp_path) -> None:
        """REACH, and the edge is the ONLY call into WebSocket, so nothing else
        can satisfy the module and the assertion below tests route 1."""
        edges = self._edges(tmp_path)
        edge = self._assignment_edge(edges)
        assert edge["meta"]["call_construct"] == "assignment"
        assert [e for e in edges if e["type"] == "calls"
                and e["dst"].startswith("javascript:WebSocket:")] == [edge]

    def test_it_classifies(self, tmp_path) -> None:
        edge = self._assignment_edge(self._edges(tmp_path))
        prim = classify_call(_catalogs("javascript"), edge["dst"], edge.get("meta"))
        assert prim is not None and prim.boundary == "net_recv"

    def test_it_does_not_starve_the_module(self, tmp_path) -> None:
        assert method_starved_modules(self._edges(tmp_path), _catalogs("javascript")) == []

    def test_the_coverage_gate_stays_complete(self, tmp_path) -> None:
        coverage = compute_boundary_coverage(
            self._edges(tmp_path), {"javascript"}, _catalogs("javascript"),
        )
        assert coverage.complete is True, coverage.reason


class TestTheConstructCrosswalkIsNotALoosening:
    """The crosswalk admits only constructs that reach a kind the module
    declares. The blind-language signal and an unknown value still starve."""

    _WS = "javascript:WebSocket:0-0:onmessage:unresolved"
    _SRC = "javascript:app.js:1-5:start:function"

    def _starved(self, construct) -> list[str]:
        edges = [
            {"src": self._SRC, "dst": self._WS, "type": "calls",
             "meta": {"call_construct": construct}},
            # Construct evidence for the language, into no catalogued module.
            {"src": self._SRC, "dst": "javascript:app.js:9-9:g:function",
             "type": "calls", "meta": {"call_construct": "function"}},
        ]
        return method_starved_modules(edges, _catalogs("javascript"))

    @pytest.mark.parametrize("construct", ["assignment", "method"])
    def test_a_construct_reaching_a_method_row_satisfies(self, construct) -> None:
        assert self._starved(construct) == []

    @pytest.mark.parametrize("construct", ["constructor", "macro_body", "bogus"])
    def test_a_construct_reaching_no_declared_kind_still_starves(self, construct) -> None:
        """``onmessage`` is a METHOD row, so route 2 (function-kind names) cannot
        rescue it either; the only way out is route 1, and these do not reach
        a method row."""
        assert self._starved(construct) == ["WebSocket"]

    def test_a_python_protocol_edge_satisfies_the_orm_module(self) -> None:
        """``for o in qs`` emits ``django.db.models.__iter__`` with construct
        ``protocol`` (WI-fasap). The dunder row is method-kind, so the edge
        reaches it through route 1; route 2 cannot rescue it."""
        edges = [
            {"src": "python:v.py:1-3:view:function",
             "dst": "python:django.db.models:0-0:__iter__:unresolved",
             "type": "calls", "meta": {"call_construct": "protocol"}},
        ]
        assert method_starved_modules(edges, _catalogs("python")) == []
