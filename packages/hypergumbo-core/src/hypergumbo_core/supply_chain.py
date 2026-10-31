# SPDX-License-Identifier: AGPL-3.0-or-later
"""Supply chain classification for code analysis.

Classifies nodes into tiers by the supply-chain distance, from the project,
of the code each stands for -- the project being every package whose source
is in the analysed tree (ADR-0041 §1). This enables focused analysis
(first-party code prioritized) and noise reduction (derived artifacts
excluded).

Tiers
-----
- FIRST_PARTY (1): The project's own code, workspace siblings included
  (highest priority)
- INTERNAL_DEP (2): Org-internal code that is not part of the project
  (``internal_package_roots`` when a caller passes a ``SupplyChainConfig`` —
  see "Configuration" below; no CLI path does)
- EXTERNAL_DEP (3): Third-party dependencies in readable form
- DERIVED (4): Build artifacts, transpiled/bundled output (skip analysis)

Classification Algorithm
------------------------
Classification runs after analysis, over the symbols: ``cli._classify_symbols``
calls :func:`classify_file` once per file path and stamps every symbol in that
file. Signals are checked in order; first match wins:

1. Derived artifact detection (tier 4) - ``derived_patterns``, then
   generated output that discovery's build-output content rules recognise
   (``_build_output_root``: ``target/`` beside ``Cargo.toml``, ``site/``
   beside ``mkdocs.yml`` ... -- never a bare ``dist``/``build``/``out``/
   ``target`` name), then the remaining path patterns (dot-named tool
   directories, ``__pycache__``) and filename patterns, then a content check
   for minified output (also generated-file declarations, C-family generator
   fingerprints, and a webpack bootstrap)
2. External dependency detection (tier 3) - root-anchored node_modules/,
   vendor/, third_party/ (and the third-party/, thirdparty/ spellings),
   external/, deps/, Pods/, Carthage/, .yarn/cache/, _vendor/
   (``EXTERNAL_DEP_PATTERNS``); then nested node_modules/, vendor/,
   bower_components/ and vendoring markers such as vendored Go SDKs anywhere
   in the path (``EXTERNAL_DEP_DEEP_PATTERNS``); then installed dependency
   source that discovery's content rules recognise
   (``_installed_dependency_root`` asks ``discovery.match_content_rule``),
   which reaches classification only when traced via ``--trace-deps``
   (ADR-0004) - e.g. a virtualenv's site-packages or a nested ``server/deps/``
3. Example/demo detection (tier 1, is_example=True) - examples/, demos/,
   samples/, tutorials/ (in-repo → first-party; the role is on is_example)
4. Documentation detection (tier 1) - in-repo docs; the role is carried by
   the reason string (INV-naduh)
5. ``SupplyChainConfig.internal_package_roots`` → tier 2 (the only tier-2
   producer)
6. Workspace package detection:
   - If file is in a test directory, or matches a test file pattern beside
     its source → tier 1 with is_test=True
   - Otherwise → tier 1 (workspace IS the project)
7. Test code detection (tier 1 with is_test=True) - language-specific
   overrides first, then tests/, spec/, __tests__/, _test.go, .test.js, etc.
   Routing tests through tier 2 historically made tier 2 a synonym for
   is_test and drowned out the real internal-dep signal (INV-tisid).
8. Notebook (.ipynb) and fuzz/bench detection (tier 1) - in-repo role files;
   the role is carried by the reason string (INV-naduh).
9. First-party detection (tier 1) - ``first_party_patterns``, then
   src/, lib/, app/, pkg/, cmd/, internal/, crates/*/src/, packages/*/src/,
   or default

Per INV-naduh / ADR-0041 §1 the tier names supply-chain DISTANCE only: all
in-repo files are first-party (distance 0), and tier 2 is reserved for
org-internal *dependency* packages. Role files (examples, docs, notebooks,
fuzz/bench, tests) carry their role on a separate axis (is_example / is_test
/ reason), not by tier.

Configuration
-------------
The ``SupplyChainConfig`` arms (``derived_patterns``,
``internal_package_roots``, ``first_party_patterns``) are honoured in steps
1, 5 and 9 when a caller passes ``classify_file(config=...)``, but as of
2026-10 no CLI flag or config key feeds them: its one production caller,
``cli._classify_symbols`` (which ``sketch._run_analysis`` also goes through),
passes no ``config``, and ``.hypergumbo.toml`` rejects these keys as unknown
settings, so a CLI run emits no tier-2 node.

Dependency Declarations
-----------------------
A ``kind='dependency'`` node is not a file of the project: it stands for the
package it names. ``cli._classify_symbols`` gives a declaration naming an
in-tree package (:func:`collect_in_tree_packages`) the tier that package's
manifest classifies to, and every other declaration tier 3, so the declaration
and the package node it names carry one tier (INV-kutub, ADR-0041 §1). It
also stamps each external declaration's ADR-0041 §3 ``ecosystem``
(:func:`declaration_ecosystem`: ``third_party``, except composer's platform
packages, which are ``stdlib``).

Role Flags Beside the Tier
--------------------------
:func:`classify_file` stamps two more flags after the tier walk, independent
of which step matched:

- ``is_generated`` (WI-tizij / WI-pofin) - a path match against
  ``GENERATED_CODE_PATTERNS`` (OpenAPI models, protobuf stubs, k8s code-gen,
  go-swagger, openapi-gen TS), else a declaration in the file's leading
  comment block within the first 4 KiB, or a C-family generator's
  structural fingerprint (``_generator_fingerprint``: tree-sitter, Bison,
  flex, re2c). The header tests, ``_has_generated_marker`` and
  ``_generator_fingerprint``, are shared with the minified-content check in
  step 1 so the flag and tier 4 cannot disagree (INV-bokim / INV-pogob).
- ``is_config`` (WI-jobuj) - the basename is a package-manager or build
  manifest in ``CONFIG_FILE_NAMES``; suppressed when ``is_test`` or
  ``is_example`` is already set, so the three role flags stay exclusive.

Manifest Readers
----------------
- :func:`detect_package_roots` reads the root ``package.json``
  ``workspaces`` key (npm and yarn; a pnpm ``pnpm-workspace.yaml`` is not
  read), Cargo workspace members, Maven modules and Gradle ``include`` lines
  into the workspace roots that step 6 consults.
- :class:`DependencyManifest` holds what a language analyzer parsed from
  go.mod / Maven / Gradle / Python manifests (the flat ``entries``) and from
  Mix and npm manifests (the per-package-scheme ``scoped`` tables, WI-juzaj).
  Per ADR-0041 §1 it no longer sets tier (every boundary node is tier 3);
  ``directness_for(language, module)`` returns ``direct`` / ``transitive`` /
  ``undeclared`` (or ``None`` when it cannot tell), and
  ``ir.create_boundary_nodes`` stamps that on the ``directness`` meta key.
- :func:`collect_first_party_package_names` (INV-vivok) reads the names the
  repo publishes itself under (Cargo ``[package].name``, the go.mod module
  path, ``package.json`` ``name``) so ``verify-claims`` stops reporting a
  repo's self-references by published name as uncatalogued third-party
  modules. Its per-manifest form, :func:`own_package_name`, lets js_module,
  js_ts and the Rust ``use`` resolver map a workspace sibling's import to
  that sibling's directory.

Vendored-Code Disclosure and Path-Only Tiering
----------------------------------------------
Committed vendored code is analysed as tier 3 but left out of the summaries
(ADR-0004 point 3a), so these helpers disclose it:

- :func:`vendored_root` returns the vendored directory an EXTERNAL_DEP_*
  pattern matched for a path (``pkg/vendor``), else ``None``.
- :func:`vendored_analysed_summary` builds
  ``supply_chain_summary.vendored_analysed`` from the tier-3 ``file`` nodes:
  one entry per vendored directory with its languages and file count,
  sorted by path and capped at 10.
- :func:`format_vendored_disclosure` renders that summary as the one-line
  disclosure (at most three directories named) that sketch and the CLI print.
- :func:`tier_by_path` runs only the path-pattern steps of
  :func:`classify_file` (DERIVED_* then EXTERNAL_DEP_*), with no file read,
  for cheap summaries such as the profile's per-language own-code counts.
  ``None`` means the path alone does not decide the tier, so it under-reports
  tiers 3-4 rather than inventing them.

See §14 of the hypergumbo spec for full details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Container, Iterable, Iterator, Optional


class Tier(IntEnum):
    """Supply-chain distance, from the project, of the code a node stands for;
    lower = closer = higher priority (ADR-0041 §1). Tiers 1-3 are distances;
    DERIVED (4) is the derived-artifact exception."""

    FIRST_PARTY = 1
    INTERNAL_DEP = 2
    EXTERNAL_DEP = 3
    DERIVED = 4


@dataclass
class DependencyManifest:
    """Language-agnostic dependency manifest for tier classification of boundary nodes.

    Maps module paths (e.g., Go module paths from go.mod, npm package names
    from package.json) to dependency metadata. Used by ``create_boundary_nodes``
    to classify synthetic boundary nodes that represent unresolved external
    references — all tier 3 (``EXTERNAL_DEP``) per ADR-0041 (see below), with the
    direct/transitive distinction recorded on the ``directness`` meta key.

    Entries map module path strings to dicts with at least a ``direct`` bool key.

    ADR-0041 §1/§2 (supply:F5): the manifest no longer influences *tier*. Tier
    names supply-chain distance only, so every third-party import is tier 3
    (``classify_import`` is now constant ``EXTERNAL_DEP``). The direct/transitive
    declaration relationship the old mapping burned into tier 2 is exposed by
    ``classify_directness`` and recorded on the ``directness`` meta key instead.
    Tier 2 (``internal_dep``) is org-internal code outside the project,
    assigned by file classification — never by this manifest classifier.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    scoped: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    """Per-package-scheme tables (WI-juzaj): ``{"hex": {...}, "npm": {...}}``.

    Each maps a *package name* (a Mix dep / mix.lock key, an npm package name)
    to ``{"direct": bool}``. They are kept apart from the flat ``entries``
    because those are matched by raw longest-prefix against every legacy
    language's import path, and a bare npm name (``yaml``, ``redis``) would
    then stamp an unrelated Python import of the same spelling. A scoped table
    is consulted only for the languages :data:`LANGUAGE_PACKAGE_SCHEME` routes
    to it, after the scheme's own name rule maps the boundary's module path to
    a package name.
    """

    def classify_import(self, import_path: str) -> "Tier":
        """Classify the supply-chain *tier* of an external import path.

        ADR-0041 §1: tier names supply-chain distance and nothing else, so
        every third-party / external import — direct, transitive, stdlib, or
        unknown alike — is :data:`Tier.EXTERNAL_DEP` (3). Boundary nodes are
        external by construction; first-party code (tier 1, workspace
        siblings included) and org-internal code (tier 2) are assigned by file
        classification, not here.

        The direct/transitive/undeclared *declaration relationship* this method
        used to fold into tier 2 now lives on :meth:`classify_directness`.

        Returns:
            Tier.EXTERNAL_DEP (3), always.
        """
        return Tier.EXTERNAL_DEP

    def classify_directness(self, import_path: str) -> str:
        """Classify the declaration relationship of an external import (ADR-0041 §2).

        Records how the project's manifests relate to an external dependency:

        * ``"direct"`` — declared in a project manifest (longest-prefix match
          carries ``direct: True``);
        * ``"transitive"`` — present in the manifest but not declared direct
          (pulled in by another dependency);
        * ``"undeclared"`` — imported but declared in no manifest (a phantom
          dependency; also the bucket the language runtime's stdlib falls into,
          since stdlib is declared nowhere — the stdlib-vs-third_party split is
          the separate ``ecosystem`` axis, ADR-0041 §3).

        Matching mirrors the old tier classifier's longest-prefix logic across
        slash-separated (Go: ``github.com/foo/bar``) and dot-separated
        (Java/Kotlin: ``com.fasterxml.jackson.core``) module paths.
        """
        best_match = ""
        for module_path in self.entries:
            if (
                import_path == module_path
                or import_path.startswith(module_path + "/")
                or import_path.startswith(module_path + ".")
            ):
                if len(module_path) > len(best_match):
                    best_match = module_path

        if not best_match:
            return "undeclared"
        return "direct" if self.entries[best_match].get("direct", False) else "transitive"

    def directness_for(self, language: str, module: str) -> Optional[str]:
        """Directness of a boundary node of ``language`` at ``module``, or None.

        The single dispatch ``ir.create_boundary_nodes`` calls (WI-juzaj):

        * Go / Java / Kotlin / Python match the flat ``entries`` by
          :meth:`classify_directness` -- but only when some manifest produced
          flat entries. Without one, "undeclared" would be a claim made from
          the absence of a file we never read (a Mix-only repo's one Python
          script would otherwise read as importing phantom dependencies).
        * Elixir / JavaScript / TypeScript map ``module`` to a package name by
          their scheme's rule (:func:`hex_module_package`,
          :func:`npm_specifier_package`) and look it up in ``scoped``.
          A module that maps to no declared or locked package returns None,
          NOT "undeclared": those boundary paths also carry unresolved
          in-repo references (an un-expanded alias, a relative import the
          analyzer stripped to a bare name, the ``external`` placeholder), so
          "declared nowhere" cannot be told apart from "not a package".
        * Every other language: None.
        """
        if language in LEGACY_MANIFEST_LANGUAGES:
            if not self.entries:
                return None
            return self.classify_directness(module)
        scheme = LANGUAGE_PACKAGE_SCHEME.get(language)
        if scheme is None:
            return None
        packages = self.scoped.get(scheme)
        if not packages:
            return None
        package = _SCHEME_RESOLVERS[scheme](module, packages)
        if package is None:
            return None
        return "direct" if packages[package].get("direct", False) else "transitive"

    @classmethod
    def merge(cls, manifests: list["DependencyManifest"]) -> "DependencyManifest":
        """Merge multiple manifests into one.

        Later entries override earlier ones for the same module path in the
        flat ``entries``. Scoped tables union, and a package any manifest
        declares direct stays direct (one package.json declaring ``ws`` makes
        it direct even if another workspace only has it in a lockfile).
        """
        merged: dict[str, dict[str, Any]] = {}
        scoped: dict[str, dict[str, dict[str, Any]]] = {}
        for m in manifests:
            merged.update(m.entries)
            for scheme, packages in m.scoped.items():
                table = scoped.setdefault(scheme, {})
                for name, info in packages.items():
                    direct = bool(info.get("direct", False)) or bool(
                        table.get(name, {}).get("direct", False)
                    )
                    table[name] = {"direct": direct}
        return cls(entries=merged, scoped=scoped)


