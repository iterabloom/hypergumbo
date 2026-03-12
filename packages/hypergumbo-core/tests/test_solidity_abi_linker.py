"""Tests for the Solidity ABI bridge linker.

Covers: Solidity function export detection, TypeScript/JavaScript ethers.js and
viem contract call patterns, edge creation between TS/JS callers and Solidity
function definitions, and registry integration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol


def _make_span(start: int = 1, end: int = 1) -> Span:
    return Span(start_line=start, end_line=end, start_col=0, end_col=0)


def _make_sol_sym(
    name: str,
    path: str = "contracts/Token.sol",
    kind: str = "function",
    start_line: int = 1,
) -> Symbol:
    """Create a Solidity symbol."""
    return Symbol(
        id=f"solidity:{path}:{start_line}-{start_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="solidity",
        path=path,
        span=_make_span(start_line, start_line),
    )


def _make_ts_sym(
    name: str,
    path: str = "scripts/deploy.ts",
    language: str = "typescript",
    kind: str = "function",
    start_line: int = 1,
) -> Symbol:
    """Create a TypeScript/JavaScript symbol."""
    return Symbol(
        id=f"{language}:{path}:{start_line}-{start_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=_make_span(start_line, start_line),
    )


class TestCollectSolidityFunctions:
    """Tests for _collect_solidity_functions internal function."""

    def test_basic_function(self) -> None:
        """Detects Solidity function symbols."""
        from hypergumbo_core.linkers.solidity_abi import _collect_solidity_functions

        sym = _make_sol_sym("transfer")
        result = _collect_solidity_functions([sym])
        assert "transfer" in result
        assert sym in result["transfer"]

    def test_ignores_non_function_kinds(self) -> None:
        """Skips Solidity contracts, events, modifiers."""
        from hypergumbo_core.linkers.solidity_abi import _collect_solidity_functions

        contract = _make_sol_sym("Token", kind="contract")
        event = _make_sol_sym("Transfer", kind="event")
        modifier = _make_sol_sym("onlyOwner", kind="modifier")
        func = _make_sol_sym("transfer")
        result = _collect_solidity_functions([contract, event, modifier, func])
        assert "Token" not in result
        assert "Transfer" not in result
        assert "onlyOwner" not in result
        assert "transfer" in result

    def test_ignores_non_solidity(self) -> None:
        """Skips non-Solidity symbols."""
        from hypergumbo_core.linkers.solidity_abi import _collect_solidity_functions

        ts_func = _make_ts_sym("transfer")
        result = _collect_solidity_functions([ts_func])
        assert "transfer" not in result

    def test_multiple_overloads(self) -> None:
        """Groups overloaded functions under the same name."""
        from hypergumbo_core.linkers.solidity_abi import _collect_solidity_functions

        f1 = _make_sol_sym("transfer", start_line=10)
        f2 = _make_sol_sym("transfer", path="contracts/ERC20.sol", start_line=20)
        result = _collect_solidity_functions([f1, f2])
        assert len(result["transfer"]) == 2


class TestScanContractCalls:
    """Tests for _scan_contract_calls internal function."""

    def test_ethers_method_call(self, tmp_path: Path) -> None:
        """Detects contract.method() ethers.js pattern."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        ts_file = tmp_path / "deploy.ts"
        ts_file.write_text(
            "const tx = await dao.execute(callId, actions, allowFailureMap);\n"
        )
        result = _scan_contract_calls(tmp_path, {"execute"})
        assert ("deploy.ts", "execute") in {
            (r[0], r[1]) for r in result
        }

    def test_viem_read_contract(self, tmp_path: Path) -> None:
        """Detects viem readContract({ functionName: 'foo' }) pattern."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        ts_file = tmp_path / "client.ts"
        ts_file.write_text(
            "const balance = await readContract({\n"
            "  address: tokenAddr,\n"
            "  abi: ERC20_ABI,\n"
            "  functionName: 'balanceOf',\n"
            "  args: [userAddr],\n"
            "});\n"
        )
        result = _scan_contract_calls(tmp_path, {"balanceOf"})
        assert ("client.ts", "balanceOf") in {
            (r[0], r[1]) for r in result
        }

    def test_viem_write_contract(self, tmp_path: Path) -> None:
        """Detects viem writeContract({ functionName: 'foo' }) pattern."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        ts_file = tmp_path / "client.ts"
        ts_file.write_text(
            'await writeContract({ functionName: "transfer", args: [to, amt] });\n'
        )
        result = _scan_contract_calls(tmp_path, {"transfer"})
        assert ("client.ts", "transfer") in {
            (r[0], r[1]) for r in result
        }

    def test_no_false_positive_for_non_contract_methods(
        self, tmp_path: Path
    ) -> None:
        """Does not match method calls when the name is not a Solidity function."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        ts_file = tmp_path / "util.ts"
        ts_file.write_text("console.log('hello');\narray.push(1);\n")
        # Only "transfer" is a known Solidity function name
        result = _scan_contract_calls(tmp_path, {"transfer"})
        assert len(result) == 0

    def test_skips_sol_files(self, tmp_path: Path) -> None:
        """Does not scan .sol files for contract calls."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        sol_file = tmp_path / "Token.sol"
        sol_file.write_text("token.transfer(to, amount);\n")
        result = _scan_contract_calls(tmp_path, {"transfer"})
        assert len(result) == 0

    def test_multiple_calls_in_one_file(self, tmp_path: Path) -> None:
        """Detects multiple contract calls in the same file."""
        from hypergumbo_core.linkers.solidity_abi import _scan_contract_calls

        ts_file = tmp_path / "deploy.ts"
        ts_file.write_text(
            "await contract.approve(spender, amount);\n"
            "await contract.transfer(to, amount);\n"
        )
        result = _scan_contract_calls(tmp_path, {"approve", "transfer"})
        names = {r[1] for r in result}
        assert "approve" in names
        assert "transfer" in names


