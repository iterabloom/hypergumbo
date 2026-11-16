# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry-driven parity gate: can INV-fibis's untyped-receiver disclosure
actually FIRE for this language?

WHY THIS FILE EXISTS. ``verify_claims.untyped_receiver_sites`` — the channel
that makes a clean boundary/taint verdict name the sink call sites whose
receiver it could not type — selects on ``meta.call_construct == "method"``.
A language whose analyzer never stamps that key reaches a clean ``confirmed``
and discloses NOTHING, and no test anywhere notices, because every analyzer
test asserts what it DOES emit rather than what a downstream safety gate needs.

WHAT IT ASSERTS. The edge for a method call on a receiver the analyzer cannot
type carries ``call_construct="method"``, in three receiver shapes:

- a plain identifier (``f.mkdirs()``) -- :data:`FIXTURES`;
- an EXPRESSION -- object creation, a call result, a cast, a chain
  (``new File("x").mkdirs()``, ``get().mkdirs()``, ``((File) o).mkdirs()``) --
  :data:`COMPLEX_RECEIVER_FIXTURES`. Deriving ``call_construct`` from the
  receiver's NAME misses every one of these, because the name is ``None`` for
  an expression: "what type can I look this receiver up under" and "is there a
  receiver at all" are two questions, and one variable cannot answer both
  (LIVE.md rule 7; history: INV-tutar, INV-pirot);
- an explicit ``this`` / ``self`` -- :data:`THIS_RECEIVER_FIXTURES`.

A fourth gate checks that the callee's name slot does not carry the receiver
identifier.

THE FIXTURE SHAPE IS LOAD-BEARING. A cast expression takes a different,
bare-call branch than a plain ``receiver.method()`` in the scala and swift
analyzers, so a fixture set built on one shape can report a whole language
inert when only that shape is. The plain arm uses the shape real code uses;
the expression arm varies the shape across languages.

THE GATE, not the stamps, is the point. It is driven from the io_primitives
catalogues rather than a hand-written list, so a language that ARRIVES LATER
with method-kind sinks and no fixture fails here instead of going silently
blind. LIVE.md rule 7: a predicate is inert until its call sites pass it, and
the site that RUNS may not be the one that READS it.

