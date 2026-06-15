# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase D/E stage-ordering gate for run_behavior_map (WI-pozur, ADR-0043 C2).

Boundary-node synthesis (``create_boundary_nodes`` + ``apply_external_id_remap``) must
run AFTER tier+noise filtering, not before. Before this fix, synthesis ran over the
PRE-filter symbol set: a tier-4 DERIVED file (e.g. ``*_pb2.py``) was still present at
synthesis time, so its file-level outgoing edges were not yet dangling and no boundary
was minted for it; tier filtering then deleted the file while the file-level src carve-out
(``cli.py`` ``_is_valid_edge_src``) kept its edges, leaving a dangling ``src`` — the C2
defect (ADR-0043 §4). Moving synthesis after filtering closes the class by construction:
the now-dangling src is seen by ``create_boundary_nodes``, which mints a boundary and
``apply_external_id_remap`` rewrites the src onto it.

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
* **G2 dangling-source CLOSED (the fix; RED before the reorder):** zero dangling edges, and
  the previously-dangling ``*_pb2`` import edges now resolve their ``src`` to a present node.
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
})

# The tier-4 DERIVED file-level symbol that must be filtered out (the defect precondition).
PB2_FILE_ID = "python:pkg/foo_pb2.py:1-1:file:file"


def _run(tmp_path: Path) -> dict:
    for rel, content in FIXTURE_FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _first_party_stable_ids(data: dict) -> frozenset:
    return frozenset(
        n["stable_id"] for n in data["nodes"]
        if "<external>" not in n["id"] and ":unresolved" not in n["id"]
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

    def test_pb2_import_srcs_resolved(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        node_ids = {n["id"] for n in data["nodes"]}
        # The previously-dangling edges: foo_pb2's file-level imports of google.protobuf.
        proto_imports = [
            e for e in data["edges"]
            if e["type"] == "imports" and "google.protobuf" in e["dst"]
        ]
        assert len(proto_imports) >= 2, (
            f"fixture no longer exercises the *_pb2 external-import path: {proto_imports}"
        )
        unresolved = [e for e in proto_imports if e["src"] not in node_ids]
        assert not unresolved, f"*_pb2 import edges still have a dangling src: {unresolved}"


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
