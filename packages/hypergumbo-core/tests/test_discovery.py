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


def test_is_excluded_normalizes_trailing_slash(tmp_path: Path) -> None:
    """`-e ui/` (trailing slash) should match the same paths as `-e ui`.

    Per WI-zirik (UAT 2026-04-13 BUG-01): user-supplied exclude patterns
    with a trailing slash were silently ignored because per-part fnmatch
    never matches a part containing '/'.
    """
    ui_file = tmp_path / "ui" / "app.tsx"
    assert is_excluded(ui_file, tmp_path, excludes=["ui/"]) is True


def test_is_excluded_normalizes_glob_double_star_suffix(tmp_path: Path) -> None:
    """`-e ui/**` (gitignore-style) should match files under ui/.

    Per WI-zirik: this was silently ignored because per-part fnmatch
    cannot match 'ui/**' against any single path component.
    """
    ui_file = tmp_path / "ui" / "app" / "src" / "main.elm"
    assert is_excluded(ui_file, tmp_path, excludes=["ui/**"]) is True


def test_is_excluded_normalizes_glob_double_star_prefix(tmp_path: Path) -> None:
    """`-e **/ui/**` should match a `ui` directory at any depth.

    Per WI-zirik: silently ignored due to the same per-part fnmatch issue.
    """
    nested = tmp_path / "src" / "ui" / "app.tsx"
    assert is_excluded(nested, tmp_path, excludes=["**/ui/**"]) is True
    deep = tmp_path / "a" / "b" / "ui" / "c" / "d.tsx"
    assert is_excluded(deep, tmp_path, excludes=["**/ui/**"]) is True


def test_is_excluded_normalizes_glob_double_star_prefix_only(tmp_path: Path) -> None:
    """`-e **/ui` should match a directory named `ui` at any depth."""
    nested = tmp_path / "src" / "ui" / "app.tsx"
    assert is_excluded(nested, tmp_path, excludes=["**/ui"]) is True


def test_is_excluded_path_aware_glob(tmp_path: Path) -> None:
    """Patterns containing '/' (after normalization) match relative path.

    `-e cmd/server.go` should exclude exactly that one file, not any file
    named `server.go` at any depth.
    """
    target = tmp_path / "cmd" / "server.go"
    assert is_excluded(target, tmp_path, excludes=["cmd/server.go"]) is True

    elsewhere = tmp_path / "other" / "server.go"
    assert is_excluded(elsewhere, tmp_path, excludes=["cmd/server.go"]) is False


def test_is_excluded_empty_pattern_after_normalization(tmp_path: Path) -> None:
    """Patterns that normalize to empty string should not match anything.

    `-e /` and `-e **/` both normalize to empty; they must not be
    treated as "match everything".
    """
    src_file = tmp_path / "src" / "main.py"
    assert is_excluded(src_file, tmp_path, excludes=["/"]) is False
    assert is_excluded(src_file, tmp_path, excludes=["**/"]) is False


def test_is_excluded_does_not_match_substring_of_part(tmp_path: Path) -> None:
    """`-e ui` should not match a directory named `gui` or `quiet`.

    Regression guard: per-part fnmatch already gives this correctly,
    but the path-aware fallback must not weaken it.
    """
    gui_file = tmp_path / "gui" / "main.py"
    assert is_excluded(gui_file, tmp_path, excludes=["ui"]) is False
    quiet_file = tmp_path / "quiet" / "main.py"
    assert is_excluded(quiet_file, tmp_path, excludes=["ui"]) is False


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


# ---------------------------------------------------------------------------
# FileIndex tests
# ---------------------------------------------------------------------------

