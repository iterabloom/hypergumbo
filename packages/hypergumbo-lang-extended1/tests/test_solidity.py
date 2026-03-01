"""Tests for Solidity analysis pass."""
from pathlib import Path

import pytest

from hypergumbo_lang_extended1.solidity import (
    analyze_solidity,
    find_solidity_files,
    is_solidity_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository with Solidity files."""
    return tmp_path


class TestFindSolidityFiles:
    """Tests for find_solidity_files function."""

    def test_finds_sol_files(self, temp_repo: Path) -> None:
        """Finds .sol files in repo."""
        (temp_repo / "Token.sol").write_text("contract Token {}")
        (temp_repo / "ERC20.sol").write_text("contract ERC20 {}")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_solidity_files(temp_repo))
        filenames = {f.name for f in files}

        assert "Token.sol" in filenames
        assert "ERC20.sol" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_sol_files(self, temp_repo: Path) -> None:
        """Finds .sol files in subdirectories."""
        contracts = temp_repo / "contracts"
        contracts.mkdir()
        (contracts / "Token.sol").write_text("contract Token {}")

        files = list(find_solidity_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "Token.sol"


class TestSolidityTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_solidity_tree_sitter_available()
        assert isinstance(result, bool)


class TestSolidityAnalysis:
    """Tests for Solidity analysis with tree-sitter."""

    def test_analyzes_contract(self, temp_repo: Path) -> None:
        """Detects contract declarations."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    uint256 public totalSupply;
}
""")

        result = analyze_solidity(temp_repo)

        assert not result.skipped
        assert any(s.kind == "contract" and s.name == "Token" for s in result.symbols)

    def test_analyzes_interface(self, temp_repo: Path) -> None:
        """Detects interface declarations."""
        (temp_repo / "IERC20.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20 {
    function totalSupply() external view returns (uint256);
}
""")

        result = analyze_solidity(temp_repo)

        assert any(s.kind == "interface" and s.name == "IERC20" for s in result.symbols)

    def test_analyzes_library(self, temp_repo: Path) -> None:
        """Detects library declarations."""
        (temp_repo / "SafeMath.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

library SafeMath {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        return a + b;
    }
}
""")

        result = analyze_solidity(temp_repo)

        assert any(s.kind == "library" and s.name == "SafeMath" for s in result.symbols)

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions within contracts."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)

        functions = [s for s in result.symbols if s.kind == "function"]
        assert any("transfer" in s.name for s in functions)

    def test_analyzes_constructor(self, temp_repo: Path) -> None:
        """Detects constructor definitions."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    address public owner;

    constructor() {
        owner = msg.sender;
    }
}
""")

        result = analyze_solidity(temp_repo)

        assert any(s.kind == "constructor" for s in result.symbols)

    def test_analyzes_modifier(self, temp_repo: Path) -> None:
        """Detects modifier definitions."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    address public owner;

    modifier onlyOwner() {
        require(msg.sender == owner);
        _;
    }
}
""")

        result = analyze_solidity(temp_repo)

        assert any(s.kind == "modifier" and "onlyOwner" in s.name for s in result.symbols)

    def test_analyzes_event(self, temp_repo: Path) -> None:
        """Detects event definitions."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    event Transfer(address indexed from, address indexed to, uint256 value);
}
""")

        result = analyze_solidity(temp_repo)

        assert any(s.kind == "event" and "Transfer" in s.name for s in result.symbols)

    def test_detects_imports(self, temp_repo: Path) -> None:
        """Detects import statements as edges."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract Token {}
""")

        result = analyze_solidity(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_detects_function_calls(self, temp_repo: Path) -> None:
        """Detects function call relationships."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function _mint(address to, uint256 amount) internal {}

    function mint(address to, uint256 amount) public {
        _mint(to, amount);
    }
}
""")

        result = analyze_solidity(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_symbols_have_span(self, temp_repo: Path) -> None:
        """Symbols include source location information."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {}
""")

        result = analyze_solidity(temp_repo)

        contracts = [s for s in result.symbols if s.kind == "contract"]
        assert len(contracts) == 1
        assert contracts[0].span is not None
        assert contracts[0].span.start_line > 0

    def test_symbols_have_language(self, temp_repo: Path) -> None:
        """All symbols have language set to solidity."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {}
