# SPDX-License-Identifier: AGPL-3.0-or-later
"""Field-agnostic AST drift detector for axis-bearing canonical registries.

The original ``find_axis_drift`` in ``edge_types.py`` hard-coded two
things: (1) the substring ``EDGE_TYPE`` for matching consumer-side
set names, and (2) the search scope ``packages/`` only. This module
parameterizes both so the same machinery serves every axis-bearing
field that ADR-0024's template introduces. ``Symbol.kind`` (per
ADR-0027) and ``Edge.evidence_type`` (per ADR-0028) already plug
into this machinery via their own canonical registries; additional
axis-bearing fields follow the same template.

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


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Map module-local names to the string constants they are bound to.

    Shallow on purpose. Only a direct ``NAME = "literal"`` (or the annotated
    ``NAME: Final[str] = "literal"``) counts — no import following, no
    concatenation, no conditional rebinding. A vocabulary written as a set of
    names, ``frozenset({BOUNDARY_RULING_UNRULED, ...})``, is spelled that way
    in one module beside its constants, which is the case this reaches.
    """
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # ``Final[str]`` is the house style for these constants, so
            # skipping AnnAssign here would miss most of them.
            name, value = node.target.id, node.value
        if (
            name is not None
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            consts[name] = value.value
    return consts


def _resolved_strings(
    elements: Iterable[ast.expr], consts: dict[str, str],
) -> list[str] | None:
    """Every element as a string, or ``None`` if any element is not one.

    ALL-OR-NOTHING, and that is the safety property: reporting the resolvable
    subset of a partially-opaque collection would let a real drift value hide
    behind an unresolved sibling, turning a linter that says nothing into a
    linter that says the wrong thing.
    """
    out: list[str] = []
    for elt in elements:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        elif isinstance(elt, ast.Name) and elt.id in consts:
            out.append(consts[elt.id])
        else:
            return None
    return out or None


def iter_axis_set_assignments(
    path: Path,
    *,
    name_filter: str,
) -> Iterator[tuple[int, str, frozenset[str]]]:
    """Yield ``(lineno, target_name, frozenset_of_string_elements)`` for every
    module-level assignment in *path* whose target name contains *name_filter*
    and whose value enumerates string constants.

    COLLECTED SHAPES (WI-jinuj). Originally only a ``{...}`` set literal or a
    ``frozenset({...})`` wrapper, which meant a hardcoded copy of a vocabulary
    written any other way was invisible to every axis linter at once:

    - ``{...}`` and ``frozenset({...} / [...] / (...))`` — as before
    - a bare ``(...)`` tuple or ``[...]`` list (``CATALOG_BOUNDARY_TYPES``)
    - a ``{...}`` dict, by its KEYS and by its VALUES (``DEFERRED_CROSSING_SHADOWS``
      maps one boundary to another; ``DEFAULT_EDGE_TYPE_WEIGHTS`` is keyed by them)
    - elements that are module-local NAME references to string constants
      (``VALID_BOUNDARY_RULINGS`` is ``frozenset({NAME, NAME})``)

    A DICT WITH STRINGS ON BOTH SIDES YIELDS ITS SIDES SEPARATELY, as
    ``<NAME>:keys`` and ``<NAME>:values``. ``io_boundary._READ_TARGET_KIND_BOUNDARY``
    is keyed by ``io_target_kind`` values and valued by io-boundary names — two
    axes in one assignment — so folding the sides together would force the
    io-boundary linter to exclude the whole constant to silence the keys,
    losing the check on the values. When only one side is string-valued there
    is no ambiguity and the plain target name is kept, so exclusions written
    against the old behaviour keep working.

    STILL INVISIBLE, DELIBERATELY: a value computed by a CALL —
    ``frozenset(A + B)``, ``frozenset(SOMEDICT.values())``,
    ``all_io_boundary_names()``. A derived constant cannot DRIFT from the
    registry it is derived from, so there is nothing here for a drift linter
    to check; the risk such a linter exists to catch is a hardcoded copy.

    The substring filter prevents false positives from unrelated string sets
    (programming-language keyword vocabularies, stdlib method-name catalogs)
    that happen to share an element with the target registry by coincidence.

    Files that fail to read (binary garbage, permission errors) or to parse
    (syntax errors mid-edit) are silently skipped — this helper is best-effort
    and treats unreadable files as "no offenders found here."
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover
        return

    consts = _module_string_constants(tree)

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
        # target_name is set only on the two statement branches above, both of
        # which carry .lineno (the ast.AST base does not).
        assert isinstance(node, (ast.Assign, ast.AnnAssign))

        if isinstance(value, ast.Dict):
            keys = _resolved_strings(
                [k for k in value.keys if k is not None], consts,
            )
            vals = _resolved_strings(value.values, consts)
            if keys and vals:
                yield node.lineno, f"{target_name}:keys", frozenset(keys)
                yield node.lineno, f"{target_name}:values", frozenset(vals)
            elif keys:
                yield node.lineno, target_name, frozenset(keys)
            elif vals:
                yield node.lineno, target_name, frozenset(vals)
            continue

        elements: list[ast.expr] | None = None
        if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
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

        values = _resolved_strings(elements, consts)
        if values:
            yield node.lineno, target_name, frozenset(values)


def find_drift(
    repo_root: Path,
    *,
    name_filter: str,
    registry_names: frozenset[str],
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
    excluded_target_names: Iterable[str] = (),
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

    *excluded_target_names* is the set of target-name strings that
    match *name_filter* by substring but are NOT enumerations of the
    target axis. Some axes share substrings with unrelated
    vocabularies — e.g., ``Symbol.kind``'s ``KIND`` filter also
    matches ``PROTOCOL_KINDS`` and ``BRIDGE_KINDS``, which are
    ``Edge.meta`` key vocabularies, not ``Symbol.kind`` enumerations.
    Listing those names here lets the scan skip them without having
    to rename the pre-existing constants.

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
    excluded_targets = frozenset(excluded_target_names)
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
                # A dict yields ``<NAME>:keys`` / ``<NAME>:values``; an
                # exclusion written against the bare NAME silences both
                # sides, so pre-existing exclusions keep their meaning and a
                # caller can still name one side to keep the other checked.
                base_name = target_name.split(":", 1)[0]
                if target_name in excluded_targets or (
                    base_name in excluded_targets
                ):
                    continue
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
                    # The precondition check above raises when
                    # allowed_axis_names is set but name_to_axis is None, so it
                    # is non-None here.
                    assert name_to_axis is not None
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
