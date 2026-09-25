# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nular (bash half): a primitive's KIND must match what it does.

The 0006 refutation pass found a family across five languages — a catalogue row
binds a NAME to a boundary kind and nothing ever checks that the named
primitive performs that boundary operation. Three of the reported instances are
bash's, and this module is the measured answer to each. TWO OF THE SIX REPORTED
BASH CLAIMS DO NOT REPRODUCE, and saying so is part of the fix:

  REPRODUCES — `> /dev/null` counts as a filesystem write. Measured on the
  shipped CLI before the fix: `echo "$API_KEY" > /dev/null` returns `violated`
  (rc 1) against a `{boundary: fs_write, must_not_exist: true}` claim. Nothing
  is written to any filesystem; the kernel discards the bytes.

  REPRODUCES — bash-maintained variables count as environment reads, so
  `$BASH_SOURCE`, `$RANDOM` and `$LINENO` each derive a `host_secret` taint
  SOURCE. The analyzer's own comment already claimed otherwise ("$PWD-style
  shell-maintained names are not secrets the caller supplied") while the code
  filtered only names whose first character is not a letter.

  DOES NOT REPRODUCE — "`>&2` counts". It does not: `_redirect_edge` returns
  None for fd duplication, and `echo hi >&2` emits no edge at all. (That is a
  RECALL gap — a write to stderr is a real `logging` crossing bash does not
  report — but it is the opposite of the over-report that was filed.)

  DOES NOT REPRODUCE — "`>>` is reported as `>`, losing append/truncate". The
  operators carry distinct dst ids (`redirect:0-0:>>` vs `redirect:0-0:>`) and
  distinct `io_mode` (`a` vs `w`), end to end into the `io-boundaries` map.
  `test_bash_redirection.py` has pinned it since INV-vavup.

THE RULE THIS DRAWS, and it is not "secret vs not secret" — that would be the
curated name list the env_read row exists to refuse. It is WHO SET THE
VARIABLE, which is INV-jurif's own discriminator applied one level out: a name
the SCRIPT assigns is not an environment read (already shipped), and a name
BASH assigns is not one either. Among the names bash assigns, those that
describe the host or the user (`HOSTNAME`, `OSTYPE`, `PWD`, `UID`) are
`host_info_read` on the INV-tutar precedent, and the rest are in-process shell
state that crosses no boundary at all. `$HOME` stays an env read: bash does not
set it, it inherits it.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.bash import analyze_bash


def _edges(tmp_path: Path, script: str):
    (tmp_path / "s.sh").write_text(script)
    return analyze_bash(tmp_path).edges


def _redirects(edges):
    return [e for e in edges
            if (e.meta or {}).get("io_primitive", "").startswith("redirect")]


def _env_dsts(edges):
    """The DISTINCT destinations — one edge per expansion, before dedup."""
    return sorted(
        {e.dst for e in edges if e.edge_type == "module_attr_ref"}
    )


def _env_vars(edges):
    return sorted(
        (e.meta or {}).get("env_var") for e in edges
        if e.edge_type == "module_attr_ref"
        and e.dst == "bash:env:0-0:env.environ:attribute"
    )


# ---------------------------------------------------------------------------
# Redirect target kind
# ---------------------------------------------------------------------------


