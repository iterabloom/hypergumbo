# SPDX-License-Identifier: AGPL-3.0-or-later
"""One JS/TS extension list, read by every linker (WI-hizon, WI-pokij, WI-fovus).

The defect these tests pin is structural: several consumers each carried a
private copy of "which extensions are JS/TS" and "what language is this file",
and the copies drifted. The http / graphql / message_queue linkers never read
``.mjs`` / ``.cjs`` / ``.mts`` / ``.cts`` (message_queue skipped ``.jsx`` /
``.tsx`` too); the annotation-convention linker labelled ``.tsx`` sites with
the GRAMMAR name ``tsx`` and ``.mts`` / ``.cts`` sites ``unknown``.

Three layers:

1. The shared helpers in ``taxonomy`` (the list discovery classifies by).
2. Behaviour, per extension: every JS/TS-scanning linker's discovery reaches
   every JS/TS suffix, its dispatch selects the JS/TS scanner, and a minted
   node carries the analyzer's language tag.
3. The gate: no linker module (nor ``ddg_build``) may hold a private JS/TS
   extension literal, and ``language_from_path`` -- a GRAMMAR lookup -- may be
   called only by the modules listed here. Each gate has a control that feeds
   it a private literal and watches it fire.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import hypergumbo_core
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.taxonomy import (
    JS_TS_LANGUAGES,
    extension_globs,
    extension_suffixes,
    get_language,
    grammar_for_path,
    js_ts_language_for_path,
)

#: Every suffix the JS/TS analyzer reads, with the tag it gives the file.
#: Written out (not derived) so the helpers are tested against a fixed table.
ANALYZER_TAG = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
}
SUFFIXES = sorted(ANALYZER_TAG)

_CORE = Path(hypergumbo_core.__file__).parent
_LINKERS = _CORE / "linkers"


# ---------------------------------------------------------------------------
# 1. Shared helpers
# ---------------------------------------------------------------------------

class TestSharedHelpers:
    def test_js_ts_globs_are_the_analyzer_set_without_compound_duplicates(self) -> None:
        globs = extension_globs(*JS_TS_LANGUAGES)
        assert sorted(globs) == sorted(f"*{s}" for s in SUFFIXES)
        # ``*.d.ts`` is in LANGUAGES but covered by ``*.ts``; the rglob fallback
        # of find_files does not de-duplicate, so it must not be listed twice.
        assert "*.d.ts" not in globs
        assert len(globs) == len(set(globs))

    def test_suffixes_match_globs(self) -> None:
        assert extension_suffixes(*JS_TS_LANGUAGES) == frozenset(SUFFIXES)
        assert extension_suffixes("typescript") == frozenset({".ts", ".tsx", ".mts", ".cts"})

    def test_compound_glob_kept_when_its_last_suffix_is_not_listed(self) -> None:
        """``*.lagda.md`` stays: agda lists no ``*.md`` to cover it."""
        assert "*.lagda.md" in extension_globs("agda")

    def test_exact_filename_entries_are_not_globs(self) -> None:
        assert all(g.startswith("*.") for g in extension_globs("dockerfile"))

    def test_unknown_language_raises(self) -> None:
        with pytest.raises(KeyError):
            extension_globs("no-such-language")

    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_js_ts_language_for_path_is_the_analyzer_tag(self, suffix: str) -> None:
        assert js_ts_language_for_path(Path(f"a{suffix}")) == ANALYZER_TAG[suffix]
        assert js_ts_language_for_path(Path(f"A{suffix.upper()}")) == ANALYZER_TAG[suffix]

    def test_js_ts_language_for_path_else_branch(self) -> None:
        """The analyzer's else-branch: a Vue/Svelte script reads as javascript."""
        assert js_ts_language_for_path(Path("a.vue")) == "javascript"
        assert js_ts_language_for_path(Path("types.d.ts")) == "typescript"

    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_discovery_classifier_agrees(self, suffix: str) -> None:
        assert get_language(Path(f"a{suffix}")) == ANALYZER_TAG[suffix]

    def test_grammar_for_path(self) -> None:
        assert grammar_for_path(Path("a.tsx"), "typescript") == "tsx"
        assert grammar_for_path(Path("a.TSX"), "typescript") == "tsx"
        assert grammar_for_path(Path("a.mts"), "typescript") == "typescript"
        assert grammar_for_path(Path("a.jsx"), "javascript") == "javascript"

    def test_text_filters_label_helper_delegates(self) -> None:
        from hypergumbo_core.linkers._text_filters import js_ts_language_from_path

        for suffix in SUFFIXES:
            assert js_ts_language_from_path(Path(f"a{suffix}")) == ANALYZER_TAG[suffix]

    def test_masking_grammar_covers_every_js_ts_suffix(self, tmp_path: Path) -> None:
        """A linker that reads ``.mts`` / ``.cts`` must get their comments
        masked: the hand-written grammar map lacked both, so a ``fetch`` in a
        comment there was scanned as code."""
        from hypergumbo_core.linkers._text_filters import language_from_path, read_masked_source

        for suffix in SUFFIXES:
            assert language_from_path(Path(f"a{suffix}")) is not None, suffix
            f = tmp_path / f"m{suffix}"
            f.write_text("// fetch('/api/hidden')\nconst x = 1;\n")
            assert "/api/hidden" not in read_masked_source(f), suffix


