# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fatok: which row an abstaining call falls back to must be declarable.

WHAT WAS WRONG. A primitive declared under two boundaries with
``boundary_ruling: call_site_undecidable`` is narrowed at match time by the
stamped ``io_target_kind``. When the stamp ABSTAINS -- an unresolvable origin,
a struct field, a parameter -- ``_narrow_by_target_kind`` returns the candidate
list untouched and whatever selects afterwards takes the FIRST row. That is the
documented safety argument: an unstamped call classifies exactly as it did
before the seam existed.

WHICH ROW IS FIRST WAS NOT A PROPERTY OF THE CATALOGUE. ``_from_dict`` builds
its primitives by iterating ``CATALOG_BOUNDARY_TYPES``, a frozen module-level
list, so the fallback was that list's order -- fs_read (index 0) before
net_recv (3) before ipc_recv (4) -- for every language and every primitive,
regardless of how the YAML was written. Demonstrated rather than inferred:
moving go.yaml's entire ``fs_read:`` block below ``ipc_recv:``, with the parsed
content asserted byte-identical, did not move the loaded primitive indices.

WHY THAT IS A DEFECT AND NOT A CONVENTION. Which fallback is CONSERVATIVE is a
property of the PRIMITIVE, not of the vocabulary:

* c's ``fgets`` and haskell's ``hGetLine`` were fs_read-ONLY -- a false
  NEGATIVE, since fs_read mints no taint source and a read of stdin therefore
  minted nothing. Their conservative direction is toward NOT minting, so
  falling back to fs_read is right, and the registry order gives it to them.
* go's ``bufio.NewScanner`` is ipc_recv-ONLY -- a false POSITIVE, an
  untrusted_input source minted over every file read. Its conservative
  direction is the opposite one. Measured over six Go repositories, adding the
  fs_read row moves 51 rows, 46 correctly and 5 wrongly, and all five of the
  wrong ones have ``io_target_kind`` ABSENT: they moved because the fallback
  flipped, not because anything was inferred. Two read back against source --
  alertmanager cluster/tls_connection.go:136 wraps a TLS connection, caddy
  httpredirectlistener.go:90 wraps a net.Conn returned by Accept -- and each
  loses a TRUE untrusted_input source.

So the mechanism could express one direction and the corpus needs both.

WHAT ``abstains_to`` DOES. It names, per row, the boundary an UNSTAMPED call
falls back to, by reordering that primitive's rows at load time so the named
one is first. Absent, the registry order stands, so every row shipped before
this field behaves identically -- the default is today's behaviour, not a new
one.

IT IS VALIDATED RATHER THAN TRUSTED, because all three failure modes are
silent. A misspelled boundary, a boundary the primitive does not actually
declare, and two rows of one primitive naming different targets would each
leave the catalogue reading as though a fallback had been chosen while the
registry order quietly decided it -- which is the exact defect this field
exists to remove.
"""
import pytest

from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    select_by_mode,
)


def _catalog(data: dict) -> IoBoundaryCatalog:
    base = {"language": "probe", "status": "in_progress"}
    base.update(data)
    return IoBoundaryCatalog._from_dict(base)


def _order_for(cat: IoBoundaryCatalog, name: str) -> list[str]:
    """The boundaries of *name*'s rows, in the order the catalogue holds them."""
    return [p.boundary for p in cat.primitives if p.name == name]


UNDECIDABLE = "call_site_undecidable"


