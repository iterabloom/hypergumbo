# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tumol: the read through an HTTP response or a connected socket mints a source.

THE DEFECT. ``urlopen`` / ``OpenerDirector.open`` /
``HTTPConnection.request`` are ``net_send`` only, deliberately: the response
is a consequence of the send, and what they return is a HANDLE (ADR-0049
Ruling 4). The far side's bytes reach the program where that handle is READ,
and nothing rowed the read: ``http.client.HTTPResponse`` carried no row and
nothing typed a response. ``socket.create_connection`` is a function returning
a ``socket.socket``, and nothing typed that either, so ``s.recv()`` -- rowed
``net_recv`` on ``socket.socket`` -- was unreachable from it. So, on a
``untrusted_input -> subprocess`` claim, each of these read
``confirmed_with_caveats``, a false clean with the network body visibly
reaching a shell::

    body = urllib.request.urlopen(url).read();             subprocess.run(body, shell=True)
    body = urllib.request.build_opener().open(url).read(); subprocess.run(body, shell=True)
    s = socket.create_connection((h, 80)); body = s.recv(1024); subprocess.run(body, shell=True)

THE FIX, rows plus one general rule:

* ``library_signatures/python.yaml`` types the producers: ``urlopen`` and
  ``OpenerDirector.open`` -> ``http.client.HTTPResponse`` (the http/https
  type; a ``file:`` / ``data:`` / ``ftp:`` URL returns
  ``urllib.response.addinfourl``, which is not typed and carries no row), the
  ``HTTPConnection`` / ``HTTPSConnection`` ``getresponse`` members likewise,
  and ``socket.create_connection`` -> ``socket.socket``.
* ``OpenerDirector.open`` and ``getresponse`` are MEMBERS whose return type
  differs from their owner, which a typed receiver did not propagate (WI-kozaj's
  stated residual). py.py's receiver hop now asks the member's signature row
  for its return type; a type-preserving row is the special case whose value is
  its owner (``test_py_member_return_types.py``).
* ``http.client.HTTPResponse``'s reads are rowed ``net_recv`` -- the call where
  the bytes cross (ADR-0049 Ruling 1: the call returns content the far side
  chose) -- and its public surface is enumerated, so a typed response's other
  calls (``close``, ``isclosed``, ...) are examined rather than withholding a
  verdict as unclassified.

Every assertion runs on REAL analyzer and verify-claims output and asserts
reach first.
"""
from __future__ import annotations

import contextlib
import io
import json
import socket
import threading
from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges, main
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.library_signatures import load_library_signatures
from hypergumbo_core.taint import load_builtin_taint_catalog

_RESPONSE = "http.client.HTTPResponse"

#: Every HTTPResponse member whose return value (or, for ``readinto`` /
#: ``readinto1``, the buffer it fills; for ``begin``, the attributes it sets)
#: is content the far side chose.
_RECV = {"begin", "getcode", "getheader", "getheaders", "geturl", "info", "peek",
         "read", "read1", "readinto", "readinto1", "readline", "readlines"}

_CLAIMS = """claims:
  - id: untrusted-no-shell
    text: Data from the network never reaches a shell.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: subprocess
  - id: secret-no-network
    text: An environment secret is never sent over the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""

_SHAPES = {
    "urlopen-chain": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    body = urllib.request.urlopen(url).read()\n"
        "    subprocess.run(body, shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "urlopen-with": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    with urllib.request.urlopen(url) as resp:\n"
        "        body = resp.read()\n"
        "    subprocess.run(body, shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "urlopen-from-import-readlines": (
        "import subprocess\nfrom urllib.request import urlopen\n\n\ndef run(url):\n"
        "    resp = urlopen(url)\n"
        "    for line in resp.readlines():\n"
        "        subprocess.run(line, shell=True)\n",
        f"{_RESPONSE}.readlines",
    ),
    "opener-chain": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    body = urllib.request.build_opener().open(url).read()\n"
        "    subprocess.run(body, shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "opener-assigned": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    opener = urllib.request.build_opener()\n"
        "    resp = opener.open(url)\n"
        "    subprocess.run(resp.read(), shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "opener-with": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    opener = urllib.request.build_opener()\n"
        "    with opener.open(url) as source:\n"
        "        body = source.read()\n"
        "    subprocess.run(body, shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "header": (
        "import subprocess\nimport urllib.request\n\n\ndef run(url):\n"
        "    resp = urllib.request.urlopen(url)\n"
        "    subprocess.run(resp.getheader('X-Cmd'), shell=True)\n",
        f"{_RESPONSE}.getheader",
    ),
    "http-client-getresponse": (
        "import http.client\nimport subprocess\n\n\ndef run(h):\n"
        "    conn = http.client.HTTPSConnection(h)\n"
        "    conn.request('GET', '/')\n"
        "    resp = conn.getresponse()\n"
        "    subprocess.run(resp.read(), shell=True)\n",
        f"{_RESPONSE}.read",
    ),
    "create-connection-recv": (
        "import socket\nimport subprocess\n\n\ndef run(h):\n"
        "    s = socket.create_connection((h, 80))\n"
        "    body = s.recv(1024)\n"
        "    subprocess.run(body, shell=True)\n",
        "socket.socket.recv",
    ),
}


