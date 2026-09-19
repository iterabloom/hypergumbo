# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analyzer registry for decorator-based dynamic dispatch.

This module provides the canonical registration system for language analyzers,
mirroring the proven pattern from linkers/registry.py. Analyzers self-register
via the @register_analyzer decorator at import time; entry-point-based plugin
discovery triggers the imports.

How It Works
------------
1. Each analyzer module decorates its entry function with @register_analyzer()
2. Language packages export ANALYZER_MODULES lists via entry-points
3. ensure_discovered() loads entry-points, imports listed modules (triggering decorators)
4. run_all_analyzers() / get_all_analyzers() iterate the populated registry

Why This Design
---------------
- Self-registration: the analyzer file is self-describing (decorator on the function)
- Plugin extensibility: entry-points enable external language packages
- Rich metadata: priority, supports_max_files, capture_symbols_as, depends_on
- Consistency: mirrors the linker registry pattern (ADR-0012 Step 1)
- One language vocabulary (WI-juzig): ``languages`` is gated at decoration
  time against ``taxonomy.LANGUAGES`` unless the registration declares a
  ``language_state`` of ``no_taxonomy_spec`` or ``no_language``; there is no
  silent ``[name]`` default for a name that is not a language
- Producer contract (ADR-0057 §10 / WI-hohuh): a backend that shares a
  language with another producer declares ON ITS REGISTRATION how its
  records pair with the other's (``merge=MergeAnchor(...)``) or that the two
  never emit a record in common (``merge=MergeDisjoint(...)``), plus
  ADR-0045 §5's ``executes_analysed_code``. ``merge_participants()`` is what
  the merge pass reads; it refuses an undeclared producer BY NAME rather
  than pairing nothing and reading as "nothing to merge"

Usage
-----
In an analyzer module:

    from hypergumbo_core.analyze.registry import register_analyzer

    @register_analyzer("go", priority=50)
    def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        ...

Discovery happens automatically via entry-points when ensure_discovered() is called.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .base import AnalysisResult
from ..ir import compute_pass_version

logger = logging.getLogger(__name__)

# Type alias for analyzer functions
AnalyzerFunc = Callable[..., AnalysisResult]


# ---------------------------------------------------------------------------
# ADR-0057 §10 / WI-hohuh: the producer contract
# ---------------------------------------------------------------------------
#
# A language can have more than one producer (``rust``: the tree-sitter
# incumbent and the SCIP backend). The merge pass that folds two producers'
# records for one declaration into one (WI-kokiz) needs, PER PRODUCER, how to
# derive the backend-neutral declaration name from ``Symbol.name`` and whether
# ``Symbol.span`` is the identifier TOKEN or the ITEM extent. As first
# designed that key was Rust-shaped and lived in the pass; a key hardcoded
# for one language makes backend N+1 a rewrite of the pass — or, worse, pairs
# nothing and reads as "nothing to merge" (ABSENT != EMPTY). So each backend
# DECLARES it here, on the registration, the pass reads the declaration, and
# an undeclared producer is refused by name.
#
# Not every pair of producers for one language is two backends for one set
# of declarations: the ``javascript`` analyzer reads a component file's
# <script> block while ``svelte`` / ``vue`` read its template, and they never
# emit a record in common. That is DECLARED too (``MergeDisjoint``): inferring
# it from backend inequality would misfire on exactly that pair, and a silent
# "no anchor" would be indistinguishable from an omission.
#
# ADR-0045 §5's ``executes_analysed_code`` lands on the same surface: which
# store a backend's opt-in belongs in (trust grant vs preference) follows
# from a bit the backend declares, never from a list someone remembers to
# update — so the next backend cannot omit either declaration.

SPAN_ROLE_TOKEN = "token"
"""``Symbol.span`` is the identifier token (rust-analyzer's Definition
``range``, single-line on 79 of 79 methods — INV-lodum)."""

SPAN_ROLE_ITEM = "item"
"""``Symbol.span`` is the whole item (every tree-sitter analyzer)."""

_SPAN_ROLES = frozenset({SPAN_ROLE_TOKEN, SPAN_ROLE_ITEM})

AUDIT_CITATION_PREFIX = "docs/audits/"

