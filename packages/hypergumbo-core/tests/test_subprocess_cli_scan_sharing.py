# SPDX-License-Identifier: AGPL-3.0-or-later
"""The subprocess-CLI linker walks every python file ONCE per run, not four times.

WHAT WAS HAPPENING. ``_scan_argparse_commands`` and ``_scan_fire_commands`` each
did their own ``for file_path in _find_python_files(root): ... ast.parse(...)``
over the SAME file set — and each is called from two places: the linker proper
(which needs the resolved commands) and ``_count_cli_command_symbols`` (a
requirement-check diagnostic that only wants a COUNT). Four full parse-and-walk
passes over every python file per survey, two of them purely to produce a number.

Measured on this monorepo before the change (scripts/measure-parse-redundancy.py,
per-call-site attribution):

    subprocess_cli.py:532   2244 calls   7.6s   3.8s of it re-parsing
    subprocess_cli.py:629   2244 calls   8.4s   8.4s of it re-parsing

2244 = 2 x 1122 python files, i.e. each scan running twice. The file-walking half
of both scans is a pure function of the files on disk — it is only the RESOLUTION
half that depends on the symbol table — so one walk can serve all four callers.

WHY THESE TESTS AND NOT JUST THE EQUIVALENCE ONES. A shared/memoized scan has
exactly one dangerous failure mode, and it is the one WI-madut names for its much
larger cousin: a STALE HIT. If the cache key is incomplete, a caller silently gets
facts from a tree that no longer exists, and nothing in the output says so.
``test_rescans_after_a_file_changes`` is therefore the load-bearing test here —
the equivalence tests would all pass against a cache that never invalidates.

``test_walks_each_file_once_across_both_scans`` is the other half: without it, a
"fix" that kept the four walks and merely returned the same answers would satisfy
every correctness test in this file while saving nothing.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers import subprocess_cli


def _handler_symbol() -> Symbol:
    return Symbol(
        id="python:myapp/cli.py:2-3:cmd_serve:function",
        name="cmd_serve",
        kind="function",
        language="python",
        path="myapp/cli.py",
        span=Span(2, 3, 0, 0),
    )


def _class_symbol() -> Symbol:
    return Symbol(
        id="python:myapp/svc.py:1-4:Service:class",
        name="Service",
        kind="class",
        language="python",
        path="myapp/svc.py",
        span=Span(1, 4, 0, 0),
    )


def _method_symbol() -> Symbol:
    # The fire scan splits ``sym.name`` on "." to recover (class, method), so a
    # method symbol must carry its owner: a bare "deploy" is skipped outright.
    return Symbol(
        id="python:myapp/svc.py:2-3:Service.deploy:method",
        name="Service.deploy",
        kind="method",
        language="python",
        path="myapp/svc.py",
        span=Span(2, 3, 0, 0),
    )


def _project(tmp_path: Path, *, subcommand: str = "serve") -> None:
    """A repo exercising BOTH scans: an argparse pair and a fire target."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
    src = tmp_path / "myapp"
    src.mkdir(exist_ok=True)
    (src / "cli.py").write_text(
        "import argparse\n"
        "def cmd_serve(args):\n"
        "    pass\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    sub = parser.add_subparsers()\n"
        f"    p = sub.add_parser('{subcommand}')\n"
        "    p.set_defaults(func=cmd_serve)\n"
    )
    (src / "svc.py").write_text(
        "import fire\n"
        "class Service:\n"
        "    def deploy(self):\n"
        "        pass\n"
        "fire.Fire(Service)\n"
    )


def _reset_cache() -> None:
    """Drop any memoized scan so each test starts from a cold walk."""
    resetter = getattr(subprocess_cli, "_reset_cli_scan_cache", None)
    if resetter is not None:
        resetter()


