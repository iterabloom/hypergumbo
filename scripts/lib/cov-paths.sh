#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# ------------------------------------------------------------------
# cov-paths.sh: Canonical `--cov=` args for authoritative full-suite runs.
#
# Sourced by release-check (and intended to be sourced by any future
# script that needs to measure coverage across every released package).
# Scheduled CI workflows (full-suite.yml, nightly.yml, release.yml) already
# list per-package `--cov=` args inline, per-job; this fragment exists so
# that non-CI full-suite runners (notably scripts/release-check) and any
# future equivalent share one source of truth instead of drifting.
#
# Deliberately NOT sourced by scripts/smart-test. smart-test is the
# dev-loop tool and maintains its own COV_PATHS that reflects a dev-loop
# policy choice (e.g., excluding hypergumbo-tracker, which has its own CI
# and rarely intersects with dev-loop changes outside tracker itself).
# Converging the two lists is possible in a later change if the dev-loop
# choice is revisited; for now the dev-loop and the gate keep separate
# coverage policies intentionally.
#
# Usage:
#   source "$(dirname "$0")/lib/cov-paths.sh"
#   python -m pytest packages/*/tests/ "${COV_PATHS_ALL[@]}" ...
#
# When a new package with a src/ tree is added, append its --cov= entry
# below. That single edit reaches every authoritative full-suite runner
# that sources this file.
# ------------------------------------------------------------------

# Every package under packages/ that ships a src/ tree. Order is
# alphabetical for easy diff review; pytest-cov does not care about order.
COV_PATHS_ALL=(
    "--cov=packages/hypergumbo-core/src"
    "--cov=packages/hypergumbo-lang-common/src"
    "--cov=packages/hypergumbo-lang-extended1/src"
    "--cov=packages/hypergumbo-lang-mainstream/src"
    "--cov=packages/hypergumbo-lang-rust-analyzer/src"
    "--cov=packages/hypergumbo-tracker/src"
)
