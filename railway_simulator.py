"""
================================================================================
  MULTI-CORE RAILWAY RESERVATION SIMULATOR  —  v6.0  (Bogie Architecture Edition)
  Academic Demonstration: Parallel Organization, Mutex Synchronization & Amdahl's Law

  Phase 1 : 1 Core   · Serial Baseline      (mutex protected, timed → T₁)
  Phase 2 : 2 Cores  · Parallel Mutex       (mutex protected, timed → T₂)
  Phase 3 : 4 Cores  · Parallel Mutex       (mutex protected, timed → T₄)
  Phase 4 : 4 Cores  · CHAOS — No Lock      (race conditions, untimed)
  Phase 5 : Telemetry Dashboard             (Amdahl's Law line graph, pure pygame)

  v6.0 FEATURES:
    • Indian Railways LHB Coach Bogie UI — teal/silver palette, 10 bays.
    • Motion parallax windows simulating train movement.
    • Hardware Thread Activity sidebar (Core Monitor Hub).
    • Visual Mutex Gate with live waiting queue.
    • Needle speedometer gauge for real-time throughput.
    • Collision glitch effect: rapid flicker → solid red with "CORRUPT".
    • Confirmation modal for human bookings (workers run LIVE behind it).
    • Presenter controls:
        [SPACE]  Manual phase transitions (no auto-advance).
        [P]      Global Pause / Resume  (OS-level worker freeze via Event).
        [R]      Reset & restart current phase.
        [S]      Slow-motion toggle   (5× worker latency via shared Value).

  Architecture (UNCHANGED CORE):
    Main Process (Core 0)  → Pygame UI + state orchestrator + click handler.
    Worker Processes 1–4   → multiprocessing.Process booking agents.
    Shared Memory          → multiprocessing.Array('i', 50)  [seats]
                           → multiprocessing.Array('i', 4)   [core states]
    IPC Channels           → multiprocessing.Queue × 2  (log + events)
    Synchronization        → multiprocessing.Lock   (Phase 1/2/3)
                           → multiprocessing.Event  (pause gate)
                           → multiprocessing.Value  (speed multiplier)
================================================================================
"""

import multiprocessing
import time
import random
import sys
import math
from collections import deque

# ── Windows multiprocessing guard ──────────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.freeze_support()

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_SEATS    = 50
TOTAL_BOOKINGS = 32

PARALLEL_WORK_MIN = 0.10       # parallel fraction (outside lock)
PARALLEL_WORK_MAX = 0.18
DB_WRITE_LATENCY  = 0.007      # serial fraction (inside lock)

RACE_WIN_MIN = 0.06            # chaos-mode sleep
RACE_WIN_MAX = 0.12

HUMAN_ID        = 99
TOAST_DURATION  = 2.0

SEAT_AVAILABLE =  0
SEAT_COLLISION = -1

# ── Core state codes (written by workers to core_states shared array) ─────
CORE_IDLE    = 0
CORE_WAITING = 1   # blocked on lock.acquire()
CORE_WORKING = 2   # inside critical section

# ── Animation timing ──────────────────────────────────────────────────────
GLITCH_DURATION     = 0.35
PACKET_DURATION     = 0.30
THROUGHPUT_INTERVAL = 0.5

SLOW_MO_FACTOR = 5.0   # multiplier when Slow-Mo is active

# ── Phase definitions ─────────────────────────────────────────────────────
PHASE_DEFS = [
    {"num": 1, "cores": 1, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS,
     "label": "Phase 1  ·  1 Core — Serial Baseline",
     "badge_color": (16, 100, 120), "overlay_color": (16, 100, 120),
     "detail": "Single worker. Sequential. Establishes T₁."},
    {"num": 2, "cores": 2, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS // 2,
     "label": "Phase 2  ·  2 Cores — Parallel Mutex",
     "badge_color": (20, 140, 80), "overlay_color": (20, 140, 80),
     "detail": "2 workers. Mutex serialises critical section."},
    {"num": 3, "cores": 4, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS // 4,
     "label": "Phase 3  ·  4 Cores — Parallel Mutex",
     "badge_color": (88, 55, 200), "overlay_color": (88, 55, 200),
     "detail": "4 workers. Max parallelism w/ Mutex. T₄ recorded."},
    {"num": 4, "cores": 4, "locked": False, "timed": False,
     "bookings_each": TOTAL_BOOKINGS // 4,
     "label": "Phase 4  ·  4 Cores — CHAOS (No Lock)",
     "badge_color": (195, 40, 40), "overlay_color": (195, 40, 40),
     "detail": "No mutex. Race conditions. RED = corruption."},
]

# ═════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE  — Indian Railways LHB Coach (Teal / Silver)
# ═════════════════════════════════════════════════════════════════════════════

COLOR_BG           = (232, 240, 245)      # light blue-gray
COLOR_PANEL_BG     = (255, 255, 255)
COLOR_HEADER_BG    = (10,  42,  58)       # dark teal-navy
COLOR_TEXT_DARK     = (12,  20,  30)
COLOR_TEXT_LIGHT    = (225, 242, 255)
COLOR_TEXT_MID      = (65,  85, 108)
COLOR_ACCENT        = (0,  142, 130)      # teal accent
COLOR_DIVIDER       = (175, 195, 212)

COLOR_COACH_BODY    = (216, 226, 235)     # silver-gray LHB body
COLOR_COACH_STRIPE  = (0,  118, 128)      # teal stripe
COLOR_PARTITION     = (130, 145, 165)     # bay partition lines
COLOR_AISLE_BG      = (200, 210, 222)

COLOR_COLLISION     = (200, 36,  36)
COLOR_COLLISION_TXT = (255, 255, 255)

CORE_COLORS = {
    1:        ( 30,  95, 210),      # Royal Blue
    2:        ( 18, 155,  70),      # Emerald Green
    3:        (110,  50, 220),      # Violet
    4:        (215,  78,  10),      # Burnt Orange
    HUMAN_ID: (195, 155,   0),      # Solid Gold
}

COLOR_CORE_IDLE    = (150, 158, 175)
COLOR_CORE_WAITING = (220, 180,  18)
COLOR_CORE_WORKING = ( 28, 178,  68)

COLOR_GRAPH_BG    = (250, 252, 255)
COLOR_GRID_LINE   = (200, 210, 228)
COLOR_AXIS        = (38,  45,  65)
COLOR_IDEAL_LINE  = (30,  95, 210)
COLOR_ACTUAL_LINE = (18, 155,  70)

COLOR_SKY     = (135, 195, 230)
COLOR_GROUND  = ( 85, 155,  78)
COLOR_POLE    = ( 75,  58,  42)

# ═════════════════════════════════════════════════════════════════════════════
#  LAYOUT GEOMETRY  — 1280 × 720  (16:9 projector-safe)
# ═════════════════════════════════════════════════════════════════════════════

SIDEBAR_W = 218
WIN_W     = 1280
WIN_H     = 720
MAIN_W    = WIN_W - SIDEBAR_W     # 1062
FPS       = 30

HEADER_H     = 96
LEGEND_H     = 32
CTRL_BAR_H   = 28
GRID_MARGIN  = 18     # coach wall thickness (houses windows)
AISLE_W      = 32
ROW_LABEL_W  = 26
SEAT_COLS    = 10
SEAT_ROWS    = 5
CELL_H       = 66
SEAT_PAD     = 4

_AVAIL_W = MAIN_W - 2 * GRID_MARGIN - AISLE_W - ROW_LABEL_W
CELL_W   = _AVAIL_W // SEAT_COLS

_LEFT_X  = GRID_MARGIN + ROW_LABEL_W
_RIGHT_X = _LEFT_X + 5 * CELL_W + AISLE_W

GRID_TOP    = HEADER_H + LEGEND_H + 2
GRID_H      = SEAT_ROWS * CELL_H
STATS_TOP   = GRID_TOP + GRID_H + 2
STATS_BAR_H = 28
LOG_TOP     = STATS_TOP + STATS_BAR_H
LOG_H       = WIN_H - LOG_TOP - CTRL_BAR_H
CTRL_BAR_Y  = WIN_H - CTRL_BAR_H

