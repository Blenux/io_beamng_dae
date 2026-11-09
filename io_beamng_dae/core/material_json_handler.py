"""BeamNG materials.json ↔ ShaderNodeBeamngMaterial conversion.

Ports of:
- fork cdae_material_json.cc parse_stage()/parse (dict → node props)
- fork beamng_material_importer.cc populate + auto-discovery fallback
- fork cdae_material_export.cc storage_to_bng_material() (node → dict)

Key mapping is driven by the vendored beamng_materials.maps tables
(STAGE_IMPORT_MAP / MATERIAL_IMPORT_MAP) so every documented key — including
detail PBR maps, retroreflectivity, animation, subsurface and the legacy
specular override set — maps to a typed node prop. Keys the node does not
model are preserved in extra_json for lossless round-trip.
"""

import os
import json

import bpy

from .. import backend
from .materials_library import (
    resolve_and_load_texture,
    resolve_texture_path,
)
from ..vendor.beamng_materials import maps as _bng_maps


# ---------------------------------------------------------------------------
# Key mapping (driven by vendored beamng_materials.maps)
# ---------------------------------------------------------------------------

# Library stage attr → node prop where the names differ.
_STAGE_PROP_RENAME = {
    "detail_base_color_map_strength": "detail_strength",
    "normal_detail_map_uv": "detail_normal_map_uv",
}

# Attrs holding RGBA colors.
_STAGE_COLOR_ATTRS = {
    "emissive_map_color", "retroreflective_color", "specular_color",
}

# Attrs holding 2-float vectors.
_STAGE_VEC2_ATTRS = {"detail_scale", "scroll_dir", "wave_dir"}

# Attrs holding bools — mirror stage.py STAGE_BOOL_ATTRIBUTES.
_STAGE_BOOL_ATTRS = {
    "emissive", "glow", "instance_diffuse", "instance_emissive",
    "instance_opacity", "palette_base_color", "palette_clear_coat",
    "palette_clear_coat_roughness", "palette_metallic", "palette_roughness",
    "pixel_specular", "stage_double_sided", "subsurface",
    "use_anisotropic", "use_anisotropic_filter", "vert_color",
    "vert_color_emissive",
}

# Attrs written as raw strings (paths + wave_type).
_STAGE_STR_ATTRS = {"wave_type"}

# Attrs needing custom handling — everything else is float/int.
_STAGE_SPECIAL = {"emissive_factor", "color_map_color", "base_color_factor",
                  "emissive_map_color"}

# Path attrs (value or *_img filepath fallback written on export).
_STAGE_PATH_ATTRS = {
    "color_map", "normal_map", "metallic_map", "roughness_map", "ao_map",
    "specular_map", "color_palette_map", "opacity_map", "emissive_map",
    "clear_coat_map", "clear_coat_roughness_map", "detail_map",
    "detail_normal_map", "clear_coat_bottom_normal_map", "overlay_map",
    "reflectivity_map",
    "detail_ao_map", "detail_metallic_map", "detail_roughness_map",
    "detail_opacity_map", "opacity_detail_map", "roughness_detail_map",
}

# Material attr → node prop.
_MAT_PROP_MAP = {
    "name": "bng_name", "map_to": "bng_map_to",
    "material_tag0": "bng_material_tag0",
    "material_tag1": "bng_material_tag1",
    "material_tag2": "bng_material_tag2",
    "material_tag3": "bng_material_tag3",
    "ground_type_game": "bng_ground_type",
    "groundmodel_name": "bng_groundmodel_name",
    "annotation": "bng_annotation",
    "annotation_map": "bng_annotation_map",
    "cubemap": "bng_cubemap",
    "double_sided": "bng_double_sided",
    "alpha_test": "bng_alpha_test",
    "translucent": "bng_translucent",
    "dynamic_cubemap": "bng_dynamic_cubemap",
    "z_write": "bng_z_write",
    "recv_shadows": "bng_recv_shadows",
    "cast_shadows": "bng_cast_shadows",
    "invert_back_face_normals": "bng_invert_backface_normals",
    "sub_surface": "bng_sub_surface",
    "sub_surface_intensity": "bng_sub_surface_intensity",
    "specular_color0": "bng_specular_color0",
    "specular_color1": "bng_specular_color1",
    "specular_color2": "bng_specular_color2",
    "specular_strength0": "bng_specular_strength0",
    "glow": "bng_glow",
    "class_name": "bng_class_name",
    "persistent_id": "bng_persistent_id",
    "order_simset": "bng_order_simset",
    "internal_name": "bng_internal_name",
    "cube_face": "bng_cube_face",
    "diffuse_color_slot": "bng_diffuse_color_slot",
    "detail_map_scale0": "bng_detail_map_scale0",
}

