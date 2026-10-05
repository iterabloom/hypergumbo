# SPDX-License-Identifier: AGPL-3.0-or-later
"""A source value a helper RETURNS is followed into its caller (INV-komoj).

THE DEFECT. Taint crossed a call boundary in one direction only. Both
propagators seed a call-graph BFS at the function that reads a source and test
whether a sink caller is reachable FORWARD from it, so a sink in a CALLER of
that function was never in the set. ``def get(): return os.getenv("K")`` with
``send(get())`` in the caller read ``confirmed_with_caveats`` -- a false
all-clear -- while the same flow written inline read ``violated``.

THE FIX REUSES THE MACHINERY. A function whose return statement carries a
source's value is lifted into a SOURCE CALL SITE in each of its callers -- the
caller's call to the helper becomes one more ``(caller, callee, source)`` entry
-- and from there the existing forward BFS, the ADR-0017 §3a walk in the
caller, the INV-karud collapse and the sanitizer barrier decide the flow
exactly as they decide a direct read. "Does the return carry the value" is
asked of the same walk (return statements as its targets), so the only new
machinery is the lift itself.

THE TESTS ARE PAIRS. A helper shape must read what the same flow written
inline reads, and the controls must stay clean -- a value the helper reads and
does NOT return, a return inside a nested callable, a return through an I/O
primitive (INV-fumod's rule: the far side of a boundary is not the argument).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.taint import (
    TaintFlowFinding,
    TaintSource,
    _adjacency_past_the_returning_call,
    _lift_returned_sources,
    _received_seeds,
    collapse_unadjudicated_flows,
)

_CLAIMS = '''claims:
  - id: SECRET-NO-SUBPROCESS
    text: No environment value reaches a subprocess.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''

_GO_MOD = "module example.com/rf\n\ngo 1.21\n"


def _verdict(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    """Run the shipped CLI and return the SECRET-NO-SUBPROCESS verdict."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    return verdicts["SECRET-NO-SUBPROCESS"]


def _returned_by(verdict: dict[str, Any]) -> set[str]:
    return {fn.split(":")[-2] for row in verdict.get("evidence", [])
            for fn in row.get("source_returned_by", [])}


def _methods(verdict: dict[str, Any]) -> set[str]:
    return {row.get("analysis_method") for row in verdict.get("evidence", [])}


_PY_HEAD = "import os\nimport subprocess\n\n\n"

#: (helper shape, inline twin). The inline twin is what the flow reads when no
#: call boundary is in the way; the helper shape must read the same.
_PY_PAIRS = {
    "returned_into_the_sink_argument": (
        _PY_HEAD + "def get_tool():\n    return os.getenv(\"TOOL\")\n\n\n"
        "def main():\n    subprocess.run([get_tool(), \"--flag\"])\n",
        _PY_HEAD + "def main():\n    subprocess.run([os.getenv(\"TOOL\"), \"--flag\"])\n",
    ),
    "returned_into_a_local": (
        _PY_HEAD + "def get_tool():\n    return os.environ[\"TOOL\"]\n\n\n"
        "def main():\n    tool = get_tool()\n    subprocess.run([tool, \"--flag\"])\n",
        _PY_HEAD + "def main():\n    tool = os.environ[\"TOOL\"]\n"
        "    subprocess.run([tool, \"--flag\"])\n",
    ),
    "discarded_by_the_caller": (
        _PY_HEAD + "def get_tool():\n    return os.getenv(\"TOOL\")\n\n\n"
        "def main():\n    get_tool()\n    subprocess.run([\"ls\", \"--flag\"])\n",
        _PY_HEAD + "def main():\n    os.getenv(\"TOOL\")\n"
        "    subprocess.run([\"ls\", \"--flag\"])\n",
    ),
}

_GO_HEAD = 'package main\n\nimport (\n\t"os"\n\t"os/exec"\n)\n\n'

_GO_PAIRS = {
    "returned_into_the_sink_argument": (
        _GO_HEAD + 'func get() string {\n\treturn os.Getenv("TOOL")\n}\n\n'
        "func main() {\n\texec.Command(get()).Run()\n}\n",
        _GO_HEAD + 'func main() {\n\texec.Command(os.Getenv("TOOL")).Run()\n}\n',
    ),
    "returned_into_a_local": (
        _GO_HEAD + 'func get() string {\n\treturn os.Getenv("TOOL")\n}\n\n'
        "func main() {\n\tname := get()\n\texec.Command(name).Run()\n}\n",
        _GO_HEAD + 'func main() {\n\tname := os.Getenv("TOOL")\n'
        "\texec.Command(name).Run()\n}\n",
    ),
}


