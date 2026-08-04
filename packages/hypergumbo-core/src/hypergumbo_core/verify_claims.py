# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security claim verification against I/O boundary and taint-flow analysis.

Loads security claims from a YAML file, checks each claim against either the
boundary map (ADR-0016) or taint-flow results (ADR-0017), and returns verdicts.

Claim Format
------------
Claims are YAML files with a ``claims`` list. Each claim specifies:

- ``id``: Unique identifier (e.g., SC-001)
- ``text``: Human-readable description of the security property
- ``constraint``: What to check — one of:

  **Boundary constraint** (ADR-0016):
  - ``boundary``: Which I/O boundary type to check (e.g., "net_send")
  - ``must_not_exist``: If true, the boundary must have zero chains
  - ``max_chains``: Maximum allowed chain count for the boundary

  **Taint-flow constraint** (ADR-0017):
  - ``taint_flow``: Sub-object with taint-flow verification parameters
    - ``source_taint``: Taint label that must not reach the sink zone
    - ``prohibited_sink_zone``: Zone where tainted data must not arrive
    - ``allowed_sanitizers``: List of sanitizer qualified names (optional)

Verdict Types
-------------
- ``confirmed``: Claim was actively checked and held (no violations found)
- ``violated``: Specific evidence contradicts the claim
- ``inconclusive``: Verification couldn't proceed or couldn't be trusted —
  no machine-checkable constraint, broken input, missing catalog, or the
  I/O analysis was blind to the relevant code (empty analysis, or a
  supported language that produced no call edges). Kept distinct from
  ``confirmed`` to close the silent-confirm fall-through (ADR-0033 Phase 3;
  WI-kajil / INV-bitig).

For taint-flow claims the adjudication travels with the verdict rather than
being asserted by this module. Structural analysis produces ``approximate``
confidence; the ADR-0017 §3a data-dependence walk produces ``precise`` where it
confirms a dependence and ``approximate`` / ``ddg_mixed`` where it ran without
confirming one. A verdict's ``analysis_methods`` breakdown and each evidence
row's ``confidence`` / ``analysis_method`` report what actually happened. This
module previously hardcoded the literal ``approximate`` into every violated
verdict, which made a ``precise`` finding indistinguishable from a structural
one at the only surface a user reads.

Load-time validation
---------------------
``load_claims`` validates the claims file before constructing any ``Claim``
(the META-jurig gate): malformed YAML, an unexpected root/claim/constraint
shape, an unknown field name, or a ``constraint.boundary`` outside the
io-boundaries vocabulary each raise :class:`ClaimsFileError` (→ CLI exit 2)
rather than tracebacking or silently producing a degraded verdict stream.

How It Works
------------
1. ``load_claims(path)`` validates and parses the YAML into ``Claim`` objects
2. ``verify_claim(claim, boundary_map)`` checks one claim → ``ClaimVerdict``
3. ``verify_taint_claim(claim, findings)`` checks taint-flow → ``ClaimVerdict``
4. ``verify_claims(claims, boundary_map, findings)`` checks all
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .taint import TaintFlowFinding

import yaml

from .edge_types import is_grpc_rpc_implementation
from .io_boundary import KNOWN_IO_BOUNDARIES, BoundaryMap
from .paths import classify_test_file, is_migration_file


# Schema version for the ``verify-claims --json`` envelope (WI-nulot / INV-gatog).
# 1.0 introduces the top-level object (schema_version + view + verdicts +
# unsupported_taint_languages) replacing the legacy bare JSON array.
# 1.2 adds the per-verdict ``excluded_flows`` disclosure bucket (WI-bifob).
# Additive: every 1.1 key keeps its meaning, and the new key is an empty dict
# when nothing was excluded, so a 1.1 consumer that ignores it still reads a
# correct verdict — but it would UNDERSTATE what the analysis saw, which is
# why this is a version change rather than a silent field addition.
# 1.3 adds the per-verdict ``flow_origins`` breakdown and a per-evidence-row
# ``source_boundary`` (WI-vazal). Also additive, and deliberately so: the
# alternative considered was RELABELLING database reads away from
# ``untrusted_input``, which would have been a silent semantic change to every
# claim already written against that label. Reporting the split instead means
# no published claim changes meaning and no verdict moves.
# 1.4 adds the per-verdict ``analysis_methods`` breakdown and a per-evidence-row
# ``confidence`` / ``analysis_method`` (INV-karud clause c). Additive, and a
# version change rather than a silent field addition for the same reason 1.2
# was: a 1.3 consumer reading a violated verdict cannot tell a flow the
# ADR-0017 §3a walk confirmed by data dependence from one included by call
# reachability alone, and will keep treating every flow as equally supported.
# This release also stops hardcoding the string ``approximate`` into every
# violated verdict's ``details`` — that literal made the label unobservable
# from BOTH surfaces at once, so no consumer could have noticed the difference.
# 1.5 SPLITS the ``excluded_flows`` bucket keys by which rule fired. NOT purely
# additive, and that is the point: a flow formerly counted under
# ``test_sourced`` may now appear under ``benchmark_sourced`` /
# ``fixture_sourced`` / ``mock_sourced`` / ``test_support_sourced``. Which
# flows are excluded does not change at all — ``paths.classify_test_file`` IS
# ``is_test_file``, the boolean being defined as "reason is not None" — only
# what they are called. A 1.4 consumer summing the dict still gets the right
# total; one keyed on the literal ``test_sourced`` now sees only genuine test
# code, which is what that key always claimed to mean. This release also
# splices ``excluded_flows`` and ``flow_origins`` into the VIOLATED path's
# ``details``, closing WI-bifob's stated but unimplemented contract that
# exclusions are disclosed on both paths.
VERIFY_CLAIMS_SCHEMA_VERSION = "1.5"
# WI-kikis: cap on the per-verdict structured drill-down evidence list. A
# violated claim can have thousands of flows (3,969 on the self-corpus); the
# deduplicated ``evidence`` list is bounded to this many DISTINCT flows so the
# JSON stays a reasonable size, while ``evidence_count`` still reports the true
# total and ``details`` discloses the distinct count. 100 distinct source→sink
# paths is far more than a human triages by hand and comfortably covers the
# handful of distinct flows a high-count claim collapses to in practice.
_MAX_EVIDENCE_ROWS = 100


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TaintFlowConstraint:
    """Taint-flow constraint for ADR-0017 claims.

    Attributes:
        source_taint: Taint label that must not reach the sink zone.
        prohibited_sink_zone: Zone where tainted data must not arrive.
        allowed_sanitizers: Sanitizer names that neutralize the taint (optional).
    """

    source_taint: str
    prohibited_sink_zone: str
    allowed_sanitizers: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.allowed_sanitizers is None:
            self.allowed_sanitizers = []


