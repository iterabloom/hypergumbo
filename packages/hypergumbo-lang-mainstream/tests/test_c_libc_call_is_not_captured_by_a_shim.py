# SPDX-License-Identifier: AGPL-3.0-or-later
"""A libc call is the libc primitive, not a same-named shim elsewhere (WI-rimon).

The C resolver bound a call by NAME to any same-named in-repo definition. A
vendored platform shim that re-implements ``sendto`` for another OS therefore
captured every ``sendto`` in the repo, and a resolved call is never classified,
so the socket I/O vanished. Measured on the shipped CLI before this change, a
first-party ``sendto(fd, getenv("API_KEY"), ...)`` beside
``lib/wamr/.../nuttx_platform.c`` defining a stub ``sendto``:

* io-boundaries: ``env_read`` only -- no ``net_send``, no ``net_recv``;
* verify-claims ``host_secret -> network``: CONFIRMED, 0 flows.

On fluent-bit, 440 resolved calls went to WAMR's sgx/nuttx shims, mruby's
Win32 shim and monkey's mk_core this way; on ten other C repos, zero.

THE RULE, from C's own semantics: a translation unit that includes the header
declaring ``sendto`` calls the ``sendto`` that header declares. A same-named
definition in ANOTHER file can only be a link-time interposer or a platform
replacement of that same contract -- a conflicting prototype would not compile
-- and name resolution cannot tell which one the build links. So the call is
the catalogued primitive. A definition in the SAME file still wins: it is
visible to the call by C's scoping, whatever the includes say.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_CALLER = (
    "#include <stdlib.h>\n#include <string.h>\n#include <sys/socket.h>\n\n"
    "int ship(int fd, struct sockaddr *to, socklen_t n) {\n"
    '    char *key = getenv("API_KEY");\n'
    "    return (int)sendto(fd, key,\n"
    "                       strlen(key), 0, to, n);\n"
    "}\n"
)

_SHIM = (
    "#include <errno.h>\n#include <sys/socket.h>\n\n"
    "ssize_t sendto(int s, const void *b, size_t l, int f,\n"
    "               const struct sockaddr *to, socklen_t n)\n"
    "{\n    errno = ENOTSUP;\n    return -1;\n}\n"
)

_CLAIMS = (
    "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
)


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    return repo


def _verdict(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict


def _net_send(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    net = json.loads(out)["boundaries"].get("net_send", {"chains": []})
    return {c["primitive"] for c in net["chains"]}


def test_a_shim_elsewhere_does_not_capture_the_libc_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path, {
        "src/main.c": _CALLER,
        "lib/wamr/core/shared/platform/nuttx/nuttx_platform.c": _SHIM,
    })
    assert "sys/socket.sendto" in _net_send(repo, tmp_path, monkeypatch)
    verdict = _verdict(repo, tmp_path, monkeypatch)
    assert verdict["verdict"] == "violated", verdict["details"]
    assert any("sys/socket.sendto" in e["sink_primitives"] for e in verdict["evidence"])


def test_without_the_shim_the_call_was_already_the_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL the fix must match: the same caller with no shim."""
    repo = _repo(tmp_path, {"src/main.c": _CALLER})
    assert "sys/socket.sendto" in _net_send(repo, tmp_path, monkeypatch)
    assert _verdict(repo, tmp_path, monkeypatch)["verdict"] == "violated"


def test_a_same_file_definition_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C scoping: a definition in the calling file is the one the call sees."""
    repo = _repo(tmp_path, {"src/main.c": _SHIM + "\n" + _CALLER})
    assert "sys/socket.sendto" not in _net_send(repo, tmp_path, monkeypatch)


def test_a_project_function_whose_header_is_not_included_stays_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No <sys/socket.h> in the caller: a project ``sendto`` is just a function."""
    caller = _CALLER.replace("#include <sys/socket.h>\n", '#include "net.h"\n')
    repo = _repo(tmp_path, {"src/main.c": caller, "src/net.c": _SHIM})
    assert "sys/socket.sendto" not in _net_send(repo, tmp_path, monkeypatch)


def test_cpp_is_held_to_the_same_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cpp.py resolves a free call through the same kind of name lookup.

    The C header spellings are used on purpose: ``<cstdlib>`` classifies nothing
    for an unrelated reason (WI-hilot), which would test that gap instead.
    """
    shim = _SHIM.replace("<errno.h>", "<cerrno>")
    repo = _repo(tmp_path, {"src/main.cpp": _CALLER, "lib/plat/shim.cpp": shim})
    assert "sys/socket.sendto" in _net_send(repo, tmp_path, monkeypatch)
    verdict = _verdict(repo, tmp_path, monkeypatch)
    assert verdict["verdict"] == "violated", verdict["details"]


def test_cpp_member_call_is_not_rebound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``conn.sendto(...)`` is a METHOD: the header's free function is not it."""
    repo = _repo(tmp_path, {
        "src/main.cpp": (
            "#include <sys/socket.h>\n\nstruct Conn { int sendto(int x) { return x; } };\n\n"
            "int f(Conn &c) {\n    return c.sendto(1);\n}\n"
        ),
    })
    assert "sys/socket.sendto" not in _net_send(repo, tmp_path, monkeypatch)
