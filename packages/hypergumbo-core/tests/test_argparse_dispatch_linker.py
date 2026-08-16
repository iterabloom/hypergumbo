# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Python argparse subcommand dispatch linker (INV-zuhig).

argparse is the stdlib CLI framework: subcommand handlers are registered
via ``parser.set_defaults(func=handler)`` and dispatched at runtime with
``args.func(args)``. The analyzer records the registration as a
``references`` edge — which taint source minting never consumes — so
every argparse-dispatched handler is unreachable from production
structure. INV-zuhig measured the consequence on the self-proof: all 18
claims declare ``cmd_*`` handlers as ``start_at: callee`` sources, and
under artifact scope those sources minted ZERO flows because the only
concrete callers were tests.

The linker bridges the registration site to the handler with a
``dispatches_to`` edge, following the family convention set by
``go_cobra.py`` (the same CLI-dispatch shape in Go).

The tests exercise two layers, mirroring ``test_go_cobra_linker.py``:
- Regex-level detection (``_find_handler_registrations``,
  ``_file_mentions_argparse``).
- Linker-level integration (``argparse_dispatch_linker(ctx)``): symbol
  resolution, kind filtering, enclosing-function wiring, dedupe,
  disambiguation fallback, and the language gate.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.argparse_dispatch import (
    _file_mentions_argparse,
    _find_handler_registrations,
    argparse_dispatch_linker,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestFileMentionsArgparse:
    """Pre-filter: only files that mention argparse are considered."""

    def test_true_on_plain_import(self) -> None:
        assert _file_mentions_argparse(b"import argparse\n") is True

    def test_true_on_from_import(self) -> None:
        src = b"from argparse import ArgumentParser\n"
        assert _file_mentions_argparse(src) is True

    def test_false_without_mention(self) -> None:
        src = b"import click\n\nx.set_defaults(func=go)\n"
        assert _file_mentions_argparse(src) is False


class TestFindHandlerRegistrations:
    """Parse ``kwarg=identifier`` pairs out of set_defaults() calls."""

    def test_finds_func_kwarg(self) -> None:
        src = b"p.set_defaults(func=cmd_sketch)\n"
        assert _find_handler_registrations(src) == [
            ("func", "cmd_sketch", 1),
        ]

    def test_finds_nonstandard_kwarg_name(self) -> None:
        # The kwarg name is user-chosen convention, not argparse API —
        # ``handler=``, ``command=``, ``action=`` all appear in the wild.
        src = b"p.set_defaults(handler=do_thing)\n"
        assert _find_handler_registrations(src) == [
            ("handler", "do_thing", 1),
        ]

    def test_skips_literal_values(self) -> None:
        # Booleans / None / numbers / strings are configuration defaults,
        # not dispatch targets.
        src = (
            b"p.set_defaults(func=cmd_sketch, first_party_priority=True,\n"
            b"               verbose=False, level=None, count=0, name='x')\n"
        )
        assert _find_handler_registrations(src) == [
            ("func", "cmd_sketch", 1),
        ]

    def test_skips_call_valued_kwarg(self) -> None:
        # ``func=make_handler()`` binds the RESULT of a call; the handler
        # identity is not statically the callee name.
        src = b"p.set_defaults(func=make_handler())\n"
        assert _find_handler_registrations(src) == []

    def test_skips_lambda(self) -> None:
        src = b"p.set_defaults(func=lambda args: 0)\n"
        assert _find_handler_registrations(src) == []

    def test_finds_dotted_handler(self) -> None:
        src = b"p.set_defaults(func=handlers.do_run)\n"
        assert _find_handler_registrations(src) == [
            ("func", "handlers.do_run", 1),
        ]

    def test_multiline_call(self) -> None:
        src = (
            b"p.set_defaults(\n"
            b"    func=cmd_run,\n"
            b"    language_proportional=True,\n"
            b")\n"
        )
        assert _find_handler_registrations(src) == [
            ("func", "cmd_run", 2),
        ]

    def test_multiple_registrations(self) -> None:
        src = (
            b"p_a.set_defaults(func=cmd_a)\n"
            b"p_b.set_defaults(func=cmd_b)\n"
        )
        assert _find_handler_registrations(src) == [
            ("func", "cmd_a", 1),
            ("func", "cmd_b", 2),
        ]

    def test_unclosed_call_at_eof_does_not_crash(self) -> None:
        # Pathological input: file ends mid-call. The span scanner caps
        # at EOF and the kwarg is still captured (trailing newline is a
        # valid terminator).
        src = b"p.set_defaults(func=cmd_x,\n"
        assert _find_handler_registrations(src) == [
            ("func", "cmd_x", 1),
        ]


def _make_symbol(
    path: Path, name: str, kind: str, start: int, end: int,
) -> Symbol:
    return Symbol(
        id=f"py:{path}:{start}-{end}:{name}:{kind}",
        name=name,
        kind=kind,
        language="python",
        path=str(path),
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
    )


_CLI_SOURCE = (
    "import argparse\n"                                     # 1
    "\n"                                                    # 2
    "def cmd_sketch(args):\n"                               # 3
    "    return 0\n"                                        # 4
    "\n"                                                    # 5
    "def build_parser():\n"                                 # 6
    "    parser = argparse.ArgumentParser()\n"              # 7
    "    sub = parser.add_subparsers()\n"                   # 8
    "    p = sub.add_parser('sketch')\n"                    # 9
    "    p.set_defaults(func=cmd_sketch)\n"                 # 10
    "    return parser\n"                                   # 11
)


class TestArgparseDispatchLinkerIntegration:
    """End-to-end linker runs on real tmp_path Python sources."""

    def _write_cli(self, tmp_path: Path, source: str = _CLI_SOURCE) -> Path:
        p = tmp_path / "cli.py"
        p.write_text(source)
        return p

    def _symbols(self, path: Path) -> tuple[Symbol, Symbol]:
        handler = _make_symbol(path, "cmd_sketch", "function", 3, 4)
        builder = _make_symbol(path, "build_parser", "function", 6, 11)
        return builder, handler

    def test_links_registration_to_handler(self, tmp_path: Path) -> None:
        file_path = self._write_cli(tmp_path)
        builder, handler = self._symbols(file_path)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[builder, handler],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        matching = [
            e for e in result.edges
            if e.dst == handler.id and e.edge_type == "dispatches_to"
        ]
        assert len(matching) == 1
        edge = matching[0]
        assert edge.src == builder.id
        assert edge.confidence == 0.85
        assert edge.meta is not None
        assert edge.meta.get("argparse_kwarg") == "func"
        assert edge.meta.get("handler_name") == "cmd_sketch"
        assert edge.meta.get("framework_dispatch") == "argparse"
        assert "disambiguation_fallback" not in edge.meta

    def test_noop_when_no_python_detected(self, tmp_path: Path) -> None:
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"go"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []

    def test_noop_without_argparse_mention(self, tmp_path: Path) -> None:
        # set_defaults on a non-argparse object (e.g. a config registry)
        # in a file that never mentions argparse: skipped by pre-filter.
        p = tmp_path / "config.py"
        p.write_text(
            "def cmd_x(args):\n"
            "    return 0\n"
            "\n"
            "def setup(registry):\n"
            "    registry.set_defaults(func=cmd_x)\n"
        )
        handler = _make_symbol(p, "cmd_x", "function", 1, 2)
        setup = _make_symbol(p, "setup", "function", 4, 5)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[handler, setup],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []

    def test_noop_without_set_defaults_anchor(self, tmp_path: Path) -> None:
        p = tmp_path / "util.py"
        p.write_text("import argparse\n\nHELP = 'usage'\n")
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []

    def test_unresolvable_handler_emits_nothing(self, tmp_path: Path) -> None:
        file_path = self._write_cli(tmp_path)
        builder, _handler = self._symbols(file_path)
        # cmd_sketch is NOT in the symbol table.
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[builder],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []

    def test_non_callable_symbol_kind_is_filtered(
        self, tmp_path: Path,
    ) -> None:
        # ``set_defaults(level=DEFAULT_LEVEL)`` where DEFAULT_LEVEL is an
        # in-repo variable: identifier-valued but not a dispatch target.
        p = tmp_path / "cli.py"
        p.write_text(
            "import argparse\n"                        # 1
            "DEFAULT_LEVEL = 3\n"                      # 2
            "def build_parser():\n"                    # 3
            "    p = argparse.ArgumentParser()\n"      # 4
            "    p.set_defaults(level=DEFAULT_LEVEL)\n"  # 5
            "    return p\n"                           # 6
        )
        var_sym = _make_symbol(p, "DEFAULT_LEVEL", "variable", 2, 2)
        builder = _make_symbol(p, "build_parser", "function", 3, 6)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[var_sym, builder],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []

    def test_dotted_handler_resolves_by_last_component(
        self, tmp_path: Path,
    ) -> None:
        p = tmp_path / "cli.py"
        p.write_text(
            "import argparse\n"                          # 1
            "from . import handlers\n"                   # 2
            "def build_parser():\n"                      # 3
            "    p = argparse.ArgumentParser()\n"        # 4
            "    p.set_defaults(func=handlers.do_run)\n"  # 5
            "    return p\n"                             # 6
        )
        handlers_file = tmp_path / "handlers.py"
        handlers_file.write_text("def do_run(args):\n    return 0\n")
        do_run = _make_symbol(handlers_file, "do_run", "function", 1, 2)
        builder = _make_symbol(p, "build_parser", "function", 3, 6)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[do_run, builder],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst == do_run.id
        assert result.edges[0].meta is not None
        assert result.edges[0].meta.get("handler_name") == "handlers.do_run"

    def test_multi_candidate_marks_disambiguation_fallback(
        self, tmp_path: Path,
    ) -> None:
        file_path = self._write_cli(tmp_path)
        builder, handler = self._symbols(file_path)
        other_file = tmp_path / "other.py"
        other_file.write_text("def cmd_sketch(args):\n    return 1\n")
        twin = _make_symbol(other_file, "cmd_sketch", "function", 1, 2)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[builder, handler, twin],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert len(result.edges) == 2
        for edge in result.edges:
            assert edge.confidence == 0.5
            assert edge.meta is not None
            assert edge.meta.get("disambiguation_fallback") is True

    def test_duplicate_registration_dedupes(self, tmp_path: Path) -> None:
        source = _CLI_SOURCE + "    p.set_defaults(func=cmd_sketch)\n"
        p = tmp_path / "cli.py"
        p.write_text(source)
        handler = _make_symbol(p, "cmd_sketch", "function", 3, 4)
        builder = _make_symbol(p, "build_parser", "function", 6, 12)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[builder, handler],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert len(result.edges) == 1

    def test_registration_outside_any_symbol_is_skipped(
        self, tmp_path: Path,
    ) -> None:
        # Module-level registration with no enclosing symbol recorded:
        # no source to hang the edge on, so the site is skipped.
        p = tmp_path / "cli.py"
        p.write_text(
            "import argparse\n"                      # 1
            "parser = argparse.ArgumentParser()\n"   # 2
            "parser.set_defaults(func=cmd_x)\n"      # 3
        )
        other = tmp_path / "impl.py"
        other.write_text("def cmd_x(args):\n    return 0\n")
        handler = _make_symbol(other, "cmd_x", "function", 1, 2)
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[handler],
            detected_languages={"python"},
        )
        result = argparse_dispatch_linker(ctx)
        assert result.edges == []
