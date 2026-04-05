# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go analysis pass using tree-sitter-go.

This analyzer uses tree-sitter to parse Go files and extract:
- Function declarations (func)
- Method declarations (func with receiver)
- Struct declarations (type X struct)
- Interface declarations (type X interface)
- Package-level var aliases (var Name = expr) as variable symbols
- Function call relationships
- Function references in struct literal fields (cobra, http dispatch)
- Import relationships (import statements)
- Web framework routes (Gin, Echo, Fiber, Gorilla mux)

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
     edge with ``evidence_type="ambiguous_method_call"`` instead of picking
     an arbitrary candidate (which would produce a false-positive call edge)
7. Stdlib interface method guard:
   - When a method call ``x.Lock()`` has no inferred receiver type and the
     method name matches a well-known Go stdlib interface method (Lock, Close,
     Read, Write, String, Error, etc.), creates an unresolved edge with
     ``evidence_type="stdlib_method_call"`` even if only 1 candidate exists
     in the repo. This prevents ``sync.Mutex.Lock()`` calls from resolving
     to ``DirLocker.Lock`` (the only repo candidate), which would give
     DirLocker.Lock 255+ false in-degree edges.
4. Route detection:
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
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
    find_child_by_field,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_route_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
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
# 1. UsageContext records — matched by YAML patterns (ADR-0003 v1.1.x) to enrich
#    handler symbols with concept: route metadata.
# 2. Route symbols (kind="route") — consumed by the route_handler linker to
#    create routes_to edges. See py.py docstring for full architecture notes.
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
    if node.type not in ("function_declaration", "method_declaration"):
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


def _process_import_spec(
    spec: "tree_sitter.Node",
    source: bytes,
    aliases: dict[str, str],
) -> None:
    """Process a single import_spec node and add to aliases dict."""
    path_node = find_child_by_field(spec, "path")
    if not path_node:
        return  # pragma: no cover - defensive for malformed AST

    import_path = node_text(path_node, source).strip('"')

    # Check for explicit alias
    name_node = find_child_by_field(spec, "name")
    if name_node:
        alias = node_text(name_node, source)
        if alias != "_" and alias != ".":  # Ignore blank and dot imports
            aliases[alias] = import_path
    else:
        # No explicit alias - use last component of path
        # e.g., "github.com/foo/bar" -> "bar"
        alias = import_path.rsplit("/", 1)[-1]
        aliases[alias] = import_path


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


