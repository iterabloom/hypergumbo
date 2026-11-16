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


# -- instantiations the grammar reads as checker or UDP instances --------------


def _instantiations(result: AnalysisResult) -> list[tuple[str, str, int]]:
    """``(source name, target name or external id, line)`` of every
    ``instantiates`` edge, sorted by line."""
    names = {s.id: s.name for s in result.symbols}
    return sorted(
        ((names[e.src], names.get(e.dst, e.dst), e.line)
         for e in result.edges if e.edge_type == "instantiates"),
        key=lambda edge: edge[2],
    )


def test_one_port_positional_wildcard_and_empty_instantiations_emit_edges(tmp_path):
    """tree-sitter-verilog gives ``module_instantiation`` to an instantiation
    with two or more named ports; one named port, no port, one positional
    port and ``.*`` parse as ``checker_instantiation``. Each is an
    ``instantiates`` edge to the declared module, like the two-port form."""
    result = _analyze(tmp_path, {"src/d.v": """\
module a(input x); endmodule
module b;
  wire w, z;
  a u_a(.x(w));
  a u_b();
  a u_c(w);
  a u_h(.*);
endmodule
module c(input x, input y); endmodule
module d;
  wire w, z;
  c u_e(.x(w), .y(z));
endmodule
"""})

    assert _instantiations(result) == [
        ("b", "a", 4), ("b", "a", 5), ("b", "a", 6), ("b", "a", 7), ("d", "c", 12),
    ]
    assert {e.confidence for e in result.edges if e.edge_type == "instantiates"} == {0.90}
    assert {e.evidence_type for e in result.edges if e.edge_type == "instantiates"} == {
        "verilog_instantiation",
    }


def test_instance_arrays_and_positional_port_lists_emit_edges(tmp_path):
    """An instance array, an implicit ``.x`` port, a port expression the
    grammar rejects as a checker argument (a concatenation), and two or more
    positional ports (``udp_instantiation``, with or without an array range)
    are module instantiations too."""
    result = _analyze(tmp_path, {"src/forms.sv": """\
module a(input x, input y); endmodule
module b;
  wire w, z, q;
  a u_arr[3:0](.x(w));
  a u_impl(.x);
  a u_mix(.x(w), .*);
  a u_cat({w, z});
  a u_pos2(w, z);
  a u_pos3(w, z, q);
  a u_posarr[1:0](w, z);
endmodule
"""})

    assert _instantiations(result) == [("b", "a", line) for line in range(4, 11)]


def test_a_real_checker_udp_or_program_instance_is_no_module_edge(tmp_path):
    """The same node types hold real checker and UDP instances. A name the
    repository declares as a checker, a primitive or a program (in any file)
    and not as a module is not a module instantiation; neither is a gate, an
    unnamed UDP instance, a checker instantiated in procedural code, or a
    package-scoped checker."""
    result = _analyze(tmp_path, {
        "src/units.sv": """\
checker chk(input x); endchecker
primitive udp(output o, input i, input j);
  table 0 0 : 0; endtable
endprimitive
primitive udp_old(o, i, j);
  output o; input i, j;
  table 0 0 : 0; endtable
endprimitive
program prog(input x); endprogram
""",
        "src/top.sv": """\
module top(input clk);
  wire w, z, o;
  chk c1(w);
  udp p1(o, w, z);
  udp_old p2(o, w, z);
  udp (o, w, z);
  prog pr(w);
  and g1(o, w, z);
  pkg::scoped_chk c2(.x(w));
  always @(posedge clk) proc_chk c3(w);
  initial begin proc_chk c4(w); end
endmodule
""",
    })

    assert _instantiations(result) == []


def test_a_task_enable_is_no_instantiation(tmp_path):
    """``initial t2(w, z);`` reads as a checker instantiation with no instance
    name; it is a task enable."""
    result = _analyze(tmp_path, {"src/tb.sv": """\
module t2(input a, input b); endmodule
module tb;
  wire w, z;
  initial t2(w, z);
endmodule
"""})

    assert _instantiations(result) == []


