# 9. Feature-Focused Bakeoff Suite

Date: 2026-01-30
Status: Accepted

## Context

### The Problem: Parse Correctness vs Developer Usefulness

The existing `scripts/bakeoff` infrastructure answers one question well: "Does hypergumbo parse this codebase correctly?" It:
- Uses smaller repos (1-100MB) for fast iteration
- Focuses on detecting parsing failures, missing call edges, route detection issues
- Runs frequently as part of the development cycle

But there's a second, equally important question: **"Are hypergumbo's outputs actually useful to developers?"**

This requires different infrastructure:
- **Larger repos** (20-200MB) where slice limits and supply chain tiers actually matter
- **Feature-specific testing** (slice, reverse-slice, tier filtering, graph centrality)
- **Qualitative assessment** by adopting a developer's perspective
- **Less frequent cadence** because it's more expensive (includes LLM reasoning)

### Why Separate Loops?

We considered integrating feature testing into the existing bakeoff but rejected it:

| Aspect | Regular Bakeoff | Feature Bakeoff |
|--------|-----------------|-----------------|
| Question answered | "Does it parse?" | "Is it useful?" |
| Repo size | 1-100MB | 20-200MB |
| Run frequency | Every fix | Less often |
| Artifacts | hg.json, routes, entrypoints | Slices, reverse-slices, tiers |
| Assessment | Automated flags | LLM-driven qualitative |
| Convergence signal | No CRITICAL/HIGH issues | Developer finds it helpful |

Combining these would:
1. Slow down the fast feedback loop that makes regular bakeoff effective
2. Conflate two different types of "success"
3. Make the output harder to interpret

### The LLM Assessment Approach

Static metrics (slice coverage %, Gini coefficient) provide signals but can't answer "Would a developer actually find this helpful for refactoring?" We use LLM-driven qualitative assessment:

1. Generate a structured prompt with repo artifacts
2. LLM agent examines artifacts + actual source code
3. Agent adopts developer persona and evaluates 3 tasks:
   - Refactoring: "Does the slice show what I'd break?"
   - New feature: "Can I find patterns to follow?"
   - Understanding: "Does this help me learn the codebase?"
4. Agent produces structured YAML assessment
5. Human or automated process aggregates findings

This is automated from the human's perspective (no manual analysis needed) but captures qualitative judgments that metrics alone cannot.

## Decision

### 1. Create Separate Feature Bakeoff Loop

New scripts:
- `scripts/bakeoff-features` - Main orchestration (init, cohort, run, diagnose, status)
- `scripts/bakeoff-features-reflect` - LLM-driven qualitative assessment

### 2. Repo Selection Criteria

For feature testing, repos should be:
- **Size**: 20-200MB (large enough for slice limits to matter)
- **File count**: 500+ files (enough complexity)
- **Multi-language bonus**: Polyglot repos are more interesting
- **Framework presence**: Repos with routes/handlers test more features

Complexity scoring:
```python
score = 0
if 20 <= size_mb <= 50: score += 1.0
elif 50 < size_mb <= 100: score += 2.0
elif 100 < size_mb <= 200: score += 1.5

if file_count >= 500: score += 1.0
if file_count >= 1000: score += 1.0
if file_count >= 2000: score += 0.5

if len(languages) >= 2: score += 1.0
if len(languages) >= 3: score += 0.5
```

### 2b. Curriculum-Based Cohort Selection

As an alternative to auto-selection, cohorts can be pre-planned as a **curriculum** — a
sequence of cohorts chosen and ordered with intent (e.g., progression from easy to hard,
or grouping by domain to isolate variables).

A curriculum is a markdown file stored in `~/hypergumbo_lab_notebook/curricula/` containing:
- The rationale for the cohort groupings and ordering
- The `bakeoff-features cohort --repos` commands to run in sequence
- Any per-cohort notes or hypotheses

Example curriculum (repos are illustrative):
````markdown
# Curriculum: Widget Platform Stress Test

## Rationale
Progress from single-language repos to polyglot monoliths.

## Cohorts

```bash
./scripts/bakeoff-features cohort --repos alpha-api,beta-service,gamma-lib
./scripts/bakeoff-features cohort --repos delta-monolith,epsilon-gateway,zeta-dashboard
./scripts/bakeoff-features cohort --repos eta-platform,theta-infra,iota-legacy
```

