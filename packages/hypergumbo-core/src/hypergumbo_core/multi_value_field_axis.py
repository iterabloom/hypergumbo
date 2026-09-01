# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-value field axis declaration linter (WI-busij).

Closes ADR-0024 §"Open questions" Q2: the creation-practice rule that
every new ``str``-typed field on a core dataclass must declare its
axis was, until this module, prose-only — relying on the author to
notice the rule applied. This module mechanizes the rule.

What the linter does
--------------------
For each ``@dataclass``-decorated class in the configured "core files"
list (``ir.py`` and ``datamodels.py`` by default), the linter walks
every field annotation. For each field whose annotation is ``str``,
``Optional[str]``, ``str | None``, or a ``Literal[...]`` of strings,
the linter requires a trailing source-line comment of the form
``# axis: <category>`` (optionally with extra text). The category must
be one of:

- A **known axis name** (``edge-type``, ``symbol-kind``,
  ``evidence-type``, ``language``, ``pass-id``, ``protocol-origin``,
  ``qualified-name``) — the field's value space is the legal set
  returned by the axis's all-names function. ``language`` and
  ``pass-id`` are derived from the analyzer/linker catalog
  (:func:`hypergumbo_core.catalog`); the other five live in dedicated
  ``*_types.py`` / ``*_origins.py`` / ``*_axis.py`` registry modules.
  The ``protocol-origin`` axis (ADR-0031) covers
  ``Symbol.protocol_origin`` values for synthetic stand-ins emitted by
  linkers that detect protocol patterns (Kafka, WebSocket, IPC, WASM,
  GraphQL, etc.). The ``qualified-name`` axis (ADR-0032) covers
  ``Symbol.qualified_name`` and declares the per-language separator
  policy (Python ``.``, Rust ``::``, PHP ``\\``, etc.).
- ``identity`` — the field's role is to uniquely identify a record
  (ids, hashes, signatures keyed per instance). Not enumerable.
- ``bounded-enum`` — the field's value comes from a small fixed list
  (≤5 values by convention) documented in the dataclass docstring.
  No separate registry module needed.
- ``free-text`` — the field's value is open-ended payload, never
  branched on by consumers. **Requires a justification** after the
  tag (``# axis: free-text — <reason>``) so the choice can't be a
  drive-by escape hatch.

Why each category exists
------------------------
The four categories partition every realistic role a string field
can play on a core dataclass. The categorization is the design
discipline; the linter is the mechanical backstop.

ADR-0024 §3 open question 2 originally proposed a ``# axis: pending
WI-xxxxx`` escape hatch for "I know this is multi-value but haven't
designed the axis yet." That option was removed during the WI-busij
design discussion (2026-05-20) because it duplicates the
``todo_hard`` circuit breaker from the structural-fix protocol and
weakens the gate at the moment when the design conversation should
happen — i.e., at PR review, before the field ships. If you're not
ready to declare the axis, you're not ready to add the field.

``free-text`` carries a justification requirement for the same
reason: it's the only category whose "this is the right call" claim
isn't anchored elsewhere (named axes have a registry; ``identity``
has a uniqueness invariant; ``bounded-enum`` has the docstring
listing). Requiring the author to write a one-sentence explanation
raises the friction enough to deter drive-by use.

Exit semantics
--------------
The drift report is a list of ``"<file>:<line>: <message>"`` strings.
Empty list means agreement; non-empty means at least one
declaration-site violation. The script ``scripts/check-multi-value-
field-axis-declaration`` wraps this with exit-code conventions
(0 / 1 / 2) matching ``scripts/check-edge-type-drift``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable, Iterable, Iterator


# Hard-coded core dataclass files. To add a file, append its path
# (relative to repo root) here. A sentinel-comment opt-in mechanism
# is reserved for a future PR if drift surfaces.
DEFAULT_CORE_FILES: tuple[str, ...] = (
    "packages/hypergumbo-core/src/hypergumbo_core/ir.py",
    "packages/hypergumbo-core/src/hypergumbo_core/datamodels.py",
)