class TestLinkSolidityAbi:
    """Integration tests for link_solidity_abi."""

    def test_basic_link(self, tmp_path: Path) -> None:
        """Creates abi_call edge from TS call to Solidity function."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        sol_func = _make_sol_sym("transfer")
        ts_func = _make_ts_sym("deploy")

        ts_file = tmp_path / "scripts" / "deploy.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("await token.transfer(to, amount);\n")

        result = link_solidity_abi(
            tmp_path, [ts_func], [sol_func],
        )
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "abi_call"
        assert edge.dst == sol_func.id

    def test_viem_pattern_creates_edge(self, tmp_path: Path) -> None:
        """Creates abi_call edge from viem readContract to Solidity function."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        sol_func = _make_sol_sym("balanceOf")
        ts_func = _make_ts_sym("getBalance")

        ts_file = tmp_path / "scripts" / "getBalance.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text(
            "const bal = await readContract({\n"
            "  functionName: 'balanceOf',\n"
            "  args: [addr],\n"
            "});\n"
        )

        result = link_solidity_abi(tmp_path, [ts_func], [sol_func])
        assert len(result.edges) == 1
        assert result.edges[0].dst == sol_func.id

    def test_no_edges_without_matching_names(self, tmp_path: Path) -> None:
        """No edges when TS methods don't match Solidity functions."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        sol_func = _make_sol_sym("transfer")
        ts_func = _make_ts_sym("deploy")

        ts_file = tmp_path / "scripts" / "deploy.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("await token.approve(spender, amount);\n")

        result = link_solidity_abi(tmp_path, [ts_func], [sol_func])
        assert len(result.edges) == 0

    def test_synthetic_node_created(self, tmp_path: Path) -> None:
        """Creates synthetic abi_call node for the call site."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        sol_func = _make_sol_sym("transfer")
        ts_func = _make_ts_sym("deploy")

        ts_file = tmp_path / "scripts" / "deploy.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("await token.transfer(to, amount);\n")

        result = link_solidity_abi(tmp_path, [ts_func], [sol_func])
        assert len(result.symbols) == 1
        syn = result.symbols[0]
        assert syn.kind == "abi_call"
        assert "transfer" in syn.name

    def test_edge_confidence(self, tmp_path: Path) -> None:
        """Edge confidence is 0.75 (name-based matching is heuristic)."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        sol_func = _make_sol_sym("transfer")
        ts_func = _make_ts_sym("deploy")

        ts_file = tmp_path / "scripts" / "deploy.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("await token.transfer(to, amount);\n")

        result = link_solidity_abi(tmp_path, [ts_func], [sol_func])
        assert result.edges[0].confidence == 0.75

    def test_no_solidity_functions_early_return(self, tmp_path: Path) -> None:
        """Returns empty result when no Solidity functions exist."""
        from hypergumbo_core.linkers.solidity_abi import link_solidity_abi

        ts_func = _make_ts_sym("deploy")
        ts_file = tmp_path / "deploy.ts"
        ts_file.write_text("await token.transfer(to, amount);\n")

        result = link_solidity_abi(tmp_path, [ts_func], [])
        assert len(result.edges) == 0
        assert len(result.symbols) == 0
        assert result.run is not None


class TestRequirementChecks:
    """Tests for linker activation requirement functions."""

    def test_count_ts_js_files(self) -> None:
        """Counts unique TS/JS file paths."""
        from unittest.mock import MagicMock
        from hypergumbo_core.linkers.solidity_abi import _count_ts_js_files

        ctx = MagicMock()
        ctx.symbols = [
            _make_ts_sym("a", path="src/a.ts"),
            _make_ts_sym("b", path="src/a.ts"),  # same path
            _make_ts_sym("c", path="src/b.ts"),
            _make_sol_sym("d"),  # not JS/TS
        ]
        assert _count_ts_js_files(ctx) == 2

    def test_count_solidity_functions(self) -> None:
        """Counts Solidity function symbols."""
        from unittest.mock import MagicMock
        from hypergumbo_core.linkers.solidity_abi import _count_solidity_functions

        ctx = MagicMock()
        ctx.symbols = [
            _make_sol_sym("transfer"),
            _make_sol_sym("Token", kind="contract"),
            _make_sol_sym("approve"),
            _make_ts_sym("deploy"),
        ]
        assert _count_solidity_functions(ctx) == 2


class TestRegistryIntegration:
    """Tests for linker registration and dispatch."""

    def test_linker_registered(self) -> None:
        """solidity_abi linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import list_registered

        # Ensure the module is imported (triggers @register_linker)
        import hypergumbo_core.linkers.solidity_abi

        assert "solidity_abi" in list_registered()

    def test_linker_function_runs(self, tmp_path: Path) -> None:
        """The registered linker function produces results via LinkerContext."""
        from unittest.mock import MagicMock
        from hypergumbo_core.linkers.solidity_abi import solidity_abi_linker

        sol_func = _make_sol_sym("transfer")
        ts_func = _make_ts_sym("deploy", path="scripts/deploy.ts")

        ts_file = tmp_path / "scripts" / "deploy.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("await token.transfer(to, amount);\n")

        ctx = MagicMock()
        ctx.symbols = [sol_func, ts_func]
        ctx.repo_root = tmp_path

        result = solidity_abi_linker(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "abi_call"