class TestTheDeclarationDecidesTheFallback:
    """The whole point: a per-row choice overriding the registry order."""

    def test_without_a_declaration_the_registry_order_stands(self) -> None:
        """Every row shipped before this field must behave identically."""
        cat = _catalog({
            "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                         "boundary_ruling": UNDECIDABLE}],
            "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"],
                          "boundary_ruling": UNDECIDABLE}],
        })
        assert _order_for(cat, "NewScanner") == ["fs_read", "ipc_recv"]

    def test_a_declaration_moves_its_row_first(self) -> None:
        cat = _catalog({
            "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                         "boundary_ruling": UNDECIDABLE,
                         "abstains_to": "ipc_recv"}],
            "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"],
                          "boundary_ruling": UNDECIDABLE,
                          "abstains_to": "ipc_recv"}],
        })
        assert _order_for(cat, "NewScanner") == ["ipc_recv", "fs_read"]

    def test_it_may_be_declared_on_only_one_of_the_rows(self) -> None:
        """The rows live in different YAML sections by construction.

        Requiring both to repeat it would be a second home for one fact, which
        is the hazard ``simultaneous`` records for the same shape.
        """
        cat = _catalog({
            "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                         "boundary_ruling": UNDECIDABLE}],
            "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"],
                          "boundary_ruling": UNDECIDABLE,
                          "abstains_to": "ipc_recv"}],
        })
        assert _order_for(cat, "NewScanner") == ["ipc_recv", "fs_read"]

    def test_three_rows_keep_their_relative_order_behind_the_named_one(
        self,
    ) -> None:
        """Only the named row moves; the rest are not reshuffled."""
        cat = _catalog({
            "fs_read": [{"module": "unistd", "functions": ["read"],
                         "boundary_ruling": UNDECIDABLE}],
            "net_recv": [{"module": "unistd", "functions": ["read"],
                          "boundary_ruling": UNDECIDABLE}],
            "ipc_recv": [{"module": "unistd", "functions": ["read"],
                          "boundary_ruling": UNDECIDABLE,
                          "abstains_to": "ipc_recv"}],
        })
        assert _order_for(cat, "read") == ["ipc_recv", "fs_read", "net_recv"]

    def test_a_different_primitive_is_untouched(self) -> None:
        cat = _catalog({
            "fs_read": [
                {"module": "bufio", "functions": ["NewScanner"],
                 "boundary_ruling": UNDECIDABLE, "abstains_to": "ipc_recv"},
                {"module": "os", "functions": ["Open"]},
            ],
            "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"],
                          "boundary_ruling": UNDECIDABLE}],
        })
        assert _order_for(cat, "Open") == ["fs_read"]

    def test_methods_and_attributes_are_reordered_too(self) -> None:
        """Kind is part of the primitive's identity, not a carve-out."""
        cat = _catalog({
            "fs_read": [{"module": "T", "methods": ["r"],
                         "boundary_ruling": UNDECIDABLE}],
            "ipc_recv": [{"module": "T", "methods": ["r"],
                          "boundary_ruling": UNDECIDABLE,
                          "abstains_to": "ipc_recv"}],
        })
        assert _order_for(cat, "r") == ["ipc_recv", "fs_read"]


class TestTheDeclarationIsValidatedRatherThanTrusted:
    """Every failure mode here is otherwise SILENT."""

    def test_an_unknown_boundary_is_refused(self) -> None:
        with pytest.raises(ValueError, match="abstains_to"):
            _catalog({
                "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                             "abstains_to": "ipc_recieve"}],
            })

    def test_a_boundary_the_primitive_does_not_declare_is_refused(self) -> None:
        """The trap this field exists to remove, wearing a new hat.

        Naming a boundary with no row would leave the registry order deciding
        while the catalogue reads as though a fallback had been chosen.
        """
        with pytest.raises(ValueError, match="does not declare"):
            _catalog({
                "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                             "abstains_to": "net_recv"}],
                "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"]}],
            })

    def test_rows_that_disagree_are_refused(self) -> None:
        """A marker live or inert depending on which section a later editor
        touched is the row-order hazard again."""
        with pytest.raises(ValueError, match="disagree"):
            _catalog({
                "fs_read": [{"module": "bufio", "functions": ["NewScanner"],
                             "abstains_to": "fs_read"}],
                "ipc_recv": [{"module": "bufio", "functions": ["NewScanner"],
                              "abstains_to": "ipc_recv"}],
            })

    def test_a_single_boundary_primitive_is_refused(self) -> None:
        """One row is not a choice; declaring a fallback claims one exists."""
        with pytest.raises(ValueError, match=r"does not declare|single"):
            _catalog({
                "fs_read": [{"module": "os", "functions": ["Open"],
                             "abstains_to": "ipc_recv"}],
            })


