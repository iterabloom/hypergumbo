# SPDX-License-Identifier: AGPL-3.0-or-later
"""``test_input_dirs``: a repository declares the directories that hold test input.

ADR-0004 point 10 (owner ruling 2026-11-16). A fixture project kept outside a
test directory -- hypergumbo's own ``recorded/aardvark_dns`` crate and
``recorded/sample_project`` -- was read as first-party code: its ``main``
functions were entry points, its language counted in the stack, its tests fed
the sketch's Tests line. These tests pin:

- the config key's refusals, each naming the entry (exit 2 through the CLI);
- that every test predicate gives a declared file the answer a ``tests/``
  file gets, with its own reason;
- the three places a declaration is installed (an analysis, a map a command
  reads, a sketch) and that each restores what it found;
- the map field, the stderr line and the sketch Overview line;
- acceptance on small copies of the two recorded trees: no entry point from
  them, their Rust counted as test files only, no ``cargo test`` on the
  Tests line -- each against a control without the declaration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

from hypergumbo_core import declared_test_inputs as dti
from hypergumbo_core.declared_test_inputs import (
    Declaration,
    active_declaration,
    declaration_for_repo,
    declaration_from_map,
    declared,
    declared_summary,
    format_declared_disclosure,
    install_declaration,
)
from hypergumbo_core.paths import (
    TEST_FILE_REASON_DECLARED,
    _path_rule_reason,
    classify_test_file,
    is_test_file,
    is_test_node,
)
from hypergumbo_core.selection.filters import is_test_path
from hypergumbo_core.user_config import (
    ConfigError,
    config_cache_token,
    load_layered_config,
)

_CRATE = "packages/ra/recorded/aardvark_dns"
_SAMPLE = "packages/sp/recorded/sample_project"


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _config(repo: Path, body: str) -> Path:
    return _write(repo / ".hypergumbo.toml", body)


def _load(repo: Path, tmp_path: Path) -> Any:
    return load_layered_config(
        repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )


# ---------------------------------------------------------------------------
# The config key
# ---------------------------------------------------------------------------


class TestConfigKey:
    def test_entries_are_normalised_deduplicated_and_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "x").mkdir(parents=True)
        _config(tmp_path, 'test_input_dirs = ["b/", "./a/x", "a/x", "a/../b"]\n')
        assert _load(tmp_path, tmp_path).test_input_dirs == ("a/x", "b")

    def test_no_key_declares_nothing(self, tmp_path: Path) -> None:
        _config(tmp_path, 'trace_deps = "none"\n')
        assert _load(tmp_path, tmp_path).test_input_dirs == ()

    def _refused(self, tmp_path: Path, body: str) -> str:
        _config(tmp_path, body)
        with pytest.raises(ConfigError) as info:
            _load(tmp_path, tmp_path)
        message = str(info.value)
        assert str(tmp_path / ".hypergumbo.toml") in message
        assert "'test_input_dirs'" in message
        return message

    def test_a_non_list_value_is_refused(self, tmp_path: Path) -> None:
        assert "must be a list" in self._refused(tmp_path, 'test_input_dirs = "a"\n')

    def test_a_non_string_entry_is_refused(self, tmp_path: Path) -> None:
        assert "got 3" in self._refused(tmp_path, "test_input_dirs = [3]\n")

    def test_an_absolute_path_is_refused(self, tmp_path: Path) -> None:
        message = self._refused(tmp_path, f'test_input_dirs = ["{tmp_path}"]\n')
        assert f"'{tmp_path}' is absolute" in message

    def test_a_drive_path_is_refused(self, tmp_path: Path) -> None:
        message = self._refused(tmp_path, 'test_input_dirs = ["C:/fixtures"]\n')
        assert "'C:/fixtures' is absolute" in message

    def test_a_path_leaving_the_repository_is_refused(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "outside").mkdir()
        _config(repo, 'test_input_dirs = ["../outside"]\n')
        with pytest.raises(ConfigError, match=re.escape("'../outside' leaves the repository")):
            _load(repo, tmp_path)

    def test_a_symlink_leaving_the_repository_is_refused(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "outside").mkdir()
        (repo / "link").symlink_to(tmp_path / "outside")
        _config(repo, 'test_input_dirs = ["link"]\n')
        with pytest.raises(ConfigError, match="'link' leaves the repository through a symbolic link"):
            _load(repo, tmp_path)

    def test_a_missing_directory_is_refused(self, tmp_path: Path) -> None:
        message = self._refused(tmp_path, 'test_input_dirs = ["nope"]\n')
        assert "'nope' does not exist" in message

    def test_a_file_is_refused(self, tmp_path: Path) -> None:
        _write(tmp_path / "f.py", "x = 1\n")
        assert "'f.py' is not a directory" in self._refused(
            tmp_path, 'test_input_dirs = ["f.py"]\n',
        )

    @pytest.mark.parametrize("char", ["*", "?", "[", "]"])
    def test_a_glob_pattern_is_refused(self, tmp_path: Path, char: str) -> None:
        message = self._refused(tmp_path, f'test_input_dirs = ["a{char}"]\n')
        assert "glob patterns are not accepted" in message

    @pytest.mark.parametrize("entry", ["", ".", "./", "a/.."])
    def test_the_root_itself_is_refused(self, tmp_path: Path, entry: str) -> None:
        (tmp_path / "a").mkdir()
        message = self._refused(tmp_path, f'test_input_dirs = ["{entry}"]\n')
        assert "names the repository root" in message

    def test_the_user_tier_refuses_the_key(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        user = _write(
            tmp_path / "xdg" / "hypergumbo" / "config.toml", 'test_input_dirs = ["a"]\n',
        )
        with pytest.raises(ConfigError) as info:
            _load(tmp_path, tmp_path)
        assert str(user) in str(info.value)
        assert "belongs in that repository's .hypergumbo.toml" in str(info.value)

    def test_a_refused_entry_exits_2_naming_the_file_and_the_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import main

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        repo = tmp_path / "repo"
        _write(repo / "app.py", "def f():\n    return 1\n")
        _config(repo, 'test_input_dirs = ["fixtures_missing"]\n')
        with pytest.raises(SystemExit) as info:
            main(["survey", str(repo), "--out", str(tmp_path / "o.json"),
                  "--no-sketch-fan-out", "--no-handler-slices"])
        assert info.value.code == 2
        err = capsys.readouterr().err
        assert str(repo / ".hypergumbo.toml") in err
        assert "'fixtures_missing' does not exist" in err

    def test_the_results_cache_key_moves_with_the_key(self, tmp_path: Path) -> None:
        """The config's whole bytes are in the results-cache key (census
        claim, verified): declaring a directory, or changing which, misses
        the cache a run without it wrote."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        tokens = [config_cache_token(tmp_path, environ=env)]
        for body in ('test_input_dirs = ["a"]\n', 'test_input_dirs = ["b"]\n'):
            _config(tmp_path, body)
            tokens.append(config_cache_token(tmp_path, environ=env))
        assert len(set(tokens)) == 3


