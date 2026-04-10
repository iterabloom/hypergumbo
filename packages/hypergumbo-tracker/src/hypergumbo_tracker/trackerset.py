# SPDX-License-Identifier: MPL-2.0
"""TrackerSet: multi-tier unified view over canonical, workspace, and stealth Stores.

Merges three Store instances into a single read surface with tier-annotated
CompiledItems, write routing to the correct tier, tier movement operations
(promote/demote/stealth/unstealth), scope-aware count_todos, and self-healing
reconciliation for cross-tier duplicates.

Design rationale:
- Three tiers map to three physical directories under tracker_root:
  canonical: tracker_root/"tracker"/".ops"
  workspace: tracker_root/"tracker-workspace"/".ops"
  stealth:   tracker_root/"tracker-workspace"/"stealth" (acts as its own .ops)
- Each tier has its own Store instance with independent config loading.
- Merged reads annotate each CompiledItem with .tier so consumers know provenance.
- Write routing defaults to workspace; callers can specify tier explicitly.
- Tier movement appends an audit op and physically moves the ops file.
- Reconciliation is append-only (never deletes ops), capped at 3 attempts.
- Cache integration is optional — TrackerSet works without it.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import (
    CompiledItem,
    Tier,
    TrackerConfig,
    load_config,
    resolve_actor,
)
from hypergumbo_tracker.store import (
    AmbiguousPrefixError,
    HumanAuthorityError,
    ItemNotFoundError,
    Store,
    _make_nonce,
    _parse_ops_file,
    _serialize_op,
    compile_ops,
)


# Maximum reconciliation attempts per item before giving up
_MAX_RECONCILE_ATTEMPTS = 3


class TierMovementError(Exception):
    """Raised when a tier movement is invalid (wrong source tier)."""


class ReconciliationError(Exception):
    """Raised when reconciliation fails persistently."""


class TrackerSet:
    """Multi-tier unified view over canonical, workspace, and stealth Stores.

    Args:
        tracker_root: Path to the .agent/ directory (or equivalent).
            Derives three ops directories from this root.
        config: Optional TrackerConfig. If None, loads from the canonical
            tracker directory.
        cache_dir: Optional path for SQLite read caches. If None, no caching.
    """

    def __init__(
        self,
        tracker_root: Path,
        config: TrackerConfig | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._tracker_root = tracker_root

        # Derive ops directories
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"
        stealth_ops = tracker_root / "tracker-workspace" / "stealth"

        # Ensure directories exist
        canonical_ops.mkdir(parents=True, exist_ok=True)
        workspace_ops.mkdir(parents=True, exist_ok=True)
        stealth_ops.mkdir(parents=True, exist_ok=True)

        # Load config from canonical tracker dir if not provided
        if config is None:
            config = load_config(tracker_root / "tracker")

        self._config = config

        # Create Store instances
        self._canonical = Store(canonical_ops, config=config)
        self._workspace = Store(workspace_ops, config=config)
        self._stealth = Store(stealth_ops, config=config)

        # Tier → Store mapping
        self._tier_stores: dict[Tier, Store] = {
            Tier.CANONICAL: self._canonical,
            Tier.WORKSPACE: self._workspace,
            Tier.STEALTH: self._stealth,
        }

        # Cache integration (optional)
        self._cache_dir = cache_dir
        self._caches: dict[Tier, Any] | None = None

        # Positional alias stash (merged list across all tiers)
        self._last_list: list[str] = []
        self._load_last_list()

    @property
    def config(self) -> TrackerConfig:
        """Return the tracker configuration."""
        return self._config

    @property
    def canonical(self) -> Store:
        """Return the canonical tier Store."""
        return self._canonical

    @property
    def workspace(self) -> Store:
        """Return the workspace tier Store."""
        return self._workspace

    @property
    def stealth(self) -> Store:
        """Return the stealth tier Store."""
        return self._stealth

    def set_caches(self, caches: dict[Tier, Any]) -> None:
        """Set cache instances for each tier.

        Called by integration code to wire Cache objects into TrackerSet.
        """
        self._caches = caches

    # -------------------------------------------------------------------
    # Merged reads
    # -------------------------------------------------------------------

    def list_items(
        self,
        status: str | list[str] | None = None,
        kind: str | None = None,
        tag: str | None = None,
        tier: Tier | None = None,
    ) -> list[CompiledItem]:
        """Merge items from all 3 tiers, annotate each with .tier.

        If tier is specified, only return items from that tier.
        Passes per-tier cache to Store.list_items() for cache-accelerated reads.
        Updates the positional alias stash for cross-invocation :N persistence.
        """
        items: list[CompiledItem] = []

        tiers_to_query = [tier] if tier is not None else list(Tier)
        for t in tiers_to_query:
            store = self._tier_stores[t]
            cache = self._caches.get(t) if self._caches else None
            tier_items = store.list_items(
                status=status, kind=kind, tag=tag, cache=cache
            )
            for item in tier_items:
                item.tier = t
            items.extend(tier_items)

        items.sort(key=lambda i: (i.priority, i.created_at))

        # Update positional alias stash (merged, sorted order)
        self._last_list = [i.id for i in items]
        self._save_last_list()

        return items

    def ready(self) -> list[CompiledItem]:
        """Unblocked items across all tiers.

        Cross-tier before references are resolved: if item X in canonical
        has before: [Y] and Y is in workspace, Y is still blocked.
        Passes per-tier cache for cache-accelerated reads.
        Updates the positional alias stash.
        """
        # Collect all items from all tiers
        all_items: list[CompiledItem] = []
        for t, store in self._tier_stores.items():
            cache = self._caches.get(t) if self._caches else None
            compile_fn = (
                store._compile_all_cached(cache)
                if cache is not None
                else store._compile_all()
            )
            for item in compile_fn:
                item.tier = t
                all_items.append(item)

        # Build resolved set
        resolved_ids: set[str] = set()
        for item in all_items:
            if item.status in self._config.resolved_statuses:
                resolved_ids.add(item.id)

        # Build blocked set (cross-tier aware)
        blocked_ids: set[str] = set()
        for item in all_items:
            if item.id not in resolved_ids:
                for target_id in item.before:
                    blocked_ids.add(target_id)

        blocking_set = set(self._config.blocking_statuses)
        ready_items: list[CompiledItem] = []

        for item in all_items:
            if item.status not in blocking_set:
                continue
            if item.duplicate_of:
                continue
            if item.cross_tier_conflict:
                continue
            if item.id in blocked_ids:
                continue
            ready_items.append(item)

        ready_items.sort(key=lambda i: (i.priority, i.created_at))

        # Update positional alias stash (merged, sorted order)
        self._last_list = [i.id for i in ready_items]
        self._save_last_list()

        return ready_items

    def get(self, item_id: str) -> CompiledItem:
        """Get item by ID, searching all tiers.

        Raises:
            ItemNotFoundError: If item not found in any tier.
            AmbiguousPrefixError: If prefix matches items in multiple tiers.
        """
        _, store, t = self._resolve_id(item_id)
        item = store.get(item_id)
        item.tier = t
        return item

    def children(self, item_id: str) -> list[CompiledItem]:
        """Return children across all tiers."""
        # Resolve to full ID first
        full_id, _, _ = self._resolve_id(item_id)

        all_items: list[CompiledItem] = []
        for t, store in self._tier_stores.items():
            for item in store._compile_all():
                item.tier = t
                all_items.append(item)

        return [i for i in all_items if i.parent == full_id]

    def ancestors(self, item_id: str) -> list[CompiledItem]:
        """Return ancestors across all tiers."""
        full_id, _, _ = self._resolve_id(item_id)

        all_items: list[CompiledItem] = []
        for t, store in self._tier_stores.items():
            for item in store._compile_all():
                item.tier = t
                all_items.append(item)

        item_map = {i.id: i for i in all_items}
        result: list[CompiledItem] = []
        current_id = full_id
        visited: set[str] = set()

        while current_id in item_map:
            item = item_map[current_id]
            if item.parent is None or item.parent in visited:
                break
            visited.add(item.parent)
            if item.parent in item_map:
                result.append(item_map[item.parent])
                current_id = item.parent
            else:
                break

        return result

    # -------------------------------------------------------------------
    # Write routing
    # -------------------------------------------------------------------

    def add(
        self,
        kind: str,
        title: str,
        tier: Tier = Tier.WORKSPACE,
        **kwargs: Any,
    ) -> str:
        """Route add() to the specified tier's Store. Default: workspace."""
        store = self._tier_stores[tier]
        item_id = store.add(kind=kind, title=title, **kwargs)
        self._cache_upsert_item(tier, store, item_id)
        return item_id

    def update(self, item_id: str, **kwargs: Any) -> None:
        """Resolve item to its tier, delegate update to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.update(full_id, **kwargs)
        self._cache_upsert_item(t, store, full_id)

    def discuss(self, item_id: str, message: str, **kwargs: Any) -> None:
        """Resolve item to its tier, delegate discuss to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.discuss(full_id, message=message, **kwargs)
        self._cache_upsert_item(t, store, full_id)

    def lock(self, item_id: str, field_names: list[str]) -> None:
        """Resolve item to its tier, delegate lock to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.lock(full_id, field_names)
        self._cache_upsert_item(t, store, full_id)

    def unlock(self, item_id: str, field_names: list[str]) -> None:
        """Resolve item to its tier, delegate unlock to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.unlock(full_id, field_names)
        self._cache_upsert_item(t, store, full_id)

    def freeze(self, item_id: str) -> None:
        """Resolve item to its tier, delegate freeze to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.freeze(full_id)

    def unfreeze(self, item_id: str) -> None:
        """Resolve item to its tier, delegate unfreeze to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.unfreeze(full_id)

    def is_frozen(self, item_id: str) -> bool:
        """Check if an item is frozen."""
        full_id, store, t = self._resolve_id(item_id)
        return store.is_frozen(full_id)

    def drift_check(self, item_id: str) -> bool | None:
        """Check freeze drift for an item."""
        full_id, store, t = self._resolve_id(item_id)
        return store.drift_check(full_id)

    def repair_drift(self, item_id: str) -> None:
        """Resolve item to its tier, delegate repair_drift to that Store."""
        full_id, store, t = self._resolve_id(item_id)
        store.repair_drift(full_id)

    # -------------------------------------------------------------------
    # Scope-aware queries
    # -------------------------------------------------------------------

    def count_todos(self) -> int:
        """Count blocking items respecting config scope.

        scope="all": canonical + workspace + stealth
        scope="workspace": workspace + stealth only
        Stealth is always counted regardless of scope.
        """
        blocking_set = set(self._config.blocking_statuses)
        count = 0

        tiers_to_count: list[Tier]
        if self._config.scope == "workspace":
            tiers_to_count = [Tier.WORKSPACE, Tier.STEALTH]
        else:
            tiers_to_count = list(Tier)

        for t in tiers_to_count:
            store = self._tier_stores[t]
            for item in store._compile_all():
                if item.status in blocking_set:
                    count += 1

        return count

    def hash_todos(self) -> str:
        """SHA256 fingerprint of blocking items for circuit-breaker detection.

        Returns hex string of SHA256(sorted lines of "id\\tstatus\\ttitle\\n").
        Only includes identity + status — ignores discussion, fields, etc.
        Respects scope setting.
        """
        import hashlib

        blocking_set = set(self._config.blocking_statuses)

        tiers_to_count: list[Tier]
        if self._config.scope == "workspace":
            tiers_to_count = [Tier.WORKSPACE, Tier.STEALTH]
        else:
            tiers_to_count = list(Tier)

        lines: list[str] = []
        for t in tiers_to_count:
            store = self._tier_stores[t]
            for item in store._compile_all():
                if item.status in blocking_set:
                    lines.append(f"{item.id}\t{item.status}\t{item.title}\n")

        lines.sort()
        h = hashlib.sha256("".join(lines).encode())
        return h.hexdigest()

    # -------------------------------------------------------------------
    # Tier movement
    # -------------------------------------------------------------------

    def promote(self, item_id: str) -> None:
        """Promote item from workspace → canonical.

        Appends a promote op to the item's ops file, then moves the file
        from workspace to canonical.

        Raises:
            TierMovementError: If item is not in workspace.
            ItemNotFoundError: If item not found.
        """
        full_id, store, t = self._resolve_id(item_id)
        if t != Tier.WORKSPACE:
            raise TierMovementError(
                f"Can only promote from workspace, but {full_id} is in {t.value}"
            )

        src_path = store.item_path(full_id)
        dst_path = self._canonical.item_path(full_id)

        # Append promote op before moving
        by, actor = resolve_actor(self._config.agent_usernames)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        op_dict = {
            "op": "promote",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
        }
        store._append_op(src_path, op_dict)

        # Move file
        shutil.move(str(src_path), str(dst_path))

        # Update caches
        self._cache_delete_item(Tier.WORKSPACE, full_id)
        self._cache_upsert_item(Tier.CANONICAL, self._canonical, full_id)

    def demote(self, item_id: str) -> None:
        """Demote item from canonical → workspace.

        Appends a demote op, then moves the file.

        Raises:
            TierMovementError: If item is not in canonical.
            ItemNotFoundError: If item not found.
        """
        full_id, store, t = self._resolve_id(item_id)
        if t != Tier.CANONICAL:
            raise TierMovementError(
                f"Can only demote from canonical, but {full_id} is in {t.value}"
            )

        src_path = store.item_path(full_id)
        dst_path = self._workspace.item_path(full_id)

        by, actor = resolve_actor(self._config.agent_usernames)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        op_dict = {
            "op": "demote",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
        }
        store._append_op(src_path, op_dict)

        shutil.move(str(src_path), str(dst_path))

        self._cache_delete_item(Tier.CANONICAL, full_id)
        self._cache_upsert_item(Tier.WORKSPACE, self._workspace, full_id)

    def stealth_item(self, item_id: str) -> None:
        """Move item from workspace → stealth. Human-authority only.

        Raises:
            HumanAuthorityError: If called by an agent.
            TierMovementError: If item is not in workspace.
        """
        by, actor = resolve_actor(self._config.agent_usernames)
        if by == "agent":
            raise HumanAuthorityError("stealth requires human authority")

        full_id, store, t = self._resolve_id(item_id)
        if t != Tier.WORKSPACE:
            raise TierMovementError(
                f"Can only stealth from workspace, but {full_id} is in {t.value}"
            )

        src_path = store.item_path(full_id)
        dst_path = self._stealth.item_path(full_id)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        op_dict = {
            "op": "stealth",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
        }
        store._append_op(src_path, op_dict)

        shutil.move(str(src_path), str(dst_path))

        self._cache_delete_item(Tier.WORKSPACE, full_id)
        self._cache_upsert_item(Tier.STEALTH, self._stealth, full_id)

    def unstealth_item(self, item_id: str) -> None:
        """Move item from stealth → workspace. Human-authority only.

        Raises:
            HumanAuthorityError: If called by an agent.
            TierMovementError: If item is not in stealth.
        """
        by, actor = resolve_actor(self._config.agent_usernames)
        if by == "agent":
            raise HumanAuthorityError("unstealth requires human authority")

        full_id, store, t = self._resolve_id(item_id)
        if t != Tier.STEALTH:
            raise TierMovementError(
                f"Can only unstealth from stealth, but {full_id} is in {t.value}"
            )

        src_path = store.item_path(full_id)
        dst_path = self._workspace.item_path(full_id)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        op_dict = {
            "op": "unstealth",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
        }
        store._append_op(src_path, op_dict)

        shutil.move(str(src_path), str(dst_path))

        self._cache_delete_item(Tier.STEALTH, full_id)
        self._cache_upsert_item(Tier.WORKSPACE, self._workspace, full_id)

    # -------------------------------------------------------------------
    # Reconciliation
    # -------------------------------------------------------------------

    def reconcile(self) -> list[str]:
        """Auto-reconcile cross-tier duplicates.

        When the same item ID exists in multiple tiers (e.g., after a merge
        conflict), this method resolves the conflict:

        - If the item has promote/demote op history, auto-merge to the
          target tier (append reconcile op to winner, delete loser).
        - If no tier-movement ops exist, flag cross_tier_conflict on both.
        - After _MAX_RECONCILE_ATTEMPTS, stop and flag persistent error.

        Returns list of reconciled item IDs.
        """
        # Collect all item IDs per tier
        tier_items: dict[Tier, set[str]] = {}
        for t, store in self._tier_stores.items():
            tier_items[t] = set(store.item_ids())

        # Find duplicates (same ID in multiple tiers)
        all_ids: set[str] = set()
        for ids in tier_items.values():
            all_ids |= ids

        reconciled: list[str] = []

        for item_id in sorted(all_ids):
            present_in = [t for t in Tier if item_id in tier_items.get(t, set())]
            if len(present_in) <= 1:
                continue

            # Count existing reconcile ops to check attempt limit
            reconcile_count = 0
            for t in present_in:
                store = self._tier_stores[t]
                try:
                    ops = _parse_ops_file(store.item_path(item_id))
                    reconcile_count += sum(
                        1 for o in ops if o.get("op") == "reconcile"
                    )
                except Exception:  # noqa: S112  # nosec B112  # pragma: no cover — defensive
                    continue

            if reconcile_count >= _MAX_RECONCILE_ATTEMPTS:
                # Flag persistent conflict on all copies
                for t in present_in:
                    store = self._tier_stores[t]
                    try:
                        ops = _parse_ops_file(store.item_path(item_id))
                        item = compile_ops(ops, item_id)
                        item.cross_tier_conflict = True
                    except Exception:  # noqa: S112  # nosec B112  # pragma: no cover — defensive
                        continue
                continue

            # Check for tier-movement ops to determine winner
            winner_tier = self._determine_reconcile_winner(item_id, present_in)

            if winner_tier is not None:
                # Auto-reconcile: merge ops into winner, remove loser
                self._do_reconcile(item_id, present_in, winner_tier)
                reconciled.append(item_id)
            else:
                # No clear winner — flag conflict
                for t in present_in:
                    store = self._tier_stores[t]
                    by, actor = resolve_actor(self._config.agent_usernames)
                    now = datetime.datetime.now(
                        datetime.timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    op_dict = {
                        "op": "reconcile",
                        "at": now,
                        "by": by,
                        "actor": actor,
                        "clock": 0,
                        "nonce": _make_nonce(),
                        "from_tier": t.value,
                        "reason": f"cross-tier conflict flagged: {item_id} in "
                        + ", ".join(tt.value for tt in present_in),
                    }
                    store._append_op(store.item_path(item_id), op_dict)
                reconciled.append(item_id)

        return reconciled

    def reconcile_reset(self, item_id: str) -> None:
        """Human-authority manual reset for persistent conflicts.

        Merges all ops into the canonical tier and removes other copies.

        Raises:
            HumanAuthorityError: If called by an agent.
            ItemNotFoundError: If item not found.
        """
        by, actor = resolve_actor(self._config.agent_usernames)
        if by == "agent":
            raise HumanAuthorityError("reconcile_reset requires human authority")

        # Find all tiers containing this item
        present_in: list[Tier] = []
        for t, store in self._tier_stores.items():
            if store.item_path(item_id).exists():
                present_in.append(t)

        if not present_in:
            raise ItemNotFoundError(f"Item not found: {item_id}")

        # Merge into canonical
        self._do_reconcile(item_id, present_in, Tier.CANONICAL, reason="manual-reset")

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _find_item_tier(self, item_id: str) -> tuple[Store, Tier]:
        """Locate which tier an item lives in.

        Raises:
            ItemNotFoundError: If item not found in any tier.
        """
        for t, store in self._tier_stores.items():
            if store.item_path(item_id).exists():
                return store, t
        raise ItemNotFoundError(f"Item not found in any tier: {item_id}")

    def _resolve_id(self, prefix: str) -> tuple[str, Store, Tier]:
        """Cross-tier prefix resolution with positional alias support.

        Returns (full_id, store, tier). Handles :N positional aliases
        at the TrackerSet level before delegating to per-store resolution.

        Raises:
            AmbiguousPrefixError: If prefix matches items in multiple tiers.
            ItemNotFoundError: If no items match or positional alias out of range.
        """
        # Positional alias: intercept at TrackerSet level (merged list)
        if prefix.startswith(":"):
            try:
                idx = int(prefix[1:]) - 1
            except ValueError:
                raise ItemNotFoundError(f"Invalid positional alias: {prefix}") from None
            if 0 <= idx < len(self._last_list):
                full_id = self._last_list[idx]
                # Recurse with actual ID to find store/tier
                return self._resolve_id(full_id)
            raise ItemNotFoundError(
                f"Positional alias {prefix} out of range "
                f"(last list had {len(self._last_list)} items)"
            )

        candidates: list[tuple[str, Store, Tier]] = []

        for t, store in self._tier_stores.items():
            try:
                full_id = store._resolve_id(prefix)
                candidates.append((full_id, store, t))
            except ItemNotFoundError:
                continue
            except AmbiguousPrefixError:
                raise

        if len(candidates) == 0:
            raise ItemNotFoundError(f"No items match prefix: {prefix}")
        elif len(candidates) == 1:
            return candidates[0]
        else:
            # Same ID in multiple tiers — check if it's the same full ID
            unique_ids = {c[0] for c in candidates}
            if len(unique_ids) == 1:
                # Same item in multiple tiers — return first (canonical > workspace > stealth)
                for t in [Tier.CANONICAL, Tier.WORKSPACE, Tier.STEALTH]:
                    for c in candidates:
                        if c[2] == t:
                            return c
            # Different IDs across tiers
            cand_list = [
                (c[0], f"[{c[2].value}]") for c in candidates
            ]
            raise AmbiguousPrefixError(prefix, cand_list)

    def _determine_reconcile_winner(
        self, item_id: str, tiers: list[Tier]
    ) -> Tier | None:
        """Determine which tier should win reconciliation.

        If the item has promote ops, canonical wins. If it has demote ops,
        workspace wins. If no tier-movement ops, returns None (no clear winner).
        """
        has_promote = False
        has_demote = False

        for t in tiers:
            store = self._tier_stores[t]
            try:
                ops = _parse_ops_file(store.item_path(item_id))
                for op in ops:
                    if op.get("op") == "promote":
                        has_promote = True
                    elif op.get("op") == "demote":
                        has_demote = True
            except Exception:  # noqa: S112  # nosec B112  # pragma: no cover — defensive
                continue

        if has_promote:
            return Tier.CANONICAL
        if has_demote:
            return Tier.WORKSPACE
        return None

    def _do_reconcile(
        self, item_id: str, present_in: list[Tier], winner_tier: Tier,
        *, reason: str | None = None,
    ) -> None:
        """Merge all ops into the winner tier and remove other copies.

        Reads ops from all tiers, merges them, appends a reconcile op,
        and writes the merged ops to the winner tier. Removes loser files.

        Args:
            reason: Custom reason for the reconcile op. If None, uses the
                default "auto-reconciled to {winner_tier}" message.
        """
        winner_store = self._tier_stores[winner_tier]
        winner_path = winner_store.item_path(item_id)

        # Collect all ops from all tiers
        all_ops: list[dict[str, Any]] = []
        for t in present_in:
            store = self._tier_stores[t]
            try:
                ops = _parse_ops_file(store.item_path(item_id))
                all_ops.extend(ops)
            except Exception:  # noqa: S112  # nosec B112  # pragma: no cover — defensive
                continue

        # Deduplicate ops by nonce
        seen_nonces: set[str] = set()
        unique_ops: list[dict[str, Any]] = []
        for op in all_ops:
            nonce = op.get("nonce", "")
            if nonce and nonce in seen_nonces:
                continue
            if nonce:
                seen_nonces.add(nonce)
            unique_ops.append(op)

        # Append reconcile op
        by, actor = resolve_actor(self._config.agent_usernames)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        reconcile_op: dict[str, Any] = {
            "op": "reconcile",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": max((o.get("clock", 0) for o in unique_ops), default=0) + 1,
            "nonce": _make_nonce(),
            "from_tier": ",".join(t.value for t in present_in if t != winner_tier),
            "reason": reason or f"auto-reconciled to {winner_tier.value}",
        }
        unique_ops.append(reconcile_op)

        # Write merged ops to winner
        serialized_parts: list[str] = []
        for op in unique_ops:
            serialized_parts.append(_serialize_op(op))
        merged_content = "".join(serialized_parts)

        winner_path.parent.mkdir(parents=True, exist_ok=True)
        winner_path.write_text(merged_content)

        # Remove loser files
        for t in present_in:
            if t != winner_tier:
                store = self._tier_stores[t]
                loser_path = store.item_path(item_id)
                if loser_path.exists():
                    loser_path.unlink()

        # Update caches
        for t in present_in:
            if t != winner_tier:
                self._cache_delete_item(t, item_id)
        self._cache_upsert_item(winner_tier, winner_store, item_id)

    # -------------------------------------------------------------------
    # Positional alias persistence
    # -------------------------------------------------------------------

    def _save_last_list(self) -> None:
        """Persist the positional alias stash to cache_dir/last_list."""
        if self._cache_dir is None:
            return
        path = self._cache_dir / "last_list"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(self._last_list) + "\n" if self._last_list else ""
        )

    def _load_last_list(self) -> None:
        """Load the positional alias stash from cache_dir/last_list."""
        if self._cache_dir is None:
            return
        path = self._cache_dir / "last_list"
        if path.exists():
            content = path.read_text().strip()
            self._last_list = content.split("\n") if content else []

    # -------------------------------------------------------------------
    # Cache integration helpers
    # -------------------------------------------------------------------

    def _cache_upsert_item(self, tier: Tier, store: Store, item_id: str) -> None:
        """Update cache for an item after a mutation. No-op if no cache."""
        if self._caches is None:
            return
        cache = self._caches.get(tier)
        if cache is None:
            return
        path = store.item_path(item_id)
        if path.exists():
            stat = path.stat()
            ops = _parse_ops_file(path)
            item = compile_ops(ops, item_id)
            item.tier = tier
            cache.upsert(item_id, item, stat.st_mtime, stat.st_size)

    def _cache_delete_item(self, tier: Tier, item_id: str) -> None:
        """Remove an item from a tier's cache. No-op if no cache."""
        if self._caches is None:
            return
        cache = self._caches.get(tier)
        if cache is None:
            return
        cache.delete(item_id)