ALTERNATIVE_BACKENDS: frozenset[str] = frozenset({"scip"})
"""Backends that are the ALTERNATIVE arm of a language, never its incumbent.

ADR-0057 §5's built-in arbitration default is "incumbent first" — tree-sitter
before any SCIP/LSP backend. The Python incumbent is ``ast``-backed, not
tree-sitter, so the rule is spelled on the alternative side: among a
language's anchored producers, the ones whose ``backend`` is NOT listed here
are incumbents. Extend when an LSP-backed backend registers. This is a fact
about BACKENDS (which arm is the alternative), read by :func:`incumbents_of`
and by the recorded-producer-input lint; which arm WINS a contest is the
arbitration policy's (``hypergumbo_core.arbitration``, WI-hukuf), whose
built-in is :func:`incumbent_first` and which ``config.toml`` can reorder.
"""
"""Every ``authoritative_for`` entry cites a committed table under here,
produced by the backend-agreement instrument (ADR-0057 §5, §10). No citation,
no authority: the built-in arbitration default stays incumbent-first."""


class MergeDeclarationError(ValueError):
    """A merge declaration is malformed.

    Raised at construction or decoration time, naming the offending value.
    A silently-ignored declaration would be the ABSENT != EMPTY defect at
    registration, which is why a dict with the right keys is refused too:
    duck typing would accept it and then ignore a misspelled key.
    """


class UndeclaredProducerError(ValueError):
    """A language has more than one producer and one has not declared how
    its records relate to another's.

    Raised by :func:`merge_participants`. The merge pass refuses, naming the
    analyzer and the language, rather than falling through — a pass that
    pairs nothing is indistinguishable from a language with nothing to pair.
    """


def as_emitted(name: str) -> str:
    """Name key: ``Symbol.name`` unchanged (the SCIP descriptor name)."""
    return name


def last_segment(separator: str) -> Callable[[str], str]:
    """Name-key factory: the last ``separator``-delimited segment of a name.

    The separator is the DECLARING analyzer's to supply, from the taxonomy's
    ``QUALIFIED_NAME_SEPARATORS``; this module assumes no language's
    spelling (``test_registry_merge_contract`` pins that it carries none).
    """

    def key(name: str) -> str:
        return name.rsplit(separator, 1)[-1]

    key.__name__ = f"last_segment({separator!r})"
    return key


@dataclass(frozen=True)
class MergeAnchor:
    """How a backend's records pair with another producer's for one declaration.

    Attributes:
        name_key: ``Symbol.name`` -> backend-neutral declaration name. The
            incumbent and every alternative for a language must map the two
            records for ONE declaration to the SAME key; that equality on
            recorded producer output is the registry test (ADR-0057 §12).
        span_role: :data:`SPAN_ROLE_TOKEN` or :data:`SPAN_ROLE_ITEM`. The
            pass's containment test is directional — token inside item; two
            item spans compare by equality.
        authoritative_for: attribute -> citation. Attributes this backend
            is authoritative on BY MEASUREMENT, each citing a committed
            ``docs/audits/`` table. Empty by default (ADR-0057 §5:
            incumbent-first until a measurement shows otherwise).
    """

    name_key: Callable[[str], str]
    span_role: str
    authoritative_for: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not callable(self.name_key):
            raise MergeDeclarationError(
                f"MergeAnchor: name_key must be callable, got {self.name_key!r}"
            )
        if self.span_role not in _SPAN_ROLES:
            raise MergeDeclarationError(
                f"MergeAnchor: span_role={self.span_role!r} is not one of "
                f"{sorted(_SPAN_ROLES)}"
            )
        for attribute, citation in self.authoritative_for.items():
            if not citation.startswith(AUDIT_CITATION_PREFIX):
                raise MergeDeclarationError(
                    f"MergeAnchor: authoritative_for[{attribute!r}]={citation!r} does "
                    f"not cite a committed {AUDIT_CITATION_PREFIX} table; authority is "
                    f"by measurement only (ADR-0057 §5, §10)"
                )
        object.__setattr__(self, "authoritative_for", dict(self.authoritative_for))


@dataclass(frozen=True)
class MergeDisjoint:
    """This analyzer shares a language with ``partners`` but never a record.

    A declaration, not an inference: the pass treats the declarer's records
    as single-producer with respect to each named partner. Naming the
    partner is what keeps the claim honest when a third producer arrives —
    a bare "disjoint" would silently cover it too.
    """

    partners: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.partners or not all(self.partners):
            raise MergeDeclarationError(
                "MergeDisjoint: partners must name at least one analyzer"
            )
        object.__setattr__(self, "partners", tuple(self.partners))


