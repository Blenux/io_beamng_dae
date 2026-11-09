"""BeamNG material library — discovery, merge, resolution, texture loading.

Python port of the fork's BeamNGMaterialImporter
(source/blender/io/cdae/importer/beamng_material_importer.cc) plus the
texture helpers from cdae_texture_resolve.cc.

Load order (first wins):
    main.materials.json → skin.materials.json → dynamicDecals/main.materials.json
    → all *.materials.json in vehicle subdirs → vehicles/common/ (recursive)

resolve_material() order:
    glowMap remap → skin variants → mapTo reverse → direct key → normalized
    (strip "-material" suffix, case-insensitive)

No JSON match → auto-discovery fallback: scan vehicle dir for
<name>_b.color.png / _n.normal.png / _m.data.png / ... suffixes.

Parsed materials are cached per vehicle_dir (fork's s_cached_json_materials).
"""

import os

import bpy

from .. import backend
from .bng_vfs import vfs_get_global


# ---------------------------------------------------------------------------
# Texture helpers — port of cdae_texture_resolve.cc
# ---------------------------------------------------------------------------

_SRGB_SUFFIXES = (
    "_b.color", "_d.color", "_s.color", "_g.color", "_p.color",
    "_c.color", "_r.color", "_d.dds", "_c.dds",
)


def is_srgb_texture(path):
    """Fork's is_srgb_texture(): sRGB for color maps, Non-Color for data
    (_n.normal, _m.data, _r.data, _ao.data, _cc.data, _c.data, _o.data)."""
    return any(s in path for s in _SRGB_SUFFIXES)


def _try_ext_fallback(path):
    """Prefer .dds over a .png reference; fall back .dds → .png.
    Game zips/extracted dirs use uppercase .DDS — try all case variants."""
    if path.lower().endswith(".png"):
        base = path[:-4]
        for ext in (".dds", ".DDS", ".Dds"):
            if os.path.isfile(base + ext):
                return base + ext
    if os.path.isfile(path):
        return path
    if path[-4:].lower() == ".dds":
        base = path[:-4]
        for ext in (".png", ".PNG", ".DDS", ".Dds"):
            if os.path.isfile(base + ext):
                return base + ext
    return ""


def resolve_texture_path(beamng_path, vehicle_dir):
    """Port of resolve_texture_path() — four strategies in order."""
    if not beamng_path:
        return ""

    basename = os.path.basename(beamng_path.replace("\\", "/"))

    # 1. vehicle_dir + basename
    if basename and vehicle_dir:
        candidate = _try_ext_fallback(
            os.path.join(vehicle_dir, basename))
        if candidate:
            return candidate

    # 2. strip "/vehicles/" (or "vehicles/") and join with vehicle_dir parent
    rel = ""
    norm = beamng_path.replace("\\", "/")
    if norm.startswith("/vehicles/"):
        rel = norm[10:]
    elif norm.startswith("vehicles/"):
        rel = norm[9:]
    else:
        vpos = norm.find("/vehicles/")
        if vpos >= 0:
            rel = norm[vpos + 10:]
    if rel:
        vehicles_dir = os.path.dirname(vehicle_dir.rstrip("/\\"))
        if vehicles_dir:
            candidate = _try_ext_fallback(
                os.path.join(vehicles_dir, *rel.split("/")))
            if candidate:
                return candidate

    # 3. beamng_path as an absolute filesystem path
    candidate = _try_ext_fallback(beamng_path)
    if candidate:
        return candidate

    # 4. vehicle_dir + beamng_path (relative path)
    if vehicle_dir:
        candidate = _try_ext_fallback(
            os.path.join(vehicle_dir, beamng_path))
        if candidate:
            return candidate

    return ""


def resolve_and_load_texture(beamng_path, vehicle_dir, vfs_dir=""):
    """resolve_and_load_texture_vfs(): VFS first (game zips/mods), disk
    fallback — fork order: VFS direct → VFS vehicle_dir+basename →
    VFS strip /vehicles/ → disk resolve_texture_path."""
    if not beamng_path:
        return None
    vfs = vfs_get_global()
    if vfs.has_mounts():
        norm = beamng_path.replace("\\", "/")
        if vfs.file_exists(norm):
            return vfs.load_image(norm)
        basename = os.path.basename(norm)
        for d in (vfs_dir, vehicle_dir):
            if d and d.startswith("/"):
                cand = d.rstrip("/") + "/" + basename
                if vfs.file_exists(cand):
                    return vfs.load_image(cand)
        rel = ""
        if norm.startswith("/vehicles/"):
            rel = norm[10:]
        elif norm.startswith("vehicles/"):
            rel = norm[9:]
        elif "/vehicles/" in norm:
            rel = norm[norm.find("/vehicles/") + 10:]
        if rel:
            parent = os.path.dirname(
                (vfs_dir or vehicle_dir).rstrip("/\\"))
            if parent.startswith("/"):
                cand = parent + "/" + rel
                if vfs.file_exists(cand):
                    return vfs.load_image(cand)

    resolved = resolve_texture_path(beamng_path, vehicle_dir)
    if not resolved:
        return None
    try:
        img = bpy.data.images.load(resolved, check_existing=True)
    except RuntimeError:
        return None
    try:
        img.colorspace_settings.name = (
            'sRGB' if is_srgb_texture(resolved) else 'Non-Color')
    except TypeError:
        pass
    return img