@dataclass
class Claim:
    """A security claim to verify against a boundary map or taint-flow analysis.

    Attributes:
        id: Unique identifier.
        text: Human-readable description.
        constraint_boundary: Which I/O boundary type to check (ADR-0016).
        constraint_must_not_exist: If true, the boundary must have 0 chains.
        constraint_max_chains: Maximum allowed chains for the boundary.
        constraint_taint_flow: Taint-flow constraint (ADR-0017, optional).
    """

    id: str
    text: str
    constraint_boundary: str = ""
    constraint_must_not_exist: bool = False
    constraint_max_chains: Optional[int] = None
    constraint_taint_flow: Optional[TaintFlowConstraint] = None


@dataclass
class ClaimVerdict:
    """Result of verifying a single claim.

    Attributes:
        claim_id: The claim's ID.
        claim_text: The claim's human-readable text.
        verdict: One of "confirmed", "violated", "inconclusive" (ADR-0033
            Phase 3 PR4 / WI-rolol sub-task A). ``confirmed`` means the
            claim was actively checked and held; ``violated`` means
            specific evidence contradicted it; ``inconclusive`` means
            the verification couldn't proceed (no matching constraint,
            broken input data, missing catalog) — distinguished from
            ``confirmed`` to close the silent-confirm fall-through
            class (INV-bitig P0, INV-gobob, INV-mofih, INV-nufob).
        evidence_count: Number of I/O chains that violate the claim (0 if confirmed).
        details: Human-readable explanation.
        evidence: Bounded, deduplicated list of per-flow drill-down records for
            a violated taint claim (WI-kikis). Each entry carries
            ``source_symbol`` / ``sink_symbol`` (the graph node IDs a consumer
            uses to locate the exact edge), the corresponding primitive names,
            and the ``path`` (list of symbol IDs through the call graph). Empty
            for confirmed / inconclusive / boundary verdicts. Capped at
            ``_MAX_EVIDENCE_ROWS`` DISTINCT flows — ``evidence_count`` remains
            the full total, so ``len(evidence) < evidence_count`` signals either
            verbatim-duplicate flows collapsed away or a high-count claim
            truncated to the cap.
        excluded_flows: Counts of flows that MATCHED the claim's constraint but
            were not counted against it, keyed by why (WI-bifob). Empty when
            nothing was excluded. This is a disclosure bucket in the D7 shape —
            a production default plus a labelled, counted account of what the
            default left out — not a silent filter. A silent drop would make
            the tool quieter without making it more honest, and the vanished
            count is exactly what a later session rediscovers as a mystery
            regression. Keys are ``migration_sourced`` plus one per rule that
            ``paths.classify_test_file`` can fire: ``test_sourced``,
            ``mock_sourced``, ``fixture_sourced``, ``benchmark_sourced``,
            ``test_support_sourced``. The split exists because
            ``is_test_file`` is deliberately broad and reporting a ``benches/``
            path as ``test_sourced`` sends the reader looking for a test that
            does not exist. Which flows are excluded is unchanged by the split.
        flow_origins: Counts of the flows this verdict IS about, keyed by the
            io_primitives boundary their source came from (WI-vazal). Empty
            when there are no flows. The taint label alone cannot express
            this: ``AUTO_SOURCE_LABEL_MAP`` collapses ``net_recv``,
            ``ipc_recv`` and ``db_read`` into the single label
            ``untrusted_input``, so "a request body reached the database" and
            "a row read from the database reached the database" were
            indistinguishable — and on an ORM-backed application the second
            dominates. This reports the split WITHOUT relabelling anything,
            so no already-published claim changes meaning. ``declared`` is the
            bucket for YAML-declared sources, which have no boundary.
        analysis_methods: Counts of the flows this verdict IS about, keyed by
            HOW each was adjudicated (INV-karud clause c). Empty when there
            are no counted flows. Same shape and rationale as
            ``flow_origins``: the pipeline computed this and the consumer
            could not see it. ``ddg`` means the ADR-0017 §3a walk found a data
            dependence; ``ddg_mixed`` means the walk ran and did not confirm
            one, so inclusion rests on call-graph reachability; ``structural``
            means no reaching-def data existed for the language and no walk
            was possible. ``confidence`` collapses the last two into
            ``approximate``, which is why this is the finer axis and the one
            worth carrying — "the analysis looked and found nothing" and "the
            analysis could not look" are different facts about a security
            verdict.
    """

    claim_id: str
    claim_text: str
    verdict: str  # axis: bounded-enum — "confirmed" / "violated" / "inconclusive"
    evidence_count: int = 0
    details: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    excluded_flows: dict[str, int] = field(default_factory=dict)
    flow_origins: dict[str, int] = field(default_factory=dict)
    analysis_methods: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "verdict": self.verdict,
            "evidence_count": self.evidence_count,
            "details": self.details,
            "evidence": self.evidence,
            "excluded_flows": self.excluded_flows,
            "flow_origins": self.flow_origins,
            "analysis_methods": self.analysis_methods,
        }


