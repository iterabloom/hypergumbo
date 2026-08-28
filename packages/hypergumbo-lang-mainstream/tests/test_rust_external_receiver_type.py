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


class TestQualifiedReceiverTypeSurvivesNormalization:
    """A path the SOURCE WROTE must reach the edge (INV-linub L3, second half).

    The first half of this fix recovers the module path through
    ``use_aliases``, which works only when the receiver's type was imported
    BY NAME. When the source spells the path out instead — a
    ``std::fs::File`` parameter annotation, or a
    ``std::time::Instant::now()`` construction with no ``use`` — the path is
    right there in the text and is still dropped, because
    ``_normalize_rust_type_to_bare_name`` reduces the annotation to its bare
    identifier ("strip module paths", per its own docstring) and the bare name
    is then absent from ``use_aliases``.

    Measured on four repos (aardvark-dns, candle, didkit, loro) at 229
    available catalogued trait-method call sites: ``elapsed`` matched 28 times
    and missed 52, the SAME name resolving or not purely on receiver shape.

    THE RULE THIS CLASS PINS IS NARROW ON PURPOSE: a path is carried only when
    the source wrote one. Nothing is synthesized, so the negative control in
    ``TestExternalTypedReceiverKeepsItsType`` — a prelude ``Vec`` with no
    ``use`` and no written path — must keep degrading to ``external``.
    """

    def test_qualified_param_annotation_keeps_its_path(
        self, tmp_path: Path,
    ) -> None:
        """``f: &mut std::fs::File`` writes the path at the call site's door."""
        result = _analyze(tmp_path, (
            "pub fn dump(f: &mut std::fs::File, b: &[u8]) {\n"
            "    let _ = f.write_all(b);\n"
            "}\n"
        ))
        hits = [
            e for e in _method_edges(result)
            if (e.meta or {}).get("callee_name") == "write_all"
        ]
        assert hits, "no method edge for f.write_all() — that is an L2 failure"
        slots = {e.dst_ref.module_path for e in hits if e.dst_ref}
        assert slots == {"std::fs::File"}, (
            f"module_path is {slots}, expected {{'std::fs::File'}}. The "
            "parameter annotation spells the full path, so nothing had to be "
            "inferred — it was normalized to 'File' and then looked up in "
            "use_aliases, which has no entry because nothing was imported."
        )

    def test_qualified_let_annotation_keeps_its_path(
        self, tmp_path: Path,
    ) -> None:
        """``let f: std::fs::File`` is the same written path, at a binding."""
        result = _analyze(tmp_path, (
            "pub fn dump(p: &str, b: &[u8]) {\n"
            "    let mut f: std::fs::File = "
            "std::fs::File::create(p).unwrap();\n"
            "    let _ = f.write_all(b);\n"
            "}\n"
        ))
        hits = [
            e for e in _method_edges(result)
            if (e.meta or {}).get("callee_name") == "write_all"
        ]
        assert hits, "no method edge for f.write_all() — that is an L2 failure"
        slots = {e.dst_ref.module_path for e in hits if e.dst_ref}
        assert slots == {"std::fs::File"}

    def test_qualified_construction_without_use_keeps_its_path(
        self, tmp_path: Path,
    ) -> None:
        """``let s = std::time::Instant::now(); s.elapsed()``.

        The single largest miss bucket in the four-repo measurement: 52 of the
        80 ``elapsed`` call sites. The constructing call already names the
        type in full, so the binding's type is known without an annotation and
        without an import.
        """
        result = _analyze(tmp_path, (
            "pub fn timed() -> std::time::Duration {\n"
            "    let start = std::time::Instant::now();\n"
            "    start.elapsed()\n"
            "}\n"
        ))
        hits = [
            e for e in _method_edges(result)
            if (e.meta or {}).get("callee_name") == "elapsed"
        ]
        assert hits, "no method edge for start.elapsed() — that is an L2 failure"
        slots = {e.dst_ref.module_path for e in hits if e.dst_ref}
        assert slots == {"std::time::Instant"}, (
            f"module_path is {slots}, expected {{'std::time::Instant'}}"
        )


