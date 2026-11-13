# SPDX-License-Identifier: AGPL-3.0-or-later
"""Embedding-based utilities for sketch generation.

This module contains optional sentence-transformers-based functionality
for semantic operations in sketch generation. It's separated from
sketch.py to allow coverage to be measured independently when the heavy
dependencies (sentence-transformers, torch) aren't available.

The main entry point is ``extract_readme_description_embedding()``: extract
the project description from the README. ``sketch.py``'s config discovery
and extraction load the same model through ``_load_embedding_model``.

The install path has its own entry point: ``fetch_embedding_models()``
downloads every model in ``EMBEDDING_MODELS`` that is not yet cached,
lifting ``HF_HUB_OFFLINE`` while it runs. ``cli.py`` calls it, in a fresh
interpreter, only for ``install-embeddings`` and ``add-extras``; it is the
one place a model is fetched. Analysis loads from the cache only.

One model backs these: ``microsoft/unixcoder-base``
(``_load_embedding_model``) serves README description extraction, and
``sketch.py``'s config discovery and extraction import
``_load_embedding_model`` and ``NEGATIVE_PATTERNS`` from here directly.
A second model, ModernBERT, ranked the sketch's Additional Files until
measurement 0033 found the ranking slow, unrepeatable and no better than
file role and depth; that code and its per-file embedding cache are gone,
and the ranking is ``overview_rank``. Before every sentence_transformers
import (in ``_has_sentence_transformers`` and in the loader),
``_ensure_hf_offline_for_cached_models`` sets ``HF_HUB_OFFLINE=1`` for the
whole process unless the variable already holds a value huggingface_hub reads
as offline; ``0`` or ``false`` is overridden. It does this whether or not the
models are cached, despite its name (INV-dasig, INV-gujus).

The module also hosts the per-repo XDG cache layout under
``$XDG_CACHE_HOME/hypergumbo/<fingerprint>/`` (``_get_repo_fingerprint``,
``_get_xdg_cache_base``): the analysis-results cache
(``_get_results_cache_dir``), keyed by repo state hash, resolved opt-in
backend set, trace_deps token,
digests of the analysis-stage user catalogue channels and the config tiers,
a digest of the run's ``--exclude`` patterns, and analyzer identity.
``cli.py`` and ``sketch.py`` import the results-cache helpers from here;
they have nothing to do with embeddings.

Degradation contract — TWO failure modes, not one
-------------------------------------------------
"sentence-transformers is unavailable" means two independent things. A
caller that handles only the first lets the second escape to the CLI top
level, so ``hypergumbo .`` exits 1 without writing a sketch (INV-rupid):

1. **The library is not importable.** ``_has_sentence_transformers()``
   answers this, and only this — it catches ``ImportError`` and nothing
   else. ``extract_readme_description_embedding`` checks it first.
2. **The library imports but the model WEIGHTS are not on disk** (a
   machine that has never run ``hypergumbo install-embeddings``, or a
   cleared cache). Analysis never downloads them.
   ``_has_sentence_transformers()`` returns True in this case. The load
   itself raises, so
   ``_load_st_model_offline_first`` converts that into
   :class:`EmbeddingsUnavailable`.

Callers must handle BOTH. ``extract_readme_description_embedding`` does: it
catches :class:`EmbeddingsUnavailable` and degrades to the result its
import-guard branch returns, and :func:`_warn_embeddings_unavailable`
warns once per process rather than per call. Consumers outside this module
(see ``sketch.py``) degrade to their heuristic path.

The probe is deliberately NOT strengthened to demand weights: a first
install legitimately has the library and no weights, and the remedy,
``hypergumbo install-embeddings``, is named by the loader's error and the
one-time warning. The guard answers "can I import"; the loader
answers "can I load".
"""

from __future__ import annotations

import logging
import os
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    import numpy as np

# Model name constant
_EMBEDDING_MODEL = "microsoft/unixcoder-base"

# Regex pattern for IPv6 CIDR (contains :: and /prefix, e.g., fd00:200::/40)
_IPV6_CIDR_PATTERN = re.compile(r"[0-9a-fA-F:]*::[0-9a-fA-F:]*/\d+")

# Set HF env vars BEFORE importing sentence_transformers downstream — most
# HuggingFace libraries cache these at their own import time (WI-gatot).
from ._hf_noise import suppress_hf_noise as _suppress_hf_noise  # noqa: E402
from .safety_zones import (  # noqa: E402
    cache_mkdir,
    repo_inspect_git,
)

_suppress_hf_noise()


def _sanitize_no_proxy_for_httpx() -> tuple[str | None, str | None]:
    """Temporarily remove IPv6 CIDR entries from NO_PROXY for httpx compatibility.

    httpx has a bug where IPv6 CIDR notation (e.g., fd00:200::/40) in NO_PROXY
    causes InvalidURL errors during Client initialization. This function removes
    such entries temporarily and returns the original values for restoration.

    Returns:
        Tuple of (original NO_PROXY, original no_proxy) for restoration.
    """
    old_values: tuple[str | None, str | None] = (
        os.environ.get("NO_PROXY"),
        os.environ.get("no_proxy"),
    )

    for var in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(var)
        if value:
            # Filter out IPv6 CIDR entries
            entries = [e.strip() for e in value.split(",")]
            filtered = [e for e in entries if not _IPV6_CIDR_PATTERN.match(e)]
            if len(filtered) < len(entries):
                os.environ[var] = ",".join(filtered)

    return old_values


def _restore_no_proxy(old_values: tuple[str | None, str | None]) -> None:
    """Restore NO_PROXY environment variables to their original values."""
    old_no_proxy, old_no_proxy_lower = old_values
    if old_no_proxy is not None:
        os.environ["NO_PROXY"] = old_no_proxy
    elif "NO_PROXY" in os.environ:
        del os.environ["NO_PROXY"]

    if old_no_proxy_lower is not None:
        os.environ["no_proxy"] = old_no_proxy_lower
    elif "no_proxy" in os.environ:
        del os.environ["no_proxy"]


