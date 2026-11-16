# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalogued receiver TYPE must be mintable from its constructor.

``io_primitives/python.yaml`` declares a receiver type in the ``module`` slot of
every ``kind: method`` primitive — ``pathlib.Path``, ``smtplib.SMTP``,
``sqlite3.Cursor`` and the rest. For a method call on such a receiver to reach
its catalogue entry, the analyzer must first know the receiver's type, and the
only way it learns that for an external object is ``EXTERNAL_CONSTRUCTOR_TYPES``.
A type the table cannot mint leaves every method hanging off it structurally
unreachable: ``smtplib.SMTP(a).sendmail(b, b, b)`` would produce no method-call
edge at all. (``scripts/measure-catalogue-reach.py python`` measures reach by
generating one call site per catalogued primitive in both import forms.)

WHY A PARITY TEST AND NOT A FEW EXAMPLES. The failure mode is not "these types
are missing", it is "the table and the catalogue are two homes for one fact and
they drift". So the assertions enumerate the LIVE catalogue and require the table
to cover it — a new catalogue entry fails this file rather than silently going
unreachable.

DIRECTION. A newly mintable receiver type exposes taint sources and sinks, which
only ADDS findings. Typing a SANITIZER receiver would arm the barrier arm, where a
walk returning ``False`` earns ``sanitized`` and drops a flow from the violation
set; :class:`TestDerivationCannotArmTheBarrier` pins that no sanitizer receiver
type is mintable. (history: INV-linub, WI-fasub)

WHY ``io_primitives`` AND NOT ALSO THE TAINT CATALOGUE. The taint catalogue names
two module strings ``io_primitives`` does not —
``cryptography.hazmat.primitives.asymmetric`` and ``...ciphers.aead``. They appear
in taint SOURCE entries (not sanitizer receivers), and neither is a constructible
type: their entries carry the class in the NAME slot (``AESGCM.decrypt``,
``rsa.generate_private_key``), so the module slot holds a module and
``asymmetric(...)`` constructs nothing.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_lang_mainstream.py import (
    BUILTIN_CONSTRUCTOR_NAMES,
    EXTERNAL_CONSTRUCTOR_TYPES,
    _external_constructor_type,
)


def _catalogue_receiver_types() -> set[str]:
    """Every type a ``kind: method`` entry hangs off, read from the live YAML."""
    return {
        p.module for p in load_catalog("python").primitives
        if p.kind == "method" and p.module and "." in p.module
    }


class TestEveryCatalogueReceiverTypeIsMintable:
    """The parity assertion: catalogue types ⊆ what the constructor table mints."""

    def test_the_catalogue_declares_receiver_types(self) -> None:
        """NON-VACUITY FLOOR. An empty catalogue would satisfy every assertion
        below while testing nothing. (history: WI-jolif)"""
        types = _catalogue_receiver_types()
        assert len(types) >= 15, (
            f"expected the python catalogue to declare a substantial set of "
            f"method-receiver types; got {sorted(types)}"
        )

    def test_every_catalogue_type_has_a_dotted_key(self) -> None:
        """``import smtplib; smtplib.SMTP(a)`` — the ``ast.Attribute`` branch."""
        missing = sorted(
            t for t in _catalogue_receiver_types()
            if EXTERNAL_CONSTRUCTOR_TYPES.get(t) != t
        )
        assert missing == [], (
            f"{len(missing)} catalogued receiver type(s) cannot be minted from a "
            f"dotted constructor, so every method on them is unreachable: {missing}"
        )

    def test_every_catalogue_type_is_mintable_from_a_bare_constructor(self) -> None:
        """``from smtplib import SMTP; SMTP(a)`` — the ``ast.Name`` branch.

        Asked of the RESOLVER, not of the table's bare keys. A leaf two types
        claim is withheld from the table by design (``csv.DictWriter`` and the
        community ``defusedcsv.csv.DictWriter``, WI-kozaj), and the binding
        check still mints each from its own import through the PascalCase
        construction rule. A withheld LOWERCASE leaf would have no such route,
        and this assertion is what would say so.
        """
        missing = []
        for t in sorted(_catalogue_receiver_types()):
            module, leaf = t.rsplit(".", 1)
            call = ast.parse(f"{leaf}(a)").body[0].value
            assert isinstance(call, ast.Call)
            if _external_constructor_type(call, {leaf: (module, leaf)}, {}) != t:
                missing.append(t)
        assert missing == [], (
            f"{len(missing)} catalogued receiver type(s) cannot be minted from a "
            f"bare constructor: {missing}"
        )

    def test_a_withheld_leaf_is_withheld_from_the_table(self) -> None:
        """The collision guard is live: the shared ``DictWriter`` leaf is not
        in the table, so the test above exercised the binding route for it."""
        types = _catalogue_receiver_types()
        assert {"csv.DictWriter", "defusedcsv.csv.DictWriter"} <= types
        assert "DictWriter" not in EXTERNAL_CONSTRUCTOR_TYPES

    def test_the_file_row_survives_derivation(self) -> None:
        """``open`` → ``file`` is NOT catalogue-derived and must not be lost.

        The synthetic ``file`` module has no dot and hangs off no constructor
        name that appears in the catalogue's module slots, so a derivation that
        only reads ``python.yaml`` would silently drop it and take
        ``f.read()`` / ``f.write()`` down with it.
        """
        assert EXTERNAL_CONSTRUCTOR_TYPES.get("open") == "file"
        assert "open" in BUILTIN_CONSTRUCTOR_NAMES


