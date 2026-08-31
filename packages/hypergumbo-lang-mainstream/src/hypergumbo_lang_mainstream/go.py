# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go analysis pass using tree-sitter-go.

This analyzer uses tree-sitter to parse Go files and extract:
- Function declarations (func)
- Method declarations (func with receiver)
- Struct declarations (type X struct)
- Interface declarations (type X interface)
- Package-level var aliases (var Name = expr) as variable symbols
- Struct fields and interface methods as their own symbols
- Closure-wrapper functions (middleware), tagged ``concepts: [middleware]``
- Function call relationships
- Function references in struct literal fields (cobra, http dispatch)
- Import relationships (import statements)
- ``wraps`` edges for middleware composition, ``module_attr_ref`` for bare
  package-attribute reads, and ``references`` edges for build-tag alternatives
- UsageContext records, including Cobra ``AddCommand`` registration
- The module's dependency manifest, parsed from ``go.mod``
- Web framework routes (Gin, Echo, Fiber, Gorilla mux, Chi, Macaron,
  go-swagger, and Go 1.22+ ``ServeMux`` "POST /path" patterns)

If tree-sitter with Go support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-go is available
2. If not available, return skipped result (not an error)
3. Read go.mod to extract the module path (e.g.,
   ``github.com/aquasecurity/trivy``).  Strip this prefix from import
   paths before passing to the resolver so that suffix matching operates
   on repo-relative paths (``pkg/log``) instead of full module paths.
4. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Extract variable-type bindings, detect calls, imports, and routes
5. Receiver-type disambiguation:
   - Tracks variable types **per function scope** from ``:=`` composite
     literals, ``var`` declarations, and function parameters
     (e.g., in ``func foo() { s := &Server{} }``, s has type Server only in foo)
   - When resolving ``s.Method()``, looks up ``Server.Method`` before falling
     back to short-name resolution, preventing incorrect disambiguation when
     multiple types define the same method name
6. Ambiguous method call guard:
   - When a method call ``x.Method()`` has no inferred receiver type and the
     method name has 2+ candidates in global symbols, creates an unresolved
     edge with ``evidence_type="ast_call"`` + ``meta={"call_construct":
     "method", "resolution_quality": "ambiguous"}`` instead of picking
     an arbitrary candidate (which would produce a false-positive call edge).
   - EXCEPTION: when at least one candidate is an interface method, the
     interface-method test is itself the disambiguator, so the call IS
     resolved — to that interface method, as an ``interface_dispatch`` edge
     at confidence 0.75, or 0.5 with ``meta["disambiguation_fallback"]=True``
     when several interface methods tie (``min`` by symbol id). The
     ``dispatches_to`` edges then route a slice on to the concrete impls.
7. Stdlib interface method guard:
   - When a method call ``x.Lock()`` has no inferred receiver type and the
     method name matches a well-known Go stdlib interface method (Lock, Close,
     Read, Write, String, Error, etc.), creates an unresolved edge with
     ``evidence_type="ast_call"`` + ``meta={"call_construct": "method",
     "receiver": "stdlib"}`` even if only 1 candidate exists
     in the repo. This prevents ``sync.Mutex.Lock()`` calls from resolving
     to ``DirLocker.Lock`` (the only repo candidate), which would give
     DirLocker.Lock 255+ false in-degree edges.
8. Route detection:
   - Gin/Echo: r.GET("/path", handler), e.POST("/path", handler)
   - Fiber: app.Get("/path", handler) (lowercase methods)
   - Gorilla mux: router.HandleFunc("/path", handler), router.Handle("/path", handler)
   - Gorilla mux builder: router.Path("/path").Methods("GET").Handler(handler)
   - Creates route symbols with stable_id = sha256("route:{method}:{path}")
   - Group prefix composition: routes inside Group/Route closures get the
     group path prepended (e.g., Group("/api") > GET("/users") → /api/users).
     Handles nested groups via AST ancestor walk.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-go package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Rust/Elixir/Java/PHP/C analyzers for consistency
- Route detection enables `hypergumbo routes` command for Go

Population of ``is_exported`` follows Go's lexical case rule: identifiers
starting with an uppercase letter are exported (public).
"""
from __future__ import annotations

import os
import re
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Iterator, Optional

if TYPE_CHECKING:
    from hypergumbo_core.supply_chain import DependencyManifest

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, UsageContext,
    make_pass_id,
)
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core.analyze.base import (
    constructed_from_callee,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    emit_module_attribute_refs,
    populate_docstrings_from_tree,
    find_child_by_field,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_route_symbol,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.symbol_resolution import ListNameResolver

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("go")

# Well-known Go standard library interface and concrete-type methods.
# When a method call ``x.Lock()`` has no inferred receiver type and
# the method name is in this set, the call is treated as ambiguous even
# if only 1 candidate exists in the repo. Without this guard,
# ``DirLocker.Lock`` (the only repo-defined Lock method) absorbs 255+
# false in-degree edges from unrelated ``sync.Mutex.Lock()`` calls,
# making it falsely rank #1 in centrality.
# Also covers sync.Map methods (Store, Load, etc.) and sync.WaitGroup.
#
# This set covers the most common interface methods from:
# - sync: Locker (Lock, Unlock)
# - io: Reader (Read), Writer (Write), Closer (Close), Seeker (Seek)
# - fmt: Stringer (String)
# - error: Error
# - sort: Interface (Len, Less, Swap)
# - context: Context (Deadline, Done, Err, Value)
# - encoding: Marshaler (MarshalJSON, MarshalText, etc.)
# - net/http: Handler (ServeHTTP), ResponseWriter (Header, WriteHeader)
_GO_STDLIB_INTERFACE_METHODS: frozenset[str] = frozenset({
    # sync.Locker
    "Lock", "Unlock", "RLock", "RUnlock",
    # io interfaces
    "Read", "Write", "Close", "Seek", "ReadAt", "WriteAt",
    "ReadFrom", "WriteTo", "ReadByte", "WriteByte",
    # fmt.Stringer / error
    "String", "Error",
    # sort.Interface
    "Len", "Less", "Swap",
    # encoding
    "MarshalJSON", "UnmarshalJSON", "MarshalText", "UnmarshalText",
    "MarshalBinary", "UnmarshalBinary",
    # net/http
    "ServeHTTP", "Header", "WriteHeader",
    # context.Context
    "Deadline", "Done", "Err", "Value",
    # hash.Hash
    "Sum", "Reset", "BlockSize",
    # database/sql
    "Scan", "Next", "Prepare", "Exec", "Query", "QueryRow",
    # encoding
    "Encode", "Decode",
    # sync.Map (concrete type, but methods are extremely common)
    "Store", "Load", "LoadOrStore", "LoadAndDelete",
    "CompareAndSwap", "CompareAndDelete", "Range",
    # sync.WaitGroup
    "Wait",
})

# Go web framework HTTP method names
# Gin/Echo use uppercase: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
# Fiber uses lowercase: Get, Post, Put, Delete, Patch, Head, Options
#
# Route detection produces two outputs from the same extraction pass:
# 1. UsageContext records — matched by YAML patterns (ADR-3aaa v1.1.x) to enrich
#    handler symbols with concept: route metadata.
# 2. Route-marker symbols (kind="function" + meta.framework_role="route")
#    — consumed by the route_handler linker to create dispatches_to edges.
#    See py.py docstring for full architecture notes.
GO_HTTP_METHODS = {
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    "Get", "Post", "Put", "Delete", "Patch", "Head", "Options",
    "Del",  # Chi shorthand for Delete
}

# Uppercase HTTP methods for Go 1.22+ stdlib mux combined method-path
# patterns like Handle("POST /path", handler).
GO_HTTP_METHODS_UPPER = {
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
}

# Short method aliases that need normalization before .upper().
# Chi uses r.Del() as shorthand for r.Delete().
_GO_METHOD_ALIASES: dict[str, str] = {
    "Del": "DELETE",
}

# Gorilla mux simple methods: router.HandleFunc("/path", handler)
GORILLA_HANDLE_METHODS = {"HandleFunc", "Handle"}

# Gorilla mux builder chain terminators: .Handler(h) or .HandlerFunc(h)
GORILLA_CHAIN_TERMINATORS = {"Handler", "HandlerFunc"}

# Gorilla mux builder chain path methods: .Path("/x") or .PathPrefix("/x")
GORILLA_PATH_METHODS = {"Path", "PathPrefix"}

# Route group methods that take a path prefix and a closure.
# Macaron/Chi/Gin/Echo/Fiber all use Group; Chi also uses Route.
_GO_GROUP_METHODS = {"Group", "Route"}

# Route mount methods that attach a sub-handler at a path prefix.
# Unlike Group/Route (which use closures), Mount takes a handler argument
# that typically comes from a separate function.
_GO_MOUNT_METHODS = {"Mount"}

# Go builtin type names.  When used as bare call expressions — e.g.
# ``string(data)`` or ``int(x)`` — these are type conversions, not
# function calls.  We skip them during call resolution to avoid
# false-positive edges (e.g. string() → recalcRequest.string).
_GO_BUILTIN_TYPES = frozenset({
    "bool", "byte", "complex64", "complex128",
    "error", "float32", "float64",
    "int", "int8", "int16", "int32", "int64",
    "rune", "string",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
})

# Go builtin functions that should not be resolved to user-defined symbols.
_GO_BUILTIN_FUNCS = frozenset({
    "append", "cap", "clear", "close", "complex", "copy",
    "delete", "imag", "len", "make", "max", "min", "new",
    "panic", "print", "println", "real", "recover",
})


def find_go_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Go files in the repository."""
    yield from find_files(repo_root, ["*.go"])


class GoTreeSitterAnalyzer(TreeSitterAnalyzer):
    """TreeSitterAnalyzer wrapper for Go files.

    Overrides ``analyze()`` entirely because Go analysis uses a custom
    two-pass approach with routes, usage contexts, import alias extraction,
    and list-based global symbol resolution. The base class provides grammar
    availability checking via ``_check_grammar_available()``.
    """

    lang = "go"
    file_patterns: ClassVar[list[str]] = ["*.go"]
    grammar_module = "tree_sitter_go"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the Go analysis using the existing analyze_go logic."""
        return _analyze_go_impl(repo_root, max_files=max_files)


_analyzer = GoTreeSitterAnalyzer()


def is_go_tree_sitter_available() -> bool:
    """Check if tree-sitter with Go grammar is available."""
    return _analyzer._check_grammar_available()


# Keep GoAnalysisResult as an alias for backwards compatibility
GoAnalysisResult = AnalysisResult


def _extract_go_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Go function/method declaration.

    Returns a signature string like "(x int, y string) error" or "(a, b int) (int, error)"
    for Go functions. None if extraction fails.

    Args:
        node: A tree-sitter function_declaration or method_declaration node.
        source: Source bytes of the file.
    """
    if node.type not in (
        "function_declaration",
        "method_declaration",
        "method_elem",  # WI-vibad: interface-method specs also carry a
                        # "parameters"/"result" field (see _count_go_method_arity)
    ):
        return None  # pragma: no cover

    params_node = find_child_by_field(node, "parameters")
    if not params_node:
        return None  # pragma: no cover

    # Extract parameters from parameter_list
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            # Go parameters: can have multiple names sharing a type
            # e.g., "a, b int" or "x string"
            names: list[str] = []
            type_str = ""
            for param_child in child.children:
                if param_child.type == "identifier":
                    names.append(node_text(param_child, source))
                elif param_child.type in ("type_identifier", "pointer_type",
                                          "slice_type", "map_type", "array_type",
                                          "interface_type", "struct_type",
                                          "function_type", "channel_type",
                                          "qualified_type"):
                    type_str = node_text(param_child, source)

            if names and type_str:
                param_strs.append(f"{', '.join(names)} {type_str}")
            elif type_str:  # pragma: no cover
                # Unnamed parameter (rare but valid in Go interfaces)
                param_strs.append(type_str)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type(s) from result field
    result_node = find_child_by_field(node, "result")
    if result_node:
        ret_text = node_text(result_node, source)
        sig += f" {ret_text}"

    return sig


def normalize_go_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Go signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_go
    return normalize_signature_go(signature, type_params)


def _extract_import_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import alias → import path mappings from a Go file.

    Returns a dict mapping alias names to their import paths.
    For imports without explicit aliases, uses the last path component.

    Example:
        import (
            "fmt"                    -> {"fmt": "fmt"}
            pb "github.com/foo/bar"  -> {"pb": "github.com/foo/bar"}
        )
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    _process_import_spec(child, source, aliases)
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            _process_import_spec(spec, source, aliases)

    return aliases


def _extract_dot_imports(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> list[str]:
    """Extract dot-imported package paths (``import . "strings"``).

    Returns a list of package import paths brought into scope unprefixed.
    Powers the WI-vovum / WI-mafik gap fix: a bare call whose name was
    dot-imported gets an unresolved edge keyed to the source package.
    """
    dot_imports: list[str] = []
    for node in iter_tree(root_node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    _process_import_spec(child, source, {}, dot_imports)
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            _process_import_spec(spec, source, {}, dot_imports)
    return dot_imports


def _process_import_spec(
    spec: "tree_sitter.Node",
    source: bytes,
    aliases: dict[str, str],
    dot_imports: list[str] | None = None,
) -> None:
    """Process a single import_spec node and add to aliases dict.

    When ``dot_imports`` is provided and the spec is a dot import
    (``import . "strings"``), the import path is appended there
    so dot-imported bare calls can be associated with the source
    package downstream (WI-vovum).
    """
    path_node = find_child_by_field(spec, "path")
    if not path_node:
        return  # pragma: no cover - defensive for malformed AST

    import_path = node_text(path_node, source).strip('"')

    # Check for explicit alias
    name_node = find_child_by_field(spec, "name")
    if name_node:
        alias = node_text(name_node, source)
        if alias != "_" and alias != ".":  # Ignore blank imports
            aliases[alias] = import_path
        elif alias == "." and dot_imports is not None:
            dot_imports.append(import_path)
    else:
        # No explicit alias - derive the package identifier from the path.
        alias = _go_package_identifier(import_path)
        aliases[alias] = import_path


#: A trailing Go-modules major-version element: ``.../echo/v4`` -> ``v4``.
#: Matched only for N >= 2 BECAUSE THAT IS THE ACTUAL RULE — Go requires the
#: ``/vN`` suffix from major version 2 onward and v0/v1 modules carry none, so a
#: literal ``v1`` element is far more likely to be a real directory than a
#: version marker. Anchored, and digits-only, so ``v2beta1`` and ``v2bar`` are
#: not touched.
_GO_MAJOR_VERSION_ELEMENT = re.compile(r"^v(?:[2-9]|[1-9][0-9]+)$")

#: gopkg.in spells the same thing as a DOTTED SUFFIX: ``gopkg.in/yaml.v2``.
#: gopkg.in genuinely uses ``.v1`` (``gopkg.in/check.v1``), which is why this
#: pattern accepts any N and the path-element one above does not.
_GOPKG_IN_VERSION_SUFFIX = re.compile(r"\.v[0-9]+$")


def _go_package_identifier(import_path: str) -> str:
    """The identifier a Go import binds, derived from its path (INV-javid).

    Go source refers to an imported package by an identifier, and with no
    explicit alias that identifier is CONVENTIONALLY the last path element.
    The convention has two documented exceptions, and both put a major-version
    marker exactly where the identifier is expected:

      * **Go modules semantic import versioning** — a module at major version 2
        or higher carries the version as a trailing PATH ELEMENT, so
        ``github.com/labstack/echo/v4`` is imported as ``echo``.
      * **gopkg.in** — the version is a DOTTED SUFFIX on the last element, so
        ``gopkg.in/yaml.v2`` is imported as ``yaml``.

    Taking the last element literally therefore bound ``v4`` and left ``echo``
    unbound, which dropped the module hint from every call on that package and
    sent the dst to the ``external`` placeholder. Since Go modules, ``/vN`` is
    the standard for any library at v2+, so this was not an edge case.

    THIS IS A HEURISTIC AND SO WAS WHAT IT REPLACES. A package's real name is
    declared in its own source (``package gin``) and can differ from its path;
    for an external dependency that source is not present, so the identifier
    has to be inferred either way. The change is strictly a better inference,
    not a move from exact to approximate.

    THE OVER-STRIPPING HAZARD IS THE ONE TO WATCH, because it would invent a
    defect while closing one. ``v2`` is a legal package name, and
    Kubernetes-style API packages are routinely named ``v1alpha1`` /
    ``v2beta1``. Both patterns are anchored and digits-only, the path-element
    rule refuses to strip when there is no earlier element to fall back to, and
    the dotted rule is scoped to the ``gopkg.in`` host rather than applied
    everywhere a name happens to end in ``.vN``.
    """
    segments = [s for s in import_path.split("/") if s]
    if not segments:
        return import_path  # pragma: no cover - a non-empty path is guaranteed
    if segments[0] == "gopkg.in":
        return _GOPKG_IN_VERSION_SUFFIX.sub("", segments[-1]) or segments[-1]
    if len(segments) > 1 and _GO_MAJOR_VERSION_ELEMENT.match(segments[-1]):
        return segments[-2]
    return segments[-1]


def _import_path_to_dir_hint(import_path: str) -> str | None:
    """Convert an import path to a directory hint for matching.

    For paths like "github.com/example/src/checkoutservice/genproto",
    returns "/src/checkoutservice/genproto" or similar suffix that
    can be used to match against file paths.
    """
    # Look for common patterns that indicate local paths
    if "/src/" in import_path:
        # Extract from /src/ onwards
        tail = import_path.split("/src/", 1)[1]
        return f"/src/{tail}"

    # For other paths, use the last 2-3 components
    parts = import_path.split("/")
    if len(parts) >= 2:
        return "/" + "/".join(parts[-2:])

    return None


def _read_go_module_path(repo_root: Path) -> str | None:
    """Read the module path from go.mod at the repo root.

    Go modules declare their module path in ``go.mod`` (e.g.,
    ``module github.com/aquasecurity/trivy``).  This path is the
    prefix used in all internal import statements.  Stripping this
    prefix from import paths yields repo-relative directory paths
    that the suffix-matching resolver can match against file paths.

    Returns the module path string, or None if go.mod is absent or
    doesn't contain a module declaration.
    """
    go_mod = repo_root / "go.mod"
    if not go_mod.exists():
        return None
    try:
        content = go_mod.read_text(errors="replace")
    except OSError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.split(None, 1)[1].strip()
    return None


def parse_go_mod_dependencies(repo_root: Path) -> "DependencyManifest":
    """Parse go.mod to extract dependency metadata for tier classification.

    Reads ``require`` directives from go.mod and classifies each as
    direct or indirect (``// indirect`` comment).  Returns a
    ``DependencyManifest`` that ``create_boundary_nodes`` uses to assign
    tier 2 (direct dep) or tier 3 (indirect dep) to boundary nodes.

    Handles both block (``require ( ... )``) and single-line
    (``require github.com/foo/bar v1.0.0``) forms.  ``replace`` and
    other directives are ignored — they don't affect tier classification.
    """
    from hypergumbo_core.supply_chain import DependencyManifest

    go_mod = repo_root / "go.mod"
    if not go_mod.exists():
        return DependencyManifest()
    try:
        content = go_mod.read_text(errors="replace")
    except OSError:
        return DependencyManifest()

    entries: dict[str, dict] = {}
    in_require_block = False

    for line in content.splitlines():
        stripped = line.strip()

        # Track block require boundaries
        if stripped.startswith("require (") or stripped == "require(":
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue

        if in_require_block:
            # Inside require block: "github.com/foo/bar v1.0.0 // indirect"
            _parse_require_line(stripped, entries)
        elif stripped.startswith("require ") and "(" not in stripped:
            # Single-line require: "require github.com/foo/bar v1.0.0"
            _parse_require_line(stripped[len("require "):].strip(), entries)

    return DependencyManifest(entries=entries)


def _parse_require_line(line: str, entries: dict[str, dict]) -> None:
    """Parse a single require line and add to entries dict.

    Expected format: ``module_path version [// indirect]``
    Empty lines and comments are ignored.
    """
    if not line or line.startswith("//"):
        return
    parts = line.split()
    if len(parts) < 2:
        return
    module_path = parts[0]
    is_indirect = "// indirect" in line
    entries[module_path] = {"direct": not is_indirect}


def _strip_module_prefix(import_path: str, module_path: str) -> str:
    """Strip the Go module path prefix from an import path.

    Given ``import_path="github.com/example/trivy/pkg/log"`` and
    ``module_path="github.com/example/trivy"``, returns ``"pkg/log"``.

    If the import_path doesn't start with the module_path, returns
    the original import_path unchanged (it's an external dependency).
    """
    if import_path.startswith(module_path + "/"):
        return import_path[len(module_path) + 1:]
    if import_path == module_path:
        return ""
    return import_path


def _external_package_for_type(
    type_name: str,
    import_aliases: dict[str, str],
    module_path: Optional[str],
) -> Optional[str]:
    """Import path of ``type_name``'s package, when that package is OUTSIDE this module.

    A package-qualified receiver type reaches the analyzer in two flavours that look
    identical and must be resolved oppositely. ``notify.Stage`` names a type in a
    SIBLING PACKAGE OF THIS REPO, whose methods are in ``global_symbols`` under the
    unqualified ``Stage.Exec`` — stripping the prefix and looking it up is correct.
    ``http.Client`` names a type this repo does not define at all; stripping its prefix
    yields a bare ``Client`` that can collide with any same-named local type, and the
    resulting edge points at the wrong function while claiming
    ``resolution_quality: typed_receiver``.

    That collision is not hypothetical. go.py already carries a note that
    ``q.Set("k","v")`` where ``q`` is a ``url.Values`` "absorbed 13 spurious in-edges
    into one struct, poisoning the centrality ranking" on alertmanager. It was closed
    then by a guard keyed on the receiver type being UNKNOWN — which held only for as
    long as external composite literals stayed untyped.

    The discriminator is go.mod: an import path inside this module is a prefix match on
    the module path. Returns None when the type is unqualified, when its prefix is not
    an imported alias, when the package IS in this module, or when no module path is
    known — in that last case in-repo and external are genuinely indistinguishable
    here, so the caller keeps its existing behaviour rather than guessing. That is a
    DISCLOSED gap for a Go tree analysed without a go.mod, not a silent one.
    """
    if "." not in type_name:
        return None
    full_import_path = import_aliases.get(type_name.split(".")[0])
    if full_import_path is None or not module_path:
        return None
    if (
        full_import_path == module_path
        or full_import_path.startswith(module_path + "/")
    ):
        return None
    return full_import_path


def _count_go_method_arity(
    node: "tree_sitter.Node",
) -> tuple[int, int]:
    """Count parameter and return arity of a Go method node.

    Works on both ``method_elem`` (interface method specs) and
    ``method_declaration`` / ``function_declaration`` nodes.

    Returns (param_count, return_count).  For multi-name parameter
    declarations like ``a, b int`` each name is counted separately.
    For return types, a bare type (``error``) counts as 1, and a
    parameter_list (``(int, error)``) counts its declarations.

    Uses ``child_by_field_name("parameters")`` to correctly skip the
    receiver in ``method_declaration`` nodes (where the receiver is
    also a ``parameter_list`` but accessed via the "receiver" field).
    """
    param_count = 0
    return_count = 0

    # Use the named "parameters" field to avoid counting the receiver
    params_node = node.child_by_field_name("parameters")
    if params_node is not None:
        for pc in params_node.children:
            if pc.type == "parameter_declaration":
                # Count identifiers — ``a, b int`` has 2 names
                names = sum(
                    1 for n in pc.children if n.type == "identifier"
                )
                param_count += max(names, 1)

    # Result can be a single type node or a parameter_list (for tuples).
    result_node = node.child_by_field_name("result")
    if result_node is not None:
        if result_node.type == "parameter_list":
            for rc in result_node.children:
                if rc.type == "parameter_declaration":
                    names = sum(
                        1 for n in rc.children if n.type == "identifier"
                    )
                    return_count += max(names, 1)
        else:
            # Single return type (e.g., ``error``, ``*Foo``)
            return_count = 1

    return param_count, return_count


def _extract_interface_methods(
    interface_type_node: "tree_sitter.Node",
    source: bytes,
) -> set[tuple[str, int, int]]:
    """Extract method signatures from a Go interface definition.

    Returns a set of ``(name, param_count, return_count)`` tuples for
    structural interface matching.  Go's structural typing requires
    matching both method names AND arities: a struct with
    ``Close(ctx context.Context) error`` does NOT satisfy an interface
    with ``Close() error`` because the parameter counts differ.

    Example::

        type Notifier interface {
            Notify(msg string) error    // → {("Notify", 1, 1)}
            Close() error               // → {("Notify", 1, 1), ("Close", 0, 1)}
        }

    Embedded interfaces (e.g., ``io.Reader`` inside another interface) are
    skipped — we only match on explicitly declared methods in this interface.
    """
    methods: set[tuple[str, int, int]] = set()
    for child in interface_type_node.children:
        if child.type == "method_elem":
            # method_elem has a field_identifier child for the method name
            name_node = find_child_by_type(child, "field_identifier")
            if name_node:
                name = node_text(name_node, source)
                params, returns = _count_go_method_arity(child)
                methods.add((name, params, returns))
    return methods


def _extract_struct_embeddings(
    struct_type_node: "tree_sitter.Node",
    source: bytes,
) -> list[str]:
    """Extract embedded type names from a Go struct definition.

    Go struct embedding occurs when a field_declaration has a type but no
    explicit field name.  The embedded type's methods are promoted to the
    embedding struct, enabling implicit interface satisfaction.

    Recognised embedding forms::

        type MyStruct struct {
            BaseStruct            // simple embedding
            *AnotherStruct        // pointer embedding
            pkg.ExternalStruct    // qualified embedding
            *pkg.QualifiedPtr     // qualified pointer embedding
        }

    Returns a list of embedded type names (unqualified base name only,
    matching how the inheritance linker resolves names).
    """
    embedded: list[str] = []
    field_list = find_child_by_type(struct_type_node, "field_declaration_list")
    if field_list is None:  # pragma: no cover — struct_type always has field_declaration_list
        return embedded

    for field in field_list.children:
        if field.type != "field_declaration":
            continue

        # An embedding has no explicit field name — the field_declaration
        # contains only a type (and optionally a tag).  If there's an
        # identifier child that is NOT inside a pointer_type/qualified_type,
        # it's a named field, not an embedding.
        has_name = find_child_by_field(field, "name") is not None
        if has_name:
            continue

        # Extract the embedded type from the type child
        type_child = find_child_by_field(field, "type")
        if type_child is None:  # pragma: no cover — embeddings always have a type
            continue

        embedded_name = _extract_embedding_type_name(type_child, source)
        if embedded_name:
            embedded.append(embedded_name)

    return embedded


def _extract_struct_field_types(
    struct_type_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract named field-to-type mappings from a Go struct definition.

    Scans field_declaration children for named fields (those with an explicit
    field name, excluding embeddings).  Returns ``{field_name: type_name}``.

    Recognised type forms::

        integration Integration      → integration: Integration
        metrics     *Metrics         → metrics: Metrics
        cache       pkg.Cache        → cache: Cache (unqualified)
        data        map[string]int   → (skipped, not a named type)

    Built-in types (string, int, bool, error, etc.) are excluded because
    they don't correspond to user-defined methods and would create noise
    in chained-access resolution.
    """
    _GO_BUILTINS = frozenset({
        "string", "int", "int8", "int16", "int32", "int64",
        "uint", "uint8", "uint16", "uint32", "uint64",
        "float32", "float64", "complex64", "complex128",
        "bool", "byte", "rune", "error", "any",
    })
    field_types: dict[str, str] = {}
    field_list = find_child_by_type(struct_type_node, "field_declaration_list")
    if field_list is None:  # pragma: no cover
        return field_types

    for field in field_list.children:
        if field.type != "field_declaration":
            continue

        name_node = find_child_by_field(field, "name")
        type_child = find_child_by_field(field, "type")
        if name_node is None or type_child is None:
            continue

        field_name = node_text(name_node, source)

        # Unwrap pointer types: *Metrics → Metrics
        actual_type = type_child
        if actual_type.type == "pointer_type":
            inner = find_child_by_type(actual_type, "type_identifier")
            if inner is None:
                # *pkg.Type or *map[...]... — try qualified_type
                inner = find_child_by_type(actual_type, "qualified_type")
            if inner is not None:
                actual_type = inner

        # Extract type name from type_identifier or qualified_type
        if actual_type.type == "type_identifier":
            type_name = node_text(actual_type, source)
            if type_name not in _GO_BUILTINS:
                field_types[field_name] = type_name
        elif actual_type.type == "qualified_type":
            # pkg.Type → store the full qualified name (e.g. "http.Client")
            # so that chained field call resolution can recover the import
            # path from the package prefix for IO boundary detection.
            full_text = node_text(actual_type, source)
            tid = find_child_by_type(actual_type, "type_identifier")
            if tid:
                type_name = node_text(tid, source)
                if type_name not in _GO_BUILTINS:
                    field_types[field_name] = full_text

    return field_types


def _extract_embedding_type_name(
    type_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the base type name from an embedded struct field type node.

    Handles: type_identifier, pointer_type(→type_identifier),
    qualified_type(→type_identifier), pointer_type(→qualified_type),
    and generic_type variants.
    """
    if type_node.type == "type_identifier":
        return node_text(type_node, source)

    if type_node.type == "pointer_type":  # pragma: no cover
        # *Struct or *pkg.Struct — tree-sitter-go represents pointer
        # embeddings with '*' as a raw token child of field_declaration,
        # so the type field points to the inner type directly.  This
        # branch is defensive for future grammar changes.
        inner = find_child_by_type(type_node, "type_identifier")
        if inner:
            return node_text(inner, source)
        qual = find_child_by_type(type_node, "qualified_type")
        if qual:
            tid = find_child_by_type(qual, "type_identifier")
            if tid:
                return node_text(tid, source)
        return None

    if type_node.type == "qualified_type":
        # pkg.Struct — use the unqualified name for resolution
        tid = find_child_by_type(type_node, "type_identifier")
        if tid:
            return node_text(tid, source)
        return None  # pragma: no cover — qualified_type always has type_identifier

    if type_node.type == "generic_type":
        # Cache[K] — extract the base type name
        base = find_child_by_type(type_node, "type_identifier")
        if base:
            return node_text(base, source)
        return None  # pragma: no cover — generic_type always has a base type

    return None  # pragma: no cover — unrecognized type node


def _detect_interface_assertion(
    var_spec: "tree_sitter.Node",
    source: bytes,
    impl_assertions: dict[str, list[str]],
) -> None:
    """Detect Go interface-implementation assertions in var specs.

    Parses patterns like ``var _ Interface = &Struct{}``,
    ``var _ Interface = (*Struct)(nil)``, and generic forms like
    ``var _ Cache[string] = &StringCache{}`` which are compile-time
    assertions that a struct satisfies an interface. Populates
    impl_assertions mapping struct names to interface names.

    Interface type forms: ``type_identifier`` (simple), ``qualified_type``
    (e.g., ``io.Reader``), ``generic_type`` (e.g., ``Cache[string]`` or
    ``entity.Interface[T]``).

    Two RHS forms are recognized:
    1. ``&Struct{}`` — unary_expression(&) → composite_literal → type_identifier
    2. ``(*Struct)(nil)`` — call_expression → parenthesized_expression →
       pointer_type → type_identifier
    """
    # Check that the variable name is "_" (blank identifier)
    name_node = find_child_by_field(var_spec, "name")
    if name_node is None or node_text(name_node, source) != "_":
        return

    # Extract the interface type from the type annotation
    type_node = find_child_by_field(var_spec, "type")
    if type_node is None:
        return

    if type_node.type == "type_identifier":
        iface_name = node_text(type_node, source)
    elif type_node.type == "qualified_type":
        # e.g., io.Writer → use "io.Writer"
        iface_name = node_text(type_node, source)
    elif type_node.type == "generic_type":
        # e.g., Cache[string] or entity.Interface[T]
        # The base type is a child type_identifier or qualified_type
        base_tid = find_child_by_type(type_node, "type_identifier")
        if base_tid:
            iface_name = node_text(base_tid, source)
        else:
            qual = find_child_by_type(type_node, "qualified_type")
            if qual:
                iface_name = node_text(qual, source)
            else:
                return  # pragma: no cover — generic_type always has a base type
    else:
        return

    # Extract the implementing struct from the RHS expression
    value_node = find_child_by_field(var_spec, "value")
    if value_node is None:  # pragma: no cover — Go syntax requires RHS for var _ T = ...
        return

    struct_name = _extract_struct_from_assertion_rhs(value_node, source)
    if struct_name is None:
        return

    if struct_name not in impl_assertions:
        impl_assertions[struct_name] = []
    impl_assertions[struct_name].append(iface_name)


def _extract_struct_from_assertion_rhs(
    expr_list: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Extract struct name from the RHS of a var _ Interface = ... assertion.

    Handles two patterns:
    1. ``&Struct{}`` — expression_list → unary_expression → composite_literal
    2. ``(*Struct)(nil)`` — expression_list → call_expression →
       parenthesized_expression → pointer_type → type_identifier
    """
    # The value is wrapped in an expression_list
    for child in expr_list.children:
        if child.type == "unary_expression":
            # Pattern: &Struct{} or &pkg.Struct{}
            for sub in child.children:
                if sub.type == "composite_literal":
                    type_node = find_child_by_type(sub, "type_identifier")
                    if type_node:
                        return node_text(type_node, source)
                    # qualified: &pkg.Struct{}
                    qual_node = find_child_by_type(sub, "qualified_type")
                    if qual_node:
                        tid = find_child_by_type(qual_node, "type_identifier")
                        if tid:
                            return node_text(tid, source)
        elif child.type == "call_expression":
            # Pattern: (*Struct)(nil)
            # call_expression → function: parenthesized_expression, arguments: argument_list
            func_node = find_child_by_field(child, "function")
            if func_node and func_node.type == "parenthesized_expression":
                # Inside parenthesized_expression: unary_expression(*) → identifier
                for sub in func_node.children:
                    if sub.type == "unary_expression":
                        tid = find_child_by_type(sub, "identifier")
                        if tid:
                            return node_text(tid, source)
    return None


def _extract_receiver_type_from_node(
    receiver_node: "tree_sitter.Node",
    source: bytes,
) -> str:
    """Extract the receiver type name from a method_declaration's receiver node.

    Given the parameter_list node that serves as the receiver, walks through
    its children to find the parameter_declaration and extracts the type name.
    Handles both value receivers (``u User``) and pointer receivers (``u *User``),
    returning just the type name in either case (e.g., ``"User"``).

    Returns an empty string if no type can be extracted.
    """
    for child in receiver_node.children:
        if child.type == "parameter_declaration":
            type_node = find_child_by_field(child, "type")
            if type_node:
                if type_node.type == "pointer_type":
                    elem_node = find_child_by_type(type_node, "type_identifier")
                    if elem_node:
                        return node_text(elem_node, source)
                elif type_node.type == "type_identifier":
                    return node_text(type_node, source)
    return ""  # pragma: no cover - well-formed Go always has a typed receiver


def _get_enclosing_func_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Walk up the tree to find the enclosing function/method name.

    For method declarations, returns the qualified name (``Type.Method``).
    For function declarations, returns the simple function name.
    For positions outside any function, returns None.

    Used to scope variable type bindings to their enclosing function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source)
        elif current.type == "method_declaration":
            name_node = find_child_by_field(current, "name")
            receiver_node = find_child_by_field(current, "receiver")
            if name_node:
                method_name = node_text(name_node, source)
                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )
                    if receiver_type:
                        return f"{receiver_type}.{method_name}"
                return method_name  # pragma: no cover - methods always have receivers
        current = current.parent
    return None


def _extract_go_package(
    root: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract the Go package name from the file's ``package_clause``.

    Returns ``None`` for malformed files lacking a package clause.
    """
    for child in root.children:
        if child.type == "package_clause":
            for sub in child.children:
                if sub.type == "package_identifier":
                    return node_text(sub, source)
    return None  # pragma: no cover - well-formed Go always has a package clause


def _make_go_qualified_name(
    package: Optional[str], receiver_type: Optional[str], name: str
) -> str:
    """Build a Go qualified name: ``pkg.[Receiver.]name``.

    For top-level functions / structs / interfaces, omit the receiver
    segment. For methods on a receiver type, include it as the "class"
    segment.
    """
    sep = separator_for_language("go")  # "."
    parts: list[str] = []
    if package:
        parts.append(package)
    if receiver_type:
        parts.append(receiver_type)
    parts.append(name)
    return sep.join(parts)



def _go_init_value(
    var_spec: "tree_sitter.Node", value_node: "tree_sitter.Node | None",
) -> "tree_sitter.Node | None":
    """The initializer expression of a Go ``var_spec``.

    Go wraps it in an ``expression_list`` (``var a, b = f(), g()``), so the
    ``value`` field is absent for the common single-binding case and the
    expression sits one level down. Returns the first element, which is the
    initializer for a single-name spec.
    """
    node = value_node or find_child_by_type(var_spec, "expression_list")
    if node is None:
        return None
    # The `value` field itself resolves to the expression_list, so unwrap
    # whichever we got rather than assuming the field is absent.
    if node.type == "expression_list":
        return node.children[0] if node.children else None
    return node


def _go_visibility_modifiers(name: str) -> list[str]:
    """Derive visibility modifiers from Go naming convention.

    In Go, identifiers starting with an uppercase letter are exported (public).
    All others are unexported (package-private).  We use Go-native terms
    so the cross-language normalization layer (WI-silik) can map them.
    """
    # For qualified names like "Receiver.Method", check the method part
    short = name.rsplit(".", 1)[-1] if "." in name else name
    if short and short[0].isupper():
        return ["exported"]
    return ["unexported"]


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
    file_stable_id: str = "",
) -> FileAnalysis:
    """Extract symbols from a single Go file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    WI-bokab (v7): ``file_stable_id`` is the file-identity anchor for this
    file's symbols. ``_analyze_go_impl`` computes it once per file from the
    REPO-RELATIVE path (``make_file_stable_id("go", normalize_path(rel_path))``)
    — ``file_path`` here is ABSOLUTE (``find_go_files`` yields absolute paths),
    so it cannot be folded directly. The anchor is threaded into every
    ``make_typed_stable_id`` call's ``file_stable_id=`` slot so same-name
    functions/methods in different files hash distinctly. Defaults to ``""``
    (the no-op fold) for legacy/test callers that don't supply it.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
        run.record_failed_file(str(file_path), f"{type(e).__name__}: {e}")
        return FileAnalysis()

    analysis = FileAnalysis()

    # WI-potun: extract //go:build constraint from top-of-file comments.
    # Build directives appear as comment nodes before the package clause.
    build_constraint: str | None = None
    for child in tree.root_node.children:
        if child.type == "comment":
            text = node_text(child, source).strip()
            if text.startswith("//go:build "):
                build_constraint = text[len("//go:build "):]
                break
        elif child.type == "package_clause":
            break  # past the preamble, no more build directives

    # ADR-0032 Phase 4 PR4: extract package once for qualified_name population.
    package_name = _extract_go_package(tree.root_node, source)

    # Extract import aliases for this file (used later in edge extraction)
    analysis.import_aliases = _extract_import_aliases(tree.root_node, source)
    # WI-vovum: dot-imported packages bring names into the file scope
    # unprefixed; capture them so call-emit can attribute bare calls.
    analysis.dot_imports = _extract_dot_imports(tree.root_node, source)

    # Collect interface-implementation assertions: struct_name -> [interface_names]
    # Populated during tree walk, applied to struct symbols after extraction.
    impl_assertions: dict[str, list[str]] = {}

    # For structural interface matching (Go implicit satisfaction):
    # interface_name -> set of (method_name, param_count, return_count)
    interface_method_sets: dict[str, set[tuple[str, int, int]]] = {}
    # struct_name -> set of (method_name, param_count, return_count)
    struct_method_sets: dict[str, set[tuple[str, int, int]]] = {}
    # WI-tudib: track embedding relationships for promoted-method resolution.
    # struct_name -> list of embedded type names (built during tree walk,
    # used after walk to augment struct_method_sets with promoted methods).
    embedding_map: dict[str, list[str]] = {}

    for node in iter_tree(tree.root_node):
        # Function declaration (including methods with receivers)
        if node.type == "function_declaration":
            name_node = find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract function signature
                signature = _extract_go_signature(node, source)
                modifiers = _go_visibility_modifiers(func_name)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_go_signature(signature)
                stable_id = make_typed_stable_id(
                    "function", norm_sig, visibility_from_modifiers(modifiers),
                    name=func_name,
                    qualified_name=_make_go_qualified_name(package_name, None, func_name),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, func_name, "function"),
                    name=func_name,
                    kind="function",
                    language="go",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "go"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    shape_id=_analyzer.compute_shape_id(node),
                    is_exported=bool(func_name) and func_name[0].isupper(),
                    qualified_name=_make_go_qualified_name(package_name, None, func_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "go"),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

                # WI-kuroj: capture return type for standalone functions
                # (e.g. NewEngine() → Engine, ParseQuery() → Query).
                if signature:
                    ret_type = _go_return_type_from_signature(signature)
                    if ret_type:
                        analysis.method_return_types[func_name] = ret_type

        # Method declaration (function with receiver)
        elif node.type == "method_declaration":
            name_node = find_child_by_field(node, "name")
            receiver_node = find_child_by_field(node, "receiver")

            if name_node:
                method_name = node_text(name_node, source)
                receiver_type = ""

                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )

                full_name = f"{receiver_type}.{method_name}" if receiver_type else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract method signature
                signature = _extract_go_signature(node, source)
                modifiers = _go_visibility_modifiers(full_name)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_go_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                    name=method_name,
                    qualified_name=_make_go_qualified_name(package_name, receiver_type or None, method_name),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="go",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "go"),
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    shape_id=_analyzer.compute_shape_id(node),
                    is_exported=bool(method_name) and method_name[0].isupper(),
                    qualified_name=_make_go_qualified_name(package_name, receiver_type or None, method_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "go"),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

                # WI-kuroj: populate return-type registry for chained
                # receiver resolution (e.g. x := e.Query() → x has
                # type Result if Engine.Query returns Result).
                if receiver_type and signature:
                    ret_type = _go_return_type_from_signature(signature)
                    if ret_type:
                        analysis.method_return_types[full_name] = ret_type

                # Track struct method sets for structural interface matching
                if receiver_type:
                    if receiver_type not in struct_method_sets:
                        struct_method_sets[receiver_type] = set()
                    arity = _count_go_method_arity(node)
                    struct_method_sets[receiver_type].add(
                        (method_name, arity[0], arity[1])
                    )

        # Type declaration (struct or interface)
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = find_child_by_field(child, "name")
                    type_node = find_child_by_field(child, "type")

                    if name_node and type_node:
                        type_name = node_text(name_node, source)
                        start_line = child.start_point[0] + 1
                        end_line = child.end_point[0] + 1

                        if type_node.type == "struct_type":
                            kind = "struct"
                        elif type_node.type == "interface_type":
                            kind = "interface"
                            # Extract method names for structural matching.
                            # Skip empty interfaces (interface{}) — they
                            # would match every struct.
                            iface_methods = _extract_interface_methods(
                                type_node, source,
                            )
                            if iface_methods:
                                interface_method_sets[type_name] = iface_methods

                            # Create method symbols for each interface method.
                            # Named InterfaceName.MethodName so the containment
                            # linker connects interface→method and the type
                            # hierarchy linker creates dispatches_to edges.
                            for iface_child in type_node.children:
                                if iface_child.type != "method_elem":
                                    continue
                                mname_node = find_child_by_type(
                                    iface_child, "field_identifier",
                                )
                                if not mname_node:
                                    continue  # pragma: no cover
                                mname = node_text(mname_node, source)
                                qualified = f"{type_name}.{mname}"
                                m_start = iface_child.start_point[0] + 1
                                m_end = iface_child.end_point[0] + 1
                                m_modifiers = _go_visibility_modifiers(qualified)
                                # WI-vibad: interface-method declarations are
                                # kind="method", which is excluded from the
                                # WI-rihob name-scoped backstop (overloads/
                                # cross-interface same-name methods would
                                # collide), so mint the typed producer stable_id
                                # here exactly as the concrete-method site does.
                                # method_elem always carries a "parameters"
                                # field, so the signature is a real "(...)"
                                # string; the typed id also folds name +
                                # qualified_name, keeping same-named methods
                                # across interfaces distinct.
                                _m_norm_sig = normalize_go_signature(
                                    _extract_go_signature(iface_child, source)
                                )
                                # Guard exactly as the two concrete-method sites
                                # do: a signature-less method mints NO typed
                                # stable_id rather than one built from an empty
                                # signature, which would be the name-only key
                                # the typed id exists to avoid colliding on.
                                m_stable_id = make_typed_stable_id(
                                    "method",
                                    _m_norm_sig,
                                    visibility_from_modifiers(m_modifiers),
                                    name=mname,
                                    qualified_name=_make_go_qualified_name(
                                        package_name, type_name, mname
                                    ),
                                    file_stable_id=file_stable_id,
                                ) if _m_norm_sig else None
                                m_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path),
                                        m_start, m_end, qualified, "method",
                                    ),
                                    name=qualified,
                                    kind="method",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=m_start,
                                        end_line=m_end,
                                        start_col=iface_child.start_point[1],
                                        end_col=iface_child.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    modifiers=m_modifiers,
                                    line_span=1,
                                    stable_id=m_stable_id,
                                    shape_id=_analyzer.compute_shape_id(iface_child),
                                    is_exported=bool(mname) and mname[0].isupper(),
                                    qualified_name=_make_go_qualified_name(package_name, type_name, mname),
                                    cyclomatic_complexity=compute_cyclomatic_complexity(iface_child, "go"),
                                )
                                analysis.symbols.append(m_sym)
                                analysis.symbol_by_name[qualified] = m_sym
                        else:
                            kind = "type"

                        # Detect struct embedding (base_classes)
                        embedded_types: list[str] = []
                        if kind == "struct":
                            embedded_types = _extract_struct_embeddings(
                                type_node, source,
                            )
                            # WI-tudib: record for promoted-method resolution
                            if embedded_types:
                                embedding_map[type_name] = embedded_types
                            # Extract field name→type mappings for chained
                            # field access resolution (e.g., r.integration.Notify()
                            # → Integration.Notify when integration is type Integration).
                            field_types = _extract_struct_field_types(
                                type_node, source,
                            )
                            if field_types:
                                analysis.class_field_types[type_name] = field_types

                        symbol = Symbol(
                            id=make_symbol_id("go", str(file_path), start_line, end_line, type_name, kind),
                            name=type_name,
                            kind=kind,
                            language="go",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=child.start_point[1],
                                end_col=child.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            modifiers=_go_visibility_modifiers(type_name),
                            meta={"base_classes": embedded_types} if embedded_types else None,
                            line_span=end_line - start_line + 1,
                            shape_id=_analyzer.compute_shape_id(child),
                            is_exported=bool(type_name) and type_name[0].isupper(),
                            qualified_name=_make_go_qualified_name(package_name, None, type_name),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[type_name] = symbol

                        # WI-jusus (emission-parity F5): emit a kind="field"
                        # Symbol per NAMED struct field. Embeddings carry no
                        # field name (captured above as base_classes), so they
                        # produce no field symbol. One field_declaration may name
                        # several fields (`a, b int`) — emit one Symbol per name.
                        if kind == "struct":
                            field_list = find_child_by_type(
                                type_node, "field_declaration_list",
                            )
                            for field in field_list.children if field_list else ():
                                if field.type != "field_declaration":
                                    continue
                                ftype_node = find_child_by_field(field, "type")
                                ftype = (
                                    node_text(ftype_node, source)
                                    if ftype_node is not None else None
                                )
                                f_start = field.start_point[0] + 1
                                f_end = field.end_point[0] + 1
                                for nn in field.children:
                                    if nn.type != "field_identifier":
                                        continue
                                    fname = node_text(nn, source)
                                    fqn = f"{type_name}.{fname}"
                                    f_qualified = _make_go_qualified_name(
                                        package_name, type_name, fname,
                                    )
                                    f_sym = Symbol(
                                        id=make_symbol_id(
                                            "go", str(file_path),
                                            f_start, f_end, fqn, "field",
                                        ),
                                        name=fqn,
                                        kind="field",
                                        language="go",
                                        path=str(file_path),
                                        span=Span(
                                            start_line=f_start,
                                            end_line=f_end,
                                            start_col=field.start_point[1],
                                            end_col=field.end_point[1],
                                        ),
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        modifiers=_go_visibility_modifiers(fname),
                                        signature=ftype,
                                        stable_id=make_typed_stable_id(
                                            "field", ftype or "",
                                            name=fname,
                                            qualified_name=f_qualified,
                                            file_stable_id=file_stable_id,
                                        ),
                                        line_span=f_end - f_start + 1,
                                        is_exported=bool(fname) and fname[0].isupper(),
                                        qualified_name=f_qualified,
                                    )
                                    analysis.symbols.append(f_sym)
                                    analysis.symbol_by_name[fqn] = f_sym

        # var declarations: interface assertions AND package-level aliases
        elif node.type == "var_declaration":
            # Only package (file) scope vars are module variables. The all-node
            # walk also reaches `var x = ...` inside function/block bodies
            # (parent `statement_list`); those are LOCALS, not module variables,
            # and must not be emitted (INV-sidab). Interface-assertion detection
            # still runs for every var_spec regardless of scope.
            is_package_level = (
                node.parent is not None and node.parent.type == "source_file"
            )
            for child in node.children:
                if child.type == "var_spec":
                    _detect_interface_assertion(child, source, impl_assertions)
                    if not is_package_level:
                        continue

                    # Extract package-level var aliases as symbols.
                    # Pattern: var Name = expr (has initializer, not blank _)
                    # This makes aliases like `var String = slog.String` visible
                    # to the resolver when other packages call log.String().
                    vname_node = find_child_by_field(child, "name")
                    if vname_node is None:  # pragma: no cover
                        continue
                    vname = node_text(vname_node, source)
                    if vname == "_":
                        continue

                    # Only extract vars with initializers — plain `var x int`
                    # declarations are not callable aliases.
                    vvalue_node = find_child_by_field(child, "value")
                    if vvalue_node is None:
                        # Check for expression_list children as alternative
                        # tree-sitter representation
                        has_init = any(
                            c.type == "expression_list" for c in child.children
                        )
                        if not has_init:
                            continue

                    start_line = child.start_point[0] + 1
                    end_line = child.end_point[0] + 1
                    modifiers = _go_visibility_modifiers(vname)

                    vsymbol = Symbol(
                        id=make_symbol_id("go", str(file_path), start_line, end_line, vname, "variable"),
                        name=vname,
                        kind="variable",
                        language="go",
                        path=str(file_path),
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=child.start_point[1],
                            end_col=child.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        modifiers=modifiers,
                        meta=(
                            {"constructed_from": _go_cf}
                            if (_go_cf := constructed_from_callee(
                                _go_init_value(child, vvalue_node), source))
                            else None
                        ),
                        line_span=end_line - start_line + 1,
                        shape_id=_analyzer.compute_shape_id(child),
                        is_exported=bool(vname) and vname[0].isupper(),
                    )
                    analysis.symbols.append(vsymbol)
                    analysis.symbol_by_name[vname] = vsymbol

    # Populate docstrings from tree-sitter comments in a single pass
    populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

    # Apply interface assertions to struct symbols as base_classes metadata
    for sym in analysis.symbols:
        if sym.kind == "struct" and sym.name in impl_assertions:
            if sym.meta is None:
                sym.meta = {}
            sym.meta.setdefault("base_classes", []).extend(
                impl_assertions[sym.name]
            )

    # WI-tudib: augment struct_method_sets with promoted methods from
    # embedded types.  In Go, embedding promotes the embedded type's
    # methods to the embedder, enabling implicit interface satisfaction
    # (e.g. WeekdayRange embeds InclusiveRange and inherits setBegin/setEnd).
    # Walk the embedding_map and union each embedded type's methods
    # into the embedder's method set.  Handles transitive embedding.
    if embedding_map and struct_method_sets:
        # Iterative resolution handles transitive embedding (A embeds B,
        # B embeds C → A gets C's methods too).  Cap iterations to prevent
        # cycles (shouldn't happen in valid Go, but be defensive).
        for _ in range(len(embedding_map) + 1):
            changed = False
            for struct_name, embedded_names in embedding_map.items():
                if struct_name not in struct_method_sets:
                    struct_method_sets[struct_name] = set()
                current = struct_method_sets[struct_name]
                for emb_name in embedded_names:
                    emb_methods = struct_method_sets.get(emb_name, set())
                    if not emb_methods.issubset(current):
                        struct_method_sets[struct_name] = current | emb_methods
                        changed = True
            if not changed:
                break

    # Per-file structural interface matching: Go implicit satisfaction.
    # A struct satisfies an interface if its method set is a superset of
    # the interface's method set (same method names).
    if interface_method_sets and struct_method_sets:
        for struct_name, struct_methods in struct_method_sets.items():
            for iface_name, iface_methods in interface_method_sets.items():
                if not iface_methods.issubset(struct_methods):
                    continue
                # Check not already added via explicit assertion
                struct_sym = next(
                    (s for s in analysis.symbols
                     if s.kind == "struct" and s.name == struct_name),
                    None,
                )
                if struct_sym is None:
                    continue  # pragma: no cover
                if struct_sym.meta is None:
                    struct_sym.meta = {}
                existing = struct_sym.meta.get("base_classes", [])
                if iface_name not in existing:
                    struct_sym.meta.setdefault("base_classes", []).append(
                        iface_name,
                    )

    # Store method sets for cross-file structural matching in _analyze_go_impl
    analysis.interface_method_sets = interface_method_sets
    analysis.struct_method_sets = struct_method_sets

    # WI-potun: stamp build_constraint on all symbols from this file.
    if build_constraint:
        for sym in analysis.symbols:
            if sym.meta is None:
                sym.meta = {}
            sym.meta["build_constraint"] = build_constraint

    return analysis


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    For calls inside anonymous functions (func_literal), continues walking up
    to find the containing named function. This enables call attribution for
    patterns like: go func() { helper() }()

    For method declarations, tries the qualified name first (``Type.Method``)
    to avoid incorrect attribution when multiple types define the same method
    name. Falls back to short name if the qualified name isn't in local_symbols.

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        elif current.type == "method_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Try qualified name first to handle same-name methods
                receiver_node = find_child_by_field(current, "receiver")
                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )
                    if receiver_type:
                        qualified = f"{receiver_type}.{method_name}"
                        if qualified in local_symbols:
                            return local_symbols[qualified]
                # Fall back to short name (when qualified name not in local_symbols)
                if method_name in local_symbols:  # pragma: no cover - qualified always present
                    return local_symbols[method_name]  # pragma: no cover
        # For func_literal (anonymous functions), continue walking up
        # to find the containing named function rather than returning None
        # This handles: go func() { helper() }(), callbacks, etc.
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_go_var_types(
    root_node: "tree_sitter.Node",
    source: bytes,
    method_return_type_registry: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Extract function-scoped variable-to-type-name mappings from Go code.

    Scans for statements and declarations where a variable is bound to a
    known type, scoped by the enclosing function/method name.

    Recognized patterns:

        s := &Server{}            → s has type Server (in enclosing func)
        s := Server{}             → s has type Server (in enclosing func)
        var c Client              → c has type Client (in enclosing func)
        var p *Client             → p has type Client (in enclosing func)
        func foo(s *Server)       → s has type Server (in foo)
        func (s *Server) Foo()    → s has type Server (in Server.Foo)
        x := e.Query()            → x has type Result (via return-type registry,
                                    when e has type Engine and Engine.Query
                                    returns Result)

    Only the first assignment to a variable within a function wins
    (single-assignment SSA assumption per function scope). Built-in types
    (string, int, etc.) are excluded because they don't correspond to
    user-defined methods.

    When ``method_return_type_registry`` is provided, chained method-call
    return types are resolved: if the RHS is a call expression with a
    typed receiver, the receiver type + method name is looked up in the
    registry and the return type (if any) is assigned to the LHS variable.
    This enables resolution chains like ``e := NewEngine(); r := e.Query();
    r.Rows()`` — without the registry, ``r`` has no type and ``r.Rows()``
    falls through to the unresolved path.

    Returns a nested dict: ``{func_name: {var_name: type_name}}``.
    For methods, func_name is qualified (``Type.Method``).
    """
    # Go built-in types that don't have user-defined methods
    _GO_BUILTINS = frozenset({
        "string", "int", "int8", "int16", "int32", "int64",
        "uint", "uint8", "uint16", "uint32", "uint64",
        "float32", "float64", "complex64", "complex128",
        "bool", "byte", "rune", "error", "any",
    })
    scoped_var_types: dict[str, dict[str, str]] = {}

    for node in iter_tree(root_node):
        # Pattern 1: Short var declaration  s := &Server{} or s := Server{}
        if node.type == "short_var_declaration":
            # Left side: expression_list with identifier(s)
            lhs = node.children[0] if node.children else None
            rhs = node.children[-1] if len(node.children) >= 3 else None
            if lhs is None or rhs is None:
                continue  # pragma: no cover - tree-sitter always produces valid AST

            # Get var name from first identifier in left expression_list
            var_name = None
            if lhs.type == "expression_list":
                for child in lhs.children:
                    if child.type == "identifier":
                        var_name = node_text(child, source)
                        break
            if var_name is None:
                continue  # pragma: no cover - short var always has identifier LHS

            # Scope to enclosing function
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue  # pragma: no cover - short vars always inside functions
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids redeclaration in same scope

            # Get type from right side.  Record even if type unknown (empty
            # string) so the variable name is tracked for false-positive
            # prevention in function reference detection.
            type_name = _type_from_rhs(rhs, source)

            # WI-kuroj: when _type_from_rhs can't infer a type from
            # the RHS (e.g. x := e.Query()), try the return-type
            # registry.  If the RHS is a method call on a receiver
            # whose type is already known, look up the return type.
            #
            # WI-jolif: this used to test ``rhs.type == "call_expression"``
            # directly, and ``rhs`` is the ``expression_list`` wrapper — a
            # condition the node can NEVER satisfy, so the whole block was dead
            # from the day it shipped. It now unwraps through the same helper
            # ``_type_from_rhs`` uses, so the two consumers cannot disagree about
            # what "the right-hand side" means. The stale
            # ``# pragma: no cover - requires Go tree-sitter grammar`` that sat on
            # this branch is gone with it: the grammar IS installed, and the
            # pragma was excusing itself on the same false premise that kept the
            # three end-to-end tests skipped.
            _rhs_expr = _unwrap_rhs_expression(rhs)
            if (
                not type_name
                and method_return_type_registry
                and _rhs_expr is not None
                and _rhs_expr.type == "call_expression"
            ):
                call_func = find_child_by_field(_rhs_expr, "function")
                if call_func is not None and call_func.type == "selector_expression":
                    operand = find_child_by_field(call_func, "operand")
                    field_node = find_child_by_field(call_func, "field")
                    if operand is not None and field_node is not None:
                        recv_name = node_text(operand, source)
                        method_name = node_text(field_node, source)
                        recv_type = func_vars.get(recv_name, "")
                        if recv_type:
                            # Fold: registry keys carry a BARE receiver, values
                            # are qualified (WI-doluf).
                            qualified = (
                                f"{_bare_go_type(recv_type)}.{method_name}"
                            )
                            type_name = method_return_type_registry.get(
                                qualified
                            )
                elif call_func is not None and call_func.type == "identifier":
                    # WI-doluf: a PLAIN function call -- ``conn := makeConn()``.
                    # This branch did not exist, so the registry was populated
                    # for standalone functions (``analysis.method_return_types
                    # [func_name]``) and consulted only for method calls. Every
                    # factory function in every Go repo fell through to an
                    # untyped variable, in-repo return types included: the
                    # control fixture ``res := makeResult(); res.Rows()``
                    # resolved to ``go:external:0-0:Rows`` before this branch.
                    fn_name = node_text(call_func, source)
                    if (
                        fn_name not in _GO_BUILTIN_TYPES
                        and fn_name not in _GO_BUILTIN_FUNCS
                    ):
                        type_name = method_return_type_registry.get(fn_name)

            if type_name and type_name not in _GO_BUILTINS:
                func_vars[var_name] = type_name
            else:
                func_vars[var_name] = ""

        # Pattern 2: Var declaration  var c Client  or  var p *Client
        # Also handles: var n Notifier = &DiscordNotifier{}
        # When an initializer with a concrete type exists, prefer it over
        # the declared (interface) type for dispatch narrowing.
        elif node.type == "var_spec":
            # var_spec children: identifier, type_identifier (or pointer_type),
            # and optionally an expression_list with the initializer
            name_node = None
            type_node = None
            init_node = None
            for child in node.children:
                if child.type == "identifier" and name_node is None:
                    name_node = child
                elif child.type in (
                    "type_identifier", "pointer_type", "qualified_type",
                ):
                    type_node = child
                elif child.type == "expression_list":
                    init_node = child

            if name_node is None or type_node is None:
                continue

            var_name = node_text(name_node, source)

            # Scope to enclosing function (file-level vars have no enclosing func)
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids redeclaration in same scope

            # If there's an initializer, try to extract the concrete type
            # from the RHS (e.g., &DiscordNotifier{} → DiscordNotifier).
            # Prefer the concrete type over the declared (interface) type
            # for dispatch narrowing.
            concrete_type = None
            if init_node is not None:
                concrete_type = _type_from_rhs(init_node, source)

            declared_type = _type_identifier_from_node(type_node, source)

            if concrete_type and concrete_type not in _GO_BUILTINS:
                func_vars[var_name] = concrete_type
            elif declared_type and declared_type not in _GO_BUILTINS:
                func_vars[var_name] = declared_type

        # Pattern 3: Function/method parameters
        elif node.type == "parameter_declaration":
            # Only extract params that are inside a parameter_list of a
            # function_declaration or method_declaration (not return types)
            parent = node.parent
            if parent is None or parent.type != "parameter_list":
                continue  # pragma: no cover - params always in parameter_list
            grandparent = parent.parent
            if grandparent is None or grandparent.type not in (
                "function_declaration", "method_declaration",
            ):
                continue
            # Must be the *parameters* list, not the result list
            params_node = find_child_by_field(grandparent, "parameters")
            if params_node is None or params_node.id != parent.id:
                continue

            name_node = None
            type_node = None
            for child in node.children:
                if child.type == "identifier" and name_node is None:
                    name_node = child
                elif child.type in (
                    "type_identifier", "pointer_type", "qualified_type",
                ):
                    type_node = child

            if name_node is None or type_node is None:
                continue

            var_name = node_text(name_node, source)

            # Scope to enclosing function
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue  # pragma: no cover - params always inside functions
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids duplicate param names

            type_name = _type_identifier_from_node(type_node, source)
            if type_name and type_name not in _GO_BUILTINS:
                func_vars[var_name] = type_name

        # Pattern 4: Method receiver  func (s *Server) Foo()
        # The receiver variable (e.g. ``s``) has the receiver type (e.g.
        # ``Server``), scoped to the method's qualified name (``Server.Foo``).
        # This enables self-method calls like ``s.Close()`` inside
        # ``Server.Cleanup()`` to resolve via typed_receiver_call.
        elif node.type == "method_declaration":
            receiver_node = find_child_by_field(node, "receiver")
            name_node = find_child_by_field(node, "name")
            if receiver_node is None or name_node is None:
                continue  # pragma: no cover - well-formed Go methods have both
            method_name = node_text(name_node, source)
            receiver_type = _extract_receiver_type_from_node(
                receiver_node, source,
            )
            if not receiver_type:
                continue  # pragma: no cover - well-formed Go receivers have types
            qualified_name = f"{receiver_type}.{method_name}"
            # Extract receiver parameter name from the parameter_declaration
            for child in receiver_node.children:
                if child.type == "parameter_declaration":
                    for param_child in child.children:
                        if param_child.type == "identifier":
                            var_name = node_text(param_child, source)
                            func_vars = scoped_var_types.setdefault(
                                qualified_name, {},
                            )
                            func_vars[var_name] = receiver_type
                            break
                    break

    return scoped_var_types


def _unwrap_rhs_expression(
    rhs_node: "tree_sitter.Node",
) -> "tree_sitter.Node | None":
    """Peel the ``expression_list`` wrapper off a short-var-declaration RHS.

    THE SINGLE ANSWER to "what expression is actually on the right", because two
    consumers need it and only one had it. ``short_var_declaration``'s last child
    is an ``expression_list``, never the expression itself — so ``x := e.Query()``
    presents as ``expression_list``, not ``call_expression``.

    :func:`_type_from_rhs` unwrapped this inline and worked. The WI-kuroj
    return-type-registry lookup in :func:`_extract_go_var_types_from_root` did NOT,
    and guarded on ``rhs.type == "call_expression"`` — a condition the node can
    never satisfy. That guard was therefore DEAD CODE from the day it shipped: the
    registry was built, threaded through three call layers, and consulted by a
    branch nothing could reach, so ``result := e.Query(); result.Rows()`` emitted
    ``go:external:0-0:Rows:unresolved`` (WI-jolif). Its three end-to-end tests
    never ran to say so, because a vacuous fixture skipped them.

    Returns ``None`` when the list holds no type-bearing expression, which is the
    same answer the inline version gave.
    """
    node = rhs_node
    if node.type == "expression_list":
        for child in node.children:
            if child.type in (
                "unary_expression", "composite_literal", "call_expression",
            ):
                return child
        return None
    return node


def _type_from_rhs(
    rhs_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract a type name from the right side of a short var declaration.

    Handles:
    - expression_list wrapping: ``expression_list -> unary_expression / composite_literal / call_expression``
    - ``&Server{}`` → unary_expression(&, composite_literal(type_identifier=Server))
    - ``Server{}`` → composite_literal(type_identifier=Server)
    - ``NewServer()`` → call_expression(identifier("NewServer")) → type "Server"
    - ``pkg.NewFoo()`` → call_expression(selector_expression("pkg.NewFoo")) → type "Foo"

    Constructor inference follows Go naming convention: ``NewXxx()`` returns
    ``*Xxx``.  Only simple ``New`` prefix is stripped; ``NewXxxYyy`` → ``XxxYyy``.

    Returns the type name string or None.
    """
    node = _unwrap_rhs_expression(rhs_node)
    if node is None:
        return None

    # &Server{} → unary_expression with & operator
    if node.type == "unary_expression":
        for child in node.children:
            if child.type == "composite_literal":
                node = child
                break
        else:
            return None  # pragma: no cover - defensive for unrecognized unary

    # Server{} / http.Client{} → composite_literal whose ``type`` field names the
    # type. This delegates to _type_identifier_from_node rather than scanning for a
    # ``type_identifier`` child, because tree-sitter-go spells a package-qualified
    # name differently by SYNTACTIC POSITION: an expression gives
    # ``selector_expression`` (exec.Command(...)), a TYPE gives ``qualified_type``
    # (&http.Client{}). Matching only ``type_identifier`` therefore bound in-repo
    # struct literals and silently dropped every external one, so ``client.Do(req)``
    # emitted the bare ``external`` module placeholder and the no-module gate
    # (io-boundary:F3) correctly refused it — ``Do`` is in go.yaml's ambiguous_names.
    # _type_identifier_from_node already answers this question for the ``var x T``
    # spelling (its docstring calls returning the full ``http.Client`` "critical for
    # IO boundary detection"); the copy here had drifted narrower, so the two
    # spellings of one declaration disagreed.
    if node.type == "composite_literal":
        type_node = find_child_by_field(node, "type")
        if type_node is not None and type_node.type in (
            "type_identifier", "qualified_type",
        ):
            return _type_identifier_from_node(type_node, source)
        # map[k]v{} / []T{} / struct{}{} — composite types name no receiver type.
        return None

    # NewFoo() or pkg.NewFoo() → constructor return type inference
    if node.type == "call_expression":
        return _type_from_constructor_call(node, source)

    return None  # pragma: no cover - defensive for unrecognized RHS


def _type_from_constructor_call(
    call_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Infer return type from Go constructor naming convention.

    Go convention: ``NewFoo()`` returns ``*Foo`` or ``Foo``.  Also handles
    package-qualified calls: ``pkg.NewFoo()`` → ``pkg.Foo``.

    THE QUALIFIER IS PART OF THE ANSWER, and this branch used to drop it. Its
    sibling — the ``composite_literal`` branch of :func:`_type_from_rhs` — was
    fixed to keep it because a bare ``Client`` resolves to no package and lands
    in the ``external`` module slot, where the catalogue cannot match it and the
    no-module gate (io-boundary:F3) correctly refuses it.
    :func:`_type_identifier_from_node` states the contract outright: the full
    ``http.Client`` is "critical for IO boundary detection". One branch honoured
    it and its neighbour did not, so two spellings of one declaration disagreed:

        var reader *bufio.Reader        -> "bufio.Reader"   (reaches the catalogue)
        reader := bufio.NewReader(...)  -> "Reader"         (reached nothing)

    Measured on WI-vutav's fixtures: Go's whole catalogued stdin surface is three
    rows and the calls that actually transfer bytes could not be catalogued at
    all, because the dominant idiom emitted ``go:external:0-0:ReadString``.

    ``a.b.NewFoo()`` ABSTAINS rather than inventing ``a.b.Foo``. Go has no
    three-segment package selector in expression position, so the operand of that
    selector is a value and the call is a method, not a package constructor —
    there is no alias for ``a.b`` in ``import_aliases`` and a qualified name that
    cannot be resolved is worse than none.

    Only matches when the suffix after ``New`` starts with an uppercase letter
    (Go exported type convention).
    """
    func_node = find_child_by_field(call_node, "function")
    if func_node is None:
        return None  # pragma: no cover

    # NewFoo() → identifier "NewFoo"
    if func_node.type == "identifier":
        name = node_text(func_node, source)
        if name.startswith("New") and len(name) > 3 and name[3].isupper():
            return name[3:]

    # pkg.NewFoo() → selector_expression with field_identifier "NewFoo"
    elif func_node.type == "selector_expression":
        field = find_child_by_field(func_node, "field")
        operand = find_child_by_field(func_node, "operand")
        if field is not None:
            name = node_text(field, source)
            if name.startswith("New") and len(name) > 3 and name[3].isupper():
                # Only a BARE identifier operand can be a package name. Anything
                # else (a nested selector, an index, a call) is a receiver, so
                # this is a method call and not a package constructor.
                if operand is not None and operand.type == "identifier":
                    return f"{node_text(operand, source)}.{name[3:]}"
                return None

    return None


def _type_identifier_from_node(
    type_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the type name from a type node, stripping pointer indirection.

    Handles:
    - ``type_identifier`` → direct type name (e.g. ``Client``)
    - ``qualified_type`` → package-qualified name (e.g. ``http.Client``)
    - ``pointer_type`` → unwrap ``*`` then extract from child
      (supports ``*Client``, ``*http.Client``)

    Returning the full qualified name (``http.Client``) is critical for
    IO boundary detection: the package prefix can be mapped through
    import_aliases to recover the full import path (e.g. ``net/http``),
    which the IO boundary catalog needs to classify method calls like
    ``client.Do()`` as ``net_send``.
    """
    if type_node.type == "type_identifier":
        return node_text(type_node, source)
    elif type_node.type == "qualified_type":
        return node_text(type_node, source)
    elif type_node.type == "pointer_type":
        for child in type_node.children:
            if child.type == "type_identifier":
                return node_text(child, source)
            elif child.type == "qualified_type":
                return node_text(child, source)
    return None  # pragma: no cover - pointer to non-named type (e.g. *func(), *chan)


def _bare_go_type(type_name: str) -> str:
    """``net.Conn`` -> ``Conn``. The fold, in one place.

    The return-type registry is keyed ``Receiver.Method`` with an UNQUALIFIED
    receiver, because that is how in-repo symbols are stored. Its VALUES are
    package-qualified (WI-doluf), because the io-boundary module slot needs the
    package. So every site that turns a *value* back into a *key*, or into a
    symbol lookup, folds here -- open-coding ``rsplit`` at each one is how the
    two forms drift apart.
    """
    return type_name.rsplit(".", 1)[-1] if "." in type_name else type_name


def _go_return_type_from_signature(signature: str | None) -> str | None:
    """Extract the primary return type from a Go function signature.

    WI-kuroj / INV-dihos return-type registry: enables chained receiver
    resolution by letting ``x := obj.Method()`` infer ``x``'s type from
    the registry when ``obj``'s type is known.

    Handles three forms:
    - ``"(params) ReturnType"``  → ``"ReturnType"``
    - ``"(params) (Type1, error)"``  → ``"Type1"`` (pick non-error)
    - ``"(params)"``  → ``None`` (no return type / void)

    Go tuple returns with multiple non-error types (ambiguous) return
    None.  Pointer-star prefixes are stripped.

    PACKAGE-QUALIFIED TYPES KEEP THEIR PACKAGE (``net.Conn`` stays
    ``net.Conn``), and that is WI-doluf. They used to be stripped to the bare
    name "because symbol names in the symbol registry are unqualified" -- true
    of the SYMBOL LOOKUP and false of the io-boundary MODULE SLOT, which is the
    one consumer that needs the package and was therefore served the
    ``external`` placeholder forever. Measured consequence: a receiver typed
    from a factory's return value could never match a method row, so on an
    idiomatic accept loop go's entire reported network-input surface was setup
    calls that receive nothing.

    The bare form is now DERIVED where it is needed (:func:`_bare_go_type` at
    the registry-key and symbol-lookup sites), rather than stored in the only
    form one of two consumers wanted. Callers that build a registry key from a
    type MUST fold it first -- the keys are ``Receiver.Method`` with a bare
    receiver.
    """
    if not signature:
        return None
    # Find the end of the parameter list: first ')' at depth 0.
    paren_depth = 0
    params_end = -1
    for i, c in enumerate(signature):
        if c == "(":
            paren_depth += 1
        elif c == ")":
            paren_depth -= 1
            if paren_depth == 0:
                params_end = i
                break
    if params_end < 0 or params_end >= len(signature) - 1:
        return None
    ret_part = signature[params_end + 1 :].strip()
    if not ret_part:
        return None
    # Tuple return: "(Type1, error)" → pick the non-error type.
    if ret_part.startswith("(") and ret_part.endswith(")"):
        inner = ret_part[1:-1]
        types = [t.strip().lstrip("*") for t in inner.split(",")]
        non_error = [
            t for t in types if t not in _GO_BUILTIN_TYPES and t != "any"
        ]
        if len(non_error) == 1:
            return non_error[0]
        return None  # ambiguous: 0 or 2+ non-builtin return types
    # Single return type: strip pointer and package prefix.
    bare = ret_part.lstrip("*")
    if bare in _GO_BUILTIN_TYPES or bare == "any":
        return None
    return bare



#: Go wrapper constructors whose BOUNDARY is a property of their argument.
#:
#: ``go.yaml`` files ``bufio.{NewScanner,NewReader}`` as ``ipc_recv`` on the
#: note "When wrapping os.Stdin" -- a condition no catalogue row can enforce,
#: because the row sees the callee and the answer is in the ARGUMENT. Measured
#: on the ADR-0049 cohort's Go repositories, of the 83 bare-local sites whose
#: origin the shipped reaching-def solver resolves, 63 wrap an ``os.Open``
#: handle, 3 an HTTP body, 1 a buffer, and ZERO wrap ``os.Stdin`` (WI-lipis).
_GO_HANDLE_WRAPPERS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("bufio", "NewScanner"), ("bufio", "NewReader"),
})

#: Argument prefixes that prove the wrapped handle never left this process.
#: Only the PROVABLE case is listed: anything unrecognised stamps nothing and
#: classifies exactly as before, because a gate whose default silenced
#: findings would be a false-negative generator on a security analysis.
_GO_IN_MEMORY_READERS: Final[tuple[str, ...]] = (
    "strings.NewReader(", "bytes.NewReader(", "bytes.NewBuffer(",
    "bytes.NewBufferString(",
)


#: Calls that PRODUCE a filesystem handle. A read through one of these is an
#: ``fs_read`` crossing, which hypergumbo deliberately does not mint a taint
#: source from -- the sensitivity of a file read depends on what is stored, so
#: ``fs_read`` is absent from ``AUTO_SOURCE_LABEL_MAP`` by design.
#:
#: 63 of the 83 resolved bare-local ``bufio.New*`` sites in the ADR-0049 cohort
#: wrap one of these (WI-lipis), which makes it the LARGEST false-source
#: population the wrapper row creates -- larger than the in-memory case the
#: first deliverable addressed, and omitted from that estimate.
_GO_FILE_HANDLE_PRODUCERS: Final[tuple[str, ...]] = (
    "os.Open(", "os.OpenFile(", "os.Create(", "os.CreateTemp(",
    "ioutil.TempFile(", "ioutil.OpenFile(",
)

#: Node types that BIND a name in a Go function body. ``var_spec`` covers
#: ``var f = os.Open(p)``; the other two cover ``:=`` and ``=``.
_GO_BINDING_NODES: Final[frozenset[str]] = frozenset({
    "short_var_declaration", "assignment_statement", "var_spec",
})


def _go_enclosing_body(node: "tree_sitter.Node") -> "Optional[tree_sitter.Node]":
    """The body of the function or literal this node sits inside, or None."""
    cur = node.parent
    while cur is not None:
        if cur.type in (
            "function_declaration", "method_declaration", "func_literal",
        ):
            return find_child_by_field(cur, "body")
        cur = cur.parent
    return None  # pragma: no cover - a call at file scope does not parse in Go


def _go_binding_rhs(
    node: "tree_sitter.Node", source: bytes, name: str,
) -> Optional[str]:
    """Text of the LAST binding of ``name`` at or above ``node``'s line.

    A deliberately smaller instrument than a reaching-def solver, and its
    answers are a SUBSET of one: the enclosing function only, textual line
    order only, no branch or loop reasoning. The analyzer runs before any DDG
    exists, so the alternative is not "use the DDG here" but "answer nothing",
    and 74.1% of the resolvable population is a single ``:=`` five lines up.

    ORDER IS THE WHOLE POINT rather than a detail. A scan that took the last
    match in the FILE would read a rebinding below the call as if it reached
    it, and one that took the first would miss a rebinding above it. Both
    shapes are pinned by tests.
    """
    body = _go_enclosing_body(node)
    if body is None:  # pragma: no cover - see _go_enclosing_body
        return None
    use_line = node.start_point[0]
    best_line = -1
    best_text: Optional[str] = None
    stack = [body]
    while stack:
        cur = stack.pop()
        stack.extend(cur.children)
        if cur.type not in _GO_BINDING_NODES:
            continue
        if cur.start_point[0] > use_line or cur.start_point[0] < best_line:
            continue
        left = find_child_by_field(cur, "left") or find_child_by_field(
            cur, "name",
        )
        right = find_child_by_field(cur, "right") or find_child_by_field(
            cur, "value",
        )
        if left is None or right is None:
            continue
        targets = [node_text(c, source) for c in left.named_children] or [
            node_text(left, source),
        ]
        if name not in targets:
            continue
        # ``f, err := os.Open(p)`` binds TWO names from ONE call, so the right
        # side has fewer elements than the left and the whole of it is what
        # produced ``f``. ``a, b := g(), h()`` has as many, and there the
        # position decides -- taking the whole text there would report ``h()``
        # as ``a``'s origin.
        sources = right.named_children or [right]
        if len(sources) == len(targets):
            chosen = sources[targets.index(name)]
        else:
            chosen = right
        best_line = cur.start_point[0]
        best_text = node_text(chosen, source).strip()
    return best_text


def _go_classify_handle_text(text: str) -> Optional[str]:
    """``io_target_kind`` for an expression that produces a handle, or None.

    One place, so the inline argument and the resolved binding cannot drift
    into disagreeing about what ``strings.NewReader(s)`` is.
    """
    if any(r in text for r in _GO_IN_MEMORY_READERS):
        return "in_memory"
    if any(o in text for o in _GO_FILE_HANDLE_PRODUCERS):
        return "host_path"
    if "os.Stdin" in text:
        return "std_stream"
    return None


def _go_wrapped_handle_kind(
    node: "tree_sitter.Node", source: bytes, module: str, callee: str,
) -> Optional[str]:
    """``io_target_kind`` for a handle-wrapper call site, or None.

    WI-lipis. Same shape as bash's redirect target (INV-nular): ONE catalogue
    row whose boundary depends on a per-call-site fact the row cannot see, so
    the analyzer stamps the discriminator and the consumers read it.

    TWO STEPS, and the second is this item's second deliverable. The argument
    is classified DIRECTLY when it names the producer inline; when it is a bare
    identifier -- 64.8% of the measured population -- the enclosing function is
    read for that name's last binding and the binding is classified instead.

    Returns None for everything still not provable: a parameter, a struct
    field, a value from a function this file does not bind. INV-zumin's ruling
    is that a call site gets ONE answer or NONE, so an unresolved origin stamps
    nothing and classifies exactly as it did before this existed.
    """
    if (module, callee) not in _GO_HANDLE_WRAPPERS:
        return None
    args = find_child_by_field(node, "arguments")
    if args is None:  # pragma: no cover - defensive
        # A tree-sitter-go ``call_expression`` always carries an ``arguments``
        # field, so this cannot fire on a well-formed parse. Kept because the
        # line below would raise on None, and a crash inside an analyzer takes
        # the whole repo's analysis with it.
        return None
    arg_text = node_text(args, source).strip()
    direct = _go_classify_handle_text(arg_text)
    if direct is not None:
        return direct
    bare = arg_text.strip("()").strip()
    if not bare.isidentifier():
        return None
    rhs = _go_binding_rhs(node, source, bare)
    return None if rhs is None else _go_classify_handle_text(rhs)


def _extract_function_reference_edges(
    args_node: "tree_sitter.Node",
    source: bytes,
    current_function: Symbol,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, list[Symbol]],
    resolver: ListNameResolver,
    line: int,
    edges: list[Edge],
    run: AnalysisRun,
    scoped_vars: dict[str, str] | None = None,
) -> None:
    """Detect function identifiers passed as arguments and create call edges.

    In Go, functions are first-class values.  When a known function is passed
    as an argument to another function — e.g. ``r.Get("/path", ViewIssue)`` —
    it will typically be called by the receiving function.  This creates a
    ``calls`` edge with ``evidence_type="function_reference_arg"`` and lower
    confidence (0.70) to enable reverse-slice navigation from route handlers
    and callback targets.

    Handles two forms:
    - ``identifier``: simple function reference like ``handler``
    - ``selector_expression``: qualified reference like ``h.GetAPI``

    The ``scoped_vars`` parameter (from ``_extract_go_var_types``) maps
    local variable names to their types within the enclosing function.
    Identifiers found in ``scoped_vars`` are skipped to avoid false
    positives where a local variable shares a name with a function
    (e.g., ``start := time.Now()`` vs ``func start()``).
    """
    _scoped = scoped_vars or {}
    for arg in args_node.children:
        if arg.type == "identifier":
            ref_name = node_text(arg, source)
            # Skip identifiers that are local variables in the current scope
            if ref_name in _scoped:
                continue
            # Check if it's a known function/method (local or global)
            if ref_name in local_symbols:
                sym = local_symbols[ref_name]
                if sym.kind in ("function", "method") and sym.id != current_function.id:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=sym.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
            else:
                lookup_result = resolver.lookup(ref_name)
                if lookup_result.found and lookup_result.symbol.kind in ("function", "method"):
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=lookup_result.symbol.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        confidence=0.70 * lookup_result.confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
        elif arg.type == "selector_expression":
            # h.GetAPI or pkg.Handler
            field_node = find_child_by_field(arg, "field")
            if field_node:
                ref_name = node_text(field_node, source)
                # Try full selector text first (e.g., "handlers.GetAPI")
                full_ref = node_text(arg, source)
                if full_ref in local_symbols:  # pragma: no cover - selector text rarely matches symbol name
                    sym = local_symbols[full_ref]
                    if sym.kind in ("function", "method") and sym.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=sym.id,
                            edge_type="calls",
                            line=line,
                            evidence_type="function_reference_arg",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                        continue
                # Try short name via resolver
                lookup_result = resolver.lookup(ref_name)
                if lookup_result.found and lookup_result.symbol.kind in ("function", "method"):
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=lookup_result.symbol.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        confidence=0.70 * lookup_result.confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))


def _registry_type_key(
    type_name: str,
    field_type_registry: dict[str, dict[str, str]],
    import_aliases: dict[str, str],
    module_path: Optional[str],
) -> Optional[str]:
    """The key ``field_type_registry`` holds ``type_name`` under, or ``None``.

    ``field_type_registry`` is built from ANALYSED declarations, so its keys are
    BARE type names — ``Tester``, never ``caddytest.Tester``. Once receiver typing
    started preserving the package qualifier (so that external types reach the I/O
    catalogue at all), every qualified IN-REPO type stopped matching its own entry.
    Measured on caddy: ``tester := caddytest.NewTester(t)`` then
    ``tester.Client.Get(proxyURL)`` lost its ``net_send`` tag at two sites, because
    the chain's first lookup asked for ``caddytest.Tester``.

    THE BARE FALLBACK IS GATED, and the gate is the whole point. Stripping a
    package prefix off an EXTERNAL type is the collision
    :func:`_external_package_for_type` exists to prevent: a bare ``Values`` from
    ``url.Values`` once "absorbed 13 spurious in-edges into one struct, poisoning
    the centrality ranking". So the bare key is tried only when the qualifier is
    NOT a definitively out-of-module package — which is exactly the set of cases
    where the pre-qualifier code looked the bare name up anyway, and leaves the
    one case where doing so was the known bug.
    """
    if type_name in field_type_registry:
        return type_name
    if "." not in type_name:
        return None
    if _external_package_for_type(type_name, import_aliases, module_path) is not None:
        return None
    bare = type_name.split(".")[-1]
    return bare if bare in field_type_registry else None


def _resolve_field_chain(
    operand_node: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
    field_type_registry: dict[str, dict[str, str]],
    import_aliases: Optional[dict[str, str]] = None,
    module_path: Optional[str] = None,
) -> str | None:
    """Resolve a chained selector expression to a field type.

    Given the operand of a call like ``r.integration.Notify()``, walks
    the selector chain to resolve the receiver type:

    1. Decompose ``r.integration`` into segments ``["r", "integration"]``
    2. Look up root ``r`` in ``var_types`` → ``RetryStage``
    3. Walk ``field_type_registry["RetryStage"]["integration"]`` → ``Integration``

    Returns the resolved type name (e.g., ``"Integration"``) or ``None``
    if any step fails.
    """
    # Decompose nested selector_expression into segments from leaf to root.
    # r.integration → ["r", "integration"]
    # r.inner.field → ["r", "inner", "field"]
    segments: list[str] = []
    current = operand_node
    while current is not None and current.type == "selector_expression":
        field = find_child_by_field(current, "field")
        if field is None:  # pragma: no cover — Go AST always has field
            return None
        segments.append(node_text(field, source))
        current = find_child_by_field(current, "operand")

    if current is None or current.type != "identifier":
        return None

    root_name = node_text(current, source)
    segments.reverse()  # Now ["r", "integration"] for r.integration

    # Resolve root variable to its type
    current_type = var_types.get(root_name)
    if current_type is None:
        return None

    # Walk through field chain using the registry
    for field_name in segments:
        key = _registry_type_key(
            current_type, field_type_registry, import_aliases or {}, module_path,
        )
        if key is None:
            return None
        current_type = field_type_registry[key].get(field_name)
        if current_type is None:
            return None

    return current_type


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, list[Symbol]],
    run: AnalysisRun,
    import_aliases: dict[str, str] | None = None,
    resolver: ListNameResolver | None = None,
    module_path: str | None = None,
    field_type_registry: dict[str, dict[str, str]] | None = None,
    interface_method_sets: dict[str, set[tuple[str, int, int]]] | None = None,
    method_return_type_registry: dict[str, str] | None = None,
    dot_imports: list[str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    Uses import_aliases to disambiguate when multiple files define the same symbol.
    Tracks variable types from assignments and parameters to disambiguate
    method calls when multiple types define the same method name.

    When ``field_type_registry`` is provided (aggregated from Pass 1), chained
    field access calls like ``r.integration.Notify()`` are resolved through the
    registry: r's type → field "integration" → type "Integration" → symbol
    "Integration.Notify".

    When ``method_return_type_registry`` is provided (aggregated from Pass 1),
    chained method calls like ``x := e.Query(); x.Rows()`` are resolved: the
    return type of ``Engine.Query`` is looked up and assigned to ``x`` in
    var_types, enabling ``x.Rows()`` to resolve to ``Result.Rows``.

    When ``module_path`` is provided (from go.mod), import path hints are
    transformed by stripping the module prefix so that suffix matching in
    the resolver operates on repo-relative paths (e.g., ``pkg/log``)
    instead of full module paths (e.g., ``github.com/example/trivy/pkg/log``).
    """
    if import_aliases is None:
        import_aliases = {}
    if resolver is None:
        resolver = ListNameResolver(global_symbols, ambiguity_threshold=3)
    if field_type_registry is None:
        field_type_registry = {}
    if interface_method_sets is None:
        interface_method_sets = {}
    if method_return_type_registry is None:
        method_return_type_registry = {}

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = make_file_id("go", str(file_path))

    # Extract function-scoped variable-to-type bindings for receiver disambiguation
    scoped_var_types = _extract_go_var_types(
        tree.root_node, source,
        method_return_type_registry=method_return_type_registry,
    )

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import_declaration":
            # Handle both single imports and import blocks
            for child in node.children:
                if child.type == "import_spec":
                    path_node = find_child_by_field(child, "path")
                    if path_node:
                        import_path = node_text(path_node, source).strip('"')
                        edges.append(Edge.create(
                            src=file_id,
                            dst=f"go:{import_path}:0-0:package:package",
                            edge_type="imports",
                            line=child.start_point[0] + 1,
                            evidence_type="import_declaration",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            path_node = find_child_by_field(spec, "path")
                            if path_node:
                                import_path = node_text(path_node, source).strip('"')
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"go:{import_path}:0-0:package:package",
                                    edge_type="imports",
                                    line=spec.start_point[0] + 1,
                                    evidence_type="import_declaration",
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                # Get var_types scoped to the current enclosing function
                var_types = scoped_var_types.get(current_function.name, {})

                func_node = find_child_by_field(node, "function")
                if func_node:
                    callee_name = None
                    import_path_hint = None
                    # Set only when the hint was derived from the RECEIVER's own
                    # tracked type (``q`` is a ``url.Values``), as opposed to from a
                    # package alias in operand position (``url.Parse(...)``). The
                    # WI-jopar guard below needs to tell those apart: a typed local
                    # receiver must terminate resolution either way, and the module
                    # slot it terminates with should name the real package instead of
                    # the ``external`` placeholder when we know it.
                    receiver_module_hint: Optional[str] = None
                    full_import_path = None

                    if func_node.type == "identifier":
                        # Simple call: helper()
                        raw_name = node_text(func_node, source)
                        # Skip Go builtin type conversions (string(x),
                        # int(x)) and builtin functions (len, cap, make)
                        if raw_name not in _GO_BUILTIN_TYPES and raw_name not in _GO_BUILTIN_FUNCS:
                            callee_name = raw_name
                    elif func_node.type == "selector_expression":
                        # Method call: obj.Method() or pkg.Func()
                        operand_node = find_child_by_field(func_node, "operand")
                        field_node = find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        # Check if operand is a package alias
                        if operand_node and operand_node.type == "identifier":
                            alias = node_text(operand_node, source)
                            if alias in import_aliases:
                                full_import_path = import_aliases[alias]
                                if module_path:
                                    import_path_hint = _strip_module_prefix(
                                        full_import_path, module_path,
                                    )
                                else:
                                    import_path_hint = full_import_path
                            # Check if operand has an inferred type for
                            # receiver-type method disambiguation
                            elif alias in var_types:
                                receiver_type = var_types[alias]
                                # A type from OUTSIDE this module cannot be defined
                                # by this repo, so the unqualified lookup below would
                                # only ever find an impostor. Take the import-path
                                # hint directly. (_strip_module_prefix is a no-op on
                                # an out-of-module path by construction.)
                                _external = _external_package_for_type(
                                    receiver_type, import_aliases, module_path,
                                )
                                if _external is not None:
                                    full_import_path = _external
                                    import_path_hint = _external
                                    receiver_module_hint = _external
                                else:
                                    # Strip package prefix from qualified types
                                    # (e.g. "notify.Stage" → "Stage") since symbol
                                    # names in global_symbols are unqualified.
                                    bare_recv = (
                                        receiver_type.rsplit(".", 1)[-1]
                                        if "." in receiver_type
                                        else receiver_type
                                    )
                                    qualified_name = f"{bare_recv}.{callee_name}"
                                    # Try qualified name in local or global symbols
                                    if qualified_name in local_symbols:
                                        callee = local_symbols[qualified_name]
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=callee.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="ast_call",
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            meta={"call_construct": "method", "resolution_quality": "typed_receiver"},
                                        ))
                                        callee_name = None  # Already resolved
                                    elif qualified_name in global_symbols:
                                        # Direct lookup by qualified name
                                        candidates = global_symbols[qualified_name]
                                        if candidates:
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=candidates[0].id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="ast_call",
                                                origin=PASS_ID,
                                                origin_run_id=run.execution_id,
                                                meta={"call_construct": "method", "resolution_quality": "typed_receiver"},
                                            ))
                                            callee_name = None  # Already resolved
                                    # Fallback: a qualified type whose package we
                                    # could not classify as out-of-module (no go.mod
                                    # module path). Recover the import path so the
                                    # unresolved edge still gets a module hint
                                    # instead of "external".
                                    elif "." in receiver_type:
                                        pkg_prefix = receiver_type.split(".")[0]
                                        if pkg_prefix in import_aliases:
                                            full_import_path = import_aliases[
                                                pkg_prefix
                                            ]
                                            if module_path:
                                                import_path_hint = (
                                                    _strip_module_prefix(
                                                        full_import_path,
                                                        module_path,
                                                    )
                                                )
                                            else:
                                                import_path_hint = full_import_path
                                            receiver_module_hint = import_path_hint
                        # Chained field access: r.integration.Notify()
                        # Walk selector chain through field_type_registry
                        # to resolve the receiver type.
                        elif (
                            operand_node
                            and operand_node.type == "selector_expression"
                            and callee_name
                            and current_function
                            and field_type_registry
                        ):
                            resolved_type = _resolve_field_chain(
                                operand_node, source, var_types,
                                field_type_registry,
                                import_aliases=import_aliases,
                                module_path=module_path,
                            )
                            if resolved_type:
                                # For cross-package qualified types
                                # (e.g. "notify.Stage"), symbol names in
                                # global_symbols are unqualified ("Stage.Exec",
                                # not "notify.Stage.Exec").  Strip the package
                                # prefix before constructing the method name.
                                bare_type = (
                                    resolved_type.rsplit(".", 1)[-1]
                                    if "." in resolved_type
                                    else resolved_type
                                )
                                qualified_name = f"{bare_type}.{callee_name}"
                                target = local_symbols.get(qualified_name)
                                if target is None and qualified_name in global_symbols:
                                    candidates = global_symbols[qualified_name]
                                    if candidates:
                                        target = candidates[0]
                                if target is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=target.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        meta={"call_construct": "method", "receiver": "typed_field"},
                                    ))
                                    callee_name = None  # Already resolved
                                # Fallback: if resolved_type is a qualified
                                # type like "http.Client" (from a struct field
                                # with package-qualified type), extract the
                                # package prefix and recover the import path.
                                elif "." in resolved_type:
                                    pkg_prefix = resolved_type.split(".")[0]
                                    if pkg_prefix in import_aliases:
                                        full_import_path = import_aliases[
                                            pkg_prefix
                                        ]
                                        if module_path:
                                            import_path_hint = (
                                                _strip_module_prefix(
                                                    full_import_path,
                                                    module_path,
                                                )
                                            )
                                        else:
                                            import_path_hint = full_import_path
                        # Chained call: pkg.Func(args).Method()
                        # e.g. json.NewEncoder(w).Encode(data) — propagate
                        # the import path from the inner call's package prefix
                        # so .Encode() doesn't falsely resolve to a local
                        # method with the same name.
                        elif operand_node and operand_node.type == "call_expression":
                            inner_func = find_child_by_field(
                                operand_node, "function",
                            )
                            if inner_func and inner_func.type == "selector_expression":
                                inner_operand = find_child_by_field(
                                    inner_func, "operand",
                                )
                                if (
                                    inner_operand
                                    and inner_operand.type == "identifier"
                                    and node_text(inner_operand, source)
                                    in import_aliases
                                ):
                                    import_path_hint = import_aliases[
                                        node_text(inner_operand, source)
                                    ]
                        # WI-vadin: Before the chained-call ambiguity guard,
                        # try to resolve via the return-type registry.
                        # For e.NewQuery().Exec(), if e's type is Engine
                        # and the registry maps Engine.NewQuery → Query,
                        # resolve .Exec() as Query.Exec.
                        if (
                            callee_name
                            and import_path_hint is None
                            and operand_node is not None
                            and operand_node.type == "call_expression"
                            and method_return_type_registry
                        ):
                            inner_func = find_child_by_field(
                                operand_node, "function",
                            )
                            if (
                                inner_func is not None
                                and inner_func.type == "selector_expression"
                            ):
                                inner_operand = find_child_by_field(
                                    inner_func, "operand",
                                )
                                inner_field = find_child_by_field(
                                    inner_func, "field",
                                )
                                if inner_operand is not None and inner_field is not None:
                                    recv_name = node_text(inner_operand, source)
                                    inner_method = node_text(inner_field, source)
                                    recv_type = var_types.get(recv_name, "")
                                    if recv_type:
                                        # Both folds: key from a value, then a
                                        # symbol lookup from a value (WI-doluf).
                                        qualified = (
                                            f"{_bare_go_type(recv_type)}"
                                            f".{inner_method}"
                                        )
                                        ret_type = method_return_type_registry.get(
                                            qualified
                                        )
                                        if ret_type:
                                            outer_qualified = (
                                                f"{_bare_go_type(ret_type)}"
                                                f".{callee_name}"
                                            )
                                            target = local_symbols.get(outer_qualified)
                                            if target is None and outer_qualified in global_symbols:
                                                candidates = global_symbols[outer_qualified]
                                                if candidates:
                                                    target = candidates[0]
                                            if target is not None:
                                                edges.append(Edge.create(
                                                    src=current_function.id,
                                                    dst=target.id,
                                                    edge_type="calls",
                                                    line=node.start_point[0] + 1,
                                                    evidence_type="ast_call",
                                                    origin=PASS_ID,
                                                    origin_run_id=run.execution_id,
                                                    # INV-tadup: the CONSTRUCT is a method call;
                                                    # "chained_return_type" names HOW the receiver
                                                    # type was resolved, which is the
                                                    # ``resolution_quality`` axis.
                                                    meta={
                                                        "call_construct": "method",
                                                        "resolution_quality": "chained_return_type",
                                                    },
                                                ))
                                                callee_name = None

                        # Chained-call ambiguity guard: when the receiver
                        # is a call_expression but we couldn't determine its
                        # package (import_path_hint still None), the receiver
                        # type is a return value of unknown type.  Resolving
                        # to a single global candidate is almost certainly
                        # wrong (e.g. c.CoreV1().Endpoints(ns).Update() →
                        # Manager.Update is false positive).  Emit as
                        # unresolved instead.
                        if (
                            callee_name
                            and import_path_hint is None
                            and operand_node is not None
                            and operand_node.type == "call_expression"
                        ):
                            dst_id = (
                                f"go:external:0-0:{callee_name}:unresolved"
                            )
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                is_resolved=False,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"call_construct": "method", "receiver": "field_chain"},
                            ))
                            callee_name = None  # Already handled

                        # WI-jopar: receiver-type guard. When the operand is a
                        # simple identifier that the analyzer is tracking as a
                        # local variable (regardless of whether it inferred a
                        # concrete type), the typed lookup at lines 2079-2108
                        # already tried qualified resolution and failed.
                        # Falling through to the short-name dispatch guard
                        # would emit a wrong edge to a local Interface.Set or
                        # Alerts.Set just because the method names collide
                        # (e.g. ``q.Set("k", "v")`` where ``q`` is a
                        # ``url.Values``). On alertmanager this single bug
                        # absorbed 13 spurious in-edges into one struct,
                        # poisoning the centrality ranking. Emit an unresolved
                        # external edge instead — cross-language linkers can
                        # still match it later, but no false intra-repo edge
                        # is created.
                        #
                        # Note: var_types may map ``alias`` to an empty string
                        # when the analyzer recognised the variable but not
                        # its type. The empty-string entry is still meaningful
                        # — it tells us the operand is a tracked local, not a
                        # free-floating identifier — so the guard fires on key
                        # presence, not value truthiness.
                        #
                        # WHY THE CONDITION IS NOT ``import_path_hint is None``.
                        # It used to be, and that worked only while a qualified
                        # composite literal (``url.Values{}``) left the receiver
                        # UNTYPED: no type meant no hint, so hint-absence was an
                        # accidental proxy for "the receiver is a tracked local".
                        # Once such a receiver types correctly the hint is set,
                        # the proxy inverts, and this guard stops firing exactly
                        # where it is most needed — the alertmanager
                        # ``AlertStore.Set`` false edge came straight back. The
                        # guard's real subject is the RECEIVER, so it now keys on
                        # the receiver directly and terminates with the package it
                        # knows instead of the ``external`` placeholder.
                        if (
                            callee_name
                            and (
                                import_path_hint is None
                                or receiver_module_hint is not None
                            )
                            and operand_node is not None
                            and operand_node.type == "identifier"
                        ):
                            _alias = node_text(operand_node, source)
                            if _alias in var_types:
                                _slot = receiver_module_hint or "external"
                                dst_id = (
                                    f"go:{_slot}:0-0:{callee_name}:unresolved"
                                )
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    # Stated, not inherited. Edge.create defaults
                                    # is_resolved=True, so this guard was minting
                                    # a "resolved" edge onto a dst whose kind slot
                                    # reads ``unresolved`` — the same incoherence
                                    # INV-fazim is filed for, latent here only
                                    # because the guard seldom fired. It is an
                                    # out-of-repo target by construction.
                                    is_resolved=False,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"call_construct": "method", "receiver": "external"},
                                ))
                                callee_name = None  # Already handled

                        # Unified ambiguity guard for ALL selector expressions:
                        # covers simple identifiers (x.Close()), chained calls
                        # (getWriter().Close()), field access (resp.Body.Close()),
                        # and index expressions (items[0].Close()).
                        # When receiver type is unknown and 2+ types define the
                        # method, produce an unresolved edge instead of picking
                        # an arbitrary candidate.  Threshold was originally 3 but
                        # lowered to 2 because 2-candidate collisions (e.g.
                        # Close(), Error(), String()) cause widespread false
                        # positives that inflate centrality rankings.
                        #
                        # Exception: if one of the ambiguous candidates is an
                        # interface method, prefer it.  The type hierarchy linker
                        # creates dispatches_to edges from interface methods to
                        # concrete implementations, so resolving to the interface
                        # method enables slice traversal through the dispatch.
                        if (
                            callee_name
                            and import_path_hint is None
                            and callee_name in global_symbols
                            and len(global_symbols[callee_name]) >= 2
                        ):
                            # Collect all interface-method candidates. When
                            # the short-name resolves to >=2 global symbols
                            # and >=2 of them are interface methods, the
                            # interface-method preference does not uniquely
                            # disambiguate — pick deterministically by id and
                            # flag the edge as INV-zuhub fallback (confidence
                            # <=0.5 + meta["disambiguation_fallback"]=True).
                            # When exactly one is an interface method, the
                            # interface-method test IS the precision
                            # disambiguator — keep confidence=0.75.
                            _iface_candidates = [
                                _cand for _cand in global_symbols[callee_name]
                                if "." in _cand.name
                                and _cand.name.rsplit(".", 1)[0] in interface_method_sets
                            ]
                            if _iface_candidates:
                                _is_iface_fallback = len(_iface_candidates) > 1
                                _iface_candidate = (
                                    min(_iface_candidates, key=lambda s: s.id)
                                    if _is_iface_fallback
                                    else _iface_candidates[0]
                                )
                                # Resolve to the interface method instead of
                                # unresolved — dispatches_to edges will route
                                # the slice to concrete implementations.
                                _iface_confidence = 0.5 if _is_iface_fallback else 0.75
                                _iface_meta = (
                                    # INV-tadup: the construct is a method call. The
                                    # interface-dispatch PATHWAY is already carried by
                                    # ``evidence_type="interface_dispatch"`` on this same
                                    # edge, so naming it again here duplicated one value
                                    # across two axes.
                                    {"call_construct": "method", "disambiguation_fallback": True}
                                    if _is_iface_fallback
                                    else {"call_construct": "method"}
                                )
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=_iface_candidate.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="interface_dispatch",
                                    confidence=_iface_confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta=_iface_meta,
                                ))
                                callee_name = None  # Already handled
                            else:
                                dst_id = f"go:external:0-0:{callee_name}:unresolved"
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    # INV-fazim: an ``external`` path slot carries no
                                    # module evidence and the kind slot reads
                                    # ``unresolved``, so the edge is NOT resolved.
                                    # Edge.create defaults the flag to True, and
                                    # under ADR-0037 ruling 4 that flag is
                                    # authoritative — a consumer may not re-derive
                                    # the verdict from the dst string. Left silent,
                                    # this minted a "resolved" edge that taint's
                                    # propagation lookup takes into the ungated
                                    # ``if not path_module: return hits[0]``
                                    # bare-name fallback, never reaching the F3 gate.
                                    is_resolved=False,
                                    # WI-nurun: confidence stays EXPLICIT and stays
                                    # 0.50. It is not the derived unresolved base
                                    # (0.40) because this call did resolve — to 2+
                                    # candidates — which is strictly more evidence
                                    # than "no idea", and the ambiguity is carried in
                                    # meta. An explicit value also means the
                                    # is_resolved correction above cannot move it.
                                    confidence=0.50,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"call_construct": "method", "resolution_quality": "ambiguous"},
                                ))
                                callee_name = None  # Already handled

                        # Go visibility guard: lowercase methods are unexported
                        # (package-private).  When receiver type is unknown and
                        # the callee starts with a lowercase letter, global
                        # resolution would cross package boundaries — producing
                        # false positives (e.g. recalcRequest.string with 577
                        # spurious callers).  Emit an unresolved edge instead.
                        if (
                            callee_name
                            and import_path_hint is None
                            and func_node.type == "selector_expression"
                            and callee_name[0].islower()
                        ):
                            dst_id = f"go:external:0-0:{callee_name}:unresolved"
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                # INV-fazim: the ``external`` placeholder means no
                                # package was attributed, so the edge is unresolved.
                                # This guard exists precisely BECAUSE resolution
                                # would have been wrong (crossing package
                                # boundaries), which makes claiming resolution here
                                # self-contradictory.
                                is_resolved=False,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"call_construct": "method", "visibility": "unexported"},
                            ))
                            callee_name = None  # Already handled

                        # Go stdlib interface method guard: When receiver type
                        # is unknown and the method name matches a well-known
                        # stdlib interface method (Lock, Close, Read, Write,
                        # String, Error, etc.), treat it as ambiguous even with
                        # only 1 repo candidate.  Most .Lock() calls are
                        # sync.Mutex.Lock(), not DirLocker.Lock — without this
                        # guard, DirLocker.Lock absorbs 255+ false in-degree
                        # edges.  Only applies to selector expressions (method
                        # calls), not package-qualified calls.
                        if (
                            callee_name
                            and import_path_hint is None
                            and func_node.type == "selector_expression"
                            and callee_name in _GO_STDLIB_INTERFACE_METHODS
                        ):
                            dst_id = f"go:external:0-0:{callee_name}:unresolved"
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                # INV-fazim: same rule as the sibling guards. This
                                # one fires when the receiver type is UNKNOWN and the
                                # method name merely looks like a stdlib interface
                                # method — the weakest evidence of the three, so
                                # reporting it resolved was the least defensible.
                                is_resolved=False,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"call_construct": "method", "receiver": "stdlib"},
                            ))
                            callee_name = None  # Already handled

                    if callee_name:
                        # Check local symbols first — but NOT when the call
                        # is package-qualified (import_path_hint set), because
                        # e.g. bug.AddComment() should resolve to the imported
                        # package, not a local method named AddComment.
                        if callee_name in local_symbols and import_path_hint is None:
                            callee = local_symbols[callee_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"call_construct": "function"},
                            ))
                        # Check global symbols with disambiguation via ListNameResolver
                        else:
                            lookup_result = resolver.lookup(callee_name, path_hint=import_path_hint)
                            # INV-fahub: a BARE identifier call (``import_path_hint
                            # is None`` — no package evidence) that resolves only to
                            # a DIFFERENT type's METHOD on weak (ambiguous /
                            # non-exact) short-name evidence is a magnet — dozens of
                            # bare call sites collapse onto one arbitrary
                            # ``Type.method``. Withhold that bind and emit an honest
                            # unresolved edge stamped with the enclosing class, so
                            # the inherited_calls Site-1 walker can recover a genuine
                            # inherited implicit-receiver call (and a true cross-class
                            # magnet stays external). Free functions, same-class
                            # methods, and strong exact / path-hint matches are
                            # unaffected; a package-qualified selector (``pkg.Foo()``,
                            # ``import_path_hint`` set) carries real routing evidence
                            # and is never withheld here. The enclosing type is the
                            # receiver of the calling method (``Type.method`` →
                            # ``Type``); a free-function caller has no enclosing type.
                            _enclosing_type = (
                                current_function.name.rsplit(".", 1)[0]
                                if "." in current_function.name else None
                            )
                            _sym = lookup_result.symbol
                            _defer = (
                                import_path_hint is None
                                and _sym is not None
                                and defer_bare_method_call(
                                    _sym.kind, _sym.name,
                                    lookup_result.match_type, _enclosing_type,
                                )
                            )
                            if lookup_result.found and not _defer:
                                # Scale base confidence by resolver's confidence multiplier
                                edge_confidence = 0.80 * lookup_result.confidence
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    confidence=edge_confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"call_construct": "function"},
                                ))
                            elif _defer:
                                # Bare call only (``import_path_hint`` is None here),
                                # so the target module is unknown → external hint.
                                edges.append(make_unresolved_edge(
                                    "go", current_function.id, callee_name,
                                    node.start_point[0] + 1, PASS_ID,
                                    run.execution_id,
                                    enclosing_class=_enclosing_type,
                                ))
                            # Bug #2 fix: Create edge for external/unresolved method calls
                            # This enables linkers to potentially match across languages
                            elif func_node.type == "selector_expression":
                                # For s.Method() where Method is external, create unresolved edge
                                # Use the FULL import path (not stripped) to keep the ID meaningful
                                unresolved_path = full_import_path if full_import_path else import_path_hint
                                if unresolved_path:
                                    # e.g., go:google.golang.org/grpc:0-0:RegisterService:unresolved
                                    dst_id = f"go:{unresolved_path}:0-0:{callee_name}:unresolved"
                                else:
                                    # Fallback: use "external" as the path
                                    dst_id = f"go:external:0-0:{callee_name}:unresolved"
                                _meta: dict[str, object] = {
                                    "call_construct": "method",
                                }
                                # WI-lipis: what this call site actually
                                # touches, where the catalogue row cannot say.
                                _handle = _go_wrapped_handle_kind(
                                    node, source,
                                    (unresolved_path or "").rsplit("/", 1)[-1],
                                    callee_name,
                                )
                                if _handle:
                                    _meta["io_target_kind"] = _handle
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    is_resolved=False,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta=_meta,
                                ))
                            # WI-vovum / WI-mafik: bare-identifier call whose
                            # name was dot-imported (``import . "strings"`` +
                            # ``Contains(...)``). Attribute it to the first
                            # dot-imported package — we don't know which
                            # specific package exported the symbol, but
                            # surfacing one of the dot-import paths gives
                            # downstream linkers the right place to look.
                            elif (
                                func_node.type == "identifier"
                                and dot_imports
                            ):
                                source_pkg = dot_imports[0]
                                ref = ExternalRef(
                                    lang="go",
                                    module_path=source_pkg,
                                    name=callee_name,
                                )
                                dst_id = f"go:{source_pkg}:0-0:{callee_name}:unresolved"
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    is_resolved=False,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"call_construct": "function", "binding": "dot_import"},
                                    dst_ref=ref,
                                ))

                # Detect function references passed as arguments
                # e.g., register(handler), r.Get("/path", ViewIssue)
                args_node = find_child_by_field(node, "arguments")
                if args_node and current_function is not None:
                    _extract_function_reference_edges(
                        args_node, source, current_function,
                        local_symbols, global_symbols, resolver,
                        node.start_point[0] + 1, edges, run,
                        scoped_vars=var_types,
                    )

        # Detect function references in struct literal fields
        # e.g., cobra.Command{RunE: myFunc}, http.ServeMux{Handler: h}
        # AST: keyed_element -> literal_element (key) : literal_element (value)
        # The value's child may be an identifier or selector_expression.
        elif node.type == "keyed_element":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                # Get the value part: the last literal_element child
                lit_elems = [c for c in node.children if c.type == "literal_element"]
                if len(lit_elems) >= 2:
                    value_elem = lit_elems[-1]
                    # Unwrap literal_element to find the actual expression
                    value_node = value_elem.children[0] if value_elem.children else value_elem
                    if value_node.type == "identifier":
                        ref_name = node_text(value_node, source)
                        if ref_name in local_symbols:
                            sym = local_symbols[ref_name]
                            if sym.kind in ("function", "method") and sym.id != current_function.id:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="struct_field_reference",
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                        else:
                            lookup_result = resolver.lookup(ref_name)
                            if (
                                lookup_result.found
                                and lookup_result.symbol.kind in ("function", "method")
                            ):
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="struct_field_reference",
                                    confidence=0.70 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                    elif value_node.type == "selector_expression":
                        # pkg.Handler or obj.Method as field value
                        sel_field = find_child_by_field(value_node, "field")
                        if sel_field:
                            ref_name = node_text(sel_field, source)
                            lookup_result = resolver.lookup(ref_name)
                            if (
                                lookup_result.found
                                and lookup_result.symbol.kind in ("function", "method")
                            ):
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="struct_field_reference",
                                    confidence=0.70 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

    # WI-lozug: emit module_attr_ref edges for attribute reads on imported
    # Go packages (e.g. ``os.Stdout``, ``os.Stderr``).  These pair with the
    # ``attributes:`` entries in io_primitives/go.yaml — without them, the
    # ipc_send / ipc_recv / env_read chains for attribute primitives were
    # silently inert.  The src is a file-level pseudo-symbol (matching the
    # convention used for ``imports`` edges earlier in this function),
    # because Go does not carry a per-function caller through to the
    # io_boundary pipeline's attribute matching — the dst encodes the
    # primitive identity, which is what io_boundary tags against.  Skip
    # blank/dot imports (``_`` and ``.``) because they don't introduce a
    # namespaced alias.
    attr_imports = {
        alias: path
        for alias, path in import_aliases.items()
        if alias not in ("_", ".")
    }
    if attr_imports:
        file_pseudo_symbol = Symbol(
            id=file_id,
            name=file_path.name,
            kind="module",
            language="go",
            path=str(file_path),
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            line_span=1,
        )
        emit_module_attribute_refs(
            tree.root_node,
            source,
            attr_imports,
            file_pseudo_symbol,
            "go",
            edges,
            node_kinds=("selector_expression",),
            object_field_names=("operand",),
            property_field_names=("field",),
            pass_id=PASS_ID,
            run_id=run.execution_id,
            call_node_kinds=("call_expression",),
            call_function_field_names=("function",),
            # INV-fafol: anchor each read to the callable that performs it.
            enclosing_symbols=list(local_symbols.values()),
        )

    return edges


def _extract_handler_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract handler name from a call argument node.

    Handles three cases:
    - identifier: ``listUsers`` -> ``"listUsers"``
    - selector_expression: ``handlers.GetAPI`` -> ``"handlers.GetAPI"``
    - call_expression: ``api.ready(api.remoteRead)`` -> ``"api.remoteRead"``
      Unwraps one level of wrapper calls: when a route handler argument is
      a call wrapping a function reference (identifier or selector_expression),
      extracts the inner handler.  This handles the common Go pattern of
      middleware wrappers: ``r.Post("/read", api.ready(api.remoteRead))``
      correctly attributes the route to ``api.remoteRead``, not ``api.ready``.
      Falls back to the function name for constructors like
      ``httpapi.NewHandler(env)`` where the argument is not a handler.

    Note: ``func_literal`` (anonymous closures) are handled at the call sites
    in ``_extract_go_routes`` and ``_extract_go_usage_contexts``, not here.
    Those functions skip closures and prefer named handlers, falling back to
    ``"<closure>"`` only when no named handler exists.

    Returns None if the node type is not recognized.
    """
    if node.type == "identifier":
        return node_text(node, source)
    elif node.type == "selector_expression":
        return node_text(node, source)
    elif node.type == "call_expression":
        # Check if any argument is a selector_expression (e.g.,
        # api.remoteRead).  This indicates a middleware wrapper pattern:
        # api.ready(api.remoteRead) → extract api.remoteRead.
        # Only selector_expressions qualify — plain identifiers like
        # ``env`` in ``httpapi.NewHandler(env)`` are likely config args,
        # not handler references.
        args_node = find_child_by_field(node, "arguments")
        if args_node:
            for arg in args_node.children:
                if arg.type == "selector_expression":
                    return node_text(arg, source)
        # Fallback: use the function name (constructor pattern)
        func_node = find_child_by_field(node, "function")
        if func_node:
            return node_text(func_node, source)
    return None  # pragma: no cover - defensive for unrecognized node types


def _extract_wrapper_info(
    handler_arg: "tree_sitter.Node",
    handler_name: str,
    source: bytes,
    closure_var_map: dict[str, tuple[int, int, int, int]] | None = None,
) -> tuple[str | None, str | None]:
    """Extract wrapper function name and true inner handler from a handler arg.

    When a route handler argument is a call expression like
    ``wrapAgent(api.query)``, and the handler name was extracted from an
    inner argument (not the callee itself), the callee is a wrapper function.

    Also handles the case where the inner handler is a plain identifier
    (e.g., ``auth(listUsers)``) — ``_extract_handler_name`` falls back to
    returning the callee as the handler in this case, so we need to check
    whether the callee is a known closure variable to distinguish wrapper
    calls from constructor calls.

    Returns ``(wrapper_name, corrected_handler_name)`` or ``(None, None)``
    if no wrapper is detected.  When ``corrected_handler_name`` is not None,
    the caller should use it instead of the original ``handler_name``.
    """
    if handler_arg.type != "call_expression":
        return None, None
    func_node = find_child_by_field(handler_arg, "function")
    if func_node is None:
        return None, None  # pragma: no cover
    callee_name = node_text(func_node, source)

    # Case 1: handler was unwrapped from an inner selector_expression
    # (e.g., wrapAgent(api.query) → handler_name="api.query", callee="wrapAgent")
    if callee_name != handler_name:
        return callee_name, None

    # Case 2: handler IS the callee (constructor/fallback pattern).
    # Check if the callee is a known closure variable — if so, the
    # first argument is the actual handler being wrapped.
    # e.g., auth(listUsers) where auth := func(f func()) func() { ... }
    if closure_var_map and callee_name in closure_var_map:
        args_node = find_child_by_field(handler_arg, "arguments")
        if args_node:
            for arg in args_node.children:
                if arg.type == "identifier":
                    return callee_name, node_text(arg, source)
                if arg.type == "selector_expression":  # pragma: no cover
                    return callee_name, node_text(arg, source)  # pragma: no cover

    return None, None


def _maybe_create_wrapper_symbol(
    wrapper_name: str,
    closure_var_map: dict[str, tuple[int, int, int, int]],
    wrapper_symbols_created: dict[str, Symbol],
    file_path: Path,
    run: AnalysisRun,
    file_stable_id: str = "",
) -> tuple[Symbol | None, bool]:
    """Create a Symbol for a closure wrapper variable, if not already created.

    If the wrapper name matches a known closure variable from the pre-pass,
    creates a function Symbol at the closure's definition location with
    ``middleware`` concept metadata.

    Returns ``(symbol, is_new)`` where ``is_new`` is True only when a
    new Symbol was created (False for cache hits and non-matches).

    WI-bokab (v7): ``file_stable_id`` is the repo-relative file-identity anchor
    (computed in ``_analyze_go_impl``, threaded through ``_extract_go_routes``)
    folded into the wrapper Symbol's ``make_typed_stable_id`` so same-named
    closure wrappers in different files hash distinctly. Defaults to ``""``
    (no-op fold) for legacy/test callers.
    """
    if wrapper_name in wrapper_symbols_created:
        return wrapper_symbols_created[wrapper_name], False
    if wrapper_name not in closure_var_map:
        return None, False
    start_line, end_line, start_col, end_col = closure_var_map[wrapper_name]
    sym = Symbol(
        id=make_symbol_id(
            "go", str(file_path), start_line, end_line,
            wrapper_name, "function",
        ),
        stable_id=make_typed_stable_id(
            "function", wrapper_name,
            name=wrapper_name,
            qualified_name=wrapper_name,
            file_stable_id=file_stable_id,
        ),
        name=wrapper_name,
        kind="function",
        language="go",
        path=str(file_path),
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        ),
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        meta={"concepts": ["middleware"], "is_closure_wrapper": True},
        line_span=end_line - start_line + 1,
        is_exported=bool(wrapper_name) and wrapper_name[0].isupper(),
        # No AST node available here (the wrapper Symbol is synthesized
        # from positional data tracked in the closure-var pre-pass), so
        # cyclomatic_complexity defaults to None per the
        # writer-contract spec.
    )
    wrapper_symbols_created[wrapper_name] = sym
    return sym, True


def _extract_string_from_node(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract a string value from a node, handling string literals and concatenation.

    For a plain string literal, returns its content.
    For a binary expression (string concat), collects all string literal
    segments.  Variable parts are omitted, so ``baseUrl + "/users"``
    yields ``"/users"`` (the resolvable suffix).
    """
    if node.type == "interpreted_string_literal":
        content_node = find_child_by_type(node, "interpreted_string_literal_content")
        if content_node:
            return node_text(content_node, source)
        return node_text(node, source).strip('"')  # pragma: no cover
    if node.type == "binary_expression":
        # Collect all string literal parts from the concat chain
        parts: list[str] = []
        for child in node.children:
            val = _extract_string_from_node(child, source)
            if val is not None:
                parts.append(val)
        return "".join(parts) if parts else None
    return None


