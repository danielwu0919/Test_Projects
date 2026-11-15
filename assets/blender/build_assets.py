"""Builds every 3D asset for Lewis's An-225 Adventure and renders the game sprites.

Run from the project folder:

    blender -b --factory-startup --python assets/blender/build_assets.py

Optional: add `-- plane bird` to rebuild only assets whose name starts with those words
(the .blend and the metadata are only written on a full build).

Outputs, relative to this file:
    an225_assets.blend              every model, one collection per asset
    ../sprites/*.png                transparent sprites, SCALE pixels per game unit
    ../sprites/sprites.js / .json   sprite sizes, anchors and named points for the two games

Models are built in game units (1 unit = 1 px on the 800x450 playfield), with the nose
along +X and up along +Z.  Sprites are rendered with an orthographic camera looking from
the -Y side, turned AZ degrees towards the nose and raised EL degrees.
"""
import json
import math
import os
import sys
import warnings

import bmesh
import bpy
from mathutils import Matrix, Vector

warnings.filterwarnings('ignore', category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, '..', 'sprites'))
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
ONLY = [a for a in ARGS if not a.startswith('-')]

SCALE = 3                      # sprite pixels per game unit
EL = math.radians(15)          # camera elevation
AZ = math.radians(22)          # camera turn towards +X (use -AZ for things that fly left)
PAD = 3                        # transparent margin around each sprite, in game units
TAU = math.tau


# ───────────────────────────────────────────────────────────── small helpers
def T(x=0.0, y=0.0, z=0.0):
    return Matrix.Translation((x, y, z))


def R(axis, deg):
    return Matrix.Rotation(math.radians(deg), 4, axis)


def S(x, y, z):
    return Matrix.Diagonal((x, y, z, 1))


ALONG_X = R('Y', 90)           # turns a Z-axis primitive to lie along X
ALONG_Y = R('X', 90)


def lin(hexcol):
    """sRGB hex colour -> linear RGBA, which is what Blender materials expect."""
    n = int(hexcol[1:], 16)

    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(n >> 16), f((n >> 8) & 255), f(n & 255), 1.0)


def basis(view):
    """Camera axes for a view = (azimuth, level): D points from the subject to the camera,
    Rt is screen-right and Up is screen-up.  A raised, turned camera makes the model's X axis
    slope on screen; level=True rolls the camera so aircraft still fly dead level."""
    az, level = view
    d = Vector((math.sin(az) * math.cos(EL), -math.cos(az) * math.cos(EL), math.sin(EL)))
    if level:
        x = Vector((1.0, 0.0, 0.0))
        rt = (x - d * x.dot(d)).normalized()
    else:
        rt = Vector((math.cos(az), math.sin(az), 0.0))
    return d, rt, d.cross(rt)


FLYING = (AZ, True)            # player's plane: seen slightly from the front, flying level
ONCOMING = (-AZ, True)         # things that fly left towards the player
STANDING = (AZ, False)         # scenery: world-up stays up


def project(p, view):
    """Model-space point -> game offset from the sprite anchor (x right, y down)."""
    _, rt, up = basis(view)
    p = Vector(p)
    return (round(p.dot(rt), 2), round(-p.dot(up), 2))


# ───────────────────────────────────────────────────────────────── materials
MATS = {}


def principled(m):
    return next(n for n in m.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')


def mat(name, color='#ffffff', rough=0.6, metal=0.0, emit=0.0, emit_color=None):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = principled(m)
    b.inputs['Base Color'].default_value = lin(color)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if emit:
        b.inputs['Emission Color'].default_value = lin(emit_color or color)
        b.inputs['Emission Strength'].default_value = emit
    m.diffuse_color = lin(color)
    MATS[name] = m
    return m


class Nodes:
    """Tiny wrapper for wiring procedural colour masks into a material."""

    def __init__(self, m):
        self.m, self.nt = m, m.node_tree
        self.bsdf = principled(m)
        tc = self.nt.nodes.new('ShaderNodeTexCoord')
        sep = self.nt.nodes.new('ShaderNodeSeparateXYZ')
        self.nt.links.new(tc.outputs['Object'], sep.inputs[0])
        self.coords = tc.outputs['Object']
        self.x, self.y, self.z = sep.outputs
        self.color = None

    def math(self, op, a, b=None):
        n = self.nt.nodes.new('ShaderNodeMath')
        n.operation = op
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                self.nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    def band(self, sock, lo, hi):
        return self.math('MULTIPLY', self.math('GREATER_THAN', sock, lo), self.math('LESS_THAN', sock, hi))

    def curve(self, sock, pts):
        """Piecewise-linear function of a socket through (input, value) points; flat beyond the ends."""
        pts = sorted(pts)
        out = pts[0][1]
        for (x0, v0), (x1, v1) in zip(pts, pts[1:]):
            n = self.nt.nodes.new('ShaderNodeMapRange')
            n.clamp = True
            self.nt.links.new(sock, n.inputs[0])
            for i, v in enumerate((x0, x1, 0.0, v1 - v0), 1):
                n.inputs[i].default_value = v
            out = self.math('ADD', out, n.outputs[0])
        return out

    def both(self, a, b):
        return self.math('MULTIPLY', a, b)

    def paint(self, hexcol, mask):
        """Lay a colour over what is there so far, wherever mask is 1."""
        mix = self.nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        a, b = [s for s in mix.inputs if s.type == 'RGBA'][:2]
        self.nt.links.new(mask, mix.inputs[0])
        if self.color is None:
            a.default_value = self.bsdf.inputs['Base Color'].default_value
        else:
            self.nt.links.new(self.color, a)
        b.default_value = lin(hexcol)
        self.color = next(s for s in mix.outputs if s.type == 'RGBA')
        self.nt.links.new(self.color, self.bsdf.inputs['Base Color'])

    def shade(self, mask, amount):
        """Darken the colour so far by `amount` wherever mask is 1 (panel lines, grime)."""
        mix = self.nt.nodes.new('ShaderNodeMix')
        mix.data_type, mix.blend_type = 'RGBA', 'MULTIPLY'
        a, b = [s for s in mix.inputs if s.type == 'RGBA'][:2]
        mix.inputs[0].default_value = 1.0
        if self.color is None:
            a.default_value = self.bsdf.inputs['Base Color'].default_value
        else:
            self.nt.links.new(self.color, a)
        self.nt.links.new(self.math('SUBTRACT', 1.0, self.math('MULTIPLY', mask, amount)), b)
        self.color = next(s for s in mix.outputs if s.type == 'RGBA')
        self.nt.links.new(self.color, self.bsdf.inputs['Base Color'])

    def drive(self, name, mask, on):
        """Push a BSDF input (roughness, metallic, ...) to `on` wherever mask is 1."""
        inp = self.bsdf.inputs[name]
        prev = inp.links[0].from_socket if inp.is_linked else inp.default_value
        keep = self.math('MULTIPLY', prev, self.math('SUBTRACT', 1.0, mask))
        self.nt.links.new(self.math('ADD', keep, self.math('MULTIPLY', mask, on)), inp)

    def noise(self, sx, sy, sz, detail=3.0):
        """0..1 noise over object coordinates, stretched by the three scales."""
        vm = self.nt.nodes.new('ShaderNodeVectorMath')
        vm.operation = 'MULTIPLY'
        self.nt.links.new(self.coords, vm.inputs[0])
        vm.inputs[1].default_value = (sx, sy, sz)
        n = self.nt.nodes.new('ShaderNodeTexNoise')
        self.nt.links.new(vm.outputs[0], n.inputs['Vector'])
        n.inputs['Scale'].default_value, n.inputs['Detail'].default_value = 1.0, detail
        return n.outputs[0]

    def gores(self, count):
        """1 on every other slice around the Z axis (balloon and parachute panels)."""
        ang = self.math('ADD', self.math('ARCTAN2', self.y, self.x), math.pi)
        return self.math('GREATER_THAN', self.math('FRACT', self.math('MULTIPLY', ang, count / TAU)), 0.5)


# ────────────────────────────────────────────────────────────────── geometry
class Build:
    """Collects primitives and lofts into one mesh object with several materials."""

    def __init__(self, name):
        self.name, self.bm, self.mats = name, bmesh.new(), []

    def _slot(self, m):
        if m not in self.mats:
            self.mats.append(m)
        return self.mats.index(m)

    def _own(self, verts, m, smooth=True):
        idx = self._slot(m)
        for f in {f for v in verts for f in v.link_faces}:
            f.material_index, f.smooth = idx, smooth

    def cone(self, r1, r2, depth, M, m, seg=24, smooth=True):
        r = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=seg,
                                  radius1=r1, radius2=r2, depth=depth, matrix=M)
        self._own(r['verts'], m, smooth)

    def sphere(self, radius, M, m, seg=28):
        r = bmesh.ops.create_uvsphere(self.bm, u_segments=seg, v_segments=seg // 2, radius=radius, matrix=M)
        self._own(r['verts'], m)

    def ico(self, radius, M, m, sub=3):
        r = bmesh.ops.create_icosphere(self.bm, subdivisions=sub, radius=radius, matrix=M)
        self._own(r['verts'], m)

    def box(self, sx, sy, sz, M, m):
        r = bmesh.ops.create_cube(self.bm, size=1.0, matrix=M @ S(sx, sy, sz))
        self._own(r['verts'], m, smooth=False)

    def loft(self, rings, m, caps=(True, True), mats=None, smooth=True):
        """Skin a list of equal-length point rings; mats optionally gives a material per segment."""
        vr = [[self.bm.verts.new(p) for p in ring] for ring in rings]
        n = len(rings[0])
        for i in range(len(vr) - 1):
            idx = self._slot(mats[i] if mats else m)
            for j in range(n):
                k = (j + 1) % n
                f = self.bm.faces.new((vr[i][j], vr[i][k], vr[i + 1][k], vr[i + 1][j]))
                f.material_index, f.smooth = idx, smooth
        for want, ring, seg in ((caps[0], vr[0], 0), (caps[1], vr[-1], -1)):
            if want:
                f = self.bm.faces.new(ring)
                f.material_index, f.smooth = self._slot(mats[seg] if mats else m), False

    def revolve(self, profile, m, M=None, seg=28, caps=(True, True), mats=None):
        """Lathe a list of (height, radius) pairs around Z, then place it with matrix M."""
        M = M or Matrix.Identity(4)
        rings = [[M @ Vector((r * math.cos(TAU * i / seg), r * math.sin(TAU * i / seg), h)) for i in range(seg)]
                 for h, r in profile]
        self.loft(rings, m, caps, mats)

    def outline(self, pts, y0, y1, m):
        """Extrude a flat XZ outline between two Y depths (power-up icons)."""
        front = [Vector((x, y0, z)) for x, z in pts]
        back = [Vector((x, y1, z)) for x, z in pts]
        self.loft([front, back], m, smooth=False)

    def finish(self, coll, origin, subsurf=0, sharp=40):
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces[:])
        me = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(m)
        if hasattr(me, 'set_sharp_from_angle'):
            me.set_sharp_from_angle(angle=math.radians(sharp))
        ob = bpy.data.objects.new(self.name, me)
        ob.location = origin
        coll.objects.link(ob)
        if subsurf:
            mod = ob.modifiers.new('Subdivision', 'SUBSURF')
            mod.levels = mod.render_levels = subsurf
        return ob


