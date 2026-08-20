# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-linub: a catalogued receiver TYPE must be mintable from its constructor.

THE GAP THIS PINS. ``io_primitives/python.yaml`` declares a receiver type in the
``module`` slot of every ``kind: method`` primitive — ``pathlib.Path``,
``smtplib.SMTP``, ``sqlite3.Cursor``, seventeen of them. For a method call on
such a receiver to reach its catalogue entry, the analyzer must first know the
receiver's type, and the only way it learns that for an external object is
``EXTERNAL_CONSTRUCTOR_TYPES``. That table was hand-curated at FOUR rows, so
fifteen of the seventeen declared types could never be minted and every method
hanging off them was structurally unreachable.

MEASURED BEFORE THE FIX, by generating one call site per catalogued primitive in
both import forms (``scripts/measure-catalogue-reach.py python``):

    reachable both ways   105   48.8%
    dotted form only       27   12.6%
    bare form only          0    0.0%
    UNREACHABLE            83   38.6%      <- 78 of them method-kind

and the discriminating fixture, run through the real analyzer:

    pathlib.Path(a).write_text(b)              -> calls python:pathlib.Path:...:write_text
    smtplib.SMTP(a).sendmail(b, b, b)          -> NO method-call edge at all
    sqlite3.Cursor(a).execute(b)               -> NO method-call edge at all

WHY A PARITY TEST AND NOT THREE EXAMPLES. The failure mode is not "these three
types are missing", it is "the table and the catalogue are two homes for one
fact and they drift". Fifteen rows added by hand today would be sixteen short
the day someone adds a type to ``python.yaml``. So the assertion enumerates the
LIVE catalogue and requires the table to cover it — a new catalogue entry
tomorrow fails this file rather than silently going unreachable.

DIRECTION, MEASURED BEFORE THE CHANGE. The fifteen newly-mintable types carry
43 of 83 python taint SOURCES and 35 of 113 SINKS — and 0 of 4 sanitizers. That
last figure is the load-bearing one and :class:`TestDerivationCannotArmTheBarrier`
pins it: typing a SANITIZER receiver arms the WI-fasub barrier arm, where a walk
returning ``False`` earns ``sanitized`` and drops a flow from the violation set.
This change cannot reach that arm, so it is additive-only.

