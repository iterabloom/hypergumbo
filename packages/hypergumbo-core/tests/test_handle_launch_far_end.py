# SPDX-License-Identifier: AGPL-3.0-or-later
"""A launch that returns a HANDLE has its crossing at the READ (WI-kanor).

ADR-0049 ruling 1 asks of a call: *does it return a value whose content the far
side chose?* ``subprocess.Popen(...)``, ``os.popen(cmd)``, ``popen(3)``,
``ProcessBuilder.start()`` and ``exec.Cmd.StdoutPipe()`` all answer NO -- they
hand back a handle the CHILD fills later. So the launch rows are rightly
launch-only, and the child's bytes cross at a later call: the read through the
handle. Before this change that later call was represented in go (a ``bufio``
reader over ``cmd.StdoutPipe()`` is stamped ``pipe``, WI-suhug) and in no other
language, so a program that fed a child's output into a shell returned
``confirmed_with_caveats`` -- the opacity caveat standing in for a receive the
tool was looking at.

WHAT IS NOW REPRESENTED, one mechanism per language, each one the language's
EXISTING mechanism for "a read's boundary is its handle's origin":

* python -- ``subprocess.Popen.communicate`` is rowed ``ipc_recv`` beside its
  launch row (``simultaneous``, the ``check_output`` shape: it returns the
  child's output), and ``os.popen`` is typed ``os._wrap_close`` by a
  library-signature row (WI-kozaj's producer typing) whose read methods are
  rowed ``ipc_recv``.
* c -- ``popen`` is a ``pipe`` producer for c.py's stream-argument stamp
  (WI-lipis), so ``fgets(buf, n, f)`` over it selects stdio's ``ipc_recv`` twin.
* java -- ``getInputStream()`` / ``getErrorStream()`` on a provable ``Process``
  is a ``pipe`` for java.py's receiver-origin stamp (WI-tusav), so the
  ``BufferedReader`` / ``InputStream`` read over it selects the ``ipc_recv`` twin.

WHAT IS NOT: python's ``p.stdout.read()`` (an attribute of a typed receiver is
not typed, so the read is ``external``), go's ``io.ReadAll(cmd.StdoutPipe())``
(``io.ReadAll`` is a fixed ``fs_read`` row with no stream stamp). Each is
pinned below as a control, so a later fix turns it red rather than passing
unnoticed. (A source read inside a helper and returned to its caller is
followed since INV-komoj; ``test_taint_return_flow.py`` pins it.)
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.library_signatures import load_library_signatures
from hypergumbo_core.taint import (
    AUTO_SOURCE_LABEL_MAP,
    _derive_auto_imports_from_io_primitives,
)

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"

_CLAIMS = """claims:
  - id: untrusted-no-subprocess
    text: Untrusted input never reaches a launched program.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: subprocess