# Material attrs handled with bespoke logic (not generic setattr).
_MAT_SPECIAL = {"version", "active_layers", "translucent_blend_op",
                "alpha_ref", "normal_map_space"}

_MAT_BOOL_ATTRS = {
    "double_sided", "alpha_test", "translucent", "dynamic_cubemap",
    "z_write", "recv_shadows", "cast_shadows",
    "invert_back_face_normals", "sub_surface", "glow",
}
_MAT_COLOR_ATTRS = {"specular_color0", "specular_color1", "specular_color2"}
_MAT_INT_ATTRS = {"order_simset"}

_BLEND_OP_TO_ENUM = {
    "None": "NONE",
    "PreMulAlpha": "PREMUL_ALPHA",
    "Add": "ADD",
    "AddAlpha": "ADD_ALPHA",
    "LerpAlpha": "LERP_ALPHA",
    "Mul": "MUL",
    "Sub": "SUB",
}
_ENUM_TO_BLEND_OP = {v: k for k, v in _BLEND_OP_TO_ENUM.items()}

# Fork auto-discovery suffixes (beamng_material_importer.cc).
_AUTO_DISCOVER = (
    ("_b.color.png", "color_map_img", "color_map"),
    ("_n.normal.png", "normal_map_img", "normal_map"),
    ("_m.data.png", "metallic_map_img", "metallic_map"),
    ("_r.data.png", "roughness_map_img", "roughness_map"),
    ("_ao.data.png", "ao_map_img", "ao_map"),
    ("_c.data.png", "opacity_map_img", "opacity_map"),
    ("_g.color.png", "emissive_map_img", "emissive_map"),
    ("_cc.data.png", "clear_coat_map_img", "clear_coat_map"),
    ("_p.color.png", "color_palette_map_img", "color_palette_map"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_bng_node(material):
    """Return the material's ShaderNodeBeamngMaterial node or None."""
    if not material.use_nodes or material.node_tree is None:
        return None
    for n in material.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeBeamngMaterial':
            return n
    return None


def _bng_shader_output(bng):
    """The node's 'Shader' output socket.

    ShaderNodeCustomGroup socket declaration is deferred in Blender 5.x —
    freshly created nodes expose no outputs until the next update pass.
    Re-assigning node_tree forces a synchronous socket rebuild; fall back
    to the first output when the name still isn't there.
    """
    sock = bng.outputs.get('Shader')
    if sock is None and bng.node_tree is not None:
        try:
            bng.node_tree = bng.node_tree
        except Exception:
            pass
        sock = bng.outputs.get('Shader')
    if sock is None and len(bng.outputs):
        sock = bng.outputs[0]
    return sock


def _ensure_bng_node(material):
    """Find or create the BeamNG Material node + Material Output link.
    Mirrors the fork importer: the node tree is rebuilt around the BeamNG
    node — any existing BSDF/other nodes are removed so the BeamNG node is
    the sole surface source."""
    material.use_nodes = True
    ntree = material.node_tree
    bng = _find_bng_node(material)

    # Remove everything that isn't our BeamNG node (default Principled BSDF,
    # leftover outputs, ...). The fork clears the tree on import.
    for n in list(ntree.nodes):
        if n is not bng:
            ntree.nodes.remove(n)
    if bng is None:
        bng = ntree.nodes.new('ShaderNodeBeamngMaterial')
        bng.location = (0.0, 300.0)

    output = ntree.nodes.new('ShaderNodeOutputMaterial')
    output.location = (380.0, 300.0)
    output.is_active_output = True
    sock = _bng_shader_output(bng)
    if sock is not None:
        ntree.links.new(sock, output.inputs['Surface'])
    return bng


def _is_null_stage(d):
    """A stage dict with no keys is a null stage (JSON {} or null)."""
    return not d or not isinstance(d, dict)


def _coerce_bool(v):
    """Loose bool coercion — the game writes "None"/"false" strings in places."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "none", "no", "off")
    return bool(v)


def _as_rgba(v):
    """List-like → 4-float tuple, or None."""
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        c = list(v[:4]) + [1.0] * (4 - len(v[:4]))
        try:
            return tuple(float(x) for x in c)
        except (TypeError, ValueError):
            return None
    return None


def _as_vec2(v):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            return (float(v[0]), float(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_images(stage, base_dir, vfs_dir=""):
    """Resolve + load stage textures into *_img props."""
    for attr in _STAGE_PATH_ATTRS:
        path = getattr(stage, attr, "")
        if not path:
            continue
        img = resolve_and_load_texture(path, base_dir, vfs_dir)
        if img is not None:
            setattr(stage, attr + "_img", img)


# ---------------------------------------------------------------------------
# Import: dict → node
# ---------------------------------------------------------------------------

def _apply_stage_key(stage, json_key, val, dae_diffuse, extra):
    """Apply one JSON stage key to the stage props via STAGE_IMPORT_MAP.
    Unmapped keys land in `extra`."""
    attr = _bng_maps.STAGE_IMPORT_MAP.get(json_key)
    if attr is None:
        extra[json_key] = val
        return
    prop = _STAGE_PROP_RENAME.get(attr, attr)

    if attr == "emissive_factor":
        # Scalar or [r,g,b] array.
        if isinstance(val, (int, float)):
            stage.emissive_factor = float(val)
            stage.emissive_map_color = (
                float(val), float(val), float(val), 1.0)
        else:
            c = _as_rgba(val)
            if c is not None:
                stage.emissive_factor = c[0]
                stage.emissive_map_color = c
        return

    if attr in ("color_map_color", "base_color_factor"):
        # diffuseColor / baseColorFactor / baseColor [r,g,b,a] → solid
        # fallback; alpha folds into layer opacity (v1 shader has no
        # baseColor.a — this is what makes "invis" materials invisible).
        c = _as_rgba(val)
        if c is not None:
            stage.color_map_color = c
            stage.opacity_factor *= c[3]
        return

    if attr == "emissive_map_color":
        c = _as_rgba(val)
        if c is not None:
            stage.emissive_map_color = c
        return

    if not hasattr(stage, prop):
        extra[json_key] = val
        return

    if attr in _STAGE_COLOR_ATTRS:
        c = _as_rgba(val)
        if c is not None:
            setattr(stage, prop, c)
    elif attr in _STAGE_VEC2_ATTRS:
        v = _as_vec2(val)
        if v is not None:
            setattr(stage, prop, v)
    elif attr in _STAGE_BOOL_ATTRS:
        setattr(stage, prop, _coerce_bool(val))
    elif attr.endswith("_uv") or attr in ("anim_flags",):
        setattr(stage, prop, int(_num(val, 0)))
    elif attr in _STAGE_PATH_ATTRS or attr in _STAGE_STR_ATTRS:
        if val:
            setattr(stage, prop, str(val))
    else:
        setattr(stage, prop, _num(val))


def _populate_stage(stage, sd, base_dir, dae_diffuse=None, vfs_dir=""):
    """parse_stage() + stage populate — dict keys → stage props.

    *UseUV keys apply in a second pass so they override the direct *UV
    index only when non-zero, matching fork beamng_material_importer.cc.
    """
    extra = {}
    useuv = []
    for json_key, val in sd.items():
        if json_key.endswith("UseUV"):
            useuv.append((json_key, val))
        else:
            _apply_stage_key(stage, json_key, val, dae_diffuse, extra)

    for json_key, val in useuv:
        attr = _bng_maps.STAGE_IMPORT_MAP.get(json_key)
        if attr is None:
            extra[json_key] = val
            continue
        prop = _STAGE_PROP_RENAME.get(attr, attr)
        if not hasattr(stage, prop):
            extra[json_key] = val
            continue
        iv = int(_num(val, 0))
        if iv != 0:
            setattr(stage, prop, iv)

    # DAE <diffuse> fallback when JSON carried no color key.
    if dae_diffuse is not None and len(dae_diffuse) >= 4 \
            and float(dae_diffuse[3]) != 0.0 \
            and "diffuseColor" not in sd and "baseColorFactor" not in sd \
            and "baseColor" not in sd:
        stage.color_map_color = tuple(float(v) for v in dae_diffuse[:4])

    stage.extra_json = json.dumps(extra) if extra else ""


def _populate_node_from_dict(node, d, base_dir, dae_diffuse=None,
                             vfs_dir=""):
    """Populate node props from a parsed material dict, then load
    textures + rebuild + sync flags (fork's setup_beamng_material_node)."""
    from ..nodes.beamng_material_node import _sync_material_flags

    node.is_rebuilding = True
    try:
        # Fork defaults color_map_color to white for every stage — the
        # 0.8-gray prop default would darken every mapless material.
        for st in node.stages:
            st.color_map_color = (1.0, 1.0, 1.0, 1.0)

        extra = {}
        for json_key, val in d.items():
            if json_key == "Stages":
                continue
            attr = _bng_maps.MATERIAL_IMPORT_MAP.get(json_key)
            if attr is None:
                extra[json_key] = val
                continue
            if attr == "version":
                node.bng_version = "V1_0" if _num(val, 1.5) < 1.5 else "V1_5"
            elif attr == "active_layers":
                node.bng_active_layers = max(1, min(4, int(_num(val, 1))))
            elif attr == "alpha_ref":
                node.bng_alpha_ref = _num(val, 0.0)
            elif attr == "normal_map_space":
                node.bng_normal_map_space = (
                    "OPENGL" if int(_num(val, 0)) == 1 else "DIRECTX")
            elif attr == "translucent_blend_op":
                continue  # applied below once bng_translucent is known
            else:
                prop = _MAT_PROP_MAP.get(attr)
                if prop is None or not hasattr(node, prop):
                    extra[json_key] = val
                    continue
                if attr in _MAT_BOOL_ATTRS:
                    setattr(node, prop, _coerce_bool(val))
                elif attr in _MAT_COLOR_ATTRS:
                    c = _as_rgba(val)
                    if c is not None:
                        setattr(node, prop, c)
                elif attr in _MAT_INT_ATTRS:
                    setattr(node, prop, int(_num(val, 0)))
                elif isinstance(val, str):
                    setattr(node, prop, val)
                elif isinstance(val, (int, float)):
                    setattr(node, prop, val)
                else:
                    extra[json_key] = val

        # translucentBlendOp → enum (only when translucent, per fork).
        # Key absent = standard alpha blending (LerpAlpha) — only an
        # explicit "None" disables blending (fork beamng_material_importer).
        if node.bng_translucent:
            op = d.get("translucentBlendOp")
            node.bng_alpha_blend_mode = (
                _BLEND_OP_TO_ENUM.get(op, "NONE") if op else "LERP_ALPHA")
        else:
            node.bng_alpha_blend_mode = "NONE"

        # Stages.
        stages_json = d.get("Stages") or []
        for i in range(min(4, len(node.stages))):
            sd = stages_json[i] if i < len(stages_json) else None
            if _is_null_stage(sd):
                # Fork populates every stage — null stages still pick up
                # the DAE diffuse fallback color.
                if (dae_diffuse is not None and len(dae_diffuse) >= 4
                        and float(dae_diffuse[3]) != 0.0):
                    node.stages[i].color_map_color = tuple(
                        float(v) for v in dae_diffuse[:4])
            else:
                _populate_stage(node.stages[i], sd, base_dir, dae_diffuse,
                                vfs_dir)

        node.extra_json = json.dumps(extra) if extra else ""

        # Texture loading — keep is_rebuilding so image assignment does
        # not overwrite the BeamNG path strings or rebuild per texture.
        for i in range(min(4, len(node.stages))):
            sd = stages_json[i] if i < len(stages_json) else None
            if not _is_null_stage(sd):
                _load_images(node.stages[i], base_dir, vfs_dir)
    finally:
        node.is_rebuilding = False

    node._rebuild()
    _sync_material_flags(node)


def import_material_from_dict(mat_data, base_dir="", material=None,
                              dae_name=None, dae_diffuse=None, vfs_dir=""):
    """Create or update a Blender material from a parsed material dict.
    Returns the Material."""
    mat_data = dict(mat_data)
    mat_name = mat_data.get("name") or dae_name or "BeamNGMaterial"
    mat_data.setdefault("name", mat_name)
    mat = material
    if mat is None:
        mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True

    node = _ensure_bng_node(mat)
    _populate_node_from_dict(node, mat_data, base_dir, dae_diffuse, vfs_dir)
    return mat


def setup_fallback_material_node(mat, dae_mat_name, base_dir="",
                                 dae_diffuse=None, vfs_dir=""):
    """Fork's auto-discovery fallback — no JSON match: create the node and
    scan for <name>_b.color.png / _n.normal.png / ... in the vehicle dir."""
    from ..nodes.beamng_material_node import _sync_material_flags

    node = _ensure_bng_node(mat)
    node.is_rebuilding = True
    try:
        node.bng_name = dae_mat_name
        node.bng_map_to = dae_mat_name
        node.bng_version = "V1_5"
        node.bng_active_layers = 1
        node.bng_cast_shadows = True

        stage0 = node.stages[0]
        if dae_diffuse is not None:
            stage0.color_map_color = tuple(dae_diffuse[:4])
    finally:
        node.is_rebuilding = False

    from .bng_vfs import vfs_get_global
    vfs = vfs_get_global()
    base = os.path.join(base_dir, dae_mat_name) if base_dir else dae_mat_name
    for suffix, img_field, path_field in _AUTO_DISCOVER:
        img = None
        path = base + suffix
        # VFS vehicle dir: <name>_<suffix> lives inside mounted content.
        if vfs_dir and vfs.has_mounts():
            vpath = vfs_dir.rstrip("/") + "/" + dae_mat_name + suffix
            if vfs.file_exists(vpath):
                img = vfs.load_image(vpath)
                path = vpath
        if img is None:
            img = resolve_and_load_texture(path, base_dir, vfs_dir)
        if img is not None:
            setattr(stage0, img_field, img)
            setattr(stage0, path_field, path)

    node._rebuild()
    _sync_material_flags(node)
    return mat


# ---------------------------------------------------------------------------
# Export: node → dict (storage_to_bng_material + serialize semantics)
# ---------------------------------------------------------------------------

# Stage attr → canonical export key with its default (write when != default).
_STAGE_EXPORT_FLOATS = {
    "metallicFactor": ("metallic_factor", 0.0),
    "roughnessFactor": ("roughness_factor", 1.0),
    "clearCoatFactor": ("clear_coat_factor", 0.0),
    "opacityFactor": ("opacity_factor", 1.0),
    "normalMapStrength": ("normal_map_strength", 1.0),
    "clearCoatRoughnessFactor": ("clear_coat_roughness_factor", 0.0),
    "detailBaseColorMapStrength": ("detail_strength", 1.0),
    "detailNormalMapStrength": ("detail_normal_strength", 1.0),
    "clearCoatBottomNormalStrength": ("clear_coat_bottom_normal_strength", 1.0),
    "emissiveIntensityNits": ("emissive_intensity_nits", 0.0),
    "glowFactor": ("glow_factor", 0.0),
    "retroreflectivity": ("retroreflectivity", 0.0),
    "specularPower": ("specular_power", 0.0),
    "detailAOMapStrength": ("detail_ao_map_strength", 1.0),
    "detailMetallicMapStrength": ("detail_metallic_map_strength", 1.0),
    "detailRoughnessMapStrength": ("detail_roughness_map_strength", 1.0),
    "detailOpacityMapStrength": ("detail_opacity_map_strength", 1.0),
    "sequenceFramePerSec": ("sequence_frame_per_sec", 0.0),
    "sequenceSegmentSize": ("sequence_segment_size", 0.0),
    "waveAmp": ("wave_amp", 0.0),
    "waveFreq": ("wave_freq", 0.0),
    "animFlags": ("anim_flags", 0.0),
}

# Canonical export path keys — aliases stay in extra_json untouched.
_STAGE_EXPORT_PATH_KEYS = {
    "baseColorMap": "color_map",
    "normalMap": "normal_map",
    "metallicMap": "metallic_map",
    "roughnessMap": "roughness_map",
    "ambientOcclusionMap": "ao_map",
    "specularMap": "specular_map",
    "colorPaletteMap": "color_palette_map",
    "opacityMap": "opacity_map",
    "emissiveMap": "emissive_map",
    "clearCoatMap": "clear_coat_map",
    "clearCoatRoughnessMap": "clear_coat_roughness_map",
    "detailMap": "detail_map",
    "detailNormalMap": "detail_normal_map",
    "clearCoatBottomNormalMap": "clear_coat_bottom_normal_map",
    "overlayMap": "overlay_map",
    "reflectivityMap": "reflectivity_map",
    "detailAOMap": "detail_ao_map",
    "metallicDetailMap": "detail_metallic_map",
    "detailRoughnessMap": "detail_roughness_map",
    "detailOpacityMap": "detail_opacity_map",
    "opacityDetailMap": "opacity_detail_map",
    "roughnessDetailMap": "roughness_detail_map",
}

# UV keys written on export: each non-zero index emits the *UseUV alias
# plus the direct *UV key (fork emits both).
_UV_EXPORT_ALIASES = {
    "color_map_uv": ("colorMapUseUV", "colorMapUV", "diffuseMapUseUV",
                     "baseColorMapUseUV"),
    "normal_map_uv": ("normalMapUseUV", "normalMapUV"),
    "metallic_map_uv": ("metallicMapUseUV", "metallicMapUV"),
    "roughness_map_uv": ("roughnessMapUseUV", "roughnessMapUV"),
    "ao_map_uv": ("ambientOcclusionMapUseUV", "ambientOcclusionMapUV"),
    "opacity_map_uv": ("opacityMapUseUV", "opacityMapUV"),
    "emissive_map_uv": ("emissiveMapUseUV", "emissiveMapUV"),
    "clear_coat_map_uv": ("clearCoatMapUseUV", "clearCoatMapUV"),
    "clear_coat_roughness_map_uv": ("clearCoatRoughnessMapUseUV",
                                    "clearCoatRoughnessMapUV"),
    "color_palette_map_uv": ("colorPaletteMapUseUV", "colorPaletteMapUV"),
    "specular_map_uv": ("specularMapUseUV", "specularMapUV"),
    "detail_map_uv": ("detailMapUseUV", "detailMapUV"),
    "detail_normal_map_uv": ("detailNormalMapUseUV", "detailNormalMapUV",
                             "normalDetailMapUseUV"),
    "clear_coat_bottom_normal_map_uv": ("clearCoatBottomNormalMapUseUV",
                                        "clearCoatBottomNormalMapUV"),
    "overlay_map_uv": ("overlayMapUseUV", "overlayMapUV"),
    "reflectivity_map_uv": ("reflectivityMapUseUV", "reflectivityMapUV"),
    "detail_ao_map_uv": ("detailAOMapUseUV", "detailAOMapUV"),
    "detail_metallic_map_uv": ("metallicDetailMapUseUV",
                               "metallicDetailMapUV"),
    "detail_roughness_map_uv": ("detailRoughnessMapUseUV",
                                "detailRoughnessMapUV"),
    "detail_opacity_map_uv": ("detailOpacityMapUseUV", "detailOpacityMapUV"),
    "opacity_detail_map_uv": ("opacityDetailMapUseUV", "opacityDetailMapUV"),
    "roughness_detail_map_uv": ("roughnessDetailMapUseUV",
                                "roughnessDetailMapUV"),
}

_STAGE_EXPORT_BOOLS = {
    "pixelSpecular": "pixel_specular",
    "useAnisotropic": "use_anisotropic",
    "useAnisotropicFilter": "use_anisotropic_filter",
    "instanceDiffuse": "instance_diffuse",
    "instanceOpacity": "instance_opacity",
    "instanceEmissive": "instance_emissive",
    "vertColor": "vert_color",
    "vertColorEmissive": "vert_color_emissive",
    "glow": "glow",
    "emissive": "emissive",
    "subsurface": "subsurface",
    "doubleSided": "stage_double_sided",
}

_STAGE_EXPORT_PALETTE = {
    "paletteBaseColor": "palette_base_color",
    "paletteMetallic": "palette_metallic",
    "paletteRoughness": "palette_roughness",
    "paletteClearCoat": "palette_clear_coat",
    "paletteClearCoatRoughness": "palette_clear_coat_roughness",
}


def _stage_to_dict(stage):
    """Fork serialize_stage() as a dict — only non-default values written."""
    d = {}
    # Restore extras first so edited values can override them below.
    if stage.extra_json:
        try:
            d.update(json.loads(stage.extra_json))
        except (TypeError, ValueError):
            pass

    for json_key, field in _STAGE_EXPORT_PATH_KEYS.items():
        val = getattr(stage, field) or _img_filepath(
            getattr(stage, field + "_img", None))
        if val:
            d[json_key] = val

    for json_key, (field, default) in _STAGE_EXPORT_FLOATS.items():
        val = getattr(stage, field)
        if val != default:
            d[json_key] = val

    if stage.detail_scale[0] != 1.0 or stage.detail_scale[1] != 1.0:
        d["detailScale"] = [stage.detail_scale[0], stage.detail_scale[1]]

    # emissiveFactor — array when emissive_map_color channels differ.
    if stage.emissive_factor != 0.0:
        c = stage.emissive_map_color
        if c[0] != c[1] or c[1] != c[2]:
            d["emissiveFactor"] = [c[0], c[1], c[2]]
        else:
            d["emissiveFactor"] = stage.emissive_factor
    else:
        c = stage.emissive_map_color
        if tuple(c[:3]) != (0.0, 0.0, 0.0):
            d["emissiveColor"] = [c[0], c[1], c[2], c[3]]

    for json_key, field in _STAGE_EXPORT_BOOLS.items():
        if getattr(stage, field):
            d[json_key] = True

    for json_key, field in _STAGE_EXPORT_PALETTE.items():
        if not getattr(stage, field):
            d[json_key] = False

    for field, keys in _UV_EXPORT_ALIASES.items():
        val = getattr(stage, field)
        if val != 0:
            for k in keys:
                d[k] = val

    # Retroreflective + wave/scroll vectors.
    if stage.retroreflective_color != (1.0, 1.0, 1.0, 1.0):
        c = stage.retroreflective_color
        d["retroreflectiveColor"] = [c[0], c[1], c[2], c[3]]
    if stage.specular_color != (0.5, 0.5, 0.5, 1.0):
        c = stage.specular_color
        d["specular"] = [c[0], c[1], c[2], c[3]]
    if stage.scroll_dir[0] != 0.0 or stage.scroll_dir[1] != 0.0:
        d["scrollDir"] = [stage.scroll_dir[0], stage.scroll_dir[1]]
    if stage.wave_type:
        d["waveType"] = stage.wave_type

    # diffuseColor when no color map and color is non-default (0.8 gray).
    # Fork checks the resolved path including the image-filepath fallback.
    if not (stage.color_map or _img_filepath(stage.color_map_img)):
        c = stage.color_map_color
        if tuple(c) != (0.8, 0.8, 0.8, 1.0) and tuple(c) != (1.0,) * 4:
            d["diffuseColor"] = [c[0], c[1], c[2], c[3]]

    return d


def _img_filepath(img):
    return img.filepath if img is not None and img.filepath else ""


def export_material_to_dict(material, vehicle_name=""):
    """storage_to_bng_material + serialize → dict for one material.
    Returns None when the material has no BeamNG node."""
    node = _find_bng_node(material)
    if node is None:
        return None

    mat_name = node.bng_name or material.name
    d = {}

    # Restore extras first so edited values can override them below.
    if node.extra_json:
        try:
            d.update(json.loads(node.extra_json))
        except (TypeError, ValueError):
            pass

    d["Stages"] = [_stage_to_dict(node.stages[i]) for i in range(4)]
    d["activeLayers"] = node.bng_active_layers
    d["class"] = "Material"
    d["name"] = mat_name
    d["mapTo"] = node.bng_map_to or mat_name
    d["version"] = 1.0 if node.bng_version == "V1_0" else 1.5

    if node.bng_dynamic_cubemap:
        d["dynamicCubemap"] = True
    if node.bng_alpha_test:
        d["alphaTest"] = True
    # Fork derives translucent from alpha_blend_mode > 0.
    if node.bng_alpha_blend_mode != "NONE":
        d["translucent"] = True
        d["translucentBlendOp"] = _ENUM_TO_BLEND_OP[node.bng_alpha_blend_mode]
    elif node.bng_translucent:
        d["translucent"] = True
    if node.bng_double_sided:
        d["doubleSided"] = True
    if not node.bng_cast_shadows:
        d["castShadows"] = False
    if node.bng_alpha_ref != 0.0:
        d["alphaRef"] = node.bng_alpha_ref
    if node.bng_normal_map_space == "OPENGL":
        d["normalMapSpace"] = 1
    if node.bng_invert_backface_normals:
        d["invertBackFaceNormals"] = True  # game's exact key (capital F)
    if node.bng_z_write:
        d["translucentZWrite"] = True
    if node.bng_recv_shadows:
        d["translucentRecvShadows"] = True
    if node.bng_sub_surface:
        d["subSurface"] = True
    if node.bng_sub_surface_intensity != 0.0:
        d["subSurfaceIntensity"] = node.bng_sub_surface_intensity
    if node.bng_glow:
        d["glow"] = True
    if node.bng_specular_strength0 != 0.0:
        d["specularStrength0"] = node.bng_specular_strength0
    for attr, key in (("bng_specular_color0", "specularColor0"),
                      ("bng_specular_color1", "specularColor1"),
                      ("bng_specular_color2", "specularColor2")):
        c = getattr(node, attr)
        if tuple(c) != (0.5, 0.5, 0.5, 1.0):
            d[key] = [c[0], c[1], c[2], c[3]]

    # String/int metadata via the same attr→key tables.
    for attr, json_key in (("bng_material_tag0", "materialTag0"),
                           ("bng_material_tag1", "materialTag1"),
                           ("bng_material_tag2", "materialTag2"),
                           ("bng_material_tag3", "materialTag3"),
                           ("bng_ground_type", "groundType"),
                           ("bng_groundmodel_name", "groundmodelName"),
                           ("bng_annotation", "annotation"),
                           ("bng_annotation_map", "annotationMap"),
                           ("bng_cubemap", "cubemap"),
                           ("bng_persistent_id", "persistentId"),
                           ("bng_internal_name", "internalName"),
                           ("bng_cube_face", "cubeFace"),
                           ("bng_diffuse_color_slot", "beamngDiffuseColorSlot"),
                           ("bng_detail_map_scale0", "detailMapScale0")):
        val = getattr(node, attr)
        if val:
            d[json_key] = val
    if node.bng_order_simset != 0:
        d["order_simset"] = node.bng_order_simset
    if node.bng_class_name and node.bng_class_name != "Material":
        d["class"] = node.bng_class_name

    return d


# ---------------------------------------------------------------------------
# File-level import / export (standalone operators)
# ---------------------------------------------------------------------------

def import_materials_from_json(filepath, base_dir=""):
    """Standalone import: create one Blender material per entry."""
    data = backend.parse_materials_json(filepath)
    if not isinstance(data, dict):
        return []
    if not base_dir:
        base_dir = os.path.dirname(filepath)
    return [
        import_material_from_dict(d, base_dir)
        for d in data.values()
        if isinstance(d, dict)
    ]


def export_materials_to_json(materials, filepath, vehicle_name=""):
    """collect_beamng_materials + write_materials_json."""
    out = {}
    for mat in materials:
        d = export_material_to_dict(mat, vehicle_name)
        if d is not None:
            out[d["name"]] = d
    if not out:
        return False
    return backend.write_materials_json(filepath, out)
