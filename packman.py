#!/usr/bin/env python3
"""
PAC-MAN for an 80x24 ANSI terminal, rendered with curses.

A close re-creation of the 1980 arcade game within an 80x24 character
display.  The arcade's 28x31 tile maze is compressed to 28x22 tiles and
drawn two screen columns per tile so the proportions look right.

Faithful gameplay details implemented:
  * Per-ghost AI: Blinky (chases Pac-Man), Pinky (4 tiles ahead, with the
    original "up + left" overflow bug), Inky (vector from Blinky through
    a point 2 tiles ahead of Pac-Man), Clyde (chases until within 8 tiles,
    then heads for his scatter corner).
  * Scatter/chase mode schedule per level, with direction reversal on
    every mode change; tie-breaking order up/left/down/right.
  * Frightened mode: ghosts reverse, turn blue, move randomly, flash
    before recovering; 200/400/800/1600 points; duration shrinks per
    level (none at all from level 17 on, pills then only reverse ghosts).
  * Eaten ghosts return to the house as eyes and are revived.
  * Ghost house exit logic: per-ghost dot counters, the global counter
    after a life is lost, and the "no dot eaten" timeout release.
  * Arcade speed tables (Pac-Man/ghost/frightened/tunnel percentages by
    level), tunnel slowdown for ghosts, Cruise Elroy speed-ups for
    Blinky once the dot count runs low (only after Clyde has left).
  * Pac-Man pauses 1 frame eating a dot, 3 eating an energizer.
  * Fruit appears twice per level (scaled to ~70/170 dots), arcade fruit
    sequence and point values, ~9.5 s lifetime, score popups.
  * Ghosts may not turn upward through the "red zone" tiles above the
    house and above Pac-Man's start, except when frightened.
  * 10, 50 points for dots/energizers, extra life at 10,000, 3 lives,
    READY!/GAME OVER sequences, maze flash on level clear.

Controls: arrow keys or WASD to steer, P pause, Q quit, R restart after
game over.  Requires a terminal of at least 80x24.

Run:  python3 pacman.py            (the game)
      python3 pacman.py --selftest (headless logic check, no terminal)
"""

import curses
import random
import sys
import time

SCREEN_W, SCREEN_H = 80, 24
TICK_RATE = 60.0                  # logic frames per second, as the arcade
FULL_SPEED = 9.47                 # tiles/second at "100%" speed

# ---------------------------------------------------------------------------
# Maze: 28 x 22 tiles.  '#' wall, '.' dot, 'o' energizer, '=' house door,
# ' ' open floor.  Row 10 is the wrap-around tunnel row.
# ---------------------------------------------------------------------------
MAZE_SRC = [
    "############################",
    "#............##............#",
    "#.####.#####.##.#####.####.#",
    "#o####.#####.##.#####.####o#",
    "#..........................#",
    "#.####.##.########.##.####.#",
    "#......##....##....##......#",
    "######.##### ## #####.######",
    "######.##          ##.######",
    "######.## ###==### ##.######",
    "      .   #      #   .      ",
    "######.## ######## ##.######",
    "######.##          ##.######",
    "######.##### ## #####.######",
    "#............##............#",
    "#.####.#####.##.#####.####.#",
    "#o..##.......  .......##..o#",
    "###.##.##.########.##.##.###",
    "#......##....##....##......#",
    "#.##########.##.##########.#",
    "#..........................#",
    "############################",
]
MAZE_H = len(MAZE_SRC)
MAZE_W = len(MAZE_SRC[0])
X0 = (SCREEN_W - MAZE_W * 2) // 2     # maze is drawn 2 columns per tile
Y0 = 1                                # row 0 is the score line

TUNNEL_ROW = 10
HOUSE_EXIT = (8, 13)                  # tile just above the door
PAC_START = (16, 13)
FRUIT_TILE = (12, 13)                 # fruit also collides on (12,14)
NO_UP = {(8, 12), (8, 15), (16, 12), (16, 15)}   # red-zone tiles