"""


def _verify(tmp_path: Path, files: dict[str, str],
            monkeypatch: pytest.MonkeyPatch) -> dict:
    """The one verdict of the shipped ``verify-claims`` over ``files``."""
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims),
              "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def _source_primitives(verdict: dict) -> set[str]:
    return {p for e in verdict.get("evidence", [])
            for p in e.get("source_primitives") or []}


def _python_rows(module: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in load_catalog("python").primitives:
        if p.module == module:
            out.setdefault(p.name, set()).add(p.boundary)
    return out


class TestThePythonRows:
    def test_communicate_is_a_receive_and_still_a_launch(self) -> None:
        assert _python_rows("subprocess.Popen")["communicate"] == {
            "ipc_recv", "subprocess"}

    def test_the_other_popen_methods_stay_launch_only(self) -> None:
        rows = _python_rows("subprocess.Popen")
        for name in ("wait", "poll", "terminate", "kill", "send_signal"):
            assert rows[name] == {"subprocess"}, name

    def test_communicate_mints_untrusted_input_from_ipc_recv(self) -> None:
        sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(
            _CATALOG_DIR)
        found = [s for s in sources["python"]
                 if (s.module, s.name) == ("subprocess.Popen", "communicate")]
        assert [(s.taint_label, s.source_boundary) for s in found] == [
            (AUTO_SOURCE_LABEL_MAP["ipc_recv"], "ipc_recv")]

    def test_os_popen_returns_the_wrapper_it_rows(self) -> None:
        assert load_library_signatures("python")["os.popen"] == "os._wrap_close"
        rows = _python_rows("os._wrap_close")
        for name in ("read", "readline", "readlines", "close"):
            assert rows[name] == {"ipc_recv"}, name
        for name in ("write", "writelines"):
            assert rows[name] == {"ipc_send"}, name

    def test_the_wrapper_type_is_cpythons(self) -> None:
        """The library row names a REAL type: the class Lib/os.py's popen
        returns, read from its code object rather than by launching a child
        (and not from source text, which an installed CPython may not ship)."""
        import inspect
        import os

        assert inspect.isclass(os._wrap_close)
        assert "_wrap_close" in os.popen.__code__.co_names


_PY_COMMUNICATE = (
    "import subprocess\n\n\n"
    "def main():\n"
    "    p = subprocess.Popen(['git', 'log'], stdout=subprocess.PIPE)\n"
    "    out, _err = p.communicate()\n"
    "    subprocess.run(out.decode(), shell=True)\n"
)
_PY_POPEN = (
    "import os\nimport subprocess\n\n\n"
    "def main():\n"
    "    f = os.popen('git log')\n"
    "    data = f.read()\n"
    "    subprocess.run(data, shell=True)\n"
)
_PY_INPUT_ONLY = (
    "import subprocess\n\n\n"
    "def main():\n"
    "    p = subprocess.Popen(['cat'], stdin=subprocess.PIPE)\n"
    "    p.communicate(input=b'hello')\n"
    "    subprocess.run('ls', shell=True)\n"
)
_PY_STDOUT_ATTRIBUTE = (
    "import subprocess\n\n\n"
    "def main():\n"
    "    p = subprocess.Popen(['git', 'log'], stdout=subprocess.PIPE)\n"
    "    data = p.stdout.read()\n"
    "    subprocess.run(data.decode(), shell=True)\n"
)
_C_POPEN = (
    "#include <stdio.h>\n#include <stdlib.h>\n\n"
    "int main(void) {\n"
    "    char buf[256];\n"
    "    FILE *f = popen(\"git log\", \"r\");\n"
    "    fgets(buf, sizeof buf, f);\n"
    "    system(buf);\n"
    "    pclose(f);\n"
    "    return 0;\n"
    "}\n"
)
_C_FOPEN = _C_POPEN.replace('popen("git log", "r")', 'fopen("/etc/motd", "r")')
_JAVA_PROCESS = (
    "import java.io.BufferedReader;\n"
    "import java.io.InputStreamReader;\n\n"
    "public class App {\n"
    "    public static void main(String[] args) throws Exception {\n"
    "        Process p = new ProcessBuilder(\"git\", \"log\").start();\n"
    "        BufferedReader r = new BufferedReader("
    "new InputStreamReader(p.getInputStream()));\n"
    "        String line = r.readLine();\n"
    "        Runtime.getRuntime().exec(line);\n"
    "    }\n"
    "}\n"
)
_GO_GOMOD = "module example.com/k\n\ngo 1.21\n"
_GO_SCANNER = (
    "package main\n\n"
    "import (\n\t\"bufio\"\n\t\"os/exec\"\n)\n\n"
    "func main() {\n"
    "\tcmd := exec.Command(\"git\", \"log\")\n"
    "\tout, _ := cmd.StdoutPipe()\n"
    "\tcmd.Start()\n"
    "\tsc := bufio.NewScanner(out)\n"
    "\tfor sc.Scan() {\n"
    "\t\texec.Command(\"sh\", \"-c\", sc.Text()).Run()\n"
    "\t}\n"
    "}\n"
)
_GO_READALL = (
    "package main\n\n"
    "import (\n\t\"io\"\n\t\"os/exec\"\n)\n\n"
    "func main() {\n"
    "\tcmd := exec.Command(\"git\", \"log\")\n"
    "\tout, _ := cmd.StdoutPipe()\n"
    "\tcmd.Start()\n"
    "\tb, _ := io.ReadAll(out)\n"
    "\texec.Command(\"sh\", \"-c\", string(b)).Run()\n"
    "}\n"
)


class TestTheChildsOutputIsAReceive:
    """End to end, through the shipped CLI: the child's output reaches a shell.

    Each of these returned ``confirmed_with_caveats`` (python, c) or
    ``inconclusive`` (java) on the tree before WI-kanor."""

    def test_python_communicate(self, tmp_path, monkeypatch) -> None:
        verdict = _verify(tmp_path, {"app.py": _PY_COMMUNICATE}, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert "subprocess.Popen.communicate" in _source_primitives(verdict)

    def test_python_os_popen(self, tmp_path, monkeypatch) -> None:
        verdict = _verify(tmp_path, {"app.py": _PY_POPEN}, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert "os._wrap_close.read" in _source_primitives(verdict)

    def test_c_popen(self, tmp_path, monkeypatch) -> None:
        verdict = _verify(tmp_path, {"main.c": _C_POPEN}, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert "stdio.fgets" in _source_primitives(verdict)

    def test_java_process_input_stream(self, tmp_path, monkeypatch) -> None:
        verdict = _verify(tmp_path, {"src/App.java": _JAVA_PROCESS}, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert "java.io.BufferedReader.readLine" in _source_primitives(verdict)

    def test_go_stdout_pipe_was_already_represented(
        self, tmp_path, monkeypatch,
    ) -> None:
        """The parity anchor: go reached this before WI-kanor (WI-suhug's
        ``pipe`` kind), which is what the other three now match."""
        verdict = _verify(tmp_path, {"go.mod": _GO_GOMOD, "main.go": _GO_SCANNER},
                          monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        assert _source_primitives(verdict) & {
            "bufio.NewScanner", "bufio.Scanner.Text"}


class TestControls:
    def test_a_communicate_that_only_sends_mints_no_flow(
        self, tmp_path, monkeypatch,
    ) -> None:
        """The source is the RETURN value; discarding it carries nothing."""
        verdict = _verify(tmp_path, {"app.py": _PY_INPUT_ONLY}, monkeypatch)
        assert verdict["verdict"] != "violated", verdict["evidence"]

    def test_a_file_read_is_still_not_a_receive(self, tmp_path, monkeypatch) -> None:
        verdict = _verify(tmp_path, {"main.c": _C_FOPEN}, monkeypatch)
        assert verdict["verdict"] != "violated", verdict["evidence"]

    def test_residual_python_stdout_attribute_is_not_reached(
        self, tmp_path, monkeypatch,
    ) -> None:
        """RESIDUAL, pinned: ``p.stdout`` is an attribute of a typed receiver
        and is not typed, so ``.read()`` arrives ``external``. A fix that
        types it turns this red, which is the point."""
        verdict = _verify(tmp_path, {"app.py": _PY_STDOUT_ATTRIBUTE}, monkeypatch)
        assert verdict["verdict"] != "violated"

    def test_residual_go_io_readall_over_a_pipe_is_not_reached(
        self, tmp_path, monkeypatch,
    ) -> None:
        """RESIDUAL, pinned: ``io.ReadAll`` is a fixed ``fs_read`` row and
        carries no stream stamp, so the pipe it drains mints nothing."""
        verdict = _verify(tmp_path, {"go.mod": _GO_GOMOD, "main.go": _GO_READALL},
                          monkeypatch)
        assert verdict["verdict"] != "violated"
