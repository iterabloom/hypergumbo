# SPDX-License-Identifier: AGPL-3.0-or-later
import fnmatch
import os
import pwd


def resolve_actor(agent_patterns=None):
    if agent_patterns is None:
        agent_patterns = ["*_agent"]

    uid = os.getuid()
    pw_entry = pwd.getpwuid(uid)
    username = pw_entry.pw_name

    for pattern in agent_patterns:
        if fnmatch.fnmatch(username, pattern):
            return ("agent", username)
    return ("human", username)
