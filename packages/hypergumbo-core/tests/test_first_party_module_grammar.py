# SPDX-License-Identifier: AGPL-3.0-or-later
"""The uncatalogued-module disclosure must not report FIRST-PARTY code as an
unexamined third-party module (INV-juvul).

WHAT WAS BROKEN, measured 2026-08-24 on two UNMODIFIED upstream repos by
recomputing ``_uncatalogued_external_modules`` over each run's own cached
survey (the rendered ``details`` string truncates the list at five)::

    express (javascript)  17 modules reported "the I/O catalog could not classify"
        FIRST-PARTY (7)  ..  ../  ../../db  ./post  ./site  ./user  lib/utils
        GENUINE (4)      body-parser  mime-types  proxy-addr  statuses

    bellman (rust)        23 modules reported
        FIRST-PARTY (6)  crate.SynthesisError  crate.gadgets.boolean.Boolean
                         super.SynthesisError  super.boolean.Boolean
                         super.uint32.UInt32   bellman.VerificationError
        GENUINE (5)      blake2s_simd::Params  ff.Field  rand  rayon
                         rand_xorshift::XorShiftRng

``crate::``, ``super::`` and ``self::`` are Rust's own path qualifiers and
resolve inside the current crate by definition (Rust reference, "Paths"). A
JavaScript specifier beginning ``./``, ``../`` or ``/`` is resolved as a path
relative to the importing file and is never looked up in ``node_modules``
(CommonJS / ESM resolution). Neither can name a third-party module, so
reporting them as unexamined ones both withholds the verdict for a false
reason and buries the entries that are real.

WHY IT COSTS MORE THAN THE COUNT SUGGESTS. ``BoundaryCoverage.qualifying_only``
is ``not unknown``, so ONE noise entry is enough to withhold every claim. On
both repos above the first-party entries ALONE are sufficient to do that.

THE THIRD MECHANISM, and it is not language-specific. ``_analyzed_modules``
normalises ``/`` to ``.`` on the src side while the callee side was compared
raw, so express's own ``lib/utils`` could never match its own analyzed
``lib.utils``. That asymmetry is a one-sided implementation of a contract
:func:`io_boundary._module_matches` already documents ("normalises ``/`` to
``.`` and compares whole components"); it is fixed by giving the normalisation
ONE home rather than by widening a gate.

WHAT THIS DELIBERATELY DOES **NOT** FIX, stated because the count invites the
wrong conclusion. ``bellman.VerificationError`` — a crate referring to itself
by its own published name — is still reported. The crate name is available in
the graph (``toml:Cargo.toml:7-22:bellman:package``), but telling that node
from a DEPENDENCY package node (``javascript:npm:0-0:morgan:package``, same
``package`` kind) currently requires reading the id's path slot and span
shape, and ``supply_chain_tier`` is ``None`` on every package node in both
repos. That is a shape heuristic, and this is a safety gate, so it is filed
rather than guessed at.

AND THE HONEST HEADLINE: fixing all of this FLIPS NO VERDICT. express still
reports 10 modules and bellman still reports stdlib, because what remains is
stdlib enumeration — only ``python.yaml`` declares any ``module_completeness``
and 14 of 15 catalogued languages declare zero. That is WI-lutuh, and this
work is a prerequisite for reading its output, not a substitute for it.
"""

from pathlib import Path

import pytest

from hypergumbo_core import io_boundary
from hypergumbo_core.io_boundary import (
    FIRST_PARTY_MODULE_GRAMMARS,
    IoBoundaryCatalog,
    IoPrimitive,
    is_definitionally_first_party,
)
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _rust_catalog() -> IoBoundaryCatalog:
    """A stand-in for rust.yaml. ``module_completeness`` is EMPTY on purpose —
    that is rust.yaml's real state (WI-lutuh), so a module reaching the gate is
    reported unless something upstream of enumeration excludes it."""
    return IoBoundaryCatalog(
        language="rust",
        primitives=[
            IoPrimitive(boundary="fs_read", module="std::fs",
                        name="read_to_string", kind="function"),
        ],
        stdlib_modules=frozenset({"std"}),
        module_completeness={},
    )


def _js_catalog() -> IoBoundaryCatalog:
    """A stand-in for javascript.yaml, likewise with no enumerated modules."""
    return IoBoundaryCatalog(
        language="javascript",
        primitives=[
            IoPrimitive(boundary="fs_write", module="node:fs",
                        name="writeFileSync", kind="function"),
        ],
        stdlib_modules=frozenset({"node:fs"}),
        module_completeness={},
    )


