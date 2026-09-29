# SPDX-License-Identifier: AGPL-3.0-or-later
"""The C def/use extractor, CFG mapping and DDG spec (WI-himob).

Before this, c had no extractor, no CFG mapping and no DDG spec, so every c
taint finding was ``structural`` with the walk ``unavailable``. Measured on the
shipped CLI: ``getenv`` then ``send`` of that value, and ``getenv`` whose result
is thrown away beside a ``send`` of a constant, produced BYTE-IDENTICAL
verdicts. A finding that cannot be told from its refutation cannot be triaged.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.c_def_use import CDefUseExtractor

_FUNC = """int f(int fd) {
    %s
}
"""


def _extract(stmt: str) -> tuple[list[str], list[str]]:
    src = (_FUNC % stmt).encode()
    tree = get_parser("c").parse(src)
    body = tree.root_node.named_children[0].child_by_field_name("body")
    result = CDefUseExtractor().extract(body.named_children[0], src)
    return result.defines, result.uses


@pytest.mark.parametrize("stmt,defines,uses", [
    ('char *k = getenv("K"), buf[8];', ["k"], []),
    ("int n;", [], []),                              # declares, defines no value
    ("char big[n];", [], ["n"]),                     # an array size is a read
    ("n = strlen(k);", ["n"], ["k"]),                # the callee name is not a use
    ("n += m;", ["n"], ["n", "m"]),                  # compound assignment reads first
    ("s->len = n;", ["s"], ["s", "n"]),              # a field write mutates its root
    ("*p = k;", ["p"], ["p", "k"]),
    ("a[i] = k;", ["a"], ["a", "i", "k"]),
    ("i++;", ["i"], ["i"]),
    ("ops->send(fd, k);", [], ["ops", "fd", "k"]),   # a callee EXPRESSION reads its root
    ("x = (int)sizeof(buf);", ["x"], ["buf"]),       # type positions are not reads
    ("while ((n = read(fd, b, 8)) > 0) n--;", ["n", "n"], ["fd", "b", "n"]),
])
def test_defines_and_uses(stmt: str, defines: list[str], uses: list[str]) -> None:
    assert _extract(stmt) == (defines, uses)


_CLAIMS = (
    "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
)
_HEAD = "#include <stdlib.h>\n#include <string.h>\n#include <sys/socket.h>\n\n"


def _evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "leak.c").write_text(_HEAD + "void leak(int fd) {\n" + body + "}\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_a_real_flow_is_confirmed_by_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _evidence(tmp_path, monkeypatch,
                        '    char *k = getenv("API_KEY");\n    send(fd, k, strlen(k), 0);\n')
    assert verdict["verdict"] == "violated", verdict["details"]
    assert [(e["analysis_method"], e["walk_verdict"]) for e in verdict["evidence"]] == [
        ("ddg", "confirmed"),
    ]


def test_a_secret_that_never_reaches_the_sink_is_labelled_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ITEM'S REPRO: source and sink sharing a function, the value not passed.

    ``ddg_mixed`` is java's label for the same shape (WI-gotun): the walk ran and
    did not confirm the value reaches the sink. The finding still counts -- the
    extractor changes the LABEL, not the count, as the item priced it -- but it
    is no longer indistinguishable from the leak above.
    """
    verdict = _evidence(tmp_path, monkeypatch,
                        '    char *k = getenv("API_KEY");\n    int n = 1;\n'
                        '    send(fd, "x", n, 0);\n')
    assert [e["analysis_method"] for e in verdict["evidence"]] == ["ddg_mixed"]


def test_c_reports_data_flow_capable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The four blockers verify-claims named for c (cfg_mapping, atomic_statement,
    def_use_extractor, ddg_spec) are gone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "leak.c").write_text(
        _HEAD + 'void leak(int fd) {\n    char *k = getenv("K");\n    send(fd, k, 1, 0);\n}\n'
    )
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (row,) = [x for x in json.loads(buf.getvalue())["dataflow_coverage"]["languages"]
              if x["language"] == "c"]
    assert row["blockers"] == [] and row["dataflow_capable"] is True, row
