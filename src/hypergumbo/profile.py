"""Repo profile detection - language and framework heuristics.

This module provides fast, heuristic-based detection of programming
languages and frameworks in a repository, without requiring full parsing.

How It Works
------------
Language detection scans file extensions using the discovery module:
- Counts files matching each language's extension patterns
- Tallies lines of code (LOC) for each detected language
- Returns a RepoProfile with language statistics

Framework detection examines dependency manifests:
- Python: pyproject.toml, requirements.txt, setup.py, Pipfile
- JavaScript: package.json dependencies and devDependencies

Detection is intentionally shallow - we look for package names in
dependency files rather than analyzing imports. This keeps profiling
fast (milliseconds) even for large repos.

Why This Design
---------------
- Extension-based language detection is simple and reliable
- Dependency file scanning catches frameworks even in empty repos
- Shallow heuristics prioritize speed over precision
- The profile informs which analyzers to run and what to expect
- Results are used by sketch generation for the language breakdown
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import find_files

# Language extensions mapping
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": ["*.py", "*.pyi"],
    "javascript": ["*.js", "*.mjs", "*.cjs", "*.jsx"],
    "typescript": ["*.ts", "*.tsx", "*.d.ts"],
    "vue": ["*.vue"],
    "html": ["*.html", "*.htm"],
    "css": ["*.css", "*.scss", "*.sass", "*.less"],
    "json": ["*.json"],
    "yaml": ["*.yaml", "*.yml"],
    "markdown": ["*.md", "*.markdown"],
    "rust": ["*.rs"],
    "go": ["*.go"],
    "java": ["*.java"],
    "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx"],
    "ruby": ["*.rb"],
    "php": ["*.php"],
    "swift": ["*.swift"],
    "kotlin": ["*.kt", "*.kts"],
    "shell": ["*.sh", "*.bash", "*.zsh"],
    "scala": ["*.scala", "*.sc"],
    "elixir": ["*.ex", "*.exs"],
    "lua": ["*.lua"],
    "haskell": ["*.hs", "*.lhs"],
    "ocaml": ["*.ml", "*.mli"],
    "solidity": ["*.sol"],
    "csharp": ["*.cs"],
}

# Framework detection patterns
# Maps framework name -> (file to check, pattern to look for)
PYTHON_FRAMEWORKS = {
    # Web frameworks
    "fastapi": ["fastapi"],
    "flask": ["flask", "Flask"],
    "django": ["django", "Django"],
    "aiohttp": ["aiohttp"],
    # Testing
    "pytest": ["pytest"],
    # Data/ORM
    "sqlalchemy": ["sqlalchemy", "SQLAlchemy"],
    "pydantic": ["pydantic"],
    # Task queues
    "celery": ["celery"],
    # ML/AI - Deep Learning
    "pytorch": ["torch", "pytorch"],
    "tensorflow": ["tensorflow"],
    "keras": ["keras"],
    "jax": ["jax", "flax"],
    # ML/AI - NLP/Transformers
    "transformers": ["transformers", "huggingface"],
    "spacy": ["spacy"],
    "nltk": ["nltk"],
    # ML/AI - LLM Orchestration
    "langchain": ["langchain"],
    "langgraph": ["langgraph"],
    "langsmith": ["langsmith"],
    "llamaindex": ["llama-index", "llama_index"],
    "haystack": ["haystack", "farm-haystack"],
    # ML/AI - Classical
    "scikit-learn": ["scikit-learn", "sklearn"],
    "xgboost": ["xgboost"],
    "lightgbm": ["lightgbm"],
    # ML/AI - GPU/CUDA
    "cuda": ["cupy", "pycuda", "numba"],
    # ML/AI - MLOps
    "mlflow": ["mlflow"],
    "wandb": ["wandb"],
    # LLM APIs
    "openai": ["openai"],
    "anthropic": ["anthropic"],
}

JS_FRAMEWORKS = {
    "react": ["react"],
    "vue": ["vue"],
    "angular": ["@angular/core"],
    "express": ["express"],
    "next": ["next"],
    "nuxt": ["nuxt"],
    "svelte": ["svelte"],
    "nestjs": ["@nestjs/core"],
}


@dataclass
class LanguageStats:
    """Statistics for a detected language."""

    files: int = 0
    loc: int = 0

    def to_dict(self) -> dict:
        return {"files": self.files, "loc": self.loc}


@dataclass
class RepoProfile:
    """Profile of a repository's languages and frameworks."""

    languages: dict[str, LanguageStats] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "languages": {k: v.to_dict() for k, v in self.languages.items()},
            "frameworks": sorted(self.frameworks),
        }


def _count_loc(file_path: Path) -> int:
    """Count non-empty lines in a file."""
    try:
        content = file_path.read_text(errors="ignore")
        return sum(1 for line in content.splitlines() if line.strip())
    except (OSError, IOError):
        return 0


def _detect_languages(repo_root: Path) -> dict[str, LanguageStats]:
    """Detect languages by scanning file extensions."""
    languages: dict[str, LanguageStats] = {}

    for lang, patterns in LANGUAGE_EXTENSIONS.items():
        # Use a set to deduplicate files (e.g., *.ts and *.d.ts both match foo.d.ts)
        files = set(find_files(repo_root, patterns))
        if files:
            stats = LanguageStats(files=len(files))
            for f in files:
                stats.loc += _count_loc(f)
            languages[lang] = stats

    return languages


def _read_dependency_file(repo_root: Path, filename: str) -> str:
    """Read a dependency file if it exists."""
    path = repo_root / filename
    if path.exists():
        try:
            return path.read_text(errors="ignore").lower()
        except (OSError, IOError):
            pass
    return ""


def _detect_python_frameworks(repo_root: Path) -> list[str]:
    """Detect Python frameworks from dependency files."""
    detected = []

    # Check pyproject.toml, requirements.txt, setup.py
    content = ""
    content += _read_dependency_file(repo_root, "pyproject.toml")
    content += _read_dependency_file(repo_root, "requirements.txt")
    content += _read_dependency_file(repo_root, "setup.py")
    content += _read_dependency_file(repo_root, "Pipfile")

    for framework, patterns in PYTHON_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_js_frameworks(repo_root: Path) -> list[str]:
    """Detect JavaScript/TypeScript frameworks from package.json."""
    detected = []

    package_json = repo_root / "package.json"
    if package_json.exists():
        try:
            content = package_json.read_text(errors="ignore")
            data = json.loads(content)
            deps = set()
            deps.update(data.get("dependencies", {}).keys())
            deps.update(data.get("devDependencies", {}).keys())

            for framework, patterns in JS_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern in deps:
                        detected.append(framework)
                        break
        except (OSError, IOError, json.JSONDecodeError):
            pass

    return detected


def _detect_frameworks(repo_root: Path) -> list[str]:
    """Detect all frameworks in the repository."""
    frameworks = []
    frameworks.extend(_detect_python_frameworks(repo_root))
    frameworks.extend(_detect_js_frameworks(repo_root))
    return frameworks


def detect_profile(repo_root: Path) -> RepoProfile:
    """Detect the profile of a repository.

    Returns a RepoProfile with detected languages and frameworks.
    """
    languages = _detect_languages(repo_root)
    frameworks = _detect_frameworks(repo_root)

    return RepoProfile(languages=languages, frameworks=frameworks)
