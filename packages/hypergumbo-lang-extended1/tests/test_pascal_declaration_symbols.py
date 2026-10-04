# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-darik / WI-sigit, pascal: every declaration that holds calls is a symbol, and
every call edge is drawn from one.

Pass 1 stopped at the first ``defProc`` and read its name from a bare
``identifier``. A method implementation ``procedure TA.Run`` names itself with a
``genericDot`` node, so it got no symbol and every call in its body was lost
(WI-darik). A nested procedure was never reached, while the edge walk minted its
caller id from the NEAREST ``defProc`` by position, so every call inside it
carried a src id no symbol has (WI-sigit). A call in the main program block or a
unit's ``initialization`` / ``finalization`` section had no ``defProc`` at all and
emitted no edge.

Each test asserts REACH (the edge exists, at the line) before CONTAINMENT (its src
span holds the line), and every test checks that no edge's src is unemitted
(``unemitted_edge_sources``), so a dangling src cannot pass as anchored.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_source import unemitted_edge_sources
from hypergumbo_lang_extended1.pascal import analyze_pascal


def _calls(result) -> dict[int, tuple[str, str]]:
    """line -> (src name, dst) for every call edge; asserts no edge src dangles
    and every call edge's src span holds its line."""
    assert unemitted_edge_sources(result.symbols, result.edges) == []
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, tuple[str, str]] = {}
    for e in result.edges:
        if e.edge_type != "calls":
            continue
        src = by_id[e.src]
        assert src.span.start_line <= e.line <= src.span.end_line, (e.line, src.name)
        out[e.line] = (src.name, e.dst)
    return out


def _dst_name(result, dst: str) -> str:
    by_id = {s.id: s for s in result.symbols}
    return by_id[dst].name if dst in by_id else dst


def test_a_method_implementation_is_a_symbol_and_its_calls_are_drawn_from_it(
    tmp_path: Path,
) -> None:
    """WI-darik: ``procedure TA.Run`` / ``function TA.Get`` / ``constructor``,
    ``destructor`` and ``class function`` implementations are methods named
    ``TA.<name>``, and the calls in their bodies are drawn from them."""
    (tmp_path / "a.pas").write_text("""\
program P;
type
  TA = class
    constructor Create;
    destructor Destroy; override;
    class function Make: TA;
    procedure Run;
    function Get(X: Integer): Integer;
  end;

procedure One; begin end;

constructor TA.Create;
begin
  One;
end;

destructor TA.Destroy;
begin
  One();
end;

class function TA.Make: TA;
begin
  One;
end;

procedure TA.Run;
begin
  One();
end;

function TA.Get(X: Integer): Integer;
begin
  One;
  Result := X;
end;

begin
end.
""")
    result = analyze_pascal(tmp_path)
    methods = {s.name: s for s in result.symbols if s.kind == "method"}
    assert set(methods) == {"TA.Create", "TA.Destroy", "TA.Make", "TA.Run", "TA.Get"}
    assert methods["TA.Get"].meta["proc_kind"] == "function"
    assert methods["TA.Get"].meta["param_count"] == 1
    assert methods["TA.Create"].meta["proc_kind"] == "constructor"
    assert methods["TA.Destroy"].meta["proc_kind"] == "destructor"
    assert methods["TA.Get"].signature == "function TA.Get(X): Integer"
    calls = _calls(result)
    assert {line: src for line, (src, _) in calls.items()} == {
        15: "TA.Create", 20: "TA.Destroy", 25: "TA.Make", 30: "TA.Run", 35: "TA.Get",
    }
    assert {_dst_name(result, dst) for _, dst in calls.values()} == {"One"}


def test_an_unqualified_call_in_a_method_reaches_its_own_class_first(
    tmp_path: Path,
) -> None:
    """Inside ``TA.Run`` a bare ``Get`` is ``Self.Get``: Pascal looks a name up in
    the method's class before the unit. A free procedure of the same name is the
    callee only outside the class."""
    (tmp_path / "a.pas").write_text("""\
program P;
type
  TA = class
    procedure Run;
    procedure Get;
  end;

procedure Get; begin end;

procedure TA.Get; begin end;

procedure TA.Run;
begin
  Get;
end;

procedure Free;
begin
  Get;
end;

begin
end.
""")
    result = analyze_pascal(tmp_path)
    calls = _calls(result)
    assert calls[14][0] == "TA.Run"
    assert _dst_name(result, calls[14][1]) == "TA.Get"
    assert calls[19][0] == "Free"
    assert _dst_name(result, calls[19][1]) == "Get"


