#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure candidate signals for "this language's I/O is structurally invisible".

WHY THIS EXISTS. ``verify_claims.compute_boundary_coverage`` asks one question
per language — *did it produce ANY call edge?* — and a single edge is enough to
look covered. A Kotlin repo that reads a socket and writes the bytes to disk
emits exactly one ``calls`` edge (an intra-repo function call), carries 93
catalogued sinks, reports ``dataflow_capable: False``, finds nothing, and
returns ``confirmed``. The gap is pinned as an xfail by
``test_language_with_a_token_call_edge_still_falsely_confirms``; its docstring
records that closing it "needs a signal with resolution finer than 'any' ...
which is a measurement exercise rather than a predicate change". This is that
measurement.

WHAT IT DOES NOT ASSUME. The obvious finer predicate — "count only call edges
whose dst is not a first-party callable" — was checked first and does NOT
separate the fixtures: ``is_first_party_callable_dst`` requires the path slot to
be ABSOLUTE, and a relative-path dst (``kotlin:App.kt:9-12:helper:function``)
therefore reads as external. That is the disclosed relative-path gap pinned in
``test_symbol_path_slot_span_anchored.py``, surfacing here as a second consumer.
So the candidates below are measured rather than argued.

THE CANDIDATES, per (repo, language):
  A  any_call_edge          — today's predicate, kept as the baseline
  B  non_first_party_calls  — dst is not a first-party callable
  C  method_construct_calls — call edges carrying meta.call_construct == "method"
  D  catalogue_shape_match  — does the language emit ANY call edge of the
                              construct its catalogue is DOMINATED by?
  E  tagged_io_boundaries   — did the catalogue ever match anything at all?
  F  referenced_module_gap  — for each external module this repo REFERENCES that
                              the catalogue covers with method-keyed entries, did
                              the analysis produce any method-construct call edge
                              into it?

MEASURED OUTCOMES, six fixtures (kt_blind / kt_clean / py_io / py_clean / py_pure
/ go_io), which is why F is the one implemented:

  A  fails outright — kt_blind emits 5 call edges and reads as covered.
  B  does not discriminate — 5 for kt_blind too, because the relative-path dst
     defeats ``is_first_party_callable_dst`` (that predicate requires an ABSOLUTE
     path slot). Second consumer of the disclosed relative-path gap.
  C/D catch kt_blind but ALSO downgrade ``py_pure`` — a pure-computation repo with
     no external calls at all. That conflates "the analyzer cannot see this
     language's I/O" with "this repo makes no external calls", and the second is a
     legitimate ``confirmed``. This is the blanket-downgrade failure mode.
  E  IS NOT MEASURABLE FROM A SURVEY MAP AT ALL — ``io_boundary`` meta is stamped
     downstream of ``survey`` by the boundary pass, so the column reads 0 even for
     ``py_io``, which really does call ``os.mkdir``. Recorded as UNMEASURED rather
     than as a null; do not cite the zero.
  F  separates all six. kt_blind references ``java.io.File`` (catalogued fs_write,
     13 method entries) and emits exactly one call edge into it — the CONSTRUCTOR,
     ``File`` — while ``writeText`` produces nothing, which is WI-nasuf verbatim.
     py_pure references no catalogued module, so it raises no expectation and
     keeps its clean ``confirmed``.

THEN F WAS RUN ON REAL REPOS, WHICH CHANGED IT. A first version reported ``fs``,
``console`` and ``process`` starved on BOTH real JavaScript repos — i.e. it would
have downgraded every JS repo in existence. Cause: **JS/TS populate
``call_construct`` on ZERO of 4,872 call edges** while Go populates 7,741. For those
analyzers the field carries no information, so "no method call landed here" is
absence of evidence. The predicate now ABSTAINS for any language that never stamps
the field, and the JS/TS gap is filed separately rather than papered over.

