"""Branch coverage tests for make.py analyzer.

Tests specific branch paths in the Make analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Variable extraction
- Target extraction
- Pattern rule extraction
- Prerequisite extraction and edges
- Include directive extraction
- Define blocks
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_mainstream.make import _make_edge_id, analyze_make_files, find_make_files

def make_makefile(tmp_path: Path, name: str, content: str) -> None:
    """Create a Makefile with given content."""
    (tmp_path / name).write_text(content)

class TestMakeHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("make", "Makefile", 1, 5, "build", "target")
        assert symbol_id == "make:Makefile:1-5:build:target"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "depends_on")
        edge_id2 = _make_edge_id("src1", "dst1", "depends_on")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")

class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_simple_variable(self, tmp_path: Path) -> None:
        """Test simple variable extraction."""
        make_makefile(tmp_path, "Makefile", """CC = gcc
CFLAGS = -Wall -O2
""")
        result = analyze_make_files(tmp_path)
        assert not result.skipped

        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2
        assert any(v.name == "CC" for v in variables)
        assert any(v.name == "CFLAGS" for v in variables)

    def test_variable_dedup(self, tmp_path: Path) -> None:
        """Test duplicate variable definitions are deduped."""
        make_makefile(tmp_path, "Makefile", """CFLAGS := -Wall
CFLAGS += -O2
""")
        result = analyze_make_files(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable" and s.name == "CFLAGS"]

        # Should only have one CFLAGS entry
        assert len(variables) == 1

class TestTargetExtraction:
    """Branch coverage for target extraction."""

    def test_simple_target(self, tmp_path: Path) -> None:
        """Test simple target extraction."""
        make_makefile(tmp_path, "Makefile", """all:
	echo "Building all"

build:
	echo "Building"
""")
        result = analyze_make_files(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]

        assert len(targets) >= 2
        assert any(t.name == "all" for t in targets)
        assert any(t.name == "build" for t in targets)

    def test_target_with_prerequisites(self, tmp_path: Path) -> None:
        """Test target with prerequisites."""
        make_makefile(tmp_path, "Makefile", """all: build test

build:
	echo "Building"

test:
	echo "Testing"
""")
        result = analyze_make_files(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]

        assert len(targets) >= 3

        # Should have dependency edges
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(dep_edges) >= 2

    def test_special_target(self, tmp_path: Path) -> None:
        """Test special target like .PHONY."""
        make_makefile(tmp_path, "Makefile", """.PHONY: all build test

all: build
	echo "All"

build:
	echo "Build"
""")
        result = analyze_make_files(tmp_path)
        special_targets = [s for s in result.symbols if s.kind == "special_target"]

        assert len(special_targets) >= 1
        assert any(".PHONY" in t.name for t in special_targets)

class TestPatternRuleExtraction:
    """Branch coverage for pattern rule extraction."""

    def test_pattern_rule(self, tmp_path: Path) -> None:
        """Test pattern rule extraction."""
        make_makefile(tmp_path, "Makefile", """%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@
""")
        result = analyze_make_files(tmp_path)
        pattern_rules = [s for s in result.symbols if s.kind == "pattern_rule"]

        assert len(pattern_rules) >= 1
        assert any("%.o" in p.name for p in pattern_rules)

class TestPrerequisiteEdges:
    """Branch coverage for prerequisite edge extraction."""

    def test_resolved_prerequisite(self, tmp_path: Path) -> None:
        """Test resolved prerequisite creates edge."""
        # Note: Makefile requires actual tab characters for recipe lines
        make_makefile(tmp_path, "Makefile", "all: build\n\nbuild:\n\techo \"Building\"\n")
        result = analyze_make_files(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]

        assert len(dep_edges) >= 1
        # Confidence is 0.90 for resolved, 0.70 for unresolved
        assert any(e.confidence >= 0.70 for e in dep_edges)

    def test_unresolved_prerequisite(self, tmp_path: Path) -> None:
        """Test unresolved prerequisite creates edge with lower confidence."""
        make_makefile(tmp_path, "Makefile", """all: external_target
	echo "Building"
