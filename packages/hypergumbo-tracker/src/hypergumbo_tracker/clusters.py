# SPDX-License-Identifier: MPL-2.0
"""Predicted clusters of tracker items: TF-IDF cosine, top-k neighbours, Jarvis-Patrick.

This is the clustering path that survived the 2026-09-04..06 investigation in the
lab notebook (``embed_family_09042026/DESIGN.md`` — twelve pre-registered pilots
— and ``tracker_neighbornet_09052026/README.md`` — the NeighborNet port, its
block ensemble, and the probes that retired both). What it found, in order:

* **Similarity.** TF-IDF cosine on the item's masked full text beat a dense
  embedding model (modernbert-embed-base), BM25 and BMX at recovering families
  nobody chose (declared parent/child sets, ``isbefore`` sets, author-linked
  pairs). BM25-style scorers collapse on short items because they have no
  candidate-side norm; masking item IDs matters (an ID shared by two items is a
  link, not vocabulary); masking ADR numbers moved nothing but is kept for the
  same reason.
* **Grouping.** Every heavier method was matched or beaten by plain k-nearest
  neighbours on that similarity: a 16-minute NeighborNet run, its 19-minute
  96-seed block ensemble with a group-level consensus, average and complete
  linkage. At equal pair budgets, top-k pairs on raw cosine covered as many
  declared links and resolved as many "hubs" (items with several declared
  partners) as the best of them, in about a second. A shared-nearest-neighbour
  rerank added a few hubs on top. Bagging kNN over item subsets cannot add
  information (within a subset, j is in i's top-k iff fewer than k subset
  members are closer: a monotone function of global rank), so no ensemble.
* **Cluster formation.** Connected components of a non-mutual top-k graph fuse
  into one giant component. Jarvis-Patrick — an edge only where the two items
  are in each other's top-k AND share at least ``shared`` of those k
  neighbours — is the classic remedy for high-dimensional text where most
  pairs are near-orthogonal, and a parameter sweep on the 2059-item tracker
  put ``k=6, shared=3`` at 308 clusters, largest 17, with 12.5% of
  within-cluster pairs being declared links (chance 0.04%); ``k=10`` and
  above chained into a giant component at any ``shared``.

Design constraints honoured here: the tracker package is MPL-2.0 and prefers
the standard library, so this is pure Python — sparse dict vectors and an
inverted index. The vectorizer reproduces scikit-learn's ``TfidfVectorizer``
with ``sublinear_tf=True, min_df=2, max_df=0.8`` (the validated settings):
tokens are maximal runs of two or more word characters, lowercased; term
frequency is ``1 + ln(tf)``; document frequency is smoothed,
``ln((1 + N) / (1 + df)) + 1``; rows are l2-normalised; the vocabulary is cut
on the FIT corpus, which is every non-deleted item so the IDF is stable, while
neighbours are searched only among the items being clustered (the open ones by
default). The item's text is its title, description, string-valued fields and
non-tombstoned discussion messages, joined with spaces.

Cost is dominated by the neighbour search, ``sum over terms of df^2``: a few
seconds for two thousand items. A newly entered item can be placed against the
existing clusters with the same vectors in milliseconds; nothing here needs to
be incremental.

Ranking: clusters are ordered by the mean pairwise cosine of their members,
which is a distance in the same units for every size and does not grow with
size (the notebook measured the opposite — larger groups share less), so no
size normalisation is applied.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from .id_matching import build_item_id_pattern
from .models import CompiledItem, TrackerConfig

DEFAULT_K = 6
DEFAULT_SHARED = 3
DEFAULT_MIN_DF = 2
DEFAULT_MAX_DF = 0.8

_TOKEN = re.compile(r"\w\w+")
_ADR = re.compile(r"\bADR-[0-9A-Za-z]{4}\b")


@dataclass(frozen=True)
class Cluster:
    """A predicted cluster: member item IDs and the mean pairwise cosine among them."""

    item_ids: list[str]
    mean_similarity: float


def id_pattern_for(config: TrackerConfig) -> re.Pattern[str] | None:
    """The configured kinds' item-ID regex, or None when the config declares no kinds."""
    try:
        return build_item_id_pattern(config)
    except ValueError:
        return None


def mask_text(text: str, id_pattern: re.Pattern[str] | None) -> str:
    """Replace item IDs with ``ITEM`` and ADR references with ``ADR``.

    An ID shared by two items is a declared relationship, not shared
    vocabulary; leaving it in would let the model recover links it is meant
    to predict.
    """
    if id_pattern is not None:
        text = id_pattern.sub("ITEM", text)
    return _ADR.sub("ADR", text)


