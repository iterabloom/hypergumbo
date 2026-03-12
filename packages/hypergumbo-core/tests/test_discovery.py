# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the discovery module."""
from pathlib import Path

from hypergumbo_core.discovery import (
    DEFAULT_EXCLUDES,
    classify_dot_d_file,
    classify_dot_m_file,
    find_files,
    is_excluded,
)


def test_is_excluded_with_default_patterns(tmp_path: Path) -> None:
    """Should exclude paths matching default patterns."""
    node_modules = tmp_path / "node_modules" / "package" / "index.py"
    assert is_excluded(node_modules, tmp_path) is True


def test_is_excluded_returns_false_for_normal_paths(tmp_path: Path) -> None:
    """Should not exclude normal source paths."""
    src_file = tmp_path / "src" / "app.py"
    assert is_excluded(src_file, tmp_path) is False


def test_is_excluded_with_custom_patterns(tmp_path: Path) -> None:
    """Should respect custom exclude patterns."""
    third_party = tmp_path / "third_party" / "lib.py"
    # Not excluded by default
    assert is_excluded(third_party, tmp_path) is False
    # Excluded with custom pattern
    assert is_excluded(third_party, tmp_path, excludes=["third_party"]) is True


def test_is_excluded_with_path_outside_repo_root(tmp_path: Path) -> None:
    """Should handle paths that are not relative to repo_root."""
    # Create a path that's not under tmp_path
    outside_path = Path("/some/other/node_modules/file.py")
    # Should still work - checks path components
    assert is_excluded(outside_path, tmp_path) is True


def test_is_excluded_matches_glob_patterns(tmp_path: Path) -> None:
    """Should support glob patterns like *.egg-info."""
    egg_info = tmp_path / "mypackage.egg-info" / "PKG-INFO"
    assert is_excluded(egg_info, tmp_path) is True


def test_find_files_yields_matching_files(tmp_path: Path) -> None:
    """Should yield files matching the patterns."""
    py_file = tmp_path / "app.py"
    py_file.write_text("# python")
    txt_file = tmp_path / "readme.txt"
    txt_file.write_text("text")

    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 1
    assert results[0] == py_file


def test_find_files_excludes_by_default(tmp_path: Path) -> None:
    """Should exclude files in default excluded directories."""
    good_file = tmp_path / "src" / "app.py"
    good_file.parent.mkdir()
    good_file.write_text("# good")

    bad_file = tmp_path / "node_modules" / "pkg" / "index.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("# bad")

    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 1
    assert results[0] == good_file


def test_public_directory_not_excluded(tmp_path: Path) -> None:
    """public/ directories should NOT be excluded by default.

    Hugo uses public/ for generated output, but it's typically gitignored.
    Excluding it by default causes false positives: Airflow's routes/public/
    (109 API handlers), Laravel/Symfony public/ (web root), and other
    frameworks use public/ for production code.
    """
    # Nested public/ (e.g., Airflow routes/public/)
    nested = tmp_path / "routes" / "public" / "dags.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("# API route handler")
    assert is_excluded(nested, tmp_path) is False

    # Root-level public/ (e.g., Laravel web root)
    root_level = tmp_path / "public" / "index.php"
    root_level.parent.mkdir(parents=True)
    root_level.write_text("<?php // web root")
    assert is_excluded(root_level, tmp_path) is False

    # Verify files are actually found by find_files
    results = list(find_files(tmp_path, ["*.py"]))
    assert any("public" in str(p) for p in results)


def test_default_excludes_contains_expected_patterns() -> None:
    """DEFAULT_EXCLUDES should contain all expected patterns."""
    expected = [
        "node_modules",
        "vendor",  # PHP Composer dependencies
        "venv",
        ".venv",
        "dist",
        "build",
        ".git",
        "__pycache__",
        ".hypergumbo",  # Hypergumbo output directory
        "hypergumbo.results*.json",  # Hypergumbo behavior map (including budget files)
        # Lock files - generated, inflate LOC counts
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
    ]
    for pattern in expected:
        assert pattern in DEFAULT_EXCLUDES


def test_is_excluded_lock_files(tmp_path: Path) -> None:
    """Lock files should be excluded to prevent inflated LOC counts."""
    lock_files = [
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
        "Gemfile.lock",
    ]
    for lock_file in lock_files:
        lock_path = tmp_path / lock_file
        assert is_excluded(lock_path, tmp_path) is True, f"{lock_file} should be excluded"


