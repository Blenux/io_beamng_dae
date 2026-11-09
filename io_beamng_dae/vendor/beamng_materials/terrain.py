"""Terrain material data model.

BeamNG terrain materials use a different shape than standard ``Material``
objects.  They are declared with ``new TerrainMaterial()`` and carry legacy
``diffuseMap`` / ``detailMap`` / ``macroMap`` fields as well as PBR v1.5
per-channel layers (``baseColorBaseTex``, ``normalBaseTex``, ``roughnessBaseTex``,
etc.) and distance-blend parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TerrainLayer:
    """One PBR texture layer for a terrain material.

    Each layer is split into three detail levels: base, macro and detail.
    ``*TexSize`` values are usually scalars (meters in the world) and
    ``*Strength`` values are ``Point2F`` pairs.
    """

    base_tex: Optional[str] = None
    macro_tex: Optional[str] = None
    detail_tex: Optional[str] = None
    base_tex_size: Optional[float] = None
    macro_tex_size: Optional[float] = None
    detail_tex_size: Optional[float] = None
    macro_strength: Optional[List[float]] = None
    detail_strength: Optional[List[float]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TerrainLayer":
        layer = cls()
        for key, value in data.items():
            if key in _TERRAIN_LAYER_IMPORT_MAP:
                setattr(layer, _TERRAIN_LAYER_IMPORT_MAP[key], value)
            else:
                layer.extra[key] = value
        return layer

    def to_dict(self) -> Dict[str, Any]:
        out = {}
        for key, value in self.__dict__.items():
            if key == "extra" or value is None:
                continue
            out[_TERRAIN_LAYER_EXPORT_MAP.get(key, key)] = value
        if self.extra:
            out.update(self.extra)
        return out

    extra: Dict[str, Any] = field(default_factory=dict, repr=False)


_TERRAIN_LAYER_SUFFIX_IMPORT = {
    "BaseTex": "base_tex",
    "MacroTex": "macro_tex",
    "DetailTex": "detail_tex",
    "BaseTexSize": "base_tex_size",
    "MacroTexSize": "macro_tex_size",
    "DetailTexSize": "detail_tex_size",
    "MacroStrength": "macro_strength",
    "DetailStrength": "detail_strength",
}

_TERRAIN_LAYER_IMPORT_MAP: Dict[str, str] = {}
_TERRAIN_LAYER_EXPORT_MAP: Dict[str, str] = {}
for _suffix, _attr in _TERRAIN_LAYER_SUFFIX_IMPORT.items():
    _TERRAIN_LAYER_IMPORT_MAP[_suffix] = _attr
    _TERRAIN_LAYER_EXPORT_MAP[_attr] = _suffix

_TERRAIN_LAYER_RE = re.compile(r"^(baseColor|normal|roughness|ao|height)(BaseTex|MacroTex|DetailTex|BaseTexSize|MacroTexSize|DetailTexSize|MacroStrength|DetailStrength)$")


_LAYER_ATTR_NAME = {
    "baseColor": "base_color",
    "normal": "normal",
    "roughness": "roughness",
    "ao": "ao",
    "height": "height",
}


def _layer_attr_from_key(key: str) -> Optional[tuple[str, str]]:
    match = _TERRAIN_LAYER_RE.match(key)
    if not match:
        return None
    layer_attr = _LAYER_ATTR_NAME.get(match.group(1))
    suffix = match.group(2)
    if not layer_attr or suffix not in _TERRAIN_LAYER_SUFFIX_IMPORT:
        return None
    return layer_attr, _TERRAIN_LAYER_SUFFIX_IMPORT[suffix]


_TERRAIN_IMPORT_MAP = {
    "internalName": "internal_name",
    "isManaged": "is_managed",
    "Enabled": "enabled",
    "diffuseMap": "diffuse_map",
    "detailMap": "detail_map",
    "macroMap": "macro_map",
    "normalMap": "normal_map",
    "diffuseSize": "diffuse_size",
    "detailSize": "detail_size",
    "detailDistance": "detail_distance",
    "macroStrength": "macro_strength",
    "detailStrength": "detail_strength",
    "detailBrightness": "detail_brightness",
    "parallaxScale": "parallax_scale",
    "useSideProjection": "use_side_projection",
    "macroDistances": "macro_distances",
    "detailDistances": "detail_distances",
    "macroDistAtten": "macro_dist_atten",
    "detailDistAtten": "detail_dist_atten",
}

_TERRAIN_EXPORT_MAP = {v: k for k, v in _TERRAIN_IMPORT_MAP.items()}


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)


_TERRAIN_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _is_number(text: str) -> bool:
    return _TERRAIN_NUMBER_RE.match(text.strip()) is not None


def _to_number(text: str) -> int | float:
    text = text.strip()
    if "." in text or "e" in text.lower():
        return float(text)
    return int(text)


def _coerce_terrain_value(value: Any) -> Any:
    """Convert numeric strings into numbers / lists for terrain fields."""
    if not isinstance(value, str):
        return value
    parts = value.split()
    if parts and all(_is_number(p) for p in parts):
        numbers = [_to_number(p) for p in parts]
        return numbers[0] if len(numbers) == 1 else numbers
    return value


@dataclass
class TerrainMaterial:
    """A BeamNG terrain material definition."""

    class_name: str = "TerrainMaterial"
    name: Optional[str] = None
    internal_name: Optional[str] = None
    is_managed: Optional[bool] = True
    enabled: Optional[bool] = True

    # Legacy single-texture paths
    diffuse_map: Optional[str] = None
    detail_map: Optional[str] = None
    macro_map: Optional[str] = None
    normal_map: Optional[str] = None

    # Legacy scalar properties
    diffuse_size: Optional[float] = None
    detail_size: Optional[float] = None
    detail_distance: Optional[float] = None
    macro_strength: Optional[float] = None
    detail_strength: Optional[float] = None
    detail_brightness: Optional[float] = None
    parallax_scale: Optional[float] = None
    use_side_projection: Optional[bool] = None

    # PBR v1.5 per-channel layers
    base_color: TerrainLayer = field(default_factory=TerrainLayer)
    normal: TerrainLayer = field(default_factory=TerrainLayer)
    roughness: TerrainLayer = field(default_factory=TerrainLayer)
    ao: TerrainLayer = field(default_factory=TerrainLayer)
    height: TerrainLayer = field(default_factory=TerrainLayer)

    # Distance blending
    macro_distances: Optional[List[float]] = None
    detail_distances: Optional[List[float]] = None
    macro_dist_atten: Optional[List[float]] = None
    detail_dist_atten: Optional[List[float]] = None

    extra: Dict[str, Any] = field(default_factory=dict, repr=False)
    metadata: Optional[Any] = field(default=None, repr=False)

    @classmethod
    def from_cs(cls, data: Dict[str, Any]) -> "TerrainMaterial":
        """Build a TerrainMaterial from a raw ``*.cs`` key/value dictionary."""
        mat = cls()
        for key, value in data.items():
            if key in _TERRAIN_IMPORT_MAP:
                attr = _TERRAIN_IMPORT_MAP[key]
                value = _coerce_terrain_value(value)
                if attr in ("is_managed", "enabled", "use_side_projection") and value is not None:
                    value = _coerce_bool(value)
                setattr(mat, attr, value)
            else:
                value = _coerce_terrain_value(value)
                layer = _layer_attr_from_key(key)
                if layer:
                    layer_attr, suffix_attr = layer
                    layer_obj = getattr(mat, layer_attr)
                    setattr(layer_obj, suffix_attr, value)
                else:
                    mat.extra[key] = value

        if not mat.name and mat.internal_name:
            mat.name = mat.internal_name
        if not mat.name:
            mat.name = mat.internal_name or "TerrainMaterial"
        return mat

    def to_cs(self) -> str:
        """Export this terrain material as a ``new TerrainMaterial() { ... };`` block."""
        from .cs import _format_cs_value

        lines = ["new TerrainMaterial() {"]

        for attr, value in self.__dict__.items():
            if attr in ("extra", "class_name", "name") or value is None:
                continue
            if attr in ("base_color", "normal", "roughness", "ao", "height"):
                layer = getattr(self, attr)
                if not isinstance(layer, TerrainLayer):
                    continue
                prefix = _LAYER_PREFIX.get(attr)
                if not prefix:
                    continue
                for suffix, sub_value in layer.__dict__.items():
                    if suffix == "extra" or sub_value is None:
                        continue
                    cs_key = f"{prefix}{_TERRAIN_LAYER_EXPORT_MAP.get(suffix, suffix)}"
                    lines.append(f"    {cs_key} = {_format_cs_value(sub_value)};")
                continue
            cs_key = _TERRAIN_EXPORT_MAP.get(attr)
            if cs_key:
                lines.append(f"    {cs_key} = {_format_cs_value(value)};")

        for key, value in sorted(self.extra.items()):
            lines.append(f"    {key} = {_format_cs_value(value)};")

        lines.append("};")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-ready dictionary for this terrain material."""
        out = {}
        for attr, value in self.__dict__.items():
            if attr in ("extra", "class_name") or value is None:
                continue
            if attr in ("base_color", "normal", "roughness", "ao", "height"):
                if isinstance(value, TerrainLayer):
                    out[attr] = value.to_dict()
                continue
            cs_key = _TERRAIN_EXPORT_MAP.get(attr, attr)
            out[cs_key] = value
        if self.extra:
            out.update(self.extra)
        return out


_LAYER_PREFIX = {
    "base_color": "baseColor",
    "normal": "normal",
    "roughness": "roughness",
    "ao": "ao",
    "height": "height",
}
