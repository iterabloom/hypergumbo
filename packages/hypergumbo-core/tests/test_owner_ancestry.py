# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call on an in-repo type it does not define reaches its library ancestor's row.

WI-gabuv. ``defmodule App.Repo do use Ecto.Repo ... end`` called as
``Repo.all(q)`` arrived as ``App.Repo.all`` and the catalogue keys the row on
``Ecto.Repo``, so every Repo call of a Phoenix application was
``external_potential`` and a db claim read inconclusive. The condition is not
Elixir's: ``class Api(requests.Session)``, ``class R extends FileInputStream``
and ``class Conn extends net.Socket`` had the same miss. ``owner_ancestry``
stamps the owner's external ancestors in finalize; the io-boundary and taint
matchers retry with them.

The production-path tests drive the shipped CLI on each language's fixture; the
unit tests pin the precedence rules the module docstring states.
"""

from __future__ import annotations

import contextlib
import io
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import IoBoundaryCatalog, classify_call
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.owner_ancestry import (
    EXTERNAL_OWNER_ANCESTORS_KEY,
    ancestor_retry_hints,
    external_owner_ancestors,
    stamp_external_owner_ancestors,
)

# --------------------------------------------------------------------------
# Production path: the shipped CLI.
# --------------------------------------------------------------------------

def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = main(argv)
    return rc, buf.getvalue()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    return repo


def _boundaries(tmp_path: Path, files: dict[str, str],
                monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, int]]:
    """``{boundary: {primitive: count}}`` from ``io-boundaries --format json``."""
    repo = _repo(tmp_path, files)
    _rc, out = _run(["io-boundaries", str(repo), "--format", "json"],
                    tmp_path / "cache", monkeypatch)
    return {b: v["primitive_counts"] for b, v in json.loads(out)["boundaries"].items()}


_ELIXIR_REPO = """defmodule App.Repo do
  use Ecto.Repo, otp_app: :app, adapter: Ecto.Adapters.Postgres
end
"""

_ELIXIR_EXPORT = """defmodule App.Export do
  alias App.Repo
  def export(query, path) do
    Repo.transaction(fn ->
      rows = query |> Repo.stream() |> Enum.map(&inspect/1)
      all = Repo.all(query)
      File.write!(path <> ".all", inspect(all))
      File.write!(path, Enum.join(rows, "\\n"))
    end)
  end
end
"""


class TestElixirUseEctoRepo:
    """The filed repro, verbatim."""

    def test_repo_calls_reach_the_ecto_repo_rows(self, tmp_path, monkeypatch) -> None:
        got = _boundaries(tmp_path, {"lib/export.ex": _ELIXIR_REPO + _ELIXIR_EXPORT},
                          monkeypatch)
        assert got["db_read"] == {"Ecto.Repo.all": 1, "Ecto.Repo.stream": 1}
        assert got["db_write"] == {"Ecto.Repo.transaction": 1}
        assert not any(p.startswith("App.Repo") for p in got.get("external_potential", {}))

    def test_a_db_claim_is_decided_not_inconclusive(self, tmp_path, monkeypatch) -> None:
        repo = _repo(tmp_path, {"lib/export.ex": _ELIXIR_REPO + _ELIXIR_EXPORT})
        claims = tmp_path / "claims.yaml"
        claims.write_text(
            "claims:\n"
            "  - id: no-db-write\n"
            "    text: no db writes\n"
            "    constraint:\n"
            "      boundary: db_write\n"
            "      must_not_exist: true\n"
        )
        rc, out = _run(["verify-claims", str(repo), "--claims", str(claims),
                        "--format", "json"], tmp_path / "cache", monkeypatch)
        assert rc == 1, out
        assert "violated" in out

    def test_an_own_definition_beats_the_inherited_row(self, tmp_path, monkeypatch) -> None:
        """OWN MEMBER BEATS INHERITED ROW: ``App.Repo`` defining ``all`` itself
        runs the application's code, so ``Repo.all`` is not ``Ecto.Repo.all``."""
        repo_mod = _ELIXIR_REPO.replace(
            "end\n", "  def all(query), do: [query]\nend\n", 1,
        )
        got = _boundaries(tmp_path, {"lib/export.ex": repo_mod + _ELIXIR_EXPORT},
                          monkeypatch)
        assert "Ecto.Repo.all" not in got.get("db_read", {})
        assert got["db_read"] == {"Ecto.Repo.stream": 1}  # reach: the rest still classify