class EmbeddingsUnavailable(RuntimeError):
    """The embedding model could not be loaded, so semantic work must degrade.

    Raised by :func:`_load_st_model_offline_first` when the local HF cache
    does not hold the weights — analysis never downloads them, so this is the
    case for a machine that has not run ``hypergumbo install-embeddings``. It
    exists so that consumers can tell "the enhancement is unavailable" apart
    from a genuine bug, and answer with the SAME empty value their
    ``_has_sentence_transformers()`` guard already returns.

    An untyped ``OSError`` from the loader would instead reach the CLI top
    level and fail the default ``hypergumbo .`` command with no sketch written,
    for an enhancement that is optional in a local-first tool (INV-rupid).

    The originating exception is always attached as ``__cause__``; callers that
    report to the user should surface it rather than inventing a message.
    """


_EMBEDDINGS_UNAVAILABLE_WARNED = False


def _warn_embeddings_unavailable(exc: BaseException) -> None:
    """Warn ONCE per process that semantic enhancement is being skipped.

    Once, not per call site: six consumers degrade independently and a
    per-consumer warning would bury the sketch it is attached to. The remedy is
    named because "couldn't connect to huggingface.co" tells a user what failed
    but not what to do about it.
    """
    global _EMBEDDINGS_UNAVAILABLE_WARNED
    if _EMBEDDINGS_UNAVAILABLE_WARNED:
        return
    _EMBEDDINGS_UNAVAILABLE_WARNED = True
    warnings.warn(
        "Embedding model unavailable — continuing without semantic "
        f"enhancement. Cause: {exc}. The sketch is complete but omits "
        "semantic config discovery and README description extraction. "
        "To enable it, run 'hypergumbo install-embeddings' on a machine with "
        "network access: it downloads the model weights into $HF_HOME. "
        "Analysis never downloads them itself (runtime-cli-no-network).",
        UserWarning,
        stacklevel=2,
    )


_cached_embedding_model = None


def _load_embedding_model():
    """Load SentenceTransformer model with warnings suppressed.

    The sentence-transformers library logs a warning when creating a new model
    wrapper for models it doesn't recognize. This is expected for UnixCoder
    and not useful to users.

    The warning is suppressed by setting log level BEFORE importing/loading,
    and by capturing any stdout output during initialization.

    Returns a cached singleton after the first call — the model is ~500MB in
    memory but is reused across config discovery, config extraction, and README
    analysis within a single process invocation.

    Note:
        Temporarily sanitizes NO_PROXY to work around httpx bug with IPv6 CIDR
        notation (e.g., fd00:200::/40) which causes InvalidURL parsing errors.
    """
    global _cached_embedding_model
    if _cached_embedding_model is not None:
        return _cached_embedding_model

    # Force HF offline BEFORE importing sentence_transformers, which imports
    # huggingface_hub and freezes its offline constant at import time
    # (INV-dasig).
    _ensure_hf_offline_for_cached_models()

    # Suppress warnings BEFORE importing to catch all submodule loggers
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)

    from sentence_transformers import SentenceTransformer
    import sys
    import io

    # Work around httpx bug with IPv6 CIDR in NO_PROXY
    old_no_proxy = _sanitize_no_proxy_for_httpx()

    # Capture any stdout during model loading (library prints to stdout)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        model = _load_st_model_offline_first(SentenceTransformer, _EMBEDDING_MODEL)
    finally:
        sys.stdout = old_stdout
        _restore_no_proxy(old_no_proxy)
    _cached_embedding_model = model
    return model


def _load_st_model_offline_first(st_cls, model_name, **kwargs):
    """Construct a SentenceTransformer model, preferring the local cache.

    sentence_transformers' ``SentenceTransformer(name)`` constructor
    contacts HuggingFace Hub on every invocation for a cache-freshness
    check (a HEAD request on the model's manifest). Even when the model
    is fully cached locally, this produces network traffic and the
    library logs an "unauthenticated requests to the HF Hub" warning if
    no HF_TOKEN is set. For ``hypergumbo .`` runs that's noise on every
    invocation.

    Strategy: the constructor with ``local_files_only=True`` (which
    suppresses the freshness check entirely), and ONLY that. If the model is
    not cached, HuggingFace Hub raises ``LocalEntryNotFoundError`` (a subclass
    of ``OSError``) and the enhancement is unavailable: this raises
    :class:`EmbeddingsUnavailable` naming ``hypergumbo install-embeddings``,
    which is where the weights are fetched (:func:`fetch_embedding_models`).

    There is deliberately no network-allowed fallback: ``runtime-cli-no-network``
    says analysis never downloads, and a retry without ``local_files_only``
    would fetch both models from huggingface.co on the first ``sketch`` after
    the library is installed (INV-gujus).

    Catching the broader ``(OSError, ValueError)`` covers all the
    documented "no local entry" exception variants without forcing a
    hard import of ``huggingface_hub.errors`` (which would create an
    implicit dep on a specific HF Hub version).

    ``local_files_only=True`` is necessary but NOT sufficient. It only
    suppresses the constructor's freshness HEAD; the HF Hub metadata API
    (model card / commits / discussions), the xet daemon's cache-freshness
    ping, and the ``transformers`` safetensors auto-conversion *background
    thread* fired during ``encode()`` all still reach ``huggingface.co``
    otherwise — so a plain ``hypergumbo run`` / sketch would leak outbound
    network on every invocation, violating the ``runtime-cli-no-network``
    claim (INV-dasig). ``HF_HUB_OFFLINE=1`` is the only switch that
    hard-disables every HF Hub sub-API, so we set it before the cached
    load and (on success) leave it set for the rest of the process. On a
    miss the caller's prior setting is restored before raising.
    """
    prior_offline = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        model = st_cls(model_name, local_files_only=True, **kwargs)
    except (OSError, ValueError) as exc:
        if prior_offline is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prior_offline
        # The enhancement being unavailable is NOT a bug, so it must not
        # reach the CLI's internal-error handler. A typed error rather than
        # None keeps the failure distinguishable and preserves the original
        # for the operator via __cause__ (INV-rupid).
        raise EmbeddingsUnavailable(
            f"embedding model {model_name!r} is not in the local cache; "
            "run 'hypergumbo install-embeddings' to download it"
        ) from exc
    # Cached-load success: keep HF_HUB_OFFLINE=1 set so encode()-time HF
    # background threads stay offline too.
    return model