# ---------------------------------------------------------------------------
# Matching and the predicates
# ---------------------------------------------------------------------------


class TestMatching:
    def test_relative_paths_match_at_a_directory_boundary(self) -> None:
        d = Declaration(dirs=("a/recorded",))
        assert d.dir_of("a/recorded/x.rs") == "a/recorded"
        assert d.dir_of("./a/recorded/x.rs") == "a/recorded"
        assert d.dir_of("a/recorded") == "a/recorded"
        assert d.dir_of("a/recorded2/x.rs") is None
        assert d.dir_of("b/a/recorded/x.rs") is None
        assert d.dir_of("") is None
        assert Declaration(dirs=()).dir_of("a/recorded/x.rs") is None

    def test_backslash_paths_match(self) -> None:
        assert Declaration(dirs=("a/recorded",)).dir_of("a\\recorded\\x.rs") == "a/recorded"

    def test_absolute_paths_match_only_under_the_installed_root(self, tmp_path: Path) -> None:
        d = declaration_for_repo(tmp_path, ["a"], None)
        assert d.dir_of(f"{tmp_path}/a/x.py") == "a"
        assert d.dir_of(f"{tmp_path}/b/x.py") is None
        assert d.dir_of("/elsewhere/a/x.py") is None
        assert d.dir_of("C:/elsewhere/a/x.py") is None

    @pytest.mark.parametrize("path", [
        "/other/internal/e2e/x.go",  # under another root, tail is the declared dir
        "/internal/e2e/x.go",  # the declared dir at the filesystem root
        "/repo2/internal/e2e/x.go",  # a sibling whose name extends the root's
        "C:/repo/internal/e2e/x.go",  # a drive path
    ])
    def test_an_absolute_path_outside_the_roots_is_not_declared(self, path: str) -> None:
        d = Declaration(dirs=("internal/e2e",), roots=("/repo",))
        assert d.dir_of("/repo/internal/e2e/x.go") == "internal/e2e"  # control
        assert d.dir_of(path) is None
        with declared(d):
            assert classify_test_file(path) is None
            assert classify_test_file("/repo/internal/e2e/x.go") == TEST_FILE_REASON_DECLARED

    @pytest.mark.parametrize("path", [
        "/internal/e2e/x.go", "/repo/internal/e2e/x.go", "C:/internal/e2e/x.go",
    ])
    def test_without_roots_no_absolute_path_is_declared(self, path: str) -> None:
        """A declaration read from a map has no root: map node paths are
        relative, and an absolute path cannot be placed in the repository."""
        d = Declaration(dirs=("internal/e2e",))
        assert d.dir_of("internal/e2e/x.go") == "internal/e2e"  # control
        assert d.dir_of(path) is None

    def test_a_declaration_read_from_a_map_has_no_root(self, tmp_path: Path) -> None:
        bm = {"supply_chain_summary": {"declared_test_inputs": {
            "config": ".hypergumbo.toml", "setting": "test_input_dirs",
            "dirs": [{"path": "a", "files": 2}, {"bad": 1}], "files": 2,
        }}}
        d = declaration_from_map(bm)
        assert d == Declaration(dirs=("a",), config=".hypergumbo.toml")
        assert d.dir_of("a/x.py") == "a"
        assert d.dir_of(f"{tmp_path}/a/x.py") is None
        rooted = declaration_from_map(bm, tmp_path)
        assert rooted is not None and rooted.dir_of(f"{tmp_path}/a/x.py") == "a"

    def test_a_map_without_the_field_carries_no_declaration(self) -> None:
        assert declaration_from_map({}) is None
        assert declaration_from_map({"supply_chain_summary": {}}) is None
        assert declaration_from_map(
            {"supply_chain_summary": {"declared_test_inputs": "x"}},
        ) is None

    def test_nothing_installed_is_the_path_rules_alone(self) -> None:
        assert active_declaration() is None
        assert dti.declared_dir_of("a/recorded/x.rs") is None
        assert classify_test_file("a/recorded/x.rs") is None

    def test_the_declaration_has_its_own_reason_and_path_rules_go_first(self) -> None:
        with declared(Declaration(dirs=("a/recorded",))):
            assert classify_test_file("a/recorded/src/main.rs") == TEST_FILE_REASON_DECLARED
            assert classify_test_file("a/recorded/src/test/test.rs") == "test"
            assert classify_test_file("a/recorded/fixtures/x.json") == "fixture"
            assert classify_test_file("a/src/main.rs") is None

    @pytest.mark.parametrize("path", ["a/recorded/src/main.rs", "tests/test_x.py"])
    def test_the_four_predicates_agree(self, path: str) -> None:
        with declared(Declaration(dirs=("a/recorded",))):
            assert classify_test_file(path) is not None
            assert is_test_file(path)
            assert is_test_node(path, None)
            assert is_test_path(path)
        assert is_test_file(path) is (path == "tests/test_x.py")

    def test_declared_restores_what_it_found(self) -> None:
        outer = Declaration(dirs=("a",))
        with declared(outer):
            with declared(Declaration(dirs=("b",))):
                assert dti.declared_dir_of("b/x") == "b"
                assert dti.declared_dir_of("a/x") is None
            assert active_declaration() is outer
        assert active_declaration() is None

    def test_symbol_source_scope_names_the_declaration(self) -> None:
        from hypergumbo_core.verify_claims import symbol_source_scope

        sid = "python:e2e_app/client.py:4-7:leak:function"
        assert symbol_source_scope(sid) == "production"
        with declared(Declaration(dirs=("e2e_app",))):
            assert symbol_source_scope(sid) == "declared_test_input_sourced"

    def test_a_declared_source_is_excluded_like_a_test_source(self) -> None:
        """The default the owner chose: a declared directory is treated
        exactly like a path-classified test directory, and named apart."""
        from hypergumbo_core.taint import TaintFlowFinding
        from hypergumbo_core.verify_claims import (
            Claim, TaintFlowConstraint, verify_taint_claim,
        )

        claim = Claim(
            id="TF", text="no plaintext to disk",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext", prohibited_sink_zone="host_fs",
            ),
        )

        def flow(path: str) -> TaintFlowFinding:
            return TaintFlowFinding(
                taint_label="plaintext",
                source_symbol=f"python:{path}:1-2:leak:function",
                source_primitive="Fernet.decrypt",
                sink_symbol="python:file:0-0:write:external_symbol",
                sink_primitive="file.write", sink_zone="host_fs",
                sanitized=False, confidence="approximate",
                analysis_method="structural", path=[],
            )

        assert verify_taint_claim(claim, [flow("e2e_app/c.py")]).verdict == "violated"
        assert verify_taint_claim(claim, [flow("tests/c.py")]).excluded_flows == {
            "test_sourced": 1,
        }
        with declared(Declaration(dirs=("e2e_app",))):
            verdict = verify_taint_claim(claim, [flow("e2e_app/c.py")])
        assert verdict.verdict == "confirmed"
        assert verdict.excluded_flows == {"declared_test_input_sourced": 1}
        assert "1 declared_test_input_sourced" in verdict.details


