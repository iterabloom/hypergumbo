# SPDX-License-Identifier: AGPL-3.0-or-later
"""``p = Path(x)`` types the receiver — and an UNBOUND non-builtin constructor name does not.

The most common filesystem idiom in Python must reach the ``pathlib.Path`` rows
(``fs_read`` / ``fs_write`` methods) in ``python.yaml``::

    from pathlib import Path
    p = Path(raw)
    p.write_text(data)      # -> python:pathlib.Path:...:write_text, a boundary

TWO TABLE KEYS, NOT ONE. Bare ``Path`` (``from pathlib import Path``) and dotted
``pathlib.Path`` (``import pathlib``) are both common, and they hit different branches
of ``_external_constructor_type``, so each needs its own row; a single key would cover
only part of the sites while looking complete.

UNBOUND IS TRUSTED ONLY FOR A REAL BUILTIN. "An unbound bare name means the builtin" is
true of ``open``. It is false of ``Path``, which is not a builtin — an unbound ``Path``
is a locally defined class, a star-import, or a name from a module the analyzer did not
read. Trusting it would mint an ``fs_write`` boundary and a ``host_fs`` taint sink for
any class that happens to be called ``Path``, and constructor names that are also
in-repo class names are common. So the permitting case is enumerated rather than
assumed: a row is trusted while unbound only if it is named in
:data:`BUILTIN_CONSTRUCTOR_NAMES`. Everything else must be POSITIVELY bound to the
module it claims — "confirmed", not merely "not contradicted" (INV-kipor's default-deny
binding check).

DELIBERATELY NOT TYPED: ``io.StringIO`` / ``io.BytesIO`` — the catalogue's ``io``
module declares ZERO method-kind primitives, so typing them reaches nothing; and
``asyncio.open_connection``, which returns a TUPLE, not the type. ``multiprocessing.Pipe``
also returns a tuple but IS in the table, because the table is derived from the
catalogue's receiver types. The usual tuple-unpacking idiom (``a, b = Pipe()``) is
refused by the Name-target guard, so only a non-unpacked ``c = Pipe()`` is typed —
as the pair, which is a latent wrong-type source.
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
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


class TestBothConstructorFormsReachTheCatalogue:
    """Recall: each form enters a different branch of the resolver."""

    def test_from_import_bare_name(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "bare",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            "    p = Path(raw)\n"
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_module_import_dotted_name(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "dotted",
            "import pathlib\n"
            "\n"
            "def h(raw):\n"
            "    p = pathlib.Path(raw)\n"
            "    return p.read_text()\n",
        )
        assert _slot(edges, "read_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_aliased_module_import(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "aliasmod",
            "import pathlib as pl\n"
            "\n"
            "def h(raw):\n"
            "    p = pl.Path(raw)\n"
            "    return p.read_bytes()\n",
        )
        assert _slot(edges, "read_bytes") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_constructor_result_feeds_the_derivation_allowlist(
        self, tmp_path: Path,
    ) -> None:
        """A typed constructor result is a derivation ROOT, so
        ``TYPE_PRESERVING_MEMBERS`` propagation has something to start from."""
        edges = _edges(
            tmp_path / "chain",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            '    p = Path(raw) / "out.txt"\n'
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1


class TestAnUnboundNameIsNoLongerTrusted:
    """Each of these would mint a false ``fs_write`` boundary AND a false
    ``host_fs`` taint sink if an unbound name were trusted as the builtin."""

    def test_locally_defined_class_named_path(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "localclass",
            "class Path:\n"
            "    def write_text(self, d): ...\n"
            "\n"
            "def h(raw, data):\n"
            "    p = Path(raw)\n"
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_completely_unbound_name(self, tmp_path: Path) -> None:
        """No import of any kind — a star-import, or a module never read. ``Path``
        is not a builtin, so absence of evidence is not evidence of pathlib."""
        edges = _edges(
            tmp_path / "unbound",
            "def h(raw, data):\n"
            "    p = Path(raw)\n"
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_same_name_from_a_different_library(self, tmp_path: Path) -> None:
        """``fastapi.Path`` is a parameter declaration, not a filesystem path."""
        edges = _edges(
            tmp_path / "fastapi",
            "from fastapi import Path\n"
            "\n"
            "def h(raw, a, b):\n"
            "    p = Path(raw)\n"
            "    return p.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"
        assert _tagged(edges) == 0


class TestBuiltinRowsKeepTheirOldBehaviour:
    """REGRESSION GUARD. ``open`` genuinely is a builtin, so an unbound ``open`` must
    stay trusted — the default-deny above must not quietly narrow the ``open`` ->
    ``file`` typing. (history: WI-fuvuj)"""

    def test_unbound_open_is_still_trusted(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "openbuiltin",
            "def h(p, data):\n"
            "    f = open(p)\n"
            "    return f.write(data)\n",
        )
        assert _slot(edges, "write") == "file"
        assert _tagged(edges) >= 1

    def test_shadowed_open_is_still_refused(self, tmp_path: Path) -> None:
        """An ``open`` bound to another module is refused the ``file`` type.
        (history: INV-kipor)"""
        edges = _edges(
            tmp_path / "openshadow",
            "from decoy_lib import open\n"
            "\n"
            "def h(p, data):\n"
            "    f = open(p)\n"
            "    return f.write(data)\n",
        )
        assert _tagged(edges) == 0


class TestTaintSeesTheSameProducerDecision:
    """One producer decision, both consumers — the boundary tagger and the taint
    sink matcher. A wrong type here is a false SINK, not just a false boundary."""

    def test_bound_constructor_reaches_the_sink_matcher(self, tmp_path: Path) -> None:
        catalog = load_full_taint_catalog()
        good = _edges(
            tmp_path / "t_good",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, a, b):\n"
            "    p = Path(raw)\n"
            "    return p.replace(a, b)\n",
        )
        bad = _edges(
            tmp_path / "t_bad",
            "class Path:\n"
            "    def replace(self, a, b): ...\n"
            "\n"
            "def h(raw, a, b):\n"
            "    p = Path(raw)\n"
            "    return p.replace(a, b)\n",
        )
        assert catalog.match_sink(
            "python", "replace", _slot(good, "replace"), "method",
        ) is not None
        assert catalog.match_sink(
            "python", "replace", _slot(bad, "replace"), "method",
        ) is None


class TestRecordedNonGoals:
    """Pinned so a later PR that changes one argues with an assertion."""

    def test_aliased_from_import_binds_through_the_alias(self, tmp_path: Path) -> None:
        """``from pathlib import Path as P`` misses the constructor table by key,
        but the imported-class rule (WI-makij) asks the BINDING rather than the
        table, and the alias binds to ``pathlib.Path`` exactly: the instance is
        typed and the catalogued row is reached."""
        edges = _edges(
            tmp_path / "aliasfrom",
            "from pathlib import Path as P\n"
            "\n"
            "def h(raw, data):\n"
            "    p = P(raw)\n"
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_stringio_is_deliberately_absent(self, tmp_path: Path) -> None:
        """The catalogue's ``io`` module declares ZERO method-kind primitives —
        typing ``StringIO`` would reach nothing at all."""
        edges = _edges(
            tmp_path / "sio",
            "import io\n"
            "\n"
            "def h(data):\n"
            "    b = io.StringIO()\n"
            "    return b.write(data)\n",
        )
        assert _tagged(edges) == 0
