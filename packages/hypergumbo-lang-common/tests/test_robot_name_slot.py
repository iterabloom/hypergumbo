# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/robot.py
"""ADR-0036 Ruling 1: a Robot variable's id name slot is its name, ``${NAME}``.

The variable was minted by ``make_doc_symbol_ids`` from the bare ``NAME``,
which is both the id's name slot and the stable id's name input, while the
Symbol is named ``${NAME}``. The id now comes from the name; the stable id
keeps hashing the bare name (``make_doc_stable_id`` with the same arguments
``make_doc_symbol_ids`` passed), so it does not move.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_doc_stable_id
from hypergumbo_core.ir import sanitize_id_name_segment
from hypergumbo_lang_common.robot import analyze_robot

_SRC = (
    "*** Variables ***\n"
    "${NAME}    demo\n"
    "${OTHER}=    x\n\n"
    "*** Test Cases ***\n"
    "First Test\n    Log    ${NAME}\n\n"
    "*** Keywords ***\n"
    "My Keyword\n    Log    kw\n"
)


def _symbols(tmp_path: Path):
    (tmp_path / "suites").mkdir()
    (tmp_path / "suites" / "s.robot").write_text(_SRC)
    return analyze_robot(tmp_path).symbols


def test_the_fixture_emits_variables_tests_and_keywords(tmp_path: Path) -> None:
    kinds = {(s.kind, s.name) for s in _symbols(tmp_path)}
    assert {("variable", "${NAME}"), ("variable", "${OTHER}"),
            ("test", "First Test"), ("keyword", "My Keyword")} <= kinds


def test_every_name_slot_is_the_sanitized_name(tmp_path: Path) -> None:
    wrong = [(s.id, s.name) for s in _symbols(tmp_path)
             if s.kind != "file"
             and s.id.rsplit(":", 2)[1] != sanitize_id_name_segment(s.name)]
    assert wrong == []


def test_a_variables_stable_id_hashes_the_bare_name(tmp_path: Path) -> None:
    """Control: the stable id is still the doc formula over ``NAME``."""
    (var,) = [s for s in _symbols(tmp_path) if s.name == "${NAME}"]
    assert var.stable_id == make_doc_stable_id(
        "robot", "suites/s.robot", "variable", "NAME",
        var.span.start_line, var.span.end_line,
    )