# ---------------------------------------------------------------------------
# The summary and its disclosure line
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_summary_counts_only_files_the_path_rules_miss(self, tmp_path: Path) -> None:
        d = declaration_for_repo(tmp_path, ["a", "a/inner", "b"], ".hypergumbo.toml")
        files = [
            tmp_path / "a" / "main.rs",
            tmp_path / "a" / "inner" / "lib.rs",
            tmp_path / "a" / "test" / "t.rs",  # the path rules already classify it
            tmp_path / "c" / "x.py",
        ]
        summary = declared_summary(d, files, _path_rule_reason)
        assert summary == {
            "config": ".hypergumbo.toml", "setting": "test_input_dirs",
            "dirs": [
                {"path": "a", "files": 2}, {"path": "a/inner", "files": 0},
                {"path": "b", "files": 0},
            ],
            "files": 2,
        }

    def test_the_line_names_three_and_counts_the_rest(self) -> None:
        summary = {
            "config": ".hypergumbo.toml", "setting": "test_input_dirs", "files": 4,
            "dirs": [{"path": p, "files": n} for p, n in
                     [("a", 1), ("b", 3), ("c", 0), ("d", 0)]],
        }
        assert format_declared_disclosure(summary) == (
            "Classified as test input because .hypergumbo.toml declares it "
            "(test_input_dirs), 4 files: a/ (1 file), b/ (3 files), c/ (0 files), "
            "1 more (ADR-0004)."
        )

    def test_one_file_is_singular(self) -> None:
        line = format_declared_disclosure(
            {"config": "x.toml", "setting": "s", "files": 1,
             "dirs": [{"path": "a", "files": 1}]},
        )
        assert line == "Classified as test input because x.toml declares it (s), 1 file: a/ (1 file) (ADR-0004)."

    @pytest.mark.parametrize("summary", [None, {}, {"dirs": []}])
    def test_nothing_declared_prints_nothing(self, summary: Any) -> None:
        assert format_declared_disclosure(summary) is None