Each fixture calls a sink taken from that language's OWN catalogue, on a
receiver the analyzer cannot type. The fixtures are deliberately minimal: this
gate asserts the disclosure channel is REACHABLE, not per-sink recall.
"""
from __future__ import annotations

import glob
import importlib
import os
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog


def _catalogued_languages() -> set[str]:
    import hypergumbo_core.io_boundary as iob
    root = Path(iob.__file__).parent / "io_primitives"
    return {os.path.basename(p)[:-5] for p in glob.glob(str(root / "*.yaml"))}


def _has_method_sinks(lang: str) -> bool:
    return any(
        getattr(p, "kind", None) == "method"
        for p in load_catalog(lang).primitives
    )


#: language -> (analyzer module, entry function, fixture filename, source).
#: The call in each is an INSTANCE-METHOD call on a receiver whose type the
#: analyzer cannot resolve, naming a method the language's catalogue declares
#: as a sink.
FIXTURES: dict[str, tuple[str, str, str, str]] = {
    "java": (
        "hypergumbo_lang_mainstream.java", "analyze_java", "Main.java",
        "public class Main { void leak(Untyped u) { u.mkdirs(); } }\n",
    ),
    "scala": (
        "hypergumbo_lang_mainstream.scala", "analyze_scala", "Main.scala",
        "object Main { def leak(f: Untyped): Unit = { f.createNewFile() } }\n",
    ),
    "swift": (
        "hypergumbo_lang_mainstream.swift", "analyze_swift", "main.swift",
        'func leak(fm: Untyped) '
        '{ fm.createFile(atPath: "x", contents: nil) }\n',
    ),
    "objc": (
        "hypergumbo_lang_mainstream.objc", "analyze_objc", "main.m",
        "@implementation Foo\n"
        "- (void)leak:(id)fm { [fm createFileAtPath:@\"x\" "
        "contents:nil attributes:nil]; }\n"
        "@end\n",
    ),
    "rust": (
        "hypergumbo_lang_mainstream.rust", "analyze_rust", "src/lib.rs",
        "use std::io::Write;\n"
        "pub fn leak<W: Write>(w: &mut W, x: &[u8]) -> std::io::Result<()> "
        "{ w.write_all(x) }\n",
    ),
    "python": (
        "hypergumbo_lang_mainstream.py", "analyze_python", "m.py",
        'def leak(p):\n    return p.open("w")\n',
    ),
    "go": (
        "hypergumbo_lang_mainstream.go", "analyze_go", "main.go",
        "package main\n\nfunc leak(c Conn) { c.Write([]byte(\"x\")) }\n",
    ),
}

#: Languages exempt from the plain-receiver fixtures on the grounds that their
#: analyzer emits NO external call edge for an instance method call, so there
#: is nothing to stamp; the declared-blindness path
#: (``analyzer_disclosure.DECLARATIONS``) is what qualifies a verdict for such
#: a language. Listed here so the set is visible and BOUNDED — see
#: :func:`test_the_no_edge_exemption_set_is_exactly_the_measured_two`.
#: STALE: ``analyzer_disclosure.DECLARATIONS`` declares both kotlin and
#: javascript as emitting this edge (WI-nasuf), so the grounds no longer hold
#: and neither language is covered by a fixture here.
NO_EXTERNAL_CALL_EDGE: frozenset[str] = frozenset({"kotlin", "javascript"})


def test_every_language_with_method_sinks_is_covered_here() -> None:
    """A language that arrives later with method-kind sinks and no fixture
    FAILS HERE rather than reaching a silent clean verdict in production.

    Driven from the shipped catalogues, so the list cannot drift out of date
    the way a hand-maintained one does.
    """
    with_method_sinks = {
        lang for lang in _catalogued_languages() if _has_method_sinks(lang)
    }
    uncovered = with_method_sinks - set(FIXTURES) - NO_EXTERNAL_CALL_EDGE
    assert not uncovered, (
        f"languages declaring method-kind I/O sinks with no disclosure "
        f"fixture: {sorted(uncovered)}. Add a fixture with an untypable "
        f"receiver calling one of that language's catalogued method sinks, "
        f"or — if its analyzer emits no external call edge at all — add it to "
        f"NO_EXTERNAL_CALL_EDGE and give it a declared-blindness entry."
    )


def test_the_no_edge_exemption_set_is_exactly_the_measured_two() -> None:
    """The exemption is a MEASUREMENT, not a place to put inconvenient
    languages. Growing it requires re-running the parity probe and saying so.
    """
    assert NO_EXTERNAL_CALL_EDGE == frozenset({"kotlin", "javascript"})


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_untyped_receiver_method_call_carries_call_construct(
    lang: str, tmp_path: Path,
) -> None:
    """The edge for an untypable receiver's method call must carry
    ``call_construct="method"``, because that is the key
    ``untyped_receiver_sites`` selects on.

    It costs nothing on the RECALL side and the gate it feeds proves why:
    ``io_boundary.gate_named_entry`` refuses a method-kind hit with no module
    hint either way — via the ``call_construct == "method"`` arm WITH the
    stamp, and via the ``non_method`` filter WITHOUT it. What the stamp buys
    is that the verdict can SAY SO.
    """
    module, entry, filename, source = FIXTURES[lang]
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    if lang == "rust":
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "p"\nversion = "0.1.0"\nedition = "2021"\n'
        )

    analyze = getattr(importlib.import_module(module), entry)
    result = analyze(tmp_path)

    external = [
        e for e in result.edges
        if e.edge_type in ("calls", "instantiates")
        and e.dst.split(":")[-1] in ("external_symbol", "unresolved")
    ]
    assert external, (
        f"{lang}: no external call edge emitted at all — this language belongs "
        f"in NO_EXTERNAL_CALL_EDGE, not in FIXTURES"
    )
    stamped = [
        e for e in external if (e.meta or {}).get("call_construct") == "method"
    ]
    assert stamped, (
        f"{lang}: emitted {len(external)} external call edge(s) but none "
        f"carries call_construct='method', so INV-fibis's untyped-receiver "
        f"disclosure is inert for this language. "
        f"metas: {[e.meta for e in external]}"
    )


#: The SECOND arm of the same rule: the receiver is an EXPRESSION rather than a
#: bare identifier. Same languages, same catalogued sinks, same assertion — the
#: only thing that changes is the shape of the receiver (INV-pirot).
#:
#: Shapes are deliberately varied across languages (object creation, cast, call
#: result) rather than uniform: a uniform fixture set exercises one analyzer
#: branch and can misreport a whole language from it (LIVE.md rule 12).
COMPLEX_RECEIVER_FIXTURES: dict[str, tuple[str, str, str, str]] = {
    "java": (
        "hypergumbo_lang_mainstream.java", "analyze_java", "Main.java",
        'public class Main { void leak() { new Untyped("x").mkdirs(); } }\n',
    ),
    "scala": (
        "hypergumbo_lang_mainstream.scala", "analyze_scala", "Main.scala",
        'object Main { def leak(): Unit = '
        '{ new Untyped("x").createNewFile() } }\n',
    ),
    "swift": (
        "hypergumbo_lang_mainstream.swift", "analyze_swift", "main.swift",
        'func leak(o: Any) '
        '{ (o as! Untyped).createFile(atPath: "x", contents: nil) }\n',
    ),
    "objc": (
        "hypergumbo_lang_mainstream.objc", "analyze_objc", "main.m",
        "@implementation Foo\n"
        "- (void)leak { [[Untyped alloc] createFileAtPath:@\"x\" "
        "contents:nil attributes:nil]; }\n"
        "@end\n",
    ),
    "rust": (
        "hypergumbo_lang_mainstream.rust", "analyze_rust", "src/lib.rs",
        "use std::io::Write;\n"
        "fn make() -> Box<dyn Write> { unimplemented!() }\n"
        "pub fn leak(x: &[u8]) { let _ = make().write_all(x); }\n",
    ),
    "go": (
        "hypergumbo_lang_mainstream.go", "analyze_go", "main.go",
        "package main\n\nfunc make2() Conn { panic(\"x\") }\n\n"
        "func leak() { make2().Write([]byte(\"x\")) }\n",
    ),
}

#: Languages that emit NO external call edge for a COMPLEX-receiver method call,
#: so there is nothing to stamp. python emits an edge for the ``make()`` call
#: and none for the ``.open("w")`` on its result: an EMISSION gap, not a stamp
#: gap (WI-makij, chained method calls emit no call edge).
COMPLEX_RECEIVER_NO_EDGE: frozenset[str] = frozenset({"python"})


def test_every_parity_language_has_a_complex_receiver_fixture() -> None:
    """The two arms cover the same languages, or the difference is DECLARED.

    Without this the complex-receiver arm could quietly cover three languages
    while the plain arm covers seven, and the gap would read as "clean".
    """
    missing = (
        set(FIXTURES)
        - set(COMPLEX_RECEIVER_FIXTURES)
        - COMPLEX_RECEIVER_NO_EDGE
    )
    assert not missing, (
        f"languages with a plain-receiver fixture and no complex-receiver "
        f"fixture: {sorted(missing)}. Add one with an object-creation, cast or "
        f"call-result receiver, or — if the analyzer emits no external call "
        f"edge for that shape at all — add it to COMPLEX_RECEIVER_NO_EDGE with "
        f"the work item that tracks the emission gap."
    )


def test_the_complex_receiver_no_edge_set_is_exactly_the_measured_one() -> None:
    """Same discipline as :data:`NO_EXTERNAL_CALL_EDGE`: the exemption is a
    MEASUREMENT, not a place to put inconvenient languages."""
    assert COMPLEX_RECEIVER_NO_EDGE == frozenset({"python"})


@pytest.mark.parametrize("lang", sorted(COMPLEX_RECEIVER_FIXTURES))
def test_complex_receiver_method_call_carries_call_construct(
    lang: str, tmp_path: Path,
) -> None:
    """A receiver the analyzer cannot NAME is still a receiver (INV-pirot).

    THE FAIL-OPEN DIRECTION, which is why this arm is not merely a disclosure
    nicety. ``taint._register_sanitizer_callers`` refuses to bind an unresolved
    bare-name sanitizer match ONLY when ``call_construct == "method"``. With the
    key absent the edge reaches the permit branch and registers a PHANTOM
    BARRIER — and a barrier earns ``sanitized``, which DROPS the flow from the
    claim's violation set. A missing stamp therefore DELETES
    findings, so the shape that misses it is a security defect and not a
    cosmetic one.
    """
    module, entry, filename, source = COMPLEX_RECEIVER_FIXTURES[lang]
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    if lang == "rust":
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "p"\nversion = "0.1.0"\nedition = "2021"\n'
        )

    analyze = getattr(importlib.import_module(module), entry)
    result = analyze(tmp_path)

    external = [
        e for e in result.edges
        if e.edge_type in ("calls", "instantiates")
        and e.dst.split(":")[-1] in ("external_symbol", "unresolved")
    ]
    assert external, (
        f"{lang}: no external call edge for a complex-receiver method call — "
        f"this language belongs in COMPLEX_RECEIVER_NO_EDGE, not in "
        f"COMPLEX_RECEIVER_FIXTURES"
    )
    stamped = [
        e for e in external if (e.meta or {}).get("call_construct") == "method"
    ]
    assert stamped, (
        f"{lang}: emitted {len(external)} external call edge(s) for a receiver "
        f"EXPRESSION and none carries call_construct='method'. The sanitizer "
        f"method-guard is therefore inert for this shape and a bare short name "
        f"can bind a catalogued barrier (INV-pirot). "
        f"metas: {[e.meta for e in external]}"
    )


#: The THIRD shape: an EXPLICIT ``this`` / ``self`` receiver on a call the analyzer cannot resolve, because the method
#: is inherited from a supertype outside the repository. The receiver is named
#: but the DECLARING TYPE is not, so the catalogue cannot be asked what the call
#: does — which is precisely what ``call_construct`` exists to record.
#:
#: Every language with a plain-receiver fixture must stamp it; this arm is
#: parity with the plain shape, not a new convention (history: WI-sajis; java
#: was the language that left it bare).
#:
#: THE IMPLICIT form (a bare ``doFinal(p)`` with no receiver token) is
#: DELIBERATELY NOT ASSERTED here and is not a bug in this key. A bare call is
#: not syntactically a method call, and ``call_construct`` names the syntactic
#: construct (ADR-0024 / audit-findings 0012); stamping it "method" would put a
#: resolution fact under a construct name, which is the leak the concept audits
#: exist to prevent. It IS a live phantom-barrier surface in java, which has no
#: free functions, and it is filed on its own terms.
THIS_RECEIVER_FIXTURES: dict[str, tuple[str, str, str, str]] = {
    "java": (
        "hypergumbo_lang_mainstream.java", "analyze_java", "Main.java",
        "public class Main extends Base "
        "{ void run(byte[] p) { this.doFinal(p); } }\n",
    ),
    "scala": (
        "hypergumbo_lang_mainstream.scala", "analyze_scala", "Main.scala",
        "class Main extends Base "
        "{ def run(p: Array[Byte]): Unit = { this.doFinal(p) } }\n",
    ),
    "swift": (
        "hypergumbo_lang_mainstream.swift", "analyze_swift", "main.swift",
        "class Main: Base { func run(p: [UInt8]) { self.doFinal(p) } }\n",
    ),
    "python": (
        "hypergumbo_lang_mainstream.py", "analyze_python", "m.py",
        "class Main(Base):\n    def run(self, p):\n        self.encrypt(p)\n",
    ),
    "objc": (
        "hypergumbo_lang_mainstream.objc", "analyze_objc", "main.m",
        "@implementation Main\n- (void)run:(NSData *)p { [self doFinal:p]; }\n"
        "@end\n",
    ),
    "rust": (
        "hypergumbo_lang_mainstream.rust", "analyze_rust", "src/lib.rs",
        "struct Main;\nimpl Main "
        "{ fn run(&self, p: &[u8]) { self.do_final(p); } }\n",
    ),
    "go": (
        "hypergumbo_lang_mainstream.go", "analyze_go", "main.go",
        "package main\n\ntype Main struct{}\n\n"
        "func (m *Main) run(p []byte) { m.doFinal(p) }\n",
    ),
}


def test_every_parity_language_has_a_this_receiver_fixture() -> None:
    """No exemption set for this arm, and that is the finding: every language
    with a plain-receiver fixture emits an edge for an explicit ``this``."""
    assert set(THIS_RECEIVER_FIXTURES) == set(FIXTURES)


@pytest.mark.parametrize("lang", sorted(THIS_RECEIVER_FIXTURES))
def test_explicit_this_receiver_carries_call_construct(
    lang: str, tmp_path: Path,
) -> None:
    """``this.m()`` that does not resolve names an EXTERNAL declaring type.

    The receiver is spelled, so the analyzer knows *which object*; it does not
    know *which type declares the method*, because the method is not on the
    enclosing class and the supertype is outside the repository. That is a want
    of receiver evidence in exactly the sense the two taint gates read the key
    for, and leaving it bare is what lets a bare short name bind a catalogued
    sanitizer as a phantom barrier — shown, with a control, in
    ``test_this_receiver_phantom_barrier.py`` in hypergumbo-core.

    THE RECALL COST is bounded: ``gate_named_entry`` returns ``None``
    immediately for ``call_construct == "method"``, so the stamp can only
    refuse a FUNCTION-kind catalogue hit, and an explicit ``this.m()`` is not a
    call of a free function. scala and swift, whose catalogues declare
    function-kind entries, stamp this shape too and serve as controls.
    """
    module, entry, filename, source = THIS_RECEIVER_FIXTURES[lang]
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    if lang == "rust":
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "p"\nversion = "0.1.0"\nedition = "2021"\n'
        )

    analyze = getattr(importlib.import_module(module), entry)
    result = analyze(tmp_path)

    external = [
        e for e in result.edges
        if e.edge_type in ("calls", "instantiates")
        and e.dst.split(":")[-1] in ("external_symbol", "unresolved")
    ]
    assert external, f"{lang}: no external call edge for an explicit this/self"
    stamped = [
        e for e in external if (e.meta or {}).get("call_construct") == "method"
    ]
    assert stamped, (
        f"{lang}: emitted {len(external)} external call edge(s) for an EXPLICIT "
        f"this/self receiver and none carries call_construct='method'. The "
        f"declaring type is external and unknown, so the sanitizer guard has "
        f"nothing to refuse on (INV-pirot). metas: {[e.meta for e in external]}"
    )


#: The receiver IDENTIFIER in each :data:`FIXTURES` source, so the parity gate
#: below can name what must NOT appear in the callee's name slot. Kept beside
#: the fixtures rather than parsed out of them: a regex over java source is a
#: second thing to get wrong, and a fixture detail nobody wrote down is how a
#: parity gate misreports a language.
FIXTURE_RECEIVERS: dict[str, str] = {
    "java": "u",
    "scala": "f",
    "swift": "fm",
    "objc": "fm",
    "rust": "w",
    "python": "p",
    "go": "c",
}


def test_every_parity_language_declares_its_receiver_identifier() -> None:
    """A fixture added later must declare its receiver or the gate below is
    silently inert for it."""
    assert set(FIXTURE_RECEIVERS) == set(FIXTURES), (
        f"FIXTURE_RECEIVERS and FIXTURES disagree: "
        f"{set(FIXTURE_RECEIVERS) ^ set(FIXTURES)}"
    )


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_the_name_slot_names_the_callee_not_the_receiver(
    lang: str, tmp_path: Path,
) -> None:
    """The unresolved dst's name slot must not carry the receiver identifier.

    THE THIRD ARM OF THE SAME RULE. The two arms above
    ask whether the disclosure can FIRE for a language; this asks whether what
    it fires with is a name any catalogue has keyed. Both boundary-scoped
    consumers — ``verify_claims.untyped_receiver_sites`` and
    ``untyped_receiver_sink_zones`` — match the callee name against catalogue
    rows keyed WITHOUT a receiver, so a language that glues one on reaches a
    clean verdict, counts the sites, and can never say which boundary they are
    catalogued for.

    The gate exists so no analyzer, including one that arrives later, can glue
    a receiver on silently (history: WI-nakut, where java emitted
    ``u.write``).

    ASSERTED AS "DOES NOT START WITH ``<receiver>.``" RATHER THAN "CONTAINS NO
    DOT", because a dot in the name slot is not by itself wrong: ADR-0036
    sanitizes ``:`` to ``.``, so an Objective-C selector legitimately renders as
    ``writeToFile.atomically.`` and a blanket dot ban would fail objc for being
    correct.
    """
    module, entry, filename, source = FIXTURES[lang]
    receiver = FIXTURE_RECEIVERS[lang]
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    if lang == "rust":
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "p"\nversion = "0.1.0"\nedition = "2021"\n'
        )

    analyze = getattr(importlib.import_module(module), entry)
    result = analyze(tmp_path)

    external = [
        e for e in result.edges
        if e.edge_type in ("calls", "instantiates")
        and e.dst.split(":")[-1] in ("external_symbol", "unresolved")
    ]
    assert external, f"{lang}: no external call edge emitted at all"
    glued = [
        e for e in external
        if e.dst.split(":")[-2].startswith(f"{receiver}.")
    ]
    assert not glued, (
        f"{lang}: the callee name slot carries the receiver identifier "
        f"{receiver!r}, so no catalogue can key it and the boundary-scoped "
        f"untyped_receiver caveat cannot fire (WI-nakut). "
        f"dsts: {[e.dst for e in glued]}"
    )
