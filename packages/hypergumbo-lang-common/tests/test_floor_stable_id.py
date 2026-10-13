# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-motiz: the stable_id floor, end to end, and the producers that left it.

The backstop (``populate_kind_stable_ids``, core ``analyze/base.py``) gives a
symbol whose producer computes no ``stable_id`` the FLOOR key
``make_declaration_stable_id(kind, language, path, name)`` when that key is
unique among the file's floor candidates, and leaves it ``None`` otherwise
(ADR-0035 §1). Every language of this file has since moved to the ADR-0035
producer formula (WI-tusad), so what is pinned here through the real survey
path is that the move kept the floor's guarantees:

* no function of the exact fixture (clojure, erlang, haskell, ocaml) is null;
* where names repeat in one file -- elixir ``Demo.greet`` of three arities,
  proto ``A.Inner`` / ``B.Inner`` (both named ``Inner``), rpc ``Get`` in two
  services -- every member holds its own id, and the two-run tests delete an
  EARLIER same-name sibling between runs: each survivor keeps its id and none
  takes an id another symbol held before the edit (the failure an occurrence
  split of floor keys produced: ``B.Inner`` inheriting ``A.Inner``'s id).

The producers are pinned in ``test_callable_producer_stable_ids.py`` (erlang,
clojure), ``test_ml_family_producer_stable_ids.py`` (haskell, ocaml) and
``test_idl_scoped_producer_stable_ids.py`` (proto).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map

_EXACT = {
    "a.clj": (
        "(ns demo.core)\n"
        "(defn greet [name] (str \"hi \" name))\n"
        "(defn main [] (greet \"x\"))\n"
    ),
    "a.erl": (
        "-module(a).\n"
        "-export([greet/1, main/0]).\n"
        "greet(Name) -> Name.\n"
        "main() -> greet(x).\n"
        "main(X) -> greet(X).\n"
    ),
    "A.hs": (
        "module A where\n"
        "greet :: String -> String\n"
        "greet n = n\n"
        "main :: IO ()\n"
        "main = putStrLn (greet \"x\")\n"
    ),
    "a.ml": (
        "let greet name = \"hi \" ^ name\n"
        "let main () = print_string (greet \"x\")\n"
    ),
}

# Functions each analyzer emits on _EXACT (reach: an analyzer that stops
# emitting them would make the stable_id assertions vacuous).
_EXACT_FUNCTIONS = {"clojure": 2, "erlang": 3, "haskell": 2, "ocaml": 2}

_ELIXIR = (
    "defmodule Demo do\n"
    "  def greet(name), do: \"hi \" <> name\n"
    "  def greet(name, greeting), do: greeting <> name\n"
    "  def greet(a, b, c), do: a <> b <> c\n"
    "  def main(), do: greet(\"x\")\n"
    "end\n"
)
# The same file with greet/1 deleted; line numbers kept (a blank
# line), so each survivor is matched to itself across the two runs by line.
_ELIXIR_EDITED = _ELIXIR.replace("  def greet(name), do: \"hi \" <> name\n", "\n")

_PROTO = (
    "syntax = \"proto3\";\n"
    "package demo;\n"
    "message A {\n"
    "  message Inner { int32 x = 1; }\n"
    "  Inner i = 1;\n"
    "}\n"
    "message B {\n"
    "  message Inner { string y = 1; }\n"
    "  Inner i = 1;\n"
    "}\n"
    "service S {\n"
    "  rpc Get (A) returns (B);\n"
    "}\n"
    "service T {\n"
    "  rpc Get (B) returns (A);\n"
    "}\n"
)
# Message A and service S deleted (blank lines keep the survivors' lines).
_PROTO_EDITED = (
    _PROTO
    .replace("message A {\n  message Inner { int32 x = 1; }\n  Inner i = 1;\n}\n",
             "\n\n\n\n")
    .replace("service S {\n  rpc Get (A) returns (B);\n}\n", "\n\n\n")
    .replace("returns (A)", "returns (B)")
)


def _survey(root: Path, files: dict[str, str]) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    out = root / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _by_line(nodes: list[dict], language: str, kind: str) -> dict[int, dict]:
    picked = [n for n in nodes if n.get("language") == language and n["kind"] == kind]
    by_line = {n["span"]["start_line"]: n for n in picked}
    assert len(by_line) == len(picked), "two nodes on one line: match is ambiguous"
    return by_line


def test_exact_floor_fills_every_function(tmp_path: Path) -> None:
    """Positive case: names unique in their file get the floor value."""
    nodes = _survey(tmp_path, _EXACT)["nodes"]
    functions = [n for n in nodes if n["kind"] == "function"
                 and n.get("language") in _EXACT_FUNCTIONS]
    counts: dict[str, int] = {}
    for n in functions:
        counts[n["language"]] = counts.get(n["language"], 0) + 1
    assert counts == _EXACT_FUNCTIONS
    missing = [(n["language"], n["name"]) for n in functions if n["stable_id"] is None]
    assert not missing, f"functions still at stable_id=None: {missing}"
    erlang = {n["name"] for n in functions if n["language"] == "erlang"}
    assert erlang == {"greet/1", "main/0", "main/1"}  # the name carries arity


def test_elixir_same_name_functions_have_producer_ids(tmp_path: Path) -> None:
    """Reach: the three ``Demo.greet`` functions (arities 1, 2, 3) each hold a
    distinct producer id, not a null; nothing abstains."""
    bm = _survey(tmp_path, {"a.ex": _ELIXIR})
    fns = _by_line(bm["nodes"], "elixir", "function")
    assert {fns[n]["name"] for n in (2, 3, 4)} == {"Demo.greet"}
    ids = [fns[n]["stable_id"] for n in (2, 3, 4, 5)]
    assert None not in ids
    assert len(set(ids)) == 4
    assert fns[5]["name"] == "Demo.main"
    assert bm["validation_report"]["stable_id_stats"]["floor_abstained"] == 0


def _assert_no_id_moved(before: dict[int, dict], after: dict[int, dict]) -> None:
    for line, node in after.items():
        sid = node["stable_id"]
        if sid is None:
            continue
        held_by_others = {n["stable_id"] for k, n in before.items() if k != line}
        assert sid not in held_by_others, (
            f"line {line} ({node['name']}) took an id another symbol held "
            f"before the edit: {sid}"
        )


def test_deleting_an_earlier_elixir_sibling_moves_no_id(tmp_path: Path) -> None:
    """Two runs: delete ``Demo.greet/1``. Each survivor keeps its id -- never
    null, never the deleted function's or a sibling's old id."""
    before = _by_line(_survey(tmp_path / "r1", {"a.ex": _ELIXIR})["nodes"],
                      "elixir", "function")
    after = _by_line(_survey(tmp_path / "r2", {"a.ex": _ELIXIR_EDITED})["nodes"],
                     "elixir", "function")
    assert set(after) == {3, 4, 5}  # reach: the edit deleted exactly line 2
    for line, node in after.items():
        assert node["stable_id"] == before[line]["stable_id"] is not None, line
    _assert_no_id_moved(before, after)


def test_deleting_proto_message_and_service_moves_no_id(tmp_path: Path) -> None:
    """Two runs: delete ``message A`` (with its nested ``Inner``) and
    ``service S`` (with its ``Get``). ``B``'s ``Inner`` and ``T``'s ``Get`` keep
    the producer id they held, which is never the id ``A``'s ``Inner`` or
    ``S``'s ``Get`` held."""
    r1 = _survey(tmp_path / "r1", {"a.proto": _PROTO})["nodes"]
    r2 = _survey(tmp_path / "r2", {"a.proto": _PROTO_EDITED})["nodes"]
    msgs_before = _by_line(r1, "proto", "message")
    msgs_after = _by_line(r2, "proto", "message")
    rpcs_before = _by_line(r1, "proto", "method")
    rpcs_after = _by_line(r2, "proto", "method")
    # Reach: both same-name pairs exist before, one member of each after.
    assert msgs_before[4]["name"] == msgs_before[8]["name"] == "Inner"
    assert rpcs_before[12]["name"] == rpcs_before[15]["name"] == "Get"
    assert set(msgs_after) == {7, 8} and set(rpcs_after) == {15}
    # The transfer property (it is what an occurrence split breaks)...
    _assert_no_id_moved(msgs_before, msgs_after)
    _assert_no_id_moved(rpcs_before, rpcs_after)
    # ...and the producer ids that guarantee it: each pair is filled and
    # distinct before the edit, and the survivors keep their ids.
    assert None not in (msgs_before[4]["stable_id"], msgs_before[8]["stable_id"])
    assert msgs_before[4]["stable_id"] != msgs_before[8]["stable_id"]
    assert rpcs_before[12]["stable_id"] != rpcs_before[15]["stable_id"]
    assert None not in (rpcs_before[12]["stable_id"], rpcs_before[15]["stable_id"])
    for line in (7, 8):
        assert msgs_after[line]["stable_id"] == msgs_before[line]["stable_id"], line
    assert rpcs_after[15]["stable_id"] == rpcs_before[15]["stable_id"]
