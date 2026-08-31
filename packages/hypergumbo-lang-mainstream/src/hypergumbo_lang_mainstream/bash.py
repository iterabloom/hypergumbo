# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bash/shell script analyzer using tree-sitter.

This analyzer extracts functions, exported variables, aliases, and source statements
from Bash and shell scripts. It also emits a per-file ``file`` pseudo-node Symbol
stamped with a ``shell_script`` entrypoint concept (INV-tajap), since every parsed
bash/.sh/.bash file is treated as an executable entry point and is consumed by
entrypoints.py as a SHELL_SCRIPT entrypoint. It uses tree-sitter-bash for parsing
when available, falling back gracefully when the grammar is not installed.

Node types handled:
- function_definition: Both 'function name()' and 'name()' styles
- declaration_command with 'export': Exported variables
- command with 'alias': Alias definitions
- command with 'source' or '.': Source/import statements
- command: Function calls (when command_name matches a known function)

Two-pass analysis:
- Pass 1: Extract all symbols (functions, exports, aliases) from all files
- Pass 2: Resolve function calls using global symbol registry

How It Works
------------
Uses the TreeSitterAnalyzer base class for two-pass orchestration:
1. extract_symbols_from_file: extracts functions, exports, aliases
2. register_symbol: registers function and file symbols for cross-file resolution (the file pseudo-node, per INV-kokaj, so top-level calls outside any function can be attributed to it)
3. extract_edges_from_file: resolves source/dot imports and function calls
4. _find_source_files: overridden for shebang-based file discovery

