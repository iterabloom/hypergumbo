# SPDX-License-Identifier: AGPL-3.0-or-later
"""A typed receiver whose type is EXTERNAL must keep its type (INV-linub L3).

INV-linub's four-link chain is L1 catalogue → L2 emission → L3 receiver
evidence → L4 the match gate. Rust passes L2 (it emits method-construct edges
and populates ``call_construct``) and fails L3: measured on
encrypted-dns-server, 203 of its 219 method-construct edges carry the bare
``external`` placeholder in the module slot and NOT ONE carries a stdlib
module, so ``_lookup_named_entry`` correctly refuses every one of them and the
whole method-kind half of the Rust catalogue is unreachable.

The mechanism is not that the analyzer never knows the type. It KNOWS and then
DISCARDS: the typed-receiver strategies resolve ``Type::method`` against
first-party symbols only, so when the receiver's type is external the lookup
misses, ``resolved`` stays False, and control falls through to the unresolved
path — which rebuilds ``module_hint`` from ``use_aliases`` alone. A method name
is never in ``use_aliases``, so the slot stays ``external`` and the receiver
type computed moments earlier is dropped on the floor.

WHY THIS TEST IS AT L3 AND NOT L4. It asserts on the MODULE SLOT rather than on
a taint finding deliberately: INV-linub's rule is that an emission-side fix must
be measured through L4 on findings, and that measurement belongs on the item.
What this pins is the narrower structural property the fix is FOR — that a
computed receiver type survives into the edge — because that is the part a
future refactor can silently undo.
"""
from pathlib import Path


def _analyze(tmp_path: Path, body: str):
    from hypergumbo_lang_mainstream.rust import analyze_rust

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "f"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text(body)
    return analyze_rust(tmp_path)


def _method_edges(result):
    return [
        e for e in result.edges
        if (e.meta or {}).get("call_construct") == "method"
    ]


class TestExternalTypedReceiverKeepsItsType:
    def test_typed_param_receiver_carries_its_module(self, tmp_path: Path) -> None:
        """``p: &Path`` then ``p.exists()`` must not degrade to ``external``.

        ``_extract_var_types_rust`` binds ``p -> Path`` from the parameter and
        ``_extract_use_aliases`` binds ``Path -> std::path::Path``, so both
        halves of the module path are already in hand when the edge is built.
        rust.yaml declares ``std::path::Path`` with ``methods: [exists, ...]``,
        so this exact call is a catalogued ``fs_read`` primitive that the tool
        cannot currently see.
        """
        result = _analyze(tmp_path, (
            "use std::path::Path;\n"
            "\n"
            "pub fn probe(p: &Path) -> bool {\n"
            "    p.exists()\n"
            "}\n"
        ))
        exists = [
            e for e in _method_edges(result)
            if (e.meta or {}).get("callee_name") == "exists"
        ]
        assert exists, (
            "no method-construct edge for p.exists() at all — that would be an "
            "L2 failure, and this test is about L3"
        )
        # READ dst_ref, NOT the dst STRING. A Rust module path contains "::",
        # so splitting the id on ":" shears `std::path::Path` at its first
        # colon and yields `std` — which is exactly the string-parsing ADR-0037
        # removed by deriving `dst_ref` unconditionally. The first cut of this
        # test made that mistake and reported a working fix as broken.
        slots = {e.dst_ref.module_path for e in exists if e.dst_ref}
        assert slots == {"std::path::Path"}, (
            f"module_path is {slots}, expected {{'std::path::Path'}}. A bare "
            "'external' here means the receiver type was computed and then "
            "discarded, so io_boundary's method-kind gate refuses the edge and "
            "the catalogued std::path::Path fs_read primitive is invisible."
        )

    def test_untyped_receiver_still_degrades(self, tmp_path: Path) -> None:
        """The negative control: an untyped receiver must STAY ``external``.

        Without this the fix could 'pass' by stamping a module on everything,
        which is precisely the false-match INV-linub's L4 gate exists to
        prevent (``str.replace`` must not match ``Path.replace``).
        """
        result = _analyze(tmp_path, (
            "pub fn probe(x: &dyn std::any::Any) -> bool {\n"
            "    x.is::<u8>()\n"
            "}\n"
            "pub fn opaque(v: Vec<String>) -> usize {\n"
            "    v.capacity()\n"
            "}\n"
        ))
        cap = [
            e for e in _method_edges(result)
            if (e.meta or {}).get("callee_name") == "capacity"
        ]
        assert cap, "expected a method edge for v.capacity()"
        assert all(e.dst_ref is None for e in cap), (
            "Vec is not in use_aliases (no `use` statement names it), so there "
            "is no module path to carry and the slot must stay 'external'"
        )
