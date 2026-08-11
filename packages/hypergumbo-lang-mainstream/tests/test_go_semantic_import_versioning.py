# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go semantic import versioning: a ``/vN`` import must still bind its package name.

THE GAP (INV-javid). ``_process_import_spec`` derived a package's identifier as the
LAST element of its import path. Go's semantic import versioning puts a ``/vN``
element at the end of every module path at major version 2 or higher, so the derived
identifier was the literal string ``v4`` and the real one — ``echo`` — was never
registered. Every call on a value from such a package then missed ``import_aliases``,
``_go_external_import_path`` returned ``None``, and the dst fell back to the
``external`` placeholder.

WHY THIS IS NOT AN IO-BOUNDARY BUG WEARING A CALL-GRAPH HAT. The lost value is the
MODULE HINT on an ordinary ``calls`` edge, so the blast radius is the Go call graph
and everything downstream of it — io-boundaries, taint matching, dead-code
reachability — not one catalogue. Since Go modules, ``/vN`` is the standard for any
library at v2+, so the exposed population is a large and unmeasured share of real
third-party Go.

THE CONTROL THAT MAKES THIS A MEASUREMENT AND NOT A GUESS. A bare "these calls go
external" observation cannot separate this defect from ordinary unresolved-external
behaviour. The discriminator is that the DEEPER path resolves while the MODULE ROOT
does not, in the same file and the same run::

    import "github.com/caddyserver/caddy/v2"                    -> alias 'v2'
    import "github.com/caddyserver/caddy/v2/modules/caddyhttp"   -> alias 'caddyhttp'

    caddy.RegisterModule(x)      -> go:external:0-0:RegisterModule    LOST
    caddyhttp.RegisterHandler(x) -> go:…/modules/caddyhttp:0-0:…      RESOLVED

That asymmetry is the signature of the alias derivation rather than of anything about
the callee, which is why ``TestModuleRootVersusDeeperPath`` pins BOTH arms rather than
asserting the fixed one alone.

TWO CONVENTIONS, ONE CONCEPT. Go modules spell the major version as a trailing PATH
ELEMENT (``github.com/labstack/echo/v4``); gopkg.in spells it as a DOTTED SUFFIX on
the last element (``gopkg.in/yaml.v2``). Both are "a major-version marker sitting
where the package identifier is expected", so both belong to one helper — but they
are different rules and are tested separately, because gopkg.in legitimately uses
``.v1`` while a Go-module ``/v1`` element is far more likely to be a real directory.

WHAT MUST NOT REGRESS. ``v2`` is a legal package name and ``v2beta1`` is a common
Kubernetes-style one. Stripping either would invent a second defect while fixing the
first, so ``TestVersionLookalikesAreNotStripped`` is the guard and it is deliberately
adversarial.

DIRECTION. This ADDS module hints, so it is the recall direction: an edge that
carried no path now carries one. It cannot delete a taint finding by earning
``sanitized``, because it changes no walk verdict. It CAN move a dst from the
``external`` placeholder onto an in-repo symbol when the versioned module is the
repo's own (the Caddy shape) — that is the hijack surface a prior Go typing attempt
tripped on, and it is measured separately rather than assumed benign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import _go_package_identifier


class TestPackageIdentifierDerivation:
    """Unit-level: the derivation itself, with no analyzer in the way."""

    @pytest.mark.parametrize(
        ("import_path", "expected"),
        [
            # Go modules semantic import versioning — the filed defect.
            ("github.com/labstack/echo/v4", "echo"),
            ("github.com/gofiber/fiber/v2", "fiber"),
            ("github.com/caddyserver/caddy/v2", "caddy"),
            ("example.com/mod/v10", "mod"),
            # Unversioned paths are untouched.
            ("github.com/gin-gonic/gin", "gin"),
            ("google.golang.org/grpc", "grpc"),
            ("net/http", "http"),
            ("os", "os"),
            # A /vN element that is NOT last leaves the last element alone.
            ("github.com/caddyserver/caddy/v2/modules/caddyhttp", "caddyhttp"),
        ],
    )
    def test_module_version_element_is_not_the_package_name(
        self, import_path: str, expected: str,
    ) -> None:
        assert _go_package_identifier(import_path) == expected

    @pytest.mark.parametrize(
        ("import_path", "expected"),
        [
            ("gopkg.in/yaml.v2", "yaml"),
            ("gopkg.in/yaml.v3", "yaml"),
            # gopkg.in DOES use .v1, unlike Go modules.
            ("gopkg.in/check.v1", "check"),
            ("gopkg.in/user/pkg.v2", "pkg"),
        ],
    )
    def test_gopkg_in_dotted_version_suffix(
        self, import_path: str, expected: str,
    ) -> None:
        assert _go_package_identifier(import_path) == expected


