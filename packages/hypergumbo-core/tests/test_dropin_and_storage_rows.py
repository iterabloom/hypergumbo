# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nibav: defusedcsv and django's default_storage are reached by their OWN rows.

TWO GAPS, ONE QUESTION -- which catalogue row is this call?

1. ``defusedcsv`` (pretix's CSV writer) is a drop-in for stdlib ``csv`` that
   escapes leading ``= + - @ | %`` against CSV injection. Its call
   ``csv.writer(f)`` after ``from defusedcsv import csv`` arrives as module
   ``defusedcsv.csv``, name ``writer`` (WI-sugom). No row named that module;
   the call was classified anyway, as STDLIB ``csv.writer``, because
   ``_module_matches('csv', 'defusedcsv.csv')`` is True by component suffix.
   The same rule says ``mycsvlib.csv`` is stdlib csv, so identity was decided
   by spelling, not by the catalogue. The suffix arm itself is NOT changed
   here: measured over 29 baseline surveys, its hint-longer direction decides
   78 tagged edges in 4 repos, 24 of them go true positives that exist only
   because go.yaml spells ``filepath`` for ``path/filepath`` (WI-mujod). So
   defusedcsv is identified by a ROW, which ``prefer_exact_owner`` picks over
   the suffix match, and ``mycsvlib.csv`` stays WI-mujod's. (WI-mujod has
   since refused that direction, so the row is now what classifies the call
   at all.)

   SURFACE READ, NOT ASSUMED (github.com/raphaelm/defusedcsv, 3.0.0):
   ``defusedcsv/__init__.py`` holds only ``version``; ``defusedcsv/csv.py``
   re-exports stdlib csv and defines its own ``writer`` (a proxy over stdlib
   ``csv.writer``) and ``DictWriter`` (a subclass of stdlib's). The rows
   mirror the stdlib csv rows exactly, module for module, and nothing else,
   because a drop-in that gained a row its stdlib twin lacks would make the
   two disagree about one write.

2. ``django.core.files.storage.default_storage`` is a filesystem abstraction
   (a ``LazyObject`` over ``storages['default']``, ``FileSystemStorage``
   unless the project configures another backend). Uncatalogued, so
   ``default_storage.save(name, content)`` was no boundary at all. Surface
   read from django's own source (``core/files/storage/base.py`` and
   ``filesystem.py``): ``save`` / ``delete`` write; ``open`` is dual like
   builtins.open (mode in the same seat, default ``'rb'``); ``exists``,
   ``listdir``, ``size``, the three ``get_*_time`` stats and the two
   name-availability probes (which call ``exists``) read. ``url`` /
   ``path`` / ``generate_filename`` / ``get_valid_name`` /
   ``get_alternative_name`` are string work and carry NO row --
   ``test_url_is_not_a_boundary`` pins that.

Both live in the COMMUNITY overlay ``python-web-and-orm.yaml`` (ADR-0047,
ADR-0061): third-party rows hypergumbo ships but does not vouch for.

Every assertion runs on REAL analyzer output for the source shape it names,
through the shipped CLI path's own functions, after asserting the call was
reached at all (LIVE rule 6).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import (
    _build_callee_index,
    _match_propagation_entry,
    load_builtin_taint_catalog,
)

_DEFUSED_FROM = (
    "import sys\n"
    "from defusedcsv import csv\n"
    "\n"
    "\n"
    "def export(rows):\n"
    "    w = csv.writer(sys.stdout)\n"
    "    w.writerows(rows)\n"
)

_DEFUSED_IMPORT = (
    "import sys\n"
    "import defusedcsv.csv\n"
    "\n"
    "\n"
    "def export(rows):\n"
    "    defusedcsv.csv.writer(sys.stdout).writerows(rows)\n"
)

_STORAGE = (
    "from django.core.files.storage import default_storage\n"
    "\n"
    "\n"
    "def keep(name, content):\n"
    "    default_storage.save(name, content)\n"
    "\n"
    "\n"
    "def drop(name):\n"
    "    default_storage.delete(name)\n"
    "\n"
    "\n"
    "def overwrite(name):\n"
    "    return default_storage.open(name, 'w')\n"
    "\n"
    "\n"
    "def read(name):\n"
    "    return default_storage.open(name)\n"
    "\n"
    "\n"
    "def probe(name):\n"
    "    return default_storage.exists(name)\n"
    "\n"
    "\n"
    "def link(name):\n"
    "    return default_storage.url(name)\n"
)


#: Each stdlib csv module that carries a row, and the defusedcsv module that
#: mirrors it. defusedcsv.csv re-exports stdlib csv, defines its own ``writer``
#: (returning a ``_ProxyWriter`` where stdlib returns a ``_csv.Writer``) and
#: subclasses ``DictWriter``.
_CSV_MIRROR = {
    "_csv.Writer": "defusedcsv.csv._ProxyWriter",
    "csv.DictWriter": "defusedcsv.csv.DictWriter",
}


def _in_stdlib_csv_family(module: str) -> bool:
    return module.split(".", 1)[0] in ("csv", "_csv")


def _tagged(tmp_path: Path, source: str, *, defaults: bool = True) -> dict:
    """``{(caller, callee name): (boundary, primitive)}`` for one file."""
    from hypergumbo_lang_mainstream.py import analyze_python

    (tmp_path / "mod.py").write_text(source)
    raw = [e.to_dict() for e in analyze_python(tmp_path).edges]
    edges = _rehydrate_io_boundary_edges(raw)
    tag_io_boundaries(
        edges, {"python": load_catalog("python", include_defaults=defaults)},
    )
    out = {}
    for e in edges:
        if e.edge_type not in ("calls", "instantiates"):
            continue
        caller = e.src.split(":")[-2]
        callee = e.dst.split(":")[-2]
        meta = e.meta or {}
        out[(caller, callee)] = (meta.get("io_boundary"), meta.get("io_primitive"))
    return out


