# SPDX-License-Identifier: AGPL-3.0-or-later
"""A repo's OWN function must not be reported as a stdlib I/O primitive (INV-sapit).

THE HAZARD. A file defining ``func MkdirAll(...)`` — with no import of ``os``
anywhere — must not be given an ``fs_write`` boundary naming ``os.MkdirAll``. A
local ``Command(...)`` reported as ``os/exec.Command`` would land under the
``subprocess`` boundary, the one category ``HIGH_RISK_PRIMITIVES`` flags as
``*** HIGH RISK ***`` on the stated invariant that launching an external program is
arbitrary code execution, though it launches nothing. A write-side primitive also
auto-derives a taint SINK (ADR-0017 §2b), so a false primitive can manufacture a
violated claim.

MECHANISM. ``_extract_module_hint`` returns ``None`` for a module slot beginning
``/`` or ``\\`` (an absolute file path is not a module hint). With no module hint,
``lookup_with_module`` skips the module filter entirely and falls through to
``gate_named_entry``, whose no-module branch permits a function-kind hit. And this is
the PRODUCTION path, not a fixture artifact: ``cli.py`` resolves the repo root, so
every real run carries absolute paths in first-party dsts.
``is_first_party_callable_dst`` is the rule that refuses such a dst.

WHY THE PARITY TEST IS THE POINT OF THIS FILE. A single-language probe can come back
clean for the wrong reason. Each catalogue's ``ambiguous_names`` is a curated per-name
BLOCKLIST, not a structural rule: a probe that picks a listed name (python's
``remove``, rust's ``write``) passes, while a name outside the list (``makedirs``)
exposes the defect. Rust is clean for a reason that is not reassuring: it emits a
RELATIVE module slot, which is returned AS a hint and then fails ``_module_matches``.
Right outcome, wrong reason — it is protected by a path-format difference between
analyzers rather than by a rule, and would acquire the defect silently if it ever
emitted absolute paths.

THE PREDICATE IS SCOPED TO DEFINITIONS. Refusing *every* first-party dst would
destroy true positives: an alias binding such as ``var readFileSync =
fs.readFileSync`` resolves to a first-party dst of kind ``variable`` while the tag is
CORRECT. So the rule is scoped to dst kinds that mean "a callable DEFINED here", and
alias bindings are deliberately left alone as a separate, unresolved question.

BLAST RADIUS. The rule withholds only the ungated short-name fallback, and only for
a ``function``/``method`` dst whose module slot is a filesystem path. External
placeholders such as Haskell's ``haskell:external:0-0:readFile:function`` carry a
module slot that is not a path and are unaffected, and a resolved stdlib call keeps
its exact qualified match. So the rule removes no true boundary while closing the
hole the fixtures prove. A broader gate on the hinted path would destroy most real
boundaries for no gain.
"""

import pytest

from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered
from hypergumbo_core.ddg_build import registered_ddg_languages
from hypergumbo_core.io_boundary import (
    FIRST_PARTY_CALLABLE_KINDS,
    is_first_party_callable_dst,
    load_catalog,
    tag_io_boundaries,
)
from hypergumbo_core.ir import Edge, ExternalRef
from hypergumbo_core.symbol_kinds import all_symbol_kind_names


def _dataflow_capable_languages() -> frozenset[str]:
    """The registry, populated deterministically.

    ``register_ddg_language`` fires on first IMPORT of each language module, so the
    registry is empty until something imports them and its contents otherwise depend on
    what a given test session happened to load first. That import-order dependence is a
    defect in its own right (WI-kogop: ``javascript dataflow_capable`` can read False
    at some ``-n`` worker orderings while CI stays green), so this parity test forces the
    registration through production's own helper rather than trusting ambient state — a
    parity test that silently enumerates one language is worse than no parity test.
    """
    ensure_def_use_extractors_registered()
    return registered_ddg_languages()

#: Per language: a catalogued primitive's short name chosen to sit OUTSIDE that
#: language's ``ambiguous_names`` list, so the test exercises the structural rule
#: rather than the curated blocklist, which can mask the defect.
SHADOWED_NAME = {
    "python": "makedirs",
    "go": "MkdirAll",
    "javascript": "mkdirSync",
    "typescript": "mkdirSync",
    "rust": "create_dir_all",
    # java.nio.file.Files.createDirectory: function-kind, and outside java's
    # ambiguous_names (createDirectories is on it). (WI-gotun)
    "java": "createDirectory",
    # unistd.unlink: function-kind, outside c's ambiguous_names. (WI-himob)
    "c": "unlink",
}


