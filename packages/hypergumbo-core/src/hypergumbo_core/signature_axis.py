# SPDX-License-Identifier: AGPL-3.0-or-later
"""The callable-signature axis: what ``Symbol.signature`` is for (ADR-0058).

THE AXIOM:

    ``Symbol.signature`` preserves the DECLARATION SURFACE of a callable,
    verbatim, in the source language's own grammar, for DISPLAY. Every fact
    inside it -- return type, parameter arity, a field's declared type -- is
    a property of the symbol and is read from its declared home, never
    parsed back out of this string.

WHY THIS AXIS EXISTS. The field did not merely lack a declaration; it carried
a FALSE one, for long enough that nine consumers grew underneath it::

    signature: Optional[str] = None  # axis: free-text -- callable signature
    string in source-language grammar; consumers display, never branch on
    the value itself.

Nine shipped consumers branch on the value itself (:data:`LEGACY_VALUE_PARSERS`
enumerates them): six parse a return type across five languages, two count
parameters for overload selection, one reads a field's declared type. The
static linter accepted the declaration because a ``free-text`` justification is
required to be PRESENT, not TRUE (ADR-0051; ADR-0033 section 1) -- the same
mechanism, one field over. ADR-0024 defines ``free-text`` as an open-ended
payload NO CONSUMER BRANCHES ON, so the CATEGORY was wrong rather than the
comment, and rewording the justification to admit branching would have left a
declaration contradicting its own category. ADR-0051 answered the identical
question by retiring the axis and declaring a real one; this does the same.

ADR-0024 anticipated this failure by name when it made the justification
mandatory: ``free-text`` is "the only category whose 'this is the right call'
claim isn't anchored elsewhere ... so it would otherwise be the natural
can-kicker". A false justification is the scheme's designed-for failure mode,
not a surprise.

THE SLOT CARRIES TWO NOTIONS, which is the measurable half. Over 52,983 symbols
from a survey of this repository, 38,573 carry a signature: 36,662 are a
callable's declaration surface, and 1,911 (4.95%) are a bare value type on a
symbol that is not callable at all -- 1,886 fields and 25 variables, 1,817 of
them Python. ``int``, ``list[Edge]``, ``'Mapping[str, str]'``, ``&'static str``.
A field has no signature; what it has is a declared type, and that fact has a
home (:data:`FACT_HOMES`). The producer-side dispatcher says so itself --
``symbol_introspection`` "does not extend signature/docstring to non-callable
Symbols (vars, type aliases, fields)" -- and Python, which is not one of that
module's ten languages, does it anyway.

WHY THIS IS A STRUCTURAL-POLICY AXIS RATHER THAN A REGISTRY OF VALUES.
Signature strings cannot be enumerated: every callable in every grammar is a
legal value. What IS enumerable, and what this module declares, is the set of
NOTIONS a slot may carry -- the lighter shape ADR-0024 section 4's "use
judgment" carveout permits, already used by :mod:`qualified_name_axis` and
:mod:`module_key_axis`.

THE FACTS INSIDE HAVE HOMES, AND THIS IS THE ACTIONABLE HALF. A return type
lives in ``FileAnalysis.method_return_types`` -- the language-neutral registry
that go, rust, swift and objc populate in Pass 1 and that the base analyzer
aggregates across files into ``_method_return_type_registry`` -- or in the
registered ``return_type`` / ``inferred_return_type`` meta keys, which java,
luau and apex populate. A field's declared type lives in
``FileAnalysis.class_field_types`` (csharp, cpp). Parameters live in the
``parameters`` / ``params`` meta keys. Python populates NONE of them and parses
the display string instead, and ``py.py`` cites the home in a comment while
doing so::

    #: THE CONCEPT'S HOME IS ``FileAnalysis.method_return_types``
    #: (INV-dihos / WI-kuroj), the language-neutral return-type registry Go
    #: and Rust already populate from parsed signatures.

That is ADR-0023's cut, which ADR-0051 section 2 reuses: properties of an
endpoint are queried from the endpoint, not smuggled into the label. It is also
ADR-0024's Fold-residue rule 1 verbatim -- "if a property is queryable from
``src`` or ``dst``, query the endpoint instead" -- and WI-lalot's own argument
in ``py.py``: "Deriving beats enumerating".

SCOPE: THIS DECLARES AND CLOSES; IT DOES NOT MIGRATE. Nothing any analyzer
emits changes, and the nine existing parsers keep working -- ADR-0051 left
``_module_matches`` branching on orthography for the same reason, filing the
replacement as its own row. What this adds is the closure: the nine are
GRANDFATHERED and enumerated, and :func:`find_undeclared_value_parsers` fails
on a tenth. That is the owner's 2026-09-21 ruling ("A+no"): fix the category,
stop widening, port the producers language by language. Migrating them is
ADR-0024 step 7.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final


#: The axiom, as one sentence, so a consumer can quote it without
#: re-deriving it from prose.
SIGNATURE_AXIOM: Final[str] = (
    "Symbol.signature preserves the DECLARATION SURFACE of a callable, "
    "verbatim, in the source language's own grammar, for DISPLAY. Every fact "
    "inside it -- return type, parameter arity, a field's declared type -- is "
    "a property of the symbol and is read from its declared home, never "
    "parsed back out of this string."
)


AXIS_DECLARATION_SURFACE: Final[str] = "declaration_surface"
AXIS_FOREIGN_FACT: Final[str] = "foreign_fact"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_DECLARATION_SURFACE,
    AXIS_FOREIGN_FACT,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class CitedSite:
    """A source location cited by this module, checked against the tree.

    ``test_every_..._citation_still_says_what_it_claimed`` asserts the file
    exists and that ``line`` still contains ``anchor``. A citation that rots
    is worse than no citation: it sends the next reader to a line that now
    means something else (ADR-0051 section 5).
    """

    path: str
    line: int
    anchor: str
    note: str


@dataclass(frozen=True)
class FactHome:
    """Where a fact inside a signature is DECLARED, as opposed to parsed."""

    path: str
    line: int
    anchor: str
    home: str
    populated_by: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class LegacyValueParser(CitedSite):
    """One grandfathered consumer that parses the signature's value.

    ``fact`` names which entry of :data:`FACT_HOMES` it is going after, so
    the migration has a per-site target rather than a general intention.
    """

    fact: str = ""


@dataclass(frozen=True)
class SignatureNotion:
    """One kind of thing a signature slot can carry.

    Axiom-conformance is NOT stored here. It is derived from ``axis`` by
    :func:`is_axiom_conformant`, because a stored flag beside the section
    would be one fact in two homes -- the shape this axis exists to remove.
    """

    name: str
    axis: str
    description: str
    emission_sites: tuple[CitedSite, ...] = field(default_factory=tuple)


SIGNATURE_NOTIONS: Final[tuple[SignatureNotion, ...]] = (
    SignatureNotion(
        name="callable_surface",
        axis=AXIS_DECLARATION_SURFACE,
        description=(
            "The parameter-and-return surface of a callable as its own "
            "grammar spells it: '(self) -> int', 'func(a string) error', "
            "'fn new(cfg: &Config) -> Self'. 36,662 of 38,573 populated "
            "slots. Conformant: it is the declaration, preserved for a "
            "reader, and it is the only notion the field's name describes."
        ),
        emission_sites=(
            CitedSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/symbol_introspection.py"
                ),
                line=35,
                anchor="``Symbol.signature`` was historically extracted",
                note=(
                    "The centralised producer for the ten languages that "
                    "have an extractor. Its own docstring draws this axis's "
                    "line: it 'does not extend signature/docstring to "
                    "non-callable Symbols (vars, type aliases, fields)'."
                ),
            ),
        ),
    ),
    SignatureNotion(
        name="value_type",
        axis=AXIS_FOREIGN_FACT,
        description=(
            "The declared type of a symbol that is NOT callable -- a field "
            "or a variable: 'int', 'list[Edge]', \"'Mapping[str, str]'\", "
            "\"&'static str\". 1,911 of 38,573 populated slots (4.95%): "
            "1,886 fields and 25 variables; python 1,817, rust 49, "
            "typescript 35, and single digits in java, swift, csharp, go, "
            "solidity. Non-conformant: a field has no signature, so the "
            "slot is answering a question the field did not ask. The fact "
            "is real and worth having -- its home is "
            "FileAnalysis.class_field_types, which csharp and cpp populate."
        ),
        emission_sites=(
            CitedSite(
                path=(
                    "packages/hypergumbo-core/src/hypergumbo_core/"
                    "linkers/method_call_recovery.py"
                ),
                line=198,
                anchor="HOLDS BOTH A VALUE TYPE AND A CALL SIGNATURE",
                note=(
                    "The consumer that found the conflation, and the reason "
                    "INV-lotoh was filed: it discriminates the two notions "
                    "by testing for a parenthesis, because nothing declares "
                    "which one a slot holds. Python-safe, not "
                    "language-general -- rust's 'flume::Receiver<()>' is a "
                    "value type WITH a parenthesis (1 slot of 1,887). It "
                    "under-refuses there, which is the safe direction."
                ),
            ),
        ),
    ),
)


#: Where each fact inside a signature is DECLARED. This is the table that
#: answers "then where do I read it instead?", and the reason the answer is
#: not "nowhere" -- every one of these already ships, populated by at least
#: two languages. Python populates none of them (WI-<port row>).
FACT_HOMES: Final[dict[str, FactHome]] = {
    "return_type": FactHome(
        path="packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
        line=227,
        anchor="method_return_types: dict[str, str]",
        home="FileAnalysis.method_return_types",
        populated_by=("go", "rust", "swift", "objc"),
        note=(
            "INV-dihos / WI-kuroj. Aggregated across files by the base "
            "analyzer into _method_return_type_registry, with library rows "
            "joined AFTER the analysed ones so a declaration in the "
            "repository always beats a catalogue guess (WI-lalot). The "
            "registered meta keys return_type / inferred_return_type "
            "(axis_meta_keys.py:1082, :1085) are the per-Symbol half, "
            "populated by java, luau and apex; inferred_return_type's own "
            "description names Python as an intended producer."
        ),
    ),
    "parameter_arity": FactHome(
        path="packages/hypergumbo-core/src/hypergumbo_core/axis_meta_keys.py",
        line=1073,
        anchor='MetaKeySpec("parameters"',
        home='Symbol.meta["parameters"] / Symbol.meta["params"]',
        populated_by=("15 analyzers, including py.py",),
        note=(
            "A structured list has a length; a rendered string has to be "
            "re-parsed to get one, and the two consumers that count "
            "parameters do exactly that. 'params' is the name-only short "
            "form for analyzers that do not extract types. This is the "
            "best-supplied of the three homes -- 15 analyzers populate it "
            "(apex, fennel, fortran, hack, janet, jsonnet, luau, odin, "
            "pony, puppet, py, scala, scss, thrift, zig), so unlike the "
            "return-type and field-type registries, PYTHON ALREADY WRITES "
            "HERE. The arity parsers are csharp and jackson_dispatch."
        ),
    ),
    "value_type": FactHome(
        path="packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
        line=189,
        anchor="class_field_types: dict[str, dict[str, str]]",
        home="FileAnalysis.class_field_types",
        populated_by=("csharp", "cpp"),
        note=(
            "Aggregated into _field_type_registry beside the return-type "
            "one, first writer wins. This is the home for the 1,911 slots "
            "the value_type notion describes."
        ),
    ),
}


#: The nine consumers that parse the value, GRANDFATHERED by the owner's
#: 2026-09-21 ruling. The list is CLOSED: a tenth fails
#: :func:`find_undeclared_value_parsers`, which is the point -- adding one is
#: a decision, and this is where it gets made rather than noticed afterwards.
LEGACY_VALUE_PARSERS: Final[tuple[LegacyValueParser, ...]] = (
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/csharp.py"
        ),
        line=442,
        anchor="_extract_csharp_return_type_name(resolved_sym.signature)",
        fact="return_type",
        note="Resolves a class from the parsed return type.",
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/csharp.py"
        ),
        line=1650,
        anchor="_count_signature_params(c.signature) == arg_count",
        fact="parameter_arity",
        note="Overload selection by counting rendered parameters.",
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/py.py"
        ),
        line=5881,
        anchor="assigned_class.signature",
        fact="return_type",
        note=(
            "Seeds var_types from a callee's return annotation. The largest "
            "of the nine by consequence: it is how a Python variable gets a "
            "type at all, so it feeds receiver_type_hint and therefore the "
            "method-call-recovery linker. py.py:641 cites the declared home "
            "in a comment 5,240 lines above this line."
        ),
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/kotlin.py"
        ),
        line=1587,
        anchor="resolved_nav_sym.signature",
        fact="return_type",
        note="Navigation-target return type.",
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/kotlin.py"
        ),
        line=1724,
        anchor="resolved_simple_sym.signature",
        fact="return_type",
        note="Simple-name receiver return type.",
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-lang-mainstream/src/"
            "hypergumbo_lang_mainstream/js_ts.py"
        ),
        line=5309,
        anchor="callee.signature",
        fact="return_type",
        note="Chained-call receiver typing.",
    ),
    LegacyValueParser(
        path="packages/hypergumbo-lang-common/src/hypergumbo_lang_common/dart.py",
        line=817,
        anchor="resolved_sym.signature",
        fact="return_type",
        note="Chained-call receiver typing.",
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-core/src/hypergumbo_core/"
            "linkers/jackson_dispatch.py"
        ),
        line=295,
        anchor='getattr(sym, "signature", None)',
        fact="parameter_arity",
        note=(
            "Reached through getattr rather than attribute access, so a "
            "plain attribute walk cannot see it -- the blind spot INV-lafid "
            "records for the io-boundary drift linter. "
            ":func:`find_undeclared_value_parsers` matches the getattr form "
            "for exactly this reason."
        ),
    ),
    LegacyValueParser(
        path=(
            "packages/hypergumbo-core/src/hypergumbo_core/"
            "linkers/method_call_recovery.py"
        ),
        line=209,
        anchor='signature = (member.signature or "").strip()',
        fact="value_type",
        note=(
            "The ninth, added deliberately in PR #1113 (WI-fihun cure 2) on "
            "the reading that free-text has no value check. It is what "
            "surfaced INV-lotoh, and it is the last one: the ruling that "
            "closed that row closed this list with it."
        ),
    ),
)


def all_signature_notions() -> frozenset[str]:
    """Return every declared notion name.

    This is the callable wired into
    :func:`hypergumbo_core.multi_value_field_axis._known_axes` under the
    ``callable-signature`` key. As with ``qualified-name`` and
    ``module-key``, the returned set is the axis's NOTIONS rather than its
    legal field values -- signature strings are unenumerable -- and the
    validator uses it as the axis-is-wired check, since ``_check_field``
    tests the declared axis NAME for membership and never the field's
    values.
    """
    return frozenset(notion.name for notion in SIGNATURE_NOTIONS)


def notions_on_axis(axis: str) -> tuple[SignatureNotion, ...]:
    """Return every notion whose axis equals *axis*."""
    return tuple(n for n in SIGNATURE_NOTIONS if n.axis == axis)


def find_signature_notion(name: str) -> SignatureNotion | None:
    """Look up a notion by name; None if not declared."""
    for notion in SIGNATURE_NOTIONS:
        if notion.name == name:
            return notion
    return None


def home_for_fact(fact: str) -> FactHome | None:
    """Where *fact* is declared, or None if it is not one we have placed."""
    return FACT_HOMES.get(fact)


def is_axiom_conformant(name: str) -> bool:
    """Does *name* satisfy :data:`SIGNATURE_AXIOM`?

    DERIVED FROM THE SECTION rather than stored per-notion, so the two can
    never disagree. An unknown name is False: a notion nobody declared
    cannot have been argued to satisfy the axiom, and returning True by
    default is the direction that manufactures a false all-clear.
    """
    notion = find_signature_notion(name)
    return notion is not None and notion.axis == AXIS_DECLARATION_SURFACE


# ---------------------------------------------------------------------------
# The closure gate.
# ---------------------------------------------------------------------------

_SEARCH_GLOB = "packages/*/src/**/*.py"
_EXCLUDED = ("/tests/", "/test_")


def _declared_sites() -> frozenset[tuple[str, int]]:
    return frozenset((s.path, s.line) for s in LEGACY_VALUE_PARSERS)


class _ParseSiteVisitor(ast.NodeVisitor):
    """Find reads of ``.signature`` whose VALUE is consumed.

    A read is a PARSE when the value flows into a call -- as an argument, or
    as the receiver of a method call on it (``sig.split('->')``). It is
    DISPLAY when the value is only stored, returned, serialised or
    truth-tested, which is what the axiom permits:
    ``{s.id: s.signature for s in symbols if s.signature}`` reads it twice
    and parses neither.

    ``getattr(x, "signature", ...)`` counts unconditionally. The indirection
    is precisely what a static check cannot follow, so the honest posture is
    to make it declare itself rather than to let it through.

    TWO STATED LIMITS, neither papered over:

    * A value bound to a LOCAL is beyond reach -- ``sig = sym.signature``
      then ``sig.split('->')`` is caught at the first line (a read handed to
      nothing is still flagged only if it flows into a call), and a parser's
      own body operating on its parameter is not an attribute read at all.
      The gate catches the HANDOFF, which is what the citations record.
    * A keyword argument named ``signature`` is treated as a carry, so a
      parser called as ``_extract(signature=sym.signature)`` is invisible to
      this gate. That form does not occur in the tree today; it is a known
      hole rather than a covered case, and it is written down here so the
      next reader does not mistake silence for coverage.
    """

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "signature"
        ):
            self.hits.append(node.lineno)
        for arg in node.args:
            self._flag_signature_reads(arg)
        for kw in node.keywords:
            # ``signature=<expr>.signature`` CARRIES the value into an
            # identically-named slot -- a copy, not a read of what is in it.
            # The SCIP round-trip does this at translate.py:164. See the
            # stated limit in this class's docstring.
            if kw.arg == "signature":
                continue
            self._flag_signature_reads(kw.value)
        if isinstance(node.func, ast.Attribute):
            self._flag_signature_reads(node.func.value)
        self.generic_visit(node)

    def _flag_signature_reads(self, node: ast.expr) -> None:
        """Flag ``.signature`` reads inside *node*, through ``or`` chains."""
        if isinstance(node, ast.Attribute) and node.attr == "signature":
            if isinstance(node.ctx, ast.Load):
                self.hits.append(node.lineno)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                self._flag_signature_reads(value)


def find_undeclared_value_parsers(
    repo_root: Path,
) -> list[tuple[str, int, str]]:
    """Return ``(path, line, source)`` for every undeclared parse site.

    Scope is ``packages/*/src`` — production code only. Tests read the field
    freely and are not consumers in the sense the axiom governs.

    A file that does not parse is SKIPPED rather than fatal: this gate's job
    is to catch a new consumer, and a syntax error is already caught louder
    somewhere else.
    """
    declared = _declared_sites()
    found: list[tuple[str, int, str]] = []
    for path in sorted(repo_root.glob(_SEARCH_GLOB)):
        rel = path.relative_to(repo_root).as_posix()
        if any(marker in f"/{rel}" for marker in _EXCLUDED):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visitor = _ParseSiteVisitor()
        visitor.visit(tree)
        lines = text.splitlines()
        for lineno in sorted(set(visitor.hits)):
            if (rel, lineno) in declared:
                continue
            found.append((rel, lineno, lines[lineno - 1].strip()))
    return found
