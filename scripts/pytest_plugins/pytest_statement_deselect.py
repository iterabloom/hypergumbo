# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest plugin: ``--statement-deselect=FILE`` deselects the node ids in FILE.

smart-test selects test FILES; the by-statement selector
(``hypergumbo_core.statement_narrow``) names, inside those files, the tests a
change does not need. ``coverage-select statement-narrow`` writes their ids,
one per line, and this plugin leaves them out after collection, reported as
deselected in pytest's summary line.

WHY A PLUGIN AND NOT ``--deselect`` ON THE COMMAND LINE: a narrowed run can
leave out tens of thousands of tests, and each ``--deselect`` is an argument.

WHY AN ID IS MATCHED AFTER COLLECTION: a test the file holds and the list does
not name runs. That is the rule for a test added since the index last saw its
file, which the selector cannot know about.

``hypergumbo_core`` is imported inside the hook, not at the top: a plugin named
with ``-p`` is imported before pytest-cov starts measuring, and the module
bodies it pulled in would then count as never run in the coverage gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--statement-deselect", default=None, metavar="FILE",
        help="deselect the node ids listed in FILE (one per line)",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    path = config.getoption("statement_deselect")
    if not path or not Path(path).is_file():
        return
    left_out = {line.strip() for line in Path(path).read_text().splitlines() if line.strip()}
    if not left_out:
        return
    from hypergumbo_core.statement_narrow import deselected

    gone = set(deselected([item.nodeid for item in items], left_out))
    if gone:
        config.hook.pytest_deselected(items=[items[i] for i in sorted(gone)])
        items[:] = [item for i, item in enumerate(items) if i not in gone]
