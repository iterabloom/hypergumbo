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
from hypergumbo_core.io_boundary import BoundaryMap, BoundaryMapEntry, IoChain
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
        sources, sinks, sanitizers = load_extra_catalog_paths(path)
        assert sources == []
        assert sinks == []
        assert sanitizers == []

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
        sources, sinks, sanitizers = load_extra_catalog_paths(path)
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
        sources, sinks, sanitizers = load_extra_catalog_paths(path)
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
        sources, _sinks, _san = load_extra_catalog_paths(path)
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
        confidence="approximate",
        analysis_method="structural",
        path=path if path is not None else [],
    )


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
                "sink_symbol": "d",
                "sink_primitive": "replace",
                "sink_module": "",
                "path": ["s", "mid", "d"],
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


class TestComputeBoundaryCoverage:
    """WI-kajil: compute_boundary_coverage decides whether the I/O analysis is
    trustworthy enough to CONFIRM a zero-chain boundary claim. A clean verdict
    is only meaningful if the analysis could actually have seen the I/O."""

    def test_no_call_edges_is_incomplete(self) -> None:
        cov = compute_boundary_coverage([], {"python"})
        assert cov.complete is False
        assert cov.reason  # non-empty human-readable reason

    def test_blind_supported_language_is_incomplete(self) -> None:
        # python produced a call edge; javascript (supported, present) did not.
        cov = compute_boundary_coverage([_PY_CALL], {"python", "javascript"})
        assert cov.complete is False
        assert "javascript" in cov.reason

    def test_all_supported_languages_covered_is_complete(self) -> None:
        cov = compute_boundary_coverage(
            [_PY_CALL, _JS_CALL], {"python", "javascript"},
        )
        assert cov.complete is True

    def test_unsupported_language_without_calls_does_not_block(self) -> None:
        # A language with no I/O catalog is absent from supported_languages, so
        # its lack of call edges must not make coverage incomplete.
        non_call = {
            "src": "json:c.json:1:x:key", "dst": "json:c.json:2:y:key",
            "type": "contains",
        }
        cov = compute_boundary_coverage([_PY_CALL, non_call], {"python"})
        assert cov.complete is True

    def test_non_call_edge_types_do_not_count(self) -> None:
        non_call = {
            "src": "python:a.py:1:f:function",
            "dst": "python:b.py:1:g:function", "type": "contains",
        }
        cov = compute_boundary_coverage([non_call], {"python"})
        assert cov.complete is False  # no call edges at all

    def test_src_without_language_prefix_is_skipped(self) -> None:
        # A malformed src (no colon) must not crash or fabricate a language.
        cov = compute_boundary_coverage(
            [{"src": "weird-no-colon", "dst": "x", "type": "calls"}], set(),
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
