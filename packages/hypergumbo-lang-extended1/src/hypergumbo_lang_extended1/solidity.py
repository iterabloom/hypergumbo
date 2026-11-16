# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solidity analysis pass using tree-sitter-solidity.

This analyzer uses tree-sitter to parse Solidity smart contract files and extract:
- Contract declarations
- Interface declarations
- Library declarations
- Function definitions (kind="method" inside a contract, interface or
  library; kind="function" for a file-scope free function; INV-lapas)
- Constructor definitions
- Modifier definitions
- Event definitions
- State variables + struct members (kind="field"; WI-jusus); a member of a
  struct declared in a contract hashes the contract too (INV-fabih)
- File-level constants (kind="variable"; WI-jusus)
- Function call relationships
- Import relationships
- Inheritance relationships (contract A is B)
- Function override relationships
- Event emit references (emit Event(...))

If tree-sitter with Solidity support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Parse all files, extract all symbols, and register every symbol
  except the field/variable data anchors in the global registry (WI-jusus;
  they stay in the output symbols)
- Pass 2: Detect calls, imports, inheritance, event emits, and function
  overrides. Calls, emits and base contracts are resolved by scope (below);
  overrides against the registry's ``Contract.member`` keys.

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Solidity-specific
extraction logic.

Both passes take the REPO-RELATIVE path. It is an identity input (the file
anchor folded into every callable and field stable_id, a file-level
constant's ``make_variable_stable_id``), so an absolute path would make every
Solidity id differ between two checkouts of the same repository. A
``library``, which is no backstop table kind, mints
``overload_arity.scoped_stable_id`` rather than falling to the floor key.

Name Resolution
---------------
A name is resolved in the scope its site names, never by short name across
the file. The per-file table ``symbol_by_name`` keeps one symbol per short
name (the last declared), so with two contracts in one file that each declare
``helper`` and ``Transfer``, it answers every ``helper()`` and every
``emit Transfer(...)`` with the later contract's member.

Pass 1 therefore also records, per file, which contract declares which
member, each contract's ``is`` list and each state variable's declared type
(``_FileScopes``). Pass 2 reads all files' tables through ``_ScopeIndex`` and
``_MemberResolver``:

- ``f()`` and ``emit E(...)``: the enclosing contract's own member, then a
  member it inherits, then a file-level name (a free function, a contract
  type used as a conversion, a file-level event) of this file or another.
  The global registry keys a member as ``Contract.member``, so the file-level
  step cannot reach another contract's member. The registry's suffix match is
  not used: it would.
- ``this.f()``: the enclosing contract's scope. ``super.f()``: the same scope
  searched from the first base on, so an override that calls ``super`` joins
  the function it overrides and not itself.
- ``Base.f()``, ``Lib.f()``, ``emit IThing.E(...)``: the named contract's
  scope.
- ``token.f()`` where ``token`` is a parameter, a local or a state variable
  (own or inherited), and ``IERC20(x).f()``: the scope of the declared type.
  Solidity declares every variable with its type, so this reads the
  declaration. A type that is no contract in the tree (``address``, a struct,
  an interface from an unanalysed dependency) has no member to bind; the
  caller's own same-named function is not the callee.
- A receiver of unknown type (a call result, an index, a chain): the member
  is bound, at confidence 0.60, only when exactly one contract in the file
  declares it.
- ``using L for T`` attaches ``L``'s functions to a receiver wherever the
  receiver's own type gave no answer. A library function is a member of the
  type of its first parameter, so with the receiver's type known, the
  functions whose first parameter has it are preferred. If two attached
  libraries still declare the member, that is a tie and stays unresolved.

The receiver is read through one quirk of the grammar: tree-sitter-solidity
binds a member access looser than an operator before it, so
``ok || token.f(x)`` arrives as ``(ok || token).f(x)`` (``_receiver_of``).

A scope is searched in Solidity's linearisation order: C3 over the ``is``
list read right to left, so in ``contract C is X, Y`` a member of ``Y`` hides
one of ``X``. A base that is not in the tree contributes nothing, which
leaves a call to its members unresolved.

The walk is done here and not by the shared ``inherited_calls`` linker
because that linker indexes class-like kinds and ``calls`` edges only: it
does not know ``contract`` / ``library``, an emit is a ``references`` edge,
and the choice between an inherited member and a file-level function has to
be made at the call site.
An inherited call carries that linker's evidence type and
confidence (``ast_call_inherited``, 0.90), so the finding reads the same in
every language.

Limits. Overloads are not told apart by argument (the last one declared in
the contract found is bound). Visibility is not checked (a ``private`` base
member is found). A contract name declared in two files resolves to this
file's, else to whichever the global registry holds. A modifier invocation
and ``revert Error(...)`` emit no edge; ``new C(...)`` emits an unresolved
one.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-solidity package for grammar (direct grammar module)
- Two-pass allows cross-file call resolution
- Solidity-specific: contracts, modifiers, events are first-class symbols
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.symbol_kinds import SOLIDITY_CALLABLE_DECLARATION_KINDS
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    make_variable_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("solidity")

# WI-vibad: callable kinds that mint their own typed stable_id at the producer
# (rather than via the WI-rihob name-scoped backstop, which would collide on
# overloads). Container kinds (contract / interface / library) are NOT here.
# ``contract`` and ``interface`` are backstop table kinds and take their table
# value; ``library`` is not, so rather than fall to the floor key (WI-motiz)
# it mints ``overload_arity.scoped_stable_id`` (WI-tusad; a library cannot
# nest, so its scope is empty).
_CALLABLE_STABLE_ID_KINDS: frozenset[str] = frozenset(
    {"function", "method", "constructor", "modifier", "event"}
)

# INV-lapas: the two kinds a Solidity function declaration can carry.
# Containment decides which: a function declared inside a contract /
# interface / library body is a ``method``, exactly as in every other OO
# analyzer in the fleet; a file-scope function (legal since Solidity 0.7)
# stays a ``function``.
#
# One name, four readers: the enclosing-symbol span index, the
# ``using X for Y`` library-call resolver, and both halves of the
# ``overrides`` walker all test against this set rather than a
# ``kind == "function"`` literal each, so they cannot fall out of step with
# the emitted kinds. A reader that tests only ``"function"`` misses every
# method and silently drops edges (``overrides`` edges are numerous on
# inheritance-heavy code).
_FUNCTION_DECLARATION_KINDS: frozenset[str] = (
    SOLIDITY_CALLABLE_DECLARATION_KINDS - frozenset({"constructor"})
)

# Callables that own a body a call can be attributed to. Used by the
# span index that resolves which symbol encloses a call site.
_BODY_BEARING_CALLABLE_KINDS: frozenset[str] = (
    _FUNCTION_DECLARATION_KINDS | frozenset({"constructor", "modifier"})
)

# The three declarations that open a member scope.
_CONTAINER_KINDS: frozenset[str] = frozenset({"contract", "interface", "library"})

# What a call expression can name inside a contract scope. ``event`` is here
# for Solidity before 0.4.21, which raised an event by calling it.
_CALL_TARGET_KINDS: frozenset[str] = _FUNCTION_DECLARATION_KINDS | frozenset({"event"})

# What a bare call can name at file level: a free function, or a contract
# type (``IERC20(token)`` is a conversion written as a call).
_FILE_LEVEL_CALL_KINDS: frozenset[str] = frozenset({"function"}) | _CONTAINER_KINDS

_EVENT_KINDS: frozenset[str] = frozenset({"event"})

# Receivers that are part of the language and never a contract instance.
_BUILTIN_NAMESPACES: frozenset[str] = frozenset({"abi", "block", "msg", "tx"})

# ``address(x)`` / ``uint(x)`` / ``payable(x)``: the receiver is a value of
# an elementary type, so only a library attached with ``using`` has members.
_ELEMENTARY_CONVERSION_NODES: frozenset[str] = frozenset(
    {"type_cast_expression", "payable_conversion_expression"}
)

_CALLABLE_NODE_TYPES: frozenset[str] = frozenset(
    {"function_definition", "constructor_definition", "modifier_definition"}
)

# Operators the grammar binds tighter than the member access after them
# (see ``_receiver_of``).
_OPERATOR_NODES: frozenset[str] = frozenset({
    "binary_expression", "unary_expression", "update_expression",
    "ternary_expression",
})

# Spellings of one elementary type, for comparing two declared types.
_TYPE_ALIASES: dict[str, str] = {
    "uint": "uint256", "int": "int256", "address payable": "address",
}

# Confidence of a member call bound without knowing the receiver's type.
_RECEIVER_BLIND_CONFIDENCE = 0.60

# Confidence of a call bound to a member a base contract declares. The value
# the shared ``inherited_calls`` linker gives the same finding in other
# languages; the walk here follows the language's own linearisation, so the
# edge deserves no less.
_INHERITED_CALL_CONFIDENCE = 0.90

# A contract is identified by (repo-relative path, name): two files may each
# declare a ``Token``.
_ContractKey = tuple[str, str]


@dataclass
class _FileScopes:
    """The declarations of one file that name lookup reads, keyed by owner.

    The owner is the enclosing contract / interface / library name; the owner
    ``""`` is the file itself (free functions, file-level events).

    ``members`` keeps every declaration of a name in source order, so
    overloads and a same-named event and function stay apart; a lookup picks
    by kind. ``var_types`` holds the declared type text of state variables,
    which is what types a receiver such as ``token.transfer(...)``.
    ``first_parameter_types`` maps a function's symbol id to the type of its
    first parameter: for a library function attached with ``using``, that is
    the receiver type it becomes a member of.
    """

    containers: dict[str, Symbol] = field(default_factory=dict)
    bases: dict[str, list[str]] = field(default_factory=dict)
    members: dict[str, dict[str, list[Symbol]]] = field(default_factory=dict)
    var_types: dict[str, dict[str, str]] = field(default_factory=dict)
    first_parameter_types: dict[str, str] = field(default_factory=dict)


def _type_key(type_text: Optional[str]) -> Optional[str]:
    """A declared type reduced to what two spellings of it share.

    ``Memory.Slice`` written outside the library and ``Slice`` written inside
    it are one type; so are ``uint`` and ``uint256``.
    """
    if type_text is None:
        return None
    key = type_text.rsplit(".", 1)[-1]
    return _TYPE_ALIASES.get(key, key)


def _c3_merge(sequences: list[list[_ContractKey]]) -> list[_ContractKey]:
    """C3 merge. Takes a head that is in no other sequence's tail, repeatedly.

    A hierarchy the compiler would reject has no such head; the first head is
    taken instead, so the walk stays total.
    """
    seqs = [list(s) for s in sequences if s]
    merged: list[_ContractKey] = []
    while seqs:
        head = next(
            (s[0] for s in seqs if not any(s[0] in other[1:] for other in seqs)),
            seqs[0][0],
        )
        merged.append(head)
        seqs = [rest for rest in ([k for k in s if k != head] for s in seqs) if rest]
    return merged


class _ScopeIndex:
    """Contract scopes across files, as seen from one file.

    Answers the two questions Solidity name lookup needs: which contract a
    name denotes here (this file first, then the global registry, exactly as
    the ``inherits`` edge resolves a base), and in what order a contract and
    its bases are searched.
    """

    def __init__(
        self, files: dict[str, _FileScopes], path: str,
        global_symbols: dict[str, Symbol],
    ) -> None:
        self._files = files
        self._path = path
        self._global = global_symbols
        self._linearized: dict[_ContractKey, list[_ContractKey]] = {}
        self.here = self.scopes(path)

    def scopes(self, path: str) -> _FileScopes:
        """The scope tables of ``path``; empty for a file that was not read."""
        return self._files.get(path) or _FileScopes()

    def container_symbol(self, name: str, from_path: Optional[str] = None) -> Optional[Symbol]:
        """The contract / interface / library ``name`` denotes in a file."""
        local = self.scopes(from_path or self._path).containers.get(name)
        if local is not None:
            return local
        found = self._global.get(name)
        if found is not None and found.kind in _CONTAINER_KINDS:
            return found
        return None

    def container(self, name: str, from_path: Optional[str] = None) -> Optional[_ContractKey]:
        """``container_symbol`` as a key into the scope tables."""
        symbol = self.container_symbol(name, from_path)
        return (symbol.path, symbol.name) if symbol is not None else None

    def linearization(self, key: _ContractKey) -> list[_ContractKey]:
        """``key`` followed by its bases in Solidity's search order.

        Solidity linearises with C3 over the ``is`` list read right to left:
        in ``contract C is X, Y`` a member of ``Y`` is found before one of
        ``X``. A base that is not in the tree contributes nothing.
        """
        return self._linearize(key, frozenset())[0]

    def _linearize(
        self, key: _ContractKey, open_keys: frozenset[_ContractKey],
    ) -> tuple[list[_ContractKey], bool]:
        """Linearise ``key``; the flag is False when a cycle cut a base off.

        Only a complete answer is remembered: one computed inside a cycle
        lacks the base that was still open.
        """
        known = self._linearized.get(key)
        if known is not None:
            return known, True
        path, name = key
        open_here = open_keys | {key}
        complete = True
        bases: list[_ContractKey] = []
        for base_name in self.scopes(path).bases.get(name, ()):
            base = self.container(base_name, path)
            if base is None or base in bases:
                continue
            if base in open_here:
                complete = False
                continue
            bases.append(base)
        bases.reverse()
        sequences: list[list[_ContractKey]] = []
        for base in bases:
            base_order, base_complete = self._linearize(base, open_here)
            complete = complete and base_complete
            sequences.append(base_order)
        order = [key] + _c3_merge([*sequences, bases])
        if complete:
            self._linearized[key] = order
        return order, complete

    def member(
        self, start: _ContractKey, name: str, kinds: frozenset[str],
        *, skip_own: bool = False,
    ) -> Optional[tuple[Symbol, bool]]:
        """The declaration ``name`` denotes in ``start``'s scope.

        Returns it with whether it is inherited. ``skip_own`` is ``super``:
        the search starts after ``start`` itself. Among overloads in one
        contract the last declared is returned; arguments are not matched.
        """
        order = self.linearization(start)
        for path, owner in order[1:] if skip_own else order:
            declared = self.scopes(path).members.get(owner, {}).get(name, ())
            for symbol in reversed(declared):
                if symbol.kind in kinds:
                    return symbol, (path, owner) != start
        return None

    def declared(
        self, owner: _ContractKey, name: str, kinds: frozenset[str],
    ) -> list[Symbol]:
        """Every declaration of ``name`` in one contract itself, overloads included."""
        declared = self.scopes(owner[0]).members.get(owner[1], {}).get(name, ())
        return [symbol for symbol in declared if symbol.kind in kinds]

    def first_parameter_type(self, function: Symbol) -> Optional[str]:
        """The declared type of a function's first parameter, if it has one."""
        return self.scopes(function.path).first_parameter_types.get(function.id)

    def state_variable_type(self, start: _ContractKey, name: str) -> Optional[str]:
        """The declared type of a state variable ``start`` declares or inherits."""
        for path, owner in self.linearization(start):
            declared = self.scopes(path).var_types.get(owner, {}).get(name)
            if declared is not None:
                return declared
        return None

    def file_level(self, name: str, kinds: frozenset[str]) -> Optional[tuple[Symbol, bool]]:
        """A file-level declaration: this file's, else another file's.

        Returns it with whether it is in this file. The global registry keys
        a member as ``Contract.member``, so a bare key is a file-level name:
        a member of another contract cannot be reached from here.
        """
        for symbol in reversed(self.here.members.get("", {}).get(name, ())):
            if symbol.kind in kinds:
                return symbol, True
        local = self.here.containers.get(name)
        if local is not None and local.kind in kinds:
            return local, True
        found = self._global.get(name)
        if found is not None and found.kind in kinds:
            return found, False
        return None


def find_solidity_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Solidity files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_contract(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing contract/interface/library name."""
    current = node.parent
    while current is not None:
        if current.type in ("contract_declaration", "interface_declaration", "library_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_struct(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up to the nearest enclosing struct name (owner of a struct member)."""
    current = node.parent
    while current is not None:
        if current.type == "struct_declaration":
            name_node = find_child_by_type(current, "identifier")
            if name_node is not None:
                return node_text(name_node, source)
            return None  # pragma: no cover - a struct declaration always names
        current = current.parent
    return None  # pragma: no cover - a struct_member is always under a struct


def _base_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The names in a contract's ``is`` list, in source order."""
    names: list[str] = []
    for child in node.children:
        if child.type == "inheritance_specifier":
            parent_type_node = find_child_by_type(child, "user_defined_type")
            if parent_type_node:
                names.append(node_text(parent_type_node, source))
    return names


def _strip_wrappers(node: "tree_sitter.Node") -> "tree_sitter.Node":
    """The expression a callee or receiver really is.

    Unwraps the grammar's ``expression`` node, parentheses, and call options:
    ``target.pay{value: 1}()`` parses its callee as a ``struct_expression``
    whose ``type`` is ``target.pay``.
    """
    while True:
        inner: Optional["tree_sitter.Node"] = None
        if node.type in ("expression", "parenthesized_expression"):
            if node.named_child_count == 1:
                inner = node.named_children[0]
        elif node.type == "struct_expression":
            inner = node.child_by_field_name("type")
        if inner is None:
            return node
        node = inner


def _receiver_of(node: "tree_sitter.Node") -> "tree_sitter.Node":
    """The receiver of a member access, as the language reads it.

    tree-sitter-solidity binds a member access looser than an operator before
    it: ``ok || token.f(x)`` parses as ``(ok || token).f(x)``, and
    ``!token.f(x)`` as ``(!token).f(x)``. The receiver is the operator's last
    operand. An operator the source put in parentheses is left alone: there
    the receiver is the whole expression.
    """
    while True:
        while node.type == "expression" and node.named_child_count == 1:
            node = node.named_children[0]
        if node.type not in _OPERATOR_NODES:
            return _strip_wrappers(node)
        node = node.named_children[-1]


def _local_variable_types(
    site: "tree_sitter.Node", source: bytes,
    cache: dict[int, dict[str, str]],
) -> dict[str, str]:
    """Declared type text of the parameters and locals around a call site.

    Solidity declares every variable with its type, so the receiver of
    ``token.transfer(...)`` is typed by reading the declaration. One table
    per callable; a name declared twice keeps its first declaration.
    """
    current = site.parent
    while current is not None and current.type not in _CALLABLE_NODE_TYPES:
        current = current.parent
    if current is None:
        return {}  # pragma: no cover - an attributed call site is inside a callable
    known = cache.get(current.start_byte)
    if known is None:
        known = {}
        for node in iter_tree(current):
            if node.type in ("parameter", "variable_declaration"):
                name_node = _find_child_by_field(node, "name")
                type_node = _find_child_by_field(node, "type")
                if name_node is not None and type_node is not None:
                    known.setdefault(
                        node_text(name_node, source), node_text(type_node, source),
                    )
        cache[current.start_byte] = known
    return known


def _solidity_value_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The declared ``type_name`` text of a state-variable / member / constant."""
    type_node = find_child_by_type(node, "type_name")
    if type_node is not None:
        return node_text(type_node, source)
    return None  # pragma: no cover - a value declaration always has a type_name


def _get_enclosing_function_solidity(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    symbols_by_span: Optional[dict[tuple[int, int], Symbol]] = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/constructor/modifier.

    Uses position-based matching (symbols_by_span) when available to correctly
    handle Solidity function overloading. Without it, overloaded functions
    (same name, different parameters) would resolve to the last-registered
    overload because symbol_by_name can only hold one entry per name.
    """
    current = node.parent
    while current is not None:
        if current.type in ("function_definition", "constructor_definition", "modifier_definition"):
            start_line = current.start_point[0] + 1
            end_line = current.end_point[0] + 1
            # Prefer position-based match (handles overloads correctly)
            if symbols_by_span is not None:
                sym = symbols_by_span.get((start_line, end_line))
                if sym:
                    return sym
            # Fall back to name-based lookup (when symbols_by_span not provided)
            if current.type == "function_definition":  # pragma: no cover - position match preferred
                name_node = _find_child_by_field(current, "name")
                if name_node:
                    func_name = node_text(name_node, source)
                    sym = local_symbols.get(func_name) or global_symbols.get(func_name)
                    if sym:
                        return sym
            elif current.type == "constructor_definition":  # pragma: no cover - constructor calls rare
                sym = local_symbols.get("constructor") or global_symbols.get("constructor")
                if sym:
                    return sym
            elif current.type == "modifier_definition":  # pragma: no cover - modifier calls rare
                name_node = _find_child_by_field(current, "name")
                if name_node:
                    mod_name = node_text(name_node, source)
                    sym = local_symbols.get(mod_name) or global_symbols.get(mod_name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_solidity_signature(
    node: "tree_sitter.Node", source: bytes
) -> str:
    """Extract function signature from a Solidity function definition.

    Solidity syntax: function name(type1 param1, type2 param2) returns (type3)
    Returns signature like "(address to, uint256 amount) returns (bool)".

    Always returns a string: the parameter list degrades to ``"()"`` when the
    node carries no ``parameter`` children, so there is no failure path.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        # Parameters are direct children with type "parameter"
        if child.type == "parameter":
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "return_type_definition":
            # Return type definition: returns (type)
            return_type = node_text(child, source).strip()

    sig = "(" + ", ".join(params) + ")"
    if return_type:
        sig += f" {return_type}"
    return sig


def _extract_import_aliases(
    node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, dict[str, str]]:
    """Extract import path and alias mappings from an import directive.

    Solidity import patterns:
    - import "file.sol";                      -> path, no aliases
    - import * as Alias from "file.sol";      -> path, {Alias: path}
    - import {X as Y} from "file.sol";        -> path, {Y: path}
    - import {X, Y as Z} from "file.sol";     -> path, {Z: path}

    Returns (import_path, {alias: import_path}).
    """
    import_path = ""
    aliases: dict[str, str] = {}

    # Find the import path (string node)
    string_node = find_child_by_type(node, "string")
    if string_node:
        import_path = node_text(string_node, source).strip('"\'')

    if not import_path:
        return "", {}  # pragma: no cover - defensive

    # Look for alias patterns
    children = list(node.children)
    i = 0
    while i < len(children):
        child = children[i]
        if child.type == "as" and i + 1 < len(children):
            # The next identifier is the alias
            next_child = children[i + 1]
            if next_child.type == "identifier":
                alias = node_text(next_child, source)
                aliases[alias] = import_path
        i += 1

    return import_path, aliases


def _extract_visibility_modifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract visibility and state mutability modifiers from a Solidity function.

    Solidity functions can have:
    - Visibility: public, external, internal, private
    - State mutability: view, pure, payable

    These are direct children of function_definition nodes with types
    'visibility' and 'state_mutability' in the tree-sitter-solidity grammar.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "visibility":
            modifiers.append(node_text(child, source))
        elif child.type == "state_mutability":
            modifiers.append(node_text(child, source))
    return modifiers


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    run_id: str, analysis: FileAnalysis,
) -> _FileScopes:
    """Extract symbols from a single Solidity file's parse tree.

    Returns the file's scope tables (which contract declares which member,
    its ``is`` list, its state-variable types): Pass 2 resolves a name in the
    contract that uses it, and a base may live in another file.
    """
    scopes = _FileScopes()

    def add_symbol(
        name: str,
        kind: str,
        node: "tree_sitter.Node",
        prefix: str = "",
        signature: Optional[str] = None,
        modifiers: Optional[list[str]] = None,
        complexity: bool = False,
    ) -> Symbol:
        """Helper to create and register a symbol.

        ``complexity=True`` (callable kinds — function / constructor /
        modifier) populates ``cyclomatic_complexity`` and ``line_span``
        per INV-loguk. It is gated rather than unconditional so a non-callable
        symbol (contract / interface / event) does not silently aggregate
        every branch node in its whole subtree.
        """
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        full_name = f"{prefix}.{name}" if prefix else name

        # WI-vibad: callable-kind declarations (function / method / constructor
        # / modifier / event) are deliberately excluded from the WI-rihob name-scoped
        # backstop (``_KIND_STABLE_ID_FACTORIES``) because a name-only key
        # collides on overloads (two ``Token.mint(...)`` bind to one id). They
        # must carry the typed producer stable_id — the signature keeps overloads
        # distinct — exactly as the field path (``add_value_symbol``)
        # does. ``contract`` / ``interface`` leave ``stable_id=None`` here and
        # receive their table value from the backstop; ``library``, no table
        # kind, mints its producer id here rather than falling to the floor
        # key.
        callable_stable_id: Optional[str] = None
        if kind == "library":
            callable_stable_id = scoped_stable_id(
                kind=kind, lang="solidity", path=file_path, name=name,
            )
        elif kind in _CALLABLE_STABLE_ID_KINDS:
            # _extract_solidity_signature always returns a "(...)" string, so
            # the fallback is a real signature, never empty.
            norm_sig = signature or _extract_solidity_signature(node, source)
            callable_stable_id = make_typed_stable_id(
                kind,
                norm_sig,
                visibility_from_modifiers(modifiers),
                name=name,
                qualified_name=full_name,
                file_stable_id=file_stable_id,
            )

        symbol = Symbol(
            id=make_symbol_id("solidity", file_path, start_line, end_line, full_name, kind),
            name=full_name,
            kind=kind,
            language="solidity",
            path=file_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
            stable_id=callable_stable_id,
            signature=signature,
            modifiers=modifiers or [],
            cyclomatic_complexity=(
                compute_cyclomatic_complexity(node, "solidity") if complexity else None
            ),
            line_span=(end_line - start_line + 1) if complexity else None,
        )
        analysis.symbols.append(symbol)
        analysis.symbol_by_name[name] = symbol
        analysis.symbol_by_name[full_name] = symbol
        if kind in _CONTAINER_KINDS:
            scopes.containers[name] = symbol
        else:
            scopes.members.setdefault(prefix, {}).setdefault(name, []).append(symbol)
        if kind in _FUNCTION_DECLARATION_KINDS:
            first_parameter = find_child_by_type(node, "parameter")
            if first_parameter is not None:
                type_node = _find_child_by_field(first_parameter, "type")
                if type_node is not None:
                    scopes.first_parameter_types[symbol.id] = node_text(type_node, source)
        # Register the defining node so the base-class auto-stamp loop
        # (TreeSitterAnalyzer.analyze) computes shape_id (and any NatSpec doc
        # comment) for this body-bearing symbol. Solidity is a plain
        # TreeSitterAnalyzer subclass and does not override analyze(), so
        # populating node_for_symbol is all that is needed to stamp shape_id —
        # making these symbols visible to the repeat-finder clone detector,
        # which groups by (language, shape_id) and drops None (WI-lutob).
        analysis.node_for_symbol[symbol.id] = node
        return symbol

    # WI-jusus (emission-parity): file-identity anchor so same-named state
    # variables / constants in different files hash to distinct stable_ids.
    file_stable_id = make_file_stable_id("solidity", normalize_path(file_path))

    def add_value_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        full_name: str,
        vtype: Optional[str],
        modifiers: list[str],
        is_exported: bool,
        hashed_name: Optional[str] = None,
    ) -> None:
        """Append a field/variable Symbol (WI-jusus).

        ``hashed_name`` is the qualified name a field hashes when it is not
        ``full_name`` (a struct member's: the contract too, INV-fabih).

        Deliberately NOT registered in ``symbol_by_name`` (nor, via
        ``SolidityAnalyzer.register_symbol``, in the global registry): a state
        variable / member / constant is a data anchor, never a call target, so
        keeping it out of resolution prevents a bare-named value from shadowing
        a same-named function. It still reaches output/search/centrality/io-
        boundaries because the output symbol set is analysis.symbols.
        """
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        if kind == "field":
            stable_id = make_typed_stable_id(
                "field", vtype or "",
                visibility_from_modifiers(modifiers),
                name=name, qualified_name=hashed_name or full_name,
                file_stable_id=file_stable_id,
            )
        else:
            stable_id = make_variable_stable_id("solidity", normalize_path(file_path), name)
        analysis.symbols.append(Symbol(
            id=make_symbol_id(
                "solidity", file_path, start_line, end_line, full_name, kind
            ),
            name=full_name,
            kind=kind,
            language="solidity",
            path=file_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
            stable_id=stable_id,
            signature=vtype,
            modifiers=modifiers,
            is_exported=is_exported,
        ))

    for node in iter_tree(tree.root_node):
        # Contract declaration
        if node.type == "contract_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                contract_name = node_text(name_node, source)
                add_symbol(contract_name, "contract", node)
                scopes.bases[contract_name] = _base_names(node, source)

        # Interface declaration
        elif node.type == "interface_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                interface_name = node_text(name_node, source)
                add_symbol(interface_name, "interface", node)
                scopes.bases[interface_name] = _base_names(node, source)

        # Library declaration
        elif node.type == "library_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                lib_name = node_text(name_node, source)
                add_symbol(lib_name, "library", node)

        # Function definition
        elif node.type == "function_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                signature = _extract_solidity_signature(node, source)
                modifiers = _extract_visibility_modifiers(node, source)
                # INV-lapas: a function with an enclosing contract / interface
                # / library is a method. Solidity 0.7+ also allows file-scope
                # free functions, which stay ``function`` — so this reads
                # containment rather than renaming unconditionally.
                func_kind = "method" if current_contract else "function"
                add_symbol(func_name, func_kind, node, current_contract, signature=signature, modifiers=modifiers, complexity=True)

        # Constructor definition
        elif node.type == "constructor_definition":
            current_contract = _get_enclosing_contract(node, source) or ""
            add_symbol("constructor", "constructor", node, current_contract, complexity=True)

        # Modifier definition
        elif node.type == "modifier_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                mod_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(mod_name, "modifier", node, current_contract, complexity=True)

        # Event definition
        elif node.type == "event_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                event_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(event_name, "event", node, current_contract)

        # State variable (contract storage) -> field (WI-jusus emission-parity).
        # The security-critical persistent storage: taint source/sink, io-boundary
        # anchor, and what functions read/write. Function-body locals use a distinct
        # node (variable_declaration inside variable_declaration_statement) and never
        # reach here, so the local-leak class is structurally impossible.
        elif node.type == "state_variable_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node is not None:
                name = node_text(name_node, source)
                owner = _get_enclosing_contract(node, source) or ""
                vtype = _solidity_value_type(node, source)
                vis_node = find_child_by_type(node, "visibility")
                visibility = node_text(vis_node, source) if vis_node is not None else None
                modifiers = [visibility] if visibility else []
                full_name = f"{owner}.{name}" if owner else name
                add_value_symbol(
                    node, name, "field", full_name, vtype, modifiers,
                    visibility == "public",
                )
                if vtype is not None:
                    scopes.var_types.setdefault(owner, {})[name] = vtype

        # Struct member -> field (owner = the struct type).
        elif node.type == "struct_member":
            name_node = find_child_by_type(node, "identifier")
            if name_node is not None:
                name = node_text(name_node, source)
                owner = _get_enclosing_struct(node, source) or ""
                vtype = _solidity_value_type(node, source)
                full_name = f"{owner}.{name}" if owner else name
                # A struct declared in a contract hashes the contract too, so
                # ``struct Inner`` in contracts ``A`` and ``B`` yields two
                # ``Inner.v`` identities and deleting ``A`` does not move
                # ``B``'s field id (INV-fabih).
                contract = _get_enclosing_contract(node, source)
                add_value_symbol(
                    node, name, "field", full_name, vtype, [], True,
                    hashed_name=f"{contract}.{full_name}" if contract else None,
                )

        # File-level constant -> variable (contract-level constants parse as
        # state_variable_declaration and are handled above as fields).
        elif node.type == "constant_variable_declaration":
            parent = node.parent
            if parent is not None and parent.type == "source_file":
                name_node = find_child_by_type(node, "identifier")
                if name_node is not None:
                    name = node_text(name_node, source)
                    vtype = _solidity_value_type(node, source)
                    add_value_symbol(node, name, "variable", name, vtype, [], True)

    return scopes


@dataclass(frozen=True)
class _Bound:
    """A call resolved to a declaration, and how the edge should say so.

    ``confidence=None`` leaves the value to ``Edge.create``, which derives it
    from the evidence type.
    """

    symbol: Symbol
    evidence_type: str
    confidence: Optional[float] = None
    meta: Optional[dict[str, Any]] = None


def _scope_bound(symbol: Symbol, inherited: bool) -> _Bound:
    """A call bound in the calling contract's scope: own member or inherited."""
    if inherited:
        return _Bound(symbol, "ast_call_inherited", confidence=_INHERITED_CALL_CONFIDENCE)
    return _Bound(symbol, "ast_call_direct")


class _MemberResolver:
    """Which declaration a name denotes at one site of one file.

    Every answer starts from the scope the site names: the enclosing contract
    for a bare name, ``this`` and ``super``; the named contract for
    ``Base.f()``; the receiver's declared type for ``token.f()`` and
    ``IERC20(x).f()``. A scope is searched own members first, then bases in
    linearisation order. A bare name then falls back to file-level names. A
    member of a contract the site is unrelated to is never an answer to a
    bare name.
    """

    def __init__(
        self, index: _ScopeIndex, source: bytes, path: str,
        using_libraries: dict[str, set[str]],
    ) -> None:
        self._index = index
        self._source = source
        self._path = path
        self._using = using_libraries
        self._locals: dict[int, dict[str, str]] = {}

    def _own(self, site: "tree_sitter.Node") -> Optional[_ContractKey]:
        """The contract a site is written in; None for a free function."""
        contract = _get_enclosing_contract(site, self._source)
        return (self._path, contract) if contract else None

    def call(self, site: "tree_sitter.Node", callee: "tree_sitter.Node") -> Optional[_Bound]:
        """Resolve the callee of a call expression; None leaves it unresolved."""
        own = self._own(site)
        if callee.type == "identifier":
            return self._bare_call(own, node_text(callee, self._source))
        if callee.type == "member_expression":
            receiver = _find_child_by_field(callee, "object")
            member = _find_child_by_field(callee, "property")
            if receiver is not None and member is not None:
                return self._member_call(
                    site, own, _receiver_of(receiver), node_text(member, self._source),
                )
        return None

    def _bare_call(self, own: Optional[_ContractKey], name: str) -> Optional[_Bound]:
        """``name(...)``: the contract's scope, then a file-level name."""
        if own is not None:
            scoped = self._index.member(own, name, _CALL_TARGET_KINDS)
            if scoped is not None:
                symbol, inherited = scoped
                return _scope_bound(symbol, inherited)
        loose = self._index.file_level(name, _FILE_LEVEL_CALL_KINDS)
        if loose is not None:
            symbol, same_file = loose
            return _Bound(symbol, "ast_call_direct" if same_file else "ast_call")
        return None

    def _member_call(
        self, site: "tree_sitter.Node", own: Optional[_ContractKey],
        receiver: "tree_sitter.Node", member: str,
    ) -> Optional[_Bound]:
        """``receiver.member(...)``: the scope the receiver names."""
        if receiver.type == "identifier":
            word = node_text(receiver, self._source)
            if own is not None and word in ("this", "super"):
                scoped = self._index.member(
                    own, member, _FUNCTION_DECLARATION_KINDS, skip_own=word == "super",
                )
                if scoped is None:
                    return None
                symbol, inherited = scoped
                return _scope_bound(symbol, inherited)
            declared = _local_variable_types(site, self._source, self._locals).get(word)
            if declared is None and own is not None:
                declared = self._index.state_variable_type(own, word)
            if declared is not None:
                return self._typed_call(own, declared, member)
            named = self._index.container(word)
            if named is not None:
                # ``Base.f()`` / ``Lib.f()``: the call site names the scope.
                scoped = self._index.member(named, member, _FUNCTION_DECLARATION_KINDS)
                if scoped is None:
                    return None
                return _Bound(scoped[0], "ast_call_direct", meta={"receiver": "qualified"})
            if word in _BUILTIN_NAMESPACES:
                return None
            return self._untyped_call(own, member)
        if receiver.type in _ELEMENTARY_CONVERSION_NODES:
            return self._attached_call(own, member)
        if receiver.type == "call_expression":
            # ``IERC20(token).transfer(...)``: a conversion types the receiver.
            converted_to = _find_child_by_field(receiver, "function")
            if converted_to is not None:
                converted_to = _strip_wrappers(converted_to)
                if converted_to.type == "identifier":
                    type_name = node_text(converted_to, self._source)
                    if self._index.container(type_name) is not None:
                        return self._typed_call(own, type_name, member)
        return self._untyped_call(own, member)

    def _typed_call(
        self, own: Optional[_ContractKey], receiver_type: str, member: str,
    ) -> Optional[_Bound]:
        """The receiver's declared type is known: its scope, then a library.

        A type that is no contract in the tree (an elementary type, a struct,
        a contract declared elsewhere) has no scope to search. No same-named
        member of another contract is tried: the type rules it out.
        """
        contract = self._index.container(receiver_type)
        if contract is not None:
            scoped = self._index.member(contract, member, _FUNCTION_DECLARATION_KINDS)
            if scoped is not None:
                return _Bound(scoped[0], "ast_call_type_inferred")
        return self._attached_call(own, member, receiver_type)

    def _attached_call(
        self, own: Optional[_ContractKey], member: str,
        receiver_type: Optional[str] = None,
    ) -> Optional[_Bound]:
        """A library function attached with ``using L for T``.

        The directives of the enclosing contract apply, and those at file
        level. ``using`` makes a library function a member of the type of its
        first parameter, so when the receiver's type is known the functions
        whose first parameter has that type are preferred. What is left must
        come from one library; two libraries are a tie, and a tie is left
        unresolved. Overloads within the library are not told apart further.
        """
        libraries = set(self._using.get("", ()))
        if own is not None:
            libraries.update(self._using.get(own[1], ()))
        declared: list[Symbol] = []
        for library_name in libraries:
            library = self._index.container(library_name)
            if library is not None:
                declared.extend(
                    self._index.declared(library, member, _FUNCTION_DECLARATION_KINDS)
                )
        if receiver_type is not None:
            wanted = _type_key(receiver_type)
            fitting = [
                function for function in declared
                if _type_key(self._index.first_parameter_type(function)) == wanted
            ]
            declared = fitting or declared
        if len({(function.path, function.name) for function in declared}) == 1:
            return _Bound(declared[-1], "ast_call", confidence=_RECEIVER_BLIND_CONFIDENCE)
        return None

    def _untyped_call(self, own: Optional[_ContractKey], member: str) -> Optional[_Bound]:
        """The receiver's type is unknown (a call result, an index, a chain).

        An attached library first. Otherwise the member is bound only when
        exactly one contract in this file declares it; with two there is
        nothing to choose by. Last, a free function of that name.
        """
        attached = self._attached_call(own, member)
        if attached is not None:
            return attached
        candidates: list[Symbol] = []
        for owner in self._index.here.containers:
            scoped = self._index.member(
                (self._path, owner), member, _FUNCTION_DECLARATION_KINDS,
            )
            if scoped is not None and not scoped[1]:
                candidates.append(scoped[0])
        if not candidates:
            loose = self._index.file_level(member, _FUNCTION_DECLARATION_KINDS)
            if loose is not None:
                candidates.append(loose[0])
        if len(candidates) == 1:
            return _Bound(candidates[0], "ast_call", confidence=_RECEIVER_BLIND_CONFIDENCE)
        return None

    def event(self, site: "tree_sitter.Node", name: "tree_sitter.Node") -> Optional[Symbol]:
        """The event an ``emit`` names: ``E`` in scope, or ``Contract.E``."""
        if name.type == "identifier":
            word = node_text(name, self._source)
            own = self._own(site)
            if own is not None:
                scoped = self._index.member(own, word, _EVENT_KINDS)
                if scoped is not None:
                    return scoped[0]
            loose = self._index.file_level(word, _EVENT_KINDS)
            return loose[0] if loose is not None else None
        if name.type == "member_expression":
            qualifier = _find_child_by_field(name, "object")
            member = _find_child_by_field(name, "property")
            if qualifier is not None and member is not None:
                named = self._index.container(node_text(qualifier, self._source))
                if named is not None:
                    scoped = self._index.member(
                        named, node_text(member, self._source), _EVENT_KINDS,
                    )
                    return scoped[0] if scoped is not None else None
        return None


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
    run_id: str, resolver: NameResolver,
    all_local_symbols: Optional[list[Symbol]] = None,
    scopes: Optional[dict[str, _FileScopes]] = None,
) -> tuple[list[Edge], dict[str, str]]:
    """Extract edges (calls, imports) from a Solidity file's parse tree.

    Returns (edges, import_aliases) where import_aliases maps alias names
    to import paths.

    The all_local_symbols parameter provides the full list of symbols for
    position-based lookup, needed to correctly resolve overloaded functions
    (where symbol_by_name would only return the last-registered overload).

    ``scopes`` is every file's scope tables from Pass 1, keyed by path. Calls,
    emits and base contracts are resolved through it (``_MemberResolver``),
    not through ``local_symbols``: that table keeps one symbol per short name
    for the whole file, so two contracts declaring ``helper`` share one
    entry. ``resolver`` is not consulted for the same reason one level up:
    its suffix match answers a bare ``helper`` with any contract's member.
    """
    edges: list[Edge] = []
    import_aliases: dict[str, str] = {}
    file_id = make_file_id("solidity", file_path)
    index = _ScopeIndex(scopes or {}, file_path, global_symbols)

    # Build position-based symbol index for overload-safe enclosing function lookup.
    # Maps (start_line, end_line) -> Symbol for functions/constructors/modifiers.
    symbols_by_span: dict[tuple[int, int], Symbol] = {}
    if all_local_symbols:
        for sym in all_local_symbols:
            if sym.kind in _BODY_BEARING_CALLABLE_KINDS and sym.span is not None:
                symbols_by_span[(sym.span.start_line, sym.span.end_line)] = sym

    # Collect `using Library for Type` directives per contract.
    # Maps contract_name -> set of library names, used to resolve
    # implicit library calls like x.add(y) -> SafeMath.add(x, y).
    using_libraries: dict[str, set[str]] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "using_directive":
            contract_name = _get_enclosing_contract(node, source) or ""
            # Extract library name from type_alias child (using Lib for Type)
            for child in node.children:
                if child.type == "type_alias":
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        lib_name = node_text(id_node, source)
                        using_libraries.setdefault(contract_name, set()).add(lib_name)
    members = _MemberResolver(index, source, file_path, using_libraries)

    # First pass: extract import aliases.
    # Gate on the ``import_directive`` node type: _extract_import_aliases scans
    # children for the first ``string`` node, which would otherwise
    # match the string argument of any call (e.g.,
    # ``require(cond, "Not owner")``) and synthesize a bogus
    # ``imports`` edge whose dst is the error-message literal.
    for node in iter_tree(tree.root_node):
            if node.type != "import_directive":
                continue
            import_path, aliases = _extract_import_aliases(node, source)
            if import_path:
                # A well-formed id, not the bare import path: finalize's
                # 4-part fallback would put a bare path in the LANG slot.
                # Same shape as the js/ts import edge (INV-dulah).
                edge = Edge.create(
                    src=file_id,
                    dst=f"solidity:{import_path}:0-0:module:module",
                    edge_type="imports",
                    evidence_type="ast_import",
                    line=node.start_point[0] + 1,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                edges.append(edge)
                import_aliases.update(aliases)

    # Second pass: extract inheritance and call edges
    for node in iter_tree(tree.root_node):
        # Contract/interface inheritance: contract A is B, C { ... }
        if node.type in ("contract_declaration", "interface_declaration"):
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                child_name = node_text(name_node, source)
                # A contract name is looked up among contracts: before 0.5 a
                # constructor was a function named as its contract, and the
                # short-name table would answer with that function.
                child_sym = index.here.containers.get(child_name)
                if child_sym:
                    for child in node.children:
                        if child.type == "inheritance_specifier":
                            parent_type_node = find_child_by_type(child, "user_defined_type")
                            if parent_type_node:
                                parent_name = node_text(parent_type_node, source)
                                parent_sym = index.container_symbol(parent_name)
                                if parent_sym:
                                    edge = Edge.create(
                                        src=child_sym.id,
                                        dst=parent_sym.id,
                                        edge_type="inherits",
                                        line=child.start_point[0] + 1,
                                        # vocab:F2 (WI-lojug): AST-derived from the
                                        # `is` (inheritance_specifier) clause — not a
                                        # call. Without this, the Edge.create default
                                        # 'ast_call_direct' would mislabel a contract
                                        # inheritance as a direct function call.
                                        evidence_type="ast_extends",
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    )
                                    edges.append(edge)

        # Function call
        elif node.type == "call_expression":
            func_node = _find_child_by_field(node, "function")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols,
                symbols_by_span=symbols_by_span,
            )
            if func_node and current_function:
                callee = _strip_wrappers(func_node)
                bound = members.call(node, callee)
                if bound is not None:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=bound.symbol.id,
                        edge_type="calls",
                        evidence_type=bound.evidence_type,
                        line=node.start_point[0] + 1,
                        confidence=bound.confidence,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta=bound.meta,
                    ))
                else:
                    # The name the unresolved edge carries: the callee as
                    # written, without a ``this.`` / ``super.`` receiver.
                    call_name = node_text(func_node, source)
                    if call_name.startswith(("super.", "this.")):
                        call_name = call_name.split(".", 1)[1]
                    edges.append(make_unresolved_edge(
                        "solidity", current_function.id, call_name,
                        node.start_point[0] + 1, PASS_ID, run_id,
                    ))

        # Emit statement: emit Transfer(...) → emits edge to event definition
        elif node.type == "emit_statement":
            # emit_statement children: "emit", expression (event name), "(", args, ")"
            event_name_node = find_child_by_type(node, "expression")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols,
                symbols_by_span=symbols_by_span,
            )
            if event_name_node and current_function:
                event_sym = members.event(node, _strip_wrappers(event_name_node))
                if event_sym:
                    # Solidity `emit Event(...)` is a function→event-symbol
                    # reference, not pub-sub-shaped: a canonical
                    # 'references' edge + meta['ref_construct']='event_emit'
                    # (ADR-0023 §6, audit-findings 0001; WI-vasik-jofiv).
                    edge = Edge.create(
                        src=current_function.id,
                        dst=event_sym.id,
                        edge_type="references",
                        line=node.start_point[0] + 1,
                        # vocab:F2 (WI-lojug): a name-resolved reference to the
                        # event symbol, not a direct call — avoid the
                        # 'ast_call_direct' default on this non-call edge.
                        evidence_type="reference",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta={"ref_construct": "event_emit"},
                    )
                    edges.append(edge)

    # Third pass: override edges.
    # For each contract that inherits from parents, connect child functions
    # to parent functions with the same unqualified name.
    for node in iter_tree(tree.root_node):
        if node.type not in ("contract_declaration", "interface_declaration"):
            continue
        name_node = find_child_by_type(node, "identifier")
        if not name_node:
            continue  # pragma: no cover - defensive
        child_contract = node_text(name_node, source)

        parent_names = _base_names(node, source)
        if not parent_names:
            continue

        # Collect functions defined in this contract
        child_functions: dict[str, Symbol] = {}
        for sym in (all_local_symbols or []):
            if (sym.kind in _FUNCTION_DECLARATION_KINDS
                    and sym.name.startswith(f"{child_contract}.")):
                unqualified = sym.name[len(child_contract) + 1:]
                child_functions[unqualified] = sym

        # Create override edges for matching parent functions
        for func_name, child_sym in child_functions.items():
            for parent_name in parent_names:
                parent_qualified = f"{parent_name}.{func_name}"
                parent_sym = (
                    local_symbols.get(parent_qualified)
                    or global_symbols.get(parent_qualified)
                )
                if parent_sym and parent_sym.kind in _FUNCTION_DECLARATION_KINDS:
                    edge = Edge.create(
                        src=child_sym.id,
                        dst=parent_sym.id,
                        edge_type="overrides",
                        line=child_sym.span.start_line if child_sym.span else 0,
                        confidence=0.85,
                        # vocab:F2 (WI-lojug): resolved by name-matching child
                        # functions against the inheritance hierarchy, not from a
                        # direct call — avoid the 'ast_call_direct' default.
                        evidence_type="type_hierarchy",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)

    return edges, import_aliases