# Modal
MODAL_W, MODAL_H = 390, 240
MODAL_X = (MAIN_W - MODAL_W) // 2
MODAL_Y = (WIN_H  - MODAL_H) // 2
_YES  = (MODAL_X + 36,  MODAL_Y + MODAL_H - 66, 136, 42)
_CANC = (MODAL_X + MODAL_W - 172, MODAL_Y + MODAL_H - 66, 136, 42)


# ═════════════════════════════════════════════════════════════════════════════
#  COORDINATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _in_rect(px, py, r):
    return r[0] <= px < r[0]+r[2] and r[1] <= py < r[1]+r[3]

def seat_xy(idx):
    row = idx // SEAT_COLS; col = idx % SEAT_COLS
    x = (_LEFT_X + col*CELL_W) if col < 5 else (_RIGHT_X + (col-5)*CELL_W)
    return x, GRID_TOP + row * CELL_H

def seat_center(idx):
    sx, sy = seat_xy(idx)
    return sx + CELL_W//2, sy + CELL_H//2

def mouse_to_seat(mx, my):
    if my < GRID_TOP or my >= GRID_TOP + GRID_H: return -1
    row = (my - GRID_TOP) // CELL_H
    if row < 0 or row >= SEAT_ROWS: return -1
    if _LEFT_X <= mx < _LEFT_X + 5*CELL_W:
        col = (mx - _LEFT_X) // CELL_W
    elif _RIGHT_X <= mx < _RIGHT_X + 5*CELL_W:
        col = 5 + (mx - _RIGHT_X) // CELL_W
    else:
        return -1
    sx, sy = seat_xy(row*SEAT_COLS + col)
    if mx < sx+SEAT_PAD or mx > sx+CELL_W-SEAT_PAD: return -1
    if my < sy+SEAT_PAD or my > sy+CELL_H-SEAT_PAD: return -1
    idx = row*SEAT_COLS + col
    return idx if 0 <= idx < TOTAL_SEATS else -1


# ═════════════════════════════════════════════════════════════════════════════
#  WORKER PROCESS  — runs on a separate OS core
# ═════════════════════════════════════════════════════════════════════════════

def booking_agent(core_id, shared_seats, core_states, lock, bookings,
                  log_queue, event_queue, run_flag, speed_mult):
    """
    Each worker:
      1. run_flag.wait() — blocks if presenter paused the sim.
      2. Updates core_states[core_id-1] before/after lock.
      3. Multiplies sleep durations by speed_mult.value for slow-mo.
    NEVER imports pygame.
    """
    secured = collisions = 0

    for _ in range(bookings):
        # ── Pause gate — blocks at OS level until run_flag is set ─────
        run_flag.wait()

        sm = max(speed_mult.value, 0.1)   # read speed multiplier once

        if lock is not None:
            core_states[core_id - 1] = CORE_WAITING
            lock.acquire()
        core_states[core_id - 1] = CORE_WORKING

        try:
            target = -1
            for idx in range(TOTAL_SEATS):
                if shared_seats[idx] == SEAT_AVAILABLE:
                    target = idx; break
            if target == -1:
                log_queue.put(f"[Core {core_id}] No seats left — exiting.")
                break

            read_val = shared_seats[target]

            if lock is None:
                time.sleep(random.uniform(RACE_WIN_MIN, RACE_WIN_MAX) * sm)
            else:
                time.sleep(DB_WRITE_LATENCY * sm)

            if read_val == SEAT_AVAILABLE:
                if shared_seats[target] == SEAT_AVAILABLE:
                    shared_seats[target] = core_id
                    secured += 1
                    log_queue.put(f"[Core {core_id}] ✓ Booked S-{target+1:02d}")
                    event_queue.put(('BOOK', core_id, target, time.time()))
                else:
                    shared_seats[target] = SEAT_COLLISION
                    collisions += 1
                    log_queue.put(f"[Core {core_id}] ✗ COLLISION S-{target+1:02d}")
                    event_queue.put(('COLLISION', core_id, target, time.time()))
        finally:
            if lock is not None:
                lock.release()

        core_states[core_id - 1] = CORE_IDLE

        run_flag.wait()   # check pause again between bookings
        if lock is not None:
            time.sleep(random.uniform(PARALLEL_WORK_MIN, PARALLEL_WORK_MAX) * sm)
        else:
            time.sleep(random.uniform(0.02, 0.06) * sm)

    core_states[core_id - 1] = CORE_IDLE
    log_queue.put(f"[Core {core_id}] Done. OK:{secured} Coll:{collisions}")


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def launch_phase(pdef, seats, cstates, lq, eq, run_flag, speed_mult):
    lock = multiprocessing.Lock() if pdef["locked"] else None
    for i in range(4): cstates[i] = CORE_IDLE
    procs = []
    t0 = time.perf_counter()
    for cid in range(1, pdef["cores"] + 1):
        p = multiprocessing.Process(
            target=booking_agent,
            args=(cid, seats, cstates, lock, pdef["bookings_each"],
                  lq, eq, run_flag, speed_mult),
            daemon=True, name=f"Core{cid}")
        p.start(); procs.append(p)
    return procs, lock, t0

def reset_seats(s):
    for i in range(TOTAL_SEATS): s[i] = SEAT_AVAILABLE

def all_done(p):
    return all(not x.is_alive() for x in p)

def kill_all(p):
    for x in p:
        if x.is_alive(): x.terminate(); x.join(timeout=1)

def drain_queue(q):
    while not q.empty():
        try:    q.get_nowait()
        except: break


# ═════════════════════════════════════════════════════════════════════════════
#  HUMAN CLICK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

def handle_human_book(seat_idx, shared_seats, lock, phase_locked):
    if seat_idx < 0 or seat_idx >= TOTAL_SEATS:
        return False, "Invalid."
    if phase_locked and lock is not None:
        lock.acquire()
        try:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ S-{seat_idx+1:02d} confirmed!"
            else:
                return False, f"✗ S-{seat_idx+1:02d} taken!"
        finally:
            lock.release()
    else:
        rv = shared_seats[seat_idx]
        time.sleep(0.008)
        if rv == SEAT_AVAILABLE:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ S-{seat_idx+1:02d} booked (chaos!)."
            else:
                shared_seats[seat_idx] = SEAT_COLLISION
                return False, f"✗ COLLISION on S-{seat_idx+1:02d}!"
        return False, f"✗ S-{seat_idx+1:02d} unavailable."


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — Header, Legend, Control Bar
# ═════════════════════════════════════════════════════════════════════════════

def draw_header(surface, fonts, pdef, status, phase_idx):
    import pygame
    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, HEADER_H))

    surface.blit(fonts["title"].render(
        "INDIAN RAILWAYS  —  LHB Coach Reservation Portal", True, COLOR_TEXT_LIGHT),
        (18, 5))
    surface.blit(fonts["small"].render(
        "Train: 12951 Mumbai Rajdhani  ·  Coach: A1 (AC Chair Car)  ·  LHB Bogie",
        True, (120, 165, 205)), (18, 30))
    surface.blit(fonts["tiny"].render(
        "Dep: 06:00  ·  Platform: 3  ·  PNR: 284-7921083  ·  50 Berths  ·  10 Bays",
        True, (100, 140, 185)), (18, 48))

    # Progress pips
    px = 18
    for i, d in enumerate(PHASE_DEFS):
        done = i < phase_idx; cur = i == phase_idx
        c = (55, 200, 100) if done else (230, 120, 35) if cur else (45, 60, 82)
        pygame.draw.circle(surface, c, (px+7, 70), 7)
        pygame.draw.circle(surface, (200, 220, 245), (px+7, 70), 7, 2)
        surface.blit(fonts["tiny"].render(f"P{d['num']}", True, COLOR_TEXT_LIGHT),
                     (px, 80))
        px += 46

    # Phase badge
    bs = fonts["badge"].render(pdef["label"], True, COLOR_TEXT_LIGHT)
    br = bs.get_rect(right=MAIN_W - 14, centery=16)
    pygame.draw.rect(surface, pdef["badge_color"], br.inflate(16, 8), border_radius=5)
    surface.blit(bs, br)

    surface.blit(fonts["tiny"].render(pdef["detail"], True, (130, 160, 200)),
                 (18 + 46*4 + 10, 68))
    ss = fonts["status"].render(status, True, (160, 185, 220))
    surface.blit(ss, (MAIN_W - ss.get_width() - 14, 48))
    surface.blit(fonts["tiny"].render(
        "Click any OPEN seat to book", True, (110, 148, 200)), (18, HEADER_H - 14))
    pygame.draw.line(surface, COLOR_COACH_STRIPE, (0, HEADER_H-2), (WIN_W, HEADER_H-2), 2)


