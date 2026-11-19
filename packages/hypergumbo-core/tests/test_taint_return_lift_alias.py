# SPDX-License-Identifier: AGPL-3.0-or-later
"""A value stored through one name and returned through another is not lost (WI-zaboh).

THE DEFECT. INV-komoj's return lift asks the ADR-0017 §3a walk whether a
helper's return statement carries a source's value, and lifts only on
``True``. The walk follows names, not objects, so

    def load():
        s = [""]; t = s; t[0] = os.getenv("API_KEY"); return s

exhausts along ``t`` and never reaches ``return s``. The caller was never
seeded, no flow was ever formed, and ``open(...).write(load()[0])`` read
``confirmed`` with nothing in the output saying a flow was dropped. WI-buzup's
exposed-definition rule withholds a REFUTATION that rests on such a
definition, but the lift does not refute anything: a non-``True`` answer there
silently meant "not returned".

THE RULE (option (c) of the item, chosen on the corpus measurement). When the
walk to the return relied on an exposed definition and did not reach a
return, the lift is TENTATIVE: callers are seeded, and every flow descending
from a tentative entry is WITHHELD (``withheld_flows``), never reported, so a
confirming verdict on that (label, sink zone) reads ``inconclusive`` and a
violated one is unchanged. Option (a) (report them) would assert a leak the
analysis did not find; option (b) (withhold every verdict on the label) does
not use the sink the descendant flows reach.

THE SIBLING (WI-bamuj shape 3). ``a.key = k; return a, k`` reaches the return
through ``k``, and the lift recorded only ``k``'s result position, so
``mApp, _ := NewApp()`` seeded the blank and the caller's walk removed the
flow. The positions now include every one that names a variable the value was
written INTO (an exposed definition on its route), not every heir: ``v, err :=
f(secret)`` makes ``err`` an heir, and seeding it is the error-chain climb
WI-kikar keeps the lift off.

THE THIRD READER. ``find_unfollowed_escapes`` asks the same walk whether the
value reaches a C construct taint does not follow, and also counted only
``True``. A walk that relied on an exposed definition now records an escape
at every stamped site of the function, naming the variable.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from hypergumbo_core.cfg import (
    EXPOSURE_IN_PLACE_STORE,
    DdgEdge,
    DefinitionExposure,
)
from hypergumbo_core.cli import main
from hypergumbo_core.taint import (
    TaintSanitizer,
    TaintSink,
    TaintSource,
    TentativeLift,
    UnfollowedEscape,
    WithheldFlow,
    WithheldSite,
    _alias_group,
    _ddg_taint_reaches,
    _lift_returned_sources,
    find_unfollowed_escapes,
    propagate_taint_ddg,
)
from hypergumbo_core.verify_claims import (
    ClaimVerdict,
    _withhold_for_unfollowed_escapes,
    _withhold_for_unmodeled_alias,
)

_CLAIMS = '''claims:
  - id: SECRET-NO-FS
    text: No environment value is written to a file.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
'''

_GO_MOD = "module example.com/za\n\ngo 1.21\n"


def _verdict(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    """Run the shipped CLI and return the SECRET-NO-FS verdict."""
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
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


_PY_CALLER = (
    "\n\ndef main():\n    v = load()\n"
    "    with open(\"/tmp/out.txt\", \"w\") as fh:\n        fh.write(v[0])\n"
)

#: The filed Python shape and its variants: the store goes through ``t``, the
#: return names ``s``.
_PY_ALIAS = {
    "filed": (
        "import os\n\n\ndef load():\n    s = [\"\"]\n    t = s\n"
        "    t[0] = os.getenv(\"API_KEY\")\n    print(len(t))\n    return s\n"
    ),
    # No later use of ``t``: the DDG holds no edge for the store at all.
    "store_unused_after": (
        "import os\n\n\ndef load():\n    s = [\"\"]\n    t = s\n"
        "    t[0] = os.getenv(\"API_KEY\")\n    return s\n"
    ),
}

_GO_CALLER = (
    "\nfunc main() {\n\tv := load()\n"
    "\tos.WriteFile(\"/tmp/out.txt\", []byte(v[0]), 0o600)\n}\n"
)

_GO_ALIAS = {
    "filed": (
        "package main\n\nimport (\n\t\"fmt\"\n\t\"os\"\n)\n\n"
        "func load() []string {\n\ts := []string{\"\"}\n\tt := s\n"
        "\tt[0] = os.Getenv(\"API_KEY\")\n\tfmt.Println(len(t))\n\treturn s\n}\n"
    ),
    "store_unused_after": (
        "package main\n\nimport \"os\"\n\n"
        "func load() []string {\n\ts := []string{\"\"}\n\tt := s\n"
        "\tt[0] = os.Getenv(\"API_KEY\")\n\treturn s\n}\n"
    ),
}


class TestAStoreThroughAnAliasIsNotAClearAnswer:
    """The filed fixtures: ``confirmed`` (exit 0) before, ``inconclusive`` now,
    with the withheld flow counted and the helper named."""

    @pytest.mark.parametrize("shape", sorted(_PY_ALIAS))
    def test_python(self, tmp_path: Path, shape: str) -> None:
        verdict = _verdict(tmp_path, {"app.py": _PY_ALIAS[shape] + _PY_CALLER})
        assert verdict["verdict"] == "inconclusive", verdict["details"]
        assert verdict["withheld_flows"] == 1
        assert "stored into `t` at app.py:7" in verdict["details"]
        assert "load returns it only if another name holds it" in verdict["details"]

    @pytest.mark.parametrize("shape", sorted(_GO_ALIAS))
    def test_go(self, tmp_path: Path, shape: str) -> None:
        verdict = _verdict(tmp_path, {
            "go.mod": _GO_MOD, "main.go": _GO_ALIAS[shape] + _GO_CALLER})
        assert verdict["verdict"] == "inconclusive", verdict["details"]
        assert verdict["withheld_flows"] == 1
        assert "load returns it only if another name holds it" in verdict["details"]

    def test_python_control_storing_through_the_returned_name(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"app.py": (
            "import os\n\n\ndef load():\n    s = [\"\"]\n"
            "    s[0] = os.getenv(\"API_KEY\")\n    print(len(s))\n    return s\n"
        ) + _PY_CALLER})
        assert verdict["verdict"] == "violated", verdict["details"]

    def test_go_control_storing_through_the_returned_name(
        self, tmp_path: Path,
    ) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": (
            "package main\n\nimport \"os\"\n\n"
            "func load() []string {\n\ts := []string{\"\"}\n"
            "\ts[0] = os.Getenv(\"API_KEY\")\n\treturn s\n}\n"
        ) + _GO_CALLER})
        assert verdict["verdict"] == "violated", verdict["details"]

    def test_a_helper_returning_a_literal_returns_no_alias(
        self, tmp_path: Path,
    ) -> None:
        """A result that names nothing cannot be another name for the store,
        so nothing is lifted and the caller's write reads clean."""
        verdict = _verdict(tmp_path, {"app.py": (
            "import os\n\nBOX = [\"\"]\n\n\ndef load():\n    t = BOX\n"
            "    t[0] = os.getenv(\"API_KEY\")\n    return 0\n\n\n"
            "def main():\n    v = load()\n"
            "    with open(\"/tmp/out.txt\", \"w\") as fh:\n        fh.write(str(v))\n"
        )})
        assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), (
            verdict["details"])
        assert verdict["withheld_flows"] == 0


