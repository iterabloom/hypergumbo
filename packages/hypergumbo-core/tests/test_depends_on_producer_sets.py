# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rasal: every repaired ``depends_on`` clause is an ENUMERATED producer set.

The six clauses WI-jijor's detector falsified were not six independent
mistakes. Each was written as *"languages where this concept exists"* — a
statement about the world — where ``depends_on`` is defined as *"passes whose
output this pass reads"* — a statement about this tree. Both readings produce a
plausible list of language names, which is why nothing caught the difference
for as long as nothing read the field.

``inherited-calls-linker`` is the clean specimen: the comment above its
declaration names ``_MRO_WALKERS`` as its source of truth and claims to mirror
it. INV-fahub's fleet-walker PR grew that dict from 3 entries to 14 and the
declaration stayed at three. A list copied from a registry is a snapshot; the
registry keeps moving.

So these tests derive the producer set rather than restating it. Two gates:

- **Registry drift** — every language in ``_MRO_WALKERS`` must appear in the
  clause, mapped through the analyzer registry (``_MRO_WALKERS`` is keyed on
  ``Symbol.language``, which is a WIDER vocabulary than pass ids: ``typescript``
  is a language value with no pass of its own, since ``js_ts`` registers as
  ``javascript``).
- **Source drift** — the analyzers that actually write the metadata or emit the
  edge type a linker consumes, enumerated from their ASTs. String literals and
  keyword-argument names both count, because a hint key reaches ``Edge.meta``
  either as a dict key or as a kwarg to the shared ``make_unresolved_edge``
  helper; comments and prose do not count, which is why this is an AST walk and
  not a grep.

Each gate is one-directional: it asserts the declaration COVERS the enumerated
producers. A clause may name more (a producer that exists but is not yet
enumerable here), and the exact-value tests below pin what each one currently
says so that widening is a deliberate edit.
"""
from __future__ import annotations

import ast
import importlib
from functools import lru_cache
from typing import Iterable

import hypergumbo_core.cli  # linkers register by import side-effect
from hypergumbo_core.analyze.all_analyzers import get_analyzers
from hypergumbo_core.catalog import get_default_catalog
from hypergumbo_core.linkers.inherited_calls import _MRO_WALKERS


@lru_cache(maxsize=1)
def _analyzer_sources() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(module source, pass ids) for every registered analyzer.

    One module may register under several pass ids, and one pass id may be
    served by a module registering several analyzers, so this is a list of
    pairs rather than either direction of a dict.
    """
    by_file: dict[str, list[str]] = {}
    for analyzer in get_analyzers():
        module = importlib.import_module(analyzer.module_path)
        path = module.__file__
        assert path is not None
        by_file.setdefault(path, []).append(analyzer.name)
    out = []
    for path, names in sorted(by_file.items()):
        with open(path, encoding="utf-8") as handle:
            out.append((handle.read(), tuple(sorted(names))))
    return tuple(out)


def _pass_ids_writing(*tokens: str) -> set[str]:
    """Analyzer pass ids whose module names any of ``tokens`` as data."""
    wanted = set(tokens)
    found: set[str] = set()
    for source, names in _analyzer_sources():
        tree = ast.parse(source)
        seen = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        seen |= {
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg
        }
        if seen & wanted:
            found.update(names)
    return found


def _pass_ids_emitting_edge_type(*edge_types: str) -> set[str]:
    """Analyzer pass ids with a literal ``edge_type=`` argument in ``edge_types``.

    Narrower than :func:`_pass_ids_writing` on purpose: ``"extends"`` and
    ``"implements"`` are ordinary English words that appear in prose and in
    unrelated identifiers, so only a keyword argument position counts.
    """
    wanted = set(edge_types)
    found: set[str] = set()
    for source, names in _analyzer_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "edge_type"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value in wanted
                ):
                    found.update(names)
    return found


def _clause_containing(pass_id: str, literal: str) -> list[str]:
    """The one clause of ``pass_id``'s CNF that names ``literal``."""
    declared = {p.id: p for p in get_default_catalog().passes}
    matches = [c for c in declared[pass_id].depends_on if literal in c]
    assert len(matches) == 1, (
        f"{pass_id} has {len(matches)} clauses naming {literal!r}; "
        "the test can no longer identify which one it is about"
    )
    return matches[0]


