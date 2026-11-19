# SPDX-License-Identifier: AGPL-3.0-or-later
"""The identifier NewTypes ``StableId``, ``ShapeId``, ``RepoFingerprint`` and ``NodeId``.

What is pinned, and why:

- Each name is a ``typing.NewType`` over ``str``, and the three fields it
  types (``Symbol.stable_id``, ``Symbol.shape_id``,
  ``AnalysisRun.repo_fingerprint``) resolve to it. A NewType is the
  identity at run time, so these values are the ``str`` they always were;
  the gain is static: mypy (the strict ratchet) rejects a plain ``str``
  reaching one of these slots.
- The NewType constructors are called ONLY at the minting sites listed in
  ``MINT_SITES``. The type only helps if a value is wrapped where it is
  hashed and prefixed, not wherever a caller wants mypy to stop
  complaining: ``StableId(f"...")`` anywhere else is a hand-built id that
  the factories exist to prevent. A new legitimate mint is added to the
  list on purpose, in review.
- ``NodeId`` types ``Symbol.id``, ``UsageContext.symbol_ref`` and
  ``DataModel.symbol_id``; ``Edge.src``, ``Edge.dst`` and the ``IoChain``
  endpoints stay ``str`` (see the ``NodeId`` docstring in ir.py for why).
  Its constructor is called by the
  factories in ``MINT_SITES``, by the one boundary ``read_node_id`` (whose own
  call sites are ``BOUNDARY_READS``), and at the sites in
  ``HAND_BUILT_NODE_IDS``: producers whose id a factory would emit with
  different text, kept byte-identical, each with its reason. A new entry in
  either table is a decision made in review.
- What a raw ``str`` in a ``NodeId`` slot does is not run here: the
  whole-tree mypy strict ratchet over ``packages/*/src`` (smart-test's first
  step, and CI's mypy job) counts it as a new ``arg-type`` / ``assignment``
  error and fails. These tests pin what that rule rests on: the annotations
  and the list of places allowed to call the constructor.
- The mints produce the scheme's label plus the digest the pre-NewType
  code produced: the first 16 hex digits for stable and shape ids, the full
  64-hex digest for the repo fingerprint. The label is the one derived from
  each scheme constant (``schema.value_label``); the SCIP arm has a mint and
  a label of its own.
"""
from __future__ import annotations

import ast
import collections
import hashlib
import typing
from pathlib import Path
from typing import Optional

import pytest

from hypergumbo_core.analyze.base import (
    _short_sha256,
    make_doc_symbol_ids,
    make_file_id,
    make_manifest_target_dst,
    make_module_import_dst,
    make_symbol_id,
    mint_scip_stable_id,
    mint_shape_id,
)
from hypergumbo_core.datamodels import DataModel
from hypergumbo_core.io_boundary import IoChain
from hypergumbo_core.ir import (
    AnalysisRun,
    Edge,
    NodeId,
    RepoFingerprint,
    ShapeId,
    StableId,
    Symbol,
    UsageContext,
    _canonical_external_id,
    _canonical_external_stable_id,
    read_node_id,
    relativize_node_id,
)
from hypergumbo_core.repo_fingerprint import (
    compute_repo_fingerprint,
    compute_repo_fingerprint_field,
)
from hypergumbo_core.schema import (
    REPO_FINGERPRINT_PREFIX,
    SCIP_STABLE_ID_PREFIX,
    SHAPE_ID_PREFIX,
    STABLE_ID_PREFIX,
)
from hypergumbo_core.scip.index import scip_stable_id

REPO_ROOT = Path(__file__).resolve().parents[3]

_CORE = "hypergumbo-core/src/hypergumbo_core"
_COMMON = "hypergumbo-lang-common/src/hypergumbo_lang_common"
_EXT1 = "hypergumbo-lang-extended1/src/hypergumbo_lang_extended1"

