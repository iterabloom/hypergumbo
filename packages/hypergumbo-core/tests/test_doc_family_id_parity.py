# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-dulah doc-family ``node.id`` grammar gate — the gate-before-sweep for the
one limb of the invariant that the self-corpus structurally cannot measure.

INV-dulah's statement enumerates four surviving limbs. Three are drained (the
route-marker kind-slot via WI-zugob, the synthetic-linker name-slot via
WI-vuzaf, the manifest-analyzer role fossils via the three config analyzers).
This gate covers the fourth: the **doc-family slot ORDER**.

The eleven doc/markup/template analyzers (rst / scss / vue / svelte / puppet /
robot / pony / twig / sparql / kdl / astro) mint both their ids through the one
shared ``analyze.base.make_doc_symbol_ids`` chokepoint, which emitted
``{lang}:{path}:{kind}:{start_line}:{name}`` — kind third, no span, name last.
That is deliberately NOT the ADR-0036 grammar (``{lang}:{path}:{start}-{end}:
{name}:{kind}``), and the helper's own docstring recorded the deferral. Against
the canonical right-anchored parse (``span, name, kind = parts[-3:]``) the kind
word lands in the span slot, so every one of these ids failed ``id_format`` with
``malformed_span_segment`` and could not be parsed back into its slots at all.

**Why a fixture corpus is REQUIRED here, and why it is staged out of the repo.**
This limb was invisible for as long as it existed, for two independent reasons
that compound:

  1. *Nothing in this repo is written in these languages.* Zero of the eleven
     file types are tracked here, so a self-analysis reports ``id_format: 0``
     while saying nothing whatsoever about the doc family. Reading that 0 as
     evidence about this limb is exactly the proxy closure the closure-evidence
     discipline exists to prevent.
  2. *Even with such files present, the default pipeline suppresses them.* Doc
     kinds are ``--include-docs``-gated, so rst emits its section symbols
     through ``analyze_rst`` but NOT through a default ``run_behavior_map``
     (measured: 2 sections direct, 0 in the pipeline). This gate therefore runs
     the pipeline with ``include_docs=True``; without it, rst would be a
     vacuous green.

**Why the corpus is written at runtime instead of living in ``tests/fixtures/``.**
Two independent reasons, the second measured the hard way:

  1. The reason ``test_route_concept_parity`` records for *staging* its fixtures
     out: analyzing under ``…/tests/fixtures/…`` subjects every case to the
     pipeline's own test-path heuristics, which is not what this gate measures,
     and it has been shown to suppress a producer outright (a byte-identical
     ``.proto`` yields 4 route markers at an ordinary path and 0 under
     ``tests/fixtures/``).
  2. Staging out is not sufficient here, because a corpus that *lives* in the
     repo is still part of hypergumbo's own tree, and the full-suite self-tree
     ratchet (``scripts/check-self-tree-validation``, ceiling 0 for every
     unbaselined ``(class, severity)`` cell) counts every violation in it. This
     gate's whole design is to RECORD two known producer defects as strict-xfail
     holes — so committing their fixtures would have added 1 axis_conformance
     error and 3 id_format warnings to the self-tree matrix and broken that gate.
     Measured, not predicted: a full self-analysis with the files committed
     reported exactly those four, each keyed to
     ``…/tests/fixtures/doc-family/…``. Generating the corpus into ``tmp_path``
     keeps the recorded holes honest without making the tool's own tree carry
     them, and satisfies (1) by construction.

The general form, which is the inverse of the pt.41 in-place lesson: a gate that
deliberately preserves a defect cannot host its corpus inside the tree the
project ratchets against itself.

**The properties.** Each gets its own test and its own hole set, per the pt.40
lesson that a gate's UNCHECKED properties are a blind spot exactly as much as an
unwalked code path — the route gate checked three properties for months and the
one it omitted (the id name-slot) turned out to be violated by its own anchor
producer:

  * ``canonical_grammar`` — the id parses as five anchored segments with a
    ``{start}-{end}`` span slot (ADR-0036).
  * ``kind_slot`` — the id kind-slot equals ``Symbol.kind`` AND names a
    registered symbol kind (ADR-0036 Ruling 2).

