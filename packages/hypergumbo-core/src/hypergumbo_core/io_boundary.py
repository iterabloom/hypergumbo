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
from typing import TYPE_CHECKING, Any, Iterable, Optional

import yaml

from .edge_types import is_grpc_rpc_implementation
from .ir import symbol_name_slot, symbol_path_slot

if TYPE_CHECKING:
    from .ir import Edge


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
    """

    boundary: str
    module: str
    name: str
    kind: str  # "function" or "method"
    notes: str = ""

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
    _by_short: dict[str, list[IoPrimitive]] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        """Build lookup indices."""
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """Rebuild the qualified-name and short-name lookup dicts."""
        self._by_qualified.clear()
        self._by_short.clear()
        for p in self.primitives:
            # Qualified name: first one wins (shouldn't have duplicates)
            if p.qualified_name not in self._by_qualified:
                self._by_qualified[p.qualified_name] = p
            # WI-vipur: also register a dot-normalized alias so edges
            # emitted in scoped-path mode (``::`` replaced with ``.`` to
            # avoid colliding with the ``:``-delimited edge ID format)
            # still hit the qualified index.  Only relevant for languages
            # whose catalog module names contain ``::`` (Rust, C++).
            dot_form = p.qualified_name.replace("::", ".")
            if dot_form != p.qualified_name:
                self._by_qualified.setdefault(dot_form, p)
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
        # Qualified match is unique
        hit = self._by_qualified.get(name)
        if hit is not None:
            return [hit]
        return list(self._by_short.get(name, []))

    def lookup_with_module(
        self, name: str, module_hint: str | None = None,
        *, call_construct: str | None = None,
        allow_short_name_fallback: bool = True,
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
        """
        # Qualified-name match always wins (exact)
        hit = self._by_qualified.get(name)
        if hit is not None:
            return hit

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
                return filtered[0]
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

    def is_stdlib_module_complete(self, module: str) -> bool:
        """Return True when ``module`` is flagged closed-world complete.

        Closed-world means we've enumerated every I/O primitive in this
        module, so an unmatched call to ``module.X`` is provably NOT
        I/O. The F3 PR-C Filter 2 short-circuit consults this method
        before suppressing ``external_potential`` chains.
        """
        return module in self.stdlib_module_completeness

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

                for func_name in entry.get("functions", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=func_name,
                        kind="function",
                        notes=notes,
                    ))
                for method_name in entry.get("methods", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=method_name,
                        kind="method",
                        notes=notes,
                    ))
                for attr_name in entry.get("attributes", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=attr_name,
                        kind="attribute",
                        notes=notes,
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
                # Adding to completeness implies the module is stdlib —
                # auto-promote so callers don't have to keep both
                # sections in sync.
                stdlib_modules_set.add(name)
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


def load_catalog(language: str) -> IoBoundaryCatalog:
    """Load the I/O primitive catalog for a language.

    Looks for ``io_primitives/<language>.yaml`` relative to this module.
    Falls back to language aliases (e.g. cpp → c) if no exact match.
    When a language has a parent catalog (e.g. scala → java), the child
    catalog is loaded first and then merged with the parent so that
    child entries take precedence while parent entries fill in gaps.
    Returns an empty catalog if no catalog is found.
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
        if module_hint and catalog.is_stdlib_module_complete(module_hint):
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
        chain = IoChain(
            boundary=boundary,
            primitive=primitive,
            io_edge_src=edge.src,
            io_edge_dst=edge.dst,
            entry_points=chain_eps,
            dst_tier=dst_tier,
            dst_tier_name=dst_tier_name,
            dst_external_boundary=dst_external,
        )
        by_boundary.setdefault(boundary, []).append(chain)

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


def tag_io_boundaries(
    edges: list[Edge],
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    call_types: frozenset[str] = frozenset({
        "calls", "imports",
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

        # Extract language from dst ID (first colon-delimited segment)
        dst_parts = edge.dst.split(":")
        lang = dst_parts[0]

        # WI-tihup: prefer the structured ExternalRef when present.
        # ``getattr`` keeps MockEdge-style test doubles without the
        # attribute working.
        edge_dst_ref = getattr(edge, "dst_ref", None)
        if edge_dst_ref is not None:
            callee = edge_dst_ref.name
            module_hint = edge_dst_ref.module_path
        else:
            callee = _extract_callee_name(edge.dst)
            module_hint = _extract_module_hint(edge.dst)

        # Try FFI pseudo-namespace redirect first (e.g., go:C: → c catalog),
        # then fall back to the primary language catalog.
        catalog, adjusted_hint = _resolve_ffi_catalog(
            lang, module_hint, catalogs,
        )
        if catalog is None:
            continue

        # io-boundary:F3 — thread the edge's call construct so the no-module
        # gate can reject untyped method calls (no receiver evidence).
        cc = (getattr(edge, "meta", None) or {}).get("call_construct")
        match = catalog.lookup_with_module(
            callee, adjusted_hint, call_construct=cc,
            # INV-sapit: this call resolved to a callable defined in the analysed
            # repository, so a bare short-name collision with a stdlib primitive is a
            # different function. Exact-qualified and module-filtered matches are
            # unaffected — only the ungated short-name fallback is withheld.
            allow_short_name_fallback=not is_first_party_callable_dst(edge.dst),
        )
        if match is None:
            continue

        if edge.meta is None:
            edge.meta = {}
        edge.meta["io_boundary"] = match.boundary
        edge.meta["io_primitive"] = match.qualified_name
        tagged += 1

    return tagged