Why override _find_source_files: Bash scripts can have no extension but
a shebang line (#!/bin/bash), requiring special detection beyond glob patterns.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files, is_excluded
from hypergumbo_core.ir import (
    AnalysisRun,
    Edge,
    ExternalRef,
    Span,
    Symbol,
    make_pass_id,
)
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
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("bash")

# WI-javoh: shell BUILTINS run in-process — they are NOT external-program
# launches, so they never emit a ``command_launch`` subprocess edge. A command
# surviving this denylist that is also neither a defined shell function nor a
# resolver-resolved symbol is an EXTERNAL PROGRAM launch (curl/rm/git/grep/…),
# i.e. a subprocess crossing (arbitrary code execution). This is a CLASSIFICATION
# gate (launch vs builtin), NOT an I/O-relevance curation: every launch is a
# subprocess crossing per ADR-0016 §1's "all launches risky" invariant, so the
# gate must not try to distinguish curl from git (no clean invariant separates
# them — the same hand-curation the high_risk retirement / WI-tijos closed). The
# high VOLUME of low-signal launches (git/grep/sed) is handled downstream as a
# COUNT question: ``command_launch`` is a DISCLOSED cohort excluded from
# ``total_io_edges`` (io_boundary.py), mirroring ``external_potential`` — the
# established count-vs-disclose doctrine (WI-huhit/WI-foduh), never hidden.
SHELL_BUILTINS: frozenset[str] = frozenset({
    # POSIX special builtins + control
    ":", ".", "source", "break", "continue", "eval", "exec", "exit",
    "export", "readonly", "return", "set", "shift", "times", "trap", "unset",
    # test / conditionals
    "[", "[[", "test", "true", "false",
    # variable / arithmetic / input
    "declare", "typeset", "local", "let", "read", "readarray", "mapfile",
    "getopts", "printf", "echo", "unalias", "alias",
    # directory / job control
    "cd", "pwd", "pushd", "popd", "dirs", "jobs", "fg", "bg", "kill",
    "disown", "suspend", "wait", "umask",
    # introspection / shell control
    "command", "builtin", "type", "hash", "enable", "help", "history", "fc",
    "bind", "caller", "complete", "compgen", "compopt", "logout", "shopt",
    "time",
})


def is_bash_tree_sitter_available() -> bool:
    """Check if tree-sitter and bash grammar are available."""
    return _analyzer._check_grammar_available()


def _is_bash_shebang(first_line: str) -> bool:
    """Check if a shebang line indicates a bash/sh script."""
    if not first_line.startswith("#!"):
        return False
    shebang = first_line[2:].strip()
    # Match /bin/bash, /usr/bin/bash, /bin/sh, /usr/bin/env bash, etc.
    bash_patterns = ["/bash", "/sh", "env bash", "env sh"]
    return any(p in shebang for p in bash_patterns)


def find_bash_files(root: Path) -> list[Path]:
    """Find all Bash/shell script files in a directory tree, excluding vendor dirs.

    Identifies files by:
    - .sh extension
    - .bash extension
    - No extension but with bash/sh shebang
    """
    bash_files: list[Path] = []

    # Get .sh and .bash files using find_files (respects DEFAULT_EXCLUDES)
    bash_files.extend(find_files(root, ["*.sh", "*.bash"]))

    # For files without extension, check shebang.
    # Use the global FileIndex if available to avoid a redundant walk.
    from hypergumbo_core.discovery import get_file_index
    file_index = get_file_index()
    if file_index is not None and file_index.repo_root == root:
        candidates = (f for f in file_index.all_files() if f.suffix == "")
    else:
        candidates = (
            p for p in root.rglob("*")
            if p.is_file() and p.suffix == "" and not is_excluded(p, root)
        )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline()
                if _is_bash_shebang(first_line):
                    bash_files.append(path)
        except (OSError, IOError):  # pragma: no cover
            pass

    return bash_files


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_definition node."""
    word_node = find_child_by_type(node, "word")
    if word_node:
        return node_text(word_node, source)
    return None  # pragma: no cover


def _extract_alias_info(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract alias name from alias command.

    alias name='value' or alias name="value"
    """
    children = [c for c in node.children if c.type not in ("command_name",)]
    if not children:
        return None  # pragma: no cover

    for child in children:
        if child.type == "word":
            text = node_text(child, source)
            if "=" in text:
                return text.split("=")[0]
            return text  # pragma: no cover - unusual alias format
        elif child.type == "concatenation":
            first = find_child_by_type(child, "word")
            if first:
                text = node_text(first, source)
                # Remove trailing = if present (alias ll='value' parses as 'll=')
                if text.endswith("="):
                    return text[:-1]
                return text  # pragma: no cover - unusual alias format

    return None  # pragma: no cover


# INV-nular. Variables BASH ITSELF assigns, from the shell manual's "Shell
# Variables" section. A parameter expansion of one of these is not a read of
# anything the caller supplied, so it is not an environment read — the same
# reason INV-jurif already excludes a name the SCRIPT assigns. Splitting them
# by SETTER rather than by apparent sensitivity is deliberate: a
# "which names are secrets" list is exactly the curated enumeration the
# env_read row refuses, wrong the moment a repo invents a name and wrong
# silently.
#
# NOT AN EXHAUSTIVE MODEL OF BASH. It is the documented shell-variable set;
# a name bash gains in a future release reads as an environment variable until
# it is added here, which is the same fail-open direction the rest of this
# analyzer takes for sources.
_SHELL_STATE_NAMES: frozenset[str] = frozenset({
    "_", "BASH", "BASHOPTS", "BASHPID", "BASH_ALIASES", "BASH_ARGC",
    "BASH_ARGV", "BASH_ARGV0", "BASH_CMDS", "BASH_COMMAND",
    "BASH_EXECUTION_STRING", "BASH_LINENO", "BASH_LOADABLES_PATH",
    "BASH_MONOSECONDS", "BASH_REMATCH", "BASH_SOURCE", "BASH_SUBSHELL",
    "BASH_TRAPSIG", "BASH_VERSINFO", "BASH_VERSION", "COMP_CWORD", "COMP_KEY",
    "COMP_LINE", "COMP_POINT", "COMP_TYPE", "COMP_WORDBREAKS", "COMP_WORDS",
    "COPROC", "DIRSTACK", "EPOCHREALTIME", "EPOCHSECONDS", "FUNCNAME",
    "GROUPS", "HISTCMD", "LINENO", "MAPFILE", "OPTARG", "OPTIND",
    "PIPESTATUS", "PPID", "RANDOM", "READLINE_ARGUMENT", "READLINE_LINE",
    "READLINE_MARK", "READLINE_POINT", "REPLY", "SECONDS", "SHELLOPTS",
    "SHLVL", "SRANDOM",
})

# The bash-assigned names that describe the HOST or the USER rather than the
# shell's own bookkeeping. INV-tutar split `host_info_read` out of `env_read`
# in exactly this situation one language over: env_read auto-derives the
# `host_secret` taint label, so a host DESCRIPTION read counted as a credential
# flow and made every host-secret-* claim fire on it. Python's catalogue puts
# the syscall equivalents of every one of these under host_info_read already
# (`getcwd`, `getuid`, `uname`), so this is the shipped classification reached
# through bash's syntax rather than a new judgement.
_HOST_DESCRIPTION_NAMES: frozenset[str] = frozenset({
    "EUID", "HOSTNAME", "HOSTTYPE", "MACHTYPE", "OLDPWD", "OSTYPE", "PWD",
    "UID",
})


#: Redirection operators under which the SHELL performs a write. ``<`` is a
#: read and is excluded: this map answers "what did the shell put there".
_WRITE_REDIRECT_OPS: frozenset[str] = frozenset({">", ">|", ">>"})


def _expansion_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Every variable name expanded anywhere under ``node``.

    Walks the whole subtree, so a name inside a command substitution counts —
    ``$(basename "$X")`` really does carry ``X`` into the result. That matches
    the env-read rule one level up, which is also purely syntactic over
    expansions: a subprocess that reads the environment ITSELF (``$(printenv
    S)``) is invisible to both, so this introduces no gap the source side does
    not already have.
    """
    return [
        node_text(v, source)
        for n in iter_tree(node)
        if n.type in ("simple_expansion", "expansion")
        for v in (find_child_by_type(n, "variable_name"),)
        if v is not None
    ]


def _redirect_origin_names(
    tree: "tree_sitter.Tree",
    source: bytes,
    assigned_names: set[str],
) -> dict[int, tuple[str, ...]]:
    """Per write redirect, the EXTERNALLY-DERIVED names the shell can put there.

    WI-zovuz. bash carries no dataflow (``dataflow_capable=False``), so a taint
    finding in a shell script was call-graph reachability alone: "this file
    reads the environment somewhere AND reaches a function that writes
    somewhere". Measured over 15 cohort repos, 186 environment names are read
    in the 69 files that also carry a write redirect and only 28 of them can
    reach what the shell writes — 48 of those files have NO name that reaches
    any redirect, so their redirect-sink findings rested on nothing.

    WHAT THE SHELL CONTRIBUTES, and it is exactly three things:

    1. the redirect's TARGET operand — ``> "$OUT"`` chooses *where* with a
       value this program holds;
    2. a HEREDOC body, which the shell expands itself before handing the
       result to the command's stdin. Missing this is a false ALL-CLEAR over
       ``cat > cfg <<EOF\npassword = $DB_PASSWORD\nEOF`` — every stage is
       external there and the secret is still written. A quoted delimiter
       (``<<'EOF'``) suppresses expansion, and the grammar already reflects
       that by emitting no expansion node;
    3. the ARGUMENTS of every producing stage. Deliberately every stage, not
       only shell builtins: measured, ``sed -E "s#x#${v}#" f > out``
       interpolates an in-process value THROUGH an external command, and
       ``echo "$SECRET" | base64 -d > cert`` carries a real signing
       certificate from stage one while the stage feeding the redirect is
       external. Both are why the byte-producer question ("is the writer
       external?") is the WRONG one; this asks whether a NAME can reach.

    Returns a mapping keyed by the ``file_redirect`` node's start byte, which
    is unique per site — deliberately not by line, because INV-vukiv is the
    measurement that two redirects on one line must not collapse.
    """
    # A PARSE FAILURE IS NOT A PROOF OF EMPTINESS, and the damage is not
    # local. tree-sitter recovers from a shape it cannot model with an ERROR
    # node, and an unparsed heredoc body is then attached NOWHERE — cilium's
    # `<<EOF cat >/etc/dnsmasq.conf` lands the ERROR on a SIBLING statement
    # while the redirect itself parses cleanly, so a parent-scoped check reads
    # it as "no name reaches" over a body that writes a value read from the
    # environment. Answering for the whole file only when the whole file
    # parsed is the only scope that holds. Measured: 11 of the 212 cohort bash
    # files carrying a redirect contain an ERROR or MISSING node (5.2%), so
    # the conservative scope costs almost nothing.
    if any(n.type == "ERROR" or n.is_missing
           for n in iter_tree(tree.root_node)):
        return {}

    def _is_external(name: str) -> bool:
        # The same discriminator the env-read branch uses one level up: a name
        # this file assigns is not external, and neither is one BASH assigns.
        # Host-DESCRIPTION names stay in (INV-tutar routes them to a different
        # label, but they are still a taint source, so a gate built on this set
        # must not let them through unseen).
        return bool(
            name
            and name not in assigned_names
            and (name[0].isalpha() or name[0] == "_")
            and name not in _SHELL_STATE_NAMES
        )

    derived: dict[str, set[str]] = {}
    for node in iter_tree(tree.root_node):
        # A LOOP VARIABLE IS A BINDING, and missing it is a false ALL-CLEAR.
        # `for f in $SECRET_LIST; do echo "$f" > out; done` writes the secret,
        # and the env-read rule one level up already treats `f` as ASSIGNED
        # (``for_statement`` is in its own set), so without this clause `f`
        # would be neither an external name nor derived from one, and the
        # redirect would report reaching nothing. Found by reading a removed
        # row back against source, which is the only reason it is here.
        if node.type not in ("variable_assignment", "for_statement"):
            continue
        target = find_child_by_type(node, "variable_name")
        if target is None:  # pragma: no cover - grammar always yields one
            continue
        rhs: list[str] = []
        for child in node.children:
            if child is target or child.type == "=":
                continue
            # A for-statement's body is not part of the binding — only the
            # word list it iterates is. Taking the body too would credit every
            # name mentioned anywhere in the loop.
            if node.type == "for_statement" and child.type in ("do_group",
                                                               "compound_statement"):
                continue
            rhs.extend(_expansion_names(child, source))
        derived.setdefault(node_text(target, source), set()).update(rhs)

    # Positional binding: `download "$A" "$B"` binds $1/$2 inside download.
    # Whole-file and union-over-call-sites, matching the assigned_names rule's
    # own reason — bash is dynamically scoped, so a per-call-site answer would
    # claim a precision the language does not offer.
    functions = {
        name: node
        for node in iter_tree(tree.root_node)
        if node.type == "function_definition"
        for name in (_extract_function_name(node, source),)
        if name
    }
    positional: dict[str, dict[str, set[str]]] = {}
    for node in iter_tree(tree.root_node):
        if node.type != "command":
            continue
        name_node = find_child_by_type(node, "command_name")
        if name_node is None:  # pragma: no cover - grammar always yields one
            # tree-sitter-bash synthesizes a `command_name` for every
            # `command`, inserting a MISSING word rather than omitting the
            # node (verified on `FOO=bar > out`). The `word` guard below is
            # the one that actually fires.
            continue
        word = find_child_by_type(name_node, "word")
        if word is None:
            continue
        called = node_text(word, source)
        if called not in functions:
            continue
        args = [c for c in node.children if c is not name_node]
        slot = positional.setdefault(called, {})
        for index, arg in enumerate(args, start=1):
            slot.setdefault(str(index), set()).update(
                _expansion_names(arg, source))

    def _enclosing_function(node: "tree_sitter.Node") -> Optional[str]:
        current = node.parent
        while current is not None:
            if current.type == "function_definition":
                return _extract_function_name(current, source)
            current = current.parent
        return None

    def _origins(name: str, fn: Optional[str],
                 seen: frozenset[str]) -> set[str]:
        if name in seen:  # a = "$b"; b = "$a" must terminate
            return set()
        seen = seen | {name}
        if _is_external(name):
            return {name}
        if name.isdigit() and fn is not None:
            return {
                origin
                for bound in positional.get(fn, {}).get(name, ())
                for origin in _origins(bound, fn, seen)
            }
        return {
            origin
            for parent in derived.get(name, ())
            for origin in _origins(parent, fn, seen)
        }

    out: dict[int, tuple[str, ...]] = {}
    for node in iter_tree(tree.root_node):
        if node.type != "file_redirect":
            continue
        if not any(c.type in _WRITE_REDIRECT_OPS for c in node.children):
            continue
        parent = node.parent
        fn = _enclosing_function(node)
        names: list[str] = []
        target = next(
            (c for c in node.children
             if c.type in ("word", "string", "raw_string", "concatenation",
                           "simple_expansion", "expansion")),
            None,
        )
        if target is not None:
            names.extend(_expansion_names(target, source))
        if parent is not None:
            for sibling in parent.children:
                # Skip EVERY file_redirect, not just this one by identity:
                # tree-sitter hands out a fresh Node wrapper per access, so an
                # `is` comparison silently never matches. Skipping the type is
                # also the more correct rule — in `cmd > out 2> "$LOG"` the
                # second redirect's target is not what the first one writes.
                if sibling.type == "file_redirect":
                    continue
                names.extend(_expansion_names(sibling, source))
        origins: set[str] = set()
        for name in names:
            origins |= _origins(name, fn, frozenset())
        out[node.start_byte] = tuple(sorted(origins))
    return out


class BashAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Bash/shell script analyzer.

    Uses tree-sitter-bash to parse .sh, .bash, and extensionless shebang files.
    Extracts functions, exported variables, aliases, source/dot imports, and
    function call edges.

    Overrides ``_find_source_files`` because bash scripts can lack file extensions
    and require shebang-line detection (#!/bin/bash).

    Overrides ``register_symbol`` to only register function symbols for cross-file
    call resolution (exports and aliases are file-local).
    """

    lang = "bash"
    file_patterns: ClassVar[list[str]] = ["*.sh", "*.bash"]
    grammar_module = "tree_sitter_bash"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield bash source files, including extensionless shebang scripts."""
        yield from find_bash_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single Bash file.

        Detects function definitions (both 'function name()' and 'name()' styles),
        exported variables (export VAR=value), and alias definitions.
        """
        analysis = FileAnalysis()

        # WI-bokab (v7): file-identity anchor for this file's symbols.
        # ``rel_path`` is the repo-relative path (the base-class extract
        # contract passes it). Folded into compute_stable_id's
        # containing_stable_id slot so same-name functions in different files
        # hash distinctly. Computed once and reused across this file's symbols.
        file_anchor = self._file_anchor(rel_path)

        # INV-kokaj: emit the file pseudo-node as kind="file" with the
        # canonical file-id shape so the orchestrator file-symbol synthesizer
        # dedups against it (existing_ids check). Before this fix, every
        # bash file emitted TWO Symbols for the same path — kind="module"
        # here AND kind="file" from the synthesizer when any edge targeted
        # the file id. File-kind is the cross-language canonical for
        # "this file" (see analyze.base.make_file_id); this Symbol provides
        # an enclosing scope for module-level edges (function calls outside
        # any function definition) so the script remains reachable in slice
        # traversal.
        end_line = tree.root_node.end_point[0] + 1
        # INV-tajap: every bash file we successfully parse is an executable
        # entry point — either it has a shebang (find_bash_files's discovery
        # criterion for extensionless files) or it has a .sh / .bash extension
        # (executable by convention). Stamp a ``shell_script`` concept on the
        # file Symbol so entrypoints.py's concept-driven pipeline picks it up
        # as a SHELL_SCRIPT entrypoint (parallel to Python's ``main_guard``).
        module_symbol = Symbol(
            id=make_file_id("bash", rel_path),
            name=rel_path,
            kind="file",
            language="bash",
            path=rel_path,
            span=Span(start_line=1, end_line=end_line, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={"concepts": [{"concept": "shell_script", "framework": "bash"}]},
        )
        analysis.symbols.append(module_symbol)

        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                func_name = _extract_function_name(node, source)
                if func_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("bash", rel_path, start_line, end_line, func_name, "function")

                    # Bash functions don't have formal parameters - they use $1, $2, etc.
                    # Signature is always "()" since there's no parameter declaration syntax
                    symbol = Symbol(
                        id=symbol_id,
                        name=func_name,
                        kind="function",
                        language="bash",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        signature="()",
                        # WI-pulor: end_line - start_line + 1 matches the
                        # convention documented at ir.py:349. Without this,
                        # bash function symbols render as ``? LOC`` in
                        # dead-code-maybe output.
                        line_span=end_line - start_line + 1,
                        # INV-loguk: McCabe complexity over the bash grammar's
                        # if/elif/for/while/case decision points.
                        cyclomatic_complexity=compute_cyclomatic_complexity(
                            node, "bash",
                        ),
                        stable_id=self.compute_stable_id(
                            node, kind="function", name=func_name,
                            file_stable_id=file_anchor,
                        ),
                    )
                    analysis.symbols.append(symbol)
                    analysis.node_for_symbol[symbol.id] = node
                    analysis.symbol_by_name[func_name] = symbol

            elif node.type == "declaration_command":
                export_node = find_child_by_type(node, "export")
                if export_node:
                    var_node = find_child_by_type(node, "variable_assignment")
                    if var_node:
                        name_node = find_child_by_type(var_node, "variable_name")
                        if name_node:
                            var_name = node_text(name_node, source)
                            start_line = node.start_point[0] + 1
                            symbol_id = make_symbol_id("bash", rel_path, start_line, start_line, var_name, "export")

                            symbol = Symbol(
                                id=symbol_id,
                                name=var_name,
                                kind="export",
                                language="bash",
                                path=rel_path,
                                span=Span(
                                    start_line=start_line,
                                    end_line=start_line,
                                    start_col=node.start_point[1],
                                    end_col=node.end_point[1],
                                ),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            )
                            analysis.symbols.append(symbol)
                            analysis.node_for_symbol[symbol.id] = node

            elif node.type == "command":
                cmd_name_node = find_child_by_type(node, "command_name")
                if cmd_name_node:
                    word_node = find_child_by_type(cmd_name_node, "word")
                    if word_node:
                        cmd_name = node_text(word_node, source)

                        if cmd_name == "alias":
                            alias_name = _extract_alias_info(node, source)
                            if alias_name:
                                start_line = node.start_point[0] + 1
                                symbol_id = make_symbol_id("bash", rel_path, start_line, start_line, alias_name, "alias")

                                symbol = Symbol(
                                    id=symbol_id,
                                    name=alias_name,
                                    kind="alias",
                                    language="bash",
                                    path=rel_path,
                                    span=Span(
                                        start_line=start_line,
                                        end_line=start_line,
                                        start_col=node.start_point[1],
                                        end_col=node.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                )
                                analysis.symbols.append(symbol)
                                analysis.node_for_symbol[symbol.id] = node

        return analysis

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register only function symbols for cross-file call resolution.

        Exports and aliases are file-local; only functions can be called
        from other files via 'source' + function-name invocation.

        INV-kokaj: ``file`` joins ``function`` so the file pseudo-node
        (formerly ``kind="module"``) remains reachable from the global
        registry — Pass 2 looks up the file Symbol by name to attribute
        top-level calls (calls outside any function definition) to it.
        """
        if symbol.kind in ("function", "file"):
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract edges from a file using global symbol knowledge.

        Detects:
        - source/dot imports (source utils.sh, . ./local.sh)
        - function calls (both local and cross-file via resolver)
        """
        edges: list[Edge] = []
        _caller_path = str(file_path)
        file_id = make_file_id("bash", rel_path)

        # INV-kokaj: look up the file pseudo-node by its new canonical
        # name (the rel_path the Pass 1 emitter stamped) for top-level
        # call attribution.
        module_symbol: Symbol | None = global_symbols.get(rel_path)

        def _get_enclosing_function(node: "tree_sitter.Node") -> Optional[Symbol]:
            """Walk up the tree to find enclosing function."""
            current = node.parent
            while current is not None:
                if current.type == "function_definition":
                    func_name = _extract_function_name(current, source)
                    if func_name and func_name in local_symbols:
                        return local_symbols[func_name]
                current = current.parent
            return None  # pragma: no cover - defensive

        # WI-javoh: dedup external-command launches per (caller, command_name).
        # A script that calls ``git`` 171 times from one function is ONE launch
        # relationship, not 171 — hygiene that keeps the disclosed
        # ``command_launch`` cohort honest (an invocation-count multiplicity,
        # not a distinct boundary each time).
        seen_launches: set[tuple[str, str]] = set()

        # INV-jurif: names ASSIGNED anywhere in this file. A name that is
        # expanded but never assigned here came from the environment — the
        # conservative discriminator available without cross-file analysis.
        # Deliberately whole-file rather than per-scope: bash assignment is
        # dynamically scoped and a name assigned in one function is visible in
        # another it calls, so a per-scope rule would call an assigned name an
        # env read and over-report. Erring toward FEWER sources is the right
        # direction for a taint SOURCE — a missed source under-reports, an
        # invented one manufactures findings that do not exist.
        assigned_names: set[str] = set()
        for _n in iter_tree(tree.root_node):
            if _n.type in ("variable_assignment", "for_statement"):
                _name = find_child_by_type(_n, "variable_name")
                if _name is not None:
                    assigned_names.add(node_text(_name, source))

        # WI-zovuz: which externally-derived names can reach what the SHELL
        # writes at each redirect. Computed once per file — the derivation
        # closure is whole-file, so recomputing it per redirect would be the
        # same answer at N times the cost.
        redirect_origins = _redirect_origin_names(tree, source, assigned_names)

        for node in iter_tree(tree.root_node):
            if node.type in ("simple_expansion", "expansion"):
                # INV-jurif: `$API_KEY` reads the environment, and bash emitted
                # NOTHING for it — so bash carried 0 taint sources, failed the
                # both-halves predicate, and made every claim on any repo
                # containing a shell script `inconclusive` (INV-dabuf's 18/18).
                # The SINK half shipped with INV-vavup; this is the source half
                # it named as "taint-support SECOND".
                #
                # Emitted as module_attr_ref on the shipped os.environ
                # precedent: an environment read is an attribute access, not a
                # call. One catalogue row matches every variable, exactly as
                # `os.environ` matches any subscript of it — enumerating
                # variable names would be a curated list that is wrong the
                # moment a repo invents a name.
                name_node = find_child_by_type(node, "variable_name")
                if name_node is None:
                    continue
                var_name = node_text(name_node, source)
                if not var_name or var_name in assigned_names:
                    continue
                # Positional/special params ($1, $?, $$) are shell state, not
                # environment.
                if not var_name[0].isalpha() and var_name[0] != "_":
                    continue
                # INV-nular. The comment here used to claim that "$PWD-style
                # shell-maintained names" were excluded while the code filtered
                # only the non-alphabetic first character, so $BASH_SOURCE,
                # $RANDOM and $LINENO each derived a host_secret taint SOURCE.
                #
                # THE RULE IS WHO SET THE VARIABLE, not whether the name looks
                # sensitive — a sensitivity list is the curated name list the
                # env_read row exists to refuse (it is wrong the moment a repo
                # invents a name, and wrong in the SILENT direction). It is
                # INV-jurif's own discriminator one level out: a name the
                # SCRIPT assigns is not an environment read, and neither is a
                # name BASH assigns. $HOME stays an env read because bash does
                # not set it, it inherits it.
                if var_name in _SHELL_STATE_NAMES:
                    continue
                if var_name in _HOST_DESCRIPTION_NAMES:
                    # INV-tutar, one language over: env_read auto-derives the
                    # host_secret label, so routing OSTYPE through it made a
                    # host DESCRIPTION read count as a credential flow. The
                    # read is real and stays reported — reclassified, not
                    # suppressed.
                    dst = "bash:shell:0-0:shell.hostinfo:attribute"
                else:
                    dst = "bash:env:0-0:env.environ:attribute"
                owner = _get_enclosing_function(node) or module_symbol
                if owner is None:  # pragma: no cover - defensive
                    continue
                edges.append(Edge.create(
                    src=owner.id,
                    dst=dst,
                    edge_type="module_attr_ref",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="module_attribute_reference",
                    meta={"env_var": var_name},
                ))
            elif node.type == "file_redirect":
                # INV-vavup: the SHELL'S OWN write, which is a different
                # surface from a launched program's I/O. ADR-0016 forbids
                # attributing curl's network activity to the script that ran
                # it; it says nothing about `echo x > file`, where the shell
                # itself opens and writes the target (echo is a builtin, and
                # even for an external command the redirection is established
                # before exec). Attributing fs_write here is the same standing
                # os.remove has in python.
                #
                # ANALYZER EMITS, CATALOGUE CLASSIFIES — the shipped
                # module_attr_ref precedent (os.environ is an attribute
                # access, not a call, and reaches the boundary pipeline as a
                # synthesized edge). The row lives in io_primitives/bash.yaml.
                # Compute the enclosing symbol HERE rather than reading the
                # `current_function` the command branch happens to have left
                # behind: that binding depends on tree-walk order, so a
                # redirect reached before any command would read a stale
                # value or none at all.
                edge = self._redirect_edge(
                    node, source,
                    _get_enclosing_function(node) or module_symbol, run,
                    redirect_origins.get(node.start_byte),
                )
                if edge is not None:
                    edges.append(edge)
            elif node.type == "command":
                cmd_name_node = find_child_by_type(node, "command_name")
                if cmd_name_node:
                    word_node = find_child_by_type(cmd_name_node, "word")
                    if word_node:
                        cmd_name = node_text(word_node, source)
                        line = node.start_point[0] + 1

                        # Handle source/. commands
                        if cmd_name in ("source", "."):
                            words = [
                                c for c in node.children if c.type == "word" and c != word_node
                            ]
                            if words:
                                sourced_path = node_text(words[0], source)
                                # WI-hugom: previously dst was the raw
                                # sourced_path (e.g. '/etc/kafka/docker/launch'),
                                # which fell through ir._parse_dangling_id and
                                # stuffed the path into the language slot of the
                                # synthesized boundary node (observed on kafka
                                # cohort-001/iter-001 as 8 such nodes). Construct
                                # a properly-formed 5-part dst id; sourced_path
                                # preserved on edge.meta for any future consumer
                                # that needs path-based resolution.
                                dst_basename = (
                                    sourced_path.rsplit("/", 1)[-1]
                                    or sourced_path
                                )
                                dst_id = (
                                    f"bash:{sourced_path}:0-0:{dst_basename}:file"
                                )
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=dst_id,
                                    edge_type="sources",
                                    line=line,
                                    evidence_type="source_statement",
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={"sourced_path": sourced_path},
                                ))

                        # Track function calls
                        else:
                            current_function = _get_enclosing_function(node) or module_symbol
                            if current_function is not None:
                                if cmd_name in local_symbols:
                                    callee = local_symbols[cmd_name]
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=line,
                                        evidence_type="ast_call",
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        meta={"call_construct": "function"},
                                    ))
                                else:
                                    # Check global symbols via resolver
                                    lookup_result = resolver.lookup(cmd_name, caller_path=_caller_path)
                                    if lookup_result.found and lookup_result.symbol is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=lookup_result.symbol.id,
                                            edge_type="calls",
                                            line=line,
                                            evidence_type="ast_call",
                                            confidence=0.80 * lookup_result.confidence,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            meta={"call_locality": "cross_file"},
                                        ))
                                    elif cmd_name not in SHELL_BUILTINS:
                                        # WI-javoh: an EXTERNAL PROGRAM launch —
                                        # neither a defined shell function nor a
                                        # resolver-resolved symbol nor a builtin.
                                        # A subprocess crossing (arbitrary code
                                        # execution). Prestamp io_boundary so it
                                        # rides the io-boundary aggregation as a
                                        # DISCLOSED ``command_launch`` chain
                                        # (excluded from total_io_edges). Opacity
                                        # is carried by is_resolved=False (the
                                        # launched program has no in-tree source);
                                        # dst_ref gives the external identity.
                                        key = (current_function.id, cmd_name)
                                        if key not in seen_launches:
                                            seen_launches.add(key)
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=f"bash:{cmd_name}:0-0:{cmd_name}:unresolved",
                                                dst_ref=ExternalRef(
                                                    lang="bash",
                                                    module_path=cmd_name,
                                                    name=cmd_name,
                                                ),
                                                edge_type="calls",
                                                line=line,
                                                evidence_type="ast_call",
                                                is_resolved=False,
                                                origin=PASS_ID,
                                                origin_run_id=run.execution_id,
                                                meta={
                                                    "io_boundary": "command_launch",
                                                    "io_primitive": cmd_name,
                                                    "call_construct": "function",
                                                },
                                            ))

        return edges

    # POSIX redirection operators, by the boundary they cross and the mode
    # they open the target in. `>` and `>>` are ONE primitive at two modes,
    # not two primitives — the truncate/append distinction rides on io_mode
    # (the builtins.open pattern) so it cannot become an INV-zumin row-order
    # collision. `>|` is `>` with noclobber defeated: same write, same mode.
    # The MODE only. Which BOUNDARY an operator crosses is the catalogue's
    # call (io_primitives/bash.yaml), not the analyzer's — ADR-0016's
    # amendment puts the operator in data so a divergent shell (tcsh, fish)
    # can be given different rows without touching this file. `>|` is `>`
    # with noclobber defeated: same write, same mode.
    _REDIRECT_OPS: ClassVar[dict[str, str]] = {
        ">": "w", ">|": "w", ">>": "a", "<": "r",
    }

    #: Targets that name a KERNEL DEVICE rather than a place in a filesystem.
    #: INV-nular: `echo "$API_KEY" > /dev/null` was measured returning
    #: `violated` (rc 1) against `{boundary: fs_write, must_not_exist: true}`,
    #: and nothing is written to any filesystem — the kernel discards the
    #: bytes, so no observation anywhere differs because the redirect ran. That
    #: is what makes the finding VACUOUS rather than merely imprecise.
    #:
    #: `/dev/null` is separated from the STANDARD STREAMS because they are not
    #: the same fact. A write to `/dev/stderr` really does leave the process
    #: and really can publish a secret — it is a `logging` crossing, not a
    #: filesystem one — so it is MARKED here and deliberately not reclassified;
    #: doing that needs the `logging`-vs-`fs_write` decision the haskell
    #: hPutStrLn rows are already waiting on (INV-vaduk shape 4).
    _NULL_DEVICE_TARGETS: ClassVar[frozenset[str]] = frozenset({
        "/dev/null",
    })
    _STD_STREAM_TARGETS: ClassVar[frozenset[str]] = frozenset({
        "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty", "/dev/console",
    })

    @classmethod
    def _target_kind(cls, target: str, resolved: bool) -> str:
        """Classify a redirect target for the boundary pipeline.

        The catalogue cannot answer this: `redirect.>` is ONE row, and whether
        it crosses a filesystem boundary depends on the target at the CALL
        SITE. That is exactly the shape `io_mode` already has — `open(p)` and
        `open(p, 'w')` are one row and two boundaries — so the analyzer stamps
        the discriminator and `io_boundary.classify_call` reads it, which is
        the one place both the boundary tagger and the coverage gate inherit.
        """
        if not resolved:
            return "unresolved"
        if target in cls._NULL_DEVICE_TARGETS:
            return "null_device"
        if (target in cls._STD_STREAM_TARGETS
                or target.startswith("/dev/fd/")):
            return "std_stream"
        return "host_path"

    def _redirect_edge(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        enclosing: Optional[Symbol],
        run: AnalysisRun,
        origin_names: Optional[tuple[str, ...]] = None,
    ) -> Optional[Edge]:
        """One synthesized edge for a `file_redirect`, or None.

        Returns None only for operators this catalog does not model (here-doc
        bodies, fd duplication such as `2>&1`, and the process-substitution
        forms) — NOT for an unresolvable target. A redirect whose target is a
        variable (`> "$OUT"`) still emits, marked unresolved: the write
        happened, and staying silent about it is the fail-open direction this
        area keeps paying for. An honest "wrote somewhere I cannot name" is
        strictly better than nothing.
        """
        op_node = next(
            (c for c in node.children if c.type in self._REDIRECT_OPS), None,
        )
        if op_node is None or enclosing is None:
            return None
        operator = op_node.type
        mode = self._REDIRECT_OPS[operator]

        target_node = next(
            (c for c in node.children
             if c.type in ("word", "string", "raw_string",
                           "concatenation", "simple_expansion", "expansion")),
            None,
        )
        target = node_text(target_node, source) if target_node is not None else ""
        target = target.strip('"\'')
        # A target naming a variable cannot be resolved to a path here; the
        # DDG is where that would be answered, not the analyzer.
        resolved = bool(target) and "$" not in target

        return Edge.create(
            src=enclosing.id,
            dst=f"bash:redirect:0-0:{operator}:unresolved",
            dst_ref=ExternalRef(
                lang="bash", module_path="redirect", name=operator,
            ),
            edge_type="calls",
            line=node.start_point[0] + 1,
            evidence_type="ast_call",
            is_resolved=False,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={
                "io_primitive": f"redirect.{operator}",
                "io_mode": mode,
                "redirect_target": target or "<unresolved>",
                "redirect_target_resolved": resolved,
                "io_target_kind": self._target_kind(target, resolved),
                # ABSENT, not empty, when the closure could not answer: an
                # empty list is a PROOF the consumer acts on.
                **({} if origin_names is None
                   else {"redirect_origin_names": list(origin_names)}),
            },
        )


_analyzer = BashAnalyzer()


@register_analyzer("bash", find_files=find_bash_files)
def analyze_bash(root: Path) -> AnalysisResult:
    """Analyze Bash/shell scripts in a directory.

    Uses tree-sitter-bash for parsing. Falls back gracefully if not available.
    """
    return _analyzer.analyze(root)