FINAL DIRECTION MEASUREMENT — sherpa-onnx, a real 14-language repo:

    kotlin  BLIND  java.io.File, java.io.FileOutputStream   (2,414 calls, 0 method)
    java    BLIND  java.nio.file.Files, java.net.http.HttpClient
    bash c cpp csharp dart go javascript pascal python rust swift typescript  PASS

12 of 14 languages pass, and both catches name specific modules rather than
condemning a language wholesale. The Kotlin result reproduces WI-nasuf on real code
at scale. The Java result was NEW: ``java.io.File.exists`` stamps ``method`` and
matches, while ``java.nio.file.Files.copy`` stamps nothing because those methods are
STATIC and the catalogue declares them ``kind: method`` — so the whole ``Files``
sink surface is unmatchable. Filed as its own invariant.

DIRECTION IS THE POINT, NOT THE HEADLINE. A coverage gate can only make verdicts
WORSE (confirmed -> inconclusive). The first version of this gate blocked on
``unsupported_taint_languages`` directly and made ``confirmed`` unreachable for
any repo containing a single YAML file; that is the failure mode to measure
against, so every candidate is reported with the count of clean repos it would
downgrade, not just the blind repos it would catch.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any


def _load_map(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _lang_of(symbol_id: str) -> str:
    return symbol_id.split(":", 1)[0] if ":" in symbol_id else ""


def _catalogue_modules(language: str) -> dict[str, bool]:
    """module -> True when the catalogue's entries for it are METHOD-keyed.

    A method-keyed entry (``java.io.File`` with ``methods: [writeText, ...]``)
    can only ever be matched by a call edge whose construct is a method call.
    A function-keyed entry (``kotlin.io.ConsoleKt`` with ``functions:
    [println]``) is matched by a plain call, and that catalogue's own note
    explains why: the receiver is compiler-synthesised and absent at AST level.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is a hard dependency
        return {}
    import hypergumbo_core

    cat = Path(hypergumbo_core.__file__).parent / "io_primitives" / f"{language}.yaml"
    if not cat.exists():
        return {}
    doc = yaml.safe_load(cat.read_text(encoding="utf-8")) or {}
    out: dict[str, bool] = {}
    for entries in doc.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module")
            if not module:
                continue
            has_methods = bool(entry.get("methods"))
            out[module] = out.get(module, False) or has_methods
    return out


def _catalogue_shape(language: str) -> tuple[int, int]:
    """(method-keyed entries, function-keyed entries) in the io catalogue."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is a hard dependency
        return (0, 0)
    import hypergumbo_core

    # ``io_primitives`` is a DATA directory, not a module — importing it yields a
    # namespace package whose ``__file__`` is None. Locate it off the package root.
    root = Path(hypergumbo_core.__file__).parent / "io_primitives"
    cat = root / f"{language}.yaml"
    if not cat.exists():
        return (0, 0)
    doc = yaml.safe_load(cat.read_text(encoding="utf-8")) or {}
    methods = functions = 0
    for entries in doc.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            methods += len(entry.get("methods") or [])
            functions += len(entry.get("functions") or [])
    return (methods, functions)


_CALL_TYPES = {"calls", "imports", "module_attr_ref"}


def measure(map_path: Path) -> dict[str, dict[str, Any]]:
    """Per-language candidate signals for one behavior map."""
    from hypergumbo_core.io_boundary import is_first_party_callable_dst

    bmap = _load_map(map_path)
    edges = bmap.get("edges") or []
    per: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {
            "any_call_edge": 0,
            "non_first_party_calls": 0,
            "method_construct_calls": 0,
            "tagged_io_boundaries": 0,
            "constructs": collections.Counter(),
            "module_refs": collections.Counter(),
            "module_method_calls": collections.Counter(),
        }
    )
    from hypergumbo_core.ir import symbol_path_slot

    for edge in edges:
        etype = edge.get("type", "")
        src = edge.get("src", "")
        if etype not in _CALL_TYPES or ":" not in src:
            continue
        lang = _lang_of(src)
        row = per[lang]
        # Candidate F bookkeeping: which external modules does this repo touch,
        # and did any METHOD-construct call land in each of them?
        module = symbol_path_slot(edge.get("dst", ""))
        if module and not module.startswith(("/", "\\")):
            row["module_refs"][module] += 1
            if (edge.get("meta") or {}).get("call_construct") == "method":
                row["module_method_calls"][module] += 1
        row["any_call_edge"] += 1
        if not is_first_party_callable_dst(edge.get("dst", "")):
            row["non_first_party_calls"] += 1
        meta = edge.get("meta") or {}
        construct = meta.get("call_construct") or "<none>"
        row["constructs"][construct] += 1
        if construct == "method":
            row["method_construct_calls"] += 1
        if meta.get("io_boundary"):
            row["tagged_io_boundaries"] += 1

    out: dict[str, dict[str, Any]] = {}
    for lang, row in per.items():
        methods, functions = _catalogue_shape(lang)
        dominant = "method" if methods > functions else "function"
        row["catalogue_methods"] = methods
        row["catalogue_functions"] = functions
        row["catalogue_dominant_construct"] = dominant
        # Candidate D: the language emits at least one edge of the construct its
        # catalogue is dominated by. A function-dominated catalogue is satisfied
        # by a plain call edge (construct absent or "function").
        if dominant == "method":
            row["catalogue_shape_match"] = row["method_construct_calls"] > 0
        else:
            row["catalogue_shape_match"] = row["any_call_edge"] > 0

        # Candidate F is READ OFF PRODUCTION, not reimplemented here. An
        # instrument that carries its own copy of the predicate measures the
        # instrument; this repo has been bitten by that shape repeatedly (two
        # parsers of one symbol id, four copies of one receiver-trust rule).
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_core.verify_claims import method_starved_modules

        lang_edges = [
            edge for edge in edges
            if _lang_of(edge.get("src", "")) == lang
        ]
        try:
            catalogs = {lang: load_catalog(lang)}
        except Exception:  # pragma: no cover - unknown language
            catalogs = {}
        starved = method_starved_modules(lang_edges, catalogs) if catalogs else []
        row["referenced_module_gap"] = starved
        row["module_refs"] = dict(row["module_refs"])
        row["module_method_calls"] = dict(row["module_method_calls"])
        row["constructs"] = dict(row["constructs"])
        out[lang] = row
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "maps", nargs="+", type=Path,
        help="behavior-map JSON files (label them <name>.json)",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    report: dict[str, Any] = {}
    for map_path in args.maps:
        name = map_path.stem
        try:
            report[name] = measure(map_path)
        except Exception as exc:  # pragma: no cover - operator feedback
            print(f"!! {name}: {exc}", file=sys.stderr)
            continue

    header = (
        f"{'repo':<12} {'lang':<8} {'A:any':>6} {'B:nonFP':>8} {'C:meth':>7} "
        f"{'D':>6} {'F':>6}  starved modules (F)"
    )
    print(header)
    print("-" * len(header))
    for name, langs in report.items():
        for lang, row in sorted(langs.items()):
            starved = row["referenced_module_gap"]
            print(
                f"{name:<12} {lang:<8} {row['any_call_edge']:>6} "
                f"{row['non_first_party_calls']:>8} "
                f"{row['method_construct_calls']:>7} "
                f"{'PASS' if row['catalogue_shape_match'] else 'BLIND':>6} "
                f"{'BLIND' if starved else 'PASS':>6}  "
                f"{', '.join(starved) if starved else '-'}"
            )
    print(
        "\nE (tagged_io_boundaries) is NOT REPORTED: io_boundary meta is stamped "
        "downstream of `survey`, so a survey map cannot carry it. Unmeasured, not zero."
    )
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
