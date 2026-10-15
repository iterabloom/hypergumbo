# SPDX-License-Identifier: AGPL-3.0-or-later
"""Partial-install warnings count only first-party code (WI-dipag, ADR-0004 3a).

Production-path tests: a whole survey over a fixture, with the C and Go
analyzers this package provides, and the warnings captured as the CLI emits
them. On hypergumbo itself the vendored tree-sitter grammar C fired "CGO-LINKER
linker found 53 C/C++ function implementations but 0 Go cgo calls" and the
matching RUBY-FFI warning; committed vendored code (tier 3) must not speak for
the project, while first-party C with no cgo calls is still reported.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from hypergumbo_core.cli import run_survey


def _survey_warnings(repo: Path, files: dict[str, str]) -> list[str]:
    """The survey's stderr lines (the CLI writes the warnings there)."""
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        run_survey(
            repo, repo / "out.json", progress=False,
            include_sketch_precomputed=False, enable_handler_slices=False,
        )
    return err.getvalue().splitlines()


_GO = "package main\n\nfunc main() {}\n"
_C = "int helper(int x) { return x + 1; }\nint other(void) { return 2; }\n"


def test_vendored_c_does_not_fire_linker_warnings(tmp_path: Path) -> None:
    messages = _survey_warnings(tmp_path, {
        "main.go": _GO,
        "third_party/lib/helper.c": _C,
    })
    assert not [m for m in messages if m.startswith(("CGO linker", "RUBY-FFI linker"))], messages


def test_first_party_c_still_fires_the_cgo_warning(tmp_path: Path) -> None:
    messages = _survey_warnings(tmp_path, {
        "main.go": _GO,
        "native/helper.c": _C,
    })
    cgo = [m for m in messages if m.startswith("CGO linker")]
    assert len(cgo) == 1, messages
    assert "2 C/C++ function implementations" in cgo[0]
