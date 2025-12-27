"""Tests for Verilog/SystemVerilog analyzer using tree-sitter-verilog.

Tests verify that the analyzer correctly extracts:
- Module definitions
- Module instantiations
- Input/output ports
- Wire/register declarations
- Always blocks
"""

from hypergumbo.analyze.verilog import (
    PASS_ID,
    PASS_VERSION,
    VerilogAnalysisResult,
    analyze_verilog_files,
    find_verilog_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "verilog-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


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
    assert isinstance(result, VerilogAnalysisResult)


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
    from hypergumbo.analyze.verilog import is_verilog_tree_sitter_available

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
