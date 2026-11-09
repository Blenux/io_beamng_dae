"""Pure-Python DAE (Collada XML) parser — replaces dae_import.cpp.

Uses xml.etree.ElementTree instead of pugixml.
Outputs a dict with numpy arrays matching the cdae_native interface.
"""

import math
import struct
import xml.etree.ElementTree as ET

import numpy as np

# Mesh type constants
MESH_NULL = 4
MESH_STANDARD = 0

# Cache for stripped namespace tags — avoids repeated string operations
_ns_cache = {}


def _strip_ns(tag):
    """Remove XML namespace from a tag name (cached)."""
    cached = _ns_cache.get(tag)
    if cached is not None:
        return cached
    # ElementTree format: {http://...}tag
    pos = tag.find("}")
    if pos != -1:
        result = tag[pos + 1:]
    else:
        # pugixml format: prefix:tag
        pos = tag.find(":")
        if pos != -1:
            result = tag[pos + 1:]
        else:
            result = tag
    _ns_cache[tag] = result
    return result


def _child_by_tag(parent, tag):
    """Find first child element with the given (namespace-stripped) tag."""
    if parent is None:
        return None
    for child in parent:
        if _strip_ns(child.tag) == tag:
            return child
    return None


def _children_by_tag(parent, tag):
    """Find all children with the given (namespace-stripped) tag."""
    if parent is None:
        return []
    return [child for child in parent if _strip_ns(child.tag) == tag]


def _parse_float_array_text(text):
    """Parse a whitespace-separated float array from text (C-level numpy parsing)."""
    if not text:
        return np.array([], dtype=np.float32)
    return np.loadtxt([text], dtype=np.float32, ndmin=1)


def _parse_int_array_text(text):
    """Parse a whitespace-separated int array from text (C-level numpy parsing)."""
    if not text:
        return np.array([], dtype=np.int32)
    return np.loadtxt([text], dtype=np.int32, ndmin=1)


def _attr_source_id(node, attr_name):
    """Get a source attribute value, stripping leading '#'."""
    if node is None:
        return ""
    val = node.get(attr_name, "")
    if val.startswith("#"):
        val = val[1:]
    return val


