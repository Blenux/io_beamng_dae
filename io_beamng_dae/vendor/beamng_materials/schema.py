"""Generate a JSON Schema for the agnostic BeamNG material dataclasses."""
from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Dict, List, Optional, Type, Union, get_args, get_origin, get_type_hints

from . import maps
from .enums import (
    AlphaBlendMode,
    ImageSource,
    MaterialVersion,
    NormalMapSpace,
    TextureExtension,
    TextureInterpolation,
    TextureProjection,
)
from .material import Material, MaterialLibrary
from .stage import Stage
from .terrain import (
    TerrainLayer,
    TerrainMaterial,
    _TERRAIN_EXPORT_MAP,
    _TERRAIN_LAYER_EXPORT_MAP,
)


SCHEMA_VERSION = "http://json-schema.org/draft-07/schema#"


# Field type overrides for attributes that need special schema handling.
_FIELD_TYPE_OVERRIDES: Dict[Type, Dict[str, Any]] = {
    Material: {},
    Stage: {},
    TerrainMaterial: {},
    TerrainLayer: {},
}


# Dataclass field name -> JSON key.  If a field is absent, the field name is used.
_FIELD_RENAMES: Dict[Type, Dict[str, str]] = {
    Material: dict(maps.MATERIAL_EXPORT_MAP),
    Stage: dict(maps.STAGE_EXPORT_MAP),
    TerrainMaterial: dict(_TERRAIN_EXPORT_MAP),
    TerrainLayer: dict(_TERRAIN_LAYER_EXPORT_MAP),
}


# A few JSON keys for nested terrain layers (matches TerrainMaterial.to_dict).
_TERRAIN_LAYER_JSON_KEYS = {
    "base_color": "base_color",
    "normal": "normal",
    "roughness": "roughness",
    "ao": "ao",
    "height": "height",
}


class SchemaBuilder:
    """Build a JSON Schema for the agnostic BeamNG material model."""

    def __init__(self):
        self.schema: Dict[str, Any] = {
            "$schema": SCHEMA_VERSION,
            "$id": "https://beamng-materials/schema.json",
            "title": "BeamNG Material Library",
            "type": "object",
            "additionalProperties": {
                "oneOf": [
                    {"$ref": "#/$defs/Material"},
                    {"$ref": "#/$defs/TerrainMaterial"},
                ]
            },
            "$defs": {},
        }
        self._built: set = set()

    def build(self) -> Dict[str, Any]:
        self.schema["$defs"] = self._build_defs()
        return self.schema

    def _build_defs(self) -> Dict[str, Any]:
        defs: Dict[str, Any] = {}
        for cls in (
            Material,
            Stage,
            TerrainMaterial,
            TerrainLayer,
            MaterialLibrary,
            AlphaBlendMode,
            MaterialVersion,
            NormalMapSpace,
            TextureExtension,
            TextureInterpolation,
            TextureProjection,
            ImageSource,
        ):
            defs[cls.__name__] = self._schema_for(cls)
        return defs

    def _schema_for(self, t: Any) -> Any:
        if isinstance(t, type) and issubclass(t, Enum):
            return {
                "type": "string",
                "enum": [e.value for e in t],
                "description": f"{t.__doc__ or t.__name__}",
            }

        if dataclasses.is_dataclass(t):
            return self._dataclass_schema(t)

        origin = get_origin(t)
        if origin in (Union, UnionType):
            args = [a for a in get_args(t) if a is not type(None)]
            if not args:
                return {}
            if len(args) == 1:
                s = self._schema_for(args[0])
            else:
                s = {"oneOf": [self._schema_for(a) for a in args]}
            if type(None) in get_args(t):
                s = {"anyOf": [s, {"type": "null"}]}
            return s

        if t is str:
            return {"type": "string"}
        if t is int:
            return {"type": "integer"}
        if t is float:
            return {"type": "number"}
        if t is bool:
            return {"type": "boolean"}
        if t is Any:
            return True

        if origin in (list, List):
            args = get_args(t)
            items = self._schema_for(args[0]) if args else True
            return {"type": "array", "items": items}

        if origin in (dict, Dict):
            return {"type": "object"}

        return True

    def _dataclass_schema(self, cls: Type) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": "object",
            "title": cls.__name__,
            "properties": {},
            "additionalProperties": True,
        }
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = {f.name: f.type for f in dataclasses.fields(cls)}

        overrides = _FIELD_TYPE_OVERRIDES.get(cls, {})
        renames = _FIELD_RENAMES.get(cls, {})

        required: List[str] = []
        for field in dataclasses.fields(cls):
            if field.name == "extra":
                continue

            if field.name in overrides:
                prop_schema = overrides[field.name]
            else:
                t = hints.get(field.name, field.type)
                prop_schema = self._schema_for(t)

            # TerrainMaterial nests its PBR layers rather than flattening them.
            if cls is TerrainMaterial and field.name in _TERRAIN_LAYER_JSON_KEYS:
                key = _TERRAIN_LAYER_JSON_KEYS[field.name]
                prop_schema = {"$ref": "#/$defs/TerrainLayer"}
            else:
                key = renames.get(field.name, field.name)

            if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                required.append(key)

            out["properties"][key] = prop_schema

        if cls is MaterialLibrary:
            out["type"] = "object"
            out["additionalProperties"] = {
                "oneOf": [
                    {"$ref": "#/$defs/Material"},
                    {"$ref": "#/$defs/TerrainMaterial"},
                ]
            }

        if required:
            out["required"] = required

        return out


def generate_schema() -> Dict[str, Any]:
    """Return the JSON Schema as a dictionary."""
    return SchemaBuilder().build()


def write_schema(path: str | Path, indent: int = 2) -> None:
    """Write the generated JSON Schema to ``path``."""
    schema = generate_schema()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(schema, indent=indent), encoding="utf-8")
