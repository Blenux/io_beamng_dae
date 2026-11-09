"""Scene-level vehicle paints — single source of truth, matching the fork's
Scene.beamng_vehicle_paints (DNA_scene_types.h BeamNGVehiclePaints) and the
BeamNGPaintResolver (io/jbeam/importer/beamng_paint_resolver.cc +
vehicle_config.cc resolve_paints()).

Resolution chain: pc paints → info.json defaultPaintName1/2/3 (or
defaultMultiPaintSetup) → info_<config>.json overrides → paint presets →
common.paintLibrary.json.

Paint baseColor values are sRGB in the JSON; stored here linearized
(bng_paint_color_from_base), alpha channel = paint intensity.
"""

import os
import json

import bpy
from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    CollectionProperty,
)
from bpy.types import PropertyGroup


# ---------------------------------------------------------------------------
# Scene storage — mirrors BeamNGVehiclePaintSlot / BeamNGVehiclePaints
# ---------------------------------------------------------------------------

_applying_paints = False


def _paint_update(self, context):
    """Scene paints feed named uniform nodes inside each graph — rewriting
    their values in place avoids a full rebuild per color-picker tick."""
    if _applying_paints:
        return
    from ..nodes.beamng_material_graph import update_paint_uniforms
    update_paint_uniforms()


class BeamNGVehiclePaintSlot(PropertyGroup):
    """One paint slot: name + color (linear RGBA) + metallic paint data.
    Defaults match MetallicPaintData {0.2, 0.5, 0.8, 0.0}."""

    name: StringProperty(name="Name", default="")
    color: FloatVectorProperty(
        name="Color", subtype='COLOR_GAMMA', size=4,
        default=(1.0, 1.0, 1.0, 1.0), min=0.0, soft_max=1.0,
        update=_paint_update)
    metallic: FloatProperty(
        name="Metallic", default=0.2, min=0.0, max=1.0,
        update=_paint_update)
    roughness: FloatProperty(
        name="Roughness", default=0.5, min=0.0, max=1.0,
        update=_paint_update)
    clearcoat: FloatProperty(
        name="Clear Coat", default=0.8, min=0.0, max=1.0,
        update=_paint_update)
    clearcoat_roughness: FloatProperty(
        name="CC Roughness", default=0.0, min=0.0, max=1.0,
        update=_paint_update)


class BeamNGVehiclePaints(PropertyGroup):
    """Scene.beamng_vehicle_paints — 3 paint slots
    ([0]=vehicle.color, [1]=colorPalette0, [2]=colorPalette1)."""

    slots: CollectionProperty(type=BeamNGVehiclePaintSlot)


def _ensure_slots(vp):
    while len(vp.slots) < 3:
        vp.slots.add()


# ---------------------------------------------------------------------------
# sRGB → linear (bng_paint_color_from_base)
# ---------------------------------------------------------------------------

def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _paint_color_from_base(src):
    return (_srgb_to_linear(src[0]), _srgb_to_linear(src[1]),
            _srgb_to_linear(src[2]), src[3])


# ---------------------------------------------------------------------------
# Paint resolution — resolve_paints() port
# ---------------------------------------------------------------------------

def _parse_paint_entry(d):
    """{baseColor:[r,g,b,a], metallic, roughness, clearcoat,
    clearcoatRoughness} → ((color4 sRGB), metallic, roughness, cc, ccr)."""
    base = d.get("baseColor") or [1.0, 1.0, 1.0, 1.0]
    color = tuple(float(v) for v in list(base[:4]) + [1.0] * (4 - len(base[:4])))
    return (
        color,
        float(d.get("metallic", 0.2)),
        float(d.get("roughness", 0.5)),
        float(d.get("clearcoat", 0.8)),
        float(d.get("clearcoatRoughness", 0.0)),
    )


def _find_named_paint(paints, name):
    """Match a paint by dict key or inner "name" (display name)."""
    for key, p in paints.items():
        if key == name or p.get("name") == name:
            return _parse_paint_entry(p), p.get("name", key)
    return None


def _resolve_single_paint(name, presets, library):
    found = _find_named_paint(presets, name)
    if found is None:
        found = _find_named_paint(library, name)
    if found is None:
        return (1.0, 1.0, 1.0, 1.0), 0.2, 0.5, 0.8, 0.0, name
    paint, pname = found
    return (*paint, pname)