def _tagged(tmp_path: Path, source: str) -> dict:
    """``{(callee module, callee name): (boundary, primitive)}`` over call edges."""
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
        out[(parts[1], parts[-2])] = (meta.get("io_boundary"), meta.get("io_primitive"))
    return out


def _verify(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """``{claim id: verdict}`` of the shipped ``verify-claims`` over ``source``."""
    repo = tmp_path / "vrepo"
    repo.mkdir()
    (repo / "app.py").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


def _flows(verdict: dict) -> set[tuple[frozenset, frozenset]]:
    return {(frozenset(ev["source_primitives"]), frozenset(ev["sink_primitives"]))
            for ev in verdict["evidence"]}


class TestTheRows:

    def test_the_reads_are_net_recv_methods(self) -> None:
        rows = {p.name: (p.boundary, p.kind) for p in load_catalog("python").primitives
                if p.module == _RESPONSE}
        assert rows == dict.fromkeys(_RECV, ("net_recv", "method"))

    @pytest.mark.parametrize("name", sorted(_RECV))
    def test_each_is_an_untrusted_input_source(self, name: str) -> None:
        sources = {(s.module, s.name): s.taint_label
                   for s in load_builtin_taint_catalog().sources_for_language("python")}
        assert sources.get((_RESPONSE, name)) == "untrusted_input"

    def test_the_class_surface_is_enumerated(self) -> None:
        assert load_catalog("python").module_io_is_enumerated(_RESPONSE)

    def test_the_enumerated_surface_is_the_real_one(self) -> None:
        """The completeness note adjudicates every public member; checked
        against the running interpreter, not asserted from a doc."""
        import http.client

        public = {n for n in dir(http.client.HTTPResponse) if not n.startswith("_")}
        unrowed = {"close", "closed", "detach", "fileno", "flush", "isatty",
                   "isclosed", "readable", "seek", "seekable", "tell",
                   "truncate", "writable", "write", "writelines"}
        assert public == _RECV | unrowed, sorted(public ^ (_RECV | unrowed))

    def test_addinfourl_carries_no_row(self) -> None:
        """Nothing types a receiver ``urllib.response.addinfourl``, so a row
        there would be a value with no producer (LIVE rule 7)."""
        assert not [p for p in load_catalog("python").primitives
                    if p.module == "urllib.response.addinfourl"]


class TestTheProducerRows:

    @pytest.mark.parametrize("key,value", [
        ("urllib.request.urlopen", _RESPONSE),
        ("urllib.request.OpenerDirector.open", _RESPONSE),
        ("http.client.HTTPConnection.getresponse", _RESPONSE),
        ("http.client.HTTPSConnection.getresponse", _RESPONSE),
        ("socket.create_connection", "socket.socket"),
    ])
    def test_the_return_type_is_declared(self, key: str, value: str) -> None:
        assert load_library_signatures("python")[key] == value

    def test_the_declared_types_are_the_real_ones(self) -> None:
        """On a loopback server: no host outside this process is contacted.
        ``ProxyHandler({})`` keeps a ``*_proxy`` variable in the test
        environment from routing the request elsewhere."""
        import http.client
        import urllib.request

        server = socket.create_server(("127.0.0.1", 0))
        port = server.getsockname()[1]

        def serve(n: int) -> None:
            for _ in range(n):
                conn, _addr = server.accept()
                with conn:
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")

        worker = threading.Thread(target=serve, args=(3,), daemon=True)
        worker.start()
        try:
            sock = socket.create_connection(("127.0.0.1", port))
            assert type(sock) is socket.socket
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
            sock.recv(4096)
            sock.close()

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/") as resp:
                assert type(resp) is http.client.HTTPResponse

            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert type(resp) is http.client.HTTPResponse
            resp.read()
            conn.close()
        finally:
            worker.join(timeout=10)
            server.close()

    def test_a_data_url_returns_the_untyped_class(self) -> None:
        """The scheme decides the type, which is why ``addinfourl`` is named in
        the row's comment and not typed: a ``data:`` read is not a network
        receive."""
        import urllib.request
        import urllib.response

        with urllib.request.urlopen("data:,x") as resp:  # noqa: S310 -- a data: URL, no I/O
            assert type(resp) is urllib.response.addinfourl


class TestTheAnalyzedReadClassifies:

    @pytest.mark.parametrize("shape", sorted(_SHAPES))
    def test_the_read_is_net_recv(self, tmp_path: Path, shape: str) -> None:
        source, primitive = _SHAPES[shape]
        module, _, name = primitive.rpartition(".")
        tagged = _tagged(tmp_path, source)
        assert (module, name) in tagged, sorted(tagged)  # reach: receiver typed
        assert tagged[(module, name)] == ("net_recv", primitive)


class TestTheFindingIsReported:
    """The item's repro at the surface a user reads. Each was
    ``confirmed_with_caveats`` (``http-client-getresponse`` ``inconclusive``)."""

    @pytest.mark.parametrize("shape", sorted(_SHAPES))
    def test_network_bytes_reaching_a_shell_are_violated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
    ) -> None:
        source, primitive = _SHAPES[shape]
        verdict = _verify(tmp_path, source, monkeypatch)["untrusted-no-shell"]
        assert verdict["verdict"] == "violated", verdict["details"]
        assert (frozenset({primitive}), frozenset({"subprocess.run"})) in _flows(
            verdict), _flows(verdict)

    def test_a_response_passed_to_a_helper_is_followed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = (
            "import subprocess\nimport urllib.request\n\n\n"
            "def run_body(resp):\n    subprocess.run(resp.read(), shell=True)\n\n\n"
            "def main(url):\n    run_body(urllib.request.urlopen(url))\n"
        )
        verdict = _verify(tmp_path, source, monkeypatch)["untrusted-no-shell"]
        assert verdict["verdict"] == "violated", verdict["details"]

    def test_control_a_response_read_into_nothing_dangerous_is_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The source alone is not a finding, and the typed response's other
        calls are examined (completeness), so no caveat survives."""
        source = (
            "import urllib.request\n\n\ndef size(url):\n"
            "    resp = urllib.request.urlopen(url)\n"
            "    n = len(resp.read())\n"
            "    resp.close()\n"
            "    return n, resp.isclosed()\n"
        )
        verdict = _verify(tmp_path, source, monkeypatch)["untrusted-no-shell"]
        assert verdict["verdict"] == "confirmed", (
            verdict["verdict"], verdict["caveats"])

    def test_control_a_plain_urlopen_stays_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        verdicts = _verify(tmp_path, (
            "import urllib.request\n\n\ndef ping():\n"
            "    urllib.request.urlopen('https://example.com/status')\n"
        ), monkeypatch)
        assert {k: v["verdict"] for k, v in verdicts.items()} == {
            "untrusted-no-shell": "confirmed", "secret-no-network": "confirmed",
        }

    def test_control_a_secret_sent_through_an_opener_is_still_violated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        verdict = _verify(tmp_path, (
            "import os\nimport urllib.request\n\n\ndef leak(url):\n"
            "    secret = os.environ['API_KEY']\n"
            "    opener = urllib.request.build_opener()\n"
            "    opener.open(url, data=secret.encode())\n"
        ), monkeypatch)["secret-no-network"]
        assert verdict["verdict"] == "violated", verdict["details"]
        assert (frozenset({"os.environ"}),
                frozenset({"urllib.request.OpenerDirector.open"})) in _flows(verdict)