class TestDerivationCannotArmTheBarrier:
    """The direction claim is asserted, not just described in a docstring."""

    def test_no_sanitizer_receiver_type_is_mintable(self) -> None:
        """A sanitizer receiver arms the barrier arm, which DELETES findings.

        A SANITIZER ROW HAS A DIFFERENT SHAPE FROM A SOURCE OR SINK ROW, and
        getting that wrong makes this assertion pass while measuring nothing. Sources and sinks carry ``module`` + ``name`` + ``kind``;
        sanitizers carry only ``qualified_name`` (``cryptography.fernet.Fernet.
        encrypt``) and ``short_name`` (``Fernet.encrypt``). A ``kind == "method"``
        filter therefore matches ZERO sanitizer rows and yields an empty set that
        intersects everything to nothing — an uncontrolled zero dressed as a
        clean result. The receiver type is recovered by stripping the METHOD off
        ``qualified_name``, and the set is asserted non-empty first.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog

        catalog = load_builtin_taint_catalog()
        sanitizers = list(catalog.sanitizers_for_language("python"))
        assert sanitizers, "no python sanitizers loaded — assertion vacuous"
        sanitizer_types = {
            s.qualified_name.rsplit(".", 1)[0]
            for s in sanitizers
            if "." in getattr(s, "qualified_name", "")
        }
        assert sanitizer_types, "recovered no sanitizer receiver types — vacuous"
        leaked = sorted(sanitizer_types & set(EXTERNAL_CONSTRUCTOR_TYPES.values()))
        assert leaked == [], (
            f"constructor table mints sanitizer receiver type(s) {leaked}, which "
            f"arms the WI-fasub barrier arm; that direction needs its own "
            f"measurement and its own revertable PR"
        )


class TestBareKeysStayUnambiguous:
    """Default-deny on a leaf name two types both claim."""

    def test_no_bare_key_maps_to_two_types(self) -> None:
        leaves = collections.Counter(
            t.rsplit(".", 1)[1] for t in _catalogue_receiver_types()
        )
        collisions = {k: v for k, v in leaves.items() if v > 1}
        for leaf in collisions:
            assert leaf not in EXTERNAL_CONSTRUCTOR_TYPES, (
                f"bare key {leaf!r} is claimed by {collisions[leaf]} distinct "
                f"catalogue types; picking one silently mis-types the others, so "
                f"the ambiguous leaf must be withheld"
            )


class TestTheReceiverActuallyResolves:
    """Behavioural, through the real analyzer — the table is a means, not the end.

    NO GRAMMAR-AVAILABILITY FIXTURE, DELIBERATELY. ``analyze_python`` parses
    with the stdlib ``ast`` module and needs no tree-sitter grammar at all, so a
    guard such as ``is_grammar_available("tree_sitter_python")`` can only skip
    these tests silently and report green while asserting nothing. A python
    analyzer test has no unavailability path to guard. (history: WI-jolif)
    """
    @staticmethod
    def _method_call_dsts(tmp_path: Path, source: str) -> set[str]:
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "app.py").write_text(source)
        analysis = analyze_python(repo)
        return {
            e.dst for e in analysis.edges
            if e.edge_type == "calls" and (e.meta or {}).get(
                "call_construct") == "method"
        }

    @pytest.mark.parametrize(("prelude", "ctor"), [
        ("import smtplib", "smtplib.SMTP"),
        ("from smtplib import SMTP", "SMTP"),
    ])
    def test_smtp_sendmail_reaches_its_catalogue_module(
        self, tmp_path: Path, prelude: str, ctor: str,
    ) -> None:
        """Both import forms, because they enter different analyzer branches."""
        dsts = self._method_call_dsts(tmp_path, (
            f"{prelude}\n\n\ndef send(a, b):\n    return {ctor}(a).sendmail(b, b, b)\n"
        ))
        # THE MODULE SLOT IS THE ASSERTION, NOT THE KIND SUFFIX. At the analyzer
        # stage an unresolved external callee ends ``:unresolved``; WI-pubiv's
        # boundary-id remap rewrites that to ``:external_symbol`` only on the
        # final graph. Asserting the suffix here tests which pipeline stage you
        # are standing in, not whether the receiver got typed.
        assert any(d.startswith("python:smtplib.SMTP:") and ":sendmail:" in d
                   for d in dsts), (
            f"sendmail did not resolve onto the catalogued smtplib.SMTP receiver; "
            f"method-call dsts were {sorted(dsts)}"
        )

    def test_a_shadowed_binding_still_withholds_the_type(
        self, tmp_path: Path,
    ) -> None:
        """Every bare catalogue name in the table is a chance to mint a type
        for a class that merely shares the name, and the binding check
        (INV-kipor) is the only thing standing between them and a fabricated
        network sink."""
        dsts = self._method_call_dsts(tmp_path, (
            "from decoy_lib import SMTP\n\n\n"
            "def send(a, b):\n    return SMTP(a).sendmail(b, b, b)\n"
        ))
        assert not any(":smtplib.SMTP:" in d for d in dsts), (
            f"a decoy SMTP was typed as the stdlib smtplib.SMTP: {sorted(dsts)}"
        )


class TestWideningDoesNotMintFakeBuiltins:
    """The table's bare keys must not license a ``builtins`` call edge.

    ``_process_call``'s bare-call arm emits
    ``python:builtins:0-0:<name>:unresolved`` — an assertion that the name IS a
    builtin. The catalogue-derived ``EXTERNAL_CONSTRUCTOR_TYPES`` holds bare type
    names that are not builtins (``StreamWriter``, from ``asyncio.StreamWriter``),
    so that arm gates on ``PY_BUILTIN_CALLABLES`` (real builtins) and on the name
    not being rebound in an enclosing scope — never on table membership. Gating
    on the table would fabricate ``builtins.StreamWriter`` for
    ``StreamWriter = codecs.getwriter('utf-8'); StreamWriter(byte_data)``, a
    LOCAL name rebound to a codecs writer that ``_external_constructor_type``'s
    binding check (INV-kipor) correctly refuses to type.
    """

    @staticmethod
    def _dsts(tmp_path: Path, source: str) -> set[str]:
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "app.py").write_text(source)
        return {e.dst for e in analyze_python(repo).edges}

    def test_an_unbound_non_builtin_key_mints_no_builtins_edge(
        self, tmp_path: Path,
    ) -> None:
        """A local name rebound from ``codecs.getwriter``, then called."""
        dsts = self._dsts(tmp_path, (
            "import codecs\n\n\n"
            "def export(byte_data):\n"
            "    StreamWriter = codecs.getwriter('utf-8')\n"
            "    return StreamWriter(byte_data)\n"
        ))
        assert not any(d.startswith("python:builtins:0-0:StreamWriter:")
                       for d in dsts), (
            f"a local name rebound from codecs was reported as a builtin "
            f"constructor: {sorted(d for d in dsts if 'StreamWriter' in d)}"
        )

    def test_open_still_mints_its_builtins_edge(self, tmp_path: Path) -> None:
        """NON-VACUITY / NON-DESTRUCTION. The arm exists for ``open`` (WI-mitul)
        and gating it must not take that with it — when 'no change' is the
        correct outcome for everything else, a branch that emits nothing at all
        looks identical to a working one."""
        dsts = self._dsts(tmp_path, (
            "def load(p):\n    return open(p).read()\n"
        ))
        assert any(d.startswith("python:builtins:0-0:open:") for d in dsts), (
            f"open() no longer emits its builtins call edge, which is the whole "
            f"reason the arm exists: {sorted(dsts)}"
        )


def _resolve_ctor(source: str) -> str | None:
    """Run production's own import extraction + constructor typing over ``source``.

    Using :func:`_extract_imports` rather than a hand-built dict is the point: the
    binding rules for ``import a.b`` (which binds ``a``, not ``a.b``) live there,
    and a test that hand-rolls them tests the hand-roll.

    ``source`` must contain EXACTLY ONE call, and that is asserted rather than
    assumed. ``ast.walk`` is breadth-first, so on the chained form
    ``http.client.HTTPConnection(h).request(...)`` the first call it yields is
    ``.request(...)`` — the outer one, which would report a working resolver as
    broken. Picking the wrong node silently is the failure mode; the chained shape is covered behaviourally instead, where
    the analyzer decides which node is the constructor.
    """
    import ast

    from hypergumbo_lang_mainstream.py import (
        _collect_scope_value_names,
        _extract_imports,
        _external_constructor_type,
    )

    tree = ast.parse(source)
    imports, module_imports = _extract_imports(tree, "app")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert len(calls) == 1, (
        f"fixture must contain exactly one call so the subject is unambiguous; "
        f"found {len(calls)}"
    )
    # INV-dulum: the call's own function scope, from production's collector, so
    # a parameter or local that shadows an import is seen the way the analyzer
    # sees it. Fixtures here hold at most one function around the call.
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    shadowed = _collect_scope_value_names(funcs[0]) if funcs else frozenset()
    return _external_constructor_type(
        calls[0], imports, module_imports, shadowed=shadowed,
    )


class TestDottedModuleConstructorsResolve:
    """A constructor under a DOTTED module must be typable.

    For ``http.client.HTTPConnection(h)`` the call's base is itself an
    ``ast.Attribute`` (``http.client``), so a resolver that requires an
    ``ast.Name`` base cannot reach it no matter what the table holds.
    :class:`TestEveryCatalogueReceiverTypeIsMintable` above asserts the TABLE
    holds a key; this class asserts RESOLUTION. Table coverage is not the same
    claim as reachability.

    WHY THE PARITY TEST ENUMERATES DEPTH RATHER THAN NAMING TYPES. Which types
    sit under a dotted module is an accident of what ``python.yaml`` declares;
    the defect class is a depth assumption. So the assertion is over every
    catalogued type at every depth, and a five-segment type added to the YAML
    fails here rather than going quietly unreachable.

    ``django.db.models`` also sits under a dotted module but is a MODULE, not a
    constructible type: ``models(...)`` constructs nothing, and its primitives
    reach production through the ``.objects.`` dispatch instead (WI-sozoj).
    (history: WI-lifol)
    """

    def test_the_dotted_form_types_its_receiver(self) -> None:
        assert _resolve_ctor(
            "import http.client\n\n\n"
            "def fetch(h):\n"
            "    return http.client.HTTPConnection(h)\n"
        ) == "http.client.HTTPConnection"

    def test_every_catalogue_type_resolves_from_its_dotted_form(self) -> None:
        """The anti-drift assertion, over the live catalogue at any depth."""
        types = sorted(_catalogue_receiver_types())
        assert types, "no catalogued receiver types — assertion vacuous"
        unreachable = [
            t for t in types
            if _resolve_ctor(
                f"import {t.rsplit('.', 1)[0]}\n\n\ndef f(a):\n    return {t}(a)\n"
            ) != t
        ]
        assert unreachable == [], (
            f"{len(unreachable)} catalogued receiver type(s) hold a table key but "
            f"cannot be RESOLVED from the dotted call form, so every method on "
            f"them stays unreachable: {unreachable}"
        )

    def test_single_segment_modules_still_resolve(self) -> None:
        """NON-DESTRUCTION. The single-segment module case carries most
        catalogued types; breaking it would be a net loss that a green suite on
        the dotted case would happily hide."""
        assert _resolve_ctor(
            "import smtplib\n\n\ndef send(a):\n    return smtplib.SMTP(a)\n"
        ) == "smtplib.SMTP"

    def test_an_alias_still_resolves(self) -> None:
        """``import http.client as hc`` binds ``hc`` directly to the module;
        dotted-module resolution must not regress it."""
        assert _resolve_ctor(
            "import http.client as hc\n\n\ndef f(h):\n    return hc.HTTPConnection(h)\n"
        ) == "http.client.HTTPConnection"

    def test_an_unimported_root_mints_nothing(self) -> None:
        """DEFAULT-DENY. A local object that happens to expose the same attribute
        path must not be typed as the stdlib class — the standing receiver-typing
        hazard is an external qualified type walking into a bare-name lookup
        against the repo's own symbols."""
        assert _resolve_ctor(
            "def f(http, h):\n    return http.client.HTTPConnection(h)\n"
        ) is None

    def test_a_decoy_module_of_the_same_shape_mints_its_own_type_not_the_catalogued_one(
        self,
    ) -> None:
        """``import mylib.client`` must not satisfy ``http.client.HTTPConnection``
        merely by ending in the same two segments — the resolver must refuse it
        itself rather than rely on a downstream module match. The instance IS a
        construction of what the import supplied, so it is typed
        ``mylib.client.HTTPConnection`` (WI-makij's imported-class rule) — its
        own path, which reaches no row."""
        assert _resolve_ctor(
            "import mylib.client\n\n\n"
            "def f(h):\n    return mylib.client.HTTPConnection(h)\n"
        ) == "mylib.client.HTTPConnection"

    def test_a_local_shadow_should_not_be_typed(self) -> None:
        """``module_imports`` is file-scoped, so on its own a ``http``
        PARAMETER would satisfy the root check and the call would be typed as
        the stdlib class; the scope's value bindings withhold it.
        (history: INV-dulum)"""
        assert _resolve_ctor(
            "import http.client\n\n\n"
            "def f(http, h):\n    return http.client.HTTPConnection(h)\n"
        ) is None

    def test_a_function_local_import_is_still_typed(self) -> None:
        """THE TRAP for the shadow check: a function-local
        ``import`` binds the name TO THE MODULE. It is a local binding, but not
        a shadow, and refusing it would turn the false positive into a false
        negative on the ordinary lazy-import idiom."""
        assert _resolve_ctor(
            "def f(h):\n    import http.client\n"
            "    return http.client.HTTPConnection(h)\n"
        ) == "http.client.HTTPConnection"


