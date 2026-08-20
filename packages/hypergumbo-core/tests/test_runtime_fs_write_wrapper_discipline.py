# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-zudak: runtime CLI code writes to the filesystem only through a wrapper.

THE INVARIANT, as filed: *every entry point with an asserted "no direct host_fs
writes" claim must route every fs-write primitive through a ``safety_zones.py``
wrapper.* ``runtime-cli-no-host-fs`` is such a claim.

WHY THIS FILE EXISTS RATHER THAN A FIX FOR EIGHT CALL SITES. INV-zudak was
closed `satisfied` in May after migrating ``build_grammars.py`` and
``gitleaks.py`` — the INSTALL entry points. The RUNTIME entry points carry the
identical defect and were never in scope, so the invariant read as held while
eight call sites violated it. A per-site fix would close today's eight and
leave the ninth to be found by the next person who happens to run
`verify-claims` and read the output. This asserts the rule instead, over the
LIVE catalogue, so a newly-added unwrapped write fails here.

WHAT THE VIOLATIONS WERE, and why they are easy to write by accident: four of
the five ``cli.py`` sites sit on the line DIRECTLY ABOVE a ``user_out_write`` /
``user_out_open_json_dump`` call. The write was wrapped; the ``mkdir`` that
creates its directory was not — because ``safety_zones`` had ``cache_mkdir``,
``tmp_artifact_mkdir`` and ``install_artifact_mkdir`` but no ``user_out_mkdir``.
The one zone that takes a USER-SUPPLIED path was the one zone with no mkdir
wrapper, so the correct call did not exist to make.

SCOPE. The modules listed in ``_RUNTIME_MODULES`` are those reachable from a
runtime ``cmd_*`` handler that perform filesystem writes. Install/build modules
(``build_grammars``, ``gitleaks``, ``rust_analyzer_install``) are deliberately
excluded: they have their own zones and their own claims, and INV-zudak's
May closure already covers them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog

_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core"

# Modules on the runtime-CLI path that touch the filesystem. safety_zones.py
# is the wrapper implementation itself and is where these calls belong.
_RUNTIME_MODULES = ("cli.py", "sketch_embeddings.py", "finalize.py")


def _fs_write_names() -> frozenset[str]:
    """Short names of catalogued python ``fs_write`` primitives, minus the
    ones a bare name cannot identify.

    Derived from the shipped catalogue, not listed here, so adding a primitive
    to ``python.yaml`` extends this guard automatically.

    TWO EXCLUSIONS, both of which are the difference between a guard and a
    false-positive generator. This check matches on the ATTRIBUTE NAME with no
    receiver evidence, which is precisely the shape that produces spurious
    findings everywhere else in this codebase (INV-tapat / INV-maluk / the
    WI-dozon ``str.replace`` population).

    1. ``ambiguous_names`` — the catalogue's OWN curated set of names that
       collide with non-I/O methods. Reusing it rather than hand-listing
       exclusions keeps one home for the fact. Without it this flagged 21
       false positives against 8 real ones: ``sym.id.replace(prefix, "")``
       (``str.replace``, 13 sites), ``sys.stderr.write(...)`` (console output,
       not a host_fs path, 3 sites), ``yaml.dump(...)`` and friends.
    2. ``open`` — dual-classified, with the mode deciding (WI-rusof), so a
       bare ``open(p)`` is a READ and flagging it would fire on every file
       the CLI reads.

    KNOWN LIMIT, stated rather than implied: an unwrapped ``os.remove(p)`` on
    the runtime path is NOT caught here, because ``remove`` is ambiguous. This
    is a syntactic backstop for the unambiguous majority; the boundary
    analysis, which has receiver evidence, is what covers the rest. A null
    from this file means "no unambiguous direct write", not "no direct write".
    """
    catalog = load_catalog("python")
    return frozenset(
        p.name
        for p in catalog.primitives
        if p.boundary == "fs_write"
        and p.name != "open"
        and p.name not in catalog.ambiguous_names
    )


def _direct_fs_writes(module_path: Path, names: frozenset[str]) -> list[str]:
    """Calls to an fs-write primitive by attribute or bare name.

    Matches ``x.mkdir(...)`` / ``shutil.rmtree(...)`` / ``os.remove(...)``.
    A call routed through a wrapper reads ``cache_mkdir(path)`` and does not
    match, which is exactly the distinction being enforced.
    """
    tree = ast.parse(module_path.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called = func.attr
        elif isinstance(func, ast.Name):
            called = func.id
        else:
            continue
        if called in names:
            found.append(f"{module_path.name}:{node.lineno} -> {called}")
    return found


class TestRuntimeCodeWritesOnlyThroughWrappers:

    @pytest.mark.parametrize("module_name", _RUNTIME_MODULES)
    def test_no_direct_fs_write_primitive(self, module_name: str) -> None:
        offenders = _direct_fs_writes(_SRC / module_name, _fs_write_names())
        assert offenders == [], (
            "runtime-path code calls an fs-write primitive directly instead of "
            "through a safety_zones wrapper, so the zone-barrier sanitizer does "
            "not apply and the write lands as an unsanitized host_fs flow:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_guard_would_catch_a_regression(self) -> None:
        """Positive control — a null here must mean "clean", not "blind".

        Without this, deleting the catalogue lookup or mistyping an attribute
        name would leave every module reporting zero offenders and the suite
        green over an unenforced invariant.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(
                "import shutil\n"
                "from pathlib import Path\n\n"
                "def bad(p):\n"
                "    Path(p).mkdir(parents=True)\n"
                "    shutil.rmtree(p)\n",
            )
            offenders = _direct_fs_writes(probe, _fs_write_names())
        assert len(offenders) == 2, offenders


class TestTheWrapperExistsForEveryZone:
    """The gap that made the violation unavoidable.

    ``user_out`` is the zone whose path comes from the USER (``--out``), so it
    is the one most in need of a checked wrapper, and it was the only zone
    without a mkdir. Asserted per-zone so a future zone cannot be added with a
    write wrapper and no mkdir wrapper.
    """

    @pytest.mark.parametrize("zone", ["cache", "user_out", "tmp_artifact"])
    def test_zone_has_a_mkdir_wrapper(self, zone: str) -> None:
        from hypergumbo_core import safety_zones

        assert hasattr(safety_zones, f"{zone}_mkdir"), (
            f"zone {zone!r} can write but cannot create its own directory "
            f"through a wrapper, so callers must reach for a bare Path.mkdir"
        )
