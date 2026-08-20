# SPDX-License-Identifier: AGPL-3.0-or-later
"""Emission-parity python fixture.

Uniform construct set (see tests/fixtures/emission-parity/README.md):
import, documented callable with a branchy body (complexity > 1) that calls a
helper, a helper callee, a class with a method, an exported public surface, an
entrypoint idiom (the ``__main__`` guard), an enumerated type with named
members, and an abstract type with member signatures.
"""
import os
from enum import Enum
from typing import Protocol

MAX_ITEMS = 100


def helper(value):
    """Return a derived string."""
    return os.getcwd() + str(value)


def process(items, flag):
    """Process items with branching."""
    total = 0
    if flag:
        total += 1
    if items:
        total += len(items)
    if total > 5:
        total = 5
    return helper(total)


class Service:
    """A small service."""

    count = 0

    def run(self):
        """Run the service."""
        return process([1, 2, 3], True)


class Color(Enum):
    """Enumerated type whose named members are container members."""

    RED = "red"
    GREEN = "green"


class Drawable(Protocol):
    """Abstract type whose member signatures are container members."""

    def draw(self) -> str:
        """Render."""
        ...

    def area(self) -> float:
        """Measure."""
        ...


if __name__ == "__main__":
    Service().run()
