# 5. Sketch Budget Allocation and Section Composition

Date: 2025-01-15
Status: Draft

## Context

The `sketch` command generates a token-budgeted overview of a repository. Given a fixed token budget (default 4000), hypergumbo must decide:

1. **Which sections to include** (and in what order)
2. **How much budget to allocate** to each section
3. **How to rank items** within each section
4. **When to cut off** each section

These decisions are currently scattered across `generate_sketch()` and various `_format_*` helper functions. This ADR documents the current design and rationale.

## Decision

### Section Order (Fixed)

Sections appear in this order, each conditional on remaining budget:

| # | Section | Minimum Budget | Purpose |
|---|---------|----------------|---------|
| 1 | Header | Always | Title, description |
| 2 | Overview | Always | Language breakdown, file counts, LOC |
| 3 | Structure | 50 tokens | Top-level directory layout |
| 4 | Frameworks | 30 tokens | Detected frameworks/libraries |
| 5 | Tests | 30 tokens | Test file count, frameworks, coverage estimate |
| 6 | Configuration | 50 tokens | Config file excerpts (heuristic + semantic) |
| 7 | Entry Points | 50 tokens | CLI commands, HTTP routes |
| 8 | Data Models | 50 tokens | ORM models, entities, core data structures |
| 9 | Source Files | 50 tokens | File listing by importance |
| 10 | Key Symbols | 200 tokens* | Functions, classes, types with centrality |
| 11 | Additional Files | 50 tokens | Semantic + centrality ranked |
| 12 | Source Content | 100 tokens | Actual code (--with-source only) |
| 13 | Additional File Content | 50 tokens | Code for semantic picks (--with-source only) |

*Key Symbols has a minimum guarantee of 5 symbols regardless of budget.

**Removed sections:**
- **Domain Vocabulary**: Removed. TF-IDF terms were too generic ("model", "response", "request") to provide value. Budget better spent on actionable sections.

### Budget Allocation Strategy

Each section consumes a fraction of **remaining** tokens (not total budget). This waterfall approach means earlier sections can starve later ones if they're greedy.

#### Default Mode (no --with-source)

| Section | Allocation Rule |
|---------|-----------------|
| Overview | Fixed: always included |
| Structure | Tree expands until 10 root-level directories shown |
| Frameworks | Fixed: all detected frameworks (no limit) |
| Tests | Fixed: summary line only |
| Configuration | Heuristic lines + semantic embedding (HYBRID mode default) |
| Entry Points | 33% of remaining |
| Data Models | 20% of remaining |
| Source Files | 66% of remaining (if <300 left) or 25% (larger budgets) |
| Key Symbols | 80% of remaining |
| Additional Files | 100% of remaining minus 10 |

#### With Source Mode (--with-source)

When `--with-source` is set, budget shifts dramatically from file listings to actual source code:

| Section | Allocation Rule | Rationale |
|---------|-----------------|-----------|
| Overview | Fixed: always included | — |
| Structure | Tree expands until 10 root-level directories shown | — |
| Frameworks | Fixed: all detected frameworks (no limit) | — |
| Tests | Fixed: summary line only | — |
| Configuration | Heuristic lines + semantic embedding | — |
| Entry Points | 33% of remaining | High signal, keep as-is |
| Data Models | 20% of remaining | High signal for domain understanding |
| Source Files (list) | **15%** of remaining | Shrink: paths are low-value |
| Key Symbols | **30%** of remaining | Shrink: actual code is better |
| Additional Files (list) | **10%** of remaining | Shrink: paths are low-value |
| Source Content | **70%** of remaining | Expand: this is the point |
| Additional File Content | 100% of remaining minus 50 | NEW: code for semantic picks |

This rebalancing addresses the problem where a 32k `--with-source` sketch would show ~1600 file paths but only ~50 lines of actual code.

### Ordering Within Sections

| Section | Ordering Criterion |
|---------|-------------------|
| Overview | Fixed format: language %, file counts, LOC |
| Structure | Tree built from important files (see below) |
| Frameworks | Detection order (alphabetical in output) |
| Configuration | Heuristic priority, then semantic similarity to query |
| Entry Points | Confidence score (descending)* |
| Data Models | Centrality (most-referenced models first) |
| Source Files | Symbol importance density = Σ(in-degrees) / LOC |
| Key Symbols | Two-phase: coverage-first, then centrality × tier weight |
| Additional Files | Hybrid: semantic similarity + file mention centrality |
| Source Content | Same as Source Files (top density first) |
| Additional File Content | Same as Additional Files (top semantic+centrality first) |

*Entry Point confidence reflects detection reliability. Current detectors use decorator/annotation patterns (0.95). Future work may add manifest-based detection (0.99) and naming-convention heuristics (0.70-0.85).

