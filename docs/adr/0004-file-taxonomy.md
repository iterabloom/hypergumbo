# 4. File Taxonomy: Tier and Role Classification

Date: 2025-01-14
Status: Accepted

## Context

Hypergumbo currently classifies files using several overlapping, independently-maintained systems:

| System | Location | Purpose |
|--------|----------|---------|
| `LANGUAGE_EXTENSIONS` | profile.py | LOC counting (67+ languages) |
| `SOURCE_EXTENSIONS` | sketch.py | Exclude from Additional Files (10 languages) |
| `CONFIG_FILES_BY_LANG` | sketch.py | Config extraction targets |
| `ADDITIONAL_FILES_EXCLUDES` | sketch.py | Patterns to skip for embedding |
| `DEFAULT_EXCLUDES` | discovery.py | Patterns to skip everywhere |
| `Tier` enum | supply_chain.py | Provenance classification (4 tiers) |

This scattered approach creates problems:

1. **Duplication**: `SOURCE_EXTENSIONS` is a subset of `LANGUAGE_EXTENSIONS` but maintained separately.

2. **Ambiguity**: JSON files can be config (`package.json`), data (`model_prices.json`), or generated (`package-lock.json`). The extension alone doesn't distinguish them.

3. **Conflation**: "Should we count this in LOC?" and "Should we extract symbols from this?" are different questions with different answers, but the current system conflates them.

4. **Inconsistent definitions of "code"**: Lock files are excluded (correctly), but data files like `model_prices_and_context_window.json` (34K lines) inflate LOC statistics.

### What is "Code"?

A reasonable definition:

| Category | Examples | Is it code? |
|----------|----------|-------------|
| **Instructions** | Python, JavaScript, SQL | Yes - tells computer what to do |
| **Configuration** | package.json, YAML configs | Yes - parameterizes behavior |
| **Documentation** | Markdown, RST | Yes - tells humans what to do |
| **Data** | JSON datasets, CSV, fixtures | No - input/output, not instructions |
| **Generated** | Lock files, minified bundles | No - machine output |

This definition treats documentation as code (the instructions happen to be in natural language) while excluding pure data files. This aligns with modern "Docs as Code" and "Infrastructure as Code" practices.

## Decision

We will introduce a **two-dimensional classification** where every file has both a **Tier** (provenance) and a **Role** (purpose):

### Dimension 1: Tier (Provenance)

Unchanged from the existing supply chain model:

```python
class Tier(IntEnum):
    """Where does this file come from?"""
    FIRST_PARTY = 1   # Project's own code
    INTERNAL_DEP = 2  # Internal libraries, examples
    EXTERNAL_DEP = 3  # Third-party dependencies
    DERIVED = 4       # Build artifacts, generated output
```

### Dimension 2: Role (Purpose)

New classification for content type:

```python
class FileRole(Flag):
    """What is this file for?"""
    ANALYZABLE = auto()     # Has symbols to extract (functions, classes)
    CONFIG = auto()         # Parameterizes behavior
    DOCUMENTATION = auto()  # Human-readable instructions/explanations
    DATA = auto()           # Raw information, not instructions
```

### Single Source of Truth

All file type information consolidated in one place:

```python
@dataclass
class LanguageSpec:
    """Complete specification for a language/file type."""
    name: str
    extensions: list[str]
    roles: FileRole

    # For ambiguous extensions, filename-level overrides
    config_files: list[str] | None = None
    data_patterns: list[str] | None = None

LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=["*.py", "*.pyi"],
        roles=FileRole.ANALYZABLE,
    ),
    "markdown": LanguageSpec(
        name="markdown",
        extensions=["*.md", "*.markdown"],
        roles=FileRole.DOCUMENTATION,
    ),
    "json": LanguageSpec(
        name="json",
        extensions=["*.json"],
        roles=FileRole.CONFIG | FileRole.DATA,  # Ambiguous, needs filename rules
        config_files=["package.json", "tsconfig.json", "composer.json"],
        data_patterns=["*_data.json", "**/fixtures/**/*.json"],
    ),
    # ... etc
}
```

