# SPDX-License-Identifier: AGPL-3.0-or-later
"""A repo referring to itself by its PUBLISHED PACKAGE NAME is first-party
(INV-vivok).

THE THIRD FIRST-PARTY MECHANISM. ``_uncatalogued_external_modules`` already
asks two questions before reporting a module as unexamined:
:func:`_is_analyzed_module` (path-derived — did I read a file at this path)
and :func:`is_definitionally_first_party` (language-derived — is this
``crate::``/``./`` and therefore unable to name a dependency). Neither can see
a repo that refers to itself by the name it PUBLISHES, because that name lives
in a MANIFEST and not in the directory layout::

    bellman   bellman.VerificationError            <- Cargo.toml [package] name
    caddy     github.com/caddyserver/caddy/v2      <- go.mod module
              github.com/caddyserver/caddy/v2/modules/caddyhttp

caddy is the sharper case: its RELATIVE spellings (``modules/caddyhttp``) are
already suppressed by the path-derived test, so the SAME package was judged
differently depending on how the importing file happened to spell it.

WHY NOT ``supply_chain_tier``, and this file exists partly to record the
refutation. INV-vivok was filed saying the tier "is None on every package node
in both repos". It is not: ``ir.py`` declares ``supply_chain_tier: int = 1``
and serialises it NESTED under ``supply_chain.tier``, so only a TOP-LEVEL read
returns None. Measured over all 42 cached surveys, 796 package nodes: 187 at
tier 1, 609 at tier 3, none None — and 10 of 19 repos carry both, so it does
discriminate. It is still the wrong instrument, for a different reason: tier on
a package node is derived from the DECLARING FILE'S PATH, so a genuinely
third-party package declared in an in-repo manifest reads first_party::

    cmake:CMakeLists.txt:204-204:"GnuTLS":package   tier=1

GnuTLS is not libzmq. Suppressing on tier would hand a FALSE CLEAN VERDICT to
exactly the population this gate protects. So the name is read from the
manifest directly, which is a fact rather than a shape heuristic.

THE FAILURE DIRECTION GOVERNS THE MATCH RULE. Suppression here can only ever
HIDE a genuine third-party module, so the match is component-bounded: a
package ``caddy`` must not swallow a dependency named ``caddyserver``. The
controls in this file are the point, not the positive cases.

HONEST SCOPE: this flips no verdict. 1 of bellman's 18 remaining entries and 4
of caddy's 158. It is noise reduction in a disclosure whose remaining bulk is
stdlib enumeration (WI-lutuh).
"""