**Data Models** are framework-afforded concepts like Entry Points. Detection identifies ORM models, entities, and core data structures via:
- Framework decorators: `@dataclass`, `@Entity`, Django `models.Model`, SQLAlchemy `Base`, Pydantic `BaseModel`
- Naming conventions: classes ending in `Model`, `Entity`, `Schema`
- Type annotations: classes used as return types or parameters across multiple functions

### Cutoff Logic

Each section computes a maximum item count from its budget allocation:

```
max_items = max(minimum, budget_for_section // tokens_per_item)
```

| Section | Tokens per Item | Minimum | Default Ceiling | Notes |
|---------|-----------------|---------|-----------------|-------|
| Structure | — | — | 10 root dirs | Tree expands until N root dirs shown |
| Entry Points | ~25 | 5 | 20 | Shows CLI commands + HTTP routes |
| Data Models | ~20 | 3 | 30 | ORM models, entities, core data structures |
| Source Files | ~15 | 5 | 50 | Paths only; shrinks with --with-source |
| Key Symbols | ~25 | 5 | 100 | Max 5 per file to ensure breadth |
| Additional Files | ~15 | 1 | — | Paths only; shrinks with --with-source |
| Source Content | varies | 1 | — | Dynamic truncation with elbow/median floor |
| Additional File Content | varies | 1 | — | Dynamic truncation with median floor |

The "tokens per item" values are estimates used to calculate how many items fit in the allocated budget. Actual token usage varies by item complexity. Source content sections use dynamic truncation: files that fit are included in full, oversized files are truncated to a computed floor (elbow-based for early source files, median-based thereafter).

### Structure: Tree Built from Important Files

The Structure section displays a `tree`-like visualization showing paths to important files, revealing directory organization along the way. Files are sampled from other sketch sections until the target number of root-level directories (default: 10) are represented.

**File sampling order:**

| Priority | Source | Minimum | Purpose |
|----------|--------|---------|---------|
| 1 | Configuration | 2+ files | Show where config lives |
| 2 | Tests | 1+ file | Highest-LOC test file |
| 3 | Entry Points | 1+ file | Highest-confidence entrypoint |
| 4 | Source Files | 3+ files | Top centrality density |
| 5 | Additional Files | 3+ files | Top semantic picks |

**Algorithm:**

1. Sample files from each source in priority order
2. For each file, check if its root-level directory is already shown
3. If the file adds a new root-level directory, include it in the tree
4. If the file's root directory is already shown, skip it
5. Stop when the target number of root directories is reached (or all sources exhausted)

**Output format:**

```
/path/to/repo/
├── config.yaml
├── src
│   ├── main.py
│   └── [and 42 other items]
├── tests
│   ├── integration
│   │   ├── test_api.py
│   │   └── [and 12 other items]
│   └── [and 8 other items]
└── [and 5 other items]
```

- Every expanded branch terminates at least one visible file (the "important file" that caused inclusion)
- Sibling counts shown as `[and N other items]`
- Nested structure revealed along the path to important files
- Hidden directories (`.github/`, etc.) included if they contain important files

### Source Content: Dynamic Truncation

Both Source Files Content (Section 9) and Additional Files Content (Section 10) use dynamic truncation. Files that fit within budget are included in full; oversized files are truncated to a computed target and appended with `[...truncated...]`.

**Truncation floor for Source Files Content:**
- **First 3 files** (before enough data for a meaningful median): Uses an elbow-based floor from `compute_truncation_elbow()`, which analyzes cumulative symbol centrality to find the point of diminishing returns — the line number after which most important symbols have been covered. This is converted to a token count.
- **After 3 files**: Uses `max(median(token_counts), 500)`, matching the Additional Files Content pattern.

**Truncation floor for Additional Files Content:**
- Uses `max(median(token_counts), 500)` throughout.

**Why elbow-based truncation for early source files:** The most important files (e.g., `ir.py`, `base.py`, `cli.py`) tend to be the largest and were frequently skipped entirely under the previous all-or-nothing strategy. By using symbol centrality to determine where to truncate, we capture the architecturally significant portion of each file while staying within budget.

**Safety:** Truncation uses `truncate_to_tokens()` which respects line boundaries and proper markdown fencing. The `[...truncated...]` marker signals to readers that content continues.

### Key Symbols: Minimum Guarantee

The Key Symbols section has special handling: **always include at least 5 symbols** when analysis produces results, even if this causes slight budget overage. This was added because experiments showed some projects had 0 Key Symbols at 1k budget when budget was exhausted by earlier sections. Key Symbols is the most valuable section for code understanding.

