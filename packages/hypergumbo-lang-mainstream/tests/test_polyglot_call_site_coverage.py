# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-pisab polyglot call-site coverage fixture.

This test is the regression instrument for the WI-zigah invariant:
*every syntactic call site whose callable is an imported external symbol
must emit a ``calls`` edge whose dst contains the qualified module path.*

Each fixture encodes a set of (import_style, call_style) pairs per
language. Adding a new construct to the fixture automatically fails CI
when the analyzer misses it, so the fixture doubles as an audit
instrument for analyzer coverage regressions and for cross-language
analog bugs (WI-mafik, Level 1b — the same bug class in languages
other than Python).

## Current coverage

- **Python** — 8 distinct import-times-call styles, including the two WI-zigah
  fix cases (multi-segment attribute chain and dotted-submodule bare
  name) plus aliased / deep-nested / multi-name-import variants.

## Adding a new language

1. Build a language fixture that exercises the top import-times-call styles
   for that language (start with the constructs that WI-zigah covered
   in Python — bare name from qualified-module import, multi-segment
   attribute chain, aliased root, aliased terminal name).
2. Append a ``PolyglotFixture`` entry to ``POLYGLOT_FIXTURES`` below.
3. Each expected ``(module_substring, callable_substring)`` target must
   appear in at least one ``calls`` edge's ``dst``. The match is
   substring-based so the fixture is not coupled to the analyzer's
   exact qualified-name format (``:``-delimited vs ``.``-delimited vs
   ``::``-delimited for Rust/C++).

Do NOT add a language until its analyzer handles dotted-submodule
resolution (the WI-zigah Level 1 analog for that language). Adding a
language with a broken analyzer fails CI with a known-bad state — which
is precisely what this fixture exists to surface — but the fix must
land first, or the fixture must be gated behind an ``@pytest.mark.xfail``
that documents the pending fix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map


@dataclass(frozen=True)
class PolyglotFixture:
    """A language-specific import-times-call coverage fixture."""

    language: str
    filename: str
    source: str
    expected_targets: tuple[tuple[str, str], ...]


POLYGLOT_FIXTURES: list[PolyglotFixture] = [
    PolyglotFixture(
        language="python",
        filename="polyglot_python_fixture.py",
        source=(
            '# SPDX-License-Identifier: AGPL-3.0-or-later\n'
            '"""WI-pisab Python import-times-call coverage fixture."""\n'
            "import urllib.request\n"
            "import urllib.parse as parse_alias\n"
            "from urllib.request import urlopen\n"
            "from urllib.request import Request as MyRequest\n"
            "from urllib.parse import urlparse, quote\n"
            "import os.path\n"
            "\n"
            "\n"
            "def exercise_all_styles(url):\n"
            "    # 2-segment attribute chain (WI-zigah Case A).\n"
            "    urllib.request.urlopen(url)\n"
            "    # Aliased module namespace, attribute access.\n"
            "    parse_alias.urlparse(url)\n"
            "    # Bare name from ``from pkg.sub import X`` (WI-zigah Case B).\n"
            "    urlopen(url)\n"
            "    # Aliased bare name from ``from pkg.sub import X as Y``.\n"
            "    MyRequest(url)\n"
            "    # 3-segment chain — Attribute(Attribute(Name)).\n"
            "    urllib.request.Request(url)\n"
            "    # Two bare calls from a single ``from`` with multiple names.\n"
            "    urlparse(url)\n"
            "    quote(url)\n"
            "    # Another 3-segment chain.\n"
            "    os.path.join('a', 'b')\n"
            "    # os.path.dirname — exercise more of the os.path surface.\n"
            "    os.path.dirname('/path')\n"
        ),
        expected_targets=(
            ("urllib.request", "urlopen"),
            ("urllib.parse", "urlparse"),
            ("urllib.request", "Request"),
            ("urllib.parse", "quote"),
            ("os.path", "join"),
            ("os.path", "dirname"),
        ),
    ),
]


@pytest.mark.parametrize(
    "fixture", POLYGLOT_FIXTURES, ids=lambda f: f.language,
)
def test_polyglot_call_site_coverage(
    tmp_path: Path, fixture: PolyglotFixture,
) -> None:
    """Every expected ``(module, callable)`` target is emitted as a ``calls`` edge.

    This is a coverage-hit-set assertion: missing targets fail the test;
    over-counting (emitting more edges than strictly necessary) is
    tolerated because over-coverage is a separate, usually-harmless
    concern compared to under-coverage (which produces silent false
    negatives in io-boundaries, taint-flow, and slice analysis).
    """
    (tmp_path / fixture.filename).write_text(fixture.source)

    out_path = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out_path,
        include_sketch_precomputed=False,
    )
    data = json.loads(out_path.read_text())

    calls = [e for e in data["edges"] if e["type"] == "calls"]
    call_dsts = [e["dst"] for e in calls]

    missing: list[tuple[str, str]] = []
    for module, callable_name in fixture.expected_targets:
        if not any(
            module in dst and callable_name in dst for dst in call_dsts
        ):
            missing.append((module, callable_name))

    assert not missing, (
        f"WI-pisab polyglot fixture ({fixture.language}): analyzer "
        f"missed {len(missing)} of {len(fixture.expected_targets)} "
        f"expected call targets: {missing}. "
        f"Emitted call edge dsts: {call_dsts}"
    )
