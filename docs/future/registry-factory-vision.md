# Registry & Factory Vision (Future Work)

> **Note**: This document captures aspirational ideas for a registry and factory system. These are speculative features that would only make sense if there's demonstrated demand for custom analyzers. Currently, the general-purpose analyzer works well enough that custom composition isn't needed.

## Registry

A place to share analyzers and discover what works for repos like yours.

### Artifacts stored

* **Analyzer capsules** (code or container image)
* **Rule packs** (linter rules, invariant checkers)
* **Schema versions** supported (compatibility matrix)
* **Behavioral fingerprint** from benchmark runs

### Repo profiles

* **Language mix**: percentages, framework signals
* **Architectural features**: endpoint count, database access patterns, IPC usage
* **No source code** uploaded (privacy-preserving)

### Similarity search

* **Nearest-neighbor** on profile vectors + analyzer fingerprints
* **Use case**: "Your repo looks like 50 others; here's an analyzer that worked well for them"

### Trust and provenance

**Phase 1: Centralized trust**
* hypergumbo team runs registry + signing authority
* Analyzers submitted for review + benchmark
* Approved analyzers get signed by central key

**Phase 2: Sigstore/transparency log**
* Analyzers signed by author's key
* Signatures recorded in append-only transparency log (Rekor)
* Benchmarks + reviews published alongside

**Future: Web-of-trust**
* Users build their own trust networks
* No central authority

### Lightweight alternative

If full registry proves unnecessary:
* GitHub repo: `hypergumbo-community/capsule-examples`
* Organized by framework: `fastapi/`, `flask/`, `electron/`, `nextjs/`
* Community PRs welcome
* No server infrastructure

## "Factory" Evolution

Generation becomes retrieval-augmented:

1. **Profile repo** → get nearest-neighbor analyzers/rulepacks from registry
2. **Compose starter analyzer** from proven parts (not full LLM generation)
3. **Use LLM only to**:
   * Adapt deltas for repo-specific patterns
   * Create missing cross-language linkers
   * Generate summaries for domain-specific flows
4. **Run benchmark suite** + local tests → self-repair loop until stable
5. **Optionally publish** to registry (with sanitized metadata only)

**Key idea:** Amortize LLM cost via reuse. Most repos get 90% working analyzer from registry; LLM only fills 10% gaps.

## Privacy & Security for Registry

### Opt-in registry sharing

**Default: k-anonymity approach**
* Client computes profile locally
* Downloads cluster centroids from registry
* Finds nearest centroid locally
* Sends only cluster ID (not full profile)
* Server never sees individual repo profile

**Opt-in personalized recommendations**
* Explicit consent required
* Profile includes: language mix, framework signals, aggregate metrics
* Profile excludes: source code, symbol names, file paths
* Data retained 90 days max (GDPR compliance)

**Self-hosted registry**
* Organizations can run registry on-premise
* All data stays internal
* Full control over profiles, analyzers, benchmarks

### Analyzer sandboxing

* **Containers** (Docker/Podman) for untrusted capsules
* **Restricted runner** (seccomp/AppArmor profiles) for Python scripts
* **Signed artifacts** for registry distribution

## Technology Choices

### Registry backend

**Hosted (managed service)**:
* Storage: S3 + CloudFront
* Database: PostgreSQL + pgvector (similarity search)

**Self-hosted simple (SQLite + files)**:
* Storage: Local filesystem
* Database: SQLite with FTS
* Deployment: Docker Compose

**Self-hosted production (MinIO + Postgres)**:
* Storage: MinIO (S3-compatible)
* Database: PostgreSQL + pgvector
* Deployment: Docker Compose or Kubernetes

### Signing/provenance
* **Primary**: Sigstore (Cosign for signing, Rekor for transparency log)
* **Fallback**: PGP/GPG (web-of-trust model)