#: (path under packages/, enclosing function, NewType) of every NewType
#: constructor call in shipped source, with its number of calls. Each is
#: where a value of that kind is minted (for ``NodeId``: the factories, the
#: boundary ``read_node_id`` and the finalize rewrite ``relativize_node_id``).
MINT_SITES = {
    (f"{_CORE}/analyze/base.py", "_short_sha256", "StableId"): 1,
    (f"{_CORE}/analyze/base.py", "mint_scip_stable_id", "StableId"): 1,
    (f"{_CORE}/ir.py", "_canonical_external_stable_id", "StableId"): 1,
    (f"{_CORE}/analyze/base.py", "mint_shape_id", "ShapeId"): 1,
    (f"{_CORE}/repo_fingerprint.py", "compute_repo_fingerprint_field", "RepoFingerprint"): 1,
    (f"{_CORE}/analyze/base.py", "make_symbol_id", "NodeId"): 1,
    (f"{_CORE}/analyze/base.py", "make_file_id", "NodeId"): 1,
    (f"{_CORE}/analyze/base.py", "make_module_import_dst", "NodeId"): 1,
    (f"{_CORE}/ir.py", "_canonical_external_id", "NodeId"): 1,
    (f"{_CORE}/ir.py", "read_node_id", "NodeId"): 1,
    (f"{_CORE}/ir.py", "relativize_node_id", "NodeId"): 1,
}

_FREE_NAME = (
    "the name slot is a free string that may hold ':'; make_symbol_id would "
    "rewrite it to '.', a visible id change"
)

#: Producers that keep a hand-built node id, wrapped in ``NodeId(...)`` at the
#: site, because routing it through a factory would change its text (an id
#: that changes is a visible change and may move a stable id). Each is a
#: pre-existing defect to fix in a change that is allowed to move ids.
HAND_BUILT_NODE_IDS: dict[tuple[str, str, str], tuple[int, str]] = {
    (f"{_CORE}/linkers/event_sourcing.py", "_create_event_symbol", "NodeId"):
        (1, f"Symbol.id; an event name (\"user:created\"): {_FREE_NAME}"),
    (f"{_CORE}/linkers/_view_template_core.py", "link_via_strategies", "NodeId"):
        (1, f"Symbol.id of a template; a file name: {_FREE_NAME}"),
    (f"{_COMMON}/handlebars.py", "_make_symbol_id", "NodeId"):
        (1, f"Symbol.id; a partial name: {_FREE_NAME}"),
    (f"{_EXT1}/bibtex.py", "_make_symbol_id", "NodeId"):
        (1, f"Symbol.id; a citation key (knuth:1984): {_FREE_NAME}"),
    (f"{_EXT1}/blade.py", "_make_symbol_id", "NodeId"):
        (1, f"Symbol.id; a quoted view name (package::view): {_FREE_NAME}"),
    (
        "hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/translate.py",
        "refine_rust_kinds",
        "NodeId",
    ): (1, "rewrites the kind slot of a factory-minted id in place; re-minting "
           "from the record's fields is not guaranteed to give the same text"),
}

#: Every call of the boundary ``read_node_id``: where a ``str`` that holds a
#: node id enters the typed world.
BOUNDARY_READS: dict[tuple[str, str], tuple[int, str]] = {
    (f"{_CORE}/analyze/base.py", "synthesize_file_symbols_for_dangling_edges"):
        (1, "an edge endpoint in the make_file_id shape becomes the file node it names"),
}

_NEWTYPE_NAMES = {"StableId", "ShapeId", "RepoFingerprint", "NodeId"}


@pytest.mark.parametrize("newtype", [StableId, ShapeId, RepoFingerprint, NodeId])
def test_each_name_is_a_newtype_over_str(newtype):
    assert isinstance(newtype, typing.NewType), newtype
    assert newtype.__supertype__ is str, newtype


def test_each_retyped_field_resolves_to_its_newtype():
    symbol_hints = typing.get_type_hints(Symbol)
    assert symbol_hints["stable_id"] == Optional[StableId]
    assert symbol_hints["shape_id"] == Optional[ShapeId]
    run_hints = typing.get_type_hints(AnalysisRun)
    assert run_hints["repo_fingerprint"] == Optional[RepoFingerprint]
    create_hints = typing.get_type_hints(AnalysisRun.create)
    assert create_hints["repo_fingerprint"] == Optional[RepoFingerprint]