import json
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.supply_chain import collect_first_party_package_names
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _rust_catalog() -> IoBoundaryCatalog:
    """No ``module_completeness`` on purpose — a module reaching the gate is
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


def _go_catalog() -> IoBoundaryCatalog:
    return IoBoundaryCatalog(
        language="go",
        primitives=[
            IoPrimitive(boundary="fs_read", module="os",
                        name="ReadFile", kind="function"),
        ],
        stdlib_modules=frozenset({"os"}),
        module_completeness={},
    )


def _rust_call(dst: str) -> dict:
    return {"src": "rust:src/lib.rs:1-5:prove:function", "dst": dst,
            "type": "calls"}


def _go_call(dst: str) -> dict:
    return {"src": "go:caddy.go:1-5:Run:function", "dst": dst, "type": "calls"}


def _rust_coverage(edges: list[dict], packages: set[str] | None = None):
    return compute_boundary_coverage(
        edges, {"rust"}, {"rust": _rust_catalog()},
        first_party_packages=packages,
    )


def _go_coverage(edges: list[dict], packages: set[str] | None = None):
    return compute_boundary_coverage(
        edges, {"go"}, {"go": _go_catalog()},
        first_party_packages=packages,
    )


class TestReadingThePublishedNameFromTheManifest:
    """``collect_first_party_package_names`` reads a FACT out of each manifest
    — not a regex over every ``name =`` it can find."""

    def test_cargo_package_name(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "bellman"\nversion = "0.14.0"\n'
        )
        assert collect_first_party_package_names(tmp_path) == {"bellman"}

    def test_cargo_workspace_members_at_any_depth(self, tmp_path: Path) -> None:
        """bellman's real shape: a root manifest plus ``groth16/Cargo.toml``."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = [".", "groth16"]\n\n'
            '[package]\nname = "bellman"\n'
        )
        (tmp_path / "groth16").mkdir()
        (tmp_path / "groth16" / "Cargo.toml").write_text(
            '[package]\nname = "bellman-groth16"\n'
        )
        assert collect_first_party_package_names(tmp_path) == {
            "bellman", "bellman-groth16",
        }

    def test_a_dependency_table_name_is_not_the_packages_own_name(
        self, tmp_path: Path
    ) -> None:
        """THE REASON ``_detect_project_binary_names`` COULD NOT BE REUSED: it
        regex-scrapes every ``name = "..."`` in the file. A dependency's name
        is not this repo's name, and admitting one here would suppress a
        genuine third-party module."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "bellman"\n\n'
            '[dependencies]\nrand = { version = "0.8", package = "rand" }\n\n'
            '[dependencies.blake2s_simd]\nversion = "1.0"\n'
        )
        assert collect_first_party_package_names(tmp_path) == {"bellman"}

    def test_go_mod_keeps_the_WHOLE_module_path(self, tmp_path: Path) -> None:
        """Not the last component. caddy's callee slots carry the full path,
        so ``caddy`` alone would match none of them."""
        (tmp_path / "go.mod").write_text(
            "module github.com/caddyserver/caddy/v2\n\ngo 1.22\n"
        )
        assert collect_first_party_package_names(tmp_path) == {
            "github.com/caddyserver/caddy/v2",
        }

    def test_package_json_name(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "express"}))
        assert collect_first_party_package_names(tmp_path) == {"express"}

    def test_vendored_manifests_cannot_leak_in(self, tmp_path: Path) -> None:
        """A vendored dependency's OWN manifest names a THIRD-PARTY package.
        Admitting it would suppress precisely the module the gate should
        report."""
        (tmp_path / "package.json").write_text(json.dumps({"name": "app"}))
        for vendor in ("node_modules", "vendor", ".venv"):
            d = tmp_path / vendor / "morgan"
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": "morgan"}))
        assert collect_first_party_package_names(tmp_path) == {"app"}

    def test_no_manifest_yields_an_empty_set(self, tmp_path: Path) -> None:
        """Unchanged behaviour on a repo with no manifest — the suppression
        simply never fires."""
        (tmp_path / "main.c").write_text("int main(void){return 0;}\n")
        assert collect_first_party_package_names(tmp_path) == set()

    def test_a_virtual_workspace_manifest_has_no_package_of_its_own(
        self, tmp_path: Path
    ) -> None:
        """The standard Rust monorepo root: ``[workspace]`` with NO ``[package]``
        table at all. It publishes nothing itself, and the members carry the
        names."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n'
        )
        (tmp_path / "crates" / "globset").mkdir(parents=True)
        (tmp_path / "crates" / "globset" / "Cargo.toml").write_text(
            '[package]\nname = "globset"\n'
        )
        assert collect_first_party_package_names(tmp_path) == {"globset"}

    def test_a_quoted_go_module_path(self, tmp_path: Path) -> None:
        """Legal though rare; the directive accepts a quoted string."""
        (tmp_path / "go.mod").write_text('module "example.com/x"\n\ngo 1.22\n')
        assert collect_first_party_package_names(tmp_path) == {"example.com/x"}

    def test_a_package_json_that_is_not_an_object(self, tmp_path: Path) -> None:
        """Valid JSON, wrong shape — must not raise."""
        (tmp_path / "package.json").write_text("[1, 2, 3]")
        assert collect_first_party_package_names(tmp_path) == set()

    @pytest.mark.parametrize("manifest,body", [
        ("Cargo.toml", "this is not : valid toml ["),
        ("go.mod", "// a comment and no module line\n"),
        ("package.json", "{not json"),
        ("package.json", '{"name": 42}'),
        ("Cargo.toml", '[package]\nversion = "1.0"\n'),
    ])
    def test_a_malformed_or_nameless_manifest_is_skipped_not_fatal(
        self, tmp_path: Path, manifest: str, body: str
    ) -> None:
        (tmp_path / manifest).write_text(body)
        assert collect_first_party_package_names(tmp_path) == set()


