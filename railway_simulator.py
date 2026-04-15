"""
================================================================================
  MULTI-CORE RAILWAY RESERVATION SIMULATOR  —  v5.0  (Systems Monitor Edition)
  Academic Demonstration: Hardware Parallelism, Race Conditions, Mutex & Amdahl's Law

  Phase 1 : 1 Core   · Serial Baseline      (mutex protected, timed → T₁)
  Phase 2 : 2 Cores  · Parallel Mutex       (mutex protected, timed → T₂)
  Phase 3 : 4 Cores  · Parallel Mutex       (mutex protected, timed → T₄)
  Phase 4 : 4 Cores  · CHAOS — No Lock      (race conditions, untimed)
  Phase 5 : Telemetry Dashboard             (Amdahl's Law line graph, pure pygame)

  v5.0 UPGRADES:
    • Hardware Thread Activity sidebar with animated CPU core icons.
    • Core states: Active (green), Waiting for Lock (yellow), Idle (gray).
    • Visual Mutex Gate with live waiting queue.
    • Real-time Throughput Gauge (bookings/sec, updates every 0.5s).
    • Animated "data packet" flight paths from Core Hub to booked seat.
    • Collision Glitch Effect: rapid flicker before solid RED in Phase 4.
    • Window widened to 1200px to accommodate sidebar.

  Architecture:
    Main Process (Core 0) → Pygame UI + State Orchestrator + Human click handler.
    Worker Processes 1-4  → multiprocessing.Process booking agents.
    Shared Memory         → multiprocessing.Array('i', 50)  [seats]
                          → multiprocessing.Array('i', 5)   [core states]
    IPC Channels          → multiprocessing.Queue × 2  (log + events)
    Synchronization       → multiprocessing.Lock   (Phase 1/2/3 only).
================================================================================
"""

import multiprocessing
import time
import random
import sys
import math
from collections import deque

if __name__ == "__main__":
    multiprocessing.freeze_support()

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_SEATS    = 50
TOTAL_BOOKINGS = 32

PARALLEL_WORK_MIN = 0.10
PARALLEL_WORK_MAX = 0.18
DB_WRITE_LATENCY  = 0.007

RACE_WIN_MIN = 0.06
RACE_WIN_MAX = 0.12

PHASE_PAUSE_SEC = 2.5
HUMAN_ID        = 99
TOAST_DURATION  = 2.0

SEAT_AVAILABLE =  0
SEAT_COLLISION = -1

# Core state codes (written to shared core_states array by workers)
CORE_IDLE    = 0
CORE_WAITING = 1   # blocked on lock.acquire()
CORE_WORKING = 2   # inside critical section

# Glitch / animation timing
GLITCH_DURATION   = 0.35   # seconds a collision seat flickers
PACKET_DURATION   = 0.30   # seconds for data-packet flight animation
THROUGHPUT_INTERVAL = 0.5  # recalculate throughput every 0.5s