@pytest.fixture(autouse=True)
def _cold_cache() -> None:
    _reset_cache()


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Install a counting ast.parse; returns a one-element mutable counter."""
    counter = [0]
    real = ast.parse

    def counting(source, *a, **kw):  # type: ignore[no-untyped-def]
        counter[0] += 1
        return real(source, *a, **kw)

    monkeypatch.setattr(subprocess_cli.ast, "parse", counting)
    return counter


# --- equivalence: the answers must not change -------------------------------


def test_argparse_scan_still_resolves_its_handler(tmp_path: Path) -> None:
    _project(tmp_path)
    commands = subprocess_cli._scan_argparse_commands(
        tmp_path, [_handler_symbol()]
    )
    assert "serve" in commands
    assert [s.name for s in commands["serve"]] == ["cmd_serve"]


def test_fire_scan_still_resolves_its_method(tmp_path: Path) -> None:
    _project(tmp_path)
    commands = subprocess_cli._scan_fire_commands(
        tmp_path, [_class_symbol(), _method_symbol()]
    )
    assert "deploy" in commands
    assert [s.name for s in commands["deploy"]] == ["Service.deploy"]


def test_the_two_scans_do_not_contaminate_each_other(tmp_path: Path) -> None:
    """Sharing one walk must not leak one scan's facts into the other's result.

    Worth pinning explicitly: the shared walk collects both fact sets in a single
    pass, so a mis-wired consumer could return fire targets as argparse commands
    (or vice versa) and every single-scan test above would still pass.
    """
    _project(tmp_path)
    symbols = [_handler_symbol(), _class_symbol(), _method_symbol()]
    argparse_commands = subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    fire_commands = subprocess_cli._scan_fire_commands(tmp_path, symbols)
    assert set(argparse_commands) == {"serve"}
    assert set(fire_commands) == {"deploy"}


# --- the point of the change ------------------------------------------------


def test_walks_each_file_once_across_both_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both scans, called twice each, must parse each file exactly once.

    Four calls x 2 python files used to mean 8 parses. Without this assertion a
    change that preserved every answer while keeping all four walks would look
    like a success and save nothing.
    """
    _project(tmp_path)
    symbols = [_handler_symbol(), _class_symbol(), _method_symbol()]
    counter = _count_parses(monkeypatch)

    subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    subprocess_cli._scan_fire_commands(tmp_path, symbols)
    subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    subprocess_cli._scan_fire_commands(tmp_path, symbols)

    python_files = list(subprocess_cli._find_python_files(tmp_path))
    assert counter[0] == len(python_files), (
        f"{counter[0]} parses for {len(python_files)} files across 4 scan "
        "calls; the walk is not being shared"
    )


def test_rescans_after_a_file_changes(tmp_path: Path) -> None:
    """THE STALE-HIT GUARD. A changed tree must produce changed facts.

    This is the failure mode that makes memoization dangerous: an incomplete key
    returns facts for a tree that no longer exists, silently, with nothing in the
    output indicating it. Every other test in this file would pass against a
    cache that never invalidates — this one would not.
    """
    _project(tmp_path, subcommand="serve")
    symbols = [_handler_symbol()]
    first = subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    assert set(first) == {"serve"}

    # Rewrite the same file with a different subcommand name.
    _project(tmp_path, subcommand="deploy")
    second = subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    assert set(second) == {"deploy"}, (
        "the scan returned facts from the pre-edit tree — the cache key does "
        "not capture file content"
    )


def test_a_new_file_is_picked_up(tmp_path: Path) -> None:
    """Adding a file must invalidate too, not just editing one.

    A key built from content digests of the FILES IT KNEW ABOUT would miss a
    brand-new file entirely, which is a different hole from a stale edit.
    """
    _project(tmp_path)
    symbols = [_handler_symbol()]
    subprocess_cli._scan_argparse_commands(tmp_path, symbols)

    extra = tmp_path / "myapp" / "extra.py"
    extra.write_text(
        "import argparse\n"
        "def main():\n"
        "    sub = argparse.ArgumentParser().add_subparsers()\n"
        "    q = sub.add_parser('extra')\n"
        "    q.set_defaults(func=cmd_serve)\n"
    )
    after = subprocess_cli._scan_argparse_commands(tmp_path, symbols)
    assert "extra" in after, "a newly added file was not scanned"


def test_fire_target_assigned_to_a_variable_is_still_found(tmp_path: Path) -> None:
    """``cli = fire.Fire(Service)`` must still register its target.

    REGRESSION GUARD for the merge itself. The fire probe originally ran over
    every ``ast.Call`` node; the argparse probe runs over an if/elif where a Call
    that is an Assign's VALUE takes the first branch. Folding the fire probe into
    that elif — the obvious way to merge two loops — silently stops matching this
    shape, and no other test in the suite uses it. Found while writing the merge,
    not after.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
    src = tmp_path / "myapp"
    src.mkdir(exist_ok=True)
    (src / "svc.py").write_text(
        "import fire\n"
        "class Service:\n"
        "    def deploy(self):\n"
        "        pass\n"
        "cli = fire.Fire(Service)\n"   # assigned, not a bare expression
    )
    commands = subprocess_cli._scan_fire_commands(
        tmp_path, [_class_symbol(), _method_symbol()]
    )
    assert "deploy" in commands, (
        "an assigned fire.Fire(...) target was dropped — the fire probe is "
        "hanging off the argparse if/elif instead of seeing every Call"
    )
