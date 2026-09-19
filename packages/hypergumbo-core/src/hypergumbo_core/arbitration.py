# SPDX-License-Identifier: AGPL-3.0-or-later
"""The arbitration policy: precedence among a language's producers (ADR-0057 §5, WI-hukuf).

WHAT IS DECIDED HERE. When two producers emit a record for one declaration
the merge pass folds them (§3) and, for every categorical attribute they
disagree on, ONE value becomes the scalar a consumer reads (§4) — the
other stays in the provenance slot (§6). Which one is a *precedence* among
producers, and §5 rules that this precedence is a preference: a built-in
default that config can change, never a hardcoded "type-aware wins". This
module is the one place that precedence is computed, and the two declared
LEVELS the same ruling covers live beside it: the corroboration confidence
(§13) and the same-site supersession factor (§14).

THE BUILT-IN. Incumbent first — the registry's ``incumbent_first`` order:
producers whose backend is not an ALTERNATIVE arm (``ALTERNATIVE_BACKENDS``)
before those that are, then registration priority. Levels 0.95 and 0.5.
No per-attribute exception ships: the first backend-agreement table
(``docs/audits/0019``, WI-dajif) found none warranted — the ``kind``
disagreements are a producer gap (WI-bamar) and the ``is_exported`` ones a
merge-pass defect (INV-huboz), neither a case for precedence.

WHAT CONFIG CAN CHANGE (``[merge]`` in either ADR-0045 preference tier —
it executes nothing, so it is not a trust key):

    [merge]
    prefer = ["scip", "tree-sitter"]     # backend order; unlisted backends
                                         # follow, incumbents first
    corroborated_confidence = 0.95
    superseded_stub_rank_factor = 0.5
    [merge.prefer_by_attribute]
    kind = ["scip"]                      # REPLACES `prefer` for this attribute

Orders are spelled by BACKEND (``tree-sitter``, ``scip``, ``ast``, …), not by
analyzer name, so one line means the same thing for every language that
backend serves. The project tier outranks the user tier key by key.

WHAT MAY NOT BE DONE QUIETLY. §10: a BUILT-IN per-attribute exception —
one this module ships rather than one a user writes — exists only by
citing a committed ``docs/audits/`` table of ``kind: backend_agreement``.
:data:`BUILTIN_ATTRIBUTE_EXCEPTIONS` is that table and
:func:`validate_builtin_exceptions` is the gate a test runs on the live
tree: an entry with no citation, a citation outside ``docs/audits/``, a
missing file or a document of another kind is refused by name.

What stays the registry's: WHICH arm is the alternative
(``ALTERNATIVE_BACKENDS``, read by ``incumbents_of`` and by the
recorded-producer-input lint) is a fact about backends; this module only
decides who wins a contest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .analyze.registry import RegisteredAnalyzer
    from .user_config import LayeredConfig

#: §13: the declared corroboration level when two producers reach one edge
#: by DISTINCT inference pathways (ADR-0012's own number for a type-resolved
#: pathway). Not max, not noisy-OR: a declared level.
CORROBORATED_CONFIDENCE = 0.95

#: §14: a resolved first-party edge demotes a same-site external stub's
#: ``rank_score`` by this factor. Never deleted, ``confidence`` untouched.
SUPERSEDED_STUB_RANK_FACTOR = 0.5

#: Where a built-in exception's citation must live (mirrors the registry's
#: ``AUDIT_CITATION_PREFIX`` for ``authoritative_for``).
AUDIT_CITATION_PREFIX = "docs/audits/"

#: The sibling document kind the citation must declare (WI-dajif).
BACKEND_AGREEMENT_KIND = "backend_agreement"


@dataclass(frozen=True)
class AttributeException:
    """A BUILT-IN per-attribute precedence, licensed by a committed table."""

    language: str
    attribute: str
    prefer: Tuple[str, ...]  # backend order for this attribute
    cites: str  # ``docs/audits/<NN>-....md`` of kind backend_agreement


#: §10: empty until a committed backend-agreement table warrants an entry.
BUILTIN_ATTRIBUTE_EXCEPTIONS: Tuple[AttributeException, ...] = ()


class BuiltinExceptionError(ValueError):
    """A built-in per-attribute exception without a valid citation."""


def validate_builtin_exceptions(
    repo_root: Path,
    *,
    exceptions: Sequence[AttributeException] = BUILTIN_ATTRIBUTE_EXCEPTIONS,
) -> None:
    """Refuse, by name, any built-in exception that does not cite a committed
    ``docs/audits/`` document of ``kind: backend_agreement``."""
    from .audit_findings import declared_kind

    for entry in exceptions:
        where = f"built-in exception {entry.language}.{entry.attribute}"
        if not entry.cites:
            raise BuiltinExceptionError(f"{where} cites no backend-agreement table (ADR-0057 §10)")
        if not entry.cites.startswith(AUDIT_CITATION_PREFIX):
            raise BuiltinExceptionError(
                f"{where} cites {entry.cites!r}, which is not under {AUDIT_CITATION_PREFIX}"
            )
        path = repo_root / entry.cites
        if not path.is_file():
            raise BuiltinExceptionError(f"{where} cites {entry.cites!r}, which does not exist")
        kind = declared_kind(path)
        if kind != BACKEND_AGREEMENT_KIND:
            raise BuiltinExceptionError(
                f"{where} cites {entry.cites!r}, whose kind is {kind!r}, not {BACKEND_AGREEMENT_KIND!r}"
            )


@dataclass(frozen=True)
class ArbitrationPolicy:
    """Precedence among producers plus the two declared levels.

    ``prefer`` orders BACKENDS; a backend not listed follows every listed
    one, and among unlisted backends the registry's incumbent-first order
    holds — so the empty tuple IS the built-in. ``prefer_by_attribute``
    replaces ``prefer`` for the named attribute.
    """

    prefer: Tuple[str, ...] = ()
    prefer_by_attribute: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    corroborated_confidence: float = CORROBORATED_CONFIDENCE
    superseded_stub_rank_factor: float = SUPERSEDED_STUB_RANK_FACTOR

    def order(
        self, analyzers: Iterable["RegisteredAnalyzer"], *, attribute: Optional[str] = None,
    ) -> List["RegisteredAnalyzer"]:
        """``analyzers`` in precedence order for ``attribute`` (or the general order)."""
        from .analyze.registry import incumbent_first

        prefer = self.prefer_by_attribute.get(attribute, self.prefer) if attribute else self.prefer
        rank = {backend: index for index, backend in enumerate(prefer)}
        # incumbent_first is the tiebreak among equally-ranked (unlisted) backends.
        return sorted(incumbent_first(analyzers), key=lambda a: rank.get(a.backend or "", len(rank)))

    def precedence(
        self, analyzers: Iterable["RegisteredAnalyzer"], *, attribute: Optional[str] = None,
    ) -> List[str]:
        """Producer NAMES in precedence order — what the merge pass consumes."""
        return [a.name for a in self.order(analyzers, attribute=attribute)]

    def precedence_by_attribute(self, analyzers: Iterable["RegisteredAnalyzer"]) -> Dict[str, List[str]]:
        """The per-attribute orders that differ from the general one, by name."""
        analyzers = list(analyzers)
        return {attribute: self.precedence(analyzers, attribute=attribute) for attribute in self.prefer_by_attribute}


#: The §5 built-in: incumbent first, levels 0.95 / 0.5, no exceptions.
BUILTIN_POLICY = ArbitrationPolicy()


def policy_from_config(config: "LayeredConfig") -> ArbitrationPolicy:
    """The policy a validated :class:`LayeredConfig` expresses; unset keys are the built-in."""
    return ArbitrationPolicy(
        prefer=tuple(config.merge_prefer),
        prefer_by_attribute={k: tuple(v) for k, v in config.merge_prefer_by_attribute.items()},
        corroborated_confidence=(
            CORROBORATED_CONFIDENCE if config.merge_corroborated_confidence is None
            else config.merge_corroborated_confidence
        ),
        superseded_stub_rank_factor=(
            SUPERSEDED_STUB_RANK_FACTOR if config.merge_superseded_stub_rank_factor is None
            else config.merge_superseded_stub_rank_factor
        ),
    )


def resolve_arbitration_policy(
    *, repo_root: Path, environ: Optional[Mapping[str, str]] = None,
) -> ArbitrationPolicy:
    """Load both ADR-0045 preference tiers and express them as a policy.

    Raises :class:`~.user_config.ConfigError` for a bad ``[merge]`` key,
    naming the file — the CLI turns that into exit 2 with the reason.
    """
    from .user_config import load_layered_config

    return policy_from_config(load_layered_config(repo_root=repo_root, environ=environ))