class TestVersionLookalikesAreNotStripped:
    """Adversarial: a fix that over-strips invents a defect while closing one."""

    @pytest.mark.parametrize(
        ("import_path", "expected"),
        [
            # A package legitimately NAMED v2, with nothing to fall back to.
            ("v2", "v2"),
            # Kubernetes-style API versions are package names, not module versions.
            ("k8s.io/api/apps/v2beta1", "v2beta1"),
            ("k8s.io/api/core/v1alpha1", "v1alpha1"),
            # Go modules do not use a /v1 suffix (v0 and v1 are unsuffixed), so a
            # literal v1 element is far more likely to be a real directory.
            ("example.com/mod/v1", "v1"),
            # Not a version marker at all — only a prefix collision.
            ("github.com/foo/v2bar", "v2bar"),
            ("github.com/foo/version", "version"),
            # The dotted rule is scoped to gopkg.in and must not leak.
            ("github.com/foo/yaml.v2", "yaml.v2"),
        ],
    )
    def test_lookalike_is_left_alone(
        self, import_path: str, expected: str,
    ) -> None:
        assert _go_package_identifier(import_path) == expected


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent.

    Mirrors the probe in ``test_go_composite_literal_receiver_typing`` and for the
    same recorded reason: a skip reachable by a typo is a green tick over a hole, so
    this calls the availability function directly with no exception handler.
    """
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


def _call_dsts(analysis, callee: str) -> list[str]:
    return [
        e.dst for e in analysis.edges
        if e.edge_type == "calls" and e.dst.split(":")[-2].split(".")[-1] == callee
    ]


class TestModuleRootVersusDeeperPath:
    """End-to-end, and BOTH arms are pinned.

    The deeper-path arm is the control. Without it, a failure of the versioned arm is
    indistinguishable from "this analyzer resolves nothing here", which is exactly the
    ambiguity that let the filed symptom sit under a guess for a month.
    """

    SOURCE: str = (
        "package encode\n"
        "\n"
        "import (\n"
        '\t"github.com/caddyserver/caddy/v2"\n'
        '\t"github.com/caddyserver/caddy/v2/modules/caddyhttp"\n'
        ")\n"
        "\n"
        "func register() {\n"
        "\tcaddy.RegisterModule(Gzip{})\n"
        "\tcaddyhttp.RegisterHandler(Gzip{})\n"
        "}\n"
        "\n"
        "type Gzip struct{}\n"
    )

    def _analyze(self, tmp_path: Path):
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = tmp_path / "caddyish"
        repo.mkdir()
        (repo / "go.mod").write_text(
            "module github.com/caddyserver/caddy/v2\n\ngo 1.21\n",
        )
        (repo / "encode.go").write_text(self.SOURCE)
        return analyze_go(repo)

    def test_deeper_path_resolves(self, tmp_path: Path, go_available) -> None:
        """CONTROL. Unversioned last element — this already worked."""
        dsts = _call_dsts(self._analyze(tmp_path), "RegisterHandler")
        assert dsts, "control produced no calls edge at all; the fixture is broken"
        assert any("modules/caddyhttp" in d for d in dsts), dsts

    def test_module_root_also_resolves(self, tmp_path: Path, go_available) -> None:
        """THE DEFECT. ``caddy`` was never bound, so this fell to ``external``."""
        dsts = _call_dsts(self._analyze(tmp_path), "RegisterModule")
        assert dsts, "no calls edge for RegisterModule; the fixture is broken"
        assert not any(":external:" in d for d in dsts), (
            "module root lost its import path to the /vN alias defect: " + repr(dsts)
        )
        assert any("caddyserver/caddy" in d for d in dsts), dsts
