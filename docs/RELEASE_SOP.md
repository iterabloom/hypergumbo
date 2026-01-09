# Release Standard Operating Procedure (SOP)

This document describes the hypergumbo release process, including what happens when the release pipeline runs, how to trigger releases, and troubleshooting guidance.

## Quick Reference

```
AGENT                                         HUMAN
─────                                         ─────
./scripts/prepare-release 0.8.0
  ├── Bump version
  ├── Update CHANGELOG
  ├── Run validations
  └── Create PR ────────────────────────────► Merge PR on Codeberg

                                              ./scripts/tag-release 0.8.0
                                                ├── Create signed tag
                                                └── Push tag ──► CI Release
```

## Release Workflow Overview

The release pipeline is defined in `.github/workflows/release.yml` and runs on Forgejo/Codeberg.

### Trigger Conditions

The release workflow starts under two conditions:

1. **Tag Push**: When a version tag matching `v*` is pushed (e.g., `v0.6.0`, `v1.0.0-rc1`)
2. **Manual Dispatch**: Via the Forgejo Actions UI or API with:
   - `version`: Required. The version to release (e.g., `0.6.0`)
   - `dry_run`: Optional. Set to `true` to skip PyPI publish (default: `false`)

### Pipeline Stages

The workflow runs four jobs in sequence:

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   test-matrix   │  │  security-audit │  │integration-tests│
│  (Python 3.10-  │  │  (pip-audit,    │  │  (quick mode)   │
│   3.13 on Linux)│  │  bandit, etc.)  │  │                 │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                   ┌─────────────────────┐
                   │  build-and-publish  │
                   │  (only if all pass) │
                   └─────────────────────┘
```

#### Job 1: test-matrix
- Runs tests on Python 3.10, 3.11, 3.12, and 3.13
- Builds source-only grammars (tree-sitter-lean, tree-sitter-wolfram)
- Requires 100% test coverage
- Uses conditional coverage config when `sentence-transformers` unavailable

#### Job 2: security-audit
- **pip-audit**: Scans for known vulnerabilities in dependencies
- **Bandit**: Security linting for Python code
- **Safety**: Dependency safety check (advisory, non-blocking)
- **pip-licenses**: Audits dependency licenses, warns on copyleft
- **trufflehog**: Scans for accidentally committed secrets

#### Job 3: integration-tests
- Runs `./scripts/integration-test --quick`
- Tests CLI functionality on real repositories
- Uses lightweight repos only (Express) to avoid OOM on small runners
- 30-minute timeout

#### Job 4: build-and-publish
Only runs if all previous jobs succeed.

1. **Build**: Creates wheel and source distribution
2. **Checksums**: Generates SHA256SUMS for all artifacts
3. **SBOM**: Generates Software Bill of Materials (CycloneDX format)
4. **Verify**: Dry-run install and twine check
5. **Publish to PyPI**: Uses `PYPI_TOKEN` secret (skipped on dry run)
6. **Create Forgejo Release**: Uses `FORGEJO_TOKEN` secret to:
   - Create a release with changelog notes
   - Upload wheel, tarball, checksums, and SBOM

## Prerequisites

### Secrets Configuration

Two secrets must be configured in the repository settings:

| Secret | Purpose | How to Obtain |
|--------|---------|---------------|
| `PYPI_TOKEN` | Publishing to PyPI | Create at https://pypi.org/manage/account/token/ |
| `FORGEJO_TOKEN` | Creating Forgejo releases | Create at https://codeberg.org/user/settings/applications |

### GPG Signing Setup

Tags must be GPG-signed. Set up GPG signing before your first release.

#### Key Hierarchy (Recommended)

For security, use a master key on a trusted machine with subkeys on development machines:

```
Master Key [C] (trusted machine, kept offline)
├── Signing Subkey [S] (exported to dev machines)
└── Encryption Subkey [E] (exported to dev machines)
```

If a development machine is compromised, revoke subkeys and generate new ones without losing your identity.

#### Setup Steps

**On trusted machine (once):**

```bash
# 1. Create master key (Certify only)
gpg --expert --full-generate-key
# Select: (11) ECC → toggle off Sign → Curve 25519 → 2y expiry

# 2. Add signing subkey
gpg --expert --edit-key YOUR_EMAIL
# gpg> addkey → (11) ECC → keep Sign → Curve 25519 → 2y → save

# 3. Add encryption subkey
gpg --expert --edit-key YOUR_EMAIL
# gpg> addkey → (12) ECC (encrypt only) → Curve 25519 → 2y → save

