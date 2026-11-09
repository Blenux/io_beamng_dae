import bpy
import os
import time
import threading
import numpy as np
from bpy.props import StringProperty, BoolProperty, FloatProperty, CollectionProperty
from bpy_extras.io_utils import ImportHelper

from ..core.mesh_builder import MeshBuilder


# ---------------------------------------------------------------------------
# Material creation — MaterialLibrary (fork's BeamNGMaterialImporter)
# ---------------------------------------------------------------------------

def _find_vehicle_dir(filepath):
    """Directory containing main.materials.json for this DAE/CDAE.

    Tries the file's directory, then its parent, then
    vehicles/<vehicle_name>/ relative to parent. Returns the DAE's own
    directory when nothing matches (auto-discovery still applies).
    """
    base_dir = os.path.dirname(filepath)
    candidates = [
        base_dir,
        os.path.dirname(base_dir),
    ]
    vehicle_name = os.path.basename(base_dir)
    if vehicle_name:
        candidates.append(
            os.path.join(base_dir, "..", "vehicles", vehicle_name))
    for cand in candidates:
        cand = os.path.normpath(cand)
        if os.path.isfile(os.path.join(cand, "main.materials.json")):
            return cand
    return base_dir


def _create_materials(materials_data, library, base_dir):
    """Create Blender materials from parsed DAE material data.

    Matches the fork's import path: every DAE material resolves through
    MaterialLibrary — JSON match → BeamNG node populated from JSON;
    no match → auto-discovery fallback node (never a raw Principled).

    Returns the list of materials (in DAE order).
    """
    materials = []
    for mat_data in materials_data:
        name = mat_data.get("name", "default")

        # DAE diffuse → color_map_color when JSON has no diffuseColor
        # (fork's standalone_import cdae_diffuse path).
        dae_diffuse = None
        bc = mat_data.get("base_color")
        if bc is not None:
            arr = np.asarray(bc, dtype=np.float32).flatten()
            if arr.size >= 4:
                dae_diffuse = tuple(float(v) for v in arr[:4])

        if library is not None:
            mat = library.import_material(name, base_dir,
                                          dae_diffuse=dae_diffuse)
        else:
            # materials.json import disabled — plain fallback material.
            mat = _create_fallback_material(mat_data)
        materials.append(mat)

        # Store roundtrip flags from DAE (not in JSON)
        mat["dae_has_shininess"] = bool(mat_data.get("has_shininess", False))
        mat["dae_has_reflectivity"] = bool(mat_data.get("has_reflectivity", False))
        mat["dae_ior"] = float(mat_data.get("ior", 1.45))

    return materials


def _create_fallback_material(mat_data):
    """Create a Blender material with Principled BSDF from parsed PBR data.

    Used when no materials.json is available or when a DAE material
    has no matching entry in the JSON.
    """
    name = mat_data.get("name", "default")
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True

    # Extract PBR values
    base_color = mat_data.get("base_color")
    roughness = float(mat_data.get("roughness", 0.5))
    metallic = float(mat_data.get("metallic", 0.0))
    ior = float(mat_data.get("ior", 1.45))
    has_shininess = bool(mat_data.get("has_shininess", False))
    has_reflectivity = bool(mat_data.get("has_reflectivity", False))

    bc_r = bc_g = bc_b = 0.8
    bc_a = 1.0
    if base_color is not None:
        bc = np.asarray(base_color, dtype=np.float32).flatten()
        if bc.size >= 4:
            bc_r, bc_g, bc_b, bc_a = float(bc[0]), float(bc[1]), float(bc[2]), float(bc[3])

    # Set viewport properties (work regardless of node setup)
    mat.diffuse_color = (bc_r, bc_g, bc_b, bc_a)
    mat.roughness = roughness
    mat.metallic = metallic

    # Set Principled BSDF node inputs
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
    if bsdf is not None:
        if "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (bc_r, bc_g, bc_b, bc_a)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = roughness
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = metallic
        if "IOR" in bsdf.inputs:
            bsdf.inputs["IOR"].default_value = ior

    # Store roundtrip flags as custom properties
    mat["dae_has_shininess"] = has_shininess
    mat["dae_has_reflectivity"] = has_reflectivity
    mat["dae_ior"] = ior

    return mat


