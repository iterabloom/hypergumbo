# Maintainer Agent Specification

This document specifies the behavior of an automated maintainer agent that processes incoming pull requests from external contributors.

## Overview

The maintainer agent is an LLM-powered automation that:
1. Monitors incoming PRs from contributor forks
2. Evaluates trust signals and security posture
3. Reviews code changes for correctness and style
4. Merges PRs that pass all checks
5. Requests changes or escalates when appropriate

The agent operates under the principle: **automate the boring, escalate the interesting**.

## Design Goals

1. **Reduce maintainer toil** - Handle routine PRs without human intervention
2. **Maintain security** - Never merge code that weakens the project's security posture
3. **Preserve trust model** - Apply GOVERNANCE.md policies consistently
4. **First come, first serve** - Process PRs in order, no favoritism
5. **Transparent decisions** - All actions are logged and explainable

## Triggers

The agent activates when:

| Event | Action |
|-------|--------|
| New PR opened | Queue for review |
| PR updated (new commits) | Re-queue for review |
| CI status changes | Check if ready to merge |
| Contributor responds to feedback | Re-evaluate |

The agent does NOT activate for:
- PRs from maintainers (they use `auto-pr`)
- Draft PRs
- PRs marked `[WIP]` or `[DO NOT MERGE]`
- PRs to branches other than `dev`

## Processing Pipeline

```
┌─────────────────┐
│  PR Opened      │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Gate Checks    │──── FAIL ───► Close with explanation
└────────┬────────┘
         │ PASS
         ▼
┌─────────────────┐
│  CI Status      │──── PENDING ─► Wait
└────────┬────────┘
         │ PASS/FAIL
         ▼
┌─────────────────┐
│  Trust Eval     │──── LOW ────► Escalate to human
└────────┬────────┘
         │ OK
         ▼
┌─────────────────┐
│  Code Review    │──── ISSUES ──► Request changes
└────────┬────────┘
         │ APPROVED
         ▼
┌─────────────────┐
│  Conflict Check │──── CONFLICT ► Request rebase
└────────┬────────┘
         │ CLEAN
         ▼
┌─────────────────┐
│  Merge          │
└─────────────────┘
```

## Gate Checks

Immediate rejection if:

| Condition | Response |
|-----------|----------|
| PR targets non-dev branch | "Please target `dev` branch" |
| No commits | "Empty PR" |
| All commits unsigned (no DCO) | "Please sign commits with `-s`" |
| Binary files added | "Binary files require maintainer review" |
| Secrets detected | "Potential secrets detected - PR blocked" |

## CI Integration

Wait for CI to complete before proceeding:

```python
def check_ci_status(pr):
    status = get_commit_status(pr.head_sha)

    if status == "pending":
        return WAIT
    elif status == "success":
        return PROCEED
    elif status == "failure":
        comment_ci_failure(pr, get_failed_checks(pr))
        return WAIT_FOR_FIX
```

The agent does NOT merge PRs with failing CI, ever.

## Trust Evaluation

Based on GOVERNANCE.md, evaluate contributor trust:

### Trust Score Calculation

```python
def calculate_trust_score(contributor, pr):
    score = 50  # Base score (neutral)

    # Positive signals
    if contributor.previous_merges > 0:
        score += min(contributor.previous_merges * 5, 25)
    if all_commits_signed(pr):
        score += 10
    if has_tests(pr):
        score += 10
    if small_diff(pr, max_lines=200):
        score += 5

    # Concerning signals
    if large_diff(pr, min_lines=500):
        score -= 15
    if touches_security_sensitive_files(pr):
        score -= 20
    if vague_commit_messages(pr):
        score -= 10
    if code_doesnt_match_description(pr):
        score -= 30

    # Red flags (immediate escalation)
    if has_red_flags(pr):
        return ESCALATE

    return score
```

### Trust Thresholds

| Score | Action |
|-------|--------|
| 80+ | Auto-merge eligible |
| 60-79 | Light review, likely merge |
| 40-59 | Careful review required |
| 20-39 | Escalate to human |
| <20 | Block and escalate |

### Red Flags (Immediate Escalation)

From GOVERNANCE.md:
- Code contradicts its description
- Obfuscated code or encoded strings
- Unexplained network calls
- Changes to security-sensitive files without rationale
- `eval()`, `exec()`, or dynamic code loading
- Binary blobs
- Attempts to disable tests/linting/security
- Pressure to merge quickly

