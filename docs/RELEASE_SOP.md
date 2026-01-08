# Release Standard Operating Procedure (SOP)

This document describes the hypergumbo release process, including what happens when the release pipeline runs, how to trigger releases, and troubleshooting guidance.

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
- Builds source-only grammars
- Requires 100% test coverage

#### Job 2: security-audit
- **pip-audit**: Scans for known vulnerabilities in dependencies
- **Bandit**: Security linting for Python code
- **Safety**: Dependency safety check (advisory, non-blocking)
- **pip-licenses**: Audits dependency licenses, warns on copyleft
- **trufflehog**: Scans for accidentally committed secrets

#### Job 3: integration-tests
- Runs `./scripts/integration-test --quick`
- Tests CLI functionality on real repositories
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

Branch protection requires signed commits. Set up GPG signing before your first release.

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
- Git tag (`vX.Y.Z`)

Use `./scripts/bump-version X.Y.Z` to update the version.

### Changelog

Update `CHANGELOG.md` with release notes. The workflow extracts the section matching the version for the release body.

## How to Release

### Option A: Tag-Based Release (Recommended)

Requires GPG signing configured (see Prerequisites). Signed commits satisfy branch protection.

```bash
# 1. Sync dev and merge to main (creates signed merge commit)
git checkout dev && git pull origin dev
git checkout main && git pull origin main
git merge dev --no-ff -m "chore: merge dev into main for release"
git push origin main

# 2. Update version and changelog
./scripts/bump-version 0.6.0
# Edit CHANGELOG.md with release notes

# 3. Commit version bump (signed automatically)
git add pyproject.toml CHANGELOG.md
git commit -s -m "chore: release v0.6.0"

# 4. Create and push tag
git push origin main
git tag v0.6.0
git push origin v0.6.0

# 5. Merge release commit back to dev
git checkout dev
git merge main
git push origin dev
```

The workflow triggers automatically on tag push.

**Without GPG signing:** Use PRs instead of direct pushes for steps 1 and 5.

### Option B: Manual Dispatch (for testing)

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

## Dry Run Mode

Dry run skips:
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

### Workflow doesn't trigger on tag push
- Verify the tag matches `v*` pattern
- Check that tag was pushed: `git push origin v0.6.0`

### Tests fail in release workflow but pass locally
- Check Python version differences
- Run `./scripts/ci-debug analyze-deps` for dependency issues
- Some grammars need `./scripts/build-source-grammars` first

### PyPI publish fails
- Verify `PYPI_TOKEN` is set in repository secrets
- Check token hasn't expired
- Ensure version doesn't already exist on PyPI

### Forgejo release creation fails
- Verify `FORGEJO_TOKEN` is set and has write permissions
- Check API rate limits

### SBOM generation fails
- Non-blocking; check cyclonedx-bom installation
- May fail in minimal environments

## Post-Release Checklist

- [ ] Verify package on PyPI: https://pypi.org/project/hypergumbo/
- [ ] Verify release on Codeberg: https://codeberg.org/iterabloom/hypergumbo/releases
- [ ] Test installation: `pip install hypergumbo==X.Y.Z`
- [ ] Update CHANGELOG.md if needed
- [ ] Announce release (if significant)

## Platform Notes

Codeberg/Forgejo only provides Linux runners (`codeberg-small-lazy`). Multi-platform testing (macOS, Windows) should be done locally before tagging a release.