def _hf_model_is_cached(model_name: str) -> bool:
    """Filesystem-only probe: is ``model_name`` already in the HF Hub cache?

    Checks the standard hub layout
    ``$HF_HOME/hub/models--<org>--<name>/snapshots/<rev>/`` for a non-empty
    revision directory, WITHOUT importing huggingface_hub or touching the
    network. Used by :func:`fetch_embedding_models` to skip models that are
    already downloaded.
    """
    hf_home = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface",
    )
    snapshots = os.path.join(
        hf_home, "hub", "models--" + model_name.replace("/", "--"), "snapshots",
    )
    if not os.path.isdir(snapshots):
        return False
    for rev in os.listdir(snapshots):
        rev_dir = os.path.join(snapshots, rev)
        if os.path.isdir(rev_dir) and os.listdir(rev_dir):
            return True
    return False


#: The values huggingface_hub reads as true (``constants.ENV_VARS_TRUE_VALUES``,
#: matched upper-cased). Copied, not imported: importing huggingface_hub here
#: would freeze its offline switch before this module could set it.
_HF_TRUE_VALUES = frozenset({"1", "ON", "YES", "TRUE"})


def _ensure_hf_offline_for_cached_models() -> bool:
    """Set ``HF_HUB_OFFLINE=1`` BEFORE the first huggingface_hub import (INV-dasig).

    huggingface_hub freezes its offline switch into the module constant
    ``huggingface_hub.constants.HF_HUB_OFFLINE`` at IMPORT time and does not
    re-read ``os.environ`` afterward — so setting the env var inside the model
    loader (after ``from sentence_transformers import ...``) is too late and
    the cached load still reaches the network, as does the encode()-time
    safetensors auto-conversion background thread. This helper therefore runs
    at the top of every function that imports sentence_transformers to load a
    model, BEFORE the import. (cli's install-path availability checks import
    the library and load nothing.)

    Offline is forced unconditionally, whether or not every model is cached:
    analysis never downloads (the weights come from ``install-embeddings``,
    :func:`fetch_embedding_models`), so there is no download to protect, and
    leaving it off while a model is missing would open the hub to metadata
    traffic (INV-gujus).

    A value the user already set is kept only when huggingface_hub reads it as
    offline (:data:`_HF_TRUE_VALUES`, compared upper-cased as the library
    does). Anything else -- ``0``, ``false``, ``no`` -- is replaced with ``1``,
    because honouring it leaves the hub reachable: a plain ``sketch`` connects
    to the configured endpoint even with the models loaded from cache.

    Returns True: offline mode is in effect (set here or already set).
    """
    if os.environ.get("HF_HUB_OFFLINE", "").upper() not in _HF_TRUE_VALUES:
        os.environ["HF_HUB_OFFLINE"] = "1"
    return True


# Probe patterns for embedding-based config extraction
# These are embedded and compared against config file content
#
# WARNING: If you modify any probe patterns (ANSWER_PATTERNS, BIG_PICTURE_QUESTIONS,
# or README_DESCRIPTION_PROBES), you MUST regenerate the precomputed embeddings:
#     python scripts/compute_probe_embeddings.py
# Otherwise the embeddings in _embedding_data.py will be out of sync with the probes.

ANSWER_PATTERNS = [
    # Project identity
    "project name declaration",
    "package name",
    "module name",
    "application name",

    # Versioning
    "version number",
    "semantic version",
    "edition or language version",
    "minimum required version",

    # Dependencies
    "dependency declaration",
    "package dependency",
    "library dependency",
    "dev dependency",
    "build dependency",
    "optional dependency",

    # Licensing
    "license identifier",
    "SPDX license expression",
    "open source license",

    # Build configuration
    "build system configuration",
    "build target",
    "compilation settings",
    "entry point",
    "main module",
    "script definition",
    "command definition",

    # Runtime configuration
    "environment variable",
    "configuration option",
    "feature flag",
    "runtime setting",

    # Repository and authorship
    "repository URL",
    "homepage URL",
    "author name",
    "maintainer",
    "contributors list",

    # Documentation
    "project description",
    "readme file",

    # Discovery
    "package keywords",
    "package categories",
    "package tags",

    # Exports and binaries
    "binary executable",
    "library exports",
    "public API",
]