@dataclass
class RegisteredAnalyzer:
    """Metadata for a registered analyzer.

    Attributes:
        name: Unique identifier (e.g., "go", "rust", "python")
        func: The analyzer function (direct reference from decorator)
        module_path: Module containing the function (for test patchability)
        func_name: Function name in the module (for test patchability)
        priority: Execution order (lower = earlier). Default 50.
        supports_max_files: Whether this analyzer accepts a max_files parameter
        capture_symbols_as: If set, store this analyzer's symbols under
            this key in captured_symbols dict for linkers (e.g., "java" for JNI)
        description: Human-readable description of the analyzer (INV-morag PR 2)
        pass_label: Display label for cmd_catalog output (INV-morag PR 2)
        backend: Parsing backend identifier — e.g., "ast", "tree-sitter",
            "regex", "pattern". Decoupled from pass_id so the ID stays stable
            across backend swaps (INV-morag PR 2 / ADR-spec).
        languages: Languages this analyzer handles, in the TAXONOMY's
            vocabulary (used by suggest_passes_for_languages, the finalize
            ``skipped_languages`` honesty signal and the file-presence
            pre-filter). Set by :func:`register_analyzer` after the WI-juzig
            gate; ``[]`` only under ``language_state == "no_language"``.
        language_state: Which declared state ``languages`` is in — one of
            ``LANGUAGE_STATE_TAXONOMY`` / ``LANGUAGE_STATE_NO_SPEC`` /
            ``LANGUAGE_STATE_NO_LANGUAGE`` (WI-juzig).
        availability: ``"core"`` (always available) or ``"extra"`` (requires
            optional deps like tree-sitter).
        requires: Optional package requirement label (e.g.,
            ``"tree-sitter-language-pack"``).
        pass_version: Code-hash of the analyzer's module source, computed at
            decoration time via :func:`hypergumbo_core.ir.compute_pass_version`
            (INV-morag PR 1). Changes only when the pass implementation
            changes; immune to unrelated package-version bumps.
        find_files: Optional ``(repo_root) -> Iterable[Path]`` callable that
            returns the canonical file set this analyzer will see. When
            set, ``profile.detect_profile`` uses it for the per-language
            file count instead of extension-globbing — keeping
            ``profile.languages[L].files`` consistent with what the L-pass
            analyzes (INV-hokig). Required for languages whose canonical
            file set isn't fully captured by their extension globs
            (e.g., bash detects extensionless shebang scripts).
        executes_analysed_code: ADR-0045 §5. True when activating this backend
            runs code from the analysed repository (rust-analyzer executes
            ``build.rs`` and proc macros); its opt-in is then a per-repository
            trust grant, never a config preference. The trust store and the
            config deny-list are DERIVED from this bit.
        merge: ADR-0057 §10. ``None`` for the sole producer of its languages;
            a :class:`MergeAnchor` for a backend whose records pair with
            another producer's; a :class:`MergeDisjoint` for one that shares a
            language but never a record. Required — by
            :func:`merge_participants` and the contract test — on every
            producer of a language that has more than one.
    """

    name: str
    func: AnalyzerFunc
    module_path: str | None = None
    func_name: str | None = None
    priority: int = 50
    supports_max_files: bool = False
    capture_symbols_as: str | None = None
    description: str = ""
    pass_label: str = ""
    backend: str = ""
    languages: list[str] = field(default_factory=list)
    language_state: str = "taxonomy"  # WI-juzig: LANGUAGE_STATE_* (defined below)
    availability: str = "core"
    requires: str | None = None
    pass_version: str = ""
    find_files: Callable[[Path], Iterable[Path]] | None = None
    # WI-hupaz / WI-dilab / INV-hujog: pass-id dependencies surfaced into
    # ``Pass.depends_on``. CNF: outer-AND of inner-OR clauses. Empty default
    # = no declared upstream.
    depends_on: list[list[str]] = field(default_factory=list)
    # ADR-0045 §5 / ADR-0057 §10 (WI-hohuh): the producer contract.
    executes_analysed_code: bool = False
    merge: MergeAnchor | MergeDisjoint | None = None

    def get_func(self) -> AnalyzerFunc:
        """Get the analyzer function, resolving from module for patchability.

        When module_path and func_name are set, looks up the function from
        the module at call time rather than using the stored reference. This
        enables test patching (e.g., ``patch("module.func", ...)``) to work
        correctly, since the module attribute is checked at each call.

        Falls back to the stored function reference if the module-level
        lookup fails (e.g., for functions defined inside test methods that
        aren't module-level attributes).
        """
        if self.module_path and self.func_name:
            try:
                module = importlib.import_module(self.module_path)
                return getattr(module, self.func_name)
            except (ImportError, AttributeError):
                return self.func
        return self.func


