# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on OCaml's call edges (audit 0030, WI-hirol).

``A.run`` found under the name the call site writes (exact, no suffix) is
``qualified``; a bare name the run-wide resolver finds takes the lookup's
value (``name``). A call nothing declares ends on the synthetic ``?`` node
and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.ocaml import analyze_ocaml


def test_ocaml_routes(tmp_path: Path) -> None:
    (tmp_path / "util.ml").write_text("let helper x = x\n")
    (tmp_path / "main.ml").write_text(
        "module A = struct\n  let run x = x\nend\n\n"
        "let go y = A.run y\nlet bare y = helper y\nlet ext y = List.length y\n"
    )
    calls = {
        e.src.split(":")[-2]: e for e in analyze_ocaml(tmp_path).edges
        if e.edge_type == "calls"
    }
    assert calls["go"].dst.endswith(":A.run:function")
    assert calls["go"].meta["resolved_by"] == "qualified"
    assert calls["bare"].dst.endswith(":helper:function")
    assert calls["bare"].meta["resolved_by"] == "name"
    assert calls["ext"].dst == "ocaml:?:0-0:length:function"
    assert "resolved_by" not in calls["ext"].meta
