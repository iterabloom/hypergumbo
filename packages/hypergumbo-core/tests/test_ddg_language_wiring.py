# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate: a registered def/use extractor must actually produce DDG edges.

WHY A GATE AND NOT A SWEEP. Four independent things must all be true before a
language's def/use extractor has any effect, and each one alone is sufficient
to make it silently inert:

  1. ``cfg_nodes/<lang>.yaml`` declares ``atomic_statement``. Without it
     ``CfgBuilder`` treats every statement as a compound node and recurses to
     bare identifiers, so an extractor keyed on statement node types is never
     handed one. The CFG is still structurally correct and fully reachable,
     which is why the existing CFG tests cannot see this.
  2. A ``LanguageDdgSpec`` is registered, or ``build_repo_ddg`` skips the
     language outright.
  3. The module that registers the extractor is imported on the production
     path — registration happens as an import side effect, so a module nobody
     imports registers nothing.
  4. The whole chain actually yields an edge on real source.

Rust and TypeScript shipped extractors, tests and 100% coverage while failing
(1), (2) and (3) simultaneously. Fixing the three sites by hand would leave
the next language to rediscover the same four-way trap, so this asserts the
*property* for every registered extractor at once — present and future.

WHY (3) IS AN EXHAUSTIVENESS TEST RATHER THAN DYNAMIC DISCOVERY. The
production path names its imports explicitly, which is worth keeping: it is
greppable and it keeps import cost visible. But an explicit list is a second
home for the fact "which def/use modules exist", and the filesystem is the
first. Rather than replace the list with a scan, the list is *declared* and a
test fails when the two disagree — the same declare-the-scope-as-data shape the
G2 parity matrix uses, and the reason a new analyzer cannot silently shrink
what "full coverage" means.

NON-VACUITY. Every assertion here is quantified over "languages with a
registered def/use extractor". If registration itself broke, that set would be
empty and every check would pass over nothing — so the set is asserted
non-empty first, and asserted to contain the languages we know shipped
extractors. A gate whose population can silently become zero is not a gate.

SCOPE, DECLARED RATHER THAN IMPLIED. This gate is blind by construction to a
language that has *no* extractor at all, because that language is not in its
population. Two such languages carry substantial taint catalogs today and
neither is failed by anything here:

  javascript   50 sources, 83 sinks — no cfg mapping under its own key, no
               extractor, no spec. ``cfg_nodes/typescript.yaml`` states it
               "also covers JavaScript", but it is keyed to ``typescript``,
               so a ``.js`` file reaches none of this machinery.
  java         45 sources, 69 sinks — has a cfg mapping, but no def/use
               extractor exists to write one for.

So "every registered extractor is wired" is a regression guard, not a coverage
claim. The complement — how much of the sink catalog is data-flow adjudicable
at all — is a published coverage table, not a test, precisely because it is a
disclosure rather than a pass/fail.
"""
from pathlib import Path

import pytest
import yaml

from hypergumbo_core import cfg as cfg_mod
from hypergumbo_core.cfg import get_def_use_extractor
from hypergumbo_core.ddg_build import build_repo_ddg, get_ddg_language

#: Languages known to ship a def/use extractor. Declared rather than derived so
#: that a registration that silently stops happening fails loudly here instead
#: of shrinking every quantified assertion below to a vacuous pass.
EXPECTED_DEF_USE_LANGUAGES = frozenset({"python", "go", "rust", "typescript"})

#: One minimal function per language whose body defines a local and then uses
#: it in a *second* definition — the smallest shape that must yield a
#: reaching-def edge.
#:
#: The def and the use deliberately sit in two ordinary assignments rather than
#: in an assignment and a ``return``. Every one of these languages classifies
#: ``return`` as a control-flow node, and a control-flow node must be kept out
#: of ``atomic_statement`` or the atomic check shadows its control-flow role.
#: A fixture whose only use lives in a ``return`` would therefore be measuring
#: whether the return hook happens to reach the extractor, not whether the
#: language's statements carry def/use at all — and could fail for a reason
#: that has nothing to do with what this gate is for.
DDG_SMOKE_SOURCES: dict[str, tuple[str, str]] = {
    "python": (
        "mod.py",
        "def f(x):\n"
        "    y = x + 1\n"
        "    z = y * 2\n"
        "    return z\n",
    ),
    "go": (
        "mod.go",
        "package p\n"
        "\n"
        "func F(x int) int {\n"
        "\ty := x + 1\n"
        "\tz := y * 2\n"
        "\treturn z\n"
        "}\n",
    ),
    "rust": (
        "mod.rs",
        "fn f(x: i32) -> i32 {\n"
        "    let y = x + 1;\n"
        "    let z = y * 2;\n"
        "    z\n"
        "}\n",
    ),
    "typescript": (
        "mod.ts",
        "export function f(x: number): number {\n"
        "  const y = x + 1;\n"
        "  const z = y * 2;\n"
        "  return z;\n"
        "}\n",
    ),
}

_CFG_NODES_DIR = Path(cfg_mod.__file__).parent / "cfg_nodes"


def _import_production_def_use_modules() -> None:
    """Run the production import path so registration side effects happen."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    # The registrations are import side effects of the language modules that
    # _build_ddg_for_verify_claims imports. Importing the same modules here
    # mirrors production rather than reaching into the registry directly.
    import hypergumbo_lang_mainstream.go_def_use
    import hypergumbo_lang_mainstream.py_def_use
    import hypergumbo_lang_mainstream.rust_def_use
    import hypergumbo_lang_mainstream.ts_def_use


