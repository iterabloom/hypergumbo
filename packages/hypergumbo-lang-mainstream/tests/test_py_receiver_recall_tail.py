# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two receiver-typing shapes left over after PR #246 / #247.

**Inline expression receivers.** PR #247 taught the analyzer that a derivation from a
typed receiver keeps the type, but only entered from an *assignment* — the typed-emit
path is gated on ``isinstance(func.value, ast.Name)``, so a receiver that is an
expression rather than a bound name never reaches it. ``(d / "f.txt").write_text(x)``
and ``with (d / "f").open("w")`` are thoroughly idiomatic Python and reported nothing.
This is a gap introduced by #247's own scoping, not a pre-existing one.

**AnnAssign roots.** ``d: Path = raw`` was excluded from #246 on a measurement showing
zero payload. That measurement predates #247: an annotated *assignment* now seeds a
whole derivation chain, so its payload is no longer zero and the exclusion had to be
re-priced rather than re-asserted. This is the standing rule that a filing decays —
applied to one of my own, four hours old.

Still excluded, and each is a recorded decision rather than an oversight: return-
annotated factories (``def mk() -> Path``) need external-type resolution of a
function's return annotation, a separate mechanism from reading an annotation in
place; ``.parent``/``.parents[n]`` are attribute reads, measured at 16 sites = 0.7% of
chain-on-Name misses; loop targets, interprocedural propagation and ``self.attr``
receivers are unbuilt.

Every negative from the earlier files still applies here and is re-asserted at the new
entry points: the binding check, the exact-string type comparison, and the
allowlist. An expression receiver must not become a back door around any of them.
"""

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.py import analyze_python

_H = "from pathlib import Path\n\n"


def _edges(root: Path, source: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _tagged(edges: list) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


def _slot(edges: list, method: str) -> str:
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


class TestInlineExpressionReceiver:
    def test_parenthesised_derivation_receiver(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "inline",
            _H + "def h(d: Path, x):\n"
            '    return (d / "f.txt").write_text(x)\n',
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_chained_call_derivation_receiver(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "inlinecall",
            _H + "def h(d: Path, x):\n"
            '    return d.joinpath("f.txt").write_text(x)\n',
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_untyped_root_expression_stays_refused(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL for the negative: no hint on the root, no hint inline."""
        edges = _edges(
            tmp_path / "inline_untyped",
            "def h(d, x):\n"
            '    return (d / "f.txt").write_text(x)\n',
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_inline_receiver_honours_the_allowlist(self, tmp_path: Path) -> None:
        """``.name`` returns ``str``; an expression receiver must not bypass the
        allowlist that an assigned receiver obeys."""
        edges = _edges(
            tmp_path / "inline_str",
            _H + "def h(d: Path, a, b):\n"
            "    return d.name.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"
        assert _tagged(edges) == 0


class TestAnnotatedAssignmentRoot:
    def test_annassign_types_the_receiver(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "ann",
            _H + "def h(raw, x):\n"
            "    d: Path = raw\n"
            "    return d.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_annassign_seeds_a_derivation_chain(self, tmp_path: Path) -> None:
        """The reason the exclusion had to be re-priced: an AnnAssign root now
        feeds #247's derivation propagation."""
        edges = _edges(
            tmp_path / "annchain",
            _H + "def h(raw, x):\n"
            "    d: Path = raw\n"
            '    p = d / "f.txt"\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_annassign_binding_check_still_applies(self, tmp_path: Path) -> None:
        """The measured FP class: a same-named class from another library."""
        edges = _edges(
            tmp_path / "annbad",
            "from fastapi import Path\n\n"
            "def h(raw, a, b):\n"
            "    d: Path = raw\n"
            "    return d.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_annassign_to_an_in_file_class_is_not_catalogued(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(
            tmp_path / "annlocal",
            "class Path:\n"
            "    def write_text(self, x): ...\n"
            "\n"
            "def h(raw, x):\n"
            "    d: Path = raw\n"
            "    return d.write_text(x)\n",
        )
        assert _tagged(edges) == 0


class TestStillExcludedByDecision:
    """Pinned so a later PR that routes them argues with a test, not with silence."""

    def test_return_annotated_factory_is_not_routed(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "retann",
            _H + "def mk() -> Path: ...\n"
            "\n"
            "def h(x):\n"
            "    d = mk()\n"
            "    return d.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"

    def test_parent_attribute_is_not_routed(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "parent",
            _H + "def h(d: Path, x):\n"
            "    p = d.parent\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"