class TestQualifiedPathHelperRefusesToInvent:
    """Unit-level guards on ``_qualified_rust_type_path``.

    The class above proves the path reaches the edge. These prove the helper
    cannot manufacture one — which is the half that keeps the L4 gate honest,
    since a module slot filled with a wrong path is worse than an empty one:
    it converts a correct refusal into a confident mismatch.
    """

    def test_strips_reference_lifetime_and_mut(self) -> None:
        from hypergumbo_lang_mainstream.rust import _qualified_rust_type_path

        assert _qualified_rust_type_path(
            "&'a mut std::fs::File", "File") == "std::fs::File"
        assert _qualified_rust_type_path(
            "mut std::fs::File", "File") == "std::fs::File"
        assert _qualified_rust_type_path(
            "&std::path::Path", "Path") == "std::path::Path"

    def test_unqualified_type_yields_nothing(self) -> None:
        from hypergumbo_lang_mainstream.rust import _qualified_rust_type_path

        assert _qualified_rust_type_path("File", "File") is None
        assert _qualified_rust_type_path("Vec<String>", "Vec") is None

    def test_path_whose_tail_disagrees_with_the_bare_name_is_refused(
        self,
    ) -> None:
        """THE BINDING CHECK. Two readings of one type must not disagree.

        ``_normalize_rust_type_to_bare_name`` unwraps ``Result``/``Option``/
        ``Box`` to reach the inner type, so a wrapped annotation normalizes to
        the payload while the written text still starts with the wrapper. If
        this helper returned the written path anyway, a
        ``std::io::Result<std::fs::File>`` receiver would be stamped
        ``std::io::Result`` and matched against the wrong catalogue rows.
        """
        from hypergumbo_lang_mainstream.rust import _qualified_rust_type_path

        assert _qualified_rust_type_path(
            "std::io::Result<std::fs::File>", "File") is None
        assert _qualified_rust_type_path(
            "std::sync::Arc<std::fs::File>", "File") is None

    def test_generic_arguments_are_stripped_before_the_check(self) -> None:
        from hypergumbo_lang_mainstream.rust import _qualified_rust_type_path

        assert _qualified_rust_type_path(
            "std::collections::HashMap<String, u8>", "HashMap"
        ) == "std::collections::HashMap"


class TestQualifiedPathWalkerBindingRules:
    def test_first_writer_wins_like_the_bare_map(self, tmp_path: Path) -> None:
        """Same rule as ``_extract_var_types_rust``, so the maps agree.

        If the two disagreed about which binding a name refers to, the bare
        name would resolve against one type and the module path against
        another — the one-fact-two-homes failure in its most dangerous form,
        because both halves would look individually correct.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_qualified_var_type_paths,
        )
        import tree_sitter
        import tree_sitter_rust

        src = (
            b"pub fn a() {\n"
            b"    let h = std::fs::File::create(\"x\");\n"
            b"}\n"
            b"pub fn b() {\n"
            b"    let h = std::process::Command::new(\"x\");\n"
            b"}\n"
        )
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_rust.language())
        )
        paths = _extract_qualified_var_type_paths(
            parser.parse(src).root_node, src,
        )
        assert paths["h"] == "std::fs::File"

    def test_destructuring_and_uninitialized_bindings_are_skipped(
        self, tmp_path: Path,
    ) -> None:
        """``let (a, b) = ...`` and ``let x;`` carry no name/type pair here."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_qualified_var_type_paths,
        )
        import tree_sitter
        import tree_sitter_rust

        src = (
            b"pub fn a() {\n"
            b"    let x;\n"
            b"    let (p, q) = (1, 2);\n"
            b"    x = 1;\n"
            b"}\n"
        )
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_rust.language())
        )
        assert _extract_qualified_var_type_paths(
            parser.parse(src).root_node, src) == {}

    def test_closure_parameter_without_a_type_is_skipped(
        self, tmp_path: Path,
    ) -> None:
        """A parameter the grammar gives no type node must not raise."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_qualified_var_type_paths,
        )
        import tree_sitter
        import tree_sitter_rust

        src = (
            b"pub fn a() {\n"
            b"    let f = |v| v + 1;\n"
            b"    let _ = f(1);\n"
            b"}\n"
            b"pub fn b(self_like: std::fs::File) {}\n"
            # non-identifier parameter patterns: a tuple destructure and a
            # wildcard bind no NAME, so there is nothing to key a path on.
            b"pub fn c((x, y): (u8, u8), _: std::fs::File) {}\n"
        )
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_rust.language())
        )
        paths = _extract_qualified_var_type_paths(
            parser.parse(src).root_node, src,
        )
        assert paths == {"self_like": "std::fs::File"}