@dataclass
class BoundaryCoverage:
    """Whether the I/O boundary analysis is trustworthy enough to *confirm* a
    zero-chain ``must_not_exist`` / within-limit ``max_chains`` claim.

    A clean (zero-chain) boundary verdict only means "this boundary is unused"
    if the analysis could actually have detected the I/O. Two blind spots make
    a clean verdict untrustworthy (WI-kajil / INV-bitig P0):

    * the analysis produced no call edges at all (empty repo, wrong cwd, or an
      unanalyzable input) — nothing could be traced to an I/O primitive; or
    * a *supported* language (one with an I/O catalog) was analyzed but
      produced zero call edges, so ``io_boundary`` saw none of its I/O — the
      F69.A1 missing-edge-production case (e.g. the JS body-call gap).

    When ``complete`` is ``False``, :func:`verify_claim` returns
    ``inconclusive`` instead of ``confirmed`` so verify-claims never asserts a
    boundary is unused on an analysis that couldn't see it. ``reason`` is a
    human-readable explanation surfaced in the verdict details.
    """

    complete: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# Claim loading
# ---------------------------------------------------------------------------


class ClaimsFileError(Exception):
    """Raised by :func:`load_claims` when the claims YAML cannot be loaded
    as a well-formed set of claims.

    Covers the four failure classes that previously either tracebacked or
    silently degraded the verdict stream — the META-jurig ``load_claims``
    validation gate:

    * malformed YAML (``yaml.YAMLError``) — INV-zurih;
    * an unexpected root / claim / constraint shape — WI-fuhaf;
    * a ``constraint.boundary`` outside the io-boundaries vocabulary —
      INV-gobob / WI-ruzib;
    * an unrecognized field name, e.g. the typo ``constrant`` — WI-bopoz.

    :func:`hypergumbo_core.cli.cmd_verify_claims` catches it, prints
    ``Error: <message>`` to stderr, and exits ``2`` — distinct from ``1``
    (a genuine ``violated`` verdict) so a CI gate keyed on ``rc == 0`` still
    fails closed on a typo'd claim without misreporting a security
    regression, and distinct from ``0`` so malformed input can never read
    as "all confirmed".
    """


# Field-name allowlists for the claims YAML (WI-bopoz). An unrecognized key
# is a typo or a misremembered field; both previously loaded a
# defaults-populated Claim whose ``inconclusive`` verdict was
# indistinguishable from a claim that legitimately lacks a checker.
# Rejecting unknown keys at load time makes the typo loud.
_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"claims", "extra_catalogs"})
_ALLOWED_CLAIM_KEYS: frozenset[str] = frozenset({"id", "text", "constraint"})
_ALLOWED_CONSTRAINT_KEYS: frozenset[str] = frozenset(
    {"boundary", "must_not_exist", "max_chains", "taint_flow"},
)
_ALLOWED_TAINT_FLOW_KEYS: frozenset[str] = frozenset(
    {"source_taint", "prohibited_sink_zone", "allowed_sanitizers"},
)


def _did_you_mean(value: str, vocabulary) -> str:
    """Return a ``' Did you mean: ...?'`` suffix for close matches, else ''.

    Mirrors the subcommand did-you-mean hint in ``cli.py`` (difflib,
    cutoff 0.5) so a near-miss boundary or field name gets a correction.
    """
    import difflib

    close = difflib.get_close_matches(value, sorted(vocabulary), n=3, cutoff=0.5)
    if close:
        return f" Did you mean: {', '.join(close)}?"
    return ""


def _reject_unknown_keys(observed, allowed: frozenset[str], *, where: str) -> None:
    """Raise :class:`ClaimsFileError` if ``observed`` has a key outside ``allowed``.

    The message lists the offending key(s), the allowed set, and a
    did-you-mean hint for the first unknown key (WI-bopoz).
    """
    unknown = sorted(str(key) for key in observed if key not in allowed)
    if not unknown:
        return
    listed = ", ".join(repr(key) for key in unknown)
    allowed_list = ", ".join(sorted(allowed))
    hint = _did_you_mean(unknown[0], allowed)
    raise ClaimsFileError(
        f"unknown {where} field(s): {listed} (allowed: {allowed_list}).{hint}",
    )


def load_extra_catalog_paths(
    path: Path,
) -> tuple[list[Path], list[Path], list[Path]]:
    """Read ``extra_catalogs:`` from a claims YAML and return its path lists.

    The claims file may declare project-local taint catalog files under a
    top-level ``extra_catalogs`` key (WI-votan)::

        extra_catalogs:
          sources:
            - taint/project_sources.yaml
            - taint/extra_sources_dir
          sinks:
            - taint/project_sinks.yaml
          sanitizers: []

    Each entry is a YAML file or a directory of YAML files.  Relative
    paths resolve against the claims-file directory so a repo can keep
    its extra catalogs beside the claims document.

    Returns ``(sources, sinks, sanitizers)`` — each a list of ``Path``
    values that callers can concatenate onto CLI-supplied paths and hand
    to :func:`hypergumbo_core.taint.load_full_taint_catalog`.
    """
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content) or {}
    extras = data.get("extra_catalogs") or {}
    base_dir = path.parent

    def _resolve_rel(raw: object) -> list[Path]:
        if not raw:
            return []
        if not isinstance(raw, list):
            return []
        out: list[Path] = []
        for entry in raw:
            if not isinstance(entry, str):
                continue
            pp = Path(entry)
            if not pp.is_absolute():
                pp = base_dir / pp
            out.append(pp)
        return out

    return (
        _resolve_rel(extras.get("sources")),
        _resolve_rel(extras.get("sinks")),
        _resolve_rel(extras.get("sanitizers")),
    )


