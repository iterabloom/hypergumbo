# SPDX-License-Identifier: AGPL-3.0-or-later
"""A derivation from a typed receiver keeps the type — for an ALLOWLISTED member only.

PR #246 taught the analyzer that an annotated parameter is receiver evidence, so ``d`` in
``def h(d: Path, x)`` is correctly typed ``pathlib.Path``. The type was then lost the moment
anything was derived from it::

    p = d / "f.txt";      p.write_text(x)    # slot 'external', 0 boundaries
    p = d.joinpath("f");  p.write_text(x)    # slot 'external', 0 boundaries

WHY THIS IS AN ALLOWLIST AND NOT "PROPAGATE THE RECEIVER TYPE". Most members of a typed
receiver do NOT return that type, and propagating through them is the single most dangerous
error available here. ``Path.read_text`` returns ``str``; ``.stat`` returns ``os.stat_result``;
``.exists`` returns ``bool``; ``.open`` returns a file object; ``.glob``/``.iterdir`` return
iterators; ``.name``/``.stem``/``.suffix``/``.as_posix`` return ``str``.

Measured: propagating by default scores **25.9% precision** on adversarial fixtures — 7 of
27 added boundaries correct — and mints 16 false taint sinks including 2 ``database`` and 2
``network``. Across 8 Python repos, 1,955 of 2,296 (85.1%) hop>=1 propagations are provably
wrong or unverifiable, and 20 of the 21 provably-wrong ones are a Path-to-``str`` transition.
The allowlist scores 85.7% on the same fixtures and costs 3 boundaries out of 427 (0.7%).
Default-deny is therefore not caution, it is the measured design.

WHY THE HINT IS COMPARED BY STRING EQUALITY. ``_module_matches`` is permissive by design —
it accepts an unqualified reference as a component suffix, so it treats a vendored
``mylib.pathlib.Path`` as ``pathlib.Path``. Using it here mints a real ``fs_write`` boundary
plus a ``host_fs`` taint sink for a third-party class that merely shares a name. The table is
keyed by exact type string, so the comparison is a dict lookup and the vendored case simply
misses.

THERE IS NO BACKSTOP, which is why the allowlist carries the safety. A minted hint bypasses
both ``gate_named_entry`` and the ``ambiguous_names`` net by design. Control measurement: the
19 catalogued ``pathlib.Path`` member names — 10 of them taint sinks, including ``replace``
(also ``str.replace``) and ``rename`` (also ``DataFrame.rename``) — match **zero** times with
no module hint, and are all reachable the instant a hint appears.

SCOPE. Calls and the ``/`` operator on a bare-name or derived receiver. Deliberately NOT
routed: ``.parent`` and ``.parents[n]`` (attribute reads, a different AST shape and a smaller
population), ``Path.cwd()``/``Path.home()`` (classmethods on the type, not derivations),
interprocedural propagation, ``for p in d.iterdir()`` loop targets, and ``self.attr``
receivers. Each is a recorded exclusion, not an oversight.
"""

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import load_full_taint_catalog
from hypergumbo_lang_mainstream.py import (
    TYPE_PRESERVING_MEMBERS,
    analyze_python,
)

_HEADER = "from pathlib import Path\n\n"


