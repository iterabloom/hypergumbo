#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# WI-luzuh fixture: Bash source-language constructs.
# Triggers: function, alias, export.

alias ll='ls -la'

export MY_VAR="hello"
export PATH="/usr/local/bin:$PATH"

greet() {
    echo "Hello, $1!"
}

deploy() {
    local target="$1"
    greet "$target"
}

deploy "world"
