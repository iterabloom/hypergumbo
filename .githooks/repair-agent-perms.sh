#!/bin/bash
# Repair .agent/ file/directory permissions for two-user setup.
#
# Belt-and-suspenders complement to the runtime repair in store.py
# (_ensure_dir_group_writable, _take_ownership_via_tmp) and the setup
# wizard (_check_group_permissions).  Git operations (checkout, merge)
# restore files with 0644/0755 permissions, stripping group-write bits
# that the two-user setup requires.  This script proactively re-applies
# g+rw on files and g+rwxs on directories owned by the current user.
#
# Called from post-checkout and post-merge hooks.  Designed to be
# silent and best-effort: errors are swallowed so git operations are
# never blocked.

repair_agent_perms() {
    local repo_root="$1"
    local agent_dir="$repo_root/.agent"
    [[ -d "$agent_dir" ]] || return 0

    local my_uid my_primary_gid
    my_uid=$(id -u)
    my_primary_gid=$(id -g)

    # Detect two-user setup: check if any tracker directory uses a
    # non-primary group (meaning chgrp was run for sharing).
    local shared=false
    local d
    for d in \
        "$agent_dir/tracker" \
        "$agent_dir/tracker/.ops" \
        "$agent_dir/tracker-workspace" \
        "$agent_dir/tracker-workspace/.ops" \
        "$agent_dir/tracker-workspace/stealth"; do
        [[ -d "$d" ]] || continue
        local dir_gid
        dir_gid=$(stat -c '%g' "$d" 2>/dev/null) || continue
        if [[ "$dir_gid" != "$my_primary_gid" ]]; then
            shared=true
            break
        fi
    done

    $shared || return 0

    # Fix directories: ensure group-write + setgid (only dirs we own).
    find "$agent_dir" -type d -user "$my_uid" \
        \( ! -perm -g+w -o ! -perm -g+s \) \
        -exec chmod g+rwxs {} + 2>/dev/null || true

    # Fix files: ensure group read+write (only files we own).
    find "$agent_dir" -type f -user "$my_uid" ! -perm -g+rw \
        -exec chmod g+rw {} + 2>/dev/null || true
}
