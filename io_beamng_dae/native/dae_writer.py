"""Pure-Python DAE (Collada XML) writer — replaces dae_export.cpp.

Uses xml.etree.ElementTree instead of pugixml.
Writes: asset, library_effects, library_materials, library_geometries, library_visual_scenes, scene.
"""

import datetime
import xml.etree.ElementTree as ET

import numpy as np

MESH_NULL = 4
MESH_STANDARD = 0


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_float(v):
    """Format a float with %.6g precision, trimming trailing zeros."""
    return f"{v:.6g}"


def _fmt_float_nor(v):
    """Lower precision for normals (%.5g)."""
    return f"{v:.5g}"


def _fmt_floats(data, count, is_normal=False):
    """Format floats as space-separated string (%.6g; %.5g for normals)."""
    vals = data[:count]
    if isinstance(vals, np.ndarray):
        vals = vals.tolist()
    if not vals:
        return ""
    return " ".join(map("{:.5g}".format if is_normal else "{:.6g}".format, vals))


def _fmt_uints(data, count):
    """Format uints as space-separated string."""
    vals = data[:count]
    if isinstance(vals, np.ndarray):
        vals = vals.tolist()
    if not vals:
        return ""
    return " ".join(map(str, vals))


def _sanitize_id(s):
    """Replace dots with underscores for XML ID safety."""
    return s.replace(".", "_")


# ---------------------------------------------------------------------------
# Quaternion → Collada matrix
# ---------------------------------------------------------------------------

def _quat_trans_scale_to_collada_matrix(q, trans, scale):
    """Build a 16-element column-major Collada matrix from quaternion (x,y,z,w), translation, scale."""
    xx = q[0] * q[0]
    yy = q[1] * q[1]
    zz = q[2] * q[2]
    xy = q[0] * q[1]
    xz = q[0] * q[2]
    yz = q[1] * q[2]
    wx = q[3] * q[0]
    wy = q[3] * q[1]
    wz = q[3] * q[2]

    # Rotation matrix rows with scale applied
    mat = [
        [(1.0 - 2.0 * (yy + zz)) * scale[0], 2.0 * (xy + wz) * scale[0], 2.0 * (xz - wy) * scale[0], 0.0],
        [2.0 * (xy - wz) * scale[1], (1.0 - 2.0 * (xx + zz)) * scale[1], 2.0 * (yz + wx) * scale[1], 0.0],
        [2.0 * (xz + wy) * scale[2], 2.0 * (yz - wx) * scale[2], (1.0 - 2.0 * (xx + yy)) * scale[2], 0.0],
        [trans[0], trans[1], trans[2], 1.0],
    ]

    # Transpose to Collada column-major order
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = mat[row][col]

    return out


# ---------------------------------------------------------------------------
# Source writing
# ---------------------------------------------------------------------------

_PN = {1: ["X"], 2: ["X", "Y"], 3: ["X", "Y", "Z"], 4: ["X", "Y", "Z", "W"]}
_UV_PARAMS = ["S", "T"]
_COLOR_PARAMS = ["R", "G", "B", "A"]


def _get_pn(s):
    return _PN.get(s, _PN[4])


def _write_source(parent, sid, data, count, stride, is_normal=False, param_names=None):
    """Write a <source> element with float_array and accessor."""
    src = ET.SubElement(parent, "source")
    src.set("id", sid)

    aid = sid + "_array"
    total = count * stride

    fa = ET.SubElement(src, "float_array")
    fa.set("id", aid)
    fa.set("count", str(total))
    fa.text = _fmt_floats(data, total, is_normal)

    tc = ET.SubElement(src, "technique_common")
    acc = ET.SubElement(tc, "accessor")
    acc.set("source", "#" + aid)
    acc.set("count", str(count))
    acc.set("stride", str(stride))

    params = param_names if param_names else _get_pn(stride)
    for i in range(stride):
        p = ET.SubElement(acc, "param")
        p.set("name", params[i])
        p.set("type", "float")


# ---------------------------------------------------------------------------
# Float array dedup
# ---------------------------------------------------------------------------

