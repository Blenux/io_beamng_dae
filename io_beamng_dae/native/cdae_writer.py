"""Pure-Python CDAE binary writer — replaces cdae_binary_export.cpp.

Writes CDAE binary files (custom msgpack + optional zstd compression).
Accepts a dict with numpy arrays matching the cdae_native interface.
"""

import struct

import numpy as np

from .msgpack_writer import MsgPackWriter

MESH_NULL = 4
MESH_STANDARD = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compress_zstd(data, level=3):
    """Compress data with zstd at the given compression level."""
    try:
        import zstandard as zstd
    except ImportError:
        try:
            import zstd as zstd
        except ImportError:
            raise RuntimeError("zstandard library not found. Install with: pip install zstandard")
    cctx = zstd.ZstdCompressor(level=level)
    return cctx.compress(data)


def _numpy_to_block(data, elem_size):
    """Convert a numpy array to a VectorBlock dict (count, element_size, bytes)."""
    if data is None:
        return {"count": 0, "element_size": elem_size, "bytes": b""}
    arr = np.ascontiguousarray(data)
    raw_bytes = arr.tobytes()
    count = len(raw_bytes) // elem_size
    return {"count": count, "element_size": elem_size, "bytes": raw_bytes}


def _write_vector_block(w, block):
    """Write a VectorBlock to a MsgPackWriter."""
    w.write_int32(block["count"])
    w.write_int32(block["element_size"])
    if not block["bytes"]:
        w.write_nil()
    else:
        w.write_binary(block["bytes"])


def _write_float_array(w, values, count):
    """Write an array of float32 values to a MsgPackWriter."""
    w.write_array_size(count)
    for i in range(count):
        w.write_float(float(values[i]))


# ---------------------------------------------------------------------------
# Raw tree building (NodeEntry/ObjectEntry → CDAE binary raw tree)
# ---------------------------------------------------------------------------

def _build_raw_tree(shape):
    """Build raw_nodes, raw_objects, default_rotations, etc. from nodes/objects."""
    nodes = shape.get("nodes", [])
    objects = shape.get("objects", [])
    names = shape.get("names", [])

    n_nodes = len(nodes)
    n_objs = len(objects)

    vec_blocks = {}

    # Nodes: 5 x int32 per node
    if n_nodes > 0:
        node_bytes = bytearray(n_nodes * 20)
        for i, node in enumerate(nodes):
            # Find name index
            name_idx = -1
            for j, name in enumerate(names):
                if name == node["name"]:
                    name_idx = j
                    break
            struct.pack_into("<5i", node_bytes, i * 20,
                             name_idx,
                             node["parent_index"],
                             node["first_object"],
                             node["first_child"],
                             node["next_sibling"])
        vec_blocks["raw_nodes"] = {
            "count": n_nodes,
            "element_size": 20,
            "bytes": bytes(node_bytes),
        }
    else:
        vec_blocks["raw_nodes"] = {"count": 0, "element_size": 20, "bytes": b""}

    # Objects: 6 x int32 per object
    if n_objs > 0:
        obj_bytes = bytearray(n_objs * 24)
        for i, obj in enumerate(objects):
            struct.pack_into("<6i", obj_bytes, i * 24,
                             obj["name_index"],
                             obj["num_meshes"],
                             obj["start_mesh"],
                             obj["node_index"],
                             obj["next_sibling"],
                             -1)  # first_decal
        vec_blocks["raw_objects"] = {
            "count": n_objs,
            "element_size": 24,
            "bytes": bytes(obj_bytes),
        }
    else:
        vec_blocks["raw_objects"] = {"count": 0, "element_size": 24, "bytes": b""}

    # Default rotations: 4 x int16 per node. CDAE w is negated.
    if n_nodes > 0:
        rot_bytes = bytearray(n_nodes * 8)
        for i, node in enumerate(nodes):
            q = node["quaternion"]
            struct.pack_into("<4h", rot_bytes, i * 8,
                             int(float(q[0]) * 32767.0),
                             int(float(q[1]) * 32767.0),
                             int(float(q[2]) * 32767.0),
                             int(-float(q[3]) * 32767.0))
        vec_blocks["default_rotations"] = {
            "count": n_nodes,
            "element_size": 8,
            "bytes": bytes(rot_bytes),
        }
    else:
        vec_blocks["default_rotations"] = {"count": 0, "element_size": 8, "bytes": b""}

    # Default translations: 3 x float per node
    if n_nodes > 0:
        trans_bytes = bytearray(n_nodes * 12)
        for i, node in enumerate(nodes):
            t = node["translation"]
            struct.pack_into("<3f", trans_bytes, i * 12,
                             float(t[0]), float(t[1]), float(t[2]))
        vec_blocks["default_translations"] = {
            "count": n_nodes,
            "element_size": 12,
            "bytes": bytes(trans_bytes),
        }
    else:
        vec_blocks["default_translations"] = {"count": 0, "element_size": 12, "bytes": b""}

    # Default aligned scales: 3 x float per node
    if n_nodes > 0:
        scale_bytes = bytearray(n_nodes * 12)
        for i, node in enumerate(nodes):
            s = node["scale"]
            struct.pack_into("<3f", scale_bytes, i * 12,
                             float(s[0]), float(s[1]), float(s[2]))
        vec_blocks["node_aligned_scales"] = {
            "count": n_nodes,
            "element_size": 12,
            "bytes": bytes(scale_bytes),
        }
    else:
        vec_blocks["node_aligned_scales"] = {"count": 0, "element_size": 12, "bytes": b""}

    # Subshape data: 1 subshape, root at node 0
    if n_nodes > 0:
        vec_blocks["sub_shape_first_node"] = {
            "count": 1, "element_size": 4,
            "bytes": struct.pack("<i", 0),
        }
        vec_blocks["sub_shape_num_nodes"] = {
            "count": 1, "element_size": 4,
            "bytes": struct.pack("<i", n_nodes),
        }
    else:
        vec_blocks["sub_shape_first_node"] = {"count": 0, "element_size": 4, "bytes": b""}
        vec_blocks["sub_shape_num_nodes"] = {"count": 0, "element_size": 4, "bytes": b""}

    if n_objs > 0:
        vec_blocks["sub_shape_first_object"] = {
            "count": 1, "element_size": 4,
            "bytes": struct.pack("<i", 0),
        }
        vec_blocks["sub_shape_num_objects"] = {
            "count": 1, "element_size": 4,
            "bytes": struct.pack("<i", n_objs),
        }
    else:
        vec_blocks["sub_shape_first_object"] = {"count": 0, "element_size": 4, "bytes": b""}
        vec_blocks["sub_shape_num_objects"] = {"count": 0, "element_size": 4, "bytes": b""}

    # Empty vector blocks for unused fields
    for name in ["node_rotations", "node_translations", "node_uniform_scales",
                 "node_arbitrary_scale_factors", "node_arbitrary_scale_rots",
                 "ground_translations", "ground_rotations",
                 "object_states", "triggers", "details"]:
        vec_blocks[name] = {"count": 0, "element_size": 4, "bytes": b""}

    return vec_blocks


