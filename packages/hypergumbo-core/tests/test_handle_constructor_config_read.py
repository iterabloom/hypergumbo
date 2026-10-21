# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-patit: a handle constructor's own configuration read taints no value.

THE DEFECT. ``urllib.request.build_opener()`` is rowed ``env_read`` (WI-bakik):
it always installs a default ``ProxyHandler``, whose ``__init__`` calls
``getproxies()`` and so reads the ``*_proxy`` variables. Every ``env_read`` row
auto-derives a ``host_secret`` taint SOURCE, and a source taints the value the
call returns. ``build_opener`` returns an ``OpenerDirector``, so the opener was
``host_secret``, and taint models the RECEIVER of a sink as a carrier: once
WI-dibit rowed ``OpenerDirector.open`` ``net_send``, every
``opener.open(url)`` -- with no secret in any argument -- read ``violated`` on
``host_secret -> network``. Plain ``urlopen(url)`` performs the same proxy read
(it builds the same default opener) and read clean, so two spellings of one
send disagreed.

THE RULING (owner, 2026-10-04, re-confirmed 2026-10-20; recorded on WI-patit). OPENER SENDS STAY CLEAN.
A handle constructor's own configuration read is not data sent through the
handle (ADR-0049 Ruling 4, the Handle-constructor shape: "returns to caller: a
handle, not data").

THE MECHANISM, GENERAL BY CONSTRUCTION. Not a ``build_opener`` special case:
an io_primitives row may declare ``returns_handle: true``. The row's boundary
is still classified at the call -- the environment IS read there, and
``io-boundaries`` and a ``must_not_exist: env_read`` claim still see it -- but
the auto-derived ``TaintSource`` carries ``return_tainted=False``, and
:func:`taint.find_source_callers` (the one place both propagators ask "what
does this call mint?") mints nothing for a source that taints neither its
return value nor an argument, unless it is callee-seeded (an entry point, not
a returned value). INV-komoj's return lift reads ``return_tainted`` the same
way, so a helper that RETURNS ``build_opener()`` does not carry the read into
its caller either (pinned below).

ROWS IT AFFECTS. The four urllib handle constructors in ``env_read`` --
``ProxyHandler``, ``build_opener``, ``URLopener``, ``FancyURLopener`` -- and
``xml.dom.getDOMImplementation`` (which reads ``PYTHON_DOM`` to pick an
implementation and returns that implementation object). The rest of the python
``env_read`` block returns the data it read and keeps minting: ``getproxies``
and ``getproxies_environment`` return the proxy mapping (WI-bakik's real
``getproxies() -> urlopen`` finding is pinned below), ``proxy_bypass*`` a bool
computed from ``no_proxy``, ``sysconfig.get_platform`` a string. No other
language's ``env_read`` block has a handle-shaped row (enumerated: bash, c,
cpp, elixir, erlang, go, haskell, java, javascript, kotlin, objc, rust, scala,
swift -- every row returns the value it read).

Every assertion runs on real catalogue / analyzer / verify-claims output and
asserts reach first.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges, main
from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    load_catalog,
    tag_io_boundaries,
)
from hypergumbo_core.taint import (
    TaintSource,
    find_source_callers,
    load_builtin_taint_catalog,
)

_HANDLE_ROWS = {
    ("urllib.request", "ProxyHandler"),
    ("urllib.request", "build_opener"),
    ("urllib.request", "URLopener"),
    ("urllib.request", "FancyURLopener"),
    ("xml.dom", "getDOMImplementation"),
}