_PY_SESSION = """import requests


class Api(requests.Session):
    def fetch(self, url):
        return self.get(url)


def main():
    api = Api()
    api.post("http://x")
"""


class TestPythonSessionSubclass:
    def test_self_and_typed_receiver_calls_reach_the_session_rows(
        self, tmp_path, monkeypatch,
    ) -> None:
        got = _boundaries(tmp_path, {"api.py": _PY_SESSION}, monkeypatch)
        assert got["net_send"] == {
            "requests.Session.get": 1, "requests.Session.post": 1,
        }

    def test_an_in_repo_intermediate_base_is_walked_through(
        self, tmp_path, monkeypatch,
    ) -> None:
        src = _PY_SESSION.replace(
            "class Api(requests.Session):",
            "class Base(requests.Session):\n    pass\n\n\nclass Api(Base):",
        )
        got = _boundaries(tmp_path, {"api.py": src}, monkeypatch)
        assert got["net_send"] == {
            "requests.Session.get": 1, "requests.Session.post": 1,
        }

    def test_an_own_method_beats_the_inherited_row(self, tmp_path, monkeypatch) -> None:
        src = _PY_SESSION.replace(
            "    def fetch(self, url):",
            "    def get(self, url):\n        return url\n\n    def fetch(self, url):",
        )
        got = _boundaries(tmp_path, {"api.py": src}, monkeypatch)
        assert got.get("net_send") == {"requests.Session.post": 1}

    def test_an_in_repo_class_named_like_the_library_is_not_the_library(
        self, tmp_path, monkeypatch,
    ) -> None:
        """FALSE-POSITIVE GUARD: ``Session`` here is the application's own
        class, so nothing it lacks is credited to ``requests.Session``."""
        src = (
            "class Session:\n    pass\n\n\n"
            "class Api(Session):\n"
            "    def fetch(self, url):\n        return self.get(url)\n"
        )
        got = _boundaries(tmp_path, {"api.py": src}, monkeypatch)
        assert "net_send" not in got


class TestJavaAndJavaScriptSubclasses:
    def test_java_bare_and_typed_calls_reach_the_superclass_row(
        self, tmp_path, monkeypatch,
    ) -> None:
        src = (
            "import java.io.FileInputStream;\n\n"
            "class Reader2 extends FileInputStream {\n"
            "    Reader2(String p) throws Exception { super(p); }\n"
            "    int next() throws Exception { return read(); }\n"
            "}\n\n"
            "class Main {\n"
            "    void run() throws Exception {\n"
            "        Reader2 r = new Reader2(\"x\");\n"
            "        r.read();\n"
            "    }\n"
            "}\n"
        )
        got = _boundaries(tmp_path, {"Main.java": src}, monkeypatch)
        assert got["fs_read"] == {"java.io.FileInputStream.read": 2}

    def test_javascript_typed_call_reaches_the_superclass_row(
        self, tmp_path, monkeypatch,
    ) -> None:
        src = (
            "const net = require('net');\n\n"
            "class Conn extends net.Socket {\n"
            "  hello() { return 1; }\n"
            "}\n\n"
            "function main() {\n"
            "  const c = new Conn();\n"
            "  c.connect(80);\n"
            "}\n"
            "module.exports = { main };\n"
        )
        got = _boundaries(tmp_path, {"conn.js": src}, monkeypatch)
        assert got["net_send"] == {"net.Socket.connect": 1}


