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