""")

        result = analyze_solidity(temp_repo)

        for symbol in result.symbols:
            assert symbol.language == "solidity"

    def test_analysis_run_recorded(self, temp_repo: Path) -> None:
        """Analysis run is recorded with timing info."""
        (temp_repo / "Token.sol").write_text("contract Token {}")

        result = analyze_solidity(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "solidity-v1"
        assert result.run.duration_ms >= 0


class TestSolidityAnalysisWithoutTreeSitter:
    """Tests for graceful degradation without tree-sitter."""

    def test_returns_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter not available."""
        from unittest.mock import patch
        import hypergumbo_lang_extended1.solidity as sol_module

        (temp_repo / "Token.sol").write_text("contract Token {}")

        with patch.object(sol_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="solidity analysis skipped"):
                result = analyze_solidity(temp_repo)

        assert result.skipped
        assert "not available" in result.skip_reason

    def test_returns_false_when_unavailable(self) -> None:
        """Returns False when grammar is not available."""
        from unittest.mock import patch
        import hypergumbo_lang_extended1.solidity as sol_module

        with patch.object(sol_module._analyzer, "_check_grammar_available", return_value=False):
            assert sol_module.is_solidity_tree_sitter_available() is False


class TestSolidityEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_file_symbols(self, temp_repo: Path) -> None:
        """Extracting symbols from an empty file produces no symbols."""
        from hypergumbo_lang_extended1.solidity import _extract_symbols_from_tree
        from hypergumbo_core.analyze.base import FileAnalysis
        import tree_sitter
        import tree_sitter_solidity
        import warnings

        (temp_repo / "Empty.sol").write_text("")

        # Create parser and parse
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            language = tree_sitter.Language(tree_sitter_solidity.language())
            parser = tree_sitter.Parser(language)

        source = (temp_repo / "Empty.sol").read_bytes()
        tree = parser.parse(source)
        analysis = FileAnalysis()

        _extract_symbols_from_tree(tree, source, str(temp_repo / "Empty.sol"), "test-run-id", analysis)

        assert analysis.symbols == []
        assert analysis.symbol_by_name == {}

    def test_empty_file_edges(self, temp_repo: Path) -> None:
        """Extracting edges from an empty file produces no edges."""
        from hypergumbo_lang_extended1.solidity import _extract_edges_from_tree
        from hypergumbo_core.symbol_resolution import NameResolver
        import tree_sitter
        import tree_sitter_solidity
        import warnings

        (temp_repo / "Empty.sol").write_text("")

        # Create parser and parse
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            language = tree_sitter.Language(tree_sitter_solidity.language())
            parser = tree_sitter.Parser(language)

        source = (temp_repo / "Empty.sol").read_bytes()
        tree = parser.parse(source)
        resolver = NameResolver({})

        edges, aliases = _extract_edges_from_tree(
            tree, source, str(temp_repo / "Empty.sol"),
            {}, {}, "test-run-id", resolver,
        )

        assert edges == []
        assert aliases == {}

    def test_find_child_by_type_returns_none(self, temp_repo: Path) -> None:
        """find_child_by_type returns None when child type not found."""
        from hypergumbo_core.analyze.base import find_child_by_type
        import tree_sitter
        import tree_sitter_solidity
        import warnings

        (temp_repo / "Token.sol").write_text("contract Token {}")

        # Create parser and parse
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            language = tree_sitter.Language(tree_sitter_solidity.language())
            parser = tree_sitter.Parser(language)

        source = (temp_repo / "Token.sol").read_bytes()
        tree = parser.parse(source)

        # Try to find a non-existent child type
        result = find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None

    def test_contract_without_name(self, temp_repo: Path) -> None:
        """Handles malformed contracts gracefully."""
        # A file with syntax that might not have a proper identifier
        (temp_repo / "Empty.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Just a pragma, no actual contract
""")

        result = analyze_solidity(temp_repo)

        # Should not crash, just find no contracts
        assert not result.skipped

    def test_function_without_calls(self, temp_repo: Path) -> None:
        """Functions without calls produce no call edges."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function empty() public pure {}
}
""")

        result = analyze_solidity(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 0


class TestSolidityVisibilityModifiers:
    """Tests for Solidity visibility modifier extraction."""

    def test_public_function_has_modifier(self, temp_repo: Path) -> None:
        """Public functions have 'public' in modifiers."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "transfer" in s.name]
        assert len(funcs) == 1
        assert "public" in funcs[0].modifiers

    def test_external_function_has_modifier(self, temp_repo: Path) -> None:
        """External functions have 'external' in modifiers."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function externalFn() external pure returns (uint256) {
        return 42;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "externalFn" in s.name]
        assert len(funcs) == 1
        assert "external" in funcs[0].modifiers

    def test_internal_function_has_modifier(self, temp_repo: Path) -> None:
        """Internal functions have 'internal' in modifiers."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function _internal() internal pure returns (uint256) {
        return 0;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "_internal" in s.name]
        assert len(funcs) == 1
        assert "internal" in funcs[0].modifiers

    def test_private_function_has_modifier(self, temp_repo: Path) -> None:
        """Private functions have 'private' in modifiers."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function _secret() private pure returns (uint256) {
        return 0;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "_secret" in s.name]
        assert len(funcs) == 1
        assert "private" in funcs[0].modifiers

    def test_view_and_pure_captured(self, temp_repo: Path) -> None:
        """State mutability modifiers (view, pure) are captured."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function getValue() public view returns (uint256) {
        return 0;
    }
    function compute() public pure returns (uint256) {
        return 42;
    }
}
""")

        result = analyze_solidity(temp_repo)
        view_fn = next(s for s in result.symbols if s.kind == "function" and "getValue" in s.name)
        pure_fn = next(s for s in result.symbols if s.kind == "function" and "compute" in s.name)

        assert "view" in view_fn.modifiers
        assert "public" in view_fn.modifiers
        assert "pure" in pure_fn.modifiers
        assert "public" in pure_fn.modifiers

    def test_no_visibility_means_empty_modifiers(self, temp_repo: Path) -> None:
        """Functions without explicit visibility have empty modifiers."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function noVisibility() returns (uint256) {
        return 0;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "noVisibility" in s.name]
        assert len(funcs) == 1
        # No visibility keyword → empty modifiers
        assert funcs[0].modifiers == []

    def test_multiple_modifiers_captured(self, temp_repo: Path) -> None:
        """Functions with multiple modifiers capture all of them."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function externalView() external view returns (uint256) {
        return 0;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "externalView" in s.name]
        assert len(funcs) == 1
        assert "external" in funcs[0].modifiers
        assert "view" in funcs[0].modifiers


class TestSolidityInheritance:
    """Tests for Solidity inheritance edge detection."""

    def test_detects_contract_inheritance(self, temp_repo: Path) -> None:
        """Detects 'inherits' edges for contract is Base."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Base {
    function baseFunc() public virtual returns (uint256) { return 0; }
}

