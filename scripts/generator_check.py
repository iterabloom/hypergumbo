# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit codes for a doc generator's ``--check``: fresh, stale, or could not run.

WHY THREE. ``.githooks/pre-commit`` gates commits on ``generate-schema``,
``generate-concept-axes`` and ``generate-concepts`` with ``--check``, and it
read every non-zero exit as "out of date". The generators return 1 for stale,
and Python also exits 1 on an uncaught exception, so an import failure printed
"Schema is out of date. Run: ./scripts/generate-schema" over a schema that was
current. Running the suggested command then failed the same way or changed
nothing (WI-ziguk). A check that cannot run has not found the doc stale, and
the reader needs to be told which of the two happened.

So: 0 fresh, 1 stale (the generator compared and the doc differs, or it is
missing), 2 could not run (any exception, with the reason on stderr). This
follows the mypy ratchet's contract, where exit 2 is likewise an
infrastructure failure rather than a finding. The hook still blocks on 2; it
just says why.

Usage, in a generator that puts ``scripts/`` on ``sys.path``::

    try:
        from hypergumbo_core.x import y
    except ImportError as exc:
        sys.exit(could_not_run(exc))
    ...
    if __name__ == "__main__":
        run(main)
"""
from __future__ import annotations

import sys
import traceback
from typing import Callable, NoReturn

FRESH = 0
STALE = 1
COULD_NOT_RUN = 2

ACTIVATE_HINT = (
    "  The dev environment is probably not active. Run: source .venv/bin/activate"
)


def could_not_run(exc: BaseException) -> int:
    """Say why the check could not run, and return :data:`COULD_NOT_RUN`."""
    print(f"ERROR: could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
    if isinstance(exc, ImportError):
        print(ACTIVATE_HINT, file=sys.stderr)
    return COULD_NOT_RUN


def run(main: Callable[[], int]) -> NoReturn:
    """Exit with ``main()``'s code; an exception exits :data:`COULD_NOT_RUN`.

    ``SystemExit`` passes through untouched: argparse already exits 2 on a
    usage error, which is the same "could not run" outcome.
    """
    try:
        code = main()
    except Exception as exc:  # every crash is "could not run"
        traceback.print_exc()
        code = could_not_run(exc)
    sys.exit(code)
