"""Export operator for main.materials.json — standalone export of BeamNG
material definitions from Blender materials that have a BeamNGMaterialNode."""

import bpy
import os
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper

from ..core.material_json_handler import export_materials_to_json


class ExportMaterialsJSON(bpy.types.Operator, ExportHelper):
    """Export BeamNG material definitions to main.materials.json"""

    bl_idname = "export_scene.materials_json"
    bl_label = "Export Materials JSON"
    bl_options = {"REGISTER"}

    filename_ext = ".json"
    filter_glob: StringProperty(
        default="*.materials.json",
        options={"HIDDEN"},
    )

    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Export materials from selected objects only",
        default=False,
    )
    vehicle_name: StringProperty(
        name="Vehicle Name",
        description="Vehicle name used for texture path resolution (e.g. 'bx'). "
                    "If empty, derived from the first material name.",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header, body = layout.panel("MAT_export_general", default_closed=False)
        header.label(text="General")
        if body:
            body.prop(self, "selected_only")
            body.prop(self, "vehicle_name")

    def check(self, context):
        # Ensure .materials.json extension
        filepath = self.filepath
        if not filepath.endswith(".materials.json") and not filepath.endswith(".json"):
            self.filepath = filepath + ".materials.json"
            return True
        return False

    def execute(self, context):
        # Collect materials
        if self.selected_only:
            objects = context.selected_objects
        else:
            objects = context.scene.objects

        materials = []
        seen = set()
        for obj in objects:
            if obj.type != 'MESH':
                continue
            for slot in obj.material_slots:
                if slot.material is None or slot.material.name in seen:
                    continue
                # Only include materials that have a BeamNGMaterialNode
                if slot.material.use_nodes:
                    has_bng = any(
                        n.bl_idname == 'ShaderNodeBeamngMaterial'
                        for n in slot.material.node_tree.nodes
                    )
                    if has_bng:
                        materials.append(slot.material)
                        seen.add(slot.material.name)

        if not materials:
            self.report({"WARNING"}, "No materials with BeamNG Material node found")
            return {"CANCELLED"}

        vehicle = self.vehicle_name
        if not vehicle and materials:
            # Derive from first material name (e.g. "bx_body" → "bx")
            vehicle = materials[0].name.split("_")[0]

        try:
            export_materials_to_json(materials, self.filepath, vehicle)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to write materials.json: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported {len(materials)} material(s) to {os.path.basename(self.filepath)}")
        return {"FINISHED"}