_GO_SLOT = (
    "package main\n\nimport (\n\t\"fmt\"\n\t\"os\"\n)\n\n"
    "type App struct{ key string }\n\n"
    "func NewApp() (*App, string) {\n\tk := os.Getenv(\"API_KEY\")\n"
    "\ta := &App{}\n\ta.key = k\n\treturn a, k\n}\n\n"
    "func main() {\n\t{RECEIVE}\n\tfmt.Println(mApp != nil)\n"
    "\tos.WriteFile(\"/tmp/out.txt\", []byte({WRITE}), 0o600)\n}\n"
)


class TestTheValueComesBackInEveryPositionItWasStoredInto:
    """WI-bamuj shape 3: ``a.key = k; return a, k``."""

    def test_the_struct_it_was_stored_into(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": _GO_SLOT.replace(
            "{RECEIVE}", "mApp, _ := NewApp()").replace("{WRITE}", "mApp.key")})
        assert verdict["verdict"] == "violated", verdict["details"]

    def test_control_the_value_itself(self, tmp_path: Path) -> None:
        verdict = _verdict(tmp_path, {"go.mod": _GO_MOD, "main.go": _GO_SLOT.replace(
            "{RECEIVE}", "mApp, k := NewApp()").replace("{WRITE}", "k")})
        assert verdict["verdict"] == "violated", verdict["details"]


