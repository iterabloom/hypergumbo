# SPDX-License-Identifier: AGPL-3.0-or-later
"""The unseeded-ingress disclosure reads every request handler, not the capped list (WI-vumig).

``detect_entrypoints`` floors its ranked list at ``MIN_ENTRYPOINT_CONFIDENCE``
(a test-file handler ranks x0.1 and falls under it) and caps it at
``compute_entrypoint_cap(node_count)`` with a 40% per-kind share. Both are
right for a ranked overview and wrong for a completeness claim: WI-fuzik's
disclosure ("N request handler(s) receive network data that no taint source
represents") reading ``behavior_map["entrypoints"]`` would name only the
handlers that survived, and on a large repo the cap drops a large share of
the route handlers.

So the survey writer computes the candidates once, derives the capped
``entrypoints`` from them, and writes ``request_ingress`` beside it:
every candidate whose kind is a request-ingress kind, before the floor and the
cap. The disclosure reads that list when the map has it and falls back to
``entrypoints`` for an older map; its ``include_non_production`` flag keeps
deciding test-code handlers.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.entrypoints import (
    BASE_ENTRYPOINT_CAP,
    REQUEST_INGRESS_KINDS,
    EntrypointKind,
    apply_rank_floor_and_cap,
    detect_entrypoint_candidates,
    detect_entrypoints,
    request_ingress_entries,
)
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.verify_claims import ingress_candidates, unseeded_ingress_handlers


def _handler(i: int, *, path: str | None = None, concept: str = "route_handler") -> Symbol:
    p = path or f"src/handlers_{i}.py"
    name = f"H{i}.do_GET"
    return Symbol(
        id=f"python:{p}:1-9:{name}:method",
        name=name, kind="method", language="python", path=p,
        span=Span(1, 9, 0, 0),
        meta={"concepts": [{"concept": concept, "framework": "python-http-server"}]},
    )


def _export(i: int) -> Symbol:
    p = f"src/lib_{i}.py"
    return Symbol(
        id=f"python:{p}:1-3:fn{i}:function", name=f"fn{i}", kind="function",
        language="python", path=p, span=Span(1, 3, 0, 0),
        meta={"concepts": [{"concept": "library_export"}]},
    )


def test_every_handler_beyond_the_cap_is_a_request_ingress_entry() -> None:
    nodes = [_handler(i) for i in range(BASE_ENTRYPOINT_CAP + 10)]
    capped = detect_entrypoints(nodes, [])
    assert len(capped) <= BASE_ENTRYPOINT_CAP
    entries = request_ingress_entries(detect_entrypoint_candidates(nodes, []))
    assert len(entries) == BASE_ENTRYPOINT_CAP + 10
    assert {e["kind"] for e in entries} == {EntrypointKind.HTTP_ROUTE.value}
    assert {e["symbol_id"] for e in entries} == {n.id for n in nodes}
    assert all(e["label"] for e in entries)


def test_the_capped_list_is_the_candidates_floored_and_capped() -> None:
    nodes = [_handler(i) for i in range(BASE_ENTRYPOINT_CAP + 10)] + [_export(0)]
    cands = detect_entrypoint_candidates(nodes, [])
    assert [e.symbol_id for e in detect_entrypoints(nodes, [])] == [
        e.symbol_id for e in apply_rank_floor_and_cap(cands, len(nodes))
    ]


def test_a_test_file_handler_is_an_entry_the_disclosure_scopes() -> None:
    """Below the rank floor (x0.1) it is no entrypoint; it is still a request
    ingress entry, and the disclosure's own scope flag decides whether to name
    it, as it does for flows."""
    prod = _handler(0)
    fixture = _handler(1, path="tests/test_srv.py")
    assert fixture.id not in {e.symbol_id for e in detect_entrypoints([prod, fixture], [])}
    entries = request_ingress_entries(detect_entrypoint_candidates([prod, fixture], []))
    assert {e["symbol_id"] for e in entries} == {prod.id, fixture.id}
    named = unseeded_ingress_handlers(entries)
    assert len(named) == 1 and "H0.do_GET" in named[0]
    named_all = unseeded_ingress_handlers(entries, include_non_production=True)
    assert len(named_all) == 2


def test_a_non_ingress_kind_is_not_an_entry() -> None:
    entries = request_ingress_entries(detect_entrypoint_candidates([_export(0), _handler(0)], []))
    assert [e["kind"] for e in entries] == [EntrypointKind.HTTP_ROUTE.value]
    assert EntrypointKind.LIBRARY_EXPORT.value not in REQUEST_INGRESS_KINDS


def test_the_disclosure_reads_request_ingress_and_falls_back_to_entrypoints() -> None:
    capped = [{"symbol_id": "python:src/a.py:1-2:A.do_GET:method", "kind": "http_route", "label": "x"}]
    full = capped + [{"symbol_id": "python:src/b.py:1-2:B.do_GET:method", "kind": "http_route", "label": "y"}]
    assert ingress_candidates({"entrypoints": capped, "request_ingress": full}) == full
    assert ingress_candidates({"entrypoints": capped}) == capped
    assert ingress_candidates({}) == []
    assert len(unseeded_ingress_handlers(ingress_candidates({"entrypoints": capped, "request_ingress": full}))) == 2


_N = BASE_ENTRYPOINT_CAP + 10
_CLAIMS = (
    "claims:\n"
    "  - id: untrusted-input-no-host-fs\n"
    "    text: request data never chooses a file to write\n"
    "    constraint:\n"
    "      taint_flow:\n"
    "        source_taint: untrusted_input\n"
    "        prohibited_sink_zone: host_fs\n"
)


def _handlers_module() -> str:
    """One module holding _N http.server handlers: more than the small-repo cap."""
    return "from http.server import BaseHTTPRequestHandler\n" + "".join(
        f"\n\nclass H{i}(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        with open(self.path) as f:\n"
        "            self.wfile.write(f.read().encode())\n"
        for i in range(_N)
    )


def _details(repo: Path, claims: Path, bmap: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json",
              "--input", str(bmap)])
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    details: str = verdicts["untrusted-input-no-host-fs"]["details"]
    return details


def test_the_disclosure_counts_every_handler_beyond_the_cap_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """survey -> artifact -> verify-claims. The artifact carries the capped
    ``entrypoints`` and the full ``request_ingress``; the disclosure counts the
    full list. The same artifact with the key removed (a map written before
    the key existed) counts the capped list: partial, and not invented."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "srv.py").write_text(_handlers_module())
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    out = tmp_path / "map.json"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        main(["survey", str(repo), "--out", str(out)])
    bmap = json.loads(out.read_text())
    routes = [e for e in bmap["entrypoints"] if e["kind"] == "http_route"]
    assert len(routes) <= BASE_ENTRYPOINT_CAP < _N
    assert len(bmap["request_ingress"]) == _N
    assert {e["kind"] for e in bmap["request_ingress"]} == {"http_route"}
    assert {e["symbol_id"] for e in routes} < {e["symbol_id"] for e in bmap["request_ingress"]}

    assert f"{_N} request handler(s)" in _details(repo, claims, out)

    old = tmp_path / "old_map.json"
    del bmap["request_ingress"]
    old.write_text(json.dumps(bmap))
    assert f"{len(routes)} request handler(s)" in _details(repo, claims, old)
