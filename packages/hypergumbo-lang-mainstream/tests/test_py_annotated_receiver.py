# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-zilag: a parameter's type annotation is receiver evidence, once binding-checked.

WHAT WAS MISSING. A fully annotated, unambiguous receiver produced no I/O boundary at
all::

    import pathlib
    def h(p: pathlib.Path, x):
        return p.write_text(x)      # emitted python:external:...:write_text -> 0 boundaries

The annotation names the receiver's type exactly, and the catalogue holds
``pathlib.Path.write_text`` as ``fs_write``. But the analyzer never routed the annotation
into the symbol id's module slot, so the edge arrived carrying the ``external`` placeholder
and ``gate_named_entry`` correctly refused it as an untyped method call.

SCOPE, AND WHY IT IS THIS NARROW. Measured over six Python repos: 95,245 method-call edges
carry the ``external`` placeholder; 4,160 of those have a receiver with a resolvable
annotation; **63** actually hit the catalogue. 96% of that payload comes from ONE shape —
an annotated *parameter*. ``AnnAssign`` (``p: Path = raw``), return-annotated factories,
attribute reads and generics contribute exactly **zero**, so they are deliberately not
routed. Excluding them is scope, not oversight.

WHY THE BINDING CHECK IS LOAD-BEARING RATHER THAN DEFENSIVE. This change ADDS
classifications, which is the opposite direction from the rest of this subsystem, and a
minted hint is trusted: it bypasses both ``gate_named_entry`` and the ``ambiguous_names``
net by design (measured — routing the hinted path through that gate destroys 61.5-87.2% of
all reported boundaries for zero gain, so it is not an option). So a wrong hint is a
confident false boundary AND a false taint sink, never silence.

Putting the *raw annotation text* in the module slot — which is what the existing
tree-sitter-side resolver does for dotted annotations — was measured to produce confirmed
false boundaries: ``conn: Connection`` in sqlalchemy matched ``sqlite3.Connection.execute``
and minted a database-zone taint sink, with 174 such annotations in the corpus. The cause
is that ``_module_matches`` accepts an unqualified reference as a component suffix, so the
bare text ``Connection`` matches ``sqlite3.Connection``. Resolving the annotation through
its import binding first yields ``sqlalchemy.engine.Connection``, which does not match —
verified directly. Every negative below is one of those measured FP classes.

