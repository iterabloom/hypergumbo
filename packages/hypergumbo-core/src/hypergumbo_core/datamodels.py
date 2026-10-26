# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data model detection for code analysis.

Detects data models (ORM entities, DTOs, domain objects) via:
1. YAML framework pattern concepts ("model", "schema", "entity")
2. Decorator patterns (@dataclass, @Entity, etc.)
3. Base class patterns (BaseModel, Model, Base, etc.)
4. Naming conventions (classes ending in Model, Entity, Schema, DTO)

How It Works
------------
Data model detection uses multiple signals to identify classes that represent
data structures in the codebase - the "nouns" of the domain model:

1. **Semantic concepts** (ADR-3aaa): YAML framework patterns emit "model",
   "schema", or "entity" concepts for ORM models, Pydantic models, etc.

2. **Decorator detection**: Looks for @dataclass, @Entity, @Table, etc.
   decorators in symbol metadata.

3. **Base class detection**: Matches classes inheriting from known data model
   base classes (BaseModel, Model, DeclarativeBase, and ``Base``, the usual name
   for a ``declarative_base()`` result, etc.)

4. **Naming conventions**: Classes with names ending in Model, Entity,
   Schema, DTO, Record (with lowercase prefix) are likely data models.

Confidence Scores
-----------------
- 0.95: Framework pattern concept detection (explicit model/schema/entity)
- 0.90: Decorator detection (@dataclass, @Entity, etc.)
- 0.85: Base class detection (BaseModel, Model, etc.)
- 0.70: Naming convention detection (ends with Model, Entity, etc.)

Centrality Boost:
- Models with high in-degree (many references) get a boost to ``rank_score``,
  which starts at ``confidence`` and is the sort key; ``confidence`` itself
  stays the pure detection reliability (ADR-0039)
- Boost formula: min(0.15, log(1 + in_degree) / 20), with ``rank_score``
  capped at 1.0
- This ranks "core" domain models higher than utility DTOs
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List

from .ir import Symbol, Edge


@dataclass
class DataModel:
    """A detected data model in the codebase.

    Attributes:
        symbol_id: ID of the symbol that is a data model.
        confidence: Confidence score (0.0-1.0).
        label: Human-readable label for the data model.
        framework: Framework that defined this model (if known).
    """

    symbol_id: str  # axis: identity
    confidence: float
    label: str  # axis: free-text — human-readable label for display; consumers render, never branch on the value itself.
    framework: str = ""  # axis: free-text — framework label like "Django", "Pydantic"; consumers display, never branch on the value itself.
    # ADR-0039 (WI-gozik): ranking prominence (base confidence + centrality
    # boost). `confidence` stays the pure evidence-derived detection reliability;
    # `rank_score` carries the importance signal that ordering keys on. Set by
    # ``detect_datamodels`` after dedup; default 0.0 for a bare-constructed model.
    rank_score: float = 0.0


# Known data model base classes (regex patterns)
MODEL_BASE_CLASSES = [
    # Python ORMs
    (r"^(django\.db\.)?models\.Model$", "Django"),
    (r"^Model$", "Django"),  # Short form
    (r"^(sqlalchemy\.orm\.)?DeclarativeBase$", "SQLAlchemy"),
    (r"^Base$", "SQLAlchemy"),  # Common SQLAlchemy pattern
    (r"^(pydantic\.)?BaseModel$", "Pydantic"),
    (r"^BaseModel$", "Pydantic"),  # Short form
    # Python schemas
    (r"^(marshmallow\.)?Schema$", "Marshmallow"),
    (r"^Schema$", "Marshmallow"),  # Short form
    # TypeScript/JavaScript
    (r"^Entity$", "TypeORM"),
    # Java/Kotlin
    (r"^(javax\.persistence\.)?Entity$", "JPA"),
    # Go
    (r"^gorm\.Model$", "GORM"),
]

# Known data model decorators (regex patterns)
MODEL_DECORATORS = [
    (r"^dataclass$", "Python"),
    (r"^dataclasses\.dataclass$", "Python"),
    (r"^Entity$", "TypeORM"),
    (r"^Table$", "SQLAlchemy"),
    (r"^mapped_as_dataclass$", "SQLAlchemy"),
]

# Naming convention patterns (class name endings) and the label each gives
NAMING_PATTERNS = [
    (r"[a-z]Model$", "Domain model"),
    (r"[a-z]Entity$", "Entity"),
    (r"[a-z]Schema$", "Schema"),
    (r"[a-z]DTO$", "DTO"),
    (r"[a-z]Record$", "Record"),
]


def _detect_from_concepts(symbols: List[Symbol]) -> List[DataModel]:
    """Detect data models from semantic concept metadata.

    YAML framework patterns emit "model", "schema", or "entity" concepts
    for classes that match known data model patterns.

    The label names the construct only ("model"); the framework goes in
    ``DataModel.framework``, and the sketch renders ``{framework} {label}``.
    A label carrying the framework too printed it twice (WI-kibat).

    Args:
        symbols: All symbols with potential concept metadata.

    Returns:
        List of data models detected via semantic concepts.
    """
    models: list[DataModel] = []

    for sym in symbols:
        if sym.kind != "class":
            continue
        if not sym.meta:
            continue

        concepts = sym.meta.get("concepts", [])
        if not concepts:
            continue

        for concept in concepts:
            if not isinstance(concept, dict):
                continue

            concept_type = concept.get("concept")
            framework = concept.get("framework", "")

            if concept_type == "model":
                label = "model" if framework else "Data model"
                models.append(DataModel(
                    symbol_id=sym.id,
                    confidence=0.95,
                    label=label,
                    framework=framework,
                ))
                break  # One model per symbol

            elif concept_type == "schema":
                label = "schema" if framework else "Schema"
                models.append(DataModel(
                    symbol_id=sym.id,
                    confidence=0.95,
                    label=label,
                    framework=framework,
                ))
                break

            elif concept_type == "entity":
                label = "entity" if framework else "Entity"
                models.append(DataModel(
                    symbol_id=sym.id,
                    confidence=0.95,
                    label=label,
                    framework=framework,
                ))
                break

    return models


