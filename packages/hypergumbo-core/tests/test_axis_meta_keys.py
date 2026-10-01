# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical Symbol.meta / Edge.meta key registry.

Mirrors the invariant + accessor tests in ``test_symbol_kinds.py`` and
``test_evidence_types.py``, scoped to the simpler axis taxonomy
(``symbol_meta`` vs. ``edge_meta``; no ``pending_classification``
axis because meta keys are either named or not named — there's no
deferral state for a meta key).

Drift detection is intentionally out of scope here (see the module
docstring of :mod:`hypergumbo_core.axis_meta_keys` — meta-key access
shape differs from the set-literal shape that
``axis_drift.find_drift`` walks; a subscript-access drift linter is
filed as a follow-on).
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from hypergumbo_core.axis_meta_keys import (
    AXIS_EDGE_META,
    AXIS_ENTRYPOINT_META,
    AXIS_SYMBOL_META,
    META_KEYS,
    MetaKeySpec,
    VALID_AXES,
    access_mode_applicable_edge_types,
    access_mode_na_edge_types,
    all_meta_key_names,
    find_meta_key,
    is_access_mode_not_applicable,
    meta_keys_on_axis,
)
from hypergumbo_core.call_constructs import all_call_construct_values
from hypergumbo_core.edge_types import all_edge_type_names

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Registry invariants ---

def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in META_KEYS]
    assert len(names) == len(set(names))


def test_every_spec_has_a_valid_axis():
    for spec in META_KEYS:
        assert spec.axis in VALID_AXES, (
            f"{spec.name} has invalid axis {spec.axis!r}"
        )


def test_every_spec_has_nonempty_description():
    for spec in META_KEYS:
        assert spec.description, f"{spec.name} has empty description"


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        META_KEYS[0].name = "mutated"  # type: ignore[misc]


def test_valid_axes_constant_matches_module_constants():
    assert VALID_AXES == frozenset({
        AXIS_SYMBOL_META, AXIS_EDGE_META, AXIS_ENTRYPOINT_META,
    })


def test_every_axis_has_at_least_one_spec():
    """Each declared axis must have at least one registry entry — an
    empty axis section is structurally pointless and signals either a
    typo'd axis name or a botched migration."""
    for axis in VALID_AXES:
        assert meta_keys_on_axis(axis), (
            f"axis {axis!r} has zero specs in the registry"
        )


# --- Accessors ---

def test_all_meta_key_names_returns_frozenset():
    names = all_meta_key_names()
    assert isinstance(names, frozenset)
    # Spot-check well-known keys from each axis.
    assert "framework_role" in names
    assert "framework_dispatch" in names


def test_meta_keys_on_axis_returns_only_matching():
    symbol_keys = meta_keys_on_axis(AXIS_SYMBOL_META)
    assert all(spec.axis == AXIS_SYMBOL_META for spec in symbol_keys)
    names = {spec.name for spec in symbol_keys}
    # Cross-check a handful of canonical Symbol.meta fold residues.
    assert {
        "framework_role", "component_framework", "base_classes",
    } <= names


def test_meta_keys_on_axis_edge_meta_includes_fold_residues():
    edge_keys = {spec.name for spec in meta_keys_on_axis(AXIS_EDGE_META)}
    # Wave 5 (ADR-0028 Cluster C) + Wave 4 (Cluster D) + ADR-0023
    # protocol/bridge vocabularies all live on Edge.meta.
    assert {
        "framework_dispatch", "call_construct", "protocol",
        "bridge_kind", "io_primitive", "access_mode",
    } <= edge_keys


def test_linker_vocabulary_keys_registered_on_edge_meta():
    """WI-mulub: the cross-language / framework vocabularies emitted on
    ``Edge.meta`` by their linkers must be registered so the writer-contract
    and registry-drift checks can police them. These were emitted but absent
    from ``MetaKeySpec`` — websocket (client/server_framework), message_queue
    (topic/topic_type/queue_type), database_query (query_type/table_name),
    route_handler + go_cobra (handler_name), the analyzer receiver-inference
    (receiver_type_hint), and the ir.py edge-dedup pass (referring_paths)."""
    edge_keys = {spec.name for spec in meta_keys_on_axis(AXIS_EDGE_META)}
    assert {
        "client_framework", "server_framework", "topic", "topic_type",
        "queue_type", "query_type", "table_name", "handler_name",
        "receiver_type_hint", "referring_paths", "call_lines",
    } <= edge_keys


