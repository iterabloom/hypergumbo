# SPDX-License-Identifier: AGPL-3.0-or-later
"""A SQL call site naming several tables is the same in every process.

``database_query._extract_tables_from_query`` collected a statement's tables
in a ``set`` and returned ``list(tables)``, so their order followed the
per-process str-hash salt. That order fed the call site's ``name``
(``"SELECT file, block"`` in one run, ``"SELECT block, file"`` in the next),
its node id (the name is an id segment), ``meta["tables"]`` and the order of
the ``references`` edges. The ``stable_id`` was never affected: it hashes the
SORTED tables.

The contract pinned here: the tables are listed in the order the statement
first mentions them, which is a function of the source text alone. The
``stable_id`` keeps hashing the sorted tables, so no stable id moves.

Why these tests can fail:

- The subprocess test is the behavioural evidence. One process has one hash
  salt, so an in-process test cannot see the defect; this one runs the linker
  under four fixed, distinct ``PYTHONHASHSEED`` values on a statement naming
  six tables and requires one answer, equal to the text order. Before the fix
  the four processes disagreed with each other.
- The in-process tests pin the ordering contract structurally: every
  permutation of one table set must come back in its own text order, which a
  set (one iteration order per process for a given content, barring a full
  hash collision) cannot satisfy; and the ``UPDATE ... FROM`` case tells text
  order apart from the order of ``TABLE_EXTRACTION_PATTERNS``.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

from hypergumbo_core.analyze.base import make_site_stable_id
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.database_query import (
    _extract_tables_from_query,
    database_query_linker,
    link_database_queries,
)
from hypergumbo_core.linkers.registry import LinkerContext

_SIX = ["shipments", "users", "orders", "products", "warehouses", "audit_log"]
_SIX_QUERY = (
    "SELECT * FROM shipments s JOIN users u ON u.id = s.user_id "
    "JOIN orders o ON o.id = s.order_id JOIN products p ON p.id = o.product_id "
    "JOIN warehouses w ON w.id = s.warehouse_id JOIN audit_log a ON a.ref = s.id"
)

_DRIVER = """
import json, sys
from pathlib import Path
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.database_query import link_database_queries

