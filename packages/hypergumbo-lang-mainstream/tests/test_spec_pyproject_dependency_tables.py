# SPDX-License-Identifier: AGPL-3.0-or-later
"""The spec names every ``pyproject.toml`` table that yields dependency nodes.

``docs/hypergumbo-spec.md`` section 14, "3a. Dependency declarations", is
where a consumer learns which tables of a manifest become ``kind="dependency"``
nodes and with which ``meta["dependency_scope"]``. That list has one source in
the code, ``py_deps.PYPROJECT_DEPENDENCY_TABLES``. A table added to the code
and not to the spec is read without being disclosed; a table removed from the
code and left in the spec is disclosed without being read, which is how the
section came to say nothing about tables that emitted no node. Both are silent,
so the section is checked against the list here.

It lives in this package's suite because it imports ``py_deps``; the
repo-root suite runs with no hypergumbo package installed.
"""
from __future__ import annotations

import re
from pathlib import Path

from hypergumbo_lang_mainstream.py_deps import PYPROJECT_DEPENDENCY_TABLES

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "docs" / "hypergumbo-spec.md"


def _section_3a() -> str:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("#### 3a. Dependency declarations")
    end = text.index("**Workspace / monorepo detection", start)
    return text[start:end]


def _table_rows() -> dict[str, str]:
    """Key path -> the row's ``dependency_scope`` cell, from the pyproject
    table of 3a (the one whose header starts with ``Key path``)."""
    section = _section_3a()
    table = section[section.index("| Key path"):].split("\n\n", 1)[0]
    rows: dict[str, str] = {}
    for line in table.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        match = re.match(r"`([^`]+)`", cells[0])
        assert match and len(cells) == 3, line
        rows[match.group(1)] = cells[2]
    return rows


def test_spec_lists_exactly_the_tables_the_code_reads() -> None:
    assert set(_table_rows()) == {".".join(t.path) for t in PYPROJECT_DEPENDENCY_TABLES}


def test_spec_states_each_tables_dependency_scope() -> None:
    rows = _table_rows()
    for table in PYPROJECT_DEPENDENCY_TABLES:
        cell = rows[".".join(table.path)]
        if table.scope is None:
            assert cell.startswith("none"), (table.path, cell)
        else:
            assert cell == f"`{table.scope}`", (table.path, cell)


def test_spec_names_the_tables_left_unread() -> None:
    section = _section_3a()
    unread = section[section.index("Not recognized in a `pyproject.toml`"):]
    for name in (
        "tool.uv.sources", "tool.poetry.extras", "tool.setuptools.dynamic",
        "tool.pixi", "hatch.toml",
    ):
        assert name in unread, name