## Notes
- Cohort 1: Pure Go services — baseline for graph quality
- Cohort 2: Mixed Go/TypeScript — tests cross-language linkers
- Cohort 3: Large polyglot monoliths — stress-tests slice limits
````

Run each cohort command followed by `bakeoff-features run` and `bakeoff-features diagnose`
before proceeding to the next.

### 3. Feature Test Battery

For each repo, run:
```bash
# 1. Full analysis
hypergumbo run repo --out hg.json

# 2. List entrypoints
hypergumbo slice --list-entries --input hg.json

# 3. Forward slices (top 5 entries)
hypergumbo slice --entry {entry} --inline --out slice.N.json

# 4. Reverse slices (top 3 entries)
hypergumbo slice --entry {entry} --reverse --inline --out rslice.N.json

# 5. Tier-bounded slices
hypergumbo slice --entry auto --max-tier 1 --out slice.tier1.json
hypergumbo slice --entry auto --max-tier 2 --out slice.tier2.json

# 6. Symbols by connectivity
hypergumbo symbols --all

# 7. Routes
hypergumbo routes
```

### 4. Quality Metrics

Automated metrics with thresholds:

**Slice Quality:**
| Metric | Good Range | Warning |
|--------|------------|---------|
| slice_coverage_pct | 5-30% | <1% or >50% |
| limit_hit_frequency | <50% | >80% |
| cross_file_ratio | >20% | <5% |

**Supply Chain:**
| Metric | Good Range | Warning |
|--------|------------|---------|
| tier1_pct | 40-80% | <20% or >95% |
| tier4_pct | 0-5% | >10% |

**Graph:**
| Metric | Good Range | Warning |
|--------|------------|---------|
| centrality_gini | 0.3-0.7 | <0.1 or >0.9 |
| orphan_rate | <20% | >40% |

### 5. LLM Assessment Protocol

The reflection script generates prompts for each repo. The LLM agent evaluates:

**A. Refactoring Task:**
- Pick a function from slice output
- Check if forward slice shows dependencies
- Check if reverse slice shows callers
- Note missing/noise

**B. New Feature Implementation:**
- Identify feature addition point
- Check if entrypoints help find similar code
- Check if supply chain tier helps focus

**C. Codebase Understanding:**
- Evaluate centrality rankings
- Check if architecture is visible
- Assess cross-file edge meaningfulness

Output format:
```yaml
developer_assessment:
  repo: django
  overall_verdict: USEFUL | PARTIALLY_USEFUL | NOT_USEFUL
  improvement_ideas: [...]
```

### 6. Extend loop-toggle for Multiple Modes

AUTONOMOUS_MODE.txt becomes: `OFF` | `BROAD` | `DEEP`
- `OFF`: Autonomous mode disabled
- `BROAD`: Regular bakeoff (current behavior, `TRUE` is alias)
- `DEEP`: Feature-focused bakeoff

Commands:
```bash
./scripts/loop-toggle off     # Disable
./scripts/loop-toggle broad   # Regular bakeoff
./scripts/loop-toggle deep    # Feature bakeoff
./scripts/loop-toggle status  # Show current mode
```

## Consequences

### Positive

1. **Targeted feedback**: Different questions get different infrastructure
2. **Developer perspective**: LLM assessment captures qualitative usefulness
3. **Actionable improvements**: Aggregated findings point to specific enhancements
4. **Maintained velocity**: Regular bakeoff stays fast for parse-correctness

### Negative

1. **Two loops to maintain**: More infrastructure complexity
2. **LLM cost**: Reflection step requires LLM invocations
3. **Subjectivity**: LLM assessments may vary

### Neutral

1. **Optional**: Feature bakeoff is opt-in; regular bakeoff continues to work
2. **Complementary**: The two loops answer different questions
3. **Iterative**: Assessment protocol can be refined based on experience

## Implementation Checklist

- [x] Create `scripts/bakeoff-features` with init, cohort, run, diagnose, status commands
- [x] Create `scripts/bakeoff-features-reflect` for LLM-driven assessment
- [x] Define quality metric thresholds
- [x] Document recommended larger repos for testing
- [x] Extend `scripts/loop-toggle` for multiple modes
- [x] Update AGENTS.md with deep bakeoff in priority queue
- [x] Verify end-to-end flow with real repos

## References

- ADR-0008: Autonomous Governance and Vendor-Agnostic Hooks
- `scripts/bakeoff`: Regular bakeoff infrastructure
- `docs/hypergumbo-spec.md`: Slice and supply chain specifications