class TestAHelperReadsWhatTheInlineFlowReads:
    """The filed instances, python and go, against their inline twins."""

    @pytest.mark.parametrize("shape", sorted(_PY_PAIRS))
    def test_python(self, tmp_path: Path, shape: str) -> None:
        helper, inline = _PY_PAIRS[shape]
        via_helper = _verdict(tmp_path / "h", {"app.py": helper})
        written_inline = _verdict(tmp_path / "i", {"app.py": inline})
        assert written_inline["verdict"] == "violated"  # the reference reads
        assert via_helper["verdict"] == written_inline["verdict"], (
            via_helper["details"])
        assert _returned_by(via_helper) == {"get_tool"}

    @pytest.mark.parametrize("shape", sorted(_GO_PAIRS))
    def test_go(self, tmp_path: Path, shape: str) -> None:
        helper, inline = _GO_PAIRS[shape]
        via_helper = _verdict(
            tmp_path / "h", {"go.mod": _GO_MOD, "main.go": helper})
        written_inline = _verdict(
            tmp_path / "i", {"go.mod": _GO_MOD, "main.go": inline})
        assert written_inline["verdict"] == "violated"
        assert via_helper["verdict"] == written_inline["verdict"], (
            via_helper["details"])
        assert _returned_by(via_helper) == {"get"}


class TestTheCallerIsAdjudicatedByTheSameWalk:
    """A lifted source is a source call site in the caller, so the §3a walk
    runs THERE -- it is not a call-reachability-only finding."""

    def test_python_local_in_the_caller_is_ddg_confirmed(
        self, tmp_path: Path,
    ) -> None:
        helper, _ = _PY_PAIRS["returned_into_a_local"]
        verdict = _verdict(tmp_path, {"app.py": helper})
        assert "ddg" in _methods(verdict), verdict["evidence"]

    def test_go_local_in_the_caller_is_ddg_confirmed(
        self, tmp_path: Path,
    ) -> None:
        helper, _ = _GO_PAIRS["returned_into_a_local"]
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": helper})
        assert "ddg" in _methods(verdict), verdict["evidence"]


class TestTheHelperReturnsTheValueThroughItsOwnDataflow:
    """The return carries a value DERIVED from the source, which only the walk
    over the helper can establish -- the source call is not on the return line."""

    def test_python_derived_local(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def get_tool():\n    value = os.getenv(\"TOOL\")\n"
            "    cleaned = value.strip()\n    return cleaned\n\n\n"
            "def main():\n    tool = get_tool()\n"
            "    subprocess.run([tool, \"--flag\"])\n")})
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _returned_by(verdict) == {"get_tool"}

    def test_go_multi_value_return(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": _GO_HEAD + (
            "func get() (string, error) {\n\tv := os.Getenv(\"TOOL\")\n"
            "\treturn v, nil\n}\n\n"
            "func main() {\n\tname, err := get()\n\tif err != nil {\n\t\treturn\n"
            "\t}\n\texec.Command(name).Run()\n}\n")})
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _returned_by(verdict) == {"get"}


class TestAChainOfReturnsIsFollowed:
    """read -> get -> main: the lift repeats from the caller it reached."""

    def test_python(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def read_tool():\n    return os.getenv(\"TOOL\")\n\n\n"
            "def get_tool():\n    return read_tool()\n\n\n"
            "def main():\n    tool = get_tool()\n"
            "    subprocess.run([tool, \"--flag\"])\n")})
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _returned_by(verdict) == {"read_tool", "get_tool"}

    def test_go(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": _GO_HEAD + (
            'func read() string {\n\treturn os.Getenv("TOOL")\n}\n\n'
            "func get() string {\n\treturn read()\n}\n\n"
            "func main() {\n\tname := get()\n\texec.Command(name).Run()\n}\n")})
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _returned_by(verdict) == {"read", "get"}


_GO_ERR_CHAIN = _GO_HEAD + (
    'func get() (string, error) {\n\tv := os.Getenv("TOOL")\n'
    "\treturn v, nil\n}\n\n"
    "func wrap() (string, error) {\n\tvalue, err := get()\n"
    '\tif err != nil {\n\t\treturn "", err\n\t}\n'
    "\t{RETURN}\n}\n\n"
    "func main() {\n\tname, err := wrap()\n\tif err != nil {\n"
    "\t\treturn\n\t}\n\texec.Command(name).Run()\n}\n")


