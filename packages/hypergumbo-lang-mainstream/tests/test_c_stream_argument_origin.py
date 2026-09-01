# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C stdio read's boundary is a property of its STREAM argument.

WI-lipis's third deliverable, for C. ``c.yaml`` files ``fgets`` / ``fscanf`` /
``fread`` / ``getc`` / ``fgetc`` as ``fs_read`` and says so deliberately: their
boundary "is a property of the argument (INV-bagok / INV-zumin class (b)), and
moving them would assert an IPC crossing for every read over a file". That is
the right DEFAULT and the wrong ANSWER whenever the stream is ``stdin``.

WHY THIS DIRECTION IS THE OPPOSITE OF GO'S, which matters for how it is judged.
Go's ``bufio.NewScanner`` was filed ``ipc_recv``, which MINTS ``untrusted_input``
via ``AUTO_SOURCE_LABEL_MAP``, so a wrong argument INVENTED a taint source and
the fix removed false positives. ``fs_read`` is absent from that map by design,
so a wrong argument here MISSES a source instead. Measured on a three-function
repro through the production CLI, with the control in the same run::

    control_scanf   scanf(...)             ipc_recv   -> violated, 1 row
    from_stdin      fgets(buf, n, stdin)   fs_read    -> NO ROW
    from_pipe       fgets over popen()     fs_read    -> NO ROW

Identical flow shape, identical sink (``fputs`` into an ``fopen``ed file); the
only difference is whether the catalogue could see the stream. So this is a
FALSE NEGATIVE, and a false negative is the expensive direction for a security
tool.

CORPUS POPULATION: 2114 stream-taking read sites across 67 repositories.

``popen`` IS DELIBERATELY NOT ANSWERED HERE. Its ``FILE*`` is a pipe from a
child process, which is neither ``host_path`` nor ``std_stream``; naming it
would need a new ``io_target_kind`` value and the four-step registry chain that
goes with one. 64 of the 2114 sites (3.0%). Filed separately -- it is also
WI-kanor's family, a handle-returning launch whose crossing is unrepresented.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.c import analyze_c

READS = ("fgets", "fscanf", "fread", "getc", "fgetc")


def _kind(tmp_path: Path, source: str, callee: str) -> object:
    """``io_target_kind`` stamped on the call edge for *callee*, or None.

    Keyed on the CALLEE rather than on a boundary tag, following
    ``test_c_io_mode_emission``: a consumer-side regression must not surface
    here as "the analyzer emitted nothing".
    """
    (tmp_path / "main.c").write_text(source)
    edges = [e for e in analyze_c(tmp_path).edges
             if e.edge_type == "calls" and f":{callee}:" in e.dst]
    assert len(edges) == 1, [e.dst for e in edges]
    return (edges[0].meta or {}).get("io_target_kind")


class TestAStandardStreamIsRecognised:
    def test_fgets_over_stdin_is_a_std_stream(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { char b[8]; fgets(b, 8, stdin); }\n',
                     "fgets") == "std_stream"

    def test_fscanf_takes_its_stream_first(self, tmp_path):
        # The argument index differs per function and getting it wrong reads
        # some other argument as the stream.
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { int x; fscanf(stdin, "%d", &x); }\n',
                     "fscanf") == "std_stream"

    def test_fread_takes_its_stream_fourth(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { char b[8]; fread(b, 1, 8, stdin); }\n',
                     "fread") == "std_stream"

    def test_getc_takes_its_stream_first(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { int c = getc(stdin); (void)c; }\n',
                     "getc") == "std_stream"

    def test_fgetc_takes_its_stream_first(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { int c = fgetc(stdin); (void)c; }\n',
                     "fgetc") == "std_stream"


