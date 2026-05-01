# SPDX-License-Identifier: AGPL-3.0-or-later
"""Field-agnostic AST drift detector for axis-bearing canonical registries.

The original ``find_axis_drift`` in ``edge_types.py`` hard-coded two
things: (1) the substring ``EDGE_TYPE`` for matching consumer-side
set names, and (2) the search scope ``packages/`` only. This module
parameterizes both so the same machinery serves every axis-bearing
field that ADR-0024's template introduces (``Symbol.kind``,
``evidence_type``, etc. — once each lands its own canonical
registry).

The Edge.edge_type case keeps a thin wrapper at
``edge_types.find_axis_drift`` so existing callers (the property test
in ``test_edge_types.py`` and the pre-commit CLI
``scripts/check-edge-type-drift``) need no changes.

Why this lives in ``hypergumbo_core`` rather than ``scripts``: future
``<field>_types.py`` registry modules in this package import the
helper directly. Keeping it next to the registries means new axis
declarations inherit the function without crossing the
``packages``/``scripts`` boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final, Iterable, Iterator


DEFAULT_SEARCH_ROOTS: Final[tuple[str, ...]] = (
    "packages",
    "scripts",
    ".agent",
)
"""Default repo-relative directories scanned for drift.

Originally narrowed to ``packages/`` at the first ship; broadened
once the detector proved out, per WI-zisit-hagud. Each axis can
override via the ``search_roots`` parameter on ``find_drift`` if its
codebase layout differs.
"""

DEFAULT_EXCLUDED_PATH_SUBSTRINGS: Final[tuple[str, ...]] = (
    "/tests/",
)
"""Path-substring filters: any matching path is silently skipped.

Test directories legitimately contain arbitrary string sets (fixture
data, synthetic-fixture drift assertions). Each axis can extend or
override via the ``excluded_path_substrings`` parameter — a future
``Symbol.kind`` audit may also exclude ``/migrations/``, for
example.
"""


def iter_axis_set_assignments(
    path: Path,
    *,
    name_filter: str,
) -> Iterator[tuple[int, str, frozenset[str]]]:
    """Yield ``(lineno, target_name, frozenset_of_string_elements)`` for
    every module-level ``<NAME> = {...}`` or
    ``<NAME> = frozenset({...})`` assignment in *path* where ``NAME``
    contains *name_filter* as a substring and every element is a
    string literal.

    The substring filter prevents false positives from unrelated
    string sets (programming-language keyword vocabularies, language
    stdlib method-name catalogs, etc.) that happen to share an
    element with the target registry by coincidence.

    Files that fail to read (binary garbage, permission errors) or to
    parse (syntax errors mid-edit) are silently skipped — this helper
    is best-effort and treats unreadable files as "no offenders found
    here."
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover
        return

    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name,
            ):
                target_name = node.targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name,
        ):
            target_name = node.target.id
            value = node.value
        if target_name is None or name_filter not in target_name:
            continue
        if value is None:
            continue

        elements: list[ast.expr] | None = None
        if isinstance(value, ast.Set):
            elements = list(value.elts)
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and len(value.args) == 1
            and isinstance(value.args[0], (ast.Set, ast.List, ast.Tuple))
        ):
            elements = list(value.args[0].elts)
        if not elements:
            continue

        values: list[str] = []
        all_strings = True
        for elt in elements:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                all_strings = False
                break
        if all_strings and values:
            yield node.lineno, target_name, frozenset(values)


def find_drift(
    repo_root: Path,
    *,
    name_filter: str,
    registry_names: frozenset[str],
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
    allowed_axis_names: frozenset[str] | None = None,
    name_to_axis: dict[str, str] | None = None,
) -> list[str]:
    """Return human-readable drift offenders found under *repo_root*.

    AST-walks every ``.py`` file under each *search_roots* directory
    (skipping any path containing one of *excluded_path_substrings*),
    looking for module-level assignments whose target name contains
    *name_filter*. Each set's values are checked against
    *registry_names*; any value not in the registry is reported as
    drift.

    When *allowed_axis_names* is provided (strict mode), additionally
    enforces axis-principle membership: every value must appear in the
    intersection of the registry AND the allowed-axis set. Values
    that are in the registry but on a disallowed axis are reported as
    "off-axis" drift. *name_to_axis* is the registry's name→axis map
    used for that lookup; required when *allowed_axis_names* is given,
    ignored otherwise. The two parameters are split rather than
    fused into a single ``axis_filter`` so callers can pass the
    canonical map once and toggle strictness via the allowed-set.

    Returns an empty list if no drift is detected, and silently skips
    *search_roots* entries that don't exist on disk (so a synthetic
    test repo with only ``packages/`` is fine).

    The output lines are formatted ``<rel_path>:<lineno>
    (<target_name>): contains [<sorted_drift_values>] not in
    canonical registry`` — same shape as the original Edge-types
    detector so downstream messages (test failures, pre-commit
    output) read consistently across axes. Off-axis offenders use
    the suffix ``not on allowed axis <{...}>`` so callers can
    distinguish unregistered drift from registered-but-off-axis drift.
    """
    if allowed_axis_names is not None and name_to_axis is None:
        raise ValueError(
            "allowed_axis_names requires name_to_axis "
            "(the registry's name→axis lookup map)",
        )
    excluded_tuple = tuple(excluded_path_substrings)
    offenders: list[str] = []
    for root_name in search_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in root.rglob("*.py"):
            py_str = str(py_file)
            if any(sub in py_str for sub in excluded_tuple):
                continue
            for lineno, target_name, values in iter_axis_set_assignments(
                py_file, name_filter=name_filter,
            ):
                try:
                    rel = py_file.relative_to(repo_root)
                except ValueError:  # pragma: no cover
                    rel = py_file
                drift = values - registry_names
                if drift:
                    offenders.append(
                        f"{rel}:{lineno} ({target_name}): "
                        f"contains {sorted(drift)} not in canonical registry"
                    )
                if allowed_axis_names is not None:
                    off_axis = {
                        v for v in (values & registry_names)
                        if name_to_axis.get(v) not in allowed_axis_names
                    }
                    if off_axis:
                        offenders.append(
                            f"{rel}:{lineno} ({target_name}): "
                            f"contains {sorted(off_axis)} not on "
                            f"allowed axis {sorted(allowed_axis_names)}"
                        )
    return offenders