def _edge(
    dst: str,
    *,
    call_construct: str | None = None,
    dst_ref: ExternalRef | None = None,
) -> Edge:
    return Edge.create(
        src="python:app.py:1-1:caller:function",
        dst=dst,
        edge_type="calls",
        line=1,
        evidence_type="ast_call",
        is_resolved=True,
        dst_ref=dst_ref,
        meta={"call_construct": call_construct} if call_construct else None,
        origin="test",
        origin_run_id="run",
    )


class TestTheKindsAreRegistered:
    """The predicate names symbol kinds, so they must be the registry's kinds and not
    a private vocabulary that drifts from it (ADR-0027)."""

    def test_every_first_party_kind_is_a_registered_symbol_kind(self) -> None:
        known = all_symbol_kind_names()
        assert FIRST_PARTY_CALLABLE_KINDS <= set(known), (
            f"unregistered kinds: {FIRST_PARTY_CALLABLE_KINDS - set(known)}"
        )

    def test_alias_bindings_are_deliberately_excluded(self) -> None:
        """``variable`` is NOT in the set. An alias binding such as ``var readFileSync =
        fs.readFileSync`` reaches the real primitive through a first-party variable, and
        refusing that would delete a true positive. Pinned so a future widening has to
        argue with a test."""
        assert "variable" not in FIRST_PARTY_CALLABLE_KINDS
        assert "attribute" not in FIRST_PARTY_CALLABLE_KINDS


class TestThePredicate:
    def test_resolved_first_party_function(self) -> None:
        assert is_first_party_callable_dst("go:/abs/app.go:3-3:MkdirAll:function")

    def test_resolved_first_party_method(self) -> None:
        assert is_first_party_callable_dst("python:/abs/app.py:9-9:write:method")

    def test_external_unresolved_is_not_first_party(self) -> None:
        assert not is_first_party_callable_dst("go:os:0-0:MkdirAll:unresolved")

    def test_external_symbol_is_not_first_party(self) -> None:
        assert not is_first_party_callable_dst(
            "python:pathlib.Path:0-0:write_text:external_symbol",
        )

    def test_alias_variable_is_not_refused(self) -> None:
        assert not is_first_party_callable_dst(
            "javascript:/abs/view.js:25-25:dirname:variable",
        )

    def test_attribute_is_not_refused(self) -> None:
        assert not is_first_party_callable_dst("python:/abs/app.py:1-1:environ:attribute")

    def test_malformed_dst_is_not_refused(self) -> None:
        """A dst that does not carry five slots cannot be shown to be first-party, and
        an unprovable claim must not silently suppress a boundary."""
        assert not is_first_party_callable_dst("garbage")
        assert not is_first_party_callable_dst("")

    def test_external_placeholder_carrying_a_callable_kind(self) -> None:
        """The kind slot alone does not make a dst first-party. Haskell emits external
        placeholders as ``haskell:external:0-0:readFile:function`` — module slot
        ``external``, kind ``function``. A predicate keyed on the kind slot alone
        silently un-tags Haskell's ``readFile``/``writeFile``: a FALSE NEGATIVE in a
        security tool, which is the expensive direction and precisely what this
        predicate exists to avoid causing. A corpus scan without Haskell in it shows no
        tagged boundary with a ``function`` kind, so only this test catches it.
        """
        assert not is_first_party_callable_dst("haskell:external:0-0:readFile:function")
        assert not is_first_party_callable_dst("haskell:external:0-0:writeFile:function")

    def test_a_module_path_with_a_callable_kind_is_not_first_party(self) -> None:
        """A real module path in the module slot is never a repo location."""
        assert not is_first_party_callable_dst("go:os:0-0:MkdirAll:function")
        assert not is_first_party_callable_dst("rust:std::fs:0-0:create_dir_all:function")

    def test_relative_first_party_path_is_a_DISCLOSED_gap(self) -> None:
        """Stated rather than hidden: a first-party dst carrying a RELATIVE path is not
        recognised here. Rust emits that shape, and it is protected instead by the module
        filter — ``_module_matches("std::fs", "app.rs")`` fails, so the lookup returns
        None before the short-name fallback is reached. Two mechanisms covering one case,
        which is fragile; it is pinned here so the coverage is legible rather than
        accidental.

        RE-EVALUATION TRIGGER: if an analyzer ever emits a relative first-party path AND
        a module slot that could pass ``_module_matches``, this gap becomes reachable and
        the predicate must widen to a real path test.
        """
        assert not is_first_party_callable_dst("rust:app.rs:1-1:create_dir_all:function")