#: Languages whose boundary paths are matched against the flat ``entries``.
LEGACY_MANIFEST_LANGUAGES = frozenset({"go", "java", "kotlin", "python"})

#: Language -> package scheme of its scoped manifest table (WI-juzaj).
LANGUAGE_PACKAGE_SCHEME: dict[str, str] = {
    "elixir": "hex",
    "javascript": "npm",
    "typescript": "npm",
}

_UNDERSCORE_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_UNDERSCORE_WORD_RE = re.compile(r"([a-z\d])([A-Z])")


def macro_underscore(segment: str) -> str:
    """Elixir's ``Macro.underscore`` for one module-name segment.

    ``LiveView`` -> ``live_view``, ``HTTPoison`` -> ``htt_poison`` (sic --
    Elixir's own result, which is why HTTPoison is an exception below),
    ``NimbleTOTP`` -> ``nimble_totp``, ``JOSE`` -> ``jose``. This is the rule
    Mix itself uses to turn ``Phoenix.LiveView`` into the app name
    ``phoenix_live_view``, which is why it is the first-choice mapping.
    """
    out = _UNDERSCORE_ACRONYM_RE.sub(r"\1_\2", segment)
    out = _UNDERSCORE_WORD_RE.sub(r"\1_\2", out)
    return out.lower()


#: Module namespaces whose Hex package is NOT the underscored namespace.
#: Consulted before the underscore rule at each prefix length, and only
#: honoured when the named package is actually in the project's manifest, so
#: an entry here can never introduce a dependency the project does not have.
HEX_MODULE_PACKAGE_EXCEPTIONS: dict[str, str] = {
    "Phoenix.Component": "phoenix_live_view",
    "Phoenix.LiveComponent": "phoenix_live_view",
    "Phoenix.LiveViewTest": "phoenix_live_view",
    "Phoenix.PubSub": "phoenix_pubsub",
    "Phoenix.PubSub.Redis": "phoenix_pubsub_redis",
    "Ecto.Adapters.SQL": "ecto_sql",
    "Ecto.Migration": "ecto_sql",
    "Ecto.Migrator": "ecto_sql",
    "HTTPoison": "httpoison",
    "Bcrypt": "bcrypt_elixir",
    "Argon2": "argon2_elixir",
    "Pbkdf2": "pbkdf2_elixir",
    "Stripe": "stripity_stripe",
    "Cluster": "libcluster",
    "EQRCode": "eqrcode",
}


def hex_module_package(module: str, packages: Container[str]) -> Optional[str]:
    """Map an Elixir/Erlang module reference to a Mix package, or None.

    Conservative by construction -- an answer is always a name in
    ``packages`` (the project's mix.exs deps plus its mix.lock keys), never a
    synthesised guess:

    * An Erlang atom module (``:telemetry`` arrives as ``telemetry``) matches
      only a package of exactly that name. Erlang libraries routinely ship
      modules prefixed by, but not named after, their app (``jose_jwk`` in
      ``jose``), and prefix-guessing those would misattribute.
    * An Elixir alias is tried from its longest dotted prefix down: at each
      length first :data:`HEX_MODULE_PACKAGE_EXCEPTIONS`, then the
      ``Macro.underscore`` join (``Phoenix.LiveView.JS`` -> try
      ``phoenix_live_view_js``, then ``phoenix_live_view``). Longest-first
      matters because ``phoenix`` is also a package.
    """
    if module.startswith("Elixir."):
        # The fully-qualified atom form (``Elixir.Stripe.PaymentIntent``).
        module = module[len("Elixir."):]
    if not module or module == "external":
        return None
    if module[0].islower():
        return module if module in packages else None
    segments = module.split(".")
    for end in range(len(segments), 0, -1):
        prefix = ".".join(segments[:end])
        exception = HEX_MODULE_PACKAGE_EXCEPTIONS.get(prefix)
        if exception is not None and exception in packages:
            return exception
        candidate = "_".join(macro_underscore(seg) for seg in segments[:end])
        if candidate in packages:
            return candidate
    return None


def npm_specifier_package(specifier: str) -> Optional[str]:
    """Map a JS/TS import specifier to its npm package name, or None.

    ``lodash/fp`` -> ``lodash``; ``@scope/name/sub`` -> ``@scope/name``;
    ``lit@3`` (a Deno ``npm:`` specifier after the analyzer strips the
    scheme) -> ``lit``. Relative and absolute paths are not packages. The
    analyzer has already stripped ``node:`` from built-ins, so ``fs`` comes
    through as a bare name and simply matches no declared package.
    """
    if not specifier or specifier.startswith((".", "/")) or specifier == "external":
        return None
    parts = specifier.split("/")
    if specifier.startswith("@"):
        if len(parts) < 2 or not parts[1]:
            return None
        name = f"{parts[0]}/{parts[1]}"
        at = name.find("@", 1)
    else:
        name = parts[0]
        at = name.find("@")
    if at > 0:
        name = name[:at]
    return name or None


def _npm_resolver(module: str, packages: Container[str]) -> Optional[str]:
    name = npm_specifier_package(module)
    return name if name is not None and name in packages else None


_SCHEME_RESOLVERS: dict[str, Callable[[str, Container[str]], Optional[str]]] = {
    "hex": hex_module_package,
    "npm": _npm_resolver,
}


@dataclass
class SupplyChainConfig:
    """Configuration for supply chain classification.

    Allows customizing tier classification when passed as
    ``classify_file(config=...)``. No production caller constructs one as of
    2026-10 (see the module docstring's "Configuration" section).

    Attributes:
        first_party_patterns: Additional patterns to classify as tier 1
        derived_patterns: Additional patterns to classify as tier 4
        internal_package_roots: Explicit internal package paths
    """

    first_party_patterns: list[str] = field(default_factory=list)
    derived_patterns: list[str] = field(default_factory=list)
    internal_package_roots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "first_party_patterns": self.first_party_patterns,
            "derived_patterns": self.derived_patterns,
            "internal_package_roots": self.internal_package_roots,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplyChainConfig":
        """Parse from dict."""
        return cls(
            first_party_patterns=data.get("first_party_patterns", []),
            derived_patterns=data.get("derived_patterns", []),
            internal_package_roots=data.get("internal_package_roots", []),
        )


