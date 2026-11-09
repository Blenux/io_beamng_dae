"""Internal shader graph builder for ShaderNodeBeamngMaterial.

Python port of the fork's gpu_beamng_material() in
source/blender/nodes/shader/nodes/node_shader_beamng_material.cc.

The fork compiles one monolithic GPU graph from node storage; here we build
the equivalent from standard Blender nodes inside the node's internal group.
Structure, ordering and fallbacks follow gpu_beamng_material step-for-step:

  per-layer: sample maps (active UV / "UV1" via ShaderNodeUVMap) → factor × map →
  vertex color → instance diffuse (Scene paints) → emissive-flag redirect →
  metallic/roughness/coat (+palette paint) → opacity accumulate → blend.
  then: detail color (additive signed), detail normal, AO multiply into base,
  normal map + DirectX green-flip, specular→IOR level, reflectivity→metallic,
  alpha clip (alpha_ref/255), PreMulAlpha, Principled BSDF.

Paint palette math ports bng_paint_base_color / bng_paint_scalar from
gpu_shader_material_tex_image.glsl.
"""

import bpy


# ---------------------------------------------------------------------------
# Small node helpers
# ---------------------------------------------------------------------------

class _Builder:
    """Builds standard Blender nodes inside a ShaderNodeTree for one
    BeamNGMaterialNode. All value-socket helpers accept either a node
    socket or a python constant (float / 4-tuple color)."""

    def __init__(self, node, tree):
        self.node = node
        self.tree = tree
        self.uv_nodes = {}
        self.vcol_node = None
        # (node, group label) — drives NodeFrame grouping + compile cleanup.
        self.created = []
        self.group_label = "Material"
        # name → live paint-uniform node; values set by _build via paint_vals.
        self.paint_nodes = {}
        self.paint_vals = {}

        _interp = {'LINEAR': 'Linear', 'CLOSEST': 'Closest',
                   'CUBIC': 'Cubic', 'SMART': 'Smart'}
        self.interp = _interp.get(node.bng_tex_interp, 'Linear')
        self.proj = node.bng_tex_proj
        self.ext = node.bng_tex_ext

    def new(self, idname):
        n = self.tree.nodes.new(idname)
        self.created.append((n, self.group_label))
        return n

    # -- value plumbing --

    @staticmethod
    def _is_sock(v):
        return hasattr(v, 'is_linked')  # NodeSocket duck-typing

    def _in(self, sock_or_const, input_socket):
        """Link or assign an input socket."""
        if self._is_sock(sock_or_const):
            self.tree.links.new(sock_or_const, input_socket)
        else:
            input_socket.default_value = sock_or_const

    # -- UV / texture sampling --

    def _uvmap_node(self, name):
        n = self.new('ShaderNodeUVMap')
        n.uv_map = name
        return n.outputs['UV']

    def _vlen(self, vec):
        n = self.new('ShaderNodeVectorMath')
        n.operation = 'LENGTH'
        self._in(vec, n.inputs[0])
        return n.outputs['Value']

    def mix_vec(self, a, b, fac):
        n = self.new('ShaderNodeMix')
        n.data_type = 'VECTOR'
        self._in(fac, n.inputs['Factor'])
        self._in(a, n.inputs['A'])
        self._in(b, n.inputs['B'])
        return n.outputs['Result']

    def _recorded_uv_names(self, idx):
        """Layer names the importer recorded on this material — the real
        index→name mapping when the mesh's UV layers aren't UV0/UVMap."""
        mat = getattr(self.node, 'id_data', None)
        if mat is not None and hasattr(mat, 'nodes') \
                and not hasattr(mat, 'blend_method'):
            for m in bpy.data.materials:
                if m.node_tree == mat:
                    mat = m
                    break
        if mat is None:
            return []
        rec = mat.get("bng_uv_names")
        if rec and idx < len(rec):
            return [rec[idx]]
        return []

    def uv(self, idx):
        """Mesh UV layer by index — name-independent fallback chain:
        the material's recorded layer name first (bng_uv_names), then
        our import names ("UV0"/"UV1"), Blender's defaults ("UVMap"/
        "UVMap.001"), finally the active/render UV (empty name).
        A missing layer samples (0,0,0) in EEVEE, so the first
        non-zero candidate wins."""
        idx = min(max(int(idx), 0), len(_UV_NAME_CANDIDATES) - 1)
        if idx not in self.uv_nodes:
            out = self._uvmap_node("")
            candidates = (self._recorded_uv_names(idx)
                          + list(_UV_NAME_CANDIDATES[idx]))
            for name in reversed(candidates):
                s = self._uvmap_node(name)
                exists = self.math('GREATER_THAN', self._vlen(s), 0.0)
                out = self.mix_vec(out, s, exists)
            self.uv_nodes[idx] = out
        return self.uv_nodes[idx]

    def sample(self, image, uv_idx, scale=None):
        """bng_sample_map / bng_sample_map_scaled. Returns Color socket or
        None when no image is set."""
        if image is None:
            return None
        tex = self.new('ShaderNodeTexImage')
        tex.image = image
        tex.interpolation = self.interp
        tex.projection = self.proj
        tex.extension = self.ext
        if scale is not None:
            mapping = self.new('ShaderNodeMapping')
            mapping.inputs['Scale'].default_value = (scale[0], scale[1], 1.0)
            self.tree.links.new(self.uv(uv_idx), mapping.inputs['Vector'])
            self.tree.links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
        else:
            self.tree.links.new(self.uv(uv_idx), tex.inputs['Vector'])
        return tex.outputs['Color']

    # -- math / mix --

    def math(self, op, a, b=0.0):
        n = self.new('ShaderNodeMath')
        n.operation = op
        self._in(a, n.inputs[0])
        self._in(b, n.inputs[1])
        return n.outputs['Value']

    def mix_color(self, blend_type, a, b, fac=1.0):
        """node_mix_mult / node_mix_blend / ADD on RGBA."""
        n = self.new('ShaderNodeMix')
        n.data_type = 'RGBA'
        n.blend_type = blend_type
        self._in(fac, n.inputs['Factor'])
        self._in(a, n.inputs['A'])
        self._in(b, n.inputs['B'])
        return n.outputs['Result']

    def mix_float(self, a, b, fac):
        """node_mix_float."""
        n = self.new('ShaderNodeMix')
        n.data_type = 'FLOAT'
        self._in(fac, n.inputs['Factor'])
        self._in(a, n.inputs['A'])
        self._in(b, n.inputs['B'])
        return n.outputs['Result']

    def paint_sock(self, name):
        """Live paint uniform socket — deterministic name lets
        update_paint_uniforms() rewrite default_value in place instead of
        rebuilding the whole graph on every Scene paint change."""
        n = self.paint_nodes.get(name)
        if n is None:
            n = self.new('ShaderNodeRGB' if name.endswith(' Color')
                         else 'ShaderNodeValue')
            n.name = name
            n.label = name
            n.hide = True
            self.paint_nodes[name] = n
        n.outputs[0].default_value = self.paint_vals[name]
        return n.outputs[0]

    def luminance(self, color):
        n = self.new('ShaderNodeRGBToBW')
        self._in(color, n.inputs['Color'])
        return n.outputs['Val']

    def separate(self, color):
        n = self.new('ShaderNodeSeparateColor')
        self._in(color, n.inputs['Color'])
        return n.outputs['Red'], n.outputs['Green'], n.outputs['Blue']

    def combine(self, r, g, b, a=1.0):
        n = self.new('ShaderNodeCombineColor')
        self._in(r, n.inputs['Red'])
        self._in(g, n.inputs['Green'])
        self._in(b, n.inputs['Blue'])
        alpha_in = n.inputs.get('Alpha')  # not present on all versions
        if alpha_in is not None:
            self._in(a, alpha_in)
        return n.outputs['Color']

    def vcol(self):
        """GPU_attribute_default_color equivalent — active color attribute."""
        if self.vcol_node is None:
            self.vcol_node = self.new('ShaderNodeVertexColor')
        return self.vcol_node.outputs['Color']

    # -- BeamNG helpers --

    def _paint_weights(self, palette):
        """Shared palette split + normalization (rgb_norm, alpha)."""
        pr, pg, pb = self.separate(palette)
        pa = palette.node.outputs.get('Alpha') if self._is_sock(palette) \
            else None
        if pa is None:
            sep = self.new('ShaderNodeSeparateColor')
            self._in(palette, sep.inputs['Color'])
            pa = sep.outputs['Alpha']
        rgb_sum = self.math('ADD', self.math('ADD', pr, pg), pb)
        rgb_sum = self.math('ADD', rgb_sum, 1e-10)
        nr = self.math('DIVIDE', pr, rgb_sum)
        ng = self.math('DIVIDE', pg, rgb_sum)
        nb = self.math('DIVIDE', pb, rgb_sum)
        dw = self.math('SUBTRACT', 1.0, pa)
        return nr, ng, nb, pa, dw

    def paint_base_color(self, base, palette, terms):
        """bng_paint_base_color — palette RGB selects paint slots,
        alpha selects white/no-paint. terms[s][ci] is a socket holding
        paint_s.color[ci] * color[3] * 2.0."""
        nr, ng, nb, pa, dw = self._paint_weights(palette)
        br, bg, bb = self.separate(base)
        out = []
        for ci in range(3):
            t = self.math('MULTIPLY', nr, terms[0][ci])
            t = self.math('ADD', t, self.math('MULTIPLY', ng, terms[1][ci]))
            t = self.math('ADD', t, self.math('MULTIPLY', nb, terms[2][ci]))
            pal_c = self.math('ADD', self.math('MULTIPLY', t, pa), dw)
            out.append(self.math('MULTIPLY', (br, bg, bb)[ci], pal_c))
        return self.combine(*out, 1.0)

    def paint_scalar(self, base, palette, d0, d1, d2):
        """bng_paint_scalar."""
        nr, ng, nb, pa, dw = self._paint_weights(palette)
        blend = self.math('MULTIPLY', nr, d0)
        blend = self.math('ADD', blend, self.math('MULTIPLY', ng, d1))
        blend = self.math('ADD', blend, self.math('MULTIPLY', nb, d2))
        blend = self.math('ADD', self.math('MULTIPLY', blend, pa), dw)
        return self.math('MULTIPLY', base, blend)

    def detail_color(self, base, detail, factor):
        """bng_detail_color: base + (detail*2 - 1) * factor."""
        d2 = self.mix_color('MULTIPLY', detail, (2.0, 2.0, 2.0, 2.0))
        dm = self.mix_color('ADD', d2, (-1.0, -1.0, -1.0, -1.0))
        df = self.mix_color('MULTIPLY', dm,
                            (factor, factor, factor, factor))
        return self.mix_color('ADD', base, df)

    def _decode_normal(self, color):
        """color_to_normal_new_shading: tangent-space decode (c*2-1)."""
        r, g, b = self.separate(color)
        x = self.math('SUBTRACT', self.math('MULTIPLY', r, 2.0), 1.0)
        y = self.math('SUBTRACT', self.math('MULTIPLY', g, 2.0), 1.0)
        z = self.math('SUBTRACT', self.math('MULTIPLY', b, 2.0), 1.0)
        n = self.new('ShaderNodeCombineXYZ')
        self._in(x, n.inputs['X'])
        self._in(y, n.inputs['Y'])
        self._in(z, n.inputs['Z'])
        return n.outputs['Vector']

    def _encode_normal(self, vec):
        n = self.new('ShaderNodeVectorMath')
        n.operation = 'SCALE'
        self._in(vec, n.inputs[0])
        n.inputs[3].default_value = 0.5
        n2 = self.new('ShaderNodeVectorMath')
        n2.operation = 'ADD'
        self._in(n.outputs['Vector'], n2.inputs[0])
        n2.inputs[1].default_value = (0.5, 0.5, 0.5)
        return n2.outputs['Vector']

    def invert_green(self, color):
        """color_invert_green_channel on the *encoded* color — equivalent
        since decode/encode are affine."""
        r, g, b = self.separate(color)
        g_inv = self.math('SUBTRACT', 1.0, g)
        return self.combine(r, g_inv, b)

    def detail_normal(self, base_color, detail_color, strength):
        """bng_detail_normal: n.xy += detail.xy*strength; normalize."""
        n_base = self._decode_normal(base_color)
        n_det = self._decode_normal(detail_color)
        ds = self.new('ShaderNodeSeparateXYZ')
        self._in(n_det, ds.inputs['Vector'])
        sx = self.math('MULTIPLY', ds.outputs['X'], strength)
        sy = self.math('MULTIPLY', ds.outputs['Y'], strength)
        comb = self.new('ShaderNodeCombineXYZ')
        self._in(sx, comb.inputs['X'])
        self._in(sy, comb.inputs['Y'])
        comb.inputs['Z'].default_value = 0.0
        add = self.new('ShaderNodeVectorMath')
        add.operation = 'ADD'
        self._in(n_base, add.inputs[0])
        self._in(comb.outputs['Vector'], add.inputs[1])
        norm = self.new('ShaderNodeVectorMath')
        norm.operation = 'NORMALIZE'
        self._in(add.outputs['Vector'], norm.inputs[0])
        return self._encode_normal(norm.outputs['Vector'])


