# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-simiv: the same-function sanitizer credit fires on ordinary Python.

WI-fasub's arm (a sanitizer called in the same function as the source earns
``sanitized`` when the barrier walk returns a definite ``False``) was tested
only with hand-built DDG edges. On the production path, three ordinary
decrypt -> encrypt -> write shapes reported ``sanitized_flows 0``. Instrumented
at dev b39173ecbe, the guarded walk never returned ``False``, for two
extractor reasons and not by design:

* ``with open(...) as f: f.write(e)`` -- the ``with`` clause was a CFG
  statement no AST node matched, so ``f`` was never defined and the clause was
  uncovered code; uncovered code forfeits the function's refutation (INV-lupav),
  and a forfeited walk returns ``None``.
* ``open(...).write(e)`` -- the inner ``open(...)`` call overwrote the
  statement's uses with ``[]``, so ``e`` had no recorded use and the walk
  escaped.

The core half is ``test_cfg_statement_node_matching.py``. This file pins the
python ``with_clause`` rule and the end-to-end credit, with a control whose
encrypt call does NOT sit between source and sink.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.py_def_use import PythonDefUseExtractor


def _with_clause(source: str) -> tuple[Any, bytes]:
    src = source.encode()
    tree = tree_sitter.Parser(get_language("python")).parse(src)
    with_stmt = tree.root_node.children[0]
    clause = next(c for c in with_stmt.children if c.type == "with_clause")
    return clause, src


@pytest.mark.parametrize(("source", "defines", "uses"), [
    ('with open(p, "wb") as f:\n    pass\n', ["f"], ["p"]),
    ("with lock:\n    pass\n", [], ["lock"]),
    ("with a(x) as f, b(y) as (g, h):\n    pass\n", ["f", "g", "h"], ["x", "y"]),
    ("with x.y() as [m, n]:\n    pass\n", ["m", "n"], ["x"]),
    ("with a() as self.f:\n    pass\n", ["self"], []),
])
def test_a_with_clause_defines_its_targets_and_uses_its_values(
    source: str, defines: list[str], uses: list[str],
) -> None:
    clause, src = _with_clause(source)
    result = PythonDefUseExtractor().extract(clause, src)
    assert sorted(result.defines) == sorted(defines)
    assert sorted(result.uses) == sorted(uses)


_CLAIMS = """claims:
  - id: PLAINTEXT-FS
    text: Decrypted plaintext never reaches a filesystem write.
    constraint:
      taint_flow:
        source_taint: plaintext
        prohibited_sink_zone: host_fs
"""

_WITH = """from cryptography.fernet import Fernet
def reenc(key, ct):
    pt = Fernet(key).decrypt(ct)
    e = Fernet(key).encrypt(pt)
    with open("out.bin", "wb") as f:
        f.write(e)
"""

_CHAINED = """from cryptography.fernet import Fernet
def reenc(key, ct):
    pt = Fernet(key).decrypt(ct)
    e = Fernet(key).encrypt(pt)
    open("o", "wb").write(e)
"""

#: The control. The encrypt call is present but the plaintext reaches the
#: write around it, so no credit may be earned.
_AROUND = """from cryptography.fernet import Fernet
def reenc(key, ct):
    pt = Fernet(key).decrypt(ct)
    e = Fernet(key).encrypt(pt)
    with open("out.bin", "wb") as f:
        f.write(e)
        f.write(pt)
"""


def _verdict(tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str],
             monkeypatch: pytest.MonkeyPatch) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(source)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
          "--format", "json"])
    return json.loads(capsys.readouterr().out)["verdicts"][0]


@pytest.mark.parametrize("source", [_WITH, _CHAINED], ids=["with", "chained"])
def test_encrypt_before_write_earns_the_sanitizer_credit(
    tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credit removes the flow from the violation set. The verdict itself
    is ``inconclusive`` here, and correctly: the coverage gate withholds a
    clean verdict because ``cryptography.fernet`` has no I/O catalogue rows,
    which is a separate question from whether the flow was sanitized."""
    v = _verdict(tmp_path, source, capsys, monkeypatch)
    assert v["sanitized_flows"] >= 1, v
    assert v["evidence_count"] == 0, v
    assert v["verdict"] != "violated", v["verdict"]


def test_plaintext_written_around_the_encrypt_is_still_a_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v = _verdict(tmp_path, _AROUND, capsys, monkeypatch)
    assert v["verdict"] == "violated", v