# ---------------------------------------------------------------------------
# The walk: ``relied_on``
# ---------------------------------------------------------------------------

_F = "f"


class TestTheWalkSaysWhatItReliedOn:
    def test_a_visited_store_is_reported_whatever_the_walk_returns(self) -> None:
        """``t`` stored at 11, used at 12 by a call nothing is known about:
        the walk escapes, and the store is still on the route."""
        relied: list[WithheldSite] = []
        result = _ddg_taint_reaches(
            _F, [11], [13], {(_F, "t", 11): {12}}, {(_F, 12): {"mystery"}},
            defs_at={(_F, 11): {"t"}},
            exposure=DefinitionExposure(stores=frozenset({("t", 11)})),
            relied_on=relied,
        )
        assert result is None
        assert relied == [WithheldSite("t", 11, EXPOSURE_IN_PLACE_STORE)]

    def test_a_store_the_ddg_holds_no_edge_for_is_on_the_route(self) -> None:
        """``t[0] = getenv(..)`` with no later use of ``t``: nothing to seed,
        and the store is still where the value went."""
        relied: list[WithheldSite] = []
        exposure = DefinitionExposure(
            stores=frozenset({("t", 11), ("u", 30)}),
            self_references=frozenset({("t", 11)}),
        )
        assert _ddg_taint_reaches(
            _F, [11], [13], {}, exposure=exposure, relied_on=relied,
        ) is None
        assert relied == [WithheldSite("t", 11, EXPOSURE_IN_PLACE_STORE)]
        restricted: list[WithheldSite] = []
        _ddg_taint_reaches(
            _F, [11], [13], {}, exposure=exposure, relied_on=restricted,
            seed_variables=frozenset({"other"}),
        )
        assert restricted == []

    def test_without_an_exposure_record_nothing_is_reported(self) -> None:
        relied: list[WithheldSite] = []
        _ddg_taint_reaches(_F, [11], [13], {}, relied_on=relied)
        assert relied == []


# ---------------------------------------------------------------------------
# The lift
# ---------------------------------------------------------------------------

_GETENV = "python:os:0-0:getenv:external_symbol"
_ENV = TaintSource(taint_label="host_secret", module="os", name="getenv",
                   kind="function")
_SITE = WithheldSite("t", 3, EXPOSURE_IN_PLACE_STORE)


def _call(src: str, dst: str, line: int, resolved: bool = True) -> dict[str, Any]:
    return {"src": src, "dst": dst, "type": "calls", "line": line,
            "is_resolved": resolved}


def _with_s(_fn: str, names: Any) -> set[str]:
    """An alias group in which ``t`` and ``s`` name one object (``t = s``)."""
    return set(names) | ({"s"} if "t" in names else set())


