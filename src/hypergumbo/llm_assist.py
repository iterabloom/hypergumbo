"""LLM-assisted capsule plan generation.

**Status: Proof-of-concept infrastructure.**

With the current catalog size (6 passes, 2 packs), template-based generation
produces equivalent results. This module exists to support future catalog
expansion where LLM assistance will help navigate complex framework pack
combinations and configuration options.

Provides optional LLM integration for generating capsule plans from repo profiles.
Supports multiple backends: OpenRouter (free tier), OpenAI, and local models via
the llm package (https://pypi.org/project/llm/).

How It Works
------------
1. detect_backend() finds the best available LLM backend based on environment
2. generate_plan_with_llm() builds a prompt from profile + catalog
3. LLM generates a capsule_plan.json structure
4. Output is validated against catalog; invalid plans fall back to template

Backend Selection Priority
--------------------------
1. HYPERGUMBO_LLM_BACKEND env var (explicit choice)
2. OPENROUTER_API_KEY set → OpenRouter with free model
3. OPENAI_API_KEY set → OpenAI
4. `llm` package installed → local model via llm
5. None available → fall back to template generation

Why This Design
---------------
- Optional dependency: core hypergumbo works without any LLM
- OpenRouter offers free inference for capable models (Devstral, Qwen3 Coder)
- Local models via llm package enable fully offline operation
- Validation gate ensures only valid plans are used
- Graceful degradation: LLM failures fall back to template
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Generator, Optional, Tuple

from .catalog import Catalog
from .plan import (
    CapsulePlan,
    PackConfig,
    PassConfig,
    Rule,
    PLAN_VERSION,
    generate_plan,
    validate_plan,
)
from .profile import RepoProfile


class LLMBackend(Enum):
    """Supported LLM backends."""

    OPENROUTER = "openrouter"
    OPENAI = "openai"
    LLM_PACKAGE = "llm"
    NONE = "none"


# Default models for each backend
DEFAULT_MODELS = {
    LLMBackend.OPENROUTER: "mistralai/devstral-2512:free",
    LLMBackend.OPENAI: "gpt-4o-mini",
    LLMBackend.LLM_PACKAGE: None,  # Uses default from llm package
}

# OpenRouter free models suitable for code/plan generation
OPENROUTER_FREE_MODELS = [
    "mistralai/devstral-2512:free",  # 262K context, coding specialist
    "qwen/qwen3-coder:free",  # 262K context, agentic coding
    "openai/gpt-oss-20b:free",  # 131K context, function calling
    "google/gemma-3-27b-it:free",  # 131K context, multimodal
]


@dataclass
class LLMConfig:
    """Configuration for LLM-assisted plan generation.

    Attributes:
        backend: Which LLM backend to use
        model: Model identifier (backend-specific)
        api_key: API key for cloud backends
        base_url: Base URL for API (OpenRouter/OpenAI compatible)
        timeout: Request timeout in seconds
    """

    backend: LLMBackend
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: int = 60


@dataclass
class LLMResult:
    """Result of LLM plan generation.

    Attributes:
        success: Whether LLM generation succeeded
        plan: Generated plan (or None if failed)
        raw_response: Raw LLM response text
        error: Error message if failed
        backend_used: Which backend was used
        model_used: Which model was used
    """

    success: bool
    plan: Optional[CapsulePlan]
    raw_response: Optional[str] = None
    error: Optional[str] = None
    backend_used: Optional[LLMBackend] = None
    model_used: Optional[str] = None


def detect_backend() -> Tuple[LLMBackend, LLMConfig]:
    """Detect the best available LLM backend.

    Checks environment variables and installed packages to find
    the most appropriate backend.

    Returns:
        Tuple of (backend enum, config for that backend)
    """
    # Priority 1: Explicit backend selection via env var
    explicit = os.environ.get("HYPERGUMBO_LLM_BACKEND", "").lower()
    if explicit:
        if explicit == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key:
                return LLMBackend.OPENROUTER, LLMConfig(
                    backend=LLMBackend.OPENROUTER,
                    model=os.environ.get(
                        "HYPERGUMBO_LLM_MODEL",
                        DEFAULT_MODELS[LLMBackend.OPENROUTER],
                    ),
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                )
        elif explicit == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                return LLMBackend.OPENAI, LLMConfig(
                    backend=LLMBackend.OPENAI,
                    model=os.environ.get(
                        "HYPERGUMBO_LLM_MODEL",
                        DEFAULT_MODELS[LLMBackend.OPENAI],
                    ),
                    api_key=api_key,
                    base_url="https://api.openai.com/v1",
                )
        elif explicit == "llm":
            if _is_llm_package_available():
                return LLMBackend.LLM_PACKAGE, LLMConfig(
                    backend=LLMBackend.LLM_PACKAGE,
                    model=os.environ.get("HYPERGUMBO_LLM_MODEL"),
                )
        # Explicit backend requested but not available
        return LLMBackend.NONE, LLMConfig(backend=LLMBackend.NONE)

    # Priority 2: Auto-detect based on available credentials/packages
    # Check OpenRouter first (free tier available)
    # Check both env var and user config file
    from .user_config import get_api_key

    openrouter_key = get_api_key("openrouter")
    if openrouter_key:
        return LLMBackend.OPENROUTER, LLMConfig(
            backend=LLMBackend.OPENROUTER,
            model=DEFAULT_MODELS[LLMBackend.OPENROUTER],
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
        )

    # Check OpenAI
    openai_key = get_api_key("openai")
    if openai_key:
        return LLMBackend.OPENAI, LLMConfig(
            backend=LLMBackend.OPENAI,
            model=DEFAULT_MODELS[LLMBackend.OPENAI],
            api_key=openai_key,
            base_url="https://api.openai.com/v1",
        )

    # Check llm package
    if _is_llm_package_available():
        return LLMBackend.LLM_PACKAGE, LLMConfig(
            backend=LLMBackend.LLM_PACKAGE,
            model=None,  # Use llm's default
        )

    return LLMBackend.NONE, LLMConfig(backend=LLMBackend.NONE)


def _is_llm_package_available() -> bool:
    """Check if the llm package is installed and usable."""
    try:
        import llm  # noqa: F401

        return True
    except ImportError:
        return False


def _is_openai_sdk_available() -> bool:
    """Check if the OpenAI SDK is installed."""
    try:
        import openai  # noqa: F401

        return True
    except ImportError:
        return False


def _sanitize_no_proxy(value: str) -> str:
    """Remove CIDR ranges from NO_PROXY value that httpx cannot parse.

    The httpx library (used by OpenAI SDK) cannot handle CIDR notation
    like '10.0.0.0/8' or 'fd00::/8' in NO_PROXY. This filters them out
    while preserving valid hostname entries.
    """
    if not value:
        return value
    valid = [entry.strip() for entry in value.split(",") if "/" not in entry]
    return ",".join(valid)


@contextmanager
def _sanitized_proxy_env() -> Generator[None, None, None]:
    """Context manager that temporarily sanitizes NO_PROXY env vars for httpx.

    The OpenAI SDK uses httpx, which fails to parse IPv6 CIDR notation
    in NO_PROXY (e.g., 'fd00:200::/40'). This context manager temporarily
    removes CIDR entries while keeping valid hostname entries.
    """
    # Save originals
    orig_no_proxy = os.environ.get("NO_PROXY")
    orig_no_proxy_lower = os.environ.get("no_proxy")

    try:
        # Apply sanitized versions
        if orig_no_proxy:
            os.environ["NO_PROXY"] = _sanitize_no_proxy(orig_no_proxy)
        if orig_no_proxy_lower:
            os.environ["no_proxy"] = _sanitize_no_proxy(orig_no_proxy_lower)
        yield
    finally:
        # Restore originals
        if orig_no_proxy is not None:
            os.environ["NO_PROXY"] = orig_no_proxy
        elif "NO_PROXY" in os.environ:
            del os.environ["NO_PROXY"]

        if orig_no_proxy_lower is not None:
            os.environ["no_proxy"] = orig_no_proxy_lower
        elif "no_proxy" in os.environ:
            del os.environ["no_proxy"]


def _build_prompt(
    profile: RepoProfile,
    catalog: Catalog,
    tier: str = "tier0",
) -> str:
    """Build the prompt for LLM plan generation.

    Args:
        profile: Detected repo profile
        catalog: Available passes and packs
        tier: How much repo info to include (tier0/tier1/tier2)

    Returns:
        Prompt string for the LLM
    """
    # Build catalog summary
    passes_info = []
    for p in catalog.passes:
        avail_note = "" if p.availability == "core" else f" (requires: {p.requires})"
        passes_info.append(f"  - {p.id}: {p.description}{avail_note}")

    packs_info = []
    for pack in catalog.packs:
        passes_note = f" (uses: {', '.join(pack.passes)})" if pack.passes else ""
        packs_info.append(f"  - {pack.id}: {pack.description}{passes_note}")

    # Build profile summary based on tier
    if tier == "tier0":
        # Minimal: just language names and framework names
        profile_info = f"""Detected languages: {', '.join(profile.languages.keys())}
