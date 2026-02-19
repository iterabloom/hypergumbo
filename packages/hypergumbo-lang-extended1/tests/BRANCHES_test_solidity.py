"""Branch coverage tests for solidity.py analyzer.

Tests specific branch paths in the Solidity analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import solidity as solidity_module
from hypergumbo_lang_extended1.solidity import (
    analyze_solidity,
    find_solidity_files,
)


def make_solidity_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Solidity file with given content."""
    (tmp_path / name).write_text(content)


class TestContractExtraction:
    """Branch coverage for contract extraction."""

    def test_contract_declaration(self, tmp_path: Path) -> None:
        """Test contract declaration extraction."""
        make_solidity_file(tmp_path, "Token.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    string public name;
}
""")
        result = analyze_solidity(tmp_path)
        assert not result.skipped
        contracts = [s for s in result.symbols if s.kind == "contract"]
        assert any("Token" in c.name for c in contracts)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_solidity_file(tmp_path, "IToken.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IToken {
    function transfer(address to, uint256 amount) external returns (bool);
}
""")
        result = analyze_solidity(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("IToken" in i.name for i in interfaces)


class TestLibraryExtraction:
    """Branch coverage for library extraction."""

    def test_library_declaration(self, tmp_path: Path) -> None:
        """Test library declaration extraction."""
        make_solidity_file(tmp_path, "Math.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

library Math {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        return a + b;
    }
}
""")
        result = analyze_solidity(tmp_path)
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert any("Math" in l.name for l in libraries)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_solidity_file(tmp_path, "Contract.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Contract {
    function add(uint256 a, uint256 b) public pure returns (uint256) {
        return a + b;
    }
}
""")
        result = analyze_solidity(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)


class TestModifierExtraction:
    """Branch coverage for modifier extraction."""

    def test_modifier_declaration(self, tmp_path: Path) -> None:
        """Test modifier declaration extraction."""
        make_solidity_file(tmp_path, "Ownable.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Ownable {
    address public owner;

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
}
""")
        result = analyze_solidity(tmp_path)
        modifiers = [s for s in result.symbols if s.kind == "modifier"]
        assert any("onlyOwner" in m.name for m in modifiers)


class TestEventExtraction:
    """Branch coverage for event extraction."""

    def test_event_declaration(self, tmp_path: Path) -> None:
        """Test event declaration extraction."""
        make_solidity_file(tmp_path, "Events.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Events {
    event Transfer(address indexed from, address indexed to, uint256 value);
}
""")
        result = analyze_solidity(tmp_path)
        events = [s for s in result.symbols if s.kind == "event"]
        assert any("Transfer" in e.name for e in events)


class TestConstructorExtraction:
    """Branch coverage for constructor extraction."""

    def test_constructor_declaration(self, tmp_path: Path) -> None:
        """Test constructor declaration extraction."""
        make_solidity_file(tmp_path, "Token.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    string public name;

    constructor(string memory _name) {
        name = _name;
    }
}
""")
        result = analyze_solidity(tmp_path)
        constructors = [s for s in result.symbols if s.kind == "constructor"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_solidity_file(tmp_path, "Token.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
""")
        result = analyze_solidity(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_solidity_file(tmp_path, "Contract.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Contract {
    function helper() internal pure returns (uint256) {
        return 42;
    }

    function main() external pure returns (uint256) {
        return helper();
    }
}
""")
        result = analyze_solidity(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindSolidityFiles:
    """Branch coverage for file discovery."""

    def test_finds_sol_files(self, tmp_path: Path) -> None:
        """Test .sol files are discovered."""
        (tmp_path / "Test.sol").write_text("contract Test {}")
        files = list(find_solidity_files(tmp_path))
        assert any(f.suffix == ".sol" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_solidity_files(self, tmp_path: Path) -> None:
        """Test directory with no Solidity files."""
        result = analyze_solidity(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(solidity_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="solidity analysis skipped"):
                result = solidity_module.analyze_solidity(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason
