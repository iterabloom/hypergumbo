"""Catalog of available analysis passes and packs.

The catalog provides a registry of all analysis components available in
hypergumbo. Each component is either:

- **core**: Always available, included in base installation
- **extra**: Requires optional dependencies (e.g., tree-sitter for JS/TS)

How It Works
------------
The catalog is a static registry defined in code. Each Pass represents
a single analyzer (e.g., python-ast-v1), while Packs bundle multiple
passes for common use cases (e.g., python-fastapi combines Python AST
analysis with FastAPI-specific route detection).

Availability checking uses importlib to probe for optional dependencies
without importing them, keeping the base install lightweight.

Why This Design
---------------
- Static registry avoids filesystem scanning or plugin discovery complexity
- Core/extra distinction lets users see what's possible without installing
  everything
- Packs provide curated combinations for common frameworks
"""
from __future__ import annotations

import importlib.util
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
    """

    id: str
    description: str
    availability: str  # 'core' or 'extra'
    requires: Optional[str] = None

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

    Attributes:
        id: Unique identifier (e.g., 'python-fastapi')
        description: Human-readable description
        passes: List of pass IDs included in this pack
    """

    id: str
    description: str
    passes: List[str] = field(default_factory=list)

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
    dependency to be importable.
    """
    if p.availability == "core":
        return True

    # Check for tree-sitter dependency based on the requires field
    if p.requires:
        ts_langs = ["javascript", "php", "c", "java", "elixir", "rust", "go", "ruby", "kotlin", "swift", "scala", "lua", "haskell", "agda", "lean", "ocaml", "sql", "dockerfile", "cuda", "verilog", "cmake", "make", "vhdl", "graphql", "nix", "glsl", "fortran", "toml", "css"]
        if any(lang in p.requires for lang in ts_langs):
            return importlib.util.find_spec("tree_sitter") is not None

    return False


def get_default_catalog() -> Catalog:
    """Return the default catalog with all known passes and packs."""
    return Catalog(
        passes=[
            Pass(
                id="python-ast-v1",
                description="Python AST parser",
                availability="core",
            ),
            Pass(
                id="html-pattern-v1",
                description="HTML script tag parser",
                availability="core",
            ),
            Pass(
                id="javascript-ts-v1",
                description="JS/TS/Svelte/Vue via tree-sitter",
                availability="extra",
                requires="hypergumbo[javascript]",
            ),
            Pass(
                id="php-ts-v1",
                description="PHP via tree-sitter",
                availability="extra",
                requires="hypergumbo[php]",
            ),
            Pass(
                id="c-ts-v1",
                description="C via tree-sitter",
                availability="extra",
                requires="hypergumbo[c]",
            ),
            Pass(
                id="java-ts-v1",
                description="Java via tree-sitter",
                availability="extra",
                requires="hypergumbo[java]",
            ),
            Pass(
                id="elixir-ts-v1",
                description="Elixir via tree-sitter",
                availability="extra",
                requires="hypergumbo[elixir]",
            ),
            Pass(
                id="rust-ts-v1",
                description="Rust via tree-sitter",
                availability="extra",
                requires="hypergumbo[rust]",
            ),
            Pass(
                id="go-ts-v1",
                description="Go via tree-sitter",
                availability="extra",
                requires="hypergumbo[go]",
            ),
            Pass(
                id="ruby-ts-v1",
                description="Ruby via tree-sitter",
                availability="extra",
                requires="hypergumbo[ruby]",
            ),
            Pass(
                id="kotlin-ts-v1",
                description="Kotlin via tree-sitter",
                availability="extra",
                requires="hypergumbo[kotlin]",
            ),
            Pass(
                id="swift-ts-v1",
                description="Swift via tree-sitter",
                availability="extra",
                requires="hypergumbo[swift]",
            ),
            Pass(
                id="scala-ts-v1",
                description="Scala via tree-sitter",
                availability="extra",
                requires="hypergumbo[scala]",
            ),
            Pass(
                id="lua-ts-v1",
                description="Lua via tree-sitter",
                availability="extra",
                requires="hypergumbo[lua]",
            ),
            Pass(
                id="haskell-ts-v1",
                description="Haskell via tree-sitter",
                availability="extra",
                requires="hypergumbo[haskell]",
            ),
            Pass(
                id="agda-v1",
                description="Agda proof assistant via tree-sitter",
                availability="extra",
                requires="hypergumbo[agda]",
            ),
            Pass(
                id="lean-v1",
                description="Lean 4 theorem prover via tree-sitter (build from source)",
                availability="extra",
                requires="hypergumbo[lean]",
            ),
            Pass(
                id="wolfram-v1",
                description="Wolfram Language via tree-sitter (build from source)",
                availability="extra",
                requires="hypergumbo[wolfram]",
            ),
            Pass(
                id="ocaml-ts-v1",
                description="OCaml via tree-sitter",
                availability="extra",
                requires="hypergumbo[ocaml]",
            ),
            Pass(
                id="sql-v1",
                description="SQL schema analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[sql]",
            ),
            Pass(
                id="dockerfile-v1",
                description="Dockerfile analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[dockerfile]",
            ),
            Pass(
                id="cuda-v1",
                description="CUDA GPU kernel analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[cuda]",
            ),
            Pass(
                id="verilog-v1",
                description="Verilog/SystemVerilog hardware design via tree-sitter",
                availability="extra",
                requires="hypergumbo[verilog]",
            ),
            Pass(
                id="cmake-v1",
                description="CMake build system analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[cmake]",
            ),
            Pass(
                id="make-v1",
                description="Makefile build system analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[make]",
            ),
            Pass(
                id="vhdl-v1",
                description="VHDL hardware design via tree-sitter",
                availability="extra",
                requires="hypergumbo[vhdl]",
            ),
            Pass(
                id="graphql-v1",
                description="GraphQL schema analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[graphql]",
            ),
            Pass(
                id="nix-v1",
                description="Nix expression analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[nix]",
            ),
            Pass(
                id="glsl-v1",
                description="GLSL shader analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[glsl]",
            ),
            Pass(
                id="fortran-v1",
                description="Fortran analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[fortran]",
            ),
            Pass(
                id="toml-v1",
                description="TOML configuration file analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[toml]",
            ),
            Pass(
                id="css-v1",
                description="CSS stylesheet analysis via tree-sitter",
                availability="extra",
                requires="hypergumbo[css]",
            ),
            Pass(
                id="websocket-linker-v1",
                description="WebSocket communication patterns",
                availability="core",
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
        ],
    )
