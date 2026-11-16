# SPDX-License-Identifier: AGPL-3.0-or-later
"""A struct-field receiver's EXTERNAL type must reach the module slot (WI-dizag).

A DISCARD, NOT A MISSING INFERENCE. Strategy 1.5 in ``rust.py`` resolves a
``self.field`` receiver chain to its type through
``analyzer.resolve_receiver_type`` + the struct field-type registry, and
``analyzer.resolve_var_field_chain`` does the same for a var-rooted chain
(``h.f``). The type is used to look up a FIRST-PARTY symbol
(``File::write_all``); when the field's type is external that lookup misses and
``resolved`` stays False. Unless the computed type is carried to the unresolved
branch (``field_receiver_type``), the call keeps the bare ``external``
placeholder and every method-kind row in ``rust.yaml`` is unreachable through
it -- the same hazard the ``_var_types`` recovery covers for plain identifier
receivers (INV-linub L3).

WHY IT MATTERS: ``write_all`` is ``fs_write``, an auto-derived SINK (whereas
``fs_read`` is excluded from ``AUTO_SOURCE_LABEL_MAP`` by design), so this
shape is on the sink side and can change a taint verdict.

THE FIVE RECEIVER SHAPES, each of which must classify as ``fs_write``:

    A  self.f.write_all(..)                    self-rooted field chain
    B  h.f.write_all(..), h a typed param      var-rooted field chain
    C  let c = File::create(..).unwrap()       producer's scoped path
    D  fn f(d: &mut File)  d.write_all(..)     declared param   [CONTROL]
    E  let e: File = ..;   e.write_all(..)     annotated let    [CONTROL]

D and E are the identifier-receiver shapes; they are pinned so a regression
there is attributed here rather than discovered later.

A CAUTION FOR THE FIXTURE: Rust's ``_var_types`` is FILE-scoped and
first-writer-wins, so a receiver named ``f`` in one function is typed by an
annotation on a DIFFERENT function's ``f``. Sharing a receiver name across
shapes lets one shape type another (D's ``d: &mut File`` would make C pass
spuriously). Every receiver below has a distinct name for that reason.
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

    DERIVED, NOT COUNTED. A hardcoded line inventory decays the moment the
    fixture gains a line, and an off-by-one surfaces as a CONTROL failing —
    indistinguishable at a glance from a real regression. Each receiver name
    is unique precisely so this lookup is unambiguous.
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


class TestFieldReceiverReachesTheCatalogue:
    """Shapes A and B: a field receiver's type must not be discarded.

    A is ``self.f`` (Strategy 1.5, self-rooted). B is ``h.f`` with ``h`` a
    typed PARAMETER — the var-rooted field chain
    (``resolve_var_field_chain``). Both resolve ``File`` through the struct
    field-type registry; the only difference is where the root's type comes
    from (``enclosing_type`` for self, ``_var_types`` for a variable).
    """

    def test_self_field_write_all_is_fs_write(
        self, tmp_path: Path, rust_available: None,
    ) -> None:
        got = _write_all_boundaries(tmp_path)
        assert got.get(LINE_SELF_FIELD) == "fs_write", (
            f"self.f.write_all classified {got.get(LINE_SELF_FIELD)!r}; the "
            f"receiver type was resolved and then dropped"
        )

    def test_param_field_write_all_is_fs_write(
        self, tmp_path: Path, rust_available: None,
    ) -> None:
        """Shape B: ``h.f.write_all`` with ``h: &mut Holder`` a parameter.

        The root ``h`` is typed ``Holder`` in ``_var_types`` (the same
        mechanism that types shapes D and E), and ``Holder.f`` is ``File`` in
        the field-type registry, so a var-rooted walk recovers ``File`` and the
        module slot reaches ``std::fs::File.write_all`` — an ``fs_write`` sink.
        """
        got = _write_all_boundaries(tmp_path)
        assert got.get(LINE_PARAM_FIELD) == "fs_write", (
            f"h.f.write_all classified {got.get(LINE_PARAM_FIELD)!r}; the "
            f"var-rooted field chain did not reach std::fs::File"
        )


class TestThePriorFixStillHolds:
    """Shapes D and E: identifier receivers, pinned as controls.

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


class TestTheShapeThisDidNotFix_NowFixed:
    """Shape C: a receiver typed from an external function's RETURN VALUE.

    In ``let c = File::create(..).unwrap()`` the type IS written down — in the
    PRODUCER's own scoped path, ``File::create``. ``_infer_type_from_rust_rhs``
    strips the ``.unwrap()`` / ``?`` wrapper to reach it, and when the in-repo
    ``Type::method`` lookup misses, the inferred type must be CARRIED into the
    module slot, qualified as ``std::fs::File`` rather than the bare ``File``.
    No catalogue row for ``std::fs::File::create`` is needed (history:
    WI-papar; WI-lalot is a separate item).
    """

    def test_external_return_receiver_now_classifies(
        self, tmp_path: Path, rust_available: None,
    ) -> None:
        got = _write_all_boundaries(tmp_path)
        assert got.get(LINE_EXTERNAL_RETURN) == "fs_write", (
            f"let c = File::create(..).unwrap(); c.write_all(..) classified "
            f"{got.get(LINE_EXTERNAL_RETURN)!r}; WI-papar carries the producer's "
            f"own scoped path into the module slot, so this must reach the "
            f"catalogue"
        )
