"""Build-target extraction from language-specific manifest files.

Extracts ``defines_target`` edges from build/project manifest files that
specify entry points. Covers 11 config formats using simple regex/text
parsing (no tree-sitter grammars required):

- Gradle: ``build.gradle(.kts)`` — ``mainClass``/``mainClassName``
- C#: ``*.csproj`` — ``<StartupObject>``
- Dart: ``pubspec.yaml`` — ``executables`` mapping
- Swift: ``Package.swift`` — ``.executableTarget()``
- Haskell: ``*.cabal`` — ``executable`` stanza with ``main-is``
- Elixir: ``mix.exs`` — ``escript: [main_module: ...]``
- Ruby: ``*.gemspec`` — ``spec.executables``
- Scala: ``build.sbt`` — ``mainClass := Some(...)``
- OCaml: ``dune`` — ``(executable (name ...))``
- Zig: ``build.zig`` — ``addExecutable(...)``
- Nim: ``*.nimble`` — ``bin = @[...]``

How It Works
------------
1. Scan repo for manifest files using ``find_files``
2. Route each file to a format-specific extractor based on name/extension
3. Extractors regex-parse the file content and yield ``(name, target_path,
   line, target_function)`` tuples
4. A shared helper converts each tuple into a Symbol + ``defines_target`` Edge
5. The build-target linker (``linkers/build_target.py``) later resolves these
   edges to ``main()`` functions

Why Regex
---------
These manifest files have simple, well-defined syntax where regex is
sufficient and avoids adding tree-sitter grammar dependencies. Each
extractor is a small function (5-15 lines) that handles the format's
idiomatic patterns.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, make_symbol_id
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("manifest-targets")

# All manifest file patterns to scan
_MANIFEST_PATTERNS = [
    "build.gradle",
    "build.gradle.kts",
    "*.csproj",
    "pubspec.yaml",
    "Package.swift",
    "*.cabal",
    "mix.exs",
    "*.gemspec",
    "build.sbt",
    "dune",
    "build.zig",
    "*.nimble",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _class_to_path(class_name: str, ext: str) -> str:
    """Convert dotted class name to file path: ``com.example.Main`` -> ``com/example/Main.java``."""
    return class_name.replace(".", "/") + ext


def _elixir_module_to_path(module: str) -> str:
    """Convert Elixir module to file path: ``MyApp.CLI`` -> ``lib/my_app/cli.ex``.

    Follows Mix convention: CamelCase segments become snake_case path
    components under ``lib/``.
    """
    parts = module.split(".")
    snake_parts = [re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", p).lower() for p in parts]
    return "lib/" + "/".join(snake_parts) + ".ex"


def _emit(
    symbols: list[Symbol],
    edges: list[Edge],
    rel_path: str,
    name: str,
    kind: str,
    line: int,
    target_path: str,
    language: str,
    *,
    target_function: str | None = None,
) -> None:
    """Create a build-target Symbol and its ``defines_target`` Edge."""
    sym_id = make_symbol_id("manifest", rel_path, line, line, name, kind)
    symbols.append(Symbol(
        id=sym_id,
        stable_id=None,
        shape_id=None,
        canonical_name=name,
        fingerprint=None,
        kind=kind,
        name=name,
        path=rel_path,
        language=language,
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
        origin=PASS_ID,
        meta={"target_path": target_path},
    ))
    meta = {"target_function": target_function} if target_function else None
    edges.append(Edge.create(
        src=sym_id,
        dst=target_path,
        edge_type="defines_target",
        line=line,
        confidence=1.0,
        origin=PASS_ID,
        meta=meta,
    ))


# ---------------------------------------------------------------------------
# Per-format extractors — each takes file content and yields matches
# ---------------------------------------------------------------------------

# Gradle: mainClass = "..." / mainClassName = "..." / mainClass.set("...")
_GRADLE_MAIN_RE = re.compile(
    r"""mainClass(?:Name)?\s*(?:=|\.set\s*\()\s*["']([^"']+)["']""",
)


def _extract_gradle(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _GRADLE_MAIN_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        class_name = m.group(1)
        # Could be Java or Kotlin — try .java first (more common in Gradle)
        target = _class_to_path(class_name, ".java")
        _emit(symbols, edges, rel_path, class_name.rsplit(".", 1)[-1],
              "binary", line, target, "gradle")


# C#: <StartupObject>Namespace.Program</StartupObject>
_CSPROJ_STARTUP_RE = re.compile(
    r"<StartupObject>\s*([^<]+?)\s*</StartupObject>",
)


def _extract_csproj(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _CSPROJ_STARTUP_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        class_name = m.group(1)
        target = _class_to_path(class_name, ".cs")
        _emit(symbols, edges, rel_path, class_name.rsplit(".", 1)[-1],
              "binary", line, target, "csproj")


# Dart: executables:\n  command_name: script_name
_DART_EXEC_SECTION_RE = re.compile(
    r"^executables:\s*$",
    re.MULTILINE,
)
_DART_EXEC_ENTRY_RE = re.compile(
    r"^\s{2,}(\w[\w-]*):\s*(\w[\w-]*)?\s*$",
    re.MULTILINE,
)


def _extract_dart(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    section_match = _DART_EXEC_SECTION_RE.search(content)
    if not section_match:
        return
    rest = content[section_match.end():]
    for m in _DART_EXEC_ENTRY_RE.finditer(rest):
        # Stop at next top-level key (no indent)
        before = rest[:m.start()]
        if re.search(r"^\S", before, re.MULTILINE):
            break
        cmd_name = m.group(1)
        script_name = m.group(2) or cmd_name
        line_offset = content[:section_match.end()].count("\n")
        line = line_offset + rest[:m.start()].count("\n") + 1
        target = f"bin/{script_name}.dart"
        _emit(symbols, edges, rel_path, cmd_name, "binary", line,
              target, "dart")


# Swift: .executableTarget(name: "AppName"
_SWIFT_EXEC_RE = re.compile(
    r'\.executableTarget\s*\(\s*name:\s*"([^"]+)"',
)


def _extract_swift(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _SWIFT_EXEC_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        name = m.group(1)
        target = f"Sources/{name}/main.swift"
        _emit(symbols, edges, rel_path, name, "binary", line,
              target, "swift")


# Haskell: executable <name> \n ... main-is: <file> \n ... hs-source-dirs: <dir>
_CABAL_EXEC_RE = re.compile(
    r"^executable\s+(\S+)",
    re.MULTILINE,
)
_CABAL_MAIN_IS_RE = re.compile(
    r"^\s+main-is:\s*(\S+)",
    re.MULTILINE,
)
_CABAL_SRC_DIRS_RE = re.compile(
    r"^\s+hs-source-dirs:\s*(\S+)",
    re.MULTILINE,
)


def _extract_cabal(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _CABAL_EXEC_RE.finditer(content):
        exe_name = m.group(1)
        line = content[:m.start()].count("\n") + 1
        # Find the stanza body (until next top-level keyword or EOF)
        stanza_start = m.end()
        next_top = re.search(r"^(?:executable|library|test-suite|benchmark|common)\s",
                             content[stanza_start:], re.MULTILINE)
        stanza = content[stanza_start:stanza_start + next_top.start()] if next_top else content[stanza_start:]

        main_is_m = _CABAL_MAIN_IS_RE.search(stanza)
        if not main_is_m:
            continue
        main_file = main_is_m.group(1)

        src_dirs_m = _CABAL_SRC_DIRS_RE.search(stanza)
        src_dir = src_dirs_m.group(1) if src_dirs_m else ""

        target = f"{src_dir}/{main_file}" if src_dir else main_file
        _emit(symbols, edges, rel_path, exe_name, "binary", line,
              target, "haskell")


# Elixir: escript: [main_module: MyApp.CLI]
_ELIXIR_ESCRIPT_RE = re.compile(
    r"main_module:\s*(\w[\w.]*)",
)


def _extract_elixir(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    m = _ELIXIR_ESCRIPT_RE.search(content)
    if m:
        line = content[:m.start()].count("\n") + 1
        module = m.group(1)
        target = _elixir_module_to_path(module)
        _emit(symbols, edges, rel_path, module, "binary", line,
              target, "elixir", target_function="main")


# Ruby: spec.executables = ["name"] or %w[name1 name2]
_RUBY_EXEC_ARRAY_RE = re.compile(
    r"""\.executables\s*=\s*\[([^\]]*)\]""",
)
_RUBY_EXEC_W_RE = re.compile(
    r"""\.executables\s*=\s*%w\[([^\]]*)\]""",
)


def _extract_gemspec(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    line = 1
    names: list[str] = []

    m = _RUBY_EXEC_ARRAY_RE.search(content)
    if m:
        line = content[:m.start()].count("\n") + 1
        # Parse ["name1", "name2"] — extract quoted strings
        names = re.findall(r"""["']([^"']+)["']""", m.group(1))

    if not names:
        m = _RUBY_EXEC_W_RE.search(content)
        if m:
            line = content[:m.start()].count("\n") + 1
            names = m.group(1).split()

    for name in names:
        # Modern gems use exe/, older use bin/
        target = f"exe/{name}"
        _emit(symbols, edges, rel_path, name, "binary", line,
              target, "ruby")


# Scala: mainClass := Some("com.example.Main")
_SCALA_MAIN_RE = re.compile(
    r"""mainClass\s*(?:(?:in\s+\w+|/)\s*)?:=\s*Some\s*\(\s*"([^"]+)"\s*\)""",
)


