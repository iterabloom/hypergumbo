# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-lopus: a function declared in an ``extern`` block is a symbol.

The Rust analyzer extracted no symbol for a ``function_signature_item`` inside
a ``foreign_mod_item`` (it handled that node only when a trait owned it), so a
call to a foreign declaration had nothing to resolve to. It stayed unresolved
with the ``external`` module slot, and the catalogue then matched it by SHORT
NAME: loro's wasm_bindgen ``Date.now()`` binding was reported as
``std::time::Instant.now`` -- a Rust type the code never touches.

These tests pin the declaration (a symbol, with the foreign binding recorded),
the resolution (the call binds to it), and the consequence the item is about
(io-boundaries no longer names a Rust primitive for it), through the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.rust import analyze_rust

_FFI = """extern "C" { fn now() -> f64; fn getpid() -> i32; }
fn native() -> f64 { unsafe { now() } }
fn pid() -> i32 { unsafe { getpid() } }
"""

_WASM = """use wasm_bindgen::prelude::wasm_bindgen;

#[wasm_bindgen]
extern "C" {
    #[wasm_bindgen(js_namespace = Date)]
    pub fn now() -> f64;
    #[wasm_bindgen(js_namespace = console, js_name = log)]
    fn log_str(s: &str);
}

#[link(name = "m")]
extern {
    #[link_name = "cos"]
    fn c_cos(x: f64) -> f64;
}

extern "system" { fn GetTickCount() -> u32; }

#[wasm_bindgen(module = "/js/clock.js")]
extern "C" {
    #[wasm_bindgen(js_namespace = ["window", "performance"])]
    fn perf_now() -> f64;
}

pub fn stamp() -> f64 { now() }
"""