WHY ``io_primitives`` AND NOT ALSO THE TAINT CATALOGUE. The taint catalogue names
two module strings ``io_primitives`` does not —
``cryptography.hazmat.primitives.asymmetric`` and ``...ciphers.aead``. An earlier
draft of this file asserted they were sanitizer receivers and excluded them on
direction grounds; that was WRONG and is corrected here rather than left standing.
They are taint SOURCES, and the real reason they are out of scope is that neither
is a constructible type: their entries carry the class in the NAME slot
(``AESGCM.decrypt``, ``rsa.generate_private_key``), so the module slot holds a
module and ``asymmetric(...)`` constructs nothing.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_lang_mainstream.py import (
    BUILTIN_CONSTRUCTOR_NAMES,
    EXTERNAL_CONSTRUCTOR_TYPES,
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
        below while testing nothing — the shape that let three Go integration
        tests pass without executing once (WI-jolif)."""
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

    def test_every_catalogue_type_has_a_bare_key(self) -> None:
        """``from smtplib import SMTP; SMTP(a)`` — the ``ast.Name`` branch."""
        missing = sorted(
            t for t in _catalogue_receiver_types()
            if EXTERNAL_CONSTRUCTOR_TYPES.get(t.rsplit(".", 1)[1]) != t
        )
        assert missing == [], (
            f"{len(missing)} catalogued receiver type(s) cannot be minted from a "
            f"bare constructor: {missing}"
        )

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
        getting that wrong is how this assertion first passed while measuring
        nothing. Sources and sinks carry ``module`` + ``name`` + ``kind``;
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

    NO GRAMMAR-AVAILABILITY FIXTURE, DELIBERATELY. An earlier draft guarded these
    with ``is_grammar_available("tree_sitter_python")``, which returns False on a
    working tree: ``analyze_python`` parses with the stdlib ``ast`` module and
    needs no tree-sitter grammar at all. The three tests below skipped silently
    and the file reported green while asserting nothing — the exact shape that
    hid an ``AttributeError`` behind three never-executed Go tests (WI-jolif).
    A python analyzer test has no unavailability path to guard.
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
        """INV-kipor must survive the widening. Fifteen new bare names are
        fifteen new chances to mint a type for a class that merely shares a
        name, and the binding check is the only thing standing between them
        and a fabricated network sink."""
        dsts = self._method_call_dsts(tmp_path, (
            "from decoy_lib import SMTP\n\n\n"
            "def send(a, b):\n    return SMTP(a).sendmail(b, b, b)\n"
        ))
        assert not any(":smtplib.SMTP:" in d for d in dsts), (
            f"a decoy SMTP was typed as the stdlib smtplib.SMTP: {sorted(dsts)}"
        )


class TestWideningDoesNotMintFakeBuiltins:
    """The table's SECOND consumer assumed it held only builtins.

    ``_process_call`` has an ``elif callee_name in EXTERNAL_CONSTRUCTOR_TYPES``
    arm that emits ``python:builtins:0-0:<name>:unresolved`` — an assertion that
    the name IS a builtin. Its comment states the assumption outright ("only the
    bare ``EXTERNAL_CONSTRUCTOR_TYPES`` key ``open`` can match"), which was true
    while the table was hand-curated and false the moment it grew seventeen more
    bare keys. That is the N-places-one-rule drift this project keeps meeting,
    with a comment claiming it had not happened.

    CAUGHT BY THE DIRECTION CHECK, NOT BY REVIEW. The A/B over hypergumbo and
    pretix gained exactly one edge — ``builtins.StreamWriter`` — from pretix's
    ``StreamWriter = codecs.getwriter('utf-8'); StreamWriter(byte_data)``, a
    LOCAL name rebound to a codecs writer. ``_external_constructor_type``'s
    INV-kipor binding check refused to type the receiver (correctly); this second
    arm fabricated the edge anyway because it never consults a binding at all.

    The permitting predicate is ``BUILTIN_CONSTRUCTOR_NAMES``, which already
    exists and already means exactly "bare rows that are REAL builtins" — so this
    consolidates onto it rather than minting a third rule.
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
        """pretix's exact shape, reduced."""
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
    ``.request(...)`` — the outer one. An earlier draft took that first call and
    reported a genuine fix as still broken. Picking the wrong node silently is
    the failure mode; the chained shape is covered behaviourally instead, where
    the analyzer decides which node is the constructor.
    """
    import ast

    from hypergumbo_lang_mainstream.py import (
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
    return _external_constructor_type(calls[0], imports, module_imports)


class TestDottedModuleConstructorsResolve:
    """WI-lifol: a constructor under a DOTTED module must be typable.

    THE GAP. ``_external_constructor_type``'s attribute branch required
    ``isinstance(func.value, ast.Name)``. For ``http.client.HTTPConnection(h)``
    the base is itself an ``ast.Attribute`` (``http.client``), so the branch was
    structurally unreachable no matter what the table held — and
    :class:`TestEveryCatalogueReceiverTypeIsMintable` above passed the whole
    time, because it asserts the TABLE holds a key and this is a RESOLUTION
    defect. Table coverage was never the same claim as reachability.

    WHY THE PARITY TEST ENUMERATES DEPTH RATHER THAN NAMING FOUR TYPES. The four
    affected types today (``http.client.HTTPConnection`` / ``HTTPSConnection``,
    ``http.server.HTTPServer``, ``xmlrpc.server.SimpleXMLRPCServer``) are an
    accident of what ``python.yaml`` currently declares. The defect is a depth
    assumption, so the assertion is over every catalogued type at every depth;
    adding a five-segment type tomorrow fails here rather than going quietly
    unreachable.

    THE REAL-CODE FLOOR, measured before building, because a generated reach
    probe is a ceiling and this project has mistaken one for a payoff three
    times. Across the 103 corpus files naming these types, counting only the
    JOINED shape that can actually mint a boundary (constructor assigned to a
    name, that name later the receiver of a CATALOGUED method):

        dotted  production 10     <- invisible today
        dotted  test       21     <- invisible today
        bare    production  4     <- works today
        bare    test       13     <- works today

    The dotted form is the MORE common of the two in real code for these types,
    not a long tail. The ten production sites are genuine ``net_send``:
    mitmproxy's two release scripts, envoy's ``socket_passing`` tool, ceph's
    barbican task, ClickHouse CI.

    NOT COUNTED, AND DELIBERATELY. ``django.db.models`` also sits at depth 2 and
    supplies 29 of the 41 primitives the reach probe attributed to this item —
    but it is a MODULE, not a constructible type, so ``models(...)`` constructs
    nothing and no fix can make those 29 reachable through a constructor. They
    already reach production by a different route entirely (the ``.objects.``
    dispatch, WI-sozoj: 447 ``filter`` + 121 ``get`` chains on pretix). The
    probe counted a call form that cannot exist; the honest scope is 12
    primitives across 4 types, not 41.
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
        """NON-DESTRUCTION. The depth-1 case is the branch being generalized, and
        it carries 13 of the 17 catalogued types — breaking it to fix 4 would be
        a net loss that a green suite on the new case would happily hide."""
        assert _resolve_ctor(
            "import smtplib\n\n\ndef send(a):\n    return smtplib.SMTP(a)\n"
        ) == "smtplib.SMTP"

    def test_an_alias_still_resolves(self) -> None:
        """``import http.client as hc`` binds ``hc`` directly and already worked;
        the generalization must not regress it."""
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

    def test_a_decoy_module_of_the_same_shape_mints_nothing(self) -> None:
        """``import mylib.client`` must not satisfy ``http.client.HTTPConnection``
        merely by ending in the same two segments — ``_module_matches`` is
        permissive by design and is not a guard."""
        assert _resolve_ctor(
            "import mylib.client\n\n\n"
            "def f(h):\n    return mylib.client.HTTPConnection(h)\n"
        ) is None

    @pytest.mark.xfail(strict=True, reason=(
        "KNOWN GAP, PRE-EXISTING AND WIDENED HERE — filed, not fixed in this PR. "
        "module_imports is built by an ast.walk over the WHOLE FILE, so it is "
        "file-scoped and knows nothing about a local binding that shadows an "
        "imported module name. This already mistyped the depth-1 form (a local "
        "`socket` parameter still resolves `socket.socket(h)` to the stdlib "
        "type); chain-unwinding extends the same exposure to depth >= 2. Marked "
        "strict so whoever makes it scope-aware gets a RED test rather than a "
        "silently-passing one they never notice."
    ))
    def test_a_local_shadow_should_not_be_typed(self) -> None:
        assert _resolve_ctor(
            "import http.client\n\n\n"
            "def f(http, h):\n    return http.client.HTTPConnection(h)\n"
        ) is None


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
        """mitmproxy's and envoy's exact shape, reduced: construct, then call."""
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
