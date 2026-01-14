# Citations

Hypergumbo uses several pretrained models for semantic analysis. If you use hypergumbo in academic work, please consider citing the underlying model papers.

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

### ModernBERT (Additional Files Ranking)

Used for semantic ranking of additional files using 5W1H similarity scoring.

```bibtex
@misc{modernbert,
  title={Smarter, Better, Faster, Longer: A Modern Bidirectional Encoder for Fast, Memory Efficient, and Long Context Finetuning and Inference},
  author={Benjamin Warner and Antoine Chaffin and Benjamin Clavié and Orion Weller and Oskar Hallström and Said Taghadouini and Alexis Gallagher and Raja Biswas and Faisal Ladhak and Tom Aarsen and Nathan Cooper and Griffin Adams and Jeremy Howard and Iacopo Poli},
  year={2024},
  eprint={2412.13663},
  archivePrefix={arXiv},
  primaryClass={cs.CL},
  url={https://arxiv.org/abs/2412.13663}
}
```

**Links:** [arXiv](https://arxiv.org/abs/2412.13663) | [Hugging Face](https://huggingface.co/nomic-ai/modernbert-embed-base)

### Nomic Embed

The ModernBERT model we use is based on the Nomic Embed training methodology.

```bibtex
@misc{nussbaum2024nomic,
  title={Nomic Embed: Training a Reproducible Long Context Text Embedder},
  author={Zach Nussbaum and John X. Morris and Brandon Duderstadt and Andriy Mulyar},
  year={2024},
  eprint={2402.01613},
  archivePrefix={arXiv},
  primaryClass={cs.CL}
}
```

**Links:** [arXiv](https://arxiv.org/abs/2402.01613) | [Nomic AI](https://www.nomic.ai/)

## How These Models Are Used

1. **Config extraction** (`--config`): UniXcoder embeddings identify configuration-relevant files by comparing them against semantic probes like "what is the project structure" and "how to build the project".

2. **Additional files ranking**: ModernBERT embeddings rank non-source files (docs, configs, data) by relevance using 5W1H (Who, What, Where, When, Why, How) similarity scoring.

3. **All embedding computation is local** — no data is sent to external services.

## Optional Dependencies

These models are loaded via `sentence-transformers` and only used when available:

```bash
pip install sentence-transformers
```

Without this dependency, hypergumbo falls back to heuristic-based ranking which works well for most use cases.
