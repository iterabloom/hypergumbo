# SPDX-License-Identifier: MPL-2.0
"""The auto-sync credential helper names two variables and holds no value (WI-jizos).

``sync._CREDENTIAL_HELPER`` is the text git receives after ``-c
credential.helper=``. A process's argument list is public on the machine, so
that text must NAME the two environment variables the token travels in and
never contain the token. The rule, and the tests of the shell scripts that
follow it, are in ``tests/test_token_not_on_command_line.py``.

These two tests live HERE, in the tracker's own package, because they import
``hypergumbo_tracker``. They were first written in the top-level ``tests/``,
which the scheduled suite runs with no hypergumbo package installed: both
failed there with ``ModuleNotFoundError`` while the per-PR run, which does
install the tracker, passed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hypergumbo_tracker import sync

REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET_ARGS = REPO_ROOT / "scripts" / "lib" / "secret-args.sh"
TOKEN = "s3cr3t-T0KEN-must-not-be-an-argument-0123456789"


def _answer(env: dict[str, str]) -> list[str]:
    """What the helper prints when git runs it: through ``sh -c``, with ``get``."""
    helper = sync._CREDENTIAL_HELPER
    assert helper.startswith("!")
    return subprocess.run(
        ["/bin/sh", "-c", helper[1:] + " get"], capture_output=True, text=True, env=env,
    ).stdout.splitlines()


def test_the_helper_names_two_variables_and_holds_no_value() -> None:
    assert TOKEN not in sync._CREDENTIAL_HELPER
    with_env = {**os.environ, sync._CRED_USER_VAR: "x-access-token", sync._CRED_TOKEN_VAR: TOKEN}
    assert _answer(with_env) == ["username=x-access-token", f"password={TOKEN}"]
    without = {k: v for k, v in os.environ.items() if k not in (sync._CRED_USER_VAR, sync._CRED_TOKEN_VAR)}
    assert _answer(without) == ["username=", "password="]


def test_the_shell_and_python_helpers_are_the_same_text() -> None:
    """One contract, two spellings: the scripts and the tracker name the same variables."""
    assert f"credential.helper='{sync._CREDENTIAL_HELPER}'" in SECRET_ARGS.read_text()