class TestTheValueComesBackInItsOwnResultPosition:
    """``value, err := get()`` receives the value in ``value`` only, so a
    caller that hands back ``err`` does not return it (the error chain the
    lift would otherwise climb), and one that hands back ``value`` does."""

    def test_go_returning_the_error_does_not_return_the_value(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": (
            _GO_ERR_CHAIN.replace("{RETURN}", '_ = value\n\treturn "ls", nil'))})
        assert verdict["verdict"] != "violated", verdict["evidence"]

    def test_go_returning_the_value_does(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": (
            _GO_ERR_CHAIN.replace("{RETURN}", "return value, nil"))})
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _returned_by(verdict) == {"get", "wrap"}

    def test_python_returning_the_other_element_does_not(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def get():\n    v = os.getenv(\"TOOL\")\n    return v, 0\n\n\n"
            "def wrap():\n    value, code = get()\n    print(value)\n"
            "    return code\n\n\n"
            "def main():\n    subprocess.run([\"ls\", str(wrap())])\n")})
        assert verdict["verdict"] != "violated", verdict["evidence"]


class TestControlsStayClean:
    """Each control names a reason the value does NOT come back, and must not
    read violated. Reach first: every one of them reads the source."""

    def test_python_value_read_but_a_constant_returned(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def get_tool():\n    os.getenv(\"TOOL\")\n    return \"ls\"\n\n\n"
            "def main():\n    tool = get_tool()\n"
            "    subprocess.run([tool, \"--flag\"])\n")})
        assert verdict["verdict"] != "violated", verdict["details"]

    def test_python_return_inside_a_nested_function(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def get_tool():\n    def inner():\n"
            "        return os.getenv(\"TOOL\")\n    return \"ls\"\n\n\n"
            "def main():\n    tool = get_tool()\n"
            "    subprocess.run([tool, \"--flag\"])\n")})
        assert verdict["verdict"] != "violated", verdict["details"]

    def test_python_return_through_an_io_primitive(
        self, tmp_path: Path,
    ) -> None:
        # INV-fumod: os.path.exists' result comes from the far side of a
        # filesystem boundary; it is not a computation on the env value.
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def tool_present():\n"
            "    return os.path.exists(os.getenv(\"TOOL\"))\n\n\n"
            "def main():\n    present = tool_present()\n"
            "    subprocess.run([\"ls\", str(present)])\n")})
        assert verdict["verdict"] != "violated", verdict["details"]

    def test_go_return_inside_a_func_literal(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": _GO_HEAD + (
            "func get() string {\n\tf := func() string {\n"
            '\t\treturn os.Getenv("TOOL")\n\t}\n\t_ = f\n\treturn "ls"\n}\n\n'
            "func main() {\n\tname := get()\n\texec.Command(name).Run()\n}\n")})
        assert verdict["verdict"] != "violated", verdict["details"]


class TestTheCallTheValueCameOutOfIsNotARouteForIt:
    """A lifted seed does not re-report the helper's own sinks under the
    caller: the helper's own read already reports them, and the value the
    call returned cannot be that same call's argument (INV-lozat's rule)."""

    def test_the_helpers_own_sink_is_reported_once(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_HEAD + (
            "def get_tool():\n    value = os.getenv(\"TOOL\")\n"
            "    subprocess.run([value])\n    return value\n\n\n"
            "def main():\n    tool = get_tool()\n    print(tool)\n")})
        assert verdict["verdict"] == "violated"  # reach: the helper's own row
        assert _returned_by(verdict) == set(), verdict["evidence"]

    def test_the_edge_goes_only_for_exactly_one_call(self) -> None:
        adj = {"g": {"f", "h"}, "f": {"sink"}}
        one = _adjacency_past_the_returning_call(
            adj, "g", "f", ("f",), {("g", "f"): [7]})
        assert set(one["g"]) == {"h"}
        assert one["f"] == {"sink"}
        assert adj["g"] == {"f", "h"}  # overlaid, not mutated
        two = _adjacency_past_the_returning_call(
            adj, "g", "f", ("f",), {("g", "f"): [7, 9]})
        assert two is adj
        direct = _adjacency_past_the_returning_call(
            adj, "g", "f", (), {("g", "f"): [7]})
        assert direct is adj


# ---------------------------------------------------------------------------
# The lift itself, on synthetic edges.
# ---------------------------------------------------------------------------

