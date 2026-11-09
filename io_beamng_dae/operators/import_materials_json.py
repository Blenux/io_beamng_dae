"""Import operator for main.materials.json — creates Blender materials with
BeamNGMaterialNode from a BeamNG materials.json file.

Uses MaterialLibrary for the full merged-library path (main → skin →
dynamicDecals → subdirs → vehicles/common), VFS mounts from addon prefs,
skin/glow substitutions and vehicle paint resolution — the same pipeline as
the DAE importer.
"""

import bpy
import os
from bpy.props import StringProperty, CollectionProperty
from bpy_extras.io_utils import ImportHelper

from ..core.material_json_handler import import_material_from_dict


class ImportMaterialsJSON(bpy.types.Operator, ImportHelper):
    """Import BeamNG material definitions from main.materials.json"""

    bl_idname = "import_scene.materials_json"
    bl_label = "Import Materials JSON"
    bl_options = {"REGISTER", "UNDO"}

    # Only main/skin materials files carry complete material data —
    # per-part *.materials.json files (fix, map, wheel, …) are subsets.
    # NOTE: each ;-separated pattern is truncated to 15 chars by
    # BLI_path_extension_check_glob — keep patterns short.
    filter_glob: StringProperty(
        default="*main*.json;*skin*.json",
        options={"HIDDEN"},
    )

    # Files collection for FileHandler drag/drop support
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    directory: StringProperty(
        subtype='DIR_PATH',
        options={"HIDDEN", "SKIP_SAVE"},
    )

    texture_base_dir: StringProperty(
        name="Texture Base Dir",
        description="Base directory for resolving texture paths. "
                    "If empty, uses the directory of the JSON file.",
        default="",
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

        header, body = layout.panel("MAT_import_general", default_closed=False)
        header.label(text="General")
        if body:
            body.prop(self, "texture_base_dir")

        header, body = layout.panel("MAT_import_materials", default_closed=False)
        header.label(text="Materials")
        if body:
            body.prop(self, "skin_slots")
            body.prop(self, "glow_map")

    def invoke(self, context, event):
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
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        from .. import backend
        from ..core.materials_library import MaterialLibrary
        from ..core.bng_vfs import auto_mount
        from .import_dae import _find_vehicle_dir

        vehicle_dir = self.texture_base_dir or _find_vehicle_dir(filepath)

        # VFS mounts — same sources as the DAE importer.
        addon_pkg = __package__.split(".")[0]
        addon = context.preferences.addons.get(addon_pkg)
        prefs = addon.preferences if addon else None
        game_dir = getattr(prefs, "game_vehicles_dir", "") if prefs else ""
        user_dir = getattr(prefs, "user_dir", "") if prefs else ""

        data = backend.parse_materials_json(filepath)
        if not isinstance(data, dict):
            self.report({"ERROR"}, f"Failed to parse {filepath}")
            return {"CANCELLED"}

        vfs_dir = auto_mount(filepath, vehicle_dir,
                             [k for k in data],
                             game_vehicles_dir=game_dir,
                             user_dir=user_dir)
        library = MaterialLibrary(vehicle_dir, vfs_dir=vfs_dir)
        library.load()
        library.skin_slots = self.skin_slots
        if self.glow_map:
            library.glow_map = self.glow_map
        else:
            from ..core.glowmap import glow_map_string
            library.glow_map = glow_map_string(vehicle_dir)

        imported = []
        for d in data.values():
            if not isinstance(d, dict):
                continue
            imported.append(
                import_material_from_dict(
                    d, vehicle_dir, vfs_dir=vfs_dir))

        # Scene paints — resolve from info.json/paint library.
        try:
            from ..core.paints import resolve_and_apply_paints
            resolve_and_apply_paints(context.scene, vehicle_dir)
        except Exception as e:
            self.report({"WARNING"}, f"Paint resolution failed: {e}")

        # Persist vehicle context for the glowmap/sidebar tools.
        context.scene["bng_vehicle_dir"] = vehicle_dir
        context.scene["bng_vfs_dir"] = vfs_dir or ""

        self.report({"INFO"}, f"Imported {len(imported)} material(s) from {os.path.basename(filepath)}")
        return {"FINISHED"}
