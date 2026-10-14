# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Python analyzer's repository walks terminate on a directory symlink cycle (INV-pivir).

After the supply_chain walks were fixed, arti still hung: the Python analyzer's
``_detect_source_roots`` (py.py) and ``py_deps._find_pyproject_files`` are the
same hand-written ``iterdir()`` stack walk, and ``Path.is_dir()`` follows
``maint/rust-maint-common/{maint,common} -> .`` forever (faulthandler:
pathlib.is_dir <- _detect_source_roots <- analyze_python). Both now ask
``discovery.walks_into``, which never descends into a directory symlink.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import _detect_source_roots
from hypergumbo_lang_mainstream.py_deps import _find_pyproject_files


@pytest.fixture
def deadline() -> Iterator[None]:
    def _expired(_signum: int, _frame: object) -> None:
        raise TimeoutError("the repository walk did not terminate")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(20)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def cyclic_repo(tmp_path: Path) -> Path:
    common = tmp_path / "maint" / "rust-maint-common"
    common.mkdir(parents=True)
    (common / "maint").symlink_to(".")
    (common / "common").symlink_to(".")
    pkg = tmp_path / "libs" / "tool" / "src" / "tool"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (tmp_path / "libs" / "tool" / "pyproject.toml").write_text(
        '[project]\nname = "tool"\n',
    )
    return tmp_path


def test_source_roots_terminate_on_a_self_link(
    deadline: None, cyclic_repo: Path,
) -> None:
    assert _detect_source_roots(cyclic_repo) == [cyclic_repo / "libs" / "tool" / "src"]


def test_pyproject_walk_terminates_on_a_self_link(
    deadline: None, cyclic_repo: Path,
) -> None:
    assert _find_pyproject_files(cyclic_repo) == [
        cyclic_repo / "libs" / "tool" / "pyproject.toml",
    ]
