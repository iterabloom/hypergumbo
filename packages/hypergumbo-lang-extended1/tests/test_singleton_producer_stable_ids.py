# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for LLVM IR, COBOL and Circom (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``. The floor key has
no scope and no parameters: two COBOL programs in one file that each declare a
paragraph ``MAIN-PARA`` or a data item ``WS-COUNT``, or two records that each
hold a ``CUST-NAME``, shared it, and every member of the group was null.

Each producer now mints the ADR-0035 formula:

* LLVM IR functions (``define``) and declarations (``declare``): the typed
  parameter list goes to ``meta["parameters"]`` and the id is
  ``overload_arity.declared_arity_stable_id`` over it (``@names`` are unique
  in a module, so the chain is the name);
* COBOL programs, sections, paragraphs and data items:
  ``declared_arity_stable_id`` with no arity, over the full scope chain --
  the enclosing programs (nesting read from ``END PROGRAM``), the section of a
  paragraph, and the group items above a data item by level number;
* Circom functions: ``compute_stable_id`` (parameter count and flags).

What is pinned, per language, on the analyzer's own output: every symbol holds
its own non-null id (matched by kind and position, never through a dict keyed
on name); the exact value; and the delete-an-earlier-sibling property (the
deleted lines are kept as blank lines so each survivor matches itself by line).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

