# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Verilog/SystemVerilog analyzer using tree-sitter-verilog.

Tests verify that the analyzer correctly extracts:
- Module definitions
- Module instantiations
- Input/output ports
- Wire/register declarations
- Always blocks
"""

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import PASS_VERSION
from hypergumbo_core import __version__
from hypergumbo_lang_extended1.verilog import (
    PASS_ID,
    analyze_verilog_files,
    find_verilog_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "verilog"
    assert PASS_VERSION == __version__


def test_analyze_simple_module(tmp_path):
    """Test detection of simple module definition."""
    verilog_file = tmp_path / "counter.v"
    verilog_file.write_text("""
module counter (
    input clk,
    input reset,
    output reg [7:0] count
);
    always @(posedge clk) begin
        count <= count + 1;
    end
endmodule
""")
    result = analyze_verilog_files(tmp_path)

    assert not result.skipped
    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    assert modules[0].name == "counter"
    assert modules[0].language == "verilog"


def test_analyze_module_instantiation(tmp_path):
    """Test detection of module instantiation."""
    verilog_file = tmp_path / "top.v"
    verilog_file.write_text("""
module counter (input clk, output [7:0] count);
endmodule

module top;
    wire clk;
    wire [7:0] out;

    counter u1 (.clk(clk), .count(out));
endmodule
""")
    result = analyze_verilog_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 2

    # Check for instantiates edge
    inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
    assert len(inst_edges) >= 1


def test_module_instantiation_fallback_when_names_collide_across_files(tmp_path):
    """WI-sofaf / INV-paroh: two files each declare ``module driver`` (the
    same module name); the instantiation site can't disambiguate by
    kind (both are ``module``), so ``SymbolByName.lookup_one`` returns
    ``is_fallback=True``. The resulting edge must carry
    ``confidence=0.50`` and ``meta["disambiguation_fallback"]=True``
    per the INV-zuhub ambiguous-resolution contract.

    This pins the fallback branch end-to-end (the unit test in
    ``test_symbol_indexes.TestLookupOne`` covers the helper; this test
    covers the verilog integration applying the contract to ``Edge``).
    """
    (tmp_path / "a.v").write_text("""
module counter (input clk, output [7:0] count);
endmodule
""")
    (tmp_path / "b.v").write_text("""
module counter (input clk, output [7:0] count);
endmodule
""")
    (tmp_path / "top.v").write_text("""
module top;
    wire clk;
    wire [7:0] out;
    counter u1 (.clk(clk), .count(out));
endmodule
""")
    result = analyze_verilog_files(tmp_path)

    inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
    assert len(inst_edges) >= 1, "Expected at least one instantiates edge"

    # The driver instantiation should be marked as a disambiguation fallback.
    fallback_edges = [
        e for e in inst_edges
        if e.meta and e.meta.get("disambiguation_fallback") is True
    ]
    assert len(fallback_edges) >= 1, (
        "Expected the ambiguous driver instantiation to carry "
        "meta['disambiguation_fallback']=True"
    )
    assert fallback_edges[0].confidence == 0.50, (
        f"Expected confidence=0.50 on fallback edge, got "
        f"{fallback_edges[0].confidence}"
    )


def test_analyze_wire_and_reg(tmp_path):
    """Test detection of wire and reg declarations."""
    verilog_file = tmp_path / "signals.v"
    verilog_file.write_text("""
module test;
    wire clk;
    wire [31:0] data_bus;
    reg [7:0] counter;
    reg valid;
