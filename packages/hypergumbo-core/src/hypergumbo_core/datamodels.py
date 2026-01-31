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

1. **Semantic concepts** (ADR-0003): YAML framework patterns emit "model",
   "schema", or "entity" concepts for ORM models, Pydantic models, etc.

2. **Decorator detection**: Looks for @dataclass, @Entity, @Table, etc.
   decorators in symbol metadata.

3. **Base class detection**: Matches classes inheriting from known data model
   base classes (BaseModel, Model, Base, declarative_base(), etc.)

4. **Naming conventions**: Classes with names ending in Model, Entity,
   Schema, DTO, Record (with lowercase prefix) are likely data models.

Confidence Scores
-----------------
- 0.95: Framework pattern concept detection (explicit model/schema/entity)
- 0.90: Decorator detection (@dataclass, @Entity, etc.)
- 0.85: Base class detection (BaseModel, Model, etc.)
- 0.70: Naming convention detection (ends with Model, Entity, etc.)

Centrality Boost:
- Models with high in-degree (many references) get a confidence boost
- Boost formula: min(0.15, log(1 + in_degree) / 20)
- This ranks "core" domain models higher than utility DTOs
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import List

from .ir import Symbol, Edge


class DataModelKind(Enum):
    """Types of data models that can be detected."""

    ORM_MODEL = "orm_model"  # Django Model, SQLAlchemy, TypeORM, etc.
    PYDANTIC_MODEL = "pydantic_model"  # Pydantic BaseModel
    DATACLASS = "dataclass"  # Python @dataclass
    ENTITY = "entity"  # JPA @Entity, TypeORM @Entity
    SCHEMA = "schema"  # Marshmallow, GraphQL schema types
    DTO = "dto"  # Data Transfer Object
    DOMAIN_MODEL = "domain_model"  # Generic domain model (naming convention)


@dataclass
class DataModel:
    """A detected data model in the codebase.

    Attributes:
        symbol_id: ID of the symbol that is a data model.
        kind: Type of data model detected.
        confidence: Confidence score (0.0-1.0).
        label: Human-readable label for the data model.
        framework: Framework that defined this model (if known).
    """

    symbol_id: str
    kind: DataModelKind
    confidence: float
    label: str
    framework: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "symbol_id": self.symbol_id,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "label": self.label,
            "framework": self.framework,
        }


# Known data model base classes (regex patterns)
MODEL_BASE_CLASSES = [
    # Python ORMs
    (r"^(django\.db\.)?models\.Model$", DataModelKind.ORM_MODEL, "Django"),
    (r"^Model$", DataModelKind.ORM_MODEL, "Django"),  # Short form
    (r"^(sqlalchemy\.orm\.)?DeclarativeBase$", DataModelKind.ORM_MODEL, "SQLAlchemy"),
    (r"^Base$", DataModelKind.ORM_MODEL, "SQLAlchemy"),  # Common SQLAlchemy pattern
    (r"^(pydantic\.)?BaseModel$", DataModelKind.PYDANTIC_MODEL, "Pydantic"),
    (r"^BaseModel$", DataModelKind.PYDANTIC_MODEL, "Pydantic"),  # Short form
    # Python schemas
    (r"^(marshmallow\.)?Schema$", DataModelKind.SCHEMA, "Marshmallow"),
    (r"^Schema$", DataModelKind.SCHEMA, "Marshmallow"),  # Short form
    # TypeScript/JavaScript
    (r"^Entity$", DataModelKind.ENTITY, "TypeORM"),
    # Java/Kotlin
    (r"^(javax\.persistence\.)?Entity$", DataModelKind.ENTITY, "JPA"),
    # Go
    (r"^gorm\.Model$", DataModelKind.ORM_MODEL, "GORM"),
]

# Known data model decorators (regex patterns)
MODEL_DECORATORS = [
    (r"^dataclass$", DataModelKind.DATACLASS, "Python"),
    (r"^dataclasses\.dataclass$", DataModelKind.DATACLASS, "Python"),
    (r"^Entity$", DataModelKind.ENTITY, "TypeORM"),
    (r"^Table$", DataModelKind.ORM_MODEL, "SQLAlchemy"),
    (r"^mapped_as_dataclass$", DataModelKind.ORM_MODEL, "SQLAlchemy"),
]

# Naming convention patterns (class name endings)
NAMING_PATTERNS = [
    (r"[a-z]Model$", DataModelKind.DOMAIN_MODEL),
    (r"[a-z]Entity$", DataModelKind.ENTITY),
    (r"[a-z]Schema$", DataModelKind.SCHEMA),
    (r"[a-z]DTO$", DataModelKind.DTO),
    (r"[a-z]Record$", DataModelKind.DOMAIN_MODEL),
]


def _detect_from_concepts(symbols: List[Symbol]) -> List[DataModel]:
    """Detect data models from semantic concept metadata.

    YAML framework patterns emit "model", "schema", or "entity" concepts
    for classes that match known data model patterns.

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
                label = f"{framework} model" if framework else "Data model"
                models.append(DataModel(
                    symbol_id=sym.id,
                    kind=DataModelKind.ORM_MODEL,
                    confidence=0.95,
                    label=label,
                    framework=framework,
                ))
                break  # One model per symbol

            elif concept_type == "schema":
                label = f"{framework} schema" if framework else "Schema"
                models.append(DataModel(
                    symbol_id=sym.id,
                    kind=DataModelKind.SCHEMA,
                    confidence=0.95,
                    label=label,
                    framework=framework,
                ))
                break

            elif concept_type == "entity":
                label = f"{framework} entity" if framework else "Entity"
                models.append(DataModel(
                    symbol_id=sym.id,
                    kind=DataModelKind.ENTITY,
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
            for pattern, kind, framework in MODEL_DECORATORS:
                if re.match(pattern, dec_name):
                    models.append(DataModel(
                        symbol_id=sym.id,
                        kind=kind,
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
            for pattern, kind, framework in MODEL_BASE_CLASSES:
                if re.match(pattern, base_name):
                    models.append(DataModel(
                        symbol_id=sym.id,
                        kind=kind,
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

        for pattern, kind in NAMING_PATTERNS:
            if re.search(pattern, sym.name):
                # Extract the suffix for the label
                if sym.name.endswith("Model"):
                    label = "Domain model"
                elif sym.name.endswith("Entity"):
                    label = "Entity"
                elif sym.name.endswith("Schema"):
                    label = "Schema"
                elif sym.name.endswith("DTO"):
                    label = "DTO"
                elif sym.name.endswith("Record"):
                    label = "Record"
                else:  # pragma: no cover
                    label = "Data model"

                models.append(DataModel(
                    symbol_id=sym.id,
                    kind=kind,
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

    # Boost confidence based on centrality (incoming edges)
    # Data models that are widely referenced are more important
    incoming_counts: dict[str, int] = {}
    for edge in edges:
        incoming_counts[edge.dst] = incoming_counts.get(edge.dst, 0) + 1

    for model in unique_models:
        in_degree = incoming_counts.get(model.symbol_id, 0)
        if in_degree > 0:
            # Logarithmic boost: diminishing returns for very high counts
            # Cap at 0.15 to avoid overwhelming the base confidence
            centrality_boost = min(0.15, math.log(1 + in_degree) / 20)
            model.confidence = min(1.0, model.confidence + centrality_boost)

    # Sort by confidence (highest first)
    unique_models.sort(key=lambda m: m.confidence, reverse=True)

    return unique_models