# Open-ended questions for big-picture/architectural context
# NOTE: No license questions here - ANSWER_PATTERNS already captures compact
# license declarations (e.g., 'license = "MIT"') without matching verbose
# LICENSE file boilerplate.
BIG_PICTURE_QUESTIONS = [
    # Machine learning and AI
    "What ML framework does this use?",
    "Does this use PyTorch?",
    "Does this use TensorFlow?",
    "Does this use JAX?",
    "Does this use scikit-learn?",
    "Does this use Hugging Face Transformers?",
    "What model architecture does this implement?",
    "Does this support GPU acceleration?",
    "Does this support TPU?",
    "Does this use CUDA?",
    "What quantization methods are supported?",
    "Does this use ONNX?",
    "What inference runtime does this use?",

    # Version and release info
    "What version is this project?",
    "What is the current version number?",
    "When was the last release?",
    "What version of Node.js does this require?",
    "What Python version is required?",
    "What is the minimum supported version?",

    # Database and storage
    "What database does this project use?",
    "Does this use PostgreSQL?",
    "Does this use MySQL?",
    "Does this use MongoDB?",
    "Does this use Redis?",
    "Does this use SQLite?",
    "What ORM does this use?",
    "How does this store data?",

    # Web frameworks and HTTP
    "What web framework does this use?",
    "Is this built with Express?",
    "Is this built with FastAPI?",
    "Is this built with Django?",
    "Is this built with Flask?",
    "Is this built with Rails?",
    "Is this built with Spring?",
    "Is this a REST API?",
    "Does this use GraphQL?",

    # Frontend frameworks
    "What frontend framework does this use?",
    "Is this built with React?",
    "Is this built with Vue?",
    "Is this built with Angular?",
    "Is this built with Svelte?",
    "Does this use TypeScript?",
    "What CSS framework does this use?",

    # Testing
    "What testing framework does this use?",
    "Does this use Jest?",
    "Does this use pytest?",
    "Does this use JUnit?",
    "How do I run the tests?",
    "What is the test coverage?",

    # Build and tooling
    "What build system does this use?",
    "Does this use webpack?",
    "Does this use Vite?",
    "Does this use Maven?",
    "Does this use Gradle?",
    "Does this use Cargo?",
    "How do I build this project?",

    # Package management
    "What package manager does this use?",
    "Does this use npm or yarn?",
    "Does this use pnpm?",
    "Does this use pip?",
    "What are the main dependencies?",
    "What are the dev dependencies?",

    # Language and runtime
    "What programming language is this?",
    "What runtime does this require?",
    "Is this a TypeScript project?",
    "Is this a Python project?",
    "Is this a Go project?",
    "Is this a Rust project?",
    "Is this a Java project?",

    # Project identity
    "What is this project called?",
    "What is the project name?",
    "Who maintains this project?",
    "What organization owns this?",
    "Who are the contributors?",

    # Deployment and infrastructure
    "How do I deploy this?",
    "Does this use Docker?",
    "Does this use Kubernetes?",
    "What cloud platform does this target?",
    "Is this serverless?",
    "Does this run on AWS?",
    "Does this run on GCP?",
    "Does this run on Azure?",
    "Does this use Terraform?",
    "Does this use Helm?",
    "What container registry does this use?",
    "Does this use GitHub Actions?",
    "Does this use GitLab CI?",
    "What infrastructure as code tool is used?",

    # API and protocols
    "What API does this expose?",
    "Does this use WebSockets?",
    "Does this use gRPC?",
    "What ports does this use?",

    # Miscellaneous metadata
    "What is the project description?",
    "What problem does this solve?",
    "Is this a library or application?",
    "Is this a CLI tool?",
    "Is this production ready?",

    # Architecture and design (harder, open-ended)
    "What is the overall architecture of this project?",
    "How is the codebase organized?",
    "What design patterns does this use?",
    "How do the components communicate?",
    "What is the data flow through the system?",
    "How does authentication work?",
    "How does authorization work?",
    "What are the main modules or services?",
    "Is this a monolith or microservices?",
    "How is state managed?",

    # Scale and complexity
    "How large is this codebase?",
    "How many services does this have?",
    "What are the performance characteristics?",
    "How does this handle concurrency?",
    "What are the scaling considerations?",

    # Integration and external systems
    "What external services does this integrate with?",
    "What third-party APIs does this call?",
    "How does this communicate with other systems?",
    "What message queues or event buses are used?",
    "What caching strategy is used?",

    # Security and reliability
    "How are secrets managed?",
    "What security measures are in place?",
    "How are errors handled?",
    "What logging and monitoring is used?",
    "How is configuration managed across environments?",

    # Development workflow
    "How do I set up the development environment?",
    "What are the contribution guidelines?",
    "How is code review done?",
    "What CI/CD pipeline is used?",
    "How are database migrations handled?",
]

# Negative probe patterns: chunks matching these are penalized.
# These represent content that shares vocabulary with config probes
# but is NOT actual project configuration (docs, schemas, tests, etc.).
#
# WARNING: If you modify NEGATIVE_PATTERNS, you MUST regenerate precomputed
# embeddings: python scripts/compute_probe_embeddings.py
NEGATIVE_PATTERNS = [
    "JSON schema type definition with required properties",
    "API documentation example showing request and response",
    "output format specification describing data structure",
    "test fixture data for unit testing",
    "error message string for exception handling",
    "legal license text boilerplate copyright notice",
    "changelog release notes version history",
    "code comment explaining implementation details",
    "markdown documentation section heading",
    "function signature with parameters and return type",
    "class definition with methods and properties",
    "HTML template markup with tags and attributes",
]


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers is available."""
    # Force HF offline before this import freezes the huggingface_hub offline
    # constant — this is often the process's first one (INV-dasig).
    _ensure_hf_offline_for_cached_models()
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _decode_probe_embeddings(b64_data: str, num_probes: int) -> "np.ndarray":
    """Decode pre-computed probe embeddings from base64 float16.

    Args:
        b64_data: Base64-encoded float16 array.
        num_probes: Number of probes (for reshape).

    Returns:
        Normalized float32 embeddings array of shape (num_probes, 768).
    """
    import base64
    import numpy as np

    raw = base64.b64decode(b64_data)
    arr = np.frombuffer(raw, dtype=np.float16).reshape(num_probes, 768)
    return arr.astype(np.float32)


# Probe patterns for README description extraction
# These are mission statements from well-known open source projects
# Used to identify lines that describe what a project does
#
# WARNING: Changes require regenerating embeddings! Run:
#     python scripts/compute_probe_embeddings.py
README_DESCRIPTION_PROBES = [
    # Canonical “what it is + what it does + why it matters”
    "(Project Name) is an open-source (tool type/category) built for (user demographic) to (do their job) in (relevant circumstances). It offers (top 2-3 capabilities) so you can (primary benefit) with (reliability/security/scale/simplicity).",
    "(Project Name) is an open-source (tool type/category) for (audience/context) that (does something). With (top 2-3 capabilities), it helps you (primary benefit) while keeping things (reliable/secure/scalable/simple).",
    "(Project Name) is a (tool type/category) that enables (user demographic) to (do their job) in (relevant circumstances). It combines (top 2-3 capabilities) to deliver (primary benefit) at (scale/security/reliability/simplicity).",

    # Concise one-liners (common when repos lead with a tight thesis)
    "(Project Name) is a (tool type/category) for (audience/context) that (does something)—so you can (benefit).",
    "(Project Name) is a (tool type/category) that (does something) in (context) for (audience), helping you (benefit).",

    # Two-beat openers (matches “local-first CLI…” style)
    "A (adjective) (tool type/category) that (does something) from (input/source). Helps (audience) (achieve outcome) in (context).",
    "(Does something) in (context). So (audience) can (benefit).",

    # Tagline-led (common in trendy repos)
    "(Project Name): (tagline describing outcome in 3-7 words).",
    "(Punchy label/tagline). A (adjective) (tool type/category) for (audience/context) that (does something), so you can (benefit).",

    # Noun-phrase + promise (very “README-first”)
    "A (tool type/category) for (job-to-be-done) in (context). (Primary outcome/benefit), for (audience).",

    # Problem-first / user-need framing
    "If you need to (problem/job) in (context), (Project Name) helps by (core mechanism), so you can (benefit).",
    "For (audience) who need to (job) in (context), (Project Name) (does something) to (benefit).",

    # Purpose / mission framing (often used instead of “is a…”)
    "Built to (primary job) for (audience) in (context), (Project Name) provides (capabilities) to deliver (benefit).",
    "The goal of (Project Name) is to (primary outcome) for (audience) working in (context) by (mechanism).",

    # Category implied (library/framework/service language)
    "This (library/framework/service) lets you (do something) by (mechanism), making it easier to (benefit).",

    # Positioning by analogy (“X for Y”, “like A but B”)
    "(Project Name) is (known thing/category) for (new domain/audience)—like (comparison), but (key difference).",

    # Feature-bundle opener (some READMEs list capabilities before benefits)
    "(Project Name) is a (tool type/category) for (audience/context) that includes (capability 1), (capability 2), and (capability 3).",

    # Trust/attribute hook (leads with adjectives instead of function)
    "Fast, (secure/reliable/simple), and (scalable/portable), (Project Name) is a (tool type/category) for (audience) to (do job).",

    # Imperative “use it to…” (instructional openers)
    "Use (Project Name) to (primary job) in (context)—for example, (example use case).",
]

# Cache for probe embeddings (decoded from pre-computed base64)
_README_PROBE_EMBEDDINGS: "np.ndarray | None" = None
_ANSWER_PROBE_EMBEDDINGS: "np.ndarray | None" = None
_BIGPIC_PROBE_EMBEDDINGS: "np.ndarray | None" = None


def _get_readme_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for README description extraction.

    Uses base64-encoded float16 embeddings from _embedding_data.py,
    avoiding the ~2-3s startup cost of computing embeddings at runtime.

    Returns:
        Normalized probe embeddings array of shape (19, 768).
    """
    global _README_PROBE_EMBEDDINGS

    if _README_PROBE_EMBEDDINGS is None:
        from ._embedding_data import README_PROBES_B64
        _README_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            README_PROBES_B64, len(README_DESCRIPTION_PROBES)
        )

    return _README_PROBE_EMBEDDINGS