def _lift(
    edges: list[dict[str, Any]],
    spans: dict[str, list[Any]],
    *,
    reaches: Any = None,
    exposed: Any = None,
    aliases: Any = _with_s,
) -> tuple[list[Any], dict[int, TentativeLift]]:
    tentative: dict[int, TentativeLift] = {}
    entries = _lift_returned_sources(
        [("f", _GETENV, _ENV)], edges, spans, io_names=frozenset({"os.getenv"}),
        sanitizer_lines={},
        reaches_return=reaches or (lambda *a: None),
        exposed_on_route=exposed, tentative=tentative, alias_names=aliases,
    )
    return entries, tentative


def _by_caller(entries: list[Any]) -> dict[tuple[str, str], Any]:
    return {(c, callee): received for c, callee, _s, _ch, received in entries}


class TestATentativeLift:
    _EDGES: ClassVar[Any] = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7),
              _call("h", "g", 12)]

    def test_an_exposed_route_that_reaches_no_return_lifts_tentatively(self) -> None:
        entries, tentative = _lift(
            self._EDGES, {"f": [(5, 5, (frozenset({"s"}),))]},
            exposed=lambda *a: (_SITE,),
        )
        assert ("g", "f") in _by_caller(entries)
        (index,) = [i for i, e in enumerate(entries) if e[:2] == ("g", "f")]
        assert tentative == {index: TentativeLift("f", _SITE)}

    def test_a_lift_from_a_tentative_entry_is_tentative_on_the_first_word(
        self,
    ) -> None:
        def reaches(fn: str, *a: Any) -> tuple[str, int] | None:
            return ("v", 13) if fn == "g" else None

        entries, tentative = _lift(
            self._EDGES,
            {"f": [(5, 5, (frozenset({"s"}),))], "g": [(13, 13, (frozenset({"v"}),))]},
            reaches=reaches, exposed=lambda fn, *a: (_SITE,) if fn == "f" else (),
        )
        (index,) = [i for i, e in enumerate(entries) if e[:2] == ("h", "g")]
        assert tentative[index] == TentativeLift("f", _SITE)

    def test_no_exposed_route_no_lift(self) -> None:
        entries, tentative = _lift(
            self._EDGES, {"f": [(5, 5, (frozenset({"s"}),))]},
            exposed=lambda *a: (),
        )
        assert [e[:2] for e in entries] == [("f", _GETENV)]
        assert tentative == {}

    @pytest.mark.parametrize("results", [
        (frozenset(),), (frozenset({"err"}),), (frozenset({"u"}), frozenset({"err"})),
    ], ids=["names_nothing", "names_another_variable", "multi_value_without_it"])
    def test_a_return_naming_no_alias_is_not_lifted(
        self, results: tuple[frozenset[str], ...],
    ) -> None:
        entries, tentative = _lift(
            self._EDGES, {"f": [(5, 5, results)]}, exposed=lambda *a: (_SITE,),
        )
        assert [e[:2] for e in entries] == [("f", _GETENV)]
        assert tentative == {}

    @pytest.mark.parametrize("results, received", [
        ((frozenset({"t"}), frozenset({"err"})), (2, frozenset({0}))),
        ((frozenset({"err"}), frozenset({"s", "x"})), (2, frozenset({1}))),
    ], ids=["the_stored_into_variable", "a_name_in_its_group"])
    def test_the_positions_of_a_multi_value_return(
        self, results: tuple[frozenset[str], ...], received: Any,
    ) -> None:
        entries, _ = _lift(
            self._EDGES, {"f": [(5, 5, results), (9, 9, (frozenset(), frozenset()))]},
            exposed=lambda *a: (_SITE,),
        )
        assert _by_caller(entries)[("g", "f")] == received

    def test_without_an_alias_reader_only_the_variable_itself(self) -> None:
        entries, _ = _lift(
            self._EDGES, {"f": [(5, 5, (frozenset({"s"}),))]},
            exposed=lambda *a: (_SITE,), aliases=None,
        )
        assert [e[:2] for e in entries] == [("f", _GETENV)]

    def test_returns_of_different_arity_record_no_position(self) -> None:
        entries, _ = _lift(
            self._EDGES,
            {"f": [(5, 5, (frozenset({"s"}), frozenset())),
                   (6, 6, (frozenset({"s"}),))]},
            exposed=lambda *a: (_SITE,),
        )
        assert _by_caller(entries)[("g", "f")] is None

    def test_without_the_route_question_nothing_is_tentative(self) -> None:
        entries = _lift_returned_sources(
            [("f", _GETENV, _ENV)], self._EDGES,
            {"f": [(5, 5, (frozenset({"s"}),))]},
            io_names=frozenset({"os.getenv"}), sanitizer_lines={},
            reaches_return=lambda *a: None,
        )
        assert [e[:2] for e in entries] == [("f", _GETENV)]


    def test_a_definite_route_found_after_a_tentative_one_still_lifts(
        self,
    ) -> None:
        """``f`` reads two sources; the first returns only through an alias,
        the second plainly. ``g`` gets both entries."""
        other = "python:os:0-0:environ_get:external_symbol"
        edges = [_call("f", _GETENV, 2, resolved=False),
                 _call("f", other, 4, resolved=False), _call("g", "f", 7)]
        tentative: dict[int, TentativeLift] = {}
        entries = _lift_returned_sources(
            [("f", _GETENV, _ENV), ("f", other, _ENV)], edges,
            {"f": [(5, 5, (frozenset({"s"}),))]},
            io_names=frozenset({"os.getenv"}), sanitizer_lines={},
            reaches_return=lambda fn, lines, *a: ("s", 5) if lines == [4] else None,
            exposed_on_route=lambda *a: (_SITE,), tentative=tentative,
            alias_names=_with_s,
        )
        lifted = [i for i, e in enumerate(entries) if e[:2] == ("g", "f")]
        assert len(lifted) == 2
        assert [i in tentative for i in lifted] == [True, False]



