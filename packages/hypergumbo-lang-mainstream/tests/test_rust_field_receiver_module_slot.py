# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-dizag: a struct-field receiver's EXTERNAL type must reach the module slot.

THE GAP, AND IT IS A DISCARD RATHER THAN A MISSING INFERENCE. Strategy 1.5 in
``rust.py`` already resolves a ``self.field`` receiver chain to its type, through
``analyzer.resolve_receiver_type`` + the struct field-type registry. It then uses
that type ONLY to look up a FIRST-PARTY symbol (``File::write_all``). When the
field's type is external the lookup misses, ``resolved`` stays False, and **the
type it just computed is thrown away** — so the call falls through to the
unresolved branch carrying the bare ``external`` placeholder, and every
method-kind row in ``rust.yaml`` is unreachable through it.

That is the same shape PR #595 fixed for ``_var_types`` (plain identifier
receivers) and it is INV-linub's L3 residual.

WHY IT MATTERS MORE THAN #595 DID: #595 moved io-boundaries 303 → 360 classified
Rust call sites and moved ZERO taint flows, because the rows it unlocked were
``host_info_read`` and ``fs_read``, and ``fs_read`` is excluded from
``AUTO_SOURCE_LABEL_MAP`` by design. ``write_all`` is ``fs_write``, which IS an
auto-derived SINK — so this shape is on the sink side and can change a verdict.
Item measurement, 11 Rust repos: ``write_all`` matches 5 catalogued method sites
and MISSES 125; ``lock`` is the largest single miss at 440.

THE FIVE RECEIVER SHAPES, measured on this fixture before the fix
(``~/hypergumbo_lab_notebook/dizag_rust_08302026/``):

    A  self.f.write_all(..)                    -> external   MISS   <- fixed here
    B  h.f.write_all(..), h a typed param      -> external   MISS   <- NOT fixed
    C  let c = File::create(..).unwrap()       -> external   MISS   (WI-lalot)
    D  fn f(d: &mut File)  d.write_all(..)     -> fs_write   works  [CONTROL]
    E  let e: File = ..;   e.write_all(..)     -> fs_write   works  [CONTROL]

D and E are PR #595's shapes and already pass; they are pinned so a regression
there is attributed here rather than discovered later.

B IS DELIBERATELY OUT OF SCOPE AND IS NOT SILENTLY LEFT OUT.
``resolve_receiver_type`` resolves "only ... chains rooted at a ``self_keywords``
token. Non-self receivers return None" — so ``h.f`` needs a var-rooted chain
walk, which Go already has (``_resolve_field_chain``) and Rust does not. That is
a change in shared ``analyze/base.py`` with a wider blast radius, and it is
recorded on the item rather than folded in here.

A CAUTION FOR THE FIXTURE, learned by getting it wrong: Rust's ``_var_types`` is
FILE-scoped and first-writer-wins, so a receiver named ``f`` in one function is
typed by an annotation on a DIFFERENT function's ``f``. The first cut of this
fixture used ``f`` everywhere and shape C appeared to pass, because D's
``d: &mut File`` had already typed the name. Every receiver below has a distinct
name for that reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog


@pytest.fixture()
def rust_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_rust"):
        pytest.skip("Rust tree-sitter grammar not installed")


_SOURCE = '''\
use std::fs::File;
use std::io::Write;

struct Holder {
    f: File,
}

impl Holder {
    fn write_self(&mut self, data: &[u8]) {
        self.f.write_all(data).unwrap();
    }
}

fn write_param(h: &mut Holder, data: &[u8]) {
    h.f.write_all(data).unwrap();
}

fn write_local(data: &[u8]) {
    let mut c_var = File::create("/tmp/x").unwrap();
    c_var.write_all(data).unwrap();
}

fn write_annotated(d_var: &mut File, data: &[u8]) {
    d_var.write_all(data).unwrap();
}

fn write_annotated_local(data: &[u8]) {
    let mut e_var: File = File::create("/tmp/y").unwrap();
    e_var.write_all(data).unwrap();
}
'''


