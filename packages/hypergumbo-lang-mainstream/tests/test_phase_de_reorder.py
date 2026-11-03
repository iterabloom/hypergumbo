# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase D/E stage-ordering gate for run_behavior_map (WI-pozur, ADR-0043 C2).

Boundary-node synthesis (``create_boundary_nodes`` + ``apply_external_id_remap``) must
run AFTER tier+noise filtering, not before. Before this fix, synthesis ran over the
PRE-filter symbol set: a tier-4 DERIVED file (e.g. ``*_pb2.py``) was still present at
synthesis time, so its file-level outgoing edges were not yet dangling and no boundary
was minted for it; tier filtering then deleted the file while a src carve-out kept its
edges, leaving a dangling ``src`` — the C2 defect (ADR-0043 §4).

INV-saruf (2026-11-03, owner ruling): ADR-0043 §4 says boundary nodes are minted only for
endpoints that SURVIVE filtering. So the class is closed at the filter, not by synthesis:
an edge whose src the tier filter removed is removed with it, and no
``<path>:0-0:...:external_symbol`` stand-in is ever minted for in-tree derived code. The
former carve-out kept a removed file's edges by the shape of the src id (``:file`` in it,
or ``.py:`` / ``.js:`` / ``.ts:`` anywhere in it, which is every symbol-level source in
those languages), so a derived file came back as an external node that imports, extends
and calls. Synthesis still runs after filtering; it now finds nothing from a removed file.

This is a **T0** change: it changes which boundary nodes exist (output set membership)
but is identity-NEUTRAL for surviving first-party symbols — their ``stable_id`` is
content-derived and position-independent. The producer-half ``stable_id`` hash change
(WI-gitun) is T1/v6 and explicitly NOT done here.

This test lives in the mainstream package (not core) because it exercises ``cli.py``'s
``run_behavior_map`` over **Python** fixtures, which needs the Python analyzer present at
runtime — the same reason ``test_identity_neutrality_call_resolution.py`` lives here. The
reorder itself adds no new ``cli.py`` lines (a block is relocated), so core's isolated
coverage is unaffected.

Gate (mirrors the WI-jafat asymmetric gate — survivor identity frozen WHILE the intended
set-change is asserted; committed-golden-in-test, since the ``campaign-r0-baseline`` tag is
local-only / CI-invisible):

* **G1 identity-NEUTRAL:** surviving first-party ``{stable_id}`` == committed golden frozenset.
* **G2 dangling-source CLOSED:** zero dangling edges, and the ``*_pb2`` file's edges (two
  file-level imports and the class's ``extends``) are GONE at the default tier, with no
  stand-in node for the file; a ``--max-tier 4`` run proves the fixture emits them.
