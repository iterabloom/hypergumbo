# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-mital: a linker's synthetic manifest->entrypoint edge must not withhold.

THE DEFECT. Two safety gates ask "which languages made calls, and do we have a
catalogue for them": ``verify_claims._call_production_coverage`` (I/O) and
``cli``'s taint-language census. Both derive the language from a call edge's
SOURCE ID, deliberately — the I/O gate's comment says why it is not keyed on
languages merely PRESENT:

    "Keyed on languages PRESENT this would name up to 16 apiece — markdown,
     gitignore, yaml, toml — and downgrade every verdict for a .gitignore.
     Keyed on languages that emitted CALL EDGES it names one to three real ones"

That proxy has since been broken from the other end. ``linkers/build_target.py``
resolves each manifest ``defines_target`` edge to the ``main()`` it names and
emits a **``calls``** edge, so a forward slice can traverse from a Cargo
``[[bin]]`` into application code. Its source is the build-target symbol, whose
language slot is the manifest's. So ``toml`` emits call edges after all, lands in
``languages_with_calls - set(catalogs)``, and EVERY boundary and taint claim over
a Rust binary crate is withheld on a file that performs no I/O.

Reproduced on mini-redis: 516 call edges, exactly 2 sourced at ``toml``, both
from ``build-target-linker``:

    toml:Cargo.toml:15-19:mini-redis-cli:binary  -> rust:src/bin/cli.rs:...:main
    toml:Cargo.toml:19-23:mini-redis-server:...  -> rust:src/bin/server.rs:...:main

WHY NOT THE ITEM'S OPTION (a). WI-mital proposes retyping the edge to
``defines_target``, flagging the risk that "the call graph may deliberately
traverse" it. It does: the ``defines_target`` edge points at a bare path string
the slicer cannot follow, which is the entire reason ``build_target.py`` exists.
Retyping would reopen the gap the linker was written to close.

WHY NOT THE OBVIOUS RULE EITHER — and this is the part worth reading. The first
cut keyed on ``taxonomy``'s ``FileRole.ANALYZABLE``: "a config format cannot
call." That is WRONG. ``FileRole`` describes a file's ROLE, not whether an
analyzer exists, and **17** of the languages it marks non-analyzable have
registered analyzers — ``dockerfile``, ``cmake``, ``hcl``, ``starlark``,
``just``, ``jsonnet`` among them. A Dockerfile ``RUN curl ...`` is real I/O, so
exempting the LANGUAGE would have been a false all-clear, which is the direction
these gates exist to prevent. ``TestARealAnalyzersCallIsNeverExempt`` is that
refutation kept as a test.

THE RULE THAT SHIPPED keys on WHAT MINTED THE EDGE. No real analyzer's output
carries the build-target linker's pass id, so a synthetic traversal aid is
exempt and a Dockerfile's genuine call is not. It also covers the ``manifest``
pseudo-language for free — the linker walks every ``defines_target`` edge,
whatever extractor produced it — which WI-mital flagged as "very likely the SAME
CLASS under a different name".