@dataclass
class FileClassification:
    """Classification result for a file.

    `tier` and the boolean role flags (`is_test`, `is_example`,
    `is_config`, `is_generated`) are independent axes (WI-rigun-patuz,
    WI-jobuj). Per INV-tisid, test code is tier 1 with is_test=True —
    tests are first-party code, and routing them through tier 2 made
    tier 2 a synonym for is_test (~99% of self-analysis tier-2 entries
    were tests, drowning out actual internal-dep signal). Per INV-naduh /
    ADR-0041 §1, tier 2 is reserved for org-internal *dependency* packages
    (``SupplyChainConfig.internal_package_roots``, when a caller passes a
    config); other in-repo role files
    — examples, docs, notebooks, fuzz/bench harnesses — are first-party
    (tier 1) with the role carried by the flag/reason, not by tier.

    At most one of `is_test`, `is_example`, `is_config` is True per file
    (mutual exclusion preserved by the classifier order); the property is
    tier-independent now that role files are first-party.
    """

    tier: Tier
    reason: str
    package_name: Optional[str] = None
    is_test: bool = False
    is_example: bool = False
    is_config: bool = False
    is_generated: bool = False


# Path patterns for tier inference (checked as prefixes on relative path).
# Only names no project uses for source. ``dist/`` / ``build/`` / ``out/`` /
# ``target/`` are NOT here: whether one is generated output is decided by the
# tool that writes it (discovery.BUILD_OUTPUT_RULES, asked through
# ``_build_output_root``) -- qemu's root ``target/`` is 653 files of CPU
# emulation source, and matching it by name made all of it tier 4.
DERIVED_PATH_PATTERNS = [
    r"^\.next/",
    r"^\.nuxt/",
    r"^\.output/",
    r"^\.svelte-kit/",
    r"^\.build/",         # Swift Package Manager build artifacts
    r"^__pycache__/",
    r"/__pycache__/",
]

DERIVED_FILENAME_PATTERNS = [
    r"\.min\.js$",
    r"\.min\.css$",
    r"\.bundle\.js$",
    r"\.compiled\.js$",
    r"\.pyc$",
    r"\.pyo$",
    # Protobuf/gRPC code generation artifacts
    r"\.serde\.rs$",        # Rust prost-build generated serde implementations
    r"\.pb\.go$",           # Go protobuf generated code
    r"_pb2\.py$",           # Python protobuf generated code
    r"_pb2_grpc\.py$",      # Python gRPC generated code
]

# WI-tizij: patterns for generated code (OpenAPI/Swagger, protobuf, etc.)
# These files are structurally central but have low developer relevance.
# The is_generated flag lets ranking apply a centrality penalty.
GENERATED_CODE_PATTERNS = [
    # OpenAPI/Swagger generated Python models (kserve, kubernetes-client, etc.)
    r"^(?:.*/)?(v\d+(?:alpha\d+|beta\d+)?_\w+)\.py$",  # v1alpha1_foo.py, v1beta1_bar.py, v1_baz.py
    r"^(?:.*/)?knative_\w+\.py$",                        # knative_foo.py
    # Protobuf/gRPC (already DERIVED tier, but also mark generated)
    r"\.pb\.go$",
    r"_pb2\.py$",
    r"_pb2_grpc\.py$",
    r"\.serde\.rs$",
    # OpenAPI generated Go clients
    r"^(?:.*/)?zz_generated\.\w+\.go$",                  # Kubernetes code-gen
    r"^(?:.*/)?mock_\w+\.go$",                           # mockgen output
    # WI-sozah: go-swagger output layouts. Conventional alertmanager-style
    # ``api/vN/{restapi,models}/`` directory hierarchies hold generated server
    # stubs and DTOs. The catch-all is anchored at ``api/v\d+/(restapi|models)/``
    # so first-party ``models/`` directories without an ``api/vN/`` parent are
    # not falsely flagged.
    r"(?:^|/)api/v\d+/(?:restapi|models)/",
    # WI-sozah: go-swagger fingerprint files. These names are unambiguous
    # — the tool always emits them under the chosen output package — so
    # they can be matched at any depth without needing the ``api/vN/`` anchor.
    r"(?:^|/)restapi/embedded_spec\.go$",
    r"(?:^|/)restapi/configure_[^/]+\.go$",
    r"(?:^|/)restapi/server\.go$",
    r"(?:^|/)restapi/doc\.go$",
    # WI-vubad: openapi-codegen TypeScript SDK output. Airflow, FastAPI,
    # and many Python web projects ship a generated TS client under
    # ``openapi-gen/`` (sometimes ``openapi/`` or ``api-client/``). The
    # prospector evidence from WI-tubot 2026-04-11 showed ~200 candidate
    # dead-code functions per airflow run came from
    # ``airflow-core/src/airflow/api_fastapi/auth/managers/simple/ui/
    # openapi-gen/requests/core/{request,CancelablePromise}.ts`` alone.
    # These files are generated by openapi-codegen / openapi-typescript
    # and are effectively unreachable via hypergumbo's normal call
    # graph — they should be is_generated=True and demoted out of
    # dead-code candidate ranking.
    r"(?:^|/)openapi-gen/.*\.(?:ts|tsx|js|jsx|mjs|cjs)$",
]

EXTERNAL_DEP_PATTERNS = [
    (r"^node_modules/", "node_modules/"),
    (r"^vendor/", "vendor/"),
    (r"^third_party/", "third_party/"),
    (r"^third-party/", "third-party/"),
    (r"^thirdparty/", "thirdparty/"),
    (r"^external/", "external/"),
    (r"^deps/", "deps/"),
    (r"^Pods/", "Pods/"),
    (r"^Carthage/", "Carthage/"),
    (r"^\.yarn/cache/", ".yarn/cache/"),
    (r"^_vendor/", "_vendor/"),
]

# Patterns matched with re.search (anywhere in path) for vendored SDKs
# that live inside subdirectories rather than at the repo root.
# Common in Go monorepos where cloud providers embed SDK copies.
# INV-mogud: cluster-autoscaler has 19K+ nodes from these SDKs classified
# as tier-1 because they don't match the root-anchored patterns above.
EXTERNAL_DEP_DEEP_PATTERNS = [
    (r"(?:^|/)[^/]+-sdk-go(?:-[^/]+)?/", "vendored Go SDK"),
    (r"(?:^|/)[^/]+-go-sdk/", "vendored Go SDK"),
    (r"(?:^|/)[^/]+-sdk-golang/", "vendored Go SDK"),
    (r"(?:^|/)vendor-internal/", "vendor-internal/"),
    # INV-kokik: vendored third-party front-end assets nested under a
    # first-party root (typically src/.../static/). These marker directory
    # names are unambiguous vendoring signals (same rationale as
    # vendor-internal/ above), so they fire even under a src/ prefix and
    # demote un-minified vendored JS/CSS out of tier-1. Deliberately NOT a
    # blanket static/ rule — many projects keep first-party JS/CSS under
    # static/; only explicit vendored-marker dirs are demoted. (Minified
    # bundles are already caught upstream by the DERIVED filename patterns.)
    (r"(?:^|/)vendored/", "vendored assets"),
    (r"(?:^|/)npm_mirror/", "vendored npm mirror"),
    (r"(?:^|/)bower_components/", "bower_components/"),
    # ADR-0004 §"Installed dependency source" point 3: a COMMITTED vendor tree
    # below the root (kata-containers ``src/runtime/vendor``, 3,282 files; a
    # ``web/node_modules`` committed for offline builds) is analysed now that
    # discovery no longer drops these names wholesale, and it is dependency
    # code wherever it sits. The root-anchored patterns above keep their
    # labels; these catch the nested remainder. Cost, stated: a first-party
    # directory that is literally named ``vendor`` below the root (a
    # marketplace app's domain module) is tier 3 rather than tier 1 -- still
    # analysed, which is more than the old name exclusion gave it.
    (r"(?:^|/)node_modules/", "node_modules/"),
    (r"(?:^|/)vendor/", "vendor/"),
]

FIRST_PARTY_PATTERNS = [
    r"^src/",
    r"^lib/",
    r"^app/",
    r"^pkg/",
    r"^cmd/",
    r"^internal/",
    r"^crates/[^/]+/src/",
    r"^packages/[^/]+/src/",
]

# Patterns for example/demo code (lower priority than workspace packages)
EXAMPLE_PATTERNS = [
    r"^examples?/",  # examples/ or example/
    r"^demos?/",     # demos/ or demo/
    r"^samples?/",   # samples/ or sample/
    r"^tutorials?/",  # tutorials/ or tutorial/
]

# WI-jobuj: dependency/build manifest filenames. Files matching these
# names are flagged with ``is_config=True`` independently of tier (see
# FileClassification). The set is intentionally narrow — canonical
# package-manager and build-tool manifests, not arbitrary dotfiles —
# so the bit means "this file declares dependencies / build config",
# not "this file happens to configure something."
CONFIG_FILE_NAMES = frozenset({
    # Python
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    # Node
    "package.json",
    "package-lock.json",
    "yarn.lock",
    # Rust
    "Cargo.toml",
    "Cargo.lock",
    # Go
    "go.mod",
    "go.sum",
    # Ruby
    "Gemfile",
    "Gemfile.lock",
    # Java / Kotlin / JVM
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    # PHP
    "composer.json",
    "composer.lock",
    # Elixir
    "mix.exs",
    "mix.lock",
})

# Patterns for documentation directories — first-party (tier 1), not production code.
# Checked with re.search to match at any depth (e.g., Sources/Lib/Documentation.docc/).
# Swift DocC (.docc) bundles contain tutorial fragments, articles, and extension files
# that look like code but are documentation content (not importable modules).
DOCUMENTATION_PATTERNS = [
    r"(?:^|/)\.docc/",              # .docc/ at any level (Swift DocC bundles)
    r"(?:^|/)Documentation\.docc/",  # Documentation.docc/ (conventional name)
]

# Patterns for fuzz targets and benchmarks — first-party (tier 1), not production code.
# Checked with re.search to match at any depth (e.g., crates/core/fuzz/).
FUZZ_BENCH_PATTERNS = [
    r"(?:^|/)fuzz(?:ing)?/",       # fuzz/ or fuzzing/ at any level
    r"(?:^|/)fuzz_targets/",       # fuzz_targets/ (cargo-fuzz convention)
    r"(?:^|/)benchmarks?/",        # benchmark/ or benchmarks/ at any level
    r"(?:^|/)benches/",            # benches/ (Rust convention) at any level
]

