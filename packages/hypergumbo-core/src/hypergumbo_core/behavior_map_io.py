# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deprecation shim: ``behavior_map_io`` was renamed to ``survey_io`` (ADR-0042).

The survey-substrate loader now lives in :mod:`hypergumbo_core.survey_io`. This
module re-exports its public API from the new home and warns on import, purely
for the one-minor-version deprecation window mandated by ADR-0042's shim-first
sequencing rule (an in-flight bakeoff on the editable install must keep
resolving ``from hypergumbo_core.behavior_map_io import ...`` while call-sites
migrate). Import from ``hypergumbo_core.survey_io`` instead; this module is
removed at window-close.
"""
from __future__ import annotations

import warnings

from .survey_io import (
    CANONICAL_SURVEY_FILENAME,
    LEGACY_SURVEY_FILENAMES,
    SURVEY_FILENAMES,
    SubstrateError,
    find_behavior_map,
    find_survey_in_dir,
    load_behavior_map,
    load_substrate,
)

warnings.warn(
    "hypergumbo_core.behavior_map_io is deprecated (ADR-0042) and will be "
    "removed at the deprecation-window close; import from "
    "hypergumbo_core.survey_io instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CANONICAL_SURVEY_FILENAME",
    "LEGACY_SURVEY_FILENAMES",
    "SURVEY_FILENAMES",
    "SubstrateError",
    "find_behavior_map",
    "find_survey_in_dir",
    "load_behavior_map",
    "load_substrate",
]
