"""Tests for VHDL analyzer using tree-sitter-vhdl.

Tests verify that the analyzer correctly extracts:
- Entity declarations
- Architecture definitions
- Package declarations
- Component declarations
- Architecture-entity relationships
"""

from hypergumbo.analyze.vhdl import (
    PASS_ID,
    PASS_VERSION,
    VHDLAnalysisResult,
    analyze_vhdl_files,
    find_vhdl_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "vhdl-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_entity(tmp_path):
    """Test detection of entity declaration."""
    vhdl_file = tmp_path / "counter.vhd"
    vhdl_file.write_text("""
library ieee;
use ieee.std_logic_1164.all;

entity counter is
    port (
        clk   : in  std_logic;
        reset : in  std_logic;
        count : out std_logic_vector(7 downto 0)
    );
end entity counter;
""")
    result = analyze_vhdl_files(tmp_path)

    assert not result.skipped
    entities = [s for s in result.symbols if s.kind == "entity"]
    assert len(entities) >= 1
    assert entities[0].name == "counter"
    assert entities[0].language == "vhdl"


def test_analyze_architecture(tmp_path):
    """Test detection of architecture definition."""
    vhdl_file = tmp_path / "counter.vhd"
    vhdl_file.write_text("""
entity counter is
end entity counter;

architecture rtl of counter is
begin
end architecture rtl;
""")
    result = analyze_vhdl_files(tmp_path)

    architectures = [s for s in result.symbols if s.kind == "architecture"]
    assert len(architectures) >= 1
    assert architectures[0].name == "rtl"

    # Should have implements edge
    impl_edges = [e for e in result.edges if e.edge_type == "implements"]
    assert len(impl_edges) >= 1


def test_analyze_package(tmp_path):
    """Test detection of package declaration."""
    vhdl_file = tmp_path / "my_pkg.vhd"
    vhdl_file.write_text("""
package my_package is
    type state_t is (IDLE, RUNNING, DONE);
end package;
""")
    result = analyze_vhdl_files(tmp_path)

    packages = [s for s in result.symbols if s.kind == "package"]
    assert len(packages) >= 1
    assert packages[0].name == "my_package"


def test_architecture_implements_entity(tmp_path):
    """Test that architecture creates implements edge to entity."""
    vhdl_file = tmp_path / "test.vhd"
    vhdl_file.write_text("""
entity my_entity is
end entity my_entity;

architecture behavioral of my_entity is
begin
end architecture behavioral;
""")
    result = analyze_vhdl_files(tmp_path)

    # Should have implements edge with high confidence
    impl_edges = [e for e in result.edges if e.edge_type == "implements"]
    assert len(impl_edges) >= 1
    assert impl_edges[0].confidence == 0.90  # Internal reference


def test_architecture_external_entity(tmp_path):
    """Test architecture referencing external entity."""
    vhdl_file = tmp_path / "arch.vhd"
    vhdl_file.write_text("""
architecture rtl of external_entity is
begin
end architecture rtl;
""")
    result = analyze_vhdl_files(tmp_path)

    # Should have implements edge with lower confidence
    impl_edges = [e for e in result.edges if e.edge_type == "implements"]
    assert len(impl_edges) >= 1
    assert impl_edges[0].confidence == 0.70  # External reference


def test_find_vhdl_files(tmp_path):
    """Test that VHDL files are discovered correctly."""
    (tmp_path / "counter.vhd").write_text("entity counter is end;")
    (tmp_path / "alu.vhdl").write_text("entity alu is end;")
    (tmp_path / "not_vhdl.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "sub.vhd").write_text("entity sub is end;")

    files = list(find_vhdl_files(tmp_path))
    # Should find .vhd and .vhdl files
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no VHDL files."""
    result = analyze_vhdl_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    vhdl_file = tmp_path / "test.vhd"
    vhdl_file.write_text("entity test is end;")

    result = analyze_vhdl_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    vhdl_file = tmp_path / "broken.vhd"
    vhdl_file.write_text("entity broken syntax {{{{")

    # Should not raise an exception
    result = analyze_vhdl_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, VHDLAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    vhdl_file = tmp_path / "span.vhd"
    vhdl_file.write_text("""entity test_entity is
end entity;
""")
    result = analyze_vhdl_files(tmp_path)

    entities = [s for s in result.symbols if s.kind == "entity"]
    assert len(entities) >= 1

    # Check span
    assert entities[0].span.start_line >= 1
    assert entities[0].span.end_line >= entities[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.vhdl import is_vhdl_tree_sitter_available

    # The function should return a boolean
    result = is_vhdl_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_vhdl_files(tmp_path):
    """Test analysis across multiple VHDL files."""
    (tmp_path / "entity.vhd").write_text("""
entity my_ent is
end entity my_ent;
""")
    (tmp_path / "arch.vhd").write_text("""
architecture rtl of my_ent is
begin
end architecture rtl;
""")

    result = analyze_vhdl_files(tmp_path)

    entities = [s for s in result.symbols if s.kind == "entity"]
    architectures = [s for s in result.symbols if s.kind == "architecture"]
    assert len(entities) >= 1
    assert len(architectures) >= 1


def test_complete_vhdl_example(tmp_path):
    """Test a complete VHDL design structure."""
    vhdl_file = tmp_path / "complete.vhd"
    vhdl_file.write_text("""
library ieee;
use ieee.std_logic_1164.all;

package types_pkg is
    type state_t is (IDLE, RUN, DONE);
end package;

entity fsm is
    port (
        clk   : in  std_logic;
        reset : in  std_logic;
        state : out std_logic_vector(1 downto 0)
    );
end entity fsm;

architecture rtl of fsm is
    signal current_state : state_t;
begin
    process(clk)
    begin
        if rising_edge(clk) then
            if reset = '1' then
                current_state <= IDLE;
            end if;
        end if;
    end process;
end architecture rtl;
""")
    result = analyze_vhdl_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "package" in kinds
    assert "entity" in kinds
    assert "architecture" in kinds

    # Check for implements edge
    impl_edges = [e for e in result.edges if e.edge_type == "implements"]
    assert len(impl_edges) >= 1