# 4. Backup master key (store securely offline!)
gpg --export-secret-keys --armor YOUR_EMAIL > master-secret.asc
gpg --gen-revoke YOUR_EMAIL > revoke.asc

# 5. Export subkeys only (for dev machines)
gpg --export-secret-subkeys --armor YOUR_EMAIL > subkeys-only.asc
gpg --export --armor YOUR_EMAIL > public.asc
```

**On development machine:**

```bash
# 1. Import keys
gpg --import public.asc
gpg --import subkeys-only.asc

# 2. Trust the key
gpg --edit-key YOUR_EMAIL
# gpg> trust → 5 (ultimate) → quit

# 3. Configure git
git config --global user.signingkey YOUR_SIGNING_SUBKEY_ID
git config --global commit.gpgsign true
git config --global gpg.program gpg

# 4. Fix terminal issue (add to ~/.bashrc)
echo 'export GPG_TTY=$(tty)' >> ~/.bashrc
source ~/.bashrc
```

**On Codeberg:**

1. Go to https://codeberg.org/user/settings/keys
2. Add your public key under "GPG Keys"

#### Verify Setup

```bash
echo "test" | gpg --clear-sign  # Should prompt for passphrase
git commit --allow-empty -m "test: verify signing"
git log --show-signature -1  # Should show "Good signature"
```

### Version Consistency

Before releasing, ensure version is consistent across:
- `pyproject.toml` (`version = "X.Y.Z"`)
- `src/hypergumbo/__init__.py` (`__version__ = "X.Y.Z"`)
- Git tag (`vX.Y.Z`)

The `prepare-release` script handles this automatically.

### Changelog

Update `CHANGELOG.md` with release notes. The workflow extracts the section matching the version for the release body.

## How to Release

### Primary Method: Agent + Human Workflow (Recommended)

This workflow separates automated preparation (agent) from authorization (human), respecting branch protection on main.

#### Step 1: Agent Prepares Release

```bash
# Agent runs this command
./scripts/prepare-release 0.8.0
```

This script:
1. ✓ Verifies on dev branch with clean working directory
2. ✓ Bumps version in `pyproject.toml` and `__init__.py`
3. ✓ Updates CHANGELOG.md (`[Unreleased]` → `[0.8.0] - YYYY-MM-DD`)
4. ✓ Commits: `chore: release 0.8.0`
5. ✓ Runs `./scripts/release-check` (tests, security, build)
6. ✓ Pushes to dev
7. ✓ Creates PR from dev → main
8. ✓ Outputs handoff instructions

**If any step fails**, the script stops and provides fix instructions.

#### Step 2: Human Reviews and Merges PR

1. Go to Codeberg and review the PR
2. Verify CI passes
3. Merge the PR (this requires human action due to branch protection)

#### Step 3: Human Creates Signed Tag

```bash
# Human runs this after merging the PR
./scripts/tag-release 0.8.0
```

This script:
1. Switches to main and pulls latest
2. Verifies version in pyproject.toml matches
3. Creates GPG-signed tag: `git tag -s v0.8.0`
4. Pushes tag to trigger release workflow
5. Optionally syncs dev with main

### Alternative: Direct Release (No Branch Protection)

If your repository doesn't have branch protection on main:

```bash
./scripts/release 0.8.0
```

This script will detect if main is protected and suggest the agent+human workflow if needed.

### Manual Dispatch (for testing)

Via Codeberg UI:
1. Go to Actions → Release workflow
2. Click "Run workflow"
3. Enter version (e.g., `0.6.0`)
4. Optionally check "Dry run"
5. Click "Run workflow"

Via API:
```bash
source .env
curl -X POST \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "ref": "dev",
    "inputs": {
      "version": "0.6.0-test",
      "dry_run": "true"
    }
  }' \
  "https://codeberg.org/api/v1/repos/iterabloom/hypergumbo/actions/workflows/release.yml/dispatches"
