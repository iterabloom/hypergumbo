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

A CHANGED ``build.rs`` REVOKES; A CHANGED ``Cargo.toml`` DOES NOT. Owner
ruling 2026-08-23, resolving ADR-0045 OQ1. The split is the substance:
``build.rs`` is the file that actually EXECUTES during indexing and it changes
rarely, so a prompt about it carries information, while ``Cargo.toml`` churns
with routine dependency bumps and re-prompting on that is how a person learns
to click through the prompt that matters. Revocation is applied inside
:func:`read_decision` rather than at each call site, because a consumer that
had to remember the check would eventually forget, and forgetting fails OPEN.

HONEST LIMIT, on the table when the ruling was made: only TOP-LEVEL manifests
are hashed, so no form of this protects against a *dependency's* build script.
It catches tampering with this project's own build script, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .user_config import BACKENDS_EXECUTING_ANALYSED_CODE

#: Files whose change REVOKES a grant. Owner ruling 2026-08-23 resolving
#: ADR-0045 OQ1: ``build.rs`` is the file that actually EXECUTES during
#: indexing and it changes rarely, so a prompt about it carries information.
#: ``Cargo.toml`` is deliberately NOT here — it churns with routine dependency
#: bumps, and re-prompting on that is how a person learns to click through the
#: prompt that matters (the reasoning this project already wrote down in
#: ``TestRustAnalyzerDisclosureRespectsTheGate``).
#:
#: HONEST LIMIT, decided with this on the table: only TOP-LEVEL manifests are
#: hashed, so no form of this protects against a *dependency's* build script.
#: It catches tampering with this project's own build script, and nothing else.
_REVOKING_MANIFESTS = ("build.rs",)

#: Hashed and reported, but never revoking — a changed ``Cargo.toml`` is worth
#: seeing on ``--show`` and is not worth interrupting a run for.
_ADVISORY_MANIFESTS = ("Cargo.toml",)

_STORE_DIRNAME = "trust.d"


@dataclass(frozen=True)
class TrustDecision:
    """One recorded answer about one backend on one repository path."""

    backend: str
    #: The EFFECTIVE answer: a grant whose ``build.rs`` has changed reports
    #: ``False`` here, because the owner's 2026-08-23 ruling makes that change
    #: revoking. ``recorded_grant`` keeps what was actually written down, so a
    #: caller can tell "you said no" from "you said yes and the build script
    #: then changed" — two very different things to show a person.
    granted: bool
    repo_path: str
    build_digest: str
    advisory_digest: str
    #: ``build.rs`` differs from the grant. REVOKING (ADR-0045 ruling 7 as
    #: amended).
    build_changed: bool = False
    #: ``Cargo.toml`` differs. Reported, never revoking.
    advisory_changed: bool = False
    #: What was written down, before revocation is applied.
    recorded_grant: bool = False


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


def _digest_of(repo_root: Path, names: "tuple[str, ...]") -> str:
    """Digest of ``names`` under ``repo_root``, or ``""`` if none are present.

    The empty string is a meaningful value rather than a missing one: a
    repository with no build script has nothing for the hash to protect, so a
    later "changed" report about it would be pure noise.
    """
    hasher = hashlib.sha256()
    found = False
    for name in names:
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
        recorded_grant=granted,
        repo_path=str(resolved),
        build_digest=_digest_of(resolved, _REVOKING_MANIFESTS),
        advisory_digest=_digest_of(resolved, _ADVISORY_MANIFESTS),
    )
    path = _record_path(resolved, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_record(path)
    existing[backend] = {
        "granted": decision.recorded_grant,
        "repo_path": decision.repo_path,
        "build_digest": decision.build_digest,
        "advisory_digest": decision.advisory_digest,
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
    recorded_build = str(entry.get("build_digest", ""))
    recorded_adv = str(entry.get("advisory_digest", ""))
    build_changed = bool(recorded_build) and recorded_build != _digest_of(
        resolved, _REVOKING_MANIFESTS,
    )
    recorded_grant = bool(entry.get("granted", False))
    return TrustDecision(
        backend=backend,
        # The revocation is applied HERE, at the one place every consumer
        # reads through, rather than at each caller. A gate that had to
        # remember to check build_changed itself is a gate that eventually
        # forgets — and forgetting fails OPEN, executing a build script the
        # user never approved.
        granted=recorded_grant and not build_changed,
        recorded_grant=recorded_grant,
        repo_path=str(entry.get("repo_path", str(resolved))),
        build_digest=recorded_build,
        advisory_digest=recorded_adv,
        build_changed=build_changed,
        advisory_changed=bool(recorded_adv) and recorded_adv != _digest_of(
            resolved, _ADVISORY_MANIFESTS,
        ),
    )
