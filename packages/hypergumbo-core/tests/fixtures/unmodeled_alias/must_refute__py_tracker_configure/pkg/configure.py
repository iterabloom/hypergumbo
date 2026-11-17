# SPDX-License-Identifier: AGPL-3.0-or-later
import sys
from pathlib import Path

from pkg.models import resolve_actor


def run_configure(root: Path) -> int:
    agent_patterns = ["*_agent"]
    by, username = resolve_actor(agent_patterns)
    if by == "agent":
        print(
            f"error: configure requires human authority "
            f"(current user '{username}' is agent)",
            file=sys.stderr,
        )
        return 1
    with open(root / "config.yaml", "w") as fh:
        fh.write("x: 1\n")
    return 0
