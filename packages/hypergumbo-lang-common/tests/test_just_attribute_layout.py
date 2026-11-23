# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
"""A just recipe's attributes are hashed without their layout.

The untyped formula hashes its ``decorators`` slot, and just writes a
recipe's attributes there as written (``[group('ci')]`` -> ``group('ci')``),
so ``[group( 'ci' )]`` was another id (WI-sidus). ``assemble_stable_id`` now
hashes the slot ``without_layout``: whitespace that is layout goes, a text
without whitespace is unchanged, so no other id moves.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.just import analyze_just


def _ids(tmp_path: Path, attribute: str) -> dict[str, str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "justfile").write_text(f"{attribute}\nbuild:\n    echo hi\n")
    return {s.name: s.stable_id for s in analyze_just(tmp_path).symbols}


def test_an_attribute_in_another_spacing_keeps_the_id(tmp_path: Path) -> None:
    tight = _ids(tmp_path, "[group('ci')]")
    spaced = _ids(tmp_path, "[group( 'ci' )]")
    assert tight["build"] is not None  # reach
    assert tight == spaced


def test_another_attribute_is_another_id(tmp_path: Path) -> None:
    assert _ids(tmp_path, "[group('ci')]")["build"] != _ids(tmp_path, "[group('cd')]")["build"]
    assert _ids(tmp_path, "[group('c i')]")["build"] != _ids(tmp_path, "[group('c  i')]")["build"]