from hypergumbo_core.analyze.base import (
    AnalysisResult, floor_stable_id_key,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    declared_arity_of_entries, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_extended1.circom import analyze_circom
from hypergumbo_lang_extended1.cobol import analyze_cobol
from hypergumbo_lang_extended1.llvm_ir import analyze_llvm_ir

Analyze = Callable[[Path], AnalysisResult]
Key = tuple[str, int, int]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _at(symbols: list[Symbol], kinds: set[str]) -> dict[Key, Symbol]:
    picked = [s for s in symbols if s.kind in kinds]
    by_pos = {(s.kind, s.span.start_line, s.span.start_col): s for s in picked}
    assert len(by_pos) == len(picked), "two symbols of one kind at one position"
    return by_pos


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], survivors: int,
) -> None:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert text.count(deleted) == 1
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _at(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _at(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert len(after) == survivors < len(before)  # reach
    for key, sym in after.items():
        assert sym.stable_id is not None, (key, sym.name)
        assert sym.stable_id == before[key].stable_id, (key, sym.name)


def _assert_pinned(
    by_pos: dict[Key, Symbol], lang: str, path: str,
    expected: dict[Key, tuple[str, str, Optional[Sequence[dict]]]],
) -> None:
    """``expected``: (kind, line, col) -> (name, chain hashed, parameter entries)."""
    assert set(by_pos) == set(expected)  # reach
    for key, (name, qualified, params) in expected.items():
        sym = by_pos[key]
        assert sym.name == name
        arity = declared_arity_of_entries(params) if params is not None else None
        assert sym.stable_id == declared_arity_stable_id(
            kind=sym.kind, lang=lang, path=path, name=name,
            qualified_name=qualified, arity=arity,
        ), key
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, name)
    ids = [s.stable_id for s in by_pos.values()]
    assert len(set(ids)) == len(ids)


# -- llvm_ir -------------------------------------------------------------------

_LL = (
    "@counter = global i32 0\n"
    "declare i32 @printf(ptr, ...)\n"
    "declare void @ext(i32 noundef, ptr)\n"
    "define dso_local i32 @add(i32 %a, i32 %b) {\n"
    "  %s = add i32 %a, %b\n"
    "  ret i32 %s\n"
    "}\n"
    "define i32 @main() {\n"
    "  %r = call i32 @add(i32 1, i32 2)\n"
    "  ret i32 %r\n"
    "}\n"
)


def test_llvm_ir_functions_and_declarations_hash_their_parameters(tmp_path: Path) -> None:
    syms = _symbols(analyze_llvm_ir, tmp_path, "src/m.ll", _LL)
    by_pos = _at(syms, {"function", "declaration"})
    printf = [parameter_entry("", "ptr"), parameter_entry("...", None, variadic=True)]
    ext = [parameter_entry("", "i32"), parameter_entry("", "ptr")]
    add = [parameter_entry("a", "i32"), parameter_entry("b", "i32")]
    _assert_pinned(by_pos, "llvm_ir", "src/m.ll", {
        ("declaration", 2, 0): ("printf", "printf", printf),
        ("declaration", 3, 0): ("ext", "ext", ext),
        ("function", 4, 0): ("add", "add", add),
        ("function", 8, 0): ("main", "main", []),
    })
    assert by_pos[("function", 4, 0)].meta == {"parameters": add}
    assert by_pos[("declaration", 2, 0)].meta == {"parameters": printf}
    assert by_pos[("function", 8, 0)].meta == {"parameters": []}
    _assert_deletion_moves_nothing(
        analyze_llvm_ir, tmp_path / "del", "src/m.ll", _LL,
        "declare i32 @printf(ptr, ...)\n", {"function", "declaration"}, survivors=3,
    )


# -- cobol ---------------------------------------------------------------------

_COB = (
    "       IDENTIFICATION DIVISION.\n"          # 1
    "       PROGRAM-ID. BANK.\n"
    "       DATA DIVISION.\n"
    "       WORKING-STORAGE SECTION.\n"
    "       01 CUSTOMER-REC.\n"                  # 5
    "          05 CUST-NAME PIC X(20).\n"
    "          05 CUST-ID PIC 9(5).\n"
    "       01 ACCOUNT-REC.\n"
    "          05 CUST-NAME PIC X(20).\n"
    "             88 NO-NAME VALUE SPACES.\n"    # 10
    "          05 BALANCE PIC 9(7)V99.\n"
    "       66 ALIAS-BAL RENAMES BALANCE.\n"
    "       77 WS-COUNT PIC 9(3).\n"
    "       PROCEDURE DIVISION.\n"
    "       MAIN-SECTION SECTION.\n"             # 15
    "       MAIN-PARA.\n"
    "           PERFORM INIT-PARA.\n"
    "           CALL \"AUDIT\".\n"
    "           STOP RUN.\n"
    "       INIT-PARA.\n"                        # 20
    "           MOVE 0 TO WS-COUNT.\n"
    "       OTHER-SECTION SECTION.\n"
    "       INIT-PARA.\n"
    "           MOVE 1 TO WS-COUNT.\n"
    "       IDENTIFICATION DIVISION.\n"          # 25
    "       PROGRAM-ID. AUDIT.\n"
    "       DATA DIVISION.\n"
    "       WORKING-STORAGE SECTION.\n"
    "       01 WS-COUNT PIC 9(3).\n"
    "       PROCEDURE DIVISION.\n"               # 30
    "       MAIN-PARA.\n"
    "           MOVE 2 TO WS-COUNT.\n"
    "           GOBACK.\n"
    "       END PROGRAM AUDIT.\n"
    "       END PROGRAM BANK.\n"                 # 35
    "       IDENTIFICATION DIVISION.\n"
    "       PROGRAM-ID. AUDIT.\n"
    "       PROCEDURE DIVISION.\n"
    "       MAIN-PARA.\n"
    "           GOBACK.\n"                       # 40
    "       END PROGRAM AUDIT.\n"
)
_COB_KINDS = {"program", "section", "paragraph", "data"}


def test_cobol_symbols_hash_the_program_section_and_group_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_cobol, tmp_path, "src/bank.cob", _COB)
    by_pos = _at(syms, _COB_KINDS)
    _assert_pinned(by_pos, "cobol", "src/bank.cob", {
        ("program", 1, 7): ("BANK", "BANK", None),
        ("data", 5, 7): ("CUSTOMER-REC", "BANK/CUSTOMER-REC", None),
        ("data", 6, 10): ("CUST-NAME", "BANK/CUSTOMER-REC/CUST-NAME", None),
        ("data", 7, 10): ("CUST-ID", "BANK/CUSTOMER-REC/CUST-ID", None),
        ("data", 8, 7): ("ACCOUNT-REC", "BANK/ACCOUNT-REC", None),
        ("data", 9, 10): ("CUST-NAME", "BANK/ACCOUNT-REC/CUST-NAME", None),
        ("data", 10, 13): ("NO-NAME", "BANK/ACCOUNT-REC/CUST-NAME/NO-NAME", None),
        ("data", 11, 10): ("BALANCE", "BANK/ACCOUNT-REC/BALANCE", None),
        ("data", 12, 7): ("ALIAS-BAL", "BANK/ACCOUNT-REC/ALIAS-BAL", None),
        ("data", 13, 7): ("WS-COUNT", "BANK/WS-COUNT", None),
        ("section", 15, 7): ("MAIN-SECTION", "BANK/MAIN-SECTION", None),
        ("paragraph", 16, 7): ("MAIN-PARA", "BANK/MAIN-SECTION/MAIN-PARA", None),
        ("paragraph", 20, 7): ("INIT-PARA", "BANK/MAIN-SECTION/INIT-PARA", None),
        ("section", 22, 7): ("OTHER-SECTION", "BANK/OTHER-SECTION", None),
        ("paragraph", 23, 7): ("INIT-PARA", "BANK/OTHER-SECTION/INIT-PARA", None),
        # AUDIT is nested in BANK (its END PROGRAM comes first); the second
        # AUDIT is a sibling of BANK.
        ("program", 25, 7): ("AUDIT", "BANK/AUDIT", None),
        ("data", 29, 7): ("WS-COUNT", "BANK/AUDIT/WS-COUNT", None),
        ("paragraph", 31, 7): ("MAIN-PARA", "BANK/AUDIT/MAIN-PARA", None),
        ("program", 36, 7): ("AUDIT", "AUDIT", None),
        ("paragraph", 39, 7): ("MAIN-PARA", "AUDIT/MAIN-PARA", None),
    })
    _assert_deletion_moves_nothing(
        analyze_cobol, tmp_path / "del", "src/bank.cob", _COB,
        "       01 CUSTOMER-REC.\n"
        "          05 CUST-NAME PIC X(20).\n"
        "          05 CUST-ID PIC 9(5).\n",
        _COB_KINDS, survivors=17,
    )


