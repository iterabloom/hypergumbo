# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-dinum: the GraphQL schema side gets a producer that is not a file extension.

Both GraphQL linkers take the destination of every edge they can emit from
``language == "graphql"`` symbols, and until this pass the only producer of
those read ``*.graphql`` / ``*.gql`` files. The measured consequence:
``graphql-linker`` emitted 0 edges in 14 corpus runs and
``graphql-resolver-linker`` emitted edges in 2 of 794.

The integration test at the bottom is the one that matters — it is the repro
from the tracker row, reduced to a fixture: a repository whose schema lives in
a ``gql`` template literal and whose resolvers sit beside it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core.cli  # linkers register by import side-effect
from hypergumbo_core.catalog import get_default_catalog
from hypergumbo_core.linkers.graphql_sdl import (
    PASS_ID,
    _blank_comments,
    _definitions_in_block,
    _matching_brace,
    _tagged_blocks,
    extract_embedded_sdl,
    graphql_sdl_linker,
)
from hypergumbo_core.linkers.graphql_resolver import graphql_resolver_linker
from hypergumbo_core.linkers.registry import LinkerContext


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestBlankComments:
    def test_comment_text_is_removed(self) -> None:
        assert "secret" not in _blank_comments("a # secret\nb")

    def test_line_structure_survives_so_spans_stay_honest(self) -> None:
        source = "a # secret\nb"
        blanked = _blank_comments(source)
        assert blanked.count("\n") == source.count("\n")
        assert len(blanked) == len(source)

    def test_text_without_a_comment_is_unchanged(self) -> None:
        assert _blank_comments("type Q { a: Int }") == "type Q { a: Int }"


class TestTaggedBlocks:
    def test_js_tag_yields_body_and_start_line(self) -> None:
        content = "const x = 1;\nconst t = gql`\ntype Q { a: Int }\n`;\n"
        blocks = list(_tagged_blocks(content, is_python=False))
        assert len(blocks) == 1
        body, line = blocks[0]
        assert "type Q" in body
        assert line == 2

    def test_graphql_tag_is_accepted_too(self) -> None:
        blocks = list(_tagged_blocks("graphql`type Q { a: Int }`", is_python=False))
        assert len(blocks) == 1

    def test_a_longer_identifier_ending_in_gql_does_not_match(self) -> None:
        """``mygql`` and ``schema.gql`` are not the ``gql`` tag."""
        assert list(_tagged_blocks("mygql`type Q { a: Int }`", is_python=False)) == []
        assert list(_tagged_blocks("s.gql`type Q { a: Int }`", is_python=False)) == []

    def test_python_triple_quoted_call_yields_the_body(self) -> None:
        content = 'schema = gql(\n    """\ntype Q { a: Int }\n"""\n)\n'
        blocks = list(_tagged_blocks(content, is_python=True))
        assert len(blocks) == 1
        assert "type Q" in blocks[0][0]

    def test_python_single_quoted_triple_is_accepted(self) -> None:
        content = "schema = gql('''\ntype Q { a: Int }\n''')\n"
        assert len(list(_tagged_blocks(content, is_python=True))) == 1

    def test_untagged_template_is_not_sdl(self) -> None:
        assert list(_tagged_blocks("const t = `type Q { a: Int }`", is_python=False)) == []


class TestMatchingBrace:
    def test_simple_body(self) -> None:
        text = "{ a }"
        assert _matching_brace(text, 0) == 4

    def test_nested_braces_do_not_end_the_body_early(self) -> None:
        """A directive argument carrying an object literal contains ``}``."""
        text = "{ a: Int @c(shape: {min: 1}) b: Int }"
        assert text[_matching_brace(text, 0)] == "}"
        assert _matching_brace(text, 0) == len(text) - 1

    def test_unterminated_returns_minus_one(self) -> None:
        assert _matching_brace("{ a: Int", 0) == -1


class TestDefinitionsInBlock:
    def test_type_and_its_fields_are_found(self) -> None:
        block = "\ntype Book {\n  title: String\n  author: String\n}\n"
        (definition,) = _definitions_in_block(block, block_line=10)
        assert (definition.kind, definition.name) == ("type", "Book")
        assert definition.start_line == 11
        assert definition.end_line == 14
        assert definition.fields == [("title", 12), ("author", 13)]

    def test_interface_is_found(self) -> None:
        (definition,) = _definitions_in_block("interface Node {\n id: ID\n}", 1)
        assert definition.kind == "interface"

    def test_extend_type_is_found(self) -> None:
        """``extend type Query`` declares fields a resolver implements
        identically to a bare definition."""
        (definition,) = _definitions_in_block("extend type Query {\n a: Int\n}", 1)
        assert (definition.kind, definition.name) == ("type", "Query")

    def test_a_field_with_inline_arguments_is_found(self) -> None:
        (definition,) = _definitions_in_block("type Q {\n posts(n: Int): [P]\n}", 1)
        assert definition.fields == [("posts", 2)]

    def test_a_commented_out_definition_mints_nothing(self) -> None:
        """A ``gql`` template is a STRING to the host language, so the shared
        doc-region masking never reaches inside it."""
        assert _definitions_in_block("# type Ghost {\n#  a: Int\n# }", 1) == []

    def test_an_unterminated_definition_claims_nothing(self) -> None:
        assert _definitions_in_block("type Truncated {\n  a: Int", 1) == []

    def test_two_definitions_in_one_block(self) -> None:
        block = "type A {\n x: Int\n}\ntype B {\n y: Int\n}\n"
        assert [d.name for d in _definitions_in_block(block, 1)] == ["A", "B"]

    def test_a_block_with_no_definition_yields_nothing(self) -> None:
        assert _definitions_in_block("query GetUser { user { id } }", 1) == []

    def test_input_and_enum_are_deliberately_not_minted(self) -> None:
        """Only the kinds a consumer reads. Minting nodes nothing can link to
        is the defect this row is about, one level down."""
        block = "input NewUser {\n name: String\n}\nenum Role {\n ADMIN\n}"
        assert _definitions_in_block(block, 1) == []


