# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kipor: a bare constructor name must be binding-confirmed before it is
emitted as receiver evidence.

WHAT WAS WRONG. ``_external_constructor_module`` (py.py) answers "does this call
construct a catalogued I/O object?" from two branches. The ``ast.Attribute``
branch (``socket.socket(...)``) verifies the base name is a real module import
(``func.value.id in module_imports``) before trusting it. The ``ast.Name`` branch
(``open(...)``) did not verify anything at all — it looked the bare identifier up
in ``EXTERNAL_CONSTRUCTOR_TYPES`` and emitted the catalogued module as the
receiver type of whatever the call returned. So a file that rebinds the name::

    from decoy_lib import open
    v = open(spec)
    return v.read()          # reported fs_read against ``file.read``

produced a filesystem-read boundary for an object that is not a file. Wrong in
the expensive direction for a security tool, on the shipping tree, with no
configuration required.

WHY THE CONSUMER CANNOT FIX IT. The lying edge and an honest one are
byte-identical by the time they reach the catalogue: same ``call_construct``,
same ``resolution_quality``, same shape. The evidence that would separate them —
what the name is bound to in this file — is destroyed at the producer. Gating the
consumer instead (the remedy INV-kipor was originally filed with: route the
module-hint branch of ``lookup_with_module`` through ``gate_named_entry``) refuses
the false positive only by refusing the entire class, because that gate vetoes
every ``call_construct == "method"`` unconditionally — measured at 61.5% of
reported boundaries destroyed, nothing gained, and every correctly-bound site
lost along with the incorrect one.

WHY THIS IS NOT A NEW RULE. The identical test already exists inline in
``_receiver_type_id_trustworthy`` (WI-supat D3), which refuses to stamp a concrete
receiver-type id when a same-name import could rebind it. This is that rule
applied at the one site that skipped it, so both callers now share
``_import_binding_for``.

THE POSITIVE CONTROLS ARE LOAD-BEARING. Every negative assertion here is paired
with an unshadowed twin asserting the boundary IS still reported. Without them a
test asserting "0 boundaries" passes just as well when the analyzer is broken
outright — which is the failure mode this file exists to catch. INV-kipor's
pre-existing regression test stayed green through the entire defect precisely
because it hand-constructed its ``external`` dst instead of running the analyzer.
"""

from pathlib import Path

import hypergumbo_lang_mainstream.py as py
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import load_full_taint_catalog
from hypergumbo_lang_mainstream.py import (
    BUILTIN_CONSTRUCTOR_NAMES,
    EXTERNAL_CONSTRUCTOR_TYPES,
    analyze_python,
)


def _analyse(root: Path, source: str) -> list:
    """Write ``source`` into its own directory under ``root`` and analyse it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _tagged(edges: list) -> int:
    """Count io-boundary tags via the production tagger."""
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


def _module_slot(dst: str) -> str:
    """The module segment of a ``lang:module:span:name:state`` symbol id."""
    return dst.split(":")[1]


def _dsts_named(edges: list, name: str) -> list[str]:
    return [e.dst for e in edges if e.dst.endswith(f":{name}:unresolved")]


class TestBareConstructorBindingIsChecked:
    """The live shipping-tree defect: ``open`` rebound by an import."""

    SHADOWED = (
        "from decoy_lib import open\n"
        "\n"
        "def handler(spec):\n"
        "    v = open(spec)\n"
        "    return v.read()\n"
    )
    UNSHADOWED = (
        "def handler(spec):\n"
        "    v = open(spec)\n"
        "    return v.read()\n"
    )

    def test_import_shadowed_open_reports_no_io_boundary(
        self, tmp_path: Path,
    ) -> None:
        """``open`` is ``decoy_lib.open`` here, so ``v`` is not a file."""
        edges = _analyse(tmp_path / "shadow", self.SHADOWED)
        assert _tagged(edges) == 0, (
            "`open` is import-rebound to decoy_lib, so the WI-fuvuj "
            "constructor inference must withhold the `file` receiver hint "
            "rather than emit it as evidence"
        )

    def test_unshadowed_open_still_reports_its_io_boundaries(
        self, tmp_path: Path,
    ) -> None:
        """POSITIVE CONTROL — the assertion above must not be satisfiable by
        breaking the analyzer."""
        edges = _analyse(tmp_path / "control", self.UNSHADOWED)
        assert _tagged(edges) >= 1

    def test_shadowed_receiver_edge_degrades_to_external(
        self, tmp_path: Path,
    ) -> None:
        """Producer-level: the hint is WITHHELD, not merely unmatched.

        Degrading to ``external`` is what makes one producer fix close every
        consumer — io-boundary and taint both refuse an untyped method call.
        """
        shadow = _dsts_named(_analyse(tmp_path / "s", self.SHADOWED), "read")
        control = _dsts_named(_analyse(tmp_path / "c", self.UNSHADOWED), "read")
        assert shadow and control, "expected a `read` call edge in both arms"
        assert _module_slot(shadow[0]) == "external"
        assert _module_slot(control[0]) == "file"