_ENV = TaintSource(
    name="getenv", module="os", kind="function", taint_label="host_secret",
)


def _call(src: str, dst: str, line: int, resolved: bool = True) -> dict[str, Any]:
    return {"src": src, "dst": dst, "type": "calls", "line": line,
            "is_resolved": resolved}


_GETENV = "python:os:0-0:getenv:external_symbol"


def _lift(
    edges: list[dict[str, Any]],
    spans: dict[str, list[Any]],
    *,
    callers: list[tuple[str, str, TaintSource]] | None = None,
    reaches: Any = None,
    io_names: frozenset[str] = frozenset({"os.getenv"}),
    sanitizer_lines: dict[tuple[str, str], list[int]] | None = None,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], tuple[str, ...]]]:
    # A bare (start, end) span is a single-valued return of nothing named.
    full = {fn: [s if len(s) == 3 else (s[0], s[1], (frozenset(),))
                 for s in rows] for fn, rows in spans.items()}
    entries = _lift_returned_sources(
        callers if callers is not None else [("f", _GETENV, _ENV)],
        edges, full, io_names=io_names,
        sanitizer_lines=sanitizer_lines or {}, reaches_return=reaches,
    )
    return (
        {(c, callee) for c, callee, _src, _chain, _rcv in entries},
        {(c, callee): chain for c, callee, _src, chain, _rcv in entries if chain},
    )