```

## Scripts Reference

| Script | Who Runs | Purpose |
|--------|----------|---------|
| `./scripts/prepare-release VERSION` | Agent | Full preparation: bump, changelog, validate, PR |
| `./scripts/tag-release VERSION` | Human | Create signed tag after PR merge |
| `./scripts/release VERSION` | Either | Legacy single-step (detects branch protection) |
| `./scripts/release-check` | Either | Validate release readiness (standalone) |
| `./scripts/bump-version VERSION` | Either | Just bump version (used by prepare-release) |

## Dry Run Mode

Both `prepare-release` and `tag-release` support `--dry-run`:

```bash
./scripts/prepare-release 0.8.0 --dry-run  # Shows what would happen
./scripts/tag-release 0.8.0 --dry-run      # Shows what would happen
```

For the CI workflow, dry run skips:
- PyPI publishing
- Forgejo release creation

Use dry run to:
- Verify the build process works
- Test the workflow after changes
- Validate a pre-release version

## Prerelease Detection

Versions containing these strings are marked as prereleases:
- `dev` (e.g., `0.6.0.dev1`)
- `rc` (e.g., `0.6.0-rc1`)
- `alpha` (e.g., `0.6.0-alpha`)
- `beta` (e.g., `0.6.0-beta`)

Prereleases are:
- Published to PyPI (but not default install)
- Marked as prerelease on Forgejo

## Troubleshooting

### Branch Protection Errors

**Symptom:** `remote: Forgejo: Not allowed to push to protected branch main`

**Solution:** Use the agent+human workflow:
```bash
./scripts/prepare-release VERSION  # Creates PR instead of direct push
# Merge PR on Codeberg
./scripts/tag-release VERSION      # Tags after merge
```

### Coverage Failure in CI (98-99%)

**Symptom:** Tests pass locally with 100% but fail in CI with ~99%

**Cause:** `sentence-transformers` (1GB+ with PyTorch) may not install on small CI runners, causing embedding-related code paths to be skipped.

**Solution:** This is handled automatically. The workflow uses `.coveragerc.no-embeddings` when embeddings are unavailable.

### pip-audit Fails on Non-PyPI Packages

**Symptom:** `pip-audit --strict` fails with warnings about packages not on PyPI

**Cause:** `tree-sitter-lean` and `tree-sitter-wolfram` are built from source.

**Solution:** `release-check` now uses `pip-audit` without `--strict` and distinguishes real vulnerabilities from non-PyPI warnings.

### Integration Tests OOM-Killed

**Symptom:** Integration tests fail with `ANALYSIS_FAILED` on Flask or Gin repos

**Cause:** Small CI runners don't have enough memory for large repos.

**Solution:** The `--quick` mode now only tests Express (proven to work on small runners).

### Workflow Doesn't Trigger on Tag Push

- Verify the tag matches `v*` pattern
- Check that tag was pushed: `git push origin v0.6.0`
- Ensure you didn't just push to a branch named after the tag

### Tests Fail in Release but Pass Locally

- Check Python version differences (CI tests 3.10-3.13)
- Run `./scripts/ci-debug analyze-deps` for dependency issues
- Some grammars need `./scripts/build-source-grammars` first

### PyPI Publish Fails

- Verify `PYPI_TOKEN` is set in repository secrets
- Check token hasn't expired
- Ensure version doesn't already exist on PyPI

### Forgejo Release Creation Fails

- Verify `FORGEJO_TOKEN` is set and has write permissions
- Check API rate limits

### Local Main Diverged from Origin

**Symptom:** `Your branch is ahead of 'origin/main' by N commits`

**Cause:** Failed direct pushes accumulated locally.

**Solution:**
```bash
git checkout main
git reset --hard origin/main
```

### Tag Already Exists

**Symptom:** Need to recreate a tag after failed release attempt

**Solution:**
```bash
# Delete local and remote tag
git tag -d v0.8.0
git push origin :refs/tags/v0.8.0

# Recreate
git tag -s v0.8.0 -m "Release v0.8.0"
git push origin v0.8.0
```

## Post-Release Checklist

- [ ] Verify package on PyPI: https://pypi.org/project/hypergumbo/
- [ ] Verify release on Codeberg: https://codeberg.org/iterabloom/hypergumbo/releases
- [ ] Test installation: `pip install hypergumbo==X.Y.Z`
- [ ] Sync dev with main (if not done by tag-release):
  ```bash
  git checkout dev && git merge main && git push origin dev
  ```
- [ ] Announce release (if significant)

## Platform Notes

Codeberg/Forgejo only provides Linux runners (`codeberg-small-lazy`). Multi-platform testing (macOS, Windows) should be done locally before tagging a release.

## Two-User Workflow (Optional)

For attribution clarity, you can use separate accounts:
- **Agent account** (e.g., `jgstern_agent`): Runs `prepare-release`, creates PRs
- **Human account** (e.g., `jgstern`): Merges PRs, runs `tag-release`

This makes it clear in the git history which actions were automated vs. human-authorized.
