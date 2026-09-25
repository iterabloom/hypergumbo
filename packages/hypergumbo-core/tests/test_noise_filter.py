# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the default-view noise predicate (``noise_filter``).

Covers every branch of :func:`is_noise_symbol`, with the WI-papag focus on the
``entry_role=script`` split: npm ``package.json`` run-scripts (no
``entry_point``) are noise, while pyproject ``[project.scripts]`` console-scripts
(a declared ``entry_point`` target) are entrypoint-bearing and must survive.
"""
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.noise_filter import is_noise_symbol


def _sym(kind, language="python", meta=None):
    return Symbol(
        id=f"{language}:f:1-1:x:{kind}",
        name="x",
        kind=kind,
        language=language,
        path="f",
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        meta=meta,
    )


def test_noise_kinds_are_filtered():
    for kind in ("section", "pattern", "requirement", "label"):
        assert is_noise_symbol(_sym(kind)) is True


def test_a_property_is_noise_only_for_its_declaration_only_producers():
    """WI-tisom: ``property`` is a homonym. A swift computed property and a C#
    property are callables with bodies; a ``.properties`` key, a QML property
    and an objective-c ``@property`` are declarations with no body. Measured on
    the corpus, the first two carried 12,916 edges on five swift repos (all
    dropped with their src), the last three 0 edges."""
    for language in ("properties", "qml", "objc"):
        assert is_noise_symbol(_sym("property", language=language)) is True, language
    for language in ("swift", "csharp", "kotlin", "typescript"):
        assert is_noise_symbol(_sym("property", language=language)) is False, language


def test_every_property_producer_is_classified():
    """A module that emits ``kind="property"`` must say whether its property is
    noise, so a new producer cannot inherit the drop silently. That is how swift
    and C# came to lose theirs: the entry was written for a CSS kind that no CSS
    analyzer ever emitted."""
    import re
    from pathlib import Path

    from hypergumbo_core.noise_filter import _NOISE_PROPERTY_LANGUAGES

    root = Path(__file__).resolve().parents[2]
    emits = re.compile(r"""kind\s*=\s*["']property["']""")
    # The analyzers live in the language packages. Core's SCIP importer maps
    # SCIP's ``Property`` to the kind for any language, and that passes through.
    found = {p.stem for p in root.glob("hypergumbo-lang-*/src/*/*.py")
             if emits.search(p.read_text(encoding="utf-8"))}
    assert "swift" in found, "reach: the scan must see a known producer"
    classified = {"swift": False, "csharp": False,
                  "objc": True, "properties": True, "qml": True}
    assert found == set(classified), (
        f"unclassified: {sorted(found - set(classified))}; stale: {sorted(set(classified) - found)}")
    for module, noise in classified.items():
        assert (module in _NOISE_PROPERTY_LANGUAGES) is noise, module


def test_css_variable_is_noise_but_other_variables_survive():
    assert is_noise_symbol(_sym("variable", language="css")) is True
    assert is_noise_symbol(_sym("variable", language="scss")) is True
    # WI-gafog E2: a Python/Go top-level binding is real, not noise.
    assert is_noise_symbol(_sym("variable", language="python")) is False


def test_toml_table_is_noise_but_sql_table_survives():
    # INV-bovif: config-language `table` (section header) is noise; SQL
    # `CREATE TABLE` is a first-class schema entity.
    assert is_noise_symbol(_sym("table", language="toml")) is True
    assert is_noise_symbol(_sym("table", language="ini")) is True
    assert is_noise_symbol(_sym("table", language="sql")) is False


def test_npm_run_script_is_noise():
    # package.json "scripts" (build/test/lint): shell command, no entry_point.
    sym = _sym("file", language="json",
               meta={"entry_role": "script", "script_name": "build",
                     "command": "webpack --mode production"})
    assert is_noise_symbol(sym) is True


def test_pyproject_console_script_is_not_noise():
    # WI-papag: [project.scripts] declares a code target (entry_point) and is
    # detected as CLI_COMMAND @0.99 — entrypoint-bearing, must survive.
    sym = _sym("file", language="toml",
               meta={"entry_role": "script", "entry_point": "mypkg.cli:main"})
    assert is_noise_symbol(sym) is False


def test_plain_file_symbol_is_not_noise():
    assert is_noise_symbol(_sym("file", language="python", meta=None)) is False
    assert is_noise_symbol(_sym("file", language="python", meta={})) is False


def test_file_with_non_script_entry_role_is_not_noise():
    # Only entry_role=script is a noise candidate; entry_role=main survives.
    sym = _sym("file", language="json", meta={"entry_role": "main"})
    assert is_noise_symbol(sym) is False


def test_regular_code_symbol_is_not_noise():
    assert is_noise_symbol(_sym("function")) is False
    assert is_noise_symbol(_sym("class")) is False