def item_text(item: CompiledItem, id_pattern: re.Pattern[str] | None) -> str:
    """Title, description, string fields and live discussion, masked, space-joined."""
    parts = [item.title, item.description]
    parts.extend(v for v in item.fields.values() if isinstance(v, str))
    parts.extend(d.message for d in item.discussion if not d.is_tombstoned)
    return mask_text(" ".join(p for p in parts if p), id_pattern)


def tokenize(text: str) -> list[str]:
    """scikit-learn's default token pattern ``(?u)\\b\\w\\w+\\b`` on lowercased text."""
    return _TOKEN.findall(text.lower())


def tfidf_vectors(
    docs: Sequence[Sequence[str]],
    min_df: int = DEFAULT_MIN_DF,
    max_df: float = DEFAULT_MAX_DF,
) -> list[dict[str, float]]:
    """Sublinear-tf, smooth-idf, l2-normalised vectors; vocabulary cut by df on this corpus.

    ``max_df`` is a fraction of the corpus (scikit-learn's float form): a term
    is kept when ``min_df <= df <= max_df * N``.
    """
    n = len(docs)
    if n == 0:
        return []
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))
    max_count = max_df * n
    idf = {
        t: math.log((1 + n) / (1 + c)) + 1.0
        for t, c in df.items()
        if c >= min_df and c <= max_count
    }
    vectors: list[dict[str, float]] = []
    for doc in docs:
        tf = Counter(t for t in doc if t in idf)
        weights = {t: (1.0 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(w * w for w in weights.values()))
        vectors.append({t: w / norm for t, w in weights.items()} if norm > 0 else {})
    return vectors


def top_neighbors(vectors: Sequence[dict[str, float]], k: int) -> list[list[tuple[int, float]]]:
    """For each vector, its ``k`` highest-cosine others as ``(index, cosine)``, ties broken by index.

    Inverted index: a document only ever meets the documents it shares a term
    with, so the work is the sum over terms of the squared document frequency.
    """
    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for i, vec in enumerate(vectors):
        for term, w in vec.items():
            postings[term].append((i, w))
    out: list[list[tuple[int, float]]] = []
    for i, vec in enumerate(vectors):
        scores: dict[int, float] = defaultdict(float)
        for term, w in vec.items():
            for j, wj in postings[term]:
                if j != i:
                    scores[j] += w * wj
        ranked = sorted(scores.items(), key=lambda p: (-p[1], p[0]))
        out.append(ranked[:k])
    return out


def jarvis_patrick(neighbors: Sequence[Sequence[tuple[int, float]]], shared: int) -> list[list[int]]:
    """Connected components of the graph with an edge where two items are mutual
    top-k neighbours sharing at least ``shared`` of their neighbours; singletons dropped.

    Components are returned sorted, each sorted, ordered by their first member.
    """
    tops = [{j for j, _ in row} for row in neighbors]
    parent = list(range(len(neighbors)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, row in enumerate(neighbors):
        for j, _ in row:
            if j > i and i in tops[j] and len(tops[i] & tops[j]) >= shared:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(neighbors)):
        groups[find(i)].append(i)
    return sorted((sorted(g) for g in groups.values() if len(g) >= 2), key=lambda g: g[0])


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine of two l2-normalised sparse vectors, clamped at 1 (rounding can exceed it by an ulp)."""
    if len(a) > len(b):
        a, b = b, a
    return min(1.0, sum(w * b[t] for t, w in a.items() if t in b))


def compute_clusters(
    items: Sequence[CompiledItem],
    fit_items: Sequence[CompiledItem],
    *,
    id_pattern: re.Pattern[str] | None,
    k: int = DEFAULT_K,
    shared: int = DEFAULT_SHARED,
    min_df: int = DEFAULT_MIN_DF,
    max_df: float = DEFAULT_MAX_DF,
) -> list[Cluster]:
    """Cluster ``items``; vocabulary and IDF are fitted on ``fit_items`` (plus any of
    ``items`` missing from it). Clusters are ranked by mean pairwise cosine, descending."""
    if len(items) < 2:
        return []
    corpus: list[CompiledItem] = list(fit_items)
    seen = {it.id for it in corpus}
    corpus.extend(it for it in items if it.id not in seen)
    index = {it.id: i for i, it in enumerate(corpus)}
    vectors = tfidf_vectors([tokenize(item_text(it, id_pattern)) for it in corpus], min_df, max_df)
    sub = [vectors[index[it.id]] for it in items]
    components = jarvis_patrick(top_neighbors(sub, k), shared)
    clusters: list[Cluster] = []
    for comp in components:
        pairs = [(a, b) for ai, a in enumerate(comp) for b in comp[ai + 1:]]
        mean = sum(_dot(sub[a], sub[b]) for a, b in pairs) / len(pairs)
        clusters.append(Cluster([items[i].id for i in comp], mean))
    clusters.sort(key=lambda c: (-c.mean_similarity, c.item_ids[0]))
    return clusters
