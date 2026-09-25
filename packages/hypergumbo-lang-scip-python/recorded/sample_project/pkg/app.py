# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-file calls: import, instantiate, call methods, and a stdlib receiver."""
import re

from pkg.shapes import Shape, describe


PATTERN = re.compile(r"\d+")


def main(argv: list[str]) -> int:
    shape = Shape(argv[0] if argv else "unit")
    shape.tag(" first ")
    text = describe(shape)
    match = PATTERN.search(text)
    if match is not None:
        return int(match.group(0))
    return len(shape.tags)