def test_a_nested_procedure_is_a_symbol_and_its_calls_have_a_real_src(
    tmp_path: Path,
) -> None:
    """WI-sigit's filed repro. ``Inner`` is a local symbol; the call in it is drawn
    from it (not from a dangling id), the call to it from ``Outer`` resolves to it,
    and the main program's ``Outer;`` is drawn from the program."""
    (tmp_path / "a.pas").write_text("""\
program P;
procedure Helper; begin end;
procedure Outer;
  procedure Inner; begin Helper; end;
begin Inner; end;
begin Outer; end.
""")
    result = analyze_pascal(tmp_path)
    inner = [s for s in result.symbols if s.name == "Inner"]
    assert len(inner) == 1 and inner[0].meta["is_local"] is True, inner
    calls = _calls(result)
    assert calls[4] == ("Inner", next(s.id for s in result.symbols if s.name == "Helper"))
    assert calls[5] == ("Outer", inner[0].id)
    assert calls[6][0] == "P"
    assert _dst_name(result, calls[6][1]) == "Outer"


def test_a_nested_procedure_is_not_visible_outside_its_parent(tmp_path: Path) -> None:
    """A local ``Inner`` is in scope only inside ``Outer``. A call to ``Inner``
    elsewhere is the unit-level ``Inner``, never the local one, and two local
    procedures of one name in two parents are told apart by position."""
    (tmp_path / "a.pas").write_text("""\
program P;
procedure Inner; begin end;
procedure A;
  procedure Inner; begin end;
begin Inner; end;
procedure B;
  procedure Inner; begin end;
begin Inner; end;
procedure C;
begin Inner; end;
begin end.
""")
    result = analyze_pascal(tmp_path)
    by_line = {s.span.start_line: s for s in result.symbols if s.name == "Inner"}
    assert set(by_line) == {2, 4, 7}
    assert "is_local" not in (by_line[2].meta or {})
    calls = _calls(result)
    assert calls[5] == ("A", by_line[4].id)
    assert calls[8] == ("B", by_line[7].id)
    assert calls[10] == ("C", by_line[2].id)


def test_a_procedure_nested_in_a_method_sees_the_method_class(tmp_path: Path) -> None:
    """A local function inside ``TA.Make`` is drawn as itself, and a bare call in
    the method or its local function still reaches the method's class first."""
    (tmp_path / "u.pas").write_text("""\
unit U;
interface
type
  TA = class
    class function Make: TA;
    procedure Run;
  end;
implementation
procedure Helper; begin end;
class function TA.Make: TA;
  function Local: Integer; begin Run; Result := 1; end;
begin
  Local;
end;
procedure TA.Run;
begin
  Make;
end;
initialization
  Helper;
finalization
  Helper();
end.
""")
    result = analyze_pascal(tmp_path)
    calls = _calls(result)
    assert calls[11][0] == "Local"
    assert _dst_name(result, calls[11][1]) == "TA.Run"
    assert calls[13][0] == "TA.Make"
    assert _dst_name(result, calls[13][1]) == "Local"
    assert calls[17][0] == "TA.Run"
    assert _dst_name(result, calls[17][1]) == "TA.Make"
    # A unit's initialization / finalization code is drawn from the unit.
    assert calls[20][0] == "U" and calls[22][0] == "U"


def test_a_call_outside_every_declaration_is_drawn_from_the_file(tmp_path: Path) -> None:
    """A file with neither ``program`` nor ``unit`` (a fragment, or a ``library``,
    which emits no symbol of its own) still anchors its block's call: on the file,
    never on a dangling id."""
    (tmp_path / "frag.pas").write_text("""\
procedure Helper; begin end;
begin
  Helper;
end.
""")
    (tmp_path / "lib.pas").write_text("""\
library L;
procedure Helper2; begin end;
exports Helper2;
begin
  Helper2;
end.
""")
    result = analyze_pascal(tmp_path)
    assert unemitted_edge_sources(result.symbols, result.edges) == []
    srcs = {(e.src.split(":")[1], e.line): e.src for e in result.edges if e.edge_type == "calls"}
    assert srcs[("frag.pas", 3)].endswith(":1-1:file:file"), srcs
    assert srcs[("lib.pas", 5)].endswith(":1-1:file:file"), srcs