class TestDefusedcsvHasItsOwnRow:

    @pytest.mark.parametrize("source", [_DEFUSED_FROM, _DEFUSED_IMPORT],
                             ids=["from-import", "dotted-import"])
    def test_the_write_classifies_as_defusedcsv(self, tmp_path, source) -> None:
        """WI-kozaj: the write is the proxy's ``writerows``, reached through the
        community signature row ``defusedcsv.csv.writer``; the factory call
        itself carries no row, exactly as stdlib ``csv.writer`` does not."""
        tagged = _tagged(tmp_path, source)
        assert ("export", "writerows") in tagged, sorted(tagged)  # reach
        assert tagged[("export", "writerows")] == (
            "fs_write", "defusedcsv.csv._ProxyWriter.writerows")
        assert tagged[("export", "writer")] == (None, None)

    def test_the_taint_sink_is_the_defusedcsv_row(self) -> None:
        cat = load_builtin_taint_catalog()
        idx = _build_callee_index(cat.sinks_for_language("python"))
        hit = _match_propagation_entry(
            idx, "python:defusedcsv.csv._ProxyWriter:0-0:writerow:external_symbol",
            cat.ambiguous_names_for_language("python"), is_resolved=False,
        )
        assert hit is not None
        assert (hit.module, hit.name, hit.zone) == (
            "defusedcsv.csv._ProxyWriter", "writerow", "host_fs")

    def test_the_factory_return_type_mirrors_the_stdlib_row(self) -> None:
        from hypergumbo_core.library_signatures import load_library_signatures

        rows = load_library_signatures("python")
        assert rows["csv.writer"] == "_csv.Writer"
        assert rows["defusedcsv.csv.writer"] == "defusedcsv.csv._ProxyWriter"
        assert _CSV_MIRROR[rows["csv.writer"]] == rows["defusedcsv.csv.writer"]

    def test_the_rows_mirror_the_stdlib_csv_rows(self) -> None:
        """A drop-in must not carry a row its stdlib twin lacks, nor lack one,
        module for module (WI-kozaj added the DictWriter pair)."""
        prims = load_catalog("python").primitives
        stdlib_family = {p.module for p in prims if _in_stdlib_csv_family(p.module)}
        assert "csv" not in stdlib_family, (
            "WI-kozaj: the csv.writer factory carries no row; its write is "
            "_csv.Writer's")
        assert stdlib_family == set(_CSV_MIRROR), (
            "every stdlib csv module that carries a row needs its drop-in twin")
        for stdlib_module, dropin_module in _CSV_MIRROR.items():
            stdlib = {(p.boundary, p.name, p.kind) for p in prims
                      if p.module == stdlib_module}
            dropin = {(p.boundary, p.name, p.kind) for p in prims
                      if p.module == dropin_module}
            assert stdlib, f"control: stdlib {stdlib_module} rows exist"
            assert dropin == stdlib, (stdlib_module, dropin_module)

    def test_the_row_is_community(self) -> None:
        rows = [p for p in load_catalog("python").primitives
                if p.module in _CSV_MIRROR.values()]
        assert {p.module for p in rows} == set(_CSV_MIRROR.values())
        assert all(p.unvouched for p in rows)


_STORAGE_EXPECTED = {
    ("keep", "save"): ("fs_write", "django.core.files.storage.default_storage.save"),
    ("drop", "delete"): ("fs_write", "django.core.files.storage.default_storage.delete"),
    ("overwrite", "open"): ("fs_write", "django.core.files.storage.default_storage.open"),
    ("read", "open"): ("fs_read", "django.core.files.storage.default_storage.open"),
    ("probe", "exists"): ("fs_read", "django.core.files.storage.default_storage.exists"),
}


class TestDefaultStorageIsCatalogued:

    @pytest.mark.parametrize("site", sorted(_STORAGE_EXPECTED),
                             ids=lambda s: f"{s[0]}-{s[1]}")
    def test_the_call_classifies(self, tmp_path, site) -> None:
        tagged = _tagged(tmp_path, _STORAGE)
        assert site in tagged, sorted(tagged)  # reach
        assert tagged[site] == _STORAGE_EXPECTED[site]

    def test_url_is_not_a_boundary(self, tmp_path) -> None:
        """FileSystemStorage.url is ``urljoin(base_url, filepath_to_uri(name))``."""
        tagged = _tagged(tmp_path, _STORAGE)
        assert ("link", "url") in tagged, sorted(tagged)  # reach
        assert tagged[("link", "url")] == (None, None)

    def test_save_is_a_host_fs_sink(self) -> None:
        cat = load_builtin_taint_catalog()
        idx = _build_callee_index(cat.sinks_for_language("python"))
        hit = _match_propagation_entry(
            idx,
            "python:django.core.files.storage.default_storage:0-0:save:external_symbol",
            cat.ambiguous_names_for_language("python"), is_resolved=False,
        )
        assert hit is not None and hit.zone == "host_fs"

    def test_without_the_community_overlay_nothing_classifies(self, tmp_path) -> None:
        """Control: the classification is the overlay's rows and nothing else."""
        tagged = _tagged(tmp_path, _STORAGE, defaults=False)
        assert ("keep", "save") in tagged  # reach
        assert all(v == (None, None) for k, v in tagged.items()), tagged
