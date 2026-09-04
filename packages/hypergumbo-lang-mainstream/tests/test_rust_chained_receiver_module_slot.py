# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust CHAINED receiver, and the `?` form, carry their inferred type into the
module slot (WI-papar, INV-linub L3 residual shape 2 of 2).

`File::create(p)?.write_all(b)` leaves no intermediate variable for the var_types
walker, so neither PR #595's written-path map nor WI-dizag's field arm can reach it.
Strategy 1.9 ALREADY infers the receiver's type here — it calls
`_infer_type_from_rust_rhs` on the receiver `call_expression` and resolves
`Type::method` against the symbol tables — and DROPS the inferred type when that
lookup misses, which is exactly what an external type does. The inference has run;
only the carry was missing.

The `?` half is the other deliberate gap: #595 left `try_expression` unwrapped on
purpose so its shape-3 walker would mirror `_infer_type_from_rust_rhs` exactly
rather than the two disagreeing about which node carries the type. Unwrapping
belongs in the shared helper, where both forms get it.

The value must be QUALIFIED. `_infer_type_from_rust_rhs` returns a BARE name, and a
bare name in the module slot asserts a module that does not exist — the same
package-stripping hazard WI-kamin guards in Go.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.rust import analyze_rust


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.rs").write_text(src)
    return analyze_rust(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


class TestAChainedReceiverCarriesItsQualifiedType:
    def test_the_unwrapped_two_step_form_is_the_positive_control(
        self, tmp_path: Path,
    ) -> None:
        """The binding that ALREADY works, verified against the shipped analyzer before
        this change: a bare associated-fn call with NO wrapper between it and the local.

        The obvious control -- `let f = File::create(p).unwrap()` -- does NOT work today
        and is a test below, not a control. Picking it would have made a red test look
        like a broken control.
        """
        edges = _edges(tmp_path / "ctl", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    let mut f = File::create(p);\n"
            "    f.write_all(b);\n"
            "}\n"
        ))
        edge = _call(edges, "write_all")
        assert edge.dst == "rust:std::fs::File:0-0:write_all:unresolved", edge.dst

    def test_an_unwrap_wrapped_binding(self, tmp_path: Path) -> None:
        """`.unwrap()` projects `Result<File, E>` to `File`, the same mechanism `?` is —
        and measured 1.7x more common as a receiver (49 sites against 29)."""
        edges = _edges(tmp_path / "unw", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    let mut f = File::create(p).unwrap();\n"
            "    f.write_all(b);\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == (
            "rust:std::fs::File:0-0:write_all:unresolved"
        )

    def test_an_expect_wrapped_chained_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "exp", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    File::create(p).expect(\"boom\").write_all(b);\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == (
            "rust:std::fs::File:0-0:write_all:unresolved"
        )

    def test_ok_is_not_a_projector(self, tmp_path: Path) -> None:
        """`ok()` yields `Option<T>`, not `T`, so it is deliberately NOT in the set."""
        edges = _edges(tmp_path / "ok", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    let f = File::create(p).ok();\n"
            "    f.write_all(b);\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == "rust:external:0-0:write_all:unresolved"

    def test_a_chained_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "chain", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    File::create(p).unwrap().write_all(b).unwrap();\n"
            "}\n"
        ))
        edge = _call(edges, "write_all")
        assert edge.dst == "rust:std::fs::File:0-0:write_all:unresolved", edge.dst
        assert (edge.dst_ref.module_path if edge.dst_ref else None) == "std::fs::File"

    def test_the_try_expression_form(self, tmp_path: Path) -> None:
        """`File::create(p)?` — the spelling #595 left alone on purpose."""
        edges = _edges(tmp_path / "try", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) -> std::io::Result<()> {\n"
            "    File::create(p)?.write_all(b)?;\n"
            "    Ok(())\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == (
            "rust:std::fs::File:0-0:write_all:unresolved"
        )

    def test_a_try_wrapped_let_binding_types_the_local(self, tmp_path: Path) -> None:
        """The same unwrap, reached through the let-binding walker rather than the chain."""
        edges = _edges(tmp_path / "letq", (
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) -> std::io::Result<()> {\n"
            "    let mut f = File::create(p)?;\n"
            "    f.write_all(b)?;\n"
            "    Ok(())\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == (
            "rust:std::fs::File:0-0:write_all:unresolved"
        )

    def test_a_fully_written_path_needs_no_use_alias(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "scoped", (
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) -> std::io::Result<()> {\n"
            "    std::fs::File::create(p)?.write_all(b)?;\n"
            "    Ok(())\n"
            "}\n"
        ))
        assert _call(edges, "write_all").dst == (
            "rust:std::fs::File:0-0:write_all:unresolved"
        )

    def test_an_unimportable_bare_type_stays_out_of_the_slot(
        self, tmp_path: Path,
    ) -> None:
        """A single-segment name is not a module; putting it in the slot asserts one
        that does not exist."""
        edges = _edges(tmp_path / "bare", (
            "use std::io::Write;\n"
            "fn go(p: &str, b: &[u8]) {\n"
            "    Widget::create(p).write_all(b);\n"
            "}\n"
        ))
        edge = _call(edges, "write_all")
        assert edge.dst == "rust:external:0-0:write_all:unresolved", edge.dst