class TestTaggingRefusesAFirstPartyDefinition:
    def test_local_definition_is_not_tagged(self) -> None:
        edges = [_edge("go:/abs/app.go:3-3:MkdirAll:function")]
        assert tag_io_boundaries(edges, {"go": load_catalog("go")}) == 0
        assert (edges[0].meta or {}).get("io_boundary") is None

    def test_the_real_external_call_still_tags(self) -> None:
        """The positive control. Without it a zero above would be indistinguishable
        from a catalogue that cannot match anything."""
        edges = [_edge("go:os:0-0:MkdirAll:unresolved")]
        assert tag_io_boundaries(edges, {"go": load_catalog("go")}) == 1
        assert (edges[0].meta or {})["io_primitive"] == "os.MkdirAll"

    def test_high_risk_subprocess_case(self) -> None:
        """The costliest case: a local ``Command`` reported as a subprocess
        launch, on the one boundary flagged HIGH RISK."""
        edges = [_edge("go:/abs/app.go:7-7:Command:function")]
        assert tag_io_boundaries(edges, {"go": load_catalog("go")}) == 0

    def test_alias_binding_is_still_tagged(self) -> None:
        """``var readFileSync = fs.readFileSync`` — a TRUE positive that a blanket
        first-party refusal would delete. (Not ``path.dirname``: path arithmetic
        is not I/O, so the catalogue has no ``path`` rows; INV-nular F7.)"""
        edges = [_edge("javascript:/abs/view.js:25-25:readFileSync:variable")]
        assert tag_io_boundaries(edges, {"javascript": load_catalog("javascript")}) == 1


class TestEveryDataflowCapableLanguage:
    """THE PARITY TEST, and the reason this file exists.

    It enumerates the DDG registry rather than a hand-written list, so a newly
    wired language FAILS here instead of silently skipping the fix. A
    grep-for-the-call test would be satisfied by a second copy of the rule that merely
    looks right; this asserts behaviour, per language, in both directions.
    """

    @pytest.mark.parametrize("language", sorted(_dataflow_capable_languages()))
    def test_first_party_definition_never_tags_but_the_real_call_does(
        self, language: str,
    ) -> None:
        name = SHADOWED_NAME.get(language)
        assert name is not None, (
            f"{language!r} is dataflow-capable but has no shadowing fixture here. "
            f"Add one to SHADOWED_NAME — do not delete this assertion, which exists "
            f"so a newly wired language cannot skip this rule silently."
        )
        catalog = load_catalog(language)
        hits = catalog._by_short.get(name)
        assert hits, (
            f"{language}: {name!r} is not in the catalogue, so this fixture cannot "
            f"exercise the rule — the control would pass for the wrong reason."
        )
        module = hits[0].module

        first_party = [_edge(f"{language}:/abs/app.src:3-3:{name}:function")]
        # The external arm carries a structured ``dst_ref``, which is what production
        # analyzers emit for an external target (WI-tihup) and what ``tag_io_boundaries``
        # prefers over parsing the id, so the fixture has production's shape. The
        # first-party arm deliberately carries NO dst_ref, because a resolved in-repo
        # call edge does not carry one.
        external = [_edge(
            f"{language}:{module}:0-0:{name}:unresolved",
            dst_ref=ExternalRef(lang=language, module_path=module, name=name),
        )]

        assert tag_io_boundaries(first_party, {language: catalog}) == 0, (
            f"{language}: a first-party definition of {name!r} was attributed a "
            f"stdlib I/O boundary"
        )
        assert tag_io_boundaries(external, {language: catalog}) == 1, (
            f"{language}: the REAL {module}.{name} call stopped tagging — this rule "
            f"has destroyed a true positive, which is the expensive direction"
        )
