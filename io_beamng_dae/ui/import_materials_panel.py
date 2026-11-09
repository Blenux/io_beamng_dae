"""Import panel for materials.json file browser options."""

import bpy


class ImportMaterialsJSONPanel(bpy.types.Panel):
    bl_label = "Import Materials JSON"
    bl_idname = "MAT_PT_import_json"
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        if not sfile or not sfile.active_operator:
            return False
        return sfile.active_operator.bl_idname == "import_scene.materials_json"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        op = context.space_data.active_operator

        layout.prop(op, "texture_base_dir")