def _detect_from_decorators(symbols: List[Symbol]) -> List[DataModel]:
    """Detect data models from decorator patterns.

    Looks for @dataclass, @Entity, @Table, etc. decorators in symbol metadata.

    Args:
        symbols: All symbols to check.

    Returns:
        List of data models detected via decorators.
    """
    models: list[DataModel] = []

    for sym in symbols:
        if sym.kind != "class":
            continue
        if not sym.meta:
            continue

        decorators = sym.meta.get("decorators", [])
        if not decorators:
            continue

        for dec in decorators:
            dec_name = dec if isinstance(dec, str) else dec.get("name", "")
            for pattern, framework in MODEL_DECORATORS:
                if re.match(pattern, dec_name):
                    models.append(DataModel(
                        symbol_id=sym.id,
                        confidence=0.90,
                        label=f"@{dec_name.split('.')[-1]}",
                        framework=framework,
                    ))
                    break
            else:
                continue
            break  # Found a match, move to next symbol

    return models


def _detect_from_base_classes(symbols: List[Symbol]) -> List[DataModel]:
    """Detect data models from base class inheritance.

    Matches classes inheriting from known data model base classes.

    Args:
        symbols: All symbols to check.

    Returns:
        List of data models detected via base class patterns.
    """
    models: list[DataModel] = []

    for sym in symbols:
        if sym.kind != "class":
            continue
        if not sym.meta:
            continue

        bases = sym.meta.get("parent_bases", [])
        if not bases:
            continue

        for base in bases:
            base_name = base if isinstance(base, str) else str(base)
            for pattern, framework in MODEL_BASE_CLASSES:
                if re.match(pattern, base_name):
                    models.append(DataModel(
                        symbol_id=sym.id,
                        confidence=0.85,
                        label=f"extends {base_name.split('.')[-1]}",
                        framework=framework,
                    ))
                    break
            else:
                continue
            break  # Found a match, move to next symbol

    return models


def _detect_from_naming(symbols: List[Symbol]) -> List[DataModel]:
    """Detect data models from naming conventions.

    Matches classes with names ending in Model, Entity, Schema, DTO, etc.
    Requires a lowercase letter before the suffix to avoid false positives
    (e.g., "Model" base class itself).

    Args:
        symbols: All symbols to check.

    Returns:
        List of data models detected via naming conventions.
    """
    models: list[DataModel] = []

    for sym in symbols:
        if sym.kind != "class":
            continue

        for pattern, label in NAMING_PATTERNS:
            if re.search(pattern, sym.name):
                models.append(DataModel(
                    symbol_id=sym.id,
                    confidence=0.70,
                    label=label,
                    framework="",
                ))
                break  # One detection per symbol

    return models


def detect_datamodels(
    symbols: List[Symbol],
    edges: List[Edge],
) -> List[DataModel]:
    """Detect data models in the codebase.

    Uses multiple detection strategies in order of confidence:
    1. Semantic concepts from YAML framework patterns (0.95)
    2. Decorator patterns (@dataclass, @Entity, etc.) (0.90)
    3. Base class patterns (BaseModel, Model, etc.) (0.85)
    4. Naming conventions (UserModel, OrderEntity, etc.) (0.70)

    Higher confidence detections take precedence for duplicate symbols.

    Args:
        symbols: All symbols in the codebase.
        edges: All edges (used for centrality boost).

    Returns:
        List of detected data models with confidence scores.
    """
    # Collect all detections
    all_models: list[DataModel] = []
    all_models.extend(_detect_from_concepts(symbols))
    all_models.extend(_detect_from_decorators(symbols))
    all_models.extend(_detect_from_base_classes(symbols))
    all_models.extend(_detect_from_naming(symbols))

    # Keep highest-confidence detection per symbol
    best_by_id: dict[str, DataModel] = {}
    for model in all_models:
        existing = best_by_id.get(model.symbol_id)
        if existing is None or model.confidence > existing.confidence:
            best_by_id[model.symbol_id] = model

    unique_models = list(best_by_id.values())

    # ADR-0039 (WI-gozik): rank_score carries importance. Data models that are
    # widely referenced (high in-degree) are more prominent, but centrality is a
    # RANKING signal — it must not inflate `confidence` (pure evidence-derived
    # detection reliability, which the old confidence+boost sum could also push
    # onto the reserved 1.0 ceiling). rank_score starts at confidence and absorbs
    # the centrality boost; ordering keys on it.
    incoming_counts: dict[str, int] = {}
    for edge in edges:
        incoming_counts[edge.dst] = incoming_counts.get(edge.dst, 0) + 1

    for model in unique_models:
        model.rank_score = model.confidence
        in_degree = incoming_counts.get(model.symbol_id, 0)
        if in_degree > 0:
            # Logarithmic boost: diminishing returns for very high counts,
            # capped at 0.15 so centrality never overwhelms the base signal.
            centrality_boost = min(0.15, math.log(1 + in_degree) / 20)
            model.rank_score = min(1.0, model.rank_score + centrality_boost)

    # Sort by rank_score (importance), highest first.
    unique_models.sort(key=lambda m: m.rank_score, reverse=True)

    return unique_models