def _newtype_calls(
    path: Path, names: "frozenset[str] | set[str]" = frozenset(_NEWTYPE_NAMES),
) -> "collections.Counter[tuple[str, str]]":
    """(enclosing function, called name) -> calls, for each call of *names* in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: collections.Counter[tuple[str, str]] = collections.Counter()

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name in names:
                    found[(enclosing, name)] += 1
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


def _calls_in_shipped_source(names: "set[str]") -> dict[tuple[str, str, str], int]:
    packages = REPO_ROOT / "packages"
    seen: dict[tuple[str, str, str], int] = {}
    for path in sorted(packages.glob("*/src/**/*.py")):
        rel = path.relative_to(packages).as_posix()
        for (enclosing, name), count in _newtype_calls(path, names).items():
            seen[(rel, enclosing, name)] = count
    return seen


def test_newtype_constructors_are_called_only_at_the_listed_sites():
    expected = dict(MINT_SITES)
    expected.update({site: count for site, (count, _why) in HAND_BUILT_NODE_IDS.items()})
    seen = _calls_in_shipped_source(_NEWTYPE_NAMES)
    assert seen == expected, (
        f"unexpected: {sorted(set(seen.items()) - set(expected.items()))}; "
        f"missing: {sorted(set(expected.items()) - set(seen.items()))}"
    )


def test_the_boundary_is_called_only_at_the_listed_sites():
    seen = _calls_in_shipped_source({"read_node_id"})
    expected = {
        (path, fn, "read_node_id"): count
        for (path, fn), (count, _why) in BOUNDARY_READS.items()
    }
    assert seen == expected, (
        f"unexpected: {sorted(set(seen.items()) - set(expected.items()))}; "
        f"missing: {sorted(set(expected.items()) - set(seen.items()))}"
    )


def test_every_listed_exception_says_why():
    for table in (HAND_BUILT_NODE_IDS, BOUNDARY_READS):
        for site, (count, why) in table.items():
            assert count >= 1 and len(why) > 20, site
    assert not set(HAND_BUILT_NODE_IDS) & set(MINT_SITES)


def test_mint_scan_sees_nested_and_attribute_calls(tmp_path: Path):
    """Non-vacuity for the scan above: both call forms, at any depth."""
    source = tmp_path / "m.py"
    source.write_text(
        "import ir\n"
        "X = StableId('a')\n"
        "def outer():\n"
        "    def inner():\n"
        "        return ir.ShapeId('b')\n"
        "    return RepoFingerprint(str('c'))\n",
        encoding="utf-8",
    )
    assert _newtype_calls(source) == {
        ("<module>", "StableId"): 1,
        ("inner", "ShapeId"): 1,
        ("outer", "RepoFingerprint"): 1,
    }


def test_mint_scan_counts_repeated_calls_and_reads_only_the_names_given(tmp_path: Path):
    """A second hand-built wrap in an already-listed function changes the count;
    the boundary scan sees ``read_node_id`` and nothing else."""
    source = tmp_path / "m.py"
    source.write_text(
        "def f(a):\n"
        "    NodeId(a)\n"
        "    NodeId(a)\n"
        "    return read_node_id(a)\n",
        encoding="utf-8",
    )
    assert _newtype_calls(source) == {("f", "NodeId"): 2}
    assert _newtype_calls(source, {"read_node_id"}) == {("f", "read_node_id"): 1}


def _digest16(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def test_mints_produce_their_label_and_the_unchanged_digest(tmp_path: Path):
    """Each mint writes its scheme's label before the digest the pre-label
    code wrote after ``sha256:``: the relabel moved no digest."""
    assert _short_sha256("file:python:a.py") == (
        STABLE_ID_PREFIX + _digest16("file:python:a.py")
    )
    assert mint_shape_id("function:FunctionDef()") == (
        SHAPE_ID_PREFIX + _digest16("function:FunctionDef()")
    )
    assert _canonical_external_stable_id("python", "a.py", "f") == (
        STABLE_ID_PREFIX + _digest16("external:python:a.py:f")
    )
    assert mint_scip_stable_id("scip:a.rs:m") == SCIP_STABLE_ID_PREFIX + _digest16("scip:a.rs:m")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_repo_fingerprint_field(tmp_path) == (
        REPO_FINGERPRINT_PREFIX + compute_repo_fingerprint(tmp_path)
    )
    for value in (_short_sha256("x"), mint_shape_id("x"), mint_scip_stable_id("x")):
        assert type(value) is str


def test_each_mint_site_carries_its_own_label(tmp_path: Path):
    """A value minted at each site starts with that site's label, written out
    here as literals so the labels themselves are pinned."""
    assert _short_sha256("p").startswith(STABLE_ID_PREFIX)
    assert _canonical_external_stable_id("python", "a.py", "f").startswith(STABLE_ID_PREFIX)
    assert mint_shape_id("p").startswith(SHAPE_ID_PREFIX)
    assert scip_stable_id("src/lib.rs", "rust-analyzer cargo x 1.0 crate/").startswith(SCIP_STABLE_ID_PREFIX)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_repo_fingerprint_field(tmp_path).startswith(REPO_FINGERPRINT_PREFIX)


def test_the_scip_mint_and_the_tree_sitter_mint_never_agree():
    """Same hash input, two schemes: the digests match and the values do not,
    so a SCIP-arm id can never be mistaken for a tree-sitter one."""
    a, b = _short_sha256("scip:a.rs:m"), mint_scip_stable_id("scip:a.rs:m")
    assert a != b
    assert a.rpartition(":")[2] == b.rpartition(":")[2]


# --- NodeId ---------------------------------------------------------------


def test_the_node_id_fields_resolve_to_node_id_and_the_edge_endpoints_stay_str():
    assert typing.get_type_hints(Symbol)["id"] is NodeId
    assert typing.get_type_hints(UsageContext)["symbol_ref"] == Optional[NodeId]
    assert typing.get_type_hints(UsageContext.create)["symbol_ref"] == Optional[NodeId]
    assert typing.get_type_hints(DataModel)["symbol_id"] is NodeId
    edge_hints = typing.get_type_hints(Edge)
    create_hints = typing.get_type_hints(Edge.create)
    chain_hints = typing.get_type_hints(IoChain)
    for hints, names in (
        (edge_hints, ("src", "dst")),
        (create_hints, ("src", "dst")),
        (chain_hints, ("io_edge_src", "io_edge_dst")),
    ):
        for name in names:
            assert hints[name] is str, name


@pytest.mark.parametrize("factory", [
    make_symbol_id, make_file_id, make_module_import_dst, make_manifest_target_dst,
    _canonical_external_id, read_node_id, relativize_node_id,
])
def test_each_factory_returns_a_node_id(factory):
    assert typing.get_type_hints(factory)["return"] is NodeId


def test_the_doc_pair_factory_returns_a_node_id_and_a_stable_id():
    assert typing.get_type_hints(make_doc_symbol_ids)["return"] == tuple[NodeId, StableId]


def test_the_factories_emit_the_text_they_emitted_before_the_type():
    """A NewType changes no value: the factories return the same plain ``str``."""
    values = {
        make_symbol_id("python", "a.py", 1, 2, "f", "function"): "python:a.py:1-2:f:function",
        make_file_id("python", "a.py"): "python:a.py:1-1:file:file",
        make_module_import_dst("javascript", "./u"): "javascript:./u:0-0:module:module",
        make_manifest_target_dst("pkg/package.json", "./bin/cli.js", "javascript"):
            "javascript:pkg/bin/cli.js:1-1:file:file",
        _canonical_external_id("rust", "std", "Vec::new"):
            "rust:std:0-0:Vec..new:external_symbol",
        relativize_node_id(NodeId("python:/r/a.py:1-1:file:file"), "/r/"):
            "python:a.py:1-1:file:file",
    }
    for got, want in values.items():
        assert got == want
        assert type(got) is str


def test_the_boundary_returns_its_argument_itself():
    value = "python:a.py:1-2:f:function"
    assert read_node_id(value) is value