class TestItOutranksTheModeDefaultButNotModeEvidence:
    """Reordering the list alone is NOT enough, and this is why.

    ``resolve_mode_boundary_across_sites`` answers ``fs_read`` for an ABSENT
    mode -- deliberately, since guessing ``fs_write`` from ignorance would
    re-create the false positives the mode gate exists to remove. So
    ``select_by_mode`` selects whatever ``fs_read`` row is present regardless of
    where it sits in the candidate list, and a declaration expressed only as
    order is inert. Measured: go's bufio rows reordered so ipc_recv came first,
    and an unstamped call still classified ``fs_read``.

    That default is right for ``builtins.open``, whose mode IS the
    discriminator. It is a category error for a primitive with no mode argument
    at all, which is discriminated by its stream argument.
    """

    def _rows(self, **kw: object) -> list[IoPrimitive]:
        return [
            IoPrimitive("fs_read", "bufio", "NewScanner", "function", **kw),
            IoPrimitive("ipc_recv", "bufio", "NewScanner", "function", **kw),
        ]

    def test_without_a_declaration_the_mode_default_still_wins(self) -> None:
        """The pre-existing behaviour, pinned so the change is visible."""
        assert select_by_mode(self._rows(), None).boundary == "fs_read"

    def test_a_declaration_beats_the_mode_default(self) -> None:
        rows = self._rows(abstains_to="ipc_recv")
        assert select_by_mode(rows, None).boundary == "ipc_recv"

    def test_positive_mode_evidence_still_decides(self) -> None:
        """It outranks the DEFAULT only. A mode read off the source wins."""
        rows = [
            IoPrimitive("fs_read", "builtins", "open", "function",
                        abstains_to="fs_read"),
            IoPrimitive("fs_write", "builtins", "open", "function",
                        abstains_to="fs_read"),
        ]
        assert select_by_mode(rows, ("w",)).boundary == "fs_write"

    def test_builtins_open_is_untouched(self) -> None:
        """It declares no target, so the mode default decides as it always did."""
        rows = [
            IoPrimitive("fs_read", "builtins", "open", "function"),
            IoPrimitive("fs_write", "builtins", "open", "function"),
        ]
        assert select_by_mode(rows, None).boundary == "fs_read"
        assert select_by_mode(rows, ("w",)).boundary == "fs_write"


class TestEndToEndThroughTheProductionClassifier:
    """A predicate is inert until every call site passes it."""

    def _go(self, meta: object) -> object:
        from hypergumbo_core.io_boundary import (
            classify_call_in_catalog, load_catalog,
        )
        prim, _ = classify_call_in_catalog(
            {"go": load_catalog("go")},
            "go:bufio:0-0:NewScanner:external_symbol", meta,
        )
        return prim.boundary if prim else None

    def test_an_unstamped_scanner_keeps_todays_answer(self) -> None:
        assert self._go(None) == "ipc_recv"

    def test_a_file_handle_selects_fs_read(self) -> None:
        assert self._go({"io_target_kind": "host_path"}) == "fs_read"

    def test_stdin_selects_ipc_recv(self) -> None:
        assert self._go({"io_target_kind": "std_stream"}) == "ipc_recv"

    def test_c_fgets_is_unchanged_in_every_arm(self) -> None:
        """It declares no target, so none of this reaches it."""
        from hypergumbo_core.io_boundary import (
            classify_call_in_catalog, load_catalog,
        )
        cats = {"c": load_catalog("c")}
        dst = "c:external:0-0:fgets:external_symbol"
        got = [
            classify_call_in_catalog(cats, dst, m)[0].boundary
            for m in (None, {"io_target_kind": "std_stream"},
                      {"io_target_kind": "host_path"})
        ]
        assert got == ["fs_read", "ipc_recv", "fs_read"]


