<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ALLOWED_WEBSITES.md
# Security boundary: outbound network access is restricted to these domains only. If a link redirects to a non-allowlisted domain, do not follow it.
# Changes to this file require human approval.

## Rules
- Prefer HTTPS.
- Read-only research: GET/HEAD only (no posting credentials, no uploading repo content).
- No authentication tokens may be pasted into URLs or headers.
- If a needed domain is missing, stop and request human approval to add it (domain + rationale).

## Search / discovery
- duckduckgo.com
- bing.com
- google.com
- scholar.google.com

## Source code hosting / forge
- github.com
- raw.githubusercontent.com
- api.github.com
- gitlab.com
- bitbucket.org
- codeberg.org

## CI
# This project's own Woodpecker instance, named by WOODPECKER_SERVER in .env
# rather than written here, so the host is not published in a public repo.
# Needed because CI logs are NOT retrievable through the GitHub API — the
# instance sits behind Cloudflare Access — so a failing pipeline is
# undiagnosable from the agent side without reaching it (WI-solob).
- $WOODPECKER_SERVER  (see .env)

## Papers / scholarly
- arxiv.org
- openreview.net
- semanticscholar.org
- aclanthology.org
- proceedings.mlr.press
- jmlr.org

## Official docs / standards (high-signal references)
- docs.python.org
- developer.mozilla.org
- rfc-editor.org
- tree-sitter.github.io
- woodpecker-ci.org

## Package registries (lookup + installs, depending on language)
# Container artifacts
- hub.docker.com
- quay.io
# Machine-learning artifacts
- huggingface.co
# Python
- pypi.org
- files.pythonhosted.org
# Rust
- crates.io
- docs.rs
# Node
- npmjs.com
- registry.npmjs.org
# .NET
- nuget.org
# Ruby
- rubygems.org
# PHP
- packagist.org
# Conda (optional)
- conda-forge.org
- anaconda.org
# Developer CLI tooling
# GitHub CLI (`gh`) apt repository and its signing key, both served from
# this host (`/packages`, `/packages/githubcli-archive-keyring.gpg`).
# RATIONALE: `scripts/contribute` genuinely invokes `gh pr list` / `gh pr
# create` for the fork-based external-contributor flow, so a working `gh`
# is a real dependency of shipped tooling, not a convenience. The
# distro-packaged build (Ubuntu 2.45.0-1ubuntu0.3) emits a malformed
# User-Agent — `GitHub CLI ` with a trailing space, the version string
# never injected — which api.github.com rejects as an HTTP/2
# PROTOCOL_ERROR, so *every* gh API call fails on that build. Upstream
# builds set the header correctly.
# SCOPE LIMIT, stated rather than left implicit: this covers the apt path
# ONLY. GitHub serves release-tarball assets from
# objects.githubusercontent.com, which is deliberately NOT added here — the
# tarball route remains outside the allowlist.
- cli.github.com

## Security advisories / CVEs
- osv.dev
- nvd.nist.gov
- cve.org
- github.com

