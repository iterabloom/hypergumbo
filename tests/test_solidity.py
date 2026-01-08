"""Tests for Solidity analysis pass."""
from pathlib import Path

import pytest

from hypergumbo.analyze.solidity import (
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

    def test_returns_skipped_when_unavailable(self, temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns skipped result when tree-sitter not available."""
        import hypergumbo.analyze.solidity as sol_module
        monkeypatch.setattr(sol_module, "is_solidity_tree_sitter_available", lambda: False)

        (temp_repo / "Token.sol").write_text("contract Token {}")

        result = analyze_solidity(temp_repo)

        assert result.skipped
        assert "tree-sitter" in result.skip_reason.lower()

    def test_tree_sitter_check_tree_sitter_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns False when tree_sitter module is missing."""
        import importlib.util
        import hypergumbo.analyze.solidity as sol_module

        original_find_spec = importlib.util.find_spec

        def mock_find_spec(name: str):
            if name == "tree_sitter":
                return None
            return original_find_spec(name)

        monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

        # Call the actual function - should return False
        result = sol_module.is_solidity_tree_sitter_available()
        assert result is False

    def test_tree_sitter_check_solidity_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns False when tree_sitter_solidity module is missing."""
        import importlib.util
        import hypergumbo.analyze.solidity as sol_module

        original_find_spec = importlib.util.find_spec

        def mock_find_spec(name: str):
            if name == "tree_sitter_solidity":
                return None
            return original_find_spec(name)

        monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

        # Call the actual function - should return False
        result = sol_module.is_solidity_tree_sitter_available()
        assert result is False


class TestSolidityEdgeCases:
    """Tests for edge cases and error handling."""

    def test_handles_unreadable_file_symbols(self, temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gracefully handles files that cannot be read during symbol extraction."""
        from hypergumbo.analyze.solidity import _extract_symbols_from_file
        from hypergumbo.ir import AnalysisRun
        import tree_sitter
        import tree_sitter_solidity
        import warnings

        (temp_repo / "Token.sol").write_text("contract Token {}")

        # Create parser
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            language = tree_sitter.Language(tree_sitter_solidity.language())
            parser = tree_sitter.Parser(language)

        run = AnalysisRun.create(pass_id="test", version="0.1.0")

        # Create a fake file path that doesn't exist
        fake_path = temp_repo / "nonexistent.sol"

        # This should trigger OSError and return empty FileAnalysis
        result = _extract_symbols_from_file(fake_path, parser, run)

        assert result.symbols == []
        assert result.symbol_by_name == {}

    def test_handles_unreadable_file_edges(self, temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gracefully handles files that cannot be read during edge extraction."""
        from hypergumbo.analyze.solidity import _extract_edges_from_file
        from hypergumbo.ir import AnalysisRun
        import tree_sitter
        import tree_sitter_solidity
        import warnings

        (temp_repo / "Token.sol").write_text("contract Token {}")

        # Create parser
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            language = tree_sitter.Language(tree_sitter_solidity.language())
            parser = tree_sitter.Parser(language)

        run = AnalysisRun.create(pass_id="test", version="0.1.0")

        # Create a fake file path that doesn't exist
        fake_path = temp_repo / "nonexistent.sol"

        # This should trigger OSError and return empty list
        result = _extract_edges_from_file(fake_path, parser, {}, {}, run)

        assert result == []

    def test_find_child_by_type_returns_none(self, temp_repo: Path) -> None:
        """_find_child_by_type returns None when child type not found."""
        from hypergumbo.analyze.solidity import _find_child_by_type
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
        result = _find_child_by_type(tree.root_node, "nonexistent_type")
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
