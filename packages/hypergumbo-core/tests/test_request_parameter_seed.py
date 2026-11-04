# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sosov: a request handler's request PARAMETER is a taint source.

Express hands a route handler its request as ``req``; Go's ``http.Handler``
gets ``r *http.Request``. Neither arrives through a call a catalogue row could
match, so no source was ever minted for it and WI-fuzik's disclosure was the
only thing between a clean ``untrusted_input`` verdict and
``fs.writeFileSync(req.query.p, ...)``. With the parameters now reaching
definitions in the DDG (commit 1), a seed can be minted WITHOUT a call edge:
``(handler, parameter)`` for every ``request_ingress`` entry whose framework
contract names the request parameter, only when the handler has a DDG and the
parameter has a use -- a handler that never touches ``req`` stays clean AND
listed. The seed enters the DDG arm's own walk (same-function, variable-level)
and the structural arm takes none: a symbol-level seed at a handler would
taint everything it reaches. A seeded handler is a REPRESENTED arrival
(ADR-0049 ruling 3), so the disclosure no longer names it; the shipped
contract table is builtin, the standing of a builtin row.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.cli import main
from hypergumbo_core.taint import (
    GENERIC_ROUTE_CONTRACTS,
    REQUEST_PARAMETER_CONTRACTS,
    ParameterSeed,
    TaintSink,
    TaintSource,
    parameter_seeds,
    propagate_taint_ddg,
    represented_ingress_callers,
)

_EXPRESS = (
    'const express = require("express");\n'
    'const fs = require("fs");\n'
    "\n"
    "const app = express();\n"
    "\n"
    'app.get("/save", (req, res) => {\n'
    '  fs.writeFileSync(req.query.p, "x");\n'
    '  res.send("ok");\n'
    "});\n"
    "\n"
    'app.get("/ping", (req, res) => {\n'
    '  res.send("pong");\n'
    "});\n"
    "\n"
    'app.get("/echo", (req, res) => {\n'
    "  res.send(req.query.q);\n"
    "});\n"
    "\n"
    # A one-line arrow: the sink sits on the parameter's own line.
    'app.get("/one", (req, res) => fs.writeFileSync(req.query.p, "x"));\n'
    "\n"
    "app.listen(3000);\n"
)
_GO = (
    "package main\n\n"
    'import (\n\t"net/http"\n\t"os"\n)\n\n'
    "type saver struct{}\n\n"
    "func (saver) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
    '\tos.WriteFile(r.URL.Query().Get("p"), []byte("x"), 0o644)\n'
    "}\n\n"
    "type pinger struct{}\n\n"
    "func (pinger) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
    '\tw.Write([]byte("pong"))\n'
    "}\n\n"
    "func main() {\n"
    '\thttp.Handle("/save", saver{})\n'
    '\thttp.Handle("/ping", pinger{})\n'
    '\thttp.ListenAndServe(":8080", nil)\n'
    "}\n"
)
_CLAIMS = (
    "claims:\n"
    "  - id: untrusted-input-no-host-fs\n"
    "    text: request data never chooses a file to write\n"
    "    constraint:\n"
    "      taint_flow:\n"
    "        source_taint: untrusted_input\n"
    "        prohibited_sink_zone: host_fs\n"
    "  - id: untrusted-input-no-subprocess\n"
    "    text: request data never reaches a subprocess\n"
    "    constraint:\n"
    "      taint_flow:\n"
    "        source_taint: untrusted_input\n"
    "        prohibited_sink_zone: subprocess\n"
    "  - id: never-receives-network-data\n"
    "    text: the program never receives network data\n"
    "    constraint:\n"
    "      boundary: net_recv\n"
    "      must_not_exist: true\n"
)


def _verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], *extra: str,
) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, text in files.items():
        (repo / rel).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json", *extra])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