@pytest.fixture(scope="module", autouse=True)
def _registered() -> None:
    _import_production_def_use_modules()


def test_expected_languages_have_a_registered_extractor() -> None:
    """The population every other test quantifies over is non-empty and known.

    Without this, deleting a registration would make the assertions below pass
    over an empty set — the gate would go green precisely when it broke.
    """
    missing = {
        lang for lang in EXPECTED_DEF_USE_LANGUAGES
        if get_def_use_extractor(lang) is None
    }
    assert not missing, f"def/use extractor not registered for: {sorted(missing)}"


@pytest.mark.parametrize("language", sorted(EXPECTED_DEF_USE_LANGUAGES))
def test_cfg_mapping_declares_atomic_statement(language: str) -> None:
    """(1) A mapping without atomic_statement yields a CFG of leaf tokens.

    ADR-0017 §1d calls this list load-bearing for any language shipping a
    def/use extractor, and predicts exactly this failure — the invariant was
    documented and unenforced.
    """
    mapping_path = _CFG_NODES_DIR / f"{language}.yaml"
    assert mapping_path.is_file(), f"no cfg_nodes mapping for {language}"

    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    atomic = mapping.get("atomic_statement")
    assert atomic, (
        f"{language}.yaml declares no atomic_statement, so CfgBuilder will "
        f"recurse past its statements to bare identifiers and the "
        f"{language} def/use extractor will never be handed a statement node"
    )


@pytest.mark.parametrize("language", sorted(EXPECTED_DEF_USE_LANGUAGES))
def test_ddg_language_spec_is_registered(language: str) -> None:
    """(2) build_repo_ddg skips any language with no registered spec."""
    assert get_ddg_language(language) is not None, (
        f"no LanguageDdgSpec registered for {language}; build_repo_ddg will "
        f"skip it even with a working extractor and a correct mapping"
    )


@pytest.mark.parametrize("language", sorted(EXPECTED_DEF_USE_LANGUAGES))
def test_language_yields_at_least_one_ddg_edge(
    language: str, tmp_path: Path,
) -> None:
    """(4) The end-to-end property the other three exist to serve.

    Asserted at the pipeline level rather than the extractor level, because an
    extractor correct in isolation is precisely what every layer of this bug
    looked like.
    """
    filename, source = DDG_SMOKE_SOURCES[language]
    (tmp_path / filename).write_text(source, encoding="utf-8")

    result = build_repo_ddg(tmp_path, (language,))

    assert result.ddg_edges, (
        f"{language}: a function defining a local and then using it produced "
        f"zero DDG edges"
    )
    assert result.ddg_symbols, f"{language}: no symbol carried DDG coverage"


@pytest.mark.parametrize("language", sorted(EXPECTED_DEF_USE_LANGUAGES))
def test_language_yields_statement_level_def_use(
    language: str, tmp_path: Path,
) -> None:
    """(5) Edges alone are not enough to CONFIRM anything (INV-sadah).

    A ``DdgEdge`` says "variable v defined at line D is used at line U". It
    does not say which variable defined at U inherited v — and when one line
    defines two, the §3a walk cannot tell a real dependence from an accident
    of shared line numbers. That is how a ``precise`` label came to be stamped
    on a flow with no data dependence at all.

    The walk therefore consumes ``RepoDdg.stmt_defuse``, and a language that
    emits edges but annotates no statements would lose every confirmation
    SILENTLY — flows keep being reported, only the label quietly degrades.
    That is the ADR-0017 inertness shape exactly (a capability with no
    production effect, passing its own tests), so it gets a gate rather than a
    note. Sibling of property (4): that one asserts the edges exist, this one
    asserts they can be interpreted.
    """
    filename, source = DDG_SMOKE_SOURCES[language]
    (tmp_path / filename).write_text(source, encoding="utf-8")

    result = build_repo_ddg(tmp_path, (language,))

    assert result.stmt_defuse, (
        f"{language}: produced DDG edges but no statement-level defines/uses, "
        f"so the §3a walk can never justify a hop and every finding in this "
        f"language falls back to 'approximate'"
    )
    assert any(
        defines and uses
        for statements in result.stmt_defuse.values()
        for _line, defines, uses in statements
    ), (
        f"{language}: statements were recorded but none both DEFINES and USES "
        f"anything, which is the shape a taint hop is made of"
    )


