"""Catalog of available analysis passes and packs.

The catalog provides a registry of all analysis components available in
hypergumbo. Each component is either:

- **core**: Always available, included in base installation
- **extra**: Requires optional dependencies (e.g., tree-sitter grammars)

How It Works
------------
The catalog is a static registry defined in code. Each Pass represents
a single analyzer (e.g., python-ast-v1), while Packs bundle multiple
passes for common use cases (e.g., python-fastapi combines Python AST
analysis with FastAPI-specific route detection).

Availability checking uses importlib to probe for optional dependencies
without importing them, keeping the base install lightweight.

The `suggest_passes_for_languages` function takes a set of detected language
names and returns passes relevant to those languages.

Why This Design
---------------
- Static registry avoids filesystem scanning or plugin discovery complexity
- Core/extra distinction lets users see what's possible without installing
  everything
- Packs provide curated combinations for common frameworks
- Language-based suggestions enable "suggested passes" based on project content
"""
from __future__ import annotations

import importlib.util
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Pass:
    """An analysis pass that can be applied to source code.

    Attributes:
        id: Unique identifier (e.g., 'python-ast-v1')
        description: Human-readable description
        availability: 'core' (always available) or 'extra' (requires deps)
        requires: Optional package requirement for extras
        languages: Languages this pass handles (for suggestions)
    """

    id: str
    description: str
    availability: str  # 'core' or 'extra'
    requires: Optional[str] = None
    languages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "availability": self.availability,
        }
        if self.requires:
            d["requires"] = self.requires
        return d