def test_meta_keys_on_axis_unknown_returns_empty():
    assert meta_keys_on_axis("not-an-axis") == ()


def test_find_meta_key_returns_spec():
    spec = find_meta_key("framework_role")
    assert spec is not None
    assert spec.name == "framework_role"
    assert spec.axis == AXIS_SYMBOL_META


def test_find_meta_key_returns_edge_meta_spec():
    spec = find_meta_key("framework_dispatch")
    assert spec is not None
    assert spec.axis == AXIS_EDGE_META


def test_find_meta_key_unknown_returns_none():
    assert find_meta_key("not-a-real-key") is None


def test_meta_key_spec_is_dataclass():
    assert dataclasses.is_dataclass(MetaKeySpec)


# --- Cross-axis hygiene ---

def test_no_meta_key_collides_with_typed_field():
    """A meta key must NOT collide with a named typed field ON THE SAME
    dataclass. The convention (per the module docstring) is that a field
    elevated to a typed slot is named on the dataclass rather than carried
    in ``meta``. A registry entry colliding with a typed field name on its
    OWN parent dataclass signals an unfinished promotion (the key was added
    to meta and the typed field was later introduced, but the meta entry
    wasn't retired) — both shapes existing simultaneously creates an
    ambiguity for consumers.

    The check is axis-aware: a ``symbol_meta`` key is checked against
    ``Symbol`` fields, an ``edge_meta`` key against ``Edge`` fields, and an
    ``entrypoint_meta`` key against ``Entrypoint`` fields. Cross-dataclass
    name reuse is NOT a collision — ``Entrypoint.meta["id"]`` is fine even
    though ``Edge`` has a typed ``id`` field, because they describe
    different records (WI-rukam introduced the third axis)."""
    from hypergumbo_core.ir import Edge, Symbol
    from hypergumbo_core.entrypoints import Entrypoint

    axis_to_fields = {
        AXIS_SYMBOL_META: {f.name for f in dataclasses.fields(Symbol)},
        AXIS_EDGE_META: {f.name for f in dataclasses.fields(Edge)},
        AXIS_ENTRYPOINT_META: {f.name for f in dataclasses.fields(Entrypoint)},
    }
    for spec in META_KEYS:
        own_fields = axis_to_fields[spec.axis]
        assert spec.name not in own_fields, (
            f"Meta key {spec.name!r} (axis {spec.axis}) collides with a "
            f"typed field on its own parent dataclass. Either retire the "
            f"meta entry or rename the typed field."
        )


def test_meta_keys_on_axis_entrypoint_meta_includes_provenance():
    """WI-rukam: the Entrypoint provenance keys live on the
    ``entrypoint_meta`` axis."""
    entrypoint_keys = {spec.name for spec in meta_keys_on_axis(AXIS_ENTRYPOINT_META)}
    assert {"id", "source", "evidence_type"} <= entrypoint_keys


# --- ADR-0038 access_mode applicability matrix (INV-tibob + WI-pusuv census) ---
#
# ADR-0038 ruling 2 declares a per-edge-type applicability matrix for
# ``access_mode``: 4 APPLICABLE (None = "missing data, fix the emitter") and
# DECLARED-N/A (None = "the question does not arise"). The matrix originally
# covered only INV-tibob's 17-type census; the WI-pusuv census (2026-07-21)
# classified the residual relationship tail after both non-relationship axes
# drained to empty, so EVERY canonical edge type is now classified (applicable
# XOR N/A). ``data_flows_to`` is N/A here (its DIRECTION lives in the
# ``data_direction`` key, ADR-0038 ruling 3, not access_mode); ``crypto_flow``
# folded into it and is no longer a registry value.

_ADR0038_APPLICABLE = frozenset({
    "calls", "references", "module_attr_ref", "event_publishes",
})
_ADR0038_NA = frozenset({
    # INV-tibob's original 17-type census:
    "contains", "decorated_by", "depends_on", "depends_on_manifest",
    "dispatches_to", "extends", "implements", "imports", "inherits",
    "overrides", "uses", "instantiates",
    # WI-pusuv census completion — the residual relationship tail (all N/A-now):
    "constrains", "data_flows_to", "defines_target", "includes", "links",
    "module_exports", "sources", "subprocess_calls", "wraps",
})


