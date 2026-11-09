"""FileHandler for materials.json drag-and-drop import/export support."""

import bpy
from bpy_extras.io_utils import poll_file_object_drop


class IO_FH_materials_json(bpy.types.FileHandler):
    bl_idname = "IO_FH_materials_json"
    bl_label = "Materials JSON"
    bl_import_operator = "import_scene.materials_json"
    bl_export_operator = "export_scene.materials_json"
    bl_file_extensions = ".materials.json;.json"

    @classmethod
    def poll_drop(cls, context):
        return poll_file_object_drop(context)