def _rust_call(dst: str, src: str = "rust:src/lib.rs:1-5:prove:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls"}


def _js_call(dst: str, src: str = "javascript:lib/express.js:1-5:handle:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls"}


def _rust_coverage(edges: list[dict]):
    return compute_boundary_coverage(edges, {"rust"}, {"rust": _rust_catalog()})


def _js_coverage(edges: list[dict]):
    return compute_boundary_coverage(edges, {"javascript"}, {"javascript": _js_catalog()})


class TestRustSelfReferenceKeywords:
    """``crate`` / ``super`` / ``self`` are RESERVED WORDS. Cargo refuses them as
    package names, so a leading component spelled that way cannot be a
    third-party crate under any repository's dependency set — which is what
    makes this an exact rule rather than a heuristic."""

    @pytest.mark.parametrize("dst", [
        # dotted spelling — every one of these is a real bellman edge
        "rust:crate.SynthesisError:0-0:crate.SynthesisError.AssignmentMissing:external_symbol",
        "rust:crate.gadgets.boolean.Boolean:0-0:crate.gadgets.boolean.Boolean.Is:external_symbol",
        "rust:super.SynthesisError:0-0:super.SynthesisError.UnexpectedIdentity:external_symbol",
        "rust:super.uint32.UInt32:0-0:super.uint32.UInt32.from_bits_be:external_symbol",
        # colon spelling — the same fact reached through a `calls` edge
        "rust:crate::gadgets::boolean:0-0:enforce:external_symbol",
        "rust:super::boolean:0-0:enforce:external_symbol",
        "rust:self::multicore:0-0:Worker:external_symbol",
    ])
    def test_a_self_reference_is_not_an_unexamined_module(self, dst: str) -> None:
        coverage = _rust_coverage([_rust_call(dst)])
        assert coverage.complete is True, coverage.reason

    def test_a_crate_whose_name_merely_starts_with_a_keyword_is_still_reported(
        self,
    ) -> None:
        """Component-bounded, not string-prefixed. ``cratedb`` is a real crate
        name; ``superstruct`` and ``selfie`` are too. A ``startswith("crate")``
        rule would silently swallow all three, and swallowing a real dependency
        is the failure direction this whole gate exists to prevent."""
        coverage = _rust_coverage([
            _rust_call("rust:cratedb::Client:0-0:connect:external_symbol"),
            _rust_call("rust:superstruct:0-0:derive:external_symbol"),
            _rust_call("rust:selfie::Selfie:0-0:new:external_symbol"),
        ])
        assert coverage.complete is False
        for name in ("cratedb", "superstruct", "selfie"):
            assert name in coverage.reason

    def test_the_keyword_counts_only_at_the_HEAD_of_the_path(self) -> None:
        """``blake2s_simd::crate`` is a third-party path that happens to contain
        the word. Only the first component qualifies a Rust path."""
        coverage = _rust_coverage([
            _rust_call("rust:blake2s_simd::crate:0-0:Params:external_symbol"),
        ])
        assert coverage.complete is False
        assert "blake2s_simd" in coverage.reason


class TestJavascriptRelativeSpecifiers:
    """A specifier beginning ``.`` or ``/`` is a PATH. Node resolves it against
    the importing file and never consults ``node_modules``, so it cannot name a
    package the catalogue might have had an opinion about."""

    @pytest.mark.parametrize("dst", [
        # every one of these is a real express edge
        "javascript:./post:0-0:./post.list:external_symbol",
        "javascript:./site:0-0:./site.index:external_symbol",
        "javascript:./user:0-0:./user.edit:external_symbol",
        "javascript:../../db:0-0:../../db.pets:external_symbol",
        "javascript:../:0-0:../.Router:external_symbol",
        "javascript:..:0-0:json:external_symbol",
    ])
    def test_a_relative_specifier_is_not_an_unexamined_module(self, dst: str) -> None:
        coverage = _js_coverage([_js_call(dst)])
        assert coverage.complete is True, coverage.reason

    def test_an_ABSOLUTE_path_is_still_reported(self) -> None:
        """The rule is RELATIVE, not "is a path", and the difference is
        load-bearing. ``./x`` resolves against the importing file, which is in
        this repository. ``/opt/app/config`` — or, in C, ``/usr/include/openssl
        /ssl.h`` — resolves against the filesystem and routinely names code
        this analysis never read. Treating the two alike would suppress system
        headers, which is the failure direction that produces a false clean
        verdict."""
        coverage = _js_coverage([
            _js_call("javascript:/opt/app/config:0-0:load:external_symbol"),
        ])
        assert coverage.complete is False
        assert "/opt/app/config" in coverage.reason

    def test_a_scoped_package_is_still_reported(self) -> None:
        """``@scope/pkg`` carries a separator but is a REGISTRY name, not a
        path. It must survive both the relative rule and the ``/``-to-``.``
        normalisation."""
        coverage = _js_coverage([
            _js_call("javascript:@sentry/node:0-0:captureException:external_symbol"),
        ])
        assert coverage.complete is False
        assert "@sentry/node" in coverage.reason