endmodule
""")
    result = analyze_verilog_files(tmp_path)

    # Module should be detected
    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1


def test_find_verilog_files(tmp_path):
    """Test that Verilog files are discovered correctly."""
    (tmp_path / "counter.v").write_text("module counter; endmodule")
    (tmp_path / "top.sv").write_text("module top; endmodule")  # SystemVerilog
    (tmp_path / "header.vh").write_text("`define WIDTH 8")
    (tmp_path / "not_verilog.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "sub.v").write_text("module sub; endmodule")

    files = list(find_verilog_files(tmp_path))
    # Should find .v, .sv, .vh files
    assert len(files) >= 4


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no Verilog files."""
    result = analyze_verilog_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    verilog_file = tmp_path / "test.v"
    verilog_file.write_text("module test; endmodule")

    result = analyze_verilog_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    verilog_file = tmp_path / "broken.v"
    verilog_file.write_text("module broken syntax {{{{")

    # Should not raise an exception
    result = analyze_verilog_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, AnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    verilog_file = tmp_path / "span.v"
    verilog_file.write_text("""module test_module;
endmodule
""")
    result = analyze_verilog_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1

    # Check span
    assert modules[0].span.start_line >= 1
    assert modules[0].span.end_line >= modules[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo_lang_extended1.verilog import is_verilog_tree_sitter_available

    # The function should return a boolean
    result = is_verilog_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_verilog_files(tmp_path):
    """Test analysis across multiple Verilog files."""
    (tmp_path / "alu.v").write_text("""
module alu (input [7:0] a, b, output [7:0] result);
endmodule
""")
    (tmp_path / "cpu.v").write_text("""
module cpu;
    wire [7:0] a, b, r;
    alu u_alu (.a(a), .b(b), .result(r));
endmodule
""")

    result = analyze_verilog_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 2


def test_systemverilog_interface(tmp_path):
    """Test detection of SystemVerilog interface."""
    sv_file = tmp_path / "axi.sv"
    sv_file.write_text("""
interface axi_if;
    logic [31:0] addr;
    logic [31:0] data;
    logic valid;
    logic ready;
endinterface
""")
    result = analyze_verilog_files(tmp_path)

    # Interface should be detected
    interfaces = [s for s in result.symbols if s.kind == "interface"]
    assert len(interfaces) >= 1


# -- tasks, functions and nets inside a design unit ----------------------------


def _analyze(tmp_path, files: dict[str, str]) -> AnalysisResult:
    """Write ``files`` under ``tmp_path`` and analyze the directory."""
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return analyze_verilog_files(tmp_path)


def _named(result: AnalysisResult, kind: str) -> list[str]:
    """Names of the symbols of ``kind``, sorted."""
    return sorted(s.name for s in result.symbols if s.kind == kind)


def _one(result: AnalysisResult, kind: str, name: str, inside: str):
    """The symbol of ``kind`` and ``name`` whose span lies in module ``inside``."""
    owner = next(s for s in result.symbols if s.kind in ("module", "interface")
                 and s.name == inside)
    found = [
        s for s in result.symbols
        if s.kind == kind and s.name == name and s.path == owner.path
        and (owner.span.start_line, owner.span.start_col)
        <= (s.span.start_line, s.span.start_col)
        and (s.span.end_line, s.span.end_col)
        <= (owner.span.end_line, owner.span.end_col)
    ]
    assert len(found) == 1, f"{kind} {name} in {inside}: {found}"
    return found[0]


_MODULE_BODY = """\
  task run; begin end endtask
  function integer f; input x; f = x; endfunction
  wire w;
"""


def test_task_function_and_net_in_a_module_are_symbols(tmp_path):
    """A module's task, function and net each emit a symbol, with a
    ``stable_id``, beside the module itself."""
    result = _analyze(tmp_path, {"rtl/a.v": f"module a;\n{_MODULE_BODY}endmodule\n"})

    assert _named(result, "module") == ["a"]
    assert _named(result, "task") == ["run"]
    assert _named(result, "function") == ["f"]
    assert _named(result, "signal") == ["w"]
    for kind, name in (("task", "run"), ("function", "f"), ("signal", "w")):
        sym = _one(result, kind, name, "a")
        assert sym.language == "verilog"
        assert sym.stable_id and sym.stable_id.startswith("sha256:")


def test_same_named_members_of_two_modules_hold_different_ids(tmp_path):
    """``task run`` in module a and in module b are two symbols with two
    ``stable_id``s, and deleting module a leaves module b's ids where they
    were: the enclosing module is part of the identity."""
    module_b = f"module b;\n{_MODULE_BODY}endmodule\n"
    both = _analyze(tmp_path / "both", {"rtl/t.v": f"module a;\n{_MODULE_BODY}endmodule\n" + module_b})
    alone = _analyze(tmp_path / "alone", {"rtl/t.v": module_b})

    for kind, name in (("task", "run"), ("function", "f"), ("signal", "w")):
        in_a = _one(both, kind, name, "a")
        in_b = _one(both, kind, name, "b")
        assert in_a.stable_id != in_b.stable_id
        assert in_a.id != in_b.id
        assert _one(alone, kind, name, "b").stable_id == in_b.stable_id


def test_every_declared_net_name_is_a_signal(tmp_path):
    """One signal per declared name, for every built-in net type, with or
    without a range, strength, delay or initial assignment."""
    result = _analyze(tmp_path, {"rtl/nets.sv": """\
module m;
  wire w, z;
  wire signed [7:0] bus = 8'h0, other;
  wire (strong0, weak1) #5 driven = w;
  tri t;
  supply0 gnd;
  supply1 vdd;
  wand wa;
  wor wo;
  uwire u;
  trireg (small) tr;
  wire [3:0] arr [0:7];
  interconnect ic1, ic2;
endmodule
"""})

    assert _named(result, "signal") == sorted([
        "w", "z", "bus", "other", "driven", "t", "gnd", "vdd", "wa", "wo", "u",
        "tr", "arr", "ic1", "ic2",
    ])


def test_members_of_an_interface_package_program_class_and_checker(tmp_path):
    """A task, function or net is emitted wherever it is declared, not only
    in a module; the same name under two different parents holds two ids."""
    result = _analyze(tmp_path, {"rtl/units.sv": """\
package pkg;
  function automatic int go(int a); return a; endfunction
  task automatic wait_n(input int n); endtask
endpackage
interface bus_if;
  wire rdy;
  task automatic send(input logic d); endtask
  function logic go(); return 1; endfunction
endinterface
program prog; task pt; endtask wire pw; endprogram
class C;
  function new(); endfunction
  function int go(int x); return x; endfunction
  static function void sf(); endfunction
endclass
checker chk(input x); function bit go(); return 1; endfunction endchecker
function int go(int x); return x; endfunction
"""})

    assert _named(result, "task") == ["pt", "send", "wait_n"]
    assert _named(result, "function") == ["go"] * 5 + ["new", "sf"]
    assert _named(result, "signal") == ["pw", "rdy"]
    go_ids = [s.stable_id for s in result.symbols if s.kind == "function" and s.name == "go"]
    assert len(set(go_ids)) == 5
    assert all(go_ids)


def test_members_of_generate_blocks_and_nested_modules(tmp_path):
    """A labelled generate block and a nested module are parents too."""
    result = _analyze(tmp_path, {"rtl/gen.sv": """\
module outer;
  generate
    if (1) begin : left
      wire gw;
      function int gf(int x); return x; endfunction
    end
    if (1) begin : right
      wire gw;
      function int gf(int x); return x; endfunction
    end
    if (1) begin wire uw; end
  endgenerate
  module inner; wire gw; endmodule
endmodule
"""})

    for kind, name, count in (("signal", "gw", 3), ("function", "gf", 2)):
        ids = [s.stable_id for s in result.symbols if s.kind == kind and s.name == name]
        assert len(ids) == count
        assert len(set(ids)) == count
    # An unlabelled generate block names no scope; its net is still a symbol.
    assert _named(result, "signal") == ["gw", "gw", "gw", "uw"]


def test_nonansi_function_and_port_nets(tmp_path):
    """Verilog-1995 style: ports declared in the body, redeclared as nets."""
    result = _analyze(tmp_path, {"rtl/old.v": """\
module old(a, b);
  input a;
  output b;
  wire a;
  wire b;
  function [3:0] rf;
    input x;
    rf = x;
  endfunction
endmodule
"""})

    assert _named(result, "signal") == ["a", "b"]
    assert _named(result, "function") == ["rf"]


def test_what_the_grammar_does_not_parse_emits_no_made_up_symbol(tmp_path):
    """tree-sitter-verilog gives the class as the name of an out-of-block
    ``task C::et``, and reads a variable of a user-defined type as a net of a
    user-defined nettype. Neither is a symbol, and the declarations around
    them still are."""
    result = _analyze(tmp_path, {"rtl/hard.sv": """\
module m;
  typedef enum { IDLE, BUSY } state_t;
  state_t st;
  wire w, z;
  task t2(input a, output b); b = a; endtask
  initial begin
    t2(w, z);
  end
  generate
    if (1) begin : g wire gw; end
  endgenerate
endmodule
class C;
  extern task et(int y);
endclass
task C::et(int y); endtask
function int C::m2(int x); return x; endfunction
function int top_f(int x); return x; endfunction
"""})

    assert _named(result, "module") == ["m"]
    assert _named(result, "signal") == ["gw", "w", "z"]
    assert _named(result, "task") == ["t2"]
    assert _named(result, "function") == ["top_f"]


def test_error_recovery_debris_is_not_a_net(tmp_path):
    """A package-scoped task enable is a syntax error to tree-sitter-verilog,
    which recovers by reading ``end generate`` as a net named ``generate`` of
    a user-defined nettype. Only the nets the source declares are symbols."""
    result = _analyze(tmp_path, {"rtl/debris.sv": """\
module d;
  wire dw;
  initial begin
    pkg::wait_n(3);
  end
  generate
    if (1) begin : g wire dg; end
  endgenerate
endmodule
"""})

    assert _named(result, "signal") == ["dg", "dw"]


def test_a_net_does_not_capture_an_instantiation(tmp_path):
    """An instantiation of an undeclared module stays an external edge when a
    net, task or function somewhere has the module's name."""
    result = _analyze(tmp_path, {"rtl/top.v": """\
module top;
  wire sub, x, y;
  task other; begin end endtask
  sub u1 (.a(x), .b(y));
  other u2 (.a(x), .b(y));
endmodule
"""})

    targets = sorted(e.dst for e in result.edges if e.edge_type == "instantiates")
    assert targets == ["verilog:external:other:module", "verilog:external:sub:module"]


def test_a_member_lies_inside_its_module_and_spans_its_own_declaration(tmp_path):
    """A member's span is its own declaration (one declarator for a net),
    inside the module's span, so containment can place it."""
    result = _analyze(tmp_path, {"rtl/span.v": """\
module a;
  task run;
    begin end
  endtask
  wire w, z;
endmodule
"""})

    run = _one(result, "task", "run", "a")
    assert (run.span.start_line, run.span.end_line) == (2, 4)
    w = _one(result, "signal", "w", "a")
    z = _one(result, "signal", "z", "a")
    assert (w.span.start_line, w.span.start_col, w.span.end_col) == (5, 7, 8)
    assert (z.span.start_line, z.span.start_col, z.span.end_col) == (5, 10, 11)
    assert w.id != z.id


def test_tasks_and_nets_are_contained_by_their_innermost_design_unit(tmp_path):
    """The containment linker places functions but neither tasks nor signals,
    so the analyzer emits their ``contains`` edge: from the innermost module
    or interface around the member, or from the file when there is none."""
    result = _analyze(tmp_path, {"rtl/own.sv": """\
module outer;
  wire ow;
  task ot; begin end endtask
  function integer of; input x; of = x; endfunction
  module inner;
    wire iw;
  endmodule
endmodule
interface bus_if;
  wire rdy;
  task send; begin end endtask
endinterface
package pkg;
  task automatic wait_n(input int n); endtask
endpackage
wire loose;
"""})

    by_id = {s.id: s for s in result.symbols}
    owners = {
        (by_id[e.dst].kind, by_id[e.dst].name): by_id[e.src].name if e.src in by_id else e.src
        for e in result.edges if e.edge_type == "contains"
    }
    assert owners == {
        ("signal", "ow"): "outer",
        ("task", "ot"): "outer",
        ("signal", "iw"): "inner",
        ("signal", "rdy"): "bus_if",
        ("task", "send"): "bus_if",
        ("task", "wait_n"): "verilog:rtl/own.sv:1-1:file:file",
        ("signal", "loose"): "verilog:rtl/own.sv:1-1:file:file",
    }
    contains = [e for e in result.edges if e.edge_type == "contains"]
    assert len(contains) == len(owners)
    assert {e.evidence_type for e in contains} == {"enclosing_scope"}
