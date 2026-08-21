# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Solidity function declared inside a contract is a method (INV-lapas).

The defect
----------
``solidity.py`` emitted ``kind="function"`` for every function definition,
whether it was declared inside a ``contract`` / ``interface`` / ``library``
body or at file scope. Every other OO analyzer in the fleet distinguishes
the two, and the consumers are keyed on that distinction — most visibly
``link_type_hierarchy``, which indexes dispatch candidates with
``if sym.kind != "method": continue``.

Measured on openzeppelin-contracts before the fix:

* **2,025 members of type-like containers, 100% ``kind="function"``, zero
  ``kind="method"``** — the only 100% cell in the corpus (ruby 814/0,
  kotlin 1140/0, java 1040/0, python 634/0 the other way).
* 516 ``inherits`` edges over 164 distinct parents, **none of which had a
  single ``method``-kind member**, so the parent-method lookup returned
  empty for every one of them and the repo produced **zero** Solidity
  ``dispatches_to`` edges — against 545 ``overrides`` edges, i.e. the
  repository is densely virtual and none of it was visible.

Scope of the change
-------------------
Containment decides the kind, exactly as it does for every other analyzer:
a function with an enclosing contract / interface / library is a
``method``, a file-scope function (legal since Solidity 0.7) stays a
``function``. ``constructor``, ``modifier`` and ``event`` keep their own
kinds — they are distinct constructs, not functions with a different
receiver.

Identity impact, deliberate and disclosed
------------------------------------------
``kind`` is a slot in both ``Symbol.id`` and the typed ``stable_id``, so
the ids of contract members CHANGE. This is a producer correction, not a
scheme change — the formula is untouched, the input is now right — but the
values are not comparable across the boundary. The tests below pin the
containment rule rather than any particular id string, so they do not have
to be rewritten the next time the formula moves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_extended1.solidity import (
    analyze_solidity,
    is_solidity_tree_sitter_available,
)

pytestmark = pytest.mark.skipif(
    not is_solidity_tree_sitter_available(),
    reason="solidity tree-sitter grammar not available",
)


def _symbols(repo: Path) -> dict[str, str]:
    """Return ``{symbol_name: kind}`` for one analysed repo."""
    result = analyze_solidity(repo)
    return {s.name: s.kind for s in result.symbols}


