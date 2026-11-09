"""GlowMap support — JBeam glowMap entries → object tags + material switching.

Port of the fork's glowMap runtime (beamng_glowmap.cc / beamng_glowmap.py)
at addon scope: imported objects carry a "bng_glowmap" IDProperty with the
switch entries, and a state property drives which of the off/on/on_intense
materials is assigned to the glow slot.

Entry format (matches the fork): "slot|dae|off|on|on_intense|func".
State enum: 0=AUTO (electrics trigger, defaults off), 1=OFF, 2=ON,
3=INTENSE.
"""

import bpy
import os


_ENTRY_SEP = ";"
_FIELD_SEP = "|"

# AUTO evaluates the entry's func name against scene["bng_electrics"];
# entries without a func stay off — matches the game's initial state.
_STATE_NAMES = {1: "off", 2: "on", 3: "on_intense"}


def collect_glowmaps(vehicle_dir):
    """Scan *.jbeam under vehicle_dir; return {org_mat: GlowMapEntry}."""
    from ..vendor.beamng_materials.metadata import load_jbeam_metadata

    out = {}
    if not vehicle_dir or not os.path.isdir(vehicle_dir):
        return out
    for dirpath, _dirs, files in os.walk(vehicle_dir):
        for fname in files:
            if not fname.endswith(".jbeam"):
                continue
            try:
                meta = load_jbeam_metadata(os.path.join(dirpath, fname))
            except Exception:
                continue
            out.update(meta.glow_maps)
    return out


def glow_map_string(vehicle_dir):
    """Build the 'org=off;org=off;...' remap string MaterialLibrary expects —
    flexbody material names (covet_headlightglass) → off-state material
    (covet_glass). Used to auto-fill the import glow_map when unset."""
    pairs = []
    for name, e in collect_glowmaps(vehicle_dir).items():
        if e.off:
            pairs.append(f"{name}={e.off}")
    return ";".join(pairs)


def _func_of(entry):
    f = getattr(entry, "simple_function", None)
    return f if isinstance(f, str) else ""


def tag_objects(objects, vehicle_dir):
    """Attach bng_glowmap/bng_glowmap_state to objects whose assigned
    materials appear in the vehicle's JBeam glowMap. Returns count."""
    glow_maps = collect_glowmaps(vehicle_dir)
    if not glow_maps:
        return 0
    tagged = 0
    for ob in objects.values():
        if ob is None or ob.type != 'MESH':
            continue
        entries = []
        for i, slot in enumerate(ob.material_slots):
            mat = slot.material
            if mat is None:
                continue
            name = _bng_name(mat) or mat.name
            e = glow_maps.get(name)
            if e is None:
                continue
            entries.append(_FIELD_SEP.join([
                str(i), name, e.off or "", e.on or "",
                e.on_intense or "", _func_of(e)]))
        if entries:
            ob["bng_glowmap"] = _ENTRY_SEP.join(entries)
            ob["bng_glowmap_state"] = 0
            tagged += 1
    return tagged


def _bng_name(mat):
    if not mat.use_nodes or mat.node_tree is None:
        return ""
    for n in mat.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeBeamngMaterial':
            return n.bng_name or mat.name
    return ""


def decode_entries(ob):
    raw = ob.get("bng_glowmap")
    if not raw:
        return []
    out = []
    for part in raw.split(_ENTRY_SEP):
        f = part.split(_FIELD_SEP)
        if len(f) < 2:
            continue
        out.append({
            "slot": int(_num(f[0])),
            "dae": f[1],
            "off": f[2] if len(f) > 2 else "",
            "on": f[3] if len(f) > 3 else "",
            "on_intense": f[4] if len(f) > 4 else "",
            "func": f[5] if len(f) > 5 else "",
        })
    return out


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _electrics(scene):
    g = scene.get("bng_electrics")
    return g if isinstance(g, dict) else {}


def _target_name(e, state, electrics):
    if state == 0:  # AUTO — evaluate func, default off
        if e["func"] and float(electrics.get(e["func"], 0.0) or 0.0) > 0.0:
            return e["on"] or e["off"]
        return e["off"]
    return e.get(_STATE_NAMES.get(state, "off")) or e["off"]


def _material_for_state(name, scene):
    """Resolve the state material — reuse datablock or create it from the
    merged vehicle library (scene context stored at import)."""
    if not name:
        return None
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    from .materials_library import MaterialLibrary
    vdir = scene.get("bng_vehicle_dir", "")
    if not vdir:
        return None
    lib = MaterialLibrary(vdir, vfs_dir=scene.get("bng_vfs_dir", ""))
    lib.load()
    return lib.import_material(name, vdir)


def apply_glow_state(ob, state=None, scene=None):
    """Swap the glow slot's material to the state's off/on/on_intense."""
    if state is None:
        state = int(ob.get("bng_glowmap_state", 0))
    if scene is None:
        scene = bpy.context.scene
    electrics = _electrics(scene)
    for e in decode_entries(ob):
        idx = e["slot"]
        if not (0 <= idx < len(ob.material_slots)):
            continue
        name = _target_name(e, state, electrics)
        mat = _material_for_state(name, scene)
        if mat is not None:
            ob.material_slots[idx].material = mat


def apply_all(scene):
    """Re-apply every glowmap object's state (state, electrics or library
    change)."""
    for ob in scene.objects:
        if ob.get("bng_glowmap") is not None:
            apply_glow_state(ob, scene=scene)


def glowmap_objects(scene):
    return sorted((ob for ob in scene.objects
                   if ob.get("bng_glowmap") is not None),
                  key=lambda o: o.name)
