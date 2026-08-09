# SPDX-License-Identifier: AGPL-3.0-or-later
"""``p = Path(x)`` types the receiver — and an UNBOUND constructor name no longer does.

WHAT WAS MISSING. ``EXTERNAL_CONSTRUCTOR_TYPES`` held exactly two rows, ``open`` and
``socket.socket``, so the single most common filesystem idiom in Python produced no
boundary at all::

    from pathlib import Path
    p = Path(raw)
    p.write_text(data)      # emitted python:external:...:write_text -> 0 boundaries

``pathlib.Path`` has been in ``python.yaml`` the whole time — 9 ``fs_read`` methods and
10 ``fs_write`` — so the catalogue entry existed and nothing could reach it.

TWO TABLE KEYS, NOT ONE. Measured over the corpus: bare ``Path`` (``from pathlib import
Path``) accounts for 103 of 136 constructor sites and 65 of the would-be reaches; dotted
``pathlib.Path`` (``import pathlib``) accounts for 33 sites and 29 reaches. A bare-only
patch delivers 65 of 94. The two forms hit different branches of
``_external_constructor_module`` and each needs its own row.

THE RULE THAT CHANGED, AND WHY IT HAD TO. The bare-name branch trusted an UNBOUND name:
"unbound stays trusted, because for a bare name that means the builtin." That is true of
``open``. It is false of ``Path``, which is not a builtin — an unbound ``Path`` is a
locally defined class, a star-import, or a name from a module the analyzer did not read.
Trusting it would mint an ``fs_write`` boundary and a ``host_fs`` taint sink for any
class in the corpus that happens to be called ``Path``, and 254 corpus sites have a
constructor name that is also an in-repo class name.

So the permitting case is now enumerated rather than assumed: a row is trusted while
unbound only if it is named in :data:`BUILTIN_CONSTRUCTOR_NAMES`. Everything else must be
POSITIVELY bound to the module it claims. ``open`` keeps its old behaviour by being in
that set; ``Path`` must prove itself. This is the same default-deny shape as INV-kipor's
binding check, tightened from "not contradicted" to "confirmed".

DELIBERATELY NOT ADDED, each for a measured reason rather than caution:
``io.StringIO`` / ``io.BytesIO`` — 160 corpus sites, but the catalogue's ``io`` module
declares ZERO method-kind primitives, so typing them reaches nothing.
``multiprocessing.Pipe`` and ``asyncio.open_connection`` — both return a TUPLE, not the
type; they score zero here only because the corpus idiom is tuple unpacking, which the
Name-target guard already refuses, so a row for either is a latent wrong-type source
waiting for the first non-unpacked use.
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
    """The recall gap. Each form enters a different branch of the resolver."""

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
        """A typed constructor result is a derivation ROOT, so PR #247's
        ``TYPE_PRESERVING_MEMBERS`` propagation now has something to start from —
        which is where a chunk of the measured payload actually comes from."""
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
    """The rule this change tightened. Each of these would mint a false ``fs_write``
    boundary AND a false ``host_fs`` taint sink under the old "unbound is the
    builtin" assumption."""

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
    stay trusted — the tightening above must not quietly narrow WI-fuvuj."""

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
        """INV-kipor's original defect, re-asserted at the new branch."""
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

    def test_aliased_from_import_mints_nothing(self, tmp_path: Path) -> None:
        """``from pathlib import Path as P`` misses the table by key. A false
        NEGATIVE, in the safe direction, and measured at 0 of 136 corpus sites."""
        edges = _edges(
            tmp_path / "aliasfrom",
            "from pathlib import Path as P\n"
            "\n"
            "def h(raw, data):\n"
            "    p = P(raw)\n"
            "    return p.write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"

    def test_stringio_is_deliberately_absent(self, tmp_path: Path) -> None:
        """160 corpus sites, and the catalogue's ``io`` module declares ZERO
        method-kind primitives — typing them would reach nothing at all."""
        edges = _edges(
            tmp_path / "sio",
            "import io\n"
            "\n"
            "def h(data):\n"
            "    b = io.StringIO()\n"
            "    return b.write(data)\n",
        )
        assert _tagged(edges) == 0
