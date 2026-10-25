"""Stargate Command gate room, procedural, for the stargate-sg1 theme.

Builds the SGC embarkation room around a Milky Way stargate -- glyph ring,
nine chevrons, iris, event horizon, kawoosh -- seen from the floor at the foot
of the ramp, animates one full dial / kawoosh / iris / shutdown cycle from
sgc_timeline.py and renders linear-light EXR frames for gate_compose.py.

Usage:
  blender -b --factory-startup -P sgc_gate.py -- OUT_DIR [options]

  --samples S      Cycles samples per pixel (default 48, adaptive, OIDN denoised)
  --scale PCT      resolution percentage of 3440x1440
  --look T,T,...   render just these times (seconds) to OUT/look_<t>.exr
  --preview        Workbench render of every frame at 25% (motion check)
  --only-frames A,B  render just these frame numbers
  --skip-existing  keep frames already in OUT (resume)
  --blend          also save OUT/sgc_gate.blend
  --only-lights A,B  look-dev: render with just these light objects
  --debug-gobo     look-dev: the horizon's caustic spot alone, fixed power

Frames whose visible state is identical to the previous frame (idle, iris
closed) are rendered once; OUT/manifest.json maps every loop frame to the
frame file that holds it.

Units are metres. Z up, floor at z=0. The gate stands in the XZ plane facing
-Y; the camera sits on -Y at eye height. Gate-local axes: X right, Z up,
-Y toward the room. "Clockwise" always means as seen from the room.
"""

import bpy
import bmesh
import json
import math
import os
import sys
import time
from mathutils import Matrix, Vector

import numpy as np

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import sgc_timeline as TL  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = (argv[0] if argv else os.path.expanduser("~/.cache/sg1-render/frames")).rstrip("/")


def arg(name, default, cast=str):
    if name in argv:
        return cast(argv[argv.index(name) + 1])
    return default


SAMPLES = arg("--samples", 48, int)
SCALE = arg("--scale", 100, int)
PREVIEW = "--preview" in argv
LOOK = [float(x) for x in arg("--look", "").split(",") if x]
ONLY = [int(x) for x in arg("--only-frames", "").split(",") if x]
SKIP_EXISTING = "--skip-existing" in argv
SAVE_BLEND = "--blend" in argv

W, H = 3440, 1440
FPS, FRAMES = TL.FPS, TL.FRAMES
os.makedirs(OUT, exist_ok=True)

# ------------------------------------------------------------------ dimensions
R_BORE = 2.45          # event horizon / aperture radius
R_OUT = 3.35           # outer radius of the gate
R_TRACK0, R_TRACK1 = 2.62, 3.00   # rotating glyph ring
GATE_Z = 3.50          # gate centre height (its foot sits in the platform)
Y_EH = -0.02           # event horizon plane (gate-local y)
IRIS_Y = -0.115        # back of the iris blade stack
IRIS_H = 0.045         # stack depth
IRIS_N = 20
IRIS_PIVOT_R = 2.90
IRIS_MASK_R = 2.47
RX = 8.0               # room half-width
Y_BACK = 5.0           # back wall
Y_FRONT = -20.0        # wall behind the camera (only seen in reflections)
Z_CEIL = 9.5
CAM = Vector((0.0, -17.5, 1.75))

# ---------------------------------------------------------------- scene setup
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.get_devices()
for d in prefs.devices:
    d.use = d.type == "OPTIX"
cy = scene.cycles
cy.device = "GPU"
cy.samples = SAMPLES
cy.use_denoising = True
cy.denoiser = "OPENIMAGEDENOISE"
cy.denoising_use_gpu = True
cy.denoising_input_passes = "RGB_ALBEDO_NORMAL"
cy.denoising_prefilter = "ACCURATE"
# Fixed noise pattern: static parts of the room come out bit-identical from
# frame to frame, so the denoised result doesn't crawl.
cy.seed = 0
cy.use_animated_seed = False
cy.max_bounces = 8
cy.diffuse_bounces = 3
cy.glossy_bounces = 3
cy.transmission_bounces = 4
cy.transparent_max_bounces = 32
cy.volume_bounces = 0
cy.sample_clamp_indirect = 8.0
cy.blur_glossy = 1.0
cy.caustics_reflective = False
cy.caustics_refractive = False
cy.use_light_tree = True
cy.use_adaptive_sampling = True
cy.adaptive_threshold = 0.025   # the denoiser absorbs the rest
scene.render.resolution_x = W
scene.render.resolution_y = H
scene.render.resolution_percentage = SCALE
scene.render.use_persistent_data = True
scene.render.use_motion_blur = not PREVIEW
scene.render.motion_blur_shutter = 0.5
scene.render.fps = FPS
scene.frame_start = 0
scene.frame_end = FRAMES - 1
# Linear HDR out; gate_compose.py does bloom and tone mapping.
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.color_depth = "16"
scene.render.image_settings.exr_codec = "ZIP"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

if PREVIEW:
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_percentage = 25 if SCALE == 100 else SCALE
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"

world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.0015, 0.002, 0.003, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0


