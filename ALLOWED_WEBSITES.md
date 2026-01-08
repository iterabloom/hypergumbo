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

## Security advisories / CVEs
- osv.dev
- nvd.nist.gov
- cve.org
- github.com

