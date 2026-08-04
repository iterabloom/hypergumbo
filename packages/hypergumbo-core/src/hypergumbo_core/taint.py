# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taint catalog loading and taint-flow propagation (ADR-0017 Phases 1-2).

Provides YAML-driven taint source/sink/sanitizer catalogs and a structural
(call-graph BFS) taint-flow analyzer. This is the Phase 1 fallback path
that works for all languages without requiring def/use extractors.

How It Works
------------
1. ``load_taint_catalog()`` reads YAML files defining taint sources (functions
   whose return values carry taint labels), sinks (functions that should not
   receive tainted data), and sanitizers (functions that transform taint).

2. ``propagate_taint_structural()`` performs two-phase BFS on the call graph:
   (a) compute nodes reachable from each taint source without passing through
   sanitizers for the relevant taint label, (b) check if any sink is in that
   reachable set. Reports violations as ``TaintFlowFinding`` objects.

The structural approach cannot distinguish between two variables in the same
function — it operates at the symbol level. Findings are explicitly labeled
``confidence="approximate"`` and ``analysis_method="structural"`` per ADR-0017.
DDG-backed analysis (Phase 2+) will improve precision for languages with
def/use extractors.

Catalog Format
--------------
Sources, sinks, and sanitizers use YAML files following patterns established
by the IO primitive catalogs (ADR-0016). See ``taint_sources/`` and
``taint_sanitizers/`` directories alongside this module, or project-local
catalogs provided via ``--taint-sources``, etc. Built-in sinks are derived
automatically from ``io_primitives/*.yaml`` — every write-side IO primitive
becomes an ``untrusted`` sink in a zone determined by its boundary category
(see :data:`AUTO_SINK_ZONE_MAP` below). Project-local sink overrides flow
through the ``--taint-sinks`` CLI flag.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Iterator, Optional, TypeVar, Union

import yaml

from .edge_types import is_grpc_rpc_implementation

if TYPE_CHECKING:
    from .cfg import DdgEdge
    from .function_summaries import FunctionSummary


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintSource:
    """A function/method/attribute whose return-or-read value carries a taint label.

    Attributes:
        taint_label: The taint category (e.g. "plaintext", "key_material",
            "host_secret", "untrusted_input").
        module: The module or class path (e.g. "cryptography.fernet", "os").
        name: The function/method/attribute name (e.g. "Fernet.decrypt", "environ").
        kind: One of "function", "method", or "attribute".  ``"attribute"``
            covers bare reads like ``os.environ`` or ``sys.argv``; pairs with
            ``module_attr_ref`` edges emitted by the language analyzer
            (WI-guhok for Python; WI-gapam follow-up for tree-sitter langs).
        return_tainted: Whether the return (or read) value is tainted.
        argument_tainted: Indices of arguments that become tainted (optional).
        start_at: BFS seed origin. Default ``"caller"`` (original semantics)
            seeds at the function that *calls* a source — appropriate for
            value-tainting sources like ``os.environ.get`` or
            ``Fernet.decrypt``. ``"callee"`` seeds at the source-callee
            symbol itself, which models reachability-from-entry semantics
            for synthetic entry-point sources: a CLI handler like
            ``cmd_sketch`` declared with ``start_at: callee`` taints every
            symbol reachable downstream of itself, not every symbol
            reachable downstream of its dispatcher. Project-local
            catalogs declaring runtime/extras/dev CLI handlers as sources
            use ``"callee"`` so reachability is precisely scoped.
    """

    taint_label: str
    module: str
    name: str
    kind: str  # "function", "method", or "attribute"
    return_tainted: bool = True
    argument_tainted: tuple[int, ...] = ()
    start_at: str = "caller"  # "caller" or "callee"
    # WI-vazal: the io_primitives boundary this source was auto-derived from,
    # drawn from io_boundary.CATALOG_BOUNDARY_TYPES. Empty string for a
    # YAML-DECLARED source (crypto, key_material, project-local catalogs),
    # which has no boundary because it did not come from an io_primitives
    # entry.
    #
    # WHY IT IS CARRIED. AUTO_SOURCE_LABEL_MAP collapses THREE boundaries —
    # net_recv, ipc_recv and db_read — into the single label
    # `untrusted_input`, and until now discarded which one it was. So "a
    # request body reached the database" and "a row read from the database
    # reached the database" were the same fact downstream, and on an
    # ORM-backed application the second dominates: 93 of the 100 displayed
    # rows on pretix's largest violated claim are database-read to
    # database-write. Keeping the boundary makes them separable WITHOUT
    # changing the label, so no already-published claim changes meaning.
    source_boundary: str = ""

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSink:
    """A function/method/attribute that should not receive tainted data.

    Attributes:
        zone: The trust zone (e.g. "host_fs", "network", "host_env", "ipc",
            "browser_storage", "relay").
        trust_level: The trust level (e.g. "untrusted", "semi-trusted").
        module: The module or class path.
        name: The function/method/attribute name.
        kind: One of "function", "method", or "attribute".
    """

    zone: str
    trust_level: str
    module: str
    name: str
    kind: str  # "function", "method", or "attribute"

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSanitizer:
    """A function that transforms one taint label into another.

    Attributes:
        input_taint: The taint label consumed (e.g. "plaintext").
        output_taint: The taint label produced (e.g. "ciphertext").
        qualified_name: Full dotted name (e.g. "cryptography.fernet.Fernet.encrypt").
    """

    input_taint: str
    output_taint: str
    qualified_name: str

    @property
    def short_name(self) -> str:
        """Extract the shortest unambiguous suffix.

        For dotted names (Python style), takes the last two segments:
        "cryptography.fernet.Fernet.encrypt" → "Fernet.encrypt"

        For double-colon names (Rust style), takes the last segment:
        "aes_gcm::Aes256Gcm::encrypt" → "encrypt"
        """
        if "::" in self.qualified_name:
            return self.qualified_name.rsplit("::", 1)[-1]
        parts = self.qualified_name.rsplit(".", 2)
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return self.qualified_name


# Catalog entries that flow through the callee index and the user-override
# merge. Both expose the (module, name, kind) triple those helpers key on.
# Deliberately NOT widened to TaintSanitizer: every call site passes a
# source or sink list, and widening would claim a capability untested here.
TaintEntry = Union[TaintSource, TaintSink]

# The user-override merge is type-PRESERVING: hand it sources and you get
# sources back. Widening its return to TaintEntry would let a merged
# source+sink mapping be assigned onto a sources-only catalog field.
TEntry = TypeVar("TEntry", bound=TaintEntry)


@dataclass
class TaintFlowFinding:
    """A reported taint-flow violation or confirmed path.

    Attributes:
        taint_label: The taint category that flowed to the sink.
        source_symbol: Symbol ID of the function containing the taint source.
        source_primitive: Name of the taint source function.
        sink_symbol: Symbol ID of the sink function call.
        sink_primitive: Name of the sink function.
        source_module / sink_module: The MODULE declared by the catalog entry
            that matched, which is not recoverable from the emitted symbol.
            An emitted symbol may record a package (``go:net/http:0-0:Do``)
            where the catalog records package.Type (``net/http.Client``);
            without both, a reader cannot tell a correct match from a
            short-name collision without re-running the matcher.
        sink_zone: Trust zone of the sink.
        sanitized: Whether all paths from source to sink are sanitized.
        confidence: ``precise`` only where the ADR-0017 §3a walk actually
            confirmed a data dependence; ``approximate`` everywhere else. NOT
            "DDG-backed" — running the DDG is not the same as having used it,
            and stamping ``precise`` on the strength of a walk whose result was
            discarded is exactly what INV-sadah was filed for.
        analysis_method: ``structural`` (no reaching-def data for the language,
            so no walk was possible), ``ddg`` (the walk ran and confirmed a
            dependence), or ``ddg_mixed`` (the walk ran and did not confirm
            one, so inclusion rests on call-graph reachability). ``confidence``
            collapses the last two into ``approximate``; this is the finer
            axis, because "the analysis looked and found nothing" and "the
            analysis could not look" are different facts.
        path: Symbol IDs along ONE call-graph route from source to sink —
            a witness, not "the path". A source frequently reaches a sink by
            several equally-valid routes and this reports one of them; the
            others are real and are not listed. Which one is a DECLARED
            tie-break (BFS visiting each node's callees in sorted id order),
            not an artifact of iteration order: adjacency is set-valued, so
            before INV-havos the winner followed ``str`` set iteration and
            therefore ``PYTHONHASHSEED``, and two runs of the same binary on
            an unchanged repo reported different middle hops for the same
            flow. Reproducible now, but still one witness — a consumer that
            needs "every route" must not read this field as if it were that.
    """

    taint_label: str
    source_symbol: str
    source_primitive: str
    sink_symbol: str
    sink_primitive: str
    sink_zone: str
    sanitized: bool
    confidence: str  # "approximate" or "precise"
    # "structural" / "ddg" / "ddg_mixed" — ddg_mixed was assigned by the
    # propagator while this comment named only two values, so a reader
    # enumerating the vocabulary from here got it wrong.
    analysis_method: str
    path: list[str] = field(default_factory=list)
    source_module: str = ""
    sink_module: str = ""
    # WI-vazal: the io_primitives boundary the SOURCE was derived from
    # (io_boundary.CATALOG_BOUNDARY_TYPES), carried through from the matched
    # TaintSource. Empty for a YAML-declared source, which has no boundary.
    # Lets a consumer separate "data off the wire reached the database" from
    # "a row read from the database reached the database" without either
    # flow's taint_label changing — so no published claim changes meaning.
    source_boundary: str = ""

    @property
    def verdict(self) -> str:
        """Return verdict string based on sanitization status."""
        return "confirmed_safe" if self.sanitized else "violated"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return {
            "taint_label": self.taint_label,
            "source_symbol": self.source_symbol,
            "source_primitive": self.source_primitive,
            "sink_symbol": self.sink_symbol,
            "sink_primitive": self.sink_primitive,
            "sink_zone": self.sink_zone,
            "verdict": self.verdict,
            "sanitized": self.sanitized,
            "confidence": self.confidence,
            "analysis_method": self.analysis_method,
            "path": self.path,
        }


# ---------------------------------------------------------------------------
# Catalog container
# ---------------------------------------------------------------------------


# The two spellings the analyzers use for "receiver module could not be
# recovered". ADR-0017 §3a exempts both from module filtering; keeping them in
# one frozenset is what stops the pair drifting apart again — the live sink
# matcher tested only the bare spelling for months while the (never-wired)
# _sink_module_compatible tested both.
_UNRESOLVED_MODULE_PLACEHOLDERS = frozenset({"external", "<external>"})


def _lookup_named_entry(
    hits: Sequence[TaintEntry] | None,
    callee_name: str,
    module_hint: str | None,
    ambiguous_names: frozenset[str],
    call_construct: str | None = None,
):
    """Pick the matching catalog entry from ``hits``, mirroring
    :meth:`io_boundary.IoBoundaryCatalog.lookup_with_module` (WI-razol).

    ``hits`` is the index bucket for ``callee_name`` (entries registered under
    that short OR qualified name); each entry exposes ``module`` / ``name`` /
    ``kind`` attributes.

    * No hits → ``None``.
    * With a usable module hint, filter to entries whose ``module`` matches
      (via :func:`io_boundary._module_matches`) and return the first match;
      if none match, return ``None`` — a present-but-mismatched module means
      this is not the catalogued primitive (e.g. ``sys.stdout.write`` is not
      ``asyncio.StreamWriter.write``; F156.A1).
    * With no usable module hint, delegate to the shared kind-aware gate
      (:func:`io_boundary.gate_named_entry`, io-boundary:F3): an untyped
      method call (``call_construct == "method"``) has no receiver evidence
      and never matches (INV-tapat/INV-maluk — ``str.replace`` must not match
      ``Path.replace``), a free-function call may match only a function-kind
      hit, and the ``ambiguous_names`` short-name set is retained as the
      meta-absent / non-Python safety net.

    A qualified ``callee_name`` (e.g. ``"os.replace"`` /
    ``"pathlib.Path.write_text"``) carries its own receiver evidence (the full
    module path), so an exact ``qualified_name`` match wins regardless of
    ambiguity OR kind — mirroring :meth:`lookup_with_module`'s qualified-name-
    first branch, which runs before its kind-aware gate (io-boundary:F3).
    """
    if not hits:
        return None
    # Both spellings of the unresolved-receiver placeholder are exempted, per
    # ADR-0017 §3a: when the analyzer could not recover module information,
    # degrade to short-name matching rather than reject, because rejecting
    # outright suppresses legitimate findings. `<external>` was missing here
    # (only the bare `external` was tested), so it fell into the module-FILTER
    # branch below and was compared as though it were a real module name —
    # matching nothing and silently dropping the finding. Harvested from the
    # retired `_sink_module_compatible`, which had it right.
    if module_hint and module_hint not in _UNRESOLVED_MODULE_PLACEHOLDERS:
        from .io_boundary import _module_matches
        for h in hits:
            if _module_matches(h.module, module_hint):
                return h
        return None
    # Exact qualified-name match carries its own receiver evidence — allow it
    # before the kind-aware no-module gate (parity with lookup_with_module's
    # qualified-name-first branch).
    for h in hits:
        if h.qualified_name == callee_name:
            return h
    from .io_boundary import gate_named_entry
    return gate_named_entry(
        hits, callee_name, module_hint, ambiguous_names,
        call_construct=call_construct,
    )


def _build_callee_index(
    entries: Sequence[TaintEntry],
) -> dict[str, list[TaintEntry]]:
    """Index source/sink entries by short name, qualified name, and bare
    method name (last dotted component), each mapping to the LIST of entries
    registered under that key.

    A list (not a single overwrite-on-collision value) is required so
    :func:`_lookup_named_entry` can disambiguate by module / ambiguity when
    several catalog entries share a short name (WI-razol).
    """
    idx: dict[str, list[TaintEntry]] = defaultdict(list)
    for entry in entries:
        idx[entry.name].append(entry)
        idx[entry.qualified_name].append(entry)
        if "." in entry.name:
            idx[entry.name.rsplit(".", 1)[-1]].append(entry)
    return idx


def _match_propagation_entry(
    index: Mapping[str, Sequence[TaintEntry]],
    edge_dst: str,
    ambiguous_names: frozenset[str],
    call_construct: str | None = None,
    *,
    is_resolved: bool = True,
):
    """Match an edge's callee against a propagation source/sink ``index``.

    A *resolved* (first-party) edge matches by exact callee name — the symbol
    is already disambiguated by resolution, and its symbol-ID "module" segment
    is a file path, not a dotted module to filter on (so module filtering would
    spuriously reject e.g. a ``cmd_sketch`` source whose declared module is
    ``hypergumbo_core.cli`` against the edge's ``cli.py`` path). An *unresolved*
    edge is the short-name-collision risk surface, so it goes through
    :func:`_lookup_named_entry`: a bare ambiguous callee with no module hint, or
    a module-mismatched hint, is not falsely matched (WI-razol), and an untyped
    *method* call (``call_construct``, threaded from the edge's ``meta``) never
    matches a method-kind sink/source (io-boundary:F3, INV-tapat/INV-maluk).

    ADR-0037 ruling 4: the resolution verdict is read from ``Edge.is_resolved``,
    NOT from the ``:unresolved`` dst-string suffix. That suffix is a producer
    convention that the WI-pubiv boundary-id remap rewrites to ``:external_symbol``
    on the final graph, so a string check would make every unresolved edge look
    "resolved" here and silently bypass the collision guard below.
    """
    callee_name = _extract_callee_name(edge_dst)
    hits = index.get(callee_name)
    if not hits:
        return None
    if is_resolved:
        # WI-damir. This used to be an ungated `return hits[0]`, justified as
        # "the symbol is already disambiguated by resolution". That premise is
        # FALSE, and it was the single largest source of non-realizable sinks
        # on fresh substrate. Resolution establishes WHICH IN-REPO SYMBOL is
        # called; it says nothing about whether that symbol IS the catalogued
        # primitive. The built-in catalogs describe stdlib and third-party
        # surfaces, so a first-party definition matching one BY NAME ALONE is a
        # category error — measured 30 of 30 false on the 9-repo cohort:
        # caddy's `func Log() *zap.Logger` (which RETURNS a logger and writes
        # nothing) reported as a logging sink 18 times, and d3's `function
        # log()` — the LOGARITHM, building d3.scaleLog — reported as
        # console.log. The latter is INV-karud's headline example and survived
        # WI-zazul because it was never a substring defect.
        #
        # The gate cannot be "resolved edges never match": a user-supplied
        # catalog may legitimately name a first-party symbol, which is what the
        # old comment was protecting. So compare the entry's declared module
        # against the symbol's own PATH, normalised to module shape. The
        # component-aware predicate's SUFFIX arm is what makes that work —
        # a catalog module of `hypergumbo_core.cli` matches a path of
        # `packages/…/hypergumbo_core/cli.py` because the trailing components
        # agree, while `log/slog` does not match `logging.go`.
        path_module = _module_from_symbol_path(edge_dst)
        if not path_module:
            # No path evidence to judge on (synthetic or external-shaped id) —
            # keep the legacy exact-name behaviour rather than silently
            # dropping the finding.
            return hits[0]
        from .io_boundary import _module_matches
        for h in hits:
            # An entry that declares no module carries no evidence to
            # contradict the match; legacy behaviour is preserved for it.
            if not getattr(h, "module", None) or _module_matches(
                h.module, path_module,
            ):
                return h
        return None
    return _lookup_named_entry(
        hits, callee_name, _extract_callee_module(edge_dst), ambiguous_names,
        call_construct=call_construct,
    )


@dataclass
class TaintCatalog:
    """Container for all taint sources, sinks, and sanitizers.

    Organizes entries by language for efficient lookup. Provides matching
    methods that check callee names against catalog entries.
    """

    _sources: dict[str, list[TaintSource]] = field(default_factory=dict)
    _sinks: dict[str, list[TaintSink]] = field(default_factory=dict)
    _sanitizers: dict[str, list[TaintSanitizer]] = field(default_factory=dict)

    # Per-language ambiguous short names, sourced from the io_primitives
    # ``ambiguous_names`` lists (WI-razol). These are short names that collide
    # with common non-IO methods (``str.replace``, ``dict.get``, ``sys.stdout``
    # vs ``socket`` ``write``...). match_source / match_sink return None for
    # them when no module hint disambiguates, mirroring
    # ``io_boundary.IoBoundaryCatalog.lookup_with_module`` so taint analysis
    # agrees with io-boundaries instead of blindly matching the first entry.
    _ambiguous_names: dict[str, frozenset[str]] = field(default_factory=dict)

    # Lookup indices built from entries
    _source_by_name: dict[str, dict[str, list[TaintSource]]] = field(
        default_factory=dict, repr=False,
    )
    _sink_by_name: dict[str, dict[str, list[TaintSink]]] = field(
        default_factory=dict, repr=False,
    )
    _sanitizer_by_name: dict[str, dict[str, list[TaintSanitizer]]] = field(
        default_factory=dict, repr=False,
    )

    def _rebuild_indices(self) -> None:
        """Build name-based lookup indices for all languages."""
        self._source_by_name.clear()
        self._sink_by_name.clear()
        self._sanitizer_by_name.clear()

        for lang, sources in self._sources.items():
            idx: dict[str, list[TaintSource]] = {}
            for src in sources:
                idx.setdefault(src.name, []).append(src)
                idx.setdefault(src.qualified_name, []).append(src)
            self._source_by_name[lang] = idx

        for lang, sinks in self._sinks.items():
            idx_s: dict[str, list[TaintSink]] = {}
            for sink in sinks:
                idx_s.setdefault(sink.name, []).append(sink)
                idx_s.setdefault(sink.qualified_name, []).append(sink)
            self._sink_by_name[lang] = idx_s

        for lang, sanitizers in self._sanitizers.items():
            idx_san: dict[str, list[TaintSanitizer]] = {}
            for san in sanitizers:
                idx_san.setdefault(san.qualified_name, []).append(san)
                idx_san.setdefault(san.short_name, []).append(san)
            self._sanitizer_by_name[lang] = idx_san

    def sources_for_language(self, language: str) -> list[TaintSource]:
        """Return all taint sources for a language."""
        return list(self._sources.get(language, []))

    def sinks_for_language(self, language: str) -> list[TaintSink]:
        """Return all taint sinks for a language."""
        return list(self._sinks.get(language, []))

    def sanitizers_for_language(self, language: str) -> list[TaintSanitizer]:
        """Return all taint sanitizers for a language."""
        return list(self._sanitizers.get(language, []))

    def ambiguous_names_for_language(self, language: str) -> frozenset[str]:
        """Return the ambiguous short names for a language (WI-razol).

        These collide with common non-IO methods (``str.replace``,
        ``dict.get``); propagation passes them to
        :func:`propagate_taint_structural` / :func:`propagate_taint_ddg` so a
        bare ambiguous callee with no module hint is not matched to a sink.
        """
        return self._ambiguous_names.get(language, frozenset())

    def match_source(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
        call_construct: str | None = None,
    ) -> Optional[TaintSource]:
        """Match a callee name against taint sources for a language.

        Honors the module qualifier and ``ambiguous_names`` via
        :func:`_lookup_named_entry` (WI-razol): a module hint that matches
        nothing yields ``None`` rather than the first source, and an ambiguous
        short name with no hint yields ``None`` rather than a false match.
        ``call_construct`` (io-boundary:F3) lets a bare untyped method call be
        rejected without the name needing to be in ``ambiguous_names``.
        """
        idx = self._source_by_name.get(language, {})
        return _lookup_named_entry(
            idx.get(callee_name), callee_name, module_hint,
            self._ambiguous_names.get(language, frozenset()),
            call_construct=call_construct,
        )

    def match_sink(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
        call_construct: str | None = None,
    ) -> Optional[TaintSink]:
        """Match a callee name against taint sinks for a language.

        Honors the module qualifier and ``ambiguous_names`` via
        :func:`_lookup_named_entry` (WI-razol): ``str.replace`` no longer
        matches ``Path.replace`` (the 5541-FP cascade) and ``sys.stdout.write``
        no longer matches ``asyncio.StreamWriter.write`` net_send (F156.A1).
        ``call_construct`` (io-boundary:F3) lets a bare untyped method call be
        rejected without the name needing to be in ``ambiguous_names``.
        """
        idx = self._sink_by_name.get(language, {})
        return _lookup_named_entry(
            idx.get(callee_name), callee_name, module_hint,
            self._ambiguous_names.get(language, frozenset()),
            call_construct=call_construct,
        )

    def match_sanitizer(
        self,
        language: str,
        callee_name: str,
        input_taint: str,
    ) -> Optional[TaintSanitizer]:
        """Match a callee name against sanitizers that handle the given taint label."""
        idx = self._sanitizer_by_name.get(language, {})
        hits = idx.get(callee_name)
        if not hits:
            return None
        for h in hits:
            if h.input_taint == input_taint:
                return h
        return None


# ---------------------------------------------------------------------------
# YAML catalog loading
# ---------------------------------------------------------------------------


class TaintCatalogError(Exception):
    """A project-local taint catalog file could not be parsed or has an
    invalid shape.

    This is the single umbrella for taint-catalog load failures —
    malformed YAML, a non-mapping document, a wrong-typed top-level section
    (``sources``/``sinks``/``transforms``), or an invalid ``start_at`` value.
    The CLI maps it to exit code 2 (inconclusive): a broken taint
    configuration means verification could not proceed, which must never be
    reported as a confirmed (exit 0) or violated (exit 1) verdict
    (INV-nufob / ADR-0033 Phase 3 silent-confirm closure). Before this, the
    loaders raised raw ``yaml.YAMLError`` / ``AttributeError`` / ``ValueError``
    that escaped the CLI as an uncaught traceback, or — worse, when no
    ``taint_flow`` claim was present — were never reached, so a bad
    ``--taint-*`` path silently fell through to "all CONFIRMED".
    """


def _safe_load_catalog_yaml(
    path: Path, section: str, section_type: type,
) -> dict[str, Any]:
    """Parse a taint-catalog YAML file with shape validation.

    Raises :class:`TaintCatalogError` (never a raw ``yaml.YAMLError`` or
    ``AttributeError``) on malformed YAML, a non-mapping document, or a
    top-level ``section`` whose value is not an instance of ``section_type``.
    Returns the parsed mapping (``{}`` for an empty file).
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaintCatalogError(
            f"could not parse taint catalog {path}: {exc}"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TaintCatalogError(
            f"taint catalog {path} must be a mapping at top level, got "
            f"{type(data).__name__}."
        )
    section_val = data.get(section)
    if section_val is not None and not isinstance(section_val, section_type):
        raise TaintCatalogError(
            f"taint catalog {path}: '{section}:' must be a "
            f"{section_type.__name__}, got {type(section_val).__name__}."
        )
    return data


def _load_source_yaml(path: Path) -> tuple[str, dict[str, list[TaintSource]]]:
    """Load a single taint source YAML file.

    Returns (taint_label, per-language dict of TaintSource entries). The stale
    ``list[TaintSource]`` annotation misdescribed the returned value — the body
    builds and returns ``sources_by_lang`` keyed by language, and every caller
    iterates it via ``.items()``.
    """
    data = _safe_load_catalog_yaml(path, "sources", dict)
    label = data.get("taint_label", "unknown")
    sources_by_lang: dict[str, list[TaintSource]] = {}

    for lang, entries in data.get("sources", {}).items():
        lang_sources: list[TaintSource] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")
            return_tainted = entry.get("return_tainted", True)
            arg_tainted = tuple(entry.get("argument_tainted", []))
            start_at = entry.get("start_at", "caller")
            if start_at not in {"caller", "callee"}:
                raise TaintCatalogError(
                    f"Invalid start_at={start_at!r} in {path}; "
                    f"must be 'caller' or 'callee'."
                )

            for func_name in entry.get("functions", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=func_name,
                    kind="function",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                    start_at=start_at,
                ))
            for method_name in entry.get("methods", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=method_name,
                    kind="method",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                    start_at=start_at,
                ))
        sources_by_lang[lang] = lang_sources

    return label, sources_by_lang


def _load_sink_yaml(path: Path) -> dict[str, list[TaintSink]]:
    """Load a single taint sink YAML file.

    Returns dict mapping language → list of TaintSink entries.
    """
    data = _safe_load_catalog_yaml(path, "sinks", dict)
    zone = data.get("zone", "unknown")
    trust_level = data.get("trust_level", "unknown")
    sinks_by_lang: dict[str, list[TaintSink]] = {}

    for lang, entries in data.get("sinks", {}).items():
        lang_sinks: list[TaintSink] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")

            for func_name in entry.get("functions", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=func_name,
                    kind="function",
                ))
            for method_name in entry.get("methods", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=method_name,
                    kind="method",
                ))
        sinks_by_lang[lang] = lang_sinks

    return sinks_by_lang


def _load_sanitizer_yaml(path: Path) -> dict[str, list[TaintSanitizer]]:
    """Load a single taint sanitizer YAML file.

    Returns dict mapping language → list of TaintSanitizer entries.
    """
    data = _safe_load_catalog_yaml(path, "transforms", list)
    sanitizers_by_lang: dict[str, list[TaintSanitizer]] = {}

    for transform in data.get("transforms", []):
        input_taint = transform.get("input_taint", "unknown")
        output_taint = transform.get("output_taint", "unknown")

        for lang, func_names in transform.get("functions", {}).items():
            lang_sans = sanitizers_by_lang.setdefault(lang, [])
            for func_name in func_names:
                lang_sans.append(TaintSanitizer(
                    input_taint=input_taint,
                    output_taint=output_taint,
                    qualified_name=func_name,
                ))

    return sanitizers_by_lang


def load_taint_catalog(
    source_paths: list[Path],
    sink_paths: list[Path],
    sanitizer_paths: list[Path],
) -> TaintCatalog:
    """Load taint catalogs from YAML files.

    Args:
        source_paths: Paths to taint source YAML files.
        sink_paths: Paths to taint sink YAML files.
        sanitizer_paths: Paths to taint sanitizer YAML files.

    Returns:
        A TaintCatalog with all entries indexed by language.
    """
    all_sources: dict[str, list[TaintSource]] = defaultdict(list)
    all_sinks: dict[str, list[TaintSink]] = defaultdict(list)
    all_sanitizers: dict[str, list[TaintSanitizer]] = defaultdict(list)

    for path in source_paths:
        _label, sources_by_lang = _load_source_yaml(path)
        for lang, sources in sources_by_lang.items():
            all_sources[lang].extend(sources)

    for path in sink_paths:
        sinks_by_lang = _load_sink_yaml(path)
        for lang, sinks in sinks_by_lang.items():
            all_sinks[lang].extend(sinks)

    for path in sanitizer_paths:
        sanitizers_by_lang = _load_sanitizer_yaml(path)
        for lang, sans in sanitizers_by_lang.items():
            all_sanitizers[lang].extend(sans)

    catalog = TaintCatalog(
        _sources=dict(all_sources),
        _sinks=dict(all_sinks),
        _sanitizers=dict(all_sanitizers),
    )
    catalog._rebuild_indices()
    return catalog


# ---------------------------------------------------------------------------
# Auto-import from io_primitives (WI-lokuv)
# ---------------------------------------------------------------------------
#
# ADR-0017 deliberately separates io_primitives (syscall-level IO boundary
# classification) from taint sources/sinks (trust-zone classification).  The
# rationale holds for project-local extension — every project has its own
# trust-zone structure — but the shipped *first-party* catalogs should not
# drift: every io_primitives write-side primitive is, by construction, a
# candidate sink for tainted data; every io_primitives read-side primitive
# for a sensitive category is a candidate source.
#
# Since 2026-04 (commit 51e1d232f3) the shipped sink catalog no longer
# exists as a separate ``taint_sinks/`` directory — sinks derive entirely
# from io_primitives via the mapping below.  Sources still ship as YAML in
# ``taint_sources/`` because their taint_label is project-meaningful
# (host_secret vs untrusted_input vs ...) and not derivable from the
# IO-boundary category alone.
#
# Auto-import is paranoid by design ("reading A" in the WI-lokuv discussion):
# each auto-derived sink is trust_level=untrusted and matches ANY taint
# label; each auto-derived source carries the label indicated below.  Users
# narrow the default by contributing overrides via ``--taint-sources`` or
# ``--taint-sinks`` whose entries match the auto-derived ``(module, name,
# kind)`` triple — the user entry wins, the auto entry is dropped.
#
# `fs_read` is intentionally absent from the source map: reading a file
# does not by itself make its contents sensitive; the label is project-
# specific (a config file vs. a credential vault vs. user-uploaded JSON).
# Projects that want every fs_read tainted can declare their own source
# catalog entries.
#
# This sink/source split IS hypergumbo's canonical I/O-boundary risk
# taxonomy: write-side/outbound boundaries are untrusted sinks (where
# tainted data lands or escapes), read-side sensitive boundaries are
# untrusted sources, and it is what ``verify-claims`` consumes. Do NOT
# confuse it with ``io_boundary.HIGH_RISK_PRIMITIVES`` — that is a narrow,
# display-only ``high_risk`` marker scoped to ``subprocess`` alone, not a
# competing risk axis. Destructive-filesystem and network-egress risk live
# HERE (and, for network, additionally at the chain ``dst_tier`` level),
# NOT in a second hand-curated high_risk set.
AUTO_SINK_ZONE_MAP: dict[str, tuple[str, str]] = {
    # io_primitives boundary -> (taint zone, trust_level)
    "fs_write": ("host_fs", "untrusted"),
    # WI-bibuk: subprocess gets its own zone rather than collapsing into
    # host_fs. Shelling out to a trusted external program (``pip``, ``git``,
    # ``rustup``, ``gitleaks``) is not the same trust surface as a direct
    # arbitrary-path filesystem write — the external program owns where
    # its bytes land. Claims that prohibit ``host_fs`` no longer fire on
    # legitimate ``subprocess.run`` calls; claims that need to prohibit
    # subprocess use ``prohibited_sink_zone: subprocess`` explicitly.
    "subprocess": ("subprocess", "untrusted"),
    "net_send": ("network", "untrusted"),
    "env_write": ("host_env", "untrusted"),
    "ipc_send": ("ipc", "untrusted"),
    "browser_storage_write": ("browser_storage", "untrusted"),
    # WI-gofaz: previously undocumented exclusions — now mapped.
    "db_write": ("database", "untrusted"),
    "process_send": ("ipc", "untrusted"),
    "logging": ("logging", "untrusted"),
}

AUTO_SOURCE_LABEL_MAP: dict[str, str] = {
    # io_primitives boundary -> taint_label for auto-derived source
    "env_read": "host_secret",
    "net_recv": "untrusted_input",
    "ipc_recv": "untrusted_input",
    "db_read": "untrusted_input",
}


def _derive_auto_imports_from_io_primitives(
    io_catalog_dir: Path,
) -> tuple[
    dict[str, list[TaintSource]],
    dict[str, list[TaintSink]],
    dict[str, frozenset[str]],
]:
    """Scan io_primitives/*.yaml and derive default taint sources + sinks.

    Returns ``(sources_by_lang, sinks_by_lang, ambiguous_by_lang)``.  Each
    IoPrimitive whose ``boundary`` matches :data:`AUTO_SOURCE_LABEL_MAP` yields
    a TaintSource; each whose ``boundary`` matches :data:`AUTO_SINK_ZONE_MAP`
    yields a TaintSink.  Language is taken from each YAML's ``language:``
    field.  Primitives declared under YAML ``attributes:`` produce
    ``kind="attribute"`` records — these pair with ``module_attr_ref``
    edges emitted by language analyzers (see WI-guhok, WI-gapam).

    ``ambiguous_by_lang`` carries each catalog's ``ambiguous_names`` so the
    taint matchers can disambiguate exactly as io-boundaries does (WI-razol).
    """
    from hypergumbo_core.io_boundary import IoBoundaryCatalog

    sources_by_lang: dict[str, list[TaintSource]] = defaultdict(list)
    sinks_by_lang: dict[str, list[TaintSink]] = defaultdict(list)
    ambiguous_by_lang: dict[str, frozenset[str]] = {}

    if not io_catalog_dir.is_dir():
        return dict(sources_by_lang), dict(sinks_by_lang), ambiguous_by_lang

    for yaml_path in sorted(io_catalog_dir.glob("*.yaml")):
        catalog = IoBoundaryCatalog.from_yaml(yaml_path)
        lang = catalog.language
        ambiguous_by_lang[lang] = (
            ambiguous_by_lang.get(lang, frozenset()) | catalog.ambiguous_names
        )
        for prim in catalog.primitives:
            if prim.boundary in AUTO_SOURCE_LABEL_MAP:
                sources_by_lang[lang].append(TaintSource(
                    taint_label=AUTO_SOURCE_LABEL_MAP[prim.boundary],
                    module=prim.module,
                    name=prim.name,
                    kind=prim.kind,
                    # The map above is many-to-one: net_recv, ipc_recv and
                    # db_read all become `untrusted_input`. Carry the
                    # boundary so the collapse is reversible downstream
                    # (WI-vazal) instead of information the label ate.
                    source_boundary=prim.boundary,
                ))
            if prim.boundary in AUTO_SINK_ZONE_MAP:
                zone, trust = AUTO_SINK_ZONE_MAP[prim.boundary]
                sinks_by_lang[lang].append(TaintSink(
                    zone=zone,
                    trust_level=trust,
                    module=prim.module,
                    name=prim.name,
                    kind=prim.kind,
                ))

    return dict(sources_by_lang), dict(sinks_by_lang), ambiguous_by_lang


def _merge_with_user_override(
    auto_by_lang: Mapping[str, Sequence[TEntry]],
    user_by_lang: Mapping[str, Sequence[TEntry]],
) -> dict[str, list[TEntry]]:
    """Merge auto-derived entries with user entries; user entries win on
    (module, name, kind) match.

    The result preserves every user entry and adds auto entries whose
    (module, name, kind) triple is not already declared by the user.
    Works for both TaintSource and TaintSink (both expose those fields).
    """
    merged: dict[str, list[TEntry]] = {}
    all_langs = set(auto_by_lang) | set(user_by_lang)
    for lang in all_langs:
        user_list = user_by_lang.get(lang, [])
        user_keys = {(e.module, e.name, e.kind) for e in user_list}
        auto_list = auto_by_lang.get(lang, [])
        filtered_auto = [
            e for e in auto_list
            if (e.module, e.name, e.kind) not in user_keys
        ]
        merged[lang] = filtered_auto + list(user_list)
    return merged


# ---------------------------------------------------------------------------
# Built-in catalog discovery
# ---------------------------------------------------------------------------

_TAINT_SOURCES_DIR = Path(__file__).parent / "taint_sources"
_TAINT_SANITIZERS_DIR = Path(__file__).parent / "taint_sanitizers"
_IO_PRIMITIVES_DIR = Path(__file__).parent / "io_primitives"
# Note: there is no ``_TAINT_SINKS_DIR``.  Commit 51e1d232f3 retired the
# shipped ``taint_sinks/`` directory and derives all built-in sinks from
# ``io_primitives/*.yaml`` via :func:`_derive_auto_imports_from_io_primitives`.
# Project-local sinks still flow in via the ``--taint-sinks`` CLI flag.


def _resolve_catalog_paths(paths: list[Path]) -> list[Path]:
    """Resolve project-local taint-catalog path arguments to a file list.

    Each input path is either a single YAML file (``*.yaml``/``*.yml``) or a
    directory — directories are globbed for ``*.yaml`` (sorted for
    deterministic merge order).  Raises :class:`FileNotFoundError` on any
    missing path so a typo in a CLI flag or claims-file entry does not
    silently fall through to the built-in defaults.
    """
    resolved: list[Path] = []
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"Taint catalog path not found: {p}")
        if p.is_dir():
            resolved.extend(sorted(p.glob("*.yaml")))
        else:
            resolved.append(p)
    return resolved


def load_full_taint_catalog(
    extra_source_paths: list[Path] | None = None,
    extra_sink_paths: list[Path] | None = None,
    extra_sanitizer_paths: list[Path] | None = None,
    *,
    cli_source_paths: list[Path] | None = None,
    cli_sink_paths: list[Path] | None = None,
    cli_sanitizer_paths: list[Path] | None = None,
) -> TaintCatalog:
    """Load built-in taint catalogs and merge in user-supplied YAML files.

    Path arguments accept YAML files or directories of YAMLs — resolved via
    :func:`_resolve_catalog_paths`.  Four layers stack, each overriding the
    ones below it on ``(module, name, kind)`` for sources/sinks (sanitizers
    key on ``qualified_name`` and so concatenate, never replace):

    1. Auto-derived taint entries from ``io_primitives/*.yaml`` (paranoid
       default: every write-side primitive is a sink, every read-side
       sensitive primitive is a source).
    2. Built-in YAML under ``taint_sources/`` and ``taint_sanitizers/``
       alongside this module.  (Built-in sinks come from layer 1 only;
       the ``taint_sinks/`` directory was retired in 51e1d232f3.)
    3. Claims-file extras — ``extra_*_paths`` (the ``extra_catalogs:`` key in
       the claims YAML, WI-votan).
    4. CLI extras — ``cli_*_paths`` (the ``--taint-sources`` /
       ``--taint-sinks`` / ``--taint-sanitizers`` flags).

    INV-hukug: layers 3 and 4 are kept distinct so a CLI flag (layer 4)
    *replaces* a claims-file entry (layer 3) on a matching
    ``(module, name, kind)`` instead of coexisting with it as a duplicate.
    Previously both were concatenated into one user layer with no intra-layer
    dedup, so a CLI ``--taint-sources`` override silently failed to displace a
    claims-file ``extra_catalogs.sources`` entry. Passing only ``extra_*_paths``
    (no ``cli_*``) preserves the prior single-user-layer behavior.

    The helper is the single entry point for end-users running
    ``verify-claims`` on a repo other than hypergumbo's own.
    """
    extra_source_paths = _resolve_catalog_paths(extra_source_paths or [])
    extra_sink_paths = _resolve_catalog_paths(extra_sink_paths or [])
    extra_sanitizer_paths = _resolve_catalog_paths(extra_sanitizer_paths or [])
    cli_source_paths = _resolve_catalog_paths(cli_source_paths or [])
    cli_sink_paths = _resolve_catalog_paths(cli_sink_paths or [])
    cli_sanitizer_paths = _resolve_catalog_paths(cli_sanitizer_paths or [])

    catalog = load_builtin_taint_catalog()

    any_extra = extra_source_paths or extra_sink_paths or extra_sanitizer_paths
    any_cli = cli_source_paths or cli_sink_paths or cli_sanitizer_paths
    if not (any_extra or any_cli):
        return catalog

    # Two user layers: claims-file extras (lower) and CLI extras (higher).
    # CLI overrides claims on (module, name, kind) for sources/sinks
    # (INV-hukug); sanitizers concatenate (claims then CLI). The unified user
    # layer then overrides the built-in catalog (layers 1+2).
    claims_layer = load_taint_catalog(
        extra_source_paths, extra_sink_paths, extra_sanitizer_paths,
    )
    cli_layer = load_taint_catalog(
        cli_source_paths, cli_sink_paths, cli_sanitizer_paths,
    )
    user_sources = _merge_with_user_override(
        claims_layer._sources, cli_layer._sources,
    )
    user_sinks = _merge_with_user_override(
        claims_layer._sinks, cli_layer._sinks,
    )
    user_sanitizers: dict[str, list[TaintSanitizer]] = {}
    for layer in (claims_layer._sanitizers, cli_layer._sanitizers):
        for lang, sans in layer.items():
            user_sanitizers.setdefault(lang, []).extend(sans)

    catalog._sources = _merge_with_user_override(catalog._sources, user_sources)
    catalog._sinks = _merge_with_user_override(catalog._sinks, user_sinks)
    for lang, sans in user_sanitizers.items():
        catalog._sanitizers.setdefault(lang, []).extend(sans)
    catalog._rebuild_indices()
    return catalog


def load_builtin_taint_catalog() -> TaintCatalog:
    """Load built-in taint catalogs shipped with hypergumbo.

    Two contributions merge into one catalog:

    1. YAML-declared entries in ``taint_sources/`` and ``taint_sanitizers/``.
       These cover project-agnostic domains the core team maintains
       explicitly (crypto decryption labels, key material generation,
       sanitizer pairings, ...) and provide the project-local extension
       point described in ADR-0017.
    2. Auto-derived entries from ``io_primitives/*.yaml`` (WI-lokuv).
       Every write-side IO primitive becomes a TaintSink at
       trust_level=untrusted with a zone determined by its boundary
       category; every read-side sensitive-category primitive becomes a
       TaintSource with a default taint_label.  User YAML entries that
       match (module, name, kind) override the auto-derived defaults.

    The merge makes io_primitives the single source of truth for primitive
    enumeration: adding a primitive there propagates into taint analysis
    automatically, which replaces the manual drift-guard previously shipped
    under WI-hizik.  Built-in sinks come entirely from layer 2 — the
    shipped ``taint_sinks/`` directory was retired in 51e1d232f3.
    """
    source_paths = sorted(_TAINT_SOURCES_DIR.glob("*.yaml")) if _TAINT_SOURCES_DIR.exists() else []
    sanitizer_paths = sorted(_TAINT_SANITIZERS_DIR.glob("*.yaml")) if _TAINT_SANITIZERS_DIR.exists() else []
    # No built-in sinks: 51e1d232f3 retired the shipped ``taint_sinks/``
    # directory and derives them from ``io_primitives/`` instead.
    user_catalog = load_taint_catalog(source_paths, [], sanitizer_paths)

    auto_sources, auto_sinks, ambiguous_by_lang = (
        _derive_auto_imports_from_io_primitives(_IO_PRIMITIVES_DIR)
    )
    user_catalog._sources = _merge_with_user_override(
        auto_sources, user_catalog._sources,
    )
    user_catalog._sinks = _merge_with_user_override(
        auto_sinks, user_catalog._sinks,
    )
    # WI-razol: carry the io_primitives ambiguous_names onto the catalog so
    # match_source / match_sink disambiguate exactly as io-boundaries does.
    user_catalog._ambiguous_names = ambiguous_by_lang
    user_catalog._rebuild_indices()
    return user_catalog


# ---------------------------------------------------------------------------
# Structural taint-flow propagation (Phase 1 fallback)
# ---------------------------------------------------------------------------


def _extract_callee_name(symbol_id: str) -> str:
    """Extract the callee function name from a symbol ID.

    Symbol ID format: {lang}:{file_or_module}:{start}-{end}:{name}:{kind}
    For unresolved externals: {lang}:external:0-0:{name}:unresolved

    Handles names containing colons (ObjC selectors) by parsing from
    both ends: language is before the first colon, kind is after the last.
    """
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return symbol_id
    # For names with colons (ObjC selectors), reconstruct from middle parts
    # Format: lang:file:line-range:name:kind
    # Parse from both ends
    # Find the line range (contains a dash)
    line_range_idx = -1
    for i in range(1, len(parts) - 1):
        if "-" in parts[i] and parts[i].replace("-", "").isdigit():
            line_range_idx = i
            break
    if line_range_idx < 0:
        return parts[-2] if len(parts) >= 2 else symbol_id

    # Name is everything between line_range and kind
    name_parts = parts[line_range_idx + 1: -1]
    return ":".join(name_parts)


def _qualified_callee(symbol_id: str) -> str:
    """``{module}.{name}`` for a callee id, or "" when either slot is missing.

    The key ADR-0017 §4 summaries are declared under: ``fmt.Printf``,
    ``net/http.Get``, ``os.getenv``. Built from the two existing production
    extractors rather than a fresh parse of the id grammar — a third naive
    split of a colon-tolerant format is exactly what ``_symbol_path_slot``'s
    header warns about.

    Returns "" for an id with no module evidence (``python:external:0-0:print:
    unresolved`` yields module ``external``, which no catalogue declares), so
    such a callee stays uncatalogued and therefore unknown. That is the safe
    direction: an unknown callee keeps a branch open.
    """
    module = _extract_callee_module(symbol_id)
    name = _extract_callee_name(symbol_id)
    if not module or not name or name == symbol_id:
        return ""
    return f"{module}.{name}"


def _extract_callee_language(symbol_id: str) -> str:
    """Extract the language prefix from a symbol ID.

    Symbol id format: ``{lang}:{path}:{line-range}:{name}:{kind}``.
    Returns the language token. Used by sink/source matching to filter
    cross-language pollution — without this filter, a sink declared in
    elixir (``HTTPoison.get``) collides with every Python ``.get()`` call
    via short-name indexing, producing thousands of false positives.
    """
    parts = symbol_id.split(":", 1)
    return parts[0] if parts else ""


def _extract_callee_module(symbol_id: str) -> str:
    """Extract the callee module/path hint from a symbol ID.

    Mirrors :func:`_extract_callee_name`'s parsing but returns the file
    or module segment instead of the name. For unresolved externals
    this is typically ``"external"`` (entirely ambiguous) or a module
    path like ``"os.environ"`` / ``"subprocess"`` when the analyzer
    pinned it down. For in-repo dsts it's the relative file path.

    Used by sink-matching to filter short-name collisions: a sink
    declared as ``multiprocessing.Queue.get`` should NOT match an edge
    whose dst is ``python:external:0-0:get:unresolved`` because the
    edge could equally be ``dict.get``, ``args.get``, etc.
    """
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[1] if len(parts) > 1 else ""


# Source-file extensions stripped when reading a symbol id's PATH segment as a
# module (WI-damir). Named explicitly rather than inferred by length: `net.ws`
# is a real module whose trailing component is two characters, and a heuristic
# that treats short tails as extensions silently rewrites it to `net`.
_SOURCE_FILE_EXTENSIONS = frozenset({
    "py", "pyi", "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs", "rb",
    "java", "kt", "kts", "swift", "scala", "sc", "php", "cs", "c", "h", "cc",
    "cpp", "hpp", "cxx", "m", "mm", "ex", "exs", "erl", "hrl", "sh", "bash",
    "zsh", "pl", "pm", "lua", "dart", "sol", "vue", "svelte",
})


def _module_from_symbol_path(symbol_id: str) -> str:
    """Normalise an in-repo symbol id's PATH segment to a module-shaped string.

    An in-repo symbol id carries a file path where an external one carries a
    module (``go:logging.go:779-783:Log:function`` vs
    ``go:os:0-0:Remove:external_symbol``). To judge a resolved symbol against a
    catalog entry's declared module (WI-damir) the two have to be comparable,
    so the trailing file extension is dropped and the rest is handed to
    :func:`io_boundary._module_matches`, which normalises ``/`` to ``.`` and
    compares whole components.

    Returns ``""`` when there is no usable path evidence — an ``external``
    placeholder or a malformed id — so the caller can fall back rather than
    reject on the strength of nothing.

    Examples::

        packages/hypergumbo-core/src/hypergumbo_core/cli.py
            -> packages/hypergumbo-core/src/hypergumbo_core/cli   (suffix-matches
                                                                   hypergumbo_core.cli)
        logging.go                    -> logging     (does NOT match log/slog)
        src/pretix/static/d3/d3.v6.js -> src/pretix/static/d3/d3.v6
                                                     (does NOT match console)
    """
    raw = _extract_callee_module(symbol_id)
    if not raw or raw in _UNRESOLVED_MODULE_PLACEHOLDERS:
        return ""
    head, sep, tail = raw.rpartition(".")
    # Strip a trailing SOURCE-FILE EXTENSION, matched against an explicit list.
    #
    # The first draft used a length heuristic — "a short alphanumeric segment
    # after a dot is an extension" — and an existing test refuted it
    # immediately: the module `net.ws` had its real trailing component `ws`
    # stripped to `net`, which then failed to match a catalog entry declared as
    # `net.ws`. A short component is not evidence of an extension, and no
    # length threshold can separate the two; the set has to be named.
    if sep and head and tail.lower() in _SOURCE_FILE_EXTENSIONS:
        raw = head
    return raw


# Edge types that represent call-like relationships for taint propagation.
# Includes direct calls and cross-language linker bridge edges (ADR-0017 §5).
#
# ADR-0023 §6 Phase 2 audit (WI-sahab-fatoz): mixes relationship-axis
# (``calls``, ``module_attr_ref``), pending_classification
# (``implements_rpc``), and endpoint_shape bridge values. Forward-
# compatible through Phase 3 because ``calls`` is already a member, so
# bridges folding into ``calls`` + ``meta["bridge_kind"]`` continue to
# match; bridge entries become dead-but-harmless and get pruned in
# Phase 4.
TAINT_CALL_EDGE_TYPES = frozenset({
    "calls",
    # WI-lokuv: attribute-read edges for IO primitives declared under
    # ``attributes:`` in io_primitives YAML (os.environ, sys.argv, ...).
    # Emitted by the Python analyzer per WI-guhok; extending to the
    # tree-sitter analyzer base class is tracked as WI-gapam.  Without
    # this edge type, auto-imported TaintSource records for attribute
    # kind primitives would never match in structural propagation.
    "module_attr_ref",
    # Bridge edges no longer enumerated explicitly: post-Phase-3
    # (WI-mifor-vabul), every bridge folds to 'calls' which is already a
    # member; meta['bridge_kind'] carries the bridge type. Protocol-call
    # family (WI-vumum-juvil) similarly folds into 'calls' + meta['protocol'],
    # so HTTP/gRPC/GraphQL call taint propagation transfers automatically.
    # implements_rpc folded to 'implements' + meta['protocol']='grpc'
    # (audit-findings 0016) — NOT a plain set member (that would wholesale-
    # include every structural 'implements' edge); matched by the
    # is_grpc_rpc_implementation predicate via _is_taint_call_edge below.
})


def _is_taint_call_edge(edge: dict[str, Any]) -> bool:
    """True if *edge* (a behavior-map edge dict) carries taint like a call.

    Membership in :data:`TAINT_CALL_EDGE_TYPES`, OR the folded gRPC
    RPC-implementation edge (``implements`` + ``meta['protocol']='grpc'``,
    audit-findings 0016) — the one place taint recognizes the folded form,
    so gRPC taint propagation is preserved without demoting or over-
    including structural ``implements`` edges.
    """
    etype = edge.get("type", "")
    return etype in TAINT_CALL_EDGE_TYPES or is_grpc_rpc_implementation(
        etype, edge.get("meta")
    )


def _build_adjacency(
    edges: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build forward and reverse adjacency lists from edge dicts.

    Includes call-type edges and cross-language linker bridge edges
    (ADR-0017 §5). Bridge edges are taint-transparent by default —
    IPC serialization does not sanitize taint.
    Returns (forward_adj, reverse_adj).
    """
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)

    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        src = edge["src"]
        dst = edge["dst"]
        forward[src].add(dst)
        reverse[dst].add(src)

    return dict(forward), dict(reverse)


def _build_sanitizer_index_multi(
    sanitizers: list[TaintSanitizer],
) -> dict[str, list[TaintSanitizer]]:
    """Index sanitizers by callee name as a list, not a single entry.

    One function may be declared as a sanitizer for several distinct
    input_taint labels — the zone-barrier pattern in hypergumbo's
    self-audit uses one barrier function per wrapper to sanitize every
    entry-point label so a single call blocks BFS regardless of which
    entry-point seeded the trace. Indexing as a flat dict would
    overwrite all but one entry; the list-indexed version preserves
    every (qualified_name → input_taint) declaration so the BFS
    consumer can register sanitization for every applicable label.
    """
    index: dict[str, list[TaintSanitizer]] = defaultdict(list)
    for san in sanitizers:
        index[san.qualified_name].append(san)
        index[san.short_name].append(san)
        # Bare-method-name fallback (parity with the source/sink indexers)
        # so unresolved edges that only have the leaf name still match.
        if "." in san.qualified_name:
            leaf = san.qualified_name.rsplit(".", 1)[-1]
            if leaf != san.short_name:
                index[leaf].append(san)
    return index


def _iter_sink_sites(
    sink_callers: "dict[str, list[tuple[str, TaintSink]]]",
) -> "Iterator[tuple[str, str, TaintSink]]":
    """Yield ``(caller, sink_callee_id, sink)`` for every sink call site.

    ``sink_callers`` maps a CALLER to every sink it calls. It used to hold a
    single tuple per caller and was populated by assignment, so a function
    calling two different sinks reported only the last edge encountered —
    a silent under-report present in both the structural and the DDG pass.
    Shared by both so the two cannot drift again.
    """
    for caller, entries in sink_callers.items():
        for sink_callee_id, taint_sink in entries:
            yield caller, sink_callee_id, taint_sink


def _register_sanitizer_callers(
    edges: list[dict[str, Any]],
    sanitizer_by_callee: dict[str, list[TaintSanitizer]],
    sanitizer_callers: "dict[str, dict[str, TaintSanitizer]]",
    ambiguous_names: frozenset[str] = frozenset(),
) -> None:
    """Populate sanitizer_callers from edges + multi-sanitizer index.

    Each edge whose callee matches one or more sanitizers adds an entry
    per matched sanitizer's input_taint label to the caller's sanitizer
    dict — so a caller of a multi-label barrier picks up every label.

    INV-finoh: sanitizer matching applies the same resolution-/kind-aware
    gate that source/sink matching does (``_match_propagation_entry`` /
    ``gate_named_entry``), so a phantom barrier is never registered from a
    bare-name collision — which would silently SUPPRESS a real taint flow (a
    false negative, worse than a missed barrier for a security tool). A
    *resolved* edge trusts its resolution (exact-name match, unchanged). An
    *unresolved* edge is the short-name-collision surface: a qualified callee
    carries its own receiver evidence (an exact ``qualified_name`` match wins,
    parity with ``_lookup_named_entry``'s qualified-first branch), but a bare
    untyped *method* call (``call_construct == "method"``, threaded from the
    edge meta) has no receiver evidence and must NOT match — ``x.encrypt()``
    must not bind ``Fernet.encrypt`` and falsely sanitize a flow (the
    INV-tapat/INV-maluk rule ``gate_named_entry`` enforces). An
    ``ambiguous_names`` bare short name is the meta-absent safety net. (The
    ``kind``-filter for a free-function call matching a method-kind sanitizer
    is not applied here because the sanitizer catalog carries no explicit
    ``kind`` — a documented follow-up requiring a sanitizer-YAML schema field.)
    """
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched_list = sanitizer_by_callee.get(callee_name)
        if not matched_list:
            continue
        if not edge.get("is_resolved", True):
            qualified = any(
                s.qualified_name == callee_name for s in matched_list
            )
            if not qualified:
                call_construct = edge.get("meta", {}).get("call_construct")
                if call_construct == "method":
                    continue
                if ambiguous_names and callee_name in ambiguous_names:
                    continue
        for matched in matched_list:
            sanitizer_callers[edge["src"]][matched.input_taint] = matched


def propagate_taint_structural(
    edges: list[dict[str, Any]],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
    ambiguous_names: frozenset[str] = frozenset(),
) -> list[TaintFlowFinding]:
    """Structural taint-flow propagation via call-graph BFS.

    Two-phase BFS per ADR-0017 §3b:
    1. For each taint source, compute the set of nodes reachable from the
       source's caller without passing through any sanitizer for that
       taint label.
    2. Check if any taint sink is in the reachable set.

    This is an overapproximation: it cannot distinguish between different
    variables in the same function. Findings are labeled as approximate.

    Args:
        edges: List of edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.
        ambiguous_names: Short names the catalog flags as ambiguous (e.g.
            ``replace`` / ``write`` / ``get``); a bare ambiguous callee with
            no usable module hint is not matched to a source/sink (WI-razol).

    Returns:
        List of TaintFlowFinding for each source→sink violation.
    """
    if not edges or not sources or not sinks:
        return []

    forward_adj, reverse_adj = _build_adjacency(edges)

    # Index: callee name → source/sink/sanitizer (list per name).
    # Index by qualified name, catalog name, AND short method name (last
    # component after dots) to match unresolved edges that only have the
    # bare method name (e.g., "decrypt" instead of "Fernet.decrypt").
    source_by_callee = _build_callee_index(sources)
    sink_by_callee = _build_callee_index(sinks)
    sanitizer_by_callee = _build_sanitizer_index_multi(sanitizers)

    # Step 1: Find source call sites — which symbol IDs call taint sources?
    # A "source caller" is a node that has an outgoing call edge to a source.
    # _lookup_named_entry honors the edge's module hint and ambiguous_names so
    # a bare ambiguous callee (str.replace, dict.get) is not falsely matched
    # (WI-razol).
    source_callers: list[tuple[str, str, TaintSource]] = []
    # (caller_symbol_id, source_callee_symbol_id, TaintSource)
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            source_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
        )
        if matched:
            source_callers.append((edge["src"], edge["dst"], matched))

    # Step 2: Find sink call sites — which symbol IDs call taint sinks?
    sink_callers: dict[str, list[tuple[str, TaintSink]]] = defaultdict(list)
    # Maps caller_symbol_id → (sink_callee_symbol_id, TaintSink)
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            sink_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
        )
        if matched:
            site = (edge["dst"], matched)
            if site not in sink_callers[edge["src"]]:
                sink_callers[edge["src"]].append(site)

    # Step 3: Find sanitizer call sites — multi-label-aware so one
    # caller of a barrier function picks up every input_taint label it
    # sanitizes.
    sanitizer_callers: dict[str, dict[str, TaintSanitizer]] = defaultdict(dict)
    _register_sanitizer_callers(
        edges, sanitizer_by_callee, sanitizer_callers, ambiguous_names,
    )

    # Step 4: For each source, BFS forward to find reachable sinks
    # without passing through sanitizers.
    findings: list[TaintFlowFinding] = []

    for caller_id, source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label

        # Choose BFS seed by source's start_at field. "caller" (default)
        # preserves legacy semantics: BFS from the call site of the source
        # function. "callee" seeds at the source callee itself — used by
        # synthetic entry-point sources (CLI handlers declared in
        # project-local catalogs) so the reachable set is exactly the
        # downstream of that one entry point, not everything reachable
        # from the dispatcher.
        seed_id = (
            source_callee_id
            if taint_source.start_at == "callee"
            else caller_id
        )

        # Phase 1: forward reachability, split by whether a sanitizer
        # intervened. See _reachability_past_sanitizers for why the sanitized
        # side is retained rather than pruned into silence.
        (
            reachable, parent, sanitized_reachable, sanitized_parent,
        ) = _reachability_past_sanitizers(
            seed_id, taint_label, forward_adj, sanitizer_callers,
        )

        # Phase 2: Check if any sink caller or sink callee is reachable
        for sink_node, sink_callee_id, taint_sink in _iter_sink_sites(
            sink_callers,
        ):
            if sink_node in reachable:
                is_sanitized = False
                path = _reconstruct_path(parent, seed_id, sink_node)
            elif sink_node in sanitized_reachable:
                is_sanitized = True
                path = _reconstruct_path(
                    {**parent, **sanitized_parent}, seed_id, sink_node,
                )
            else:
                continue
            findings.append(TaintFlowFinding(
                taint_label=taint_label,
                source_symbol=seed_id,
                source_primitive=taint_source.name,
                source_module=taint_source.module,
                source_boundary=taint_source.source_boundary,
                sink_symbol=sink_callee_id,
                sink_primitive=taint_sink.name,
                sink_module=taint_sink.module,
                sink_zone=taint_sink.zone,
                sanitized=is_sanitized,
                confidence="approximate",
                analysis_method="structural",
                path=path,
            ))

    return findings


def _reachability_past_sanitizers(
    seed_id: str,
    taint_label: str,
    forward_adj: dict[str, set[str]],
    sanitizer_callers: dict[str, dict[str, "TaintSanitizer"]],
) -> tuple[set[str], dict[str, str | None], set[str], dict[str, str | None]]:
    """Forward reachability, split by whether a sanitizer intervened.

    Returns ``(reachable, parent, sanitized_reachable, sanitized_parent)``.

    WHY BOTH SETS. The barrier used to prune the subtree beyond a sanitizer,
    which meant a protected flow produced exactly the output of a flow that
    does not exist: nothing. A reader could not distinguish "no path from this
    source to this sink" from "a path exists and your ``encrypt()`` call is
    what makes it safe" — and the second is what they need before deleting
    that call. ``TaintFlowFinding.sanitized`` existed for this and was written
    ``False`` at both and only construction sites, so ``verify_claims``'
    ``and not f.sanitized`` was a tautology and ``confirmed_safe`` was
    unreachable in production (owner ruling 2026-08-03: emit it labelled).

    UNSANITIZED WINS. ``sanitized_reachable`` excludes everything in
    ``reachable``, so a sink the taint reaches by *any* unprotected route is
    reported unsanitized even when another route encrypts. Labelling that
    ``sanitized`` would be the dangerous direction of wrong.

    ONE IMPLEMENTATION, TWO CALLERS. The structural and DDG propagators each
    carried a copy of this walk and had already drifted (the structural one
    tracked ``sanitized_nodes``, the DDG one dropped them on the floor). A
    barrier that means one thing in one pass and another in the other is the
    shape that produces "fixed in Python, still broken in Go" reports.

    The seed is exempt from the barrier in both passes: it is the taint origin
    and must be reachable whether ``start_at`` puts it at the caller or callee.
    """
    reachable: set[str] = set()
    barrier_nodes: list[str] = []
    parent: dict[str, str | None] = {seed_id: None}
    queue: deque[str] = deque([seed_id])

    while queue:
        node = queue.popleft()
        if node in reachable:
            continue  # pragma: no cover
        node_sanitizers = sanitizer_callers.get(node, {})
        if taint_label in node_sanitizers and node != seed_id:
            barrier_nodes.append(node)
            continue
        reachable.add(node)
        for neighbor in sorted(forward_adj.get(node, set())):
            if neighbor not in reachable and neighbor not in parent:
                parent[neighbor] = node
                queue.append(neighbor)

    # Second pass: what the taint reaches only AFTER being transformed. Seeded
    # at the barrier nodes themselves, since the sanitizer call site is where
    # the protected value comes into existence. Further sanitizers are not
    # barriers here — re-encrypting already-ciphertext changes nothing about
    # the fact that this route is protected.
    sanitized_reachable: set[str] = set()
    sanitized_parent: dict[str, str | None] = {}
    queue = deque()
    for node in barrier_nodes:
        if node in reachable:
            continue  # pragma: no cover
        sanitized_parent.setdefault(node, parent.get(node))
        queue.append(node)

    while queue:
        node = queue.popleft()
        if node in sanitized_reachable or node in reachable:
            # Defensive. Both conditions are already enforced at every enqueue
            # site — barrier seeds skip `reachable`, and neighbours are
            # enqueued only when absent from `reachable`, `sanitized_reachable`
            # AND `sanitized_parent` — so nothing can be queued twice today.
            # Kept because the alternative to a redundant guard here is an
            # infinite loop if a future edit relaxes one of those enqueue
            # conditions.
            continue  # pragma: no cover
        sanitized_reachable.add(node)
        for neighbor in sorted(forward_adj.get(node, set())):
            if neighbor in reachable or neighbor in sanitized_reachable:
                continue
            if neighbor not in sanitized_parent:
                sanitized_parent[neighbor] = node
                queue.append(neighbor)

    return reachable, parent, sanitized_reachable, sanitized_parent


def _reconstruct_path(
    parent: dict[str, str | None],
    start: str,
    end: str,
) -> list[str]:
    """Reconstruct a path from start to end using parent pointers."""
    path = [end]
    current = end
    while current != start and current in parent and parent[current] is not None:
        current = parent[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Field-sensitivity lite (ADR-0017 §7a)
# ---------------------------------------------------------------------------


def is_field_tainted(variable: str, tainted_vars: set[str]) -> bool:
    """Check if a variable name inherits taint from a tainted base.

    Field-sensitivity lite rules (ADR-0017 §7a):
    - If ``x`` is tainted, then ``x.field``, ``x.method``, ``x[key]`` are tainted.
    - If ``obj.field`` is tainted, only ``obj.field`` is tainted (not ``obj``).
    - Direct match: ``x`` in tainted_vars → True.
    - Field access: ``x.anything`` where ``x`` is in tainted_vars → True.

    Args:
        variable: Variable name to check (may contain dots for field access).
        tainted_vars: Set of currently tainted variable names.

    Returns:
        True if the variable is tainted (directly or via field access on
        a tainted base).
    """
    if variable in tainted_vars:
        return True

    # Check if this is a field access on a tainted base: x.field where x is tainted
    if "." in variable:
        base = variable.split(".")[0]
        if base in tainted_vars:
            return True

    return False


# ---------------------------------------------------------------------------
# DDG-backed taint propagation (ADR-0017 §3a, §3c-3d)
# ---------------------------------------------------------------------------


def _ddg_taint_reaches(
    symbol_id: str,
    source_lines: list[int],
    sink_lines: list[int],
    ddg_uses: dict[tuple[str, int], set[int]],
    callees_at: dict[tuple[str, int], frozenset[str]] | None = None,
    summaries: dict[str, "FunctionSummary"] | None = None,
) -> bool | None:
    """Does a value defined at a source call reach a use at a sink call?

    THREE-VALUED, and that is the load-bearing part. ``True`` means a data
    dependence was found; ``False`` means the walk ran to completion without
    finding one AND accounted for the tainted value at every step; ``None``
    means the value escaped somewhere the DDG cannot follow, so nothing is
    known either way.

    Collapsing ``None`` into ``False`` — the obvious implementation — silently
    converts ADR-0017 §7b's exclusion of alias analysis into false negatives.
    Measured on pretix: ``vouchers_send`` does
    ``voucher_list.append(vouchers.pop(0))`` and later
    ``bulk_update(voucher_list, ...)``; the taint leaves the tracked definition
    chain the moment it enters the list, and a two-valued walk "proves" the
    flow absent. Three of nine verified removals in the first cohort arm were
    this shape.

    WHY NOTHING IS REMOVED TODAY, EVEN ON ``False``. Distinguishing a use that
    *terminates* the taint (``fmt.Printf(cwd)`` — argument consumed, result
    discarded) from one that *propagates* it (``lst.append(x)`` — argument
    escapes into the receiver) requires knowing whether the callee mutates its
    arguments. That is precisely ADR-0017 §4 function summaries, and §3a's own
    step 3 says so: "At call sites, apply function summaries (§4)." §4a
    (``infer_summary``) and §4b (``load_function_summaries``) have zero
    production callers, so the information does not exist at runtime and every
    ``False`` here is really an unverified ``None``. The walk therefore
    CONFIRMS and never refutes: it earns the ``precise`` label where it finds
    a dependence and leaves inclusion to call-graph reachability everywhere
    else. Removal becomes sound once §4 runs — that is the fifth prerequisite,
    absent from WI-kabif's filed list of four.

    The ADR-0017 §3a forward walk, over one function's reaching-definition
    edges. Seeds at the lines where the taint source is called — the value it
    returns is defined there — and follows def→use edges transitively: if a
    tainted value is used at line U, then whatever is *defined* at U inherits
    the taint, so U becomes a new seed.

    Returns True as soon as a tainted value is used at a line where the sink is
    called, which is the ADR's step 5 read correctly. The ADR words it "if
    tainted data reaches a sink", and implemented as "a tainted definition
    reaches the sink's basic block" it removes almost nothing: the definition
    and the sink call routinely share a block, so the test passes for a
    function that calls a source and a sink on unrelated data. The load-bearing
    reading is that a tainted variable is an ARGUMENT AT THE SINK CALL SITE,
    and a use recorded at the sink's line is exactly that.

    Intraprocedural by construction — every edge belongs to one function, so
    this can only adjudicate a flow whose source and sink share a function.
    Callers must fall back to call-graph reachability for everything else,
    which is not a shortcut but ADR-0017 §7b's exclusion of alias and
    whole-program analysis.

    Args:
        symbol_id: The function whose DDG is being walked.
        source_lines: Lines where the taint source is called.
        sink_lines: Lines where the sink is called.
        ddg_uses: ``(symbol_id, def_line) -> {use_line, ...}``.

    Returns:
        True if the taint reaches a sink call site.
    """
    targets = set(sink_lines)
    frontier = list(source_lines)
    seen: set[int] = set()
    escaped = False
    while frontier:
        line = frontier.pop()
        if line in seen:
            continue
        seen.add(line)
        uses = ddg_uses.get((symbol_id, line))
        if not uses:
            continue
        if uses & targets:
            return True
        for use_line in uses:
            if (symbol_id, use_line) in ddg_uses:
                # Something is defined here and we can follow it: the taint
                # continues along a chain we still understand.
                if use_line not in seen:
                    frontier.append(use_line)
            else:
                # The tainted value is consumed at a line that defines nothing
                # the DDG tracked — it went into a container, a call argument,
                # a field, or a closure. ADR-0017 §7b excludes alias analysis,
                # so we cannot say where it went next. That is unknown, not
                # absent — UNLESS §4 can tell us the callee consumed it.
                if _use_site_terminates(
                    symbol_id, use_line, callees_at, summaries,
                ):
                    continue
                escaped = True
    if escaped:
        return None
    return False


def _summary_terminates(summary: "FunctionSummary") -> bool:
    """Does this callee CONSUME its arguments without passing them anywhere?

    True only for a side-effecting function that returns nothing derived from
    its arguments, mutates no receiver, invokes no callback and transforms no
    taint label — ``fmt.Printf``, ``log.Println``, ``os.Exit``. Every other
    shape leaves the value somewhere the intraprocedural walk cannot follow.

    Deliberately conservative in the SAFE direction. A false "terminates" lets
    the walk close a branch that is really open, which (once refutation acts on
    ``False``) deletes a real security finding; a false "does not terminate"
    only leaves an unknown unknown. Those costs are not symmetric, so every
    clause here is a conjunction and any doubt reads as "no".
    """
    return bool(
        summary.side_effect
        and not summary.param_to_return
        and not summary.param_to_self
        and not summary.mutates_self
        and summary.callback is None
        and not summary.sanitizes
    )


def _use_site_terminates(
    symbol_id: str,
    use_line: int,
    callees_at: dict[tuple[str, int], frozenset[str]] | None,
    summaries: dict[str, "FunctionSummary"] | None,
) -> bool:
    """Does EVERY call at this line consume the tainted value and stop?

    ADR-0017 §3a step 3 — "at call sites, apply function summaries (§4)" — is
    exactly this. Three properties, each load-bearing:

    **Uncatalogued means unknown, not "assume it propagates".**
    ``function_summaries.get_default_summary`` returns
    ``param_to_return = {0..9: True}``, and using it here would make an EMPTY
    catalogue change behaviour: every callee would read as passing the taint
    on. Returning False for an unknown callee instead means a catalogue
    covering nothing reproduces the pre-§4 output exactly, so every behaviour
    change is attributable to an entry somebody deliberately wrote.

    **All-or-nothing.** Several calls can share a line (``log(transform(x))``)
    and the value may have gone into any of them. Closing the branch because
    one of them terminates would be a guess dressed as an analysis.

    **Qualified names only.** ``load_function_summaries`` also indexes every
    entry under its bare last component — 11 of its 33 keys are such aliases,
    including ``log``, ``map``, ``filter`` and ``parse``. A short-name match
    would let ``audit.log(secret)`` resolve to ``console.log`` and read as
    terminating. Since a false "terminates" removes a real finding, the alias
    index is never consulted; the caller passes qualified names and the lookup
    is exact.
    """
    if not callees_at or not summaries:
        return False
    callees = callees_at.get((symbol_id, use_line))
    if not callees:
        return False
    for qualified in callees:
        summary = summaries.get(qualified)
        if summary is None or not _summary_terminates(summary):
            return False
    return True


def propagate_taint_ddg(
    ddg_edges: list[DdgEdge],
    call_edges: list[dict[str, Any]],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
    ddg_symbols: set[str] | None = None,
    ambiguous_names: frozenset[str] = frozenset(),
    function_summaries: dict[str, "FunctionSummary"] | None = None,
) -> list[TaintFlowFinding]:
    """DDG-backed taint-flow propagation with mixed-coverage analysis.

    When DDG (data dependence graph) edges are available for a function,
    taint propagation uses variable-level precision instead of symbol-level
    BFS. For functions without DDG data, structural reachability bridges
    the gap.

    Algorithm (ADR-0017 §3a):
    1. Identify taint source call sites from call_edges.
    2. For source functions with DDG data: walk forward through DDG edges
       to see which variables carry taint.
    3. At call sites within DDG-analyzed functions, check if the callee
       is a sanitizer (transforms taint) or a sink (reports finding).
    4. For functions without DDG data on the path, fall back to structural
       reachability.

    Mixed-coverage verdict (ADR-0017 §3c-3d):
    - If source AND sink functions both have DDG data: ``confidence="precise"``
    - If either lacks DDG data: ``confidence="approximate"``
    - Structural-only findings (no DDG anywhere): fall back entirely to
      ``propagate_taint_structural()``.

    Args:
        ddg_edges: DdgEdge objects from ``solve_reaching_defs()``.
        call_edges: Edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.
        ddg_symbols: Set of symbol IDs that have DDG analysis data.
            Functions in this set use DDG-precision; others use structural.
        ambiguous_names: Short names the catalog flags as ambiguous; a bare
            ambiguous callee with no usable module hint is not matched to a
            source/sink (WI-razol).

    Returns:
        List of TaintFlowFinding objects.
    """
    if not ddg_edges or not sources or not sinks:
        return []

    analyzed = ddg_symbols or set()

    # Forward index for the §3a walk: (function, def line) → lines that use
    # the value defined there.
    #
    # Keyed on ``symbol_id``, NOT ``def_block``. Block ids are function-local —
    # ``bb_5`` occurs in every function — so once edges from a whole repo are
    # aggregated into one list a block id identifies nothing. The predecessor
    # of this index was keyed ``(def_block, variable)`` and compared a block id
    # against a symbol id, which cannot match; it was also never read.
    #
    # Lines rather than variables because that is what makes the index
    # composable with call sites: a call edge records the LINE it occurs on, so
    # "is the tainted value an argument here" becomes a set membership test.
    ddg_uses: dict[tuple[str, int], set[int]] = defaultdict(set)
    for ddg_edge in ddg_edges:
        ddg_uses[(ddg_edge.symbol_id, ddg_edge.def_line)].add(ddg_edge.use_line)

    # (caller, callee) → every line that call occurs on. A caller may invoke
    # the same callee more than once, and a flow is real if the taint reaches
    # ANY of those call sites, so this is a list rather than a single line.
    #
    # ``edge["line"]`` alone is NOT every call site. The call graph keeps one
    # edge per (src, dst, edge_type) and that edge carries whichever site was
    # encountered FIRST, so asking "does the taint reach ``line``?" asks about
    # an arbitrary one of N sites. That produced a verified false negative on
    # caddy: ``printEnvironment`` calls ``fmt.Printf`` twelve times, the edge
    # recorded line 454, and the tainted call sat at 469. ``meta.call_lines``
    # (ir.deduplicate_edges) preserves the rest; read it when present and fall
    # back to ``line`` when absent — absence is that field's contract for
    # "exactly one site".
    #
    # Validated rather than trusted: ``meta`` is an open dict deserialized from
    # an artifact that may predate the field or have been hand-edited, and a
    # non-int member would reach the ``sink_line > source_line`` comparison
    # below as a TypeError.
    call_lines: dict[tuple[str, str], list[int]] = defaultdict(list)
    callee_names: dict[tuple[str, int], set[str]] = defaultdict(set)
    for edge in call_edges:
        sites: list[int] = []
        recorded = (edge.get("meta") or {}).get("call_lines")
        if isinstance(recorded, list):
            sites = [ln for ln in recorded if isinstance(ln, int)]
        line = edge.get("line")
        if isinstance(line, int) and line not in sites:
            sites.append(line)
        if sites:
            call_lines[(edge.get("src", ""), edge.get("dst", ""))].extend(sites)
            # (function, line) → the QUALIFIED names called there, for §4.
            # This is the index `meta.call_lines` exists to make possible:
            # "which callee is invoked at line U" was unanswerable for every
            # call site but the first while one edge carried one line.
            qualified = _qualified_callee(edge.get("dst", ""))
            if qualified:
                src_id = edge.get("src", "")
                for site in sites:
                    callee_names[(src_id, site)].add(qualified)

    # ADR-0017 §4b declared summaries, QUALIFIED KEYS ONLY.
    # ``load_function_summaries`` also indexes every entry under its bare last
    # component (``console.log`` → ``log``); an entry is its own qualified key
    # exactly when the key equals ``summary.function``, so this filter drops
    # the alias index without re-deriving what the loader parsed. The aliases
    # are dangerous here specifically: a false "this callee terminates the
    # taint" removes a real finding, and ``log`` / ``map`` / ``filter`` /
    # ``parse`` collide with almost anything.
    if function_summaries is None:
        from .function_summaries import load_function_summaries
        function_summaries = load_function_summaries()
    summaries = {
        k: v for k, v in function_summaries.items() if k == v.function
    }

    # Index sources, sinks, sanitizers by name (same as structural) — a list
    # per name so _lookup_named_entry can disambiguate by module/ambiguity.
    source_by_callee = _build_callee_index(sources)
    sink_by_callee = _build_callee_index(sinks)
    sanitizer_by_callee = _build_sanitizer_index_multi(sanitizers)

    # Build call-graph adjacency for structural fallback
    forward_adj, _reverse_adj = _build_adjacency(call_edges)

    # Step 1: Find source call sites (module + ambiguous_names aware — WI-razol)
    source_callers: list[tuple[str, str, TaintSource]] = []
    for edge in call_edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            source_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
        )
        if matched:
            source_callers.append((edge["src"], edge["dst"], matched))

    # Step 2: Find sink call sites (module + ambiguous_names aware — WI-razol)
    sink_callers: dict[str, list[tuple[str, TaintSink]]] = defaultdict(list)
    for edge in call_edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            sink_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
        )
        if matched:
            site = (edge["dst"], matched)
            if site not in sink_callers[edge["src"]]:
                sink_callers[edge["src"]].append(site)

    # Step 3: Find sanitizer call sites — through the SHARED helper, so the
    # INV-finoh resolution-/kind-aware gate applies here too.
    #
    # This used to be a private copy that matched on bare short name with no
    # is_resolved, call_construct or ambiguous_names filter. Because a
    # phantom barrier PRUNES the walk, the copy did not merely miss
    # sanitizers — an unrelated unresolved `x.encrypt()` bound
    # `Fernet.encrypt` and silently deleted a real flow. That is a false
    # negative, the expensive direction for a security tool, and it was live
    # on the path verify-claims runs Python through. INV-finoh's own filing
    # named this site; the fix landed only at the structural one.
    sanitizer_callers: dict[str, dict[str, TaintSanitizer]] = defaultdict(dict)
    _register_sanitizer_callers(
        call_edges, sanitizer_by_callee, sanitizer_callers, ambiguous_names,
    )

    findings: list[TaintFlowFinding] = []

    for caller_id, source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label
        # Seed selection mirrors the structural pass — see propagate_taint_structural.
        seed_id = (
            source_callee_id
            if taint_source.start_at == "callee"
            else caller_id
        )
        # The function the source is called FROM, which is what the DDG is
        # keyed on. This is ``caller_id`` regardless of ``start_at``: a
        # ``callee`` seed relocates where the BFS begins, not where the call
        # physically occurs.
        source_fn = caller_id
        source_call_lines = call_lines.get((caller_id, source_callee_id), [])
        fn_has_ddg = source_fn in analyzed

        # Structural BFS for reachability (used for mixed-coverage), through
        # the SHARED helper — this pass and the structural one had already
        # drifted on the sanitizer barrier, which is how a barrier ends up
        # meaning one thing in Python and another in Go.
        (
            reachable, parent, sanitized_reachable, sanitized_parent,
        ) = _reachability_past_sanitizers(
            seed_id, taint_label, forward_adj, sanitizer_callers,
        )

        # Check sinks
        for sink_node, sink_callee_id, taint_sink in _iter_sink_sites(
            sink_callers,
        ):
            is_sanitized = False
            if sink_node not in reachable:
                if sink_node not in sanitized_reachable:
                    continue
                is_sanitized = True

            # ADR-0017 §3a. The DDG can adjudicate exactly one shape: a sink
            # called in the SAME function as the source, where that function
            # has reaching-definition coverage. There the walk is authoritative
            # and may REMOVE a flow — structural reachability is trivially true
            # for such a pair (the two callers are the same symbol), so it has
            # no way to tell "the source's value is passed to the sink" apart
            # from "both happen to be called here".
            #
            # For every other shape — different functions, or no coverage —
            # the DDG has nothing to say, because it is intraprocedural. Those
            # keep call-graph reachability and are labelled as such. Silently
            # dropping them would convert ADR-0017 §7b's declared limitation
            # into false negatives, the expensive direction for a security
            # tool.
            # TWO SOUNDNESS GUARDS, both of which exist because the walk may
            # only REFUTE a flow on positive evidence. Neither is tuning; each
            # was added after a verified false negative on real code, and a
            # false negative is the expensive direction for a security tool.
            #
            # (1) The source's value must actually be tracked. If the DDG
            #     recorded no use of whatever the source call defines, we know
            #     nothing about where that value went — absence of evidence,
            #     not evidence of absence. caddy's printEnvironment binds
            #     `for _, v := range os.Environ()`; the Go CFG mapping's loop
            #     hook never names the range clause, so `v` has no definition
            #     in the DDG at all (WI-losod), and without this guard the
            #     walk "proved" that a literal `fmt.Println(v)` on the next
            #     line was unreachable.
            #
            # (2) A sink call site recorded BEFORE the source cannot be the
            #     one that consumes it, and — crucially — is not necessarily
            #     the only one. The call graph emits ONE edge per
            #     (caller, callee) pair, so ``line`` is *a* line where that
            #     callee is invoked, not every line. printEnvironment calls
            #     fmt.Printf twelve times and the edge records only the first
            #     (454); the tainted call at 469 is invisible. When the
            #     recorded line precedes the source, later call sites we
            #     cannot see may well receive the taint, so the absence of a
            #     dependence to the one line we can see licenses nothing.
            adjudicated = False
            if fn_has_ddg and sink_node == source_fn and source_call_lines:
                sink_call_lines = call_lines.get(
                    (sink_node, sink_callee_id), [],
                )
                source_tracked = any(
                    ddg_uses.get((source_fn, line)) for line in source_call_lines
                )
                sink_after_source = any(
                    sink_line > source_line
                    for sink_line in sink_call_lines
                    for source_line in source_call_lines
                )
                if sink_call_lines and source_tracked and sink_after_source:
                    # CONFIRM-ONLY. The walk raises confidence when it finds a
                    # data dependence and never removes a flow, because a sound
                    # refutation is not available on this substrate — see the
                    # note on §4 below. ``None`` (escaped) and ``False``
                    # (exhausted) are therefore treated alike: neither is
                    # evidence the flow is absent.
                    adjudicated = _ddg_taint_reaches(
                        source_fn, source_call_lines, sink_call_lines,
                        ddg_uses, callee_names, summaries,
                    ) is True

            # The label now records what actually decided inclusion, which is
            # what INV-sadah asserts. Before §3a existed every finding here was
            # stamped "ddg"/"precise" on the strength of a walk whose result
            # was discarded; "precise" is now earned only where the walk ran.
            if adjudicated:
                confidence = "precise"
                method = "ddg"
            else:
                confidence = "approximate"
                method = "ddg_mixed"

            path = _reconstruct_path(
                {**parent, **sanitized_parent} if is_sanitized else parent,
                seed_id, sink_node,
            )
            findings.append(TaintFlowFinding(
                taint_label=taint_label,
                source_symbol=seed_id,
                source_primitive=taint_source.name,
                source_module=taint_source.module,
                source_boundary=taint_source.source_boundary,
                sink_symbol=sink_callee_id,
                sink_primitive=taint_sink.name,
                sink_module=taint_sink.module,
                sink_zone=taint_sink.zone,
                sanitized=is_sanitized,
                confidence=confidence,
                analysis_method=method,
                path=path,
            ))

    return findings
