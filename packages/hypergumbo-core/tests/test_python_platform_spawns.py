# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three ``platform`` functions launch a program (INV-bosus).

python.yaml rowed all of ``platform``'s host-description functions under
``host_info_read``. Three of them also start a child process, measured on
CPython 3.12 on Linux by wrapping ``subprocess.check_output``:

* ``platform.architecture()`` runs ``file -b <sys.executable>``
  (``_syscmd_file``);
* ``platform.processor()`` runs ``uname -p``: ``_Processor.get`` falls back to
  ``from_subprocess`` for every platform without a ``get_<sys.platform>``,
  and only win32 and OpenVMS have one;
* ``platform.platform()`` runs ``uname -p`` through ``uname().processor``.

The other rowed functions (``system``, ``node``, ``release``, ``version``,
``machine``, ``python_version``, ``libc_ver``, ``mac_ver``,
``freedesktop_os_release``) spawn nothing. So the three are rowed
``simultaneous`` under both boundaries: the call reads the host description
AND launches a program, and neither fact replaces the other.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import is_high_risk, load_catalog

_SPAWNS = ("architecture", "processor", "platform")
_DOES_NOT = ("system", "node", "release", "version", "machine", "python_version",
             "libc_ver", "mac_ver", "freedesktop_os_release")


def _boundaries(name: str) -> set[str]:
    return {p.boundary for p in load_catalog("python").primitives
            if p.module == "platform" and p.name == name}


@pytest.mark.parametrize("name", _SPAWNS)
def test_a_spawning_platform_call_is_also_a_launch(name: str) -> None:
    assert _boundaries(name) == {"host_info_read", "subprocess"}
    both = load_catalog("python").simultaneous_boundaries_for(f"platform.{name}")
    assert both == {"host_info_read", "subprocess"}


@pytest.mark.parametrize("name", _DOES_NOT)
def test_the_others_stay_host_reads(name: str) -> None:
    assert _boundaries(name) == {"host_info_read"}


@pytest.mark.parametrize("name", _SPAWNS)
def test_the_launch_is_marked_high_risk(name: str) -> None:
    """HIGH_RISK_PRIMITIVES' own rule: a launch is a launch (builtins.help)."""
    assert is_high_risk(f"platform.{name}")


_PROGRAM = '''import platform


def arch():
    return platform.architecture()


def cpu():
    return platform.processor()


def ident():
    return platform.platform()


def os_name():
    return platform.system()
'''


def test_the_shipped_command_reports_both_crossings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(_PROGRAM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    assert rc == 0
    chains = {
        (b, c["primitive"], c["io_edge_src"].split(":")[-2])
        for b, v in json.loads(buf.getvalue())["boundaries"].items() for c in v["chains"]
    }
    for fn, name in (("arch", "architecture"), ("cpu", "processor"), ("ident", "platform")):
        assert ("subprocess", f"platform.{name}", fn) in chains
        assert ("host_info_read", f"platform.{name}", fn) in chains
    assert ("host_info_read", "platform.system", "os_name") in chains
    assert not any(b == "subprocess" and src == "os_name" for b, _, src in chains)
