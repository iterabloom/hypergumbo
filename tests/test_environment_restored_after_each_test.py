# SPDX-License-Identifier: AGPL-3.0-or-later
"""The root conftest's ``_hg_environment_restored_after_test`` is needed and works.

``hypergumbo_core.cli`` turns ``--backend`` into ``HYPERGUMBO_RUST_ANALYZER``
and ``HYPERGUMBO_SCIP_PYTHON`` in ``os.environ``, for the process. A test
that calls ``main`` in-process leaves them set, and ``monkeypatch.delenv(name,
raising=False)`` does not help: for a variable that was absent it records
nothing to undo. Every later test of that xdist worker then ran with a
backend switched on or off (WI-holuj: with ``HYPERGUMBO_SCIP_PYTHON=1``,
21 tests of three files fail; after ``test_backend_cli_flag.py`` the
environment held ``HYPERGUMBO_RUST_ANALYZER=1``). Which tests failed depended
on how xdist spread the tests.

This module has NO fixture of its own that restores the environment, on
purpose. The tests below change it and return. The module-scoped probe's
teardown runs after the last test of this module on this worker -- after every
function-scoped teardown, the conftest's included -- and fails if anything
they set is still there. Remove the conftest fixture and this module errors.
"""

# covers: conftest.py
from __future__ import annotations

import os
from typing import Iterator
from unittest.mock import patch

import pytest

from hypergumbo_core.cli import main

_SET_HERE = ("HG_LEAK_PROBE_SET", "HYPERGUMBO_RUST_ANALYZER", "HYPERGUMBO_SCIP_PYTHON")
_BEFORE = {name: os.environ.get(name) for name in _SET_HERE}


@pytest.fixture(scope="module")
def _probe_after_module() -> Iterator[None]:
    yield
    after = {name: os.environ.get(name) for name in _BEFORE}
    assert after == _BEFORE, f"the environment leaked past its test: {after} != {_BEFORE}"


@pytest.mark.usefixtures("_probe_after_module")
def test_a_test_that_sets_a_variable_and_returns() -> None:
    os.environ["HG_LEAK_PROBE_SET"] = "1"


@pytest.mark.usefixtures("_probe_after_module")
def test_the_backend_flag_called_in_process() -> None:
    """The leak as it happened: ``--backend tree-sitter`` writes both
    backends' variables (an explicit off) for the rest of the process."""
    with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
        assert main(["--backend", "tree-sitter", "cache-status", "--quiet"]) == 0
    assert os.environ.get("HYPERGUMBO_SCIP_PYTHON") == "0"