# Patterns for test code — checked BEFORE first-party patterns.
# Per INV-tisid, BOTH dedicated test directories AND co-located test
# files route to tier 1 (FIRST_PARTY) with is_test=True. Tier 2 is
# reserved for org-internal dependency packages (INV-naduh); a
# "test dir → tier 2" rule had made tier 2 a synonym
# for is_test (~99% of self-analysis tier-2 entries were tests).
TEST_DIR_PATTERNS = [
    r"(?:^|/)tests?/",       # tests/ or test/ at any level
    r"(?:^|/)__tests__/",    # __tests__/ (Jest convention) at any level
    r"(?:^|/)specs?/",       # spec/ or specs/ (RSpec/Jasmine) at any level
    r"(?:^|/)unit_tests?/",  # unit_tests/ or unit_test/ (C++ GTest convention)
]

# File suffix patterns: language-specific test naming conventions (co-located → tier 1)
TEST_FILE_PATTERNS = [
    r"_test\.go$",                # Go: handler_test.go
    r"\.test\.[jt]sx?$",         # JS/TS: app.test.js, app.test.tsx
    r"\.spec\.[jt]sx?$",         # JS/TS: service.spec.ts, component.spec.tsx
    r"_spec\.rb$",               # Ruby: user_spec.rb
    r"/test_[^/]+\.(?:cpp|cc|cxx|c|h|hpp)$",  # C/C++: test_utils.cpp (GTest convention)
    r"(?:^|/)test_[^/]+\.py$",   # Python: test_foo.py (pytest/unittest, WI-mozum)
    r"_test\.py$",               # Python: foo_test.py (WI-mozum)
    r"(?:^|/)test_[^/]+\.sh$",   # Bash: test_hooks.sh (WI-mozum)
    r"(?:^|/)tests\.rs$",        # Rust: co-located test module (src/consensus/tests.rs)
    r"(?:^|/)testonly\.rs$",     # Rust: test-only helpers (src/vm_executor/testonly.rs)
]

# File suffix patterns that take precedence over TEST_DIR_PATTERNS (tier 1).
# The standard TEST_FILE_PATTERNS are checked AFTER the dir patterns — that's
# correct for Go / JS / Ruby etc., where the dir pattern's "tests live in
# tests/" is uncommon (Go has none, JS uses __tests__/ which IS the test
# subtree, Ruby uses spec/). For Elixir + Phoenix, the conventional layout
# puts `*_test.exs` files at `test/<context>/<thing>_test.exs` — they ARE
# in a `test/` directory by convention, but they're still first-party
# production-adjacent code (the dir is part of the project, not vendored).
# WI-pugas (FCA finding on phoenix bakeoff 2026-05-10): 1328/1328 nodes
# at supply_chain.tier=2 on phoenix were Elixir test files, none vendored
# — tier 2 had become a synonym for "is_test" instead of "internal_dep".
# Adding the Elixir test pattern HERE (override) routes Phoenix tests to
# tier 1 with is_test=True, freeing tier 2 to mean what it should:
# "in-repo non-test internal dependency / fuzz / example / etc."
TEST_FILE_PATTERNS_DIR_OVERRIDE = [
    r"_test\.exs$",              # Elixir/Phoenix: user_test.exs
]

# Simple first-party patterns to check within workspaces
WORKSPACE_FIRST_PARTY_PATTERNS = [
    r"^src/",
    r"^lib/",
    r"^app/",
]


_GENERATED_CODE_RE = [re.compile(p) for p in GENERATED_CODE_PATTERNS]

# WI-pofin: content-based generated-code detection.
#
# Matches the canonical ``@generated`` marker (Facebook / openapi /
# various codegen tools) and the Go stdlib convention
# ``Code generated by ... DO NOT EDIT`` from go-tooling and protoc.
# The pattern is intentionally narrow: it requires the phrase to sit
# on a comment line so we don't accidentally flag a file that
# mentions the word in prose. Matches bytes (not str) so we can skip
# decoding for most files.
#
# Covered cases:
# - ``// @generated`` or ``/* @generated`` (JS/TS/Go/C/C++/Rust)
# - ``# @generated`` (Python/Ruby/YAML)
# - ``// Code generated by X. DO NOT EDIT.`` (Go stdlib convention)
# - ``# Code generated by X. DO NOT EDIT`` (Python codegen)
# - ``<!-- @generated -->`` (HTML/XML/Markdown)
#: THE ONE PREDICATE for "does this file announce itself as generated"
#: (INV-bokim + INV-pogob). Two lived here, disagreeing, and the more
#: DESTRUCTIVE consumer held the weaker rule: ``is_likely_minified``
#: heuristic 3 was an unanchored case-insensitive
#: ``(Generated by|@generated|DO NOT EDIT)`` over five lines, and it decides
#: ``Tier.DERIVED``, which drops every symbol in the file. Measured cost --
#: two hand-written first-party test files in THIS repository contributed
#: ZERO nodes to a default survey because their DOCSTRINGS mention some other
#: file being regenerated ("regenerated by" contains "generated by"); and a
#: Go probe reported 0 of 210 primitives reachable because its own fixture
#: carried ``// generated by <script>``.
#:
#: TWO HALVES, EACH LOAD-BEARING.
#:   COMMENT-ANCHORED. A generated file DECLARES itself in a comment. Prose
#:     ABOUT generated code -- a docstring, a README paragraph, a test name --
#:     is a sentence, not a declaration, and that distinction is the whole of
#:     INV-bokim.
#:   THE WEAK PHRASE NEEDS A CO-OCCURRENCE. ``generated by <tool>`` on its own
#:     is how a human annotates a checked-in artefact they still edit;
#:     ``DO NOT EDIT`` is what makes it a generator speaking. So the bare
#:     phrase is NOT a marker, while ``DO NOT EDIT`` anywhere on an anchored
#:     line is -- which also keeps the Python protobuf header
#:     ``# Generated by the protocol buffer compiler.  DO NOT EDIT!``, a real
#:     detection the previous STRICT pattern missed because it required the
#:     literal "Code generated by".
#:
#: The marker may sit anywhere on the anchored line, because real headers put
#: the tool name first (``// Code generated by protoc-gen-go. DO NOT EDIT.``).
_GENERATED_MARKER_PATTERN = re.compile(
    r"(?mi)^[ \t]*"
    r"(?://|\#|/\*+|\*|<!--|--|;)"
    r"[^\n]*?"
    r"(?:@generated|auto-?generated|automatically generated|DO NOT EDIT)",
)

#: A line that is part of a file's leading comment block.
_COMMENT_LINE_PREFIX = re.compile(r"[ \t]*(?://|\#|/\*+|\*|<!--|--|;)")

#: How far a leading comment block may run before it stops being a header. An
#: Apache-2.0 notice is 13-16 lines, GPL around 20; 60 is comfortably clear of
#: both and still unmistakably a header rather than a file.
_HEADER_COMMENT_MAX_LINES = 60


def _leading_comment_block(text: str) -> str:
    """The comment block at the top of the file -- blank lines included, the
    first line of actual code excluded.

    WHY THE SCOPE IS THE BLOCK AND NOT A BYTE OR LINE COUNT, measured rather
    than reasoned. A first cut scanned the whole ``_HEADER_SCAN_BYTES`` head
    for an anchored marker and, on a five-repository sweep, newly classified
    three first-party files as generated:

      test_max_tier.py:106   ``# ...classified tier 4 via its "DO NOT EDIT" header,``
      BaseToggleActionDelegate.java:38  ``// TODO Auto-generated method stub``
      CloverModuleConfig.java:41        ``// autogenerated``

    The first is INV-bokim's own defect wearing a comment instead of a
    docstring -- prose ABOUT generated code, in a hand-written test. The second
    is Eclipse's per-METHOD stub marker, which says nothing about the file. A
    generated file DECLARES itself in its header, so the header is the scope.

    It has to be the BLOCK and not the first five lines, because a real
    declaration routinely sits below a licence notice: http4s's ``MimeDB.scala``
    carries ``// Autogenerated file, don't edit`` at line 17, under a 15-line
    Apache header, and it is genuinely generated.

    A block comment whose interior lines carry no prefix of their own (a bare
    ``/*`` followed by unprefixed prose) ends the block at that line. That is
    deliberate and it errs toward NOT classifying as generated, which is the
    direction that keeps symbols -- the direction INV-bokim was filed about.
    """
    out: list[str] = []
    for line in text.splitlines()[:_HEADER_COMMENT_MAX_LINES]:
        if not line.strip() or _COMMENT_LINE_PREFIX.match(line):
            out.append(line)
            continue
        break
    return "\n".join(out)


def _has_generated_marker(head: str) -> bool:
    """Does ``head`` carry a comment-anchored generated-file declaration?

    The single home for the question. Both consumers -- the ``is_generated``
    flag via :func:`_has_generated_header` and the tier via
    :func:`is_likely_minified` -- route through here, so they cannot drift
    apart again the way INV-pogob found them.
    """
    return _GENERATED_MARKER_PATTERN.search(
        _leading_comment_block(head),
    ) is not None

#: C-family files: the only ones the generator fingerprints below examine.
_C_FAMILY_EXTS = frozenset({".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"})

#: Structural fingerprints of generator output that does not DECLARE itself
#: (WI-munam). Each entry is a set of patterns that must ALL match the head.
#: Measured cause: of the three vendored tree-sitter grammars in this tree only
#: lean's parser.c says ``@generated``; circom's and wolfram's open with the
#: parser include and the ``LANGUAGE_VERSION`` table header, so their parse
#: tables were tier 3 and stood for hand-written C. The INV-bokim discipline
#: holds: tree-sitter and ``#line`` are preprocessor directives at the start of
#: a line, the banners must sit on a comment line, and only C-family files are
#: examined, so prose ABOUT a generator -- a docstring, a Python test fixture,
#: a string literal -- is not mistaken for its output. tree-sitter needs both
#: halves: a hand-written external ``scanner.c`` includes the parser header
#: too, but defines no tables. ``#line N "x.y"`` pointing into a grammar
#: source is how bison, flex, ragel and re2c tie their output to its input;
#: ``#line`` into an ordinary ``.c`` is a hand-written trick and does not count.
_COMMENT_LINE = r"(?m)^[ \t]*(?://|/\*+|\*)[^\n]*?"
_GENERATOR_FINGERPRINTS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("tree-sitter", (
        re.compile(r'(?m)^#include\s*[<"]tree_sitter/parser\.h[>"]'),
        re.compile(r"(?m)^#define\s+LANGUAGE_VERSION\s+\d+"),
    )),
    ("GNU Bison", (re.compile(_COMMENT_LINE + r"A Bison parser, made by GNU Bison"),)),
    ("flex", (re.compile(_COMMENT_LINE + r"A lexical scanner generated by flex"),)),
    ("re2c", (re.compile(_COMMENT_LINE + r"Generated by re2c"),)),
    ("grammar #line", (re.compile(
        r'(?m)^#line\s+\d+\s+"[^"\n]*\.(?:y|yy|ypp|l|ll|lex|rl|re)"',
    ),)),
)