contract Token is Base {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)

        inherit_edges = [e for e in result.edges if e.edge_type == "inherits"]
        assert len(inherit_edges) >= 1
        # Token inherits from Base
        token = next(s for s in result.symbols if s.name == "Token" and s.kind == "contract")
        base = next(s for s in result.symbols if s.name == "Base" and s.kind == "contract")
        assert any(e.src == token.id and e.dst == base.id for e in inherit_edges)

    def test_detects_multiple_inheritance(self, temp_repo: Path) -> None:
        """Detects inheritance from multiple parents."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20 {
    function totalSupply() external view returns (uint256);
}

contract Ownable {
    address public owner;
}

contract Token is IERC20, Ownable {
    function totalSupply() external view returns (uint256) { return 0; }
}
""")

        result = analyze_solidity(temp_repo)

        inherit_edges = [e for e in result.edges if e.edge_type == "inherits"]
        # Token inherits from both IERC20 and Ownable
        assert len(inherit_edges) >= 2

    def test_interface_inheritance(self, temp_repo: Path) -> None:
        """Detects interface extending another interface."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20 {
    function totalSupply() external view returns (uint256);
}

interface IERC20Metadata is IERC20 {
    function name() external view returns (string memory);
}
""")

        result = analyze_solidity(temp_repo)

        inherit_edges = [e for e in result.edges if e.edge_type == "inherits"]
        assert len(inherit_edges) >= 1


