# SPDX-License-Identifier: MPL-2.0
"""CLI entry points for hypergumbo-tracker.

Stubs only in PR 1a. Real CLI implementation comes in PR 3.
The two entry points are:
- main(): Primary CLI for tracker operations (add, update, list, etc.)
- textconv_main(): Git textconv driver for rendering .ops files as readable text.
"""

import sys


def main() -> None:
    """Primary CLI entry point — stub for PR 1a."""
    print("hypergumbo-tracker: not yet implemented (PR 3)", file=sys.stderr)
    raise SystemExit(1)


def textconv_main() -> None:
    """Git textconv driver entry point — stub for PR 1a."""
    print("hypergumbo-tracker-textconv: not yet implemented (PR 3)", file=sys.stderr)
    raise SystemExit(1)
