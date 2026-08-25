# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary that auto-derives a taint label must mean what the label means
(INV-tutar).

WHAT WAS WRONG. ``AUTO_SOURCE_LABEL_MAP`` derived ``host_secret`` from the
``env_read`` boundary, and ``env_read`` catalogued two different things:
ambient CONFIGURATION reads, whose values may carry a credential
(``os.getenv``, ``System.getProperty``, ``std::env::var``), and host
DESCRIPTION reads, which are not secrets in any ordinary sense
(``runtime.GOOS``, ``os.uname``, ``navigator.platform``, ``platform.system``,
``pwd.getpwnam``). Census over the shipped catalogues: **134 of 195 rows** were
the second kind. Every ``host-secret-*`` claim therefore counted description
reads as secret flows -- 48 of 85 adjudicated flows at 22.9% precision, the
weakest family in measurement 0001, and 51 of 59 situations in 0004.

THE CATALOGUE WAS ALREADY DISTORTING ITSELF TO COPE, which is the strongest
evidence the vocabulary was wrong rather than merely imprecise.
``io_primitives/python.yaml`` deliberately withheld ``getpid`` / ``cpu_count``
/ ``times`` -- not because they are not environment reads, but because
"env_read rows auto-derive host_secret TAINT SOURCES ... and a pid is not a
secret". ``go.yaml`` meanwhile rowed ``GOOS``, ``Getwd`` and ``Executable``.
One boundary value, two membership rules, two shipped files: the apex/peer hit
in the fundamental-concept audit, which alone is grounds to deprecate.

WHY THE BOUNDARY SPLIT AND NOT A LABEL RENAME OR A PER-ROW OVERRIDE.
A rename keeps one boundary meaning both things and breaks three shipped
example claims for nothing. A per-row ``taint_label`` would let the row AND the
boundary each decide the label -- one fact, two homes. The boundary vocabulary
(``CATALOG_BOUNDARY_TYPES``) is the registry-backed thing, so it is where the
distinction belongs. Owner-ratified 2026-08-25; full audit at
``~/hypergumbo_lab_notebook/campaign_p0_08252026/P2_env_read_concept_audit.md``.
"""

from collections import Counter
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.io_boundary import (
    CATALOG_BOUNDARY_TYPES,
    KNOWN_IO_BOUNDARIES,
    load_catalog,
)
from hypergumbo_core.taint import (
    AUTO_SOURCE_LABEL_MAP,
    _derive_auto_imports_from_io_primitives,
)

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


class TestTheTwoVocabulariesArePinnedToEachOther:
    """The contract that keeps this from happening again: a boundary cannot be
    added without deciding what, if anything, it derives."""

    def test_every_auto_labelled_boundary_is_a_real_boundary(self) -> None:
        unknown = set(AUTO_SOURCE_LABEL_MAP) - set(CATALOG_BOUNDARY_TYPES)
        assert not unknown, (
            f"AUTO_SOURCE_LABEL_MAP derives a label from {sorted(unknown)}, "
            f"which no catalogue may declare -- so the label can never be "
            f"produced and a claim naming it matches nothing (INV-todas shape)."
        )

    def test_every_boundary_a_catalogue_uses_is_a_declared_boundary(self) -> None:
        """Catches a typo'd or invented boundary in a shipped YAML, which would
        otherwise be silently inert: no auto-source, no auto-sink, no display
        grouping, no error."""
        used = {
            p.boundary
            for path in sorted(_CATALOG_DIR.glob("*.yaml"))
            for p in load_catalog(path.stem).primitives
        }
        assert not (used - KNOWN_IO_BOUNDARIES), sorted(used - KNOWN_IO_BOUNDARIES)

    def test_the_two_read_boundaries_derive_DIFFERENT_labels(self) -> None:
        """The whole point. If these ever collapse to one label the split is
        cosmetic and the precision loss returns."""
        assert AUTO_SOURCE_LABEL_MAP["env_read"] == "host_secret"
        assert AUTO_SOURCE_LABEL_MAP["host_info_read"] == "host_description"


#: Rows named in INV-tutar and in measurements 0001 / 0004 as the ones calling
#: a description read a secret. Each is asserted at the DERIVED-SOURCE layer,
#: not by reading the YAML, because the YAML is not what the taint arm consumes.
_MUST_BE_DESCRIPTION = [
    ("go", "runtime", "GOOS"),
    ("go", "os", "Getwd"),
    ("go", "os", "Executable"),
    ("go", "os", "Hostname"),
    ("python", "platform", "system"),
    ("python", "os", "uname"),
    ("python", "shutil", "get_terminal_size"),
    ("python", "pwd", "getpwnam"),
    ("javascript", "navigator", "platform"),
    ("javascript", "os", "hostname"),
    ("java", "java.lang.Runtime", "availableProcessors"),
]

#: The other arm. A one-arm test of the wrong arm generalises to a falsehood
#: (LIVE.md rule 12), and "everything is host_description now" would pass every
#: assertion above.
_MUST_STAY_SECRET = [
    ("go", "os", "Getenv"),
    ("go", "os", "Environ"),
    ("python", "os", "getenv"),
    ("python", "sys", "argv"),
    ("java", "java.lang.System", "getenv"),
    ("java", "java.lang.System", "getProperty"),
    ("rust", "std::env", "var"),
    ("javascript", "process", "env"),
    ("scala", "scala.sys", "env"),
]


def _label_of(lang: str, module: str, name: str) -> str | None:
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    for src in sources.get(lang, ()):
        if src.module == module and src.name == name:
            return src.taint_label
    return None


@pytest.mark.parametrize("lang,module,name", _MUST_BE_DESCRIPTION)
def test_a_description_read_derives_host_description(
    lang: str, module: str, name: str,
) -> None:
    assert _label_of(lang, module, name) == "host_description"


@pytest.mark.parametrize("lang,module,name", _MUST_STAY_SECRET)
def test_a_configuration_read_still_derives_host_secret(
    lang: str, module: str, name: str,
) -> None:
    assert _label_of(lang, module, name) == "host_secret"


class TestThePopulationMoved:
    """Population-level, so the parametrised rows above cannot be satisfied by
    a handful of edits while the bulk of the catalogue stays mislabelled."""

    def test_most_of_the_old_population_is_no_longer_called_a_secret(
        self,
    ) -> None:
        sources, _s, _a = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
        counts: Counter[str] = Counter()
        for langs in sources.values():
            counts.update(s.taint_label for s in langs)
        assert counts["host_description"] > counts["host_secret"], counts
        assert counts["host_secret"] > 0, (
            "a split that emptied env_read would 'fix' precision by deleting "
            "the family, which is not the same thing"
        )

    def test_a_csprng_read_is_not_an_environment_read_at_all(self) -> None:
        """``os.getrandom`` was filed under ``env_read`` and derived a
        ``host_secret`` source. It is neither, and the same file already keeps
        ``os.urandom`` out for its own reason. Removed by the split rather than
        moved."""
        assert _label_of("python", "os", "getrandom") is None

    def test_the_browser_credential_rows_stayed_put(self) -> None:
        """``document.cookie`` is genuinely credential material, so it keeps
        ``host_secret`` even though its two row-mates
        (``document.location`` / ``document.referrer``) are arguably
        attacker-influenceable input. That is a distinct defect and is filed
        separately rather than folded in here."""
        assert _label_of("javascript", "document", "cookie") == "host_secret"