def test_taint_reads_the_same_stamp() -> None:
    """The taint matcher takes the inherited row through the shared helper, and
    an unstamped twin of the same call still matches nothing."""
    from hypergumbo_core.taint import (
        _build_callee_index,
        _match_propagation_entry,
        load_builtin_taint_catalog,
    )

    cat = load_builtin_taint_catalog()
    idx = _build_callee_index(cat.sinks_for_language("python"))
    dst = "python:external:0-0:post:external_symbol"
    ambiguous = cat.ambiguous_names_for_language("python")
    hit = _match_propagation_entry(
        idx, dst, ambiguous, "method", is_resolved=False,
        edge_meta={EXTERNAL_OWNER_ANCESTORS_KEY: ["requests.Session"]},
    )
    assert hit is not None and (hit.module, hit.name) == ("requests.Session", "post")
    assert _match_propagation_entry(
        idx, dst, ambiguous, "method", is_resolved=False, edge_meta={},
    ) is None


# --------------------------------------------------------------------------
# Matcher precedence.
# --------------------------------------------------------------------------

def _elixir_catalog(extra: dict[str, Any] | None = None) -> dict[str, IoBoundaryCatalog]:
    data: dict[str, Any] = {
        "language": "elixir", "status": "in_progress",
        "db_read": [{"module": "Ecto.Repo", "functions": ["all"]}],
    }
    data.update(extra or {})
    return {"elixir": IoBoundaryCatalog._from_dict(data)}


_REPO_ALL = "elixir:App.Repo:0-0:all:external_symbol"


class TestMatcherPrecedence:
    def test_a_row_keyed_on_the_owner_as_written_beats_the_ancestor(self) -> None:
        catalogs = _elixir_catalog({
            "db_write": [{"module": "App.Repo", "functions": ["all"]}],
        })
        hit = classify_call(catalogs, _REPO_ALL,
                            {EXTERNAL_OWNER_ANCESTORS_KEY: ["Ecto.Repo"]})
        assert hit is not None and hit.qualified_name == "App.Repo.all"

    def test_the_first_ancestor_with_a_row_wins(self) -> None:
        hit = classify_call(_elixir_catalog(), _REPO_ALL,
                            {EXTERNAL_OWNER_ANCESTORS_KEY: ["Other.Lib", "Ecto.Repo"]})
        assert hit is not None and hit.qualified_name == "Ecto.Repo.all"

    def test_no_stamp_is_the_old_answer(self) -> None:
        assert classify_call(_elixir_catalog(), _REPO_ALL, {}) is None

    def test_a_nameless_callee_is_not_looked_up(self) -> None:
        assert classify_call(_elixir_catalog(), "elixir:App.Repo:0-0::external_symbol",
                             {EXTERNAL_OWNER_ANCESTORS_KEY: ["Ecto.Repo"]}) is None

    def test_an_ancestor_with_no_row_falls_through_to_the_usual_lookup(self) -> None:
        assert classify_call(_elixir_catalog(), _REPO_ALL,
                             {EXTERNAL_OWNER_ANCESTORS_KEY: ["Other.Lib"]}) is None


def test_the_reader_tolerates_absent_and_malformed_stamps() -> None:
    assert external_owner_ancestors(None) == ()
    assert external_owner_ancestors({EXTERNAL_OWNER_ANCESTORS_KEY: "Ecto.Repo"}) == ()
    assert external_owner_ancestors(
        {EXTERNAL_OWNER_ANCESTORS_KEY: ["Ecto.Repo", 3, ""]},
    ) == ("Ecto.Repo",)
    assert ancestor_retry_hints("App.Repo", {}) == ()
    assert ancestor_retry_hints(
        "App.Repo", {EXTERNAL_OWNER_ANCESTORS_KEY: ["Ecto.Repo"]},
    ) == ("App.Repo", "Ecto.Repo")


