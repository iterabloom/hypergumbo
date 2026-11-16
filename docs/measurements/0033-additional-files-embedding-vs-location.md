<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0033: What the embedding ranking in sketch's Additional Files costs, whether it repeats, and what finds the files a README points to

**Status:** Complete
**Date:** 2026-11-13
**Instrument:** `~/hypergumbo_lab_notebook/patof_embedding_prefilter_11132026/` — `measure.py` (cold embedding of every candidate, per-file score and token count), `resample.py` (the same embedding under two other sampling seeds), `batching.py` (padding against tokens), `build_corpus.py` + `rank_experiment.py` (444-repository ranker comparison), `FROZEN.md` and `FROZEN2.md` (each written before the split it names was read), `holdout.txt` and `test_result.txt` (the one scoring of each held-out set).
**Tracker:** `WI-patof` (the cost)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: **a README-linked candidate file** — an Additional Files candidate
  (configuration or documentation role, not a source file) that the
  repository's own README links to. A ranker is scored by the share of a
  repository's linked files it places in its first K (R@K), averaged over
  repositories that have at least one, with the pooled share beside it.
- allocation: CENSUS of candidates within each repository; every repository in
  the cohort that has a README and at most 3,000 candidates.
- seed: none for the lexical and location rankers (deterministic). The shipped
  embedding samples each file at offsets seeded by `crc32(path)`; the
  repeatability arm re-seeds with the path plus `#b` and plus `#c`. The
  repository split is `crc32(lower-cased name) % 5` (0-2 train, 3 dev, 4 test).
  The shuffled baseline uses `random.Random(0)`.
- language_scope: none of the rankers reads source code. Candidate files are
  Markdown, reStructuredText, AsciiDoc, HTML, YAML, JSON, TOML and XML as
  `taxonomy.get_role` classes them. The probe paragraphs are English; a
  repository documented in another language was not separated out.
- cohort: 444 local clones of well-known projects under `~/curriculum_repos`,
  `~/repos` and `~/ALL_REPOS/*`, de-duplicated by name; 30 more were skipped
  (18 no README, 11 over 3,000 candidates, 1 no candidates), listed in
  `corpus/log.txt`. The embedding arms ran on seven: hypergumbo, alertmanager,
  killbill, prometheus (design) and kserve, jenkins, keycloak (held out).
- claim_set: none. No claim is adjudicated.
- analyzer_sha: `hypergumbo_core` as of dev `847ce5888e`, copied to a snapshot
  directory and pinned with `PYTHONPATH` so edits in the checkout could not
  reach a run.
- rubric: a candidate is "linked" iff
  `sketch._extract_readme_internal_links(readme, root)` returns its path and
  it is not the README itself. No ranker reads README links.

## The question

A cold `hypergumbo sketch` on this repository spent 1m52s (2026-10-14) and then
3m37s (2026-11-09 recorded example) embedding every Additional Files candidate
to choose the files for one of three interleaved lists. Is the embedding worth
its cost: does it repeat, and does it find the files worth showing?

## 1. Cost

Cold cache, six CPU cores, `nomic-ai/modernbert-embed-base`, model load 3.6 s
on top.

| Repository | Candidates | Sample tokens | Embedding time | Per file |
| --- | --- | --- | --- | --- |
| alertmanager | 131 | 13,794 | 30.4 s | 0.23 s |
| killbill | 153 | 14,242 | 24.8 s | 0.16 s |
| hypergumbo | 451 | 122,589 | 115.2 s | 0.26 s |
| prometheus | 451 | 39,277 | 71.9 s | 0.16 s |
| kserve | 958 | 156,461 | 239.1 s | 0.25 s |
| jenkins | 1,247 | 106,822 | 260.2 s | 0.21 s |
| keycloak | 1,379 | 190,720 | 423.1 s | 0.31 s |

The jenkins and keycloak rows were timed while a test run used the same
machine; they are upper bounds. The time follows the number of files, not the
number of tokens (hypergumbo has three times prometheus's tokens for the same
file count and takes 1.6 times as long). Sorting by length before batching, to
cut padding, took 72.1 s against 82.3 s on alertmanager under load: a 12%
saving, not a different order of cost.

The 8,000-token sketch lists eight Additional Files; at most four come from
this list.

## 2. The ranking does not repeat itself

Each file is embedded from three 400-character windows of its first third, at
offsets seeded by its path. Overlap of the top S between the shipped seed and
two others, and between those two:

| Repository | Files whose score moved | Top 1 | Top 4 | Top 10 | Top 31 |
| --- | --- | --- | --- | --- | --- |
| alertmanager | 16 of 130 | 1.00 / 1.00 / 1.00 | 0.75 / 0.75 / 0.75 | 0.90 / 0.80 / 0.90 | 0.94 / 0.97 / 0.97 |
| killbill | 6 of 149 | 0.00 / 0.00 / 1.00 | 1.00 / 0.75 / 0.75 | 1.00 / 0.90 / 0.90 | 1.00 / 1.00 / 1.00 |
| prometheus | 48 of 450 | 0.00 / 0.00 / 1.00 | 0.75 / 0.75 / 1.00 | 0.90 / 0.90 / 0.80 | 0.84 / 0.84 / 0.87 |
| hypergumbo | 265 of 451 | 0.00 / 0.00 / 0.00 | 0.00 / 0.00 / 0.25 | 0.30 / 0.30 / 0.40 | 0.48 / 0.45 / 0.58 |