class ImportDAE(bpy.types.Operator, ImportHelper):
    """Import a Collada (.dae) or Cached Collada (.cdae) file"""

    bl_idname = "import_scene.dae_native"
    bl_label = "Import DAE/CDAE"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(
        default="*.dae;*.cdae",
        options={"HIDDEN"},
    )

    # Files collection and directory for FileHandler drag/drop support
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    directory: StringProperty(
        subtype='DIR_PATH',
        options={"HIDDEN", "SKIP_SAVE"},
    )

    global_scale: FloatProperty(
        name="Scale",
        description="Manual scale factor",
        default=1.0,
        min=0.001,
        max=1000.0,
    )
    custom_normals: BoolProperty(
        name="Custom Normals",
        description="Import custom/smooth normals",
        default=True,
    )
    import_to_collection: BoolProperty(
        name="Import to Collection",
        description="Create a collection named after the imported file and place objects in it",
        default=True,
    )
    import_materials_json: BoolProperty(
        name="Import materials.json",
        description="Look for a main.materials.json next to the DAE/CDAE and import "
                    "BeamNG material definitions (textures, flags, paints) from it",
        default=True,
    )
    skin_slots: StringProperty(
        name="Skin",
        description="Skin slot substitutions, e.g. 'skin=red' or 'slot=suffix;...' "
                    "(fork MaterialImportParams::skin_slots)",
        default="",
    )
    glow_map: StringProperty(
        name="Glow Map",
        description="Glow material remaps, e.g. 'mat=mat_off;...' "
                    "(fork MaterialImportParams::glow_map_str)",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header, body = layout.panel("DAE_import_general", default_closed=False)
        header.label(text="General")
        if body:
            body.prop(self, "global_scale")

        header, body = layout.panel("DAE_import_mesh", default_closed=False)
        header.label(text="Mesh")
        if body:
            body.prop(self, "custom_normals")

        header, body = layout.panel("DAE_import_pipeline", default_closed=False)
        header.label(text="Pipeline")
        if body:
            body.prop(self, "import_to_collection")

        header, body = layout.panel("DAE_import_materials", default_closed=False)
        header.label(text="Materials")
        if body:
            body.prop(self, "import_materials_json")
            if self.import_materials_json:
                body.prop(self, "skin_slots")
                body.prop(self, "glow_map")

    def invoke(self, context, event):
        # Show popup dialog for drag/drop and menu invocation
        return ImportHelper.invoke_popup(self, context)

    def execute(self, context):
        # Handle multiple files from FileHandler (drag/drop)
        if self.files:
            ret = {"CANCELLED"}
            for file in self.files:
                filepath = os.path.join(self.directory, file.name)
                if self._import_single(context, filepath) == {"FINISHED"}:
                    ret = {"FINISHED"}
            return ret
        else:
            return self._import_single(context, self.filepath)

    def _import_single(self, context, filepath):
        """Import a single DAE or CDAE file using a background thread for parsing."""
        from .. import native as cdae_native

        wm = context.window_manager
        is_cdae = filepath.lower().endswith(".cdae")
        wall_start = time.time()

        wm.progress_begin(0, 100)
        self.report({"INFO"}, "Parsing file...")

        # Run parse in a background thread to keep UI responsive
        parse_result = {}
        parse_start = time.time()

        def _parse_worker():
            try:
                if is_cdae:
                    parse_result["data"] = cdae_native.parse_cdae(filepath)
                else:
                    parse_result["data"] = cdae_native.parse_dae(filepath)
            except Exception as e:
                parse_result["error"] = str(e)

        thread = threading.Thread(target=_parse_worker, daemon=True)
        thread.start()

        # Poll thread completion while keeping Blender UI alive
        while thread.is_alive():
            wm.progress_update(10)
            time.sleep(0.05)

        thread.join()
        parse_time = time.time() - parse_start

        if "error" in parse_result:
            self.report({"ERROR"}, f"Failed to parse file: {parse_result['error']}")
            wm.progress_end()
            return {"CANCELLED"}

        data = parse_result.get("data")
        wm.progress_update(50)

        if not data or not data.get("meshes"):
            self.report({"WARNING"}, "No meshes found in file")
            wm.progress_end()
            return {"FINISHED"}

        # MaterialLibrary: merged *.materials.json for the vehicle dir
        # (main → skin → dynamicDecals → subdirs → vehicles/common).
        library = None
        vehicle_dir = _find_vehicle_dir(filepath)
        if self.import_materials_json:
            from ..core.materials_library import MaterialLibrary
            from ..core.bng_vfs import auto_mount
            # VFS mounts — game zips, user path, mods, local dirs (fork's
            # bng_vfs_mount_all_sources). Lets /vehicles/... paths resolve
            # even when the DAE sits outside an extracted vehicle tree.
            addon_pkg = __package__.split(".")[0]
            addon = context.preferences.addons.get(addon_pkg)
            prefs = addon.preferences if addon else None
            game_dir = getattr(prefs, "game_vehicles_dir", "") \
                if prefs else ""
            user_dir = getattr(prefs, "user_dir", "") if prefs else ""
            mat_names = [m.get("name", "")
                         for m in data.get("materials", [])]
            vfs_dir = auto_mount(filepath, vehicle_dir, mat_names,
                                 game_vehicles_dir=game_dir,
                                 user_dir=user_dir)
            library = MaterialLibrary(vehicle_dir, vfs_dir=vfs_dir)
            library.load()
            library.skin_slots = self.skin_slots
            # Glow remap defaults to the vehicle's JBeam glowMap so flexbody
            # names (covet_headlightglass) resolve to off-state materials.
            if self.glow_map:
                library.glow_map = self.glow_map
            else:
                from ..core.glowmap import glow_map_string
                library.glow_map = glow_map_string(vehicle_dir)

        # Time Blender mesh building (must run on main thread)
        self.report({"INFO"}, "Building meshes...")
        build_start = time.time()

        # Create materials — BeamNG node from JSON where available,
        # auto-discovery fallback otherwise.
        materials = _create_materials(
            data.get("materials", []),
            library=library,
            base_dir=vehicle_dir,
        )

        # Scene paints — resolve from info.json/paint library (fork's
        # BeamNGPaintResolver).
        if self.import_materials_json:
            try:
                from ..core.paints import resolve_and_apply_paints
                resolve_and_apply_paints(context.scene, vehicle_dir)
            except Exception as e:
                self.report({"WARNING"}, f"Paint resolution failed: {e}")

        # Build meshes
        builder = MeshBuilder(
            context=context,
            materials=materials,
            global_scale=self.global_scale,
            custom_normals=self.custom_normals,
            is_dae=not is_cdae,
            filepath=filepath,
            use_collection=self.import_to_collection,
        )

        objects = builder.build_meshes(data)
        wm.progress_update(90)
        builder.build_node_hierarchy(data, objects)
        build_time = time.time() - build_start

        # GlowMap — tag objects whose materials have JBeam glowMap entries
        # and persist vehicle context for the sidebar tools.
        if self.import_materials_json:
            context.scene["bng_vehicle_dir"] = vehicle_dir or ""
            context.scene["bng_vfs_dir"] = library.vfs_dir if library else ""
            try:
                from ..core.glowmap import tag_objects
                tag_objects(objects, vehicle_dir)
            except Exception as e:
                self.report({"WARNING"}, f"GlowMap tagging failed: {e}")

        # Store DAE material order for 1:1 roundtrip preservation
        mat_names = [m.get("name", "") for m in data.get("materials", [])]
        if mat_names:
            col = builder._get_import_collection() if builder.use_collection else context.collection
            col["dae_material_order"] = mat_names

        # Force depsgraph update so objects are actually visible in viewport
        self.report({"INFO"}, "Updating viewport...")
        depsgraph_start = time.time()
        context.view_layer.update()
        depsgraph_time = time.time() - depsgraph_start

        wm.progress_update(100)
        wm.progress_end()

        mesh_count = len([o for o in objects.values() if o.type == 'MESH'])
        beamng_count = sum(
            1 for m in materials
            if m.use_nodes and any(n.bl_idname == 'ShaderNodeBeamngMaterial' for n in m.node_tree.nodes)
        )
        wall_time = time.time() - wall_start
        msg = f"Imported {mesh_count} mesh(es), {len(materials)} material(s)"
        if beamng_count > 0:
            msg += f", {beamng_count} BeamNG node(s)"
        msg += f" in {wall_time:.2f}s (parse: {parse_time:.2f}s, build: {build_time:.2f}s, depsgraph: {depsgraph_time:.2f}s)"
        self.report({"INFO"}, msg)

        # Log true total time after viewport redraw + shader compilation.
        # print() not self.report — the operator's report list is freed when
        # execute() returns, so reporting from a timer is a use-after-free.
        _final_start = wall_start
        _final_mesh_count = mesh_count
        _final_mat_count = len(materials)

        def _post_import_timer():
            total = time.time() - _final_start
            print(f"Import complete (viewport ready): {_final_mesh_count} "
                  f"mesh(es), {_final_mat_count} material(s), "
                  f"total time: {total:.2f}s")
            return None  # one-shot, don't repeat

        bpy.app.timers.register(_post_import_timer, first_interval=0.5)
        return {"FINISHED"}