# ------------------------------------------------------------- node helpers
class NB:
    """Tiny shader-graph builder: inputs may be sockets, nodes or constants."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links

    def set(self, sock, v):
        if isinstance(v, bpy.types.NodeSocket):
            self.links.new(v, sock)
        elif isinstance(v, bpy.types.Node):
            self.links.new(v.outputs[0], sock)
        else:
            if isinstance(v, (tuple, list)) and sock.type == "RGBA" and len(v) == 3:
                v = (*v, 1.0)
            sock.default_value = v

    def new(self, typ, inputs=None, **props):
        n = self.nodes.new(typ)
        for k, v in props.items():
            setattr(n, k, v)
        for k, v in (inputs or {}).items():
            self.set(n.inputs[k], v)
        return n

    def math(self, op, a, b=0.0, c=None, clamp=False):
        n = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.set(n.inputs[0], a)
        self.set(n.inputs[1], b)
        if c is not None:
            self.set(n.inputs[2], c)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        n = self.new("ShaderNodeVectorMath", operation=op)
        self.set(n.inputs[0], a)
        if b is not None:
            self.set(n.inputs[1], b)
        if scale is not None:
            self.set(n.inputs["Scale"], scale)
        out = "Value" if op in ("LENGTH", "DOT_PRODUCT", "DISTANCE") else "Vector"
        return n.outputs[out]

    def value(self, name, v=0.0):
        n = self.new("ShaderNodeValue")
        n.name = n.label = name
        n.outputs[0].default_value = v
        return n.outputs[0]

    def lerp(self, a, b, f):
        """a + (b - a) * f for colours/vectors (f a float socket or const)."""
        d = self.vmath("SUBTRACT", b, a)
        return self.vmath("ADD", a, self.vmath("SCALE", d, scale=f))

    def scale(self, v, s):
        return self.vmath("SCALE", v, scale=s)

    def sep(self, v):
        n = self.new("ShaderNodeSeparateXYZ")
        self.set(n.inputs[0], v)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def comb(self, x, y, z):
        n = self.new("ShaderNodeCombineXYZ")
        self.set(n.inputs[0], x)
        self.set(n.inputs[1], y)
        self.set(n.inputs[2], z)
        return n.outputs[0]

    def noise(self, vec, scale=1.0, detail=2.0, rough=0.5, w=None, distortion=0.0):
        n = self.new("ShaderNodeTexNoise", noise_dimensions="4D" if w is not None else "3D")
        self.set(n.inputs["Vector"], vec)
        if w is not None:
            self.set(n.inputs["W"], w)
        self.set(n.inputs["Scale"], scale)
        self.set(n.inputs["Detail"], detail)
        self.set(n.inputs["Roughness"], rough)
        self.set(n.inputs["Distortion"], distortion)
        return n.outputs[0]

    def ridge(self, n, power):
        """Thin bright filaments where a noise crosses 0.5 (caustic webs)."""
        r = self.math("SUBTRACT", 1.0, self.math("ABSOLUTE", self.math("MULTIPLY_ADD", n, 2.0, -1.0)))
        return self.math("POWER", r, power)

    def smoothstep(self, x, e0, e1):
        n = self.new("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP")
        self.set(n.inputs["Value"], x)
        n.inputs["From Min"].default_value = e0
        n.inputs["From Max"].default_value = e1
        return n.outputs["Result"]

    def coords(self, kind="Object", obj=None):
        n = self.new("ShaderNodeTexCoord")
        if obj is not None:
            n.object = obj
        return n.outputs[kind]

    def position(self):
        return self.new("ShaderNodeNewGeometry").outputs["Position"]

    def output(self, shader):
        out = self.new("ShaderNodeOutputMaterial")
        self.set(out.inputs["Surface"], shader)
        return out


def material(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    return m, NB(m.node_tree)


def principled(nb, color, metallic=0.0, rough=0.5, normal=None, **extra):
    b = nb.new("ShaderNodeBsdfPrincipled")
    nb.set(b.inputs["Base Color"], color)
    b.inputs["Metallic"].default_value = metallic
    nb.set(b.inputs["Roughness"], rough)
    if normal is not None:
        nb.set(b.inputs["Normal"], normal)
    for k, v in extra.items():
        nb.set(b.inputs[k.replace("_", " ")], v)
    return b


def bump(nb, height, strength=0.5, distance=0.01, normal=None):
    b = nb.new("ShaderNodeBump")
    nb.set(b.inputs["Height"], height)
    b.inputs["Strength"].default_value = strength
    b.inputs["Distance"].default_value = distance
    if normal is not None:
        nb.set(b.inputs["Normal"], normal)
    return b.outputs[0]


# ------------------------------------------------------------- mesh helpers
def link(o, parent=None):
    scene.collection.objects.link(o)
    if parent is not None:
        o.parent = parent
    return o


def to_mesh(name, bm, mats, smooth=False, sharp_deg=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats if isinstance(mats, (list, tuple)) else [mats]:
        me.materials.append(m)
    if smooth:
        me.shade_smooth()
        if sharp_deg is not None:
            me.set_sharp_from_angle(angle=math.radians(sharp_deg))
    return me


def new_obj(name, me, parent=None, loc=(0, 0, 0), rot=(0, 0, 0)):
    o = bpy.data.objects.new(name, me)
    o.location = loc
    o.rotation_euler = rot
    return link(o, parent)


def bevel(o, width=0.01, segments=2):
    m = o.modifiers.new("bevel", "BEVEL")
    m.width = width
    m.segments = segments
    m.limit_method = "ANGLE"
    m.harden_normals = False
    return o


def bm_box(bm, lo, hi, matrix=Matrix(), mat_index=0):
    lo, hi = Vector(lo), Vector(hi)
    size, ctr = hi - lo, (hi + lo) / 2
    m = matrix @ Matrix.Translation(ctr) @ Matrix.Diagonal((*size, 1.0))
    r = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    faces = {f for v in r["verts"] for f in v.link_faces}
    for f in faces:
        f.material_index = mat_index
    return r["verts"]


def box(name, lo, hi, mat, parent=None, bev=0.0):
    bm = bmesh.new()
    bm_box(bm, lo, hi)
    o = new_obj(name, to_mesh(name, bm, mat), parent)
    if bev:
        bevel(o, bev)
    return o


def bm_prism(bm, outline_xz, y0, y1, matrix=Matrix(), mat_index=0):
    """Extrude a planar outline (x, z points, CCW seen from -Y) from y0 to y1."""
    front = [bm.verts.new(matrix @ Vector((x, y0, z))) for x, z in outline_xz]
    back = [bm.verts.new(matrix @ Vector((x, y1, z))) for x, z in outline_xz]
    faces = [bm.faces.new(front), bm.faces.new(list(reversed(back)))]
    n = len(outline_xz)
    for i in range(n):
        j = (i + 1) % n
        faces.append(bm.faces.new((front[j], front[i], back[i], back[j])))
    for f in faces:
        f.material_index = mat_index
    bmesh.ops.recalc_face_normals(bm, faces=faces)
    return faces


def bm_cyl(bm, p0, p1, r, segs=24, mat_index=0):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    m = Matrix.Translation((p0 + p1) / 2) @ rot
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r,
                                depth=d.length, matrix=m)
    for f in {f for v in res["verts"] for f in v.link_faces}:
        f.material_index = mat_index
    return res["verts"]


def revolve(bm, profile, segs, th0=0.0, th1=2 * math.pi, uv=None, mat_index=0, cap=False):
    """Sweep a closed (r, y) cross-section around the gate's Y axis.

    th is the clockwise angle from the top: (r, y) -> (r sin th, y, r cos th).
    uv=(r_min, r_max) writes u = th/2pi, v = (r - r_min)/(r_max - r_min).
    """
    full = abs(th1 - th0 - 2 * math.pi) < 1e-9
    n_th = segs if full else segs + 1
    ths = [th0 + (th1 - th0) * j / segs for j in range(n_th)]
    rings = [[bm.verts.new((r * math.sin(t), y, r * math.cos(t))) for (r, y) in profile] for t in ths]
    uvl = bm.loops.layers.uv.verify()
    faces = []
    np_ = len(profile)
    for j in range(segs):
        a, b = rings[j], rings[(j + 1) % n_th]
        for i in range(np_):
            k = (i + 1) % np_
            f = bm.faces.new((a[i], a[k], b[k], b[i]))
            f.material_index = mat_index
            faces.append(f)
            if uv:
                us = (ths[j] / (2 * math.pi), ths[j] / (2 * math.pi),
                      (th0 + (th1 - th0) * (j + 1) / segs) / (2 * math.pi),
                      (th0 + (th1 - th0) * (j + 1) / segs) / (2 * math.pi))
                rs = (profile[i][0], profile[k][0], profile[k][0], profile[i][0])
                for loop, u, r in zip(f.loops, us, rs):
                    loop[uvl].uv = (u, (r - uv[0]) / (uv[1] - uv[0]))
    if cap and not full:
        for ring in (rings[0], rings[-1]):
            f = bm.faces.new(ring)
            f.material_index = mat_index
            faces.append(f)
    bmesh.ops.recalc_face_normals(bm, faces=faces)
    return faces


def plane(name, origin, u_vec, v_vec, u_len, v_len, mat, parent=None):
    """Quad from origin spanning u_len along u_vec and v_len along v_vec,
    UVs in metres (for panel seams). Normal = u x v."""
    o, u, v = Vector(origin), Vector(u_vec).normalized(), Vector(v_vec).normalized()
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.verify()
    vs = [bm.verts.new(p) for p in (o, o + u * u_len, o + u * u_len + v * v_len, o + v * v_len)]
    f = bm.faces.new(vs)
    for loop, c in zip(f.loops, ((0, 0), (u_len, 0), (u_len, v_len), (0, v_len))):
        loop[uvl].uv = c
    return new_obj(name, to_mesh(name, bm, mat), parent)


# ================================================================ materials
def mat_wall(name, base, seam_w=2.4, seam_h=1.2, grime=0.35):
    """Painted steel wall panels: seams from a brick texture over UV metres,
    vertical streak grime, slightly glossy paint."""
    m, nb = material(name)
    uv = nb.coords("UV")
    brick = nb.new("ShaderNodeTexBrick", offset=0.0, offset_frequency=1, squash=1.0, squash_frequency=1)
    nb.set(brick.inputs["Vector"], uv)
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Mortar Size"].default_value = 0.012
    brick.inputs["Mortar Smooth"].default_value = 0.3
    brick.inputs["Brick Width"].default_value = seam_w
    brick.inputs["Row Height"].default_value = seam_h
    seam = brick.outputs[1]
    x, y, z = nb.sep(uv)
    streak = nb.noise(nb.comb(nb.math("MULTIPLY", x, 3.0), nb.math("MULTIPLY", y, 0.25), 0.0), 1.0, 4.0, 0.6)
    blotch = nb.noise(nb.scale(uv, 0.35), 1.0, 3.0, 0.55)
    tone = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY", streak, blotch), grime, 1.0 - grime * 0.25)
    col = nb.scale(base, nb.math("MULTIPLY", tone, nb.math("SUBTRACT", 1.0, nb.math("MULTIPLY", seam, 0.55))))
    rough = nb.math("MULTIPLY_ADD", blotch, 0.25, 0.42)
    nrm = bump(nb, nb.math("SUBTRACT", 1.0, seam), 0.6, 0.006)
    nb.output(principled(nb, col, 0.25, rough, nrm))
    return m


def mat_floor():
    m, nb = material("floor")
    uv = nb.coords("UV")
    brick = nb.new("ShaderNodeTexBrick", offset=0.5, offset_frequency=2)
    nb.set(brick.inputs["Vector"], uv)
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Mortar Size"].default_value = 0.01
    brick.inputs["Brick Width"].default_value = 2.0
    brick.inputs["Row Height"].default_value = 2.0
    seam = brick.outputs[1]
    big = nb.noise(nb.scale(uv, 0.25), 1.0, 4.0, 0.6)
    fine = nb.noise(nb.scale(uv, 6.0), 1.0, 3.0, 0.5)
    tone = nb.math("MULTIPLY_ADD", big, 0.5, 0.75)
    col = nb.scale((0.11, 0.115, 0.12), nb.math("MULTIPLY", tone, nb.math("SUBTRACT", 1.0, nb.math("MULTIPLY", seam, 0.6))))
    # sealed concrete: glossy patches where it's been polished by boots
    rough = nb.math("MULTIPLY_ADD", nb.smoothstep(big, 0.35, 0.7), 0.4, 0.18)
    nrm = bump(nb, nb.math("SUBTRACT", nb.math("MULTIPLY", fine, 0.3), seam), 0.25, 0.004)
    nb.output(principled(nb, col, 0.0, rough, nrm))
    return m


def mat_simple(name, color, metallic=0.0, rough=0.5, noise_amt=0.15, noise_scale=3.0):
    m, nb = material(name)
    p = nb.position()
    n = nb.noise(p, noise_scale, 4.0, 0.6)
    col = nb.scale(color, nb.math("MULTIPLY_ADD", n, noise_amt * 2, 1.0 - noise_amt))
    nb.output(principled(nb, col, metallic, nb.math("MULTIPLY_ADD", n, 0.15, rough - 0.07),
                         bump(nb, n, 0.08, 0.01)))
    return m


def mat_emit(name, color, strength):
    m, nb = material(name)
    e = nb.new("ShaderNodeEmission")
    nb.set(e.inputs["Color"], color)
    e.inputs["Strength"].default_value = strength
    nb.output(e)
    return m


def mat_hazard():
    m, nb = material("hazard")
    uv = nb.coords("UV")
    x, y, _ = nb.sep(uv)
    d = nb.math("ADD", x, y)
    band = nb.math("GREATER_THAN", nb.math("FRACT", nb.math("MULTIPLY", d, 2.5)), 0.5)
    col = nb.lerp((0.012, 0.012, 0.012), (0.75, 0.48, 0.02), band)
    wear = nb.noise(nb.scale(uv, 4.0), 1.0, 5.0, 0.7)
    col = nb.scale(col, nb.math("MULTIPLY_ADD", wear, 0.5, 0.7))
    nb.output(principled(nb, col, 0.0, 0.55))
    return m


def mat_diamond():
    """Diamond-plate ramp deck: two crossed wave fields make the lozenges."""
    m, nb = material("diamond_plate")
    uv = nb.coords("UV")
    x, y, _ = nb.sep(uv)
    a = nb.math("SINE", nb.math("MULTIPLY", nb.math("ADD", x, y), 22.0))
    b = nb.math("SINE", nb.math("MULTIPLY", nb.math("SUBTRACT", x, y), 22.0))
    lozenge = nb.math("POWER", nb.math("MULTIPLY", nb.math("ADD", a, 1.0), nb.math("ADD", b, 1.0)), 0.5)
    tread = nb.smoothstep(lozenge, 1.45, 1.75)
    scuff = nb.noise(nb.scale(uv, 2.0), 1.0, 4.0, 0.6)
    col = nb.scale((0.30, 0.31, 0.32), nb.math("MULTIPLY_ADD", scuff, 0.5, 0.65))
    nb.output(principled(nb, col, 0.9, nb.math("MULTIPLY_ADD", scuff, 0.2, 0.28), bump(nb, tread, 0.5, 0.004)))
    return m


M_WALL = mat_wall("wall", (0.085, 0.10, 0.12))
M_WALL_DARK = mat_wall("wall_bay", (0.05, 0.058, 0.07), 1.6, 1.6, 0.25)
M_CEIL = mat_wall("ceiling", (0.035, 0.04, 0.045), 3.0, 3.0, 0.2)
M_FLOOR = mat_floor()
M_PAINT = mat_simple("steel_paint", (0.10, 0.115, 0.135), 0.35, 0.45)
M_PAINT_DARK = mat_simple("steel_dark", (0.05, 0.055, 0.06), 0.5, 0.42)
M_STEEL = mat_simple("steel_bare", (0.42, 0.43, 0.44), 1.0, 0.32, 0.1, 6.0)
M_RAIL = mat_simple("rail_paint", (0.16, 0.18, 0.2), 0.4, 0.38, 0.08)
M_CONCRETE = mat_simple("concrete", (0.12, 0.12, 0.12), 0.0, 0.75, 0.25, 1.5)
M_HAZARD = mat_hazard()
M_DIAMOND = mat_diamond()
M_LAMP_WARM = mat_emit("lamp_warm", (1.0, 0.78, 0.5), 12.0)
M_LAMP_COOL = mat_emit("lamp_cool", (0.85, 0.92, 1.0), 3.0)
M_LAMP_RED = mat_emit("lamp_red", (1.0, 0.08, 0.03), 8.0)
M_SCREEN_BLUE = mat_emit("screen_blue", (0.15, 0.45, 1.0), 2.5)
M_SCREEN_GREEN = mat_emit("screen_green", (0.25, 1.0, 0.45), 1.8)
M_SCREEN_AMBER = mat_emit("screen_amber", (1.0, 0.55, 0.1), 2.0)


def mat_glass():
    m, nb = material("window_glass")
    lp = nb.new("ShaderNodeLightPath")
    tr = nb.new("ShaderNodeBsdfTransparent")
    gl = nb.new("ShaderNodeBsdfGlossy", inputs={"Roughness": 0.03, "Color": (1, 1, 1)})
    fr = nb.new("ShaderNodeFresnel", inputs={"IOR": 1.5})
    mix = nb.new("ShaderNodeMixShader")
    nb.set(mix.inputs[0], nb.math("MAXIMUM", fr.outputs[0], 0.04))
    nb.set(mix.inputs[1], tr)
    nb.set(mix.inputs[2], gl)
    # shadow rays pass straight through so the control room light spills out
    mix2 = nb.new("ShaderNodeMixShader")
    nb.set(mix2.inputs[0], lp.outputs["Is Shadow Ray"])
    nb.set(mix2.inputs[1], mix)
    nb.set(mix2.inputs[2], tr)
    nb.output(mix2)
    return m


M_GLASS = mat_glass()

# =================================================================== camera
cam_d = bpy.data.cameras.new("cam")
cam_d.sensor_fit = "HORIZONTAL"
cam_d.sensor_width = 36.0
HFOV = math.radians(67.7)
cam_d.lens = 18.0 / math.tan(HFOV / 2)
cam_d.clip_start = 0.1
cam_d.clip_end = 200.0
# Level camera with lens shift up to the gate centre: verticals stay vertical.
cam_d.shift_y = ((GATE_Z - CAM.z) / (0.0 - CAM.y)) / (2 * math.tan(HFOV / 2))
cam = new_obj("camera", cam_d, loc=CAM, rot=(math.pi / 2, 0, 0))
scene.camera = cam

# ===================================================================== room
plane("floor", (-RX, Y_FRONT, 0), (1, 0, 0), (0, 1, 0), 2 * RX, Y_BACK - Y_FRONT, M_FLOOR)
plane("ceiling", (-RX, Y_BACK, Z_CEIL), (1, 0, 0), (0, -1, 0), 2 * RX, Y_BACK - Y_FRONT, M_CEIL)
plane("wall_back", (-RX, Y_BACK, 0), (1, 0, 0), (0, 0, 1), 2 * RX, Z_CEIL, M_WALL)
plane("wall_front", (RX, Y_FRONT, 0), (-1, 0, 0), (0, 0, 1), 2 * RX, Z_CEIL, M_WALL)
plane("wall_left", (-RX, Y_FRONT, 0), (0, 1, 0), (0, 0, 1), Y_BACK - Y_FRONT, Z_CEIL, M_WALL)

# Right wall with the control-room window cut out of it.
WIN_Y0, WIN_Y1, WIN_Z0, WIN_Z1 = -1.1, 2.2, 4.3, 6.3
plane("wall_right_a", (RX, WIN_Y0, 0), (0, -1, 0), (0, 0, 1), WIN_Y0 - Y_FRONT, Z_CEIL, M_WALL)
plane("wall_right_b", (RX, Y_BACK, 0), (0, -1, 0), (0, 0, 1), Y_BACK - WIN_Y1, Z_CEIL, M_WALL)
plane("wall_right_c", (RX, WIN_Y1, 0), (0, -1, 0), (0, 0, 1), WIN_Y1 - WIN_Y0, WIN_Z0, M_WALL)
plane("wall_right_d", (RX, WIN_Y1, WIN_Z1), (0, -1, 0), (0, 0, 1), WIN_Y1 - WIN_Y0, Z_CEIL - WIN_Z1, M_WALL)


def room_details():
    bm = bmesh.new()
    # --- gate bay: two pilasters and a lintel framing the gate
    for s in (-1, 1):
        x0, x1 = sorted((s * 4.5, s * 5.5))
        bm_box(bm, (x0, 4.15, 0), (x1, Y_BACK, Z_CEIL))
    bm_box(bm, (-5.5, 4.3, 7.9), (5.5, Y_BACK, 9.0))
    # --- back wall corner columns
    for s in (-1, 1):
        x0, x1 = sorted((s * 7.45, s * RX))
        bm_box(bm, (x0, 4.45, 0), (x1, Y_BACK, Z_CEIL))
    # --- side wall columns (I-beam: web + flange)
    for s in (-1, 1):
        for yc in (-5.5, -1.5, 2.5):
            xw0, xw1 = sorted((s * RX, s * (RX - 0.32)))
            bm_box(bm, (xw0, yc - 0.06, 0), (xw1, yc + 0.06, Z_CEIL))
            xf0, xf1 = sorted((s * (RX - 0.32), s * (RX - 0.40)))
            bm_box(bm, (xf0, yc - 0.28, 0), (xf1, yc + 0.28, Z_CEIL))
            bm_box(bm, (min(xw0, xw1), yc - 0.28, 0), (max(xw0, xw1), yc + 0.28, 0.5))  # foot plinth
    # --- ceiling beams across the room
    for yc in (-9.5, -5.5, -1.5, 2.5):
        bm_box(bm, (-RX, yc - 0.22, Z_CEIL - 0.7), (RX, yc + 0.22, Z_CEIL))
    # --- cable trays along the side walls
    for s in (-1, 1):
        x0, x1 = sorted((s * (RX - 0.42), s * (RX - 0.85)))
        bm_box(bm, (x0, Y_FRONT, 6.55), (x1, Y_BACK, 6.62))
        bm_box(bm, (x0, Y_FRONT, 6.62), (x0 + 0.02, Y_BACK, 6.75))
        bm_box(bm, (x1 - 0.02, Y_FRONT, 6.62), (x1, Y_BACK, 6.75))
    o = new_obj("structure", to_mesh("structure", bm, M_PAINT))
    bevel(o, 0.012)

    # --- pipes: along both side walls and across the back above the lintel
    bm = bmesh.new()
    for s in (-1, 1):
        for z, r in ((7.35, 0.11), (7.68, 0.08), (7.95, 0.14)):
            x = s * (RX - 0.62)
            bm_cyl(bm, (x, Y_FRONT, z), (x, Y_BACK - 0.6, z), r, 20)
            bm_cyl(bm, (x, Y_BACK - 0.6, z), (x * 0.0 + s * 5.6, Y_BACK - 0.6, z), r, 20)
            # flanges every 3 m
            for y in np.arange(Y_FRONT + 1.0, Y_BACK - 0.6, 3.0):
                bm_cyl(bm, (x, y - 0.04, z), (x, y + 0.04, z), r * 1.35, 20)
    for z, r in ((9.2, 0.12), (9.35, 0.07)):
        bm_cyl(bm, (-RX, 4.05, z), (RX, 4.05, z), r, 20)
    new_obj("pipes", to_mesh("pipes", bm, M_STEEL, smooth=True, sharp_deg=50))

    # --- vents either side of the bay
    bm = bmesh.new()
    for s in (-1, 1):
        x0, x1 = sorted((s * 5.85, s * 7.15))
        bm_box(bm, (x0 - 0.08, 4.85, 1.4), (x1 + 0.08, Y_BACK, 4.3))
        for k in range(14):
            z = 1.55 + k * 0.195
            bm_box(bm, (x0, 4.80, z), (x1, 4.86, z + 0.06), Matrix.Rotation(0.0, 4, "X"))
    new_obj("vents", to_mesh("vents", bm, M_PAINT_DARK))
    # dark interior behind the slats
    box("vent_back", (-7.15, 4.95, 1.4), (-5.85, 4.99, 4.3), M_PAINT_DARK)
    box("vent_back2", (5.85, 4.95, 1.4), (7.15, 4.99, 4.3), M_PAINT_DARK)

    # --- red warning lamps on the pilasters, cage lamps on the side columns
    bm, bl = bmesh.new(), bmesh.new()
    for s in (-1, 1):
        x = s * 5.0
        bm_cyl(bm, (x, 4.15, 6.2), (x, 4.0, 6.2), 0.16, 24)
        bm_cyl(bl, (x, 4.0, 6.2), (x, 3.92, 6.2), 0.12, 24)
    new_obj("warn_housing", to_mesh("warn_housing", bm, M_PAINT_DARK, smooth=True, sharp_deg=40))
    new_obj("warn_lens", to_mesh("warn_lens", bl, M_LAMP_RED, smooth=True, sharp_deg=40))
    bm, bl = bmesh.new(), bmesh.new()
    for s in (-1, 1):
        for yc in (-5.5, -1.5, 2.5):
            x = s * (RX - 0.40)
            bm_cyl(bm, (x, yc, 5.35), (x - s * 0.32, yc, 5.35), 0.17, 24)
            bm_cyl(bl, (x - s * 0.32, yc, 5.35), (x - s * 0.36, yc, 5.35), 0.13, 24)
    new_obj("lamp_housing", to_mesh("lamp_housing", bm, M_PAINT_DARK, smooth=True, sharp_deg=40))
    new_obj("lamp_lens", to_mesh("lamp_lens", bl, M_LAMP_WARM, smooth=True, sharp_deg=40))

    # --- ceiling light panels between the beams
    bl = bmesh.new()
    for yc in (-7.5, -3.5, 0.5, 4.0):
        for xc in (-4.5, 0.0, 4.5):
            bm_box(bl, (xc - 1.3, yc - 0.45, Z_CEIL - 0.06), (xc + 1.3, yc + 0.45, Z_CEIL - 0.02))
    new_obj("ceiling_panels", to_mesh("ceiling_panels", bl, M_LAMP_COOL))

    # --- blast door on the left wall
    y0, y1, zt = -4.9, -2.1, 3.5
    bm = bmesh.new()
    bm_box(bm, (-RX, y0 - 0.35, 0), (-RX + 0.3, y0, zt + 0.35))
    bm_box(bm, (-RX, y1, 0), (-RX + 0.3, y1 + 0.35, zt + 0.35))
    bm_box(bm, (-RX, y0 - 0.35, zt), (-RX + 0.3, y1 + 0.35, zt + 0.45))
    o = new_obj("door_frame", to_mesh("door_frame", bm, M_PAINT_DARK))
    bevel(o, 0.015)
    door = box("door", (-RX, y0, 0), (-RX + 0.1, y1, zt), M_STEEL)
    bevel(door, 0.01)
    box("door_seam", (-RX + 0.1, (y0 + y1) / 2 - 0.01, 0), (-RX + 0.11, (y0 + y1) / 2 + 0.01, zt), M_PAINT_DARK)
    # hazard stripes up the frame faces
    for yy in (y0 - 0.33, y1 + 0.02):
        plane("door_hazard", (-RX + 0.305, yy, 0.0), (0, 1, 0), (0, 0, 1), 0.31, zt + 0.4, M_HAZARD)
    box("door_lamp", (-RX + 0.3, (y0 + y1) / 2 - 0.25, zt + 0.55), (-RX + 0.42, (y0 + y1) / 2 + 0.25, zt + 0.7), M_LAMP_RED)

    # --- control room behind the right-hand window
    plane("window_glass", (RX - 0.02, WIN_Y0, WIN_Z0), (0, 1, 0), (0, 0, 1), WIN_Y1 - WIN_Y0, WIN_Z1 - WIN_Z0, M_GLASS)
    bm = bmesh.new()
    bm_box(bm, (RX, WIN_Y0 - 0.15, WIN_Z0 - 0.15), (RX + 0.05, WIN_Y1 + 0.15, WIN_Z0))
    bm_box(bm, (RX, WIN_Y0 - 0.15, WIN_Z1), (RX + 0.05, WIN_Y1 + 0.15, WIN_Z1 + 0.15))
    for yy in (WIN_Y0 - 0.15, (WIN_Y0 + WIN_Y1) / 2 - 0.05, WIN_Y1):
        bm_box(bm, (RX - 0.12, yy, WIN_Z0), (RX + 0.05, yy + 0.15, WIN_Z1))
    bm_box(bm, (RX - 0.25, WIN_Y0 - 0.25, WIN_Z0 - 0.3), (RX, WIN_Y1 + 0.25, WIN_Z0 - 0.15))  # sill
    o = new_obj("window_frame", to_mesh("window_frame", bm, M_PAINT_DARK))
    bevel(o, 0.01)
    cr = (RX, RX + 4.0, WIN_Y0 - 0.6, WIN_Y1 + 0.6, WIN_Z0 - 0.6, WIN_Z1 + 1.0)
    bm = bmesh.new()
    bm_box(bm, (cr[0] + 0.05, cr[2], cr[4]), (cr[1], cr[2] + 0.05, cr[5]))
    bm_box(bm, (cr[0] + 0.05, cr[3] - 0.05, cr[4]), (cr[1], cr[3], cr[5]))
    bm_box(bm, (cr[1] - 0.05, cr[2], cr[4]), (cr[1], cr[3], cr[5]))
    bm_box(bm, (cr[0] + 0.05, cr[2], cr[4]), (cr[1], cr[3], cr[4] + 0.05))
    bm_box(bm, (cr[0] + 0.05, cr[2], cr[5] - 0.05), (cr[1], cr[3], cr[5]))
    # consoles: a desk row facing the window
    bm_box(bm, (RX + 0.9, WIN_Y0 - 0.3, cr[4]), (RX + 1.6, WIN_Y1 + 0.3, WIN_Z0 - 0.2))
    new_obj("control_room", to_mesh("control_room", bm, M_PAINT_DARK))
    mats = [M_SCREEN_BLUE, M_SCREEN_GREEN, M_SCREEN_AMBER, M_SCREEN_BLUE]
    for k, yy in enumerate(np.linspace(WIN_Y0 + 0.1, WIN_Y1 - 0.75, 4)):
        plane("screen%d" % k, (RX + 1.25, yy, WIN_Z0 - 0.15), (0, 1, 0), (-0.35, 0, 1), 0.6, 0.42, mats[k])
    for k, yy in enumerate(np.linspace(WIN_Y0 - 0.4, WIN_Y1 - 0.4, 3)):
        plane("wallscreen%d" % k, (cr[1] - 0.06, yy + 1.0, WIN_Z0 + 0.4), (0, -1, 0), (0, 0, 1), 0.9, 0.6,
              M_SCREEN_BLUE if k != 1 else M_SCREEN_AMBER)


room_details()


# ------------------------------------------------------------ platform/ramp
RAMP_Y0, RAMP_Y1 = -0.75, -7.4
RAMP_Z0, RAMP_Z1 = 1.0, 0.28
RAMP_HW = 1.55


def ramp_z(y):
    return RAMP_Z0 + (RAMP_Z1 - RAMP_Z0) * (y - RAMP_Y0) / (RAMP_Y1 - RAMP_Y0)


def platform_and_ramp():
    bm = bmesh.new()
    bm_box(bm, (-3.8, RAMP_Y0, 0), (3.8, 1.4, RAMP_Z0))
    o = new_obj("platform", to_mesh("platform", bm, M_CONCRETE))
    bevel(o, 0.02)
    plane("platform_deck", (-3.8, RAMP_Y0, RAMP_Z0 + 0.003), (1, 0, 0), (0, 1, 0), 7.6, 2.15, M_DIAMOND)
    # ramp wedge
    bm = bmesh.new()
    pts = []
    for x in (-RAMP_HW, RAMP_HW):
        pts += [(x, RAMP_Y0, 0), (x, RAMP_Y1, 0), (x, RAMP_Y1, RAMP_Z1), (x, RAMP_Y0, RAMP_Z0)]
    v = [bm.verts.new(p) for p in pts]
    for f in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
        bm.faces.new([v[i] for i in f])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = new_obj("ramp", to_mesh("ramp", bm, M_PAINT))
    bevel(o, 0.015)
    L = math.hypot(RAMP_Y1 - RAMP_Y0, RAMP_Z1 - RAMP_Z0)
    plane("ramp_deck", (-RAMP_HW + 0.05, RAMP_Y1, RAMP_Z1 + 0.004), (1, 0, 0),
          (0, RAMP_Y0 - RAMP_Y1, RAMP_Z0 - RAMP_Z1), 2 * RAMP_HW - 0.1, L, M_DIAMOND)
    # hazard edging along the ramp sides and the platform lip
    for s in (-1, 1):
        x0 = s * RAMP_HW - (0.18 if s > 0 else 0)
        plane("ramp_edge", (x0, RAMP_Y1, RAMP_Z1 + 0.006), (1, 0, 0),
                  (0, RAMP_Y0 - RAMP_Y1, RAMP_Z0 - RAMP_Z1), 0.18, L, M_HAZARD)
    plane("platform_edge", (-3.8, RAMP_Y0 - 0.002, RAMP_Z0 - 0.18), (1, 0, 0), (0, 0, 1), 7.6, 0.18, M_HAZARD)
    # floor safety lines either side of the ramp
    for s in (-1, 1):
        x0 = s * (RAMP_HW + 0.35) - (0.22 if s > 0 else 0)
        plane("floor_line", (x0, RAMP_Y1 - 0.8, 0.003), (1, 0, 0), (0, 1, 0), 0.22, RAMP_Y0 - RAMP_Y1 + 0.8, M_HAZARD)
    # handrails: posts every ~1.4 m, top and mid rails following the slope
    bm = bmesh.new()
    for s in (-1, 1):
        x = s * (RAMP_HW - 0.07)
        ys = np.linspace(RAMP_Y0 - 0.35, RAMP_Y1 + 0.2, 6)
        for y in ys:
            z = ramp_z(y)
            bm_cyl(bm, (x, y, z), (x, y, z + 1.02), 0.03, 16)
        for h, r in ((1.02, 0.03), (0.52, 0.02)):
            bm_cyl(bm, (x, ys[0], ramp_z(ys[0]) + h), (x, ys[-1], ramp_z(ys[-1]) + h), r, 16)
        # rail along the platform edge out to the gate
        bm_cyl(bm, (x, ys[0], ramp_z(ys[0]) + 1.02), (s * 2.6, RAMP_Y0 + 0.05, RAMP_Z0 + 1.02), 0.03, 16)
        bm_cyl(bm, (s * 2.6, RAMP_Y0 + 0.05, RAMP_Z0), (s * 2.6, RAMP_Y0 + 0.05, RAMP_Z0 + 1.02), 0.03, 16)
    new_obj("handrails", to_mesh("handrails", bm, M_RAIL, smooth=True, sharp_deg=50))


platform_and_ramp()

# ===================================================================== gate
gate = bpy.data.objects.new("Gate", None)
gate.location = (0, 0, GATE_Z)
link(gate)


def glyph_image():
    """39 procedural constellation-style glyphs around the inner track (u =
    angle, v = radius outward). 1 = engraved stroke. Original designs, not
    the show's symbols."""
    cells, cw, ch = TL.GLYPHS, 128, 192
    img = np.zeros((ch, cells * cw), np.float32)
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    rng = np.random.default_rng(1994)

    def seg(a, b, w):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy + 1e-6
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / L2, 0, 1)
        d = np.hypot(xx - ax - t * dx, yy - ay - t * dy)
        return np.clip(w + 0.75 - d, 0, 1)

    for c in range(cells):
        cell = np.zeros((ch, cw), np.float32)
        n = rng.integers(3, 6)
        pts = np.column_stack([rng.uniform(30, cw - 30, n), rng.uniform(42, ch - 42, n)])
        pts = pts[np.argsort(pts[:, 1])]
        for i in range(n - 1):
            cell = np.maximum(cell, seg(pts[i], pts[i + 1], 3.2))
        if rng.random() < 0.6:                     # a branch
            i = rng.integers(0, n)
            q = pts[i] + rng.uniform(-30, 30, 2)
            cell = np.maximum(cell, seg(pts[i], np.clip(q, 24, [cw - 24, ch - 24]), 2.6))
        for p in pts:                              # stars at the joints
            d = np.hypot(xx - p[0], yy - p[1])
            cell = np.maximum(cell, np.clip(6.0 - d, 0, 1))
        if rng.random() < 0.35:                    # a ring
            p = pts[rng.integers(0, n)]
            d = np.hypot(xx - p[0], yy - p[1])
            cell = np.maximum(cell, np.clip(2.6 - np.abs(d - 13), 0, 1))
        # cell frame: grooves down each side and along the track edges
        cell = np.maximum(cell, np.clip(3.0 - np.minimum(xx, cw - 1 - xx), 0, 1))
        cell = np.maximum(cell, np.clip(3.0 - np.abs(yy - 14), 0, 1))
        cell = np.maximum(cell, np.clip(3.0 - np.abs(yy - (ch - 15)), 0, 1))
        img[:, c * cw:(c + 1) * cw] = cell
    im = bpy.data.images.new("glyphs", cells * cw, ch, float_buffer=True)
    rgba = np.repeat(img[..., None], 4, axis=2)
    rgba[..., 3] = 1.0
    im.pixels.foreach_set(rgba.ravel())
    im.pack()
    return im