def test_a_module_wins_over_a_same_named_checker(tmp_path):
    """A name declared as a module in one file and as a checker in another
    resolves to the module."""
    result = _analyze(tmp_path, {
        "src/m.sv": "module both(input x); endmodule\n",
        "src/c.sv": "checker both(input x); endchecker\n",
        "src/top.sv": "module top;\n  wire w;\n  both u1(.x(w));\nendmodule\n",
    })

    assert _instantiations(result) == [("top", "both", 3)]


def test_an_undeclared_module_is_external_whatever_its_port_list(tmp_path):
    """An instantiation of a module the repository does not declare is the
    same external edge (confidence 0.70) with one port as with two."""
    result = _analyze(tmp_path, {"src/top.sv": """\
module top;
  wire w, z;
  vendor_two u1(.a(w), .b(z));
  vendor_one u2(.a(w));
  vendor_none u3();
  vendor_pos u4(w, z);
endmodule
"""})

    assert _instantiations(result) == [
        ("top", "verilog:external:vendor_two:module", 3),
        ("top", "verilog:external:vendor_one:module", 4),
        ("top", "verilog:external:vendor_none:module", 5),
        ("top", "verilog:external:vendor_pos:module", 6),
    ]
    assert {e.confidence for e in result.edges if e.edge_type == "instantiates"} == {0.70}


def test_interface_instances_in_a_module_and_in_an_interface(tmp_path):
    """An interface instance (``bus_if bif();``) is an ``instantiates`` edge
    to the interface, and an instance inside an interface body has the
    enclosing interface as its source, whichever node type the grammar gives
    it. A same-named module does not take an interface-body instance."""
    result = _analyze(tmp_path, {"src/ifs.sv": """\
interface inner_if; logic v; endinterface
module inner_if; endmodule
interface outer_if;
  logic v;
  inner_if u_one();
  inner_if u_two(.a(v), .b(v));
  inner_if u_pos(v, v);
endinterface
interface bus_if; logic v; endinterface
module top;
  bus_if bif();
endmodule
"""})

    by_id = {s.id: s for s in result.symbols}
    edges = sorted(
        ((by_id[e.src].kind, by_id[e.src].name, by_id[e.dst].kind, by_id[e.dst].name, e.line)
         for e in result.edges if e.edge_type == "instantiates"),
        key=lambda edge: edge[4],
    )
    assert edges == [
        ("interface", "outer_if", "interface", "inner_if", 5),
        ("interface", "outer_if", "interface", "inner_if", 6),
        ("interface", "outer_if", "interface", "inner_if", 7),
        ("module", "top", "interface", "bus_if", 11),
    ]


def test_an_instantiation_outside_every_parsed_unit_emits_no_edge(tmp_path):
    """A user task enable in a ``begin`` block is a syntax error to
    tree-sitter-verilog, which then yields no ``module_declaration`` for the
    module around it: the module is no symbol, and its instantiations have no
    source to hang an edge on."""
    result = _analyze(tmp_path, {"tb/tb.sv": """\
module dut(input clk, input rst); endmodule
module tb;
  reg clk, rst;
  dut u_dut(.clk(clk), .rst(rst));
  dut u_one(.clk(clk));
  task check(input [7:0] expect); begin end endtask
  initial begin
    check(8'd1);
  end
endmodule
"""})

    assert _named(result, "module") == ["dut"]
    assert _instantiations(result) == []


# -- calls into functions, and what the dead-code view makes of them -----------


def _calls(result: AnalysisResult) -> list[tuple[str, str, int]]:
    """``(source, target, line)`` of every ``calls`` edge, sorted by line then
    names. A symbol is written ``kind:name@line`` (its own start line); a
    source that is no symbol (the file node) is written as its id."""
    by_id = {s.id: s for s in result.symbols}

    def show(symbol_id: str) -> str:
        sym = by_id.get(symbol_id)
        return f"{sym.kind}:{sym.name}@{sym.span.start_line}" if sym else symbol_id

    return sorted(
        ((show(e.src), show(e.dst), e.line) for e in result.edges if e.edge_type == "calls"),
        key=lambda edge: (edge[2], edge[0], edge[1]),
    )