def _parse_taint_flow(
    taint_flow_data: object,
    claim_id: str,
) -> Optional[TaintFlowConstraint]:
    """Validate and build the optional ``taint_flow`` sub-constraint (ADR-0017).

    ``None`` (key absent or explicit null) yields ``None``; a non-mapping or
    an unrecognized key raises :class:`ClaimsFileError` rather than being
    silently ignored.
    """
    if taint_flow_data is None:
        return None
    if not isinstance(taint_flow_data, dict):
        raise ClaimsFileError(
            f"taint_flow in claim '{claim_id}' must be a mapping, "
            f"got {type(taint_flow_data).__name__}.",
        )
    _reject_unknown_keys(
        taint_flow_data.keys(),
        _ALLOWED_TAINT_FLOW_KEYS,
        where=f"taint_flow in claim '{claim_id}'",
    )
    return TaintFlowConstraint(
        source_taint=taint_flow_data.get("source_taint", ""),
        prohibited_sink_zone=taint_flow_data.get("prohibited_sink_zone", ""),
        allowed_sanitizers=taint_flow_data.get("allowed_sanitizers", []),
    )


def _parse_claim(entry: object, index: int) -> Claim:
    """Validate and build one :class:`Claim` from a raw YAML entry.

    Enforces the per-entry half of the load gate: the entry must be a
    mapping with only allowed keys; ``constraint`` (if present) must be a
    mapping with only allowed keys; a present ``constraint.boundary`` must
    be in :data:`~hypergumbo_core.io_boundary.KNOWN_IO_BOUNDARIES`.
    """
    if not isinstance(entry, dict):
        raise ClaimsFileError(
            f"claim #{index + 1} must be a mapping, "
            f"got {type(entry).__name__}.",
        )
    claim_id = entry.get("id") or f"#{index + 1}"
    _reject_unknown_keys(
        entry.keys(), _ALLOWED_CLAIM_KEYS, where=f"claim '{claim_id}'",
    )

    constraint = entry.get("constraint")
    if constraint is None:
        constraint = {}
    if not isinstance(constraint, dict):
        raise ClaimsFileError(
            f"constraint in claim '{claim_id}' must be a mapping, "
            f"got {type(constraint).__name__}.",
        )
    _reject_unknown_keys(
        constraint.keys(),
        _ALLOWED_CONSTRAINT_KEYS,
        where=f"constraint in claim '{claim_id}'",
    )

    # Boundary-vocabulary check (INV-gobob / WI-ruzib). Validate against the
    # canonical universe, NOT the keys present in a given boundary map: a
    # repo with zero net_send chains must still accept a
    # ``must_not_exist: net_send`` claim and confirm it. An unknown value
    # here would otherwise make verify_claim's ``boundary_map.entries.get``
    # return None → chain_count 0 → silent "confirmed".
    boundary = constraint.get("boundary", "") or ""
    if boundary and boundary not in KNOWN_IO_BOUNDARIES:
        raise ClaimsFileError(
            f"unknown boundary '{boundary}' in claim '{claim_id}'; valid "
            f"boundaries: {', '.join(sorted(KNOWN_IO_BOUNDARIES))}."
            + _did_you_mean(boundary, KNOWN_IO_BOUNDARIES),
        )

    taint_flow = _parse_taint_flow(constraint.get("taint_flow"), claim_id)

    return Claim(
        id=entry.get("id", ""),
        text=entry.get("text", ""),
        constraint_boundary=boundary,
        constraint_must_not_exist=constraint.get("must_not_exist", False),
        constraint_max_chains=constraint.get("max_chains"),
        constraint_taint_flow=taint_flow,
    )