# --------------------------------------------------------------------------
# The stamp: owner choice, walk order, refusals.
# --------------------------------------------------------------------------

def _sym(name: str, kind: str = "class", lang: str = "python", line: int = 1) -> Symbol:
    return Symbol(
        id=f"{lang}:app:{line}-{line}:{name}:{kind}", name=name, kind=kind,
        language=lang, path="app", span=Span(line, line, 0, 0),
    )


def _ext(dst: str) -> Symbol:
    parts = dst.split(":")
    return Symbol(id=dst, name=parts[-2], kind="external_symbol",
                  language=parts[0], path=parts[1], span=Span(0, 0, 0, 0))


def _rel(src: Symbol, dst: str, edge_type: str = "extends") -> Edge:
    return Edge.create(src=src.id, dst=dst, edge_type=edge_type, line=1,
                       origin="test", origin_run_id="test")


def _call(dst: str, meta: dict[str, Any] | None = None, *,
          edge_type: str = "calls", resolved: bool = False) -> Edge:
    edge = Edge.create(src="python:app:9-9:caller:function", dst=dst,
                       edge_type=edge_type, line=9, origin="test",
                       origin_run_id="test", meta=meta)
    edge.is_resolved = resolved
    return edge


_SESSION = "python:requests:0-0:Session:external_symbol"
_POST = "python:external:0-0:post:external_symbol"


def _stamp(symbols: list[Symbol], edges: list[Edge]) -> list[Any]:
    stamp_external_owner_ancestors(symbols, edges)
    return [(e.meta or {}).get(EXTERNAL_OWNER_ANCESTORS_KEY) for e in edges
            if e.edge_type in ("calls", "instantiates")]


class TestOwnerChoice:
    def test_a_typed_receiver_by_hint(self) -> None:
        api = _sym("Api")
        got = _stamp([api, _ext(_SESSION)],
                     [_rel(api, _SESSION), _call(_POST, {"receiver_type_hint": "Api"})])
        assert got == [["requests.Session"]]

    def test_a_typed_receiver_by_id_beats_an_ambiguous_name(self) -> None:
        a1, a2 = _sym("Api"), _sym("Api", line=20)
        got = _stamp([a1, a2], [
            _rel(a1, _SESSION),
            _call(_POST, {"receiver_type_hint": "Api", "receiver_type_id": a1.id}),
            _call(_POST, {"receiver_type_hint": "Api"}),
        ])
        assert got == [["requests.Session"], None]

    def test_an_id_naming_a_non_owner_is_ignored(self) -> None:
        api, fn = _sym("Api"), _sym("helper", kind="function")
        rb = _sym("Api", lang="ruby", line=30)
        got = _stamp([api, fn, rb], [
            _rel(api, _SESSION),
            _call(_POST, {"receiver_type_id": fn.id}),
            _call(_POST, {"receiver_type_id": rb.id}),
            _call(_POST, {"receiver_type_id": 7}),
            _call(_POST, {"receiver_type_hint": ""}),
        ])
        assert got == [None, None, None, None]

    def test_the_module_slot_names_the_owner(self) -> None:
        repo = _sym("App.Repo", kind="module", lang="elixir")
        behaviour = "elixir:external:0-0:Ecto.Repo:unresolved"
        got = _stamp([repo], [_rel(repo, behaviour, "implements"), _call(_REPO_ALL)])
        assert got == [["Ecto.Repo"]]

    def test_an_unknown_module_slot_falls_back_to_the_enclosing_class(self) -> None:
        api = _sym("Api")
        got = _stamp([api], [
            _rel(api, _SESSION),
            _call("python:somewhere:0-0:post:external_symbol",
                  {"enclosing_class": "Api"}),
            _call(_POST, {"enclosing_class_id": api.id}),
        ])
        assert got == [["requests.Session"], ["requests.Session"]]

    def test_a_field_receiver_is_not_the_enclosing_class(self) -> None:
        api = _sym("Api")
        got = _stamp([api], [
            _rel(api, _SESSION),
            _call(_POST, {"enclosing_class": "Api", "inherited_field_receiver": "s"}),
        ])
        assert got == [None]

    def test_only_unresolved_calls_with_a_name_are_stamped(self) -> None:
        api = _sym("Api")
        meta = {"receiver_type_hint": "Api"}
        got = _stamp([api], [
            _rel(api, _SESSION),
            _call(_POST, dict(meta), resolved=True),
            _call(_POST, dict(meta), edge_type="instantiates"),
            _call("python:external:0-0::external_symbol", dict(meta)),
            _call(_POST),
        ])
        assert got == [None, None, None, None]