# ---------------------------------------------------------------------------
# *.materials.json discovery + merge
# ---------------------------------------------------------------------------

_MAT_JSON_SUFFIX = ".materials.json"


def find_materials_json_files(root_dir):
    """Recursively find all *.materials.json under root_dir."""
    out = []
    for dirpath, _dirs, files in os.walk(root_dir):
        for fname in files:
            if fname.endswith(_MAT_JSON_SUFFIX):
                out.append(os.path.join(dirpath, fname))
    return out


# Per-vehicle-dir parse cache (fork's s_cached_json_materials).
_cache_vehicle_dir = None
_cache_materials = {}


def clear_cache():
    global _cache_vehicle_dir, _cache_materials
    _cache_vehicle_dir = None
    _cache_materials = {}


class MaterialLibrary:
    """Merged materials for one vehicle directory.

    Fork: BeamNGMaterialImporter::load_material_jsons() + build_lookup_maps()
    + resolve_material() + import_material().
    """

    def __init__(self, vehicle_dir, vfs_dir=""):
        self.vehicle_dir = vehicle_dir
        # VFS vehicle root ("/vehicles/vivace") when the vehicle's content
        # resolves through mounts — bare DAEs next to nothing still get
        # game materials/textures (fork: spawner-mounted VFS paths).
        self.vfs_dir = vfs_dir
        self.materials = {}          # name → material dict
        self.mapto_to_key = {}       # mapTo → material key
        self.normalized_to_key = {}  # normalized name → key
        # Fork MaterialImportParams.
        self.skin_slots = ""
        self.glow_map = ""

    # -- loading --

    def _load_file(self, path):
        return backend.parse_materials_json(path) if os.path.isfile(path) \
            else {}

    def _load_vfs(self, vfs_path):
        """Parse a .materials.json through the VFS (zip/dir mounts)."""
        vfs = vfs_get_global()
        text = vfs.read_file_text(vfs_path) if vfs.has_mounts() else ""
        if not text:
            return {}
        from ..vendor.beamng_materials import _sjson
        try:
            return _sjson.parse(text) or {}
        except Exception:
            return {}

    def _load_source(self, rel):
        """Load a .materials.json by vehicle-relative path from both the
        VFS vehicle dir and the disk vehicle dir — disk wins (the user's
        extracted working copy overrides mounted game content)."""
        mats = {}
        if self.vfs_dir:
            mats.update(self._load_vfs(self.vfs_dir + "/" + rel))
        mats.update(self._load_file(
            os.path.join(self.vehicle_dir, *rel.split("/"))))
        return mats

    def _find_vfs_jsons(self, vfs_dir):
        """find_materials_json_files_vfs() — recursive *.materials.json
        listing under a VFS dir across all mounts."""
        vfs = vfs_get_global()
        out = []
        if not vfs.has_mounts():
            return out
        stack = [vfs_dir]
        while stack:
            d = stack.pop()
            for name, path, is_dir in vfs.list_directory(d):
                if is_dir:
                    stack.append(path)
                elif name.endswith(_MAT_JSON_SUFFIX):
                    out.append(path)
        return out

    def load(self):
        """Merge all *.materials.json for the vehicle; use the per-dir
        cache to avoid re-parsing per spawn."""
        global _cache_vehicle_dir, _cache_materials
        cache_key = (self.vehicle_dir, self.vfs_dir)
        if _cache_vehicle_dir == cache_key and _cache_materials:
            self.materials = dict(_cache_materials)
            self.build_lookup_maps()
            return

        vdir = self.vehicle_dir.rstrip("/\\")
        mats = self._load_source("main.materials.json")
        mats.update(self._load_source("skin.materials.json"))
        mats.update(self._load_source("dynamicDecals/main.materials.json"))

        # Vehicle subdirs — lower priority (first wins); disk before VFS
        # so the extracted working copy shadows mounted content.
        skip = {os.path.join(vdir, "main.materials.json"),
                os.path.join(vdir, "skin.materials.json"),
                os.path.join(vdir, "dynamicDecals", "main.materials.json")}
        for path in find_materials_json_files(vdir):
            if path in skip:
                continue
            for k, v in self._load_file(path).items():
                mats.setdefault(k, v)
        if self.vfs_dir:
            for path in self._find_vfs_jsons(self.vfs_dir):
                base = os.path.basename(path)
                if base in ("main.materials.json", "skin.materials.json"):
                    rel = path[len(self.vfs_dir):].lstrip("/")
                    if "/" not in rel:
                        continue  # top-level mains already loaded
                for k, v in self._load_vfs(path).items():
                    mats.setdefault(k, v)

        # vehicles/common/ (main + recursive subdirs).
        vehicles_root = os.path.dirname(vdir)
        common_dir = os.path.join(vehicles_root, "common")
        if os.path.isdir(common_dir):
            for path in find_materials_json_files(common_dir):
                for k, v in self._load_file(path).items():
                    mats.setdefault(k, v)
        if self.vfs_dir:
            common_vfs = "/".join(self.vfs_dir.split("/")[:-1]) + "/common"
            for path in self._find_vfs_jsons(common_vfs):
                for k, v in self._load_vfs(path).items():
                    mats.setdefault(k, v)

        self.materials = mats
        _cache_vehicle_dir = cache_key
        _cache_materials = dict(mats)
        self.build_lookup_maps()

    # -- resolution --

    @staticmethod
    def _normalize_name(name):
        """Fork's normalize_name: '-material' suffix + lowercase; also strip
        whitespace and Blender-style '.001' dedup suffixes present in some
        source names."""
        name = name.strip()
        if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
            name = name[:-4]
        if len(name) > 9 and name.endswith("-material"):
            name = name[:-9]
        return name.lower()

    def build_lookup_maps(self):
        self.mapto_to_key.clear()
        self.normalized_to_key.clear()
        for key, mat in self.materials.items():
            map_to = mat.get("mapTo", "")
            if map_to and map_to != key:
                self.mapto_to_key.setdefault(map_to, key)
        for key, mat in self.materials.items():
            self.normalized_to_key.setdefault(self._normalize_name(key), key)
            map_to = mat.get("mapTo", "")
            if map_to:
                self.normalized_to_key.setdefault(
                    self._normalize_name(map_to), key)

    def resolve_material(self, mat_name, skin_slots=None, glow_map=None):
        """Fork's resolve_material() order:
        glowMap remap → skin variants → mapTo → direct → normalized."""
        if skin_slots is None:
            skin_slots = self.skin_slots
        if glow_map is None:
            glow_map = self.glow_map
        effective = mat_name

        # glowMap: "dae_name=off_name;..." remaps glow materials.
        if glow_map:
            for pair in glow_map.split(";"):
                if "=" in pair:
                    dae_name, off_name = pair.split("=", 1)
                    if dae_name == mat_name:
                        effective = off_name
                        break

        # Skin substitution: "<slot>=<suffix>;..." → "<mat>.<slot>.<suffix>"
        # (slot "paint_design" → key "skin").
        if skin_slots:
            for pair in skin_slots.split(";"):
                if "=" not in pair:
                    continue
                slot_name, suffix = pair.split("=", 1)
                key_name = "skin" if slot_name == "paint_design" else slot_name
                skin_key = f"{effective}.{key_name}.{suffix}"
                skin_mat = self.materials.get(skin_key)
                if skin_mat is not None:
                    return skin_mat

        key = self.mapto_to_key.get(effective)
        if key is not None:
            return self.materials[key]

        direct = self.materials.get(effective)
        if direct is not None:
            return direct

        norm_key = self.normalized_to_key.get(self._normalize_name(effective))
        if norm_key is not None:
            return self.materials[norm_key]

        return None

    # -- material creation --

    def import_material(self, dae_mat_name, base_dir="", existing=None,
                        dae_diffuse=None):
        """Fork's import_material(): resolve → create/reuse Material →
        populate ShaderNodeBeamngMaterial → sync flags. Returns Material."""
        from .material_json_handler import (
            import_material_from_dict, setup_fallback_material_node)

        bng_mat = self.resolve_material(dae_mat_name)
        effective_name = dae_mat_name
        if bng_mat is not None and bng_mat.get("name"):
            effective_name = bng_mat["name"]

        mat = existing
        if mat is None:
            mat = bpy.data.materials.get(effective_name)
        if mat is None:
            mat = bpy.data.materials.new(effective_name)
            mat.use_nodes = True
            if bng_mat is not None:
                import_material_from_dict(bng_mat, base_dir or self.vehicle_dir,
                                          material=mat, dae_name=dae_mat_name,
                                          dae_diffuse=dae_diffuse,
                                          vfs_dir=self.vfs_dir)
            else:
                setup_fallback_material_node(mat, dae_mat_name,
                                             base_dir or self.vehicle_dir,
                                             dae_diffuse,
                                             vfs_dir=self.vfs_dir)
        else:
            # Reuse existing material — repopulate when it has no BeamNG node.
            has_bng = (mat.use_nodes and mat.node_tree is not None and any(
                n.bl_idname == 'ShaderNodeBeamngMaterial'
                for n in mat.node_tree.nodes))
            if not has_bng:
                if bng_mat is not None:
                    import_material_from_dict(bng_mat,
                                              base_dir or self.vehicle_dir,
                                              material=mat,
                                              dae_name=dae_mat_name,
                                              dae_diffuse=dae_diffuse,
                                              vfs_dir=self.vfs_dir)
                else:
                    setup_fallback_material_node(mat, dae_mat_name,
                                                 base_dir or self.vehicle_dir,
                                                 dae_diffuse,
                                                 vfs_dir=self.vfs_dir)
        return mat