class TestAFileHandleIsRecognised:
    """The control that separates a fix from a blanket flip: without it,
    stamping ``std_stream`` unconditionally would pass every test above and
    assert an IPC crossing for every read over a file -- precisely what
    ``c.yaml``'s note says must not happen."""

    def test_a_binding_from_fopen_is_a_host_path(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(const char *p) {\n'
                     '    char b[8];\n'
                     '    FILE *h = fopen(p, "r");\n'
                     '    fgets(b, 8, h);\n'
                     '}\n', "fgets") == "host_path"

    def test_an_inline_fopen_is_a_host_path(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(const char *p) { char b[8];'
                     ' fgets(b, 8, fopen(p, "r")); }\n',
                     "fgets") == "host_path"


class TestUnprovableOriginsStampNothing:
    """Absence is recorded as absence. ``read_boundary_for_target_kind``
    returns ``known=False`` for an unstamped edge and the catalogue row
    decides, which is the only safe default in a direction that MOVES a
    boundary."""

    def test_a_parameter_stream_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(FILE *h) { char b[8]; fgets(b, 8, h); }\n',
                     "fgets") is None

    def test_a_popen_pipe_stamps_nothing_for_now(self, tmp_path):
        # DELIBERATE, not an oversight: a child-process pipe is neither a
        # host path nor a standard stream, and inventing a kind for it here
        # would put a value in the vocabulary without the registry work.
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) {\n'
                     '    char b[8];\n'
                     '    FILE *p = popen("ls", "r");\n'
                     '    fgets(b, 8, p);\n'
                     '}\n', "fgets") is None

    def test_a_stream_from_an_unknown_call_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'FILE *get_stream(void);\n'
                     'void f(void) { char b[8]; fgets(b, 8, get_stream()); }\n',
                     "fgets") is None

    def test_a_call_with_too_few_arguments_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(void) { char b[8]; fgets(b); }\n',
                     "fgets") is None

    def test_a_non_read_stdio_call_is_untouched(self, tmp_path):
        # fputs writes; this mechanism answers the READ side only, and
        # read_boundary_for_target_kind is direction-specific for that reason.
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(FILE *h) { fputs("x", h); }\n',
                     "fputs") is None


class TestBindingOrder:
    """Same rule as ``_go_binding_rhs``: the LAST binding at or above the use,
    in the enclosing function only. Both directions are pinned because a
    last-match-in-file scan gets one wrong and a first-match scan the other."""

    def test_a_rebinding_above_the_use_wins(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(const char *p) {\n'
                     '    char b[8];\n'
                     '    FILE *h = fopen(p, "r");\n'
                     '    h = stdin;\n'
                     '    fgets(b, 8, h);\n'
                     '}\n', "fgets") == "std_stream"

    def test_a_rebinding_below_the_use_is_not_used(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void f(const char *p) {\n'
                     '    char b[8];\n'
                     '    FILE *h = stdin;\n'
                     '    fgets(b, 8, h);\n'
                     '    h = fopen(p, "r");\n'
                     '}\n', "fgets") == "std_stream"

    def test_a_binding_in_another_function_is_not_read(self, tmp_path):
        assert _kind(tmp_path, '#include <stdio.h>\n'
                     'void other(const char *p) { FILE *h = fopen(p, "r"); (void)h; }\n'
                     'void f(FILE *h) { char b[8]; fgets(b, 8, h); }\n',
                     "fgets") is None


class TestOriginLookupEdgesThatReturnNothing:
    """The abstention paths, each reached by real C rather than by a mock.

    Every one of these must stamp NOTHING. The direction this seam runs in ADDS
    findings, so an origin the analyzer cannot name has to leave the call
    classified exactly as the catalogue's first-declared row says.
    """

    def test_a_call_outside_any_function_has_no_body_to_search(
        self, tmp_path,
    ) -> None:
        """A file-scope initializer has no enclosing ``function_definition``.

        There is no caller symbol either, so the analyzer emits NO edge at all
        and there is nothing to stamp -- which is why this asserts the edge set
        rather than a stamp. The origin lookup still runs (the stamp is
        computed per CALL, before the edges it would decorate are known), and
        it has to survive a node with no enclosing body rather than raise.
        """
        (tmp_path / "main.c").write_text(
            "#include <stdio.h>\n"
            "char buf[8];\n"
            "int x = fgets(buf, 8, f);\n"
        )
        edges = [e for e in analyze_c(tmp_path).edges
                 if e.edge_type == "calls" and ":fgets:" in e.dst]
        assert edges == []

    def test_a_non_identifier_assignment_target_is_skipped(
        self, tmp_path,
    ) -> None:
        """``s.f = fopen(...)`` binds a FIELD, and this scan names variables.

        The declarator walk bottoms out with no ``identifier``, so the binding
        is not attributed to any name -- rather than being attributed to the
        wrong one, which is the failure that would invent a crossing.
        """
        assert _kind(tmp_path, (
            "#include <stdio.h>\n"
            "struct S { FILE *f; };\n"
            "void g(struct S s, char *buf) {\n"
            "    s.f = fopen(\"/x\", \"r\");\n"
            "    fgets(buf, 8, f);\n"
            "}\n"
        ), "fgets") is None

    def test_a_binding_of_a_different_name_is_skipped(self, tmp_path) -> None:
        """The function binds ``q``; the read is over ``f``."""
        assert _kind(tmp_path, (
            "#include <stdio.h>\n"
            "void g(char *buf) {\n"
            "    FILE *q = fopen(\"/x\", \"r\");\n"
            "    fgets(buf, 8, f);\n"
            "}\n"
        ), "fgets") is None
