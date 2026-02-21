"""Ruby analysis pass using tree-sitter-ruby.

This analyzer uses tree-sitter to parse Ruby files and extract:
- Method definitions (def)
- Class declarations (class)
- Module declarations (module)
- Method call relationships
- Require/require_relative statements
- Rails callback edges (before_action, after_action, around_action)
- ActiveRecord association edges (has_many, belongs_to, has_one)
- Ruby delegate macro edges (delegate :method, to: :association)
- Receiver-type tracking for variable-receiver calls
- Method parameter filtering to prevent bare-identifier false positives

If tree-sitter with Ruby support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-ruby is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect method calls and require statements
5. Track variable types from constructor/factory calls (``var = Class.new``)
   to resolve ``var.method`` → ``Class#method`` (typed_receiver_call evidence)
6. Filter method parameters from bare-identifier handler to prevent
   false-positive edges from parameter references

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ruby package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Rust/Elixir/Java/PHP/C analyzers for consistency
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("ruby")

# HTTP methods for Rails/Sinatra route detection (used by UsageContext extraction)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "match"}

# Rails callback methods that create implicit call edges from class to method.
# Covers both controller callbacks (before_action) and model lifecycle callbacks
# (before_save, after_create, validate, etc.). Includes legacy Rails 3 API names.
RAILS_CALLBACK_METHODS = frozenset({
    # Controller callbacks
    "before_action", "after_action", "around_action",
    "before_filter", "after_filter", "around_filter",
    # Model lifecycle callbacks (ActiveRecord::Callbacks)
    "before_save", "after_save", "around_save",
    "before_create", "after_create", "around_create",
    "before_update", "after_update", "around_update",
    "before_destroy", "after_destroy", "around_destroy",
    "before_validation", "after_validation",
    "after_initialize", "after_find", "after_touch",
    # Transaction callbacks (ActiveRecord::Transactions)
    "after_commit", "after_rollback",
    "after_create_commit", "after_update_commit", "after_destroy_commit",
    "after_save_commit",
    # Custom validation method registration
    "validate",
})

# ActiveRecord association macros that create model-to-model relationships.
_ASSOCIATION_METHODS = frozenset({
    "has_many", "belongs_to", "has_one", "has_and_belongs_to_many",
})

# Directories that contain test files where get/post/delete are HTTP test helpers,
# not route definitions.
_TEST_DIR_NAMES = frozenset({"spec", "test", "tests", "features", "test-suite"})


def _is_test_directory(file_path: Path, repo_root: Path) -> bool:
    """Check if file is inside a test/spec directory.

    Returns True for files under spec/, test/, tests/, features/, test-suite/.
    These directories use HTTP method helpers (get, post, delete) for making
    test requests, NOT for defining routes.
    """
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return bool(_TEST_DIR_NAMES & set(rel.parts))


def _is_route_definition_file(file_path: Path, repo_root: Path) -> bool:
    """Check if file is a route definition file.

    Returns True for:
    - Files named routes.rb (e.g., config/routes.rb)
    - Files inside a routes/ directory (e.g., config/routes/api.rb)

    Rails routes are defined in config/routes.rb and optionally split into
    config/routes/*.rb partials. Matching by name/directory catches both
    standard and non-standard layouts.
    """
    if file_path.name == "routes.rb":
        return True
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return "routes" in rel.parts


def find_ruby_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Ruby files in the repository."""
    yield from find_files(repo_root, ["*.rb"])


def is_ruby_tree_sitter_available() -> bool:
    """Check if tree-sitter with Ruby grammar is available.

    Delegates to the RubyAnalyzer singleton (defined at module level below).
    This function is kept for backward compatibility.
    """
    return _analyzer._check_grammar_available()


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase.

    Examples:
        user_service -> UserService
        http_client -> HttpClient
        api -> Api
    """
    return "".join(word.capitalize() for word in name.split("_"))


def _extract_require_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract require/require_relative statements and infer class/module names.

    Ruby convention maps snake_case file paths to PascalCase class names.
    For example:
        require 'user_service' -> hints that UserService class comes from this path
        require 'math/calculator' -> hints that Calculator class comes from this path

    Returns a dict mapping inferred class/module names to their require paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name
        method_node = None
        for child in node.children:
            if child.type == "identifier":
                method_node = child
                break

        if not method_node:  # pragma: no cover - call nodes always have identifier
            continue

        callee_name = node_text(method_node, source)
        if callee_name not in ("require", "require_relative"):
            continue

        # Extract the require path from arguments
        args_node = _find_child_by_field(node, "arguments")
        if not args_node:  # pragma: no cover - require always has arguments
            continue

        for arg in args_node.children:
            if arg.type == "string":
                content_node = find_child_by_type(arg, "string_content")
                if content_node:
                    require_path = node_text(content_node, source)
                    # Extract the last component and convert to PascalCase
                    # 'math/calculator' -> 'calculator' -> 'Calculator'
                    basename = require_path.rsplit("/", 1)[-1]
                    # Remove .rb extension if present
                    if basename.endswith(".rb"):
                        basename = basename[:-3]
                    if basename:
                        class_name = _snake_to_pascal(basename)
                        hints[class_name] = require_path

    return hints


def _get_enclosing_class_or_module(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], str]:
    """Walk up the tree to find the enclosing class or module name.

    Returns (name, type) where type is 'class' or 'module'.
    """
    current = node.parent
    while current is not None:
        if current.type == "class":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source), "class"
        elif current.type == "module":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source), "module"
        current = current.parent
    return None, ""  # pragma: no cover - defensive


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method (instance or class method)."""
    current = node.parent
    while current is not None:
        if current.type in ("method", "singleton_method"):
            name_node = _find_child_by_field(current, "name")
            if name_node:
                method_name = node_text(name_node, source)
                if method_name in local_symbols:
                    return local_symbols[method_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_method_params(
    node: "tree_sitter.Node",
    source: bytes,
) -> set[str]:
    """Extract parameter names from the enclosing method definition.

    Walks up the tree to find the nearest ``method`` or ``singleton_method``
    node, then collects all ``identifier`` children of the ``method_parameters``
    child.  Handles simple params, keyword params, and splat params.

    Returns the set of bare parameter names (without ``*``, ``**``, ``&``, or
    ``:`` prefixes).
    """
    current = node.parent
    while current is not None:
        if current.type in ("method", "singleton_method"):
            params_node = find_child_by_type(current, "method_parameters")
            if params_node is None:
                return set()
            names: set[str] = set()
            for param in iter_tree(params_node):
                if param.type == "identifier":
                    names.add(node_text(param, source))
            return names
        current = current.parent
    return set()  # pragma: no cover - defensive


def _enclosing_class_from_method(method: Symbol) -> Optional[str]:
    """Extract the enclosing class/module name from a method's qualified name.

    Ruby method names are qualified as 'ClassName#method' (instance) or
    'ClassName.method' (class/module method). Returns None for top-level methods.
    """
    name = method.name
    if "#" in name:
        return name.split("#", 1)[0]
    if "." in name:
        return name.split(".", 1)[0]
    return None


def _symbol_belongs_to_class(symbol: Symbol, class_name: str) -> bool:
    """Check if a symbol belongs to the given class/module.

    Matches both instance methods (Class#method) and class methods (Class.method).
    """
    return (
        symbol.name.startswith(f"{class_name}#")
        or symbol.name.startswith(f"{class_name}.")
    )


def _resolve_bare_call_class_aware(
    callee_name: str,
    enclosing_class: Optional[str],
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Resolve a bare method call with class-aware priority.

    Priority cascade:
    1. Same-class qualified lookup: 'EnclosingClass#callee' or 'EnclosingClass.callee'
    2. Unqualified lookup: 'callee' in local_symbols (may match any class)

    Returns the resolved symbol, or None if not found in local_symbols.
    """
    if enclosing_class is not None:
        # Try instance method in same class
        qualified_instance = f"{enclosing_class}#{callee_name}"
        if qualified_instance in local_symbols:
            return local_symbols[qualified_instance]
        # Try class/module method in same class
        qualified_class = f"{enclosing_class}.{callee_name}"
        if qualified_class in local_symbols:
            return local_symbols[qualified_class]

    # Fall back to unqualified lookup
    if callee_name in local_symbols:
        return local_symbols[callee_name]

    return None


def _extract_ruby_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method node.

    Returns signature in format: (param, param2 = ..., keyword:, &block)
    Ruby is dynamically typed, so no type annotations are included.
    """
    params: list[str] = []

    # Find parameters node
    params_node = _find_child_by_field(node, "parameters")
    if params_node is None:
        return "()"

    for child in params_node.children:
        if child.type == "identifier":
            # Simple positional parameter
            params.append(node_text(child, source))
        elif child.type == "optional_parameter":
            # Parameter with default value: name = value
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"{param_name} = ...")
        elif child.type == "keyword_parameter":
            # Keyword parameter: name: or name: value
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                # Check if it has a default value (look for value node after identifier)
                has_value = False
                for pc in child.children:
                    # Skip the identifier and punctuation - look for an actual value
                    if pc.type not in ("identifier", ":"):
                        has_value = True
                        break
                if has_value:
                    params.append(f"{param_name}: ...")
                else:
                    params.append(f"{param_name}:")
        elif child.type == "splat_parameter":
            # *args
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"*{param_name}")
            else:
                params.append("*")  # pragma: no cover - bare splat
        elif child.type == "hash_splat_parameter":
            # **kwargs
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"**{param_name}")
            else:
                params.append("**")  # pragma: no cover - bare hash splat
        elif child.type == "block_parameter":
            # &block
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"&{param_name}")

    params_str = ", ".join(params)
    return f"({params_str})"


@dataclass
class _NamespaceBlock:
    """A Rails namespace or scope-module block with byte range and prefix."""

    start_byte: int
    end_byte: int
    prefix: str
    prefixes_path: bool  # True for namespace (prefixes URL), False for scope module:


@dataclass
class _ResourceBlock:
    """A Rails resources/resource block with byte range and resource info.

    Tracks do_block byte ranges so nested routes, member blocks, and
    collection blocks can determine their parent resource context.
    """

    start_byte: int
    end_byte: int
    resource_name: str  # e.g. "users", "posts"
    path_prefix: str  # e.g. "/users/:user_id" for nested resources
    controller_name: str  # e.g. "users", "admin/users"
    is_singular: bool  # True for 'resource', False for 'resources'


@dataclass
class _MemberCollectionBlock:
    """A Rails member/collection block inside a resource.

    member do ... end → routes at /:id/action
    collection do ... end → routes at /action
    """

    start_byte: int
    end_byte: int
    block_type: str  # "member" or "collection"
    resource_block: _ResourceBlock  # parent resource


def _find_namespace_blocks(
    root: "tree_sitter.Node",
    source: bytes,
) -> list[_NamespaceBlock]:
    """Pre-scan tree for Rails namespace and scope-module blocks.

    Finds ``namespace :name do ... end`` and ``scope module: :name do ... end``
    call nodes and records their byte ranges and prefix strings. Supports
    arbitrarily deep nesting by recursive descent.

    Returns:
        Flat list of _NamespaceBlock entries. Nesting is determined by
        containment of byte ranges at query time.
    """
    blocks: list[_NamespaceBlock] = []

    for n in iter_tree(root):
        if n.type != "call":
            continue

        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        if method_name == "namespace":
            # namespace :admin do ... end
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover - namespace always has arguments
                continue
            for arg in args_node.children:
                if arg.type == "simple_symbol":
                    ns_name = node_text(arg, source).strip(":")
                    # Find the do_block to get the byte range
                    for child in n.children:
                        if child.type in ("do_block", "block"):
                            blocks.append(_NamespaceBlock(
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                prefix=ns_name,
                                prefixes_path=True,
                            ))
                    break

        elif method_name == "scope":
            # scope module: :admin do ... end
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover - scope always has arguments
                continue
            for arg in args_node.children:
                if arg.type == "pair":
                    # Look for module: :name pattern
                    # pair children: hash_key_symbol("module"), :, simple_symbol(":admin")
                    pair_children = list(arg.children)
                    has_module_key = any(
                        c.type == "hash_key_symbol"
                        and node_text(c, source).strip(":") == "module"
                        for c in pair_children
                    )
                    if not has_module_key:
                        continue
                    # Find the value (simple_symbol after the key)
                    val_node = None
                    for c in pair_children:
                        if c.type == "simple_symbol":
                            text = node_text(c, source).strip(":")
                            if text != "module":
                                val_node = c
                                break
                    if val_node is not None:
                        mod_name = node_text(val_node, source).strip(":")
                        for child in n.children:
                            if child.type in ("do_block", "block"):
                                blocks.append(_NamespaceBlock(
                                    start_byte=child.start_byte,
                                    end_byte=child.end_byte,
                                    prefix=mod_name,
                                    prefixes_path=False,
                                ))
                        break

    return blocks


def _get_namespace_context(
    node: "tree_sitter.Node",
    ns_blocks: list[_NamespaceBlock],
) -> tuple[str, str]:
    """Determine the namespace prefix for a route call node.

    Checks which namespace blocks contain this node (by byte range) and
    accumulates their prefixes. Returns separate path_prefix and
    module_prefix since ``scope module:`` only affects the controller
    lookup, not the URL path.

    Returns:
        (path_prefix, module_prefix) where each is a slash-separated
        prefix string like "admin" or "api/v1", or "" if no namespace.
    """
    path_parts: list[str] = []
    module_parts: list[str] = []

    # Sort by start_byte to get outermost-first ordering
    containing = [
        b for b in ns_blocks
        if b.start_byte <= node.start_byte and node.end_byte <= b.end_byte
    ]
    containing.sort(key=lambda b: b.start_byte)

    for block in containing:
        module_parts.append(block.prefix)
        if block.prefixes_path:
            path_parts.append(block.prefix)

    return "/".join(path_parts), "/".join(module_parts)


def _find_resource_blocks(
    root: "tree_sitter.Node",
    source: bytes,
    ns_blocks: list[_NamespaceBlock],
) -> tuple[list[_ResourceBlock], list[_MemberCollectionBlock]]:
    """Pre-scan tree for Rails resource blocks with do_block children.

    Finds ``resources :name do ... end`` and ``resource :name do ... end``
    calls that contain nested route definitions. Also finds ``member do``
    and ``collection do`` blocks inside resource blocks.

    Returns:
        Tuple of (resource_blocks, member_collection_blocks).
    """
    resource_blocks: list[_ResourceBlock] = []
    mc_blocks: list[_MemberCollectionBlock] = []

    for n in iter_tree(root):
        if n.type != "call":
            continue

        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        if method_name in ("resources", "resource"):
            # Check for do_block
            do_block = None
            for child in n.children:
                if child.type in ("do_block", "block"):
                    do_block = child
                    break
            if do_block is None:
                continue

            # Extract resource name from arguments
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover
                continue
            resource_name = None
            for arg in args_node.children:
                if arg.type == "simple_symbol":
                    resource_name = node_text(arg, source).strip(":")
                    break
            if resource_name is None:  # pragma: no cover
                continue

            # Get namespace context for this resource
            ns_path_prefix, module_prefix = _get_namespace_context(n, ns_blocks)

            # Build path prefix: check if this resource is nested inside
            # another resource block. Use innermost (narrowest byte range).
            parent_path = ""
            innermost_rb = None
            for rb in resource_blocks:
                if rb.start_byte <= n.start_byte and n.end_byte <= rb.end_byte:
                    if innermost_rb is None or rb.start_byte > innermost_rb.start_byte:
                        innermost_rb = rb
            if innermost_rb is not None:
                parent_path = innermost_rb.path_prefix

            is_singular = method_name == "resource"
            if innermost_rb is not None:
                # Nested resource: include parent's :parent_id in path
                parent_id = f":{_singularize(innermost_rb.resource_name)}_id"
                path_prefix = f"{parent_path}/{parent_id}/{resource_name}"
            elif ns_path_prefix:
                path_prefix = f"/{ns_path_prefix}/{resource_name}"
            else:
                path_prefix = f"/{resource_name}"

            controller_name = (
                f"{module_prefix}/{resource_name}" if module_prefix else resource_name
            )

            resource_blocks.append(_ResourceBlock(
                start_byte=do_block.start_byte,
                end_byte=do_block.end_byte,
                resource_name=resource_name,
                path_prefix=path_prefix,
                controller_name=controller_name,
                is_singular=is_singular,
            ))

        elif method_name in ("member", "collection"):
            # member/collection blocks inside a resource
            do_block = None
            for child in n.children:
                if child.type in ("do_block", "block"):
                    do_block = child
                    break
            if do_block is None:  # pragma: no cover - member/collection always has block
                continue

            # Find the innermost enclosing resource block
            parent_rb = None
            for rb in resource_blocks:
                if rb.start_byte <= n.start_byte and n.end_byte <= rb.end_byte:
                    if parent_rb is None or rb.start_byte > parent_rb.start_byte:
                        parent_rb = rb
            if parent_rb is None:  # pragma: no cover - member/collection always inside resource
                continue

            mc_blocks.append(_MemberCollectionBlock(
                start_byte=do_block.start_byte,
                end_byte=do_block.end_byte,
                block_type=method_name,
                resource_block=parent_rb,
            ))

    return resource_blocks, mc_blocks


def _get_resource_context(
    node: "tree_sitter.Node",
    resource_blocks: list[_ResourceBlock],
    mc_blocks: list[_MemberCollectionBlock],
) -> tuple[str, str, str]:
    """Determine the resource context for a route call node.

    Checks if the node is inside a resource block's do_block (nested resource)
    or inside a member/collection block.

    Returns:
        (path_prefix, controller_name, context_type) where context_type is
        "nested", "member", "collection", or "" if no resource context.
    """
    # Check member/collection blocks first (more specific, innermost wins)
    innermost_mc = None
    for mc in mc_blocks:
        if mc.start_byte <= node.start_byte and node.end_byte <= mc.end_byte:
            if innermost_mc is None or mc.start_byte > innermost_mc.start_byte:
                innermost_mc = mc
    if innermost_mc is not None:
        return (
            innermost_mc.resource_block.path_prefix,
            innermost_mc.resource_block.controller_name,
            innermost_mc.block_type,
        )

    # Check resource blocks (for nested resources, innermost wins)
    innermost_rb = None
    for rb in resource_blocks:
        if rb.start_byte <= node.start_byte and node.end_byte <= rb.end_byte:
            if innermost_rb is None or rb.start_byte > innermost_rb.start_byte:
                innermost_rb = rb
    if innermost_rb is not None:
        parent_id = f":{_singularize(innermost_rb.resource_name)}_id"
        return f"{innermost_rb.path_prefix}/{parent_id}", "", "nested"

    return "", "", ""


def _singularize(name: str) -> str:
    """Naive English singularization for Rails resource param names.

    Converts plural resource names to singular for :resource_id params.
    E.g., "users" → "user", "posts" → "post", "categories" → "category".
    """
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes") or name.endswith("zes"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _extract_rails_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Rails/Sinatra route DSL calls.

    Detects patterns like:
    - Rails: get '/users', to: 'users#index'
    - Rails: post '/login', to: 'sessions#create'
    - Rails: resources :users, resource :profile
    - Rails: namespace :admin do resources :users end
    - Rails: scope module: :admin do resources :settings end
    - Rails: resources :users do resources :posts end (nested)
    - Rails: resources :users do member do post :activate end end
    - Rails: resources :users do collection do get :active end end
    - Sinatra: get '/path' do ... end
    - Sinatra: post '/users' do ... end

    Namespace DSL blocks (``namespace :admin do ... end``) prepend a module
    path to controller_action and URL path. ``scope module: :name`` only
    prefixes the controller lookup, not the URL path.

    Nested resources prepend parent path + :parent_id to child routes.
    Member blocks generate routes at /:id/action. Collection blocks
    generate routes at /action.

    The has_block metadata field distinguishes Sinatra (with block) from Rails (with to: option).

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols have kind="route" which matches rails.yaml symbol_kind pattern.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    # Pre-scan for namespace/scope-module blocks
    ns_blocks = _find_namespace_blocks(node, source)

    # Pre-scan for resource blocks and member/collection blocks
    resource_blocks, mc_blocks = _find_resource_blocks(node, source, ns_blocks)

    for n in iter_tree(node):
        if n.type != "call":
            continue

        # Get the method name
        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break

        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        # Check if it's an HTTP method route or resources
        if method_name not in HTTP_METHODS and method_name not in ("resources", "resource"):
            continue

        # Check resource context (nested resources, member/collection blocks)
        res_path_prefix, res_controller, res_context = _get_resource_context(
            n, resource_blocks, mc_blocks
        )

        # Extract route path from first argument
        args_node = _find_child_by_field(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        controller_action = None
        resource_name = None  # Original resource name before namespace prefix
        action_name = None  # For member/collection routes

        for arg in args_node.children:
            # String path for HTTP method routes
            if arg.type == "string" and method_name in HTTP_METHODS:
                content_node = find_child_by_type(arg, "string_content")
                if content_node:
                    route_path = node_text(content_node, source)
                    break
            # Symbol for resources/resource
            elif arg.type == "simple_symbol" and method_name in ("resources", "resource"):
                resource_name = node_text(arg, source).strip(":")
                route_path = resource_name
                break
            # Symbol for HTTP method inside member/collection block
            # e.g., member do; post :activate; end → action_name = "activate"
            elif (
                arg.type == "simple_symbol"
                and method_name in HTTP_METHODS
                and res_context in ("member", "collection")
            ):
                action_name = node_text(arg, source).strip(":")
                if res_context == "member":
                    route_path = f"{res_path_prefix}/:id/{action_name}"
                else:  # collection
                    route_path = f"{res_path_prefix}/{action_name}"
                controller_action = f"{res_controller}#{action_name}"
                break
            # Handle "path" => "controller#action" syntax (pair with string key)
            elif arg.type == "pair" and method_name in HTTP_METHODS:
                # Extract key (path) and value (controller#action) from pair
                pair_children = list(arg.children)
                if len(pair_children) >= 2:
                    key_node = pair_children[0]
                    val_node = pair_children[-1]  # Last child is value
                    if key_node.type == "string":
                        key_content = find_child_by_type(key_node, "string_content")
                        if key_content:
                            route_path = node_text(key_content, source)
                    if val_node.type == "string":
                        val_content = find_child_by_type(val_node, "string_content")
                        if val_content:
                            controller_action = node_text(val_content, source)
                if route_path:
                    break

        if not route_path:  # pragma: no cover
            continue

        # Try to extract controller#action from 'to:' option (e.g., match "path", to: "ctrl#act")
        for arg in args_node.children:
            if arg.type == "pair":
                for pair_child in arg.children:
                    if pair_child.type in ("hash_key_symbol", "simple_symbol"):
                        key_text = node_text(pair_child, source).strip(":")
                        if key_text == "to":
                            for sibling in arg.children:
                                if sibling.type == "string":
                                    content = find_child_by_type(sibling, "string_content")
                                    if content:
                                        controller_action = node_text(content, source)

        # Check for block (Sinatra style: get '/path' do ... end)
        has_block = False
        for child in n.children:
            if child.type in ("do_block", "block"):
                has_block = True
                break

        # Apply namespace prefix (e.g., namespace :admin do resources :users end)
        path_prefix, module_prefix = _get_namespace_context(n, ns_blocks)

        # For nested resources and member/collection routes, the resource
        # block's path_prefix already includes the namespace path. Skip
        # namespace path prefixing to avoid double-prefixing.
        if res_context in ("nested", "member", "collection"):
            # Resource nesting prefix already includes namespace path
            if res_context == "nested" and method_name in ("resources", "resource"):
                if route_path.startswith("/"):  # pragma: no cover - resource symbols don't start with /
                    route_path = f"{res_path_prefix}{route_path}"
                else:
                    route_path = f"{res_path_prefix}/{route_path}"
            # member/collection paths were already built in argument extraction
        else:
            # Standard namespace prefix (not inside a resource block)
            if path_prefix:
                if route_path.startswith("/"):
                    route_path = f"/{path_prefix}{route_path}"
                else:
                    route_path = f"{path_prefix}/{route_path}"

        # Prefix the controller_action for both namespace and scope module:
        if module_prefix and controller_action:
            controller_action = f"{module_prefix}/{controller_action}"

        # Build metadata
        http_method = method_name.upper() if method_name in HTTP_METHODS else "RESOURCES"
        metadata: dict[str, str | bool] = {
            "route_path": route_path,
            "http_method": http_method,
            "has_block": has_block,  # True for Sinatra-style, False for Rails-style
        }
        if controller_action:
            metadata["controller_action"] = controller_action
        elif method_name in ("resources", "resource") and resource_name is not None:
            # For resources :users, infer controller_action from resource name
            # Rails convention: resources :users → UsersController#index (primary entry)
            # This enables route-handler linking for resource routes
            if module_prefix:
                metadata["controller_action"] = f"{module_prefix}/{resource_name}#index"
            else:
                metadata["controller_action"] = f"{resource_name}#index"

        # Create span
        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        # Create UsageContext (for backwards compatibility)
        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,  # e.g., "get", "post", "resources"
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,  # Route DSL doesn't reference a handler symbol directly
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) (kind="route" matches rails.yaml pattern)
        # This enables route detection and entrypoint detection for Rails apps
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # For resources/resource, expand into all RESTful routes
        # This enables route-handler linking for all controller actions
        if method_name == "resources":
            # resources :users creates 7 RESTful routes
            restful_routes = [
                ("GET", normalized_path, "index"),  # GET /users
                ("GET", f"{normalized_path}/new", "new"),  # GET /users/new
                ("POST", normalized_path, "create"),  # POST /users
                ("GET", f"{normalized_path}/:id", "show"),  # GET /users/:id
                ("GET", f"{normalized_path}/:id/edit", "edit"),  # GET /users/:id/edit
                ("PATCH", f"{normalized_path}/:id", "update"),  # PATCH /users/:id
                ("DELETE", f"{normalized_path}/:id", "destroy"),  # DELETE /users/:id
            ]
            # Controller name from resource with namespace prefix
            # e.g., namespace :admin do resources :users end → admin/users
            assert resource_name is not None
            controller_name = (
                f"{module_prefix}/{resource_name}" if module_prefix else resource_name
            )
            for http_meth, route_pth, action in restful_routes:
                route_name = f"{http_meth} {route_pth}"
                route_id = make_symbol_id("ruby",
                    path=str(file_path),
                    start_line=span.start_line,
                    end_line=span.end_line,
                    name=route_name,
                    kind="route",
                )
                route_symbol = Symbol(
                    id=route_id,
                    name=route_name,
                    kind="route",
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller_action": f"{controller_name}#{action}",
                    },
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                route_symbols.append(route_symbol)
        elif method_name == "resource":
            # resource :profile creates 6 RESTful routes (no index)
            # Singular resource uses singular path but plural controller
            restful_routes = [
                ("GET", normalized_path, "show"),  # GET /profile
                ("GET", f"{normalized_path}/new", "new"),  # GET /profile/new
                ("POST", normalized_path, "create"),  # POST /profile
                ("GET", f"{normalized_path}/edit", "edit"),  # GET /profile/edit
                ("PATCH", normalized_path, "update"),  # PATCH /profile
                ("DELETE", normalized_path, "destroy"),  # DELETE /profile
            ]
            # Rails convention: resource :profile → ProfilesController (pluralized)
            # Don't double the 's' if name already ends in 's' (audit_logs, settings)
            assert resource_name is not None
            base_name = resource_name if resource_name.endswith("s") else f"{resource_name}s"
            controller_name = (
                f"{module_prefix}/{base_name}" if module_prefix else base_name
            )
            for http_meth, route_pth, action in restful_routes:
                route_name = f"{http_meth} {route_pth}"
                route_id = make_symbol_id("ruby",
                    path=str(file_path),
                    start_line=span.start_line,
                    end_line=span.end_line,
                    name=route_name,
                    kind="route",
                )
                route_symbol = Symbol(
                    id=route_id,
                    name=route_name,
                    kind="route",
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    meta={
                        "http_method": http_meth,
                        "route_path": route_pth,
                        "controller_action": f"{controller_name}#{action}",
                    },
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                route_symbols.append(route_symbol)
        else:
            # Regular HTTP method route (get, post, etc.)
            route_name = f"{http_method} {normalized_path}"
            route_id = make_symbol_id("ruby",
                path=str(file_path),
                start_line=span.start_line,
                end_line=span.end_line,
                name=route_name,
                kind="route",
            )
            route_meta: dict[str, str | bool] = {
                "http_method": http_method,
                "route_path": normalized_path,
                "has_block": has_block,
            }
            if controller_action:
                route_meta["controller_action"] = controller_action
            route_symbol = Symbol(
                id=route_id,
                name=route_name,
                kind="route",
                language="ruby",
                path=str(file_path),
                span=span,
                meta=route_meta,
                origin=PASS_ID,
                origin_run_id=run_id,
            )
            route_symbols.append(route_symbol)

    return contexts, route_symbols


def _extract_block_callback_edges(
    block_node: "tree_sitter.Node",
    source: bytes,
    enclosing_class_name: str,
    class_sym: "Symbol",
    file_symbols: dict[str, "Symbol"],
    global_symbols: dict[str, "Symbol"],
    line: int,
    run_id: str,
    edges: list["Edge"],
) -> None:
    """Extract method calls from a block-style callback body.

    Block-style callbacks like ``after_commit do ... end`` or
    ``before_save { ... }`` contain inline code rather than symbol
    references. We walk the block body looking for bare method calls
    (identifiers that resolve to methods in the enclosing class) and
    create ``invokes_callback`` edges for each.

    Args:
        block_node: The ``do_block`` or ``block`` AST node.
        source: Raw source bytes.
        enclosing_class_name: Name of the class containing the callback.
        class_sym: The Symbol for the enclosing class.
        file_symbols: File-local symbol lookup dict.
        global_symbols: Global symbol lookup dict.
        line: Source line of the callback declaration (for edge metadata).
        run: Current AnalysisRun for edge provenance.
        edges: List to append new edges to (mutated in place).
    """
    for child in iter_tree(block_node):
        # Look for bare method calls: identifiers used as call targets
        if child.type == "call":
            callee_node = child.child_by_field_name("method")
            if callee_node is None:
                continue  # pragma: no cover
            call_name = node_text(callee_node, source)
        elif child.type == "identifier":
            # Bare identifiers at statement level may be method calls
            # (Ruby allows calling methods without parens)
            # Only consider identifiers that are direct children of
            # body_statement / block_body (statement-level)
            if child.parent is None or child.parent.type not in (
                "body_statement", "block_body",
            ):
                continue  # pragma: no cover — identifiers nested in expressions
            call_name = node_text(child, source)
        else:
            continue

        # Resolve to a method in the enclosing class
        qualified = f"{enclosing_class_name}#{call_name}"
        callee = file_symbols.get(qualified) or global_symbols.get(qualified)
        if callee is None:
            continue

        edges.append(Edge.create(
            src=class_sym.id,
            dst=callee.id,
            edge_type="invokes_callback",
            line=line,
            evidence_type="rails_block_callback",
            confidence=0.85,
            origin=PASS_ID,
            origin_run_id=run_id,
        ))


def _extract_rails_callbacks(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    run_id: str,
) -> list[Edge]:
    """Extract invokes_callback edges from Rails callback declarations.

    Rails controllers and models declare callbacks at class level in two forms:

    **Named-method callbacks** (symbol arguments):
        before_action :authenticate!
        after_action :log_request, :track_metrics
        before_save :normalize_email

    **Block-style callbacks** (do...end or braces):
        after_commit do
          provision_database
        end
        before_save { normalize_name }

    Named-method callbacks create edges from the class to the named method
    (confidence 0.90, evidence ``rails_callback``). Block-style callbacks
    extract method calls from the block body and create edges for each
    (confidence 0.85, evidence ``rails_block_callback``).

    Also handles legacy Rails 3 API: before_filter, after_filter, around_filter.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name via the 'method' field
        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name not in RAILS_CALLBACK_METHODS:
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(node, source)
        if enclosing_type != "class" or enclosing_class_name is None:
            continue

        # Must not be inside a method (class-level declaration)
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True
                break
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue  # pragma: no cover — unusual but defensive

        # Find the class symbol
        class_sym = file_symbols.get(enclosing_class_name)
        if class_sym is None:  # pragma: no cover — class always in file symbols
            continue

        # Extract callback method names from symbol arguments
        args_node = _find_child_by_field(node, "arguments")
        if args_node is not None:
            for arg in args_node.children:
                if arg.type != "simple_symbol":
                    continue

                callback_name = node_text(arg, source).lstrip(":")

                # Resolve the callback method:
                # 1. Try qualified name in same class (ClassName#callback)
                qualified = f"{enclosing_class_name}#{callback_name}"
                callee = file_symbols.get(qualified) or global_symbols.get(qualified)

                # 2. Try bare name in file-local symbols
                if callee is None:
                    callee = file_symbols.get(callback_name)

                # 3. Try bare name in global symbols (parent class)
                if callee is None:
                    callee = global_symbols.get(callback_name)

                # 4. Try resolver for fuzzy matching (steps 1-3 cover all normal cases;
                #    this handles edge cases where method name differs from symbol key)
                if callee is None:  # pragma: no cover — steps 1-3 resolve all known patterns
                    lookup = resolver.lookup(callback_name)
                    if lookup.found and lookup.symbol is not None:
                        callee = lookup.symbol

                if callee is None:
                    continue

                edges.append(Edge.create(
                    src=class_sym.id,
                    dst=callee.id,
                    edge_type="invokes_callback",
                    line=node.start_point[0] + 1,
                    evidence_type="rails_callback",
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Block-style callbacks: after_commit do...end or before_save { ... }
        # Extract method calls from the block body and create edges.
        block_node = None
        for child in node.children:
            if child.type in ("do_block", "block"):
                block_node = child
                break

        if block_node is not None:
            _extract_block_callback_edges(
                block_node, source, enclosing_class_name,
                class_sym, file_symbols, global_symbols,
                node.start_point[0] + 1, run_id, edges,
            )

    return edges


def _association_name_to_class(name: str) -> str:
    """Convert an ActiveRecord association name to a PascalCase class name.

    Singularizes plural association names (has_many :comments → Comment)
    and converts snake_case to PascalCase (order_item → OrderItem).

    Handles common English pluralization rules:
    - ies → y (categories → Category)
    - ses → s (addresses → Address)
    - s → '' (comments → Comment)

    Singular names (belongs_to :user) are just capitalized.
    """
    # Singularize: ies → y, ses → s, s → ''
    if name.endswith("ies"):
        name = name[:-3] + "y"
    elif name.endswith("sses"):
        name = name[:-2]
    elif name.endswith("ses"):
        name = name[:-2]
    elif name.endswith("s") and not name.endswith("ss"):
        name = name[:-1]

    # PascalCase: split on _ and capitalize each segment
    return "".join(part.capitalize() for part in name.split("_"))


def _extract_class_name_option(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract class_name: value from association keyword arguments.

    Parses ``has_many :messages, class_name: "ChatMessage"`` to return
    ``"ChatMessage"``. Returns None if no class_name option is present.
    """
    for child in args_node.children:
        if child.type == "pair":
            key_node = child.child_by_field_name("key")
            val_node = child.child_by_field_name("value")
            if key_node is None or val_node is None:
                continue  # pragma: no cover — defensive against malformed AST
            key_text = node_text(key_node, source).lstrip(":")
            if key_text == "class_name":
                raw = node_text(val_node, source)
                # Strip surrounding quotes: "ChatMessage" or 'ChatMessage'
                return raw.strip("\"'")
    return None


def _extract_activerecord_associations(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract association edges from ActiveRecord model declarations.

    Rails models declare associations at class level:
        has_many :comments
        belongs_to :user
        has_one :profile
        has_many :messages, class_name: "ChatMessage"

    These create ``association`` edges from the declaring model class to the
    target model class. The target class is inferred from the association name
    by singularizing and converting to PascalCase, or from an explicit
    ``class_name:`` option.

    Returns edges with type ``association`` from class symbol to target class.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name not in _ASSOCIATION_METHODS:
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(
            node, source,
        )
        if enclosing_type != "class" or enclosing_class_name is None:
            continue  # pragma: no cover — defensive, associations at module level

        # Must not be inside a method
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True  # pragma: no cover — defensive
                break  # pragma: no cover — defensive
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue  # pragma: no cover — defensive

        # Find the class symbol
        class_sym = file_symbols.get(enclosing_class_name)
        if class_sym is None:  # pragma: no cover
            continue

        # Extract the association name (first symbol argument)
        args_node = _find_child_by_field(node, "arguments")
        if args_node is None:  # pragma: no cover
            continue

        assoc_name = None
        for arg in args_node.children:
            if arg.type == "simple_symbol":
                assoc_name = node_text(arg, source).lstrip(":")
                break

        if assoc_name is None:
            continue  # pragma: no cover — associations always have symbol arg

        # Determine target class name: explicit class_name option or convention
        target_class = _extract_class_name_option(args_node, source)
        if target_class is None:
            target_class = _association_name_to_class(assoc_name)

        # Look up target class in global symbols
        target_sym = global_symbols.get(target_class)

        if target_sym is not None:
            edges.append(Edge.create(
                src=class_sym.id,
                dst=target_sym.id,
                edge_type="association",
                line=node.start_point[0] + 1,
                evidence_type="activerecord_association",
                confidence=0.90,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
        else:
            # Unresolved: target model not found (may be in a gem)
            edges.append(Edge.create(
                src=class_sym.id,
                dst=f"ruby:?:0-0:{target_class}:unresolved",
                edge_type="association",
                line=node.start_point[0] + 1,
                evidence_type="activerecord_association",
                confidence=0.70,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    return edges


# Special pseudo-associations in delegate that should be skipped.
_DELEGATE_SKIP_TARGETS = frozenset({"class"})


def _extract_to_option(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract to: value from delegate keyword arguments.

    Parses ``delegate :foo, to: :account`` to return ``"account"``.
    Returns None if no ``to:`` option is present.
    """
    for child in args_node.children:
        if child.type == "pair":
            key_node = child.child_by_field_name("key")
            val_node = child.child_by_field_name("value")
            if key_node is None or val_node is None:
                continue  # pragma: no cover — defensive against malformed AST
            key_text = node_text(key_node, source).lstrip(":")
            if key_text == "to":
                raw = node_text(val_node, source)
                return raw.lstrip(":").strip("\"'")
    return None  # pragma: no cover — delegate without to: is invalid Ruby


def _extract_ruby_delegates(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract delegate edges from Ruby class declarations.

    Rails/Ruby classes use ``delegate`` to forward method calls::

        delegate :auto_resolve_after, to: :account
        delegate :name, :email, to: :user

    This creates ``delegates_to`` edges from the declaring class to the
    target method on the associated class. The target class is inferred
    by PascalCasing the ``to:`` symbol name (e.g., ``:account`` → ``Account``).

    Special targets like ``:class`` are skipped as they don't create
    meaningful cross-class edges.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name != "delegate":
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(
            node, source,
        )
        if enclosing_type != "class" or enclosing_class_name is None:
            continue  # pragma: no cover — delegate outside class is rare

        # Must not be inside a method
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True
                break
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue

        # Find the class symbol
        class_sym = file_symbols.get(enclosing_class_name)
        if class_sym is None:  # pragma: no cover
            continue

        # Extract arguments
        args_node = _find_child_by_field(node, "arguments")
        if args_node is None:  # pragma: no cover
            continue

        # Extract the to: target
        to_target = _extract_to_option(args_node, source)
        if to_target is None:
            continue  # pragma: no cover — delegate without to: is invalid Ruby

        # Skip special pseudo-associations
        if to_target in _DELEGATE_SKIP_TARGETS:
            continue

        # Resolve target class from to: symbol (e.g., :account → Account)
        target_class_name = _association_name_to_class(to_target)
        target_class_sym = global_symbols.get(target_class_name)

        # Extract all delegated method names (symbol arguments before to:)
        delegated_methods: list[str] = []
        for arg in args_node.children:
            if arg.type == "simple_symbol":
                delegated_methods.append(node_text(arg, source).lstrip(":"))

        # Create edges for each delegated method
        for method in delegated_methods:
            if target_class_sym is not None:
                # Try to find the specific method on the target class
                qualified = f"{target_class_name}#{method}"
                target_method = global_symbols.get(qualified)
                if target_method is not None:
                    edges.append(Edge.create(
                        src=class_sym.id,
                        dst=target_method.id,
                        edge_type="delegates_to",
                        line=node.start_point[0] + 1,
                        evidence_type="ruby_delegate",
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                else:
                    # Target class exists but method not found
                    edges.append(Edge.create(
                        src=class_sym.id,
                        dst=f"ruby:?:0-0:{qualified}:unresolved",
                        edge_type="delegates_to",
                        line=node.start_point[0] + 1,
                        evidence_type="ruby_delegate",
                        confidence=0.65,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
            else:
                # Target class not found at all
                qualified = f"{target_class_name}#{method}"
                edges.append(Edge.create(
                    src=class_sym.id,
                    dst=f"ruby:?:0-0:{qualified}:unresolved",
                    edge_type="delegates_to",
                    line=node.start_point[0] + 1,
                    evidence_type="ruby_delegate",
                    confidence=0.65,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return edges


# NOTE: We use the base FileAnalysis from hypergumbo_core.analyze.base.
# Ruby's require_hints are stored in FileAnalysis.import_aliases.


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a single Ruby file.

    Args:
        tree: Parsed tree-sitter tree
        source: Raw source bytes
        file_path: Relative path to the file
        run_id: Execution ID for provenance
    """
    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Method definition (instance methods)
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Qualify with class/module name if inside one
                enclosing_name, enclosing_type = _get_enclosing_class_or_module(node, source)
                if enclosing_type == "class" and enclosing_name:
                    full_name = f"{enclosing_name}#{method_name}"
                elif enclosing_type == "module" and enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_ruby_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Singleton method definition (class methods: def self.method_name)
        elif node.type == "singleton_method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Class methods use dot separator: ClassName.method_name
                enclosing_name, _ = _get_enclosing_class_or_module(node, source)
                if enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_ruby_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class definition
        elif node.type == "class":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                class_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract superclass if present (META-001)
                meta: dict[str, object] | None = None
                superclass_node = _find_child_by_field(node, "superclass")
                if superclass_node:
                    # The superclass node contains a child with the base class name
                    # For "class User < BaseModel": superclass has constant "BaseModel"
                    # For "class User < ActiveRecord::Base": superclass has scope_resolution
                    # We take the first named child which is the actual type reference
                    for child in superclass_node.children:
                        if child.is_named:
                            superclass_name = node_text(child, source)
                            meta = {"base_classes": [superclass_name]}
                            break

                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[class_name] = symbol

        # Module definition
        elif node.type == "module":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                module_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[module_name] = symbol

    # Extract require hints for disambiguation (stored in import_aliases)
    analysis.import_aliases = _extract_require_hints(tree, source)

    return analysis


# ActiveJob/Sidekiq class-method names that enqueue a job.
# When we see SomeJob.perform_later(args), we redirect to SomeJob#perform.
_JOB_ENQUEUE_METHODS = frozenset({"perform_later", "perform_async", "perform_in",
                                   "perform_at"})


def _try_receiver_call(
    receiver_node: "tree_sitter.Node",
    method_name: str,
    source: bytes,
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    line: int,
    edges: list[Edge],
    run_id: str,
) -> bool:
    """Try to resolve a receiver-qualified method call.

    Handles constant receivers (User.find) and scope_resolution receivers
    (ActiveRecord::Base.connection). Extracts the receiver class name and
    looks up ClassName#method or ClassName.method in global symbols.

    Special case: ActiveJob/Sidekiq enqueue methods (perform_later, perform_async,
    perform_in, perform_at) are redirected to the job class's ``perform`` instance
    method and emit ``enqueues`` edges instead of ``calls``.

    Returns True if an edge was created, False to fall through to bare name lookup.
    """
    # Extract receiver class name from constant, scope_resolution, or chained call
    receiver_class: str | None = None
    short_name: str | None = None
    if receiver_node.type == "constant":
        receiver_class = node_text(receiver_node, source)
    elif receiver_node.type == "scope_resolution":
        # Extract full qualified name (e.g., "Voice::InboundCallBuilder")
        # for inline-namespace class definitions
        full_name = node_text(receiver_node, source)
        # Also extract rightmost segment as fallback (e.g., "InboundCallBuilder")
        name_node = _find_child_by_field(receiver_node, "name")
        short_name = node_text(name_node, source) if name_node is not None else None
        receiver_class = full_name
    elif receiver_node.type == "call":
        # Handle chained calls: ClassName.new(args).method()
        # or ClassName.set(wait: 1.hour).perform_later(args)
        # Extract class from the inner call's receiver
        inner_receiver = receiver_node.child_by_field_name("receiver")
        if inner_receiver is not None:
            if inner_receiver.type == "constant":
                receiver_class = node_text(inner_receiver, source)
            elif inner_receiver.type == "scope_resolution":
                receiver_class = node_text(inner_receiver, source)

    if receiver_class is None:
        return False

    # Detect ActiveJob/Sidekiq enqueue patterns:
    # SomeJob.perform_later(args) -> enqueues SomeJob#perform
    # SomeJob.set(wait: 1.hour).perform_later(args) -> enqueues SomeJob#perform
    if method_name in _JOB_ENQUEUE_METHODS:
        return _try_job_enqueue(
            receiver_class, short_name, current_method,
            global_symbols, resolver, line, edges, run_id,
        )

    # Ruby constructor: SomeClass.new → SomeClass#initialize
    # In Ruby, .new is the class-level allocator that delegates to #initialize.
    # A Rails controller ``def new`` is an instance method (action), not the
    # constructor.  Matching ``SomeClass.new`` to ``SomeClass#new`` would create
    # massive false-positive edges (e.g. RoutesController#new with 130 false
    # in-edges in chatwoot).  Redirect to #initialize instead.
    if method_name == "new":
        candidates = [receiver_class]
        if receiver_node.type == "scope_resolution" and short_name and short_name != receiver_class:
            candidates.append(short_name)
        for candidate in candidates:
            init_key = f"{candidate}#initialize"
            if init_key in global_symbols:
                callee = global_symbols[init_key]
                if callee.id != current_method.id:
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="constructor_call",
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                    return True
        # No user-defined #initialize found — inherits Object#initialize (not
        # in user code), so no edge to create.
        return True

    # Build list of candidate class names to try
    # For scope_resolution: try full name first, then short name as fallback
    candidates = [receiver_class]
    if receiver_node.type == "scope_resolution" and short_name and short_name != receiver_class:
        candidates.append(short_name)

    # Try Class#method (instance method convention) and Class.method (module method)
    for candidate in candidates:
        for sep in ("#", "."):
            qualified = f"{candidate}{sep}{method_name}"
            if qualified in global_symbols:
                callee = global_symbols[qualified]
                if callee.id != current_method.id:
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="receiver_call",
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                    return True

    # Try resolver with class name as path hint for suffix matching
    lookup_result = resolver.lookup(method_name, path_hint=receiver_class)
    if lookup_result.found and lookup_result.symbol is not None:
        callee = lookup_result.symbol
        if callee.id != current_method.id:
            edges.append(Edge.create(
                src=current_method.id,
                dst=callee.id,
                edge_type="calls",
                line=line,
                evidence_type="receiver_call",
                confidence=0.75 * lookup_result.confidence,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
            return True

    return False


def _try_job_enqueue(
    receiver_class: str,
    short_name: str | None,
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    line: int,
    edges: list[Edge],
    run_id: str,
) -> bool:
    """Create an enqueues edge from current method to a job's perform method.

    When SomeJob.perform_later(args) is called, the actual work is done by
    SomeJob#perform at runtime. This function resolves the job class and creates
    an ``enqueues`` edge to its ``perform`` instance method.

    Falls back to an unresolved edge targeting the job class symbol if
    the ``perform`` method is not found (e.g., job defined in a gem).
    """
    candidates = [receiver_class]
    if short_name and short_name != receiver_class:
        candidates.append(short_name)

    # Try to find JobClass#perform or JobClass.perform
    for candidate in candidates:
        for sep in ("#", "."):
            qualified = f"{candidate}{sep}perform"
            if qualified in global_symbols:
                callee = global_symbols[qualified]
                if callee.id != current_method.id:
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="enqueues",
                        line=line,
                        evidence_type="job_enqueue",
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                    return True

    # Fallback: try to find the job class symbol itself
    for candidate in candidates:
        if candidate in global_symbols:
            callee = global_symbols[candidate]
            if callee.id != current_method.id:
                edges.append(Edge.create(
                    src=current_method.id,
                    dst=callee.id,
                    edge_type="enqueues",
                    line=line,
                    evidence_type="job_enqueue",
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
                return True

    # Last resort: unresolved edge to the job class name
    edges.append(Edge.create(
        src=current_method.id,
        dst=f"ruby:?:0-0:{receiver_class}:unresolved",
        edge_type="enqueues",
        line=line,
        evidence_type="job_enqueue",
        confidence=0.70,
        origin=PASS_ID,
        origin_run_id=run_id,
    ))
    return True


def _extract_ruby_var_types(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract variable-to-class-name mappings from assignment patterns.

    Scans for single-assignment statements where a variable is assigned
    from a class method call on a constant receiver.  Recognized patterns:

        var = ClassName.new(...)       → var has type ClassName
        var = ClassName.find(...)      → var has type ClassName
        var = ClassName.create(...)    → var has type ClassName
        var = ClassName.where(...)     → var has type ClassName (AR query)

    More generally, ``var = Constant.method(...)`` where the receiver starts
    with an uppercase letter maps *var* → *Constant*.  Only the first
    assignment to a variable wins (single-assignment SSA assumption
    within a method body).

    Returns a dict mapping variable names to inferred class names.
    """
    var_types: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type != "assignment":
            continue
        # LHS must be a simple identifier
        lhs = node.child_by_field_name("left")
        if lhs is None or lhs.type != "identifier":
            continue
        var_name = node_text(lhs, source)
        if var_name in var_types:
            continue  # first-assignment wins
        # RHS must be a call node with a constant receiver
        rhs = node.child_by_field_name("right")
        if rhs is None or rhs.type != "call":
            continue
        receiver = rhs.child_by_field_name("receiver")
        if receiver is None:
            continue
        if receiver.type == "constant":
            var_types[var_name] = node_text(receiver, source)
        elif receiver.type == "scope_resolution":
            # Namespace::Class.new → use full qualified name
            var_types[var_name] = node_text(receiver, source)
    return var_types


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: NameResolver,
    require_hints: dict[str, str],
    method_candidates: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Args:
        tree: Parsed tree-sitter tree
        source: Raw source bytes
        file_path: Relative path to the file
        local_symbols: File-local symbol lookup dict
        global_symbols: Global symbol lookup dict
        run_id: Execution ID for provenance
        resolver: Name resolver for cross-file lookups
        require_hints: Dict mapping class/module names to require paths for disambiguation.
    """
    edges: list[Edge] = []
    file_id = make_file_id("ruby", str(file_path))
    var_types = _extract_ruby_var_types(tree, source)
    _mc = method_candidates or {}

    for node in iter_tree(tree.root_node):
        # Detect call nodes (require statements and method calls)
        if node.type == "call":
            # Get method name from the 'method' field (not the first identifier!)
            # For `data.chop`, the tree has:
            #   call: receiver=identifier("data"), method=identifier("chop")
            # We want "chop", not "data"
            method_node = node.child_by_field_name("method")
            # Fallback for simple calls without receiver (e.g., `require "foo"`)
            # All tested call forms have method field, but this handles potential
            # edge cases in tree-sitter-ruby grammar versions.
            if method_node is None:  # pragma: no cover
                for child in node.children:
                    if child.type == "identifier":
                        method_node = child
                        break

            if method_node:
                callee_name = node_text(method_node, source)

                # Handle require/require_relative as imports
                if callee_name in ("require", "require_relative"):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content_node = find_child_by_type(arg, "string_content")
                                if content_node:
                                    import_path = node_text(content_node, source)
                                    edges.append(Edge.create(
                                        src=file_id,
                                        dst=f"ruby:{import_path}:0-0:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        evidence_type="require_statement",
                                        confidence=0.95,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))

                # Handle regular method calls
                else:
                    current_method = _get_enclosing_method(node, source, local_symbols)
                    if current_method is not None:
                        # Try receiver-qualified resolution first
                        receiver_node = node.child_by_field_name("receiver")
                        if receiver_node is not None and _try_receiver_call(
                            receiver_node, callee_name, source,
                            current_method, global_symbols, resolver,
                            node.start_point[0] + 1, edges, run_id,
                        ):
                            pass  # Resolved via receiver
                        elif receiver_node is not None:
                            # Variable receiver — _try_receiver_call only handles
                            # constant receivers (User.find), so reaching here
                            # means the receiver is a local variable or expression.
                            # Try typed receiver resolution: if var was assigned
                            # from ClassName.new / ClassName.find, resolve
                            # var.method → ClassName#method.
                            receiver_text = node_text(receiver_node, source)
                            inferred_class = var_types.get(receiver_text)
                            if inferred_class is not None:
                                # Build candidate keys: full name first, then
                                # short name for scope_resolution (Admin::User → User)
                                candidates = [f"{inferred_class}#{callee_name}"]
                                if "::" in inferred_class:
                                    short = inferred_class.rsplit("::", 1)[-1]
                                    candidates.append(f"{short}#{callee_name}")
                                callee = None
                                for key in candidates:
                                    callee = global_symbols.get(key)
                                    if callee is not None:
                                        break
                                if (
                                    callee is not None
                                    and callee.id != current_method.id
                                ):
                                    edges.append(Edge.create(
                                        src=current_method.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="typed_receiver_call",
                                        confidence=0.80,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))
                            # else: variable receiver with unknown type — no edge
                            # (avoids false positives from bare-name matching)
                        # AMB-METHOD guard: skip bare-call resolution when
                        # 3+ classes define the same method name.
                        elif len(_mc.get(callee_name, ())) >= 3:
                            pass
                        # Check local symbols (bare calls only, no receiver)
                        elif callee_name in local_symbols:
                            callee = local_symbols[callee_name]
                            # Skip self-referential edges (e.g., logger method
                            # calling Postal.logger where method name matches)
                            if callee.id != current_method.id:
                                edges.append(Edge.create(
                                    src=current_method.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="method_call",
                                    confidence=0.85,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))
                        # Check global symbols via resolver (bare calls only)
                        elif receiver_node is None:
                            # Use require hints for disambiguation
                            path_hint = require_hints.get(callee_name)
                            lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="method_call",
                                    confidence=0.80 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

        # Detect bare method calls (identifier nodes that are method names)
        # Skip identifiers that are part of a call/method_call - those are handled above
        elif node.type == "identifier":
            # If parent is a call-related node, skip (already handled by call handler)
            if node.parent is not None and node.parent.type in (
                "call", "method_call", "element_reference", "scope_resolution"
            ):
                continue
            current_method = _get_enclosing_method(node, source, local_symbols)
            if current_method is not None:
                callee_name = node_text(node, source)

                # Skip identifiers that match method parameter names.
                # In Ruby, ``def initialize(ip); @ip = ip; end`` — the
                # second ``ip`` is a parameter reference, not a method call.
                # Without this guard, ``ip`` would match any ``ip`` method
                # in the codebase via bare-name lookup.
                method_params = _get_enclosing_method_params(node, source)
                if callee_name in method_params:
                    continue

                # Class-aware resolution: extract enclosing class/module
                # from the current method's qualified name (e.g. "Worker#run"
                # → "Worker", "Utils.process" → "Utils").
                enclosing_class = _enclosing_class_from_method(current_method)

                # AMB-METHOD guard: when 3+ classes define the same
                # method name, only allow same-class resolution.
                ambiguous = len(_mc.get(callee_name, ())) >= 3

                # Priority cascade:
                # 1. Same-class qualified lookup (confidence 0.90)
                # 2. Unqualified local_symbols lookup (confidence 0.75)
                # 3. Global resolver fallback (confidence 0.70 * resolver)
                callee = _resolve_bare_call_class_aware(
                    callee_name, enclosing_class, local_symbols
                )

                if callee is not None:
                    if callee.kind == "method" and callee.id != current_method.id:
                        # Same-class match gets higher confidence
                        same_class = (
                            enclosing_class is not None
                            and _symbol_belongs_to_class(callee, enclosing_class)
                        )
                        # AMB-METHOD: skip cross-class matches when ambiguous
                        if ambiguous and not same_class:
                            pass
                        else:
                            confidence = 0.90 if same_class else 0.75
                            edges.append(Edge.create(
                                src=current_method.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="bare_method_call",
                                confidence=confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))
                elif not ambiguous:
                    # Use require hints for disambiguation
                    path_hint = require_hints.get(callee_name)
                    lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:
                        callee = lookup_result.symbol
                        if callee.kind == "method" and callee.id != current_method.id:
                            edges.append(Edge.create(
                                src=current_method.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="bare_method_call",
                                confidence=0.70 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

    return edges


def _resolve_base_class_ruby(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
) -> Symbol | None:
    """Resolve a base class name to a specific Symbol, disambiguating collisions.

    When multiple classes share the same name (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Require-hint match: child's file has ``require_relative 'path/to/model'``
       and the inferred PascalCase class name matches the base_name; then match
       the require path against candidate file paths
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
            (PascalCase class name -> require path)

    Returns:
        The resolved base class Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base class in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Require-hint match: check if child's file requires match a candidate
    require_hints = sym_file_require_hints.get(child_sym.id, {})
    if base_name in require_hints:
        require_path = require_hints[base_name]
        # Match require path against candidate file paths
        # e.g., require_path="models/model" matches candidate path "models/model.rb"
        for cand in candidates:
            cand_path = cand.path or ""
            # Strip .rb extension for comparison
            cand_no_ext = cand_path.rsplit(".rb", 1)[0]
            if cand_no_ext.endswith(require_path):
                return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
    run_id: str,
) -> list[Edge]:
    """Extract extends edges from class inheritance (META-001).

    For each class with base_classes metadata, creates extends edges to
    base classes that exist in the analyzed codebase. This enables the
    type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    When multiple classes share the same name (common in large repos with test stubs),
    uses require-hint-aware disambiguation via ``_resolve_base_class_ruby()`` to find
    the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
        run: Current analysis run for provenance

    Returns:
        List of extends edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Handle qualified names like "ActiveRecord::Base" -> look for last segment too
            base_name = base_class_name.split("::")[-1] if "::" in base_class_name else base_class_name

            # Try exact match first (for fully qualified names), then last segment
            base_sym = _resolve_base_class_ruby(
                base_class_name, sym, class_by_name, sym_file_require_hints
            )
            if base_sym is None and base_name != base_class_name:
                base_sym = _resolve_base_class_ruby(
                    base_name, sym, class_by_name, sym_file_require_hints
                )
            if base_sym is None or base_sym.id == sym.id:
                continue

            edge = Edge.create(
                src=sym.id,
                dst=base_sym.id,
                edge_type="extends",
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="ast_extends",
            )
            edges.append(edge)

    return edges


class RubyAnalyzer(TreeSitterAnalyzer):
    """Ruby language analyzer using tree-sitter-ruby.

    Overrides ``analyze()`` because Ruby requires multiple custom passes
    beyond the standard two-pass architecture:
    - Pass 1: Symbol extraction (standard)
    - Pass 2: Call/import edge extraction (standard)
    - Pass 2b: Rails callback edges (before_action, after_action, etc.)
    - Pass 2c: ActiveRecord association edges (has_many, belongs_to, etc.)
    - Pass 2d: Ruby delegate edges (delegate :method, to: :association)
    - Pass 3: Route extraction with test-file filtering
    - Post: Inheritance edges with require-hint disambiguation
    """

    lang = "ruby"
    file_patterns: ClassVar[list[str]] = ["*.rb"]
    grammar_module = "tree_sitter_ruby"

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol globally with short-name variants for cross-file resolution."""
        # Store by short name for cross-file resolution
        short_name = symbol.name.split("#")[-1] if "#" in symbol.name else symbol.name
        short_name = short_name.split(".")[-1] if "." in short_name else short_name
        global_symbols[short_name] = symbol
        global_symbols[symbol.name] = symbol

    def analyze(
        self,
        repo_root: Path,
        max_files: int | None = None,
    ) -> AnalysisResult:
        """Run multi-pass Ruby analysis.

        Overrides the base ``analyze()`` to add Rails callback, ActiveRecord
        association, delegate, and route extraction passes.
        """
        start_time = time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        # 1. Check grammar availability
        if not self._check_grammar_available():
            import warnings

            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        # 2. Initialize parser
        try:
            parser = self._create_parser()
        except Exception as e:
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"Failed to load Ruby parser: {e}",
            )

        # Pass 1: Extract all symbols from all files
        file_analyses: dict[Path, FileAnalysis] = {}
        all_rb_files: list[Path] = list(find_ruby_files(repo_root))
        files_skipped = 0

        for rb_file in all_rb_files:
            try:
                source = rb_file.read_bytes()
            except OSError:
                files_skipped += 1
                continue
            tree = parser.parse(source)
            rel_path = str(rb_file.relative_to(repo_root))
            analysis = _extract_symbols_from_file(
                tree, source, rel_path, run.execution_id,
            )
            if analysis.symbols:
                file_analyses[rb_file] = analysis
            else:
                files_skipped += 1

        # Build global symbol registry and method candidate counts
        global_symbols: dict[str, Symbol] = {}
        method_candidates: dict[str, list[Symbol]] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)
                if symbol.kind == "method":
                    short = symbol.name.split("#")[-1] if "#" in symbol.name else symbol.name
                    short = short.split(".")[-1] if "." in short else short
                    method_candidates.setdefault(short, []).append(symbol)

        # Pass 2: Extract edges from files with symbols
        resolver = NameResolver(global_symbols)
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_usage_contexts: list[UsageContext] = []

        for rb_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            source = rb_file.read_bytes()
            tree = parser.parse(source)
            rel_path = str(rb_file.relative_to(repo_root))

            edges = _extract_edges_from_file(
                tree, source, rel_path,
                analysis.symbol_by_name, global_symbols,
                run.execution_id, resolver,
                require_hints=analysis.import_aliases,
                method_candidates=method_candidates,
            )
            all_edges.extend(edges)

        # Pass 2b: Extract Rails callback edges (before_action, after_action, etc.)
        # These create invokes_callback edges from controller class -> callback method.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            callback_edges = _extract_rails_callbacks(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                resolver, run.execution_id,
            )
            all_edges.extend(callback_edges)

        # Pass 2c: Extract ActiveRecord association edges (has_many, belongs_to, etc.)
        # These create association edges from model class -> target model class.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            assoc_edges = _extract_activerecord_associations(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                run.execution_id,
            )
            all_edges.extend(assoc_edges)

        # Pass 2d: Extract Ruby delegate edges (delegate :method, to: :association)
        # These create delegates_to edges from class -> target method.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            delegate_edges = _extract_ruby_delegates(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                run.execution_id,
            )
            all_edges.extend(delegate_edges)

        # Pass 3: Extract usage contexts and route symbols with file-path filtering.
        # Test files (spec/, test/, etc.) use get/post/delete as HTTP test helpers,
        # NOT route definitions. Only detect routes in:
        # 1. Route definition files (routes.rb, config/routes/*.rb)
        # 2. Non-test app files with Sinatra-style do-block routes
        for rb_file in all_rb_files:
            is_test = _is_test_directory(rb_file, repo_root)
            if is_test:
                continue  # Skip test files entirely -- HTTP methods are test helpers
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
                symbol_by_name = file_analyses.get(rb_file, FileAnalysis()).symbol_by_name
                usage_contexts, route_symbols = _extract_rails_routes(
                    tree.root_node, source, rb_file, symbol_by_name, run.execution_id
                )
                if _is_route_definition_file(rb_file, repo_root):
                    # Route definition files: include all detected routes
                    all_usage_contexts.extend(usage_contexts)
                    all_symbols.extend(route_symbols)
                else:
                    # Non-route app files: only Sinatra-style routes (with do blocks)
                    # Bare get/post calls without blocks are likely HTTP client calls
                    for ctx in usage_contexts:
                        if ctx.metadata.get("has_block"):
                            all_usage_contexts.append(ctx)
                    for sym in route_symbols:
                        if sym.meta.get("has_block"):
                            all_symbols.append(sym)
            except (OSError, IOError):  # pragma: no cover
                pass

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        # Extract inheritance edges (META-001: base_classes metadata -> extends edges)
        # Build multi-value class lookup for disambiguation (INV-015)
        class_by_name: dict[str, list[Symbol]] = {}
        for s in all_symbols:
            if s.kind == "class":
                if s.name not in class_by_name:
                    class_by_name[s.name] = []
                class_by_name[s.name].append(s)
        # Build per-symbol require_hints mapping for import disambiguation
        sym_file_require_hints: dict[str, dict[str, str]] = {}
        for _rb_file, analysis in file_analyses.items():
            for sym in analysis.symbols:
                sym_file_require_hints[sym.id] = analysis.import_aliases
        inheritance_edges = _extract_inheritance_edges(
            all_symbols, class_by_name, sym_file_require_hints, run.execution_id,
        )
        all_edges.extend(inheritance_edges)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_usage_contexts,
            run=run,
        )


_analyzer = RubyAnalyzer()


@register_analyzer("ruby")
def analyze_ruby(repo_root: Path) -> AnalysisResult:
    """Analyze all Ruby files in a repository."""
    return _analyzer.analyze(repo_root)
