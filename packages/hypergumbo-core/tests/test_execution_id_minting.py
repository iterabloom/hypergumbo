# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/ir.py
"""Every run id is minted by ``ir.new_execution_id``.

The spec gives ``AnalysisRun.execution_id`` the form ``uuid:<UUID v4>`` (or
``sha256:`` for a deterministic hash). ``AnalysisRun.create`` minted that,
and ten producers that build their run by hand each spelled it again. Two
spelled it differently: the starlark analyzer minted a bare UUID, without
the prefix, and the circom analyzer's grammar-unavailable run used
``skip-circom``, the same string on every run. The gate below reads every
package's source, so a producer added later cannot mint its own.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from hypergumbo_core.ir import AnalysisRun, new_execution_id

_RUN_ID = re.compile(
    r"uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_ROOT = Path(__file__).resolve().parents[3]
_MINTER = ("packages/hypergumbo-core/src/hypergumbo_core/ir.py", "new_execution_id")


def test_new_execution_id_is_a_prefixed_uuid4() -> None:
    first, second = new_execution_id(), new_execution_id()
    assert _RUN_ID.fullmatch(first) and _RUN_ID.fullmatch(second)
    assert first != second
    run = AnalysisRun.create(pass_id="probe", version="0")
    assert _RUN_ID.fullmatch(run.execution_id)


def _hand_minted(path: Path, rel: str) -> list[str]:
    """``"<rel>:<line> <what>"`` for each run id ``path`` mints by hand: a
    ``uuid4()`` call outside the minter, or an ``execution_id=`` argument
    that is a literal or an f-string."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed: set[int] = set()
    if rel == _MINTER[0]:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == _MINTER[1]:
                allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno not in allowed:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "uuid4":
                found.append(f"{rel}:{node.lineno} uuid4()")
        if (
            isinstance(node, ast.keyword) and node.arg == "execution_id"
            and isinstance(node.value, (ast.Constant, ast.JoinedStr))
        ):
            found.append(f"{rel}:{node.value.lineno} execution_id=<literal>")
    return sorted(found)


def test_no_producer_mints_a_run_id_by_hand() -> None:
    sources = sorted(_ROOT.glob("packages/*/src/**/*.py"))
    assert any(p.name == "ir.py" for p in sources)  # reach: the tree is read
    offenders = [
        site for path in sources
        for site in _hand_minted(path, path.relative_to(_ROOT).as_posix())
    ]
    assert offenders == [], (
        "mint a run id with hypergumbo_core.ir.new_execution_id(): " + ", ".join(offenders)
    )


def test_the_gate_sees_both_forms(tmp_path: Path) -> None:
    """Control: the reader finds a bare ``uuid4()`` and a literal
    ``execution_id=``, and passes the minter itself."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import uuid\n"
        "run_id = str(uuid.uuid4())\n"
        "AnalysisRun(execution_id=f'skip-{x}')\n"
    )
    assert _hand_minted(probe, "probe.py") == [
        "probe.py:2 uuid4()", "probe.py:3 execution_id=<literal>",
    ]
    ir_path = _ROOT / _MINTER[0]
    assert "uuid4" in ir_path.read_text(encoding="utf-8")
    assert _hand_minted(ir_path, _MINTER[0]) == []