PHASE_DEFS = [
    {
        "num": 1, "cores": 1, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS,
        "label": "Phase 1  ·  1 Core — Serial Baseline",
        "badge_color": (37, 96, 232),
        "overlay_color": (37, 96, 232),
        "detail": "Single worker. Sequential. Establishes T₁.",
    },
    {
        "num": 2, "cores": 2, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS // 2,
        "label": "Phase 2  ·  2 Cores — Parallel Mutex",
        "badge_color": (20, 160, 72),
        "overlay_color": (20, 160, 72),
        "detail": "2 workers. Mutex serialises critical section.",
    },
    {
        "num": 3, "cores": 4, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 3  ·  4 Cores — Parallel Mutex",
        "badge_color": (122, 55, 238),
        "overlay_color": (122, 55, 238),
        "detail": "4 workers. Max parallelism with Mutex. T₄ recorded.",
    },
    {
        "num": 4, "cores": 4, "locked": False, "timed": False,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 4  ·  4 Cores — CHAOS (No Lock)",
        "badge_color": (210, 38, 38),
        "overlay_color": (210, 38, 38),
        "detail": "No mutex. Race conditions. RED = corruption.",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE
# ═════════════════════════════════════════════════════════════════════════════

COLOR_BG          = (244, 245, 250)
COLOR_PANEL_BG    = (255, 255, 255)
COLOR_HEADER_BG   = (18,  24,  52)
COLOR_TEXT_DARK    = (16,  18,  28)
COLOR_TEXT_LIGHT   = (238, 242, 255)
COLOR_TEXT_MID     = (88,  94, 116)
COLOR_ACCENT       = (55,  92, 218)
COLOR_DIVIDER      = (208, 212, 226)
COLOR_COLLISION    = (212, 34,  34)
COLOR_COLLISION_TXT= (255, 255, 255)

CORE_COLORS = {
    1:        ( 37,  96, 232),
    2:        ( 20, 160,  72),
    3:        (122,  55, 238),
    4:        (228,  82,   8),
    HUMAN_ID: (204, 164,   0),
}

# Core state indicator colours
COLOR_CORE_IDLE    = (155, 160, 178)
COLOR_CORE_WAITING = (230, 190,  20)
COLOR_CORE_WORKING = ( 30, 185,  72)

COLOR_GRAPH_BG   = (252, 253, 255)
COLOR_GRID_LINE  = (208, 213, 230)
COLOR_AXIS       = (42,  48,  70)
COLOR_IDEAL_LINE = (55,  92, 218)
COLOR_ACTUAL_LINE= (20, 160,  72)

# ═════════════════════════════════════════════════════════════════════════════
#  LAYOUT GEOMETRY — 1200 × 720  (wider for sidebar)
# ═════════════════════════════════════════════════════════════════════════════

SIDEBAR_W = 218       # right sidebar width
WIN_W     = 1200
WIN_H     = 720
MAIN_W    = WIN_W - SIDEBAR_W   # 982 — main content area
FPS       = 30

HEADER_H    = 106
LEGEND_H    = 36
GRID_MARGIN = 12
AISLE_W     = 34
ROW_LABEL_W = 28
SEAT_COLS   = 10
SEAT_ROWS   = 5
CELL_H      = 68
SEAT_PAD    = 4

_AVAIL_W = MAIN_W - 2 * GRID_MARGIN - AISLE_W - ROW_LABEL_W
CELL_W   = _AVAIL_W // SEAT_COLS

_LEFT_X  = GRID_MARGIN + ROW_LABEL_W
_RIGHT_X = _LEFT_X + 5 * CELL_W + AISLE_W

GRID_TOP    = HEADER_H + LEGEND_H + 4
GRID_H      = SEAT_ROWS * CELL_H
STATS_TOP   = GRID_TOP + GRID_H + 2
STATS_BAR_H = 32
LOG_TOP     = STATS_TOP + STATS_BAR_H
LOG_H       = WIN_H - LOG_TOP

# Modal geometry
MODAL_W, MODAL_H = 400, 250
MODAL_X = (MAIN_W - MODAL_W) // 2
MODAL_Y = (WIN_H  - MODAL_H) // 2
_YES  = (MODAL_X + 40,  MODAL_Y + MODAL_H - 68, 140, 44)
_CANC = (MODAL_X + MODAL_W - 180, MODAL_Y + MODAL_H - 68, 140, 44)


# ═════════════════════════════════════════════════════════════════════════════
#  COORDINATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _in_rect(px, py, r):
    return r[0] <= px < r[0]+r[2] and r[1] <= py < r[1]+r[3]

def seat_xy(idx):
    row = idx // SEAT_COLS
    col = idx %  SEAT_COLS
    x = (_LEFT_X + col * CELL_W) if col < 5 else (_RIGHT_X + (col - 5) * CELL_W)
    y = GRID_TOP + row * CELL_H
    return x, y

def seat_center(idx):
    sx, sy = seat_xy(idx)
    return sx + CELL_W // 2, sy + CELL_H // 2

def mouse_to_seat(mx, my):
    if my < GRID_TOP or my >= GRID_TOP + GRID_H:
        return -1
    row = (my - GRID_TOP) // CELL_H
    if row < 0 or row >= SEAT_ROWS:
        return -1
    if _LEFT_X <= mx < _LEFT_X + 5 * CELL_W:
        col = (mx - _LEFT_X) // CELL_W
    elif _RIGHT_X <= mx < _RIGHT_X + 5 * CELL_W:
        col = 5 + (mx - _RIGHT_X) // CELL_W
    else:
        return -1
    sx, sy = seat_xy(row * SEAT_COLS + col)
    if mx < sx + SEAT_PAD or mx > sx + CELL_W - SEAT_PAD:
        return -1
    if my < sy + SEAT_PAD or my > sy + CELL_H - SEAT_PAD:
        return -1
    idx = row * SEAT_COLS + col
    return idx if 0 <= idx < TOTAL_SEATS else -1


# ═════════════════════════════════════════════════════════════════════════════
#  WORKER PROCESS — upgraded to report state transitions via shared array
# ═════════════════════════════════════════════════════════════════════════════

def booking_agent(core_id, shared_seats, core_states, lock, bookings,
                  log_queue, event_queue):
    """
    Each worker writes its real-time state to core_states[core_id-1]:
      CORE_IDLE(0)    → between bookings / done
      CORE_WAITING(1) → blocked on lock.acquire()
      CORE_WORKING(2) → inside critical section
    """
    secured = collisions = 0

    for _ in range(bookings):
        # Signal: about to acquire lock (or enter critical section)
        if lock is not None:
            core_states[core_id - 1] = CORE_WAITING
            lock.acquire()
        core_states[core_id - 1] = CORE_WORKING

        try:
            target = -1
            for idx in range(TOTAL_SEATS):
                if shared_seats[idx] == SEAT_AVAILABLE:
                    target = idx
                    break
            if target == -1:
                log_queue.put(f"[Core {core_id}] No seats left — exiting.")
                break

            read_val = shared_seats[target]

            if lock is None:
                time.sleep(random.uniform(RACE_WIN_MIN, RACE_WIN_MAX))
            else:
                time.sleep(DB_WRITE_LATENCY)

            if read_val == SEAT_AVAILABLE:
                if shared_seats[target] == SEAT_AVAILABLE:
                    shared_seats[target] = core_id
                    secured += 1
                    log_queue.put(f"[Core {core_id}] ✓ Booked seat #{target+1:02d}")
                    event_queue.put(('BOOK', core_id, target, time.time()))
                else:
                    shared_seats[target] = SEAT_COLLISION
                    collisions += 1
                    log_queue.put(
                        f"[Core {core_id}] ✗ COLLISION seat #{target+1:02d}"
                        f" — overwritten!")
                    event_queue.put(('COLLISION', core_id, target, time.time()))
        finally:
            if lock is not None:
                lock.release()

        core_states[core_id - 1] = CORE_IDLE
        if lock is not None:
            time.sleep(random.uniform(PARALLEL_WORK_MIN, PARALLEL_WORK_MAX))
        else:
            time.sleep(random.uniform(0.02, 0.06))

    core_states[core_id - 1] = CORE_IDLE
    log_queue.put(f"[Core {core_id}] Done. Secured: {secured}  Collisions: {collisions}")


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def launch_phase(phase_def, shared_seats, core_states, log_queue, event_queue):
    lock = multiprocessing.Lock() if phase_def["locked"] else None
    # Reset core states
    for i in range(4):
        core_states[i] = CORE_IDLE
    procs = []
    t0 = time.perf_counter()
    for cid in range(1, phase_def["cores"] + 1):
        p = multiprocessing.Process(
            target=booking_agent,
            args=(cid, shared_seats, core_states, lock,
                  phase_def["bookings_each"], log_queue, event_queue),
            daemon=True, name=f"Agent-Core{cid}",
        )
        p.start()
        procs.append(p)
    return procs, lock, t0


def reset_seats(s):
    for i in range(TOTAL_SEATS): s[i] = SEAT_AVAILABLE

def all_done(p):
    return all(not x.is_alive() for x in p)

def kill_all(p):
    for x in p:
        if x.is_alive(): x.terminate(); x.join(timeout=1)


# ═════════════════════════════════════════════════════════════════════════════
#  HUMAN CLICK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

def handle_human_book(seat_idx, shared_seats, lock, phase_locked):
    if seat_idx < 0 or seat_idx >= TOTAL_SEATS:
        return False, "Invalid seat."
    if phase_locked and lock is not None:
        lock.acquire()
        try:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ Seat S-{seat_idx+1:02d} confirmed!"
            else:
                return False, f"✗ S-{seat_idx+1:02d} was just taken!"
        finally:
            lock.release()
    else:
        read_val = shared_seats[seat_idx]
        time.sleep(0.008)
        if read_val == SEAT_AVAILABLE:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ S-{seat_idx+1:02d} booked (chaos!)."
            else:
                shared_seats[seat_idx] = SEAT_COLLISION
                return False, f"✗ COLLISION on S-{seat_idx+1:02d}!"
        else:
            return False, f"✗ S-{seat_idx+1:02d} unavailable."


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _rr(srf, col, rect, r=8, bw=0, bc=None):
    import pygame
    pygame.draw.rect(srf, col, rect, border_radius=r)
    if bw and bc:
        pygame.draw.rect(srf, bc, rect, bw, border_radius=r)


def draw_train_header(surface, fonts, phase_def, status, phase_idx):
    import pygame
    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, HEADER_H))

    surface.blit(
        fonts["title"].render("INDIAN RAILWAYS  —  Reservation Portal", True, COLOR_TEXT_LIGHT),
        (20, 6))
    surface.blit(
        fonts["small"].render(
            "Train: 12951 Mumbai Rajdhani Express  ·  Coach: A1 (AC Chair Car)",
            True, (148, 162, 210)),
        (20, 32))
    surface.blit(
        fonts["tiny"].render(
            "Departure: 06:00 AM  ·  Platform: 3  ·  PNR: 284-7921083",
            True, (120, 135, 185)),
        (20, 50))

    pip_x = 20
    for i, pd_i in enumerate(PHASE_DEFS):
        done    = i < phase_idx
        current = i == phase_idx
        c = (70, 210, 105) if done else (240, 120, 40) if current else (55, 62, 90)
        pygame.draw.circle(surface, c, (pip_x + 7, 72), 7)
        pygame.draw.circle(surface, (255, 255, 255), (pip_x + 7, 72), 7, 2)
        surface.blit(fonts["tiny"].render(f"P{pd_i['num']}", True, COLOR_TEXT_LIGHT),
                     (pip_x, 82))
        pip_x += 48

    bs = fonts["badge"].render(phase_def["label"], True, COLOR_TEXT_LIGHT)
    br = bs.get_rect(right=MAIN_W - 16, centery=18)
    pygame.draw.rect(surface, phase_def["badge_color"], br.inflate(18, 10), border_radius=6)
    surface.blit(bs, br)

    surface.blit(fonts["tiny"].render(phase_def["detail"], True, (140, 155, 205)),
                 (20 + 48*4 + 10, 70))
    ss = fonts["status"].render(status, True, (170, 185, 225))
    surface.blit(ss, (MAIN_W - ss.get_width() - 16, 50))
    surface.blit(
        fonts["tiny"].render("Click any OPEN seat to book", True, (130, 148, 208)),
        (20, 94))
    pygame.draw.line(surface, (40, 52, 85), (0, HEADER_H - 1), (WIN_W, HEADER_H - 1), 2)


