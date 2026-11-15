"""Lewis's An-225 Adventure! -- tkinter edition.

Same flight physics and game rules as lewis_an225_game.html:
hold SPACE / UP / mouse to take off and climb, DOWN to dive, P to pause, R to restart.

With Pillow installed (pip install pillow) the game uses the 3D sprites in assets/sprites,
rendered from the Blender models; without it, it draws the same scene as vector art.
"""
import json
import math
import os
import random
import time
import tkinter as tk
from types import SimpleNamespace as NS

try:
    from PIL import Image, ImageChops, ImageTk
except ImportError:                      # no Pillow: fall back to the drawn artwork
    Image = None

# ------------------------------------------------------------------ constants
W, H = 800, 450
GROUND = H - 44          # y of the grass line
PLANE_X = 190            # screen x of the plane's centre
RUNWAY_Y = GROUND + 12   # where the wheels meet the grass
GEAR_TRAVEL = 12         # how far the gear sprite slides up into the belly when retracted
HARD_LANDING = 3         # touchdown sink rate (px/tick) that damages the hull
PLANE_SCALE = 1.2        # the 3D An-225 is drawn a little larger than it was modelled
STEP = 1 / 60            # fixed physics step, so the game runs the same on any PC
LEVEL_TICKS = 1200       # 20 s per level
MAX_HP, MAX_COMBO = 3, 5
TAU = math.tau
FONT = 'Trebuchet MS'
UP_KEYS, DOWN_KEYS = {'space', 'Up', 'w'}, {'Down', 's'}


# -------------------------------------------------------------------- helpers
def clamp(v, a, b):
    return max(a, min(b, v))


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    return t * t * (3 - 2 * t)


def hash01(n):
    x = math.sin(n * 127.1 + 311.7) * 43758.5453
    return x - math.floor(x)


def hex_rgb(h):
    n = int(h[1:], 16)
    return (n >> 16, (n >> 8) & 255, n & 255)


def mix3(a, b, t):
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def css(c):
    return '#%02x%02x%02x' % (clamp(int(c[0]), 0, 255), clamp(int(c[1]), 0, 255), clamp(int(c[2]), 0, 255))


def bezier(p0, p1, p2, p3, n=8):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def dist_to_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = clamp(((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy), 0, 1)
    return math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t))


# ------------------------------------------------- time of day (one per level)
def _sky(name, top, bot, amb, sun, stars):
    return NS(name=name, top=hex_rgb(top), bot=hex_rgb(bot), amb=amb, sun=sun, stars=stars)


SKY = [
    _sky('Blue Skies',       '#2a7fd6', '#a9dcf7', (255, 255, 255), (640, 78), 0),
    _sky('Golden Afternoon', '#3b7cc9', '#f6dfae', (255, 242, 216), (655, 175), 0),
    _sky('Sunset',           '#3a3f8a', '#ff9b5e', (255, 188, 150), (668, 268), 0.15),
    _sky('Dusk',             '#161c4a', '#93507f', (150, 135, 188), (680, 440), 0.7),
    _sky('Starry Night',     '#050a1f', '#1a2b5a', (98, 112, 168), (690, 540), 1),
    _sky('Sunrise',          '#3d4f96', '#ffb59a', (255, 205, 190), (612, 280), 0.2),
]
STARS = [(hash01(i) * W, hash01(i + 0.37) * (GROUND - 120), hash01(i + 0.13) * TAU) for i in range(50)]

# ------------------- plane geometry (local coords: origin = centre, nose = +x)
NEAR_WING = [(40, -9), (-34, 30), (-52, 30), (-8, -9)]
FAR_WING = [(44, -13), (-14, -46), (-30, -46), (2, -13)]
NEAR_ENG = [(lerp(40, -34, t) + 5, lerp(-9, 30, t) + 5) for t in (0.26, 0.5, 0.74)]
FAR_ENG = [(lerp(44, -14, t) + 4, lerp(-13, -46, t) + 4) for t in (0.3, 0.55, 0.8)]
_TAIL_BELLY = bezier((-110, -3), (-90, 2), (-60, 12), (-30, 14))
_CHIN = bezier((70, 14), (88, 14), (100, 10), (104, 3))
FUSELAGE = ([(104, 3)] + bezier((104, 3), (100, -6), (88, -13), (70, -15)) + [(-30, -15)]
            + bezier((-30, -15), (-60, -15), (-90, -12), (-108, -9)) + [(-110, -3)]
            + _TAIL_BELLY + [(70, 14)] + _CHIN)
BELLY = ([(-62, 7)] + [p for p in _TAIL_BELLY if p[1] > 7] + [(70, 14)]
         + [p for p in _CHIN if p[1] > 7] + [(100, 7)])
GEAR_POD = [(48, 12)] + bezier((48, 12), (40, 23), (-40, 23), (-50, 12))

# Hit circles per obstacle type as (dx, dy, radius) -- a touch smaller than the art.
SHAPES = {
    'bird': [(-2, -1, 9)],
    'balloon': [(0, -13, 25), (0, 31, 7)],
    'storm': [(-24, 4, 17), (0, -2, 22), (24, 4, 17)],
    'jet': [(-18, 0, 7), (0, 1, 8), (18, -3, 8)],
}
CLOUD_PUFFS = [(-34, 8, 20), (-12, -6, 27), (16, -2, 24), (40, 9, 17), (4, 12, 24)]
STORM_PUFFS = [(-40, 6, 20), (-18, -8, 27), (12, -10, 29), (40, 4, 21), (0, 10, 26)]
BALLOON_COLS = [('#e84a5f', '#ffd166'), ('#2a9d8f', '#f4f1de'), ('#7b5cd6', '#ffb4a2')]
POWER_COL = {'heart': '#ff5d73', 'shield': '#39a9f5', 'magnet': '#a56bff'}

# Markers that tell good from bad at a glance.  Everything worth catching floats inside a
# pulsing green ring; everything dangerous has a red haze behind it and a warning sign above it.
GOOD = {'crate': (-9, 33), 'heart': (0, 24), 'shield': (0, 24), 'magnet': (0, 24)}       # ring: dy, radius
DANGER = {'bird': (0, 27, 24, -18, 0.8), 'balloon': (-6, 46, 56, -52, 1.0),              # haze: dy, rx, ry;
          'storm': (0, 84, 58, -42, 1.1), 'jet': (0, 48, 30, -24, 1.0)}                  # sign: dy, size
GREEN, RED = (80, 255, 140), (255, 40, 40)
LEGEND_GOOD = (('crate', 152, 'crates'), ('heart', 216, 'repairs'), ('shield', 266, 'shields'),
               ('magnet', 316, 'magnets'))
LEGEND_BAD = (('bird', 388, 'birds'), ('balloon', 450, 'balloons'), ('storm', 550, 'storms'), ('jet', 652, 'jets'))


# Numbers the physics needs from the plane's shape.  These match the hand-drawn plane; when
# the 3D sprites load, the same numbers come from the points exported by the Blender model.
VECTOR_GEOM = dict(
    caps=[((-96, -2), (94, 1), 12), ((30, -4), (-38, 26), 7)],       # fuselage and near wing: a, b, radius
    wheels=dict(rear=-30, front=20, nose=74, drop=38),               # wheel x positions, depth below centre
    exhaust=[(x - 18, y) for x, y in NEAR_ENG] + [(x - 15, y) for x, y in FAR_ENG],
    lights=dict(wing=(-43, 30), tail=(-114, -19)),
)


