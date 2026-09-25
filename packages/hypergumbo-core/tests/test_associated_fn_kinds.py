# SPDX-License-Identifier: AGPL-3.0-or-later
"""A receiverless callee must not be catalogued as a method (INV-nular).

INV-nular's invariant is that a primitive's KIND must be checked against
semantics rather than asserted by name. rust.yaml asserted it wrongly for every
constructor-shaped stdlib entry, and the cost was not a missed finding — it was
a WITHHELD VERDICT on every repo that opens a file.

THE MECHANISM, measured on encrypted-dns-server. ``File::open(path)`` takes no
receiver, so its call site can only ever produce a function-construct edge. The
analyzer resolves it perfectly — ``rust:std::fs::File:0-0:open:external_symbol``
— but with ``methods: [open]`` in the catalogue:

  1. ``io_boundary``'s method-kind gate can never match it, so the whole
     ``std::fs::File`` half of the fs catalogue was unreachable BY
     CONSTRUCTION, not by any analyzer gap; and
  2. ``verify_claims.method_starved_modules`` saw a resolved call into a
     method-keyed module with no method-construct edge and concluded the
     analysis "did not look", withholding all 7 generic claims.

Re-kinding ``std::fs::File`` alone dropped the starved list from two modules to
one, live on that repo.

WHY THE CONTROLS MATTER AS MUCH AS THE SUBJECTS. The fix is a kind SPLIT, not a
blanket move: ``TcpStream::connect`` is an associated function while
``TcpStream::write_all`` is a method, and both live under the same module. A
regression that moved the whole module either way would be invisible without
asserting both halves.
"""
import importlib
import inspect
from typing import Optional

import pytest


def _kinds(module: str, language: str = "rust") -> dict[str, str]:
    from hypergumbo_core.io_boundary import load_catalog

    catalog = load_catalog(language)
    assert catalog is not None, f"{language} catalogue must load"
    return {
        p.name: p.kind for p in catalog.primitives if p.module == module
    }


@pytest.mark.parametrize(
    "module,name",
    [
        ("std::fs::File", "open"),
        ("std::fs::File", "create"),
        ("std::fs::File", "create_new"),
        ("std::net::TcpStream", "connect"),
        # ``TcpListener::bind`` / ``UdpSocket::bind`` were here. INV-nular
        # removed those ROWS entirely — net_recv is an auto-derived taint
        # source and binding a socket receives nothing — so there is no longer
        # a kind for them to declare. The property below is about how a
        # PRESENT row is keyed; the absence of a row is a different question,
        # governed by test_blind_language_method_starvation.py, which now pins
        # that a bind-only repo is correctly told the module's receive surface
        # was not examined.
        ("std::process::Command", "new"),
    ],
)
def test_associated_functions_are_function_kind(module: str, name: str) -> None:
    """These take no receiver — a method-kind entry is unmatchable."""
    kinds = _kinds(module)
    assert name in kinds, f"{module}.{name} missing from the rust catalogue"
    assert kinds[name] == "function", (
        f"{module}::{name} is an ASSOCIATED FUNCTION (no receiver), so a "
        f"method-kind entry can never match a call site and additionally marks "
        f"{module} method-starved, withholding every verdict on the repo."
    )


@pytest.mark.parametrize(
    "module,name",
    [
        ("std::net::TcpStream", "write_all"),
        ("std::net::TcpStream", "read_to_end"),
        ("std::net::TcpListener", "accept"),
        ("std::net::UdpSocket", "recv_from"),
        ("std::process::Command", "output"),
        ("std::io::Write", "write_all"),
        ("std::fs::OpenOptions", "open"),
        ("std::path::Path", "exists"),
    ],
)
def test_real_methods_stay_method_kind(module: str, name: str) -> None:
    """The control. These DO take a receiver and must not be swept along.

    ``OpenOptions::open`` is the sharpest case: ``OpenOptions::new()`` is an
    associated function but ``.open(path)`` is called on the builder, so the
    module carries one of each and a blanket re-kind would break it.
    """
    kinds = _kinds(module)
    assert name in kinds, f"{module}.{name} missing from the rust catalogue"
    assert kinds[name] == "method", (
        f"{module}.{name} is called on a receiver; a function-kind entry would "
        f"match a bare {name}() call anywhere in the program."
    )