def _get_answer_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for config answer patterns.

    Returns:
        Normalized probe embeddings array of shape (41, 768).
    """
    global _ANSWER_PROBE_EMBEDDINGS

    if _ANSWER_PROBE_EMBEDDINGS is None:
        from ._embedding_data import ANSWER_PROBES_B64
        _ANSWER_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            ANSWER_PROBES_B64, len(ANSWER_PATTERNS)
        )

    return _ANSWER_PROBE_EMBEDDINGS


def _get_bigpic_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for big picture questions.

    Returns:
        Normalized probe embeddings array of shape (127, 768).
    """
    global _BIGPIC_PROBE_EMBEDDINGS

    if _BIGPIC_PROBE_EMBEDDINGS is None:
        from ._embedding_data import BIGPIC_PROBES_B64
        _BIGPIC_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            BIGPIC_PROBES_B64, len(BIG_PICTURE_QUESTIONS)
        )

    return _BIGPIC_PROBE_EMBEDDINGS


def _is_readme_line_filterable(line: str) -> bool:
    """Check if a README line should be filtered out before embedding.

    Filters badges, empty lines, pure-image lines, link reference definitions,
    and GitHub callout syntax.
    Does NOT filter HTML with text content (may contain descriptions).

    Args:
        line: The line to check.

    Returns:
        True if the line should be skipped.
    """
    import re

    stripped = line.strip()

    # Skip empty lines
    if not stripped:
        return True

    # Skip badge-only lines: [![alt](url)](link) or ![alt](url)
    if re.match(r"^!?\[!\[.*?\]\(.*?\)\]\(.*?\)$", stripped):
        return True
    if re.match(r"^!\[.*?\]\(.*?\)$", stripped):
        return True

    # Skip pure link lines (often badge URLs)
    if re.match(r"^\[.*?\]\(https?://.*?\)$", stripped):
        return True

    # Skip markdown link reference definitions: [label]: https://...
    # These are common at the top of READMEs but contain no description content
    if re.match(r"^\[.+?\]:\s*https?://", stripped):
        return True

    # Skip GitHub callout syntax: > [!NOTE], > [!IMPORTANT], > [!WARNING], etc.
    # These are typically announcements, not project descriptions
    if re.match(r"^>\s*\[!", stripped):
        return True

    # Skip HTML comments
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return True

    # Skip lines that are just <img> or <a> tags with image content
    if re.match(r"^<(img|a|picture|source)\s.*?/?>$", stripped, re.IGNORECASE):
        return True

    return False