def _extract_first_string_arg(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the first string literal from an argument list.

    Handles plain string literals and string concatenation expressions
    (e.g., ``baseUrl + "/path"`` → ``"/path"``).

    Returns the string content without quotes, or None if no string arg found.
    """
    for arg in args_node.children:
        if arg.type == "interpreted_string_literal":
            content_node = find_child_by_type(arg, "interpreted_string_literal_content")
            if content_node:
                return node_text(content_node, source)
            return node_text(arg, source).strip('"')  # pragma: no cover
        if arg.type == "binary_expression":
            result = _extract_string_from_node(arg, source)
            if result is not None:
                return result
    return None  # pragma: no cover - only called with argument lists containing strings


def _extract_gorilla_chain_route(
    call_node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str | None, str | None, str | None]:
    """Walk a Gorilla mux builder chain to extract route path, HTTP method, and handler.

    Given an outermost call_expression whose method is Handler/HandlerFunc,
    walks the chain backwards to find Path/PathPrefix and optional Methods calls.

    Example chains:
    - ``router.Path("/api").Handler(h)`` -> ("/api", None, "h")
    - ``router.Path("/api").Methods("GET").Handler(h)`` -> ("/api", "GET", "h")
    - ``router.PathPrefix("/").Handler(h)`` -> ("/", None, "h")

    Returns:
        Tuple of (route_path, http_method, handler_name).
        Any element may be None if not found.
    """
    # Step 1: Extract handler from the outermost call's arguments.
    # Pick the last resolvable argument (consistent with HTTP-method and
    # Handle branches where middleware precedes the handler).
    args_node = find_child_by_field(call_node, "arguments")
    handler_name = None
    if args_node:
        for arg in reversed(args_node.children):
            if arg.type in ("(", ")", ","):
                continue
            handler_name = _extract_handler_name(arg, source)
            if handler_name is not None:
                break

    # Step 2: Walk the chain backwards through the selector/call nesting
    route_path = None
    http_method = None

    # The function field of the outer call is a selector_expression
    # e.g., for .Handler(h), the operand of that selector is the previous call
    func_node = find_child_by_field(call_node, "function")
    if not func_node or func_node.type != "selector_expression":
        return (None, None, handler_name)  # pragma: no cover - defensive for malformed AST

    # Walk the chain: operand is the inner call_expression
    current = find_child_by_field(func_node, "operand")

    while current is not None and current.type == "call_expression":
        # Get this call's method name from its function (selector_expression)
        inner_func = find_child_by_field(current, "function")
        if not inner_func or inner_func.type != "selector_expression":
            break  # pragma: no cover - defensive for malformed AST

        inner_field = find_child_by_field(inner_func, "field")
        if not inner_field:
            break  # pragma: no cover

        method_name = node_text(inner_field, source)

        inner_args = find_child_by_field(current, "arguments")

        if method_name in GORILLA_PATH_METHODS and inner_args:
            route_path = _extract_first_string_arg(inner_args, source)
            break  # Path/PathPrefix is the start of the chain

        if method_name == "Methods" and inner_args:
            method_str = _extract_first_string_arg(inner_args, source)
            if method_str:
                http_method = method_str.upper()

        # Continue walking: the operand of this selector is the next inner call
        current = find_child_by_field(inner_func, "operand")

    return (route_path, http_method, handler_name)


def _get_go_route_prefix(
    node: "tree_sitter.Node",
    source: bytes,
) -> str:
    """Walk up the AST to collect Group/Route prefix strings.

    When a route call like ``r.GET("/users", handler)`` is nested inside
    ``r.Group("/api/v1", func() { ... })``, this function discovers the
    Group call and returns ``"/api/v1"``.  For nested groups, prefixes
    compose: ``Group("/admin") > Group("/users") > GET("/list")`` →
    ``"/admin/users"``.

    Works for closure-based group patterns used by Macaron (Forgejo/Gitea)
    and Chi.  Return-value-based groups (Gin/Echo/Fiber ``v1 := r.Group(...)``
    followed by ``v1.GET(...)``) require variable tracking and are not
    handled here.

    Returns the composed prefix string (empty if no Group ancestors).
    """
    prefixes: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "func_literal":
            parent = current.parent
            if parent is not None and parent.type == "argument_list":
                grandparent = parent.parent
                if grandparent is not None and grandparent.type == "call_expression":
                    func_node = find_child_by_field(grandparent, "function")
                    if func_node is not None and func_node.type == "selector_expression":
                        field_node = find_child_by_field(func_node, "field")
                        if field_node is not None:
                            method = node_text(field_node, source)
                            if method in _GO_GROUP_METHODS:
                                args_node = find_child_by_field(
                                    grandparent, "arguments",
                                )
                                if args_node is not None:
                                    prefix = _extract_first_string_arg(
                                        args_node, source,
                                    )
                                    if prefix:
                                        prefixes.append(prefix)
        current = current.parent
    prefixes.reverse()
    return "".join(prefixes)


def _build_group_prefix_map(
    node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Build a mapping of variable names to router group path prefixes.

    Scans for Gin/Echo/Fiber-style variable-based group patterns::

        api := r.Group("/api")
        v1 := api.Group("/v1")

    Returns a dict like ``{"api": "/api", "v1": "/api/v1"}``.

    This complements ``_get_go_route_prefix`` which handles closure-based
    groups (Macaron/Chi).  Gin, Echo, and Fiber assign groups to variables
    instead of using closures.
    """
    prefix_map: dict[str, str] = {}

    for n in iter_tree(node):
        # Match: varName := receiver.Group("/path")
        if n.type != "short_var_declaration":
            continue

        left = find_child_by_field(n, "left")
        right = find_child_by_field(n, "right")
        if left is None or right is None:  # pragma: no cover
            continue

        # Left side: expression_list with a single identifier
        left_ids = [c for c in left.children if c.type == "identifier"]
        if len(left_ids) != 1:  # pragma: no cover
            continue
        var_name = node_text(left_ids[0], source)

        # Right side: expression_list with a call_expression
        right_calls = [c for c in right.children if c.type == "call_expression"]
        if len(right_calls) != 1:
            continue
        call_node = right_calls[0]

        func_node = find_child_by_field(call_node, "function")
        if func_node is None or func_node.type != "selector_expression":
            continue

        field_node = find_child_by_field(func_node, "field")
        if field_node is None:  # pragma: no cover
            continue

        method = node_text(field_node, source)
        if method not in _GO_GROUP_METHODS:
            continue

        # Extract the group prefix string from the call arguments
        args_node = find_child_by_field(call_node, "arguments")
        if args_node is None:  # pragma: no cover
            continue
        group_path = _extract_first_string_arg(args_node, source)
        if not group_path:  # pragma: no cover
            continue

        # Check if the receiver is itself a group variable (nested groups)
        operand_node = find_child_by_field(func_node, "operand")
        receiver_prefix = ""
        if operand_node is not None and operand_node.type == "identifier":
            receiver_name = node_text(operand_node, source)
            receiver_prefix = prefix_map.get(receiver_name, "")

        prefix_map[var_name] = receiver_prefix + group_path

    return prefix_map


def _build_closure_var_map(
    node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, tuple[int, int, int, int]]:
    """Find local variables assigned to func literals (closure variables).

    Scans for short variable declarations where the RHS is a func_literal:
        wrap := func(f apiFunc) http.HandlerFunc { ... }

    Returns a dict mapping variable name to source location tuple:
        {var_name: (start_line, end_line, start_col, end_col)}

    Used by ``_extract_go_routes`` to detect when a route handler argument
    is a call to a closure wrapper (e.g., ``wrapAgent(api.query)``), enabling
    the emission of ``wraps`` edges that make middleware wrappers visible
    in the call graph.
    """
    closure_vars: dict[str, tuple[int, int, int, int]] = {}
    for n in iter_tree(node):
        if n.type != "short_var_declaration":
            continue
        lhs = n.children[0] if n.children else None
        rhs = n.children[-1] if len(n.children) >= 3 else None
        if lhs is None or rhs is None:
            continue  # pragma: no cover
        # The RHS may be a direct func_literal or wrapped in an
        # expression_list (tree-sitter Go grammar wraps the RHS of
        # short_var_declaration in expression_list).
        func_lit_node = None
        if rhs.type == "func_literal":
            func_lit_node = rhs  # pragma: no cover - grammar always wraps in expr_list
        elif rhs.type == "expression_list":
            for child in rhs.children:
                if child.type == "func_literal":
                    func_lit_node = child
                    break
        if func_lit_node is None:
            continue
        var_name = None
        if lhs.type == "expression_list":
            for child in lhs.children:
                if child.type == "identifier":
                    var_name = node_text(child, source)
                    break
        if var_name:
            closure_vars[var_name] = (
                func_lit_node.start_point[0] + 1,
                func_lit_node.end_point[0] + 1,
                func_lit_node.start_point[1],
                func_lit_node.end_point[1],
            )
    return closure_vars


def _extract_go_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    local_symbols: dict[str, Symbol] | None = None,
    file_stable_id: str = "",
) -> tuple[list[Symbol], list[Edge]]:
    """Extract Go web framework route symbols from a tree-sitter node.

    Detects patterns like:
    - Gin/Echo: r.GET("/path", handler), e.POST("/users", createUser)
    - Fiber: app.Get("/path", handler) (lowercase methods)
    - Gorilla mux: router.HandleFunc("/path", handler), router.Handle("/path", handler)
    - Gorilla mux builder: router.Path("/path").Methods("GET").Handler(handler)
    - Group prefix composition (closure): r.Group("/api", func() { r.GET("/users", h) }) → /api/users
    - Group prefix composition (variable): api := r.Group("/api"); api.GET("/users", h) → /api/users
    - Mount points: r.Mount("/api/v1", apiRoutes()) → route_mount symbol + edge

    Creates symbols with stable_id = sha256("route:{method}:{path}") per ADR-0014.
    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Also detects closure wrapper patterns: when a route handler argument
    is a call to a local func-typed variable (e.g., ``wrapAgent(api.query)``),
    records ``wrapper_name`` in route metadata, creates a Symbol for the
    wrapper closure, and emits a ``wraps`` edge from the wrapper to the
    inner handler (when resolvable via ``local_symbols``).

    Returns:
        Tuple of (route symbols + wrapper symbols, mount edges + wraps edges).
    """
    routes: list[Symbol] = []
    mount_edges: list[Edge] = []

    # Pre-pass: build variable-to-prefix map for Gin/Echo/Fiber groups
    group_prefix_map = _build_group_prefix_map(node, source)

    # Pre-pass: build closure variable map for wrapper detection
    closure_var_map = _build_closure_var_map(node, source)
    # Track wrapper symbols already created (deduplicate by name)
    _wrapper_symbols_created: dict[str, Symbol] = {}

    for n in iter_tree(node):
        # Look for call_expression with selector_expression function
        if n.type == "call_expression":
            func_node = find_child_by_field(n, "function")

            if func_node and func_node.type == "selector_expression":
                # Get the method name (e.g., GET, POST, Get, Post)
                field_node = find_child_by_field(func_node, "field")

                if field_node:
                    method_name = node_text(field_node, source)

                    if method_name in GO_HTTP_METHODS:
                        # Gin/Echo/Fiber: r.GET("/path", handler)
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = _extract_first_string_arg(args_node, source)
                            handler_name = None

                            # Route paths must start with "/" to distinguish
                            # from non-route method calls with string args
                            # (e.g., K8s client.Get(ctx, "my-app", opts)).
                            if route_path and route_path.startswith("/"):
                                # Pick the LAST non-string argument as handler.
                                # Go frameworks pass middleware before the handler:
                                # r.GET("/path", mw1, mw2, handler)
                                # Prefer named handlers over closures — if the
                                # last arg is a func_literal, keep looking for
                                # a named handler but remember the closure as
                                # fallback.
                                closure_fallback = False
                                handler_arg_node = None
                                for arg in reversed(args_node.children):
                                    if arg.type in (
                                        "interpreted_string_literal",
                                        "(",
                                        ")",
                                        ",",
                                    ):
                                        continue
                                    if arg.type == "func_literal":
                                        closure_fallback = True
                                        continue
                                    handler_name = _extract_handler_name(arg, source)
                                    if handler_name is not None:
                                        handler_arg_node = arg
                                        break
                                if handler_name is None and closure_fallback:
                                    handler_name = "<closure>"

                            if route_path and handler_name:
                                prefix = _get_go_route_prefix(n, source)
                                # Variable-based group prefix (Gin/Echo/Fiber)
                                if not prefix:
                                    operand = find_child_by_field(func_node, "operand")
                                    if operand is not None and operand.type == "identifier":
                                        prefix = group_prefix_map.get(
                                            node_text(operand, source), "",
                                        )
                                route_path = prefix + route_path
                                normalized_method = _GO_METHOD_ALIASES.get(
                                    method_name, method_name.upper(),
                                )
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                # Detect wrapper pattern
                                # WI-zugob: route_path / http_method /
                                # framework_role are owned by make_route_symbol
                                # below; only producer-specific keys go here.
                                route_meta: dict[str, str] = {
                                    "handler_name": handler_name,
                                }
                                if handler_arg_node is not None:
                                    w_name, corrected = _extract_wrapper_info(
                                        handler_arg_node, handler_name, source,
                                        closure_var_map=closure_var_map,
                                    )
                                    if w_name:
                                        if corrected:
                                            handler_name = corrected
                                            route_meta["handler_name"] = corrected
                                        route_meta["wrapper_name"] = w_name
                                        # Create wrapper symbol and wraps edge
                                        wrapper_sym, is_new = _maybe_create_wrapper_symbol(
                                            w_name, closure_var_map,
                                            _wrapper_symbols_created,
                                            file_path, run,
                                            file_stable_id=file_stable_id,
                                        )
                                        if wrapper_sym:
                                            if is_new:
                                                routes.append(wrapper_sym)
                                            # Try to resolve inner handler for wraps edge
                                            if local_symbols:
                                                handler_parts = handler_name.rsplit(".", 1)
                                                short_name = handler_parts[-1] if handler_parts else handler_name
                                                resolved = local_symbols.get(handler_name) or local_symbols.get(short_name)
                                                if resolved:
                                                    mount_edges.append(Edge.create(
                                                        src=wrapper_sym.id,
                                                        dst=resolved.id,
                                                        edge_type="wraps",
                                                        line=start_line,
                                                        origin=PASS_ID,
                                                        evidence_type="closure_wrapper",
                                                        origin_run_id=run.execution_id,
                                                    ))

                                route_sym = make_route_symbol(
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    method=normalized_method,
                                    route_path=route_path,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    extra_meta=route_meta,
                                    is_exported=bool(handler_name) and handler_name.rsplit(".", 1)[-1][:1].isupper(),
                                )
                                routes.append(route_sym)

                    elif method_name in GORILLA_HANDLE_METHODS:
                        # Gorilla mux simple: router.HandleFunc("/path", handler)
                        # Also supports Go 1.22+ stdlib: mux.Handle("POST /path", h)
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = _extract_first_string_arg(args_node, source)
                            handler_name = None
                            # Go 1.22+ http.ServeMux combined method-path:
                            # "POST /v1/data/{path...}" → method="POST", path="/v1/data/{path...}"
                            handle_http_method = "ANY"
                            if route_path and not route_path.startswith("/"):
                                parts = route_path.split(" ", 1)
                                if (
                                    len(parts) == 2
                                    and parts[0].upper() in GO_HTTP_METHODS_UPPER
                                    and parts[1].startswith("/")
                                ):
                                    handle_http_method = parts[0].upper()
                                    route_path = parts[1]

                            if route_path and route_path.startswith("/"):
                                # Last non-string arg is handler (same
                                # middleware convention as HTTP methods).
                                # Prefer named handlers over closures.
                                closure_fallback = False
                                handler_arg_node = None
                                for arg in reversed(args_node.children):
                                    if arg.type in (
                                        "interpreted_string_literal",
                                        "(",
                                        ")",
                                        ",",
                                    ):
                                        continue
                                    if arg.type == "func_literal":
                                        closure_fallback = True
                                        continue
                                    handler_name = _extract_handler_name(arg, source)
                                    if handler_name is not None:
                                        handler_arg_node = arg
                                        break
                                if handler_name is None and closure_fallback:
                                    handler_name = "<closure>"

                            if route_path and handler_name:
                                prefix = _get_go_route_prefix(n, source)
                                if not prefix:
                                    operand = find_child_by_field(func_node, "operand")
                                    if operand is not None and operand.type == "identifier":
                                        prefix = group_prefix_map.get(
                                            node_text(operand, source), "",
                                        )
                                route_path = prefix + route_path
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                # Detect wrapper pattern
                                # WI-zugob: canonical route keys are owned by
                                # make_route_symbol below.
                                route_meta_g: dict[str, str] = {
                                    "handler_name": handler_name,
                                }
                                if handler_arg_node is not None:
                                    w_name, corrected = _extract_wrapper_info(
                                        handler_arg_node, handler_name, source,
                                        closure_var_map=closure_var_map,
                                    )
                                    if w_name:
                                        if corrected:
                                            handler_name = corrected
                                            route_meta_g["handler_name"] = corrected
                                        route_meta_g["wrapper_name"] = w_name
                                        wrapper_sym, is_new = _maybe_create_wrapper_symbol(
                                            w_name, closure_var_map,
                                            _wrapper_symbols_created,
                                            file_path, run,
                                            file_stable_id=file_stable_id,
                                        )
                                        if wrapper_sym:
                                            if is_new:
                                                routes.append(wrapper_sym)
                                            if local_symbols:
                                                handler_parts = handler_name.rsplit(".", 1)
                                                short_name = handler_parts[-1] if handler_parts else handler_name
                                                resolved = local_symbols.get(handler_name) or local_symbols.get(short_name)
                                                if resolved:
                                                    mount_edges.append(Edge.create(
                                                        src=wrapper_sym.id,
                                                        dst=resolved.id,
                                                        edge_type="wraps",
                                                        line=start_line,
                                                        origin=PASS_ID,
                                                        evidence_type="closure_wrapper",
                                                        origin_run_id=run.execution_id,
                                                    ))

                                route_sym = make_route_symbol(
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    method=handle_http_method,
                                    route_path=route_path,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    extra_meta=route_meta_g,
                                    is_exported=bool(handler_name) and handler_name.rsplit(".", 1)[-1][:1].isupper(),
                                )
                                routes.append(route_sym)

                    elif method_name in GORILLA_CHAIN_TERMINATORS:
                        # Gorilla mux builder: router.Path("/x").Methods("GET").Handler(h)
                        route_path, http_method, handler_name = (
                            _extract_gorilla_chain_route(n, source)
                        )

                        if route_path and handler_name:
                            prefix = _get_go_route_prefix(n, source)
                            if not prefix:  # pragma: no cover - gorilla chains have call operands
                                operand = find_child_by_field(func_node, "operand")
                                if operand is not None and operand.type == "identifier":
                                    prefix = group_prefix_map.get(
                                        node_text(operand, source), "",
                                    )
                            route_path = prefix + route_path
                            normalized_method = http_method or "ANY"
                            start_line = n.start_point[0] + 1
                            end_line = n.end_point[0] + 1

                            route_sym = make_route_symbol(
                                language="go",
                                path=str(file_path),
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=n.start_point[1],
                                    end_col=n.end_point[1],
                                ),
                                method=normalized_method,
                                route_path=route_path,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                extra_meta={"handler_name": handler_name},
                                is_exported=bool(handler_name) and handler_name.rsplit(".", 1)[-1][:1].isupper(),
                            )
                            routes.append(route_sym)

                    elif method_name in _GO_MOUNT_METHODS:
                        # Chi/Gorilla: r.Mount("/api/v1", handler)
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            mount_prefix = _extract_first_string_arg(
                                args_node, source,
                            )
                            if mount_prefix:
                                # Find handler ref (second non-punctuation arg)
                                handler_ref: str | None = None
                                found_first = False
                                for arg in args_node.children:
                                    if arg.type in ("(", ")", ","):
                                        continue
                                    if arg.type == "interpreted_string_literal":
                                        found_first = True
                                        continue
                                    if found_first:
                                        handler_ref = _extract_handler_name(
                                            arg, source,
                                        )
                                        break

                                if handler_ref:
                                    start_line = n.start_point[0] + 1
                                    end_line = n.end_point[0] + 1

                                    # WI-zugob: the FIFTH go minting site. The
                                    # earlier go migration moved the four
                                    # net/http + mux + chi Handle paths and
                                    # missed this one, because the go
                                    # route-parity fixture is net/http only and
                                    # never exercised a Mount — so the gate
                                    # could not see that it violated BOTH the id
                                    # kind-slot (unregistered "route_mount") and
                                    # the id name-slot (name was the handler,
                                    # the slot was "MOUNT {prefix}"). The
                                    # go-mount fixture case now covers it.
                                    mount_sym = make_route_symbol(
                                        language="go",
                                        path=str(file_path),
                                        span=Span(
                                            start_line=start_line,
                                            end_line=end_line,
                                            start_col=n.start_point[1],
                                            end_col=n.end_point[1],
                                        ),
                                        method="MOUNT",
                                        route_path=mount_prefix,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        framework_role="route_mount",
                                        handler_ref=handler_ref,
                                        extra_meta={"mount_prefix": mount_prefix},
                                        is_exported=bool(handler_ref) and handler_ref.rsplit(".", 1)[-1][:1].isupper(),
                                    )
                                    routes.append(mount_sym)

                                    # Create edge from enclosing function
                                    # to handler, carrying mount prefix.
                                    if local_symbols is not None:
                                        enclosing = _get_enclosing_function(
                                            n, source, local_symbols,
                                        )
                                        if enclosing is not None:
                                            # Resolve handler dst: try
                                            # local_symbols first, then
                                            # build synthetic ID.
                                            dst_id: str | None = None
                                            # INV-fazim: the flag MIRRORS THE BRANCH
                                            # rather than being stated once for the
                                            # whole call. This is the only external
                                            # emit site in this file whose dst may be
                                            # either a resolved in-repo symbol or the
                                            # ``external`` placeholder, so a blanket
                                            # is_resolved=False would be as wrong as
                                            # the inherited default of True.
                                            _handler_resolved = (
                                                handler_ref in local_symbols
                                            )
                                            if _handler_resolved:
                                                dst_id = local_symbols[
                                                    handler_ref
                                                ].id
                                            else:
                                                dst_id = (
                                                    f"go:external:0-0:"
                                                    f"{handler_ref}:function"
                                                )
                                            # ADR-0028 Phase 3 / audit-findings 0014:
                                            # framework-dispatch leak.
                                            mount_edges.append(Edge.create(
                                                src=enclosing.id,
                                                dst=dst_id,
                                                edge_type="calls",
                                                line=start_line,
                                                is_resolved=_handler_resolved,
                                                origin=PASS_ID,
                                                origin_run_id=(
                                                    run.execution_id
                                                ),
                                                evidence_type="ast_call_direct",
                                                meta={
                                                    "framework_dispatch": "route_mount",
                                                },
                                            ))

        elif n.type == "assignment_statement":
            # go-swagger/go-openapi handler cache pattern:
            #   o.handlers["DELETE"]["/silence/{id}"] = silence.NewDeleteSilence(...)
            #
            # AST: assignment_statement
            #   left: expression_list
            #     index_expression           ← outer: ["/path"]
            #       index_expression         ← inner: ["METHOD"]
            #         selector_expression    ← o.handlers
            #         interpreted_string_literal  ← "DELETE"
            #       interpreted_string_literal    ← "/silence/{id}"
            #   right: expression_list
            #     call_expression            ← silence.NewDeleteSilence(...)
            left = find_child_by_field(n, "left")
            if left is None:  # pragma: no cover - tree-sitter guarantees left field
                continue
            # The left side is an expression_list with one child
            outer_idx = None
            for child in left.children:
                if child.type == "index_expression":
                    outer_idx = child
                    break
            if outer_idx is None:
                continue

            # outer_idx should contain an inner index_expression and a string
            inner_idx = None
            route_path_node = None
            for child in outer_idx.children:
                if child.type == "index_expression":
                    inner_idx = child
                elif child.type == "interpreted_string_literal":
                    route_path_node = child

            if inner_idx is None or route_path_node is None:  # pragma: no cover - structural
                continue

            # inner_idx should contain a string literal with HTTP method
            method_node = None
            for child in inner_idx.children:
                if child.type == "interpreted_string_literal":
                    method_node = child

            if method_node is None:  # pragma: no cover - structural
                continue

            method_str = node_text(method_node, source).strip('"')
            route_path = node_text(route_path_node, source).strip('"')

            # Validate: method must be a known HTTP method, path must start with /
            if method_str.upper() not in {
                "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
            }:
                continue
            if not route_path.startswith("/"):
                continue

            # Extract handler from the RHS call expression
            right = find_child_by_field(n, "right")
            handler_name = None
            handler_field: str | None = None
            if right is not None:
                for child in right.children:
                    if child.type == "call_expression":
                        func_node = find_child_by_field(child, "function")
                        if func_node:
                            handler_name = node_text(func_node, source)
                        # Extract handler_field from constructor args:
                        # alert.NewGetAlerts(o.context, o.AlertGetAlertsHandler)
                        # The last selector_expression arg whose field ends
                        # with "Handler" is the handler interface field.
                        call_args = find_child_by_field(child, "arguments")
                        if call_args:
                            for arg in call_args.children:
                                if arg.type == "selector_expression":
                                    fld = find_child_by_field(arg, "field")
                                    if fld:
                                        fld_text = node_text(fld, source)
                                        if fld_text.endswith("Handler"):
                                            handler_field = fld_text
                        break
                    elif child.type in ("identifier", "selector_expression"):
                        handler_name = node_text(child, source)
                        break

            if handler_name is None:
                continue

            normalized_method = method_str.upper()
            start_line = n.start_point[0] + 1
            end_line = n.end_point[0] + 1

            meta: dict[str, str] = {"handler_name": handler_name}
            if handler_field:
                meta["handler_field"] = handler_field

            route_sym = make_route_symbol(
                language="go",
                path=str(file_path),
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=n.start_point[1],
                    end_col=n.end_point[1],
                ),
                method=normalized_method,
                route_path=route_path,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                extra_meta=meta,
            )
            routes.append(route_sym)

    return (routes, mount_edges)