def mat_naquadah(name, glyphs=None):
    """Dark cast metal of the gate. Concentric grooves come from the gate-
    space radius; the glyph track uses the generated engraving map."""
    m, nb = material(name)
    gp = nb.coords("Object", gate)
    gx, gy, gz = nb.sep(gp)
    r = nb.vmath("LENGTH", nb.comb(gx, 0.0, gz))
    grit = nb.noise(nb.scale(gp, 9.0), 1.0, 5.0, 0.6)
    patina = nb.noise(nb.scale(gp, 0.8), 1.0, 4.0, 0.55)
    height = nb.math("MULTIPLY", grit, 0.25)
    for r0 in (2.535, 3.12, 3.24):
        g = nb.math("SUBTRACT", 1.0, nb.smoothstep(nb.math("ABSOLUTE", nb.math("SUBTRACT", r, r0)), 0.0, 0.012))
        height = nb.math("SUBTRACT", height, g)
    dirt = 0.0
    if glyphs is not None:
        tex = nb.new("ShaderNodeTexImage", image=glyphs, interpolation="Cubic", extension="REPEAT")
        nb.set(tex.inputs[0], nb.coords("UV"))
        stroke = tex.outputs["Color"]
        stroke = nb.math("MULTIPLY", nb.sep(stroke)[0], 1.0)
        height = nb.math("SUBTRACT", height, nb.math("MULTIPLY", stroke, 2.6))
        dirt = stroke
    col = nb.scale((0.06, 0.062, 0.06), nb.math("MULTIPLY_ADD", patina, 0.6, 0.7))
    if glyphs is not None:
        col = nb.scale(col, nb.math("SUBTRACT", 1.0, nb.math("MULTIPLY", dirt, 0.8)))
    rough = nb.math("MULTIPLY_ADD", patina, 0.24, 0.27)
    nb.output(principled(nb, col, 0.92, rough, bump(nb, height, 0.7, 0.006)))
    return m