def test_calls_resolve_in_the_enclosing_scopes_innermost_first(tmp_path):
    """A call with arguments (``tf_call``) and one without (``f0()``, which
    the grammar reads as a ``let_expression``) reach the function declared in
    the nearest enclosing scope: the generate block, then the module, then the
    file. The source is the innermost function or task around the call, else
    the module or interface."""
    result = _analyze(tmp_path, {"rtl/m.sv": """\
function int top_f(int x); return x; endfunction
function int top_g(int x); return top_f(x); endfunction
module m(input [7:0] a, output [7:0] y);
  function [7:0] f; input [7:0] x; f = x + 1; endfunction
  function [7:0] g; input [7:0] x; g = f(x); endfunction
  function [7:0] f0; f0 = 8'd0; endfunction
  wire [7:0] w = g(a);
  assign y = f(w) + f0();
  reg [7:0] r;
  always @(*) r = top_f(a);
  task t; output [7:0] o; o = f(8'd1); endtask
  generate if (1) begin : gen
    function [7:0] f; input [7:0] x; f = x; endfunction
    wire [7:0] gw = f(a) + g(a);
  end endgenerate
endmodule
interface bus_if;
  logic v;
  function logic get(); return v; endfunction
  wire rdy = get();
endinterface
"""})

    assert _calls(result) == [
        ("function:top_g@2", "function:top_f@1", 2),
        ("function:g@5", "function:f@4", 5),
        ("module:m@3", "function:g@5", 7),
        ("module:m@3", "function:f0@6", 8),
        ("module:m@3", "function:f@4", 8),
        ("module:m@3", "function:top_f@1", 10),
        ("task:t@11", "function:f@4", 11),
        ("module:m@3", "function:f@13", 14),
        ("module:m@3", "function:g@5", 14),
        ("interface:bus_if@17", "function:get@19", 20),
    ]
    assert {e.evidence_type for e in result.edges if e.edge_type == "calls"} == {"ast_call"}


def test_package_qualified_static_and_imported_calls(tmp_path):
    """``pkg::f(...)`` reaches the package's function in whatever file
    declares it, ``C::m(...)`` the class's, and an unqualified name reaches a
    package function through an ``import`` visible where the call is: in the
    module header, the module body or the compilation unit. A local
    declaration wins over an import and an explicit import over a wildcard
    one; an import in another module is not visible."""
    result = _analyze(tmp_path, {
        "rtl/pkg.sv": """\
package pkg;
  function automatic int add(int a, int b); return a + b; endfunction
  function automatic int twice(int a); return add(a, a); endfunction
  function automatic int zero(); return 0; endfunction
endpackage
package other;
  function automatic int add(int a, int b); return a; endfunction
endpackage
class C;
  static function int m(int x); return x; endfunction
endclass
""",
        "rtl/use.sv": """\
import other::*;
module q(input int a, output int y);
  assign y = pkg::add(a, 1) + pkg::zero() + C::m(a) + nopkg::add(a, 1);
endmodule
module hdr import pkg::add; (input int a, output int y);
  assign y = add(a, 1);
endmodule
module body(input int a, output int y);
  import pkg::*;
  function int twice(int x); return x; endfunction
  assign y = twice(a) + zero();
endmodule
module unit_import(input int a, output int y);
  assign y = add(a, 1) + twice(a);
endmodule
module explicit_wins(input int a, output int y);
  import other::*;
  import pkg::add;
  assign y = add(a, 1);
endmodule
""",
    })

    assert _calls(result) == [
        ("function:twice@3", "function:add@2", 3),
        ("module:q@2", "function:add@2", 3),
        ("module:q@2", "function:m@10", 3),
        ("module:q@2", "function:zero@4", 3),
        ("module:hdr@5", "function:add@2", 6),
        ("module:body@8", "function:twice@10", 11),
        ("module:body@8", "function:zero@4", 11),
        ("module:unit_import@13", "function:add@7", 14),
        ("module:explicit_wins@16", "function:add@2", 19),
    ]