# ---------------------------------------------------------------------------
# Scene paints (GPU_uniform equivalents of Scene->beamng_vehicle_paints)
# ---------------------------------------------------------------------------

def _scene_paint_slots():
    """Return [(color4, metallic, roughness, clearcoat, cc_roughness) ×3]
    from the active scene's bng_vehicle_paints, or neutral defaults."""
    defaults = ((1.0, 1.0, 1.0, 1.0), 0.0, 0.5, 0.0, 0.0)
    try:
        scene = bpy.context.scene or bpy.data.scenes[0]
        vp = getattr(scene, 'bng_vehicle_paints', None)
        if vp is None:
            return [defaults] * 3
        from ..core.paints import _ensure_slots
        _ensure_slots(vp)
        out = []
        for slot in vp.slots:
            out.append((tuple(slot.color), slot.metallic, slot.roughness,
                        slot.clearcoat, slot.clearcoat_roughness))
        while len(out) < 3:
            out.append(defaults)
        return out[:3]
    except Exception:
        return [defaults] * 3


def _paint_uniform_defaults(paints):
    """{uniform node name: value} for every live paint socket a graph may
    create — single source of truth for both _build (initial values) and
    update_paint_uniforms (in-place rewrite). 'Term i' folds
    color[i]*alpha*2.0 for bng_paint_base_color."""
    d = {}
    for s in range(3):
        color, met, rough, coat, ccr = paints[s]
        d[f"BNG Paint {s} Color"] = tuple(color)
        d[f"BNG Paint {s} Intensity"] = float(color[3])
        d[f"BNG Paint {s} Metallic"] = float(met)
        d[f"BNG Paint {s} Roughness"] = float(rough)
        d[f"BNG Paint {s} Clearcoat"] = float(coat)
        d[f"BNG Paint {s} CC Roughness"] = float(ccr)
        for ci in range(3):
            d[f"BNG Paint {s} Term {ci}"] = float(color[ci]) * float(color[3]) * 2.0
    return d