def test_express_request_parameter_is_a_source_and_the_handler_leaves_the_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, {"server.js": _EXPRESS})
    host_fs = verdicts["untrusted-input-no-host-fs"]
    assert host_fs["verdict"] == "violated", host_fs["details"]
    assert "writeFileSync" in host_fs["details"], host_fs["details"]
    # Both writing handlers, the multi-line one and the one-line arrow whose
    # sink shares the parameter's line, are found precisely.
    assert "_GET_save_handler" in host_fs["details"], host_fs["details"]
    assert "_GET_one_handler" in host_fs["details"], host_fs["details"]
    assert "confidence: precise" in host_fs["details"], host_fs["details"]
    other = verdicts["untrusted-input-no-subprocess"]
    # /ping never reads req: unseeded, still listed. The others read it: represented.
    assert "no taint source represents" in other["details"], other["details"]
    assert "_GET_ping_handler" in other["details"], other["details"]
    for name in ("_GET_save_handler", "_GET_echo_handler", "_GET_one_handler"):
        assert name not in other["details"], other["details"]
    # The BOUNDARY arm: a followed parameter is not a recorded net_recv
    # crossing, so "never receives network data" stays withheld, naming every
    # handler (ADR-0049 ruling 3: a webserver must not confirm this).
    boundary = verdicts["never-receives-network-data"]
    assert boundary["verdict"] == "inconclusive", boundary["details"]
    assert "no net_recv crossing represents" in boundary["details"], boundary["details"]
    for name in ("_GET_save_handler", "_GET_ping_handler", "_GET_echo_handler", "_GET_one_handler"):
        assert name in boundary["details"], boundary["details"]


def test_a_handler_reading_its_request_without_a_sink_mints_no_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, {"server.js": _EXPRESS})
    host_fs = verdicts["untrusted-input-no-host-fs"]
    assert "_GET_echo_handler" not in host_fs["details"], host_fs["details"]
    assert "_GET_ping_handler" not in host_fs["details"].split("ALSO")[0], host_fs["details"]


def test_go_servehttp_request_parameter_is_a_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(
        tmp_path, monkeypatch, {"main.go": _GO, "go.mod": "module probe\n\ngo 1.21\n"},
    )
    host_fs = verdicts["untrusted-input-no-host-fs"]
    assert host_fs["verdict"] == "violated", host_fs["details"]
    assert "WriteFile" in host_fs["details"], host_fs["details"]
    other = verdicts["untrusted-input-no-subprocess"]
    assert "saver.ServeHTTP" not in other["details"], other["details"]
    assert "pinger.ServeHTTP" in other["details"], other["details"]


# --- the producer, directly -------------------------------------------------

_JS_SAVE = "javascript:server.js:6-9:_GET_save_handler:function"
_JS_PING = "javascript:server.js:11-13:_GET_ping_handler:function"
_GO_SAVE = "go:main.go:10-12:saver.ServeHTTP:method"


def _ingress(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"symbol_id": s, "kind": k, "label": s.rsplit(":", 2)[-2]} for s, k in pairs]


def _edge(sym: str, var: str, def_line: int, use_line: int) -> DdgEdge:
    return DdgEdge(variable=var, def_block="b0", def_line=def_line, use_block="b0", use_line=use_line, symbol_id=sym)


def test_the_contract_tables_name_the_shapes() -> None:
    assert REQUEST_PARAMETER_CONTRACTS["express_route"] == ("positional", 0)
    assert REQUEST_PARAMETER_CONTRACTS["go_handler"] == ("typed", "*http.Request")
    # The analyzers file most handlers under the generic kind (the probes'
    # _GET_save_handler and Go's HandleFunc target are http_route), contracted
    # per language; python is absent: a Flask view's request is a global.
    assert GENERIC_ROUTE_CONTRACTS["javascript"] == ("positional", 0)
    assert GENERIC_ROUTE_CONTRACTS["typescript"] == ("positional", 0)
    assert GENERIC_ROUTE_CONTRACTS["go"] == ("typed", "*http.Request")
    assert "python" not in GENERIC_ROUTE_CONTRACTS


def test_the_generic_route_kind_is_contracted_per_language() -> None:
    js = parameter_seeds(
        _ingress((_JS_SAVE, "http_route")), {_JS_SAVE: ("req", "res")},
        [_edge(_JS_SAVE, "req", 6, 7)], {}, "javascript",
    )
    assert [(s.handler, s.parameter, s.source.module) for s in js] == [(_JS_SAVE, "req", "express")]
    go_fn = "go:main.go:13-15:save:function"
    go = parameter_seeds(
        _ingress((go_fn, "http_route")), {go_fn: ("w", "r")}, [_edge(go_fn, "r", 13, 14)],
        {go_fn: {"parameters": [
            {"name": "w", "type": "http.ResponseWriter"}, {"name": "r", "type": "*http.Request"},
        ]}},
        "go",
    )
    assert [(s.handler, s.parameter, s.source.module) for s in go] == [(go_fn, "r", "net/http")]
    py = "python:app.py:5-9:item:function"
    assert parameter_seeds(
        _ingress((py, "http_route")), {py: ("item_id",)}, [_edge(py, "item_id", 5, 6)], {}, "python",
    ) == []