def load_claims(path: Path) -> list[Claim]:
    """Load and validate security claims from a YAML file.

    Validates the file before constructing any :class:`Claim` (the
    META-jurig ``load_claims`` gate), raising :class:`ClaimsFileError` on
    the first failure so a malformed or typo'd claims file errors loudly
    instead of tracebacking or silently producing a degraded verdict
    stream:

    1. **Readability / shape** (WI-fuhaf): ``path`` must be a file (not a
       directory), decode as UTF-8 text, and parse to a top-level mapping
       with a list-valued ``claims:`` key. An empty file, ``{}``,
       ``claims: []``, ``claims: null`` / ``~``, and an absent ``claims:``
       key all mean "no claims" and load to ``[]``.
    2. **YAML well-formedness** (INV-zurih): ``yaml.YAMLError`` is caught
       and re-raised naming the file and reason.
    3. **Field names** (WI-bopoz): every top-level, claim, constraint, and
       taint-flow key is checked against an allowlist with a did-you-mean
       hint.
    4. **Boundary vocabulary** (INV-gobob / WI-ruzib): a present
       ``constraint.boundary`` must be one of
       :data:`~hypergumbo_core.io_boundary.KNOWN_IO_BOUNDARIES`.

    Args:
        path: Path to the claims YAML file.

    Returns:
        List of Claim objects (possibly empty).

    Raises:
        ClaimsFileError: on any of the validation failures above.
    """
    if path.is_dir():
        raise ClaimsFileError(
            f"claims path is a directory, not a file: {path}",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ClaimsFileError(
            f"claims file is not valid UTF-8 text: {path}",
        ) from exc
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ClaimsFileError(
            f"could not parse claims file {path}: {exc}",
        ) from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ClaimsFileError(
            f"claims file must have a top-level mapping with a 'claims:' key, "
            f"got {type(data).__name__}: {path}",
        )
    _reject_unknown_keys(
        data.keys(), _ALLOWED_TOP_LEVEL_KEYS, where="top-level",
    )

    raw_claims = data.get("claims")
    if raw_claims is None:
        raw_claims = []
    if not isinstance(raw_claims, list):
        raise ClaimsFileError(
            f"'claims:' must be a list, got {type(raw_claims).__name__}: {path}",
        )

    return [_parse_claim(entry, index) for index, entry in enumerate(raw_claims)]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


# Analyzer-produced call edge types used to decide per-language I/O coverage.
# A deliberately registry-clean subset of the edge types ``io_boundary``'s
# ``tag_io_boundaries`` scans (all relationship axis). It omits the
# bridge-family endpoint_shape values (``cgo_bridge``/``wasm_bridge``/...)
# that the tagger lists defensively: those fold into the canonical ``calls``
# relationship (edge_types.py), so a folded FFI edge is already counted via
# ``calls`` — and they are not relationship-axis edge types, so listing them
# here would (correctly) fail the ADR-0023 drift linter. The folded gRPC
# RPC-implementation edge (``implements`` + ``meta['protocol']='grpc'``,
# audit-findings 0016) is counted via the is_grpc_rpc_implementation predicate
# at the loop below, not membership. This set answers "did the analyzer extract
# call structure for this language", which is what the WI-kajil blindness
# signal needs.
_COVERAGE_CALL_EDGE_TYPES: frozenset[str] = frozenset({
    "calls",
    "imports",
    "module_attr_ref",
    "event_publishes",
})


def compute_boundary_coverage(
    raw_edges: list,
    supported_languages: set,
) -> BoundaryCoverage:
    """Decide whether the I/O boundary analysis can support a clean verdict.

    Coverage is derived from *call-edge production*, not from
    ``limits.skipped_languages`` (a dead field — WI-nihir) or from the bare
    fact that an analyzer pass ran (``analysis_runs`` records that a pass
    executed, not that it produced the call structure ``io_boundary`` needs).
    A supported language that was analyzed but emitted zero call edges (of
    :data:`_COVERAGE_CALL_EDGE_TYPES`) is *io-blind*: ``io_boundary`` tags I/O
    on call edges, so it saw none of that language's I/O (F69.A1).

    Args:
        raw_edges: Behavior-map edge dicts (``src`` / ``type`` keys read).
        supported_languages: Languages present in the repo that have an I/O
            catalog (and could therefore have produced boundary chains).

    Returns:
        ``BoundaryCoverage(complete=False, reason=...)`` when no call edges
        were produced at all, or when a supported language produced none;
        otherwise ``BoundaryCoverage(complete=True)``.
    """
    languages_with_calls: set[str] = set()
    total_call_edges = 0
    for edge in raw_edges:
        etype = edge.get("type", "")
        if etype not in _COVERAGE_CALL_EDGE_TYPES and not is_grpc_rpc_implementation(
            etype, edge.get("meta")
        ):
            continue
        total_call_edges += 1
        src = edge.get("src", "")
        if ":" in src:
            languages_with_calls.add(src.split(":", 1)[0])

    if total_call_edges == 0:
        return BoundaryCoverage(
            complete=False,
            reason=(
                "the analysis produced no call edges at all (empty, "
                "wrong directory, or unanalyzable input), so no I/O could "
                "be detected"
            ),
        )

    blind = sorted(supported_languages - languages_with_calls)
    if blind:
        return BoundaryCoverage(
            complete=False,
            reason=(
                f"supported language(s) {', '.join(blind)} were analyzed but "
                f"produced no call edges, so their I/O is invisible to the "
                f"boundary analysis"
            ),
        )

    return BoundaryCoverage(complete=True)


def _default_coverage(boundary_map: BoundaryMap) -> BoundaryCoverage:
    """Coverage inferred from the boundary map alone, for direct callers that
    do not supply richer per-language coverage.

    An empty boundary map (no I/O edges at all) is treated as incomplete — a
    must_not_exist claim cannot be confirmed against an analysis that found no
    I/O whatsoever (INV-bitig). A non-empty map is treated as complete; the CLI
    supplies the stricter per-language signal that also catches a partially
    blind analysis (WI-kajil).
    """
    # INV-bitig gate: "did the analysis SEE any I/O at all?" — count BOTH
    # confirmed I/O (total_io_edges, real categories) AND the external_potential
    # bucket. The WI-huhit/WI-foduh headline redefine excludes external_potential
    # from total_io_edges, but external_potential>0 still means the analysis ran
    # and found (receiver-unresolved) calls, so it is NOT an unanalyzed input;
    # this gate keeps the original "any boundary signal" semantics.
    if (
        boundary_map.total_io_edges == 0
        and boundary_map.external_potential_edges == 0
    ):
        return BoundaryCoverage(
            complete=False,
            reason=(
                "the boundary analysis found no I/O edges at all, so a clean "
                "verdict cannot be distinguished from an unanalyzed input"
            ),
        )
    return BoundaryCoverage(complete=True)


def verify_claim(
    claim: Claim,
    boundary_map: BoundaryMap,
    coverage: Optional[BoundaryCoverage] = None,
) -> ClaimVerdict:
    """Verify a single boundary-constraint claim against a boundary map.

    Args:
        claim: The claim to verify.
        boundary_map: The I/O boundary map to check against.
        coverage: Whether the boundary analysis is trustworthy enough to
            *confirm* a clean (zero-chain / within-limit) verdict. When
            ``None``, coverage is inferred from the map alone
            (:func:`_default_coverage`); the CLI always supplies the stricter
            per-language signal. When coverage is incomplete, a would-be
            ``confirmed`` verdict is downgraded to ``inconclusive`` so the tool
            never asserts a boundary is unused on an analysis that couldn't see
            it (WI-kajil / INV-bitig P0). Coverage never affects ``violated``
            verdicts: found evidence is positive regardless of blind spots.

    Returns:
        ClaimVerdict with the result.
    """
    if coverage is None:
        coverage = _default_coverage(boundary_map)

    entry = boundary_map.entries.get(claim.constraint_boundary)
    chain_count = len(entry.chains) if entry else 0

    # Check must_not_exist constraint
    if claim.constraint_must_not_exist:
        if chain_count == 0:
            if not coverage.complete:
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="inconclusive",
                    details=(
                        f"No {claim.constraint_boundary} chains found, but "
                        f"{coverage.reason}; cannot confirm the boundary is "
                        f"unused."
                    ),
                )
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="confirmed",
                details=f"No {claim.constraint_boundary} chains found.",
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="violated",
            evidence_count=chain_count,
            details=(
                f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                f"but claim requires none."
            ),
        )

    # Check max_chains constraint
    if claim.constraint_max_chains is not None:
        if chain_count <= claim.constraint_max_chains:
            if not coverage.complete:
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="inconclusive",
                    details=(
                        f"{chain_count} {claim.constraint_boundary} chain(s) "
                        f"found, within limit of {claim.constraint_max_chains}, "
                        f"but {coverage.reason}; cannot confirm the limit holds."
                    ),
                )
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="confirmed",
                details=(
                    f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                    f"within limit of {claim.constraint_max_chains}."
                ),
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="violated",
            evidence_count=chain_count,
            details=(
                f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                f"exceeds limit of {claim.constraint_max_chains}."
            ),
        )

    # No constraint matched — inconclusive (ADR-0033 Phase 3 PR4 /
    # WI-rolol sub-task A). Previously fell through to verdict="confirmed"
    # with details="No constraint to check.", silently confirming claims
    # that had no machinery to verify them (INV-bitig P0).
    return ClaimVerdict(
        claim_id=claim.id,
        claim_text=claim.text,
        verdict="inconclusive",
        details=(
            "No machine-checkable constraint on this claim. The claim "
            "may be true, but verify-claims has nothing to assert against."
        ),
    )