class TestWalk:
    def test_nearest_first_then_extends_before_implements(self) -> None:
        api, base = _sym("Api"), _sym("Base", line=5)
        got = _stamp([api, base], [
            _rel(api, "python:ifaces:0-0:Closer:external_symbol", "implements"),
            _rel(api, "python:mixins:0-0:Log:external_symbol", "includes"),
            _rel(api, base.id),
            _rel(base, _SESSION),
            _call(_POST, {"receiver_type_hint": "Api"}),
        ])
        assert got == [["ifaces.Closer", "mixins.Log", "requests.Session"]]

    def test_an_in_repo_ancestor_declaring_the_member_refuses(self) -> None:
        api, base = _sym("Api"), _sym("Base", line=5)
        member = _sym("Base#post", kind="method", line=6)
        got = _stamp([api, base, member], [
            _rel(api, base.id), _rel(base, _SESSION),
            _call(_POST, {"receiver_type_hint": "Api"}),
        ])
        assert got == [None]

    def test_parent_names_as_written(self) -> None:
        api = _sym("Api")
        got = _stamp([api], [
            _rel(api, "python:external:0-0:FileInputStream:external_symbol"),
            _rel(api, "python:net:0-0:net.Socket:external_symbol"),
            _rel(api, "python:net:0-0:net.Socket:external_symbol", "implements"),
            _rel(api, "python:somefile.py:1-2:Gone:class"),  # dangling, not external
            _rel(api, "python:external:0-0::external_symbol"),  # no name to read
            _call(_POST, {"receiver_type_hint": "Api"}),
        ])
        assert got == [["FileInputStream", "net.Socket"]]

    def test_a_cycle_and_the_depth_cap_end_the_walk(self) -> None:
        chain = [_sym(f"C{i}", line=i + 1) for i in range(13)]
        edges = [_rel(a, b.id) for a, b in pairwise(chain)]
        edges.append(_rel(chain[1], chain[0].id))  # cycle back to the start
        edges.append(_rel(chain[-1], _SESSION))  # beyond the cap
        edges.append(_rel(chain[2], "python:near:0-0:Lib:external_symbol"))
        edges.append(_call(_POST, {"receiver_type_hint": "C0"}))
        assert _stamp(chain, edges) == [["near.Lib"]]

    def test_no_external_ancestor_is_no_stamp(self) -> None:
        api, base = _sym("Api"), _sym("Base", line=5)
        call = _call(_POST, {"receiver_type_hint": "Api"})
        assert _stamp([api, base], [_rel(api, base.id), call]) == [None]


def test_a_call_with_no_meta_gets_one_when_its_module_names_the_owner() -> None:
    repo = _sym("App.Repo", kind="module", lang="elixir")
    call = _call(_REPO_ALL)
    call.meta = None
    assert stamp_external_owner_ancestors(
        [repo], [_rel(repo, "elixir:external:0-0:Ecto.Repo:unresolved", "implements"), call],
    ) == 1
    assert call.meta == {EXTERNAL_OWNER_ANCESTORS_KEY: ["Ecto.Repo"]}
