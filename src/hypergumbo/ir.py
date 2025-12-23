"""Internal Representation (IR) for code analysis.

Parsers emit Symbol objects to this IR layer. The IR is then compiled
to output views (e.g., behavior_map JSON).
"""
from dataclasses import dataclass


@dataclass
class Symbol:
    """A code symbol (function, class, etc.) detected by analysis.

    Attributes:
        id: Location-based identifier in format {lang}:{file}:{start}-{end}:{name}:{kind}
        name: The symbol's name (e.g., function name, class name)
        kind: Type of symbol (function, class, etc.)
        language: Programming language (python, javascript, etc.)
        path: File path where the symbol is defined
        line: Starting line number (1-indexed)
        end_line: Ending line number (1-indexed)
    """

    id: str
    name: str
    kind: str
    language: str
    path: str
    line: int
    end_line: int

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "language": self.language,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
        }
