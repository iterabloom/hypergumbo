# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Solidity member name resolves in the contract that uses it.

The invariant
-------------
An unqualified name inside contract ``C`` (a call ``helper()``, an
``emit Transfer(...)``) names ``C``'s own member, then a member ``C``
inherits, in the order Solidity's linearisation gives, and only then a
file-level name. It never names a member of a contract ``C`` is unrelated
to. A member reached through a receiver (``this.f()``, ``super.f()``,
``Base.f()``, ``b.f()`` with ``B b``, ``IERC20(x).f()``) resolves in the
scope the receiver names.

Why these tests exist
---------------------
The analyzer used to answer all of these from one table that holds a single
symbol per short name for the whole file (the last one declared). Two
contracts in one file that each declare ``helper`` therefore shared one
answer, and the calling contract's own member lost to whichever contract came
later in the file.

The tests assert which two symbols an edge joins, by name. They pin no id
string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_extended1.solidity import (
    analyze_solidity,
    is_solidity_tree_sitter_available,
)

pytestmark = pytest.mark.skipif(
    not is_solidity_tree_sitter_available(),
    reason="solidity tree-sitter grammar not available",
)


class _Graph:
    """One analysed repo, queried by symbol name."""

    def __init__(self, repo: Path) -> None:
        result = analyze_solidity(repo)
        self.edges: list[Edge] = result.edges
        self._name = {s.id: s.name for s in result.symbols}
        self._path = {s.id: s.path for s in result.symbols}
        self._kind = {s.id: s.kind for s in result.symbols}

    def _dst(self, edge: Edge) -> str:
        if edge.dst in self._name:
            return self._name[edge.dst]
        return "?" + str((edge.meta or {}).get("callee_name", edge.dst))

    def out(self, src: str, edge_type: str = "calls") -> list[str]:
        """Targets of ``src``'s edges of one type; an unresolved one as ``?name``."""
        return sorted(
            self._dst(e) for e in self.edges
            if e.edge_type == edge_type and self._name.get(e.src) == src
        )

    def edge(self, src: str, dst: str, edge_type: str = "calls") -> Edge:
        """The single edge of one type joining two named symbols."""
        found = [
            e for e in self.edges
            if e.edge_type == edge_type
            and self._name.get(e.src) == src and self._name.get(e.dst) == dst
        ]
        assert len(found) == 1, f"{src} -> {dst}: {len(found)} {edge_type} edges"
        return found[0]

    def path_of(self, edge: Edge) -> str:
        return self._path[edge.dst]

    def kind_of(self, edge: Edge) -> str:
        return self._kind[edge.dst]


