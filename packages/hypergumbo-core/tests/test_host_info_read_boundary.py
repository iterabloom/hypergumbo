# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary that auto-derives a taint label must mean what the label means
(INV-tutar).

THE SPLIT. ``AUTO_SOURCE_LABEL_MAP`` derives one taint label per boundary, so
one boundary must not catalogue two kinds of read. Ambient CONFIGURATION reads,
whose values may carry a credential (``os.getenv``, ``System.getProperty``,
``std::env::var``), are ``env_read`` and derive ``host_secret``. Host
DESCRIPTION reads, which are not secrets in any ordinary sense
(``runtime.GOOS``, ``os.uname``, ``navigator.platform``, ``platform.system``,
``pwd.getpwnam``), are ``host_info_read`` and derive ``host_description``.
Description reads are the majority of the shipped rows, so filing them under
``env_read`` makes every ``host-secret-*`` claim count them as secret flows, at
very low precision (measurements 0001 / 0004).

A SHARED BOUNDARY DISTORTS THE CATALOGUE, which is the evidence that the
vocabulary is wrong rather than merely imprecise: a catalogue protecting the
derived label withholds description reads it would otherwise row ("a pid is not
a secret"), while another catalogue rows the same kind of read anyway. One
boundary value, two membership rules: the apex/peer hit in the
fundamental-concept audit, which alone is grounds to deprecate.

WHY THE BOUNDARY SPLIT AND NOT A LABEL RENAME OR A PER-ROW OVERRIDE.
A rename keeps one boundary meaning both things and breaks shipped example
claims for nothing. A per-row ``taint_label`` would let the row AND the
boundary each decide the label -- one fact, two homes. The boundary vocabulary
(``CATALOG_BOUNDARY_TYPES``) is the registry-backed thing, so it is where the
distinction belongs. (Owner-ratified; audit:
``~/hypergumbo_lab_notebook/campaign_p0_08252026/P2_env_read_concept_audit.md``.)
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
    """The contract: a boundary cannot be added without deciding what, if
    anything, it derives."""

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


#: Description reads a shared boundary would call secrets (INV-tutar;
#: measurements 0001 / 0004). Each is asserted at the DERIVED-SOURCE layer,
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

#: The other arm. A one-arm test of the wrong arm generalises to a falsehood,
#: and "everything is host_description" would pass every assertion above.
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
        """``os.getrandom`` is neither an environment read nor a ``host_secret``
        source, so it has no row at all; the same file keeps ``os.urandom`` out
        for its own reason."""
        assert _label_of("python", "os", "getrandom") is None

    def test_the_browser_credential_rows_stayed_put(self) -> None:
        """``document.cookie`` is genuinely credential material, so it keeps
        ``host_secret``. The page URL and referrer keep it too and are ALSO
        attacker input, through ``navigation_read``
        (``test_navigation_read_boundary.py``)."""
        assert _label_of("javascript", "document", "cookie") == "host_secret"