class Sprites:
    """The 3D sprites rendered from the Blender models by assets/blender/build_assets.py.

    Pillow does the scaling, rotating and time-of-day tinting.  `load` returns None when
    Pillow or the sprite folder is missing, and the game then draws its vector artwork."""

    @classmethod
    def load(cls):
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'sprites')
        if Image is None or not os.path.isfile(os.path.join(folder, 'sprites.json')):
            return None
        try:
            return cls(folder)
        except (OSError, KeyError, ValueError):
            return None

    def __init__(self, folder):
        with open(os.path.join(folder, 'sprites.json')) as fh:
            data = json.load(fh)
        self.meta = data['sprites']
        self.base = {name: Image.open(os.path.join(folder, m['file'])).convert('RGBA')
                     for name, m in self.meta.items()}
        self.filters = getattr(Image, 'Resampling', Image)
        self.amb = (255, 255, 255)
        self.sized = {}                    # (name, scale) -> resized sprite, kept for the whole run
        self.images, self.photos = {}, {}  # tinted sprites and their Tk images, per light level
        self.flat, self.planes = {}, {}    # the plane with its gear composited, and rotated copies
        self.glows = {}                    # soft coloured glows behind the catch / dodge markers

    def set_ambient(self, amb):
        """amb is the time-of-day light as three 0..1 floats; a new level re-tints the sprites."""
        rgb = tuple(int(round(v * 24) / 24 * 255) for v in amb)
        if rgb != self.amb:
            self.amb = rgb
            self.images = {k: v for k, v in self.images.items() if k[3]}      # glowing sprites keep theirs
            self.photos = {k: v for k, v in self.photos.items() if k[3]}
            self.flat.clear()
            self.planes.clear()

    def image(self, name, scale=1.0, alpha=1.0, glow=False):
        """Sprite at playfield size.  glow skips the tint, for things that shine by themselves."""
        key = (name, round(scale * 50), round(alpha * 20), glow)
        im = self.images.get(key)
        if im is None:
            im = self.sized.get(key[:2])
            if im is None:
                m = self.meta[name]
                im = self.sized[key[:2]] = self.base[name].resize(
                    (max(1, round(m['w'] * scale)), max(1, round(m['h'] * scale))), self.filters.LANCZOS)
            if not glow and self.amb != (255, 255, 255):
                im = ImageChops.multiply(im, Image.new('RGBA', im.size, self.amb + (255,)))
            if alpha < 0.99:
                im = im.copy()
                im.putalpha(im.getchannel('A').point(lambda a: int(a * alpha)))
            self.images[key] = im
        return key, im

    def photo(self, name, scale=1.0, alpha=1.0, glow=False):
        key, im = self.image(name, scale, alpha, glow)
        ph = self.photos.get(key)
        if ph is None:
            ph = self.photos[key] = ImageTk.PhotoImage(im)
        return ph

    def glow(self, rgb, rx, ry, alpha):
        """A soft elliptical glow that fades out towards its edge, as a Tk image."""
        key = (rgb, round(rx), round(ry), round(alpha * 12))
        ph = self.glows.get(key)
        if ph is None:
            # radial_gradient runs from 0 in the middle to 255 in the corners, so the glow has
            # to reach zero at about 180 for its edge to be an ellipse and not the image's box
            grad = Image.radial_gradient('L').resize((key[1] * 2, key[2] * 2), self.filters.BILINEAR)
            im = Image.new('RGBA', grad.size, rgb + (0,))
            im.putalpha(grad.point(lambda v: max(0, 255 - v * 36 // 25) * key[3] // 12))
            ph = self.glows[key] = ImageTk.PhotoImage(im)
        return ph

    def plane(self, pitch, gear, susp):
        """The An-225 with its gear at the right extension, rotated about its anchor.
        Returns (photo, ox, oy): draw its top-left corner at (anchor x - ox, anchor y - oy)."""
        k = PLANE_SCALE
        deg = round(-math.degrees(pitch) * 2) / 2             # half-degree steps, so copies get reused
        off = max(0, round((1 - gear) * GEAR_TRAVEL * k + susp)) if gear > 0.02 else None
        hit = self.planes.get((deg, off))
        if hit:
            return hit
        if len(self.planes) > 300:
            self.planes.clear()
        flat = self.flat.get(off)
        if flat is None:
            m = self.meta['plane_body']
            im = Image.new('RGBA', self.image('plane_body', k)[1].size, (0, 0, 0, 0))
            if off is not None:                               # the gear slides up behind the belly
                im.alpha_composite(self.image('plane_gear', k)[1], (0, 0), (0, off))
            im.alpha_composite(self.image('plane_body', k)[1])
            flat = self.flat[off] = (im, m['ax'] * k, m['ay'] * k)
        im, ax, ay = flat
        if deg:                                               # rotate, then work out where the anchor went
            a, dx, dy = math.radians(deg), ax - im.width / 2, ay - im.height / 2
            im = im.rotate(deg, resample=self.filters.BICUBIC, expand=True)
            ax = im.width / 2 + dx * math.cos(a) + dy * math.sin(a)
            ay = im.height / 2 - dx * math.sin(a) + dy * math.cos(a)
        hit = self.planes[(deg, off)] = (ImageTk.PhotoImage(im), ax, ay)
        return hit

    def plane_geom(self):
        k = PLANE_SCALE
        p = {name: (x * k, y * k) for name, (x, y) in self.meta['plane_body']['points'].items()}
        return dict(
            caps=[(p['fuse_a'], p['fuse_b'], 11 * k), (p['wing_a'], p['wing_b'], 5.5 * k)],
            wheels=dict(rear=p['wheel_rear'][0], front=p['wheel_front'][0], nose=p['wheel_nose'][0],
                        drop=p['wheel_rear'][1]),
            exhaust=[p[f'exhaust_{i}'] for i in range(6)],
            lights=dict(wing=p['light_wing'], tail=p['light_tail']),
        )


def storm_striking(o):
    return o.t % 150 >= 120


def storm_charging(o):
    return 90 <= o.t % 150 < 120


class An225Game:
    def __init__(self, root):
        self.root = root
        root.title("Lewis's An-225 Adventure!")
        root.resizable(False, False)
        self.cv = tk.Canvas(root, width=W, height=H, bg='#87CEEB', highlightthickness=0)
        self.cv.pack()

        self.state = 'start'            # start | playing | paused | crashing | gameover
        self.clock = 0                  # animation ticks (never resets)
        self.ticks = 0                  # ticks of actual play this run
        self.world_x, self.speed = 0.0, 2.2
        self.score, self.score_f, self.dist, self.level = 0, 0.0, 0.0, 1
        self.hp, self.combo, self.crates = MAX_HP, 1, 0
        self.best, self.new_best, self.over_t = 0, False, 0
        self.obstacles, self.pickups, self.particles, self.popups = [], [], [], []
        self.spawn_t = self.crate_t = self.power_t = 0.0
        self.last_y = H / 2
        self.shake, self.banner = 0.0, None
        self.tips = {}                     # how many of each kind of thing have appeared this flight
        self.spr = Sprites.load()                             # None -> vector artwork
        self.geom = self.spr.plane_geom() if self.spr else VECTOR_GEOM
        self.plane = NS(y=300.0, vy=0.0, pitch=0.0, gear=1.0, invuln=0, shield=0, magnet=0,
                        on_ground=True, susp=0.0, susp_v=0.0, air_t=0)
        self.plane.y = self.floor_y()
        self.clouds = sorted((NS(x=random.uniform(-60, W + 60), y=random.uniform(26, 215),
                                 s=random.uniform(0.45, 1.25), d=random.uniform(0.15, 0.7),
                                 v=random.randint(0, 2))
                              for _ in range(7)), key=lambda c: c.d)

        self.amb = (1.0, 1.0, 1.0)
        self.env = self.environment()
        self._tint = {}
        self.held, self._release, self.mouse = set(), {}, False

        root.bind('<KeyPress>', self.on_key)
        root.bind('<KeyRelease>', self.on_key_up)
        self.cv.bind('<ButtonPress-1>', self.on_mouse_down)
        self.cv.bind('<ButtonRelease-1>', lambda _e: setattr(self, 'mouse', False))
        root.bind('<FocusOut>', self.on_blur)

        self.last, self.acc = time.perf_counter(), 0.0
        self.root.after(10, self.frame)

    # ------------------------------------------------------------------ input
    @property
    def up(self):
        return self.mouse or bool(self.held & UP_KEYS)

    @property
    def down(self):
        return bool(self.held & DOWN_KEYS)

    @staticmethod
    def _key(e):
        return e.keysym if len(e.keysym) > 1 else e.keysym.lower()

    def on_key(self, e):
        k = self._key(e)
        if k in UP_KEYS or k in DOWN_KEYS:
            job = self._release.pop(k, None)
            if job:                                   # auto-repeat: the key never really went up
                self.root.after_cancel(job)
            fresh = k not in self.held
            self.held.add(k)
            if fresh and k in UP_KEYS:
                self.press_up()
        elif k in ('p', 'Escape'):
            self.toggle_pause()
        elif k in ('r', 'Return'):
            if self.state == 'gameover' or (self.state == 'start' and k == 'Return'):
                self.start_game()

    def on_key_up(self, e):
        k = self._key(e)
        if k in self.held and k not in self._release:
            self._release[k] = self.root.after(40, lambda: self._do_release(k))

    def _do_release(self, k):
        self._release.pop(k, None)
        self.held.discard(k)

    def on_mouse_down(self, _e):
        self.mouse = True
        self.press_up()

    def on_blur(self, _e):
        self.held.clear()
        self.mouse = False
        if self.state == 'playing':
            self.state = 'paused'

    def press_up(self):
        if self.state == 'start':
            self.start_game()
        elif self.state == 'gameover':
            if self.over_t > 45:
                self.start_game()
        elif self.state == 'paused':
            self.toggle_pause()
        elif self.state == 'playing':
            if self.plane.on_ground:
                self.plane.pitch -= 0.03              # on the runway a tap just nudges the nose up
            elif self.plane.vy > -2.2:
                self.plane.vy -= 1.2                  # in the air a tap gives a little hop

    def toggle_pause(self):
        if self.state == 'playing':
            self.state = 'paused'
        elif self.state == 'paused':
            self.state = 'playing'

    # ------------------------------------------------------------------ state
    def start_game(self):
        self.state = 'playing'
        self.ticks, self.score, self.score_f, self.dist, self.level = 0, 0, 0.0, 0.0, 1
        self.hp, self.combo, self.crates = MAX_HP, 1, 0
        self.speed = max(self.speed, 0.6)                     # accelerate from wherever we were rolling
        self.new_best, self.over_t = False, 0
        self.obstacles, self.pickups, self.popups = [], [], []
        self.spawn_t, self.crate_t, self.power_t, self.last_y = 170, 200, 620, H / 2
        self.shake = 0.0
        p = self.plane                                        # every flight starts with a take-off roll
        p.vy, p.pitch, p.gear, p.invuln, p.shield, p.magnet = 0.0, 0.0, 1.0, 120, 0, 0
        p.on_ground, p.susp, p.susp_v, p.air_t = True, 0.0, 0.0, 0
        p.y = self.floor_y()
        self.banner = NS(title='Level 1', sub=SKY[0].name, t=150)
        self.tips = {}

    def end_game(self):
        self.state, self.over_t, self.speed = 'gameover', 0, 0.0
        self.new_best = self.score > self.best
        self.best = max(self.best, self.score)

    def environment(self):
        t = 0 if self.state == 'start' else self.ticks / LEVEL_TICKS
        i = int(t) % len(SKY)
        f = smooth(clamp((t % 1 - 0.6) / 0.4, 0, 1))          # blend into the next phase late in the level
        a, b = SKY[i], SKY[(i + 1) % len(SKY)]
        return NS(top=mix3(a.top, b.top, f), bot=mix3(a.bot, b.bot, f), amb=mix3(a.amb, b.amb, f),
                  sun=(lerp(a.sun[0], b.sun[0], f), lerp(a.sun[1], b.sun[1], f)),
                  stars=lerp(a.stars, b.stars, f))

    # --------------------------------------------------------------- geometry
    def plane_to_world(self, lx, ly):
        c, s = math.cos(self.plane.pitch), math.sin(self.plane.pitch)
        return (PLANE_X + lx * c - ly * s, self.plane.y + lx * s + ly * c)

    def plane_capsules(self):
        """Collision shape: fuselage + near wing, as capsules that rotate with the plane."""
        tw = self.plane_to_world
        return [(tw(*a), tw(*b), r) for a, b, r in self.geom['caps']]

    def gear_reach(self, pitch):
        """How far below the plane's centre its lowest wheel is: nose-up puts the rear main
        wheels lowest, nose-down the nose wheel."""
        w, s = self.geom['wheels'], math.sin(pitch)
        return max(w['rear'] * s, w['front'] * s, w['nose'] * s) + w['drop'] * math.cos(pitch)

    def floor_y(self):
        """plane.y when the wheels are on the grass."""
        return RUNWAY_Y - self.gear_reach(self.plane.pitch)

    @staticmethod
    def gap_to(o, caps):
        return min(dist_to_seg(o.x + dx, o.y + dy, a, b) - r - cr
                   for dx, dy, r in SHAPES[o.kind] for a, b, cr in caps)

    @staticmethod
    def lightning_hits(o, caps):
        for a, b, cr in caps:
            for i in range(7):
                px, py = lerp(a[0], b[0], i / 6), lerp(a[1], b[1], i / 6)
                if abs(px - o.x) < 8 + cr * 0.6 and py > o.y + 14:
                    return True
        return False

    # --------------------------------------------------------------- spawning
    def pick_y(self, lo, hi, sep=85):
        y = random.uniform(lo, hi)
        if random.random() < 0.3:                             # sometimes aim at the plane
            y = clamp(self.plane.y + random.uniform(-35, 35), lo, hi)
        else:
            for _ in range(6):
                if abs(y - self.last_y) >= sep:
                    break
                y = random.uniform(lo, hi)
        self.last_y = y
        return y

    def want_tip(self, kind):
        """The first two times each kind of thing appears in a flight it carries a "CATCH!" or
        "DODGE!" label, so a new pilot learns what is what."""
        self.tips[kind] = self.tips.get(kind, 0) + 1
        return self.tips[kind] <= 2

    def new_obstacle(self, kind, tip=True, **props):
        o = NS(kind=kind, x=W + 40.0, y=0.0, vx=0.0, t=0, min_gap=1e9, seed=random.uniform(0, 100),
               phase=random.uniform(0, TAU), scared=False, touched=False, passed=False, icon=False,
               tip=tip and self.want_tip(kind))
        o.__dict__.update(props)
        self.obstacles.append(o)

    def spawn_obstacle(self):
        r, lv = random.random(), self.level
        if lv >= 4 and r < 0.13:
            self.new_obstacle('jet', x=W + 380.0, y=self.pick_y(70, GROUND - 80), vx=4.5)
        elif lv >= 3 and r < 0.33:
            self.new_obstacle('storm', x=W + 70.0, y=self.pick_y(135, 235, 40), t=random.randint(0, 70))
        elif lv >= 2 and r < 0.52:
            self.new_obstacle('balloon', y=self.pick_y(95, GROUND - 90),
                              vy=random.uniform(0.2, 0.45) * random.choice((-1, 1)), col=random.randint(0, 2))
        elif lv >= 2 and r < 0.72:
            y, phase, vx = self.pick_y(95, GROUND - 85), random.uniform(0, TAU), random.uniform(0.8, 1.4)
            self.new_obstacle('bird', y=y, base_y=y, amp=8, phase=phase, vx=vx)
            for k in range(1, (2 if lv >= 5 else 1) + 1):     # V formation: 3 birds, later 5
                for side in (-1, 1):
                    self.new_obstacle('bird', x=W + 40.0 + 26 * k, y=y + side * 18 * k,
                                      base_y=y + side * 18 * k, amp=8, phase=phase, vx=vx, tip=False)
        else:
            y = self.pick_y(60, GROUND - 20)                  # single birds can skim the ground too
            self.new_obstacle('bird', y=y, base_y=y, amp=random.uniform(6, 18), vx=random.uniform(0.8, 1.6))

    def new_pickup(self, kind, x, y, tip=True):
        self.pickups.append(NS(kind=kind, x=float(x), y=y, base_y=y, t=0, phase=random.uniform(0, TAU),
                               tip=tip and self.want_tip(kind)))

    def spawn_crates(self):
        y = random.uniform(70, GROUND - 75)
        for i in range(3 if random.random() < 0.25 else 1):   # sometimes a row of three
            self.new_pickup('crate', W + 30 + i * 70, y, i == 0)

    def spawn_power(self):
        kinds = ['shield', 'magnet']
        if self.hp < MAX_HP:                                  # repair kits only when they're useful
            kinds += ['heart', 'heart']
        self.new_pickup(random.choice(kinds), W + 30, random.uniform(80, GROUND - 90))

    # ---------------------------------------------------------------- effects
    def emit(self, x, y, vx, vy, life, r, col, a=1.0, g=0.0, drag=1.0, grow=0.0, front=False):
        if len(self.particles) < 170:
            self.particles.append(NS(x=x, y=y, vx=vx, vy=vy, life=life, max=life, r=r, col=col, a=a,
                                     g=g, drag=drag, grow=grow, front=front))

    def popup(self, text, x, y, col='#FFD700', size=13):
        self.popups.append(NS(text=text, x=x, y=y, col=col, size=size, life=62))

    def sparkle(self, x, y, col, n=8):
        for _ in range(n):
            a, v = random.uniform(0, TAU), random.uniform(1, 4.2)
            self.emit(x, y, math.cos(a) * v - self.speed * 0.4, math.sin(a) * v, random.randint(16, 28),
                      random.uniform(1.6, 3), col, g=0.05, drag=0.95, front=True)

    def dust(self, x, y, n, power=1.0):
        for _ in range(n):
            self.emit(x + random.uniform(-40, 40), y + random.uniform(-3, 2),
                      -self.speed * 0.7 + random.uniform(-1.2, 0.6), random.uniform(-1.8, -0.2) * power,
                      random.randint(20, 36), random.uniform(3, 6), '#cdbb93', a=0.6, g=0.03, drag=0.97,
                      grow=0.2, front=True)

    def emit_exhaust(self):
        hurt = self.state in ('crashing', 'gameover')
        if self.clock % 3 == 0 and not hurt:
            for i, (ex, ey) in enumerate(self.geom['exhaust']):
                far = i >= 3
                x, y = self.plane_to_world(ex, ey)
                self.emit(x, y, -self.speed * 0.55 - 1.2, random.uniform(-0.12, 0.12), 18,
                          1.6 if far else 2.2, '#ffffff', a=0.25 if far else 0.42, grow=0.22)
        # a struggling engine smokes when the hull is nearly gone
        if self.clock % 5 == 0 and (hurt or (self.hp == 1 and self.state == 'playing')):
            x, y = self.plane_to_world(self.geom['exhaust'][1][0] + 4, self.geom['exhaust'][1][1])
            self.emit(x, y, -self.speed * 0.6 - 0.5, random.uniform(-0.9, -0.3), 40, 3.5, '#3a3d44',
                      a=0.7, grow=0.3, front=True)

    # ------------------------------------------------------------- game logic
    def hit_plane(self, o, why='Ouch!'):
        p = self.plane
        if p.invuln > 0 or self.state != 'playing':
            return
        if o and o.kind == 'bird':
            o.scared = True
            self.sparkle(o.x, o.y, '#e9edf3', 7)              # a puff of feathers
        if o and o.kind == 'balloon':
            o.vy = (-1 if o.y < p.y else 1) * 1.3

        if p.shield > 0:                                      # the shield soaks up one hit
            p.shield, p.invuln, self.shake = 0, 60, 5
            self.sparkle(PLANE_X + 40, p.y, '#8fd8ff', 14)
            self.popup('Shield saved you!', PLANE_X, p.y - 46, '#8fd8ff')
            return
        self.hp -= 1
        self.combo, p.invuln, self.shake = 1, 120, 11
        if o:
            p.vy += -2.6 if p.y < o.y else 2.6                # knocked away from whatever we hit
            self.sparkle((o.x + PLANE_X) / 2, (o.y + p.y) / 2, '#ffcf5a', 10)
        self.popup(why, PLANE_X, p.y - 46, '#ff8a8a')
        if self.hp <= 0:
            self.state, p.shield, p.magnet = 'crashing', 0, 0

    # --------------------------------------------------------- flight physics
    def fly(self):
        """In the air: thrust, gravity and drag, an air cushion close to the ground, and a
        flare that brings the nose up before the wheels touch."""
        p = self.plane
        alt = self.floor_y() - p.y
        # A heavy aeroplane: it gathers vertical speed slowly and never gets much of it.  Held
        # inputs settle at about 3.6 px/tick up, 2.5 down hands-off and 5 in a dive.
        ay = 0.12                                             # gravity
        if self.up:
            ay -= 0.29                                        # lift while climbing
        if self.down:
            ay += 0.12                                        # nose-down dive
        if alt < 50:
            ay -= 0.05 * (1 - max(alt, 0) / 50)               # ground effect
        if p.y < 52:
            ay += (52 - p.y) * 0.02                           # thin air: soft ceiling
        p.vy = clamp((p.vy + ay) * 0.955, -4.2, 5.6)          # drag gives the big plane its weight
        p.y += p.vy
        if p.y < 34:
            p.y, p.vy = 34.0, max(p.vy, 0)

        want = clamp(math.atan2(p.vy, 10), -0.45, 0.4)
        want = lerp(want, min(want, -0.05), clamp(1 - alt / 70, 0, 1))        # flare
        p.pitch += (want - p.pitch) * 0.1
        p.gear = clamp(p.gear + (0.06 if alt < 110 else -0.04), 0, 1)
        p.air_t += 1

        fy = self.floor_y()
        if p.y >= fy:
            p.y = fy
            if p.vy >= 0:
                self.touch_down()

    def touch_down(self):
        """The wheels have met the grass.  A gentle arrival settles onto the gear, a firm one
        skips once, and a hard one bounces and costs a heart.  Each bounce loses most of its
        energy, so the plane always ends up rolling instead of hopping forever."""
        p, w = self.plane, self.geom['wheels']
        v, wx = p.vy, PLANE_X + (w['rear'] + w['front']) / 2
        p.susp_v += min(v, 5) * 0.8                           # the landing gear soaks up the hit
        if v > HARD_LANDING:
            p.vy = -v * 0.38
            self.dust(wx, RUNWAY_Y, 10, 1.5)
            self.hit_plane(None, 'Hard landing!')
        elif v > 1.7:
            p.vy = -v * 0.25
            self.dust(wx, RUNWAY_Y, 5)
        else:
            p.on_ground, p.vy = True, 0.0
            self.dust(wx, RUNWAY_Y, 3, 0.6)
            if p.air_t > 90:
                self.popup('Smooth landing!', PLANE_X, p.y - 46, '#d9f7c9', 11)

    def ground_roll(self):
        """Rolling on the gear: the wheels stay planted and nothing bounces.  Holding climb
        raises the nose about the main wheels (faster with more speed); once rotated, the
        plane lifts off."""
        p = self.plane
        p.vy, p.air_t = 0.0, 0
        p.gear = min(1, p.gear + 0.06)
        rate = 0.055 * clamp(self.speed / 4, 0.4, 1) if self.up else 0.07
        p.pitch += ((-0.17 if self.up else 0) - p.pitch) * rate
        p.y = self.floor_y()
        if self.up and p.pitch < -0.09:
            p.on_ground, p.vy = False, -1.0

    def update_play(self):
        p = self.plane
        self.ticks += 1
        lv = 1 + self.ticks // LEVEL_TICKS
        if lv != self.level:
            self.level = lv
            self.banner = NS(title=f'Level {lv}', sub=SKY[(lv - 1) % len(SKY)].name, t=150)
        target = min(3.5 + (self.level - 1) * 0.38, 7.5)
        self.speed += (target - self.speed) * (0.02 if self.speed < 3.6 else 0.01)    # brisk take-off roll
        if not p.on_ground:                                   # distance and score only count while flying
            self.dist += self.speed
            self.score_f += self.speed / 4
            whole = int(self.score_f)
            self.score += whole
            self.score_f -= whole

        if p.on_ground:
            self.ground_roll()
        else:
            self.fly()
        p.invuln, p.shield, p.magnet = max(0, p.invuln - 1), max(0, p.shield - 1), max(0, p.magnet - 1)

        # -- spawning
        self.spawn_t -= 1
        if self.spawn_t <= 0:
            self.spawn_obstacle()
            self.spawn_t = max(56, 130 - self.level * 9) * random.uniform(0.8, 1.25)
        self.crate_t -= 1
        if self.crate_t <= 0:
            self.spawn_crates()
            self.crate_t = random.uniform(140, 230)
        self.power_t -= 1
        if self.power_t <= 0:
            self.spawn_power()
            self.power_t = random.uniform(700, 1000)

        # -- collisions
        caps = self.plane_capsules()
        for o in self.obstacles:
            if not o.passed and o.x < PLANE_X - 135:          # it got past us: was it a near miss?
                o.passed = True
                if not o.touched and not o.scared and o.kind != 'storm' and o.min_gap < 13:
                    self.score += 25
                    self.popup('Close call! +25', PLANE_X + 20, p.y - 40, '#ffffff', 11)
            if o.scared or abs(o.x - PLANE_X) > 210:
                continue
            gap = self.gap_to(o, caps)
            o.min_gap = min(o.min_gap, gap)
            if gap < 0:
                o.touched = True
                self.hit_plane(o)
            if o.kind == 'storm':
                if abs(o.x - PLANE_X) < 130 and not p.on_ground:
                    p.vy += random.uniform(-0.12, 0.12)       # turbulence
                if storm_striking(o) and self.lightning_hits(o, caps):
                    o.touched = True
                    self.hit_plane(o, 'Zap!')
            if self.state != 'playing':
                return

        # -- pickups
        keep = []
        for c in self.pickups:
            if p.magnet > 0 and math.hypot(c.x - PLANE_X, c.y - p.y) < 200:   # the magnet reels things in
                c.x += (PLANE_X - c.x) * 0.09
                c.base_y += (p.y - c.base_y) * 0.09
            if min(dist_to_seg(c.x, c.y, a, b) - cr for a, b, cr in caps) < 17:
                self.collect(c)
            elif c.x < -40:
                if c.kind == 'crate':
                    self.combo = max(1, self.combo - 1)
            else:
                keep.append(c)
        self.pickups = keep

    def collect(self, c):
        p = self.plane
        if c.kind == 'crate':
            pts = 50 * self.combo
            self.score += pts
            self.crates += 1
            self.popup(f'+{pts}' + (f'  x{self.combo}' if self.combo > 1 else ''), c.x, c.y - 18, '#FFD700',
                       12 + self.combo)
            self.sparkle(c.x, c.y, '#ffe27a')
            self.combo = min(MAX_COMBO, self.combo + 1)
        elif c.kind == 'heart':
            if self.hp < MAX_HP:
                self.hp += 1
                self.popup('Repaired!', c.x, c.y - 18, '#ff9aa8')
            else:
                self.score += 100
                self.popup('+100', c.x, c.y - 18, '#ff9aa8')
            self.sparkle(c.x, c.y, '#ff8fa0', 10)
        elif c.kind == 'shield':
            p.shield = 480
            self.popup('Shield!', c.x, c.y - 18, '#8fd8ff')
            self.sparkle(c.x, c.y, '#8fd8ff', 10)
        else:
            p.magnet = 480
            self.popup('Cargo magnet!', c.x, c.y - 18, '#d3b0ff')
            self.sparkle(c.x, c.y, '#d3b0ff', 10)

    def update_crash(self):
        """Out of hull points: glide down, flare, and roll out to a stop on the gear."""
        p = self.plane
        p.gear = min(1, p.gear + 0.06)
        if not p.on_ground:
            alt = self.floor_y() - p.y
            p.vy = min(p.vy + 0.2, 2.4 if alt < 60 else 4.5)
            p.y += p.vy
            p.pitch += ((-0.06 if alt < 70 else 0.14) - p.pitch) * 0.06
            if p.y >= self.floor_y():
                p.on_ground, p.susp_v, p.vy, self.shake = True, p.susp_v + p.vy, 0.0, 9
                self.dust(PLANE_X, RUNWAY_Y, 14, 1.6)
        else:
            self.speed *= 0.966
            p.pitch += (0 - p.pitch) * 0.07
            if self.speed > 0.6 and self.clock % 4 == 0:
                self.dust(PLANE_X - 20, RUNWAY_Y, 1)
            if self.speed < 0.2:
                self.end_game()
        if p.on_ground:
            p.y = self.floor_y()

    def update_world(self):
        sp = self.speed
        self.world_x += sp
        for c in self.clouds:
            c.x -= sp * c.d * 0.45 + 0.12
            if c.x < -130 * c.s:
                c.x, c.y = W + 130 * c.s, random.uniform(26, 215)

        for o in self.obstacles:
            o.t += 1
            o.x -= sp + o.vx
            if o.kind == 'bird':
                if o.scared:
                    o.y -= 3.2
                    o.x += 1.5
                else:
                    o.y = o.base_y + math.sin(o.t * 0.07 + o.phase) * o.amp
            elif o.kind == 'balloon':
                o.y += o.vy
                if o.y < 85 or o.y > GROUND - 75:
                    o.vy *= -1
            elif o.kind == 'jet' and o.x < W + 40 and self.clock % 3 == 0:
                self.emit(o.x + 30, o.y, -sp * 0.3, random.uniform(-0.1, 0.1), 16, 2.4, '#ffffff', a=0.45, grow=0.25)
            elif o.kind == 'storm' and o.t % 150 == 120 and -40 < o.x < W + 40:
                self.shake = max(self.shake, 3)
        self.obstacles = [o for o in self.obstacles if o.x > -140 and o.y > -60]

        for c in self.pickups:
            c.t += 1
            c.x -= sp * 0.85
            c.y = c.base_y + math.sin(c.t * 0.05 + c.phase) * 6
        if self.state != 'playing':
            self.pickups = [c for c in self.pickups if c.x > -40]

        for q in self.particles:
            q.x += q.vx
            q.y += q.vy
            q.vy += q.g
            q.vx *= q.drag
            q.vy *= q.drag
            q.r += q.grow
            q.life -= 1
        self.particles = [q for q in self.particles if q.life > 0]
        for q in self.popups:
            q.y -= 0.7
            q.life -= 1
        self.popups = [q for q in self.popups if q.life > 0]

        # landing-gear suspension: a damped spring, so touchdowns settle instead of ringing
        p = self.plane
        p.susp_v = (p.susp_v - p.susp * 0.2) * 0.72
        p.susp = clamp(p.susp + p.susp_v, -1.5, 6)
        self.shake *= 0.86
        if self.banner:
            self.banner.t -= 1
            if self.banner.t <= 0:
                self.banner = None

    def tick(self):
        if self.state == 'paused':
            return
        self.clock += 1
        p = self.plane
        if self.state == 'start':
            self.speed = 2.2                                  # taxiing, waiting for take-off
            p.on_ground, p.gear, p.pitch, p.vy = True, 1.0, 0.0, 0.0
            p.y = self.floor_y()
        elif self.state == 'playing':
            self.update_play()
        elif self.state == 'crashing':
            self.update_crash()
        else:
            self.over_t += 1
        self.emit_exhaust()
        self.update_world()

    def frame(self):
        start = time.perf_counter()
        self.acc += min(start - self.last, 0.1)               # never try to catch up more than 0.1 s
        self.last = start
        while self.acc >= STEP:
            self.tick()
            self.acc -= STEP
        self.draw()
        spent = (time.perf_counter() - start) * 1000
        self.root.after(max(1, int(1000 * STEP - spent)), self.frame)

    # ------------------------------------------------------ drawing primitives
    def C(self, hexcol):
        """Tint a colour by the current time-of-day light."""
        v = self._tint.get(hexcol)
        if v is None:
            v = self._tint[hexcol] = css(self.tint3(hex_rgb(hexcol)))
        return v

    def tint3(self, c):
        return (c[0] * self.amb[0], c[1] * self.amb[1], c[2] * self.amb[2])

    def backdrop(self, y):
        """Rough background colour at height y -- used to fake transparency."""
        if y >= GROUND - 40:                                  # grass, or the hills just above it
            return self.tint3(hex_rgb('#6bbf5c' if y >= GROUND else '#4a9150'))
        return mix3(self.env.top, self.env.bot, clamp(y / GROUND, 0, 1))

    def fade(self, hexcol, alpha, y, lit=True):
        c = hex_rgb(hexcol)
        return css(mix3(self.backdrop(y), self.tint3(c) if lit else c, clamp(alpha, 0, 1)))

    def poly(self, pts, fill, outline='', width=1):
        self.cv.create_polygon([v for pt in pts for v in pt], fill=fill, outline=outline, width=width)

    def oval(self, x, y, rx, ry, fill, outline='', width=1):
        self.cv.create_oval(x - rx, y - ry, x + rx, y + ry, fill=fill, outline=outline, width=width)

    def line(self, pts, fill, width=1.0, cap='butt', **kw):
        self.cv.create_line([v for pt in pts for v in pt], fill=fill, width=width, capstyle=cap, **kw)

    def rect(self, x, y, w, h, fill, outline='', width=1):
        self.cv.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=width)

    def text(self, s, x, y, size, fill, anchor='center', style='bold', shadow=None, angle=0):
        font = (FONT, -int(size), style)                      # negative size = pixels
        if shadow:
            self.cv.create_text(x + 1.5, y + 1.5, text=s, font=font, fill=shadow, anchor=anchor, angle=angle)
        self.cv.create_text(x, y, text=s, font=font, fill=fill, anchor=anchor, angle=angle)

    def heart(self, x, y, s, fill):
        self.oval(x - s * 0.5, y - s * 0.3, s * 0.56, s * 0.56, fill)
        self.oval(x + s * 0.5, y - s * 0.3, s * 0.56, s * 0.56, fill)
        self.poly([(x - s * 1.03, y - s * 0.08), (x + s * 1.03, y - s * 0.08), (x, y + s)], fill)

    # ------------------------------------------------------- drawing: scenery
    def draw_sky(self):
        env, bands = self.env, 14
        for i in range(bands):
            self.rect(0, i * GROUND / bands, W, GROUND / bands + 1,
                      css(mix3(env.top, env.bot, (i + 0.5) / bands)))
        if env.stars > 0.12:
            for x, y, ph in STARS:
                k = env.stars * (0.55 + 0.45 * math.sin(self.clock * 0.05 + ph))
                self.rect(x, y, 2, 2, css(mix3(self.backdrop(y), (255, 255, 255), k)))
            moon = css(mix3(self.backdrop(70), (243, 240, 223), env.stars))
            self.oval(268, 70, 21, 21, moon)
            self.oval(261, 64, 4.5, 4.5, css(mix3(self.backdrop(70), (220, 216, 196), env.stars)))
        sx, sy = env.sun
        if sy < GROUND + 70:
            low = clamp((sy - 150) / 190, 0, 1)               # the sun reddens near the horizon
            col = mix3((255, 250, 220), (255, 150, 80), low)
            for r, k in ((62, 0.18), (44, 0.4)):
                self.oval(sx, sy, r, r, css(mix3(self.backdrop(sy), col, k)))
            self.oval(sx, sy, 27, 27, css(mix3((255, 253, 235), (255, 190, 110), low)))

    def draw_ridge(self, offset, base, amp, seed, fill, sharp):
        pts = [(-20, H + 20)]
        for x in range(-20, W + 21, 14):
            u = x + offset
            n = (math.sin(u * 0.011 + seed) * 0.5 + math.sin(u * 0.023 + seed * 2.3) * 0.3
                 + math.sin(u * 0.047 + seed * 4.1) * 0.2)
            pts.append((x, base - amp * (1 - abs(n) * 1.25 if sharp else 0.5 + 0.5 * n)))
        pts.append((W + 20, H + 20))
        self.poly(pts, fill)

    def sprite(self, name, x, y, scale=1.0, alpha=1.0, glow=False):
        """Draw a 3D sprite with its anchor at (x, y)."""
        m = self.spr.meta[name]
        self.cv.create_image(x - m['ax'] * scale, y - m['ay'] * scale, anchor='nw',
                             image=self.spr.photo(name, scale, alpha, glow))

    def draw_cloud(self, c):
        if self.spr:
            return self.sprite(f'cloud_{c.v}', c.x, c.y, c.s, 0.55 + 0.45 * c.d)
        a = 0.5 + 0.45 * c.d
        shade, white = self.fade('#c3d2e6', a, c.y), self.fade('#ffffff', a, c.y)
        for px, py, r in CLOUD_PUFFS[1:4]:
            self.oval(c.x + px * c.s, c.y + (py + 6) * c.s, r * c.s, r * c.s, shade)
        for px, py, r in CLOUD_PUFFS:
            self.oval(c.x + px * c.s, c.y + py * c.s, r * c.s, r * c.s, white)

    def draw_decor(self):
        C, cell = self.C, 90
        first = int(self.world_x // cell) - 1
        for i in range(first, first + 12):
            h = hash01(i)
            x, y = i * cell - self.world_x + hash01(i + 0.5) * 50, GROUND + 3
            s = 0.8 + hash01(i + 0.25) * 0.55
            if self.spr:
                kind = ('tree' if h < 0.32 else 'pine' if h < 0.47 else 'bush' if h < 0.57
                        else 'house' if h < 0.63 else 'hay' if h < 0.68 else '')
                if kind:
                    self.sprite(kind, x, y, round(s * 20) / 20 if kind in ('tree', 'pine') else 1.0)
                if kind == 'house' and self.env.stars > 0.3:  # windows light up at dusk
                    self.sprite('house_glow', x, y, 1.0, clamp((self.env.stars - 0.3) * 3, 0, 1), True)
                continue
            if h < 0.32:                                      # leafy tree
                self.rect(x - 2 * s, y - 14 * s, 4 * s, 14 * s, C('#7a5230'))
                self.oval(x, y - 24 * s, 14 * s, 14 * s, C('#3c9648'))
                self.oval(x - 4 * s, y - 28 * s, 9 * s, 9 * s, C('#59b962'))
            elif h < 0.47:                                    # pine
                self.rect(x - 1.5 * s, y - 8 * s, 3 * s, 8 * s, C('#6b4628'))
                for k in range(3):
                    self.poly([(x - (13 - k * 3) * s, y - (6 + k * 10) * s),
                               (x + (13 - k * 3) * s, y - (6 + k * 10) * s),
                               (x, y - (24 + k * 10) * s)], C('#2f8455' if k else '#27724a'))
            elif h < 0.57:                                    # bushes
                self.oval(x, y - 5, 8, 8, C('#4aa651'))
                self.oval(x + 10, y - 4, 6, 6, C('#3c9648'))
            elif h < 0.63:                                    # farmhouse (windows glow after dark)
                self.rect(x - 14, y - 17, 28, 17, C('#f1e3c8'))
                self.poly([(x - 18, y - 17), (x + 18, y - 17), (x, y - 31)], C('#c0503a'))
                self.rect(x - 3, y - 10, 6, 10, C('#7a4a2a'))
                glass = '#ffd86b' if self.env.stars > 0.4 else C('#9fd0ee')
                self.rect(x - 11, y - 13, 5, 5, glass)
                self.rect(x + 6, y - 13, 5, 5, glass)
            elif h < 0.68:                                    # hay bale
                self.oval(x, y - 6, 10, 7, C('#e6c75c'), C('#c4a23c'))

    def draw_scenery(self):
        env, C, wx = self.env, self.C, self.world_x

        def haze(hexcol, k):
            return css(mix3(self.tint3(hex_rgb(hexcol)), env.bot, k))

        for c in self.clouds:
            if c.d < 0.4:
                self.draw_cloud(c)
        self.draw_ridge(wx * 0.06, GROUND - 6, 165, 1, haze('#6a7fae', 0.5), True)
        self.draw_ridge(wx * 0.12, GROUND, 112, 5, haze('#506e96', 0.28), True)
        for c in self.clouds:
            if c.d >= 0.4:
                self.draw_cloud(c)
        self.draw_ridge(wx * 0.25, GROUND + 6, 64, 9, haze('#5a9a66', 0.14), False)
        self.draw_ridge(wx * 0.45, GROUND + 6, 36, 13, C('#4a9150'), False)

        self.rect(-20, GROUND, W + 40, 16, C('#84d06c'))      # grass in three bands, lighter at the horizon
        self.rect(-20, GROUND + 15, W + 40, 16, C('#6bbf5c'))
        self.rect(-20, GROUND + 30, W + 40, H - GROUND, C('#55a84c'))
        sw = 80                                               # mowing stripes show the ground speed
        x = -(wx % (sw * 2)) - sw
        while x < W + sw * 2:
            self.poly([(x, GROUND), (x + sw, GROUND), (x + sw - 26, H + 20), (x - 26, H + 20)], C('#5fb357'))
            x += sw * 2
        self.rect(-20, GROUND, W + 40, 2, C('#b9ee9c'))
        self.draw_decor()

    # ----------------------------------------------------- drawing: the plane
    def draw_plane(self, x, y, pitch, gear, susp):
        """susp is how far the body has sunk onto its landing gear; the wheels stay put."""
        y += susp
        if self.spr:
            photo, ox, oy = self.spr.plane(pitch, gear, susp)
            self.cv.create_image(x - ox, y - oy, anchor='nw', image=photo)
            lights = self.geom['lights']
            for point, period, on, col in ((lights['wing'], 64, 7, '#ff4040'), (lights['tail'], 90, 5, '#ffffff')):
                if self.clock % period < on:
                    lx, ly = self.plane_to_world(*point)
                    self.oval(lx, ly + susp, 3, 3, col)
            return
        C = self.C
        c, s = math.cos(pitch), math.sin(pitch)

        def tp(lx, ly):
            return (x + lx * c - ly * s, y + lx * s + ly * c)

        def shape(pts, fill, outline=None):
            self.poly([tp(*pt) for pt in pts], fill, edge if outline is None else outline)

        def fin(tx, ty, fill, flag):
            shape([(tx + 10, ty + 5), (tx - 9, ty + 5), (tx - 20, ty - 27), (tx - 8, ty - 27)], fill)
            if flag:                                          # Ukrainian flag on the near fin
                self.line([tp(tx - 14.5, ty - 17), tp(tx - 5, ty - 17)], C('#0057B7'), 5)
                self.line([tp(tx - 12.5, ty - 12), tp(tx - 3, ty - 12)], C('#FFD700'), 5)

        def nacelle(cx, cy, k, far):
            ln, r = 13 * k, 5.5 * k
            self.line([tp(cx - ln - 5 * k, cy), tp(cx - ln + 4, cy)], C('#2a2f36'), r * 1.2)     # nozzle
            self.line([tp(cx - ln + r, cy), tp(cx + ln - r, cy)], C('#5b636d' if far else '#737c87'),
                      r * 2, 'round')
            ix, iy = tp(cx + ln - 2.2 * k, cy)
            self.oval(ix, iy, r * 0.95, r * 0.95, C('#dfe5ec'))                                   # intake lip
            self.oval(ix + 0.8 * k, iy, r * 0.62, r * 0.62, C('#14171b'))

        edge = C('#77818c')
        # far side: tailplane, fin, wing and its three engines
        shape([(-84, -10), (-100, -10), (-113, -24), (-103, -24)], C('#a3adb8'))
        fin(-107, -24, C('#aeb7c2'), False)
        shape(FAR_WING, C('#aab4bf'))
        for ex, ey in FAR_ENG:
            nacelle(ex, ey, 0.82, True)

        if gear > 0.02:                                       # landing gear swings down near the ground
            wy = 19.5 + 14 * gear - susp
            strut = C('#5d646c')
            self.line([tp(74, 12), tp(74, wy)], strut, 2.2)
            for sx in (-25, -5, 15):
                self.line([tp(sx, 14), tp(sx, wy)], strut, 2.2)
            self.line([tp(-31, wy), tp(21, wy)], strut, 2.2)
            for wx_ in (71, 77.5, -30, -20, -10, 0, 10, 20):
                px, py = tp(wx_, wy)
                rad = 3.6 if wx_ > 50 else 4.2
                self.oval(px, py, rad, rad, C('#1b1d21'))
                self.oval(px, py, rad * 0.4, rad * 0.4, C('#8a9199'))

        shape(GEAR_POD, C('#a9b3be'))                         # gear pod under the belly
        shape(FUSELAGE, C('#fbfcfd'), '')
        shape(BELLY, C('#c5ced8'), '')                        # shaded underside
        self.line([tp(-80, -1.2), tp(100, -1.2)], C('#0057B7'), 3.2)                             # cheat line
        self.line([tp(-80, 2), tp(100, 2)], C('#FFD700'), 3.2)
        self.poly([tp(*pt) for pt in FUSELAGE], '', edge)
        self.line([tp(63, -14.5), tp(61, 13.5)], C('#aab3bd'))                                   # nose-door seam
        shape([(70, -12.5), (86, -9.6), (93, -5.6), (90, -4.2), (72, -6.2)], C('#1f3650'), '')   # flight deck
        self.line([tp(73, -11), tp(84, -9)], C('#8fc4ee'))
        self.text('AN-225', *tp(51, -8), 7, C('#003366'), angle=-math.degrees(pitch))

        # near side: tailplane, fin, wing and its three engines
        shape([(-82, -5), (-100, -4), (-117, 9), (-105, 9)], C('#cfd7df'))
        fin(-110, 9, C('#e3e8ed'), True)
        shape(NEAR_WING, C('#dfe5eb'))
        self.line([tp(-1, -9), tp(-46, 30)], C('#9aa4af'))                                       # flap line
        for ex, ey in NEAR_ENG:
            nacelle(ex, ey, 1, False)

        # navigation lights
        if self.clock % 64 < 7:
            self.oval(*tp(-43, 30), 3, 3, '#ff4040')
        if self.clock % 90 < 5:
            self.oval(*tp(-114, -19), 3, 3, '#ffffff')

    def draw_aura(self):
        p = self.plane
        if p.magnet > 0:
            k = (self.clock % 40) / 40
            self.oval(PLANE_X, p.y, 190 - 150 * k, 110 - 86 * k, '', self.fade('#c79bff', 1 - k, p.y, False), 2)
        if p.shield > 0 and (p.shield > 90 or self.clock % 12 < 7):
            pulse = 1 + 0.03 * math.sin(self.clock * 0.15)
            self.oval(PLANE_X - 4, p.y - 4, 132 * pulse, 64 * pulse, '', '#bfeaff', 2.5)
            self.oval(PLANE_X - 4, p.y - 4, 127 * pulse, 60 * pulse, '', self.fade('#78c8ff', 0.6, p.y, False), 1.5)

    # ------------------------------------------ drawing: catch / dodge markers
    def haze(self, x, y, rx, ry, rgb, alpha, bg=None):
        """A soft coloured glow: a real gradient with Pillow, two flat ovals without it."""
        if self.spr:
            self.cv.create_image(x, y, image=self.spr.glow(rgb, rx, ry, alpha))
        else:
            back = bg or self.backdrop(y)
            for size, a in ((0.8, 0.4), (0.5, 0.75)):
                self.oval(x, y, rx * size, ry * size, css(mix3(back, rgb, alpha * a)))

    def draw_good_ring(self, c, bg=None):
        dy, r0 = GOOD[c.kind]
        k = 0.5 + 0.5 * math.sin(self.clock * 0.14 + c.phase)
        r, x, y = r0 + k * 2.5, c.x, c.y + dy
        self.haze(x, y, r0 + 19, r0 + 19, GREEN, 0.3 + 0.2 * k, bg)
        self.oval(x, y, r, r, '', '#0b4a22', 6.5)
        self.oval(x, y, r, r, '', '#6dff9a', 3)
        for i in range(3):                                    # twinkles riding round the ring
            a = self.clock * 0.045 + c.phase + i * TAU / 3
            tx, ty = x + math.cos(a) * r, y + math.sin(a) * r
            s = 4.2 + 1.3 * math.sin(self.clock * 0.2 + i * 2)
            q = s * 0.28
            self.poly([(tx, ty - s), (tx + q, ty - q), (tx + s, ty), (tx + q, ty + q), (tx, ty + s),
                       (tx - q, ty + q), (tx - s, ty), (tx - q, ty - q)], '#ffffff')
        if c.tip:
            self.text('CATCH!', x, y - r - 14, 14, '#8dffb0', shadow='#04280f')

    @staticmethod
    def unmarked(o):
        """Birds that are fleeing, and jets that are still only a warning arrow, carry no marker."""
        return o.scared or (o.kind == 'jet' and o.x > W + 14)

    def draw_danger_haze(self, o, bg=None):
        if self.unmarked(o):
            return
        dy, rx, ry = DANGER[o.kind][:3]
        self.haze(o.x, o.y + dy, rx, ry, RED, 0.46 + 0.1 * math.sin(self.clock * 0.2 + o.phase), bg)

    def draw_danger_sign(self, o):
        if self.unmarked(o):
            return
        dy, k = DANGER[o.kind][3:]
        x, y = o.x, o.y + dy + math.sin(self.clock * 0.18 + o.phase) * 1.5
        self.poly([(x, y - 15 * k), (x + 9.5 * k, y + k), (x - 9.5 * k, y + k)], '#ff2d2d', '#ffffff', 1.5)
        self.text('!', x, y - 5.2 * k, 11.5 * k, '#ffffff')
        if o.tip and o.kind == 'bird':                        # beside the sign, clear of the flock
            self.text('DODGE!', x - 12, y - 7, 14, '#ff9a9a', 'e', shadow='#460000')
        elif o.tip:
            self.text('DODGE!', x, y - 15 * k - 12, 14, '#ff9a9a', shadow='#460000')

    # --------------------------------------------- drawing: obstacles/pickups
    def draw_bird(self, o):
        if self.spr:
            frame = (0, 1, 2, 1)[int((self.clock + o.phase * 3) / 5) % 4]
            return self.sprite(f'bird_{frame}', o.x, o.y)
        C, x, y = self.C, o.x, o.y
        flap = math.sin(self.clock * 0.32 + o.phase)
        self.poly([(x - 3, y - 2), (x + 5, y - 3 - 11 * flap), (x + 12, y - 2 - 9 * flap), (x + 5, y + 1)],
                  C('#2c3038'))                                                                  # far wing
        self.poly([(x + 8, y - 1), (x + 17, y - 4), (x + 16, y + 2.5), (x + 8, y + 2)], C('#2c3038'))
        self.oval(x, y, 10, 5.5, C('#454b57'))
        self.oval(x - 1, y + 2, 6.5, 2.7, C('#9aa2b0'))
        self.oval(x - 9, y - 3, 4.6, 4.6, C('#454b57'))
        self.poly([(x - 13, y - 4.2), (x - 19.5, y - 2.4), (x - 13, y - 1)], C('#f2a23a'))
        self.oval(x - 10.6, y - 4, 1.4, 1.4, '#ffffff')
        self.poly([(x - 4, y - 2), (x + 3, y - 5 - 14 * flap), (x + 13, y - 3 - 13 * flap),
                   (x + 8, y - 1 - 3 * flap), (x + 5, y + 1)], C('#5d6573'))                     # near wing

    def draw_balloon(self, o):
        if self.spr:
            return self.sprite(f'balloon_{o.col}', o.x, o.y)
        C, x, y = self.C, o.x, o.y
        a, b = BALLOON_COLS[o.col]
        self.line([(x - 8, y + 14), (x - 6, y + 28)], C('#5a4632'))
        self.line([(x + 8, y + 14), (x + 6, y + 28)], C('#5a4632'))
        self.poly([(x - 21, y - 2), (x + 21, y - 2), (x + 9, y + 15), (x - 9, y + 15)], C(a))
        for rx, col in ((32, a), (22, b), (11, a)):           # nested ovals read as gores
            self.oval(x, y - 16, rx, 27, C(col))
        self.rect(x - 8, y + 27, 16, 11, C('#8a5a2b'), C('#5f3c18'))

    def draw_storm(self, o):
        C, x, y = self.C, o.x, o.y
        strike, charging = storm_striking(o), storm_charging(o)
        fall = GROUND - y - 18
        rain = self.fade('#bcd6f2', 0.5, (y + GROUND) / 2)
        for i in range(0 if o.icon else 12):
            ry = (self.clock * 7 + hash01(i * 3.1 + o.seed) * 400) % fall
            rx = x - 52 + hash01(i + o.seed) * 104 - ry * 0.12
            self.line([(rx, y + 18 + ry), (rx - 1.2, y + 28 + ry)], rain, 1.2)
        if charging:                                          # warning column before the bolt
            self.line([(x, y + 14), (x, GROUND)], '#ffe86a', 3, dash=(6, 6))
        lit = strike or (charging and self.clock % 8 < 4)
        if self.spr:
            self.sprite('storm_lit' if lit else 'storm', x, y, glow=lit)
        else:
            for px, py, r in STORM_PUFFS[:4]:
                self.oval(x + px, y + py + 6, r, r, C('#34384e'))
            top = '#e3e6ff' if strike else C('#8f95b8' if lit else '#5f6584')
            for px, py, r in STORM_PUFFS:
                self.oval(x + px, y + py, r, r, top)
        if strike:
            seed = self.clock // 3 + o.seed
            pts, yy, i = [(x, y + (26 if self.spr else 16))], y + 38, 1
            while yy < GROUND:
                pts.append((x + (hash01(seed + i * 7.7) - 0.5) * 26, yy))
                yy += 22
                i += 1
            pts.append((x + (hash01(seed) - 0.5) * 14, GROUND + 2))
            self.line(pts, '#ffe98a', 6, 'round')
            self.line(pts, '#ffffff', 2, 'round')
            self.oval(pts[-1][0], GROUND, 12, 5, '#fff7c2')

    def draw_jet(self, o):
        C, x, y = self.C, o.x, o.y
        if x > W + 14:                                        # warning marker before it arrives
            if self.clock % 18 < 11:
                self.poly([(W - 44, y), (W - 16, y - 16), (W - 16, y + 16)], '#ff4d4d', '#ffffff')
                self.text('!', W - 25, y, 17, '#ffffff')
            return
        self.oval(x + 32, y, 6 + 2 * math.sin(self.clock * 0.9), 4, '#ffb347')
        if self.spr:
            return self.sprite('jet', x, y)
        self.poly([(x + 14, y - 4), (x + 23, y - 5), (x + 29, y - 17), (x + 21, y - 17)], C('#a52f3a'))
        self.line([(x - 18, y), (x + 20, y)], C('#dc4753'), 12, 'round')
        self.poly([(x - 22, y - 5.5), (x - 36, y + 0.5), (x - 22, y + 5.5)], C('#f3f5f7'))
        self.oval(x - 9, y - 4.5, 8, 3.4, C('#bfe6ff'))
        self.poly([(x - 4, y + 2), (x + 11, y + 2), (x + 20, y + 13), (x + 9, y + 13)], C('#a52f3a'))

    def draw_crate(self, c):
        if self.spr:
            return self.sprite('crate', c.x, c.y)
        C, x, y = self.C, c.x, c.y
        for cx, bx in ((-19, -9), (-7, -4), (7, 4), (19, 9)):
            self.line([(x + cx, y - 25), (x + bx, y - 9)], C('#f5f5f5'))
        self.cv.create_arc(x - 21, y - 40, x + 21, y - 10, start=0, extent=180, style='chord',
                           fill=C('#ffffff'), outline=C('#c9d3df'))                              # plain white canopy
        self.rect(x - 11, y - 9, 22, 20, C('#b9772f'), C('#74461a'), 2)                          # wooden crate
        self.line([(x - 11, y - 9), (x + 11, y + 11)], C('#74461a'))
        self.line([(x + 11, y - 9), (x - 11, y + 11)], C('#74461a'))
        self.rect(x - 11, y - 1.5, 22, 5, C('#ffd54a'))

    def draw_power(self, c):
        x, y, col = c.x, c.y, POWER_COL[c.kind]
        r = 14 * (1 + 0.08 * math.sin(self.clock * 0.12 + c.phase))
        if self.spr:
            return self.sprite('power_' + c.kind, x, y, round(r / 14 * 25) / 25, glow=True)
        self.oval(x, y, r, r, col, '#ffffff', 2)
        if c.kind == 'heart':
            self.heart(x, y + 0.5, 6.2, '#ffffff')
        elif c.kind == 'shield':
            self.poly([(x, y - 7.5), (x + 6.5, y - 5), (x + 6.5, y + 1), (x + 4.5, y + 5.5), (x, y + 8.5),
                       (x - 4.5, y + 5.5), (x - 6.5, y + 1), (x - 6.5, y - 5)], '#ffffff')
        else:
            self.cv.create_arc(x - 5, y - 4, x + 5, y + 6, start=180, extent=180, style='arc',
                               outline='#ffffff', width=3.6)
            self.line([(x - 5, y - 6.5), (x - 5, y + 1.5)], '#ffffff', 3.6)
            self.line([(x + 5, y - 6.5), (x + 5, y + 1.5)], '#ffffff', 3.6)

    def draw_particles(self, front):
        for q in self.particles:
            if q.front == front:
                self.oval(q.x, q.y, q.r, q.r, self.fade(q.col, q.a * q.life / q.max, q.y))

    # ------------------------------------------------- drawing: HUD & screens
    def draw_hud(self):
        p = self.plane
        for i in range(MAX_HP):
            self.heart(28 + i * 24, 27, 8, '#ff5d73' if i < self.hp else '#5b6b8c')
        self.text(str(self.score), 18, 55, 22, '#ffffff', 'w', shadow='#04163e')
        if self.combo > 1:
            self.oval(143, 55, 24, 11, '#FFD700')
            self.text(f'x{self.combo}', 143, 55, 15, '#3a2a00')
        self.text(f'Level {self.level}', W - 16, 24, 16, '#FFD700', 'e', shadow='#04163e')
        self.text(f'{self.dist / 2000:.1f} km', W - 16, 43, 12, '#ffffff', 'e', 'normal', '#04163e')
        self.text(f'Best {max(self.best, self.score)}', W - 16, 59, 12, '#d6e6ff', 'e', 'normal', '#04163e')

        y = 80                                                # active power-up timers
        for kind, left, label in (('shield', p.shield, 'Shield'), ('magnet', p.magnet, 'Magnet')):
            if left > 0:
                self.rect(12, y, 116, 16, '#0b1f48', '#ffffff')
                self.rect(13, y + 1, 114 * left / 480, 14, POWER_COL[kind])
                self.text(label, 18, y + 8, 11, '#ffffff', 'w')
                y += 22

        if self.banner:
            self.text(self.banner.title, W / 2, 96, 34, '#FFD700', shadow='#04163e')
            self.text(self.banner.sub, W / 2, 124, 16, '#ffffff', shadow='#04163e')

    def draw_legend(self):
        """One of everything, drawn exactly as it looks in flight, sorted into catch and dodge."""
        good_bg, bad_bg = hex_rgb('#103557'), hex_rgb('#232652')
        boxes = ((108, 242, 'CATCH THESE', '#103557', '#6dff9a', '#06361a'),
                 (358, 334, 'DODGE THESE', '#232652', '#ff7b7b', '#4a0606'))
        for x, w, _title, fill, line, _ink in boxes:
            self.rect(x, 122, w, 146, fill, line, 2)
        for kind, x, label in LEGEND_GOOD:
            c = NS(kind=kind, x=x, y=213 if kind == 'crate' else 204, t=0, phase=float(x), tip=False)
            self.draw_good_ring(c, good_bg)
            (self.draw_crate if kind == 'crate' else self.draw_power)(c)
            self.text(label, x, 254, 11, '#d9ffe6', style='normal')
        for kind, x, label in LEGEND_BAD:
            o = NS(kind=kind, x=x, y=212 if kind in ('bird', 'jet') else 204, t=0, phase=0.0, col=0, seed=1,
                   scared=False, tip=False, icon=True)
            self.draw_danger_haze(o, bad_bg)
            getattr(self, 'draw_' + kind)(o)
            self.draw_danger_sign(o)
            self.text(label, x, 254, 11, '#ffe0e0', style='normal')
        for x, w, title, _fill, line, ink in boxes:           # the title tabs go on last, over any glow
            self.rect(x + w / 2 - 62, 111, 124, 22, line)
            self.text(title, x + w / 2, 122, 13, ink)

    def draw_start_screen(self):
        cx = W / 2
        self.rect(cx - 300, 12, 600, 290, '#0b2253', '#FFD700', 3)
        self.text("Lewis's An-225 Adventure!", cx, 42, 30, '#FFD700', shadow='#04163e')
        self.text('Hold SPACE or UP (or hold the mouse) to take off and climb   -   DOWN to dive', cx, 71, 13,
                  '#aadaff', style='normal')
        self.text('Catch crates in a row for a combo up to x5   -   Land softly or lose a heart!', cx, 91, 13,
                  '#FFD700', style='normal')
        self.draw_legend()
        if self.clock % 60 < 42:
            self.text('Press SPACE or click to take off!', cx, 285, 16, '#ffffff')
        if self.best:
            self.text(f'Best: {self.best}', cx, 318, 14, '#ffffff', shadow='#04163e')

    def draw_game_over(self):
        cx, cy = W / 2, H / 2 - 16
        self.rect(cx - 235, cy - 112, 470, 224, '#0b2253', '#FFD700', 3)
        self.text('The An-225 needs a rest!', cx, cy - 80, 23, '#FFD700')
        self.text(str(self.score), cx, cy - 40, 40, '#ffffff')
        if self.new_best:
            self.text('* New best score! *', cx, cy - 4, 16, '#ffe27a')
        else:
            self.text(f'Best: {self.best}', cx, cy - 4, 16, '#aadaff')
        plural = '' if self.crates == 1 else 's'
        self.text(f'Level {self.level}   -   {self.dist / 2000:.1f} km flown   -   {self.crates} crate{plural}',
                  cx, cy + 29, 14, '#d6e6ff', style='normal')
        if self.over_t > 45 and self.clock % 60 < 42:
            self.text('Press R or SPACE to fly again!', cx, cy + 62, 16, '#ffffff')
        self.text('Amazing flying, Lewis!', cx, cy + 91, 14, '#FFD700', style='italic')

    def draw(self):
        cv, p = self.cv, self.plane
        cv.delete('all')
        self.env = self.environment()
        amb = tuple(round(v / 255 * 40) / 40 for v in self.env.amb)
        if amb != self.amb:
            self.amb = amb
            self._tint.clear()
        if self.spr:
            self.spr.set_ambient(amb)

        self.draw_sky()
        self.draw_scenery()
        alt = clamp((self.floor_y() - p.y) / 330, 0, 1)       # shadow shrinks with altitude
        self.oval(PLANE_X - 6, RUNWAY_Y + 3, 100 * (1 - alt * 0.5), 7 * (1 - alt * 0.5),
                  self.fade('#0b2010', 0.38 * (1 - alt * 0.8), RUNWAY_Y + 3))
        for o in self.obstacles:
            self.draw_danger_haze(o)
        for o in self.obstacles:
            if o.kind == 'storm':
                self.draw_storm(o)
        for c in self.pickups:
            self.draw_good_ring(c)
            (self.draw_crate if c.kind == 'crate' else self.draw_power)(c)
        for o in self.obstacles:
            if o.kind == 'bird':
                self.draw_bird(o)
            elif o.kind == 'balloon':
                self.draw_balloon(o)
            elif o.kind == 'jet':
                self.draw_jet(o)
        for o in self.obstacles:
            self.draw_danger_sign(o)
        self.draw_particles(False)
        if not (p.invuln > 0 and self.state == 'playing' and self.clock % 10 < 3):   # blink while invulnerable
            self.draw_plane(PLANE_X, p.y, p.pitch, p.gear, p.susp)
        self.draw_aura()
        self.draw_particles(True)
        if self.shake > 0.5:                                  # screen shake moves the world, not the HUD
            cv.move('all', random.uniform(-self.shake, self.shake), random.uniform(-self.shake, self.shake))

        for q in self.popups:
            self.text(q.text, q.x, q.y, q.size, q.col, shadow='#04163e')
        if self.state == 'start':
            self.draw_start_screen()
        else:
            self.draw_hud()
        if self.state == 'gameover':
            self.draw_game_over()
        elif self.state == 'paused':
            self.rect(W / 2 - 190, H / 2 - 50, 380, 100, '#0b2253', '#FFD700', 3)
            self.text('Paused', W / 2, H / 2 - 14, 34, '#FFD700')
            self.text('Press P or SPACE to keep flying', W / 2, H / 2 + 22, 14, '#ffffff', style='normal')


if __name__ == '__main__':
    root = tk.Tk()
    An225Game(root)
    root.mainloop()