class TestContainmentDecidesTheKind:
    def test_contract_member_is_a_method(self, tmp_path: Path) -> None:
        (tmp_path / "T.sol").write_text(
            "contract Token {\n"
            "    function transfer(address to) public returns (bool) {\n"
            "        return true;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        assert _symbols(tmp_path)["Token.transfer"] == "method"

    def test_interface_member_is_a_method(self, tmp_path: Path) -> None:
        (tmp_path / "I.sol").write_text(
            "interface IShape {\n"
            "    function area() external returns (uint);\n"
            "}\n",
            encoding="utf-8",
        )
        assert _symbols(tmp_path)["IShape.area"] == "method"

    def test_library_member_is_a_method(self, tmp_path: Path) -> None:
        (tmp_path / "L.sol").write_text(
            "library SafeMath {\n"
            "    function add(uint a, uint b) internal pure returns (uint) {\n"
            "        return a + b;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        assert _symbols(tmp_path)["SafeMath.add"] == "method"

    def test_file_scope_function_stays_a_function(self, tmp_path: Path) -> None:
        """THE DISCRIMINATING CONTROL.

        Solidity 0.7+ allows free functions at file scope. If this ever
        returns ``method``, the rule has stopped reading containment and has
        become an unconditional rename — which would be a different, wrong
        change that the other three tests could not distinguish from the
        right one.
        """
        (tmp_path / "F.sol").write_text(
            "function helper(uint a) pure returns (uint) {\n"
            "    return a + 1;\n"
            "}\n",
            encoding="utf-8",
        )
        assert _symbols(tmp_path)["helper"] == "function"

    def test_sibling_construct_kinds_are_untouched(self, tmp_path: Path) -> None:
        """``constructor`` / ``modifier`` / ``event`` are distinct constructs.

        They are not "functions with a receiver", so the containment rule
        must not sweep them up.
        """
        (tmp_path / "C.sol").write_text(
            "contract Vault {\n"
            "    event Deposit(address who);\n"
            "    modifier onlyOwner() { _; }\n"
            "    constructor() { }\n"
            "    function deposit() public onlyOwner { }\n"
            "}\n",
            encoding="utf-8",
        )
        kinds = _symbols(tmp_path)
        assert kinds["Vault.deposit"] == "method"
        assert kinds["Vault.constructor"] == "constructor"
        assert kinds["Vault.onlyOwner"] == "modifier"
        assert kinds["Vault.Deposit"] == "event"

    def test_container_kinds_are_untouched(self, tmp_path: Path) -> None:
        (tmp_path / "M.sol").write_text(
            "contract A { }\ninterface B { }\nlibrary C { }\n", encoding="utf-8",
        )
        kinds = _symbols(tmp_path)
        assert kinds["A"] == "contract"
        assert kinds["B"] == "interface"
        assert kinds["C"] == "library"


class TestTheEdgesThatKeyOnTheKind:
    """The four in-file consumers of the old literal must still work.

    ``solidity.py`` held FOUR separate ``kind == "function"`` checks — the
    enclosing-symbol span index, the ``using X for Y`` library-call
    resolver, and both halves of the ``overrides`` walker. A rename that
    missed any of them would silently drop edges, and openzeppelin-contracts
    carries 545 ``overrides`` edges to lose.
    """

    def test_overrides_edges_survive(self, tmp_path: Path) -> None:
        (tmp_path / "O.sol").write_text(
            "contract Base {\n"
            "    function ping() public virtual returns (uint) { return 1; }\n"
            "}\n"
            "contract Child is Base {\n"
            "    function ping() public override returns (uint) { return 2; }\n"
            "}\n",
            encoding="utf-8",
        )
        result = analyze_solidity(tmp_path)
        overrides = [e for e in result.edges if e.edge_type == "overrides"]
        assert overrides, (
            "the overrides walker stopped finding contract functions — it "
            "keys on the member kind, and 545 such edges exist on "
            "openzeppelin-contracts alone"
        )

    def test_using_for_library_call_still_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "U.sol").write_text(
            "library SafeMath {\n"
            "    function add(uint a, uint b) internal pure returns (uint) {\n"
            "        return a + b;\n"
            "    }\n"
            "}\n"
            "contract Calc {\n"
            "    using SafeMath for uint;\n"
            "    function run(uint x) public pure returns (uint) {\n"
            "        return x.add(1);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        result = analyze_solidity(tmp_path)
        by_id = {s.id: s for s in result.symbols}
        targets = {
            by_id[e.dst].name for e in result.edges
            if e.edge_type == "calls" and e.dst in by_id
        }
        assert "SafeMath.add" in targets, (
            "the `using X for Y` resolver keys on the member kind and no "
            "longer finds the library function"
        )

    def test_call_edges_are_attributed_to_the_enclosing_member(
        self, tmp_path: Path
    ) -> None:
        """The span index that resolves a call's enclosing symbol."""
        (tmp_path / "E.sol").write_text(
            "contract Host {\n"
            "    function inner() internal returns (uint) { return 1; }\n"
            "    function outer() public returns (uint) { return inner(); }\n"
            "}\n",
            encoding="utf-8",
        )
        result = analyze_solidity(tmp_path)
        by_id = {s.id: s for s in result.symbols}
        srcs = {
            by_id[e.src].name for e in result.edges
            if e.edge_type == "calls" and e.src in by_id
        }
        assert "Host.outer" in srcs, (
            "call edges lost their enclosing-member attribution; the span "
            "index filters on the member kind"
        )


class TestTheCrossPackageConsumerStaysInSync:
    """The kind vocabulary has a reader in ANOTHER package (INV-lapas).

    ``hypergumbo_core.linkers.solidity_abi`` filters Solidity symbols by
    kind to mint ABI call-site nodes. When the producer here started
    emitting ``method`` while that linker still read
    ``("function", "constructor")``, it silently stopped minting — **1,250
    call-site nodes on openzeppelin-contracts, and 6,689 resolved call
    edges with them**. Nothing raised; a count just dropped.

    Reading the diff did not catch it. Re-running the corpus did. So the
    coupling is pinned from the producer side, where the emitted kinds
    actually originate.
    """

    def test_emitted_callable_kinds_are_all_declared_in_the_shared_set(
        self, tmp_path: Path
    ) -> None:
        from hypergumbo_core.symbol_kinds import (
            SOLIDITY_CALLABLE_DECLARATION_KINDS,
        )

        (tmp_path / "All.sol").write_text(
            "function free(uint a) pure returns (uint) { return a; }\n"
            "library L { function lib() internal pure { } }\n"
            "interface I { function iface() external; }\n"
            "contract C {\n"
            "    constructor() { }\n"
            "    function member() public { }\n"
            "}\n",
            encoding="utf-8",
        )
        result = analyze_solidity(tmp_path)
        callable_kinds = {
            s.kind for s in result.symbols
            if s.kind in {"function", "method", "constructor"}
        }
        assert callable_kinds, "fixture produced no callables"
        assert callable_kinds <= SOLIDITY_CALLABLE_DECLARATION_KINDS, (
            f"the analyzer emits {sorted(callable_kinds - SOLIDITY_CALLABLE_DECLARATION_KINDS)}, "
            f"which hypergumbo_core.linkers.solidity_abi will not recognize — "
            f"it filters on SOLIDITY_CALLABLE_DECLARATION_KINDS. Add the kind "
            f"there, in core, so both packages read one name."
        )

    def test_both_member_and_free_function_kinds_are_covered(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL: the fixture exercises BOTH sides of the rule.

        A subset assertion passes trivially if the fixture only ever
        produces one kind. This asserts the fixture actually discriminates.
        """
        (tmp_path / "Both.sol").write_text(
            "function free(uint a) pure returns (uint) { return a; }\n"
            "contract C { function member() public { } }\n",
            encoding="utf-8",
        )
        kinds = {s.name: s.kind for s in analyze_solidity(tmp_path).symbols}
        assert kinds["free"] == "function"
        assert kinds["C.member"] == "method"
