# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m hypergumbo_core.cli`` must fail loudly, not exit 0 doing nothing.

WI-burol: ``cli.py`` had no ``__main__`` block, so the ``.cli`` module form
imported the module, ran no command and exited 0 with empty output. The
documented module entry point is ``python -m hypergumbo_core``
(``hypergumbo_core/__main__.py``). The fix keeps that ONE entry point and makes
the ``.cli`` spelling refuse with exit 2 and a message naming the right form.

The unit test drives the refusal helper in-process (coverage); the subprocess
test is the behavioral repro of the filed instance, end to end.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hypergumbo_core import cli

_SRC = Path(cli.__file__).resolve().parents[1]


def test_refuse_module_form_exits_2_naming_the_entry_point(capsys) -> None:
    rc = cli._refuse_module_form()
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "python -m hypergumbo_core.cli" in err
    assert "python -m hypergumbo_core <command>" in err


def test_python_dash_m_cli_refuses_with_exit_2(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.run(
        [sys.executable, "-m", "hypergumbo_core.cli", "verify-claims", ".",
         "--format", "json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "python -m hypergumbo_core <command>" in proc.stderr
