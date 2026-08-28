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

    @pytest.mark.parametrize("name", ["cwd", "home"])
    def test_classmethods_are_function_kind(self, name: str) -> None:
        kinds = _kinds("pathlib.Path", language="python")
        assert kinds.get(name) == "function", (
            f"pathlib.Path.{name} is a classmethod; a method-kind entry "
            f"cannot match `Path.{name}()`."
        )

    @pytest.mark.parametrize("name", ["expanduser", "absolute"])
    def test_instance_methods_stay_method_kind(self, name: str) -> None:
        """The control — the split must not sweep the real methods along."""
        kinds = _kinds("pathlib.Path", language="python")
        assert kinds.get(name) == "method", (
            f"pathlib.Path.{name} is called on a Path instance."
        )