# ---------------------------------------------------------------------------
# 2. Behaviour per extension
# ---------------------------------------------------------------------------

def _one_file_per_suffix(root: Path, body: str) -> None:
    for i, suffix in enumerate(SUFFIXES):
        (root / f"src{i}{suffix}").write_text(body.replace("@N@", str(i)))


def _suffixes(paths: object) -> set[str]:
    return {Path(str(p)).suffix for p in paths}  # type: ignore[attr-defined]


def _finders() -> list[tuple[str, object]]:
    from hypergumbo_core.linkers import (
        database_query, event_sourcing, graphql, graphql_resolver, graphql_sdl,
        grpc, http, ipc, message_queue, websocket,
    )
    return [
        ("http", http._find_source_files),
        ("graphql", graphql._find_source_files),
        ("graphql_sdl", graphql_sdl._find_source_files),
        ("graphql_resolver", graphql_resolver._find_source_files),
        ("database_query", database_query._find_source_files),
        ("event_sourcing", event_sourcing._find_source_files),
        ("message_queue", message_queue._find_source_files),
        ("grpc", grpc._find_grpc_files),
        ("ipc", ipc._find_js_files),
        ("websocket", websocket.find_js_ts_files),
    ]


class TestDiscoveryReach:
    @pytest.mark.parametrize("name,finder", _finders(), ids=lambda v: v if isinstance(v, str) else "")
    def test_every_js_ts_suffix_is_discovered(self, tmp_path: Path, name: str, finder) -> None:
        _one_file_per_suffix(tmp_path, "const x@N@ = 1;\n")
        assert _suffixes(finder(tmp_path)) >= set(SUFFIXES), name


class TestDispatchSelectsTheJsTsScanner:
    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_detect_language_selectors(self, suffix: str) -> None:
        from hypergumbo_core.linkers import (
            database_query, event_sourcing, graphql_resolver, message_queue,
        )

        p = Path(f"a{suffix}")
        assert graphql_resolver._detect_language(p) == "javascript"
        assert database_query._detect_language(p) == "javascript"
        assert event_sourcing._detect_language(p) == "javascript"
        # message_queue's value IS the label: the analyzer's tag.
        assert message_queue._detect_language(p) == ANALYZER_TAG[suffix]


