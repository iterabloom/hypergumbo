"""Tests for the discovery module."""
from pathlib import Path

from hypergumbo_core.discovery import (
    DEFAULT_EXCLUDES,
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