def test_cobol_program_without_end_program_closes_at_the_file(tmp_path: Path) -> None:
    """A lone program needs no ``END PROGRAM``; one file's open program does
    not leak into the next file's chain."""
    lone = (
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. LONE.\n"
        "       PROCEDURE DIVISION.\n       P1.\n           STOP RUN.\n"
    )
    (tmp_path / "b.cob").write_text(lone.replace("LONE", "OTHER"))
    syms = _symbols(analyze_cobol, tmp_path, "a.cob", lone)
    progs = sorted((s.path, s.name, s.stable_id) for s in syms if s.kind == "program")
    assert [(p, n) for p, n, _ in progs] == [("a.cob", "LONE"), ("b.cob", "OTHER")]
    for path, name, sid in progs:
        assert sid == declared_arity_stable_id(
            kind="program", lang="cobol", path=path, name=name,
            qualified_name=name, arity=None,
        )


# -- circom --------------------------------------------------------------------

_CIRCOM = (
    "pragma circom 2.0.0;\n"
    "function first(x) {\n"
    "    return x;\n"
    "}\n"
    "template T(n) {\n"
    "    signal input a;\n"
    "    signal b;\n"
    "    b <== a;\n"
    "}\n"
    "function run(x, y) {\n"
    "    return x + y;\n"
    "}\n"
    "function none() {\n"
    "    return 1;\n"
    "}\n"
)


def test_circom_functions_hash_their_parameter_count(tmp_path: Path) -> None:
    syms = _symbols(analyze_circom, tmp_path, "src/demo.circom", _CIRCOM)
    by_pos = _at(syms, {"function"})
    _assert_pinned(by_pos, "circom", "src/demo.circom", {
        ("function", 2, 0): ("first", "first", [parameter_entry("x")]),
        ("function", 10, 0): ("run", "run", [parameter_entry("x"), parameter_entry("y")]),
        ("function", 13, 0): ("none", "none", []),
    })
    # an intermediate signal (no input/output) carries no modifier; it used to
    # be emitted as ``modifiers=None``, which crashed the run's visibility fold
    signals = sorted((s.name, s.modifiers) for s in syms if s.kind == "variable")
    assert signals == [("a", ["input"]), ("b", [])]
    _assert_deletion_moves_nothing(
        analyze_circom, tmp_path / "del", "src/demo.circom", _CIRCOM,
        "function first(x) {\n    return x;\n}\n", {"function"}, survivors=2,
    )
