// SPDX-License-Identifier: MIT
// WI-luzuh fixture: Solidity source-language constructs.
// Triggers: contract, interface, event, modifier, function.

pragma solidity ^0.8.0;

interface IToken {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract Token is IToken {
    address public owner;
    uint256 public totalSupply;

    event Transfer(address indexed from, address indexed to, uint256 value);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor(uint256 supply) {
        owner = msg.sender;
        totalSupply = supply;
    }

    function transfer(address to, uint256 amount) external override returns (bool) {
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