root = Path(sys.argv[1])
tables = [
    Symbol(id=f"sql:schema.sql:{i}-{i}:{name}:table", name=name, kind="table",
           path="schema.sql", language="sql",
           span=Span(start_line=i, end_line=i, start_col=0, end_col=0))
    for i, name in enumerate(sys.argv[2].split(","), start=1)
]
result = link_database_queries(root, tables)
print(json.dumps({
    "symbols": [[s.name, s.id, s.stable_id, s.meta["tables"]] for s in result.symbols],
    "edges": [[e.src, e.dst, e.meta["table_name"]] for e in result.edges],
}))
"""


def _table(name: str, line: int) -> Symbol:
    return Symbol(
        id=f"sql:schema.sql:{line}-{line}:{name}:table",
        name=name,
        kind="table",
        path="schema.sql",
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
        language="sql",
    )


def _write_app(root: Path) -> None:
    (root / "app.py").write_text(f'cursor.execute("{_SIX_QUERY}")\n')
    (root / "store.js").write_text(
        'db.query("SELECT * FROM products p JOIN orders o ON o.product_id = p.id");\n'
    )
    (root / "Repo.java").write_text(
        'class Repo { void f() { stmt.executeQuery("SELECT * FROM users u '
        'JOIN audit_log a ON a.user_id = u.id"); } }\n'
    )


def test_one_answer_under_four_hash_seeds(tmp_path: Path) -> None:
    """Four processes, four hash salts, one name / id / meta / edge order."""
    _write_app(tmp_path)
    outputs = []
    for seed in ("0", "1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", _DRIVER, str(tmp_path), ",".join(sorted(_SIX))],
            capture_output=True, text=True, env=env, check=True,
        )
        outputs.append(json.loads(proc.stdout))

    assert all(out == outputs[0] for out in outputs[1:]), outputs
    by_file = {sym[1].split(":")[1]: sym for sym in outputs[0]["symbols"]}
    name, node_id, _stable, meta_tables = by_file["app.py"]
    assert name == "SELECT " + ", ".join(_SIX)
    assert name in node_id
    assert meta_tables == _SIX
    app_edges = [e[2] for e in outputs[0]["edges"] if e[0] == node_id]
    assert app_edges == _SIX
    assert by_file["store.js"][0] == "SELECT products, orders"
    assert by_file["Repo.java"][0] == "SELECT users, audit_log"


def test_every_permutation_comes_back_in_text_order() -> None:
    """A set has one order per process for a given content; text has 24."""
    names = ["zebra", "apple", "mango", "kiwi"]
    for perm in itertools.permutations(names):
        first, *rest = perm
        words = ["SELECT", "*", "FROM", first]
        for table in rest:
            words += ["JOIN", table, "ON", f"{table}.id", "=", f"{first}.id"]
        query = " ".join(words)
        assert _extract_tables_from_query(query) == list(perm)


def test_text_order_is_not_extraction_pattern_order() -> None:
    """``UPDATE`` is tried after ``FROM``, yet the updated table is first."""
    query = "UPDATE zz SET n = aa.n FROM aa WHERE aa.id = zz.id"
    assert _extract_tables_from_query(query) == ["zz", "aa"]
    assert _extract_tables_from_query(
        "INSERT INTO archive SELECT * FROM live JOIN extra ON extra.id = live.id"
    ) == ["archive", "live", "extra"]


def test_repeated_table_is_listed_once_at_its_first_mention() -> None:
    query = (
        "SELECT * FROM nodes n JOIN edges e ON e.src = n.id "
        "JOIN Nodes m ON e.dst = m.id"
    )
    assert _extract_tables_from_query(query) == ["nodes", "edges"]
    # DELETE FROM is matched by two patterns at one position.
    assert _extract_tables_from_query("DELETE FROM users WHERE id = 1") == ["users"]


def test_keywords_are_skipped_without_disturbing_the_order() -> None:
    query = "SELECT * FROM t2 JOIN (SELECT 1) x ON 1 = 1 JOIN t1 ON 1 = 1 UPDATE set"
    assert _extract_tables_from_query(query) == ["t2", "t1"]


def test_name_id_meta_and_edges_follow_the_statement(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(f'cursor.execute("{_SIX_QUERY}")\n')
    table_symbols = [_table(n, i) for i, n in enumerate(sorted(_SIX), start=1)]

    result = link_database_queries(tmp_path, table_symbols)

    (symbol,) = result.symbols
    assert symbol.name == "SELECT " + ", ".join(_SIX)
    assert symbol.id == f"python:app.py:1-1:{symbol.name}:call_site"
    assert symbol.meta["tables"] == _SIX
    assert [e.meta["table_name"] for e in result.edges] == _SIX
    assert {e.src for e in result.edges} == {symbol.id}


def test_registered_linker_orders_every_host_language_by_statement(
    tmp_path: Path,
) -> None:
    """The registry entry point, over the Python, JS and Java scanners."""
    _write_app(tmp_path)
    tables = [_table(n, i) for i, n in enumerate(sorted(_SIX), start=1)]
    ctx = LinkerContext(repo_root=tmp_path, symbols=tables, edges=[])

    result = database_query_linker(ctx)

    by_file = {Path(s.path).name: s for s in result.symbols}
    assert by_file["app.py"].meta["tables"] == _SIX
    assert by_file["store.js"].name == "SELECT products, orders"
    assert by_file["Repo.java"].name == "SELECT users, audit_log"
    for sym in result.symbols:
        linked = [e.meta["table_name"] for e in result.edges if e.src == sym.id]
        assert linked == sym.meta["tables"]


def test_stable_id_still_hashes_the_sorted_tables(tmp_path: Path) -> None:
    """The name follows the text; the stable id does not move (scheme v14)."""
    (tmp_path / "app.py").write_text(f'cursor.execute("{_SIX_QUERY}")\n')

    (symbol,) = link_database_queries(tmp_path, []).symbols

    assert symbol.stable_id == make_site_stable_id(
        "db_query", "app.py", "SELECT:" + ",".join(sorted(_SIX))
    )
    assert sorted(_SIX) != _SIX


def test_two_text_orders_of_one_table_set_share_the_stable_id_key(
    tmp_path: Path,
) -> None:
    """One table set in two files: two names, one SITE target per file."""
    (tmp_path / "a.py").write_text(
        'cursor.execute("SELECT * FROM users u JOIN orders o ON o.uid = u.id")\n'
    )
    (tmp_path / "b.py").write_text(
        'cursor.execute("SELECT * FROM orders o JOIN users u ON o.uid = u.id")\n'
    )

    by_path = {
        Path(s.path).name: s for s in link_database_queries(tmp_path, []).symbols
    }

    assert by_path["a.py"].name == "SELECT users, orders"
    assert by_path["b.py"].name == "SELECT orders, users"
    for file_name, sym in by_path.items():
        assert sym.stable_id == make_site_stable_id(
            "db_query", file_name, "SELECT:orders,users"
        )