class TestTheAliasGroup:
    """``_alias_group``: names joined by a call-free statement."""

    _ROWS: ClassVar[Any] = [
        (1, ("v",), ("s",)),        # v = s          only a second pass joins v
        (2, ("s",), ()),            # s = [""]       defines only
        (3, ("t",), ("s",)),        # t = s          joins t and s
        (4, ("u",), ("t",)),        # u = t          joins u (forward)
        (5, ("w",), ("s",)),        # w = wrap(s)    a call: not joined
        (6, ("x",), ("y",)),        # x = y          unrelated
    ]

    def test_copies_join_and_calls_do_not(self) -> None:
        assert _alias_group({"t"}, self._ROWS, lambda line: line == 5) == {
            "s", "t", "u", "v"}

    def test_a_name_with_no_copy_is_alone(self) -> None:
        assert _alias_group({"q"}, self._ROWS, lambda line: False) == {"q"}



class TestTheStoredIntoPosition:
    """Shape 3 at unit level: the walk reached ``return a, k`` through ``k``."""

    _EDGES: ClassVar[Any] = [_call("f", _GETENV, 2, resolved=False), _call("g", "f", 7)]
    _SPANS: ClassVar[Any] = {"f": [(5, 5, (frozenset({"a"}), frozenset({"k"})))]}

    def test_the_position_of_a_name_the_value_was_stored_into(self) -> None:
        entries, tentative = _lift(
            self._EDGES, self._SPANS, reaches=lambda *a: ("k", 5),
            exposed=lambda *a: (WithheldSite("a", 4, EXPOSURE_IN_PLACE_STORE),),
        )
        assert _by_caller(entries)[("g", "f")] == (2, frozenset({0, 1}))
        assert tentative == {}

    def test_without_one_only_the_reached_position(self) -> None:
        entries, _ = _lift(
            self._EDGES, self._SPANS, reaches=lambda *a: ("k", 5),
            exposed=lambda *a: (),
        )
        assert _by_caller(entries)[("g", "f")] == (2, frozenset({1}))