#: A branch per language, used to prove that declaring a type
#: ``atomic_statement`` did not swallow the language's control flow.
CONTROL_FLOW_SOURCES: dict[str, str] = {
    "python":
        "def f(x):\n"
        "    if x > 0:\n"
        "        y = 1\n"
        "    else:\n"
        "        y = 2\n"
        "    return y\n",
    "go":
        "package p\n"
        "\n"
        "func F(x int) int {\n"
        "\tif x > 0 {\n"
        "\t\treturn 1\n"
        "\t} else {\n"
        "\t\treturn 2\n"
        "\t}\n"
        "}\n",
    "rust":
        "fn f(x: i32) -> i32 {\n"
        "    if x > 0 { 1 } else { 2 }\n"
        "}\n",
    "typescript":
        "function f(x: number): number {\n"
        "  if (x > 0) { return 1; } else { return 2; }\n"
        "}\n",
}


def _find_function_body(node: object, node_types: frozenset) -> object:
    """Depth-first search for the first function node's body."""
    if getattr(node, "type", None) in node_types:
        body = node.child_by_field_name("body")  # type: ignore[attr-defined]
        if body is not None:
            return body
    for child in getattr(node, "named_children", []):
        found = _find_function_body(child, node_types)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("language", sorted(EXPECTED_DEF_USE_LANGUAGES))
def test_atomic_statement_does_not_swallow_control_flow(language: str) -> None:
    """Declaring a type atomic must not erase the branch it wraps.

    ``atomic_statement`` tells the builder to stop descending so that a def/use
    extractor is handed a whole statement. In a statement-oriented grammar that
    is always safe. In an expression-oriented one it is not: Rust parses
    ``if c { } else { }`` as ``expression_statement > if_expression``, so
    declaring ``expression_statement`` atomic — which Rust must, to get def/use
    on bare calls — stopped the descent before the branch was classified and
    the CFG lost its true/false edges entirely.

    Nothing about DDG edge counts can see that: the wrapper was declared *for*
    def/use, so def/use kept working while control flow silently vanished. This
    asserts the property the edge-count checks cannot, for every language at
    once, so the next expression-oriented grammar does not rediscover it.
    """
    import tree_sitter
    from tree_sitter_language_pack import get_language

    from hypergumbo_core.cfg import build_function_cfg, load_cfg_mapping

    spec = get_ddg_language(language)
    assert spec is not None, f"no spec for {language}"
    mapping = load_cfg_mapping(language)
    assert mapping is not None, f"no cfg mapping for {language}"

    parser = tree_sitter.Parser(get_language(language))
    source = CONTROL_FLOW_SOURCES[language].encode("utf-8")
    body = _find_function_body(
        parser.parse(source).root_node, spec.function_node_types,
    )
    assert body is not None, f"{language}: no function body found in fixture"

    cfg = build_function_cfg(
        body, source, mapping, f"{language}:t:1-9:f:function",
    )
    branching = [
        block for block in cfg.blocks.values()
        if {"true", "false"} <= {e.edge_type for e in block.successors}
    ]
    assert branching, (
        f"{language}: an if/else produced no block with both true and false "
        f"successors — an atomic_statement declaration is swallowing the "
        f"branch it wraps"
    )


def test_production_import_list_covers_every_def_use_module() -> None:
    """(3) A module nobody imports registers nothing.

    ``dataflow_scope.ensure_def_use_extractors_registered`` names its imports
    explicitly. That is worth keeping — greppable, and import cost stays
    visible — but it is a second home for a fact the filesystem already holds,
    so the two are pinned together here. Rust and TypeScript sat unimported for
    months behind exactly this gap while both modules existed and both were
    tested.

    The list moved out of ``cli._build_ddg_for_verify_claims`` when the
    INV-karud (a3) coverage table needed the same registries populated at the
    same moment: two force-import sites would be two things to drift, and a
    scope table computed against an empty registry reports every language
    incapable without erroring.
    """
    from hypergumbo_core import dataflow_scope as scope_mod

    lang_pkg = Path(
        __import__("hypergumbo_lang_mainstream").__file__,
    ).parent
    on_disk = {p.stem for p in lang_pkg.glob("*_def_use.py")}
    assert on_disk, "no *_def_use modules found; the glob is wrong"

    registrar_source = Path(scope_mod.__file__).read_text(encoding="utf-8")
    not_imported = {
        module for module in on_disk
        if f"hypergumbo_lang_mainstream.{module}" not in registrar_source
    }
    assert not not_imported, (
        f"def/use modules exist but are never imported on the production "
        f"path, so their registrations never fire: {sorted(not_imported)}"
    )
