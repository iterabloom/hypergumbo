#!/usr/bin/env bash
set -u

# ==============================================================================
# TEST SUITE FOR HYPERGUMBO commit-msg HOOK
# ==============================================================================

# 1. Setup Sandbox
# ------------------------------------------------------------------------------
TEST_DIR="$(mktemp -d -t hypergumbo-test.XXXXXX)"
HOOKS_DIR="$TEST_DIR/.githooks"
mkdir -p "$HOOKS_DIR"

cleanup() {
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

echo "📂 Initialized test sandbox at: $TEST_DIR"

# 2. Populate Configuration Files
# ------------------------------------------------------------------------------

cat > "$HOOKS_DIR/brand-patterns.txt" <<EOF
Claude
Gemini
GPT
EOF

FERRET_PHRASE="a ferret riding a surface of holographic panels in a mossy Shoney's atrium with a dynasty of pigeons made of pumpernickel crumbs"
cat > "$HOOKS_DIR/absurd-phrases.txt" <<EOF
$FERRET_PHRASE
EOF

FERRET_SLUG=$(echo "$FERRET_PHRASE" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')
BAD_EMAIL="${FERRET_SLUG}@racialcapitalism.isbad"

# 3. Install the Hook script
# ------------------------------------------------------------------------------
COMMIT_MSG_HOOK="$HOOKS_DIR/commit-msg"

cat > "$COMMIT_MSG_HOOK" <<'END_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

msg_file="${1:-}"
if [[ -z "$msg_file" || ! -f "$msg_file" ]]; then
  echo "commit-msg hook: missing commit message file path" >&2
  exit 1
fi

have() { command -v "$1" >/dev/null 2>&1; }

hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sha256_hex() {
  if have sha256sum; then sha256sum | awk '{print $1}'
  elif have shasum; then shasum -a 256 | awk '{print $1}'
  elif have openssl; then openssl dgst -sha256 | awk '{print $NF}'
  elif have python3; then
    python3 - <<'PY'
import sys, hashlib
data = sys.stdin.buffer.read()
sys.stdout.write(hashlib.sha256(data).hexdigest())
PY
  else exit 1; fi
}

gen_secret_base64_32bytes() {
  if have openssl; then openssl rand -base64 32
  elif have base64; then head -c 32 /dev/urandom | base64
  elif have python3; then
    python3 - <<'PY'
import os, base64, sys
sys.stdout.write(base64.b64encode(os.urandom(32)).decode("ascii") + "\n")
PY
  else exit 1; fi
}

slugify() {
  local s
  s="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  s="${s#-}"
  s="${s%-}"
  printf '%s' "$s"
}

escape_regex_basic() {
  if have sed; then printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
  elif have perl; then perl -e 'my $s = join("", <STDIN>); chomp($s); print quotemeta($s);' <<<"$1"
  elif have python3; then
    python3 - <<'PY'
import re, sys
s = sys.stdin.read()
s = s[:-1] if s.endswith("\n") else s
sys.stdout.write(re.escape(s))
PY
  else exit 1; fi
}

join_with_pipe() {
  local out="" first=1
  for s in "$@"; do
    if (( first )); then out="$s"; first=0; else out="${out}|${s}"; fi
  done
  printf '%s' "$out"
}

load_brand_regex() {
  local file="$1"
  local pats=() line p
  if [[ -f "$file" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -z "$line" ]] && continue
      if [[ "$line" == re:* ]]; then p="${line#re:}"; else p="$(escape_regex_basic "$line")"; fi
      [[ -z "$p" ]] && continue
      pats+=( "$p" )
    done < "$file"
  fi
  if (( ${#pats[@]} == 0 )); then
    pats+=( "$(escape_regex_basic "FanDuel")" )
  fi
  printf '(%s)' "$(join_with_pipe "${pats[@]}")"
}

brands_file="${HOOK_BRANDS_FILE:-$hook_dir/brand-patterns.txt}"
brand_re="$(load_brand_regex "$brands_file")"

replace_brands_ci() {
  local repl="$1"
  local in="$2"
  if have perl; then REPL="$repl" BRAND_RE="$brand_re" perl -pe 's{$ENV{BRAND_RE}}{$ENV{REPL}}ig' <<<"$in"
  elif have python3; then
    REPL="$repl" BRAND_RE="$brand_re" python3 - <<'PY'
import os, re, sys
repl = os.environ["REPL"]
pat  = os.environ["BRAND_RE"]
s = sys.stdin.read()
sys.stdout.write(re.sub(pat, repl, s, flags=re.I))
PY
  else exit 1; fi
}

lines=()
while IFS= read -r line || [[ -n "$line" ]]; do lines+=( "$line" ); done < "$msg_file"
n=${#lines[@]}

last=$n
while (( last > 0 )) && [[ -z "${lines[$((last-1))]}" ]]; do ((last--)); done

trailer_re='^[A-Za-z0-9][A-Za-z0-9-]*: '
trailer_start=$((last+1))

i=$last
while (( i >= 1 )); do
  line="${lines[$((i-1))]}"
  [[ -z "$line" ]] && break
  if [[ "$line" =~ $trailer_re ]]; then trailer_start=$i; ((i--)); continue; fi
  break
done

subject_line=""
body_lines=()
trailers=()

if (( n > 0 )); then subject_line="${lines[0]}"; fi
body_end=$((trailer_start-1))
if (( body_end > 1 )); then body_lines=( "${lines[@]:1:$((body_end-1))}" ); fi
if (( trailer_start <= last )); then
  len=$(( last - trailer_start + 1 ))
  trailers=( "${lines[@]:$((trailer_start-1)):$len}" )
fi

nocase_was_enabled=0
if shopt -q nocasematch; then nocase_was_enabled=1; fi
shopt -s nocasematch

phrases_file="${HOOK_PHRASES_FILE:-$hook_dir/absurd-phrases.txt}"

secret_file="${BRAND_SCRUB_SECRET_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/brand-scrub.key}"
if [[ ! -f "$secret_file" ]]; then
  mkdir -p "$(dirname "$secret_file")"
  ( umask 077; gen_secret_base64_32bytes > "$secret_file" )
  chmod 600 "$secret_file" 2>/dev/null || true
fi
secret="$(cat "$secret_file")"

phrases=()
if [[ -f "$phrases_file" ]]; then
  while IFS= read -r pline || [[ -n "$pline" ]]; do
    [[ -z "$pline" ]] && continue
    [[ "$pline" =~ ^[[:space:]]*# ]] && continue
    pline="${pline#"${pline%%[![:space:]]*}"}"
    pline="${pline%"${pline##*[![:space:]]}"}"
    [[ -z "$pline" ]] && continue
    phrases+=( "$pline" )
  done < "$phrases_file"
fi
if (( ${#phrases[@]} == 0 )); then phrases=( "a walrus" ); fi

pick_phrase_private() {
  local seed="$1" h idx
  h="$(printf '%s|%s' "$secret" "$seed" | sha256_hex)"
  idx=$(( 16#${h:0:8} % ${#phrases[@]} ))
  printf '%s' "${phrases[$idx]}"
}

replacement_out="$(pick_phrase_private "$subject_line")"
replacement_in="$(slugify "$replacement_out")"

if [[ "$subject_line" =~ $brand_re ]]; then
  subject_line="$(replace_brands_ci "$replacement_out" "$subject_line")"
fi

filtered_body=()
for l in "${body_lines[@]}"; do
  if [[ "$l" =~ $brand_re ]]; then continue; fi
  filtered_body+=( "$l" )
done

out_body=( "$subject_line" "${filtered_body[@]}" )

while (( ${#out_body[@]} > 0 )); do
  last_idx=$(( ${#out_body[@]} - 1 ))
  [[ -z "${out_body[$last_idx]}" ]] || break
  unset "out_body[$last_idx]"
done

rewrite_trailer_value_smartass() {
  local v="$1"
  # FIX: Store regex in variable to prevent glob expansion of [^<] and [^>]
  local identity_re='^([^<]*)<([^>]*)>(.*)$'

  if [[ "$v" =~ $identity_re ]]; then
    printf '%s <%s@racialcapitalism.isbad>%s' "$replacement_out" "$replacement_in" "${BASH_REMATCH[3]}"
  else
    printf '%s' "$replacement_out"
  fi
}

out_trailers=()
for t in "${trailers[@]}"; do
  if [[ "$t" =~ ^([A-Za-z0-9][A-Za-z0-9-]*:)([[:space:]]*)(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    ws="${BASH_REMATCH[2]}"
    val="${BASH_REMATCH[3]}"
    if [[ "$val" =~ $brand_re ]]; then
      val="$(rewrite_trailer_value_smartass "$val")"
    fi
    t="${key}${ws}${val}"
  fi
  out_trailers+=( "$t" )
done

if (( nocase_was_enabled == 0 )); then shopt -u nocasematch; fi

# FIX: Separate the blank line printf from the array printf
{
  if (( ${#out_body[@]} > 0 )); then
    printf "%s\n" "${out_body[@]}"
  fi
  if (( ${#out_trailers[@]} > 0 )); then
    printf "\n"
    printf "%s\n" "${out_trailers[@]}"
  fi
} > "$msg_file"

if ! grep -q "^Signed-off-by: " "$msg_file"; then
  echo "Error: DCO Sign-off missing." >&2; exit 1
fi
END_SCRIPT

chmod +x "$COMMIT_MSG_HOOK"

# 4. Helpers for Testing
# ------------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0

run_test() {
  local test_name="$1"
  local input_msg="$2"
  local expected_msg="$3"
  
  local msg_file_path="$TEST_DIR/COMMIT_EDITMSG"
  printf '%s' "$input_msg" > "$msg_file_path"

  echo "--------------------------------------------------------"
  echo "TEST: $test_name"
  
  if ! "$COMMIT_MSG_HOOK" "$msg_file_path" 2>/dev/null; then
    echo "❌ CRASH: Hook exited with error."
    ((FAIL_COUNT++))
    return 1
  fi

  local actual_msg
  actual_msg=$(cat "$msg_file_path")

  if [[ "$actual_msg" == "$expected_msg" ]]; then
    echo "✅ PASS"
    ((PASS_COUNT++))
  else
    echo "❌ FAIL"
    echo "--- Expected ---"
    echo "$expected_msg" | cat -A | sed 's/^/  /'
    echo "--- Actual ---"
    echo "$actual_msg" | cat -A | sed 's/^/  /'
    ((FAIL_COUNT++))
    return 1
  fi
}

# 5. Define Basic Text Blocks (Shared)
# ------------------------------------------------------------------------------
read -r -d '' BODY <<'EOF' || true
test: enforce 100% coverage in CI and add missing tests

CI was running pytest without coverage enforcement, allowing the codebase
to ship at 68% coverage despite the 100% requirement in AGENTS.md. This
adds --cov=src --cov-fail-under=100 to CI and the unit tests needed to
achieve full coverage.
EOF

DIRTY_LINE="🤖 Generated with [Claude Code](https://claude.com/claude-code)"
SIGNER="Signed-off-by: jgstern-agent <josh-agent@iterabloom.com>"

# 6. Execute Scenarios
# ------------------------------------------------------------------------------

# SCENARIO 1: "Claude Opus 4.5"
INPUT_1="${BODY}

${DIRTY_LINE}

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
${SIGNER}
"

EXPECTED_1="${BODY}

Co-Authored-By: ${FERRET_PHRASE} <${BAD_EMAIL}>
${SIGNER}"

run_test "Scenario 1: Claude Opus (Nuclear Replacement)" "$INPUT_1" "$EXPECTED_1"

# SCENARIO 2: "Tom Morello"
INPUT_2="${BODY}

${DIRTY_LINE}

Co-Authored-By: Tom Morello <tmorello@anthropic.com>
${SIGNER}
"

EXPECTED_2="${BODY}

Co-Authored-By: Tom Morello <tmorello@anthropic.com>
${SIGNER}"

run_test "Scenario 2: Tom Morello (Identity Preserved)" "$INPUT_2" "$EXPECTED_2"


# SCENARIO 3: "Claude Shannon"
INPUT_3="${BODY}

Co-Authored-By: Claude Shannon <cshannon@anthropic.com>
${SIGNER}
"

EXPECTED_3="${BODY}

Co-Authored-By: ${FERRET_PHRASE} <${BAD_EMAIL}>
${SIGNER}"

run_test "Scenario 3: Claude Shannon (Prof Shannon Unluckily Wiped)" "$INPUT_3" "$EXPECTED_3"

# SCENARIO 4: DCO Check
echo "--------------------------------------------------------"
echo "TEST: Scenario 4: DCO Check (Expecting Failure)"
echo "Update readme" > "$TEST_DIR/COMMIT_EDITMSG"

if ! "$COMMIT_MSG_HOOK" "$TEST_DIR/COMMIT_EDITMSG" >/dev/null 2>&1; then
    echo "✅ PASS (Hook blocked commit w/o signature)"
    ((PASS_COUNT++))
else
    echo "❌ FAIL (Hook allowed commit w/o signature)"
    ((FAIL_COUNT++))
fi

# 7. Summary
# ------------------------------------------------------------------------------
echo "========================================================"
echo "SUMMARY: $PASS_COUNT passed, $FAIL_COUNT failed"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi
