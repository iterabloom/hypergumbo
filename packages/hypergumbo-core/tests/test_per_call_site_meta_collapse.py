# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vukiv: a collapsed edge must not present one site's fact as the whole.

``deduplicate_edges`` keeps ONE edge per ``(src, dst, edge_type)`` and unions
the collapsed call sites into ``meta["call_lines"]``. Everything else on the
survivor is the FIRST edge's meta, verbatim — so any key whose value varies
per call site reported an arbitrary site's value with nothing to mark it as
partial.

MEASURED, three keys, and the third is a security property rather than a
tidiness one:

  bash ``redirect_target`` — ``echo a > /dev/null`` followed by
  ``echo "$API_KEY" > /etc/cron.d/pwned`` in one function survives as ONE edge
  reading ``redirect_target='/dev/null'``. The cron-dropper write is reported
  as a write to the bit bucket.

  bash ``env_var`` — ``$HOME`` then ``$API_KEY`` survives as ``env_var='HOME'``.
  The secret read disappears and the map names the harmless one.

  python ``io_mode`` — ``open(p,'r')`` then ``open(p,'w')`` survives as
  ``io_mode='r'``, and the mode gate then eliminates the ``fs_write`` row.
  Measured end-to-end on the shipped CLI with a control in the same run::

      def truncate_logs(p):        def truncate_logs(p):
          fh = open(p, 'r')            open(p, 'w').close()
          fh.close()
          open(p, 'w').close()

      io-boundaries: fs_read       io-boundaries: fs_write

  Same truncating write; adding a READ above it deletes it from the boundary
  map outright. A ``must_not_exist: fs_write`` claim confirms on the left.

THE CONTRACT, mirroring ``call_lines``: the singular key survives ONLY if every
collapsed site agreed on it. When sites disagree the singular key is REMOVED —
so no consumer can read a partial fact as a total one — and the distinct values
move to ``<key>_values``. Absence of ``<key>_values`` means "every site agreed",
exactly as absence of ``call_lines`` means "exactly one site".

The mechanism is a generalization, not an invention: ``call_arg_shape`` has
carried precisely this "may only survive if it holds of EVERY collapsed site"
rule since INV-fubag, hardcoded for one key. What was missing was a way to
DECLARE a second one — hence ``MetaKeySpec.per_call_site``.
"""

from hypergumbo_core.axis_meta_keys import META_KEYS, per_call_site_keys
from hypergumbo_core.ir import Edge, deduplicate_edges


def _edge(line: int, **meta):
    return Edge.create(
        src="bash:s.sh:1-9:f:function",
        dst="bash:redirect:0-0:>:unresolved",
        edge_type="calls",
        line=line,
        origin="bash",
        origin_run_id="run-inv-vukiv",
        evidence_type="ast_call",
        meta=dict(meta),
    )


# ---------------------------------------------------------------------------
# The registry declaration
# ---------------------------------------------------------------------------


def test_the_per_call_site_keys_are_declared_in_the_registry():
    """A key nobody declared cannot be unioned, which is the root cause."""
    declared = per_call_site_keys()
    assert {"io_mode", "redirect_target", "env_var",
            "call_arg_shape"} <= declared, sorted(declared)


def test_every_declared_per_call_site_key_is_an_edge_meta_key():
    """``Symbol.meta`` has no call sites, so the flag would be meaningless."""
    for spec in META_KEYS:
        if spec.per_call_site:
            assert spec.axis == "edge_meta", spec.name


# ---------------------------------------------------------------------------
# The collapse contract
# ---------------------------------------------------------------------------


def test_agreeing_sites_keep_the_singular_key_and_add_no_values_list():
    """The overwhelming majority case must not grow a key."""
    kept, = deduplicate_edges([
        _edge(3, redirect_target="/var/log/app.log"),
        _edge(4, redirect_target="/var/log/app.log"),
    ])
    assert kept.meta["redirect_target"] == "/var/log/app.log"
    assert "redirect_target_values" not in kept.meta
    assert kept.meta["call_lines"] == [3, 4]


def test_disagreeing_sites_drop_the_singular_key():
    """The cron-dropper: no consumer may read '/dev/null' as the answer."""
    kept, = deduplicate_edges([
        _edge(3, redirect_target="/dev/null"),
        _edge(4, redirect_target="/etc/cron.d/pwned"),
    ])
    assert "redirect_target" not in kept.meta


def test_disagreeing_sites_preserve_every_distinct_value():
    kept, = deduplicate_edges([
        _edge(3, redirect_target="/dev/null"),
        _edge(4, redirect_target="/etc/cron.d/pwned"),
    ])
    assert kept.meta["redirect_target_values"] == [
        "/dev/null", "/etc/cron.d/pwned",
    ]


def test_a_site_that_omits_the_key_is_itself_a_disagreement():
    """Adopting the one site that HAS a value would state it of all of them."""
    kept, = deduplicate_edges([
        _edge(3, redirect_target="/etc/cron.d/pwned"),
        _edge(4),
    ])
    assert "redirect_target" not in kept.meta
    assert kept.meta["redirect_target_values"] == ["/etc/cron.d/pwned"]


def test_the_absent_first_site_direction_is_covered_too():
    """Encounter order must not decide the verdict."""
    kept, = deduplicate_edges([
        _edge(3),
        _edge(4, redirect_target="/etc/cron.d/pwned"),
    ])
    assert "redirect_target" not in kept.meta
    assert kept.meta["redirect_target_values"] == ["/etc/cron.d/pwned"]


def test_three_sites_union_rather_than_pairwise_forget():
    kept, = deduplicate_edges([
        _edge(3, redirect_target="a"),
        _edge(4, redirect_target="b"),
        _edge(5, redirect_target="c"),
    ])
    assert kept.meta["redirect_target_values"] == ["a", "b", "c"]


def test_a_key_that_is_not_declared_per_call_site_is_untouched():
    """The flag is opt-in; an undeclared key keeps its historical behaviour."""
    kept, = deduplicate_edges([
        _edge(3, evidence_note="first"),
        _edge(4, evidence_note="second"),
    ])
    assert kept.meta["evidence_note"] == "first"
    assert "evidence_note_values" not in kept.meta


def test_a_single_site_edge_gains_nothing():
    kept, = deduplicate_edges([_edge(3, redirect_target="/tmp/x")])
    assert kept.meta["redirect_target"] == "/tmp/x"
    assert "redirect_target_values" not in kept.meta
    assert "call_lines" not in kept.meta


def test_non_string_per_site_values_survive_the_union():
    """``redirect_target_resolved`` is a bool; sorting must not explode."""
    kept, = deduplicate_edges([
        _edge(3, redirect_target_resolved=True),
        _edge(4, redirect_target_resolved=False),
    ])
    assert "redirect_target_resolved" not in kept.meta
    assert sorted(map(str, kept.meta["redirect_target_resolved_values"])) == [
        "False", "True",
    ]


def test_call_arg_shape_keeps_its_inv_fubag_behaviour():
    """The hardcoded rule this generalizes: a disagreeing proof is no proof."""
    kept, = deduplicate_edges([
        _edge(3, call_arg_shape="literal_only"),
        _edge(4),
    ])
    assert "call_arg_shape" not in kept.meta


def test_an_edge_with_no_meta_at_all_collapses_without_error():
    kept, = deduplicate_edges([_edge(3), _edge(4)])
    assert kept.meta["call_lines"] == [3, 4]
