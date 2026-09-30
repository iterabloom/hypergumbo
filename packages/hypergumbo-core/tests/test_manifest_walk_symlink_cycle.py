# SPDX-License-Identifier: AGPL-3.0-or-later
"""The manifest walks terminate on a directory symlink cycle (INV-pivir).

arti carries ``maint/rust-maint-common/maint -> .`` and ``.../common -> .``:
two links back to their own parent at every level. The two stack walks in
``supply_chain`` pushed every ``entry.is_dir()`` -- which follows links -- with
no visited set, so the walk was exponential and ``verify-claims`` sat at 100%
CPU for 30+ minutes before any analysis ran (faulthandler: pathlib.is_file <-
collect_first_party_package_names <- cmd_verify_claims).

The walk now does what ``Path.rglob`` and discovery do: it does not descend
into a directory symlink. A symlinked manifest FILE is still read.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from pathlib import Path

import pytest

from hypergumbo_core.supply_chain import (
    collect_first_party_package_names,
    collect_workspace_package_names,
)


@pytest.fixture
def deadline() -> Iterator[None]:
    """Fail in seconds rather than hang the suite if the walk loops."""
    def _expired(_signum: int, _frame: object) -> None:
        raise TimeoutError("the manifest walk did not terminate")

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
    crate = tmp_path / "crates" / "tor-proto"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "tor-proto"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "My_Tool"\n')
    return tmp_path


def test_first_party_names_terminate_on_a_self_link(
    deadline: None, cyclic_repo: Path,
) -> None:
    assert "tor-proto" in collect_first_party_package_names(cyclic_repo)


def test_workspace_names_terminate_on_a_self_link(
    deadline: None, cyclic_repo: Path,
) -> None:
    assert collect_workspace_package_names(cyclic_repo) == {"my-tool"}


def test_a_symlinked_directory_is_not_entered_but_a_linked_file_is_read(
    deadline: None, tmp_path: Path,
) -> None:
    """THE CONTROL on what the walk still sees: the same rule rglob follows."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "Cargo.toml").write_text('[package]\nname = "inside-real"\n')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Cargo.toml").write_text('[package]\nname = "only-via-link"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "linked_dir").symlink_to(elsewhere)
    (repo / "Cargo.toml").symlink_to(real / "Cargo.toml")
    assert collect_first_party_package_names(repo) == {"inside-real"}