def smooth_profile(pts, steps=4):
    """Catmull-Rom resample of a lathe profile so big round shapes shade without banding."""
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, len(pts) - 1)]
        for k in range(steps):
            t = k / steps
            out.append(tuple(0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                                    + (3 * b - a - 3 * c + d) * t ** 3) for a, b, c, d in zip(p0, p1, p2, p3)))
    return out + [pts[-1]]


def ring_yz(x, zc, hw, hh, n=28, power=2.4):
    """Rounded-box cross-section in the YZ plane (fuselage slices)."""
    pts = []
    for i in range(n):
        c, s = math.cos(TAU * i / n), math.sin(TAU * i / n)
        pts.append(Vector((x, hw * math.copysign(abs(c) ** (2 / power), c),
                           zc + hh * math.copysign(abs(s) ** (2 / power), s))))
    return pts


def airfoil(span, x_le, x_te, lift, thick, axis='Y', n=18):
    """One wing section. axis='Y': span runs along Y and thickness along Z (wings);
    axis='Z': span runs along Z and thickness along Y (fins, where `lift` is the Y position)."""
    pts = []
    for i in range(n):
        t = TAU * i / n
        u = 0.5 + 0.5 * math.cos(t)                               # 1 at the leading edge
        x = x_te + (x_le - x_te) * u
        off = thick * 0.5 * math.sin(t) * (0.3 + 0.7 * math.sqrt(u))
        pts.append(Vector((x, span, lift + off)) if axis == 'Y' else Vector((x, lift + off, span)))
    return pts


# ─────────────────────────────────────────────────────────── scene + sprites
ASSETS = []        # (name, collection)
ASSET_ORIGIN = {}  # collection name -> where that model sits in the .blend
SPRITES = []       # render jobs
_slot_count = [0]


