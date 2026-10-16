# SPDX-License-Identifier: AGPL-3.0-or-later
"""Requirements discovery finds requirements/ and reqs/ files under a FileIndex.

Every survey installs a FileIndex; before INV-kalaj the index matched
``requirements/*.txt`` against bare file names and found nothing, so only a
top-level ``requirements*.txt`` reached the analyzer in production.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.discovery import FileIndex, set_file_index
from hypergumbo_lang_mainstream.requirements import find_requirements_files


def test_subdirectory_requirements_are_found_with_an_index(tmp_path: Path) -> None:
    for rel in ("requirements.txt", "requirements/base.txt", "reqs/dev.txt"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("requests==2.0\n")

    set_file_index(FileIndex.build(tmp_path))
    try:
        found = {p.relative_to(tmp_path).as_posix() for p in find_requirements_files(tmp_path)}
    finally:
        set_file_index(None)

    assert found == {"requirements.txt", "requirements/base.txt", "reqs/dev.txt"}
