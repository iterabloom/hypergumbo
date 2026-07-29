# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical-id parity gate for the three manifest/config analyzers (INV-dulah).

Why this file exists
--------------------
``toml_config`` / ``json_config`` / ``xml_config`` mint ``Symbol.id`` values for
manifest constructs (pyproject console scripts, package.json scripts, Maven
dependencies). Each analyzer grew its own id-minting helper, so the ADR-0036
grammar ``{lang}:{path}:{start}-{end}:{name}:{kind}`` was re-implemented three
times and drifted in two distinct ways:

* a **kind-slot fossil** — the id carried the *role* (``script``) while
  ``Symbol.kind`` was ``file``, so the id did not round-trip to its own record
  and named an unregistered kind; and
* a **name-slot colon** — a Maven coordinate (``groupId:artifactId``) went into
  the name slot verbatim, pushing the id past its five anchored segments.

Both are the same class the route-marker sweep drained (WI-zugob): N producers,
no shared minting contract. This gate states the contract as *properties over
the producers* rather than as golden ids, so a fourth manifest analyzer inherits
the check for free.

Parse direction matters
-----------------------
The canonical parse is anchored **from the right** (``spec_validator``:
``span, name, kind = parts[-3], parts[-2], parts[-1]``). That is why a colon in
the *path* slot is harmless (Rust's ``std::cmp`` ids are canonical) while a
colon in the *name* slot is fatal — it shifts every anchored slot left by one
and the validator reports the wreckage as ``malformed_span_segment``, naming the
symptom rather than the cause. The properties below assert on the slots
directly so a failure names the real defect.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.symbol_kinds import all_symbol_kind_names
from hypergumbo_lang_mainstream.json_config import analyze_json_files
from hypergumbo_lang_mainstream.toml_config import analyze_toml_files
from hypergumbo_lang_mainstream.xml_config import analyze_xml_files

# --- corpus -----------------------------------------------------------------
#
# One case per (analyzer, construct) pair that mints an id from manifest text.
# Each writes the smallest manifest that exercises its construct.

PYPROJECT_WITH_SCRIPTS = """\
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
demo-cli = "demo.cli:main"

[project.gui-scripts]
demo-gui = "demo.gui:main"
"""

# Carries "main" as well as "scripts": both mint a kind="file" symbol whose id
# slot used to name the ROLE (main_entry / script) instead of the kind.
PACKAGE_JSON_WITH_SCRIPTS = """\
{
  "name": "demo",
  "version": "0.1.0",
  "main": "dist/index.js",
  "scripts": {
    "build": "tsc -p .",
    "test": "vitest run"
  }
}
"""

# composer.json mints a kind="package" project symbol whose id slot used to name
# the ECOSYSTEM (composer_package). Same fossil, third construct.
COMPOSER_JSON = """\
{
  "name": "vendor/demo",
  "type": "library",
  "require": {
    "php": ">=8.1"
  }
}
"""

# The groupId carries dots and the coordinate as a whole carries a COLON —
# this is the shape that broke the grammar (org.springframework.boot).
POM_WITH_COORDINATE_DEPENDENCY = """\
<project>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
  </dependencies>
</project>
"""

CASES: dict[str, tuple[str, str, Callable[[Path], AnalysisResult]]] = {
    "toml-scripts": ("pyproject.toml", PYPROJECT_WITH_SCRIPTS, analyze_toml_files),
    "json-scripts": ("package.json", PACKAGE_JSON_WITH_SCRIPTS, analyze_json_files),
    "json-composer": ("composer.json", COMPOSER_JSON, analyze_json_files),
    "xml-maven-dependency": ("pom.xml", POM_WITH_COORDINATE_DEPENDENCY, analyze_xml_files),
}


def _run_case(tmp_path: Path, case: str) -> list:
    """Write the case's manifest into an isolated dir and return its symbols."""
    filename, content, analyzer = CASES[case]
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / filename).write_text(content)
    return list(analyzer(tmp_path).symbols)


@pytest.fixture(params=sorted(CASES))
def case_symbols(request: pytest.FixtureRequest, tmp_path: Path) -> list:
    return _run_case(tmp_path, request.param)


def test_corpus_is_not_vacuous(case_symbols: list) -> None:
    """Non-vacuity guard: every case must actually mint symbols.

    Without this, a producer that silently stops emitting would turn every
    property below into a vacuous green — the failure mode the route-parity
    gate hit when its fixtures were analyzed in place.
    """
    assert case_symbols, "case produced no symbols; the properties would be vacuous"


def test_id_has_five_anchored_segments(case_symbols: list) -> None:
    """ADR-0036: the id parses into exactly {lang}:{path}:{span}:{name}:{kind}.

    Colons in the path slot are legal (the parse is right-anchored), so this
    asserts the *right-hand* anchors are intact rather than counting colons.
    """
    for sym in case_symbols:
        parts = sym.id.split(":")
        assert len(parts) >= 5, f"{sym.id!r} has fewer than 5 segments"
        span = parts[-3]
        assert "-" in span and all(
            p.isdigit() for p in span.split("-", 1)
        ), f"{sym.id!r}: span slot is {span!r}, not '{{start}}-{{end}}' — a colon in the name slot shifts the anchors"


def test_id_kind_slot_round_trips_to_symbol_kind(case_symbols: list) -> None:
    """The id's kind slot equals the record's own ``Symbol.kind`` (ADR-0036 Ruling 2)."""
    for sym in case_symbols:
        kind_slot = sym.id.split(":")[-1]
        assert kind_slot == sym.kind, (
            f"{sym.id!r}: kind slot {kind_slot!r} != Symbol.kind {sym.kind!r}; "
            "the id does not round-trip to its own record"
        )


def test_id_kind_slot_is_a_registered_symbol_kind(case_symbols: list) -> None:
    """The kind slot names a kind in the registry, never an ad-hoc role."""
    registered = all_symbol_kind_names()
    for sym in case_symbols:
        kind_slot = sym.id.split(":")[-1]
        assert kind_slot in registered, (
            f"{sym.id!r}: kind slot {kind_slot!r} is not a registered symbol-kind"
        )


def test_script_role_survives_the_fold(tmp_path: Path) -> None:
    """Folding the kind slot to ``file`` must not lose the script role.

    The role's home is ``meta['entry_role']`` — read by ``noise_filter`` and
    ``selection.filters`` — so this pins the fold as *lossless* rather than
    trading one defect for another.
    """
    for case in ("toml-scripts", "json-scripts"):
        symbols = _run_case(tmp_path / case, case)
        scripts = [s for s in symbols if (s.meta or {}).get("entry_role") == "script"]
        assert scripts, f"{case}: no symbol carries meta['entry_role'] == 'script'"
        for sym in scripts:
            assert sym.kind == "file", f"{case}: script symbol kind is {sym.kind!r}"