class TestTheGateStopsReportingTheRepositorysOwnName:
    """The positive cases — each one a real edge from the item's measurement."""

    def test_bellman_referring_to_itself_by_crate_name(self) -> None:
        dst = ("rust:bellman.VerificationError:0-0:"
               "bellman.VerificationError.InvalidProof:external_symbol")
        assert _rust_coverage([_rust_call(dst)]).complete is False
        assert _rust_coverage([_rust_call(dst)], {"bellman"}).complete is True

    @pytest.mark.parametrize("module", [
        "github.com/caddyserver/caddy/v2",
        "github.com/caddyserver/caddy/v2/caddyconfig/caddyfile",
        "github.com/caddyserver/caddy/v2/internal/metrics",
        "github.com/caddyserver/caddy/v2/modules/caddyhttp",
    ])
    def test_caddy_referring_to_itself_by_module_path(self, module: str) -> None:
        """A SUBPACKAGE of the declared module path is first-party too — that
        is what a Go module path means."""
        dst = f"go:{module}:0-0:New:external_symbol"
        packages = {"github.com/caddyserver/caddy/v2"}
        assert _go_coverage([_go_call(dst)]).complete is False
        assert _go_coverage([_go_call(dst)], packages).complete is True


class TestTheControlsThatMakeItSafe:
    """Suppression can only HIDE a real dependency, so these matter more than
    the cases above."""

    def test_a_genuine_dependency_is_still_reported(self) -> None:
        """bellman's real third-party callees, with bellman's own name
        supplied. If this ever passes, the gate has gone blind."""
        for module in ("blake2s_simd::Params", "rand", "rayon", "ff.Field"):
            dst = f"rust:{module}:0-0:new:external_symbol"
            coverage = _rust_coverage([_rust_call(dst)], {"bellman"})
            assert coverage.complete is False, module

    def test_the_match_is_component_bounded(self) -> None:
        """``caddyserver`` and ``bellmanx`` are plausible crate names. A
        ``startswith`` rule swallows both."""
        assert _rust_coverage(
            [_rust_call("rust:bellmanx::Client:0-0:connect:external_symbol")],
            {"bellman"},
        ).complete is False
        assert _go_coverage(
            [_go_call("go:github.com/caddyserver/caddyx:0-0:New:external_symbol")],
            {"github.com/caddyserver/caddy/v2"},
        ).complete is False

    def test_an_empty_package_set_changes_nothing(self) -> None:
        """The default. Every existing caller keeps its behaviour exactly."""
        dst = ("rust:bellman.VerificationError:0-0:"
               "bellman.VerificationError.InvalidProof:external_symbol")
        assert _rust_coverage([_rust_call(dst)], set()).complete is False
        assert _rust_coverage([_rust_call(dst)], None).complete is False


class TestTheItemsOwnRepositories:
    """Read the two real manifests the item measured. Skipped when the corpus
    is absent so the suite stays hermetic."""

    @pytest.mark.parametrize("repo,expected", [
        ("optattest_repos/bellman", "bellman"),
        ("curriculum_repos/caddy", "github.com/caddyserver/caddy/v2"),
    ])
    def test_the_published_name_is_recovered(self, repo: str, expected: str) -> None:
        root = Path.home() / "ALL_REPOS" / repo
        if not root.is_dir():  # pragma: no cover - corpus-dependent
            pytest.skip(f"corpus repo absent: {repo}")
        assert expected in collect_first_party_package_names(root)