def _known_axes() -> dict[str, Callable[[], Iterable[str]]]:
    """Map axis name → callable returning that axis's legal value set.

    Imports are deferred so this module is importable from environments
    that don't have the catalog (and its transitive analyzer/linker
    registry walking) initialised. Each callable is invoked lazily by
    :func:`check_axis_named` only when a field actually claims the
    axis.
    """
    from .edge_types import all_edge_type_names
    from .entrypoints import all_known_entrypoint_kinds
    from .evidence_types import all_evidence_type_names
    from .io_boundary_types import all_io_boundary_names
    from .module_key_axis import all_module_key_notions
    from .protocol_origins import all_protocol_origin_names
    from .qualified_name_axis import all_qualified_name_languages
    from .symbol_kinds import all_symbol_kind_names
    from .visibility import all_known_visibility_levels
    from .catalog import all_known_languages, all_known_pass_ids

    return {
        "edge-type": all_edge_type_names,
        "symbol-kind": all_symbol_kind_names,
        "evidence-type": all_evidence_type_names,
        # INV-tafig/ADR-0050: the I/O-boundary vocabulary six
        # consumers branch on. Registry-backed, heavyweight.
        "io-boundary": all_io_boundary_names,
        # WI-kijup/ADR-0051: what may occupy a module slot.
        # Structural-policy axis (qualified-name shape): the
        # resolver returns the axis's NOTIONS, not legal field
        # values, which are unenumerable.
        "module-key": all_module_key_notions,
        "language": all_known_languages,
        "pass-id": all_known_pass_ids,
        "protocol-origin": all_protocol_origin_names,
        "qualified-name": all_qualified_name_languages,
        # WI-pupiz: entrypoint-kind catalog (single source = EntrypointKind).
        "entrypoint-kind": all_known_entrypoint_kinds,
        # INV-jusot: canonical visibility levels (closed enum).
        "visibility": all_known_visibility_levels,
    }


_AXIS_COMMENT_RE = re.compile(
    r"^.*?#\s*axis:\s*(?P<category>[a-z\-]+)(?:\s+(?P<extra>.*))?$"
)


def _is_str_like_annotation(node: ast.expr) -> bool:
    """Return True iff *node* is a type annotation rooted at ``str``.

    Covers ``str``, ``Optional[str]``, ``str | None`` / ``None | str``,
    and ``Literal[...]`` of string constants. Container types whose
    elements are ``str`` (``List[str]``, ``Dict[str, str]``) are NOT
    in scope — those are payload containers, not single-string
    enumerable fields.
    """
    # Bare ``str``
    if isinstance(node, ast.Name) and node.id == "str":
        return True
    # ``Optional[str]`` → Subscript(value=Name('Optional'), slice=Name('str'))
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        if node.value.id == "Optional":
            return _is_str_like_annotation(node.slice)
        if node.value.id == "Literal":
            # ``Literal[...]`` where all values are str constants
            # counts as a multi-value str enumeration.
            elts = (
                node.slice.elts
                if isinstance(node.slice, ast.Tuple)
                else [node.slice]
            )
            return all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in elts
            )
    # ``str | None`` / ``None | str``
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_str_like_annotation(node.left) or _is_str_like_annotation(
            node.right
        )
    return False


def _iter_dataclass_str_fields(
    py_file: Path,
) -> Iterator[tuple[str, str, int, str]]:
    """Yield ``(class_name, field_name, line_no, source_line)`` per str field.

    A "str field" is any class-body ``AnnAssign`` whose target is a
    ``Name`` and whose annotation passes :func:`_is_str_like_annotation`.
    Class-level fields that aren't annotated assignments (regular
    assignments, methods, etc.) are skipped.
    """
    source = py_file.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(py_file))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_dataclass(node):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue  # pragma: no cover — class-body AnnAssign with non-Name target is exotic
            if not _is_str_like_annotation(stmt.annotation):
                continue
            line_no = stmt.lineno
            source_line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            yield (node.name, stmt.target.id, line_no, source_line)