def _write(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")


TWO_CONTRACTS = """
contract {first} {{
    event Transfer(uint a);
    function helper() internal {{}}
    function run() public {{
        helper();
        emit Transfer(1);
    }}
}}

contract {second} {{
    event Transfer(uint a);
    function helper() internal {{}}
    function run() public {{
        helper();
        emit Transfer(2);
    }}
}}
"""


class TestOwnMemberWins:
    """The filed defect: two contracts in one file, same member names."""

    @pytest.mark.parametrize("first,second", [("A", "B"), ("B", "A")])
    def test_call_binds_to_the_calling_contracts_member(
        self, tmp_path: Path, first: str, second: str,
    ) -> None:
        _write(tmp_path, "t.sol", TWO_CONTRACTS.format(first=first, second=second))
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["A.helper"]
        assert g.out("B.run") == ["B.helper"]

    @pytest.mark.parametrize("first,second", [("A", "B"), ("B", "A")])
    def test_emit_binds_to_the_calling_contracts_event(
        self, tmp_path: Path, first: str, second: str,
    ) -> None:
        _write(tmp_path, "t.sol", TWO_CONTRACTS.format(first=first, second=second))
        g = _Graph(tmp_path)
        assert g.out("A.run", "references") == ["A.Transfer"]
        assert g.out("B.run", "references") == ["B.Transfer"]
        emit = g.edge("A.run", "A.Transfer", "references")
        assert emit.meta == {"ref_construct": "event_emit"}

    def test_own_member_keeps_the_direct_call_evidence(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", TWO_CONTRACTS.format(first="A", second="B"))
        assert _Graph(tmp_path).edge("A.run", "A.helper").evidence_type == "ast_call_direct"

    def test_this_binds_to_the_calling_contracts_member(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function helper() public {}
    function run() public { this.helper(); }
}
contract B {
    function helper() public {}
}
""")
        assert _Graph(tmp_path).out("A.run") == ["A.helper"]

    def test_library_function_calls_its_own_sibling(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
library L {
    function inner() internal {}
    function outer() internal { inner(); }
}
library M {
    function inner() internal {}
}
""")
        assert _Graph(tmp_path).out("L.outer") == ["L.inner"]

    def test_a_later_overload_in_the_same_contract_is_the_one_bound(
        self, tmp_path: Path,
    ) -> None:
        """Overloads are not told apart by argument: one contract, last declared."""
        _write(tmp_path, "t.sol", """
contract A {
    function helper(uint a) internal {}
    function helper(address a) internal {}
    function run() public { helper(1); }
}
""")
        g = _Graph(tmp_path)
        targets = [e for e in g.edges if e.edge_type == "calls"]
        assert len(targets) == 1
        assert g.out("A.run") == ["A.helper"]

    def test_event_called_without_emit_still_binds_in_the_contract(
        self, tmp_path: Path,
    ) -> None:
        """Solidity before 0.4.21 raised an event by calling it."""
        _write(tmp_path, "t.sol", """
contract A {
    event Sent(uint a);
    function run() public { Sent(1); }
}
contract B {
    event Sent(uint a);
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["A.Sent"]


class TestInheritedMember:
    def test_inherited_call_binds_to_the_base_not_an_unrelated_contract(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract Base {
    event Seen(uint a);
    function inh() internal {}
}
contract A is Base {
    function run() public { inh(); emit Seen(1); }
}
contract B {
    event Seen(uint a);
    function inh() internal {}
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["Base.inh"]
        assert g.out("A.run", "references") == ["Base.Seen"]
        inherited = g.edge("A.run", "Base.inh")
        assert inherited.evidence_type == "ast_call_inherited"
        assert inherited.confidence == pytest.approx(0.90)

    def test_base_declared_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "far.sol", """
contract FarBase {
    event FarSeen(uint a);
    function far() internal {}
}
""")
        _write(tmp_path, "near.sol", """
import "./far.sol";
contract Kid is FarBase {
    function run() public { far(); this.far(); emit FarSeen(1); }
}
contract Decoy {
    event FarSeen(uint a);
    function far() internal {}
}
""")
        g = _Graph(tmp_path)
        assert g.out("Kid.run") == ["FarBase.far", "FarBase.far"]
        assert g.out("Kid.run", "references") == ["FarBase.FarSeen"]

    def test_member_two_levels_up(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Root { function deep() internal {} }
contract Mid is Root {}
contract Leaf is Mid {
    function run() public { deep(); }
}
contract Other { function deep() internal {} }
""")
        assert _Graph(tmp_path).out("Leaf.run") == ["Root.deep"]

    def test_base_given_with_constructor_arguments(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Base {
    constructor(uint a) {}
    function inh() internal {}
}
contract A is Base(1) {
    function run() public { inh(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Base.inh"]

    def test_own_member_shadows_the_inherited_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Base { function who() public virtual {} }
contract A is Base {
    function who() public override {}
    function run() public { who(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["A.who"]


class TestLinearisation:
    """``contract C is X, Y``: the base named last is searched first."""

    DIAMOND = """
contract Root { function who() public virtual {} }
contract Left is Root { function who() public virtual override {} }
contract Right is Root { function who() public virtual override {} }
"""

    def test_rightmost_base_wins(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", self.DIAMOND + """
contract Bottom is Left, Right {
    function run() public { who(); }
}
""")
        assert _Graph(tmp_path).out("Bottom.run") == ["Right.who"]

    def test_order_of_the_is_list_decides(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", self.DIAMOND + """
contract Bottom is Right, Left {
    function run() public { who(); }
}
""")
        assert _Graph(tmp_path).out("Bottom.run") == ["Left.who"]

    def test_a_shared_root_is_searched_after_every_branch(self, tmp_path: Path) -> None:
        """Depth-first search through ``Left`` would reach ``Root.who`` first.

        The linearisation of ``Bottom is Right, Left`` is Bottom, Left, Right,
        Root: ``Left`` has no ``who``, so ``Right.who`` is the member.
        """
        _write(tmp_path, "t.sol", """
contract Root { function who() public virtual {} }
contract Left is Root {}
contract Right is Root { function who() public virtual override {} }
contract Bottom is Right, Left {
    function run() public { who(); }
}
""")
        assert _Graph(tmp_path).out("Bottom.run") == ["Right.who"]

    def test_super_skips_the_calling_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", self.DIAMOND + """
contract Bottom is Left, Right {
    function who() public override(Left, Right) { super.who(); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("Bottom.who") == ["Right.who"]
        to_base = g.edge("Bottom.who", "Right.who")
        assert to_base.evidence_type == "ast_call_inherited"
        assert to_base.confidence == pytest.approx(0.90)

    def test_super_with_no_base_member_is_unresolved(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function who() public {}
    function run() public { super.who(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?who"]

    def test_inheritance_cycle_terminates(self, tmp_path: Path) -> None:
        """Not valid Solidity; the analyzer must still return."""
        _write(tmp_path, "t.sol", """
contract A is B {
    function run() public { gone(); mine(); }
}
contract B is A {
    function mine() internal {}
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?gone", "B.mine"]

    def test_inconsistent_hierarchy_still_resolves(self, tmp_path: Path) -> None:
        """``is X, Y`` against ``is Y, X`` has no linearisation; no crash."""
        _write(tmp_path, "t.sol", """
contract X { function who() public virtual {} }
contract Y { function who() public virtual {} }
contract P is X, Y {}
contract Q is Y, X {}
contract Z is P, Q {
    function run() public { who(); }
}
""")
        (target,) = _Graph(tmp_path).out("Z.run")
        assert target in ("X.who", "Y.who")


class TestFileLevelNames:
    def test_free_function_in_the_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
function free() pure {}
contract A {
    function run() public { free(); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["free"]
        assert g.edge("A.run", "free").evidence_type == "ast_call_direct"

    def test_contract_member_shadows_a_free_function(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
function shared() pure {}
contract Base { function shared() internal {} }
contract A is Base {
    function run() public { shared(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Base.shared"]

    def test_free_function_calls_a_free_function(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
function leaf() pure {}
function top() pure { leaf(); }
contract A { function leaf() internal {} }
""")
        assert _Graph(tmp_path).out("top") == ["leaf"]

    def test_free_function_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib.sol", "function distant() pure {}\n")
        _write(tmp_path, "t.sol", """
import "./lib.sol";
contract A {
    function run() public { distant(); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["distant"]
        assert g.edge("A.run", "distant").evidence_type == "ast_call"

    def test_bare_call_never_reaches_an_unrelated_contract_in_the_file(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function run() public { helper(); }
}
contract B {
    function helper() internal {}
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?helper"]

    def test_bare_call_never_reaches_an_unrelated_contract_in_another_file(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "b.sol", "contract B { function helper() internal {} }\n")
        _write(tmp_path, "t.sol", """
contract A {
    function run() public { helper(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?helper"]

    def test_free_function_never_reaches_a_contract_member(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
function top() pure { helper(); }
contract B { function helper() internal {} }
""")
        assert _Graph(tmp_path).out("top") == ["?helper"]

    @pytest.mark.parametrize("where", ["same", "other"])
    def test_conversion_to_a_contract_type_names_the_type(
        self, tmp_path: Path, where: str,
    ) -> None:
        decl = "interface IThing { function poke() external; }\n"
        if where == "other":
            _write(tmp_path, "i.sol", decl)
            decl = 'import "./i.sol";\n'
        _write(tmp_path, "t.sol", decl + """
contract A {
    function run(address a) public { IThing(a); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["IThing"]
        assert g.kind_of(g.edge("A.run", "IThing")) == "interface"

    def test_file_level_event(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
event Loose(uint a);
contract A {
    function run() public { emit Loose(1); }
}
""")
        assert _Graph(tmp_path).out("A.run", "references") == ["Loose"]

    def test_file_level_event_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "e.sol", "event Loose(uint a);\n")
        _write(tmp_path, "t.sol", """
import "./e.sol";
contract A {
    function run() public { emit Loose(1); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run", "references") == ["Loose"]
        assert g.path_of(g.edge("A.run", "Loose", "references")) == "e.sol"

    def test_emit_never_reaches_an_unrelated_contracts_event(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function run() public { emit Gone(1); }
}
contract B {
    event Gone(uint a);
    function Loose() public {}
}
contract C {
    function run() public { emit Loose(1); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run", "references") == []
        assert g.out("C.run", "references") == []


class TestQualifiedByContract:
    def test_explicit_base_call(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Base { function who() public virtual {} }
contract A is Base {
    function who() public override { Base.who(); }
}
contract B { function who() public {} }
""")
        g = _Graph(tmp_path)
        assert g.out("A.who") == ["Base.who"]
        edge = g.edge("A.who", "Base.who")
        assert edge.evidence_type == "ast_call_direct"
        assert edge.meta == {"receiver": "qualified"}

    def test_library_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib.sol", """
library Maths { function twice(uint a) internal pure returns (uint) { return a * 2; } }
""")
        _write(tmp_path, "t.sol", """
import "./lib.sol";
contract A {
    function twice(uint a) internal pure returns (uint) { return a; }
    function run() public { Maths.twice(1); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["Maths.twice"]

    def test_member_the_named_contract_inherits(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Root { function deep() internal {} }
contract Base is Root {}
contract A is Base {
    function deep2() internal {}
    function run() public { Base.deep(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Root.deep"]

    def test_named_contract_without_the_member_is_unresolved(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract Base {}
contract A is Base {
    function gone() internal {}
    function run() public { Base.gone(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?Base.gone"]

    def test_emit_qualified_by_a_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
interface IRoot { event Seen(uint a); }
interface IThing is IRoot {}
contract A {
    event Seen(uint a);
    function run() public { emit IThing.Seen(1); emit IThing.Gone(1); }
}
""")
        assert _Graph(tmp_path).out("A.run", "references") == ["IRoot.Seen"]

    def test_emit_through_something_that_is_no_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    event Seen(uint a);
    function run() public { emit nothing.Seen(1); }
}
""")
        assert _Graph(tmp_path).out("A.run", "references") == []


class TestTypedReceiver:
    CALLEES = """
contract B { function other() public {} function pay() public payable {} }
contract C { function other() public {} function pay() public payable {} }
"""

    def test_parameter_type_names_the_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function run(B b) public { b.other(); }
}
""" + self.CALLEES)
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["B.other"]
        assert g.edge("A.run", "B.other").evidence_type == "ast_call_type_inferred"

    def test_local_variable_type_names_the_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function run(address a) public {
        B loc = B(a);
        (loc).other();
    }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["B", "B.other"]

    def test_state_variable_type_names_the_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    B internal held;
    function run() public { held.other(); }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["B.other"]

    def test_inherited_state_variable_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "base.sol", self.CALLEES + """
contract Holder { B internal held; }
""")
        _write(tmp_path, "t.sol", """
import "./base.sol";
contract A is Holder {
    function other() public {}
    function run() public { held.other(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["B.other"]

    def test_local_variable_shadows_a_state_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    B internal held;
    function run(C held) public { held.other(); }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["C.other"]

    def test_variable_shadows_a_contract_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function run(C B) public { B.other(); }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["C.other"]

    def test_conversion_names_the_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function other() public {}
    function run(address a) public { C(a).other(); }
}
""" + self.CALLEES)
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["C", "C.other"]
        assert g.edge("A.run", "C.other").evidence_type == "ast_call_type_inferred"

    def test_conversion_to_a_type_in_another_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "i.sol", """
interface IToken { function transfer(address to, uint256 amount) external returns (bool); }
""")
        _write(tmp_path, "t.sol", """
import "./i.sol";
contract Vault {
    function transfer(address to, uint256 amount) internal returns (bool) { return true; }
    function run(address token, address to) external { IToken(token).transfer(to, 1); }
}
""")
        assert _Graph(tmp_path).out("Vault.run") == ["IToken", "IToken.transfer"]

    def test_member_the_receiver_type_inherits(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Root { function deep() public {} }
contract B is Root {}
contract A {
    function deep() public {}
    function run(B b) public { b.deep(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Root.deep"]

    def test_call_options_do_not_hide_the_callee(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function pay() public payable {}
    function run(B b) public payable {
        b.pay{value: 1}();
        this.pay{value: 1}();
    }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["A.pay", "B.pay"]

    def test_type_without_the_member_is_unresolved(self, tmp_path: Path) -> None:
        """The caller's own same-named member is not the callee."""
        _write(tmp_path, "t.sol", """
contract A {
    function absent() public {}
    function run(B b) public { b.absent(); }
}
""" + self.CALLEES)
        assert _Graph(tmp_path).out("A.run") == ["?b.absent"]

    def test_type_declared_nowhere_is_unresolved(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function transfer(address to, uint256 amount) public {}
    function run(IElsewhere token, address to) public { token.transfer(to, 1); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?token.transfer"]

    def test_address_member_is_not_a_contract_member(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function transfer(uint256 amount) public {}
    function run(address payable to) public {
        to.transfer(1);
        payable(msg.sender).transfer(1);
        address(this).transfer(1);
    }
}
""")
        assert _Graph(tmp_path).out("A.run") == [
            "?address(this).transfer", "?payable(msg.sender).transfer", "?to.transfer",
        ]

    def test_builtin_namespace_is_not_a_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function encode(uint a) public {}
    function run() public { abi.encode(1); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?abi.encode"]

    def test_library_attached_to_a_contract_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
interface IToken { function transfer(address to, uint256 amount) external; }
library SafeToken {
    function safeTransfer(IToken token, address to, uint256 amount) internal {}
}
contract A {
    using SafeToken for IToken;
    function run(IToken token, address to) public { token.safeTransfer(to, 1); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["SafeToken.safeTransfer"]
        assert g.edge("A.run", "SafeToken.safeTransfer").confidence == pytest.approx(0.60)


class TestUntypedReceiver:
    def test_one_candidate_in_the_file_is_bound_at_low_confidence(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract B { function other() public {} }
contract A {
    function make() internal returns (B) { return B(address(0)); }
    function run() public { make().other(); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.run") == ["A.make", "B.other"]
        edge = g.edge("A.run", "B.other")
        assert edge.evidence_type == "ast_call"
        assert edge.confidence == pytest.approx(0.60)

    def test_attached_library_is_tried_first(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
library Maths { function twice(uint a) internal pure returns (uint) { return a * 2; } }
contract B { function twice() public {} }
contract A {
    using Maths for uint;
    function run(uint[] memory xs) public { xs[0].twice(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Maths.twice"]

    def test_two_candidates_in_the_file_are_not_guessed_between(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract B { function other() public {} }
contract C { function other() public {} }
contract A {
    function make() internal returns (B) { return B(address(0)); }
    function run() public { make().other(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?make().other", "A.make"]

    def test_free_function_in_another_file_is_the_last_candidate(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "lib.sol", "function widen(uint a) pure returns (uint) { return a; }\n")
        _write(tmp_path, "t.sol", """
import "./lib.sol";
contract A {
    function run(uint[] memory xs) public { xs[0].widen(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["widen"]

    def test_a_contract_named_like_the_member_is_no_candidate(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
contract widen {}
contract A {
    function run(uint[] memory xs) public { xs[0].widen(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?xs[0].widen"]

    def test_two_attached_libraries_with_the_member_are_not_guessed_between(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", """
library MathA { function add(uint a, uint b) internal pure returns (uint) { return a + b; } }
library MathB { function add(uint a, uint b) internal pure returns (uint) { return a + b; } }
contract A {
    using MathA for uint;
    using MathB for uint;
    function run(uint x) public { x.add(1); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?x.add"]

    def test_callee_that_is_no_name_is_unresolved(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract A {
    function() internal[] hooks;
    function run() public { hooks[0](); new A(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?hooks[0]", "?new A"]


class TestAttachedLibrary:
    """``using L for T``: a library function is a member of its first parameter's type."""

    LIBRARIES = """
library Bytes {
    function slice(bytes memory buffer, uint256 start) internal pure returns (bytes memory) {}
    function slice(bytes memory buffer, uint256 start, uint256 end) internal pure returns (bytes memory) {}
    function none() internal pure {}
}
library Memory {
    type Slice is bytes32;
    function asSlice(bytes memory self) internal pure returns (Slice) {}
    function slice(Slice self, uint256 offset) internal pure returns (Slice) {}
    function none() internal pure {}
}
"""

    def test_receiver_type_picks_the_library(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", self.LIBRARIES + """
contract A {
    using Bytes for *;
    using Memory for *;
    function onBytes(bytes memory input) public { input.slice(1); }
    function onSlice(Memory.Slice piece) public { piece.slice(1); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("A.onBytes") == ["Bytes.slice"]
        assert g.out("A.onSlice") == ["Memory.slice"]

    def test_receiver_of_unknown_type_leaves_two_libraries_tied(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", self.LIBRARIES + """
contract A {
    using Bytes for *;
    using Memory for *;
    function run(bytes memory input) public { input.asSlice().slice(1); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?input.asSlice().slice", "Memory.asSlice"]

    def test_no_first_parameter_of_the_receiver_type_leaves_the_tie(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "t.sol", self.LIBRARIES + """
contract A {
    using Bytes for *;
    using Memory for *;
    function run(uint x) public { x.slice(1); x.none(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?x.none", "?x.slice"]

    def test_one_library_is_bound_whatever_its_first_parameter(
        self, tmp_path: Path,
    ) -> None:
        """A derived contract passed where the library names its base."""
        _write(tmp_path, "t.sol", """
interface IToken {}
contract Token is IToken {}
library SafeToken { function safeSend(IToken token) internal {} }
contract A {
    using SafeToken for IToken;
    function run(Token token) public { token.safeSend(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["SafeToken.safeSend"]

    def test_integer_alias_is_one_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
library Wide { function grow(uint256 a) internal pure {} }
library Narrow { function grow(uint8 a) internal pure {} }
contract A {
    using Wide for uint;
    using Narrow for uint8;
    function run(uint x) public { x.grow(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["Wide.grow"]


class TestOperatorBeforeAMemberCall:
    """The grammar parses ``a || x.f()`` as ``(a || x).f()``; the receiver is ``x``."""

    def test_super_after_an_operator(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Base { function ok(uint a) internal virtual returns (bool) { return true; } }
contract A is Base {
    function ok(uint a) internal override returns (bool) {
        return a == 0 || super.ok(a);
    }
}
contract B { function ok(uint a) internal returns (bool) { return false; } }
""")
        assert _Graph(tmp_path).out("A.ok") == ["Base.ok"]

    def test_typed_receiver_after_operators(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract T { function yes() public returns (bool) {} function num() public returns (uint) {} }
contract U { function yes() public returns (bool) {} function num() public returns (uint) {} }
contract A {
    function run(T t, bool flag) public returns (uint) {
        if (flag && !t.yes()) { return 1 + t.num(); }
        return flag ? 0 : t.num();
    }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["T.num", "T.num", "T.yes"]

    def test_parenthesised_operator_is_the_receiver_itself(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract T { function num() public returns (uint) {} }
contract U { function num() public returns (uint) {} }
contract A {
    function run(T t, T u, bool flag) public { (flag ? t : u).num(); }
}
""")
        assert _Graph(tmp_path).out("A.run") == ["?(flag ? t : u).num"]


class TestContractNamesAreNotMemberNames:
    def test_old_style_constructor_does_not_take_the_inheritance_edge(
        self, tmp_path: Path,
    ) -> None:
        """Before Solidity 0.5 a constructor was a function named as its contract."""
        _write(tmp_path, "t.sol", """
contract Parent { function Parent() public {} }
contract Token is Parent {
    function Token() public {}
}
""")
        g = _Graph(tmp_path)
        edge = g.edge("Token", "Parent", "inherits")
        assert g.kind_of(edge) == "contract"
        assert g.out("Token.Token", "inherits") == []

    def test_base_that_is_not_in_the_tree_gives_no_edge(self, tmp_path: Path) -> None:
        _write(tmp_path, "t.sol", """
contract Token is Missing, Pkg.Qualified {
    function Missing() public {}
    function run() public { Missing(); }
}
""")
        g = _Graph(tmp_path)
        assert g.out("Token", "inherits") == []
        assert g.out("Token.run") == ["Token.Missing"]