class TestSeparatorNormalisationIsSymmetric:
    """The src side was normalised and the callee side was not, so a repo's own
    module could not match its own analyzed path. Language-independent."""

    def test_a_slash_spelled_callee_matches_a_dotted_analyzed_path(self) -> None:
        """express's ``lib/utils``: analyzed as ``lib.utils`` from the src side
        of a real edge, reported as an unexamined module from the callee side."""
        coverage = _js_coverage([
            _js_call("javascript:lib/utils:0-0:wetag:external_symbol",
                     src="javascript:lib/utils.js:10-20:wetag:function"),
        ])
        assert coverage.complete is True, coverage.reason

    def test_folding_a_separator_does_not_license_SHORTENING_the_module(self) -> None:
        """CADDY REFUTED THE FIRST CUT OF THIS FIX, in the control run, before it
        shipped — and it is the reason a Go repo was in the control set at all.

        Folding ``/`` into ``.`` and then running the existing prefix loop turns
        ``os/exec`` into two components, lets the loop shorten it to ``os``, and
        matches caddy's own ``internal/filesystems/os.go``. Result: the
        SUBPROCESS module suppressed from the disclosure, plus ``crypto/tls``,
        ``crypto/x509`` and seven more of Go's standard library, because the
        repo owns a file called ``crypto.go``. Thirteen suppressions on caddy,
        ten of them wrong, every one in the false-clean direction.

        Shortening strips a trailing TYPE off a dotted callee slot. A change of
        separator does not license it."""
        go_catalog = IoBoundaryCatalog(
            language="go",
            primitives=[IoPrimitive(boundary="fs_read", module="io",
                                    name="ReadAll", kind="function")],
            stdlib_modules=frozenset({"os", "io"}),
            module_completeness={},
        )
        # caddy's real shape: a first-party file whose LAST component collides
        # with the FIRST component of a stdlib package path.
        edges = [
            {"src": "go:internal/filesystems/os.go:1-5:New:function",
             "dst": "go:os/exec:0-0:Command:external_symbol", "type": "calls"},
            {"src": "go:modules/caddypki/crypto.go:1-5:load:function",
             "dst": "go:crypto/tls:0-0:Dial:external_symbol", "type": "calls"},
        ]
        coverage = compute_boundary_coverage(edges, {"go"}, {"go": go_catalog})
        assert coverage.complete is False
        assert "os/exec" in coverage.reason, (
            "the subprocess module was suppressed because the repo owns os.go"
        )
        assert "crypto/tls" in coverage.reason

    def test_a_repos_own_slash_spelled_package_IS_suppressed(self) -> None:
        """The other side of the same run, so the fix above is not just a
        refusal to do anything. caddy's ``modules/caddyhttp`` matches its own
        analyzed ``modules/caddyhttp/...`` files WHOLE, with no shortening, and
        is correctly dropped from the disclosure."""
        go_catalog = IoBoundaryCatalog(
            language="go", primitives=[], stdlib_modules=frozenset(),
            module_completeness={},
        )
        edges = [
            {"src": "go:modules/caddyhttp/reverseproxy/proxy.go:1-5:h:function",
             "dst": "go:modules/caddyhttp:0-0:Handler:external_symbol",
             "type": "calls"},
        ]
        coverage = compute_boundary_coverage(edges, {"go"}, {"go": go_catalog})
        assert coverage.complete is True, coverage.reason

    def test_normalisation_alone_does_not_vouch_for_an_unread_module(self) -> None:
        """The positive control for the test above: the same slash-spelled shape,
        with NO analyzed file behind it, is still reported. Otherwise the fix
        would be suppressing on separator shape rather than on evidence."""
        coverage = _js_coverage([
            _js_call("javascript:vendor/telemetry:0-0:send:external_symbol",
                     src="javascript:lib/express.js:1-5:handle:function"),
        ])
        assert coverage.complete is False
        assert "vendor/telemetry" in coverage.reason