# ---------------------------------------------------------------------------
# End to end: small copies of hypergumbo's two recorded trees
# ---------------------------------------------------------------------------


def _fixture_repo(root: Path) -> Path:
    """A first-party Python package, its pytest suite, and small copies of
    the two recorded trees (a Rust crate with a ``main`` and a ``#[test]``
    module; a Python sample project with a ``main``)."""
    _write(root / "app" / "__init__.py")
    _write(root / "app" / "cli.py", (
        "def greet(name):\n    return 'hello ' + name\n\n\n"
        "def main():\n    print(greet('x'))\n\n\n"
        "if __name__ == '__main__':\n    main()\n"
    ))
    _write(root / "tests" / "test_cli.py", (
        "import pytest\nfrom app.cli import greet\n\n\n"
        "def test_greet():\n    assert greet('a') == 'hello a'\n"
    ))
    _write(root / "pyproject.toml", '[project]\nname = "app"\nversion = "0.1"\n')
    crate = root / _CRATE / "crate"
    _write(crate / "Cargo.toml", '[package]\nname = "aardvark-dns"\nversion = "1.0.0"\n')
    _write(crate / "src" / "main.rs", "mod server;\n\nfn main() {\n    server::serve();\n}\n")
    _write(crate / "src" / "server.rs", 'pub fn serve() {\n    println!("serving");\n}\n')
    _write(crate / "src" / "test" / "test.rs", "#[test]\nfn t() {\n    assert!(true);\n}\n")
    pkg = root / _SAMPLE / "pkg"
    _write(pkg / "__init__.py")
    _write(pkg / "shapes.py", "def area(r):\n    return 3 * r * r\n")
    _write(pkg / "app.py", (
        "from pkg.shapes import area\n\n\ndef main():\n    print(area(2))\n\n\n"
        "if __name__ == '__main__':\n    main()\n"
    ))
    return root