# ---------------------------------------------------------------------------
# Body building
# ---------------------------------------------------------------------------

def _build_body(shape):
    """Build the CDAE body bytes from a shape dict."""
    w = MsgPackWriter()

    # Shape info
    w.write_float(float(shape.get("smallest_visible_size", 0.0)))
    w.write_int32(int(shape.get("smallest_visible_dl", 0)))
    w.write_float(float(shape.get("radius", 0.0)))
    w.write_float(float(shape.get("tube_radius", 0.0)))

    # Center and bounds
    center = shape.get("center")
    if center is not None:
        center_arr = np.asarray(center, dtype=np.float32).flatten()
        _write_float_array(w, center_arr, 3)
    else:
        _write_float_array(w, [0, 0, 0], 3)

    bounds = shape.get("bounds")
    if bounds is not None:
        bounds_arr = np.asarray(bounds, dtype=np.float32).flatten()
        _write_float_array(w, bounds_arr, 6)
    else:
        _write_float_array(w, [0, 0, 0, 0, 0, 0], 6)

    # Build raw tree for vector blocks
    vec_blocks = _build_raw_tree(shape)

    # Write vector blocks in order
    vec_block_order = [
        "raw_nodes", "raw_objects",
        "sub_shape_first_node", "sub_shape_first_object",
        "sub_shape_num_nodes", "sub_shape_num_objects",
        "default_rotations", "default_translations",
        "node_rotations", "node_translations",
        "node_uniform_scales", "node_aligned_scales",
        "node_arbitrary_scale_factors", "node_arbitrary_scale_rots",
        "ground_translations", "ground_rotations",
        "object_states", "triggers", "details",
    ]
    for name in vec_block_order:
        _write_vector_block(w, vec_blocks[name])

    # Names
    names = shape.get("names", [])
    w.write_int32(len(names))
    for name in names:
        w.write_string(name)

    # Meshes
    meshes = shape.get("meshes", [])
    w.write_int32(len(meshes))
    for mesh in meshes:
        mesh_type = mesh.get("mesh_type", MESH_STANDARD)
        is_null = mesh.get("is_null", False)
        if is_null:
            mesh_type = MESH_NULL

        w.write_int32(mesh_type)

        if mesh_type == MESH_NULL:
            continue

        w.write_int32(int(mesh.get("num_frames", 1)))
        w.write_int32(int(mesh.get("num_mat_frames", 1)))
        w.write_int32(int(mesh.get("parent_mesh", -1)))

        # Mesh bounds and center
        mesh_bounds = mesh.get("bounds")
        if mesh_bounds is not None:
            mb_arr = np.asarray(mesh_bounds, dtype=np.float32).flatten()
            _write_float_array(w, mb_arr, 6)
        else:
            _write_float_array(w, [0, 0, 0, 0, 0, 0], 6)

        mesh_center = mesh.get("center")
        if mesh_center is not None:
            mc_arr = np.asarray(mesh_center, dtype=np.float32).flatten()
            _write_float_array(w, mc_arr, 3)
        else:
            _write_float_array(w, [0, 0, 0], 3)

        w.write_float(float(mesh.get("radius", 0.0)))

        # Mesh vector blocks
        verts = _numpy_to_block(mesh.get("vertices"), 12)
        tverts = _numpy_to_block(mesh.get("uv0"), 8)
        tverts2 = _numpy_to_block(mesh.get("uv1"), 8)
        colors = _numpy_to_block(mesh.get("colors"), 4)
        norms = _numpy_to_block(mesh.get("normals"), 12)
        encoded_norms = _numpy_to_block(mesh.get("encoded_norms"), 1)
        prims = _numpy_to_block(mesh.get("primitives"), 12)
        indices = _numpy_to_block(mesh.get("indices"), 4)
        tangents = _numpy_to_block(mesh.get("tangents"), 16)

        _write_vector_block(w, verts)
        _write_vector_block(w, tverts)
        _write_vector_block(w, tverts2)
        _write_vector_block(w, colors)
        _write_vector_block(w, norms)
        _write_vector_block(w, encoded_norms)
        _write_vector_block(w, prims)
        _write_vector_block(w, indices)
        _write_vector_block(w, tangents)

        # verts_per_frame
        vpf = mesh.get("verts_per_frame", 0)
        if vpf == 0 and verts["count"] > 0:
            vpf = verts["count"]
        w.write_int32(int(vpf))

        # mesh_flags
        w.write_int32(int(mesh.get("mesh_flags", 0)))

    # Sequences (none)
    w.write_int32(0)

    # Materials
    materials = shape.get("materials", [])
    w.write_int32(len(materials))
    # PBR flag
    w.write_bool(True)
    for mat in materials:
        w.write_string(mat.get("name", ""))
        w.write_int32(int(mat.get("flags", 3)))
        w.write_int32(int(mat.get("reflectance_map", 0)))
        w.write_int32(int(mat.get("bump_map", 0)))
        w.write_int32(int(mat.get("detail_map", 0)))
        w.write_float(float(mat.get("detail_scale", 0.0)))
        w.write_float(float(mat.get("reflection_amount", 0.0)))

        # PBR properties
        bc = mat.get("base_color")
        if bc is not None:
            bc_arr = np.asarray(bc, dtype=np.float32).flatten()
            _write_float_array(w, bc_arr, 4)
        else:
            _write_float_array(w, [0.8, 0.8, 0.8, 1.0], 4)

        w.write_float(float(mat.get("roughness", 0.5)))
        w.write_float(float(mat.get("metallic", 0.0)))

    return w.take_bytes()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_cdae(filepath, data, compress=False):
    """Write mesh/material/node data to a .cdae binary file. Returns True on success."""
    body_bytes = _build_body(data)

    # Compress body if requested
    if compress:
        body_to_write = _compress_zstd(body_bytes, level=3)
    else:
        body_to_write = body_bytes

    # Build header dict
    header = MsgPackWriter()
    header.write_map_size(4)
    header.write_string("info")
    header.write_string("BeamNG CDAE export")
    header.write_string("compression")
    header.write_bool(compress)
    header.write_string("bodysize")
    header.write_int32(len(body_to_write))
    header.write_string("objectNames")
    # Object names: shape.names[0] is root node; objects follow from index 1
    names = data.get("names", [])
    obj_count = max(0, len(names) - 1)
    header.write_array_size(obj_count)
    for i in range(1, len(names)):
        header.write_string(names[i])
    header_bytes = header.take_bytes()

    # Write file
    with open(filepath, "wb") as f:
        # File version header: LE uint16(31), uint16(0), uint32(header_size)
        f.write(struct.pack("<HHI", 31, 0, len(header_bytes)))
        f.write(header_bytes)
        f.write(body_to_write)

    return True
