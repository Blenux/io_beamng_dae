"""BeamNG sidebar — VIEW3D N-panel for glowmap states, electrics and
material validation."""

import bpy
from bpy.props import EnumProperty, StringProperty, BoolProperty
from bpy.types import Operator, Panel

from ..core import glowmap

_GLOW_STATES = (
    ("0", "Auto", "Follow the electrics trigger (defaults off)"),
    ("1", "Off", "Force the off material"),
    ("2", "On", "Force the on material"),
    ("3", "Intense", "Force the on_intense material"),
)


def _state_update(self, context):
    self["bng_glowmap_state"] = int(self.bng_glowmap_state)
    glowmap.apply_glow_state(self)


def _all_update(self, context):
    state = int(context.scene.bng_glowmap_all)
    for ob in glowmap.glowmap_objects(context.scene):
        if int(ob.get("bng_glowmap_state", 0)) != state:
            ob["bng_glowmap_state"] = state
        glowmap.apply_glow_state(ob, state=state, scene=context.scene)


def _electric_update(self, context):
    scene = context.scene
    group = scene.get("bng_electrics")
    if group is None:
        scene["bng_electrics"] = {self.name: float(self.value)}
    else:
        group[self.name] = float(self.value)
    glowmap.apply_all(scene)


class BNG_ElectricChannel(bpy.types.PropertyGroup):
    name: StringProperty()
    value: bpy.props.FloatProperty(name="Value", update=_electric_update)
    on: BoolProperty(name="On", update=_electric_update)


class BNG_OT_electric_set(Operator):
    """Toggle an electrics channel (writes scene bng_electrics IDP)"""

    bl_idname = "beamng.electric_set"
    bl_label = "Toggle Electrics Channel"

    channel: StringProperty()

    def execute(self, context):
        scene = context.scene
        group = scene.get("bng_electrics")
        if group is not None and self.channel in group:
            group[self.channel] = 0.0 if float(group[self.channel]) > 0.0 else 1.0
        glowmap.apply_all(scene)
        return {'FINISHED'}


class BNG_OT_electric_remove(Operator):
    """Remove an electrics channel"""

    bl_idname = "beamng.electric_remove"
    bl_label = "Remove Electrics Channel"

    channel: StringProperty()

    def execute(self, context):
        group = context.scene.get("bng_electrics")
        if group is not None and self.channel in group:
            del group[self.channel]
        glowmap.apply_all(context.scene)
        return {'FINISHED'}


class BNG_OT_glowmap_apply(Operator):
    """Re-apply glowMap material states from stored states + electrics"""

    bl_idname = "beamng.glowmap_apply"
    bl_label = "Apply GlowMap States"

    def execute(self, context):
        glowmap.apply_all(context.scene)
        return {'FINISHED'}


class BNG_OT_validate_materials(Operator):
    """Validate BeamNG materials — missing textures and unmapped keys"""

    bl_idname = "beamng.validate_materials"
    bl_label = "Validate Materials"

    def execute(self, context):
        from ..core.materials_library import resolve_texture_path, \
            _try_ext_fallback  # noqa
        from ..core.bng_vfs import vfs_get_global
        import json as _json

        scene = context.scene
        vdir = scene.get("bng_vehicle_dir", "")
        vfs = vfs_get_global()

        checked = missing = extras = 0
        missing_names = set()
        for mat in bpy.data.materials:
            if not mat.use_nodes or mat.node_tree is None:
                continue
            node = next((n for n in mat.node_tree.nodes
                         if n.bl_idname == 'ShaderNodeBeamngMaterial'), None)
            if node is None:
                continue
            checked += 1
            for st in node.stages:
                for attr in dir(st):
                    if not attr.endswith("_map") or attr.endswith("_map_img") \
                            or attr.endswith("_map_uv") or attr.endswith("_map_strength"):
                        continue
                    path = getattr(st, attr, "")
                    if not path:
                        continue
                    ok = False
                    if vfs.has_mounts():
                        ok = vfs.file_exists(path) or vfs.file_exists(
                            vdir.rstrip("/") + "/" + path.split("/")[-1])
                    if not ok and vdir:
                        ok = bool(resolve_texture_path(path, vdir))
                    elif not ok:
                        ok = bool(resolve_texture_path(path, ""))
                    if not ok:
                        missing += 1
                        missing_names.add(path.split("/")[-1])
                if st.extra_json:
                    try:
                        extras += len(_json.loads(st.extra_json))
                    except Exception:
                        pass
            if node.extra_json:
                try:
                    extras += len(_json.loads(node.extra_json))
                except Exception:
                    pass

        msg = f"{checked} BeamNG material(s): {missing} missing texture(s), " \
              f"{extras} unmapped key(s) in extra_json"
        self.report({'INFO'}, msg)
        if missing_names:
            print("Missing textures:", ", ".join(sorted(missing_names)[:25]))
        return {'FINISHED'}