""")
        result = analyze_make_files(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]

        assert len(dep_edges) >= 1
        # Should have lower confidence for unresolved targets
        assert any(e.confidence <= 0.75 for e in dep_edges)

class TestIncludeDirective:
    """Branch coverage for include directive extraction."""

    def test_include_directive(self, tmp_path: Path) -> None:
        """Test include directive extraction."""
        make_makefile(tmp_path, "Makefile", """include config.mk
include $(wildcard *.d)

all:
	echo "Building"
""")
        result = analyze_make_files(tmp_path)
        includes = [s for s in result.symbols if s.kind == "include"]

        assert len(includes) >= 1

class TestDefineDirective:
    """Branch coverage for define directive extraction."""

    def test_define_function(self, tmp_path: Path) -> None:
        """Test define directive (macro/function) extraction."""
        make_makefile(tmp_path, "Makefile", """define compile_rule
$(CC) $(CFLAGS) -c $< -o $@
endef

all:
	echo "Building"
""")
        result = analyze_make_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        assert any(f.name == "compile_rule" for f in functions)

class TestFindMakeFiles:
    """Branch coverage for file discovery."""

    def test_finds_makefile(self, tmp_path: Path) -> None:
        """Test Makefile is discovered."""
        (tmp_path / "Makefile").write_text("all:\n\techo hello")

        files = list(find_make_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "Makefile" for f in files)

    def test_finds_makefile_lowercase(self, tmp_path: Path) -> None:
        """Test makefile (lowercase) is discovered."""
        (tmp_path / "makefile").write_text("all:\n\techo hello")

        files = list(find_make_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "makefile" for f in files)

    def test_finds_mk_files(self, tmp_path: Path) -> None:
        """Test .mk files are discovered."""
        (tmp_path / "config.mk").write_text("CC = gcc")

        files = list(find_make_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".mk" for f in files)

    def test_finds_gnumakefile(self, tmp_path: Path) -> None:
        """Test GNUmakefile is discovered."""
        (tmp_path / "GNUmakefile").write_text("all:\n\techo hello")

        files = list(find_make_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "GNUmakefile" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_make_files(self, tmp_path: Path) -> None:
        """Test directory with no Makefiles."""
        result = analyze_make_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_makefile(self, tmp_path: Path) -> None:
        """Test minimal Makefile."""
        make_makefile(tmp_path, "Makefile", """all:
	echo "hello"
""")
        result = analyze_make_files(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_makefile(tmp_path, "Makefile", """all:
	echo "hello"
""")
        result = analyze_make_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestComplexMakefile:
    """Branch coverage for complex Makefiles."""

    def test_comprehensive_makefile(self, tmp_path: Path) -> None:
        """Test comprehensive Makefile."""
        make_makefile(tmp_path, "Makefile", """# Compiler settings
CC = gcc
CFLAGS = -Wall -O2

# Directories
SRC_DIR = src
BUILD_DIR = build

# Pattern rules
%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

# Targets
.PHONY: all clean test

all: build test

build: $(BUILD_DIR)/main

$(BUILD_DIR)/main: $(SRC_DIR)/main.o $(SRC_DIR)/utils.o
	$(CC) $^ -o $@

test: build
	./$(BUILD_DIR)/main --test

clean:
	rm -rf $(BUILD_DIR)

# Include dependencies
-include $(wildcard $(BUILD_DIR)/*.d)
""")
        result = analyze_make_files(tmp_path)

        # Should have variables
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 4

        # Should have targets
        targets = [s for s in result.symbols if s.kind in ("target", "special_target", "pattern_rule")]
        assert len(targets) >= 5

        # Should have dependency edges
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(dep_edges) >= 2