def test_express_seeds_the_first_parameter_only_when_it_is_used() -> None:
    seeds = parameter_seeds(
        _ingress((_JS_SAVE, "express_route"), (_JS_PING, "express_route")),
        {_JS_SAVE: ("req", "res"), _JS_PING: ("req", "res")},
        [_edge(_JS_SAVE, "req", 6, 7), _edge(_JS_SAVE, "res", 6, 8), _edge(_JS_PING, "res", 11, 12)],
        {}, "javascript",
    )
    assert [(s.handler, s.parameter, s.line) for s in seeds] == [(_JS_SAVE, "req", 6)]
    assert seeds[0].source.taint_label == "untrusted_input"
    assert seeds[0].source.name == "req" and seeds[0].source.community is False


def test_go_seeds_the_parameter_typed_as_the_request() -> None:
    seeds = parameter_seeds(
        _ingress((_GO_SAVE, "go_handler")),
        {_GO_SAVE: ("w", "r")},
        [_edge(_GO_SAVE, "r", 10, 11), _edge(_GO_SAVE, "w", 10, 11)],
        {_GO_SAVE: {"parameters": [
            {"name": "w", "type": "http.ResponseWriter"}, {"name": "r", "type": "*http.Request"},
        ]}},
        "go",
    )
    assert [(s.handler, s.parameter, s.line) for s in seeds] == [(_GO_SAVE, "r", 10)]


def test_no_seed_without_a_contract_a_ddg_or_a_typed_request_parameter() -> None:
    # An ingress kind with no contract (Flask's request is a global, not a parameter).
    assert parameter_seeds(
        _ingress(("python:app.py:5-9:save:function", "http_route")),
        {"python:app.py:5-9:save:function": ("x",)},
        [_edge("python:app.py:5-9:save:function", "x", 5, 6)], {}, "python",
    ) == []
    # A handler the DDG never walked.
    assert parameter_seeds(_ingress((_JS_SAVE, "express_route")), {}, [], {}, "javascript") == []
    # A Go handler whose recorded parameters carry no *http.Request.
    assert parameter_seeds(
        _ingress((_GO_SAVE, "go_handler")), {_GO_SAVE: ("w", "r")}, [_edge(_GO_SAVE, "r", 10, 11)],
        {_GO_SAVE: {"parameters": [{"name": "w", "type": "http.ResponseWriter"}, {"name": "r", "type": "Thing"}]}},
        "go",
    ) == []
    # Another language's entries are not this language's seeds.
    assert parameter_seeds(
        _ingress((_JS_SAVE, "express_route")), {_JS_SAVE: ("req",)}, [_edge(_JS_SAVE, "req", 6, 7)], {}, "go",
    ) == []


def test_a_seeded_handler_is_a_represented_ingress_caller() -> None:
    seeds = parameter_seeds(
        _ingress((_JS_SAVE, "express_route")), {_JS_SAVE: ("req", "res")},
        [_edge(_JS_SAVE, "req", 6, 7)], {}, "javascript",
    )
    assert represented_ingress_callers([], [], frozenset(), "javascript", parameter_seeds=seeds) == {_JS_SAVE}
    assert represented_ingress_callers([], [], frozenset(), "javascript") == set()


_GO_MARKER = "go:main.go:13-13:ANY /save:function"
_GO_FN = "go:main.go:8-10:save:function"