Detected frameworks: {', '.join(profile.frameworks) if profile.frameworks else 'none'}"""
    elif tier == "tier1":
        # Include file counts
        lang_details = [
            f"{lang}: {stats.files} files, {stats.loc} LOC"
            for lang, stats in profile.languages.items()
        ]
        profile_info = f"""Languages:
{chr(10).join('  - ' + d for d in lang_details)}
Frameworks: {', '.join(profile.frameworks) if profile.frameworks else 'none'}"""
    else:
        # tier2: fuller profile (still no code)
        lang_details = [
            f"{lang}: {stats.files} files, {stats.loc} LOC"
            for lang, stats in profile.languages.items()
        ]
        profile_info = f"""Languages:
{chr(10).join('  - ' + d for d in lang_details)}
Frameworks: {', '.join(profile.frameworks) if profile.frameworks else 'none'}
Repository kind: code analysis target"""

    prompt = f"""You are a code analysis configuration expert. Generate a capsule_plan.json for hypergumbo based on the repository profile below.

## Repository Profile
{profile_info}

## Available Passes
{chr(10).join(passes_info)}

## Available Packs
{chr(10).join(packs_info)}

## Task
Generate a capsule_plan.json that:
1. Enables appropriate passes for the detected languages
2. Enables relevant packs for detected frameworks
3. Includes sensible exclude rules for test files and dependencies
4. The plan version must be "{PLAN_VERSION}"