class TestSolidityOverloading:
    """Tests for Solidity function overloading resolution.

    Solidity supports function overloading (same name, different params).
    The analyzer must correctly identify the enclosing function by position
    rather than by name, so that overloaded functions aren't orphaned.
    """

    def test_overloaded_functions_both_connected(self, temp_repo: Path) -> None:
        """Both overloads of a function should have call edges, not just the last one."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract AccessControl {
    function hasRole(bytes32 role, address account) public view returns (bool) {
        return true;
    }

    function _checkRole(bytes32 role) internal view {
        _checkRole(role, msg.sender);
    }

    function _checkRole(bytes32 role, address account) internal view {
        require(hasRole(role, account));
    }
}
""")

        result = analyze_solidity(temp_repo)

        # Both _checkRole overloads should exist as symbols
        check_role_syms = [s for s in result.symbols if "._checkRole" in s.name and s.kind == "function"]
        assert len(check_role_syms) == 2, f"Expected 2 _checkRole symbols, got {len(check_role_syms)}"

        # Both should be connected (not orphaned)
        connected_ids = set()
        for e in result.edges:
            connected_ids.add(e.src)
            connected_ids.add(e.dst)

        for sym in check_role_syms:
            assert sym.id in connected_ids, (
                f"Overload {sym.name} (lines {sym.span.start_line}-{sym.span.end_line}) is orphaned"
            )

    def test_overloaded_caller_resolved_by_position(self, temp_repo: Path) -> None:
        """Call from first overload should have that overload as src, not the second."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to) public returns (bool) {
        return transfer(to, 0);
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)

        # Get both transfer symbols
        transfer_syms = sorted(
            [s for s in result.symbols if "transfer" in s.name and s.kind == "function"],
            key=lambda s: s.span.start_line,
        )
        assert len(transfer_syms) == 2
        first_overload = transfer_syms[0]  # transfer(address)
        second_overload = transfer_syms[1]  # transfer(address, uint256)

        # The call from transfer(address) to transfer(address, uint256)
        # should have first_overload as src
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        matching = [e for e in call_edges if e.dst == second_overload.id]
        assert len(matching) >= 1, "Expected call edge to second overload"
        assert matching[0].src == first_overload.id, (
            f"Call src should be first overload (line {first_overload.span.start_line}), "
            f"not second (line {second_overload.span.start_line})"
        )


class TestSolidityEmitEdges:
    """Tests for Solidity emit event edge detection.

    Solidity's emit statement (emit Transfer(...)) creates edges from the
    emitting function to the event definition, connecting event symbols
    to the call graph.
    """

    def test_emit_creates_edge_to_event(self, temp_repo: Path) -> None:
        """emit Transfer(...) creates an 'emits' edge from function to event."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    event Transfer(address indexed from, address indexed to, uint256 amount);

    function transfer(address to, uint256 amount) public returns (bool) {
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)

        emit_edges = [e for e in result.edges if e.edge_type == "emits"]
        assert len(emit_edges) >= 1, "Expected at least one 'emits' edge"

        transfer_func = next(s for s in result.symbols if "transfer" in s.name and s.kind == "function")
        transfer_event = next(s for s in result.symbols if "Transfer" in s.name and s.kind == "event")
        assert any(e.src == transfer_func.id and e.dst == transfer_event.id for e in emit_edges)

    def test_multiple_emits_in_same_function(self, temp_repo: Path) -> None:
        """Multiple emit statements create separate edges."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function transferAndApprove(address to, uint256 amount) public {
        emit Transfer(msg.sender, to, amount);
        emit Approval(msg.sender, to, amount);
    }
}
""")

        result = analyze_solidity(temp_repo)

        emit_edges = [e for e in result.edges if e.edge_type == "emits"]
        assert len(emit_edges) >= 2, f"Expected 2+ emits edges, got {len(emit_edges)}"

    def test_event_not_orphaned_when_emitted(self, temp_repo: Path) -> None:
        """Events that are emitted should not be orphans."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    event Transfer(address indexed from, address indexed to, uint256 amount);

    function transfer(address to, uint256 amount) public returns (bool) {
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)

        connected = set()
        for e in result.edges:
            connected.add(e.src)
            connected.add(e.dst)

        transfer_event = next(s for s in result.symbols if "Transfer" in s.name and s.kind == "event")
        assert transfer_event.id in connected, "Event should be connected via emits edge"


class TestSolidityImportAliases:
    """Tests for Solidity import alias tracking (ADR-0007)."""

    def test_extracts_named_import_alias(self, temp_repo: Path) -> None:
        """Extracts aliased imports from named import statements."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import {IERC20 as Token} from "./interfaces/IERC20.sol";

