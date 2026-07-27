# SPDX-License-Identifier: AGPL-3.0-or-later
"""Default-view noise predicate for the survey pipeline (Phase D).

``run_survey`` drops degree-0 "noise" symbols from the default (``--include-docs``
off) behavior map so the output carries architectural signal, not config/doc
scaffolding. The decision lives here — as a module-level, unit-testable pure
predicate — rather than as a closure inside ``run_survey`` (which made every
branch reachable only through a full survey integration run).

Three static branches (documentation/config kinds; CSS-family ``variable``
custom-properties; config-language ``table`` section headers) plus one
axis-refined branch for ``entry_role=script`` file symbols.

**The ``entry_role=script`` split (WI-papag).** A single predicate
(``kind=="file" and entry_role=="script"``) conflated two genuinely different
node populations:

* **npm ``package.json`` ``"scripts"``** (``build`` / ``test`` / ``lint``):
  ``kind="file"``, ``language="json"``, degree-0, ``meta["command"]`` holding a
  shell command and **no** ``meta["entry_point"]``. The ``npm_script`` concept
  is *not* recognized by ``detect_entrypoints`` — these are genuine noise, and
  filtering them is correct (audit-findings 0005, the Wave-6 fold).
* **pyproject ``[project.scripts]`` console-scripts** (``mycli = "pkg.mod:func"``):
  ``kind="file"``, ``language="toml"``, ``meta["entry_point"]`` naming a code
  target (plus a ``defines_target`` edge into it). The ``pyproject_script``
  concept **is** recognized by ``detect_entrypoints`` → ``CLI_COMMAND`` @0.99
  (``manifest_declared``). These are entrypoint-bearing and MUST survive the
  noise filter, or the noise filter erases them before they are detected —
  exactly the defect ADR-0043 §5 named "C3".

The blanket filter fixed the npm noise but re-opened C3 for the pyproject/console
subset (a real, reproducible entrypoint-loss regression: e.g. a package's sole
declared CLI command vanished from its map). The discriminator is
``meta["entry_point"]``, available at filter time (the ``pyproject_script``
concept is not yet attached when Phase D runs): present == a declared code
entrypoint (exempt), absent == a bare shell run-script (filter). This reconciles
audit-findings 0005 (filter npm run-scripts) with ADR-0043 §5 (exempt
entrypoint-bearing symbols) by subset — both stand, each scoped to the
population it correctly describes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ir import Symbol

# Documentation / config / CSS-structural kinds that are degree-0 in behavior
# maps. ADR-0027 Phase-2 audit (WI-jukav): all members are AXIS_PENDING
# (build/config-shape, domain long-tail) or AXIS_LANGUAGE_CONSTRUCT
# (``property`` / ``label`` / ``paragraph`` per audit-findings 0006/0007); none
# is scheduled for a Phase-3 fold/rename, so the noise-filtering semantics are
# forward-compatible.
_NOISE_SYMBOL_KINDS = frozenset({
    # Documentation / config
    "section", "table_array", "code_block",
    "link", "paragraph", "label",
    "setting",
    # CSS structural (degree-0 in behavior maps)
    "class_selector", "id_selector", "rule_set",
    "property", "media", "keyframes", "font_face",
    # Config metadata (degree-0 across all tested repos)
    "pattern",      # .gitignore entries
    "requirement",  # pip requirements.txt entries
})

# CSS-family ``variable`` (custom properties, SCSS / Sass variables) is
# zero-edge noise. WI-gafog E2: in any other language ``variable`` is a real
# top-level binding (Python module constants, Go top-level ``var``, YAML / Make
# variables) and must survive for cross-file ``from <mod> import NAME``.
_NOISE_CSS_VARIABLE_LANGUAGES = frozenset({"css", "scss", "sass", "less"})

# INV-bovif: ``kind="table"`` is overloaded between TOML/INI/properties
# ``[section]`` headers (config noise) and SQL ``CREATE TABLE`` entities
# (first-class schema constructs). Filter only the config-language producers; SQL
# tables pass through so the database_query linker can link query call-sites to
# schema tables.
_NOISE_TABLE_LANGUAGES = frozenset({"toml", "ini", "properties"})


def is_noise_symbol(sym: "Symbol") -> bool:
    """Return True if ``sym`` is default-view noise (dropped when not
    ``--include-docs``). See the module docstring for the ``entry_role=script``
    reconciliation (WI-papag)."""
    if sym.kind in _NOISE_SYMBOL_KINDS:
        return True
    if sym.kind == "variable" and sym.language in _NOISE_CSS_VARIABLE_LANGUAGES:
        return True
    if sym.kind == "table" and sym.language in _NOISE_TABLE_LANGUAGES:
        return True
    # WI-papag: entry_role=script is two populations (see module docstring).
    # Filter the bare npm run-script (no entry_point); exempt the
    # entrypoint-bearing pyproject/console-script (declares an entry_point).
    if (
        sym.kind == "file"
        and sym.meta
        and sym.meta.get("entry_role") == "script"
        and not sym.meta.get("entry_point")
    ):
        return True
    return False
