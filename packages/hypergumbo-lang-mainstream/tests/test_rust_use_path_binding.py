# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust call binds the function its ``use`` path names, whatever the file order (WI-lirik).

Found by re-taking WI-vosop's harness-vs-survey diff edge by edge on the
recorded aardvark-dns crate:

1. ``use crate::config::parse_configs; parse_configs(..)`` looked the callee up
   by its bare name, and the resolver's exact-name entry is whichever
   same-named function registered LAST. Under the walk order the recorded
   harness uses that was ``config/mod.rs``; under the sorted order a survey's
   FileIndex gives it was a ``#[cfg(test)]`` helper of the same name. One
   analyzer, two call graphs.
2. ``use aardvark_dns::config; config::parse_configs(..)`` inside that helper
   bound to the helper itself, and the survey then dropped it as a self-loop.
3. ``self.0.push(x)`` and ``self.items.push(x)`` -- a field of type ``Vec`` --
   bound to a first-party method named ``push`` (the enclosing one, or another
   type's), because the typed lookup ``Vec::push`` missed and the short-name
   fallback took any ``push``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core.analyze.base as analyze_base
from hypergumbo_lang_mainstream.rust import analyze_rust

_CARGO = '[package]\nname = "app"\nversion = "0.1.0"\n'


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _calls(root: Path) -> list[tuple[str, int, str]]:
    result = analyze_rust(root)
    return [
        (e.src.split(":")[-2], e.line, e.dst)
        for e in result.edges if e.edge_type == "calls"
    ]


def _order(monkeypatch: pytest.MonkeyPatch, reverse: bool) -> None:
    """Force discovery order: the bare-name defect showed in one order only."""
    original = analyze_base.find_files

    def ordered(repo_root, patterns, *args, **kwargs):
        return iter(sorted(original(repo_root, patterns, *args, **kwargs), reverse=reverse))

    monkeypatch.setattr(analyze_base, "find_files", ordered)


_SAME_NAME = {
    "Cargo.toml": _CARGO,
    "src/lib.rs": "pub mod config;\npub mod server;\n#[cfg(test)]\nmod test;\n",
    "src/config/mod.rs": "pub fn parse_configs(dir: &str) -> u8 { 0 }\n",
    "src/server.rs": (
        "use crate::config::parse_configs;\n\n"
        "pub fn run() -> u8 {\n    parse_configs(\"/etc\")\n}\n"
    ),
    "src/test/mod.rs": "pub mod test;\n",
    "src/test/test.rs": (
        "use crate::config;\n\n"
        "fn parse_configs(dir: &str) -> u8 {\n    config::parse_configs(dir)\n}\n"
    ),
}


@pytest.mark.parametrize("reverse", [False, True], ids=["sorted", "reversed"])
def test_an_imported_function_is_bound_by_its_use_path_in_either_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool,
) -> None:
    _write(tmp_path, _SAME_NAME)
    _order(monkeypatch, reverse)
    calls = _calls(tmp_path)
    run = [dst for src, _line, dst in calls if src == "run"]
    assert run, calls  # reach
    assert all(":src/config/mod.rs:" in dst for dst in run), run


@pytest.mark.parametrize("reverse", [False, True], ids=["sorted", "reversed"])
def test_a_call_through_a_used_module_does_not_bind_its_same_named_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool,
) -> None:
    _write(tmp_path, _SAME_NAME)
    _order(monkeypatch, reverse)
    helper = [dst for src, _line, dst in _calls(tmp_path) if src == "parse_configs"]
    assert helper and all(":src/config/mod.rs:" in dst for dst in helper), helper


_FIELDS = {
    "Cargo.toml": _CARGO,
    "src/lib.rs": (
        "pub struct List(Vec<u8>);\n"
        "pub struct Named { items: Vec<u8> }\n"
        "pub struct Node { left: Box<Node> }\n\n"
        "impl List {\n    pub fn push(&mut self, x: u8) {\n        self.0.push(x)\n    }\n}\n\n"
        "impl Named {\n    pub fn push(&mut self, x: u8) {\n        self.items.push(x)\n    }\n}\n\n"
        "impl Node {\n    pub fn insert(&mut self, x: u8) {\n        self.left.insert(x)\n    }\n}\n"
    ),
}


def test_a_field_of_an_external_type_does_not_bind_a_first_party_method(tmp_path: Path) -> None:
    """``push`` on a ``Vec`` is std's. With no first-party candidate left the
    call takes the external path, where ``push`` -- a generic-method name on a
    receiver whose module no ``use`` names (``Vec`` is the prelude's) -- is
    suppressed as before (INV-pamis); what must not happen is a first-party
    bind."""
    _write(tmp_path, _FIELDS)
    calls = _calls(tmp_path)
    assert ("Node.insert", 19) in {(src, line) for src, line, _dst in calls}  # reach: the file was walked
    for src, line in (("List.push", 7), ("Named.push", 13)):
        bound = [dst for s, ln, dst in calls if (s, ln) == (src, line) and ":src/lib.rs:" in dst]
        assert not bound, (src, line, bound)


def test_a_field_of_the_enclosing_type_still_reaches_its_method(tmp_path: Path) -> None:
    """Control: ``self.left.insert(x)`` with ``left: Box<Node>`` is recursion."""
    _write(tmp_path, _FIELDS)
    calls = {(src, line): dst for src, line, dst in _calls(tmp_path)}
    assert calls.get(("Node.insert", 19), "").endswith(":Node.insert:method"), calls


def test_a_field_typed_by_a_type_parameter_keeps_its_fallback(tmp_path: Path) -> None:
    """Control: ``backend: B`` has no type until the struct is instantiated, so
    it is not a known external type (firecracker's ``Vsock<B>`` with its test
    backend's ``as_raw_fd``): the short-name fallback still runs."""
    _write(tmp_path, {
        "Cargo.toml": _CARGO,
        "src/lib.rs": (
            "pub trait Backend { fn as_raw_fd(&self) -> i32; }\n"
            "pub struct TestBackend;\n"
            "impl Backend for TestBackend {\n    fn as_raw_fd(&self) -> i32 { 3 }\n}\n"
            "pub struct Vsock<B: Backend> { backend: B }\n"
            "impl<B: Backend> Vsock<B> {\n    pub fn fd(&self) -> i32 {\n        self.backend.as_raw_fd()\n    }\n}\n"
        ),
    })
    calls = {(src, line): dst for src, line, dst in _calls(tmp_path)}
    assert ":src/lib.rs:" in calls.get(("Vsock.fd", 9), ""), calls


def test_a_call_written_from_crate_binds_the_path_without_a_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = dict(_SAME_NAME)
    files["src/server.rs"] = "pub fn run() -> u8 {\n    crate::config::parse_configs(\"/etc\")\n}\n"
    _write(tmp_path, files)
    _order(monkeypatch, reverse=False)
    run = [dst for src, _line, dst in _calls(tmp_path) if src == "run"]
    assert run and all(":src/config/mod.rs:" in dst for dst in run), run


def test_a_field_typed_by_an_external_path_does_not_bind_a_first_party_method(tmp_path: Path) -> None:
    _write(tmp_path, {
        "Cargo.toml": _CARGO,
        "src/lib.rs": (
            "pub struct Queue { items: std::collections::VecDeque<u8> }\n\n"
            "impl Queue {\n    pub fn front(&self) -> Option<&u8> {\n        self.items.front()\n    }\n}\n"
        ),
    })
    bound = [dst for src, line, dst in _calls(tmp_path) if (src, line) == ("Queue.front", 5) and ":src/lib.rs:" in dst]
    assert not bound, bound