def test_a_route_marker_entry_seeds_the_function_it_dispatches_to_and_represents_the_marker() -> None:
    """Go's ``http.HandleFunc("/save", save)``: the ingress entry is the marker
    at the registration line; the DDG walked ``save``; the disclosure names
    the marker."""
    seeds = parameter_seeds(
        _ingress((_GO_MARKER, "http_route")), {_GO_FN: ("w", "r")},
        [_edge(_GO_FN, "r", 8, 9)],
        {_GO_FN: {"parameters": [
            {"name": "w", "type": "http.ResponseWriter"}, {"name": "r", "type": "*http.Request"},
        ]}},
        "go",
        edges=[{"src": _GO_MARKER, "dst": _GO_FN, "type": "dispatches_to"}],
    )
    assert [(s.handler, s.parameter, s.line, s.represents) for s in seeds] == [(_GO_FN, "r", 8, _GO_MARKER)]
    assert represented_ingress_callers([], [], frozenset(), "go", parameter_seeds=seeds) == {_GO_MARKER}
    # Without the edge the marker has no handler to seed.
    assert parameter_seeds(
        _ingress((_GO_MARKER, "http_route")), {_GO_FN: ("w", "r")}, [_edge(_GO_FN, "r", 8, 9)],
        {_GO_FN: {"parameters": [{"name": "r", "type": "*http.Request"}]}}, "go",
    ) == []


def test_a_marker_follows_a_dispatch_only_into_its_own_file() -> None:
    """Measured on the curriculum express repo: ``GET /users`` in
    route-separation/index.js dispatches_to ``examples/online/index.js list(ids)``,
    a same-named function of another example the route linker picked by bare
    name; positional 0 would have seeded ``ids`` as the request and represented
    a handler whose real flows were never followed. A cross-file target is no
    handler of this marker's."""
    marker = "javascript:examples/route-separation/index.js:40-40:GET /users:function"
    wrong = "javascript:examples/online/index.js:40-44:list:function"
    right = "javascript:examples/route-separation/index.js:60-64:list:function"
    params = {wrong: ("ids",), right: ("req", "res")}
    edges = [_edge(wrong, "ids", 40, 41), _edge(right, "req", 60, 61)]
    assert parameter_seeds(
        _ingress((marker, "http_route")), params, edges, {}, "javascript",
        edges=[{"src": marker, "dst": wrong, "type": "dispatches_to"}],
    ) == []
    seeds = parameter_seeds(
        _ingress((marker, "http_route")), params, edges, {}, "javascript",
        edges=[{"src": marker, "dst": wrong, "type": "dispatches_to"},
               {"src": marker, "dst": right, "type": "dispatches_to"}],
    )
    assert [(s.handler, s.parameter, s.represents) for s in seeds] == [(right, "req", marker)]


def test_express_error_middleware_takes_its_request_second() -> None:
    """``(err, req, res, next)`` is Express's error-handling signature, by
    arity: the request is parameter 1, and ``err`` is not a request."""
    fn4 = "javascript:test/app.router.js:949-951:fn3:function"
    seeds = parameter_seeds(
        _ingress((fn4, "http_route")), {fn4: ("err", "req", "res", "next")},
        [_edge(fn4, "err", 949, 950), _edge(fn4, "req", 949, 950)], {}, "javascript",
    )
    assert [(s.handler, s.parameter) for s in seeds] == [(fn4, "req")]
    # The same four names with ``req`` unused: no seed, not a fallback to ``err``.
    assert parameter_seeds(
        _ingress((fn4, "http_route")), {fn4: ("err", "req", "res", "next")},
        [_edge(fn4, "err", 949, 950)], {}, "javascript",
    ) == []


def test_the_walk_ignores_a_seed_for_a_handler_it_never_walked() -> None:
    """A seed is only ever minted for a walked handler; the walk still refuses
    one it was not (the structural fallback would be reachability)."""
    seed = ParameterSeed(
        handler=_JS_SAVE, parameter="req", line=6,
        source=TaintSource(taint_label="untrusted_input", module="express", name="req", kind="attribute"),
    )
    assert seed.represents == _JS_SAVE
    other = "javascript:other.js:1-3:f:function"
    findings = propagate_taint_ddg(
        [_edge(other, "x", 1, 2)],
        [{"src": other, "dst": "javascript:fs:0-0:writeFileSync:external_symbol", "type": "calls",
          "line": 2, "is_resolved": False, "meta": {"call_construct": "method"}}],
        [TaintSource(taint_label="untrusted_input", module="express", name="get", kind="method")],
        [TaintSink(zone="host_fs", trust_level="untrusted", module="fs", name="writeFileSync", kind="function")],
        [],
        ddg_symbols={other}, language="javascript", parameter_seeds=[seed],
    )
    assert findings == []
