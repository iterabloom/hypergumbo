<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# URL-Folding Scope (WI-mugog Phase A)

This file declares which languages whose route-detector is active are
**intentionally left at literal-only URL extraction** for the current phase of
the URL-folding programme — i.e., the HTTP-linker scanner for that language
recognises a bare quoted-string URL or a single identifier reference, but does
**not** attempt to fold a composed URL (interpolation, array join, printf
formatting, string concatenation, etc.).

The companion property test
`packages/hypergumbo-core/tests/test_url_folding.py::test_active_route_languages_are_covered_or_scoped`
enforces that every active route-language is either:

1. listed in a YAML file under `url_folding/*.yaml` (covered by at least one
   folding idiom), **or**
2. listed below in this file with a one-line justification.

Failing both is a hard test failure.

## Phase A literal-only languages

| Language     | Scanner function in `linkers/http.py` | Justification |
|--------------|---------------------------------------|---------------|
| `python`     | `_scan_python_file`                   | Phase A defers Python f-string folding to **Phase B** (string-interpolation idiom expansion). Current scanner extracts URLs from `requests.X("/literal/path")` and `httpx.X("/literal/path")` call sites only. |
| `go`         | `_scan_go_file`                       | Phase A defers Go `fmt.Sprintf` folding to **Phase C** (printf-format idiom). Current scanner extracts URLs from literal-string call sites only. |
| `ruby`       | `_scan_ruby_file`                     | Phase A defers Ruby string interpolation (`"#{var}"`) to **Phase B**. Current scanner extracts URLs from literal-string call sites only. |
| `java`       | `_scan_java_file`                     | Phase A defers Java `String.format` / `StringBuilder` folding to **Phase C** and **Phase D** respectively. Current scanner extracts URLs from literal-string call sites only. |

## Phase B/C/D (future)

When a sibling tracker item under `INV-miloj` ships a folder for one of the
above languages, the corresponding row is removed from this file and a
`languages.<language>` entry is added to the relevant idiom YAML under
`url_folding/`.

If a NEW active route-language is added that is genuinely not yet wired for
folding, add it here with a justification rather than reactively patching the
property test — the test is the authoritative coverage gate.
