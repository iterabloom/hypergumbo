<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Citations

Hypergumbo uses a pretrained model for semantic analysis. If you use hypergumbo in academic work, please consider citing the underlying model paper.

## Embedding Models

### UniXcoder (Code Embeddings)

Used for config file semantic ranking and code understanding.

```bibtex
@article{guo2022unixcoder,
  title={UniXcoder: Unified Cross-Modal Pre-training for Code Representation},
  author={Guo, Daya and Lu, Shuai and Duan, Nan and Wang, Yanlin and Zhou, Ming and Yin, Jian},
  journal={arXiv preprint arXiv:2203.03850},
  year={2022}
}
```

**Links:** [arXiv](https://arxiv.org/abs/2203.03850) | [Hugging Face](https://huggingface.co/microsoft/unixcoder-base)

## How The Model Is Used

1. **Config extraction** (`--config`): UniXcoder embeddings identify configuration-relevant files by comparing them against semantic probes like "what is the project structure" and "how to build the project".

2. **Additional files ranking** uses no model: documentation before configuration, shallower paths first, wording as the tie-break. It used a second embedding model, ModernBERT, until [measurement 0033](measurements/0033-additional-files-embedding-vs-location.md) found that ranking slow, unrepeatable and no better than file role and depth.

3. **All embedding computation is local** — no data is sent to external services.

## Optional Dependencies

These models are loaded via `sentence-transformers` and only used when available:

```bash
pip install sentence-transformers
```

Without this dependency, hypergumbo falls back to heuristic-based ranking which works well for most use cases.