# ---------------------------------------------------------------------------
# Graph build — port of gpu_beamng_material()
# ---------------------------------------------------------------------------

# UV name fallback chain per index: our importer's names, then Blender's
# default names, then the active UV (empty name) — stock Blender has no
# index-based UV accessor ('.uvN' aliases exist only in the fork's draw code).
_UV_NAME_CANDIDATES = {
    0: ("UV0", "UVMap"),
    1: ("UV1", "UVMap.001"),
    2: ("UV2", "UVMap.002"),
    3: ("UV3", "UVMap.003"),
}


def _first_input(node, *names):
    for name in names:
        if name in node.inputs:
            return node.inputs[name]
    return None


def _build(node, tree):
    """Build the full shading graph inside `tree`.

    Returns (final shader socket, [(node, group label), ...]) — the caller
    links the socket to group output / Material Output. Mirrors
    gpu_beamng_material()'s structure.
    Does not clear the tree — callers decide (compile keeps the source node).
    """
    stages = node.stages
    stage0 = stages[0] if len(stages) > 0 else None
    b = _Builder(node, tree)
    b.paint_vals = _paint_uniform_defaults(_scene_paint_slots())

    # === 1. Factors & defaults (stage 0) ===
    roughness = min(max(stage0.roughness_factor, 0.0), 1.0) if stage0 else 1.0
    emissive_factor = stage0.emissive_factor if stage0 else 0.0
    metallic = min(max(stage0.metallic_factor, 0.0), 0.999) if stage0 else 0.0
    coat_weight = stage0.clear_coat_factor if stage0 else 0.0
    coat_roughness = stage0.clear_coat_roughness_factor if stage0 else 0.0
    if stage0:
        if stage0.emissive and emissive_factor == 0.0:
            emissive_factor = 1.0
        if stage0.glow and emissive_factor > 0.0:
            emissive_factor *= 2.0
        # emissiveIntensityNits → scaled viewport luminance (fork parity:
        # max(ef,1) * max(1, nits/1000) so on vs on_intense stays visible).
        if stage0.emissive_intensity_nits > 0.0:
            emissive_factor = max(emissive_factor, 1.0) * max(
                1.0, stage0.emissive_intensity_nits / 1000.0)

    active_layers = min(max(node.bng_active_layers, 1), 4)

    color_link = None
    emission_link = None
    metallic_link = None
    roughness_link = None
    ao_link = None
    coat_weight_link = None
    coat_roughness_link = None
    normal_map_color_link = None
    accum_opacity = None

    # === 2. Per-layer sampling + paint + blend ===
    # Fork: stages past active_layers still contribute when they have maps.
    for i in range(len(stages)):
        st = stages[i]
        b.group_label = f"Layer {i + 1}"
        if i >= active_layers and not st.color_map and not st.normal_map \
                and st.color_map_img is None and st.normal_map_img is None:
            continue

        # --- base color: factor × map (map defaults to 1.0) ---
        layer_color = b.sample(st.color_map_img, st.color_map_uv)
        if layer_color is not None:
            layer_color = b.mix_color('MULTIPLY', layer_color,
                                      tuple(st.color_map_color))
        else:
            layer_color = tuple(st.color_map_color)

        # vertex color (before instance/paint, matching game order)
        if st.vert_color:
            layer_color = b.mix_color('MULTIPLY', layer_color, b.vcol())

        # instance diffuse × paints[0].color — skipped when palette handles it
        if st.instance_diffuse and not (
                st.color_palette_map_img is not None and st.palette_base_color):
            layer_color = b.mix_color('MULTIPLY', layer_color,
                                      b.paint_sock("BNG Paint 0 Color"))

        # MATERIAL_LAYER_FLAG_emissive: base → emission, diffuse zeroed
        if st.emissive:
            if emission_link is None:
                emission_link = layer_color
            layer_color = (0.0, 0.0, 0.0, 0.0)

        # --- metallic ---
        layer_metallic = st.metallic_factor
        if (st.instance_diffuse and st.metallic_map_img is None
                and not (st.color_palette_map_img is not None
                         and st.palette_metallic)):
            layer_metallic = b.paint_sock("BNG Paint 0 Metallic")
        if not b._is_sock(layer_metallic):
            layer_metallic = min(max(layer_metallic, 0.0), 0.999)
        mm = b.sample(st.metallic_map_img, st.metallic_map_uv)
        layer_metallic_link = (b.math('MULTIPLY', layer_metallic,
                                      b.luminance(mm))
                               if mm is not None else layer_metallic)

        # --- roughness ---
        layer_roughness = st.roughness_factor
        if (st.instance_diffuse and st.roughness_map_img is None
                and not (st.color_palette_map_img is not None
                         and st.palette_roughness)):
            layer_roughness = b.paint_sock("BNG Paint 0 Roughness")
        if not b._is_sock(layer_roughness):
            layer_roughness = min(max(layer_roughness, 0.0), 1.0)
        rm = b.sample(st.roughness_map_img, st.roughness_map_uv)
        layer_roughness_link = (b.math('MULTIPLY', layer_roughness,
                                       b.luminance(rm))
                                if rm is not None else layer_roughness)

        # --- clear coat weight ---
        layer_coat = st.clear_coat_factor
        if (st.instance_diffuse and st.clear_coat_map_img is None
                and layer_coat == 0.0
                and not (st.color_palette_map_img is not None
                         and st.palette_clear_coat)):
            layer_coat = b.paint_sock("BNG Paint 0 Clearcoat")
        cm = b.sample(st.clear_coat_map_img, st.clear_coat_map_uv)
        layer_coat_link = (b.math('MULTIPLY', layer_coat, b.luminance(cm))
                           if cm is not None else layer_coat)

        # --- clear coat roughness ---
        layer_ccr = st.clear_coat_roughness_factor
        crm = b.sample(st.clear_coat_roughness_map_img,
                       st.clear_coat_roughness_map_uv)
        layer_ccr_link = (b.math('MULTIPLY', layer_ccr, b.luminance(crm))
                          if crm is not None else layer_ccr)

        # --- per-layer palette paint tinting ---
        palette = b.sample(st.color_palette_map_img,
                           st.color_palette_map_uv)
        if palette is not None:
            if st.palette_base_color:
                terms = [[b.paint_sock(f"BNG Paint {s} Term {ci}")
                          for ci in range(3)] for s in range(3)]
                layer_color = b.paint_base_color(layer_color, palette, terms)
            if st.palette_metallic:
                layer_metallic_link = b.paint_scalar(
                    layer_metallic_link, palette,
                    b.paint_sock("BNG Paint 0 Metallic"),
                    b.paint_sock("BNG Paint 1 Metallic"),
                    b.paint_sock("BNG Paint 2 Metallic"))
            if st.palette_roughness:
                layer_roughness_link = b.paint_scalar(
                    layer_roughness_link, palette,
                    b.paint_sock("BNG Paint 0 Roughness"),
                    b.paint_sock("BNG Paint 1 Roughness"),
                    b.paint_sock("BNG Paint 2 Roughness"))
            if st.palette_clear_coat:
                layer_coat_link = b.paint_scalar(
                    layer_coat_link, palette,
                    b.paint_sock("BNG Paint 0 Clearcoat"),
                    b.paint_sock("BNG Paint 1 Clearcoat"),
                    b.paint_sock("BNG Paint 2 Clearcoat"))
            if st.palette_clear_coat_roughness:
                layer_ccr_link = b.paint_scalar(
                    layer_ccr_link, palette,
                    b.paint_sock("BNG Paint 0 CC Roughness"),
                    b.paint_sock("BNG Paint 1 CC Roughness"),
                    b.paint_sock("BNG Paint 2 CC Roughness"))

        # --- opacity / layer blend factor ---
        op_fac = min(max(st.opacity_factor, 0.0), 1.0)
        om = b.sample(st.opacity_map_img, st.opacity_map_uv)
        fac = (b.math('MULTIPLY', op_fac, b.luminance(om))
               if om is not None else op_fac)
        if st.instance_opacity:
            fac = b.math('MULTIPLY', fac,
                         b.paint_sock("BNG Paint 0 Intensity"))

        if accum_opacity is None:
            accum_opacity = fac
        else:
            accum_opacity = b.mix_float(accum_opacity, fac, fac)

        color_link = (layer_color if color_link is None
                      else b.mix_color('MIX', color_link, layer_color, fac))

        # --- per-layer normal map (blend decoded normals by opacity) ---
        nm = b.sample(st.normal_map_img, st.normal_map_uv)
        if nm is not None:
            # DirectX → OpenGL: invert green (affine — applied to raw color)
            if node.bng_normal_map_space == 'DIRECTX':
                nm = b.invert_green(nm)
            normal_map_color_link = (nm if normal_map_color_link is None
                                     else b.mix_color('MIX',
                                                      normal_map_color_link,
                                                      nm, fac))

        metallic_link = (layer_metallic_link if metallic_link is None
                         else b.mix_float(metallic_link, layer_metallic_link,
                                          fac))
        roughness_link = (layer_roughness_link if roughness_link is None
                          else b.mix_float(roughness_link,
                                           layer_roughness_link, fac))
        coat_weight_link = (layer_coat_link if coat_weight_link is None
                            else b.mix_float(coat_weight_link,
                                             layer_coat_link, fac))
        coat_roughness_link = (layer_ccr_link if coat_roughness_link is None
                               else b.mix_float(coat_roughness_link,
                                                layer_ccr_link, fac))

        # --- per-layer AO ---
        am = b.sample(st.ao_map_img, st.ao_map_uv)
        if am is not None:
            ao_link = am if ao_link is None else b.mix_color(
                'MIX', ao_link, am, fac)

        # --- emission (first-wins) ---
        if emission_link is None:
            em = b.sample(st.emissive_map_img, st.emissive_map_uv)
            if em is not None:
                if st.vert_color_emissive:
                    em = b.mix_color('MULTIPLY', em, b.vcol())
                if st.instance_emissive:
                    em = b.mix_color('MULTIPLY', em,
                                     b.paint_sock("BNG Paint 0 Color"))
                emission_link = em

    # --- detail maps (stage 0 settings, detail_scale UV scaling) ---
    b.group_label = "Post"
    detail_color_link = None
    detail_normal_link = None
    if stage0 is not None:
        detail_color_link = b.sample(stage0.detail_map_img,
                                     stage0.detail_map_uv,
                                     scale=stage0.detail_scale)
        detail_normal_link = b.sample(stage0.detail_normal_map_img,
                                      stage0.detail_normal_map_uv,
                                      scale=stage0.detail_scale)

    if color_link is None:
        color_link = (0.8, 0.8, 0.8, 1.0)

    # AO multiplies into base color
    if ao_link is not None:
        color_link = b.mix_color('MULTIPLY', color_link, ao_link)

    # Detail color: base + (detail*2-1)*strength
    if detail_color_link is not None and stage0.detail_strength > 0.0:
        color_link = b.detail_color(color_link, detail_color_link,
                                    stage0.detail_strength)

    # === alpha: accumulated layer opacity when translucent/alpha_test ===
    alpha_in = 1.0
    if (node.bng_translucent or node.bng_alpha_test) \
            and accum_opacity is not None:
        alpha_in = accum_opacity
        if node.bng_alpha_test:
            alpha_in = b.math('GREATER_THAN', alpha_in,
                              node.bng_alpha_ref / 255.0)

    metallic_in = metallic_link if metallic_link is not None else metallic
    roughness_in = roughness_link if roughness_link is not None else roughness
    if coat_weight_link is None:
        coat_weight_link = coat_weight
    if coat_roughness_link is None:
        coat_roughness_link = coat_roughness

    # Detail PBR maps: channel * lerp(1, map_luminance, strength).
    # Sampled with detail_scale like the color/normal detail maps.
    def _detail_mul(base_link, img, uv_idx, strength):
        if img is None or strength <= 0.0:
            return base_link
        s = b.sample(img, uv_idx, scale=stage0.detail_scale)
        if s is None:
            return base_link
        mod = b.mix_float(1.0, b.luminance(s), strength)
        return b.math('MULTIPLY', base_link, mod)

    if stage0 is not None:
        metallic_in = _detail_mul(
            metallic_in, stage0.detail_metallic_map_img,
            stage0.detail_metallic_map_uv,
            stage0.detail_metallic_map_strength)
        roughness_in = _detail_mul(
            roughness_in, stage0.detail_roughness_map_img,
            stage0.detail_roughness_map_uv,
            stage0.detail_roughness_map_strength)
        roughness_in = _detail_mul(
            roughness_in, stage0.roughness_detail_map_img,
            stage0.roughness_detail_map_uv, 1.0)
        if stage0.detail_ao_map_img is not None \
                and stage0.detail_ao_map_strength > 0.0:
            am = b.sample(stage0.detail_ao_map_img,
                          stage0.detail_ao_map_uv,
                          scale=stage0.detail_scale)
            if am is not None:
                mod = b.mix_color('MIX', (1.0, 1.0, 1.0, 1.0), am,
                                  stage0.detail_ao_map_strength)
                color_link = b.mix_color('MULTIPLY', color_link, mod)
        if stage0.detail_opacity_map_img is not None \
                and stage0.detail_opacity_map_strength > 0.0:
            om = b.sample(stage0.detail_opacity_map_img,
                          stage0.detail_opacity_map_uv,
                          scale=stage0.detail_scale)
            if om is not None:
                mod = b.mix_float(1.0, b.luminance(om),
                                  stage0.detail_opacity_map_strength)
                alpha_in = b.math('MULTIPLY', alpha_in, mod) \
                    if b._is_sock(alpha_in) else alpha_in
        if stage0.opacity_detail_map_img is not None:
            om = b.sample(stage0.opacity_detail_map_img,
                          stage0.opacity_detail_map_uv,
                          scale=stage0.detail_scale)
            if om is not None:
                alpha_in = b.math('MULTIPLY', alpha_in,
                                  b.luminance(om)) \
                    if b._is_sock(alpha_in) else alpha_in

    # PreMulAlpha: base color *= opacity
    if node.bng_alpha_blend_mode == 'PREMUL_ALPHA':
        alpha_col = (_broadcast_alpha(b, alpha_in)
                     if b._is_sock(alpha_in)
                     else (alpha_in, alpha_in, alpha_in, 1.0))
        color_link = b.mix_color('MULTIPLY', color_link, alpha_col)

    # === 3. Normal ===
    normal_link = None
    if normal_map_color_link is not None:
        normal_color = normal_map_color_link
        if detail_normal_link is not None and stage0.detail_normal_strength > 0.0:
            if node.bng_normal_map_space == 'DIRECTX':
                detail_normal_link = b.invert_green(detail_normal_link)
            normal_color = b.detail_normal(normal_color, detail_normal_link,
                                           stage0.detail_normal_strength)
        nm_node = b.new('ShaderNodeNormalMap')
        nm_node.space = 'TANGENT'
        # Empty = active/render UV — NormalMap has no index-based uv option
        # in stock Blender (fork resolves .uvN for tangents natively).
        nm_node.uv_map = ""
        nm_node.inputs['Strength'].default_value = (
            stage0.normal_map_strength if stage0 else 1.0)
        b._in(normal_color, nm_node.inputs['Color'])
        normal_link = nm_node.outputs['Normal']

    # === specular map → Specular IOR Level (v1.0) ===
    specular_ior_link = None
    if stage0 is not None and stage0.specular_map_img is not None:
        sm = b.sample(stage0.specular_map_img, stage0.specular_map_uv)
        if sm is not None:
            sr, sg, sb = b.separate(sm)
            specular_ior_link = b.math(
                'MULTIPLY', b.math('ADD', b.math('ADD', sr, sg), sb),
                1.0 / 3.0)

    # v1.0 specularPower → roughness approximation when nothing drove it.
    if node.bng_version == 'V1_0' and stage0 is not None \
            and stage0.specular_power > 0.0 and roughness_link is None:
        roughness_in = min(max(1.0 - stage0.specular_power / 128.0, 0.0), 1.0)

    # === reflectivity map → metallic (v1.0; uses specular uv like fork) ===
    if stage0 is not None and stage0.reflectivity_map_img is not None \
            and metallic_link is not None:
        rm = b.sample(stage0.reflectivity_map_img, stage0.specular_map_uv)
        if rm is not None:
            rr, rg, rb = b.separate(rm)
            refl_avg = b.math(
                'MULTIPLY', b.math('ADD', b.math('ADD', rr, rg), rb),
                1.0 / 3.0)
            metallic_in = b.math('MULTIPLY', metallic_link, refl_avg)

    # === 4. Principled BSDF ===
    b.group_label = "Output"
    bsdf = b.new('ShaderNodeBsdfPrincipled')
    b._in(color_link, bsdf.inputs['Base Color'])
    b._in(metallic_in, bsdf.inputs['Metallic'])
    b._in(roughness_in, bsdf.inputs['Roughness'])
    b._in(alpha_in, bsdf.inputs['Alpha'])
    if normal_link is not None:
        tree.links.new(normal_link, bsdf.inputs['Normal'])
    inp = _first_input(bsdf, 'Specular IOR Level')
    if inp is not None:
        if specular_ior_link is not None:
            tree.links.new(specular_ior_link, inp)
        else:
            inp.default_value = 0.5
    inp = _first_input(bsdf, 'Coat Weight', 'Clearcoat')
    if inp is not None:
        b._in(coat_weight_link, inp)
    inp = _first_input(bsdf, 'Coat Roughness', 'Clearcoat Roughness')
    if inp is not None:
        b._in(coat_roughness_link, inp)
    inp = _first_input(bsdf, 'Emission Color', 'Emission')
    if inp is not None:
        if emission_link is not None:
            b._in(emission_link, inp)
        elif stage0 is not None:
            inp.default_value = tuple(stage0.emissive_map_color)
    inp = _first_input(bsdf, 'Emission Strength')
    if inp is not None:
        inp.default_value = emissive_factor

    # === subsurface (stage flag or material-level intensity) ===
    if stage0 is not None and (stage0.subsurface or node.bng_sub_surface):
        inp = _first_input(bsdf, 'Subsurface Weight', 'Subsurface')
        if inp is not None:
            inp.default_value = node.bng_sub_surface_intensity \
                if node.bng_sub_surface_intensity > 0.0 else 0.1

    # === v1.0 specular color → tint ===
    if node.bng_version == 'V1_0' and stage0 is not None \
            and tuple(stage0.specular_color) != (0.5, 0.5, 0.5, 1.0):
        inp = _first_input(bsdf, 'Specular Tint')
        if inp is not None:
            b._in(tuple(stage0.specular_color), inp)

    return bsdf.outputs['BSDF'], b.created