def _extract_sbt(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _SCALA_MAIN_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        class_name = m.group(1)
        target = _class_to_path(class_name, ".scala")
        _emit(symbols, edges, rel_path, class_name.rsplit(".", 1)[-1],
              "binary", line, target, "scala")


# OCaml: (executable (name main) ...)
_DUNE_EXEC_RE = re.compile(
    r"""\(executable\s[^)]*\(name\s+(\w+)\)""",
    re.DOTALL,
)
_DUNE_EXECS_RE = re.compile(
    r"""\(executables\s[^)]*\(names\s+([^)]+)\)""",
    re.DOTALL,
)


def _extract_dune(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    for m in _DUNE_EXEC_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        name = m.group(1)
        target = f"{name}.ml"
        _emit(symbols, edges, rel_path, name, "binary", line,
              target, "ocaml")

    for m in _DUNE_EXECS_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        for name in m.group(1).split():
            target = f"{name}.ml"
            _emit(symbols, edges, rel_path, name, "binary", line,
                  target, "ocaml")


# Zig: b.addExecutable(.{ .name = "app", .root_source_file = b.path("src/main.zig") })
# Old API: b.addExecutable("app", "src/main.zig")
_ZIG_EXEC_NEW_RE = re.compile(
    r"""addExecutable\s*\(\s*\.(?:\s*\{[^}]*\.root_source_file\s*=\s*"""
    r"""(?:\w+\.path\s*\(\s*|\.(?:\s*\{[^}]*\.path\s*=\s*))"""
    r""""([^"]+)"\s*\)?)""",
    re.DOTALL,
)
_ZIG_EXEC_OLD_RE = re.compile(
    r"""addExecutable\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"\s*\)""",
)


def _extract_zig(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    seen: set[str] = set()
    for m in _ZIG_EXEC_NEW_RE.finditer(content):
        path = m.group(1)
        if path in seen:
            continue  # pragma: no cover
        seen.add(path)
        line = content[:m.start()].count("\n") + 1
        name = Path(path).stem
        _emit(symbols, edges, rel_path, name, "binary", line,
              path, "zig")

    for m in _ZIG_EXEC_OLD_RE.finditer(content):
        path = m.group(1)
        if path in seen:
            continue  # pragma: no cover
        seen.add(path)
        line = content[:m.start()].count("\n") + 1
        name = Path(path).stem
        _emit(symbols, edges, rel_path, name, "binary", line,
              path, "zig")


# Nim: bin = @["myapp", "other"]
_NIM_BIN_RE = re.compile(
    r"""bin\s*=\s*@\[([^\]]+)\]""",
)


def _extract_nimble(
    content: str,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    m = _NIM_BIN_RE.search(content)
    if not m:
        return
    line = content[:m.start()].count("\n") + 1
    names = re.findall(r'"([^"]+)"', m.group(1))
    for name in names:
        target = f"src/{name}.nim"
        _emit(symbols, edges, rel_path, name, "binary", line,
              target, "nim")


# ---------------------------------------------------------------------------
# Routing table: file name/extension -> extractor
# ---------------------------------------------------------------------------

_EXTRACTORS: dict[str, object] = {
    "build.gradle": _extract_gradle,
    "build.gradle.kts": _extract_gradle,
    ".csproj": _extract_csproj,
    "pubspec.yaml": _extract_dart,
    "Package.swift": _extract_swift,
    ".cabal": _extract_cabal,
    "mix.exs": _extract_elixir,
    ".gemspec": _extract_gemspec,
    "build.sbt": _extract_sbt,
    "dune": _extract_dune,
    "build.zig": _extract_zig,
    ".nimble": _extract_nimble,
}


def _route_file(filename: str) -> object | None:
    """Find the extractor for a given file name."""
    # Try exact name match first
    if filename in _EXTRACTORS:
        return _EXTRACTORS[filename]
    # Try suffix match for extension-based patterns
    for suffix, extractor in _EXTRACTORS.items():
        if suffix.startswith(".") and filename.endswith(suffix):
            return extractor
    return None  # pragma: no cover


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------


def _analyze_manifest_targets(repo_root: Path) -> AnalysisResult:
    """Scan manifest files and extract build-target symbols and edges."""
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    t0 = time.monotonic()

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for pattern in _MANIFEST_PATTERNS:
        for fpath in find_files(repo_root, [pattern]):
            extractor = _route_file(fpath.name)
            if extractor is None:
                continue  # pragma: no cover
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover
                continue
            rel_path = str(fpath.relative_to(repo_root))
            extractor(content, rel_path, symbols, edges)

    run.wall_time = time.monotonic() - t0
    return AnalysisResult(symbols=symbols, edges=edges, run=run)


@register_analyzer("manifest_targets")
def analyze_manifest_targets(repo_root: Path) -> AnalysisResult:
    """Analyze manifest files for build-target entry points."""
    return _analyze_manifest_targets(repo_root)