def _flow_identity(v: "TaintFlowFinding") -> tuple[Any, ...]:
    """Full per-flow identity of a taint finding (WI-kikis).

    Two findings are "the same flow" iff their source/sink symbols, primitive
    names, and call-graph path all match — so verbatim-duplicate findings
    collapse while flows that merely share a primitive NAME (distinct symbols)
    stay distinct.
    """
    return (
        v.source_primitive,
        v.source_symbol,
        v.sink_primitive,
        v.sink_symbol,
        tuple(v.path),
    )


def _render_flow(v: "TaintFlowFinding") -> str:
    """Render one violating flow with its drill-down identity (WI-kikis):
    ``<source_primitive> [<source_symbol>] -> <sink_primitive> [<sink_symbol>]``
    plus a hop count when the path routes through intermediate nodes."""
    row = (
        f"{v.source_primitive} [{v.source_symbol}] -> "
        f"{v.sink_primitive} [{v.sink_symbol}]"
    )
    # path = [source, ...intermediate..., sink]; hops are the interior nodes.
    if len(v.path) > 2:
        row += f" via {len(v.path) - 2} hop(s)"
    return row


def _flow_evidence_dict(v: "TaintFlowFinding") -> dict[str, Any]:
    """Structured per-flow drill-down record for the verdict ``evidence`` list
    (WI-kikis) — the machine-readable counterpart of :func:`_render_flow`."""
    return {
        "source_symbol": v.source_symbol,
        "source_primitive": v.source_primitive,
        "source_module": v.source_module,
        "source_boundary": v.source_boundary,
        "sink_symbol": v.sink_symbol,
        "sink_primitive": v.sink_primitive,
        # WI-joruv: the MODULE the matched catalog entry declares, which is
        # frequently not recoverable from ``sink_symbol``. A Go sink emits
        # ``go:net/http:0-0:Do:external_symbol`` (package) while the catalog
        # entry is ``net/http.Client.Do`` (package.Type). Emitting only the
        # symbol leaves a *correct* match unverifiable — a reader cannot tell
        # it from a short-name collision without re-running the matcher,
        # which is precisely the "realizable by lookup" property INV-karud
        # asks for. Pure provenance: this changes no match.
        "sink_module": v.sink_module,
        "path": list(v.path),
        # INV-karud (c): HOW this flow was adjudicated. Both fields existed on
        # the finding and neither reached the record, so a reader could not
        # tell a flow the ADR-0017 §3a walk confirmed by data dependence from
        # one included by call reachability alone — and the whole point of a
        # `precise` label is that a consumer can act on it differently. This is
        # the same "the pipeline computed it and then discarded it" shape that
        # `source_module` (WI-joruv) and `source_boundary` (WI-vazal) closed on
        # this very record.
        "confidence": v.confidence,
        "analysis_method": v.analysis_method,
    }


#: Disclosure-bucket keys for flows excluded from a claim verdict (WI-bifob).
#:
#: ``SOURCE_SCOPE_TEST`` is no longer the only test-side bucket. ``is_test_file``
#: is deliberately BROAD — it also matches ``bench/``, ``fixtures/``,
#: ``testdata/``, ``mocks/``, ``harnesses/`` and ``fv/`` — and reporting all of
#: that as ``test_sourced`` told the reader something false about, say, a
#: benchmark directory. The owner ruling (2026-08-03) was to KEEP the breadth
#: and DISCLOSE which rule fired, so the bucket key now carries the reason
#: ``paths.classify_test_file`` returned. ``test_sourced`` keeps its exact
#: former meaning for genuine test code, so a consumer keyed on it does not
#: silently lose the case it cared about — it stops being credited with the
#: others.
SOURCE_SCOPE_PRODUCTION = "production"
SOURCE_SCOPE_TEST = "test_sourced"
SOURCE_SCOPE_MIGRATION = "migration_sourced"


def _symbol_path_slot(symbol_id: str) -> str:
    """Extract the path slot from ``{lang}:{path}:{span}:{name}:{kind}``.

    RIGHT-ANCHORED deliberately. Per ADR-0036 (D1a) the path slot is the one
    colon-TOLERANT slot in the grammar — ``dart:dart:io:0-0:module:module`` has
    path ``dart:io`` — while lang/span/name/kind are colon-free. So the parse
    takes the last three tokens as span/name/kind and joins everything between
    the language and that suffix. ``ir._extract_path_slot`` returns ``parts[1]``
    and is wrong for exactly this case; ``ir._parse_dangling_id`` right beside
    it is correct and documents why. Not folding those two here, but this must
    not become a third naive copy.

    Returns the empty string for anything that is not a well-formed id, which
    the callers treat as "not test, not migration" — an unparseable source is
    not evidence for excluding a flow.
    """
    parts = symbol_id.split(":") if symbol_id else []
    if len(parts) < 5:
        return ""
    return ":".join(parts[1:-3])