def _edges(root: Path, source: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _tagged(edges: list) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


def _slot(edges: list, method: str) -> str:
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


class TestAllowlistedDerivationsKeepTheType:
    def test_truediv_operator(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "div",
            _HEADER + "def h(d: Path, x):\n"
            '    p = d / "f.txt"\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_joinpath_call(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "join",
            _HEADER + "def h(d: Path, x):\n"
            '    p = d.joinpath("f.txt")\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_derivation_chains(self, tmp_path: Path) -> None:
        """A derived value is itself a valid receiver for a further derivation."""
        edges = _edges(
            tmp_path / "chain",
            _HEADER + "def h(d: Path, x):\n"
            '    p = d / "a" / "b.txt"\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_with_suffix_and_resolve(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "suffix",
            _HEADER + "def h(d: Path, x):\n"
            '    p = d.with_suffix(".bak").resolve()\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1


class TestMeasuredFalsePositiveClassesStayRefused:
    """Each is a measured FP class. All are at zero today and must remain there."""

    def test_numeric_division_is_not_a_path(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "numeric",
            "def h(a: int, b: int):\n"
            "    q = a / b\n"
            '    return q.write_text("x")\n',
        )
        assert _tagged(edges) == 0

    def test_os_path_join_returns_str_not_path(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "ospj",
            "import os\n\n"
            "def h(d: str, x):\n"
            '    p = os.path.join(d, "f.txt")\n'
            "    return p.write_text(x)\n",
        )
        assert _tagged(edges) == 0

    def test_path_to_str_member_does_not_propagate(self, tmp_path: Path) -> None:
        """``.name`` returns ``str``; ``str.replace`` must not become ``Path.replace``.

        This is the exact shape behind 20 of the 21 provably-wrong corpus propagations,
        and it is the WI-razol false-positive cascade the catalogue's ``ambiguous_names``
        list exists to prevent.
        """
        edges = _edges(
            tmp_path / "tostr",
            _HEADER + "def h(d: Path, a, b):\n"
            "    n = d.name\n"
            "    return n.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_read_text_returns_str(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "readtext",
            _HEADER + "def h(d: Path, a, b):\n"
            "    s = d.read_text()\n"
            "    return s.replace(a, b)\n",
        )
        assert _slot(edges, "replace") != "pathlib.Path"

    def test_predicate_member_returns_bool(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "bool",
            _HEADER + "def h(d: Path, x):\n"
            "    b = d.exists()\n"
            "    return b.write_text(x)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"

    def test_vendored_lookalike_type_does_not_propagate(self, tmp_path: Path) -> None:
        """A third-party class sharing the name must not inherit path semantics.

        ``_module_matches`` would accept ``mylib.pathlib.Path`` as ``pathlib.Path``;
        the table is keyed by exact type string, so this misses.
        """
        edges = _edges(
            tmp_path / "vendored",
            "from mylib.pathlib import Path\n\n"
            "def h(d: Path, x):\n"
            '    p = d / "f.txt"\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_untyped_receiver_derivation_stays_external(self, tmp_path: Path) -> None:
        """No hint on the root means no hint on the derivation."""
        edges = _edges(
            tmp_path / "untyped",
            "def h(d, x):\n"
            '    p = d / "f.txt"\n'
            "    return p.write_text(x)\n",
        )
        assert _slot(edges, "write_text") == "external"
        assert _tagged(edges) == 0


class TestTaintSeesTheSameProducerDecision:
    def test_allowlisted_derivation_reaches_the_sink_matcher_and_str_does_not(
        self, tmp_path: Path,
    ) -> None:
        catalog = load_full_taint_catalog()
        good = _edges(
            tmp_path / "tk_good",
            _HEADER + "def h(d: Path, a, b):\n"
            '    p = d / "f.txt"\n'
            "    return p.replace(a, b)\n",
        )
        bad = _edges(
            tmp_path / "tk_bad",
            _HEADER + "def h(d: Path, a, b):\n"
            "    n = d.name\n"
            "    return n.replace(a, b)\n",
        )
        assert catalog.match_sink(
            "python", "replace", _slot(good, "replace"), "method",
        ) is not None
        assert catalog.match_sink(
            "python", "replace", _slot(bad, "replace"), "method",
        ) is None


class TestTheAllowlistIsDataAndStaysHonest:
    """Parity over the table itself, so a future row cannot skip the reasoning."""

    def test_table_is_non_empty_and_keyed_by_exact_type(self) -> None:
        assert TYPE_PRESERVING_MEMBERS, "table emptied — the parity test would be vacuous"
        for type_name in TYPE_PRESERVING_MEMBERS:
            assert "." in type_name, (
                f"{type_name!r} must be a fully qualified type; a bare name would "
                f"match any same-named third-party class"
            )

    def test_no_known_transforming_member_is_allowlisted(self) -> None:
        """The members measured to break propagation must never appear in the table."""
        transforming = {
            "read_text", "read_bytes", "as_posix", "as_uri", "name", "stem",
            "suffix", "suffixes", "parts", "exists", "is_dir", "is_file",
            "stat", "lstat", "open", "glob", "rglob", "iterdir", "owner",
            "group", "is_symlink", "samefile", "match",
        }
        for type_name, members in TYPE_PRESERVING_MEMBERS.items():
            leaked = members & transforming
            assert not leaked, (
                f"{type_name}: {sorted(leaked)} do not return {type_name}; "
                f"propagating through them was measured at 25.9% precision"
            )

    def test_every_allowlisted_member_actually_preserves_the_type(
        self, tmp_path: Path,
    ) -> None:
        """Each row is exercised end-to-end through the production analyzer.

        A row that does not survive this is a row whose claim is false.
        """
        members = sorted(TYPE_PRESERVING_MEMBERS["pathlib.Path"] - {"__truediv__"})
        assert members, "expected named members besides the operator"
        for member in members:
            edges = _edges(
                tmp_path / f"row_{member}",
                _HEADER + "def h(d: Path, x, arg):\n"
                f"    p = d.{member}(arg)\n"
                "    return p.write_text(x)\n",
            )
            assert _slot(edges, "write_text") == "pathlib.Path", (
                f"{member!r} is in the allowlist but did not preserve the type"
            )
