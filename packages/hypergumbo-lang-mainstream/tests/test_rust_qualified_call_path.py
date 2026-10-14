# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fully-qualified Rust call path is split into its owner and its name.

INV-duzom. With no ``use`` alias to consult, the unresolved-call path kept the
whole path in the NAME slot and put the ``external`` sentinel in the module
slot, so ``std::io::stdin()`` emitted ``rust:external:0-0:std::io::stdin`` and
matched no catalogue row, while ``io::stdin()`` after ``use std::io;`` resolved.
The same file, two spellings, two answers.

WHY A SPLIT IS SAFE HERE. In CALL position the last path segment is the callee
and everything before it is the item that owns it: a module
(``std::io::stdin``), a type (``std::fs::File::open``) or an enum
(``Option::Some``). That is exactly the (module, name) pair ADR-0051 asks the
slots to carry. The split is limited to plain ``a::b::c`` paths, so a
turbofish or a qualified-self path (``<T as Trait>::m``) is left as it was, and
to paths rooted at ``std`` / ``core`` / ``alloc`` (or a leading ``::``), which
are absolute by construction. A bare ``Type::method`` is left alone: an
unimported ``Command::new`` is often a project type, and ``Command`` in the
module slot would be paired with ``std::process::Command``.

The same defect sat one level down in the ALIASED arm: after ``use std::fs;``,
``fs::File::open`` became module ``std::fs`` with name ``File::open``, which
matches no row either; it is split the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.rust import analyze_rust

_SOURCE = """use std::io;
use std::fs;

fn full_stdin() { let _ = std::io::stdin(); }
fn full_open() { let _ = std::fs::File::open("/x"); }
fn alias_stdin() { let _ = io::stdin(); }
fn alias_deep() { let _ = fs::File::open("/x"); }
fn rel() { let _ = crate::util::helper(); }
fn qself() { let _ = <Vec<u8> as Default>::default(); }
fn bare_type() { let _ = Command::new("ls"); }
fn main() {}
"""


@pytest.fixture(scope="module")
def dsts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    repo = tmp_path_factory.mktemp("repo")
    (repo / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text(_SOURCE)
    analysis = analyze_rust(repo)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[str, list[str]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls":
            out.setdefault(names.get(e.src, e.src), []).append(e.dst)
    return out


@pytest.mark.parametrize(("fn", "expected"), [
    ("full_stdin", "rust:std::io:0-0:stdin:"),
    ("full_open", "rust:std::fs::File:0-0:open:"),
    ("alias_stdin", "rust:std::io:0-0:stdin:"),
    ("alias_deep", "rust:std::fs::File:0-0:open:"),
])
def test_the_path_is_split_into_owner_and_name(
    dsts: dict[str, list[str]], fn: str, expected: str,
) -> None:
    assert any(d.startswith(expected) for d in dsts[fn]), dsts[fn]


def _module_slot(dst: str) -> str:
    return dst.split(":", 1)[1].split(":0-0:", 1)[0]


def test_the_split_reaches_the_boundary_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the item, through ``io-boundaries`` itself. Before the fix
    only the aliased spelling produced an I/O chain; a bare ``classify_call``
    on the id string is NOT the instrument, since it already matched."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    (repo / "src" / "main.rs").write_text(_SOURCE)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["io-boundaries", str(repo), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    callers = {
        (b, chain["io_edge_src"].split(":")[-2])
        for b, entry in report["boundaries"].items()
        for chain in entry["chains"]
    }
    assert ("ipc_recv", "full_stdin") in callers, callers
    assert ("fs_read", "full_open") in callers, callers
    assert ("ipc_recv", "alias_stdin") in callers, callers


def test_a_crate_relative_path_is_not_split(dsts: dict[str, list[str]]) -> None:
    assert [_module_slot(d) for d in dsts["rel"]] == ["external"], dsts["rel"]


def test_a_qualified_self_path_is_not_split(dsts: dict[str, list[str]]) -> None:
    assert all("Default" not in _module_slot(d) for d in dsts.get("qself", [])), dsts.get("qself")


def test_a_bare_type_path_is_not_split(dsts: dict[str, list[str]]) -> None:
    """The INV-kipor hazard: ``Command`` alone must not become a module slot."""
    assert all(_module_slot(d) != "Command" for d in dsts.get("bare_type", [])), dsts.get("bare_type")