def _source_scope(finding: "TaintFlowFinding") -> str:
    """Classify a flow by whether its SOURCE is production code.

    The SOURCE side is what is classified, not the sink. A test that reaches a
    real network primitive is still a test doing its job; a production function
    that reaches a primitive inside a test helper is still a production flow.
    The question the claim asks is "does the shipped application do this", and
    that is decided by where the data enters.
    """
    path = _symbol_path_slot(getattr(finding, "source_symbol", "") or "")
    if not path:
        return SOURCE_SCOPE_PRODUCTION
    # ``classify_test_file`` IS ``is_test_file`` — the boolean is defined as
    # "reason is not None" — so asking for the reason changes which flows are
    # excluded not at all. It changes only what the disclosure is allowed to
    # call them. Re-deriving the categories here instead would put a second
    # copy of a production classification in a consumer (L53).
    reason = classify_test_file(path)
    if reason is not None:
        return f"{reason}_sourced"
    if is_migration_file(path):
        return SOURCE_SCOPE_MIGRATION
    return SOURCE_SCOPE_PRODUCTION


def verify_taint_claim(
    claim: Claim,
    findings: list,
    include_non_production: bool = False,
) -> ClaimVerdict:
    """Verify a single taint-flow claim against propagation findings.

    Checks whether any TaintFlowFinding matches the claim's constraint:
    the source taint label flows to the prohibited sink zone without
    being sanitized.

    Args:
        claim: The claim with a taint_flow constraint.
        findings: List of TaintFlowFinding objects from propagation.
        include_non_production: Count flows whose SOURCE is test, fixture or
            migration code against the claim. Default False (WI-bifob): those
            flows are excluded from the verdict and disclosed in the verdict's
            ``excluded_flows`` bucket instead. Set True to restore the previous
            behavior of treating every source as in scope.

    Returns:
        ClaimVerdict with the result. ``excluded_flows`` reports what the
        production default set aside, on both the confirmed and violated paths.
    """
    tf = claim.constraint_taint_flow
    if tf is None:
        # ADR-0033 Phase 3 PR4 / WI-rolol sub-task A: silent-confirm
        # fall-through. Previously returned verdict="confirmed" with
        # details="No taint_flow constraint to check.", which silently
        # confirms claims that have no taint_flow machinery to verify
        # them.
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="inconclusive",
            details=(
                "No taint_flow constraint on this claim. "
                "verify_taint_claim cannot assert anything."
            ),
        )

    # Filter findings matching this claim's taint label and sink zone
    matching = [
        f for f in findings
        if f.taint_label == tf.source_taint
        and f.sink_zone == tf.prohibited_sink_zone
        and not f.sanitized
    ]

    # WI-bifob: production is the default scope, and what the default leaves
    # out is DISCLOSED rather than dropped. A test that opens a listener is not
    # a network-exposure finding about the product, and a migration that writes
    # to the database is not an untrusted-input finding — yet because a claim
    # verdict is a DISJUNCTION over its flows, a single such flow was enough to
    # hold a whole claim at `violated` forever, and no amount of precision work
    # elsewhere could move it. Measured on the 9-repo cohort: 2 of 18 violated
    # claims rested entirely on one test-sourced flow each (a conftest.py
    # reading an env var, a _test.go dialling TLS).
    #
    # Nobody ever decided taint should count test code; it simply was never
    # wired, which is the tell that this was an unmade decision rather than a
    # considered scope.
    excluded_flows: dict[str, int] = {}
    if include_non_production:
        violations = matching
    else:
        violations = []
        for finding in matching:
            scope = _source_scope(finding)
            if scope == SOURCE_SCOPE_PRODUCTION:
                violations.append(finding)
            else:
                excluded_flows[scope] = excluded_flows.get(scope, 0) + 1

    if not violations:
        excluded_clause = ""
        if excluded_flows:
            # State the exclusions on the CONFIRMED path especially. This is
            # where a silent filter would be most misleading: the claim reads
            # as clean, and the reader has no way to learn that flows existed
            # and were set aside by a policy they did not choose.
            parts = ", ".join(
                f"{count} {reason}"
                for reason, count in sorted(excluded_flows.items())
            )
            excluded_clause = (
                f" Excluded from this verdict as non-production: {parts} "
                f"(pass --include-non-production-sources to count them)."
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="confirmed",
            details=(
                f"No unsanitized {tf.source_taint} data reaches "
                f"{tf.prohibited_sink_zone} zone."
                f"{excluded_clause}"
            ),
            excluded_flows=excluded_flows,
        )

    # Build detailed violation message with per-flow drill-down evidence
    # (WI-kikis). The previous rendering collapsed every violation to a bare
    # "<source_primitive> -> <sink_primitive>" pair and showed the first 5 —
    # which, on high-count claims (3,969 flows on the self-corpus), were
    # frequently VERBATIM DUPLICATES, because many distinct source/sink SYMBOLS
    # share a primitive NAME. The consumer saw the same string five times and
    # had no way to locate any individual flow (no symbol, no path, no edge).
    # Fix: (a) deduplicate on the full per-flow identity (source/sink symbol +
    # primitive + call-graph path) so genuinely-identical findings collapse and
    # the shown rows are DISTINCT; (b) render each shown row WITH its symbol IDs
    # (and path-hop count) so distinct flows are visibly distinct and drillable;
    # (c) attach the bounded structured ``evidence`` list to the verdict so a
    # consumer can triage programmatically even at high evidence counts.
    distinct_violations: list["TaintFlowFinding"] = []
    _seen_flows: set[tuple[Any, ...]] = set()
    for v in violations:
        identity = _flow_identity(v)
        if identity in _seen_flows:
            continue
        _seen_flows.add(identity)
        distinct_violations.append(v)

    paths_desc = "; ".join(_render_flow(v) for v in distinct_violations[:5])
    suffix = ""
    if len(distinct_violations) > 5:
        suffix = (
            f" (and {len(distinct_violations) - 5} more distinct flow(s))"
        )
    # Only disclose the distinct count when it differs from the raw total, so
    # the common no-duplicates case stays terse.
    distinct_clause = ""
    if len(distinct_violations) < len(violations):
        distinct_clause = f" ({len(distinct_violations)} distinct)"

    # WI-vazal: report WHICH boundary each counted flow entered through.
    # The label cannot carry it — three boundaries share `untrusted_input`.
    flow_origins: dict[str, int] = {}
    for finding in violations:
        origin = getattr(finding, "source_boundary", "") or "declared"
        flow_origins[origin] = flow_origins.get(origin, 0) + 1

    # INV-karud (c): report HOW each counted flow was adjudicated.
    #
    # Both breakdowns count `violations` — every flow the verdict rests on —
    # NOT `distinct_violations[:_MAX_EVIDENCE_ROWS]`. The shown rows are
    # deduplicated and capped, so a breakdown over them would silently
    # disagree with the `len(violations)` count printed beside it in `details`.
    analysis_methods: dict[str, int] = {}
    confidences: dict[str, int] = {}
    for finding in violations:
        method = getattr(finding, "analysis_method", "") or "structural"
        analysis_methods[method] = analysis_methods.get(method, 0) + 1
        conf = getattr(finding, "confidence", "") or "approximate"
        confidences[conf] = confidences.get(conf, 0) + 1

    # AGGREGATION POLICY, stated rather than left to `max()`. A verdict is a
    # DISJUNCTION over its flows, and after ADR-0017 §3a those flows can be
    # adjudicated differently — some by a data-dependence walk, some by call
    # reachability alone. Collapsing upward would claim a precision most flows
    # did not have; collapsing downward would erase the ones that earned it.
    # So the composition is reported. The single-value case stays terse (the
    # same shape as `distinct_clause` above), which keeps today's rendering
    # byte-identical for a verdict whose flows all agree — and today they
    # nearly all do, which is exactly why the hardcoded literal went unnoticed.
    if len(confidences) == 1:
        confidence_clause = next(iter(confidences))
    else:
        confidence_clause = ", ".join(
            f"{count} {name}" for name, count in sorted(confidences.items())
        )

    # WI-bifob's own stated contract is that exclusions are disclosed "on the
    # CONFIRMED path as well as the violated one". Only the confirmed path
    # implemented it: on the violated path `excluded_flows` was attached to the
    # dataclass and never rendered, and `cli.py` prints `details` alone — so a
    # TEXT-mode reader of a violated claim never learned that flows were set
    # aside by a policy they did not choose. `flow_origins` (WI-vazal) reached
    # no text surface at all, on either path, which mattered most exactly where
    # it was measured: all 140 flows on pretix's largest violated claim are
    # database-read-to-database-write, and that is decision-changing.
    #
    # Both are spliced into `details` rather than rendered by the CLI, because
    # `details` is where this module already puts the confirmed-path exclusion
    # clause — one home for the prose, not two.
    origins_clause = ", ".join(
        f"{count} {name}" for name, count in sorted(flow_origins.items())
    )
    excluded_clause = ""
    if excluded_flows:
        parts = ", ".join(
            f"{count} {reason}"
            for reason, count in sorted(excluded_flows.items())
        )
        excluded_clause = (
            f" Excluded from this verdict as non-production: {parts} "
            f"(pass --include-non-production-sources to count them)."
        )

    return ClaimVerdict(
        claim_id=claim.id,
        claim_text=claim.text,
        verdict="violated",
        evidence_count=len(violations),
        evidence=[
            _flow_evidence_dict(v)
            for v in distinct_violations[:_MAX_EVIDENCE_ROWS]
        ],
        details=(
            f"{len(violations)} unsanitized {tf.source_taint} flow(s)"
            f"{distinct_clause} "
            f"to {tf.prohibited_sink_zone} zone "
            f"[{tf.source_taint} confidence: {confidence_clause}] "
            f"[origins: {origins_clause}]: "
            f"{paths_desc}{suffix}{excluded_clause}"
        ),
        excluded_flows=excluded_flows,
        flow_origins=flow_origins,
        analysis_methods=analysis_methods,
    )