class TestTheLift:

    def test_a_direct_return_lifts_into_every_caller(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False),
                 _call("g", "f", 7), _call("h", "f", 9)]
        lifted, chains = _lift(edges, {"f": [(2, 2)]})
        assert lifted == {("f", _GETENV), ("g", "f"), ("h", "f")}
        assert chains[("g", "f")] == ("f",)

    def test_the_lift_repeats_and_records_the_chain(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False),
                 _call("g", "f", 7), _call("h", "g", 12)]
        lifted, chains = _lift(edges, {"f": [(2, 2)], "g": [(7, 7)]})
        assert ("h", "g") in lifted
        assert chains[("h", "g")] == ("f", "g")

    def test_recursion_terminates(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("f", "f", 2),
                 _call("g", "f", 7), _call("f", "g", 3)]
        lifted, _ = _lift(edges, {"f": [(2, 3)], "g": [(7, 7)]})
        assert lifted == {("f", _GETENV), ("f", "f"), ("g", "f"), ("f", "g")}

    def test_no_return_span_no_lift(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        lifted, chains = _lift(edges, {})
        assert lifted == {("f", _GETENV)}
        assert chains == {}

    def test_a_source_off_the_return_line_needs_the_walk(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        asked: list[tuple[str, list[int], list[int], str]] = []

        def reaches(fn: str, lines: list[int], targets: list[int],
                    barriers: list[int], label: str,
                    received: Any) -> tuple[str, int] | None:
            asked.append((fn, lines, targets, label))
            return ("v", 4) if fn == "f" else None

        lifted, _ = _lift(edges, {"f": [(4, 4)]}, reaches=reaches)
        assert ("g", "f") in lifted
        assert asked[0] == ("f", [2], [4], "host_secret")

    def test_a_walk_that_does_not_confirm_lifts_nothing(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        lifted, _ = _lift(
            edges, {"f": [(4, 4)]},
            reaches=lambda *a: None,
        )
        assert lifted == {("f", _GETENV)}

    def test_without_a_walk_only_the_return_line_counts(self) -> None:
        # The structural arm: no reaching-def data, so only a source call ON a
        # return statement is known to be returned.
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        lifted, _ = _lift(edges, {"f": [(4, 4)]}, reaches=None)
        assert lifted == {("f", _GETENV)}

    def test_a_return_through_another_io_primitive_is_blocked(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False),
                 _call("f", "python:os.path:0-0:exists:external_symbol", 2,
                       resolved=False),
                 _call("g", "f", 7)]
        lifted, _ = _lift(
            edges, {"f": [(2, 2)]},
            io_names=frozenset({"os.getenv", "os.path.exists"}),
        )
        assert lifted == {("f", _GETENV)}

    def test_the_source_itself_does_not_block_its_own_return(self) -> None:
        # Every source is an I/O primitive (env_read), so exempting it is what
        # lets the direct shape lift at all -- the reach for the test above.
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        lifted, _ = _lift(edges, {"f": [(1, 3)]})
        assert ("g", "f") in lifted

    def test_a_sanitized_return_is_blocked(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        lifted, _ = _lift(
            edges, {"f": [(2, 2)]},
            sanitizer_lines={("f", "host_secret"): [2]},
        )
        assert lifted == {("f", _GETENV)}

    def test_the_walk_is_handed_the_sanitizer_lines_as_barriers(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        seen: list[list[int]] = []

        def reaches(fn: str, lines: list[int], targets: list[int],
                    barriers: list[int], label: str,
                    received: Any) -> tuple[str, int] | None:
            seen.append(barriers)
            return ("v", 5)

        _lift(edges, {"f": [(5, 5)]}, reaches=reaches,
              sanitizer_lines={("f", "host_secret"): [3]})
        assert seen == [[3]]

    def test_a_source_whose_return_is_not_its_value_is_not_lifted(
        self,
    ) -> None:
        # ``return_tainted: false`` -- the primitive's data lands in an
        # argument, so returning the CALL returns a count or a status.
        entry = TaintSource(
            name="read", module="", kind="function",
            taint_label="untrusted_input", return_tainted=False,
            argument_tainted=(1,),
        )
        edges = [_call("f", "c:read", 2, resolved=False), _call("g", "f", 7)]
        lifted, _ = _lift(edges, {"f": [(2, 2)]},
                          callers=[("f", "c:read", entry)])
        assert lifted == {("f", "c:read")}

    @pytest.mark.parametrize("edge", [
        {"type": "dispatches_to"},
        {"type": "calls", "meta": {"protocol": "http"}},
    ], ids=["framework_dispatch", "protocol_call"])
    def test_only_an_invocation_hands_the_return_back(
        self, edge: dict[str, Any],
    ) -> None:
        # A registrant receives nothing from the handler it registers, and a
        # protocol client's read of the response is a source of its own.
        edges = [_call("f", _GETENV, 2, resolved=False),
                 {**_call("g", "f", 7), **edge},
                 _call("h", "f", 9)]
        lifted, _ = _lift(edges, {"f": [(2, 2)]})
        assert ("h", "f") in lifted  # reach: a plain call does lift
        assert ("g", "f") not in lifted

    def test_a_constructor_call_is_an_invocation(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False),
                 {**_call("g", "f", 7), "type": "instantiates"}]
        lifted, _ = _lift(edges, {"f": [(2, 2)]})
        assert ("g", "f") in lifted

    def test_the_result_positions_the_value_occupies_are_recorded(
        self,
    ) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        entries = _lift_returned_sources(
            [("f", _GETENV, _ENV)], edges,
            {"f": [(4, 4, (frozenset({"v"}), frozenset()))]},
            io_names=frozenset(), sanitizer_lines={},
            reaches_return=lambda *a: ("v", 4),
        )
        assert [e[4] for e in entries if e[0] == "g"] == [(2, frozenset({0}))]

    def test_a_tail_call_passes_the_positions_through(self) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7),
                 _call("h", "g", 12)]
        entries = _lift_returned_sources(
            [("f", _GETENV, _ENV)], edges,
            {"f": [(4, 4, (frozenset({"v"}), frozenset()))],
             "g": [(7, 7, (frozenset({"f"}),))]},
            io_names=frozenset(), sanitizer_lines={},
            reaches_return=lambda fn, *a: ("v", 4) if fn == "f" else None,
        )
        assert [e[4] for e in entries if e[0] == "h"] == [(2, frozenset({0}))]

    @pytest.mark.parametrize("results", [
        (frozenset({"v"}),),  # single-valued: the whole result
        (frozenset({"w"}), frozenset()),  # the value is in no position
    ], ids=["single_valued", "unplaced"])
    def test_unreadable_positions_fall_back_to_every_variable(
        self, results: tuple[frozenset[str], ...],
    ) -> None:
        edges = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
        entries = _lift_returned_sources(
            [("f", _GETENV, _ENV)], edges, {"f": [(4, 4, results)]},
            io_names=frozenset(), sanitizer_lines={},
            reaches_return=lambda *a: ("v", 4),
        )
        assert [e[4] for e in entries if e[0] == "g"] == [None]

    def test_received_seeds_pick_the_defines_at_those_positions(self) -> None:
        defines = {("g", 7): [("name", "err")]}
        assert _received_seeds("g", [7], (2, frozenset({0})), defines) == {
            "name"}
        # ``_, err := get()``: the blank is not a define, so the arity does
        # not match and every variable is seeded, as before.
        assert _received_seeds("g", [7], (3, frozenset({0})), defines) is None
        assert _received_seeds("g", [7], None, defines) is None

    def test_a_callee_seeded_source_is_not_a_value(self) -> None:
        entry = TaintSource(
            name="cmd_run", module="", kind="function",
            taint_label="untrusted_input", start_at="callee",
        )
        edges = [_call("f", "python:app.py:1-2:cmd_run:function", 2),
                 _call("g", "f", 7)]
        lifted, _ = _lift(
            edges, {"f": [(2, 2)]},
            callers=[("f", "python:app.py:1-2:cmd_run:function", entry)],
        )
        assert lifted == {("f", "python:app.py:1-2:cmd_run:function")}


class TestTheFindingSaysTheValueWasReturned:

    def _finding(self, symbol: str, returned_by: tuple[str, ...]) -> TaintFlowFinding:
        return TaintFlowFinding(
            taint_label="host_secret", source_symbol=symbol,
            source_primitive="getenv", sink_symbol="run", sink_primitive="run",
            sink_zone="subprocess", sanitized=False, confidence="approximate",
            analysis_method="structural", source_returned_by=returned_by,
        )

    def test_serialized(self) -> None:
        assert self._finding("g", ("f",)).to_dict()["source_returned_by"] == ["f"]

    def test_the_verdict_row_says_where_the_read_is(self) -> None:
        from hypergumbo_core.verify_claims import (
            Claim,
            TaintFlowConstraint,
            verify_taint_claim,
        )

        claim = Claim(
            id="S", text="no secret to a subprocess",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="host_secret", prohibited_sink_zone="subprocess",
            ),
        )
        returned = verify_taint_claim(claim, [self._finding("g", ("f",))])
        assert returned.evidence[0]["source_returned_by"] == ["f"]
        assert "returned to it by 1 function(s)" in returned.details
        direct = verify_taint_claim(claim, [self._finding("g", ())])
        assert "returned to it" not in direct.details

    def test_collapse_unions_it(self) -> None:
        rows = collapse_unadjudicated_flows([
            self._finding("g", ("f",)), self._finding("g", ()),
            self._finding("g", ("e", "f")),
        ])
        assert len(rows) == 1
        assert rows[0].source_returned_by == ("e", "f")