def _pass_ids_for_language(language: str) -> list[str]:
    """Every producer of ``language`` (WI-juzig: rust has two)."""
    return [a.name for a in get_analyzers() if language in a.languages]


class TestTheScannerCanReturnBothAnswers:
    """LIVE.md §1.6: a control that cannot fail is not a control."""

    def test_a_token_no_analyzer_writes_is_not_found(self) -> None:
        assert _pass_ids_writing("wi_rasal_token_no_analyzer_writes") == set()

    def test_a_token_analyzers_do_write_is_found(self) -> None:
        assert "python" in _pass_ids_writing("base_classes")

    def test_edge_type_scan_ignores_the_word_outside_the_argument(self) -> None:
        """``includes`` appears in prose in most analyzers and as an
        ``edge_type`` in seven; only the argument position counts."""
        emitters = _pass_ids_emitting_edge_type("includes")
        assert emitters
        assert "python" not in emitters


class TestInheritedCallsDeclaresItsWalkers:
    """Clause repaired from a 3-entry snapshot of a 14-entry registry."""

    def test_every_registered_mro_walker_language_is_declared(self) -> None:
        clause = _clause_containing("inherited-calls-linker", "java")
        missing = sorted(
            f"{language}:{pid}"
            for language in _MRO_WALKERS
            for pid in _pass_ids_for_language(language)
            if pid not in clause
        )
        assert missing == [], (
            f"_MRO_WALKERS registers walkers for {missing} and "
            "inherited-calls-linker's depends_on does not name them — the "
            "declaration has drifted from the registry it says it mirrors"
        )

    def test_every_site_2_and_site_3_hint_producer_is_declared(self) -> None:
        """Sites 2 and 3 emit WITHOUT a walker: step 1 resolves the method
        directly on the inferred type, so a language that stamps only the hint
        still makes this linker produce edges."""
        clause = _clause_containing("inherited-calls-linker", "java")
        producers = _pass_ids_writing(
            "receiver_type_hint", "inherited_field_receiver",
        )
        assert sorted(producers - set(clause)) == []

    def test_the_clause_is_exactly_this(self) -> None:
        assert _clause_containing("inherited-calls-linker", "java") == [
            "cpp", "csharp", "d", "go", "groovy", "java", "javascript",
            "kotlin", "objc", "php", "python", "ruby", "rust",
            "rust_analyzer",  # WI-juzig: rust's second producer
            "scala", "scip_python",  # WI-nanom: python's second producer
            "swift",
        ]

    def test_the_inheritance_linker_conjunct_is_gone(self) -> None:
        """It was falsified on 26 surveys and it was false: Site-2 step 1 emits
        ``ast_call_type_inferred`` with no inheritance edges in the graph at
        all. The ordering fact it recorded (priority 18, after inheritance's
        15) is carried by ``priority``, not by this axis."""
        declared = {p.id: p for p in get_default_catalog().passes}
        clauses = declared["inherited-calls-linker"].depends_on
        assert ["inheritance-linker"] not in clauses
        assert len(clauses) == 1


class TestInheritanceDeclaresItsMetadataProducers:
    """The linker reads two Symbol.meta keys and nothing else."""

    def test_every_base_classes_and_included_modules_producer_is_declared(
        self,
    ) -> None:
        clause = _clause_containing("inheritance-linker", "java")
        producers = _pass_ids_writing("base_classes", "included_modules")
        assert sorted(producers - set(clause)) == []

    def test_the_clause_is_exactly_this(self) -> None:
        assert _clause_containing("inheritance-linker", "java") == [
            "apex", "cpp", "csharp", "go", "groovy", "java", "javascript",
            "kotlin", "objc", "php", "python", "ruby", "scala", "swift",
        ]

    def test_rust_dart_and_elixir_are_no_longer_declared(self) -> None:
        """Declared for four months, contributing nothing. ``rust`` emits
        ``implements`` EDGES directly — which feeds ``type-hierarchy-linker``,
        not this linker, and it is declared there. ``dart`` and ``elixir``
        write neither metadata key."""
        clause = _clause_containing("inheritance-linker", "java")
        assert [x for x in ("rust", "dart", "elixir") if x in clause] == []


