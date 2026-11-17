# SPDX-License-Identifier: AGPL-3.0-or-later
import shutil
import sys
from pathlib import Path

from pkg.models import resolve_actor


def _cmd_init(root: Path) -> int:
    agent_patterns = ["*_agent"]
    by, username = resolve_actor(agent_patterns)
    if by == "agent":
        print(
            f"error: init requires human authority "
            f"(current user '{username}' is agent)",
            file=sys.stderr,
        )
        return 1
    shutil.copy2(root / "a", root / "b")
    return 0
