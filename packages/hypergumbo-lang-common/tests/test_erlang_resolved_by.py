# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Erlang's call edges (audit 0030, WI-hirol).

One fixture per route: a local ``g()`` found in the file's name/arity table
is ``same_module`` (an Erlang file is exactly one module); ``b:h()`` found
under the module the call site writes is ``qualified``; a bare ``h()`` the
file does not define falls to the run-wide resolver and takes the lookup's
value (``name``). A remote call to a module the tree does not define
(``lists:reverse``) and an expanded ``?LOG_*`` macro end on synthetic
external nodes and carry no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.erlang import analyze_erlang


def test_erlang_routes(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.erl").write_text(
        "-module(a).\n-export([f/0, g/0]).\n"
        '-include_lib("kernel/include/logger.hrl").\n\n'
        'f() -> g(), b:h(), h(), lists:reverse([]), ?LOG_INFO("x").\n'
        "g() -> ok.\n"
    )
    (src / "b.erl").write_text("-module(b).\n-export([h/0]).\nh() -> ok.\n")
    calls = [
        e for e in analyze_erlang(tmp_path).edges
        if e.edge_type == "calls" and ":f/0:" in e.src
    ]
    marks = sorted((
        ((e.meta or {}).get("call_construct"), e.dst.split(":")[1], (e.meta or {}).get("resolved_by"))
        for e in calls
    ), key=str)
    assert marks == sorted([
        (None, "logger", None),
        ("local", "src/a.erl", "same_module"),
        ("local", "src/b.erl", "name"),
        ("remote", "lists", None),
        ("remote", "src/b.erl", "qualified"),
    ], key=str)
