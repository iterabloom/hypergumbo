# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-gotir: the DDG walk must not spend a Python stack frame per AST level.

WHAT WAS WRONG. ``ddg_build._walk_functions`` was a pure pre-order traversal
that recursed once per child::

    for child in node.children:
        _walk_functions(child, ...)

so the Python stack depth WAS the tree-sitter AST depth, one frame per level.
Python's default recursion limit is 1000, and a machine-generated file exceeds
it easily: measured on keda,
``vendor/go.temporal.io/api/workflowservice/v1/request_response.pb.go``
(725,832 bytes of generated protobuf) has an **AST depth of 1171**, because one
``const`` is a string built as ``"..." + "..." + "..."`` **1,165 times** and
``+`` is left-associative, yielding a 1,165-deep left spine.

WHAT THE USER SAW, and why this is filed as an invariant rather than a bug:
``verify-claims`` aborted with ``RecursionError`` and exited **1 with an empty
stdout** — and exit 1 is also what VIOLATED returns. A CI gate written
``verify-claims ... || exit 1`` cannot tell "claims violated" from "the analysis
crashed and examined nothing", and the JSON consumer gets a zero-byte file
instead of a verdict list.

WHY AN EXPLICIT STACK RATHER THAN ``sys.setrecursionlimit``. Raising the limit
trades a catchable ``RecursionError`` for a C-stack segfault, which is strictly
worse for a tool whose failure mode here is already indistinguishable from a
finding. The transformation is EXACTLY EQUIVALENT for this function precisely
because there is no work after the children loop: the recursion carries no
state a worklist cannot hold, and visit order is preserved by pushing children
in reverse.

SCOPE, MEASURED. An AST sweep finds 72 self-recursive child-loop walks in the
tree, so this shape is a class. A corpus AST-depth census (files >= 60KB,
measured iteratively so the instrument cannot hit the bug it measures) sizes
the realized exposure narrowly: keda has 8 files at depth >= 500 and **7 at
>= 900**, all generated Go under ``vendor/``; dash.js, caddy and mitmproxy top
out at 74, 37 and 79. Six of keda's seven are
``golang.org/x/text/unicode/norm/tables*.go`` at 947-948 — within 5% of the
limit, so this is not one pathological file. ``survey`` on the same input does
NOT crash: only the DDG build did, which is why the fix is scoped here and the
class is filed rather than rewritten wholesale.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from hypergumbo_core.ddg_build import build_repo_ddg

#: Deep enough to exceed CPython's default 1000-frame limit with margin, small
#: enough to be a fixture: 1,200 terms is ~13KB and parses to depth ~1203.
_TERMS = 1200


@pytest.fixture(autouse=True)
def _extractor_registered() -> None:
    """The Python def/use extractor registers on IMPORT and lives in a language
    package, so ``ddg_edges`` is empty without it — and ``test_cfg.py`` calls
    ``clear_def_use_extractors()``, after which a bare import is a no-op
    because the module is already in ``sys.modules``. Same guard, and same
    reason, as ``test_ddg_build.py::test_python_still_produces_edges``: without
    it these assertions would pass vacuously against a pipeline that computed
    nothing.
    """
    import hypergumbo_lang_mainstream.py_def_use as py_mod
    from hypergumbo_core.cfg import get_def_use_extractor

    if get_def_use_extractor("python") is None:
        importlib.reload(py_mod)
    assert get_def_use_extractor("python") is not None


def _deep_module(n: int = _TERMS) -> str:
    """A left-associative ``+`` chain followed by a function with a real
    def-use chain.

    The chain is not contrived: it is the same construct as the protobuf
    descriptor that produced the live crash. ``handler`` sits AFTER it as a
    sibling, so a walk that lost its remaining work at the deep node would
    never reach it — which is what the second test below turns into an
    assertion rather than an assumption.
    """
    chain = " + ".join(f'"seg{i}"' for i in range(n))
    return (
        f"blob = {chain}\n"
        "\n"
        "\n"
        "def handler(req):\n"
        "    secret = req.password\n"
        "    send(secret)\n"
    )


class TestADeepAstDoesNotCrashTheDdgWalk:
    def test_build_repo_ddg_survives_a_1200_deep_expression(
        self, tmp_path: Path,
    ) -> None:
        """Pre-fix this raised ``RecursionError`` at ``ddg_build.py:206``."""
        (tmp_path / "deep.py").write_text(_deep_module())
        result = build_repo_ddg(tmp_path, ["python"])
        assert result is not None

    def test_the_function_after_the_deep_node_is_still_analysed(
        self, tmp_path: Path,
    ) -> None:
        """NOT CRASHING IS NOT ENOUGH (L7). A walk that silently dropped its
        remaining work at the deep node would pass the test above too. This
        asserts the deep file yields the SAME analysed functions as a shallow
        one — so the traversal is shown to complete, not merely to survive.
        """
        (tmp_path / "deep.py").write_text(_deep_module(2))
        shallow = build_repo_ddg(tmp_path, ["python"])
        (tmp_path / "deep.py").write_text(_deep_module())
        deep = build_repo_ddg(tmp_path, ["python"])
        assert shallow.ddg_symbols, "floor: the shallow arm must analyse something"
        assert {s.rsplit(":", 2)[-2] for s in deep.ddg_symbols} == {
            s.rsplit(":", 2)[-2] for s in shallow.ddg_symbols
        }
        assert len(deep.ddg_edges) == len(shallow.ddg_edges)

    @pytest.mark.parametrize("terms", [2, 1200, 3000])
    def test_it_is_depth_independent(self, tmp_path: Path, terms: int) -> None:
        """The property is not "1200 works" but "depth does not matter". 3000
        is past any limit a recursive walk could reach by tuning."""
        (tmp_path / "deep.py").write_text(_deep_module(terms))
        result = build_repo_ddg(tmp_path, ["python"])
        assert any("handler" in s for s in result.ddg_symbols)
