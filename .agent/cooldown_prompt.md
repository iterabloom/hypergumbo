# Cooldown Mode — Retrospective & Tool Development

Reflection was completed recently. Instead of re-running the full checklist, use this time for creative, low-stakes work that compounds over sessions.

## 1. Process Retrospective
Think about the last few hours of work:
- What approaches worked well? How can you do more of what worked?
- What approaches wasted time or led to dead ends? What to avoid next time?
- Were there moments of insight? What triggered them?
- Did any tools, scripts, or workflows create unnecessary friction?

## 2. Human-Gated File Ideas
If the retrospective suggests improvements to governance files (`.agent/`, `AGENTS.md`, `.githooks/`, `scripts/auto-pr`, `scripts/merge-pr`, `scripts/contribute`, `scripts/ci-debug`, `scripts/lib/forgejo-api.sh`, `CODEOWNERS`):
- Document the idea in `~/hypergumbo_lab_notebook/` with rationale and proposed changes
- Do NOT attempt to modify these files during cooldown — move on to the next section
- Tag the entry with `[GOVERNANCE-IDEA]` so it's findable later

## 3. Analysis Tool Development
Improve or create analysis scripts in `~/hypergumbo_lab_notebook/analysis_lib/`:
- Current toolkit: quality_overview, edge_resolution, language_comparison, entrypoint_analysis, potential_issues, signature_quality, complexity_metrics
- Ideas: dependency fan-out visualization, framework pattern coverage tracker, cross-language linking accuracy, dead code detection, concept density heatmaps
- Follow naming convention: `NN_short_name.py`
- Test new scripts against existing bakeoff artifacts in `~/hypergumbo_lab_notebook/bakeoff_artifacts/`

## 4. Lab Notebook Documentation
Record in `~/hypergumbo_lab_notebook/`:
- Observations from recent work that might inform future experiments
- Hypotheses about hypergumbo's behavior on different repo types
- Ideas for bakeoff cohort selection criteria
- Interesting patterns noticed in artifacts

Continue working — do not stop.