def new_asset(name):
    """A collection for one model, parked at its own spot so the .blend is easy to browse."""
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    i = _slot_count[0]
    _slot_count[0] += 1
    origin = Vector((0, 0, 0)) if i == 0 else Vector((((i - 1) % 6 - 2.5) * 110, 260 + ((i - 1) // 6) * 130, 0))
    ASSETS.append((name, coll))
    ASSET_ORIGIN[coll.name] = origin
    return coll, origin


def sprite(name, show, origin, view=STANDING, frame=None, points=None, photo=False, px=SCALE, shade=()):
    """Queue a render: `show` collections are visible, `frame` collections set the canvas size.
    photo=True path-traces the sprite under a real sky at `px` pixels per game unit; `shade`
    collections stay out of the picture but still cast their shadows into it."""
    SPRITES.append(dict(name=name, show=show, frame=frame or show, origin=origin, view=view,
                        points=points or {}, photo=photo, px=px, shade=list(shade)))


def wanted(name):
    return not ONLY or any(name.startswith(o) for o in ONLY)


# ════════════════════════════════════════════════════════════ the An-225
# Proportions follow the real aircraft at 224 units for its 84 m length (2.67 units per metre):
# 88.4 m wing span, 32.65 m tailplane, 18.1 m to the top of the twin fins, six D-18T turbofans,
# 28 main wheels and 4 nose wheels.  Only the wheels and the ground clearance are oversized a
# little, so that the landing gear still reads at game size.  The paint is the aircraft's last
# livery: white, with a yellow-over-blue swoosh from the nose that thins out towards the tail.
# Unlike the other props, the plane is rendered photo-realistically: glossy paint with panel
# lines and a little grime, bare-metal leading edges, reflective glass, and a path-traced
# render under a sky (see render_mode).
PLANE_PX = 6                   # the plane's sprites get twice the resolution of the rest
BLUE, YELLOW = '#0b55b0', '#ffd21a'
FUSELAGE = [  # x, centre z, half width, half height
    (112, -2.4, 1.5, 1.5), (110.8, -2.2, 4.4, 4.2), (107.5, -1.5, 6.6, 6.5), (102, -0.5, 8.2, 8.4),
    (96, 0.3, 9.2, 9.8), (88, 0.8, 9.8, 10.7), (78, 1.0, 10.0, 11.0), (30, 1.0, 10.0, 11.0),
    (-20, 1.0, 10.0, 11.0), (-45, 2.2, 9.6, 9.9), (-65, 4.2, 8.6, 7.8), (-82, 6.2, 7.2, 5.6),
    (-97, 7.8, 5.6, 3.6), (-108, 8.8, 4.2, 2.0), (-113, 9.1, 2.7, 1.5), (-118, 9.3, 1.3, 0.9),
    (-121, 9.4, 0.3, 0.3),                                 # the pointed tail cone pokes out past the fins
]
HALF_SPAN = 118
WING_KINK = 45                 # the trailing edge sweeps back harder outboard of this
ENGINE_Y = (32, 53, 74)
FLAP_TRACK_Y = (24, 45, 66, 87, 106)
TAIL_HALF_SPAN = 43.5
WHEEL_Z = -18.5                # bottom of the tyres with the gear down
MAIN_WHEEL_X = [-24 + 6 * i for i in range(7)]
NOSE_WHEEL_X = 82


def wing_le(y):
    return 34 - abs(y) * 0.62


def wing_te(y):
    a = abs(y)
    return -9 - a * 0.28 if a < WING_KINK else -9 - WING_KINK * 0.28 - (a - WING_KINK) * 0.403


def wing_z(y):
    return 12.6 - abs(y) * 0.09                            # anhedral: the tips droop


def engine_centre(y):
    return (wing_le(y) + 7, y, wing_z(y) - 6.8)


def tail_le(y):
    return -85 - abs(y) * 0.36


def tail_te(y):
    return -108.5 - abs(y) * 0.12


def tail_z(y):
    return 10.4 + abs(y) * 0.06                            # the tailplane has a little dihedral


def fuselage_side(x, z):
    """Half width of the fuselage skin at station x and height z (0 above or below it)."""
    secs = sorted(FUSELAGE)
    for (x0, zc0, hw0, hh0), (x1, zc1, hw1, hh1) in zip(secs, secs[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            zc, hw, hh = zc0 + (zc1 - zc0) * t, hw0 + (hw1 - hw0) * t, hh0 + (hh1 - hh0) * t
            k = abs(z - zc) / hh
            return hw * (1 - k ** 2.2) ** (1 / 2.2) if k < 1 else 0.0
    return 0.0


def build_plane():
    body_coll, origin = new_asset('An225')
    gear_coll, _ = new_asset('An225_Gear')
    _slot_count[0] -= 1                                    # the gear shares the plane's spot
    ASSET_ORIGIN[gear_coll.name] = origin

    def lacquer(m, coat=0.5):
        """Aircraft paint: a clear glossy coat over the colour."""
        inputs = principled(m).inputs
        for name, value in (('Coat Weight', coat), ('Coat Roughness', 0.08)):
            if name in inputs:
                inputs[name].default_value = value
        return m

    def skin(name, edge_x):
        """Painted wing or tail skin whose leading edge is bare metal.  edge_x(nodes) gives the
        x where the paint stops."""
        m = lacquer(mat(name, '#e9ecf0', rough=0.4), 0.35)
        n = Nodes(m)
        metal = n.math('GREATER_THAN', n.x, edge_x(n))
        n.paint('#c9ced5', metal)
        n.drive('Metallic', metal, 0.55)
        n.drive('Roughness', metal, 0.42)
        n.shade(n.noise(0.5, 0.06, 0.5), 0.06)             # faint chordwise streaks
        return m, n

    wing_m, wn = skin('an225_wing', lambda n: n.math('SUBTRACT', 34 - 2.4,
                                                     n.math('MULTIPLY', n.math('ABSOLUTE', n.y), 0.62)))
    # flap, aileron and spoiler outlines on the wing
    span = wn.math('ABSOLUTE', wn.y)
    hinge = wn.curve(span, [(0, 3.0), (WING_KINK, -13.8), (HALF_SPAN, -47.7)])
    spoiler = wn.curve(span, [(0, 8.2), (WING_KINK, -10.5), (HALF_SPAN, -46.3)])
    seams = wn.both(wn.math('GREATER_THAN', span, 11),
                    wn.band(wn.x, wn.math('SUBTRACT', hinge, 0.1), wn.math('ADD', hinge, 0.1)))
    seams = wn.math('MAXIMUM', seams, wn.both(wn.band(span, 13, 92), wn.band(
        wn.x, wn.math('SUBTRACT', spoiler, 0.08), wn.math('ADD', spoiler, 0.08))))
    for y in (30, 45, 62, 80, 98):
        seams = wn.math('MAXIMUM', seams, wn.both(wn.math('LESS_THAN', wn.x, hinge), wn.band(span, y - 0.09, y + 0.09)))
    wn.shade(seams, 0.32)
    tail_m, _ = skin('an225_tailplane', lambda n: n.math('SUBTRACT', -85 - 1.8,
                                                         n.math('MULTIPLY', n.math('ABSOLUTE', n.y), 0.36)))
    fin_m, _ = skin('an225_fin', lambda n: n.math('SUBTRACT', n.curve(
        n.z, [(6.5, -103), (13, -95.5), (25, -101.5), (34, -107.5)]), 1.5))

    lip = mat('an225_intake_lip', '#dfe4ea', rough=0.16, metal=1.0)
    fan_m = mat('an225_fan', '#4d535b', rough=0.32, metal=0.9)
    dark = mat('an225_dark', '#0e1014', rough=0.6)
    hot = mat('an225_nozzle', '#4a4f57', rough=0.38, metal=0.9)
    blue, yellow = lacquer(mat('an225_blue', BLUE, rough=0.4)), lacquer(mat('an225_yellow', YELLOW, rough=0.4))
    white = lacquer(mat('an225_white', '#f7f8fa', rough=0.35))
    tip_red = lacquer(mat('an225_wingtip', '#e8452c', rough=0.4))

    # Fuselage paint is procedural (masks on object coordinates).  The blue band runs the whole
    # length: thick under the nose, dipping over the gear sponsons, then a thin line climbing the
    # upswept tail.  Yellow sits on top of it at the front and fades out by the wing; a thin
    # yellow line runs underneath it at the back.  Everything else is white.
    fus = lacquer(mat('an225_fuselage', '#f7f8fa', rough=0.32))
    nd = Nodes(fus)
    top = nd.curve(nd.x, [(-121, 9.6), (-112, 9.1), (-97, 6.6), (-82, 3.3), (-65, -0.7), (-45, -4.6), (-15, -4.8),
                          (20, -3.8), (60, -2.8), (96, -3.0), (104, -3.6), (112, -5.0)])
    bot = nd.curve(nd.x, [(-121, 9.2), (-112, 8.6), (-97, 5.9), (-82, 2.5), (-65, -1.6), (-45, -5.6), (-15, -6.6),
                          (20, -6.0), (60, -5.4), (96, -5.8), (104, -6.4), (112, -7.6)])
    thick = nd.curve(nd.x, [(-45, 0.0), (-15, 0.9), (20, 1.6), (60, 2.4), (112, 2.8)])
    nd.paint(BLUE, nd.band(nd.z, bot, top))
    nd.paint(YELLOW, nd.band(nd.z, top, nd.math('ADD', top, thick)))
    nd.paint(YELLOW, nd.both(nd.band(nd.x, -104, -25),
                             nd.band(nd.z, nd.math('SUBTRACT', bot, 1.0), nd.math('SUBTRACT', bot, 0.4))))
    # The cockpit: a row of side windows under the roof line that wraps round the front as the
    # windscreen, split into panes by white posts.  It is taller than life so that it still
    # reads as a cockpit at game size.
    roof = nd.curve(nd.x, [(86, 11.6), (96, 10.1), (102, 7.9), (107.5, 5.0)])
    sill = nd.math('SUBTRACT', roof, 4.2)
    side = nd.both(nd.band(nd.x, 87.5, 99.5), nd.band(nd.z, sill, nd.math('SUBTRACT', roof, 1.2)))
    front = nd.both(nd.band(nd.x, 99.5, 103.6), nd.math('GREATER_THAN', nd.z, sill))
    cockpit = nd.math('MAXIMUM', side, front)
    nd.paint('#0d1b2c', cockpit)
    posts = nd.band(nd.y, -0.25, 0.25)                     # the windscreen's centre post
    for x in (90.5, 93.5, 96.5, 99.5):
        posts = nd.math('MAXIMUM', posts, nd.band(nd.x, x - 0.2, x + 0.2))
    posts = nd.both(posts, nd.both(nd.math('GREATER_THAN', nd.z, sill), nd.band(nd.x, 87.5, 103.6)))
    nd.paint('#f7f8fa', posts)
    glass = nd.both(cockpit, nd.math('SUBTRACT', 1.0, posts))
    nd.drive('Roughness', glass, 0.02)                     # the panes mirror the sky
    # skin detail: frame and stringer lines, the joint of the lifting nose, a crew door and a
    # little grime running down the sides
    painted = nd.math('SUBTRACT', 1.0, cockpit)
    frames = nd.math('LESS_THAN', nd.math('FRACT', nd.math('DIVIDE', nd.x, 7.0)), 0.016)
    stringers = nd.math('LESS_THAN', nd.math('FRACT', nd.math('DIVIDE', nd.math('ADD', nd.z, 20.3), 5.5)), 0.02)
    nd.shade(nd.both(painted, nd.math('MAXIMUM', frames, stringers)), 0.12)
    nd.shade(nd.band(nd.x, 85.2, 85.45), 0.4)
    door = nd.math('SUBTRACT', nd.both(nd.band(nd.x, 72.6, 76.4), nd.band(nd.z, -7.2, 0.4)),
                   nd.both(nd.band(nd.x, 72.76, 76.24), nd.band(nd.z, -7.04, 0.24)))
    nd.shade(door, 0.4)
    nd.shade(nd.noise(0.7, 0.7, 0.07), 0.07)

    def nacelle_paint(i, cx, cz):
        """White cowl with a small blue-and-yellow flash across its side."""
        m = lacquer(mat(f'an225_nacelle_{i}', '#f1f4f7', rough=0.35))
        n = Nodes(m)
        u = n.math('ADD', n.math('SUBTRACT', n.z, cz), n.math('MULTIPLY', n.math('SUBTRACT', n.x, cx + 2), 0.5))
        on = n.band(n.x, cx - 3.5, cx + 6.5)
        n.paint(BLUE, n.both(on, n.band(u, -0.2, 0.9)))
        n.paint(YELLOW, n.both(on, n.band(u, -1.1, -0.2)))
        return m

    nacelles = [nacelle_paint(i, *engine_centre(y)[::2]) for i, y in enumerate(ENGINE_Y)]

    b = Build('An225_Fuselage')
    b.loft([ring_yz(*s, n=40, power=2.2) for s in FUSELAGE], fus)
    fuselage = b.finish(body_coll, origin, subsurf=2)

    def foil(*args, **kw):                                 # wing sections, finely divided
        return airfoil(*args, n=40, **kw)

    b = Build('An225_Airframe')
    # main wing: one skin from the near tip to the far tip, swept, tapered and drooping
    stations = [-HALF_SPAN, -82, -WING_KINK, -12, 0, 12, WING_KINK, 82, HALF_SPAN]
    b.loft([foil(y, wing_le(y), wing_te(y), wing_z(y), 5.2 - abs(y) * 0.031) for y in stations], wing_m)
    for side in (-1, 1):
        for fy in FLAP_TRACK_Y:                            # flap-track fairings under the trailing edge
            y = side * fy
            b.sphere(1, T(wing_te(y) + 6.5, y, wing_z(y) - 2.5) @ S(9, 1.1, 1.7), wing_m, seg=14)
        tip, cap = side * HALF_SPAN, side * (HALF_SPAN + 2.6)                  # red-orange wing tips
        b.loft([foil(tip, wing_le(tip), wing_te(tip), wing_z(tip), 1.6),
                foil(cap, wing_le(tip) - 3.5, wing_te(tip) + 2.0, wing_z(cap), 0.7)], tip_red)
    # wing-root fairing, the gear sponsons along the lower fuselage and the old Buran mounts on top
    b.sphere(1, T(10, 0, 7.0) @ S(48, 8.6, 8.6), fus)
    for side in (-1, 1):
        b.sphere(1, T(-5, side * 8.4, -7.8) @ S(44, 5.2, 6.0), fus)
        for x, y in ((50, 3.4), (-46, 4.6)):
            b.sphere(1, T(x, side * y, 11.5) @ S(3.6, 0.9, 1.1), fus, seg=14)

    # six turbofans on pylons, three under each wing: a short fan cowl with the core poking out behind
    for side in (-1, 1):
        for nacelle, ey in zip(nacelles, ENGINE_Y):
            cx, cy, cz = engine_centre(side * ey)
            M = T(cx, cy, cz) @ ALONG_X
            b.revolve([(-5.0, 3.5), (-3.8, 4.1), (1.0, 4.5), (5.5, 4.4), (7.6, 4.1)], nacelle, M,
                      seg=48, caps=(True, False))
            b.revolve([(7.6, 4.1), (8.8, 3.7), (9.2, 3.2), (8.8, 2.8), (5.5, 2.7)], lip, M, seg=48,
                      caps=(False, True), mats=[lip, lip, lip, dark])      # bare-metal lip, dark fan well
            b.cone(1.3, 0.2, 2.8, T(cx + 6.6, cy, cz) @ ALONG_X, lip, seg=24)          # spinner
            for k in range(26):                                                        # fan blades
                b.box(0.55, 0.07, 1.5, T(cx + 5.85, cy, cz) @ R('X', 360 * k / 26) @ T(0, 0, 1.95) @ R('Z', 32),
                      fan_m)
            b.revolve([(-10.0, 1.8), (-5.0, 2.8)], white, M, seg=40, caps=(True, False))   # core cowl
            b.cone(0.3, 1.5, 4.0, T(cx - 11.8, cy, cz) @ ALONG_X, hot, seg=32)         # exhaust plug
            top = wing_z(cy) - 0.6
            b.loft([[Vector((x, cy + w, z)) for w, z in ((-0.6, z0), (0.6, z0), (0.6, z1), (-0.6, z1))]
                    for x, z0, z1 in ((cx + 7.5, cz + 3.6, cz + 4.1), (cx - 1.0, cz + 3.6, top),
                                      (cx - 9.0, cz + 2.4, top))], wing_m, smooth=False)   # pylon

    # H-tail: a tailplane with dihedral and a big fin on each tip, reaching above and below it
    tail_y = [-TAIL_HALF_SPAN, -24, -6, 0, 6, 24, TAIL_HALF_SPAN]
    b.loft([foil(y, tail_le(y), tail_te(y), tail_z(y), 2.6 - abs(y) * 0.025) for y in tail_y], tail_m)
    for side in (-1, 1):
        fy = side * TAIL_HALF_SPAN
        b.loft([foil(z, le, te, fy, th, axis='Z') for z, le, te, th in
                ((6.5, -103, -110.5, 0.9), (13, -95.5, -114.5, 2.0), (25, -101.5, -117, 1.7),
                 (34, -107.5, -118.5, 1.0))], fin_m)
    # markings on the outside of the near fin: a round blue emblem, and a blue-and-yellow flash below it
    fin_y = -TAIL_HALF_SPAN - 0.9
    for radius, depth, m in ((3.1, 0.5, blue), (2.2, 0.6, white), (1.1, 0.7, blue)):
        b.cone(radius, radius, depth, T(-110.2, fin_y, 27.3) @ ALONG_Y, m, seg=28)
    b.box(12, 0.5, 1.1, T(-107.6, fin_y, 18.6) @ R('Y', 35), blue)
    b.box(9, 0.5, 0.7, T(-108.0, fin_y, 17.3) @ R('Y', 35), yellow)
    # a small Ukrainian flag behind the name on the nose
    for z, m in ((3.0, blue), (2.0, yellow)):
        b.box(3.0, 0.3, 1.0, T(70.5, -fuselage_side(70.5, 2.5) - 0.05, z), m)
    b.finish(body_coll, origin, sharp=50)

    # lettering on the near side, each line leaning with the curve of the skin behind it
    def label(name, text, x, z, size, colour):
        cu = bpy.data.curves.new('An225_' + name, 'FONT')
        cu.body, cu.size, cu.extrude, cu.align_x = text, size, 0.06, 'CENTER'
        cu.materials.append(mat('an225_text_' + colour[1:], colour, rough=0.5))
        y0, y1 = fuselage_side(x, z), fuselage_side(x, z + size * 0.7)
        lean = math.degrees(math.atan2(y0 - y1, size * 0.7))
        half = 0.3 * size * max(len(line) for line in text.split('\n'))       # rough half width of the text
        turn = math.degrees(math.atan2(fuselage_side(x - half, z) - fuselage_side(x + half, z), 2 * half))
        ob = bpy.data.objects.new('An225_' + name, cu)
        ob.visible_shadow = False                          # paint, not a floating sign
        ob.matrix_world = (T(origin.x + x, origin.y - y0 - 0.22, origin.z + z)
                           @ R('Z', turn) @ R('X', 90 - lean))
        body_coll.objects.link(ob)

    label('Name', 'МРІЯ', 80, 0.6, 3.8, BLUE)
    label('Type', 'ANTONOV 225', 94, 3.2, 1.9, BLUE)
    label('Role', 'INTERNATIONAL CARGO\nTRANSPORTER', 79, 7.2, 1.3, BLUE)
    label('Registration', 'UR-82060', -56, 3.4, 2.8, '#2a3340')

    # landing gear: seven pairs of main wheels a side under the sponsons, and two twin nose legs
    tyre, hub = mat('tyre', '#1d1f23', rough=0.85), mat('wheel_hub', '#aeb6bf', rough=0.4, metal=0.6)
    strut = mat('gear_strut', '#6b737c', rough=0.4, metal=0.7)
    g = Build('An225_Gear')

    def wheel(x, y, radius, width):
        z = WHEEL_Z + radius
        g.cone(radius, radius, width, T(x, y, z) @ ALONG_Y, tyre, seg=32)
        g.cone(radius * 0.5, radius * 0.5, width + 0.5, T(x, y, z) @ ALONG_Y, hub, seg=14)

    for side in (-1, 1):
        for x in MAIN_WHEEL_X:
            for y in (7.4, 10.3):
                wheel(x, side * y, 2.7, 2.0)
            g.cone(0.7, 0.7, 7.5, T(x, side * 8.85, WHEEL_Z + 6.4), strut, seg=8)
        for y in (1.5, 4.3):
            wheel(NOSE_WHEEL_X, side * y, 2.4, 1.8)
        g.cone(0.7, 0.7, 8.5, T(NOSE_WHEEL_X, side * 2.9, WHEEL_Z + 6.6), strut, seg=8)
    g.finish(gear_coll, origin)

    eng = [engine_centre(s * y) for s in (-1, 1) for y in ENGINE_Y]           # near side first
    points = dict(
        fuse_a=(-92, 0, 6.5), fuse_b=(100, 0, 0.4),
        wing_a=(14, -12, 12), wing_b=((wing_le(96) + wing_te(96)) / 2, -96, wing_z(96)),
        wheel_rear=(MAIN_WHEEL_X[0], 0, WHEEL_Z), wheel_front=(MAIN_WHEEL_X[-1], 0, WHEEL_Z),
        wheel_nose=(NOSE_WHEEL_X, 0, WHEEL_Z),
        light_wing=(wing_te(HALF_SPAN) + 4, -HALF_SPAN, wing_z(HALF_SPAN)),
        light_tail=(-113, -TAIL_HALF_SPAN, 34.5), light_top=(6, 0, 17.0),
    )
    for i, (cx, cy, cz) in enumerate(eng):
        points[f'exhaust_{i}'] = (cx - 14, cy, cz)
    both = [body_coll, gear_coll]
    sprite('plane_body', [body_coll], origin, FLYING, frame=both, points=points, photo=True, px=PLANE_PX)
    sprite('plane_gear', [gear_coll], origin, FLYING, frame=both, photo=True, px=PLANE_PX, shade=[body_coll])
    return fuselage


# ═════════════════════════════════════════════════════ things in the sky
def build_birds():
    """Three wing positions of the same bird; the game cycles up, level, down, level."""
    body, belly = mat('bird_body', '#4a5160', rough=0.8), mat('bird_belly', '#cdd4df', rough=0.8)
    wing, beak = mat('bird_wing', '#636c7d', rough=0.8), mat('bird_beak', '#f2a23a', rough=0.5)
    white, black = mat('eye_white', '#ffffff', rough=0.3), mat('eye_black', '#101114', rough=0.3)
    colls = []
    for i, flap in enumerate((38, 0, -34)):
        coll, origin = new_asset(f'Bird_{i}')
        colls.append(coll)
        b = Build(f'Bird_{i}')
        b.sphere(1, S(11, 5.6, 5.4), body)
        b.sphere(1, T(-0.5, 0, -2.0) @ S(8.2, 4.6, 3.9), belly)
        b.sphere(4.5, T(-9.5, 0, 2.6), body)
        b.cone(1.8, 0.1, 6.5, T(-15.6, 0, 1.9) @ R('Y', -90), beak, seg=14)
        for side in (-1, 1):
            b.sphere(1.15, T(-12.0, side * 3.1, 3.9), white, seg=12)
            b.sphere(0.62, T(-12.75, side * 3.45, 4.0), black, seg=10)
            M = T(0, side * 3, 2) @ R('X', side * flap)
            b.loft([[M @ p for p in airfoil(side * s, le, te, 0, th)]
                    for s, le, te, th in ((0, -5.5, 6, 1.3), (8, -4, 5.5, 1.0), (16, 1, 4.2, 0.5))], wing)
        b.loft([[Vector(p) for p in ((8, -3, 0.7), (8, 3, 0.7), (8, 3, -0.7), (8, -3, -0.7))],
                [Vector(p) for p in ((18.5, -5, 2.4), (18.5, 5, 2.4), (18.5, 5, 1.6), (18.5, -5, 1.6))]],
               wing, smooth=False)
        b.finish(coll, origin)
    for i, coll in enumerate(colls):
        sprite(f'bird_{i}', [coll], ASSET_ORIGIN[coll.name], ONCOMING, frame=colls)


BALLOON_COLS = [('#e84a5f', '#ffd166'), ('#2a9d8f', '#f4f1de'), ('#7b5cd6', '#ffb4a2')]


def build_balloons():
    wicker, rope = mat('wicker', '#8a5a2b', rough=0.9), mat('rope', '#5a4632', rough=0.9)
    burner = mat('burner', '#3b4048', rough=0.4, metal=0.7)
    colls = []
    for i, (c1, c2) in enumerate(BALLOON_COLS):
        coll, origin = new_asset(f'Balloon_{i}')
        colls.append(coll)
        env = mat(f'balloon_envelope_{i}', c1, rough=0.55)
        nd = Nodes(env)
        nd.paint(c2, nd.gores(6))
        b = Build(f'Balloon_{i}')
        b.revolve(smooth_profile([(-21, 7), (-18, 8.6), (-12, 13.2), (-4, 20), (6, 25.6), (16, 28), (26, 25.4),
                                  (33.5, 19), (38.3, 10.5), (40.3, 3.2), (40.6, 0.3)]), env, seg=72)
        b.box(11, 11, 8, T(0, 0, -32), wicker)
        b.box(12, 12, 1.4, T(0, 0, -28), rope)                                # basket rim
        b.cone(2.2, 2.2, 3, T(0, 0, -23.5), burner, seg=12)
        for sx in (-1, 1):
            for sy in (-1, 1):
                a, c = Vector((sx * 5, sy * 5, -28)), Vector((sx * 5.6, sy * 5.6, -20))
                M = Matrix.Translation((a + c) / 2) @ (c - a).to_track_quat('Z', 'Y').to_matrix().to_4x4()
                b.cone(0.3, 0.3, (c - a).length, M, rope, seg=6)
        b.finish(coll, origin)
    for i, coll in enumerate(colls):
        sprite(f'balloon_{i}', [coll], ASSET_ORIGIN[coll.name], STANDING, frame=colls)


CLOUD_SHAPES = {   # puffs as (x, depth, z, radius)
    'cloud_0': [(-34, 0, -8, 20), (-12, 6, 6, 27), (16, -5, 2, 24), (40, 2, -9, 17), (4, -8, -12, 24)],
    'cloud_1': [(-26, 0, -6, 18), (0, 5, 5, 25), (26, -4, -4, 20), (-6, -9, -10, 20)],
    'cloud_2': [(-44, 3, -8, 17), (-22, -4, 2, 24), (6, 6, 8, 29), (34, -3, 0, 23), (54, 2, -9, 15),
                (10, -10, -12, 26), (-14, 8, -12, 20)],
}
STORM_SHAPE = [(-40, 2, -6, 20), (-18, -6, 8, 27), (12, 6, 10, 29), (40, -2, -4, 21), (0, -10, -10, 26),
               (-22, 8, -12, 20), (24, -8, -12, 20)]


def build_clouds():
    white = mat('cloud_white', '#ffffff', rough=1.0, emit=0.22, emit_color='#dfe9fb')
    for name, puffs in CLOUD_SHAPES.items():
        coll, origin = new_asset(name.capitalize())
        b = Build(name.capitalize())
        for x, y, z, r in puffs:
            b.ico(1, T(x, y, z) @ S(r, r, r * 0.9), white, sub=4)
        b.finish(coll, origin, sharp=180)
        sprite(name, [coll], origin, STANDING)

    # the storm cloud is rendered twice: dark, and lit from inside for the lightning flash
    dark = mat('storm_dark', '#666d8c', rough=1.0)
    lit = mat('storm_lit', '#c9cdf0', rough=1.0, emit=0.55, emit_color='#e4e7ff')
    colls = []
    for name, m in (('Storm', dark), ('Storm_lit', lit)):
        coll, origin = new_asset(name)
        colls.append(coll)
        b = Build(name)
        for x, y, z, r in STORM_SHAPE:
            b.ico(1, T(x, y, z) @ S(r, r, r * 0.9), m, sub=4)
        b.finish(coll, origin, sharp=180)
    sprite('storm', [colls[0]], ASSET_ORIGIN['Storm'], STANDING, frame=colls)
    sprite('storm_lit', [colls[1]], ASSET_ORIGIN['Storm_lit'], STANDING, frame=colls)


def build_jet():
    """A small red jet that flies right to left (nose towards -X)."""
    coll, origin = new_asset('Jet')
    red, dark_red = mat('jet_red', '#dc4753', rough=0.35), mat('jet_dark_red', '#a52f3a', rough=0.4)
    nose, glass = mat('jet_nose', '#f3f5f7', rough=0.35), mat('jet_glass', '#9fd6ff', rough=0.1)
    flame = mat('jet_flame', '#ffb347', rough=0.5, emit=4.0)
    b = Build('Jet')
    sections = [(-33, 0, 0.5, 0.5), (-28, 0, 2.4, 2.4), (-20, 0, 4.4, 4.6), (-8, 0.3, 5.6, 6.0),
                (10, 0.3, 5.4, 5.8), (22, 0.7, 4.3, 4.5), (27.5, 0.9, 3.5, 3.5)]
    b.loft([ring_yz(*s, n=20, power=2.0) for s in sections], red, mats=[nose, nose] + [red] * 4)
    b.sphere(1, T(-10, 0, 5.0) @ S(8.5, 3.5, 3.6), glass)
    b.loft([airfoil(y, -3 + abs(y) * 0.62, 12 + abs(y) * 0.22, -1.2, 1.9 - abs(y) * 0.04)
            for y in (-25, -12, 0, 12, 25)], dark_red)
    b.loft([airfoil(y, 17 + abs(y) * 0.5, 26.5 + abs(y) * 0.15, 1.6, 1.2) for y in (-11, 0, 11)], dark_red)
    b.loft([airfoil(z, le, te, 0, 1.3, axis='Z') for z, le, te in ((4, 12, 26.5), (12, 18.5, 28), (18, 23, 29))],
           dark_red)
    b.cone(2.9, 2.9, 1.0, T(27.8, 0, 0.9) @ ALONG_X, flame, seg=16)
    b.finish(coll, origin, subsurf=1)
    sprite('jet', [coll], origin, ONCOMING, points=dict(exhaust=(30, 0, 0.9)))


def build_crate():
    """Cargo crate under a white parachute; the anchor is the centre of the crate."""
    coll, origin = new_asset('Crate')
    wood, frame = mat('crate_wood', '#c08238', rough=0.85), mat('crate_frame', '#7a4a1c', rough=0.85)
    gold, line = mat('crate_band', '#ffd54a', rough=0.35, metal=0.3), mat('chute_line', '#f2f2f2', rough=0.8)
    canopy = mat('chute_canopy', '#ffffff', rough=0.6)     # plain, so it can't be taken for a striped balloon
    b = Build('Crate')
    b.box(21, 21, 19, T(0, 0, 0), wood)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.box(2.4, 2.4, 20.4, T(sx * 10, sy * 10, 0), frame)              # corner posts
        for sz in (-1, 1):
            b.box(2.4, 21, 2.4, T(sx * 10, 0, sz * 9), frame)
            b.box(21, 2.4, 2.4, T(0, sx * 10, sz * 9), frame)
    b.box(22.2, 22.2, 4.6, T(0, 0, 0.4), gold)
    b.revolve(smooth_profile([(15, 21), (18.2, 20), (22.6, 17), (26.6, 12), (29.2, 6), (30.2, 0.3)]), canopy,
              seg=64, caps=(False, True))
    for i in range(8):
        a = TAU * (i + 0.5) / 8
        top = Vector((20.4 * math.cos(a), 20.4 * math.sin(a), 15.2))
        foot = Vector((9 * math.copysign(1, math.cos(a)), 9 * math.copysign(1, math.sin(a)), 9.8))
        M = Matrix.Translation((top + foot) / 2) @ (top - foot).to_track_quat('Z', 'Y').to_matrix().to_4x4()
        b.cone(0.28, 0.28, (top - foot).length, M, line, seg=6)
    b.finish(coll, origin)
    sprite('crate', [coll], origin, STANDING)


def build_powerups():
    """Chunky tokens with a raised icon: repair heart, shield, cargo magnet."""
    rim = mat('token_rim', '#ffffff', rough=0.25)
    icon = mat('token_icon', '#ffffff', rough=0.3, emit=0.25)
    tip = mat('magnet_tip', '#ff5d73', rough=0.3)
    heart = []
    for i in range(44):
        t = TAU * i / 44
        heart.append((16 * math.sin(t) ** 3 * 0.47,
                      (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) * 0.47 + 0.9))
    shield = [(0, 8.4), (7, 5.8), (7, -0.4), (5.2, -5), (0, -8.6), (-5.2, -5), (-7, -0.4), (-7, 5.8)]
    arc = [math.pi + math.pi * i / 14 for i in range(15)]
    magnet = ([(-7.6, 7.2)] + [(7.6 * math.cos(a), -1.5 + 7.6 * math.sin(a)) for a in arc] + [(7.6, 7.2), (3.8, 7.2)]
              + [(3.8 * math.cos(a), -1.5 + 3.8 * math.sin(a)) for a in reversed(arc)] + [(-3.8, 7.2)])
    colls = []
    for kind, col, shape in (('heart', '#ff5d73', heart), ('shield', '#39a9f5', shield), ('magnet', '#a56bff', magnet)):
        coll, origin = new_asset('Power_' + kind)
        colls.append(coll)
        b = Build('Power_' + kind)
        b.cone(13.4, 13.4, 4.2, ALONG_Y, mat('token_' + kind, col, rough=0.22), seg=56)
        rings = []
        for i in range(57):                                                   # white torus around the edge
            a = TAU * i / 56
            rings.append([Vector(((14 + 1.8 * math.cos(TAU * j / 12)) * math.cos(a), 1.8 * math.sin(TAU * j / 12),
                                  (14 + 1.8 * math.cos(TAU * j / 12)) * math.sin(a))) for j in range(12)])
        b.loft(rings, rim, caps=(False, False))
        b.outline(shape, -1.8, -4.6, icon)
        if kind == 'magnet':
            for sx in (-1, 1):
                b.box(4.0, 3.0, 2.8, T(sx * 5.7, -3.2, 5.9), tip)
        b.finish(coll, origin)
    for kind, coll in zip(('heart', 'shield', 'magnet'), colls):
        sprite('power_' + kind, [coll], ASSET_ORIGIN[coll.name], STANDING, frame=colls)


# ════════════════════════════════════════════════════════════ scenery
def build_scenery():
    """Ground props; each anchor is the middle of the base, where it meets the grass."""
    trunk = mat('trunk', '#7a5230', rough=0.9)
    leaf_a, leaf_b = mat('leaf_dark', '#3c9648', rough=0.9), mat('leaf_light', '#5fbf66', rough=0.9)
    pine_a, pine_b = mat('pine_dark', '#27724a', rough=0.9), mat('pine_light', '#34905a', rough=0.9)

    coll, origin = new_asset('Tree')
    b = Build('Tree')
    b.cone(2.4, 1.9, 16, T(0, 0, 8), trunk, seg=12)
    b.ico(14, T(0, 0, 25), leaf_a)
    b.ico(9.5, T(-5, -4, 30), leaf_b)
    b.ico(9, T(6, -3, 21), leaf_b)
    b.ico(8, T(2, 5, 31), leaf_a)
    b.finish(coll, origin, sharp=180)
    sprite('tree', [coll], origin)

    coll, origin = new_asset('Pine')
    b = Build('Pine')
    b.cone(1.8, 1.5, 9, T(0, 0, 4.5), trunk, seg=10)
    for z, r, h, m in ((6, 13, 17, pine_a), (16, 10.2, 15, pine_b), (25.5, 7.2, 13, pine_a)):
        b.cone(r, 0.15, h, T(0, 0, z + h / 2), m, seg=20)
    b.finish(coll, origin)
    sprite('pine', [coll], origin)

    coll, origin = new_asset('Bush')
    b = Build('Bush')
    b.ico(1, T(0, 0, 4.5) @ S(8.5, 8, 7.5), leaf_a)
    b.ico(1, T(9.5, -1, 3.5) @ S(6.5, 6, 5.5), leaf_b)
    b.ico(1, T(-6.5, -3, 3) @ S(5.5, 5, 4.8), leaf_b)
    b.finish(coll, origin, sharp=180)
    sprite('bush', [coll], origin)

    # farmhouse, plus a windows-only layer that the game fades in over it after dark
    wall, roof = mat('house_wall', '#f1e3c8', rough=0.9), mat('house_roof', '#c0503a', rough=0.8)
    door, brick = mat('house_door', '#7a4a2a', rough=0.8), mat('house_chimney', '#a3523f', rough=0.9)
    glass = mat('house_window', '#9fd0ee', rough=0.2)
    glow = mat('house_window_lit', '#ffd86b', rough=0.4, emit=3.0)
    coll, origin = new_asset('House')
    glow_coll, _ = new_asset('House_glow')
    _slot_count[0] -= 1                                    # the glow layer sits on top of the house
    ASSET_ORIGIN[glow_coll.name] = origin
    b, g = Build('House'), Build('House_glow')
    b.box(28, 20, 17, T(0, 0, 8.5), wall)
    b.loft([[Vector((x, -12.5, 16.6)), Vector((x, 12.5, 16.6)), Vector((x, 0, 29))] for x in (-16.5, 16.5)],
           roof, smooth=False)
    b.box(4, 4, 9, T(8, 4, 25), brick)
    b.box(6, 0.8, 10, T(0, -10.2, 5), door)
    for x in (-8.6, 8.6):
        b.box(5.2, 0.8, 5.2, T(x, -10.2, 10.4), glass)
        g.box(5.2, 0.8, 5.2, T(x, -10.4, 10.4), glow)
    b.box(0.8, 5.2, 5.2, T(14.2, 0, 10.4), glass)
    g.box(0.8, 5.2, 5.2, T(14.4, 0, 10.4), glow)
    b.finish(coll, origin)
    g.finish(glow_coll, origin)
    sprite('house', [coll], origin, frame=[coll, glow_coll])
    sprite('house_glow', [glow_coll], origin, frame=[coll, glow_coll])

    coll, origin = new_asset('Hay')
    straw, straw_dark = mat('hay', '#e6c75c', rough=0.95), mat('hay_dark', '#c4a23c', rough=0.95)
    b = Build('Hay')
    M = T(0, 0, 7) @ R('Z', 35) @ ALONG_Y
    b.cone(7, 7, 12, M, straw, seg=28)
    b.cone(4.2, 4.2, 12.3, M, straw_dark, seg=20)
    b.cone(1.6, 1.6, 12.6, M, straw, seg=12)
    b.finish(coll, origin)
    sprite('hay', [coll], origin)


# ══════════════════════════════════════════════════════════════ the scene
RIG = {}            # lights, worlds and whether Cycles is available


def enable_cycles(sc):
    """Cycles ships with Blender but --factory-startup leaves its add-on switched off."""
    try:
        import addon_utils
        addon_utils.enable('cycles', default_set=True)
        sc.render.engine = 'CYCLES'
        sc.render.engine = 'BLENDER_EEVEE'
        return True
    except Exception:                                      # any failure just means: stay with EEVEE
        return False


def photo_world(sun_dir):
    """A clear daytime sky above and soft green-grey ground light below, for the photo renders."""
    world = bpy.data.worlds.new('PhotoSky')
    world.use_nodes = True
    nt = world.node_tree
    bg = next(n for n in nt.nodes if n.bl_idname == 'ShaderNodeBackground')
    sky = nt.nodes.new('ShaderNodeTexSky')
    sky.sky_type = 'HOSEK_WILKIE'
    sky.sun_direction, sky.turbidity, sky.ground_albedo = Vector(sun_dir).normalized(), 2.4, 0.35
    tc = nt.nodes.new('ShaderNodeTexCoord')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    nt.links.new(tc.outputs['Generated'], sep.inputs[0])
    up = nt.nodes.new('ShaderNodeMapRange')                # 0 below the horizon, 1 above it
    nt.links.new(sep.outputs[2], up.inputs[0])
    for i, v in enumerate((-0.06, 0.06, 0.0, 1.0), 1):
        up.inputs[i].default_value = v
    mix = nt.nodes.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    a, b = [s for s in mix.inputs if s.type == 'RGBA'][:2]
    a.default_value = (0.40, 0.44, 0.36, 1.0)              # light bounced up from fields far below
    nt.links.new(up.outputs[0], mix.inputs[0])
    nt.links.new(sky.outputs[0], b)
    nt.links.new(next(s for s in mix.outputs if s.type == 'RGBA'), bg.inputs[0])
    bg.inputs[1].default_value = 0.68
    return world


def render_mode(sc, photo):
    """Game props are quick EEVEE renders under flat studio light.  The An-225 is path-traced
    by Cycles under a sky with a sharper, stronger sun (EEVEE with ray tracing if Cycles is
    not available)."""
    sc.world = RIG['sky'] if photo else RIG['world']
    RIG['sun'].data.energy = 2.7 if photo else 2.0
    RIG['sun'].data.angle = math.radians(2 if photo else 8)
    RIG['fill'].hide_render = photo
    if photo and RIG['cycles']:
        sc.render.engine = 'CYCLES'
        sc.cycles.device, sc.cycles.samples, sc.cycles.use_denoising = 'CPU', 256, True
    else:
        sc.render.engine = 'BLENDER_EEVEE'
        sc.eevee.use_raytracing = photo


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_mode = 'RGBA'
    sc.render.resolution_percentage = 100
    try:
        sc.view_settings.view_transform = 'Standard'       # keep the flat, bright game colours
    except TypeError:
        pass
    sc.eevee.taa_render_samples = 64

    world = bpy.data.worlds.new('Sky')
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground')
    bg.inputs[0].default_value = lin('#cfe3ff')
    bg.inputs[1].default_value = 0.42
    sc.world = world

    rig = bpy.data.collections.new('Rig')
    sc.collection.children.link(rig)

    def sun(name, direction, energy, shadow):
        data = bpy.data.lights.new(name, 'SUN')
        data.energy, data.use_shadow, data.angle = energy, shadow, math.radians(8)
        ob = bpy.data.objects.new(name, data)
        ob.rotation_euler = Vector(direction).normalized().to_track_quat('Z', 'Y').to_euler()
        ob.location = (0, -200, 200)
        rig.objects.link(ob)
        return ob

    sun_dir = (-0.35, -0.6, 0.72)
    RIG['sun'] = sun('Sun', sun_dir, 2.0, True)            # key light: high, from the viewer's left
    RIG['fill'] = sun('Fill', (0.5, -0.8, -0.1), 0.3, False)   # soft fill so undersides stay readable
    RIG.update(world=world, sky=photo_world(sun_dir), cycles=enable_cycles(sc))

    cam_data = bpy.data.cameras.new('SpriteCam')
    cam_data.type, cam_data.clip_start, cam_data.clip_end = 'ORTHO', 1, 5000
    cam = bpy.data.objects.new('SpriteCam', cam_data)
    rig.objects.link(cam)
    sc.camera = cam
    return sc, cam


def bounds(colls, view):
    """Screen-space box (in game units, relative to the anchor) of everything in these collections."""
    dg = bpy.context.evaluated_depsgraph_get()
    _, rt, up = basis(view)
    xs, ys = [], []
    for coll in colls:
        origin = ASSET_ORIGIN[coll.name]                  # each model is measured from its own anchor
        for ob in coll.objects:
            ev = ob.evaluated_get(dg)
            me = ev.to_mesh()
            for v in me.vertices:
                p = ev.matrix_world @ v.co - origin
                xs.append(p.dot(rt))
                ys.append(p.dot(up))
            ev.to_mesh_clear()
    return min(xs), max(xs), min(ys), max(ys)


def render_all(sc, cam):
    os.makedirs(OUT, exist_ok=True)
    meta = {}
    for job in SPRITES:
        if not wanted(job['name']):
            continue
        view, origin = job['view'], job['origin']
        d, rt, up = basis(view)
        x0, x1, y0, y1 = bounds(job['frame'], view)
        left, top = math.floor(x0 - PAD), math.ceil(y1 + PAD)
        w, h = math.ceil(x1 + PAD) - left, top - math.floor(y0 - PAD)
        centre = origin + rt * (left + w / 2) + up * (top - h / 2)
        cam.matrix_world = Matrix.Translation(centre + d * 1500) @ Matrix((rt, up, d)).transposed().to_4x4()
        cam.data.ortho_scale = max(w, h)
        sc.render.resolution_x, sc.render.resolution_y = w * job['px'], h * job['px']
        render_mode(sc, job['photo'])
        for _, coll in ASSETS:
            coll.hide_render = coll not in job['show'] and coll not in job['shade']
            for ob in coll.objects:                        # shadow casters are hidden from the camera only
                ob.visible_camera = coll not in job['shade']
        sc.render.filepath = os.path.join(OUT, job['name'] + '.png')
        bpy.ops.render.render(write_still=True)
        meta[job['name']] = dict(file=job['name'] + '.png', w=w, h=h, ax=-left, ay=top,
                                 points={k: project(p, view) for k, p in job['points'].items()})
        print(f"  rendered {job['name']}  {w}x{h} units")
    for _, coll in ASSETS:
        coll.hide_render = False
        for ob in coll.objects:
            ob.visible_camera = True
    render_mode(sc, False)
    return meta


def main():
    sc, cam = setup_scene()
    for build in (build_plane, build_birds, build_balloons, build_clouds, build_jet, build_crate,
                  build_powerups, build_scenery):
        build()
    meta = render_all(sc, cam)
    if not ONLY:
        data = dict(scale=SCALE, sprites=meta)
        with open(os.path.join(OUT, 'sprites.json'), 'w') as f:
            json.dump(data, f, indent=1)
        with open(os.path.join(OUT, 'sprites.js'), 'w') as f:
            f.write('// Generated by assets/blender/build_assets.py -- do not edit by hand.\n')
            f.write('window.AN225_SPRITES = ' + json.dumps(data, indent=1) + ';\n')
        bpy.context.preferences.filepaths.save_version = 0     # no .blend1 backup next to the file
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, 'an225_assets.blend'))
    print('DONE', len(meta), 'sprites')


main()
