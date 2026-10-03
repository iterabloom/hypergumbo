# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every JS/TS file the analyzer reads reaches the DDG (WI-fovus).

The javascript and typescript ``LanguageDdgSpec`` registrations globbed
``*.js`` / ``*.ts`` only, so a function in ``.mjs`` / ``.cjs`` / ``.jsx`` /
``.tsx`` / ``.mts`` / ``.cts`` -- a file the analyzer emitted symbols for --
was never walked, and a taint flow through it read ``structural`` by
construction (apollo-server's only finding sat in a ``.mjs`` file;
growthbook is mostly ``.tsx``). Widening the TS glob alone could not fix
``.tsx``: the ``typescript`` grammar does not parse JSX.

The specs now name no glob: they walk ``taxonomy.LANGUAGES``' extensions for
their language, and ``ddg_build`` parses each file with
``taxonomy.grammar_for_path``. These tests pin, per extension:

* reach -- the file's function is walked under the analyzer's language tag;
* JSX -- a value used inside a JSX element is a DDG use, with no coverage
  forfeit and no unaccounted name;
* the gates -- no registered DDG spec narrows its language without a declared
  reason, and the JS/TS analyzer's own extension list and language tag agree
  with the shared helpers (the analyzer keeps its literal on purpose: its
  file ORDER feeds name resolution).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import dataflow_scope
from hypergumbo_core.ddg_build import build_repo_ddg, get_ddg_language, registered_ddg_languages
from hypergumbo_core.taxonomy import (
    JS_TS_LANGUAGES,
    extension_globs,
    extension_suffixes,
    js_ts_language_for_path,
)

ANALYZER_TAG = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
}

_PLAIN = "function f{i}(a) {{\n  const x = a;\n  const y = x + 1;\n  return y;\n}}\n"
_JSX = (
    "function g(a) {\n"
    "  const x = a;\n"
    "  const el = <Foo onClick={x} label=\"t\">{a.name}<Bar.Baz v={x} /></Foo>;\n"
    "  return el;\n"
    "}\n"
)


@pytest.fixture(autouse=True)
def _registered() -> None:
    # INV-zunik: extractors and specs register by import side-effect.
    dataflow_scope.ensure_def_use_extractors_registered()


def test_every_extension_reaches_the_ddg(tmp_path: Path) -> None:
    for i, suffix in enumerate(sorted(ANALYZER_TAG)):
        (tmp_path / f"m{i}{suffix}").write_text(_PLAIN.format(i=i))
    ddg = build_repo_ddg(tmp_path, languages=JS_TS_LANGUAGES)
    walked = {k.split(":")[1]: k.split(":")[0] for k in ddg.ddg_symbols}
    assert {Path(p).suffix: lang for p, lang in walked.items()} == ANALYZER_TAG


@pytest.mark.parametrize(("suffix", "language"), [(".jsx", "javascript"), (".tsx", "typescript")])
def test_a_value_used_inside_jsx_is_a_ddg_use(tmp_path: Path, suffix: str, language: str) -> None:
    (tmp_path / f"c{suffix}").write_text(_JSX)
    ddg = build_repo_ddg(tmp_path, languages=(language,))
    sym = f"{language}:c{suffix}:1-5:g:function"
    assert ddg.ddg_symbols == {sym}  # reach first: parsed, walked, keyed
    edges = {(e.variable, e.def_line, e.use_line) for e in ddg.ddg_edges}
    assert ("x", 2, 3) in edges  # x flows into the JSX attribute
    assert ("el", 3, 4) in edges
    uses_line_3 = next(u for line, _, u in ddg.stmt_defuse[sym] if line == 3)
    assert {"x", "a"} <= set(uses_line_3)
    # The coverage gates see nothing missed: JSX lives inside a recorded
    # statement and its identifiers reach the extractor.
    assert sym not in ddg.forfeit_refutation
    assert not ddg.unaccounted_names.get(sym)


def test_typescript_grammar_alone_cannot_parse_jsx() -> None:
    """Why ``.tsx`` needs ``grammar_for_path``: not a widened glob."""
    import tree_sitter
    from tree_sitter_language_pack import get_language

    tree = tree_sitter.Parser(get_language("typescript")).parse(_JSX.encode())
    assert tree.root_node.has_error
    tree = tree_sitter.Parser(get_language("tsx")).parse(_JSX.encode())
    assert not tree.root_node.has_error


# --- gates -------------------------------------------------------------------

#: Specs that walk fewer extensions than their language declares, and why.
#: May only shrink.
_DECLARED_NARROWINGS = {
    "python": ("*.py",),  # *.pyi stubs: not measured
    "c": ("*.c",),  # *.h inline functions: not measured
}


def private_spec_globs() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for language in sorted(registered_ddg_languages()):
        spec = get_ddg_language(language)
        assert spec is not None
        if spec.file_globs is not None:
            out[language] = spec.file_globs
    return out


def test_no_ddg_spec_narrows_its_language_without_a_declared_reason() -> None:
    assert {"javascript", "typescript", "python", "go", "rust", "java", "c"} <= (
        registered_ddg_languages()
    )
    assert private_spec_globs() == _DECLARED_NARROWINGS


def test_every_spec_walks_only_its_own_language() -> None:
    for language in registered_ddg_languages():
        spec = get_ddg_language(language)
        assert spec is not None
        assert set(spec.globs()) <= set(extension_globs(language)), language


def test_spec_gate_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation control: a JS spec re-growing ``*.js`` is caught."""
    import dataclasses

    import hypergumbo_core.ddg_build as mod

    spec = mod._DDG_LANGUAGES["javascript"]
    monkeypatch.setitem(
        mod._DDG_LANGUAGES, "javascript", dataclasses.replace(spec, file_globs=("*.js",)),
    )
    assert private_spec_globs() != _DECLARED_NARROWINGS


def test_analyzer_finds_exactly_the_shared_extensions(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.js_ts import find_js_ts_files

    for glob in extension_globs(*JS_TS_LANGUAGES):
        (tmp_path / f"a{glob[1:]}").write_text("")
    (tmp_path / "x.vue").write_text("")
    found = {p.suffix for p in find_js_ts_files(tmp_path)}
    assert found == extension_suffixes(*JS_TS_LANGUAGES)


def test_analyzer_patterns_are_the_shared_extensions_plus_sfc() -> None:
    from hypergumbo_lang_mainstream.js_ts import JstsTreeSitterAnalyzer

    patterns = set(JstsTreeSitterAnalyzer.file_patterns)
    assert patterns - {"*.svelte", "*.vue"} == set(extension_globs(*JS_TS_LANGUAGES))


@pytest.mark.parametrize("suffix", sorted(ANALYZER_TAG) + [".vue", ".svelte", ".d.ts"])
def test_analyzer_language_tag_is_the_shared_one(suffix: str) -> None:
    from hypergumbo_lang_mainstream.js_ts import _get_language_for_file

    path = Path(f"a{suffix}")
    assert _get_language_for_file(path) == js_ts_language_for_path(path)
