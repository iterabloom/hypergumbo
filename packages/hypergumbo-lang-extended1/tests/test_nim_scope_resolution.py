# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Nim call resolves only to a declaration the calling module can see (WI-giloh).

Nim's scope rules, which the resolver applies:

- a declaration without the ``*`` export marker is private to its module;
- another module's exported declaration is visible only through an ``import``
  of that module (narrowed by ``except`` / ``from ... import``), an ``include``,
  or a module that re-exports it with ``export``;
- the calling module's own declaration outranks an imported one.

Each fixture is multi-file and runs the production entry point. Where two
same-named declarations compete, the fixture calls both sides, so the old
one-symbol-per-name registry (last registered wins, in file-discovery order)
gets one of them wrong whichever order the files are found in. Every negative
case failed against the bare-name lookup this replaced; the positive cases
(plain, bracket, search-path, include, re-export) guard the recall the scoping
could lose.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.nim import analyze_nim

UNRESOLVED = "UNRESOLVED"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _targets(root: Path, caller_file: str) -> dict[int, str]:
    """``{line: target file}`` for every call edge made from ``caller_file``;
    a call that resolved to no in-repo declaration maps to UNRESOLVED."""
    result = analyze_nim(root)
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, str] = {}
    for e in result.edges:
        if e.edge_type != "calls" or e.src not in by_id:
            continue
        if by_id[e.src].path != caller_file:
            continue
        tgt = by_id.get(e.dst)
        out[e.line] = tgt.path if tgt is not None else UNRESOLVED
    return out


class TestVisibility:
    def test_private_proc_is_not_a_cross_file_target(self, tmp_path: Path) -> None:
        """nitter's shape: redis_cache.nim's private ``proc get`` took calls from
        modules that cannot see it."""
        _write(tmp_path, {
            "b.nim": "import z\nproc f() =\n  discard get(\"k\")\n",
            "z.nim": "proc get(q: string): string = q\n",
        })
        assert _targets(tmp_path, "b.nim") == {3: UNRESOLVED}

    def test_own_module_outranks_another_module(self, tmp_path: Path) -> None:
        """config.nim's shape: the caller declares its own ``get*`` and another
        module declares one too. Symmetric, so a one-symbol-per-name registry gets
        one side wrong whichever file registers last."""
        _write(tmp_path, {
            "config.nim": (
                "proc get*(k: string): string = k\n"
                "proc load() =\n"
                "  discard get(\"k\")\n"
            ),
            "z.nim": (
                "proc get*(q: string): string = q\n"
                "proc use() =\n"
                "  discard get(\"q\")\n"
            ),
        })
        assert _targets(tmp_path, "config.nim") == {3: "config.nim"}
        assert _targets(tmp_path, "z.nim") == {3: "z.nim"}

    def test_own_module_outranks_an_imported_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "config.nim": (
                "import z\n"
                "proc get*(k: string): string = k\n"
                "proc load() =\n"
                "  discard get(\"k\")\n"
            ),
            "z.nim": (
                "import config\n"
                "proc get*(q: string): string = q\n"
                "proc use() =\n"
                "  discard get(\"q\")\n"
            ),
        })
        assert _targets(tmp_path, "config.nim") == {4: "config.nim"}
        assert _targets(tmp_path, "z.nim") == {4: "z.nim"}


