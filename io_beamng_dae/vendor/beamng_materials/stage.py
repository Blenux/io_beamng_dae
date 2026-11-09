"""A single BeamNG material stage / layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import maps

STAGE_BOOL_ATTRIBUTES = {
    "emissive",
    "glow",
    "instance_diffuse",
    "instance_emissive",
    "instance_opacity",
    "palette_base_color",
    "palette_clear_coat",
    "palette_clear_coat_roughness",
    "palette_metallic",
    "palette_roughness",
    "pixel_specular",
    "stage_double_sided",
    "subsurface",
    "use_anisotropic",
    "use_anisotropic_filter",
    "vert_color",
    "vert_color_emissive",
}


@dataclass
class Stage:
    """One of the four possible material stages (layers).

    BeamNG supports up to four stages blended by ``activeLayers``.  This
    dataclass exposes every documented JSON key as a typed attribute.  Any key
    not listed here is stored in ``extra`` so that round-tripping is lossless.
    """

    anim_flags: Optional[int] = None
    ao_map: Optional[str] = None
    ao_map_uv: Optional[int] = None
    base_color_factor: Optional[List[float]] = None
    clear_coat_bottom_normal_map: Optional[str] = None
    clear_coat_bottom_normal_map_uv: Optional[int] = None
    clear_coat_bottom_normal_strength: Optional[float] = None
    clear_coat_factor: Optional[float] = None
    clear_coat_map: Optional[str] = None
    clear_coat_map_uv: Optional[int] = None
    clear_coat_roughness_factor: Optional[float] = None
    clear_coat_roughness_map: Optional[str] = None
    clear_coat_roughness_map_uv: Optional[int] = None
    color_map: Optional[str] = None
    color_map_color: Optional[List[float]] = None
    color_map_uv: Optional[int] = None
    color_palette_map: Optional[str] = None
    color_palette_map_uv: Optional[int] = None
    detail_ao_map: Optional[str] = None
    detail_ao_map_strength: Optional[float] = None
    detail_base_color_map_strength: Optional[float] = None
    detail_map: Optional[str] = None
    detail_map_uv: Optional[int] = None
    detail_metallic_map: Optional[str] = None
    detail_metallic_map_strength: Optional[float] = None
    detail_normal_map: Optional[str] = None
    detail_normal_map_uv: Optional[int] = None
    detail_normal_strength: Optional[float] = None
    detail_opacity_map: Optional[str] = None
    detail_opacity_map_strength: Optional[float] = None
    detail_roughness_map: Optional[str] = None
    detail_roughness_map_strength: Optional[float] = None
    detail_scale: Optional[List[float]] = None
    detail_strength: Optional[float] = None
    emissive: Optional[bool] = None
    emissive_factor: Optional[List[float]] = None
    emissive_intensity_nits: Optional[int] = None
    emissive_map: Optional[str] = None
    emissive_map_color: Optional[List[float]] = None
    emissive_map_uv: Optional[int] = None
    glow: Optional[bool] = None
    glow_factor: Optional[float] = None
    instance_diffuse: Optional[bool] = None
    instance_emissive: Optional[bool] = None
    instance_opacity: Optional[bool] = None
    metallic_factor: Optional[float] = None
    metallic_map: Optional[str] = None
    metallic_map_uv: Optional[int] = None
    normal_detail_map_uv: Optional[int] = None
    normal_map: Optional[str] = None
    normal_map_strength: Optional[float] = None
    normal_map_uv: Optional[int] = None
    opacity_detail_map: Optional[str] = None
    opacity_detail_map_uv: Optional[int] = None
    opacity_factor: Optional[float] = None
    opacity_map: Optional[str] = None
    opacity_map_uv: Optional[int] = None
    overlay_map: Optional[str] = None
    overlay_map_uv: Optional[int] = None
    palette_base_color: Optional[bool] = None
    palette_clear_coat: Optional[bool] = None
    palette_clear_coat_roughness: Optional[bool] = None
    palette_metallic: Optional[bool] = None
    palette_roughness: Optional[bool] = None
    pixel_specular: Optional[bool] = None
    reflectivity_map: Optional[str] = None
    reflectivity_map_uv: Optional[int] = None
    retroreflective_color: Optional[List[float]] = None
    retroreflectivity: Optional[int] = None
    roughness_detail_map: Optional[str] = None
    roughness_detail_map_uv: Optional[int] = None
    roughness_factor: Optional[float] = None
    roughness_map: Optional[str] = None
    roughness_map_uv: Optional[int] = None
    scroll_dir: Optional[List[float]] = None
    sequence_frame_per_sec: Optional[int] = None
    sequence_segment_size: Optional[float] = None
    specular_color: Optional[List[float]] = None
    specular_map: Optional[str] = None
    specular_map_uv: Optional[int] = None
    specular_power: Optional[int] = None
    stage_double_sided: Optional[bool] = None
    subsurface: Optional[bool] = None
    use_anisotropic: Optional[bool] = None
    use_anisotropic_filter: Optional[bool] = None
    vert_color: Optional[bool] = None
    vert_color_emissive: Optional[bool] = None
    wave_amp: Optional[float] = None
    wave_freq: Optional[float] = None
    wave_dir: Optional[List[float]] = None
    wave_type: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Stage":
        """Create a Stage from the raw JSON stage dictionary."""
        stage = cls()
        for key, value in data.items():
            if key in maps.STAGE_IMPORT_MAP:
                attr = maps.STAGE_IMPORT_MAP[key]
                if attr in STAGE_BOOL_ATTRIBUTES and value is not None:
                    value = _coerce_bool(value)
                setattr(stage, attr, value)
            else:
                stage.extra[key] = value
        return stage

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this stage to the raw JSON dictionary representation."""
        out = {}
        for key, value in self.__dict__.items():
            if key in ("extra",):
                continue
            if value is None:
                continue
            json_key = maps.STAGE_EXPORT_MAP.get(key)
            if json_key:
                out[json_key] = value
        # Preserve any unknown keys that were present at load time.
        for key, value in self.extra.items():
            if key not in out:
                out[key] = value
        return out

    @property
    def base_color_map(self) -> Optional[str]:
        """Convenience alias for ``color_map`` (base / diffuse texture)."""
        return self.color_map

    @base_color_map.setter
    def base_color_map(self, value: Optional[str]) -> None:
        self.color_map = value

    @property
    def base_color_map_use_uv(self) -> Optional[int]:
        """Convenience alias for ``color_map_uv``."""
        return self.color_map_uv

    @base_color_map_use_uv.setter
    def base_color_map_use_uv(self, value: Optional[int]) -> None:
        self.color_map_uv = value

    @property
    def diffuse_color(self) -> Optional[List[float]]:
        """Convenience alias for ``color_map_color`` (legacy v1.0 name)."""
        return self.color_map_color

    @diffuse_color.setter
    def diffuse_color(self, value: Optional[List[float]]) -> None:
        self.color_map_color = value

    @property
    def emissive_color(self) -> Optional[List[float]]:
        """Convenience alias for ``emissive_map_color``."""
        return self.emissive_map_color

    @emissive_color.setter
    def emissive_color(self, value: Optional[List[float]]) -> None:
        self.emissive_map_color = value

    def is_empty(self) -> bool:
        return all(v is None or v == [] or v == {} for k, v in self.__dict__.items() if k != "extra") and not self.extra


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)