class TestFileIndex:
    """Tests for the FileIndex single-pass file discovery cache."""

    def _make_tree(self, tmp_path: Path) -> None:
        """Create a sample repo tree for testing."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("# app")
        (tmp_path / "src" / "utils.py").write_text("# utils")
        (tmp_path / "src" / "index.ts").write_text("// index")
        (tmp_path / "Makefile").write_text("all:")
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        (tmp_path / "Dockerfile.dev").write_text("FROM python:3.12-slim")
        (tmp_path / "build.gradle.kts").write_text("plugins {}")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("// excluded")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "justfile").write_text("default:")

    def test_build_path_anchored_excludes(self, tmp_path: Path) -> None:
        """FileIndex should honor relative-path exclude patterns (WI-zirik).

        Patterns containing '/' (after normalization) are matched against
        the relative path string, not just per-name. Both file-level
        (``cmd/server.go``) and directory-level (``ui/**`` -> reduces to
        ``ui`` so handled by name; ``cmd/foo/**`` -> reduces to
        ``cmd/foo`` which is path-anchored) forms must work.
        """
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "cmd").mkdir()
        (tmp_path / "cmd" / "server.go").write_text("package main")
        (tmp_path / "cmd" / "client.go").write_text("package main")
        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "server.go").write_text("package main")
        (tmp_path / "internal").mkdir()
        (tmp_path / "internal" / "deep").mkdir()
        (tmp_path / "internal" / "deep" / "x.go").write_text("package deep")

        idx = FileIndex.build(tmp_path, excludes=["cmd/server.go", "internal/deep/**"])
        rels = {f.relative_to(tmp_path).as_posix() for f in idx.all_files()}
        assert "cmd/server.go" not in rels  # file-level path exclude
        assert "cmd/client.go" in rels      # sibling kept
        assert "other/server.go" in rels    # not anchored to cmd/
        assert "internal/deep/x.go" not in rels  # directory pruned

    def test_build_glob_directory_exclude(self, tmp_path: Path) -> None:
        """FileIndex should prune directories matching glob name patterns."""
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "mypkg.egg-info").mkdir()
        (tmp_path / "mypkg.egg-info" / "PKG-INFO").write_text("name: x")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# ok")

        idx = FileIndex.build(tmp_path)
        rels = {f.relative_to(tmp_path).as_posix() for f in idx.all_files()}
        assert "mypkg.egg-info/PKG-INFO" not in rels
        assert "src/main.py" in rels

    def test_build_indexes_all_files(self, tmp_path: Path) -> None:
        """Build should index all non-excluded files."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        names = {f.name for f in idx.all_files()}
        assert "app.py" in names
        assert "utils.py" in names
        assert "index.ts" in names
        assert "Makefile" in names
        assert "Dockerfile" in names
        assert "Dockerfile.dev" in names
        assert "build.gradle.kts" in names
        assert "justfile" in names
        # Excluded dirs
        assert "pkg.js" not in names
        assert "HEAD" not in names

    def test_by_extension(self, tmp_path: Path) -> None:
        """by_extension should return files with matching suffix."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        py_files = list(idx.by_extension(".py"))
        assert {f.name for f in py_files} == {"app.py", "utils.py"}

        ts_files = list(idx.by_extension(".ts"))
        assert {f.name for f in ts_files} == {"index.ts"}

    def test_by_extension_case_insensitive(self, tmp_path: Path) -> None:
        """by_extension should match case-insensitively."""
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "readme.MD").write_text("# Hello")
        idx = FileIndex.build(tmp_path)

        md_files = list(idx.by_extension(".md"))
        assert {f.name for f in md_files} == {"readme.MD"}

    def test_by_name(self, tmp_path: Path) -> None:
        """by_name should return files matching exact filename."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        makefiles = list(idx.by_name("Makefile"))
        assert len(makefiles) == 1
        assert makefiles[0].name == "Makefile"

        justfiles = list(idx.by_name("justfile"))
        assert len(justfiles) == 1

    def test_by_glob(self, tmp_path: Path) -> None:
        """by_glob should match filename patterns."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        dockerfiles = list(idx.by_glob("Dockerfile*"))
        assert {f.name for f in dockerfiles} == {"Dockerfile", "Dockerfile.dev"}

    def test_by_glob_compound_extension(self, tmp_path: Path) -> None:
        """by_glob should handle compound extensions like *.gradle.kts."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        gradle = list(idx.by_glob("*.gradle.kts"))
        assert len(gradle) == 1
        assert gradle[0].name == "build.gradle.kts"

    def test_match_pattern_extension(self, tmp_path: Path) -> None:
        """match_pattern should classify *.py as extension lookup."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        result = list(idx.match_pattern("*.py"))
        assert {f.name for f in result} == {"app.py", "utils.py"}

    def test_match_pattern_exact_name(self, tmp_path: Path) -> None:
        """match_pattern should classify Makefile as exact name lookup."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        result = list(idx.match_pattern("Makefile"))
        assert len(result) == 1

    def test_match_pattern_glob(self, tmp_path: Path) -> None:
        """match_pattern should classify Dockerfile* as glob."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        result = list(idx.match_pattern("Dockerfile*"))
        assert {f.name for f in result} == {"Dockerfile", "Dockerfile.dev"}

    def test_match_pattern_strips_double_star(self, tmp_path: Path) -> None:
        """match_pattern should handle **/*.py prefix."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        result = list(idx.match_pattern("**/*.py"))
        assert {f.name for f in result} == {"app.py", "utils.py"}

    def test_len(self, tmp_path: Path) -> None:
        """__len__ should return total file count."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)
        assert len(idx) == 8  # 2 py + 1 ts + Makefile + 2 Dockerfiles + gradle + justfile

    def test_custom_excludes(self, tmp_path: Path) -> None:
        """Build with custom excludes should filter accordingly."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path, excludes=["node_modules", ".git", "sub"])

        names = {f.name for f in idx.all_files()}
        assert "justfile" not in names
        assert "app.py" in names

    def test_filename_level_excludes(self, tmp_path: Path) -> None:
        """Build should exclude files by exact name and glob pattern."""
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "package-lock.json").write_text("{}")
        (tmp_path / "hypergumbo.results.json").write_text("{}")
        (tmp_path / "app.py").write_text("# app")

        idx = FileIndex.build(tmp_path)
        names = {f.name for f in idx.all_files()}
        # package-lock.json is in DEFAULT_EXCLUDES (exact name)
        assert "package-lock.json" not in names
        # hypergumbo.results*.json matches glob exclude
        assert "hypergumbo.results.json" not in names
        assert "app.py" in names

    def test_locale_excludes_nested(self, tmp_path: Path) -> None:
        """Locale exclude should skip subdirectories of excluded dirs."""
        from hypergumbo_core.discovery import FileIndex

        locale_dir = tmp_path / "docs" / "ja"
        locale_dir.mkdir(parents=True)
        (locale_dir / "sub").mkdir()
        (locale_dir / "sub" / "deep.md").write_text("# deep")
        (tmp_path / "keep.py").write_text("# keep")

        idx = FileIndex.build(
            tmp_path,
            locale_excludes=[locale_dir],
        )
        names = {f.name for f in idx.all_files()}
        assert "deep.md" not in names
        assert "keep.py" in names

    def test_locale_excludes(self, tmp_path: Path) -> None:
        """Build with locale_excludes should skip locale dirs."""
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "en").mkdir()
        (tmp_path / "docs" / "en" / "guide.md").write_text("# Guide")
        (tmp_path / "docs" / "ja").mkdir()
        (tmp_path / "docs" / "ja" / "guide.md").write_text("# ガイド")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")

        idx = FileIndex.build(
            tmp_path,
            locale_excludes=[tmp_path / "docs" / "ja"],
        )
        # ja guide should be excluded
        ja_paths = [p for p in idx.all_files() if p.parent.name == "ja"]
        assert len(ja_paths) == 0
        # en guide should remain
        en_paths = [p for p in idx.all_files() if p.parent.name == "en"]
        assert len(en_paths) == 1

    def test_repo_root_property(self, tmp_path: Path) -> None:
        """repo_root property returns the root used during construction."""
        from hypergumbo_core.discovery import FileIndex

        idx = FileIndex.build(tmp_path)
        assert idx.repo_root == tmp_path

    def test_all_files_sorted(self, tmp_path: Path) -> None:
        """all_files should return paths in sorted order."""
        from hypergumbo_core.discovery import FileIndex

        self._make_tree(tmp_path)
        idx = FileIndex.build(tmp_path)

        paths = idx.all_files()
        assert paths == sorted(paths)

    def test_by_glob_no_duplicate_yields(self, tmp_path: Path) -> None:
        """by_glob should not yield the same file twice for multiple patterns."""
        from hypergumbo_core.discovery import FileIndex

        (tmp_path / "test.py").write_text("# test")
        idx = FileIndex.build(tmp_path)

        # A file should only be yielded once even if multiple patterns match
        result = list(idx.by_glob("*.py", "test*"))
        names = [f.name for f in result]
        # by_glob yields once per file (breaks after first matching pattern)
        assert names.count("test.py") == 1


class TestFileIndexFindFilesIntegration:
    """Tests that find_files uses the global FileIndex when available."""

    def test_find_files_uses_index(self, tmp_path: Path) -> None:
        """find_files should return same results via index as via rglob."""
        from hypergumbo_core.discovery import FileIndex, set_file_index

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("# app")
        (tmp_path / "src" / "lib.py").write_text("# lib")
        (tmp_path / "readme.md").write_text("# Readme")

        # Get baseline results without index
        baseline = set(find_files(tmp_path, ["*.py"]))

        # Build index and set global
        idx = FileIndex.build(tmp_path)
        try:
            set_file_index(idx)
            indexed = set(find_files(tmp_path, ["*.py"]))
        finally:
            set_file_index(None)

        assert indexed == baseline

    def test_find_files_index_respects_max_files(self, tmp_path: Path) -> None:
        """find_files with index should respect max_files limit."""
        from hypergumbo_core.discovery import FileIndex, set_file_index

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"# {i}")

        idx = FileIndex.build(tmp_path)
        try:
            set_file_index(idx)
            result = list(find_files(tmp_path, ["*.py"], max_files=3))
        finally:
            set_file_index(None)

        assert len(result) == 3

    def test_find_files_index_respects_max_file_bytes(self, tmp_path: Path) -> None:
        """find_files with index should skip oversized files."""
        from hypergumbo_core.discovery import FileIndex, set_file_index

        (tmp_path / "small.py").write_text("x")
        (tmp_path / "big.py").write_text("x" * 10000)

        skipped: list[str] = []

        idx = FileIndex.build(tmp_path)
        try:
            set_file_index(idx)
            result = list(find_files(
                tmp_path, ["*.py"],
                max_file_bytes=100,
                on_file_skipped=lambda p, s, r: skipped.append(p.name),
            ))
        finally:
            set_file_index(None)

        assert {f.name for f in result} == {"small.py"}
        assert "big.py" in skipped

    def test_find_files_index_deduplicates(self, tmp_path: Path) -> None:
        """find_files with index should not yield duplicates for overlapping patterns."""
        from hypergumbo_core.discovery import FileIndex, set_file_index

        (tmp_path / "app.ts").write_text("// ts")
        (tmp_path / "app.d.ts").write_text("// dts")

        idx = FileIndex.build(tmp_path)
        try:
            set_file_index(idx)
            # *.ts and *.d.ts both match app.d.ts
            result = list(find_files(tmp_path, ["*.ts", "*.d.ts"]))
        finally:
            set_file_index(None)

        names = [f.name for f in result]
        assert names.count("app.d.ts") == 1
        assert "app.ts" in names

    def test_find_files_falls_back_without_index(self, tmp_path: Path) -> None:
        """find_files should work normally when no index is set."""
        (tmp_path / "test.py").write_text("# test")

        result = list(find_files(tmp_path, ["*.py"]))
        assert len(result) == 1
        assert result[0].name == "test.py"

    def test_find_files_index_different_root_falls_back(self, tmp_path: Path) -> None:
        """find_files should fall back to rglob when repo_root differs from index."""
        from hypergumbo_core.discovery import FileIndex, set_file_index

        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.py").write_text("# file")

        # Build index for tmp_path, but query for sub
        idx = FileIndex.build(tmp_path)
        try:
            set_file_index(idx)
            result = list(find_files(sub, ["*.py"]))
        finally:
            set_file_index(None)

        # Should still find the file (via rglob fallback)
        assert len(result) == 1

    def test_set_get_file_index(self) -> None:
        """set_file_index / get_file_index round-trip."""
        from hypergumbo_core.discovery import get_file_index, set_file_index

        assert get_file_index() is None  # default state
        sentinel = object()
        try:
            set_file_index(sentinel)  # type: ignore[arg-type]
            assert get_file_index() is sentinel
        finally:
            set_file_index(None)