# Global registry of analyzers
_ANALYZER_REGISTRY: dict[str, RegisteredAnalyzer] = {}

# Discovery flag — ensures entry-point loading happens only once
_discovered: bool = False


# ---------------------------------------------------------------------------
# WI-juzig: the language declaration gate
# ---------------------------------------------------------------------------
#
# ``languages`` used to default to ``[name]`` and nothing checked it, so the
# registry spoke a second vocabulary next to the taxonomy's: a pass named
# ``make`` was language ``make`` while the taxonomy, profile detection and
# every file-anchored ``Symbol.language`` said ``makefile`` (INV-nidul — a
# FALSE ``limits.skipped_languages`` verdict on a shipped artifact), and two
# names that are not languages at all (``rust_analyzer``, ``manifest_targets``)
# became languages ``all_known_languages()`` — and therefore the spec
# validator — accepted. The default also conflated absent with empty: an
# explicit ``languages=[]`` became ``[name]``.
#
# ONE VOCABULARY: the taxonomy's. An analyzer sits in exactly one declared
# state, and the declaration is checked in BOTH directions so it cannot go
# stale silently.
LANGUAGE_STATE_TAXONOMY = "taxonomy"
"""Default: every language is a ``taxonomy.LANGUAGES`` key (``LANGUAGE_ALIASES``
keys are accepted and canonicalised to the taxonomy name)."""

LANGUAGE_STATE_NO_SPEC = "no_taxonomy_spec"
"""A real language the taxonomy has no ``LanguageSpec`` for (the WI-futin
coverage gap: gleam, hack, odin, ansible, ...). The profile can never detect
it, so its analyzer is dispatched unconditionally. NONE of the declared
languages may be a taxonomy key — once a spec lands, the declaration must go,
and the gate raises until it does."""

LANGUAGE_STATE_NO_LANGUAGE = "no_language"
"""Not a language pass — a synthesis pass over many formats that stamps each
symbol with the FORMAT's language (``manifest_targets``). ``languages`` is
stored as ``[]`` and no downstream consumer may re-inflate that into
``{name}``."""

_LANGUAGE_STATES = frozenset({
    LANGUAGE_STATE_TAXONOMY, LANGUAGE_STATE_NO_SPEC, LANGUAGE_STATE_NO_LANGUAGE,
})


class LanguageDeclarationError(ValueError):
    """An ``@register_analyzer`` language declaration is outside the vocabulary.

    Raised at decoration (import) time, naming the analyzer and the offending
    value. It is a defect in the registration, not a missing optional
    dependency, so :func:`_import_module_list` re-raises it instead of
    logging it away — an analyzer that silently vanished from the catalogue
    would be the ABSENT != EMPTY defect in a new costume.
    """