GLYPHS = glyph_image()
M_GATE = mat_naquadah("naquadah")
M_TRACK = mat_naquadah("glyph_track", GLYPHS)

# Stationary ring: closed cross-section (r, y), front at -y.
OUTER_PROFILE = [
    (2.45, 0.20), (2.45, -0.20), (2.49, -0.25), (2.60, -0.25), (2.615, -0.17),
    (2.615, -0.08), (3.005, -0.08), (3.01, -0.235), (3.05, -0.27), (3.29, -0.27),
    (3.35, -0.21), (3.35, 0.21), (3.29, 0.27), (2.51, 0.27),
]
bm = bmesh.new()
revolve(bm, OUTER_PROFILE, 360)
# raised panels on the outer band between chevrons, and blocks on the rim
for k in range(9):
    c = math.radians(40 * k + 20)
    for a0, a1 in ((-0.27, -0.06), (0.06, 0.27)):
        revolve(bm, [(3.07, -0.27), (3.07, -0.30), (3.26, -0.30), (3.26, -0.27)], 12,
                c + a0, c + a1, cap=True)
    revolve(bm, [(3.345, -0.18), (3.38, -0.18), (3.38, 0.18), (3.345, 0.18)], 10,
            c - 0.10, c + 0.10, cap=True)
outer = new_obj("gate_ring", to_mesh("gate_ring", bm, M_GATE, smooth=True, sharp_deg=35), gate)