class TestDottedConstructorsReachTheCatalogue:
    """Behavioural, through the real analyzer — resolution is a means, not the end."""

    @staticmethod
    def _method_call_dsts(tmp_path: Path, source: str) -> set[str]:
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "app.py").write_text(source)
        analysis = analyze_python(repo)
        return {
            e.dst for e in analysis.edges
            if e.edge_type == "calls" and (e.meta or {}).get(
                "call_construct") == "method"
        }

    def test_httpconnection_request_reaches_its_catalogue_module(
        self, tmp_path: Path,
    ) -> None:
        """The common real-code shape: construct, then call."""
        dsts = self._method_call_dsts(tmp_path, (
            "import http.client\n\n\n"
            "def fetch(host, path):\n"
            "    conn = http.client.HTTPConnection(host)\n"
            "    return conn.request('GET', path)\n"
        ))
        assert any(d.startswith("python:http.client.HTTPConnection:")
                   and ":request:" in d for d in dsts), (
            f"request did not resolve onto the catalogued receiver; method-call "
            f"dsts were {sorted(dsts)}"
        )

    def test_the_chain_root_form_also_reaches_it(self, tmp_path: Path) -> None:
        """``http.client.HTTPConnection(h).request(...)`` with no intervening
        variable — a SECOND consumer of :func:`_external_constructor_type`, in a
        different scope (``_process_call`` types a chain ROOT; the per-block
        closure types an ASSIGNMENT). One of the two working is not evidence for
        the other, and the unit test above cannot cover this shape because the
        fixture then holds two calls."""
        dsts = self._method_call_dsts(tmp_path, (
            "import http.client\n\n\n"
            "def fetch(host, path):\n"
            "    return http.client.HTTPConnection(host).request('GET', path)\n"
        ))
        assert any(d.startswith("python:http.client.HTTPConnection:")
                   and ":request:" in d for d in dsts), (
            f"the chain-root form did not resolve onto the catalogued receiver; "
            f"method-call dsts were {sorted(dsts)}"
        )