class TestImportScope:
    def test_unimported_module_is_not_a_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "proc f() =\n  helper()\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {2: UNRESOLVED}

    def test_imported_module_is_a_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "import z\nproc f() =\n  helper()\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim"}

    def test_relative_bracket_import(self, tmp_path: Path) -> None:
        """``import ../lib/[z, y]`` and ``import ".."/[z]`` name sibling-directory
        modules relative to the importing file."""
        _write(tmp_path, {
            "app/a.nim": "import ../lib/[y, z]\nproc f() =\n  helper()\n  other()\n",
            "app/b.nim": 'import ".."/[lib/z]\nproc g() =\n  helper()\n',
            "lib/y.nim": "proc other*() = discard\n",
            "lib/z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "app/a.nim") == {3: "lib/z.nim", 4: "lib/y.nim"}
        assert _targets(tmp_path, "app/b.nim") == {3: "lib/z.nim"}

    def test_search_path_import(self, tmp_path: Path) -> None:
        """A bare module name not beside the importer is found on the search path
        (``src/`` in a nimble package): any repository file with that module name."""
        _write(tmp_path, {
            "tests/t.nim": "import z\nproc f() =\n  helper()\n",
            "src/z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "tests/t.nim") == {3: "src/z.nim"}

    def test_stdlib_import_names_no_repository_module(self, tmp_path: Path) -> None:
        """``std/z`` is the standard library's module, not the repository's z.nim."""
        _write(tmp_path, {
            "a.nim": "import std/z\nproc f() =\n  helper()\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: UNRESOLVED}

    def test_except_hides_a_name(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "import z except helper\nproc f() =\n  helper()\n  other()\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: UNRESOLVED, 4: "z.nim"}

    def test_from_import_admits_only_the_named(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "from z import helper\nproc f() =\n  helper()\n  other()\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim", 4: UNRESOLVED}

    def test_include_sees_private_declarations(self, tmp_path: Path) -> None:
        """``include`` pastes the file in, so its private procs are the includer's."""
        _write(tmp_path, {
            "a.nim": "include z\nproc f() =\n  helper()\n",
            "z.nim": "proc helper() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim"}

    def test_included_file_sees_its_includer(self, tmp_path: Path) -> None:
        """The pasted file calls the includer's private procs and uses its imports."""
        _write(tmp_path, {
            "sel.nim": "import util\nproc base() = discard\ninclude impl\n",
            "impl.nim": "proc run() =\n  base()\n  helper()\n",
            "util.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "impl.nim") == {2: "sel.nim", 3: "util.nim"}

    def test_included_alternatives_keep_their_own(self, tmp_path: Path) -> None:
        """chronos's shape: ``when`` includes one of several platform files, each
        declaring the same API. A call in one goes to its own declaration, not a
        sibling's; symmetric, so a path-sorted pick gets one side wrong."""
        _write(tmp_path, {
            "sel.nim": (
                "when defined(linux):\n  include epoll\n"
                "else:\n  include kqueue\n"
            ),
            "epoll.nim": "proc select() = discard\nproc run() =\n  select()\n",
            "kqueue.nim": "proc select() = discard\nproc run() =\n  select()\n",
        })
        assert _targets(tmp_path, "epoll.nim") == {3: "epoll.nim"}
        assert _targets(tmp_path, "kqueue.nim") == {3: "kqueue.nim"}


class TestReExport:
    def test_module_reexport(self, tmp_path: Path) -> None:
        """``export z`` in m.nim makes z's exported procs visible to m's importers,
        transitively."""
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n",
            "m.nim": "import n\nexport n\n",
            "n.nim": "import z\nexport z\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim"}

    def test_symbol_reexport(self, tmp_path: Path) -> None:
        """``export helper`` re-exports one imported symbol, not its module."""
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n  other()\n",
            "m.nim": "import z\nexport helper\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim", 4: UNRESOLVED}

    def test_module_reexport_except_withholds_names(self, tmp_path: Path) -> None:
        """nimble's compat/syntaxes.nim: ``export syntaxes except parseAll`` passes
        the module on minus the names it wraps itself."""
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n  other()\n",
            "m.nim": "import z\nexport z except helper\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: UNRESOLVED, 4: "z.nim"}

    def test_import_without_export_is_not_transitive(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n",
            "m.nim": "import z\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: UNRESOLVED}

    def test_import_cycle_terminates(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n",
            "m.nim": "import n\nexport n\nproc helper*() = discard\n",
            "n.nim": "import m\nexport m\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "m.nim"}


    def test_reexport_cycle_is_order_independent(self, tmp_path: Path) -> None:
        """Three modules re-export each other in a ring; each importer sees all
        three, whichever module the closure reaches first."""
        _write(tmp_path, {
            "m.nim": "import n\nexport n\nproc mh*() = discard\n",
            "n.nim": "import o\nexport o\nproc nh*() = discard\n",
            "o.nim": "import m\nexport m\nproc oh*() = discard\n",
            "a.nim": "import m\nproc f() =\n  mh()\n  nh()\n  oh()\n",
            "b.nim": "import n\nproc f() =\n  mh()\n  nh()\n  oh()\n",
            "c.nim": "import o\nproc f() =\n  mh()\n  nh()\n  oh()\n",
        })
        want = {3: "m.nim", 4: "n.nim", 5: "o.nim"}
        for caller in ("a.nim", "b.nim", "c.nim"):
            assert _targets(tmp_path, caller) == want, caller

    def test_from_import_narrows_a_reexported_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "from m import helper\nproc f() =\n  helper()\n  other()\n",
            "m.nim": "import z\nexport z\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim", 4: UNRESOLVED}

    def test_reexport_of_a_from_import_keeps_its_filter(self, tmp_path: Path) -> None:
        """m re-exports module z but imported only ``helper`` from it."""
        _write(tmp_path, {
            "a.nim": "import m\nproc f() =\n  helper()\n  other()\n",
            "b.nim": "from m import helper, other\nproc f() =\n  helper()\n  other()\n",
            "m.nim": "from z import helper\nexport z\n",
            "z.nim": "proc helper*() = discard\nproc other*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "z.nim", 4: UNRESOLVED}
        assert _targets(tmp_path, "b.nim") == {3: "z.nim", 4: UNRESOLVED}

    def test_from_import_skips_an_unnamed_symbol_reexport(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.nim": "from m import other\nproc f() =\n  helper()\n",
            "m.nim": "import z\nexport helper\n",
            "z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: UNRESOLVED}

    def test_import_reaches_the_included_files(self, tmp_path: Path) -> None:
        """chronos imports selectors2, whose API is declared in the files it
        includes: importing a module grants its whole include group."""
        _write(tmp_path, {
            "a.nim": "import sel\nproc f() =\n  select()\n",
            "sel.nim": "include impl\n",
            "impl.nim": "proc select*() = discard\n",
        })
        assert _targets(tmp_path, "a.nim") == {3: "impl.nim"}

    def test_relative_import_of_a_missing_file(self, tmp_path: Path) -> None:
        """``import ./gone`` names no repository file; a same-named module
        elsewhere is not a stand-in for it."""
        _write(tmp_path, {
            "app/a.nim": "import ./z\nproc f() =\n  helper()\n",
            "lib/z.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "app/a.nim") == {3: UNRESOLVED}


class TestQualifiedAndAmbiguous:
    def test_module_qualified_call_picks_that_module(self, tmp_path: Path) -> None:
        """``a.helper()`` names module a even when b also exports a ``helper``.
        Both qualifiers are called, so a bare-name lookup gets one wrong."""
        _write(tmp_path, {
            "c.nim": "import a, b\nproc f() =\n  a.helper()\n  b.helper()\n",
            "a.nim": "proc helper*() = discard\n",
            "b.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "c.nim") == {3: "a.nim", 4: "b.nim"}

    def test_aliased_module_call(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "c.nim": "import a as q, b as r\nproc f() =\n  q.helper()\n  r.helper()\n",
            "a.nim": "proc helper*() = discard\n",
            "b.nim": "proc helper*() = discard\n",
        })
        assert _targets(tmp_path, "c.nim") == {3: "a.nim", 4: "b.nim"}

    def test_value_shadowing_a_module_name_is_a_ufcs_call(self, tmp_path: Path) -> None:
        """nitter: ``query.getTabClass(posts)`` where ``query`` is a parameter and
        also an imported module that declares no ``getTabClass``."""
        _write(tmp_path, {
            "search.nim": (
                "import query, render\n"
                "proc show(query: int) =\n"
                "  discard query.getTabClass()\n"
                "  discard query.parse()\n"
            ),
            "query.nim": "proc parse*(q: int): int = q\n",
            "render.nim": "proc getTabClass*(q: int): string = \"\"\n",
        })
        assert _targets(tmp_path, "search.nim") == {3: "render.nim", 4: "query.nim"}

    def test_qualified_call_on_external_module_stays_unresolved(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "c.nim": "import strutils as su\nproc f(s: string) =\n  discard su.strip(s)\n",
            "z.nim": "proc strip*(s: string): string = s\n",
        })
        assert _targets(tmp_path, "c.nim") == {3: UNRESOLVED}

    def test_ufcs_call_is_scoped_too(self, tmp_path: Path) -> None:
        """``cfg.get(...)`` is UFCS for ``get(cfg, ...)``: the same visibility rule."""
        _write(tmp_path, {
            "b.nim": "proc f(cfg: int) =\n  discard cfg.get(\"k\")\n",
            "z.nim": "proc get(c: int; q: string): string = q\n",
        })
        assert _targets(tmp_path, "b.nim") == {2: UNRESOLVED}

    def test_two_visible_overloads_lower_confidence(self, tmp_path: Path) -> None:
        """Two imported modules both export ``helper``: Nim picks by argument
        types, which the analyzer does not know, so the edge says it guessed."""
        _write(tmp_path, {
            "c.nim": "import a, b\nproc f() =\n  helper()\nproc g() =\n  only()\n",
            "a.nim": "proc helper*(x: int) = discard\nproc only*() = discard\n",
            "b.nim": "proc helper*(s: string) = discard\n",
        })
        result = analyze_nim(tmp_path)
        by_id = {s.id: s for s in result.symbols}
        conf = {
            e.line: e.confidence for e in result.edges
            if e.edge_type == "calls" and e.dst in by_id and by_id[e.src].path == "c.nim"
        }
        assert set(conf) == {3, 5}  # reach: both calls resolved
        assert conf[3] < conf[5]