# Rotating glyph track with raised dividers between the 39 cells.
bm = bmesh.new()
revolve(bm, [(R_TRACK0 + 0.005, -0.09), (R_TRACK0 + 0.005, -0.195), (R_TRACK0 + 0.025, -0.215),
             (R_TRACK1 - 0.02, -0.215), (R_TRACK1 - 0.002, -0.195), (R_TRACK1 - 0.002, -0.09)],
        TL.GLYPHS * 12, uv=(R_TRACK0 + 0.025, R_TRACK1 - 0.02))
for k in range(TL.GLYPHS):
    th = 2 * math.pi * k / TL.GLYPHS
    bm_box(bm, (-0.016, -0.238, R_TRACK0 + 0.03), (0.016, -0.20, R_TRACK1 - 0.025),
           Matrix.Rotation(th, 4, "Y"), mat_index=1)
# Half a cell round, so a glyph (not a divider) sits under the master chevron
# whenever the ring stops on a whole glyph step.
bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.pi / TL.GLYPHS, 3, "Y"))
track = new_obj("glyph_ring", to_mesh("glyph_ring", bm, [M_TRACK, M_GATE], smooth=True, sharp_deg=35), gate)


# ----------------------------------------------------------------- chevrons
def mat_crystal(name):
    m, nb = material(name)
    level = nb.value("level", 0.0)
    gp = nb.coords("Object")
    n = nb.noise(nb.scale(gp, 30.0), 1.0, 3.0, 0.5)
    glow = nb.math("MULTIPLY", level, nb.math("MULTIPLY_ADD", n, 0.4, 0.8))
    b = principled(nb, (0.30, 0.06, 0.01), 0.0, 0.25)
    nb.set(b.inputs["Emission Color"], (1.0, 0.36, 0.06))
    nb.set(b.inputs["Emission Strength"], nb.math("MULTIPLY", glow, 45.0))
    b.inputs["Coat Weight"].default_value = 1.0
    nb.output(b)
    return m


