# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry-driven parity gate: can INV-fibis's untyped-receiver disclosure
actually FIRE for this language?

WHY THIS FILE EXISTS. ``verify_claims.untyped_receiver_sites`` — the channel
that makes a clean boundary/taint verdict name the sink call sites whose
receiver it could not type — selects on ``meta.call_construct == "method"``.
A language whose analyzer never stamps that key reaches a clean ``confirmed``
and discloses NOTHING, and no test anywhere notices, because every analyzer
test asserts what it DOES emit rather than what a downstream safety gate needs.

Measured 2026-08-23 across the nine catalogued languages that declare
method-kind sinks (fixtures and results:
``~/hypergumbo_lab_notebook/disclosure_parity_08232026/``):

    python, go, scala, swift   disclosure fires              427 sinks
    java, objc, rust           edge emitted, NO stamp        295 sinks
    kotlin, javascript         no external call edge at all  232 sinks

527 of 954 method-kind sinks (55%) sat in languages where the disclosure could
not fire. Method-kind is nearly the whole catalogue for several of them:
java 98%, kotlin 97%, objc 95%, swift 93%, scala 89%.

THE FIXTURE SHAPE IS LOAD-BEARING AND A FIRST DRAFT GOT IT WRONG. Written with
a CAST receiver (``f.asInstanceOf[java.io.File].createNewFile()``,
``(fm as! FileManager).createFile(...)``), scala and swift both read as INERT
and the headline was 82%. They are not: a cast expression takes a different,
bare-call branch in both analyzers, and the plain ``receiver.method()`` shape —
the one real code uses — is already stamped correctly in both. Fixtures here
use the plain shape deliberately. The cast-receiver gap is real but separate
and much narrower; it is recorded on the PR1 work item, not fixed here.

THE CAST-RECEIVER RESIDUAL IS NOW CLOSED HERE, AND IT WAS BIGGER THAN "CASTS".
Re-measured 2026-08-25 (INV-tutar/INV-pirot Phase 0, dev e41a655d6d): the gap
is not casts, it is EVERY receiver that is not a bare identifier -- object
creation, a call result, a cast, a chain -- and it is in THREE languages, not
two:

    java   f.mkdirs()                  method   <- control
           new File("x").mkdirs()      ABSENT
           get().mkdirs()              ABSENT
           ((File) o).mkdirs()         ABSENT
           f.getParentFile().mkdirs()  ABSENT
    scala  f.createNewFile()           method   <- control
           new File(..) / get() / asInstanceOf   ABSENT
    swift  fm.createFile(...)          method   <- control
           get() / (o as! FileManager)           ABSENT
    objc / rust / go                   method   (all shapes, already clean)
    python                             NO EDGE AT ALL for a call-result
                                       receiver -- WI-makij, not a stamp gap

The cause is one predicate per analyzer: ``call_construct`` was derived from
the receiver's NAME, and the name is ``None`` for a receiver EXPRESSION -- so
a variable that answers "what type can I look this receiver up under" was
asked "is there a receiver at all". Two questions, one variable (LIVE.md rule
7). :data:`COMPLEX_RECEIVER_FIXTURES` below is the arm that keeps them apart.

THE GATE, not the five stamps, is the point. It is driven from the io_primitives
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

#: Languages whose analyzer emits NO external call edge at all for an instance
#: method call, so there is nothing to stamp and the disclosure cannot be made
#: to fire by stamping (WI-nasuf: kotlin 89/93 sinks, javascript 29/83).
#: These are handed to the DECLARED-BLINDNESS work (owner ruling 2026-08-23,
#: "declare the blindness"), which makes the coverage gate refuse a bare
#: ``confirmed`` for them. Listed here so the set is visible and BOUNDED —
#: see :func:`test_the_no_edge_exemption_set_is_exactly_the_measured_two`.
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
#: only thing that changes is the shape of the receiver, which is exactly the
#: variable the defect turned on (INV-pirot).
#:
#: Shapes are deliberately varied across languages (object creation, cast, call
#: result) rather than uniform, because a uniform fixture set is how the FIRST
#: draft of this file concluded "scala and swift are inert" from one arm and
#: was wrong (LIVE.md rule 12).
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
#: so there is nothing to stamp. Measured 2026-08-25: python emits an edge for
#: the ``make()`` call and none for the ``.open("w")`` on its result. That is an
#: EMISSION gap, not a stamp gap, and it is already filed as WI-makij (chained
#: method calls emit no call edge) — cited rather than re-filed.
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
    BARRIER — and since PR #214 a barrier earns ``sanitized``, which DROPS the
    flow from the claim's violation set. A missing stamp therefore DELETES
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


#: The THIRD shape, and the one where java stands alone: an EXPLICIT ``this`` /
#: ``self`` receiver on a call the analyzer cannot resolve, because the method
#: is inherited from a supertype outside the repository. The receiver is named
#: but the DECLARING TYPE is not, so the catalogue cannot be asked what the call
#: does — which is precisely what ``call_construct`` exists to record.
#:
#: Measured 2026-08-25 across every language with a plain-receiver fixture:
#: python, objc, rust and go stamp it; scala and swift stamp it; java alone
#: leaves it bare. This arm is parity with the other six, not a new convention
#: — the same argument WI-sajis used for the plain shape.
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
    sanitizer as a phantom barrier — measured, with a control, in
    ``test_this_receiver_phantom_barrier.py`` in hypergumbo-core.

    THE RECALL COST IS ZERO WHERE IT MATTERS AND SMALL ELSEWHERE, measured
    rather than assumed: ``gate_named_entry`` returns ``None`` immediately for
    ``call_construct == "method"``, so the stamp can only refuse a
    FUNCTION-kind catalogue hit — and java's catalogue declares 139 method-kind
    primitives, 3 attribute-kind and **no function-kind entry at all**, so
    nothing it could refuse exists. scala has 17 function-kind entries and
    swift 7; both already stamp this shape and are here as controls.
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