def test_calls_the_source_does_not_decide_emit_no_edge(tmp_path):
    """No ``calls`` edge for a system task or function, a name declared in no
    enclosing scope and no visible import, a call through an instance or an
    interface port, or a name two declarations of one scope share. ``this.m``
    reaches the enclosing class's own ``m`` and nothing else, and a bare name
    in a class that ``extends`` another does not look past the class: the
    name may be inherited."""
    result = _analyze(tmp_path, {"rtl/no.sv": """\
function int outside(int x); return x; endfunction
module m(input [7:0] a, output [7:0] y);
`ifdef A
  function [7:0] twin; input [7:0] x; twin = x; endfunction
`else
  function [7:0] twin; input [7:0] x; twin = ~x; endfunction
`endif
  reg [7:0] r;
  initial begin
    $display("%d", $clog2(8));
    r = undefined_fn(a);
    r = u_sub.h(a);
    r = port_if.get();
    r = twin(a);
  end
endmodule
class C;
  function int m(int x); return x; endfunction
  function int n(int x); return this.m(x) + this.outside(x); endfunction
endclass
class D extends C;
  function int p(int x); return outside(x); endfunction
endclass
module standalone;
  reg [7:0] r;
  initial r = this.outside(8'd1);
endmodule
"""})

    assert _calls(result) == [("function:n@19", "function:m@18", 19)]


def test_each_of_two_same_named_modules_calls_its_own_function(tmp_path):
    """Scopes are told apart by where they are, not by name: an ``ifdef`` pair
    of ``module dup`` each calls the ``f`` it declares."""
    result = _analyze(tmp_path, {"rtl/dup.v": """\
`ifdef FPGA
module dup(input a, output y);
  function f; input x; f = x; endfunction
  assign y = f(a);
endmodule
`else
module dup(input a, output y);
  function f; input x; f = ~x; endfunction
  assign y = f(a);
endmodule
`endif
"""})

    assert _calls(result) == [
        ("module:dup@2", "function:f@3", 4),
        ("module:dup@7", "function:f@8", 9),
    ]


def test_a_task_enable_emits_no_calls_edge(tmp_path):
    """tree-sitter-verilog parses neither ``t2(w, z);`` nor a bare ``run;`` as
    a call (a syntax error and a declaration), so neither is an edge. The
    function call beside them still is: anchored on the module, or on the file
    where the task enable cost the module its ``module_declaration``."""
    result = _analyze(tmp_path, {
        "tb/tb.sv": """\
module tb;
  reg [7:0] w, z;
  task run; begin end endtask
  task t2(input [7:0] a, output [7:0] b); b = a; endtask
  function [7:0] twice; input [7:0] x; twice = x * 2; endfunction
  initial begin
    run;
    t2(w, z);
    w = twice(z);
  end
endmodule
""",
        "tb/lost.sv": """\
module lost;
  reg [7:0] q;
  task check(input [7:0] expect); begin end endtask
  function [7:0] half; input [7:0] x; half = x / 2; endfunction
  initial begin
    check(8'd1);
    q = half(q);
  end
endmodule
""",
    })

    assert _named(result, "module") == ["tb"]
    assert _named(result, "task") == ["check", "run", "t2"]
    assert _calls(result) == [
        ("verilog:tb/lost.sv:1-1:file:file", "function:half@4", 7),
        ("module:tb@1", "function:twice@5", 9),
    ]


