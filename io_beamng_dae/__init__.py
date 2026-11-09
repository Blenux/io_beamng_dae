import os

import bpy
from bpy.props import StringProperty

from .nodes.beamng_material_node import (
    BeamNGStageSlot,
    BeamNGMaterialNode,
    NODE_OT_bng_add_layer,
    NODE_OT_bng_remove_layer,
    NODE_OT_bng_move_layer,
    NODE_OT_bng_compile_to_nodes,
)
from .core.paints import BeamNGVehiclePaintSlot, BeamNGVehiclePaints
from .operators.import_dae import ImportDAE
from .operators.export_dae import ExportDAE
from .operators.import_materials_json import ImportMaterialsJSON
from .operators.export_materials_json import ExportMaterialsJSON
from .ui.filehandler import IO_FH_dae_native
from .ui.filehandler_materials import IO_FH_materials_json
from .ui.import_panel import ImportDAEPanel
from .ui.export_panel import ExportDAEPanel
from .ui.export_materials_panel import ExportMaterialsJSONPanel
from .ui.import_materials_panel import ImportMaterialsJSONPanel


def menu_import(self, context):
    layout = self.layout
    layout.operator(ImportDAE.bl_idname, text="BeamNG Collada (.dae, .cdae)")
    layout.operator(ImportMaterialsJSON.bl_idname, text="BeamNG Materials (.materials.json)")


def menu_export(self, context):
    layout = self.layout
    layout.operator(ExportDAE.bl_idname, text="BeamNG Collada (.dae, .cdae)")
    layout.operator(ExportMaterialsJSON.bl_idname, text="BeamNG Materials (.materials.json)")


def _detect_game_vehicles_dir():
    """BeamNG.drive content/vehicles — Steam default locations."""
    for p in (
        os.path.expanduser(
            "~/.local/share/Steam/steamapps/common/BeamNG.drive"
            "/content/vehicles"),
        os.path.expanduser(
            "~/Library/Application Support/Steam/steamapps/common"
            "/BeamNG.drive/content/vehicles"),
        "C:/Program Files (x86)/Steam/steamapps/common/BeamNG.drive"
        "/content/vehicles",
    ):
        if os.path.isdir(p):
            return p
    return ""


def _detect_user_dir():
    """BeamNG.drive user path (vehicles/, mods/)."""
    for p in (
        os.path.expanduser("~/Documents/BeamNG.drive"),
        os.path.expanduser("~/.local/share/BeamNG.drive"),
    ):
        if os.path.isdir(p):
            return p
    return ""


class BeamNGAddonPreferences(bpy.types.AddonPreferences):
    """BeamNG content locations — the VFS mounts these so materials and
    textures resolve from game zips/extracted dirs (fork space_beamng)."""

    bl_idname = __package__

    game_vehicles_dir: StringProperty(
        name="Game Vehicles",
        description="BeamNG.drive content/vehicles directory — "
                    "vehicle zips and common.zip are mounted for "
                    "material/texture resolution",
        subtype='DIR_PATH',
        default=_detect_game_vehicles_dir(),
    )
    user_dir: StringProperty(
        name="User Path",
        description="BeamNG.drive user directory — vehicles/ and mods/ "
                    "override mounted game content",
        subtype='DIR_PATH',
        default=_detect_user_dir(),
    )


classes = (
    BeamNGAddonPreferences,
    # Property groups (before the node that uses them)
    BeamNGStageSlot,
    BeamNGVehiclePaintSlot,
    BeamNGVehiclePaints,
    # Node
    BeamNGMaterialNode,
    NODE_OT_bng_add_layer,
    NODE_OT_bng_remove_layer,
    NODE_OT_bng_move_layer,
    NODE_OT_bng_compile_to_nodes,
    # DAE operators
    ImportDAE,
    ExportDAE,
    # Materials JSON operators
    ImportMaterialsJSON,
    ExportMaterialsJSON,
    # File handlers
    IO_FH_dae_native,
    IO_FH_materials_json,
    # Panels
    ImportDAEPanel,
    ExportDAEPanel,
    ExportMaterialsJSONPanel,
    ImportMaterialsJSONPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    from . import nodes
    nodes.register()

    from .ui import beamng_sidebar
    beamng_sidebar.register()

    bpy.types.Scene.bng_vehicle_paints = bpy.props.PointerProperty(
        type=BeamNGVehiclePaints)

    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)

    del bpy.types.Scene.bng_vehicle_paints

    from .ui import beamng_sidebar
    beamng_sidebar.unregister()

    from . import nodes
    nodes.unregister()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