def _gate_language_declaration(
    name: str,
    languages: list[str] | None,
    language_state: str,
) -> list[str]:
    """Return the effective, canonical language list or raise.

    ``None`` means "not given" and defaults to ``[name]`` — then gated like
    any other value. ``[]`` is NOT "not given": the caller wrote something,
    and the registry must not guess whether they meant ``[name]`` or
    "no language" — they say which with ``language_state``.
    """
    # Local import: catalog/taxonomy import this module's registry at call
    # time; keep the module import graph acyclic.
    from ..taxonomy import LANGUAGE_ALIASES, LANGUAGES

    if language_state not in _LANGUAGE_STATES:
        raise LanguageDeclarationError(
            f"analyzer {name!r}: language_state={language_state!r} is not one of "
            f"{sorted(_LANGUAGE_STATES)}"
        )
    if language_state == LANGUAGE_STATE_NO_LANGUAGE:
        if languages:
            raise LanguageDeclarationError(
                f"analyzer {name!r}: language_state={LANGUAGE_STATE_NO_LANGUAGE!r} "
                f"declares no language, but languages={list(languages)!r} was given"
            )
        return []
    if languages is None:
        effective = [name]
    elif not languages:
        raise LanguageDeclarationError(
            f"analyzer {name!r}: languages=[] is not a declaration (absent != empty). "
            f"Omit it to mean [{name!r}], or declare "
            f"language_state={LANGUAGE_STATE_NO_LANGUAGE!r} for a pass that is not "
            f"a language"
        )
    else:
        effective = list(languages)
    if language_state == LANGUAGE_STATE_NO_SPEC:
        stale = [
            lang for lang in effective
            if lang in LANGUAGES or lang in LANGUAGE_ALIASES
        ]
        if stale:
            raise LanguageDeclarationError(
                f"analyzer {name!r}: language_state={LANGUAGE_STATE_NO_SPEC!r} is stale — "
                f"the taxonomy now carries a LanguageSpec for {stale!r}; remove the "
                f"declaration"
            )
        return effective
    canonical: list[str] = []
    for lang in effective:
        resolved = LANGUAGE_ALIASES.get(lang, lang)
        if resolved not in LANGUAGES:
            raise LanguageDeclarationError(
                f"analyzer {name!r} declares language {lang!r}, which is not a "
                f"taxonomy language (taxonomy.LANGUAGES) or alias. Either name a "
                f"taxonomy language via languages=[...], declare "
                f"language_state={LANGUAGE_STATE_NO_SPEC!r} for a language the "
                f"taxonomy lacks a LanguageSpec for, or "
                f"language_state={LANGUAGE_STATE_NO_LANGUAGE!r} for a pass that is "
                f"not a language"
            )
        canonical.append(resolved)
    return canonical


