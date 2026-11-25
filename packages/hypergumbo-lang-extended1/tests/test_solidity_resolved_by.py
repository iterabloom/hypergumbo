# SPDX-License-Identifier: AGPL-3.0-or-later
"""``meta["resolved_by"]`` on a Solidity call: what scoped the choice of its target.

Audit 0030 (WI-hirol): every resolved call edge carries one declared value
naming the evidence that chose its target. ``_MemberResolver`` answers each
call from the scope the site names, and each ``_Bound`` it returns says which
scope that was. One fixture per route; each asserts the edge exists before it
reads the mark.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.solidity import analyze_solidity


def _marks(repo: Path, files: dict[str, str]) -> dict[tuple[str, str], str]:
    """``{(caller name, callee name): resolved_by}`` over the resolved calls."""
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    result = analyze_solidity(repo)
    name = {s.id: s.name for s in result.symbols}
    return {
        (name[e.src], name[e.dst]): e.meta["resolved_by"]
        for e in result.edges
        if e.edge_type == "calls" and e.src in name and e.dst in name
    }


def test_enclosing_contract_scope_is_own_type(tmp_path: Path) -> None:
    marks = _marks(tmp_path, {"t.sol": """
contract Base {
    function who() public virtual {}
    function inherited() internal {}
}
contract A is Base {
    function helper() internal {}
    function who() public override {
        helper();
        inherited();
        this.helper();
        super.who();
    }
}
"""})
    assert marks[("A.who", "A.helper")] == "own_type"
    assert marks[("A.who", "Base.inherited")] == "own_type"
    assert marks[("A.who", "Base.who")] == "own_type"


def test_file_level_name_is_same_file_or_name(tmp_path: Path) -> None:
    marks = _marks(tmp_path, {
        "lib.sol": "function far(uint a) pure returns (uint) { return a; }\n",
        "t.sol": """
import "./lib.sol";
function near(uint a) pure returns (uint) { return a; }
contract A {
    function run(uint x) public { near(x); far(x); }
}
""",
    })
    assert marks[("A.run", "near")] == "same_file"
    assert marks[("A.run", "far")] == "name"


def test_named_contract_is_qualified(tmp_path: Path) -> None:
    marks = _marks(tmp_path, {"t.sol": """
library Maths { function twice(uint a) internal pure returns (uint) { return a * 2; } }
contract A {
    function run(uint x) public { Maths.twice(x); }
}
"""})
    assert marks[("A.run", "Maths.twice")] == "qualified"


def test_declared_or_converted_receiver_is_receiver_type(tmp_path: Path) -> None:
    marks = _marks(tmp_path, {"t.sol": """
contract B { function ping() public {} function pong() public {} function pang() public {} }
contract A {
    B stored;
    function run(address at) public {
        B local = B(at);
        local.ping();
        stored.pong();
        B(at).pang();
    }
}
"""})
    assert marks[("A.run", "B.ping")] == "receiver_type"
    assert marks[("A.run", "B.pong")] == "receiver_type"
    assert marks[("A.run", "B.pang")] == "receiver_type"


def test_attached_library_is_import(tmp_path: Path) -> None:
    """``using L for T`` (closest fit, see the report): typed, untyped and
    elementary-conversion receivers all search the attached libraries."""
    marks = _marks(tmp_path, {"t.sol": """
interface IToken { function transfer(address to, uint256 amount) external; }
library SafeToken {
    function safeTransfer(IToken token, address to, uint256 amount) internal {}
}
library Maths {
    function twice(uint a) internal pure returns (uint) { return a * 2; }
    function thrice(uint a) internal pure returns (uint) { return a * 3; }
}
contract A {
    using SafeToken for IToken;
    using Maths for uint;
    function run(IToken token, address to, uint[] memory xs) public {
        token.safeTransfer(to, 1);
        xs[0].twice();
        uint(7).thrice();
    }
}
"""})
    assert marks[("A.run", "SafeToken.safeTransfer")] == "import"
    assert marks[("A.run", "Maths.twice")] == "import"
    assert marks[("A.run", "Maths.thrice")] == "import"


def test_untyped_receiver_routes(tmp_path: Path) -> None:
    """An untyped receiver: one contract of this file declaring the member,
    else a file-level function (this file's, else the run's)."""
    marks = _marks(tmp_path, {
        "lib.sol": "function widen(uint a) pure returns (uint) { return a; }\n",
        "t.sol": """
import "./lib.sol";
function narrow(uint a) pure returns (uint) { return a; }
contract B { function other() public {} }
contract A {
    function make() internal returns (B) { return B(address(0)); }
    function run(uint[] memory xs) public {
        make().other();
        xs[0].narrow();
        xs[1].widen();
    }
}
""",
    })
    assert marks[("A.run", "B.other")] == "same_file"
    assert marks[("A.run", "narrow")] == "same_file"
    assert marks[("A.run", "widen")] == "name"
