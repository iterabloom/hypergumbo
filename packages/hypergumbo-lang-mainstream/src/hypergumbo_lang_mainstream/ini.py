"""INI configuration file analyzer using tree-sitter.

INI files are a common format for configuration files used by many
applications and frameworks. Understanding INI structure helps with
configuration management and security auditing.

How It Works
------------
Uses the TreeSitterAnalyzer base class for orchestration:
1. Uses tree-sitter-ini grammar from tree-sitter-language-pack
2. extract_symbols_from_file: extracts sections and their settings
3. Categorizes settings by domain (database, logging, etc.)
4. Masks sensitive values (passwords, secrets, tokens)

Symbols Extracted
-----------------
- **Sections**: INI sections (e.g., [database], [logging])
- **Settings**: Key-value pairs within sections

Why This Design
---------------
- INI files are ubiquitous in configuration management
- Section organization reveals application structure
- Setting categories help understand configuration domains
- Sensitive value masking protects security audits
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("ini")


def is_ini_tree_sitter_available() -> bool:
    """Check if tree-sitter-ini is available."""
    return _analyzer._check_grammar_available()


def find_ini_files(repo_root: Path) -> list[Path]:
    """Find all INI configuration files in the repository, excluding vendor dirs."""
    # Standard INI files and common INI-format files
    patterns = [
        "*.ini",
        "*.cfg",
        "*.conf",
        ".editorconfig",
        ".flake8",
        ".pylintrc",
    ]
    return sorted(set(find_files(repo_root, patterns)))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node.

    Uses errors="replace" because INI files may use Windows CP-1252 or
    Latin-1 encoding, not UTF-8.
    """
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"ini:{path}:{kind}:{line}:{name}"


# Keywords that indicate sensitive values
SENSITIVE_KEYWORDS = {
    "password", "passwd", "pwd", "secret", "token", "key", "api_key",
    "apikey", "auth", "credential", "private", "encryption", "cert",
    "certificate", "ssh_key", "access_key", "secret_key",
}

# Keywords that indicate configuration domains
DOMAIN_KEYWORDS = {
    "database": {"db", "database", "mysql", "postgres", "sqlite", "mongo", "redis"},
    "logging": {"log", "logging", "logger"},
    "server": {"server", "host", "port", "listen", "bind", "address"},
    "security": {"security", "ssl", "tls", "https", "auth", "authentication"},
    "cache": {"cache", "caching", "memcache", "redis"},
    "email": {"email", "mail", "smtp", "imap"},
    "storage": {"storage", "s3", "gcs", "azure", "blob", "bucket"},
    "api": {"api", "endpoint", "url", "uri"},
    "feature": {"feature", "flag", "toggle", "experiment"},
}


def _categorize_section(section_name: str) -> str:
    """Categorize a section by its name."""
    name_lower = section_name.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in name_lower:
                return domain
    return "general"


def _is_sensitive_key(key_name: str) -> bool:
    """Check if a key name indicates a sensitive value."""
    name_lower = key_name.lower()
    return any(keyword in name_lower for keyword in SENSITIVE_KEYWORDS)


def _mask_value(value: str) -> str:
    """Mask a sensitive value."""
    if not value or len(value) <= 2:
        return "***"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def _extract_ini_symbols(
    node: "tree_sitter.Node",
    rel_path: str,
    current_section: str,
    symbols: list[Symbol],
) -> str:
    """Extract symbols from a syntax tree node, returning current section name.

    Recursively walks the tree extracting section and setting symbols.
    Tracks current_section state to associate settings with their enclosing section.

    Args:
        node: Tree-sitter node to process
        rel_path: Path relative to repo root (as string)
        current_section: Currently active section name
        symbols: List to append extracted symbols to

    Returns:
        The current section name (may have changed if a section node was found)
    """
    if node.type == "section":
        current_section = _extract_section(node, rel_path, symbols)

    elif node.type == "setting":
        _extract_setting(node, rel_path, current_section, symbols)

    for child in node.children:
        current_section = _extract_ini_symbols(child, rel_path, current_section, symbols)

    return current_section


def _extract_section(
    node: "tree_sitter.Node",
    rel_path: str,
    symbols: list[Symbol],
) -> str:
    """Extract a section definition. Returns the section name."""
    section_name = ""

    for child in node.children:
        if child.type == "section_name":
            # Get the text inside the brackets
            for name_child in child.children:
                if name_child.type == "text":
                    section_name = _get_node_text(name_child).strip()
                    break
            break

    if not section_name:
        return ""  # pragma: no cover

    line = node.start_point[0] + 1
    rel_path_obj = Path(rel_path)

    symbol_id = _make_symbol_id(rel_path_obj, section_name, "section", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Count settings in this section
    settings_count = sum(1 for child in node.children if child.type == "setting")
    category = _categorize_section(section_name)

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=section_name,
        kind="section",
        language="ini",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"[{section_name}]",
        meta={
            "settings_count": settings_count,
            "category": category,
        },
    )
    symbols.append(symbol)
    return section_name


def _extract_setting(
    node: "tree_sitter.Node",
    rel_path: str,
    current_section: str,
    symbols: list[Symbol],
) -> None:
    """Extract a setting (key-value pair)."""
    key_name = ""
    value = ""

    for child in node.children:
        if child.type == "setting_name":
            key_name = _get_node_text(child).strip()
        elif child.type == "setting_value":
            value = _get_node_text(child).strip()

    if not key_name:
        return  # pragma: no cover

    rel_path_obj = Path(rel_path)
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path_obj, key_name, "setting", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Check if value is sensitive
    is_sensitive = _is_sensitive_key(key_name)
    display_value = _mask_value(value) if is_sensitive else value

    # Determine category from current section
    category = _categorize_section(current_section) if current_section else "general"

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=key_name,
        kind="setting",
        language="ini",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{key_name} = {display_value}",
        meta={
            "section": current_section,
            "is_sensitive": is_sensitive,
            "category": category,
        },
    )
    symbols.append(symbol)


class IniTreeSitterAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based INI configuration file analyzer.

    Uses tree-sitter-ini from tree-sitter-language-pack to parse INI files
    (.ini, .cfg, .conf, .editorconfig, .flake8, .pylintrc).

    Extracts section headers and key-value settings. Categorizes settings
    by domain and masks sensitive values.

    Overrides ``_find_source_files`` because INI files use a variety of
    extensions and filenames that go beyond simple glob patterns.
    """

    lang = "ini"
    file_patterns: ClassVar[list[str]] = ["*.ini", "*.cfg", "*.conf", ".editorconfig", ".flake8", ".pylintrc"]
    language_pack_name = "ini"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield INI configuration files, deduplicating across patterns."""
        yield from find_ini_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract section and setting symbols from a single INI file.

        Walks the tree recursively, tracking the current section to associate
        settings with their enclosing section. Categorizes settings by domain
        and masks sensitive values.
        """
        analysis = FileAnalysis()
        _extract_ini_symbols(tree.root_node, rel_path, "", analysis.symbols)
        return analysis


_analyzer = IniTreeSitterAnalyzer()


@register_analyzer("ini")
def analyze_ini(repo_root: Path) -> AnalysisResult:
    """Analyze INI configuration files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols
    """
    return _analyzer.analyze(repo_root)