def _generator_fingerprint(head: str, suffix: str) -> Optional[str]:
    """The generator whose structural fingerprint ``head`` carries, if any.

    Only for C-family ``suffix`` (see :data:`_GENERATOR_FINGERPRINTS`).
    """
    if suffix.lower() not in _C_FAMILY_EXTS:
        return None
    for name, patterns in _GENERATOR_FINGERPRINTS:
        if all(p.search(head) for p in patterns):
            return name
    return None


# Only scan the first chunk of the file. Generated-file markers
# conventionally live in the first few lines, so a 4 KiB cap is more
# than enough and keeps the cost bounded on large files.
_HEADER_SCAN_BYTES = 4096

# Extensions that make sense to scan for content markers. Text-like
# source extensions. Binary files (images, archives) are skipped.
_CONTENT_SCAN_EXTS = frozenset({
    ".py", ".pyi", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx",
    ".mjs", ".cjs", ".java", ".kt", ".kts", ".scala", ".sc",
    ".rb", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh",
    ".cs", ".m", ".mm", ".swift", ".php", ".ex", ".exs", ".erl",
    ".elm", ".dart", ".html", ".xml", ".md", ".yaml", ".yml",
    ".proto", ".thrift", ".gql", ".graphql",
})


def _is_generated_file(rel_path: str) -> bool:
    """Check if a file path matches known generated-code patterns.

    WI-tizij: generated code (OpenAPI models, protobuf stubs, Kubernetes
    code-gen) is structurally central but has low developer relevance.
    """
    for pat in _GENERATED_CODE_RE:
        if pat.search(rel_path):
            return True
    return False


def _is_config_file(rel_path: str) -> bool:
    """Check if a file path matches a known dependency/build manifest filename.

    WI-jobuj: matches the basename against ``CONFIG_FILE_NAMES``.
    Path-position-agnostic — a ``pyproject.toml`` at the repo root and
    one inside a workspace are both flagged.
    """
    rel_norm = rel_path.replace("\\", "/")
    basename = rel_norm.rsplit("/", 1)[-1]
    return basename in CONFIG_FILE_NAMES


def _has_generated_header(path: Path) -> bool:
    """Check the first few KB of *path* for a generated-file marker.

    WI-pofin: complements path-pattern detection for files that don't
    match known paths but clearly announce themselves as generated via
    a comment-line marker in the file header. Reads only
    ``_HEADER_SCAN_BYTES`` bytes and skips files whose extension isn't
    in ``_CONTENT_SCAN_EXTS`` to keep the cost bounded. Errors
    reading the file (permission, missing, etc.) are silently treated
    as "not generated" — the caller's fallback is the path-based
    check.
    """
    if path.suffix.lower() not in _CONTENT_SCAN_EXTS:
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEADER_SCAN_BYTES)
    except (OSError, IOError):  # pragma: no cover
        return False
    text = head.decode("utf-8", errors="ignore")
    return _has_generated_marker(text) or (
        _generator_fingerprint(text, path.suffix) is not None
    )


def classify_file(
    path: Path,
    repo_root: Path,
    package_roots: Optional[set[Path]] = None,
    config: Optional[SupplyChainConfig] = None,
) -> FileClassification:
    """Classify a file's supply chain tier.

    Args:
        path: Absolute path to the file
        repo_root: Root directory of the repository
        package_roots: Set of internal package root paths (from detect_package_roots)
        config: Optional custom classification configuration

    Returns:
        FileClassification with tier, reason, and optional package_name.
        The ``is_generated`` flag is set independently of tier when the
        file path matches known generated-code patterns (WI-tizij).
        The ``is_config`` flag (WI-jobuj) is set independently of tier
        when the basename matches a dependency/build manifest filename
        (``pyproject.toml``, ``package.json``, ``Cargo.toml``, etc.) —
        but NOT when ``is_test`` or ``is_example`` is already True, so
        the role flags remain mutually exclusive.
    """
    # Get relative path for pattern matching
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        # Path not under repo_root, default to first-party
        return FileClassification(Tier.FIRST_PARTY, "default (outside repo)")

    # WI-tizij: path-based generated-code detection.
    # WI-pofin: fall back to a content-based header scan when the path
    # is unambiguous. Path check first because it's cheaper.
    generated = _is_generated_file(rel) or _has_generated_header(path)
    config_filename = _is_config_file(rel)

    result = _classify_file_core(rel, path, repo_root, package_roots, config)
    if generated:
        result.is_generated = True
    # WI-jobuj: is_config is mutually exclusive with is_test / is_example
    # (test and example detection wins on a tie, e.g., a
    # package.json under examples/ is is_example=True, not is_config).
    if config_filename and not result.is_test and not result.is_example:
        result.is_config = True
    return result


def _derived_path_reason(rel: str) -> Optional[str]:
    """The DERIVED_* pattern a repo-relative path matches, as a reason."""
    for pattern in DERIVED_PATH_PATTERNS:
        if re.match(pattern, rel):
            return f"path matches {pattern}"
    for pattern in DERIVED_FILENAME_PATTERNS:
        if re.search(pattern, rel):
            return f"filename matches {pattern}"
    return None


def _external_path_match(rel: str) -> Optional[tuple[str, str]]:
    """``(label, root)`` of the EXTERNAL_DEP_* pattern a repo-relative path
    matches, ``root`` being the directory the pattern ends at (every pattern
    ends in ``/``): ``a/vendor/x/y.c`` gives ``("vendor/", "a/vendor")``."""
    for pattern, label in EXTERNAL_DEP_PATTERNS:
        m = re.match(pattern, rel)
        if m:
            return label, rel[: m.end() - 1]
    for pattern, label in EXTERNAL_DEP_DEEP_PATTERNS:
        m = re.search(pattern, rel)
        if m:
            return label, rel[: m.end() - 1]
    return None


def _external_path_label(rel: str) -> Optional[str]:
    """The label of the EXTERNAL_DEP_* pattern a repo-relative path matches."""
    found = _external_path_match(rel)
    return found[0] if found else None


def vendored_root(rel: str) -> Optional[str]:
    """The vendored directory a repo-relative path sits in, else ``None``.

    The directory an EXTERNAL_DEP_* pattern matched (``pkg/vendor``), which
    is what the vendored-code disclosure names (WI-pasan).
    """
    found = _external_path_match(rel.replace("\\", "/"))
    return found[1] if found else None


#: How many vendored directories ``supply_chain_summary.vendored_analysed``
#: lists, like the other disclosure buckets.
_VENDORED_ENTRIES_CAP = 10


def vendored_analysed_summary(symbols: "Iterable[Any]") -> dict[str, Any]:
    """``supply_chain_summary.vendored_analysed``: committed vendored code that
    was analysed as tier 3 and left out of the summaries (ADR-0004 point 3a).

    ``{dirs, entries: [{path, languages, files}]}``, one entry per vendored
    directory (:func:`vendored_root`), from the tier-3 ``file`` nodes; a file
    two analyzers read counts once. Tier-3 code with no vendored root --
    installed dependency source traced with ``--trace-deps`` -- is not
    vendored and has its own disclosure. Sorted by path, capped at 10.
    """
    files: dict[str, set[str]] = {}
    languages: dict[str, set[str]] = {}
    for sym in symbols:
        if sym.kind != "file" or sym.supply_chain_tier != Tier.EXTERNAL_DEP:
            continue
        root = vendored_root(sym.path)
        if root is None:
            continue
        files.setdefault(root, set()).add(sym.path)
        languages.setdefault(root, set()).add(sym.language)
    entries = [
        {"path": root, "languages": sorted(languages[root]), "files": len(paths)}
        for root, paths in sorted(files.items())
    ]
    return {"dirs": len(entries), "entries": entries[:_VENDORED_ENTRIES_CAP]}


def format_vendored_disclosure(summary: "dict[str, Any] | None") -> Optional[str]:
    """The one-line vendored-code disclosure, or ``None`` when there is none.

    Names at most three directories, like the installed-dependency line.
    """
    if not summary or not summary.get("dirs"):
        return None
    parts = []
    for e in (summary.get("entries") or [])[:3]:
        n = e["files"]
        parts.append(
            f"{e['path']}/ ({', '.join(e['languages'])}; "
            f"{n} file{'' if n == 1 else 's'})"
        )
    more = int(summary["dirs"]) - len(parts)
    if more > 0:
        parts.append(f"{more} more director{'y' if more == 1 else 'ies'}")
    return (
        "Vendored third-party code analysed as tier 3, not counted in the "
        f"language breakdown: {', '.join(parts)}. To skip: --exclude <dir>."
    )


def tier_by_path(rel: str) -> Optional[Tier]:
    """The tier a repo-relative path gets from its path alone, else ``None``.

    The path-pattern steps of :func:`classify_file` (DERIVED_* then
    EXTERNAL_DEP_*), with no file read and no filesystem check, for
    summaries that must stay cheap: the profile's per-language own-code
    counts (WI-kojul, ADR-0004 point 3a). ``None`` means the path does not
    decide it; the full classifier may still find a minified file, build
    output or installed dependency source, so this under-reports tier 3-4
    rather than inventing it. Both use the same helpers, so they cannot
    disagree about a path pattern.
    """
    rel = rel.replace("\\", "/")
    if _derived_path_reason(rel) is not None:
        return Tier.DERIVED
    if _external_path_label(rel) is not None:
        return Tier.EXTERNAL_DEP
    return None


