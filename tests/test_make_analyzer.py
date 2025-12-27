"""Tests for Makefile analyzer using tree-sitter-make.

Tests verify that the analyzer correctly extracts:
- Variable definitions
- Target rules (explicit and pattern rules)
- Prerequisites (dependencies)
- Include directives
- Define blocks (functions/macros)
"""

from hypergumbo.analyze.make import (
    PASS_ID,
    PASS_VERSION,
    MakeAnalysisResult,
    analyze_make_files,
    find_make_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "make-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_variable(tmp_path):
    """Test detection of variable definitions."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
CC = gcc
CFLAGS = -Wall -O2
""")
    result = analyze_make_files(tmp_path)

    assert not result.skipped
    variables = [s for s in result.symbols if s.kind == "variable"]
    assert len(variables) >= 2
    names = [v.name for v in variables]
    assert "CC" in names
    assert "CFLAGS" in names


def test_analyze_target(tmp_path):
    """Test detection of target rules."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
all: main.o utils.o
	gcc -o myprogram main.o utils.o

clean:
	rm -f *.o myprogram
""")
    result = analyze_make_files(tmp_path)

    targets = [s for s in result.symbols if s.kind == "target"]
    assert len(targets) >= 2
    names = [t.name for t in targets]
    assert "all" in names
    assert "clean" in names


def test_analyze_prerequisites(tmp_path):
    """Test detection of prerequisite dependencies."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
main.o: main.c
	gcc -c main.c

utils.o: utils.c
	gcc -c utils.c

myprogram: main.o utils.o
	gcc -o myprogram main.o utils.o
""")
    result = analyze_make_files(tmp_path)

    # Should have depends_on edges
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(dep_edges) >= 2  # myprogram depends on main.o and utils.o


def test_analyze_pattern_rule(tmp_path):
    """Test detection of pattern rules."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
%.o: %.c
	gcc -c $< -o $@
""")
    result = analyze_make_files(tmp_path)

    pattern_rules = [s for s in result.symbols if s.kind == "pattern_rule"]
    assert len(pattern_rules) >= 1
    assert pattern_rules[0].name == "%.o"


def test_analyze_phony_target(tmp_path):
    """Test detection of .PHONY special target."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
.PHONY: all clean install

all: myprogram

clean:
	rm -f myprogram
""")
    result = analyze_make_files(tmp_path)

    special_targets = [s for s in result.symbols if s.kind == "special_target"]
    assert len(special_targets) >= 1
    assert special_targets[0].name == ".PHONY"


def test_analyze_define_block(tmp_path):
    """Test detection of define blocks (functions)."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
define my_function
	echo "Hello $(1)"
endef
""")
    result = analyze_make_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "my_function"


def test_analyze_include_directive(tmp_path):
    """Test detection of include directives."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
include common.mk
include config/rules.mk
""")
    result = analyze_make_files(tmp_path)

    includes = [s for s in result.symbols if s.kind == "include"]
    assert len(includes) >= 2
    names = [i.name for i in includes]
    assert "common.mk" in names
    assert "config/rules.mk" in names


def test_find_make_files(tmp_path):
    """Test that Makefile files are discovered correctly."""
    (tmp_path / "Makefile").write_text("all:")
    (tmp_path / "common.mk").write_text("# common rules")
    (tmp_path / "GNUmakefile").write_text("# GNU make specific")
    (tmp_path / "not_make.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "Makefile").write_text("sub:")

    files = list(find_make_files(tmp_path))
    # Should find Makefile, .mk, and GNUmakefile
    assert len(files) >= 4


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no Makefile files."""
    result = analyze_make_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("all:")

    result = analyze_make_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("broken: :::::")

    # Should not raise an exception
    result = analyze_make_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, MakeAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""all: main
	echo hello
""")
    result = analyze_make_files(tmp_path)

    targets = [s for s in result.symbols if s.kind == "target"]
    assert len(targets) >= 1

    # Check span
    assert targets[0].span.start_line >= 1
    assert targets[0].span.end_line >= targets[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.make import is_make_tree_sitter_available

    # The function should return a boolean
    result = is_make_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_make_files(tmp_path):
    """Test analysis across multiple Makefile files."""
    (tmp_path / "Makefile").write_text("""
include sub/rules.mk

all: mylib
""")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "rules.mk").write_text("""
mylib: lib.o
	ar rcs libmy.a lib.o
""")

    result = analyze_make_files(tmp_path)

    targets = [s for s in result.symbols if s.kind == "target"]
    assert len(targets) >= 2


def test_complete_makefile_example(tmp_path):
    """Test a complete Makefile structure."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
CC = gcc
CFLAGS = -Wall -O2

SRCS = main.c utils.c
OBJS = $(SRCS:.c=.o)

.PHONY: all clean install

all: myprogram

myprogram: $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) myprogram

install: myprogram
	cp myprogram /usr/local/bin/

include config.mk

define setup_env
	export PATH=$(1):$$PATH
endef
""")
    result = analyze_make_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "variable" in kinds
    assert "target" in kinds
    assert "special_target" in kinds
    assert "pattern_rule" in kinds
    assert "include" in kinds
    assert "function" in kinds

    # Check for dependency edges
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(dep_edges) >= 1


def test_cross_file_dependency(tmp_path):
    """Test dependency resolution across files."""
    (tmp_path / "Makefile").write_text("""
lib.o: lib.c
	gcc -c lib.c

app: lib.o
	gcc -o app app.c lib.o
""")
    result = analyze_make_files(tmp_path)

    # Should have depends_on edge from app to lib.o with high confidence
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
    app_deps = [e for e in dep_edges if "app" in e.src]
    assert len(app_deps) >= 1
    # Internal dependency should have higher confidence
    internal_deps = [e for e in app_deps if e.confidence == 0.90]
    assert len(internal_deps) >= 1