def resolve_paints(pc_paints, info, paint_library):
    """resolve_paints() port.

    pc_paints: list of paint dicts from a .pc file (or None).
    info: parsed info.json dict.
    paint_library: parsed "paints" dict from common.paintLibrary.json.
    Returns [(srgb color4, metallic, roughness, clearcoat, ccr, name), ...]."""
    if pc_paints:
        return [(*_parse_paint_entry(p), p.get("name", ""))
                for p in pc_paints[:3]]

    name1 = info.get("defaultPaintName1", "")
    name2 = info.get("defaultPaintName2", "")
    name3 = info.get("defaultPaintName3", "")

    mps = info.get("defaultMultiPaintSetup") or {}
    if not name1 and mps.get("paint1"):
        name1 = mps["paint1"]
        name2 = name2 or mps.get("paint2", "")
        name3 = name3 or mps.get("paint3", "")

    if not name1:
        return []
    name2 = name2 or name1
    name3 = name3 or name1

    presets = info.get("paints") or {}
    return [
        _resolve_single_paint(name1, presets, paint_library),
        _resolve_single_paint(name2, presets, paint_library),
        _resolve_single_paint(name3, presets, paint_library),
    ]


def _load_sjson(path):
    from ..vendor.beamng_materials import _sjson
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return _sjson.parse(f.read()) or {}
    except Exception:
        return {}


def _read_pc_paints(path):
    """Extract the "paints" array (modern) or "colors" array (legacy) from
    a .pc file. Returns list of paint dicts or None."""
    data = _load_sjson(path)
    if not isinstance(data, dict):
        return None
    paints = data.get("paints")
    if isinstance(paints, list) and paints:
        return [p for p in paints if isinstance(p, dict)]
    colors = data.get("colors")
    if isinstance(colors, list) and colors:
        # Legacy flat array: groups of 4 floats per paint.
        out = []
        for i in range(0, min(len(colors), 12), 4):
            out.append({"baseColor": colors[i:i + 4]})
        return out or None
    return None


def resolve_and_apply_paints(scene, vehicle_dir, config_name="",
                             pc_path=None):
    """BeamNGPaintResolver::resolve_and_apply() port.

    vehicle_dir: the vehicle's directory (containing info.json).
    config_name: optional .pc/config name → info_<config>.json overrides.
    pc_path: optional .pc file → its paints win directly.
    """
    pc_paints = _read_pc_paints(pc_path) if pc_path else None

    info = _load_sjson(os.path.join(vehicle_dir, "info.json"))
    if not isinstance(info, dict):
        info = {}

    if config_name:
        cfg = _load_sjson(
            os.path.join(vehicle_dir, f"info_{config_name}.json"))
        if isinstance(cfg, dict):
            for k in ("defaultPaintName1", "defaultPaintName2",
                      "defaultPaintName3", "defaultMultiPaintSetup"):
                if cfg.get(k):
                    info[k] = cfg[k]

    # vehicles/common/paintLibraries/common.paintLibrary.json
    paint_library = {}
    common_lib = os.path.join(os.path.dirname(vehicle_dir.rstrip("/\\")),
                              "common", "paintLibraries",
                              "common.paintLibrary.json")
    lib = _load_sjson(common_lib)
    if isinstance(lib, dict):
        paint_library = lib.get("paints") or {}

    paints = resolve_paints(pc_paints, info, paint_library)

    global _applying_paints
    _applying_paints = True
    try:
        vp = scene.bng_vehicle_paints
        _ensure_slots(vp)
        for i, (color, met, rough, cc, ccr, name) in enumerate(paints[:3]):
            slot = vp.slots[i]
            slot.name = name
            slot.color = _paint_color_from_base(color)
            slot.metallic = met
            slot.roughness = rough
            slot.clearcoat = cc
            slot.clearcoat_roughness = ccr
    finally:
        _applying_paints = False

    if paints:
        from ..nodes.beamng_material_graph import update_paint_uniforms
        update_paint_uniforms()

    return paints


# Registration lives in the addon __init__ (classes + Scene.bng_vehicle_paints).
classes = (BeamNGVehiclePaintSlot, BeamNGVehiclePaints)