class TestBindingOwnershipDecidesTrust:
    """The ticket's own example, and the reason the filed remedy was wrong.

    A binding to the module the catalogue claims must be TRUSTED; a binding to any
    other module must be WITHHELD. Refusing both is what the filed remedy did.

    THESE NOW RUN AGAINST THE REAL TABLE. When INV-kipor was filed, ``Path`` was not
    in ``EXTERNAL_CONSTRUCTOR_TYPES``, so this class monkeypatched the row in to pin
    a risk before anyone landed it. The row shipped, the monkeypatch became a no-op
    setting the same value, and a fixture that no longer does anything is a fixture
    that hides whether the real table still satisfies the property. It is removed,
    which is what the original comment ("pinned before anyone lands it") was for.
    """

    GOOD = (
        "from pathlib import Path\n"
        "\n"
        "def h(i, a, b):\n"
        "    p = Path(i)\n"
        "    return p.replace(a, b)\n"
    )
    BAD = (
        "from fastapi import Path\n"
        "\n"
        "def h(i, a, b):\n"
        "    p = Path(i)\n"
        "    return p.replace(a, b)\n"
    )

    def test_the_row_this_class_depends_on_is_really_in_the_table(self) -> None:
        """Guard against the class going vacuous now that the monkeypatch is gone."""
        assert py.EXTERNAL_CONSTRUCTOR_TYPES.get("Path") == "pathlib.Path"

    def test_matching_binding_is_trusted(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL for the class: ``from pathlib import Path`` is the
        real thing and must keep its boundary."""
        edges = _analyse(tmp_path / "good", self.GOOD)
        assert _tagged(edges) >= 1
        assert _module_slot(_dsts_named(edges, "replace")[0]) == "pathlib.Path"

    def test_mismatched_binding_is_withheld(self, tmp_path: Path) -> None:
        """``fastapi.Path`` is a parameter declaration, not a filesystem path;
        ``.replace`` on it is ``str.replace``. The CATALOGUED type is withheld;
        the instance is typed as what the import actually supplied
        (``fastapi.Path``, WI-makij's imported-class rule), which reaches no row."""
        edges = _analyse(tmp_path / "bad", self.BAD)
        assert _module_slot(_dsts_named(edges, "replace")[0]) == "fastapi.Path"
        assert _tagged(edges) == 0

    def test_mismatched_binding_is_withheld_from_taint_too(
        self, tmp_path: Path,
    ) -> None:
        """One producer fix closes BOTH consumers.

        ``pathlib.Path.replace`` is a taint sink (host_fs), so the mis-bound hint
        would mint a sink as well as a boundary — the WI-razol cascade the
        catalogue's ``ambiguous_names`` list was written to close.
        """
        catalog = load_full_taint_catalog()
        bad = _dsts_named(_analyse(tmp_path / "bad", self.BAD), "replace")[0]
        good = _dsts_named(_analyse(tmp_path / "good", self.GOOD), "replace")[0]
        assert catalog.match_sink(
            "python", "replace", _module_slot(bad), "method",
        ) is None
        assert catalog.match_sink(
            "python", "replace", _module_slot(good), "method",
        ) is not None


class TestEveryBareConstructorKeyIsGuarded:
    """Parity over the DATA TABLE, so a future key cannot skip the check.

    The population that must be enumerated is ``EXTERNAL_CONSTRUCTOR_TYPES``
    itself — not the catalogue consumers — because the defect is upstream of
    every consumer. Fixture method names are read from ``python.yaml`` rather
    than written here, so the test grows with the catalogue.
    """

    @staticmethod
    def _bare_keys() -> list[tuple[str, str]]:
        return [(k, v) for k, v in EXTERNAL_CONSTRUCTOR_TYPES.items() if "." not in k]

    def test_dotted_keys_are_already_binding_checked(self) -> None:
        """A dotted key is reachable only through the ``ast.Attribute`` branch,
        which has always required ``func.value.id in module_imports``."""
        dotted = [k for k in EXTERNAL_CONSTRUCTOR_TYPES if "." in k]
        assert dotted, "expected at least one dotted key (socket.socket)"

    def test_every_bare_key_is_enumerated(self) -> None:
        """Guard against the table emptying and the parity test going vacuous."""
        assert self._bare_keys(), "no bare constructor keys left to guard"

    @staticmethod
    def _trusting_prelude(key: str, module: str) -> str:
        """The import that makes ``key`` trusted, or '' when none is needed.

        A REAL BUILTIN is trusted while unbound, so its control fixture must have no
        import — adding one would test a different branch. Every other key must be
        POSITIVELY bound, so its control fixture has to supply the binding. Deriving
        the import from the claimed module string rather than hard-coding it is what
        keeps this parity test growing with the table instead of with this file.
        """
        if key in BUILTIN_CONSTRUCTOR_NAMES:
            return ""
        package, _, name = module.rpartition(".")
        assert package and name == key, (
            f"{key!r} is not a builtin, so it must be positively bound — but its "
            f"claimed module {module!r} is not of the form '<package>.{key}', so no "
            f"import can be constructed and the guard below could not be exercised"
        )
        return f"from {package} import {key}\n\n"

    def test_each_bare_key_withholds_a_shadowed_binding(
        self, tmp_path: Path,
    ) -> None:
        catalog = load_catalog("python")
        for key, module in self._bare_keys():
            methods = [
                p.name for p in catalog.primitives
                if p.module == module and p.kind == "method"
            ]
            assert methods, (
                f"{key!r} claims module {module!r} but python.yaml declares no "
                f"method on it — the fixture below could not detect a regression"
            )
            method = sorted(methods)[0]
            body = (
                "def handler(spec):\n"
                f"    v = {key}(spec)\n"
                f"    return v.{method}()\n"
            )
            shadowed = _analyse(
                tmp_path / f"neg_{key}", f"from decoy_lib import {key}\n\n{body}",
            )
            control = _analyse(
                tmp_path / f"pos_{key}", self._trusting_prelude(key, module) + body,
            )
            assert _tagged(control) >= 1, (
                f"positive control failed for {key!r}: the unshadowed fixture "
                f"reports no boundary, so the negative assertion proves nothing"
            )
            assert _tagged(shadowed) == 0, (
                f"{key!r} is import-rebound yet still minted a {module!r} "
                f"receiver hint"
            )

    def test_each_non_builtin_bare_key_withholds_when_unbound(
        self, tmp_path: Path,
    ) -> None:
        """An unbound bare name is trusted ONLY for a real builtin.

        The table used to hold only ``open``, so "unbound means the builtin" was
        true of every row and the branch was written that way. It stopped being true
        the moment a non-builtin bare key was added: an unbound ``Path`` is a local
        class, a star-import, or a name from a module never read — and trusting it
        mints an ``fs_write`` boundary and a ``host_fs`` taint SINK for any class
        merely named ``Path``. Enumerated over the table so the next non-builtin key
        inherits the guard instead of re-learning it.
        """
        catalog = load_catalog("python")
        checked = 0
        for key, module in self._bare_keys():
            if key in BUILTIN_CONSTRUCTOR_NAMES:
                continue
            method = sorted(
                p.name for p in catalog.primitives
                if p.module == module and p.kind == "method"
            )[0]
            body = (
                "def handler(spec):\n"
                f"    v = {key}(spec)\n"
                f"    return v.{method}()\n"
            )
            assert _tagged(_analyse(tmp_path / f"unbound_{key}", body)) == 0, (
                f"{key!r} is not a builtin, yet an UNBOUND use still minted a "
                f"{module!r} receiver hint"
            )
            # Control: the same fixture WITH the binding must report a boundary,
            # or the zero above is a fixture that never worked.
            bound = _analyse(
                tmp_path / f"bound_{key}", self._trusting_prelude(key, module) + body,
            )
            assert _tagged(bound) >= 1, (
                f"positive control failed for {key!r}: the BOUND fixture reports no "
                f"boundary, so the unbound zero proves nothing"
            )
            checked += 1
        assert checked, (
            "no non-builtin bare keys in the table — this guard went vacuous; "
            "delete it or add the key it was written for"
        )

    def test_builtin_names_are_a_subset_of_the_bare_keys(self) -> None:
        """A name in ``BUILTIN_CONSTRUCTOR_NAMES`` that is not in the table grants
        an exemption to nothing and hides a typo — the set would silently stop
        protecting the row it was written for."""
        assert BUILTIN_CONSTRUCTOR_NAMES <= {k for k, _ in self._bare_keys()}