class TestTypeHierarchyDeclaresItsEdgeSources:
    """The linker indexes extends/implements EDGES, not language constructs."""

    def test_the_language_agnostic_producer_is_declared(self) -> None:
        clause = _clause_containing("type-hierarchy-linker", "inheritance-linker")
        assert "inheritance-linker" in clause

    def test_every_direct_analyzer_emitter_is_declared(self) -> None:
        clause = _clause_containing("type-hierarchy-linker", "inheritance-linker")
        producers = _pass_ids_emitting_edge_type("extends", "implements")
        assert sorted(producers - set(clause)) == []

    def test_the_clause_is_exactly_this(self) -> None:
        assert _clause_containing("type-hierarchy-linker", "inheritance-linker") == [
            "inheritance-linker", "blade", "haskell", "java", "javascript",
            "python", "ruby", "rust", "rust_analyzer", "scip_python", "twig", "vhdl",
        ]


class TestBuildTargetDeclaresItsManifestProducers:
    def test_every_defines_target_producer_is_declared(self) -> None:
        clause = _clause_containing("build-target-linker", "toml")
        producers = _pass_ids_emitting_edge_type("defines_target")
        assert sorted(producers - set(clause)) == []

    def test_the_clause_is_exactly_this(self) -> None:
        assert _clause_containing("build-target-linker", "toml") == [
            "json", "manifest_targets", "toml", "xml",
        ]

    def test_the_entry_point_conjunct_is_gone(self) -> None:
        """It named five languages and was falsified from Haskell. The linker
        matches a function or method symbol literally named ``main`` (or the
        manifest's ``target_function``), which every analyzer that emits
        functions can supply — so the honest enumeration is all 118 and a
        narrow one is false. A conjunct satisfied by everything states nothing
        the empty list does not."""
        declared = {p.id: p for p in get_default_catalog().passes}
        clauses = declared["build-target-linker"].depends_on
        assert clauses == [["json", "manifest_targets", "toml", "xml"]]


