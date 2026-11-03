# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ritog step 2: a servlet's request accessor is a source, and the
unseeded-ingress disclosure narrows on positive per-handler evidence.

A servlet container hands ``doGet`` its request as a parameter; the handler
reads it through accessors (``req.getParameter("p")``), and the Java analyzer
emits that call with its DECLARED receiver type as the module slot
(``java:jakarta.servlet.http.HttpServletRequest:0-0:getParameter``). So the
arrival is representable by an ordinary caller-seeded ``net_recv`` row, with
no new seed path -- and no catalogue carried one, so the servlet probe read
every ``untrusted_input`` claim as inconclusive, held only by WI-fuzik's
disclosure, over ``new FileWriter(req.getParameter("p"))``.

Two things ship here. (1) ``io_primitives_overlays/java-servlet.yaml``: the
``javax`` / ``jakarta`` ``ServletRequest`` and ``HttpServletRequest`` accessors
as ``net_recv`` sources. java.yaml is strict stdlib, so the file is a
COMMUNITY overlay: by ADR-0061 ruling 2 it may ADD a finding (the probe's
claim goes violated through the flow) but never make a verdict cleaner, so a
handler whose only representation is a community row STAYS in the
disclosure. (2) The disclosure skips a handler that is the CALLER of a
matched ``untrusted_input`` source from a builtin or user-vouched row
(ADR-0049 ruling 3: removal only against a represented crossing), computed
from the very edge and source sets the propagator walks.
"""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import (
    _DEFAULT_OVERLAY_DIR,
    load_catalog,
    load_overlay_catalog,
    overlay_languages_for,
)
from hypergumbo_core.taint import (
    TaintSource,
    load_builtin_taint_catalog,
    represented_ingress_callers,
)
from hypergumbo_core.verify_claims import unseeded_ingress_handlers

_OVERLAY = _DEFAULT_OVERLAY_DIR / "java-servlet.yaml"
_POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>a</groupId>"
    "<artifactId>b</artifactId><version>1</version><dependencies><dependency>"
    "<groupId>jakarta.servlet</groupId><artifactId>jakarta.servlet-api</artifactId>"
    "<version>6.0.0</version></dependency></dependencies></project>\n"
)
_SAVER = (
    "package app;\n\n"
    "import jakarta.servlet.http.HttpServlet;\n"
    "import jakarta.servlet.http.HttpServletRequest;\n"
    "import jakarta.servlet.http.HttpServletResponse;\n"
    "import java.io.FileWriter;\n"
    "import java.io.IOException;\n\n"
    "public class Saver extends HttpServlet {\n"
    "    protected void doGet(HttpServletRequest req, HttpServletResponse resp)"
    " throws IOException {\n"
    '        String p = req.getParameter("p");\n'
    "        FileWriter w = new FileWriter(p);\n"
    '        w.write("x");\n'
    "        w.close();\n"
    "    }\n"
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
)
_HANDLER = "java:src/main/java/app/Saver.java:10-15:Saver.doGet:method"
_GET_PARAMETER = "java:jakarta.servlet.http.HttpServletRequest:0-0:getParameter:external_symbol"


def _verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str,
) -> dict[str, dict]:
    repo = tmp_path / "repo"
    (repo / "src/main/java/app").mkdir(parents=True)
    (repo / "pom.xml").write_text(_POM)
    (repo / "src/main/java/app/Saver.java").write_text(_SAVER)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json", *extra])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


class TestTheOverlay:
    def test_it_is_a_dated_community_overlay_for_java_granting_no_completeness(self) -> None:
        raw = yaml.safe_load(_OVERLAY.read_text())
        assert raw["language"] == "java"
        assert raw["provenance"] == "community"
        assert raw["retrieved"]
        assert "module_completeness" not in raw
        assert load_overlay_catalog(_OVERLAY).language == "java"

    def test_the_request_accessors_are_net_recv_rows_on_both_namespaces(self) -> None:
        raw = yaml.safe_load(_OVERLAY.read_text())
        rows = {(r["module"], m) for r in raw["net_recv"] for m in r["methods"]}
        for ns in ("javax", "jakarta"):
            assert (f"{ns}.servlet.http.HttpServletRequest", "getParameter") in rows
            assert (f"{ns}.servlet.http.HttpServletRequest", "getHeader") in rows
            assert (f"{ns}.servlet.ServletRequest", "getParameter") in rows
            assert (f"{ns}.servlet.ServletRequest", "getInputStream") in rows
        assert not any(m.startswith("set") for _, m in rows)
        assert set(raw) <= {"language", "provenance", "retrieved", "net_recv", "notes"}


def test_the_community_rows_add_the_finding_and_the_handler_stays_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0061 ruling 2: a community row adds a finding, never cleans."""
    verdicts = _verdicts(tmp_path, monkeypatch)
    host_fs = verdicts["untrusted-input-no-host-fs"]
    assert host_fs["verdict"] == "violated", host_fs["details"]
    assert "getParameter" in host_fs["details"], host_fs["details"]
    other = verdicts["untrusted-input-no-subprocess"]
    assert other["verdict"] == "inconclusive", other["details"]
    assert "no taint source represents" in other["details"], other["details"]
    assert "Saver.doGet" in other["details"], other["details"]