def _extract_go_swagger_wiring(
    node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract go-swagger handler wiring from assignment statements.

    Detects the pattern where a go-swagger typed handler interface field is
    assigned a HandlerFunc wrapper around the actual implementation method::

        openAPI.AlertGetAlertsHandler = alert_ops.GetAlertsHandlerFunc(api.getAlertsHandler)

    AST structure::

        assignment_statement
          left: expression_list
            selector_expression     <- openAPI.AlertGetAlertsHandler
          right: expression_list
            call_expression         <- alert_ops.GetAlertsHandlerFunc(api.getAlertsHandler)

    The call's function name must end with ``HandlerFunc`` to match.  The
    first non-context argument to the call is the actual handler implementation.

    Returns:
        Mapping from handler field name (e.g. ``AlertGetAlertsHandler``) to
        the actual handler implementation name (e.g. ``api.getAlertsHandler``).
    """
    wiring: dict[str, str] = {}

    for n in iter_tree(node):
        if n.type != "assignment_statement":
            continue

        # LHS: selector_expression with field ending in "Handler"
        left = find_child_by_field(n, "left")
        if left is None:  # pragma: no cover - tree-sitter guarantees left field
            continue

        lhs_selector = None
        for child in left.children:
            if child.type == "selector_expression":
                lhs_selector = child
                break
        if lhs_selector is None:
            continue

        lhs_field = find_child_by_field(lhs_selector, "field")
        if lhs_field is None:  # pragma: no cover - structural
            continue
        field_name = node_text(lhs_field, source)
        if not field_name.endswith("Handler"):
            continue

        # RHS: call_expression whose function ends with "HandlerFunc"
        right = find_child_by_field(n, "right")
        if right is None:  # pragma: no cover - tree-sitter guarantees right field
            continue

        call_expr = None
        for child in right.children:
            if child.type == "call_expression":
                call_expr = child
                break
        if call_expr is None:  # pragma: no cover - structural
            continue

        func_node = find_child_by_field(call_expr, "function")
        if func_node is None:  # pragma: no cover - structural
            continue
        func_name = node_text(func_node, source)
        if not func_name.endswith("HandlerFunc"):
            continue

        # Extract the actual handler from the call args — last non-literal
        # argument (skip context params which are typically identifiers
        # matching "ctx" or "context").
        call_args = find_child_by_field(call_expr, "arguments")
        if call_args is None:  # pragma: no cover - structural
            continue

        impl_name: str | None = None
        for arg in call_args.children:
            if arg.type in ("(", ")", ","):
                continue
            if arg.type in ("selector_expression", "identifier"):
                impl_name = node_text(arg, source)

        if impl_name:
            wiring[field_name] = impl_name

    return wiring


def _extract_go_usage_contexts(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
) -> list[UsageContext]:
    """Extract UsageContext records for Go web framework route calls.

    Creates UsageContext records that capture how handler functions are used
    in Gin/Echo/Chi/Fiber route registration calls. These are matched against
    YAML patterns in the enrichment phase.

    Supported patterns:
    - Gin: r.GET("/path", handler), router.POST("/users", createUser)
    - Echo: e.GET("/path", handler), echo.POST("/users", createUser)
    - Chi: r.Get("/path", handler), router.Post("/users", createUser)
    - Fiber: app.Get("/path", handler), app.Post("/users", createUser)

    Args:
        node: The root tree-sitter node
        source: Source file bytes
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file

    Returns:
        List of UsageContext records for Go route patterns.
    """
    contexts: list[UsageContext] = []
    group_prefix_map = _build_group_prefix_map(node, source)

    for n in iter_tree(node):
        if n.type != "call_expression":
            continue

        func_node = find_child_by_field(n, "function")
        if not func_node or func_node.type != "selector_expression":
            continue

        # Get the method name (e.g., GET, POST, Get, Post)
        field_node = find_child_by_field(func_node, "field")
        if not field_node:  # pragma: no cover
            continue

        method_name = node_text(field_node, source)

        # Cobra AddCommand() detection
        if method_name == "AddCommand":
            operand_node = find_child_by_field(func_node, "operand")
            parent_name = node_text(operand_node, source) if operand_node else None
            args_node = find_child_by_field(n, "arguments")
            if args_node and parent_name:
                for arg in args_node.children:
                    if arg.type == "identifier":
                        child_name = node_text(arg, source)
                        ctx = UsageContext.create(
                            kind="call",
                            context_name=f"{parent_name}.AddCommand",
                            position="args[0]",
                            path=str(file_path),
                            span=Span(
                                start_line=n.start_point[0] + 1,
                                end_line=n.end_point[0] + 1,
                                start_col=n.start_point[1],
                                end_col=n.end_point[1],
                            ),
                            metadata={
                                "parent_command": parent_name,
                                "child_command": child_name,
                            },
                        )
                        contexts.append(ctx)
            continue

        if method_name not in GO_HTTP_METHODS:
            continue

        # Get the receiver name (e.g., r, router, e, echo, app)
        operand_node = find_child_by_field(func_node, "operand")
        receiver_name = node_text(operand_node, source) if operand_node else None

        # Extract arguments
        args_node = find_child_by_field(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        handler_name = None

        # Extract route path (first string literal)
        for arg in args_node.children:
            if arg.type == "interpreted_string_literal":
                content_node = find_child_by_type(arg, "interpreted_string_literal_content")
                if content_node:
                    route_path = node_text(content_node, source)
                else:  # pragma: no cover
                    route_path = node_text(arg, source).strip('"')
                break

        # Handler is the LAST identifier/selector arg (middleware precedes it).
        # If only a func_literal (anonymous closure) is found, use "<closure>".
        if route_path is not None:
            closure_fallback = False
            for arg in reversed(args_node.children):
                if arg.type == "identifier":
                    handler_name = node_text(arg, source)
                    break
                elif arg.type == "selector_expression":
                    handler_name = node_text(arg, source)
                    break
                elif arg.type == "func_literal":
                    closure_fallback = True
            if handler_name is None and closure_fallback:
                handler_name = "<closure>"

        if not route_path:  # pragma: no cover
            continue

        # Skip single-arg calls like cache.Get("/key"), field.Tag.Get("/metric"),
        # Header.Get("/Authorization") — these are not route registrations.
        # Real route registrations always have at least a handler argument.
        if handler_name is None:
            continue

        # Try to resolve handler to a symbol reference
        handler_ref = None
        if handler_name and handler_name in symbol_by_name:
            handler_ref = symbol_by_name[handler_name].id

        # Prepend Group/Route prefix (closure-based or variable-based)
        prefix = _get_go_route_prefix(n, source)
        if not prefix and operand_node is not None and operand_node.type == "identifier":
            prefix = group_prefix_map.get(
                node_text(operand_node, source), "",
            )
        route_path = prefix + route_path

        # Normalize method name (handles aliases like Del → DELETE)
        normalized_method = _GO_METHOD_ALIASES.get(
            method_name, method_name.upper(),
        )

        # Build full call name (e.g., "r.GET", "router.Post")
        call_name = f"{receiver_name}.{method_name}" if receiver_name else method_name

        # Normalize route path
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=call_name,
            position="args[last]",  # Handler is typically last argument
            path=str(file_path),
            span=span,
            symbol_ref=handler_ref,
            metadata={
                "route_path": normalized_path,
                "http_method": normalized_method,
                "handler_name": handler_name,
                "receiver": receiver_name,
            },
        )
        contexts.append(ctx)

    return contexts


@register_analyzer("go", priority=50)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    """Analyze all Go files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-go is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root, max_files=max_files)


