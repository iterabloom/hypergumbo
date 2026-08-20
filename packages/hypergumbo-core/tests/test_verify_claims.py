# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for security claim verification (ADR-0016 Phase 3 + ADR-0017 taint flow).

Covers claim YAML parsing, boundary map checking, taint-flow verdict generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hypergumbo_core.verify_claims import (
    _MAX_EVIDENCE_ROWS,
    CONFIRMING_VERDICTS,
    BoundaryCoverage,
    Claim,
    ClaimsFileError,
    ClaimVerdict,
    TaintFlowConstraint,
    compute_boundary_coverage,
    load_claims,
    load_extra_catalog_paths,
    verify_claim,
    verify_claims,
    verify_taint_claim,
)
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    BoundaryMapEntry,
    IoChain,
    load_catalog,
)
from hypergumbo_core.taint import TaintFlowFinding


def _make_boundary_map(**kwargs) -> BoundaryMap:
    """Create a BoundaryMap with specified boundaries."""
    bmap = BoundaryMap()
    for boundary, chain_count in kwargs.items():
        chains = [
            IoChain(
                boundary=boundary,
                primitive=f"test.{boundary}",
                io_edge_src=f"src:{i}",
                io_edge_dst=f"dst:{i}",
            )
            for i in range(chain_count)
        ]
        bmap.entries[boundary] = BoundaryMapEntry(
            boundary=boundary,
            chains=chains,
            primitives_used=[f"test.{boundary}"],
        )
        bmap.total_io_edges += chain_count
    return bmap


