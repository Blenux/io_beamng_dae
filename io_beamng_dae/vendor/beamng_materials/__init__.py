"""Vendored copy of the beamng_materials library — agnostic BeamNG material data model and I/O.

This is a self-contained copy bundled with the io_beamng_dae addon so that
the addon does not require an external pip install.  Upstream:
/home/juso3d/Projects/beamng_materials/
"""
from __future__ import annotations

from .enums import (
    AlphaBlendMode,
    ImageSource,
    MaterialVersion,
    NormalMapSpace,
    TextureExtension,
    TextureInterpolation,
    TextureProjection,
)
from .cs import load_cs, loads_cs, save_cs, dumps_cs
from .converter import (
    from_cdae_dict,
    library_to_blender,
    library_to_cdae,
    to_blender_node,
    to_cdae_dict,
)
from .io import load_json, load_library, save_json, save_library
from .material import Material, MaterialLibrary
from .metadata import (
    DeformGroupEntry,
    GlowMapEntry,
    MaterialRuntimeMetadata,
    VehicleMaterialMetadata,
    load_jbeam_metadata,
)
from .schema import generate_schema, write_schema
from .terrain import TerrainLayer, TerrainMaterial
from .stage import Stage
from .textures import TextureResolver, is_srgb_texture, resolve_texture_path

__all__ = [
    "AlphaBlendMode",
    "ImageSource",
    "MaterialVersion",
    "NormalMapSpace",
    "TextureExtension",
    "TextureInterpolation",
    "TextureProjection",
    "DeformGroupEntry",
    "from_cdae_dict",
    "GlowMapEntry",
    "library_to_blender",
    "library_to_cdae",
    "MaterialRuntimeMetadata",
    "to_blender_node",
    "to_cdae_dict",
    "VehicleMaterialMetadata",
    "dumps_cs",
    "generate_schema",
    "load_cs",
    "loads_cs",
    "load_jbeam_metadata",
    "load_json",
    "load_library",
    "save_cs",
    "save_json",
    "save_library",
    "write_schema",
    "Material",
    "MaterialLibrary",
    "Stage",
    "TerrainLayer",
    "TerrainMaterial",
    "TextureResolver",
    "is_srgb_texture",
    "resolve_texture_path",
]

__version__ = "0.1.0"