class TestGenuineThirdPartyModulesSurvive:
    """The positive controls, taken from the same two runs. A fix that quiets
    the noise by quieting everything is the failure mode with the better
    metric, so the entries the disclosure EXISTS for are pinned here."""

    def test_bellman_genuine_dependencies_are_still_reported(self) -> None:
        coverage = _rust_coverage([
            _rust_call("rust:blake2s_simd::Params:0-0:new:external_symbol"),
            _rust_call("rust:rand:0-0:thread_rng:external_symbol"),
            _rust_call("rust:rayon:0-0:current_num_threads:external_symbol"),
        ])
        assert coverage.complete is False
        for name in ("blake2s_simd::Params", "rand", "rayon"):
            assert name in coverage.reason

    def test_express_genuine_dependencies_are_still_reported(self) -> None:
        coverage = _js_coverage([
            _js_call("javascript:body-parser:0-0:json:external_symbol"),
            _js_call("javascript:mime-types:0-0:lookup:external_symbol"),
            _js_call("javascript:proxy-addr:0-0:compile:external_symbol"),
        ])
        assert coverage.complete is False
        for name in ("body-parser", "mime-types", "proxy-addr"):
            assert name in coverage.reason

    def test_a_first_party_entry_no_longer_hides_a_genuine_one(self) -> None:
        """The whole point, at the resolution the reader sees. Before this
        landed, express's reason opened with ``..``, ``../``, ``../../db`` and
        truncated at five, so the four real dependencies were literally
        invisible in the rendered output."""
        coverage = _js_coverage([
            _js_call("javascript:..:0-0:json:external_symbol"),
            _js_call("javascript:../:0-0:../.Router:external_symbol"),
            _js_call("javascript:../../db:0-0:../../db.pets:external_symbol"),
            _js_call("javascript:./post:0-0:./post.list:external_symbol"),
            _js_call("javascript:./site:0-0:./site.index:external_symbol"),
            _js_call("javascript:./user:0-0:./user.edit:external_symbol"),
            _js_call("javascript:statuses:0-0:message:external_symbol"),
        ])
        assert coverage.complete is False
        assert "statuses" in coverage.reason
        for noise in ("..", "../", "../../db", "./post", "./site", "./user"):
            assert noise not in coverage.reason


class TestEveryCataloguedLanguageDeclaresItsGrammar:
    """A REGISTRY PLUS A GATE, not a better literal (LIVE.md rule 7).

    The defect this file closes is a python-shaped rule applied to two
    languages whose module grammar differs from python's, and the way it
    repeats is a sixteenth language arriving with nobody having asked the
    question. So every language the boundary gate can reach must carry an
    entry — including an EMPTY one, which is a declaration that the language
    has no definitional first-party spelling, not an omission."""

    @staticmethod
    def _catalogued_languages() -> set[str]:
        catalog_dir = Path(io_boundary.__file__).parent / "io_primitives"
        langs = {p.stem for p in catalog_dir.glob("*.yaml")}
        assert langs, "no io_primitives catalogs found — vacuous gate"
        return langs | set(io_boundary._CATALOG_ALIASES)

    def test_every_catalogued_language_has_a_grammar_entry(self) -> None:
        missing = self._catalogued_languages() - set(FIRST_PARTY_MODULE_GRAMMARS)
        assert not missing, (
            f"languages reachable by the boundary gate with no first-party "
            f"module grammar declared: {sorted(missing)}. Add an entry to "
            f"FIRST_PARTY_MODULE_GRAMMARS — an empty one is a valid answer, "
            f"but it has to be written down."
        )

    def test_every_grammar_states_its_basis(self) -> None:
        """An entry with no basis is an assertion nobody can check. Empty
        grammars need one MORE than populated ones, because 'this language has
        no self-reference spelling' is the claim that fails open."""
        for language, grammar in FIRST_PARTY_MODULE_GRAMMARS.items():
            assert grammar.basis.strip(), f"{language} declares no basis"

    def test_an_undeclared_language_fails_OPEN_and_that_is_deliberate(self) -> None:
        """The direction here is the opposite of ``analyzer_disclosure``'s, and
        the difference is which way each one is unsafe.

        A missing method-call declaration means "I could not see it" and must
        fail CLOSED. A missing first-party grammar means "I know of no reason
        to suppress this module", and suppressing on an absent rule is what
        would hide a real dependency. So an unknown language suppresses
        NOTHING and every module it names is reported — noisier, never
        quieter. The parity gate above is what keeps that from being a silent
        default."""
        assert is_definitionally_first_party("cobol", "crate.Thing") is False
        assert is_definitionally_first_party("cobol", "./local") is False