## Output Format
Respond with ONLY valid JSON matching this schema:
{{
  "version": "{PLAN_VERSION}",
  "passes": [
    {{"id": "pass-id", "enabled": true, "config": {{}}}}
  ],
  "packs": [
    {{"id": "pack-id", "enabled": true, "config": {{}}}}
  ],
  "rules": [
    {{"type": "exclude_pattern", "glob": "pattern", "reason": "why"}}
  ],
  "features": []
}}

Respond with only the JSON object, no markdown code blocks or explanation."""

    return prompt


def _call_openai_compatible(
    prompt: str,
    config: LLMConfig,
) -> Tuple[Optional[str], Optional[str]]:
    """Call an OpenAI-compatible API (OpenAI or OpenRouter).

    Args:
        prompt: The prompt to send
        config: LLM configuration

    Returns:
        Tuple of (response text, error message)
    """
    if not _is_openai_sdk_available():
        return None, "OpenAI SDK not installed. Run: pip install hypergumbo[llm-assist]"

    try:
        from openai import OpenAI

        # Sanitize NO_PROXY to remove CIDR entries httpx can't parse
        with _sanitized_proxy_env():
            client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
            )

            # Add optional headers for OpenRouter
            extra_headers = {}
            if config.backend == LLMBackend.OPENROUTER:
                extra_headers = {
                    "HTTP-Referer": "https://github.com/hypergumbo/hypergumbo",
                    "X-Title": "hypergumbo",
                }

            response = client.chat.completions.create(
                model=config.model or "gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a code analysis configuration expert. "
                        "Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,  # Low temperature for deterministic output
                extra_headers=extra_headers if extra_headers else None,
            )

            content = response.choices[0].message.content
        return content, None

    except Exception as e:
        return None, f"API call failed: {e}"


def _call_llm_package(
    prompt: str,
    config: LLMConfig,
) -> Tuple[Optional[str], Optional[str]]:
    """Call the llm package for local model inference.

    Args:
        prompt: The prompt to send
        config: LLM configuration

    Returns:
        Tuple of (response text, error message)
    """
    if not _is_llm_package_available():
        return None, "llm package not installed. Run: pip install llm"

    try:
        import llm

        model_name = config.model
        if model_name:
            model = llm.get_model(model_name)
        else:
            # Use default model
            model = llm.get_model()

        response = model.prompt(
            prompt,
            system="You are a code analysis configuration expert. "
            "Respond only with valid JSON.",
        )

        return response.text(), None

    except Exception as e:
        return None, f"llm package call failed: {e}"


def _parse_plan_json(
    raw_response: str,
    catalog: Catalog,
) -> Tuple[Optional[CapsulePlan], Optional[str]]:
    """Parse and validate LLM response as a CapsulePlan.

    Args:
        raw_response: Raw LLM response text
        catalog: Catalog to validate against

    Returns:
        Tuple of (parsed plan, error message)
    """
    # Strip markdown code blocks if present
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"

    # Parse into CapsulePlan
    try:
        passes = []
        for p in data.get("passes", []):
            passes.append(
                PassConfig(
                    id=p["id"],
                    enabled=p.get("enabled", True),
                    config=p.get("config", {}),
                    requires=p.get("requires"),
                )
            )

        packs = []
        for p in data.get("packs", []):
            packs.append(
                PackConfig(
                    id=p["id"],
                    enabled=p.get("enabled", True),
                    config=p.get("config", {}),
                )
            )

        rules = []
        for r in data.get("rules", []):
            rules.append(
                Rule(
                    rule_type=r.get("type", "exclude_pattern"),
                    glob=r.get("glob"),
                    pattern=r.get("pattern"),
                    label=r.get("label"),
                    reason=r.get("reason"),
                )
            )

        plan = CapsulePlan(
            version=data.get("version", PLAN_VERSION),
            passes=passes,
            packs=packs,
            rules=rules,
            features=data.get("features", []),
        )

    except (KeyError, TypeError) as e:
        return None, f"Invalid plan structure: {e}"

    # Validate against catalog
    errors = validate_plan(plan, catalog)
    if errors:
        return None, f"Plan validation failed: {'; '.join(errors)}"

    return plan, None


def generate_plan_with_llm(
    profile: RepoProfile,
    catalog: Catalog,
    tier: str = "tier0",
    config: Optional[LLMConfig] = None,
) -> LLMResult:
    """Generate a capsule plan using LLM assistance.

    Falls back to template generation if LLM fails.

    Args:
        profile: Detected repo profile
        catalog: Available passes and packs
        tier: How much repo info to send (tier0/tier1/tier2)
        config: Optional LLM configuration (auto-detected if None)

    Returns:
        LLMResult with success status and plan
    """
    # Detect backend if not provided
    if config is None:
        backend, config = detect_backend()
    else:
        backend = config.backend

    if backend == LLMBackend.NONE:
        return LLMResult(
            success=False,
            plan=None,
            error="No LLM backend available",
            backend_used=LLMBackend.NONE,
        )

    # Build prompt
    prompt = _build_prompt(profile, catalog, tier)

    # Call appropriate backend
    if backend in (LLMBackend.OPENROUTER, LLMBackend.OPENAI):
        raw_response, error = _call_openai_compatible(prompt, config)
    elif backend == LLMBackend.LLM_PACKAGE:
        raw_response, error = _call_llm_package(prompt, config)
    else:
        return LLMResult(
            success=False,
            plan=None,
            error=f"Unknown backend: {backend}",
            backend_used=backend,
        )

    if error:
        return LLMResult(
            success=False,
            plan=None,
            raw_response=raw_response,
            error=error,
            backend_used=backend,
            model_used=config.model,
        )

    # Parse and validate response
    plan, parse_error = _parse_plan_json(raw_response or "", catalog)
    if parse_error:
        return LLMResult(
            success=False,
            plan=None,
            raw_response=raw_response,
            error=parse_error,
            backend_used=backend,
            model_used=config.model,
        )

    return LLMResult(
        success=True,
        plan=plan,
        raw_response=raw_response,
        backend_used=backend,
        model_used=config.model,
    )


def generate_plan_with_fallback(
    profile: RepoProfile,
    catalog: Catalog,
    use_llm: bool = False,
    tier: str = "tier0",
) -> Tuple[CapsulePlan, Optional[LLMResult]]:
    """Generate a capsule plan with optional LLM assistance and fallback.

    If use_llm is True, attempts LLM generation first. On any failure,
    falls back to template-based generation.

    Args:
        profile: Detected repo profile
        catalog: Available passes and packs
        use_llm: Whether to attempt LLM-assisted generation
        tier: How much repo info to send to LLM

    Returns:
        Tuple of (plan, llm_result if attempted else None)
    """
    if not use_llm:
        return generate_plan(profile, catalog), None

    # Try LLM generation
    llm_result = generate_plan_with_llm(profile, catalog, tier)

    if llm_result.success and llm_result.plan is not None:
        return llm_result.plan, llm_result

    # Fall back to template
    template_plan = generate_plan(profile, catalog)
    return template_plan, llm_result
