# SPDX-License-Identifier: AGPL-3.0-or-later
"""URL-folding idiom substrate (WI-mugog, Phase A).

This package consolidates the family of "fold a composed URL down to a literal
route-skeleton" helpers that the HTTP-linker scanners (``linkers/http.py``)
previously inlined per language. Each YAML sibling file in this directory
declares one composition **idiom** (string interpolation, array join, printf
format, string concatenation, ...) and the languages whose route detector is
covered by that idiom. The Python engine functions (``fold_string_interpolation``,
``fold_array_join``, ...) are the actual implementations that the per-language
scanners call into.

Why a per-idiom YAML registry
-----------------------------
The original WI-luvaj / WI-sijoh / WI-rosan work added URL folding for one
language at a time, inlined in the JS/TS scanner (template literals) and the
Elm scanner (``String.join "/"``). Doing the same for Python f-strings, Go
``fmt.Sprintf``, Kotlin / Ruby / Scala interpolation, Java ``String.format``
and ``StringBuilder``, etc. would scale poorly — the matrix is ~5 idioms x
~10 languages, and the idioms are the durable structure (a Python f-string
folds the same way conceptually as a JS template literal; only the
placeholder regex differs). This package factors the matrix the right way:

* **Engines** (this module) implement the language-agnostic folding logic
  parameterised by a placeholder regex (string_interpolation) or a separator
  (array_join).
* **YAML files** (``string_interpolation.yaml``, ``array_join.yaml``, ...)
  declare which languages bind which engine with which parameters. New
  languages join by adding a YAML entry, not by writing new Python.
* **SCOPE.md** records languages intentionally left at literal-only
  extraction for the current phase — a deliberate scope decision rather than
  a silent gap.

The companion property test (``tests/test_url_folding.py``) asserts that
every active route-detector language is either covered by a YAML entry or
documented in SCOPE.md. Coverage gaps fail loud.

Phase A scope
-------------
Phase A is behaviour-preserving. It moves the two existing folders out of
``linkers/http.py`` into this package, declares them via YAML, and adds the
property test. No new languages are folded; no edge counts or confidences
shift on any existing fixture. The legacy ``_fold_template_literal`` and
``_fold_elm_string_join`` names in ``linkers/http.py`` are retained as thin
wrappers that delegate to the engines here, so existing tests and any
external imports keep working.

Phase B (string_interpolation expansion to Python f-strings, Kotlin, Ruby,
Scala, Swift), Phase C (printf_format idiom for Go fmt.Sprintf, Java
String.format, Python percent/.format), and Phase D (string_concat idiom)
ship under the parent invariant INV-miloj as sibling tracker items.

Note on dispatcher design
-------------------------
The WI-mugog task description sketches an ``_apply_url_folding_yaml(language,
content, module_consts)`` dispatcher that would replace the inline
regex-match-and-fold blocks in each scanner with a single YAML-driven call.
Phase A intentionally defers this dispatcher: with only two idioms x two
language groups today, the abstraction would have to handle Elm's two-pass
let-binding scan as a special case from day one, and the resulting design
would be premature. The per-scanner inline calls below are explicit and
local; the dispatcher returns when Phase B adds a second variant per idiom
and the abstraction earns its complexity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml


def fold_string_interpolation(
    template: str,
    consts: dict[str, str],
    placeholder_pattern: str,
) -> tuple[str, str]:
    """Fold an interpolation-style URL template into a literal route skeleton.

    Generalisation of the legacy ``_fold_template_literal`` from
    ``linkers/http.py``. The ``placeholder_pattern`` is a regex with one
    capture group that yields the slot's identifier. For JS/TS template
    literals (``${NAME}``) the pattern is ``r'\\$\\{([^}]+)\\}'``; for a
    hypothetical Python f-string adapter it would be ``r'\\{([^}!:]+)\\}'``,
    etc.

    Strategy (preserved bit-for-bit from the original implementation, so
    Phase A is behaviour-preserving):

    * Slots whose identifier is in ``consts`` substitute the const value.
    * Unresolved slots rewrite to ``{NAME}`` (so the downstream route-pattern
      matcher can equate them with ``:NAME`` / ``{NAME}`` / ``<NAME>`` route
      placeholders).
    * A leading ``{VAR}/`` is stripped as a host/base URL prefix (the JS/TS
      analogue of Elm's ``apiUrl ++ "/path"`` wrapper-module idiom).
    * An unresolved ``{VAR}`` preceded by ``/`` is a path-segment param and
      is retained.
    * An unresolved ``{VAR}`` NOT preceded by ``/`` is template continuation
      (may span segments or be a query-string tail). The URL is truncated
      there and the remaining literal prefix is used for route matching.
      Result is marked ``url_type="variable"`` so callers can opt into
      prefix-matching semantics.

    Returns ``(folded_url, url_type)`` where ``url_type`` is ``"literal"``
    when every slot resolved cleanly (no truncation) AND the URL has a
    leading ``/`` anchor, else ``"variable"``.
    """
    slot_re = re.compile(placeholder_pattern)

    def _sub(match: re.Match) -> str:
        name = match.group(1).strip()
        if name in consts:
            return consts[name]
        return "{" + name + "}"

    folded = slot_re.sub(_sub, template)

    host_prefix = re.match(r"^\{[^}]+\}(/.*)$", folded)
    if host_prefix is not None:
        folded = host_prefix.group(1)

    truncated = False
    trunc_match = re.search(r"(?<!/)\{[^}]+\}", folded)
    if trunc_match is not None:
        folded = folded[: trunc_match.start()]
        truncated = True

    if truncated or not folded.startswith("/"):
        url_type = "variable"
    else:
        url_type = "literal"
    return folded, url_type


def fold_array_join(
    items: list[tuple[str, bool]],
    separator: str,
) -> str | None:
    """Fold a list of pre-parsed URL segments into a path under ``separator``.

    Generalisation of the legacy ``_fold_elm_string_join`` from
    ``linkers/http.py``. ``items`` is a list of ``(value, is_literal)``
    tuples — the per-language scanner is responsible for parsing the call-site
    syntax into this shape, since the bracketing and identifier conventions
    are language-specific (Elm: ``"literal", ident, ident.field``; future
    Clojure / Lisp variants will differ). The engine itself is concerned only
    with the structural fold:

    * Drop the first item (assumed host/base URL variable, e.g. ``apiUrl``).
    * Literal items contribute their raw text.
    * Non-literal items contribute ``{value}`` so the downstream route-pattern
      matcher can equate them with route parameters.
    * Join the remainder with ``separator`` (Elm: ``"/"``).

    Returns the URL (``"/silence/{uuid}"``) or ``None`` when the list has
    fewer than two items (no path segments after the base URL stripping).
    """
    if len(items) < 2:
        return None
    segments: list[str] = []
    for value, is_literal in items[1:]:
        segments.append(value if is_literal else "{" + value + "}")
    return "/" + separator.join(segments)


# Engine registry: maps the ``engine:`` identifier in each YAML file to the
# Python callable that implements it. New idioms add an entry here alongside
# their YAML file. Used by ``load_url_folding_registry`` to resolve each
# variant's engine reference at load time.
ENGINES: dict[str, Callable] = {
    "fold_string_interpolation": fold_string_interpolation,
    "fold_array_join": fold_array_join,
}


@dataclass(frozen=True)
class UrlFoldingVariant:
    """One declared (idiom, language) pairing from the YAML registry.

    Attributes:
        idiom: Idiom identifier from the YAML's ``idiom:`` field (e.g.,
            ``"string_interpolation"``).
        engine: Engine identifier from the YAML's ``engine:`` field. Must
            be a key in ``ENGINES``.
        language: One language name (the YAML's ``languages:`` mapping is
            expanded so each language becomes its own variant record).
        config: Per-language config dict from the YAML — e.g.,
            ``{"placeholder_pattern": "..."}`` for string_interpolation,
            ``{"separator": "/"}`` for array_join. Engine callers pass the
            relevant fields through.
    """

    idiom: str
    engine: str
    language: str
    config: tuple[tuple[str, str], ...]

    def config_dict(self) -> dict[str, str]:
        """Return the config tuple as a dict for engine-call convenience."""
        return dict(self.config)


def _url_folding_dir() -> Path:
    """Return the path to this package directory (where the YAMLs live)."""
    return Path(__file__).parent


def load_url_folding_registry() -> list[UrlFoldingVariant]:
    """Load every ``*.yaml`` sibling and return one variant per (idiom, language).

    Each YAML file under ``url_folding/`` is expected to have the shape:

    .. code-block:: yaml

        idiom: <idiom_name>
        engine: <engine_name>      # must be a key in ENGINES
        languages:
          <language_name>:
            <config_key>: <config_value>
            ...

    Returns a flat list of ``UrlFoldingVariant`` records — one per
    (idiom, language) — so callers can filter by either dimension cheaply.
    """
    variants: list[UrlFoldingVariant] = []
    for yaml_path in sorted(_url_folding_dir().glob("*.yaml")):
        with open(yaml_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        idiom = data["idiom"]
        engine = data["engine"]
        languages = data.get("languages") or {}
        for lang_name, lang_config in languages.items():
            config_items = tuple(sorted((lang_config or {}).items()))
            variants.append(
                UrlFoldingVariant(
                    idiom=idiom,
                    engine=engine,
                    language=lang_name,
                    config=config_items,
                )
            )
    return variants


def get_covered_languages() -> set[str]:
    """Return the set of language names covered by at least one YAML variant."""
    return {v.language for v in load_url_folding_registry()}


_SCOPE_TABLE_ROW = re.compile(
    r"""^\|\s*`([a-z_][a-z0-9_]*)`\s*\|""",
    re.MULTILINE,
)


def get_scoped_languages() -> set[str]:
    """Parse ``SCOPE.md`` and return languages declared literal-only.

    Looks for table rows whose first cell is a backtick-quoted lowercase
    ASCII language identifier (the canonical names emitted by analyzers and
    used as YAML keys), so the property-test set-arithmetic works.
    """
    scope_path = _url_folding_dir() / "SCOPE.md"
    content = scope_path.read_text(encoding="utf-8")
    return set(_SCOPE_TABLE_ROW.findall(content))