```python
def has_red_flags(pr):
    return any([
        contains_obfuscated_code(pr),
        adds_network_calls_without_justification(pr),
        modifies_security_files_without_explanation(pr),
        contains_dynamic_execution(pr),
        adds_binary_files(pr),
        disables_security_checks(pr),
        description_contradicts_code(pr),
    ])
```

## Code Review

The agent performs automated code review:

### Style Checks
- Follows existing code patterns
- Consistent naming conventions
- Appropriate documentation
- No commented-out code

### Correctness Checks
- Tests cover new functionality
- No obvious logic errors
- Edge cases considered
- Error handling appropriate

### Security Checks
- No hardcoded secrets
- Input validation present
- No SQL injection / XSS vectors
- Dependencies are pinned

### Review Comment Format

```markdown
## Maintainer Agent Review

### Summary
[Brief description of what the PR does]

### Checks
- ✅ CI passing
- ✅ Tests included
- ✅ Code style consistent
- ⚠️ Large diff (412 lines) - please consider splitting

### Requested Changes
1. `src/foo.py:42` - Missing error handling for `None` case
2. `tests/test_foo.py` - Test doesn't cover edge case X

### Trust Score: 72/100
Contributor has 3 previous merges. PR is well-structured.

---
*Automated review by maintainer-agent. Human maintainers may provide additional feedback.*
```

## Conflict Handling

### First Come, First Serve

PRs are processed in order of CI completion:

```python
def get_merge_queue():
    prs = get_open_prs(state="approved", ci="passing")
    return sorted(prs, key=lambda p: p.ci_completed_at)

def process_queue():
    for pr in get_merge_queue():
        if has_merge_conflicts(pr):
            request_rebase(pr)
            continue
        merge(pr)
```

### Rebase Requests

```markdown
This PR has merge conflicts with `dev`.

Another PR was merged while yours was in review. Please rebase:

```bash
git fetch upstream
git rebase upstream/dev
git push --force-with-lease origin your-branch
```

This is normal in active projects. First come, first serve! 🏃
```

## Escalation Paths

### To Human Maintainer

Escalate when:
- Trust score < 40
- Red flags detected
- Security-sensitive files modified
- Complex architectural changes
- Contributor disputes feedback
- Agent is uncertain

Escalation format:

```markdown
## 🚨 Maintainer Review Required

**PR:** #123 - Add new feature
**Contributor:** @username (0 previous merges)
**Reason:** Modifies `scripts/auto-pr` (security-sensitive)

### Trust Evaluation
- Score: 35/100
- Concerning: Large diff, modifies CI configuration
- Red flags: None detected

### Agent Assessment
The code appears functional but changes security-sensitive files.
A human should verify the changes are intentional and safe.

@maintainer-team please review.
```

### To Admin

Escalate when:
- Suspected malicious activity
- Repeated concerning behavior from same contributor
- Governance file modifications
- Agent detects coordinated attack patterns

## State Machine

```
                    ┌──────────────┐
                    │   QUEUED     │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ BLOCKED  │ │ PENDING  │ │ REVIEWING│
        │ (gates)  │ │ (CI)     │ │          │
        └──────────┘ └────┬─────┘ └────┬─────┘
                          │            │
                    ┌─────┴─────┐      │
                    ▼           ▼      ▼
              ┌──────────┐ ┌──────────────┐
              │ CI_FAIL  │ │ CHANGES_REQ  │
              └──────────┘ └──────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              ┌──────────┐  ┌──────────┐  ┌──────────┐
              │ APPROVED │  │ ESCALATED│  │ CONFLICT │
              └────┬─────┘  └──────────┘  └──────────┘
                   │
                   ▼
              ┌──────────┐
              │  MERGED  │
              └──────────┘
```

## Configuration

```yaml
# .maintainer-agent.yml
agent:
  enabled: true

  # Trust thresholds
  auto_merge_threshold: 80
  review_threshold: 60
  escalate_threshold: 40

  # Timeouts
  ci_timeout_minutes: 30
  review_timeout_hours: 24

  # Limits
  max_diff_lines: 1000
  max_files_changed: 50

  # Security-sensitive paths (always escalate)
  sensitive_paths:
    - AGENTS.md
    - GOVERNANCE.md
    - .github/workflows/*
    - scripts/auto-pr
    - scripts/contribute
    - scripts/release*

  # Auto-approve paths (lower scrutiny)
  safe_paths:
    - docs/*.md
    - tests/*
    - "*.txt"

  # Maintainers to notify on escalation
  maintainers:
    - "@admin"
```

