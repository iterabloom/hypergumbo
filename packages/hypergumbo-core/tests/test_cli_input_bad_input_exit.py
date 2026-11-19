# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every command that reads ``--input`` refuses an unusable path with exit 2.

THE RULING (WI-nidun, 2026-11-19): exit 1 is a command's own answer
(``violated`` for verify-claims), 70 is an internal error and 141 a broken
pipe, set once in ``cli.main``. A ``--input`` path that does not exist, or
that is a directory, is neither: it is bad input, exit 2, with a message that
names the path. Before the ruling a missing path exited 1 from eleven
commands (verify-claims alone said 2), and a directory reached the
internal-error net ("This is a bug") from every command but verify-claims.

ONE LOADER, ONE REFUSAL. Every ``--input`` command reads its map through
``load_substrate``, which turns a missing path, a directory, a refusal, bytes
that are not UTF-8 and a broken ``.gz`` into ``SubstrateError``, which
``main`` maps to 2. The matrix below drives each command through
``main([...])``, the value a user's shell sees.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.survey_io import SubstrateError, load_substrate


def _argv(command: str, repo: Path, claims: Path, input_path: Path) -> list[str]:
    """``command``'s argv with ``--input`` set, and whatever else it needs."""
    t, x = str(repo), str(input_path)
    return {
        "sketch": ["sketch", t, "--input", x],
        "slice": ["slice", t, "--list-entries", "--input", x],
        "search": ["search", "foo", t, "--input", x],
        "routes": ["routes", t, "--input", x],
        "explain": ["explain", "foo", "--path", t, "--input", x],
        "test-coverage": ["test-coverage", t, "--input", x],
        "dead-code-maybe": ["dead-code-maybe", t, "--input", x],
        "repeat-finder": ["repeat-finder", t, "--input", x],
        "symbols": ["symbols", t, "--input", x],
        "io-boundaries": ["io-boundaries", t, "--input", x],
        "verify-claims": ["verify-claims", t, "--claims", str(claims), "--input", x],
        "compact": ["compact", "--input", x],
    }[command]


_COMMANDS = [
    "sketch", "slice", "search", "routes", "explain", "test-coverage",
    "dead-code-maybe", "repeat-finder", "symbols", "io-boundaries",
    "verify-claims", "compact",
]


@pytest.fixture
def claims(tmp_path: Path) -> Path:
    path = tmp_path / "claims.yaml"
    path.write_text("claims:\n  - id: A\n    text: a\n    constraint:\n"
                    "      boundary: net_send\n      must_not_exist: true\n")
    return path


def _assert_refused(rc: int, err: str, *needles: str) -> None:
    assert "internal error" not in err, err
    assert "This is a bug" not in err, err
    assert rc == 2, err
    for needle in needles:
        assert needle in err, err


@pytest.mark.parametrize("command", _COMMANDS)
def test_a_missing_input_exits_2(
    tmp_path: Path, capsys, claims: Path, command: str,
) -> None:
    missing = tmp_path / "nope.json"
    rc = main(_argv(command, tmp_path, claims, missing))
    _assert_refused(rc, capsys.readouterr().err, "Input file not found", str(missing))


@pytest.mark.parametrize("command", _COMMANDS)
def test_an_input_that_is_a_directory_exits_2(
    tmp_path: Path, capsys, claims: Path, command: str,
) -> None:
    directory = tmp_path / "survey.json"
    directory.mkdir()
    rc = main(_argv(command, tmp_path, claims, directory))
    _assert_refused(rc, capsys.readouterr().err, "could not read behavior map",
                    str(directory))


def test_load_substrate_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(SubstrateError, match="could not read behavior map"):
        load_substrate(tmp_path)


def test_load_substrate_refuses_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(SubstrateError, match="Input file not found"):
        load_substrate(tmp_path / "nope.json")


def test_load_substrate_refuses_bytes_that_are_not_text(tmp_path: Path) -> None:
    """Not UTF-8: a ``UnicodeDecodeError``, which is no ``JSONDecodeError``,
    reached the internal-error net."""
    path = tmp_path / "survey.json"
    path.write_bytes(b"\xff\xfe\x00{")
    with pytest.raises(SubstrateError, match="not UTF-8 text"):
        load_substrate(path)


def test_load_substrate_refuses_a_truncated_gzip(tmp_path: Path) -> None:
    """A ``.gz`` cut short raises ``EOFError``, which is no ``OSError``."""
    import gzip

    whole = gzip.compress(b'{"nodes": [], "edges": []}')
    path = tmp_path / "survey.json.gz"
    path.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(SubstrateError, match="could not read behavior map"):
        load_substrate(path)
