# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: scripts/check-symbol-kind-drift
"""Tests for ``scripts/check-symbol-kind-drift``.

The script runs two axes' drift checks in the one pre-commit step:
Symbol.kind's ``*KIND*`` set walker and, since ADR-0063, the entrypoint-kind
axis's three: the hand-written-set check (``find_kind_set_drift``), the
allow-list's shrink-only check (``unused_not_a_role_question_keys``) and the
keep-trigger watcher (``find_watched_kind_reads`` against
``KNOWN_WATCHED_READS``). The checks themselves are tested in
``packages/hypergumbo-core/tests/test_symbol_kinds.py`` and
``test_entrypoint_kinds.py``; this file covers the shell: which exit code each
combination gives and what it prints.

The last two were run by tests alone until the scheduled test-only-reachability
gate reported them (2026-11-22): a check no gate runs is a check a commit can
break and not hear about until the suite does.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-symbol-kind-drift"


def _load(path: Path, name: str) -> Any:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cli = _load(SCRIPT_PATH, "check_symbol_kind_drift")


_KEY = ("packages/x/src/x/reader.py", "pick", "MAIN_GUARD")


def _run(
    *, kind_drift: tuple[str, ...] = (), entry_drift: tuple[str, ...] = (),
    stale_keys: tuple[tuple[str, str, str], ...] = (),
    reads: tuple[tuple[str, str, str], ...] = (),
    known: tuple[tuple[str, str, str], ...] = (),
) -> int:
    with patch.object(cli, "find_axis_drift", return_value=list(kind_drift)), \
            patch.object(cli, "find_kind_set_drift", return_value=list(entry_drift)), \
            patch.object(cli, "unused_not_a_role_question_keys", return_value=list(stale_keys)), \
            patch.object(cli, "find_watched_kind_reads", return_value=list(reads)), \
            patch.object(cli, "KNOWN_WATCHED_READS", dict.fromkeys(known, "why")):
        return int(cli.main())


@pytest.mark.parametrize(("kind_drift", "entry_drift", "rc"), [
    ((), (), 0),
    (("a.py:1 (X_KINDS): phantom",), (), 1),
    ((), ("b.py:2: hand-written kind set",), 1),
    (("a.py:1 (X_KINDS): phantom",), ("b.py:2: hand-written kind set",), 1),
])
def test_exit_code_reflects_either_check(
    capsys: pytest.CaptureFixture[str], kind_drift: tuple[str, ...],
    entry_drift: tuple[str, ...], rc: int,
) -> None:
    assert _run(kind_drift=kind_drift, entry_drift=entry_drift) == rc
    out = capsys.readouterr()
    assert ("entrypoint-kind axis check failed" in out.out) == bool(entry_drift)
    assert ("kinds_with_role" in out.err) == bool(entry_drift)
    assert ("Symbol.kind axis-coherence check failed" in out.out) == bool(kind_drift)


def test_a_listed_watched_read_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(reads=(_KEY,), known=(_KEY,)) == 0
    assert capsys.readouterr().out == ""


def test_an_allow_list_key_that_matches_no_site_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """NOT_A_ROLE_QUESTION only shrinks: a key whose site is gone would let a
    later hand-written set at that place through."""
    assert _run(stale_keys=(_KEY,)) == 1
    out = capsys.readouterr()
    assert "entrypoint-kind axis check failed" in out.out
    assert "packages/x/src/x/reader.py" in out.out and "pick" in out.out
    assert "NOT_A_ROLE_QUESTION" in out.err and "remove" in out.err


def test_an_unlisted_branch_on_a_kept_kind_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The keep trigger of audit 0027 firing: a reader branches on a kind kept
    beside its sibling, and nobody has said why that is the kind's own."""
    assert _run(reads=(_KEY,)) == 1
    out = capsys.readouterr()
    assert "entrypoint-kind axis check failed" in out.out
    assert "MAIN_GUARD" in out.out and "pick" in out.out
    assert "KNOWN_WATCHED_READS" in out.err


def test_a_listed_watched_read_that_is_gone_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(known=(_KEY,)) == 1
    out = capsys.readouterr()
    assert "MAIN_GUARD" in out.out and "no longer" in out.out
    assert "KNOWN_WATCHED_READS" in out.err


def test_the_live_tree_passes(capsys: pytest.CaptureFixture[str]) -> None:
    """Unpatched: the three entrypoint-kind checks and the Symbol.kind check
    on this repository."""
    assert cli.main() == 0, capsys.readouterr()
