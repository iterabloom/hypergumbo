"""INI configuration file analyzer using tree-sitter.

INI files are a common format for configuration files used by many
applications and frameworks. Understanding INI structure helps with
configuration management and security auditing.

How It Works
------------
1. Uses tree-sitter-ini grammar from tree-sitter-language-pack
2. Extracts sections and their settings
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

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "ini.tree_sitter"
PASS_VERSION = "0.1.0"


class IniAnalysisResult:
    """Result of INI file analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges: list = []  # INI files don't have edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_ini_tree_sitter_available() -> bool:
    """Check if tree-sitter-ini is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("ini")
        return True
    except Exception:  # pragma: no cover
        return False


def find_ini_files(repo_root: Path) -> list[Path]:
    """Find all INI configuration files in the repository."""
    files: list[Path] = []
    # Standard INI files
    files.extend(repo_root.glob("**/*.ini"))
    # Config files that use INI format
    files.extend(repo_root.glob("**/*.cfg"))
    files.extend(repo_root.glob("**/*.conf"))
    # Common INI-format files
    files.extend(repo_root.glob("**/setup.cfg"))
    files.extend(repo_root.glob("**/tox.ini"))
    files.extend(repo_root.glob("**/.editorconfig"))
    files.extend(repo_root.glob("**/.flake8"))
    files.extend(repo_root.glob("**/.pylintrc"))
    files.extend(repo_root.glob("**/pytest.ini"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


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


class IniAnalyzer:
    """Analyzer for INI configuration files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._current_section: str = ""

    def analyze(self) -> IniAnalysisResult:
        """Run the INI analysis."""
        start_time = time.time()

        files = find_ini_files(self.repo_root)
        if not files:
            return IniAnalysisResult(
                symbols=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("ini")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_section = ""
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "ini", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return IniAnalysisResult(
            symbols=self._symbols,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "section":
            self._extract_section(node, path)
        elif node.type == "setting":
            self._extract_setting(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_section(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a section definition."""
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
            return  # pragma: no cover

        self._current_section = section_name

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, section_name, "section", line)
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
        self._symbols.append(symbol)

    def _extract_setting(self, node: "tree_sitter.Node", path: Path) -> None:
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

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, key_name, "setting", line)
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
        category = _categorize_section(self._current_section) if self._current_section else "general"

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
                "section": self._current_section,
                "is_sensitive": is_sensitive,
                "category": category,
            },
        )
        self._symbols.append(symbol)


def analyze_ini(repo_root: Path) -> IniAnalysisResult:
    """Analyze INI configuration files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        IniAnalysisResult containing extracted symbols
    """
    if not is_ini_tree_sitter_available():
        warnings.warn(
            "INI analysis skipped: tree-sitter-ini not available",
            UserWarning,
            stacklevel=2,
        )
        return IniAnalysisResult(
            symbols=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "ini", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-ini not available",
        )

    analyzer = IniAnalyzer(repo_root)
    return analyzer.analyze()
