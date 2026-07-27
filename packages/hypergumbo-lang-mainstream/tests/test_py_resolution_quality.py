# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-javus: resolution_quality='type_inferred' must reflect an ACTUAL inference.

py.py's unresolved method-call fallback historically stamped
``meta.resolution_quality='type_inferred'`` UNCONDITIONALLY — including on the
give-up branch whose own comment reads "type cannot be inferred". On pretix that
mislabeled ~62% of the ``type_inferred`` population (7920/12839): edges with no
receiver hint and a bare ``python:external:`` placeholder dst, where the receiver
type was NOT inferred at all. A consumer branching on the string reads the
give-up case as a success — a data-honesty defect (the "no weasel words" rule
applied to emitted metadata).

The field is a pathway-quality label (spec §903 / MetaKeySpec: e.g.
``recovery`` / ``ambiguous``), optional, and orthogonal to ``Edge.is_resolved``.
So it is honest ONLY when a receiver type/hint was actually established
(``self`` → enclosing class, an annotated/constructed var → ``receiver_type_hint``,
or a bare local class name). The untyped / duck give-up establishes no type and
therefore carries NO ``resolution_quality``.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python


def _unresolved_by_callee(edges: list) -> dict:
    """Map callee short-name -> meta dict for unresolved external method calls."""
    out = {}
    for e in edges:
        if e.edge_type == "calls" and ":external:0-0:" in e.dst and ":unresolved" in e.dst:
            out[e.dst.split(":")[3]] = e.meta or {}
    return out


class TestResolutionQualityHonesty:
    def test_give_up_receiver_carries_no_resolution_quality(
        self, tmp_path: Path
    ) -> None:
        """An untyped / duck receiver (no hint established) must NOT claim
        type_inferred — the give-up branch establishes no type."""
        (tmp_path / "app.py").write_text(
            "def handler(x):\n"
            "    return x.frobnicate()\n"
        )
        meta = _unresolved_by_callee(analyze_python(tmp_path).edges)["frobnicate"]
        assert "receiver_type_hint" not in meta
        assert "enclosing_class" not in meta
        assert "resolution_quality" not in meta

    def test_self_call_keeps_type_inferred(self, tmp_path: Path) -> None:
        """self.method() DOES establish a type (the enclosing class) → keeps the
        type_inferred label alongside the enclosing_class hint."""
        (tmp_path / "app.py").write_text(
            "class C:\n"
            "    def m(self):\n"
            "        self.inherited_or_absent()\n"
        )
        meta = _unresolved_by_callee(analyze_python(tmp_path).edges)[
            "inherited_or_absent"
        ]
        assert meta.get("enclosing_class") == "C"
        assert meta.get("resolution_quality") == "type_inferred"

    def test_local_class_receiver_keeps_type_inferred(self, tmp_path: Path) -> None:
        """A bare in-file class name used as a receiver (Foo.bar()) establishes a
        receiver_type_hint → keeps type_inferred."""
        (tmp_path / "app.py").write_text(
            "class Foo:\n"
            "    pass\n"
            "\n"
            "def caller():\n"
            "    return Foo.bar()\n"
        )
        meta = _unresolved_by_callee(analyze_python(tmp_path).edges)["bar"]
        assert meta.get("receiver_type_hint") == "Foo"
        assert meta.get("resolution_quality") == "type_inferred"

    def test_constructed_var_receiver_keeps_type_inferred(
        self, tmp_path: Path
    ) -> None:
        """A local typed by a constructor (x = Foo(); x.method()) is tracked in
        var_types → receiver_type_hint → keeps type_inferred."""
        (tmp_path / "app.py").write_text(
            "class Foo:\n"
            "    pass\n"
            "\n"
            "def caller():\n"
            "    x = Foo()\n"
            "    return x.inherited_or_absent()\n"
        )
        meta = _unresolved_by_callee(analyze_python(tmp_path).edges)[
            "inherited_or_absent"
        ]
        assert meta.get("receiver_type_hint") == "Foo"
        assert meta.get("resolution_quality") == "type_inferred"

    def test_give_up_still_carries_call_construct(self, tmp_path: Path) -> None:
        """Removing the false resolution_quality must not drop call_construct —
        io-boundary / taint still need to see it is a method call."""
        (tmp_path / "app.py").write_text(
            "def handler(x):\n"
            "    return x.frobnicate()\n"
        )
        meta = _unresolved_by_callee(analyze_python(tmp_path).edges)["frobnicate"]
        assert meta.get("call_construct") == "method"
