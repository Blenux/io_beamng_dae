"""Pure-Python CDAE binary parser — replaces cdae_binary_import.cpp.

Reads CDAE binary files (custom msgpack + optional zstd compression).
Outputs a dict with numpy arrays matching the cdae_native interface.
"""

import struct

import numpy as np

from .msgpack_reader import MsgPackReader, MsgPackValue

MESH_NULL = 4
MESH_STANDARD = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_u32_le(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


def _decompress_zstd(data):
    """Decompress zstd-compressed data using the zstandard library."""
    try:
        import zstandard as zstd
    except ImportError:
        try:
            import zstd as zstd
        except ImportError:
            raise RuntimeError("zstandard library not found. Install with: pip install zstandard")
    dctx = zstd.ZstdDecompressor()
    return dctx.decompress(data)


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------

def _read_int32(reader):
    """Read a msgpack value and return it as int32. Returns (value, ok)."""
    v = reader.parse_value()
    if v.type == MsgPackValue.INT:
        return int(v.i), True
    if v.type == MsgPackValue.UINT:
        return int(v.u), True
    if v.type == MsgPackValue.FLOAT:
        return int(v.f), True
    return 0, False


def _read_float(reader):
    """Read a msgpack value and return it as float. Returns (value, ok)."""
    v = reader.parse_value()
    if v.type == MsgPackValue.FLOAT:
        return float(v.f), True
    if v.type == MsgPackValue.INT:
        return float(v.i), True
    if v.type == MsgPackValue.UINT:
        return float(v.u), True
    return 0.0, False


def _read_vector_block(reader):
    """Read a VectorBlock (count, element_size, binary_data). Returns (block_dict, ok)."""
    count, ok = _read_int32(reader)
    if not ok:
        return None, False
    element_size, ok = _read_int32(reader)
    if not ok:
        return None, False

    bytes_val = reader.parse_value()
    if bytes_val.type == MsgPackValue.NIL:
        return {"count": count, "element_size": element_size, "bytes": b""}, True
    if bytes_val.type == MsgPackValue.MAP or bytes_val.type == MsgPackValue.ARRAY:
        return {"count": count, "element_size": element_size, "bytes": b""}, True
    if bytes_val.type != MsgPackValue.BINARY:
        return None, False

    raw_bytes = bytes_val.bin
    # Clamp count to actual available data
    if element_size > 0:
        max_count = len(raw_bytes) // element_size
        if count > max_count:
            count = max_count

    return {"count": count, "element_size": element_size, "bytes": raw_bytes}, True


def _read_float_array(reader, size):
    """Read a msgpack array or binary of floats. Returns (list_of_floats, ok)."""
    v = reader.parse_value()
    if v.type == MsgPackValue.ARRAY:
        if len(v.arr) < size:
            return None, False
        result = []
        for i in range(size):
            elem = v.arr[i]
            if elem.type == MsgPackValue.FLOAT:
                result.append(float(elem.f))
            elif elem.type == MsgPackValue.INT:
                result.append(float(elem.i))
            elif elem.type == MsgPackValue.UINT:
                result.append(float(elem.u))
            else:
                return None, False
        return result, True

    if v.type == MsgPackValue.BINARY:
        if len(v.bin) < size * 4:
            return None, False
        # Read as little-endian floats (CDAE stores native LE on disk)
        result = list(struct.unpack_from(f"<{size}f", v.bin))
        return result, True

    return None, False


def _parse_body(reader):
    """Parse the CDAE body from a MsgPackReader. Returns a shape dict."""
    shape = {
        "unit_meter": 1.0,
        "radius": 0.0,
        "tube_radius": 0.0,
        "center": np.zeros(3, dtype=np.float32),
        "bounds": np.zeros(6, dtype=np.float32),
        "meshes": [],
        "materials": [],
        "nodes": [],
        "objects": [],
        "names": [],
    }

    # Shape info
    shape["smallest_visible_size"] = _read_float(reader)[0]
    shape["smallest_visible_dl"] = _read_int32(reader)[0]
    shape["radius"] = _read_float(reader)[0]
    shape["tube_radius"] = _read_float(reader)[0]

    center, ok = _read_float_array(reader, 3)
    if not ok:
        raise RuntimeError("Failed to read center")
    shape["center"] = np.array(center, dtype=np.float32)

    bounds, ok = _read_float_array(reader, 6)
    if not ok:
        raise RuntimeError("Failed to read bounds")
    shape["bounds"] = np.array(bounds, dtype=np.float32)

    # Vector blocks (19 total)
    vec_blocks = {}
    vec_block_names = [
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
    for name in vec_block_names:
        block, ok = _read_vector_block(reader)
        if not ok:
            raise RuntimeError(f"Failed to read vector block: {name}")
        vec_blocks[name] = block

    # Names
    names_count, ok = _read_int32(reader)
    if not ok:
        raise RuntimeError("Failed to read names count")
    for i in range(names_count):
        name_val = reader.parse_value()
        if name_val.type == MsgPackValue.STRING:
            shape["names"].append(name_val.str)
        else:
            shape["names"].append("")

    # Meshes
    meshes_count, ok = _read_int32(reader)
    if not ok:
        raise RuntimeError("Failed to read meshes count")
    for i in range(meshes_count):
        mesh = {"is_dae": False}
        mesh_type, ok = _read_int32(reader)
        if not ok:
            raise RuntimeError("Failed to read mesh type")
        mesh["mesh_type"] = mesh_type

        if mesh_type == MESH_NULL:
            mesh["is_null"] = True
            mesh["verts_per_frame"] = 0
            shape["meshes"].append(mesh)
            continue

        mesh["is_null"] = False
        mesh["num_frames"] = _read_int32(reader)[0]
        mesh["num_mat_frames"] = _read_int32(reader)[0]
        mesh["parent_mesh"] = _read_int32(reader)[0]

        mesh_bounds, ok = _read_float_array(reader, 6)
        mesh["bounds"] = np.array(mesh_bounds, dtype=np.float32) if mesh_bounds else np.zeros(6, dtype=np.float32)

        mesh_center, ok = _read_float_array(reader, 3)
        mesh["center"] = np.array(mesh_center, dtype=np.float32) if mesh_center else np.zeros(3, dtype=np.float32)

        mesh["radius"] = _read_float(reader)[0]

        # Mesh vector blocks
        mesh_blocks = {}
        mesh_block_names = [
            "verts", "tverts", "tverts2", "colors", "norms",
            "encoded_norms", "primitives", "indices", "tangents",
        ]
        for name in mesh_block_names:
            block, ok = _read_vector_block(reader)
            if not ok:
                raise RuntimeError(f"Failed to read mesh vector block: {name}")
            mesh_blocks[name] = block

        mesh["verts_per_frame"] = _read_int32(reader)[0]
        mesh_flags, ok = _read_int32(reader)
        mesh["mesh_flags"] = mesh_flags

        # Convert vector blocks to numpy arrays in the mesh dict
        _convert_mesh_blocks(mesh, mesh_blocks)
        shape["meshes"].append(mesh)

    # Sequences (skip but consume)
    seq_count, ok = _read_int32(reader)
    if not ok:
        raise RuntimeError("Failed to read sequence count")
    for i in range(seq_count):
        _read_int32(reader)  # nameIndex
        _read_int32(reader)  # flags
        _read_int32(reader)  # numKeyframes
        _read_float(reader)  # duration
        _read_int32(reader)  # priority
        _read_int32(reader)  # firstGroundFrame
        _read_int32(reader)  # numGroundFrames
        _read_int32(reader)  # baseRotation
        _read_int32(reader)  # baseTranslation
        _read_int32(reader)  # baseScale
        _read_int32(reader)  # baseObjectState
        _read_int32(reader)  # baseDecalState
        _read_int32(reader)  # firstTrigger
        _read_int32(reader)  # numTriggers
        _read_float(reader)  # toolBegin
        # Integer sets: each is a msgpack array
        for j in range(6):
            reader.parse_value()

    # Materials
    mat_count, ok = _read_int32(reader)
    if not ok:
        raise RuntimeError("Failed to read material count")

    # PBR flag: detect by checking if next value is Bool vs String
    has_pbr = False
    first_val = reader.parse_value()
    if first_val.type == MsgPackValue.BOOL:
        has_pbr = first_val.b

    for i in range(mat_count):
        mat = {}
        # For old format (no PBR flag), first_val is the first material's name
        if i == 0 and first_val.type != MsgPackValue.BOOL:
            name_val = first_val
        else:
            name_val = reader.parse_value()

        if name_val.type == MsgPackValue.STRING:
            mat["name"] = name_val.str
        else:
            mat["name"] = ""

        mat["flags"] = _read_int32(reader)[0]
        mat["reflectance_map"] = _read_int32(reader)[0]
        mat["bump_map"] = _read_int32(reader)[0]
        mat["detail_map"] = _read_int32(reader)[0]
        mat["detail_scale"] = _read_float(reader)[0]
        mat["reflection_amount"] = _read_float(reader)[0]

        # Default PBR values
        mat["base_color"] = np.array([0.8, 0.8, 0.8, 1.0], dtype=np.float32)
        mat["roughness"] = 0.5
        mat["metallic"] = 0.0
        mat["ior"] = 1.45
        mat["has_shininess"] = False
        mat["has_reflectivity"] = False

        if has_pbr:
            bc, ok = _read_float_array(reader, 4)
            if ok:
                mat["base_color"] = np.array(bc, dtype=np.float32)
            rough, ok = _read_float(reader)
            if ok:
                mat["roughness"] = rough
            metal, ok = _read_float(reader)
            if ok:
                mat["metallic"] = metal

        shape["materials"].append(mat)

    # Convert raw tree (raw_nodes, raw_objects, default_rotations, etc.) to nodes/objects
    _convert_raw_tree(shape, vec_blocks)

    return shape


def _convert_mesh_blocks(mesh, blocks):
    """Convert raw vector blocks to numpy arrays in mesh dict."""
    # Vertices: float32 [N, 3]
    verts = blocks["verts"]
    if verts["count"] > 0 and verts["bytes"]:
        mesh["vertices"] = np.frombuffer(verts["bytes"], dtype=np.float32).reshape(-1, 3)

    # Normals: float32 [N, 3]
    norms = blocks["norms"]
    if norms["count"] > 0 and norms["bytes"]:
        mesh["normals"] = np.frombuffer(norms["bytes"], dtype=np.float32).reshape(-1, 3)

    # UV0: float32 [N, 2]
    tverts = blocks["tverts"]
    if tverts["count"] > 0 and tverts["bytes"]:
        mesh["uv0"] = np.frombuffer(tverts["bytes"], dtype=np.float32).reshape(-1, 2)

    # UV1: float32 [N, 2]
    tverts2 = blocks["tverts2"]
    if tverts2["count"] > 0 and tverts2["bytes"]:
        mesh["uv1"] = np.frombuffer(tverts2["bytes"], dtype=np.float32).reshape(-1, 2)

    # Colors: uint8 [N, 4]
    colors = blocks["colors"]
    if colors["count"] > 0 and colors["bytes"]:
        mesh["colors"] = np.frombuffer(colors["bytes"], dtype=np.uint8).reshape(-1, 4)

    # Encoded norms: uint8 [N]
    encoded_norms = blocks["encoded_norms"]
    if encoded_norms["count"] > 0 and encoded_norms["bytes"]:
        mesh["encoded_norms"] = np.frombuffer(encoded_norms["bytes"], dtype=np.uint8)

    # Primitives: uint32 [N, 3]
    prims = blocks["primitives"]
    if prims["count"] > 0 and prims["bytes"]:
        mesh["primitives"] = np.frombuffer(prims["bytes"], dtype=np.uint32).reshape(-1, 3)

    # Indices: uint32 [N]
    indices = blocks["indices"]
    if indices["count"] > 0 and indices["bytes"]:
        mesh["indices"] = np.frombuffer(indices["bytes"], dtype=np.uint32)

    # Tangents: float32 [N, 4]
    tangents = blocks["tangents"]
    if tangents["count"] > 0 and tangents["bytes"]:
        mesh["tangents"] = np.frombuffer(tangents["bytes"], dtype=np.float32).reshape(-1, 4)


def _convert_raw_tree(shape, vec_blocks):
    """Convert raw_nodes/raw_objects/default_rotations to nodes/objects lists."""
    raw_nodes = vec_blocks["raw_nodes"]
    raw_objects = vec_blocks["raw_objects"]
    default_rotations = vec_blocks["default_rotations"]
    default_translations = vec_blocks["default_translations"]
    node_aligned_scales = vec_blocks["node_aligned_scales"]
    node_uniform_scales = vec_blocks["node_uniform_scales"]

    if not raw_nodes["bytes"] or not raw_objects["bytes"]:
        return

    # Parse nodes: 5 x int32 per node
    node_count = raw_nodes["count"]
    if len(raw_nodes["bytes"]) < node_count * 20:
        node_count = len(raw_nodes["bytes"]) // 20

    node_ptr = np.frombuffer(raw_nodes["bytes"], dtype=np.int32)

    for i in range(node_count):
        name_idx = int(node_ptr[i * 5 + 0])
        node = {
            "name": shape["names"][name_idx] if 0 <= name_idx < len(shape["names"]) else "",
            "parent_index": int(node_ptr[i * 5 + 1]),
            "first_object": int(node_ptr[i * 5 + 2]),
            "first_child": int(node_ptr[i * 5 + 3]),
            "next_sibling": int(node_ptr[i * 5 + 4]),
        }

        # Default rotation: int16 quaternion (x, y, z, w). CDAE w is negated.
        if default_rotations["bytes"] and i < default_rotations["count"]:
            rot = np.frombuffer(default_rotations["bytes"], dtype=np.int16)
            node["quaternion"] = np.array([
                float(rot[i * 4 + 0]) / 32767.0,
                float(rot[i * 4 + 1]) / 32767.0,
                float(rot[i * 4 + 2]) / 32767.0,
                -float(rot[i * 4 + 3]) / 32767.0,
            ], dtype=np.float32)
        else:
            node["quaternion"] = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)

        # Default translation: float3
        if default_translations["bytes"] and i < default_translations["count"]:
            trans = np.frombuffer(default_translations["bytes"], dtype=np.float32)
            node["translation"] = np.array([
                trans[i * 3 + 0],
                trans[i * 3 + 1],
                trans[i * 3 + 2],
            ], dtype=np.float32)
        else:
            node["translation"] = np.zeros(3, dtype=np.float32)

        # Default scale: node_aligned_scales (Vec3F), fallback to node_uniform_scales (float)
        if node_aligned_scales["bytes"] and i < node_aligned_scales["count"]:
            scales = np.frombuffer(node_aligned_scales["bytes"], dtype=np.float32)
            node["scale"] = np.array([
                scales[i * 3 + 0],
                scales[i * 3 + 1],
                scales[i * 3 + 2],
            ], dtype=np.float32)
        elif node_uniform_scales["bytes"] and i < node_uniform_scales["count"]:
            us = np.frombuffer(node_uniform_scales["bytes"], dtype=np.float32)
            node["scale"] = np.array([us[i], us[i], us[i]], dtype=np.float32)
        else:
            node["scale"] = np.array([1.0, 1.0, 1.0], dtype=np.float32)

        shape["nodes"].append(node)

    # Parse objects: 6 x int32 per object
    obj_count = raw_objects["count"]
    if len(raw_objects["bytes"]) < obj_count * 24:
        obj_count = len(raw_objects["bytes"]) // 24

    obj_ptr = np.frombuffer(raw_objects["bytes"], dtype=np.int32)

    for i in range(obj_count):
        name_idx = int(obj_ptr[i * 6 + 0])
        obj = {
            "name_index": name_idx,
            "name": shape["names"][name_idx] if 0 <= name_idx < len(shape["names"]) else "",
            "num_meshes": int(obj_ptr[i * 6 + 1]),
            "start_mesh": int(obj_ptr[i * 6 + 2]),
            "node_index": int(obj_ptr[i * 6 + 3]),
            "next_sibling": int(obj_ptr[i * 6 + 4]),
        }
        shape["objects"].append(obj)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_cdae(filepath):
    """Parse a .cdae file and return a shape dict with numpy arrays."""
    with open(filepath, "rb") as f:
        data = f.read()
    if not data:
        raise RuntimeError("Cannot read .cdae file")
    return _parse_cdae_bytes(data)


def parse_cdae_bytes(data):
    """Parse .cdae from in-memory bytes and return a shape dict."""
    return _parse_cdae_bytes(data)


def _parse_cdae_bytes(data):
    """Internal: parse CDAE binary from bytes."""
    if len(data) < 8:
        raise RuntimeError(".cdae file too small")

    version = _read_u32_le(data, 0)
    header_size = _read_u32_le(data, 4)
    sm_version = version & 0xFF

    if sm_version != 15 and sm_version != 31:
        raise RuntimeError("Unsupported .cdae version")

    if len(data) < 8 + header_size:
        raise RuntimeError(".cdae file truncated")

    # Parse header dict
    header_reader = MsgPackReader(data[8:8 + header_size])
    header = header_reader.parse_value()
    if header.type != MsgPackValue.MAP:
        raise RuntimeError("Invalid .cdae header")

    compression = MsgPackReader.map_get(header, "compression")
    is_compressed = (compression is not None and
                     compression.type == MsgPackValue.BOOL and
                     compression.b)

    # Read body
    body_offset = 8 + header_size
    if body_offset >= len(data):
        raise RuntimeError(".cdae missing body")

    body_data = data[body_offset:]
    body_size = len(body_data)

    if is_compressed:
        body_bytes = _decompress_zstd(body_data)
        if not body_bytes:
            raise RuntimeError("Failed to decompress .cdae body")
    else:
        body_bytes = body_data

    # Parse body
    body_reader = MsgPackReader(body_bytes)
    return _parse_body(body_reader)
