"""Application-specific converters for the agnostic material model.

This module turns :class:`Material` / :class:`TerrainMaterial` objects into
representations that Blender and the Blender fork CDAE importer can consume
without any dependency on ``bpy`` or C++.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .cs import material_to_cs
from .material import Material, MaterialLibrary
from .stage import Stage
from .terrain import TerrainMaterial


# CDAE-specific renames over the standard JSON keys.  Most keys are identical,
# but the CDAE parser/serializer prefers ``*MapUV`` for UV indices and has a
# couple of strength / color names that differ from ``main.materials.json``.
_CDAE_STAGE_RENAMES = {
    # UseUV -> UV aliases (CDAE reads both; UV is preferred).
    "baseColorMapUseUV": "colorMapUV",
    "normalMapUseUV": "normalMapUV",
    "metallicMapUseUV": "metallicMapUV",
    "roughnessMapUseUV": "roughnessMapUV",
    "ambientOcclusionMapUseUV": "ambientOcclusionMapUV",
    "opacityMapUseUV": "opacityMapUV",
    "emissiveMapUseUV": "emissiveMapUV",
    "clearCoatMapUseUV": "clearCoatMapUV",
    "colorPaletteMapUseUV": "colorPaletteMapUV",
    "detailMapUseUV": "detailMapUV",
    "detailNormalMapUseUV": "detailNormalMapUV",
    "clearCoatBottomNormalMapUseUV": "clearCoatBottomNormalMapUV",
    "clearCoatRoughnessMapUseUV": "clearCoatRoughnessMapUV",
    "specularMapUseUV": "specularMapUV",
    "overlayMapUseUV": "overlayMapUV",
    "reflectivityMapUseUV": "reflectivityMapUV",
    "roughnessDetailMapUseUV": "roughnessDetailMapUV",
    "opacityDetailMapUseUV": "opacityDetailMapUV",
    # Names that differ between the main JSON and CDAE JSON.
    "baseColor": "diffuseColor",
    "emissiveColor": "emissiveFactor",
    "detailStrength": "detailBaseColorMapStrength",
    "detailNormalStrength": "detailNormalMapStrength",
}


_CDAE_MATERIAL_RENAMES: Dict[str, str] = {}


def _transform_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply CDAE-specific key renames and aliases to a stage dictionary."""
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if key in _CDAE_STAGE_RENAMES:
            cdae_key = _CDAE_STAGE_RENAMES[key]
            if cdae_key == "emissiveFactor" and value is not None and isinstance(value, (list, tuple)):
                out["emissiveFactor"] = list(value)
                continue
            if cdae_key == "diffuseColor" and value is not None:
                out["diffuseColor"] = value
                continue
            if cdae_key == "detailBaseColorMapStrength" and value is not None:
                out["detailBaseColorMapStrength"] = value
                # keep the legacy fallback too
                out["detailStrength"] = value
                continue
            if cdae_key == "detailNormalMapStrength" and value is not None:
                out["detailNormalMapStrength"] = value
                out["detailNormalStrength"] = value
                continue
            out[cdae_key] = value
            # also keep the original key as a fallback for round-trips
            out[key] = value
        else:
            out[key] = value
    return out


def to_cdae_dict(material: Material | TerrainMaterial) -> Dict[str, Any]:
    """Convert a material to a CDAE-compatible material dictionary.

    The output is the same shape that the Blender fork's
    ``cdae_material_json.cc`` parser/serializer consumes.
    """
    if isinstance(material, TerrainMaterial):
        return material.to_dict()

    data = material.to_dict()
    if "Stages" in data:
        data["Stages"] = [_transform_dict(s) if s else s for s in data["Stages"]]
    return data


def from_cdae_dict(name: str, data: Dict[str, Any]) -> Material:
    """Create a :class:`Material` from a CDAE-style dictionary."""
    data = dict(data)
    for stage in data.get("Stages", []):
        if not isinstance(stage, dict):
            continue
        emissive = stage.get("emissiveFactor")
        if isinstance(emissive, (list, tuple)):
            stage["emissiveColor"] = list(emissive)
            stage.pop("emissiveFactor", None)
    return Material.from_dict(name, data)


def to_blender_node(material: Material) -> Dict[str, Any]:
    """Return a generic Blender node description for the material."""
    nodes: List[Dict[str, Any]] = [
        {
            "type": "ShaderNodeBeamngMaterial",
            "name": material.name or "BeamNG_Material",
            "inputs": _material_inputs(material),
        }
    ]
    for i, stage in enumerate(material.stages):
        if stage.is_empty():
            continue
        nodes.append(
            {
                "type": "ShaderNodeBeamngMaterialStage",
                "name": f"{material.name or 'BeamNG_Material'}_stage_{i}",
                "stage_index": i,
                "inputs": _stage_inputs(stage),
            }
        )
    return {
        "type": "BeamNGMaterialNodeTree",
        "material_name": material.name,
        "nodes": nodes,
        "links": [
            {"from": n["name"], "to": nodes[0]["name"], "socket": "Stages"}
            for n in nodes[1:]
        ],
    }


def _material_inputs(material: Material) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    for key, value in material.__dict__.items():
        if key in ("stages", "extra", "metadata"):
            continue
        if value is None:
            continue
        inputs[_snake_to_blender(key)] = value
    for key, value in material.extra.items():
        inputs[key] = value
    return inputs


def _stage_inputs(stage: Stage) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    for key, value in stage.__dict__.items():
        if key in ("extra",):
            continue
        if value is None:
            continue
        inputs[_snake_to_blender(key)] = value
    for key, value in stage.extra.items():
        inputs[key] = value
    return inputs


def _snake_to_blender(name: str) -> str:
    """Convert a snake_case attribute name to a Blender-style socket name."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def library_to_cdae(library: MaterialLibrary) -> Dict[str, Any]:
    """Convert a whole library to a CDAE-style ``main.materials.json`` dict."""
    out: Dict[str, Any] = {}
    for name, mat in library.materials.items():
        if isinstance(mat, TerrainMaterial):
            continue
        out[name] = to_cdae_dict(mat)
    return out


def library_to_blender(library: MaterialLibrary) -> List[Dict[str, Any]]:
    """Return a list of Blender node tree descriptions for every material."""
    return [to_blender_node(mat) for mat in library.materials.values() if isinstance(mat, Material)]