def test_is_excluded_hypergumbo_artifacts(tmp_path: Path) -> None:
    """Should exclude hypergumbo output artifacts by default.

    The .hypergumbo directory and hypergumbo.results*.json files are generated
    by hypergumbo run and should not pollute sketch or analysis input.
    """
    # .hypergumbo directory
    capsule_file = tmp_path / ".hypergumbo" / "capsule.json"
    assert is_excluded(capsule_file, tmp_path) is True

    # hypergumbo.results.json at repo root
    results_file = tmp_path / "hypergumbo.results.json"
    assert is_excluded(results_file, tmp_path) is True

    # Budget-tiered output files (4k, 16k, 64k, etc.)
    budget_files = [
        "hypergumbo.results.4k.json",
        "hypergumbo.results.16k.json",
        "hypergumbo.results.64k.json",
        "hypergumbo.results.2k.json",  # Custom budget
        "hypergumbo.results.128k.json",  # Custom budget
    ]
    for budget_file in budget_files:
        budget_path = tmp_path / budget_file
        assert is_excluded(budget_path, tmp_path) is True, f"{budget_file} should be excluded"

    # hypergumbo.results.json in subdirectory (less common but should still match)
    nested_results = tmp_path / "subdir" / "hypergumbo.results.json"
    assert is_excluded(nested_results, tmp_path) is True

    # Budget file in subdirectory should also be excluded
    nested_budget = tmp_path / "output" / "hypergumbo.results.4k.json"
    assert is_excluded(nested_budget, tmp_path) is True


def test_find_files_respects_max_files(tmp_path: Path) -> None:
    """Should limit the number of files returned when max_files is set."""
    # Create 5 Python files
    for i in range(5):
        (tmp_path / f"file{i}.py").write_text(f"# file {i}")

    # Without limit, should find all 5
    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 5

    # With limit of 3, should only return 3
    results = list(find_files(tmp_path, ["*.py"], max_files=3))
    assert len(results) == 3

    # With limit of 0, should return none
    results = list(find_files(tmp_path, ["*.py"], max_files=0))
    assert len(results) == 0

    # With limit higher than available, should return all
    results = list(find_files(tmp_path, ["*.py"], max_files=100))
    assert len(results) == 5


def test_find_files_skips_directories(tmp_path: Path) -> None:
    """Directories matching a pattern should not be yielded.

    Path.rglob("*.d") matches both files and directories. Git repos
    like forgejo have ``hooks/pre-receive.d/`` directories — these
    should not be counted as D language source files.
    """
    # Create a directory ending in .d (like git hook dirs)
    (tmp_path / "hooks" / "pre-receive.d").mkdir(parents=True)
    (tmp_path / "hooks" / "post-receive.d").mkdir(parents=True)

    # Create a real .d file
    (tmp_path / "hello.d").write_text("import std.stdio;")

    results = list(find_files(tmp_path, ["*.d"]))
    assert len(results) == 1
    assert results[0].name == "hello.d"


# ---------- classify_dot_m_file tests ----------


