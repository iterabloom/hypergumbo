# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load an extensionless script as a module.

``scripts/check-measurement-frame`` has no ``.py`` suffix because it is an
executable, so ``import`` cannot find it. Loading it by path is what lets the
tests exercise THE SHIPPED FILE rather than a copy of its logic — a copy is
free to drift from what CI runs, which is the failure this whole gate is about.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_gate(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_loader(
        "check_measurement_frame",
        importlib.machinery.SourceFileLoader("check_measurement_frame", str(path)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
