# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shapes: a class with methods, a property, a constant and a free function."""
from pathlib import Path

UNIT = 1.0


class Shape:
    """A named shape."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tags: list[str] = []

    @property
    def label(self) -> str:
        return self.name.upper()

    def area(self) -> float:
        return UNIT

    def tag(self, value: str) -> None:
        self.tags.append(value.strip())


def describe(shape: Shape) -> str:
    """Free function calling a method and a stdlib method on a typed receiver."""
    parts = [shape.label, str(shape.area())]
    return " ".join(parts)


def list_configs(root: Path) -> list[Path]:
    return sorted(root.glob("*.toml"))