def _dedupe_repeated_prefix(text: str, max_phrase_len: int = 10) -> str:
    """Remove repeated word sequences at the start of text.

    When README headers get merged with content, we can end up with:
    "hypergumbo hypergumbo is a local-first CLI..."

    This function detects and removes such repetitions for phrase lengths
    1 through max_phrase_len.

    Examples:
        "foo foo foo bar" → "foo bar"
        "foo bar foo bar baz" → "foo bar baz"
        "a b c a b c d" → "a b c d"

    Args:
        text: The text to deduplicate.
        max_phrase_len: Maximum phrase length to check (1 to this value).

    Returns:
        Text with repeated prefixes collapsed to single occurrence.
    """
    if not text:
        return text

    words = text.split()
    if len(words) < 2:
        return text

    # Check for repeated prefixes of length 1, 2, ..., max_phrase_len
    for phrase_len in range(1, min(max_phrase_len + 1, len(words) // 2 + 1)):
        phrase = words[:phrase_len]
        # Count how many times this phrase repeats consecutively at the start
        repeat_count = 1
        pos = phrase_len
        while pos + phrase_len <= len(words):
            if words[pos:pos + phrase_len] == phrase:
                repeat_count += 1
                pos += phrase_len
            else:
                break

        if repeat_count > 1:
            # Found repetition - remove all but one occurrence
            remaining = words[pos:]
            return " ".join(phrase + remaining)

    return text


class ReadmeExtractionDebug:
    """Debug info from README description extraction."""

    def __init__(
        self,
        description: str | None,
        k_scores: list[tuple[int, float]],
        final_k: int,
        stopped_early: bool,
        quality_drop: float | None,
        elapsed_seconds: float,
        lines_processed: int,
    ):
        self.description = description
        self.k_scores = k_scores  # List of (k, best_score) for each k tried
        self.final_k = final_k  # The k value that was used
        self.stopped_early = stopped_early  # True if stopped due to quality drop
        self.quality_drop = quality_drop  # The drop that triggered early stop, if any
        self.elapsed_seconds = elapsed_seconds
        self.lines_processed = lines_processed

    def __repr__(self) -> str:
        return (
            f"ReadmeExtractionDebug(k={self.final_k}, "
            f"stopped_early={self.stopped_early}, "
            f"quality_drop={self.quality_drop}, "
            f"elapsed={self.elapsed_seconds:.2f}s)"
        )


def extract_readme_description_embedding(
    readme_path: Path,
    max_lines: int = 80,
    max_window: int = 15,
    quality_drop_threshold: float = 0.07,
    min_quality_threshold: float = 0.12,
    top_k_probes: int = 3,
    position_bias: float = 0.4,
    debug: bool = False,
) -> str | ReadmeExtractionDebug | None:
    """Extract project description from README using embedding similarity.

    Uses probe embeddings from mission statements of well-known projects to
    identify lines that describe what the project does. Finds the best
    consecutive window of lines (up to max_window) using a sliding window
    approach that stops when quality drops significantly.

    Position bias ensures earlier lines are favored, since descriptions
    typically appear near the top of READMEs, right after the title.

    Args:
        readme_path: Path to the README file.
        max_lines: Maximum lines from README to consider (default 80).
        max_window: Maximum window size k (default 15).
        quality_drop_threshold: Stop when score drops by this fraction (default 0.07).
        top_k_probes: Number of top probe similarities to average (default 3).
        position_bias: Penalty for later lines (default 0.4 = 40% penalty at end).
        debug: If True, return ReadmeExtractionDebug with k-value scores and timing.

    Returns:
        If debug=False: Extracted description string, or None if extraction fails.
        If debug=True: ReadmeExtractionDebug object with description and debug info.
    """
    import time as _time
    start_time = _time.time()

    if not _has_sentence_transformers():
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=0, lines_processed=0
            )
        return None

    import numpy as np

    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=0
            )
        return None

    lines = content.split("\n")[:max_lines]

    # Track code block state for filtering
    in_code_block = False
    filtered_lines: list[tuple[int, str]] = []  # (original_idx, line)

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Track fenced code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        # Skip lines inside code blocks
        if in_code_block:
            continue

        # Skip filterable lines (badges, empty, pure images)
        if _is_readme_line_filterable(line):
            continue

        filtered_lines.append((idx, stripped))

    if not filtered_lines:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=0
            )
        return None

    # Merge header lines with their following content
    # Headers alone are structural, not descriptive - expand them to include
    # the paragraph that follows, similar to license chunk expansion.
    merged_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(filtered_lines):
        idx, line = filtered_lines[i]
        if line.startswith("#"):
            # This is a header - merge with following non-header lines
            merged_content = [line]
            j = i + 1
            # Include following lines until we hit another header or run out
            while j < len(filtered_lines):
                next_idx, next_line = filtered_lines[j]
                if next_line.startswith("#"):
                    break  # Stop at next header
                merged_content.append(next_line)
                j += 1
            # Only include the merged chunk if we found content after the header
            if len(merged_content) > 1:
                merged_lines.append((idx, " ".join(merged_content)))
                i = j  # Skip the lines we merged
            else:
                # Header with no following content - skip it entirely
                i += 1
        else:
            # Non-header line - keep as-is
            merged_lines.append((idx, line))
            i += 1

    filtered_lines = merged_lines

    if not filtered_lines:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=0
            )
        return None

    # Load model and get pre-computed probe embeddings. An unavailable model
    # answers with the SAME shape the _has_sentence_transformers() guard above
    # returns, so there is exactly one definition of "no description could be
    # extracted" (INV-rupid).
    try:
        model = _load_embedding_model()
    except EmbeddingsUnavailable as exc:
        _warn_embeddings_unavailable(exc)
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=len(filtered_lines),
            )
        return None
    probe_embeddings = _get_readme_probe_embeddings()

    # Embed all filtered lines
    line_texts = [line for _, line in filtered_lines]
    line_embeddings = model.encode(line_texts, convert_to_numpy=True)
    line_norms = np.linalg.norm(line_embeddings, axis=1, keepdims=True)
    normalized_lines = line_embeddings / (line_norms + 1e-8)

    # Compute pairwise cosine similarities: (num_lines, num_probes)
    similarities = np.dot(normalized_lines, probe_embeddings.T)

    # Score each line as mean of top-k similarities with probes
    top_k = min(top_k_probes, len(README_DESCRIPTION_PROBES))
    line_scores = np.mean(np.sort(similarities, axis=1)[:, -top_k:], axis=1)

    # Apply position bias - earlier lines are more likely to be descriptions
    # Two-part bias:
    # 1. Title-proximity bonus: lines 1-5 after title get 25% boost (not the title itself)
    # 2. Exponential decay based on ABSOLUTE position (not relative to doc length)
    #    This ensures consistent behavior regardless of README length
    if position_bias > 0 and len(filtered_lines) > 1:
        # Use absolute positions, normalized to a fixed scale (assume ~20 lines is typical)
        # Lines 0-5 get minimal penalty, lines 20+ get maximum penalty
        scale_factor = 20.0  # Typical number of meaningful lines in a README
        absolute_positions = np.arange(len(filtered_lines)) / scale_factor
        # Cap at 1.0 to avoid over-penalizing very long READMEs
        absolute_positions = np.minimum(absolute_positions, 1.0)
        # Exponential decay based on absolute position
        position_weights = np.exp(-position_bias * 2 * absolute_positions)
        # Title-proximity bonus for early lines (positions 0-4)
        # Headers are merged with their following content, so position 0 is
        # typically the title+description chunk. We boost early positions
        # since descriptions usually appear near the top of READMEs.
        title_bonus = np.ones(len(filtered_lines))
        title_bonus[0:5] = 1.25  # 25% boost for lines 0-4
        line_scores = line_scores * position_weights * title_bonus

    # Sliding window to find best consecutive k lines
    best_window: tuple[int, int] | None = None  # (start_idx, end_idx)
    prev_best_score = -1.0
    k_scores: list[tuple[int, float]] = []  # Track scores for debug
    stopped_early = False
    quality_drop_value: float | None = None
    final_k = 0

    for k in range(1, max_window + 1):
        if k > len(filtered_lines):
            break

        # Find best window of size k
        window_scores = []
        for start in range(len(filtered_lines) - k + 1):
            window_score = float(np.mean(line_scores[start : start + k]))
            window_scores.append((start, window_score))

        if not window_scores:
            break

        # Get best window for this k
        best_start, best_k_score = max(window_scores, key=lambda x: x[1])
        k_scores.append((k, best_k_score))

        # Check for quality drop (only after k=1)
        if k > 1 and prev_best_score > 0:
            drop = (prev_best_score - best_k_score) / prev_best_score
            if drop >= quality_drop_threshold:
                # Quality dropped too much, use previous k
                stopped_early = True
                quality_drop_value = drop
                break

        # Update best
        best_window = (best_start, best_start + k)
        prev_best_score = best_k_score
        final_k = k

    if best_window is None:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=k_scores, final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=len(filtered_lines)
            )
        return None

    # Reject low-quality descriptions (score below threshold)
    # This prevents non-descriptive content like "pip install foo" from being
    # selected as descriptions when there's no real description paragraph.
    if prev_best_score < min_quality_threshold:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=k_scores, final_k=final_k,
                stopped_early=stopped_early, quality_drop=quality_drop_value,
                elapsed_seconds=_time.time() - start_time,
                lines_processed=len(filtered_lines)
            )
        return None

    # Extract the winning lines
    start_idx, end_idx = best_window
    selected_lines = [line for _, line in filtered_lines[start_idx:end_idx]]

    # Join and return
    description = " ".join(selected_lines)

    # Handle Markdown soft line breaks: if the description ends mid-sentence
    # (no sentence-ending punctuation), try to extend it with following lines
    # until we reach a sentence boundary or run out of content.
    sentence_endings = (".", "!", "?", ":")
    max_extension_lines = 5  # Limit how far we extend
    extended_count = 0
    current_end_idx = end_idx

    while (
        description
        and not description.rstrip().rstrip(")]}").endswith(sentence_endings)
        and current_end_idx < len(filtered_lines)
        and extended_count < max_extension_lines
    ):
        next_line = filtered_lines[current_end_idx][1]
        # Stop if we hit a header or section break
        if next_line.startswith("#") or next_line.startswith("---"):
            break
        description = description + " " + next_line
        current_end_idx += 1
        extended_count += 1

    # Cleanup: strip HTML tags and excessive whitespace
    import re
    # Remove HTML tags but keep content
    description = re.sub(r"<[^>]+>", "", description)
    # Collapse whitespace
    description = " ".join(description.split())

    # Strip leading markdown header if present (from merged header+content)
    # Headers were merged with content for selection, but the header text
    # itself shouldn't appear in the final elevator pitch.
    if description.startswith("#"):
        # Match header patterns:
        # - "## catatonit ## A container init..." -> "A container init..."
        # - "# Title Some content..." -> "Some content..."
        # The first alternative handles "## Word(s) ##" (closing hash) format
        # The second alternative handles simple "# " prefix
        header_match = re.match(r"^(#+\s+\S+\s*#+\s*|#+\s+)", description)
        if header_match:
            description = description[header_match.end():].lstrip()

    # Deduplicate repeated word sequences at the start (e.g., "foo foo bar" → "foo bar")
    # This handles cases where the header title gets duplicated with the opening text
    description = _dedupe_repeated_prefix(description)

    final_description = description if description else None

    if debug:
        return ReadmeExtractionDebug(
            description=final_description,
            k_scores=k_scores,
            final_k=final_k,
            stopped_early=stopped_early,
            quality_drop=quality_drop_value,
            elapsed_seconds=_time.time() - start_time,
            lines_processed=len(filtered_lines)
        )

    return final_description


