# SPDX-License-Identifier: AGPL-3.0-or-later
"""c.py stamps the call sites C taint cannot follow a value through (INV-nohob).

A taint verdict reads clean only if every flow is reported or disclosed as
unfollowed. Four C constructs carry a value somewhere the intraprocedural walk
and the call graph do not go, so c.py names each one on the call edge
(``meta.unfollowed_construct``) and taint withholds a clean verdict whose
source value reaches one:

* ``out_parameter_call`` -- a call to a project function that writes through a
  pointer parameter (torsocks' ``conf_file_set_socks5_pass(password,
  &tsocks_config)``, whose body ``strlcpy``s into ``config->...``);
* ``non_local_store`` -- a copy-family call whose destination is rooted at the
  caller's own pointer parameter or a file-scope variable
  (``snprintf(config->conf_file.socks5_username, ..., pid, now)``);
* ``macro_call`` -- a call to a function-like macro the file can see (``DBG``);
* ``variadic_call`` -- a call that passes arguments in a project function's
  ``...`` tail (``log_print(fmt, pass)``).

Following each one is filed separately (WI-nulam, WI-zitul, WI-ropab,
WI-ronuj); this is the disclosure.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.c import analyze_c


def _stamps(tmp_path: Path, files: dict[str, str]) -> dict[tuple[str, str], str | None]:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    result = analyze_c(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    out: dict[tuple[str, str], str | None] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            callee = names.get(e.dst) or e.dst.split(":")[-2]
            out[(names[e.src], callee)] = (e.meta or {}).get("unfollowed_construct")
    return out


def test_a_callee_writing_through_a_pointer_parameter(tmp_path: Path) -> None:
    stamps = _stamps(tmp_path, {"m.c": (
        "#include <string.h>\n"
        "struct conf { char pass[64]; int n; };\n"
        "struct conf g_conf;\n"
        "static void set_pass(const char *v, struct conf *c) {\n"
        "  strncpy(c->pass, v, 63);\n"
        "}\n"
        "static void set_n(int v, struct conf *c) {\n"
        "  c->n = v;\n"
        "}\n"
        "static void set_deref(int v, int *out) {\n"
        "  *out = v;\n"
        "}\n"
        "static int pure(int v) {\n"
        "  return v + 1;\n"
        "}\n"
        "void init(const char *p, int *slot) {\n"
        "  set_pass(p, &g_conf);\n"
        "  set_n(1, &g_conf);\n"
        "  set_deref(2, slot);\n"
        "  pure(3);\n"
        "}\n"
    )})
    assert stamps[("init", "set_pass")] == "out_parameter_call"
    assert stamps[("init", "set_n")] == "out_parameter_call"
    assert stamps[("init", "set_deref")] == "out_parameter_call"
    assert stamps[("init", "pure")] is None


def test_a_copy_into_non_local_memory(tmp_path: Path) -> None:
    stamps = _stamps(tmp_path, {"m.c": (
        "#include <stdio.h>\n"
        "#include <string.h>\n"
        "struct conf { char user[64]; };\n"
        "char g_buf[64];\n"
        "void name_it(struct conf *config, long pid) {\n"
        "  snprintf(config->user, sizeof(config->user), \"t-%ld\", pid);\n"
        "}\n"
        "void keep(const char *v) {\n"
        "  strcpy(g_buf, v);\n"
        "}\n"
        "void local_only(const char *v) {\n"
        "  char tmp[64];\n"
        "  strcpy(tmp, v);\n"
        "}\n"
    )})
    assert stamps[("name_it", "snprintf")] == "non_local_store"
    assert stamps[("keep", "strcpy")] == "non_local_store"
    assert stamps[("local_only", "strcpy")] is None


def test_a_function_like_macro_the_file_can_see(tmp_path: Path) -> None:
    stamps = _stamps(tmp_path, {
        "log.h": "#define DBG(fmt, args...) log_print(fmt, ## args)\n#define LEVEL 3\n",
        "m.c": (
            "#include \"log.h\"\n"
            "#define LOCAL_MACRO(x) do_it(x)\n"
            "void go(const char *p) {\n"
            "  DBG(\"pass %s\", p);\n"
            "  LOCAL_MACRO(p);\n"
            "  not_a_macro(p);\n"
            "}\n"
        ),
    })
    assert stamps[("go", "DBG")] == "macro_call"
    assert stamps[("go", "LOCAL_MACRO")] == "macro_call"
    assert stamps[("go", "not_a_macro")] is None


def test_arguments_in_a_project_variadic_tail(tmp_path: Path) -> None:
    stamps = _stamps(tmp_path, {"m.c": (
        "void log_print(const char *fmt, ...) {\n"
        "}\n"
        "void go(const char *p) {\n"
        "  log_print(\"%s\", p);\n"
        "}\n"
        "void plain(void) {\n"
        "  log_print(\"no args\");\n"
        "}\n"
    )})
    assert stamps[("go", "log_print")] == "variadic_call"
    assert stamps[("plain", "log_print")] is None


# --- the verdict: a value lost through one of them is not a clean answer ------

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

_CLAIMS = """\
claims:
  - id: SECRET-NO-NET
    text: A secret is never sent.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""

_SHAPES = {
    "out_parameter_call": (
        "#include <stdlib.h>\n#include <string.h>\n#include <sys/socket.h>\n"
        "struct conf { char pass[64]; };\nstruct conf g_conf;\n"
        "static void set_pass(const char *v, struct conf *c) {\n  strncpy(c->pass, v, 63);\n}\n"
        "void init(void) {\n  char *p = getenv(\"PASS\");\n  set_pass(p, &g_conf);\n}\n"
        "void go(int fd) {\n  send(fd, g_conf.pass, 8, 0);\n}\n"
    ),
    "non_local_store": (
        "#include <stdlib.h>\n#include <string.h>\n"
        "char g_buf[64];\n"
        "void keep(void) {\n  char *p = getenv(\"PASS\");\n  strcpy(g_buf, p);\n}\n"
    ),
    "macro_call": (
        "#include <stdio.h>\n#include <stdlib.h>\n"
        "#define DBG(fmt, ...) fprintf(stderr, fmt, __VA_ARGS__)\n"
        "void go(void) {\n  char *p = getenv(\"PASS\");\n  DBG(\"pass %s\\n\", p);\n}\n"
    ),
    "variadic_call": (
        "#include <stdlib.h>\n"
        "void log_print(const char *fmt, ...) {\n}\n"
        "void go(void) {\n  char *p = getenv(\"PASS\");\n  log_print(\"%s\", p);\n}\n"
    ),
}


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


@pytest.mark.parametrize("construct", list(_SHAPES))
def test_a_secret_lost_through_the_construct_is_not_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, construct: str,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _SHAPES[construct])
    assert verdict["verdict"] == "inconclusive"
    assert construct in verdict["details"]


def test_a_found_flow_and_a_clean_function_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _verdict(tmp_path / "a", monkeypatch, (
        "#include <stdlib.h>\n#include <sys/socket.h>\n"
        "void go(int fd) {\n  char *p = getenv(\"PASS\");\n  send(fd, p, 8, 0);\n}\n"
    ))
    assert sent["verdict"] == "violated"
    clean = _verdict(tmp_path / "b", monkeypatch, (
        "#include <stdlib.h>\n#include <string.h>\n"
        "int go(void) {\n  char *p = getenv(\"PASS\");\n  char tmp[64];\n"
        "  strcpy(tmp, \"x\");\n  return p != 0;\n}\n"
    ))
    assert clean["verdict"] == "confirmed"
