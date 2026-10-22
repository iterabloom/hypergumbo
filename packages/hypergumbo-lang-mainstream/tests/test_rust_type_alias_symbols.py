# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust ``type`` alias is a declaration, so it is a Symbol (WI-bamar).

rust.py emitted nothing for a ``type_item``: on aardvark-dns two of the three
declarations the SCIP arm paired with no incumbent record were type aliases
(``pub type AardvarkResult<T> = Result<T, AardvarkError>;``), and every
consumer asking "what is declared at this line" -- slice anchoring, the
ADR-0057 merge, dead code -- got nothing. The third, a ``static``, is emitted
since WI-jusus. Kind ``type_alias``, the registry's "Type alias declaration";
module level only, like a const: an impl's ``type Item = u32;`` is an
associated type, not an alias declaration.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.rust import analyze_rust


def _aliases(tmp_path: Path, source: str) -> list:
    (tmp_path / "lib.rs").write_text(source)
    return [s for s in analyze_rust(tmp_path).symbols if s.kind == "type_alias"]


def test_a_module_level_type_alias_is_a_symbol(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, (
        "pub type AardvarkResult<T> = Result<T, AardvarkError>;\n"
        "mod server {\n    type ThreadHandleMap<Ip> = Vec<Ip>;\n}\n"
    ))
    assert sorted((a.name, a.span.start_line) for a in aliases) == [
        ("AardvarkResult", 1), ("ThreadHandleMap", 3),
    ]
    by_name = {a.name: a for a in aliases}
    assert by_name["AardvarkResult"].is_exported
    assert not by_name["ThreadHandleMap"].is_exported
    assert by_name["AardvarkResult"].signature == "Result<T, AardvarkError>"
    assert by_name["ThreadHandleMap"].qualified_name == "server::ThreadHandleMap"
    assert all(_CANONICAL_STABLE_ID_PATTERN.match(a.stable_id) for a in aliases)
    assert all(a.id.endswith(":type_alias") for a in aliases)


def test_an_associated_type_is_not_an_alias_declaration(tmp_path: Path) -> None:
    assert _aliases(tmp_path, (
        "struct S;\nimpl Iterator for S {\n    type Item = u32;\n"
        "    fn next(&mut self) -> Option<u32> { None }\n}\n"
    )) == []