# ==============================================================================
# Per-repo cache layout (nothing to do with embeddings; see the module docstring)
# ==============================================================================

def _find_git_executable() -> str:
    """Find the full path to git executable.

    Returns:
        Full path to git, or "git" if not found (will fail gracefully).
    """
    import shutil
    return shutil.which("git") or "git"


def _run_git_command(
    args: list[str], cwd: Path, timeout: int = 5
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr).

    Args:
        args: Git command arguments (without 'git' prefix).
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (return_code, stdout, stderr).
    """
    git_path = _find_git_executable()
    try:
        # repo_inspection safety zone (WI-fasuv).
        result = repo_inspect_git(
            [git_path, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception:  # pragma: no cover
        return 1, "", ""


def _get_repo_fingerprint(repo_root: Path) -> str:
    """Generate a stable fingerprint for a repository.

    For git repositories, uses the remote origin URL and first commit SHA to create
    a stable identifier that doesn't change when files are modified. This allows
    the cache to be shared across checkouts of the same repo.

    For non-git directories, falls back to hashing the absolute path.

    Args:
        repo_root: Repository root path.

    Returns:
        A 16-character hex fingerprint string.
    """
    import hashlib

    # Check if this is a git repo
    git_dir = repo_root / ".git"
    if git_dir.exists():
        fingerprint_parts = []

        # Get remote origin URL (stable across clones)
        returncode, stdout, _ = _run_git_command(
            ["config", "--get", "remote.origin.url"], cwd=repo_root
        )
        if returncode == 0 and stdout.strip():
            fingerprint_parts.append(stdout.strip())

        # Get first commit SHA (stable identifier for the repo)
        returncode, stdout, _ = _run_git_command(
            ["rev-list", "--max-parents=0", "HEAD"], cwd=repo_root
        )
        if returncode == 0 and stdout.strip():
            # Use first commit if multiple roots
            first_commit = stdout.strip().split("\n")[0]
            fingerprint_parts.append(first_commit)

        if fingerprint_parts:
            combined = ":".join(fingerprint_parts)
            return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Fallback: hash the absolute path
    abs_path = str(repo_root.resolve())
    return hashlib.sha256(abs_path.encode()).hexdigest()[:16]


def _get_repo_state_hash(repo_root: Path) -> str:
    """Generate a 16-char hash of the current repo state.

    Returns the first 16 hex chars of the spec-defined repo_fingerprint
    (``repo_fingerprint.compute_repo_fingerprint``). The truncated form
    keeps cache-dir names short while inheriting the spec algorithm's
    content-hash semantics — content change → hash change, mtime-only
    change → hash stable.

    Content hashing is what keeps the key stable across mtime-only churn
    such as tracker ``.ops`` files being touched between unrelated
    analysis runs (INV-magul; see INV-tofur and ``repo_fingerprint`` in
    ``docs/hypergumbo-spec.md``).
    """
    from .repo_fingerprint import compute_repo_fingerprint
    return compute_repo_fingerprint(repo_root)[:16]


def _get_xdg_cache_base() -> Path:
    """Get the XDG cache base directory for hypergumbo.

    Returns ~/.cache/hypergumbo/ following XDG Base Directory Specification.
    Uses XDG_CACHE_HOME if set, otherwise defaults to ~/.cache.

    Returns:
        Path to the hypergumbo cache base directory.
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".cache"

    return base / "hypergumbo"