class TestPythonClassmethodsAreFunctionKind:
    """python.yaml asserted the same error, found by the same sweep.

    ``Path.cwd()`` and ``Path.home()`` are CLASSMETHODS — called on the class,
    no instance. Verified with ``inspect.getattr_static``, which is the probe
    the python stdlib audit already established as the bar for this language
    (unaided reading scored 16% wrong there, every error a false all-clear).

    Milder than rust's: ``pathlib.Path`` also carries genuine instance methods,
    so a repo calling any of them marks the module satisfied and no verdict is
    withheld. The entries were still unmatchable.
    """

    @pytest.mark.parametrize(
        "module,name",
        [
            ("pathlib.Path", "cwd"),
            ("pathlib.Path", "home"),
            # INV-fugus. Keyed ``methods`` by WI-tubij, whose own note called
            # them classmethods. ``_dt.date.today()`` reaches the catalogue on
            # an edge with no ``call_construct``, so the methods-only
            # ``datetime.date`` row starved the module and withheld every
            # verdict of hypergumbo's own self-proof.
            ("datetime.date", "today"),
            ("datetime.datetime", "now"),
            ("datetime.datetime", "utcnow"),
            ("datetime.datetime", "today"),
        ],
    )
    def test_classmethods_are_function_kind(self, module: str, name: str) -> None:
        kinds = _kinds(module, language="python")
        assert kinds.get(name) == "function", (
            f"{module}.{name} is a classmethod; a method-kind entry "
            f"cannot match a call on the class."
        )

    def test_no_method_row_on_an_importable_class_is_receiverless(self) -> None:
        """THE FAMILY, not the instances: sweep every method-kind row.

        The two instances above were found one at a time, a month apart, by
        their symptoms. This asks the bar the stdlib audit set --
        ``inspect.getattr_static`` -- of every method-kind row whose module
        resolves to a class in this interpreter. A row on a module that does
        not import here (a third-party package) is NOT checked, and is not
        claimed to be.
        """
        from hypergumbo_core.io_boundary import load_catalog

        # C-level classmethods (``date.today``) are ``classmethod_descriptor``.
        receiverless = (classmethod, staticmethod, type(dict.__dict__["fromkeys"]))
        checked: list[str] = []
        offenders: list[str] = []
        for prim in load_catalog("python").primitives:
            if prim.kind != "method":
                continue
            owner = _resolve_class(prim.module)
            if owner is None:
                continue
            checked.append(f"{prim.module}.{prim.name}")
            attr = inspect.getattr_static(owner, prim.name, None)
            if isinstance(attr, receiverless):
                offenders.append(f"{prim.module}.{prim.name}")
        assert "pathlib.Path.expanduser" in checked  # reach: the sweep sees rows
        assert offenders == [], (
            f"classmethod/staticmethod rows keyed as methods: {offenders}"
        )

    def test_no_method_row_is_callable_as_written_on_an_object(self) -> None:
        """INV-dihun / INV-zikab: the sweep above only sees CLASS owners.

        A row's kind says how the primitive is reached from the row's own
        ``module`` (INV-zikab, owner-ratified 2026-09-23): ``methods:`` iff it is
        called on an INSTANCE of ``module``. When ``module`` is itself an
        object -- ``ctypes.cdll`` is a module-level ``LibraryLoader`` instance
        -- then ``module.name(...)`` is a valid call as written, so the row is a
        function. Keyed ``methods:``, ``ctypes.cdll.LoadLibrary(p)`` classified
        fs_read while ``method_starved_modules`` reported ``ctypes.cdll``
        starved in the same run: INV-fugus's contradiction, missed by the
        class-only sweep by construction.
        """
        from hypergumbo_core.io_boundary import load_catalog

        offenders = [
            f"{p.module}.{p.name}"
            for p in load_catalog("python").primitives
            if p.kind == "method" and _callable_on_a_non_class_owner(p.module, p.name)
        ]
        assert offenders == [], (
            f"method rows callable as written on an object owner: {offenders}"
        )

    def test_the_object_owner_predicate_has_reach(self) -> None:
        """CONTROLS for the sweep above, which can pass vacuously once every
        offender is fixed: the predicate must still say yes to an object owner
        and no to the shapes that really are methods."""
        assert _callable_on_a_non_class_owner("ctypes.cdll", "LoadLibrary")
        # A CLASS owner is the other sweep's business, not this one's.
        assert not _callable_on_a_non_class_owner("pathlib.Path", "expanduser")
        # A factory whose product carries the method: ``multiprocessing.Queue``
        # is a function, and ``multiprocessing.Queue.put`` is not a valid call.
        assert not _callable_on_a_non_class_owner("multiprocessing.Queue", "put")

    def test_ctypes_cdll_load_library_is_function_kind(self) -> None:
        assert _kinds("ctypes.cdll", language="python").get("LoadLibrary") == "function"

    @pytest.mark.parametrize("name", ["expanduser", "absolute"])
    def test_instance_methods_stay_method_kind(self, name: str) -> None:
        """The control — the split must not sweep the real methods along."""
        kinds = _kinds("pathlib.Path", language="python")
        assert kinds.get(name) == "method", (
            f"pathlib.Path.{name} is called on a Path instance."
        )


def _resolve(dotted: str) -> object:
    """The object a catalogue module path names here, or None."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr, None)
        return obj
    return None


def _resolve_class(dotted: str) -> Optional[type]:
    """The class a catalogue module path names, or None if it is not one here."""
    obj = _resolve(dotted)
    return obj if inspect.isclass(obj) else None


def _callable_on_a_non_class_owner(module: str, name: str) -> bool:
    """True when ``module`` names a non-class object here that ITSELF carries a
    callable ``name`` -- so ``module.name(...)`` is a valid call as written.

    A module object counts: its members are functions by the same rule. A
    module without the name (``django.db.models.filter``, a queryset method
    keyed on its package) or a factory whose product has it
    (``multiprocessing.Queue.put``) is not a valid call as written and is not
    claimed here.
    """
    obj = _resolve(module)
    if obj is None or inspect.isclass(obj):
        return False
    return callable(getattr(obj, name, None))
