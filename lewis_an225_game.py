import tkinter as tk
import random

WIDTH = 800
HEIGHT = 450
PLANE_X = 150  # fixed horizontal center of the plane


class An225Game:
    def __init__(self, root):
        self.root = root
        self.root.title("Lewis's An-225 Adventure!")
        self.root.resizable(False, False)
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg='#87CEEB')
        self.canvas.pack()

        self.state = 'start'
        self.high_score = 0
        self._reset()

        self.root.bind('<space>', self.on_up)
        self.root.bind('<Up>', self.on_up)
        self.root.bind('<w>', self.on_up)
        self.root.bind('<Down>', self.on_down)
        self.root.bind('<s>', self.on_down)
        self.root.bind('<r>', self.on_restart)

        self.root.after(100, self._show_start)

    # ------------------------------------------------------------------ state

    def _reset(self):
        self.plane_y = HEIGHT // 2
        self.vel_y = 0
        self.score = 0
        self.frame = 0
        self.speed = 4.0
        self.obstacles = []   # [x, y, kind]
        self.cargo = []       # [x, y]
        self.clouds = []      # [x, y, w]

    # -------------------------------------------------------------- screens

    def _show_start(self):
        self.canvas.delete('all')
        self._draw_sky_static()
        self.canvas.create_text(WIDTH // 2, 75,
                                text="Lewis's An-225 Adventure!",
                                font=('Arial', 26, 'bold'), fill='#003366')
        self.canvas.create_text(WIDTH // 2, 115,
                                text="Fly the World's Biggest Cargo Plane!",
                                font=('Arial', 14), fill='#003366')
        self._draw_plane(PLANE_X - 90, HEIGHT // 2 - 10)
        box_y = 310
        self.canvas.create_rectangle(WIDTH // 2 - 230, box_y,
                                     WIDTH // 2 + 230, box_y + 105,
                                     fill='#003366', outline='#FFD700', width=2)
        self.canvas.create_text(WIDTH // 2, box_y + 20,
                                text="SPACE or UP arrow  ->  Fly UP",
                                font=('Arial', 13), fill='white')
        self.canvas.create_text(WIDTH // 2, box_y + 45,
                                text="DOWN arrow  ->  Dive DOWN",
                                font=('Arial', 13), fill='white')
        self.canvas.create_text(WIDTH // 2, box_y + 70,
                                text="Avoid birds & storms!  Collect cargo crates for +50 pts!",
                                font=('Arial', 11), fill='#FFD700')
        self.canvas.create_text(WIDTH // 2, box_y + 92,
                                text="Press SPACE or UP to start",
                                font=('Arial', 12, 'italic'), fill='#aadaff')

    def _show_game_over(self):
        if self.score > self.high_score:
            self.high_score = self.score
        cx, cy = WIDTH // 2, HEIGHT // 2
        self.canvas.create_rectangle(cx - 220, cy - 100, cx + 220, cy + 105,
                                     fill='#003366', outline='#FFD700', width=3)
        self.canvas.create_text(cx, cy - 65,
                                text="Oh no -- An-225 needs a rest!",
                                font=('Arial', 17, 'bold'), fill='#FFD700')
        self.canvas.create_text(cx, cy - 25,
                                text=f"Score:  {self.score}",
                                font=('Arial', 22, 'bold'), fill='white')
        self.canvas.create_text(cx, cy + 15,
                                text=f"Best:   {self.high_score}",
                                font=('Arial', 16), fill='#aadaff')
        self.canvas.create_text(cx, cy + 52,
                                text="Press  R  to fly again!",
                                font=('Arial', 14), fill='white')
        self.canvas.create_text(cx, cy + 82,
                                text="Amazing flying, Lewis!",
                                font=('Arial', 13, 'italic'), fill='#FFD700')

    # -------------------------------------------------------------- game loop

    def _start(self):
        self.state = 'playing'
        self._reset()
        self._loop()

    def _loop(self):
        if self.state != 'playing':
            return

        self.frame += 1
        self.score += 1

        # gradually speed up
        if self.frame % 400 == 0:
            self.speed = min(self.speed + 0.5, 13)

        # spawn obstacles
        interval = max(32, 95 - self.score // 70)
        if self.frame % interval == 0:
            y = random.randint(55, HEIGHT - 75)
            kind = 'storm' if random.random() < 0.25 else 'bird'
            self.obstacles.append([float(WIDTH + 10), y, kind])

        # spawn cargo crates
        if self.frame % 210 == 0:
            y = random.randint(55, HEIGHT - 75)
            self.cargo.append([float(WIDTH + 10), y])

        # spawn background clouds
        if self.frame % 70 == 0:
            y = random.randint(15, HEIGHT // 2 - 30)
            w = random.randint(50, 110)
            self.clouds.append([float(WIDTH + w), y, w])

        # physics
        self.vel_y += 0.36
        self.vel_y = min(self.vel_y, 9.0)
        self.plane_y += self.vel_y

        if self.plane_y < 28:
            self.plane_y = 28
            self.vel_y = 0
        if self.plane_y > HEIGHT - 48:
            self._end()
            return

        # move everything left
        for o in self.obstacles:
            o[0] -= self.speed
        self.obstacles = [o for o in self.obstacles if o[0] > -65]

        for c in self.cargo:
            c[0] -= self.speed * 0.85
        self.cargo = [c for c in self.cargo if c[0] > -30]

        for cl in self.clouds:
            cl[0] -= 1.6
        self.clouds = [cl for cl in self.clouds if cl[0] > -130]

        # collision with obstacles  (fuselage hitbox)
        for o in self.obstacles:
            ox, oy = o[0], o[1]
            if PLANE_X - 75 < ox < PLANE_X + 20 and abs(self.plane_y - oy) < 22:
                self._end()
                return

        # collect cargo
        keep = []
        for c in self.cargo:
            cx, cy = c[0], c[1]
            if PLANE_X - 85 < cx < PLANE_X + 20 and abs(self.plane_y - cy) < 30:
                self.score += 50
            else:
                keep.append(c)
        self.cargo = keep

        self._render()
        self.root.after(16, self._loop)

    def _end(self):
        self.state = 'gameover'
        self._render()
        self._show_game_over()

    # -------------------------------------------------------------- rendering

    def _render(self):
        self.canvas.delete('all')
        self._draw_sky_static()
        for cl in self.clouds:
            self._draw_cloud(cl[0], cl[1], cl[2])
        for c in self.cargo:
            self._draw_cargo(c[0], c[1])
        for o in self.obstacles:
            if o[2] == 'bird':
                self._draw_bird(o[0], o[1])
            else:
                self._draw_storm(o[0], o[1])
        self._draw_plane(PLANE_X - 90, int(self.plane_y))
        # HUD shadow then text
        self.canvas.create_text(14, 14, anchor='nw',
                                text=f'Score: {self.score}',
                                font=('Arial', 18, 'bold'), fill='white')
        self.canvas.create_text(12, 12, anchor='nw',
                                text=f'Score: {self.score}',
                                font=('Arial', 18, 'bold'), fill='#003366')
        lvl = int((self.speed - 4) * 2) + 1
        self.canvas.create_text(12, 40, anchor='nw',
                                text=f'Level: {lvl}',
                                font=('Arial', 12), fill='#003366')

    def _draw_sky_static(self):
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill='#87CEEB', outline='')
        self.canvas.create_rectangle(0, HEIGHT - 42, WIDTH, HEIGHT, fill='#90EE90', outline='')
        self.canvas.create_rectangle(0, HEIGHT - 10, WIDTH, HEIGHT, fill='#228B22', outline='')

    # ------------------------------------------------------ draw helpers

    def _draw_plane(self, x, y):
        # fuselage body
        self.canvas.create_oval(x + 15, y - 14, x + 158, y + 14,
                                fill='white', outline='#aaa', width=1)
        # nose
        self.canvas.create_polygon(x + 15, y - 12, x, y, x + 15, y + 12,
                                   fill='white', outline='#aaa')
        # main wing
        self.canvas.create_polygon(x + 55, y - 4, x + 82, y - 44,
                                   x + 118, y - 44, x + 90, y + 7,
                                   fill='#ddd', outline='#aaa')
        # 6 engines (3 visible under the wing)
        for ex in [x + 62, x + 76, x + 90]:
            self.canvas.create_rectangle(ex, y + 7, ex + 11, y + 20,
                                         fill='#555', outline='#333')
            self.canvas.create_oval(ex, y + 7, ex + 11, y + 13,
                                    fill='#222', outline='')
        # left vertical tail fin
        self.canvas.create_polygon(x + 132, y - 14, x + 150, y - 44,
                                   x + 157, y - 14,
                                   fill='#ddd', outline='#aaa')
        # right vertical tail fin (twin-tail!)
        self.canvas.create_polygon(x + 142, y - 14, x + 160, y - 44,
                                   x + 167, y - 14,
                                   fill='#ddd', outline='#aaa')
        # horizontal stabilizer
        self.canvas.create_polygon(x + 132, y - 2, x + 162, y - 10,
                                   x + 164, y + 8, x + 132, y + 4,
                                   fill='#ddd', outline='#aaa')
        # Ukrainian flag stripe
        self.canvas.create_rectangle(x + 25, y - 6, x + 128, y,
                                     fill='#0057B7', outline='')
        self.canvas.create_rectangle(x + 25, y, x + 128, y + 6,
                                     fill='#FFD700', outline='')
        # cockpit windows
        for wx in [x + 34, x + 48, x + 62]:
            self.canvas.create_oval(wx, y - 9, wx + 9, y - 2,
                                    fill='#c8eaff', outline='#888')
        # AN-225 label
        self.canvas.create_text(x + 88, y + 3, text='AN-225',
                                font=('Arial', 7, 'bold'), fill='#003366')

    def _draw_bird(self, x, y):
        flap = 8 if self.frame % 18 < 9 else -3
        self.canvas.create_line(x - 13, y, x, y - flap,
                                fill='#333', width=2)
        self.canvas.create_line(x, y - flap, x + 13, y,
                                fill='#333', width=2)
        self.canvas.create_oval(x - 4, y - 14, x + 4, y - 6,
                                fill='#555', outline='')

    def _draw_storm(self, x, y):
        for dx, dy, w, h in [(-20, -14, 40, 28), (-30, -6, 42, 22), (-8, -6, 42, 22)]:
            self.canvas.create_oval(x + dx, y + dy, x + dx + w, y + dy + h,
                                    fill='#5a5a7a', outline='#444')
        # lightning bolt
        self.canvas.create_line(x + 2, y + 8, x - 6, y + 20,
                                x + 2, y + 17, x - 4, y + 30,
                                fill='#FFD700', width=2)

    def _draw_cargo(self, x, y):
        self.canvas.create_rectangle(x - 14, y - 14, x + 14, y + 14,
                                     fill='#8B4513', outline='#5C2D0A', width=2)
        self.canvas.create_line(x - 14, y, x + 14, y, fill='#5C2D0A')
        self.canvas.create_line(x, y - 14, x, y + 14, fill='#5C2D0A')
        self.canvas.create_text(x, y, text='+50',
                                font=('Arial', 8, 'bold'), fill='#FFD700')

    def _draw_cloud(self, x, y, w):
        h = w // 2
        self.canvas.create_oval(x - w // 2, y, x + w // 2, y + h,
                                fill='white', outline='')
        self.canvas.create_oval(x - w // 3, y - h // 2,
                                x + w // 3, y + int(h * 0.7),
                                fill='white', outline='')
        self.canvas.create_oval(x, y - h // 4,
                                x + int(w * 0.65), y + int(h * 0.8),
                                fill='white', outline='')

    # -------------------------------------------------------------- input

    def on_up(self, _event):
        if self.state == 'start':
            self._start()
        elif self.state == 'playing':
            self.vel_y = -7.2

    def on_down(self, _event):
        if self.state == 'playing':
            self.vel_y = 5.0

    def on_restart(self, _event):
        if self.state == 'gameover':
            self._start()


if __name__ == '__main__':
    root = tk.Tk()
    An225Game(root)
    root.mainloop()