def _classify_file_core(
    rel: str,
    path: Path,
    repo_root: Path,
    package_roots: Optional[set[Path]] = None,
    config: Optional[SupplyChainConfig] = None,
) -> FileClassification:
    """Core classification logic (called by classify_file)."""

    # Normalize path separators for consistent matching
    rel = rel.replace("\\", "/")

    # 0. Check custom derived patterns from config first
    if config and config.derived_patterns:
        for pattern in config.derived_patterns:
            if rel.startswith(pattern) or re.match(f"^{re.escape(pattern)}", rel):
                return FileClassification(Tier.DERIVED, f"config derived_patterns: {pattern}")

    # 1. Check derived patterns first (these should be skipped). Generated
    # output first, by the same content rule that prunes it from discovery,
    # so the walk and the classifier can never disagree about what is output.
    output = _build_output_root(rel, repo_root)
    if output is not None:
        return FileClassification(
            Tier.DERIVED, f"in {output[1]} output {output[0]}/",
        )
    derived = _derived_path_reason(rel)
    if derived is not None:
        return FileClassification(Tier.DERIVED, derived)

    # 2. Check content heuristics for minification (only for existing files)
    if path.exists() and is_likely_minified(path):
        return FileClassification(Tier.DERIVED, "detected as minified/generated")

    # 3. Check external dependencies, root-anchored then anywhere in the
    # path (vendored SDKs, nested vendor/ trees).
    external = _external_path_label(rel)
    if external is not None:
        pkg = _extract_package_name(rel, external)
        return FileClassification(Tier.EXTERNAL_DEP, f"in {external}", pkg)

    # 3c. Installed dependency source traced with --trace-deps where no path
    # pattern reaches it (a virtualenv's site-packages, a nested Mix
    # ``server/deps/``). ADR-0004 §"Installed dependency source" point 5:
    # traced dependency source is tier 3, as dependency source always was.
    installed = _installed_dependency_root(rel, repo_root)
    if installed is not None:
        return FileClassification(
            Tier.EXTERNAL_DEP,
            f"in installed dependency source {installed[0]}/",
            installed[1],
        )

    # 4. Check example/demo patterns (lower priority than workspace packages).
    # INV-naduh / ADR-0041 §1: tier names supply-chain DISTANCE only. In-repo
    # example/demo files are the project's OWN code (distance 0) → tier 1
    # first_party; their role is carried by is_example, NOT by tier 2 (which is
    # reserved for org-internal *dependency* packages).
    for pattern in EXAMPLE_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY, f"path matches {pattern}", is_example=True
            )

    # 4b. Check documentation patterns (DocC bundles, etc.). INV-naduh: in-repo
    # docs are first-party (tier 1); the role is captured by the reason string.
    for pattern in DOCUMENTATION_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"documentation path matches {pattern}")

    # 5a. Check custom internal_package_roots from config
    if config and config.internal_package_roots:
        for pkg_pattern in config.internal_package_roots:
            if rel.startswith(pkg_pattern) or rel.startswith(pkg_pattern + "/"):
                return FileClassification(
                    Tier.INTERNAL_DEP, f"config internal_package_roots: {pkg_pattern}"
                )

    # 5. Check internal packages (monorepo workspaces)
    if package_roots:
        for pkg_root in package_roots:
            try:
                if path.is_relative_to(pkg_root):
                    rel_to_pkg = str(path.relative_to(pkg_root)).replace("\\", "/")
                    # INV-tisid: test directories inside workspace packages
                    # are tier 1 with is_test=True. Tier 2 is reserved for
                    # org-internal dependency packages (INV-naduh).
                    # Previously this branch returned
                    # INTERNAL_DEP, causing 99% of self-analysis tier-2
                    # entries to be tests rather than actual internal deps.
                    for test_pat in TEST_DIR_PATTERNS:
                        if re.search(test_pat, rel_to_pkg):
                            return FileClassification(
                                Tier.FIRST_PARTY,
                                f"in workspace {pkg_root.name} (test dir)",
                                is_test=True,
                            )
                    # Co-located test FILE patterns (e.g. _test.go next to
                    # handler.go) are tier 1 with is_test=True.  The is_test
                    # flag captures their test nature; tier 1 reflects that
                    # they live in the same package as the source they test.
                    for test_pat in TEST_FILE_PATTERNS:
                        if re.search(test_pat, rel_to_pkg):
                            return FileClassification(
                                Tier.FIRST_PARTY,
                                f"in workspace {pkg_root.name} (co-located test)",
                                is_test=True,
                            )
                    # All other workspace files are first-party.
                    # Workspace members are by definition part of the project.
                    return FileClassification(
                        Tier.FIRST_PARTY,
                        f"in workspace {pkg_root.name}",
                    )
            except (ValueError, TypeError):
                continue

    # 5b-pre. WI-pugas: language-specific test patterns that OVERRIDE
    # the dir pattern. Phoenix/Elixir tests live at `test/<ctx>/foo_test.exs`
    # by convention — they match both the dir pattern (test/) and the
    # file pattern (_test.exs), and the right verdict is tier 1
    # (first-party with is_test=True), not tier 2 (which conflates "is
    # test" with "is vendored").
    for pattern in TEST_FILE_PATTERNS_DIR_OVERRIDE:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"co-located test file matches {pattern}",
                is_test=True,
            )

    # 5b. INV-tisid: test directories are tier 1 with is_test=True.
    # Tier 2 is reserved for org-internal dependency packages
    # (INV-naduh); test code is first-party code that
    # the project's authors write, not a vendored dependency.
    for pattern in TEST_DIR_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"test path matches {pattern}",
                is_test=True,
            )
    # Co-located test FILE patterns (e.g. _test.go, .test.js next to
    # source files) → tier 1 with is_test=True.  Prior to this change,
    # these were tier 2, which made tier-2 useless for distinguishing
    # first-party tests from actual third-party dependencies in Go and
    # other languages where tests are co-located with source.
    for pattern in TEST_FILE_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"co-located test file matches {pattern}",
                is_test=True,
            )

    # 5c. Jupyter notebooks are exploratory, not part of the import namespace
    if rel.endswith(".ipynb"):
        # INV-naduh: in-repo notebooks are first-party (tier 1), not a tier-2
        # dependency; the role is captured by the reason string.
        return FileClassification(Tier.FIRST_PARTY, "notebook file (.ipynb)")

    # 5d. Check fuzz/benchmark patterns (not production code). INV-naduh: in-repo
    # fuzz/bench harnesses are first-party (tier 1); "not production" is captured
    # by the reason string, not by mislabeling them as a tier-2 dependency.
    for pattern in FUZZ_BENCH_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"fuzz/bench path matches {pattern}")

    # 5e. Check custom first_party_patterns from config
    if config and config.first_party_patterns:
        for pattern in config.first_party_patterns:
            if rel.startswith(pattern) or re.match(f"^{re.escape(pattern)}", rel):
                return FileClassification(
                    Tier.FIRST_PARTY, f"config first_party_patterns: {pattern}"
                )

    # 6. Check first-party patterns
    for pattern in FIRST_PARTY_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"path matches {pattern}")

    # 7. Default: assume first-party if no other signals
    return FileClassification(Tier.FIRST_PARTY, "default (no matching pattern)")


# INV-lukop: the average-line-length minification heuristic (Heuristic 1 in
# ``is_likely_minified``) only makes sense for WEB ASSETS — JS/CSS/HTML bundles
# are the artifacts that get minified into long single lines. A dense-but-real
# source file in another language (a Python data/lookup/i18n module, a very long
# function signature, generated protobuf) legitimately has a high average line
# length and must NOT be misclassified as minified: doing so classifies its whole
# file tier-4 (derived), and the default tier filter then drops EVERY symbol and
# edge for that file with no diagnostic. Gating the heuristic to these extensions
# closes that silent-whole-file-drop vector; the @generated / sourcemap / webpack
# heuristics stay universal because they are language-agnostic generation signals.
_MINIFIABLE_WEB_EXTENSIONS = frozenset({
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm", ".xhtml",
    ".vue", ".svelte",
})


