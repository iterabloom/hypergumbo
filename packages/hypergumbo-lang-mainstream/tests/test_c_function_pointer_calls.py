# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call through a C function-pointer variable reaches the function it holds (INV-nohob).

``static ssize_t (*send_data)(int, const void *, size_t) = send_data_impl;``
then ``send_data(fd, buf, n)`` -- torsocks' ``src/common/socks5.c`` sends
every SOCKS5 message this way. The call was emitted as the unresolved name
``send_data``, so the call graph stopped at the caller and taint never reached
the ``send()`` inside ``send_data_impl``: a secret sent through it read
``confirmed``. A variable the file initialises or assigns with a function
(``= f`` or ``= &f``) now calls that function; several assignments call each
of them. A pointer the file never assigns (a parameter) is unchanged.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.c import analyze_c


def _calls(tmp_path: Path, text: str) -> dict[str, list[tuple[str, str]]]:
    (tmp_path / "m.c").write_text(text)
    result = analyze_c(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[tuple[str, str]]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            out.setdefault(names[e.src], []).append((names.get(e.dst, e.dst), e.evidence_type))
    return out


_SEND_IMPL = (
    "#include <sys/socket.h>\n"
    "static int send_impl(int fd, const char *b, int n) {\n"
    "  return (int)send(fd, b, n, 0);\n"
    "}\n"
)


def test_a_file_scope_initialised_pointer_calls_its_function(tmp_path: Path) -> None:
    calls = _calls(tmp_path, _SEND_IMPL + (
        "static int (*send_data)(int, const char *, int) = send_impl;\n"
        "void go(int fd, const char *p) {\n"
        "  send_data(fd, p, 8);\n"
        "}\n"
    ))
    assert calls["go"] == [("send_impl", "function_pointer")]


def test_an_assigned_pointer_calls_every_function_it_is_given(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "static void on_a(int x) { }\n"
        "static void on_b(int x) { }\n"
        "static void (*handler)(int);\n"
        "void setup(int which) {\n"
        "  if (which)\n"
        "    handler = on_a;\n"
        "  else\n"
        "    handler = &on_b;\n"
        "}\n"
        "void fire(int x) {\n"
        "  handler(x);\n"
        "}\n"
    ))
    assert sorted(calls["fire"]) == [("on_a", "function_pointer"), ("on_b", "function_pointer")]


def test_a_local_pointer_calls_its_function(tmp_path: Path) -> None:
    calls = _calls(tmp_path, _SEND_IMPL + (
        "void go(int fd, const char *p) {\n"
        "  int (*s)(int, const char *, int) = &send_impl;\n"
        "  s(fd, p, 8);\n"
        "}\n"
    ))
    assert calls["go"] == [("send_impl", "function_pointer")]


def test_a_pointer_the_file_never_assigns_stays_unresolved(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "void run(int (*cb)(int)) {\n"
        "  cb(1);\n"
        "}\n"
        "void deref(int (**slot)(int)) {\n"
        "  int (*h)(int);\n"
        "  h = *slot;\n"
        "  h(2);\n"
        "}\n"
    ))
    assert calls["run"] == [("c:external:0-0:cb:unresolved", "ast_call_direct")]
    # ``h = *slot`` names no function: a dereference is not ``&f``.
    assert calls["deref"] == [("c:external:0-0:h:unresolved", "ast_call_direct")]


def test_a_held_libc_name_is_libc_s_and_a_non_function_calls_nothing(
    tmp_path: Path,
) -> None:
    """A held name an included header declares is libc's even though the
    repository defines one (WI-rimon's rule, as for a direct call -- the shape
    of jemalloc's ``pthread_create_fptr = pthread_create`` fallback); ``=
    oom_handler`` (a parameter) holds nothing nameable, and a pointer holding
    only that keeps its unresolved edge."""
    (tmp_path / "shim.c").write_text("int send(int fd) { return 0; }\n")
    calls = _calls(tmp_path, (
        "#include <sys/socket.h>\n"
        "static int (*send_fptr)(int);\n"
        "static void (*oom)(int);\n"
        "void init(void) {\n"
        "  send_fptr = send;\n"
        "}\n"
        "void set_oom(void (*oom_handler)(int)) {\n"
        "  oom = oom_handler;\n"
        "}\n"
        "int spawn(int fd) {\n"
        "  return send_fptr(fd);\n"
        "}\n"
        "void fail(int n) {\n"
        "  oom(n);\n"
        "}\n"
    ))
    assert [t for t, _ in calls["spawn"]] == ["c:sys/socket.h:0-0:send:unresolved"]
    assert [t for t, _ in calls["fail"]] == ["c:external:0-0:oom:unresolved"]


_CLAIMS = """\
claims:
  - id: SECRET-NO-NET
    text: A secret is never sent.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""


def test_a_secret_sent_through_the_pointer_is_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.c").write_text("#include <stdlib.h>\n" + _SEND_IMPL + (
        "static int (*send_data)(int, const char *, int) = send_impl;\n"
        "void go(int fd) {\n"
        "  char *p = getenv(\"PASS\");\n"
        "  send_data(fd, p, 8);\n"
        "}\n"
    ))
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated"
