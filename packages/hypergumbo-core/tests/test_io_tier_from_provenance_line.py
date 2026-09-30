# SPDX-License-Identifier: AGPL-3.0-or-later
"""An I/O overlay's tier comes from its ``provenance:`` line, not its directory (INV-lamap).

ADR-0061 ruling 3: "The loader reads the tier from that line, never from the
directory. A community file copied into your config home stays community.
Deleting the ``provenance: community`` line is how you vouch for a file."

THE DEFECT. ``load_catalog`` stamped a row ``unvouched`` only when its file sat
in the shipped ``io_primitives_overlays/`` directory. ``init-catalogs`` seeds
copies of those files into ``$XDG_CONFIG_HOME/hypergumbo/io_primitives.d/``;
the copies keep ``provenance: community``, and the loader ignored it, so the
same rows became the user's own and licensed clean verdicts the shipped
originals cannot. Measured on dev 4a89b020ff, a repo calling
``requests.get`` under a ``fs_write must_not_exist`` claim: inconclusive rc 2
with an empty config home, confirmed rc 0 after ``init-catalogs``.

WHAT A COMMUNITY FILE MAY DO, WHEREVER IT LIVES (ruling 2): its rows ADD a
detection but do not count as examined, its ``module_completeness`` grants
are withheld, and it does not restate the catalogue's stdlib status. A file
with no provenance line in the user's home is the user's, and takes full
effect. The end-to-end verdicts live in the mainstream package's
``test_seeded_community_overlay_stays_community.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import _warn_community_overlays
from hypergumbo_core.io_boundary import load_catalog, overlay_declares_community

_SHIPPED_HTTP = Path(iob.__file__).parent / "io_primitives_overlays" / \
    "python-http-clients.yaml"

_OVERLAY = """\
language: python
status: overlay
{provenance}retrieved: 2026-01-01
net_send:
  - module: vendorlib
    functions: [post]
module_completeness:
  - module: vendorlib
    completeness: complete
    retrieved: "2026-01-01"
"""


def _overlay(tmp_path: Path, name: str, *, community: bool) -> Path:
    path = tmp_path / name
    path.write_text(_OVERLAY.format(
        provenance="provenance: community\n" if community else "",
    ))
    return path


def _rows(catalog: iob.IoBoundaryCatalog, module: str) -> list[iob.IoPrimitive]:
    return [p for p in catalog.primitives if p.module == module]


def test_the_line_is_what_is_read() -> None:
    assert overlay_declares_community(_SHIPPED_HTTP)


def test_a_community_file_outside_the_shipped_directory_stays_community(
    tmp_path: Path,
) -> None:
    """THE FILED DEFECT, at the loader: the same file, named as a user
    overlay, must not become the user's rows."""
    copy = tmp_path / _SHIPPED_HTTP.name
    copy.write_text(_SHIPPED_HTTP.read_text())
    catalog = load_catalog("python", overlay_paths=[copy], include_defaults=False)
    rows = _rows(catalog, "requests")
    assert rows, "reach: the copied overlay's requests rows must load"
    assert all(p.unvouched for p in rows)


def test_deleting_the_line_is_the_act_of_vouching(tmp_path: Path) -> None:
    """THE CONTROL: with the line gone the file is yours and its rows count."""
    copy = tmp_path / _SHIPPED_HTTP.name
    copy.write_text(_SHIPPED_HTTP.read_text().replace(
        "provenance: community\n", "",
    ))
    assert not overlay_declares_community(copy)
    catalog = load_catalog("python", overlay_paths=[copy], include_defaults=False)
    rows = _rows(catalog, "requests")
    assert rows and not any(p.unvouched for p in rows)


def test_a_community_files_completeness_grant_is_withheld(tmp_path: Path) -> None:
    """A grant is the removing half (ADR-0061 ruling 2): from a community file
    it does not make the module an examined negative."""
    community = _overlay(tmp_path, "c.yaml", community=True)
    catalog = load_catalog("python", overlay_paths=[community],
                           include_defaults=False)
    assert _rows(catalog, "vendorlib"), "reach: the adding half still loads"
    assert not catalog.module_io_is_enumerated("vendorlib")


def test_your_files_completeness_grant_still_counts(tmp_path: Path) -> None:
    yours = _overlay(tmp_path, "y.yaml", community=False)
    catalog = load_catalog("python", overlay_paths=[yours], include_defaults=False)
    assert catalog.module_io_is_enumerated("vendorlib")


def test_a_community_file_does_not_restate_the_catalogue_status(
    tmp_path: Path,
) -> None:
    """Its rows are not part of the stdlib enumeration, so python stays
    ``provenance_declared`` -- the same rule the shipped defaults follow --
    and the status it keeps is the one BEFORE this file, not a reset past a
    file of yours merged earlier."""
    base = load_catalog("python", include_defaults=False)
    community = _overlay(tmp_path, "c.yaml", community=True)
    catalog = load_catalog("python", overlay_paths=[community],
                           include_defaults=False)
    assert catalog.status == base.status
    assert catalog.stdlib_provenance == base.stdlib_provenance
    yours = _overlay(tmp_path, "y.yaml", community=False)
    after_yours = load_catalog("python", overlay_paths=[yours],
                               include_defaults=False)
    both = load_catalog("python", overlay_paths=[yours, community],
                        include_defaults=False)
    assert both.status == after_yours.status


class TestTheNotice:
    def test_a_community_overlay_you_load_is_named_with_its_withheld_grants(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        community = _overlay(tmp_path, "c.yaml", community=True)
        yours = _overlay(tmp_path, "y.yaml", community=False)
        named = _warn_community_overlays([yours, community])
        err = capsys.readouterr().err
        assert named == [community]
        assert str(community) in err and str(yours) not in err
        assert "retrieved 2026-01-01" in err
        assert "provenance: community" in err
        assert "vendorlib" in err

    def test_a_missing_file_is_left_to_the_real_load(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The loader reports a bad path with exit 2; the notice must not
        pre-empt that with a traceback of its own."""
        assert _warn_community_overlays([tmp_path / "does-not-exist.yaml"]) == []
        assert capsys.readouterr().err == ""

    def test_no_community_overlay_prints_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        yours = _overlay(tmp_path, "y.yaml", community=False)
        assert _warn_community_overlays([yours]) == []
        assert capsys.readouterr().err == ""

    def test_a_community_overlay_with_no_grant_names_no_withheld_modules(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        copy = tmp_path / _SHIPPED_HTTP.name
        copy.write_text(_SHIPPED_HTTP.read_text())
        assert _warn_community_overlays([copy]) == [copy]
        assert "withheld" not in capsys.readouterr().err