class _Runs:
    """Three surveys in one process, in this order: with the declaration, a
    config that declares nothing, then no config (the leakage control)."""

    def __init__(self, base: Path) -> None:
        from hypergumbo_core.cli import main

        self.base = base
        self.maps: dict[str, dict[str, Any]] = {}
        self.err: dict[str, str] = {}
        self.after: dict[str, Any] = {}
        for name, config in (
            ("declared", f'test_input_dirs = ["{_CRATE}", "{_SAMPLE}"]\n'),
            ("empty", "test_input_dirs = []\n"),
            ("none", None),
        ):
            repo = _fixture_repo(base / name)
            if config is not None:
                _config(repo, config)
            out = base / f"{name}.json"
            import contextlib
            import io

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = main(["survey", str(repo), "--out", str(out),
                           "--no-sketch-fan-out", "--no-handler-slices"])
            assert rc == 0, err.getvalue()
            self.maps[name] = json.loads(out.read_text())
            self.err[name] = err.getvalue()
            self.after[name] = active_declaration()

    def repo(self, name: str) -> Path:
        return self.base / name


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Runs]:
    base = tmp_path_factory.mktemp("declared")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(base / "cache"))
        mp.setenv("XDG_CONFIG_HOME", str(base / "xdg"))
        install_declaration(None)
        yield _Runs(base)
        install_declaration(None)


def _entry_paths(bm: dict[str, Any]) -> set[str]:
    by_id = {n["id"]: n for n in bm["nodes"]}
    return {
        by_id[e["symbol_id"]]["path"] for e in bm.get("entrypoints", [])
        if e.get("symbol_id") in by_id
    }


def _under(path: str, *dirs: str) -> bool:
    return any(path == d or path.startswith(d + "/") for d in dirs)


