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
- **JS/TS, Go, Rust, Java, C++, Ruby, Elixir** — WI-mafik audit fixtures
  covering the canonical import-times-call constructs per language.
  All seven were initially loaded with ``xfail_reason`` capturing the
  per-language gap discovered at audit time. WI-tihup / WI-mafik PR2
  closed all seven gaps; the ``xfail_reason`` fields have been stripped
  and the test now demands all 8 fixtures pass. Re-introducing an
  ``xfail_reason`` later is a regression signal.

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
4. If the analyzer doesn't yet handle one or more of the constructs,
   set ``xfail_reason`` to a short message naming the gap and the
   tracker WI that will fix it. The test will run, expect failure, and
   surface a regression if the analyzer is later improved without
   updating this fixture.
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
    xfail_reason: str | None = None


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
    PolyglotFixture(
        language="js_ts",
        filename="polyglot_js_ts_fixture.ts",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik JS/TS import-times-call coverage fixture.\n"
            'import { readFile } from "node:fs/promises";\n'
            'import * as fs from "node:fs";\n'
            'import { writeFile as wf } from "node:fs/promises";\n'
            'import path from "node:path";\n'
            "\n"
            "async function exerciseAllStyles(p: string) {\n"
            "  // Named import + bare call.\n"
            "  await readFile(p);\n"
            "  // Namespace import + member call.\n"
            "  fs.readFileSync(p);\n"
            "  // Aliased named import + bare call.\n"
            '  await wf(p, "content");\n'
            "  // Default import + member call.\n"
            '  path.join("a", "b");\n'
            "}\n"
        ),
        expected_targets=(
            # Analyzer drops the ``node:`` scheme prefix when emitting dsts
            # (cf. ``typescript:fs/promises:0-0:readFile:unresolved``). The
            # module-spec normalization is a separate concern from the
            # call-site coverage axis WI-mafik is auditing.
            ("fs/promises", "readFile"),
            ("fs", "readFileSync"),
            ("fs/promises", "writeFile"),
            ("path", "join"),
        ),
    ),
    PolyglotFixture(
        language="go",
        filename="polyglot_go_fixture.go",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik Go import-times-call coverage fixture.\n"
            "package main\n"
            "\n"
            "import (\n"
            '\t"fmt"\n'
            '\tf "fmt"\n'
            '\t. "strings"\n'
            ")\n"
            "\n"
            "func exerciseAllStyles() {\n"
            "\t// Standard import + member call.\n"
            '\tfmt.Println("hi")\n'
            "\t// Aliased import + member call.\n"
            '\tf.Sprintf("hi %d", 1)\n'
            "\t// Dot import + bare call.\n"
            '\t_ = Contains("haystack", "needle")\n'
            "}\n"
        ),
        expected_targets=(
            ("fmt", "Println"),
            ("fmt", "Sprintf"),
            ("strings", "Contains"),
        ),
    ),
    PolyglotFixture(
        language="rust",
        filename="polyglot_rust_fixture.rs",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik Rust import-times-call coverage fixture.\n"
            "use std::fs;\n"
            "use std::fs::write;\n"
            "use std::fs::create_dir as mkdir;\n"
            "\n"
            "fn exercise_all_styles() {\n"
            "    // Qualified call through module alias.\n"
            '    let _ = fs::read_to_string("path");\n'
            "    // Bare call from ``use std::fs::write``.\n"
            '    let _ = write("path", "content");\n'
            "    // Aliased terminal name + bare call.\n"
            '    let _ = mkdir("path");\n'
            "}\n"
        ),
        expected_targets=(
            ("std::fs", "read_to_string"),
            ("std::fs", "write"),
            ("std::fs", "create_dir"),
        ),
    ),
    PolyglotFixture(
        language="java",
        filename="Fixture.java",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik Java import-times-call coverage fixture.\n"
            "import java.util.Arrays;\n"
            "import java.util.Collections;\n"
            "import static java.util.Collections.singletonList;\n"
            "\n"
            "public class Fixture {\n"
            "    public static void exerciseAllStyles() {\n"
            "        // Standard class import + static method call.\n"
            "        Arrays.asList(1, 2, 3);\n"
            "        // Another standard import.\n"
            "        Collections.emptyList();\n"
            "        // Static import + bare call.\n"
            "        singletonList(1);\n"
            "    }\n"
            "}\n"
        ),
        expected_targets=(
            ("java.util", "asList"),
            ("java.util", "emptyList"),
            ("java.util", "singletonList"),
        ),
    ),
    PolyglotFixture(
        language="cpp",
        filename="polyglot_cpp_fixture.cpp",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik C++ import-times-call coverage fixture.\n"
            "#include <cstdio>\n"
            "#include <cstring>\n"
            "#include <cstdlib>\n"
            "\n"
            "using namespace std;\n"
            "using std::strlen;\n"
            "\n"
            "int exercise_all_styles() {\n"
            "    // Qualified call (no using).\n"
            '    std::printf("hi\\n");\n'
            "    // using-namespace + bare call.\n"
            "    abort();\n"
            "    // using std::name + bare call.\n"
            '    return strlen("hi");\n'
            "}\n"
        ),
        expected_targets=(
            ("cstdio", "printf"),
            ("cstdlib", "abort"),
            ("cstring", "strlen"),
        ),
    ),
    PolyglotFixture(
        language="ruby",
        filename="polyglot_ruby_fixture.rb",
        source=(
            "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "# WI-mafik Ruby import-times-call coverage fixture.\n"
            'require "json"\n'
            'require "set"\n'
            "\n"
            "def exercise_all_styles\n"
            "  # require + namespaced class-method call.\n"
            '  JSON.parse("{}")\n'
            "  # Same shape.\n"
            "  Set.new([1, 2, 3])\n"
            "  # Fully qualified with leading ::.\n"
            "  ::JSON.generate({})\n"
            "end\n"
        ),
        expected_targets=(
            ("json", "parse"),
            ("set", "new"),
            ("json", "generate"),
        ),
    ),
    PolyglotFixture(
        language="elixir",
        filename="polyglot_elixir_fixture.ex",
        source=(
            "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "# WI-mafik Elixir import-times-call coverage fixture.\n"
            "defmodule Fixture do\n"
            "  alias String, as: S\n"
            "  import Enum, only: [count: 1]\n"
            "\n"
            "  def exercise_all_styles do\n"
            "    # Fully qualified.\n"
            '    String.length("hi")\n'
            "    # Aliased module + member call.\n"
            '    S.upcase("hi")\n'
            "    # import only: + bare call.\n"
            "    count([1, 2, 3])\n"
            "  end\n"
            "end\n"
        ),
        expected_targets=(
            ("String", "length"),
            ("String", "upcase"),
            ("Enum", "count"),
        ),
    ),
    # WI-mafik audit extension (2026-05-21): the per-language idioms in
    # the WI-mafik description that were NOT exercised by the original
    # 8-fixture set. Two cases: Java wildcard import + bare type use, and
    # Elixir ``import X, except: [...]`` + bare call. Both styles are
    # canonical and routinely appear in real-world projects; if the
    # analyzer misses them, file a per-language analyzer-fix WI item
    # (sibling shape: WI-hudud / WI-rakul / WI-vovum / WI-kujom — all
    # already closed by WI-mafik PR2 for the original 7 fixtures).
    PolyglotFixture(
        language="java_wildcard",
        filename="WildcardFixture.java",
        source=(
            "// SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "// WI-mafik Java wildcard-import coverage fixture.\n"
            "// The class name is fully qualified by the wildcard only —\n"
            "// no explicit ``import java.util.Arrays``. This is the case\n"
            "// the WI-mafik description called out as part of the Java audit.\n"
            "import java.util.*;\n"
            "\n"
            "public class WildcardFixture {\n"
            "    public static void exerciseWildcard() {\n"
            "        Arrays.asList(1, 2, 3);\n"
            "        Collections.emptyList();\n"
            "    }\n"
            "}\n"
        ),
        expected_targets=(
            ("java.util", "asList"),
            ("java.util", "emptyList"),
        ),
    ),
    PolyglotFixture(
        language="elixir_except",
        filename="polyglot_elixir_except_fixture.ex",
        source=(
            "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
            "# WI-mafik Elixir ``import X, except: [...]`` coverage fixture.\n"
            "defmodule ExceptFixture do\n"
            "  # All Enum functions are imported except count/1; map/2 is\n"
            "  # still bare-callable.\n"
            "  import Enum, except: [count: 1]\n"
            "\n"
            "  def exercise_import_except do\n"
            "    map([1, 2, 3], fn x -> x + 1 end)\n"
            "  end\n"
            "end\n"
        ),
        expected_targets=(
            ("Enum", "map"),
        ),
    ),
]


def _polyglot_params() -> list:
    """Wrap each fixture as a parametrize entry, marking xfail when set."""
    params = []
    for fixture in POLYGLOT_FIXTURES:
        marks: list = []
        if fixture.xfail_reason is not None:
            marks.append(pytest.mark.xfail(reason=fixture.xfail_reason, strict=True))
        params.append(pytest.param(fixture, id=fixture.language, marks=marks))
    return params


@pytest.mark.parametrize("fixture", _polyglot_params())
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