def draw_legend(surface, fonts, top_y, n_cores):
    import pygame
    pygame.draw.rect(surface, COLOR_PANEL_BG, (0, top_y, WIN_W, LEGEND_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)
    x = 14
    surface.blit(fonts["tiny"].render("Legend:", True, COLOR_TEXT_MID), (x, top_y+10))
    x += 52
    for cid in range(1, n_cores+1):
        pygame.draw.rect(surface, CORE_COLORS[cid], (x, top_y+10, 10, 10), border_radius=2)
        surface.blit(fonts["tiny"].render(f"C{cid}", True, COLOR_TEXT_DARK), (x+13, top_y+9))
        x += 44
    pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], (x, top_y+10, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("You", True, COLOR_TEXT_DARK), (x+13, top_y+9))
    x += 38
    pygame.draw.rect(surface, COLOR_COLLISION, (x, top_y+10, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Collision", True, COLOR_TEXT_DARK), (x+13, top_y+9))
    x += 68
    pygame.draw.rect(surface, (210,218,230), (x, top_y+10, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Open", True, COLOR_TEXT_DARK), (x+13, top_y+9))


def draw_control_bar(surface, fonts, is_paused, is_slow):
    """Bottom control bar with keyboard shortcuts."""
    import pygame
    pygame.draw.rect(surface, (16, 35, 50), (0, CTRL_BAR_Y, WIN_W, CTRL_BAR_H))
    pygame.draw.line(surface, COLOR_COACH_STRIPE, (0, CTRL_BAR_Y), (WIN_W, CTRL_BAR_Y), 1)
    hints = "[SPACE] Next Phase   |   [P] Pause   |   [R] Reset Phase   |   [S] Slow-Mo"
    surface.blit(fonts["tiny"].render(hints, True, (140, 170, 200)), (18, CTRL_BAR_Y + 8))
    mode = "PAUSED" if is_paused else ("SLOW-MO ×5" if is_slow else "NORMAL")
    mc = (220, 80, 80) if is_paused else ((220, 180, 20) if is_slow else (55, 200, 100))
    ms = fonts["badge"].render(f"Mode: {mode}", True, mc)
    surface.blit(ms, (WIN_W - ms.get_width() - 18, CTRL_BAR_Y + 6))


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — Bogie Train Car (bays, partitions, parallax windows)
# ═════════════════════════════════════════════════════════════════════════════

def draw_parallax_window(surface, rect, scroll_offset):
    """Draw a tiny window with scrolling scenery (sky + ground + poles)."""
    import pygame
    old_clip = surface.get_clip()
    surface.set_clip(rect)
    pygame.draw.rect(surface, COLOR_SKY, rect)
    # Ground (bottom 40%)
    gh = int(rect.height * 0.40)
    pygame.draw.rect(surface, COLOR_GROUND,
                     (rect.x, rect.y + rect.height - gh, rect.width, gh))
    # Scrolling telegraph poles
    spacing = 28
    for i in range(-1, rect.width // spacing + 3):
        px = rect.x + i * spacing - int(scroll_offset) % spacing
        py_top = rect.y + rect.height - gh - 3
        py_bot = rect.y + rect.height - 1
        pygame.draw.line(surface, COLOR_POLE, (px, py_top), (px, py_bot), 1)
    surface.set_clip(old_clip)
    pygame.draw.rect(surface, (80, 95, 115), rect, 1, border_radius=2)


def draw_train_car(surface, fonts, shared_seats, hover_seat,
                   glitch_seats, now, scroll_offset):
    import pygame

    # ── Coach body ────────────────────────────────────────────────────────
    coach = pygame.Rect(4, GRID_TOP - 5, MAIN_W - 8, GRID_H + 10)
    pygame.draw.rect(surface, COLOR_COACH_BODY, coach, border_radius=10)
    pygame.draw.rect(surface, (130, 148, 170), coach, 2, border_radius=10)

    # Teal stripe along top & bottom of coach
    pygame.draw.rect(surface, COLOR_COACH_STRIPE,
                     (4, GRID_TOP - 5, MAIN_W - 8, 4), border_radius=2)
    pygame.draw.rect(surface, COLOR_COACH_STRIPE,
                     (4, GRID_TOP + GRID_H + 3, MAIN_W - 8, 4), border_radius=2)

    # ── Parallax windows on LEFT wall ─────────────────────────────────────
    for r in range(SEAT_ROWS):
        wy = GRID_TOP + r * CELL_H + 6
        draw_parallax_window(surface,
            pygame.Rect(6, wy, 13, CELL_H - 12), scroll_offset)

    # ── Parallax windows on RIGHT wall ────────────────────────────────────
    for r in range(SEAT_ROWS):
        wy = GRID_TOP + r * CELL_H + 6
        draw_parallax_window(surface,
            pygame.Rect(MAIN_W - 19, wy, 13, CELL_H - 12), scroll_offset)

    # ── Aisle ─────────────────────────────────────────────────────────────
    ax = _LEFT_X + 5 * CELL_W + 1
    aw = AISLE_W - 2
    pygame.draw.rect(surface, COLOR_AISLE_BG, (ax, GRID_TOP, aw, GRID_H))
    cx = ax + aw // 2
    for yy in range(GRID_TOP + 4, GRID_TOP + GRID_H - 4, 11):
        pygame.draw.line(surface, (175, 185, 200), (cx, yy), (cx, yy + 5), 1)
    al = fonts["tiny"].render("AISLE", True, (145, 155, 175))
    al_r = pygame.transform.rotate(al, 90)
    surface.blit(al_r, (cx - al_r.get_width()//2,
                         GRID_TOP + GRID_H//2 - al_r.get_height()//2))

    # ── Bay partition lines (between rows) ────────────────────────────────
    for r in range(1, SEAT_ROWS):
        py = GRID_TOP + r * CELL_H
        # Left half
        pygame.draw.line(surface, COLOR_PARTITION,
                         (_LEFT_X - 2, py), (_LEFT_X + 5*CELL_W + 1, py), 3)
        # Right half
        pygame.draw.line(surface, COLOR_PARTITION,
                         (_RIGHT_X - 1, py), (_RIGHT_X + 5*CELL_W + 2, py), 3)

    # ── Bay labels (left margin) ──────────────────────────────────────────
    for r in range(SEAT_ROWS):
        by = GRID_TOP + r * CELL_H
        bl = r * 2 + 1; br_ = r * 2 + 2
        surface.blit(fonts["tiny"].render(f"B{bl}", True, (110,125,148)),
                     (GRID_MARGIN + 1, by + 4))
        surface.blit(fonts["tiny"].render(f"B{br_}", True, (110,125,148)),
                     (GRID_MARGIN + 1, by + CELL_H // 2 + 2))

    # ── Seats ─────────────────────────────────────────────────────────────
    for i in range(TOTAL_SEATS):
        sx, sy = seat_xy(i)
        gi = glitch_seats.get(i)
        _draw_seat(surface, fonts, sx, sy, shared_seats[i], i,
                   is_hover=(i == hover_seat), glitch=gi, now=now)


def _draw_seat(surface, fonts, cx, cy, val, idx,
               is_hover=False, glitch=None, now=0.0):
    """Backrest + cushion chair seat with optional glitch flicker."""
    import pygame
    p = SEAT_PAD
    rx, ry, rw, rh = cx+p, cy+p, CELL_W-2*p, CELL_H-2*p
    rect = pygame.Rect(rx, ry, rw, rh)

    # ── Glitch flicker ────────────────────────────────────────────────────
    use_glitch = False
    if glitch:
        dl, c1, c2 = glitch
        if now < dl:
            use_glitch = True
            flick = (int(now * 10) % 2) == 0
            base = c1 if flick else c2
            back = tuple(max(0, v-35) for v in base)
            tc, brd = COLOR_TEXT_LIGHT, tuple(max(0, v-55) for v in base)

    if not use_glitch:
        if val == SEAT_AVAILABLE:
            base, back = (218, 226, 238), (200, 210, 226)
            tc, brd = COLOR_TEXT_MID, (168, 178, 198)
        elif val == SEAT_COLLISION:
            base, back = COLOR_COLLISION, (170, 28, 28)
            tc, brd = COLOR_COLLISION_TXT, (140, 18, 18)
        elif val == HUMAN_ID:
            base, back = (210, 172, 14), (175, 142, 10)
            tc, brd = COLOR_TEXT_DARK, (142, 115, 6)
        else:
            base = CORE_COLORS.get(val, (180,180,180))
            back = tuple(max(0, c-30) for c in base)
            tc = COLOR_TEXT_LIGHT
            brd = tuple(max(0, c-52) for c in base)

    # Shadow
    pygame.draw.rect(surface, (175, 185, 200), rect.move(2, 2), border_radius=6)
    bk_h = int(rh * 0.36)
    # Backrest
    pygame.draw.rect(surface, back, pygame.Rect(rx, ry, rw, bk_h+4), border_radius=6)
    pygame.draw.rect(surface, back, pygame.Rect(rx, ry+bk_h-2, rw, 6))
    # Cushion
    pygame.draw.rect(surface, base, pygame.Rect(rx, ry+bk_h, rw, rh-bk_h), border_radius=6)
    pygame.draw.rect(surface, base, pygame.Rect(rx, ry+bk_h, rw, 6))
    # Border + fold line + armrests
    pygame.draw.rect(surface, brd, rect, 2, border_radius=6)
    pygame.draw.line(surface, brd, (rx+3, ry+bk_h), (rx+rw-3, ry+bk_h), 1)
    arm_y = ry + bk_h - 3
    pygame.draw.rect(surface, brd, (rx-1, arm_y, 3, 6), border_radius=1)
    pygame.draw.rect(surface, brd, (rx+rw-2, arm_y, 3, 6), border_radius=1)

    # Seat label
    ns = fonts["seat_num"].render(f"S-{idx+1:02d}", True, tc)
    surface.blit(ns, ns.get_rect(centerx=rect.centerx, centery=ry+bk_h//2))
    # Status
    if use_glitch: st = "CORRUPT"
    elif val == SEAT_AVAILABLE: st = "OPEN"
    elif val == SEAT_COLLISION: st = "ERR!"
    elif val == HUMAN_ID: st = "YOU"
    else: st = f"C-{val}"
    ss = fonts["seat_state"].render(st, True, tc)
    surface.blit(ss, ss.get_rect(centerx=rect.centerx, centery=ry+bk_h+(rh-bk_h)//2))

    if is_hover and val == SEAT_AVAILABLE:
        pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], rect.inflate(4,4), 3, border_radius=8)


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — Stats Bar, Event Log
# ═════════════════════════════════════════════════════════════════════════════

def draw_stats_bar(surface, fonts, seats, top_y, elapsed, throughput):
    import pygame
    c_cpu  = sum(1 for i in range(TOTAL_SEATS) if 1 <= seats[i] <= 4)
    c_hum  = sum(1 for i in range(TOTAL_SEATS) if seats[i] == HUMAN_ID)
    c_col  = sum(1 for i in range(TOTAL_SEATS) if seats[i] == SEAT_COLLISION)
    c_opn  = sum(1 for i in range(TOTAL_SEATS) if seats[i] == SEAT_AVAILABLE)
    pygame.draw.rect(surface, (225, 232, 242), (0, top_y, MAIN_W, STATS_BAR_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (MAIN_W, top_y), 1)
    items = [(f"CPU:{c_cpu}", COLOR_ACCENT, 12),
             (f"You:{c_hum}", CORE_COLORS[HUMAN_ID], 96),
             (f"✗ Coll:{c_col}", COLOR_COLLISION, 180),
             (f"Open:{c_opn}", COLOR_TEXT_MID, 300),
             (f"⏱{elapsed:.1f}s", COLOR_TEXT_DARK, 400),
             (f"⚡{throughput:.1f}/s", (20, 155, 68), 500)]
    for t, c, xp in items:
        surface.blit(fonts["stat"].render(t, True, c), (xp, top_y + 7))


def draw_log(surface, fonts, lines, top_y):
    import pygame
    pygame.draw.rect(surface, (222, 230, 240), (0, top_y, MAIN_W, LOG_H))
    pygame.draw.line(surface, (190, 200, 215), (0, top_y), (MAIN_W, top_y), 1)
    surface.blit(fonts["tiny"].render("Event Log:", True, COLOR_TEXT_MID), (12, top_y+4))
    for j, line in enumerate(lines[-6:]):
        if "COLLISION" in line or "✗" in line: c = COLOR_COLLISION
        elif "HUMAN" in line or "YOU" in line: c = CORE_COLORS[HUMAN_ID]
        else: c = COLOR_TEXT_DARK
        surface.blit(fonts["log"].render(line[:105], True, c), (12, top_y+20+j*15))


# ═════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — Core Monitor, Mutex Gate, Needle Speedometer
# ═════════════════════════════════════════════════════════════════════════════

def draw_sidebar(surface, fonts, core_states, n_cores, phase_locked,
                 throughput, now):
    import pygame
    sx = MAIN_W; sw = SIDEBAR_W

    pygame.draw.rect(surface, (230, 238, 248), (sx, 0, sw, WIN_H))
    pygame.draw.line(surface, (170, 182, 200), (sx, 0), (sx, WIN_H), 2)

    # ── Sidebar header ────────────────────────────────────────────────────
    pygame.draw.rect(surface, COLOR_HEADER_BG, (sx, 0, sw, HEADER_H))
    surface.blit(fonts["badge"].render("Hardware Thread", True, COLOR_TEXT_LIGHT),
                 (sx+12, 8))
    surface.blit(fonts["badge"].render("Activity Monitor", True, COLOR_TEXT_LIGHT),
                 (sx+12, 26))
    surface.blit(fonts["tiny"].render("Real-time core states", True, (110,145,195)),
                 (sx+12, 50))

    # ── Core cards ────────────────────────────────────────────────────────
    cy = HEADER_H + 8
    surface.blit(fonts["badge"].render("CPU Cores", True, COLOR_TEXT_DARK), (sx+12, cy))
    cy += 18
    s_labels = {CORE_IDLE: "IDLE", CORE_WAITING: "WAIT", CORE_WORKING: "RUN"}
    s_colors = {CORE_IDLE: COLOR_CORE_IDLE, CORE_WAITING: COLOR_CORE_WAITING,
                CORE_WORKING: COLOR_CORE_WORKING}
    for cid in range(1, 5):
        active = cid <= n_cores
        cs = core_states[cid-1] if active else CORE_IDLE
        card = pygame.Rect(sx+8, cy, sw-16, 42)
        pygame.draw.rect(surface, COLOR_PANEL_BG, card, border_radius=7)
        pygame.draw.rect(surface, COLOR_DIVIDER, card, 1, border_radius=7)
        cc = CORE_COLORS.get(cid, (180,180,180))
        pygame.draw.rect(surface, cc if active else (200,205,215),
                         pygame.Rect(sx+8, cy, 5, 42), border_radius=4)
        lbl = f"Core {cid}" if active else f"Core {cid}"
        surface.blit(fonts["small"].render(lbl, True,
                     COLOR_TEXT_DARK if active else (175,180,195)), (sx+20, cy+3))
        sc = s_colors[cs] if active else COLOR_CORE_IDLE
        # Pulsing dot for WORKING
        r = 5
        if active and cs == CORE_WORKING:
            r = int(4 + 2 * (0.5 + 0.5*math.sin(now*8)))
        pygame.draw.circle(surface, sc, (sx+sw-60, cy+13), r)
        surface.blit(fonts["tiny"].render(
            s_labels[cs] if active else "OFF", True, sc), (sx+sw-48, cy+8))
        # Utilisation bar
        bw = sw - 36; bh = 5; bx = sx+20; by = cy+28
        pygame.draw.rect(surface, (210,215,228), (bx, by, bw, bh), border_radius=3)
        ff = {CORE_IDLE: 0.0, CORE_WAITING: 0.5, CORE_WORKING: 1.0}[cs] if active else 0
        if ff > 0:
            pygame.draw.rect(surface, sc, (bx, by, int(bw*ff), bh), border_radius=3)
        cy += 48

    # ── Mutex Gate ────────────────────────────────────────────────────────
    cy += 6
    surface.blit(fonts["badge"].render("Mutex Gate", True, COLOR_TEXT_DARK), (sx+12, cy))
    cy += 18
    gr = pygame.Rect(sx+10, cy, sw-20, 56)
    pygame.draw.rect(surface, (245, 248, 255), gr, border_radius=7)
    pygame.draw.rect(surface, (160, 170, 190), gr, 2, border_radius=7)

    if not phase_locked:
        surface.blit(fonts["small"].render("NO LOCK", True, COLOR_COLLISION),
                     (sx+22, cy+8))
        surface.blit(fonts["tiny"].render("DATA RACE mode!", True, (180,75,18)),
                     (sx+22, cy+28))
        surface.blit(fonts["tiny"].render("Threads write freely", True, (180,75,18)),
                     (sx+22, cy+42))
    else:
        # Gate icon
        gx = sx + 18
        pygame.draw.rect(surface, (70, 82, 110), (gx, cy+6, 5, 44), border_radius=2)
        pygame.draw.rect(surface, (70, 82, 110), (gx+18, cy+6, 5, 44), border_radius=2)
        waiting = [c for c in range(1, n_cores+1) if core_states[c-1]==CORE_WAITING]
        holder = None
        for c in range(1, n_cores+1):
            if core_states[c-1] == CORE_WORKING: holder = c; break
        if holder:
            pygame.draw.rect(surface, COLOR_COLLISION, (gx+5, cy+28, 18, 3), border_radius=1)
            surface.blit(fonts["tiny"].render(f"Held: C{holder}", True, COLOR_COLLISION),
                         (gx+32, cy+6))
        else:
            surface.blit(fonts["tiny"].render("Open", True, (28,175,68)),
                         (gx+32, cy+6))
        if waiting:
            wt = "Wait: " + ",".join(f"C{w}" for w in waiting)
            surface.blit(fonts["tiny"].render(wt, True, COLOR_CORE_WAITING), (gx+32, cy+22))
        else:
            surface.blit(fonts["tiny"].render("Queue: empty", True, (155,162,180)),
                         (gx+32, cy+22))
        surface.blit(fonts["tiny"].render(f"Thru: {throughput:.1f}/s", True, (90,102,130)),
                     (gx+32, cy+38))

    cy += 64

    # ── Needle Speedometer ────────────────────────────────────────────────
    cy += 8
    surface.blit(fonts["badge"].render("Throughput", True, COLOR_TEXT_DARK), (sx+12, cy))
    cy += 22
    _draw_speedometer(surface, fonts, sx + sw//2, cy + 62, 56, throughput)


def _draw_speedometer(surface, fonts, cx, cy, radius, throughput, max_tp=20.0):
    """Semi-circular needle gauge."""
    import pygame

    # Arc background  — draw manually with line segments
    # Colored zones: green (0-8), yellow (8-14), red (14-20)
    zones = [
        (0.0,  0.4,  (50, 185, 80)),    # green
        (0.4,  0.7,  (220, 185, 25)),   # yellow
        (0.7,  1.0,  (200, 50, 40)),    # red
    ]
    for f_start, f_end, zcol in zones:
        a_start = math.pi * (1 - f_end)
        a_end   = math.pi * (1 - f_start)
        for step in range(20):
            t = a_start + (a_end - a_start) * step / 20
            t2 = a_start + (a_end - a_start) * (step+1) / 20
            x1 = cx + int(radius * math.cos(t))
            y1 = cy - int(radius * math.sin(t))
            x2 = cx + int(radius * math.cos(t2))
            y2 = cy - int(radius * math.sin(t2))
            pygame.draw.line(surface, zcol, (x1, y1), (x2, y2), 4)

    # Tick marks + labels
    for i in range(5):
        frac = i / 4.0
        angle = math.pi * (1 - frac)
        ix = cx + int((radius - 8) * math.cos(angle))
        iy = cy - int((radius - 8) * math.sin(angle))
        ox = cx + int((radius + 2) * math.cos(angle))
        oy = cy - int((radius + 2) * math.sin(angle))
        pygame.draw.line(surface, (70, 80, 100), (ix, iy), (ox, oy), 2)
        tl = fonts["tiny"].render(str(i*5), True, (70, 80, 100))
        lx = cx + int((radius + 14) * math.cos(angle)) - tl.get_width()//2
        ly = cy - int((radius + 14) * math.sin(angle)) - tl.get_height()//2
        surface.blit(tl, (lx, ly))

    # Needle
    frac = min(throughput / max_tp, 1.0)
    angle = math.pi * (1 - frac)
    nl = radius - 10
    nx = cx + int(nl * math.cos(angle))
    ny = cy - int(nl * math.sin(angle))
    pygame.draw.line(surface, (200, 40, 40), (cx, cy), (nx, ny), 3)
    pygame.draw.circle(surface, (65, 75, 95), (cx, cy), 6)
    pygame.draw.circle(surface, (200, 40, 40), (cx, cy), 4)

    # Value
    vt = fonts["stat"].render(f"{throughput:.1f}", True, (20, 155, 68))
    surface.blit(vt, vt.get_rect(centerx=cx, top=cy + 10))
    surface.blit(fonts["tiny"].render("bk/sec", True, (100, 112, 135)),
                 fonts["tiny"].render("bk/sec", True, (100,112,135)).get_rect(
                     centerx=cx, top=cy+26))


# ═════════════════════════════════════════════════════════════════════════════
#  DATA PACKET ANIMATION
# ═════════════════════════════════════════════════════════════════════════════

def draw_data_packets(surface, packets, now):
    import pygame
    alive = []
    for (cid, sidx, t0) in packets:
        el = now - t0
        if el > PACKET_DURATION: continue
        alive.append((cid, sidx, t0))
        prog = 1 - (1 - el/PACKET_DURATION)**3    # ease-out cubic
        src_x = MAIN_W + SIDEBAR_W//2
        src_y = HEADER_H + 26 + (max(cid, 1)-1)*48 + 21
        if cid == HUMAN_ID: src_x, src_y = MAIN_W//2, WIN_H//2
        dx, dy = seat_center(sidx)
        cur_x = int(src_x + (dx-src_x)*prog)
        cur_y = int(src_y + (dy-src_y)*prog)
        col = CORE_COLORS.get(cid, (180,180,180))
        tp = max(0, prog - 0.2)
        tx = int(src_x + (dx-src_x)*tp)
        ty = int(src_y + (dy-src_y)*tp)
        pygame.draw.line(surface, col, (tx, ty), (cur_x, cur_y), 2)
        pygame.draw.circle(surface, col, (cur_x, cur_y), 5)
        pygame.draw.circle(surface, (255,255,255), (cur_x, cur_y), 3)
    return alive


# ═════════════════════════════════════════════════════════════════════════════
#  MODAL, TOAST, OVERLAYS
# ═════════════════════════════════════════════════════════════════════════════

def draw_modal(surface, fonts, sidx, seats, locked):
    import pygame
    ov = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
    ov.fill((8, 18, 32, 130)); surface.blit(ov, (0,0))
    mr = pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, MODAL_H)
    pygame.draw.rect(surface, (22, 30, 48), mr.move(4,4), border_radius=14)
    pygame.draw.rect(surface, COLOR_PANEL_BG, mr, border_radius=14)
    pygame.draw.rect(surface, COLOR_ACCENT, mr, 2, border_radius=14)
    # Title bar
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, 40), border_radius=14)
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y+28, MODAL_W, 14))
    t = fonts["badge"].render("Confirm Ticket Booking", True, COLOR_TEXT_LIGHT)
    surface.blit(t, (MODAL_X + MODAL_W//2 - t.get_width()//2, MODAL_Y+11))
    # Seat info
    v = seats[sidx]
    surface.blit(fonts["stat"].render(f"Seat  S-{sidx+1:02d}", True, COLOR_ACCENT),
                 (MODAL_X+24, MODAL_Y+54))
    if v == SEAT_AVAILABLE: st, sc = "AVAILABLE", (20, 150, 65)
    elif v == SEAT_COLLISION: st, sc = "CORRUPTED", COLOR_COLLISION
    elif v == HUMAN_ID: st, sc = "YOURS", CORE_COLORS[HUMAN_ID]
    elif 1 <= v <= 4: st, sc = f"TAKEN by C{v}!", COLOR_COLLISION
    else: st, sc = "UNAVAIL", COLOR_TEXT_MID
    surface.blit(fonts["small"].render(f"Status: {st}", True, sc), (MODAL_X+24, MODAL_Y+78))
    lt = "Mutex protects this booking." if locked else "⚠ NO LOCK — race possible!"
    lc = (20,125,65) if locked else (195,90,12)
    surface.blit(fonts["tiny"].render(lt, True, lc), (MODAL_X+24, MODAL_Y+102))
    surface.blit(fonts["tiny"].render("⚡ Workers still active!", True, (175,75,18)),
                 (MODAL_X+24, MODAL_Y+120))
    # Buttons
    yr = pygame.Rect(*_YES)
    pygame.draw.rect(surface, (20, 150, 65), yr, border_radius=7)
    yt = fonts["badge"].render("✓ CONFIRM", True, (255,255,255))
    surface.blit(yt, yt.get_rect(center=yr.center))
    cr = pygame.Rect(*_CANC)
    pygame.draw.rect(surface, (145, 152, 172), cr, border_radius=7)
    ct = fonts["badge"].render("✗ CANCEL", True, (255,255,255))
    surface.blit(ct, ct.get_rect(center=cr.center))


def draw_toast(surface, fonts, text, color):
    import pygame
    ts = fonts["small"].render(text, True, (255,255,255))
    tw = ts.get_width()+32; tx = (MAIN_W-tw)//2; ty = HEADER_H+2
    pygame.draw.rect(surface, (0,0,0), pygame.Rect(tx+2,ty+2,tw,30), border_radius=9)
    pygame.draw.rect(surface, color, pygame.Rect(tx,ty,tw,30), border_radius=9)
    pygame.draw.rect(surface, (255,255,255), pygame.Rect(tx,ty,tw,30), 2, border_radius=9)
    surface.blit(ts, (tx+16, ty+6))


def draw_pause_overlay(surface, fonts):
    """Full-screen dark overlay with PAUSED text."""
    import pygame
    ov = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
    ov.fill((5, 15, 28, 160)); surface.blit(ov, (0, 0))
    t1 = fonts["pause"].render("SIMULATION  PAUSED", True, (255, 255, 255))
    t2 = fonts["badge"].render("Press  [P]  to resume", True, (180, 210, 245))
    r1 = t1.get_rect(center=(WIN_W//2, WIN_H//2 - 16))
    r2 = t2.get_rect(center=(WIN_W//2, WIN_H//2 + 22))
    bg = r1.union(r2).inflate(60, 30)
    pygame.draw.rect(surface, (12, 38, 58), bg, border_radius=14)
    pygame.draw.rect(surface, COLOR_COACH_STRIPE, bg, 3, border_radius=14)
    surface.blit(t1, r1); surface.blit(t2, r2)


def draw_waiting_overlay(surface, fonts, pd, phase_idx, now):
    """Pulsing overlay: 'Phase X Complete — Press SPACE for Phase Y'."""
    import pygame
    pulse = 0.5 + 0.5 * math.sin(now * 3.5)
    alpha = int(110 + pulse * 70)
    ov = pygame.Surface((MAIN_W, WIN_H - CTRL_BAR_H), pygame.SRCALPHA)
    ov.fill((8, 28, 45, alpha)); surface.blit(ov, (0, 0))

    if phase_idx + 1 < len(PHASE_DEFS):
        nxt = PHASE_DEFS[phase_idx + 1]
        txt = f"Phase {pd['num']} Complete  —  Press SPACE for Phase {nxt['num']}"
    else:
        txt = f"Phase {pd['num']} Complete  —  Press SPACE for Telemetry"
    ts = fonts["overlay"].render(txt, True, (255, 255, 255))
    tr = ts.get_rect(center=(MAIN_W // 2, WIN_H // 2 - 10))
    bg = tr.inflate(50, 24)
    pygame.draw.rect(surface, pd["overlay_color"], bg, border_radius=12)
    pygame.draw.rect(surface, (255, 255, 255), bg, 2, border_radius=12)
    surface.blit(ts, tr)


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — TELEMETRY  (Amdahl's Law Dashboard)
# ═════════════════════════════════════════════════════════════════════════════

def draw_telemetry(surface, fonts, ptimes, p4_col, h_total):
    import pygame
    surface.fill(COLOR_BG)
    t1 = max(ptimes.get(1, 1.0), 0.001)
    t2 = max(ptimes.get(2, t1),  0.001)
    t4 = max(ptimes.get(3, t1),  0.001)
    s2, s4 = t1/t2, t1/t4
    eff4 = s4/4.0
    sf = max(0.0, ((1.0/s4)-0.25)/0.75)

    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, 80))
    surface.blit(fonts["title"].render(
        "Phase 5  ·  Performance Telemetry  &  Amdahl's Law", True, COLOR_TEXT_LIGHT), (20,10))
    surface.blit(fonts["small"].render(
        "Empirical speedup from Phases 1–3  ·  Pure pygame, zero deps",
        True, (130, 160, 210)), (20, 46))

    fy = WIN_H - 30
    pygame.draw.rect(surface, (222,230,240), (0, fy, WIN_W, 30))
    surface.blit(fonts["small"].render("Simulation complete  ·  Press ESC to exit",
                 True, COLOR_TEXT_MID), (20, fy+7))

    cy, ch = 88, fy - 88
    PW, PX, gap = 260, 14, 6
    cards = [
        ("T₁  —  1 Core",  f"{t1:.3f} s", "Serial (P1)",  CORE_COLORS[1]),
        ("T₂  —  2 Cores", f"{t2:.3f} s", "2-core (P2)",  CORE_COLORS[2]),
        ("T₄  —  4 Cores", f"{t4:.3f} s", "4-core (P3)",  CORE_COLORS[4]),
        ("S₂  Speedup",    f"{s2:.3f}×",  "ideal=2×",     CORE_COLORS[2]),
        ("S₄  Speedup",    f"{s4:.3f}×",  "ideal=4×",     CORE_COLORS[4]),
        ("Efficiency",     f"{eff4*100:.1f}%", "S₄÷4",    (88,55,200)),
        ("Serial Frac.",   f"{sf*100:.1f}%","Amdahl bot.", COLOR_COLLISION),
        ("P4 Collisions",  str(p4_col),   "corruptions",  (195,45,30)),
        ("Human Bookings", str(h_total),  "user clicks",  CORE_COLORS[HUMAN_ID]),
    ]
    card_h = min(60, max(52, (ch-gap*(len(cards)-1))//len(cards)))
    for i, (lbl, val, sub, acc) in enumerate(cards):
        ccy = cy + i*(card_h+gap)
        cr = pygame.Rect(PX, ccy, PW, card_h)
        pygame.draw.rect(surface, COLOR_PANEL_BG, cr, border_radius=8)
        pygame.draw.rect(surface, COLOR_DIVIDER, cr, 1, border_radius=8)
        pygame.draw.rect(surface, acc, pygame.Rect(PX, ccy, 4, card_h), border_radius=3)
        surface.blit(fonts["tiny"].render(lbl, True, COLOR_TEXT_MID), (PX+12, ccy+3))
        surface.blit(fonts["stat"].render(val, True, acc), (PX+12, ccy+16))
        surface.blit(fonts["tiny"].render(sub, True, (155,162,185)), (PX+12, ccy+card_h-14))

    GX = PX+PW+12; GW = WIN_W-GX-12; GY = cy; GH = ch
    gcr = pygame.Rect(GX, GY, GW, GH)
    pygame.draw.rect(surface, COLOR_GRAPH_BG, gcr, border_radius=11)
    pygame.draw.rect(surface, COLOR_DIVIDER, gcr, 1, border_radius=11)
    surface.blit(fonts["badge"].render("Speedup vs. Physical Cores", True, COLOR_TEXT_DARK),
                 (GX+16, GY+12))

    ML,MR,MT,MB = 68,28,48,58
    px0=GX+ML; py0=GY+MT; pw=GW-ML-MR; ph=GH-MT-MB; YMAX=5.0
    xc=[1,2,4]; yi=[1.0,2.0,4.0]; ya=[1.0,s2,s4]
    def to_px(cv,sv):
        return int(px0+(cv-1)/3.0*pw), int(py0+ph-sv/YMAX*ph)

    for yt_ in range(6):
        _,gy=to_px(1,yt_)
        pygame.draw.line(surface,COLOR_GRID_LINE,(px0,gy),(px0+pw,gy),1)
        tl=fonts["axis"].render(f"{yt_}×",True,COLOR_AXIS)
        surface.blit(tl,(px0-tl.get_width()-6,gy-tl.get_height()//2))
    for xv in xc:
        gx,_=to_px(xv,0)
        pygame.draw.line(surface,COLOR_GRID_LINE,(gx,py0),(gx,py0+ph),1)
        tl=fonts["axis"].render(str(xv),True,COLOR_AXIS)
        surface.blit(tl,(gx-tl.get_width()//2,py0+ph+8))
    pygame.draw.line(surface,COLOR_AXIS,(px0,py0),(px0,py0+ph),2)
    pygame.draw.line(surface,COLOR_AXIS,(px0,py0+ph),(px0+pw,py0+ph),2)
    xl=fonts["small"].render("Physical Cores",True,COLOR_AXIS)
    surface.blit(xl,(px0+pw//2-xl.get_width()//2,py0+ph+32))
    ylr=pygame.transform.rotate(fonts["small"].render("Speedup S=T₁/Tₙ",True,COLOR_AXIS),90)
    surface.blit(ylr,(GX+4,py0+ph//2-ylr.get_height()//2))

    ipts=[to_px(c,y) for c,y in zip(xc,yi)]
    _draw_dashed(surface,COLOR_IDEAL_LINE,ipts,3,14,8)
    for pt in ipts:
        pygame.draw.circle(surface,COLOR_IDEAL_LINE,pt,7)
        pygame.draw.circle(surface,COLOR_GRAPH_BG,pt,4)

    apts=[to_px(c,y) for c,y in zip(xc,ya)]
    if len(apts)>1: pygame.draw.lines(surface,COLOR_ACTUAL_LINE,False,apts,3)
    for (apx,apy),(_,yv) in zip(apts,zip(xc,ya)):
        pygame.draw.circle(surface,COLOR_ACTUAL_LINE,(apx,apy),8)
        pygame.draw.circle(surface,COLOR_GRAPH_BG,(apx,apy),4)
        tag=fonts["axis"].render(f"{yv:.2f}×",True,COLOR_TEXT_DARK)
        tr=pygame.Rect(apx-tag.get_width()//2-3,apy-32,tag.get_width()+6,tag.get_height()+3)
        pygame.draw.rect(surface,(230,245,235),tr,border_radius=4)
        pygame.draw.rect(surface,COLOR_ACTUAL_LINE,tr,1,border_radius=4)
        surface.blit(tag,(tr.x+3,tr.y+1))

    lx,ly=px0+pw-210,py0+10
    pygame.draw.rect(surface,(240,243,255),pygame.Rect(lx-6,ly-4,204,52),border_radius=6)
    pygame.draw.rect(surface,COLOR_DIVIDER,pygame.Rect(lx-6,ly-4,204,52),1,border_radius=6)
    _draw_dashed(surface,COLOR_IDEAL_LINE,[(lx,ly+8),(lx+26,ly+8)],2,6,4)
    pygame.draw.circle(surface,COLOR_IDEAL_LINE,(lx+36,ly+8),4)
    surface.blit(fonts["axis"].render("Ideal",True,COLOR_TEXT_DARK),(lx+46,ly+2))
    pygame.draw.line(surface,COLOR_ACTUAL_LINE,(lx,ly+28),(lx+26,ly+28),2)
    pygame.draw.circle(surface,COLOR_ACTUAL_LINE,(lx+36,ly+28),4)
    surface.blit(fonts["axis"].render("Actual",True,COLOR_TEXT_DARK),(lx+46,ly+22))

    ax_,ay_=px0+14,py0+ph-64
    pygame.draw.rect(surface,(246,248,255),pygame.Rect(ax_-6,ay_-3,350,62),border_radius=6)
    pygame.draw.rect(surface,COLOR_DIVIDER,pygame.Rect(ax_-6,ay_-3,350,62),1,border_radius=6)
    for li,al in enumerate([
        f"Amdahl: S(N)=1/(s+(1-s)/N)",
        f"Serial fraction s ≈ {sf*100:.1f}%",
        f"Efficiency at 4 cores = {eff4*100:.1f}%"]):
        surface.blit(fonts["tiny"].render(al,True,
                     COLOR_TEXT_DARK if li==0 else (85,95,115)), (ax_,ay_+li*19))


def _draw_dashed(surface, color, points, width=2, dash=12, gap=6):
    import pygame
    for i in range(len(points)-1):
        x1,y1=points[i]; x2,y2=points[i+1]
        dx,dy=x2-x1,y2-y1; ln=math.hypot(dx,dy)
        if ln==0: continue
        ux,uy=dx/ln,dy/ln; pos,d=0.0,True
        while pos<ln:
            seg=dash if d else gap; end=min(pos+seg,ln)
            if d:
                pygame.draw.line(surface,color,
                    (int(x1+ux*pos),int(y1+uy*pos)),
                    (int(x1+ux*end),int(y1+uy*end)),width)
            pos+=seg; d=not d


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "Indian Railways  ·  LHB Coach Reservation Simulator  v6.0  —  Bogie Architecture")
    clock = pygame.time.Clock()

    def F(sz, bold=False):
        try:    return pygame.font.SysFont("Segoe UI", sz, bold=bold)
        except: return pygame.font.Font(None, sz)

    fonts = {
        "title"     : F(21, True),
        "badge"     : F(13, True),
        "status"    : F(11),
        "small"     : F(12),
        "seat_num"  : F(10, True),
        "seat_state": F(9),
        "log"       : F(10),
        "stat"      : F(12, True),
        "overlay"   : F(22, True),
        "pause"     : F(30, True),
        "axis"      : F(11),
        "tiny"      : F(9),
    }

    # ── Shared hardware memory & IPC ──────────────────────────────────────
    shared_seats = multiprocessing.Array('i', TOTAL_SEATS)
    core_states  = multiprocessing.Array('i', 4)
    log_queue    = multiprocessing.Queue()
    event_queue  = multiprocessing.Queue()
    run_flag     = multiprocessing.Event()
    run_flag.set()   # initially running (not paused)
    speed_mult   = multiprocessing.Value('d', 1.0)

    # ── State ─────────────────────────────────────────────────────────────
    state         = "running"     # "running" | "waiting_presenter" | "telemetry"
    phase_idx     = 0
    processes     = []
    current_lock  = None
    log_lines     = []
    status_text   = ""

    phase_start        = 0.0
    phase_elapsed      = 0.0
    total_pause_dur    = 0.0      # accumulated pause time for timing accuracy
    pause_perf_start   = 0.0

    phase_times        = {}
    p4_collisions      = 0
    human_total        = 0

    is_paused  = False
    is_slow    = False

    modal_open     = False
    modal_seat_idx = -1
    toast_text  = ""
    toast_color = (0, 0, 0)
    toast_start = 0.0

    data_packets       = []
    glitch_seats       = {}
    booking_timestamps = deque(maxlen=200)
    throughput         = 0.0
    last_tp_calc       = 0.0
    scroll_offset      = 0.0     # parallax window scroll

    # ── Launch Phase 1 ────────────────────────────────────────────────────
    reset_seats(shared_seats)
    pd = PHASE_DEFS[phase_idx]
    processes, current_lock, phase_start = launch_phase(
        pd, shared_seats, core_states, log_queue, event_queue, run_flag, speed_mult)
    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
    log_lines.append("  💡 Click any OPEN seat to book!")
    status_text = f"Running {pd['label']}…"

    # ══════════════════════════════════════════════════════════════════════
    #   MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════
    running = True
    while running:
        now = time.time()

        # ── Events ─────────────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                kill_all(processes); running = False

            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    kill_all(processes); running = False

                # ── [P] Pause / Resume ────────────────────────────────
                elif ev.key == pygame.K_p and state != "telemetry":
                    if is_paused:
                        # Resume
                        is_paused = False
                        run_flag.set()
                        total_pause_dur += time.perf_counter() - pause_perf_start
                        log_lines.append("[SYSTEM] Simulation RESUMED.")
                    else:
                        # Pause
                        is_paused = True
                        run_flag.clear()
                        pause_perf_start = time.perf_counter()
                        log_lines.append("[SYSTEM] Simulation PAUSED.")

                # ── [R] Reset current phase ───────────────────────────
                elif ev.key == pygame.K_r and state in ("running", "waiting_presenter"):
                    # Ensure not paused so workers can be killed cleanly
                    run_flag.set()
                    is_paused = False
                    kill_all(processes)
                    drain_queue(log_queue); drain_queue(event_queue)
                    reset_seats(shared_seats)
                    for i in range(4): core_states[i] = CORE_IDLE
                    data_packets.clear(); glitch_seats.clear()
                    booking_timestamps.clear(); throughput = 0.0
                    total_pause_dur = 0.0; modal_open = False
                    log_lines.clear()
                    processes, current_lock, phase_start = launch_phase(
                        pd, shared_seats, core_states, log_queue, event_queue,
                        run_flag, speed_mult)
                    log_lines.append(f"══  {pd['label']}  —  RESTARTED  ══")
                    log_lines.append("  🔄 Phase reset by presenter.")
                    status_text = f"Running {pd['label']}…"
                    state = "running"

                # ── [S] Slow-Mo toggle ────────────────────────────────
                elif ev.key == pygame.K_s and state != "telemetry":
                    is_slow = not is_slow
                    speed_mult.value = SLOW_MO_FACTOR if is_slow else 1.0
                    log_lines.append(
                        f"[SYSTEM] Slow-Mo {'ON (×5)' if is_slow else 'OFF'}.")

                # ── [SPACE] Next phase ────────────────────────────────
                elif ev.key == pygame.K_SPACE:
                    if state == "waiting_presenter":
                        nxt = phase_idx + 1
                        if nxt < len(PHASE_DEFS):
                            phase_idx = nxt; pd = PHASE_DEFS[phase_idx]
                            reset_seats(shared_seats)
                            log_lines.clear(); data_packets.clear()
                            glitch_seats.clear(); booking_timestamps.clear()
                            throughput = 0.0; total_pause_dur = 0.0
                            modal_open = False
                            processes, current_lock, phase_start = launch_phase(
                                pd, shared_seats, core_states, log_queue,
                                event_queue, run_flag, speed_mult)
                            log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
                            log_lines.append("  💡 Click seats to book!")
                            status_text = f"Running {pd['label']}…"
                            state = "running"
                        else:
                            state = "telemetry"

            # ── Mouse click ───────────────────────────────────────────
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if is_paused: continue    # ignore clicks while paused
                mx, my = ev.pos
                if modal_open:
                    if _in_rect(mx, my, _YES):
                        ok, msg = handle_human_book(
                            modal_seat_idx, shared_seats, current_lock, pd["locked"])
                        if ok:
                            human_total += 1; toast_color = (20, 150, 65)
                            data_packets.append((HUMAN_ID, modal_seat_idx, now))
                            booking_timestamps.append(now)
                        else:
                            toast_color = COLOR_COLLISION
                        log_lines.append(f"[HUMAN] {msg}")
                        toast_text = msg; toast_start = now; modal_open = False
                    elif _in_rect(mx, my, _CANC):
                        modal_open = False; log_lines.append("[HUMAN] Cancelled.")
                elif state == "running":
                    si = mouse_to_seat(mx, my)
                    if si >= 0:
                        v = shared_seats[si]
                        if v == SEAT_AVAILABLE:
                            modal_open = True; modal_seat_idx = si
                        elif v == HUMAN_ID:
                            toast_text = f"Already yours: S-{si+1:02d}"
                            toast_color = CORE_COLORS[HUMAN_ID]; toast_start = now
                        elif v == SEAT_COLLISION:
                            toast_text = f"S-{si+1:02d} corrupted!"
                            toast_color = COLOR_COLLISION; toast_start = now
                        else:
                            toast_text = f"S-{si+1:02d} taken by C{v}"
                            toast_color = COLOR_TEXT_MID; toast_start = now

        if not running: break

        # ── Drain queues ───────────────────────────────────────────────
        while not log_queue.empty():
            try: log_lines.append(log_queue.get_nowait())
            except: pass
        while not event_queue.empty():
            try:
                ed = event_queue.get_nowait()
                if ed[0] == 'BOOK':
                    _, cid, si, t = ed
                    data_packets.append((cid, si, t))
                    booking_timestamps.append(t)
                elif ed[0] == 'COLLISION':
                    _, cid, si, t = ed
                    c1 = CORE_COLORS.get(cid, (180,180,180))
                    ov = shared_seats[si]
                    c2 = CORE_COLORS.get(ov, COLOR_COLLISION) if ov > 0 else COLOR_COLLISION
                    glitch_seats[si] = (now + GLITCH_DURATION, c1, c2)
                    data_packets.append((cid, si, t))
            except: pass

        # ── Throughput ─────────────────────────────────────────────────
        if now - last_tp_calc >= THROUGHPUT_INTERVAL:
            cutoff = now - 2.0
            throughput = sum(1 for ts in booking_timestamps if ts >= cutoff) / 2.0
            last_tp_calc = now

        # ── Hover ──────────────────────────────────────────────────────
        hover_seat = -1
        if state == "running" and not modal_open and not is_paused:
            hover_seat = mouse_to_seat(*pygame.mouse.get_pos())

        # ── Purge expired glitches ─────────────────────────────────────
        for k in [k for k,(dl,_,_) in glitch_seats.items() if now >= dl]:
            del glitch_seats[k]

        # ── Scroll offset for parallax (pauses when paused) ───────────
        if not is_paused and state != "telemetry":
            scroll_offset += 1.8

        # ── Timing (excludes pause duration) ───────────────────────────
        if is_paused:
            phase_elapsed = pause_perf_start - phase_start - total_pause_dur
        else:
            phase_elapsed = time.perf_counter() - phase_start - total_pause_dur

        # ── State machine ──────────────────────────────────────────────
        if state == "running":
            status_text = f"{pd['label']}  —  {phase_elapsed:.1f}s"
            if all_done(processes):
                modal_open = False
                if pd["timed"]:
                    phase_times[pd["num"]] = phase_elapsed
                    log_lines.append(f"   ⏱  T{pd['cores']} = {phase_elapsed:.3f}s")
                else:
                    p4_collisions = sum(
                        1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION)
                log_lines.append(f"══  {pd['label']}  —  DONE ({phase_elapsed:.2f}s)  ══")
                log_lines.append("  ▶ Press [SPACE] to continue.")
                if phase_idx + 1 < len(PHASE_DEFS):
                    nxt_pd = PHASE_DEFS[phase_idx + 1]
                    status_text = f"Phase {pd['num']} done  |  SPACE → Phase {nxt_pd['num']}"
                else:
                    status_text = f"Phase {pd['num']} done  |  SPACE → Telemetry"
                current_lock = None; state = "waiting_presenter"
                for i in range(4): core_states[i] = CORE_IDLE

        # ── Rendering ──────────────────────────────────────────────────
        if state == "telemetry":
            draw_telemetry(screen, fonts, phase_times, p4_collisions, human_total)
        else:
            screen.fill(COLOR_BG)
            draw_header(screen, fonts, pd, status_text, phase_idx)
            draw_legend(screen, fonts, HEADER_H, pd["cores"])
            draw_train_car(screen, fonts, shared_seats, hover_seat,
                           glitch_seats, now, scroll_offset)
            draw_stats_bar(screen, fonts, shared_seats, STATS_TOP,
                           phase_elapsed, throughput)
            draw_log(screen, fonts, log_lines, LOG_TOP)
            draw_control_bar(screen, fonts, is_paused, is_slow)
            draw_sidebar(screen, fonts, core_states, pd["cores"], pd["locked"],
                         throughput, now)

            data_packets = draw_data_packets(screen, data_packets, now)

            if state == "waiting_presenter":
                draw_waiting_overlay(screen, fonts, pd, phase_idx, now)

            if modal_open:
                draw_modal(screen, fonts, modal_seat_idx,
                           shared_seats, pd["locked"])

        if is_paused and state != "telemetry":
            draw_pause_overlay(screen, fonts)

        if toast_text and (now - toast_start < TOAST_DURATION):
            draw_toast(screen, fonts, toast_text, toast_color)

        pygame.display.flip()
        clock.tick(FPS)

    # ── Clean shutdown ────────────────────────────────────────────────────
    run_flag.set()   # unblock any waiting workers before terminate
    kill_all(processes)
    pygame.quit()
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