# ---------------------------------------------------------------------------
# SolidityAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class SolidityAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Solidity smart contract files using TreeSitterAnalyzer base class.

    Stores per-file symbol lists between Pass 1 and Pass 2 to enable
    position-based enclosing function resolution. This is needed because
    Solidity supports function overloading (same name, different params),
    and the name-based symbol_by_name dict can only hold one entry per name.
    """

    lang = "solidity"
    file_patterns: ClassVar[list[str]] = ["*.sol"]
    grammar_module = "tree_sitter_solidity"

    def __init__(self) -> None:
        super().__init__()
        self._file_symbols: dict[str, list[Symbol]] = {}
        # Scope tables of every file read in Pass 1, by repo-relative path.
        # Pass 2 needs all of them at once: a base contract, a receiver's
        # type and an attached library may each be declared in another file.
        self._file_scopes: dict[str, _FileScopes] = {}

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Keep field/variable symbols OUT of the call-resolution registry (WI-jusus).

        A state variable / struct member / file-level constant is a data anchor,
        never a call target (a ``public`` state variable's auto-generated getter is
        a synthesized function, not this symbol). Excluding them prevents a
        bare-named value from clobbering a same-named function's flat registry key
        or being a suffix-matched spurious call target — closing the call-graph
        integrity vector at one chokepoint. They remain in ``analysis.symbols``
        (search / centrality / io-boundaries) since the output set is built
        independently of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        super().register_symbol(symbol, global_symbols)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Solidity file."""
        analysis = FileAnalysis()
        # The REPO-RELATIVE path: it is an identity input (the file anchor,
        # a constant's make_variable_stable_id). An absolute path would make
        # every Solidity stable_id depend on where the repo is checked out.
        self._file_scopes[rel_path] = _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id, analysis,
        )
        # Store full symbol list for position-based lookup in Pass 2
        self._file_symbols[rel_path] = list(analysis.symbols)
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a single Solidity file."""
        all_local = self._file_symbols.get(rel_path)
        edges, _aliases = _extract_edges_from_tree(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver,
            all_local_symbols=all_local,
            scopes=self._file_scopes,
        )
        return edges


_analyzer = SolidityAnalyzer()


def is_solidity_tree_sitter_available() -> bool:
    """Check if tree-sitter with Solidity grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("solidity")
def analyze_solidity(repo_root: Path) -> AnalysisResult:
    """Analyze Solidity files in a repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols, edges, and analysis run info.
    """
    return _analyzer.analyze(repo_root)