def register_analyzer(  # nosec B107 — pass_label/backend defaults are tag strings, not passwords; bandit flags any "pass*" name with "" default
    name: str,
    priority: int = 50,
    supports_max_files: bool = False,
    capture_symbols_as: str | None = None,
    description: str = "",
    pass_label: str = "",
    backend: str = "",
    languages: list[str] | None = None,
    language_state: str = LANGUAGE_STATE_TAXONOMY,
    availability: str = "core",
    requires: str | None = None,
    find_files: Callable[[Path], Iterable[Path]] | None = None,
    depends_on: list[list[str]] | None = None,
    executes_analysed_code: bool = False,
    merge: MergeAnchor | MergeDisjoint | None = None,
) -> Callable[[AnalyzerFunc], AnalyzerFunc]:
    """Decorator to register an analyzer function.

    Raises:
        LanguageDeclarationError: at decoration time when the effective
            ``languages`` are outside the taxonomy's vocabulary for the
            declared ``language_state`` (WI-juzig). There is no silent
            default: an undeclared non-language name does not register.
        MergeDeclarationError: at decoration time when ``merge`` is neither
            a :class:`MergeAnchor` nor a :class:`MergeDisjoint` (ADR-0057
            §10) — the registry does not duck-type a declaration.

    Args:
        name: Unique identifier for this analyzer (e.g., "go", "rust").
        priority: Execution order (lower = earlier). Default 50.
        supports_max_files: Whether the analyzer accepts max_files parameter.
        capture_symbols_as: Key for storing symbols in captured_symbols dict.
        description: Human-readable description (catalog display).
        pass_label: Display label for cmd_catalog (INV-morag PR 2; defaults
            to the analyzer name when empty).
        backend: Parsing backend tag — ``"ast"``, ``"tree-sitter"``,
            ``"regex"``, ``"pattern"``, etc. Decoupled from pass_id so
            backend swaps don't churn the ID.
        languages: Languages this analyzer handles, in the taxonomy's
            vocabulary (``taxonomy.LANGUAGES`` keys; ``LANGUAGE_ALIASES``
            keys are canonicalised). Not given -> ``[name]``, then gated.
            An explicit ``[]`` is an error — absent is not empty.
        language_state: ``"taxonomy"`` (default), ``"no_taxonomy_spec"``
            for a real language the taxonomy has no LanguageSpec for (the
            WI-futin coverage gap; none of ``languages`` may be a taxonomy
            key), or ``"no_language"`` for a pass that is not a language
            (``languages`` must be omitted; stored as ``[]``).
        availability: ``"core"`` or ``"extra"``.
        requires: Optional pip-package requirement label.
        find_files: Optional ``(repo_root) -> Iterable[Path]`` callable. When
            set, ``profile.detect_profile`` calls it to count files for
            this language instead of extension-globbing, keeping
            ``profile.languages[L].files`` consistent with the analyzer's
            enumeration (INV-hokig). Use for languages whose canonical
            file set extends beyond extension patterns — e.g., bash
            detects extensionless shebang scripts.
        executes_analysed_code: ADR-0045 §5 — True when activating this
            backend executes code from the analysed repository. Decides
            which store its opt-in lands in (trust grant vs preference).
        merge: ADR-0057 §10 — how this backend's records relate to another
            producer's for the same language: a :class:`MergeAnchor`
            (``name_key``, ``span_role``, measured ``authoritative_for``)
            when the two emit records for the same declarations, or a
            :class:`MergeDisjoint` naming the producers it shares a language
            with but never a record. Required on every producer of a
            language that has more than one; ``None`` otherwise.

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_analyzer(
            "go",
            priority=50,
            description="Go via tree-sitter",
            backend="tree-sitter",
            languages=["go"],
            availability="extra",
            requires="tree-sitter-language-pack",
        )
        def analyze_go(repo_root: Path) -> AnalysisResult:
            ...
    """

    def decorator(func: AnalyzerFunc) -> AnalyzerFunc:
        if merge is not None and not isinstance(merge, (MergeAnchor, MergeDisjoint)):
            raise MergeDeclarationError(
                f"analyzer {name!r}: merge={merge!r} is not a declaration; pass a "
                f"MergeAnchor(name_key=..., span_role=..., authoritative_for=...) or a "
                f"MergeDisjoint(partners=...) (ADR-0057 §10)"
            )
        _ANALYZER_REGISTRY[name] = RegisteredAnalyzer(
            name=name,
            func=func,
            module_path=func.__module__,
            func_name=func.__name__,
            priority=priority,
            supports_max_files=supports_max_files,
            capture_symbols_as=capture_symbols_as,
            description=description,
            pass_label=pass_label or name,
            backend=backend,
            languages=_gate_language_declaration(name, languages, language_state),
            language_state=language_state,
            availability=availability,
            requires=requires,
            pass_version=compute_pass_version(func),
            find_files=find_files,
            depends_on=[list(clause) for clause in depends_on] if depends_on else [],
            executes_analysed_code=executes_analysed_code,
            merge=merge,
        )
        return func

    return decorator


def get_analyzer(name: str) -> RegisteredAnalyzer | None:
    """Get a registered analyzer by name.

    Args:
        name: The analyzer identifier

    Returns:
        The RegisteredAnalyzer, or None if not found.
    """
    return _ANALYZER_REGISTRY.get(name)


def get_all_analyzers() -> Iterator[RegisteredAnalyzer]:
    """Get all registered analyzers in priority order.

    Yields:
        RegisteredAnalyzer objects, sorted by priority (ascending).
    """
    for analyzer in sorted(_ANALYZER_REGISTRY.values(), key=lambda a: a.priority):
        yield analyzer


def analyzers_for_language(language: str) -> list[RegisteredAnalyzer]:
    """Every registered analyzer that declares ``language``, in priority order.

    WI-juzig: the one place that answers "who produces language L?" in the
    taxonomy's vocabulary. A language can have MORE THAN ONE producer —
    ``rust`` has the tree-sitter incumbent and the SCIP backend — so callers
    that used to key a dict by language, or ``get_analyzer(<language>)`` by
    NAME, silently kept one and lost the other (or found nothing when the
    pass name and the language differ: ``make`` / ``makefile``). Does not
    trigger discovery; call :func:`ensure_discovered` first.
    """
    return [a for a in get_all_analyzers() if language in a.languages]