def _write_all_boundaries(tmp_path: Path) -> dict[int, str | None]:
    """``{line: io boundary}`` for every ``write_all`` call in the fixture."""
    from hypergumbo_lang_mainstream.rust import analyze_rust

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text(_SOURCE)

    result = analyze_rust(tmp_path)
    assert not result.skipped
    catalogs = {"rust": load_catalog("rust", include_defaults=True)}
    out: dict[int, str | None] = {}
    for edge in result.edges:
        if edge.edge_type not in ("calls", "instantiates"):
            continue
        if "write_all" not in edge.dst:
            continue
        prim = classify_call(catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref)
        out[edge.line] = prim.boundary if prim is not None else None
    return out


def _line_of(receiver: str) -> int:
    """1-indexed line of ``<receiver>.write_all`` in ``_SOURCE``.

    DERIVED, NOT COUNTED. The first cut hardcoded five line numbers and got one
    of them wrong by one, which surfaced as a CONTROL failing — indistinguishable
    at a glance from the fix having broken the prior fix. A hardcoded inventory
    decays the moment the fixture gains a line; each receiver name is unique
    precisely so this lookup is unambiguous.
    """
    needle = f"{receiver}.write_all"
    for i, line in enumerate(_SOURCE.split("\n"), start=1):
        if needle in line:
            return i
    raise AssertionError(f"no {needle!r} in the fixture")


LINE_SELF_FIELD = _line_of("self.f")
LINE_PARAM_FIELD = _line_of("h.f")
LINE_EXTERNAL_RETURN = _line_of("c_var")
LINE_DECLARED_PARAM = _line_of("d_var")
LINE_ANNOTATED_LET = _line_of("e_var")


class TestSelfFieldReceiverReachesTheCatalogue:
    """Shape A: the type Strategy 1.5 computes must not be discarded."""

    def test_self_field_write_all_is_fs_write(
        self, tmp_path: Path, rust_available: None,
    ) -> None:
        got = _write_all_boundaries(tmp_path)
        assert got.get(LINE_SELF_FIELD) == "fs_write", (
            f"self.f.write_all classified {got.get(LINE_SELF_FIELD)!r}; the "
            f"receiver type was resolved and then dropped"
        )


class TestThePriorFixStillHolds:
    """Shapes D and E: PR #595's identifier receivers, pinned as controls.

    A change that reaches the module slot from a new direction is exactly the
    kind that can disturb the old one. These fail loudly here rather than
    quietly somewhere else.
    """

    @pytest.mark.parametrize(
        ("line", "shape"),
        [(LINE_DECLARED_PARAM, "declared parameter"),
         (LINE_ANNOTATED_LET, "annotated let binding")],
    )
    def test_identifier_receivers_still_classify(
        self, line: int, shape: str, tmp_path: Path, rust_available: None,
    ) -> None:
        got = _write_all_boundaries(tmp_path)
        assert got.get(line) == "fs_write", (
            f"{shape} receiver regressed to {got.get(line)!r}"
        )


class TestTheTwoShapesThisDoesNotFix:
    """Recorded as OPEN, not asserted as correct.

    Shape B needs a var-rooted field-chain walk (``resolve_receiver_type``
    handles only ``self``-rooted chains); shape C is a receiver typed from an
    external function's RETURN VALUE, which is WI-lalot. Both are genuinely
    unfixed, so this test states what they do TODAY and is expected to be
    edited — not deleted — when either lands. Asserting them as ``fs_write``
    would be pinning work that has not been done.
    """

    def test_param_field_and_external_return_are_still_unclassified(
        self, tmp_path: Path, rust_available: None,
    ) -> None:
        got = _write_all_boundaries(tmp_path)
        assert got.get(LINE_PARAM_FIELD) is None, (
            "h.f.write_all now classifies — WI-dizag shape B has been fixed; "
            "move this assertion into the passing class above"
        )
        assert got.get(LINE_EXTERNAL_RETURN) is None, (
            "the external-return receiver now classifies — that is WI-lalot; "
            "move this assertion into the passing class above"
        )
