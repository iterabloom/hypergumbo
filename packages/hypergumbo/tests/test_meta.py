# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo meta-package.

CI installs the individual packages (hypergumbo-core, hypergumbo-lang-*)
but not the meta-package itself. These tests load the meta-package's
__init__.py directly via importlib to avoid requiring installation.

The pyproject extras tests are regression guards on the meta-package
distribution contract: `pipx install 'hypergumbo[rust-analyzer]'` must
keep working, and the extra's pinned version must track the meta-package
version in lockstep so a release-time `bump-version` covers both.
"""

import importlib.util
from pathlib import Path

try:  # pragma: no cover
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

META_DIR = Path(__file__).resolve().parent.parent
META_INIT = META_DIR / "src" / "hypergumbo" / "__init__.py"
META_PYPROJECT = META_DIR / "pyproject.toml"


def _load_meta_init():
    """Load the meta-package __init__.py without requiring installation."""
    spec = importlib.util.spec_from_file_location("hypergumbo", META_INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_pyproject() -> dict:
    return tomllib.loads(META_PYPROJECT.read_text(encoding="utf-8"))


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


def test_rust_analyzer_extra_is_defined():
    """The [rust-analyzer] extra must exist so `pipx install 'hypergumbo[rust-analyzer]'`
    engages the SCIP backend. Without it, --backend rust-analyzer either silently
    falls through (pre-BUG-06) or hits the runtime gate (post-BUG-06) but never
    actually runs SCIP."""
    pyproject = _load_pyproject()
    extras = pyproject["project"].get("optional-dependencies", {})
    assert "rust-analyzer" in extras, (
        "missing 'rust-analyzer' extra in packages/hypergumbo/pyproject.toml"
    )
    deps = extras["rust-analyzer"]
    assert any(d.startswith("hypergumbo-lang-rust-analyzer") for d in deps), (
        f"[rust-analyzer] extra must pull hypergumbo-lang-rust-analyzer, got {deps!r}"
    )


def test_rust_analyzer_extra_version_pin_matches_meta_version():
    """The extra must pin to the same version as the meta-package itself, so
    `scripts/bump-version` keeps them coordinated. A drift here would silently
    install a stale rust-analyzer integration against a newer hypergumbo-core."""
    pyproject = _load_pyproject()
    meta_version = pyproject["project"]["version"]
    extras = pyproject["project"]["optional-dependencies"]
    pin = next(
        d for d in extras["rust-analyzer"]
        if d.startswith("hypergumbo-lang-rust-analyzer")
    )
    assert f"=={meta_version}" in pin, (
        f"[rust-analyzer] extra version pin {pin!r} must match meta version {meta_version!r}"
    )
