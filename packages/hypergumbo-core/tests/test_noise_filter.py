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
    for kind in ("section", "property", "pattern", "requirement", "label"):
        assert is_noise_symbol(_sym(kind)) is True


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