def draw_legend(surface, fonts, top_y, active_cores):
    import pygame
    pygame.draw.rect(surface, COLOR_PANEL_BG, (0, top_y, WIN_W, LEGEND_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)
    x = 16
    surface.blit(fonts["tiny"].render("Legend:", True, COLOR_TEXT_MID), (x, top_y + 11))
    x += 56
    for cid in range(1, active_cores + 1):
        pygame.draw.rect(surface, CORE_COLORS[cid], (x, top_y+11, 10, 10), border_radius=2)
        surface.blit(fonts["tiny"].render(f"C{cid}", True, COLOR_TEXT_DARK), (x+14, top_y+10))
        x += 48
    pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], (x, top_y+11, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("You", True, COLOR_TEXT_DARK), (x+14, top_y+10))
    x += 42
    pygame.draw.rect(surface, COLOR_COLLISION, (x, top_y+11, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Collision", True, COLOR_TEXT_DARK), (x+14, top_y+10))
    x += 72
    pygame.draw.rect(surface, (220,224,236), (x, top_y+11, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Open", True, COLOR_TEXT_DARK), (x+14, top_y+10))


# ─────────────────────── TRAIN CAR ────────────────────────────────────────────

def draw_train_car(surface, fonts, shared_seats, hover_seat, glitch_seats, now):
    import pygame
    coach = pygame.Rect(GRID_MARGIN - 2, GRID_TOP - 5,
                        MAIN_W - 2*GRID_MARGIN + 4, GRID_H + 10)
    pygame.draw.rect(surface, (235, 238, 248), coach, border_radius=10)
    pygame.draw.rect(surface, (165, 170, 192), coach, 2, border_radius=10)

    aisle_x = _LEFT_X + 5 * CELL_W + 2
    aisle_w = AISLE_W - 4
    pygame.draw.rect(surface, (228, 230, 240),
                     pygame.Rect(aisle_x, GRID_TOP, aisle_w, GRID_H))
    cx = aisle_x + aisle_w // 2
    for yy in range(GRID_TOP + 5, GRID_TOP + GRID_H - 5, 12):
        pygame.draw.line(surface, (195, 200, 218), (cx, yy), (cx, yy + 6), 1)
    al = fonts["tiny"].render("AISLE", True, (165, 170, 195))
    al_r = pygame.transform.rotate(al, 90)
    surface.blit(al_r, (cx - al_r.get_width()//2,
                         GRID_TOP + GRID_H//2 - al_r.get_height()//2))

    for r in range(SEAT_ROWS):
        wy = GRID_TOP + r * CELL_H + CELL_H // 2
        pygame.draw.rect(surface, (135,180,235), (GRID_MARGIN, wy-4, 4, 8), border_radius=2)
        pygame.draw.rect(surface, (135,180,235), (MAIN_W-GRID_MARGIN-4, wy-4, 4, 8), border_radius=2)

    for r in range(SEAT_ROWS):
        ry = GRID_TOP + r * CELL_H + CELL_H // 2
        rl = fonts["tiny"].render(f"R{r+1}", True, (148, 152, 170))
        surface.blit(rl, (GRID_MARGIN + 4, ry - rl.get_height()//2))

    for i in range(TOTAL_SEATS):
        sx, sy = seat_xy(i)
        # Check for glitch effect
        glitch_info = glitch_seats.get(i)
        _draw_train_seat(surface, fonts, sx, sy, shared_seats[i], i,
                         is_hover=(i == hover_seat),
                         glitch=glitch_info, now=now)


def _draw_train_seat(surface, fonts, cx, cy, val, idx,
                     is_hover=False, glitch=None, now=0.0):
    import pygame
    p  = SEAT_PAD
    rx, ry = cx + p, cy + p
    rw, rh = CELL_W - 2*p, CELL_H - 2*p
    rect = pygame.Rect(rx, ry, rw, rh)

    # ── Glitch flicker override (Phase 4 collisions) ─────────────────────
    use_glitch = False
    if glitch is not None:
        deadline, c1, c2 = glitch
        if now < deadline:
            use_glitch = True
            # Rapid flicker at ~10Hz between the two colliding core colours
            flicker = (int(now * 10) % 2) == 0
            base = c1 if flicker else c2
            back = tuple(max(0, v - 35) for v in base)
            tc   = COLOR_TEXT_LIGHT
            brd  = tuple(max(0, v - 55) for v in base)

    if not use_glitch:
        if val == SEAT_AVAILABLE:
            base, back, tc, brd = (226,230,244), (210,215,232), COLOR_TEXT_MID, (178,183,202)
        elif val == SEAT_COLLISION:
            base, back = COLOR_COLLISION, (178,26,26)
            tc, brd = COLOR_COLLISION_TXT, (148,18,18)
        elif val == HUMAN_ID:
            base, back = (218,180,18), (182,150,12)
            tc, brd = COLOR_TEXT_DARK, (148,122,6)
        else:
            base = CORE_COLORS.get(val, (200,200,200))
            back = tuple(max(0, c-32) for c in base)
            tc, brd = COLOR_TEXT_LIGHT, tuple(max(0, c-55) for c in base)

    pygame.draw.rect(surface, (188,192,206), rect.move(2, 2), border_radius=7)
    bk_h = int(rh * 0.38)
    pygame.draw.rect(surface, back, pygame.Rect(rx, ry, rw, bk_h+5), border_radius=7)
    pygame.draw.rect(surface, back, pygame.Rect(rx, ry+bk_h-2, rw, 7))
    pygame.draw.rect(surface, base, pygame.Rect(rx, ry+bk_h, rw, rh-bk_h), border_radius=7)
    pygame.draw.rect(surface, base, pygame.Rect(rx, ry+bk_h, rw, 7))
    pygame.draw.rect(surface, brd, rect, 2, border_radius=7)
    pygame.draw.line(surface, brd, (rx+3, ry+bk_h), (rx+rw-3, ry+bk_h), 1)
    arm_y = ry + bk_h - 3
    pygame.draw.rect(surface, brd, (rx-1, arm_y, 3, 7), border_radius=1)
    pygame.draw.rect(surface, brd, (rx+rw-2, arm_y, 3, 7), border_radius=1)

    ns = fonts["seat_num"].render(f"S-{idx+1:02d}", True, tc)
    surface.blit(ns, ns.get_rect(centerx=rect.centerx, centery=ry + bk_h//2))

    if use_glitch:
        st = "CORRUPT"
    elif val == SEAT_AVAILABLE:
        st = "OPEN"
    elif val == SEAT_COLLISION:
        st = "ERR!"
    elif val == HUMAN_ID:
        st = "YOU"
    else:
        st = f"C-{val}"
    ss = fonts["seat_state"].render(st, True, tc)
    surface.blit(ss, ss.get_rect(centerx=rect.centerx, centery=ry+bk_h+(rh-bk_h)//2))

    if is_hover and val == SEAT_AVAILABLE:
        pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], rect.inflate(4, 4), 3, border_radius=9)


# ─────────────────────── STATS BAR ────────────────────────────────────────────

def draw_stats_bar(surface, fonts, shared_seats, top_y, elapsed, throughput):
    import pygame
    cpu   = sum(1 for i in range(TOTAL_SEATS) if 1 <= shared_seats[i] <= 4)
    human = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == HUMAN_ID)
    colls = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION)
    avail = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_AVAILABLE)

    pygame.draw.rect(surface, (232, 235, 246), (0, top_y, MAIN_W, STATS_BAR_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (MAIN_W, top_y), 1)
    items = [
        (f"CPU:{cpu}", COLOR_ACCENT, 14),
        (f"You:{human}", CORE_COLORS[HUMAN_ID], 100),
        (f"✗ Coll:{colls}", COLOR_COLLISION, 190),
        (f"Open:{avail}", COLOR_TEXT_MID, 320),
        (f"⏱{elapsed:.1f}s", COLOR_TEXT_DARK, 430),
        (f"⚡{throughput:.1f} bk/s", (22, 158, 70), 530),
    ]
    for text, color, xp in items:
        surface.blit(fonts["stat"].render(text, True, color), (xp, top_y + 8))


# ─────────────────────── EVENT LOG ────────────────────────────────────────────

def draw_log(surface, fonts, log_lines, top_y):
    import pygame
    pygame.draw.rect(surface, (226, 230, 244), (0, top_y, MAIN_W, LOG_H))
    pygame.draw.line(surface, (196, 200, 218), (0, top_y), (MAIN_W, top_y), 1)
    surface.blit(fonts["tiny"].render("Event Log:", True, COLOR_TEXT_MID), (14, top_y + 5))
    for j, line in enumerate(log_lines[-5:]):
        if "COLLISION" in line or "✗" in line:
            c = COLOR_COLLISION
        elif "HUMAN" in line or "YOU" in line:
            c = CORE_COLORS[HUMAN_ID]
        else:
            c = COLOR_TEXT_DARK
        surface.blit(fonts["log"].render(line[:110], True, c), (14, top_y + 22 + j * 16))


# ═════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — HARDWARE THREAD ACTIVITY MONITOR
# ═════════════════════════════════════════════════════════════════════════════

def draw_sidebar(surface, fonts, core_states, active_cores, phase_locked,
                 throughput, now):
    """
    Right sidebar panel showing:
      1. CPU Core status icons (IDLE / WAITING / WORKING)
      2. Mutex Gate with waiting queue
      3. Live throughput gauge bar
    """
    import pygame

    sx = MAIN_W
    sw = SIDEBAR_W

    # Sidebar background
    pygame.draw.rect(surface, (238, 241, 252), (sx, 0, sw, WIN_H))
    pygame.draw.line(surface, (185, 190, 210), (sx, 0), (sx, WIN_H), 2)

    # Title
    pygame.draw.rect(surface, COLOR_HEADER_BG, (sx, 0, sw, HEADER_H))
    surface.blit(fonts["badge"].render("Hardware Thread", True, COLOR_TEXT_LIGHT),
                 (sx + 14, 10))
    surface.blit(fonts["badge"].render("Activity Monitor", True, COLOR_TEXT_LIGHT),
                 (sx + 14, 30))
    surface.blit(fonts["tiny"].render("Real-time core states", True, (130, 145, 200)),
                 (sx + 14, 54))

    # ── Core status cards ─────────────────────────────────────────────────
    cy = HEADER_H + 12
    surface.blit(fonts["badge"].render("CPU Cores", True, COLOR_TEXT_DARK),
                 (sx + 14, cy))
    cy += 22

    state_labels = {CORE_IDLE: "IDLE", CORE_WAITING: "WAITING", CORE_WORKING: "WORKING"}
    state_colors = {CORE_IDLE: COLOR_CORE_IDLE, CORE_WAITING: COLOR_CORE_WAITING,
                    CORE_WORKING: COLOR_CORE_WORKING}

    for cid in range(1, 5):
        active = cid <= active_cores
        cs = core_states[cid - 1] if active else CORE_IDLE

        card = pygame.Rect(sx + 10, cy, sw - 20, 44)
        pygame.draw.rect(surface, COLOR_PANEL_BG, card, border_radius=8)
        pygame.draw.rect(surface, COLOR_DIVIDER, card, 1, border_radius=8)

        if not active:
            # Inactive core — grayed out
            pygame.draw.rect(surface, (210, 215, 225),
                             pygame.Rect(sx+10, cy, 6, 44), border_radius=4)
            surface.blit(fonts["small"].render(f"Core {cid}", True, (175, 180, 195)),
                         (sx + 22, cy + 4))
            surface.blit(fonts["tiny"].render("OFFLINE", True, (175, 180, 195)),
                         (sx + 22, cy + 24))
        else:
            sc = state_colors[cs]
            cc = CORE_COLORS[cid]
            # Left accent bar
            pygame.draw.rect(surface, cc,
                             pygame.Rect(sx+10, cy, 6, 44), border_radius=4)

            # Core name
            surface.blit(fonts["small"].render(f"Core {cid}", True, COLOR_TEXT_DARK),
                         (sx + 22, cy + 4))

            # Status indicator (animated pulse for WORKING)
            ind_x = sx + sw - 80
            pulse = 1.0
            if cs == CORE_WORKING:
                pulse = 0.7 + 0.3 * math.sin(now * 8)
            r = int(6 * pulse)
            pygame.draw.circle(surface, sc, (ind_x, cy + 14), r)

            # Status text
            surface.blit(fonts["tiny"].render(state_labels[cs], True, sc),
                         (ind_x + 10, cy + 8))

            # Tiny utilisation bar
            bar_w = sw - 40
            bar_h = 6
            bar_x = sx + 22
            bar_y = cy + 30
            pygame.draw.rect(surface, (215, 218, 230),
                             pygame.Rect(bar_x, bar_y, bar_w, bar_h), border_radius=3)
            fill_frac = {CORE_IDLE: 0.0, CORE_WAITING: 0.5, CORE_WORKING: 1.0}[cs]
            if fill_frac > 0:
                pygame.draw.rect(surface, sc,
                                 pygame.Rect(bar_x, bar_y, int(bar_w * fill_frac), bar_h),
                                 border_radius=3)

        cy += 50

    # ── Mutex Gate Visualization ──────────────────────────────────────────
    cy += 8
    surface.blit(fonts["badge"].render("Mutex Gate", True, COLOR_TEXT_DARK),
                 (sx + 14, cy))
    cy += 22

    gate_rect = pygame.Rect(sx + 14, cy, sw - 28, 60)
    pygame.draw.rect(surface, (248, 249, 255), gate_rect, border_radius=8)
    pygame.draw.rect(surface, (165, 170, 192), gate_rect, 2, border_radius=8)

    if not phase_locked:
        # No lock in this phase
        surface.blit(fonts["small"].render("NO LOCK", True, COLOR_COLLISION),
                     (sx + 28, cy + 10))
        surface.blit(fonts["tiny"].render("All threads write", True, (180, 90, 20)),
                     (sx + 28, cy + 30))
        surface.blit(fonts["tiny"].render("freely — DATA RACE!", True, (180, 90, 20)),
                     (sx + 28, cy + 44))
    else:
        # Gate icon
        gate_x = sx + 22
        gate_y_mid = cy + 30
        # Gate graphic: two vertical bars with a line between them
        pygame.draw.rect(surface, (80, 90, 120),
                         (gate_x, cy + 8, 6, 44), border_radius=2)
        pygame.draw.rect(surface, (80, 90, 120),
                         (gate_x + 22, cy + 8, 6, 44), border_radius=2)

        # Check which cores are waiting or working
        waiting_ids = []
        holder_id = None
        for cid in range(1, active_cores + 1):
            cs = core_states[cid - 1]
            if cs == CORE_WAITING:
                waiting_ids.append(cid)
            elif cs == CORE_WORKING:
                holder_id = cid

        # Gate bar (green if open, red if held)
        if holder_id is not None:
            bar_col = COLOR_COLLISION
            # Horizontal bar (closed gate)
            pygame.draw.rect(surface, bar_col,
                             (gate_x + 6, gate_y_mid - 2, 22, 4), border_radius=2)
            surface.blit(fonts["tiny"].render(f"Held: C{holder_id}", True, bar_col),
                         (gate_x + 36, cy + 8))
        else:
            bar_col = (30, 185, 72)
            # Gap (open gate)
            surface.blit(fonts["tiny"].render("Open", True, bar_col),
                         (gate_x + 36, cy + 8))

        # Waiting queue
        if waiting_ids:
            wt = "Queue: " + ", ".join(f"C{w}" for w in waiting_ids)
            surface.blit(fonts["tiny"].render(wt, True, COLOR_CORE_WAITING),
                         (gate_x + 36, cy + 26))
        else:
            surface.blit(fonts["tiny"].render("Queue: empty", True, (165, 170, 190)),
                         (gate_x + 36, cy + 26))

        # Throughput through gate (pass-through count hint)
        surface.blit(fonts["tiny"].render(f"Pass-thru: {throughput:.1f}/s", True, (100,110,140)),
                     (gate_x + 36, cy + 42))

    cy += 68

    # ── Throughput Gauge ──────────────────────────────────────────────────
    cy += 8
    surface.blit(fonts["badge"].render("Throughput", True, COLOR_TEXT_DARK),
                 (sx + 14, cy))
    cy += 22

    gauge_rect = pygame.Rect(sx + 14, cy, sw - 28, 52)
    pygame.draw.rect(surface, (248, 249, 255), gauge_rect, border_radius=8)
    pygame.draw.rect(surface, COLOR_DIVIDER, gauge_rect, 1, border_radius=8)

    # Big number
    tp_str = f"{throughput:.1f}"
    tp_surf = fonts["overlay"].render(tp_str, True, (22, 158, 70))
    surface.blit(tp_surf, (sx + 24, cy + 4))
    surface.blit(fonts["tiny"].render("bookings/sec", True, COLOR_TEXT_MID),
                 (sx + 24 + tp_surf.get_width() + 6, cy + 14))

    # Gauge bar
    bar_x = sx + 18
    bar_y = cy + 38
    bar_w = sw - 36
    bar_h = 8
    pygame.draw.rect(surface, (215, 218, 230),
                     pygame.Rect(bar_x, bar_y, bar_w, bar_h), border_radius=4)
    max_throughput = 20.0
    fill = min(1.0, throughput / max_throughput)
    if fill > 0:
        gc = (22, 158, 70) if fill < 0.7 else (230, 190, 20) if fill < 0.9 else COLOR_COLLISION
        pygame.draw.rect(surface, gc,
                         pygame.Rect(bar_x, bar_y, int(bar_w * fill), bar_h),
                         border_radius=4)


# ═════════════════════════════════════════════════════════════════════════════
#  DATA PACKET ANIMATION
# ═════════════════════════════════════════════════════════════════════════════

def draw_data_packets(surface, packets, now):
    """
    Draw animated lines flying from the sidebar core hub position to the
    target seat position.  `packets` is a list of (core_id, seat_idx, start_time).
    """
    import pygame

    alive = []
    for (cid, seat_idx, t0) in packets:
        elapsed = now - t0
        if elapsed > PACKET_DURATION:
            continue
        alive.append((cid, seat_idx, t0))

        progress = elapsed / PACKET_DURATION
        # Ease-out cubic
        progress = 1 - (1 - progress) ** 3

        # Source: sidebar core card centre
        src_x = MAIN_W + SIDEBAR_W // 2
        src_y = HEADER_H + 12 + 22 + (cid - 1) * 50 + 22
        if cid == HUMAN_ID:
            src_x = MAIN_W // 2
            src_y = WIN_H // 2

        # Destination: seat centre
        dst_x, dst_y = seat_center(seat_idx)

        # Current position
        cur_x = int(src_x + (dst_x - src_x) * progress)
        cur_y = int(src_y + (dst_y - src_y) * progress)

        color = CORE_COLORS.get(cid, (200, 200, 200))

        # Trail line (fading)
        trail_progress = max(0, progress - 0.2)
        trail_x = int(src_x + (dst_x - src_x) * trail_progress)
        trail_y = int(src_y + (dst_y - src_y) * trail_progress)
        pygame.draw.line(surface, color, (trail_x, trail_y), (cur_x, cur_y), 2)

        # Packet head (bright dot)
        pygame.draw.circle(surface, color, (cur_x, cur_y), 5)
        pygame.draw.circle(surface, (255, 255, 255), (cur_x, cur_y), 3)

    # Return alive packets (caller should update list)
    return alive


# ─────────────────────── CONFIRMATION MODAL ───────────────────────────────────

def draw_booking_modal(surface, fonts, seat_idx, shared_seats, phase_locked):
    import pygame
    ov = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
    ov.fill((8, 12, 30, 130))
    surface.blit(ov, (0, 0))

    mr = pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, MODAL_H)
    pygame.draw.rect(surface, (25, 30, 50), mr.move(5, 5), border_radius=14)
    pygame.draw.rect(surface, COLOR_PANEL_BG, mr, border_radius=14)
    pygame.draw.rect(surface, COLOR_ACCENT, mr, 2, border_radius=14)

    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, 44), border_radius=14)
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y + 32, MODAL_W, 14))
    t = fonts["badge"].render("Confirm Ticket Booking", True, COLOR_TEXT_LIGHT)
    surface.blit(t, (MODAL_X + MODAL_W//2 - t.get_width()//2, MODAL_Y + 12))

    seat_val = shared_seats[seat_idx]
    surface.blit(
        fonts["stat"].render(f"Seat  S-{seat_idx+1:02d}", True, COLOR_ACCENT),
        (MODAL_X + 28, MODAL_Y + 60))

    if seat_val == SEAT_AVAILABLE:
        st_text, st_col = "AVAILABLE", (20, 155, 68)
    elif seat_val == SEAT_COLLISION:
        st_text, st_col = "CORRUPTED", COLOR_COLLISION
    elif seat_val == HUMAN_ID:
        st_text, st_col = "YOURS", CORE_COLORS[HUMAN_ID]
    elif 1 <= seat_val <= 4:
        st_text, st_col = f"TAKEN by Core {seat_val}!", COLOR_COLLISION
    else:
        st_text, st_col = "UNAVAILABLE", COLOR_TEXT_MID
    surface.blit(fonts["small"].render(f"Status: {st_text}", True, st_col),
                 (MODAL_X + 28, MODAL_Y + 86))

    lt = "Mutex protects this booking." if phase_locked else "⚠ NO LOCK — race possible!"
    lc = (20, 130, 70) if phase_locked else (200, 100, 15)
    surface.blit(fonts["tiny"].render(lt, True, lc), (MODAL_X+28, MODAL_Y+112))
    surface.blit(fonts["tiny"].render("⚡ Workers are still booking!", True, (180, 80, 20)),
                 (MODAL_X + 28, MODAL_Y + 132))

    yr = pygame.Rect(*_YES)
    pygame.draw.rect(surface, (22, 158, 70), yr, border_radius=8)
    yt = fonts["badge"].render("✓  CONFIRM", True, (255, 255, 255))
    surface.blit(yt, yt.get_rect(center=yr.center))

    cr = pygame.Rect(*_CANC)
    pygame.draw.rect(surface, (155, 160, 178), cr, border_radius=8)
    ct = fonts["badge"].render("✗  CANCEL", True, (255, 255, 255))
    surface.blit(ct, ct.get_rect(center=cr.center))


def draw_toast(surface, fonts, text, color):
    import pygame
    ts = fonts["small"].render(text, True, (255, 255, 255))
    tw = ts.get_width() + 36
    tx = (MAIN_W - tw) // 2
    ty = HEADER_H + 3
    pygame.draw.rect(surface, (0,0,0), pygame.Rect(tx+2, ty+2, tw, 34), border_radius=10)
    pygame.draw.rect(surface, color, pygame.Rect(tx, ty, tw, 34), border_radius=10)
    pygame.draw.rect(surface, (255,255,255), pygame.Rect(tx, ty, tw, 34), 2, border_radius=10)
    surface.blit(ts, (tx + 18, ty + 7))


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — TELEMETRY  (uses full WIN_W for graph)
# ═════════════════════════════════════════════════════════════════════════════

def draw_telemetry(surface, fonts, phase_times, phase4_collisions, human_total):
    import pygame
    surface.fill(COLOR_BG)

    t1 = max(phase_times.get(1, 1.0), 0.001)
    t2 = max(phase_times.get(2, t1),  0.001)
    t4 = max(phase_times.get(3, t1),  0.001)
    s2, s4 = t1/t2, t1/t4
    eff4 = s4 / 4.0
    sf = max(0.0, ((1.0/s4) - 0.25) / 0.75)

    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, 80))
    surface.blit(fonts["title"].render(
        "Phase 5  ·  Performance Telemetry  &  Amdahl's Law", True, COLOR_TEXT_LIGHT), (24, 12))
    surface.blit(fonts["small"].render(
        "Empirical speedup from Phases 1–3  ·  Pure pygame, zero external deps",
        True, (145, 158, 210)), (24, 48))

    fy = WIN_H - 32
    pygame.draw.rect(surface, (228, 232, 246), (0, fy, WIN_W, 32))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, fy), (WIN_W, fy), 1)
    surface.blit(fonts["small"].render("Simulation complete  ·  Press ESC to exit",
                 True, COLOR_TEXT_MID), (24, fy + 8))

    cy, ch = 88, fy - 88

    PW, PX, gap = 272, 16, 7
    cards = [
        ("T₁  —  1 Core",  f"{t1:.3f} s", "Serial baseline (P1)",  CORE_COLORS[1]),
        ("T₂  —  2 Cores", f"{t2:.3f} s", "2-core parallel (P2)",  CORE_COLORS[2]),
        ("T₄  —  4 Cores", f"{t4:.3f} s", "4-core parallel (P3)",  CORE_COLORS[4]),
        ("S₂  Speedup",    f"{s2:.3f}×",  "T₁÷T₂ (ideal=2×)",     CORE_COLORS[2]),
        ("S₄  Speedup",    f"{s4:.3f}×",  "T₁÷T₄ (ideal=4×)",     CORE_COLORS[4]),
        ("Efficiency",     f"{eff4*100:.1f}%", "S₄÷4 (100%=perf)", (98, 60, 220)),
        ("Serial Frac.",   f"{sf*100:.1f}%",   "Amdahl bottleneck", COLOR_COLLISION),
        ("P4 Collisions",  str(phase4_collisions), "Corruptions P4", (200, 50, 30)),
        ("Human Bookings", str(human_total), "User clicks total", CORE_COLORS[HUMAN_ID]),
    ]
    card_h = min(62, max(54, (ch - gap*(len(cards)-1)) // len(cards)))
    for i, (lbl, val, sub, acc) in enumerate(cards):
        ccy = cy + i * (card_h + gap)
        cr = pygame.Rect(PX, ccy, PW, card_h)
        pygame.draw.rect(surface, COLOR_PANEL_BG, cr, border_radius=9)
        pygame.draw.rect(surface, COLOR_DIVIDER, cr, 1, border_radius=9)
        pygame.draw.rect(surface, acc, pygame.Rect(PX, ccy, 5, card_h), border_radius=4)
        surface.blit(fonts["tiny"].render(lbl, True, COLOR_TEXT_MID), (PX+14, ccy+3))
        surface.blit(fonts["stat"].render(val, True, acc), (PX+14, ccy+17))
        surface.blit(fonts["tiny"].render(sub, True, (165,170,192)), (PX+14, ccy+card_h-15))

    GX = PX + PW + 14
    GW = WIN_W - GX - 14
    GY, GH = cy, ch
    gcr = pygame.Rect(GX, GY, GW, GH)
    pygame.draw.rect(surface, COLOR_GRAPH_BG, gcr, border_radius=12)
    pygame.draw.rect(surface, COLOR_DIVIDER, gcr, 1, border_radius=12)
    surface.blit(fonts["badge"].render("Speedup vs. Physical Cores", True, COLOR_TEXT_DARK),
                 (GX+18, GY+14))

    ML, MR, MT, MB = 72, 30, 52, 62
    px0 = GX+ML; py0 = GY+MT; pw = GW-ML-MR; ph = GH-MT-MB; YMAX = 5.0
    x_cores = [1, 2, 4]
    y_ideal = [1.0, 2.0, 4.0]
    y_actual = [1.0, s2, s4]
    def to_px(cv, sv):
        return int(px0+(cv-1)/3.0*pw), int(py0+ph-sv/YMAX*ph)

    for yt in range(6):
        _, gy = to_px(1, yt)
        pygame.draw.line(surface, COLOR_GRID_LINE, (px0, gy), (px0+pw, gy), 1)
        tl = fonts["axis"].render(f"{yt}×", True, COLOR_AXIS)
        surface.blit(tl, (px0-tl.get_width()-8, gy-tl.get_height()//2))
    for xc in x_cores:
        gx, _ = to_px(xc, 0)
        pygame.draw.line(surface, COLOR_GRID_LINE, (gx, py0), (gx, py0+ph), 1)
        tl = fonts["axis"].render(str(xc), True, COLOR_AXIS)
        surface.blit(tl, (gx-tl.get_width()//2, py0+ph+10))

    pygame.draw.line(surface, COLOR_AXIS, (px0, py0), (px0, py0+ph), 2)
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0+ph), (px0+pw, py0+ph), 2)
    xl = fonts["small"].render("Number of Physical Cores", True, COLOR_AXIS)
    surface.blit(xl, (px0+pw//2-xl.get_width()//2, py0+ph+36))
    ylr = pygame.transform.rotate(
        fonts["small"].render("Speedup ( S = T₁/Tₙ )", True, COLOR_AXIS), 90)
    surface.blit(ylr, (GX+6, py0+ph//2-ylr.get_height()//2))

    ipts = [to_px(c, y) for c, y in zip(x_cores, y_ideal)]
    _draw_dashed_line(surface, COLOR_IDEAL_LINE, ipts, width=3, dash=14, gap=8)
    for pt in ipts:
        pygame.draw.circle(surface, COLOR_IDEAL_LINE, pt, 8)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, pt, 5)

    apts = [to_px(c, y) for c, y in zip(x_cores, y_actual)]
    if len(apts) > 1:
        pygame.draw.lines(surface, COLOR_ACTUAL_LINE, False, apts, 3)
    for (apx, apy), (_, ya) in zip(apts, zip(x_cores, y_actual)):
        pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (apx, apy), 9)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, (apx, apy), 5)
        tag = fonts["axis"].render(f"{ya:.2f}×", True, COLOR_TEXT_DARK)
        tr = pygame.Rect(apx-tag.get_width()//2-4, apy-34, tag.get_width()+8, tag.get_height()+4)
        pygame.draw.rect(surface, (235,248,240), tr, border_radius=4)
        pygame.draw.rect(surface, COLOR_ACTUAL_LINE, tr, 1, border_radius=4)
        surface.blit(tag, (tr.x+4, tr.y+2))

    lx, ly = px0+pw-218, py0+12
    pygame.draw.rect(surface, (242,245,255), pygame.Rect(lx-8, ly-6, 212, 56), border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER, pygame.Rect(lx-8, ly-6, 212, 56), 1, border_radius=7)
    _draw_dashed_line(surface, COLOR_IDEAL_LINE, [(lx, ly+10), (lx+28, ly+10)], 2, 6, 4)
    pygame.draw.circle(surface, COLOR_IDEAL_LINE, (lx+38, ly+10), 5)
    pygame.draw.circle(surface, (242,245,255), (lx+38, ly+10), 3)
    surface.blit(fonts["axis"].render("Ideal (linear)", True, COLOR_TEXT_DARK), (lx+48, ly+3))
    pygame.draw.line(surface, COLOR_ACTUAL_LINE, (lx, ly+32), (lx+28, ly+32), 2)
    pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (lx+38, ly+32), 5)
    pygame.draw.circle(surface, (242,245,255), (lx+38, ly+32), 3)
    surface.blit(fonts["axis"].render("Actual measured", True, COLOR_TEXT_DARK), (lx+48, ly+25))

    ann = [f"Amdahl's Law:  S(N) = 1/(s+(1-s)/N)",
           f"Serial fraction  s ≈ {sf*100:.1f}%",
           f"Parallel efficiency at 4 cores = {eff4*100:.1f}%"]
    ax_, ay_ = px0+16, py0+ph-68
    pygame.draw.rect(surface, (248,248,255), pygame.Rect(ax_-8, ay_-4, 360, 66), border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER, pygame.Rect(ax_-8, ay_-4, 360, 66), 1, border_radius=7)
    for li, al in enumerate(ann):
        surface.blit(fonts["tiny"].render(al, True,
                     COLOR_TEXT_DARK if li==0 else (90,96,120)), (ax_, ay_+li*20))


def _draw_dashed_line(surface, color, points, width=2, dash=12, gap=6):
    import pygame
    for i in range(len(points) - 1):
        x1, y1 = points[i]; x2, y2 = points[i+1]
        dx, dy = x2-x1, y2-y1; length = math.hypot(dx, dy)
        if length == 0: continue
        ux, uy = dx/length, dy/length
        pos, draw = 0.0, True
        while pos < length:
            seg = dash if draw else gap
            end = min(pos+seg, length)
            if draw:
                pygame.draw.line(surface, color,
                    (int(x1+ux*pos), int(y1+uy*pos)),
                    (int(x1+ux*end), int(y1+uy*end)), width)
            pos += seg; draw = not draw


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "Indian Railways Reservation Simulator  v5  —  Systems Monitor Edition"
    )
    clock = pygame.time.Clock()

    def F(sz, bold=False):
        try:    return pygame.font.SysFont("Segoe UI", sz, bold=bold)
        except: return pygame.font.Font(None, sz)

    fonts = {
        "title"     : F(22, True),
        "badge"     : F(14, True),
        "status"    : F(12),
        "small"     : F(13),
        "seat_num"  : F(11, True),
        "seat_state": F(10),
        "log"       : F(11),
        "stat"      : F(13, True),
        "overlay"   : F(26, True),
        "axis"      : F(12),
        "tiny"      : F(10),
    }

    # ── Shared memory ─────────────────────────────────────────────────────
    shared_seats = multiprocessing.Array('i', TOTAL_SEATS)
    core_states  = multiprocessing.Array('i', 4)   # 4 cores max
    log_queue    = multiprocessing.Queue()
    event_queue  = multiprocessing.Queue()

    # ── Simulator state ───────────────────────────────────────────────────
    state        = "running"
    phase_idx    = 0
    processes    = []
    current_lock = None
    log_lines    = []
    status_text  = ""
    overlay_text = ""

    phase_start   = 0.0
    phase_elapsed = 0.0
    pause_start   = 0.0

    phase_times        = {}
    p4_collisions      = 0
    human_bookings_total = 0

    modal_open     = False
    modal_seat_idx = -1
    toast_text  = ""
    toast_color = (0, 0, 0)
    toast_start = 0.0

    # ── Animation state ───────────────────────────────────────────────────
    data_packets = []                     # [(core_id, seat_idx, start_time), ...]
    glitch_seats = {}                     # {seat_idx: (deadline, color1, color2)}
    booking_timestamps = deque(maxlen=200)  # timestamps of successful bookings
    throughput = 0.0
    last_tp_calc = 0.0

    # ── Launch Phase 1 ────────────────────────────────────────────────────
    reset_seats(shared_seats)
    pd = PHASE_DEFS[phase_idx]
    processes, current_lock, phase_start = launch_phase(
        pd, shared_seats, core_states, log_queue, event_queue
    )
    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
    log_lines.append("  💡 Click any OPEN seat to book!")
    status_text = f"Running {pd['label']}…"

    running = True
    while running:
        now = time.time()

        # ── Events ─────────────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                kill_all(processes); running = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                kill_all(processes); running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx, my = ev.pos
                if modal_open:
                    if _in_rect(mx, my, _YES):
                        ok, msg = handle_human_book(
                            modal_seat_idx, shared_seats, current_lock, pd["locked"])
                        if ok:
                            human_bookings_total += 1
                            toast_color = (22, 155, 68)
                            data_packets.append((HUMAN_ID, modal_seat_idx, now))
                            booking_timestamps.append(now)
                        else:
                            toast_color = COLOR_COLLISION
                        log_lines.append(f"[HUMAN] {msg}")
                        toast_text = msg; toast_start = now; modal_open = False
                    elif _in_rect(mx, my, _CANC):
                        modal_open = False
                        log_lines.append("[HUMAN] Cancelled.")
                elif state == "running":
                    seat_idx = mouse_to_seat(mx, my)
                    if seat_idx >= 0:
                        v = shared_seats[seat_idx]
                        if v == SEAT_AVAILABLE:
                            modal_open = True; modal_seat_idx = seat_idx
                        elif v == HUMAN_ID:
                            toast_text = f"Already yours: S-{seat_idx+1:02d}"
                            toast_color = CORE_COLORS[HUMAN_ID]; toast_start = now
                        elif v == SEAT_COLLISION:
                            toast_text = f"S-{seat_idx+1:02d} corrupted!"
                            toast_color = COLOR_COLLISION; toast_start = now
                        else:
                            toast_text = f"S-{seat_idx+1:02d} taken by C{v}"
                            toast_color = COLOR_TEXT_MID; toast_start = now

        if not running:
            break

        # ── Drain log queue ────────────────────────────────────────────
        while not log_queue.empty():
            try:    log_lines.append(log_queue.get_nowait())
            except: pass

        # ── Drain event queue (animation triggers) ─────────────────────
        while not event_queue.empty():
            try:
                ev_data = event_queue.get_nowait()
                if ev_data[0] == 'BOOK':
                    _, cid, sidx, t = ev_data
                    data_packets.append((cid, sidx, t))
                    booking_timestamps.append(t)
                elif ev_data[0] == 'COLLISION':
                    _, cid, sidx, t = ev_data
                    # Determine the two colliding colours
                    c1 = CORE_COLORS.get(cid, (200, 200, 200))
                    # The other colour: try to guess from current seat value
                    other = shared_seats[sidx]
                    c2 = CORE_COLORS.get(other, COLOR_COLLISION) if other > 0 else COLOR_COLLISION
                    glitch_seats[sidx] = (now + GLITCH_DURATION, c1, c2)
                    data_packets.append((cid, sidx, t))
            except:
                pass

        # ── Throughput calculation ─────────────────────────────────────
        if now - last_tp_calc >= THROUGHPUT_INTERVAL:
            cutoff = now - 2.0
            count = sum(1 for ts in booking_timestamps if ts >= cutoff)
            throughput = count / 2.0
            last_tp_calc = now

        # ── Hover ──────────────────────────────────────────────────────
        if state == "running" and not modal_open:
            hover_seat = mouse_to_seat(*pygame.mouse.get_pos())
        else:
            hover_seat = -1

        # ── Purge expired glitches ─────────────────────────────────────
        expired = [k for k, (dl, _, _) in glitch_seats.items() if now >= dl]
        for k in expired:
            del glitch_seats[k]

        # ── State machine ──────────────────────────────────────────────
        if state == "running":
            phase_elapsed = time.perf_counter() - phase_start
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
                overlay_text = f"Phase {pd['num']} Complete ({phase_elapsed:.2f}s)"
                status_text = (f"Phase {pd['num']} done" +
                    (f"  |  Next in {PHASE_PAUSE_SEC:.0f}s…"
                     if phase_idx+1 < len(PHASE_DEFS) else "  |  Dashboard…"))
                pause_start = time.time(); current_lock = None; state = "done_wait"
                # Reset core states to idle
                for i in range(4): core_states[i] = CORE_IDLE

        elif state == "done_wait":
            if time.time() - pause_start >= PHASE_PAUSE_SEC:
                overlay_text = ""
                nxt = phase_idx + 1
                if nxt < len(PHASE_DEFS):
                    phase_idx = nxt; pd = PHASE_DEFS[phase_idx]
                    reset_seats(shared_seats)
                    log_lines.clear(); data_packets.clear()
                    glitch_seats.clear(); booking_timestamps.clear()
                    throughput = 0.0
                    processes, current_lock, phase_start = launch_phase(
                        pd, shared_seats, core_states, log_queue, event_queue)
                    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
                    log_lines.append("  💡 Click any OPEN seat to book!")
                    status_text = f"Running {pd['label']}…"; state = "running"
                else:
                    state = "telemetry"

        # ── Rendering ──────────────────────────────────────────────────
        if state == "telemetry":
            draw_telemetry(screen, fonts, phase_times, p4_collisions,
                           human_bookings_total)
        else:
            screen.fill(COLOR_BG)
            elapsed_show = phase_elapsed if state == "done_wait" else (
                time.perf_counter() - phase_start)

            draw_train_header(screen, fonts, pd, status_text, phase_idx)
            draw_legend(screen, fonts, HEADER_H, pd["cores"])
            draw_train_car(screen, fonts, shared_seats, hover_seat, glitch_seats, now)
            draw_stats_bar(screen, fonts, shared_seats, STATS_TOP, elapsed_show, throughput)
            draw_log(screen, fonts, log_lines, LOG_TOP)
            draw_sidebar(screen, fonts, core_states, pd["cores"], pd["locked"],
                         throughput, now)

            # Data packet animations (drawn on top of grid)
            data_packets = draw_data_packets(screen, data_packets, now)

            if overlay_text:
                ov_s = fonts["overlay"].render(overlay_text, True, COLOR_TEXT_LIGHT)
                orr = ov_s.get_rect(center=(MAIN_W // 2, WIN_H // 2))
                bg = orr.inflate(50, 24)
                pygame.draw.rect(screen, pd["overlay_color"], bg, border_radius=12)
                screen.blit(ov_s, orr)

            if modal_open:
                draw_booking_modal(screen, fonts, modal_seat_idx,
                                   shared_seats, pd["locked"])

        if toast_text and (now - toast_start < TOAST_DURATION):
            draw_toast(screen, fonts, toast_text, toast_color)

        pygame.display.flip()
        clock.tick(FPS)

    kill_all(processes)
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
