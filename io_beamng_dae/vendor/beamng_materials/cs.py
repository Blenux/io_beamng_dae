"""Legacy TorqueScript ``*.cs`` material I/O.

BeamNG mods and older game content still use ``singleton Material(name) { ... };``
style definitions.  This module reads and writes them using the same
version-agnostic :class:`~beamng_materials.material.Material` model as the
JSON files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import maps
from .enums import AlphaBlendMode, MaterialVersion
from .material import Material, MaterialLibrary
from .stage import Stage
from .terrain import TerrainMaterial

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_CS_BLOCK_RE = re.compile(
    r"(?:singleton|new)\s+(?P<form>Material|material|TerrainMaterial)\s*\(\s*(?P<name>[^)]*?)\s*\)\s*\{(?P<body>.*?)\};",
    re.DOTALL | re.IGNORECASE,
)

# Matches a single TorqueScript assignment inside a material block.
_CS_STMT_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\[\s*(?P<idx>\d+)\s*\])?"
    r"\s*=\s*(?P<value>.*)$",
    re.MULTILINE,
)

_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _is_number(text: str) -> bool:
    return _NUMBER_RE.match(text.strip()) is not None


def _to_number(text: str) -> int | float:
    text = text.strip()
    if "." in text or "e" in text.lower():
        return float(text)
    return int(text)


def _parse_value(raw: str) -> Any:
    """Parse a TorqueScript material value into a Python object.

    Quoted strings are unquoted.  If the unquoted body is a single number it
    is returned as a scalar, and if it is several whitespace-separated numbers
    it is returned as a list.  Unquoted tokens become strings, except for the
    literals ``true``/``false``.
    """
    raw = raw.strip()
    if not raw:
        return ""

    if raw.startswith('"') and raw.endswith('"'):
        inner = raw[1:-1]
        parts = inner.split()
        if parts and all(_is_number(p) for p in parts):
            numbers = [_to_number(p) for p in parts]
            return numbers[0] if len(numbers) == 1 else numbers
        return inner

    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _is_number(raw):
        return _to_number(raw)

    # Unquoted token (e.g. a cubemap object reference)
    return raw


def _strip_comments(text: str) -> str:
    # C-style block comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # C++ line comments
    text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    return text


# ---------------------------------------------------------------------------
# Stage key overrides
# ---------------------------------------------------------------------------

# Some .cs files use slightly different key names than the JSON files.  These
# .cs keys are remapped to typed Stage attributes before the data is stored.
_CS_STAGE_EXTRA_OVERRIDES = {
    "detailNormalMapStrength": "detail_normal_strength",
    "glowFactor": "glow_factor",
    "waveDir": "wave_dir",
}

# Stage attribute -> canonical .cs key.
CS_STAGE_EXPORT_MAP = {
    "anim_flags": "animFlags",
    "ao_map": "ambientOcclusionMap",
    "ao_map_uv": "ambientOcclusionMapUseUV",
    "base_color_factor": "baseColorFactor",
    "clear_coat_bottom_normal_map": "clearCoatBottomNormalMap",
    "clear_coat_bottom_normal_map_uv": "clearCoatBottomNormalMapUseUV",
    "clear_coat_bottom_normal_strength": "clearCoatBottomNormalStrength",
    "clear_coat_factor": "clearCoatFactor",
    "clear_coat_map": "clearCoatMap",
    "clear_coat_map_uv": "clearCoatMapUseUV",
    "clear_coat_roughness_factor": "clearCoatRoughnessFactor",
    "clear_coat_roughness_map": "clearCoatRoughnessMap",
    "clear_coat_roughness_map_uv": "clearCoatRoughnessMapUseUV",
    "color_map": "baseColorMap",
    "color_map_color": "diffuseColor",
    "color_map_uv": "baseColorMapUseUV",
    "color_palette_map": "colorPaletteMap",
    "color_palette_map_uv": "colorPaletteMapUseUV",
    "detail_ao_map": "detailAOMap",
    "detail_ao_map_strength": "detailAOMapStrength",
    "detail_base_color_map_strength": "detailBaseColorMapStrength",
    "detail_map": "detailMap",
    "detail_map_uv": "detailMapUseUV",
    "detail_metallic_map": "detailMetallicMap",
    "detail_metallic_map_strength": "detailMetallicMapStrength",
    "detail_normal_map": "detailNormalMap",
    "detail_normal_map_uv": "detailNormalMapUseUV",
    "detail_normal_strength": "detailNormalMapStrength",
    "detail_opacity_map": "detailOpacityMap",
    "detail_opacity_map_strength": "detailOpacityMapStrength",
    "detail_roughness_map": "detailRoughnessMap",
    "detail_roughness_map_strength": "detailRoughnessMapStrength",
    "detail_scale": "detailScale",
    "detail_strength": "detailStrength",
    "emissive": "emissive",
    "emissive_factor": "emissiveFactor",
    "emissive_intensity_nits": "emissiveIntensityNits",
    "emissive_map": "emissiveMap",
    "emissive_map_color": "emissiveColor",
    "emissive_map_uv": "emissiveMapUseUV",
    "glow": "glow",
    "glow_factor": "glowFactor",
    "instance_diffuse": "instanceDiffuse",
    "instance_emissive": "instanceEmissive",
    "instance_opacity": "instanceOpacity",
    "metallic_factor": "metallicFactor",
    "metallic_map": "metallicMap",
    "metallic_map_uv": "metallicMapUseUV",
    "normal_map": "normalMap",
    "normal_map_strength": "normalMapStrength",
    "normal_map_uv": "normalMapUseUV",
    "opacity_factor": "opacityFactor",
    "opacity_map": "opacityMap",
    "opacity_map_uv": "opacityMapUseUV",
    "overlay_map": "overlayMap",
    "overlay_map_uv": "overlayMapUseUV",
    "pixel_specular": "pixelSpecular",
    "reflectivity_map": "reflectivityMap",
    "reflectivity_map_uv": "reflectivityMapUseUV",
    "retroreflective_color": "retroreflectiveColor",
    "retroreflectivity": "retroreflectivity",
    "roughness_factor": "roughnessFactor",
    "roughness_map": "roughnessMap",
    "roughness_map_uv": "roughnessMapUseUV",
    "scroll_dir": "scrollDir",
    "specular_color": "specular",
    "specular_map": "specularMap",
    "specular_map_uv": "specularMapUseUV",
    "specular_power": "specularPower",
    "stage_double_sided": "doubleSided",
    "subsurface": "subsurface",
    "use_anisotropic": "useAnisotropic",
    "use_anisotropic_filter": "useAnisotropicFilter",
    "vert_color": "vertColor",
    "vert_color_emissive": "vertColorEmissive",
    "wave_amp": "waveAmp",
    "wave_dir": "waveDir",
    "wave_freq": "waveFreq",
    "wave_type": "waveType",
}


def _cs_stage_key(attr: str, version: MaterialVersion | None) -> Optional[str]:
    """Return the .cs key for a Stage attribute, optionally version-aware."""
    if attr == "color_map":
        if version is MaterialVersion.V1:
            return "diffuseMap"
        return "baseColorMap"
    if attr == "color_map_color":
        if version is MaterialVersion.V1:
            return "diffuseColor"
        return "baseColor"
    if attr == "color_map_uv":
        if version is MaterialVersion.V1:
            return "diffuseMapUseUV"
        return "baseColorMapUseUV"
    return CS_STAGE_EXPORT_MAP.get(attr)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _parse_terrain(name: str, body: str) -> TerrainMaterial:
    body = _strip_comments(body)
    raw: Dict[str, Any] = {"name": name}
    for raw_stmt in body.split(";"):
        raw_stmt = raw_stmt.strip()
        if not raw_stmt:
            continue
        match = _CS_STMT_RE.match(raw_stmt)
        if not match:
            continue
        key = match.group("key")
        value = _parse_value(match.group("value"))
        raw[key] = value
    return TerrainMaterial.from_cs(raw)


def _parse_block(name: str, form: str, body: str) -> Material | TerrainMaterial:
    if form and form.lower() == "terrainmaterial":
        return _parse_terrain(name, body)

    body = _strip_comments(body)

    material_data: Dict[str, Any] = {"name": name}
    if form and form != "Material":
        material_data["class"] = form

    stages_raw: List[Dict[str, Any]] = [{}, {}, {}, {}]

    for raw_stmt in body.split(";"):
        raw_stmt = raw_stmt.strip()
        if not raw_stmt:
            continue
        match = _CS_STMT_RE.match(raw_stmt)
        if not match:
            continue

        key = match.group("key")
        idx = match.group("idx")
        value = _parse_value(match.group("value"))

        if idx is not None:
            idx = int(idx)
            if 0 <= idx < 4:
                stages_raw[idx][key] = value
            continue

        # Unindexed key: material property or stage 0 fallback.
        if key in maps.MATERIAL_IMPORT_MAP:
            material_data[key] = value
        elif key in maps.STAGE_IMPORT_MAP:
            stages_raw[0][key] = value
        else:
            material_data[key] = value

    material_data["Stages"] = stages_raw
    mat = Material.from_dict(name, material_data)

    # Promote a few .cs-only keys from stage extras to typed attributes.
    for stage in mat.stages:
        for cs_key, attr in _CS_STAGE_EXTRA_OVERRIDES.items():
            if cs_key in stage.extra and attr in stage.__dict__:
                setattr(stage, attr, stage.extra.pop(cs_key))

    if not mat.name and mat.internal_name:
        mat.name = mat.internal_name
    if not mat.map_to:
        mat.map_to = mat.name

    return mat


def parse_cs(text: str) -> MaterialLibrary:
    """Parse a ``*.cs`` file contents and return a :class:`MaterialLibrary`."""
    library = MaterialLibrary()
    for match in _CS_BLOCK_RE.finditer(text):
        form = match.group("form").strip()
        name = match.group("name").strip()
        body = match.group("body")

        if form.lower() == "material":
            form = "Material"
        mat = _parse_block(name, form, body)

        if mat.name:
            library.materials[mat.name] = mat
        else:
            # Fall back to a generated key for unnamed TerrainMaterial blocks.
            key = mat.internal_name or f"{form}_{len(library.materials)}"
            library.materials[key] = mat

    return library


def loads_cs(text: str) -> MaterialLibrary:
    """Parse ``*.cs`` material definitions from a string."""
    return parse_cs(text)


def load_cs(path: str | Path) -> MaterialLibrary:
    """Load a ``*.cs`` material file."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_cs(text)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _format_cs_value(value: Any, is_color: bool = False) -> str:
    """Format a Python value for a ``*.cs`` assignment."""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, MaterialVersion):
        return f'"{value.value}"'
    if isinstance(value, AlphaBlendMode):
        return f'"{value.value}"'
    if isinstance(value, (list, tuple)):
        # Lists are emitted as space-separated numbers in quotes.
        return '"' + " ".join(str(v) for v in value) + '"'
    if isinstance(value, (int, float)):
        return str(value)
    # Strings and everything else are quoted.
    return f'"{value}"'


def _is_color_attr(attr: str) -> bool:
    return attr in {
        "color_map_color",
        "base_color_factor",
        "specular_color",
        "emissive_map_color",
        "emissive_factor",
        "clear_coat_factor",
        "clear_coat_roughness_factor",
        "metallic_factor",
        "roughness_factor",
        "opacity_factor",
        "normal_map_strength",
        "specular_power",
        "retroreflectivity",
        "glow_factor",
        "emissive_intensity_nits",
        "detail_normal_strength",
        "detail_base_color_map_strength",
        "detail_metallic_map_strength",
        "detail_roughness_map_strength",
        "detail_opacity_map_strength",
        "detail_ao_map_strength",
        "detail_strength",
        "specular_color0",
        "specular_color1",
        "specular_color2",
        "specular_strength0",
        "sub_surface_intensity",
        "ground_depth",
        "sub_surface0",
        "sub_surface1",
    }


def _format_material_attr(key: str, value: Any) -> Optional[str]:
    """Return a formatted material-level ``key = value;`` line or None."""
    if value is None:
        return None
    if key == "class_name":
        return None  # implicit in the singleton/new header
    if key == "name":
        return None
    if key == "map_to" and value == "":
        return None

    cs_key = maps.MATERIAL_EXPORT_MAP.get(key)
    if not cs_key:
        return None

    color = _is_color_attr(key)
    return f"    {cs_key} = {_format_cs_value(value, color)};"


def material_to_cs(material: Material | TerrainMaterial, declaration: str = "singleton", form: str = "Material") -> str:
    """Return one material formatted as a ``*.cs`` block."""
    if isinstance(material, TerrainMaterial):
        return material.to_cs()

    form = material.class_name or form
    if not material.name:
        name = material.internal_name or ""
    else:
        name = material.name

    if form == "TerrainMaterial":
        header = f"new TerrainMaterial() {{"
    else:
        header = f"{declaration} {form}({name}) {{"

    lines: List[str] = [header]

    # mapTo is only emitted when it differs from the material name.
    if material.map_to and material.map_to != name:
        lines.append(f'    mapTo = "{material.map_to}";')

    # Material-level attributes.
    material_items = []
    for attr, value in material.__dict__.items():
        if attr in ("stages", "extra"):
            continue
        formatted = _format_material_attr(attr, value)
        if formatted:
            material_items.append(formatted)
    material_items.sort()
    lines.extend(material_items)

    # Extra material-level keys (e.g. shaderModel, reflectivity, fresnelBias).
    for key, value in sorted(material.extra.items()):
        lines.append(f"    {key} = {_format_cs_value(value)};")

    # Stage attributes.
    for idx, stage in enumerate(material.stages):
        if stage.is_empty() and not stage.extra:
            continue

        stage_items = []
        for attr, value in stage.__dict__.items():
            if attr in ("extra",):
                continue
            if value is None:
                continue
            cs_key = _cs_stage_key(attr, material.version)
            if not cs_key:
                continue
            color = _is_color_attr(attr)
            stage_items.append((cs_key, f"    {cs_key}[{idx}] = {_format_cs_value(value, color)};"))

        for key, value in sorted(stage.extra.items()):
            stage_items.append((key, f"    {key}[{idx}] = {_format_cs_value(value)};"))

        stage_items.sort(key=lambda x: x[0])
        lines.extend(item[1] for item in stage_items)

    lines.append("};")
    return "\n".join(lines)


def library_to_cs(library: MaterialLibrary, declaration: str = "singleton") -> str:
    """Return all materials in a library formatted as a ``*.cs`` file."""
    blocks = [material_to_cs(m, declaration=declaration) for m in library.materials.values()]
    return "\n\n".join(blocks)


def dumps_cs(library: MaterialLibrary, declaration: str = "singleton") -> str:
    """Alias for :func:`library_to_cs`."""
    return library_to_cs(library, declaration=declaration)


def save_cs(library: MaterialLibrary, path: str | Path, declaration: str = "singleton") -> None:
    """Save a :class:`MaterialLibrary` to a ``*.cs`` file."""
    Path(path).write_text(dumps_cs(library, declaration=declaration) + "\n", encoding="utf-8")