def verify_claims(
    claims: list[Claim],
    boundary_map: BoundaryMap,
    taint_findings: list | None = None,
    coverage: Optional[BoundaryCoverage] = None,
    include_non_production: bool = False,
) -> list[ClaimVerdict]:
    """Verify all claims against boundary map and/or taint-flow findings.

    Claims with ``constraint_taint_flow`` are verified against taint findings.
    Claims with boundary constraints are verified against the boundary map.

    Args:
        claims: List of claims to verify.
        boundary_map: The I/O boundary map to check against.
        taint_findings: Optional list of TaintFlowFinding objects.
        coverage: Boundary-analysis coverage signal, passed through to
            :func:`verify_claim` for boundary claims (WI-kajil). Taint claims
            have their own unsupported-language signal (INV-javam) and are
            unaffected.
        include_non_production: Count test/fixture/migration-sourced taint
            flows against taint claims (WI-bifob). Default False; excluded
            flows are disclosed per-verdict in ``excluded_flows``. Boundary
            claims are unaffected.

    Returns:
        List of ClaimVerdict objects, one per claim.
    """
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        if claim.constraint_taint_flow is not None:
            verdicts.append(verify_taint_claim(
                claim, taint_findings or [],
                include_non_production=include_non_production,
            ))
        else:
            verdicts.append(verify_claim(claim, boundary_map, coverage=coverage))
    return verdicts