class BNG_OT_rebuild_materials(Operator):
    """Rebuild every BeamNG material's internal node graph"""

    bl_idname = "beamng.rebuild_materials"
    bl_label = "Rebuild All BeamNG Materials"

    def execute(self, context):
        from ..nodes.beamng_material_graph import rebuild_all_beamng_materials
        rebuild_all_beamng_materials()
        return {'FINISHED'}


class VIEW3D_PT_beamng(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BeamNG'
    bl_label = "BeamNG"

    def draw(self, context):
        layout = self.layout
        layout.label(text="BeamNG Tools", icon='MATERIAL')


class VIEW3D_PT_beamng_glowmap(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BeamNG'
    bl_label = "GlowMap"
    bl_parent_id = "VIEW3D_PT_beamng"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and bool(
            glowmap.glowmap_objects(context.scene))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        col = layout.column(align=True)
        col.label(text="All Objects")
        col.prop(scene, "bng_glowmap_all", expand=True)

        for ob in glowmap.glowmap_objects(scene):
            box = layout.box()
            row = box.row()
            row.label(text=ob.name, icon='MESH_DATA')
            box.prop(ob, "bng_glowmap_state", expand=True)
            for e in glowmap.decode_entries(ob):
                col = box.column(align=True)
                col.scale_y = 0.85
                col.label(text=f"{e['dae']}  (slot {e['slot']})",
                          icon='MATERIAL')
                col.label(text=f"off: {e['off'] or '-'}   on: {e['on'] or '-'}"
                               + (f"   intense: {e['on_intense']}"
                                  if e['on_intense'] else ""))
                if e['func']:
                    col.label(text=f"func: {e['func']}")

        layout.operator("beamng.glowmap_apply", text="Apply States",
                        icon='FILE_REFRESH')


class VIEW3D_PT_beamng_electrics(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BeamNG'
    bl_label = "Electrics"
    bl_parent_id = "VIEW3D_PT_beamng_glowmap"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        group = scene.get("bng_electrics")
        if not group:
            layout.label(text="No electrics channels — add one below")
        else:
            for name in sorted(group):
                row = layout.row(align=True)
                val = float(group.get(name, 0.0) or 0.0)
                icon = 'CHECKBOX_HLT' if val > 0.0 else 'CHECKBOX_DEHLT'
                op = row.operator("beamng.electric_set", text=name,
                                  icon=icon)
                op.channel = name
                row.label(text=f"{val:g}")
                op = row.operator("beamng.electric_remove", text="", icon='X')
                op.channel = name
        row = layout.row(align=True)
        row.prop(context.window_manager, "bng_electric_new", text="")
        row.operator("beamng.electric_add", text="Add", icon='ADD')


class BNG_OT_electric_add(Operator):
    """Add an electrics channel driving glowMap triggers"""

    bl_idname = "beamng.electric_add"
    bl_label = "Add Electrics Channel"

    def execute(self, context):
        name = context.window_manager.bng_electric_new.strip()
        if not name:
            return {'CANCELLED'}
        scene = context.scene
        group = scene.get("bng_electrics")
        if group is None:
            scene["bng_electrics"] = {name: 0.0}
        elif name not in group:
            group[name] = 0.0
        glowmap.apply_all(scene)
        return {'FINISHED'}


class VIEW3D_PT_beamng_materials(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BeamNG'
    bl_label = "Materials"
    bl_parent_id = "VIEW3D_PT_beamng"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("beamng.validate_materials", icon='CHECKMARK')
        col.operator("beamng.rebuild_materials", icon='FILE_REFRESH')


classes = (
    BNG_ElectricChannel,
    BNG_OT_glowmap_apply,
    BNG_OT_validate_materials,
    BNG_OT_rebuild_materials,
    BNG_OT_electric_add,
    BNG_OT_electric_set,
    BNG_OT_electric_remove,
    VIEW3D_PT_beamng,
    VIEW3D_PT_beamng_glowmap,
    VIEW3D_PT_beamng_electrics,
    VIEW3D_PT_beamng_materials,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.bng_glowmap_state = EnumProperty(
        name="Glow State",
        items=_GLOW_STATES,
        default="0",
        update=_state_update,
    )
    bpy.types.Scene.bng_glowmap_all = EnumProperty(
        name="Glow State",
        items=_GLOW_STATES,
        default="0",
        update=_all_update,
    )
    bpy.types.WindowManager.bng_electric_new = StringProperty(
        name="Channel", default="")
    bpy.types.Scene.bng_electric_channels = bpy.props.CollectionProperty(
        type=BNG_ElectricChannel)


def unregister():
    del bpy.types.Scene.bng_electric_channels
    del bpy.types.WindowManager.bng_electric_new
    del bpy.types.Scene.bng_glowmap_all
    del bpy.types.Object.bng_glowmap_state

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
