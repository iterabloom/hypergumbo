#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many catalogue rows can only be matched with evidence the analyzer may not have?

WHAT THIS REPLACES, AND WHY IT IS IN-REPO. INV-linub carried a hand-built
exposure table dated 2026-08-06 that ranked languages by METHOD-KIND SHARE and
concluded ``c/cpp/elixir/erlang/haskell: 0% method-kind -- NOT at risk from this
class``. WI-lajus falsified that for C a month later — 156 of 324 corpus socket
sites classify as nothing, through ambiguous names carrying no module slot from
``#include``, a route needing no method-kind rows at all. A table that cannot
predict its own class's counterexamples is not a frame, and a hand-restated
closed set drifts by default. This script exists so the table is REGENERATED
rather than remembered.

TWO ROUTES BREAK THE MATCH, AND THE OLD TABLE MODELLED ONE.

    R1  a method-kind row      -> needs the RECEIVER'S TYPE, because for a
                                  method the receiver type is what fills the
                                  module slot.
    R2  a row whose name the catalogue ITSELF lists in ``ambiguous_names``
                               -> needs MODULE CONTEXT. ``io_boundary``'s F3
                                  gate refuses these without it, deliberately
                                  and correctly (INV-tapat / INV-maluk:
                                  ``str.replace`` must not match
                                  ``Path.replace``).

A DUAL row is both, and it is NOT a third problem: receiver typing fixes it,
because typing the receiver is what supplies the module. The genuinely distinct
population is R2-ONLY — function-kind, ambiguous-named, no receiver to type.
Receiver typing cannot reach those at any quality; they need module context
recovered from an import or an ``#include``.

WHY THE PRODUCTION LOADER AND NOT THE YAML. ``load_catalog`` applies overlays,
merges and normalisation that a direct YAML parse does not. Reading the file
would create a second home for one fact, and the second home silently wins.

THIS IS STRUCTURAL EXPOSURE, NOT MEASURED FAILURE, and the distinction is the
whole reason the numbers are safe to publish. R2 counts rows that REQUIRE module
context, not rows that fail to get it. Whether an analyzer supplies it is
language-specific and is not determinable from the catalogue: Elixir and Erlang
write the module at the call site by convention (``File.read``,
``file:read_file``) and are largely fine; C never does and is measurably blind.
An R2 count is therefore an UPPER BOUND on the at-risk set, and its job is to
say which cells need behavioural measurement — not to score them.

BOTH COUNTS ARE REPORTED ON PURPOSE. The lab-notebook prototype of this script
counted C's R2 as 16 in one place and 22 in another: deduplicated by
``qualified_name`` versus raw rows. Both are true, of different things, and the
gap is duplicate qualified names in the catalogue. Reporting one silently
recreates exactly the second-home failure this file's docstring opens with, so
``r2_ambiguous`` (rows) and ``r2_distinct`` (qualified names) are both named.

A ZERO AND A MISSING INPUT MUST NOT LOOK THE SAME. A catalogue that declares no
``ambiguous_names`` key and one that declares an empty list both yield R2 = 0,
and only one of them is a statement. ``ambiguous_declared`` separates them, and
an unknown language RAISES rather than returning a well-formed row of zeros.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "hypergumbo-core" / "src"))

from hypergumbo_core.io_boundary import load_catalog  # noqa: E402


@dataclass(frozen=True)
class Exposure:
    """One language's structural exposure, by route."""

    language: str
    status: str
    total: int
    r1_method: int
    r2_ambiguous: int
    r2_distinct: int
    dual: int
    r1_only: int
    r2_only: int
    neither: int
    at_risk: int
    ambiguous_declared: bool
    ambiguous_names: int

    @property
    def route(self) -> str:
        """Which mechanism dominates — the column the old table lacked."""
        if self.r1_only and self.r2_only:
            return "both"
        if self.r1_only:
            return "receiver"
        if self.r2_only:
            return "ambiguous-name"
        return "neither"


def catalogue_dir() -> Path:
    """Where the shipped ``io_primitives`` YAMLs live."""
    return (
        _REPO_ROOT
        / "packages"
        / "hypergumbo-core"
        / "src"
        / "hypergumbo_core"
        / "io_primitives"
    )


def shipped_languages() -> list[str]:
    return sorted(p.stem for p in catalogue_dir().glob("*.yaml"))


def exposure(language: str) -> Exposure:
    """Structural exposure for one language. Raises on an unknown language."""
    if language not in shipped_languages():
        raise ValueError(
            f"no shipped catalogue for {language!r}; "
            f"known: {', '.join(shipped_languages())}"
        )
    catalog = load_catalog(language)
    prims = list(catalog.primitives)
    declared = catalog.ambiguous_names is not None
    ambiguous = set(catalog.ambiguous_names or ())

    r1 = [p for p in prims if p.kind == "method"]
    r2 = [p for p in prims if p.name in ambiguous]
    dual = [p for p in r1 if p.name in ambiguous]
    at_risk = {p.qualified_name for p in r1} | {p.qualified_name for p in r2}
    return Exposure(
        language=language,
        status=catalog.status,
        total=len(prims),
        r1_method=len(r1),
        r2_ambiguous=len(r2),
        r2_distinct=len({p.qualified_name for p in r2}),
        dual=len(dual),
        r1_only=len(r1) - len(dual),
        r2_only=len(r2) - len(dual),
        neither=len(prims) - len(r1) - len(r2) + len(dual),
        at_risk=len(at_risk),
        ambiguous_declared=declared,
        ambiguous_names=len(ambiguous),
    )


def census() -> list[Exposure]:
    """Every shipped catalogue, no sample."""
    return [exposure(lang) for lang in shipped_languages()]


def render(rows: Iterable[Exposure]) -> str:
    """The mechanism-by-language table, ordered by receiver-typing exposure."""
    ordered = sorted(rows, key=lambda r: (-r.r1_only, -r.r2_only))
    out = [
        f"{'lang':<12}{'rows':>6}{'R1-only':>9}{'R2-only':>9}{'dual':>6}"
        f"{'neither':>9}   route",
        "-" * 72,
    ]
    for r in ordered:
        out.append(
            f"{r.language:<12}{r.total:>6}{r.r1_only:>9}{r.r2_only:>9}"
            f"{r.dual:>6}{r.neither:>9}   {r.route}"
        )
    undeclared = [r.language for r in ordered if not r.ambiguous_declared]
    if undeclared:
        out.append("")
        out.append(
            "ambiguous_names NOT DECLARED (a silence, not a zero): "
            + ", ".join(undeclared)
        )
    return "\n".join(out)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", dest="out", help="also write the census as JSON to this path"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    rows = census()
    print(render(rows))
    if args.out:
        Path(args.out).write_text(
            json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