def test_is_exported_follows_where_a_task_or_function_is_declared(tmp_path):
    """A package's tasks and functions are importable by every design unit:
    exported. One declared in a module, program or checker (a generate block
    included) is local to it: not exported. For an interface, a class and the
    compilation unit the analyzer has no rule and says nothing (``None``)."""
    result = _analyze(tmp_path, {"rtl/vis.sv": """\
package pkg;
  function automatic int pf(int a); return a; endfunction
  task automatic pt(input int n); endtask
endpackage
module m;
  function integer mf; input x; mf = x; endfunction
  task mt; begin end endtask
  generate if (1) begin : g
    function integer gf; input x; gf = x; endfunction
  end endgenerate
endmodule
program prog; task progt; endtask endprogram
checker chk(input x); function bit cf(); return 1; endfunction endchecker
interface bus_if; function logic bf(); return 1; endfunction endinterface
class C; function int cm(int x); return x; endfunction endclass
function int uf(int x); return x; endfunction
"""})

    exported = {s.name: s.is_exported for s in result.symbols if s.kind in ("task", "function")}
    assert exported == {
        "pf": True, "pt": True,
        "mf": False, "mt": False, "gf": False, "progt": False, "cf": False,
        "bf": None, "cm": None, "uf": None,
    }
    assert {s.is_exported for s in result.symbols if s.kind in ("module", "signal")} == {None}


def test_a_module_nothing_instantiates_is_an_entrypoint(tmp_path):
    """A module no instantiation statement names is a top-level module
    (IEEE 1800 §23.3.1), elaborated without being instantiated: it carries the
    ``entrypoint`` concept. An instantiated module and an interface do not."""
    result = _analyze(tmp_path, {"rtl/top.sv": """\
interface bus_if; logic v; endinterface
module leaf(input x); endmodule
module mid(input x);
  leaf u_leaf(.x(x));
endmodule
module top;
  wire w;
  bus_if bif();
  mid u_mid(.x(w));
endmodule
module spare; endmodule
"""})

    concepts = {
        s.name: (s.meta or {}).get("concepts")
        for s in result.symbols if s.kind in ("module", "interface")
    }
    entry = [{"concept": "entrypoint", "framework": "verilog"}]
    assert concepts == {
        "bus_if": None, "leaf": None, "mid": None, "top": entry, "spare": entry,
    }


def _dead_code(repo, files: dict[str, str]) -> tuple[dict, set[str]]:
    """Survey ``files`` and return ``dead-code-maybe``'s summary and the
    ``name@line`` of its candidates (default seeds)."""
    import io
    import json
    from contextlib import redirect_stderr, redirect_stdout

    from hypergumbo_core.cli import build_parser, cmd_dead_code_maybe, run_behavior_map

    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    map_file = repo / "map.json"
    run_behavior_map(repo_root=repo, out_path=map_file, include_sketch_precomputed=False)
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        assert cmd_dead_code_maybe(build_parser().parse_args([
            "dead-code-maybe", str(repo), "--input", str(map_file), "--format", "json",
        ])) == 0
    data = json.loads(out.getvalue())
    return data["summary"], {
        f'{c["name"]}@{c["span"]["start_line"]}' for c in data["dead_candidates"]
    }


def test_dead_code_view_keeps_only_the_functions_nothing_calls(tmp_path):
    """End to end: a function called from a module body, from another
    function, through a package qualifier or in an instantiated module is not
    a dead-code candidate; an exported package function nothing calls is a
    seed; the functions nothing calls stay candidates."""
    summary, dead = _dead_code(tmp_path, {
        "rtl/pkg.sv": """\
package pkg;
  function automatic int add(int a, int b); return a + b; endfunction
  function automatic int unused_api(int a); return a; endfunction
endpackage
""",
        "rtl/design.sv": """\
module leaf(input int a, output int y);
  function int helper(int x); return x + 1; endfunction
  function int inner(int x); return helper(x); endfunction
  function int orphan(int x); return x; endfunction
  assign y = inner(a);
endmodule
module top(input int a, output int y);
  function int local_dead(int x); return x; endfunction
  function int used_by_top(int x); return x; endfunction
  wire int w = pkg::add(a, 1) + used_by_top(a);
  leaf u_leaf(.a(w), .y(y));
endmodule
""",
    })

    # ``used_by_top`` is reached only because ``top``, which nothing
    # instantiates, is a seed; ``helper`` and ``inner`` only through the
    # instantiation of ``leaf`` under it.
    assert dead == {"orphan@4", "local_dead@8"}
    assert summary["total_production_functions"] == 7
    assert summary["reachable_functions"] == 5
    assert summary["dead_candidates"] == 2
    # ``top`` and the two exported package functions.
    assert summary["seed_count"] == 3