def test_access_mode_matrix_matches_adr0038_census():
    assert access_mode_applicable_edge_types() == _ADR0038_APPLICABLE
    assert access_mode_na_edge_types() == _ADR0038_NA


def test_access_mode_matrix_sets_are_disjoint():
    assert access_mode_applicable_edge_types().isdisjoint(
        access_mode_na_edge_types()
    )


def test_access_mode_matrix_edge_types_are_canonical():
    canonical = all_edge_type_names()
    assert access_mode_applicable_edge_types() <= canonical
    assert access_mode_na_edge_types() <= canonical


def test_access_mode_matrix_data_flows_to_is_na_direction_family():
    """``data_flows_to`` carries dataflow value-propagation + DIRECTION (the
    ``data_direction`` key, ADR-0038 ruling 3), NOT state access — so the
    access question does not arise and it is declared N/A (WI-pusuv census).
    ``crypto_flow`` folded into ``data_flows_to`` (Batch 7) and is no longer a
    registry value, so it is absent from both sets."""
    assert is_access_mode_not_applicable("data_flows_to") is True
    both = access_mode_applicable_edge_types() | access_mode_na_edge_types()
    assert "data_flows_to" in both
    assert "crypto_flow" not in all_edge_type_names()
    assert "crypto_flow" not in both


def test_access_mode_matrix_total_census_complete():
    """WI-pusuv census COMPLETE (2026-07-21): after both non-relationship axes
    drained to empty (endpoint_shape via WI-pumav, pending_classification via
    WI-sumik), EVERY canonical edge type is classified applicable XOR N/A —
    zero unclassified. This is the census close condition: a consumer reading
    ``access_mode=None`` can always tell "does not arise" (N/A type) from
    "missing data" (applicable type)."""
    classified = (
        access_mode_applicable_edge_types() | access_mode_na_edge_types()
    )
    assert classified == (_ADR0038_APPLICABLE | _ADR0038_NA)
    # No canonical edge type is left unclassified.
    unclassified = all_edge_type_names() - classified
    assert unclassified == set(), (
        f"WI-pusuv census must classify every edge type; unclassified: "
        f"{sorted(unclassified)}"
    )
    # And no classified value is stale (absent from the registry).
    assert classified <= all_edge_type_names()


def test_is_access_mode_not_applicable_resolver():
    # Declared-N/A → True (the dataflow gate skips these).
    assert is_access_mode_not_applicable("instantiates") is True
    assert is_access_mode_not_applicable("contains") is True
    assert is_access_mode_not_applicable("dispatches_to") is True
    # Applicable → False.
    assert is_access_mode_not_applicable("calls") is False
    assert is_access_mode_not_applicable("references") is False
    # WI-pusuv census additions → N/A (True).
    assert is_access_mode_not_applicable("subprocess_calls") is True
    assert is_access_mode_not_applicable("data_flows_to") is True
    assert is_access_mode_not_applicable("wraps") is True
    # A non-registry / unknown value → False (not declared N/A).
    assert is_access_mode_not_applicable("crypto_flow") is False
    assert is_access_mode_not_applicable("not_a_real_edge_type") is False
    # script_src was pruned from the registry (WI-pumav Batch 0, ADR-0023
    # Phase 4b'): it is no longer a registered edge type, so it is not in the
    # N/A set and the classifier treats it as unclassified → False.
    assert is_access_mode_not_applicable("script_src") is False


def test_access_mode_description_not_stale():
    """ADR-0038 flags the ``'read_write'`` example in the access_mode
    description as stale — the four-cell vocabulary is
    read / write / mutate / delete."""
    spec = find_meta_key("access_mode")
    assert spec is not None
    assert "read_write" not in spec.description
    assert "mutate" in spec.description


# --- ADR-0038 ruling 3: data_direction declared, dest_access_mode removed ---

def test_data_direction_registered_on_edge_meta():
    spec = find_meta_key("data_direction")
    assert spec is not None
    assert spec.axis == AXIS_EDGE_META