class TestTheDescendantFlows:
    """``propagate_taint_ddg``: a tentative entry's flows are withheld.

    ``load`` (1-4): ``t = s`` at 2, ``t[0] = decrypt(x)`` at 3, ``return s``
    at 4. ``main`` (7-10): ``v = load()`` at 8, ``w = encrypt(v)`` at 9,
    ``send(w)`` at 10 -- or ``send(v)`` without the sanitizer.
    """

    _LOAD = "python:a.py:1-4:load:function"
    _MAIN = "python:a.py:7-10:main:function"

    def _run(self, sanitized: bool) -> tuple[list[Any], list[WithheldFlow]]:
        def ext(src: str, name: str, line: int) -> dict[str, Any]:
            return {"src": src, "dst": f"python:external:0-0:{name}:unresolved",
                    "is_resolved": False, "type": "calls", "line": line}

        def ddg(fn: str, *edges: tuple[str, int, int]) -> list[DdgEdge]:
            return [DdgEdge(variable=v, def_block="bb_0", def_line=d,
                            use_block="bb_0", use_line=u, symbol_id=fn)
                    for v, d, u in edges]

        sink_arg = "w" if sanitized else "v"
        main_edges = ddg(self._MAIN, ("v", 8, 9), ("w", 9, 10)) if sanitized else ddg(
            self._MAIN, ("v", 8, 10))
        calls = [ext(self._LOAD, "decrypt", 3), _call(self._MAIN, self._LOAD, 8),
                 ext(self._MAIN, "send", 10)]
        if sanitized:
            calls.append(ext(self._MAIN, "crypto.encrypt", 9))
        withheld: list[WithheldFlow] = []
        findings = propagate_taint_ddg(
            ddg(self._LOAD, ("s", 1, 2)) + main_edges, calls,
            [TaintSource(taint_label="plaintext", module="crypto", name="decrypt",
                         kind="function")],
            [TaintSink(zone="relay", trust_level="untrusted", module="net",
                       name="send", kind="function")],
            [TaintSanitizer(input_taint="plaintext", output_taint="ciphertext",
                            qualified_name="crypto.encrypt")],
            ddg_symbols={self._LOAD, self._MAIN},
            stmt_defuse={
                self._LOAD: [(1, ("s",), ()), (2, ("t",), ("s",)), (3, ("t",), ("t",))],
                self._MAIN: [(8, ("v",), ()), (10, (), (sink_arg,))]
                + ([(9, ("w",), ("v",))] if sanitized else []),
            },
            exposure={self._LOAD: DefinitionExposure(stores=frozenset({("t", 3)}))},
            return_spans={self._LOAD: [(4, 4, (frozenset({"s"}),))]},
            withheld_flows=withheld,
        )
        return findings, withheld

    def test_an_unsanitized_descendant_is_withheld_naming_the_helper(self) -> None:
        findings, withheld = self._run(sanitized=False)
        assert findings == []
        assert [(f.function, f.returned, f.site) for f in withheld] == [
            (self._LOAD, True, WithheldSite("t", 3, EXPOSURE_IN_PLACE_STORE))]

    def test_a_sanitized_descendant_violates_nothing_and_withholds_nothing(
        self,
    ) -> None:
        findings, withheld = self._run(sanitized=True)
        assert findings == [] and withheld == []


# ---------------------------------------------------------------------------
# The unfollowed-escape reader
# ---------------------------------------------------------------------------

_CFN = "c:src/init.c:1-9:init:function"
_CGETENV = "c:stdlib.h:0-0:getenv:unresolved"
_SET = "c:src/conf.c:3-6:set_pass:function"
_CSOURCES = [TaintSource(
    taint_label="host_secret", module="stdlib", name="getenv", kind="function",
)]


