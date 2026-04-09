#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Incremental transcript filter for the per-session sync pipeline.

Reads new lines from a JSONL transcript (from a saved byte offset),
filters out redundant noise, appends meaningful lines to the
destination file, and emits normalized interjection events alongside
vendor-specific originals (WI-nadud).

Per-session isolation (ADR-0018 amendment): the state file path encodes
the session id, so each concurrent session has its own offset state.
There is no cross-session validation needed because the state file path
is unique per session — if a state file exists, it belongs to that
session by construction. The defensive "file shrank" reset path remains
for filesystem oddities (truncation/replacement edge cases that should
not occur under normal Claude Code operation since transcripts are
append-only across compaction).

Filter rules:
  1. Drop bash_progress lines with empty output
  2. Drop bash_progress lines with output identical to the previous one
  3. Drop file-history-snapshot lines (internal bookkeeping)
  4. Keep the LAST bash_progress before a non-progress line (captures
     final command output even when intermediate snapshots are dropped)
  5. Keep everything else as-is

After filtering, a normalization pass (normalize_interjections module)
detects vendor-specific interjection patterns and emits synthetic
``normalized_user_interjection`` rows alongside the originals. Vendor
is auto-detected from row content and cached in the state file.

Usage: filter-transcript.py <source> <dest> <state-file>
"""

import hashlib
import importlib.util
import json
import os
import sys


def _import_normalize():
    """Import normalize_interjections from the same directory."""
    norm_path = os.path.join(os.path.dirname(__file__), "normalize_interjections.py")
    spec = importlib.util.spec_from_file_location("normalize_interjections", norm_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_state(state_path):
    """Load filter state (byte offset, last output hash).

    Returns empty state if the state file is missing or corrupt. Under
    per-session isolation the state file path is unique per session, so
    no cross-session validation is needed.
    """
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"offset": 0, "last_bash_hash": ""}
    return {"offset": 0, "last_bash_hash": ""}


def save_state(state_path, state):
    """Persist filter state atomically."""
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)


def hash_output(output):
    """Fast hash of bash output for dedup comparison."""
    return hashlib.md5(output.encode("utf-8", errors="replace")).hexdigest()


def should_keep(obj, last_bash_hash):
    """Decide whether to keep a line. Returns (keep, new_hash, is_bash_progress)."""
    line_type = obj.get("type")

    # Rule 3: drop file-history-snapshot
    if line_type == "file-history-snapshot":
        return False, last_bash_hash, False

    # Rules 1-2: filter bash_progress
    if line_type == "progress":
        data = obj.get("data", {})
        if isinstance(data, dict) and data.get("type") == "bash_progress":
            output = data.get("output", "")

            # Rule 1: drop empty output
            if not output:
                return False, last_bash_hash, True

            # Rule 2: drop if identical to previous
            h = hash_output(output)
            if h == last_bash_hash:
                return False, h, True

            return True, h, True

    # Everything else: keep
    return True, last_bash_hash, False


def filter_new_lines(src_path, dest_path, state):
    """Read new lines from source, filter, append to dest. Returns updated state."""
    offset = state["offset"]
    last_bash_hash = state["last_bash_hash"]

    src_size = os.path.getsize(src_path)
    if src_size <= offset:
        # File hasn't grown (or was truncated/replaced — reset)
        if src_size < offset:
            offset = 0
            last_bash_hash = ""
        else:
            return state

    kept = []
    pending_bash = None  # Rule 4: buffer last bash_progress line

    with open(src_path, "rb") as f:
        f.seek(offset)
        raw = f.read()
        new_offset = f.tell()

    for raw_line in raw.split(b"\n"):
        if not raw_line.strip():
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        keep, last_bash_hash, is_bash = should_keep(obj, last_bash_hash)

        if is_bash:
            if keep:
                # Buffer this bash_progress (might be superseded by next one)
                pending_bash = raw_line
            # If not kept, pending_bash stays as-is (previous kept one)
        else:
            # Non-bash line: flush any pending bash_progress first (Rule 4)
            if pending_bash is not None:
                kept.append(pending_bash)
                pending_bash = None
            if keep:
                kept.append(raw_line)

    # Don't flush pending_bash at EOF — wait for next non-bash line
    # (avoids writing intermediate state that will be superseded)

    # --- Normalization pass (WI-nadud) ---
    # Parse kept rows, run through normalize_rows, re-serialize.
    norm_vendor = state.get("norm_vendor")
    norm_state = state.get("norm_state", {})
    output_lines: list[bytes] = []

    if kept:
        parsed_kept = []
        raw_kept = []  # parallel list of original raw bytes
        for raw_line in kept:
            try:
                parsed_kept.append(json.loads(raw_line))
                raw_kept.append(raw_line)
            except json.JSONDecodeError:
                output_lines.append(raw_line)  # keep unparseable lines as-is

        if parsed_kept:
            try:
                norm_mod = _import_normalize()
                normalized, norm_state = norm_mod.normalize_rows(
                    parsed_kept, vendor=norm_vendor, state=norm_state,
                )
                # Auto-detect and cache vendor for future batches
                if norm_vendor is None:
                    norm_vendor = norm_mod.detect_vendor(parsed_kept)

                # Build output: for each row in normalized result, either
                # use the original raw bytes (if it's an original row) or
                # serialize the new synthetic row.
                orig_idx = 0
                for row in normalized:
                    if (
                        row.get("type") == "normalized_user_interjection"
                    ):
                        # Synthetic row — serialize fresh
                        output_lines.append(
                            json.dumps(row, separators=(",", ":")).encode("utf-8")
                        )
                    else:
                        # Original row — use raw bytes to avoid re-serialization drift
                        if orig_idx < len(raw_kept):
                            output_lines.append(raw_kept[orig_idx])
                            orig_idx += 1
                        else:
                            output_lines.append(
                                json.dumps(row, separators=(",", ":")).encode("utf-8")
                            )
            except Exception:  # pragma: no cover — normalization is best-effort
                # If normalization fails, fall back to original kept rows
                output_lines = list(kept)
        else:
            output_lines = list(kept)
    # --- End normalization pass ---

    if output_lines:
        # Ensure the destination directory exists (per-session DESTs may
        # be the first file written to .agent/ in a fresh repo).
        dest_dir = os.path.dirname(dest_path)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        with open(dest_path, "ab") as f:
            for line in output_lines:
                f.write(line)
                f.write(b"\n")

    return {
        "offset": new_offset,
        "last_bash_hash": last_bash_hash,
        "norm_vendor": norm_vendor,
        "norm_state": norm_state,
    }


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <source> <dest> <state-file>", file=sys.stderr)
        sys.exit(1)

    src_path, dest_path, state_path = sys.argv[1], sys.argv[2], sys.argv[3]

    if not os.path.exists(src_path):
        sys.exit(0)

    state = load_state(state_path)
    state = filter_new_lines(src_path, dest_path, state)
    save_state(state_path, state)


if __name__ == "__main__":
    main()