* **G2-js:** the same for a derived ``*.min.js`` whose function calls first-party code (the
  knex ``lib/dialects/index.js`` shape, INV-saruf's filed instance): the symbol-level
  ``calls`` edge leaves with the file.
* **G3 no orphaned boundary nodes:** every synthetic ``<external>`` node is referenced by an edge.
* **precondition (non-vacuity):** the ``*_pb2`` file IS tier-filtered out, so the fixture
  genuinely exercises the dangling-src-prone path (a vacuous fixture would pass G2 trivially).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map

# Fixture: three first-party modules (the survivors) + one tier-4 DERIVED ``*_pb2.py`` file
# whose file-level external imports become dangling sources once it is tier-filtered.
FIXTURE_FILES = {
    "pkg/__init__.py": "",
    "pkg/app.py": (
        "from pkg import foo_pb2\n"
        "from pkg.service import Service\n"
        "\n"
        "def handler():\n"
        "    return foo_pb2.Thing()\n"
        "\n"
        "def setup():\n"
        "    return Service().run()\n"
    ),
    "pkg/service.py": (
        "class Service:\n"
        "    def run(self):\n"
        "        return self.stop()\n"
        "\n"
        "    def stop(self):\n"
        "        return 1\n"
    ),
    "pkg/models.py": (
        "class Item:\n"
        "    def total(self):\n"
        "        return 0\n"
        "\n"
        "class Order:\n"
        "    def submit(self):\n"
        "        return Item().total()\n"
    ),
    "pkg/foo_pb2.py": (
        "from google.protobuf import descriptor as _descriptor\n"
        "from google.protobuf import message as _message\n"
        "\n"
        "class Thing(_message.Message):\n"
        "    pass\n"
    ),
}

# Surviving first-party stable_ids, captured on the fixture above (via /tmp/probe_pozur.py).
# A reader-order change to boundary synthesis cannot change any of these (content-derived).
# Regenerate this frozenset if the fixture changes.
GOLDEN_FIRSTPARTY_STABLE_IDS = frozenset({
    "sha256:eabef7c9b0ca629a",  # pkg/app.py setup:function
    "sha256:43dec3a64c659abd",  # pkg/app.py file
    "sha256:29e41c02cfe5b84e",  # pkg/service.py Service:class
    "sha256:70a8e859b1ee3b9c",  # pkg/app.py handler:function
    "sha256:eb937f52b3e30564",  # pkg/models.py Order.submit:method
    "sha256:81cf3c740bec2d42",  # pkg/service.py Service.run:method
    "sha256:13474f4add8dfeb3",  # pkg/models.py Item:class
    "sha256:d89075745234437a",  # pkg/models.py Item.total:method
    "sha256:36d8981e5e413f29",  # pkg/models.py Order:class
    "sha256:4e2034fe2ff7a100",  # pkg/service.py Service.stop:method
    # WI-dagif (file-anchor:F1, node-bearing slice): service.py and models.py
    # have content nodes but no imports/calls, so they now get synthesized
    # kind="file" anchors (app.py already had one via its import edges). Proven
    # purely additive — the 10 identities above are UNCHANGED, so the
    # reorder-neutrality invariant still holds.
    "sha256:11d642e4bc07a11a",  # pkg/service.py file anchor
    "sha256:68d4dd5cd2cc022f",  # pkg/models.py file anchor
})

# The tier-4 DERIVED file-level symbol that must be filtered out (the defect precondition).
PB2_FILE_ID = "python:pkg/foo_pb2.py:1-1:file:file"


def _run(
    tmp_path: Path,
    files: dict[str, str] = FIXTURE_FILES,
    max_tier: int | None = None,
) -> dict:
    repo = tmp_path / "repo"
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    out = tmp_path / f"out_{max_tier or 'default'}.json"
    kwargs = {"max_tier": max_tier} if max_tier is not None else {}
    run_behavior_map(
        repo_root=repo, out_path=out, include_sketch_precomputed=False, **kwargs,
    )
    return json.loads(out.read_text())


def _first_party_stable_ids(data: dict) -> frozenset:
    return frozenset(
        n["stable_id"] for n in data["nodes"]
        # Exclude synthetic boundary nodes; per ADR-0036 Ruling 2 their id
        # kind-slot is now external_symbol (was :unresolved).
        if "<external>" not in n["id"] and ":external_symbol" not in n["id"]
    )


class TestIdentityNeutral:
    """G1 — the reorder changes no surviving first-party identity."""

    def test_first_party_stable_ids_unchanged(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        assert _first_party_stable_ids(data) == GOLDEN_FIRSTPARTY_STABLE_IDS, (
            "surviving first-party stable_id set drifted from golden — the reorder is "
            "NOT identity-neutral (a T0/T1 violation)"
        )


class TestDanglingSourceClosed:
    """G2 — boundary synthesis after filtering closes the dangling-src class (RED pre-fix)."""

    def test_no_dangling_edges(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        node_ids = {n["id"] for n in data["nodes"]}
        offenders = [
            e for e in data["edges"]
            if e["src"] not in node_ids or e["dst"] not in node_ids
        ]
        assert not offenders, f"dangling edges remain (src/dst not in node set): {offenders}"

    def test_pb2_edges_leave_with_the_file(self, tmp_path: Path) -> None:
        """INV-saruf: a removed file's edges are removed with it, both the
        file-level imports and the class's extends; nothing is minted for it."""
        # Reach: with tier 4 included, foo_pb2 emits the two imports and the extends.
        full = _run(tmp_path, max_tier=4)
        from_pb2 = [e for e in full["edges"] if "pkg/foo_pb2.py" in e["src"]]
        assert {e["type"] for e in from_pb2} >= {"imports", "extends"}, from_pb2
        assert sum(1 for e in from_pb2 if "google.protobuf" in e["dst"]) >= 3, from_pb2

        data = _run(tmp_path)
        assert not [n["id"] for n in data["nodes"] if "pkg/foo_pb2.py" in n["id"]], (
            "a stand-in node was minted for the tier-filtered file"
        )
        assert not [e for e in data["edges"] if "pkg/foo_pb2.py" in e["src"]], (
            "the tier-filtered file's edges survived the filter"
        )


JS_FIXTURE_FILES = {
    "package.json": '{"name": "fx", "version": "1.0.0"}\n',
    # Tier 4 by filename (minified); its function calls first-party code and the
    # file requires a first-party module: the knex lib/dialects/index.js shape.
    "lib/app.min.js": (
        '"use strict";\n'
        'const { helper } = require("./helpers");\n'
        "function pick(n) { return helper(n); }\n"
        "module.exports = { pick };\n"
    ),
    "lib/helpers.js": (
        '"use strict";\n'
        "function helper(n) { return n; }\n"
        "module.exports = { helper };\n"
    ),
}


class TestDerivedJsSourceLeavesWithItsFile:
    """G2-js — the symbol-level arm. The former fallback kept any src whose id
    contained ``.js:``, so a derived file's function came back as
    ``javascript:lib/app.min.js:0-0:pick:external_symbol`` calling first-party code."""

    def test_min_js_calls_edge_leaves_with_the_file(self, tmp_path: Path) -> None:
        full = _run(tmp_path, JS_FIXTURE_FILES, max_tier=4)
        calls = [
            e for e in full["edges"]
            if e["type"] == "calls" and "lib/app.min.js" in e["src"]
            and "lib/helpers.js" in e["dst"]
        ]
        assert calls, [e for e in full["edges"] if "app.min.js" in e["src"]]
        assert any(
            "lib/app.min.js" in n["id"] and n["supply_chain"]["tier"] == 4
            for n in full["nodes"]
        )

        data = _run(tmp_path, JS_FIXTURE_FILES)
        assert not [n["id"] for n in data["nodes"] if "lib/app.min.js" in n["id"]]
        assert not [e for e in data["edges"] if "lib/app.min.js" in e["src"]]
        node_ids = {n["id"] for n in data["nodes"]}
        assert all(e["src"] in node_ids and e["dst"] in node_ids for e in data["edges"])
        assert any(n["path"] == "lib/helpers.js" for n in data["nodes"])


class TestNoOrphanBoundaryNodes:
    """G3 — every synthetic <external> boundary node is referenced by an edge."""

    def test_no_orphan_external_nodes(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        referenced: set[str] = set()
        for e in data["edges"]:
            referenced.add(e["src"])
            referenced.add(e["dst"])
        orphans = [
            n["id"] for n in data["nodes"]
            if "<external>" in n["id"] and n["id"] not in referenced
        ]
        assert not orphans, f"orphaned boundary nodes (minted but unreferenced): {orphans}"


class TestFixturePrecondition:
    """Non-vacuity — the *_pb2 file is genuinely tier-filtered, so the gate isn't vacuous."""

    def test_pb2_file_is_tier_filtered(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        node_ids = {n["id"] for n in data["nodes"]}
        assert PB2_FILE_ID not in node_ids, (
            "foo_pb2.py file survived tier filtering — the fixture no longer sets up the "
            "dangling-src precondition (is the _pb2.py DERIVED pattern still active?)"
        )