contract MyToken {
    Token public token;
}
""")

        result = analyze_solidity(temp_repo)

        # Import edge should be created for the aliased symbol
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_extracts_namespace_import_alias(self, temp_repo: Path) -> None:
        """Extracts aliased imports from namespace import statements."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import * as Utils from "./utils.sol";

contract MyToken {}
""")

        result = analyze_solidity(temp_repo)

        # Import edge should be created for the namespace import
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestSoliditySuperCalls:
    """Tests for Solidity super.method() and this.method() call resolution."""

    def test_super_call_creates_edge(self, temp_repo: Path) -> None:
        """super.transfer() should create a calls edge to the local transfer function."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }

    function safeTransfer(address to, uint256 amount) public returns (bool) {
        super.transfer(to, amount);
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)
        edges = result.edges

        # Find the edge from safeTransfer → transfer
        safe_transfer = next(
            (s for s in result.symbols if "safeTransfer" in s.name and s.kind == "function"),
            None,
        )
        transfer = next(
            (s for s in result.symbols if s.name.endswith("transfer") and "safe" not in s.name.lower() and s.kind == "function"),
            None,
        )
        assert safe_transfer is not None
        assert transfer is not None

        call_edges = [
            e for e in edges
            if e.src == safe_transfer.id and e.dst == transfer.id and e.edge_type == "calls"
        ]
        assert len(call_edges) == 1, f"Expected 1 edge from safeTransfer→transfer, found {len(call_edges)}"

    def test_this_call_creates_edge(self, temp_repo: Path) -> None:
        """this.transfer() should create a calls edge to the local transfer function."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }

    function delegateTransfer(address to, uint256 amount) public returns (bool) {
        this.transfer(to, amount);
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)
        edges = result.edges

        delegate = next(
            (s for s in result.symbols if "delegateTransfer" in s.name and s.kind == "function"),
            None,
        )
        transfer = next(
            (s for s in result.symbols if s.name.endswith("transfer") and "delegate" not in s.name.lower() and s.kind == "function"),
            None,
        )
        assert delegate is not None
        assert transfer is not None

        call_edges = [
            e for e in edges
            if e.src == delegate.id and e.dst == transfer.id and e.edge_type == "calls"
        ]
        assert len(call_edges) == 1, f"Expected 1 edge from delegateTransfer→transfer, found {len(call_edges)}"


class TestSolidityMemberAccessCalls:
    """Tests for resolving dotted member access calls (e.g., IERC20(x).transfer())."""

    def test_interface_cast_call_creates_edge(self, temp_repo: Path) -> None:
        """IERC20(token).transfer() resolves to the local transfer function."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract Vault {
    function withdraw(address token, address to, uint256 amount) external {
        IERC20(token).transfer(to, amount);
    }

    function transfer(address to, uint256 amount) internal returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)
        edges = result.edges

        withdraw = next(
            (s for s in result.symbols if "withdraw" in s.name and s.kind == "function"),
            None,
        )
        assert withdraw is not None

        # Should resolve "IERC20(token).transfer" to a transfer function
        call_edges = [
            e for e in edges
            if e.src == withdraw.id and e.edge_type == "calls"
        ]
        # At minimum, the dotted call should resolve to a transfer symbol
        transfer_calls = [
            e for e in call_edges
            if any("transfer" in s.name for s in result.symbols if s.id == e.dst)
        ]
        assert len(transfer_calls) >= 1, (
            f"Expected at least 1 call edge from withdraw→transfer, found {len(transfer_calls)}"
        )


class TestSoliditySignatureExtraction:
    """Tests for Solidity function signature extraction."""

    def test_function_with_params(self, temp_repo: Path) -> None:
        """Extract signature from function with typed params."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "transfer" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "address to" in funcs[0].signature
        assert "uint256 amount" in funcs[0].signature

    def test_function_with_return_type(self, temp_repo: Path) -> None:
        """Extract signature with return type."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function getBalance() public view returns (uint256) {
        return 0;
    }
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "getBalance" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "returns" in funcs[0].signature

    def test_function_no_params(self, temp_repo: Path) -> None:
        """Extract signature from function with no params."""
        (temp_repo / "Token.sol").write_text("""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    function empty() public pure {}
}
""")

        result = analyze_solidity(temp_repo)
        funcs = [s for s in result.symbols if s.kind == "function" and "empty" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"