class TestAnalysis:
    def test_the_map_records_the_declaration(self, runs: _Runs) -> None:
        summary = runs.maps["declared"]["supply_chain_summary"]["declared_test_inputs"]
        assert summary == {
            "config": ".hypergumbo.toml", "setting": "test_input_dirs",
            # crate: Cargo.toml, main.rs, server.rs (test/test.rs is already
            # test code by its path); sample: three .py files.
            "dirs": [{"path": _CRATE, "files": 3}, {"path": _SAMPLE, "files": 3}],
            "files": 6,
        }

    def test_a_config_declaring_nothing_records_an_empty_list(self, runs: _Runs) -> None:
        summary = runs.maps["empty"]["supply_chain_summary"]["declared_test_inputs"]
        assert summary["dirs"] == [] and summary["files"] == 0

    def test_no_config_no_field(self, runs: _Runs) -> None:
        assert "declared_test_inputs" not in runs.maps["none"]["supply_chain_summary"]

    def test_stderr_discloses_the_declaration(self, runs: _Runs) -> None:
        assert (
            "Classified as test input because .hypergumbo.toml declares it "
            f"(test_input_dirs), 6 files: {_CRATE}/ (3 files), {_SAMPLE}/ (3 files)"
        ) in runs.err["declared"]
        assert "Classified as test input" not in runs.err["empty"]
        assert "Classified as test input" not in runs.err["none"]

    def test_no_entry_point_from_a_declared_directory(self, runs: _Runs) -> None:
        assert not {p for p in _entry_paths(runs.maps["declared"]) if _under(p, _CRATE, _SAMPLE)}
        assert "app/cli.py" in _entry_paths(runs.maps["declared"])

    def test_without_the_declaration_the_fixture_mains_are_entry_points(
        self, runs: _Runs,
    ) -> None:
        """The control, and the leakage check: the ``none`` repository was
        analysed after the ``declared`` one in the same process and has the
        same directory names, so a leaked declaration would hide these."""
        for name in ("empty", "none"):
            paths = _entry_paths(runs.maps[name])
            assert f"{_CRATE}/crate/src/main.rs" in paths
            assert f"{_SAMPLE}/pkg/app.py" in paths

    def test_the_declaration_does_not_outlive_the_run(self, runs: _Runs) -> None:
        assert runs.after == {"declared": None, "empty": None, "none": None}

    def test_rust_is_counted_only_as_test_files(self, runs: _Runs) -> None:
        rust = runs.maps["declared"]["profile"]["languages"]["rust"]
        assert rust["test_files"] == rust["files"] == 3
        control = runs.maps["none"]["profile"]["languages"]["rust"]
        assert control["test_files"] == 1

    def _sketch(self, runs: _Runs, name: str, *, from_map: bool) -> str:
        from hypergumbo_core.sketch import generate_sketch

        return generate_sketch(
            runs.repo(name), max_tokens=4000,
            cached_results=runs.maps[name] if from_map else None,
        )

    def test_the_tests_line_drops_the_fixture_framework(self, runs: _Runs) -> None:
        sketch = self._sketch(runs, "declared", from_map=True)
        tests = sketch.split("## Tests", 1)[1].split("##", 1)[0]
        assert "pytest" in tests and "cargo test" not in tests
        control = self._sketch(runs, "none", from_map=True)
        assert "cargo test" in control.split("## Tests", 1)[1].split("##", 1)[0]
        assert active_declaration() is None

    def test_the_overview_discloses_the_declaration(self, runs: _Runs) -> None:
        sketch = self._sketch(runs, "declared", from_map=True)
        overview = sketch.split("## Overview", 1)[1].split("## Structure", 1)[0]
        assert "Classified as test input because .hypergumbo.toml declares it" in overview
        assert "Classified as test input" not in self._sketch(runs, "none", from_map=True)


def test_a_library_run_restores_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_survey`` called directly (no ``cli.main`` around it) restores the
    declaration it found, so the next caller in the process -- another
    repository's analysis, or a bare predicate call -- is not classified by
    this repository's config."""
    from hypergumbo_core.cli import run_survey

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = _fixture_repo(tmp_path / "repo")
    _config(repo, f'test_input_dirs = ["{_CRATE}"]\n')
    outer = Declaration(dirs=("elsewhere",))
    with declared(outer):
        run_survey(repo, out_path=tmp_path / "o.json", include_sketch_precomputed=False,
                   enable_handler_slices=False, no_sketch_fan_out=True, progress=False)
        assert active_declaration() is outer
    assert classify_test_file(f"{_CRATE}/crate/src/main.rs") is None


