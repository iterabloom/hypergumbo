# Governance

This document describes contributor trust, review policies, and release processes for hypergumbo.

## Philosophy

**Trust is fact-based and continuously updated.**

We start from a presumption of good faith. Every contributor is assumed to have good intentions until their behavior indicates otherwise. Trust is not about credentials, social proof, or reputation—it's about observable patterns of behavior over time.

All contributors are expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md). 
Violations affect trust status just as technical violations do.

Each contributor has their own trust profile, updated with every interaction.

## Behavioral Signals

Trust is a function of accumulated signals. These are not rigid rules but patterns we pay attention to.

### Positive Signals (Trust-Building)

| Behavior | Why It Matters |
|----------|----------------|
| Signed commits | Identity accountability |
| Clear commit messages | Transparency of intent |
| Code matches description | Honesty |
| Well-explained changes | Nothing to hide |
| Responsive to review | Collaborative intent |
| Small, focused PRs | Easier to verify |
| Tests included | Cares about correctness |
| Consistent patterns | Predictable, reliable |
| Accepts feedback gracefully | Ego not invested in deception |
| Documents non-obvious decisions | Helps others understand |

### Concerning Signals (Trust-Reducing)

| Behavior | Why It Matters |
|----------|----------------|
| Unsigned commits (after reminder) | Avoids accountability |
| Underexplained code | Hiding something? Or just rushed? |
| Vague commit messages | Hard to verify intent |
| Large, sprawling changes | Hard to review thoroughly |
| Defensive about questions | What are they protecting? |
| Repeatedly ignores style/conventions | Doesn't respect the project |
| Changes don't match description | Accidental or intentional? |

### Red Flags (Immediate Scrutiny)

| Behavior | Concern |
|----------|---------|
| Code that contradicts its description | Possible deception |
| Obfuscated code or encoded strings | Hiding functionality |
| Unexplained network calls | Data exfiltration? |
| Changes to security-sensitive files without clear rationale | Weakening defenses |
| `eval()`, `exec()`, or dynamic code loading of external content | Arbitrary code execution |
| Binary blobs or compiled artifacts | Unverifiable content |
| Attempts to disable tests, linting, or security checks | Bypassing verification |
| Pressure to merge quickly without review | Social engineering |

## Trust Levels

Trust is continuous, not discrete, but for practical purposes:

### New Contributor
- First PR to the project
- All changes reviewed by maintainer
- Extra scrutiny on any code that touches:
  - Entry points (CLI, main)
  - Network/file operations
  - Dependencies
  - CI/CD configuration
  - Security-sensitive files

### Established Contributor
- Multiple PRs merged over time
- Consistent positive signals
- May receive lighter review on low-risk changes
- Still reviewed on security-sensitive changes

### Maintainer
- Extended history of trust-building behavior
- Can approve PRs from others
- Cannot approve their own PRs
- Can trigger releases (with co-sign from another maintainer)

### Admin
- Repository owner(s)
- Can modify governance policies
- Final authority on disputes

## Review Requirements

| Change Type | Minimum Review |
|-------------|----------------|
| Documentation only | 1 maintainer |
| Tests only | 1 maintainer |
| Bug fix with tests | 1 maintainer |
| New feature | 1 maintainer |
| Dependency changes | 1 maintainer + dependency audit |
| CI/CD configuration | 2 maintainers |
| Security-sensitive code | 2 maintainers |
| Governance/policy files | Admin approval |

### Security-Sensitive Files

Changes to these require extra scrutiny:
- `AGENTS.md`, `GOVERNANCE.md`, `CODEOWNERS`
- `.github/workflows/*`
- `scripts/auto-pr`, `scripts/contribute`, `scripts/release-*`
- `.githooks/*`
- Any file that handles secrets, auth, or network calls

## Automated Trust Signals

CI automatically checks for some signals:

```yaml
# Checked on every PR
- Commit is signed (DCO sign-off)
- No secrets detected (gitleaks/trufflehog)
- No known vulnerabilities (pip-audit)
- No suspicious code patterns (Bandit)
- Tests pass with coverage
- Linting passes
```

Future automation could track:
- Contributor history (PRs merged, issues resolved)
- Ratio of description accuracy to actual changes
- Response time to review feedback
- Frequency of concerning signals

## Handling Trust Violations

### Minor Issues (Pattern of Neglect)
- Private reminder to contributor
- Offer help/clarification
- Note in maintainer records

### Moderate Issues (Repeated Problems)
- Direct conversation about patterns
- Increased review requirements
- May require co-signing for a period

### Serious Issues (Apparent Deception)
- Immediate block on merging
- Review of all past contributions
- Public disclosure if malicious code was merged
- Permanent ban if intentional

### Good Faith Mistakes
- Everyone makes mistakes
- What matters is response to discovery
- Honest mistakes with honest acknowledgment don't reduce trust
- Covering up mistakes does

## Release Process

Releases merge `dev` → `main` and require:

1. **Extended CI** (not just the 5-minute dev CI)
   - Multi-Python version matrix (3.10-3.13)
   - Multi-platform (Linux, macOS, Windows)
   - Integration tests on real repositories
   - Performance regression check

2. **Security Audit**
   - Full dependency vulnerability scan
   - License compliance check
   - SBOM generation
   - No new concerning signals from contributors since last release

3. **Human Verification**
   - CHANGELOG reviewed and accurate
   - Version number appropriate (semver)
   - Release notes drafted
   - At least one maintainer signs off

4. **Provenance**
   - Release tag is GPG-signed
   - Build artifacts have attestation
   - SHA256 checksums published

## Versioning

We use [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes to CLI or output schema
- **MINOR**: New features, backward compatible
- **PATCH**: Bug fixes, backward compatible

On `dev` branch: frequent patches (0.5.1, 0.5.2, 0.5.3, ...)
On `main` branch: accumulated releases (0.6.0, 0.7.0, ...)

Pre-release versions: `0.5.1-dev.42` for dev builds if needed.

## Changelog

We maintain `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/):

```markdown
## [Unreleased]
### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security

## [0.6.0] - 2025-01-15
### Added
- New analyzer for GDScript
...
```

Every PR should update the Unreleased section if it changes user-facing behavior.

## Disputes and Decisions

- Technical decisions: Maintainers discuss, consensus preferred, admin breaks ties
- Governance decisions: Admin authority, with input from maintainers
- Security decisions: Err on the side of caution, disclose after mitigation
- Trust decisions: Based on facts, not feelings; document reasoning

## Evolution

This document will evolve as the project grows. Changes require:
- PR with rationale
- Discussion period (minimum 1 week for significant changes)
- Admin approval

The goal is always: **ship good software safely while treating people fairly**.

---

*"Trust, but verify. Then keep verifying."*