class TestClassifyDotMFile:
    """Tests for .m file disambiguation between Objective-C, MATLAB, and Wolfram."""

    def test_objc_interface(self, tmp_path: Path) -> None:
        """Files with @interface are Objective-C."""
        f = tmp_path / "AppDelegate.m"
        f.write_text('#import "AppDelegate.h"\n@implementation AppDelegate\n@end\n')
        assert classify_dot_m_file(f) == "objc"

    def test_objc_import(self, tmp_path: Path) -> None:
        """Files with #import are Objective-C."""
        f = tmp_path / "main.m"
        f.write_text('#import <Foundation/Foundation.h>\nint main() { return 0; }\n')
        assert classify_dot_m_file(f) == "objc"

    def test_objc_protocol(self, tmp_path: Path) -> None:
        """Files with @protocol are Objective-C."""
        f = tmp_path / "proto.m"
        f.write_text("@protocol MyProtocol\n- (void)doSomething;\n@end\n")
        assert classify_dot_m_file(f) == "objc"

    def test_objc_message_send(self, tmp_path: Path) -> None:
        """Files with [obj message] syntax are Objective-C."""
        f = tmp_path / "test.m"
        f.write_text(
            "#include <stdio.h>\n"
            "@implementation Foo\n"
            "- (void)bar { [self doThing]; }\n"
            "@end\n"
        )
        assert classify_dot_m_file(f) == "objc"

    def test_wolfram_set_delayed(self, tmp_path: Path) -> None:
        """Files with f[x_] := pattern are Wolfram."""
        f = tmp_path / "math.m"
        f.write_text("double[x_] := 2 * x\ntriple[x_] := 3 * x\n")
        assert classify_dot_m_file(f) == "wolfram"

    def test_wolfram_module(self, tmp_path: Path) -> None:
        """Files with Module[ scoping are Wolfram."""
        f = tmp_path / "util.m"
        f.write_text("f[x_] := Module[{y}, y = x + 1; y]\n")
        assert classify_dot_m_file(f) == "wolfram"

    def test_wolfram_begin_package(self, tmp_path: Path) -> None:
        """Files with BeginPackage are Wolfram."""
        f = tmp_path / "pkg.m"
        f.write_text('BeginPackage["MyPackage`"]\nf::usage = "f[x] does stuff"\nEndPackage[]\n')
        assert classify_dot_m_file(f) == "wolfram"

    def test_wolfram_needs(self, tmp_path: Path) -> None:
        """Files with Needs[...] imports are Wolfram."""
        f = tmp_path / "init.m"
        f.write_text('Needs["SomePackage`"]\nresult = SomeFunction[42]\n')
        assert classify_dot_m_file(f) == "wolfram"

    def test_wolfram_block_comment(self, tmp_path: Path) -> None:
        """Files with (* ... *) block comments and Wolfram syntax are Wolfram."""
        f = tmp_path / "calc.m"
        f.write_text("(* Helper function *)\nf[x_] := x^2\n")
        assert classify_dot_m_file(f) == "wolfram"

    def test_matlab_function(self, tmp_path: Path) -> None:
        """Files with 'function' keyword are MATLAB."""
        f = tmp_path / "myfunc.m"
        f.write_text("function result = myfunc(x)\n    result = x * 2;\nend\n")
        assert classify_dot_m_file(f) == "matlab"

    def test_matlab_classdef(self, tmp_path: Path) -> None:
        """Files with 'classdef' keyword are MATLAB."""
        f = tmp_path / "MyClass.m"
        f.write_text(
            "classdef MyClass\n"
            "    properties\n"
            "        Value\n"
            "    end\n"
            "    methods\n"
            "        function obj = MyClass(v)\n"
            "            obj.Value = v;\n"
            "        end\n"
            "    end\n"
            "end\n"
        )
        assert classify_dot_m_file(f) == "matlab"

    def test_matlab_percent_comment(self, tmp_path: Path) -> None:
        """Files with % comments and MATLAB syntax are MATLAB."""
        f = tmp_path / "script.m"
        f.write_text("% This is a MATLAB script\nx = linspace(0, 1, 100);\ny = sin(x);\nplot(x, y);\n")
        assert classify_dot_m_file(f) == "matlab"

    def test_matlab_script_with_semicolons(self, tmp_path: Path) -> None:
        """MATLAB scripts with semicolons and assignments default to MATLAB."""
        f = tmp_path / "run.m"
        f.write_text("% Run simulation\ndt = 0.01;\nT = 10;\nfor i = 1:T/dt\n    x = x + dt;\nend\n")
        assert classify_dot_m_file(f) == "matlab"

    def test_empty_file_defaults_to_matlab(self, tmp_path: Path) -> None:
        """Empty .m files default to MATLAB (most common use of .m)."""
        f = tmp_path / "empty.m"
        f.write_text("")
        assert classify_dot_m_file(f) == "matlab"

    def test_unreadable_file_defaults_to_matlab(self, tmp_path: Path) -> None:
        """Binary/unreadable .m files default to MATLAB."""
        f = tmp_path / "binary.m"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        assert classify_dot_m_file(f) == "matlab"

    def test_nonexistent_file_defaults_to_matlab(self, tmp_path: Path) -> None:
        """Nonexistent files default to MATLAB."""
        f = tmp_path / "nofile.m"
        assert classify_dot_m_file(f) == "matlab"

    def test_wolfram_underscore_pattern_args(self, tmp_path: Path) -> None:
        """Wolfram pattern-match arguments like x_ are a strong signal."""
        f = tmp_path / "patterns.m"
        f.write_text("f[x_, y_] := x + y\ng[n_Integer] := n!\n")
        assert classify_dot_m_file(f) == "wolfram"

    def test_objc_pragma_mark(self, tmp_path: Path) -> None:
        """#pragma mark is Objective-C."""
        f = tmp_path / "vc.m"
        f.write_text(
            '#import "ViewController.h"\n'
            "#pragma mark - Lifecycle\n"
            "@implementation ViewController\n"
            "@end\n"
        )
        assert classify_dot_m_file(f) == "objc"