def _analyze_go_impl(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    """Internal implementation of Go analysis.

    Called by GoTreeSitterAnalyzer.analyze() after grammar availability
    has been checked by the base class.
    """
    if not _analyzer._check_grammar_available():
        warnings.warn(
            "go analysis skipped: grammar not available. "
            "Install the required tree-sitter grammar package.",
            UserWarning,
            stacklevel=3,
        )
        return AnalysisResult(
            skipped=True,
            skip_reason="go tree-sitter grammar not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-go
    try:
        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return AnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Go parser: {e}",
        )

    # Read go.mod for module path — used to strip module prefix from
    # import paths so that the ListNameResolver's suffix matching
    # operates on repo-relative paths (e.g., "pkg/log" instead of
    # "github.com/aquasecurity/trivy/pkg/log").
    go_module_path = _read_go_module_path(repo_root)

    # Parse go.mod dependencies for tier classification of boundary nodes
    # (WI-vovuk). Direct deps → tier 2, indirect/stdlib → tier 3.
    go_dep_manifest = parse_go_mod_dependencies(repo_root)

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    files_processed = 0

    for go_file in find_go_files(repo_root):
        if max_files is not None and files_processed >= max_files:  # pragma: no cover
            break
        # WI-bokab (v7): compute the file-identity anchor from the REPO-RELATIVE
        # path. ``go_file`` is absolute (``find_go_files`` joins onto ``repo_root``),
        # so folding ``str(go_file)`` directly would make stable_ids
        # location-dependent — the regression the WI-bokab smoke test guards.
        rel_path = str(go_file.relative_to(repo_root))
        file_stable_id = make_file_stable_id("go", normalize_path(rel_path))
        analysis = _extract_symbols_from_file(go_file, parser, run, file_stable_id)
        if analysis.symbols:
            file_analyses[go_file] = analysis
            files_processed += 1
        else:
            files_skipped += 1

    # Build global symbol registry - store ALL symbols with same name
    # This enables disambiguation using import paths
    global_symbols: dict[str, list[Symbol]] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
            if short_name not in global_symbols:
                global_symbols[short_name] = []
            global_symbols[short_name].append(symbol)
            if symbol.name != short_name:
                if symbol.name not in global_symbols:
                    global_symbols[symbol.name] = []
                global_symbols[symbol.name].append(symbol)

    # Aggregate field type registry from all files for chained field
    # access resolution (e.g., r.integration.Notify() → Integration.Notify).
    field_type_registry: dict[str, dict[str, str]] = {}
    for analysis in file_analyses.values():
        for class_name, fields in analysis.class_field_types.items():
            if class_name not in field_type_registry:
                field_type_registry[class_name] = {}
            for fname, ftype in fields.items():
                field_type_registry[class_name].setdefault(fname, ftype)

    # WI-kuroj: aggregate return-type registry across all files for
    # chained method call resolution (e.g. x := e.Query() → x has
    # type Result if Engine.Query returns Result).  First writer wins
    # (same convention as field_type_registry).
    method_return_type_registry: dict[str, str] = {}
    for analysis in file_analyses.values():
        for key, ret_type in analysis.method_return_types.items():
            method_return_type_registry.setdefault(key, ret_type)

    # Aggregate interface_method_sets across all files for the ambiguity
    # guard's interface-dispatch preference (go.py ambiguity guard).
    all_interface_method_sets: dict[str, set[tuple[str, int, int]]] = {}
    for analysis in file_analyses.values():
        all_interface_method_sets.update(analysis.interface_method_sets)

    # Pass 2: Extract edges, routes, and usage contexts
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    all_route_syms: list[Symbol] = []
    # go-swagger handler wiring: field name -> impl name (cross-file)
    handler_wiring: dict[str, str] = {}

    for go_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            go_file, parser, analysis.symbol_by_name, global_symbols, run,
            analysis.import_aliases, module_path=go_module_path,
            field_type_registry=field_type_registry,
            interface_method_sets=all_interface_method_sets,
            method_return_type_registry=method_return_type_registry,
            dot_imports=analysis.dot_imports,
        )

        # ADR-0015 Tier 1: annotate call edges with dataflow access modes
        try:
            source = go_file.read_bytes()
            tree = parser.parse(source)
            _go_df_config = _get_dataflow_config("go")
            if _go_df_config is not None:
                edges = _annotate_dataflow(edges, tree, source, _go_df_config)
        except (OSError, IOError):  # pragma: no cover
            pass  # Annotation is best-effort; edges are still valid without it
        all_edges.extend(edges)

        # Extract web framework routes (Gin, Echo, Fiber, Gorilla mux)
        try:
            source = go_file.read_bytes()
            tree = parser.parse(source)
            # WI-bokab (v7): same repo-relative anchor as Pass 1 (``go_file``
            # is absolute), threaded into closure-wrapper Symbol stable_ids.
            route_file_stable_id = make_file_stable_id(
                "go", normalize_path(str(go_file.relative_to(repo_root))),
            )
            route_syms, route_mount_edges = _extract_go_routes(
                tree.root_node, source, go_file, run,
                local_symbols=analysis.symbol_by_name,
                file_stable_id=route_file_stable_id,
            )
            all_route_syms.extend(route_syms)
            all_edges.extend(route_mount_edges)

            # Extract go-swagger handler wiring (field -> implementation)
            wiring = _extract_go_swagger_wiring(tree.root_node, source)
            handler_wiring.update(wiring)

            # Extract usage contexts for YAML pattern matching (v1.1.x)
            usage_contexts = _extract_go_usage_contexts(
                tree.root_node, source, go_file, analysis.symbol_by_name
            )
            all_usage_contexts.extend(usage_contexts)
        except (OSError, IOError):  # pragma: no cover
            pass  # Skip files that can't be read

    # Post-process: resolve go-swagger handler wiring across files.
    # Routes from the handler cache have handler_field metadata (e.g.
    # "AlertGetAlertsHandler").  The wiring mapping connects those field
    # names to actual implementation methods (e.g. "api.getAlertsHandler").
    if handler_wiring:
        for route in all_route_syms:
            field = (route.meta or {}).get("handler_field")
            if field and field in handler_wiring:
                route.meta["handler_name"] = handler_wiring[field]
                route.name = handler_wiring[field]

    all_symbols.extend(all_route_syms)

    # Cross-file structural interface matching: match each struct
    # against interfaces defined in other files.  Per-file matching
    # already happened in _extract_symbols_from_file; this pass catches
    # cross-file relationships (e.g., interface in notify/notify.go,
    # implementing struct in notify/slack/slack.go).
    #
    # CRITICAL: do NOT key struct method sets or struct symbols by short
    # name across files.  Many Go packages declare a type with the same
    # short name (e.g., 18 packages in alertmanager declare
    # ``type Notifier struct``).  Aggregating them globally loses the
    # per-package association and causes only the first struct to
    # receive the ``base_classes`` annotation.  Iterate per *package*
    # (i.e., per directory) instead — Go packages are exactly one
    # directory of files, so two structs of the same name in different
    # directories belong to different packages and must not share
    # methods, while two struct method declarations in different files
    # of the *same* directory belong to the same package and MUST be
    # aggregated (WI-hobuk).
    global_iface_methods: dict[str, set[tuple[str, int, int]]] = {}
    for analysis in file_analyses.values():
        for iname, imethods in analysis.interface_method_sets.items():
            # First definition wins (interfaces with the same short name
            # in different packages would need package qualification,
            # which is out of scope here).
            if iname not in global_iface_methods:
                global_iface_methods[iname] = imethods

    if global_iface_methods:
        # Group file_analyses by package directory.  In Go, all files
        # in a single directory belong to the same package, so the
        # parent directory is the correct aggregation key.  Using the
        # directory rather than the file path lets methods split across
        # sibling files (e.g., service.go + service_helpers.go) merge
        # into one effective method set per struct, while keeping
        # cross-package shadows (foo/service.go vs bar/service.go)
        # disjoint.
        packages: dict[str, list] = {}
        for fpath, analysis in file_analyses.items():
            pkg_dir = os.path.dirname(fpath)
            packages.setdefault(pkg_dir, []).append(analysis)

        for pkg_analyses in packages.values():
            # Merge struct method sets across all files in this package.
            # If two files contribute methods for the same struct name,
            # union their method tuples — methods are keyed by
            # (name, in_arity, out_arity) so duplicates collapse cleanly.
            pkg_struct_methods: dict[
                str, set[tuple[str, int, int]]
            ] = {}
            for analysis in pkg_analyses:
                for sname, smethods in analysis.struct_method_sets.items():
                    pkg_struct_methods.setdefault(sname, set()).update(
                        smethods,
                    )

            if not pkg_struct_methods:
                continue

            # Build a per-package struct symbol lookup so we annotate
            # whichever file actually contains the struct's type
            # declaration, regardless of which sibling files supplied
            # the methods.
            pkg_struct_syms: dict[str, Symbol] = {}
            for analysis in pkg_analyses:
                for s in analysis.symbols:
                    if s.kind == "struct" and s.name not in pkg_struct_syms:
                        pkg_struct_syms[s.name] = s

            for struct_name, struct_methods in pkg_struct_methods.items():
                struct_sym = pkg_struct_syms.get(struct_name)
                if struct_sym is None:
                    # Methods defined in this package but no
                    # corresponding type declaration found.  Either the
                    # struct lives in a generated file we skipped or
                    # the receiver type is misspelled — either way we
                    # cannot annotate a non-existent symbol.
                    continue  # pragma: no cover
                for iface_name, iface_methods in global_iface_methods.items():
                    if not iface_methods.issubset(struct_methods):
                        continue
                    if struct_sym.meta is None:
                        struct_sym.meta = {}
                    existing = struct_sym.meta.get("base_classes", [])
                    if iface_name not in existing:
                        struct_sym.meta.setdefault("base_classes", []).append(
                            iface_name,
                        )

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    # WI-potun: emit build_tag_alternative_of edges for symbols that have
    # the same qualified name but live in different build-tag-gated files.
    # This links e.g. Labels.Get in labels_stringlabels.go to Labels.Get in
    # labels_dedupelabels.go, unifying centrality and letting slice/explain
    # surface "this symbol has build-tag alternates".
    _build_tag_syms: dict[str, list[Symbol]] = {}
    for sym in all_symbols:
        bc = (sym.meta or {}).get("build_constraint")
        if bc and sym.kind in ("method", "function", "struct", "interface", "type"):
            # Group by (package_dir, name) to match within-package alternates
            pkg_dir = sym.path.rsplit("/", 1)[0] if "/" in sym.path else ""
            key = (pkg_dir, sym.name)
            _build_tag_syms.setdefault(key, []).append(sym)

    for (_pkg_dir, _name), syms in _build_tag_syms.items():
        if len(syms) < 2:
            continue
        # Emit edges between all pairs (undirected — use sorted IDs for
        # deterministic dedup)
        seen_pairs: set[tuple[str, str]] = set()
        for i, a in enumerate(syms):
            for b in syms[i + 1:]:
                pair = (min(a.id, b.id), max(a.id, b.id))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    all_edges.append(Edge.create(
                        src=a.id,
                        dst=b.id,
                        edge_type="references",
                        line=a.span.start_line if a.span else 0,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        confidence=0.95,
                        meta={"ref_construct": "build_tag_alternative"},
                    ))

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
        dependency_manifest=go_dep_manifest if go_dep_manifest.entries else None,
    )
