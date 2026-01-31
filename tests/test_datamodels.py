"""Tests for data model detection."""
from __future__ import annotations

import pytest

from hypergumbo_core.datamodels import (
    DataModel,
    DataModelKind,
    detect_datamodels,
    _detect_from_concepts,
    _detect_from_decorators,
    _detect_from_base_classes,
    _detect_from_naming,
)
from hypergumbo_core.ir import Symbol, Edge, Span


def _make_class_symbol(
    name: str,
    path: str = "test.py",
    meta: dict | None = None,
) -> Symbol:
    """Create a test class symbol."""
    span = Span(start_line=1, end_line=10, start_col=0, end_col=10)
    return Symbol(
        id=f"test:{name}",
        name=name,
        kind="class",
        language="python",
        path=path,
        span=span,
        origin="test",
        origin_run_id="uuid:test",
        meta=meta or {},
    )


def _make_edge(src_id: str, dst_id: str, idx: int = 0) -> Edge:
    """Create a test edge."""
    return Edge(
        id=f"edge:{idx}",
        src=src_id,
        dst=dst_id,
        edge_type="references",
        line=1,
    )


class TestDetectFromConcepts:
    """Tests for concept-based detection."""

    def test_detects_model_concept(self) -> None:
        """Detects data models from 'model' concept."""
        sym = _make_class_symbol(
            "User",
            meta={
                "concepts": [{"concept": "model", "framework": "Django"}],
            },
        )

        models = _detect_from_concepts([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ORM_MODEL
        assert models[0].confidence == 0.95
        assert "Django" in models[0].label
        assert models[0].framework == "Django"

    def test_detects_schema_concept(self) -> None:
        """Detects data models from 'schema' concept."""
        sym = _make_class_symbol(
            "UserSchema",
            meta={
                "concepts": [{"concept": "schema", "framework": "Marshmallow"}],
            },
        )

        models = _detect_from_concepts([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.SCHEMA
        assert models[0].confidence == 0.95

    def test_detects_entity_concept(self) -> None:
        """Detects data models from 'entity' concept."""
        sym = _make_class_symbol(
            "UserEntity",
            meta={
                "concepts": [{"concept": "entity", "framework": "TypeORM"}],
            },
        )

        models = _detect_from_concepts([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ENTITY
        assert models[0].confidence == 0.95

    def test_skips_non_class_symbols(self) -> None:
        """Skips non-class symbols."""
        span = Span(start_line=1, end_line=10, start_col=0, end_col=10)
        sym = Symbol(
            id="test:func",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=span,
            origin="test",
            origin_run_id="uuid:test",
            meta={"concepts": [{"concept": "model"}]},
        )

        models = _detect_from_concepts([sym])

        assert len(models) == 0

    def test_skips_invalid_concepts(self) -> None:
        """Skips invalid concept entries."""
        sym = _make_class_symbol(
            "User",
            meta={
                "concepts": ["not_a_dict", {"concept": "unknown"}],
            },
        )

        models = _detect_from_concepts([sym])

        assert len(models) == 0

    def test_handles_empty_concepts(self) -> None:
        """Handles symbols with meta but empty concepts list."""
        sym = _make_class_symbol("User", meta={"concepts": []})

        models = _detect_from_concepts([sym])

        assert len(models) == 0

    def test_handles_missing_meta(self) -> None:
        """Handles symbols without meta."""
        sym = _make_class_symbol("User", meta=None)
        sym.meta = None

        models = _detect_from_concepts([sym])

        assert len(models) == 0


class TestDetectFromDecorators:
    """Tests for decorator-based detection."""

    def test_detects_dataclass_decorator(self) -> None:
        """Detects @dataclass decorator."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": ["dataclass"]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DATACLASS
        assert models[0].confidence == 0.90

    def test_detects_qualified_dataclass(self) -> None:
        """Detects dataclasses.dataclass decorator."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": ["dataclasses.dataclass"]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DATACLASS

    def test_detects_entity_decorator(self) -> None:
        """Detects @Entity decorator."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": ["Entity"]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ENTITY

    def test_detects_table_decorator(self) -> None:
        """Detects @Table decorator."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": ["Table"]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ORM_MODEL

    def test_handles_decorator_dict_format(self) -> None:
        """Handles decorator as dict with name field."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": [{"name": "dataclass"}]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DATACLASS

    def test_skips_unknown_decorators(self) -> None:
        """Skips unknown decorators."""
        sym = _make_class_symbol(
            "User",
            meta={"decorators": ["unknown_decorator"]},
        )

        models = _detect_from_decorators([sym])

        assert len(models) == 0


class TestDetectFromBaseClasses:
    """Tests for base class detection."""

    def test_detects_django_model(self) -> None:
        """Detects Django Model base class."""
        sym = _make_class_symbol(
            "User",
            meta={"parent_bases": ["Model"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ORM_MODEL
        assert models[0].framework == "Django"

    def test_detects_qualified_django_model(self) -> None:
        """Detects qualified django.db.models.Model."""
        sym = _make_class_symbol(
            "User",
            meta={"parent_bases": ["django.db.models.Model"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ORM_MODEL

    def test_detects_pydantic_basemodel(self) -> None:
        """Detects Pydantic BaseModel."""
        sym = _make_class_symbol(
            "User",
            meta={"parent_bases": ["BaseModel"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.PYDANTIC_MODEL
        assert models[0].framework == "Pydantic"

    def test_detects_sqlalchemy_base(self) -> None:
        """Detects SQLAlchemy Base class."""
        sym = _make_class_symbol(
            "User",
            meta={"parent_bases": ["Base"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ORM_MODEL
        assert models[0].framework == "SQLAlchemy"

    def test_detects_marshmallow_schema(self) -> None:
        """Detects Marshmallow Schema."""
        sym = _make_class_symbol(
            "UserSchema",
            meta={"parent_bases": ["Schema"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.SCHEMA

    def test_skips_unknown_bases(self) -> None:
        """Skips unknown base classes."""
        sym = _make_class_symbol(
            "User",
            meta={"parent_bases": ["SomeUnknownBase"]},
        )

        models = _detect_from_base_classes([sym])

        assert len(models) == 0


class TestDetectFromNaming:
    """Tests for naming convention detection."""

    def test_detects_model_suffix(self) -> None:
        """Detects classes ending in Model."""
        sym = _make_class_symbol("UserModel")

        models = _detect_from_naming([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DOMAIN_MODEL
        assert models[0].confidence == 0.70

    def test_detects_entity_suffix(self) -> None:
        """Detects classes ending in Entity."""
        sym = _make_class_symbol("UserEntity")

        models = _detect_from_naming([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.ENTITY

    def test_detects_schema_suffix(self) -> None:
        """Detects classes ending in Schema."""
        sym = _make_class_symbol("UserSchema")

        models = _detect_from_naming([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.SCHEMA

    def test_detects_dto_suffix(self) -> None:
        """Detects classes ending in DTO."""
        sym = _make_class_symbol("UserDTO")

        models = _detect_from_naming([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DTO

    def test_detects_record_suffix(self) -> None:
        """Detects classes ending in Record."""
        sym = _make_class_symbol("UserRecord")

        models = _detect_from_naming([sym])

        assert len(models) == 1
        assert models[0].kind == DataModelKind.DOMAIN_MODEL

    def test_requires_lowercase_before_suffix(self) -> None:
        """Requires lowercase letter before suffix to avoid base classes."""
        # "Model" alone shouldn't match (no lowercase before)
        sym = _make_class_symbol("Model")

        models = _detect_from_naming([sym])

        assert len(models) == 0

    def test_skips_non_matching_names(self) -> None:
        """Skips names that don't match patterns."""
        sym = _make_class_symbol("UserService")

        models = _detect_from_naming([sym])

        assert len(models) == 0


class TestDetectDatamodels:
    """Tests for the main detection function."""

    def test_deduplicates_by_symbol_id(self) -> None:
        """Keeps highest-confidence detection per symbol."""
        # Symbol with both concept and naming
        sym = _make_class_symbol(
            "UserModel",
            meta={
                "concepts": [{"concept": "model", "framework": "Django"}],
            },
        )

        models = detect_datamodels([sym], [])

        # Should only have one entry (concept wins at 0.95 vs naming at 0.70)
        assert len(models) == 1
        assert models[0].confidence >= 0.95

    def test_boosts_confidence_by_centrality(self) -> None:
        """Boosts confidence for highly-referenced models."""
        sym = _make_class_symbol(
            "UserModel",
            meta={"concepts": [{"concept": "model"}]},
        )
        # Create edges pointing TO this symbol
        edges = [_make_edge(f"src:{i}", sym.id, idx=i) for i in range(10)]

        models = detect_datamodels([sym], edges)

        assert len(models) == 1
        # Should have boost above base 0.95
        assert models[0].confidence > 0.95

    def test_sorts_by_confidence(self) -> None:
        """Sorts results by confidence (highest first)."""
        high_conf = _make_class_symbol(
            "HighModel",
            meta={"concepts": [{"concept": "model"}]},  # 0.95
        )
        low_conf = _make_class_symbol("LowModel")  # naming only: 0.70

        models = detect_datamodels([low_conf, high_conf], [])

        assert len(models) == 2
        assert models[0].symbol_id == high_conf.id
        assert models[1].symbol_id == low_conf.id

    def test_returns_empty_for_no_models(self) -> None:
        """Returns empty list when no models detected."""
        span = Span(start_line=1, end_line=10, start_col=0, end_col=10)
        sym = Symbol(
            id="test:func",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=span,
            origin="test",
            origin_run_id="uuid:test",
        )

        models = detect_datamodels([sym], [])

        assert len(models) == 0


class TestDataModelSerialization:
    """Tests for DataModel serialization."""

    def test_to_dict(self) -> None:
        """Serializes DataModel to dictionary."""
        model = DataModel(
            symbol_id="test:User",
            kind=DataModelKind.ORM_MODEL,
            confidence=0.95,
            label="Django model",
            framework="Django",
        )

        result = model.to_dict()

        assert result["symbol_id"] == "test:User"
        assert result["kind"] == "orm_model"
        assert result["confidence"] == 0.95
        assert result["label"] == "Django model"
        assert result["framework"] == "Django"