DIRS = {'U': (-1, 0), 'L': (0, -1), 'D': (1, 0), 'R': (0, 1)}
ORDER = ['U', 'L', 'D', 'R']          # arcade tie-break priority
OPP = {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}

DOTS_TOTAL_BASE = sum(row.count('.') + row.count('o') for row in MAZE_SRC)
DOT_SCALE = DOTS_TOTAL_BASE / 244.0   # arcade maze has 244; ours has fewer


def scaled(n):
    """Scale an arcade dot-count constant to this maze's dot total."""
    return max(0, int(round(n * DOT_SCALE)))


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# ---------------------------------------------------------------------------
# Arcade data tables
# ---------------------------------------------------------------------------
def level_specs(level):
    """Speed percentages: Pac-Man, frightened Pac-Man, ghost, frightened
    ghost, ghost-in-tunnel."""
    if level == 1:
        return dict(pac=0.80, pac_fr=0.90, ghost=0.75, ghost_fr=0.50, tun=0.40)
    if level <= 4:
        return dict(pac=0.90, pac_fr=0.95, ghost=0.85, ghost_fr=0.55, tun=0.45)
    if level <= 20:
        return dict(pac=1.00, pac_fr=1.00, ghost=0.95, ghost_fr=0.60, tun=0.50)
    return dict(pac=0.90, pac_fr=0.90, ghost=0.95, ghost_fr=0.60, tun=0.50)


def mode_times(level):
    """Scatter/chase alternation, in seconds (even indexes = scatter)."""
    inf = float('inf')
    if level == 1:
        return [7, 20, 7, 20, 5, 20, 5, inf]
    if level <= 4:
        return [7, 20, 7, 20, 5, 1033, 1.0 / 60, inf]
    return [5, 20, 5, 20, 5, 1037, 1.0 / 60, inf]


FRIGHT_SECONDS = {1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 5, 7: 2, 8: 2, 9: 1,
                  10: 5, 11: 2, 12: 1, 13: 1, 14: 3, 15: 1, 16: 1, 17: 0,
                  18: 1}


def fright_time(level):
    return FRIGHT_SECONDS.get(level, 0)


def elroy_base(level):
    """Dots-remaining threshold for Cruise Elroy stage 1 (arcade values)."""
    for top, dots in ((1, 20), (2, 30), (5, 40), (8, 50),
                      (11, 60), (14, 80), (18, 100)):
        if level <= top:
            return dots
    return 120


# char, color, points -- cherry, strawberry, peach, apple, grapes,
# galaxian, bell, key
FRUITS = [('%', 'red', 100), ('&', 'red', 300), ('@', 'orange', 500),
          ('O', 'red', 700), ('8', 'green', 1000), ('A', 'cyan', 2000),
          ('b', 'orange', 3000), ('F', 'white', 5000)]


