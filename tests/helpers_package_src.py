# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put this checkout's ``hypergumbo-core`` source on ``sys.path``, by name.

INV-vazuh. The cron's ``test-agent-infra`` step runs the top-level ``tests/``
with NO hypergumbo package installed, on purpose. A module there that imports
``hypergumbo_core`` still passed, because several sibling modules load a
``scripts/*`` file at collection time that does ``sys.path.insert(0,
<repo>/packages/hypergumbo-core/src)``, and every xdist worker collects every
module. Run alone, or without those siblings, the same module failed: it was
passing on another module's state.

A module that imports ``hypergumbo_core`` calls ``put_core_src_on_path()`` at
import, so its requirement is its own and it behaves the same alone as in the
full suite. ``tests/test_rct_pinned_standalone.py`` runs each such test in an
interpreter with the package roots scrubbed to hold that.

Only core: a test whose subject needs a language package (and so the grammars
the step does not install) is ``--ignore``d by that step and run by
``test-all-packages`` instead (``tests/test_full_suite_agent_infra_ignores.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[1] / "packages" / "hypergumbo-core" / "src"


def put_core_src_on_path() -> None:
    if CORE_SRC.is_dir() and str(CORE_SRC) not in sys.path:
        sys.path.insert(0, str(CORE_SRC))