class TestLoadClaims:
    """Tests for loading claims from YAML."""

    def test_load_basic_claim(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-001",
                    "text": "No network sends",
                    "constraint": {
                        "boundary": "net_send",
                        "must_not_exist": True,
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 1
        assert claims[0].id == "SC-001"
        assert claims[0].text == "No network sends"

    def test_load_multiple_claims(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {"id": "SC-001", "text": "No net", "constraint": {"boundary": "net_send", "must_not_exist": True}},
                {"id": "SC-002", "text": "No sub", "constraint": {"boundary": "subprocess", "must_not_exist": True}},
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 2

    def test_load_empty_claims(self, tmp_path: Path) -> None:
        path = tmp_path / "claims.yaml"
        path.write_text("claims: []\n")
        claims = load_claims(path)
        assert len(claims) == 0

    def test_load_max_chains_constraint(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-003",
                    "text": "At most 5 fs writes",
                    "constraint": {
                        "boundary": "fs_write",
                        "max_chains": 5,
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert claims[0].constraint_max_chains == 5


class TestLoadExtraCatalogPaths:
    """WI-votan: claims YAML may declare project-local taint catalogs
    under a top-level ``extra_catalogs:`` key.  Relative paths resolve
    against the claims-file directory so a repo can keep its extras
    beside the claims document.
    """

    def test_no_extra_catalogs_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "claims.yaml"
        path.write_text("claims: []\n")
        sources, sinks, sanitizers, io_prims = load_extra_catalog_paths(path)
        assert sources == []
        assert sinks == []
        assert sanitizers == []
        # INV-fotav added this slot; an unasserted tuple element is a slot
        # nobody notices going wrong.
        assert io_prims == []

    def test_relative_paths_resolve_against_claims_directory(
        self, tmp_path: Path,
    ) -> None:
        claims_dir = tmp_path / "security"
        claims_dir.mkdir()
        path = claims_dir / "claims.yaml"
        path.write_text(
            "claims: []\n"
            "extra_catalogs:\n"
            "  sources: [taint/project_sources.yaml]\n"
            "  sinks: [taint/sinks_dir]\n"
            "  sanitizers: [/abs/sanitizers.yaml]\n"
        )
        sources, sinks, sanitizers, io_prims = load_extra_catalog_paths(path)
        assert sources == [claims_dir / "taint/project_sources.yaml"]
        assert sinks == [claims_dir / "taint/sinks_dir"]
        # Absolute paths are preserved as-is.
        assert sanitizers == [Path("/abs/sanitizers.yaml")]

    def test_non_list_entries_are_ignored(self, tmp_path: Path) -> None:
        """A malformed ``extra_catalogs`` entry (dict instead of list)
        parses into an empty list rather than raising — the CLI layer
        decides whether to fail hard; parsing is lenient.
        """
        path = tmp_path / "claims.yaml"
        path.write_text(
            "claims: []\n"
            "extra_catalogs:\n"
            "  sources: {not: a list}\n"
            "  sinks: null\n"
        )
        sources, sinks, sanitizers, io_prims = load_extra_catalog_paths(path)
        assert sources == []
        assert sinks == []
        assert sanitizers == []

    def test_non_string_entries_are_skipped(self, tmp_path: Path) -> None:
        """Items that are not strings inside a path list are dropped
        (e.g. a user accidentally writing ``[42, 'sinks.yaml']``).
        """
        path = tmp_path / "claims.yaml"
        path.write_text(
            "claims: []\n"
            "extra_catalogs:\n"
            "  sources:\n"
            "    - 42\n"
            "    - project_sources.yaml\n"
        )
        sources, _sinks, _san, _io = load_extra_catalog_paths(path)
        assert sources == [tmp_path / "project_sources.yaml"]


class TestVerifyClaim:
    """Tests for verifying individual claims against boundary maps."""

    def test_must_not_exist_confirmed(self) -> None:
        """Claim passes when boundary has no chains."""
        bmap = _make_boundary_map(fs_read=5)  # no net_send
        claim = Claim(
            id="SC-001",
            text="No network sends",
            constraint_boundary="net_send",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"

    def test_must_not_exist_violated(self) -> None:
        """Claim fails when forbidden boundary has chains."""
        bmap = _make_boundary_map(fs_read=5, net_send=3)
        claim = Claim(
            id="SC-001",
            text="No network sends",
            constraint_boundary="net_send",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 3

    def test_max_chains_confirmed(self) -> None:
        """Claim passes when chain count is within limit."""
        bmap = _make_boundary_map(fs_write=3)
        claim = Claim(
            id="SC-002",
            text="At most 5 fs writes",
            constraint_boundary="fs_write",
            constraint_max_chains=5,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"

    def test_max_chains_violated(self) -> None:
        """Claim fails when chain count exceeds limit."""
        bmap = _make_boundary_map(fs_write=10)
        claim = Claim(
            id="SC-002",
            text="At most 5 fs writes",
            constraint_boundary="fs_write",
            constraint_max_chains=5,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 10

    def test_no_constraint_returns_inconclusive(self) -> None:
        """ADR-0033 Phase 3 PR4 / WI-rolol sub-task A: a claim with no
        machine-checkable constraint returns ``inconclusive`` rather
        than silently falling through to ``confirmed`` (INV-bitig P0).
        """
        bmap = _make_boundary_map(fs_read=5)
        claim = Claim(
            id="SC-X",
            text="No constraint",
            constraint_boundary="fs_read",
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "inconclusive"
        assert "No machine-checkable" in verdict.details

    def test_must_not_exist_empty_map_is_inconclusive(self) -> None:
        """INV-bitig P0: an empty boundary map (no I/O edges at all) means the
        analysis saw nothing — a must_not_exist claim is INCONCLUSIVE, not a
        silent ``confirmed``. (Was test_must_not_exist_no_boundary_data, which
        codified the bug as expected behavior; flipped when WI-kajil landed.)
        """
        bmap = BoundaryMap()  # empty: total_io_edges == 0
        claim = Claim(
            id="SC-001",
            text="No subprocess",
            constraint_boundary="subprocess",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "inconclusive"


class TestVerifyClaims:
    """Tests for batch claim verification."""

    def test_all_confirmed(self) -> None:
        bmap = _make_boundary_map(fs_read=5)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
            Claim(id="SC-002", text="No sub", constraint_boundary="subprocess", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        assert len(verdicts) == 2
        assert all(v.verdict == "confirmed" for v in verdicts)

    def test_mixed_verdicts(self) -> None:
        bmap = _make_boundary_map(fs_read=5, net_send=2)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
            Claim(id="SC-002", text="No sub", constraint_boundary="subprocess", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        assert verdicts[0].verdict == "violated"
        assert verdicts[1].verdict == "confirmed"

    def test_to_dict(self) -> None:
        bmap = _make_boundary_map(net_send=2)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        d = verdicts[0].to_dict()
        assert d["claim_id"] == "SC-001"
        assert d["verdict"] == "violated"
        assert d["evidence_count"] == 2


# ---------------------------------------------------------------------------
# Tests — Taint-flow claims (ADR-0017)
# ---------------------------------------------------------------------------


def _make_finding(
    taint_label: str = "plaintext",
    sink_zone: str = "host_fs",
    sanitized: bool = False,
    source_prim: str = "decrypt",
    sink_prim: str = "write",
) -> TaintFlowFinding:
    """Create a minimal TaintFlowFinding."""
    return TaintFlowFinding(
        taint_label=taint_label,
        source_symbol="src:1",
        source_primitive=source_prim,
        sink_symbol="dst:1",
        sink_primitive=sink_prim,
        sink_zone=sink_zone,
        sanitized=sanitized,
        confidence="approximate",
        analysis_method="structural",
    )


class TestTaintFlowConstraint:
    """Tests for TaintFlowConstraint dataclass."""

    def test_default_allowed_sanitizers(self) -> None:
        tf = TaintFlowConstraint(
            source_taint="plaintext",
            prohibited_sink_zone="host_fs",
        )
        assert tf.allowed_sanitizers == []

    def test_explicit_allowed_sanitizers(self) -> None:
        tf = TaintFlowConstraint(
            source_taint="plaintext",
            prohibited_sink_zone="host_fs",
            allowed_sanitizers=["Fernet.encrypt"],
        )
        assert tf.allowed_sanitizers == ["Fernet.encrypt"]


class TestLoadTaintFlowClaims:
    """Tests for loading claims with taint_flow constraints from YAML."""

    def test_load_taint_flow_claim(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "TF-001",
                    "text": "Plaintext must not reach host filesystem",
                    "constraint": {
                        "taint_flow": {
                            "source_taint": "plaintext",
                            "prohibited_sink_zone": "host_fs",
                            "allowed_sanitizers": ["Fernet.encrypt"],
                        },
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 1
        assert claims[0].constraint_taint_flow is not None
        tf = claims[0].constraint_taint_flow
        assert tf.source_taint == "plaintext"
        assert tf.prohibited_sink_zone == "host_fs"
        assert tf.allowed_sanitizers == ["Fernet.encrypt"]

    def test_load_mixed_claims(self, tmp_path: Path) -> None:
        """Both boundary and taint-flow claims in the same file."""
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-001",
                    "text": "No net sends",
                    "constraint": {"boundary": "net_send", "must_not_exist": True},
                },
                {
                    "id": "TF-001",
                    "text": "No plaintext to disk",
                    "constraint": {
                        "taint_flow": {
                            "source_taint": "plaintext",
                            "prohibited_sink_zone": "host_fs",
                        },
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert claims[0].constraint_taint_flow is None
        assert claims[1].constraint_taint_flow is not None


class TestVerifyTaintClaim:
    """Tests for taint-flow claim verification."""

    def test_no_findings_confirmed(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        verdict = verify_taint_claim(claim, [])
        assert verdict.verdict == "confirmed"
        assert "No unsanitized" in verdict.details

    def test_violation_found(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding()]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert "approximate" in verdict.details

    def test_sanitized_finding_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(sanitized=True)]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_wrong_taint_label_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(taint_label="key_material")]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_wrong_zone_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(sink_zone="network")]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_no_taint_flow_constraint_returns_inconclusive(self) -> None:
        """ADR-0033 Phase 3 PR4 / WI-rolol sub-task A: a claim without a
        taint_flow constraint returns ``inconclusive`` rather than
        silently falling through to ``confirmed``.
        """
        claim = Claim(id="X", text="X")
        verdict = verify_taint_claim(claim, [])
        assert verdict.verdict == "inconclusive"
        assert "No taint_flow constraint" in verdict.details

    def test_multiple_violations_truncated(self) -> None:
        """More than 5 violations shows truncated list."""
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [
            _make_finding(source_prim=f"decrypt{i}", sink_prim=f"write{i}")
            for i in range(7)
        ]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 7
        assert "and 2 more" in verdict.details


def _flow(
    *,
    source_symbol: str,
    sink_symbol: str,
    source_prim: str = "cmd_run",
    sink_prim: str = "replace",
    path: list[str] | None = None,
    confidence: str = "approximate",
    analysis_method: str = "structural",
) -> TaintFlowFinding:
    """A violating (unsanitized plaintext -> host_fs) finding with explicit
    symbol identity, for the WI-kikis drill-down evidence tests."""
    return TaintFlowFinding(
        taint_label="plaintext",
        source_symbol=source_symbol,
        source_primitive=source_prim,
        sink_symbol=sink_symbol,
        sink_primitive=sink_prim,
        sink_zone="host_fs",
        sanitized=False,
        confidence=confidence,
        analysis_method=analysis_method,
        path=path if path is not None else [],
    )


class TestConfidenceReachesTheConsumer:
    """INV-karud (c): the adjudication a verdict rests on must be readable.

    ``verify_taint_claim`` hardcoded the string ``approximate`` into every
    violated verdict's ``details``, and ``_flow_evidence_dict`` dropped
    ``confidence`` / ``analysis_method`` entirely. So the ADR-0017 §3a walk
    could adjudicate a flow by data dependence and the record would still say
    "approximate" — the label was produced and then discarded, which is the
    same provenance discontinuity the walk itself was built to close.

    The aggregation policy is STATED, not left to ``max()``: a verdict is a
    disjunction over its flows and they can be adjudicated differently, so the
    composition is reported rather than collapsed. Collapsing upward would
    claim a precision most flows did not have; collapsing downward would hide
    the flows that earned it.
    """

    def _claim(self) -> Claim:
        return Claim(
            id="TF-CONF",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )

    def test_uniform_approximate_stays_terse(self) -> None:
        """The common case reads exactly as it did — no count noise."""
        verdict = verify_taint_claim(
            self._claim(), [_flow(source_symbol="s", sink_symbol="d")],
        )
        assert "confidence: approximate]" in verdict.details

    def test_uniform_precise_says_precise(self) -> None:
        """A verdict resting entirely on DDG-adjudicated flows says so.

        This is the assertion the hardcoded literal made impossible: before,
        a fully data-dependence-confirmed verdict still printed "approximate".
        """
        verdict = verify_taint_claim(
            self._claim(),
            [_flow(source_symbol="s", sink_symbol="d",
                   confidence="precise", analysis_method="ddg")],
        )
        assert "confidence: precise]" in verdict.details
        assert "approximate" not in verdict.details

    def test_mixed_confidence_reports_the_composition(self) -> None:
        """Neither collapsed upward nor downward — both counts are stated."""
        findings = [
            _flow(source_symbol="s1", sink_symbol="d1",
                  confidence="precise", analysis_method="ddg"),
            _flow(source_symbol="s2", sink_symbol="d2"),
            _flow(source_symbol="s3", sink_symbol="d3"),
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert "confidence: 2 approximate, 1 precise]" in verdict.details

    def test_analysis_methods_breakdown_is_machine_readable(self) -> None:
        """A consumer must not have to parse ``details`` to get the split.

        Mirrors ``flow_origins`` (WI-vazal): the structured field is the
        contract, the prose is a courtesy. ``analysis_method`` is the finer
        axis — it separates ``ddg_mixed`` (the walk ran and did not confirm)
        from ``structural`` (no walk was possible), which ``confidence``
        collapses into one bucket.
        """
        findings = [
            _flow(source_symbol="s1", sink_symbol="d1",
                  confidence="precise", analysis_method="ddg"),
            _flow(source_symbol="s2", sink_symbol="d2",
                  analysis_method="ddg_mixed"),
            _flow(source_symbol="s3", sink_symbol="d3"),
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.analysis_methods == {
            "ddg": 1, "ddg_mixed": 1, "structural": 1,
        }

    def test_breakdown_counts_every_flow_not_just_shown_rows(self) -> None:
        """The breakdown denominator matches ``evidence_count``.

        ``evidence`` is deduplicated and capped; ``details`` counts every
        violation. A breakdown computed over the shown rows would silently
        disagree with the count printed beside it.
        """
        findings = [
            _flow(source_symbol="s", sink_symbol="d",
                  confidence="precise", analysis_method="ddg")
        ] * 5
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.evidence_count == 5
        assert len(verdict.evidence) == 1
        assert verdict.analysis_methods == {"ddg": 5}

    def test_confirmed_verdict_has_no_breakdown(self) -> None:
        """No counted flows, nothing to break down — an empty dict, not zeros."""
        verdict = verify_taint_claim(self._claim(), [])
        assert verdict.verdict == "confirmed"
        assert verdict.analysis_methods == {}

    def test_breakdown_survives_serialization(self) -> None:
        """``to_dict`` is the only surface the CLI emits — a field it drops
        does not exist for any consumer.

        This is the failure mode the whole clause is about: the pipeline
        computes an adjudication and a later layer discards it. A dataclass
        field that ``to_dict`` omits is discarded just as completely as one
        that was never computed, and is harder to notice because every
        in-process test still sees it.
        """
        verdict = verify_taint_claim(
            self._claim(),
            [_flow(source_symbol="s", sink_symbol="d",
                   confidence="precise", analysis_method="ddg")],
        )
        assert verdict.to_dict()["analysis_methods"] == {"ddg": 1}


class TestSanitizedFlowsAreDisclosed:
    """A sanitized flow is counted and reported, not pruned into silence.

    ``TaintFlowFinding.sanitized`` was written ``False`` at both and only
    construction sites, so ``and not f.sanitized`` here was a tautology and the
    ``confirmed_safe`` branch of the ``verdict`` property was unreachable in
    production. The propagators now emit sanitized flows labelled (owner ruling
    2026-08-03), which makes the filter real — and what a filter removes has to
    be counted, or the confirmed verdict reads identically whether the code is
    safe by construction or safe because of one ``encrypt()`` call.
    """

    def _claim(self) -> Claim:
        return Claim(
            id="TF-SAN",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )

    def _sanitized(self) -> TaintFlowFinding:
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        return f

    def test_sanitized_flow_does_not_violate(self) -> None:
        """Non-destructiveness: a sanitized flow still does not count."""
        verdict = verify_taint_claim(self._claim(), [self._sanitized()])
        assert verdict.verdict == "confirmed"
        assert verdict.evidence_count == 0

    def test_confirmed_verdict_says_what_is_holding_it(self) -> None:
        """...and the reader learns a sanitizer is why it reads clean.

        This is the case the silent prune served worst: the claim looked safe
        by construction when it was really safe by one function call.
        """
        verdict = verify_taint_claim(self._claim(), [self._sanitized()])
        assert verdict.sanitized_flows == 1
        assert "pass through a sanitizer on every route" in verdict.details

    def test_a_repo_supplied_sanitizer_is_named_and_marked(self) -> None:
        """INV-pojib — the verdict must say WHOSE sanitizer made it clean.

        MEASURED at dev adfaaeebf2 on the shipped CLI. An 11-line fixture whose
        only statements are ``secret = os.environ["API_KEY"]``,
        ``safe = launder(secret)`` and ``os.remove(safe)`` — where ``launder``
        returns its argument unchanged — went from ``violated`` rc 1 to
        ``confirmed`` rc 0 when handed an 8-line sanitizer file asserting that
        ``h.launder`` transforms host_secret to ciphertext. The same lie shipped
        INSIDE the tree via the claims file's ``extra_catalogs:`` block gives the
        same rc 0 with no flag passed by whoever runs the tool.

        INV-zosun's disclosure names the INPUT ("computed against USER-SUPPLIED
        catalogue input"). It never links that input to the EFFECT: the clause
        was built from the sanitized-flow COUNT alone, so a lying repo-supplied
        entry and a legitimate built-in ``Fernet.encrypt`` produced BYTE-IDENTICAL
        verdict text, and rc was 0 either way.

        This is not a lab case. hypergumbo's own self-proof declares its
        zone-barrier sanitizer through ``extra_catalogs:``, and that sanitizer is
        load-bearing by design — so the tool's own safety artifact is exactly the
        shape that must not read as an unaided clean verdict.
        """
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        f.sanitized_by = ("h.launder",)
        f.sanitized_by_user_supplied = ("h.launder",)
        verdict = verify_taint_claim(self._claim(), [f])
        # CONFIRMING, not the literal "confirmed". This assertion used to read
        # `== "confirmed"`, which was right for remedy (a1) — it deliberately
        # changed only the prose — and became wrong the moment (b)/(c) made the
        # VALUE carry the same fact. What this test is about is that the flow
        # is not counted against the claim; which confirming value it lands on
        # is `TestARepoSuppliedSanitizerReachesTheExitCode`'s business.
        assert verdict.verdict in CONFIRMING_VERDICTS
        assert "h.launder" in verdict.details, (
            f"the sanitizer holding the verdict up must be NAMED, or the reader "
            f"cannot check it against the source; got: {verdict.details!r}"
        )
        assert "project-local" in verdict.details, (
            f"and it must be marked as supplied by the analysed repository, "
            f"which is the whole distinction INV-pojib is about; got: "
            f"{verdict.details!r}"
        )

    def test_a_built_in_sanitizer_is_named_but_not_marked_project_local(
        self,
    ) -> None:
        """THE DISCRIMINATOR. If every sanitized verdict said "project-local",
        the marking would carry no information and a reader would learn to
        discount it — which is the failure mode this fix exists to correct, not
        a milder version of it.
        """
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        f.sanitized_by = ("cryptography.fernet.Fernet.encrypt",)
        verdict = verify_taint_claim(self._claim(), [f])
        assert "Fernet.encrypt" in verdict.details
        assert "project-local" not in verdict.details, (
            f"a shipped-catalogue sanitizer is not repo-supplied; got: "
            f"{verdict.details!r}"
        )

    def test_several_candidate_sanitizers_are_not_reported_as_one(self) -> None:
        """THE BARRIER RECORDS WHAT COULD HAVE FIRED, NOT WHAT DID.

        All four shipped ``*.encrypt`` sanitizers match a bare ``encrypt``
        callee, so a fixture calling ``Fernet.encrypt`` has four candidates. The
        first version of this attribution named ONE of them — and named the
        wrong one, printing ``ChaCha20Poly1305.encrypt`` for a Fernet call,
        because the registry kept a single slot per (caller, label) and the last
        short-name match overwrote the rest.

        That was caught by running the live discriminator AFTER the unit tests
        were already green, which is why this test exists: naming a sanitizer
        the analysis cannot single out is a new way of stating something it
        never established, and it is worse than the unattributed clause it
        replaced — a reader who checks the named function against the source
        will not find it.
        """
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        f.sanitized_by = (
            "cryptography.fernet.Fernet.encrypt",
            "cryptography.hazmat.primitives.ciphers.aead.AESGCM.encrypt",
        )
        verdict = verify_taint_claim(self._claim(), [f])
        assert "one of" in verdict.details, (
            f"with several candidates the wording must not imply a single "
            f"sanitizer; got: {verdict.details!r}"
        )
        assert "via cryptography" not in verdict.details, (
            f"'via X' asserts X fired, which is exactly what is not known "
            f"here; got: {verdict.details!r}"
        )
        for name in f.sanitized_by:
            assert name in verdict.details, f"{name} missing from the candidates"

    def test_an_unsanitized_flow_contributes_no_attribution(self) -> None:
        """A claim can be violated AND carry sanitized flows. Only the SANITIZED
        ones may name a sanitizer — attributing a protection to a flow that
        reached the sink unprotected would invert the finding.
        """
        clean = _flow(source_symbol="s", sink_symbol="d")
        clean.sanitized = True
        clean.sanitized_by = ("proj.launder",)
        clean.sanitized_by_user_supplied = ("proj.launder",)
        leaky = _flow(source_symbol="s2", sink_symbol="d2")
        leaky.sanitized_by = ("never.called",)
        verdict = verify_taint_claim(self._claim(), [clean, leaky])
        assert verdict.verdict == "violated"
        assert "never.called" not in verdict.details, (
            f"an unsanitized flow's candidate list must not be reported as "
            f"protecting anything; got: {verdict.details!r}"
        )

    def test_zero_when_nothing_was_sanitized(self) -> None:
        """No sanitizer anywhere reports 0 and adds no prose."""
        verdict = verify_taint_claim(
            self._claim(), [_flow(source_symbol="s", sink_symbol="d")],
        )
        assert verdict.sanitized_flows == 0
        assert "sanitizer on every route" not in verdict.details

    def test_counted_alongside_a_real_violation(self) -> None:
        """A claim can be violated AND have sanitized flows; both are reported."""
        verdict = verify_taint_claim(
            self._claim(),
            [self._sanitized(), _flow(source_symbol="s2", sink_symbol="d2")],
        )
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert verdict.sanitized_flows == 1

    def test_count_survives_serialization(self) -> None:
        """``to_dict`` is the only surface the CLI emits."""
        verdict = verify_taint_claim(self._claim(), [self._sanitized()])
        assert verdict.to_dict()["sanitized_flows"] == 1

    def test_only_this_claims_flows_are_counted(self) -> None:
        """A sanitized flow for a DIFFERENT label/zone is not this claim's.

        Guards the obvious way to get this wrong — counting sanitized findings
        over the whole list rather than over the ones the claim constrains.
        """
        other = _flow(source_symbol="s", sink_symbol="d")
        other.sanitized = True
        other.taint_label = "untrusted_input"
        verdict = verify_taint_claim(self._claim(), [other])
        assert verdict.sanitized_flows == 0


class TestViolatedPathDisclosure:
    """WI-bifob's contract, on the path that never implemented it.

    The item's own stated contract is that exclusions are disclosed "on the
    CONFIRMED path as well as the violated one". Only the confirmed path did
    it: on the violated path ``excluded_flows`` was attached to the dataclass
    and never rendered, and ``cli.py`` prints ``details`` alone — so a
    text-mode reader of a violated claim never learned that flows had been set
    aside by a policy they did not choose. ``flow_origins`` (WI-vazal) reached
    no text surface at all, on either path.
    """

    def _claim(self) -> Claim:
        return Claim(
            id="TF-DISC",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )

    def test_violated_verdict_discloses_exclusions_in_text(self) -> None:
        """A text reader of a VIOLATED claim learns what was set aside."""
        findings = [
            _flow(source_symbol="python:src/app.py:1-2:f:function",
                  sink_symbol="d"),
            _flow(source_symbol="python:tests/test_a.py:1-2:g:function",
                  sink_symbol="d2"),
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.verdict == "violated"
        assert verdict.excluded_flows == {"test_sourced": 1}
        assert "Excluded from this verdict as non-production" in verdict.details
        assert "1 test_sourced" in verdict.details
        assert "--include-non-production-sources" in verdict.details

    def test_violated_verdict_reports_flow_origins_in_text(self) -> None:
        """The WI-vazal breakdown reaches the only surface text mode prints.

        Measured need, not a hypothetical: all 140 flows on pretix's largest
        violated claim are database-read-to-database-write, which changes what
        a reader does about it — and no text consumer could see that.
        """
        f1 = _flow(source_symbol="s1", sink_symbol="d1")
        f1.source_boundary = "db_read"
        f2 = _flow(source_symbol="s2", sink_symbol="d2")
        f2.source_boundary = "net_recv"
        verdict = verify_taint_claim(self._claim(), [f1, f2])
        assert "[origins: 1 db_read, 1 net_recv]" in verdict.details

    def test_origins_bucket_for_declared_sources(self) -> None:
        """A YAML-declared source has no boundary and says so, not ""."""
        verdict = verify_taint_claim(
            self._claim(), [_flow(source_symbol="s", sink_symbol="d")],
        )
        assert "[origins: 1 declared]" in verdict.details

    def test_benchmark_source_is_not_called_a_test(self) -> None:
        """Owner ruling: keep ``is_test_file`` broad, disclose which rule fired.

        Before the split this reported ``test_sourced``, which is a false
        statement about a ``benches/`` path — the reader would go looking for a
        test that does not exist.
        """
        findings = [
            _flow(source_symbol="python:src/app.py:1-2:f:function",
                  sink_symbol="d"),
            _flow(source_symbol="rust:benches/bench_io.rs:1-2:g:function",
                  sink_symbol="d2"),
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.excluded_flows == {"benchmark_sourced": 1}

    def test_exclusion_buckets_separate_by_reason(self) -> None:
        """Each rule gets its own bucket rather than one undifferentiated pile."""
        findings = [
            _flow(source_symbol="python:src/app.py:1-2:p:function", sink_symbol="d"),
            _flow(source_symbol="python:tests/test_a.py:1-2:a:function", sink_symbol="d1"),
            _flow(source_symbol="go:benches/b.go:1-2:b:function", sink_symbol="d2"),
            _flow(source_symbol="go:testdata/c.go:1-2:c:function", sink_symbol="d3"),
            _flow(source_symbol="go:mocks/d.go:1-2:d:function", sink_symbol="d4"),
            _flow(source_symbol="python:app/migrations/0001_x.py:1-2:e:function",
                  sink_symbol="d5"),
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.excluded_flows == {
            "test_sourced": 1,
            "benchmark_sourced": 1,
            "fixture_sourced": 1,
            "mock_sourced": 1,
            "migration_sourced": 1,
        }
        # ...and the production flow is still counted, which is the control:
        # a split that quietly widened the exclusion would look identical in
        # the bucket dict alone.
        assert verdict.evidence_count == 1

    def test_split_does_not_change_which_flows_are_excluded(self) -> None:
        """Non-destructiveness: the same paths are excluded, only named better.

        ``classify_test_file`` IS ``is_test_file`` (the boolean is defined as
        "reason is not None"), so the population cannot move. Asserted rather
        than assumed, because "we only changed the label" is exactly the claim
        that turns out to be false.
        """
        paths = [
            "python:tests/a.py:1-2:f:function",
            "rust:benches/b.rs:1-2:f:function",
            "go:testdata/c.go:1-2:f:function",
            "go:mocks/d.go:1-2:f:function",
            "python:src/app.py:1-2:f:function",
        ]
        findings = [
            _flow(source_symbol=p, sink_symbol=f"d{i}")
            for i, p in enumerate(paths)
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert sum(verdict.excluded_flows.values()) == 4
        assert verdict.evidence_count == 1


class TestViolatedFlowEvidence:
    """WI-kikis: a violated taint claim must surface per-flow drill-down
    evidence (symbol IDs + call-graph path) and deduplicate identical-looking
    rows, instead of collapsing every flow to a bare ``primitive -> primitive``
    pair (which, at high counts, rendered as verbatim duplicates hiding 99%+
    of the evidence)."""

    def _claim(self) -> Claim:
        return Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )

    def test_details_row_carries_symbol_ids(self) -> None:
        verdict = verify_taint_claim(
            self._claim(),
            [_flow(source_symbol="pkg.a:cmd_run", sink_symbol="pkg.b:replace")],
        )
        assert "pkg.a:cmd_run" in verdict.details
        assert "pkg.b:replace" in verdict.details

    def test_identical_primitive_rows_distinguished_by_symbol(self) -> None:
        # Same primitive names, DIFFERENT symbols: these are distinct flows that
        # previously rendered as verbatim duplicates ("cmd_run -> replace" x N).
        findings = [
            _flow(source_symbol=f"s{i}", sink_symbol=f"d{i}") for i in range(3)
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        for i in range(3):
            assert f"s{i}" in verdict.details
            assert f"d{i}" in verdict.details

    def test_verbatim_duplicate_flows_collapse(self) -> None:
        # Truly identical findings (same symbols + path) are one distinct flow.
        f = _flow(source_symbol="s", sink_symbol="d")
        verdict = verify_taint_claim(self._claim(), [f, f, f, f, f])
        assert verdict.evidence_count == 5          # total flows still reported
        assert len(verdict.evidence) == 1           # one distinct flow
        assert "(1 distinct)" in verdict.details    # honest total-vs-distinct

    def test_structured_evidence_has_drilldown_keys(self) -> None:
        verdict = verify_taint_claim(
            self._claim(),
            [_flow(source_symbol="s", sink_symbol="d", path=["s", "mid", "d"])],
        )
        assert verdict.evidence == [
            {
                "source_symbol": "s",
                "source_primitive": "cmd_run",
                # WI-joruv: the catalog entry's declared module travels with
                # the row so a match is checkable without re-running the
                # matcher. Empty here because the fixture builds a finding
                # directly rather than through a propagator.
                "source_module": "",
                # WI-vazal: the io_primitives boundary the source came from,
                # so a reader can separate a database read from a socket read
                # when both carry the label `untrusted_input`. Empty here for
                # the same reason as source_module — the fixture builds the
                # finding directly rather than through a propagator.
                "source_boundary": "",
                "sink_symbol": "d",
                "sink_primitive": "replace",
                "sink_module": "",
                "path": ["s", "mid", "d"],
                # INV-karud (c): HOW this flow was adjudicated travels with the
                # row. Without it the reader cannot tell a data-dependence-
                # confirmed flow from one included by call reachability alone,
                # and the ADR-0017 §3a walk is unobservable from the record.
                "confidence": "approximate",
                "analysis_method": "structural",
            }
        ]

    def test_path_hops_shown_in_details(self) -> None:
        verdict = verify_taint_claim(
            self._claim(),
            [_flow(source_symbol="s", sink_symbol="d", path=["s", "m1", "m2", "d"])],
        )
        assert "via 2 hop(s)" in verdict.details

    def test_structured_evidence_is_bounded(self) -> None:
        findings = [
            _flow(source_symbol=f"s{i}", sink_symbol=f"d{i}")
            for i in range(_MAX_EVIDENCE_ROWS + 25)
        ]
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.evidence_count == _MAX_EVIDENCE_ROWS + 25
        assert len(verdict.evidence) == _MAX_EVIDENCE_ROWS

    def test_to_dict_includes_evidence(self) -> None:
        d = verify_taint_claim(
            self._claim(), [_flow(source_symbol="s", sink_symbol="d")]
        ).to_dict()
        assert "evidence" in d
        assert d["evidence"][0]["source_symbol"] == "s"
        assert d["evidence"][0]["sink_symbol"] == "d"


class TestVerifyClaimsMixed:
    """Tests for mixed boundary + taint-flow claim verification."""

    def test_mixed_claims_routing(self) -> None:
        """Boundary claims use boundary_map, taint claims use findings."""
        bmap = _make_boundary_map(net_send=2)
        findings = [_make_finding()]
        claims = [
            Claim(
                id="SC-001", text="No net",
                constraint_boundary="net_send",
                constraint_must_not_exist=True,
            ),
            Claim(
                id="TF-001", text="No plaintext to disk",
                constraint_taint_flow=TaintFlowConstraint(
                    source_taint="plaintext",
                    prohibited_sink_zone="host_fs",
                ),
            ),
        ]
        verdicts = verify_claims(claims, bmap, taint_findings=findings)
        assert verdicts[0].verdict == "violated"  # boundary claim
        assert verdicts[1].verdict == "violated"  # taint claim

    def test_taint_findings_default_none(self) -> None:
        """verify_claims works without taint_findings arg."""
        bmap = _make_boundary_map()
        claims = [
            Claim(
                id="TF-001", text="No plaintext to disk",
                constraint_taint_flow=TaintFlowConstraint(
                    source_taint="plaintext",
                    prohibited_sink_zone="host_fs",
                ),
            ),
        ]
        verdicts = verify_claims(claims, bmap)
        assert verdicts[0].verdict == "confirmed"  # no findings → confirmed


class TestLoadClaimsYamlError:
    """INV-zurih: malformed YAML must surface as ClaimsFileError, not a
    raw yaml.YAMLError traceback."""

    def test_malformed_yaml_raises_claims_file_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("not: valid: yaml: [\n")
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        # Names the file and carries the underlying parser reason.
        assert str(path) in str(exc.value)

    def test_yaml_error_is_not_a_bare_yaml_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("a: b: c\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)


class TestLoadClaimsShape:
    """WI-fuhaf: load_claims validates the parsed YAML's shape up front and
    raises a clear ClaimsFileError instead of an AttributeError/TypeError
    traceback. Empty/null/[] representations of 'no claims' load cleanly."""

    def test_scalar_root_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("hello\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_list_root_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("- id: SC-1\n  text: x\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_claims_null_loads_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims: null\n")
        assert load_claims(path) == []

    def test_claims_tilde_loads_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims: ~\n")
        assert load_claims(path) == []

    def test_empty_file_loads_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("")
        assert load_claims(path) == []

    def test_empty_dict_loads_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("{}\n")
        assert load_claims(path) == []

    def test_claims_scalar_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims: hello\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_claims_dict_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims:\n  a: b\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_claim_entry_not_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims:\n  - just-a-string\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_constraint_not_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("claims:\n  - id: SC-1\n    text: x\n    constraint: oops\n")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_constraint_null_loads(self, tmp_path: Path) -> None:
        # An explicit-null constraint is a claim with no machine constraint;
        # it loads (verify_claim later returns inconclusive), not an error.
        path = tmp_path / "c.yaml"
        path.write_text("claims:\n  - id: SC-1\n    text: x\n    constraint: ~\n")
        claims = load_claims(path)
        assert len(claims) == 1
        assert claims[0].constraint_boundary == ""

    def test_binary_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_bytes(b"\xff\xfe\x00\x01\x02\x80\x81binary\x00")
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ClaimsFileError):
            load_claims(tmp_path)


class TestLoadClaimsBoundaryVocab:
    """WI-ruzib / INV-gobob: constraint.boundary is validated against the
    canonical io-boundaries vocabulary at load time. Unknown values error
    (with a did-you-mean hint) instead of silently confirming must_not_exist."""

    def test_unknown_boundary_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: BANANA\n    text: x\n"
            "    constraint:\n      boundary: banana\n      must_not_exist: true\n"
        )
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        msg = str(exc.value)
        assert "banana" in msg
        # Lists the valid vocabulary so the user can self-correct.
        assert "net_send" in msg

    def test_unknown_boundary_offers_did_you_mean(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: TYPO\n    text: x\n"
            "    constraint:\n      boundary: net_sned\n      must_not_exist: true\n"
        )
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "net_send" in str(exc.value)
        assert "Did you mean" in str(exc.value)

    def test_external_potential_is_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: EP\n    text: x\n"
            "    constraint:\n      boundary: external_potential\n"
            "      must_not_exist: true\n"
        )
        claims = load_claims(path)
        assert claims[0].constraint_boundary == "external_potential"

    def test_all_catalog_boundaries_are_valid(self, tmp_path: Path) -> None:
        # Every boundary the io-boundary catalog can emit must be accepted.
        from hypergumbo_core.io_boundary import KNOWN_IO_BOUNDARIES
        for boundary in sorted(KNOWN_IO_BOUNDARIES):
            path = tmp_path / "c.yaml"
            path.write_text(
                f"claims:\n  - id: B\n    text: x\n"
                f"    constraint:\n      boundary: {boundary}\n"
                f"      must_not_exist: true\n"
            )
            claims = load_claims(path)
            assert claims[0].constraint_boundary == boundary


class TestLoadClaimsFieldAllowlist:
    """WI-bopoz: unknown YAML field names (typos like 'constrant',
    'must-not-exist') are rejected at load time rather than silently dropped
    into a defaults-populated claim that yields an indistinguishable verdict."""

    def test_unknown_top_level_key_raises(self, tmp_path: Path) -> None:
        # 'claim:' (missing 's') would otherwise silently load zero claims.
        path = tmp_path / "c.yaml"
        path.write_text("claim:\n  - id: SC-1\n    text: x\n")
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "claims" in str(exc.value)

    def test_extra_catalogs_top_level_key_allowed(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "extra_catalogs:\n  sources: []\n"
            "claims:\n  - id: SC-1\n    text: x\n"
        )
        claims = load_claims(path)
        assert claims[0].id == "SC-1"

    def test_unknown_entry_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: SC-1\n    text: x\n    constrant: {}\n"
        )
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "constraint" in str(exc.value)  # did-you-mean

    def test_unknown_constraint_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: SC-1\n    text: x\n"
            "    constraint:\n      boundary: net_send\n"
            "      must-not-exist: true\n"
        )
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "must_not_exist" in str(exc.value)  # did-you-mean

    def test_unknown_taint_flow_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: SC-1\n    text: x\n"
            "    constraint:\n      taint_flow:\n"
            "        source-taint: foo\n"
            "        prohibited_sink_zone: host_fs\n"
        )
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "source_taint" in str(exc.value)  # did-you-mean

    def test_taint_flow_not_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "claims:\n  - id: SC-1\n    text: x\n"
            "    constraint:\n      taint_flow: oops\n"
        )
        with pytest.raises(ClaimsFileError):
            load_claims(path)

    def test_unknown_key_without_id_uses_index_label(self, tmp_path: Path) -> None:
        # When a claim has no id, the error message falls back to a #N label
        # (and 'bogus' has no close match, so no did-you-mean is appended).
        path = tmp_path / "c.yaml"
        path.write_text("claims:\n  - text: x\n    bogus: 1\n")
        with pytest.raises(ClaimsFileError) as exc:
            load_claims(path)
        assert "#1" in str(exc.value)
        assert "Did you mean" not in str(exc.value)

    def test_shipped_self_claims_file_loads_clean(self) -> None:
        # Regression guard: the project's own claims file must pass the gate.
        repo_root = Path(__file__).resolve().parents[3]
        shipped = repo_root / "docs" / "hypergumbo.claims.yaml"
        claims = load_claims(shipped)
        assert len(claims) > 0
        assert all(c.id for c in claims)


_PY_CALL = {
    "src": "python:a.py:1:f:function",
    "dst": "python:os:0-0:os.getcwd:function",
    "type": "calls",
}
_JS_CALL = {
    "src": "javascript:b.js:1:g:function",
    "dst": "javascript:fs:0-0:fs.readFile:function",
    "type": "calls",
}


#: Real catalogs, not ``{}``. Every dst in this class's fixtures ends in
#: ``:function``, so the INV-fibis uncatalogued-module check skips them whatever is
#: passed — which means passing the real thing costs nothing and makes each
#: ``complete is True`` below a live guard against that check OVER-firing. ``{}``
#: would have disabled it and turned those assertions into tautologies.
_REAL_CATALOGS = {
    "python": load_catalog("python"),
    "javascript": load_catalog("javascript"),
}


class TestComputeBoundaryCoverage:
    """WI-kajil: compute_boundary_coverage decides whether the I/O analysis is
    trustworthy enough to CONFIRM a zero-chain boundary claim. A clean verdict
    is only meaningful if the analysis could actually have seen the I/O.

    Covers the first two blind spots. The third — calls into a module the catalog
    cannot adjudicate (INV-fibis) — lives in
    ``test_verify_claims_uncatalogued_module_coverage.py``."""

    def test_no_call_edges_is_incomplete(self) -> None:
        cov = compute_boundary_coverage([], {"python"}, _REAL_CATALOGS)
        assert cov.complete is False
        assert cov.reason  # non-empty human-readable reason

    def test_blind_supported_language_is_incomplete(self) -> None:
        # python produced a call edge; javascript (supported, present) did not.
        cov = compute_boundary_coverage(
            [_PY_CALL], {"python", "javascript"}, _REAL_CATALOGS,
        )
        assert cov.complete is False
        assert "javascript" in cov.reason

    def test_all_supported_languages_covered_is_complete(self) -> None:
        cov = compute_boundary_coverage(
            [_PY_CALL, _JS_CALL], {"python", "javascript"}, _REAL_CATALOGS,
        )
        assert cov.complete is True

    def test_unsupported_language_without_calls_does_not_block(self) -> None:
        # A language with no I/O catalog is absent from supported_languages, so
        # its lack of call edges must not make coverage incomplete.
        non_call = {
            "src": "json:c.json:1:x:key", "dst": "json:c.json:2:y:key",
            "type": "contains",
        }
        cov = compute_boundary_coverage(
            [_PY_CALL, non_call], {"python"}, _REAL_CATALOGS,
        )
        assert cov.complete is True

    def test_a_call_emitting_language_with_no_catalogue_is_incomplete(
        self,
    ) -> None:
        """INV-dabov: the third blind spot, and the one that failed OPEN.

        A language with no ``io_primitives/*.yaml`` never reaches
        ``supported_languages`` — ``cmd_verify_claims`` builds its catalogs
        dict under ``if catalog.primitives:``, so an empty catalogue is
        dropped and the language is invisible to the ``blind`` check above.
        It is equally invisible to ``_uncatalogued_external_modules``, whose
        ``catalog is None`` skip fires for the same reason. So the analysis
        sees the calls, can classify none of them, and says ``confirmed``.

        Reproduced on the shipped CLI with a 7-line Ruby fixture doing
        ``Net::HTTP.new(...).post(path, "key=#{ENV['API_KEY']}")``: the
        ``net_send`` claim returned **confirmed, rc 0**, with no disclosure of
        any kind — and the analyzer is not blind, it emits the call edge.

        The rule is CALL-SCOPED, not repo-scoped, and that distinction is
        load-bearing: measured across six repos, a repo-scoped rule would name
        up to 16 languages including ``markdown``, ``gitignore`` and ``yaml``,
        while the call-scoped one names between one and three real ones.
        ``test_unsupported_language_without_calls_does_not_block`` is the
        guard for that half and must keep passing.
        """
        ruby_call = {
            "src": "ruby:main.rb:3-7:exfiltrate:function",
            "dst": "ruby:http:0-0:new:external_symbol",
            "type": "calls",
        }
        cov = compute_boundary_coverage(
            [_PY_CALL, ruby_call], {"python"}, _REAL_CATALOGS,
        )
        assert cov.complete is False, (
            "a language whose calls the catalogue cannot classify at all "
            "supported a clean verdict"
        )
        assert "ruby" in cov.reason, "the reason must name what went unexamined"

    def test_non_call_edge_types_do_not_count(self) -> None:
        non_call = {
            "src": "python:a.py:1:f:function",
            "dst": "python:b.py:1:g:function", "type": "contains",
        }
        cov = compute_boundary_coverage([non_call], {"python"}, _REAL_CATALOGS)
        assert cov.complete is False  # no call edges at all

    def test_src_without_language_prefix_is_skipped(self) -> None:
        # A malformed src (no colon) must not crash or fabricate a language.
        cov = compute_boundary_coverage(
            [{"src": "weird-no-colon", "dst": "x", "type": "calls"}],
            set(), _REAL_CATALOGS,
        )
        assert cov.complete is True  # one call edge, no supported lang to be blind


class TestVerifyClaimCoverage:
    """WI-kajil: an incomplete-coverage boundary analysis downgrades a
    would-be ``confirmed`` zero-chain verdict to ``inconclusive`` rather than
    asserting the boundary is unused on an analysis that couldn't see it."""

    def _net_send_must_not_exist(self) -> Claim:
        return Claim(id="SC", text="no net", constraint_boundary="net_send",
                     constraint_must_not_exist=True)

    def test_incomplete_downgrades_confirmed_to_inconclusive(self) -> None:
        bmap = _make_boundary_map(fs_read=5)  # net_send absent -> 0 chains
        cov = BoundaryCoverage(complete=False, reason="javascript was not covered")
        verdict = verify_claim(self._net_send_must_not_exist(), bmap, coverage=cov)
        assert verdict.verdict == "inconclusive"
        assert "javascript" in verdict.details

    def test_complete_coverage_confirms(self) -> None:
        bmap = _make_boundary_map(fs_read=5)
        cov = BoundaryCoverage(complete=True)
        verdict = verify_claim(self._net_send_must_not_exist(), bmap, coverage=cov)
        assert verdict.verdict == "confirmed"

    def test_incomplete_does_not_mask_real_violation(self) -> None:
        # Coverage gaps never turn found evidence into inconclusive.
        bmap = _make_boundary_map(net_send=3)
        cov = BoundaryCoverage(complete=False, reason="x")
        verdict = verify_claim(self._net_send_must_not_exist(), bmap, coverage=cov)
        assert verdict.verdict == "violated"

    def test_incomplete_downgrades_max_chains_within_limit(self) -> None:
        bmap = _make_boundary_map(fs_write=2)
        claim = Claim(id="SC", text="few writes",
                      constraint_boundary="fs_write", constraint_max_chains=5)
        verdict = verify_claim(
            claim, bmap, coverage=BoundaryCoverage(complete=False, reason="y"),
        )
        assert verdict.verdict == "inconclusive"

    def test_incomplete_does_not_mask_max_chains_violation(self) -> None:
        bmap = _make_boundary_map(fs_write=10)
        claim = Claim(id="SC", text="few writes",
                      constraint_boundary="fs_write", constraint_max_chains=5)
        verdict = verify_claim(
            claim, bmap, coverage=BoundaryCoverage(complete=False, reason="z"),
        )
        assert verdict.verdict == "violated"

    def test_verify_claims_threads_coverage(self) -> None:
        bmap = _make_boundary_map(fs_read=5)
        cov = BoundaryCoverage(complete=False, reason="js blind")
        verdicts = verify_claims(
            [self._net_send_must_not_exist()], bmap, coverage=cov,
        )
        assert verdicts[0].verdict == "inconclusive"


class TestFlowEvidenceCarriesMatchProvenance:
    """The evidence row must let a reader confirm the match by lookup.

    WI-joruv: adding the fields to TaintFlowFinding is not enough — the
    evidence dict is the boundary where they would otherwise be dropped,
    and the dict is what a consumer actually reads.
    """

    def _finding(self) -> "TaintFlowFinding":
        from hypergumbo_core.taint import TaintFlowFinding

        return TaintFlowFinding(
            taint_label="untrusted_input",
            source_symbol="go:net/http:0-0:Body:external_symbol",
            source_primitive="Body",
            source_module="net/http",
            sink_symbol="go:net/http:0-0:Do:external_symbol",
            sink_primitive="Do",
            sink_module="net/http.Client",
            sink_zone="network",
            sanitized=False,
            confidence="approximate",
            analysis_method="structural",
            path=["go:cmd/run.go:10-40:run:function"],
        )

    def test_evidence_row_exposes_the_matched_catalog_modules(self) -> None:
        from hypergumbo_core.verify_claims import _flow_evidence_dict

        row = _flow_evidence_dict(self._finding())
        assert row["sink_module"] == "net/http.Client"
        assert row["source_module"] == "net/http"

    def test_the_emitted_symbol_and_catalog_module_are_both_present(self) -> None:
        """The pair is the point. Either alone cannot distinguish a correct
        package-vs-package.Type match from a short-name collision."""
        from hypergumbo_core.verify_claims import _flow_evidence_dict

        row = _flow_evidence_dict(self._finding())
        assert row["sink_symbol"] == "go:net/http:0-0:Do:external_symbol"
        assert row["sink_module"] == "net/http.Client"
        assert row["sink_module"] not in row["sink_symbol"]


# ---------------------------------------------------------------------------
# WI-bifob — production is the default scope, exclusions are DISCLOSED
#
# A claim verdict is a DISJUNCTION over its flows, so one test-sourced flow
# held a whole claim at `violated` and no precision work elsewhere could move
# it. Measured on the 9-repo cohort, 2 of 18 violated claims rested entirely
# on a single test-sourced flow each.
# ---------------------------------------------------------------------------


from hypergumbo_core.verify_claims import (  # noqa: E402
    SOURCE_SCOPE_MIGRATION,
    SOURCE_SCOPE_PRODUCTION,
    SOURCE_SCOPE_TEST,
    _source_scope,
    _symbol_path_slot,
)


def _finding_from(path: str) -> TaintFlowFinding:
    """A finding whose SOURCE lives at `path`."""
    return TaintFlowFinding(
        taint_label="untrusted_input",
        source_symbol=f"python:{path}:10-20:handler:function",
        source_primitive="environ",
        sink_symbol="python:urllib.request:0-0:urlopen:external_symbol",
        sink_primitive="urlopen",
        sink_zone="network",
        sanitized=False,
        confidence="approximate",
        analysis_method="structural",
    )


def _network_claim() -> Claim:
    return Claim(
        id="untrusted-input-no-network",
        text="Untrusted input must not reach a network send unsanitized.",
        constraint_taint_flow=TaintFlowConstraint(
            source_taint="untrusted_input",
            prohibited_sink_zone="network",
        ),
    )


class TestSymbolPathSlot:
    """The path slot is the one colon-TOLERANT slot (ADR-0036 D1a)."""

    def test_ordinary_path(self) -> None:
        assert _symbol_path_slot(
            "python:src/app/views.py:1-2:f:function"
        ) == "src/app/views.py"

    def test_path_containing_colons_is_right_anchored(self) -> None:
        """`dart:io` in the path slot must survive.

        A naive parts[1] parse returns "dart" here and silently mis-classifies
        the file. ir._extract_path_slot has exactly that bug; this must not
        become a third copy of it.
        """
        assert _symbol_path_slot("dart:dart:io:0-0:module:module") == "dart:io"

    def test_malformed_id_yields_empty(self) -> None:
        assert _symbol_path_slot("src:1") == ""
        assert _symbol_path_slot("") == ""


class TestSourceScope:
    """Classification is on the SOURCE side, by where data ENTERS."""

    def test_test_file_source(self) -> None:
        assert _source_scope(
            _finding_from("src/tests/e2e/conftest.py")
        ) == SOURCE_SCOPE_TEST
        assert _source_scope(
            _finding_from("cluster/tls_transport_test.go")
        ) == SOURCE_SCOPE_TEST

    def test_migration_source(self) -> None:
        assert _source_scope(
            _finding_from("src/pretix/base/migrations/0097_auto.py")
        ) == SOURCE_SCOPE_MIGRATION

    def test_production_source(self) -> None:
        assert _source_scope(
            _finding_from("src/pretix/base/exporters/answers.py")
        ) == SOURCE_SCOPE_PRODUCTION

    def test_unparseable_source_is_production_not_excluded(self) -> None:
        """An id we cannot read is NOT evidence for dropping a flow.

        Failing open here is deliberate: the cost of wrongly excluding a real
        finding from a security verdict is higher than the cost of one noisy
        row, so an unreadable source stays in scope.
        """
        finding = _finding_from("x")
        finding.source_symbol = "not-an-id"
        assert _source_scope(finding) == SOURCE_SCOPE_PRODUCTION


class TestNonProductionExclusion:
    """The default excludes; the disclosure is not optional."""

    def test_sole_test_sourced_flow_flips_verdict_to_confirmed(self) -> None:
        """The measured cohort case, reproduced.

        pretix/host-secret-no-network and alertmanager/untrusted-input-no-
        network each had exactly ONE evidence row, both test-sourced. Under
        the old default a conftest.py reading an env var held the whole claim
        at `violated`.
        """
        verdict = verify_taint_claim(
            _network_claim(), [_finding_from("src/tests/e2e/conftest.py")]
        )
        assert verdict.verdict == "confirmed"
        assert verdict.excluded_flows == {SOURCE_SCOPE_TEST: 1}

    def test_confirmed_verdict_discloses_what_it_set_aside(self) -> None:
        """The confirmed path is where a silent filter would mislead most.

        The claim reads clean; without the disclosure the reader has no way to
        learn flows existed and were set aside by a policy they did not pick.
        """
        verdict = verify_taint_claim(
            _network_claim(), [_finding_from("src/tests/e2e/conftest.py")]
        )
        assert "Excluded from this verdict as non-production" in verdict.details
        assert "1 test_sourced" in verdict.details
        assert "--include-non-production-sources" in verdict.details

    def test_production_flow_still_violates(self) -> None:
        """Non-vacuity floor (L17): the exclusion must not swallow real flows."""
        verdict = verify_taint_claim(
            _network_claim(), [_finding_from("src/pretix/views/order.py")]
        )
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert verdict.excluded_flows == {}

    def test_mixed_keeps_violated_and_still_discloses(self) -> None:
        """A claim that survives on production evidence still reports the rest.

        This is the common shape on the cohort — the excluded rows are volume,
        not verdict-changing — and reporting only on the confirmed path would
        hide the majority of what was set aside.
        """
        verdict = verify_taint_claim(
            _network_claim(),
            [
                _finding_from("src/pretix/views/order.py"),
                _finding_from("src/tests/e2e/conftest.py"),
                _finding_from("src/pretix/base/migrations/0097_auto.py"),
            ],
        )
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert verdict.excluded_flows == {
            SOURCE_SCOPE_TEST: 1,
            SOURCE_SCOPE_MIGRATION: 1,
        }

    def test_include_flag_restores_previous_behavior(self) -> None:
        """The default is a DEFAULT, not a hard filter."""
        verdict = verify_taint_claim(
            _network_claim(),
            [_finding_from("src/tests/e2e/conftest.py")],
            include_non_production=True,
        )
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert verdict.excluded_flows == {}

    def test_excluded_flows_is_serialized(self) -> None:
        """A disclosure a consumer cannot read is not a disclosure."""
        verdict = verify_taint_claim(
            _network_claim(), [_finding_from("src/tests/e2e/conftest.py")]
        )
        assert verdict.to_dict()["excluded_flows"] == {SOURCE_SCOPE_TEST: 1}


# ---------------------------------------------------------------------------
# WI-vazal — report WHICH boundary a flow entered through, without relabelling
#
# AUTO_SOURCE_LABEL_MAP collapses net_recv, ipc_recv and db_read into the one
# label `untrusted_input`. On an ORM-backed app the db_read arm dominates: 93
# of the 100 displayed rows on pretix's largest violated claim are
# database-read to database-write. The owner ruling was to keep the flows and
# make them SEPARABLE, not to relabel them — relabelling would silently change
# what every already-published `untrusted_input` claim means.
# ---------------------------------------------------------------------------


def _finding_with_boundary(boundary: str, path: str = "src/app/views.py"):
    f = _finding_from(path)
    f.source_boundary = boundary
    return f


class TestFlowOrigins:
    """The label stays put; the breakdown is reported alongside it."""

    def test_splits_counted_flows_by_source_boundary(self) -> None:
        verdict = verify_taint_claim(
            _network_claim(),
            [
                _finding_with_boundary("db_read"),
                _finding_with_boundary("db_read"),
                _finding_with_boundary("net_recv"),
            ],
        )
        assert verdict.verdict == "violated"
        assert verdict.flow_origins == {"db_read": 2, "net_recv": 1}

    def test_taint_label_is_unchanged_by_the_split(self) -> None:
        """The whole point: no flow is relabelled, so no claim changes meaning.

        A `db_read`-sourced flow must still match a claim written against
        `untrusted_input`. If this ever fails, the change has become the
        relabelling option the owner declined.
        """
        findings = [_finding_with_boundary("db_read")]
        assert findings[0].taint_label == "untrusted_input"
        verdict = verify_taint_claim(_network_claim(), findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1

    def test_yaml_declared_sources_bucket_as_declared(self) -> None:
        """A source with no boundary is `declared`, not silently uncounted.

        Built-in YAML sources (crypto, key_material) and project-local
        catalogs have no io_primitives boundary. They still need a bucket, or
        flow_origins would not sum to the flows the verdict is about.
        """
        verdict = verify_taint_claim(
            _network_claim(), [_finding_with_boundary("")]
        )
        assert verdict.flow_origins == {"declared": 1}

    def test_origins_sum_to_the_counted_flows(self) -> None:
        """Non-vacuity floor (L17): the breakdown must account for everything.

        A bucket that silently drops a category would still produce a
        plausible-looking dict.
        """
        findings = [
            _finding_with_boundary("db_read"),
            _finding_with_boundary("net_recv"),
            _finding_with_boundary("ipc_recv"),
            _finding_with_boundary(""),
        ]
        verdict = verify_taint_claim(_network_claim(), findings)
        assert sum(verdict.flow_origins.values()) == verdict.evidence_count == 4

    def test_excluded_flows_are_not_counted_in_origins(self) -> None:
        """Origins describe the flows the verdict IS about.

        A test-sourced flow is excluded from the verdict (WI-bifob), so
        counting it here would make the two disclosures contradict each other.
        """
        verdict = verify_taint_claim(
            _network_claim(),
            [
                _finding_with_boundary("db_read", "src/app/views.py"),
                _finding_with_boundary("net_recv", "src/tests/e2e/conftest.py"),
            ],
        )
        assert verdict.flow_origins == {"db_read": 1}
        assert verdict.excluded_flows == {SOURCE_SCOPE_TEST: 1}

    def test_serialized_for_consumers(self) -> None:
        verdict = verify_taint_claim(
            _network_claim(), [_finding_with_boundary("db_read")]
        )
        d = verdict.to_dict()
        assert d["flow_origins"] == {"db_read": 1}
        assert d["evidence"][0]["source_boundary"] == "db_read"


class TestExtraCatalogsIoPrimitives:
    """INV-fotav: ``extra_catalogs:`` grew a fourth key for boundary overlays.

    It is read through the SAME loader as the three taint keys rather than a
    second one, because ``extra_catalogs:`` is already the one place a claims
    file declares project-local knowledge — the boundary arm was simply never
    given a key in it.
    """

    def test_io_primitives_paths_resolve_against_the_claims_directory(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "claims.yaml"
        path.write_text(
            "claims: []\n"
            "extra_catalogs:\n"
            "  io_primitives:\n"
            "    - overlays/http.yaml\n",
        )
        _s, _k, _n, io_prims = load_extra_catalog_paths(path)
        assert io_prims == [tmp_path / "overlays" / "http.yaml"]

    def test_absent_io_primitives_key_is_empty_not_missing(
        self, tmp_path: Path,
    ) -> None:
        """A claims file that declares only taint extras must still return a
        well-formed fourth element, not raise."""
        path = tmp_path / "claims.yaml"
        path.write_text(
            "claims: []\n"
            "extra_catalogs:\n"
            "  sources:\n"
            "    - taint/src.yaml\n",
        )
        sources, _k, _n, io_prims = load_extra_catalog_paths(path)
        assert sources == [tmp_path / "taint" / "src.yaml"]
        assert io_prims == []


class TestARepoSuppliedSanitizerReachesTheExitCode:
    """INV-pojib (b)/(c) — the verdict VALUE, not only the prose, must record
    that a repo-supplied entry is what made the claim read clean.

    Remedy (a1) landed at dev 1ec23deb31 and closed the "verdict text cannot
    distinguish the two" half: the details string now says
    ``(via h.launder (project-local))``. It deliberately did NOT touch the
    verdict value or the exit code, and that residual is what this class pins.

    WHY PROSE WAS NOT ENOUGH, measured on the shipped CLI at dev 1ec23deb31
    with the fixture from the item's own repro (``os.remove(launder(
    os.environ["API_KEY"]))``, ``launder`` returning its argument):

        baseline, no sanitizer file            -> rc 1  violated
        + 8-line lie via extra_catalogs:       -> rc 0  confirmed

    rc 0 in the second case is byte-identical to the exit code of a verdict the
    analysis earned unaided. A CI gate reads the exit code; nothing reads the
    sentence. So the attribution was legible to a human re-reading stdout and
    invisible to the machine the artifact exists to convince.

    ADR-0016 §4 already specifies the fourth verdict this uses ("Confirmed with
    caveats: consistent, but opaque boundaries exist that could not be
    verified"). Implementing it here is finishing declared work, and the
    user-supplied-sanitizer case is its second and stronger consumer: an
    unverifiable ASSERTION by the analysed party, rather than an unverifiable
    boundary.
    """

    def _claim(self) -> Claim:
        return Claim(
            id="TF-CAVEAT",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )

    def _repo_supplied(self) -> TaintFlowFinding:
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        f.sanitized_by = ("h.launder",)
        f.sanitized_by_user_supplied = ("h.launder",)
        return f

    def _built_in(self) -> TaintFlowFinding:
        f = _flow(source_symbol="s", sink_symbol="d")
        f.sanitized = True
        f.sanitized_by = ("cryptography.fernet.Fernet.encrypt",)
        return f

    def test_verdict_value_records_the_repo_supplied_dependency(self) -> None:
        """The half (a1) left open: the VALUE, which is what a consumer reads."""
        verdict = verify_taint_claim(self._claim(), [self._repo_supplied()])
        assert verdict.verdict == "confirmed_with_caveats", (
            f"a claim held up by an entry the analysed repository supplied is "
            f"not a verdict the analysis earned; got {verdict.verdict!r}"
        )

    def test_a_built_in_sanitizer_still_earns_a_plain_confirmed(self) -> None:
        """THE DISCRIMINATOR, and the reason this is worth shipping at all.

        A caveat raised on every sanitized verdict would carry no information
        and a reader would learn to discount it — the same argument that made
        remedy (a2) (a run-level "a project-local catalogue was in effect"
        caveat) the wrong shape. If this test ever goes green by accident, the
        feature has degraded into noise.
        """
        verdict = verify_taint_claim(self._claim(), [self._built_in()])
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_caveat_is_structured_not_only_prose(self) -> None:
        """A consumer must be able to branch on it without parsing English."""
        verdict = verify_taint_claim(self._claim(), [self._repo_supplied()])
        assert len(verdict.caveats) == 1
        caveat = verdict.caveats[0]
        assert caveat["kind"] == "user_supplied_sanitizer"
        assert "h.launder" in caveat["entries"]

    def test_caveat_rides_the_json_envelope(self) -> None:
        """``to_dict`` is the machine surface; a field that stops at the
        dataclass is half shipped. A pre-existing property test caught exactly
        this omission on the (a1) fields.
        """
        d = verify_taint_claim(self._claim(), [self._repo_supplied()]).to_dict()
        assert d["verdict"] == "confirmed_with_caveats"
        assert d["caveats"][0]["kind"] == "user_supplied_sanitizer"

    def test_prose_and_verdict_value_cannot_disagree(self) -> None:
        """PARITY over the two surfaces built from one fact.

        The attribution string and the caveat are two consumers of "which
        sanitizers were credited, and which came from a user path". Two homes
        for one fact drift immediately, so they read it from one predicate and
        this test is what keeps them honest: ``(project-local)`` in the prose
        and ``confirmed_with_caveats`` in the value must appear together or not
        at all, in BOTH directions.
        """
        for findings in (
            [self._repo_supplied()],
            [self._built_in()],
            [self._repo_supplied(), self._built_in()],
        ):
            verdict = verify_taint_claim(self._claim(), findings)
            marked = "project-local" in verdict.details
            caveated = verdict.verdict == "confirmed_with_caveats"
            assert marked == caveated, (
                f"prose says project-local={marked} but the verdict value says "
                f"caveated={caveated}; got {verdict.verdict!r} / "
                f"{verdict.details!r}"
            )

    def test_a_violated_claim_is_not_downgraded_to_a_caveat(self) -> None:
        """Direction check. Caveats qualify a CLEAN verdict; they must never
        soften a finding. A repo that supplies a sanitizer AND still leaks is
        violated, full stop.
        """
        leaky = _flow(source_symbol="s2", sink_symbol="d2")
        verdict = verify_taint_claim(
            self._claim(), [self._repo_supplied(), leaky],
        )
        assert verdict.verdict == "violated"
        assert verdict.caveats == []

    def test_blindness_still_dominates_a_caveated_verdict(self) -> None:
        """A caveated verdict is still a CONFIRMING one, so the coverage gate
        must reach it. If ``_require_coverage_to_confirm`` kept testing
        ``!= "confirmed"``, adding this fourth value would have punched a hole
        straight through the honesty gate INV-javam/INV-bitig exist to hold —
        a blind analysis would report ``confirmed_with_caveats`` instead of
        ``inconclusive``.
        """
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        caveated = verify_taint_claim(self._claim(), [self._repo_supplied()])
        gated = _require_coverage_to_confirm(caveated, "no catalogue for bash")
        assert gated.verdict == "inconclusive"


class TestAnOpaqueLaunchQualifiesRatherThanBlinds:
    """ADR-0016 §4's ORIGINAL specified consumer of the fourth verdict, built
    at last: "consistent, but opaque boundaries exist that could not be
    verified". Owner-authorized 2026-08-13.

    WHAT ``inconclusive`` WAS LUMPING TOGETHER. Two very different epistemic
    states arrived at the same verdict:

      * "a whole language here has no catalogue — I am blind"
      * "I examined every call and understood them all; three of them hand
        control to another program (git, pip, rustup) and no static tool can
        see inside a launched process"

    The auditor distinction is exact: a DISCLAIMER ("could not form a view")
    versus a QUALIFIED OPINION ("correct, except these named items"). The tool
    issued disclaimers where a qualified opinion was warranted — and because
    hypergumbo launches git BY DESIGN, ``confirmed`` was permanently
    unreachable for its own self-proof, making the artifact one that can never
    say anything at all.

    THE DIRECTION IS TOWARDS CONFIRMING, which is why this needed authorization
    and why the launch list must be COMPLETE for it to be sound. That surface
    is what the INV-motos (constructors counted), INV-gahuz (opacity gated),
    INV-larol + INV-virat (producer stamps unstrippable) and INV-zumin
    (row-order masking) work hardened; this rests on all of it.

    AND IT QUALIFIES ONLY WHEN NOTHING ELSE BLOCKS. An opaque launch beside a
    genuinely uncatalogued module is still blindness — the reader cannot tell
    which gap the silence came from — so the caveat is raised only when the
    launch sites are the SOLE remaining blocker.
    """

    def _bmap(self):
        return _make_boundary_map(net_send=0)

    def _claim(self) -> Claim:
        return Claim(
            id="SC-OPAQUE",
            text="never sends data over the network",
            constraint_boundary="net_send",
            constraint_must_not_exist=True,
        )

    def test_launch_sites_alone_qualify_the_verdict(self) -> None:
        cov = BoundaryCoverage(
            complete=False,
            reason="the analysis launches an external program at 2 call "
                   "site(s) (subprocess.run, os.execv) and cannot see what "
                   "the launched program does, so whether this I/O happens "
                   "there was never examined",
            opaque_sites=["os.execv", "subprocess.run"],
            qualifying_only=True,
        )
        v = verify_claim(self._claim(), self._bmap(), coverage=cov)
        assert v.verdict == "confirmed_with_caveats"
        assert v.caveats and v.caveats[0]["kind"] == "opaque_boundary"
        assert v.caveats[0]["entries"] == ["os.execv", "subprocess.run"]

    def test_the_named_sites_reach_the_reader(self) -> None:
        """A qualified opinion that does not name what it excepted is just a
        clean opinion with extra words. The sites are what a human reviews
        once and a gate then trusts.
        """
        cov = BoundaryCoverage(
            complete=False, reason="…launches an external program…",
            opaque_sites=["subprocess.run"], qualifying_only=True,
        )
        v = verify_claim(self._claim(), self._bmap(), coverage=cov)
        assert "subprocess.run" in v.caveats[0]["detail"]

    def test_blindness_still_blinds(self) -> None:
        """THE DISCRIMINATOR. Coverage that is incomplete for any reason OTHER
        than opacity must stay ``inconclusive``. If this ever goes green the
        feature has become a caveat on everything, which is the failure mode
        remedy (a2) was rejected for on INV-pojib.
        """
        cov = BoundaryCoverage(
            complete=False,
            reason="language(s) bash made calls but have no I/O catalog",
        )
        v = verify_claim(self._claim(), self._bmap(), coverage=cov)
        assert v.verdict == "inconclusive"
        assert v.caveats == []

    def test_a_launch_beside_a_real_gap_still_blinds(self) -> None:
        """Opacity is only a QUALIFICATION when it is the sole blocker. Beside
        an uncatalogued module the silence is ambiguous — the reader cannot
        tell which gap produced it — so this must not qualify.
        """
        cov = BoundaryCoverage(
            complete=False, reason="…launches an external program…",
            opaque_sites=["subprocess.run"], qualifying_only=False,
        )
        v = verify_claim(self._claim(), self._bmap(), coverage=cov)
        assert v.verdict == "inconclusive"
        assert v.caveats == []

    def test_complete_coverage_still_earns_a_plain_confirmed(self) -> None:
        """NON-DESTRUCTION: a repo that launches nothing is unaffected."""
        v = verify_claim(
            self._claim(), self._bmap(),
            coverage=BoundaryCoverage(complete=True),
        )
        assert v.verdict == "confirmed"
        assert v.caveats == []

    def test_a_violation_is_never_softened_into_a_caveat(self) -> None:
        """Direction check. Caveats qualify a CLEAN verdict and must never
        touch a finding — the same guard INV-pojib's sanitizer kind carries.
        """
        cov = BoundaryCoverage(
            complete=False, reason="…launches…",
            opaque_sites=["subprocess.run"], qualifying_only=True,
        )
        v = verify_claim(self._claim(), _make_boundary_map(net_send=3),
                         coverage=cov)
        assert v.verdict == "violated"
        assert v.caveats == []


class TestOpacityIsDetectedAsSoleBlocker:
    """``qualifying_only`` is computed, not passed in — the gate derives its
    own inputs (L6: a gate whose caller can forget to arm it fails open).
    """

    def test_launches_with_everything_else_clean_qualify(self) -> None:
        edges = [
            {"src": "python:a.py:1-3:f:function",
             "dst": "python:subprocess:0-0:run:external_symbol",
             "type": "calls"},
        ]
        cov = compute_boundary_coverage(
            edges, {"python"}, {"python": load_catalog("python")},
        )
        assert cov.complete is False
        assert cov.opaque_sites == ["subprocess.run"]
        assert cov.qualifying_only is True

    def test_an_uncatalogued_module_alongside_removes_the_qualification(
        self,
    ) -> None:
        edges = [
            {"src": "python:a.py:1-3:f:function",
             "dst": "python:subprocess:0-0:run:external_symbol",
             "type": "calls"},
            {"src": "python:a.py:1-3:f:function",
             "dst": "python:requests:0-0:post:external_symbol",
             "type": "calls"},
        ]
        cov = compute_boundary_coverage(
            edges, {"python"}, {"python": load_catalog("python")},
        )
        assert cov.opaque_sites == ["subprocess.run"]
        assert cov.qualifying_only is False, (
            "an uncatalogued module beside the launch is real blindness; the "
            "reader cannot tell which gap the silence came from"
        )


class TestTheTaintArmQualifiesOnOpacityToo:
    """PARITY over the two claim kinds. The self-proof's 18 claims are ALL
    taint claims, so a fix that reached only boundary claims would leave the
    artifact this was authorized for exactly where it was.

    The taint arm reaches the gate through a different path — ``cmd_verify_
    claims`` flattens coverage into a ``blind_reason`` STRING and hands it to
    :func:`_require_coverage_to_confirm` — so the qualification has to travel
    alongside it or be lost. Both kinds must produce the same ``kind`` and the
    same site list, or one disclosure drifts from the other (L53).
    """

    def _confirmed(self) -> ClaimVerdict:
        return ClaimVerdict(
            claim_id="TF-1", claim_text="no secrets to host_fs",
            verdict="confirmed", details="No unsanitized flows.",
        )

    def test_opacity_qualifies_instead_of_blinding(self) -> None:
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        out = _require_coverage_to_confirm(
            self._confirmed(),
            "the analysis launches an external program at 1 call site(s)",
            opaque_sites=["subprocess.run"],
        )
        assert out.verdict == "confirmed_with_caveats"
        assert out.caveats[0]["kind"] == "opaque_boundary"
        assert out.caveats[0]["entries"] == ["subprocess.run"]

    def test_real_blindness_still_downgrades_to_inconclusive(self) -> None:
        """THE DISCRIMINATOR on this arm. A missing catalogue is not a named
        door — it is not knowing whether there is a door.
        """
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        out = _require_coverage_to_confirm(
            self._confirmed(),
            "this repo contains code in language(s) with no taint catalogue "
            "(bash)",
        )
        assert out.verdict == "inconclusive"
        assert out.caveats == []

    def test_both_claim_kinds_render_one_disclosure(self) -> None:
        """PARITY, asserted rather than assumed: the boundary path and the
        taint path must not grow two spellings of the same caveat.
        """
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        sites = ["os.execv", "subprocess.run"]
        boundary = verify_claim(
            Claim(id="B", text="no net", constraint_boundary="net_send",
                  constraint_must_not_exist=True),
            _make_boundary_map(net_send=0),
            coverage=BoundaryCoverage(
                complete=False, reason="…launches…",
                opaque_sites=sites, qualifying_only=True,
            ),
        )
        taint = _require_coverage_to_confirm(
            self._confirmed(), "…launches…", opaque_sites=sites,
        )
        assert boundary.caveats[0]["kind"] == taint.caveats[0]["kind"]
        assert boundary.caveats[0]["entries"] == taint.caveats[0]["entries"]
        assert boundary.caveats[0]["detail"] == taint.caveats[0]["detail"]

    def test_a_sanitizer_caveat_is_not_lost_when_opacity_also_applies(
        self,
    ) -> None:
        """BOTH KINDS CAN BE TRUE AT ONCE, and the self-proof is exactly that
        case: it declares a zone-barrier sanitizer (INV-pojib's kind) AND it
        shells out to git (this kind). Neither may silently displace the
        other — that would be the one-slot last-writer-wins class (INV-virat)
        reappearing inside the caveat list itself.
        """
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        already = ClaimVerdict(
            claim_id="TF-2", claim_text="no secrets to host_fs",
            verdict="confirmed_with_caveats", details="…via h.launder…",
            caveats=[{"kind": "user_supplied_sanitizer",
                      "entries": ["h.launder"], "detail": "…"}],
        )
        out = _require_coverage_to_confirm(
            already, "…launches…", opaque_sites=["subprocess.run"],
        )
        kinds = {c["kind"] for c in out.caveats}
        assert kinds == {"user_supplied_sanitizer", "opaque_boundary"}


class TestACaveatKindIsNotRecordedTwice:
    """The append that closed one instance of the one-slot class opened a
    duplicate-record instance of its own.

    ``verify_claims`` runs TWO stages over the same verdict: ``verify_claim``
    (where a boundary claim may already attach ``opaque_boundary``) and then
    ``_require_coverage_to_confirm`` (which appends the taint arm's copy). A
    claims file holding BOTH a boundary claim and a taint claim sets
    ``has_taint_claims``, so ``blind_reason`` is populated and applied to EVERY
    verdict — including the boundary one that already carries the caveat.

    Measured before the fix: ``['opaque_boundary', 'opaque_boundary']`` on the
    boundary verdict. Not cosmetic — ``caveats`` is the machine surface a
    consumer branches on and counts, and a doubled entry says a claim rests on
    twice as many unverifiable doors as it does. The lesson is the mirror of
    INV-virat's: switching from ASSIGN to APPEND fixes last-writer-wins and
    introduces duplicate-accumulation unless the append is idempotent per kind.
    """

    def _both_kinds_of_claim(self):
        return [
            Claim(id="B-1", text="no net", constraint_boundary="net_send",
                  constraint_must_not_exist=True),
            Claim(id="T-1", text="no secrets to fs",
                  constraint_taint_flow=TaintFlowConstraint(
                      source_taint="host_secret",
                      prohibited_sink_zone="host_fs")),
        ]

    def _run(self):
        return verify_claims(
            self._both_kinds_of_claim(),
            _make_boundary_map(net_send=0),
            taint_findings=[],
            coverage=BoundaryCoverage(
                complete=False, reason="…launches…",
                opaque_sites=["subprocess.run"], qualifying_only=True,
            ),
            blind_reason="…launches…",
            blind_opaque_sites=["subprocess.run"],
        )

    def test_no_verdict_carries_the_same_kind_twice(self) -> None:
        for v in self._run():
            kinds = [c["kind"] for c in v.caveats]
            assert len(kinds) == len(set(kinds)), (
                f"{v.claim_id} records a caveat kind more than once: {kinds}"
            )

    def test_the_surviving_entry_keeps_the_full_site_list(self) -> None:
        """De-duplication must not silently drop sites. If the two copies ever
        disagree, keeping the UNION is the honest direction — under-reporting
        unverifiable doors is the failure that matters.
        """
        for v in self._run():
            opaque = [c for c in v.caveats if c["kind"] == "opaque_boundary"]
            assert len(opaque) == 1
            assert opaque[0]["entries"] == ["subprocess.run"]

    def test_distinct_kinds_still_accumulate(self) -> None:
        """NON-DESTRUCTION for the property this was built to have: two
        DIFFERENT kinds on one verdict must both survive. Deduplicating by
        kind must not collapse the sanitizer caveat into the opacity one.
        """
        from hypergumbo_core.verify_claims import _require_coverage_to_confirm
        already = ClaimVerdict(
            claim_id="T-2", claim_text="x", verdict="confirmed_with_caveats",
            details="…", caveats=[{"kind": "user_supplied_sanitizer",
                                   "entries": ["h.launder"], "detail": "…"}],
        )
        out = _require_coverage_to_confirm(
            already, "…launches…", opaque_sites=["subprocess.run"],
        )
        assert {c["kind"] for c in out.caveats} == {
            "user_supplied_sanitizer", "opaque_boundary",
        }


class TestCaveatMergeSemantics:
    """Direct tests of the merge helper. Two of its branches are unreachable
    from today's callers — both paths currently produce the SAME site list, so
    the disagreement case never arises in production — and they are covered
    here rather than pragma'd, because the failure they prevent is
    UNDER-REPORTING unverifiable doors.
    """

    def _cav(self, kind, entries):
        return {"kind": kind, "entries": list(entries), "detail": "…"}

    def test_disagreeing_site_lists_keep_the_union(self) -> None:
        """If two paths ever see different launch sites, the merged caveat must
        name ALL of them. Dropping one would silently narrow what the verdict
        admits it could not verify — the expensive direction.
        """
        from hypergumbo_core.verify_claims import (
            _merge_caveat, _opaque_boundary_caveat,
        )
        out = _merge_caveat(
            [_opaque_boundary_caveat(["subprocess.run"])],
            _opaque_boundary_caveat(["os.execv"]),
        )
        assert len(out) == 1
        assert out[0]["entries"] == ["os.execv", "subprocess.run"]

    def test_the_prose_is_re_rendered_to_match_the_widened_list(self) -> None:
        """A disclosure whose sentence and whose data disagree is worse than
        either alone: the detail quotes a COUNT and the site names, so a merge
        that widened ``entries`` without re-rendering would keep saying "1 call
        site" over two.
        """
        from hypergumbo_core.verify_claims import (
            _merge_caveat, _opaque_boundary_caveat,
        )
        out = _merge_caveat(
            [_opaque_boundary_caveat(["subprocess.run"])],
            _opaque_boundary_caveat(["os.execv"]),
        )
        assert "2 call site(s)" in out[0]["detail"]
        assert "os.execv" in out[0]["detail"]
        assert "subprocess.run" in out[0]["detail"]

    def test_a_non_opaque_kind_merges_entries_without_re_rendering(
        self,
    ) -> None:
        """The re-render is opacity-specific because only that detail string
        embeds its own entry list. Another kind widens its entries and keeps
        its own prose, rather than being handed opacity's wording.
        """
        from hypergumbo_core.verify_claims import _merge_caveat
        out = _merge_caveat(
            [self._cav("user_supplied_sanitizer", ["h.launder"])],
            self._cav("user_supplied_sanitizer", ["h.scrub"]),
        )
        assert len(out) == 1
        assert out[0]["entries"] == ["h.launder", "h.scrub"]
        assert out[0]["detail"] == "…"

    def test_an_identical_repeat_is_a_no_op(self) -> None:
        """The common case: both paths saw the same sites, so the list is
        returned unchanged rather than rebuilt."""
        from hypergumbo_core.verify_claims import (
            _merge_caveat, _opaque_boundary_caveat,
        )
        first = [_opaque_boundary_caveat(["subprocess.run"])]
        out = _merge_caveat(first, _opaque_boundary_caveat(["subprocess.run"]))
        assert out is first
