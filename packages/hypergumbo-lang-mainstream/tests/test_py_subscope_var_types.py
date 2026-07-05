# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-ruluv: ``process_code_block`` must prune ``var_types`` of the names a
comprehension / lambda / nested-def BINDS in its own scope before recursing
into that sub-scope. Otherwise a sub-scope target/param that SHADOWS an outer
``var_types``-typed name inherits the stale outer type and the producer emits a
confidently-wrong ``receiver_type_hint`` (a resolved edge to the wrong method).

Two families:

* **MUST-SUPPRESS** — a comprehension for-target / lambda param that reuses an
  outer typed name gets NO hint inside the sub-scope (the name is pruned).
* **MUST-KEEP** — a free variable the sub-scope only reads, a comprehension's
  first iterable (enclosing scope), and a lambda default expr (enclosing scope)
  keep their outer type.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.py import extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _hint(res, attr_name: str) -> object:
    """The receiver_type_hint on the unresolved method-call edge to ``attr``
    (or the sentinel ``KeyError`` marker if the edge carries none / is absent).
    Returns the set of hints across all matching edges."""
    dst = f"python:external:0-0:{attr_name}:unresolved"
    edges = [e for e in res.edges if e.edge_type == "calls" and e.dst == dst]
    return [(e.meta or {}).get("receiver_type_hint") for e in edges]


# ---------------------------------------------------------------------------
# MUST-SUPPRESS — a shadowing sub-scope binding must not carry the outer type.
# ---------------------------------------------------------------------------


class TestSubScopeShadowSuppressed:
    def test_comprehension_for_target_shadow(self, tmp_path: Path) -> None:
        src = (
            "class Connection:\n    pass\n"
            "def f(conn: Connection, socks):\n"
            "    return [conn.send() for conn in socks]\n"
        )
        res = _analyze(tmp_path, src)
        # comp-local ``conn`` is pruned -> no Connection hint on send().
        assert _hint(res, "send") in ([], [None])

    def test_lambda_param_shadow(self, tmp_path: Path) -> None:
        src = (
            "class Connection:\n    pass\n"
            "def f(conn: Connection):\n"
            "    return lambda conn: conn.close()\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "close") in ([], [None])

    def test_nested_comprehension_inner_target(self, tmp_path: Path) -> None:
        # The adversarial regression: inner comp arrives as a NODE (outer.elt),
        # so entry-level pruning is required (descent-site pruning misses it).
        src = (
            "class Session:\n    pass\n"
            "def f(y: Session, matrix):\n"
            "    return [[y.commit() for y in row] for row in matrix]\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "commit") in ([], [None])

    def test_comprehension_in_lambda_body(self, tmp_path: Path) -> None:
        # Comp nested in a (no-arg) lambda body — entry-level pruning must
        # compose through the lambda body.
        src = (
            "class Runner:\n    pass\n"
            "def f(z: Runner, items):\n"
            "    return lambda: [z.run() for z in items]\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "run") in ([], [None])

    def test_tuple_unpack_target(self, tmp_path: Path) -> None:
        src = (
            "class Database:\n    pass\n"
            "def f(db: Database, pairs):\n"
            "    return [db.query() for (db, x) in pairs]\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "query") in ([], [None])

    def test_starred_target(self, tmp_path: Path) -> None:
        src = (
            "class Cursor:\n    pass\n"
            "def f(cur: Cursor, rows):\n"
            "    return [cur.fetch() for cur, *rest in rows]\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "fetch") in ([], [None])

    def test_lambda_vararg_kwarg_shadow(self, tmp_path: Path) -> None:
        src = (
            "class Database:\n    pass\n"
            "def g(db: Database):\n"
            "    return lambda *a, **db: db.pop()\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "pop") in ([], [None])

    def test_dictcomp_target_shadow(self, tmp_path: Path) -> None:
        src = (
            "class Database:\n    pass\n"
            "def f(db: Database, items):\n"
            "    return {k: db.query() for k, db in items}\n"
        )
        res = _analyze(tmp_path, src)
        assert _hint(res, "query") in ([], [None])

    def test_nested_def_body_leak(self, tmp_path: Path) -> None:
        # inner's unannotated ``svc`` shadows outer's ``svc: Service``. After the
        # skip hoist, inner is NOT recursed under outer's caller_symbol; inner's
        # own walk (unannotated svc -> untyped) emits no hint, and there is no
        # duplicate mis-typed edge.
        src = (
            "class Service:\n    pass\n"
            "def outer(svc: Service):\n"
            "    def inner(svc):\n"
            "        return svc.call()\n"
            "    return inner\n"
        )
        res = _analyze(tmp_path, src)
        hints = _hint(res, "call")
        assert all(h is None for h in hints), hints

    def test_nested_class_body_leak(self, tmp_path: Path) -> None:
        # A ClassDef arriving as a top-level block node is skipped too.
        src = (
            "class Service:\n    pass\n"
            "def outer(svc: Service):\n"
            "    class Inner:\n"
            "        x = svc\n"
            "    return Inner\n"
        )
        # No crash / no leaked edge attributed to outer for the class body.
        res = _analyze(tmp_path, src)
        assert res is not None


# ---------------------------------------------------------------------------
# MUST-KEEP — free reads and enclosing-scope positions retain the outer type.
# ---------------------------------------------------------------------------


class TestEnclosingTypeKept:
    def test_free_var_read_in_comprehension(self, tmp_path: Path) -> None:
        src = (
            "class Service:\n    pass\n"
            "def f(svc: Service, items):\n"
            "    return [svc.handle(x) for x in items]\n"
        )
        res = _analyze(tmp_path, src)
        # ``svc`` is free (only ``x`` is pruned) -> hint kept.
        assert "Service" in _hint(res, "handle")

    def test_first_iterable_in_enclosing_scope(self, tmp_path: Path) -> None:
        src = (
            "class Repo:\n    pass\n"
            "def f(repo: Repo):\n"
            "    return [x for x in repo.all()]\n"
        )
        res = _analyze(tmp_path, src)
        # generators[0].iter is recursed with FULL var_types -> Repo kept.
        assert "Repo" in _hint(res, "all")

    def test_multiple_generators_free_var(self, tmp_path: Path) -> None:
        src = (
            "class Service:\n    pass\n"
            "def f(svc: Service, xs, ys):\n"
            "    return [svc.f() for x in xs for y in ys]\n"
        )
        res = _analyze(tmp_path, src)
        assert "Service" in _hint(res, "f")

    def test_noarg_lambda_free_var(self, tmp_path: Path) -> None:
        src = (
            "class Service:\n    pass\n"
            "def f(svc: Service):\n"
            "    return lambda: svc.ping()\n"
        )
        res = _analyze(tmp_path, src)
        # No-arg lambda: empty shadow -> body shares var_types -> hint kept.
        assert "Service" in _hint(res, "ping")

    def test_lambda_default_in_enclosing_scope(self, tmp_path: Path) -> None:
        src = (
            "class Connection:\n    pass\n"
            "def f(conn: Connection):\n"
            "    return lambda x=conn.timeout(): x\n"
        )
        res = _analyze(tmp_path, src)
        # The default expr is recursed with FULL var_types -> Connection kept.
        assert "Connection" in _hint(res, "timeout")