class TestTheShippedCataloguesAreUnchanged:
    """Forward-only: no existing row acquires a fallback by accident."""

    def test_c_stdio_fgets_still_falls_back_to_fs_read(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        assert _order_for(load_catalog("c"), "fgets")[0] == "fs_read"

    def test_haskell_hgetline_still_falls_back_to_fs_read(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        assert _order_for(load_catalog("haskell"), "hGetLine")[0] == "fs_read"


#: WI-vutav's nine read rows, one binding after go's two wrappers.
_BUFIO_READ_ROWS = (
    ("bufio.Reader", "ReadString"), ("bufio.Reader", "ReadBytes"),
    ("bufio.Reader", "ReadLine"), ("bufio.Reader", "ReadRune"),
    ("bufio.Reader", "ReadSlice"), ("bufio.Reader", "Read"),
    ("bufio.Scanner", "Scan"), ("bufio.Scanner", "Text"),
    ("bufio.Scanner", "Bytes"),
)


class TestTheBufioReadRowsFallBackTheOtherWay:
    """WI-vutav: the READ one binding after the wrapper.

    The constructor rows fall back to ``ipc_recv`` (five network reads would
    otherwise be lost -- the measurement in this file's docstring). The read
    rows fall back to ``fs_read``, and the asymmetry is the point: an
    unstamped read is one whose receiver's origin the analyzer could not see
    (a parameter, a struct field), and the wrapper that built that handle sits
    in some OTHER scope, where its own row already carries the unresolved
    case's ``ipc_recv`` fallback. Falling back to ``ipc_recv`` here too would
    mint a second ``untrusted_input`` source on one crossing; ``fs_read``
    mints nothing and leaves the crossing where it already is. A stamped read
    narrows exactly as the wrapper does.
    """

    @staticmethod
    def _go(name: str, meta: dict[str, object] | None) -> str | None:
        from hypergumbo_core.io_boundary import (
            classify_call_in_catalog, load_catalog,
        )
        stamped: dict[str, object] = {"call_construct": "method"}
        stamped.update(meta or {})
        prim, _ = classify_call_in_catalog(
            {"go": load_catalog("go")},
            f"go:bufio:0-0:{name}:unresolved", stamped,
        )
        return prim.boundary if prim else None

    @pytest.mark.parametrize("module,name", _BUFIO_READ_ROWS)
    def test_each_read_is_dual_undecidable_and_falls_back_to_fs_read(
        self, module: str, name: str,
    ) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == module and p.name == name
        ]
        assert {r.boundary for r in rows} == {"fs_read", "ipc_recv"}
        assert {r.boundary_ruling for r in rows} == {"call_site_undecidable"}
        assert {r.abstains_to for r in rows} == {"fs_read"}
        assert {r.kind for r in rows} == {"method"}

    @pytest.mark.parametrize("module,name", _BUFIO_READ_ROWS)
    def test_an_unstamped_read_mints_nothing(self, module: str, name: str) -> None:
        assert self._go(name, None) == "fs_read"

    @pytest.mark.parametrize("name", ["ReadString", "Scan", "Text"])
    def test_a_stdin_stamp_selects_ipc_recv(self, name: str) -> None:
        assert self._go(name, {"io_target_kind": "std_stream"}) == "ipc_recv"

    @pytest.mark.parametrize("name", ["ReadString", "Scan", "Text"])
    def test_a_file_stamp_selects_fs_read(self, name: str) -> None:
        assert self._go(name, {"io_target_kind": "host_path"}) == "fs_read"

    def test_the_wrappers_own_fallback_is_untouched(self) -> None:
        """The asymmetry is between two primitives, not a change to one."""
        from hypergumbo_core.io_boundary import (
            classify_call_in_catalog, load_catalog,
        )
        prim, _ = classify_call_in_catalog(
            {"go": load_catalog("go")},
            "go:bufio:0-0:NewReader:external_symbol", None,
        )
        assert prim is not None and prim.boundary == "ipc_recv"