class TestRereadingCommands:
    """A command reading a saved map classifies by the map's declaration."""

    def _without_field(self, runs: _Runs) -> Path:
        bm = json.loads(json.dumps(runs.maps["declared"]))
        del bm["supply_chain_summary"]["declared_test_inputs"]
        path = runs.base / "declared_nofield.json"
        path.write_text(json.dumps(bm))
        return path

    def _json(
        self, capsys: pytest.CaptureFixture[str], argv: list[str],
    ) -> dict[str, Any]:
        from hypergumbo_core.cli import main

        capsys.readouterr()
        assert main(argv) == 0
        data: dict[str, Any] = json.loads(capsys.readouterr().out)
        return data

    @pytest.mark.parametrize("command,key", [
        ("dead-code-maybe", "total_production_functions"),
        ("test-coverage", "total_functions"),
    ])
    def test_the_map_field_decides(
        self, runs: _Runs, capsys: pytest.CaptureFixture[str], command: str, key: str,
    ) -> None:
        repo = str(runs.repo("declared"))
        with_field = self._json(capsys, [
            command, repo, "--input", str(runs.base / "declared.json"), "--format", "json",
        ])
        # cli.main restores what it found once the command returns: the map's
        # declaration does not outlive the command that read it.
        assert active_declaration() is None
        without = self._json(capsys, [
            command, repo, "--input", str(self._without_field(runs)), "--format", "json",
        ])
        # Same nodes and edges; only the declaration differs. app/cli.py has
        # greet and main; the fixtures add main, serve, area and main.
        assert with_field["summary"][key] == 2
        assert without["summary"][key] == 6

    def test_load_substrate_installs_nothing(self, runs: _Runs) -> None:
        from hypergumbo_core.survey_io import load_substrate

        outer = Declaration(dirs=("elsewhere",))
        with declared(outer):
            load_substrate(runs.base / "declared.json")
            assert active_declaration() is outer

    def test_reading_substrate_scopes_the_maps_declaration(self, runs: _Runs) -> None:
        """Two map loads, and a map load followed by anything, do not leak."""
        from hypergumbo_core.survey_io import reading_substrate

        outer = Declaration(dirs=("elsewhere",))
        with declared(outer):
            with reading_substrate(runs.base / "declared.json") as bm:
                assert "nodes" in bm
                installed = active_declaration()
                assert installed is not None and installed.dirs == (_CRATE, _SAMPLE)
                assert classify_test_file(f"{_CRATE}/crate/src/main.rs") == TEST_FILE_REASON_DECLARED
                with reading_substrate(self._without_field(runs)):
                    assert active_declaration() is None
                assert active_declaration() is installed
            assert active_declaration() is outer
            with pytest.raises(RuntimeError):
                with reading_substrate(runs.base / "declared.json"):
                    raise RuntimeError("boom")
            assert active_declaration() is outer
        assert classify_test_file(f"{_CRATE}/crate/src/main.rs") is None

    def test_a_command_called_directly_restores_what_it_found(
        self, runs: _Runs, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Not through ``cli.main``: the command's own decorator restores."""
        from hypergumbo_core.cli import build_parser, cmd_dead_code_maybe

        args = build_parser().parse_args([
            "dead-code-maybe", str(runs.repo("declared")),
            "--input", str(runs.base / "declared.json"), "--format", "json",
        ])
        outer = Declaration(dirs=("elsewhere",))
        with declared(outer):
            assert cmd_dead_code_maybe(args) == 0
            assert active_declaration() is outer
        data = json.loads(capsys.readouterr().out)
        assert data["summary"]["total_production_functions"] == 2


def test_every_command_that_installs_a_maps_declaration_restores_it() -> None:
    """The gate behind ``cli._load_command_substrate``: every function that
    calls it is decorated with ``_restores_declaration``, so the map's
    declaration cannot outlive the command whoever calls it."""
    import ast
    import inspect

    from hypergumbo_core import cli

    tree = ast.parse(inspect.getsource(cli))
    callers: dict[str, bool] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_load_command_substrate":
            continue
        calls = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_load_command_substrate"
            for n in ast.walk(fn)
        )
        if calls:
            callers[fn.name] = any(
                isinstance(d, ast.Name) and d.id == "_restores_declaration"
                for d in fn.decorator_list
            )
    assert len(callers) == 12, sorted(callers)
    assert [name for name, ok in callers.items() if not ok] == []


class TestSketchFallback:
    """The direct-analysis fallback of ``generate_sketch`` has no map, so it
    reads the declaration from the project config."""

    def test_a_valid_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from hypergumbo_core.sketch import _declaration_from_config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        (tmp_path / "fx").mkdir()
        _config(tmp_path, 'test_input_dirs = ["fx"]\n')
        d = _declaration_from_config(tmp_path)
        assert d.dirs == ("fx",) and d.config == ".hypergumbo.toml"

    def test_a_refused_config_declares_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_core.sketch import _declaration_from_config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        _config(tmp_path, 'test_input_dirs = ["missing"]\n')
        assert _declaration_from_config(tmp_path).dirs == ()

    def test_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from hypergumbo_core.sketch import _declaration_from_config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        d = _declaration_from_config(tmp_path)
        assert d.dirs == () and d.config is None
