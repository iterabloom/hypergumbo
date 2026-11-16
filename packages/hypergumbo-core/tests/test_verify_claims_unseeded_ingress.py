# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fuzik: a request handler no taint source represents withholds the verdict.

A web framework hands a route handler its request as a PARAMETER (Express
``req.query.p``) or a framework GLOBAL (Flask ``request.args``). Taint sources
are minted only from catalogue rows matched on call edges, so that request is
never a source (WI-ritog), and a handler that writes it to disk produced no
finding. Until now the clean verdict was withheld only by an UNRELATED reason:
"1 module(s) the I/O catalog could not classify (flask)". Measured (step 0a):
a one-entry overlay declaring ``flask`` complete turned
``untrusted-input-no-host-fs`` into ``confirmed_with_caveats`` over
``open(request.args.get("p"), "w")`` -- the textbook path-traversal write.

ADR-0049 ruling 3: an ingress is licensed only when its arrival scope is
represented as a source OR a disclosure is in place. These tests pin the
disclosure: an ``untrusted_input`` taint verdict and a ``net_recv`` boundary
verdict are withheld while the graph holds request-handler entrypoints, the
reason names each handler, and the withhold holds with the framework module
classified -- the control showing the accident no longer carries the verdict.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.verify_claims import (
    ClaimVerdict,
    _withhold_for_unseeded_ingress,
    unseeded_ingress_handlers,
)

_FLASK_APP = (
    "from flask import Flask, request\n\n"
    "app = Flask(__name__)\n\n\n"
    '@app.route("/save")\n'
    "def save():\n"
    '    p = request.args.get("p")\n'
    '    with open(p, "w") as f:\n'
    '        f.write("x")\n'
    '    return "ok"\n'
)
_EXPRESS_APP = (
    'const express = require("express");\n'
    'const fs = require("fs");\n\n'
    "const app = express();\n\n"
    'app.get("/save", (req, res) => {\n'
    '  fs.writeFileSync(req.query.p, "x");\n'
    '  res.send("ok");\n'
    "});\n\n"
    "app.listen(3000);\n"
)
# WI-jonin: Go reaches a handler through the http.Handler INTERFACE. Before it
# was an entrypoint this read confirmed_with_caveats with NO overlay: nothing
# withheld it at all. The HandleFunc registration was already a route.
_GO_SERVEHTTP = (
    "package main\n\n"
    'import (\n\t"net/http"\n\t"os"\n)\n\n'
    "type saver struct{}\n\n"
    "func (saver) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
    '\tos.WriteFile(r.URL.Query().Get("p"), []byte("x"), 0o644)\n'
    "}\n\n"
    "func main() {\n"
    '\thttp.ListenAndServe(":8080", saver{})\n'
    "}\n"
)
_GO_HANDLEFUNC = (
    "package main\n\n"
    'import (\n\t"net/http"\n\t"os"\n)\n\n'
    "func save(w http.ResponseWriter, r *http.Request) {\n"
    '\tos.WriteFile(r.URL.Query().Get("p"), []byte("x"), 0o644)\n'
    "}\n\n"
    "func main() {\n"
    '\thttp.HandleFunc("/save", save)\n'
    '\thttp.ListenAndServe(":8080", nil)\n'
    "}\n"
)
_NO_ROUTE_SCRIPT = (
    "import os\n\n\n"
    "def main():\n"
    '    with open(os.path.join("out", "x.txt"), "w") as f:\n'
    '        f.write("x")\n\n\n'
    "main()\n"
)
_CLAIMS = (
    "claims:\n"
    "  - id: untrusted-input-no-host-fs\n"
    "    text: request data never chooses a file to write\n"
    "    constraint:\n"
    "      taint_flow:\n"
    "        source_taint: untrusted_input\n"
    "        prohibited_sink_zone: host_fs\n"
    "  - id: never-receives-network-data\n"
    "    text: the program never receives network data\n"
    "    constraint:\n"
    "      boundary: net_recv\n"
    "      must_not_exist: true\n"
)


def _overlay(tmp_path: Path, language: str, module: str) -> Path:
    path = tmp_path / f"{module.replace('/', '_')}_complete.yaml"
    path.write_text(
        f"language: {language}\n"
        "module_completeness:\n"
        f"  - module: {module}\n"
        "    completeness: complete\n"
        '    retrieved: "2026-11-02"\n'
        "    notes: test overlay (WI-fuzik)\n"
    )
    return path


def _verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str],
    extra: list[str] | None = None,
) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json",
              *(extra or [])])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