def _extract_interface_methods(
    interface_type_node: "tree_sitter.Node",
    source: bytes,
) -> set[str]:
    """Extract method names from a Go interface definition.

    Go interfaces list method specifications (name + signature) in their body.
    This function extracts just the method names, used for structural interface
    matching: a struct satisfies an interface if it has all the interface's
    methods.

    Example::

        type Notifier interface {
            Notify(msg string) error    // → {"Notify"}
            Close() error               // → {"Notify", "Close"}
        }

    Embedded interfaces (e.g., ``io.Reader`` inside another interface) are
    skipped — we only match on explicitly declared methods in this interface.
    """
    methods: set[str] = set()
    for child in interface_type_node.children:
        if child.type == "method_elem":
            # method_elem has a field_identifier child for the method name
            name_node = find_child_by_type(child, "field_identifier")
            if name_node:
                methods.add(node_text(name_node, source))
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
) -> FileAnalysis:
    """Extract symbols from a single Go file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    # Extract import aliases for this file (used later in edge extraction)
    analysis.import_aliases = _extract_import_aliases(tree.root_node, source)

    # Collect interface-implementation assertions: struct_name -> [interface_names]
    # Populated during tree walk, applied to struct symbols after extraction.
    impl_assertions: dict[str, list[str]] = {}

    # For structural interface matching (Go implicit satisfaction):
    # interface_name -> set of method names defined in the interface body
    interface_method_sets: dict[str, set[str]] = {}
    # struct_name -> set of method names from method_declaration receivers
    struct_method_sets: dict[str, set[str]] = {}

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
                    modifiers=modifiers,
                    lines_of_code=end_line - start_line + 1,
                    shape_id=_analyzer.compute_shape_id(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

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
                    modifiers=modifiers,
                    lines_of_code=end_line - start_line + 1,
                    shape_id=_analyzer.compute_shape_id(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

                # Track struct method sets for structural interface matching
                if receiver_type:
                    if receiver_type not in struct_method_sets:
                        struct_method_sets[receiver_type] = set()
                    struct_method_sets[receiver_type].add(method_name)

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
                                    lines_of_code=1,
                                    shape_id=_analyzer.compute_shape_id(iface_child),
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
                            lines_of_code=end_line - start_line + 1,
                            shape_id=_analyzer.compute_shape_id(child),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[type_name] = symbol

        # var declarations: interface assertions AND package-level aliases
        elif node.type == "var_declaration":
            for child in node.children:
                if child.type == "var_spec":
                    _detect_interface_assertion(child, source, impl_assertions)

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
                        lines_of_code=end_line - start_line + 1,
                        shape_id=_analyzer.compute_shape_id(child),
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

    Only the first assignment to a variable within a function wins
    (single-assignment SSA assumption per function scope). Built-in types
    (string, int, etc.) are excluded because they don't correspond to
    user-defined methods.

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
    node = rhs_node
    # Unwrap expression_list
    if node.type == "expression_list":
        for child in node.children:
            if child.type in (
                "unary_expression", "composite_literal", "call_expression",
            ):
                node = child
                break
        else:
            return None

    # &Server{} → unary_expression with & operator
    if node.type == "unary_expression":
        for child in node.children:
            if child.type == "composite_literal":
                node = child
                break
        else:
            return None  # pragma: no cover - defensive for unrecognized unary

    # Server{} → composite_literal with type_identifier
    if node.type == "composite_literal":
        for child in node.children:
            if child.type == "type_identifier":
                return node_text(child, source)

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
    package-qualified calls: ``pkg.NewFoo()`` → ``Foo``.

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
        if field is not None:
            name = node_text(field, source)
            if name.startswith("New") and len(name) > 3 and name[3].isupper():
                return name[3:]

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
                        confidence=0.70,
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
                            confidence=0.70,
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


def _resolve_field_chain(
    operand_node: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
    field_type_registry: dict[str, dict[str, str]],
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
        fields = field_type_registry.get(current_type)
        if fields is None:
            return None
        current_type = fields.get(field_name)
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
    interface_method_sets: dict[str, set[str]] | None = None,
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

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = make_file_id("go", str(file_path))

    # Extract function-scoped variable-to-type bindings for receiver disambiguation
    scoped_var_types = _extract_go_var_types(tree.root_node, source)

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
                            confidence=0.95,
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
                                    confidence=0.95,
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
                                qualified_name = f"{receiver_type}.{callee_name}"
                                # Try qualified name in local or global symbols
                                if qualified_name in local_symbols:
                                    callee = local_symbols[qualified_name]
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="typed_receiver_call",
                                        confidence=0.85,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
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
                                            evidence_type="typed_receiver_call",
                                            confidence=0.85,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                        ))
                                        callee_name = None  # Already resolved
                                # Fallback: if receiver_type is a qualified
                                # type like "http.Client", extract the package
                                # prefix and recover the import path.  This
                                # sets import_path_hint so the unresolved edge
                                # gets the correct module hint (e.g. net/http)
                                # instead of "external", enabling IO boundary
                                # detection to classify the call.
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
                            )
                            if resolved_type:
                                qualified_name = f"{resolved_type}.{callee_name}"
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
                                        evidence_type="typed_field_call",
                                        confidence=0.88,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
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
                                evidence_type="chained_call_unresolved",
                                confidence=0.40,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
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
                            # Check if any candidate is an interface method
                            _iface_candidate = None
                            for _cand in global_symbols[callee_name]:
                                # Interface method symbols are named
                                # InterfaceName.MethodName; check if InterfaceName
                                # is in interface_method_sets.
                                if "." in _cand.name:
                                    _iface_name = _cand.name.rsplit(".", 1)[0]
                                    if _iface_name in interface_method_sets:
                                        _iface_candidate = _cand
                                        break

                            if _iface_candidate is not None:
                                # Resolve to the interface method instead of
                                # unresolved — dispatches_to edges will route
                                # the slice to concrete implementations.
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=_iface_candidate.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="interface_dispatch",
                                    confidence=0.75,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                                callee_name = None  # Already handled
                            else:
                                dst_id = f"go:external:0-0:{callee_name}:unresolved"
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ambiguous_method_call",
                                    confidence=0.50,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
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
                                evidence_type="unexported_method_call",
                                confidence=0.40,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
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
                                evidence_type="stdlib_method_call",
                                confidence=0.45,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
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
                                evidence_type="function_call",
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                        # Check global symbols with disambiguation via ListNameResolver
                        else:
                            lookup_result = resolver.lookup(callee_name, path_hint=import_path_hint)
                            if lookup_result.found:
                                # Scale base confidence by resolver's confidence multiplier
                                edge_confidence = 0.80 * lookup_result.confidence
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="function_call",
                                    confidence=edge_confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
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
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="unresolved_method_call",
                                    confidence=0.50,  # Lower confidence for unresolved
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
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
                                    confidence=0.70,
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


def _extract_go_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    local_symbols: dict[str, Symbol] | None = None,
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

    Returns:
        Tuple of (route symbols, mount edges).
    """
    routes: list[Symbol] = []
    mount_edges: list[Edge] = []

    # Pre-pass: build variable-to-prefix map for Gin/Echo/Fiber groups
    group_prefix_map = _build_group_prefix_map(node, source)

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

                                route_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path), start_line, end_line,
                                        f"{normalized_method} {route_path}", "route"
                                    ),
                                    stable_id=make_route_stable_id(normalized_method, route_path),
                                    name=handler_name,
                                    kind="route",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={
                                        "route_path": route_path,
                                        "http_method": normalized_method,
                                        "handler_name": handler_name,
                                    },
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

                                route_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path), start_line, end_line,
                                        f"{handle_http_method} {route_path}", "route"
                                    ),
                                    stable_id=make_route_stable_id(
                                        handle_http_method, route_path,
                                    ),
                                    name=handler_name,
                                    kind="route",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={
                                        "route_path": route_path,
                                        "http_method": handle_http_method,
                                        "handler_name": handler_name,
                                    },
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

                            route_sym = Symbol(
                                id=make_symbol_id(
                                    "go", str(file_path), start_line, end_line,
                                    f"{normalized_method} {route_path}", "route"
                                ),
                                stable_id=make_route_stable_id(normalized_method, route_path),
                                name=handler_name,
                                kind="route",
                                language="go",
                                path=str(file_path),
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=n.start_point[1],
                                    end_col=n.end_point[1],
                                ),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={
                                    "route_path": route_path,
                                    "http_method": normalized_method,
                                    "handler_name": handler_name,
                                },
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

                                    mount_sym = Symbol(
                                        id=make_symbol_id(
                                            "go", str(file_path), start_line,
                                            end_line,
                                            f"MOUNT {mount_prefix}",
                                            "route_mount",
                                        ),
                                        stable_id=make_route_stable_id("MOUNT", mount_prefix),
                                        name=handler_ref,
                                        kind="route_mount",
                                        language="go",
                                        path=str(file_path),
                                        span=Span(
                                            start_line=start_line,
                                            end_line=end_line,
                                            start_col=n.start_point[1],
                                            end_col=n.end_point[1],
                                        ),
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        meta={
                                            "mount_prefix": mount_prefix,
                                            "handler_ref": handler_ref,
                                        },
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
                                            if handler_ref in local_symbols:
                                                dst_id = local_symbols[
                                                    handler_ref
                                                ].id
                                            else:
                                                dst_id = (
                                                    f"go:external:0-0:"
                                                    f"{handler_ref}:function"
                                                )
                                            mount_edges.append(Edge.create(
                                                src=enclosing.id,
                                                dst=dst_id,
                                                edge_type="calls",
                                                line=start_line,
                                                confidence=0.90,
                                                origin=PASS_ID,
                                                origin_run_id=(
                                                    run.execution_id
                                                ),
                                                evidence_type="route_mount",
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
            handler_name: str | None = None
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

            meta: dict[str, str] = {
                "route_path": route_path,
                "http_method": normalized_method,
                "handler_name": handler_name,
            }
            if handler_field:
                meta["handler_field"] = handler_field

            route_sym = Symbol(
                id=make_symbol_id(
                    "go", str(file_path), start_line, end_line,
                    f"{normalized_method} {route_path}", "route",
                ),
                stable_id=make_route_stable_id(normalized_method, route_path),
                name=handler_name,
                kind="route",
                language="go",
                path=str(file_path),
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=n.start_point[1],
                    end_col=n.end_point[1],
                ),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                meta=meta,
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

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    files_processed = 0

    for go_file in find_go_files(repo_root):
        if max_files is not None and files_processed >= max_files:  # pragma: no cover
            break
        analysis = _extract_symbols_from_file(go_file, parser, run)
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

    # Aggregate interface_method_sets across all files for the ambiguity
    # guard's interface-dispatch preference (go.py ambiguity guard).
    all_interface_method_sets: dict[str, set[str]] = {}
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
            route_syms, route_mount_edges = _extract_go_routes(
                tree.root_node, source, go_file, run,
                local_symbols=analysis.symbol_by_name,
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

    # Cross-file structural interface matching: aggregate method sets from
    # all files and match structs to interfaces defined in other files.
    # Per-file matching already happened in _extract_symbols_from_file;
    # this pass catches cross-file relationships (e.g., interface in one
    # file, implementing struct in another).
    global_iface_methods: dict[str, set[str]] = {}
    global_struct_methods: dict[str, set[str]] = {}
    for analysis in file_analyses.values():
        for iname, imethods in analysis.interface_method_sets.items():
            # First definition wins (interfaces with same name in different
            # packages would need package qualification, which is out of scope)
            if iname not in global_iface_methods:
                global_iface_methods[iname] = imethods
        for sname, smethods in analysis.struct_method_sets.items():
            # Merge method sets: methods can be defined across multiple files
            if sname in global_struct_methods:
                global_struct_methods[sname] = global_struct_methods[sname] | smethods
            else:
                global_struct_methods[sname] = set(smethods)

    if global_iface_methods and global_struct_methods:
        # Build a lookup of struct symbols for efficient updates
        struct_syms: dict[str, Symbol] = {}
        for sym in all_symbols:
            if sym.kind == "struct" and sym.name not in struct_syms:
                struct_syms[sym.name] = sym

        for struct_name, struct_methods in global_struct_methods.items():
            struct_sym = struct_syms.get(struct_name)
            if struct_sym is None:
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

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
