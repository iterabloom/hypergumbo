# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ``use`` of an in-tree crate's path imports that crate's source (WI-rilof).

Every Rust ``use`` became an ``imports`` edge to
``rust:<path>:0-0:module:module``, which nothing resolves, so finalize turned
each into a tier-3 ``external_symbol``. That is right for ``use
std::collections::HashMap`` and wrong for a path into the tree: ``use
crate_b::helper`` in a Cargo workspace (the sibling crate's code read as an
unresolved external) and equally ``use crate::util::tool`` inside one crate.

A path whose first segment names an in-tree crate -- ``crate``, ``self``,
``super``, or a ``[package] name`` some ``Cargo.toml`` in the tree declares
(``-`` read as ``_``) -- now imports the deepest module file the path reaches,
or the crate root when no module file matches. Other paths are unchanged.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _imports_from(data: dict, path: str) -> set[str]:
    src = f"rust:{path}:1-1:file:file"
    return {e["dst"] for e in data["edges"] if e["type"] == "imports" and e["src"] == src}


def _externals(data: dict) -> set[str]:
    return {n["id"] for n in data["nodes"] if n["kind"] == "external_symbol"}


_WS = {
    "Cargo.toml": '[workspace]\nmembers = ["crate-a", "crate-b"]\n',
    "crate-a/Cargo.toml": (
        '[package]\nname = "crate-a"\nversion = "0.1.0"\n\n'
        '[dependencies]\ncrate-b = { path = "../crate-b" }\n'
    ),
    "crate-b/Cargo.toml": '[package]\nname = "crate-b"\nversion = "0.1.0"\n',
    "crate-b/src/lib.rs": "pub mod net;\npub fn helper() -> i32 { 1 }\n",
    "crate-b/src/net/mod.rs": "pub mod conn;\n",
    "crate-b/src/net/conn.rs": "pub struct Conn;\n",
    "crate-a/src/util.rs": "pub fn tool() -> i32 { 2 }\n",
}


def test_use_of_a_sibling_crate_imports_its_source(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_WS, "crate-a/src/lib.rs": (
        "pub mod util;\n"
        "use crate_b::helper;\n"
        "use crate_b::net::conn::Conn;\n"
        "use crate_b::net;\n"
        "pub fn run() -> i32 { helper() }\n"
    )})
    assert _imports_from(data, "crate-a/src/lib.rs") >= {
        "rust:crate-b/src/lib.rs:1-1:file:file",
        "rust:crate-b/src/net/conn.rs:1-1:file:file",
        "rust:crate-b/src/net/mod.rs:1-1:file:file",
    }
    assert not [i for i in _externals(data) if "crate_b" in i], _externals(data)


def test_use_crate_path_imports_the_module_file(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_WS, "crate-a/src/lib.rs": (
        "pub mod util;\n"
        "use crate::util::tool;\n"
        "pub fn run() -> i32 { tool() }\n"
    )})
    assert "rust:crate-a/src/util.rs:1-1:file:file" in _imports_from(
        data, "crate-a/src/lib.rs")
    assert not [i for i in _externals(data) if "crate::" in i], _externals(data)


def test_self_and_super_resolve_from_the_current_module(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_WS,
        "crate-a/src/lib.rs": "pub mod util;\npub mod net;\n",
        "crate-a/src/net/mod.rs": "pub mod conn;\nuse self::conn::Conn;\n",
        "crate-a/src/net/conn.rs": "use super::super::util::tool;\npub struct Conn;\n",
    })
    assert "rust:crate-a/src/net/conn.rs:1-1:file:file" in _imports_from(
        data, "crate-a/src/net/mod.rs")
    assert "rust:crate-a/src/util.rs:1-1:file:file" in _imports_from(
        data, "crate-a/src/net/conn.rs")


def test_binary_crate_root_is_main(tmp_path: Path) -> None:
    data = _survey(tmp_path, {
        "Cargo.toml": '[package]\nname = "tool-bin"\nversion = "0.1.0"\n',
        "src/main.rs": "mod cli;\nuse crate::cli::parse;\nfn main() { parse(); }\n",
        "src/cli.rs": "pub fn parse() {}\n",
    })
    assert "rust:src/cli.rs:1-1:file:file" in _imports_from(data, "src/main.rs")