@pytest.mark.parametrize("app,language,module,handler,parameter_seeded", [
    # Flask's request is a framework GLOBAL: no parameter to seed (WI-gabak gave
    # its chains a slot; the rows are WI-ritog's remaining Flask step).
    (("app.py", _FLASK_APP), "python", "flask", "save", False),
    # Express and Go hand the request as a PARAMETER, which WI-sosov seeds.
    (("server.js", _EXPRESS_APP), "javascript", "express", "_GET_save_handler", True),
    (("main.go", _GO_SERVEHTTP), "go", "net/http", "saver.ServeHTTP", True),
    (("main.go", _GO_HANDLEFUNC), "go", "net/http", "save", True),
])
@pytest.mark.parametrize("classified", [False, True])
def test_a_request_handler_withholds_the_untrusted_input_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    app: tuple[str, str], language: str, module: str, handler: str,
    parameter_seeded: bool, classified: bool,
) -> None:
    extra = (["--io-primitives", str(_overlay(tmp_path, language, module))]
             if classified else None)
    verdicts = _verdicts(tmp_path, monkeypatch, dict([app]), extra)
    taint = verdicts["untrusted-input-no-host-fs"]
    if parameter_seeded:
        # The request parameter is followed to the write: a finding, and the
        # handler is a represented arrival for the TAINT arm.
        assert taint["verdict"] == "violated", taint["details"]
        assert "no taint source represents" not in taint["details"], taint["details"]
    else:
        assert taint["verdict"] == "inconclusive", taint["details"]
        assert "no taint source represents" in taint["details"], taint["details"]
        assert handler in taint["details"], taint["details"]
    # The BOUNDARY arm is withheld either way: no net_recv crossing stands
    # behind a request that arrives as a parameter or a global, followed or not.
    boundary = verdicts["never-receives-network-data"]
    assert boundary["verdict"] == "inconclusive", boundary["details"]
    assert "receive network data that no" in boundary["details"], boundary["details"]
    assert handler in boundary["details"], boundary["details"]


def test_a_program_with_no_request_handler_gets_no_ingress_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, {"tool.py": _NO_ROUTE_SCRIPT})
    for verdict in verdicts.values():
        assert "no taint source represents" not in verdict["details"]


def test_the_handlers_are_the_request_ingress_entrypoints_only() -> None:
    entrypoints = [
        {"kind": "http_route", "symbol_id": "python:app.py:7-11:save:function",
         "label": "HTTP route /save"},
        {"kind": "express_route", "symbol_id": "javascript:s.js:6-9:h:function",
         "label": "HTTP GET /h"},
        {"kind": "main_function", "symbol_id": "python:app.py:3-3:app:variable",
         "label": "Flask application"},
        {"kind": "cli_command", "symbol_id": "python:cli.py:1-5:run:function",
         "label": "CLI run"},
        {"kind": "http_route", "symbol_id": "python:tests/test_app.py:1-3:fake:function",
         "label": "HTTP route /fake"},
        {"kind": "http_route", "label": "HTTP route /no-symbol"},
        {"kind": "go_handler", "node_id": "go:srv.go:4-9:Serve:function"},
    ]
    assert unseeded_ingress_handlers(entrypoints) == [
        "Serve at srv.go:4", "h at s.js:6 (HTTP GET /h)",
        "save at app.py:7 (HTTP route /save)",
    ]
    assert unseeded_ingress_handlers(entrypoints, include_non_production=True) == [
        "Serve at srv.go:4", "fake at tests/test_app.py:1 (HTTP route /fake)",
        "h at s.js:6 (HTTP GET /h)", "save at app.py:7 (HTTP route /save)",
    ]


def _verdict(kind: str) -> ClaimVerdict:
    return ClaimVerdict(claim_id="c", claim_text="t", verdict=kind, details="clean.")


def test_only_a_clean_verdict_on_an_untrusted_input_label_is_withheld() -> None:
    handlers = ["save at app.py:7 (HTTP route /save)"]
    withheld = _withhold_for_unseeded_ingress(
        _verdict("confirmed"), "untrusted_input", handlers)
    assert withheld.verdict == "inconclusive"
    assert "save at app.py:7" in withheld.details
    assert _withhold_for_unseeded_ingress(
        _verdict("confirmed"), "host_secret", handlers).verdict == "confirmed"
    assert _withhold_for_unseeded_ingress(
        _verdict("violated"), "untrusted_input", handlers).verdict == "violated"
    assert _withhold_for_unseeded_ingress(
        _verdict("confirmed"), "untrusted_input", []).verdict == "confirmed"