The resolution reuses ``_import_binding_for`` (INV-kipor, PR #244), so "does an import in
this file rebind this name, and to what?" now has one answer serving three callers.
"""

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import load_full_taint_catalog
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, source: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _tagged(edges: list) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


def _slot(edges: list, method: str) -> str:
    """Module segment of the call edge for ``method``, or '' if no edge."""
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


class TestAnnotatedParameterIsRoutedWhenBindingChecked:
    """The recall gap: a resolvable parameter annotation must reach the module slot."""

    def test_dotted_annotation_with_module_import(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "dotted",
            "import pathlib\n"
            "\n"
            "def h(p: pathlib.Path, x):\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_bare_annotation_with_from_import(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "bare",
            "from pathlib import Path\n"
            "\n"
            "def h(p: Path, x):\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_aliased_module_import_resolves_to_the_real_module(
        self, tmp_path: Path,
    ) -> None:
        """``import pathlib as pl`` must resolve ``pl.Path`` to ``pathlib.Path``,
        not emit the alias text."""
        edges = _edges(
            tmp_path / "alias",
            "import pathlib as pl\n"
            "\n"
            "def h(p: pl.Path, x):\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_socket_parameter_reaches_the_catalogue(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "sock",
            "import socket\n"
            "\n"
            "def h(s: socket.socket, b):\n"
            "    return s.send(b)\n",
        )
        assert _slot(edges, "send") == "socket.socket"
        assert _tagged(edges) >= 1


class TestMeasuredFalsePositiveClassesStayRefused:
    """Each of these was measured to produce a CONFIRMED false boundary under naive
    routing of the raw annotation text. Each must stay at zero."""

    def test_unrelated_library_sharing_a_catalogued_class_name(
        self, tmp_path: Path,
    ) -> None:
        """sqlalchemy's ``Connection`` is not ``sqlite3.Connection``.

        Naive routing put the bare text ``Connection`` in the module slot, which
        ``_module_matches`` accepts as a component suffix of ``sqlite3.Connection`` —
        4 confirmed false db boundaries, 174 such annotations in the corpus.
        """
        edges = _edges(
            tmp_path / "sqla",
            "from sqlalchemy.engine import Connection\n"
            "\n"
            "def h(conn: Connection, q):\n"
            "    return conn.execute(q)\n",
        )
        assert _slot(edges, "execute") != "sqlite3.Connection"
        assert _tagged(edges) == 0

    def test_same_class_name_from_a_different_library(self, tmp_path: Path) -> None:
        """``fastapi.Path`` is a parameter declaration, not a filesystem path."""
        edges = _edges(
            tmp_path / "fastapi",
            "from fastapi import Path\n"
            "\n"
            "def h(p: Path, a, b):\n"
            "    return p.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_unimported_dotted_annotation_is_withheld(self, tmp_path: Path) -> None:
        """No ``import pathlib`` in scope — the name is unbound, so it is not
        evidence. (A forward reference or a stub-only name looks exactly like this.)"""
        edges = _edges(
            tmp_path / "unimported",
            "def h(p: pathlib.Path, x):\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"
        assert _tagged(edges) == 0

    def test_locally_defined_class_shadowing_a_catalogued_name(
        self, tmp_path: Path,
    ) -> None:
        """An in-file ``class Path`` is first-party and carries no catalogue meaning."""
        edges = _edges(
            tmp_path / "shadow",
            "class Path:\n"
            "    def write_text(self, x): ...\n"
            "\n"
            "def h(p: Path, x):\n"
            "    return p.write_text(x)\n",
        )
        assert _tagged(edges) == 0

    def test_forward_reference_string_annotation_is_withheld(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(
            tmp_path / "fwdref",
            "from pathlib import Path\n"
            "\n"
            'def h(p: "Path", x):\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"
        assert _tagged(edges) == 0

    def test_optional_annotation_is_withheld(self, tmp_path: Path) -> None:
        """``Optional[Path]`` cannot be pinned to one module hint."""
        edges = _edges(
            tmp_path / "optional",
            "from typing import Optional\n"
            "from pathlib import Path\n"
            "\n"
            "def h(p: Optional[Path], x):\n"
            "    return p.write_text(x)\n",
        )
        assert _tagged(edges) == 0


class TestTaintSeesTheSameProducerDecision:
    """One producer fix serves both consumers, and one producer refusal binds both."""

    def test_routed_annotation_reaches_the_taint_sink_matcher(
        self, tmp_path: Path,
    ) -> None:
        catalog = load_full_taint_catalog()
        good = _edges(
            tmp_path / "t_good",
            "from pathlib import Path\n"
            "\n"
            "def h(p: Path, a, b):\n"
            "    return p.replace(a, b)\n",
        )
        bad = _edges(
            tmp_path / "t_bad",
            "from fastapi import Path\n"
            "\n"
            "def h(p: Path, a, b):\n"
            "    return p.replace(a, b)\n",
        )
        assert catalog.match_sink(
            "python", "replace", _slot(good, "replace"), "method",
        ) is not None
        assert catalog.match_sink(
            "python", "replace", _slot(bad, "replace"), "method",
        ) is None


class TestDeliberatelyUnroutedShapes:
    """Measured to contribute ZERO payload, so deliberately out of scope.

    Pinned so the exclusion is a recorded decision rather than an accident, and so a
    later PR that routes them has to change a test that says why.
    """

    def test_annassign_is_not_routed(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "annassign",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, x):\n"
            "    p: Path = raw\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"

    def test_return_annotated_factory_is_not_routed(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "retann",
            "from pathlib import Path\n"
            "\n"
            "def mk() -> Path: ...\n"
            "\n"
            "def h(x):\n"
            "    p = mk()\n"
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"