@dataclass
class Pack:
    """A bundle of passes for a specific use case.

    .. deprecated:: 0.7.0
        Packs are deprecated. Use the --frameworks flag and linker activation
        conditions instead for framework-specific analysis.

    Attributes:
        id: Unique identifier (e.g., 'python-fastapi')
        description: Human-readable description
        passes: List of pass IDs included in this pack
    """

    id: str
    description: str
    passes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Emit deprecation warning on Pack creation."""
        warnings.warn(
            "Packs are deprecated and will be removed in a future version. "
            "Use the --frameworks flag and linker activation conditions instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "id": self.id,
            "description": self.description,
            "passes": self.passes,
        }


@dataclass
class Catalog:
    """Registry of available passes and packs.

    Attributes:
        passes: List of available analysis passes
        packs: List of available pass bundles
    """

    passes: List[Pass] = field(default_factory=list)
    packs: List[Pack] = field(default_factory=list)

    def get_core_passes(self) -> List[Pass]:
        """Return only core passes (always available)."""
        return [p for p in self.passes if p.availability == "core"]

    def get_extra_passes(self) -> List[Pass]:
        """Return only extra passes (require optional deps)."""
        return [p for p in self.passes if p.availability == "extra"]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "passes": [p.to_dict() for p in self.passes],
            "packs": [p.to_dict() for p in self.packs],
        }


def is_available(p: Pass) -> bool:
    """Check if a pass is available in the current environment.

    Core passes are always available. Extra passes require their
    dependency to be importable (tree-sitter language pack).
    """
    if p.availability == "core":
        return True

    # Check for tree-sitter dependency based on the requires field
    if p.requires and "tree-sitter" in p.requires:
        return importlib.util.find_spec("tree_sitter") is not None

    return False


# Config/data formats that shouldn't trigger pass suggestions
CONFIG_LANGUAGES = {"json", "yaml", "toml", "xml", "css", "markdown"}


def get_default_catalog() -> Catalog:
    """Return the default catalog with all known passes and packs."""
    return Catalog(
        passes=[
            # Core passes (no tree-sitter required)
            Pass(
                id="python-ast-v1",
                description="Python AST parser (classes, functions, imports)",
                availability="core",
                languages=["python"],
            ),
            Pass(
                id="html-pattern-v1",
                description="HTML script tag parser",
                availability="core",
                languages=["html"],
            ),
            Pass(
                id="websocket-linker-v1",
                description="WebSocket communication patterns",
                availability="core",
            ),
            # Language analyzers (tree-sitter based)
            Pass(
                id="javascript-ts-v1",
                description="JS/TS/Svelte/Vue via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["javascript", "typescript", "vue"],
            ),
            Pass(
                id="php-ts-v1",
                description="PHP via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["php"],
            ),
            Pass(
                id="c-ts-v1",
                description="C via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["c"],
            ),
            Pass(
                id="cpp-ts-v1",
                description="C++ via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["cpp"],
            ),
            Pass(
                id="java-ts-v1",
                description="Java via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["java"],
            ),
            Pass(
                id="elixir-ts-v1",
                description="Elixir via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["elixir"],
            ),
            Pass(
                id="rust-ts-v1",
                description="Rust via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["rust"],
            ),
            Pass(
                id="go-ts-v1",
                description="Go via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["go"],
            ),
            Pass(
                id="ruby-ts-v1",
                description="Ruby via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["ruby"],
            ),
            Pass(
                id="kotlin-ts-v1",
                description="Kotlin via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["kotlin"],
            ),
            Pass(
                id="swift-ts-v1",
                description="Swift via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["swift"],
            ),
            Pass(
                id="scala-ts-v1",
                description="Scala via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["scala"],
            ),
            Pass(
                id="lua-ts-v1",
                description="Lua via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["lua"],
            ),
            Pass(
                id="dart-ts-v1",
                description="Dart via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["dart"],
            ),
            Pass(
                id="clojure-ts-v1",
                description="Clojure via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["clojure"],
            ),
            Pass(
                id="elm-ts-v1",
                description="Elm via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["elm"],
            ),
            Pass(
                id="erlang-ts-v1",
                description="Erlang via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["erlang"],
            ),
            Pass(
                id="haskell-ts-v1",
                description="Haskell via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["haskell"],
            ),
            Pass(
                id="agda-v1",
                description="Agda proof assistant via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["agda"],
            ),
            Pass(
                id="lean-v1",
                description="Lean 4 theorem prover via tree-sitter (build from source)",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["lean"],
            ),
            Pass(
                id="wolfram-v1",
                description="Wolfram Language via tree-sitter (build from source)",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["wolfram"],
            ),
            Pass(
                id="ocaml-ts-v1",
                description="OCaml via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["ocaml"],
            ),
            Pass(
                id="solidity-ts-v1",
                description="Solidity smart contracts via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["solidity"],
            ),
            Pass(
                id="csharp-ts-v1",
                description="C# via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["csharp"],
            ),
            Pass(
                id="zig-ts-v1",
                description="Zig via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["zig"],
            ),
            Pass(
                id="groovy-ts-v1",
                description="Groovy via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["groovy"],
            ),
            Pass(
                id="julia-ts-v1",
                description="Julia via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["julia"],
            ),
            Pass(
                id="objc-ts-v1",
                description="Objective-C via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["objc"],
            ),
            Pass(
                id="hcl-ts-v1",
                description="HCL/Terraform via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["hcl"],
            ),
            Pass(
                id="fsharp-ts-v1",
                description="F# via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["fsharp"],
            ),
            Pass(
                id="perl-ts-v1",
                description="Perl via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["perl"],
            ),
            Pass(
                id="r-ts-v1",
                description="R via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["r"],
            ),
            Pass(
                id="bash-v1",
                description="Bash/Shell via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["bash"],
            ),
            # Build/config systems
            Pass(
                id="sql-v1",
                description="SQL schema analysis via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["sql"],
            ),
            Pass(
                id="dockerfile-v1",
                description="Dockerfile analysis via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["dockerfile"],
            ),
            Pass(
                id="cmake-v1",
                description="CMake build system via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["cmake"],
            ),
            Pass(
                id="make-v1",
                description="Makefile build system via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
            ),
            Pass(
                id="graphql-v1",
                description="GraphQL schema via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["graphql"],
            ),
            Pass(
                id="nix-v1",
                description="Nix expressions via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["nix"],
            ),
            # Hardware description
            Pass(
                id="cuda-v1",
                description="CUDA GPU kernels via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["cuda"],
            ),
            Pass(
                id="verilog-v1",
                description="Verilog/SystemVerilog via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["verilog"],
            ),
            Pass(
                id="vhdl-v1",
                description="VHDL hardware design via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["vhdl"],
            ),
            # Shaders
            Pass(
                id="glsl-v1",
                description="GLSL shaders via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["glsl"],
            ),
            Pass(
                id="hlsl-v1",
                description="HLSL DirectX shaders via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["hlsl"],
            ),
            Pass(
                id="wgsl-v1",
                description="WGSL WebGPU shaders via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["wgsl"],
            ),
            # Scientific/legacy
            Pass(
                id="fortran-v1",
                description="Fortran via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["fortran"],
            ),
            Pass(
                id="cobol-v1",
                description="COBOL via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["cobol"],
            ),
            Pass(
                id="latex-v1",
                description="LaTeX via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["latex"],
            ),
            # RPC/serialization
            Pass(
                id="proto-v1",
                description="Protocol Buffers via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["proto"],
            ),
            Pass(
                id="thrift-v1",
                description="Apache Thrift via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["thrift"],
            ),
            Pass(
                id="capnp-v1",
                description="Cap'n Proto via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["capnp"],
            ),
            # Scripting
            Pass(
                id="powershell-v1",
                description="PowerShell via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["powershell"],
            ),
            Pass(
                id="fish-v1",
                description="Fish shell via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["fish"],
            ),
            # Game development
            Pass(
                id="gdscript-v1",
                description="GDScript (Godot) via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["gdscript"],
            ),
            # Build systems
            Pass(
                id="starlark-v1",
                description="Starlark (Bazel/Buck) via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["starlark"],
            ),
            # Systems programming
            Pass(
                id="ada-v1",
                description="Ada via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["ada"],
            ),
            Pass(
                id="d-v1",
                description="D programming language via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["d"],
            ),
            Pass(
                id="nim-v1",
                description="Nim via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["nim"],
            ),
            # Config formats (optional - not suggested by default)
            Pass(
                id="toml-v1",
                description="TOML configuration files via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["toml"],
            ),
            Pass(
                id="css-v1",
                description="CSS stylesheets via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["css"],
            ),
            Pass(
                id="json-config-v1",
                description="JSON configuration files via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["json"],
            ),
            Pass(
                id="yaml-ansible-v1",
                description="YAML/Ansible via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["yaml"],
            ),
            Pass(
                id="xml-config-v1",
                description="XML configuration files via tree-sitter",
                availability="extra",
                requires="tree-sitter-language-pack",
                languages=["xml"],
            ),
        ],
        packs=[
            Pack(
                id="python-fastapi",
                description="FastAPI route detection + call graph",
                passes=["python-ast-v1"],
            ),
            Pack(
                id="electron-app",
                description="Main/renderer split + IPC detection",
                passes=["javascript-ts-v1", "html-pattern-v1"],
            ),
            Pack(
                id="phoenix-app",
                description="Phoenix channels + routes + LiveView",
                passes=["elixir-ts-v1", "html-pattern-v1"],
            ),
            Pack(
                id="full-stack-web",
                description="Complete web app analysis",
                passes=[
                    "python-ast-v1",
                    "javascript-ts-v1",
                    "html-pattern-v1",
                    "css-v1",
                    "sql-v1",
                ],
            ),
            Pack(
                id="systems-programming",
                description="Low-level systems languages",
                passes=["c-ts-v1", "cpp-ts-v1", "rust-ts-v1", "zig-ts-v1"],
            ),
            Pack(
                id="jvm-ecosystem",
                description="JVM languages",
                passes=["java-ts-v1", "kotlin-ts-v1", "scala-ts-v1", "groovy-ts-v1"],
            ),
            Pack(
                id="functional-languages",
                description="Functional programming languages",
                passes=[
                    "haskell-ts-v1",
                    "ocaml-ts-v1",
                    "elm-ts-v1",
                    "erlang-ts-v1",
                    "elixir-ts-v1",
                    "clojure-ts-v1",
                    "fsharp-ts-v1",
                ],
            ),
            Pack(
                id="proof-assistants",
                description="Theorem provers and proof assistants",
                passes=["agda-v1", "lean-v1"],
            ),
        ],
    )


def suggest_passes_for_languages(detected_languages: set[str]) -> List[Pass]:
    """Suggest passes based on detected languages.

    Takes a set of language names (from profile.languages) and returns
    passes that would be relevant. Config-only languages (JSON, YAML, etc.)
    are filtered out.

    Args:
        detected_languages: Set of language names (e.g., {"python", "javascript"}).

    Returns:
        List of Pass objects relevant to detected languages.
    """
    # Filter out config-only languages (they don't suggest passes by default)
    code_languages = detected_languages - CONFIG_LANGUAGES

    if not code_languages:
        return []

    # Find passes that handle detected languages
    catalog = get_default_catalog()
    suggested: List[Pass] = []
    seen_ids: set[str] = set()

    for p in catalog.passes:
        if p.id in seen_ids:  # pragma: no cover - defensive for duplicate IDs
            continue
        for lang in p.languages:
            if lang in code_languages:
                suggested.append(p)
                seen_ids.add(p.id)
                break

    return suggested