class TestReturnSpansAreTheFunctionsOwn:
    """``RepoDdg.return_spans``: each function's own return statements, with
    their full line extent, and NOT those of a callable nested inside it."""

    @staticmethod
    def _spans(root: Path, language: str) -> dict[str, list[Any]]:
        from hypergumbo_core.dataflow_scope import (
            ensure_def_use_extractors_registered,
        )
        from hypergumbo_core.ddg_build import build_repo_ddg

        ensure_def_use_extractors_registered()
        result = build_repo_ddg(root, (language,))
        return {sym.split(":")[-2]: [tuple(s) for s in spans]
                for sym, spans in result.return_spans.items()}

    def test_python(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text(
            "def outer():\n    def inner():\n        return 1\n"
            "    return (\n        2\n    )\n\n\n"
            "def no_return():\n    x = 1\n    print(x)\n")
        spans = self._spans(tmp_path, "python")
        assert spans["outer"] == [(4, 6, (frozenset(),))]
        assert [(3, 3, (frozenset(),))] in [
            v for k, v in spans.items() if k.endswith("inner")]
        assert not any(k.endswith("no_return") for k in spans)

    def test_go(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text(_GO_MOD)
        (tmp_path / "main.go").write_text(
            "package main\n\nfunc get() string {\n\tf := func() string {\n"
            "\t\treturn \"x\"\n\t}\n\t_ = f\n\treturn \"ls\"\n}\n")
        assert self._spans(tmp_path, "go")["get"] == [(8, 8, (frozenset(),))]

    def test_result_positions_name_their_identifiers(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.py").write_text(
            "def get(v):\n    return v, 1\n\n\n"
            "def get2(v):\n    return (v.strip(), v)\n")
        spans = self._spans(tmp_path, "python")
        assert spans["get"] == [(2, 2, (frozenset({"v"}), frozenset()))]
        assert spans["get2"] == [
            (6, 6, (frozenset({"v", "strip"}), frozenset({"v"})))]
        go = tmp_path / "go"
        go.mkdir()
        (go / "go.mod").write_text(_GO_MOD)
        (go / "main.go").write_text(
            "package main\n\nfunc get(v string) (string, error) {\n"
            "\treturn v, nil\n}\n")
        assert self._spans(go, "go")["get"] == [
            (4, 4, (frozenset({"v"}), frozenset()))]
