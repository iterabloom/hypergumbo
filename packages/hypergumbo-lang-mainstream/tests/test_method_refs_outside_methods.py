# SPDX-License-Identifier: AGPL-3.0-or-later
"""A method reference outside any method is anchored like a call (META-mino).

Java (``M::run``) and Ruby (``method(:run)``) each emit a ``references`` edge,
evidence ``method_reference``, from the method that holds the reference. A
reference in NO method -- a Java field initializer
(``static Supplier<Integer> F = M::run;``), a Ruby constant at top level or in
a class body (``HANDLERS = {a: method(:a)}``) -- was dropped, though a CALL in
the same place is anchored on the enclosing class or module, else the file
(INV-bamij). A function referenced only from there looked unused. Both arms
now take the anchor the call arm takes.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _method_refs(result, target: str) -> list[str]:
    """Names of the srcs of every method_reference edge to ``target``."""
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        (by_id[e.src].name if e.src in by_id else e.src)
        for e in result.edges
        if e.edge_type == "references" and e.evidence_type == "method_reference"
        and e.dst in by_id and by_id[e.dst].name.split(".")[-1].split("#")[-1] == target
    )


class TestJava:
    def test_a_static_field_initializer_is_anchored_on_its_class(self, tmp_path: Path) -> None:
        (tmp_path / "M.java").write_text(
            "import java.util.function.Supplier;\n"
            "class M {\n"
            "    static int glob() { return 3; }\n"
            "    static Supplier<Integer> G = M::glob;\n"
            "}\n"
        )
        assert _method_refs(analyze_java(tmp_path), "glob") == ["M"]

    def test_an_instance_field_this_reference_is_anchored_on_its_class(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "W.java").write_text(
            "class W {\n"
            "    void tick() {}\n"
            "    final Runnable r = this::tick;\n"
            "}\n"
        )
        assert _method_refs(analyze_java(tmp_path), "tick") == ["W"]

    def test_a_reference_in_a_method_keeps_its_method(self, tmp_path: Path) -> None:
        (tmp_path / "M.java").write_text(
            "import java.util.function.Supplier;\n"
            "class M {\n"
            "    static int glob() { return 3; }\n"
            "    void run() { Supplier<Integer> s = M::glob; }\n"
            "}\n"
        )
        assert _method_refs(analyze_java(tmp_path), "glob") == ["M.run"]


class TestRuby:
    def test_a_top_level_constant_is_anchored_on_the_file(self, tmp_path: Path) -> None:
        (tmp_path / "app.rb").write_text(
            "def glob; 3; end\n"
            "G = method(:glob)\n"
        )
        result = analyze_ruby(tmp_path)
        refs = [
            e for e in result.edges
            if e.edge_type == "references" and e.evidence_type == "method_reference"
        ]
        assert [e.dst.split(":")[-2] for e in refs] == ["glob"]
        assert refs[0].src.endswith(":file") or ":file" in refs[0].src, refs[0].src

    def test_a_class_body_table_resolves_in_its_class(self, tmp_path: Path) -> None:
        """``method(:a)`` in ``class App``'s body names ``App#a``, not a
        same-named top-level method."""
        (tmp_path / "app.rb").write_text(
            "class App\n"
            "  def a; 1; end\n"
            "  def b; 2; end\n"
            "  HANDLERS = { a: method(:a), b: method(:b) }\n"
            "end\n"
            "def a; 0; end\n"
        )
        result = analyze_ruby(tmp_path)
        by_id = {s.id: s for s in result.symbols}
        refs = sorted(
            (by_id[e.src].name, by_id[e.dst].name)
            for e in result.edges
            if e.edge_type == "references" and e.evidence_type == "method_reference"
        )
        assert refs == [("App", "App#a"), ("App", "App#b")]

    def test_a_reference_in_a_method_keeps_its_method(self, tmp_path: Path) -> None:
        (tmp_path / "app.rb").write_text(
            "class App\n  def t(x); x; end\n  def run; method(:t); end\nend\n"
        )
        assert _method_refs(analyze_ruby(tmp_path), "t") == ["App#run"]


class TestTheReferenceNamesTheRightMethod:
    """Two resolution faults the wider anchoring exposed (both predate it)."""

    def test_java_this_reference_is_the_enclosing_class_s_method(self, tmp_path: Path) -> None:
        """``this::readOneByte`` is THIS class's method, not the repo's first
        ``readOneByte`` (commons-compress: FramedLZ4CompressorInputStream had
        bound ZipArchiveInputStream.readOneByte). The bare-name lookup picked
        by discovery order, so both write orders are checked."""
        caller = (
            "package lz4;\n"
            "import java.util.function.IntSupplier;\n"
            "class Caller {\n"
            "    final IntSupplier s = this::readOneByte;\n"
            "    void run() { IntSupplier t = this::readOneByte; }\n"
            "    private int readOneByte() { return 2; }\n"
            "}\n"
        )
        other = "package zip;\nclass Other {\n    private int readOneByte() { return 1; }\n}\n"
        for order, files in enumerate((
            (("lz4/Caller.java", caller), ("zip/Other.java", other)),
            (("zip/Other.java", other), ("lz4/Caller.java", caller)),
        )):
            root = tmp_path / str(order)
            for rel, text in files:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            result = analyze_java(root)
            by_id = {s.id: s for s in result.symbols}
            refs = sorted(
                (by_id[e.src].name, by_id[e.dst].name)
                for e in result.edges
                if e.evidence_type == "method_reference" and e.dst in by_id
            )
            assert refs == [
                ("Caller", "Caller.readOneByte"), ("Caller.run", "Caller.readOneByte"),
            ], (order, refs)

    def test_ruby_a_receiver_s_method_is_not_self_s(self, tmp_path: Path) -> None:
        """``1.method(:+)`` / ``obj.method(:foo)`` name a method of the
        receiver's class, which this pass does not know; ``self.method(:foo)``
        and a bare ``method(:foo)`` are self's."""
        (tmp_path / "t.rb").write_text(
            "class Enumerator\n  def +(o); o; end\nend\n"
            "def foo; 1; end\n"
            "A = 1.method(:+)\n"
            "B = obj.method(:foo)\n"
            "C = self.method(:foo)\n"
            "D = method(:foo)\n"
        )
        result = analyze_ruby(tmp_path)
        refs = sorted(
            (e.line, e.dst.split(":")[-2])
            for e in result.edges
            if e.edge_type == "references" and e.evidence_type == "method_reference"
        )
        assert refs == [(7, "foo"), (8, "foo")]
