# Hypergumbo v1.0 Validation Gates (Historical)

> **Note**: This document archives the original success criteria and validation gates planned for Hypergumbo v1.0. These were aspirational metrics defined before launch. The project shipped successfully without formal A/B testing or independent evaluation protocols.

## Success criteria

### Technical
* 🟩 Analyzes Python repo (100 files) in <10 seconds
* 🟩 Generates valid behavior map JSON 100% of runs
* 🟩 Stable node IDs (same code → same IDs across runs)
* 🟩 **Stable IDs survive refactors**: Renamed/moved functions retain stable_id (when signature unchanged)
* 🟩 Capsule runs without network/API keys (unless --assistant llm used for init)
* 🟩 Catalog displays all available building blocks
* 🟩 Template-based plans work without LLM
* 🟩 Shareable capsules export without leaking repo structure

### Adoption (measured over 3 months post-launch)
* ⬜ 5+ agent projects using output (OR 3+ with detailed case studies)
* ⬜ 100+ repos analyzed
* ⬜ 3+ community-contributed capsule plans or packs published

### Agent validation (objective metrics)

**Measured during 3-month validation period after v0.1.0 ships:**

**Metric 1: Token reduction**
* ⬜ Measure: Tokens in hypergumbo slice vs. naïve approach (full files)
* Method: A/B test on 50 edit tasks across 3+ agents
* Target: >30% reduction (median)
* Collection: Agents log token counts, submit anonymized data

**Metric 2: Edit correctness**
* ⬜ Measure: Human evaluation of agent-generated edits
* Method: Blind review of 50 edits (25 with hypergumbo, 25 without)
* Target: ≥80% correct with hypergumbo (same or better than baseline)
* Evaluators: 2 independent developers (not project team)

**Metric 3: Hallucination rate**
* ⬜ Measure: Fabricated symbols/calls in agent output
* Method: Parse agent responses, check if symbols exist in hypergumbo nodes
* Target: <20% hallucination rate (vs. >40% without hypergumbo baseline)
* Collection: Automated parsing of agent logs

**Metric 4: Error cases (qualitative → quantitative)**
* ⬜ Measure: Documented cases where AST-only analysis failed
* Collection: GitHub issues, agent developer reports, user feedback
* Target: 20+ specific cases with reproduction steps
* Analysis: Categorize by type (false positive, false negative, missing edge)

**Pre-registration:**
Protocol published at https://hypergumbo.iterabloom.com/eval/a before data collection starts.
Prevents cherry-picking results.

**Use:** These metrics feed into decision whether to proceed with advanced capabilities.

### Benchmark validation (if research continues)
* ⬜ **Precision**: >0.85 on call graph edges (ground truth from 20 hand-verified repos)
* ⬜ **Recall**: >0.70 on detectable edges (AST-visible calls, not dynamic dispatch)
* ⬜ **Confidence calibration**: Edges with confidence >0.9 have <5% false positive rate

Pre-register evaluation protocol at https://hypergumbo.iterabloom.com/eval before collecting results.

### Quality
* 🟩 Zero crashes on 50+ real-world repos
* ⬜ Documentation clarity: new user can run analysis in <10 minutes

## Spec A Validation (Prerequisite for Future Enhancements)

**Timeline:** 3 months after v0.1.0 ships

**Decision meeting:** Review evidence, decide whether to invest in advanced capabilities

**Quantitative requirements (all must pass):**
* ⬜ Agent adoption: 5+ named projects using hypergumbo in production
  - OR: 3+ projects with detailed case studies (>500 words each)
* ⬜ Quality improvement: >20% improvement vs. no-hypergumbo baseline
  - Measured via: Token reduction, OR edit correctness, OR hallucination reduction
  - A/B test with ≥50 tasks
* ⬜ Stability: <5% crash rate on 100+ repos
  - Measured via: CI runs, user reports, agent telemetry
* ⬜ Market signal: 10+ requests for features requiring higher-fidelity analysis
  - Logged in: GitHub issues, agent developer discussions, design partner feedback

**Qualitative requirements (2 of 3 must pass):**
* ⬜ Agent developers: "hypergumbo is critical, we'd pay for upgrades"
  - Survey or direct quotes from 3+ organizations
* ⬜ Design partners: 3+ orgs pledge engineering time for co-development
  - Written commitment (not just verbal interest)
* ⬜ Specific gaps: 20+ documented cases where AST analysis failed
  - With reproduction steps, expected behavior, impact assessment

**Funding secured:**
* ⬜ Research budget: 4-6 engineers × 6-8 months
* ⬜ Commitment: If research succeeds, funding for full development available

**Decision outcomes:**
1. **Go:** All quantitative + 2/3 qualitative + funding secured → Start research phase
2. **Prototype:** Partial evidence + limited funding → 2-month limited prototype
3. **Defer:** Evidence marginal → Wait 3 more months, re-evaluate
4. **Focus on iteration:** Evidence weak or absent → Improve current capabilities (more languages, better performance)

**No-go triggers (any of these → don't start research):**
* **Would HALT:** Spec A has fundamental adoption blockers (agents abandon after trying)
* **Would HALT:** Competition ships full typed analysis first (market opportunity lost)
* **Would HALT:** Team capacity unavailable (engineers exhausted, need break)
* **Would HALT:** No demonstrated need for higher fidelity (AST analysis "good enough")

**Decision makers:**
* Founder (final decision)
* 3+ design partners (advisory votes)
* 1 independent technical advisor (advisory)

**Documentation:**
* Decision recorded in ADR (Architecture Decision Record)
* Published summary: "Why we are/aren't building advanced capabilities"
* If going forward: Timeline and milestones confirmed