**The hole sets.** Every entry below was MEASURED by running this pipeline over
these fixtures, never inferred from reading a producer. ``KIND_SLOT_HOLES``
carries the two genuine residuals, both of which are **stable_id-scheme-gated
rather than merely unfixed**: the ``kind`` argument to
``make_doc_symbol_ids`` feeds ``make_doc_stable_id``, so correcting a wrong kind
word changes that symbol's ``stable_id`` — an existing computed identity value,
which under the scheme-bump principle requires a scheme bump, and those are
sequenced behind WI-talos v9/v10 exactly as WI-banod T1 is. Recording them as
strict xfails means the eventual scheme bump XPASSes and forces promotion.
"""
from __future__ import annotations

import json
import re
import textwrap

import pytest

from hypergumbo_core.analyze.registry import ensure_discovered
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.symbol_kinds import all_symbol_kind_names

# The analyzers that mint through ``make_doc_symbol_ids``. One fixture case
# each, named for the language, carrying that language's canonical declaration
# idioms.
#
# The last three (ini / properties / requirements) are the CONFIG family, folded
# onto the chokepoint by this same change. They were found by grepping for
# hand-rolled id f-strings after the doc-family fold, not by the gate — a
# reminder that the eleven named in INV-dulah's statement were the adopters of a
# shared helper, while these three had copies of the same broken shape and so
# were invisible to any measurement scoped to the helper's callers. Their
# ``stable_id`` already came from ``make_doc_stable_id`` independently of
# ``node.id``, which is exactly why they were safe to fold now.
#
# NOT here, deliberately: ``bibtex`` and ``bitbake``, whose five sites
# (bitbake 192/302/342/382, bibtex 125) set ``stable_id = symbol_id`` — the
# composite id IS their stable identity, so correcting node.id would change
# stable_id and falsify the WI-talos v9 unqualified digest-compatibility claim.
# That is WI-banod T1, blocked on a RELEASE rather than on effort.
DOC_FAMILY_CASES = [
    "astro",
    "ini",
    "kdl",
    "pony",
    "properties",
    "puppet",
    "requirements",
    "robot",
    "rst",
    "scss",
    "sparql",
    "svelte",
    "twig",
    "vue",
]

# The id lang-slot each case's doc-family symbols carry. Usually the case name;
# vue/svelte also emit ``javascript:`` symbols for their <script> blocks (minted
# by the JS analyzer through ``make_symbol_id``, already canonical), so the
# lang-slot is what separates the doc-family nodes under test from those.
CASE_LANG = {case: case for case in DOC_FAMILY_CASES}

# case -> (filename, source). One canonical declaration idiom per language,
# chosen to exercise several kinds per analyzer rather than the minimum one, so
# the properties below run over a realistic slice of each producer's emit sites.
# Written into tmp_path by the ``doc_family_maps`` fixture — see the module
# docstring for why this corpus is not committed under ``tests/fixtures/``.
CASE_FILES: dict[str, tuple[str, str]] = {
    "astro": ("Page.astro", """\
        ---
        const title = "Hello";
        export function getStaticPaths() {
          return [];
        }
        ---
        <html>
          <body><h1>{title}</h1></body>
        </html>
    """),
    "ini": ("settings.ini", """\
        [database]
        host = localhost
        port = 5432
        password = supersecret

        [logging]
        level = debug
    """),
    "kdl": ("config.kdl", """\
        server {
            host "localhost"
            port 8080
        }
        database {
            url "postgres://localhost"
        }
    """),
    # Carries an `actor` declaration deliberately: `actor` is not a registered
    # symbol kind, which is the pony entry in KIND_SLOT_HOLES. Removing it would
    # make the hole unmeasurable and the case a vacuous green.
    "pony": ("thing.pony", """\
        class Counter
          var _n: U64 = 0
          new create() =>
            _n = 0
          fun get_n(): U64 =>
            _n

        actor Main
          new create(env: Env) =>
            env.out.print("hi")
    """),
    "properties": ("application.properties", """\
        spring.datasource.url=jdbc:postgresql://localhost/db
        spring.datasource.password=secret
        logging.level.root=INFO
        server.port=8080
    """),
    "puppet": ("site.pp", """\
        class webserver {
          package { 'nginx':
            ensure => installed,
          }
        }

        define mysite($port = 80) {
          notify { "port ${port}": }
        }

        node 'web01' {
          include webserver
        }
    """),
    # The url and editable forms exercise the two sites whose id kind-slot used
    # to carry a ROLE (`url_requirement` / `editable`) rather than Symbol.kind.
    "requirements": ("requirements.txt", """\
        flask==2.3.0
        requests>=2.28.0
        django[argon2]>=4.0 ; python_version >= "3.10"
        git+https://github.com/psf/black.git@main
        -e ./local-package
    """),
    # Carries a `*** Test Cases ***` block deliberately: robot passes
    # kind="test_case" while building a kind="test" Symbol, which is the robot
    # entry in KIND_SLOT_HOLES.
    "robot": ("tests.robot", """\
        *** Settings ***
        Library    OperatingSystem

        *** Variables ***
        ${GREETING}    Hello

        *** Test Cases ***
        Login Works
            Log    ${GREETING}
            Should Be Equal    1    1

        *** Keywords ***
        Do The Thing
            Log    doing
    """),
    "rst": ("index.rst", """\
        Introduction
        ============

        Some introductory prose.

        Getting Started
        ---------------

        More prose here.
    """),
    "scss": ("main.scss", """\
        $primary-color: #333;
        $spacing: 8px;

        @mixin flex-center {
          display: flex;
          align-items: center;
        }

        .button {
          color: $primary-color;
          padding: $spacing;
        }
    """),
    "sparql": ("query.rq", """\
        PREFIX foaf: <http://xmlns.com/foaf/0.1/>
        PREFIX dc: <http://purl.org/dc/elements/1.1/>

        SELECT ?name ?title
        WHERE {
          ?person foaf:name ?name .
          ?doc dc:title ?title .
        }
    """),
    # The `on:click` handler is the case that produced a SIX-segment id before
    # the fold: the event symbol's id name-slot was `button:click`, whose colon
    # shifted every right-anchored slot. It is now sanitized to `button.click`.
    "svelte": ("Widget.svelte", """\
        <script>
          export let label = "hi";
          let count = 0;
          function bump() {
            count += 1;
          }
        </script>
        <button on:click={bump}>{label} {count}</button>
    """),
    "twig": ("page.twig", """\
        {% extends "base.twig" %}
        {% block content %}
          {% for item in items %}
            <li>{{ item.name }}</li>
          {% endfor %}
        {% endblock %}
        {% macro field(name) %}
          <input name="{{ name }}">
        {% endmacro %}
    """),
    "vue": ("App.vue", """\
        <template>
          <div class="app">{{ title }}</div>
        </template>
        <script>
        export default {
          name: 'App',
          props: {
            title: String
          },
          data() {
            return { count: 0 };
          },
          methods: {
            increment() {
              this.count += 1;
            }
          }
        };
        </script>
    """),
}

# ADR-0036: five anchored segments, right-anchored as (span, name, kind). The
# path slot is colon-TOLERANT (Rust's ``std::cmp`` module ids depend on it), so
# only the last three segments are constrained.
_CANONICAL = re.compile(r"^[a-z][a-z0-9_]*:.*:\d+-\d+:[^:]*:[a-z][a-z0-9_]*$")

# (case) -> strict-xfail reason for a MEASURED canonical-grammar hole.
# Empty: the ``make_doc_symbol_ids`` chokepoint now delegates to
# ``make_symbol_id``, so every adopter emits the canonical shape and inherits
# the WI-sikar name-slot sanitization (which is what repairs svelte's
# ``button:click`` event name, a genuine six-segment break before the fold).
CANONICAL_GRAMMAR_HOLES: dict[str, str] = {}

# (case) -> strict-xfail reason for a MEASURED kind-slot hole.
#
# Both are the same defect class the route sweep drained — a producer writing a
# word into the kind-slot that is not the record's own registered kind — and
# both are blocked on the identity-scheme sequencing rather than on effort:
KIND_SLOT_HOLES: dict[str, str] = {
    "pony": (
        "pony.py emits kind='actor' for a Pony actor declaration; 'actor' is "
        "not in the symbol-kind catalog, so the id kind-slot names an "
        "unregistered kind (it also fires axis_conformance independently of "
        "this gate). Resolving it is an ADR-0027 vocabulary call — register "
        "'actor' or fold it onto a canonical — and a fold would change the "
        "kind argument that feeds make_doc_stable_id, hence the stable_id. "
        "Tracked separately; not an id-ORDER defect."
    ),
    "robot": (
        "robot.py passes kind='test_case' to make_doc_symbol_ids while the "
        "Symbol it builds carries kind='test', so the id kind-slot both "
        "mismatches Symbol.kind and names an unregistered kind. Recorded as a "
        "pre-existing property of this invariant since 2026-06-22. The kind "
        "argument feeds make_doc_stable_id, so correcting the word churns "
        "these symbols' stable_id — scheme-gated behind WI-talos v9/v10, the "
        "same gate WI-banod T1 waits on."
    ),
}


def _cases(holes: dict[str, str]) -> list:
    """Parametrization list where a MEASURED hole carries a strict xfail MARKER.

    A marker, never an imperative ``pytest.xfail(reason)`` call inside the test
    body: the imperative form unconditionally reports xfail and can therefore
    never XPASS, which would record each violation while silently disabling the
    ratchet meant to close it — a gate that looks like enforcement and is not.
    With ``strict=True`` the sequence is the proof the ordering works: hole
    present -> xfailed; producer fixed -> XPASS(strict) -> the suite FAILS on
    success, refusing to leave a fixed producer marked broken; hole removed ->
    green.
    """
    return [
        pytest.param(
            case,
            marks=pytest.mark.xfail(strict=True, reason=holes[case]),
        )
        if case in holes
        else case
        for case in DOC_FAMILY_CASES
    ]


def _doc_family_symbols(behavior_map: dict, lang: str) -> list[dict]:
    """Doc-family symbols under test for a case.

    ``kind='file'`` anchors are excluded: they are minted by ``make_file_id``,
    not by ``make_doc_symbol_ids``, and already carry the canonical
    ``{lang}:{path}:1-1:file:file`` sentinel shape. ``external_symbol`` stand-ins
    (twig mints one for an ``{% extends %}`` target) are likewise minted
    elsewhere and already canonical.
    """
    out = []
    for node in behavior_map.get("nodes", []):
        node_id = node.get("id") or ""
        if node_id.split(":")[0] != lang:
            continue
        if node.get("kind") in {"file", "external_symbol"}:
            continue
        out.append(node)
    return out


@pytest.fixture(scope="module")
def doc_family_maps(tmp_path_factory) -> dict:
    """Run the full pipeline once per fixture case; cache the behavior map.

    ``include_docs=True`` is load-bearing, not incidental: without it the doc
    node kinds are filtered from the output and rst contributes zero symbols,
    which would make its case a vacuous green.
    """
    ensure_discovered()
    base = tmp_path_factory.mktemp("doc-family")
    corpus = base / "corpus"
    corpus.mkdir()
    maps: dict = {}
    for case in DOC_FAMILY_CASES:
        filename, source = CASE_FILES[case]
        staged = corpus / case
        staged.mkdir()
        (staged / filename).write_text(textwrap.dedent(source))
        out = base / f"{case}.json"
        run_behavior_map(staged, out_path=out, progress=False, include_docs=True)
        maps[case] = json.loads(out.read_text())
    return maps


@pytest.mark.parametrize("case", DOC_FAMILY_CASES)
def test_doc_family_emits_symbols(case: str, doc_family_maps: dict) -> None:
    """Non-vacuity guard: each case emits at least one doc-family symbol.

    Without this, a producer that regresses to emitting nothing would turn every
    property test below green by emptying them — the failure mode that made the
    old route-marker provenance check pass vacuously on an empty ``origin``
    list.
    """
    symbols = _doc_family_symbols(doc_family_maps[case], CASE_LANG[case])
    assert symbols, (
        f"{case}: fixtures/doc-family/{case}/ produced no non-file "
        f"'{CASE_LANG[case]}:' symbols, so the id properties below would pass "
        f"vacuously. Either the grammar is unavailable or the analyzer "
        f"regressed."
    )


@pytest.mark.parametrize("case", _cases(CANONICAL_GRAMMAR_HOLES))
def test_doc_family_id_canonical_grammar(case: str, doc_family_maps: dict) -> None:
    """ADR-0036: the id parses as five anchored segments with a real span slot."""
    offenders = [
        node["id"]
        for node in _doc_family_symbols(doc_family_maps[case], CASE_LANG[case])
        if not _CANONICAL.match(node["id"])
    ]
    assert not offenders, (
        f"{case}: {len(offenders)} doc-family node.id value(s) are not the "
        f"canonical {{lang}}:{{path}}:{{start}}-{{end}}:{{name}}:{{kind}} "
        f"grammar — e.g. {offenders[0]!r}. Mint through "
        f"analyze.base.make_doc_symbol_ids."
    )


@pytest.mark.parametrize("case", _cases(KIND_SLOT_HOLES))
def test_doc_family_id_kind_slot(case: str, doc_family_maps: dict) -> None:
    """ADR-0036 Ruling 2: the id kind-slot IS ``Symbol.kind``, and is registered."""
    known = all_symbol_kind_names()
    offenders = []
    for node in _doc_family_symbols(doc_family_maps[case], CASE_LANG[case]):
        slot = node["id"].split(":")[-1]
        if slot != node.get("kind"):
            offenders.append(f"{node['id']} (kind-slot {slot!r} != Symbol.kind {node.get('kind')!r})")
        elif slot not in known:
            offenders.append(f"{node['id']} (kind-slot {slot!r} is not a registered symbol kind)")
    assert not offenders, (
        f"{case}: {len(offenders)} doc-family id kind-slot violation(s) — "
        f"e.g. {offenders[0]}"
    )