class TestExtractEmbeddedSdl:
    def test_symbols_are_class_a_graphql_declarations(self, tmp_path: Path) -> None:
        """ADR-0031: a real declaration in a real language with a real span is
        Class A, not a synthetic stand-in — the ``vue_component`` and
        ``grpc.py`` proto-scan precedents."""
        _write(tmp_path, "src/schema.ts", "export const t = gql`\ntype Book {\n title: String\n}\n`;\n")
        symbols, files, definitions = extract_embedded_sdl(tmp_path)
        assert files == 1
        assert definitions == 1
        assert {s.kind for s in symbols} == {"type", "field"}
        for symbol in symbols:
            assert symbol.language == "graphql"
            assert symbol.protocol_origin is None
            assert symbol.path == "src/schema.ts"
            assert symbol.origin == [PASS_ID]

    def test_identity_is_stamped_here_not_left_to_the_backstop(
        self, tmp_path: Path,
    ) -> None:
        """``populate_kind_stable_ids`` runs in the analyzer orchestrator,
        BEFORE linkers, and has no ``field`` factory in any case — a symbol
        minted at this layer would never reach it."""
        _write(tmp_path, "s.ts", "gql`\ntype Book {\n title: String\n}\n`")
        symbols, _, _ = extract_embedded_sdl(tmp_path)
        assert all(s.stable_id for s in symbols)
        assert len({s.stable_id for s in symbols}) == len(symbols)

    def test_a_field_carries_the_parent_type_the_resolver_linker_reads(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "s.ts", "gql`\ntype Book {\n title: String\n}\n`")
        symbols, _, _ = extract_embedded_sdl(tmp_path)
        (field,) = [s for s in symbols if s.kind == "field"]
        assert field.meta["parent_type"] == "Book"
        assert field.name == "title"
        assert field.qualified_name == "Book.title"

    def test_fields_of_two_types_do_not_collide(self, tmp_path: Path) -> None:
        """A bare field name repeats across every type in a schema, so the
        stable_id is parent-qualified."""
        _write(tmp_path, "s.ts", "gql`\ntype A {\n id: ID\n}\ntype B {\n id: ID\n}\n`")
        symbols, _, _ = extract_embedded_sdl(tmp_path)
        fields = [s for s in symbols if s.kind == "field"]
        assert len(fields) == 2
        assert len({s.stable_id for s in fields}) == 2

    def test_a_file_without_the_tag_is_scanned_but_mints_nothing(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "plain.ts", "export const x = 1;\n")
        symbols, files, definitions = extract_embedded_sdl(tmp_path)
        assert (files, definitions, symbols) == (1, 0, [])

    def test_a_python_host_file_is_read_with_the_python_form(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "s.py", 'S = gql("""\ntype Book {\n title: String\n}\n""")\n')
        symbols, _, definitions = extract_embedded_sdl(tmp_path)
        assert definitions == 1
        assert [s.name for s in symbols if s.kind == "type"] == ["Book"]

    def test_a_typescript_interface_is_not_mistaken_for_sdl(
        self, tmp_path: Path,
    ) -> None:
        """Scanning source text directly for ``type X {`` was tried and is
        wrong: on apollo-server it matches every TS ``interface ITrace {``."""
        _write(tmp_path, "types.ts", "export interface ITrace {\n id: string;\n}\n")
        symbols, _, definitions = extract_embedded_sdl(tmp_path)
        assert (definitions, symbols) == (0, [])


