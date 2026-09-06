# SPDX-License-Identifier: AGPL-3.0-or-later
"""The module-key axis: what may occupy a module slot (ADR-0051).

THE AXIOM:

    The module key names the STATIC OWNER PATH of the called symbol -- the
    namespace or type in which it is DEFINED, spelled in the source
    language's import vocabulary. It is not a property of the CALL SITE:
    not the receiver's variable name, not a set of candidates, and not a
    marker for the absence of an answer.

"The module key" is one fact with two homes by design -- ``IoPrimitive.module``
on the catalogue side and ``ExternalRef.module_path`` on the edge side -- and
``io_boundary._module_matches`` is the predicate that pairs them. Everything
here governs both halves.

WHY A TYPE IS CONFORMANT AND A RECEIVER VARIABLE IS NOT, which is the
distinction the axiom exists to draw and the one an orthographic heuristic
cannot. The catalogues are FULL of types -- ``net.Conn``, ``std::fs::File``,
``java.sql.Connection``, ``pathlib.Path`` -- and that is deliberate: a
method-shaped primitive needs its owning type to be addressable at all, and
``IoPrimitive.module``'s own docstring says "the module or class path". A type
names where the symbol is DEFINED, so it is an owner path. A receiver VARIABLE
names a local binding at one call site; ``resp`` is not where ``read`` is
defined. That is the same cut ADR-0023 made for ``Edge.edge_type`` -- properties
of an endpoint are queried from the endpoint, not smuggled into the label -- and
the receiver's type already has a home in ``Edge.meta["receiver_type_hint"]``
(stamped by six analyzers, read by neither ``io_boundary`` nor ``taint``;
WI-monul).

WHY THIS IS A STRUCTURAL-POLICY AXIS RATHER THAN A REGISTRY OF VALUES. Module
names cannot be enumerated -- every package on every index is a legal value --
so there is no ``MODULE_KEYS`` tuple to check membership against. What IS
enumerable, and what this module declares, is the set of NOTIONS a slot may
carry. This is the lighter shape ADR-0024 section 4's "use judgment" carveout
permits and that :mod:`hypergumbo_core.qualified_name_axis` already uses; the
sibling io-boundary axis (ADR-0050) takes the heavyweight enumerable shape
instead, and the two differ for this reason rather than by accident.

WHAT WENT WRONG WITHOUT AN AXIOM. ``ExternalRef.module_path`` did not merely
lack a declaration -- it carried a FALSE one:

    module_path: str  # axis: free-text -- module import path in
    source-language grammar; consumers display/lookup, never branch on
    the value itself.

``_module_matches`` branches on the value itself. It decides type-vs-sub-package
from ORTHOGRAPHY (``longer_raw[shared][:1].isupper()``), on a rule its own
docstring justifies by GO's naming convention while serving all fifteen
catalogues -- so the discriminator is information-free wherever module names are
capitalised (haskell 100%, swift 97%, objc 95%, elixir 52%, javascript 21%). The
static linter accepted the declaration because a ``free-text`` justification is
required to be PRESENT, not TRUE.

The measured consequence is roughly eighteen separately-filed tracker items that
reduce to this one conflation -- INV-linub, INV-zuvib, INV-hahak, INV-fofoj,
INV-januj, INV-kotob, INV-mumov, INV-safig, INV-fokik, INV-funuf, INV-zimud,
INV-papih, INV-dijor, WI-zazul, WI-damir, WI-sugom, WI-gudak, WI-papar,
WI-kamin, WI-monul. They file as separate analyzer bugs because no document said
what the field was for, so each mismatch read as a local defect. Two of them
turned out to be ONE defect in two languages (INV-fofoj's java half IS
INV-januj), which is the signature of a missing axis rather than of unrelated
bugs. Over 65,187 external refs on a 21-repo, 10-language cold cohort, 7.8% of
slots are not a single module identity.

The producer surface says the same thing in a different way: 54 ``ExternalRef``
construction sites across 20 analyzers, and the local feeding ``module_path`` is
variously ``path_hint``, ``module_hint``, ``module_name``, ``mod``, ``hint``,
``ns``, ``pkg``, ``receiver_name``, ``wildcard_module`` and the literal
``"redirect"``. No two analyzers call the thing the same name.

SCOPE: THIS DECLARES, IT DOES NOT MIGRATE. Nothing here changes what any
analyzer emits. ``external_symbol`` node ids embed the module slot
(``rust:external:0-0:File::open``), so normalising the slot's CONTENT changes
emitted ids and needs a stable_id scheme bump, which WI-talos DECISION 3 gates
behind v9/v10 and two releases. That is ADR-0024 step 7 and is WI-marok.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


#: The axiom, as one sentence, so a consumer can quote it without
#: re-deriving it from prose. Cited by ADR-0051 and by WI-virav's
#: annotation sweep over the ~18-item pile.
MODULE_KEY_AXIOM: Final[str] = (
    "The module key names the STATIC OWNER PATH of the called symbol -- the "
    "namespace or type in which it is DEFINED, spelled in the source "
    "language's import vocabulary. It is not a property of the CALL SITE: not "
    "the receiver's variable name, not a set of candidates, and not a marker "
    "for the absence of an answer."
)


AXIS_OWNER_PATH: Final[str] = "owner_path"
AXIS_CALL_SITE_PROPERTY: Final[str] = "call_site_property"
AXIS_UNCERTAINTY: Final[str] = "uncertainty"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_OWNER_PATH,
    AXIS_CALL_SITE_PROPERTY,
    AXIS_UNCERTAINTY,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class EmissionSite:
    """A producer site that motivated a notion, cited by file and line.

    Checked against the tree by
    ``test_every_cited_emission_site_still_exists``: the file must exist and
    the cited line must still contain ``anchor``. A citation that rots is
    worse than no citation, because it sends the next reader to a line that
    now says something else.
    """

    path: str
    line: int
    anchor: str
    note: str


@dataclass(frozen=True)
class ModuleKeyNotion:
    """One kind of thing a module slot can carry.

    Axiom-conformance is NOT stored here. It is derived from ``axis`` by
    :func:`is_axiom_conformant`, because a stored flag beside the section
    would be one fact in two homes -- the exact shape this axis exists to
    remove.
    """

    name: str
    axis: str
    description: str
    emission_sites: tuple[EmissionSite, ...] = field(default_factory=tuple)


MODULE_KEY_NOTIONS: Final[tuple[ModuleKeyNotion, ...]] = (
    # ------------------------------------------------------------------
    # AXIS_OWNER_PATH -- the axiom's canonical section.
    # ------------------------------------------------------------------
    ModuleKeyNotion(
        name="namespace",
        axis=AXIS_OWNER_PATH,
        description=(
            "A package, module or import path: os, java.io, net/http, "
            "std::fs, node:fs/promises, ./relative/module. The value that "
            "would appear in a clean import statement -- NOT the in-scope "
            "alias, which is a property of the importing file. C and C++ "
            "header names (<string.h>) are namespaces in this sense, and so "
            "are JS/TS relative paths: 2.7% of shipped refs, legitimate."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/swift.py"
                ),
                line=1855,
                anchor="module_path=path_hint",
                note=(
                    "The CORRECT shape, kept as the contrast case: the hint "
                    "is an import alias alone, and the ExternalRef is "
                    "withheld entirely when it is absent rather than being "
                    "filled with something else."
                ),
            ),
        ),
    ),
    ModuleKeyNotion(
        name="type",
        axis=AXIS_OWNER_PATH,
        description=(
            "The class or type that OWNS a method-shaped primitive: net.Conn, "
            "std::fs::File, java.sql.Connection, pathlib.Path. Conformant "
            "because a type names where the symbol is DEFINED. This is not a "
            "concession -- a method primitive is unaddressable without it, and "
            "IoPrimitive.module's own docstring says 'the module or class "
            "path'. It is also what makes the capitalisation heuristic in "
            "_module_matches necessary: that predicate is trying to recover "
            "whether an extra component is a type (same owner) or a "
            "sub-package (different owner), which the axis says should be "
            "DECLARED rather than inferred from spelling."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/objc.py"
                ),
                line=845,
                anchor="_module: str | None = receiver_name",
                note=(
                    "Reads as a receiver-variable site and is not: it is "
                    "gated on receiver_name[0].isupper(), and a capitalised "
                    "Objective-C message receiver is normally a CLASS name. "
                    "The 2026-09-01 audit recorded this as objc writing a "
                    "receiver variable; that attribution was wrong and the "
                    "lowercase identifiers it counted came from swift."
                ),
            ),
        ),
    ),
    # ------------------------------------------------------------------
    # AXIS_CALL_SITE_PROPERTY -- names the call site, not the definition.
    # ------------------------------------------------------------------
    ModuleKeyNotion(
        name="receiver_variable",
        axis=AXIS_CALL_SITE_PROPERTY,
        description=(
            "The spelling of the receiver at ONE call site -- resp, session, "
            "request, fileIO. Non-conformant: a local binding is not where "
            "the callee is defined, and the same primitive reached through a "
            "differently-named variable gets a different key. Its correct "
            "home is Edge.meta on the call site, where the receiver's TYPE is "
            "already stamped as receiver_type_hint. _module_matches carries "
            "an explicit Swift carve-out for this shape (catalogue name ends "
            "with hint, never the reverse), which is the tell: the predicate "
            "has a special case whose only purpose is to tolerate a "
            "non-conformant value."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/swift.py"
                ),
                line=1784,
                anchor="_module = _external_type or import_aliases.get(callee_name)",
                note=(
                    "WAS the conflation in its purest form, and the single "
                    "best evidence for this axis: until PR #757 one fallback "
                    "chain (gate_path_hint) yielded a TYPE (receiver_type), "
                    "then a NAMESPACE (import alias), then a receiver "
                    "VARIABLE (receiver_hint), then a SENTINEL ('external') "
                    "-- four notions into one slot with no record of which "
                    "one produced it. INV-kotob (satisfied 2026-09-04) cut "
                    "the variable clause: the slot now carries an external "
                    "TYPE or an import alias, else the sentinel, and a "
                    "project type rides in receiver_type_hint only. The site "
                    "is kept as the historical anchor of the notion."
                ),
            ),
        ),
    ),
    # ------------------------------------------------------------------
    # AXIS_UNCERTAINTY -- honest admissions, not identities.
    # ------------------------------------------------------------------
    ModuleKeyNotion(
        name="disjunction",
        axis=AXIS_UNCERTAINTY,
        description=(
            "A comma-joined SET of candidate modules -- cpp joins every "
            "#include in the file, because a call in that unit could come "
            "from any of them. 6.2% of shipped refs. Non-conformant as an "
            "IDENTITY while being the honest answer to the question: the "
            "analyzer genuinely does not know which. Already handled "
            "downstream by two deliberately different quantifiers -- "
            "_module_hint_candidates asks ANY (INV-funuf) and "
            "module_hint_disjuncts asks ALL (INV-zimud) -- which is why the "
            "shape wants its own field rather than removal."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/cpp.py"
                ),
                line=1404,
                anchor='module_hint = ",".join(system_includes)',
                note="The file's entire #include set joined into one slot.",
            ),
        ),
    ),
    ModuleKeyNotion(
        name="sentinel",
        axis=AXIS_UNCERTAINTY,
        description=(
            "A fixed marker standing for 'no module identity was recovered': "
            "'external' (1.5% of refs) and bash's 'redirect' (0.1%, whose "
            "name slot is '>' -- not a call at all). Non-conformant as an "
            "identity, and load-bearing as an admission: io_boundary and "
            "taint share _UNRESOLVED_MODULE_PLACEHOLDERS_IO precisely so the "
            "two cannot drift about what 'no module' looks like."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/bash.py"
                ),
                line=1399,
                anchor='module_path="redirect"',
                note=(
                    "A shell redirection is not a call; the name slot holds "
                    "the operator ('>')."
                ),
            ),
        ),
    ),
    # ------------------------------------------------------------------
    # AXIS_PENDING -- genuinely contested; ruled by audit, not here.
    # ------------------------------------------------------------------
    ModuleKeyNotion(
        name="global_object",
        axis=AXIS_PENDING,
        description=(
            "An ambient runtime object used as the owner path: process, "
            "window, document, navigator, console, localStorage. DELIBERATELY "
            "UNRULED. The case FOR conformance is real -- you do not import "
            "`process` in node, so the global's name IS how JS's vocabulary "
            "spells that owner path, and js_ts.py maps each to itself in the "
            "import map for exactly that reason. The case AGAINST is that "
            "these name a VALUE rather than a definition site, which is the "
            "receiver-variable objection one step up. Ruling this in the "
            "declaration would be the undisciplined move the axis exists to "
            "prevent; it is the first candidate for a per-value audit under "
            "ADR-0024's family-audit methodology, and NO ROW MOVES on this "
            "note."
        ),
        emission_sites=(
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/js_ts.py"
                ),
                line=6224,
                anchor='"process": "process",',
                note=(
                    "The identity mapping that puts a global's own name into "
                    "the import map, and thence into the module slot."
                ),
            ),
            EmissionSite(
                path=(
                    "packages/hypergumbo-lang-mainstream/src/"
                    "hypergumbo_lang_mainstream/js_ts.py"
                ),
                line=799,
                anchor="JS_KNOWN_GLOBALS",
                note="The nine globals treated as addressable owners.",
            ),
        ),
    ),
)


def all_module_key_notions() -> frozenset[str]:
    """Return every declared notion name.

    This is the callable wired into
    :func:`hypergumbo_core.multi_value_field_axis._known_axes` under the
    ``module-key`` key. As with the ``qualified-name`` axis, the returned set
    is the axis's NOTIONS rather than its legal field values -- the field's
    values are unenumerable -- and the validator uses it as the
    axis-is-wired check, since ``_check_field`` tests the declared axis NAME
    for membership and never the field's values.
    """
    return frozenset(notion.name for notion in MODULE_KEY_NOTIONS)


def notions_on_axis(axis: str) -> tuple[ModuleKeyNotion, ...]:
    """Return every notion whose axis equals *axis*."""
    return tuple(n for n in MODULE_KEY_NOTIONS if n.axis == axis)


def find_module_key_notion(name: str) -> ModuleKeyNotion | None:
    """Look up a notion by name; None if not declared."""
    for notion in MODULE_KEY_NOTIONS:
        if notion.name == name:
            return notion
    return None


def is_axiom_conformant(name: str) -> bool:
    """Does *name* satisfy :data:`MODULE_KEY_AXIOM`?

    DERIVED FROM THE SECTION rather than stored per-notion, so the two can
    never disagree. An unknown name is False: a notion nobody declared cannot
    have been argued to satisfy the axiom, and returning True by default is
    the direction that manufactures a false all-clear.

    ``pending_classification`` is False for the same reason -- it means "not
    yet argued", not "argued and accepted". A consumer counting conformant
    slots must not silently absorb ``global_object`` while its audit is
    outstanding.
    """
    notion = find_module_key_notion(name)
    return notion is not None and notion.axis == AXIS_OWNER_PATH