def _is_dataclass(class_def: ast.ClassDef) -> bool:
    """Return True iff *class_def* carries an ``@dataclass`` decorator.

    Accepts both bare ``@dataclass`` and ``@dataclass(...)`` (e.g.,
    ``@dataclass(frozen=True)``). Decorator must reference ``dataclass``
    by name; ``@dataclasses.dataclass`` is also accepted.
    """
    for dec in class_def.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "dataclass"
        ):
            return True
    return False


def _parse_axis_comment(source_line: str) -> tuple[str, str] | None:
    """Parse the ``# axis: ...`` comment from *source_line*, if present.

    Returns ``(category, extra)`` where ``extra`` may be empty for
    categories that don't require it. Returns ``None`` when the line
    has no ``# axis:`` comment.
    """
    match = _AXIS_COMMENT_RE.match(source_line)
    if not match:
        return None
    category = match.group("category")
    extra = (match.group("extra") or "").strip()
    return category, extra


_VALID_CATEGORIES_NON_AXIS: frozenset[str] = frozenset(
    {"identity", "bounded-enum", "free-text"}
)


def _check_field(
    py_file: Path,
    class_name: str,
    field_name: str,
    line_no: int,
    source_line: str,
    known_axes: dict[str, Callable[[], Iterable[str]]],
) -> str | None:
    """Validate one field's axis declaration. Return error string or None."""
    parsed = _parse_axis_comment(source_line)
    if parsed is None:
        return (
            f"{py_file}:{line_no}: {class_name}.{field_name} is a str-typed "
            "field on a core dataclass but has no `# axis: ...` declaration. "
            "Add one of: `# axis: <name>` (named axis with a registry), "
            "`# axis: identity` (unique-per-record), `# axis: bounded-enum` "
            "(small fixed list in docstring), or "
            "`# axis: free-text — <justification>`."
        )
    category, extra = parsed
    if category in known_axes:
        # Resolve the axis's legal-values function to confirm the axis
        # is wired into the linter (otherwise a typo in the category
        # name would silently pass as "unknown axis assumed correct").
        try:
            known_axes[category]()
        except Exception as exc:  # pragma: no cover — defensive
            return (
                f"{py_file}:{line_no}: {class_name}.{field_name} declares "
                f"`# axis: {category}` but the axis's all-names function "
                f"raised: {exc}"
            )
        return None
    if category == "free-text":
        if not extra.startswith("—") or not extra[1:].strip():
            return (
                f"{py_file}:{line_no}: {class_name}.{field_name} declares "
                "`# axis: free-text` without a justification. Format: "
                "`# axis: free-text — <one-sentence reason no consumer "
                "branches on this value>`."
            )
        return None
    if category in _VALID_CATEGORIES_NON_AXIS:
        return None
    known_names = sorted(set(known_axes) | _VALID_CATEGORIES_NON_AXIS)
    return (
        f"{py_file}:{line_no}: {class_name}.{field_name} declares "
        f"`# axis: {category}` which is not a known category. "
        f"Valid: {', '.join(known_names)}."
    )


def find_field_drift(
    repo_root: Path,
    core_files: Iterable[str] = DEFAULT_CORE_FILES,
) -> list[str]:
    """Return a list of axis-declaration violations across *core_files*.

    Each entry is a ``"<file>:<line>: <message>"`` string. Empty list
    means agreement: every str-typed field on a core dataclass carries
    a valid ``# axis: ...`` declaration.

    Args:
        repo_root: Repo root; *core_files* paths resolved against it.
        core_files: Iterable of repo-relative paths to scan.

    Returns:
        Sorted list of offender strings (empty if agreement holds).
    """
    known_axes = _known_axes()
    offenders: list[str] = []
    for rel_path in core_files:
        py_file = repo_root / rel_path
        if not py_file.exists():  # pragma: no cover — defensive
            offenders.append(
                f"{rel_path}: configured core file does not exist on disk"
            )
            continue
        for class_name, field_name, line_no, source_line in (
            _iter_dataclass_str_fields(py_file)
        ):
            err = _check_field(
                py_file,
                class_name,
                field_name,
                line_no,
                source_line,
                known_axes,
            )
            if err is not None:
                offenders.append(err)
    return sorted(offenders)