def test_dest_access_mode_removed():
    """ADR-0038 ruling 3 removes dest_access_mode entirely (the bridge
    direction it encoded now lives in data_direction)."""
    assert find_meta_key("dest_access_mode") is None


def test_valid_data_directions_vocabulary():
    from hypergumbo_core.ir import VALID_DATA_DIRECTIONS
    assert VALID_DATA_DIRECTIONS == frozenset(
        {"src_to_dst", "dst_to_src", "bidirectional"}
    )


# --- WI-toruz: the call-family scope is DECLARED, not described ---

def test_call_construct_declares_the_call_family_scope():
    """WI-toruz: ``call_construct``'s edge scope is a typed field, not prose.

    The prose said "on ``calls`` edges" and the sibling ``ref_construct``
    entry said the two keys have "distinct edge families, zero overlap".
    That was written on 2026-06-30 (INV-lajov) from a corpus measurement
    that contained no Dart or C# object creation, and it was already false
    by the five ``constructor``-on-``instantiates`` sites that landed
    2026-05-05 (audit-findings 0012 sec 4, WI-nibis Wave 4).

    A scope that lives only in a sentence cannot be checked, which is why
    it drifted. Declaring it on ``applicable_edge_types`` — the field
    ADR-0038 ruling 2 already built for exactly this — makes it
    machine-readable, and lets the live-tree producer gate assert against
    the declaration instead of against a hand-maintained literal.
    """
    spec = find_meta_key("call_construct")
    assert spec is not None
    assert spec.applicable_edge_types == frozenset({"calls", "instantiates"})


def test_call_construct_scope_is_canonical_edge_types():
    """The declared scope may only name registered relationship values."""
    spec = find_meta_key("call_construct")
    assert spec is not None
    assert spec.applicable_edge_types is not None
    assert spec.applicable_edge_types <= all_edge_type_names()


def test_call_construct_scope_covers_instantiates_deliberately():
    """``instantiates`` is in scope because ``edge_type`` is NOT a function
    of the construct: ruby emits ``calls`` for the same source construct
    (``Klass.new`` resolves to ``Klass#initialize``), while dart/csharp emit
    ``instantiates``. So ``call_construct='constructor'`` is the only
    cross-language invariant for object creation that survives the
    relationship-label disagreement, and it is NOT redundant with
    ``edge_type``. Tracked as INV-kahig; this assertion pins the reason the
    scope is two-valued so a later reader does not "tidy" it back to one.
    """
    spec = find_meta_key("call_construct")
    assert spec is not None
    assert spec.applicable_edge_types is not None
    assert "instantiates" in spec.applicable_edge_types


# --- WI-dapap: the call_construct vocabulary is an ALLOWLIST ---
#
# Was a DENYLIST of four ejected values (INV-tadup). A denylist cannot stop a
# value it has never seen: ``assignment`` shipped with no review of whether it
# belonged on this axis. And the old walk read only DICT LITERALS, so it could
# not have seen ``assignment`` even had it been denied -- that value is emitted
# as a ``call_construct=`` KEYWORD. The walk below reads every literal shape.