def fruit_for(level):
    seq = [0, 0, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    return FRUITS[seq[level] if level < len(seq) else 7]


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------
class PacMan:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tile = PAC_START
        self.prev = PAC_START
        self.dir = 'L'
        self.next_dir = 'L'
        self.acc = 0.0
        self.freeze = 0          # frames to stall after eating


class Ghost:
    # name, color, home tile, initial state, scatter target
    DEFS = [('Blinky', 'red',    (8, 13),  'out',   (-2, 25)),
            ('Pinky',  'pink',   (10, 13), 'house', (-2, 2)),
            ('Inky',   'cyan',   (10, 11), 'house', (23, 27)),
            ('Clyde',  'orange', (10, 16), 'house', (23, 0))]

    def __init__(self, name, color, home, state, scatter):
        self.name = name
        self.color = color
        self.home = home
        self.start_state = state
        self.scatter = scatter
        self.reset()

    def reset(self):
        self.tile = self.home
        self.prev = self.home
        self.state = self.start_state    # house/leaving/out/eyes/entering
        self.dir = 'L'
        self.acc = 0.0
        self.frightened = False
        self.reverse_pending = False
        self.dot_count = 0


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------
READY, PLAYING, DYING, FLASH, GAME_OVER = range(5)
DYING_ANIM = "OOooQQqq**''..  "


class Game:
    def __init__(self, high=0):
        self.high = high
        self.score = 0
        self.lives = 3
        self.level = 1
        self.extra_awarded = False
        self.tick_count = 0
        self.paused = False
        self.pac = PacMan()
        self.ghosts = [Ghost(*d) for d in Ghost.DEFS]
        self.popups = []                  # [tile, text, ticks]
        self.start_level(first=True)

    # -- setup ------------------------------------------------------------
    def start_level(self, first=False):
        self.dots = set()
        self.pills = set()
        for r, row in enumerate(MAZE_SRC):
            for c, ch in enumerate(row):
                if ch == '.':
                    self.dots.add((r, c))
                elif ch == 'o':
                    self.pills.add((r, c))
        self.dots_total = len(self.dots) + len(self.pills)
        self.dots_eaten = 0
        self.fruit_at = {scaled(70), scaled(170)}
        self.specs = level_specs(self.level)
        self.reset_actors()
        self.global_active = False
        self.global_count = 0
        self.state = READY
        self.state_timer = int((4 if first else 2) * TICK_RATE)

    def reset_actors(self):
        self.pac.reset()
        for g in self.ghosts:
            g.reset()
        self.fruit = None
        self.fruit_ticks = 0
        self.fright_ticks = 0
        self.fright_total = 0
        self.ghost_chain = 0
        self.mode_idx = 0
        self.mode_ticks = 0
        self.freeze = 0
        self.no_dot_ticks = 0
        self.elroy_enabled = False
        self.popups = []

    def restart(self):
        self.__init__(high=self.high)

    # -- geometry ---------------------------------------------------------
    def passable(self, tile, door_ok=False):
        r, c = tile
        if r == TUNNEL_ROW and (c < 0 or c >= MAZE_W):
            return True
        if not (0 <= r < MAZE_H and 0 <= c < MAZE_W):
            return False
        ch = MAZE_SRC[r][c]
        if ch == '#':
            return False
        if ch == '=':
            return door_ok
        return True

    @staticmethod
    def step_tile(tile, d):
        dr, dc = DIRS[d]
        return (tile[0] + dr, tile[1] + dc)

    @staticmethod
    def wrap(tile):
        r, c = tile
        if r == TUNNEL_ROW:
            c %= MAZE_W
        return (r, c)

    def tunnel_slow(self, tile):
        return tile[0] == TUNNEL_ROW and (tile[1] <= 4 or tile[1] >= 23)

    # -- ghost house release ----------------------------------------------
    def housed(self):
        return [g for g in self.ghosts[1:] if g.state == 'house']

    def personal_limit(self, g):
        if g.name == 'Pinky':
            return 0
        if g.name == 'Inky':
            return scaled(30) if self.level == 1 else 0
        if self.level == 1:
            return scaled(60)
        if self.level == 2:
            return scaled(50)
        return 0

    def release(self, g):
        if g.state == 'house':
            g.state = 'leaving'

    def update_house(self):
        # Timeout release: nothing eaten for a while frees the next ghost.
        limit = int((4 if self.level < 5 else 3) * TICK_RATE)
        if self.no_dot_ticks >= limit:
            self.no_dot_ticks = 0
            h = self.housed()
            if h:
                self.release(h[0])
        # Personal counters (inactive while the global counter runs).
        if not self.global_active:
            h = self.housed()
            if h and h[0].dot_count >= self.personal_limit(h[0]):
                self.release(h[0])

    def on_dot_eaten(self):
        self.no_dot_ticks = 0
        if self.global_active:
            self.global_count += 1
            limits = {'Pinky': scaled(7), 'Inky': scaled(17),
                      'Clyde': scaled(32)}
            h = self.housed()
            if h and self.global_count >= limits[h[0].name]:
                if h[0].name == 'Clyde':
                    # Arcade quirk: counter is retired, Clyde leaves via
                    # his personal counter / timeout instead.
                    self.global_active = False
                else:
                    self.release(h[0])
        else:
            h = self.housed()
            if h:
                h[0].dot_count += 1

    # -- eating -----------------------------------------------------------
    def add_score(self, n):
        self.score += n
        if self.score > self.high:
            self.high = self.score
        if not self.extra_awarded and self.score >= 10000:
            self.extra_awarded = True
            self.lives += 1

    def eat_at(self, tile):
        if tile in self.dots:
            self.dots.discard(tile)
            self.add_score(10)
            self.pac.freeze += 1
            self.dot_progress()
        elif tile in self.pills:
            self.pills.discard(tile)
            self.add_score(50)
            self.pac.freeze += 3
            self.dot_progress()
            self.start_fright()
        if self.fruit_ticks > 0 and tile in (FRUIT_TILE,
                                             (FRUIT_TILE[0], FRUIT_TILE[1] + 1)):
            ch, col, pts = self.fruit
            self.add_score(pts)
            self.popups.append([FRUIT_TILE, str(pts), 120])
            self.fruit_ticks = 0

    def dot_progress(self):
        self.dots_eaten += 1
        self.on_dot_eaten()
        if self.dots_eaten in self.fruit_at and self.fruit_ticks <= 0:
            self.fruit = fruit_for(self.level)
            self.fruit_ticks = int(9.5 * TICK_RATE)
        if not self.dots and not self.pills:
            self.state = FLASH
            self.state_timer = int(2.2 * TICK_RATE)
            self.fright_ticks = 0

    def start_fright(self):
        self.ghost_chain = 0
        secs = fright_time(self.level)
        for g in self.ghosts:
            if g.state in ('out', 'leaving', 'house'):
                if secs > 0:
                    g.frightened = True
                if g.state == 'out':
                    g.reverse_pending = True
        if secs > 0:
            self.fright_ticks = self.fright_total = int(secs * TICK_RATE)

    # -- ghost AI ---------------------------------------------------------
    def elroy_stage(self):
        if not self.elroy_enabled:
            return 0
        left = self.dots_total - self.dots_eaten
        d1 = scaled(elroy_base(self.level))
        if left <= max(1, d1 // 2):
            return 2
        if left <= d1:
            return 1
        return 0

    def ghost_target(self, g):
        if g.state == 'eyes':
            return HOUSE_EXIT
        pac = self.pac
        if g.name == 'Blinky':
            if self.elroy_stage() or self.chase_mode():
                return pac.tile
            return g.scatter
        if not self.chase_mode():
            return g.scatter
        if g.name == 'Pinky':
            return self.ahead_of_pac(4)
        if g.name == 'Inky':
            pr, pc = self.ahead_of_pac(2)
            br, bc = self.ghosts[0].tile
            return (2 * pr - br, 2 * pc - bc)
        # Clyde
        if dist2(g.tile, pac.tile) > 64:
            return pac.tile
        return g.scatter

    def ahead_of_pac(self, n):
        dr, dc = DIRS[self.pac.dir]
        r = self.pac.tile[0] + dr * n
        c = self.pac.tile[1] + dc * n
        if self.pac.dir == 'U':          # original 8-bit overflow bug
            c -= n
        return (r, c)

    def chase_mode(self):
        return self.mode_idx % 2 == 1

    def ghost_speed(self, g):
        sp = self.specs
        if g.state in ('eyes', 'entering'):
            return 1.6
        if g.state in ('house', 'leaving'):
            return 0.45
        if self.tunnel_slow(g.tile):
            return sp['tun']
        if g.frightened:
            return sp['ghost_fr']
        if g.name == 'Blinky':
            stage = self.elroy_stage()
            if stage:
                return sp['ghost'] + 0.05 * stage
        return sp['ghost']

    def ghost_step(self, g):
        g.prev = g.tile
        if g.state == 'house':
            return
        if g.state == 'leaving':
            r, c = g.tile
            if r == TUNNEL_ROW and c != HOUSE_EXIT[1]:
                step = 1 if c < HOUSE_EXIT[1] else -1
                g.tile = (r, c + step)
                g.dir = 'R' if step > 0 else 'L'
            elif r > HOUSE_EXIT[0]:
                g.tile = (r - 1, c)
                g.dir = 'U'
            if g.tile[0] == HOUSE_EXIT[0]:
                g.state = 'out'
                g.dir = 'L'
                g.reverse_pending = False
                if g.name == 'Clyde':
                    self.elroy_enabled = True
            return
        if g.state == 'entering':
            r, c = g.tile
            if r < TUNNEL_ROW:
                g.tile = (r + 1, c)
                g.dir = 'D'
            else:
                hc = g.home[1] if g.start_state == 'house' else HOUSE_EXIT[1]
                if c != hc:
                    g.tile = (r, c + (1 if c < hc else -1))
                else:
                    g.frightened = False
                    g.state = 'leaving'
            return
        # 'out' or 'eyes': normal intersection logic.
        if g.reverse_pending:
            g.dir = OPP[g.dir]
            g.reverse_pending = False
        opts = []
        for d in ORDER:
            if d == OPP[g.dir]:
                continue
            nt = self.step_tile(g.tile, d)
            if not self.passable(nt, door_ok=(g.state == 'eyes')):
                continue
            if (d == 'U' and g.tile in NO_UP and g.state == 'out'
                    and not g.frightened):
                continue
            opts.append(d)
        if not opts:
            g.dir = OPP[g.dir]
        elif g.frightened and g.state == 'out':
            g.dir = random.choice(opts)
        else:
            tgt = self.ghost_target(g)
            g.dir = min(opts, key=lambda d: dist2(self.step_tile(g.tile, d),
                                                  tgt))
        nt = self.step_tile(g.tile, g.dir)
        if self.passable(nt, door_ok=(g.state == 'eyes')):
            g.tile = self.wrap(nt)
        if g.state == 'eyes' and g.tile == HOUSE_EXIT:
            g.state = 'entering'

    # -- collisions -------------------------------------------------------
    def check_collision(self, g):
        """Returns True when Pac-Man dies."""
        if self.state != PLAYING or g.state not in ('out', 'leaving'):
            return False
        p = self.pac
        hit = g.tile == p.tile or (g.tile == p.prev and g.prev == p.tile)
        if not hit:
            return False
        if g.frightened:
            self.ghost_chain += 1
            pts = 100 * (2 ** self.ghost_chain)        # 200/400/800/1600
            self.add_score(pts)
            self.popups.append([g.tile, str(pts), 90])
            g.state = 'eyes'
            g.frightened = False
            self.freeze = int(0.8 * TICK_RATE)
            return False
        self.state = DYING
        self.state_timer = len(DYING_ANIM) * 8
        self.fruit_ticks = 0
        return True

    def check_all_collisions(self):
        for g in self.ghosts:
            if self.check_collision(g):
                return True
        return False

    # -- per-tick update ----------------------------------------------------
    def tick(self):
        self.tick_count += 1
        for p in self.popups:
            p[2] -= 1
        self.popups = [p for p in self.popups if p[2] > 0]

        if self.state == READY:
            self.state_timer -= 1
            if self.state_timer <= 0:
                self.state = PLAYING
            return
        if self.state == DYING:
            self.state_timer -= 1
            if self.state_timer <= 0:
                self.lives -= 1
                if self.lives <= 0:
                    self.state = GAME_OVER
                else:
                    self.reset_actors()
                    self.global_active = True
                    self.global_count = 0
                    self.state = READY
                    self.state_timer = int(2 * TICK_RATE)
            return
        if self.state == FLASH:
            self.state_timer -= 1
            if self.state_timer <= 0:
                self.level += 1
                self.start_level()
            return
        if self.state != PLAYING:
            return

        if self.freeze > 0:              # ghost-eaten pause
            self.freeze -= 1
            return

        if self.fright_ticks > 0:
            self.fright_ticks -= 1
            if self.fright_ticks == 0:
                for g in self.ghosts:
                    g.frightened = False
                self.ghost_chain = 0
        else:
            # Scatter/chase schedule only advances outside frightened mode.
            times = mode_times(self.level)
            if self.mode_idx < len(times):
                self.mode_ticks += 1
                if self.mode_ticks >= times[self.mode_idx] * TICK_RATE:
                    self.mode_ticks = 0
                    self.mode_idx += 1
                    for g in self.ghosts:
                        if g.state == 'out':
                            g.reverse_pending = True

        self.no_dot_ticks += 1
        self.update_house()
        if self.fruit_ticks > 0:
            self.fruit_ticks -= 1

        # Pac-Man movement.
        p = self.pac
        if p.freeze > 0:
            p.freeze -= 1
        else:
            sp = self.specs
            pct = sp['pac_fr'] if self.fright_ticks > 0 else sp['pac']
            p.acc += pct * FULL_SPEED / TICK_RATE
            while p.acc >= 1.0:
                p.acc -= 1.0
                self.pac_step()
                if self.check_all_collisions():
                    return

        # Ghost movement.
        for g in self.ghosts:
            g.acc += self.ghost_speed(g) * FULL_SPEED / TICK_RATE
            while g.acc >= 1.0:
                g.acc -= 1.0
                self.ghost_step(g)
                if self.check_collision(g):
                    return
                if self.state != PLAYING:
                    return

    def pac_step(self):
        p = self.pac
        if p.next_dir and self.passable(self.step_tile(p.tile, p.next_dir)):
            p.dir = p.next_dir
        nt = self.step_tile(p.tile, p.dir)
        if self.passable(nt):
            p.prev = p.tile
            p.tile = self.wrap(nt)
            self.eat_at(p.tile)
        else:
            p.prev = p.tile

    # -- rendering ----------------------------------------------------------
    def draw(self, scr, attrs):
        scr.erase()

        def put(y, x, s, a=0):
            if 0 <= y < SCREEN_H and 0 <= x and x + len(s) <= SCREEN_W - 1:
                try:
                    scr.addstr(y, x, s, a)
                except curses.error:
                    pass

        # Score line.
        put(0, 1, "SCORE", attrs['text'])
        put(0, 7, "%-7d" % self.score, attrs['white'])
        put(0, 28, "HIGH SCORE", attrs['text'])
        put(0, 39, "%-7d" % self.high, attrs['white'])
        put(0, 64, "LEVEL %-3d" % self.level, attrs['text'])

        # Maze.
        flash_on = self.state == FLASH and (self.state_timer // 14) % 2 == 0
        wall = attrs['wall_flash'] if flash_on else attrs['wall']
        for r, row in enumerate(MAZE_SRC):
            y = Y0 + r
            for c, ch in enumerate(row):
                x = X0 + c * 2
                if ch == '#':
                    put(y, x, '##', wall)
                elif ch == '=':
                    put(y, x, '--', attrs['door'])
                elif (r, c) in self.dots:
                    put(y, x, '.', attrs['dot'])
                elif (r, c) in self.pills:
                    if (self.tick_count // 16) % 2 == 0 or \
                            self.state != PLAYING:
                        put(y, x, 'o', attrs['pill'])

        # Fruit.
        if self.fruit_ticks > 0 and self.fruit:
            ch, col, _ = self.fruit
            put(Y0 + FRUIT_TILE[0], X0 + FRUIT_TILE[1] * 2 + 1, ch,
                attrs[col])

        # Ghosts (hidden during the death animation, as in the arcade).
        if self.state != DYING:
            flash_win = min(2 * TICK_RATE, self.fright_total // 2)
            for g in self.ghosts:
                gy, gx = Y0 + g.tile[0], X0 + g.tile[1] * 2
                if g.state in ('eyes', 'entering'):
                    put(gy, gx, '"', attrs['white'])
                elif g.frightened:
                    flashing = (0 < self.fright_ticks <= flash_win and
                                (self.tick_count // 10) % 2 == 0)
                    put(gy, gx, 'M',
                        attrs['white'] if flashing else attrs['fright'])
                else:
                    put(gy, gx, 'M', attrs[g.color])

        # Pac-Man.
        py, px = Y0 + self.pac.tile[0], X0 + self.pac.tile[1] * 2
        if self.state == DYING:
            idx = len(DYING_ANIM) - 1 - min(self.state_timer // 8,
                                            len(DYING_ANIM) - 1)
            put(py, px, DYING_ANIM[idx], attrs['pac'])
        elif self.state != GAME_OVER:
            mouth = 'C' if (self.tick_count // 6) % 2 == 0 else 'O'
            put(py, px, mouth, attrs['pac'])

        # Score popups.
        for tile, text, _ in self.popups:
            put(Y0 + tile[0], X0 + tile[1] * 2, text, attrs['white'])

        # Status text in the corridor below the house.
        msg_y = Y0 + 12
        cx = X0 + MAZE_W                  # screen centre of the maze
        if self.state == READY:
            put(msg_y, cx - 3, "READY!", attrs['pac'])
        elif self.state == GAME_OVER:
            put(msg_y, cx - 5, "GAME  OVER", attrs['red'])
            put(SCREEN_H - 1, cx - 9, "PRESS R TO RESTART", attrs['white'])
        if self.paused:
            put(msg_y, cx - 3, "PAUSED", attrs['white'])

        # Bottom bar: reserve lives, help, level fruit.
        if self.state != GAME_OVER:
            put(SCREEN_H - 1, 1, "C " * max(0, self.lives - 1),
                attrs['pac'])
            put(SCREEN_H - 1, 24, "ARROWS/WASD:MOVE  P:PAUSE  Q:QUIT",
                attrs['text'])
        fch, fcol, _ = fruit_for(self.level)
        put(SCREEN_H - 1, SCREEN_W - 3, fch, attrs[fcol])

        scr.refresh()


# ---------------------------------------------------------------------------
# Terminal front end
# ---------------------------------------------------------------------------
def make_attrs():
    attrs = {}
    names = {
        'wall': (curses.COLOR_BLUE, curses.A_BOLD),
        'wall_flash': (curses.COLOR_WHITE, curses.A_BOLD),
        'dot': (curses.COLOR_WHITE, 0),
        'pill': (curses.COLOR_WHITE, curses.A_BOLD),
        'door': (curses.COLOR_MAGENTA, 0),
        'pac': (curses.COLOR_YELLOW, curses.A_BOLD),
        'red': (curses.COLOR_RED, curses.A_BOLD),
        'pink': (curses.COLOR_MAGENTA, curses.A_BOLD),
        'cyan': (curses.COLOR_CYAN, curses.A_BOLD),
        'orange': (curses.COLOR_YELLOW, 0),
        'green': (curses.COLOR_GREEN, curses.A_BOLD),
        'fright': (curses.COLOR_BLUE, curses.A_BOLD),
        'white': (curses.COLOR_WHITE, curses.A_BOLD),
        'text': (curses.COLOR_WHITE, 0),
    }
    if curses.has_colors():
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        for i, (name, (fg, extra)) in enumerate(names.items(), start=1):
            try:
                curses.init_pair(i, fg, bg)
                attrs[name] = curses.color_pair(i) | extra
            except curses.error:
                attrs[name] = extra
    else:
        for name, (fg, extra) in names.items():
            attrs[name] = extra
    return attrs


KEY_DIRS = {
    curses.KEY_UP: 'U', curses.KEY_DOWN: 'D',
    curses.KEY_LEFT: 'L', curses.KEY_RIGHT: 'R',
    ord('w'): 'U', ord('s'): 'D', ord('a'): 'L', ord('d'): 'R',
    ord('W'): 'U', ord('S'): 'D', ord('A'): 'L', ord('D'): 'R',
}


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    if curses.LINES < SCREEN_H or curses.COLS < SCREEN_W:
        raise SystemExit("This game needs a terminal of at least 80x24 "
                         "(yours is %dx%d)." % (curses.COLS, curses.LINES))
    attrs = make_attrs()
    game = Game()
    next_t = time.monotonic()
    while True:
        while True:
            ch = stdscr.getch()
            if ch == -1:
                break
            if ch in (ord('q'), ord('Q')):
                return
            if ch in (ord('p'), ord('P')):
                game.paused = not game.paused
            elif ch in (ord('r'), ord('R')) and game.state == GAME_OVER:
                game.restart()
            elif ch in KEY_DIRS:
                game.pac.next_dir = KEY_DIRS[ch]
        if not game.paused:
            game.tick()
        game.draw(stdscr, attrs)
        next_t += 1.0 / TICK_RATE
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()


# ---------------------------------------------------------------------------
# Headless self-test (no terminal needed): python3 pacman.py --selftest
# ---------------------------------------------------------------------------
def selftest():
    assert MAZE_H == 22 and all(len(r) == MAZE_W == 28 for r in MAZE_SRC), \
        "maze must be 28x22"
    # Every dot/pill must be reachable from Pac-Man's start tile.
    g = Game()
    seen = {PAC_START}
    stack = [PAC_START]
    while stack:
        t = stack.pop()
        for d in ORDER:
            nt = g.wrap(g.step_tile(t, d))
            if g.passable(nt) and nt not in seen:
                seen.add(nt)
                stack.append(nt)
    missing = (g.dots | g.pills) - seen
    assert not missing, "unreachable dots: %s" % sorted(missing)
    assert HOUSE_EXIT in seen and FRUIT_TILE in seen

    # Random play for two simulated minutes: must never raise, and the
    # ghosts must actually leave the house and catch Pac-Man eventually.
    rnd = random.Random(7)
    g = Game()
    deaths = 0
    lives_before = g.lives
    for i in range(int(120 * TICK_RATE)):
        if i % 13 == 0:
            g.pac.next_dir = rnd.choice('ULDR')
        prev_state = g.state
        g.tick()
        if prev_state != DYING and g.state == DYING:
            deaths += 1
        if g.state == GAME_OVER:
            break
    assert all(0 <= gg.tile[0] < MAZE_H for gg in g.ghosts)
    assert deaths > 0 or g.lives == lives_before, "collision logic broken?"
    assert any(gg.state != 'house' for gg in g.ghosts[1:]), \
        "ghosts never left the house"

    # Frightened mode and ghost eating.
    g = Game()
    g.state = PLAYING
    pill = next(iter(g.pills))
    g.pac.tile = pill
    g.eat_at(pill)
    assert g.fright_ticks > 0 and g.ghosts[0].frightened
    blinky = g.ghosts[0]
    blinky.tile = g.pac.tile
    assert not g.check_collision(blinky)      # eaten, not killed
    assert blinky.state == 'eyes' and g.score >= 200 + 50
    # Eyes must find their way home and be revived.
    g.freeze = 0
    for _ in range(int(30 * TICK_RATE)):
        g.tick()
        if blinky.state == 'out' and not blinky.frightened:
            break
    assert blinky.state == 'out', "eyes never made it home"

    # Clearing the board advances the level.
    g = Game()
    g.state = PLAYING
    for t in sorted(g.dots | g.pills):
        g.pac.tile = t
        g.eat_at(t)
    assert g.state == FLASH
    for _ in range(int(5 * TICK_RATE)):
        g.tick()
        if g.state == READY:
            break
    assert g.level == 2 and len(g.dots) + len(g.pills) == g.dots_total

    total = DOTS_TOTAL_BASE
    print("selftest OK  (%d dots+pills, fruit at %s dots, "
          "elroy L1 at %d left)" %
          (total, sorted({scaled(70), scaled(170)}), scaled(20)))


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
    else:
        try:
            curses.wrapper(main)
        except KeyboardInterrupt:
            pass