class TestLinkerEntryPoint:
    def test_it_is_registered_and_imported_by_cli(self) -> None:
        """A registry populated by import side-effect is empty until something
        imports it, and every lookup then misses QUIETLY — so this asserts the
        NAMED entry, not a count."""
        by_id = {p.id: p for p in get_default_catalog().passes}
        assert "graphql-sdl-linker" in by_id
        assert by_id["graphql-sdl-linker"].depends_on == []

    def test_it_is_an_infrastructure_linker_with_no_pass_dependency(self) -> None:
        """Both halves are deliberate and the WI-dilab closure criterion reads
        them together.

        ADR-3bbb's Infrastructure definition is "graph-structural utilities run
        as Tier 2 passes but NOT doing dispatch recovery" — the second clause is
        the discriminator, and this pass emits zero edges and resolves no
        dispatch. Its two consumers are Framework linkers; it is the substrate
        they join against, the same relation ``inheritance-linker`` has to
        ``type-hierarchy-linker``.

        That classification is what makes ``depends_on=[]`` honest rather than
        lazy: the criterion requires a non-empty declaration from Bridge /
        Framework / Protocol linkers, and this pass consumes NO upstream pass
        output — it reads source files itself. Declaring the host languages
        would be the too-wide false claim that WI-rasal spent two PRs removing.
        """
        import hypergumbo_core.linkers.graphql_sdl as module
        from hypergumbo_core.linkers.registry import _LINKER_REGISTRY

        assert module.__doc__ is not None
        assert module.__doc__.startswith("Infrastructure linker:")
        assert _LINKER_REGISTRY["graphql-sdl-linker"].name == "graphql-sdl-linker"
        by_id = {p.id: p for p in get_default_catalog().passes}
        assert by_id["graphql-sdl-linker"].depends_on == []

    def test_it_runs_before_the_two_linkers_that_consume_it(self) -> None:
        """Linkers in one priority group are snapshotted together and cannot
        see each other's output; both consumers sit at 60."""
        from hypergumbo_core.linkers.registry import _LINKER_REGISTRY

        assert _LINKER_REGISTRY["graphql-sdl-linker"].priority < min(
            _LINKER_REGISTRY["graphql-linker"].priority,
            _LINKER_REGISTRY["graphql-resolver-linker"].priority,
        )

    def test_the_run_reports_files_and_a_body_supplied_silence_reason(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "plain.ts", "export const x = 1;\n")
        result = graphql_sdl_linker(LinkerContext(repo_root=tmp_path, symbols=[], edges=[]))
        assert result.run is not None
        assert result.run.files_analyzed == 1
        assert result.run.silence_reason == "no_candidate_construct"
        assert result.edges == []

    def test_a_run_that_found_definitions_claims_candidates_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """The body claims what its own scan FOUND, not what it emitted.

        ``silence_reason_for_candidates`` never returns ``""``; the registry
        chokepoint clears a body claim when the pass emitted, so the body does
        not have to know its own output. This calls the linker directly, which
        is upstream of that chokepoint, so the raw claim is what is visible
        here.
        """
        _write(tmp_path, "s.ts", "gql`\ntype Book {\n title: String\n}\n`")
        result = graphql_sdl_linker(LinkerContext(repo_root=tmp_path, symbols=[], edges=[]))
        assert result.run is not None
        assert result.run.silence_reason == "candidates_unresolved"
        assert result.symbols
        assert all(s.origin_run_id == result.run.execution_id for s in result.symbols)


class TestTheDeadImplementsBranchNowHasAProducer:
    """The finding that made this a defect rather than a coverage gap.

    ``graphql_resolver.py`` builds its ``type.field`` lookup from symbols with
    ``kind == "field"`` carrying ``meta["parent_type"]``. No producer anywhere
    in the tree emitted a GraphQL field symbol — the analyzer emits type /
    input / interface / enum / scalar / union / directive / fragment /
    operation and never ``field`` — so ``schema_lookup`` was always empty and
    the ``implements`` branch was unreachable on every repository. Its 100%
    coverage came from tests that hand-construct the symbol.
    """

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        _write(
            tmp_path, "src/schema.ts",
            "export const typeDefs = gql`\n"
            "  type Query {\n"
            "    books: [Book]\n"
            "  }\n"
            "  type Book {\n"
            "    title: String\n"
            "  }\n"
            "`;\n",
        )
        _write(
            tmp_path, "src/resolvers.ts",
            "export const resolvers = {\n"
            "  Query: {\n"
            "    books: () => db.books(),\n"
            "  },\n"
            "};\n",
        )
        return tmp_path

    def test_without_the_sdl_pass_the_resolver_linker_emits_nothing(
        self, repo: Path,
    ) -> None:
        """The state this row was filed about: resolvers detected, 0 edges."""
        result = graphql_resolver_linker(
            LinkerContext(repo_root=repo, symbols=[], edges=[])
        )
        assert result.symbols
        assert result.edges == []

    def test_with_it_the_resolver_linker_emits_implements_and_references(
        self, repo: Path,
    ) -> None:
        schema_symbols, _, _ = extract_embedded_sdl(repo)
        result = graphql_resolver_linker(
            LinkerContext(repo_root=repo, symbols=schema_symbols, edges=[])
        )
        kinds = {edge.edge_type for edge in result.edges}
        assert "implements" in kinds
        assert "references" in kinds
        implements = [e for e in result.edges if e.edge_type == "implements"]
        assert (implements[0].meta or {})["field_name"] == "books"
        assert (implements[0].meta or {})["type_name"] == "Query"