### Key Symbols: Two-Phase Selection

Phase 1 (Coverage-First):
- Take top N files by symbol importance density
- Select the single best symbol from each file
- Ensures breadth across the codebase

Phase 2 (Diminishing Returns):
- Fill remaining slots using marginal utility scoring
- Score = centrality × tier_weight
- First-party symbols get priority over vendored/third-party
- Cap of 5 symbols per file prevents any single file from dominating

### Key Symbols: Output Format

The Key Symbols section uses a rich output format:

```
### `path/to/file.py`
- `function_name(args...) -> ReturnType` (function) ★ — Docstring excerpt...
- `ClassName` (class)
  (... +24 more, top score: 0.30)
```

- **★ (star)**: Indicates centrality ≥ 50% of the maximum centrality in the codebase
- **Grouped by file**: Symbols are organized under file headers
- **Signature truncation**: Long signatures are truncated with `…`
- **Docstring excerpts**: First line of docstring shown when available
- **Overflow indicator**: `(... +N more, top score: X.XX)` shows additional symbols not displayed

### Centrality Computation

Two distinct centrality metrics exist:

1. **Graph centrality** (used in Key Symbols): Derived from AST-based call graph and import relationships. Computed by tree-sitter parsing. Measures how "central" a symbol is based on what calls/imports it.

2. **File mention centrality** (used in Additional Files): Counts how many other files textually reference symbols from each file. Computed via parallelized Python regex with a combined alternation pattern.

## Consequences

### Positive

* **Predictable structure**: Users know where to find information regardless of project size.
* **Graceful degradation**: Small budgets still produce useful output by prioritizing earlier sections.
* **Key Symbols guarantee**: The most valuable section is never empty.
* **Efficient computation**: Centrality scores are cached between runs.

### Negative

* **Waterfall starvation**: Greedy early sections (especially Configuration with embeddings) can starve later sections.
* **Fixed section order**: Users cannot reorder sections based on project characteristics.
* **Magic numbers**: The percentage allocations (66%, 25%, 33%, 80%) are tuned heuristically, not derived from first principles.
* **No user control**: Users cannot adjust section priorities or allocations.

## References

* Main generation logic: `src/hypergumbo/sketch.py:generate_sketch()`
* Source Files ordering: `_format_source_files()` and `compute_symbol_importance_density()`
* Key Symbols selection: `_format_symbols()` and `_select_symbols_coverage_first()`
* Entry Points detection: `detect_entrypoints()` and `_format_entrypoints()`
* File mention centrality: `src/hypergumbo/ranking.py`
* Configuration extraction: `_format_config_section()` with HYBRID/HEURISTIC/SEMANTIC modes

## Example Output

A 16k-token sketch of litellm (~1M LOC, 4678 files) produces:

**Structure section (tree format):**
```
/home/user/litellm/
├── AGENTS.md
├── ci_cd
│   ├── check_files_match.py
│   └── [and 7 other items]
├── cookbook
│   ├── veo_video_generation.py
│   └── [and 48 other items]
├── db_scripts
│   ├── create_views.py
│   ├── migrate_keys.py
│   └── update_unassigned_teams.py
├── deploy
│   ├── charts
│   │   └── litellm-helm
│   │       ├── tests
│   │       │   ├── masterkey-secret_tests.yaml
│   │       │   └── [and 6 other items]
│   │       └── [and 7 other items]
│   └── [and 3 other items]
├── docs
│   └── my-website
│       ├── docs
│       │   ├── projects
│       │   │   ├── PDL.md
│       │   │   └── [and 28 other items]
│       │   └── [and 91 other items]
│       └── [and 13 other items]
├── enterprise
│   ├── __init__.py
│   └── [and 9 other items]
├── litellm
│   ├── proxy
│   │   ├── proxy_cli.py
│   │   └── [and 75 other items]
│   └── [and 54 other items]
├── Makefile
├── scripts
│   ├── benchmark_proxy_vs_provider.py
│   └── [and 2 other items]
├── tests
│   ├── test_litellm
│   │   ├── proxy
│   │   │   ├── management_endpoints
│   │   │   │   ├── test_team_endpoints.py
│   │   │   │   └── [and 19 other items]
│   │   │   └── [and 46 other items]
│   │   └── [and 52 other items]
│   └── [and 64 other items]
├── ui
│   └── litellm-dashboard
│       ├── README.md
│       └── [and 16 other items]
└── [and 34 other items]
```

**Other sections:**
- 13 frameworks detected
- ~322 source files listed (of 3680 total)
- 42 entry points (CLI commands + HTTP routes)
- ~35 files of Key Symbols with overflow indicators
- Total output: approximately 680 lines of markdown