def _broadcast_alpha(b, alpha):
    """Pack a float alpha socket into an RGBA for premultiply."""
    n = b.new('ShaderNodeCombineColor')
    b._in(alpha, n.inputs['Red'])
    b._in(alpha, n.inputs['Green'])
    b._in(alpha, n.inputs['Blue'])
    alpha_in = n.inputs.get('Alpha')
    if alpha_in is not None:
        alpha_in.default_value = 1.0
    return n.outputs['Color']


# ---------------------------------------------------------------------------
# Node layout — organize generated nodes into columns by distance from the
# sink (sources left → sinks right), no overlaps.
# ---------------------------------------------------------------------------

def _est_height(n):
    """Estimated node height (node.height is 0 until drawn in the UI)."""
    return max(len(n.inputs), len(n.outputs)) * 24 + 70


def _layout_nodes(nodes, tree, origin_x, origin_y):
    """Assign non-overlapping locations to `nodes`.

    Column index = longest path (in links) to a node with no outgoing link
    among `nodes` — i.e. sinks land rightmost, sources leftmost. Within a
    column nodes stack top→down in creation order.
    """
    if not nodes:
        return
    node_set = set(nodes)
    succ = {n: [] for n in nodes}
    for link in tree.links:
        if link.from_node in node_set and link.to_node in node_set:
            succ[link.from_node].append(link.to_node)

    level = {}

    def _lev(n, _seen=frozenset()):
        if n in level:
            return level[n]
        if n in _seen:
            return 0
        children = succ.get(n, [])
        v = 0 if not children else 1 + max(
            _lev(c, _seen | {n}) for c in children)
        level[n] = v
        return v

    for n in nodes:
        _lev(n)
    max_level = max(level.values(), default=0)

    COL_W = 300
    ROW_GAP = 24
    cols = {}
    for n in nodes:
        cols.setdefault(level[n], []).append(n)

    for lv, col_nodes in cols.items():
        x = origin_x + (max_level - lv) * COL_W
        y = origin_y
        for n in col_nodes:
            n.location = (x, y)
            y -= _est_height(n) + ROW_GAP