HOUSING = [(-0.17, 2.96), (0.17, 2.96), (0.31, 3.30), (0.27, 3.53), (-0.27, 3.53), (-0.31, 3.30)]
V_OUT = [(0.0, 2.985), (0.27, 3.45), (0.165, 3.45), (0.0, 3.13), (-0.165, 3.45), (-0.27, 3.45)]
NOTCH = [(0.0, 3.16), (0.14, 3.43), (-0.14, 3.43)]
MASTER_TRAVEL = 0.09
chevrons = {}
for num, ang in TL.CHEVRON_ANGLES.items():
    th = math.radians(ang)
    hous_bm = bmesh.new()
    bm_prism(hous_bm, list(reversed(HOUSING)), -0.36, 0.22)
    bm_box(hous_bm, (-0.2, -0.40, 3.47), (0.2, -0.36, 3.53))
    bm_box(hous_bm, (-0.09, -0.41, 3.36), (0.09, -0.36, 3.43))
    housing = new_obj("chevron%d" % num, to_mesh("chevron%d" % num, hous_bm, M_GATE), gate, rot=(0, th, 0))
    bevel(housing, 0.012)
    mc = mat_crystal("crystal%d" % num)
    vbm = bmesh.new()
    bm_prism(vbm, list(reversed(V_OUT)), -0.445, -0.36, mat_index=1)
    bm_prism(vbm, list(reversed(NOTCH)), -0.47, -0.355, mat_index=0)
    vee = new_obj("chevron%d_v" % num, to_mesh("chevron%d_v" % num, vbm, [M_GATE, mc]), housing)
    bevel(vee, 0.008)
    ld = bpy.data.lights.new("chevron%d_light" % num, "POINT")
    ld.color = (1.0, 0.42, 0.10)
    ld.shadow_soft_size = 0.12
    ld.energy = 0.0
    lo = new_obj("chevron%d_light" % num, ld, vee, loc=(0, -0.75, 3.25))
    chevrons[num] = (housing, vee, mc, ld)


# ------------------------------------------------------------ gate supports
bm = bmesh.new()
for s in (-1, 1):
    th = math.radians(180 - s * 52)
    m = Matrix.Rotation(th, 4, "Y")
    bm_box(bm, (-0.42, -0.42, 3.15), (0.42, 0.42, 3.75), m)
    c = m @ Vector((0, 0, 3.45))
    bm_box(bm, (c.x - 0.35, -0.38, -GATE_Z + RAMP_Z0 - 0.05), (c.x + 0.35, 0.38, c.z), Matrix())
o = new_obj("gate_clamps", to_mesh("gate_clamps", bm, M_PAINT_DARK), gate)
bevel(o, 0.02)


# ----------------------------------------------------------- event horizon
def mat_horizon():
    m, nb = material("event_horizon")
    T = nb.value("time")
    turb = nb.value("turb", 0.3)
    level = nb.value("level", 1.0)
    flash = nb.value("flash", 0.0)
    p = nb.coords("Object")
    x, _, z = nb.sep(p)
    p2 = nb.comb(x, z, 0.0)
    r = nb.vmath("LENGTH", p2)
    rn = nb.math("DIVIDE", r, R_BORE)
    # Radial swell travelling outward, stronger when agitated
    wave = nb.math("SINE", nb.math("SUBTRACT", nb.math("MULTIPLY", r, 5.5), nb.math("MULTIPLY", T, 3.4)))
    amp = nb.math("MULTIPLY_ADD", turb, 0.10, 0.025)
    q = nb.scale(p2, nb.math("MULTIPLY_ADD", wave, amp, 1.0))
    # collapsing: everything is drawn in toward the centre
    q = nb.scale(q, nb.math("DIVIDE", 1.0, nb.math("MAXIMUM", level, 0.05)))
    n1 = nb.noise(nb.scale(q, 1.5), 1.0, 2.0, 0.55, nb.math("MULTIPLY", T, 0.22),
                  distortion=nb.math("MULTIPLY_ADD", turb, 0.9, 0.25))
    n2 = nb.noise(nb.scale(q, 4.2), 1.0, 1.0, 0.5, nb.math("MULTIPLY_ADD", T, 0.36, 11.0),
                  distortion=nb.math("MULTIPLY_ADD", turb, 0.5, 0.1))
    # concentric swells drifting outward, warped by the ripple noise
    phase = nb.math("ADD", nb.math("SUBTRACT", nb.math("MULTIPLY", r, 9.0), nb.math("MULTIPLY", T, 2.2)),
                    nb.math("MULTIPLY", n1, 4.0))
    rings = nb.math("POWER", nb.math("MULTIPLY_ADD", nb.math("SINE", phase), 0.5, 0.5), 7.0)
    lines = nb.math("ADD", nb.math("ADD", nb.ridge(n1, 14.0), nb.math("MULTIPLY", nb.ridge(n2, 9.0), 0.6)),
                    nb.math("MULTIPLY", rings, 0.35))
    deep = nb.lerp((0.004, 0.025, 0.20), (0.02, 0.20, 0.85), nb.smoothstep(n1, 0.3, 0.75))
    centre = nb.math("MULTIPLY_ADD", nb.math("SUBTRACT", 1.0, nb.math("POWER", rn, 2.0)), 0.6, 0.6)
    col = nb.vmath("ADD", nb.scale(deep, centre), nb.scale((0.45, 0.75, 1.0), nb.math("MULTIPLY", lines, nb.math("MULTIPLY_ADD", turb, 0.7, 0.75))))
    # brighter rim where the horizon meets the gate
    rim = nb.smoothstep(rn, 0.9, 1.0)
    col = nb.vmath("ADD", col, nb.scale((0.1, 0.35, 1.0), nb.math("MULTIPLY", rim, 0.45)))
    strength = nb.math("MULTIPLY_ADD", flash, 1.5, 1.0)
    e = nb.new("ShaderNodeEmission")
    nb.set(e.inputs["Color"], col)
    nb.set(e.inputs["Strength"], nb.math("MULTIPLY", strength, 1.15))
    # a wet sheen: the ripple field as a bump, reflecting the room's lights
    gl = nb.new("ShaderNodeBsdfGlossy", inputs={"Roughness": 0.12, "Color": (0.12, 0.16, 0.2)})
    nb.set(gl.inputs["Normal"], bump(nb, nb.math("ADD", n1, nb.math("MULTIPLY", n2, 0.4)), 0.35, 0.05))
    add = nb.new("ShaderNodeAddShader")
    nb.set(add.inputs[0], e)
    nb.set(add.inputs[1], gl)
    e = add
    # radius mask shrinks with level
    edge = nb.math("MULTIPLY", level, R_BORE)
    mask = nb.math("LESS_THAN", r, edge)
    tr = nb.new("ShaderNodeBsdfTransparent")
    mix = nb.new("ShaderNodeMixShader")
    nb.set(mix.inputs[0], mask)
    nb.set(mix.inputs[1], tr)
    nb.set(mix.inputs[2], e)
    nb.output(mix)
    return m


M_EH = mat_horizon()
bm = bmesh.new()
bmesh.ops.create_circle(bm, cap_ends=True, cap_tris=True, segments=256, radius=R_BORE + 0.01,
                        matrix=Matrix.Rotation(math.pi / 2, 4, "X"))
eh = new_obj("event_horizon", to_mesh("event_horizon", bm, M_EH), gate, loc=(0, Y_EH, 0))
# Lit by itself only on camera and glossy rays; the gobo spot does the room.
eh.visible_diffuse = False
eh.visible_shadow = False
eh.visible_transmission = False
eh.visible_volume_scatter = False


def light_horizon_gobo(ld):
    """Spot-light 'gobo' that throws the horizon's rippling caustics on the
    room: a ridge-noise web over the light's emission direction."""
    ld.use_nodes = True
    nt = ld.node_tree
    nt.nodes.clear()
    nb = NB(nt)
    T = nb.value("time")
    turb = nb.value("turb", 0.3)
    d = nb.vmath("NORMALIZE", nb.coords("Normal"))
    n1 = nb.noise(nb.scale(d, 14.0), 1.0, 2.0, 0.5, nb.math("MULTIPLY", T, 0.20),
                  distortion=nb.math("MULTIPLY_ADD", turb, 0.9, 0.25))
    n2 = nb.noise(nb.scale(d, 31.0), 1.0, 1.0, 0.5, nb.math("MULTIPLY_ADD", T, 0.33, 11.0))
    web = nb.math("ADD", nb.ridge(n1, 8.0), nb.math("MULTIPLY", nb.ridge(n2, 5.0), 0.5))
    s = nb.math("MULTIPLY_ADD", web, nb.math("MULTIPLY_ADD", turb, 1.2, 1.6), 0.35)
    # The real source is a 5 m disc, not a point: tame the inverse-square
    # blow-out on the ramp right in front of it.
    fo = nb.new("ShaderNodeLightFalloff")
    nb.set(fo.inputs["Strength"], s)
    fo.inputs["Smooth"].default_value = 6.0
    e = nb.new("ShaderNodeEmission")
    nb.set(e.inputs["Strength"], fo.outputs["Quadratic"])
    out = nb.new("ShaderNodeOutputLight")
    nb.set(out.inputs["Surface"], e)


EH_SPOT = bpy.data.lights.new("eh_spot", "SPOT")
EH_SPOT.color = (0.35, 0.62, 1.0)
EH_SPOT.spot_size = math.radians(172)
EH_SPOT.spot_blend = 0.6
EH_SPOT.shadow_soft_size = 0.05
light_horizon_gobo(EH_SPOT)
# Neither horizon light may show up in reflections: the metal deck and the
# iris must mirror the horizon surface itself (~1.3), not a 5 cm point
# source or a disc several times brighter than it.
new_obj("eh_spot", EH_SPOT, gate, loc=(0, -0.06, 0), rot=(-math.pi / 2, 0, 0)).visible_glossy = False
EH_FILL = bpy.data.lights.new("eh_fill", "AREA")
EH_FILL.shape = "DISK"
EH_FILL.size = 2 * R_BORE - 0.2
EH_FILL.color = (0.30, 0.55, 1.0)
eh_fill = new_obj("eh_fill", EH_FILL, gate, loc=(0, -0.07, 0), rot=(-math.pi / 2, 0, 0))
eh_fill.visible_camera = False
eh_fill.visible_glossy = False