# ---------- classify_dot_d_file tests ----------


class TestClassifyDotDFile:
    """Content-based classification for .d files.

    .d files are ambiguous: they can be D programming language source files
    or GCC Makefile dependency files generated by ``gcc -MMD``.  Dependency
    files contain make-style ``target: prerequisites`` rules and should not
    be analyzed as D source.
    """

    def test_d_source_module_declaration(self, tmp_path: Path) -> None:
        """D source files with module declarations are classified as 'd'."""
        f = tmp_path / "app.d"
        f.write_text("module app;\n\nvoid main() {\n    writeln(\"hello\");\n}\n")
        assert classify_dot_d_file(f) == "d"

    def test_d_source_import(self, tmp_path: Path) -> None:
        """D source files with import statements are classified as 'd'."""
        f = tmp_path / "util.d"
        f.write_text("import std.stdio;\nimport std.conv;\n\nvoid run() {}\n")
        assert classify_dot_d_file(f) == "d"

    def test_d_source_class(self, tmp_path: Path) -> None:
        """D source files with class definitions are classified as 'd'."""
        f = tmp_path / "model.d"
        f.write_text("class Widget {\n    int x;\n    void update() {}\n}\n")
        assert classify_dot_d_file(f) == "d"

    def test_d_source_struct(self, tmp_path: Path) -> None:
        """D source files with struct definitions are classified as 'd'."""
        f = tmp_path / "data.d"
        f.write_text("struct Point {\n    float x, y;\n}\n")
        assert classify_dot_d_file(f) == "d"

    def test_d_source_unittest(self, tmp_path: Path) -> None:
        """D source files with unittest blocks are classified as 'd'."""
        f = tmp_path / "test.d"
        f.write_text("unittest {\n    assert(1 + 1 == 2);\n}\n")
        assert classify_dot_d_file(f) == "d"

    def test_d_source_attributes(self, tmp_path: Path) -> None:
        """D source files with @safe/@trusted are classified as 'd'."""
        f = tmp_path / "safe.d"
        f.write_text("@safe void process() {}\n@trusted void sys() {}\n")
        assert classify_dot_d_file(f) == "d"

    def test_gcc_dependency_simple(self, tmp_path: Path) -> None:
        """GCC dependency files with target: deps are classified as 'dependency'."""
        f = tmp_path / "main.d"
        f.write_text("main.o: main.c main.h utils.h\n")
        assert classify_dot_d_file(f) == "dependency"

    def test_gcc_dependency_multiline(self, tmp_path: Path) -> None:
        """GCC dependency files with backslash continuations."""
        f = tmp_path / "render.d"
        f.write_text(
            "render.o: render.c render.h \\\n"
            "  /usr/include/stdio.h \\\n"
            "  /usr/include/stdlib.h\n"
        )
        assert classify_dot_d_file(f) == "dependency"

    def test_gcc_dependency_absolute_paths(self, tmp_path: Path) -> None:
        """GCC dependency files with absolute paths in prerequisites."""
        f = tmp_path / "obj.d"
        f.write_text(
            "build/obj/parser.o: src/parser.c src/parser.h "
            "/usr/local/include/tree_sitter/api.h\n"
        )
        assert classify_dot_d_file(f) == "dependency"

    def test_empty_file_defaults_to_d(self, tmp_path: Path) -> None:
        """Empty .d files default to 'd' (benefit of the doubt)."""
        f = tmp_path / "empty.d"
        f.write_text("")
        assert classify_dot_d_file(f) == "d"

    def test_nonexistent_file_defaults_to_d(self, tmp_path: Path) -> None:
        """Nonexistent files default to 'd'."""
        f = tmp_path / "nofile.d"
        assert classify_dot_d_file(f) == "d"

    def test_di_interface_file(self, tmp_path: Path) -> None:
        """D interface files (.di) are always 'd' — no ambiguity."""
        f = tmp_path / "api.di"
        f.write_text("module api;\nvoid process();\n")
        assert classify_dot_d_file(f) == "d"