class TestDatabaseQueryWasNotRepaired:
    """The sixth falsified clause needed no repair; the detector did.

    All 68 of its falsifications emitted nodes and ZERO edges — the linker
    minting one query symbol per SQL string scraped from a host file, with no
    ``kind="table"`` dst to link it to. ``sql`` is the only analyzer that emits
    that kind, so the declaration was right and the reading was wrong (fixed in
    the preceding commit). Pinned here so the clause is not "tidied" later by
    someone reading the old audit.
    """

    def test_the_sql_conjunct_is_still_declared(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        assert ["sql"] in declared["database-query-linker"].depends_on


def _flatten(clauses: Iterable[Iterable[str]]) -> set[str]:
    return {literal for clause in clauses for literal in clause}


class TestEveryRepairedLiteralResolves:
    """A repaired clause must not introduce a literal that names no pass.

    ``_MRO_WALKERS`` is keyed on ``Symbol.language``, which includes
    ``typescript`` — a language with no pass of its own. Deriving the clause
    from the walker keys without mapping through the registry would have
    declared a dependency on a pass that does not exist, and
    ``validate_pass_name_resolution`` has no production call site to catch it.
    """

    def test_no_repaired_clause_names_an_unknown_pass(self) -> None:
        catalog = get_default_catalog()
        known = {p.id for p in catalog.passes}
        declared = {p.id: p for p in catalog.passes}
        repaired = (
            "inherited-calls-linker", "inheritance-linker",
            "type-hierarchy-linker", "build-target-linker",
        )
        unknown = sorted(
            _flatten(
                clause
                for pass_id in repaired
                for clause in declared[pass_id].depends_on
            )
            - known
        )
        assert unknown == []


class TestDatabaseQueryDeclaresOnlyItsDestinationSupplier:
    """WI-ditir: a linker that OPENS THE FILES ITSELF depends on no host
    analyzer, and saying otherwise is the too-wide shape falsification cannot
    see.

    ``database-query-linker`` declared eleven host languages in a second
    conjunct while scanning four globs and dispatching to three scanners, so
    eight of the eleven named neither a pass whose output it reads nor a file
    it opens. Too wide is the FALSE-ALL-CLEAR direction here: a clause is an
    inner-OR, so any one member satisfies it, and eight never-scanned languages
    keep it satisfiable on repos where the linker cannot have read anything.
    ``find_falsified_dependencies`` is backward-looking and can only catch a
    clause that is too NARROW, so nothing would ever have reported this.

    The repair follows the shipped WI-dinum precedent on ``graphql-linker``
    verbatim -- name what supplies the edge DESTINATION, not where the source
    pattern was found. Here that is ``sql``, whose ``kind="table"`` symbols are
    the only pass output this linker consumes; the query side it reads off disk
    itself, through ``_find_source_files``.

    NOT DONE HERE, deliberately: recording the scan capability on
    ``activation``. ``LinkerActivation`` has no file-shape field, so that is a
    core-dataclass change with its own axis-declaration obligations, and the
    seven sibling linkers this row also names each need their own verification
    that they consume no host analyzer's output. Both are residual.
    """

    def test_the_clause_is_exactly_this(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        assert declared["database-query-linker"].depends_on == [["sql"]]

    def test_the_host_language_conjunct_is_gone(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        clauses = declared["database-query-linker"].depends_on
        assert len(clauses) == 1
        assert [c for c in clauses if "python" in c] == []

    def test_the_linker_opens_source_files_itself(self) -> None:
        """The justification, derived rather than asserted: the module carries
        its own glob patterns, so the query side arrives from disk and not from
        a host analyzer's output."""
        import hypergumbo_core.linkers.database_query as mod

        assert mod.__file__ is not None
        with open(mod.__file__) as handle:
            tree = ast.parse(handle.read())
        globs = sorted({
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("**/*.")
        })
        assert globs == ["**/*.java", "**/*.js", "**/*.py", "**/*.ts"]

    def test_sql_still_supplies_the_destination(self) -> None:
        """The control on the removal: dropping the WRONG conjunct would have
        left the linker declaring no producer for its edge destination."""
        declared = {p.id: p for p in get_default_catalog().passes}
        known = {p.id for p in get_default_catalog().passes}
        assert "sql" in known
        assert ["sql"] in declared["database-query-linker"].depends_on


class TestMessageQueueDeclaresNothingBecauseItConsumesNothing:
    """WI-zujan: the second clean specimen of WI-ditir's family.

    ``message-queue-linker`` declared NINE host languages. Its wrapper is

        result = link_message_queues(ctx.repo_root)

    and ``link_message_queues(root: Path)`` takes the repository root and
    nothing else. It reads no ``ctx.symbols``, no ``ctx.edges``, and no pass
    output of any kind; it scans four globs itself and MINTS BOTH ENDS of every
    edge it emits (its result carries ``symbols`` as well as ``edges``). So no
    pass supplies its destination either, and the honest declaration is the
    empty CNF — the registry's documented default.

    Its own comment stated the WI-rasal diagnosis verbatim without noticing:
    "Kafka/RabbitMQ/SQS/Redis pub-sub clients exist across all common backend
    languages" is a statement about THE WORLD, where ``depends_on`` is defined
    as the passes whose OUTPUT this pass reads. Both readings produce a
    plausible list of language names, which is why nothing caught it.

    This is a stricter case than database-query-linker (WI-ditir), which at
    least genuinely consumed ``sql``'s ``kind="table"`` symbols and kept
    ``[["sql"]]``. Here there is nothing to keep.
    """

    def test_the_clause_is_gone_entirely(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        assert declared["message-queue-linker"].depends_on == []

    def test_no_host_language_is_declared(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        flat = _flatten(declared["message-queue-linker"].depends_on)
        assert sorted(flat & {"python", "javascript", "ruby", "java", "go"}) == []

    def test_the_wrapper_passes_only_the_repo_root(self) -> None:
        """The justification, derived from the source rather than asserted: the
        linker's entry point takes one parameter and it is the root."""
        import inspect

        from hypergumbo_core.linkers.message_queue import link_message_queues

        params = list(inspect.signature(link_message_queues).parameters)
        assert params == ["root"]

    def test_the_control_can_fail(self) -> None:
        """A linker that DOES consume a pass still declares it — so an empty
        clause here is a finding about this linker, not about the assertion."""
        declared = {p.id: p for p in get_default_catalog().passes}
        assert declared["database-query-linker"].depends_on == [["sql"]]


class TestGraphqlResolverDeclaresItsSchemaSuppliers:
    """WI-zujan: the sibling WI-dinum repaired one half of.

    ``graphql-resolver-linker`` declared five HOST languages plus ``graphql``.
    Its only read of pass output is ``_get_graphql_schema_symbols``, which
    filters ``ctx.symbols`` on ``language == "graphql"`` and
    ``kind in ("type", "field", "interface")`` — and nothing else. The RESOLVER
    side, which is where those five host languages would matter, is read off
    disk by ``link_graphql_resolvers(ctx.repo_root, schema_symbols)``.

    Its comment contained both readings side by side without noticing the
    difference: "resolvers live in the language hosting the GraphQL server" is
    a statement about the world; "Schema docs via the graphql analyzer carry
    the type/field targets" is the actual dependency.

    BOTH suppliers are declared, not just the analyzer. ``graphql-sdl-linker``
    emits ``kind == "field"`` symbols at ``language="graphql"`` from SDL
    embedded in a ``gql`` template, and ``_get_graphql_schema_field_symbols``
    records that "the GraphQL analyzer has never emitted this kind". So a
    ``field`` target can come from either, which is the same pair WI-dinum
    settled on for ``graphql-linker``.
    """

    def test_the_clause_is_exactly_this(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        assert declared["graphql-resolver-linker"].depends_on == [
            ["graphql", "graphql-sdl-linker"]
        ]

    def test_the_host_languages_are_gone(self) -> None:
        declared = {p.id: p for p in get_default_catalog().passes}
        flat = _flatten(declared["graphql-resolver-linker"].depends_on)
        assert sorted(flat & {"javascript", "python", "java", "ruby", "go"}) == []

    def test_it_matches_the_sibling_wi_dinum_already_repaired(self) -> None:
        """Both halves of the GraphQL pair now name the same suppliers, which
        is the point: they consume the same schema symbols."""
        declared = {p.id: p for p in get_default_catalog().passes}
        assert (
            declared["graphql-resolver-linker"].depends_on
            == declared["graphql-linker"].depends_on
        )

    def test_both_declared_suppliers_are_real_passes(self) -> None:
        known = {p.id for p in get_default_catalog().passes}
        assert {"graphql", "graphql-sdl-linker"} <= known


# ---------------------------------------------------------------------------
# WI-zujan: the six linkers WI-ditir's family left, each derived by reading
# what the linker consumes. The shape of every repair is the WI-dinum one:
# name the passes that supply the records the linker READS, not the languages
# where its own disk scan finds the other end.
# ---------------------------------------------------------------------------


def _pass_ids_calling(*callee_names: str) -> set[str]:
    """Analyzer pass ids whose module CALLS any of ``callee_names``.

    For producers that reach a record through a helper — ``make_route_symbol``
    writes ``route_path`` for its callers, so a literal scan of the caller's own
    source misses them — or construct a record type (``UsageContext``).
    """
    wanted = set(callee_names)
    found: set[str] = set()
    for source, names in _analyzer_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in wanted or (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in wanted
            ):
                found.update(names)
    return found


def _declared(pass_id: str) -> list[list[str]]:
    declared = {p.id: p for p in get_default_catalog().passes}
    return declared[pass_id].depends_on


class TestZujanScannerControls:
    def test_the_call_scanner_finds_a_known_caller_and_not_a_fiction(self) -> None:
        assert "python" in _pass_ids_calling("make_route_symbol")
        assert _pass_ids_calling("wi_zujan_no_such_callee") == set()


class TestHttpDeclaresItsRouteProducers:
    """http-linker reads exactly one thing from the graph: path-bearing route
    records, its edge DESTINATION (``_get_route_symbols`` -> ``route_of``). The
    client side comes off disk, which is why ``elm`` (scanned for client calls,
    never a route producer) was on the old list and ``play-routes`` (the Play
    route producer) was not.

    ``route_of`` has two arms and both collapse to the HOST analyzer: the marker
    arm's records are minted by an analyzer via ``make_route_symbol``; the
    concept arm's are the framework-YAML enrichment of a host analyzer's record,
    matched on ``decorators``/``annotations`` meta or on a ``UsageContext``.
    The enrichment is not a pass (it records no AnalysisRun), and its framework
    gating is an activation fact, not a dependency.
    """

    def test_every_route_record_producer_is_declared(self) -> None:
        (clause,) = _declared("http-linker")
        producers = (
            _pass_ids_calling("make_route_symbol", "UsageContext")
            | _pass_ids_writing("decorators", "annotations")
        )
        assert sorted(producers - set(clause)) == []

    def test_the_clause_is_exactly_this(self) -> None:
        assert _declared("http-linker") == [[
            "clojure", "csharp", "elixir", "go", "groovy", "java", "javascript",
            "kotlin", "php", "play-routes", "python", "ruby", "rust", "scala",
            "swift",
        ]]

    def test_elm_is_no_longer_declared(self) -> None:
        """The linker scans ``.elm`` files for CLIENT calls; no elm pass emits
        a route record, so a clause naming it was satisfied on an Elm-only repo
        where nothing could be matched."""
        (clause,) = _declared("http-linker")
        assert "elm" not in clause


class TestCryptoFlowDeclaresItsFileListProducers:
    """crypto-flow-linker has no glob of its own: every file it scans comes from
    ``ctx.symbols`` filtered to ``_CRYPTO_LANGUAGES``, so the passes emitting
    symbols in those languages are a STRUCTURAL requirement — without them the
    file list is empty and the linker returns nothing. Both edge ends are
    minted, which is why this is not an empty clause."""

    def test_the_clause_is_every_producer_of_a_scanned_language(self) -> None:
        from hypergumbo_core.linkers.crypto_flow import _CRYPTO_LANGUAGES

        producers = sorted({
            pid for language in _CRYPTO_LANGUAGES
            for pid in _pass_ids_for_language(language)
        })
        assert _declared("crypto-flow-linker") == [producers]

    def test_the_clause_is_exactly_this(self) -> None:
        assert _declared("crypto-flow-linker") == [["javascript", "rust", "rust_analyzer"]]


class TestPythonOnlyLinkersDeclarePython:
    """Both scan ``**/*.py`` only and read records only a Python producer
    writes; eight and nine host languages were declared."""

    def test_subprocess_names_both_python_producers(self) -> None:
        """Destinations: ``concept=command`` (written onto the ``python``
        analyzer's decorators/base_classes by enrichment), argparse handlers
        (resolvable on ``scip_python`` records too), ``fire.Fire`` methods."""
        assert _declared("subprocess-linker") == [
            sorted(_pass_ids_for_language("python")),
        ]

    def test_orm_names_the_python_analyzer_only(self) -> None:
        """Destinations are ``concept=model`` symbols, matched on
        ``base_classes`` — which ``scip_python`` never writes — so it alone
        cannot produce an edge."""
        producers = set(_pass_ids_for_language("python")) & _pass_ids_writing(
            "base_classes",
        )
        assert _declared("orm-linker") == [sorted(producers)]
        assert _declared("orm-linker") == [["python"]]


class TestSelfScanningLinkersDeclareNothing:
    """Both mint the records their edges join and read no pass output that
    their core edges need. The WI-dilab allowance requires the fixed phrase."""

    def test_event_sourcing_is_empty(self) -> None:
        assert _declared("event-sourcing-linker") == []

    def test_grpc_is_empty(self) -> None:
        assert _declared("grpc-linker") == []