def is_likely_minified(path: Path) -> bool:
    """Detect likely minified/bundled/generated files via content heuristics.

    Checks:
    1. Average line length > 150 chars (minified code) — WEB ASSETS ONLY
       (``_MINIFIABLE_WEB_EXTENSIONS``); see the INV-lukop note above.
    2. Source map reference in last 3 lines (transpiled)
    3. A comment-anchored generated-file declaration in the header --
       :func:`_has_generated_marker`, shared with the ``is_generated`` flag
       so the two cannot disagree (INV-bokim / INV-pogob); or (3b) a C-family
       generator's structural fingerprint, :func:`_generator_fingerprint`
    4. Webpack bootstrap pattern in first 10 lines

    Args:
        path: Path to the file to check

    Returns:
        True if file appears to be minified/generated
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, IOError):
        return False

    lines = content.splitlines()
    if not lines:
        return False

    # Heuristic 1: Average line length > 150 chars, but only for web assets that
    # actually get minified (INV-lukop) — a dense non-web source file is not.
    if path.suffix.lower() in _MINIFIABLE_WEB_EXTENSIONS:
        avg_line_len = len(content) / len(lines)
        if avg_line_len > 150:
            return True

    # Heuristic 2: Source map reference in last 3 lines
    tail = "\n".join(lines[-3:])
    if re.search(r"//[#@]\s*sourceMappingURL=", tail):
        return True

    # Heuristic 3: a comment-anchored generated-file DECLARATION in the header.
    # Shares :func:`_has_generated_marker` with the ``is_generated`` flag, and
    # shares its WINDOW too (``_HEADER_SCAN_BYTES``) rather than the five lines
    # it used to read -- two windows would still be two answers for a file whose
    # marker sits at line eight, which is the drift INV-pogob was filed for.
    if _has_generated_marker(content[:_HEADER_SCAN_BYTES]):
        return True

    # Heuristic 3b (WI-munam): a generator's structural fingerprint, same
    # window, shared with the is_generated flag via _has_generated_header.
    if _generator_fingerprint(content[:_HEADER_SCAN_BYTES], path.suffix):
        return True

    # Heuristic 4: Webpack bootstrap in first 10 lines (bundled but not minified)
    head_10 = "\n".join(lines[:10])
    if re.search(r"__webpack_require__|webpackBootstrap", head_10):
        return True

    return False


def detect_package_roots(repo_root: Path) -> set[Path]:
    """Detect internal package roots from workspace/monorepo configs.

    Scans for:
    - npm/yarn workspaces in package.json (pnpm-workspace.yaml is not read)
    - Cargo workspace members in Cargo.toml
    - Maven modules in pom.xml
    - Gradle subprojects in settings.gradle / settings.gradle.kts

    Args:
        repo_root: Root directory of the repository

    Returns:
        Set of Path objects pointing to internal package directories
    """
    roots: set[Path] = set()

    # npm/yarn/pnpm workspaces
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            # Skip non-dict package.json files (e.g., string or array at top level)
            if not isinstance(data, dict):
                data = {}
            workspaces = data.get("workspaces", [])

            # Handle object format: {"packages": [...]}
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])

            for pattern in workspaces:
                # Skip empty or current-dir patterns
                if not pattern or pattern == ".":
                    continue
                # Expand globs like "packages/*"
                for match in repo_root.glob(pattern):
                    if match.is_dir():
                        roots.add(match)
        except (json.JSONDecodeError, OSError):
            pass

    # Cargo workspaces
    cargo_toml = repo_root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text()
            # Simple TOML parsing for workspace members
            if "[workspace]" in content:
                for members_match in re.finditer(
                    r"members\s*=\s*\[(.*?)\]", content, re.DOTALL
                ):
                    for member in re.findall(r'"([^"]+)"', members_match.group(1)):
                        if not member or member == ".":
                            continue
                        for path in repo_root.glob(member):
                            if path.is_dir():
                                roots.add(path)
        except OSError:
            pass

    # Maven multi-module projects
    pom_xml = repo_root / "pom.xml"
    if pom_xml.exists():
        try:
            import xml.etree.ElementTree as ET  # nosec B405 - parsing local pom.xml

            tree = ET.parse(pom_xml)  # noqa: S314  # nosec B314
            root_el = tree.getroot()
            # Handle Maven namespace (xmlns="http://maven.apache.org/POM/4.0.0")
            ns = ""
            if root_el.tag.startswith("{"):
                ns = root_el.tag.split("}")[0] + "}"
            modules_el = root_el.find(f"{ns}modules")
            if modules_el is not None:
                for mod_el in modules_el.findall(f"{ns}module"):
                    if mod_el.text:
                        mod_path = repo_root / mod_el.text.strip()
                        if mod_path.is_dir():
                            roots.add(mod_path)
        except (OSError, ET.ParseError):
            pass

    # Gradle multi-project builds (WI-zizuf)
    # settings.gradle or settings.gradle.kts declares subprojects via
    # include('mod') or include 'mod'.  Colon separators (e.g., 'connect:api')
    # map to directory nesting (connect/api/).
    for settings_name in ("settings.gradle", "settings.gradle.kts"):
        settings_file = repo_root / settings_name
        if settings_file.exists():
            try:
                content = settings_file.read_text()
                # Match both Groovy (include 'a', 'b') and Kotlin DSL
                # (include("a", "b")) styles. Each include() call may
                # list multiple projects separated by commas.
                for m in re.finditer(
                    r"""include\s*\(?\s*((?:['"]:?[^'"]*['"],?\s*)+)\)?""",
                    content,
                ):
                    for name in re.findall(r"""['"][:.]?([^'"]+)['"]""", m.group(1)):
                        if not name:  # pragma: no cover - regex guarantees non-empty
                            continue
                        # Gradle ':' separator maps to directory '/'
                        dir_name = name.replace(":", "/")
                        mod_path = repo_root / dir_name
                        if mod_path.is_dir():
                            roots.add(mod_path)
            except OSError:
                pass

    return roots


def _extract_package_name(rel_path: str, pattern_label: str) -> Optional[str]:
    """Extract package name from dependency path.

    For node_modules/, extracts the npm package name (handling scoped packages).

    Args:
        rel_path: Relative path within the repo
        pattern_label: The matched pattern label (e.g., "node_modules/")

    Returns:
        Package name if extractable, None otherwise
    """
    if pattern_label == "node_modules/":
        parts = rel_path.split("node_modules/")[-1].split("/")
        if not parts[0]:
            # Empty path after node_modules/ (edge case)
            return None
        if parts[0].startswith("@"):
            # Scoped package: @scope/package
            if len(parts) >= 2:
                return "/".join(parts[:2])
            return parts[0]
        return parts[0]

    return None


def _build_output_root(rel: str, repo_root: Path) -> Optional[tuple[str, str]]:
    """``(root, tool)`` when ``rel`` lies inside a directory discovery's
    build-output rules claim as generated output (ADR-0004 point 7), else
    ``None``. Name-gated by ``match_content_rule``, so an ancestor that is no
    candidate costs one set lookup."""
    from .discovery import match_content_rule

    parts = rel.split("/")[:-1]
    for i in range(len(parts)):
        rule = match_content_rule(repo_root.joinpath(*parts[: i + 1]))
        if rule is not None and not rule.is_installed_dep:
            return "/".join(parts[: i + 1]), rule.ecosystem
    return None


def _installed_dependency_root(
    rel: str, repo_root: Path,
) -> Optional[tuple[str, Optional[str]]]:
    """``(root, package)`` when ``rel`` lies inside a directory discovery's
    content rules recognise as installed dependency source, else ``None``.

    Asks :func:`discovery.match_content_rule` -- the same test that pruned
    the untraced directories -- for each ancestor directory, so the two can
    never disagree about what is installed. ``package`` is the name of the
    package directory ``rel`` falls in, when it is one of the root's listed
    packages.
    """
    from .discovery import installed_packages, match_content_rule

    parts = rel.split("/")[:-1]
    for i in range(len(parts)):
        root = repo_root.joinpath(*parts[: i + 1])
        rule = match_content_rule(root)
        if rule is None or not rule.is_installed_dep:
            continue
        path = repo_root / rel
        pkg = next(
            (
                name for name, p in installed_packages(root, rule).items()
                if path == p or path.is_relative_to(p)
            ),
            None,
        )
        return "/".join(parts[: i + 1]), pkg
    return None


# INV-kutub / ADR-0041 §1 — a dependency declaration naming an in-tree package.
#
# A declaration stands for the package it names, so a declaration naming a
# package whose source is in the analysed tree carries THAT package's tier
# (one package, one tier). ``cli._classify_symbols`` asks
# :class:`InTreePackages` for the naming manifest and classifies it; every other
# declaration is tier 3. The Python half parallels the workspace-member
# *subtraction* in ``hypergumbo_lang_mainstream.py_deps.parse_python_dependencies``
# (the same rule, applied there to imported boundary nodes). The two cannot
# share code: ``hypergumbo-core`` is the base package and must not import a
# language-analyzer package, so the small name-collection logic is duplicated.
_WORKSPACE_NAME_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_pep503(name: str) -> str:
    """PEP 503 distribution-name normalization: lower-case and collapse runs
    of ``-``/``_``/``.`` into a single ``-`` (so ``My_Cool.Pkg`` and
    ``my-cool-pkg`` compare equal)."""
    return _WORKSPACE_NAME_NORMALIZE_RE.sub("-", name).lower()


def _own_distribution_name(data: dict[str, Any]) -> Optional[str]:
    """Read a package's OWN distribution name from parsed ``pyproject.toml``
    data. PEP 621 ``[project].name`` is preferred; Poetry
    ``[tool.poetry].name`` is the fallback. Returns ``None`` when neither is
    a non-empty string."""
    project = data.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            name = poetry.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _manifest_files(repo_root: Path, file_names: "Container[str]") -> "Iterator[Path]":
    """Every file under ``repo_root`` whose name is in ``file_names``.

    Skips ``discovery.manifest_walk_skip()`` names (``DEFAULT_EXCLUDES`` plus
    ``node_modules`` / ``vendor``), directories a discovery content rule claims
    (a virtualenv, Mix ``deps/``, ...; see ``discovery.walks_into``) and
    dot-prefixed directories, so a dependency's manifest cannot leak in.

    DOES NOT DESCEND INTO A DIRECTORY SYMLINK (INV-pivir), which is what
    ``Path.rglob`` and discovery already do. ``Path.is_dir()`` follows links,
    so the walk this replaces pushed a link back to its own parent forever:
    arti's ``maint/rust-maint-common/{maint,common} -> .`` is two self-links
    per level, an exponential walk, and ``verify-claims`` sat at 100% CPU for
    30+ minutes before any analysis ran. A symlinked manifest FILE is still
    read -- reading one file is bounded.
    """
    from .discovery import manifest_walk_skip, walks_into

    skip = manifest_walk_skip()
    stack = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):  # pragma: no cover - unreadable dir
            continue
        for entry in entries:
            if entry.is_file():
                if entry.name in file_names:
                    yield entry
            elif walks_into(entry, skip):
                stack.append(entry)


def _own_cargo_package_name(content: str) -> Optional[str]:
    """Read a crate's OWN name from ``Cargo.toml`` — ``[package].name`` and
    nothing else.

    DELIBERATELY NOT A REGEX over ``name = "..."``. Cargo.toml carries a
    ``name`` key in ``[dependencies.<x>]``, ``[[bin]]``, ``[[example]]`` and
    ``[lib]`` as well, and ``sketch._detect_project_binary_names`` scrapes all
    of them on purpose because "what is this repo's executable called" is a
    different question with a different failure direction. Here a dependency's
    name admitted as the repo's own would SUPPRESS a genuine third-party module
    from :func:`verify_claims._adjudicate_external_modules`, which is the one
    direction that gate exists to prevent.
    """
    from .profile import _load_toml

    data = _load_toml(content)
    if not isinstance(data, dict):
        return None
    package = data.get("package")
    if isinstance(package, dict):
        name = package.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


#: ``module`` is a go.mod directive, so it is anchored at line start. A ``//``
#: comment can never match. The quoted form is legal though rare.
_GO_MODULE_DIRECTIVE = re.compile(
    r'^[ \t]*module[ \t]+(?:"([^"]+)"|(\S+))', re.MULTILINE
)


def _own_go_module_path(content: str) -> Optional[str]:
    """The WHOLE module path from go.mod's ``module`` directive.

    NOT the last component. ``sketch._detect_project_binary_names`` keeps only
    ``caddy`` out of ``github.com/caddyserver/caddy/v2`` because a binary is
    named by its last component; the callee slots this feeds carry the full
    path, so the truncated form would match none of them.
    """
    match = _GO_MODULE_DIRECTIVE.search(content)
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip() or None


def _own_npm_package_name(content: str) -> Optional[str]:
    """The ``name`` field of a ``package.json`` (or ``composer.json``, which
    spells its own name the same way). ``json.JSONDecodeError``
    subclasses ``ValueError``, so a malformed manifest is skipped, not fatal."""
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


#: Manifest basename → the reader that extracts THAT manifest's own published
#: name. Python is absent deliberately: a pyproject DISTRIBUTION name
#: (``hypergumbo-core``) is not the module spelling (``hypergumbo_core``), and
#: Python's self-reference is already covered by the path-derived test in
#: ``verify_claims._analyzed_modules``.
_FIRST_PARTY_MANIFESTS: dict[str, Callable[[str], Optional[str]]] = {
    "Cargo.toml": _own_cargo_package_name,
    "go.mod": _own_go_module_path,
    "package.json": _own_npm_package_name,
}


def own_package_name(manifest: Path) -> Optional[str]:
    """The name a ``Cargo.toml`` / ``go.mod`` / ``package.json`` publishes its
    own package under, read by the reader :data:`_FIRST_PARTY_MANIFESTS` keeps
    for that basename; None for another basename, an unreadable file, or a
    manifest that names no package.

    The per-manifest form of :func:`collect_first_party_package_names`, for a
    consumer that needs to know WHICH directory a name belongs to: an import of
    a workspace sibling resolves to that sibling's source (WI-rilof, js_module
    and the Rust ``use`` resolver)."""
    reader = _FIRST_PARTY_MANIFESTS.get(manifest.name)
    if reader is None:
        return None
    try:
        content = manifest.read_text(errors="ignore")
    except OSError:
        return None
    return reader(content)


def collect_first_party_package_names(repo_root: Path) -> set[str]:
    """The names this repository PUBLISHES ITSELF UNDER, read from its manifests.

    THE THIRD FIRST-PARTY MECHANISM (INV-vivok).
    ``verify_claims._adjudicate_external_modules`` already excludes a module
    whose FILE this analysis read (``_analyzed_modules``, path-derived) and one
    the language marks as internal (``is_definitionally_first_party`` —
    ``crate::``, ``./``). Neither can see a repo that refers to itself by the
    name it publishes, because that name lives in a manifest and not in the
    directory layout: bellman's ``bellman.VerificationError`` and caddy's
    ``github.com/caddyserver/caddy/v2/modules/caddyhttp`` were both reported as
    unexamined third-party modules, the latter while caddy's RELATIVE spellings
    of the same package were correctly suppressed.

    WHY NOT ``supply_chain_tier``, which INV-vivok proposed. Measured over 42
    cached surveys the tier is populated on all 796 package nodes (187 tier 1,
    609 tier 3) — the item's "None on every package node" was a top-level read
    of a field that serialises nested under ``supply_chain.tier``. It is still
    the wrong instrument: a package node's tier comes from its DECLARING FILE'S
    path, so a third-party package declared in an in-repo manifest reads
    first_party (``cmake:CMakeLists.txt:204-204:"GnuTLS":package`` at tier 1).
    Reading the manifest is a fact; the tier is a proxy that fails open.

    Scoped to the three manifests that publish a name a callee slot can carry
    (see :data:`_FIRST_PARTY_MANIFESTS`). Skips dependency directories (see
    :func:`_manifest_files`), so a vendored ``node_modules/<dep>/package.json``
    — which names a THIRD-PARTY package — cannot leak in. Returns names in
    their published spelling; the separator fold belongs to the consumer.

    Returns an empty set for a repo with none of these manifests, which leaves
    every caller's behaviour exactly unchanged.

    MEASURED RESIDUAL, disclosed rather than asserted away. Over the 42-survey
    corpus this suppresses 154 module reports and adds none. Of those 154, 150
    are unambiguous — the name is declared by an in-repo manifest and either
    unconsumed or consumed through a ``workspace:``/``path``/``file:`` spec,
    which is the workspace-sibling shape INV-nuzas already rules first-party.
    The remaining 4 (``@y-sweet/{client,react,sdk}``, ``@simplex-chat/types``)
    are a monorepo pattern where the repo PUBLISHES a package to a registry and
    one of its own sub-projects then depends on it BY VERSION (``^0.1.0``)
    rather than by link. The name is still the repo's own and the source is in
    the tree, so the suppression matches the invariant; what is not established
    is whether that sub-project resolves to the in-repo copy or to an installed
    one. This is a disclosure gate rather than a supply-chain integrity gate,
    so the difference does not change the verdict — but it is the one case here
    where "this repo publishes it" and "this reference resolves to it" can come
    apart, and a later reader should know that before widening the rule.
    """
    names: set[str] = set()
    for entry in _manifest_files(repo_root, _FIRST_PARTY_MANIFESTS):
        try:
            content = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        own = _FIRST_PARTY_MANIFESTS[entry.name](content)
        if own:
            names.add(own)
    return names


def _own_pyproject_name(content: str) -> Optional[str]:
    """A ``pyproject.toml``'s own distribution name (``[project].name``, else
    ``[tool.poetry].name``); None for malformed TOML or no name."""
    from .profile import _load_toml

    data = _load_toml(content)
    if not isinstance(data, dict):
        return None
    return _own_distribution_name(data)


def _own_pom_coordinate(content: str) -> Optional[str]:
    """A ``pom.xml``'s own Maven coordinate, ``groupId:artifactId``.

    The groupId is the project's own, else its ``<parent>``'s: a module that
    omits it inherits the parent's, which is the usual multi-module shape.
    Only the project element's direct children are read, so a
    ``<dependency>``'s coordinate cannot be taken for the project's."""
    import xml.etree.ElementTree as ET  # nosec B405 - parsing a local pom.xml

    try:
        root = ET.fromstring(content)  # noqa: S314  # nosec B314
    except ET.ParseError:
        return None
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    artifact = (root.findtext(f"{ns}artifactId") or "").strip()
    group = (
        root.findtext(f"{ns}groupId")
        or root.findtext(f"{ns}parent/{ns}groupId")
        or ""
    ).strip()
    if not artifact or not group:
        return None
    return f"{group}:{artifact}"


def _cargo_fold(name: str) -> str:
    """Cargo treats ``-`` and ``_`` in a crate name as the same character
    (``crate_b = { path = "../crate-b" }`` names ``crate-b``)."""
    return name.replace("_", "-")


def _exact(name: str) -> str:
    return name


#: Dependency-declaring manifest basename -> (the reader of THAT manifest's own
#: package name, the fold under which the ecosystem compares two names). A
#: declaration is matched only against packages published by a manifest of the
#: same basename: an npm ``core`` is not the crate ``core``.
_DECLARATION_ECOSYSTEMS: dict[
    str, tuple[Callable[[str], Optional[str]], Callable[[str], str]]
] = {
    "pyproject.toml": (_own_pyproject_name, _normalize_pep503),
    "package.json": (_own_npm_package_name, _exact),
    "composer.json": (_own_npm_package_name, _exact),
    "Cargo.toml": (_own_cargo_package_name, _cargo_fold),
    "pom.xml": (_own_pom_coordinate, _exact),
}

#: The Maven property a module uses to name its own group; a sibling
#: dependency on ``${project.groupId}:mod-b`` names the declaring project's
#: group.
_MAVEN_OWN_GROUP = "${project.groupId}"


@dataclass(frozen=True)
class InTreePackages:
    """The packages whose manifest is in the analysed tree, by ecosystem.

    ``by_name`` maps (manifest basename, folded own name) to the repo-relative
    paths of every manifest publishing that name, sorted; ``own`` maps a
    manifest's repo-relative path to its own (unfolded) name, which resolves
    a Maven ``${project.groupId}``.

    Built by :func:`collect_in_tree_packages` in one walk; read by
    ``cli._classify_symbols`` through :meth:`manifests_named_by`."""

    by_name: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    own: dict[str, str] = field(default_factory=dict)

    def manifests_named_by(
        self, declaring_path: str, name: str, qualified_name: Optional[str] = None,
    ) -> tuple[str, ...]:
        """The in-tree manifests publishing the package a declaration names.

        ``declaring_path`` is the declaration's own manifest (its basename picks
        the ecosystem); ``name`` is the declared name, except for Maven, whose
        declaration is named by its artifactId and identified by
        ``qualified_name`` (``groupId:artifactId``). Empty when the name is not
        an in-tree package, or the manifest kind declares no dependencies."""
        basename = declaring_path.rsplit("/", 1)[-1]
        ecosystem = _DECLARATION_ECOSYSTEMS.get(basename)
        if ecosystem is None:
            return ()
        fold = ecosystem[1]
        key = name
        if basename == "pom.xml":
            if not qualified_name or ":" not in qualified_name:
                return ()
            group, artifact = qualified_name.split(":", 1)
            if group == _MAVEN_OWN_GROUP:
                own = self.own.get(declaring_path)
                if own is None:
                    return ()
                group = own.split(":", 1)[0]
            key = f"{group}:{artifact}"
        return self.by_name.get((basename, fold(key)), ())


def collect_in_tree_packages(repo_root: Path) -> InTreePackages:
    """Read every in-tree manifest of a dependency-declaring ecosystem
    (:data:`_DECLARATION_ECOSYSTEMS`) for the name it publishes its own package
    under.

    One walk over :func:`_manifest_files`, which skips dependency directories
    and dot-prefixed directories, so a vendored
    ``node_modules/<dep>/package.json`` cannot make a third-party name read as
    in-tree. A manifest that names no package (a workspace root
    ``Cargo.toml``, a pyproject with only ``[build-system]``) adds nothing.
    """
    by_name: dict[tuple[str, str], list[str]] = {}
    own: dict[str, str] = {}
    for entry in _manifest_files(repo_root, _DECLARATION_ECOSYSTEMS):
        try:
            content = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        reader, fold = _DECLARATION_ECOSYSTEMS[entry.name]
        name = reader(content)
        if not name:
            continue
        rel = entry.relative_to(repo_root).as_posix()
        own[rel] = name
        by_name.setdefault((entry.name, fold(name)), []).append(rel)
    return InTreePackages(
        by_name={key: tuple(sorted(paths)) for key, paths in by_name.items()},
        own=own,
    )


#: Composer's platform packages: the PHP runtime and what ships with it
#: (``php``, ``ext-json``, ``lib-icu``, ``composer-plugin-api``). A registry
#: package is always ``vendor/name``; these have no slash.
_COMPOSER_PLATFORM_PREFIXES: tuple[str, ...] = (
    "php", "hhvm", "ext-", "lib-", "composer",
)


def declaration_ecosystem(path: str, name: str) -> str:
    """The ADR-0041 §3 ``ecosystem`` of a manifest dependency DECLARATION (INV-fuhin).

    A manifest declares a package to FETCH, so a declaration is
    ``third_party`` -- the WI-juzaj reasoning ``ir.create_boundary_nodes``
    applies to a module the manifest resolves -- except composer's platform
    requirements, which name the runtime and its bundled extensions
    (``stdlib``). The producers are pyproject / Cargo (toml_config), Maven
    (xml_config) and package.json / composer.json (json_config); none reads a
    lockfile, so every declaration is a direct one.
    """
    if PurePosixPath(path).name == "composer.json" and "/" not in name:
        if name.startswith(_COMPOSER_PLATFORM_PREFIXES):
            return "stdlib"
    return "third_party"

