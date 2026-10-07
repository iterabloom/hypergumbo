# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoped (per-package-scheme) dependency manifests: Hex (Mix) and npm.

Covers the module/specifier -> package name rules in ``supply_chain`` and the
``DependencyManifest.directness_for`` language dispatch that
``ir.create_boundary_nodes`` calls (WI-juzaj).
"""
from __future__ import annotations

from hypergumbo_core.ir import Edge, Span, Symbol, create_boundary_nodes
from hypergumbo_core.supply_chain import (
    DependencyManifest,
    hex_module_package,
    macro_underscore,
    npm_specifier_package,
)


class TestMacroUnderscore:
    def test_simple_camel(self) -> None:
        assert macro_underscore("LiveView") == "live_view"

    def test_acronym_run_then_word(self) -> None:
        # Elixir's own quirk: Macro.underscore("HTTPoison") == "htt_poison"
        # (hence the HTTPoison entry in the exceptions table).
        assert macro_underscore("HTTPoison") == "htt_poison"
        assert macro_underscore("DNSCluster") == "dns_cluster"

    def test_all_caps(self) -> None:
        assert macro_underscore("JOSE") == "jose"
        assert macro_underscore("HTML") == "html"

    def test_trailing_acronym(self) -> None:
        assert macro_underscore("NimbleTOTP") == "nimble_totp"

    def test_digits(self) -> None:
        assert macro_underscore("Argon2") == "argon2"


class TestHexModulePackage:
    PKGS = frozenset({
        "phoenix", "phoenix_live_view", "phoenix_html", "phoenix_pubsub",
        "ecto", "ecto_sql", "plug", "httpoison", "stripity_stripe", "jose",
        "telemetry", "decimal",
    })

    def test_longest_underscored_prefix_wins(self) -> None:
        assert hex_module_package("Phoenix.LiveView", self.PKGS) == "phoenix_live_view"
        assert hex_module_package("Phoenix.LiveView.JS", self.PKGS) == "phoenix_live_view"
        assert hex_module_package("Phoenix.HTML.Form", self.PKGS) == "phoenix_html"

    def test_falls_back_to_shorter_prefix(self) -> None:
        assert hex_module_package("Ecto.Changeset", self.PKGS) == "ecto"
        assert hex_module_package("Plug.Conn", self.PKGS) == "plug"
        assert hex_module_package("Phoenix.Controller", self.PKGS) == "phoenix"

    def test_curated_exceptions(self) -> None:
        assert hex_module_package("Phoenix.Component", self.PKGS) == "phoenix_live_view"
        assert hex_module_package("Phoenix.PubSub", self.PKGS) == "phoenix_pubsub"
        assert hex_module_package("HTTPoison", self.PKGS) == "httpoison"
        assert hex_module_package("Stripe.Account", self.PKGS) == "stripity_stripe"
        assert hex_module_package("Ecto.Adapters.SQL.Sandbox", self.PKGS) == "ecto_sql"

    def test_fully_qualified_elixir_prefix(self) -> None:
        assert hex_module_package("Elixir.Stripe.PaymentIntent", self.PKGS) == "stripity_stripe"
        assert hex_module_package("Elixir.", self.PKGS) is None

    def test_exception_ignored_when_package_not_in_manifest(self) -> None:
        # Bcrypt -> bcrypt_elixir only when bcrypt_elixir is a dependency.
        assert hex_module_package("Bcrypt", self.PKGS) is None

    def test_erlang_atom_module_exact_only(self) -> None:
        assert hex_module_package("telemetry", self.PKGS) == "telemetry"
        assert hex_module_package("jose_jwk", self.PKGS) is None
        assert hex_module_package("ets", self.PKGS) is None

    def test_unknown_module_is_none(self) -> None:
        assert hex_module_package("Enum", self.PKGS) is None
        assert hex_module_package("MyApp.Repo", self.PKGS) is None
        assert hex_module_package("", self.PKGS) is None
        assert hex_module_package("external", self.PKGS) is None


class TestNpmSpecifierPackage:
    def test_bare(self) -> None:
        assert npm_specifier_package("lodash") == "lodash"

    def test_subpath(self) -> None:
        assert npm_specifier_package("lodash/fp") == "lodash"
        assert npm_specifier_package("tailwindcss/plugin") == "tailwindcss"

    def test_scoped(self) -> None:
        assert npm_specifier_package("@scope/name") == "@scope/name"
        assert npm_specifier_package("@scope/name/sub/path") == "@scope/name"

    def test_version_suffix(self) -> None:
        assert npm_specifier_package("lit@3") == "lit"
        assert npm_specifier_package("@std/foo@1.2") == "@std/foo"

    def test_relative_and_absolute_are_none(self) -> None:
        assert npm_specifier_package("./utils") is None
        assert npm_specifier_package("../x/y") is None
        assert npm_specifier_package("/abs/path") is None

    def test_degenerate(self) -> None:
        assert npm_specifier_package("") is None
        assert npm_specifier_package("@scope") is None
        assert npm_specifier_package("external") is None


class TestDirectnessFor:
    def _manifest(self) -> DependencyManifest:
        return DependencyManifest(
            entries={"github.com/foo/bar": {"direct": True}},
            scoped={
                "hex": {"phoenix": {"direct": True}, "plug": {"direct": False}},
                "npm": {"leaflet": {"direct": True}, "@babel/core": {"direct": False}},
            },
        )

    def test_legacy_language_uses_flat_entries(self) -> None:
        m = self._manifest()
        assert m.directness_for("go", "github.com/foo/bar/baz") == "direct"
        assert m.directness_for("go", "encoding/json") == "undeclared"

    def test_legacy_language_without_flat_entries_is_unknown(self) -> None:
        # A Mix-only manifest must not make Python imports read "undeclared".
        m = DependencyManifest(scoped={"hex": {"phoenix": {"direct": True}}})
        assert m.directness_for("python", "requests") is None

    def test_elixir(self) -> None:
        m = self._manifest()
        assert m.directness_for("elixir", "Phoenix.Controller") == "direct"
        assert m.directness_for("elixir", "Plug.Conn") == "transitive"
        assert m.directness_for("elixir", "Enum") is None

    def test_javascript_and_typescript(self) -> None:
        m = self._manifest()
        assert m.directness_for("javascript", "leaflet") == "direct"
        assert m.directness_for("typescript", "@babel/core/lib/x") == "transitive"
        assert m.directness_for("javascript", "./utils") is None
        assert m.directness_for("javascript", "react") is None

    def test_scheme_without_entries_is_unknown(self) -> None:
        m = DependencyManifest(scoped={"npm": {"leaflet": {"direct": True}}})
        assert m.directness_for("elixir", "Phoenix") is None

    def test_language_without_manifest_support(self) -> None:
        assert self._manifest().directness_for("lua", "redis") is None

    def test_merge_unions_scoped_and_direct_dominates(self) -> None:
        a = DependencyManifest(scoped={"npm": {"x": {"direct": False}}})
        b = DependencyManifest(scoped={"npm": {"x": {"direct": True}, "y": {"direct": False}}})
        c = DependencyManifest(scoped={"npm": {"x": {"direct": False}}})
        merged = DependencyManifest.merge([a, b, c])
        assert merged.scoped["npm"]["x"] == {"direct": True}
        assert merged.scoped["npm"]["y"] == {"direct": False}


def _caller() -> Symbol:
    return Symbol(
        id="elixir:lib/a.ex:1-2:f:function", name="f", kind="function",
        language="elixir", path="lib/a.ex",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
    )


class TestBoundaryStamping:
    def test_elixir_boundary_gets_directness_and_third_party(self) -> None:
        s = _caller()
        e1 = Edge.create(src=s.id, dst="elixir:Phoenix.Controller:0-0:json:unresolved",
                         edge_type="calls", line=1,
                         origin="test", origin_run_id="test")
        e2 = Edge.create(src=s.id, dst="elixir:Enum:0-0:map:unresolved",
                         edge_type="calls", line=1,
                         origin="test", origin_run_id="test")
        manifest = DependencyManifest(scoped={"hex": {"phoenix": {"direct": True}}})
        nodes, _ = create_boundary_nodes(
            [s], [e1, e2], dependency_manifest=manifest,
            ecosystem_classifier=lambda lang, mod: None,
        )
        by_path = {n.display_label.split(":")[1]: n for n in nodes}
        assert by_path["Phoenix.Controller"].meta["directness"] == "direct"
        assert by_path["Phoenix.Controller"].meta["ecosystem"] == "third_party"
        # Unmapped module: no guess on either axis.
        assert "directness" not in by_path["Enum"].meta
        assert "ecosystem" not in by_path["Enum"].meta

    def test_catalog_ecosystem_not_overridden(self) -> None:
        s = _caller()
        e = Edge.create(src=s.id, dst="elixir:Phoenix:0-0:x:unresolved",
                        edge_type="calls", line=1,
                        origin="test", origin_run_id="test")
        manifest = DependencyManifest(scoped={"hex": {"phoenix": {"direct": False}}})
        nodes, _ = create_boundary_nodes(
            [s], [e], dependency_manifest=manifest,
            ecosystem_classifier=lambda lang, mod: "stdlib",
        )
        assert nodes[0].meta["directness"] == "transitive"
        assert nodes[0].meta["ecosystem"] == "stdlib"