def _literal_values(node: ast.expr) -> list[str] | None:
    """String literals ``node`` can evaluate to, or None if it is not literal.

    A conditional of literals (``"method" if m else None``) is literal; a
    ``None`` branch is "stamp nothing" and contributes no value.
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        body = _literal_values(node.body)
        orelse = _literal_values(node.orelse)
        if body is None or orelse is None:
            return None
        return body + orelse
    return None


def _call_construct_value_nodes(tree: ast.AST) -> list[tuple[int, ast.expr]]:
    """Every expression assigned to ``call_construct`` in one module, in the
    three shapes a producer uses: ``{"call_construct": v}``,
    ``f(call_construct=v)`` and ``meta["call_construct"] = v``."""
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            # ``strict=True`` is safe: a dict literal's keys/values are always
            # parallel — ``{**expansion}`` yields a ``None`` key, not a short list.
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "call_construct":
                    found.append((node.lineno, value))
        elif isinstance(node, ast.Call):
            found.extend(
                (node.lineno, kw.value) for kw in node.keywords
                if kw.arg == "call_construct"
            )
        elif isinstance(node, ast.Assign):
            found.extend(
                (node.lineno, node.value) for target in node.targets
                if isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "call_construct"
            )
    return found


def _call_construct_literal_emits_in(tree: ast.AST, label: str) -> dict[str, list[str]]:
    """``{value: [site, ...]}`` for every LITERAL ``call_construct`` value."""
    out: dict[str, list[str]] = {}
    for lineno, value in _call_construct_value_nodes(tree):
        for literal in _literal_values(value) or []:
            out.setdefault(literal, []).append(f"{label}:{lineno}")
    return out


def _call_construct_literal_emits() -> dict[str, list[str]]:
    """The same, over every shipped module (tests excluded)."""
    out: dict[str, list[str]] = {}
    packages = REPO_ROOT / "packages"
    for path in sorted(packages.rglob("*.py")):
        if "/tests/" in str(path) or "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - source in the tree parses
            continue
        label = str(path.relative_to(packages))
        for value, sites in _call_construct_literal_emits_in(tree, label).items():
            out.setdefault(value, []).extend(sites)
    return out


def test_every_emitted_call_construct_value_is_declared() -> None:
    """A producer may stamp only a value ``call_constructs`` declares.

    WHAT THIS DOES NOT SEE: a value computed by a helper and passed through a
    variable (java's ``_java_call_construct``, cpp's member-call test,
    scip-python's ``_external_ref``). Those that flow through
    ``make_unresolved_edge`` are checked at run time by ``require_declared``;
    scip-python's, which sets ``Edge.meta`` directly, is checked by neither.
    """
    undeclared = {
        value: sites for value, sites in _call_construct_literal_emits().items()
        if value not in all_call_construct_values()
    }
    assert undeclared == {}, undeclared


def test_every_declared_call_construct_value_has_a_producer() -> None:
    """A declared value nothing emits is dead vocabulary. Delete it, or the
    allowlist starts to describe a field that no longer exists."""
    emitted = set(_call_construct_literal_emits())
    assert all_call_construct_values() - emitted == set()


@pytest.mark.parametrize("source, expected", [
    ('e = {"call_construct": "bogus"}', {"bogus"}),
    ('f(call_construct="bogus")', {"bogus"}),
    ('meta["call_construct"] = "bogus"', {"bogus"}),
    ('f(call_construct="bogus" if x else None)', {"bogus"}),
    ('f(call_construct=helper())', set()),
])
def test_the_emit_scan_reads_every_producer_shape(source, expected) -> None:
    """POSITIVE CONTROL, one per shape. A scan that silently misses a shape is
    the defect this section replaces: the old walk read dict literals only,
    and ``assignment`` is a keyword. The last case pins what the scan does NOT
    claim to read."""
    assert set(_call_construct_literal_emits_in(ast.parse(source), "x")) == expected


def test_the_live_scan_reaches_the_keyword_shape() -> None:
    """REACH on the real tree: ``assignment`` exists only as a keyword emit."""
    sites = _call_construct_literal_emits()["assignment"]
    assert any("js_ts.py" in s for s in sites)


#: Values ejected from this key, each because it named a different axis.
#: They must never be declared again; each entry records why.
_FOLDED_CALL_CONSTRUCTS = {
    # RESOLUTION STATUS — same AST shape as their unsuffixed siblings; the
    # suffix only marked whether the resolver found the callee, which is
    # already recoverable from ``dst`` (synthetic external node id vs in-repo
    # symbol id).
    "remote_external",
    "application_external",
    # A resolution MECHANISM, now on ``resolution_quality``.
    "chained_return_type",
    # An inference PATHWAY the same edge already carried as
    # ``evidence_type="interface_dispatch"`` — one value stated twice on two
    # different axes.
    "interface_dispatch",
    # The same: erlang's ?LOG_* edge carries ``evidence_type="macro_expansion"``
    # and no call construct was parsed at the site (WI-dapap).
    "macro",
    # The DETECTOR that found the call (rust's macro token-tree scan), not a
    # construct; the real construct varies per site (WI-dapap).
    "macro_body",
}


def test_no_folded_value_is_declared() -> None:
    """INV-tadup, kept as a record: the allowlist must not readmit an ejected
    value. Emitting one is already caught above, because it is undeclared."""
    assert _FOLDED_CALL_CONSTRUCTS & all_call_construct_values() == set()