### Composed Decision Functions

Replace scattered logic with composed queries:

```python
# What counts as "code" for LOC
CODE_ROLES = FileRole.ANALYZABLE | FileRole.CONFIG | FileRole.DOCUMENTATION

def should_count_loc(path: Path) -> bool:
    """Should this file contribute to LOC statistics?"""
    tier = get_tier(path)
    role = get_role(path)

    if tier == Tier.DERIVED:
        return False  # Build artifacts don't count
    if tier == Tier.EXTERNAL_DEP:
        return False  # Third-party code isn't "ours"
    if role == FileRole.DATA:
        return False  # Datasets aren't code

    return bool(role & CODE_ROLES)

def should_extract_symbols(path: Path, config: SupplyChainConfig) -> bool:
    """Should we run tree-sitter analysis on this file?"""
    tier = get_tier(path)
    role = get_role(path)

    if tier.value not in config.analysis_tiers:
        return False

    return role == FileRole.ANALYZABLE

def is_additional_file_candidate(path: Path) -> bool:
    """Should this appear in Additional Files section?"""
    tier = get_tier(path)
    role = get_role(path)

    # Only first-party/internal context files
    if tier not in (Tier.FIRST_PARTY, Tier.INTERNAL_DEP):
        return False

    # Config and docs are useful context; data is not
    return role in (FileRole.CONFIG, FileRole.DOCUMENTATION)
```

### Handling Ambiguous Extensions

JSON is the primary ambiguous case. Resolution order:

1. **Explicit config files**: `package.json` → CONFIG
2. **Data patterns**: `**/fixtures/*.json` → DATA
3. **Size heuristic**: >100KB → likely DATA
4. **Default**: CONFIG (conservative)

```python
def classify_json_file(path: Path) -> FileRole:
    spec = LANGUAGES["json"]

    if path.name in spec.config_files:
        return FileRole.CONFIG

    if any(path.match(p) for p in spec.data_patterns):
        return FileRole.DATA

    if path.stat().st_size > 100_000:
        return FileRole.DATA

    return FileRole.CONFIG
```

## Consequences

### Positive

* **Single source of truth**: One `LANGUAGES` dict replaces 5+ scattered definitions.

* **Correct LOC counts**: Data files no longer inflate statistics. A repo with 34K lines of pricing data won't report 34K extra "lines of code."

* **Clear semantics**: "Why was this file skipped?" has an answer: its tier, its role, or both.

* **Extensible**: Adding a new language requires one entry in `LANGUAGES`, not edits to multiple files.

* **Composable**: Tier and Role are orthogonal; decisions compose cleanly.

### Negative

* **Migration effort**: Existing code must be refactored to use the new taxonomy. This is a significant change touching profile.py, sketch.py, discovery.py, and supply_chain.py.

* **Heuristics for ambiguous files**: The JSON disambiguation logic (patterns, size thresholds) may misclassify edge cases. This is inherent to the problem, not the solution.

* **Learning curve**: Contributors must understand two dimensions instead of one. However, the dimensions are intuitive (where from? what for?) and the composed functions hide complexity.

## Migration Path

1. **Phase 1**: Add `FileRole` enum and `LanguageSpec` dataclass alongside existing code. Implement `get_role()` function.

2. **Phase 2**: Replace `LANGUAGE_EXTENSIONS` with derivation from `LANGUAGES`.

3. **Phase 3**: Replace `SOURCE_EXTENSIONS` with `role == ANALYZABLE` check.

4. **Phase 4**: Replace `ADDITIONAL_FILES_EXCLUDES` patterns with role-based filtering.

5. **Phase 5**: Remove deprecated constants, update tests.

Each phase can be a separate PR with tests verifying behavioral equivalence.

## References

* Existing supply chain classification: `src/hypergumbo/supply_chain.py`
* Current language extensions: `src/hypergumbo/profile.py` lines 61-120
* Current source extensions: `src/hypergumbo/sketch.py` lines 2472-2486
* Industry tools for comparison: cloc, tokei, sloccount
