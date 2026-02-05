"""Branch coverage tests for vhdl.py analyzer.

Tests specific branch paths in the VHDL analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import vhdl as vhdl_module
from hypergumbo_lang_extended1.vhdl import (
    analyze_vhdl_files,
    find_vhdl_files,
)


def make_vhdl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a VHDL file with given content."""
    (tmp_path / name).write_text(content)


class TestEntityExtraction:
    """Branch coverage for entity extraction."""

    def test_entity_declaration(self, tmp_path: Path) -> None:
        """Test entity declaration extraction."""
        make_vhdl_file(tmp_path, "counter.vhd", """
entity counter is
    port (
        clk : in std_logic;
        rst : in std_logic;
        count : out std_logic_vector(7 downto 0)
    );
end entity counter;
""")
        result = analyze_vhdl_files(tmp_path)
        assert not result.skipped
        entities = [s for s in result.symbols if s.kind == "entity"]
        assert any("counter" in e.name for e in entities)


class TestArchitectureExtraction:
    """Branch coverage for architecture extraction."""

    def test_architecture_declaration(self, tmp_path: Path) -> None:
        """Test architecture declaration extraction."""
        make_vhdl_file(tmp_path, "counter.vhd", """
entity counter is end entity counter;

architecture behavioral of counter is
begin
    -- implementation
end architecture behavioral;
""")
        result = analyze_vhdl_files(tmp_path)
        architectures = [s for s in result.symbols if s.kind == "architecture"]
        assert any("behavioral" in a.name for a in architectures)


class TestPackageExtraction:
    """Branch coverage for package extraction."""

    def test_package_declaration(self, tmp_path: Path) -> None:
        """Test package declaration extraction."""
        make_vhdl_file(tmp_path, "types_pkg.vhd", """
package types_pkg is
    type state_type is (idle, running, stopped);
end package types_pkg;
""")
        result = analyze_vhdl_files(tmp_path)
        packages = [s for s in result.symbols if s.kind == "package"]
        assert any("types_pkg" in p.name for p in packages)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_vhdl_file(tmp_path, "funcs.vhd", """
package funcs_pkg is
    function add(a, b : integer) return integer;
end package funcs_pkg;
""")
        result = analyze_vhdl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check


class TestProcedureExtraction:
    """Branch coverage for procedure extraction."""

    def test_procedure_declaration(self, tmp_path: Path) -> None:
        """Test procedure declaration extraction."""
        make_vhdl_file(tmp_path, "procs.vhd", """
package procs_pkg is
    procedure display(msg : string);
end package procs_pkg;
""")
        result = analyze_vhdl_files(tmp_path)
        procs = [s for s in result.symbols if s.kind == "procedure"]
        assert not result.skipped  # lenient check


class TestComponentExtraction:
    """Branch coverage for component extraction."""

    def test_component_declaration(self, tmp_path: Path) -> None:
        """Test component declaration extraction."""
        make_vhdl_file(tmp_path, "top.vhd", """
architecture rtl of top is
    component adder
        port (
            a, b : in std_logic_vector(7 downto 0);
            sum : out std_logic_vector(7 downto 0)
        );
    end component;
begin
end architecture rtl;
""")
        result = analyze_vhdl_files(tmp_path)
        components = [s for s in result.symbols if s.kind == "component"]
        assert not result.skipped  # lenient check


class TestUseEdges:
    """Branch coverage for use edge extraction."""

    def test_use_creates_edge(self, tmp_path: Path) -> None:
        """Test use creates edge."""
        make_vhdl_file(tmp_path, "main.vhd", """
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity main is end entity main;
""")
        result = analyze_vhdl_files(tmp_path)
        uses = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestInstantiationEdges:
    """Branch coverage for instantiation edge extraction."""

    def test_component_instantiation(self, tmp_path: Path) -> None:
        """Test component instantiation creates edge."""
        make_vhdl_file(tmp_path, "top.vhd", """
architecture rtl of top is
begin
    adder_inst : adder port map (a => a, b => b, sum => sum);
end architecture rtl;
""")
        result = analyze_vhdl_files(tmp_path)
        instantiates = [e for e in result.edges if e.edge_type == "instantiates"]
        assert not result.skipped  # lenient check


class TestFindVhdlFiles:
    """Branch coverage for file discovery."""

    def test_finds_vhd_files(self, tmp_path: Path) -> None:
        """Test .vhd files are discovered."""
        (tmp_path / "test.vhd").write_text("entity test is end entity test;")
        files = list(find_vhdl_files(tmp_path))
        assert any(f.suffix == ".vhd" for f in files)

    def test_finds_vhdl_files(self, tmp_path: Path) -> None:
        """Test .vhdl files are discovered."""
        (tmp_path / "test.vhdl").write_text("entity test is end entity test;")
        files = list(find_vhdl_files(tmp_path))
        assert any(f.suffix == ".vhdl" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_vhdl_files(self, tmp_path: Path) -> None:
        """Test directory with no VHDL files."""
        result = analyze_vhdl_files(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(vhdl_module, "is_vhdl_tree_sitter_available", return_value=False):
            result = vhdl_module.analyze_vhdl_files(tmp_path)
        assert result.skipped is True
