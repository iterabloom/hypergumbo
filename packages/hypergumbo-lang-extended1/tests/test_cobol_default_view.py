# SPDX-License-Identifier: AGPL-3.0-or-later
"""COBOL sections and paragraphs survive the default view (INV-siza).

The noise filter used to drop ``kind="section"`` and ``kind="paragraph"`` in
every language, because both are document structure in markdown / rst / INI.
In COBOL they are the callables PERFORM targets, so a COBOL program came out
of a default survey with no procedures and no call edges at all. A KDL node
with children (``kind="section"`` there too) is document structure and is
still dropped, which pins the language scope from the other side.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/noise_filter.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map

_BANK = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. BANK.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-BALANCE PIC 9(5) VALUE 0.
       PROCEDURE DIVISION.
       MAIN-SECTION SECTION.
       MAIN-PARA.
           PERFORM DEPOSIT-PARA.
           PERFORM REPORT-SECTION.
           STOP RUN.
       DEPOSIT-PARA.
           ADD 10 TO WS-BALANCE.
       REPORT-SECTION SECTION.
       REPORT-PARA.
           DISPLAY WS-BALANCE.
"""

_KDL = """\
server {
    port 8080
}
"""


def _survey(tmp_path: Path) -> dict:
    (tmp_path / "bank.cob").write_text(_BANK)
    (tmp_path / "config.kdl").write_text(_KDL)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def test_cobol_procedures_and_their_calls_survive_the_default_view(tmp_path: Path) -> None:
    data = _survey(tmp_path)
    cobol = {(n["kind"], n["name"]): n["id"] for n in data["nodes"]
             if n.get("language") == "cobol"}
    assert ("section", "MAIN-SECTION") in cobol
    assert ("section", "REPORT-SECTION") in cobol
    assert ("paragraph", "MAIN-PARA") in cobol
    assert ("paragraph", "DEPOSIT-PARA") in cobol
    calls = {(e["src"], e["dst"]) for e in data["edges"] if e["type"] == "calls"}
    main = cobol[("paragraph", "MAIN-PARA")]
    assert (main, cobol[("paragraph", "DEPOSIT-PARA")]) in calls
    assert (main, cobol[("section", "REPORT-SECTION")]) in calls


def test_a_kdl_section_is_still_document_structure(tmp_path: Path) -> None:
    data = _survey(tmp_path)
    assert not [n for n in data["nodes"]
                if n.get("language") == "kdl" and n["kind"] == "section"]