def test_integration_test_uses_its_own_crate_by_name(tmp_path: Path) -> None:
    """``tests/*.rs`` reaches the library by the package's own name."""
    data = _survey(tmp_path, {**_WS,
        "crate-a/src/lib.rs": "pub mod util;\n",
        "crate-a/tests/smoke.rs": "use crate_a::util::tool;\n",
    })
    assert "rust:crate-a/src/util.rs:1-1:file:file" in _imports_from(
        data, "crate-a/tests/smoke.rs")


def test_an_external_path_keeps_its_stub(tmp_path: Path) -> None:
    """Control: std and an undeclared crate are not in the tree."""
    data = _survey(tmp_path, {**_WS, "crate-a/src/lib.rs": (
        "use std::collections::HashMap;\n"
        "use serde::Serialize;\n"
    )})
    dsts = _imports_from(data, "crate-a/src/lib.rs")
    assert "rust:std::collections::HashMap:0-0:module:external_symbol" in dsts, dsts
    assert "rust:serde::Serialize:0-0:module:external_symbol" in dsts, dsts


def test_a_path_past_the_module_files_imports_the_deepest_one(tmp_path: Path) -> None:
    """``crate_b::net::conn::Conn::new`` names an item, not a module: the
    import lands on the deepest module file the path reaches (conn.rs)."""
    data = _survey(tmp_path, {**_WS, "crate-a/src/lib.rs": (
        "use crate_b::net::conn::Conn::new;\n"
        "use crate_b::missing::Thing;\n"
    )})
    dsts = _imports_from(data, "crate-a/src/lib.rs")
    assert "rust:crate-b/src/net/conn.rs:1-1:file:file" in dsts
    assert "rust:crate-b/src/lib.rs:1-1:file:file" in dsts


def test_super_inside_an_inline_module_names_the_file_first(tmp_path: Path) -> None:
    """``#[cfg(test)] mod tests { use super::*; }`` imports from the file's own
    module, so it gets no cross-file edge (it used to become an external
    stub); one more ``super`` leaves the file."""
    data = _survey(tmp_path, {**_WS,
        "crate-a/src/lib.rs": "pub mod util;\npub mod net;\n",
        "crate-a/src/net.rs": (
            "pub fn dial() {}\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    use super::*;\n"
            "    use super::super::util::tool;\n"
            "    mod deeper { use super::super::dial; use self::leaf::X; }\n"
            "}\n"
        ),
    })
    dsts = _imports_from(data, "crate-a/src/net.rs")
    assert dsts == {"rust:crate-a/src/util.rs:1-1:file:file"}, dsts
    assert not [i for i in _externals(data) if "super" in i], _externals(data)


# ---- _InTreeCrates edge branches, driven directly ----------------------------

def _crates(root: Path, files: dict[str, str]):
    from hypergumbo_lang_mainstream.rust import _InTreeCrates

    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return _InTreeCrates({rel: root / rel for rel in files if rel.endswith(".rs")})


def test_a_file_outside_every_crate_resolves_nothing(tmp_path: Path) -> None:
    """Only a virtual ``[workspace]`` manifest above it: no crate, no answer."""
    crates = _crates(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = []\n',
        "tools/gen.rs": "use crate::x;\n",
    })
    assert crates.resolve("tools/gen.rs", "crate::x") is None
    assert crates.resolve("tools/gen.rs", "self::x") is None


def test_a_crate_with_no_lib_or_main_resolves_nothing(tmp_path: Path) -> None:
    crates = _crates(tmp_path, {
        "Cargo.toml": '[package]\nname = "bins"\n',
        "src/bin/tool.rs": "use crate::x;\n",
    })
    assert crates.resolve("src/bin/tool.rs", "crate::x") is None


def test_degenerate_and_out_of_crate_paths(tmp_path: Path) -> None:
    crates = _crates(tmp_path, {
        "Cargo.toml": '[package]\nname = "one"\n',
        "src/lib.rs": "",
        "src/a.rs": "",
    })
    assert crates.resolve("src/lib.rs", "*") is None
    assert crates.resolve("src/lib.rs", "super::a") is None  # above the root
    assert crates.resolve("src/a.rs", "self::inner::X", inline_depth=1) == "src/a.rs"
    assert crates.resolve("src/a.rs", "one::a") == "src/a.rs"
