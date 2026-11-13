# SPDX-License-Identifier: AGPL-3.0-or-later
"""A parameter's type annotation is receiver evidence, once binding-checked.

A fully annotated, unambiguous receiver must reach its catalogue row::

    import pathlib
    def h(p: pathlib.Path, x):
        return p.write_text(x)      # -> python:pathlib.Path:...:write_text, a boundary

The annotation names the receiver's type exactly, and the catalogue holds
``pathlib.Path.write_text`` as ``fs_write``. Unless the annotation is routed into the
symbol id's module slot, the edge carries the ``external`` placeholder and
``gate_named_entry`` correctly refuses it as an untyped method call. (history: WI-zilag)

SCOPE, AND WHY IT IS THIS NARROW. This route reads an annotated *parameter*, the shape
that carries nearly all the catalogue-reaching payload of annotations. Return-annotated
factories, attribute reads and generics are deliberately not routed — scope, not
oversight. (An annotated assignment, ``p: Path = raw``, IS typed, because it can seed a
derivation chain; see ``test_py_receiver_recall_tail.py``.)

WHY THE BINDING CHECK IS LOAD-BEARING RATHER THAN DEFENSIVE. Routing an annotation ADDS
classifications, and a minted hint is trusted: it bypasses both ``gate_named_entry`` and
the ``ambiguous_names`` net by design (routing the hinted path through that gate destroys
most reported boundaries for no gain, so it is not an option). So a wrong hint is a
confident false boundary AND a false taint sink, never silence.

Putting the *raw annotation text* in the module slot produces confirmed false
boundaries: ``conn: Connection`` from sqlalchemy matches ``sqlite3.Connection.execute``
and mints a database-zone taint sink, because ``_module_matches`` accepts an unqualified
reference as a component suffix, so the bare text ``Connection`` matches
``sqlite3.Connection``. Resolving the annotation through its import binding first yields
``sqlalchemy.engine.Connection``, which does not match. Every negative below is one of
those false-positive classes.

The resolution reuses ``_import_binding_for``, so "does an import in this file rebind
this name, and to what?" has one answer shared by every caller. (history: INV-kipor)
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
    """Recall: a resolvable parameter annotation must reach the module slot."""

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
    """Each of these produces a CONFIRMED false boundary if the raw annotation text
    is routed into the module slot. Each must stay at zero."""

    def test_unrelated_library_sharing_a_catalogued_class_name(
        self, tmp_path: Path,
    ) -> None:
        """sqlalchemy's ``Connection`` is not ``sqlite3.Connection``.

        Routing the bare text ``Connection`` into the module slot would let
        ``_module_matches`` accept it as a component suffix of
        ``sqlite3.Connection`` — a false db boundary.
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
    """One producer decision serves both consumers, and one producer refusal binds both."""

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
    """Shapes that reach no catalogue payload, so deliberately out of scope.

    Pinned so the exclusion is a recorded decision rather than an accident: routing
    one means changing a test that says why.

    ``AnnAssign`` is NOT among them. A derivation from a typed receiver keeps its
    type, so an annotated assignment seeds a whole chain;
    ``test_py_receiver_recall_tail.py`` asserts that it is typed.
    """

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