DIRECTION, STATED BECAUSE IT IS THE DANGEROUS ONE. This makes the gates withhold
LESS. It is safe only because the withholding was FALSE, and
``TestRealLanguagesStillWithhold`` plus ``TestARealAnalyzersCallIsNeverExempt``
are what keep it that way: fish, nix, ruby, powershell, perl and the
unregistered ``tcl`` must all keep withholding, exactly as WI-mital requires
("fish is not the same defect and must not be folded in").
"""

from __future__ import annotations

import pathlib

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import (
    _BUILD_TARGET_PASS_ID,
    _call_production_coverage,
)


def _edge(
    src: str,
    dst: str = "rust:src/main.rs:1-2:main:function",
    origin: list[str] | str | None = None,
) -> dict:
    """A call edge. ``origin`` defaults to a REAL analyzer, not the linker.

    The default matters: an edge is exempt because of WHAT MINTED IT, so a test
    that wants the exemption must ask for it explicitly.
    """
    return {
        "src": src,
        "dst": dst,
        "type": "calls",
        "origin": origin if origin is not None else ["rust-analyzer-pass"],
    }


def _build_target_edge(src: str) -> dict:
    return _edge(src, origin=[_BUILD_TARGET_PASS_ID])


def _coverage(edges: list[dict], languages: set[str]):
    return _call_production_coverage(
        edges,
        supported_languages=languages,
        catalogs={"rust": load_catalog("rust", include_defaults=True)},
    )


#: A real Rust call, so the run is never "no call edges at all" — that is a
#: DIFFERENT branch and would mask what these tests are measuring.
_REAL_RUST_CALL = _edge("rust:src/main.rs:10-10:run:function")


class TestConfigLanguagesDoNotWithhold:
    """The Cargo ``[[bin]]`` shape, reproduced from mini-redis."""

    def test_a_toml_build_target_call_does_not_withhold(self) -> None:
        coverage = _coverage(
            [_REAL_RUST_CALL, _build_target_edge("toml:Cargo.toml:15-19:mini-redis-cli:binary")],
            {"rust"},
        )
        assert coverage.complete, (
            f"toml withheld the verdict: {coverage.reason!r}"
        )

    @pytest.mark.parametrize("lang", ["toml", "json", "yaml", "manifest"])
    def test_no_config_format_withholds(self, lang: str) -> None:
        """General over manifest formats -- and ``manifest`` comes free.

        The linker walks EVERY ``defines_target`` edge, whatever extractor
        produced it, so the pseudo-language WI-mital flagged as "very likely
        the SAME CLASS under a different name" is covered by the same rule.
        """
        coverage = _coverage(
            [_REAL_RUST_CALL, _build_target_edge(f"{lang}:conf.{lang}:1-1:target:binary")],
            {"rust"},
        )
        assert coverage.complete, f"{lang} withheld the verdict: {coverage.reason!r}"


class TestBothOriginSpellings:
    """``Edge.origin`` is ``str | List[str]``; only ``__post_init__`` normalises.

    A well-formed edge always serialises as a list — 895 of 895 on a real
    mini-redis map — but the field type permits the scalar, and this gate reads
    raw dicts out of an artifact that may have been hand-edited. Covering the
    scalar with a test rather than a ``pragma: no cover`` keeps the tolerance
    honest: a pragma would assert the branch is unreachable, and it is not.
    """

    def test_a_scalar_origin_is_recognised(self) -> None:
        coverage = _coverage(
            [
                _REAL_RUST_CALL,
                _edge("toml:Cargo.toml:1-1:app:binary", origin=_BUILD_TARGET_PASS_ID),
            ],
            {"rust"},
        )
        assert coverage.complete, f"scalar origin not recognised: {coverage.reason!r}"

    def test_an_edge_with_no_origin_at_all_is_not_exempt(self) -> None:
        """An artifact that predates the key, or a hand-edited one.

        Absent provenance cannot establish that an edge is synthetic, so it must
        fall through to NOT exempt — the withholding direction. This is the same
        fail-closed rule the language cases follow.
        """
        edge = {
            "src": "toml:Cargo.toml:1-1:app:binary",
            "dst": "rust:src/main.rs:1-2:main:function",
            "type": "calls",
        }
        coverage = _coverage([_REAL_RUST_CALL, edge], {"rust"})
        assert not coverage.complete, (
            "an edge with no origin was treated as synthetic; absent provenance "
            "must fail closed"
        )
        assert "toml" in coverage.reason

    def test_a_scalar_origin_from_a_real_pass_is_not_exempt(self) -> None:
        coverage = _coverage(
            [_REAL_RUST_CALL, _edge("fish:c.fish:1-1:f:function", origin="fish-pass")],
            {"rust"},
        )
        assert not coverage.complete
        assert "fish" in coverage.reason


class TestRealLanguagesStillWithhold:
    """THE CONTROL, and the reason this change is safe in the loosening direction.

    WI-mital is explicit that ``fish`` "is not the same defect and must not be
    folded in" — a fish script genuinely calls ``printf`` and genuinely can
    perform I/O, so withholding is INV-javam working as designed. Same for the
    rest of that list. If any of these stops withholding, the fix has become a
    false-all-clear generator.
    """

    @pytest.mark.parametrize(
        "lang", ["fish", "nix", "ruby", "powershell", "perl", "lean", "solidity"],
    )
    def test_an_analyzable_language_without_a_catalogue_still_withholds(
        self, lang: str,
    ) -> None:
        coverage = _coverage(
            [_REAL_RUST_CALL, _edge(f"{lang}:script.{lang}:1-1:fn:function")],
            {"rust"},
        )
        assert not coverage.complete, (
            f"{lang} no longer withholds — it is an ANALYZABLE language with no "
            f"I/O catalogue, so its calls are genuinely unclassifiable"
        )
        assert lang in coverage.reason

    def test_a_language_absent_from_the_registry_fails_CLOSED(self) -> None:
        """``tcl`` is one of WI-mital's own correct cases and is NOT in LANGUAGES.

        This is the test that forbids the cheap rule ("not in the registry →
        exclude"). Unknown must keep withholding.
        """
        coverage = _coverage(
            [_REAL_RUST_CALL, _edge("tcl:script.tcl:1-1:proc:function")], {"rust"},
        )
        assert not coverage.complete, (
            "tcl stopped withholding — an unrecognised language slot was treated "
            "as exempt rather than as unknown"
        )
        assert "tcl" in coverage.reason

    def test_the_manifest_pseudo_language_also_fails_closed(self) -> None:
        """Documented, not desired: ``manifest`` is absent from the registry too.

        WI-mital expects this class to be covered eventually. It is NOT covered
        here, and this test says so out loud rather than leaving a silent gap —
        edit it when the pseudo-language gets a home that is not the file-type
        registry.
        """
        coverage = _coverage(
            [_REAL_RUST_CALL, _edge("manifest:pom.xml:1-1:app:binary")], {"rust"},
        )
        assert not coverage.complete
        assert "manifest" in coverage.reason


class TestBothGatesUseTheSharedPredicate:
    """ONE FACT, ONE HOME — and this file is where the second home gets caught.

    Two independent checks ask "which languages made calls": the I/O gate in
    ``verify_claims._call_production_coverage`` and the taint-language census in
    ``cli``. The census's own comment records the failure mode of fixing one and
    not the other: "Narrowing only the first check is how this function would
    come to give two different answers about the same fixture."

    Measured live while making this change: patching only ``verify_claims`` left
    mini-redis still reporting ``toml`` — through the census, not the I/O gate.
    A unit test of one consumer proves nothing about the other.
    """

    def test_the_census_consults_the_shared_predicate(self) -> None:
        import hypergumbo_core.cli as cli_mod

        text = pathlib.Path(cli_mod.__file__).read_text(encoding="utf-8")
        assert "is_synthetic_build_target_call" in text, (
            "cli's taint census no longer consults the shared predicate"
        )

    def test_the_predicate_has_exactly_one_definition(self) -> None:
        import hypergumbo_core.verify_claims as vc_mod

        src_dir = pathlib.Path(vc_mod.__file__).parent
        definitions = [
            path.name
            for path in sorted(src_dir.rglob("*.py"))
            if "def is_synthetic_build_target_call" in path.read_text(
                encoding="utf-8", errors="ignore",
            )
        ]
        assert definitions == ["verify_claims.py"], (
            f"the predicate is defined in {definitions}; it must have one home"
        )

    def test_the_pass_id_matches_the_linker_that_mints_the_edge(self) -> None:
        """The constant is a LITERAL, so this is what stops it drifting.

        ``verify_claims`` cannot import ``linkers/build_target`` — importing a
        linker REGISTERS it as a side effect, and a safety gate must not change
        which linkers exist. A test has no such constraint, so the two are
        reconciled here instead.
        """
        from hypergumbo_core.linkers.build_target import PASS_ID

        assert _BUILD_TARGET_PASS_ID == PASS_ID


class TestARealAnalyzersCallIsNeverExempt:
    """THE CONTROL THAT KILLED THE OBVIOUS RULE.

    The first cut keyed on ``taxonomy``'s ``FileRole.ANALYZABLE`` — "a config
    format cannot call". That is wrong, and measurably: ``FileRole`` describes a
    file's ROLE, not whether an analyzer exists, and **17** of the languages it
    marks non-analyzable have registered analyzers — ``dockerfile``, ``cmake``,
    ``hcl``, ``starlark``, ``just``, ``jsonnet`` among them. A Dockerfile
    ``RUN curl ...`` is real I/O, so exempting the language would have been a
    false all-clear. Keying on the MINTING PASS cannot make that mistake.
    """

    @pytest.mark.parametrize("lang", ["dockerfile", "cmake", "hcl", "just", "toml"])
    def test_a_call_edge_from_a_real_pass_still_withholds(self, lang: str) -> None:
        coverage = _coverage(
            [_REAL_RUST_CALL, _edge(f"{lang}:build.{lang}:3-3:step:function")],
            {"rust"},
        )
        assert not coverage.complete, (
            f"a {lang} call edge minted by a real analyzer was exempted; only the "
            f"build-target linker's synthetic edge may be"
        )
        assert lang in coverage.reason