def _apply_frames(tree, labeled_nodes):
    """Parent generated nodes into labeled NodeFrames (fork's frame grouping).
    Runs after _layout_nodes — children keep their absolute positions."""
    groups = {}
    for n, label in labeled_nodes:
        groups.setdefault(label, []).append(n)
    frames = []
    for label, members in groups.items():
        frame = tree.nodes.new('NodeFrame')
        frame.label = label
        frame["bng_gen"] = True
        frames.append(frame)
        fx = min(n.location.x for n in members) - 40
        fy = max(n.location.y for n in members) + 40
        frame.location = (fx, fy)
        for n in members:
            abs_x, abs_y = n.location.x, n.location.y
            n.parent = frame
            # parent assignment makes location frame-relative — rebase it.
            n.location = (abs_x - fx, abs_y - fy)
    return frames


def build_graph(node):
    """Rebuild the node's internal group (called from _rebuild)."""
    tree = node._get_or_create_tree()
    tree.nodes.clear()
    tree.links.clear()
    shader_out, created = _build(node, tree)
    # Marks the group as carrying live paint uniforms — pre-uniform groups
    # get one rebuild on the next paint change (update_paint_uniforms).
    tree["bng_paint_uniforms"] = True

    # Ensure a 'Shader' output socket exists on the group interface.
    interface = tree.interface
    has_output = any(
        item.item_type == 'SOCKET' and item.name == 'Shader'
        and item.in_out == 'OUTPUT'
        for item in interface.items_tree)
    if not has_output:
        interface.new_socket('Shader', in_out='OUTPUT',
                             socket_type='NodeSocketShader')
    group_output = tree.nodes.new('NodeGroupOutput')
    tree.links.new(shader_out, group_output.inputs['Shader'])
    _layout_nodes(list(tree.nodes), tree, 0.0, 0.0)
    _apply_frames(tree, created)

    # Force re-eval of the owning tree (id_data is the NodeTree, not the
    # Material, in Blender 5.x).
    parent_tree = getattr(node, 'id_data', None)
    if parent_tree is not None and hasattr(parent_tree, 'links'):
        linked = False
        for link in list(parent_tree.links):
            if link.from_node == node:
                to_socket, from_socket = link.to_socket, link.from_socket
                parent_tree.links.remove(link)
                parent_tree.links.new(from_socket, to_socket)
                linked = True
                break
        if not linked:
            # Node was created before its group sockets refreshed — wire it
            # to the material output now that 'Shader' exists.
            sock = node.outputs.get('Shader')
            out_node = next(
                (n for n in parent_tree.nodes
                 if n.bl_idname == 'ShaderNodeOutputMaterial'
                 and n.is_active_output), None)
            if sock is not None and out_node is not None:
                parent_tree.links.new(sock, out_node.inputs['Surface'])