class TestMintedLanguage:
    """End to end through each linker's public entry: a node is minted for
    every suffix, and it carries the analyzer's tag."""

    def test_message_queue(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.message_queue import link_message_queues

        _one_file_per_suffix(tmp_path, "producer.send({ topic: 'orders@N@', messages: [] });\n")
        result = link_message_queues(tmp_path)
        got = {Path(s.path).suffix: s.id.split(":", 1)[0] for s in result.symbols}
        assert got == ANALYZER_TAG

    def test_http(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.http import link_http

        _one_file_per_suffix(tmp_path, "export const f = () => fetch('/api/items@N@');\n")
        result = link_http(tmp_path, [])
        got = {Path(s.path).suffix: s.discovery_language for s in result.symbols}
        assert got == ANALYZER_TAG

    def test_grpc(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.grpc import link_grpc

        _one_file_per_suffix(tmp_path, "const c = new Orders@N@Client(addr);\n")
        result = link_grpc(tmp_path)
        got = {Path(s.path).suffix: s.language for s in result.symbols}
        assert got == ANALYZER_TAG

    def test_solidity_abi_scans_every_suffix(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        _one_file_per_suffix(tmp_path, "await token.transfer(to, amount);\n")
        found = _scan_contract_calls(tmp_path, {"transfer"})
        assert {Path(rel).suffix for rel, _, _ in found} == set(SUFFIXES)

    @pytest.mark.parametrize("suffix", [".ts", ".tsx", ".mts", ".cts"])
    def test_di_reads_every_typescript_suffix(self, tmp_path: Path, suffix: str) -> None:
        from hypergumbo_core.linkers.di_resolution import extract_bindings_from_source

        (tmp_path / f"app.module{suffix}").write_text(
            "@Module({ providers: [{ provide: Repo, useClass: SqlRepo }] })\n"
            "export class AppModule {}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert ("Repo", "SqlRepo") in {(b.interface_name, b.impl_name) for b in bindings}


def _sym(path: str, language: str) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-10:test:function", name="test", kind="function",
        language=language, path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="test", origin_run_id="uuid:test",
    )


class TestAnnotationLabels:
    """WI-pokij: the slot is the host file's language, never a grammar name."""

    def test_every_js_ts_suffix_is_labelled_with_the_analyzer_tag(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.annotation_convention import link_annotations

        _one_file_per_suffix(
            tmp_path,
            "// @hg:route GET /api/x@N@\n// @hg:publishes ch@N@\nfunction a() {}\n"
            "// @hg:subscribes ch@N@\nfunction b() {}\n// @hg:dispatches a\n",
        )
        symbols = [
            _sym(f"src{i}{s}", ANALYZER_TAG[s]) for i, s in enumerate(SUFFIXES)
        ] + [_sym("other.js", "javascript")]
        symbols[-1].name = "a"  # a dispatch target, so every directive kind mints
        result = link_annotations(tmp_path, symbols)
        minted = [s for s in result.symbols if s.path.startswith("src")]
        # Reach first: four node kinds (route, publisher, subscriber,
        # dispatcher) per file, so no label assertion below is vacuous.
        assert len(minted) == 4 * len(SUFFIXES)
        for s in minted:
            want = ANALYZER_TAG[Path(s.path).suffix]
            assert s.id.split(":", 1)[0] == want, s.id
            assert s.discovery_language == want, s.id

    def test_a_non_js_file_takes_the_taxonomy_language(self, tmp_path: Path) -> None:
        """The grammar map named no language for ``.ex``; the taxonomy does."""
        from hypergumbo_core.linkers.annotation_convention import link_annotations

        (tmp_path / "chan.ex").write_text("# @hg:route GET /x\n")
        result = link_annotations(tmp_path, [_sym("chan.ex", "elixir")])
        assert [s.id.split(":", 1)[0] for s in result.symbols] == ["elixir"]

    def test_an_unclassified_file_stays_unknown(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.annotation_convention import link_annotations

        (tmp_path / "x.nosuchext").write_text("# @hg:route GET /x\n")
        result = link_annotations(tmp_path, [_sym("x.nosuchext", "x")])
        assert [(s.id.split(":", 1)[0], s.discovery_language) for s in result.symbols] == [
            ("unknown", None),
        ]


# ---------------------------------------------------------------------------
# 3. Gates
# ---------------------------------------------------------------------------

#: Private JS/TS extension collections that are NOT "which files are JS/TS":
#: an import-resolution PROBE order (TypeScript / bundler semantics -- which
#: candidate files an extensionless specifier tries, in order). Keyed by the
#: assignment target or enclosing function, never a line number.
_ALLOWED_PRIVATE_LISTS = {
    ("js_module.py", "_PROBE_EXTENSIONS"),
    ("tauri_ipc.py", "_resolve_import_path"),
}

#: Modules allowed to call ``language_from_path`` (a GRAMMAR lookup). The
#: masker needs a grammar; websocket maps it through the JS/TS tag; the other
#: three still use it as a LABEL for non-JS/TS files and are filed residuals --
#: this set may only shrink.
_ALLOWED_GRAMMAR_LOOKUP_CALLERS = {
    "_text_filters.py", "websocket.py", "phoenix_ipc.py", "openapi.py", "subprocess_cli.py",
}


def _js_ts_token(value: object) -> bool:
    if not isinstance(value, str):
        return False
    tail = value.rsplit("/", 1)[-1].lstrip("*")
    return tail.lower() in extension_suffixes(*JS_TS_LANGUAGES)


def private_js_ts_lists(source: str, filename: str) -> set[tuple[str, str]]:
    """``(filename, owner)`` for each collection literal holding a JS/TS
    extension token; owner is the assignment target or enclosing function."""
    tree = ast.parse(source)
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names and owner == "<module>":
                owner = names[0]
        elems: list[ast.AST] = []
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            elems = list(node.elts)
        elif isinstance(node, ast.Dict):
            elems = [k for k in node.keys if k is not None]
        if any(isinstance(e, ast.Constant) and _js_ts_token(e.value) for e in elems):
            found.add((filename, owner))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "<module>")
    return found


def grammar_lookup_callers(source: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "language_from_path"
        for n in ast.walk(ast.parse(source))
    )


def _scanned_modules() -> list[Path]:
    return sorted(_LINKERS.glob("*.py")) + [_CORE / "ddg_build.py"]


class TestGate:
    def test_no_linker_or_ddg_module_holds_a_private_js_ts_list(self) -> None:
        found: set[tuple[str, str]] = set()
        for path in _scanned_modules():
            found |= private_js_ts_lists(path.read_text(encoding="utf-8"), path.name)
        assert found - _ALLOWED_PRIVATE_LISTS == set(), (
            "a private JS/TS extension list: read taxonomy.extension_globs / "
            "extension_suffixes(*JS_TS_LANGUAGES) instead"
        )
        # The allowlist may only shrink: an entry that no longer exists goes.
        assert _ALLOWED_PRIVATE_LISTS - found == set()

    def test_gate_control_fires_on_a_private_list(self) -> None:
        """Mutation control: the shapes the drifted linkers held."""
        for snippet in (
            'patterns = ["**/*.py", "**/*.js", "**/*.ts"]',
            'def f(p):\n    return p.suffix in (".ts", ".js")',
            'EXT = frozenset({".mjs"})',
            'M = {".tsx": 1}',
        ):
            assert private_js_ts_lists(snippet, "x.py"), snippet
        assert not private_js_ts_lists('patterns = ["**/*.py", "**/*.go"]', "x.py")

    def test_grammar_lookup_is_never_a_new_label(self) -> None:
        callers = {
            p.name for p in sorted(_LINKERS.glob("*.py"))
            if grammar_lookup_callers(p.read_text(encoding="utf-8"))
        }
        assert "annotation_convention.py" not in callers
        assert callers - _ALLOWED_GRAMMAR_LOOKUP_CALLERS == set()
        assert _ALLOWED_GRAMMAR_LOOKUP_CALLERS - callers == set()

    def test_grammar_lookup_control(self) -> None:
        assert grammar_lookup_callers("lang = language_from_path(Path(p))")
        assert not grammar_lookup_callers("lang = get_language(Path(p))")