def _get_results_cache_dir(
    repo_root: Path, *, extra_excludes: "Sequence[str] | None" = None,
) -> Path:
    """Get or create the results cache directory for current repo state.

    Cache structure (WI-panih):
        ~/.cache/hypergumbo/<fingerprint>/results/<state_hash>[-<backends>]/<analyzer_identity>/

    The third segment is the repo state hash, suffixed with the RESOLVED
    opt-in backend set when it is non-empty — ``-rust_analyzer`` for a
    two-arm run — because which backends were ENABLED is run configuration
    the analyzer identity (code identity) cannot see: without the suffix a
    tree-sitter-only run and a two-arm run of one tree would share a dir and
    return each other's artifact (WI-givib). The suffix is added only when a
    backend will actually run (opt-in chain AND binary installed —
    ``backend_selection.resolved_backend_set``), so a run with no backend
    keeps the plain key, and a read command finds a two-arm artifact only
    under the same resolved set (the environment variable or the
    per-repository trust grant, not a one-off flag).
    The same segment is suffixed ``-deps-all`` / ``-deps-<hash>`` when
    installed dependency source is traced (``--trace-deps`` or the
    ``trace_deps`` config key; ``discovery.resolve_trace_deps``), so a traced
    and an untraced survey of one tree never share an entry. It is then
    suffixed ``-cat-<hash>`` with a digest of the user catalogue channels the
    analysis reads (the registry's analysis-stage families: ``frameworks.d``,
    ``dataflow_patterns.d``, ``library_signatures.d`` —
    ``catalogue_home.analysis_channel_token``) and ``-cfg-<hash>`` with a
    digest of ``config.toml`` and ``.hypergumbo.toml``
    (``user_config.config_cache_token``). Neither is repository source the
    state hash sees, and without them a cached survey would keep answering
    after a channel file was added, edited or broken — the broken file never
    read, so never reported. Each suffix is absent when its files are, so a
    user with no channel files and no config keeps the plain key. It is
    suffixed ``-ex-<hash>`` with a digest of the ``-e`` / ``--exclude``
    patterns the run was given (``discovery.exclude_cache_token``): without
    it a survey that skipped ``lib/`` would REPLACE the plain survey in the
    one directory, and a read command, which takes no ``--exclude``, would
    answer from it (WI-tisuj). Read commands look under the plain key only.

    The fourth segment (``<analyzer_identity>``) keys on the analyzer
    surface that produces the cached output — ``__version__`` plus a
    content hash of every installed ``hypergumbo_*`` package's
    ``.py`` files. Without it, released-stable and dev-editable
    installs (or RCT arms, or post-lang-upgrade runs) share a cache
    entry and poison each other's results on the same source tree.

    Results are cached per-(repo, state, run configuration, analyzer)
    because they depend on the entire repo contents, on the user inputs
    and settings the analysis reads, AND on the specific analyzer running.

    Args:
        repo_root: Repository root path.
        extra_excludes: The run's ``--exclude`` patterns, if any.

    Returns:
        Path to the results cache directory for current state.
    """
    from . import backend_selection
    from .analyzer_identity import compute_analyzer_identity_hash
    from .catalogue_home import analysis_channel_token
    from .discovery import exclude_cache_token, resolve_trace_deps
    from .user_config import config_cache_token

    fingerprint = _get_repo_fingerprint(repo_root)
    state_hash = _get_repo_state_hash(repo_root)
    backends = backend_selection.resolved_backend_set(repo_root=repo_root)
    if backends:
        state_hash = f"{state_hash}-{'+'.join(backends)}"
    # ADR-0004 §"Installed dependency source": which installed dependency
    # source was traced is run configuration too. Suffixed only when something
    # is traced, so every untraced entry keeps its key.
    trace_token = resolve_trace_deps(repo_root).cache_token()
    # The user files the analysis reads outside the repository's source:
    # analysis-stage catalogue channels and the two config tiers. Each token
    # is "" when no such file exists, so those users keep their key.
    for token in (trace_token, analysis_channel_token(),
                  config_cache_token(repo_root),
                  exclude_cache_token(extra_excludes)):
        if token:
            state_hash = f"{state_hash}-{token}"
    analyzer_identity = compute_analyzer_identity_hash()
    cache_base = _get_xdg_cache_base()
    cache_dir = (
        cache_base
        / fingerprint
        / "results"
        / state_hash
        / analyzer_identity
    )

    # Create the full path including parent directories
    cache_mkdir(cache_dir, parents=True, exist_ok=True)
    return cache_dir


#: Every model a runtime command loads, in the order install fetches them.
EMBEDDING_MODELS: tuple[str, ...] = (_EMBEDDING_MODEL,)


def fetch_embedding_models(st_cls: "Callable[..., object] | None" = None) -> None:
    """Download every model in :data:`EMBEDDING_MODELS` that is not cached.

    THE ONE PLACE A MODEL IS FETCHED (INV-gujus). Called only from the install
    path -- ``install-embeddings`` and ``add-extras``, in a fresh interpreter
    so a library pip just installed imports cleanly -- never from analysis,
    whose loader (:func:`_load_st_model_offline_first`) is cache-only. Lifts
    ``HF_HUB_OFFLINE`` for the duration, since the caller asked for a download.
    """
    # Lifted BEFORE the import: huggingface_hub freezes the switch then.
    prior_offline = os.environ.pop("HF_HUB_OFFLINE", None)
    load: Callable[..., object]
    if st_cls is not None:
        load = st_cls
    else:  # pragma: no cover - the real library; tests inject it
        from sentence_transformers import SentenceTransformer
        load = SentenceTransformer
    try:
        for name in EMBEDDING_MODELS:
            if not _hf_model_is_cached(name):
                load(name)
    finally:
        if prior_offline is not None:
            os.environ["HF_HUB_OFFLINE"] = prior_offline
