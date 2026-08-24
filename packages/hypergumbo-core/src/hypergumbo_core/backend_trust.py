# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-repository backend trust grants (ADR-0045 rulings 5-8).

WHAT IS BEING STORED, AND WHY IT IS NOT CONFIGURATION. Enabling the SCIP Rust
backend makes indexing execute the analysed crate's ``build.rs`` and proc
macros as the invoking user. Before this module the only durable way to say
"yes" was ``export HYPERGUMBO_RUST_ANALYZER=1`` from a shell profile — which is
GLOBAL, so it opted in every Rust repository the user would ever analyse,
including ones cloned specifically to audit or triage (WI-sobig). The decision
is per-repository; the only persistence available had the wrong scope for it.

Configuration was not the answer either, and that is ADR-0045's central
ruling: ``$XDG_CONFIG_HOME`` is a directory people deliberately sync between
machines and commit to dotfiles repositories. That portability is the entire
point of it and is disqualifying for a grant of code execution — a synced
``rust_analyzer = true`` is the global environment variable again, reached by
a nicer route. So grants live under ``$XDG_STATE_HOME`` ("state that persists
but is not configuration"), which is also where direnv keeps its equivalent.

KEYED BY RESOLVED ABSOLUTE PATH — deliberately NOT by ADR-0013's
repo-fingerprint (remote URL + first commit SHA). That key exists so multiple
checkouts SHARE a cache, which is right for a cache and exactly wrong here: two
clones of one remote can have entirely different working trees, and it is the
tree whose ``build.rs`` runs. Paths are resolved so the same tree reached
through a symlink cannot acquire a second, divergent decision.

A DECLINE IS A DECISION. ``read_decision`` returns ``None`` for "never asked"
and a ``TrustDecision`` with ``granted=False`` for "asked and said no" — the
same three-valued discipline :mod:`hypergumbo_core.backend_selection` uses, and
for the same reason: a consumer that cannot tell absence from refusal will keep
asking someone who has already answered, and this project has already written
down (``TestRustAnalyzerDisclosureRespectsTheGate``) that a prompt firing when
it is moot trains people to skim the one that matters.

THE HASH IS ADVISORY, AND THAT IS THE WEAKEST DECISION HERE. A grant records a
digest of the repository's build manifests; a later change surfaces
``manifest_changed`` but does NOT revoke. Revoking would re-prompt on an
ordinary dependency bump, and by the reasoning above that trains skimming. This
is ADR-0045 OQ1, still open — if it resolves toward strictness, the behaviour
changes here and the test that pins it says so.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .user_config import BACKENDS_EXECUTING_ANALYSED_CODE

#: Files whose contents are hashed into a grant's advisory digest — the ones
#: that decide what indexing will EXECUTE. Cargo.lock is deliberately absent:
#: it changes on every dependency bump without changing what runs at index
#: time, and a digest that churns is a digest nobody reads.
_BUILD_MANIFESTS = ("build.rs", "Cargo.toml")

_STORE_DIRNAME = "trust.d"


@dataclass(frozen=True)
class TrustDecision:
    """One recorded answer about one backend on one repository path."""

    backend: str
    granted: bool
    repo_path: str
    manifest_digest: str
    #: True when the build manifests differ from those present when the grant
    #: was recorded. Advisory — see the module docstring and ADR-0045 OQ1.
    manifest_changed: bool = False


def trust_store_root(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """``$XDG_STATE_HOME/hypergumbo/trust.d``, or the XDG default.

    Separate from :func:`hypergumbo_core.user_config.user_config_path` on
    purpose; a test asserts the two do not nest, so that a later tidy-up
    cannot quietly move grants into the portable directory.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME")
    root = Path(base) if base else (home or Path.home()) / ".local" / "state"
    return root / "hypergumbo" / _STORE_DIRNAME


def _repo_key(repo_root: Path) -> str:
    """Stable filename for a repository PATH.

    Hashed rather than path-escaped so the store stays a flat directory of
    fixed-length names — a path with separators, spaces or non-UTF-8 bytes
    would otherwise have to be encoded, and every encoding scheme has a
    collision story. The readable path is kept INSIDE the record.
    """
    resolved = str(Path(repo_root).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def _manifest_digest(repo_root: Path) -> str:
    """Digest of the files that decide what indexing will execute.

    Returns ``""`` when none are present, which is a meaningful value rather
    than a missing one: a repository with no build script has nothing for the
    hash to protect, so a later "changed" report would be noise.
    """
    hasher = hashlib.sha256()
    found = False
    for name in _BUILD_MANIFESTS:
        candidate = Path(repo_root) / name
        if not candidate.is_file():
            continue
        found = True
        hasher.update(name.encode("utf-8"))
        hasher.update(candidate.read_bytes())
    return hasher.hexdigest() if found else ""


def _record_path(repo_root: Path, environ: Optional[Mapping[str, str]]) -> Path:
    return trust_store_root(environ=environ) / f"{_repo_key(repo_root)}.json"


def record_decision(
    repo_root: Path,
    backend: str,
    granted: bool,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> TrustDecision:
    """Record a grant or a decline for ``backend`` on ``repo_root``.

    Refuses any backend that does not execute analysed-repo code (ruling 5):
    such a backend's opt-in is an ordinary preference and belongs in the
    config file. Accepting it here would give one setting two homes.
    """
    if backend not in BACKENDS_EXECUTING_ANALYSED_CODE:
        raise ValueError(
            f"backend {backend!r} does not execute analysed-repository code, "
            f"so its opt-in is a preference and belongs in hypergumbo's "
            f"configuration file, not the trust store.",
        )
    resolved = Path(repo_root).resolve()
    decision = TrustDecision(
        backend=backend,
        granted=granted,
        repo_path=str(resolved),
        manifest_digest=_manifest_digest(resolved),
    )
    path = _record_path(resolved, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_record(path)
    existing[backend] = {
        "granted": decision.granted,
        "repo_path": decision.repo_path,
        "manifest_digest": decision.manifest_digest,
    }
    # 0o600: the grant is the user's own, and nothing else on the machine has
    # business reading which repositories they have agreed to execute.
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return decision


def _load_record(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded: Dict[str, Any] = dict(
            json.loads(path.read_text(encoding="utf-8")),
        )
        return loaded
    except (json.JSONDecodeError, OSError):  # pragma: no cover - corrupt store
        # A corrupt store must read as NO DECISION, never as a grant.
        return {}


def read_decision(
    repo_root: Path,
    backend: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[TrustDecision]:
    """The recorded decision, or ``None`` when none was ever made.

    ``None`` and ``granted=False`` are different answers; see the module
    docstring on why collapsing them makes the opt-in nudge unable to stop.
    """
    resolved = Path(repo_root).resolve()
    entry = _load_record(_record_path(resolved, environ)).get(backend)
    if entry is None:
        return None
    recorded = str(entry.get("manifest_digest", ""))
    current = _manifest_digest(resolved)
    return TrustDecision(
        backend=backend,
        granted=bool(entry.get("granted", False)),
        repo_path=str(entry.get("repo_path", str(resolved))),
        manifest_digest=recorded,
        manifest_changed=bool(recorded) and recorded != current,
    )