def compile_to_material_tree(node):
    """node.bng_compile_to_nodes — build the same standard-node graph
    directly into the material's node tree and link it to the output.
    The BeamNG node stays; its link is replaced."""
    tree = getattr(node, 'id_data', None)
    if tree is None or not hasattr(tree, 'nodes'):
        return

    # Re-compile cleanup (fork parity): remove everything we generated last
    # time — tagged nodes, frames, and our output node — before rebuilding.
    for n in list(tree.nodes):
        if n is not node and n.get("bng_gen"):
            tree.nodes.remove(n)

    before = set(tree.nodes)
    shader_out, created = _build(node, tree)
    for n, _label in created:
        n["bng_gen"] = True

    output_node = None
    for n in tree.nodes:
        if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output:
            output_node = n
            break
    if output_node is None:
        output_node = tree.nodes.new('ShaderNodeOutputMaterial')
        output_node["bng_gen"] = True

    # Remove the BeamNG node → output link, wire compiled graph instead.
    for link in list(tree.links):
        if link.from_node == node and link.to_node == output_node:
            tree.links.remove(link)
    tree.links.new(shader_out, output_node.inputs['Surface'])

    # Lay the generated nodes out in non-overlapping columns below the
    # BeamNG node (node.height is 0 until drawn — estimate when absent).
    anchor_h = node.height if getattr(node, 'height', 0) > 0 else 500
    generated = [n for n in tree.nodes
                 if n not in before and n.bl_idname != 'NodeFrame']
    _layout_nodes(generated, tree, node.location.x,
                  node.location.y - anchor_h - 80)
    _apply_frames(tree, created)