def _repo(tmp_path: Path, source: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    (repo / "src" / "lib.rs").write_text(source)
    return repo


def _by_name(result, name):
    return [s for s in result.symbols if s.name == name]


class TestForeignDeclarationIsASymbol:
    def test_each_declaration_is_a_function_symbol(self, tmp_path: Path) -> None:
        result = analyze_rust(_repo(tmp_path, _FFI))
        for name in ("now", "getpid"):
            syms = _by_name(result, name)
            assert len(syms) == 1, [s.id for s in result.symbols]
            assert syms[0].kind == "function"
            assert syms[0].span.start_line == 1

    def test_default_abi_is_c_and_a_string_abi_is_kept(self, tmp_path: Path) -> None:
        result = analyze_rust(_repo(tmp_path, _WASM))
        cos = _by_name(result, "c_cos")[0]
        tick = _by_name(result, "GetTickCount")[0]
        assert cos.meta["ffi_import"]["abi"] == "C"
        assert tick.meta["ffi_import"]["abi"] == "system"

    def test_wasm_bindgen_namespace_and_js_name_are_recorded(self, tmp_path: Path) -> None:
        result = analyze_rust(_repo(tmp_path, _WASM))
        now = _by_name(result, "now")[0]
        log = _by_name(result, "log_str")[0]
        assert now.meta["ffi_import"] == {"abi": "C", "js_namespace": "Date"}
        assert log.meta["ffi_import"] == {
            "abi": "C", "js_namespace": "console", "foreign_name": "log",
        }

    def test_link_library_and_link_name_are_recorded(self, tmp_path: Path) -> None:
        result = analyze_rust(_repo(tmp_path, _WASM))
        cos = _by_name(result, "c_cos")[0]
        assert cos.meta["ffi_import"] == {
            "abi": "C", "link": "m", "foreign_name": "cos",
        }

    def test_a_wasm_module_and_a_list_namespace_are_recorded(self, tmp_path: Path) -> None:
        """``js_namespace = ["window", "performance"]`` is the JS path
        ``window.performance``; ``module`` on the block names the JS module."""
        result = analyze_rust(_repo(tmp_path, _WASM))
        perf = _by_name(result, "perf_now")[0]
        assert perf.meta["ffi_import"] == {
            "abi": "C", "js_module": "/js/clock.js",
            "js_namespace": "window.performance",
        }

    def test_a_rust_2024_unsafe_extern_block_declares_its_functions(
        self, tmp_path: Path,
    ) -> None:
        """Rust 2024's ``unsafe extern "C" { pub safe fn ... }``. tree-sitter-rust
        0.24.0 could not parse it (a top-level ``function_signature_item`` under
        an ERROR, so nothing was emitted); 0.24.2, the floor since, parses it as a
        ``foreign_mod_item`` -- only the ``safe`` qualifier is an ERROR leaf -- so
        the function is a foreign symbol like any other (WI-kipun's gap closes
        with the grammar)."""
        source = 'unsafe extern "C" { pub safe fn getpid() -> i32; }\n'
        result = analyze_rust(_repo(tmp_path, source))
        (sym,) = _by_name(result, "getpid")
        assert sym.kind == "function"
        assert sym.meta["ffi_import"] == {"abi": "C"}

    def test_a_signature_outside_any_block_is_no_foreign_symbol(
        self, tmp_path: Path,
    ) -> None:
        """A bare ``fn f();`` at top level is a ``function_signature_item`` that
        no extern block declares, so it is no foreign symbol."""
        result = analyze_rust(_repo(tmp_path, "fn getpid() -> i32;\n"))
        assert not any(
            "ffi_import" in (s.meta or {}) for s in _by_name(result, "getpid")
        )

    def test_a_trait_signature_is_still_a_trait_method(self, tmp_path: Path) -> None:
        """The foreign arm must not capture the WI-duguk trait-contract arm."""
        result = analyze_rust(_repo(tmp_path, "trait T { fn run(&self); }\n"))
        assert [s.kind for s in _by_name(result, "T::run")] == ["method"]
        assert not _by_name(result, "run")


class TestTheCallBindsToTheDeclaration:
    def test_a_bare_call_resolves_to_the_foreign_declaration(self, tmp_path: Path) -> None:
        result = analyze_rust(_repo(tmp_path, _FFI))
        ids = {s.name: s.id for s in result.symbols}
        calls = {
            (e.src, e.dst) for e in result.edges if e.edge_type == "calls"
        }
        assert (ids["native"], ids["now"]) in calls, calls
        assert (ids["pid"], ids["getpid"]) in calls, calls

    def test_a_declaration_after_a_same_named_module_function_still_binds(
        self, tmp_path: Path,
    ) -> None:
        """The item's second manifestation: with ``mod helpers { pub fn now() }``
        in the file, the call used to RESOLVE to ``helpers::now`` -- the only
        same-named symbol, because the real callee had none."""
        source = _FFI + "mod helpers { pub fn now() -> u64 { 7 } }\nuse helpers::*;\n"
        result = analyze_rust(_repo(tmp_path, source))
        # By SPAN, not by name: two symbols are named ``now`` here, and a
        # name-keyed lookup would make this assertion vacuously true.
        foreign = [s for s in _by_name(result, "now") if s.span.start_line == 1]
        helper = [s for s in _by_name(result, "now") if s.span.start_line == 4]
        assert len(foreign) == 1 and len(helper) == 1, [s.id for s in result.symbols]
        native = _by_name(result, "native")[0].id
        dsts = {e.dst for e in result.edges if e.edge_type == "calls" and e.src == native}
        assert dsts == {foreign[0].id}, dsts


    def test_a_call_inside_the_module_binds_to_the_module_item(
        self, tmp_path: Path,
    ) -> None:
        """Control for the test above: the preference is the CALLER's module,
        not "the root wins". Inside ``helpers``, ``now()`` is ``helpers::now``."""
        source = _FFI + (
            "mod helpers { pub fn now() -> u64 { 7 }\n"
            "    pub fn h() -> u64 { now() } }\n"
        )
        result = analyze_rust(_repo(tmp_path, source))
        helper = [s for s in _by_name(result, "now") if s.span.start_line == 4]
        h = _by_name(result, "h")[0].id
        dsts = {e.dst for e in result.edges if e.edge_type == "calls" and e.src == h}
        assert dsts == {helper[0].id}, dsts


class TestOnlyABareCallReachesADeclaration:
    """The A/B on loro found the symbol acting as a MAGNET: the one ``now`` in
    the repository bound every ``Instant::now()`` / ``SystemTime::now()`` -- 44
    real clock reads lost. A foreign declaration has no owner, so neither a
    ``Type::`` path nor a receiver reaches it; only its bare name, or the
    module path it is declared under, does."""

    _LIB = (
        "use std::time::SystemTime;\n"
        'extern "C" { fn now() -> f64; }\n'
        'mod ffi { extern "C" { pub fn getpid() -> i32; } }\n'
        "fn a() -> f64 { unsafe { now() } }\n"
        "fn b() { let _ = SystemTime::now(); }\n"
        "fn c() -> i32 { unsafe { ffi::getpid() } }\n"
    )
    _OTHER = (
        "use std::time::Instant;\n"
        "fn d() { let _ = Instant::now(); }\n"
        "fn e(x: Thing) { x.now(); }\n"
    )

    def _calls(self, tmp_path: Path) -> dict[str, set[str]]:
        repo = _repo(tmp_path, self._LIB)
        (repo / "src" / "other.rs").write_text(self._OTHER)
        result = analyze_rust(repo)
        names = {s.id: s.name for s in result.symbols}
        out: dict[str, set[str]] = {}
        for e in result.edges:
            if e.edge_type == "calls":
                out.setdefault(names.get(e.src, e.src), set()).add(e.dst)
        return out

    def test_the_bare_call_binds(self, tmp_path: Path) -> None:
        assert self._calls(tmp_path)["a"] == {"rust:src/lib.rs:2-2:now:function"}

    def test_a_type_path_in_the_same_file_does_not(self, tmp_path: Path) -> None:
        dsts = self._calls(tmp_path)["b"]
        assert all(d.startswith("rust:std::time::SystemTime:") for d in dsts), dsts

    def test_a_type_path_in_another_file_does_not(self, tmp_path: Path) -> None:
        dsts = self._calls(tmp_path)["d"]
        assert all(d.startswith("rust:std::time::Instant:") for d in dsts), dsts

    def test_a_receiver_call_does_not(self, tmp_path: Path) -> None:
        dsts = self._calls(tmp_path)["e"]
        assert all(":src/lib.rs:" not in d for d in dsts), dsts

    def test_the_module_path_it_is_declared_under_binds(self, tmp_path: Path) -> None:
        assert self._calls(tmp_path)["c"] == {"rust:src/lib.rs:3-3:getpid:function"}


class TestAnExternalUseIsNotCapturedAcrossFiles:
    """A libc-mirroring ``extern { fn write(); }`` in one file must not capture
    ``write`` in a file that imported ``std::fs::write`` -- the cross-file
    magnet the bare resolver would otherwise build."""

    def _calls(self, tmp_path: Path) -> dict[str, set[str]]:
        repo = _repo(tmp_path, "mod ffi;\n")
        src = repo / "src"
        (src / "ffi.rs").write_text(
            'extern "C" { pub fn write(fd: i32, b: *const u8, n: usize) -> isize; }\n',
        )
        (src / "a.rs").write_text(
            'use std::fs::write;\nfn f() { let _ = write("a", "b"); }\n',
        )
        (src / "c.rs").write_text(
            "use crate::ffi::write;\n"
            "fn k() { unsafe { write(1, 0 as *const u8, 0); } }\n",
        )
        result = analyze_rust(repo)
        names = {s.id: s.name for s in result.symbols}
        out: dict[str, set[str]] = {}
        for e in result.edges:
            if e.edge_type == "calls":
                out.setdefault(names.get(e.src, e.src), set()).add(e.dst)
        return out

    def test_a_std_use_keeps_the_std_function(self, tmp_path: Path) -> None:
        dsts = self._calls(tmp_path)["f"]
        assert all(d.startswith("rust:std::fs:") for d in dsts), dsts

    def test_a_crate_use_still_binds_the_declaration(self, tmp_path: Path) -> None:
        assert self._calls(tmp_path)["k"] == {"rust:src/ffi.rs:1-1:write:function"}


def test_the_scip_backend_recomputes_the_same_stable_id(tmp_path: Path) -> None:
    """WI-zakub parity: ``rust_scip`` already recomputed a stable id for an
    unowned ``function_signature_item``; the symbol now carries the same one."""
    from hypergumbo_lang_mainstream.rust_scip import (
        compute_rust_stable_id_from_source,
    )

    source = "extern \"C\" {\n    pub fn getpid() -> i32;\n}\n"
    result = analyze_rust(_repo(tmp_path, source))
    sym = _by_name(result, "getpid")[0]
    assert sym.stable_id is not None
    assert compute_rust_stable_id_from_source(
        source.encode(), 2, 2, "src/lib.rs",
    ) == sym.stable_id


def test_io_boundaries_names_no_rust_primitive_for_a_foreign_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The item's repro through ``io-boundaries`` itself: ``now`` and ``getpid``
    are foreign declarations, so no Rust catalogue row names them."""
    repo = _repo(tmp_path, _FFI)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["io-boundaries", str(repo), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    chains = [
        (chain["primitive"], chain["io_edge_src"].split(":")[-2])
        for entry in report["boundaries"].values()
        for chain in entry["chains"]
    ]
    assert chains == [], chains