def _dedup_float(data, stride, corner_count):
    """Deduplicate float arrays using numpy unique (byte-level comparison)."""
    if corner_count == 0:
        return [], []
    arr = np.asarray(data[:corner_count * stride], dtype=np.float32).reshape(-1, stride)
    arr_c = np.ascontiguousarray(arr)
    # Use void view for fast byte-level hashing (matches C++ behavior)
    void = arr_c.view(np.dtype((np.void, arr_c.dtype.itemsize * stride)))
    uniq_void, idx = np.unique(void, return_inverse=True)
    uniq_arr = uniq_void.view(arr.dtype).reshape(-1, stride)
    return uniq_arr.flatten().tolist(), idx.astype(np.int32).tolist()


def _dedup_float_np(arr, corner_count):
    """Like _dedup_float but accepts a numpy array and returns numpy arrays."""
    if corner_count == 0:
        return np.empty((0, arr.shape[1]), dtype=np.float32), np.array([], dtype=np.int32)
    arr_c = np.ascontiguousarray(arr)
    stride = arr.shape[1]
    void = arr_c.view(np.dtype((np.void, arr_c.dtype.itemsize * stride)))
    uniq_void, idx = np.unique(void, return_inverse=True)
    uniq_arr = uniq_void.view(arr.dtype).reshape(-1, stride)
    return uniq_arr, idx.astype(np.int32)


# ---------------------------------------------------------------------------
# Geometry writing
# ---------------------------------------------------------------------------