def backends_executing_analysed_code() -> frozenset[str]:
    """Names of the registered analyzers declaring ``executes_analysed_code``.

    ADR-0045 §5: the trust store accepts exactly these and the config tiers
    refuse exactly these — both derived from the declaration, so a backend
    added later lands in the right store by describing itself. Does not
    trigger discovery; call :func:`ensure_discovered` first.
    """
    return frozenset(a.name for a in _ANALYZER_REGISTRY.values() if a.executes_analysed_code)


def _pair_is_declared(left: RegisteredAnalyzer, right: RegisteredAnalyzer) -> bool:
    """Two anchored producers pair; otherwise one side's disjointness must
    name the other. An anchored incumbent need not enumerate every template
    analyzer that keeps out of its way — the analyzer that stays disjoint
    says so."""
    if isinstance(left.merge, MergeAnchor) and isinstance(right.merge, MergeAnchor):
        return True
    return any(
        isinstance(a.merge, MergeDisjoint) and b.name in a.merge.partners
        for a, b in ((left, right), (right, left))
    )


def incumbent_first(analyzers: Iterable[RegisteredAnalyzer]) -> list[RegisteredAnalyzer]:
    """``analyzers`` ordered incumbent first: non-alternative backends, then
    by registration priority — the ADR-0057 §5 built-in precedence the merge
    pass applies to categorical attributes until WI-hukuf makes it a
    ``config.toml`` preference."""
    return sorted(
        analyzers,
        key=lambda a: (a.backend in ALTERNATIVE_BACKENDS, a.priority, a.name),
    )


def incumbents_of(language: str) -> list[RegisteredAnalyzer]:
    """The anchored producers of ``language`` that are not an alternative arm
    (:data:`ALTERNATIVE_BACKENDS`) — empty when the language has no pairing
    producers. The recorded-producer-input lint reads this; precedence is
    the arbitration policy's, not this list's."""
    return [a for a in merge_participants(language) if a.backend not in ALTERNATIVE_BACKENDS]


def merge_participants(language: str) -> list[RegisteredAnalyzer]:
    """The anchored producers of ``language`` the merge pass pairs among.

    ADR-0057 §10. Empty when the language has one producer (nothing to
    merge — the pass is a no-op and a tree-sitter-only run is byte-identical)
    or when fewer than two of its producers are anchored. Raises
    :class:`UndeclaredProducerError`, naming the analyzer, when the language
    has two or more producers and any of them carries no declaration, or
    when a pair is covered by neither an anchor on both sides nor a
    :class:`MergeDisjoint` naming the other. The pass does not guess. Does
    not trigger discovery; call :func:`ensure_discovered` first.
    """
    producers = analyzers_for_language(language)
    if len(producers) < 2:
        return []
    undeclared = [a.name for a in producers if a.merge is None]
    if undeclared:
        raise UndeclaredProducerError(
            f"language {language!r} has {len(producers)} producers "
            f"({[a.name for a in producers]}) and {undeclared} declare(s) no merge "
            f"relation: declare merge=MergeAnchor(...) or "
            f"merge=MergeDisjoint(partners=...) on the registration (ADR-0057 §10); "
            f"the merge pass does not guess"
        )
    for index, left in enumerate(producers):
        for right in producers[index + 1:]:
            if not _pair_is_declared(left, right):
                raise UndeclaredProducerError(
                    f"language {language!r}: {left.name!r} and {right.name!r} both "
                    f"produce it, but neither declares the other — two MergeAnchor "
                    f"declarations pair; otherwise one side's MergeDisjoint must name "
                    f"the other (ADR-0057 §10)"
                )
    anchored = [a for a in producers if isinstance(a.merge, MergeAnchor)]
    return anchored if len(anchored) >= 2 else []


def run_analyzer(
    name: str,
    repo_root: Path,
    **kwargs: Any,
) -> AnalysisResult:
    """Run a specific analyzer by name.

    Args:
        name: The analyzer identifier
        repo_root: Repository root path
        **kwargs: Additional arguments passed to the analyzer

    Returns:
        AnalysisResult from the analyzer

    Raises:
        KeyError: If the analyzer is not registered.
    """
    analyzer = _ANALYZER_REGISTRY.get(name)
    if analyzer is None:
        available = ", ".join(sorted(_ANALYZER_REGISTRY)) or "none registered"
        raise KeyError(f"Unknown analyzer: {name!r}. Available: {available}")
    return analyzer.get_func()(repo_root, **kwargs)


