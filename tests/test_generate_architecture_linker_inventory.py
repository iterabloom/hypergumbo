# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every module that registers a linker must be inventoried as a linker.

``scripts/generate-architecture`` builds its module inventory by walking
each package's ``src`` root and skipping any file whose name begins with
an underscore. That skip is right for genuine private helpers
(``_concept_utils``, ``_text_filters``, ``_view_template_core``), which
carry no ``@register_linker`` and would otherwise be counted as linkers
they are not.

It was wrong for ``linkers/_third_party_bases.py``, which is
underscore-named *and* registers a production linker
(``django-third-party-dispatch-linker``). The generated count therefore
read 60 while ``registry.list_registered()`` returned 61, and every
downstream doc — ``docs/LINKERS.md``, ``docs/ARCHITECTURE.md``,
``docs/hypergumbo-spec.md`` §7, ADR-3bbb's own Status line — inherited
the undercount.

The deeper failure is that ADR-3bbb's enforcement promise ("the
Uncategorized counter is 0 and will surface any future regression")
could not see this file at all: a module the scan skips can be flagged
neither Uncategorized nor categorized. The guarantee had a hole exactly
the shape of an underscore-named linker.

This test closes the class rather than the instance — it asserts the
property for *every* registering module, so a future ``_foo.py`` that
registers a linker fails here instead of silently vanishing from the
count.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LINKERS_DIR = (
    REPO_ROOT
    / "packages"
    / "hypergumbo-core"
    / "src"
    / "hypergumbo_core"
    / "linkers"
)


def _load_generate_architecture_module():
    """Load ``scripts/generate-architecture`` as a Python module."""
    script_path = REPO_ROOT / "scripts" / "generate-architecture"
    loader = importlib.machinery.SourceFileLoader(
        "generate_architecture_linker_inventory_under_test",
        str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _modules_that_register_a_linker() -> set[str]:
    """Names of linker modules carrying a ``@register_linker`` decorator."""
    found = set()
    for py_file in sorted(LINKERS_DIR.glob("*.py")):
        if py_file.stem == "registry":
            continue
        if "@register_linker" in py_file.read_text(encoding="utf-8"):
            found.add(py_file.stem)
    return found


def test_every_registering_module_is_inventoried_as_a_linker() -> None:
    """A module that registers a linker is counted as one."""
    mod = _load_generate_architecture_module()
    categories = mod.categorize_modules()
    inventoried = {name.rsplit(".", 1)[-1] for name, _ in categories["Linkers"]}

    registering = _modules_that_register_a_linker()
    missing = registering - inventoried

    assert not missing, (
        "These modules register a linker but are absent from the "
        f"generate-architecture linker inventory: {sorted(missing)}. "
        "An uninventoried linker is invisible to the ADR-3bbb "
        "Uncategorized counter, so it can be flagged neither "
        "categorized nor uncategorized, and every generated count "
        "understates the catalogue."
    )


def test_private_helpers_without_registration_stay_out_of_the_count() -> None:
    """The underscore skip still holds for non-registering helpers.

    Positive control for the fix: without this, widening the scan to all
    underscore modules would pass the test above while inflating the
    linker count with helper modules that register nothing.
    """
    mod = _load_generate_architecture_module()
    categories = mod.categorize_modules()
    inventoried = {name.rsplit(".", 1)[-1] for name, _ in categories["Linkers"]}

    helpers = {
        p.stem
        for p in LINKERS_DIR.glob("_*.py")
        if p.stem != "__init__"
        and "@register_linker" not in p.read_text(encoding="utf-8")
    }
    assert helpers, "expected some private linker helpers to exist"
    assert not (helpers & inventoried), (
        "Private helper modules that register no linker must not be "
        f"counted as linkers: {sorted(helpers & inventoried)}"
    )