def _safe_atoi(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _safe_atof(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# DAE source data structures
# ---------------------------------------------------------------------------

class _DaeSource:
    __slots__ = ("data", "count", "stride")

    def __init__(self):
        self.data = []
        self.count = 0
        self.stride = 1


class _DaeInput:
    __slots__ = ("semantic", "source", "offset", "set")

    def __init__(self):
        self.semantic = ""
        self.source = ""
        self.offset = 0
        self.set = 0


class _DaeTriangles:
    __slots__ = ("material_name", "triangle_count", "indices", "inputs", "_cached_stride")

    def __init__(self):
        self.material_name = ""
        self.triangle_count = 0
        self.indices = []
        self.inputs = []
        self._cached_stride = None


class _DaeLines:
    __slots__ = ("line_count", "indices", "inputs")

    def __init__(self):
        self.line_count = 0
        self.indices = []
        self.inputs = []


class _DaeGeometry:
    __slots__ = ("id", "name", "sources", "triangles", "lines")

    def __init__(self):
        self.id = ""
        self.name = ""
        self.sources = {}
        self.triangles = []
        self.lines = []


class _DaeNode:
    __slots__ = ("name", "id", "matrix", "geometry_url",
                 "material_bindings", "uv_layer_names", "children")

    def __init__(self):
        self.name = ""
        self.id = ""
        self.matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        self.geometry_url = ""
        self.material_bindings = {}
        self.uv_layer_names = {}
        self.children = []


# ---------------------------------------------------------------------------
# DAE XML parsing
# ---------------------------------------------------------------------------

def _parse_source(src_node):
    src = _DaeSource()
    float_array = _child_by_tag(src_node, "float_array")
    if float_array is not None:
        src.data = _parse_float_array_text(float_array.text)

    tech_common = _child_by_tag(src_node, "technique_common")
    if tech_common is not None:
        accessor = _child_by_tag(tech_common, "accessor")
        if accessor is not None:
            src.count = _safe_atoi(accessor.get("count", "0"))
            stride_attr = accessor.get("stride")
            if stride_attr:
                src.stride = _safe_atoi(stride_attr)
            else:
                src.stride = 1

    if src.count == 0 and len(src.data) > 0:
        src.count = len(src.data) // src.stride

    return src


def _parse_input(input_node):
    inp = _DaeInput()
    inp.semantic = input_node.get("semantic", "")
    src_val = input_node.get("source", "")
    if src_val.startswith("#"):
        src_val = src_val[1:]
    inp.source = src_val
    inp.offset = _safe_atoi(input_node.get("offset", "0"))
    inp.set = _safe_atoi(input_node.get("set", "0"))
    return inp


def _parse_triangles(tri_node):
    result = _DaeTriangles()
    result.material_name = tri_node.get("material", "")
    result.triangle_count = _safe_atoi(tri_node.get("count", "0"))

    p_elem = _child_by_tag(tri_node, "p")
    if p_elem is not None:
        result.indices = _parse_int_array_text(p_elem.text)

    for inp_node in _children_by_tag(tri_node, "input"):
        result.inputs.append(_parse_input(inp_node))

    return result


def _parse_polylist(poly_node):
    result = _DaeTriangles()
    result.material_name = poly_node.get("material", "")
    result.triangle_count = _safe_atoi(poly_node.get("count", "0"))

    for inp_node in _children_by_tag(poly_node, "input"):
        result.inputs.append(_parse_input(inp_node))

    p_elem = _child_by_tag(poly_node, "p")
    vcount_elem = _child_by_tag(poly_node, "vcount")
    if p_elem is None or vcount_elem is None:
        return result

    raw_indices = _parse_int_array_text(p_elem.text)
    vcount = _parse_int_array_text(vcount_elem.text)

    stride = 1
    for inp in result.inputs:
        if inp.offset + 1 > stride:
            stride = inp.offset + 1

    # Fan triangulate
    tri_indices = []
    tri_count = 0
    cursor = 0
    for n in vcount:
        if n < 3:
            cursor += n
            continue
        for i in range(1, n - 1):
            tri_count += 1
            for s in range(stride):
                tri_indices.append(raw_indices[cursor * stride + s])
            for s in range(stride):
                tri_indices.append(raw_indices[(cursor + i) * stride + s])
            for s in range(stride):
                tri_indices.append(raw_indices[(cursor + i + 1) * stride + s])
        cursor += n

    result.indices = tri_indices
    result.triangle_count = tri_count
    return result


def _parse_lines(lines_node):
    result = _DaeLines()
    result.line_count = _safe_atoi(lines_node.get("count", "0"))

    p_elem = _child_by_tag(lines_node, "p")
    if p_elem is not None:
        result.indices = _parse_int_array_text(p_elem.text)

    for inp_node in _children_by_tag(lines_node, "input"):
        result.inputs.append(_parse_input(inp_node))

    return result


def _parse_geometry(geo_node):
    geo = _DaeGeometry()
    geo.id = geo_node.get("id", "")
    geo.name = geo_node.get("name", "")

    mesh = _child_by_tag(geo_node, "mesh")
    if mesh is None:
        return geo

    for src_node in _children_by_tag(mesh, "source"):
        src_id = src_node.get("id")
        if src_id:
            geo.sources[src_id] = _parse_source(src_node)

    vertices = _child_by_tag(mesh, "vertices")
    if vertices is not None:
        vert_id = vertices.get("id")
        vert_input = _child_by_tag(vertices, "input")
        if vert_id and vert_input is not None:
            vert_key = vert_id
            pos_src = _attr_source_id(vert_input, "source")
            if pos_src in geo.sources:
                geo.sources[vert_key] = geo.sources[pos_src]

    for tri_node in _children_by_tag(mesh, "triangles"):
        geo.triangles.append(_parse_triangles(tri_node))
    for poly_node in _children_by_tag(mesh, "polylist"):
        geo.triangles.append(_parse_polylist(poly_node))
    for lines_node in _children_by_tag(mesh, "lines"):
        geo.lines.append(_parse_lines(lines_node))

    return geo


def _parse_node(node_elem):
    node = _DaeNode()
    node.name = node_elem.get("name", "")
    node.id = node_elem.get("id", "")

    matrix_elem = _child_by_tag(node_elem, "matrix")
    if matrix_elem is not None:
        mat_vals = _parse_float_array_text(matrix_elem.text)
        if len(mat_vals) >= 16:
            node.matrix = mat_vals[:16]

    inst_geo = _child_by_tag(node_elem, "instance_geometry")
    if inst_geo is not None:
        node.geometry_url = _attr_source_id(inst_geo, "url")

        bind_mat = _child_by_tag(inst_geo, "bind_material")
        if bind_mat is not None:
            tech_common = _child_by_tag(bind_mat, "technique_common")
            if tech_common is not None:
                for inst_mat in _children_by_tag(tech_common, "instance_material"):
                    symbol = inst_mat.get("symbol")
                    target = inst_mat.get("target")
                    if symbol and target:
                        if target.startswith("#"):
                            target = target[1:]
                        node.material_bindings[symbol] = target

                        # Parse bind_vertex_input for UV layer names
                        for bvi in _children_by_tag(inst_mat, "bind_vertex_input"):
                            bvi_sem = bvi.get("semantic")
                            bvi_isem = bvi.get("input_semantic")
                            bvi_set = bvi.get("input_set")
                            if bvi_sem and bvi_isem and bvi_set:
                                if bvi_isem == "TEXCOORD":
                                    iset = _safe_atoi(bvi_set)
                                    node.uv_layer_names[iset] = bvi_sem

    for child_elem in _children_by_tag(node_elem, "node"):
        node.children.append(_parse_node(child_elem))

    return node


# ---------------------------------------------------------------------------
# DAE → Mesh conversion
# ---------------------------------------------------------------------------

def _get_stride(tri):
    max_offset = 0
    for inp in tri.inputs:
        if inp.offset > max_offset:
            max_offset = inp.offset
    return max_offset + 1


def _find_input(inputs, semantic, set_num=0):
    for inp in inputs:
        if inp.semantic == semantic and inp.set == set_num:
            return inp
    return None


def _get_indexed_array(tri, geo, semantic, set_num, components):
    """Get indexed array from DAE triangle data. Returns (np.ndarray, bool) or (None, False)."""
    inp = _find_input(tri.inputs, semantic, set_num)
    if inp is None:
        return None, False

    src = geo.sources.get(inp.source)
    if src is None:
        return None, False

    # Cache stride on the tri object to avoid recomputing
    stride = getattr(tri, '_cached_stride', None)
    if stride is None:
        stride = _get_stride(tri)
        tri._cached_stride = stride

    total_corners = tri.triangle_count * 3
    if total_corners == 0:
        return np.zeros((0, components), dtype=np.float32), True

    # Extract indices at this input's offset — fast path for stride=1
    idx_arr = tri.indices
    if isinstance(idx_arr, np.ndarray):
        idx_arr = idx_arr
    else:
        idx_arr = np.asarray(idx_arr, dtype=np.int32)

    if stride == 1:
        # Fast path: indices are already contiguous
        corner_idx = idx_arr[:total_corners].astype(np.int32)
    else:
        offsets = np.arange(inp.offset, total_corners * stride, stride)
        corner_idx = np.zeros(total_corners, dtype=np.int32)
        valid = offsets < len(idx_arr)
        corner_idx[valid] = idx_arr[offsets[valid]]

    # Index into source data via numpy fancy indexing
    src_arr = src.data
    if not isinstance(src_arr, np.ndarray):
        src_arr = np.asarray(src_arr, dtype=np.float32)
    if src_arr.ndim == 1:
        src_arr = src_arr.reshape(-1, src.stride)

    valid_idx = (corner_idx >= 0) & (corner_idx < src_arr.shape[0])
    result = np.zeros((total_corners, components), dtype=np.float32)
    if components <= src_arr.shape[1]:
        result[valid_idx] = src_arr[corner_idx[valid_idx], :components]
    else:
        result[valid_idx, :src_arr.shape[1]] = src_arr[corner_idx[valid_idx]]

    return result, True


def _convert_geometry(geo, mat_name_to_idx, mat_id_to_idx, unit_meter):
    """Convert a DaeGeometry to a mesh dict matching the cdae_native interface."""
    mesh = {
        "is_dae": True,
        "mesh_type": MESH_STANDARD,
        "is_null": False,
    }

    total_triangles = sum(t.triangle_count for t in geo.triangles)

    if total_triangles == 0 and not geo.lines:
        mesh["mesh_type"] = MESH_NULL
        mesh["is_null"] = True
        return mesh

    total_corners = total_triangles * 3 if total_triangles > 0 else 0

    dst_verts = [0.0] * (total_corners * 3)
    dst_norms = [0.0] * (total_corners * 3)
    dst_tverts0 = [0.0] * (total_corners * 2)
    dst_tverts1 = [0.0] * (total_corners * 2)
    dst_tverts_extra = []
    extra_uv_sets = []
    dst_colors = [255] * (total_corners * 4)
    dst_color_layers = []
    color_layer_names = []
    color_layer_sets = []
    dst_indices = [0] * total_corners
    dst_primitives = []

    # Discover extra TEXCOORD sets (set 2+) from the first triangle group
    for tri in geo.triangles:
        for inp in tri.inputs:
            if inp.semantic == "TEXCOORD" and inp.set >= 2:
                if inp.set not in extra_uv_sets:
                    extra_uv_sets.append(inp.set)
                    dst_tverts_extra.append([0.0] * (total_corners * 2))
        if extra_uv_sets:
            break

    has_norms = False
    has_tverts0 = False
    has_tverts1 = False
    has_colors = False
    has_color_layers = False
    num_color_layers = 0

    corner_offset = 0

    # Discover all COLOR input sets from the first triangle group
    for tri in geo.triangles:
        for inp in tri.inputs:
            if inp.semantic == "COLOR":
                lname = inp.source
                pos = lname.find("-colors-")
                if pos != -1:
                    lname = lname[pos + 8:]
                else:
                    lname = "Col"
                    if inp.set > 0:
                        lname += str(inp.set)
                color_layer_names.append(lname)
                color_layer_sets.append(inp.set)
                dst_color_layers.append([255] * (total_corners * 4))
                num_color_layers += 1
        if num_color_layers > 0:
            break
    if num_color_layers > 0:
        has_color_layers = True

    # Pre-allocate numpy arrays for vectorized copy
    dst_verts_arr = np.zeros((total_corners, 3), dtype=np.float32)
    dst_norms_arr = np.zeros((total_corners, 3), dtype=np.float32)
    dst_tverts0_arr = np.zeros((total_corners, 2), dtype=np.float32)
    dst_tverts1_arr = np.zeros((total_corners, 2), dtype=np.float32)
    dst_colors_arr = np.full((total_corners, 4), 255, dtype=np.uint8)
    dst_tverts_extra_arrs = [np.zeros((total_corners, 2), dtype=np.float32) for _ in extra_uv_sets]
    dst_color_layer_arrs = [np.full((total_corners, 4), 255, dtype=np.uint8) for _ in range(num_color_layers)]

    for tri in geo.triangles:
        mat_idx = 0
        mat_name = tri.material_name
        if mat_name in mat_name_to_idx:
            mat_idx = mat_name_to_idx[mat_name]
        elif mat_name in mat_id_to_idx:
            mat_idx = mat_id_to_idx[mat_name]

        tri_count = tri.triangle_count
        vtx_count = tri_count * 3

        src_verts, has_v = _get_indexed_array(tri, geo, "VERTEX", 0, 3)
        src_norms, has_n = _get_indexed_array(tri, geo, "NORMAL", 0, 3)
        src_tverts0, has_t0 = _get_indexed_array(tri, geo, "TEXCOORD", 0, 2)
        src_tverts1, has_t1 = _get_indexed_array(tri, geo, "TEXCOORD", 1, 2)
        src_colors, has_c = _get_indexed_array(tri, geo, "COLOR", 0, 4)
        src_color_layers = []
        if has_color_layers:
            for li in range(num_color_layers):
                scl, has_scl = _get_indexed_array(tri, geo, "COLOR", color_layer_sets[li], 4)
                src_color_layers.append(scl if has_scl else None)

        next_offset = corner_offset + vtx_count
        sl = slice(corner_offset, next_offset)

        # Vectorized copy with numpy (no list roundtrip)
        if has_v and src_verts is not None:
            dst_verts_arr[sl] = src_verts * unit_meter

        if has_n and src_norms is not None:
            has_norms = True
            dst_norms_arr[sl] = src_norms

        if has_t0 and src_tverts0 is not None:
            has_tverts0 = True
            dst_tverts0_arr[sl] = src_tverts0

        if has_t1 and src_tverts1 is not None:
            has_tverts1 = True
            dst_tverts1_arr[sl] = src_tverts1

        # Parse extra UV layers (set 2+) — vectorized
        for ei in range(len(extra_uv_sets)):
            src_extra, has_e = _get_indexed_array(tri, geo, "TEXCOORD", extra_uv_sets[ei], 2)
            if has_e and src_extra is not None:
                dst_tverts_extra_arrs[ei][sl] = src_extra

        # Color conversion — vectorized clip + scale
        if has_c and src_colors is not None:
            has_colors = True
            dst_colors_arr[sl] = (np.clip(src_colors, 0.0, 1.0) * 255.0).astype(np.uint8)

        if has_color_layers:
            for li in range(num_color_layers):
                if src_color_layers[li] is not None:
                    has_colors = True
                    dst_color_layer_arrs[li][sl] = (np.clip(src_color_layers[li], 0.0, 1.0) * 255.0).astype(np.uint8)

        index_start = corner_offset
        index_count = vtx_count
        raw_info = mat_idx & 0x0FFFFFFF
        dst_primitives.append(index_start)
        dst_primitives.append(index_count)
        dst_primitives.append(raw_info)

        corner_offset = next_offset

    # Build indices: pre-reversed per triangle (2,1,0, 5,4,3, ...) — vectorized
    dst_indices = np.arange(total_corners, dtype=np.uint32).reshape(-1, 3)[:, ::-1].flatten()

    # Fill mesh dict with numpy arrays
    if total_corners > 0:
        mesh["vertices"] = dst_verts_arr
        if has_norms:
            mesh["normals"] = dst_norms_arr
        if has_tverts0:
            mesh["uv0"] = dst_tverts0_arr
        if has_tverts1:
            mesh["uv1"] = dst_tverts1_arr
        if dst_tverts_extra_arrs:
            mesh["uv_extra"] = dst_tverts_extra_arrs
        if has_colors:
            mesh["colors"] = dst_colors_arr
        if has_color_layers:
            mesh["color_layers"] = dst_color_layer_arrs
            mesh["color_layer_names"] = color_layer_names
        mesh["indices"] = dst_indices
        prim_count = len(dst_primitives) // 3
        if prim_count > 0:
            mesh["primitives"] = np.array(dst_primitives, dtype=np.uint32).reshape(-1, 3)

    # Fill line indices from <lines> elements
    if geo.lines:
        # Find the position source via the <vertices> element
        for tri in geo.triangles:
            for inp in tri.inputs:
                if inp.semantic == "VERTEX":
                    vkey = inp.source
                    src = geo.sources.get(vkey)
                    if src is not None:
                        mesh["line_verts"] = np.array(src.data, dtype=np.float32).reshape(-1, 3)
                    break
            break

        all_line_indices = []
        for lines in geo.lines:
            vtx_offset = 0
            for inp in lines.inputs:
                if inp.semantic == "VERTEX":
                    vtx_offset = inp.offset
                    break
            stride = 1
            for inp in lines.inputs:
                if inp.offset + 1 > stride:
                    stride = inp.offset + 1
            idx = np.asarray(lines.indices)
            base = np.arange(lines.line_count, dtype=np.int64) * 2 * stride + vtx_offset
            base = base[base + stride < len(idx)]
            if len(base):
                pairs = np.stack([idx[base], idx[base + stride]], axis=1)
                all_line_indices.extend(pairs.ravel().tolist())
        if all_line_indices:
            mesh["line_indices"] = np.array(all_line_indices, dtype=np.int32).reshape(-1, 2)

    mesh["verts_per_frame"] = total_corners

    return mesh


# ---------------------------------------------------------------------------
# Matrix decomposition
# ---------------------------------------------------------------------------

def _mat3_to_quat(m):
    """Convert a 3x3 rotation matrix to quaternion (w, x, y, z).

    Uses Shepperd's method, matching the C++ implementation.
    """
    trace = m[0][0] + m[1][1] + m[2][2]

    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (m[2][1] - m[1][2]) / s
        qy = (m[0][2] - m[2][0]) / s
        qz = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        qw = (m[2][1] - m[1][2]) / s
        qx = 0.25 * s
        qy = (m[0][1] + m[1][0]) / s
        qz = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        qw = (m[0][2] - m[2][0]) / s
        qx = (m[0][1] + m[1][0]) / s
        qy = 0.25 * s
        qz = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        qw = (m[1][0] - m[0][1]) / s
        qx = (m[0][2] + m[2][0]) / s
        qy = (m[1][2] + m[2][1]) / s
        qz = 0.25 * s

    return (qw, qx, qy, qz)


def _decompose_matrix(m):
    """Decompose a 4x4 Collada matrix (column-major) into quaternion (x,y,z,w), translation, scale."""
    trans = (m[3], m[7], m[11])

    mat3 = [
        [m[0], m[1], m[2]],
        [m[4], m[5], m[6]],
        [m[8], m[9], m[10]],
    ]

    # Extract scale as column lengths
    scale = [
        math.sqrt(m[0] ** 2 + m[4] ** 2 + m[8] ** 2),
        math.sqrt(m[1] ** 2 + m[5] ** 2 + m[9] ** 2),
        math.sqrt(m[2] ** 2 + m[6] ** 2 + m[10] ** 2),
    ]
    if scale[0] <= 0.0:
        scale[0] = 1.0
    if scale[1] <= 0.0:
        scale[1] = 1.0
    if scale[2] <= 0.0:
        scale[2] = 1.0

    # Detect negative scale via determinant; negate Z if det < 0
    det = (mat3[0][0] * (mat3[1][1] * mat3[2][2] - mat3[1][2] * mat3[2][1])
           - mat3[0][1] * (mat3[1][0] * mat3[2][2] - mat3[1][2] * mat3[2][0])
           + mat3[0][2] * (mat3[1][0] * mat3[2][1] - mat3[1][1] * mat3[2][0]))
    if det < 0.0:
        scale[2] = -scale[2]

    # Divide mat3 by signed scale to get pure rotation matrix
    inv_sx = 1.0 / scale[0]
    inv_sy = 1.0 / scale[1]
    inv_sz = 1.0 / scale[2]
    mat3[0][0] *= inv_sx; mat3[1][0] *= inv_sx; mat3[2][0] *= inv_sx
    mat3[0][1] *= inv_sy; mat3[1][1] *= inv_sy; mat3[2][1] *= inv_sy
    mat3[0][2] *= inv_sz; mat3[1][2] *= inv_sz; mat3[2][2] *= inv_sz

    qw, qx, qy, qz = _mat3_to_quat(mat3)

    # Return as (x, y, z, w) for CDAE convention
    quat = (qx, qy, qz, qw)
    return quat, trans, scale


# ---------------------------------------------------------------------------
# Node hierarchy flattening
# ---------------------------------------------------------------------------

class _NodeBuildState:
    def __init__(self):
        self.names = []
        self.node_raws = []
        self.object_raws = []
        self.default_rots = []
        self.default_trans = []
        self.default_scales = []
        self.geometry_name_to_mesh_start = {}


def _process_node(dae_node, parent_idx, geometries, state, shape_meshes):
    node_idx = len(state.node_raws) // 5

    name_idx = len(state.names)
    state.names.append(dae_node.name if dae_node.name else dae_node.id)

    quat, trans, scale = _decompose_matrix(dae_node.matrix)

    # Store rotation as float quaternion in CDAE convention: (x, y, z, w)
    state.default_rots.extend(quat)
    state.default_trans.extend(trans)
    state.default_scales.extend(scale)

    node_slot = len(state.node_raws)
    state.node_raws.extend([name_idx, parent_idx, -1, -1, -1])

    first_obj = -1
    if dae_node.geometry_url:
        geo = geometries.get(dae_node.geometry_url)
        if geo is not None:
            mesh_start = state.geometry_name_to_mesh_start.get(dae_node.geometry_url, 0)
            mesh_count = 1

            # Apply UV layer names from bind_vertex_input to the mesh
            if 0 <= mesh_start < len(shape_meshes):
                mesh_ref = shape_meshes[mesh_start]
                if mesh_ref.get("is_null", True):
                    pass  # null mesh, no UV names
                else:
                    max_uv_set = 0
                    for uv_set in dae_node.uv_layer_names:
                        if uv_set > max_uv_set:
                            max_uv_set = uv_set
                    tvert_names = ["UVMap"] * (max_uv_set + 1)
                    for uv_set, uv_name in dae_node.uv_layer_names.items():
                        if 0 <= uv_set < len(tvert_names):
                            tvert_names[uv_set] = uv_name
                    mesh_ref["tvert_names"] = tvert_names

            obj_name_idx = name_idx
            obj_slot = len(state.object_raws)
            state.object_raws.extend([obj_name_idx, mesh_count, mesh_start, node_idx, -1, -1])
            first_obj = len(state.object_raws) // 6 - 1

    state.node_raws[node_slot + 2] = first_obj

    first_child = -1
    prev_child = -1
    for child in dae_node.children:
        child_idx = _process_node(child, node_idx, geometries, state, shape_meshes)
        if first_child == -1:
            first_child = child_idx
        if prev_child != -1:
            state.node_raws[prev_child * 5 + 4] = child_idx
        prev_child = child_idx

    state.node_raws[node_slot + 3] = first_child

    return node_idx


# ---------------------------------------------------------------------------
# Main DAE reader
# ---------------------------------------------------------------------------

def _dae_read_from_bytes(data):
    """Parse DAE XML bytes and return a shape dict with numpy arrays."""
    _ns_cache.clear()
    root = ET.fromstring(data)
    if _strip_ns(root.tag) != "COLLADA":
        raise ValueError("XML root is not <COLLADA>")

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

    # Parse <asset> for unit meter
    unit_meter = 1.0
    asset = _child_by_tag(root, "asset")
    if asset is not None:
        unit = _child_by_tag(asset, "unit")
        if unit is not None:
            meter = unit.get("meter")
            if meter:
                unit_meter = _safe_atof(meter)
                if unit_meter <= 0.0:
                    unit_meter = 1.0
    shape["unit_meter"] = unit_meter

    # Parse <library_effects> for PBR properties
    class _EffectPBR:
        def __init__(self):
            self.base_color = [0.8, 0.8, 0.8, 1.0]
            self.roughness = 0.5
            self.metallic = 0.0
            self.ior = 1.45
            self.has_shininess = False
            self.has_reflectivity = False

    effect_pbr_map = {}

    eff_lib = _child_by_tag(root, "library_effects")
    if eff_lib is not None:
        for eff in _children_by_tag(eff_lib, "effect"):
            eff_id = eff.get("id")
            if not eff_id:
                continue

            pbr = _EffectPBR()
            profile = _child_by_tag(eff, "profile_COMMON")
            if profile is not None:
                tech = _child_by_tag(profile, "technique")
                if tech is not None:
                    shader = _child_by_tag(tech, "lambert")
                    if shader is None:
                        shader = _child_by_tag(tech, "phong")
                    if shader is None:
                        shader = _child_by_tag(tech, "blinn")

                    if shader is not None:
                        # Parse <diffuse><color> → base_color
                        diffuse = _child_by_tag(shader, "diffuse")
                        if diffuse is not None:
                            color = _child_by_tag(diffuse, "color")
                            if color is not None:
                                c = _parse_float_array_text(color.text)
                                if len(c) >= 3:
                                    pbr.base_color[0] = c[0]
                                    pbr.base_color[1] = c[1]
                                    pbr.base_color[2] = c[2]
                                    pbr.base_color[3] = c[3] if len(c) >= 4 else 1.0

                        # Parse <reflectivity><float> → metallic
                        refl = _child_by_tag(shader, "reflectivity")
                        if refl is not None:
                            f = _child_by_tag(refl, "float")
                            if f is not None:
                                pbr.metallic = _safe_atof(f.text)
                                pbr.has_reflectivity = True

                        # Parse <shininess><float> → roughness (invert)
                        shin = _child_by_tag(shader, "shininess")
                        if shin is not None:
                            f = _child_by_tag(shin, "float")
                            if f is not None:
                                sh = _safe_atof(f.text)
                                pbr.roughness = max(0.0, min(1.0, 1.0 - sh / 1000.0))
                                pbr.has_shininess = True

                        # Parse <transparent><color> → alpha override
                        trans = _child_by_tag(shader, "transparent")
                        if trans is not None:
                            color = _child_by_tag(trans, "color")
                            if color is not None:
                                c = _parse_float_array_text(color.text)
                                if len(c) >= 4:
                                    opaque = trans.get("opaque")
                                    if opaque == "A_ONE":
                                        pbr.base_color[3] = 1.0 - c[3]
                                    else:
                                        pbr.base_color[3] = c[3]

                        # Parse <index_of_refraction><float> → IOR
                        ior_node = _child_by_tag(shader, "index_of_refraction")
                        if ior_node is not None:
                            f = _child_by_tag(ior_node, "float")
                            if f is not None:
                                pbr.ior = _safe_atof(f.text)

            effect_pbr_map[eff_id] = pbr

    # Parse <library_materials>
    mat_name_to_idx = {}
    mat_id_to_idx = {}

    mat_lib = _child_by_tag(root, "library_materials")
    if mat_lib is not None:
        for mat_elem in _children_by_tag(mat_lib, "material"):
            mat = {}
            mat["name"] = mat_elem.get("name", "") or mat_elem.get("id", "")
            mat["flags"] = 3
            mat["base_color"] = np.array([0.8, 0.8, 0.8, 1.0], dtype=np.float32)
            mat["roughness"] = 0.5
            mat["metallic"] = 0.0
            mat["ior"] = 1.45
            mat["has_shininess"] = False
            mat["has_reflectivity"] = False

            # Link material to effect for PBR properties
            inst_eff = _child_by_tag(mat_elem, "instance_effect")
            if inst_eff is not None:
                eff_url = _attr_source_id(inst_eff, "url")
                pbr = effect_pbr_map.get(eff_url)
                if pbr is not None:
                    mat["base_color"] = np.array(pbr.base_color, dtype=np.float32)
                    mat["roughness"] = pbr.roughness
                    mat["metallic"] = pbr.metallic
                    mat["ior"] = pbr.ior
                    mat["has_shininess"] = pbr.has_shininess
                    mat["has_reflectivity"] = pbr.has_reflectivity

            mat_name_to_idx[mat["name"]] = len(shape["materials"])
            id_attr = mat_elem.get("id")
            if id_attr:
                mat_id_to_idx[id_attr] = len(shape["materials"])
            shape["materials"].append(mat)

    # Parse <library_geometries>
    geometries = {}
    geo_lib = _child_by_tag(root, "library_geometries")
    if geo_lib is not None:
        for geo_elem in _children_by_tag(geo_lib, "geometry"):
            parsed = _parse_geometry(geo_elem)
            geometries[parsed.id] = parsed

    # Parse <library_visual_scenes>
    dae_nodes = []
    scene_lib = _child_by_tag(root, "library_visual_scenes")
    if scene_lib is not None:
        visual_scene = _child_by_tag(scene_lib, "visual_scene")
        if visual_scene is not None:
            for node_elem in _children_by_tag(visual_scene, "node"):
                dae_nodes.append(_parse_node(node_elem))

    # Convert geometries to meshes
    state = _NodeBuildState()

    for geo_id, geo in geometries.items():
        mesh_start = len(shape["meshes"])
        state.geometry_name_to_mesh_start[geo_id] = mesh_start

        has_valid_triangles = any(t.triangle_count > 0 for t in geo.triangles)

        if has_valid_triangles:
            cmesh = _convert_geometry(geo, mat_name_to_idx, mat_id_to_idx, unit_meter)
            cmesh["geometry_name"] = geo.name
            shape["meshes"].append(cmesh)
        else:
            null_mesh = {
                "is_dae": True,
                "mesh_type": MESH_NULL,
                "is_null": True,
                "verts_per_frame": 0,
                "geometry_name": geo.name,
            }
            # Check for line data on null meshes
            if geo.lines:
                # Find position source via first triangle's VERTEX input
                for tri in geo.triangles:
                    for inp in tri.inputs:
                        if inp.semantic == "VERTEX":
                            src = geo.sources.get(inp.source)
                            if src is not None:
                                null_mesh["line_verts"] = np.array(src.data, dtype=np.float32).reshape(-1, 3)
                            break
                    break

                all_line_indices = []
                for lines in geo.lines:
                    vtx_offset = 0
                    for inp in lines.inputs:
                        if inp.semantic == "VERTEX":
                            vtx_offset = inp.offset
                            break
                    stride = 1
                    for inp in lines.inputs:
                        if inp.offset + 1 > stride:
                            stride = inp.offset + 1
                    for i in range(lines.line_count):
                        if (i * 2 + 1) * stride > len(lines.indices):
                            break
                        v0 = lines.indices[i * 2 * stride + vtx_offset]
                        v1 = lines.indices[(i * 2 + 1) * stride + vtx_offset]
                        all_line_indices.append(v0)
                        all_line_indices.append(v1)
                if all_line_indices:
                    null_mesh["line_indices"] = np.array(all_line_indices, dtype=np.int32).reshape(-1, 2)

            shape["meshes"].append(null_mesh)

    # Build node/object hierarchy
    prev_sibling = -1
    for dae_node in dae_nodes:
        node_idx = _process_node(dae_node, -1, geometries, state, shape["meshes"])
        if prev_sibling != -1:
            state.node_raws[prev_sibling * 5 + 4] = node_idx
        prev_sibling = node_idx

    # Fill names
    shape["names"] = state.names

    # Fill nodes
    total_nodes = len(state.node_raws) // 5
    for i in range(total_nodes):
        name_idx = state.node_raws[i * 5 + 0]
        node = {
            "name": shape["names"][name_idx] if 0 <= name_idx < len(shape["names"]) else "node",
            "parent_index": state.node_raws[i * 5 + 1],
            "first_object": state.node_raws[i * 5 + 2],
            "first_child": state.node_raws[i * 5 + 3],
            "next_sibling": state.node_raws[i * 5 + 4],
            "quaternion": np.array([
                state.default_rots[i * 4 + 0],
                state.default_rots[i * 4 + 1],
                state.default_rots[i * 4 + 2],
                state.default_rots[i * 4 + 3],
            ], dtype=np.float32) if i * 4 + 3 < len(state.default_rots) else np.array([0, 0, 0, 1], dtype=np.float32),
            "translation": np.array([
                state.default_trans[i * 3 + 0],
                state.default_trans[i * 3 + 1],
                state.default_trans[i * 3 + 2],
            ], dtype=np.float32) if i * 3 + 2 < len(state.default_trans) else np.zeros(3, dtype=np.float32),
            "scale": np.array([
                state.default_scales[i * 3 + 0],
                state.default_scales[i * 3 + 1],
                state.default_scales[i * 3 + 2],
            ], dtype=np.float32) if i * 3 + 2 < len(state.default_scales) else np.array([1, 1, 1], dtype=np.float32),
        }
        shape["nodes"].append(node)

    # Fill objects
    total_objects = len(state.object_raws) // 6
    for i in range(total_objects):
        obj = {
            "name_index": state.object_raws[i * 6 + 0],
            "name": shape["names"][state.object_raws[i * 6 + 0]]
                if 0 <= state.object_raws[i * 6 + 0] < len(shape["names"]) else "",
            "num_meshes": state.object_raws[i * 6 + 1],
            "start_mesh": state.object_raws[i * 6 + 2],
            "node_index": state.object_raws[i * 6 + 3],
            "next_sibling": state.object_raws[i * 6 + 4],
        }
        shape["objects"].append(obj)

    return shape


def parse_dae(filepath):
    """Parse a .dae file and return a shape dict with numpy arrays."""
    with open(filepath, "rb") as f:
        data = f.read()
    return _dae_read_from_bytes(data)


def parse_dae_bytes(data):
    """Parse .dae from in-memory bytes and return a shape dict."""
    return _dae_read_from_bytes(data)