def run_all_analyzers(
    repo_root: Path,
    **kwargs: Any,
) -> list[tuple[str, AnalysisResult]]:
    """Run all registered analyzers in priority order.

    Args:
        repo_root: Repository root path
        **kwargs: Additional arguments passed to each analyzer

    Returns:
        List of (name, result) tuples in execution order.
    """
    results = []
    for analyzer in get_all_analyzers():
        result = analyzer.get_func()(repo_root, **kwargs)
        results.append((analyzer.name, result))
    return results


def _load_entry_point_modules() -> None:
    """Load analyzer modules discovered via entry-points.

    Handles two formats for the 'hypergumbo.analyzers' entry-point group:

    1. **Module list (target format):** A list of module path strings.
       Importing each module triggers @register_analyzer decorators.
       Example: ["hypergumbo_lang_mainstream.bash", "hypergumbo_lang_mainstream.go"]

    2. **AnalyzerSpec list (transition format):** A list of AnalyzerSpec
       NamedTuples. Each spec is converted to a RegisteredAnalyzer and added
       to the registry directly. This allows gradual migration from the old
       AnalyzerSpec system — packages can switch one at a time.

    Format detection: if the first element is a string, treat as module list;
    if it has a 'module_path' attribute, treat as AnalyzerSpec list.
    """
    try:
        eps = importlib.metadata.entry_points(group="hypergumbo.analyzers")
    except Exception:  # pragma: no cover
        logger.debug("Failed to load entry_points for hypergumbo.analyzers")
        return

    for ep in eps:
        try:
            entry_list = ep.load()
        except Exception:
            logger.debug("Failed to load entry point %s", ep.name)
            continue

        if not isinstance(entry_list, list) or not entry_list:
            logger.debug("Entry point %s did not return a non-empty list, skipping", ep.name)
            continue

        # Detect format from first element
        first = entry_list[0]
        if isinstance(first, str):
            # New format: list of module paths to import
            _import_module_list(entry_list)
        elif hasattr(first, "module_path"):
            # Transition format: list of AnalyzerSpec NamedTuples
            _register_from_specs(entry_list)
        else:
            logger.debug(
                "Entry point %s returned unknown format, skipping", ep.name
            )


def _import_module_list(module_paths: list[str]) -> None:
    """Import a list of module paths to trigger @register_analyzer decorators."""
    for module_path in module_paths:
        try:
            importlib.import_module(module_path)
        except LanguageDeclarationError:
            # WI-juzig: a misdeclared registration is a defect, not a missing
            # optional dependency; swallowing it would make the analyzer
            # silently vanish from the catalogue.
            raise
        except Exception:
            logger.debug("Failed to import analyzer module %s", module_path)


def _register_from_specs(specs: list) -> None:
    """Register analyzers from AnalyzerSpec NamedTuples (transition support).

    Converts each AnalyzerSpec to a RegisteredAnalyzer by lazily loading
    the analyzer function from the spec's module_path and func_name.
    """
    for spec in specs:
        if spec.name in _ANALYZER_REGISTRY:
            continue  # Already registered (e.g., via decorator)
        try:
            module = importlib.import_module(spec.module_path)
            func = getattr(module, spec.func_name)
            _ANALYZER_REGISTRY[spec.name] = RegisteredAnalyzer(
                name=spec.name,
                func=func,
                module_path=spec.module_path,
                func_name=spec.func_name,
                supports_max_files=getattr(spec, "supports_max_files", False),
                capture_symbols_as=getattr(spec, "capture_symbols_as", None),
            )
        except Exception:
            logger.debug(
                "Failed to register analyzer %s from spec", spec.name
            )


def ensure_discovered() -> None:
    """Ensure all analyzer entry-points have been loaded.

    On first call, loads entry-points from the 'hypergumbo.analyzers' group
    and imports the listed modules (triggering @register_analyzer decorators).
    Subsequent calls are no-ops until clear_registry() resets the flag.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True
    _load_entry_point_modules()


def clear_registry() -> None:
    """Clear the analyzer registry and reset discovery. For testing only."""
    global _discovered
    _ANALYZER_REGISTRY.clear()
    _discovered = False


def list_registered() -> list[str]:
    """List all registered analyzer names. For debugging."""
    return list(_ANALYZER_REGISTRY.keys())
