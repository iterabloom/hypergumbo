# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo meta-package.

CI installs the individual packages (hypergumbo-core, hypergumbo-lang-*)
but not the meta-package itself. These tests load the meta-package's
__init__.py directly via importlib to avoid requiring installation.
"""

import importlib.util
from pathlib import Path

META_INIT = (
    Path(__file__).resolve().parent.parent / "src" / "hypergumbo" / "__init__.py"
)


def _load_meta_init():
    """Load the meta-package __init__.py without requiring installation."""
    spec = importlib.util.spec_from_file_location("hypergumbo", META_INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_version_reexport():
    """Verify the meta-package re-exports __version__ from core."""
    mod = _load_meta_init()
    assert isinstance(mod.__version__, str)
    assert mod.__version__  # non-empty


def test_all_exports():
    """Verify __all__ lists the expected public API."""
    mod = _load_meta_init()
    assert hasattr(mod, "__all__")
    assert "__version__" in mod.__all__
