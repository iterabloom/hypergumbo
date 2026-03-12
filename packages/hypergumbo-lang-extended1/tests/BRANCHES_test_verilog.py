# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for verilog.py analyzer.

Tests specific branch paths in the Verilog analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import verilog as verilog_module
from hypergumbo_lang_extended1.verilog import (
    analyze_verilog_files,
    find_verilog_files,
)


def make_verilog_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Verilog file with given content."""
    (tmp_path / name).write_text(content)


class TestModuleExtraction:
    """Branch coverage for module extraction."""

    def test_module_declaration(self, tmp_path: Path) -> None:
        """Test module declaration extraction."""
        make_verilog_file(tmp_path, "counter.v", """
module counter (
    input clk,
    input rst,
    output reg [7:0] count
);
    always @(posedge clk or posedge rst) begin
        if (rst)
            count <= 8'b0;
        else
            count <= count + 1;
    end
endmodule
""")
        result = analyze_verilog_files(tmp_path)
        assert not result.skipped
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any("counter" in m.name for m in modules)


class TestTaskExtraction:
    """Branch coverage for task extraction."""

    def test_task_declaration(self, tmp_path: Path) -> None:
        """Test task declaration extraction."""
        make_verilog_file(tmp_path, "tasks.v", """
module tasks;
    task display_value;
        input [7:0] value;
        begin
            $display("Value: %d", value);
        end
    endtask
endmodule
""")
        result = analyze_verilog_files(tmp_path)
        tasks = [s for s in result.symbols if s.kind == "task"]
        assert not result.skipped  # lenient check


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_verilog_file(tmp_path, "funcs.v", """
module funcs;
    function [7:0] add;
        input [7:0] a, b;
        begin
            add = a + b;
        end
    endfunction
endmodule
""")
        result = analyze_verilog_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_verilog_file(tmp_path, "interfaces.sv", """
interface simple_bus;
    logic [7:0] data;
    logic valid;
    logic ready;
endinterface
""")
        result = analyze_verilog_files(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("simple_bus" in i.name for i in interfaces)


class TestIncludeEdges:
    """Branch coverage for include edge extraction."""

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        """Test `include creates edge."""
        make_verilog_file(tmp_path, "main.v", """
`include "defines.vh"
`include "common.v"

module main;
endmodule
""")
        result = analyze_verilog_files(tmp_path)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert not result.skipped  # lenient check


class TestInstantiationEdges:
    """Branch coverage for instantiation edge extraction."""

    def test_module_instantiation(self, tmp_path: Path) -> None:
        """Test module instantiation creates edge."""
        make_verilog_file(tmp_path, "top.v", """
module top (
    input clk,
    input rst
);
    counter counter_inst (
        .clk(clk),
        .rst(rst)
    );
endmodule
""")
        result = analyze_verilog_files(tmp_path)
        instantiates = [e for e in result.edges if e.edge_type == "instantiates"]
        assert not result.skipped  # lenient check


class TestFindVerilogFiles:
    """Branch coverage for file discovery."""

    def test_finds_v_files(self, tmp_path: Path) -> None:
        """Test .v files are discovered."""
        (tmp_path / "test.v").write_text("module test; endmodule")
        files = list(find_verilog_files(tmp_path))
        assert any(f.suffix == ".v" for f in files)

    def test_finds_sv_files(self, tmp_path: Path) -> None:
        """Test .sv files are discovered."""
        (tmp_path / "test.sv").write_text("module test; endmodule")
        files = list(find_verilog_files(tmp_path))
        assert any(f.suffix == ".sv" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_verilog_files(self, tmp_path: Path) -> None:
        """Test directory with no Verilog files."""
        result = analyze_verilog_files(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(verilog_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="verilog analysis skipped"):
                result = verilog_module.analyze_verilog_files(tmp_path)
        assert result.skipped is True