Where most candidates are short enough to be embedded whole (the first three
rows) the top four keep three files. Where most are long documents, the top
four share at most one file between any two seeds. On hypergumbo the scores
span 0.505 to 0.717 with the tenth at 0.687, and two byte-identical READMEs
scored 0.717 and 0.678: ranks 1 and 21 of 451. The embedding cache is keyed by
content hash, so on a warm run the two copies share one vector and the order
differs from the cold run's.

## 3. What finds the files a README links to

### Seven repositories, embedding included

Linked files placed in the top ten, pooled over the six repositories that have
any (37 linked files; killbill has none):

| Ranker | In top 10 |
| --- | --- |
| embedding (shipped) | 4 of 37 |
| BM25 on the first 4,000 characters against the probe paragraphs | 11 of 37 |
| BM25 on the whole file | 11 of 37 |
| documentation role, then depth | 13 of 37 |

24 of the 37 labels are hypergumbo's, where the embedding placed 1 of 24.
`FROZEN.md` pre-registered that the embedding would be kept if, on the three
held-out repositories, it beat BM25 on both R@10 and MRR; it scored 0.25 and
0.07 against 0.33 and 0.12. Eight labels: that result excludes "the embedding
is clearly better" and supports no more.

### 444 repositories, no embedding

Embedding 66,000 candidates was not attempted. Per-repository means:

| Ranker | DEV R@4 | DEV R@10 | DEV R@31 | DEV MRR | TEST R@4 | TEST R@10 | TEST R@31 | TEST MRR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shuffled | 0.15 | 0.33 | 0.52 | 0.12 | 0.09 | 0.24 | 0.35 | 0.08 |
| BM25, six probe paragraphs | 0.39 | 0.57 | 0.78 | 0.30 | 0.33 | 0.53 | 0.69 | 0.23 |
| BM25, 630 seed passages from train | 0.44 | 0.59 | 0.79 | 0.34 | 0.41 | 0.55 | 0.73 | 0.26 |
| log-odds of head terms, learned on train | 0.41 | 0.61 | 0.79 | 0.32 | 0.37 | 0.54 | 0.68 | 0.25 |
| role, then depth | 0.52 | 0.79 | 0.95 | 0.35 | 0.43 | 0.70 | 0.80 | 0.26 |
| role, depth, then BM25 on the probes (**shipped**) | 0.54 | 0.77 | 0.95 | 0.41 | 0.43 | 0.72 | 0.88 | 0.32 |
| role, depth, then BM25 on the seeds | 0.54 | 0.77 | 0.95 | 0.40 | 0.44 | 0.69 | 0.88 | 0.31 |
| log-odds of path terms + role + depth, learned on train | 0.60 | 0.81 | 0.94 | 0.44 | 0.44 | 0.70 | 0.93 | 0.33 |

DEV is 55 repositories with 214 linked files; TEST is 46 with 233, scored
once. Train is 258 repositories, 630 linked files, 65,147 other candidates.

`FROZEN2.md` pre-registered two readings of TEST. (1) The shipped order is at
least BM25-on-probes on R@10 and MRR: 0.72 and 0.32 against 0.53 and 0.23.
Holds. (2) BM25 on the 630 seed passages does not exceed the shipped order on
R@10: 0.55 against 0.72. Holds.

## Decision

The embedding is removed from Additional Files. Its place is taken by
`overview_rank.rank_overview_files`: documentation before configuration,
shallower first, BM25 against the probe paragraphs within one role and depth,
then path. It costs file reads, is the same on every run and on every machine
(the embedding list was absent wherever `sentence-transformers` was not
installed), and on the one outside test available it finds more of the files
authors point to than content similarity does.

## What this does not support

- **README links are a stand-in.** They say which files authors send a
  newcomer to. The sketch already lists them through their own channel; the
  order measured here fills a different list. Nothing here measures whether a
  reader of the sketch understood the project better.
- **Nothing here shows embeddings cannot rank documentation.** The shipped
  design sampled random windows and compared against six generic paragraphs.
  A different embedding design was not tried.
- **The learned path-term table was not adopted**, and that is a judgement,
  not a measurement: it scored 0.06 higher on DEV R@4 and 0.01 higher on TEST.
  Its largest weights are `roadmap`, `adopters`, `contributing`, `faq`: the
  names READMEs link to.
- **The seed-passage arm tests one use of the corpus**: as a BM25 query. It
  does not show that no model trained on those passages would help.
- **English.** The probes, and 444 mostly English-documented projects.
