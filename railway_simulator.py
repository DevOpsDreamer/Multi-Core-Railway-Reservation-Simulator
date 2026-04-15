"""
================================================================================
  MULTI-CORE RAILWAY RESERVATION SIMULATOR  —  v6.5  (X-Ray Logic Edition)
  Academic Demonstration: Check-then-Act, Mutex Queuing & Amdahl's Law

  Phase 1 : 1 Core   · Serial Baseline       (mutex, timed → T₁)
  Phase 2 : 2 Cores  · Parallel Mutex        (mutex, timed → T₂)
  Phase 3 : 4 Cores  · Parallel Mutex        (mutex, timed → T₄)
  Phase 4 : 4 Cores  · CHAOS — No Lock       (race conditions)
  Phase 5 : Telemetry  ·  Amdahl's Law Graph (pure pygame)

  v6.5  — "X-RAY LOGIC"  FEATURES:
  ─────────────────────────────────────────────────────────────────────
  1) X-Ray Check-then-Act:
       Workers READ a seat as available, then SLEEP (the "thinking"
       window) before WRITING. The UI pulses the seat YELLOW during
       the sleep.  In Phase 4, multiple bots target the same seat →
       seat shows orange "CHECK×N" → COLLISION flicker → red CORRUPT.

  2) 3rd-AC LHB Bogie Interior:
       50 berths in 7 bays (6 full × 8 + 1 partial × 2).
       Each berth labeled: UB, MB, LB, SL, SU.
       Central aisle separates main berths from side berths.
       Parallax scrolling scenery inside coach wall windows.

  3) Silent Pause (Presenter Mode):
       [P] → entire screen desaturates to 50% grayscale + dim.
       NO text overlay. Only a pulsing AMBER LED ("SYS") in the header
       pierces through the gray as the sole color on screen.
       Parallax stops. Workers block on multiprocessing.Event.

  4) Mutex "Waiting Room" (Sidebar):
       "ACTIVE" box shows the core currently holding the mutex.
       "QUEUE"  box shows cores blocked on lock.acquire().
       In Phase 4: shows "NO LOCK — RACE CONDITION" in red.

  5) Presenter Controls:
       [SPACE]  Manual phase transitions (no auto-advance).
       [P]      Silent Pause (grayscale + amber LED).
       [R]      Reset & restart current phase.
       [S]      Slow-Motion toggle (×2 worker latency).

  6) Naming: CPU workers = "BOT-1..4", Human user = "STUDENT".
  ─────────────────────────────────────────────────────────────────────

  Architecture (Producer-Consumer, unchanged core):
    Main Process (Core 0)   → Pygame UI + state orchestrator
    Worker Processes 1–4    → multiprocessing.Process booking agents
    Shared Memory           → Array('i', 50) seats
                            → Array('i', 4)  core states
    IPC Channels            → Queue × 2 (log + events)
    Synchronization         → Lock (Phases 1-3), Event (pause), Value (speed)
================================================================================
"""

import multiprocessing
import time
import random
import sys
import math
from collections import deque

# ── Windows multiprocessing guard ──────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.freeze_support()

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_SEATS     = 50
TOTAL_BOOKINGS  = 24        # bookings attempted per phase

# ── Timing (governs Amdahl's Law serial fraction) ─────────────────────────
CHECK_DELAY       = 0.08    # X-Ray "thinking" delay (seconds)
PARALLEL_WORK_MIN = 0.08    # parallel fraction (outside lock)
PARALLEL_WORK_MAX = 0.16
RACE_WIN_MIN      = 0.06    # chaos-mode race window
RACE_WIN_MAX      = 0.14

SLOW_MO_FACTOR = 2.0        # speed multiplier when Slow-Mo is active

HUMAN_ID       = 99
TOAST_DURATION = 2.0

SEAT_AVAILABLE =  0
SEAT_COLLISION = -1

# ── Core state codes ──────────────────────────────────────────────────────
CORE_IDLE    = 0
CORE_WAITING = 1    # blocked on lock.acquire()
CORE_WORKING = 2    # inside critical section

# ── Animation timing ──────────────────────────────────────────────────────
GLITCH_DURATION     = 0.35
PACKET_DURATION     = 0.30
THROUGHPUT_INTERVAL = 0.5

# ── Phase definitions ─────────────────────────────────────────────────────
PHASE_DEFS = [
    {"num": 1, "cores": 1, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS,
     "label": "Phase 1  ·  1 Core — Serial Baseline",
     "badge_color": (16, 100, 120), "overlay_color": (16, 100, 120),
     "detail": "Single BOT. Sequential. Establishes T₁."},
    {"num": 2, "cores": 2, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS // 2,
     "label": "Phase 2  ·  2 Cores — Parallel Mutex",
     "badge_color": (20, 140, 80), "overlay_color": (20, 140, 80),
     "detail": "2 BOTs. Mutex serialises critical section."},
    {"num": 3, "cores": 4, "locked": True,  "timed": True,
     "bookings_each": TOTAL_BOOKINGS // 4,
     "label": "Phase 3  ·  4 Cores — Parallel Mutex",
     "badge_color": (88, 55, 200), "overlay_color": (88, 55, 200),
     "detail": "4 BOTs. Max parallelism w/ Mutex. T₄ recorded."},
    {"num": 4, "cores": 4, "locked": False, "timed": False,
     "bookings_each": TOTAL_BOOKINGS // 4,
     "label": "Phase 4  ·  4 Cores — CHAOS (No Lock)",
     "badge_color": (195, 40, 40), "overlay_color": (195, 40, 40),
     "detail": "No mutex. Race conditions corrupt data. RED = BAD."},
]

# ═════════════════════════════════════════════════════════════════════════════
#  3rd-AC BAY STRUCTURE  (6 full bays × 8 + 1 partial × 2 = 50)
# ═════════════════════════════════════════════════════════════════════════════
#
#  Full bay (8 berths):
#    Pos: 0(UB) 1(MB) 2(LB) | partition | 3(LB) 4(MB) 5(UB) | aisle | 6(SL) 7(SU)
#
#  Partial bay 7 (2 berths):                                    aisle | 0(SL) 1(SU)
#
_LABELS_FULL    = ["UB", "MB", "LB", "LB", "MB", "UB", "SL", "SU"]
_LABELS_PARTIAL = ["SL", "SU"]

def berth_label(idx):
    """Return the berth type label for a seat index."""
    if idx >= 48: return _LABELS_PARTIAL[idx - 48]
    return _LABELS_FULL[idx % 8]

def seat_bay(idx):
    """Return the bay number (0–6) for a seat index."""
    if idx >= 48: return 6
    return idx // 8


# ═════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE  — Indian Railways LHB 3AC (Teal / Navy / Gray)
# ═════════════════════════════════════════════════════════════════════════════

COLOR_BG            = (232, 240, 245)
COLOR_PANEL_BG      = (255, 255, 255)
COLOR_HEADER_BG     = (10,  42,  58)
COLOR_TEXT_DARK      = (12,  20,  30)
COLOR_TEXT_LIGHT     = (225, 242, 255)
COLOR_TEXT_MID       = (65,  85, 108)
COLOR_ACCENT         = (0,  142, 130)
COLOR_DIVIDER        = (175, 195, 212)

COLOR_COACH_BODY     = (216, 226, 235)
COLOR_COACH_STRIPE   = (0,  118, 128)
COLOR_PARTITION      = (130, 145, 165)
COLOR_AISLE_BG       = (200, 210, 222)

COLOR_COLLISION      = (200, 36,  36)
COLOR_COLLISION_TXT  = (255, 255, 255)
COLOR_THINKING       = (255, 220,  30)     # yellow pulse for single-core thinking
COLOR_THINK_DANGER   = (255, 120,  15)     # orange for multi-core race

COLOR_SKY     = (135, 195, 230)
COLOR_GROUND  = ( 85, 155,  78)
COLOR_POLE    = ( 75,  58,  42)

CORE_COLORS = {
    1:        ( 30,  95, 210),      # BOT-1  Royal Blue
    2:        ( 18, 155,  70),      # BOT-2  Emerald Green
    3:        (110,  50, 220),      # BOT-3  Violet
    4:        (215,  78,  10),      # BOT-4  Burnt Orange
    HUMAN_ID: (195, 155,   0),      # STUDENT  Gold
}

COLOR_CORE_IDLE    = (150, 158, 175)
COLOR_CORE_WAITING = (220, 180,  18)
COLOR_CORE_WORKING = ( 28, 178,  68)

# ── Telemetry graph colours ───────────────────────────────────────────────
COLOR_GRAPH_BG     = (250, 252, 255)
COLOR_GRID_LINE    = (200, 210, 228)
COLOR_AXIS         = (38,  45,  65)
COLOR_IDEAL_LINE   = (30,  95, 210)
COLOR_ACTUAL_LINE  = (18, 155,  70)


# ═════════════════════════════════════════════════════════════════════════════
#  LAYOUT GEOMETRY  — 1280 × 720  (16:9 projector-safe)
# ═════════════════════════════════════════════════════════════════════════════

SIDEBAR_W  = 218
WIN_W      = 1280
WIN_H      = 720
MAIN_W     = WIN_W - SIDEBAR_W       # 1062
FPS        = 30

HEADER_H    = 88
LEGEND_H    = 28
CTRL_BAR_H  = 28

# Bay / Berth cell dimensions
CELL_W      = 112
CELL_H      = 46
BAY_SEP     = 5
BAY_ROW_H   = CELL_H + BAY_SEP       # 51
TOTAL_BAYS  = 7

GRID_MARGIN  = 14
BAY_LABEL_W  = 32
PART_GAP     = 10       # gap between left & right facing berth groups
AISLE_W      = 26

# Horizontal group positions
_L_GRP_X = GRID_MARGIN + BAY_LABEL_W                       # 46
_R_GRP_X = _L_GRP_X + 3 * CELL_W + PART_GAP                # 392
_AISLE_X = _R_GRP_X + 3 * CELL_W                            # 728
_S_GRP_X = _AISLE_X + AISLE_W                               # 754

# Vertical positions
GRID_TOP    = HEADER_H + LEGEND_H + 2                       # 118
GRID_H      = TOTAL_BAYS * BAY_ROW_H                        # 357
STATS_TOP   = GRID_TOP + GRID_H + 2                         # 477
STATS_BAR_H = 26
LOG_TOP     = STATS_TOP + STATS_BAR_H                       # 503
CTRL_BAR_Y  = WIN_H - CTRL_BAR_H                            # 692
LOG_H       = CTRL_BAR_Y - LOG_TOP                           # 189

# Coach body extents
_COACH_RIGHT = _S_GRP_X + 2 * CELL_W + 12                   # 990

# Modal
MODAL_W, MODAL_H = 380, 230
MODAL_X = (MAIN_W - MODAL_W) // 2
MODAL_Y = (WIN_H  - MODAL_H) // 2
_YES  = (MODAL_X + 30,  MODAL_Y + MODAL_H - 60, 130, 38)
_CANC = (MODAL_X + MODAL_W - 160, MODAL_Y + MODAL_H - 60, 130, 38)

SEAT_PAD = 3


# ═════════════════════════════════════════════════════════════════════════════
#  COORDINATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _in_rect(px, py, r):
    return r[0] <= px < r[0]+r[2] and r[1] <= py < r[1]+r[3]


def seat_xy(idx):
    """Top-left pixel coordinates for berth `idx`."""
    if idx >= 48:
        pos = idx - 48
        return _S_GRP_X + pos * CELL_W, GRID_TOP + 6 * BAY_ROW_H
    bay = idx // 8; pos = idx % 8
    y = GRID_TOP + bay * BAY_ROW_H
    if   pos < 3: x = _L_GRP_X + pos * CELL_W
    elif pos < 6: x = _R_GRP_X + (pos - 3) * CELL_W
    else:         x = _S_GRP_X + (pos - 6) * CELL_W
    return x, y


def seat_center(idx):
    sx, sy = seat_xy(idx)
    return sx + CELL_W // 2, sy + CELL_H // 2


def mouse_to_seat(mx, my):
    """Translate pixel coordinates to seat index, or -1."""
    if my < GRID_TOP or my >= GRID_TOP + GRID_H: return -1
    bay = (my - GRID_TOP) // BAY_ROW_H
    if bay < 0 or bay >= TOTAL_BAYS: return -1
    row_y = GRID_TOP + bay * BAY_ROW_H
    if my > row_y + CELL_H: return -1   # in separator gap

    # Determine horizontal group & position
    pos = -1
    if _L_GRP_X <= mx < _L_GRP_X + 3 * CELL_W:
        pos = (mx - _L_GRP_X) // CELL_W
    elif _R_GRP_X <= mx < _R_GRP_X + 3 * CELL_W:
        pos = 3 + (mx - _R_GRP_X) // CELL_W
    elif _S_GRP_X <= mx < _S_GRP_X + 2 * CELL_W:
        pos = 6 + (mx - _S_GRP_X) // CELL_W
    if pos < 0: return -1

    if bay == 6:   # partial bay — only side berths
        if pos < 6: return -1
        idx = 48 + (pos - 6)
    else:
        idx = bay * 8 + pos

    return idx if 0 <= idx < TOTAL_SEATS else -1


# ═════════════════════════════════════════════════════════════════════════════
#  WORKER PROCESS  — runs on a separate OS core
#
#  X-RAY LOGIC:
#    1. Worker finds an AVAILABLE seat         → "CHECK" complete
#    2. Worker sends THINK_START event
#    3. Worker sleeps (CHECK_DELAY)            → "THINKING" window
#    4. Worker sends THINK_END event
#    5. Worker writes to seat                  → "ACT"
#
#  In Phase 4 (no lock), two+ workers can be in step 2-3 on the SAME
#  seat simultaneously, causing a visible "CHECK×N" warning and then
#  a COLLISION when both try to write.
# ═════════════════════════════════════════════════════════════════════════════

def booking_agent(core_id, shared_seats, core_states, lock, bookings,
                  log_queue, event_queue, run_flag, speed_mult):
    secured = collisions = 0

    for _ in range(bookings):
        run_flag.wait()                              # OS-level pause gate
        sm = max(speed_mult.value, 0.1)

        # ── Mutex acquisition (Phases 1–3) ────────────────────────────
        if lock is not None:
            core_states[core_id - 1] = CORE_WAITING  # sidebar → WAIT
            lock.acquire()
        core_states[core_id - 1] = CORE_WORKING      # sidebar → RUN

        try:
            # Step 1: Linear scan — find first available berth
            target = -1
            for idx in range(TOTAL_SEATS):
                if shared_seats[idx] == SEAT_AVAILABLE:
                    target = idx; break
            if target == -1:
                log_queue.put(f"[BOT-{core_id}] No berths left.")
                break

            # ═══ X-RAY: "CHECK" is complete. Now "THINKING". ═══
            event_queue.put(('THINK_START', core_id, target, time.time()))

            if lock is not None:
                time.sleep(CHECK_DELAY * sm)          # safe — lock held
            else:
                time.sleep(random.uniform(RACE_WIN_MIN, RACE_WIN_MAX) * sm)

            event_queue.put(('THINK_END', core_id, target, time.time()))

            # ═══ X-RAY: "ACT" — attempt the write ═══
            if lock is not None:
                # Mutex guarantees seat is still ours
                shared_seats[target] = core_id
                secured += 1
                event_queue.put(('BOOK', core_id, target, time.time()))
                log_queue.put(f"[BOT-{core_id}] ✓ Booked S-{target+1:02d}")
            else:
                # No lock — must re-check (could have been sniped!)
                if shared_seats[target] == SEAT_AVAILABLE:
                    shared_seats[target] = core_id
                    secured += 1
                    event_queue.put(('BOOK', core_id, target, time.time()))
                    log_queue.put(f"[BOT-{core_id}] ✓ Booked S-{target+1:02d}")
                else:
                    shared_seats[target] = SEAT_COLLISION
                    collisions += 1
                    event_queue.put(('COLLISION', core_id, target, time.time()))
                    log_queue.put(f"[BOT-{core_id}] ✗ COLLISION S-{target+1:02d}")
        finally:
            if lock is not None:
                lock.release()

        core_states[core_id - 1] = CORE_IDLE
        run_flag.wait()   # pause gate between bookings
        time.sleep(random.uniform(PARALLEL_WORK_MIN, PARALLEL_WORK_MAX) * sm)

    core_states[core_id - 1] = CORE_IDLE
    log_queue.put(f"[BOT-{core_id}] Done. OK:{secured} Coll:{collisions}")


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
            daemon=True, name=f"BOT-{cid}")
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
#  HUMAN (STUDENT) BOOKING
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
            return False, f"✗ S-{seat_idx+1:02d} already taken!"
        finally:
            lock.release()
    else:
        rv = shared_seats[seat_idx]
        time.sleep(0.008)
        if rv == SEAT_AVAILABLE:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ S-{seat_idx+1:02d} booked (chaos!)."
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
        "INDIAN RAILWAYS  ·  3AC LHB Coach Reservation", True, COLOR_TEXT_LIGHT),
        (18, 4))
    surface.blit(fonts["small"].render(
        "Train: 12952 Rajdhani  ·  Coach: B1 (3-Tier AC)  ·  LHB Bogie",
        True, (120, 165, 205)), (18, 28))
    surface.blit(fonts["tiny"].render(
        "Dep: 16:55  ·  Platform 1  ·  PNR: 482-3917205  ·  50 Berths  ·  7 Bays",
        True, (100, 140, 185)), (18, 46))

    # Progress pips
    px = 18
    for i, d in enumerate(PHASE_DEFS):
        done = i < phase_idx; cur = i == phase_idx
        c = (55, 200, 100) if done else (230, 120, 35) if cur else (45, 60, 82)
        pygame.draw.circle(surface, c, (px + 7, 68), 7)
        pygame.draw.circle(surface, (200, 220, 245), (px + 7, 68), 7, 2)
        surface.blit(fonts["tiny"].render(f"P{d['num']}", True, COLOR_TEXT_LIGHT),
                     (px, 78))
        px += 44

    # Phase badge
    bs = fonts["badge"].render(pdef["label"], True, COLOR_TEXT_LIGHT)
    br = bs.get_rect(right=MAIN_W - 14, centery=16)
    pygame.draw.rect(surface, pdef["badge_color"], br.inflate(16, 8), border_radius=5)
    surface.blit(bs, br)

    surface.blit(fonts["tiny"].render(pdef["detail"], True, (130, 160, 200)),
                 (18 + 44 * 4 + 10, 66))
    ss = fonts["status"].render(status, True, (160, 185, 220))
    surface.blit(ss, (MAIN_W - ss.get_width() - 14, 46))

    # System status LED placeholder (green when running — amber drawn post-grayscale)
    pygame.draw.circle(surface, (30, 185, 70), (MAIN_W - 32, 14), 5)
    surface.blit(fonts["tiny"].render("SYS", True, (100, 140, 185)), (MAIN_W - 56, 9))

    pygame.draw.line(surface, COLOR_COACH_STRIPE, (0, HEADER_H - 2), (WIN_W, HEADER_H - 2), 2)


def draw_legend(surface, fonts, n_cores):
    import pygame
    y = HEADER_H
    pygame.draw.rect(surface, COLOR_PANEL_BG, (0, y, WIN_W, LEGEND_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, y), (WIN_W, y), 1)
    x = 12
    surface.blit(fonts["tiny"].render("Legend:", True, COLOR_TEXT_MID), (x, y + 9))
    x += 50
    for cid in range(1, n_cores + 1):
        pygame.draw.rect(surface, CORE_COLORS[cid], (x, y + 9, 10, 10), border_radius=2)
        surface.blit(fonts["tiny"].render(f"BOT-{cid}", True, COLOR_TEXT_DARK), (x + 13, y + 8))
        x += 55
    pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], (x, y + 9, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("STUDENT", True, COLOR_TEXT_DARK), (x + 13, y + 8))
    x += 62
    pygame.draw.rect(surface, COLOR_COLLISION, (x, y + 9, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Corrupt", True, COLOR_TEXT_DARK), (x + 13, y + 8))
    x += 58
    pygame.draw.rect(surface, COLOR_THINKING, (x, y + 9, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Thinking", True, COLOR_TEXT_DARK), (x + 13, y + 8))
    x += 62
    pygame.draw.rect(surface, (210, 218, 230), (x, y + 9, 10, 10), border_radius=2)
    surface.blit(fonts["tiny"].render("Open", True, COLOR_TEXT_DARK), (x + 13, y + 8))


def draw_control_bar(surface, fonts, is_paused, is_slow):
    import pygame
    pygame.draw.rect(surface, (16, 35, 50), (0, CTRL_BAR_Y, WIN_W, CTRL_BAR_H))
    pygame.draw.line(surface, COLOR_COACH_STRIPE, (0, CTRL_BAR_Y), (WIN_W, CTRL_BAR_Y), 1)
    hints = "[SPACE] Next Phase   |   [P] Pause   |   [R] Reset   |   [S] Slow-Mo"
    surface.blit(fonts["tiny"].render(hints, True, (140, 170, 200)), (18, CTRL_BAR_Y + 8))
    mode = "PAUSED" if is_paused else ("SLOW ×2" if is_slow else "NORMAL")
    mc = (220, 80, 80) if is_paused else ((220, 180, 20) if is_slow else (55, 200, 100))
    ms = fonts["badge"].render(f"Mode: {mode}", True, mc)
    surface.blit(ms, (WIN_W - ms.get_width() - 18, CTRL_BAR_Y + 7))


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — 3AC Bogie Train Car (bays, berths, aisle, parallax)
# ═════════════════════════════════════════════════════════════════════════════

def _draw_parallax_window(surface, rect, scroll_offset):
    """Draw a small coach wall window with scrolling scenery."""
    import pygame
    old_clip = surface.get_clip()
    surface.set_clip(rect)
    pygame.draw.rect(surface, COLOR_SKY, rect)
    gh = int(rect.height * 0.40)
    pygame.draw.rect(surface, COLOR_GROUND,
                     (rect.x, rect.y + rect.height - gh, rect.width, gh))
    spacing = 28
    for i in range(-1, rect.width // spacing + 3):
        px = rect.x + i * spacing - int(scroll_offset) % spacing
        pygame.draw.line(surface, COLOR_POLE,
                         (px, rect.y + rect.height - gh - 3),
                         (px, rect.y + rect.height - 1), 1)
    surface.set_clip(old_clip)
    pygame.draw.rect(surface, (80, 95, 115), rect, 1, border_radius=2)


def draw_train_car(surface, fonts, shared_seats, hover_seat,
                   thinking, glitch_seats, now, scroll_offset):
    import pygame

    # ── Coach body ────────────────────────────────────────────────────
    coach = pygame.Rect(3, GRID_TOP - 4, _COACH_RIGHT - 3, GRID_H + 8)
    pygame.draw.rect(surface, COLOR_COACH_BODY, coach, border_radius=8)
    pygame.draw.rect(surface, (120, 135, 155), coach, 2, border_radius=8)
    pygame.draw.rect(surface, COLOR_COACH_STRIPE,
                     (3, GRID_TOP - 4, _COACH_RIGHT - 3, 3), border_radius=2)
    pygame.draw.rect(surface, COLOR_COACH_STRIPE,
                     (3, GRID_TOP + GRID_H + 1, _COACH_RIGHT - 3, 3), border_radius=2)

    # ── Parallax windows — left wall ──────────────────────────────────
    for bay in range(TOTAL_BAYS):
        wy = GRID_TOP + bay * BAY_ROW_H + 4
        _draw_parallax_window(surface, pygame.Rect(5, wy, 10, CELL_H - 8), scroll_offset)

    # ── Parallax windows — right wall ─────────────────────────────────
    rwx = _COACH_RIGHT - 14
    for bay in range(TOTAL_BAYS):
        wy = GRID_TOP + bay * BAY_ROW_H + 4
        _draw_parallax_window(surface, pygame.Rect(rwx, wy, 10, CELL_H - 8), scroll_offset)

    # ── Aisle (between main berths and side berths) ───────────────────
    for bay in range(TOTAL_BAYS):
        ay = GRID_TOP + bay * BAY_ROW_H
        pygame.draw.rect(surface, COLOR_AISLE_BG, (_AISLE_X, ay, AISLE_W, CELL_H))
    acx = _AISLE_X + AISLE_W // 2
    for yy in range(GRID_TOP + 3, GRID_TOP + GRID_H - 3, 10):
        pygame.draw.line(surface, (165, 175, 195), (acx, yy), (acx, yy + 5), 1)
    al = fonts["tiny"].render("AISLE", True, (140, 150, 170))
    al_r = pygame.transform.rotate(al, 90)
    surface.blit(al_r, (acx - al_r.get_width() // 2,
                        GRID_TOP + GRID_H // 2 - al_r.get_height() // 2))

    # ── Bay partition lines ───────────────────────────────────────────
    for bay in range(1, TOTAL_BAYS):
        sy = GRID_TOP + bay * BAY_ROW_H - BAY_SEP // 2
        pygame.draw.line(surface, COLOR_PARTITION,
                         (_L_GRP_X - 2, sy), (_COACH_RIGHT - 14, sy), 2)

    # ── Centre partition (between left-facing and right-facing berths)
    px = _L_GRP_X + 3 * CELL_W + PART_GAP // 2
    for bay in range(6):   # only full bays have centre partition
        py = GRID_TOP + bay * BAY_ROW_H
        pygame.draw.line(surface, COLOR_PARTITION, (px, py + 2), (px, py + CELL_H - 2), 2)

    # ── Bay labels ────────────────────────────────────────────────────
    for bay in range(TOTAL_BAYS):
        by = GRID_TOP + bay * BAY_ROW_H + CELL_H // 2
        lbl = f"B{bay + 1}" if bay < 6 else "END"
        lt = fonts["tiny"].render(lbl, True, (100, 115, 140))
        surface.blit(lt, (GRID_MARGIN + 1, by - lt.get_height() // 2))

    # ── Draw berths (seats) ───────────────────────────────────────────
    for i in range(TOTAL_SEATS):
        sx, sy = seat_xy(i)
        gi = glitch_seats.get(i)
        tc = thinking.get(i, set())
        _draw_berth(surface, fonts, sx, sy, shared_seats[i], i,
                    berth_label(i), is_hover=(i == hover_seat),
                    thinking_cores=tc if tc else None,
                    glitch=gi, now=now)


def _draw_berth(surface, fonts, x, y, val, idx, label,
                is_hover=False, thinking_cores=None, glitch=None, now=0.0):
    """Draw a single berth with optional thinking pulse or glitch flicker."""
    import pygame
    pad = SEAT_PAD
    rect = pygame.Rect(x + pad, y + pad, CELL_W - 2*pad, CELL_H - 2*pad)

    # ── Determine colours ─────────────────────────────────────────────
    use_glitch = False
    if glitch:
        dl, c1, c2 = glitch
        if now < dl:
            use_glitch = True
            flick = (int(now * 10) % 2) == 0
            base = c1 if flick else c2
            tc = (255, 255, 255); brd = tuple(max(0, v - 50) for v in base)

    if not use_glitch:
        if val == SEAT_AVAILABLE:
            base, tc, brd = (212, 220, 232), (55, 70, 90), (170, 182, 198)
        elif val == SEAT_COLLISION:
            base, tc, brd = COLOR_COLLISION, (255,255,255), (155,20,20)
        elif val == HUMAN_ID:
            base = (200, 160, 12); tc = (30,25,5); brd = (150,120,8)
        else:
            base = CORE_COLORS.get(val, (180,180,180))
            tc = (255,255,255); brd = tuple(max(0, c-40) for c in base)

    # Shadow + body
    pygame.draw.rect(surface, (180, 190, 205), rect.move(2, 2), border_radius=5)
    pygame.draw.rect(surface, base, rect, border_radius=5)
    pygame.draw.rect(surface, brd, rect, 2, border_radius=5)

    # Berth type label (top-left)
    lt = fonts["tiny"].render(label, True, tc)
    surface.blit(lt, (rect.x + 4, rect.y + 2))
    # Seat number (top-right)
    sn = fonts["seat_num"].render(f"S-{idx+1:02d}", True, tc)
    surface.blit(sn, (rect.right - sn.get_width() - 4, rect.y + 2))
    # Status (bottom-centre)
    if use_glitch:     st = "CORRUPT"
    elif val == SEAT_AVAILABLE:  st = "OPEN"
    elif val == SEAT_COLLISION:  st = "CORRUPT"
    elif val == HUMAN_ID:       st = "STUDENT"
    else:                        st = f"BOT-{val}"
    ss = fonts["seat_state"].render(st, True, tc)
    surface.blit(ss, ss.get_rect(centerx=rect.centerx, bottom=rect.bottom - 2))

    # ═══ X-RAY: Thinking Visualisation ═══════════════════════════════
    if thinking_cores and not use_glitch:
        n = len(thinking_cores)
        pulse = 0.5 + 0.5 * math.sin(now * 8)
        ov = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        if n > 1:
            # DANGER: Multiple bots checking same seat — race imminent!
            alpha = int(100 + pulse * 120)
            ov.fill((COLOR_THINK_DANGER[0], COLOR_THINK_DANGER[1],
                     COLOR_THINK_DANGER[2], alpha))
            surface.blit(ov, rect.topleft)
            warn = fonts["badge"].render(f"CHECK×{n}", True, (200, 60, 5))
            surface.blit(warn, warn.get_rect(center=rect.center))
        else:
            alpha = int(60 + pulse * 100)
            ov.fill((COLOR_THINKING[0], COLOR_THINKING[1],
                     COLOR_THINKING[2], alpha))
            surface.blit(ov, rect.topleft)
            cid = list(thinking_cores)[0]
            tl = fonts["tiny"].render(f"BOT-{cid} CHECK", True, (135, 105, 0))
            surface.blit(tl, tl.get_rect(center=rect.center))

    # Hover highlight
    if is_hover and val == SEAT_AVAILABLE and not thinking_cores:
        pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID],
                         rect.inflate(4, 4), 3, border_radius=7)


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — Stats Bar, Event Log
# ═════════════════════════════════════════════════════════════════════════════

def draw_stats_bar(surface, fonts, seats, elapsed, throughput):
    import pygame
    c_cpu = sum(1 for i in range(TOTAL_SEATS) if 1 <= seats[i] <= 4)
    c_hum = sum(1 for i in range(TOTAL_SEATS) if seats[i] == HUMAN_ID)
    c_col = sum(1 for i in range(TOTAL_SEATS) if seats[i] == SEAT_COLLISION)
    c_opn = sum(1 for i in range(TOTAL_SEATS) if seats[i] == SEAT_AVAILABLE)
    pygame.draw.rect(surface, (225, 232, 242), (0, STATS_TOP, MAIN_W, STATS_BAR_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, STATS_TOP), (MAIN_W, STATS_TOP), 1)
    items = [(f"BOTs:{c_cpu}", COLOR_ACCENT, 12),
             (f"STUDENT:{c_hum}", CORE_COLORS[HUMAN_ID], 110),
             (f"✗ Corrupt:{c_col}", COLOR_COLLISION, 230),
             (f"Open:{c_opn}", COLOR_TEXT_MID, 370),
             (f"⏱{elapsed:.1f}s", COLOR_TEXT_DARK, 470),
             (f"⚡{throughput:.1f}/s", (20, 155, 68), 570)]
    for t, c, xp in items:
        surface.blit(fonts["stat"].render(t, True, c), (xp, STATS_TOP + 6))


def draw_log(surface, fonts, lines):
    import pygame
    pygame.draw.rect(surface, (222, 230, 240), (0, LOG_TOP, MAIN_W, LOG_H))
    pygame.draw.line(surface, (190, 200, 215), (0, LOG_TOP), (MAIN_W, LOG_TOP), 1)
    surface.blit(fonts["tiny"].render("Event Log:", True, COLOR_TEXT_MID), (12, LOG_TOP + 3))
    max_lines = min(len(lines), (LOG_H - 20) // 15)
    for j, line in enumerate(lines[-max_lines:]):
        if "COLLISION" in line or "✗" in line: c = COLOR_COLLISION
        elif "STUDENT" in line: c = CORE_COLORS[HUMAN_ID]
        else: c = COLOR_TEXT_DARK
        surface.blit(fonts["log"].render(line[:110], True, c), (12, LOG_TOP + 18 + j * 15))


# ═════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — Core Monitor, Mutex Waiting Room, Needle Speedometer
# ═════════════════════════════════════════════════════════════════════════════

def draw_sidebar(surface, fonts, core_states, n_cores, phase_locked,
                 throughput, now):
    import pygame
    sx = MAIN_W; sw = SIDEBAR_W

    pygame.draw.rect(surface, (230, 238, 248), (sx, 0, sw, WIN_H))
    pygame.draw.line(surface, (170, 182, 200), (sx, 0), (sx, WIN_H), 2)

    # ── Sidebar header ────────────────────────────────────────────────
    pygame.draw.rect(surface, COLOR_HEADER_BG, (sx, 0, sw, HEADER_H))
    surface.blit(fonts["badge"].render("Hardware Thread", True, COLOR_TEXT_LIGHT),
                 (sx + 12, 8))
    surface.blit(fonts["badge"].render("Activity Monitor", True, COLOR_TEXT_LIGHT),
                 (sx + 12, 26))
    surface.blit(fonts["tiny"].render("X-Ray: Check→Think→Act", True, (110, 145, 195)),
                 (sx + 12, 48))

    # ── CPU Core cards ────────────────────────────────────────────────
    cy = HEADER_H + 6
    surface.blit(fonts["badge"].render("CPU Cores", True, COLOR_TEXT_DARK), (sx + 12, cy))
    cy += 16
    s_labels = {CORE_IDLE: "IDLE", CORE_WAITING: "WAIT", CORE_WORKING: "RUN"}
    s_colors = {CORE_IDLE: COLOR_CORE_IDLE, CORE_WAITING: COLOR_CORE_WAITING,
                CORE_WORKING: COLOR_CORE_WORKING}
    for cid in range(1, 5):
        active = cid <= n_cores
        cs = core_states[cid - 1] if active else CORE_IDLE
        card = pygame.Rect(sx + 8, cy, sw - 16, 38)
        pygame.draw.rect(surface, COLOR_PANEL_BG, card, border_radius=6)
        pygame.draw.rect(surface, COLOR_DIVIDER, card, 1, border_radius=6)
        cc = CORE_COLORS.get(cid, (180,180,180))
        pygame.draw.rect(surface, cc if active else (200,205,215),
                         pygame.Rect(sx + 8, cy, 5, 38), border_radius=4)
        lbl = f"BOT-{cid}" if active else f"BOT-{cid}"
        surface.blit(fonts["small"].render(lbl, True,
                     COLOR_TEXT_DARK if active else (175,180,195)), (sx + 20, cy + 3))
        sc = s_colors[cs] if active else COLOR_CORE_IDLE
        r = 4
        if active and cs == CORE_WORKING:
            r = int(3 + 2 * (0.5 + 0.5 * math.sin(now * 8)))
        pygame.draw.circle(surface, sc, (sx + sw - 55, cy + 12), r)
        surface.blit(fonts["tiny"].render(
            s_labels[cs] if active else "OFF", True, sc), (sx + sw - 44, cy + 7))
        # Utilisation bar
        bw = sw - 36; bh = 4; bx = sx + 20; by = cy + 26
        pygame.draw.rect(surface, (210, 215, 228), (bx, by, bw, bh), border_radius=3)
        ff = {CORE_IDLE: 0.0, CORE_WAITING: 0.5, CORE_WORKING: 1.0}[cs] if active else 0
        if ff > 0:
            pygame.draw.rect(surface, sc, (bx, by, int(bw * ff), bh), border_radius=3)
        cy += 42

    # ── Mutex Lock Queue  ("Waiting Room") ────────────────────────────
    cy += 4
    surface.blit(fonts["badge"].render("Mutex Lock Queue", True, COLOR_TEXT_DARK),
                 (sx + 12, cy))
    cy += 16
    mutex_h = 80
    mr = pygame.Rect(sx + 8, cy, sw - 16, mutex_h)
    pygame.draw.rect(surface, (245, 248, 255), mr, border_radius=7)
    pygame.draw.rect(surface, (160, 170, 190), mr, 2, border_radius=7)

    if not phase_locked:
        # Phase 4: NO LOCK
        surface.blit(fonts["badge"].render("NO LOCK", True, COLOR_COLLISION),
                     (sx + 20, cy + 8))
        surface.blit(fonts["tiny"].render("⚠ RACE CONDITION", True, (180, 75, 18)),
                     (sx + 20, cy + 26))
        surface.blit(fonts["tiny"].render("All bots write freely!", True, (180, 75, 18)),
                     (sx + 20, cy + 40))
        surface.blit(fonts["tiny"].render("No serialisation.", True, (180, 75, 18)),
                     (sx + 20, cy + 54))
    else:
        # ── "ACTIVE" box (core holding the lock) ─────────────────────
        holder = None
        waiters = []
        for c in range(1, n_cores + 1):
            if core_states[c - 1] == CORE_WORKING: holder = c
            elif core_states[c - 1] == CORE_WAITING: waiters.append(c)

        act_r = pygame.Rect(sx + 14, cy + 4, sw - 28, 26)
        pygame.draw.rect(surface, (235, 248, 238) if holder else (240, 242, 248),
                         act_r, border_radius=4)
        pygame.draw.rect(surface, (100, 180, 110) if holder else COLOR_DIVIDER,
                         act_r, 1, border_radius=4)
        surface.blit(fonts["tiny"].render("ACTIVE:", True, (60, 110, 70)), (sx + 18, cy + 6))
        if holder:
            cc = CORE_COLORS.get(holder, (180,180,180))
            pygame.draw.circle(surface, cc, (sx + 68, cy + 15), 5)
            surface.blit(fonts["badge"].render(f"BOT-{holder}", True, cc), (sx + 78, cy + 8))
        else:
            surface.blit(fonts["tiny"].render("(open)", True, (150, 162, 180)), (sx + 68, cy + 8))

        # ── "QUEUE" box (cores waiting for the lock) ──────────────────
        que_r = pygame.Rect(sx + 14, cy + 34, sw - 28, mutex_h - 40)
        pygame.draw.rect(surface, (255, 252, 240) if waiters else (240, 242, 248),
                         que_r, border_radius=4)
        pygame.draw.rect(surface, COLOR_CORE_WAITING if waiters else COLOR_DIVIDER,
                         que_r, 1, border_radius=4)
        surface.blit(fonts["tiny"].render("QUEUE:", True, (120, 105, 40)), (sx + 18, cy + 36))
        if waiters:
            wx = sx + 68
            for w in waiters:
                cc = CORE_COLORS.get(w, (180,180,180))
                pygame.draw.circle(surface, cc, (wx, cy + 50), 5)
                surface.blit(fonts["tiny"].render(f"B{w}", True, cc), (wx + 8, cy + 44))
                wx += 44
        else:
            surface.blit(fonts["tiny"].render("(empty)", True, (155, 162, 180)),
                         (sx + 68, cy + 44))

    cy += mutex_h + 6

    # ── Needle Speedometer ────────────────────────────────────────────
    surface.blit(fonts["badge"].render("Throughput", True, COLOR_TEXT_DARK), (sx + 12, cy))
    cy += 18
    _draw_speedometer(surface, fonts, sx + sw // 2, cy + 58, 52, throughput)


def _draw_speedometer(surface, fonts, cx, cy, radius, throughput, max_tp=20.0):
    """Semi-circular needle gauge with coloured arc zones."""
    import pygame
    zones = [
        (0.0, 0.4, (50, 185, 80)),
        (0.4, 0.7, (220, 185, 25)),
        (0.7, 1.0, (200, 50, 40)),
    ]
    for f_s, f_e, zcol in zones:
        a_s = math.pi * (1 - f_e); a_e = math.pi * (1 - f_s)
        for step in range(16):
            t1 = a_s + (a_e - a_s) * step / 16
            t2 = a_s + (a_e - a_s) * (step + 1) / 16
            p1 = (cx + int(radius * math.cos(t1)), cy - int(radius * math.sin(t1)))
            p2 = (cx + int(radius * math.cos(t2)), cy - int(radius * math.sin(t2)))
            pygame.draw.line(surface, zcol, p1, p2, 4)

    for i in range(5):
        frac = i / 4.0
        angle = math.pi * (1 - frac)
        ix = cx + int((radius - 7) * math.cos(angle))
        iy = cy - int((radius - 7) * math.sin(angle))
        ox = cx + int((radius + 2) * math.cos(angle))
        oy = cy - int((radius + 2) * math.sin(angle))
        pygame.draw.line(surface, (70, 80, 100), (ix, iy), (ox, oy), 2)
        tl = fonts["tiny"].render(str(i * 5), True, (70, 80, 100))
        lx = cx + int((radius + 13) * math.cos(angle)) - tl.get_width() // 2
        ly = cy - int((radius + 13) * math.sin(angle)) - tl.get_height() // 2
        surface.blit(tl, (lx, ly))

    frac = min(throughput / max_tp, 1.0)
    angle = math.pi * (1 - frac)
    nl = radius - 10
    nx = cx + int(nl * math.cos(angle))
    ny = cy - int(nl * math.sin(angle))
    pygame.draw.line(surface, (200, 40, 40), (cx, cy), (nx, ny), 3)
    pygame.draw.circle(surface, (65, 75, 95), (cx, cy), 5)
    pygame.draw.circle(surface, (200, 40, 40), (cx, cy), 3)

    vt = fonts["stat"].render(f"{throughput:.1f}", True, (20, 155, 68))
    surface.blit(vt, vt.get_rect(centerx=cx, top=cy + 8))
    ut = fonts["tiny"].render("bk/sec", True, (100, 112, 135))
    surface.blit(ut, ut.get_rect(centerx=cx, top=cy + 24))


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
        prog = 1 - (1 - el / PACKET_DURATION) ** 3
        src_x = MAIN_W + SIDEBAR_W // 2
        src_y = HEADER_H + 22 + (max(cid, 1) - 1) * 42 + 19
        if cid == HUMAN_ID:
            src_x, src_y = MAIN_W // 2, WIN_H // 2
        dx, dy = seat_center(sidx)
        cur_x = int(src_x + (dx - src_x) * prog)
        cur_y = int(src_y + (dy - src_y) * prog)
        col = CORE_COLORS.get(cid, (180, 180, 180))
        tp = max(0, prog - 0.2)
        tx = int(src_x + (dx - src_x) * tp)
        ty = int(src_y + (dy - src_y) * tp)
        pygame.draw.line(surface, col, (tx, ty), (cur_x, cur_y), 2)
        pygame.draw.circle(surface, col, (cur_x, cur_y), 5)
        pygame.draw.circle(surface, (255, 255, 255), (cur_x, cur_y), 3)
    return alive


# ═════════════════════════════════════════════════════════════════════════════
#  MODAL, TOAST, OVERLAYS
# ═════════════════════════════════════════════════════════════════════════════

def draw_modal(surface, fonts, sidx, seats, locked):
    import pygame
    ov = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
    ov.fill((8, 18, 32, 130)); surface.blit(ov, (0, 0))
    mr = pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, MODAL_H)
    pygame.draw.rect(surface, (22, 30, 48), mr.move(4, 4), border_radius=14)
    pygame.draw.rect(surface, COLOR_PANEL_BG, mr, border_radius=14)
    pygame.draw.rect(surface, COLOR_ACCENT, mr, 2, border_radius=14)
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, 38), border_radius=14)
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y + 26, MODAL_W, 14))
    t = fonts["badge"].render("Confirm Berth Booking", True, COLOR_TEXT_LIGHT)
    surface.blit(t, (MODAL_X + MODAL_W // 2 - t.get_width() // 2, MODAL_Y + 10))
    v = seats[sidx]; bl = berth_label(sidx)
    surface.blit(fonts["stat"].render(f"S-{sidx+1:02d}  [{bl}]", True, COLOR_ACCENT),
                 (MODAL_X + 24, MODAL_Y + 50))
    if v == SEAT_AVAILABLE: st, sc = "AVAILABLE", (20, 150, 65)
    elif v == SEAT_COLLISION: st, sc = "CORRUPTED", COLOR_COLLISION
    elif v == HUMAN_ID: st, sc = "YOURS", CORE_COLORS[HUMAN_ID]
    elif 1 <= v <= 4: st, sc = f"TAKEN by BOT-{v}!", COLOR_COLLISION
    else: st, sc = "UNAVAIL", COLOR_TEXT_MID
    surface.blit(fonts["small"].render(f"Status: {st}", True, sc), (MODAL_X + 24, MODAL_Y + 74))
    lt = "Mutex protects this booking." if locked else "⚠ NO LOCK — race possible!"
    lc = (20, 125, 65) if locked else (195, 90, 12)
    surface.blit(fonts["tiny"].render(lt, True, lc), (MODAL_X + 24, MODAL_Y + 98))
    surface.blit(fonts["tiny"].render("⚡ BOTs still running!", True, (175, 75, 18)),
                 (MODAL_X + 24, MODAL_Y + 116))
    yr = pygame.Rect(*_YES)
    pygame.draw.rect(surface, (20, 150, 65), yr, border_radius=7)
    yt = fonts["badge"].render("✓ CONFIRM", True, (255, 255, 255))
    surface.blit(yt, yt.get_rect(center=yr.center))
    cr = pygame.Rect(*_CANC)
    pygame.draw.rect(surface, (145, 152, 172), cr, border_radius=7)
    ct = fonts["badge"].render("✗ CANCEL", True, (255, 255, 255))
    surface.blit(ct, ct.get_rect(center=cr.center))


def draw_toast(surface, fonts, text, color):
    import pygame
    ts = fonts["small"].render(text, True, (255, 255, 255))
    tw = ts.get_width() + 32; tx = (MAIN_W - tw) // 2; ty = HEADER_H + 2
    pygame.draw.rect(surface, (0, 0, 0), pygame.Rect(tx + 2, ty + 2, tw, 28), border_radius=9)
    pygame.draw.rect(surface, color, pygame.Rect(tx, ty, tw, 28), border_radius=9)
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(tx, ty, tw, 28), 2, border_radius=9)
    surface.blit(ts, (tx + 16, ty + 5))


def draw_waiting_overlay(surface, fonts, pd, phase_idx, now):
    """Pulsing prompt: 'Press SPACE to continue'."""
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
#  PHASE 5 — TELEMETRY  (Amdahl's Law Speedup Dashboard)
# ═════════════════════════════════════════════════════════════════════════════

def draw_telemetry(surface, fonts, ptimes, p4_col, h_total):
    import pygame
    surface.fill(COLOR_BG)
    t1 = max(ptimes.get(1, 1.0), 0.001)
    t2 = max(ptimes.get(2, t1),  0.001)
    t4 = max(ptimes.get(3, t1),  0.001)
    s2, s4 = t1 / t2, t1 / t4
    eff4 = s4 / 4.0
    sf = max(0.0, ((1.0 / s4) - 0.25) / 0.75) if s4 > 0 else 0.5

    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, 78))
    surface.blit(fonts["title"].render(
        "Phase 5  ·  Performance Telemetry  &  Amdahl's Law", True, COLOR_TEXT_LIGHT),
        (20, 8))
    surface.blit(fonts["small"].render(
        "Empirical speedup from Phases 1–3  ·  S(N) = 1/((1-P) + P/N)",
        True, (130, 160, 210)), (20, 40))

    fy = WIN_H - 28
    pygame.draw.rect(surface, (222, 230, 240), (0, fy, WIN_W, 28))
    surface.blit(fonts["small"].render("Simulation complete  ·  Press ESC to exit",
                 True, COLOR_TEXT_MID), (20, fy + 6))

    cy_top = 86; ch = fy - cy_top
    PW = 255; PX = 14
    cards = [
        ("T₁  —  1 Core",  f"{t1:.3f} s",    "Serial baseline", CORE_COLORS[1]),
        ("T₂  —  2 Cores", f"{t2:.3f} s",    "2-core mutex",    CORE_COLORS[2]),
        ("T₄  —  4 Cores", f"{t4:.3f} s",    "4-core mutex",    CORE_COLORS[4]),
        ("S₂  Speedup",    f"{s2:.3f}×",     "ideal = 2×",      CORE_COLORS[2]),
        ("S₄  Speedup",    f"{s4:.3f}×",     "ideal = 4×",      CORE_COLORS[4]),
        ("Efficiency",     f"{eff4*100:.1f}%","S₄ ÷ 4",         (88, 55, 200)),
        ("Serial Frac.",   f"{sf*100:.1f}%", "Amdahl bottleneck",COLOR_COLLISION),
        ("P4 Corruptions", str(p4_col),       "race conditions", (195, 45, 30)),
        ("STUDENT Bookings",str(h_total),     "user clicks",     CORE_COLORS[HUMAN_ID]),
    ]
    card_h = min(58, max(50, (ch - 6 * (len(cards)-1)) // len(cards)))
    for i, (lbl, val, sub, acc) in enumerate(cards):
        ccy = cy_top + i * (card_h + 5)
        cr = pygame.Rect(PX, ccy, PW, card_h)
        pygame.draw.rect(surface, COLOR_PANEL_BG, cr, border_radius=8)
        pygame.draw.rect(surface, COLOR_DIVIDER, cr, 1, border_radius=8)
        pygame.draw.rect(surface, acc, pygame.Rect(PX, ccy, 4, card_h), border_radius=3)
        surface.blit(fonts["tiny"].render(lbl, True, COLOR_TEXT_MID), (PX + 12, ccy + 3))
        surface.blit(fonts["stat"].render(val, True, acc), (PX + 12, ccy + 15))
        surface.blit(fonts["tiny"].render(sub, True, (155, 162, 185)), (PX + 12, ccy + card_h - 14))

    # ── Speedup Graph ─────────────────────────────────────────────────
    GX = PX + PW + 12; GW = WIN_W - GX - 12; GY = cy_top; GH = ch
    gcr = pygame.Rect(GX, GY, GW, GH)
    pygame.draw.rect(surface, COLOR_GRAPH_BG, gcr, border_radius=11)
    pygame.draw.rect(surface, COLOR_DIVIDER, gcr, 1, border_radius=11)
    surface.blit(fonts["badge"].render("Speedup vs. Physical Cores", True, COLOR_TEXT_DARK),
                 (GX + 16, GY + 10))

    ML, MR, MT, MB = 68, 28, 44, 56
    px0 = GX + ML; py0 = GY + MT; pw = GW - ML - MR; ph = GH - MT - MB
    YMAX = 5.0
    xc = [1, 2, 4]; yi = [1.0, 2.0, 4.0]; ya = [1.0, s2, s4]

    def to_px(cv, sv):
        return int(px0 + (cv - 1) / 3.0 * pw), int(py0 + ph - sv / YMAX * ph)

    for yt_ in range(6):
        _, gy = to_px(1, yt_)
        pygame.draw.line(surface, COLOR_GRID_LINE, (px0, gy), (px0 + pw, gy), 1)
        tl = fonts["axis"].render(f"{yt_}×", True, COLOR_AXIS)
        surface.blit(tl, (px0 - tl.get_width() - 6, gy - tl.get_height() // 2))
    for xv in xc:
        gx, _ = to_px(xv, 0)
        pygame.draw.line(surface, COLOR_GRID_LINE, (gx, py0), (gx, py0 + ph), 1)
        tl = fonts["axis"].render(str(xv), True, COLOR_AXIS)
        surface.blit(tl, (gx - tl.get_width() // 2, py0 + ph + 8))
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0), (px0, py0 + ph), 2)
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0 + ph), (px0 + pw, py0 + ph), 2)
    xl = fonts["small"].render("Physical Cores", True, COLOR_AXIS)
    surface.blit(xl, (px0 + pw // 2 - xl.get_width() // 2, py0 + ph + 30))
    ylr = pygame.transform.rotate(fonts["small"].render("Speedup S=T₁/Tₙ", True, COLOR_AXIS), 90)
    surface.blit(ylr, (GX + 4, py0 + ph // 2 - ylr.get_height() // 2))

    ipts = [to_px(c, y) for c, y in zip(xc, yi)]
    _draw_dashed(surface, COLOR_IDEAL_LINE, ipts, 3, 14, 8)
    for pt in ipts:
        pygame.draw.circle(surface, COLOR_IDEAL_LINE, pt, 7)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, pt, 4)

    apts = [to_px(c, y) for c, y in zip(xc, ya)]
    if len(apts) > 1:
        pygame.draw.lines(surface, COLOR_ACTUAL_LINE, False, apts, 3)
    for (apx, apy), (_, yv) in zip(apts, zip(xc, ya)):
        pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (apx, apy), 8)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, (apx, apy), 4)
        tag = fonts["axis"].render(f"{yv:.2f}×", True, COLOR_TEXT_DARK)
        tr = pygame.Rect(apx - tag.get_width()//2 - 3, apy - 30,
                         tag.get_width() + 6, tag.get_height() + 3)
        pygame.draw.rect(surface, (230, 245, 235), tr, border_radius=4)
        pygame.draw.rect(surface, COLOR_ACTUAL_LINE, tr, 1, border_radius=4)
        surface.blit(tag, (tr.x + 3, tr.y + 1))

    # Legend
    lx, ly = px0 + pw - 200, py0 + 10
    pygame.draw.rect(surface, (240, 243, 255), pygame.Rect(lx - 6, ly - 4, 195, 48),
                     border_radius=6)
    pygame.draw.rect(surface, COLOR_DIVIDER, pygame.Rect(lx - 6, ly - 4, 195, 48),
                     1, border_radius=6)
    _draw_dashed(surface, COLOR_IDEAL_LINE, [(lx, ly + 8), (lx + 26, ly + 8)], 2, 6, 4)
    pygame.draw.circle(surface, COLOR_IDEAL_LINE, (lx + 36, ly + 8), 4)
    surface.blit(fonts["axis"].render("Ideal (linear)", True, COLOR_TEXT_DARK),
                 (lx + 46, ly + 2))
    pygame.draw.line(surface, COLOR_ACTUAL_LINE, (lx, ly + 26), (lx + 26, ly + 26), 2)
    pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (lx + 36, ly + 26), 4)
    surface.blit(fonts["axis"].render("Actual (measured)", True, COLOR_TEXT_DARK),
                 (lx + 46, ly + 20))

    # Amdahl's Law formula
    ax_, ay_ = px0 + 14, py0 + ph - 58
    pygame.draw.rect(surface, (246, 248, 255), pygame.Rect(ax_ - 6, ay_ - 3, 340, 56),
                     border_radius=6)
    pygame.draw.rect(surface, COLOR_DIVIDER, pygame.Rect(ax_ - 6, ay_ - 3, 340, 56),
                     1, border_radius=6)
    for li, al in enumerate([
        "Amdahl: S(N) = 1 / (s + (1-s)/N)",
        f"Serial fraction s ≈ {sf*100:.1f}%",
        f"Efficiency at 4 cores = {eff4*100:.1f}%"]):
        surface.blit(fonts["tiny"].render(al, True,
                     COLOR_TEXT_DARK if li == 0 else (85, 95, 115)),
                     (ax_, ay_ + li * 18))


def _draw_dashed(surface, color, points, width=2, dash=12, gap=6):
    import pygame
    for i in range(len(points) - 1):
        x1, y1 = points[i]; x2, y2 = points[i + 1]
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        if ln == 0: continue
        ux, uy = dx / ln, dy / ln
        pos, d = 0.0, True
        while pos < ln:
            seg = dash if d else gap; end = min(pos + seg, ln)
            if d:
                pygame.draw.line(surface, color,
                    (int(x1 + ux * pos), int(y1 + uy * pos)),
                    (int(x1 + ux * end), int(y1 + uy * end)), width)
            pos += seg; d = not d


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "Indian Railways  ·  3AC LHB Coach Simulator  v6.5  —  X-Ray Logic Edition")
    clock = pygame.time.Clock()

    def F(sz, bold=False):
        try:    return pygame.font.SysFont("Segoe UI", sz, bold=bold)
        except: return pygame.font.Font(None, sz)

    fonts = {
        "title"      : F(20, True),
        "badge"      : F(12, True),
        "status"     : F(11),
        "small"      : F(12),
        "seat_num"   : F(9, True),
        "seat_state" : F(8),
        "log"        : F(10),
        "stat"       : F(11, True),
        "overlay"    : F(22, True),
        "axis"       : F(11),
        "tiny"       : F(9),
    }

    # ── Shared hardware memory & IPC ──────────────────────────────────
    shared_seats = multiprocessing.Array('i', TOTAL_SEATS)
    core_states  = multiprocessing.Array('i', 4)
    log_queue    = multiprocessing.Queue()
    event_queue  = multiprocessing.Queue()
    run_flag     = multiprocessing.Event()
    run_flag.set()
    speed_mult   = multiprocessing.Value('d', 1.0)

    # ── State ─────────────────────────────────────────────────────────
    state       = "running"
    phase_idx   = 0
    processes   = []
    current_lock = None
    log_lines   = []
    status_text = ""

    phase_start      = 0.0
    phase_elapsed    = 0.0
    total_pause_dur  = 0.0
    pause_perf_start = 0.0
    phase_times      = {}
    p4_collisions    = 0
    human_total      = 0

    is_paused = False
    is_slow   = False

    modal_open     = False
    modal_seat_idx = -1
    toast_text  = ""
    toast_color = (0, 0, 0)
    toast_start = 0.0

    # ── X-Ray thinking state (seat_idx → set of core_ids) ────────────
    thinking = {}

    data_packets       = []
    glitch_seats       = {}
    booking_timestamps = deque(maxlen=200)
    throughput         = 0.0
    last_tp_calc       = 0.0
    scroll_offset      = 0.0

    # ── Launch Phase 1 ────────────────────────────────────────────────
    reset_seats(shared_seats)
    pd = PHASE_DEFS[phase_idx]
    processes, current_lock, phase_start = launch_phase(
        pd, shared_seats, core_states, log_queue, event_queue, run_flag, speed_mult)
    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
    log_lines.append("  💡 Click any OPEN berth to book as STUDENT!")
    status_text = f"Running {pd['label']}…"

    # ══════════════════════════════════════════════════════════════════
    #   MAIN LOOP
    # ══════════════════════════════════════════════════════════════════
    running = True
    while running:
        now = time.time()

        # ── Events ────────────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                kill_all(processes); running = False

            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    kill_all(processes); running = False

                # ── [P] Silent Pause / Resume ─────────────────────────
                elif ev.key == pygame.K_p and state != "telemetry":
                    if is_paused:
                        is_paused = False
                        run_flag.set()
                        total_pause_dur += time.perf_counter() - pause_perf_start
                    else:
                        is_paused = True
                        run_flag.clear()
                        pause_perf_start = time.perf_counter()

                # ── [R] Reset current phase ───────────────────────────
                elif ev.key == pygame.K_r and state in ("running", "waiting_presenter"):
                    run_flag.set(); is_paused = False
                    kill_all(processes)
                    drain_queue(log_queue); drain_queue(event_queue)
                    reset_seats(shared_seats)
                    for i in range(4): core_states[i] = CORE_IDLE
                    data_packets.clear(); glitch_seats.clear(); thinking.clear()
                    booking_timestamps.clear(); throughput = 0.0
                    total_pause_dur = 0.0; modal_open = False; log_lines.clear()
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
                        f"[SYS] Slow-Mo {'ON (×2)' if is_slow else 'OFF'}.")

                # ── [SPACE] Next phase ────────────────────────────────
                elif ev.key == pygame.K_SPACE:
                    if state == "waiting_presenter":
                        nxt = phase_idx + 1
                        if nxt < len(PHASE_DEFS):
                            phase_idx = nxt; pd = PHASE_DEFS[phase_idx]
                            reset_seats(shared_seats); thinking.clear()
                            log_lines.clear(); data_packets.clear()
                            glitch_seats.clear(); booking_timestamps.clear()
                            throughput = 0.0; total_pause_dur = 0.0
                            modal_open = False
                            processes, current_lock, phase_start = launch_phase(
                                pd, shared_seats, core_states, log_queue,
                                event_queue, run_flag, speed_mult)
                            log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
                            log_lines.append("  💡 Click berths to book as STUDENT!")
                            status_text = f"Running {pd['label']}…"
                            state = "running"
                        else:
                            state = "telemetry"

            # ── Mouse click ───────────────────────────────────────────
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if is_paused: continue
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
                        log_lines.append(f"[STUDENT] {msg}")
                        toast_text = msg; toast_start = now; modal_open = False
                    elif _in_rect(mx, my, _CANC):
                        modal_open = False; log_lines.append("[STUDENT] Cancelled.")
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
                            toast_text = f"S-{si+1:02d} taken by BOT-{v}"
                            toast_color = COLOR_TEXT_MID; toast_start = now

        if not running: break

        # ── Drain queues ──────────────────────────────────────────────
        while not log_queue.empty():
            try: log_lines.append(log_queue.get_nowait())
            except: break
        while not event_queue.empty():
            try:
                ed = event_queue.get_nowait()
                if ed[0] == 'THINK_START':
                    _, cid, si, t = ed
                    thinking.setdefault(si, set()).add(cid)
                elif ed[0] == 'THINK_END':
                    _, cid, si, t = ed
                    thinking.get(si, set()).discard(cid)
                    if not thinking.get(si): thinking.pop(si, None)
                elif ed[0] == 'BOOK':
                    _, cid, si, t = ed
                    data_packets.append((cid, si, t))
                    booking_timestamps.append(t)
                elif ed[0] == 'COLLISION':
                    _, cid, si, t = ed
                    c1 = CORE_COLORS.get(cid, (180,180,180))
                    ov_val = shared_seats[si]
                    c2 = CORE_COLORS.get(ov_val, COLOR_COLLISION) if ov_val > 0 else COLOR_COLLISION
                    glitch_seats[si] = (now + GLITCH_DURATION, c1, c2)
                    data_packets.append((cid, si, t))
            except: break

        # ── Throughput ────────────────────────────────────────────────
        if now - last_tp_calc >= THROUGHPUT_INTERVAL:
            cutoff = now - 2.0
            throughput = sum(1 for ts in booking_timestamps if ts >= cutoff) / 2.0
            last_tp_calc = now

        # ── Hover ─────────────────────────────────────────────────────
        hover_seat = -1
        if state == "running" and not modal_open and not is_paused:
            hover_seat = mouse_to_seat(*pygame.mouse.get_pos())

        # ── Purge expired glitches ────────────────────────────────────
        for k in [k for k, (dl, _, _) in glitch_seats.items() if now >= dl]:
            del glitch_seats[k]

        # ── Scroll (pauses when paused or slow) ──────────────────────
        if not is_paused and state != "telemetry":
            scroll_offset += (0.9 if is_slow else 1.8)

        # ── Timing (excludes pause duration) ──────────────────────────
        if is_paused:
            phase_elapsed = pause_perf_start - phase_start - total_pause_dur
        else:
            phase_elapsed = time.perf_counter() - phase_start - total_pause_dur

        # ── State machine ─────────────────────────────────────────────
        if state == "running":
            status_text = f"{pd['label']}  —  {phase_elapsed:.1f}s"
            if all_done(processes):
                modal_open = False; thinking.clear()
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

        # ══════════════════════════════════════════════════════════════
        #   RENDERING
        # ══════════════════════════════════════════════════════════════
        if state == "telemetry":
            draw_telemetry(screen, fonts, phase_times, p4_collisions, human_total)
        else:
            screen.fill(COLOR_BG)
            draw_header(screen, fonts, pd, status_text, phase_idx)
            draw_legend(screen, fonts, pd["cores"])
            draw_train_car(screen, fonts, shared_seats, hover_seat,
                           thinking, glitch_seats, now, scroll_offset)
            draw_stats_bar(screen, fonts, shared_seats, phase_elapsed, throughput)
            draw_log(screen, fonts, log_lines)
            draw_control_bar(screen, fonts, is_paused, is_slow)
            draw_sidebar(screen, fonts, core_states, pd["cores"], pd["locked"],
                         throughput, now)

            data_packets = draw_data_packets(screen, data_packets, now)

            if state == "waiting_presenter":
                draw_waiting_overlay(screen, fonts, pd, phase_idx, now)

            if modal_open:
                draw_modal(screen, fonts, modal_seat_idx,
                           shared_seats, pd["locked"])

            # Toast (drawn before grayscale so it desaturates too)
            if toast_text and (now - toast_start < TOAST_DURATION):
                draw_toast(screen, fonts, toast_text, toast_color)

        # ══════════════════════════════════════════════════════════════
        #   SILENT PAUSE: Full-screen grayscale + amber LED
        # ══════════════════════════════════════════════════════════════
        if is_paused and state != "telemetry":
            try:
                px_arr = pygame.surfarray.pixels3d(screen)
                r = px_arr[:, :, 0].astype('uint32')
                g = px_arr[:, :, 1].astype('uint32')
                b = px_arr[:, :, 2].astype('uint32')
                # Perceived luminance (BT.601) then dim 50%:
                #   lum = (R*77 + G*150 + B*29) / 256  →  / 512 to halve
                lum = ((r * 77 + g * 150 + b * 29) >> 9).astype('uint8')
                px_arr[:, :, 0] = lum
                px_arr[:, :, 1] = lum
                px_arr[:, :, 2] = lum
                del px_arr                          # MUST delete to unlock surface
            except Exception:
                # Fallback if numpy/surfarray unavailable
                dark = pygame.Surface((WIN_W, WIN_H))
                dark.fill((0, 0, 0)); dark.set_alpha(140)
                screen.blit(dark, (0, 0))

            # Amber pulsing LED — the ONLY colour on screen
            pulse = 0.5 + 0.5 * math.sin(now * 4)
            lr = int(5 + pulse * 3)
            lx, ly = MAIN_W - 32, 14
            pygame.draw.circle(screen, (35, 25, 8), (lx, ly), lr + 2)
            pygame.draw.circle(screen, (220, 155, 20), (lx, ly), lr)
            pygame.draw.circle(screen, (255, 210, 55), (lx, ly), max(lr - 2, 1))
            sl = fonts["tiny"].render("SYS", True, (180, 140, 30))
            screen.blit(sl, (lx - 26, ly - 5))

        pygame.display.flip()
        clock.tick(FPS)

    # ── Clean shutdown ────────────────────────────────────────────────
    run_flag.set()
    kill_all(processes)
    pygame.quit()
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
