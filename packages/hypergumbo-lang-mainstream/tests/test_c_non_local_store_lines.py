# SPDX-License-Identifier: AGPL-3.0-or-later
"""A plain assignment into memory a C function does not own is disclosed (INV-nohob 3/3).

PR 2 stamped the CALL-shaped ways a value leaves where taint follows it.
``g_secret = getenv("K")`` in one function and ``send(fd, g_secret, ...)``
in another is the same loss with no call to stamp: the walk sees the value
defined into ``g_secret`` and, finding no later use in that function, reports
nothing, and the verdict read ``confirmed``. c.py now records each
function's non-local store lines (``meta.non_local_store_lines``): an
assignment to a name the function does not declare (a global, an ``extern``),
or through a pointer parameter (``c->f = v``, ``*c = v``, ``c[i] = v``).
Following the value is WI-zitul (global state) and WI-nulam (out-parameters).
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.c import analyze_c


def _lines(tmp_path: Path, text: str) -> dict[str, list[int]]:
    (tmp_path / "m.c").write_text(text)
    return {
        s.name: list((s.meta or {}).get("non_local_store_lines", []))
        for s in analyze_c(tmp_path).symbols if s.kind == "function"
    }


def test_stores_into_globals_and_through_pointer_parameters(tmp_path: Path) -> None:
    lines = _lines(tmp_path, (
        "struct conf { char *pass; int n[4]; };\n"     # 1
        "struct conf g_conf;\n"                         # 2
        "char *g_secret;\n"                             # 3
        "void f(char *v, struct conf *c, int *out) {\n"  # 4
        "  g_secret = v;\n"                             # 5
        "  c->pass = v;\n"                              # 6
        "  *out = 1;\n"                                 # 7
        "  c->n[0] = 2;\n"                              # 8
        "  g_conf.pass = v;\n"                          # 9
        "}\n"
    ))
    assert lines["f"] == [5, 6, 7, 8, 9]


def test_locals_value_parameters_and_re_pointing_are_not_stores(tmp_path: Path) -> None:
    lines = _lines(tmp_path, (
        "void f(char *v, char *c, int n) {\n"  # 1
        "  char *x;\n"                         # 2
        "  static int hits;\n"                 # 3
        "  struct { int a; } s;\n"             # 4
        "  x = v;\n"                           # 5
        "  hits = n;\n"                        # 6
        "  s.a = n;\n"                         # 7
        "  c = v;\n"                           # 8
        "  n = 3;\n"                           # 9
        "}\n"
    ))
    assert lines["f"] == []


_CLAIMS = """\
claims:
  - id: SECRET-NO-NET
    text: A secret is never sent.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> dict:
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "m.c").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_a_secret_stored_into_a_global_is_not_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, (
        "#include <stdlib.h>\n#include <sys/socket.h>\n"
        "char *g_secret;\n"
        "void init(void) {\n  g_secret = getenv(\"PASS\");\n}\n"
        "void go(int fd) {\n  send(fd, g_secret, 8, 0);\n}\n"
    ))
    assert verdict["verdict"] == "inconclusive"
    assert "non_local_store" in verdict["details"]


def test_a_secret_kept_in_a_local_is_still_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, (
        "#include <stdlib.h>\n"
        "int init(void) {\n  char *p;\n  p = getenv(\"PASS\");\n  return p != 0;\n}\n"
    ))
    assert verdict["verdict"] == "confirmed"