_CLAIMS = """claims:
  - id: secret-no-network
    text: An environment secret is never sent over the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""


def _verify(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    repo = tmp_path / "vrepo"
    repo.mkdir(parents=True)
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
    return {(frozenset(ev["source_primitives"]), frozenset(ev["sink_primitives"]))
            for ev in verdict["evidence"]}


def _yaml_catalog(tmp_path: Path, row: str) -> IoBoundaryCatalog:
    path = tmp_path / "python.yaml"
    path.write_text(
        "provenance: builtin\nlanguage: python\nstatus: in_progress\n" + row,
    )
    return IoBoundaryCatalog.from_yaml(path)


class TestTheRowKey:

    def test_the_marker_is_read(self, tmp_path: Path) -> None:
        cat = _yaml_catalog(
            tmp_path, "env_read:\n  - module: acme\n    functions: [make]\n"
            "    returns_handle: true\n",
        )
        (prim,) = cat.primitives
        assert prim.returns_handle is True

    def test_absent_is_false(self, tmp_path: Path) -> None:
        cat = _yaml_catalog(
            tmp_path, "env_read:\n  - module: acme\n    functions: [get]\n",
        )
        (prim,) = cat.primitives
        assert prim.returns_handle is False

    def test_a_non_boolean_is_refused(self, tmp_path: Path) -> None:
        """``returns_handle: "yes"`` would read as a ruling and decide nothing."""
        with pytest.raises(ValueError, match="returns_handle"):
            _yaml_catalog(
                tmp_path, "env_read:\n  - module: acme\n    functions: [make]\n"
                "    returns_handle: 'yes'\n",
            )

    def test_an_outbound_boundary_is_refused(self, tmp_path: Path) -> None:
        """The marker qualifies what a READ hands back; on a write-direction
        row it would derive nothing and read as if it had."""
        with pytest.raises(ValueError, match="returns_handle"):
            _yaml_catalog(
                tmp_path, "net_send:\n  - module: acme\n    functions: [post]\n"
                "    returns_handle: true\n",
            )


class TestTheShippedRows:

    def test_exactly_the_handle_constructors_carry_it(self) -> None:
        marked = {(p.module, p.name) for p in load_catalog("python").primitives
                  if p.returns_handle}
        assert marked == _HANDLE_ROWS

    def test_they_stay_env_read(self) -> None:
        """The read happens at the call: the BOUNDARY is unchanged."""
        boundaries = {(p.module, p.name): p.boundary
                      for p in load_catalog("python").primitives
                      if (p.module, p.name) in _HANDLE_ROWS}
        assert boundaries == dict.fromkeys(_HANDLE_ROWS, "env_read")

    def test_no_other_language_carries_it(self) -> None:
        import hypergumbo_core

        catalogue_dir = Path(hypergumbo_core.__file__).parent / "io_primitives"
        langs = {p.stem for p in catalogue_dir.glob("*.yaml")} - {"python"}
        assert langs, "reach: no other catalogue found"
        for lang in sorted(langs):
            assert not [p for p in load_catalog(lang).primitives
                        if p.returns_handle], lang

    def test_the_derived_source_taints_no_return(self) -> None:
        sources = {(s.module, s.name): s for s in
                   load_builtin_taint_catalog().sources_for_language("python")
                   if s.source_boundary == "env_read"}
        for key in _HANDLE_ROWS:
            assert key in sources, key  # reach: still a derived source
            assert sources[key].return_tainted is False, key
        assert sources[("urllib.request", "getproxies")].return_tainted is True
        assert sources[("os", "getenv")].return_tainted is True


class TestTheMintingRule:
    """``find_source_callers`` is where both propagators learn what a call mints."""

    _EDGE: ClassVar[dict] = {
        "src": "python:app.py:4-6:fetch:function",
        "dst": "python:acme:0-0:make:external_symbol",
        "type": "calls", "is_resolved": False, "meta": {},
    }

    def _found(self, **kw) -> list:
        src = TaintSource(taint_label="host_secret", module="acme", name="make",
                          kind="function", **kw)
        return find_source_callers([dict(self._EDGE)], [src], frozenset(), "python")

    def test_a_source_that_taints_its_return_mints(self) -> None:
        assert [s.name for _a, _b, s in self._found()] == ["make"]

    def test_a_source_that_taints_nothing_mints_nothing(self) -> None:
        assert self._found(return_tainted=False) == []

    def test_a_callee_seeded_source_still_mints(self) -> None:
        """``start_at: callee`` names an entry point whose BFS seeds at the
        callee: ``return_tainted`` says nothing about it, so it is not dropped."""
        found = self._found(return_tainted=False, start_at="callee")
        assert [s.name for _a, _b, s in found] == ["make"]

    def test_a_source_that_taints_an_argument_still_mints(self) -> None:
        """Argument taint is not modelled separately; refusing it would turn a
        declared source into a silent one (the false-negative direction)."""
        found = self._found(return_tainted=False, argument_tainted=(0,))
        assert [s.name for _a, _b, s in found] == ["make"]


_OPENER_CLEAN = (
    "import urllib.request\n\n\ndef fetch():\n"
    "    opener = urllib.request.build_opener()\n"
    "    opener.open('https://example.com/status')\n"
)


class TestOpenerSendsStayClean:
    """The owner's ruling at the surface a user reads."""

    @pytest.mark.parametrize("source", [
        _OPENER_CLEAN,
        "from urllib.request import build_opener\n\n\ndef fetch(url):\n"
        "    build_opener().open(url)\n",
        "import urllib.request\n\n\ndef fetch(url):\n"
        "    opener = urllib.request.build_opener(urllib.request.ProxyHandler())\n"
        "    opener.open(url)\n",
        "import urllib.request\n\n\ndef post(opener, url):\n"
        "    opener.open(url, b'ping')\n\n\ndef main(url):\n"
        "    post(urllib.request.build_opener(), url)\n",
        "import urllib.request\n\n\ndef make():\n"
        "    return urllib.request.build_opener()\n\n\ndef fetch(url):\n"
        "    make().open(url)\n",
    ], ids=["assigned", "chained-from-import", "explicit-proxy-handler",
            "through-a-parameter", "returned-from-a-helper"])
    def test_an_opener_send_with_no_secret_is_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
    ) -> None:
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"] in {"confirmed", "confirmed_with_caveats"}, (
            verdict["verdict"], verdict["details"])
        assert _flows(verdict) == set()

    def test_the_twin_spelling_agrees(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``urlopen(url)`` does the same proxy read and was already clean."""
        a = _verify(tmp_path / "a", _OPENER_CLEAN, monkeypatch)
        b = _verify(tmp_path / "b", (
            "import urllib.request\n\n\ndef fetch():\n"
            "    urllib.request.urlopen('https://example.com/status')\n"
        ), monkeypatch)
        assert a["verdict"] == b["verdict"] == "confirmed", (
            a["verdict"], a["caveats"], b["verdict"], b["caveats"])

    def test_control_a_secret_sent_through_the_opener_is_still_violated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The SEND row is untouched; only the constructor's own read is gone
        from the flow set."""
        verdict = _verify(tmp_path, (
            "import os\nimport urllib.request\n\n\ndef leak(url):\n"
            "    secret = os.environ['API_KEY']\n"
            "    opener = urllib.request.build_opener()\n"
            "    opener.open(url, data=secret.encode())\n"
        ), monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _flows(verdict) == {
            (frozenset({"os.environ"}),
             frozenset({"urllib.request.OpenerDirector.open"})),
        }

    def test_control_a_secret_a_helper_returns_still_reaches_the_opener(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """INV-komoj's return lift still lifts a helper that returns a real
        secret: only the handle constructor's read is gone, not the lift."""
        verdict = _verify(tmp_path, (
            "import os\nimport urllib.request\n\n\ndef token():\n"
            "    return os.environ['API_KEY']\n\n\ndef leak(url):\n"
            "    opener = urllib.request.build_opener()\n"
            "    opener.open(url, data=token().encode())\n"
        ), monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _flows(verdict) == {
            (frozenset({"os.environ"}),
             frozenset({"urllib.request.OpenerDirector.open"})),
        }

    def test_control_the_proxy_mapping_read_as_data_still_reaches_the_send(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WI-bakik's real finding: ``getproxies()`` RETURNS the mapping, so
        sending it is a host_secret -> network flow and stays one."""
        verdict = _verify(tmp_path, (
            "import json\nimport urllib.request\n\n\ndef leak(url):\n"
            "    proxies = urllib.request.getproxies()\n"
            "    urllib.request.urlopen(url, json.dumps(proxies).encode())\n"
        ), monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert (frozenset({"urllib.request.getproxies"}),
                frozenset({"urllib.request.urlopen"})) in _flows(verdict)

    def test_control_the_env_read_is_still_classified(self, tmp_path: Path) -> None:
        """``io-boundaries`` still sees the read at ``build_opener``."""
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text(_OPENER_CLEAN)
        raw = [e.to_dict() for e in analyze_python(repo).edges]
        edges = _rehydrate_io_boundary_edges(raw)
        tag_io_boundaries(edges, {"python": load_catalog("python")})
        tagged = {e.dst.split(":")[-2]: (e.meta or {}).get("io_boundary")
                  for e in edges if e.edge_type == "calls"}
        assert tagged.get("build_opener") == "env_read", tagged
