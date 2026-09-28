# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shell script's binary payload is not parsed as commands (WI-fisoh).

A self-executing archive is a short ``sh`` prefix with a binary appended:
coursier's launcher and every ``exec java -jar "$0"`` stub carry a ZIP after
one or two lines of shell. The shebang made the file a bash file, tree-sitter
parsed the ZIP as commands, and ``io-boundaries`` reported the fragments as
launched programs. On sbt, 7 of 52 ``command_launch`` primitives were
control-character blobs or an EMPTY STRING. A synthetic payload reproduces it
with ``PK\\x03\\x04...`` and single letters (``E``, ``h``) that a printable-name
check alone would let through.

The payload is blanked from the start of the line holding the first NUL, byte
length kept (``parse_source``'s rule), so every span in the prefix is
unchanged and a script with no NUL is untouched.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.bash import _plausible_command_name, shell_text


def _payload() -> bytes:
    buf = io.BytesIO()
    # Deterministic bytes that compress badly, standing in for a class file.
    noise = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(125))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", "Main-Class: a.B\n")
        z.writestr("a/B.class", noise)
    return buf.getvalue()


_PREFIX = b'#!/usr/bin/env sh\ncurl -fsSL "$1" > /dev/null\nexec java -jar "$0" "$@"\n'


def _launched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    launcher = repo / "launcher"
    launcher.write_bytes(data)
    launcher.chmod(0o755)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["io-boundaries", str(repo), "--format", "json"])
    entry = json.loads(buf.getvalue())["boundaries"].get("command_launch")
    return [c["primitive"] for c in entry["chains"]] if entry else []


def test_the_payload_launches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launched = _launched(tmp_path, monkeypatch, _PREFIX + _payload())
    assert launched == ["curl"]  # reach: the prefix's real launch is still read


def test_a_script_without_a_payload_is_unchanged() -> None:
    assert shell_text(_PREFIX) is _PREFIX


def test_the_blanked_file_keeps_its_length_and_lines() -> None:
    data = _PREFIX + _payload()
    text = shell_text(data)
    assert len(text) == len(data)
    assert text.count(b"\n") == data.count(b"\n")
    assert text.startswith(_PREFIX)
    assert b"\x00" not in text and b"PK" not in text


def test_the_cut_takes_the_whole_line_holding_the_first_nul() -> None:
    """``PK\\x03\\x04`` precedes the ZIP's first NUL on its line: cutting at the
    NUL itself would leave it to be parsed as a command."""
    assert shell_text(b"echo a\nPK\x03\x04\x00rest\nmore") == b"echo a\n" + b" " * 9 + b"\n    "


@pytest.mark.parametrize("name,ok", [
    ("curl", True), ("./build.sh", True), ("", False),
    ("\x1e\x0ez4", False), ("a b", False),
])
def test_a_launched_name_must_be_printable(name: str, ok: bool) -> None:
    assert _plausible_command_name(name) is ok