## Logging and Audit

All agent actions are logged:

```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "pr_number": 123,
  "action": "merge",
  "contributor": "alice",
  "trust_score": 85,
  "checks_passed": ["ci", "gates", "review", "conflicts"],
  "review_summary": "Added new analyzer with tests",
  "decision_reasoning": "High trust score, small diff, tests included"
}
```

Logs are retained for audit and can be used to:
- Debug agent behavior
- Improve trust scoring
- Detect attack patterns
- Train future models

## Implementation Notes

### Technology Stack

The agent can be implemented as:
1. **GitHub/Forgejo Action** - Triggered on PR events
2. **Standalone service** - Polling for new PRs
3. **Claude Code session** - Running in autonomous mode

Recommended: GitHub Action for event-driven triggering, with Claude Code for complex review logic.

### API Integration

```python
class MaintainerAgent:
    def __init__(self, forge_api, llm_client):
        self.forge = forge_api
        self.llm = llm_client

    def process_pr(self, pr_number):
        pr = self.forge.get_pr(pr_number)

        # Gate checks
        if not self.passes_gates(pr):
            return self.reject(pr, "Failed gate checks")

        # Wait for CI
        if not self.ci_passed(pr):
            return self.wait(pr)

        # Trust evaluation
        trust = self.evaluate_trust(pr)
        if trust.should_escalate:
            return self.escalate(pr, trust.reason)

        # Code review
        review = self.review_code(pr)
        if review.has_issues:
            return self.request_changes(pr, review.issues)

        # Conflict check
        if self.has_conflicts(pr):
            return self.request_rebase(pr)

        # Merge!
        return self.merge(pr)

    def review_code(self, pr):
        """Use LLM to review code changes."""
        diff = self.forge.get_diff(pr)

        prompt = f"""
        Review this pull request for:
        1. Code correctness
        2. Test coverage
        3. Security issues
        4. Style consistency

        PR Description: {pr.description}

        Diff:
        {diff}

        Respond with:
        - APPROVED if ready to merge
        - CHANGES_REQUIRED with specific issues
        - ESCALATE if uncertain or concerning
        """

        return self.llm.analyze(prompt)
```

### Deployment

```yaml
# .github/workflows/maintainer-agent.yml
name: Maintainer Agent

on:
  pull_request:
    types: [opened, synchronize, reopened]
  pull_request_review:
    types: [submitted]
  check_suite:
    types: [completed]

jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Maintainer Agent
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          FORGE_TOKEN: ${{ secrets.FORGE_TOKEN }}
        run: |
          python -m maintainer_agent process ${{ github.event.pull_request.number }}
```

## Security Considerations

1. **Token scope** - Agent token should have minimal permissions (read PRs, write comments, merge)
2. **Rate limiting** - Prevent abuse by limiting reviews per contributor per hour
3. **Escape hatch** - Maintainers can override agent decisions with `/override` command
4. **Audit trail** - All actions logged for review
5. **No secrets in logs** - Scrub sensitive data before logging
6. **Sandboxed execution** - Agent cannot execute arbitrary code from PRs

## Metrics

Track agent performance:

| Metric | Description |
|--------|-------------|
| `prs_processed` | Total PRs handled |
| `auto_merged` | PRs merged without human intervention |
| `escalated` | PRs escalated to humans |
| `false_positives` | Human overrides of agent rejections |
| `false_negatives` | Bad PRs that slipped through |
| `mean_time_to_merge` | Average time from PR open to merge |
| `contributor_satisfaction` | Survey/feedback scores |

## Future Enhancements

1. **Learning from overrides** - Improve trust scoring based on maintainer feedback
2. **Cross-project reputation** - Share trust signals across related projects
3. **Predictive conflict detection** - Warn contributors before they start overlapping work
4. **Automated dependency updates** - Handle Dependabot-style PRs fully automatically
5. **Release coordination** - Help coordinate multi-PR releases

## References

- [GOVERNANCE.md](./GOVERNANCE.md) - Trust model and review policies
- [AGENTS.md](../AGENTS.md) - Agent behavior guidelines
- [Contributor workflow](../scripts/contribute) - Fork-based PR creation

---

*"Trust, but verify. Then automate the verification."*
