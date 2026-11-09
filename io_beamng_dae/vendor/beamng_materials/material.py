"""BeamNG material and material library data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import maps
from .enums import AlphaBlendMode, MaterialVersion
from .stage import Stage


@dataclass
class Material:
    """A single BeamNG material definition.

    Mirrors the fields used in ``main.materials.json`` and is version-agnostic.
    Unknown top-level keys are preserved in ``extra``.
    """

    active_layers: Optional[int] = None
    translucent_blend_op: Optional[AlphaBlendMode] = None
    alpha_ref: Optional[int] = None
    alpha_test: Optional[bool] = None
    annotation: Optional[str] = None
    annotation_map: Optional[str] = None
    cast_shadows: Optional[bool] = None
    class_name: Optional[str] = None
    cube_face: Optional[str] = None
    cubemap: Optional[str] = None
    detail_map_scale0: Optional[str] = None
    diffuse_color_slot: Optional[str] = None
    double_sided: Optional[bool] = None
    dynamic_cubemap: Optional[bool] = None
    glow: Optional[bool] = None
    ground_depth: Optional[float] = None
    ground_type_game: Optional[str] = None
    groundmodel_name: Optional[str] = None
    internal_name: Optional[str] = None
    invert_back_face_normals: Optional[bool] = None
    map_to: Optional[str] = None
    material_tag0: Optional[str] = None
    material_tag1: Optional[str] = None
    material_tag2: Optional[str] = None
    name: Optional[str] = None
    order_simset: Optional[int] = None
    persistent_id: Optional[str] = None
    recv_shadows: Optional[bool] = None
    specular_color0: Optional[List[float]] = None
    specular_color1: Optional[List[float]] = None
    specular_color2: Optional[List[float]] = None
    specular_strength0: Optional[float] = None
    sub_surface: Optional[bool] = None
    sub_surface0: Optional[bool] = None
    sub_surface1: Optional[bool] = None
    sub_surface_intensity: Optional[float] = None
    translucent: Optional[bool] = None
    version: Optional[MaterialVersion] = None
    z_write: Optional[bool] = None
    stages: List[Stage] = field(default_factory=lambda: [Stage(), Stage(), Stage(), Stage()])
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)
    metadata: Optional[Any] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.stages:
            self.stages = [Stage() for _ in range(4)]
        while len(self.stages) < 4:
            self.stages.append(Stage())

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "Material":
        """Build a Material from its JSON object.  ``name`` is the file key."""
        mat = cls()
        mat.name = data.get("name", name)

        for key, value in data.items():
            if key == "Stages":
                continue
            if key in maps.MATERIAL_IMPORT_MAP:
                attr = maps.MATERIAL_IMPORT_MAP[key]
                if attr == "version" and value is not None:
                    if isinstance(value, (int, float)):
                        value = "{:.1f}".format(value)
                    value = MaterialVersion(str(value))
                elif attr == "translucent_blend_op" and value is not None:
                    value = AlphaBlendMode(value)
                elif attr in ("double_sided", "alpha_test", "translucent", "dynamic_cubemap",
                              "z_write", "recv_shadows", "cast_shadows", "invert_backface_normals",
                              "glow", "sub_surface", "sub_surface0", "sub_surface1"):
                    value = _coerce_bool(value)
                setattr(mat, attr, value)
            else:
                mat.extra[key] = value

        stages_raw = data.get("Stages", [{}, {}, {}, {}])
        mat.stages = [Stage.from_dict(s) if s else Stage() for s in stages_raw[:4]]
        while len(mat.stages) < 4:
            mat.stages.append(Stage())

        if mat.version is None:
            mat.version = _infer_version(mat.stages)
        if mat.map_to is None:
            mat.map_to = mat.name
        if mat.class_name is None:
            mat.class_name = "Material"
        if mat.active_layers is None:
            mat.active_layers = max(1, sum(1 for s in mat.stages if not s.is_empty()))
        if mat.translucent_blend_op is None:
            mat.translucent_blend_op = AlphaBlendMode.NONE

        return mat

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this material to a JSON-ready dictionary."""
        out = {}
        for key, value in self.__dict__.items():
            if key in ("stages", "extra"):
                continue
            if value is None:
                continue
            json_key = maps.MATERIAL_EXPORT_MAP.get(key)
            if json_key:
                if key == "version":
                    value = float(value.value)
                elif key == "translucent_blend_op":
                    value = value.value
                elif key in ("double_sided", "alpha_test", "translucent", "dynamic_cubemap",
                             "z_write", "recv_shadows", "cast_shadows", "invert_backface_normals"):
                    value = bool(value)
                out[json_key] = value

        stages_out = [s.to_dict() for s in self.stages]
        if any(stages_out):
            out["Stages"] = stages_out

        for key, value in self.extra.items():
            if key not in out:
                out[key] = value

        return out

    @property
    def active_stage_count(self) -> int:
        return max(1, sum(1 for s in self.stages if not s.is_empty()))

    def to_cs(self, declaration: str = "singleton", form: str = "Material") -> str:
        """Return this material as a legacy TorqueScript ``*.cs`` definition."""
        from .cs import material_to_cs
        return material_to_cs(self, declaration=declaration, form=form)


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


_V1_5_INDICATORS = {
    "ao_map",
    "base_color_factor",
    "clear_coat_bottom_normal_map",
    "clear_coat_map",
    "clear_coat_roughness_factor",
    "clear_coat_roughness_map",
    "detail_ao_map",
    "detail_metallic_map",
    "detail_normal_map",
    "detail_opacity_map",
    "detail_roughness_map",
    "emissive_map",
    "metallic_factor",
    "metallic_map",
    "opacity_factor",
    "opacity_map",
    "roughness_factor",
    "roughness_map",
}

_V1_INDICATORS = {
    "color_map_color",
    "color_palette_map",
    "specular_color",
    "specular_map",
    "specular_power",
}


def _stage_has_any(stage: Stage, attrs: set[str]) -> bool:
    return any(getattr(stage, a) is not None for a in attrs)


def _infer_version(stages: List[Stage]) -> MaterialVersion:
    """Infer version from the stage data when ``version`` is absent."""
    for s in stages:
        if _stage_has_any(s, _V1_5_INDICATORS):
            return MaterialVersion.V1_5
    for s in stages:
        if _stage_has_any(s, _V1_INDICATORS):
            return MaterialVersion.V1
    return MaterialVersion.V1_5


@dataclass
class MaterialLibrary:
    """A collection of materials keyed by their file/material name."""

    materials: Dict[str, Material] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MaterialLibrary":
        lib = cls()
        for key, value in data.items():
            lib.materials[key] = Material.from_dict(key, value)
        return lib

    def to_dict(self) -> Dict[str, Any]:
        return {key: mat.to_dict() for key, mat in self.materials.items()}

    def __getitem__(self, item: str) -> Material:
        return self.materials[item]

    def __setitem__(self, item: str, value: Material) -> None:
        self.materials[item] = value

    def __iter__(self):
        return iter(self.materials)

    def __len__(self) -> int:
        return len(self.materials)