# ---------------------------------------------------------------- kawoosh
def mat_kawoosh():
    m, nb = material("kawoosh")
    T = nb.value("time")
    strength = nb.value("strength", 1.0)
    p = nb.coords("Object")
    x, y, z = nb.sep(p)
    rot = nb.new("ShaderNodeVectorRotate", rotation_type="Y_AXIS")
    nb.set(rot.inputs["Vector"], p)
    nb.set(rot.inputs["Angle"], nb.math("ADD", nb.math("MULTIPLY", y, 3.0), nb.math("MULTIPLY", T, 6.0)))
    q = nb.vmath("MULTIPLY", rot.outputs[0], (1.0, 2.6, 1.0))
    n = nb.noise(nb.scale(q, 1.2), 1.0, 3.0, 0.55, nb.math("MULTIPLY", T, 2.2), distortion=0.6)
    n2 = nb.noise(nb.scale(q, 3.4), 1.0, 2.0, 0.5, nb.math("MULTIPLY_ADD", T, 3.1, 5.0), distortion=0.3)
    fil = nb.math("ADD", nb.ridge(n, 7.0), nb.math("MULTIPLY", nb.ridge(n2, 9.0), 0.6))
    lw = nb.new("ShaderNodeLayerWeight", inputs={"Blend": 0.5})
    edge = lw.outputs["Facing"]
    body = nb.lerp((0.006, 0.05, 0.40), (0.06, 0.32, 1.0), nb.smoothstep(n, 0.35, 0.7))
    col = nb.vmath("ADD", nb.scale(body, nb.math("MULTIPLY_ADD", edge, 1.3, 0.45)),
                   nb.scale((0.42, 0.68, 1.0), nb.math("MULTIPLY", fil, 0.9)))
    e = nb.new("ShaderNodeEmission")
    nb.set(e.inputs["Color"], col)
    nb.set(e.inputs["Strength"], nb.math("MULTIPLY", strength, 0.75))
    # Translucent face-on (the puddle shows through), solid toward the edges.
    # Only the near side of the plume draws, so emission doesn't stack up
    # through both walls.
    alpha = nb.math("MINIMUM", nb.math("MULTIPLY_ADD", edge, 0.6, nb.math("MULTIPLY_ADD", fil, 0.35, 0.3)), 1.0)
    front = nb.math("SUBTRACT", 1.0, nb.new("ShaderNodeNewGeometry").outputs["Backfacing"])
    tr = nb.new("ShaderNodeBsdfTransparent")
    mix = nb.new("ShaderNodeMixShader")
    nb.set(mix.inputs[0], nb.math("MULTIPLY", alpha, front))
    nb.set(mix.inputs[1], tr)
    nb.set(mix.inputs[2], e)
    nb.output(mix)
    return m


M_KW = mat_kawoosh()
bm = bmesh.new()
NS, NT = 72, 144
rings = []
for i in range(NS):
    s = i / (NS - 1) * 0.985
    # bulbous head ~1.17x the bore part-way out: head-on, the burst has to
    # read as the plume ballooning over the ring, not just getting longer
    rho = (R_BORE - 0.06) * (1 - s ** 2.4) ** 0.5 * (1 + 0.32 * math.sin(math.pi * s ** 0.9))
    rings.append([bm.verts.new((rho * math.sin(2 * math.pi * j / NT), -s, rho * math.cos(2 * math.pi * j / NT)))
                  for j in range(NT)])
tip = bm.verts.new((0, -1.0, 0))
for i in range(NS - 1):
    for j in range(NT):
        k = (j + 1) % NT
        bm.faces.new((rings[i][j], rings[i][k], rings[i + 1][k], rings[i + 1][j]))
for j in range(NT):
    bm.faces.new((rings[-1][j], rings[-1][(j + 1) % NT], tip))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
kw = new_obj("kawoosh", to_mesh("kawoosh", bm, M_KW, smooth=True), gate, loc=(0, Y_EH, 0))
kw.visible_shadow = False
kw_driver = bpy.data.objects.new("kawoosh_flow", None)
link(kw_driver, gate)
tex = bpy.data.textures.new("kawoosh_noise", "CLOUDS")
tex.noise_scale = 0.75
tex.noise_depth = 3
disp = kw.modifiers.new("turbulence", "DISPLACE")
disp.texture = tex
disp.texture_coords = "OBJECT"
disp.texture_coords_object = kw_driver
disp.strength = 0.32
disp.mid_level = 0.5
KW_LIGHT = bpy.data.lights.new("kawoosh_light", "POINT")
KW_LIGHT.color = (0.45, 0.68, 1.0)
KW_LIGHT.shadow_soft_size = 1.2
kw_light = new_obj("kawoosh_light", KW_LIGHT, gate)
kw_light.visible_camera = False
kw_light.visible_glossy = False     # lights the room, no hot specular on the horizon


# ------------------------------------------------------------------- iris
def mat_iris():
    m, nb = material("iris_titanium")
    gp = nb.coords("Object", gate)
    gx, _, gz = nb.sep(gp)
    r = nb.vmath("LENGTH", nb.comb(gx, 0.0, gz))
    lp = nb.coords("Object")
    brushed = nb.noise(nb.vmath("MULTIPLY", lp, (40.0, 40.0, 1.5)), 1.0, 3.0, 0.5)
    blot = nb.noise(nb.scale(gp, 1.4), 1.0, 3.0, 0.5)
    col = nb.scale((0.50, 0.52, 0.55), nb.math("MULTIPLY_ADD", blot, 0.3, 0.82))
    # fairly tight lobe: a rougher one sweeps up the overhead key spots at
    # grazing angles and washes the whole face out to white
    b = principled(nb, col, 1.0, nb.math("MULTIPLY_ADD", brushed, 0.10, 0.17),
                   bump(nb, brushed, 0.05, 0.004))
    # blades are only visible inside the bore; outside it they are "in" the gate
    mask = nb.math("LESS_THAN", r, IRIS_MASK_R)
    tr = nb.new("ShaderNodeBsdfTransparent")
    mix = nb.new("ShaderNodeMixShader")
    nb.set(mix.inputs[0], mask)
    nb.set(mix.inputs[1], tr)
    nb.set(mix.inputs[2], b)
    nb.output(mix)
    return m