def test_a_real_path_target_is_a_host_path(tmp_path):
    reds = _redirects(_edges(tmp_path, 'echo x > /etc/cron.d/pwned\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == ["host_path"]


def test_the_null_device_is_marked_as_such(tmp_path):
    reds = _redirects(_edges(tmp_path, 'echo x > /dev/null\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == [
        "null_device",
    ]


def test_reading_the_null_device_is_marked_too(tmp_path):
    """`< /dev/null` yields EOF; it is no more a filesystem read than a write."""
    reds = _redirects(_edges(tmp_path, 'cat < /dev/null\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == [
        "null_device",
    ]


def test_the_standard_streams_are_marked_distinctly(tmp_path):
    """Reported but NOT yet reclassified — see the module docstring."""
    reds = _redirects(_edges(tmp_path, 'echo x > /dev/stderr\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == [
        "std_stream",
    ]


def test_a_variable_target_is_unresolved_not_guessed(tmp_path):
    reds = _redirects(_edges(tmp_path, 'echo x > "$OUT"\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == [
        "unresolved",
    ]


def test_the_null_device_still_emits_an_edge(tmp_path):
    """ANALYZER EMITS, CATALOGUE CLASSIFIES: the redirect stays in the graph."""
    assert _redirects(_edges(tmp_path, 'echo x > /dev/null\n'))


def test_the_append_operator_is_still_distinguished(tmp_path):
    """The claim that did NOT reproduce, pinned so it cannot start to."""
    reds = _redirects(_edges(
        tmp_path, 'echo a > /tmp/one\necho b >> /tmp/two\n',
    ))
    assert sorted((e.meta or {}).get("io_mode") for e in reds) == ["a", "w"]
    assert sorted((e.meta or {}).get("io_primitive") for e in reds) == [
        "redirect.>", "redirect.>>",
    ]


def test_fd_duplication_still_emits_nothing(tmp_path):
    """The other claim that did NOT reproduce."""
    assert _redirects(_edges(tmp_path, 'echo hi >&2\necho hi 2>&1\n')) == []


# ---------------------------------------------------------------------------
# Who set the variable
# ---------------------------------------------------------------------------


def test_an_inherited_variable_is_still_an_environment_read(tmp_path):
    """The non-vacuity floor: this whole change must not silence real sources."""
    assert _env_vars(_edges(tmp_path, 'echo "$API_KEY"\n')) == ["API_KEY"]


def test_home_is_inherited_not_shell_set(tmp_path):
    """bash does not assign HOME; it is exactly the env read it looks like."""
    assert _env_vars(_edges(tmp_path, 'echo "$HOME"\n')) == ["HOME"]


def test_bash_source_is_not_an_environment_read(tmp_path):
    """The filed instance. BASH_SOURCE is bash's, and never exported."""
    assert _env_vars(_edges(tmp_path, 'echo "$BASH_SOURCE"\n')) == []


def test_in_process_shell_state_crosses_no_boundary(tmp_path):
    edges = _edges(
        tmp_path,
        'echo "$RANDOM $LINENO $FUNCNAME $PIPESTATUS $SECONDS $REPLY"\n',
    )
    assert [e for e in edges if e.edge_type == "module_attr_ref"] == []


def test_host_description_variables_are_not_host_secrets(tmp_path):
    """INV-tutar's split, one language over: OSTYPE is not a credential."""
    edges = _edges(tmp_path, 'echo "$HOSTNAME $OSTYPE $PWD $UID"\n')
    assert _env_vars(edges) == []
    assert _env_dsts(edges) == ["bash:shell:0-0:shell.hostinfo:attribute"]


def test_a_host_description_read_still_emits_an_edge(tmp_path):
    """Reclassified, not suppressed — reading the hostname IS a host read."""
    edges = _edges(tmp_path, 'echo "$HOSTNAME"\n')
    assert len([e for e in edges if e.edge_type == "module_attr_ref"]) == 1


def test_the_two_kinds_do_not_share_a_destination(tmp_path):
    """A secret read and a host-description read must stay tellable apart."""
    edges = _edges(tmp_path, 'echo "$API_KEY"\necho "$HOSTNAME"\n')
    assert _env_dsts(edges) == [
        "bash:env:0-0:env.environ:attribute",
        "bash:shell:0-0:shell.hostinfo:attribute",
    ]


def test_a_script_assigned_name_is_still_not_a_source(tmp_path):
    """INV-jurif's shipped discriminator is untouched."""
    assert _env_vars(_edges(tmp_path, 'FOO=1\necho "$FOO"\n')) == []


def test_an_fd_path_is_a_standard_stream_too(tmp_path):
    """`/dev/fd/N` names an already-open descriptor, not a place on disk."""
    reds = _redirects(_edges(tmp_path, 'echo x > /dev/fd/3\n'))
    assert [(e.meta or {}).get("io_target_kind") for e in reds] == [
        "std_stream",
    ]
