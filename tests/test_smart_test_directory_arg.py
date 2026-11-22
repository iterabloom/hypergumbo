# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-birog: a directory on the command line is an explicit run.

THE BUG. smart-test called an argument an explicit test target only if it
matched ``*.py*`` or ``*tests/*``. ``tests`` or ``packages/x/tests`` (no
trailing slash) matched neither, so the run SELECTED for itself -- a whole-
repository slice when no stored graph answers, and a rewrite of the committed
``.ci/affected-tests.txt`` -- and then passed the directory to pytest on top
of the selection, which ran the selection plus the whole directory.

THE RULE. pytest reads every argument that is neither an option nor an
option's value as a path to collect, so any such argument makes the run
explicit: a file, a directory, a ``path::node``, a path that does not exist
(pytest then says "not found" at once, instead of after a slice). An option's
VALUE is not a target, even when it names an existing directory: ``-k tests``,
``--basetemp DIR``, ``--rootdir DIR``, a value-taking short option at the end
of a cluster (``-xk tests``). The options that take a value are listed in the
script (``pytest_option_takes_value``).

THE HARNESS is ``test_smart_test_zero_collect``'s: the shipped script in a
throwaway git repo, real pytest, a stub stable hypergumbo that logs each call
(``_hg_calls``) and a stub mypy ratchet. The manifest is seeded with a
sentinel before each run, so a rewrite shows.
"""

# covers: scripts/smart-test
from __future__ import annotations

import re
from pathlib import Path

from test_smart_test_zero_collect import (
    _commit,
    _fixture_repo,
    _hg_calls,
    _smart_test,
)

SENTINEL = "# sentinel manifest: an explicit run leaves this alone\n"
SOURCE = "packages/p/src/pmod/mod.py"


def _repo(tmp_path: Path) -> Path:
    repo = _fixture_repo(tmp_path, {
        "tests/test_root.py": "def test_root_dir():\n    assert True\n",
        "packages/p/tests/test_pkg.py": "def test_pkg_dir():\n    assert True\n",
        SOURCE: "VALUE = 1\n",
        "basetemp_dir/.keep": "",
    })
    # A change, so a self-selected run has something to select for.
    (repo / "tests" / "test_root.py").write_text(
        "def test_root_dir():\n    assert True\n\n# edited\n"
    )
    _commit(repo)
    (repo / ".ci").mkdir(exist_ok=True)
    (repo / ".ci" / "affected-tests.txt").write_text(SENTINEL)
    return repo


def _ran(repo: Path) -> set[str]:
    """The names of the tests this run executed, from its junit report."""
    report = (repo / ".ci" / "pytest-results.xml").read_text()
    return set(re.findall(r'<testcase [^>]*?\sname="([^"]+)"', report))


def _manifest(repo: Path) -> str:
    return (repo / ".ci" / "affected-tests.txt").read_text()


def _assert_explicit(repo: Path, tmp_path: Path, arg: str, expected: set[str]) -> None:
    result = _smart_test(repo, tmp_path, arg)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert _hg_calls(tmp_path) == [], (
        f"`smart-test {arg}` asked hypergumbo for a slice\n{out}"
    )
    assert _manifest(repo) == SENTINEL, (
        f"`smart-test {arg}` rewrote the committed manifest\n{out}"
    )
    assert _ran(repo) == expected, (
        f"`smart-test {arg}` ran {_ran(repo)}, not exactly {expected}\n{out}"
    )


def test_a_root_test_directory_is_an_explicit_run(tmp_path: Path) -> None:
    _assert_explicit(_repo(tmp_path), tmp_path, "tests", {"test_root_dir"})


def test_a_package_test_directory_is_an_explicit_run(tmp_path: Path) -> None:
    _assert_explicit(
        _repo(tmp_path), tmp_path, "packages/p/tests", {"test_pkg_dir"}
    )


def test_a_node_id_is_an_explicit_run(tmp_path: Path) -> None:
    _assert_explicit(
        _repo(tmp_path), tmp_path,
        "packages/p/tests/test_pkg.py::test_pkg_dir", {"test_pkg_dir"},
    )


def _assert_self_selected(repo: Path, tmp_path: Path, *args: str) -> None:
    result = _smart_test(repo, tmp_path, *args)
    out = result.stdout + result.stderr
    manifest = _manifest(repo)
    assert manifest != SENTINEL and "# Mode: targeted" in manifest, (
        f"`smart-test {' '.join(args)}` was not a self-selected run: an option's "
        f"value that names an existing directory flipped it to explicit\n{out}"
    )


def test_an_option_value_naming_a_directory_does_not_make_it_explicit(
    tmp_path: Path,
) -> None:
    """`-k tests`: `tests` is the keyword expression, not a path."""
    _assert_self_selected(_repo(tmp_path), tmp_path, "-k", "tests")


def test_a_cluster_ending_in_a_value_option_does_not_make_it_explicit(
    tmp_path: Path,
) -> None:
    """`-xk tests`: argparse reads `-x -k tests`."""
    _assert_self_selected(_repo(tmp_path), tmp_path, "-xk", "tests")


def test_a_long_option_value_naming_a_directory_does_not_make_it_explicit(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _assert_self_selected(repo, tmp_path, "--basetemp", str(repo / "basetemp_dir"))
