# SPDX-License-Identifier: AGPL-3.0-or-later
"""A DNS lookup is ``net_recv`` in every language that has one (WI-dozul).

THE RULING AND WHY IT WENT THIS WAY. ADR-0049 asks one question of a network
call: does it return -- or write into a caller-visible location -- a value
whose content the FAR SIDE chose? A resolver's answer is chosen by the
resolver, so a lookup is a receive and mints ``untrusted_input``. The owner
ruled that on 2026-09-06, and the argument that decided it was not the ADR in
the abstract: **erlang had already rowed** ``inet:getaddr`` / ``getaddrs`` /
``gethostbyname`` / ``gethostbyaddr`` under ``net_recv`` since 2026-05-24
(WI-tukif), with the note "DNS resolution and service-name lookups (queries
name servers)". So the real choice was parity-with-erlang versus retag-erlang,
and WI-pavob rules that a vocabulary meaning different things per language is
exactly the failure the axis discipline exists to prevent.

THE ITEM'S OWN PREMISE WAS WRONG, AND THAT IS THE LESSON WORTH KEEPING. It
said "no catalogue rows any of them today (grep: zero rows across
go/rust/c/python)" -- true of the four catalogues it grepped, and the answer
was sitting in a fifth. A one-of-N instrument hides N-1.

WHY A PARITY TEST RATHER THAN PER-LANGUAGE ASSERTIONS. The defect this file
guards is not "python is missing a row"; it is the two catalogues disagreeing
about what a lookup IS. A per-language test passes while the disagreement
grows. This one fails the moment a language rows a lookup somewhere else.

WHAT IS DELIBERATELY NOT HERE. ``gethostname`` is the LOCAL hostname -- it
queries no name server -- and belongs under ``host_info_read``, where c has
filed it all along. ``getservbyname`` / ``getservbyport`` read the local
services table. Erlang had all three under ``net_recv``, and moving them is a
REMOVAL, which under ADR-0049 ruling 3 is proved rather than reasoned; see
:class:`TestTheLocalLookupsAreNotNetworkReceives`.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog


#: The lookups that QUERY A NAME SERVER, per language: (language, module,
#: name). Every one returns a resolver-chosen answer, so every one is
#: ``net_recv``. Kept as data so the parity test below is an enumeration and
#: not a pile of hand-written asserts.
DNS_LOOKUPS: tuple[tuple[str, str, str], ...] = (
    # python -- socket's module-level resolver surface.
    ("python", "socket", "gethostbyname"),
    ("python", "socket", "gethostbyname_ex"),
    ("python", "socket", "gethostbyaddr"),
    ("python", "socket", "getaddrinfo"),
    ("python", "socket", "getnameinfo"),
    ("python", "socket", "getfqdn"),
    # go -- package-level helpers.
    ("go", "net", "LookupHost"),
    ("go", "net", "LookupIP"),
    ("go", "net", "LookupAddr"),
    ("go", "net", "LookupCNAME"),
    ("go", "net", "LookupMX"),
    ("go", "net", "LookupNS"),
    ("go", "net", "LookupTXT"),
    ("go", "net", "LookupSRV"),
    # c -- netdb.
    ("c", "netdb", "getaddrinfo"),
    ("c", "netdb", "getnameinfo"),
    ("c", "netdb", "gethostbyname"),
    ("c", "netdb", "gethostbyaddr"),
    # javascript -- the dns module, which WI-nolut left unaudited.
    ("javascript", "dns", "lookup"),
    ("javascript", "dns", "resolve"),
    ("javascript", "dns", "resolve4"),
    ("javascript", "dns", "reverse"),
    # erlang -- rowed since 2026-05-24, and the reason the others look like
    # this. Asserted here so the parity claim is not "the four new languages
    # agree with each other".
    ("erlang", "inet", "getaddr"),
    ("erlang", "inet", "gethostbyname"),
    ("erlang", "inet", "gethostbyaddr"),
)


def _boundaries(language: str, module: str, name: str) -> set[str]:
    return {
        p.boundary
        for p in load_catalog(language, include_defaults=False).primitives
        if p.module == module and p.name == name
    }


@pytest.mark.parametrize(("language", "module", "name"), DNS_LOOKUPS)
def test_a_name_server_query_is_a_network_receive(
    language: str, module: str, name: str,
) -> None:
    """THE PARITY CLAIM, one row per member."""
    assert "net_recv" in _boundaries(language, module, name), (
        f"{language}: {module}.{name} does not carry net_recv. A resolver's "
        f"answer is content the far side chose (ADR-0049), and erlang has "
        f"rowed exactly this since 2026-05-24 -- a vocabulary that means "
        f"different things per language is the failure WI-pavob names."
    )


def test_every_parity_language_is_actually_represented() -> None:
    """A parity table that lost a language would still pass every row above.

    Five languages, because WI-nolut left javascript's ``dns`` unaudited and
    this is where that gap closes.
    """
    languages = {lang for lang, _, _ in DNS_LOOKUPS}
    assert languages == {"python", "go", "c", "javascript", "erlang"}


def test_the_lookups_mint_untrusted_input() -> None:
    """WHY THE BOUNDARY CHOICE HAS A CONSEQUENCE, asserted rather than
    assumed: ``net_recv`` is in ``AUTO_SOURCE_LABEL_MAP``, so every row above
    creates a taint source. If it were not, this whole change would be
    bookkeeping and the 0003 delta would be structurally zero."""
    from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP

    assert AUTO_SOURCE_LABEL_MAP.get("net_recv") == "untrusted_input"


class TestReachWasMeasuredNotAssumed:
    """PRESENCE IS NOT REACHABILITY, and the javascript half of this change is
    where that bit.

    Three claims here, each MEASURED end to end on a fixture rather than read
    off the row, because a row that classifies nothing looks identical to one
    that classifies everything until you run it.

    ==================================== ==========================
    source spelling                      emitted module slot
    ==================================== ==========================
    ``require('dns/promises')``          ``dns/promises``
    ``import .. 'node:dns/promises'``    ``dns/promises``
    ``require('dns').promises``          ``external`` (sentinel)
    ``import {promises} from 'dns'``     ``external`` (sentinel)
    ``new Resolver(); r.resolve4(h)``    ``external`` (sentinel)
    ==================================== ==========================

    A DETOUR WORTH RECORDING, because it is this project's own rule 5 landing
    on me. The member-access fixture produced no chain, I concluded the rows
    were mis-keyed, and re-keyed them to the slash spelling. That was wrong:
    ``normalize_module_separators`` folds ``/`` into ``.`` before matching, so the dotted
    key already caught both subpath spellings, and the fixture had failed for a
    DIFFERENT reason -- the sentinel. A fixture that fails for reason B while
    you are testing hypothesis A will happily confirm A. The revert is in the
    history; the lesson is that the second fixture (``require('dns/promises')``)
    is what distinguishes the two causes, and it is the one that should have
    been run first.
    """

    def test_the_promise_rows_use_the_same_spelling_as_their_fs_sibling(
        self,
    ) -> None:
        """Dotted, like ``fs.promises``. The ``/`` folds to ``.`` at match
        time, so the dotted key catches ``require('dns/promises')`` and renders
        consistently; a slash key would work but would be the only one of its
        kind in the file."""
        from hypergumbo_core.io_boundary import normalize_module_separators

        catalog = load_catalog("javascript", include_defaults=False)
        rows = {p.name for p in catalog.primitives if p.module == "dns.promises"}
        assert "resolve4" in rows
        assert normalize_module_separators("dns/promises") == "dns.promises"
        assert {p.module for p in catalog.primitives
                if p.module.startswith("fs.prom")} == {"fs.promises"}

    def test_the_callback_surface_is_the_reachable_one(self) -> None:
        """The plain ``dns`` rows classify -- verified on a fixture where
        ``dns.resolve4(h, cb)`` produced a ``net_recv`` chain carrying
        ``primitive: dns.resolve4``. These are the javascript rows this change
        counts as coverage."""
        catalog = load_catalog("javascript", include_defaults=False)
        plain = {p.name for p in catalog.primitives if p.module == "dns"}
        assert {"lookup", "resolve", "resolve4", "reverse"} <= plain

    def test_the_resolver_class_rows_declare_themselves_inert(self) -> None:
        """``new Resolver()`` binds a local the analyzer does not type, so
        ``r.resolve4(h)`` emits the external sentinel and reaches nothing.
        Measured, not assumed. The rows stay -- an inert row costs no precision
        and claims no coverage -- but they must SAY they are inert, the rust
        ``ToSocketAddrs`` treatment, or a reader counting rows counts them as
        coverage."""
        catalog = load_catalog("javascript", include_defaults=False)
        rows = [p for p in catalog.primitives if p.module == "dns.Resolver"]
        assert rows
        assert all("INERT" in p.notes for p in rows)


class TestTheLocalLookupsAreNotNetworkReceives:
    """The other half of the sort: three erlang rows that never queried a name
    server, moved out of ``net_recv`` in the same pass.

    ``inet:gethostname`` returns the LOCAL host's name from the kernel -- c
    files ``unistd.gethostname`` under ``host_info_read`` and always has.
    ``getservbyname`` / ``getservbyport`` read ``/etc/services``, a local
    table. All three sat under ``net_recv`` carrying a note about querying
    name servers, which is true of the four rows beside them and false of
    these.
    """

    @pytest.mark.parametrize("language", ["erlang", "elixir"])
    def test_the_local_hostname_is_host_info_not_a_receive(
        self, language: str,
    ) -> None:
        boundaries = _boundaries(language, "inet", "gethostname")
        assert "net_recv" not in boundaries
        assert boundaries == {"host_info_read"}, boundaries

    @pytest.mark.parametrize("language", ["erlang", "elixir"])
    @pytest.mark.parametrize("name", ["getservbyname", "getservbyport"])
    def test_the_services_table_is_not_a_name_server(
        self, language: str, name: str,
    ) -> None:
        assert "net_recv" not in _boundaries(language, "inet", name)

    def test_c_already_filed_the_local_hostname_this_way(self) -> None:
        """The precedent, pinned. If c ever moves ``gethostname`` to
        ``net_recv`` the sort above becomes the inconsistent one."""
        assert _boundaries("c", "unistd", "gethostname") == {"host_info_read"}

    @pytest.mark.parametrize("language", ["erlang", "elixir"])
    def test_the_real_resolver_rows_are_untouched(self, language: str) -> None:
        """CONTROL. The sort must not take the four rows it is sorting away
        from -- otherwise it is a deletion wearing a reclassification's
        clothes."""
        for name in ("getaddr", "getaddrs", "gethostbyname", "gethostbyaddr"):
            assert "net_recv" in _boundaries(language, "inet", name), name