def _write_geometry(lib_geoms, mesh_dict, mesh_idx, mesh_name, shape_materials):
    """Write a <geometry> element and return list of material names used."""
    mat_names = []
    gid = _sanitize_id(mesh_name) + "-mesh"

    geom = ET.SubElement(lib_geoms, "geometry")
    geom.set("id", gid)
    geom.set("name", mesh_name)
    me = ET.SubElement(geom, "mesh")

    is_null = mesh_dict.get("is_null", False)
    mesh_type = mesh_dict.get("mesh_type", MESH_STANDARD)

    # Empty meshes
    if is_null or mesh_type == MESH_NULL:
        pos_id = gid + "-positions"
        norm_id = gid + "-normals"
        uv0_id = gid + "-map-0"
        uv1_id = gid + "-map-1"
        vid = gid + "-vertices"

        # Empty position source
        pos_src = ET.SubElement(me, "source")
        pos_src.set("id", pos_id)
        pos_arr = ET.SubElement(pos_src, "float_array")
        pos_arr.set("id", pos_id + "-array")
        pos_arr.set("count", "0")
        pos_tc = ET.SubElement(pos_src, "technique_common")
        pos_acc = ET.SubElement(pos_tc, "accessor")
        pos_acc.set("source", "#" + pos_id + "-array")
        pos_acc.set("count", "0")
        pos_acc.set("stride", "3")
        for pname in ["X", "Y", "Z"]:
            p = ET.SubElement(pos_acc, "param")
            p.set("name", pname)
            p.set("type", "float")

        # Empty normals source
        nor_src = ET.SubElement(me, "source")
        nor_src.set("id", norm_id)
        nor_arr = ET.SubElement(nor_src, "float_array")
        nor_arr.set("id", norm_id + "-array")
        nor_arr.set("count", "0")
        nor_tc = ET.SubElement(nor_src, "technique_common")
        nor_acc = ET.SubElement(nor_tc, "accessor")
        nor_acc.set("source", "#" + norm_id + "-array")
        nor_acc.set("count", "0")
        nor_acc.set("stride", "3")
        for pname in ["X", "Y", "Z"]:
            p = ET.SubElement(nor_acc, "param")
            p.set("name", pname)
            p.set("type", "float")

        # Empty UV0 source
        uv0_src = ET.SubElement(me, "source")
        uv0_src.set("id", uv0_id)
        uv0_arr = ET.SubElement(uv0_src, "float_array")
        uv0_arr.set("id", uv0_id + "-array")
        uv0_arr.set("count", "0")
        uv0_tc = ET.SubElement(uv0_src, "technique_common")
        uv0_acc = ET.SubElement(uv0_tc, "accessor")
        uv0_acc.set("source", "#" + uv0_id + "-array")
        uv0_acc.set("count", "0")
        uv0_acc.set("stride", "2")
        for pname in ["S", "T"]:
            p = ET.SubElement(uv0_acc, "param")
            p.set("name", pname)
            p.set("type", "float")

        # Empty UV1 source
        uv1_src = ET.SubElement(me, "source")
        uv1_src.set("id", uv1_id)
        uv1_arr = ET.SubElement(uv1_src, "float_array")
        uv1_arr.set("id", uv1_id + "-array")
        uv1_arr.set("count", "0")
        uv1_tc = ET.SubElement(uv1_src, "technique_common")
        uv1_acc = ET.SubElement(uv1_tc, "accessor")
        uv1_acc.set("source", "#" + uv1_id + "-array")
        uv1_acc.set("count", "0")
        uv1_acc.set("stride", "2")
        for pname in ["S", "T"]:
            p = ET.SubElement(uv1_acc, "param")
            p.set("name", pname)
            p.set("type", "float")

        # Vertices element
        verts = ET.SubElement(me, "vertices")
        verts.set("id", vid)
        vi = ET.SubElement(verts, "input")
        vi.set("semantic", "POSITION")
        vi.set("source", "#" + pos_id)

        # If this MESH_NULL has line data, write position source with actual data
        line_indices = mesh_dict.get("line_indices")
        line_verts = mesh_dict.get("line_verts")
        if line_indices is not None and line_verts is not None:
            lv = np.asarray(line_verts, dtype=np.float32).reshape(-1, 3)
            li = np.asarray(line_indices, dtype=np.int32).reshape(-1, 2)
            lv_count = lv.shape[0]
            line_count = li.shape[0]

            # Update position source float_array and accessor
            for src_elem in me:
                if src_elem.tag == "source" and src_elem.get("id") == pos_id:
                    for child in src_elem:
                        if child.tag == "float_array":
                            child.text = _fmt_floats(lv.flatten(), lv_count * 3)
                            child.set("count", str(lv_count * 3))
                        elif child.tag == "technique_common":
                            for tc_elem in child:
                                if tc_elem.tag == "accessor":
                                    tc_elem.set("count", str(lv_count))
                    break

            # Write <lines> element
            lines_elem = ET.SubElement(me, "lines")
            lines_elem.set("count", str(line_count))
            lin = ET.SubElement(lines_elem, "input")
            lin.set("semantic", "VERTEX")
            lin.set("source", "#" + vid)
            lin.set("offset", "0")
            lp = ET.SubElement(lines_elem, "p")
            lp.text = _fmt_uints(li.flatten(), line_count * 2)

        return mat_names

    # Standard mesh
    vertices = mesh_dict.get("vertices")
    if vertices is None:
        return mat_names

    raw_pos = np.asarray(vertices, dtype=np.float32)
    corner_count = raw_pos.shape[0]
    raw_pos_flat = raw_pos.ravel()

    normals = mesh_dict.get("normals")
    uv0 = mesh_dict.get("uv0")
    uv1 = mesh_dict.get("uv1")
    uv_extra = mesh_dict.get("uv_extra", [])
    colors = mesh_dict.get("colors")
    color_layers = mesh_dict.get("color_layers", [])
    color_layer_names = mesh_dict.get("color_layer_names", [])
    tvert_names = mesh_dict.get("tvert_names", [])

    has_norms = normals is not None
    has_uv0 = uv0 is not None
    has_uv1 = uv1 is not None
    has_colors = colors is not None
    has_color_layers = bool(color_layers)
    if has_color_layers:
        has_colors = True

    raw_nor = np.asarray(normals, dtype=np.float32).ravel() if has_norms else None
    raw_uv0 = np.asarray(uv0, dtype=np.float32).ravel() if has_uv0 else None
    raw_uv1 = np.asarray(uv1, dtype=np.float32).ravel() if has_uv1 else None
    raw_col = colors if has_colors else None  # uint8

    # Pre-process UVs (flip V) — vectorized with numpy
    proc_uv0 = []
    proc_uv0_arr = None
    if has_uv0:
        proc_uv0_arr = np.asarray(uv0, dtype=np.float32).reshape(-1, 2).copy()
        proc_uv0_arr[:, 1] = 1.0 - proc_uv0_arr[:, 1]
        proc_uv0 = proc_uv0_arr.ravel()

    proc_uv1 = []
    proc_uv1_arr = None
    if has_uv1:
        proc_uv1_arr = np.asarray(uv1, dtype=np.float32).reshape(-1, 2).copy()
        proc_uv1_arr[:, 1] = 1.0 - proc_uv1_arr[:, 1]
        proc_uv1 = proc_uv1_arr.ravel()

    # Pre-process extra UV layers (V-flip) — vectorized
    proc_uv_extra = []
    proc_uv_extra_arrs = []
    num_extra_uvs = len(uv_extra)
    for ei in range(num_extra_uvs):
        eb = np.asarray(uv_extra[ei], dtype=np.float32)
        if eb.shape[0] > 0:
            eb_c = eb.reshape(-1, 2).copy()
            eb_c[:, 1] = 1.0 - eb_c[:, 1]
            proc_uv_extra_arrs.append(eb_c)
            proc_uv_extra.append(eb_c.ravel())
        else:
            proc_uv_extra_arrs.append(None)
            proc_uv_extra.append([])

    # Color conversion (uint8→float) — vectorized
    proc_color = []
    if has_colors and not has_color_layers:
        col_arr = np.asarray(raw_col, dtype=np.uint8).reshape(-1, 4)
        proc_color = (col_arr.astype(np.float32) / 255.0).ravel()

    proc_color_layers = []
    if has_color_layers:
        for lb in color_layers:
            lb_arr = np.asarray(lb, dtype=np.uint8)
            if lb_arr.shape[0] > 0:
                proc = (lb_arr.reshape(-1, 4).astype(np.float32) / 255.0).ravel()
                proc_color_layers.append(proc)
            else:
                proc_color_layers.append([])

    # Dedup position
    uniq_pos, pos_idx = _dedup_float(raw_pos_flat, 3, corner_count)

    # Dedup normals
    uniq_nor = []
    nor_idx = []
    if has_norms:
        uniq_nor, nor_idx = _dedup_float(raw_nor, 3, corner_count)

    # Process line (loose edge) data
    line_dedup_idx = []
    line_indices = mesh_dict.get("line_indices")
    line_verts = mesh_dict.get("line_verts")
    if line_indices is not None and line_verts is not None:
        li = np.asarray(line_indices, dtype=np.int32).reshape(-1, 2)
        line_count = li.shape[0]
        lv = np.asarray(line_verts, dtype=np.float32).reshape(-1, 3)
        lv_count = lv.shape[0]

        # Build position hash for existing dedup vertices
        pos_map = {}
        for i in range(0, len(uniq_pos), 3):
            key = (uniq_pos[i], uniq_pos[i + 1], uniq_pos[i + 2])
            pos_map[key] = i // 3

        bv_to_dedup = {}
        for i in range(line_count * 2):
            bvi = int(li.flatten()[i])
            if bvi in bv_to_dedup:
                continue
            if bvi < 0 or bvi >= lv_count:
                bv_to_dedup[bvi] = 0
                continue
            p = lv[bvi]
            key = (float(p[0]), float(p[1]), float(p[2]))
            if key in pos_map:
                bv_to_dedup[bvi] = pos_map[key]
            else:
                ni = len(uniq_pos) // 3
                pos_map[key] = ni
                uniq_pos.extend([float(p[0]), float(p[1]), float(p[2])])
                bv_to_dedup[bvi] = ni

        line_dedup_idx = [bv_to_dedup[int(li.flatten()[i])] for i in range(line_count * 2)]

    # Dedup UV layers (all UV sets share one index offset)
    uniq_uv0 = []
    uniq_uv1 = []
    uniq_uv_extra = []
    uv_idx = []

    total_uv_layers = (1 if has_uv0 else 0) + (1 if has_uv1 else 0)
    for ei in range(num_extra_uvs):
        if len(proc_uv_extra[ei]):
            total_uv_layers += 1

    if total_uv_layers >= 2:
        # Combine all UV layers into a single stream for dedup — vectorized
        combined_stride = total_uv_layers * 2
        combined = np.zeros((corner_count, combined_stride), dtype=np.float32)
        offset = 0
        if has_uv0:
            combined[:, offset:offset + 2] = proc_uv0_arr
            offset += 2
        if has_uv1:
            combined[:, offset:offset + 2] = proc_uv1_arr
            offset += 2
        for ei in range(num_extra_uvs):
            if proc_uv_extra_arrs[ei] is not None:
                combined[:, offset:offset + 2] = proc_uv_extra_arrs[ei]
                offset += 2

        u_arr, uv_idx = _dedup_float_np(combined, corner_count)

        # Split back into separate UV arrays
        src_off = 0
        if has_uv0:
            uniq_uv0 = u_arr[:, src_off:src_off + 2].flatten().tolist()
            src_off += 2
        if has_uv1:
            uniq_uv1 = u_arr[:, src_off:src_off + 2].flatten().tolist()
            src_off += 2
        for ei in range(num_extra_uvs):
            if proc_uv_extra_arrs[ei] is not None:
                uniq_uv_extra.append(u_arr[:, src_off:src_off + 2].flatten().tolist())
                src_off += 2
    elif has_uv0:
        uniq_uv0, uv_idx = _dedup_float(proc_uv0, 2, corner_count)
    elif has_uv1:
        uniq_uv1, uv_idx = _dedup_float(proc_uv1, 2, corner_count)

    # Dedup colors
    uniq_color = []
    color_idx = []
    uniq_color_layers = []
    if has_color_layers:
        nlayers = len(proc_color_layers)
        combined_stride = nlayers * 4
        combined_colors = np.zeros((corner_count, combined_stride), dtype=np.float32)
        for li_idx in range(nlayers):
            if len(proc_color_layers[li_idx]):
                cl = np.asarray(proc_color_layers[li_idx], dtype=np.float32).reshape(-1, 4)
                combined_colors[:, li_idx * 4:li_idx * 4 + 4] = cl
        u_arr, color_idx = _dedup_float_np(combined_colors, corner_count)
        for li_idx in range(nlayers):
            uniq_color_layers.append(u_arr[:, li_idx * 4:li_idx * 4 + 4].flatten().tolist())
    elif has_colors:
        uniq_color, color_idx = _dedup_float(proc_color, 4, corner_count)

    # Write sources
    pos_id = gid + "-positions"
    _write_source(me, pos_id, uniq_pos, len(uniq_pos) // 3, 3)

    norm_id = ""
    if has_norms:
        norm_id = gid + "-normals"
        _write_source(me, norm_id, uniq_nor, len(uniq_nor) // 3, 3, is_normal=True)

    uv0_id = ""
    if has_uv0:
        uv0_id = gid + "-map-0"
        _write_source(me, uv0_id, uniq_uv0, len(uniq_uv0) // 2, 2, param_names=_UV_PARAMS)

    uv1_id = ""
    if has_uv1:
        uv1_id = gid + "-map-1"
        _write_source(me, uv1_id, uniq_uv1, len(uniq_uv1) // 2, 2, param_names=_UV_PARAMS)

    # Write extra UV sources (map-2, map-3, ...)
    extra_uv_ids = []
    for ei in range(len(uniq_uv_extra)):
        uv_id = gid + "-map-" + str(ei + 2)
        _write_source(me, uv_id, uniq_uv_extra[ei], len(uniq_uv_extra[ei]) // 2, 2, param_names=_UV_PARAMS)
        extra_uv_ids.append(uv_id)

    color_id = ""
    if has_colors and not has_color_layers:
        color_id = gid + "-colors"
        _write_source(me, color_id, uniq_color, len(uniq_color) // 4, 4, param_names=_COLOR_PARAMS)

    color_layer_ids = []
    if has_color_layers:
        for li_idx in range(len(uniq_color_layers)):
            if li_idx < len(color_layer_names) and color_layer_names[li_idx]:
                lname = color_layer_names[li_idx]
            else:
                lname = "Col"
                if li_idx > 0:
                    lname += str(li_idx)
            cid = gid + "-colors-" + lname
            _write_source(me, cid, uniq_color_layers[li_idx],
                          len(uniq_color_layers[li_idx]) // 4, 4, param_names=_COLOR_PARAMS)
            color_layer_ids.append(cid)

    # <vertices> element
    vid = gid + "-vertices"
    verts = ET.SubElement(me, "vertices")
    verts.set("id", vid)
    vi = ET.SubElement(verts, "input")
    vi.set("semantic", "POSITION")
    vi.set("source", "#" + pos_id)

    # <triangles> per draw region
    primitives = mesh_dict.get("primitives")
    indices = mesh_dict.get("indices")
    if primitives is not None and indices is not None:
        prims = np.asarray(primitives, dtype=np.uint32)
        idx = np.asarray(indices, dtype=np.uint32)

        has_uv = has_uv0 or has_uv1 or bool(uniq_uv_extra)
        nor_off = 1
        uv_off = 2 if has_uv else -1
        col_off = (3 if has_uv else 2) if has_colors else -1
        nstreams = 1 + (1 if has_norms else 0) + (1 if has_uv else 0) + (1 if has_colors else 0)

        # Pre-convert index arrays to numpy for vectorized triangle building
        pos_idx_np = np.asarray(pos_idx, dtype=np.int32).ravel()
        nor_idx_np = np.asarray(nor_idx, dtype=np.int32).ravel() if has_norms else None
        uv_idx_np = np.asarray(uv_idx, dtype=np.int32).ravel() if has_uv else None
        color_idx_np = np.asarray(color_idx, dtype=np.int32).ravel() if has_colors else None

        for i in range(prims.shape[0]):
            istart = int(prims[i, 0])
            icount = int(prims[i, 1])
            info = int(prims[i, 2])
            mi = info & 0x0FFFFFFF

            if mi < len(shape_materials):
                mn = _sanitize_id(shape_materials[mi]["name"]) + "-material"
            else:
                mn = "mat_" + str(mi)
            if mn not in mat_names:
                mat_names.append(mn)

            tris = ET.SubElement(me, "triangles")
            tris.set("material", mn)
            tris.set("count", str(icount // 3))

            def add_input(sem, ref, off, set_str=None):
                inp = ET.SubElement(tris, "input")
                inp.set("semantic", sem)
                inp.set("source", ref)
                inp.set("offset", str(off))
                if set_str:
                    inp.set("set", set_str)

            add_input("VERTEX", "#" + vid, 0)
            if has_norms:
                add_input("NORMAL", "#" + norm_id, nor_off)
            if has_uv0:
                add_input("TEXCOORD", "#" + uv0_id, uv_off, "0")
            if has_uv1:
                add_input("TEXCOORD", "#" + uv1_id, uv_off, "1")
            for ei in range(len(extra_uv_ids)):
                add_input("TEXCOORD", "#" + extra_uv_ids[ei], uv_off, str(ei + 2))
            if has_color_layers:
                for li_idx in range(len(color_layer_ids)):
                    add_input("COLOR", "#" + color_layer_ids[li_idx], col_off, str(li_idx))
            elif has_colors:
                add_input("COLOR", "#" + color_id, col_off)

            # Build interleaved <p> with per-stream indices; reverse winding — vectorized
            prim_idx = idx[istart:istart + icount].reshape(-1, 3)[:, ::-1].flatten()

            p_idx_arr = np.zeros(icount * nstreams, dtype=np.int64)
            p_idx_arr[0::nstreams] = pos_idx_np[prim_idx]
            if has_norms:
                p_idx_arr[nor_off::nstreams] = nor_idx_np[prim_idx]
            if has_uv:
                p_idx_arr[uv_off::nstreams] = uv_idx_np[prim_idx]
            if has_colors:
                p_idx_arr[col_off::nstreams] = color_idx_np[prim_idx]

            p = ET.SubElement(tris, "p")
            p.text = _fmt_uints(p_idx_arr, icount * nstreams)

    # Write <lines> element for loose edges
    if line_dedup_idx:
        line_count = len(line_dedup_idx) // 2
        lines_elem = ET.SubElement(me, "lines")
        lines_elem.set("count", str(line_count))
        li_inp = ET.SubElement(lines_elem, "input")
        li_inp.set("semantic", "VERTEX")
        li_inp.set("source", "#" + vid)
        li_inp.set("offset", "0")
        lp = ET.SubElement(lines_elem, "p")
        lp.text = _fmt_uints(line_dedup_idx, line_count * 2)

    return mat_names


# ---------------------------------------------------------------------------
# Node tree writing
# ---------------------------------------------------------------------------

def _get_mesh_name(shape, mesh_idx):
    objects = shape.get("objects", [])
    if objects:
        for obj in objects:
            start = obj["start_mesh"]
            num = obj["num_meshes"]
            if start <= mesh_idx < start + num:
                ni = obj["name_index"]
                names = shape.get("names", [])
                if 0 <= ni < len(names):
                    return names[ni]
    return "mesh_" + str(mesh_idx)


def _get_geometry_name(shape, mesh_idx):
    meshes = shape.get("meshes", [])
    if 0 <= mesh_idx < len(meshes):
        geo_name = meshes[mesh_idx].get("geometry_name", "")
        if geo_name:
            return geo_name
    return _get_mesh_name(shape, mesh_idx)


def _write_node_recursive(parent, shape, node_idx, mesh_mat_names):
    node = shape["nodes"][node_idx]

    xml_node = ET.SubElement(parent, "node")
    xml_node.set("id", _sanitize_id(node["name"]))
    xml_node.set("name", node["name"])
    xml_node.set("type", "NODE")

    # Write <matrix sid="transform">
    q = node["quaternion"]
    trans = node["translation"]
    scale = node["scale"]
    mat = _quat_trans_scale_to_collada_matrix(q, trans, scale)
    mx = ET.SubElement(xml_node, "matrix")
    mx.set("sid", "transform")
    mx.text = _fmt_floats(mat, 16)

    # Process objects (follow first_object → next_sibling chain)
    oi = node["first_object"]
    objects = shape.get("objects", [])
    while 0 <= oi < len(objects):
        obj = objects[oi]

        names = shape.get("names", [])
        obj_name = names[obj["name_index"]] if 0 <= obj["name_index"] < len(names) else "object"

        if obj_name != node["name"]:
            xml_obj = ET.SubElement(xml_node, "node")
            xml_obj.set("id", _sanitize_id(obj_name))
            xml_obj.set("name", obj_name)
            xml_obj.set("type", "NODE")
        else:
            xml_obj = xml_node

        for m in range(obj["num_meshes"]):
            mi = obj["start_mesh"] + m
            geo_name = _get_geometry_name(shape, mi)
            mesh_id = _sanitize_id(geo_name) + "-mesh"
            ig = ET.SubElement(xml_obj, "instance_geometry")
            ig.set("url", "#" + mesh_id)
            ig.set("name", geo_name)

            bm = ET.SubElement(ig, "bind_material")
            tc = ET.SubElement(bm, "technique_common")

            if mi < len(mesh_mat_names):
                for mn in mesh_mat_names[mi]:
                    imat = ET.SubElement(tc, "instance_material")
                    imat.set("symbol", mn)
                    imat.set("target", "#" + mn)

                    # Add bind_vertex_input for UV layers
                    meshes = shape.get("meshes", [])
                    mesh_ptr = meshes[mi] if 0 <= mi < len(meshes) else None
                    if mesh_ptr and not mesh_ptr.get("is_null", False):
                        has_any_uv = ("uv0" in mesh_ptr or "uv1" in mesh_ptr or
                                      mesh_ptr.get("uv_extra"))
                        if has_any_uv:
                            tvert_names = mesh_ptr.get("tvert_names", [])
                            if tvert_names:
                                for ui in range(len(tvert_names)):
                                    bvi = ET.SubElement(imat, "bind_vertex_input")
                                    bvi.set("semantic", tvert_names[ui])
                                    bvi.set("input_semantic", "TEXCOORD")
                                    bvi.set("input_set", str(ui))
                            else:
                                bvi = ET.SubElement(imat, "bind_vertex_input")
                                bvi.set("semantic", "UVMap")
                                bvi.set("input_semantic", "TEXCOORD")
                                bvi.set("input_set", "0")

        oi = obj["next_sibling"]

    # Process child nodes
    ci = node["first_child"]
    nodes = shape["nodes"]
    while 0 <= ci < len(nodes):
        _write_node_recursive(xml_node, shape, ci, mesh_mat_names)
        ci = nodes[ci]["next_sibling"]


# ---------------------------------------------------------------------------
# XML serialization with proper formatting
# ---------------------------------------------------------------------------

def _serialize_xml(root, authoring_tool):
    """Serialize the ElementTree to a string with proper formatting (C-accelerated)."""
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


# ---------------------------------------------------------------------------
# Main DAE writer
# ---------------------------------------------------------------------------

def write_dae(filepath, data, authoring_tool="Blender"):
    """Write mesh/material/node data to a .dae file. Returns True on success."""
    collada = ET.Element("COLLADA")
    collada.set("xmlns", "http://www.collada.org/2005/11/COLLADASchema")
    collada.set("version", "1.4.1")
    collada.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")

    # <asset>
    asset = ET.SubElement(collada, "asset")
    contrib = ET.SubElement(asset, "contributor")
    ET.SubElement(contrib, "author").text = "Blender User"
    ET.SubElement(contrib, "authoring_tool").text = authoring_tool
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(asset, "created").text = now
    ET.SubElement(asset, "modified").text = now
    unit = ET.SubElement(asset, "unit")
    unit.set("name", "meter")
    unit.set("meter", "1")
    ET.SubElement(asset, "up_axis").text = "Z_UP"

    # Library order matches Blender DAE output
    lib_effs = ET.SubElement(collada, "library_effects")
    ET.SubElement(collada, "library_images")
    lib_mats = ET.SubElement(collada, "library_materials")
    lib_geoms = ET.SubElement(collada, "library_geometries")
    lib_scenes = ET.SubElement(collada, "library_visual_scenes")

    scene = ET.SubElement(collada, "scene")
    ivs = ET.SubElement(scene, "instance_visual_scene")
    ivs.set("url", "#Scene")

    vs = ET.SubElement(lib_scenes, "visual_scene")
    vs.set("id", "Scene")
    vs.set("name", "Scene")

    # Materials with PBR effects
    materials = data.get("materials", [])
    for i, m in enumerate(materials):
        mid = _sanitize_id(m["name"]) + "-material"
        eid = _sanitize_id(m["name"]) + "-effect"
        mat = ET.SubElement(lib_mats, "material")
        mat.set("id", mid)
        mat.set("name", m["name"])
        ie = ET.SubElement(mat, "instance_effect")
        ie.set("url", "#" + eid)

        eff = ET.SubElement(lib_effs, "effect")
        eff.set("id", eid)
        profile = ET.SubElement(eff, "profile_COMMON")
        tech = ET.SubElement(profile, "technique")
        tech.set("sid", "common")
        lambert = ET.SubElement(tech, "lambert")

        # Emission
        emission = ET.SubElement(lambert, "emission")
        em_color = ET.SubElement(emission, "color")
        em_color.set("sid", "emission")
        em_color.text = "0 0 0 1"

        # Diffuse → base_color
        bc = np.asarray(m.get("base_color", [0.8, 0.8, 0.8, 1.0]), dtype=np.float32)
        diffuse = ET.SubElement(lambert, "diffuse")
        diff_color = ET.SubElement(diffuse, "color")
        diff_color.set("sid", "diffuse")
        diff_alpha = float(bc[3]) if float(bc[3]) < 1.0 else float(bc[3])
        diff_color.text = f"{_fmt_float(float(bc[0]))} {_fmt_float(float(bc[1]))} {_fmt_float(float(bc[2]))} {_fmt_float(diff_alpha)}"

        # Transparent → alpha (A_ONE mode: alpha = 1 - base_color[3])
        if float(bc[3]) < 1.0:
            trans = ET.SubElement(lambert, "transparent")
            trans.set("opaque", "A_ONE")
            trans_color = ET.SubElement(trans, "color")
            trans_color.set("sid", "transparent")
            trans_alpha = 1.0 - float(bc[3])
            trans_color.text = f"0 0 0 {_fmt_float(trans_alpha)}"

        # Reflectivity → metallic (only if original had it)
        if m.get("has_reflectivity", False):
            refl = ET.SubElement(lambert, "reflectivity")
            refl_f = ET.SubElement(refl, "float")
            refl_f.set("sid", "specular")
            refl_f.text = _fmt_float(float(m.get("metallic", 0.0)))

        # Shininess → roughness (invert)
        if m.get("has_shininess", False):
            shin = ET.SubElement(lambert, "shininess")
            shin_f = ET.SubElement(shin, "float")
            shin_f.set("sid", "shininess")
            shin_f.text = _fmt_float(max(0.0, min(1.0, 1.0 - float(m.get("roughness", 0.5)))) * 1000.0)

        # Index of refraction
        ior = ET.SubElement(lambert, "index_of_refraction")
        ior_f = ET.SubElement(ior, "float")
        ior_f.set("sid", "ior")
        ior_f.text = _fmt_float(float(m.get("ior", 1.45)))

    # Geometries
    meshes = data.get("meshes", [])
    mesh_mat_names = []
    for i, mesh_dict in enumerate(meshes):
        mname = _get_geometry_name(data, i)
        mn = _write_geometry(lib_geoms, mesh_dict, i, mname, materials)
        mesh_mat_names.append(mn)

    # Node tree — find root nodes (parent == -1) and recurse
    nodes = data.get("nodes", [])
    for i, node in enumerate(nodes):
        if node["parent_index"] == -1:
            _write_node_recursive(vs, data, i, mesh_mat_names)

    # Serialize and write to file
    xml_str = _serialize_xml(collada, authoring_tool)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(xml_str)

    return True
