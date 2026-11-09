"""Node package — registers the "BeamNG" category in the shader Add menu
(fork: node_add_menu_shader.py adds ShaderNodeBeamngMaterial there)."""

import bpy
import nodeitems_utils
from nodeitems_utils import NodeCategory, NodeItem, register_node_categories, \
    unregister_node_categories


def _bng_category_poll(context):
    space = context.space_data
    return getattr(space, 'tree_type', None) == 'ShaderNodeTree'


class BeamNGNodeCategory(NodeCategory):
    @classmethod
    def poll(cls, context):
        return _bng_category_poll(context)


categories = [
    BeamNGNodeCategory(
        "SH_NEW_BEAMNG", "BeamNG",
        items=[
            NodeItem("ShaderNodeBeamngMaterial",
                     label="BeamNG Material"),
        ]),
]


def register():
    register_node_categories("BEAMNG_NODES", categories)
    # register_node_categories only stores the category — the addon must
    # hook the draw function into NODE_MT_add itself.
    bpy.types.NODE_MT_add.append(nodeitems_utils.draw_node_categories_menu)


def unregister():
    try:
        bpy.types.NODE_MT_add.remove(nodeitems_utils.draw_node_categories_menu)
        unregister_node_categories("BEAMNG_NODES")
    except (KeyError, ValueError):
        pass