def _write_paint_nodes(tree, values):
    for n in tree.nodes:
        v = values.get(n.name)
        if v is not None and n.outputs:
            n.outputs[0].default_value = v


def update_paint_uniforms():
    """Rewrite live paint uniform sockets in every graph — the cheap
    in-place answer to a Scene paint change (internal groups AND compiled
    material trees, since both can carry "BNG Paint *" nodes)."""
    values = _paint_uniform_defaults(_scene_paint_slots())
    for group in bpy.data.node_groups:
        _write_paint_nodes(group, values)
    for mat in bpy.data.materials:
        tree = mat.node_tree if mat.use_nodes else None
        if tree is None:
            continue
        _write_paint_nodes(tree, values)
        for n in tree.nodes:
            if n.bl_idname != 'ShaderNodeBeamngMaterial':
                continue
            inner = getattr(n, 'node_tree', None)
            # Pre-uniform groups (old .blend files) carry baked constants —
            # rebuild once; build_graph marks the tree so this runs once.
            if inner is not None and not inner.get("bng_paint_uniforms"):
                n._rebuild()
            break


def rebuild_all_beamng_materials():
    """Rebuild every material's BeamNG node graph."""
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        for n in mat.node_tree.nodes:
            if n.bl_idname == 'ShaderNodeBeamngMaterial':
                n._rebuild()
                break
