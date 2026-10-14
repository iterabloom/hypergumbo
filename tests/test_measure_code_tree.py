# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``scripts/measure_code_tree.py`` (WI-tumog).

The behavioural repros live beside each script's own tests (a fake
``hypergumbo_core`` on PYTHONPATH, run in a child process). These pin the
helper's decision table in-process, with the environment and ``sys.path``
injected, so each row is checked without spawning an interpreter.
"""
# covers: scripts/measure_code_tree.py
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest
from helpers_code_tree import REPO_ROOT

_PATH = REPO_ROOT / "scripts" / "measure_code_tree.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_code_tree", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_code_tree"] = module  # @dataclass needs it
    spec.loader.exec_module(module)
    return module


mct = _load()


def _tree(root: Path, names: list[str]) -> Path:
    """A tree with a ``packages/<dist>/src/<pkg>`` layout and a script."""
    for name in names:
        pkg = root / "packages" / name.replace("_", "-") / "src" / name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    script = root / "scripts" / "measure-x.py"
    script.write_text("", encoding="utf-8")
    return script


def _src(root: Path, name: str) -> Path:
    return root / "packages" / name.replace("_", "-") / "src"


NAMES = ["hypergumbo_core", "hypergumbo_lang_mainstream", "hypergumbo_tracker"]


class TestScopes:
    def test_analysis_scope_is_core_plus_languages(self) -> None:
        assert mct.SCOPES["analysis"]("hypergumbo_core")
        assert mct.SCOPES["analysis"]("hypergumbo_lang_mainstream")
        assert not mct.SCOPES["analysis"]("hypergumbo_tracker")
        assert not mct.SCOPES["analysis"]("hypergumbo")

    def test_core_scope_is_core_only(self) -> None:
        assert mct.SCOPES["core"]("hypergumbo_core")
        assert not mct.SCOPES["core"]("hypergumbo_lang_common")


class TestTreeOf:
    def test_a_packages_layout_resolves_to_the_tree_root(self, tmp_path: Path) -> None:
        pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
        assert mct.tree_of(pkg) == tmp_path

    def test_anything_else_is_the_directory_it_was_found_in(self, tmp_path: Path) -> None:
        assert mct.tree_of(tmp_path / "fake" / "hypergumbo_core") == tmp_path / "fake"

    def test_a_src_dir_outside_packages_is_not_mistaken_for_a_tree(
        self, tmp_path: Path,
    ) -> None:
        pkg = tmp_path / "proj" / "src" / "hypergumbo_core"
        assert mct.tree_of(pkg) == tmp_path / "proj" / "src"


class TestPin:
    def test_no_pythonpath_pins_this_tree_first_in_scope_only(self, tmp_path: Path) -> None:
        script = _tree(tmp_path / "t", NAMES)
        path = ["/x", "/y"]
        pin = mct.pin(script, "analysis", environ={}, path=path)
        assert path[:2] == [
            str(_src(tmp_path / "t", "hypergumbo_core")),
            str(_src(tmp_path / "t", "hypergumbo_lang_mainstream")),
        ]
        assert str(_src(tmp_path / "t", "hypergumbo_tracker")) not in path
        assert path[2:] == ["/x", "/y"]
        assert pin.choice == ""
        assert pin.script_tree == tmp_path / "t"

    def test_pythonpath_naming_this_tree_is_not_a_conflict(self, tmp_path: Path) -> None:
        script = _tree(tmp_path / "t", NAMES)
        pp = str(_src(tmp_path / "t", "hypergumbo_core"))
        path: list[str] = []
        mct.pin(script, "core", environ={"PYTHONPATH": pp}, path=path)
        assert path == [pp]

    def test_pythonpath_providing_nothing_in_scope_is_not_a_conflict(
        self, tmp_path: Path,
    ) -> None:
        script = _tree(tmp_path / "t", NAMES)
        other = tmp_path / "other"
        (other / "unrelated").mkdir(parents=True)
        (other / "unrelated" / "__init__.py").write_text("")
        path: list[str] = []
        mct.pin(script, "core", environ={"PYTHONPATH": f"{other}:"}, path=path)
        assert path == [str(_src(tmp_path / "t", "hypergumbo_core"))]

    def test_a_conflict_with_no_choice_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        script = _tree(tmp_path / "t", NAMES)
        _tree(tmp_path / "base", NAMES)
        base_src = _src(tmp_path / "base", "hypergumbo_core")
        path: list[str] = []
        with pytest.raises(SystemExit) as exc:
            mct.pin(script, "analysis", environ={"PYTHONPATH": str(base_src)}, path=path)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "measure-x.py" in err
        assert str(tmp_path / "t") in err and str(base_src) in err
        assert "HG_MEASURE_TREE=script" in err and "HG_MEASURE_TREE=pythonpath" in err
        assert path == [], "a refusal must not leave a half-pinned path"

    def test_a_single_module_file_on_pythonpath_also_conflicts(
        self, tmp_path: Path,
    ) -> None:
        script = _tree(tmp_path / "t", NAMES)
        other = tmp_path / "other"
        other.mkdir()
        (other / "hypergumbo_core.py").write_text("")
        with pytest.raises(SystemExit):
            mct.pin(script, "core", environ={"PYTHONPATH": str(other)}, path=[])

    def test_choice_script_pins_this_tree_over_the_conflict(self, tmp_path: Path) -> None:
        script = _tree(tmp_path / "t", NAMES)
        fake = tmp_path / "fake" / "hypergumbo_core"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("")
        path = [str(tmp_path / "fake")]
        pin = mct.pin(
            script, "core",
            environ={"PYTHONPATH": str(tmp_path / "fake"), "HG_MEASURE_TREE": "script"},
            path=path,
        )
        assert path[0] == str(_src(tmp_path / "t", "hypergumbo_core"))
        assert pin.choice == "script"

    def test_choice_pythonpath_pins_nothing(self, tmp_path: Path) -> None:
        script = _tree(tmp_path / "t", NAMES)
        fake = tmp_path / "fake" / "hypergumbo_core"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("")
        path = [str(tmp_path / "fake")]
        pin = mct.pin(
            script, "core",
            environ={"PYTHONPATH": str(tmp_path / "fake"),
                     "HG_MEASURE_TREE": "pythonpath"},
            path=path,
        )
        assert path == [str(tmp_path / "fake")]
        assert pin.choice == "pythonpath" and pin.pinned == ()

    def test_an_unknown_choice_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        script = _tree(tmp_path / "t", NAMES)
        with pytest.raises(SystemExit) as exc:
            mct.pin(script, "core", environ={"HG_MEASURE_TREE": "baseline"}, path=[])
        assert exc.value.code == 2
        assert "baseline" in capsys.readouterr().err

    def test_ignore_environment_means_pythonpath_cannot_conflict(
        self, tmp_path: Path,
    ) -> None:
        """``python -E``/``-I`` drop PYTHONPATH, so it selects nothing."""
        script = _tree(tmp_path / "t", NAMES)
        _tree(tmp_path / "base", NAMES)
        path: list[str] = []
        mct.pin(
            script, "core",
            environ={"PYTHONPATH": str(_src(tmp_path / "base", "hypergumbo_core"))},
            path=path, ignore_environment=True,
        )
        assert path == [str(_src(tmp_path / "t", "hypergumbo_core"))]

    def test_a_lone_copy_with_no_packages_pins_nothing(self, tmp_path: Path) -> None:
        """A script copied out of its tree has nothing to pin; the provenance
        line then says what the interpreter resolved instead."""
        script = tmp_path / "lone" / "scripts" / "measure-x.py"
        script.parent.mkdir(parents=True)
        script.write_text("")
        path: list[str] = []
        pin = mct.pin(script, "analysis", environ={}, path=path)
        assert path == [] and pin.pinned == ()


class TestAnnounce:
    def _pin(self, tree: Path, choice: str = "", scope: str = "core") -> object:
        return mct.Pin(script_tree=tree, scope=scope, choice=choice, pinned=())

    def test_names_the_tree_and_each_package(self, tmp_path: Path) -> None:
        _tree(tmp_path / "t", NAMES)
        out = io.StringIO()
        tree = mct.announce(
            self._pin(tmp_path / "t", scope="analysis"), "[x]", stream=out,
            path=[str(_src(tmp_path / "t", n)) for n in NAMES],
        )
        assert tree == tmp_path / "t"
        lines = out.getvalue().splitlines()
        assert lines[0].startswith("[x] code under test: ")
        assert str(tmp_path / "t") in lines[0] and "this script's tree" in lines[0]
        assert any("hypergumbo_lang_mainstream" in ln for ln in lines[1:])
        assert not any("hypergumbo_tracker" in ln for ln in lines), "out of scope"

    def test_says_when_the_tree_is_not_the_scripts_and_who_chose(
        self, tmp_path: Path,
    ) -> None:
        _tree(tmp_path / "base", NAMES)
        out = io.StringIO()
        mct.announce(
            self._pin(tmp_path / "t", choice="pythonpath"), "[x]", stream=out,
            path=[str(_src(tmp_path / "base", "hypergumbo_core"))],
        )
        first = out.getvalue().splitlines()[0]
        assert str(tmp_path / "base") in first
        assert "NOT this script's tree" in first and str(tmp_path / "t") in first
        assert "HG_MEASURE_TREE=pythonpath" in first

    def test_a_mixed_tree_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        _tree(tmp_path / "a", ["hypergumbo_core"])
        _tree(tmp_path / "b", ["hypergumbo_lang_mainstream"])
        out = io.StringIO()
        with pytest.raises(SystemExit) as exc:
            mct.announce(
                self._pin(tmp_path / "a", scope="analysis"), "[x]", stream=out,
                path=[str(_src(tmp_path / "a", "hypergumbo_core")),
                      str(_src(tmp_path / "b", "hypergumbo_lang_mainstream"))],
            )
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "MIXED" in err
        assert str(tmp_path / "a") in err and str(tmp_path / "b") in err
        assert out.getvalue() == "", "no provenance line for a run that will not happen"

    def test_core_that_does_not_resolve_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            mct.announce(
                self._pin(tmp_path / "t"), "[x]", stream=io.StringIO(),
                path=[str(tmp_path / "empty")],
            )
        assert exc.value.code == 2
        assert "hypergumbo_core" in capsys.readouterr().err

    def test_a_namespace_package_still_resolves_to_its_directory(
        self, tmp_path: Path,
    ) -> None:
        """A directory without ``__init__.py`` imports as a namespace package
        and has no ``origin``; it must still be located, not skipped."""
        (tmp_path / "ns" / "hypergumbo_core").mkdir(parents=True)
        out = io.StringIO()
        tree = mct.announce(
            self._pin(tmp_path / "t"), "[x]", stream=out,
            path=[str(tmp_path / "ns")],
        )
        assert tree == tmp_path / "ns"
