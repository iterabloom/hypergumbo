# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which declarations a name written in a file can refer to.

A framework registration names its handler as a string: a Django
``path("locale/set", views.LocaleSet)``, an Express ``router.get("/", list)``,
a Rails ``get "users", to: "users#index"``. After the analyzers have run, core
passes turn that string into a symbol: the route-handler linker
(``linkers/route_handler.py``) for route markers, and
``framework_patterns.resolve_deferred_symbol_refs`` / ``enrich_symbols`` for
``UsageContext`` records.

Each of them used to fill one table ``name -> symbol`` over every symbol of
every language, in the order the symbol list arrived in, last (or first)
write wins. Two things were wrong with that. The answer depended on the list
order, which is not a property of the source. And the table held symbols the
reference could not mean: on pretix the Django route ``^locale/set$`` was
answered with ``LocaleSet``, a class of a vendored JavaScript library, in the
runs where the JavaScript analyzer's symbols came after Python's.

The rule, stated once here for every such pass:

* **Host language.** A reference written in a file of language L names code
  of language L: the candidates are the symbols whose ``language`` is in the
  reference's host language set (:func:`candidate_languages`). JavaScript and
  TypeScript are one set, because one analyzer emits both and a ``.ts``
  router names a ``.js`` handler with no bridge between them. A reference
  with no known host language keeps every symbol.
* **A full name outranks a short one.** A reference equal to a candidate's
  ``qualified_name`` is that candidate; ``name`` answers only where no
  qualified name took the key (:func:`two_tier_name_table`).
* **A total order among equals.** Candidates are indexed in
  :func:`source_order_key` order (path, then span, then id) and the first
  holder of a key keeps it. The order is read off the source, so the choice
  is the same in every run; the keys with more than one holder are reported
  as ``collisions`` so a caller can flag the pick as one made by position.

A ``UsageContext`` carries no language of its own, only the path of the file
it was found in. :class:`CandidatePool` reads its host languages off that
file: the languages of the symbols declared in it, plus the language its
extension names (a Django ``urls.py`` declares nothing, and must still be a
Python file).

What this module does not decide: which symbols are candidates at all (a
caller drops what cannot be a handler, e.g. route markers), and what a caller
does with a tie (the linker takes the first in source order and lowers the
edge's confidence; the ``UsageContext`` resolver first asks the reference for
evidence: the module it names, its own file).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .taxonomy import JS_TS_LANGUAGES, get_language

if TYPE_CHECKING:
    from .ir import Symbol


def source_order_key(symbol: Symbol) -> tuple[str, int, int, int, int, str]:
    """The total order among candidates: path, then span, then id.

    Read off the source, so it is the same in every run whatever order the
    analyzers' results were collected in. The id closes the order for two
    symbols at one span.
    """
    span = symbol.span
    return (
        symbol.path or "",
        span.start_line if span is not None else 0,
        span.start_col if span is not None else 0,
        span.end_line if span is not None else 0,
        span.end_col if span is not None else 0,
        symbol.id,
    )


def candidate_languages(
    host_languages: Iterable[str | None],
) -> frozenset[str] | None:
    """The languages a reference's target can be written in, or ``None`` for any.

    ``host_languages`` are the languages of the code the reference is written
    in (one for a route marker; the languages of a file's declarations for a
    ``UsageContext``). JavaScript and TypeScript widen to both. No known host
    language means no restriction: the reference was not found in a host
    file (a marker fabricated from a protocol definition), and any language
    may implement it.
    """
    known = {language for language in host_languages if language}
    if not known:
        return None
    if known.intersection(JS_TS_LANGUAGES):
        known.update(JS_TS_LANGUAGES)
    return frozenset(known)


@dataclass(frozen=True)
class NameTiers:
    """The names one candidate set answers to, and which of them are ties.

    ``symbol_by_name`` maps each ``qualified_name`` and each ``name`` to the
    first candidate (in the order given) that holds it, a qualified name
    taking a key before any short name can. ``tied`` holds, for each key with
    two or more candidates at the tier that answers it, those candidates in
    the order given (``symbol_by_name[key]`` is the first of them).
    """

    symbol_by_name: dict[str, Symbol]
    tied: dict[str, tuple[Symbol, ...]]

    @property
    def collisions(self) -> frozenset[str]:
        """The keys whose answer was a pick among several holders."""
        return frozenset(self.tied)


def two_tier_name_table(candidates: Iterable[Symbol]) -> NameTiers:
    """Index ``candidates``, which must already be in :func:`source_order_key` order.

    First write wins, in two tiers: every candidate under its
    ``qualified_name``, then under its ``name`` where no qualified name took
    the key. So a reference equal to a full name is that declaration, a short
    name answers only when nothing is fully named so, and among equals the
    first in source order is the one kept. A symbol whose ``name`` and
    ``qualified_name`` are the same string is one candidate for that key, not
    two.
    """
    ordered = list(candidates)
    by_qualified: dict[str, list[Symbol]] = {}
    by_name: dict[str, list[Symbol]] = {}
    for s in ordered:
        # ADR-0032: read typed Symbol.qualified_name field
        if s.qualified_name:
            by_qualified.setdefault(s.qualified_name, []).append(s)
        if s.name != s.qualified_name:
            by_name.setdefault(s.name, []).append(s)
    symbol_by_name: dict[str, Symbol] = {
        key: holders[0] for key, holders in by_qualified.items()
    }
    for s in ordered:
        symbol_by_name.setdefault(s.name, s)
    tied = {
        key: tuple(holders) for key, holders in by_qualified.items()
        if len(holders) > 1
    }
    tied.update(
        (key, tuple(holders)) for key, holders in by_name.items()
        if len(holders) > 1 and key not in by_qualified
    )
    return NameTiers(symbol_by_name=symbol_by_name, tied=tied)


class CandidatePool:
    """One run's symbols, as the candidates of references written in its files.

    Sorted into :func:`source_order_key` order once, on first use, and
    filtered per candidate language set on demand: most runs resolve
    references from one or two language sets, and many from none.
    """

    def __init__(self, symbols: Iterable[Symbol]) -> None:
        self._symbols = list(symbols)
        self._ordered: list[Symbol] | None = None
        self._by_languages: dict[frozenset[str] | None, list[Symbol]] = {}
        self._languages_by_path: dict[str, set[str]] | None = None

    def host_languages(self, path: str) -> frozenset[str] | None:
        """The candidate languages of a reference written in the file ``path``.

        The languages of the symbols declared in that file, and the language
        its extension names; widened by :func:`candidate_languages`. ``None``
        when neither says anything: the reference then keeps every symbol.
        """
        if self._languages_by_path is None:
            self._languages_by_path = {}
            for s in self._symbols:
                if s.language and s.path:
                    self._languages_by_path.setdefault(s.path, set()).add(s.language)
        languages: set[str | None] = set(self._languages_by_path.get(path, ()))
        languages.add(get_language(Path(path)))
        return candidate_languages(languages)

    def candidates(self, languages: frozenset[str] | None) -> list[Symbol]:
        """The symbols whose ``language`` is in ``languages`` (every symbol for
        ``None``), in source order."""
        found = self._by_languages.get(languages)
        if found is None:
            if self._ordered is None:
                self._ordered = sorted(self._symbols, key=source_order_key)
            found = self._by_languages[languages] = [
                s for s in self._ordered
                if languages is None or s.language in languages
            ]
        return found