def _ccall(dst: str, line: int, construct: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"src": _CFN, "dst": dst, "is_resolved": False,
                            "type": "calls", "line": line}
    if construct is not None:
        edge["meta"] = {"unfollowed_construct": construct}
    return edge


class TestAnEscapeThroughAnAlias:
    """``t = box; t[0] = getenv(..)`` at 3, ``set_pass(box[0], &g)`` at 4,
    ``log_print(..)`` at 6: the walk seeds nothing it can follow to either."""

    _EDGES: ClassVar[Any] = [_ccall(_CGETENV, 3), _ccall(_SET, 4, "out_parameter_call"),
              _ccall(_SET, 6, "variadic_call")]
    #: ``t = box`` at 2; ``set_pass(box[0], &g)`` at 4 uses box; the
    #: variadic call at 6 uses only ``other``.
    _ROWS: ClassVar[Any] = {_CFN: [(2, ("t",), ("box",)), (4, (), ("box", "g")), (6, (), ("other",))]}

    def test_a_stamped_site_using_the_alias_group_is_an_escape(self) -> None:
        escapes = find_unfollowed_escapes(
            [DdgEdge(variable="box", def_block="bb_0", def_line=1,
                     use_block="bb_0", use_line=4, symbol_id=_CFN)],
            self._EDGES, _CSOURCES, language="c",
            stmt_defuse=self._ROWS,
            exposure={_CFN: DefinitionExposure(
                stores=frozenset({("t", 3)}), self_references=frozenset({("t", 3)}))},
        )
        assert escapes == [
            UnfollowedEscape("host_secret", _CFN, "stdlib.getenv", 4,
                             "out_parameter_call", alias="t"),
        ]

    def test_without_an_exposed_definition_there_is_none(self) -> None:
        assert find_unfollowed_escapes(
            [], self._EDGES, _CSOURCES, language="c",
            exposure={_CFN: DefinitionExposure(stores=frozenset({("t", 9)}))},
        ) == []
        assert find_unfollowed_escapes([], self._EDGES, _CSOURCES, language="c") == []
        assert find_unfollowed_escapes(
            [], self._EDGES, _CSOURCES, language="c",
            exposure={_CFN: DefinitionExposure(stores=frozenset({("t", 3)}))},
        ) == [], "no statement record: no site is known to use the group"


# ---------------------------------------------------------------------------
# What the verdict says
# ---------------------------------------------------------------------------


def _clean() -> ClaimVerdict:
    return ClaimVerdict(claim_id="C", claim_text="t", verdict="confirmed",
                        details="clean.")


class TestTheVerdictNamesTheAlias:
    def test_a_tentative_flow_names_the_helper(self) -> None:
        flow = WithheldFlow(
            taint_label="host_secret", sink_zone="host_fs",
            source_symbol="python:app.py:10-13:main:function",
            function="python:app.py:4-9:load:function", source="os.getenv",
            sink="builtins.open", site=WithheldSite("t", 7, EXPOSURE_IN_PLACE_STORE),
            returned=True,
        )
        out = _withhold_for_unmodeled_alias(_clean(), "host_secret", "host_fs", [flow], False)
        assert out.verdict == "inconclusive"
        assert ("stored into `t` at app.py:7, so load returns it only if another "
                "name holds it") in out.details
        plain = _withhold_for_unmodeled_alias(
            _clean(), "host_secret", "host_fs", [flow._replace(returned=False)], False)
        assert "returns it only if" not in plain.details

    def test_an_escape_through_an_alias_names_the_variable(self) -> None:
        escape = UnfollowedEscape("host_secret", _CFN, "stdlib.getenv", 4,
                                  "out_parameter_call", alias="t")
        out = _withhold_for_unfollowed_escapes(_clean(), "host_secret", [escape], False)
        assert out.verdict == "inconclusive"
        assert ("out_parameter_call at src/init.c:4 (stdlib.getenv, if a name the "
                "walk does not follow holds `t`)") in out.details
