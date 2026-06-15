# SPDX-License-Identifier: AGPL-3.0-or-later
"""Decision-#7 identity-neutrality gate for the py.py call-ownership fix (WI-jafat).

The reader-side fix that re-attributes a ``calls`` edge's ``src`` to the correct
enclosing same-base-name method (CHANGE A: register methods in the node-id-keyed
``func_symbol_by_node_id``; CHANGE B: keep methods out of the enclosing-function
``inner_scope``) is a **T0** change: it must change which already-minted ``node.id``
an edge endpoint points to, while changing **no symbol-identity field**
(``Symbol.id`` / ``Symbol.stable_id`` / ``Symbol.shape_id``). The producer-half hash
change (WI-gitun, ``stable_id`` carrying the enclosing function) is **T1**, deferred to
the v6 bump and explicitly NOT done here.

This gate proves the T0/T1 cut holds on the canonical WI-jafat collision substrate
(two classes sharing a ``to_dict`` method, each calling a distinct same-file helper):

* **A1 (identity-NEUTRAL — the spine):** the full node ``{stable_id}``, ``{id}`` and
  ``{shape_id}`` sets are byte-for-byte unchanged from the pre-fix tree, pinned as
  committed golden frozensets. Full-set equality is sound here because a reader-only
  fix moves no span, so every symbol's identity is genuinely invariant — any drift must
  fail loudly. (The ``campaign-r0-baseline`` tag is local-only / CI-invisible, so a
  checkout-and-diff "before" is infeasible; the committed golden is the CI-safe vehicle.
  Regenerate with ``python /tmp/gen_golden.py`` if the fixture or the Python id formula
  changes.)
* **A2 (edge-set CHANGE — required):** the asymmetric half. Edges legitimately change;
  asserting their invariance would be wrong. Instead we assert the fix *worked*: zero
  ``calls`` edges land outside their ``src`` span, and the previously-overwritten sibling
  (``Alpha.to_dict``) regains its outgoing call. This guards against a vacuous no-op
  passing A1 trivially.
* **Strawman (the gate has teeth):** a deliberately identity-mutating monkeypatch of
  ``_make_symbol_id`` (the production ``id`` minter) makes the A1 ``{id}`` set diverge
  from golden — proving a *real* T0/T1 violation in the production path would be caught,
  so a green A1 on the real fix is meaningful rather than vacuous.

No ADR-0035 amendment is needed: §5 is a producer-side per-file uniqueness gate; this is
a purely additive reader-side neutrality assertion.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

# The canonical WI-jafat collision substrate: two classes sharing a method
# short-name, each method calling a distinct same-file helper. Pre-fix, both
# ``calls`` edges resolve their src to the bare-name survivor (Beta.to_dict) and
# Alpha.to_dict's call lands out-of-span; post-fix each method owns its own call.
WIJAFAT_FIXTURE = (
    "class Alpha:\n"
    "    def to_dict(self):\n"
    "        return helper_a()\n"
    "\n"
    "class Beta:\n"
    "    def to_dict(self):\n"
    "        return helper_b()\n"
    "\n"
    "def helper_a():\n"
    "    return 1\n"
    "\n"
    "def helper_b():\n"
    "    return 2\n"
)

# Golden identity sets for the fixture above, via run_behavior_map (repo-relative,
# deterministic, tmp_path-independent). GOLDEN_IDS (node ids) and GOLDEN_SHAPE_IDS were
# captured on dev 7f8e72a22e and are UNCHANGED by the stable_id v6 bump (it touched only the
# stable_id hash); GOLDEN_STABLE_IDS was regenerated for v6 (ADR-0035 full scope-chain hash),
# same 6 symbols, new hash values. A reader-only fix cannot change any of these.
GOLDEN_STABLE_IDS = frozenset({
    "sha256:0fcbb9c686c93dae",  # helper_a
    "sha256:6d07134c1021d31f",  # Alpha
    "sha256:ba8ffea1ab1956c4",  # helper_b
    "sha256:c6933702c1cec3b9",  # Alpha.to_dict
    "sha256:fb9b04fa5f748655",  # Beta
    "sha256:fc784ee2fa5561fb",  # Beta.to_dict
})
GOLDEN_IDS = frozenset({
    "python:models.py:1-3:Alpha:class",
    "python:models.py:2-3:Alpha.to_dict:method",
    "python:models.py:5-7:Beta:class",
    "python:models.py:6-7:Beta.to_dict:method",
    "python:models.py:9-10:helper_a:function",
    "python:models.py:12-13:helper_b:function",
})
# shape_id is structural, so same-shape symbols share it: two classes, two
# methods, two functions collapse to three distinct shape_ids.
GOLDEN_SHAPE_IDS = frozenset({
    "sha256:74fe678ff5ffc768",  # both classes
    "sha256:c58a7ab960984f1c",  # both methods
    "sha256:c891ef58de2d7715",  # both functions
})


def _run(tmp_path: Path, src: str) -> dict:
    """Write ``src`` to models.py under tmp_path, run analysis, return the map."""
    (tmp_path / "models.py").write_text(src)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _identity_sets(data: dict) -> tuple[frozenset, frozenset, frozenset]:
    nodes = data["nodes"]
    return (
        frozenset(n["stable_id"] for n in nodes),
        frozenset(n["id"] for n in nodes),
        frozenset(n["shape_id"] for n in nodes),
    )


class TestIdentityNeutral:
    """A1 — the reader fix changes no symbol-identity field."""

    def test_node_identity_sets_unchanged(self, tmp_path: Path) -> None:
        data = _run(tmp_path, WIJAFAT_FIXTURE)
        stable_ids, ids, shape_ids = _identity_sets(data)
        assert stable_ids == GOLDEN_STABLE_IDS, (
            "stable_id set drifted from the pre-fix golden — the reader fix is "
            "NOT identity-neutral (a T0/T1 violation)"
        )
        assert ids == GOLDEN_IDS, "node id set drifted from the pre-fix golden"
        assert shape_ids == GOLDEN_SHAPE_IDS, "shape_id set drifted from the pre-fix golden"


class TestEdgeSetCorrected:
    """A2 — the fix actually re-attributes the edges (guards a vacuous no-op)."""

    def test_no_calls_edge_out_of_span(self, tmp_path: Path) -> None:
        data = _run(tmp_path, WIJAFAT_FIXTURE)
        spans = {
            n["id"]: (n["span"]["start_line"], n["span"]["end_line"])
            for n in data["nodes"] if n.get("span")
        }
        offenders = []
        for e in data["edges"]:
            if e["type"] != "calls":  # JSON edge-type key is "type", NOT "edge_type"
                continue
            line = e.get("line")
            if line is None or e["src"] not in spans:
                continue
            lo, hi = spans[e["src"]]
            if not (lo <= line <= hi):
                offenders.append(e)
        assert not offenders, f"calls edges land outside their src span: {offenders}"

    def test_overwritten_sibling_regains_outgoing_call(self, tmp_path: Path) -> None:
        data = _run(tmp_path, WIJAFAT_FIXTURE)
        alpha = next(
            n for n in data["nodes"]
            if n["kind"] == "method" and n["name"] == "Alpha.to_dict"
        )
        outgoing = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == alpha["id"]
        ]
        assert len(outgoing) >= 1, (
            "Alpha.to_dict has no outgoing calls edge — its call was swallowed by "
            "the bare-name survivor (Beta.to_dict)"
        )


class TestGateHasTeeth:
    """Strawman — a real identity mutation in the production path IS caught by A1."""

    def test_identity_mutation_is_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_lang_mainstream.py as pymod

        orig = pymod._make_symbol_id

        def perturbed(path: str, line: int, end_line: int, name: str, kind: str) -> str:
            sid = orig(path, line, end_line, name, kind)
            return sid + ":STRAWMAN" if kind == "method" else sid

        monkeypatch.setattr(pymod, "_make_symbol_id", perturbed)
        data = _run(tmp_path, WIJAFAT_FIXTURE)
        _stable_ids, ids, _shape_ids = _identity_sets(data)
        assert ids != GOLDEN_IDS, (
            "A1 failed to detect a deliberate identity mutation — the gate is "
            "vacuous (a green A1 on the real fix would be meaningless)"
        )
