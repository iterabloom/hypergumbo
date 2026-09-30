# SPDX-License-Identifier: AGPL-3.0-or-later
"""``System.out`` / ``System.err`` calls are PrintStream calls on a standard stream (WI-dorus).

``java.lang.System.out`` and ``.err`` are declared ``java.io.PrintStream``, so
the field fixes the receiver's type. The analyzer emitted
``java:external:0-0:println`` with ``call_construct=method`` and took the FIELD
NAME (``out``) as the receiver, so:

- every such call counted as an UNTYPED-receiver site in the
  ``unknown_receiver_scope`` caveat (census on 09-26: cassandra 383, jenkins
  125, sherpa-onnx 186), and the caveat decorated clean verdicts with
  ``println``;
- a local variable named ``out`` lent ``System.out`` its own type.

Now the receiver is typed ``java.io.PrintStream`` and the call is stamped
``io_target_kind: std_stream``; java.yaml's PrintStream rows apply ONLY with
that stamp (``requires_target_kind``). A PrintStream from anywhere else -- a
buffer, a file, a parameter -- is left exactly as before: typed, unclassified.
With INV-hopib's subsumption, ``System.out.println(k)`` is one finding, named by
the call.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.java import analyze_java

_CLAIMS = (
    "claims:\n"
    "  - id: HS-LOG\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: logging\n"
    "  - id: HS-IPC\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: ipc\n"
)


def _java(tmp_path: Path, body: str, imports: str = "") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.java").write_text(
        f"{imports}import java.io.PrintStream;\n"
        f"public class Main {{\n    public static void run(PrintStream ps) {{\n"
        f'        String k = System.getenv("API_KEY");\n{body}    }}\n}}\n'
    )
    return repo


def _call(repo: Path, name: str):
    (edge,) = [e for e in analyze_java(repo).edges
               if e.edge_type == "calls" and e.dst.split(":")[-2] == name]
    return edge


def _verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> dict:
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


class TestTheEdge:
    @pytest.mark.parametrize("spelling", ["System.out", "System.err", "java.lang.System.out"])
    def test_the_receiver_is_a_standard_stream_printstream(
        self, tmp_path: Path, spelling: str,
    ) -> None:
        edge = _call(_java(tmp_path, f"        {spelling}.println(k);\n"), "println")
        assert edge.dst.split(":")[1] == "java.io.PrintStream"
        assert (edge.meta or {}).get("call_construct") == "method"
        assert (edge.meta or {}).get("io_target_kind") == "std_stream"

    def test_a_local_named_out_does_not_lend_its_type(self, tmp_path: Path) -> None:
        edge = _call(_java(tmp_path, '        StringBuilder out = new StringBuilder();\n'
                                     "        System.out.println(k);\n"), "println")
        assert edge.dst.split(":")[1] == "java.io.PrintStream"

    def test_another_printstream_is_not_stamped(self, tmp_path: Path) -> None:
        """THE CONTROL: a parameter's origin is not visible, so no stamp."""
        edge = _call(_java(tmp_path, "        ps.println(k);\n"), "println")
        assert "io_target_kind" not in (edge.meta or {})

    def test_an_imported_system_is_not_the_standard_one(self, tmp_path: Path) -> None:
        edge = _call(_java(tmp_path, "        System.out.println(k);\n",
                           imports="import com.acme.System;\n"), "println")
        assert "io_target_kind" not in (edge.meta or {})


class TestTheVerdict:
    def test_a_standard_stream_write_is_one_logging_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        v = _verdicts(tmp_path, monkeypatch, _java(tmp_path, "        System.out.println(k);\n"))
        assert v["HS-LOG"]["verdict"] == "violated"
        assert [e["sink_primitives"] for e in v["HS-LOG"]["evidence"]] == [
            ["java.io.PrintStream.println"],
        ]

    def test_it_is_no_longer_an_untyped_receiver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        v = _verdicts(tmp_path, monkeypatch, _java(tmp_path, "        System.out.println(k);\n"))
        assert v["HS-IPC"]["verdict"] == "confirmed", v["HS-IPC"]["details"]
        assert not any(c["kind"] == "unknown_receiver_scope" for c in v["HS-IPC"]["caveats"])

    def test_printf_on_err_is_a_logging_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        v = _verdicts(tmp_path, monkeypatch,
                      _java(tmp_path, '        System.err.printf("%s", k);\n'))
        assert v["HS-LOG"]["verdict"] == "violated"
        assert ["java.io.PrintStream.printf"] in [e["sink_primitives"] for e in v["HS-LOG"]["evidence"]]

    def test_another_printstream_is_not_a_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE CONTROL: no abstention -- a parameter PrintStream stays a gap."""
        v = _verdicts(tmp_path, monkeypatch, _java(tmp_path, "        ps.println(k);\n"))
        sinks = {s for e in v["HS-LOG"]["evidence"] for s in e["sink_primitives"]}
        assert not any(s.startswith("java.io.PrintStream") for s in sinks)
        assert v["HS-LOG"]["verdict"] != "violated"


class TestTheBoundaryMap:
    """``io-boundaries`` gives the same answers -- it narrows the rows itself,
    where the verdict relies on taint's own gate (``_target_kind_admits``)."""

    def _chains(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> set:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            main(["io-boundaries", str(_java(tmp_path, body)), "--format", "json"])
        return {(b, c["primitive"]) for b, v in json.loads(buf.getvalue())["boundaries"].items()
                for c in v["chains"] if c["primitive"].startswith("java.io.PrintStream")}

    def test_a_standard_stream_write_is_a_logging_crossing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert self._chains(tmp_path, monkeypatch, "        System.out.println(k);\n") == {
            ("logging", "java.io.PrintStream.println"),
        }

    def test_another_printstream_is_no_crossing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE CONTROL, on the boundary map's own narrowing: the call stays what
        it was before the rows existed -- an unclassified external call,
        disclosed as ``external_potential`` -- and is not a logging crossing."""
        assert self._chains(tmp_path, monkeypatch, "        ps.println(k);\n") == {
            ("external_potential", "java.io.PrintStream.println"),
        }
