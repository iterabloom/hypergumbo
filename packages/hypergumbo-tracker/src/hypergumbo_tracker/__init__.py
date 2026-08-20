# SPDX-License-Identifier: MPL-2.0
"""Hypergumbo Tracker: YAML-backed structured tracker for agent governance.

This package provides a structured, append-only op-log tracker that replaces
fragile grep-based markdown governance files with YAML-backed storage.
Key features:

- **Append-only op logs:** Each item is stored as a sequence of immutable ops
  in a YAML file, enabling git-merge-safe concurrent edits via merge=union.
- **Nonce-on-every-line:** Every line carries a unique inline comment to prevent
  git merge=union from fusing or deduplicating lines across ops.
- **Lamport clock:** Cross-branch causal ordering via git cat-file --batch peek.
- **Content-hash IDs:** Proquint-encoded SHA-256 IDs for natural deduplication.
- **SimHash near-duplicate detection:** Fast fingerprint comparison on add().
- **Compile:** Deterministic fold of op log into current item state using LWW
  for scalars and accumulation for set-valued fields.
- **Lock enforcement:** Human-locked fields reject agent writes, enforced
  cross-branch via the same Lamport peek mechanism.
- **TrackerSet:** Multi-tier unified view merging canonical, workspace, and
  stealth Stores with write routing, tier movement, and reconciliation.
- **Cache:** Per-tier SQLite read cache with incremental invalidation,
  write-through on mutations, and corruption recovery.
- **Journal:** Out-of-repo mirror of every op, written inside the store's
  own file lock, so no in-repo git command can lose a pending op.
  ``recover`` union-restores from it; the git hooks call it automatically.

Beyond storage, the package ships the surfaces that consume it:

- **TUI** (``tui.py``): the full-screen interactive client, and the largest
  module in the package.
- **Web server** (``serve.py`` and the ``serve_auth_*`` modules): an HTTP
  view with WebAuthn, password, session and duress-code authentication
  (ADR-0019).
- **Sync** (``sync.py``, ``sync_log.py``): the auto-sync mechanism that
  batches pending ops into a branch, pushes, polls CI and merges, plus its
  audit log.
- **Stop hook** (``stop_hook.py``): the agent-governance integration that
  computes stopping guidance from tracker state.
- **Embeddings** (``embeddings.py``): optional Tier-2 semantic
  near-duplicate detection, complementing the SimHash fingerprint above.

See ADR-0013 for the full design specification.
"""

from hypergumbo_tracker.cache import Cache
from hypergumbo_tracker.migration import migrate
from hypergumbo_tracker.models import Tier
from hypergumbo_tracker.trackerset import TrackerSet
from hypergumbo_tracker.validation import validate_all, validate_ops_file

__all__ = [
    "Cache",
    "Tier",
    "TrackerSet",
    "__version__",
    "migrate",
    "validate_all",
    "validate_ops_file",
]
__version__ = "0.8.0"