def test_a_vouched_row_represents_the_arrival_and_the_handler_leaves_the_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rows supplied by the user (no provenance line: theirs), and
    the shipped community copy omitted: the flow is found AND the handler is
    represented, so the disclosure no longer names it."""
    raw = yaml.safe_load(_OVERLAY.read_text())
    yours = tmp_path / "servlet_yours.yaml"
    yours.write_text(yaml.safe_dump({"language": "java", "net_recv": raw["net_recv"]}))
    verdicts = _verdicts(
        tmp_path, monkeypatch, "--io-primitives", str(yours), "--no-default-overlays",
    )
    assert verdicts["untrusted-input-no-host-fs"]["verdict"] == "violated"
    other = verdicts["untrusted-input-no-subprocess"]
    assert "no taint source represents" not in other["details"], other["details"]
    assert "Saver.doGet" not in other["details"], other["details"]


def _edge(src: str, dst: str) -> dict:
    return {"src": src, "dst": dst, "type": "calls", "is_resolved": False,
            "meta": {"callee_name": dst.rsplit(":", 2)[-2]}}


def test_represented_callers_are_the_callers_of_vouched_untrusted_input_sources() -> None:
    edges = [
        _edge(_HANDLER, _GET_PARAMETER),
        _edge("java:src/Other.java:1-2:Other.run:method",
              "java:java.lang.System:0-0:getenv:external_symbol"),
    ]
    vouched = TaintSource(
        taint_label="untrusted_input", module="jakarta.servlet.http.HttpServletRequest",
        name="getParameter", kind="method",
    )
    secret = TaintSource(
        taint_label="host_secret", module="java.lang.System", name="getenv", kind="method",
    )
    assert represented_ingress_callers(edges, [vouched, secret], frozenset(), "java") == {
        _HANDLER,
    }
    community = replace(vouched, community=True)
    assert represented_ingress_callers(edges, [community, secret], frozenset(), "java") == set()
    assert represented_ingress_callers([], [vouched], frozenset(), "java") == set()


def test_the_disclosure_skips_a_represented_handler_only() -> None:
    entries = [
        {"kind": "http_route", "symbol_id": _HANDLER, "label": "HTTP handler: Saver.doGet"},
        {"kind": "http_route", "symbol_id": "java:src/main/java/app/Other.java:3-4:Other.doPost:method",
         "label": "HTTP handler: Other.doPost"},
    ]
    assert unseeded_ingress_handlers(entries) == [
        "Other.doPost at src/main/java/app/Other.java:3 (HTTP handler: Other.doPost)",
        "Saver.doGet at src/main/java/app/Saver.java:10 (HTTP handler: Saver.doGet)",
    ]
    assert unseeded_ingress_handlers(entries, represented={_HANDLER}) == [
        "Other.doPost at src/main/java/app/Other.java:3 (HTTP handler: Other.doPost)",
    ]


@pytest.mark.parametrize("lang", ["kotlin", "scala"])
def test_a_parented_language_inherits_the_parent_default_overlay(lang: str) -> None:
    """Kotlin and Scala merge Java's builtin rows; they get Java's shipped
    defaults too, through one resolution rule shared by the default list and
    the loader's "mine" test. Before it was shared, the first Java default
    reached Java only."""
    assert overlay_languages_for(lang) == frozenset({lang, "java"})
    assert overlay_languages_for("java") == frozenset({"java"})
    row = ("jakarta.servlet.http.HttpServletRequest", "getParameter")
    merged = load_catalog(lang)
    assert merged.language == lang  # the parent's overlay does not relabel the child
    assert row in {(p.module, p.name) for p in merged.primitives}
    assert row not in {
        (p.module, p.name) for p in load_catalog(lang, include_defaults=False).primitives
    }
    sources = load_builtin_taint_catalog().sources_for_language(lang)
    assert any(
        (s.module, s.name, s.community) == (*row, True) for s in sources
    )