class TestFindFilesMaxFileBytes:
    """Tests for find_files max_file_bytes parameter."""

    def test_skips_large_files(self, tmp_path: Path) -> None:
        """Files exceeding max_file_bytes are excluded."""
        small = tmp_path / "small.py"
        small.write_text("x = 1")
        large = tmp_path / "large.py"
        large.write_text("x" * 1000)

        result = list(find_files(tmp_path, ["*.py"], max_file_bytes=500))
        names = {p.name for p in result}
        assert "small.py" in names
        assert "large.py" not in names

    def test_no_limit_includes_all(self, tmp_path: Path) -> None:
        """Without max_file_bytes, all files are included."""
        for name in ("a.py", "b.py"):
            (tmp_path / name).write_text("x" * 1000)

        result = list(find_files(tmp_path, ["*.py"], max_file_bytes=None))
        assert len(result) == 2

    def test_callback_called_for_skipped(self, tmp_path: Path) -> None:
        """on_file_skipped callback fires for oversized files."""
        large = tmp_path / "big.py"
        large.write_text("x" * 2000)

        skipped: list[tuple] = []

        def on_skip(path, size, reason):
            skipped.append((path.name, size, reason))

        list(find_files(
            tmp_path, ["*.py"],
            max_file_bytes=100,
            on_file_skipped=on_skip,
        ))

        assert len(skipped) == 1
        assert skipped[0][0] == "big.py"
        assert skipped[0][1] > 100
        assert "exceeds" in skipped[0][2]

    def test_global_limit_respected(self, tmp_path: Path) -> None:
        """Global set_max_file_bytes is used when no explicit value given."""
        from hypergumbo_core.discovery import set_max_file_bytes

        large = tmp_path / "huge.py"
        large.write_text("x" * 5000)
        small = tmp_path / "tiny.py"
        small.write_text("x = 1")

        try:
            set_max_file_bytes(100)
            result = list(find_files(tmp_path, ["*.py"]))
            names = {p.name for p in result}
            assert "tiny.py" in names
            assert "huge.py" not in names
        finally:
            set_max_file_bytes(None)

    def test_explicit_overrides_global(self, tmp_path: Path) -> None:
        """Explicit max_file_bytes overrides global setting."""
        from hypergumbo_core.discovery import set_max_file_bytes

        f = tmp_path / "medium.py"
        f.write_text("x" * 500)

        try:
            set_max_file_bytes(100)  # Would skip
            # But explicit None means no limit
            result = list(find_files(
                tmp_path, ["*.py"], max_file_bytes=10000,
            ))
            assert len(result) == 1
        finally:
            set_max_file_bytes(None)

    def test_global_on_file_skipped_callback(self, tmp_path: Path) -> None:
        """Global on_file_skipped callback fires when no explicit callback."""
        from hypergumbo_core.discovery import (
            set_global_on_file_skipped,
            set_max_file_bytes,
        )

        large = tmp_path / "big.py"
        large.write_text("x" * 2000)
        small = tmp_path / "ok.py"
        small.write_text("x = 1")

        skipped: list[tuple] = []

        def on_skip(path: Path, size: int, reason: str) -> None:
            skipped.append((str(path.name), size, reason))

        try:
            set_max_file_bytes(100)
            set_global_on_file_skipped(on_skip)
            result = list(find_files(tmp_path, ["*.py"]))
            names = {p.name for p in result}
            assert "ok.py" in names
            assert "big.py" not in names
            assert len(skipped) == 1
            assert skipped[0][0] == "big.py"
            assert "exceeds" in skipped[0][2]
        finally:
            set_max_file_bytes(None)
            set_global_on_file_skipped(None)

    def test_explicit_callback_overrides_global(self, tmp_path: Path) -> None:
        """Explicit on_file_skipped overrides the global callback."""
        from hypergumbo_core.discovery import (
            set_global_on_file_skipped,
            set_max_file_bytes,
        )

        large = tmp_path / "big.py"
        large.write_text("x" * 2000)

        global_skipped: list[str] = []
        explicit_skipped: list[str] = []

        try:
            set_max_file_bytes(100)
            set_global_on_file_skipped(
                lambda p, s, r: global_skipped.append(str(p.name))
            )
            list(find_files(
                tmp_path, ["*.py"],
                on_file_skipped=lambda p, s, r: explicit_skipped.append(str(p.name)),
            ))
            assert len(explicit_skipped) == 1
            assert len(global_skipped) == 0
        finally:
            set_max_file_bytes(None)
            set_global_on_file_skipped(None)
