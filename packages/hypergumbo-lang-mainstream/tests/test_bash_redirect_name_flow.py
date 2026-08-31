# SPDX-License-Identifier: AGPL-3.0-or-later
"""A redirect's write is credited only to names the SHELL ITSELF can put there.

WI-zovuz. bash is ``dataflow_capable=False``, so every taint finding in a shell
script was call-graph reachability: "this file reads the environment somewhere
AND reaches a function that writes somewhere". Measured on 15 cohort repos, 186
environment names are read in the 69 files that also carry a write redirect,
and only 28 of them (15%) can reach what the shell writes -- so 71% of those
files carried a redirect-sink finding no name supports.

THE QUESTION THIS ASKS, and why it is the finer one rather than a sibling gate:
not "is the byte producer external?" (measured wrong -- see the two pinned
counterexamples below) but "can an externally-derived NAME reach what the SHELL
contributes here?" The shell contributes three things and only three: the
redirect's target operand, a heredoc body it expands itself, and the arguments
of a producing stage that is not a pure far-side fetch.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.bash import analyze_bash


def _redirect_origins(tmp_path: Path, script: str) -> dict[int, list[str]]:
    """Map redirect line -> the shell-reachable origin names stamped on it."""
    (tmp_path / "s.sh").write_text(script)
    out: dict[int, list[str]] = {}
    for edge in analyze_bash(tmp_path).edges:
        meta = edge.meta or {}
        if str(meta.get("io_primitive", "")).startswith("redirect."):
            out[edge.line] = list(meta.get("redirect_origin_names", []))
    return out


class TestTheShellsOwnBytes:
    """What the shell writes, it is credited with. What it does not, it is not."""

    def test_an_external_stages_arguments_still_credit_conservatively(self, tmp_path):
        # INV-fumod shape (b), the item's named instance in miniature, PINNED
        # AT THE CONSERVATIVE ANSWER ON PURPOSE. The env value only SELECTS the
        # network resource and the bytes the '>' writes are the HTTP response
        # body -- but deciding that requires knowing curl fetches rather than
        # interpolates, which is per-command semantics this change does not
        # have. Every stage's arguments therefore credit, which is the
        # fail-closed direction, and shape (b) stays open.
        #
        # MEASURED, so the omission is priced rather than assumed: ablating the
        # fetch rule over 15 cohort repos moves 3 of 186 environment names and
        # ZERO of the 69 files. It is load-bearing only for the per-name gate.
        #
        # This test also pins POSITIONAL BINDING: MYSQL_VERSION reaches the
        # redirect only via $2 at the call site, two hops from the operand.
        origins = _redirect_origins(tmp_path, (
            'download() {\n'
            '    local URL="$2"\n'
            '    curl -L "$URL" > /opt/drivers/mysql.jar\n'
            '}\n'
            'download "mysql" "https://example.com/c-$MYSQL_VERSION.tar.gz"\n'
        ))
        assert origins == {3: ["MYSQL_VERSION"]}

    def test_a_builtin_stage_upstream_of_an_external_one_still_credits(self, tmp_path):
        # PINNED COUNTEREXAMPLE 1 (beads scripts/sign-windows.sh:99). The stage
        # feeding the redirect is external (base64), but stage ONE is a builtin
        # carrying a secret, and a signing certificate really is written to
        # disk. A gate that reasoned about the last stage would delete this.
        origins = _redirect_origins(tmp_path, (
            'echo "$SIGNING_CERT_BASE64" | base64 -d > /tmp/cert.pfx\n'
        ))
        assert origins == {1: ["SIGNING_CERT_BASE64"]}

    def test_a_heredoc_body_is_the_shells_own_bytes(self, tmp_path):
        # PINNED COUNTEREXAMPLE 2. Every stage is external (cat), yet the shell
        # performs the expansion and feeds the result to stdin. Missing this is
        # a false ALL-CLEAR over a password written to a config file, which is
        # the one direction this gate may never be wrong in.
        origins = _redirect_origins(tmp_path, (
            'cat > /tmp/out.conf <<EOF\n'
            'password = $DB_PASSWORD\n'
            'EOF\n'
        ))
        assert origins == {1: ["DB_PASSWORD"]}

    def test_a_quoted_heredoc_delimiter_suppresses_expansion(self, tmp_path):
        # The other half of the heredoc rule: <<'EOF' writes the dollar sign
        # literally, so nothing crosses. Asserted so the fix above cannot be
        # implemented as "any heredoc credits every name in its body".
        origins = _redirect_origins(tmp_path, (
            "cat > /tmp/out.conf <<'EOF'\n"
            'password = $DB_PASSWORD\n'
            'EOF\n'
        ))
        assert origins == {1: []}

    def test_a_tainted_target_operand_credits_even_with_a_fetch(self, tmp_path):
        # The operand is the shell's own contribution even when the bytes are
        # not: `curl url > "$OUT"` chooses WHERE with an env value.
        origins = _redirect_origins(tmp_path, (
            'curl -L https://example.com/x > "$OUTPUT_PATH"\n'
        ))
        assert origins == {1: ["OUTPUT_PATH"]}

    def test_an_assignment_chain_carries_the_origin(self, tmp_path):
        # Name flow proper: the redirect names a local, the local derives from
        # the environment two hops up.
        origins = _redirect_origins(tmp_path, (
            'BASE="$HOME_DIR"\n'
            'DEST="$BASE/out.txt"\n'
            'echo hello > "$DEST"\n'
        ))
        assert origins == {3: ["HOME_DIR"]}

    def test_a_locally_assigned_name_is_not_an_origin(self, tmp_path):
        # The mirror: a name the SCRIPT assigns from a literal is not an
        # external origin, so it credits nothing.
        origins = _redirect_origins(tmp_path, (
            'DEST="/tmp/out.txt"\n'
            'echo hello > "$DEST"\n'
        ))
        assert origins == {2: []}


class TestABindingIsABindingHoweverItIsWritten:
    """Found by reading a removed row back against source, not by design."""

    def test_a_loop_variable_carries_its_word_lists_origin(self, tmp_path):
        # The env-read rule treats a `for` target as ASSIGNED, so without an
        # explicit binding here `f` is neither external nor derived and the
        # redirect reports reaching nothing -- a false ALL-CLEAR over a secret
        # that really is written.
        origins = _redirect_origins(tmp_path, (
            'for f in $SECRET_LIST; do\n'
            '    echo "$f" > /tmp/out.txt\n'
            'done\n'
        ))
        assert origins == {2: ["SECRET_LIST"]}

    def test_the_loop_body_is_not_part_of_the_binding(self, tmp_path):
        # Only the word list binds. Crediting the body would make every name
        # mentioned anywhere in the loop an origin of the loop variable.
        origins = _redirect_origins(tmp_path, (
            'for f in a b c; do\n'
            '    echo "$UNRELATED" >> /tmp/log.txt\n'
            '    echo "$f" > /tmp/out.txt\n'
            'done\n'
        ))
        assert origins[3] == []
        assert origins[2] == ["UNRELATED"]


def _redirect_sites(tmp_path: Path, script: str) -> list[tuple[str, list[str]]]:
    """(target, origins) per SITE — line is not a key (INV-vukiv)."""
    (tmp_path / "s.sh").write_text(script)
    sites = []
    for edge in analyze_bash(tmp_path).edges:
        meta = edge.meta or {}
        if str(meta.get("io_primitive", "")).startswith("redirect."):
            sites.append((meta.get("redirect_target"),
                          list(meta.get("redirect_origin_names", []))))
    return sorted(sites)


class TestTheAwkwardShapes:
    """Each of these silently mis-answered a first draft of the closure."""

    def test_a_sibling_redirects_target_is_not_this_ones_contribution(self, tmp_path):
        # `> out` writes the command's stdout; `2> "$ERROR_LOG"` names a
        # DIFFERENT file. Crediting ERROR_LOG to the first was a
        # fresh-Node-wrapper bug: tree-sitter hands out a new Node per access,
        # so `sibling is node` never matched and the self-skip never fired.
        #
        # ALSO PINS INV-vukiv: both redirects are on ONE line, so a per-LINE
        # answer would collapse them. The stamp is keyed per SITE.
        assert _redirect_sites(tmp_path, (
            'echo hello > /tmp/out.txt 2> "$ERROR_LOG"\n'
        )) == [("$ERROR_LOG", ["ERROR_LOG"]), ("/tmp/out.txt", [])]

    def test_a_mutual_assignment_cycle_terminates(self, tmp_path):
        # A="$B"; B="$A" is legal shell and must not recurse forever.
        origins = _redirect_origins(tmp_path, (
            'A="$B"\n'
            'B="$A"\n'
            'echo hi > "$A"\n'
        ))
        assert origins[3] == []

    def test_a_command_name_that_is_itself_an_expansion(self, tmp_path):
        # `"$RUNNER" arg` has a command_name whose child is an expansion, not
        # a word, so the positional-binding scan must skip it rather than
        # assume a `word` child exists.
        origins = _redirect_origins(tmp_path, (
            '"$RUNNER" --flag > /tmp/out.txt\n'
        ))
        assert origins[1] == ["RUNNER"]

    def test_a_bare_assignment_statement_is_not_a_call(self, tmp_path):
        # A statement that is only an assignment has no command_name at all.
        origins = _redirect_origins(tmp_path, (
            'helper() { echo hi > /tmp/out.txt; }\n'
            'FOO=bar\n'
            'helper\n'
        ))
        assert origins[1] == []


class TestAParseFailureIsNotAProof:
    """The stamp must be ABSENT, not empty, when the closure cannot answer."""

    def test_an_unparsed_statement_stamps_nothing(self, tmp_path):
        # cilium contrib/scripts/kind-setup-dns.sh: `<<EOF cat >/etc/dnsmasq.conf`
        # with the heredoc leading. tree-sitter recovers with an ERROR node and
        # the body is never attached as a sibling, so the closure sees no names
        # while the body really does write `$ddns` to a config file. Stamping
        # an empty list there is a false ALL-CLEAR the PARSER manufactured.
        (tmp_path / "s.sh").write_text(
            'read _ ddns < <(grep nameserver /etc/resolv.conf)\n'
            '<<EOF cat >/etc/dnsmasq.conf\n'
            'server=//$ddns\n'
            'EOF\n'
        )
        for edge in analyze_bash(tmp_path).edges:
            meta = edge.meta or {}
            if str(meta.get("io_primitive", "")).startswith("redirect."):
                assert "redirect_origin_names" not in meta, meta

    def test_a_clean_parse_still_stamps(self, tmp_path):
        # The control: without the unparseable form the key is present, so the
        # test above is asserting absence for the stated reason rather than
        # because the key is never written.
        (tmp_path / "s.sh").write_text('echo hi > /tmp/out.txt\n')
        stamped = [
            (edge.meta or {}) for edge in analyze_bash(tmp_path).edges
            if str((edge.meta or {}).get("io_primitive", "")).startswith("redirect.")
        ]
        assert stamped and all("redirect_origin_names" in m for m in stamped)