M_IRIS = mat_iris()
IRIS_D = 2 * math.pi / IRIS_N
IRIS_SPAN = 2.5 * IRIS_D
# Blade edges are spirals (angle grows with radius), so the closed iris is a
# pinwheel and the opening stays round. Each blade pivots about a point on
# its own edge, inside the gate; IRIS_AMAX swings it clear of the bore
# (89.5 deg measured in 2D for these numbers, plus a margin).
IRIS_TWIST = 0.35      # rad per metre
IRIS_RB = 3.4
IRIS_AMAX = math.radians(91.0)
blades = []
for i in range(IRIS_N):
    phi = i * IRIS_D          # math angle (CCW from +X) in the gate's XZ plane
    pa = phi + IRIS_TWIST * IRIS_PIVOT_R
    P = Vector((IRIS_PIVOT_R * math.cos(pa), 0, IRIS_PIVOT_R * math.sin(pa)))
    bm = bmesh.new()
    na, nr = 14, 28
    grid = []
    for a in range(na):
        u = a / (na - 1)
        # stacked like shingles: the further round from its edge, the nearer
        # the room, so every blade overlaps the next consistently
        y = IRIS_Y - IRIS_H * u
        row = []
        for k in range(nr):
            rr = 0.002 + IRIS_RB * k / (nr - 1)
            th = phi + IRIS_TWIST * rr + u * IRIS_SPAN
            row.append(bm.verts.new((rr * math.cos(th) - P.x, y, rr * math.sin(th) - P.z)))
        grid.append(row)
    for a in range(na - 1):
        for k in range(nr - 1):
            bm.faces.new((grid[a][k], grid[a][k + 1], grid[a + 1][k + 1], grid[a + 1][k]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = to_mesh("iris_blade%d" % i, bm, M_IRIS, smooth=True)
    b = new_obj("iris_blade%d" % i, me, gate, loc=P)
    sol = b.modifiers.new("thickness", "SOLIDIFY")
    sol.thickness = 0.008
    sol.offset = 0.0
    blades.append(b)


# =================================================================== lights
def light(name, typ, loc, energy, color, rot=None, size=None, spot=None, blend=0.3, target=None, cam_vis=False):
    ld = bpy.data.lights.new(name, typ)
    ld.energy = energy
    ld.color = color
    if size is not None:
        if typ == "AREA":
            ld.shape = "RECTANGLE"
            ld.size, ld.size_y = size
        else:
            ld.shadow_soft_size = size
    if spot is not None:
        ld.spot_size = math.radians(spot)
        ld.spot_blend = blend
    o = new_obj(name, ld, loc=loc)
    if target is not None:
        d = Vector(target) - Vector(loc)
        o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    elif rot is not None:
        o.rotation_euler = rot
    o.visible_camera = cam_vis
    return ld


for yc in (-7.5, -3.5, 0.5, 4.0):
    for xc in (-4.5, 0.0, 4.5):
        light("ceiling_%g_%g" % (xc, yc), "AREA", (xc, yc, Z_CEIL - 0.08), 50.0, (0.80, 0.88, 1.0),
              rot=(0, 0, 0), size=(2.6, 0.9))
for s in (-1, 1):
    light("gate_key_%d" % s, "SPOT", (s * 4.5, -5.0, 9.1), 3800.0, (0.92, 0.96, 1.0),
          size=0.25, spot=40, blend=0.7, target=(0, 0, GATE_Z))
    for yc in (-5.5, -1.5, 2.5):
        light("wall_lamp_%d_%g" % (s, yc), "SPOT", (s * (RX - 0.8), yc, 5.35), 420.0, (1.0, 0.70, 0.40),
              size=0.12, spot=120, blend=0.8, target=(s * (RX - 1.6), yc, 0.0))
    light("warn_%d" % s, "POINT", (s * 5.0, 3.75, 6.2), 25.0, (1.0, 0.1, 0.04), size=0.1)
# floor-level wash strips grazing up the back wall
for xc in (-6.5, -2.3, 2.3, 6.5):
    light("wallwash_%g" % xc, "AREA", (xc, Y_BACK - 0.25, 0.06), 260.0, (0.30, 0.50, 1.0),
          rot=(math.radians(-160), 0, 0), size=(2.4, 0.08))
# A dim, very large card over the camera: too weak to light the room, it is
# what the metal iris mirrors, so the blades read as brushed titanium (the
# room behind the camera is otherwise near black). Radiance ~ P / (pi * A).
fill = light("gate_fill", "AREA", (0, -12.0, 6.0), 80.0, (0.85, 0.9, 1.0), size=(10.0, 4.5),
             target=(0, 0, GATE_Z))
light("control_room", "AREA", (RX + 2.0, (WIN_Y0 + WIN_Y1) / 2, WIN_Z1 + 0.9), 120.0, (0.55, 0.75, 1.0),
      rot=(0, 0, 0), size=(3.0, 3.0))
light("door_lamp", "POINT", (-RX + 0.6, -3.5, 4.1), 20.0, (1.0, 0.1, 0.04), size=0.1)


# =============================================================== animation
def fcurve_for(idb, path, index):
    act = idb.animation_data.action
    curves = getattr(act, "fcurves", None)
    if curves is None:   # Blender 5 layered actions
        curves = [fc for layer in act.layers for strip in layer.strips
                  for bag in strip.channelbags for fc in bag.fcurves]
    for fc in curves:
        if fc.data_path == path and fc.array_index == index:
            return fc
    raise KeyError(path)


def animate(struct, prop, values, index=-1, constant=False):
    """Key struct.prop (array element `index`) on every loop frame."""
    if index >= 0:
        struct.keyframe_insert(prop, index=index, frame=0)
    else:
        struct.keyframe_insert(prop, frame=0)
    fc = fcurve_for(struct.id_data, struct.path_from_id(prop), max(index, 0))
    kps = fc.keyframe_points
    kps.add(len(values) - len(kps))
    co = np.empty(2 * len(values), np.float32)
    co[0::2] = np.arange(len(values))
    co[1::2] = values
    kps.foreach_set("co", co)
    interp = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["CONSTANT" if constant else "LINEAR"].value
    kps.foreach_set("interpolation", [interp] * len(values))
    fc.update()


times = [f / FPS for f in range(FRAMES)]
st = {
    "ring": [math.radians(TL.ring_angle(t)) for t in times],
    "master": [TL.master_travel(t) for t in times],
    "chev": [TL.chevron_levels(t) for t in times],
    "kw": [TL.kawoosh_length(t) for t in times],
    "eh": [TL.horizon_level(t) for t in times],
    "turb": [TL.horizon_turbulence(t) for t in times],
    "flash": [TL.horizon_flash(t) for t in times],
    "iris": [TL.iris_open(t) for t in times],
}

animate(track, "rotation_euler", st["ring"], index=1)
animate(chevrons[7][1], "location", [-MASTER_TRAVEL * v for v in st["master"]], index=2)
for num, (_h, _v, mc, ld) in chevrons.items():
    lv = [c[num] for c in st["chev"]]
    animate(mc.node_tree.nodes["level"].outputs[0], "default_value", lv)
    animate(ld, "energy", [12.0 * v for v in lv])

# Event horizon: shader clock runs continuously; presence/collapse by level.
nodes = M_EH.node_tree.nodes
animate(nodes["time"].outputs[0], "default_value", times)
animate(nodes["turb"].outputs[0], "default_value", st["turb"])
animate(nodes["level"].outputs[0], "default_value", [max(v, 0.0) for v in st["eh"]])
animate(nodes["flash"].outputs[0], "default_value", st["flash"])
animate(eh, "hide_render", [float(v <= 0) for v in st["eh"]], constant=True)
gnodes = EH_SPOT.node_tree.nodes
animate(gnodes["time"].outputs[0], "default_value", times)
animate(gnodes["turb"].outputs[0], "default_value", st["turb"])
eh_power = [v * v * (1 + 2.5 * fl) for v, fl in zip(st["eh"], st["flash"])]
animate(EH_SPOT, "energy", [2600.0 * p for p in eh_power])
animate(EH_FILL, "energy", [900.0 * p for p in eh_power])

# Kawoosh: scale along -Y is its length; the flow empty streams the
# displacement noise outward and twists it.
kl = st["kw"]
animate(kw, "hide_render", [float(v < 0.03) for v in kl], constant=True)
animate(kw, "scale", [max(v, 0.03) for v in kl], index=1)
bulge = [1.0 for v in kl]
animate(kw, "scale", bulge, index=0)
animate(kw, "scale", bulge, index=2)
animate(kw_driver, "location", [-2.2 * t for t in times], index=1)
animate(kw_driver, "rotation_euler", [1.8 * t for t in times], index=1)
knodes = M_KW.node_tree.nodes
animate(knodes["time"].outputs[0], "default_value", times)
animate(kw_light, "location", [Y_EH - 0.45 * v for v in kl], index=1)
animate(KW_LIGHT, "energy", [6000.0 * min(v / 2.0, 1.0) for v in kl])

# Camera: a short decaying shake as the kawoosh's shockwave hits the room.
rng = np.random.default_rng(7)
knots = rng.normal(size=(64, 2))
def shake(t):
    u = t - (TL.T_KAWOOSH + 0.08)
    if u <= 0 or u > 1.6:
        return 0.0, 0.0
    amp = math.radians(0.22) * math.exp(-u / 0.35) * min(u / 0.04, 1.0)
    x = u * 22.0                      # ~22 wobbles a second, smooth between knots
    i, f = int(x), x - int(x)
    f = f * f * (3 - 2 * f)
    a = knots[i] * (1 - f) + knots[i + 1] * f
    return amp * a[0], amp * a[1] * 0.6
sh = [shake(t) for t in times]
animate(cam, "rotation_euler", [math.pi / 2 + a for a, _ in sh], index=0)
animate(cam, "rotation_euler", [b for _, b in sh], index=2)

# Iris: blade swing from closed (0) to fully retracted (IRIS_AMAX).
alpha = [IRIS_AMAX * v for v in st["iris"]]
for b in blades:
    animate(b, "rotation_euler", alpha, index=1)
    animate(b, "hide_render", [float(v >= 0.9999) for v in st["iris"]], constant=True)

if "--debug-gobo" in argv:
    # Only the horizon's caustic spot, at a fixed strength, to check the gobo.
    for o in scene.objects:
        if o.type == "LIGHT" and o.data != EH_SPOT:
            o.hide_render = True
    for o in scene.objects:
        if o.type == "MESH" and any(m and any(n.type == "EMISSION" for n in m.node_tree.nodes)
                                    for m in o.data.materials):
            o.hide_render = True
            o.animation_data_clear()
    for num in chevrons:
        chevrons[num][2].node_tree.animation_data_clear()
        chevrons[num][2].node_tree.nodes["level"].outputs[0].default_value = 0.0
    EH_SPOT.animation_data_clear()
    EH_SPOT.energy = 4000.0

ONLY_LIGHTS = [x for x in arg("--only-lights", "").split(",") if x]
if ONLY_LIGHTS:
    # look-dev: isolate light objects by name to measure their contribution
    for o in scene.objects:
        if o.type == "LIGHT" and o.name not in ONLY_LIGHTS:
            o.animation_data_clear()
            o.hide_render = True

if SAVE_BLEND:
    bpy.ops.wm.save_as_mainfile(filepath=OUT + "/sgc_gate.blend")


# ================================================================== render
def render_frame(f, path):
    scene.frame_set(f)
    scene.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print("SGC frame %d -> %s  %.1fs" % (f, os.path.basename(path), time.time() - t0), flush=True)


if PREVIEW:
    scene.render.filepath = OUT + "/preview_"
    frames = ONLY or range(FRAMES)
    for f in frames:
        scene.frame_set(f)
        scene.render.filepath = OUT + "/preview_%04d.png" % f
        bpy.ops.render.render(write_still=True)
elif LOOK:
    for t in LOOK:
        f = int(round(t * FPS))
        render_frame(f, OUT + "/look_%05.2f.exr" % t)
else:
    keys = [TL.state_key(t) for t in times]
    source = []
    for f in range(FRAMES):
        source.append(f if f == 0 or keys[f] != keys[f - 1] else source[-1])
    # The loop's last idle run is the same state as the first one.
    last = FRAMES - 1
    while last > 0 and keys[last] == keys[0] and source[last] != 0:
        source[last] = 0
        last -= 1
    with open(OUT + "/manifest.json", "w") as fh:
        json.dump({"frames": FRAMES, "fps": FPS, "width": W, "height": H,
                   "source": source, "flash": st["flash"], "kawoosh": st["kw"],
                   "horizon": st["eh"]}, fh)
    todo = sorted(set(source))
    if ONLY:
        todo = [f for f in todo if f in ONLY]
    print("SGC rendering %d of %d frames" % (len(todo), FRAMES), flush=True)
    for f in todo:
        path = OUT + "/sgc_%04d.exr" % f
        if SKIP_EXISTING and os.path.exists(path):
            continue
        render_frame(f, path)
