# SPDX-License-Identifier: AGPL-3.0-or-later
"""The conftest's ``_hg_no_declared_test_inputs_after_test`` is needed and works.

``declared_test_inputs`` holds one process-wide declaration (ADR-0004 point
10). This module has NO fixture of its own that resets it, on purpose: the
test below installs a declaration and returns without restoring it, which is
what a careless test, script helper or library call does. The module-scoped
probe's teardown runs after the last test of this module on this xdist
worker -- after every function-scoped teardown, the conftest's included --
and fails if the declaration is still installed. Remove the restore from the
conftest fixture and this module errors.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from hypergumbo_core.declared_test_inputs import Declaration, active_declaration, install_declaration
from hypergumbo_core.paths import classify_test_file


@pytest.fixture(scope="module")
def _probe_after_module() -> Iterator[None]:
    yield
    leaked = active_declaration()
    assert leaked is None, f"a declaration leaked past its test: {leaked!r}"


@pytest.mark.usefixtures("_probe_after_module")
def test_a_test_that_leaves_a_declaration_installed() -> None:
    install_declaration(Declaration(dirs=("internal/e2e",)))
    assert classify_test_file("internal/e2e/main.go") == "declared_test_input"
